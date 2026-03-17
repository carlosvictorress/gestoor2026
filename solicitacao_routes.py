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

from reportlab.lib.pagesizes import A4
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
    
    # Função interna para desenhar a marca d'água em cada página
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica-Bold', 80)
        canvas.setFillAlpha(0.1)  # Bem clarinho para não atrapalhar a leitura
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
    
    # 1. CABEÇALHO (TIMBRE)
    try:
        # Tenta carregar o timbre.png da pasta static
        img = Image("static/timbre.png", width=17*cm, height=2.5*cm)
        elements.append(img)
    except:
        elements.append(Paragraph("<b>PREFEITURA DE VALENÇA DO PIAUÍ</b>", styles['Title']))
    
    elements.append(Spacer(1, 0.8*cm))

    # 2. TÍTULO E IDENTIFICAÇÃO
    titulo_style = ParagraphStyle(
        'TituloDoc', parent=styles['Title'], fontSize=16, 
        textColor=colors.hexColor("#2c3e50"), spaceAfter=20
    )
    elements.append(Paragraph(f"AUTORIZAÇÃO DE TRANSPORTE Nº {solicitacao.id:04d}", titulo_style))

    # 3. TABELA DE DADOS TÉCNICOS
    # O uso de Paragraph dentro da tabela permite negrito e quebra de linha
    data = [
        [Paragraph("<b>SETOR SOLICITANTE:</b>", styles['Normal']), solicitacao.setor.nome_setor],
        [Paragraph("<b>RESPONSÁVEL:</b>", styles['Normal']), solicitacao.responsavel],
        [Paragraph("<b>VEÍCULO AUTORIZADO:</b>", styles['Normal']), f"<b>{solicitacao.veiculo_solicitado}</b>"],
        [Paragraph("<b>DATA DA VIAGEM:</b>", styles['Normal']), solicitacao.data_solicitada.strftime('%d/%m/%Y')],
        [Paragraph("<b>HORÁRIO:</b>", styles['Normal']), f"{solicitacao.horario_saida.strftime('%H:%M')} às {solicitacao.horario_chegada.strftime('%H:%M')}"],
        [Paragraph("<b>MOTIVO / DESTINO:</b>", styles['Normal']), solicitacao.motivo]
    ]

    t = Table(data, colWidths=[5*cm, 12*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.whitesmoke),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
    ]))
    elements.append(t)

    # 4. ASSINATURAS ALINHADAS
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

    # 5. RODAPÉ DE SEGURANÇA
    elements.append(Spacer(1, 2.5*cm))
    emissao = datetime.now().strftime("%d/%m/%Y às %H:%M")
    footer = Paragraph(
        f"<font color='grey' size='8'>Documento gerado pelo sistema <b>Gestor 360</b> em {emissao}.<br/>"
        f"A autenticidade deste documento pode ser verificada na secretaria municipal.</font>", 
        styles['Normal']
    )
    elements.append(footer)

    # CONSTRUÇÃO DO PDF (Com a marca d'água via onFirstPage e onLaterPages)
    doc.build(elements, onFirstPage=on_page, onLaterPages=on_page)
    
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
        flash('Código inválido!', 'danger')
    return render_template('solicitacao/login_setor.html')

@solicitacao_bp.route('/painel', methods=['GET'])
def painel_usuario():
    if 'setor_id' not in session:
        return redirect(url_for('solicitacao.login_setor'))
    
    # Busca todas as solicitações e todos os setores para popular a tabela e os filtros
    solicitacoes = SolicitacaoVeiculo.query.join(SetorTransporte).order_by(SolicitacaoVeiculo.data_solicitada.desc()).all()
    setores = SetorTransporte.query.order_by(SetorTransporte.nome_setor).all()
    
    return render_template('solicitacao/painel_usuario.html', solicitacoes=solicitacoes, setores=setores)

from datetime import datetime, timedelta # Certifique-se de importar o timedelta no topo do arquivo

@solicitacao_bp.route('/painel', methods=['POST'])
def salvar_solicitacao():
    if 'setor_id' not in session:
        return redirect(url_for('solicitacao.login_setor'))
    
    # Captura os dados do formulário
    data_str = request.form.get('data_solicitada')
    motivo = request.form.get('motivo')
    horario_saida_str = request.form.get('horario_saida')
    horario_chegada_str = request.form.get('horario_chegada')
    responsavel = request.form.get('responsavel')
    veiculo_escolhido = request.form.get('veiculo') # Captura o veículo selecionado
    
    if not data_str or not veiculo_escolhido:
        flash('Por favor, preencha a data e escolha um veículo!', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))

    # Converte as strings para objetos de data/hora
    data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()
    horario_saida_obj = datetime.strptime(horario_saida_str, '%H:%M').time()
    horario_chegada_obj = datetime.strptime(horario_chegada_str, '%H:%M').time()

    # --- REGRA 1: LIMITE DE 2 DIAS NA SEMANA POR SETOR ---
    # Descobre a segunda-feira e o domingo da semana solicitada
    inicio_semana = data_obj - timedelta(days=data_obj.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    
    # Busca todos os dias distintos já agendados por este setor nesta semana
    dias_existentes = [d[0] for d in db.session.query(SolicitacaoVeiculo.data_solicitada).filter(
        SolicitacaoVeiculo.setor_id == session['setor_id'],
        SolicitacaoVeiculo.data_solicitada >= inicio_semana,
        SolicitacaoVeiculo.data_solicitada <= fim_semana,
        SolicitacaoVeiculo.status != 'Reprovada' # Não conta as reprovadas
    ).distinct().all()]

    # Se já agendou em 2 dias, e a data solicitada for um "terceiro" dia diferente, bloqueia
    if len(dias_existentes) >= 2 and data_obj not in dias_existentes:
        flash('Limite atingido: Seu setor só pode agendar veículos em 2 dias distintos na semana.', 'warning')
        return redirect(url_for('solicitacao.painel_usuario'))

    # --- REGRA 2: VEÍCULO INDISPONÍVEL (CONFLITO DE HORÁRIO) ---
    # A lógica de sobreposição é: (NovaSaída < VelhaChegada) E (NovaChegada > VelhaSaída)
    conflito = SolicitacaoVeiculo.query.filter(
        SolicitacaoVeiculo.data_solicitada == data_obj,
        SolicitacaoVeiculo.veiculo_solicitado == veiculo_escolhido,
        SolicitacaoVeiculo.status != 'Reprovada', # Só avalia Pendentes e Aprovadas
        SolicitacaoVeiculo.horario_saida < horario_chegada_obj,
        SolicitacaoVeiculo.horario_chegada > horario_saida_obj
    ).first()

    if conflito:
        flash(f'Veículo {veiculo_escolhido} INDISPONÍVEL POIS ESTÁ EM USO nesse horário (Ocupado das {conflito.horario_saida.strftime("%H:%M")} às {conflito.horario_chegada.strftime("%H:%M")}).', 'danger')
        return redirect(url_for('solicitacao.painel_usuario'))

    # --- SE PASSOU NAS VALIDAÇÕES, SALVA NO BANCO ---
    nova_solicitacao = SolicitacaoVeiculo(
        setor_id=session['setor_id'],
        data_solicitada=data_obj,
        motivo=motivo,
        horario_saida=horario_saida_obj,
        horario_chegada=horario_chegada_obj,
        responsavel=responsavel,
        veiculo_solicitado=veiculo_escolhido, # Salva o veículo
        status='Pendente'
    )
    
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

@solicitacao_bp.route('/admin/relatorio-mensal', methods=['GET', 'POST'])
@system_login_required
@transporte_admin_required
def relatorio_mensal():
    if request.method == 'POST':
        mes = request.form.get('mes') # Formato '01', '02', etc.
        ano = datetime.now().year
        
        # Busca todas as solicitações aprovadas daquele mês/ano
        relatorio_dados = SolicitacaoVeiculo.query.filter(
            SolicitacaoVeiculo.status == 'Aprovada',
            extract('month', SolicitacaoVeiculo.data_solicitada) == mes,
            extract('year', SolicitacaoVeiculo.data_solicitada) == ano
        ).order_by(SolicitacaoVeiculo.data_solicitada.asc()).all()

        if not relatorio_dados:
            flash(f'Nenhum agendamento aprovado encontrado para o mês {mes}.', 'warning')
            return redirect(url_for('solicitacao.painel_admin'))

        return send_file(
            gerar_pdf_relatorio_consolidado(relatorio_dados, mes, ano),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'Relatorio_Transporte_{mes}_{ano}.pdf'
        )
    
    return render_template('solicitacao/filtro_relatorio.html')

from reportlab.lib.pagesizes import landscape

def gerar_pdf_relatorio_consolidado(solicitacoes, mes, ano):
    buffer = io.BytesIO()
    # Usamos LANDSCAPE (Paisagem) para relatórios com muitas colunas
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=landscape(A4),
        rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm
    )
    
    elements = []
    styles = getSampleStyleSheet()
    
    # Cabeçalho
    try:
        img = Image("static/timbre.png", width=20*cm, height=2.5*cm)
        elements.append(img)
    except:
        elements.append(Paragraph("PREFEITURA DE VALENÇA DO PIAUÍ", styles['Title']))
    
    elements.append(Spacer(1, 0.5*cm))
    elements.append(Paragraph(f"RELATÓRIO MENSAL DE TRANSPORTES - {mes}/{ano}", styles['Title']))
    elements.append(Spacer(1, 0.5*cm))

    # Tabela de Dados
    data = [['DATA', 'SETOR', 'RESPONSÁVEL', 'VEÍCULO', 'HORÁRIO', 'MOTIVO']]
    
    for s in solicitacoes:
        data.append([
            s.data_solicitada.strftime('%d/%m/%Y'),
            s.setor.nome_setor,
            s.responsavel,
            s.veiculo_solicitado,
            f"{s.horario_saida.strftime('%H:%M')} - {s.horario_chegada.strftime('%H:%M')}",
            Paragraph(s.motivo, styles['Normal']) # Paragraph permite quebra de linha na célula
        ])

    # Estilo da Tabela de Relatório
    t = Table(data, colWidths=[2.5*cm, 4*cm, 4*cm, 4*cm, 3.5*cm, 8*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.hexColor("#0d6efd")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    elements.append(t)
    
    # Rodapé
    elements.append(Spacer(1, 1*cm))
    footer = Paragraph(f"Relatório gerado pelo Gestor 360 em {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal'])
    elements.append(footer)

    doc.build(elements)
    buffer.seek(0)
    return buffer

