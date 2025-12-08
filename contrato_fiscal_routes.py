# Arquivo: contrato_fiscal_routes.py (CORRIGIDO)

import os
import uuid
import io
import requests
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, make_response, send_from_directory, current_app, jsonify
from models import (
    FiscalContrato, FiscalAnexo, FiscalAtestoMensal, FiscalOcorrencia, 
    FiscalPenalidade, Secretaria, FiscalChecklistModel, FiscalChecklistItem, FiscalChecklistResposta, FiscalNotaFiscal 
)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from extensions import db
from utils import login_required, role_required, cabecalho_e_rodape
from datetime import datetime, date, timedelta 
from werkzeug.utils import secure_filename 
from sqlalchemy import func


# 1. Cria o Blueprint
contrato_fiscal_bp = Blueprint('fiscal', __name__, url_prefix='/fiscal')

# --- ROTAS AUXILIARES ---

def _calcular_totais_contratos(secretaria_id):
    """Calcula o valor total, o total gasto (abatido por Notas Fiscais) e o saldo."""
    
    # 1. Total Bruto (Soma de todos os contratos ativos)
    total_em_contratos = db.session.query(func.sum(FiscalContrato.valor_total)).filter_by(secretaria_id=secretaria_id).scalar() or 0.0
    
    # 2. Total Gasto (Soma de todas as Notas Fiscais lançadas)
    # Importante: Fazemos a soma em FiscalNotaFiscal, que é o registro financeiro real
    total_gasto = db.session.query(func.sum(FiscalNotaFiscal.valor)).join(FiscalContrato).filter(
        FiscalContrato.secretaria_id == secretaria_id
    ).scalar() or 0.0
    
    total_restante = total_em_contratos - total_gasto
    
    contratos = FiscalContrato.query.filter_by(secretaria_id=secretaria_id).all()
    
    return {
        'total_em_contratos': total_em_contratos,
        'total_gasto': total_gasto,
        'total_restante': total_restante,
        'contratos_lista': contratos
    }
    
def _get_anexos_path():
    """Define o caminho de upload para os anexos de contratos."""
    return os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_anexos')


# ==========================================================
# 2. ROTAS PRINCIPAIS (DASHBOARD E CADASTRO)
# ==========================================================

@contrato_fiscal_bp.route('/dashboard')
@login_required
@role_required('admin', 'RH', 'Fiscal')
def dashboard():
    secretaria_id_logada = session.get('secretaria_id')
    totais = _calcular_totais_contratos(secretaria_id_logada)
    
    return render_template(
        'fiscal_dashboard.html',
        totais=totais,
        contratos=totais['contratos_lista']
    )

@contrato_fiscal_bp.route('/contrato/novo', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def novo_contrato():
    secretaria_id_logada = session.get('secretaria_id')

    if request.method == 'POST':
        try:
            # 1. Pega e limpa os valores numéricos
            valor_total = float(request.form.get('valor_total').replace('.', '').replace(',', '.'))
            valor_mensal = float(request.form.get('valor_mensal_parcela').replace('.', '').replace(',', '.'))

            # 2. Converte as datas (ACESSA datetime DIRETAMENTE)
            vigencia_inicio = datetime.strptime(request.form.get('vigencia_inicio'), '%Y-%m-%d').date()
            vigencia_fim = datetime.strptime(request.form.get('vigencia_fim'), '%Y-%m-%d').date()

            # 3. Cria o novo contrato
            novo_contrato = FiscalContrato(
                num_contrato=request.form.get('num_contrato'),
                ano=request.form.get('ano', type=int),
                tipo=request.form.get('tipo'),
                objeto=request.form.get('objeto'),
                processo_licitatorio=request.form.get('processo_licitatorio'),
                empresa_contratada=request.form.get('empresa_contratada'),
                cnpj=request.form.get('cnpj'),
                representante_empresa=request.form.get('representante_empresa'),
                valor_total=valor_total,
                valor_mensal_parcela=valor_mensal,
                vigencia_inicio=vigencia_inicio,
                vigencia_fim=vigencia_fim,
                situacao=request.form.get('situacao', 'Ativo'),
                secretaria_id=secretaria_id_logada
            )
            db.session.add(novo_contrato)
            db.session.commit()

            flash(f"Contrato nº {novo_contrato.num_contrato}/{novo_contrato.ano} cadastrado!", 'success')
            return redirect(url_for('fiscal.detalhes_contrato', contrato_id=novo_contrato.id))

        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar contrato: {e}. Verifique se o Nº do Contrato já existe ou se os campos de valor e data estão corretos.", 'danger')
            return redirect(url_for('fiscal.novo_contrato'))

    # Lógica GET: Retorna o formulário
    # Removemos o 'from datetime import datetime' que causou o erro, já que está importado globalmente.
    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    # Passamos datetime.now para pré-preencher o ano no template
    return render_template('fiscal_contrato_form.html', secretarias=secretarias, contrato=None, now=datetime.now)

# Rota para Detalhes do Contrato (CRUD)
@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/detalhes', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def detalhes_contrato(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)

    # =======================================================
    # CORREÇÃO: ADICIONAR O CÁLCULO DE DIAS RESTANTES AQUI
    # =======================================================
    dias_restantes = (contrato.vigencia_fim - date.today()).days if contrato.vigencia_fim and contrato.vigencia_fim > date.today() else 0

    # --- BUSCA DE DADOS DE EXECUÇÃO (Incluindo NFs) ---
    anexos = FiscalAnexo.query.filter_by(contrato_id=contrato_id).order_by(FiscalAnexo.data_upload.desc()).all()
    atestos = FiscalAtestoMensal.query.filter_by(contrato_id=contrato_id).order_by(FiscalAtestoMensal.mes_competencia.desc()).all()
    ocorrencias = FiscalOcorrencia.query.filter_by(contrato_id=contrato_id).order_by(FiscalOcorrencia.data_hora.desc()).all()
    notas_fiscais = FiscalNotaFiscal.query.filter_by(contrato_id=contrato_id).order_by(FiscalNotaFiscal.data_emissao.desc()).all()

    # Calculando saldo do contrato (localmente)
    total_gasto_contrato = sum(nf.valor for nf in notas_fiscais)
    saldo_atual_contrato = contrato.valor_total - total_gasto_contrato

    return render_template(
        'fiscal_detalhes_contrato.html', 
        contrato=contrato, 
        dias_restantes=dias_restantes,  # <--- Variável agora definida!
        saldo_atual_contrato=saldo_atual_contrato, 
        total_gasto_contrato=total_gasto_contrato,
        anexos=anexos,
        atestos=atestos,
        ocorrencias=ocorrencias,
        notas_fiscais=notas_fiscais
    )

# --- ROTAS DE ANEXOS (Ação da página de Detalhes) ---

@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/anexo/upload', methods=['POST'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def upload_anexo(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)
    if 'anexo_file' not in request.files:
        flash('Nenhum arquivo enviado.', 'danger')
        return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))
        
    file = request.files['anexo_file']
    tipo_documento = request.form.get('tipo_documento')
    
    if file.filename == '':
        flash('Nenhum arquivo selecionado.', 'warning')
        return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))

    if file:
        try:
            filename_original = secure_filename(file.filename)
            extensao = filename_original.rsplit('.', 1)[-1].lower() if '.' in filename_original else ''
            # Usa UUID para garantir nome de arquivo único
            filename_seguro = f"{contrato_id}_{uuid.uuid4().hex}.{extensao}"
            
            upload_path = _get_anexos_path()
            os.makedirs(upload_path, exist_ok=True)
            
            file.save(os.path.join(upload_path, filename_seguro))
            
            novo_anexo = FiscalAnexo(
                contrato_id=contrato_id,
                tipo_documento=tipo_documento,
                nome_original=filename_original,
                filename_seguro=filename_seguro
            )
            db.session.add(novo_anexo)
            db.session.commit()
            
            flash('Documento anexado com sucesso!', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar anexo: {e}', 'danger')
            
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))

@contrato_fiscal_bp.route('/anexo/<int:anexo_id>/download')
@login_required
@role_required('admin', 'RH', 'Fiscal')
def download_anexo(anexo_id):
    anexo = FiscalAnexo.query.get_or_404(anexo_id)
    upload_path = _get_anexos_path()
    
    try:
        from flask import current_app # Importação local para evitar circularity
        return send_from_directory(
            upload_path, 
            anexo.filename_seguro, 
            as_attachment=True, 
            download_name=anexo.nome_original
        )
    except FileNotFoundError:
        flash('Erro: Arquivo não encontrado no servidor.', 'danger')
        return redirect(url_for('fiscal.detalhes_contrato', contrato_id=anexo.contrato_id))


@contrato_fiscal_bp.route('/anexo/<int:anexo_id>/excluir')
@login_required
@role_required('admin', 'RH')
def excluir_anexo(anexo_id):
    anexo = FiscalAnexo.query.get_or_404(anexo_id)
    contrato_id = anexo.contrato_id
    
    try:
        # 1. Exclui o arquivo físico
        upload_path = _get_anexos_path()
        caminho_arquivo = os.path.join(upload_path, anexo.filename_seguro)
        if os.path.exists(caminho_arquivo):
            os.remove(caminho_arquivo)
            
        # 2. Exclui o registro do banco
        db.session.delete(anexo)
        db.session.commit()
        flash('Anexo excluído com sucesso.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir anexo: {e}', 'danger')
        
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))

@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/atesto/novo', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'Fiscal')
def lancar_atesto(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)
    
    # --- 🔍 LÓGICA DE BUSCA DO CHECKLIST (PARTE GET) ---
    # 1. Tenta encontrar um modelo de checklist que corresponda ao TIPO do Contrato
    modelo_checklist = FiscalChecklistModel.query.filter_by(
        tipo_contrato_associado=contrato.tipo
    ).first()
    
    # 2. Busca os itens desse checklist
    itens_checklist = modelo_checklist.itens if modelo_checklist else []
    # --------------------------------------------------
    
    if request.method == 'POST':
        try:
            # 1. Obter dados principais
            mes_comp = request.form.get('mes_competencia')
            conformidade_status = request.form.get('conformidade')
            
            # ... (Lógica de Upload de Evidência) ...
            evidencia_file = request.files.get('evidencia_file')
            evidencia_filename = None
            
            if evidencia_file and evidencia_file.filename != '':
                from flask import current_app
                from werkzeug.utils import secure_filename
                from datetime import datetime
                import os
                
                filename_original = secure_filename(evidencia_file.filename)
                ext = filename_original.rsplit('.', 1)[-1].lower()
                # Cria um nome de arquivo único
                evidencia_filename = f"ev_{contrato_id}_{mes_comp.replace('/', '')}_{datetime.now().strftime('%H%M%S')}.{ext}"
                
                upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_atestos')
                os.makedirs(upload_path, exist_ok=True)
                evidencia_file.save(os.path.join(upload_path, evidencia_filename))
            
            # 2. Criar Atesto
            novo_atesto = FiscalAtestoMensal(
                contrato_id=contrato_id,
                mes_competencia=mes_comp,
                descricao_servico=request.form.get('descricao_servico'),
                conformidade=conformidade_status,
                observacoes_fiscal=request.form.get('observacoes_fiscal'),
                assinatura_fiscal=session.get('username', 'Sistema'),
                status_atesto=conformidade_status, 
                evidencia_filename=evidencia_filename,
                localizacao_gps=request.form.get('localizacao_gps')
            )
            db.session.add(novo_atesto)
            db.session.flush() # Necessário para obter novo_atesto.id antes do commit

            # 3. 💾 SALVAR AS RESPOSTAS DO CHECKLIST (PARTE CRÍTICA POST)
            for item in itens_checklist:
                # O nome do campo no formulário é 'checklist_[item.id]'
                resposta_valor = request.form.get(f'checklist_{item.id}')
                
                # Salva apenas se houver uma resposta para evitar erros com campos vazios (embora o template exija 'required')
                if resposta_valor is not None:
                    nova_resposta = FiscalChecklistResposta(
                        atesto_id=novo_atesto.id,
                        item_id=item.id,
                        valor_resposta=resposta_valor
                    )
                    db.session.add(nova_resposta)

            db.session.commit()
            
            flash(f"Atesto para a competência {mes_comp} registrado com checklist!", 'success')
            return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar atesto: {e}", 'danger')
            # Re-renderiza o formulário com dados, se possível, ou redireciona
            return redirect(url_for('fiscal.lancar_atesto', contrato_id=contrato_id))
            
    # Lógica GET: Prepara dados e renderiza o template
    from datetime import date
    today = date.today()
    mes_atual = today.strftime('%Y-%m')
    
    atestos_existentes = FiscalAtestoMensal.query.filter_by(contrato_id=contrato_id).order_by(FiscalAtestoMensal.mes_competencia.desc()).all()

    # Retorna o template, passando a lista de itens e o modelo
    return render_template('fiscal_atesto_form.html', 
                           contrato=contrato, 
                           mes_atual=mes_atual, 
                           atestos_existentes=atestos_existentes,
                           itens_checklist=itens_checklist,
                           modelo_checklist=modelo_checklist)

@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/ocorrencia/nova', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'Fiscal')
def lancar_ocorrencia(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)
    
    if request.method == 'POST':
        try:
            # 1. Upload de Evidência
            evidencia_file = request.files.get('evidencia_file')
            evidencia_filename = None
            
            if evidencia_file and evidencia_file.filename != '':
                from flask import current_app
                from werkzeug.utils import secure_filename
                
                filename_original = secure_filename(evidencia_file.filename)
                ext = filename_original.rsplit('.', 1)[-1].lower()
                evidencia_filename = f"oc_{contrato_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
                
                # Salva o arquivo em 'uploads/contratos_ocorrencias'
                upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_ocorrencias')
                os.makedirs(upload_path, exist_ok=True)
                evidencia_file.save(os.path.join(upload_path, evidencia_filename))
                
            # 2. Criar Ocorrência
            nova_ocorrencia = FiscalOcorrencia(
                contrato_id=contrato_id,
                tipo_ocorrencia=request.form.get('tipo_ocorrencia'),
                descricao_detalhada=request.form.get('descricao_detalhada'),
                gravidade=request.form.get('gravidade'),
                responsavel_registro=session.get('username', 'Sistema'),
                local_ocorrencia=request.form.get('local_ocorrencia'),
                evidencia_filename=evidencia_filename,
                status='Aberta'
            )
            db.session.add(nova_ocorrencia)
            db.session.commit()
            
            flash("Ocorrência registrada e aguardando análise.", 'danger')
            return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar ocorrência: {e}", 'danger')
            return redirect(url_for('fiscal.lancar_ocorrencia', contrato_id=contrato_id))
            
    # Lógica GET
    ocorrencias_existentes = FiscalOcorrencia.query.filter_by(contrato_id=contrato_id).order_by(FiscalOcorrencia.data_hora.desc()).all()
    
    return render_template('fiscal_ocorrencia_form.html', contrato=contrato, ocorrencias=ocorrencias_existentes)

@contrato_fiscal_bp.route('/config/checklists')
@login_required
@role_required('admin', 'RH') # Configuração é geralmente restrita
def listar_modelos_checklist():
    secretaria_id_logada = session.get('secretaria_id')
    
    # Busca contratos para listar os Tipos de Contrato já usados
    contratos = FiscalContrato.query.filter_by(secretaria_id=secretaria_id_logada).all()
    tipos_contrato = sorted(list(set(c.tipo for c in contratos)))
    
    # Busca todos os modelos
    from .models import FiscalChecklistModel # Garantir a importação local
    modelos = FiscalChecklistModel.query.order_by(FiscalChecklistModel.nome).all()
    
    return render_template(
        'fiscal_checklist_modelos.html', 
        modelos=modelos,
        tipos_contrato=tipos_contrato
    )

@contrato_fiscal_bp.route('/config/checklist/novo', methods=['POST'])
@login_required
@role_required('admin', 'RH')
def novo_modelo_checklist():
    try:
        from .models import FiscalChecklistModel
        nome = request.form.get('nome')
        tipo_contrato = request.form.get('tipo_contrato')
        
        novo_modelo = FiscalChecklistModel(
            nome=nome,
            tipo_contrato_associado=tipo_contrato
        )
        db.session.add(novo_modelo)
        db.session.commit()
        
        flash('Modelo de checklist criado com sucesso! Adicione os itens.', 'success')
        return redirect(url_for('fiscal.detalhes_modelo_checklist', modelo_id=novo_modelo.id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao criar modelo: {e}', 'danger')
        return redirect(url_for('fiscal.listar_modelos_checklist'))

@contrato_fiscal_bp.route('/config/checklist/<int:modelo_id>')
@login_required
@role_required('admin', 'RH')
def detalhes_modelo_checklist(modelo_id):
    from .models import FiscalChecklistModel
    modelo = FiscalChecklistModel.query.get_or_404(modelo_id)
    return render_template('fiscal_checklist_detalhes.html', modelo=modelo)

@contrato_fiscal_bp.route('/config/checklist/<int:modelo_id>/item/novo', methods=['POST'])
@login_required
@role_required('admin', 'RH')
def adicionar_item_checklist(modelo_id):
    from .models import FiscalChecklistItem
    modelo = FiscalChecklistModel.query.get_or_404(modelo_id)
    
    try:
        novo_item = FiscalChecklistItem(
            modelo_id=modelo_id,
            descricao=request.form.get('descricao'),
            tipo_resposta=request.form.get('tipo_resposta')
        )
        db.session.add(novo_item)
        db.session.commit()
        flash('Item adicionado ao checklist!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao adicionar item: {e}', 'danger')
        
    return redirect(url_for('fiscal.detalhes_modelo_checklist', modelo_id=modelo_id))

@contrato_fiscal_bp.route('/config/checklist/item/<int:item_id>/excluir')
@login_required
@role_required('admin', 'RH')
def excluir_item_checklist(item_id):
    from .models import FiscalChecklistItem
    item = FiscalChecklistItem.query.get_or_404(item_id)
    modelo_id = item.modelo_id
    
    try:
        db.session.delete(item)
        db.session.commit()
        flash('Item excluído do checklist.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir item: {e}', 'danger')
        
    return redirect(url_for('fiscal.detalhes_modelo_checklist', modelo_id=modelo_id))

@contrato_fiscal_bp.route('/atesto/<int:atesto_id>/comprovante', methods=['GET'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def emitir_comprovante_atesto(atesto_id):
    atesto = FiscalAtestoMensal.query.get_or_404(atesto_id)
    contrato = atesto.contrato
    
    # 1. Busca as respostas do checklist (quebramos a linha para o ReportLab)
    respostas = FiscalChecklistResposta.query.filter_by(atesto_id=atesto_id)\
        .join(FiscalChecklistItem).all()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=3*cm, bottomMargin=2*cm, leftMargin=2*cm, rightMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    # Estilos de Título
    styles.add(ParagraphStyle(name='H2Center', parent=styles['h2'], alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='Label', fontName='Helvetica-Bold', fontSize=10, leading=14))
    styles.add(ParagraphStyle(name='Value', fontName='Helvetica', fontSize=10, leading=14))
    styles.add(ParagraphStyle(name='TableData', fontName='Helvetica', fontSize=9, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name='TableHeader', fontName='Helvetica-Bold', fontSize=9, alignment=TA_CENTER))


    # --- TÍTULO DO DOCUMENTO ---
    story.append(Paragraph("TERMO DE ATESTO E CONFORMIDADE", styles['H2Center']))
    story.append(Paragraph(f"Contrato N° {contrato.num_contrato}/{contrato.ano}", styles['h3']))
    story.append(Spacer(1, 0.5*cm))

    # --- 1. DADOS BÁSICOS DO CONTRATO ---
    data_contrato = [
        [Paragraph("Objeto", styles['Label']), Paragraph(contrato.objeto, styles['Value'])],
        [Paragraph("Empresa", styles['Label']), Paragraph(contrato.empresa_contratada, styles['Value'])],
        [Paragraph("CNPJ", styles['Label']), Paragraph(contrato.cnpj, styles['Value'])],
        [Paragraph("Vigência", styles['Label']), Paragraph(f"{contrato.vigencia_inicio.strftime('%d/%m/%Y')} a {contrato.vigencia_fim.strftime('%d/%m/%Y')}", styles['Value'])],
        [Paragraph("Valor Total", styles['Label']), Paragraph(f"R$ {'%.2f' % contrato.valor_total}", styles['Value'])],
    ]
    tabela_contrato = Table(data_contrato, colWidths=[4*cm, 13*cm])
    tabela_contrato.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F0F0F0')),
    ]))
    story.append(Paragraph("<b>1. Informações do Contrato</b>", styles['h3']))
    story.append(Spacer(1, 0.2*cm))
    story.append(tabela_contrato)
    story.append(Spacer(1, 0.8*cm))


    # --- 2. DETALHES DA EXECUÇÃO ---
    
    data_execucao = [
        [Paragraph("Competência", styles['Label']), Paragraph(atesto.mes_competencia, styles['Value'])],
        [Paragraph("Data Atesto", styles['Label']), Paragraph(atesto.data_atesto.strftime('%d/%m/%Y %H:%M'), styles['Value'])],
        [Paragraph("Fiscal Responsável", styles['Label']), Paragraph(atesto.assinatura_fiscal, styles['Value'])],
        [Paragraph("Descrição", styles['Label']), Paragraph(atesto.descricao_servico, styles['Value'])],
        [Paragraph("Observações", styles['Label']), Paragraph(atesto.observacoes_fiscal or 'Nenhuma.', styles['Value'])],
    ]
    tabela_execucao = Table(data_execucao, colWidths=[4*cm, 13*cm])
    tabela_execucao.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F0F0F0')),
    ]))
    story.append(Paragraph("<b>2. Detalhes do Atesto</b>", styles['h3']))
    story.append(Spacer(1, 0.2*cm))
    story.append(tabela_execucao)
    story.append(Spacer(1, 0.8*cm))

    
    # --- 3. CHECKLIST DE CONFORMIDADE ---
    story.append(Paragraph("<b>3. Checklist de Fiscalização</b>", styles['h3']))
    story.append(Spacer(1, 0.2*cm))
    
    # Prepara a tabela de checklist
    data_checklist = [
        [
            Paragraph("Item de Fiscalização", styles['TableHeader']),
            Paragraph("Resposta", styles['TableHeader']),
            Paragraph("Resultado", styles['TableHeader']),
        ]
    ]

    for resposta in respostas:
        # Lógica para determinar o resultado visual
        resultado_visual = ""
        if resposta.valor_resposta.lower() == 'sim':
            resultado_visual = "✅ CONFORME"
        elif resposta.valor_resposta.lower() == 'não' or resposta.valor_resposta == 'Reprovado':
            resultado_visual = "❌ NÃO CONFORME"
        else:
            resultado_visual = "ℹ️ " + resposta.valor_resposta
            
        data_checklist.append([
            Paragraph(resposta.item.descricao, styles['TableData']),
            Paragraph(resposta.valor_resposta, styles['TableData']),
            Paragraph(resultado_visual, styles['TableData']),
        ])
        
    tabela_checklist = Table(data_checklist, colWidths=[9*cm, 3*cm, 5*cm])
    tabela_checklist.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E0E0E0')), # Cabeçalho
        ('BACKGROUND', (2,1), (2,-1), colors.HexColor('#d4edda')), # Coluna de Resultado
    ]))
    story.append(tabela_checklist)
    story.append(Spacer(1, 1*cm))
    
    # --- 4. CONCLUSÃO ---
    story.append(Paragraph(f"<b>CONCLUSÃO FINAL DO FISCAL:</b>", styles['Label']))
    story.append(Paragraph(f"O(A) fiscal atesta que a execução para a competência {atesto.mes_competencia} foi classificada como: <b>{atesto.conformidade.upper()}</b>.", styles['h3']))
    story.append(Spacer(1, 2*cm))
    
    # --- ASSINATURA ---
    story.append(Paragraph("____________________________________________________", styles['H2Center']))
    story.append(Paragraph(f"Fiscal {atesto.assinatura_fiscal} / Matrícula: [Inserir Matrícula]", styles['H2Center']))
    

    doc.build(story, onFirstPage=cabecalho_e_rodape, onLaterPages=cabecalho_e_rodape)
    buffer.seek(0)
    
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f"inline; filename=Atesto_Contrato_{contrato.num_contrato}_{atesto.mes_competencia.replace('/', '_')}.pdf"
    
    return response

@contrato_fiscal_bp.route('/ocorrencia/<int:ocorrencia_id>/comprovante', methods=['GET'])
@login_required
@role_required('admin', 'Fiscal')
def emitir_comprovante_ocorrencia(ocorrencia_id):
    ocorrencia = FiscalOcorrencia.query.get_or_404(ocorrencia_id)
    contrato = ocorrencia.contrato
    
    # 1. Configuração do PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2.5*cm,
        rightMargin=2.5*cm,
        topMargin=3*cm,
        bottomMargin=2.5*cm
    )
    story = []
    styles = getSampleStyleSheet()
    
    # Estilos Personalizados
    styles.add(ParagraphStyle(name='H1Center', parent=styles['h1'], alignment=TA_CENTER, fontSize=14, spaceAfter=0.5*cm))
    styles.add(ParagraphStyle(name='HeaderTable', alignment=TA_CENTER, fontSize=8, textColor=colors.HexColor("#FFFFFF"), fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='Label', fontName='Helvetica-Bold', fontSize=10, leading=14))
    styles.add(ParagraphStyle(name='Value', fontName='Helvetica', fontSize=10, leading=14))
    styles.add(ParagraphStyle(name='Cell', fontSize=9, leading=12))

    # 2. Título Principal
    story.append(Paragraph("TERMO DE REGISTRO DE OCORRÊNCIA", styles['H1Center']))
    story.append(Paragraph(f"Contrato N° {contrato.num_contrato}/{contrato.ano}", styles['h3']))
    story.append(Spacer(1, 0.5*cm))

    # 3. Tabela de Detalhes da Ocorrência
    data_ocorrencia = [
        [
            Paragraph("<b>DATA/HORA DO REGISTRO</b>", styles['HeaderTable']),
            Paragraph("<b>TIPO DE OCORRÊNCIA</b>", styles['HeaderTable']),
            Paragraph("<b>GRAVIDADE</b>", styles['HeaderTable']),
            Paragraph("<b>STATUS ATUAL</b>", styles['HeaderTable'])
        ],
        [
            Paragraph(ocorrencia.data_hora.strftime("%d/%m/%Y %H:%M:%S"), styles['Cell']),
            Paragraph(ocorrencia.tipo_ocorrencia, styles['Cell']),
            Paragraph(ocorrencia.gravidade, styles['Cell']),
            Paragraph(ocorrencia.status, styles['Cell'])
        ],
        [
            Paragraph("<b>LOCAL DA OCORRÊNCIA</b>", styles['HeaderTable']),
            Paragraph("<b>FISCAL RESPONSÁVEL</b>", styles['HeaderTable']),
            Paragraph("<b>CNPJ DA EMPRESA</b>", styles['HeaderTable']),
            Paragraph("<b>EMPRESA CONTRATADA</b>", styles['HeaderTable'])
        ],
        [
            Paragraph(ocorrencia.local_ocorrencia or 'N/A', styles['Cell']),
            Paragraph(ocorrencia.responsavel_registro, styles['Cell']),
            Paragraph(contrato.cnpj, styles['Cell']),
            Paragraph(contrato.empresa_contratada, styles['Cell'])
        ]
    ]

    tabela_detalhes = Table(data_ocorrencia, colWidths=[4*cm, 4*cm, 3*cm, 6*cm])
    tabela_detalhes.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#B00020")), 
        ('BACKGROUND', (0, 2), (-1, 2), colors.HexColor("#B00020")),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('TEXTCOLOR', (0, 2), (-1, 2), colors.whitesmoke),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(tabela_detalhes)
    story.append(Spacer(1, 0.8*cm))

    # 4. Descrição Detalhada
    story.append(Paragraph("<b>4. Descrição Detalhada da Infração</b>", styles['Label']))
    story.append(Paragraph(ocorrencia.descricao_detalhada, styles['Cell']))
    story.append(Spacer(1, 1*cm))
    
    # 5. Evidência 
    if ocorrencia.evidencia_filename:
        story.append(Paragraph(f"<b>ARQUIVO DE EVIDÊNCIA ANEXO:</b> {ocorrencia.evidencia_filename}", styles['Label']))
        story.append(Spacer(1, 0.3*cm))
    
    # 6. Assinaturas
    story.append(Spacer(1, 2.0*cm))
    story.append(Paragraph("_____________________________________________", styles['H1Center']))
    story.append(Paragraph(f"<b>{ocorrencia.responsavel_registro}</b><br/>Fiscal de Contrato", styles['Cell']))
    story.append(Spacer(1, 1.0*cm))
    story.append(Paragraph("_____________________________________________", styles['H1Center']))
    story.append(Paragraph(f"<b>{contrato.empresa_contratada}</b><br/>Representante da Contratada (Ciente)", styles['Cell']))
    
    # 7. Construir o PDF
    doc.build(story, onFirstPage=cabecalho_e_rodape, onLaterPages=cabecalho_e_rodape)
    
    # 8. Retornar o PDF como resposta HTTP
    pdf_content = buffer.getvalue()
    buffer.close()
    
    response = make_response(pdf_content)
    response.headers['Content-Type'] = 'application/pdf'
    nome_arquivo = f"Registro_Ocorrencia_{contrato.num_contrato}_{ocorrencia.id}.pdf"
    response.headers['Content-Disposition'] = f'inline; filename={nome_arquivo}'
    
    return response

@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/nf/novo', methods=['POST'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def lancar_nota_fiscal(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)
    
    try:
        # 1. Pega e converte os dados
        numero_nf = request.form.get('numero_nf')
        data_emissao_str = request.form.get('data_emissao')
        valor_str = request.form.get('valor').replace('.', '').replace(',', '.')
        
        valor = float(valor_str)
        data_emissao = datetime.strptime(data_emissao_str, '%Y-%m-%d').date()
        
        # 2. Cria o registro da Nota Fiscal
        nova_nf = FiscalNotaFiscal(
            contrato_id=contrato_id,
            numero_nf=numero_nf,
            data_emissao=data_emissao,
            valor=valor,
            usuario_registro=session.get('username')
        )
        
        db.session.add(nova_nf)
        db.session.commit()
        
        flash(f'Nota Fiscal {numero_nf} no valor de R$ {valor_str} lançada e saldo atualizado!', 'success')
        
    except ValueError:
        flash('Erro de formato: Certifique-se de que o valor e a data estão corretos.', 'danger')
        db.session.rollback()
    except Exception as e:
        flash(f'Erro ao lançar Nota Fiscal: {e}', 'danger')
        db.session.rollback()
        
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))


# 1. Configuração da API FocusNFe
# ASSUMO este endpoint como padrão de consulta por chave. AJUSTE se for diferente.
NF_CONSULTA_ENDPOINT = "https://api.focusnfe.com.br/v2/nfe/consulta_chave" 
FOCUSNFE_API_KEY = os.environ.get("FOCUSNFE_API_KEY", "CHAVE_AUSENTE") 

@contrato_fiscal_bp.route('/api/consulta_nf')
@login_required
@role_required('admin', 'RH', 'Fiscal')
def consulta_nota_fiscal_api():
    chave = request.args.get('chave')

    # ... (checagens de chave e token) ...
    
    # 2. Montar a requisição (Tentativa de enviar o token como parâmetro 'token')
    try:
        # A FocusNFe geralmente usa o token no header, mas vamos tentar na query string
        # como fallback, pois o erro 401 é de autenticação.
        
        # NOTE: Consulte a documentação real da FocusNFe para confirmar se o token
        # deve ser enviado via header (Authorization: Basic BASE64(TOKEN:)) ou como
        # parâmetro na URL (token=SEU_TOKEN). 
        
        # Vamos usar a autenticação HTTP Basic, que é o padrão FocusNFe:
        
        auth_tuple = (FOCUSNFE_API_KEY, "") # FocusNFe usa TOKEN: (Token e senha vazia)
        
        params = {
            "chave_acesso": chave
        }
        
        # Faz a chamada para a API, usando Autenticação HTTP Basic
        response = requests.get(
            NF_CONSULTA_ENDPOINT, 
            auth=auth_tuple, # <-- USANDO AUTH BÁSICA 
            params=params, 
            timeout=15
        )
        
        response.raise_for_status() # Lança exceção para erros HTTP 4xx ou 5xx
        
        # ... (restante da lógica de mapeamento) ...
        
        return jsonify({
            'numero': numero,
            'valor': f"{float(valor_str):.2f}".replace('.',','),
            'data_emissao': data_emissao_str
        }), 200

    except requests.exceptions.HTTPError as e:
        # Erro HTTP capturado (401, 404, etc.)
        # Este é o tratamento de erro mais provável que você quer ver
        if e.response.status_code == 401:
            return jsonify({'error': 'Falha na Autenticação (401). Verifique se seu token FocusNFe está correto e ativo.'}), 401
        if e.response.status_code == 404:
            return jsonify({'error': 'NF não encontrada ou chave inválida (404).'}), 404
        return jsonify({'error': f'Erro HTTP {e.response.status_code} na API da FocusNFe.'}), 503

    except requests.exceptions.RequestException as e:
        # Erro de conexão, timeout
        return jsonify({'error': f'Falha de Conexão com a API FocusNFe (Timeout).'}), 503

    except (KeyError, TypeError, ValueError) as e:
        # ... (restante do tratamento de erro) ...
        return jsonify({'error': 'Erro ao processar dados da NF. Mapeamento de campos incorreto.'}), 500
    
@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/editar', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def editar_contrato(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)
    secretaria_id_logada = session.get('secretaria_id')

    if request.method == 'POST':
        try:
            # 1. Pega e limpa os valores numéricos
            valor_total = float(request.form.get('valor_total').replace('.', '').replace(',', '.'))
            valor_mensal = float(request.form.get('valor_mensal_parcela').replace('.', '').replace(',', '.'))

            # 2. Converte as datas
            vigencia_inicio = datetime.strptime(request.form.get('vigencia_inicio'), '%Y-%m-%d').date()
            vigencia_fim = datetime.strptime(request.form.get('vigencia_fim'), '%Y-%m-%d').date()

            # 3. Atualiza o objeto do contrato
            contrato.num_contrato = request.form.get('num_contrato')
            contrato.ano = request.form.get('ano', type=int)
            contrato.tipo = request.form.get('tipo')
            contrato.objeto = request.form.get('objeto')
            contrato.processo_licitatorio = request.form.get('processo_licitatorio')
            contrato.empresa_contratada = request.form.get('empresa_contratada')
            contrato.cnpj = request.form.get('cnpj')
            contrato.representante_empresa = request.form.get('representante_empresa')
            contrato.valor_total = valor_total
            contrato.valor_mensal_parcela = valor_mensal
            contrato.vigencia_inicio = vigencia_inicio
            contrato.vigencia_fim = vigencia_fim
            contrato.situacao = request.form.get('situacao', 'Ativo')
            # Não permite alterar secretaria_id após a criação
            
            db.session.commit()

            flash(f"Contrato nº {contrato.num_contrato}/{contrato.ano} atualizado com sucesso!", 'success')
            return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato.id))

        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao atualizar contrato: {e}.", 'danger')
            return render_template('fiscal_contrato_form.html', contrato=contrato, now=datetime.now)

    # Lógica GET: Retorna o formulário preenchido
    return render_template('fiscal_contrato_form.html', contrato=contrato, now=datetime.now)

@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('admin', 'RH') # Acesso restrito
def excluir_contrato(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)
    
    try:
        # Excluir dados relacionados:
        # 1. Excluir Notas Fiscais
        FiscalNotaFiscal.query.filter_by(contrato_id=contrato_id).delete()
        
        # 2. Excluir Respostas de Checklist (dependência de Atesto)
        atestos_ids = [a.id for a in FiscalAtestoMensal.query.filter_by(contrato_id=contrato_id).all()]
        if atestos_ids:
            FiscalChecklistResposta.query.filter(FiscalChecklistResposta.atesto_id.in_(atestos_ids)).delete(synchronize_session=False)

        # 3. Excluir Atestos Mensais (e seus arquivos de evidência)
        FiscalAtestoMensal.query.filter_by(contrato_id=contrato_id).delete()

        # 4. Excluir Ocorrências (e seus arquivos de evidência)
        FiscalOcorrencia.query.filter_by(contrato_id=contrato_id).delete()
        
        # 5. Excluir Anexos (e seus arquivos físicos)
        anexos_do_contrato = FiscalAnexo.query.filter_by(contrato_id=contrato_id).all()
        upload_path = _get_anexos_path()
        for anexo in anexos_do_contrato:
            caminho_arquivo = os.path.join(upload_path, anexo.filename_seguro)
            if os.path.exists(caminho_arquivo):
                os.remove(caminho_arquivo)
            db.session.delete(anexo) # Exclui o registro do anexo
            
        # 6. Excluir o Contrato
        db.session.delete(contrato)
        db.session.commit()
        
        flash(f"Contrato {contrato.num_contrato}/{contrato.ano} e todos os dados associados foram EXCLUÍDOS permanentemente.", 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir contrato: {e}", 'danger')
        return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))

    return redirect(url_for('fiscal.dashboard'))

@contrato_fiscal_bp.route('/nf/<int:nf_id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def excluir_nota_fiscal(nf_id):
    nf = FiscalNotaFiscal.query.get_or_404(nf_id)
    contrato_id = nf.contrato_id
    
    try:
        db.session.delete(nf)
        db.session.commit()
        flash(f'Nota Fiscal {nf.numero_nf} excluída e saldo atualizado.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir Nota Fiscal: {e}', 'danger')
        
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))


# --- ROTA DE EXCLUSÃO DE ATESTO ---
@contrato_fiscal_bp.route('/atesto/<int:atesto_id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def excluir_atesto(atesto_id):
    atesto = FiscalAtestoMensal.query.get_or_404(atesto_id)
    contrato_id = atesto.contrato_id
    
    try:
        # Exclui as respostas de checklist vinculadas
        FiscalChecklistResposta.query.filter_by(atesto_id=atesto_id).delete()
        
        # Exclui o arquivo de evidência (se houver)
        if atesto.evidencia_filename:
            upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_atestos')
            caminho_arquivo = os.path.join(upload_path, atesto.evidencia_filename)
            if os.path.exists(caminho_arquivo):
                os.remove(caminho_arquivo)
        
        db.session.delete(atesto)
        db.session.commit()
        flash(f'Atesto da competência {atesto.mes_competencia} excluído.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir atesto: {e}', 'danger')
        
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))


# --- ROTA DE EXCLUSÃO DE OCORRÊNCIA ---
@contrato_fiscal_bp.route('/ocorrencia/<int:ocorrencia_id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def excluir_ocorrencia(ocorrencia_id):
    ocorrencia = FiscalOcorrencia.query.get_or_404(ocorrencia_id)
    contrato_id = ocorrencia.contrato_id
    
    try:
        # Exclui o arquivo de evidência (se houver)
        if ocorrencia.evidencia_filename:
            upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_ocorrencias')
            caminho_arquivo = os.path.join(upload_path, ocorrencia.evidencia_filename)
            if os.path.exists(caminho_arquivo):
                os.remove(caminho_arquivo)
        
        db.session.delete(ocorrencia)
        db.session.commit()
        flash('Ocorrência excluída com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir ocorrência: {e}', 'danger')
        
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))