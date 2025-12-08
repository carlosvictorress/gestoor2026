# patrimonio_routes.py
from .utils import role_required
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from .extensions import db, bcrypt
from .models import Patrimonio, MovimentacaoPatrimonio, Servidor
from .utils import login_required, registrar_log # LINHA CORRIGIDA
from sqlalchemy import or_
from datetime import datetime

patrimonio_bp = Blueprint('patrimonio', __name__, url_prefix='/patrimonio')

@patrimonio_bp.route('/')
@login_required
@role_required('Patrimonio', 'admin')
def listar_itens():
    query = Patrimonio.query
    termo_busca = request.args.get('termo', '')

    if termo_busca:
        search_pattern = f"%{termo_busca}%"
        query = query.filter(or_(
            Patrimonio.numero_patrimonio.ilike(search_pattern),
            Patrimonio.descricao.ilike(search_pattern),
            Patrimonio.localizacao.ilike(search_pattern)
        ))
        
    itens = query.order_by(Patrimonio.descricao).all()
    return render_template('patrimonio/lista.html', itens=itens, termo_busca=termo_busca)

@patrimonio_bp.route('/item/novo', methods=['GET', 'POST'])
@login_required
@role_required('Patrimonio', 'admin')
def novo_item():
    if request.method == 'POST':
        numero_patrimonio = request.form.get('numero_patrimonio')
        # Verifica se o número de patrimônio já existe
        if Patrimonio.query.filter_by(numero_patrimonio=numero_patrimonio).first():
            flash('Este número de patrimônio já está cadastrado.', 'danger')
            return redirect(url_for('patrimonio.novo_item'))
        
        try:
            valor_str = request.form.get('valor_aquisicao', '0').replace('.', '').replace(',', '.')
            valor = float(valor_str) if valor_str else None
            
            data_str = request.form.get('data_aquisicao')
            data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else None

            novo = Patrimonio(
                numero_patrimonio=numero_patrimonio,
                descricao=request.form.get('descricao'),
                categoria=request.form.get('categoria'),
                status=request.form.get('status'),
                localizacao=request.form.get('localizacao'),
                data_aquisicao=data,
                valor_aquisicao=valor,
                observacoes=request.form.get('observacoes'),
                servidor_responsavel_cpf=request.form.get('servidor_responsavel_cpf') or None
            )
            db.session.add(novo)
            db.session.commit()
            registrar_log(f'Cadastrou o item patrimonial: "{novo.descricao}" ({novo.numero_patrimonio}).')
            flash('Item patrimonial cadastrado com sucesso!', 'success')
            return redirect(url_for('patrimonio.listar_itens'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar o item: {e}', 'danger')

    servidores = Servidor.query.order_by(Servidor.nome).all()
    return render_template('patrimonio/form.html', servidores=servidores, item=None)


@patrimonio_bp.route('/item/editar/<int:item_id>', methods=['GET', 'POST'])
@login_required
@role_required('Patrimonio', 'admin')
def editar_item(item_id):
    item = Patrimonio.query.get_or_404(item_id)
    if request.method == 'POST':
        try:
            valor_str = request.form.get('valor_aquisicao', '0').replace('.', '').replace(',', '.')
            item.valor_aquisicao = float(valor_str) if valor_str else None
            
            data_str = request.form.get('data_aquisicao')
            item.data_aquisicao = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else None

            item.descricao = request.form.get('descricao')
            item.categoria = request.form.get('categoria')
            item.status = request.form.get('status')
            item.observacoes = request.form.get('observacoes')
            
            db.session.commit()
            registrar_log(f'Editou o item patrimonial: "{item.descricao}" ({item.numero_patrimonio}).')
            flash('Item atualizado com sucesso!', 'success')
            return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar o item: {e}', 'danger')

    servidores = Servidor.query.order_by(Servidor.nome).all()
    return render_template('patrimonio/form.html', servidores=servidores, item=item)

@patrimonio_bp.route('/item/detalhes/<int:item_id>')
@login_required
@role_required('Patrimonio', 'admin')
def detalhes_item(item_id):
    item = Patrimonio.query.get_or_404(item_id)
    servidores = Servidor.query.order_by(Servidor.nome).all()
    movimentacoes = MovimentacaoPatrimonio.query.filter_by(patrimonio_id=item_id).order_by(MovimentacaoPatrimonio.data_movimentacao.desc()).all()
    return render_template('patrimonio/detalhes.html', item=item, servidores=servidores, movimentacoes=movimentacoes)

@patrimonio_bp.route('/item/transferir/<int:item_id>', methods=['POST'])
@login_required
@role_required('Patrimonio', 'admin')
def transferir_item(item_id):
    item = Patrimonio.query.get_or_404(item_id)
    
    # Dados atuais (origem)
    local_origem = item.localizacao
    responsavel_anterior_cpf = item.servidor_responsavel_cpf
    
    # Novos dados (destino)
    novo_local = request.form.get('local_destino')
    novo_responsavel_cpf = request.form.get('servidor_responsavel_cpf') or None

    if not novo_local:
        flash('O novo local é obrigatório para a transferência.', 'warning')
        return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))
        
    try:
        # 1. Cria o registro de movimentação
        movimentacao = MovimentacaoPatrimonio(
            patrimonio_id=item.id,
            local_origem=local_origem,
            responsavel_anterior_cpf=responsavel_anterior_cpf,
            local_destino=novo_local,
            responsavel_novo_cpf=novo_responsavel_cpf,
            usuario_registro=session.get('username')
        )
        db.session.add(movimentacao)
        
        # 2. Atualiza o registro do item
        item.localizacao = novo_local
        item.servidor_responsavel_cpf = novo_responsavel_cpf
        
        db.session.commit()
        registrar_log(f'Transferiu o item "{item.descricao}" para "{novo_local}".')
        flash('Item transferido e histórico registrado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao transferir o item: {e}', 'danger')
        
    return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))

@patrimonio_bp.route('/termos_responsabilidade')
@login_required
@role_required('Patrimonio', 'admin') # Ajuste as permissões conforme necessário
def listar_termos_responsabilidade():
    """
    Rota para listar e gerenciar os Termos de Responsabilidade.
    """
    # 1. Lógica para buscar os termos de responsabilidade no banco de dados
    # (Exemplo: termos = TermoResponsabilidade.query.all())

    # 2. Renderizar o template
    return render_template('patrimonio/termos_responsabilidade.html', termos=[]) # Troque o [] pela sua variável de termos
