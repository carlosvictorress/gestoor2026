# protocolo_routes.py
import os
import io
import uuid
import csv
from datetime import datetime, timedelta, date
from functools import wraps
from werkzeug.utils import secure_filename

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session, 
    make_response, jsonify, send_from_directory, current_app
)
from sqlalchemy import extract, func, or_, and_
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    Protocolo, Tramitacao, Anexo, TipoProtocolo, ModeloDocumento, 
    LogAuditoriaProtocolo, NotificacaoProtocolo
)
from utils import role_required

# Importação do ReportLab para PDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

try:
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    HAS_QRCODE = True
except Exception:
    HAS_QRCODE = False

protocolo_bp = Blueprint(
    'protocolo', 
    __name__, 
    template_folder='templates', 
    url_prefix='/protocolo'
)

# Setores municipais padrão do sistema
SETORES_SISTEMA = [
    "Gabinete do Secretário",
    "Recursos Humanos (RH)",
    "Departamento Pedagógico",
    "Transporte Escolar",
    "Merenda Escolar",
    "Almoxarifado & Patrimônio",
    "Financeiro & Contabilidade",
    "Compras & Licitação",
    "Ouvidoria & Inspeção",
    "Assessoria Jurídica",
    "Arquivo Geral",
    "Administração Geral"
]

# Decorator de login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# Função de Schema Self-Healing & Seed
def garantir_schema_protocolo():
    try:
        db.create_all()
        # Executa migrações seguras de colunas em SQLite ou Postgres
        colunas = [
            ("protocolo", "ano", "INTEGER"),
            ("protocolo", "prioridade", "VARCHAR(20) DEFAULT 'Média'"),
            ("protocolo", "prazo_dias", "INTEGER DEFAULT 5"),
            ("protocolo", "data_limite", "TIMESTAMP"),
            ("protocolo", "sigilo", "VARCHAR(20) DEFAULT 'Público'"),
            ("protocolo", "descricao", "TEXT"),
            ("protocolo", "tags", "VARCHAR(255)"),
            ("protocolo", "solicitante_tipo", "VARCHAR(20) DEFAULT 'Pessoa Física'"),
            ("protocolo", "solicitante_cpf_cnpj", "VARCHAR(20)"),
            ("protocolo", "solicitante_nome", "VARCHAR(200)"),
            ("protocolo", "solicitante_email", "VARCHAR(120)"),
            ("protocolo", "solicitante_telefone", "VARCHAR(50)"),
            ("protocolo", "solicitante_cargo", "VARCHAR(100)"),
            ("protocolo", "solicitante_matricula", "VARCHAR(50)"),
            ("protocolo", "codigo_validacao", "VARCHAR(64)"),
            ("protocolo", "qrcode_hash", "VARCHAR(64)"),
            ("tramitacao", "prazo_dias", "INTEGER"),
            ("tramitacao", "data_limite", "TIMESTAMP"),
            ("tramitacao", "status_prazo", "VARCHAR(50) DEFAULT 'Dentro do Prazo'"),
            ("tramitacao", "assinatura_digital_tipo", "VARCHAR(50) DEFAULT 'Interna'"),
            ("tramitacao", "assinatura_hash", "VARCHAR(255)"),
            ("anexo", "tamanho_bytes", "INTEGER DEFAULT 0"),
            ("anexo", "extensao", "VARCHAR(20)"),
            ("anexo", "versao", "INTEGER DEFAULT 1"),
            ("anexo", "usuario_uploader", "VARCHAR(100)")
        ]
        
        for tabela, col_nome, col_tipo in colunas:
            try:
                with db.engine.begin() as conn:
                    conn.execute(db.text(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {col_nome} {col_tipo};"))
            except Exception:
                try:
                    with db.engine.begin() as conn:
                        conn.execute(db.text(f"ALTER TABLE {tabela} ADD COLUMN {col_nome} {col_tipo};"))
                except Exception:
                    pass

        # Popula Tipos Padrão se estiver vazio
        if TipoProtocolo.query.count() == 0:
            tipos_iniciais = [
                ("Transporte Escolar", "Solicitações de rotas, passes e veículos escolares", 5, "Transporte Escolar"),
                ("Recursos Humanos (RH)", "Requerimentos de servidores, férias, licenças e contracheques", 5, "Recursos Humanos (RH)"),
                ("Merenda Escolar", "Vistorias, laudos de estoques e cardápios", 3, "Merenda Escolar"),
                ("Compras & Licitação", "Aquisições de suprimentos, bens e contratações", 10, "Compras & Licitação"),
                ("Assessoria Jurídica", "Pareceres jurídicos, processos e sindicâncias", 15, "Assessoria Jurídica"),
                ("Patrimônio & Almoxarifado", "Transferência e tombamento de equipamentos escolares", 5, "Almoxarifado & Patrimônio"),
                ("Matrícula & Transferência", "Transferências de alunos e documentos escolares", 2, "Departamento Pedagógico"),
                ("Ouvidoria & Elogios/Denúncias", "Manifestações de cidadãos e servidores", 5, "Ouvidoria & Inspeção"),
                ("Ofício & Memorando", "Comunicação oficial interna e externa", 3, "Gabinete do Secretário"),
                ("Prestação de Contas & Convênios", "Análise financeira de recursos e verbas escolares", 15, "Financeiro & Contabilidade")
            ]
            for nome, desc, prazo, setor in tipos_iniciais:
                tp = TipoProtocolo(nome=nome, descricao=desc, prazo_padrao_dias=prazo, setor_padrao=setor)
                db.session.add(tp)
            db.session.commit()

        # Popula Modelos de Documento Padrão se estiver vazio
        if ModeloDocumento.query.count() == 0:
            modelos_iniciais = [
                ("Despacho Deferido Padrão", "Despacho", "<p><b>DESPACHO DEFERIDO</b></p><p>Analisados os autos do presente processo, <b>DEFIRO</b> o pedido formulado pelo solicitante, visto o preenchimento de todos os requisitos legais e regulamentares.</p><p>Ao setor competente para providências cabíveis.</p>"),
                ("Despacho Indeferido Padrão", "Despacho", "<p><b>DESPACHO INDEFERIDO</b></p><p>Após análise técnica e documental, <b>INDEFIRO</b> o presente requerimento com base nas justificativas anexas aos autos.</p><p>Notifique-se o interessado para fins de direito.</p>"),
                ("Encaminhamento para Instrução", "Despacho", "<p><b>ENCAMINHAMENTO TÉCNICO</b></p><p>Encaminho o presente processo ao setor responsável para análise técnica, manifestação e elaboração de parecer competente no prazo regulamentar.</p>"),
                ("Solicitação de Documentos Complementares", "Despacho", "<p><b>DILIGÊNCIA DOCUMENTAL</b></p><p>Solicita-se ao interessado a apresentação dos seguintes documentos complementares para prosseguimento do feito:</p><ul><li>Cópia de documento oficial com foto;</li><li>Comprovante de residência atualizado.</li></ul>")
            ]
            for tit, tipo_doc, cont in modelos_iniciais:
                md = ModeloDocumento(titulo=tit, tipo_documento=tipo_doc, conteudo=cont)
                db.session.add(md)
            db.session.commit()

    except Exception as e:
        print(f"Aviso ao inicializar schema do protocolo: {e}")


def registrar_auditoria(protocolo_id, acao, detalhes=""):
    try:
        usuario = session.get('username', 'Sistema')
        ip_cliente = request.remote_addr if request else '127.0.0.1'
        log = LogAuditoriaProtocolo(
            protocolo_id=protocolo_id,
            usuario=usuario,
            acao=acao,
            detalhes=detalhes,
            ip=ip_cliente
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Erro ao registrar auditoria: {e}")


def gerar_numero_protocolo():
    """Format: YYYY-MM-SEQ (Ex: 2026-08-001)"""
    now = datetime.now()
    ano = now.year
    mes = now.month

    ultimo_protocolo = Protocolo.query.filter(
        extract('year', Protocolo.data_criacao) == ano,
        extract('month', Protocolo.data_criacao) == mes
    ).count()

    seq = ultimo_protocolo + 1
    return f"{ano}-{mes:02d}-{seq:03d}"


# --- ROTAS PRINCIPAIS ---

@protocolo_bp.route('/dashboard')
@login_required
def dashboard():
    garantir_schema_protocolo()
    
    hoje = date.today()
    ano_atual = hoje.year

    total_protocolos = Protocolo.query.count()
    protocolos_hoje_recebidos = Protocolo.query.filter(func.date(Protocolo.data_criacao) == hoje).count()
    
    # Movimentações/Enviados hoje
    protocolos_hoje_enviados = Tramitacao.query.filter(func.date(Tramitacao.data_envio) == hoje).count()
    
    protocolos_pendentes = Protocolo.query.filter(Protocolo.status.in_(['Aberto', 'Em Tramitação', 'Em Análise', 'Aguardando Documentos'])).all()
    protocolos_concluidos = Protocolo.query.filter(Protocolo.status.in_(['Finalizado', 'Concluído', 'Arquivado', 'Deferido', 'Indeferido'])).count()

    # Cálculo de SLA
    atrasados = 0
    vencendo_hoje = 0
    no_prazo = 0

    for p in protocolos_pendentes:
        status_sla = p.status_sla
        if status_sla == 'Atrasado':
            atrasados += 1
        elif status_sla == 'Vence Hoje':
            vencendo_hoje += 1
        else:
            no_prazo += 1

    # Distribuição por Setor Atual
    setor_counts = db.session.query(Protocolo.setor_atual, func.count(Protocolo.id)).group_by(Protocolo.setor_atual).all()
    distribuicao_setor = {s[0]: s[1] for s in setor_counts if s[0]}

    # Distribuição por Tipo
    tipo_counts = db.session.query(Protocolo.tipo_documento, func.count(Protocolo.id)).group_by(Protocolo.tipo_documento).all()
    distribuicao_tipo = {t[0]: t[1] for t in tipo_counts if t[0]}

    # Distribuição por Prioridade
    prio_counts = db.session.query(Protocolo.prioridade, func.count(Protocolo.id)).group_by(Protocolo.prioridade).all()
    distribuicao_prioridade = {pr[0]: pr[1] for pr in prio_counts if pr[0]}

    # Tempo médio de resposta em dias
    protocolos_fechados = Protocolo.query.filter(Protocolo.status.in_(['Finalizado', 'Concluído', 'Arquivado'])).all()
    tempos = []
    for p in protocolos_fechados:
        if p.data_criacao:
            # Pega a data da última tramitação ou data atual
            data_fim = p.tramitacoes[-1].data_envio if p.tramitacoes else datetime.utcnow()
            dias = (data_fim - p.data_criacao).total_seconds() / 86400.0
            tempos.append(max(0.5, dias))
    
    tempo_medio = round(sum(tempos) / len(tempos), 1) if tempos else 0.0

    return render_template(
        'protocolo_dashboard.html',
        total_protocolos=total_protocolos,
        protocolos_hoje_recebidos=protocolos_hoje_recebidos,
        protocolos_hoje_enviados=protocolos_hoje_enviados,
        protocolos_pendentes=len(protocolos_pendentes),
        atrasados=atrasados,
        vencendo_hoje=vencendo_hoje,
        no_prazo=no_prazo,
        protocolos_concluidos=protocolos_concluidos,
        tempo_medio=tempo_medio,
        distribuicao_setor=distribuicao_setor,
        distribuicao_tipo=distribuicao_tipo,
        distribuicao_prioridade=distribuicao_prioridade
    )


@protocolo_bp.route('/')
@login_required
def listar_protocolos():
    garantir_schema_protocolo()

    q_numero = request.args.get('q_numero', '').strip()
    q_interessado = request.args.get('q_interessado', '').strip()
    q_assunto = request.args.get('q_assunto', '').strip()
    q_status = request.args.get('q_status', '').strip()
    q_setor = request.args.get('q_setor', '').strip()
    q_prioridade = request.args.get('q_prioridade', '').strip()
    q_sla = request.args.get('q_sla', '').strip()
    q_busca_geral = request.args.get('q_busca_geral', '').strip()

    query = Protocolo.query

    if q_busca_geral:
        termo = f"%{q_busca_geral}%"
        query = query.filter(
            or_(
                Protocolo.numero_protocolo.ilike(termo),
                Protocolo.interessado.ilike(termo),
                Protocolo.assunto.ilike(termo),
                Protocolo.solicitante_cpf_cnpj.ilike(termo),
                Protocolo.tags.ilike(termo),
                Protocolo.setor_atual.ilike(termo)
            )
        )
    else:
        if q_numero:
            query = query.filter(Protocolo.numero_protocolo.ilike(f'%{q_numero}%'))
        if q_interessado:
            query = query.filter(
                or_(
                    Protocolo.interessado.ilike(f'%{q_interessado}%'),
                    Protocolo.solicitante_nome.ilike(f'%{q_interessado}%'),
                    Protocolo.solicitante_cpf_cnpj.ilike(f'%{q_interessado}%')
                )
            )
        if q_assunto:
            query = query.filter(Protocolo.assunto.ilike(f'%{q_assunto}%'))
        if q_status:
            query = query.filter(Protocolo.status == q_status)
        if q_setor:
            query = query.filter(Protocolo.setor_atual == q_setor)
        if q_prioridade:
            query = query.filter(Protocolo.prioridade == q_prioridade)

    todos_protocolos = query.order_by(Protocolo.data_criacao.desc()).all()

    # Filtragem por SLA se solicitada
    if q_sla:
        protocolos_filtrados = [p for p in todos_protocolos if p.status_sla == q_sla]
    else:
        protocolos_filtrados = todos_protocolos

    tipos_protocolo = TipoProtocolo.query.filter_by(ativo=True).all()

    return render_template(
        'listar_protocolos.html', 
        protocolos=protocolos_filtrados,
        total_encontrados=len(protocolos_filtrados),
        setores=SETORES_SISTEMA,
        tipos_protocolo=tipos_protocolo,
        q_numero=q_numero,
        q_interessado=q_interessado,
        q_assunto=q_assunto,
        q_status=q_status,
        q_setor=q_setor,
        q_prioridade=q_prioridade,
        q_sla=q_sla,
        q_busca_geral=q_busca_geral
    )


@protocolo_bp.route('/novo', methods=['GET', 'POST'])
@login_required
def novo_protocolo():
    garantir_schema_protocolo()

    if request.method == 'POST':
        try:
            tipo_documento = request.form.get('tipo_documento')
            assunto = request.form.get('assunto')
            descricao = request.form.get('descricao', '')
            prioridade = request.form.get('prioridade', 'Média')
            sigilo = request.form.get('sigilo', 'Público')
            setor_destinatario = request.form.get('setor_origem')
            prazo_dias = int(request.form.get('prazo_dias', 5))
            tags = request.form.get('tags', '')

            solicitante_tipo = request.form.get('solicitante_tipo', 'Pessoa Física')
            solicitante_nome = request.form.get('solicitante_nome', '').strip()
            solicitante_cpf_cnpj = request.form.get('solicitante_cpf_cnpj', '').strip()
            solicitante_email = request.form.get('solicitante_email', '').strip()
            solicitante_telefone = request.form.get('solicitante_telefone', '').strip()
            solicitante_cargo = request.form.get('solicitante_cargo', '').strip()
            solicitante_matricula = request.form.get('solicitante_matricula', '').strip()

            interessado_final = solicitante_nome if solicitante_nome else "Não informado"

            if not all([assunto, tipo_documento, interessado_final, setor_destinatario]):
                flash('Todos os campos marcados com * são de preenchimento obrigatório.', 'warning')
                tipos = TipoProtocolo.query.filter_by(ativo=True).all()
                return render_template('protocolo_form.html', setores=SETORES_SISTEMA, tipos=tipos)

            num_proto = gerar_numero_protocolo()
            data_criacao = datetime.utcnow()
            data_limite = data_criacao + timedelta(days=prazo_dias)
            codigo_validacao = uuid.uuid4().hex[:12].upper()

            novo_p = Protocolo(
                numero_protocolo=num_proto,
                ano=data_criacao.year,
                assunto=assunto,
                tipo_documento=tipo_documento,
                interessado=interessado_final,
                setor_origem=setor_destinatario,
                setor_atual=setor_destinatario,
                status='Aberto',
                prioridade=prioridade,
                prazo_dias=prazo_dias,
                data_limite=data_limite,
                sigilo=sigilo,
                descricao=descricao,
                tags=tags,
                solicitante_tipo=solicitante_tipo,
                solicitante_cpf_cnpj=solicitante_cpf_cnpj,
                solicitante_nome=solicitante_nome,
                solicitante_email=solicitante_email,
                solicitante_telefone=solicitante_telefone,
                solicitante_cargo=solicitante_cargo,
                solicitante_matricula=solicitante_matricula,
                codigo_validacao=codigo_validacao,
                qrcode_hash=codigo_validacao
            )
            db.session.add(novo_p)
            db.session.flush()

            # Upload de Múltiplos Anexos
            ficheiros = request.files.getlist('anexos')
            pasta_protocolos = os.path.join(current_app.config['UPLOAD_FOLDER'], 'protocolos')
            os.makedirs(pasta_protocolos, exist_ok=True)

            for ficheiro in ficheiros:
                if ficheiro and ficheiro.filename != '':
                    nome_seguro = secure_filename(ficheiro.filename)
                    extensao = nome_seguro.split('.')[-1].lower() if '.' in nome_seguro else ''
                    nome_unico = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}_{nome_seguro}"
                    caminho_salvar = os.path.join(pasta_protocolos, nome_unico)
                    ficheiro.save(caminho_salvar)
                    
                    tamanho = os.path.getsize(caminho_salvar) if os.path.exists(caminho_salvar) else 0

                    novo_anexo = Anexo(
                        protocolo_id=novo_p.id,
                        nome_arquivo=nome_unico,
                        nome_original=ficheiro.filename,
                        extensao=extensao,
                        tamanho_bytes=tamanho,
                        usuario_uploader=session.get('username', 'Sistema')
                    )
                    db.session.add(novo_anexo)

            # Tramitação inicial automatizada
            tramitacao_inicial = Tramitacao(
                protocolo_id=novo_p.id,
                setor_origem="Abertura de Processo",
                setor_destino=setor_destinatario,
                despacho=f"Processo aberto e autuado no setor {setor_destinatario}. Prioridade: {prioridade}. Sigilo: {sigilo}.",
                usuario_responsavel=session.get('username', 'Sistema'),
                prazo_dias=prazo_dias,
                data_limite=data_limite
            )
            db.session.add(tramitacao_inicial)

            db.session.commit()
            registrar_auditoria(novo_p.id, "Autuação de Protocolo", f"Número {novo_p.numero_protocolo} criado para {interessado_final}")

            flash(f'Protocolo {novo_p.numero_protocolo} registrado e autuado com sucesso!', 'success')
            return redirect(url_for('protocolo.detalhes_protocolo', protocolo_id=novo_p.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao registrar o protocolo: {e}', 'danger')

    tipos = TipoProtocolo.query.filter_by(ativo=True).all()
    return render_template('protocolo_form.html', setores=SETORES_SISTEMA, tipos=tipos)


@protocolo_bp.route('/detalhes/<int:protocolo_id>', methods=['GET', 'POST'])
@login_required
def detalhes_protocolo(protocolo_id):
    garantir_schema_protocolo()

    protocolo = Protocolo.query.options(
        joinedload(Protocolo.anexos),
        joinedload(Protocolo.tramitacoes),
        joinedload(Protocolo.logs_auditoria)
    ).get_or_404(protocolo_id)

    # Processamento de Nova Tramitação (Envio para outro setor)
    if request.method == 'POST':
        try:
            setor_destino = request.form.get('setor_destino')
            despacho = request.form.get('despacho', '').strip()
            prazo_dias = int(request.form.get('prazo_dias', protocolo.prazo_dias or 5))
            tipo_assinatura = request.form.get('tipo_assinatura', 'Interna')

            if not setor_destino:
                flash('Selecione um setor de destino para tramitar.', 'warning')
                return redirect(url_for('protocolo.detalhes_protocolo', protocolo_id=protocolo_id))

            usuario_atual = session.get('username', 'Sistema')
            data_limite = datetime.utcnow() + timedelta(days=prazo_dias)

            # Assinatura Hash simulada para ICP-Brasil/Gov.br/Interna
            hash_assinatura = f"SIG-{uuid.uuid4().hex[:16].upper()}"

            nova_tramitacao = Tramitacao(
                protocolo_id=protocolo.id,
                setor_origem=protocolo.setor_atual,
                setor_destino=setor_destino,
                despacho=despacho,
                usuario_responsavel=usuario_atual,
                prazo_dias=prazo_dias,
                data_limite=data_limite,
                assinatura_digital_tipo=tipo_assinatura,
                assinatura_hash=hash_assinatura
            )

            protocolo.setor_atual = setor_destino
            protocolo.status = 'Em Tramitação'
            protocolo.data_limite = data_limite

            db.session.add(nova_tramitacao)

            # Upload de anexos complementares na tramitação
            ficheiros = request.files.getlist('novos_anexos')
            pasta_protocolos = os.path.join(current_app.config['UPLOAD_FOLDER'], 'protocolos')
            os.makedirs(pasta_protocolos, exist_ok=True)

            for ficheiro in ficheiros:
                if ficheiro and ficheiro.filename != '':
                    nome_seguro = secure_filename(ficheiro.filename)
                    extensao = nome_seguro.split('.')[-1].lower() if '.' in nome_seguro else ''
                    nome_unico = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}_{nome_seguro}"
                    caminho_salvar = os.path.join(pasta_protocolos, nome_unico)
                    ficheiro.save(caminho_salvar)
                    
                    tamanho = os.path.getsize(caminho_salvar) if os.path.exists(caminho_salvar) else 0

                    novo_anexo = Anexo(
                        protocolo_id=protocolo.id,
                        nome_arquivo=nome_unico,
                        nome_original=ficheiro.filename,
                        extensao=extensao,
                        tamanho_bytes=tamanho,
                        usuario_uploader=usuario_atual
                    )
                    db.session.add(novo_anexo)

            db.session.commit()
            registrar_auditoria(protocolo.id, "Tramitação de Processo", f"Encaminhado de {protocolo.setor_atual} para {setor_destino}")

            flash(f'Protocolo encaminhado para o setor "{setor_destino}" com sucesso!', 'success')
            return redirect(url_for('protocolo.detalhes_protocolo', protocolo_id=protocolo_id))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao tramitar o processo: {e}', 'danger')

    modelos = ModeloDocumento.query.filter_by(ativo=True).all()
    registrar_auditoria(protocolo.id, "Visualização de Processo", f"Acessado por {session.get('username', 'Usuário')}")

    return render_template(
        'protocolo_detalhes.html', 
        protocolo=protocolo, 
        setores=SETORES_SISTEMA,
        modelos=modelos
    )


@protocolo_bp.route('/mudar-status', methods=['POST'])
@login_required
def mudar_status_protocolo():
    try:
        protocolo_id = request.form.get('protocolo_id')
        novo_status = request.form.get('novo_status')
        motivo = request.form.get('motivo_cancelamento')
        
        protocolo = Protocolo.query.get_or_404(protocolo_id)
        status_antigo = protocolo.status
        protocolo.status = novo_status
        
        if novo_status in ['Cancelado', 'Indeferido']:
            protocolo.motivo_cancelamento = motivo
        else:
            protocolo.motivo_cancelamento = None

        nova_tramitacao = Tramitacao(
            protocolo_id=protocolo.id,
            setor_origem=protocolo.setor_atual,
            setor_destino=protocolo.setor_atual,
            despacho=f"Alteração de Situação: De '{status_antigo}' para '{novo_status}'. Motivo/Observação: {motivo if motivo else 'N/A'}",
            usuario_responsavel=session.get('username', 'Sistema')
        )
        db.session.add(nova_tramitacao)
        db.session.commit()

        registrar_auditoria(protocolo.id, "Mudança de Situação", f"Status alterado para {novo_status}")
        flash(f'Situação do protocolo {protocolo.numero_protocolo} alterada para "{novo_status}"!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao alterar o status: {e}', 'danger')

    return redirect(request.referrer or url_for('protocolo.listar_protocolos'))


# --- CONSULTA PÚBLICA EXTERNA (Sem necessidade de login) ---

@protocolo_bp.route('/consulta-publica', methods=['GET', 'POST'])
def consulta_publica():
    resultado = None
    erro = None

    if request.method == 'POST':
        q_numero = request.form.get('numero_protocolo', '').strip()
        q_documento = request.form.get('cpf_cnpj', '').strip()

        if not q_numero:
            erro = "Informe o Número do Protocolo para realizar a consulta."
        else:
            query = Protocolo.query.filter(Protocolo.numero_protocolo.ilike(f"%{q_numero}%"))
            if q_documento:
                query = query.filter(
                    or_(
                        Protocolo.solicitante_cpf_cnpj.ilike(f"%{q_documento}%"),
                        Protocolo.interessado.ilike(f"%{q_documento}%")
                    )
                )
            resultado = query.first()

            if not resultado:
                erro = "Nenhum protocolo encontrado com as credenciais informadas."
            elif resultado.sigilo != 'Público':
                erro = "Este processo possui nível de sigilo reservado e não pode ser exibido publicamente."

    return render_template('protocolo_consulta_publica.html', resultado=resultado, erro=erro)


@protocolo_bp.route('/validar/<codigo_validacao>')
def validar_qrcode(codigo_validacao):
    protocolo = Protocolo.query.filter(
        or_(
            Protocolo.codigo_validacao == codigo_validacao,
            Protocolo.qrcode_hash == codigo_validacao
        )
    ).first_or_404()

    return render_template('protocolo_validacao_oficial.html', protocolo=protocolo)


# --- EMISSÃO DE COMPROVANTE & PDF OFICIAL SEI ---

@protocolo_bp.route('/comprovante/<int:protocolo_id>')
@login_required
def imprimir_comprovante(protocolo_id):
    protocolo = Protocolo.query.get_or_404(protocolo_id)
    registrar_auditoria(protocolo.id, "Impressão de Comprovante", "Gerado PDF do comprovante oficial")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4, 
        rightMargin=1.5*cm, 
        leftMargin=1.5*cm, 
        topMargin=1.5*cm, 
        bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()
    style_normal = ParagraphStyle('Norm', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=13)
    style_bold = ParagraphStyle('Bold', parent=style_normal, fontName='Helvetica-Bold')
    style_header = ParagraphStyle('Header', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=14, alignment=1, textColor=colors.HexColor('#07132b'))

    elements = []

    # Cabeçalho Institucional
    elements.append(Paragraph("PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ", style_header))
    elements.append(Paragraph("SECRETARIA MUNICIPAL DE EDUCAÇÃO - SEME", ParagraphStyle('Sub', parent=style_header, fontSize=11, textColor=colors.HexColor('#475569'))))
    elements.append(Paragraph("SISTEMA ELETRÔNICO DE PROTOCOLO E TRAMITAÇÃO", ParagraphStyle('Sub2', parent=style_header, fontSize=9, textColor=colors.HexColor('#64748b'))))
    elements.append(Spacer(1, 0.4*cm))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#07132b'), spaceAfter=15))

    # Título do Documento
    elements.append(Paragraph(f"<b>COMPROVANTE OFICIAL DE AUTUAÇÃO DE PROTOCOLO</b>", ParagraphStyle('TitDoc', parent=style_header, fontSize=12, alignment=1)))
    elements.append(Paragraph(f"<b>Nº {protocolo.numero_protocolo}</b>", ParagraphStyle('NumDoc', parent=style_header, fontSize=16, alignment=1, textColor=colors.HexColor('#0d6efd'))))
    elements.append(Spacer(1, 0.5*cm))

    # Tabela de Informações Gerais
    dados_tabela = [
        [Paragraph("<b>Data de Autuação:</b>", style_normal), Paragraph(protocolo.data_criacao.strftime('%d/%m/%Y às %H:%M:%S'), style_normal)],
        [Paragraph("<b>Interessado / Solicitante:</b>", style_normal), Paragraph(f"{protocolo.interessado} ({protocolo.solicitante_tipo or 'N/A'})", style_normal)],
        [Paragraph("<b>CPF / CNPJ:</b>", style_normal), Paragraph(protocolo.solicitante_cpf_cnpj or "Não informado", style_normal)],
        [Paragraph("<b>Tipo de Documento:</b>", style_normal), Paragraph(protocolo.tipo_documento, style_normal)],
        [Paragraph("<b>Assunto:</b>", style_normal), Paragraph(protocolo.assunto, style_normal)],
        [Paragraph("<b>Setor de Origem:</b>", style_normal), Paragraph(protocolo.setor_origem, style_normal)],
        [Paragraph("<b>Setor Atual:</b>", style_normal), Paragraph(protocolo.setor_atual, style_normal)],
        [Paragraph("<b>Prioridade / Sigilo:</b>", style_normal), Paragraph(f"{protocolo.prioridade} | {protocolo.sigilo}", style_normal)],
        [Paragraph("<b>Prazo Limite / SLA:</b>", style_normal), Paragraph(f"{protocolo.data_limite.strftime('%d/%m/%Y') if protocolo.data_limite else 'N/A'} ({protocolo.status_sla})", style_normal)],
        [Paragraph("<b>Código de Autenticidade:</b>", style_normal), Paragraph(protocolo.codigo_validacao or 'N/A', style_bold)]
    ]

    t = Table(dados_tabela, colWidths=[5*cm, 13*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 0.5*cm))

    # Histórico Resumido de Tramitações
    elements.append(Paragraph("<b>HISTÓRICO DE MOVIMENTAÇÕES:</b>", style_bold))
    elements.append(Spacer(1, 0.2*cm))

    tram_dados = [["Data/Hora", "Origem", "Destino", "Responsável"]]
    for tr in protocolo.tramitacoes:
        tram_dados.append([
            tr.data_envio.strftime('%d/%m/%Y %H:%M'),
            tr.setor_origem,
            tr.setor_destino,
            tr.usuario_responsavel or 'Sistema'
        ])

    t_tram = Table(tram_dados, colWidths=[4*cm, 4.5*cm, 4.5*cm, 5*cm])
    t_tram.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#07132b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('PADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(t_tram)
    elements.append(Spacer(1, 0.8*cm))

    # Caixa de Validação QR Code
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#94a3b8'), spaceAfter=10))
    elements.append(Paragraph(f"Autenticidade garantida pelo Gestoor360º. Consulte em <b>/protocolo/consulta-publica</b> utilizando o Código: <b>{protocolo.codigo_validacao}</b>", ParagraphStyle('Rod', parent=style_normal, fontSize=8, alignment=1, textColor=colors.HexColor('#64748b'))))

    doc.build(elements)
    buffer.seek(0)

    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Comprovante_Protocolo_{protocolo.numero_protocolo}.pdf'
    return response


# --- DOWNLOAD DE ANEXOS ---

@protocolo_bp.route('/anexo/download/<int:anexo_id>')
@login_required
def download_anexo(anexo_id):
    anexo = Anexo.query.get_or_404(anexo_id)
    pasta_protocolos = os.path.join(current_app.config['UPLOAD_FOLDER'], 'protocolos')
    registrar_auditoria(anexo.protocolo_id, "Download de Anexo", f"Arquivo: {anexo.nome_original}")
    return send_from_directory(directory=pasta_protocolos, path=anexo.nome_arquivo, as_attachment=True, download_name=anexo.nome_original)


# --- RELATÓRIOS & EXPORTAÇÃO ---

@protocolo_bp.route('/exportar', methods=['GET'])
@login_required
def exportar_relatorio():
    protocolos = Protocolo.query.order_by(Protocolo.data_criacao.desc()).all()

    si = io.StringIO()
    cw = csv.writer(si, delimiter=';')
    cw.writerow(['Numero Protocolo', 'Data Autuacao', 'Interessado', 'CPF/CNPJ', 'Tipo', 'Assunto', 'Setor Atual', 'Prioridade', 'Status', 'SLA Status', 'Codigo Validacao'])

    for p in protocolos:
        cw.writerow([
            p.numero_protocolo,
            p.data_criacao.strftime('%d/%m/%Y %H:%M'),
            p.interessado,
            p.solicitante_cpf_cnpj or '',
            p.tipo_documento,
            p.assunto,
            p.setor_atual,
            p.prioridade,
            p.status,
            p.status_sla,
            p.codigo_validacao or ''
        ])

    output = make_response(si.getvalue().encode('utf-8-sig'))
    output.headers["Content-Disposition"] = "attachment; filename=relatorio_protocolos.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
    return output
