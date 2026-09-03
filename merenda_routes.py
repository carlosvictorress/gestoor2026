import os
import base64
import io
import json
import uuid
import calendar
from datetime import datetime, date, timedelta
from functools import wraps

# 2. Bibliotecas de Terceiros (Flask/SQLAlchemy/ReportLab)
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, make_response, jsonify
from sqlalchemy import or_, func, extract
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A4, landscape
from werkzeug.utils import secure_filename

# 3. Módulos Internos
from extensions import db, bcrypt
from models import (
    Escola, ProdutoMerenda, EstoqueMovimento, SolicitacaoMerenda, 
    SolicitacaoItem, Cardapio, CardapioItemDiario, PratoDiario, HistoricoCardapio, 
    Servidor, RelatorioTecnico, RelatorioAnexo, PedidoEmpresa, 
    PedidoEmpresaItem, FichaDistribuicao, FichaDistribuicaoItem,
    AgricultorFamiliar, DocumentoAgricultor, ContratoPNAE, 
    ItemProjetoVenda, EntregaPNAE, ConfiguracaoPNAE,
    CardapioNutricionista
)
from utils import (
    login_required, registrar_log, limpar_cpf, cabecalho_e_rodape, 
    currency_filter_br, cabecalho_e_rodape_moderno, 
    upload_arquivo_para_nuvem, role_required
)
merenda_bp = Blueprint('merenda', __name__, url_prefix='/merenda')

# --- ROTAS PRINCIPAIS A SEREM DESENVOLVIDAS ---

# Rota principal do módulo
@merenda_bp.route('/dashboard')
@login_required
@role_required("RH", "admin", "Merenda Escolar")
def dashboard():
    # --- KPIs BÁSICOS ---
    total_escolas_ativas = Escola.query.filter_by(status='Ativa').count()
    escolas_todas = Escola.query.order_by(Escola.nome.asc()).all()
    total_produtos = ProdutoMerenda.query.filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None))
    ).count()
    solicitacoes_pendentes = SolicitacaoMerenda.query.filter_by(status='Pendente').count()
    
    # --- LÓGICA DE ALERTA DE VALIDADE (30 DIAS) ---
    hoje = date.today()
    data_limite_alerta = hoje + timedelta(days=30) 

    alertas_validade = db.session.query(
        ProdutoMerenda.nome,
        EstoqueMovimento.lote,
        EstoqueMovimento.data_validade,
        ProdutoMerenda.unidade_consumo,
        EstoqueMovimento.quantidade
    ).join(ProdutoMerenda)\
     .filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None)),
        EstoqueMovimento.tipo == 'Entrada',
        EstoqueMovimento.data_validade.isnot(None),
        EstoqueMovimento.data_validade <= data_limite_alerta,
        EstoqueMovimento.data_validade >= hoje,
        ProdutoMerenda.estoque_atual > 0 
     ).order_by(EstoqueMovimento.data_validade.asc()).all()

    # --- ESTOQUE BAIXO ---
    produtos_estoque_baixo = ProdutoMerenda.query.filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None)),
        ProdutoMerenda.estoque_atual <= ProdutoMerenda.estoque_minimo, 
        ProdutoMerenda.estoque_atual > 0
    ).order_by(ProdutoMerenda.estoque_atual.asc()).all()

    produtos_disponiveis = ProdutoMerenda.query.filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None))
    ).order_by(ProdutoMerenda.nome.asc()).all()

    pedidos_empresa = PedidoEmpresa.query.order_by(PedidoEmpresa.data_pedido.desc()).limit(10).all()

    # --- NOVO: CÁLCULO REAL DOS GRÁFICOS DO DASHBOARD ---
    top_escolas = db.session.query(
        Escola.nome,
        func.sum(EstoqueMovimento.quantidade).label('total')
    ).join(EstoqueMovimento, EstoqueMovimento.escola_id == Escola.id)\
     .filter(EstoqueMovimento.tipo == 'Saída Escola')\
     .group_by(Escola.id, Escola.nome)\
     .order_by(func.sum(EstoqueMovimento.quantidade).desc())\
     .limit(5).all()

    escolas_labels = [e.nome[:25] for e in top_escolas] if top_escolas else ["Sem registros"]
    escolas_data = [float(e.total or 0) for e in top_escolas] if top_escolas else [0]

    top_produtos = db.session.query(
        ProdutoMerenda.nome,
        func.sum(EstoqueMovimento.quantidade).label('total')
    ).join(EstoqueMovimento, EstoqueMovimento.produto_id == ProdutoMerenda.id)\
     .filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None)),
        EstoqueMovimento.tipo == 'Saída Escola'
     ).group_by(ProdutoMerenda.id, ProdutoMerenda.nome)\
     .order_by(func.sum(EstoqueMovimento.quantidade).desc())\
     .limit(5).all()

    produtos_labels = [p.nome[:20] for p in top_produtos] if top_produtos else ["Sem baixas"]
    produtos_data = [float(p.total or 0) for p in top_produtos] if top_produtos else [0]

    return render_template('merenda/dashboard.html',
                           total_escolas_ativas=total_escolas_ativas,
                           escolas_todas=escolas_todas,
                           total_produtos=total_produtos,
                           solicitacoes_pendentes=solicitacoes_pendentes,
                           alertas_validade=alertas_validade,
                           produtos_estoque_baixo=produtos_estoque_baixo,
                           produtos_disponiveis=produtos_disponiveis,
                           pedidos_empresa=pedidos_empresa,
                           hoje=hoje,
                           escolas_labels=escolas_labels,
                           escolas_data=escolas_data,
                           produtos_labels=produtos_labels,
                           produtos_data=produtos_data)

# Rotas para Gerenciamento de Escolas
@merenda_bp.route('/escolas')
@login_required
@role_required('Merenda Escolar', 'admin')
def listar_escolas():
    escolas = Escola.query.order_by(Escola.nome).all()
    return render_template('merenda/escolas_lista.html', escolas=escolas)

@merenda_bp.route('/escolas/nova', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def nova_escola():
    if request.method == 'POST':
        nome_escola = request.form.get('nome')
        # Verifica se já existe uma escola com o mesmo nome
        if Escola.query.filter_by(nome=nome_escola).first():
            flash('Já existe uma escola cadastrada com este nome.', 'danger')
            return redirect(url_for('merenda.nova_escola'))
        
        try:
            nova = Escola(
            nome=nome_escola,
            endereco=request.form.get('endereco'),
            telefone=request.form.get('telefone'),
            status=request.form.get('status'),
            zona=request.form.get('zona'), # Adicione esta linha
            diretor_cpf=request.form.get('diretor_cpf') or None,
            responsavel_merenda_cpf=request.form.get('responsavel_merenda_cpf') or None
            )
            
            db.session.add(nova)
            db.session.commit()
            registrar_log(f'Cadastrou a escola: "{nova.nome}".')
            flash('Escola cadastrada com sucesso!', 'success')
            return redirect(url_for('merenda.listar_escolas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar escola: {e}', 'danger')

    servidores = Servidor.query.order_by(Servidor.nome).all()
    return render_template('merenda/escolas_form.html', escola=None, servidores=servidores)

@merenda_bp.route('/escolas/editar/<int:escola_id>', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def editar_escola(escola_id):
    escola = Escola.query.get_or_404(escola_id)
    if request.method == 'POST':
        try:
            escola.nome = request.form.get('nome')
            escola.endereco = request.form.get('endereco')
            escola.telefone = request.form.get('telefone')
            escola.status = request.form.get('status')
            escola.zona = request.form.get('zona')
            escola.diretor_cpf = request.form.get('diretor_cpf') or None
            escola.responsavel_merenda_cpf = request.form.get('responsavel_merenda_cpf') or None

            db.session.commit()
            registrar_log(f'Editou os dados da escola: "{escola.nome}".')
            flash('Dados da escola atualizados com sucesso!', 'success')
            return redirect(url_for('merenda.listar_escolas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao editar a escola: {e}', 'danger')

    servidores = Servidor.query.order_by(Servidor.nome).all()
    return render_template('merenda/escolas_form.html', escola=escola, servidores=servidores)
# GET /escolas -> Listar todas as escolas
# GET /escolas/nova -> Formulário de nova escola
# POST /escolas/nova -> Salvar nova escola
# GET /escolas/editar/<id> -> Formulário de edição
# POST /escolas/editar/<id> -> Salvar edição

# Rotas para Gerenciamento de Produtos
@merenda_bp.route('/produtos')
@login_required
@role_required('Merenda Escolar', 'admin')
def listar_produtos():
    # FILTRO ADICIONADO: Busca apenas produtos que NÃO sejam da Agricultura Familiar
    produtos = ProdutoMerenda.query.filter(
        ProdutoMerenda.categoria != 'Agricultura Familiar'
    ).order_by(ProdutoMerenda.nome).all()
    
    return render_template('merenda/produtos_lista.html', produtos=produtos)

@merenda_bp.route('/produtos/novo', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def novo_produto():
    if request.method == 'POST':
        try:
            # Tratamento de valores numéricos (troca vírgula por ponto)
            def flt(val): 
                if not val: return 0.0
                return float(str(val).replace(',', '.'))
            
            # Captura o fator de conversão do formulário
            # Se estiver vazio ou não existir, o padrão é 1.0
            fator_raw = request.form.get('fator_conversao')
            fator_final = flt(fator_raw) if fator_raw and flt(fator_raw) > 0 else 1.0

            novo = ProdutoMerenda(
                nome=request.form.get('nome'),
                unidade_medida=request.form.get('unidade_medida', 'CX'),
                unidade_consumo=request.form.get('unidade_consumo', 'UNID'),
                categoria=request.form.get('categoria'),
                
                # Fator de Conversão (Ex: 1 fardo = 30 pacotes)
                fator_conversao=fator_final,
                
                # Campos Profissionais
                estoque_minimo=flt(request.form.get('estoque_minimo')),
                tipo_armazenamento=request.form.get('tipo_armazenamento'),
                perecivel=True if request.form.get('perecivel') else False,
                
                # Nutricional
                calorias=flt(request.form.get('calorias')),
                proteinas=flt(request.form.get('proteinas')),
                carboidratos=flt(request.form.get('carboidratos')),
                lipidios=flt(request.form.get('lipidios'))
            )
            
            db.session.add(novo)
            db.session.commit()
            flash('Produto cadastrado com sucesso!', 'success')
            return redirect(url_for('merenda.listar_produtos'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar: {e}', 'danger')

    return render_template('merenda/produtos_form.html', produto=None)

@merenda_bp.route('/produtos/editar/<int:produto_id>', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def editar_produto(produto_id):
    produto = ProdutoMerenda.query.get_or_404(produto_id)
    if request.method == 'POST':
        try:
            # Tratamento de valores numéricos (troca vírgula por ponto)
            def flt(val): 
                if not val: return 0.0
                return float(str(val).replace(',', '.'))
            
            # Captura e atualiza o fator de conversão
            fator_raw = request.form.get('fator_conversao')
            produto.fator_conversao = flt(fator_raw) if fator_raw and flt(fator_raw) > 0 else 1.0

            produto.nome = request.form.get('nome')
            produto.unidade_medida = request.form.get('unidade_medida', 'CX')
            produto.unidade_consumo = request.form.get('unidade_consumo', 'UNID')
            produto.categoria = request.form.get('categoria')
            
            # Atualização dos demais campos
            produto.estoque_minimo = flt(request.form.get('estoque_minimo'))
            produto.tipo_armazenamento = request.form.get('tipo_armazenamento')
            produto.perecivel = True if request.form.get('perecivel') else False
            
            # Dados nutricionais
            produto.calorias = flt(request.form.get('calorias'))
            produto.proteinas = flt(request.form.get('proteinas'))
            produto.carboidratos = flt(request.form.get('carboidratos'))
            produto.lipidios = flt(request.form.get('lipidios'))

            db.session.commit()
            flash('Produto atualizado com sucesso!', 'success')
            return redirect(url_for('merenda.listar_produtos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao editar: {e}', 'danger')

    return render_template('merenda/produtos_form.html', produto=produto)
# GET /produtos -> Listar todos os produtos e estoque atual
# GET /produtos/novo -> Formulário de novo produto
# POST /produtos/novo -> Salvar novo produto

# ==========================================================
# GESTÃO INTEGRADA E AUDITÁVEL DE ESTOQUE (MERENDA ESCOLAR)
# ==========================================================

@merenda_bp.route('/estoque', methods=['GET'])
@login_required
@role_required('Merenda Escolar', 'admin')
def gerenciar_estoque():
    # REGRA CRÍTICA: Filtrar estritamente apenas produtos de Merenda (excluindo Agricultura Familiar)
    produtos = ProdutoMerenda.query.filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None))
    ).order_by(ProdutoMerenda.nome).all()

    escolas = Escola.query.order_by(Escola.nome).all()

    # Estatísticas Rápidas
    total_produtos = len(produtos)
    produtos_criticos = [p for p in produtos if (p.estoque_atual or 0) <= (p.estoque_minimo or 10)]
    
    # Movimentações recentes da Merenda Escolar (ordenadas por ID para exibir lançamentos recentes no topo)
    historico_recentes = EstoqueMovimento.query.outerjoin(ProdutoMerenda).filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None))
    ).order_by(EstoqueMovimento.id.desc()).limit(100).all()

    from datetime import datetime
    data_hoje = datetime.now().strftime('%Y-%m-%d')

    return render_template(
        'merenda/estoque_gerenciar.html',
        produtos=produtos,
        escolas=escolas,
        total_produtos=total_produtos,
        produtos_criticos=produtos_criticos,
        historico=historico_recentes,
        data_hoje=data_hoje
    )


@merenda_bp.route('/estoque/entradas', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def entrada_estoque():
    if request.method == 'POST':
        try:
            produto_id = request.form.get('produto_id', type=int)
            tipo_unidade = request.form.get('tipo_entrada', 'master')  # 'master' (Fardo/Caixa) ou 'base' (Unidade/Kg)
            
            quantidade_str = request.form.get('quantidade', '0').replace(',', '.')
            quantidade_digitada = float(quantidade_str)

            if not produto_id or quantidade_digitada <= 0:
                flash('Selecione o produto e informe uma quantidade válida maior que zero.', 'danger')
                return redirect(url_for('merenda.gerenciar_estoque'))

            produto = ProdutoMerenda.query.get(produto_id)
            if not produto:
                flash('Produto não encontrado.', 'danger')
                return redirect(url_for('merenda.gerenciar_estoque'))

            fator = float(produto.fator_conversao) if (produto.fator_conversao and produto.fator_conversao > 0) else 1.0

            if tipo_unidade == 'master' and fator > 1.0:
                quantidade_para_estoque = quantidade_digitada * fator
                unidade_movimento = produto.unidade_medida or 'CX'
                msg_detalhe = f"{quantidade_digitada:.2f} {unidade_movimento} ({quantidade_para_estoque:.2f} {produto.unidade_consumo or 'UNID'})"
            else:
                quantidade_para_estoque = quantidade_digitada
                unidade_movimento = produto.unidade_consumo or 'UNID'
                msg_detalhe = f"{quantidade_digitada:.2f} {unidade_movimento}"

            produto.estoque_atual = (produto.estoque_atual or 0.0) + quantidade_para_estoque

            data_validade_str = request.form.get('data_validade')
            data_validade = datetime.strptime(data_validade_str, '%Y-%m-%d').date() if data_validade_str else None

            movimento = EstoqueMovimento(
                produto_id=produto_id,
                tipo='Entrada',
                quantidade=quantidade_para_estoque,
                unidade_movimento=unidade_movimento,
                quantidade_embalagem=quantidade_digitada,
                fator_utilizado=fator,
                fornecedor=request.form.get('fornecedor'),
                lote=request.form.get('lote'),
                data_validade=data_validade,
                observacao=request.form.get('observacao'),
                usuario_responsavel=session.get('username', 'Sistema')
            )
            
            db.session.add(movimento)
            db.session.commit()
            
            registrar_log(f'Entrada no estoque da Merenda: {msg_detalhe} do produto "{produto.nome}".')
            flash(f'Sucesso! Entrada de {msg_detalhe} registrada para "{produto.nome}".', 'success')
            return redirect(url_for('merenda.gerenciar_estoque'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao registrar entrada de estoque: {e}', 'danger')
            return redirect(url_for('merenda.gerenciar_estoque'))
    
    return redirect(url_for('merenda.gerenciar_estoque'))


# --- GERADOR EXECUTIVO DE PDF DE ENTREGA E GUIAS PNAE ---
def gerar_pdf_termo_entrega_profissional(titulo, subtitulo, escola_nome, escola_obj, data_emissao, responsavel, itens_tabela, observacao_geral=None, num_protocolo=None):
    from utils import cabecalho_e_rodape_moderno, obter_data_hora_br_str
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from flask import make_response
    import io

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4, 
        rightMargin=1.5*cm, 
        leftMargin=1.5*cm, 
        topMargin=2.8*cm, 
        bottomMargin=2.0*cm
    )
    styles = getSampleStyleSheet()

    style_titulo = ParagraphStyle(
        'DocTitulo', 
        parent=styles['Heading1'], 
        fontName='Helvetica-Bold', 
        fontSize=12, 
        leading=14, 
        alignment=TA_CENTER, 
        textColor=colors.HexColor('#004d40')
    )
    style_subtitulo = ParagraphStyle(
        'DocSubTitulo', 
        parent=styles['Normal'], 
        fontName='Helvetica-Bold', 
        fontSize=8.5, 
        leading=11, 
        alignment=TA_CENTER, 
        textColor=colors.HexColor('#475569')
    )
    style_card_val = ParagraphStyle(
        'CardVal', 
        fontName='Helvetica', 
        fontSize=8, 
        leading=10, 
        textColor=colors.HexColor('#1e293b')
    )
    style_th = ParagraphStyle(
        'TableTH', 
        fontName='Helvetica-Bold', 
        fontSize=8, 
        leading=10, 
        alignment=TA_CENTER, 
        textColor=colors.whitesmoke
    )
    style_td_center = ParagraphStyle(
        'TableTDCenter', 
        fontName='Helvetica', 
        fontSize=8, 
        leading=10, 
        alignment=TA_CENTER, 
        textColor=colors.HexColor('#1e293b')
    )
    style_td_bold = ParagraphStyle(
        'TableTDBold', 
        fontName='Helvetica-Bold', 
        fontSize=8, 
        leading=10, 
        textColor=colors.HexColor('#0f172a')
    )
    style_declaracao = ParagraphStyle(
        'DeclaracaoText', 
        fontName='Helvetica-Oblique', 
        fontSize=8, 
        leading=11, 
        alignment=TA_JUSTIFY, 
        textColor=colors.HexColor('#334155')
    )
    style_ass_titulo = ParagraphStyle(
        'AssTitulo', 
        fontName='Helvetica-Bold', 
        fontSize=8, 
        leading=10, 
        alignment=TA_CENTER, 
        textColor=colors.HexColor('#0f172a')
    )
    style_ass_sub = ParagraphStyle(
        'AssSub', 
        fontName='Helvetica', 
        fontSize=7.5, 
        leading=9, 
        alignment=TA_CENTER, 
        textColor=colors.HexColor('#64748b')
    )

    story = []

    # 1. Título Oficial do Documento
    story.append(Paragraph(titulo.upper(), style_titulo))
    story.append(Paragraph(subtitulo, style_subtitulo))
    story.append(Spacer(1, 0.3*cm))

    # 2. Quadro Informativo de Destino & Auditoria
    data_str = data_emissao.strftime('%d/%m/%Y %H:%M') if isinstance(data_emissao, datetime) else (data_emissao.strftime('%d/%m/%Y') if hasattr(data_emissao, 'strftime') else str(data_emissao))
    protocolo_str = num_protocolo or "REC-" + datetime.now().strftime('%Y%m%d%H%M%S')
    
    inep_str = getattr(escola_obj, 'codigo_inep', None) or '-'
    endereco_str = getattr(escola_obj, 'endereco', None) or '-'

    card_data = [
        [
            Paragraph(f"<b>Unidade Escolar:</b> {escola_nome}", style_card_val),
            Paragraph(f"<b>Nº Guia/Protocolo:</b> {protocolo_str}", style_card_val)
        ],
        [
            Paragraph(f"<b>Código INEP:</b> {inep_str}", style_card_val),
            Paragraph(f"<b>Data/Hora Expedição:</b> {data_str}", style_card_val)
        ],
        [
            Paragraph(f"<b>Endereço:</b> {endereco_str}", style_card_val),
            Paragraph(f"<b>Expedido por:</b> {responsavel or 'Sistema'}", style_card_val)
        ]
    ]

    if observacao_geral:
        card_data.append([
            Paragraph(f"<b>Observações/Justificativa:</b> {observacao_geral}", style_card_val),
            Paragraph(f"<b>Programa:</b> PNAE / Alimentação Escolar", style_card_val)
        ])

    card_table = Table(card_data, colWidths=[10.5*cm, 7.5*cm])
    card_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(card_table)
    story.append(Spacer(1, 0.4*cm))

    # 3. Tabela Principal de Produtos
    table_rows = [
        [
            Paragraph("Item", style_th),
            Paragraph("Gênero Alimentício / Produto", style_th),
            Paragraph("Qtd. Lançada (Embalagem)", style_th),
            Paragraph("Baixa Real (Estoque)", style_th),
            Paragraph("Status/Lote", style_th)
        ]
    ]

    total_itens = len(itens_tabela)
    for idx, row in enumerate(itens_tabela, 1):
        p_nome = row.get('produto', '')
        q_emb = row.get('qtd_emb', '--')
        q_real = row.get('qtd_real', '--')
        obs = row.get('obs', 'Conforme pedido')

        table_rows.append([
            Paragraph(str(idx), style_td_center),
            Paragraph(p_nome, style_td_bold),
            Paragraph(q_emb, style_td_center),
            Paragraph(q_real, style_td_center),
            Paragraph(obs, style_td_center)
        ])

    table_rows.append([
        Paragraph("TOTAL", style_td_bold),
        Paragraph(f"<b>{total_itens} item(ns) discriminado(s)</b>", style_td_bold),
        Paragraph("--", style_td_center),
        Paragraph("--", style_td_center),
        Paragraph("OK", style_td_center)
    ])

    prod_table = Table(table_rows, colWidths=[1.2*cm, 7.5*cm, 3.8*cm, 3.5*cm, 2.0*cm])
    prod_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f1f5f9')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8fafc')])
    ]))
    story.append(prod_table)
    story.append(Spacer(1, 0.4*cm))

    # 4. Termo de Fiel Recebimento / Declaração Oficial
    decl_text = (
        "Atestamos que os gêneros alimentícios acima discriminados foram devidamente entregues "
        "e recebidos nesta data, encontrando-se em perfeitas condições de higiene, acondicionamento e "
        "prazo de validade, destinados exclusivamente ao atendimento dos alunos da Unidade Escolar (PNAE/FNDE)."
    )
    decl_table = Table([[Paragraph(decl_text, style_declaracao)]], colWidths=[18.0*cm])
    decl_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f1f5f9')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(decl_table)
    story.append(Spacer(1, 0.4*cm))

    # 4.5 Bloco de Observações e Justificativas da Expedição (no final do PDF)
    if observacao_geral:
        obs_title_style = ParagraphStyle(
            'ObsTitle',
            fontName='Helvetica-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor('#92400e')
        )
        obs_text_style = ParagraphStyle(
            'ObsText',
            fontName='Helvetica',
            fontSize=8,
            leading=10.5,
            textColor=colors.HexColor('#1e293b')
        )
        obs_content = [
            Paragraph("<b>OBSERVAÇÕES E JUSTIFICATIVA DA EXPEDIÇÃO:</b>", obs_title_style),
            Spacer(1, 0.1*cm),
            Paragraph(observacao_geral, obs_text_style)
        ]
        obs_table = Table([[obs_content]], colWidths=[18.0*cm])
        obs_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fef3c7')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#fde68a')),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(obs_table)
        story.append(Spacer(1, 0.5*cm))
    else:
        story.append(Spacer(1, 0.8*cm))

    # 5. Quadro de Assinaturas Duplas
    ass_data = [
        [
            Paragraph("____________________________________________", style_ass_titulo),
            Paragraph("____________________________________________", style_ass_titulo)
        ],
        [
            Paragraph("<b>Responsável pela Expedição</b>", style_ass_titulo),
            Paragraph("<b>Responsável pelo Recebimento na Escola</b>", style_ass_titulo)
        ],
        [
            Paragraph(f"Almoxarifado Central da Merenda<br/><font size=7 color='#64748b'>{responsavel or 'Sistema'}</font>", style_ass_sub),
            Paragraph("Nome: ____________________________________<br/><font size=7 color='#64748b'>Cargo / Carimbo / CPF</font>", style_ass_sub)
        ]
    ]
    ass_table = Table(ass_data, colWidths=[9.0*cm, 9.0*cm])
    ass_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(ass_table)

    doc.build(
        story, 
        onFirstPage=lambda canvas, doc: cabecalho_e_rodape_moderno(canvas, doc, "Guia de Entrega"), 
        onLaterPages=lambda canvas, doc: cabecalho_e_rodape_moderno(canvas, doc, "Guia de Entrega")
    )

    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Termo_Entrega_{protocolo_str}.pdf'
    return response


@merenda_bp.route('/estoque/movimento/<int:movimento_id>/recibo-pdf')
@login_required
@role_required('Merenda Escolar', 'admin')
def pdf_recibo_saida_movimento(movimento_id):
    movimento = EstoqueMovimento.query.get_or_404(movimento_id)
    
    # Se pertencer a um grupo, busca todos os movimentos do mesmo grupo
    if movimento.codigo_grupo:
        movimentos_grupo = EstoqueMovimento.query.filter_by(codigo_grupo=movimento.codigo_grupo).order_by(EstoqueMovimento.id.asc()).all()
    else:
        movimentos_grupo = [movimento]

    escola = Escola.query.get(movimento.escola_id) if movimento.escola_id else None
    escola_nome = escola.nome if escola else "Unidade Escolar"

    itens_tabela = []
    for mov in movimentos_grupo:
        produto = ProdutoMerenda.query.get(mov.produto_id) if mov.produto_id else None
        qtd_emb = f"{mov.quantidade_embalagem:.2f} {mov.unidade_movimento or 'unid'}" if mov.quantidade_embalagem else "--"
        qtd_real = f"{mov.quantidade:.2f} {produto.unidade_consumo if produto else 'UNID'}"
        
        # Limpar observações longas/retroativas das linhas individuais do produto para a tabela ficar limpa
        obs_item = "Conforme Solicitação"
        if mov.observacao and "[Justificativa Data Retroativa:" not in mov.observacao:
            obs_item = mov.observacao

        itens_tabela.append({
            'produto': produto.nome if produto else 'Gênero Alimentício',
            'qtd_emb': qtd_emb,
            'qtd_real': qtd_real,
            'obs': obs_item
        })

    protocolo = f"GRP-{movimento.codigo_grupo}" if movimento.codigo_grupo else f"MOV-{movimento.id:06d}"

    return gerar_pdf_termo_entrega_profissional(
        titulo="TERMO DE EXPEDIÇÃO E GUIA DE ENTREGA",
        subtitulo="PNAE - Programa Nacional de Alimentação Escolar / FNDE",
        escola_nome=escola_nome,
        escola_obj=escola,
        data_emissao=movimento.data_movimento,
        responsavel=movimento.usuario_responsavel,
        itens_tabela=itens_tabela,
        observacao_geral=movimento.observacao,
        num_protocolo=protocolo
    )


@merenda_bp.route('/estoque/saidas', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def saida_estoque():
    try:
        tipo_saida = request.form.get('tipo_saida', 'Saída Escola')  # 'Saída Escola', 'Perda/Avaria', 'Ajuste Saldo'
        escola_id = request.form.get('escola_id', type=int) if tipo_saida == 'Saída Escola' else None
        observacao_geral = request.form.get('observacao')
        data_saida_str = request.form.get('data_saida')
        justificativa_retroativa = request.form.get('justificativa_retroativa')

        # Validação de data do movimento e retroatividade (fuso horário local BR)
        agora_local = datetime.now()
        hoje_date = agora_local.date()
        data_movimento = agora_local

        if data_saida_str:
            try:
                data_saida_dt = datetime.strptime(data_saida_str, '%Y-%m-%d').date()
                if data_saida_dt > hoje_date:
                    flash('Lançamento não permitido: A data de saída não pode ser futura.', 'danger')
                    return redirect(url_for('merenda.gerenciar_estoque'))
                elif data_saida_dt < hoje_date:
                    if justificativa_retroativa and justificativa_retroativa.strip():
                        observacao_geral = f"{observacao_geral or ''} [Justificativa Data Retroativa: {justificativa_retroativa.strip()}]".strip()
                    data_movimento = datetime.combine(data_saida_dt, agora_local.time())
                else:
                    data_movimento = agora_local
            except ValueError:
                data_movimento = agora_local

        # Suporte a múltiplos produtos (arrays no formulário) e compatibilidade com envio único
        produto_ids = request.form.getlist('produto_id[]')
        quantidades = request.form.getlist('quantidade[]')
        tipos_unidade = request.form.getlist('tipo_unidade[]')

        # Se não vier em formato array, verifica o formato legado de item único
        if not produto_ids:
            p_id = request.form.get('produto_id', type=int)
            q_val = request.form.get('quantidade', '0')
            t_unid = request.form.get('tipo_unidade', 'base')
            if p_id:
                produto_ids = [p_id]
                quantidades = [q_val]
                tipos_unidade = [t_unid]

        if not produto_ids or len(produto_ids) == 0:
            flash('Nenhum produto foi selecionado para a saída.', 'danger')
            return redirect(url_for('merenda.gerenciar_estoque'))

        # Estrutura para validar e armazenar os itens processados antes de efetivar no banco
        itens_processar = []
        erros_estoque = []

        for i in range(len(produto_ids)):
            raw_p_id = produto_ids[i]
            raw_qtd = quantidades[i] if i < len(quantidades) else '0'
            raw_tipo = tipos_unidade[i] if i < len(tipos_unidade) else 'base'

            if not raw_p_id:
                continue

            try:
                p_id = int(raw_p_id)
                qtd_digitada = float(str(raw_qtd).replace(',', '.'))
            except (ValueError, TypeError):
                continue

            if qtd_digitada <= 0:
                continue

            produto = ProdutoMerenda.query.get(p_id)
            if not produto:
                erros_estoque.append(f'Produto ID {p_id} não foi encontrado.')
                continue

            fator = float(produto.fator_conversao) if (produto.fator_conversao and produto.fator_conversao > 0) else 1.0

            if raw_tipo == 'master' and fator > 1.0:
                quantidade_subtrair = qtd_digitada * fator
                unidade_movimento = produto.unidade_medida or 'CX'
                msg_detalhe = f"{qtd_digitada:.2f} {unidade_movimento} ({quantidade_subtrair:.2f} {produto.unidade_consumo or 'UNID'})"
            else:
                quantidade_subtrair = qtd_digitada
                unidade_movimento = produto.unidade_consumo or 'UNID'
                msg_detalhe = f"{qtd_digitada:.2f} {unidade_movimento}"

            # Validação estrita do saldo disponível
            saldo_atual = produto.estoque_atual or 0.0
            if saldo_atual < quantidade_subtrair:
                erros_estoque.append(
                    f'Estoque insuficiente para "{produto.nome}". Saldo atual: {saldo_atual:.2f} {produto.unidade_consumo or "UNID"}. Solicitado: {quantidade_subtrair:.2f}.'
                )

            itens_processar.append({
                'produto': produto,
                'qtd_digitada': qtd_digitada,
                'qtd_subtrair': quantidade_subtrair,
                'unidade_movimento': unidade_movimento,
                'fator': fator,
                'msg_detalhe': msg_detalhe
            })

        if erros_estoque:
            for err in erros_estoque:
                flash(err, 'danger')
            return redirect(url_for('merenda.gerenciar_estoque'))

        if not itens_processar:
            flash('Informe pelo menos um produto com quantidade válida maior que zero.', 'danger')
            return redirect(url_for('merenda.gerenciar_estoque'))

        import uuid
        codigo_grupo = f"SAIDA-{agora_local.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        escola_obj = Escola.query.get(escola_id) if escola_id else None
        nome_destino = escola_obj.nome if escola_obj else ('Descarte/Perda' if tipo_saida == 'Perda/Avaria' else 'Ajuste de Estoque')
        usuario_resp = session.get('username', 'Sistema')

        movimentos_criados = []

        for item in itens_processar:
            prod = item['produto']
            prod.estoque_atual = (prod.estoque_atual or 0.0) - item['qtd_subtrair']

            mov = EstoqueMovimento(
                produto_id=prod.id,
                tipo=tipo_saida or 'Saída Escola',
                quantidade=item['qtd_subtrair'],
                unidade_movimento=item['unidade_movimento'],
                quantidade_embalagem=item['qtd_digitada'],
                fator_utilizado=item['fator'],
                escola_id=escola_id,
                observacao=observacao_geral,
                usuario_responsavel=usuario_resp,
                codigo_grupo=codigo_grupo,
                data_movimento=data_movimento
            )
            db.session.add(mov)
            movimentos_criados.append(mov)

            registrar_log(f'Saída de estoque ({tipo_saida}): {item["msg_detalhe"]} do produto "{prod.nome}" para {nome_destino}. [Grupo: {codigo_grupo}]')

        db.session.commit()

        mov_id = movimentos_criados[0].id if movimentos_criados else None
        pdf_url = url_for('merenda.pdf_recibo_saida_movimento', movimento_id=mov_id) if mov_id else None

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({
                'success': True,
                'pdf_url': pdf_url,
                'message': f'Saída de {len(movimentos_criados)} produto(s) registrada com sucesso para {nome_destino}.'
            })

        if escola_id or tipo_saida == 'Saída Escola':
            session['pdf_abrir_id'] = mov_id
            from markupsafe import Markup
            msg_html = Markup(
                f'✓ <strong>Sucesso!</strong> Saída de {len(movimentos_criados)} produto(s) registrada com sucesso para <strong>{nome_destino}</strong>. '
                f'<a href="{pdf_url}" target="_blank" class="btn btn-sm btn-danger text-white fw-bold ms-2 shadow-sm">'
                f'<i class="bi bi-file-earmark-pdf-fill me-1"></i> Imprimir Guia / Termo em PDF</a>'
            )
            flash(msg_html, 'success')
        else:
            flash(f'Sucesso! Saída de {len(movimentos_criados)} produto(s) registrada. Destino: {nome_destino}.', 'success')

        return redirect(url_for('merenda.gerenciar_estoque'))

    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'success': False, 'error': str(e)}), 400

        flash(f'Erro ao registrar saída de estoque: {e}', 'danger')
        return redirect(url_for('merenda.gerenciar_estoque'))

# Rotas para Solicitações das Escolas
@merenda_bp.route('/solicitacoes/nova', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def nova_solicitacao():
    if request.method == 'POST':
        try:
            escola_id = request.form.get('escola_id', type=int)
            
            # --- Validação ---
            if not escola_id:
                flash('É necessário selecionar uma escola.', 'danger')
                return redirect(url_for('merenda.nova_solicitacao'))
            
            # --- Cria a Solicitação Principal ---
            nova_sol = SolicitacaoMerenda(
                escola_id=escola_id,
                status='Pendente',
                solicitante_cpf=request.form.get('solicitante_cpf'),
                data_solicitacao=datetime.utcnow()
            )
            db.session.add(nova_sol)

            # --- Adiciona os Itens à Solicitação ---
            produtos_ids = request.form.getlist('produto_id[]')
            quantidades = request.form.getlist('quantidade[]')

            if not produtos_ids:
                flash('É necessário adicionar pelo menos um produto à solicitação.', 'danger')
                return redirect(url_for('merenda.nova_solicitacao'))

            for i in range(len(produtos_ids)):
                if not produtos_ids[i]: continue
                
                produto_id = int(produtos_ids[i])
                quantidade_str = quantidades[i].replace(',', '.')
                quantidade = float(quantidade_str)
                
                if produto_id and quantidade > 0:
                    item = SolicitacaoItem(
                        solicitacao=nova_sol,
                        produto_id=produto_id,
                        quantidade_solicitada=quantidade
                    )
                    db.session.add(item)
            
            db.session.commit()
            registrar_log(f'Criou a solicitação de merenda #{nova_sol.id} para a escola ID {escola_id}.')
            flash('Solicitação de merenda enviada com sucesso!', 'success')
            return redirect(url_for('merenda.painel_solicitacoes')) 

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao criar solicitação: {e}', 'danger')

    # --- LÓGICA DO MÉDOTO GET (CARREGAMENTO DO FORMULÁRIO) ---
    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    servidores = Servidor.query.order_by(Servidor.nome).all()
    
    # FILTRO: Não misturar produtos da Agricultura Familiar
    # Carrega apenas produtos da merenda escolar comum para solicitação padrão
    produtos = ProdutoMerenda.query.filter(
        or_(
            ProdutoMerenda.categoria != 'Agricultura Familiar', 
            ProdutoMerenda.categoria.is_(None)
        )
    ).order_by(ProdutoMerenda.nome).all()
    
    return render_template('merenda/solicitacao_form.html', 
                           escolas=escolas, 
                           produtos=produtos, 
                           servidores=servidores)
# GET /solicitacoes -> Painel para a Secretaria ver todas as solicitações
@merenda_bp.route('/solicitacoes')
@login_required
@role_required('Merenda Escolar', 'admin')
def painel_solicitacoes():
    # Filtra por status, se houver um parâmetro na URL
    status_filtro = request.args.get('status', 'Pendente')
    
    query = SolicitacaoMerenda.query
    if status_filtro != 'Todas':
        query = query.filter_by(status=status_filtro)
        
    solicitacoes = query.order_by(SolicitacaoMerenda.data_solicitacao.desc()).all()
    
    return render_template('merenda/solicitacoes_painel.html', solicitacoes=solicitacoes, status_atual=status_filtro)


@merenda_bp.route('/solicitacoes/<int:solicitacao_id>', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def detalhes_solicitacao(solicitacao_id):
    solicitacao = SolicitacaoMerenda.query.get_or_404(solicitacao_id)
    servidores = Servidor.query.order_by(Servidor.nome).all()

    if request.method == 'POST':
        try:
            # 1. Atualiza dados da entrega
            solicitacao.status = 'Entregue'
            solicitacao.entregador_cpf = request.form.get('entregador_cpf')
            solicitacao.data_entrega = datetime.now()

            # 2. Itera sobre os itens para dar baixa de 1 para 1
            for item in solicitacao.itens:
                produto = item.produto
                
                # Verifica se há estoque suficiente
                if produto.estoque_atual < item.quantidade_solicitada:
                    flash(f'Estoque insuficiente para "{produto.nome}". Saldo: {produto.estoque_atual} {produto.unidade_consumo}.', 'danger')
                    db.session.rollback()
                    return redirect(url_for('merenda.detalhes_solicitacao', solicitacao_id=solicitacao.id))

                # BAIXA DIRETA (Já está tudo em quilos/unidades)
                produto.estoque_atual -= item.quantidade_solicitada
                
                # Registra o movimento de SAÍDA
                movimento_saida = EstoqueMovimento(
                    produto_id=item.produto_id,
                    tipo='Saída',
                    quantidade=item.quantidade_solicitada,
                    solicitacao_id=solicitacao.id,
                    usuario_responsavel=session.get('username')
                )
                db.session.add(movimento_saida)

            db.session.commit()
            registrar_log(f'Finalizou entrega da solicitação #{solicitacao.id}.')
            flash('Entrega registrada e estoque atualizado!', 'success')
            return redirect(url_for('merenda.painel_solicitacoes', status='Entregue'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao registrar entrega: {e}', 'danger')

    from datetime import timedelta # Garanta que este import esteja no topo
    return render_template('merenda/solicitacao_detalhes.html', 
                       solicitacao=solicitacao, 
                       servidores=servidores, 
                       timedelta=timedelta)

@merenda_bp.route('/solicitacoes/<int:solicitacao_id>/autorizar', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def autorizar_solicitacao(solicitacao_id):
    solicitacao = SolicitacaoMerenda.query.get_or_404(solicitacao_id)
    autorizador_cpf = request.form.get('autorizador_cpf')
    
    try:
        solicitacao.status = 'Autorizada'
        solicitacao.autorizador_cpf = autorizador_cpf
        db.session.commit()
        registrar_log(f'Autorizou a solicitação de merenda #{solicitacao.id}.')
        flash('Solicitação autorizada com sucesso! Pronta para entrega.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao autorizar solicitação: {e}', 'danger')
        
    return redirect(url_for('merenda.detalhes_solicitacao', solicitacao_id=solicitacao_id))
# GET /solicitacoes/<id> -> Detalhes da solicitação para autorizar e registrar entrega
# POST /solicitacoes/<id>/autorizar -> Mudar status e preparar para saída
# POST /solicitacoes/<id>/entregar -> Registrar saída do estoque, entregador e data

# Rotas para Cardápios
# ==========================================================
# ROTAS DE CARDÁPIOS (MENSAL/CALENDÁRIO E PNAE)
# ==========================================================

@merenda_bp.route('/cardapios/gerenciar', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def gerenciar_cardapio():
    escola_id = request.args.get('escola_id', type=int) or request.form.get('escola_id', type=int)
    mes_selecionado = request.args.get('mes', type=int) or request.form.get('mes', type=int) or datetime.now().month
    ano_selecionado = request.args.get('ano', type=int) or request.form.get('ano', type=int) or datetime.now().year

    # --- PROCESSA SALVAMENTO (POST - CRIA OU ATUALIZA) ---
    if request.method == 'POST' and escola_id:
        try:
            cardapio = Cardapio.query.filter_by(
                escola_id=escola_id, 
                mes=mes_selecionado, 
                ano=ano_selecionado
            ).first()

            if not cardapio:
                cardapio = Cardapio(
                    escola_id=escola_id, 
                    mes=mes_selecionado, 
                    ano=ano_selecionado
                )
                db.session.add(cardapio)
                db.session.flush()

            # Remove os pratos antigos do cardápio para regravar os atualizados
            PratoDiario.query.filter_by(cardapio_id=cardapio.id).delete()

            for key, value in request.form.items():
                if key.startswith('prato_') and value.strip():
                    data_str = key.replace('prato_', '')
                    try:
                        data_obj = datetime.strptime(data_str, '%Y-%m-%d').date()
                        novo_prato = PratoDiario(
                            cardapio_id=cardapio.id,
                            data=data_obj,
                            descricao=value.strip()
                        )
                        db.session.add(novo_prato)
                    except ValueError:
                        continue

            db.session.commit()
            registrar_log(f"Salvou o cardápio mensal #{cardapio.id} ({mes_selecionado}/{ano_selecionado}) para escola ID {escola_id}.")
            flash('Cardápio salvo com sucesso!', 'success')
            
            return redirect(url_for('merenda.editar_cardapio_mensal', cardapio_id=cardapio.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar cardápio: {str(e)}', 'danger')

    # --- CARREGAMENTO DE DADOS (GET) ---
    pratos_dict = {}
    cardapio_atual_id = None
    if escola_id:
        cardapio = Cardapio.query.filter_by(
            escola_id=escola_id, 
            mes=mes_selecionado, 
            ano=ano_selecionado
        ).first()
        
        if cardapio:
            cardapio_atual_id = cardapio.id
            pratos_registrados = PratoDiario.query.filter_by(cardapio_id=cardapio.id).all()
            for p in pratos_registrados:
                d_obj = datetime.strptime(p.data, '%Y-%m-%d').date() if isinstance(p.data, str) else p.data
                pratos_dict[d_obj] = p.descricao

    cal = calendar.Calendar(firstweekday=0)
    calendario_mes = cal.monthdayscalendar(ano_selecionado, mes_selecionado)

    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    cardapios_cadastrados = Cardapio.query.order_by(Cardapio.ano.desc(), Cardapio.mes.desc()).all()

    meses_pt = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
        5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
        9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }

    return render_template(
        'merenda/cardapio_editor.html',
        escolas=escolas,
        escola_selecionada_id=escola_id,
        mes_selecionado=mes_selecionado,
        ano_selecionado=ano_selecionado,
        pratos=pratos_dict,
        calendario_mes=calendario_mes,
        meses_pt=meses_pt,
        anos_disponiveis=[2025, 2026, 2027],
        date=date,
        cardapio_atual_id=cardapio_atual_id,
        cardapios_cadastrados=cardapios_cadastrados
    )


@merenda_bp.route('/cardapios/editar-pnae/<int:cardapio_id>', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def editar_cardapio_pnae(cardapio_id):
    """Rota para editar os dados técnicos e itens diários do Cardápio PNAE"""
    cardapio = Cardapio.query.get_or_404(cardapio_id)

    if request.method == 'POST':
        try:
            # Atualiza os campos conforme as colunas reais da tabela cardapio
            cardapio.nome = request.form.get('nome')
            cardapio.escola_id = request.form.get('escola_id', type=int)
            cardapio.etapa_pnae = request.form.get('etapa_pnae')
            cardapio.modalidade_atendimento = request.form.get('modalidade_atendimento')
            cardapio.semanas_referencia = request.form.get('semanas_referencia')  # Adicionado
            
            validade_inicio = request.form.get('validade_inicio')
            validade_fim = request.form.get('validade_fim')
            if validade_inicio:
                cardapio.validade_inicio = datetime.strptime(validade_inicio, '%Y-%m-%d').date()
            if validade_fim:
                cardapio.validade_fim = datetime.strptime(validade_fim, '%Y-%m-%d').date()

            cardapio.restricao_alergica = request.form.get('restricao_alergica')
            cardapio.observacoes = request.form.get('observacoes')

            # --- ATUALIZA MÚLTIPLOS NUTRICIONISTAS ---
            CardapioNutricionista.query.filter_by(cardapio_id=cardapio.id).delete()
            nutri_nomes = request.form.getlist('nutricionista_nome[]')
            nutri_crns = request.form.getlist('nutricionista_crn[]')
            for n, c in zip(nutri_nomes, nutri_crns):
                if n.strip():
                    nutri_item = CardapioNutricionista(
                        cardapio_id=cardapio.id,
                        nome=n.strip(),
                        crn=c.strip()
                    )
                    db.session.add(nutri_item)
            # ----------------------------------------

            # Atualiza os itens diários utilizando a tabela cardapio_item_diario
            CardapioItemDiario.query.filter_by(cardapio_id=cardapio.id).delete()

            dias = request.form.getlist('dia_semana[]')
            tipos = request.form.getlist('tipo_refeicao[]')
            horarios = request.form.getlist('horario_servido[]')
            descricoes = request.form.getlist('descricao_preparacao[]')
            bebidas = request.form.getlist('bebida_acompanhamento[]')
            nutricionais = request.form.getlist('informacao_nutricional_resumo[]')

            for i in range(len(descricoes)):
                if descricoes[i].strip():
                    novo_item = CardapioItemDiario(
                        cardapio_id=cardapio.id,
                        dia_semana=dias[i] if i < len(dias) else '',
                        tipo_refeicao=tipos[i] if i < len(tipos) else '',
                        horario_servido=horarios[i] if i < len(horarios) else '',
                        descricao_preparacao=descricoes[i].strip(),
                        bebida_acompanhamento=bebidas[i] if i < len(bebidas) else '',
                        informacao_nutricional_resumo=nutricionais[i] if i < len(nutricionais) else ''
                    )
                    db.session.add(novo_item)

            db.session.commit()
            registrar_log(f"Atualizou o cardápio PNAE #{cardapio.id} - {cardapio.nome}.")
            flash('Cardápio PNAE atualizado com sucesso!', 'success')
            return redirect(url_for('merenda.listar_cardapios_pnae'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar cardápio PNAE: {str(e)}', 'danger')

    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    
    return render_template(
        'merenda/cardapio_pnae_editar.html',
        cardapio=cardapio,
        escolas=escolas
    )

# GET /cardapios -> Visão geral dos cardápios das escolas
# GET /escola/<id>/cardapio -> Editor do cardápio semanal da escola
# POST /escola/<id>/cardapio -> Salvar as alterações do cardápio e registrar no histórico
@merenda_bp.route('/relatorios/saidas', methods=['GET'])
@login_required
@role_required('Merenda Escolar', 'admin')
def relatorio_saidas():
    escolas = Escola.query.order_by(Escola.nome).all()
    
    escola_id = request.args.get('escola_id', type=int)
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    gerar_pdf = request.args.get('gerar_pdf')

    resultados = []
    if escola_id:
        query = db.session.query(
            EstoqueMovimento.id,
            EstoqueMovimento.data_movimento,
            ProdutoMerenda.nome,
            EstoqueMovimento.quantidade,
            ProdutoMerenda.unidade_consumo,
            EstoqueMovimento.unidade_movimento,
            EstoqueMovimento.quantidade_embalagem,
            EstoqueMovimento.usuario_responsavel
        ).join(ProdutoMerenda).outerjoin(SolicitacaoMerenda).filter(
            or_(EstoqueMovimento.escola_id == escola_id, SolicitacaoMerenda.escola_id == escola_id),
            or_(EstoqueMovimento.tipo == 'Saída', EstoqueMovimento.tipo == 'Saída Escola')
        )

        if data_inicio_str and data_fim_str:
            data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d')
            data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d') + timedelta(days=1, seconds=-1)
            query = query.filter(EstoqueMovimento.data_movimento.between(data_inicio, data_fim))

        resultados = query.order_by(EstoqueMovimento.data_movimento.desc()).all()
        
        if gerar_pdf:
            escola = Escola.query.get(escola_id)
            titulo = f"Relatório de Saídas para {escola.nome if escola else 'Escola'}"
            periodo = f"Período: {data_inicio_str or 'Histórico Completo'} a {data_fim_str or 'Atual'}"
            return gerar_pdf_saidas(titulo, periodo, resultados)

    return render_template('merenda/relatorio_saidas.html', 
                           escolas=escolas, 
                           resultados=resultados,
                           escola_selecionada_id=escola_id,
                           data_inicio=data_inicio_str,
                           data_fim=data_fim_str)

def gerar_pdf_saidas(titulo, periodo, dados):
    escola_nome = titulo.replace("Relatório de Saídas para ", "").strip()
    escola_obj = Escola.query.filter_by(nome=escola_nome).first()

    itens_tabela = []
    for item in dados:
        qtd_emb = f"{item.quantidade_embalagem:.1f} {item.unidade_movimento or ''}" if item.quantidade_embalagem else "--"
        qtd_real = f"{item.quantidade:.2f} {item.unidade_consumo or 'UNID'}"
        itens_tabela.append({
            'produto': item.nome,
            'qtd_emb': qtd_emb,
            'qtd_real': qtd_real,
            'obs': f"Resp: {item.usuario_responsavel or 'Sistema'}"
        })

    return gerar_pdf_termo_entrega_profissional(
        titulo="RELATÓRIO DE SAÍDAS E GUIA DE EXPEDIÇÃO",
        subtitulo=f"PNAE | {periodo}",
        escola_nome=escola_nome,
        escola_obj=escola_obj,
        data_emissao=datetime.now(),
        responsavel=session.get('username', 'Sistema'),
        itens_tabela=itens_tabela,
        observacao_geral=f"Relatório consolidado de saídas por escola. Total de {len(dados)} entregas.",
        num_protocolo="REL-" + datetime.now().strftime('%Y%m%d%H%M')
    )                       
    
    
@merenda_bp.route('/relatorios/validade-lotes', methods=['GET'])
@login_required
@role_required('Merenda Escolar', 'admin')
def relatorio_validade_lotes():
    gerar_pdf = request.args.get('gerar_pdf')
    hoje = date.today()
    data_limite_alerta = hoje + timedelta(days=30)

    lotes_30_dias = db.session.query(
        ProdutoMerenda.nome.label('produto_nome'),
        EstoqueMovimento.lote,
        EstoqueMovimento.data_validade,
        EstoqueMovimento.fornecedor,
        ProdutoMerenda.unidade_consumo,
        ProdutoMerenda.estoque_atual
    ).join(ProdutoMerenda).filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None)),
        EstoqueMovimento.tipo == 'Entrada',
        EstoqueMovimento.data_validade.isnot(None),
        EstoqueMovimento.data_validade <= data_limite_alerta,
        EstoqueMovimento.data_validade >= hoje,
        ProdutoMerenda.estoque_atual > 0
    ).order_by(EstoqueMovimento.data_validade.asc()).all()

    if gerar_pdf:
        from utils import cabecalho_e_rodape_moderno
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from flask import make_response
        import io

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=3*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph("RELATÓRIO DE CONTROLE DE VALIDADE E LOTES (ALERTA 30 DIAS)", styles['h2']))
        story.append(Paragraph(f"Emissão: {datetime.now().strftime('%d/%m/%Y %H:%M')} | Regra FEFO - Prioridade de Consumo", styles['Normal']))
        story.append(Spacer(1, 0.5*cm))

        table_data = [['Produto', 'Lote', 'Data Validade', 'Dias Restantes', 'Fornecedor', 'Saldo Atual']]
        for item in lotes_30_dias:
            dias = (item.data_validade - hoje).days
            table_data.append([
                item.produto_nome,
                item.lote or 'S/L',
                item.data_validade.strftime('%d/%m/%Y'),
                f"{dias} dias",
                item.fornecedor or '--',
                f"{item.estoque_atual:.2f} {item.unidade_consumo or 'UNID'}"
            ])

        t = Table(table_data, colWidths=[4.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3.5*cm, 2.5*cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#c0392b')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')])
        ]))
        story.append(t)
        story.append(Spacer(1, 1.5*cm))
        story.append(Paragraph("________________________________________", styles['Normal']))
        story.append(Paragraph("Responsável Técnico / Nutricionista", styles['Normal']))

        doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Alerta de Validades (30 Dias)"),
                         onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Alerta de Validades (30 Dias)"))
        buffer.seek(0)
        resp = make_response(buffer.getvalue())
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = 'inline; filename=relatorio_validade_30dias.pdf'
        return resp

    return render_template('merenda/relatorio_validade_lotes.html', lotes=lotes_30_dias, hoje=hoje)


@merenda_bp.route('/relatorios/posicao-estoque', methods=['GET'])
@login_required
@role_required('Merenda Escolar', 'admin')
def relatorio_posicao_estoque():
    gerar_pdf = request.args.get('gerar_pdf')
    produtos = ProdutoMerenda.query.filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None))
    ).order_by(ProdutoMerenda.nome.asc()).all()

    if gerar_pdf:
        from utils import cabecalho_e_rodape_moderno
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from flask import make_response
        import io

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=3*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph("POSIÇÃO OFICIAL DE ESTOQUE - PRESTAÇÃO DE CONTAS", styles['h2']))
        story.append(Paragraph(f"Data de Emissão: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} | Almoxarifado Central", styles['Normal']))
        story.append(Spacer(1, 0.5*cm))

        table_data = [['Produto', 'Categoria', 'Saldo Real (Escola)', 'Equivalente Master', 'Situação']]
        for p in produtos:
            fator = p.fator_conversao if (p.fator_conversao and p.fator_conversao > 0) else 1.0
            saldo = p.estoque_atual or 0.0
            eq_master = f"{saldo/fator:.1f} {p.unidade_medida or 'CX'}" if fator > 1.0 else "Avulso"
            status = "Zerado" if saldo <= 0 else ("Baixo" if saldo <= (p.estoque_minimo or 10) else "Normal")
            table_data.append([
                p.nome,
                p.categoria or 'Geral',
                f"{saldo:.2f} {p.unidade_consumo or 'UNID'}",
                eq_master,
                status
            ])

        t = Table(table_data, colWidths=[5.5*cm, 3.5*cm, 4*cm, 3*cm, 2*cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')])
        ]))
        story.append(t)
        story.append(Spacer(1, 1.5*cm))
        story.append(Paragraph("________________________________________", styles['Normal']))
        story.append(Paragraph("Gestor / Responsável pelo Estoque da Merenda", styles['Normal']))

        doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Posição Oficial de Estoque"),
                         onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Posição Oficial de Estoque"))
        buffer.seek(0)
        resp = make_response(buffer.getvalue())
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = 'inline; filename=posicao_estoque_merenda.pdf'
        return resp

    return render_template('merenda/relatorio_posicao_estoque.html', produtos=produtos)


@merenda_bp.route('/relatorios/distribuicao-geral', methods=['GET'])
@login_required
@role_required('Merenda Escolar', 'admin')
def relatorio_distribuicao_geral():
    gerar_pdf = request.args.get('gerar_pdf')
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    categoria_filtro = request.args.get('categoria')

    # Base query de movimentos de saída
    base_filter = [
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None)),
        or_(EstoqueMovimento.tipo == 'Saída', EstoqueMovimento.tipo == 'Saída Escola')
    ]

    if data_inicio_str and data_fim_str:
        dt_ini = datetime.strptime(data_inicio_str, '%Y-%m-%d')
        dt_fim = datetime.strptime(data_fim_str, '%Y-%m-%d') + timedelta(days=1, seconds=-1)
        base_filter.append(EstoqueMovimento.data_movimento.between(dt_ini, dt_fim))

    if categoria_filtro:
        base_filter.append(ProdutoMerenda.categoria == categoria_filtro)

    # Resultados analíticos agrupados por produto
    resultados_query = db.session.query(
        ProdutoMerenda.id,
        ProdutoMerenda.nome.label('produto_nome'),
        ProdutoMerenda.categoria,
        ProdutoMerenda.unidade_medida,
        ProdutoMerenda.unidade_consumo,
        ProdutoMerenda.fator_conversao,
        ProdutoMerenda.estoque_atual,
        func.sum(EstoqueMovimento.quantidade).label('total_quantidade'),
        func.count(EstoqueMovimento.id).label('total_saidas')
    ).join(ProdutoMerenda).filter(*base_filter).group_by(
        ProdutoMerenda.id,
        ProdutoMerenda.nome,
        ProdutoMerenda.categoria,
        ProdutoMerenda.unidade_medida,
        ProdutoMerenda.unidade_consumo,
        ProdutoMerenda.fator_conversao,
        ProdutoMerenda.estoque_atual
    ).order_by(func.sum(EstoqueMovimento.quantidade).desc()).all()

    # Formatação de equivalência máster nos resultados
    resultados = []
    total_volume_geral = 0.0
    for r in resultados_query:
        fator = float(r.fator_conversao) if (r.fator_conversao and r.fator_conversao > 0) else 1.0
        qtd_total = float(r.total_quantidade or 0.0)
        total_volume_geral += qtd_total
        eq_master = f"~ {qtd_total / fator:.1f} {r.unidade_medida or 'CX'}" if fator > 1.0 else "Avulso"
        resultados.append({
            'id': r.id,
            'produto_nome': r.produto_nome,
            'categoria': r.categoria or 'Geral',
            'unidade_consumo': r.unidade_consumo or 'UNID',
            'unidade_medida': r.unidade_medida or 'CX',
            'total_quantidade': qtd_total,
            'total_saidas': r.total_saidas,
            'estoque_atual': r.estoque_atual or 0.0,
            'eq_master': eq_master
        })

    # KPIs de Auditoria
    escolas_atendidas_count = db.session.query(
        func.count(func.distinct(EstoqueMovimento.escola_id))
    ).filter(*base_filter).scalar() or 0

    expedicoes_count = db.session.query(
        func.count(EstoqueMovimento.id)
    ).filter(*base_filter).scalar() or 0

    # Categorias Stats para gráfico
    cat_query = db.session.query(
        ProdutoMerenda.categoria,
        func.sum(EstoqueMovimento.quantidade).label('total')
    ).join(ProdutoMerenda).filter(*base_filter).group_by(ProdutoMerenda.categoria).all()

    categorias_labels = [c.categoria or 'Geral' for c in cat_query] if cat_query else ['Geral']
    categorias_data = [float(c.total or 0.0) for c in cat_query] if cat_query else [0]

    # Top Escolas Stats para gráfico
    top_escolas_query = db.session.query(
        Escola.nome,
        func.sum(EstoqueMovimento.quantidade).label('total')
    ).join(EstoqueMovimento, EstoqueMovimento.escola_id == Escola.id)\
     .join(ProdutoMerenda, EstoqueMovimento.produto_id == ProdutoMerenda.id)\
     .filter(*base_filter).group_by(Escola.id, Escola.nome)\
     .order_by(func.sum(EstoqueMovimento.quantidade).desc()).limit(5).all()

    top_escolas_labels = [e.nome[:22] for e in top_escolas_query] if top_escolas_query else ['Sem dados']
    top_escolas_data = [float(e.total or 0.0) for e in top_escolas_query] if top_escolas_query else [0]

    # Lista de todas as categorias cadastradas para o filtro
    categorias_disponiveis = [r[0] for r in db.session.query(ProdutoMerenda.categoria).distinct().all() if r[0]]

    # PDF EXECUTIVO DE AUDITORIA (PNAE/TCE)
    if gerar_pdf:
        from utils import cabecalho_e_rodape_moderno
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from flask import make_response
        import io

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=3*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            name='AuditTitle',
            parent=styles['Heading2'],
            fontName='Helvetica-Bold',
            fontSize=13,
            leading=16,
            textColor=colors.HexColor('#004d40'),
            alignment=1 # Center
        )

        sub_style = ParagraphStyle(
            name='AuditSub',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#555555'),
            alignment=1
        )

        story = []

        story.append(Paragraph("RELATÓRIO AUDITÁVEL CONSOLIDADO DE DISTRIBUIÇÃO DA MERENDA", title_style))
        periodo_txt = f"Período de Referência: {data_inicio_str} a {data_fim_str}" if data_inicio_str and data_fim_str else "Período: Histórico Completo de Expedições"
        story.append(Paragraph(f"{periodo_txt} | Protocolo Oficial: PNAE-{datetime.now().strftime('%Y%m%d%H%M')}", sub_style))
        story.append(Spacer(1, 0.4*cm))

        # Quadro de Indicadores Resumidos
        kpi_table_data = [
            ['Volume Total Distribuído', 'Expedições Realizadas', 'Escolas Atendidas', 'Variedade de Itens'],
            [
                f"{total_volume_geral:,.2f} UN/KG/L",
                f"{expedicoes_count} guias",
                f"{escolas_atendidas_count} escolas",
                f"{len(resultados)} alimentos"
            ]
        ]
        kpi_table = Table(kpi_table_data, colWidths=[4.5*cm, 4.5*cm, 4.5*cm, 4.5*cm])
        kpi_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#e0f2f1')),
            ('TEXTCOLOR', (0, 1), (-1, 1), colors.HexColor('#004d40')),
            ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, 1), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#004d40'))
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 0.6*cm))

        # Tabela Analítica
        table_data = [['#', 'Alimento / Produto', 'Categoria', 'Total Distribuído', 'Equiv. Caixas', 'Nº Saídas']]
        idx = 1
        for res in resultados:
            table_data.append([
                str(idx),
                res['produto_nome'],
                res['categoria'],
                f"{res['total_quantidade']:.2f} {res['unidade_consumo']}",
                res['eq_master'],
                f"{res['total_saidas']} reg."
            ])
            idx += 1

        t = Table(table_data, colWidths=[1*cm, 6.5*cm, 3.5*cm, 3.5*cm, 2.5*cm, 1.8*cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a237e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 1), (1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cfd8dc')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')])
        ]))
        story.append(t)
        story.append(Spacer(1, 1*cm))

        # Bloco de Parecer Técnico e Assinaturas Auditáveis
        story.append(KeepTogether([
            Paragraph("PARECER E ASSINATURAS DE AUDITORIA E PRESTAÇÃO DE CONTAS", styles['Heading4']),
            Spacer(1, 0.3*cm),
            Paragraph("Atestamos para os devidos fins de comprovação junto ao PNAE/FNDE e Tribunal de Contas que a distribuição de gêneros alimentícios discriminada acima obedeceu rigorosamente aos critérios nutricionais e logísticos estabelecidos.", styles['Normal']),
            Spacer(1, 1.2*cm),
            Table([
                [
                    "___________________________________\nNutricionista Responsável (CRN)",
                    "___________________________________\nCoordenador da Merenda Escolar",
                    "___________________________________\nSecretário(a) Municipal de Educação"
                ]
            ], colWidths=[6*cm, 6*cm, 6*cm], style=[
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')
            ])
        ]))

        doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Relatório de Auditoria da Merenda"),
                         onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Relatório de Auditoria da Merenda"))
        buffer.seek(0)
        resp = make_response(buffer.getvalue())
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = 'inline; filename=relatorio_auditoria_merenda.pdf'
        return resp

    return render_template(
        'merenda/relatorio_distribuicao_geral.html',
        resultados=resultados,
        data_inicio=data_inicio_str,
        data_fim=data_fim_str,
        categoria_selecionada=categoria_filtro,
        categorias_disponiveis=categorias_disponiveis,
        total_volume_geral=total_volume_geral,
        escolas_atendidas_count=escolas_atendidas_count,
        expedicoes_count=expedicoes_count,
        categorias_labels=categorias_labels,
        categorias_data=categorias_data,
        top_escolas_labels=top_escolas_labels,
        top_escolas_data=top_escolas_data
    )


@merenda_bp.route('/relatorios/consumo-mensal', methods=['GET'])
@login_required
@role_required('Merenda Escolar', 'admin')
def relatorio_consolidado_mensal():
    hoje = date.today()
    mes_selecionado = request.args.get('mes', hoje.month, type=int)
    ano_selecionado = request.args.get('ano', hoje.year, type=int)
    gerar_pdf = request.args.get('gerar_pdf')

    # Define o primeiro e o último dia do mês selecionado
    primeiro_dia = date(ano_selecionado, mes_selecionado, 1)
    ultimo_dia = date(ano_selecionado, mes_selecionado, calendar.monthrange(ano_selecionado, mes_selecionado)[1])
    
    # Busca e agrupa os dados de saída para o mês inteiro
    resultados = db.session.query(
            ProdutoMerenda.nome,
            ProdutoMerenda.unidade_medida,
            func.sum(EstoqueMovimento.quantidade).label('total_quantidade')
        ).join(ProdutoMerenda).filter(
            EstoqueMovimento.tipo == 'Saída',
            func.date(EstoqueMovimento.data_movimento).between(primeiro_dia, ultimo_dia)
        ).group_by(ProdutoMerenda.nome, ProdutoMerenda.unidade_medida)\
         .order_by(ProdutoMerenda.nome).all()

    # Se o botão de PDF foi clicado, chama a função que gera o PDF
    if gerar_pdf:
        titulo = "Relatório Consolidado de Consumo Mensal"
        periodo = f"Mês/Ano: {mes_selecionado:02d}/{ano_selecionado}"
        return gerar_pdf_consolidado(titulo, periodo, resultados)
    
    # Gera uma lista de meses e anos para os filtros do formulário
    meses_pt = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
        7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
    }
    anos_disponiveis = range(hoje.year - 1, hoje.year + 2)

    return render_template('merenda/relatorio_consolidado.html',
                           resultados=resultados,
                           mes_selecionado=mes_selecionado,
                           ano_selecionado=ano_selecionado,
                           meses_pt=meses_pt,
                           anos_disponiveis=anos_disponiveis)



def gerar_pdf_consolidado(titulo, periodo, dados):
    """
    Função que gera o PDF do relatório consolidado mensal.
    """
    from .utils import cabecalho_e_rodape_moderno
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from flask import make_response
    import io

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=3*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Center', alignment=TA_CENTER))

    story = []
    
    story.append(Paragraph(titulo, styles['h1']))
    story.append(Paragraph(periodo, styles['Center']))
    story.append(Spacer(1, 1*cm))

    # Prepara os dados da tabela
    table_data = [['Produto', 'Quantidade Total Consumida']]
    
    for item in dados:
        quantidade_formatada = f"{item.total_quantidade:.2f} {item.unidade_medida}"
        table_data.append([item.nome, quantidade_formatada])

    # Cria a tabela
    t = Table(table_data, colWidths=[12*cm, 5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(t)
    
    doc.build(story, onFirstPage=lambda canvas, doc: cabecalho_e_rodape_moderno(canvas, doc, "Relatório Consolidado"), 
                     onLaterPages=lambda canvas, doc: cabecalho_e_rodape_moderno(canvas, doc, "Relatório Consolidado"))
    
    buffer.seek(0)
    
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=relatorio_consolidado_mensal.pdf'
    
    return response



# --- MÓDULO AGRICULTURA FAMILIAR ---

@merenda_bp.route('/agricultura', methods=['GET', 'POST'])
@login_required
def agricultura_dashboard():
    # Lógica para SALVAR a configuração (se o form for enviado)
    if request.method == 'POST':
        try:
            ano_atual = datetime.now().year
            valor = float(request.form.get('valor_total_repasse', '0').replace('.', '').replace(',', '.'))
            
            config = ConfiguracaoPNAE.query.filter_by(ano=ano_atual).first()
            if not config:
                config = ConfiguracaoPNAE(ano=ano_atual, valor_total_repasse=valor)
                db.session.add(config)
            else:
                config.valor_total_repasse = valor
            
            db.session.commit()
            flash(f'Orçamento do PNAE para {ano_atual} atualizado!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar configuração: {e}', 'danger')
        return redirect(url_for('merenda.agricultura_dashboard'))

    # Lógica de Visualização
    total_agricultores = AgricultorFamiliar.query.count()
    contratos_ativos = ContratoPNAE.query.count()
    
    ano_atual = datetime.now().year
    
    # 1. Soma valor total contratado (apenas informativo, se desejar manter)
    total_contratado = db.session.query(func.sum(ContratoPNAE.valor_total))\
        .filter(func.extract('year', ContratoPNAE.data_inicio) == ano_atual).scalar() or 0.0
    
    # 2. SOMA O VALOR EXECUTADO (Entregas Aprovadas) - ESTA É A MUDANÇA PRINCIPAL
    total_executado = db.session.query(func.sum(EntregaPNAE.valor_total))\
        .filter(EntregaPNAE.status == 'Aprovado')\
        .join(ContratoPNAE)\
        .filter(func.extract('year', ContratoPNAE.data_inicio) == ano_atual).scalar() or 0.0
        
    # Busca configuração do ano
    config_pnae = ConfiguracaoPNAE.query.filter_by(ano=ano_atual).first()
    
    meta_info = {
        'total_repasse': 0.0,
        'percentual_atual': 0.0,
        'meta_lei': 30 if ano_atual < 2026 else 45,
        'falta_executar': 0.0, # Renomeado para ficar claro que é sobre execução
        'status': 'Aguardando Configuração'
    }
    
    if config_pnae:
        meta_info['total_repasse'] = config_pnae.valor_total_repasse
        meta_info['meta_lei'] = config_pnae.meta_percentual
        
        if config_pnae.valor_total_repasse > 0:
            # Cálculo baseado no EXECUTADO
            percentual = (total_executado / config_pnae.valor_total_repasse) * 100
            meta_info['percentual_atual'] = percentual
            
            valor_minimo = config_pnae.valor_meta_minima
            if total_executado >= valor_minimo:
                meta_info['status'] = 'Meta Atingida! 🎉'
            else:
                meta_info['falta_executar'] = valor_minimo - total_executado
                meta_info['status'] = 'Abaixo da Meta ⚠️'

    return render_template('merenda/agricultura/dashboard.html', 
                           total_agricultores=total_agricultores, 
                           contratos_ativos=contratos_ativos,
                           total_contratado=total_contratado,
                           total_executado=total_executado,
                           meta_info=meta_info,
                           ano_atual=ano_atual)
    
@merenda_bp.route('/agricultura/fornecedores/editar/<int:agricultor_id>', methods=['GET', 'POST'])
@login_required
def editar_agricultor(agricultor_id):
    agricultor = AgricultorFamiliar.query.get_or_404(agricultor_id)
    
    if request.method == 'POST':
        try:
            # Atualiza dados básicos
            agricultor.tipo_fornecedor = request.form.get('tipo_fornecedor')
            agricultor.razao_social = request.form.get('razao_social')
            agricultor.cpf_cnpj = limpar_cpf(request.form.get('cpf_cnpj'))
            agricultor.dap_caf_numero = request.form.get('dap_caf_numero')
            agricultor.dap_caf_validade = datetime.strptime(request.form.get('dap_caf_validade'), '%Y-%m-%d').date() if request.form.get('dap_caf_validade') else None
            agricultor.representante_nome = request.form.get('representante_nome')
            agricultor.telefone = request.form.get('telefone')
            agricultor.email = request.form.get('email')
            
            # Atualiza endereço
            agricultor.zona = request.form.get('zona')
            agricultor.comunidade = request.form.get('comunidade')
            agricultor.endereco_completo = request.form.get('endereco_completo')
            agricultor.descricao_propriedade = request.form.get('descricao_propriedade')
            agricultor.latitude = request.form.get('latitude')
            agricultor.longitude = request.form.get('longitude')
            
            # Atualiza Logística
            agricultor.frequencia_entrega = request.form.get('frequencia_entrega')
            agricultor.possui_transporte = True if request.form.get('possui_transporte') == '1' else False
            agricultor.local_entrega_preferencia = request.form.get('local_entrega_preferencia')

            # --- Tratamento de Uploads (Substituição ou Adição) ---
            # Dicionário mapeando o nome do input HTML para o Tipo de Documento no banco
            mapa_arquivos = {
                'comprovante_residencia': 'Comprovante de Residência',
                'projeto_venda': 'Projeto de Venda (PVAF)',
                'cnd_federal': 'CND Federal',
                'cnd_estadual': 'CND Estadual',
                'cnd_municipal': 'CND Municipal'
            }

            for input_name, tipo_doc in mapa_arquivos.items():
                file = request.files.get(input_name)
                if file and file.filename != '':
                    # Envia para o Supabase
                    url_doc = upload_arquivo_para_nuvem(file, pasta="pnae_documentos")
                    
                    if url_doc:
                        # Verifica se já existe esse tipo de documento para atualizar, ou cria novo
                        doc_existente = DocumentoAgricultor.query.filter_by(agricultor_id=agricultor.id, tipo_documento=tipo_doc).first()
                        
                        if doc_existente:
                            doc_existente.filename = url_doc # Atualiza o link
                            doc_existente.data_upload = datetime.utcnow()
                        else:
                            novo_doc = DocumentoAgricultor(
                                agricultor_id=agricultor.id,
                                tipo_documento=tipo_doc,
                                filename=url_doc
                            )
                            db.session.add(novo_doc)

            db.session.commit()
            flash('Dados do agricultor atualizados com sucesso!', 'success')
            return redirect(url_for('merenda.listar_agricultores'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar: {e}', 'danger')
            
    # GET: Prepara dicionário de documentos existentes para mostrar no template
    docs_existentes = {doc.tipo_documento: doc.filename for doc in agricultor.documentos}
    
    return render_template('merenda/agricultura/fornecedor_form.html', agricultor=agricultor, docs=docs_existentes)

@merenda_bp.route('/agricultura/fornecedores/excluir/<int:agricultor_id>')
@login_required
def excluir_agricultor(agricultor_id):
    agricultor = AgricultorFamiliar.query.get_or_404(agricultor_id)
    
    # Validação de Segurança: Não excluir se tiver contratos
    if agricultor.contratos:
        flash(f'Não é possível excluir o agricultor "{agricultor.razao_social}" pois ele possui contratos cadastrados. Exclua os contratos primeiro.', 'warning')
        return redirect(url_for('merenda.listar_agricultores'))
        
    try:
        # Excluir documentos do banco (os arquivos no Supabase permanecem por segurança ou podem ser excluídos via API se desejar)
        DocumentoAgricultor.query.filter_by(agricultor_id=agricultor.id).delete()
        
        db.session.delete(agricultor)
        db.session.commit()
        flash('Agricultor excluído com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir agricultor: {e}', 'danger')
        
    return redirect(url_for('merenda.listar_agricultores'))    

@merenda_bp.route('/agricultura/fornecedores')
@login_required
def listar_agricultores():
    agricultores = AgricultorFamiliar.query.order_by(AgricultorFamiliar.razao_social).all()
    return render_template('merenda/agricultura/agricultores_lista.html', agricultores=agricultores)


@merenda_bp.route('/agricultura/fornecedores/pdf')
@login_required
@role_required('Merenda Escolar', 'admin')
def pdf_relatorio_agricultores():
    agricultores = AgricultorFamiliar.query.order_by(AgricultorFamiliar.razao_social.asc()).all()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=2.5*cm,
        bottomMargin=2.0*cm
    )

    styles = getSampleStyleSheet()

    style_title = ParagraphStyle(
        'DocTitle',
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        textColor=colors.HexColor('#004d40'),
        alignment=TA_LEFT
    )
    style_sub = ParagraphStyle(
        'DocSub',
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#475569'),
        alignment=TA_LEFT
    )
    style_th = ParagraphStyle(
        'TH',
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=10,
        textColor=colors.whitesmoke,
        alignment=TA_CENTER
    )
    style_td = ParagraphStyle(
        'TD',
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#1e293b')
    )
    style_td_bold = ParagraphStyle(
        'TDBold',
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#0f172a')
    )
    style_td_center = ParagraphStyle(
        'TDCenter',
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#1e293b'),
        alignment=TA_CENTER
    )

    story = []

    # 1. Cabeçalho do Relatório
    story.append(Paragraph("RELATÓRIO GERAL DE AGRICULTORES FAMILIARES CADASTRADOS", style_title))
    story.append(Paragraph("PNAE - Programa Nacional de Alimentação Escolar / Mapeamento de Fornecedores", style_sub))
    story.append(Spacer(1, 0.3*cm))

    # 2. Resumo Informativo
    data_emissao_str = obter_data_hora_br_str()
    total_cadastrados = len(agricultores)
    total_ativos = sum(1 for a in agricultores if getattr(a, 'status', 'Ativo') == 'Ativo')

    summary_html = (
        f"<b>Data de Emissão:</b> {data_emissao_str} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Total de Agricultores Cadastrados:</b> {total_cadastrados} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Status Ativo:</b> {total_ativos}"
    )
    summary_style = ParagraphStyle('Summary', fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor('#334155'))
    summary_table = Table([[Paragraph(summary_html, summary_style)]], colWidths=[26.7*cm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f1f5f9')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.4*cm))

    # 3. Tabela Principal de Fornecedores
    table_headers = [
        Paragraph("#", style_th),
        Paragraph("Nome / Razão Social", style_th),
        Paragraph("CPF / CNPJ", style_th),
        Paragraph("Telefone / Contato", style_th),
        Paragraph("Tipo de Fornecedor", style_th),
        Paragraph("Local de Produção", style_th),
        Paragraph("DAP/CAF / Status", style_th)
    ]

    table_data = [table_headers]

    for idx, a in enumerate(agricultores, 1):
        nome_txt = a.razao_social or 'Não Informado'
        if getattr(a, 'nome_fantasia', None):
            nome_txt += f"<br/><font size=7 color='#64748b'>({a.nome_fantasia})</font>"

        cpf_cnpj_txt = a.cpf_cnpj or '--'
        telefone_txt = getattr(a, 'telefone', None) or '--'
        tipo_txt = a.tipo_fornecedor or 'Individual'

        locais = []
        if getattr(a, 'comunidade', None):
            locais.append(f"Comunidade: {a.comunidade}")
        if getattr(a, 'endereco_completo', None):
            locais.append(a.endereco_completo)
        if getattr(a, 'zona', None):
            locais.append(f"Zona: {a.zona}")

        local_txt = "<br/>".join(locais) if locais else "Não especificado"

        dap_num = getattr(a, 'dap_caf_numero', None)
        dap_txt = f"DAP/CAF: {dap_num}" if dap_num else "DAP/CAF: --"
        st = getattr(a, 'status', 'Ativo') or 'Ativo'
        status_txt = f"<br/><font color='#166534'><b>Status: {st}</b></font>"
        dap_status_html = f"{dap_txt}{status_txt}"

        table_data.append([
            Paragraph(str(idx), style_td_center),
            Paragraph(nome_txt, style_td_bold),
            Paragraph(cpf_cnpj_txt, style_td_center),
            Paragraph(telefone_txt, style_td_center),
            Paragraph(tipo_txt, style_td_center),
            Paragraph(local_txt, style_td),
            Paragraph(dap_status_html, style_td_center)
        ])

    if not agricultores:
        table_data.append([
            Paragraph("--", style_td_center),
            Paragraph("Nenhum agricultor familiar cadastrado.", style_td),
            Paragraph("--", style_td_center),
            Paragraph("--", style_td_center),
            Paragraph("--", style_td_center),
            Paragraph("--", style_td_center),
            Paragraph("--", style_td_center)
        ])

    grid_table = Table(table_data, colWidths=[1.0*cm, 6.0*cm, 3.2*cm, 3.0*cm, 3.5*cm, 6.0*cm, 4.0*cm])
    grid_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')])
    ]))

    story.append(grid_table)

    doc.build(
        story,
        onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Relatório de Agricultores Familiares"),
        onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Relatório de Agricultores Familiares")
    )

    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=relatorio_agricultores_familiares.pdf'
    return response


@merenda_bp.route('/agricultura/fornecedores/novo', methods=['GET', 'POST'])
@login_required
def novo_agricultor():
    if request.method == 'POST':
        try:
            # 1. Cria o objeto Agricultor com os dados do formulário
            novo = AgricultorFamiliar(
                tipo_fornecedor=request.form.get('tipo_fornecedor'),
                razao_social=request.form.get('razao_social'),
                cpf_cnpj=limpar_cpf(request.form.get('cpf_cnpj')),
                dap_caf_numero=request.form.get('dap_caf_numero'),
                zona=request.form.get('zona'),
                # Adicionei campos comuns que geralmente existem no form
                telefone=request.form.get('telefone'),
                endereco_completo=request.form.get('endereco') 
            )
            
            db.session.add(novo)
            db.session.flush() # Importante: Gera o ID do agricultor antes de salvar os documentos

            # 2. Tratamento de Uploads para o Supabase (CORRIGIDO)
            if 'comprovante_residencia' in request.files:
                file = request.files['comprovante_residencia']
                if file and file.filename != '':
                    # Envia para o Supabase
                    url_doc = upload_arquivo_para_nuvem(file, pasta="pnae_documentos")
                    
                    if url_doc:
                        # Cria o registro na tabela de documentos
                        # Nota: Certifique-se que o modelo DocumentoAgricultor tem esses campos
                        novo_doc = DocumentoAgricultor(
                            agricultor_id=novo.id,
                            tipo_documento="Comprovante de Residência",
                            filename=url_doc  # Salva o Link da Nuvem
                        )
                        db.session.add(novo_doc)

            db.session.commit()
            flash('Agricultor cadastrado com sucesso!', 'success')
            return redirect(url_for('merenda.agricultura_dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar: {e}', 'danger')
            
    return render_template('merenda/agricultura/fornecedor_form.html')

@merenda_bp.route('/agricultura/contratos/<int:agricultor_id>/novo', methods=['GET', 'POST'])
@login_required
def novo_contrato_pnae(agricultor_id):
    agricultor = AgricultorFamiliar.query.get_or_404(agricultor_id)
    if request.method == 'POST':
        try:
            contrato = ContratoPNAE(
                agricultor_id=agricultor.id,
                numero_contrato=request.form.get('numero_contrato'),
                data_inicio=datetime.strptime(request.form.get('data_inicio'), '%Y-%m-%d'),
                data_termino=datetime.strptime(request.form.get('data_termino'), '%Y-%m-%d'),
                valor_total=float(request.form.get('valor_total').replace(',', '.'))
            )
            db.session.add(contrato)
            db.session.commit()
            
            # Adicionar Itens do Projeto de Venda
            nomes = request.form.getlist('produto_nome[]')
            qtds = request.form.getlist('produto_qtd[]')
            precos = request.form.getlist('produto_preco[]')
            
            for i in range(len(nomes)):
                item = ItemProjetoVenda(
                    contrato=contrato,
                    nome_produto=nomes[i],
                    quantidade_total=float(qtds[i]),
                    preco_unitario=float(precos[i])
                )
                db.session.add(item)
            
            db.session.commit()
            flash('Contrato e Projeto de Venda cadastrados!', 'success')
            return redirect(url_for('merenda.agricultura_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro: {e}', 'danger')
            
    return render_template('merenda/agricultura/contrato_form.html', agricultor=agricultor)

@merenda_bp.route('/agricultura/contratos/<int:contrato_id>/gerenciar', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def gerenciar_contrato_pnae(contrato_id):
    contrato = ContratoPNAE.query.get_or_404(contrato_id)
    
    # 1. Se o formulário do modal for enviado, a lógica de registro deve vir aqui
    # (Se você já tiver uma rota separada para registrar_entrega_pnae, 
    # você pode remover o 'POST' e a lógica de processamento desta rota).
    
    # 2. Busca as escolas ativas para o select do modal
    escolas_ativas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    
    # 3. Lógica para calcular o saldo de cada item
    entregue_por_produto = {} 
    
    for entrega in contrato.entregas:
        if entrega.status == 'Aprovado' and entrega.itens_json:
            try:
                itens_entrega = json.loads(entrega.itens_json)
                for item in itens_entrega:
                    prod_nome = item['nome_produto']
                    # Garante que a quantidade seja tratada como float
                    qtd = float(str(item.get('quantidade', 0)).replace(',', '.'))
                    
                    if prod_nome in entregue_por_produto:
                        entregue_por_produto[prod_nome] += qtd
                    else:
                        entregue_por_produto[prod_nome] = qtd
            except (ValueError, TypeError, KeyError):
                continue # Pula itens com erro de formato

    return render_template(
        'merenda/agricultura/contrato_gerenciar.html', 
        contrato=contrato, 
        entregue_por_produto=entregue_por_produto,
        escolas_ativas=escolas_ativas
    )

@merenda_bp.route('/agricultura/contratos/<int:contrato_id>/nova-entrega', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def registrar_entrega_pnae(contrato_id):
    contrato = ContratoPNAE.query.get_or_404(contrato_id)
    
    try:
        # 1. Captura Dados do Formulário
        # Importante: datetime.strptime cria um objeto naive (sem fuso). 
        # Vamos adicionar o fuso de Brasília para garantir consistência.
        import pytz
        fuso_br = pytz.timezone('America/Sao_Paulo')
        
        data_input = request.form.get('data_entrega')
        data_entrega = datetime.strptime(data_input, '%Y-%m-%d').date()
        
        # Se precisar gravar o horário exato da entrega em Brasília:
        data_hora_entrega = fuso_br.localize(datetime.combine(data_entrega, datetime.now().time()))
        
        nota_fiscal = request.form.get('numero_nota_fiscal')
        escola_id = request.form.get('escola_id', type=int)
        
        # 2. Tratamento de Upload da Nota Fiscal (Supabase)
        link_nf = None
        file = request.files.get('arquivo_nf')
        if file and file.filename != '':
            url_gerada = upload_arquivo_para_nuvem(file, pasta="pnae_notas")
            if url_gerada:
                link_nf = url_gerada
            else:
                flash('Atenção: Falha ao salvar o anexo na nuvem.', 'warning')

        # 3. Processamento dos Itens da Entrega
        item_ids = request.form.getlist('item_id[]')
        qtds = request.form.getlist('qtd_entregue[]')
        
        lista_itens_json = []
        valor_total_entrega = 0.0
        
        for i, item_id in enumerate(item_ids):
            qtd = float(qtds[i].replace(',', '.')) if qtds[i] else 0.0
            
            if qtd > 0:
                item_contrato = ItemProjetoVenda.query.get(item_id)
                valor_item = qtd * item_contrato.preco_unitario
                
                lista_itens_json.append({
                    'item_id': item_contrato.id,
                    'nome_produto': item_contrato.nome_produto,
                    'unidade_medida': item_contrato.unidade_medida or 'UN',
                    'quantidade': qtd,
                    'preco_unitario': item_contrato.preco_unitario,
                    'valor_total': valor_item
                })
                
                valor_total_entrega += valor_item

                # --- INTEGRAÇÃO AUTOMÁTICA COM ESTOQUE ---
                produto_estoque = ProdutoMerenda.query.filter_by(nome=item_contrato.nome_produto).first()
                
                if not produto_estoque:
                    produto_estoque = ProdutoMerenda(
                        nome=item_contrato.nome_produto,
                        unidade_medida=item_contrato.unidade_medida or 'un',
                        categoria='Agricultura Familiar',
                        estoque_atual=0.0
                    )
                    db.session.add(produto_estoque)
                    db.session.flush()

                produto_estoque.estoque_atual += qtd
                
                # Registra a movimentação de entrada no estoque
                movimento = EstoqueMovimento(
                    produto_id=produto_estoque.id,
                    tipo='Entrada',
                    quantidade=qtd,
                    data_movimento=data_hora_entrega, # Usa o horário com fuso BR
                    fornecedor=f"PNAE: {contrato.agricultor.razao_social}",
                    lote=f"CONT-{contrato.numero_contrato}",
                    usuario_responsavel=session.get('username')
                )
                db.session.add(movimento)

        if not lista_itens_json:
            flash('Informe a quantidade de pelo menos um item.', 'warning')
            return redirect(url_for('merenda.gerenciar_contrato_pnae', contrato_id=contrato.id))

        # 4. Salva o registro da Entrega
        nova_entrega = EntregaPNAE(
            contrato_id=contrato.id,
            escola_id=escola_id,
            data_entrega=data_entrega,
            numero_nota_fiscal=nota_fiscal,
            recibo_filename=link_nf,
            responsavel_recebimento=session.get('username'),
            status='Aprovado',
            valor_total=valor_total_entrega,
            itens_json=json.dumps(lista_itens_json)
        )
        
        db.session.add(nova_entrega)
        db.session.commit()
        
        registrar_log(f'Registrou entrega PNAE #{nova_entrega.id} do fornecedor {contrato.agricultor.razao_social}.')
        flash('Entrega registrada, estoque atualizado e anexo salvo!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao registrar entrega: {str(e)}', 'danger')
        
    return redirect(url_for('merenda.gerenciar_contrato_pnae', contrato_id=contrato.id))

@merenda_bp.route('/agricultura/contratos/<int:contrato_id>/pdf')
@login_required
def pdf_contrato_pnae(contrato_id):
    from utils import cabecalho_e_rodape # Importa seu cabeçalho padrão
    contrato = ContratoPNAE.query.get_or_404(contrato_id)
    agricultor = contrato.agricultor
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, 
                            rightMargin=2*cm, leftMargin=2*cm, 
                            topMargin=3*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    style_titulo = styles['Heading1']
    style_titulo.alignment = 1 # Centralizado
    style_normal = styles['BodyText']
    style_normal.alignment = 4 # Justificado
    
    story = []
    
    # Título
    story.append(Paragraph("PROJETO DE VENDA DE GÊNEROS ALIMENTÍCIOS DA AGRICULTURA FAMILIAR", style_titulo))
    story.append(Paragraph(f"(PNAE - Chamada Pública {contrato.chamada_publica or '____/____'})", style_titulo))
    story.append(Spacer(1, 1*cm))
    
    # Dados do Fornecedor
    texto_fornecedor = f"""
    <b>1. IDENTIFICAÇÃO DO FORNECEDOR</b><br/><br/>
    <b>Nome/Razão Social:</b> {agricultor.razao_social}<br/>
    <b>CPF/CNPJ:</b> {agricultor.cpf_cnpj} &nbsp;&nbsp;&nbsp; <b>DAP/CAF:</b> {agricultor.dap_caf_numero or 'Não informado'}<br/>
    <b>Endereço:</b> {agricultor.endereco_completo or 'Não informado'} - {agricultor.zona}<br/>
    <b>Telefone:</b> {agricultor.telefone or ''}
    """
    story.append(Paragraph(texto_fornecedor, style_normal))
    story.append(Spacer(1, 0.5*cm))
    
    # Dados do Contrato
    texto_contrato = f"""
    <b>2. DADOS DA CONTRATAÇÃO</b><br/><br/>
    <b>Contrato Nº:</b> {contrato.numero_contrato}<br/>
    <b>Vigência:</b> {contrato.data_inicio.strftime('%d/%m/%Y')} a {contrato.data_termino.strftime('%d/%m/%Y')}<br/>
    <b>Valor Total Estimado:</b> {currency_filter_br(contrato.valor_total)}
    """
    story.append(Paragraph(texto_contrato, style_normal))
    story.append(Spacer(1, 0.5*cm))
    
    # Tabela de Itens
    story.append(Paragraph("<b>3. RELAÇÃO DE PRODUTOS</b>", style_normal))
    story.append(Spacer(1, 0.2*cm))
    
    # Cabeçalho da Tabela
    dados_tabela = [['Produto', 'Unid.', 'Qtd.', 'Preço Unit.', 'Total']]
    
    for item in contrato.itens:
        dados_tabela.append([
            item.nome_produto,
            item.unidade_medida,
            f"{item.quantidade_total:.2f}".replace('.', ','),
            currency_filter_br(item.preco_unitario),
            currency_filter_br(item.quantidade_total * item.preco_unitario)
        ])
    
    # Estilo da Tabela
    t = Table(dados_tabela, colWidths=[8*cm, 2*cm, 2*cm, 2.5*cm, 2.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e0e0e0')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'), # Alinha nomes dos produtos à esquerda
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(t)
    story.append(Spacer(1, 2*cm))
    
    # Assinaturas
    story.append(Paragraph("_____________________________________________", style_titulo))
    story.append(Paragraph("Gestor(a) do PNAE", style_titulo))
    story.append(Spacer(1, 1*cm))
    
    story.append(Paragraph("_____________________________________________", style_titulo))
    story.append(Paragraph(f"{agricultor.razao_social}", style_titulo))
    story.append(Paragraph("Agricultor(a) Familiar", style_titulo))
    
    # Gera o PDF
    doc.build(story, onFirstPage=lambda canvas, doc: cabecalho_e_rodape(canvas, doc), 
                     onLaterPages=lambda canvas, doc: cabecalho_e_rodape(canvas, doc))
    
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Contrato_PNAE_{contrato.numero_contrato}.pdf'
    
    return response


@merenda_bp.route('/agricultura/item/<int:item_id>/regularizar', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def regularizar_saldo_item(item_id):
    # 1. Busca o item específico do projeto de venda ligado ao contrato
    item_contrato = ItemProjetoVenda.query.get_or_404(item_id)
    
    try:
        # 2. Captura os dados vindos do modal de regularização
        tipo_ajuste = request.form.get('tipo_ajuste')
        quantidade_ajuste = float(request.form.get('quantidade_ajuste', '0').replace(',', '.'))
        documento_ref = request.form.get('documento_ref')
        justificativa = request.form.get('justificativa')

        if quantidade_ajuste <= 0:
            flash('A quantidade de ajuste deve ser maior que zero.', 'danger')
            return redirect(url_for('merenda.gerenciar_contrato_pnae', contrato_id=item_contrato.contrato_id))

        # 3. Executa a regra de negócio baseada no tipo de ajuste escolhido
        if tipo_ajuste == 'ADITIVO':
            # Soma a quantidade aditivada diretamente no saldo contratado do item
            item_contrato.quantidade_total += quantidade_ajuste
            
            # Atualiza proporcionalmente o valor total estimado do contrato pai
            valor_adicionado = quantidade_ajuste * item_contrato.preco_unitario
            item_contrato.contrato.valor_total += valor_adicionado
            
            msg_sucesso = f'Saldo de "{item_contrato.nome_produto}" aditivado em {quantidade_ajuste} unidades com sucesso!'
        
        elif tipo_ajuste == 'REMANEJAMENTO':
            # Se futuramente desejar automatizar a retirada de um item para outro, a lógica entra aqui.
            # Por enquanto, pode apenas reajustar a meta superior do item alvo
            item_contrato.quantidade_total += quantidade_ajuste
            msg_sucesso = f'Saldo de "{item_contrato.nome_produto}" remanejado e regularizado com sucesso!'

        # 4. Grava a ação no log de auditoria do sistema
        registrar_log(f"Regularizou saldo do item ID {item_id} ({item_contrato.nome_produto}). Tipo: {tipo_ajuste}. Qtd: {quantidade_ajuste}. Ref: {documento_ref}. Justificativa: {justificativa}")
        
        # 5. Salva tudo de forma definitiva no banco de dados
        db.session.commit()
        flash(msg_sucesso, 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao processar regularização de saldo: {str(e)}', 'danger')
        
    # 6. Redireciona o usuário de volta para a tela de gerenciamento de saldos
    return redirect(url_for('merenda.gerenciar_contrato_pnae', contrato_id=item_contrato.contrato_id))

@merenda_bp.route('/agricultura/entrega/<int:entrega_id>/termo-pdf')
@login_required
@role_required('Merenda Escolar', 'admin')
def pdf_termo_recebimento_pnae(entrega_id):
    # Captura o parâmetro de exibição (padrão 'true' se não informado)
    exibir_valor = request.args.get('exibir_valor', 'true').lower() == 'true'
    
    # 1. Busca os dados da entrega e relações
    entrega = EntregaPNAE.query.get_or_404(entrega_id)
    contrato = entrega.contrato
    agricultor = contrato.agricultor
    
    data_entrega_formatada = entrega.data_entrega.strftime('%d/%m/%Y')
    escola_destino = Escola.query.get(entrega.escola_id) if entrega.escola_id else None
    nome_escola = escola_destino.nome if escola_destino else "Unidade Escolar não informada"
    
    # Nome do Diretor da Escola
    nome_diretor = "Diretor(a) Escolar"
    if escola_destino:
        if escola_destino.diretor and escola_destino.diretor.nome:
            nome_diretor = escola_destino.diretor.nome
        elif escola_destino.diretor_responsavel:
            nome_diretor = escola_destino.diretor_responsavel

    # 2. Configuração do Buffer e Documento A4 com Margens ajustadas para caber exatamente em 1 página
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4, 
        rightMargin=1.5*cm, 
        leftMargin=1.5*cm, 
        topMargin=2.6*cm, 
        bottomMargin=1.0*cm
    )
    
    styles = getSampleStyleSheet()
    style_titulo = ParagraphStyle('Titulo', parent=styles['Heading1'], alignment=1, fontSize=11, spaceAfter=4, fontName='Helvetica-Bold')
    style_normal = ParagraphStyle('Normal', parent=styles['BodyText'], alignment=4, fontSize=9, leading=12)
    style_ass_nome = ParagraphStyle('AssNome', parent=styles['Normal'], alignment=1, fontSize=8, leading=10, fontName='Helvetica-Bold')
    style_ass_cargo = ParagraphStyle('AssCargo', parent=styles['Normal'], alignment=1, fontSize=7.5, leading=9, textColor=colors.HexColor('#444444'))

    # --- FUNÇÃO GERADORA DE VIA (LAYOUT TRADICIONAL) ---
    def gerar_conteudo_via(titulo_via):
        via = []
        via.append(Paragraph(f"TERMO DE RECEBIMENTO - {titulo_via}", style_titulo))
        via.append(Spacer(1, 0.15*cm))
        
        texto_intro = f"""
        Atesto o recebimento em <b>{data_entrega_formatada}</b>, pelo fornecedor <b>{agricultor.razao_social}</b>, 
        referente ao Contrato <b>{contrato.numero_contrato}</b>, destinado à <b>{nome_escola}</b>, os itens abaixo:
        """
        via.append(Paragraph(texto_intro, style_normal))
        via.append(Spacer(1, 0.2*cm))
        
        # Tabela de Produtos
        cabecalho = ['Produto', 'kg', 'Qtd.']
        col_widths = [10.0*cm, 2.5*cm, 3.5*cm]
        if exibir_valor:
            cabecalho = ['Produto', 'kg', 'Qtd.', 'Valor Total']
            col_widths = [8.0*cm, 2.0*cm, 3.0*cm, 3.0*cm]
            
        dados_tabela = [cabecalho]
        
        if entrega.itens_json:
            try:
                itens = json.loads(entrega.itens_json)
            except Exception:
                itens = []
                
            for item in itens:
                nome_prod = item.get('nome_produto', 'N/A').upper()
                qtd = item.get('quantidade', 0)
                qtd_str = f"{qtd:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if isinstance(qtd, (int, float)) else str(qtd)
                
                # Unidade de medida real
                unid = item.get('unidade_medida')
                if not unid and item.get('item_id'):
                    item_pv = ItemProjetoVenda.query.get(item.get('item_id'))
                    if item_pv:
                        unid = item_pv.unidade_medida
                if not unid or str(unid).upper() in ['UN', 'UNID', 'UNID.', 'UNIDADES']:
                    unid = "KG"

                linha = [nome_prod, str(unid), qtd_str]
                if exibir_valor:
                    v_tot = item.get('valor_total', 0)
                    linha.append(currency_filter_br(v_tot))
                dados_tabela.append(linha)
        
        if exibir_valor:
            dados_tabela.append(['TOTAL', '', '', currency_filter_br(entrega.valor_total)])
            
        t = Table(dados_tabela, colWidths=col_widths)
        ts = [
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('ALIGN', (0,1), (0,-1), 'LEFT'),
            ('TOPPADDING', (0,0), (-1,-1), 2),
            ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ]
        if exibir_valor and len(dados_tabela) > 1:
            ts.append(('SPAN', (0, len(dados_tabela)-1), (2, len(dados_tabela)-1)))
            ts.append(('FONTNAME', (0, len(dados_tabela)-1), (-1, len(dados_tabela)-1), 'Helvetica-Bold'))
        t.setStyle(TableStyle(ts))
        via.append(t)
        
        via.append(Spacer(1, 0.4*cm))
        # Apenas DUAS assinaturas: Diretor Escolar e Agricultor Familiar
        t_ass = Table([
            ["_____________________________________________", "_____________________________________________"],
            [Paragraph(f"<b>{nome_diretor}</b>", style_ass_nome), Paragraph(f"<b>{agricultor.razao_social}</b>", style_ass_nome)],
            [Paragraph("Diretor(a) / Gestor(a) Escolar", style_ass_cargo), Paragraph("Agricultor(a) Familiar", style_ass_cargo)]
        ], colWidths=[8.5*cm, 8.5*cm])
        t_ass.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('TOPPADDING', (0,0), (-1,-1), 1),
            ('BOTTOMPADDING', (0,0), (-1,-1), 1),
        ]))
        via.append(t_ass)
        return via

    # --- MONTAGEM DO STORY ---
    story = []
    story.extend(gerar_conteudo_via("1ª VIA (ESCOLA)"))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("----------------------------------------------------------------------------------------------------------------------------------", styles['Normal']))
    story.append(Spacer(1, 0.3*cm))
    story.extend(gerar_conteudo_via("2ª VIA (FORNECEDOR)"))
    
    # 8. Geração Final
    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape(c, d), 
                     onLaterPages=lambda c, d: cabecalho_e_rodape(c, d))
    
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Termo_Recebimento_{entrega.id}.pdf'
    
    return response

# --- GESTÃO DE RELATÓRIOS TÉCNICOS E OFÍCIOS ----

@merenda_bp.route('/relatorios/tecnicos', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def relatorios_tecnicos():
    if request.method == 'POST':
        try:
            # 1. Cria o objeto do Relatório
            novo_doc = RelatorioTecnico(
                tipo_documento=request.form.get('tipo_documento'),
                numero_documento=request.form.get('numero_documento'),
                data_emissao=datetime.strptime(request.form.get('data_emissao'), '%Y-%m-%d').date(),
                local_emissao=request.form.get('local_emissao'),
                vocativo=request.form.get('vocativo'),
                destinatario_nome=request.form.get('destinatario_nome'),
                destinatario_cargo=request.form.get('destinatario_cargo'),
                assunto=request.form.get('assunto'),
                corpo_texto=request.form.get('corpo_texto'),
                fecho=request.form.get('fecho'),
                responsavel_assinatura=request.form.get('responsavel_assinatura')
            )
            
            db.session.add(novo_doc)
            db.session.flush() # Gera o ID para usar nos anexos

            # 2. Upload de Anexos para o Supabase
            arquivos = request.files.getlist('anexos')
            for file in arquivos:
                if file and file.filename != '':
                    # Envia para a pasta 'merenda_documentos' no Supabase
                    url_anexo = upload_arquivo_para_nuvem(file, pasta="merenda_documentos")
                    
                    if url_anexo:
                        anexo = RelatorioAnexo(
                            relatorio_id=novo_doc.id,
                            filename=url_anexo, # Salva o link da nuvem
                            nome_original=secure_filename(file.filename),
                            descricao="Anexo do Documento"
                        )
                        db.session.add(anexo)

            db.session.commit()
            flash(f'{novo_doc.tipo_documento} registrado com sucesso!', 'success')
            return redirect(url_for('merenda.relatorios_tecnicos'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar documento: {e}', 'danger')

    # Geração automática do número sugerido (Ex: 001/2026)
    ano_atual = datetime.now().year
    documentos = RelatorioTecnico.query.order_by(RelatorioTecnico.data_emissao.desc(), RelatorioTecnico.id.desc()).all()
    
    max_seq = 0
    for doc in documentos:
        if doc.numero_documento and '/' in doc.numero_documento:
            parts = doc.numero_documento.split('/')
            if len(parts) == 2 and parts[1].strip() == str(ano_atual) and parts[0].strip().isdigit():
                max_seq = max(max_seq, int(parts[0].strip()))
    
    numero_sugerido = f"{max_seq + 1:03d}/{ano_atual}"
    
    metricas = {
        'total': len(documentos),
        'relatorios': sum(1 for d in documentos if d.tipo_documento == 'Relatório Técnico'),
        'oficios': sum(1 for d in documentos if d.tipo_documento in ['Ofício', 'Memorando']),
        'ano': sum(1 for d in documentos if d.data_emissao and d.data_emissao.year == ano_atual)
    }

    return render_template('merenda/relatorios_tecnicos.html', 
                           documentos=documentos, 
                           numero_sugerido=numero_sugerido, 
                           metricas=metricas,
                           data_hoje=datetime.now().strftime('%Y-%m-%d'))


@merenda_bp.route('/relatorios/anexo/<int:anexo_id>/download')
@login_required
def download_anexo(anexo_id):
    anexo = RelatorioAnexo.query.get_or_404(anexo_id)
    
    # 1. Verifica se é um link do Supabase (nuvem)
    if anexo.filename and anexo.filename.startswith('http'):
        return redirect(anexo.filename)
        
    # 2. Fallback: Se por acaso não for link (muito difícil agora), exibe erro
    flash('Arquivo não encontrado ou link inválido.', 'danger')
    return redirect(url_for('merenda.relatorios_tecnicos'))


@merenda_bp.route('/relatorios/tecnicos/<int:id>/imprimir')
@login_required
@role_required('Merenda Escolar', 'admin')
def imprimir_relatorio(id):
    doc = RelatorioTecnico.query.get_or_404(id)
    
    # 1. Formatação da Data por Extenso
    meses = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril', 5: 'Maio', 6: 'Junho',
        7: 'Julho', 8: 'Agosto', 9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }
    data_extenso = f"{doc.data_emissao.day} de {meses[doc.data_emissao.month]} de {doc.data_emissao.year}"
    
    # 2. Função Auxiliar para Converter Imagem em Base64
    def get_image_b64(filename):
        # Monta o caminho exato dentro da pasta static/img
        filepath = os.path.join(current_app.static_folder, 'img', filename)
        
        # Verifica se existe para não dar erro
        if not os.path.exists(filepath):
            print(f"ERRO: Imagem não encontrada no caminho: {filepath}")
            return None
            
        with open(filepath, "rb") as image_file:
            # Lê o arquivo e converte para string base64
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        return encoded_string

    # 3. Carrega as imagens
    timbre_b64 = get_image_b64('timbre.JPG') 
    marcadagua_b64 = get_image_b64('marcadagua.png')
    
    return render_template('merenda/relatorio_print.html', 
                           doc=doc, 
                           data_extenso=data_extenso,
                           timbre_b64=timbre_b64,
                           marcadagua_b64=marcadagua_b64)


@merenda_bp.route('/relatorios/tecnicos/<int:id>/excluir')
@login_required
@role_required('Merenda Escolar', 'admin')
def excluir_relatorio(id):
    doc = RelatorioTecnico.query.get_or_404(id)
    try:
        # Nota: Os arquivos no Supabase permanecem, ou você pode implementar a deleção via API se desejar.
        # Aqui removemos apenas a referência no banco.
        db.session.delete(doc)
        db.session.commit()
        flash('Documento excluído com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir: {e}', 'danger')
        
    return redirect(url_for('merenda.relatorios_tecnicos'))

@merenda_bp.route('/pedidos-empresa/novo', methods=['POST'])
@login_required
def novo_pedido_empresa():
    # Captura as listas enviadas pelo formulário
    produtos_ids = request.form.getlist('produto_id[]')
    quantidades = request.form.getlist('quantidade[]')
    especificacoes = request.form.getlist('especificacao[]') # Nova lista capturada
    
    # Cria o cabeçalho do pedido (ID gerado automaticamente pelo DB)
    pedido = PedidoEmpresa(
        solicitante=session.get('username'), 
        status='Rascunho',
        data_pedido=datetime.utcnow()
    )
    
    try:
        db.session.add(pedido)
        
        # O zip combina as 3 listas para iterar sobre elas simultaneamente
        for p_id, qtd, spec in zip(produtos_ids, quantidades, especificacoes):
            # Tratamento básico para aceitar vírgula ou ponto
            qtd_formatada = qtd.replace(',', '.') if isinstance(qtd, str) else qtd
            
            if qtd_formatada and float(qtd_formatada) > 0:
                item = PedidoEmpresaItem(
                    pedido=pedido, 
                    produto_id=int(p_id), 
                    quantidade=float(qtd_formatada),
                    especificacao=spec # Salva a especificação/marca/gramatura
                )
                db.session.add(item)
        
        db.session.commit()
        flash('Solicitação salva como rascunho com sucesso!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao salvar solicitação: {str(e)}', 'danger')
    
    return redirect(url_for('merenda.dashboard'))

@merenda_bp.route('/pedidos-empresa/excluir/<int:id>')
@login_required
def excluir_pedido_empresa(id):
    pedido = PedidoEmpresa.query.get_or_404(id)
    if pedido.status == 'Enviado':
        flash('Pedidos já enviados ao fornecedor não podem ser excluídos!', 'danger')
    else:
        db.session.delete(pedido)
        db.session.commit()
        flash('Pedido excluído com sucesso.', 'success')
    return redirect(url_for('merenda.dashboard'))

@merenda_bp.route('/pedidos-empresa/<int:id>/pdf')
@login_required
@role_required('Merenda Escolar', 'admin')
def gerar_pdf_pedido(id):
    # 1. Busca o pedido e os itens associados no banco de dados
    pedido = PedidoEmpresa.query.get_or_404(id)
    
    # 2. Configuração do Buffer e do Documento PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, 
                            rightMargin=1.5*cm, leftMargin=1.5*cm, 
                            topMargin=3*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    style_titulo = styles['Heading1']
    style_titulo.alignment = 1 # Centralizado
    style_normal = styles['BodyText']
    
    story = []
    
    # Título do Documento
    story.append(Paragraph(f"SOLICITAÇÃO DE COMPRA Nº {pedido.id}/{pedido.data_pedido.year}", style_titulo))
    story.append(Spacer(1, 0.5*cm))
    
    # Informações do Pedido
    texto_info = f"""
    <b>Data da Solicitação:</b> {pedido.data_pedido.strftime('%d/%m/%Y %H:%M')}<br/>
    <b>Solicitante:</b> {pedido.solicitante}<br/>
    <b>Status:</b> {pedido.status}
    """
    story.append(Paragraph(texto_info, style_normal))
    story.append(Spacer(1, 0.8*cm))
    
    # 3. Tabela de Itens (Incluindo a nova coluna de Especificação e Lógica de Conversão)
    dados_tabela = [['Produto', 'Especificação / Marca', 'Unid.', 'Qtd. Solicitada']]
    
    for item in pedido.itens:
        # Garante que um traço seja exibido caso a especificação esteja vazia
        especificacao = item.especificacao if item.especificacao else "-"
        nome_exibicao = item.produto.nome.upper()
        
        # --- LÓGICA DE EXIBIÇÃO DA CONVERSÃO NO PDF ---
        # Se o produto tiver fator de conversão (fardo/caixa), detalha o total em unidades base
        fator = item.produto.fator_conversao or 1.0
        if fator > 1:
            total_unidades = item.quantidade * fator
            # Determina a unidade de destino (ex: KG para fardos de arroz, UNID para caixas de biscoito)
            unidade_alvo = "unid/kg" 
            detalhe_conversao = f"<br/><font color='gray' size='8'>(Total: {total_unidades:.2f} {unidade_alvo})</font>"
            nome_exibicao += detalhe_conversao

        dados_tabela.append([
            Paragraph(nome_exibicao, style_normal), # Usamos Paragraph para aceitar a quebra de linha <br/>
            especificacao,
            item.produto.unidade_medida,
            f"{item.quantidade:.2f}".replace('.', ',')
        ])
    
    # Definição das larguras das colunas: Produto (7cm), Especificação (6cm), Unidade (2cm), Qtd (3cm)
    t = Table(dados_tabela, colWidths=[7*cm, 6*cm, 2*cm, 3*cm])
    
    # Estilização da Tabela mantendo o padrão verde escuro
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 0), (1, -1), 'LEFT'), # Alinha Produto e Especificação à esquerda
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9), 
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    story.append(t)
    story.append(Spacer(1, 2.5*cm))
    
    # 4. Campo de Assinatura
    story.append(Paragraph("________________________________________________", style_titulo))
    story.append(Paragraph("Responsável pela Secretaria de Educação", style_titulo))
    
    # Geração Final com o timbre da prefeitura (Cabeçalho e Rodapé Moderno)
    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Solicitação de Compra"), 
                     onLaterPages=lambda c, d: cabecalho_e_rodape_moderno(c, d, "Solicitação de Compra"))
    
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Solicitacao_Compra_{pedido.id}.pdf'
    
    return response

@merenda_bp.route('/pedidos-empresa/enviar/<int:id>')
@login_required
@role_required('Merenda Escolar', 'admin')
def enviar_pedido_fornecedor(id):
    pedido = PedidoEmpresa.query.get_or_404(id)
    try:
        pedido.status = 'Enviado'
        db.session.commit()
        registrar_log(f'Enviou pedido à empresa #{id} para o fornecedor.')
        flash(f'Pedido #{id} enviado com sucesso! O rascunho foi bloqueado para alterações.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao enviar pedido: {e}', 'danger')
    return redirect(url_for('merenda.dashboard'))

@merenda_bp.route('/pedidos-empresa/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def editar_pedido_empresa(id):
    pedido = PedidoEmpresa.query.get_or_404(id)
    
    # Segurança: Só permite editar se for rascunho
    if pedido.status != 'Rascunho':
        flash('Apenas rascunhos podem ser editados.', 'warning')
        return redirect(url_for('merenda.dashboard'))

    if request.method == 'POST':
        try:
            # Limpa os itens antigos para reinserir os novos (mais simples que dar update um por um)
            PedidoEmpresaItem.query.filter_by(pedido_id=id).delete()
            
            produtos_ids = request.form.getlist('produto_id[]')
            quantidades = request.form.getlist('quantidade[]')
            especificacoes = request.form.getlist('especificacao[]')

            for p_id, qtd, spec in zip(produtos_ids, quantidades, especificacoes):
                qtd_formatada = qtd.replace(',', '.') if qtd else "0"
                if float(qtd_formatada) > 0:
                    novo_item = PedidoEmpresaItem(
                        pedido_id=id,
                        produto_id=int(p_id),
                        quantidade=float(qtd_formatada),
                        especificacao=spec
                    )
                    db.session.add(novo_item)
            
            db.session.commit()
            flash('Pedido atualizado com sucesso!', 'success')
            return redirect(url_for('merenda.dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar: {str(e)}', 'danger')

    # Para o GET: precisamos listar todos os produtos e marcar os que já estão no pedido
    produtos = ProdutoMerenda.query.order_by(ProdutoMerenda.nome).all()
    # Cria um dicionário {produto_id: {qtd: x, spec: y}} para facilitar o preenchimento no HTML
    itens_atuais = {item.produto_id: item for item in pedido.itens}
    
    return render_template('merenda/pedido_empresa_edit.html', 
                           pedido=pedido, 
                           produtos=produtos, 
                           itens_atuais=itens_atuais)
@merenda_bp.route('/produtos/excluir/<int:produto_id>', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def excluir_produto(produto_id):
    produto = ProdutoMerenda.query.get_or_404(produto_id)
    
    # Verifica se o produto tem histórico (movimentações ou itens de solicitação)
    tem_movimentacao = EstoqueMovimento.query.filter_by(produto_id=produto_id).first()
    tem_solicitacao = SolicitacaoItem.query.filter_by(produto_id=produto_id).first()
    
    if tem_movimentacao or tem_solicitacao:
        flash(f'Não é possível excluir "{produto.nome}" porque ele possui histórico de movimentação ou solicitações vinculadas. Tente apenas editar ou zerar o estoque.', 'danger')
        return redirect(url_for('merenda.listar_produtos'))
    
    try:
        db.session.delete(produto)
        db.session.commit()
        registrar_log(f'Excluiu o produto: "{produto.nome}" (ID: {produto_id}).')
        flash('Produto excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir produto: {e}', 'danger')
        
    return redirect(url_for('merenda.listar_produtos'))    

@merenda_bp.route('/ficha/enviar/<int:id>', methods=['POST'])
@merenda_bp.route('/fichas/enviar/<int:id>', methods=['POST'])
@login_required
def enviar_para_escola(id):
    ficha = FichaDistribuicao.query.get_or_404(id)
    
    if ficha.status == 'Enviado':
        flash('Esta ficha já foi enviada para a escola e a baixa no estoque já foi realizada.', 'warning')
        return redirect(url_for('merenda.listar_fichas'))

    try:
        itens_processados = 0
        for item in ficha.itens:
            if not item.produto or (item.quantidade or 0) <= 0:
                continue
            
            produto = item.produto
            qtd_base = float(item.quantidade)

            # 1. Subtrai a quantidade base do estoque atual do produto
            produto.estoque_atual = (produto.estoque_atual or 0.0) - qtd_base

            # 2. Registra o movimento de saída com auditoria de embalagem
            movimento = EstoqueMovimento(
                produto_id=item.produto_id,
                quantidade=qtd_base,
                tipo='Saída Escola',
                unidade_movimento=item.unidade_movimento or produto.unidade_consumo or produto.unidade_medida or 'UNID',
                quantidade_embalagem=item.quantidade_embalagem if (item.quantidade_embalagem is not None and item.quantidade_embalagem > 0) else qtd_base,
                fator_utilizado=item.fator_utilizado or 1.0,
                escola_id=ficha.escola_id,
                origem=f'Ficha de Distribuição #{ficha.id} - {ficha.mes_referencia}/{ficha.ano_referencia}',
                observacao=f'Baixa automática referente à Ficha de Distribuição #{ficha.id}',
                data_movimento=datetime.now(),
                usuario_responsavel=session.get('username', 'Sistema')
            )
            db.session.add(movimento)
            itens_processados += 1

        ficha.status = 'Enviado'
        db.session.commit()

        escola_nome = ficha.escola.nome if ficha.escola else 'Escola'
        registrar_log(f"Enviou Ficha de Distribuição #{ficha.id} para {escola_nome} com baixa no estoque ({itens_processados} itens).")
        flash(f'Sucesso! Ficha #{ficha.id} enviada para {escola_nome}. Baixa de estoque realizada com sucesso.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao processar baixa de estoque da ficha #{id}: {str(e)}', 'danger')
        
    return redirect(url_for('merenda.listar_fichas'))

# Alias para compatibilidade de rotas antigas
enviar_alimentos_ficha = enviar_para_escola


@merenda_bp.route('/fichas/pdf/<int:id>')
@login_required
@role_required('Merenda Escolar', 'admin')
def gerar_pdf_ficha(id):
    ficha = FichaDistribuicao.query.get_or_404(id)
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )
    elements = []
    styles = getSampleStyleSheet()

    # 1. Timbre oficial no topo
    basedir = current_app.root_path
    timbre_path = os.path.join(basedir, 'static', 'timbre.jpg')
    if os.path.exists(timbre_path):
        w_img = 18*cm
        h_img = w_img * (372.0 / 2362.0)
        elements.append(Image(timbre_path, width=w_img, height=h_img))
    else:
        style_hdr_fallback = ParagraphStyle('HdrFB', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, alignment=TA_CENTER)
        elements.append(Paragraph("MUNICÍPIO DE VALENÇA DO PIAUÍ", style_hdr_fallback))
        elements.append(Paragraph("SECRETARIA MUNICIPAL DE EDUCAÇÃO", style_hdr_fallback))
        elements.append(Spacer(1, 0.5*cm))

    # Estilos
    style_doc_title = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        alignment=TA_CENTER,
        spaceBefore=18,
        spaceAfter=18
    )
    style_body = ParagraphStyle(
        'BodyText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        alignment=TA_JUSTIFY,
        spaceAfter=18
    )
    style_th = ParagraphStyle(
        'TH',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        alignment=TA_CENTER
    )
    style_td_c = ParagraphStyle(
        'TDC',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11,
        alignment=TA_CENTER
    )
    style_td_l = ParagraphStyle(
        'TDL',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11,
        alignment=TA_LEFT
    )

    # 2. Título do Documento
    mes_ref_str = str(ficha.mes_referencia or '').upper()
    ano_ref_str = str(ficha.ano_referencia or '')
    if ano_ref_str and ano_ref_str not in mes_ref_str:
        mes_ano = f"{mes_ref_str} DE {ano_ref_str}"
    else:
        mes_ano = mes_ref_str
    
    title_text = f"FICHA DE DISTRIBUIÇÃO DE GÊNEROS ALIMENTICIOS {mes_ano}"
    elements.append(Paragraph(title_text, style_doc_title))

    # 3. Parágrafo Declaratório
    tipo_gen = (ficha.tipo_genero or 'PERECÍVEIS OU NÃO PERECÍVEIS').upper()
    body_text = (
        f"Recebi da PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ, por meio da SECRETARIA MUNICIPAL DE EDUCAÇÃO DE VALENÇA DO PIAUÍ, "
        f"os gêneros alimentícios <b>{tipo_gen}</b> abaixo discriminados, por cujo armazenagem, conservação e aplicação me responsabilizo "
        f"conforme as normas estabelecidas pelo PNAE."
    )
    elements.append(Paragraph(body_text, style_body))

    # 4. Tabela de Produtos
    col_hdr_2 = f"ESPECIFICAÇÃO DO PRODUTO<br/>{tipo_gen}"
    table_data = [[
        Paragraph('ITEM', style_th),
        Paragraph(col_hdr_2, style_th),
        Paragraph('UNID.', style_th),
        Paragraph('QT', style_th),
        Paragraph('OBS', style_th),
    ]]

    count = 0
    for item in ficha.itens:
        if not item.produto:
            continue
        count += 1
        unid = item.unidade_movimento or item.produto.unidade_medida or item.produto.unidade_consumo or 'UNID'
        qtd_val = item.quantidade_embalagem if (item.quantidade_embalagem is not None and item.quantidade_embalagem > 0) else item.quantidade
        qtd_fmt = f"{qtd_val:,.2f}".replace('.', ',') if qtd_val else '0,00'
        if qtd_fmt.endswith(',00'):
            qtd_fmt = qtd_fmt[:-3]
        table_data.append([
            Paragraph(str(count), style_td_c),
            Paragraph(item.produto.nome or '', style_td_l),
            Paragraph(unid, style_td_c),
            Paragraph(qtd_fmt, style_td_c),
            Paragraph(item.observacao or '', style_td_l),
        ])

    # Preenche linhas vazias até 14 linhas no total
    total_rows = max(14, count)
    for i in range(count + 1, total_rows + 1):
        table_data.append([
            Paragraph(str(i), style_td_c),
            Paragraph('', style_td_l),
            Paragraph('', style_td_c),
            Paragraph('', style_td_c),
            Paragraph('', style_td_l),
        ])

    item_table = Table(table_data, colWidths=[1.2*cm, 9.8*cm, 1.8*cm, 2.2*cm, 3.0*cm])
    item_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(item_table)

    # 5. Data
    elements.append(Spacer(1, 20))
    style_date = ParagraphStyle('DateText', parent=styles['Normal'], fontName='Helvetica', fontSize=10, alignment=TA_RIGHT)
    dt = ficha.data_emissao or datetime.now()
    meses_pt = {
        1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
        5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
        9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
    }
    date_str = f"Valença do Piauí, {dt.day} de {meses_pt.get(dt.month, '')} de {dt.year}"
    elements.append(Paragraph(date_str, style_date))

    # 6. Assinaturas
    elements.append(Spacer(1, 35))
    style_sig_label = ParagraphStyle('SigLabel', parent=styles['Normal'], fontName='Helvetica', fontSize=9, alignment=TA_LEFT, leading=14)

    sig_data = [
        [Paragraph('_______________________________________', style_sig_label)],
        [Paragraph('Assinatura do(a) Diretor(a)', style_sig_label)],
        [Spacer(1, 25)],
        [Paragraph('_______________________________________', style_sig_label)],
        [Paragraph('Coordenador(a) da Merenda Escolar', style_sig_label)]
    ]
    sig_table = Table(sig_data, colWidths=[10*cm])
    sig_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    elements.append(sig_table)

    doc.build(elements)
    buffer.seek(0)
    
    return make_response(buffer.getvalue(), 200, {
        'Content-Type': 'application/pdf',
        'Content-Disposition': f'inline; filename=Ficha_Distribuição_{id}.pdf'
    })
    
@merenda_bp.route('/fichas')
@login_required
def listar_fichas():
    # Agora que você importou lá em cima, este comando vai funcionar
    fichas = FichaDistribuicao.query.order_by(FichaDistribuicao.id.desc()).all()
    return render_template('merenda/fichas_lista.html', fichas=fichas)  

@merenda_bp.route('/fichas/nova', methods=['GET', 'POST'])
@login_required
def nova_ficha():
    if request.method == 'POST':
        try:
            nova_f = FichaDistribuicao(
                escola_id=request.form.get('escola_id'),
                mes_referencia=request.form.get('mes_referencia'),
                ano_referencia=datetime.now().year,
                tipo_genero=request.form.get('tipo_genero', 'PERECÍVEIS')
            )
            db.session.add(nova_f)
            db.session.flush()

            produtos_ids = request.form.getlist('produto_id[]')
            quantidades = request.form.getlist('quantidade[]')
            tipos_unidade = request.form.getlist('tipo_unidade[]')
            observacoes = request.form.getlist('observacao[]')
            
            for i in range(len(produtos_ids)):
                qtd_val = float(quantidades[i]) if (i < len(quantidades) and quantidades[i]) else 0.0
                if qtd_val > 0:
                    p_id = int(produtos_ids[i])
                    produto = ProdutoMerenda.query.get(p_id)
                    if not produto:
                        continue

                    tipo_un = tipos_unidade[i] if i < len(tipos_unidade) else 'base'
                    fator = float(produto.fator_conversao) if (produto.fator_conversao and produto.fator_conversao > 0) else 1.0

                    if tipo_un == 'master' and fator > 1.0:
                        quantidade_base = qtd_val * fator
                        unidade_mov = produto.unidade_medida or 'CX'
                    else:
                        quantidade_base = qtd_val
                        unidade_mov = produto.unidade_consumo or produto.unidade_medida or 'UNID'

                    obs_val = observacoes[i] if i < len(observacoes) else ''

                    item = FichaDistribuicaoItem(
                        ficha_id=nova_f.id,
                        produto_id=p_id,
                        quantidade=quantidade_base,
                        tipo_unidade=tipo_un,
                        unidade_movimento=unidade_mov,
                        quantidade_embalagem=qtd_val,
                        fator_utilizado=fator,
                        observacao=obs_val
                    )
                    db.session.add(item)
            
            db.session.commit()
            registrar_log(f"Criou Ficha de Distribuição #{nova_f.id} para escola ID {nova_f.escola_id}")
            flash('Ficha de Distribuição criada com sucesso!', 'success')
            return redirect(url_for('merenda.listar_fichas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao criar ficha de distribuição: {str(e)}', 'danger')

    escolas = Escola.query.order_by(Escola.nome.asc()).all()
    # Apenas produtos da Merenda Escolar (exclui Agricultura Familiar)
    produtos = ProdutoMerenda.query.filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None))
    ).order_by(ProdutoMerenda.nome.asc()).all()

    return render_template('merenda/ficha_form.html', escolas=escolas, produtos=produtos, itens_map={}, editando=False)

@merenda_bp.route('/fichas/excluir/<int:id>', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def excluir_ficha(id):
    ficha = FichaDistribuicao.query.get_or_404(id)
    try:
        db.session.delete(ficha)
        db.session.commit()
        registrar_log(f"Excluiu a Ficha de Distribuição #{id}")
        flash('Ficha excluída com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir: {str(e)}', 'danger')
    
    return redirect(url_for('merenda.listar_fichas'))

@merenda_bp.route('/pedido/editar/<int:id>')
def editar_pedido(id):
    pedido = PedidoEmpresa.query.get_or_404(id)
    if pedido.status == 'Entregue':
        flash('Pedidos entregues não podem ser editados.', 'warning')
        return redirect(url_for('merenda.dashboard'))

@merenda_bp.route('/fichas/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def editar_ficha(id):
    ficha = FichaDistribuicao.query.get_or_404(id)
    
    # Bloqueia edição se já tiver sido enviada
    if ficha.status != 'Pendente':
        flash('Esta ficha já foi enviada e não pode mais ser editada.', 'warning')
        return redirect(url_for('merenda.listar_fichas'))

    if request.method == 'POST':
        try:
            # Atualiza campos básicos
            ficha.escola_id = request.form.get('escola_id')
            ficha.mes_referencia = request.form.get('mes_referencia')
            ficha.tipo_genero = request.form.get('tipo_genero')
            
            # Remove itens antigos e reinsere os itens atualizados
            FichaDistribuicaoItem.query.filter_by(ficha_id=ficha.id).delete()
            
            produtos_ids = request.form.getlist('produto_id[]')
            quantidades = request.form.getlist('quantidade[]')
            tipos_unidade = request.form.getlist('tipo_unidade[]')
            observacoes = request.form.getlist('observacao[]')

            for i in range(len(produtos_ids)):
                qtd_val = float(quantidades[i]) if (i < len(quantidades) and quantidades[i]) else 0.0
                if qtd_val > 0:
                    p_id = int(produtos_ids[i])
                    produto = ProdutoMerenda.query.get(p_id)
                    if not produto:
                        continue

                    tipo_un = tipos_unidade[i] if i < len(tipos_unidade) else 'base'
                    fator = float(produto.fator_conversao) if (produto.fator_conversao and produto.fator_conversao > 0) else 1.0

                    if tipo_un == 'master' and fator > 1.0:
                        quantidade_base = qtd_val * fator
                        unidade_mov = produto.unidade_medida or 'CX'
                    else:
                        quantidade_base = qtd_val
                        unidade_mov = produto.unidade_consumo or produto.unidade_medida or 'UNID'

                    obs_val = observacoes[i] if i < len(observacoes) else ''

                    item = FichaDistribuicaoItem(
                        ficha_id=ficha.id,
                        produto_id=p_id,
                        quantidade=quantidade_base,
                        tipo_unidade=tipo_un,
                        unidade_movimento=unidade_mov,
                        quantidade_embalagem=qtd_val,
                        fator_utilizado=fator,
                        observacao=obs_val
                    )
                    db.session.add(item)

            db.session.commit()
            registrar_log(f"Editou a Ficha de Distribuição #{id}")
            flash('Ficha atualizada com sucesso!', 'success')
            return redirect(url_for('merenda.listar_fichas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar: {str(e)}', 'danger')

    escolas = Escola.query.order_by(Escola.nome.asc()).all()
    # Apenas produtos da Merenda Escolar (exclui Agricultura Familiar)
    produtos = ProdutoMerenda.query.filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None))
    ).order_by(ProdutoMerenda.nome.asc()).all()

    # Mapeia quantidades e observações existentes por produto_id
    itens_map = {
        item.produto_id: {
            'quantidade_base': item.quantidade,
            'observacao': item.observacao or '',
            'tipo_unidade': item.tipo_unidade or 'base',
            'quantidade_embalagem': item.quantidade_embalagem if item.quantidade_embalagem is not None else item.quantidade
        }
        for item in ficha.itens
    }
    
    return render_template('merenda/ficha_form.html', 
                           ficha=ficha, 
                           escolas=escolas, 
                           produtos=produtos,
                           itens_map=itens_map,
                           editando=True)

@merenda_bp.route('/solicitacoes/<int:solicitacao_id>/recibo-pdf')
@login_required
@role_required('Merenda Escolar', 'admin')
def pdf_recibo_solicitacao(solicitacao_id):
    solicitacao = SolicitacaoMerenda.query.get_or_404(solicitacao_id)
    escola = solicitacao.escola

    itens_tabela = []
    for item in solicitacao.itens:
        valor_qtd = f"{float(item.quantidade_solicitada):.1f}".replace('.', ',')
        unid = item.produto.unidade_consumo if (item.produto and item.produto.unidade_consumo) else 'UNID'
        itens_tabela.append({
            'produto': item.produto.nome if item.produto else 'Gênero Alimentício',
            'qtd_emb': f"{valor_qtd} {unid}",
            'qtd_real': f"{valor_qtd} {unid}",
            'obs': f"Status: {solicitacao.status}"
        })

    return gerar_pdf_termo_entrega_profissional(
        titulo="TERMO DE ENTREGA - SOLICITAÇÃO ESCOLAR",
        subtitulo="PNAE - Programa Nacional de Alimentação Escolar",
        escola_nome=escola.nome if escola else "Unidade Escolar",
        escola_obj=escola,
        data_emissao=solicitacao.data_entrega or solicitacao.data_solicitacao or datetime.now(),
        responsavel=session.get('username', 'Sistema'),
        itens_tabela=itens_tabela,
        observacao_geral=f"Solicitação ID: #{solicitacao.id} | Status: {solicitacao.status}",
        num_protocolo=f"SOL-{solicitacao.id:06d}"
    )

@merenda_bp.route('/contrato-pnae/excluir-entrega/<int:entrega_id>', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def excluir_entrega(entrega_id):
    entrega = EntregaPNAE.query.get_or_404(entrega_id) # Certifique-se do nome do modelo (EntregaPNAE)
    justificativa = request.form.get('justificativa')
    
    if not justificativa:
        flash('Uma justificativa é obrigatória para excluir uma entrega!', 'danger')
        return redirect(request.referrer)

    try:
        # 1. REVERSÃO DE ESTOQUE:
        # Buscamos os movimentos de estoque criados com o lote desta entrega
        lote_associado = f"CONT-{entrega.contrato.numero_contrato}"
        movimentos = EstoqueMovimento.query.filter_by(lote=lote_associado, data_movimento=datetime.combine(entrega.data_entrega, datetime.min.time())).all()
        
        for mov in movimentos:
            # Subtrai do produto o que foi adicionado na entrega
            produto = ProdutoMerenda.query.get(mov.produto_id)
            if produto:
                produto.estoque_atual -= mov.quantidade
            db.session.delete(mov)

        # 2. Registro de log
        registrar_log(f"Excluiu entrega ID {entrega_id}. Justificativa: {justificativa}")
        
        # 3. Exclui a entrega
        db.session.delete(entrega)
        db.session.commit()
        
        flash('Entrega removida, estoque estornado e saldo atualizado.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir entrega: {str(e)}', 'danger')
    
    return redirect(request.referrer)

@merenda_bp.route('/fichas/clonar/<int:id>', methods=['POST'])
@login_required
def clonar_ficha(id):
    original = FichaDistribuicao.query.get_or_404(id)
    try:
        nova = FichaDistribuicao(
            escola_id=original.escola_id,
            mes_referencia=original.mes_referencia,
            ano_referencia=original.ano_referencia,
            tipo_genero=original.tipo_genero,
            status='Pendente'
        )
        db.session.add(nova)
        db.session.flush()
        
        for item in original.itens:
            novo_item = FichaDistribuicaoItem(ficha_id=nova.id, produto_id=item.produto_id, quantidade=item.quantidade)
            db.session.add(novo_item)
            
        db.session.commit()
        flash('Ficha clonada com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao clonar: {e}', 'danger')
    return redirect(url_for('merenda.listar_fichas'))

@merenda_bp.route('/entrega/clonar/<int:entrega_id>', methods=['POST'])
@login_required
def clonar_entrega(entrega_id):
    original = EntregaPNAE.query.get_or_404(entrega_id)
    try:
        nova_data = datetime.strptime(request.form.get('nova_data'), '%Y-%m-%d').date()
        nova_nf = request.form.get('nova_nf')

        # Cria nova entrega copiando os dados básicos e o JSON de itens
        nova = EntregaPNAE(
            contrato_id=original.contrato_id,
            escola_id=original.escola_id,
            data_entrega=nova_data,
            numero_nota_fiscal=nova_nf,
            valor_total=original.valor_total,
            itens_json=original.itens_json, 
            responsavel_recebimento=session.get('username', 'Admin'),
            status='Aprovado'
        )
        
        db.session.add(nova)
        db.session.commit()
        
        flash('Registro de entrega clonado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao clonar: {str(e)}', 'danger')
        
    return redirect(request.referrer)

@merenda_bp.route('/agricultura/contrato/<int:contrato_id>/valor_mensal')
@login_required
def get_valor_executado_mensal(contrato_id):
    # Pega o mês enviado pelo seletor do HTML
    mes = request.args.get('mes')
    
    # Inicia a busca pelas entregas aprovadas deste contrato
    query = EntregaPNAE.query.filter_by(contrato_id=contrato_id, status='Aprovado')
    
    if mes and mes != 'total':
        # Filtra as entregas onde o mês da data_entrega corresponde ao selecionado
        query = query.filter(func.extract('month', EntregaPNAE.data_entrega) == int(mes))
    
    # Soma o valor_total de todas as entregas filtradas
    total = query.with_entities(func.sum(EntregaPNAE.valor_total)).scalar() or 0.0
    
    # Retorna o valor formatado como moeda brasileira (R$) para o card
    return {"valor": currency_filter_br(total)}

# ==========================================================
# MÓDULO DE CARDÁPIOS PNAE - GERENCIAMENTO E PDF
# ==========================================================

@merenda_bp.route('/cardapios', methods=['GET'])
@login_required
@role_required('Merenda Escolar', 'admin')
def listar_cardapios_pnae():
    """Listagem oficial de todos os cardápios PNAE cadastrados."""
    escola_id = request.args.get('escola_id', type=int)
    
    query = Cardapio.query
    if escola_id:
        query = query.filter_by(escola_id=escola_id)
        
    # Busca os cardápios PNAE ordenando pelos mais recentes
    cardapios = query.order_by(Cardapio.validade_inicio.desc()).all()
    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    
    return render_template(
        'merenda/cardapios_lista.html', 
        cardapios=cardapios, 
        escolas=escolas, 
        escola_id_selecionada=escola_id
    )


@merenda_bp.route('/cardapios/novo', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def novo_cardapio_pnae():
    """Criar novo cardápio PNAE dinâmico."""
    if request.method == 'POST':
        try:
            val_inicio = datetime.strptime(request.form.get('validade_inicio'), '%Y-%m-%d').date()
            val_fim = datetime.strptime(request.form.get('validade_fim'), '%Y-%m-%d').date()
            
            novo = Cardapio(
                nome=request.form.get('nome'),
                escola_id=request.form.get('escola_id', type=int),
                etapa_pnae=request.form.get('etapa_pnae'),
                modalidade_atendimento=request.form.get('modalidade_atendimento'),
                semanas_referencia=request.form.get('semanas_referencia'),  # Adicionado
                validade_inicio=val_inicio,
                validade_fim=val_fim,
                mes=val_inicio.month,
                ano=val_inicio.year,
                restricao_alergica=request.form.get('restricao_alergica'),
                observacoes=request.form.get('observacoes'),
                status='Ativo'
            )
            db.session.add(novo)
            db.session.flush()

            # --- PROCESSA MÚLTIPLOS NUTRICIONISTAS ---
            nutri_nomes = request.form.getlist('nutricionista_nome[]')
            nutri_crns = request.form.getlist('nutricionista_crn[]')
            for n, c in zip(nutri_nomes, nutri_crns):
                if n.strip():
                    nutri_item = CardapioNutricionista(
                        cardapio_id=novo.id,
                        nome=n.strip(),
                        crn=c.strip()
                    )
                    db.session.add(nutri_item)
            # ----------------------------------------

            # Processa os itens diários enviados pelo formulário
            dias_semana = request.form.getlist('dia_semana[]')
            tipos_refeicao = request.form.getlist('tipo_refeicao[]')
            horarios = request.form.getlist('horario_servido[]')
            preparacoes = request.form.getlist('descricao_preparacao[]')
            bebidas = request.form.getlist('bebida_acompanhamento[]')
            nutricional = request.form.getlist('informacao_nutricional_resumo[]')

            for i in range(len(dias_semana)):
                if preparacoes[i].strip():
                    item = CardapioItemDiario(
                        cardapio_id=novo.id,
                        dia_semana=dias_semana[i],
                        tipo_refeicao=tipos_refeicao[i],
                        horario_servido=horarios[i] if i < len(horarios) else '',
                        descricao_preparacao=preparacoes[i],
                        bebida_acompanhamento=bebidas[i] if i < len(bebidas) else '',
                        informacao_nutricional_resumo=nutricional[i] if i < len(nutricional) else ''
                    )
                    db.session.add(item)

            db.session.commit()
            registrar_log(f"Cadastrou o cardápio PNAE '{novo.nome}' para a escola ID {novo.escola_id}")
            flash('Cardápio PNAE cadastrado com sucesso!', 'success')
            return redirect(url_for('merenda.listar_cardapios_pnae'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar cardápio: {e}', 'danger')

    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    return render_template('merenda/cardapio_form.html', escolas=escolas, cardapio=None)


@merenda_bp.route('/cardapios/excluir/<int:cardapio_id>', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def excluir_cardapio_pnae(cardapio_id):
    """Remover cardápio."""
    cardapio = Cardapio.query.get_or_404(cardapio_id)
    try:
        db.session.delete(cardapio)
        db.session.commit()
        flash('Cardápio excluído com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir cardápio: {e}', 'danger')
    return redirect(url_for('merenda.listar_cardapios_pnae'))

@merenda_bp.route('/cardapios/excluir-mensal/<int:cardapio_id>', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def excluir_cardapio_mensal(cardapio_id):
    """Exclui um cardápio mensal específico e seus pratos cadastrados."""
    cardapio = Cardapio.query.get_or_404(cardapio_id)
    try:
        escola_id = cardapio.escola_id
        mes = cardapio.mes
        ano = cardapio.ano
        
        db.session.delete(cardapio)
        db.session.commit()
        registrar_log(f"Excluiu o cardápio mensal #{cardapio_id} ({mes}/{ano}) da escola ID {escola_id}.")
        flash('Cardápio excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir cardápio: {e}', 'danger')
        
    return redirect(url_for('merenda.gerenciar_cardapio'))


@merenda_bp.route('/cardapio/imprimir-mensal/<int:cardapio_id>')
@login_required
def imprimir_cardapio_mensal(cardapio_id):
    # Usa o modelo correto 'Cardapio'
    cardapio = Cardapio.query.get_or_404(cardapio_id)
    
    # Usa o modelo correto 'PratoDiario'
    pratos_db = PratoDiario.query.filter_by(cardapio_id=cardapio.id).all()
    
    pratos_dict = {}
    for p in pratos_db:
        d_obj = datetime.strptime(p.data, '%Y-%m-%d').date() if isinstance(p.data, str) else p.data
        pratos_dict[d_obj] = p.descricao

    cal = calendar.Calendar(firstweekday=0)
    calendario_mes = cal.monthdayscalendar(cardapio.ano, cardapio.mes)

    meses_pt = {
        1: 'JANEIRO', 2: 'FEVEREIRO', 3: 'MARÇO', 4: 'ABRIL',
        5: 'MAIO', 6: 'JUNHO', 7: 'JULHO', 8: 'AGOSTO',
        9: 'SETEMBRO', 10: 'OUTUBRO', 11: 'NOVEMBRO', 12: 'DEZEMBRO'
    }

    html = render_template(
        'merenda/pdf_cardapio_mensal.html',
        cardapio=cardapio,
        pratos=pratos_dict,
        calendario_mes=calendario_mes,
        mes_nome=meses_pt.get(cardapio.mes, ''),
        date=date
    )
    
    pdf = HTML(string=html).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    escola_nome = cardapio.escola.nome if cardapio.escola else "Escola"
    response.headers['Content-Disposition'] = f'inline; filename=Cardapio_{escola_nome}_{cardapio.mes}_{cardapio.ano}.pdf'
    return response


@merenda_bp.route('/cardapio/pdf/<int:cardapio_id>')
@login_required
@role_required('Merenda Escolar', 'admin')
def gerar_cardapio_pdf(cardapio_id):
    """Gera o PDF oficial do Cardápio PNAE em formato A4 Paisagem (Mural)."""
    cardapio = Cardapio.query.get_or_404(cardapio_id)
    escola_nome = cardapio.escola.nome if cardapio.escola else "TODAS AS ESCOLAS / REDE MUNICIPAL"

    buffer = io.BytesIO()
    # Margens e Formato A4 Paisagem para afixar no mural da escola
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=3.8 * cm,
        bottomMargin=1.8 * cm
    )

    styles = getSampleStyleSheet()
    
    style_title = ParagraphStyle(name='TitleStyle', fontName='Helvetica-Bold', fontSize=14, leading=16, alignment=TA_CENTER)
    style_subtitle = ParagraphStyle(name='SubTitleStyle', fontName='Helvetica-Bold', fontSize=10, leading=12, alignment=TA_CENTER, textColor=colors.HexColor('#004d40'))
    style_cell_header = ParagraphStyle(name='CellHeader', fontName='Helvetica-Bold', fontSize=9, leading=11, alignment=TA_CENTER, textColor=colors.whitesmoke)
    style_cell_body = ParagraphStyle(name='CellBody', fontName='Helvetica', fontSize=8, leading=10, alignment=TA_LEFT)
    style_cell_bold = ParagraphStyle(name='CellBold', fontName='Helvetica-Bold', fontSize=8, leading=10, alignment=TA_LEFT)
    style_footer = ParagraphStyle(name='Footer', fontName='Helvetica', fontSize=8, leading=10, alignment=TA_CENTER)

    story = []

    # Cabeçalho Principal
    story.append(Paragraph("CARDÁPIO DA ALIMENTAÇÃO ESCOLAR - PNAE", style_title))
    story.append(Paragraph(f"UNIDADE ESCOLAR: {escola_nome.upper()}", style_subtitle))
    story.append(Spacer(1, 0.2 * cm))

    # Tratamento das informações de cabeçalho
    etapa_mod = formatar_campo(f"{cardapio.etapa_pnae or ''} - {cardapio.modalidade_atendimento or ''}".strip(" -"), "Geral")
    val_inicio = cardapio.validade_inicio.strftime('%d/%m/%Y') if cardapio.validade_inicio else "N/A"
    val_fim = cardapio.validade_fim.strftime('%d/%m/%Y') if cardapio.validade_fim else "N/A"
    semanas_ref = formatar_campo(cardapio.semanas_referencia, "Ciclo Geral") # <--- ADICIONADO

    # Tabela com Detalhes da Vigência, Ciclo de Semanas e Modalidade
    info_data = [
        [
            Paragraph(f"<b>Etapa/Modalidade:</b> {etapa_mod}", style_cell_body),
            Paragraph(f"<b>Referência:</b> {semanas_ref}", style_cell_body), # <--- ADICIONADO NA COLUNA DO MEIO
            Paragraph(f"<b>Período de Vigência:</b> {val_inicio} a {val_fim}", style_cell_body)
        ]
    ]
    info_table = Table(info_data, colWidths=[9.5 * cm, 8.5 * cm, 9.3 * cm])
    info_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#004d40')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#e0f2f1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 0.4 * cm))

    # Montagem da Grade Semanal (Segunda a Sexta)
    dias_semana_ordem = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira']
    
    # Estrutura da Tabela do Cardápio
    grid_header = [
        Paragraph("Dia da Semana", style_cell_header),
        Paragraph("Refeição / Horário", style_cell_header),
        Paragraph("Preparação / Cardápio do Dia", style_cell_header),
        Paragraph("Acompanhamento / Bebida", style_cell_header),
        Paragraph("Info. Nutricional Resumida", style_cell_header)
    ]
    grid_data = [grid_header]

    for dia in dias_semana_ordem:
        itens_dia = [item for item in cardapio.itens_pnae if item.dia_semana and item.dia_semana.lower() == dia.lower()]
        if itens_dia:
            for idx, item in enumerate(itens_dia):
                # Tratamento de fallback item a item
                tipo_ref = formatar_campo(item.tipo_refeicao, "Refeição")
                horario = formatar_campo(item.horario_servido, "N/A")
                desc_prep = formatar_campo(item.descricao_preparacao, "Sem descrição do prato").replace('\n', '<br/>')
                bebida = formatar_campo(item.bebida_acompanhamento, "-")
                info_nutri = formatar_campo(item.informacao_nutricional_resumo, "-")

                grid_data.append([
                    Paragraph(f"<b>{dia}</b>" if idx == 0 else "", style_cell_bold),
                    Paragraph(f"{tipo_ref}<br/><font color='#555555'>({horario})</font>", style_cell_body),
                    Paragraph(desc_prep, style_cell_body),
                    Paragraph(bebida, style_cell_body),
                    Paragraph(info_nutri, style_cell_body)
                ])
        else:
            grid_data.append([
                Paragraph(f"<b>{dia}</b>", style_cell_bold),
                Paragraph("-", style_cell_body),
                Paragraph("<i>Sem refeição cadastrada para este dia</i>", style_cell_body),
                Paragraph("-", style_cell_body),
                Paragraph("-", style_cell_body)
            ])

    grid_table = Table(grid_data, colWidths=[4.2 * cm, 4.5 * cm, 11.5 * cm, 4.3 * cm, 2.8 * cm])
    grid_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(grid_table)
    story.append(Spacer(1, 0.3 * cm))

    # Observações e Restrições Alérgicas (Exigência PNAE)
    if cardapio.restricao_alergica or cardapio.observacoes:
        obs_texto = ""
        if cardapio.restricao_alergica:
            obs_texto += f"<b>Atenção (Restrições Alérgicas/Substituições):</b> {cardapio.restricao_alergica}<br/>"
        if cardapio.observacoes:
            obs_texto += f"<b>Observações Gerais:</b> {cardapio.observacoes}"
        
        obs_table = Table([[Paragraph(obs_texto, style_cell_body)]], colWidths=[27.3 * cm])
        obs_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#B00020')),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFEBEE')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(obs_table)
        story.append(Spacer(1, 0.5 * cm))

    # --- RODAPÉ COM MÚLTIPLAS ASSINATURAS DE NUTRICIONISTAS ---
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("__________________________________________________________", style_footer))
    
    nutris_assinaturas = []
    for nutridep in cardapio.nutricionistas:
        nutris_assinaturas.append(Paragraph(f"<b>{nutridep.nome}</b><br/>CRN: {nutridep.crn}", style_footer))

    if nutris_assinaturas:
        # Distribui as assinaturas lado a lado de forma automática com base na quantidade cadastrada
        t_nutri = Table([nutris_assinaturas], colWidths=[27.3 * cm / len(nutris_assinaturas)] * len(nutris_assinaturas))
        t_nutri.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER'), ('VALIGN', (0,0), (-1,-1), 'TOP')]))
        story.append(t_nutri)
    else:
        story.append(Paragraph("<b>Nutricionista Responsável Técnico</b><br/>CRN não informado", style_footer))
    # ---------------------------------------------------------

    doc.build(story, onFirstPage=cabecalho_e_rodape, onLaterPages=cabecalho_e_rodape)
    buffer.seek(0)

    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    
    val_str = cardapio.validade_inicio.strftime('%m_%Y') if cardapio.validade_inicio else "00_0000"
    nome_arquivo = f"Cardapio_PNAE_{escola_nome.replace(' ', '_')}_{val_str}.pdf"
    
    response.headers['Content-Disposition'] = f'inline; filename={nome_arquivo}'
    return response

def formatar_campo(texto, valor_padrao="Não informado"):
    """Garante que nenhum campo nulo desfigure ou suma do PDF."""
    if texto is None or str(texto).strip() == "":
        return valor_padrao
    return str(texto).strip()