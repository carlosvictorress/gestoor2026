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
