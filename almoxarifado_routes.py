import io
import csv
import qrcode
import base64
from datetime import datetime
from flask import Response # Certifique-se de que 'Response' está a ser importado do flask
from flask import Blueprint, render_template, request, flash, redirect, url_for, session
from models import Material, Fornecedor, MovimentoEstoque, Requisicao, RequisicaoItem, Secretaria
from extensions import db
from utils import role_required # Supondo que você tem um decorador de permissão

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

almoxarifado_bp = Blueprint('almoxarifado', __name__, url_prefix='/almoxarifado')

# Rota para o painel principal do almoxarifado
@almoxarifado_bp.route('/')
@role_required('admin', 'almoxarifado') # Protege o acesso
def dashboard():
    # --- NOVAS CONSULTAS ---
    materiais = Material.query.all()
    valor_total_estoque = sum(m.estoque_atual * m.ultimo_custo for m in materiais)
    
    req_pendentes = Requisicao.query.filter_by(status='Pendente').count()
    req_aprovadas = Requisicao.query.filter_by(status='Aprovada').count()
    
    materiais_baixo_estoque = Material.query.filter(Material.estoque_atual <= Material.estoque_minimo).all()
    total_itens = len(materiais)
    
    return render_template('almoxarifado/dashboard.html', 
                           total_itens=total_itens,
                           materiais_baixo_estoque=materiais_baixo_estoque,
                           valor_total_estoque=valor_total_estoque,
                           req_pendentes=req_pendentes,
                           req_aprovadas=req_aprovadas
                          )

# Rota para gerenciar Materiais (Listar e Adicionar)
@almoxarifado_bp.route('/materiais', methods=['GET', 'POST'])
@role_required('RH', 'admin', 'Combustivel')
def gerenciar_materiais():
    if request.method == 'POST':
        try:
            novo_material = Material(
                descricao=request.form['descricao'],
                unidade_medida=request.form['unidade_medida'],
                categoria=request.form.get('categoria'),
                estoque_minimo=float(request.form.get('estoque_minimo', 0)),
                estoque_maximo=float(request.form.get('estoque_maximo', 0)),
                localizacao_fisica=request.form.get('localizacao_fisica')
            )
            db.session.add(novo_material)
            db.session.commit()
            flash('Material cadastrado com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar material: {e}', 'danger')
        return redirect(url_for('almoxarifado.gerenciar_materiais'))
    
    materiais = Material.query.order_by(Material.descricao).all()
    return render_template('almoxarifado/materiais.html', materiais=materiais)

# Rota para gerenciar Fornecedores (Listar e Adicionar)
@almoxarifado_bp.route('/fornecedores', methods=['GET', 'POST'])
@role_required('RH', 'admin', 'Combustivel')
def gerenciar_fornecedores():
    if request.method == 'POST':
        try:
            novo_fornecedor = Fornecedor(
                nome=request.form['nome'],
                cnpj=request.form.get('cnpj'),
                endereco=request.form.get('endereco'),
                telefone=request.form.get('telefone'),
                email=request.form.get('email')
            )
            db.session.add(novo_fornecedor)
            db.session.commit()
            flash('Fornecedor cadastrado com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar fornecedor: {e}', 'danger')
        return redirect(url_for('almoxarifado.gerenciar_fornecedores'))

    fornecedores = Fornecedor.query.order_by(Fornecedor.nome).all()
    return render_template('almoxarifado/fornecedores.html', fornecedores=fornecedores)
	
@almoxarifado_bp.route('/materiais/entrada/<int:material_id>', methods=['GET', 'POST'])
@role_required('RH', 'admin', 'Combustivel')
def entrada_material(material_id):
    material = Material.query.get_or_404(material_id)

    if request.method == 'POST':
        try:
            quantidade_str = request.form.get('quantidade', '0').replace(',', '.')
            valor_unitario_str = request.form.get('valor_unitario', '0').replace(',', '.')

            quantidade = float(quantidade_str)
            valor_unitario = float(valor_unitario_str)
            
            if quantidade <= 0:
                flash('A quantidade deve ser maior que zero.', 'warning')
                return redirect(url_for('almoxarifado.entrada_material', material_id=material.id))

            # Atualiza o estoque e o custo do material
            material.estoque_atual += quantidade
            if valor_unitario > 0:
                material.ultimo_custo = valor_unitario

            # Cria o registro de movimentação para rastreabilidade
            novo_movimento = MovimentoEstoque(
                material_id=material.id,
                tipo_movimento='Entrada NF', # Pode ser alterado para outros tipos no futuro
                quantidade=quantidade,
                valor_unitario=valor_unitario,
                nota_fiscal=request.form.get('nota_fiscal'),
                justificativa=request.form.get('justificativa'),
                usuario_id=session.get('user_id') # Assumindo que você salva o ID do usuário na sessão
            )

            db.session.add(novo_movimento)
            db.session.commit()
            flash(f'Entrada de {quantidade} unidade(s) de "{material.descricao}" registada com sucesso!', 'success')
            return redirect(url_for('almoxarifado.gerenciar_materiais'))

        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao registar a entrada: {e}', 'danger')

    return render_template('almoxarifado/entrada_material.html', material=material)	
	
	
# (No final de almoxarifado_routes.py)

@almoxarifado_bp.route('/requisicoes')
@role_required('admin', 'almoxarifado', 'operador', 'RH') # Permitir que usuários comuns vejam
def minhas_requisicoes():
    secretaria_id_logada = session.get('secretaria_id')
    
    query = Requisicao.query.filter_by(secretaria_solicitante_id=secretaria_id_logada)
    
    # O admin do almoxarifado vê todas
    if session.get('role') in ['admin', 'almoxarifado']:
        query = Requisicao.query
        
    requisicoes = query.order_by(Requisicao.data_solicitacao.desc()).all()
    
    return render_template('almoxarifado/minhas_requisicoes.html', requisicoes=requisicoes)


@almoxarifado_bp.route('/requisicoes/nova', methods=['GET', 'POST'])
@role_required('admin', 'almoxarifado', 'operador', 'RH')
def nova_requisicao():
    if request.method == 'POST':
        try:
            # Pega os dados do formulário
            justificativa = request.form.get('justificativa')
            material_ids = request.form.getlist('material_id[]')
            quantidades = request.form.getlist('quantidade[]')

            if not material_ids:
                flash('Você precisa adicionar pelo menos um item à requisição.', 'warning')
                return redirect(url_for('almoxarifado.nova_requisicao'))

            # Cria a requisição principal
            nova_req = Requisicao(
                justificativa=justificativa,
                secretaria_solicitante_id=session['secretaria_id'],
                usuario_solicitante_id=session['user_id']
            )
            db.session.add(nova_req)
            
            # Adiciona os itens à requisição
            for i, material_id in enumerate(material_ids):
                quantidade_str = quantidades[i].replace(',', '.')
                quantidade = float(quantidade_str)
                if quantidade > 0:
                    item = RequisicaoItem(
                        requisicao=nova_req,
                        material_id=int(material_id),
                        quantidade_solicitada=quantidade
                    )
                    db.session.add(item)
            
            db.session.commit()
            flash('Requisição enviada com sucesso!', 'success')
            return redirect(url_for('almoxarifado.minhas_requisicoes'))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao criar requisição: {e}', 'danger')

    # Para o método GET (carregar a página)
    materiais = Material.query.order_by(Material.descricao).all()
    return render_template('almoxarifado/nova_requisicao.html', materiais=materiais)	
	
@almoxarifado_bp.route('/requisicoes/detalhes/<int:requisicao_id>', methods=['GET', 'POST'])
@role_required('admin', 'almoxarifado') # Apenas admin/almoxarifado pode atender
def detalhes_requisicao(requisicao_id):
    requisicao = Requisicao.query.get_or_404(requisicao_id)

    if request.method == 'POST':
        try:
            # Dicionários para guardar as quantidades atendidas e os materiais
            quantidades_atendidas = request.form.getlist('quantidade_atendida[]')
            item_ids = request.form.getlist('item_id[]')
            
            # Mapeia o ID do item para a quantidade atendida
            atendimento_map = {int(item_ids[i]): float(quantidades_atendidas[i].replace(',', '.')) for i in range(len(item_ids))}

            for item in requisicao.itens:
                if item.id in atendimento_map:
                    quantidade_a_retirar = atendimento_map[item.id]

                    # Validação para não deixar o estoque negativo
                    if quantidade_a_retirar > item.material.estoque_atual:
                        flash(f'Erro: Estoque insuficiente para o item "{item.material.descricao}".', 'danger')
                        db.session.rollback()
                        return redirect(url_for('almoxarifado.detalhes_requisicao', requisicao_id=requisicao.id))

                    if quantidade_a_retirar > 0:
                        # Atualiza a quantidade atendida no item da requisição
                        item.quantidade_atendida = quantidade_a_retirar
                        
                        # Deduz do estoque principal do material
                        item.material.estoque_atual -= quantidade_a_retirar

                        # Cria o registro de movimentação de SAÍDA
                        movimento_saida = MovimentoEstoque(
                            material_id=item.material_id,
                            tipo_movimento='Saída Requisição',
                            quantidade= -quantidade_a_retirar, # Quantidade negativa para indicar saída
                            justificativa=f"Atendimento à requisição #{requisicao.id}",
                            usuario_id=session['user_id'],
                            requisicao_item_id=item.id
                        )
                        db.session.add(movimento_saida)
            
            # Atualiza o status geral da requisição
            requisicao.status = 'Atendida'
            db.session.commit()
            flash('Requisição atendida com sucesso e estoque atualizado!', 'success')
            return redirect(url_for('almoxarifado.minhas_requisicoes'))

        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao atender a requisição: {e}', 'danger')

    return render_template('almoxarifado/detalhes_requisicao.html', requisicao=requisicao)	
	
# (No final de almoxarifado_routes.py)

@almoxarifado_bp.route('/relatorios/estoque')
@role_required('admin', 'almoxarifado')
def relatorio_estoque():
    materiais = Material.query.order_by(Material.descricao).all()
    
    # Calcula o valor total do estoque
    valor_total_estoque = sum(m.estoque_atual * m.ultimo_custo for m in materiais)
    
    return render_template(
        'almoxarifado/relatorio_estoque.html', 
        materiais=materiais,
        valor_total_estoque=valor_total_estoque
    )

@almoxarifado_bp.route('/relatorios/estoque/exportar')
@role_required('admin', 'almoxarifado')
def exportar_relatorio_estoque_csv():
    materiais = Material.query.order_by(Material.descricao).all()
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    
    # Escreve o cabeçalho
    header = [
        'ID', 'Descricao', 'Categoria', 'Unidade de Medida', 
        'Estoque Atual', 'Custo Unitario', 'Valor Total em Estoque'
    ]
    writer.writerow(header)
    
    # Escreve os dados de cada material
    for material in materiais:
        valor_total_item = material.estoque_atual * material.ultimo_custo
        row = [
            material.id,
            material.descricao,
            material.categoria,
            material.unidade_medida,
            str(material.estoque_atual).replace('.', ','),
            str(material.ultimo_custo).replace('.', ','),
            str(valor_total_item).replace('.', ',')
        ]
        writer.writerow(row)
    
    csv_content = output.getvalue()
    
    response = Response(
        csv_content.encode('utf-8-sig'), # utf-8-sig para compatibilidade com Excel
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment;filename=relatorio_estoque_{datetime.now().strftime("%Y-%m-%d")}.csv'
        }
    )
    
    return response

# (No final de almoxarifado_routes.py)

# --- ROTAS DE EDIÇÃO E EXCLUSÃO DE MATERIAIS ---

@almoxarifado_bp.route('/materiais/editar/<int:material_id>', methods=['POST'])
@role_required('admin', 'almoxarifado')
def editar_material(material_id):
    material = Material.query.get_or_404(material_id)
    try:
        material.descricao = request.form['descricao']
        material.unidade_medida = request.form['unidade_medida']
        material.categoria = request.form.get('categoria')
        material.estoque_minimo = float(request.form.get('estoque_minimo', 0))
        material.estoque_maximo = float(request.form.get('estoque_maximo', 0))
        material.localizacao_fisica = request.form.get('localizacao_fisica')
        db.session.commit()
        flash('Material atualizado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar o material: {e}', 'danger')
    return redirect(url_for('almoxarifado.gerenciar_materiais'))

@almoxarifado_bp.route('/materiais/excluir/<int:material_id>', methods=['POST'])
@role_required('admin', 'almoxarifado')
def excluir_material(material_id):
    material = Material.query.get_or_404(material_id)
    # Proteção: Não permite excluir se houver estoque
    if material.estoque_atual > 0:
        flash('Não é possível excluir um material com estoque. Por favor, ajuste o estoque para zero primeiro.', 'danger')
        return redirect(url_for('almoxarifado.gerenciar_materiais'))
    try:
        db.session.delete(material)
        db.session.commit()
        flash('Material excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir o material. Verifique se ele não está vinculado a requisições. Erro: {e}', 'danger')
    return redirect(url_for('almoxarifado.gerenciar_materiais'))


# --- ROTAS DE EDIÇÃO E EXCLUSÃO DE FORNECEDORES ---

@almoxarifado_bp.route('/fornecedores/editar/<int:fornecedor_id>', methods=['POST'])
@role_required('admin', 'almoxarifado')
def editar_fornecedor(fornecedor_id):
    fornecedor = Fornecedor.query.get_or_404(fornecedor_id)
    try:
        fornecedor.nome = request.form['nome']
        fornecedor.cnpj = request.form.get('cnpj')
        fornecedor.endereco = request.form.get('endereco')
        fornecedor.telefone = request.form.get('telefone')
        fornecedor.email = request.form.get('email')
        db.session.commit()
        flash('Fornecedor atualizado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar o fornecedor: {e}', 'danger')
    return redirect(url_for('almoxarifado.gerenciar_fornecedores'))

@almoxarifado_bp.route('/fornecedores/excluir/<int:fornecedor_id>', methods=['POST'])
@role_required('admin', 'almoxarifado')
def excluir_fornecedor(fornecedor_id):
    fornecedor = Fornecedor.query.get_or_404(fornecedor_id)
    try:
        db.session.delete(fornecedor)
        db.session.commit()
        flash('Fornecedor excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir fornecedor. Verifique se não há materiais vinculados a ele. Erro: {e}', 'danger')
    return redirect(url_for('almoxarifado.gerenciar_fornecedores'))


# (No final de almoxarifado_routes.py)

@almoxarifado_bp.route('/requisicoes/avaliar/<int:requisicao_id>', methods=['POST'])
@role_required('admin', 'almoxarifado') # Apenas admin pode aprovar/recusar
def avaliar_requisicao(requisicao_id):
    requisicao = Requisicao.query.get_or_404(requisicao_id)
    nova_acao = request.form.get('acao') # Espera 'Aprovar' ou 'Recusar'

    if requisicao.status != 'Pendente':
        flash('Esta requisição já foi avaliada.', 'warning')
        return redirect(url_for('almoxarifado.detalhes_requisicao', requisicao_id=requisicao.id))

    if nova_acao == 'Aprovar':
        requisicao.status = 'Aprovada'
        flash('Requisição APROVADA com sucesso.', 'info')
    elif nova_acao == 'Recusar':
        requisicao.status = 'Recusada'
        flash('Requisição RECUSADA.', 'warning')
    else:
        flash('Ação inválida.', 'danger')
        return redirect(url_for('almoxarifado.detalhes_requisicao', requisicao_id=requisicao.id))

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao atualizar o status: {e}', 'danger')

    return redirect(url_for('almoxarifado.detalhes_requisicao', requisicao_id=requisicao.id))


# (No final de almoxarifado_routes.py)

@almoxarifado_bp.route('/relatorios/movimentacoes')
@role_required('admin', 'almoxarifado')
def relatorio_movimentacoes():
    # Inicia a consulta base
    query = MovimentoEstoque.query

    # Pega os filtros da URL
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    material_id = request.args.get('material_id', type=int)
    tipo_movimento = request.args.get('tipo_movimento')

    # Aplica os filtros se eles existirem
    if data_inicio_str:
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d')
        query = query.filter(MovimentoEstoque.data_movimento >= data_inicio)
    
    if data_fim_str:
        data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        query = query.filter(MovimentoEstoque.data_movimento <= data_fim)

    if material_id:
        query = query.filter(MovimentoEstoque.material_id == material_id)

    if tipo_movimento:
        query = query.filter(MovimentoEstoque.tipo_movimento == tipo_movimento)

    movimentacoes = query.order_by(MovimentoEstoque.data_movimento.desc()).all()

    # Busca os materiais para popular o filtro dropdown
    materiais_para_filtro = Material.query.order_by(Material.descricao).all()
    
    return render_template(
        'almoxarifado/relatorio_movimentacoes.html',
        movimentacoes=movimentacoes,
        materiais=materiais_para_filtro,
        # Envia os filtros de volta para a página para manter os campos preenchidos
        data_inicio=data_inicio_str,
        data_fim=data_fim_str,
        material_id_selecionado=material_id,
        tipo_movimento_selecionado=tipo_movimento
    )

# (No final de almoxarifado_routes.py)

@almoxarifado_bp.route('/materiais/ajustar/<int:material_id>', methods=['GET', 'POST'])
@role_required('admin', 'almoxarifado')
def ajustar_estoque(material_id):
    material = Material.query.get_or_404(material_id)

    if request.method == 'POST':
        try:
            nova_quantidade_str = request.form.get('nova_quantidade', '').replace(',', '.')
            justificativa = request.form.get('justificativa')

            if not nova_quantidade_str or not justificativa:
                flash('Ambos os campos, nova quantidade e justificativa, são obrigatórios.', 'warning')
                return redirect(url_for('almoxarifado.ajustar_estoque', material_id=material.id))

            nova_quantidade = float(nova_quantidade_str)
            quantidade_ajustada = nova_quantidade - material.estoque_atual

            # Se não houver alteração, não faz nada
            if quantidade_ajustada == 0:
                flash('A nova quantidade é igual ao estoque atual. Nenhum ajuste foi feito.', 'info')
                return redirect(url_for('almoxarifado.gerenciar_materiais'))

            # Atualiza o estoque do material
            material.estoque_atual = nova_quantidade

            # Cria o registro de movimentação para rastreabilidade
            novo_movimento = MovimentoEstoque(
                material_id=material.id,
                tipo_movimento='Ajuste Inventário',
                quantidade=quantidade_ajustada, # Registra a diferença (positiva ou negativa)
                justificativa=justificativa,
                usuario_id=session.get('user_id')
            )

            db.session.add(novo_movimento)
            db.session.commit()
            flash(f'Estoque de "{material.descricao}" ajustado com sucesso!', 'success')
            return redirect(url_for('almoxarifado.gerenciar_materiais'))

        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao ajustar o estoque: {e}', 'danger')

    return render_template('almoxarifado/ajuste_estoque.html', material=material)	
	
# (No final de almoxarifado_routes.py)

def gerar_pdf_comprovante(requisicao):
    """Função auxiliar para criar o PDF de uma requisição."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    style_normal = styles['Normal']
    style_heading = styles['h2']
    style_heading.alignment = TA_CENTER
    
    story = []
    
    story.append(Paragraph("Comprovativo de Entrega de Material", style_heading))
    story.append(Spacer(1, 1*cm))
    
    # Informações da Requisição
    info_data = [
        ['Requisição Nº:', str(requisicao.id)],
        ['Data da Solicitação:', requisicao.data_solicitacao.strftime('%d/%m/%Y')],
        ['Secretaria Solicitante:', requisicao.secretaria_solicitante.nome],
        ['Solicitante:', requisicao.usuario_solicitante.username],
    ]
    info_table = Table(info_data, colWidths=[5*cm, 10*cm])
    info_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (0,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 1*cm))
    
    story.append(Paragraph("Itens Entregues:", styles['h3']))
    
    # Tabela de Itens
    items_header = [['Item', 'Quantidade Atendida', 'Unidade']]
    items_data = []
    for item in requisicao.itens:
        if item.quantidade_atendida > 0:
            items_data.append([
                Paragraph(item.material.descricao, style_normal), 
                item.quantidade_atendida, 
                item.material.unidade_medida
            ])
    
    items_table = Table(items_header + items_data, colWidths=[10*cm, 3*cm, 3*cm])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('INNERGRID', (0,0), (-1,-1), 0.25, colors.black),
        ('BOX', (0,0), (-1,-1), 0.25, colors.black),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 3*cm))
    
    # Assinatura
    story.append(Paragraph("________________________________________", style_center))
    story.append(Paragraph("Assinatura do Recebedor", style_center))
    
    doc.build(story)
    buffer.seek(0)
    return buffer

@almoxarifado_bp.route('/requisicoes/comprovante/<int:requisicao_id>')
@role_required('admin', 'almoxarifado')
def comprovante_requisicao_pdf(requisicao_id):
    requisicao = Requisicao.query.get_or_404(requisicao_id)
    if requisicao.status != 'Atendida':
        flash('Só é possível gerar comprovativo de requisições já atendidas.', 'warning')
        return redirect(url_for('almoxarifado.detalhes_requisicao', requisicao_id=requisicao_id))
        
    pdf_buffer = gerar_pdf_comprovante(requisicao)
    
    response = Response(
        pdf_buffer.getvalue(),
        mimetype='application/pdf',
        headers={
            'Content-Disposition': f'inline;filename=comprovante_req_{requisicao.id}.pdf'
        }
    )
    return response

# Helper style para a assinatura
style_center = getSampleStyleSheet()['Normal']
style_center.alignment = TA_CENTER	

# (No final de almoxarifado_routes.py)

@almoxarifado_bp.route('/inventario', methods=['GET', 'POST'])
@role_required('admin', 'almoxarifado')
def iniciar_inventario():
    if request.method == 'POST':
        try:
            # Pega todos os dados enviados pelo formulário
            materiais_contados = request.form.getlist('material_id[]')
            quantidades_contadas = request.form.getlist('quantidade_contada[]')
            justificativa = request.form.get('justificativa_geral')

            if not justificativa:
                flash('A justificativa geral para o inventário é obrigatória.', 'danger')
                return redirect(url_for('almoxarifado.iniciar_inventario'))

            ajustes_realizados = 0
            # Itera sobre cada material enviado
            for i, material_id in enumerate(materiais_contados):
                # Só processa se uma quantidade foi digitada
                if quantidades_contadas[i]:
                    material = Material.query.get(int(material_id))
                    quantidade_fisica = float(quantidades_contadas[i].replace(',', '.'))
                    
                    # Calcula a diferença
                    diferenca = quantidade_fisica - material.estoque_atual

                    # Se houver diferença, cria o ajuste
                    if diferenca != 0:
                        ajustes_realizados += 1
                        
                        # Atualiza o estoque do material
                        material.estoque_atual = quantidade_fisica

                        # Cria o movimento de ajuste
                        movimento_ajuste = MovimentoEstoque(
                            material_id=material.id,
                            tipo_movimento='Ajuste Inventário',
                            quantidade=diferenca, # Registra a diferença (pode ser + ou -)
                            justificativa=justificativa,
                            usuario_id=session['user_id']
                        )
                        db.session.add(movimento_ajuste)
            
            db.session.commit()
            flash(f'Inventário processado com sucesso! {ajustes_realizados} item(ns) tiveram o estoque ajustado.', 'success')
            return redirect(url_for('almoxarifado.relatorio_movimentacoes')) # Redireciona para ver os ajustes

        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao processar o inventário: {e}', 'danger')
    
    # Lógica para carregar a página (GET)
    materiais = Material.query.order_by(Material.descricao).all()
    return render_template('almoxarifado/inventario.html', materiais=materiais)
	
# (No final de almoxarifado_routes.py)

@almoxarifado_bp.route('/materiais/qrcode/<int:material_id>')
@role_required('admin', 'almoxarifado')
def gerar_qrcode_material(material_id):
    material = Material.query.get_or_404(material_id)
    
    # O conteúdo do QR Code. Pode ser um JSON, um ID, ou uma URL.
    # Usaremos um formato simples para facilitar a leitura futura por um app.
    qr_content = f"gestoor360_material_id:{material.id}"
    
    # Gera a imagem do QR Code em memória
    img = qrcode.make(qr_content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    
    # Converte a imagem para uma string base64 para ser usada no HTML
    img_str = base64.b64encode(buf.getvalue()).decode("utf-8")
    
    return render_template('almoxarifado/qrcode_material.html', material=material, qr_code_image=img_str)	
	
	