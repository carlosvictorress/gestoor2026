# protocolo_routes.py

import os
import io # Adicionado para o PDF
from werkzeug.utils import secure_filename
from flask import current_app, make_response # Adicionado make_response
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
from extensions import db, bcrypt
from models import Protocolo, Tramitacao, Anexo
from datetime import datetime
# Importações para gerar o PDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm
from sqlalchemy.orm import joinedload
from flask import send_from_directory
from utils import role_required

# Criação do Blueprint (sem alterações)
protocolo_bp = Blueprint(
    'protocolo', 
    __name__, 
    template_folder='templates', 
    url_prefix='/protocolo'
)

# Decorator de login (sem alterações)
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- ALTERAÇÃO APLICADA AQUI ---
# 1. Função para gerar o número do protocolo
def gerar_numero_protocolo():
    """
    Gera um número de protocolo único no formato ANO-MÊS-SEQUENCIAL.
    Ex: 2025-09-001
    """
    now = datetime.now()
    ano = now.year
    mes = now.month

    # Conta quantos protocolos já existem neste mês e ano
    ultimo_protocolo_do_mes = Protocolo.query.filter(
        db.extract('year', Protocolo.data_criacao) == ano,
        db.extract('month', Protocolo.data_criacao) == mes
    ).count()

    # O próximo número será a contagem + 1
    proximo_sequencial = ultimo_protocolo_do_mes + 1
    
    # Formata o número final (ex: 2025-09-001)
    numero_formatado = f"{ano}-{mes:02d}-{proximo_sequencial:03d}"
    
    return numero_formatado

# Rota principal do módulo (Dashboard) - sem alterações
@protocolo_bp.route('/dashboard')
@login_required
@role_required('RH', 'admin')
def dashboard():
    total_protocolos = Protocolo.query.count()
    protocolos_em_tramitacao = Protocolo.query.filter(Protocolo.status == 'Em Tramitação').count()
    protocolos_abertos = Protocolo.query.filter(Protocolo.status == 'Aberto').count()
    protocolos_finalizados = Protocolo.query.filter(Protocolo.status == 'Finalizado').count()

    return render_template('protocolo_dashboard.html',
                           total_protocolos=total_protocolos,
                           protocolos_em_tramitacao=protocolos_em_tramitacao,
                           protocolos_abertos=protocolos_abertos,
                           protocolos_finalizados=protocolos_finalizados)

# Rota para listar todos os protocolos - sem alterações
@protocolo_bp.route('/')
@login_required
@role_required('RH', 'admin')
def listar_protocolos():
    # Pega os parâmetros de pesquisa da URL
    q_numero = request.args.get('q_numero', '')
    q_interessado = request.args.get('q_interessado', '')
    q_assunto = request.args.get('q_assunto', '')
    q_status = request.args.get('q_status', '')
    
    # Começa com a query base
    query = Protocolo.query

    # Aplica os filtros se os campos de pesquisa forem preenchidos
    if q_numero:
        query = query.filter(Protocolo.numero_protocolo.ilike(f'%{q_numero}%'))
    if q_interessado:
        query = query.filter(Protocolo.interessado.ilike(f'%{q_interessado}%'))
    if q_assunto:
        query = query.filter(Protocolo.assunto.ilike(f'%{q_assunto}%'))
    if q_status:
        query = query.filter(Protocolo.status == q_status)

    # Executa a query final
    protocolos = query.order_by(Protocolo.data_criacao.desc()).all()
    
    # Envia os termos da pesquisa de volta para o template para manter os campos preenchidos
    return render_template('listar_protocolos.html', 
                           protocolos=protocolos,
                           q_numero=q_numero,
                           q_interessado=q_interessado,
                           q_assunto=q_assunto,
                           q_status=q_status)

# Rota para criar um novo protocolo (com a chamada à nova função)
@protocolo_bp.route('/novo', methods=['GET', 'POST'])
@login_required
@role_required('RH', 'admin')
def novo_protocolo():
    if request.method == 'POST':
        try:
            assunto = request.form.get('assunto')
            tipo_documento = request.form.get('tipo_documento')
            interessado = request.form.get('interessado')
            setor_origem = request.form.get('setor_origem')
            ficheiros = request.files.getlist('anexos')

            if not all([assunto, tipo_documento, interessado, setor_origem]):
                flash('Todos os campos marcados com * são obrigatórios.', 'warning')
                return render_template('protocolo_form.html')

            novo_p = Protocolo(
                numero_protocolo=gerar_numero_protocolo(), # <-- USA A NOVA FUNÇÃO
                assunto=assunto,
                tipo_documento=tipo_documento,
                interessado=interessado,
                setor_origem=setor_origem,
                setor_atual=setor_origem,
                status='Aberto'
            )
            db.session.add(novo_p)
            
            pasta_protocolos = os.path.join(current_app.config['UPLOAD_FOLDER'], 'protocolos')
            os.makedirs(pasta_protocolos, exist_ok=True)

            for ficheiro in ficheiros:
                if ficheiro and ficheiro.filename != '':
                    nome_seguro = secure_filename(ficheiro.filename)
                    nome_unico = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{nome_seguro}"
                    caminho_salvar = os.path.join(pasta_protocolos, nome_unico)
                    ficheiro.save(caminho_salvar)
                    
                    novo_anexo = Anexo(
                        protocolo=novo_p,
                        nome_arquivo=nome_unico,
                        nome_original=ficheiro.filename
                    )
                    db.session.add(novo_anexo)

            db.session.commit()
            flash(f'Protocolo {novo_p.numero_protocolo} registado com sucesso!', 'success')
            return redirect(url_for('protocolo.listar_protocolos'))

        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao registar o protocolo: {e}', 'danger')

    return render_template('protocolo_form.html')

# 2. NOVA ROTA PARA IMPRIMIR O COMPROVANTE
@protocolo_bp.route('/comprovante/<int:protocolo_id>')
@login_required
@role_required('RH', 'admin')
def imprimir_comprovante(protocolo_id):
    protocolo = Protocolo.query.get_or_404(protocolo_id)
    
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    
    # Desenha o comprovante
    p.setFont("Helvetica-Bold", 16)
    p.drawString(2*cm, 25*cm, "Comprovante de Protocolo")
    p.line(2*cm, 24.8*cm, 19*cm, 24.8*cm)

    p.setFont("Helvetica", 12)
    p.drawString(2*cm, 23.5*cm, f"Número do Protocolo: {protocolo.numero_protocolo}")
    p.drawString(2*cm, 22.5*cm, f"Data de Abertura: {protocolo.data_criacao.strftime('%d/%m/%Y às %H:%M:%S')}")
    p.drawString(2*cm, 21.5*cm, f"Interessado: {protocolo.interessado}")
    p.drawString(2*cm, 20.5*cm, f"Tipo de Documento: {protocolo.tipo_documento}")
    
    p.setFont("Helvetica-Bold", 12)
    p.drawString(2*cm, 19.5*cm, "Assunto:")
    p.setFont("Helvetica", 12)
    # Lógica para quebrar o texto do assunto em várias linhas
    texto_assunto = p.beginText(2*cm, 18.8*cm)
    texto_assunto.setFont("Helvetica", 12)
    for linha in protocolo.assunto.split('\n'):
        texto_assunto.textLine(linha)
    p.drawText(texto_assunto)

    p.setFont("Helvetica-Oblique", 10)
    p.drawString(2*cm, 4*cm, "Documento gerado pelo sistema SysEduca.")
    
    p.showPage()
    p.save()
    
    buffer.seek(0)
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=comprovante_{protocolo.numero_protocolo}.pdf'
    return response

# 3. NOVA ROTA PARA MUDAR O STATUS VIA MODAL
@protocolo_bp.route('/mudar-status', methods=['POST'])
@login_required
@role_required('RH', 'admin')
def mudar_status_protocolo():
    try:
        protocolo_id = request.form.get('protocolo_id')
        novo_status = request.form.get('novo_status')
        motivo = request.form.get('motivo_cancelamento')
        
        protocolo = Protocolo.query.get_or_404(protocolo_id)
        
        # Guarda o status antigo para o registo de tramitação
        status_antigo = protocolo.status
        protocolo.status = novo_status
        
        if novo_status == 'Cancelado':
            protocolo.motivo_cancelamento = motivo
        else:
            protocolo.motivo_cancelamento = None # Limpa o motivo se o status mudar para outro

        # Cria um registo de tramitação para a mudança de status
        nova_tramitacao = Tramitacao(
            protocolo_id=protocolo.id,
            setor_origem=protocolo.setor_atual,
            setor_destino="N/A (Mudança de Status)",
            usuario_responsavel=session.get('username', 'Sistema'),
            despacho=f"Status alterado de '{status_antigo}' para '{novo_status}'.\nMotivo: {motivo if motivo else 'N/A'}"
        )
        db.session.add(nova_tramitacao)
        db.session.commit()
        
        flash(f'Status do protocolo {protocolo.numero_protocolo} alterado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao alterar o status: {e}', 'danger')
        
    return redirect(url_for('protocolo.listar_protocolos'))
    
    
# NOVA ROTA: Para ver os detalhes de um protocolo e fazer a tramitação
@protocolo_bp.route('/detalhes/<int:protocolo_id>', methods=['GET', 'POST'])
@login_required
@role_required('RH', 'admin')
def detalhes_protocolo(protocolo_id):
    # Usamos joinedload para já carregar os anexos e tramitações e evitar múltiplas queries
    protocolo = Protocolo.query.options(
        joinedload(Protocolo.anexos),
        joinedload(Protocolo.tramitacoes)
    ).get_or_404(protocolo_id)

    # Lógica para enviar o processo para outro setor (tramitar)
    if request.method == 'POST':
        try:
            setor_destino = request.form.get('setor_destino')
            despacho = request.form.get('despacho')
            
            if not setor_destino:
                flash('É necessário selecionar um setor de destino.', 'warning')
                return redirect(url_for('protocolo.detalhes_protocolo', protocolo_id=protocolo_id))

            # Cria o registo da movimentação
            nova_tramitacao = Tramitacao(
                protocolo_id=protocolo.id,
                setor_origem=protocolo.setor_atual,
                setor_destino=setor_destino,
                despacho=despacho,
                usuario_responsavel=session.get('username', 'Sistema')
            )
            
            # Atualiza o protocolo
            protocolo.setor_atual = setor_destino
            protocolo.status = 'Em Tramitação'
            
            db.session.add(nova_tramitacao)
            db.session.commit()
            
            flash(f'Protocolo enviado para o setor "{setor_destino}" com sucesso!', 'success')
        
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao tramitar o processo: {e}', 'danger')

        return redirect(url_for('protocolo.detalhes_protocolo', protocolo_id=protocolo_id))

    # --- ALTERAÇÃO APLICADA AQUI ---
    # 2. Adiciona "Administracao" à lista de setores para tramitação
    setores = ["Gabinete do Secretário", "Recursos Humanos", "Departamento Pedagógico", "Transporte Escolar", "Almoxarifado", "Financeiro", "Arquivo", "Administracao"]
    return render_template('protocolo_detalhes.html', protocolo=protocolo, setores=setores)

# NOVA ROTA: Para fazer o download dos ficheiros anexados
@protocolo_bp.route('/anexo/download/<int:anexo_id>')
@login_required
@role_required('RH', 'admin')
def download_anexo(anexo_id):
    anexo = Anexo.query.get_or_404(anexo_id)
    pasta_protocolos = os.path.join(current_app.config['UPLOAD_FOLDER'], 'protocolos')
    return send_from_directory(directory=pasta_protocolos, path=anexo.nome_arquivo, as_attachment=True, download_name=anexo.nome_original)
