# Arquivo: caee_routes.py (VERSÃO FINAL COMPLETA)

import os 
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_from_directory, make_response
from .models import (
    CaeeAluno, CaeeProfissional, Secretaria, CaeePlanoAtendimento, 
    CaeeSessao, CaeeLaudo, CaeeRelatorioPeriodico, CaeeLinhaTempo
)
from .extensions import db
from .utils import login_required, role_required
from datetime import datetime
from werkzeug.utils import secure_filename
import io
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import cm
from .utils import cabecalho_e_rodape
from sqlalchemy import or_

caee_bp = Blueprint('caee', __name__, url_prefix='/caee')

# ==========================================================
# 1. DASHBOARD E CRUD ALUNO
# ==========================================================

@caee_bp.route('/dashboard')
@login_required
@role_required('admin', 'RH', 'CAEE')
def dashboard():
    secretaria_id_logada = session.get('secretaria_id')
    
    # --- ESTATÍSTICAS (Mantém igual) ---
    alunos_ativos = CaeeAluno.query.filter_by(secretaria_id=secretaria_id_logada, status='Ativo').count()
    alunos_em_espera = CaeeAluno.query.filter_by(secretaria_id=secretaria_id_logada, status='Fila de Espera').count()
    profissionais_ativos = CaeeProfissional.query.filter_by(secretaria_id=secretaria_id_logada, status='Ativo').count()
    
    # --- LÓGICA DE BUSCA (NOVA) ---
    termo = request.args.get('termo')
    
    if termo:
        # Se tem busca, procura por Nome OU CPF
        alunos_listados = CaeeAluno.query.filter(
            CaeeAluno.secretaria_id == secretaria_id_logada,
            or_(
                CaeeAluno.nome_completo.ilike(f"%{termo}%"),
                CaeeAluno.cpf.ilike(f"%{termo}%")
            )
        ).order_by(CaeeAluno.nome_completo).all()
        
        titulo_tabela = f"Resultados da busca por: '{termo}'"
    else:
        # Se NÃO tem busca, mostra os últimos 5 (padrão)
        alunos_listados = CaeeAluno.query.filter_by(secretaria_id=secretaria_id_logada)\
            .order_by(CaeeAluno.data_cadastro.desc())\
            .limit(5).all()
            
        titulo_tabela = "Últimos Alunos Cadastrados"

    return render_template(
        'caee_dashboard.html',
        alunos_ativos=alunos_ativos,
        alunos_em_espera=alunos_em_espera,
        profissionais_ativos=profissionais_ativos,
        ultimos_alunos=alunos_listados, # Enviamos a lista (seja busca ou últimos 5) nesta variável
        titulo_tabela=titulo_tabela     # Enviamos o título dinâmico
    )

@caee_bp.route('/aluno/novo', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def adicionar_aluno():
    if request.method == 'POST':
        try:
            secretaria_id_logada = session.get('secretaria_id')
            data_nasc_str = request.form.get('data_nascimento')

            novo_aluno = CaeeAluno(
                nome_completo=request.form.get('nome_completo'),
                data_nascimento=datetime.strptime(data_nasc_str, '%Y-%m-%d').date() if data_nasc_str else None,
                cpf=request.form.get('cpf'),
                nome_responsavel=request.form.get('nome_responsavel'),
                telefone_responsavel=request.form.get('telefone_responsavel'),
                endereco=request.form.get('endereco'),
                status=request.form.get('status'),
                hipotese_diagnostica=request.form.get('hipotese_diagnostica'),
                anamnese=request.form.get('anamnese'),
                secretaria_id=secretaria_id_logada,
                escola_origem=request.form.get('escola_origem'),
                cid_diagnostico=request.form.get('cid_diagnostico'),
                necessidade_especifica=request.form.get('necessidade_especifica')
            )
            db.session.add(novo_aluno)
            db.session.flush() # Gera o ID
            
            # Evento inicial na Linha do Tempo
            evento = CaeeLinhaTempo(
                aluno_id=novo_aluno.id,
                etapa="Cadastro Inicial",
                status="Concluído",
                observacao="Aluno cadastrado no sistema."
            )
            db.session.add(evento)
            
            db.session.commit()
            flash(f'Aluno "{novo_aluno.nome_completo}" cadastrado com sucesso!', 'success')
            return redirect(url_for('caee.dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar aluno: {e}', 'danger')
    return render_template('caee_aluno_form.html')

@caee_bp.route('/aluno/<int:aluno_id>/editar', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def editar_aluno(aluno_id):
    aluno = CaeeAluno.query.get_or_404(aluno_id)
    if request.method == 'POST':
        try:
            dn = request.form.get('data_nascimento')
            aluno.nome_completo = request.form.get('nome_completo')
            aluno.data_nascimento = datetime.strptime(dn, '%Y-%m-%d').date() if dn else None
            aluno.cpf = request.form.get('cpf')
            aluno.nome_responsavel = request.form.get('nome_responsavel')
            aluno.telefone_responsavel = request.form.get('telefone_responsavel')
            aluno.endereco = request.form.get('endereco')
            aluno.status = request.form.get('status')
            aluno.hipotese_diagnostica = request.form.get('hipotese_diagnostica')
            aluno.anamnese = request.form.get('anamnese')
            aluno.escola_origem = request.form.get('escola_origem')
            aluno.cid_diagnostico = request.form.get('cid_diagnostico')
            aluno.necessidade_especifica = request.form.get('necessidade_especifica')
            db.session.commit()
            flash('Prontuário atualizado!', 'success')
            return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro: {e}', 'danger')
    return render_template('caee_aluno_form.html', aluno=aluno)

# ==========================================================
# 2. PRONTUÁRIO E GESTÃO DE PLANOS (PAI)
# ==========================================================

@caee_bp.route('/aluno/<int:aluno_id>')
@login_required
@role_required('admin', 'RH', 'CAEE')
def prontuario_aluno(aluno_id):
    aluno = CaeeAluno.query.get_or_404(aluno_id)
    
    # Busca lista de planos, linha do tempo e sessões
    planos = aluno.planos 
    linha_tempo = CaeeLinhaTempo.query.filter_by(aluno_id=aluno.id).order_by(CaeeLinhaTempo.data_evento.desc()).all()
    sessoes = CaeeSessao.query.join(CaeePlanoAtendimento).filter(CaeePlanoAtendimento.aluno_id == aluno.id).order_by(CaeeSessao.data_sessao.desc()).all()

    # Para o modal de encaminhamento
    secretaria_id_logada = session.get('secretaria_id')
    profissionais = CaeeProfissional.query.filter_by(secretaria_id=secretaria_id_logada, status='Ativo').all()

    return render_template('caee_prontuario.html', 
        aluno=aluno, planos=planos, sessoes=sessoes, 
        linha_tempo=linha_tempo, profissionais=profissionais
    )

@caee_bp.route('/aluno/<int:aluno_id>/plano/novo', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def novo_plano(aluno_id):
    aluno = CaeeAluno.query.get_or_404(aluno_id)
    secretaria_id_logada = session.get('secretaria_id')
    profissionais = CaeeProfissional.query.filter_by(secretaria_id=secretaria_id_logada, status='Ativo').all()

    if request.method == 'POST':
        try:
            pid = request.form.get('profissional_id', type=int)
            plano_existente = CaeePlanoAtendimento.query.filter_by(aluno_id=aluno_id, profissional_id=pid, status_plano='Ativo').first()
            
            if plano_existente:
                flash('Já existe um plano ativo para este profissional.', 'warning')
                return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))

            novo = CaeePlanoAtendimento(
                aluno_id=aluno_id,
                profissional_id=pid,
                frequencia_semanal=request.form.get('frequencia_semanal', type=int),
                duracao_sessao_min=request.form.get('duracao_sessao_min', type=int),
                objetivos_gerais=request.form.get('objetivos_gerais'),
                objetivos_especificos=request.form.get('objetivos_especificos'),
                metodologia=request.form.get('metodologia'),
                status_plano='Ativo'
            )
            db.session.add(novo)
            
            # Registra na Linha do Tempo
            prof = CaeeProfissional.query.get(pid)
            evento = CaeeLinhaTempo(
                aluno_id=aluno_id,
                etapa=f"Início PAI - {prof.especialidade}",
                profissional_destino_id=pid,
                status="Em Andamento",
                observacao=f"Plano criado para {prof.nome_completo}"
            )
            db.session.add(evento)
            
            db.session.commit()
            flash('Novo Plano (PAI) criado!', 'success')
            return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro: {e}', 'danger')

    return render_template('caee_plano_form.html', aluno=aluno, profissionais=profissionais, plano=None)

@caee_bp.route('/plano/<int:plano_id>/editar', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def editar_plano(plano_id):
    plano = CaeePlanoAtendimento.query.get_or_404(plano_id)
    aluno = plano.aluno
    secretaria_id_logada = session.get('secretaria_id')
    profissionais = CaeeProfissional.query.filter_by(secretaria_id=secretaria_id_logada, status='Ativo').all()

    if request.method == 'POST':
        try:
            plano.profissional_id = request.form.get('profissional_id', type=int)
            plano.frequencia_semanal = request.form.get('frequencia_semanal', type=int)
            plano.duracao_sessao_min = request.form.get('duracao_sessao_min', type=int)
            plano.objetivos_gerais = request.form.get('objetivos_gerais')
            plano.objetivos_especificos = request.form.get('objetivos_especificos')
            plano.metodologia = request.form.get('metodologia')
            plano.status_plano = request.form.get('status_plano', 'Ativo')
            db.session.commit()
            flash('Plano atualizado!', 'success')
            return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro: {e}', 'danger')

    return render_template('caee_plano_form.html', aluno=aluno, profissionais=profissionais, plano=plano)

@caee_bp.route('/aluno/<int:aluno_id>/encaminhar', methods=['POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def encaminhar_aluno(aluno_id):
    try:
        dest_id = request.form.get('profissional_destino_id', type=int)
        etapa = request.form.get('etapa')
        obs = request.form.get('observacao')
        
        novo = CaeeLinhaTempo(
            aluno_id=aluno_id,
            etapa=etapa,
            profissional_destino_id=dest_id if dest_id else None,
            observacao=obs,
            status="Encaminhado"
        )
        db.session.add(novo)
        db.session.commit()
        flash('Encaminhamento registrado!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))

# ==========================================================
# 3. CRUD PROFISSIONAIS
# ==========================================================

@caee_bp.route('/profissionais')
@login_required
@role_required('admin', 'RH', 'CAEE')
def listar_profissionais():
    secretaria_id_logada = session.get('secretaria_id')
    profissionais = CaeeProfissional.query.filter_by(secretaria_id=secretaria_id_logada).order_by(CaeeProfissional.nome_completo).all()
    return render_template('caee_profissionais.html', profissionais=profissionais)

@caee_bp.route('/profissional/novo', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def adicionar_profissional():
    if request.method == 'POST':
        try:
            secretaria_id_logada = session.get('secretaria_id')
            novo = CaeeProfissional(
                nome_completo=request.form.get('nome_completo'),
                cpf=request.form.get('cpf'),
                telefone=request.form.get('telefone'),
                especialidade=request.form.get('especialidade'),
                registro_conselho=request.form.get('registro_conselho'),
                status=request.form.get('status'),
                secretaria_id=secretaria_id_logada
            )
            db.session.add(novo)
            db.session.commit()
            flash('Profissional cadastrado!', 'success')
            return redirect(url_for('caee.listar_profissionais'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro: {e}', 'danger')
    return render_template('caee_profissional_form.html')

@caee_bp.route('/profissional/<int:profissional_id>/editar', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def editar_profissional(profissional_id):
    profissional = CaeeProfissional.query.get_or_404(profissional_id)
    if request.method == 'POST':
        try:
            profissional.nome_completo = request.form.get('nome_completo')
            profissional.cpf = request.form.get('cpf')
            profissional.telefone = request.form.get('telefone')
            profissional.especialidade = request.form.get('especialidade')
            profissional.registro_conselho = request.form.get('registro_conselho')
            profissional.status = request.form.get('status')
            db.session.commit()
            flash('Profissional atualizado!', 'success')
            return redirect(url_for('caee.listar_profissionais'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro: {e}', 'danger')
    return render_template('caee_profissional_form.html', profissional=profissional)

# ==========================================================
# 4. SESSÕES E LAUDOS
# ==========================================================

@caee_bp.route('/plano/<int:plano_id>/sessao/nova', methods=['POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def adicionar_sessao(plano_id):
    plano = CaeePlanoAtendimento.query.get_or_404(plano_id)
    try:
        dt = request.form.get('data_sessao')
        prof_nome = plano.profissional.nome_completo if plano.profissional else "N/A"
        nova = CaeeSessao(
            plano_id=plano_id,
            data_sessao=datetime.strptime(dt, '%Y-%m-%d') if dt else datetime.utcnow(),
            presenca=request.form.get('presenca') == 'true',
            atividades_realizadas=request.form.get('atividades_realizadas'),
            observacoes_evolucao=request.form.get('observacoes_evolucao'),
            profissional_nome=prof_nome
        )
        db.session.add(nova)
        db.session.commit()
        flash('Sessão registrada!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('caee.prontuario_aluno', aluno_id=plano.aluno_id))

@caee_bp.route('/sessao/<int:sessao_id>/editar', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def editar_sessao(sessao_id):
    sessao = CaeeSessao.query.get_or_404(sessao_id)
    if request.method == 'POST':
        try:
            dt = request.form.get('data_sessao')
            sessao.data_sessao = datetime.strptime(dt, '%Y-%m-%d').date() if dt else sessao.data_sessao
            sessao.presenca = request.form.get('presenca') == 'true'
            sessao.atividades_realizadas = request.form.get('atividades_realizadas')
            sessao.observacoes_evolucao = request.form.get('observacoes_evolucao')
            db.session.commit()
            flash('Sessão atualizada!', 'success')
            return redirect(url_for('caee.prontuario_aluno', aluno_id=sessao.plano.aluno_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro: {e}', 'danger')
    return render_template('caee_sessao_form.html', sessao=sessao, aluno_id=sessao.plano.aluno_id)

@caee_bp.route('/sessao/<int:sessao_id>/excluir')
@login_required
@role_required('admin', 'RH', 'CAEE')
def excluir_sessao(sessao_id):
    sessao = CaeeSessao.query.get_or_404(sessao_id)
    aid = sessao.plano.aluno_id
    try:
        db.session.delete(sessao)
        db.session.commit()
        flash('Sessão excluída.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('caee.prontuario_aluno', aluno_id=aid))

def _get_laudos_path():
    return os.path.join(current_app.config['UPLOAD_FOLDER'], 'caee_laudos')

@caee_bp.route('/aluno/<int:aluno_id>/laudo/upload', methods=['POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def upload_laudo(aluno_id):
    if 'laudo_file' not in request.files: return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))
    file = request.files['laudo_file']
    if file.filename == '': return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))
    if file:
        try:
            fname = secure_filename(file.filename)
            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
            fsecure = f"{aluno_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
            path = _get_laudos_path()
            os.makedirs(path, exist_ok=True)
            file.save(os.path.join(path, fsecure))
            novo = CaeeLaudo(
                aluno_id=aluno_id, nome_original=fname, filename_seguro=fsecure,
                descricao=request.form.get('descricao', 'Sem descrição'),
                uploader_nome=session.get('username', 'Sistema')
            )
            db.session.add(novo)
            db.session.commit()
            flash('Laudo anexado!', 'success')
        except Exception as e:
            flash(f'Erro: {e}', 'danger')
    return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))

@caee_bp.route('/laudo/<int:laudo_id>/download')
@login_required
def download_laudo(laudo_id):
    laudo = CaeeLaudo.query.get_or_404(laudo_id)
    return send_from_directory(_get_laudos_path(), laudo.filename_seguro, as_attachment=True, download_name=laudo.nome_original)

@caee_bp.route('/laudo/<int:laudo_id>/excluir')
@login_required
@role_required('admin', 'RH')
def excluir_laudo(laudo_id):
    laudo = CaeeLaudo.query.get_or_404(laudo_id)
    aid = laudo.aluno_id
    try:
        path = os.path.join(_get_laudos_path(), laudo.filename_seguro)
        if os.path.exists(path): os.remove(path)
        db.session.delete(laudo)
        db.session.commit()
        flash('Laudo excluído.', 'success')
    except Exception as e:
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('caee.prontuario_aluno', aluno_id=aid))

# ==========================================================
# 5. RELATÓRIOS PERIÓDICOS E OFICIAIS (REQ #2 e #6)
# ==========================================================

@caee_bp.route('/aluno/<int:aluno_id>/relatorio/novo', methods=['GET', 'POST'])
@caee_bp.route('/relatorio/<int:relatorio_id>/editar', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def gerenciar_relatorio_periodico(aluno_id=None, relatorio_id=None):
    aluno = None
    relatorio = None
    if relatorio_id:
        relatorio = CaeeRelatorioPeriodico.query.get_or_404(relatorio_id)
        aluno = relatorio.aluno
    elif aluno_id:
        aluno = CaeeAluno.query.get_or_404(aluno_id)
    else:
        return redirect(url_for('caee.dashboard'))
    
    secretaria_id_logada = session.get('secretaria_id')
    profissionais = CaeeProfissional.query.filter_by(secretaria_id=secretaria_id_logada, status='Ativo').all()

    if request.method == 'POST':
        try:
            pid = request.form.get('profissional_id', type=int)
            per = request.form.get('periodo')
            revo = request.form.get('relatorio_evolucao')
            if relatorio:
                relatorio.profissional_id = pid
                relatorio.periodo = per
                relatorio.relatorio_evolucao = revo
            else:
                novo = CaeeRelatorioPeriodico(aluno_id=aluno.id, profissional_id=pid, periodo=per, relatorio_evolucao=revo)
                db.session.add(novo)
            db.session.commit()
            flash('Relatório salvo!', 'success')
            return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno.id))
        except Exception as e:
            flash(f'Erro: {e}', 'danger')
    return render_template('caee_relatorio_form.html', aluno=aluno, relatorio=relatorio, profissionais=profissionais)

@caee_bp.route('/relatorio/<int:relatorio_id>/excluir')
@login_required
def excluir_relatorio_periodico(relatorio_id):
    r = CaeeRelatorioPeriodico.query.get_or_404(relatorio_id)
    aid = r.aluno_id
    db.session.delete(r)
    db.session.commit()
    flash('Relatório excluído.', 'success')
    return redirect(url_for('caee.prontuario_aluno', aluno_id=aid))

def _get_alunos_caee_filtrados(filtros):
    secretaria_id_logada = session.get('secretaria_id')
    query = CaeeAluno.query.filter_by(secretaria_id=secretaria_id_logada)
    if filtros.get('status'):
        query = query.filter(CaeeAluno.status == filtros['status'])
    if filtros.get('profissional_id'):
        query = query.join(CaeePlanoAtendimento).filter(CaeePlanoAtendimento.profissional_id == filtros['profissional_id'])
    if filtros.get('necessidade'):
        query = query.filter(CaeeAluno.necessidade_especifica.ilike(f"%{filtros['necessidade']}%"))
    if filtros.get('escola_origem'):
        query = query.filter(CaeeAluno.escola_origem.ilike(f"%{filtros['escola_origem']}%"))
    return query.order_by(CaeeAluno.nome_completo).all()

@caee_bp.route('/relatorios', methods=['GET'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def relatorios():
    filtros = {
        'status': request.args.get('status', ''),
        'profissional_id': request.args.get('profissional_id', type=int),
        'necessidade': request.args.get('necessidade', ''),
        'escola_origem': request.args.get('escola_origem', '')
    }
    alunos = _get_alunos_caee_filtrados(filtros)
    secretaria_id_logada = session.get('secretaria_id')
    profissionais = CaeeProfissional.query.filter_by(secretaria_id=secretaria_id_logada, status='Ativo').all()
    escolas_cadastradas = db.session.query(CaeeAluno.escola_origem).filter(CaeeAluno.escola_origem != None, CaeeAluno.escola_origem != '').distinct().all()

    return render_template('caee_relatorios.html', 
        alunos=alunos, profissionais=profissionais, 
        escolas_cadastradas=[e[0] for e in escolas_cadastradas], 
        filtros_atuais=filtros
    )

@caee_bp.route('/relatorios/exportar/excel')
@login_required
def exportar_relatorio_caee_excel():
    filtros = {
        'status': request.args.get('status', ''),
        'profissional_id': request.args.get('profissional_id', type=int),
        'necessidade': request.args.get('necessidade', ''),
        'escola_origem': request.args.get('escola_origem', '')
    }
    alunos = _get_alunos_caee_filtrados(filtros)
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório CAEE"
    ws.append(["Aluno", "Status", "Escola de Origem", "Necessidade", "Profissional (PAI)"])
    for cell in ws[1]: cell.font = Font(bold=True)
    for aluno in alunos:
        profs = [p.profissional.nome_completo for p in aluno.planos if p.profissional]
        ws.append([
            aluno.nome_completo, aluno.status, aluno.escola_origem, 
            aluno.necessidade_especifica, ", ".join(profs)
        ])
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=relatorio_caee.xlsx"
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return response

@caee_bp.route('/relatorios/exportar/pdf')
@login_required
def exportar_relatorio_caee_pdf():
    filtros = {
        'status': request.args.get('status', ''),
        'profissional_id': request.args.get('profissional_id', type=int),
        'necessidade': request.args.get('necessidade', ''),
        'escola_origem': request.args.get('escola_origem', '')
    }
    alunos = _get_alunos_caee_filtrados(filtros)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=2.5*cm)
    styles = getSampleStyleSheet()
    story = [Paragraph("Relatório Consolidado - Alunos CAEE", styles['h1']), Spacer(1, 0.5*cm)]
    
    header_style = ParagraphStyle(name='Header', fontSize=8, fontName='Helvetica-Bold')
    data = [[Paragraph(x, header_style) for x in ["Aluno", "Status", "Escola", "Necessidade", "Profissionais"]]]
    cell_style = ParagraphStyle(name='Cell', fontSize=7)
    
    for aluno in alunos:
        profs = [p.profissional.nome_completo for p in aluno.planos if p.profissional]
        data.append([
            Paragraph(aluno.nome_completo, cell_style),
            Paragraph(aluno.status, cell_style),
            Paragraph(aluno.escola_origem or '-', cell_style),
            Paragraph(aluno.necessidade_especifica or '-', cell_style),
            Paragraph(", ".join(profs), cell_style)
        ])
    
    table = Table(data, colWidths=[7*cm, 3*cm, 5*cm, 5*cm, 6*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#004d40")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(table)
    doc.build(story, onFirstPage=cabecalho_e_rodape, onLaterPages=cabecalho_e_rodape)
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers["Content-Disposition"] = "inline; filename=relatorio_caee.pdf"
    response.headers["Content-Type"] = "application/pdf"
    return response

@caee_bp.route('/linha_tempo/<int:evento_id>/editar', methods=['POST'])
@login_required
@role_required('admin', 'RH', 'CAEE')
def editar_evento_linha_tempo(evento_id):
    evento = CaeeLinhaTempo.query.get_or_404(evento_id)
    aluno_id = evento.aluno_id
    
    try:
        evento.etapa = request.form.get('etapa')
        evento.observacao = request.form.get('observacao')
        evento.status = request.form.get('status')
        
        # Se quiser mudar o profissional (opcional)
        dest_id = request.form.get('profissional_destino_id', type=int)
        if dest_id:
            evento.profissional_destino_id = dest_id
            
        db.session.commit()
        flash('Evento da linha do tempo atualizado!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar evento: {e}', 'danger')
        
    return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))

@caee_bp.route('/linha_tempo/<int:evento_id>/excluir')
@login_required
@role_required('admin', 'RH')
def excluir_evento_linha_tempo(evento_id):
    evento = CaeeLinhaTempo.query.get_or_404(evento_id)
    aluno_id = evento.aluno_id
    
    try:
        db.session.delete(evento)
        db.session.commit()
        flash('Evento removido do fluxo com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir evento: {e}', 'danger')
        
    return redirect(url_for('caee.prontuario_aluno', aluno_id=aluno_id))