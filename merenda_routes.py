# merenda_routes.py
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.lib.units import cm
import io
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from .extensions import db, bcrypt
# Importe todos os novos modelos aqui
from .models import Escola, ProdutoMerenda, EstoqueMovimento, SolicitacaoMerenda, SolicitacaoItem, Cardapio, PratoDiario, HistoricoCardapio, Servidor
from .utils import login_required, registrar_log
from sqlalchemy import or_, func
from datetime import datetime
from datetime import date, timedelta
import calendar
from .utils import role_required


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
