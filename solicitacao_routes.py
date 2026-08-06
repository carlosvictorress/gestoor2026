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
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable

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
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    caminho_timbre = os.path.join(base_dir, 'static', 'timbre.png')
    
    def on_page(canvas, doc):
        # Marca d'água elegante
        canvas.saveState()
        canvas.setFont('Helvetica-Bold', 55)
        canvas.setFillColor(colors.HexColor("#1e40af"))
        canvas.setFillAlpha(0.04)
        canvas.translate(A4[0]/2, A4[1]/2)
        canvas.rotate(45)
        canvas.drawCentredString(0, 0, "DOCUMENTO AUTORIZADO")
        canvas.restoreState()
        
        # Rodapé Institucional
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        data_emissao = datetime.now().strftime('%d/%m/%Y às %H:%M')
        rodape_texto = f"Gestor 360 • Prefeitura Municipal de Valença do Piauí | Emitido em {data_emissao} | Validação: AUT-{solicitacao.id:06d}"
        canvas.drawCentredString(A4[0]/2, 1.2*cm, rodape_texto)
        canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
        canvas.setLineWidth(0.5)
        canvas.line(1.5*cm, 1.6*cm, A4[0] - 1.5*cm, 1.6*cm)
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4, 
        leftMargin=1.5*cm, 
        rightMargin=1.5*cm, 
        topMargin=1.5*cm, 
        bottomMargin=2.2*cm
    )
    elements = []
    styles = getSampleStyleSheet()

    # Estilos customizados
    style_normal = ParagraphStyle('Norm', parent=styles['Normal'], fontName='Helvetica', fontSize=10, textColor=colors.HexColor("#334155"), leading=14)
    style_bold_label = ParagraphStyle('LabelBold', parent=style_normal, fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#1e3a8a"))
    style_title = ParagraphStyle('DocTitle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=15, textColor=colors.HexColor("#1e40af"), alignment=1, spaceAfter=4)
    style_subtitle = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=colors.HexColor("#475569"), alignment=1, spaceAfter=15)

    # 1. CABEÇALHO / TIMBRE
    if os.path.exists(caminho_timbre):
        try:
            img = Image(caminho_timbre, width=17.5*cm, height=2.4*cm)
            elements.append(img)
            elements.append(Spacer(1, 0.3*cm))
        except Exception:
            elements.append(Paragraph("<b>PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ</b>", styles['Title']))
            elements.append(Spacer(1, 0.4*cm))
    else:
        elements.append(Paragraph("<b>PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ</b>", styles['Title']))
        elements.append(Spacer(1, 0.4*cm))

    # Linha divisória com acento de cor
    elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1e40af"), spaceAfter=12))

    # TÍTULO PRINCIPAL & PROTOCOLO
    elements.append(Paragraph("ORDEM DE TRÁFEGO E AUTORIZAÇÃO DE VEÍCULO", style_title))
    elements.append(Paragraph(f"AUTORIZAÇÃO Nº <b>#{solicitacao.id:06d}</b> • STATUS: <font color='#047857'><b>APROVADO</b></font>", style_subtitle))

    # 2. BLOCO DE INFORMAÇÕES DA VIAGEM (TABELA PRINCIPAL ESTILIZADA)
    data_viagem = solicitacao.data_solicitada.strftime('%d/%m/%Y')
    horario_formatado = f"{solicitacao.horario_saida.strftime('%H:%M')} às {solicitacao.horario_chegada.strftime('%H:%M')}"

    table_data = [
        [
            Paragraph("<b>ÓRGÃO / SETOR SOLICITANTE:</b>", style_bold_label),
            Paragraph(f"<b>{solicitacao.setor.nome_setor.upper()}</b>", style_normal)
        ],
        [
            Paragraph("<b>SERVIDOR RESPONSÁVEL:</b>", style_bold_label),
            Paragraph(solicitacao.responsavel, style_normal)
        ],
        [
            Paragraph("<b>VEÍCULO AUTORIZADO:</b>", style_bold_label),
            Paragraph(f"<font color='#1e40af'><b>{solicitacao.veiculo_solicitado.upper()}</b></font>", style_normal)
        ],
        [
            Paragraph("<b>DATA DA VIAGEM:</b>", style_bold_label),
            Paragraph(f"<b>{data_viagem}</b>", style_normal)
        ],
        [
            Paragraph("<b>HORÁRIO DE SAÍDA / RETORNO:</b>", style_bold_label),
            Paragraph(horario_formatado, style_normal)
        ],
        [
            Paragraph("<b>FINALIDADE / ROTEIRO:</b>", style_bold_label),
            Paragraph(solicitacao.motivo, style_normal)
        ]
    ]

    t_info = Table(table_data, colWidths=[5.5*cm, 12*cm])
    t_info.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ('BACKGROUND', (1, 0), (1, -1), colors.HexColor("#ffffff")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(t_info)

    elements.append(Spacer(1, 0.6*cm))

    # 3. CAIXA DE INSTRUÇÕES DE SEGURANÇA E TRÂNSITO
    instrucoes_html = """
    <b>INSTRUÇÕES E NORMAS DE USO DO VEÍCULO:</b><br/>
    • Esta autorização é de porte obrigatório e deve permanecer no veículo durante todo o percurso.<br/>
    • O condutor designado deve possuir CNH compatível com a categoria do veículo e respeitar as leis de trânsito.<br/>
    • O uso do veículo é estritamente institucional, sendo vedado o transporte de terceiros não autorizados.
    """
    p_instrucoes = Paragraph(instrucoes_html, ParagraphStyle('Inst', parent=style_normal, fontSize=8.5, leading=12, textColor=colors.HexColor("#475569")))
    
    t_box = Table([[p_instrucoes]], colWidths=[17.5*cm])
    t_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e1")),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(t_box)

    elements.append(Spacer(1, 1.8*cm))

    # 4. BLOCO DE ASSINATURAS E CAMPO CIDADE/DATA
    meses_pt = {
        1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril',
        5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto',
        9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro'
    }
    hoje = datetime.now()
    data_extenso = f"Valença do Piauí - PI, {hoje.day} de {meses_pt[hoje.month]} de {hoje.year}."

    elements.append(Paragraph(f"<b>{data_extenso}</b>", ParagraphStyle('DateLine', parent=style_normal, alignment=1, fontSize=9.5, spaceAfter=25)))

    assinatura_data = [
        [
            Paragraph("__________________________________________<br/><b>" + solicitacao.responsavel + "</b><br/><font size=8 color='#64748b'>Servidor Responsável / Solicitante</font>", ParagraphStyle('Ass1', parent=style_normal, alignment=1)),
            Paragraph("__________________________________________<br/><b>SECRETARIA MUNICIPAL DE TRANSPORTES</b><br/><font size=8 color='#64748b'>Visto da Administração / Chefia da Frota</font>", ParagraphStyle('Ass2', parent=style_normal, alignment=1))
        ]
    ]
    t_ass = Table(assinatura_data, colWidths=[8.75*cm, 8.75*cm])
    t_ass.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER')
    ]))
    elements.append(t_ass)

    doc.build(elements, onFirstPage=on_page, onLaterPages=on_page)
    buffer.seek(0)
    return buffer

def gerar_pdf_relatorio_consolidado(solicitacoes, mes, ano):
    buffer = io.BytesIO()
    caminho_timbre = os.path.join(current_app.root_path, 'static', 'timbre.png')
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), margin=1*cm)
    elements = []
    styles = getSampleStyleSheet()
    
    style_header = ParagraphStyle('RepHeader', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=14, textColor=colors.HexColor("#1e40af"), alignment=1, spaceAfter=10)
    
    if os.path.exists(caminho_timbre):
        try:
            img = Image(caminho_timbre, width=22*cm, height=2.5*cm)
            elements.append(img)
            elements.append(Spacer(1, 0.3*cm))
        except Exception:
            elements.append(Paragraph("<b>PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ</b>", styles['Title']))
    else:
        elements.append(Paragraph("<b>PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ</b>", styles['Title']))
    
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1e40af"), spaceAfter=10))
    elements.append(Paragraph(f"RELATÓRIO DE CONSOLIDADO DE VIAGENS E TRANSPORTES - {mes}/{ano}", style_header))
    elements.append(Spacer(1, 0.4*cm))

    data = [['DATA', 'SETOR / DEPARTAMENTO', 'RESPONSÁVEL', 'VEÍCULO', 'JANELA HORÁRIO', 'FINALIDADE / MOTIVO']]
    for s in solicitacoes:
        data.append([
            s.data_solicitada.strftime('%d/%m/%Y'),
            s.setor.nome_setor,
            s.responsavel,
            s.veiculo_solicitado,
            f"{s.horario_saida.strftime('%H:%M')} às {s.horario_chegada.strftime('%H:%M')}",
            Paragraph(s.motivo, styles['Normal'])
        ])

    t = Table(data, colWidths=[2.5*cm, 5*cm, 4.5*cm, 4*cm, 3.5*cm, 8.2*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1e40af")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
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
    
    setor_id = session['setor_id']
    solicitacoes = SolicitacaoVeiculo.query.join(SetorTransporte).order_by(SolicitacaoVeiculo.data_solicitada.desc()).all()
    setores = SetorTransporte.query.order_by(SetorTransporte.nome_setor).all()
    
    # Métricas e Indicadores do Setor
    minhas_sols = [s for s in solicitacoes if s.setor_id == setor_id]
    stats = {
        'total': len(minhas_sols),
        'aprovadas': sum(1 for s in minhas_sols if s.status == 'Aprovada'),
        'pendentes': sum(1 for s in minhas_sols if s.status == 'Pendente'),
        'canceladas': sum(1 for s in minhas_sols if s.status in ['Cancelada', 'Reprovada'])
    }
    
    return render_template('solicitacao/painel_usuario.html', solicitacoes=solicitacoes, setores=setores, stats=stats)

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
        SolicitacaoVeiculo.status.notin_(['Reprovada', 'Cancelada'])
    ).distinct().all()]

    if len(dias_existentes) >= 2 and data_obj not in dias_existentes:
        flash('Limite de 2 dias por semana atingido.', 'warning')
        return redirect(url_for('solicitacao.painel_usuario'))

    conflito = SolicitacaoVeiculo.query.filter(
        SolicitacaoVeiculo.data_solicitada == data_obj,
        SolicitacaoVeiculo.veiculo_solicitado == veiculo_escolhido,
        SolicitacaoVeiculo.status.notin_(['Reprovada', 'Cancelada']),
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

@solicitacao_bp.route('/cancelar/<int:id>', methods=['POST'])
def cancelar_solicitacao(id):
    if 'setor_id' not in session:
        return redirect(url_for('solicitacao.login_setor'))
    
    sol = SolicitacaoVeiculo.query.get_or_404(id)
    if sol.setor_id != session['setor_id']:
        flash('Você não tem permissão para cancelar esta solicitação.', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))
        
    if sol.status != 'Pendente':
        flash('Apenas solicitações em análise podem ser canceladas.', 'warning')
        return redirect(url_for('solicitacao.painel_usuario'))
        
    justificativa = request.form.get('justificativa', '').strip()
    if not justificativa:
        flash('A justificativa de cancelamento é obrigatória!', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))
        
    sol.status = 'Cancelada'
    sol.justificativa = f"Cancelado pelo solicitante: {justificativa}"
    db.session.commit()
    flash('Solicitação cancelada com sucesso.', 'info')
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

@solicitacao_bp.route('/reimprimir/<int:id>')
def reimprimir_comprovante(id):
    # Busca a solicitação
    sol = SolicitacaoVeiculo.query.get_or_404(id)
    
    if sol.status != 'Aprovada':
        flash('Apenas solicitações aprovadas podem gerar comprovante.', 'warning')
        return redirect(request.referrer or url_for('solicitacao.painel_usuario'))
    
    # Gera e envia o PDF usando a função robusta que criamos
    pdf_buffer = gerar_pdf_autorizacao(sol)
    return send_file(
        pdf_buffer, 
        mimetype='application/pdf', 
        as_attachment=True, 
        download_name=f'Comprovante_{sol.id}.pdf'
    )