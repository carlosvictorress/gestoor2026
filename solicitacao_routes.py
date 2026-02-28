import io
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, make_response, send_file, jsonify
from sqlalchemy import extract
from extensions import db
from models import SetorTransporte, SolicitacaoVeiculo
from functools import wraps
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

solicitacao_bp = Blueprint('solicitacao', __name__, url_prefix='/solicitacao')

# --- DECORADORES ---

def system_login_required(f):
    """Verifica se o usuário está logado no sistema principal."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Por favor, faça login no sistema principal.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def transporte_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Verifica se é admin do sistema
        if session.get('role') != 'admin':
            flash('Acesso restrito ao Admin!', 'danger')
            return redirect(url_for('solicitacao.login_setor'))
        return f(*args, **kwargs)
    return decorated_function

# --- FUNÇÕES DE PDF ---
def gerar_pdf_autorizacao(solicitacao):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(150, 800, "AUTORIZAÇÃO DE TRANSPORTE ESCOLAR")
    c.setFont("Helvetica", 12)
    c.drawString(50, 750, f"Solicitante (Setor): {solicitacao.setor.nome_setor}")
    c.drawString(50, 730, f"Responsável: {solicitacao.responsavel}")
    c.drawString(50, 710, f"Data da Viagem: {solicitacao.data_solicitada.strftime('%d/%m/%Y')}")
    c.drawString(50, 690, f"Horário: {solicitacao.horario_saida.strftime('%H:%M')} às {solicitacao.horario_chegada.strftime('%H:%M')}")
    c.drawString(50, 670, f"Motivo: {solicitacao.motivo}")
    c.drawString(50, 500, "__________________________________________")
    c.drawString(50, 485, "Assinatura do Administrador / Secretaria")
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
        else:
            flash('Código de setor inválido!', 'danger')
    return render_template('solicitacao/login_setor.html')

@solicitacao_bp.route('/painel', methods=['GET'])
@system_login_required 
def painel_usuario():
    return render_template('solicitacao/painel_usuario.html')

@solicitacao_bp.route('/painel', methods=['POST'])
@system_login_required
def salvar_solicitacao():
    data_str = request.form.get('data_solicitada')
    if not data_str:
        return redirect(url_for('solicitacao.painel_usuario'))
    
    data_solicitada = datetime.strptime(data_str, '%Y-%m-%d').date()
    setor_id = session.get('setor_id')

    semana = data_solicitada.isocalendar()[1]
    ano = data_solicitada.year
    
    count = SolicitacaoVeiculo.query.filter(
        SolicitacaoVeiculo.setor_id == setor_id,
        extract('year', SolicitacaoVeiculo.data_solicitada) == ano,
        extract('week', SolicitacaoVeiculo.data_solicitada) == semana
    ).count()

    if count >= 2:
        flash('Limite de 2 solicitações por semana atingido.', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))

    nova_solicitacao = SolicitacaoVeiculo(
        setor_id=setor_id,
        data_solicitada=data_solicitada,
        motivo=request.form.get('motivo'),
        horario_saida=datetime.strptime(request.form.get('horario_saida'), '%H:%M').time(),
        horario_chegada=datetime.strptime(request.form.get('horario_chegada'), '%H:%M').time(),
        responsavel=request.form.get('responsavel')
    )
    db.session.add(nova_solicitacao)
    db.session.commit()
    flash('Solicitação enviada com sucesso!', 'success')
    return redirect(url_for('solicitacao.painel_usuario'))

@solicitacao_bp.route('/admin/painel')
@system_login_required
@transporte_admin_required
def painel_admin():
    solicitacoes = SolicitacaoVeiculo.query.filter_by(status='Pendente').all()
    return render_template('solicitacao/painel_admin.html', solicitacoes=solicitacoes)

@solicitacao_bp.route('/admin/aprovar/<int:id>')
@system_login_required
@transporte_admin_required
def aprovar_solicitacao(id):
    sol = SolicitacaoVeiculo.query.get_or_404(id)
    sol.status = 'Aprovada'
    db.session.commit()
    pdf_buffer = gerar_pdf_autorizacao(sol)
    return send_file(pdf_buffer, mimetype='application/pdf', as_attachment=True, download_name=f'Autorizacao_{sol.id}.pdf')

@solicitacao_bp.route('/api/eventos')
def api_eventos():
    solicitacoes = SolicitacaoVeiculo.query.filter_by(status='Aprovada').all()
    eventos = [{'title': f'Ocupado - {sol.responsavel}', 'start': sol.data_solicitada.isoformat(), 'color': '#ffc107'} for sol in solicitacoes]
    return jsonify(eventos)