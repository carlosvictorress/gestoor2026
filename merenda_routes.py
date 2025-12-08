# merenda_routes.py
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.lib.units import cm
import io
import json
import uuid
from reportlab.lib.pagesizes import A4
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, make_response
from .extensions import db, bcrypt
# Importe todos os novos modelos aqui
from werkzeug.utils import secure_filename
from .models import Escola, ProdutoMerenda, EstoqueMovimento, SolicitacaoMerenda, SolicitacaoItem, Cardapio, PratoDiario, HistoricoCardapio, Servidor
from .utils import login_required, registrar_log, limpar_cpf, cabecalho_e_rodape, currency_filter_br, cabecalho_e_rodape_moderno
    
from sqlalchemy import or_, func
from datetime import datetime
from datetime import date, timedelta
import calendar
from .utils import role_required
from .models import AgricultorFamiliar, DocumentoAgricultor, ContratoPNAE, ItemProjetoVenda, EntregaPNAE, ConfiguracaoPNAE



merenda_bp = Blueprint('merenda', __name__, url_prefix='/merenda')

# --- ROTAS PRINCIPAIS A SEREM DESENVOLVIDAS ---

# Rota principal do módulo
@merenda_bp.route('/dashboard')
@login_required
def dashboard():
    # --- Indicadores Rápidos (KPIs) ---
    total_escolas_ativas = Escola.query.filter_by(status='Ativa').count()
    total_produtos = ProdutoMerenda.query.count()
    solicitacoes_pendentes = SolicitacaoMerenda.query.filter_by(status='Pendente').count()

    # --- Gráfico: Top 5 Escolas por Quantidade Total de Produtos Consumidos ---
    top_escolas_query = db.session.query(
        Escola.nome,
        func.sum(EstoqueMovimento.quantidade).label('total_consumido')
    ).join(SolicitacaoMerenda, Escola.id == SolicitacaoMerenda.escola_id)\
     .join(EstoqueMovimento, SolicitacaoMerenda.id == EstoqueMovimento.solicitacao_id)\
     .filter(EstoqueMovimento.tipo == 'Saída')\
     .group_by(Escola.nome)\
     .order_by(func.sum(EstoqueMovimento.quantidade).desc())\
     .limit(5).all()
    
    # --- CORREÇÃO APLICADA AQUI ---
    if top_escolas_query:
        escolas_labels, escolas_data = zip(*top_escolas_query)
    else:
        escolas_labels, escolas_data = [], []

    # --- Gráfico: Top 5 Produtos Mais Solicitados ---
    top_produtos_query = db.session.query(
        ProdutoMerenda.nome,
        func.sum(SolicitacaoItem.quantidade_solicitada).label('total_solicitado')
    ).join(ProdutoMerenda)\
     .group_by(ProdutoMerenda.nome)\
     .order_by(func.sum(SolicitacaoItem.quantidade_solicitada).desc())\
     .limit(5).all()
    
    # --- CORREÇÃO APLICADA AQUI ---
    if top_produtos_query:
        produtos_labels, produtos_data = zip(*top_produtos_query)
    else:
        produtos_labels, produtos_data = [], []

    # --- Tabela: Produtos com Estoque Baixo (Ex: < 10 unidades) ---
    estoque_baixo_limite = 10
    produtos_estoque_baixo = ProdutoMerenda.query.filter(
        ProdutoMerenda.estoque_atual < estoque_baixo_limite, 
        ProdutoMerenda.estoque_atual > 0
    ).order_by(ProdutoMerenda.estoque_atual.asc()).all()

    return render_template('merenda/dashboard.html',
                           total_escolas_ativas=total_escolas_ativas,
                           total_produtos=total_produtos,
                           solicitacoes_pendentes=solicitacoes_pendentes,
                           escolas_labels=list(escolas_labels),
                           escolas_data=list(escolas_data),
                           produtos_labels=list(produtos_labels),
                           produtos_data=list(produtos_data),
                           produtos_estoque_baixo=produtos_estoque_baixo)

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
    produtos = ProdutoMerenda.query.order_by(ProdutoMerenda.nome).all()
    return render_template('merenda/produtos_lista.html', produtos=produtos)

@merenda_bp.route('/produtos/novo', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def novo_produto():
    if request.method == 'POST':
        nome_produto = request.form.get('nome')
        # Verifica se o produto já existe
        if ProdutoMerenda.query.filter_by(nome=nome_produto).first():
            flash('Já existe um produto cadastrado com este nome.', 'danger')
            return redirect(url_for('merenda.novo_produto'))
        
        try:
            novo = ProdutoMerenda(
                nome=nome_produto,
                unidade_medida=request.form.get('unidade_medida'),
                categoria=request.form.get('categoria')
                # O estoque_atual inicia em 0 por padrão
            )
            db.session.add(novo)
            db.session.commit()
            registrar_log(f'Cadastrou o produto da merenda: "{novo.nome}".')
            flash('Produto cadastrado com sucesso!', 'success')
            return redirect(url_for('merenda.listar_produtos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar produto: {e}', 'danger')

    return render_template('merenda/produtos_form.html', produto=None)

@merenda_bp.route('/produtos/editar/<int:produto_id>', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def editar_produto(produto_id):
    produto = ProdutoMerenda.query.get_or_404(produto_id)
    if request.method == 'POST':
        try:
            produto.nome = request.form.get('nome')
            produto.unidade_medida = request.form.get('unidade_medida')
            produto.categoria = request.form.get('categoria')

            db.session.commit()
            registrar_log(f'Editou o produto da merenda: "{produto.nome}".')
            flash('Dados do produto atualizados com sucesso!', 'success')
            return redirect(url_for('merenda.listar_produtos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao editar o produto: {e}', 'danger')

    return render_template('merenda/produtos_form.html', produto=produto)
# GET /produtos -> Listar todos os produtos e estoque atual
# GET /produtos/novo -> Formulário de novo produto
# POST /produtos/novo -> Salvar novo produto

# Rotas para Movimentação de Estoque
@merenda_bp.route('/estoque/entradas', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def entrada_estoque():
    if request.method == 'POST':
        try:
            produto_id = request.form.get('produto_id', type=int)
            quantidade_str = request.form.get('quantidade', '0').replace(',', '.')
            quantidade = float(quantidade_str)

            if not produto_id or quantidade <= 0:
                flash('Produto e quantidade são obrigatórios.', 'danger')
                return redirect(url_for('merenda.entrada_estoque'))

            # Localiza o produto no banco de dados
            produto = ProdutoMerenda.query.get(produto_id)
            if not produto:
                flash('Produto não encontrado.', 'danger')
                return redirect(url_for('merenda.entrada_estoque'))

            # --- LÓGICA PRINCIPAL ---
            # 1. Adiciona a quantidade ao estoque atual do produto
            produto.estoque_atual += quantidade
            
            # 2. Cria um registro do movimento de estoque
            data_validade_str = request.form.get('data_validade')
            data_validade = datetime.strptime(data_validade_str, '%Y-%m-%d').date() if data_validade_str else None

            movimento = EstoqueMovimento(
                produto_id=produto_id,
                tipo='Entrada',
                quantidade=quantidade,
                fornecedor=request.form.get('fornecedor'),
                lote=request.form.get('lote'),
                data_validade=data_validade,
                usuario_responsavel=session.get('username')
            )
            
            db.session.add(movimento) # Adiciona o novo registro de movimento
            db.session.commit() # Salva o estoque atualizado do produto e o novo movimento
            
            registrar_log(f'Deu entrada de {quantidade} {produto.unidade_medida} do produto "{produto.nome}".')
            flash(f'Entrada de estoque para "{produto.nome}" registrada com sucesso!', 'success')
            return redirect(url_for('merenda.entrada_estoque'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao registrar entrada de estoque: {e}', 'danger')
    
    # Para o método GET (carregar a página)
    produtos = ProdutoMerenda.query.order_by(ProdutoMerenda.nome).all()
    historico_entradas = EstoqueMovimento.query.filter_by(tipo='Entrada').order_by(EstoqueMovimento.data_movimento.desc()).limit(20).all()
    return render_template('merenda/estoque_entradas.html', produtos=produtos, historico=historico_entradas)
# GET /estoque/entradas -> Listar histórico de entradas e link para registrar nova
# POST /estoque/entradas/nova -> Lógica para registrar entrada de produtos e atualizar estoque

# Rotas para Solicitações das Escolas
@merenda_bp.route('/solicitacoes/nova', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def nova_solicitacao():
    if request.method == 'POST':
        try:
            escola_id = request.form.get('escola_id', type=int)
            # Supondo que o solicitante é o usuário logado e que ele é um servidor
            solicitante = Servidor.query.filter_by(cpf=session.get('user_cpf')).first() # Nota: Precisamos adicionar o CPF à sessão no login
            
            # --- Validação ---
            if not escola_id:
                flash('É necessário selecionar uma escola.', 'danger')
                return redirect(url_for('merenda.nova_solicitacao'))
            
            # --- Cria a Solicitação Principal ---
            nova_sol = SolicitacaoMerenda(
                escola_id=escola_id,
                status='Pendente',
                solicitante_cpf=request.form.get('solicitante_cpf') # Usaremos o CPF do formulário por enquanto
            )
            db.session.add(nova_sol)

            # --- Adiciona os Itens à Solicitação ---
            produtos_ids = request.form.getlist('produto_id[]')
            quantidades = request.form.getlist('quantidade[]')

            if not produtos_ids:
                flash('É necessário adicionar pelo menos um produto à solicitação.', 'danger')
                return redirect(url_for('merenda.nova_solicitacao'))

            for i in range(len(produtos_ids)):
                produto_id = int(produtos_ids[i])
                quantidade_str = quantidades[i].replace(',', '.')
                quantidade = float(quantidade_str)
                
                if produto_id and quantidade > 0:
                    item = SolicitacaoItem(
                        solicitacao=nova_sol, # Associa o item à solicitação recém-criada
                        produto_id=produto_id,
                        quantidade_solicitada=quantidade
                    )
                    db.session.add(item)
            
            db.session.commit()
            registrar_log(f'Criou a solicitação de merenda #{nova_sol.id} para a escola ID {escola_id}.')
            flash('Solicitação de merenda enviada com sucesso!', 'success')
            # Futuramente, redirecionar para a lista de solicitações da escola
            return redirect(url_for('merenda.listar_produtos')) 

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao criar solicitação: {e}', 'danger')

    # Para o método GET
    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    produtos = ProdutoMerenda.query.order_by(ProdutoMerenda.nome).all()
    servidores = Servidor.query.order_by(Servidor.nome).all()
    return render_template('merenda/solicitacao_form.html', escolas=escolas, produtos=produtos, servidores=servidores)
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
            # --- Lógica de SAÍDA DE ESTOQUE ---
            solicitacao.status = 'Entregue'
            solicitacao.entregador_cpf = request.form.get('entregador_cpf') or None
            solicitacao.autorizador_cpf = request.form.get('autorizador_cpf') or None # Quem deu a saída
            solicitacao.data_entrega = datetime.utcnow()

            # Itera sobre cada item da solicitação para dar baixa no estoque
            for item in solicitacao.itens:
                produto = item.produto
                # Verifica se há estoque suficiente
                if produto.estoque_atual < item.quantidade_solicitada:
                    flash(f'Estoque insuficiente para o produto "{produto.nome}". Ação cancelada.', 'danger')
                    db.session.rollback()
                    return redirect(url_for('merenda.detalhes_solicitacao', solicitacao_id=solicitacao.id))

                # 1. Subtrai do estoque principal
                produto.estoque_atual -= item.quantidade_solicitada
                
                # 2. Cria o registro de movimento de SAÍDA
                movimento_saida = EstoqueMovimento(
                    produto_id=item.produto_id,
                    tipo='Saída',
                    quantidade=item.quantidade_solicitada,
                    solicitacao_id=solicitacao.id,
                    usuario_responsavel=session.get('username')
                )
                db.session.add(movimento_saida)

            db.session.commit()
            registrar_log(f'Registrou a entrega da solicitação #{solicitacao.id} e deu baixa no estoque.')
            flash('Entrega registrada e estoque atualizado com sucesso!', 'success')
            return redirect(url_for('merenda.painel_solicitacoes', status='Entregue'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao registrar entrega: {e}', 'danger')

    return render_template('merenda/solicitacao_detalhes.html', solicitacao=solicitacao, servidores=servidores)

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
@merenda_bp.route('/cardapios', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def gerenciar_cardapio():
    escola_id = request.args.get('escola_id', type=int)
    hoje = date.today()
    mes_selecionado = request.args.get('mes', hoje.month, type=int)
    ano_selecionado = request.args.get('ano', hoje.year, type=int)

    # --- Lógica de POST (Salvar o cardápio) ---
    if request.method == 'POST':
        try:
            escola_id_post = request.form.get('escola_id', type=int)
            mes_post = request.form.get('mes', type=int)
            ano_post = request.form.get('ano', type=int)
            
            cardapio = Cardapio.query.filter_by(escola_id=escola_id_post, mes=mes_post, ano=ano_post).first()
            
            if not cardapio:
                cardapio = Cardapio(escola_id=escola_id_post, mes=mes_post, ano=ano_post)
                db.session.add(cardapio)
            
            # Limpa pratos antigos para garantir que os removidos sejam excluídos
            for prato_antigo in cardapio.pratos:
                db.session.delete(prato_antigo)

            mudancas = []
            # Itera sobre todos os campos de prato enviados pelo formulário
            for key, value in request.form.items():
                if key.startswith('prato_') and value.strip():
                    data_str = key.replace('prato_', '')
                    data_prato = datetime.strptime(data_str, '%Y-%m-%d').date()
                    
                    novo_prato = PratoDiario(cardapio=cardapio, data_prato=data_prato, nome_prato=value)
                    db.session.add(novo_prato)
                    mudancas.append(f"{data_prato.strftime('%d/%m')}: '{value}'")

            # Registra o histórico da modificação
            historico = HistoricoCardapio(
                cardapio=cardapio,
                usuario=session.get('username'),
                descricao_mudanca=f"Cardápio do mês {mes_post}/{ano_post} salvo. Pratos: {', '.join(mudancas)}"
            )
            db.session.add(historico)
            
            db.session.commit()
            flash('Cardápio mensal salvo com sucesso!', 'success')
            return redirect(url_for('merenda.gerenciar_cardapio', escola_id=escola_id_post, mes=mes_post, ano=ano_post))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar cardápio: {e}', 'danger')

    # --- Lógica de GET (Exibir o cardápio) ---
    pratos_do_mes = {}
    calendario_mes = []
    if escola_id:
        cardapio_atual = Cardapio.query.filter_by(escola_id=escola_id, mes=mes_selecionado, ano=ano_selecionado).first()
        if cardapio_atual:
            for prato in cardapio_atual.pratos:
                pratos_do_mes[prato.data_prato] = prato.nome_prato
        
        # Gera a matriz do calendário para o template
        calendario_mes = calendar.monthcalendar(ano_selecionado, mes_selecionado)

    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    
    # Gera uma lista de meses e anos para os filtros
    meses_pt = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
        7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
    }
    anos_disponiveis = range(hoje.year - 1, hoje.year + 2)

    return render_template('merenda/cardapio_editor.html', 
                           escolas=escolas, 
                           escola_selecionada_id=escola_id,
                           mes_selecionado=mes_selecionado,
                           ano_selecionado=ano_selecionado,
                           pratos=pratos_do_mes,
                           calendario_mes=calendario_mes,
                           meses_pt=meses_pt,
                           anos_disponiveis=anos_disponiveis, date=date)

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
    if escola_id and data_inicio_str and data_fim_str:
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d')
        # Adiciona um dia e subtrai um segundo para incluir o dia final inteiro na busca
        data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d') + timedelta(days=1, seconds=-1)

        # Busca os movimentos de saída que correspondem aos filtros
        resultados = db.session.query(
                EstoqueMovimento.data_movimento,
                ProdutoMerenda.nome,
                EstoqueMovimento.quantidade,
                ProdutoMerenda.unidade_medida
            ).join(ProdutoMerenda).join(SolicitacaoMerenda).filter(
                SolicitacaoMerenda.escola_id == escola_id,
                EstoqueMovimento.tipo == 'Saída',
                EstoqueMovimento.data_movimento.between(data_inicio, data_fim)
            ).order_by(EstoqueMovimento.data_movimento.asc()).all()
        
        # Se o botão de PDF foi clicado, gera o PDF
        if gerar_pdf:
            escola = Escola.query.get(escola_id)
            titulo = f"Relatório de Saídas para {escola.nome}"
            periodo = f"Período: {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
            return gerar_pdf_saidas(titulo, periodo, resultados)

    return render_template('merenda/relatorio_saidas.html', 
                           escolas=escolas, 
                           resultados=resultados,
                           escola_selecionada_id=escola_id,
                           data_inicio=data_inicio_str,
                           data_fim=data_fim_str)

def gerar_pdf_saidas(titulo, periodo, dados):
    """
    Função que gera o PDF do relatório de saídas.
    """
    # --- IMPORTAÇÕES CORRIGIDAS E COMPLETAS ---
    from .utils import cabecalho_e_rodape_moderno
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4 # <-- Importação que faltava
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from flask import make_response
    import io

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=3*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Center', alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='Left', alignment=TA_LEFT))

    story = []
    
    # Adiciona o título e o período
    story.append(Paragraph(titulo, styles['h1']))
    story.append(Paragraph(periodo, styles['Center']))
    story.append(Spacer(1, 1*cm))

    # Prepara os dados da tabela
    table_data = [['Data/Hora da Saída', 'Produto', 'Quantidade']]
    
    for item in dados:
        data_formatada = item.data_movimento.strftime('%d/%m/%Y %H:%M')
        quantidade_formatada = f"{item.quantidade} {item.unidade_medida}"
        table_data.append([data_formatada, item.nome, quantidade_formatada])

    # Cria a tabela
    t = Table(table_data, colWidths=[5*cm, 8*cm, 4*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#004d40')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    story.append(t)
    story.append(Spacer(1, 2*cm))
    
    # Linhas de assinatura
    story.append(Paragraph("________________________________________", styles['Center']))
    story.append(Paragraph("Responsável pelo Almoxarifado", styles['Center']))
    
    doc.build(story, onFirstPage=lambda canvas, doc: cabecalho_e_rodape_moderno(canvas, doc, "Relatório de Saídas"), 
                     onLaterPages=lambda canvas, doc: cabecalho_e_rodape_moderno(canvas, doc, "Relatório de Saídas"))
    
    buffer.seek(0)
    
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=relatorio_saidas.pdf'
    
    return response                       
    
    
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

@merenda_bp.route('/agricultura', methods=['GET', 'POST']) # Alterado para aceitar POST
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
    
    # Busca contratos DO ANO ATUAL
    ano_atual = datetime.now().year
    
    # Soma valor total contratado no ano
    total_contratado = db.session.query(func.sum(ContratoPNAE.valor_total))\
        .filter(func.extract('year', ContratoPNAE.data_inicio) == ano_atual).scalar() or 0.0
        
    # Busca configuração do ano
    config_pnae = ConfiguracaoPNAE.query.filter_by(ano=ano_atual).first()
    
    # Dados para o gráfico de meta
    meta_info = {
        'total_repasse': 0.0,
        'percentual_atual': 0.0,
        'meta_lei': 30 if ano_atual < 2026 else 45, # Lógica da nova lei na interface
        'falta_contratar': 0.0,
        'status': 'Aguardando Configuração'
    }
    
    if config_pnae:
        meta_info['total_repasse'] = config_pnae.valor_total_repasse
        meta_info['meta_lei'] = config_pnae.meta_percentual
        
        if config_pnae.valor_total_repasse > 0:
            percentual = (total_contratado / config_pnae.valor_total_repasse) * 100
            meta_info['percentual_atual'] = percentual
            
            valor_minimo = config_pnae.valor_meta_minima
            if total_contratado >= valor_minimo:
                meta_info['status'] = 'Meta Atingida! 🎉'
            else:
                meta_info['falta_contratar'] = valor_minimo - total_contratado
                meta_info['status'] = 'Abaixo da Meta ⚠️'

    return render_template('merenda/agricultura/dashboard.html', 
                           total_agricultores=total_agricultores, 
                           contratos_ativos=contratos_ativos,
                           total_contratado=total_contratado,
                           meta_info=meta_info,
                           ano_atual=ano_atual)

@merenda_bp.route('/agricultura/fornecedores')
@login_required
def listar_agricultores():
    agricultores = AgricultorFamiliar.query.order_by(AgricultorFamiliar.razao_social).all()
    return render_template('merenda/agricultura/agricultores_lista.html', agricultores=agricultores)


@merenda_bp.route('/agricultura/fornecedores/novo', methods=['GET', 'POST'])
@login_required
def novo_agricultor():
    if request.method == 'POST':
        try:
            # Captura dados básicos (resumo)
            novo = AgricultorFamiliar(
                tipo_fornecedor=request.form.get('tipo_fornecedor'),
                razao_social=request.form.get('razao_social'),
                cpf_cnpj=limpar_cpf(request.form.get('cpf_cnpj')),
                dap_caf_numero=request.form.get('dap_caf_numero'),
                zona=request.form.get('zona'),
                # ... (Preencher todos os outros campos do form)
            )
            
            # Tratamento de Uploads de Documentos (Exemplo simplificado)
            if 'comprovante_residencia' in request.files:
                file = request.files['comprovante_residencia']
                if file.filename != '':
                    filename = secure_filename(file.filename)
                    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], 'pnae', filename))
                    # Criar registro em DocumentoAgricultor...

            db.session.add(novo)
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
def gerenciar_contrato_pnae(contrato_id):
    contrato = ContratoPNAE.query.get_or_404(contrato_id)
    
    # Lógica para calcular o saldo de cada item
    # Cria um dicionário para somar o que já foi entregue de cada produto
    entregue_por_produto = {} # Ex: {'Alface': 50.0, 'Tomate': 10.0}
    
    for entrega in contrato.entregas:
        if entrega.status == 'Aprovado' and entrega.itens_json:
            try:
                itens_entrega = json.loads(entrega.itens_json)
                for item in itens_entrega:
                    prod_nome = item['nome_produto']
                    qtd = float(item['quantidade'])
                    if prod_nome in entregue_por_produto:
                        entregue_por_produto[prod_nome] += qtd
                    else:
                        entregue_por_produto[prod_nome] = qtd
            except:
                pass # Ignora erros de JSON antigo se houver

    return render_template('merenda/agricultura/contrato_gerenciar.html', 
                           contrato=contrato, 
                           entregue_por_produto=entregue_por_produto)

@merenda_bp.route('/agricultura/contratos/<int:contrato_id>/nova-entrega', methods=['POST'])
@login_required
def registrar_entrega_pnae(contrato_id):
    contrato = ContratoPNAE.query.get_or_404(contrato_id)
    
    try:
        data_entrega = datetime.strptime(request.form.get('data_entrega'), '%Y-%m-%d').date()
        nota_fiscal = request.form.get('numero_nota_fiscal')
        
        # --- LÓGICA DE UPLOAD DA NOTA FISCAL (NOVO) ---
        filename_nf = None
        if 'arquivo_nf' in request.files:
            file = request.files['arquivo_nf']
            if file and file.filename != '':
                # Cria nome seguro: ID_CONTRATO_DATA_NOMEO.pdf
                ext = file.filename.rsplit('.', 1)[1].lower()
                nome_arquivo = f"NF_Contrato{contrato.id}_{data_entrega.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}.{ext}"
                
                # Garante que a pasta existe
                caminho_pasta = os.path.join(current_app.config['UPLOAD_FOLDER'], 'pnae_notas')
                os.makedirs(caminho_pasta, exist_ok=True)
                
                file.save(os.path.join(caminho_pasta, nome_arquivo))
                filename_nf = nome_arquivo
        # -----------------------------------------------

        # Processar os itens (igual ao anterior)
        item_ids = request.form.getlist('item_id[]')
        qtds = request.form.getlist('qtd_entregue[]')
        
        lista_itens_entrega = []
        valor_total_entrega = 0.0
        
        for i, item_id in enumerate(item_ids):
            qtd = float(qtds[i].replace(',', '.')) if qtds[i] else 0.0
            if qtd > 0:
                item_contrato = ItemProjetoVenda.query.get(item_id)
                valor_item = qtd * item_contrato.preco_unitario
                
                lista_itens_entrega.append({
                    'item_id': item_contrato.id,
                    'nome_produto': item_contrato.nome_produto,
                    'quantidade': qtd,
                    'preco_unitario': item_contrato.preco_unitario,
                    'valor_total': valor_item
                })
                valor_total_entrega += valor_item
        
        if not lista_itens_entrega:
            flash('Informe a quantidade de pelo menos um item.', 'warning')
            return redirect(url_for('merenda.gerenciar_contrato_pnae', contrato_id=contrato.id))

        nova_entrega = EntregaPNAE(
            contrato_id=contrato.id,
            data_entrega=data_entrega,
            numero_nota_fiscal=nota_fiscal,
            recibo_filename=filename_nf, # Salva o nome do arquivo aqui
            responsavel_recebimento=session.get('username'),
            status='Aprovado',
            valor_total=valor_total_entrega,
            itens_json=json.dumps(lista_itens_entrega)
        )
        
        db.session.add(nova_entrega)
        db.session.commit()
        
        flash('Entrega registrada com sucesso!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao registrar entrega: {e}', 'danger')
        
    return redirect(url_for('merenda.gerenciar_contrato_pnae', contrato_id=contrato.id))

@merenda_bp.route('/agricultura/contratos/<int:contrato_id>/pdf')
@login_required
def pdf_contrato_pnae(contrato_id):
    from .utils import cabecalho_e_rodape # Importa seu cabeçalho padrão
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

@merenda_bp.route('/agricultura/entrega/<int:entrega_id>/termo-pdf')
@login_required
def pdf_termo_recebimento_pnae(entrega_id):
    entrega = EntregaPNAE.query.get_or_404(entrega_id)
    contrato = entrega.contrato
    agricultor = contrato.agricultor
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, 
                            rightMargin=2*cm, leftMargin=2*cm, 
                            topMargin=2*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    style_titulo = styles['Heading1']
    style_titulo.alignment = 1 # Centralizado
    style_normal = styles['BodyText']
    style_normal.alignment = 4 # Justificado
    
    story = []
    
    # Cabeçalho
    story.append(Paragraph("TERMO DE RECEBIMENTO DA AGRICULTURA FAMILIAR", style_titulo))
    story.append(Spacer(1, 0.5*cm))
    
    texto_intro = f"""
    Atesto para os devidos fins que foram entregues nesta data, pelo fornecedor <b>{agricultor.razao_social}</b> 
    (CPF/CNPJ: {agricultor.cpf_cnpj}), referente ao Contrato/Chamada Pública nº {contrato.numero_contrato}, 
    os gêneros alimentícios abaixo discriminados:
    """
    story.append(Paragraph(texto_intro, style_normal))
    story.append(Spacer(1, 0.5*cm))
    
    # Tabela
    dados_tabela = [['Produto', 'Unidade', 'Qtd. Entregue', 'Valor Total']]
    
    if entrega.itens_json:
        try:
            itens = json.loads(entrega.itens_json)
            for item in itens:
                dados_tabela.append([
                    item['nome_produto'],
                    "Unid.", 
                    f"{item['quantidade']}".replace('.', ','),
                    currency_filter_br(item['valor_total'])
                ])
        except:
            dados_tabela.append(['Erro ao ler itens', '-', '-', '-'])
            
    dados_tabela.append(['TOTAL DA ENTREGA', '', '', currency_filter_br(entrega.valor_total)])
    
    t = Table(dados_tabela, colWidths=[8*cm, 2.5*cm, 3*cm, 3.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 1), (0, -2), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(t)
    story.append(Spacer(1, 1*cm))
    
    # Assinaturas
    story.append(Paragraph("_____________________________________________", style_titulo))
    story.append(Paragraph(f"Responsável: {entrega.responsavel_recebimento}", style_titulo))
    story.append(Spacer(1, 1.5*cm))
    
    story.append(Paragraph("_____________________________________________", style_titulo))
    story.append(Paragraph(f"{agricultor.razao_social}", style_titulo))
    
    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape(c, d), 
                     onLaterPages=lambda c, d: cabecalho_e_rodape(c, d))
    
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Termo_Recebimento_{entrega.id}.pdf'
    
    return response