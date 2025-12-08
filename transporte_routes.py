# transporte_routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
from sqlalchemy.orm import joinedload
from datetime import datetime, time
# CORRIJA AS LINHAS ABAIXO
from extensions import db, bcrypt
from models import RotaTransporte, AlunoTransporte, Servidor, Veiculo, TrechoRota
from utils import login_required, fleet_required # Adicione/crie esta importação
from utils import role_required


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
    
    
    
  
