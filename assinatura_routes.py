# assinatura_routes.py - VERSÃO COM SEGURANÇA VIA PIN (OTP)

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session,
    make_response, current_app, send_from_directory, send_file, jsonify
)
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as canvas_lib
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename
from flask_mail import Message # Necessário para o e-mail
import os
import io
import uuid
import fitz  # PyMuPDF
import qrcode
import random # Para gerar o PIN
from datetime import datetime

# Importações dos seus módulos
from extensions import db
from models import DocumentoAssinado, User, Servidor
from utils import login_required, role_required, registrar_log, gerar_codigo_validade

# Cria o Blueprint
assinatura_bp = Blueprint('assinatura', __name__, url_prefix='/assinatura')

# CONSTANTES
LARGURA_SELO_PT = 260 
ALTURA_SELO_PT = 55 

# ===================================================================
# FUNÇÕES AUXILIARES (IGUAIS AO ANTERIOR)
# ===================================================================

def criar_pagina_overlay_inteira(servidor, codigo_validade, largura_pag, altura_pag, x, y):
    buffer_selo = io.BytesIO()
    c = canvas_lib.Canvas(buffer_selo, pagesize=(largura_pag, altura_pag))
    
    # 1. Configuração Visual
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    c.setFillColor(colors.HexColor("#ffffff"))
    c.rect(x, y, LARGURA_SELO_PT, ALTURA_SELO_PT, stroke=1, fill=1)
    
    # 2. QR Code
    url_validacao = url_for('assinatura.validar_assinatura_publica', codigo=codigo_validade, _external=True)
    qr = qrcode.QRCode(box_size=2, border=0)
    qr.add_data(url_validacao)
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white")
    
    buffer_qr = io.BytesIO()
    img_qr.save(buffer_qr, format="PNG")
    buffer_qr.seek(0)
    qr_image = ImageReader(buffer_qr)
    
    tamanho_qr = ALTURA_SELO_PT - 10
    pos_qr_x = x + 5
    pos_qr_y = y + 5
    c.drawImage(qr_image, pos_qr_x, pos_qr_y, width=tamanho_qr, height=tamanho_qr)
    
    # 3. Textos
    c.setFillColor(colors.black)
    margem_texto_x = x + tamanho_qr + 12 
    
    c.setFont('Helvetica-Bold', 8)
    c.drawString(margem_texto_x, y + ALTURA_SELO_PT - 12, "ASSINATURA DIGITAL - GESTOOR360")
    
    c.setFont('Helvetica', 7)
    nome_servidor = servidor.nome.upper()
    if len(nome_servidor) > 35: nome_servidor = nome_servidor[:32] + "..."
    
    c.drawString(margem_texto_x, y + ALTURA_SELO_PT - 24, f"Assinado por: {nome_servidor}")
    c.drawString(margem_texto_x, y + ALTURA_SELO_PT - 34, f"CPF: {servidor.cpf} | Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    
    c.setFillColor(colors.HexColor('#cc0000'))
    c.setFont('Helvetica-Bold', 7)
    c.drawString(margem_texto_x, y + ALTURA_SELO_PT - 45, f"VALIDADE: {codigo_validade}")
    
    c.save()
    buffer_selo.seek(0)
    return buffer_selo

def inserir_selo_na_pagina(caminho_original, servidor, codigo_validade, pos_x, pos_y, pagina, doc_original_name, largura_pag, altura_pag):
    try:
        from pypdf import PdfReader, PdfWriter
        
        buffer_overlay = criar_pagina_overlay_inteira(
            servidor, codigo_validade, largura_pag, altura_pag, pos_x, pos_y
        )
        
        reader_original = PdfReader(caminho_original)
        reader_overlay = PdfReader(buffer_overlay)
        writer = PdfWriter()
        
        total_paginas = len(reader_original.pages)
        if pagina < 1: pagina = 1
        if pagina > total_paginas: pagina = total_paginas
        
        for i, page in enumerate(reader_original.pages):
            if (i + 1) == pagina:
                overlay_page = reader_overlay.pages[0]
                page.merge_page(overlay_page)
                writer.add_page(page)
            else:
                writer.add_page(page)

        upload_folder_permanente = os.path.join(current_app.root_path, 'uploads', 'assinados')
        os.makedirs(upload_folder_permanente, exist_ok=True)
        
        nome_final_arquivo = f"ASSINADO_{codigo_validade.split('-')[0]}_{secure_filename(doc_original_name)}"
        caminho_final = os.path.join(upload_folder_permanente, nome_final_arquivo)
        
        with open(caminho_final, "wb") as output_stream:
            writer.write(output_stream)
            
        return nome_final_arquivo
    except Exception as e:
        raise Exception(f"Erro PDF: {e}")

# ===================================================================
# ROTAS NOVAS E ATUALIZADAS
# ===================================================================

@assinatura_bp.route("/enviar-pin", methods=['POST'])
@login_required
def enviar_pin_seguranca():
    """Gera um PIN, salva na sessão e envia por e-mail para o servidor."""
    dados = request.get_json()
    cpf_servidor = dados.get('cpf')
    
    if not cpf_servidor:
        return jsonify({'success': False, 'message': 'CPF do servidor não informado.'})

    servidor = Servidor.query.filter_by(cpf=cpf_servidor).first()
    
    if not servidor:
        return jsonify({'success': False, 'message': 'Servidor não encontrado.'})

    if not servidor.email:
        return jsonify({'success': False, 'message': f'O servidor {servidor.nome} não possui e-mail cadastrado. Atualize o cadastro.'})

    # 1. Gerar PIN de 6 dígitos
    pin = str(random.randint(100000, 999999))
    
    # 2. Salvar na sessão (Temporário e Seguro)
    session['assinatura_pin'] = pin
    session['assinatura_cpf_alvo'] = cpf_servidor
    
    # 3. Enviar E-mail
    try:
        msg = Message(
            subject="PIN de Assinatura Digital - Gestoor360",
            recipients=[servidor.email]
        )
        msg.body = f"""Olá {servidor.nome},

Foi solicitada uma assinatura digital em seu nome no sistema Gestoor360.
Se foi você (ou o RH sob sua autorização), utilize o código abaixo para confirmar:

PIN DE SEGURANÇA: {pin}

Este código é válido apenas para esta assinatura.
"""
        # Acessa a extensão de e-mail através do current_app para evitar erro de importação circular
        mail = current_app.extensions.get('mail')
        if mail:
            mail.send(msg)
            return jsonify({'success': True, 'message': f'PIN enviado para {servidor.email}'})
        else:
            return jsonify({'success': False, 'message': 'Serviço de e-mail não configurado no servidor.'})

    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return jsonify({'success': False, 'message': f'Erro ao enviar e-mail: {str(e)}'})


@assinatura_bp.route("/confirmar", methods=["GET", "POST"])
@login_required
@role_required("RH", "admin")
def confirmar_assinatura():
    temp_filename = session.get('temp_doc_filename')
    doc_original_name = session.get('doc_original_name')
    todos_servidores = Servidor.query.order_by(Servidor.nome).all()

    if not temp_filename:
        flash("Sessão expirada.", "warning")
        return redirect(url_for('assinatura.index_assinatura'))

    if request.method == "POST":
        try:
            # Dados do formulário
            servidor_cpf = request.form.get('servidor_cpf')
            pin_digitado = request.form.get('pin_digitado') # <--- NOVO CAMPO
            rel_x = float(request.form.get('rel_x')) 
            rel_y = float(request.form.get('rel_y')) 
            pagina = int(request.form.get('pagina', 1))

            # --- VALIDAÇÃO DE SEGURANÇA (O PIN BATE?) ---
            pin_sessao = session.get('assinatura_pin')
            cpf_sessao = session.get('assinatura_cpf_alvo')

            if not pin_digitado or pin_digitado != pin_sessao:
                flash("PIN de segurança incorreto ou expirado. Tente novamente.", "danger")
                return redirect(url_for('assinatura.confirmar_assinatura'))
            
            if servidor_cpf != cpf_sessao:
                flash("O PIN gerado não pertence ao servidor selecionado.", "danger")
                return redirect(url_for('assinatura.confirmar_assinatura'))
            
            # Limpa o PIN da sessão para não ser reutilizado
            session.pop('assinatura_pin', None)
            session.pop('assinatura_cpf_alvo', None)

            # --- LÓGICA DE ASSINATURA (IGUAL A ANTES) ---
            servidor_para_assinatura = Servidor.query.filter_by(cpf=servidor_cpf).first()
            
            temp_dir = os.path.join(current_app.root_path, 'uploads', 'temp_assinatura')
            caminho_original = os.path.join(temp_dir, temp_filename)
            
            doc_fitz = fitz.open(caminho_original)
            page_fitz = doc_fitz.load_page(pagina - 1)
            rect = page_fitz.rect
            pdf_width = rect.width
            pdf_height = rect.height
            doc_fitz.close()

            click_x_pt = rel_x * pdf_width
            click_y_pt = (1 - rel_y) * pdf_height 

            pos_x = click_x_pt - (LARGURA_SELO_PT / 2)
            pos_y = click_y_pt - (ALTURA_SELO_PT / 2)
            
            nome_secretaria = servidor_para_assinatura.secretaria.nome if servidor_para_assinatura.secretaria else "GERAL"
            codigo_validade = gerar_codigo_validade(servidor_para_assinatura.cpf, servidor_para_assinatura.num_contrato, nome_secretaria)
            
            nome_arquivo_final = inserir_selo_na_pagina(
                caminho_original, servidor_para_assinatura, codigo_validade, 
                pos_x, pos_y, pagina, doc_original_name, pdf_width, pdf_height
            )
            
            novo_doc_assinado = DocumentoAssinado(
                nome_documento = doc_original_name,
                codigo_validade = codigo_validade,
                servidor_cpf = servidor_para_assinatura.cpf,
                usuario_id = session.get('user_id'),
                pos_x = pos_x,
                pos_y = pos_y,
                pagina = pagina,
                filename_seguro = nome_arquivo_final
            )
            db.session.add(novo_doc_assinado)
            db.session.commit()
            
            if os.path.exists(caminho_original): os.remove(caminho_original)
            session.pop('temp_doc_filename', None)
            session.pop('doc_original_name', None)
            
            flash("Documento assinado com sucesso e validado via PIN!", "success")
            return redirect(url_for('assinatura.download_documento_assinado', doc_id=novo_doc_assinado.id))

        except Exception as e:
            db.session.rollback()
            print(f"Erro Assinatura: {e}")
            flash(f"Erro: {str(e)}", "danger")
            return redirect(url_for('assinatura.confirmar_assinatura'))

    try:
        upload_dir = os.path.join(current_app.root_path, 'uploads', 'temp_assinatura')
        doc = fitz.open(os.path.join(upload_dir, temp_filename))
        total_paginas = len(doc)
        doc.close()
    except:
        total_paginas = 1

    return render_template('assinatura_confirmar.html', 
                            doc_original_name=doc_original_name,
                            todos_servidores=todos_servidores, 
                            temp_filename=temp_filename,
                            total_paginas=total_paginas)

# MANTENHA AS OUTRAS ROTAS (index, upload, download, preview, validar) IGUAIS
# ... (Copie do código anterior se necessário, elas não mudaram) ...
@assinatura_bp.route("/")
@login_required
@role_required("RH", "admin")
def index_assinatura():
    user_id = session.get('user_id')
    documentos_assinados = DocumentoAssinado.query.filter_by(usuario_id=user_id).order_by(DocumentoAssinado.data_assinatura.desc()).all()
    servidores = Servidor.query.order_by(Servidor.nome).all()
    return render_template('assinatura_index.html', servidores=servidores, docs_assinados=documentos_assinados)

@assinatura_bp.route("/upload", methods=["POST"])
@login_required
@role_required("RH", "admin")
def upload_documento_para_assinatura():
    if 'pdf_file' not in request.files:
        return redirect(url_for('assinatura.index_assinatura'))
    file = request.files["pdf_file"]
    if file.filename == '':
        return redirect(url_for('assinatura.index_assinatura'))

    if file and file.filename.lower().endswith('.pdf'):
        original_filename = secure_filename(file.filename)
        temp_filename = str(uuid.uuid4().hex) + "_" + original_filename
        
        upload_dir = os.path.join(current_app.root_path, 'uploads', 'temp_assinatura')
        os.makedirs(upload_dir, exist_ok=True)
        file.save(os.path.join(upload_dir, temp_filename))

        session['temp_doc_filename'] = temp_filename
        session['doc_original_name'] = original_filename
        
        return redirect(url_for('assinatura.confirmar_assinatura'))
    else:
        flash('Apenas arquivos PDF são permitidos.', 'danger')
        return redirect(url_for('assinatura.index_assinatura'))

@assinatura_bp.route("/preview_image/<filename>/<int:pagina>")
@login_required
def preview_image(filename, pagina):
    sessao_arquivo = session.get('temp_doc_filename')
    if filename != sessao_arquivo:
        return "Sessão inválida", 403

    upload_dir = os.path.join(current_app.root_path, 'uploads', 'temp_assinatura')
    filepath = os.path.join(upload_dir, filename)

    if not os.path.exists(filepath):
        return "Arquivo não encontrado", 404

    try:
        doc = fitz.open(filepath)
        if pagina < 1 or pagina > len(doc):
            return "Página inválida", 404
            
        page = doc.load_page(pagina - 1)
        matrix = fitz.Matrix(1.5, 1.5)
        pix = page.get_pixmap(matrix=matrix)
        img_data = pix.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(img_data), mimetype='image/png')
    except Exception as e:
        print(f"[ERRO CRÍTICO] Falha Fitz: {e}")
        return f"Erro interno: {e}", 500

@assinatura_bp.route("/download/<int:doc_id>") 
@login_required
@role_required("RH", "admin")
def download_documento_assinado(doc_id):
    doc_assinado = DocumentoAssinado.query.get_or_404(doc_id)
    directory = os.path.join(current_app.root_path, 'uploads', 'assinados')
    filename = doc_assinado.filename_seguro
    
    if not os.path.exists(os.path.join(directory, filename)):
        flash("Arquivo físico não encontrado no servidor.", "danger")
        return redirect(url_for('assinatura.index_assinatura'))

    return send_from_directory(
        directory, 
        filename, 
        as_attachment=True, 
        download_name=f"Assinado_{doc_assinado.nome_documento}"
    )

@assinatura_bp.route("/validar", methods=["GET", "POST"])
def validar_assinatura_publica():
    resultado = None
    codigo = request.args.get('codigo') or request.form.get('codigo')
    
    if codigo:
        codigo = codigo.strip()
        doc = DocumentoAssinado.query.filter_by(codigo_validade=codigo).first()
        
        if doc:
            caminho_arquivo = os.path.join(current_app.root_path, 'uploads', 'assinados', doc.filename_seguro)
            arquivo_existe = os.path.exists(caminho_arquivo)
            
            servidor_nome = doc.servidor.nome if doc.servidor else "Servidor Não Identificado"
            servidor_cargo = doc.servidor.funcao if doc.servidor else "Função Não Identificada"
            servidor_lotacao = doc.servidor.lotacao if doc.servidor else ""

            resultado = {
                "status": "VÁLIDO" if arquivo_existe else "ARQUIVO_AUSENTE",
                "documento": doc.nome_documento,
                "data_assinatura": doc.data_assinatura.strftime('%d/%m/%Y às %H:%M:%S'),
                "emitido_por": servidor_nome,
                "cargo": servidor_cargo,
                "lotacao": servidor_lotacao,
                "codigo": codigo
            }
        else:
            resultado = {
                "status": "INVÁLIDO",
                "codigo": codigo
            }
            
    return render_template("assinatura_validar.html", resultado=resultado, codigo_buscado=codigo)