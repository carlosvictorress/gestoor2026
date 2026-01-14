# Em motoristas_routes.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_from_directory, current_app
from werkzeug.utils import secure_filename
import os
import uuid
import io
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from flask import make_response
from utils import cabecalho_e_rodape
from datetime import datetime
from extensions import db, bcrypt
from models import Motorista, DocumentoMotorista
# --- IMPORTANTE: Adicionado upload_arquivo_para_nuvem ---
from utils import login_required, registrar_log, fleet_required, role_required, upload_arquivo_para_nuvem

motoristas_bp = Blueprint('motoristas', __name__, url_prefix='/motoristas')

@motoristas_bp.route('/')
@login_required
@fleet_required
@role_required('RH', 'admin', 'Combustivel')
def listar():
    motoristas = Motorista.query.order_by(Motorista.nome).all()
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
            validade_str = request.form.get('cnh_validade')
            motorista.cnh_validade = datetime.strptime(validade_str, '%Y-%m-%d').date() if validade_str else None
            
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
            
            ano_veiculo = request.form.get('veiculo_ano')
            motorista.veiculo_ano = int(ano_veiculo) if ano_veiculo else None
            motorista.veiculo_placa = request.form.get('veiculo_placa')

            db.session.commit()
            
            registrar_log(f'Editou os dados do motorista: "{motorista.nome}".')
            flash('Dados atualizados com sucesso!', 'success')
            return redirect(url_for('motoristas.detalhes', motorista_id=motorista.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar dados: {e}', 'danger')
            
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
        # --- UPLOAD PARA SUPABASE ---
        # Envia para a pasta 'documentos_terceirizados'
        url_doc = upload_arquivo_para_nuvem(file, pasta="documentos_terceirizados")
        
        if url_doc:
            novo_doc = DocumentoMotorista(
                motorista_id=motorista_id,
                tipo_documento=tipo_documento,
                filename=url_doc # Salva o LINK COMPLETO
            )
            db.session.add(novo_doc)
            db.session.commit()
            
            registrar_log(f'Anexou o documento "{tipo_documento}" para o motorista "{motorista.nome}".')
            flash(f'Documento "{tipo_documento}" anexado na nuvem com sucesso!', 'success')
        else:
            flash('Erro ao enviar documento para a nuvem (Supabase).', 'danger')
            
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
    
    # 1. Se for link do Supabase, redireciona
    if documento.filename and documento.filename.startswith('http'):
        return redirect(documento.filename)
    
    # 2. Fallback para arquivos locais antigos
    try:
        docs_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'documentos_terceirizados')
        return send_from_directory(docs_folder, documento.filename, as_attachment=True)
    except FileNotFoundError:
        flash('Arquivo não encontrado.', 'danger')
        return redirect(url_for('motoristas.detalhes', motorista_id=documento.motorista_id))


@motoristas_bp.route('/documento/excluir/<int:doc_id>')
@login_required
@fleet_required
@role_required('RH', 'admin', 'Combustivel')
def excluir_documento(doc_id):
    documento = DocumentoMotorista.query.get_or_404(doc_id)
    motorista_id = documento.motorista_id
    try:
        # Só tenta excluir do disco local se NÃO for link da nuvem
        if documento.filename and not documento.filename.startswith('http'):
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
        # Exclui todos os documentos associados
        for doc in motorista.documentos:
            try:
                # Remove arquivo local se existir e não for link
                if doc.filename and not doc.filename.startswith('http'):
                    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'documentos_terceirizados', doc.filename)
                    if os.path.exists(file_path):
                        os.remove(file_path)
            except Exception as e:
                print(f"Erro ao remover arquivo físico do documento {doc.id}: {e}")

        # Exclui o motorista (os documentos são excluídos em cascata pelo banco)
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
    
@motoristas_bp.route('/<int:motorista_id>/ficha', methods=['GET'])
@login_required
@role_required('RH', 'admin', 'Combustivel')
def imprimir_ficha(motorista_id):
    motorista = Motorista.query.get_or_404(motorista_id)
    
    # Configuração do PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, 
                            rightMargin=1.5*cm, leftMargin=1.5*cm, 
                            topMargin=4*cm, bottomMargin=2*cm)
    
    story = []
    styles = getSampleStyleSheet()
    
    # Estilos Personalizados
    style_titulo = ParagraphStyle(name='Titulo', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=14, spaceAfter=10)
    style_rotulo = ParagraphStyle(name='Rotulo', parent=styles['Normal'], fontSize=8, textColor=colors.gray)
    style_dado = ParagraphStyle(name='Dado', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold', spaceAfter=6)
    
    # Título da Ficha
    story.append(Paragraph(f"FICHA CADASTRAL DO MOTORISTA", style_titulo))
    story.append(Spacer(1, 0.5*cm))
    
    # --- BLOCO 1: DADOS PESSOAIS ---
    story.append(Paragraph("DADOS PESSOAIS E FUNCIONAIS", styles['Heading3']))
    story.append(Spacer(1, 0.2*cm))
    
    # Preparando dados para a tabela
    dados_pessoais = [
        [
            [Paragraph("NOME COMPLETO", style_rotulo), Paragraph(motorista.nome.upper(), style_dado)],
            [Paragraph("CPF", style_rotulo), Paragraph(motorista.cpf or "-", style_dado)]
        ],
        [
            [Paragraph("RG", style_rotulo), Paragraph(motorista.rg or "-", style_dado)],
            [Paragraph("DATA NASCIMENTO", style_rotulo), Paragraph("-", style_dado)] # Adicione se tiver esse campo no model
        ],
        [
            [Paragraph("ENDEREÇO", style_rotulo), Paragraph(motorista.endereco or "-", style_dado)],
            [Paragraph("TELEFONE", style_rotulo), Paragraph(motorista.telefone or "-", style_dado)]
        ],
        [
            [Paragraph("VÍNCULO", style_rotulo), Paragraph(motorista.tipo_vinculo or "-", style_dado)],
            [Paragraph("SECRETARIA", style_rotulo), Paragraph(motorista.secretaria or "-", style_dado)]
        ]
    ]
    
    tbl_pessoais = Table(dados_pessoais, colWidths=[13*cm, 5*cm])
    tbl_pessoais.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(tbl_pessoais)
    story.append(Spacer(1, 0.5*cm))
    
    # --- BLOCO 2: HABILITAÇÃO (CNH) ---
    story.append(Paragraph("DADOS DA HABILITAÇÃO (CNH)", styles['Heading3']))
    story.append(Spacer(1, 0.2*cm))
    
    validade_str = motorista.cnh_validade.strftime('%d/%m/%Y') if motorista.cnh_validade else "-"
    
    dados_cnh = [
        [
            [Paragraph("NÚMERO DA CNH", style_rotulo), Paragraph(motorista.cnh_numero or "-", style_dado)],
            [Paragraph("CATEGORIA", style_rotulo), Paragraph(motorista.cnh_categoria or "-", style_dado)],
            [Paragraph("VALIDADE", style_rotulo), Paragraph(validade_str, style_dado)]
        ]
    ]
    
    tbl_cnh = Table(dados_cnh, colWidths=[8*cm, 4*cm, 6*cm])
    tbl_cnh.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(tbl_cnh)
    story.append(Spacer(1, 0.5*cm))
    
    # --- BLOCO 3: VEÍCULO E ROTA ---
    story.append(Paragraph("VEÍCULO E ROTA VINCULADA", styles['Heading3']))
    story.append(Spacer(1, 0.2*cm))
    
    veiculo_info = f"{motorista.veiculo_modelo or '-'} ({motorista.veiculo_ano or '-'})"
    
    dados_rota = [
        [
            [Paragraph("VEÍCULO PADRÃO", style_rotulo), Paragraph(veiculo_info, style_dado)],
            [Paragraph("PLACA", style_rotulo), Paragraph(motorista.veiculo_placa or "-", style_dado)]
        ],
        [
            [Paragraph("DESCRIÇÃO DA ROTA", style_rotulo), Paragraph(motorista.rota_descricao or "-", style_dado)],
            [Paragraph("TURNO", style_rotulo), Paragraph(motorista.turno or "-", style_dado)]
        ]
    ]
    
    tbl_rota = Table(dados_rota, colWidths=[13*cm, 5*cm])
    tbl_rota.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(tbl_rota)
    story.append(Spacer(1, 2*cm))
    
    # --- ASSINATURAS ---
    linha = "______________________________________________________"
    
    dados_assinatura = [
        [Paragraph(linha, style_titulo)],
        [Paragraph(f"<b>{motorista.nome.upper()}</b>", style_titulo)],
        [Paragraph("Assinatura do Motorista", style_titulo)],
        [Spacer(1, 1.5*cm)],
        [Paragraph(linha, style_titulo)],
        [Paragraph("Responsável pelo Setor de Transportes", style_titulo)]
    ]
    
    tbl_ass = Table(dados_assinatura, colWidths=[18*cm])
    story.append(tbl_ass)
    
    # Gera o PDF
    # Tenta usar o cabecalho padrão se existir no utils, senão usa padrão
    try:
        from utils import cabecalho_e_rodape
        doc.build(story, onFirstPage=cabecalho_e_rodape, onLaterPages=cabecalho_e_rodape)
    except ImportError:
        doc.build(story)
        
    buffer.seek(0)
    
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ficha_{motorista.nome}.pdf'
    
    return response    