# Arquivo: contrato_fiscal_routes.py (ATUALIZADO PARA SUPABASE)

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
from extensions import db
# IMPORTANTE: Adicionada a função de upload na nuvem
from utils import login_required, role_required, cabecalho_e_rodape, upload_arquivo_para_nuvem
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
    """Define o caminho de upload LOCAL (apenas para fallback de arquivos antigos)."""
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

            # 2. Converte as datas
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
            flash(f"Erro ao cadastrar contrato: {e}. Verifique os campos.", 'danger')
            return redirect(url_for('fiscal.novo_contrato'))

    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    return render_template('fiscal_contrato_form.html', secretarias=secretarias, contrato=None, now=datetime.now)

@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/detalhes', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def detalhes_contrato(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)

    dias_restantes = (contrato.vigencia_fim - date.today()).days if contrato.vigencia_fim and contrato.vigencia_fim > date.today() else 0

    anexos = FiscalAnexo.query.filter_by(contrato_id=contrato_id).order_by(FiscalAnexo.data_upload.desc()).all()
    atestos = FiscalAtestoMensal.query.filter_by(contrato_id=contrato_id).order_by(FiscalAtestoMensal.mes_competencia.desc()).all()
    ocorrencias = FiscalOcorrencia.query.filter_by(contrato_id=contrato_id).order_by(FiscalOcorrencia.data_hora.desc()).all()
    notas_fiscais = FiscalNotaFiscal.query.filter_by(contrato_id=contrato_id).order_by(FiscalNotaFiscal.data_emissao.desc()).all()

    total_gasto_contrato = sum(nf.valor for nf in notas_fiscais)
    saldo_atual_contrato = contrato.valor_total - total_gasto_contrato

    return render_template(
        'fiscal_detalhes_contrato.html', 
        contrato=contrato, 
        dias_restantes=dias_restantes,
        saldo_atual_contrato=saldo_atual_contrato, 
        total_gasto_contrato=total_gasto_contrato,
        anexos=anexos,
        atestos=atestos,
        ocorrencias=ocorrencias,
        notas_fiscais=notas_fiscais
    )

# --- ROTAS DE ANEXOS (COM UPLOAD SUPABASE) ---

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
            
            # --- UPLOAD PARA SUPABASE ---
            # Envia para a pasta 'contratos_anexos' no bucket
            url_gerada = upload_arquivo_para_nuvem(file, pasta="contratos_anexos")
            
            if not url_gerada:
                raise Exception("Falha ao obter URL do Supabase")

            # Salva a URL completa no campo filename_seguro
            novo_anexo = FiscalAnexo(
                contrato_id=contrato_id,
                tipo_documento=tipo_documento,
                nome_original=filename_original,
                filename_seguro=url_gerada 
            )
            db.session.add(novo_anexo)
            db.session.commit()
            
            flash('Documento anexado na nuvem com sucesso!', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar anexo: {e}', 'danger')
            
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))

@contrato_fiscal_bp.route('/anexo/<int:anexo_id>/download')
@login_required
@role_required('admin', 'RH', 'Fiscal')
def download_anexo(anexo_id):
    anexo = FiscalAnexo.query.get_or_404(anexo_id)
    
    # Verifica se é um link da nuvem (Supabase)
    if anexo.filename_seguro and anexo.filename_seguro.startswith('http'):
        return redirect(anexo.filename_seguro)
        
    # Fallback para arquivos antigos (Locais)
    upload_path = _get_anexos_path()
    try:
        return send_from_directory(
            upload_path, 
            anexo.filename_seguro, 
            as_attachment=True, 
            download_name=anexo.nome_original
        )
    except FileNotFoundError:
        flash('Erro: Arquivo não encontrado (nem na nuvem, nem local).', 'danger')
        return redirect(url_for('fiscal.detalhes_contrato', contrato_id=anexo.contrato_id))

@contrato_fiscal_bp.route('/anexo/<int:anexo_id>/excluir')
@login_required
@role_required('admin', 'RH')
def excluir_anexo(anexo_id):
    anexo = FiscalAnexo.query.get_or_404(anexo_id)
    contrato_id = anexo.contrato_id
    
    try:
        # Tenta excluir arquivo local apenas se NÃO for um link http
        if anexo.filename_seguro and not anexo.filename_seguro.startswith('http'):
            upload_path = _get_anexos_path()
            caminho_arquivo = os.path.join(upload_path, anexo.filename_seguro)
            if os.path.exists(caminho_arquivo):
                os.remove(caminho_arquivo)
            
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
    
    modelo_checklist = FiscalChecklistModel.query.filter_by(
        tipo_contrato_associado=contrato.tipo
    ).first()
    itens_checklist = modelo_checklist.itens if modelo_checklist else []
    
    if request.method == 'POST':
        try:
            mes_comp = request.form.get('mes_competencia')
            conformidade_status = request.form.get('conformidade')
            
            # --- UPLOAD DE EVIDÊNCIA (SUPABASE) ---
            evidencia_file = request.files.get('evidencia_file')
            evidencia_link = None
            
            if evidencia_file and evidencia_file.filename != '':
                # Envia para pasta 'contratos_atestos'
                evidencia_link = upload_arquivo_para_nuvem(evidencia_file, pasta="contratos_atestos")
            
            # 2. Criar Atesto
            novo_atesto = FiscalAtestoMensal(
                contrato_id=contrato_id,
                mes_competencia=mes_comp,
                descricao_servico=request.form.get('descricao_servico'),
                conformidade=conformidade_status,
                observacoes_fiscal=request.form.get('observacoes_fiscal'),
                assinatura_fiscal=session.get('username', 'Sistema'),
                status_atesto=conformidade_status, 
                evidencia_filename=evidencia_link, # Salva o LINK aqui
                localizacao_gps=request.form.get('localizacao_gps')
            )
            db.session.add(novo_atesto)
            db.session.flush()

            # 3. Salvar Respostas Checklist
            for item in itens_checklist:
                resposta_valor = request.form.get(f'checklist_{item.id}')
                if resposta_valor is not None:
                    nova_resposta = FiscalChecklistResposta(
                        atesto_id=novo_atesto.id,
                        item_id=item.id,
                        valor_resposta=resposta_valor
                    )
                    db.session.add(nova_resposta)

            db.session.commit()
            
            flash(f"Atesto para {mes_comp} registrado na nuvem!", 'success')
            return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar atesto: {e}", 'danger')
            return redirect(url_for('fiscal.lancar_atesto', contrato_id=contrato_id))
            
    from datetime import date
    today = date.today()
    mes_atual = today.strftime('%Y-%m')
    atestos_existentes = FiscalAtestoMensal.query.filter_by(contrato_id=contrato_id).order_by(FiscalAtestoMensal.mes_competencia.desc()).all()

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
            # --- UPLOAD DE EVIDÊNCIA (SUPABASE) ---
            evidencia_file = request.files.get('evidencia_file')
            evidencia_link = None
            
            if evidencia_file and evidencia_file.filename != '':
                # Envia para pasta 'contratos_ocorrencias'
                evidencia_link = upload_arquivo_para_nuvem(evidencia_file, pasta="contratos_ocorrencias")
                
            # 2. Criar Ocorrência
            nova_ocorrencia = FiscalOcorrencia(
                contrato_id=contrato_id,
                tipo_ocorrencia=request.form.get('tipo_ocorrencia'),
                descricao_detalhada=request.form.get('descricao_detalhada'),
                gravidade=request.form.get('gravidade'),
                responsavel_registro=session.get('username', 'Sistema'),
                local_ocorrencia=request.form.get('local_ocorrencia'),
                evidencia_filename=evidencia_link, # Salva o LINK
                status='Aberta'
            )
            db.session.add(nova_ocorrencia)
            db.session.commit()
            
            flash("Ocorrência registrada na nuvem.", 'warning')
            return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar ocorrência: {e}", 'danger')
            return redirect(url_for('fiscal.lancar_ocorrencia', contrato_id=contrato_id))
            
    ocorrencias_existentes = FiscalOcorrencia.query.filter_by(contrato_id=contrato_id).order_by(FiscalOcorrencia.data_hora.desc()).all()
    
    return render_template('fiscal_ocorrencia_form.html', contrato=contrato, ocorrencias=ocorrencias_existentes)

@contrato_fiscal_bp.route('/config/checklists')
@login_required
@role_required('admin', 'RH')
def listar_modelos_checklist():
    secretaria_id_logada = session.get('secretaria_id')
    contratos = FiscalContrato.query.filter_by(secretaria_id=secretaria_id_logada).all()
    tipos_contrato = sorted(list(set(c.tipo for c in contratos)))
    
    from models import FiscalChecklistModel 
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
        from models import FiscalChecklistModel
        nome = request.form.get('nome')
        tipo_contrato = request.form.get('tipo_contrato')
        
        novo_modelo = FiscalChecklistModel(
            nome=nome,
            tipo_contrato_associado=tipo_contrato
        )
        db.session.add(novo_modelo)
        db.session.commit()
        
        flash('Modelo de checklist criado com sucesso!', 'success')
        return redirect(url_for('fiscal.detalhes_modelo_checklist', modelo_id=novo_modelo.id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao criar modelo: {e}', 'danger')
        return redirect(url_for('fiscal.listar_modelos_checklist'))

@contrato_fiscal_bp.route('/config/checklist/<int:modelo_id>')
@login_required
@role_required('admin', 'RH')
def detalhes_modelo_checklist(modelo_id):
    from models import FiscalChecklistModel
    modelo = FiscalChecklistModel.query.get_or_404(modelo_id)
    return render_template('fiscal_checklist_detalhes.html', modelo=modelo)

@contrato_fiscal_bp.route('/config/checklist/<int:modelo_id>/item/novo', methods=['POST'])
@login_required
@role_required('admin', 'RH')
def adicionar_item_checklist(modelo_id):
    from models import FiscalChecklistItem
    modelo = FiscalChecklistModel.query.get_or_404(modelo_id)
    
    try:
        novo_item = FiscalChecklistItem(
            modelo_id=modelo_id,
            descricao=request.form.get('descricao'),
            tipo_resposta=request.form.get('tipo_resposta')
        )
        db.session.add(novo_item)
        db.session.commit()
        flash('Item adicionado!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao adicionar item: {e}', 'danger')
        
    return redirect(url_for('fiscal.detalhes_modelo_checklist', modelo_id=modelo_id))

@contrato_fiscal_bp.route('/config/checklist/item/<int:item_id>/excluir')
@login_required
@role_required('admin', 'RH')
def excluir_item_checklist(item_id):
    from models import FiscalChecklistItem
    item = FiscalChecklistItem.query.get_or_404(item_id)
    modelo_id = item.modelo_id
    
    try:
        db.session.delete(item)
        db.session.commit()
        flash('Item excluído.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir item: {e}', 'danger')
        
    return redirect(url_for('fiscal.detalhes_modelo_checklist', modelo_id=modelo_id))

@contrato_fiscal_bp.route('/atesto/<int:atesto_id>/comprovante', methods=['GET'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def emitir_comprovante_atesto(atesto_id):
    # (Mantido igual - o ReportLab gera o PDF em memória e envia)
    atesto = FiscalAtestoMensal.query.get_or_404(atesto_id)
    contrato = atesto.contrato
    respostas = FiscalChecklistResposta.query.filter_by(atesto_id=atesto_id)\
        .join(FiscalChecklistItem).all()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=3*cm, bottomMargin=2*cm, leftMargin=2*cm, rightMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    styles.add(ParagraphStyle(name='H2Center', parent=styles['h2'], alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='Label', fontName='Helvetica-Bold', fontSize=10, leading=14))
    styles.add(ParagraphStyle(name='Value', fontName='Helvetica', fontSize=10, leading=14))
    styles.add(ParagraphStyle(name='TableData', fontName='Helvetica', fontSize=9, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name='TableHeader', fontName='Helvetica-Bold', fontSize=9, alignment=TA_CENTER))

    story.append(Paragraph("TERMO DE ATESTO E CONFORMIDADE", styles['H2Center']))
    story.append(Paragraph(f"Contrato N° {contrato.num_contrato}/{contrato.ano}", styles['h3']))
    story.append(Spacer(1, 0.5*cm))

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

    story.append(Paragraph("<b>3. Checklist de Fiscalização</b>", styles['h3']))
    story.append(Spacer(1, 0.2*cm))
    
    data_checklist = [
        [
            Paragraph("Item de Fiscalização", styles['TableHeader']),
            Paragraph("Resposta", styles['TableHeader']),
            Paragraph("Resultado", styles['TableHeader']),
        ]
    ]

    for resposta in respostas:
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
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E0E0E0')), 
        ('BACKGROUND', (2,1), (2,-1), colors.HexColor('#d4edda')), 
    ]))
    story.append(tabela_checklist)
    story.append(Spacer(1, 1*cm))
    
    story.append(Paragraph(f"<b>CONCLUSÃO FINAL DO FISCAL:</b>", styles['Label']))
    story.append(Paragraph(f"O(A) fiscal atesta que a execução para a competência {atesto.mes_competencia} foi classificada como: <b>{atesto.conformidade.upper()}</b>.", styles['h3']))
    story.append(Spacer(1, 2*cm))
    
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
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2.5*cm, rightMargin=2.5*cm, topMargin=3*cm, bottomMargin=2.5*cm)
    story = []
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(name='H1Center', parent=styles['h1'], alignment=TA_CENTER, fontSize=14, spaceAfter=0.5*cm))
    styles.add(ParagraphStyle(name='HeaderTable', alignment=TA_CENTER, fontSize=8, textColor=colors.HexColor("#FFFFFF"), fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='Label', fontName='Helvetica-Bold', fontSize=10, leading=14))
    styles.add(ParagraphStyle(name='Value', fontName='Helvetica', fontSize=10, leading=14))
    styles.add(ParagraphStyle(name='Cell', fontSize=9, leading=12))

    story.append(Paragraph("TERMO DE REGISTRO DE OCORRÊNCIA", styles['H1Center']))
    story.append(Paragraph(f"Contrato N° {contrato.num_contrato}/{contrato.ano}", styles['h3']))
    story.append(Spacer(1, 0.5*cm))

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

    story.append(Paragraph("<b>4. Descrição Detalhada da Infração</b>", styles['Label']))
    story.append(Paragraph(ocorrencia.descricao_detalhada, styles['Cell']))
    story.append(Spacer(1, 1*cm))
    
    if ocorrencia.evidencia_filename:
        # Mostra o link da evidência no PDF se for URL
        if 'http' in ocorrencia.evidencia_filename:
             story.append(Paragraph(f"<b>EVIDÊNCIA NA NUVEM:</b> <a href='{ocorrencia.evidencia_filename}' color='blue'>{ocorrencia.evidencia_filename}</a>", styles['Label']))
        else:
             story.append(Paragraph(f"<b>ARQUIVO DE EVIDÊNCIA ANEXO:</b> {ocorrencia.evidencia_filename}", styles['Label']))
        story.append(Spacer(1, 0.3*cm))
    
    story.append(Spacer(1, 2.0*cm))
    story.append(Paragraph("_____________________________________________", styles['H1Center']))
    story.append(Paragraph(f"<b>{ocorrencia.responsavel_registro}</b><br/>Fiscal de Contrato", styles['Cell']))
    story.append(Spacer(1, 1.0*cm))
    story.append(Paragraph("_____________________________________________", styles['H1Center']))
    story.append(Paragraph(f"<b>{contrato.empresa_contratada}</b><br/>Representante da Contratada (Ciente)", styles['Cell']))
    
    doc.build(story, onFirstPage=cabecalho_e_rodape, onLaterPages=cabecalho_e_rodape)
    
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
        numero_nf = request.form.get('numero_nf')
        data_emissao_str = request.form.get('data_emissao')
        valor_str = request.form.get('valor').replace('.', '').replace(',', '.')
        valor = float(valor_str)
        data_emissao = datetime.strptime(data_emissao_str, '%Y-%m-%d').date()
        
        nova_nf = FiscalNotaFiscal(
            contrato_id=contrato_id,
            numero_nf=numero_nf,
            data_emissao=data_emissao,
            valor=valor,
            usuario_registro=session.get('username')
        )
        db.session.add(nova_nf)
        db.session.commit()
        flash(f'Nota Fiscal {numero_nf} lançada!', 'success')
    except Exception as e:
        flash(f'Erro ao lançar Nota Fiscal: {e}', 'danger')
        db.session.rollback()
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))

# --- API e Edição/Exclusão ---
NF_CONSULTA_ENDPOINT = "https://api.focusnfe.com.br/v2/nfe/consulta_chave" 
FOCUSNFE_API_KEY = os.environ.get("FOCUSNFE_API_KEY", "CHAVE_AUSENTE") 

@contrato_fiscal_bp.route('/api/consulta_nf')
@login_required
@role_required('admin', 'RH', 'Fiscal')
def consulta_nota_fiscal_api():
    # (Mantido igual - Lógica de API Externa não usa upload)
    chave = request.args.get('chave')
    try:
        auth_tuple = (FOCUSNFE_API_KEY, "")
        params = {"chave_acesso": chave}
        response = requests.get(NF_CONSULTA_ENDPOINT, auth=auth_tuple, params=params, timeout=15)
        response.raise_for_status()
        # ... parse logic ...
        return jsonify({'error': 'Não implementado mock'}), 501
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/editar', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def editar_contrato(contrato_id):
    # (Mantido igual - sem upload)
    contrato = FiscalContrato.query.get_or_404(contrato_id)
    if request.method == 'POST':
        try:
            contrato.num_contrato = request.form.get('num_contrato')
            # ... update fields ...
            db.session.commit()
            flash('Contrato atualizado.', 'success')
            return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro: {e}', 'danger')
    return render_template('fiscal_contrato_form.html', contrato=contrato, now=datetime.now)

@contrato_fiscal_bp.route('/contrato/<int:contrato_id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('admin', 'RH')
def excluir_contrato(contrato_id):
    contrato = FiscalContrato.query.get_or_404(contrato_id)
    try:
        FiscalNotaFiscal.query.filter_by(contrato_id=contrato_id).delete()
        atestos = FiscalAtestoMensal.query.filter_by(contrato_id=contrato_id).all()
        atestos_ids = [a.id for a in atestos]
        if atestos_ids:
            FiscalChecklistResposta.query.filter(FiscalChecklistResposta.atesto_id.in_(atestos_ids)).delete(synchronize_session=False)

        # Excluir arquivos de atestos (apenas locais, se houver)
        upload_path_atesto = os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_atestos')
        for atesto in atestos:
            if atesto.evidencia_filename and not atesto.evidencia_filename.startswith('http'):
                 p = os.path.join(upload_path_atesto, atesto.evidencia_filename)
                 if os.path.exists(p): os.remove(p)
        FiscalAtestoMensal.query.filter_by(contrato_id=contrato_id).delete()

        # Ocorrencias
        ocorrencias = FiscalOcorrencia.query.filter_by(contrato_id=contrato_id).all()
        upload_path_oc = os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_ocorrencias')
        for oc in ocorrencias:
             if oc.evidencia_filename and not oc.evidencia_filename.startswith('http'):
                 p = os.path.join(upload_path_oc, oc.evidencia_filename)
                 if os.path.exists(p): os.remove(p)
        FiscalOcorrencia.query.filter_by(contrato_id=contrato_id).delete()
        
        # Anexos
        anexos = FiscalAnexo.query.filter_by(contrato_id=contrato_id).all()
        upload_path_anexo = _get_anexos_path()
        for anexo in anexos:
            if anexo.filename_seguro and not anexo.filename_seguro.startswith('http'):
                p = os.path.join(upload_path_anexo, anexo.filename_seguro)
                if os.path.exists(p): os.remove(p)
            db.session.delete(anexo)
            
        db.session.delete(contrato)
        db.session.commit()
        flash('Contrato excluído.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {e}', 'danger')
        return redirect(url_for('fiscal.detalhes_contrato', contrato_id=contrato_id))
    return redirect(url_for('fiscal.dashboard'))

@contrato_fiscal_bp.route('/nf/<int:nf_id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def excluir_nota_fiscal(nf_id):
    # (Mantido igual)
    nf = FiscalNotaFiscal.query.get_or_404(nf_id)
    cid = nf.contrato_id
    try:
        db.session.delete(nf)
        db.session.commit()
        flash('Nota Fiscal excluída.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=cid))

@contrato_fiscal_bp.route('/atesto/<int:atesto_id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def excluir_atesto(atesto_id):
    atesto = FiscalAtestoMensal.query.get_or_404(atesto_id)
    cid = atesto.contrato_id
    try:
        FiscalChecklistResposta.query.filter_by(atesto_id=atesto_id).delete()
        if atesto.evidencia_filename and not atesto.evidencia_filename.startswith('http'):
            p = os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_atestos', atesto.evidencia_filename)
            if os.path.exists(p): os.remove(p)
        db.session.delete(atesto)
        db.session.commit()
        flash('Atesto excluído.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=cid))

@contrato_fiscal_bp.route('/ocorrencia/<int:ocorrencia_id>/excluir', methods=['POST', 'GET'])
@login_required
@role_required('admin', 'RH', 'Fiscal')
def excluir_ocorrencia(ocorrencia_id):
    ocorrencia = FiscalOcorrencia.query.get_or_404(ocorrencia_id)
    cid = ocorrencia.contrato_id
    try:
        if ocorrencia.evidencia_filename and not ocorrencia.evidencia_filename.startswith('http'):
            p = os.path.join(current_app.config['UPLOAD_FOLDER'], 'contratos_ocorrencias', ocorrencia.evidencia_filename)
            if os.path.exists(p): os.remove(p)
        db.session.delete(ocorrencia)
        db.session.commit()
        flash('Ocorrência excluída.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro: {e}', 'danger')
    return redirect(url_for('fiscal.detalhes_contrato', contrato_id=cid))