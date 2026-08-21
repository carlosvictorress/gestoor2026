# transporte_routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_from_directory, current_app, make_response
from functools import wraps
from sqlalchemy.orm import joinedload
from datetime import datetime, time
import os
import uuid
import io

from extensions import db, bcrypt
from models import RotaTransporte, AlunoTransporte, Servidor, Veiculo, TrechoRota, MotoristaContratado, FolhaPagamentoContratado
from utils import login_required, fleet_required, role_required, cabecalho_e_rodape

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY


import requests
import json
from flask import jsonify

# O resto do arquivo continua exatamente igual...
transporte_bp = Blueprint(
    'transporte', 
    __name__, 
    template_folder='templates', 
    url_prefix='/transporte'
)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@transporte_bp.route('/dashboard')
@login_required
@role_required('Combustivel', 'admin')
def dashboard():
    total_rotas = RotaTransporte.query.count()
    total_alunos = AlunoTransporte.query.count()
    alunos_manha = db.session.query(db.func.sum(RotaTransporte.qtd_alunos_manha)).scalar() or 0
    alunos_tarde = db.session.query(db.func.sum(RotaTransporte.qtd_alunos_tarde)).scalar() or 0
    total_motoristas = db.session.query(RotaTransporte.motorista_cpf).distinct().count()

    return render_template('transporte_dashboard.html', 
                           total_rotas=total_rotas,
                           total_alunos=total_alunos,
                           total_motoristas=total_motoristas,
                           alunos_manha=int(alunos_manha),
                           alunos_tarde=int(alunos_tarde))

@transporte_bp.route('/rotas')
@login_required
@role_required('Combustivel', 'admin')
def listar_rotas():
    rotas = RotaTransporte.query.options(
        joinedload(RotaTransporte.motorista),
        joinedload(RotaTransporte.veiculo)
    ).order_by(RotaTransporte.id).all()
    
    return render_template('listar_rotas.html', rotas=rotas)


@transporte_bp.route('/rotas/nova', methods=['GET', 'POST'])
@login_required
@role_required('Combustivel', 'admin')
def nova_rota():
    """Cria uma nova rota de transporte."""
    rota = RotaTransporte()
    if request.method == 'POST':
        try:
            # Salva os dados principais
            rota.motorista_cpf = request.form.get('motorista_cpf')
            rota.veiculo_placa = request.form.get('veiculo_placa')
            rota.monitor_cpf = request.form.get('monitor_cpf') or None
            rota.escolas_manha = request.form.get('escolas_manha')
            rota.coordenadas_manha = request.form.get('coordenadas_manha')
            rota.escolas_tarde = request.form.get('escolas_tarde')
            rota.coordenadas_tarde = request.form.get('coordenadas_tarde')

            # --- LÓGICA PARA SALVAR NOVOS CAMPOS ---
            saida_manha_str = request.form.get('horario_saida_manha')
            volta_manha_str = request.form.get('horario_volta_manha')
            saida_tarde_str = request.form.get('horario_saida_tarde')
            volta_tarde_str = request.form.get('horario_volta_tarde')

            rota.horario_saida_manha = time.fromisoformat(saida_manha_str) if saida_manha_str else None
            rota.horario_volta_manha = time.fromisoformat(volta_manha_str) if volta_manha_str else None
            rota.horario_saida_tarde = time.fromisoformat(saida_tarde_str) if saida_tarde_str else None
            rota.horario_volta_tarde = time.fromisoformat(volta_tarde_str) if volta_tarde_str else None

            db.session.add(rota)
            db.session.commit() # Salva a rota para obter um ID

            # Função auxiliar para processar trechos
            def processar_trechos(turno, tipo_viagem):
                descricoes = request.form.getlist(f'descricao_{tipo_viagem}_{turno}[]')
                distancias = request.form.getlist(f'distancia_{tipo_viagem}_{turno}[]')
                for i in range(len(distancias)):
                    if distancias[i]:
                        trecho = TrechoRota(
                            rota_id=rota.id,
                            turno=turno,
                            tipo_viagem=tipo_viagem,
                            distancia_km=float(distancias[i].replace(',', '.')),
                            descricao=descricoes[i]
                        )
                        db.session.add(trecho)
            
            processar_trechos('manha', 'ida')
            processar_trechos('manha', 'volta')
            processar_trechos('tarde', 'ida')
            processar_trechos('tarde', 'volta')
            
            db.session.commit() # Salva os trechos
            flash('Rota criada com sucesso!', 'success')
            return redirect(url_for('transporte.listar_rotas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao criar a rota: {e}', 'danger')

    motoristas = Servidor.query.all()
    veiculos = Veiculo.query.all()
    return render_template('rota_form.html', rota=rota, motoristas=motoristas, veiculos=veiculos)


@transporte_bp.route('/rotas/editar/<int:rota_id>', methods=['GET', 'POST'])
@login_required
@role_required('Combustivel', 'admin')
def editar_rota(rota_id):
    """Edita uma rota de transporte existente."""
    rota = RotaTransporte.query.get_or_404(rota_id)
    if request.method == 'POST':
        try:
            # Salva os dados principais
            rota.motorista_cpf = request.form.get('motorista_cpf')
            rota.veiculo_placa = request.form.get('veiculo_placa')
            rota.monitor_cpf = request.form.get('monitor_cpf') or None
            rota.escolas_manha = request.form.get('escolas_manha')
            rota.coordenadas_manha = request.form.get('coordenadas_manha')
            rota.escolas_tarde = request.form.get('escolas_tarde')
            rota.coordenadas_tarde = request.form.get('coordenadas_tarde')

            # --- LÓGICA PARA SALVAR NOVOS CAMPOS ---
            saida_manha_str = request.form.get('horario_saida_manha')
            volta_manha_str = request.form.get('horario_volta_manha')
            saida_tarde_str = request.form.get('horario_saida_tarde')
            volta_tarde_str = request.form.get('horario_volta_tarde')

            rota.horario_saida_manha = time.fromisoformat(saida_manha_str) if saida_manha_str else None
            rota.horario_volta_manha = time.fromisoformat(volta_manha_str) if volta_manha_str else None
            rota.horario_saida_tarde = time.fromisoformat(saida_tarde_str) if saida_tarde_str else None
            rota.horario_volta_tarde = time.fromisoformat(volta_tarde_str) if volta_tarde_str else None
            
            # Limpa trechos antigos antes de adicionar os novos
            TrechoRota.query.filter_by(rota_id=rota.id).delete()

            # Função auxiliar para processar trechos
            def processar_trechos(turno, tipo_viagem):
                descricoes = request.form.getlist(f'descricao_{tipo_viagem}_{turno}[]')
                distancias = request.form.getlist(f'distancia_{tipo_viagem}_{turno}[]')
                for i in range(len(distancias)):
                    if distancias[i]:
                        trecho = TrechoRota(
                            rota_id=rota.id,
                            turno=turno,
                            tipo_viagem=tipo_viagem,
                            distancia_km=float(distancias[i].replace(',', '.')),
                            descricao=descricoes[i]
                        )
                        db.session.add(trecho)
            
            processar_trechos('manha', 'ida')
            processar_trechos('manha', 'volta')
            processar_trechos('tarde', 'ida')
            processar_trechos('tarde', 'volta')

            db.session.commit()
            flash('Rota atualizada com sucesso!', 'success')
            return redirect(url_for('transporte.listar_rotas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao editar a rota: {e}', 'danger')

    # Lógica para carregar dados para o formulário (GET)
    trechos_ida_manha = [t for t in rota.trechos if t.turno == 'manha' and t.tipo_viagem == 'ida']
    trechos_volta_manha = [t for t in rota.trechos if t.turno == 'manha' and t.tipo_viagem == 'volta']
    trechos_ida_tarde = [t for t in rota.trechos if t.turno == 'tarde' and t.tipo_viagem == 'ida']
    trechos_volta_tarde = [t for t in rota.trechos if t.turno == 'tarde' and t.tipo_viagem == 'volta']
    motoristas = Servidor.query.all()
    veiculos = Veiculo.query.all()
    return render_template('rota_form.html', rota=rota, motoristas=motoristas, veiculos=veiculos,
                           trechos_ida_manha=trechos_ida_manha, trechos_volta_manha=trechos_volta_manha,
                           trechos_ida_tarde=trechos_ida_tarde, trechos_volta_tarde=trechos_volta_tarde)
    
@transporte_bp.route('/rotas/detalhes/<int:rota_id>', methods=['GET', 'POST'])
@login_required
@role_required('Combustivel', 'admin')
def detalhes_rota(rota_id):
    rota = RotaTransporte.query.options(
        joinedload(RotaTransporte.alunos),
        joinedload(RotaTransporte.trechos)
    ).get_or_404(rota_id)

    if request.method == 'POST':
        try:
            # Coleta de dados do formulário
            nome_completo = request.form.get('nome_completo')
            data_nascimento_str = request.form.get('data_nascimento')
            ano_estudo = request.form.get('ano_estudo')
            turno = request.form.get('turno')
            escola = request.form.get('escola')
            zona = request.form.get('zona')
            nome_responsavel = request.form.get('nome_responsavel')
            telefone_responsavel = request.form.get('telefone_responsavel')
            endereco_aluno = request.form.get('endereco_aluno')

            # Validação simples
            if not all([nome_completo, data_nascimento_str, ano_estudo, turno, escola, zona, nome_responsavel, telefone_responsavel, endereco_aluno]):
                flash('Todos os campos marcados com * são obrigatórios.', 'danger')
                return redirect(url_for('transporte.detalhes_rota', rota_id=rota_id))
            
            data_nascimento = datetime.strptime(data_nascimento_str, '%Y-%m-%d').date()

            novo_aluno = AlunoTransporte(
                nome_completo=nome_completo,
                data_nascimento=data_nascimento,
                ano_estudo=ano_estudo,
                turno=turno,
                escola=escola,
                zona=zona,
                nome_responsavel=nome_responsavel,
                telefone_responsavel=telefone_responsavel,
                endereco_aluno=endereco_aluno,
                rota_id=rota.id,
                sexo=request.form.get('sexo'),
                cor=request.form.get('cor'),
                nivel_ensino=request.form.get('nivel_ensino'),
                possui_deficiencia='possui_deficiencia' in request.form,
                tipo_deficiencia=request.form.get('tipo_deficiencia') if 'possui_deficiencia' in request.form else None
            )

            db.session.add(novo_aluno)
            db.session.flush()  # Garante que o novo aluno seja contado abaixo

            # Atualiza a contagem de alunos na rota
            rota.qtd_alunos_manha = AlunoTransporte.query.filter_by(rota_id=rota.id, turno='Manhã').count()
            rota.qtd_alunos_tarde = AlunoTransporte.query.filter_by(rota_id=rota.id, turno='Tarde').count()

            db.session.commit()
            flash(f'Aluno "{nome_completo}" adicionado à rota com sucesso!', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao adicionar aluno: {e}', 'danger')

        return redirect(url_for('transporte.detalhes_rota', rota_id=rota_id))

    # Lógica para o método GET (exibição da página)
    trechos_ida_manha = [t for t in rota.trechos if t.turno == 'manha' and t.tipo_viagem == 'ida']
    trechos_volta_manha = [t for t in rota.trechos if t.turno == 'manha' and t.tipo_viagem == 'volta']
    trechos_ida_tarde = [t for t in rota.trechos if t.turno == 'tarde' and t.tipo_viagem == 'ida']
    trechos_volta_tarde = [t for t in rota.trechos if t.turno == 'tarde' and t.tipo_viagem == 'volta']

    total_km_ida_manha = sum(t.distancia_km for t in trechos_ida_manha)
    total_km_volta_manha = sum(t.distancia_km for t in trechos_volta_manha)
    total_km_ida_tarde = sum(t.distancia_km for t in trechos_ida_tarde)
    total_km_volta_tarde = sum(t.distancia_km for t in trechos_volta_tarde)

    return render_template('detalhes_rota.html', rota=rota,
                           trechos_ida_manha=trechos_ida_manha,
                           trechos_volta_manha=trechos_volta_manha,
                           trechos_ida_tarde=trechos_ida_tarde,
                           trechos_volta_tarde=trechos_volta_tarde,
                           total_km_ida_manha=total_km_ida_manha,
                           total_km_volta_manha=total_km_volta_manha,
                           total_km_ida_tarde=total_km_ida_tarde,
                           total_km_volta_tarde=total_km_volta_tarde)
    
    
    
@transporte_bp.route('/aluno/excluir/<int:aluno_id>')
@login_required
@role_required('Combustivel', 'admin')
def excluir_aluno(aluno_id):
    aluno = AlunoTransporte.query.get_or_404(aluno_id)
    rota_id = aluno.rota_id # Guarda o ID da rota para o redirecionamento
    rota = RotaTransporte.query.get(rota_id)

    try:
        db.session.delete(aluno)
        db.session.flush()

        # Atualiza a contagem de alunos na rota após a exclusão
        if rota:
            rota.qtd_alunos_manha = AlunoTransporte.query.filter_by(rota_id=rota.id, turno='Manhã').count()
            rota.qtd_alunos_tarde = AlunoTransporte.query.filter_by(rota_id=rota.id, turno='Tarde').count()

        db.session.commit()
        flash('Aluno removido da rota com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao remover aluno: {e}', 'danger')

    return redirect(url_for('transporte.detalhes_rota', rota_id=rota_id))  





@transporte_bp.route('/aluno/editar/<int:aluno_id>', methods=['GET', 'POST'])
@login_required
@role_required('Combustivel', 'admin')
def editar_aluno(aluno_id):
    aluno = AlunoTransporte.query.get_or_404(aluno_id)
    rota_id = aluno.rota_id  # Guardar o ID da rota para o redirecionamento

    if request.method == 'POST':
        try:
            # Coleta todos os dados do formulário, incluindo os novos
            aluno.nome_completo = request.form.get('nome_completo')
            data_nascimento_str = request.form.get('data_nascimento')
            aluno.data_nascimento = datetime.strptime(data_nascimento_str, '%Y-%m-%d').date() if data_nascimento_str else aluno.data_nascimento
            aluno.ano_estudo = request.form.get('ano_estudo')
            aluno.turno = request.form.get('turno')
            aluno.escola = request.form.get('escola')
            aluno.zona = request.form.get('zona')
            aluno.nome_responsavel = request.form.get('nome_responsavel')
            aluno.telefone_responsavel = request.form.get('telefone_responsavel')
            aluno.endereco_aluno = request.form.get('endereco_aluno')
            aluno.sexo = request.form.get('sexo')
            aluno.cor = request.form.get('cor')
            aluno.nivel_ensino = request.form.get('nivel_ensino')
            aluno.possui_deficiencia = 'possui_deficiencia' in request.form
            aluno.tipo_deficiencia = request.form.get('tipo_deficiencia') if aluno.possui_deficiencia else None
            
            db.session.commit()
            flash('Dados do aluno atualizados com sucesso!', 'success')
            return redirect(url_for('transporte.detalhes_rota', rota_id=rota_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar dados do aluno: {e}', 'danger')

    # Para o método GET, apenas exibe o formulário de edição
    return render_template('aluno_form.html', aluno=aluno)


@transporte_bp.route('/aluno/imprimir/<int:aluno_id>')
@login_required
@role_required('Combustivel', 'admin')
def imprimir_aluno(aluno_id):
    aluno = AlunoTransporte.query.get_or_404(aluno_id)
    
    # Importações necessárias para o PDF
    from .utils import cabecalho_e_rodape_moderno
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from flask import make_response
    import io

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=3*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    # Conteúdo do PDF
    story.append(Paragraph(f"Ficha Cadastral do Aluno", styles['h1']))
    story.append(Spacer(1, 0.5*cm))

    data_nasc = aluno.data_nascimento.strftime('%d/%m/%Y') if aluno.data_nascimento else 'Não informado'
    deficiencia_str = f"Sim ({aluno.tipo_deficiencia})" if aluno.possui_deficiencia else "Não"

    dados = [
        ['Nome Completo:', aluno.nome_completo],
        ['Data de Nascimento:', data_nasc],
        ['Sexo:', aluno.sexo or 'Não informado'],
        ['Cor/Raça:', aluno.cor or 'Não informado'],
        ['Endereço:', aluno.endereco_aluno],
        ['Escola:', aluno.escola],
        ['Nível de Ensino:', aluno.nivel_ensino or 'Não informado'],
        ['Ano/Série:', aluno.ano_estudo],
        ['Turno:', aluno.turno],
        ['Possui Deficiência:', deficiencia_str],
        ['Nome do Responsável:', aluno.nome_responsavel],
        ['Telefone do Responsável:', aluno.telefone_responsavel]
    ]

    t = Table(dados, colWidths=[5*cm, 12*cm])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey)
    ]))
    story.append(t)

    doc.build(story, onFirstPage=lambda canvas, doc: cabecalho_e_rodape_moderno(canvas, doc, "Ficha do Aluno"))
    
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Ficha_{aluno.nome_completo.replace(" ", "_")}.pdf'
    return response
    
    
@transporte_bp.route('/api/rota/<int:rota_id>/coords/<string:turno>')
@login_required
@role_required('Combustivel', 'admin')
def get_rota_coords(rota_id, turno):
    rota = RotaTransporte.query.get_or_404(rota_id)
    
    coordenadas_json = None
    if turno == 'manha':
        coordenadas_json = rota.coordenadas_manha
    elif turno == 'tarde':
        coordenadas_json = rota.coordenadas_tarde

    # Se tivermos coordenadas desenhadas, use-as
    if coordenadas_json:
        try:
            coordenadas = json.loads(coordenadas_json)
            return jsonify(coordenadas)
        except json.JSONDecodeError:
            return jsonify({'error': 'Formato de coordenadas inválido.'}), 500

    # Se não, volte para o método antigo de geocodificação do texto
    itinerario_texto = rota.itinerario_manha if turno == 'manha' else rota.itinerario_tarde
    if not itinerario_texto:
        return jsonify({'error': 'Itinerário não fornecido.'}), 404

    return jsonify(coordenadas)    
      
    
       
@transporte_bp.route('/rotas/excluir/<int:rota_id>')
@login_required
@role_required('Combustivel', 'admin')
def excluir_rota(rota_id):
    # Procura a rota pelo ID ou retorna um erro 404 se não for encontrada
    rota_para_excluir = RotaTransporte.query.get_or_404(rota_id)
    
    try:
        # Exclui a rota do banco de dados.
        # Graças à configuração 'cascade', todos os alunos desta rota também serão excluídos.
        db.session.delete(rota_para_excluir)
        
        # Confirma a transação
        db.session.commit()
        
        flash(f'Rota #{rota_id} e todos os seus alunos associados foram excluídos com sucesso!', 'success')

    except Exception as e:
        # Em caso de erro, desfaz a transação para não corromper os dados
        db.session.rollback()
        flash(f'Ocorreu um erro ao excluir a rota: {e}', 'danger')

    # Redireciona o usuário de volta para a lista de todas as rotas
    return redirect(url_for('transporte.listar_rotas'))


# ==============================================================================
# MÓDULO: MOTORISTAS CONTRATADOS DO TRANSPORTE ESCOLAR E FOLHA DE PAGAMENTO
# ==============================================================================

def gerar_pdf_folha_pagamento_contratado(motorista, folha, filepath):
    """Gera o arquivo PDF de folha de pagamento contendo restritamente os dados especificados."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=2.0*cm,
        bottomMargin=2.0*cm
    )
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#07132B')
    )
    
    subtitle_style = ParagraphStyle(
        'SubtitleStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#2563EB')
    )
    
    label_style = ParagraphStyle(
        'LabelStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#334155')
    )
    
    value_style = ParagraphStyle(
        'ValueStyle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor('#0F172A')
    )

    story.append(Paragraph("FOLHA DE PAGAMENTO - TRANSPORTE ESCOLAR CONTRATADO", title_style))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(f"MÊS DE REFERÊNCIA: {folha.mes_referencia.upper()}", subtitle_style))
    story.append(Spacer(1, 0.4*cm))

    # Banner de metadados
    cod_auth = folha.codigo_autenticacao or uuid.uuid4().hex[:12].upper()
    dt_emissao = folha.data_emissao.strftime('%d/%m/%Y às %H:%M') if folha.data_emissao else datetime.now().strftime('%d/%m/%Y às %H:%M')
    
    meta_data = [
        [
            Paragraph(f"<b>CÓDIGO DE AUTENTICAÇÃO:</b> {cod_auth}", label_style),
            Paragraph(f"<b>DATA DE EMISSÃO:</b> {dt_emissao}", label_style)
        ]
    ]
    t_meta = Table(meta_data, colWidths=[9.5*cm, 8.5*cm])
    t_meta.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F1F5F9')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW', (0,0), (-1,-1), 1, colors.HexColor('#CBD5E1')),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 0.6*cm))

    valor_fmt = f"R$ {folha.valor:,.2f}".replace(",", "v").replace(".", ",").replace("v", ".")

    # Tabela com as 9 informações solicitadas exatamente
    table_data = [
        [Paragraph("<b>CAMPO</b>", label_style), Paragraph("<b>DADO REGISTRADO</b>", label_style)],
        [Paragraph("NOME DO MOTORISTA", label_style), Paragraph(motorista.nome, value_style)],
        [Paragraph("CPF DO MOTORISTA", label_style), Paragraph(motorista.cpf, value_style)],
        [Paragraph("ROTA", label_style), Paragraph(motorista.rota, value_style)],
        [Paragraph("AGÊNCIA BANCÁRIA", label_style), Paragraph(motorista.agencia, value_style)],
        [Paragraph("CONTA BANCÁRIA", label_style), Paragraph(motorista.conta, value_style)],
        [Paragraph("TIPO DE CONTA", label_style), Paragraph(motorista.tipo_conta, value_style)],
        [Paragraph("VALOR", label_style), Paragraph(f"<b>{valor_fmt}</b>", value_style)],
        [Paragraph("VEÍCULO", label_style), Paragraph(motorista.veiculo, value_style)],
        [Paragraph("PLACA DO VEÍCULO", label_style), Paragraph(motorista.veiculo_placa, value_style)],
    ]

    tbl = Table(table_data, colWidths=[6.5*cm, 11.5*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E2E8F0')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('PADDING', (0,0), (-1,-1), 7),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 1.8*cm))

    # Assinaturas
    linha = "______________________________________________________"
    sig_data = [
        [Paragraph(linha, label_style)],
        [Paragraph(f"<b>{motorista.nome.upper()}</b>", label_style)],
        [Paragraph("Assinatura do Motorista Contratado", label_style)],
        [Spacer(1, 1.0*cm)],
        [Paragraph(linha, label_style)],
        [Paragraph("Gestão de Transporte Escolar / Controle Interno", label_style)]
    ]
    t_sig = Table(sig_data, colWidths=[18*cm])
    t_sig.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ]))
    story.append(t_sig)

    try:
        doc.build(story, onFirstPage=cabecalho_e_rodape, onLaterPages=cabecalho_e_rodape)
    except Exception:
        doc.build(story)


@transporte_bp.route('/motoristas-contratados')
@login_required
@role_required('Combustivel', 'admin')
def motoristas_contratados():
    """Exibe a dashboard elegante de motoristas contratados e folhas de pagamento."""
    try:
        db.create_all()
    except Exception as e:
        pass

    motoristas = MotoristaContratado.query.order_by(MotoristaContratado.nome.asc()).all()
    folhas = FolhaPagamentoContratado.query.order_by(FolhaPagamentoContratado.data_emissao.desc()).all()
    rotas_existentes = RotaTransporte.query.all()

    total_motoristas = len(motoristas)
    total_com_monitor = sum(1 for m in motoristas if m.tem_monitor)
    folha_mensal_total = sum(m.valor_recebe for m in motoristas)
    total_folhas_geradas = len(folhas)

    return render_template(
        'motoristas_contratados.html',
        motoristas=motoristas,
        folhas=folhas,
        rotas_existentes=rotas_existentes,
        total_motoristas=total_motoristas,
        total_com_monitor=total_com_monitor,
        folha_mensal_total=folha_mensal_total,
        total_folhas_geradas=total_folhas_geradas
    )


@transporte_bp.route('/motoristas-contratados/salvar', methods=['POST'])
@login_required
@role_required('Combustivel', 'admin')
def salvar_motorista_contratado():
    """Cadastra ou atualiza um motorista contratado do transporte escolar."""
    try:
        motorista_id = request.form.get('id')
        nome = request.form.get('nome', '').strip()
        cpf = request.form.get('cpf', '').strip()
        rota = request.form.get('rota', '').strip()
        tem_monitor = request.form.get('tem_monitor') in ['on', 'true', '1', True]
        
        monitor_nome = request.form.get('monitor_nome', '').strip() if tem_monitor else None
        monitor_cpf = request.form.get('monitor_cpf', '').strip() if tem_monitor else None
        
        veiculo = request.form.get('veiculo', '').strip()
        veiculo_placa = request.form.get('veiculo_placa', '').strip().upper()
        
        banco = request.form.get('banco', '').strip()
        agencia = request.form.get('agencia', '').strip()
        conta = request.form.get('conta', '').strip()
        tipo_conta = request.form.get('tipo_conta', '').strip()
        
        valor_str = request.form.get('valor_recebe', '0').replace('.', '').replace(',', '.')
        try:
            valor_recebe = float(valor_str)
        except ValueError:
            valor_recebe = 0.0

        if motorista_id:
            motorista = MotoristaContratado.query.get_or_404(int(motorista_id))
            flash_msg = f'Motorista Contratado "{nome}" atualizado com sucesso!'
        else:
            motorista = MotoristaContratado()
            db.session.add(motorista)
            flash_msg = f'Motorista Contratado "{nome}" cadastrado com sucesso!'

        motorista.nome = nome
        motorista.cpf = cpf
        motorista.rota = rota
        motorista.tem_monitor = tem_monitor
        motorista.monitor_nome = monitor_nome
        motorista.monitor_cpf = monitor_cpf
        motorista.veiculo = veiculo
        motorista.veiculo_placa = veiculo_placa
        motorista.banco = banco
        motorista.agencia = agencia
        motorista.conta = conta
        motorista.tipo_conta = tipo_conta
        motorista.valor_recebe = valor_recebe

        db.session.commit()
        flash(flash_msg, 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao salvar motorista contratado: {e}', 'danger')

    return redirect(url_for('transporte.motoristas_contratados'))


@transporte_bp.route('/motoristas-contratados/excluir/<int:id>', methods=['POST', 'GET'])
@login_required
@role_required('Combustivel', 'admin')
def excluir_motorista_contratado(id):
    """Exclui um motorista contratado."""
    try:
        motorista = MotoristaContratado.query.get_or_404(id)
        nome = motorista.nome
        db.session.delete(motorista)
        db.session.commit()
        flash(f'Motorista Contratado "{nome}" excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir motorista contratado: {e}', 'danger')

    return redirect(url_for('transporte.motoristas_contratados'))


@transporte_bp.route('/motoristas-contratados/gerar-folha', methods=['POST'])
@login_required
@role_required('Combustivel', 'admin')
def gerar_folha_pagamento():
    """Gera a folha de pagamento em PDF para um ou todos os motoristas contratados."""
    try:
        motorista_id_raw = request.form.get('motorista_id')
        mes_referencia = request.form.get('mes_referencia', '').strip() or datetime.now().strftime('%B/%Y').capitalize()

        if not motorista_id_raw:
            flash('Selecione um motorista para gerar a folha de pagamento.', 'warning')
            return redirect(url_for('transporte.motoristas_contratados'))

        if motorista_id_raw == 'todos':
            motoristas = MotoristaContratado.query.all()
        else:
            motoristas = [MotoristaContratado.query.get_or_404(int(motorista_id_raw))]

        if not motoristas:
            flash('Nenhum motorista contratado encontrado.', 'warning')
            return redirect(url_for('transporte.motoristas_contratados'))

        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'folhas_pagamento')
        os.makedirs(upload_dir, exist_ok=True)

        count_geradas = 0
        for m in motoristas:
            cod_auth = uuid.uuid4().hex[:12].upper()
            safe_nome = "".join(c for c in m.nome if c.isalnum() or c in (' ', '_', '-')).rstrip()
            safe_nome = safe_nome.replace(" ", "_")
            filename = f"Folha_{safe_nome}_{cod_auth}.pdf"
            filepath = os.path.join(upload_dir, filename)

            nova_folha = FolhaPagamentoContratado(
                motorista_id=m.id,
                mes_referencia=mes_referencia,
                data_emissao=datetime.utcnow(),
                valor=m.valor_recebe,
                arquivo_pdf=filename,
                codigo_autenticacao=cod_auth
            )
            db.session.add(nova_folha)
            db.session.flush()

            gerar_pdf_folha_pagamento_contratado(m, nova_folha, filepath)
            count_geradas += 1

        db.session.commit()
        flash(f'Folha de pagamento gerada com sucesso para {count_geradas} motorista(s)!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao gerar folha de pagamento: {e}', 'danger')

    return redirect(url_for('transporte.motoristas_contratados'))


@transporte_bp.route('/motoristas-contratados/folha/<filename>')
@login_required
@role_required('Combustivel', 'admin')
def download_folha_pagamento(filename):
    """Download/visualização do PDF da folha de pagamento gerada."""
    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'folhas_pagamento')
    return send_from_directory(upload_dir, filename, as_attachment=False)


@transporte_bp.route('/motoristas-contratados/folha/<int:id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('Combustivel', 'admin')
def excluir_folha_pagamento(id):
    """Exclui um registro de folha de pagamento gerada e seu arquivo PDF."""
    try:
        folha = FolhaPagamentoContratado.query.get_or_404(id)
        upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'folhas_pagamento')
        filepath = os.path.join(upload_dir, folha.arquivo_pdf)

        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass

        db.session.delete(folha)
        db.session.commit()
        flash('Arquivo de folha de pagamento excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir folha de pagamento: {e}', 'danger')

    return redirect(url_for('transporte.motoristas_contratados'))
