# utils.py
import os
import math
import json
import base64
import io
import re
import numpy as np
import face_recognition
from datetime import datetime
from functools import wraps
from flask import session, flash, redirect, url_for, request, current_app
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import Image
from extensions import db
from models import Log

# --- CORREÇÃO 1: Importar o cliente do Supabase ---
from supabase import create_client

def limpar_cpf(cpf):
    if cpf:
        return re.sub(r"\D", "", cpf)
    return None

def gerar_codigo_validade(cpf_servidor, num_vinculo, nome_secretaria):
    cpf_limpo = limpar_cpf(cpf_servidor)
    vinculo_simples = limpar_cpf(num_vinculo)
    sec_simples = nome_secretaria.replace(' ', '').lower()[:4]
    ano = datetime.now().year
    import uuid
    return f"{str(uuid.uuid4().hex)[:4]}-{vinculo_simples}-{sec_simples}-{ano}"

def currency_filter_br(value):
    if value is None:
        value = 0.0
    try:
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return f"{value}"

def registrar_log(action):
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
        basedir = current_app.root_path
        image_path = os.path.join(basedir, 'static', 'timbre.jpg')
        if os.path.exists(image_path):
            canvas.drawImage(image_path, 2*cm, A4[1] - 2.5*cm, width=17*cm, height=2.2*cm, preserveAspectRatio=True, mask='auto')
    
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
         if session.get('role') not in ['admin', 'frota de combustivel']:
             flash('Você não tem permissão para acessar esta página.', 'danger')
             return redirect(url_for('dashboard'))
         return f(*args, **kwargs)
     return decorated_function     

def role_required(*roles_permitidos):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            permissao_usuario = session.get('role')
            if permissao_usuario == 'admin':
                return f(*args, **kwargs)
            if permissao_usuario in roles_permitidos:
                return f(*args, **kwargs)
            flash('Você não tem permissão para acessar esta página.', 'danger')
            return redirect(url_for('dashboard'))
        return decorated_function
    return decorator

class NumpyArrayEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)

def gerar_encoding_facial(caminho_completo_imagem):
    try:
        imagem = face_recognition.load_image_file(caminho_completo_imagem)
        encodings = face_recognition.face_encodings(imagem)
        if not encodings:
            return None, "Nenhum rosto detectado na imagem."
        encoding_numpy = encodings[0]
        encoding_str = json.dumps(encoding_numpy, cls=NumpyArrayEncoder)
        return encoding_str, "Encoding gerado com sucesso."
    except Exception as e:
        return None, f"Erro ao processar imagem: {str(e)}"

def comparar_rostos(encoding_referencia_str, foto_ao_vivo):
    try:
        encoding_referencia = json.loads(encoding_referencia_str)
        if "base64," in foto_ao_vivo:
            foto_ao_vivo = foto_ao_vivo.split("base64,", 1)[1]
        img_bytes = base64.b64decode(foto_ao_vivo)
        imagem_ao_vivo_stream = io.BytesIO(img_bytes)
        imagem_ao_vivo = face_recognition.load_image_file(imagem_ao_vivo_stream)
        encodings_ao_vivo = face_recognition.face_encodings(imagem_ao_vivo)

        if not encodings_ao_vivo:
            return False
        
        encoding_ao_vivo = encodings_ao_vivo[0]
        resultado = face_recognition.compare_faces([encoding_referencia], encoding_ao_vivo, tolerance=0.6)
        return resultado[0]
    except Exception as e:
        print(f"Erro ao comparar rostos: {e}")
        return False

def identificar_servidor_por_rosto(foto_b64, servidores_ativos):
    """
    Nova função para identificar qual servidor é baseado na foto da câmera.
    """
    try:
        if "base64," in foto_b64:
            foto_b64 = foto_b64.split("base64,", 1)[1]
        
        img_bytes = base64.b64decode(foto_b64)
        imagem_stream = io.BytesIO(img_bytes)
        
        imagem = face_recognition.load_image_file(imagem_stream)
        encodings_na_foto = face_recognition.face_encodings(imagem)
        
        if not encodings_na_foto:
            return None, "Nenhum rosto detectado na câmera. Tente novamente."
            
        encoding_desconhecido = encodings_na_foto[0]
        
        for servidor in servidores_ativos:
            if not servidor.face_encoding:
                continue
            try:
                encoding_conhecido = np.array(json.loads(servidor.face_encoding))
                # Tolerance ajustada para 0.5 para maior precisão
                match = face_recognition.compare_faces([encoding_conhecido], encoding_desconhecido, tolerance=0.5)
                if match[0]:
                    return servidor, "Sucesso"
            except Exception as e:
                print(f"Erro encoding servidor {servidor.nome}: {e}")
                continue

        return None, "Rosto não reconhecido no sistema."
    except Exception as e:
        return None, f"Erro técnico na identificação: {str(e)}"

# --- CORREÇÃO 2: Adicionar a função Haversine no Utils ---
def haversine(lat1, lon1, lat2, lon2):
    """
    Calcula a distância em metros entre dois pontos (latitude/longitude).
    """
    R = 6371000  # Raio da Terra em metros
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    distance = R * c
    return distance

# --- Configuração do Supabase ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase_client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Erro ao conectar Supabase Storage: {e}")

def upload_arquivo_para_nuvem(file, pasta="geral"):
    if not file or file.filename == '':
        return None
    if not supabase_client:
        print("Supabase Storage não configurado!")
        return None
    try:
        extensao = file.filename.rsplit('.', 1)[1].lower()
        import uuid
        nome_arquivo = f"{uuid.uuid4().hex}.{extensao}"
        caminho_completo = f"{pasta}/{nome_arquivo}"
        file_bytes = file.read()
        
        supabase_client.storage.from_("gestoor-arquivos").upload(
            path=caminho_completo,
            file=file_bytes,
            file_options={"content-type": file.content_type}
        )
        public_url = supabase_client.storage.from_("gestoor-arquivos").get_public_url(caminho_completo)
        file.seek(0)
        return public_url
    except Exception as e:
        print(f"Erro no upload para Supabase: {e}")
        return None