# patrimonio_routes.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session,
    make_response, current_app, send_from_directory, send_file, jsonify
)
import qrcode
from models import Secretaria
from utils import role_required
from extensions import db, bcrypt
from models import Patrimonio, MovimentacaoPatrimonio, Servidor
from utils import login_required, registrar_log
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
            # 1. Tratamento do Tombamento (Opcional)
            tombamento = request.form.get('tombamento')
            if not tombamento or tombamento.strip() == "":
                tombamento = None

            # 2. Captura da foto para o Supabase
            foto_file = request.files.get('foto_bem')
            foto_link = None
            if foto_file and foto_file.filename != '':
                from utils import upload_arquivo_para_nuvem
                foto_link = upload_arquivo_para_nuvem(foto_file, pasta="patrimonio")

            # 3. Tratamento de valores numéricos (CORRIGIDO PARA O PADRÃO BRASILEIRO)
            valor_compra_raw = request.form.get('valor_compra')
            if valor_compra_raw and valor_compra_raw.strip():
                # Remove pontos de milhar e troca a vírgula por ponto para o Python entender
                valor_str = valor_compra_raw.replace('.', '').replace(',', '.')
                valor_final = float(valor_str)
            else:
                valor_final = 0.0

            # 4. Tratamento do ID da Secretaria e Servidor Responsável (Evita erro de Foreign Key)
            sec_id = request.form.get('secretaria_id')
            secretaria_id_final = int(sec_id) if sec_id and sec_id.isdigit() else None

            resp_cpf = request.form.get('servidor_responsavel_cpf')
            responsavel_cpf_final = resp_cpf if resp_cpf and resp_cpf.strip() != "" else None

            # 5. Criando o objeto com as novas validações blindadas
            novo_bem = Patrimonio(
                numero_patrimonio=tombamento,
                descricao=request.form.get('nome_bem'),
                categoria=request.form.get('categoria'),
                marca=request.form.get('marca'),
                modelo=request.form.get('modelo'),
                valor_aquisicao=valor_final,
                estado_conservacao=request.form.get('estado_conservacao'),
                situacao_uso=request.form.get('situacao_uso'),
                localizacao=request.form.get('localizacao', 'Não informada'),
                observacoes=request.form.get('descricao'),
                foto_url=foto_link,
                servidor_responsavel_cpf=responsavel_cpf_final,
                secretaria_id=secretaria_id_final 
            )
            
            db.session.add(novo_bem)
            db.session.commit()
            
            # Registrar Log
            try:
                from app import registrar_log
                registrar_log(f"Cadastrou novo patrimônio: {novo_bem.descricao} (Tomb: {tombamento or 'S/N'})")
            except:
                pass

            flash("Patrimônio cadastrado com sucesso!", "success")
            return redirect(url_for('patrimonio.listar_itens'))
            
        except Exception as e:
            db.session.rollback()
            erro_str = str(e)
            print(f"Erro ao cadastrar patrimônio: {erro_str}")
            
            # 6. MENSAGEM DE ERRO MELHORADA (Aponta o que deu errado)
            if "UNIQUE constraint failed" in erro_str or "Duplicate" in erro_str:
                flash("Erro: O Número de Tombamento informado já existe cadastrado em outro item.", "danger")
            elif "NOT NULL constraint failed" in erro_str:
                flash("Erro: Você esqueceu de preencher um campo obrigatório (Ex: Nome do Bem).", "danger")
            else:
                # Mostra parte do erro real para ajudar no debug
                flash(f"Falha ao salvar no banco de dados. Erro técnico: {erro_str[:50]}...", "danger")

    # Busca de dados para carregar o formulário (GET)
    from models import Servidor, Secretaria
    servidores = Servidor.query.order_by(Servidor.nome).all()
    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    
    return render_template('patrimonio/form.html', 
                           servidores=servidores, 
                           secretarias=secretarias, 
                           patrimonio=None)


@patrimonio_bp.route('/item/editar/<int:item_id>', methods=['GET', 'POST'])
@login_required
@role_required('Patrimonio', 'admin')
def editar_item(item_id):
    item = Patrimonio.query.get_or_404(item_id)
    from models import Secretaria

    if request.method == 'POST':
        try:
            # Tratamento de Valor de Aquisição (Corrigido também na edição)
            valor_str = request.form.get('valor_aquisicao', '0').replace('.', '').replace(',', '.')
            item.valor_aquisicao = float(valor_str) if valor_str else 0.0
            
            data_str = request.form.get('data_aquisicao')
            if data_str:
                item.data_aquisicao = datetime.strptime(data_str, '%Y-%m-%d').date()

            item.descricao = request.form.get('descricao')
            item.categoria = request.form.get('categoria')
            item.status = request.form.get('status')
            item.observacoes = request.form.get('observacoes')
            
            # Tratamento da Secretaria e Responsável
            sec_id = request.form.get('secretaria_id')
            item.secretaria_id = int(sec_id) if sec_id and sec_id.isdigit() else None
            
            resp_cpf = request.form.get('servidor_responsavel_cpf')
            item.servidor_responsavel_cpf = resp_cpf if resp_cpf and resp_cpf.strip() != "" else None
            
            db.session.commit()
            
            registrar_log(f'Editou o item patrimonial: "{item.descricao}" ({item.numero_patrimonio}).')
            flash("Patrimônio atualizado com sucesso!", "success")
            
            return redirect(url_for('patrimonio.detalhes_item', item_id=item.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar o item: {str(e)}', 'danger')

    servidores = Servidor.query.order_by(Servidor.nome).all()
    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    
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
    
    local_origem = item.localizacao
    responsavel_anterior_cpf = item.servidor_responsavel_cpf
    
    novo_local = request.form.get('local_destino')
    novo_responsavel_cpf = request.form.get('servidor_responsavel_cpf') or None

    if not novo_local:
        flash('O novo local é obrigatório para a transferência.', 'warning')
        return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))
        
    try:
        movimentacao = MovimentacaoPatrimonio(
            patrimonio_id=item.id,
            local_origem=local_origem,
            responsavel_anterior_cpf=responsavel_anterior_cpf,
            local_destino=novo_local,
            responsavel_novo_cpf=novo_responsavel_cpf,
            usuario_registro=session.get('username')
        )
        db.session.add(movimentacao)
        
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
@role_required('Patrimonio', 'admin')
def listar_termos_responsabilidade():
    return render_template('patrimonio/termos_responsabilidade.html', termos=[])

@patrimonio_bp.route('/<int:id>/etiqueta')
@login_required
@role_required('Patrimonio', 'admin')
def gerar_etiqueta_qr(id):
    bem = Patrimonio.query.get_or_404(id)
    
    try:
        buffer = BytesIO()
        p = canvas_lib.Canvas(buffer, pagesize=(200, 100))
        
        link_consulta = url_for('patrimonio.detalhes_item', item_id=bem.id, _external=True)
        qr = qrcode.make(link_consulta)
        qr_buffer = BytesIO()
        qr.save(qr_buffer, format='PNG')
        qr_buffer.seek(0)
        
        p.setFont("Helvetica-Bold", 8)
        p.drawString(10, 85, "PREFEITURA DE VALENÇA DO PIAUÍ")
        
        p.setFont("Helvetica", 7)
        p.drawString(10, 75, f"Bem: {bem.descricao[:30]}")
        
        p.setFont("Helvetica-Bold", 9)
        p.drawString(10, 60, f"PATRIMÔNIO: {bem.numero_patrimonio}")
        
        qr_img = ImageReader(qr_buffer)
        p.drawImage(qr_img, 130, 10, width=60, height=60)
        
        p.setFont("Helvetica-Oblique", 6)
        p.drawString(10, 10, "Escaneie para detalhes")
        
        p.showPage()
        p.save()
        
        buffer.seek(0)
        
        return send_file(
            buffer, 
            mimetype='application/pdf',
            as_attachment=False,
            download_name=f'etiqueta_{bem.numero_patrimonio}.pdf'
        )
        
    except Exception as e:
        flash(f"Erro ao gerar etiqueta: {str(e)}", "danger")
        return redirect(url_for('patrimonio.listar_itens'))

@patrimonio_bp.route('/movimentacao/<int:mov_id>/termo')
@login_required
def gerar_termo_recebimento(mov_id):
    from models import MovimentacaoPatrimonio
    mov = MovimentacaoPatrimonio.query.get_or_404(mov_id)
    bem = mov.patrimonio

    buffer = BytesIO()
    p = canvas_lib.Canvas(buffer, pagesize=A4)
    
    p.setFont("Helvetica-Bold", 14)
    p.drawCentredString(300, 800, "TERMO DE RESPONSABILIDADE E RECEBIMENTO")
    p.setFont("Helvetica", 10)
    p.drawCentredString(300, 785, "PREFEITURA DE VALENÇA DO PIAUÍ")

    texto = f"""
    Certifico que na data de {mov.data_movimentacao.strftime('%d/%m/%Y')}, o bem patrimonial abaixo 
    descrito foi transferido para o local: {mov.local_destino}.
    """
    p.setFont("Helvetica", 12)
    p.drawString(50, 730, "DADOS DO BEM:")
    p.setFont("Helvetica", 10)
    p.drawString(70, 710, f"Descrição: {bem.descricao}")
    p.drawString(70, 695, f"Tombamento: {bem.numero_patrimonio or 'Pendente'}")
    p.drawString(70, 680, f"Origem: {mov.local_origem}")

    p.line(50, 500, 250, 500)
    p.drawString(80, 485, "Quem Entregou")
    
    p.line(350, 500, 550, 500)
    p.drawString(380, 485, "Quem Recebeu")

    p.showPage()
    p.save()
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', download_name=f'termo_mov_{mov.id}.pdf')    

@patrimonio_bp.route('/movimentacao/<int:mov_id>/imprimir')
@login_required
def imprimir_termo_transferencia(mov_id):
    from models import MovimentacaoPatrimonio
    movimentacao = MovimentacaoPatrimonio.query.get_or_404(mov_id)
    return render_template('patrimonio/termo_recebimento.html', mov=movimentacao)

@patrimonio_bp.route('/item/baixa/<int:item_id>', methods=['POST'])
@login_required
@role_required('admin', 'Patrimonio')
def dar_baixa_item(item_id):
    item = Patrimonio.query.get_or_404(item_id)
    justificativa = request.form.get('justificativa')
    
    if not justificativa:
        flash("A justificativa é obrigatória para dar baixa.", "warning")
        return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))
    
    try:
        item.status = "Baixado"
        item.situacao_uso = "Inservível"
        
        data_atual = datetime.now().strftime('%d/%m/%Y')
        nova_obs = f"BAIXA REALIZADA EM {data_atual}: {justificativa}"
        
        if item.observacoes:
            item.observacoes = f"{item.observacoes} | {nova_obs}"
        else:
            item.observacoes = nova_obs
        
        db.session.commit()
        registrar_log(f"Baixa efetuada - Item: {item.descricao} (Pat: {item.numero_patrimonio or 'S/N'}) - Motivo: {justificativa}")
        
        flash("Baixa do patrimônio realizada com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao processar a baixa: {str(e)}", "danger")
        
    return redirect(url_for('patrimonio.listar_itens'))

@patrimonio_bp.route('/item/excluir/<int:item_id>', methods=['POST'])
@login_required
@role_required('admin') 
def excluir_item_patrimonio(item_id):
    item = Patrimonio.query.get_or_404(item_id)
    justificativa = request.form.get('justificativa_exclusao')
    
    try:
        descricao_log = f"EXCLUSÃO DEFINITIVA - Item: {item.descricao} (Tomb: {item.numero_patrimonio}) - Motivo: {justificativa}"
        db.session.delete(item)
        db.session.commit()
        
        registrar_log(descricao_log)
        flash("Item excluído permanentemente do sistema.", "warning")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir: {e}", "danger")
        
    return redirect(url_for('patrimonio.listar_itens'))

@patrimonio_bp.route('/itens-baixados')
@login_required
@role_required('admin', 'Patrimonio')
def listar_itens_baixados():
    itens = Patrimonio.query.filter_by(status='Baixado').order_by(Patrimonio.descricao).all()
    return render_template('patrimonio/itens_baixados.html', itens=itens)