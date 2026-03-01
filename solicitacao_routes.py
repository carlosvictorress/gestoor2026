import io
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from sqlalchemy import extract
from sqlalchemy.orm import joinedload
from functools import wraps

# Importações das suas extensões e modelos
from extensions import db
from models import SetorTransporte, SolicitacaoVeiculo

# Importações para o PDF
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

solicitacao_bp = Blueprint('solicitacao', __name__, url_prefix='/solicitacao')

# --- DECORADORES DE PROTEÇÃO ---

def system_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Acesso negado: faça login no sistema principal.', 'warning')
            return redirect(url_for('login')) # Rota do seu sistema principal
        return f(*args, **kwargs)
    return decorated_function

def transporte_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Acesso restrito ao Admin!', 'danger')
            return redirect(url_for('solicitacao.login_setor'))
        return f(*args, **kwargs)
    return decorated_function

# --- FUNÇÃO DE PDF COM TIMBRE ---
def gerar_pdf_autorizacao(solicitacao):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    
    # Inserção do Timbre - Certifique-se que o arquivo existe em static/timbre.png
    try:
        c.drawImage("static/timbre.png", 50, 750, width=500, height=80)
    except Exception as e:
        print(f"Erro ao carregar timbre: {e}")
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(150, 700, "AUTORIZAÇÃO DE TRANSPORTE ESCOLAR")
    c.setFont("Helvetica", 12)
    c.drawString(50, 650, f"Solicitante: {solicitacao.setor.nome_setor}")
    c.drawString(50, 630, f"Responsável: {solicitacao.responsavel}")
    c.drawString(50, 610, f"Data: {solicitacao.data_solicitada.strftime('%d/%m/%Y')}")
    c.drawString(50, 590, f"Horário: {solicitacao.horario_saida.strftime('%H:%M')} às {solicitacao.horario_chegada.strftime('%H:%M')}")
    c.drawString(50, 570, f"Motivo: {solicitacao.motivo}")
    c.drawString(50, 400, "__________________________________________")
    c.drawString(50, 385, "Assinatura do Administrador / Secretaria")
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

# --- ROTAS ---

@solicitacao_bp.route('/login', methods=['GET', 'POST'])
def login_setor():
    if request.method == 'POST':
        codigo = request.form.get('codigo_setor')
        setor = SetorTransporte.query.filter_by(codigo_setor=codigo).first()
        if setor:
            session['setor_id'] = setor.id
            session['setor_nome'] = setor.nome_setor
            return redirect(url_for('solicitacao.painel_usuario'))
        flash('Código inválido!', 'danger')
    return render_template('solicitacao/login_setor.html')

@solicitacao_bp.route('/painel', methods=['GET'])
def painel_usuario():
    if 'setor_id' not in session:
        return redirect(url_for('solicitacao.login_setor'))
    solicitacoes = SolicitacaoVeiculo.query.filter_by(setor_id=session['setor_id']).all()
    return render_template('solicitacao/painel_usuario.html', solicitacoes=solicitacoes)

@solicitacao_bp.route('/painel', methods=['POST'])
def salvar_solicitacao():
    if 'setor_id' not in session:
        return redirect(url_for('solicitacao.login_setor'))
    
    # Captura os dados do formulário
    data_str = request.form.get('data_solicitada')
    motivo = request.form.get('motivo')
    horario_saida = request.form.get('horario_saida')
    horario_chegada = request.form.get('horario_chegada')
    responsavel = request.form.get('responsavel')
    
    if not data_str:
        flash('Data inválida!', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))

    # Converte os dados
    nova_solicitacao = SolicitacaoVeiculo(
        setor_id=session['setor_id'],
        data_solicitada=datetime.strptime(data_str, '%Y-%m-%d').date(),
        motivo=motivo,
        horario_saida=datetime.strptime(horario_saida, '%H:%M').time(),
        horario_chegada=datetime.strptime(horario_chegada, '%H:%M').time(),
        responsavel=responsavel,
        status='Pendente'
    )
    
    # Salva no banco
    db.session.add(nova_solicitacao)
    db.session.commit()
    
    flash('Solicitação enviada com sucesso!', 'success')
    return redirect(url_for('solicitacao.painel_usuario'))

@solicitacao_bp.route('/admin/painel')
@system_login_required
@transporte_admin_required
def painel_admin():
    solicitacoes = SolicitacaoVeiculo.query.options(joinedload(SolicitacaoVeiculo.setor)).filter_by(status='Pendente').all()
    return render_template('solicitacao/painel_admin.html', solicitacoes=solicitacoes)

@solicitacao_bp.route('/admin/aprovar/<int:id>')
@system_login_required
@transporte_admin_required
def aprovar_solicitacao(id):
    sol = SolicitacaoVeiculo.query.get_or_404(id)
    sol.status = 'Aprovada'
    db.session.commit()
    return send_file(gerar_pdf_autorizacao(sol), mimetype='application/pdf', as_attachment=True, download_name=f'Aut_{sol.id}.pdf')

@solicitacao_bp.route('/admin/reprovar/<int:id>', methods=['POST'])
@system_login_required
@transporte_admin_required
def reprovar_solicitacao(id):
    justificativa = request.form.get('justificativa')
    sol = SolicitacaoVeiculo.query.get_or_404(id)
    
    sol.status = 'Reprovada'
    sol.justificativa = justificativa  # Registra o motivo para seu resguardo
    db.session.commit()
    
    flash(f'Solicitação de {sol.setor.nome_setor} reprovada com sucesso.', 'info')
    return redirect(url_for('solicitacao.painel_admin'))

@solicitacao_bp.route('/admin/cadastrar-setor', methods=['GET', 'POST'])
@system_login_required
@transporte_admin_required
def cadastrar_setor():
    if request.method == 'POST':
        novo = SetorTransporte(
            nome_setor=request.form.get('nome_setor'), 
            codigo_setor=request.form.get('codigo_setor')
        )
        db.session.add(novo)
        db.session.commit()
        flash('Setor cadastrado!', 'success')
        return redirect(url_for('solicitacao.cadastrar_setor'))
    
    # BUSCA TODOS OS SETORES PARA EXIBIR NA TABELA
    setores = SetorTransporte.query.all()
    return render_template('solicitacao/cadastrar_setor.html', setores=setores)

@solicitacao_bp.route('/api/eventos')
def api_eventos():
    # Busca apenas solicitações Aprovadas para mostrar no calendário
    solicitacoes = SolicitacaoVeiculo.query.filter_by(status='Aprovada').all()
    eventos = []
    for sol in solicitacoes:
        eventos.append({
            'title': f'{sol.setor.nome_setor} - {sol.responsavel}',
            'start': sol.data_solicitada.isoformat(),
            'color': '#28a745' # Verde para aprovado
        })
    return jsonify(eventos)