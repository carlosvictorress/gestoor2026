# utils.py
import os # <-- Adicionar
from datetime import datetime # <-- Adicionar
from reportlab.lib.pagesizes import A4 # <-- Adicionar
from reportlab.lib import colors # <-- Adicionar
from reportlab.lib.units import cm # <-- Adicionar
from reportlab.platypus import Image # <-- Adicionar
from functools import wraps
from flask import session, flash, redirect, url_for, request
from .extensions import db
from .models import Log
from functools import wraps
from flask import session, flash, redirect, url_for

import face_recognition
import json
import numpy as np
import locale
import uuid
import re
from flask import Flask




def limpar_cpf(cpf):
    if cpf:
        # re.sub é importado no topo do utils.py
        return re.sub(r"\D", "", cpf)
    return None



def gerar_codigo_validade(cpf_servidor, num_vinculo, nome_secretaria):
    # ... (código da função conforme proposto acima) ...
    # Exemplo: a1b2-012025-seme-2025
    cpf_limpo = limpar_cpf(cpf_servidor)
    vinculo_simples = limpar_cpf(num_vinculo)
    sec_simples = nome_secretaria.replace(' ', '').lower()[:4]
    ano = datetime.now().year
    return f"{str(uuid.uuid4().hex)[:4]}-{vinculo_simples}-{sec_simples}-{ano}"



def currency_filter_br(value):
    """Formata um valor float para a moeda brasileira (R$ 1.000.000,00)."""
    if value is None:
        return "0,00"
    # Usa a formatação de moeda do locale
    # O False no final significa que não deve incluir o símbolo da moeda (R$)
    return locale.currency(value, grouping=True, symbol=False).strip()

def registrar_log(action):
    """Registra uma ação no banco de dados."""
    try:
        if 'logged_in' in session:
            username = session.get('username', 'Anônimo')
            ip_address = request.remote_addr
            log_entry = Log(username=username, action=action, ip_address=ip_address)
            db.session.add(log_entry)
            db.session.commit()
    except Exception as e:
        print(f"Erro ao registrar log: {e}")
        db.session.rollback()

def login_required(f):
    """Decorador para exigir que o usuário esteja logado."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
    
def cabecalho_e_rodape(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 9)
    canvas.drawString(2*cm, 1.5*cm, f"Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    canvas.drawRightString(doc.width + doc.leftMargin, 1.5*cm, f"Página {doc.page}")

    if doc.page == 1:
        # CORREÇÃO APLICADA AQUI
        from flask import current_app
        basedir = current_app.root_path
        image_path = os.path.join(basedir, 'static', 'timbre.jpg')
        if os.path.exists(image_path):
            canvas.drawImage(image_path, 2*cm, A4[1] - 2.5*cm, width=17*cm, height=2.2*cm, preserveAspectRatio=True, mask='auto')
    
    canvas.restoreState()

def cabecalho_e_rodape_moderno(canvas, doc, titulo_doc="Relatório"):
    canvas.saveState()
    cor_principal = colors.HexColor('#004d40')
    
    # --- Cabeçalho ---
    # CORREÇÃO APLICADA AQUI
    from flask import current_app
    basedir = current_app.root_path
    image_path = os.path.join(basedir, 'static', 'timbre.jpg')

    if os.path.exists(image_path):
        from reportlab.lib.utils import ImageReader
        img_reader = ImageReader(image_path)
        img_width, img_height = img_reader.getSize()
        aspect = img_height / float(img_width)
        
        logo_width = 5*cm 
        logo_height = logo_width * aspect 
        
        logo = Image(image_path, width=logo_width, height=logo_height)
        logo.drawOn(canvas, doc.leftMargin, A4[1] - doc.topMargin + 1.2*cm - logo_height)

    canvas.setFont('Helvetica-Bold', 18)
    canvas.setFillColor(colors.black)
    canvas.drawString(doc.leftMargin + logo_width + 0.5*cm, A4[1] - doc.topMargin + 0.8*cm, titulo_doc)

    # --- Rodapé ---
    canvas.setFillColor(cor_principal)
    canvas.rect(doc.leftMargin, doc.bottomMargin - 0.5*cm, doc.width, 0.3*cm, fill=1, stroke=0)
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(doc.leftMargin, doc.bottomMargin - 0.4*cm, f"SysEduca | Emitido em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    canvas.drawRightString(doc.width + doc.leftMargin, doc.bottomMargin - 0.4*cm, f"Página {doc.page}")

    canvas.restoreState()
    
    
    
def admin_required(f):
     @wraps(f)
     def decorated_function(*args, **kwargs):
         if session.get('role') != 'admin':
             flash('Você não tem permissão para acessar esta página.', 'danger')
             return redirect(url_for('dashboard'))
         return f(*args, **kwargs)
     return decorated_function
     
     
def fleet_required(f):
     @wraps(f)
     def decorated_function(*args, **kwargs):
         # Permite o acesso se o papel for 'admin' ou 'frota de combustivel'
         if session.get('role') not in ['admin', 'frota de combustivel']:
             flash('Você não tem permissão para acessar esta página.', 'danger')
             return redirect(url_for('dashboard'))
         return f(*args, **kwargs)
     return decorated_function     
     
     
     
     
def role_required(*roles_permitidos):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 1. Pega a permissão do usuário que está logado
            permissao_usuario = session.get('role')

            # 2. Se o usuário for 'admin', ele tem acesso a tudo, sempre.
            if permissao_usuario == 'admin':
                return f(*args, **kwargs)
            
            # 3. Se a permissão do usuário estiver na lista de permissões permitidas para a rota, libera o acesso.
            if permissao_usuario in roles_permitidos:
                return f(*args, **kwargs)
            
            # 4. Se não passou em nenhuma das verificações acima, bloqueia o acesso.
            flash('Você não tem permissão para acessar esta página.', 'danger')
            return redirect(url_for('dashboard'))
        return decorated_function
    return decorator

class NumpyArrayEncoder(json.JSONEncoder):
    """ Classe especial para converter o 'encoding' (que é um array Numpy) em um JSON. """
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)

def gerar_encoding_facial(caminho_completo_imagem):
    """
    Carrega uma imagem, encontra o rosto e gera o 'encoding' facial.
    Retorna o encoding como uma string JSON para salvar no banco de dados.
    """
    try:
        # 1. Carrega a imagem
        imagem = face_recognition.load_image_file(caminho_completo_imagem)

        # 2. Tenta encontrar rostos. Pega apenas o primeiro rosto encontrado.
        encodings = face_recognition.face_encodings(imagem)

        if not encodings:
            # Se nenhum rosto for encontrado na imagem
            return None, "Nenhum rosto detectado na imagem."

        # 3. Pega o primeiro encoding (array numpy)
        encoding_numpy = encodings[0]

        # 4. Converte o array numpy para uma string JSON
        encoding_str = json.dumps(encoding_numpy, cls=NumpyArrayEncoder)

        return encoding_str, "Encoding gerado com sucesso."

    except Exception as e:
        return None, f"Erro ao processar imagem: {str(e)}"

def comparar_rostos(encoding_referencia_str, foto_ao_vivo):
    """
    Compara um encoding de referência (do banco) com uma foto tirada ao vivo.

    :param encoding_referencia_str: A string JSON do banco de dados (Servidor.face_encoding)
    :param foto_ao_vivo: A imagem (em bytes) vinda do formulário de ponto
    :return: True se for a mesma pessoa, False caso contrário
    """
    try:
        # 1. Converte o JSON string (do banco) de volta para uma lista/array
        encoding_referencia = json.loads(encoding_referencia_str)

        # 2. Carrega a foto ao vivo (que vem do request)
        #    O 'foto_ao_vivo' é um DataURL (string base64) vindo do registrar_ponto_com_foto.html
        #    Precisamos decodificar
        import base64

        # Remove o cabeçalho "data:image/jpeg;base64,"
        if "base64," in foto_ao_vivo:
            foto_ao_vivo = foto_ao_vivo.split("base64,", 1)[1]

        img_bytes = base64.b64decode(foto_ao_vivo)

        # Converte os bytes em um arquivo temporário para o face_recognition ler
        import io
        imagem_ao_vivo_stream = io.BytesIO(img_bytes)
        imagem_ao_vivo = face_recognition.load_image_file(imagem_ao_vivo_stream)

        # 3. Gera o encoding da foto ao vivo
        encodings_ao_vivo = face_recognition.face_encodings(imagem_ao_vivo)

        if not encodings_ao_vivo:
            # Não achou rosto na foto ao vivo
            return False

        encoding_ao_vivo = encodings_ao_vivo[0]

        # 4. Compara os dois rostos
        #    compare_faces espera uma LISTA de encodings conhecidos
        resultado = face_recognition.compare_faces([encoding_referencia], encoding_ao_vivo, tolerance=0.6)

        # 5. Retorna o resultado (True ou False)
        return resultado[0]

    except Exception as e:
        print(f"Erro ao comparar rostos: {e}")
        return False
    
    