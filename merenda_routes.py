# merenda_routes.py
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.lib.units import cm
import os
import base64
import io
import json
import uuid
from reportlab.lib.pagesizes import A4
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, make_response
from extensions import db, bcrypt
# Importe todos os novos modelos aqui
from werkzeug.utils import secure_filename
from models import Escola, ProdutoMerenda, EstoqueMovimento, SolicitacaoMerenda, SolicitacaoItem, Cardapio, PratoDiario, HistoricoCardapio, Servidor, RelatorioTecnico, RelatorioAnexo, PedidoEmpresa, PedidoEmpresaItem
from utils import login_required, registrar_log, limpar_cpf, cabecalho_e_rodape, currency_filter_br, cabecalho_e_rodape_moderno, upload_arquivo_para_nuvem
    
from sqlalchemy import or_, func
from datetime import datetime
from datetime import date, timedelta
import calendar
from utils import role_required
from models import AgricultorFamiliar, DocumentoAgricultor, ContratoPNAE, ItemProjetoVenda, EntregaPNAE, ConfiguracaoPNAE



merenda_bp = Blueprint('merenda', __name__, url_prefix='/merenda')

# --- ROTAS PRINCIPAIS A SEREM DESENVOLVIDAS ---

# Rota principal do módulo
@merenda_bp.route('/dashboard')
@login_required
@role_required("RH", "admin", "Merenda Escolar")
def dashboard():
    # --- KPIs BÁSICOS ---
    total_escolas_ativas = Escola.query.filter_by(status='Ativa').count()
    total_produtos = ProdutoMerenda.query.count()
    solicitacoes_pendentes = SolicitacaoMerenda.query.filter_by(status='Pendente').count()
    
    # --- LÓGICA DE ALERTA DE VALIDADE ---
    hoje = date.today()
    data_limite_alerta = hoje + timedelta(days=45) 
    data_corte_passado = hoje - timedelta(days=30) 

    alertas_validade = db.session.query(
        ProdutoMerenda.nome,
        EstoqueMovimento.lote,
        EstoqueMovimento.data_validade,
        ProdutoMerenda.unidade_medida,
        ProdutoMerenda.estoque_atual
    ).join(ProdutoMerenda)\
     .filter(
        EstoqueMovimento.tipo == 'Entrada',
        EstoqueMovimento.data_validade.isnot(None),
        EstoqueMovimento.data_validade <= data_limite_alerta,
        EstoqueMovimento.data_validade >= data_corte_passado,
        ProdutoMerenda.estoque_atual > 0 
     ).order_by(EstoqueMovimento.data_validade.asc()).all()

    # --- ESTOQUE BAIXO ---
    # Busca produtos com estoque abaixo do mínimo definido no cadastro de cada um
    produtos_estoque_baixo = ProdutoMerenda.query.filter(
        ProdutoMerenda.estoque_atual <= ProdutoMerenda.estoque_minimo, 
        ProdutoMerenda.estoque_atual > 0
    ).order_by(ProdutoMerenda.estoque_atual.asc()).all()

    # --- NOVO: PRODUTOS PARA O MODAL DE SOLICITAÇÃO (EMPRESA) ---
    # Filtramos para não mostrar produtos da Agricultura Familiar no pedido para empresa
    produtos_disponiveis = ProdutoMerenda.query.filter(
        or_(ProdutoMerenda.categoria != 'Agricultura Familiar', ProdutoMerenda.categoria.is_(None))
    ).order_by(ProdutoMerenda.nome.asc()).all()

    # --- NOVO: HISTÓRICO DE PEDIDOS PARA EMPRESA ---
    # Busca os pedidos realizados, ordenando pelos mais recentes
    pedidos_empresa = PedidoEmpresa.query.order_by(PedidoEmpresa.data_pedido.desc()).limit(10).all()

    # --- QUERIES DOS GRÁFICOS (Exemplo de estrutura caso você as tenha) ---
    # Se você já tiver a lógica dos gráficos pronta, mantenha os nomes das variáveis abaixo:
    escolas_labels = [] # Suas labels de consumo por escola
    escolas_data = []   # Seus dados de consumo por escola
    produtos_labels = [] # Suas labels de produtos mais saídos
    produtos_data = []   # Seus dados de produtos mais saídos

    return render_template('merenda/dashboard.html',
                           total_escolas_ativas=total_escolas_ativas,
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
                unidade_medida=request.form.get('unidade_medida'),
                categoria=request.form.get('categoria'),
                
                # NOVO CAMPO: Fator de Conversão (Ex: 1 fardo = 10 unidades)
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
            # Se o campo estiver vazio ou for zero, define o padrão como 1.0
            fator_raw = request.form.get('fator_conversao')
            produto.fator_conversao = flt(fator_raw) if fator_raw and flt(fator_raw) > 0 else 1.0

            produto.nome = request.form.get('nome')
            produto.unidade_medida = request.form.get('unidade_medida')
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

# Rotas para Movimentação de Estoque
@merenda_bp.route('/estoque/entradas', methods=['GET', 'POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def entrada_estoque():
    if request.method == 'POST':
        try:
            produto_id = request.form.get('produto_id', type=int)
            quantidade_str = request.form.get('quantidade', '0').replace(',', '.')
            quantidade_digitada = float(quantidade_str)

            if not produto_id or quantidade_digitada <= 0:
                flash('Produto e quantidade são obrigatórios.', 'danger')
                return redirect(url_for('merenda.entrada_estoque'))

            # Localiza o produto no banco de dados
            produto = ProdutoMerenda.query.get(produto_id)
            if not produto:
                flash('Produto não encontrado.', 'danger')
                return redirect(url_for('merenda.entrada_estoque'))

            # --- LÓGICA DE CONVERSÃO AUTOMÁTICA ---
            # O estoque_atual sempre armazena a menor unidade (ex: KG ou UNID).
            # Se o produto for fardo/caixa, multiplicamos pelo fator cadastrado.
            fator = produto.fator_conversao if produto.fator_conversao and produto.fator_conversao > 0 else 1.0
            quantidade_para_estoque = quantidade_digitada * fator

            # 1. Adiciona a quantidade convertida ao estoque atual do produto
            produto.estoque_atual += quantidade_para_estoque
            
            # 2. Prepara a data de validade
            data_validade_str = request.form.get('data_validade')
            data_validade = datetime.strptime(data_validade_str, '%Y-%m-%d').date() if data_validade_str else None

            # 3. Cria o registro do movimento de estoque
            # Guardamos a 'quantidade_digitada' (ex: 5 fardos) para manter a coerência com a nota fiscal
            movimento = EstoqueMovimento(
                produto_id=produto_id,
                tipo='Entrada',
                quantidade=quantidade_digitada,
                fornecedor=request.form.get('fornecedor'),
                lote=request.form.get('lote'),
                data_validade=data_validade,
                usuario_responsavel=session.get('username')
            )
            
            db.session.add(movimento)
            db.session.commit()
            
            # Log detalhado mostrando a conversão se ela existir
            msg_log = f'Entrada de {quantidade_digitada} {produto.unidade_medida}'
            if fator > 1:
                msg_log += f' (Convertido em {quantidade_para_estoque} unidades de saldo)'
            
            registrar_log(f'{msg_log} do produto "{produto.nome}".')
            flash(f'Entrada de "{produto.nome}" registrada! Saldo atualizado com sucesso.', 'success')
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
    from utils import cabecalho_e_rodape_moderno
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
def gerenciar_contrato_pnae(contrato_id):
    contrato = ContratoPNAE.query.get_or_404(contrato_id)
    
    # --- BUSCA AS ESCOLAS ATIVAS (Adicionado para o Modal funcionar) ---
    escolas_ativas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    
    # Lógica para calcular o saldo de cada item
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
                           entregue_por_produto=entregue_por_produto,
                           escolas_ativas=escolas_ativas) # <--- Variável enviada aqui

@merenda_bp.route('/agricultura/contratos/<int:contrato_id>/nova-entrega', methods=['POST'])
@login_required
@role_required('Merenda Escolar', 'admin')
def registrar_entrega_pnae(contrato_id):
    contrato = ContratoPNAE.query.get_or_404(contrato_id)
    
    try:
        # 1. Captura Dados do Formulário
        data_entrega = datetime.strptime(request.form.get('data_entrega'), '%Y-%m-%d').date()
        nota_fiscal = request.form.get('numero_nota_fiscal')
        escola_id = request.form.get('escola_id', type=int) # Novo campo solicitado
        
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
                
                # Dados para o JSON da entrega
                lista_itens_json.append({
                    'item_id': item_contrato.id,
                    'nome_produto': item_contrato.nome_produto,
                    'quantidade': qtd,
                    'preco_unitario': item_contrato.preco_unitario,
                    'valor_total': valor_item
                })
                
                valor_total_entrega += valor_item

                # --- INTEGRAÇÃO AUTOMÁTICA COM ESTOQUE ---
                # Busca ou cria o produto na tabela geral de merenda
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

                # Incrementa o estoque
                produto_estoque.estoque_atual += qtd
                
                # Registra a movimentação de entrada no estoque
                movimento = EstoqueMovimento(
                    produto_id=produto_estoque.id,
                    tipo='Entrada',
                    quantidade=qtd,
                    data_movimento=datetime.combine(data_entrega, datetime.min.time()),
                    fornecedor=f"PNAE: {contrato.agricultor.razao_social}",
                    lote=f"CONT-{contrato.numero_contrato}",
                    usuario_responsavel=session.get('username')
                )
                db.session.add(movimento)

        if not lista_itens_json:
            flash('Informe a quantidade de pelo menos um item.', 'warning')
            return redirect(url_for('merenda.gerenciar_contrato_pnae', contrato_id=contrato.id))

        # 4. Salva o registro da Entrega (Tabela: pnae_entrega)
        nova_entrega = EntregaPNAE(
            contrato_id=contrato.id,
            escola_id=escola_id, # Novo campo vinculado
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
        flash('Entrega registrada, estoque atualizado e anexo salvo na nuvem!', 'success')
        
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

@merenda_bp.route('/agricultura/entrega/<int:entrega_id>/termo-pdf')
@login_required
@role_required('Merenda Escolar', 'admin')
def pdf_termo_recebimento_pnae(entrega_id):
    # 1. Busca os dados da entrega e relações
    entrega = EntregaPNAE.query.get_or_404(entrega_id)
    contrato = entrega.contrato
    agricultor = contrato.agricultor
    
    # 2. Busca a Escola de Destino pelo ID salvo na entrega
    escola_destino = Escola.query.get(entrega.escola_id) if entrega.escola_id else None
    nome_escola = escola_destino.nome if escola_destino else "Unidade Escolar não informada"
    
    # 3. Configuração do Buffer e Documento
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, 
                            rightMargin=2*cm, leftMargin=2*cm, 
                            topMargin=2*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    
    # Estilos customizados
    style_titulo = styles['Heading1']
    style_titulo.alignment = 1  # Centralizado
    style_titulo.fontSize = 14
    
    style_normal = styles['BodyText']
    style_normal.alignment = 4  # Justificado
    style_normal.fontSize = 11
    style_normal.leading = 14
    
    style_assinatura = styles['BodyText']
    style_assinatura.alignment = 1 # Centralizado
    style_assinatura.fontSize = 10

    story = []
    
    # 4. Título e Cabeçalho
    story.append(Paragraph("TERMO DE RECEBIMENTO DA AGRICULTURA FAMILIAR", style_titulo))
    story.append(Spacer(1, 0.8*cm))
    
    # 5. Texto de Atesto com a Escola
    texto_intro = f"""
    Atesto para os devidos fins que foram entregues nesta data, pelo fornecedor <b>{agricultor.razao_social}</b> 
    (CPF/CNPJ: {agricultor.cpf_cnpj}), referente ao Contrato/Chamada Pública nº {contrato.numero_contrato}, 
    destinado à unidade escolar <b>{nome_escola}</b>, os gêneros alimentícios abaixo discriminados:
    """
    story.append(Paragraph(texto_intro, style_normal))
    story.append(Spacer(1, 0.6*cm))
    
    # 6. Tabela de Itens
    dados_tabela = [['Produto', 'Unidade', 'Qtd. Entregue', 'Valor Total']]
    
    # Processa o JSON dos itens salvos na entrega
    if entrega.itens_json:
        try:
            itens = json.loads(entrega.itens_json)
            for item in itens:
                # Busca a unidade de medida no contrato original se não estiver no JSON
                dados_tabela.append([
                    item.get('nome_produto', 'N/A').upper(),
                    "Unid.", 
                    f"{item.get('quantidade', 0)}".replace('.', ','),
                    currency_filter_br(item.get('valor_total', 0))
                ])
        except Exception as e:
            dados_tabela.append([f'Erro ao processar itens: {str(e)}', '', '', ''])
            
    # Linha do Total Geral
    dados_tabela.append(['TOTAL DA ENTREGA', '', '', currency_filter_br(entrega.valor_total)])
    
    # Estilização da Tabela
    t = Table(dados_tabela, colWidths=[8.5*cm, 2.5*cm, 3*cm, 3*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('SPAN', (0, -1), (2, -1)), # Mescla as 3 primeiras colunas da última linha
        ('ALIGN', (0, -1), (0, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]))
    story.append(t)
    story.append(Spacer(1, 2*cm))
    
    # 7. Bloco de Assinaturas
    data_hoje = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    
    # Grid para assinaturas lado a lado
    assinaturas = [
        [
            Paragraph("___________________________________<br/>Responsável pelo Recebimento", style_assinatura),
            Paragraph(f"___________________________________<br/>{agricultor.razao_social}", style_assinatura)
        ]
    ]
    
    t_ass = Table(assinaturas, colWidths=[8.5*cm, 8.5*cm])
    story.append(t_ass)
    
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph(f"<small>Emitido em: {data_hoje}</small>", style_normal))
    
    # 8. Geração Final usando o cabeçalho padrão do sistema
    doc.build(story, onFirstPage=lambda c, d: cabecalho_e_rodape(c, d), 
                     onLaterPages=lambda c, d: cabecalho_e_rodape(c, d))
    
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Termo_Recebimento_{entrega.id}.pdf'
    
    return response

# --- GESTÃO DE RELATÓRIOS TÉCNICOS E OFÍCIOS ---

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

    # Listagem para o GET
    documentos = RelatorioTecnico.query.order_by(RelatorioTecnico.data_emissao.desc()).all()
    return render_template('merenda/relatorios_tecnicos.html', documentos=documentos)


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