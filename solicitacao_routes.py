import io
import os
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file, jsonify, current_app
from sqlalchemy import extract
from sqlalchemy.orm import joinedload
from functools import wraps

# Importações das suas extensões e modelos
from extensions import db
from models import SetorTransporte, SolicitacaoVeiculo

# Importações para o PDF Profissional (ReportLab)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

solicitacao_bp = Blueprint('solicitacao', __name__, url_prefix='/solicitacao')

# --- DECORADORES DE PROTEÇÃO ---

def system_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Acesso negado: faça login no sistema principal.', 'warning')
            return redirect(url_for('login')) 
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

# --- FUNÇÕES DE GERAÇÃO DE PDF ---

def gerar_pdf_autorizacao(solicitacao):
    buffer = io.BytesIO()
    
    # Caminho absoluto robusto para o ambiente Docker
    base_dir = os.path.dirname(os.path.abspath(__file__))
    caminho_timbre = os.path.join(base_dir, 'static', 'timbre.png')
    
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica-Bold', 80)
        canvas.setFillAlpha(0.1)
        canvas.translate(A4[0]/2, A4[1]/2)
        canvas.rotate(45)
        canvas.drawCentredString(0, 0, "APROVADO")
        canvas.restoreState()

    # Configuração do Documento
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4,
        rightMargin=1.5*cm, leftMargin=1.5*cm, 
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )
    
    elements = []
    styles = getSampleStyleSheet()
    
    # Estilo base para as células da tabela (necessário para renderizar as tags <b>)
    style_tabela = styles['Normal']
    style_tabela.fontSize = 11

    # 1. CABEÇALHO (TIMBRE)
    if os.path.exists(caminho_timbre):
        try:
            img = Image(caminho_timbre, width=17*cm, height=2.5*cm)
            elements.append(img)
        except Exception as e:
            print(f"Erro ao processar imagem no ReportLab: {e}")
            elements.append(Paragraph("<b>PREFEITURA DE VALENÇA DO PIAUÍ</b>", styles['Title']))
    else:
        # Se não encontrar o arquivo, coloca o texto para não dar erro 500
        elements.append(Paragraph("<b>PREFEITURA DE VALENÇA DO PIAUÍ</b>", styles['Title']))
    
    elements.append(Spacer(1, 0.8*cm))

    # 2. TÍTULO
    titulo_style = ParagraphStyle(
        'Tit', parent=styles['Title'], fontSize=16, 
        textColor=colors.HexColor("#2c3e50"), spaceAfter=20
    )
    elements.append(Paragraph(f"AUTORIZAÇÃO DE TRANSPORTE Nº {solicitacao.id:04d}", titulo_style))

    # 3. TABELA DE DADOS (USANDO PARAGRAPH EM TODAS AS CÉLULAS)
    # Isso resolve o problema das tags <b> aparecendo como texto
    data = [
        [Paragraph("<b>SETOR SOLICITANTE:</b>", style_tabela), Paragraph(solicitacao.setor.nome_setor, style_tabela)],
        [Paragraph("<b>RESPONSÁVEL:</b>", style_tabela), Paragraph(solicitacao.responsavel, style_tabela)],
        [Paragraph("<b>VEÍCULO AUTORIZADO:</b>", style_tabela), Paragraph(f"<b>{solicitacao.veiculo_solicitado}</b>", style_tabela)],
        [Paragraph("<b>DATA DA VIAGEM:</b>", style_tabela), Paragraph(solicitacao.data_solicitada.strftime('%d/%m/%Y'), style_tabela)],
        [Paragraph("<b>HORÁRIO:</b>", style_tabela), Paragraph(f"{solicitacao.horario_saida.strftime('%H:%M')} às {solicitacao.horario_chegada.strftime('%H:%M')}", style_tabela)],
        [Paragraph("<b>MOTIVO / DESTINO:</b>", style_tabela), Paragraph(solicitacao.motivo, style_tabela)]
    ]

    t = Table(data, colWidths=[5*cm, 12*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(t)

    # 4. ASSINATURAS
    elements.append(Spacer(1, 4*cm))
    assinatura_data = [
        ["_______________________________________", "_______________________________________"],
        ["Assinatura do Responsável", "Visto da Administração / Secretaria"]
    ]
    t_ass = Table(assinatura_data, colWidths=[8.5*cm, 8.5*cm])
    t_ass.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.darkgrey),
    ]))
    elements.append(t_ass)

    # 5. RODAPÉ
    elements.append(Spacer(1, 2.5*cm))
    emissao = datetime.now().strftime("%d/%m/%Y às %H:%M")
    footer = Paragraph(
        f"<font color='grey' size='8'>Documento gerado pelo sistema <b>Gestor 360</b> em {emissao}.</font>", 
        styles['Normal']
    )
    elements.append(footer)

    doc.build(elements, onFirstPage=on_page, onLaterPages=on_page)
    
    buffer.seek(0)
    return buffer

def gerar_pdf_relatorio_consolidado(solicitacoes, mes, ano):
    buffer = io.BytesIO()
    caminho_timbre = os.path.join(current_app.root_path, 'static', 'timbre.png')
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), margin=1*cm)
    elements = []
    styles = getSampleStyleSheet()
    
    if os.path.exists(caminho_timbre):
        try:
            img = Image(caminho_timbre, width=20*cm, height=2.5*cm)
            elements.append(img)
        except Exception:
            elements.append(Paragraph("PREFEITURA DE VALENÇA DO PIAUÍ", styles['Title']))
    else:
        elements.append(Paragraph("PREFEITURA DE VALENÇA DO PIAUÍ", styles['Title']))
    
    elements.append(Spacer(1, 0.5*cm))
    elements.append(Paragraph(f"RELATÓRIO MENSAL DE TRANSPORTES - {mes}/{ano}", styles['Title']))
    elements.append(Spacer(1, 0.5*cm))

    data = [['DATA', 'SETOR', 'RESPONSÁVEL', 'VEÍCULO', 'HORÁRIO', 'MOTIVO']]
    for s in solicitacoes:
        data.append([
            s.data_solicitada.strftime('%d/%m/%Y'),
            s.setor.nome_setor,
            s.responsavel,
            s.veiculo_solicitado,
            f"{s.horario_saida.strftime('%H:%M')} - {s.horario_chegada.strftime('%H:%M')}",
            Paragraph(s.motivo, styles['Normal'])
        ])

    t = Table(data, colWidths=[2.5*cm, 4*cm, 4*cm, 4*cm, 3.5*cm, 8*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(t)
    doc.build(elements)
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
    solicitacoes = SolicitacaoVeiculo.query.join(SetorTransporte).order_by(SolicitacaoVeiculo.data_solicitada.desc()).all()
    setores = SetorTransporte.query.order_by(SetorTransporte.nome_setor).all()
    return render_template('solicitacao/painel_usuario.html', solicitacoes=solicitacoes, setores=setores)

@solicitacao_bp.route('/painel', methods=['POST'])
def salvar_solicitacao():
    if 'setor_id' not in session:
        return redirect(url_for('solicitacao.login_setor'))
    
    data_str = request.form.get('data_solicitada')
    motivo = request.form.get('motivo')
    horario_saida_str = request.form.get('horario_saida')
    horario_chegada_str = request.form.get('horario_chegada')
    responsavel = request.form.get('responsavel')
    veiculo_escolhido = request.form.get('veiculo')
    
    if not data_str or not veiculo_escolhido:
        flash('Preencha a data e escolha um veículo!', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))

    data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()
    horario_saida_obj = datetime.strptime(horario_saida_str, '%H:%M').time()
    horario_chegada_obj = datetime.strptime(horario_chegada_str, '%H:%M').time()

    inicio_semana = data_obj - timedelta(days=data_obj.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    dias_existentes = [d[0] for d in db.session.query(SolicitacaoVeiculo.data_solicitada).filter(
        SolicitacaoVeiculo.setor_id == session['setor_id'],
        SolicitacaoVeiculo.data_solicitada >= inicio_semana,
        SolicitacaoVeiculo.data_solicitada <= fim_semana,
        SolicitacaoVeiculo.status != 'Reprovada'
    ).distinct().all()]

    if len(dias_existentes) >= 2 and data_obj not in dias_existentes:
        flash('Limite de 2 dias por semana atingido.', 'warning')
        return redirect(url_for('solicitacao.painel_usuario'))

    conflito = SolicitacaoVeiculo.query.filter(
        SolicitacaoVeiculo.data_solicitada == data_obj,
        SolicitacaoVeiculo.veiculo_solicitado == veiculo_escolhido,
        SolicitacaoVeiculo.status != 'Reprovada',
        SolicitacaoVeiculo.horario_saida < horario_chegada_obj,
        SolicitacaoVeiculo.horario_chegada > horario_saida_obj
    ).first()

    if conflito:
        flash(f'Veículo ocupado entre {conflito.horario_saida.strftime("%H:%M")} e {conflito.horario_chegada.strftime("%H:%M")}.', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))

    nova_sol = SolicitacaoVeiculo(
        setor_id=session['setor_id'], data_solicitada=data_obj, motivo=motivo,
        horario_saida=horario_saida_obj, horario_chegada=horario_chegada_obj,
        responsavel=responsavel, veiculo_solicitado=veiculo_escolhido, status='Pendente'
    )
    db.session.add(nova_sol)
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
    sol.justificativa = justificativa
    db.session.commit()
    flash('Solicitação reprovada com sucesso.', 'info')
    return redirect(url_for('solicitacao.painel_admin'))

@solicitacao_bp.route('/admin/relatorio-mensal', methods=['POST'])
@system_login_required
@transporte_admin_required
def relatorio_mensal():
    mes = request.form.get('mes')
    ano = datetime.now().year
    dados = SolicitacaoVeiculo.query.filter(
        SolicitacaoVeiculo.status == 'Aprovada',
        extract('month', SolicitacaoVeiculo.data_solicitada) == mes,
        extract('year', SolicitacaoVeiculo.data_solicitada) == ano
    ).order_by(SolicitacaoVeiculo.data_solicitada.asc()).all()
    
    if not dados:
        flash(f'Sem dados para o mês {mes}.', 'warning')
        return redirect(url_for('solicitacao.painel_admin'))
        
    return send_file(gerar_pdf_relatorio_consolidado(dados, mes, ano), mimetype='application/pdf', as_attachment=True, download_name=f'Relatorio_{mes}.pdf')

@solicitacao_bp.route('/admin/cadastrar-setor', methods=['GET', 'POST'])
@system_login_required
@transporte_admin_required
def cadastrar_setor():
    if request.method == 'POST':
        novo = SetorTransporte(nome_setor=request.form.get('nome_setor'), codigo_setor=request.form.get('codigo_setor'))
        db.session.add(novo)
        db.session.commit()
        flash('Setor cadastrado!', 'success')
        return redirect(url_for('solicitacao.cadastrar_setor'))
    setores = SetorTransporte.query.all()
    return render_template('solicitacao/cadastrar_setor.html', setores=setores)

@solicitacao_bp.route('/api/eventos')
def api_eventos():
    sols = SolicitacaoVeiculo.query.filter_by(status='Aprovada').all()
    eventos = [{'title': f'{s.setor.nome_setor}', 'start': s.data_solicitada.isoformat(), 'color': '#28a745'} for s in sols]
    return jsonify(eventos)

@solicitacao_bp.route('/exportar-agenda', methods=['POST'])
def exportar_agenda():
    if 'setor_id' not in session:
        return redirect(url_for('solicitacao.login_setor'))
    mes = request.form.get('mes_agenda')
    ano = datetime.now().year
    agendamentos = SolicitacaoVeiculo.query.filter(
        SolicitacaoVeiculo.status == 'Aprovada',
        extract('month', SolicitacaoVeiculo.data_solicitada) == mes,
        extract('year', SolicitacaoVeiculo.data_solicitada) == ano
    ).order_by(SolicitacaoVeiculo.data_solicitada.asc()).all()
    
    if not agendamentos:
        flash('Nenhum agendamento para exportar.', 'warning')
        return redirect(url_for('solicitacao.painel_usuario'))
        
    return send_file(gerar_pdf_relatorio_consolidado(agendamentos, mes, ano), mimetype='application/pdf', as_attachment=True, download_name=f'Agenda_{mes}.pdf')