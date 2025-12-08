# Em motoristas_routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_from_directory, current_app
from werkzeug.utils import secure_filename
import os
import uuid
from datetime import datetime
from utils import role_required

from extensions import db, bcrypt
# IMPORTAÇÃO CORRIGIDA
from models import Motorista, DocumentoMotorista
from utils import login_required, registrar_log, fleet_required

# BLUEPRINT CORRIGIDO
motoristas_bp = Blueprint('motoristas', __name__, url_prefix='/motoristas')

@motoristas_bp.route('/')
@login_required
@fleet_required
@role_required('RH', 'admin', 'Combustivel')
def listar():
    motoristas = Motorista.query.order_by(Motorista.nome).all()
    # Template folder corrigido
    return render_template('motoristas/lista.html', motoristas=motoristas, hoje=datetime.now().date())

@motoristas_bp.route('/novo', methods=['GET', 'POST'])
@login_required
@fleet_required
@role_required('RH', 'admin', 'Combustivel')
def novo():
    if request.method == 'POST':
        try:
            validade_str = request.form.get('cnh_validade')
            validade_obj = datetime.strptime(validade_str, '%Y-%m-%d').date() if validade_str else None

            novo_motorista = Motorista(
                nome=request.form.get('nome'),
                tipo_vinculo=request.form.get('tipo_vinculo'),
                secretaria=request.form.get('secretaria'),
                rg=request.form.get('rg'),
                cpf=request.form.get('cpf'),
                endereco=request.form.get('endereco'),
                telefone=request.form.get('telefone'),
                cnh_numero=request.form.get('cnh_numero'),
                cnh_categoria=request.form.get('cnh_categoria'),
                cnh_validade=validade_obj,
                rota_descricao=request.form.get('rota_descricao'),
                turno=request.form.get('turno'),
                veiculo_modelo=request.form.get('veiculo_modelo'),
                veiculo_ano=request.form.get('veiculo_ano') or None,
                veiculo_placa=request.form.get('veiculo_placa')
            )
            db.session.add(novo_motorista)
            db.session.commit()
            registrar_log(f'Cadastrou o motorista: "{novo_motorista.nome}".')
            flash('Motorista cadastrado com sucesso! Agora anexe os documentos.', 'success')
            return redirect(url_for('motoristas.detalhes', motorista_id=novo_motorista.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar motorista: {e}', 'danger')
            
    return render_template('motoristas/form.html', motorista=None)

@motoristas_bp.route('/<int:motorista_id>/detalhes', methods=['GET', 'POST'])
@login_required
@fleet_required
@role_required('RH', 'admin', 'Combustivel')
def detalhes(motorista_id):
    motorista = Motorista.query.get_or_404(motorista_id)
    
    if request.method == 'POST':
        try:
            # Pega a data e converte para o formato de objeto Date
            validade_str = request.form.get('cnh_validade')
            motorista.cnh_validade = datetime.strptime(validade_str, '%Y-%m-%d').date() if validade_str else None
            
            # Atualiza todos os outros campos
            motorista.tipo_vinculo = request.form.get('tipo_vinculo')
            motorista.secretaria = request.form.get('secretaria')
            motorista.nome = request.form.get('nome')
            motorista.rg = request.form.get('rg')
            motorista.cpf = request.form.get('cpf')
            motorista.endereco = request.form.get('endereco')
            motorista.telefone = request.form.get('telefone')
            motorista.cnh_numero = request.form.get('cnh_numero')
            motorista.cnh_categoria = request.form.get('cnh_categoria')
            motorista.rota_descricao = request.form.get('rota_descricao')
            motorista.turno = request.form.get('turno')
            motorista.veiculo_modelo = request.form.get('veiculo_modelo')
            # Garante que o ano seja um número ou None
            ano_veiculo = request.form.get('veiculo_ano')
            motorista.veiculo_ano = int(ano_veiculo) if ano_veiculo else None
            motorista.veiculo_placa = request.form.get('veiculo_placa')

            # Confirma a transação no banco de dados
            db.session.commit()
            
            registrar_log(f'Editou os dados do motorista: "{motorista.nome}".')
            flash('Dados atualizados com sucesso!', 'success')
            
            # Redireciona para a mesma página para ver as alterações
            return redirect(url_for('motoristas.detalhes', motorista_id=motorista.id))
            
        except Exception as e:
            db.session.rollback() # Desfaz a transação em caso de erro
            flash(f'Erro ao atualizar dados: {e}', 'danger')
            
    # Para o método GET, apenas renderiza a página
    return render_template('motoristas/detalhes.html', motorista=motorista)


@motoristas_bp.route('/<int:motorista_id>/upload', methods=['POST'])
@login_required
@fleet_required
@role_required('RH', 'admin', 'Combustivel')
def upload_documento(motorista_id):
    motorista = Motorista.query.get_or_404(motorista_id)
    file = request.files.get('documento')
    tipo_documento = request.form.get('tipo_documento')
    if not file or file.filename == '' or not tipo_documento:
        flash('O tipo de documento e o arquivo são obrigatórios.', 'warning')
        return redirect(url_for('motoristas.detalhes', motorista_id=motorista.id))
    try:
        filename = str(uuid.uuid4().hex) + '_' + secure_filename(file.filename)
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'documentos_terceirizados')
        os.makedirs(upload_path, exist_ok=True)
        file.save(os.path.join(upload_path, filename))
        novo_doc = DocumentoMotorista(
            motorista_id=motorista_id,
            tipo_documento=tipo_documento,
            filename=filename
        )
        db.session.add(novo_doc)
        db.session.commit()
        registrar_log(f'Anexou o documento "{tipo_documento}" para o motorista "{motorista.nome}".')
        flash(f'Documento "{tipo_documento}" anexado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao anexar documento: {e}', 'danger')
    return redirect(url_for('motoristas.detalhes', motorista_id=motorista.id))


@motoristas_bp.route('/documento/download/<int:doc_id>')
@login_required
@fleet_required
@role_required('RH', 'admin', 'Combustivel')
def download_documento(doc_id):
    documento = DocumentoMotorista.query.get_or_404(doc_id)
    docs_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'documentos_terceirizados')
    return send_from_directory(docs_folder, documento.filename, as_attachment=True)


@motoristas_bp.route('/documento/excluir/<int:doc_id>')
@login_required
@fleet_required
@role_required('RH', 'admin', 'Combustivel')
def excluir_documento(doc_id):
    documento = DocumentoMotorista.query.get_or_404(doc_id)
    motorista_id = documento.motorista_id
    try:
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'documentos_terceirizados', documento.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        db.session.delete(documento)
        db.session.commit()
        flash('Documento excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir documento: {e}', 'danger')
    return redirect(url_for('motoristas.detalhes', motorista_id=motorista_id))
    
    
@motoristas_bp.route('/<int:motorista_id>/excluir')
@login_required
@fleet_required
@role_required('RH', 'admin')
def excluir(motorista_id):
    motorista = Motorista.query.get_or_404(motorista_id)
    try:
        # Exclui todos os documentos associados primeiro
        for doc in motorista.documentos:
            try:
                file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'documentos_terceirizados', doc.filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"Erro ao remover arquivo físico do documento {doc.id}: {e}")

        # Exclui o motorista (os documentos são excluídos em cascata)
        nome_motorista = motorista.nome
        db.session.delete(motorista)
        db.session.commit()
        
        registrar_log(f'Excluiu o motorista: "{nome_motorista}".')
        flash(f'Motorista "{nome_motorista}" e todos os seus documentos foram excluídos com sucesso.', 'success')
        return redirect(url_for('motoristas.listar'))

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir motorista: {e}', 'danger')
        return redirect(url_for('motoristas.detalhes', motorista_id=motorista_id))    
