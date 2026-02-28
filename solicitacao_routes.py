import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from flask import make_response
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from extensions import db
from models import SetorTransporte, SolicitacaoVeiculo
from datetime import datetime
from sqlalchemy import extract
from flask import jsonify
from sqlalchemy import extract
from flask import send_file
from flask_login import login_required


solicitacao_bp = Blueprint('solicitacao', __name__, url_prefix='/solicitacao')

# Rota para o Usuário (Setor)
@solicitacao_bp.route('/painel', methods=['GET', 'POST'])
def painel_usuario():
    # 1. Verificar login do setor (usando session)
    # 2. Renderizar o calendário (usando FullCalendar no HTML)
    # 3. Lógica POST para salvar a solicitação (com a trava de 2 por semana)
    pass

@solicitacao_bp.route('/painel', methods=['POST'])
def salvar_solicitacao():
    data_solicitada = datetime.strptime(request.form.get('data_solicitada'), '%Y-%m-%d').date()
    setor_id = session.get('setor_id') # Assumindo que você guardou o setor na session no login

    # Contagem de solicitações na mesma semana e ano
    semana = data_solicitada.isocalendar()[1]
    ano = data_solicitada.year
    
    count = SolicitacaoVeiculo.query.filter(
        SolicitacaoVeiculo.setor_id == setor_id,
        extract('year', SolicitacaoVeiculo.data_solicitada) == ano,
        extract('week', SolicitacaoVeiculo.data_solicitada) == semana
    ).count()

    if count >= 2:
        flash('Limite de 2 solicitações por semana atingido para este setor.', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))

    # Se passar pela trava, salvar o novo objeto SolicitacaoVeiculo no banco...
    # ... (seu código de db.session.add e commit)
    flash('Solicitação enviada com sucesso!', 'success')
    return redirect(url_for('solicitacao.painel_usuario'))


@solicitacao_bp.route('/api/eventos')
def api_eventos():
    # Busca todas as solicitações aprovadas para mostrar no calendário
    solicitacoes = SolicitacaoVeiculo.query.filter_by(status='Aprovada').all()
    eventos = []
    for sol in solicitacoes:
        eventos.append({
            'title': f'Veículo Ocupado - {sol.responsavel}',
            'start': sol.data_solicitada.isoformat(),
            'color': '#ffc107' # Cor amarela para indicar ocupado
        })
    return jsonify(eventos)

@solicitacao_bp.route('/painel')
@login_required # Garanta que apenas usuários logados acessem
def painel_usuario():
    return render_template('solicitacao/painel_usuario.html')


@solicitacao_bp.route('/admin/aprovar/<int:id>')
@login_required
@role_required('admin')
def aprovar_solicitacao(id):
    sol = SolicitacaoVeiculo.query.get_or_404(id)
    sol.status = 'Aprovada'
    db.session.commit()
    
    # Gera o PDF
    pdf_buffer = gerar_pdf_autorizacao(sol)
    
    # Retorna o PDF para download automático
    response = make_response(pdf_buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Autorizacao_{sol.id}.pdf'
    
    flash(f'Solicitação aprovada! PDF gerado.', 'success')
    return response

@solicitacao_bp.route('/admin/painel')
@login_required
@role_required('admin')
def painel_admin():
    # Busca apenas o que está pendente
    solicitacoes = SolicitacaoVeiculo.query.filter_by(status='Pendente').all()
    return render_template('solicitacao/painel_admin.html', solicitacoes=solicitacoes)


def gerar_oficio_autorizacao(solicitacao):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    
    # Cabeçalho básico (ajuste conforme seu 'timbre.jpg')
    c.setFont("Helvetica-Bold", 16)
    c.drawString(150, 800, "AUTORIZAÇÃO DE TRANSPORTE")
    
    c.setFont("Helvetica", 12)
    c.drawString(50, 750, f"Setor Solicitante: {solicitacao.setor.nome_setor}")
    c.drawString(50, 730, f"Responsável: {solicitacao.responsavel}")
    c.drawString(50, 710, f"Data da Viagem: {solicitacao.data_solicitada.strftime('%d/%m/%Y')}")
    c.drawString(50, 690, f"Motivo: {solicitacao.motivo}")
    
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer



def gerar_pdf_autorizacao(solicitacao):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    
    # Cabeçalho - Ajuste conforme seu timbre.jpg
    c.setFont("Helvetica-Bold", 16)
    c.drawString(150, 800, "AUTORIZAÇÃO DE TRANSPORTE ESCOLAR")
    
    c.setFont("Helvetica", 12)
    c.drawString(50, 750, f"Solicitante (Setor): {solicitacao.setor.nome_setor}")
    c.drawString(50, 730, f"Responsável: {solicitacao.responsavel}")
    c.drawString(50, 710, f"Data da Viagem: {solicitacao.data_solicitada.strftime('%d/%m/%Y')}")
    c.drawString(50, 690, f"Horário: {solicitacao.horario_saida.strftime('%H:%M')} às {solicitacao.horario_chegada.strftime('%H:%M')}")
    c.drawString(50, 670, f"Motivo: {solicitacao.motivo}")
    
    # Espaço para assinatura
    c.drawString(50, 500, "__________________________________________")
    c.drawString(50, 485, "Assinatura do Administrador / Secretaria")
    
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

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