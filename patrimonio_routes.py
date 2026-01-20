# patrimonio_routes.py
import qrcode
from models import Secretaria
from utils import role_required
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from extensions import db, bcrypt
from models import Patrimonio, MovimentacaoPatrimonio, Servidor
from utils import login_required, registrar_log # LINHA CORRIGIDA
from sqlalchemy import or_
from datetime import datetime
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as canvas_lib
from reportlab.lib.utils import ImageReader
from utils import upload_arquivo_para_nuvem

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
@role_required('admin', 'RH', 'Fiscal')
def novo_item():
    if request.method == 'POST':
        try:
            # Captura da foto para o Supabase
            foto_file = request.files.get('foto_bem')
            foto_link = None
            if foto_file and foto_file.filename != '':
                from utils import upload_arquivo_para_nuvem
                foto_link = upload_arquivo_para_nuvem(foto_file, pasta="patrimonio")

            # Criando o objeto com os novos campos
            novo_bem = Patrimonio(
                numero_patrimonio=request.form.get('tombamento'),
                descricao=request.form.get('nome_bem'),
                categoria=request.form.get('categoria'),
                marca=request.form.get('marca'),
                modelo=request.form.get('modelo'),
                valor_aquisicao=float(request.form.get('valor_compra') or 0),
                estado_conservacao=request.form.get('estado_conservacao'),
                situacao_uso=request.form.get('situacao_uso'),
                localizacao=request.form.get('localizacao', 'Não informada'),
                observacoes=request.form.get('descricao'),
                foto_url=foto_link,
                servidor_responsavel_cpf=request.form.get('servidor_responsavel_cpf')
            )
            
            db.session.add(novo_bem)
            db.session.commit()
            flash("Patrimônio cadastrado com sucesso!", "success")
            return redirect(url_for('patrimonio.listar_itens'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar: {str(e)}", "danger")

    from models import Servidor, Secretaria
    servidores = Servidor.query.order_by(Servidor.nome).all()
    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    return render_template('patrimonio/form.html', servidores=servidores, secretarias=secretarias, patrimonio=None)


@patrimonio_bp.route('/item/editar/<int:item_id>', methods=['GET', 'POST'])
@login_required
@role_required('Patrimonio', 'admin')
def editar_item(item_id):
    # Busca o item ou retorna 404
    item = Patrimonio.query.get_or_404(item_id)
    
    # Importação local para evitar erros de dependência circular
    from models import Secretaria

    if request.method == 'POST':
        try:
            # 1. Tratamento de Valor de Aquisição
            valor_str = request.form.get('valor_aquisicao', '0').replace('.', '').replace(',', '.')
            item.valor_aquisicao = float(valor_str) if valor_str else 0.0
            
            # 2. Tratamento de Data de Aquisição
            data_str = request.form.get('data_aquisicao')
            if data_str:
                item.data_aquisicao = datetime.strptime(data_str, '%Y-%m-%d').date()

            # 3. Atualização dos campos principais
            # Nota: Usamos 'descricao' para coincidir com o banco de dados
            item.descricao = request.form.get('descricao')
            item.categoria = request.form.get('categoria')
            item.status = request.form.get('status')
            item.observacoes = request.form.get('observacoes')
            
            # 4. Atualização da Secretaria e Responsável
            # Estes campos garantem que o item esteja vinculado corretamente
            item.secretaria_id = request.form.get('secretaria_id')
            item.servidor_responsavel_cpf = request.form.get('servidor_responsavel_cpf')
            
            # 5. Salva no Banco de Dados
            db.session.commit()
            
            # Log e Feedback
            registrar_log(f'Editou o item patrimonial: "{item.descricao}" ({item.numero_patrimonio}).')
            flash("Patrimônio atualizado com sucesso!", "success")
            
            # Redireciona para os detalhes do item recém-editado
            return redirect(url_for('patrimonio.detalhes_item', item_id=item.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar o item: {str(e)}', 'danger')

    # Busca listas para preencher os campos de seleção (Selects) no formulário
    servidores = Servidor.query.order_by(Servidor.nome).all()
    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    
    # Renderiza o formulário enviando o item atual, a lista de servidores e secretarias
    return render_template('patrimonio/form.html', 
                           item=item, 
                           servidores=servidores, 
                           secretarias=secretarias)

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

@patrimonio_bp.route('/<int:id>/etiqueta')
@login_required
def gerar_etiqueta_qr(id):
    bem = Patrimonio.query.get_or_404(id)
    
    buffer = BytesIO()
    p = canvas_lib.Canvas(buffer, pagesize=(200, 100)) # Tamanho de etiqueta pequeno
    
    # Gerar QR Code com o link de consulta
    # O link aponta para a rota de detalhes que já existe no seu sistema
    link_consulta = url_for('patrimonio.detalhes_item', item_id=bem.id, _external=True)
    qr = qrcode.make(link_consulta)
    qr_buffer = BytesIO()
    qr.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)
    
    # Desenhar na etiqueta
    p.setFont("Helvetica-Bold", 8)
    p.drawString(10, 85, "PREFEITURA DE VALENÇA DO PIAUÍ")
    p.setFont("Helvetica", 7)
    p.drawString(10, 75, f"Bem: {bem.descricao[:30]}")
    p.setFont("Helvetica-Bold", 10)
    p.drawString(10, 60, f"TOMBAMENTO: {bem.numero_patrimonio}")
    
    # Inserir QR Code
    p.drawImage(ImageReader(qr_buffer), 130, 10, width=60, height=60)
    
    p.showPage()
    p.save()
    buffer.seek(0)
    
    return send_file(buffer, mimetype='application/pdf', download_name=f'etiqueta_{bem.tombamento}.pdf')