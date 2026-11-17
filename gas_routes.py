# gas_routes.py
# -*- coding: utf-8 -*-
import os
import random
from io import BytesIO
from datetime import datetime, date, timedelta
from calendar import month_name

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, make_response, current_app, jsonify
)
from sqlalchemy import func, or_, and_, text

from extensions import db
from models import Escola, EntregaGasEscola, ConfiguracaoGasEscola
from utils import login_required, role_required, registrar_log, obter_data_hora_br_str

# ReportLab imports para comprovante e relatórios governamentais
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, HRFlowable, KeepTogether
)
from reportlab.pdfgen import canvas as canvas_lib

gas_bp = Blueprint('gas', __name__, url_prefix='/gas')

_migracao_gas_executada = False


def executar_migracao_gas_segura():
    """Garante que as tabelas de entrega e configuração de gás existam no banco."""
    global _migracao_gas_executada
    if _migracao_gas_executada:
        return
    try:
        db.create_all()
    except Exception as e:
        print(f"Aviso db.create_all() gas: {e}")

    try:
        tabelas_sql = [
            """
            CREATE TABLE IF NOT EXISTS configuracao_gas_escola (
                id SERIAL PRIMARY KEY,
                escola_id INTEGER NOT NULL UNIQUE,
                dias_estimados_por_botijao INTEGER DEFAULT 20,
                tipo_botijao_padrao VARCHAR(20) DEFAULT 'P13',
                qtd_fogoes INTEGER DEFAULT 1,
                media_refeicoes_dia INTEGER DEFAULT 0,
                observacoes TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS entrega_gas_escola (
                id SERIAL PRIMARY KEY,
                codigo_comprovante VARCHAR(30) NOT NULL UNIQUE,
                escola_id INTEGER NOT NULL,
                tipo_botijao VARCHAR(20) DEFAULT 'P13',
                quantidade INTEGER DEFAULT 1,
                data_entrega DATE NOT NULL,
                hora_entrega VARCHAR(10),
                fornecedor VARCHAR(150),
                numero_lacre_nf VARCHAR(100),
                valor_unitario FLOAT DEFAULT 0.0,
                valor_total FLOAT DEFAULT 0.0,
                dias_duracao_estimada INTEGER DEFAULT 20,
                data_prevista_fim DATE,
                recebedor_nome VARCHAR(150) NOT NULL,
                recebedor_cargo VARCHAR(100),
                recebedor_cpf VARCHAR(20),
                entregador_nome VARCHAR(150),
                observacoes TEXT,
                status VARCHAR(30) DEFAULT 'Ativo',
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                criado_por VARCHAR(80)
            );
            """
        ]
        with db.engine.begin() as conn:
            for sql_cmd in tabelas_sql:
                try:
                    conn.execute(text(sql_cmd))
                except Exception:
                    pass
    except Exception as e:
        print(f"Aviso migração gas: {e}")

    _migracao_gas_executada = True


def _gerar_codigo_comprovante_gas():
    """Gera um código único de 5 dígitos para o comprovante, ex: GAS-59302."""
    ano = datetime.now().year
    for _ in range(50):
        aleatorio = random.randint(10000, 99999)
        codigo = f"GAS-{ano}-{aleatorio}"
        if not EntregaGasEscola.query.filter_by(codigo_comprovante=codigo).first():
            return codigo
    return f"GAS-{ano}-{random.randint(10000, 99999)}"


def calcular_previsao_gas_escola(escola_id, data_nova_entrega, quantidade_nova, tipo_botijao='P13'):
    """
    Calcula os dias de autonomia e a data prevista de término com base:
    1. No histórico real de entregas da escola (intervalo entre abastecimentos anteriores).
    2. Caso seja 1ª entrega ou sem histórico confiável, usa a configuração padrão da escola ou 20 dias/botijão.
    """
    config = ConfiguracaoGasEscola.query.filter_by(escola_id=escola_id).first()
    dias_base_padrao = config.dias_estimados_por_botijao if config and config.dias_estimados_por_botijao else 20

    # Fator de multiplicação por tipo de botijão
    fator_tipo = 1.0
    if tipo_botijao == 'P20':
        fator_tipo = 1.5
    elif tipo_botijao == 'P45':
        fator_tipo = 3.4

    # Busca histórico anterior
    entregas_anteriores = (
        EntregaGasEscola.query
        .filter(
            EntregaGasEscola.escola_id == escola_id,
            EntregaGasEscola.status != 'Cancelado',
            EntregaGasEscola.data_entrega <= data_nova_entrega
        )
        .order_by(EntregaGasEscola.data_entrega.desc())
        .limit(4)
        .all()
    )

    dias_calculados_por_botijao = dias_base_padrao * fator_tipo

    if len(entregas_anteriores) >= 2:
        # Calcula taxa média real entre as entregas consecutivas
        intervalos_dias = []
        for i in range(len(entregas_anteriores) - 1):
            e_recente = entregas_anteriores[i]
            e_antiga = entregas_anteriores[i+1]
            diff = (e_recente.data_entrega - e_antiga.data_entrega).days
            if 3 <= diff <= 120 and e_antiga.quantidade > 0:
                media_dias_unidade = diff / float(e_antiga.quantidade)
                intervalos_dias.append(media_dias_unidade)

        if intervalos_dias:
            media_real = sum(intervalos_dias) / len(intervalos_dias)
            dias_calculados_por_botijao = round(media_real, 1)

    duracao_total_dias = int(round(dias_calculados_por_botijao * float(quantidade_nova)))
    if duracao_total_dias < 1:
        duracao_total_dias = 1

    data_fim = data_nova_entrega + timedelta(days=duracao_total_dias)
    return duracao_total_dias, data_fim


def obter_status_gas_escola(escola):
    """Retorna os dados da última entrega ativa e a situação atual do gás da escola."""
    ultima_entrega = (
        EntregaGasEscola.query
        .filter(
            EntregaGasEscola.escola_id == escola.id,
            EntregaGasEscola.status != 'Cancelado'
        )
        .order_by(EntregaGasEscola.data_entrega.desc(), EntregaGasEscola.id.desc())
        .first()
    )

    if not ultima_entrega:
        return {
            'tem_entrega': False,
            'ultima_entrega': None,
            'status': 'Sem Registro',
            'badge_cor': 'secondary',
            'dias_restantes': None,
            'percentual_restante': 0,
            'mensagem': 'Nenhuma entrega cadastrada'
        }

    hoje = date.today()
    data_prevista = ultima_entrega.data_prevista_fim or (ultima_entrega.data_entrega + timedelta(days=ultima_entrega.dias_duracao_estimada or 20))
    dias_restantes = (data_prevista - hoje).days

    total_dias = ultima_entrega.dias_duracao_estimada or 20
    dias_decorridos = (hoje - ultima_entrega.data_entrega).days

    if total_dias > 0:
        percentual = max(0, min(100, int((dias_restantes / total_dias) * 100)))
    else:
        percentual = 0

    if dias_restantes > 10:
        status = 'Normal'
        badge_cor = 'success'
        mensagem = f'Autonomia estável ({dias_restantes} dias restantes)'
    elif 4 <= dias_restantes <= 10:
        status = 'Atenção'
        badge_cor = 'warning'
        mensagem = f'Reposição recomendada ({dias_restantes} dias restantes)'
    elif 0 <= dias_restantes <= 3:
        status = 'Crítico'
        badge_cor = 'danger'
        mensagem = f'Gás quase no fim ({dias_restantes} dias restantes!)'
    else:
        status = 'Esgotado'
        badge_cor = 'dark'
        dias_vencido = abs(dias_restantes)
        mensagem = f'Previsão vencida há {dias_vencido} dia(s)'

    return {
        'tem_entrega': True,
        'ultima_entrega': ultima_entrega,
        'status': status,
        'badge_cor': badge_cor,
        'dias_restantes': dias_restantes,
        'dias_decorridos': max(0, dias_decorridos),
        'total_dias_estimados': total_dias,
        'percentual_restante': percentual,
        'data_prevista': data_prevista,
        'mensagem': mensagem
    }


# =============================================================================
# 1. DASHBOARD PRINCIPAL DO CONTROLE DE GÁS
# =============================================================================
@gas_bp.route('/')
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def dashboard():
    executar_migracao_gas_segura()
    hoje = date.today()
    mes_atual = hoje.month
    ano_atual = hoje.year

    # Escolas cadastradas
    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome.asc()).all()
    if not escolas:
        escolas = Escola.query.order_by(Escola.nome.asc()).all()

    # Processa o status do gás para cada escola
    lista_escolas_gas = []
    qtd_criticas = 0
    qtd_atencao = 0
    qtd_normais = 0
    qtd_sem_registro = 0

    for esc in escolas:
        info_gas = obter_status_gas_escola(esc)
        lista_escolas_gas.append({
            'escola': esc,
            'info': info_gas
        })
        if not info_gas['tem_entrega']:
            qtd_sem_registro += 1
        elif info_gas['status'] in ('Crítico', 'Esgotado'):
            qtd_criticas += 1
        elif info_gas['status'] == 'Atenção':
            qtd_atencao += 1
        else:
            qtd_normais += 1

    # Ordena: críticas e atenção primeiro, depois por dias restantes
    def chave_ordenacao(item):
        info = item['info']
        if not info['tem_entrega']:
            return 999
        return info['dias_restantes']

    lista_escolas_gas.sort(key=chave_ordenacao)

    # Métricas Globais do Mês
    entregas_mes = EntregaGasEscola.query.filter(
        db.extract('month', EntregaGasEscola.data_entrega) == mes_atual,
        db.extract('year', EntregaGasEscola.data_entrega) == ano_atual,
        EntregaGasEscola.status != 'Cancelado'
    ).all()

    total_botijoes_mes = sum(e.quantidade for e in entregas_mes)
    valor_total_mes = sum(e.valor_total or 0.0 for e in entregas_mes)

    # Entregas no Ano
    total_botijoes_ano = (
        db.session.query(func.sum(EntregaGasEscola.quantidade))
        .filter(
            db.extract('year', EntregaGasEscola.data_entrega) == ano_atual,
            EntregaGasEscola.status != 'Cancelado'
        ).scalar() or 0
    )

    # Últimas 15 entregas gerais
    ultimas_entregas = (
        EntregaGasEscola.query
        .filter(EntregaGasEscola.status != 'Cancelado')
        .order_by(EntregaGasEscola.data_entrega.desc(), EntregaGasEscola.id.desc())
        .limit(15)
        .all()
    )

    proximo_codigo = _gerar_codigo_comprovante_gas()

    return render_template(
        'gas/dashboard.html',
        escolas=escolas,
        lista_escolas_gas=lista_escolas_gas,
        total_botijoes_mes=total_botijoes_mes,
        valor_total_mes=valor_total_mes,
        total_botijoes_ano=total_botijoes_ano,
        qtd_criticas=qtd_criticas,
        qtd_atencao=qtd_atencao,
        qtd_normais=qtd_normais,
        qtd_sem_registro=qtd_sem_registro,
        ultimas_entregas=ultimas_entregas,
        proximo_codigo=proximo_codigo,
        hoje_iso=hoje.strftime('%Y-%m-%d'),
        ano_atual=ano_atual,
        mes_atual=mes_atual
    )


# =============================================================================
# 2. LANÇAMENTO DE NOVA ENTREGA DE GÁS
# =============================================================================
@gas_bp.route('/entrega/nova', methods=['POST'])
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def lancar_entrega_gas():
    executar_migracao_gas_segura()
    try:
        escola_id = request.form.get('escola_id', type=int)
        if not escola_id:
            flash("Selecione a escola beneficiária.", "warning")
            return redirect(url_for('gas.dashboard'))

        escola = Escola.query.get_or_404(escola_id)

        data_entrega_str = request.form.get('data_entrega')
        data_entrega = datetime.strptime(data_entrega_str, '%Y-%m-%d').date() if data_entrega_str else date.today()

        hora_entrega = request.form.get('hora_entrega') or datetime.now().strftime('%H:%M')
        tipo_botijao = request.form.get('tipo_botijao', 'P13').strip()
        quantidade = request.form.get('quantidade', 1, type=int)
        if quantidade <= 0:
            quantidade = 1

        fornecedor = (request.form.get('fornecedor') or '').strip()
        numero_lacre_nf = (request.form.get('numero_lacre_nf') or '').strip()

        valor_unitario_str = (request.form.get('valor_unitario') or '0').replace('.', '').replace(',', '.')
        valor_unitario = float(valor_unitario_str) if valor_unitario_str else 0.0
        valor_total = round(valor_unitario * quantidade, 2)

        recebedor_nome = (request.form.get('recebedor_nome') or '').strip()
        recebedor_cargo = (request.form.get('recebedor_cargo') or 'Merendeira / Responsável').strip()
        recebedor_cpf = (request.form.get('recebedor_cpf') or '').strip()
        entregador_nome = (request.form.get('entregador_nome') or '').strip()
        observacoes = (request.form.get('observacoes') or '').strip()

        if not recebedor_nome:
            flash("O nome do responsável pelo recebimento na escola é obrigatório.", "warning")
            return redirect(url_for('gas.dashboard'))

        # Cálculo da previsão inteligente de término
        duracao_estimada_manual = request.form.get('dias_duracao_estimada', type=int)
        if duracao_estimada_manual and duracao_estimada_manual > 0:
            duracao_dias = duracao_estimada_manual
            data_prevista_fim = data_entrega + timedelta(days=duracao_dias)
        else:
            duracao_dias, data_prevista_fim = calcular_previsao_gas_escola(
                escola_id=escola.id,
                data_nova_entrega=data_entrega,
                quantidade_nova=quantidade,
                tipo_botijao=tipo_botijao
            )

        codigo_comprovante = request.form.get('codigo_comprovante') or _gerar_codigo_comprovante_gas()

        nova_entrega = EntregaGasEscola(
            codigo_comprovante=codigo_comprovante,
            escola_id=escola.id,
            tipo_botijao=tipo_botijao,
            quantidade=quantidade,
            data_entrega=data_entrega,
            hora_entrega=hora_entrega,
            fornecedor=fornecedor,
            numero_lacre_nf=numero_lacre_nf,
            valor_unitario=valor_unitario,
            valor_total=valor_total,
            dias_duracao_estimada=duracao_dias,
            data_prevista_fim=data_prevista_fim,
            recebedor_nome=recebedor_nome,
            recebedor_cargo=recebedor_cargo,
            recebedor_cpf=recebedor_cpf,
            entregador_nome=entregador_nome,
            observacoes=observacoes,
            status='Ativo',
            criado_por=session.get('username', 'Sistema')
        )

        db.session.add(nova_entrega)
        db.session.commit()

        registrar_log(f'Cadastrou entrega de gás #{codigo_comprovante} para a escola "{escola.nome}" ({quantidade}x {tipo_botijao}).')
        flash(
            f"Entrega #{codigo_comprovante} registrada com sucesso para {escola.nome}! "
            f"Previsão de duração: {duracao_dias} dias (até {data_prevista_fim.strftime('%d/%m/%Y')}).",
            "success"
        )
        return redirect(url_for('gas.escola_detalhes', escola_id=escola.id))

    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao registrar entrega de gás: {e}", "danger")
        return redirect(url_for('gas.dashboard'))


# =============================================================================
# 3. DETALHES DA ESCOLA, HISTÓRICO & COMPARATIVO ENTRE MESES
# =============================================================================
@gas_bp.route('/escola/<int:escola_id>')
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def escola_detalhes(escola_id):
    executar_migracao_gas_segura()
    escola = Escola.query.get_or_404(escola_id)
    ano_atual = datetime.now().year

    config = ConfiguracaoGasEscola.query.filter_by(escola_id=escola.id).first()
    info_gas = obter_status_gas_escola(escola)

    # Todas as entregas da escola
    entregas = (
        EntregaGasEscola.query
        .filter(
            EntregaGasEscola.escola_id == escola.id,
            EntregaGasEscola.status != 'Cancelado'
        )
        .order_by(EntregaGasEscola.data_entrega.desc(), EntregaGasEscola.id.desc())
        .all()
    )

    # -------------------------------------------------------------
    # COMPARATIVO MÊS A MÊS DO ANO ATUAL
    # -------------------------------------------------------------
    meses_nomes = [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
    ]
    meses_siglas = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

    dados_mensais = []
    totais_por_mes = [0] * 12
    valores_por_mes = [0.0] * 12

    for m in range(1, 13):
        entregas_no_mes = [
            e for e in entregas
            if e.data_entrega.year == ano_atual and e.data_entrega.month == m
        ]
        qtd = sum(e.quantidade for e in entregas_no_mes)
        val = sum(e.valor_total or 0.0 for e in entregas_no_mes)

        totais_por_mes[m-1] = qtd
        valores_por_mes[m-1] = val

        # Comparativo com o mês anterior
        qtd_mes_anterior = totais_por_mes[m-2] if m > 1 else None
        variacao_pct = None
        if qtd_mes_anterior is not None and qtd_mes_anterior > 0:
            variacao_pct = round(((qtd - qtd_mes_anterior) / float(qtd_mes_anterior)) * 100, 1)

        dados_mensais.append({
            'mes_num': m,
            'mes_nome': meses_nomes[m-1],
            'mes_sigla': meses_siglas[m-1],
            'quantidade': qtd,
            'valor_total': val,
            'entregas_count': len(entregas_no_mes),
            'variacao_pct': variacao_pct
        })

    # Duração média histórica observada
    duracoes = [e.dias_duracao_estimada for e in entregas if e.dias_duracao_estimada and e.dias_duracao_estimada > 0]
    media_duracao_geral = round(sum(duracoes) / len(duracoes), 1) if duracoes else (config.dias_estimados_por_botijao if config else 20)

    total_botijoes_ano = sum(totais_por_mes)
    total_gasto_ano = sum(valores_por_mes)

    return render_template(
        'gas/escola_detalhes.html',
        escola=escola,
        config=config,
        info_gas=info_gas,
        entregas=entregas,
        dados_mensais=dados_mensais,
        totais_por_mes=totais_por_mes,
        meses_siglas=meses_siglas,
        media_duracao_geral=media_duracao_geral,
        total_botijoes_ano=total_botijoes_ano,
        total_gasto_ano=total_gasto_ano,
        ano_atual=ano_atual,
        proximo_codigo=_gerar_codigo_comprovante_gas(),
        hoje_iso=date.today().strftime('%Y-%m-%d')
    )


# =============================================================================
# 4. SALVAR CONFIGURAÇÕES DE ESTIMATIVA DA ESCOLA
# =============================================================================
@gas_bp.route('/escola/<int:escola_id>/config', methods=['POST'])
@login_required
@role_required('admin', 'Merenda Escolar')
def salvar_config_gas(escola_id):
    executar_migracao_gas_segura()
    escola = Escola.query.get_or_404(escola_id)

    dias_estimados = request.form.get('dias_estimados_por_botijao', 20, type=int)
    tipo_botijao_padrao = request.form.get('tipo_botijao_padrao', 'P13')
    qtd_fogoes = request.form.get('qtd_fogoes', 1, type=int)
    media_refeicoes_dia = request.form.get('media_refeicoes_dia', 0, type=int)
    observacoes = request.form.get('observacoes', '')

    config = ConfiguracaoGasEscola.query.filter_by(escola_id=escola.id).first()
    if not config:
        config = ConfiguracaoGasEscola(escola_id=escola.id)
        db.session.add(config)

    config.dias_estimados_por_botijao = dias_estimados if dias_estimados > 0 else 20
    config.tipo_botijao_padrao = tipo_botijao_padrao
    config.qtd_fogoes = qtd_fogoes if qtd_fogoes > 0 else 1
    config.media_refeicoes_dia = media_refeicoes_dia
    config.observacoes = observacoes

    db.session.commit()
    registrar_log(f'Atualizou parâmetros de gás da escola "{escola.nome}".')
    flash("Parâmetros de previsão do gás atualizados com sucesso!", "success")
    return redirect(url_for('gas.escola_detalhes', escola_id=escola.id))


# =============================================================================
# 5. CANCELAR / EXCLUIR ENTREGA DE GÁS
# =============================================================================
@gas_bp.route('/entrega/cancelar/<int:id>', methods=['POST'])
@login_required
@role_required('admin', 'Merenda Escolar')
def cancelar_entrega_gas(id):
    executar_migracao_gas_segura()
    entrega = EntregaGasEscola.query.get_or_404(id)
    escola_id = entrega.escola_id
    codigo = entrega.codigo_comprovante

    entrega.status = 'Cancelado'
    db.session.commit()
    registrar_log(f'Cancelou a entrega de gás #{codigo}.')
    flash(f"Entrega #{codigo} cancelada com sucesso.", "info")
    return redirect(url_for('gas.escola_detalhes', escola_id=escola_id))


# =============================================================================
# 6. COMPROVANTE OFICIAL DE ENTREGA DE GÁS EM PDF (PADRÃO GOVERNO)
# =============================================================================
@gas_bp.route('/comprovante/<int:id>/pdf')
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def gerar_comprovante_gas_pdf(id):
    executar_migracao_gas_segura()
    entrega = EntregaGasEscola.query.get_or_404(id)
    escola = entrega.escola

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=f"Comprovante_Entrega_Gas_{entrega.codigo_comprovante}"
    )

    styles = getSampleStyleSheet()

    cor_primaria = colors.HexColor("#072657")
    cor_secundaria = colors.HexColor("#1e3a8a")
    cor_cinza_claro = colors.HexColor("#f8fafc")

    style_titulo = ParagraphStyle(
        'TituloGov',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=cor_primaria,
        alignment=TA_CENTER
    )

    style_subtitulo = ParagraphStyle(
        'SubGov',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#334155"),
        alignment=TA_CENTER
    )

    style_codigo = ParagraphStyle(
        'CodGov',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=14,
        textColor=colors.HexColor("#b91c1c"),
        alignment=TA_RIGHT
    )

    style_label = ParagraphStyle(
        'LabelGov',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#475569")
    )

    style_val = ParagraphStyle(
        'ValGov',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#0f172a")
    )

    style_termo = ParagraphStyle(
        'TermoGov',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        alignment=TA_JUSTIFY,
        textColor=colors.HexColor("#1e293b")
    )

    def criar_bloco_comprovante(via_texto="1ª VIA - UNIDADE ESCOLAR"):
        elementos = []

        # Cabeçalho
        cabecalho_data = [
            [
                Paragraph("<b>ESTADO DO PIAUÍ</b><br/>PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ<br/>SECRETARIA MUNICIPAL DE EDUCAÇÃO<br/><b>PROGRAMA NACIONAL DE ALIMENTAÇÃO ESCOLAR (PNAE)</b>", style_subtitulo),
                Paragraph(f"<font color='#072657'><b>COMPROVANTE DE ENTREGA DE GÁS</b></font><br/><font size=7 color='#64748b'>{via_texto}</font><br/><br/><b>Nº {entrega.codigo_comprovante}</b>", style_codigo)
            ]
        ]
        t_cabecalho = Table(cabecalho_data, colWidths=[11.5 * cm, 6.5 * cm])
        t_cabecalho.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elementos.append(t_cabecalho)
        elementos.append(HRFlowable(width="100%", thickness=1.5, color=cor_primaria, spaceAfter=8))

        # 1. Dados da Unidade Escolar
        dados_escola = [
            [
                Paragraph("<b>Unidade Escolar:</b>", style_label),
                Paragraph(escola.nome, style_val),
                Paragraph("<b>Código INEP:</b>", style_label),
                Paragraph(escola.codigo_inep or 'N/I', style_val)
            ],
            [
                Paragraph("<b>Endereço / Local:</b>", style_label),
                Paragraph(escola.endereco or 'Valença do Piauí - PI', style_val),
                Paragraph("<b>Zona:</b>", style_label),
                Paragraph(escola.zona or 'Urbana', style_val)
            ],
            [
                Paragraph("<b>Diretor(a) Resp.:</b>", style_label),
                Paragraph(escola.diretor_responsavel or 'Não informado', style_val),
                Paragraph("<b>Telefone:</b>", style_label),
                Paragraph(escola.telefone or 'Não informado', style_val)
            ]
        ]
        t_escola = Table(dados_escola, colWidths=[3.2 * cm, 7.8 * cm, 2.5 * cm, 4.5 * cm])
        t_escola.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), cor_cinza_claro),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elementos.append(Paragraph("<b>1. DADOS DA UNIDADE BENEFICIÁRIA</b>", style_label))
        elementos.append(Spacer(1, 2))
        elementos.append(t_escola)
        elementos.append(Spacer(1, 6))

        # 2. Dados do Fornecimento de Gás
        data_formatada = entrega.data_entrega.strftime('%d/%m/%Y')
        hora_formatada = entrega.hora_entrega or '--:--'
        data_fim_formatada = entrega.data_prevista_fim.strftime('%d/%m/%Y') if entrega.data_prevista_fim else 'A calcular'

        dados_gas = [
            [
                Paragraph("<b>Data / Hora da Entrega:</b>", style_label),
                Paragraph(f"{data_formatada} às {hora_formatada}", style_val),
                Paragraph("<b>Tipo de Botijão:</b>", style_label),
                Paragraph(f"<b>{entrega.tipo_botijao}</b> ({'13 kg' if entrega.tipo_botijao == 'P13' else ('20 kg' if entrega.tipo_botijao == 'P20' else '45 kg')})", style_val)
            ],
            [
                Paragraph("<b>Quantidade Entregue:</b>", style_label),
                Paragraph(f"<b>{entrega.quantidade} unidade(s)</b>", style_val),
                Paragraph("<b>Nº Lacre / Nota Fiscal:</b>", style_label),
                Paragraph(entrega.numero_lacre_nf or 'Lacre Verificado / Intacto', style_val)
            ],
            [
                Paragraph("<b>Fornecedor / Distribuidora:</b>", style_label),
                Paragraph(entrega.fornecedor or 'Distribuidora Credenciada', style_val),
                Paragraph("<b>Previsão de Duração:</b>", style_label),
                Paragraph(f"{entrega.dias_duracao_estimada} dias (Até {data_fim_formatada})", style_val)
            ]
        ]
        if entrega.valor_total and entrega.valor_total > 0:
            dados_gas.append([
                Paragraph("<b>Valor Unitário:</b>", style_label),
                Paragraph(f"R$ {entrega.valor_unitario:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'), style_val),
                Paragraph("<b>Valor Total:</b>", style_label),
                Paragraph(f"<b>R$ {entrega.valor_total:,.2f}</b>".replace(',', 'X').replace('.', ',').replace('X', '.'), style_val)
            ])

        t_gas = Table(dados_gas, colWidths=[4.0 * cm, 5.5 * cm, 3.8 * cm, 4.7 * cm])
        t_gas.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elementos.append(Paragraph("<b>2. ESPECIFICAÇÃO DO PRODUTO E FORNECIMENTO</b>", style_label))
        elementos.append(Spacer(1, 2))
        elementos.append(t_gas)
        elementos.append(Spacer(1, 6))

        # 3. Termo de Recebimento e Segurança
        termo_texto = (
            "<b>TERMO DE RECEBIMENTO E CONFORMIDADE:</b> Declaro que recebi em perfeitas condições de uso e segurança "
            "a quantidade de botijão(ões) de gás GLP especificada acima, com lacre(s) de segurança intacto(s), sem vazamentos "
            "e em conformidade com as normas da ANP/ABNT. O produto destina-se exclusivamente às atividades da cozinha escolar."
        )
        elementos.append(Paragraph(termo_texto, style_termo))
        elementos.append(Spacer(1, 14))

        # 4. Assinaturas
        assinaturas_data = [
            [
                Paragraph("____________________________________________________<br/><b>Responsável pelo Recebimento na Escola</b><br/>"
                          f"Nome: {entrega.recebedor_nome}<br/>"
                          f"Cargo: {entrega.recebedor_cargo or 'Merendeira/Resp.'} | CPF: {entrega.recebedor_cpf or 'Não inf.'}", style_subtitulo),
                Paragraph("____________________________________________________<br/><b>Entregador / Distribuidora de Gás</b><br/>"
                          f"Nome: {entrega.entregador_nome or 'Entregador Credenciado'}<br/>"
                          f"Data: {data_formatada}", style_subtitulo)
            ]
        ]
        t_assinaturas = Table(assinaturas_data, colWidths=[9.0 * cm, 9.0 * cm])
        t_assinaturas.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elementos.append(t_assinaturas)
        return elementos

    story = []
    # 1ª Via (Escola)
    story.extend(criar_bloco_comprovante("1ª VIA - UNIDADE ESCOLAR / PRESTAÇÃO DE CONTAS"))
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#94a3b8"), dash=[4, 3], spaceAfter=14))
    # 2ª Via (Secretaria / Fornecedor)
    story.extend(criar_bloco_comprovante("2ª VIA - SECRETARIA DE EDUCAÇÃO / FORNECEDOR"))

    doc.build(story)
    buffer.seek(0)

    registrar_log(f'Emitiu Comprovante de Entrega de Gás #{entrega.codigo_comprovante} em PDF.')

    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Comprovante_Gas_{entrega.codigo_comprovante}.pdf'
    return response


# =============================================================================
# CLASSES AUXILIARES E HELPER FUNCTIONS PARA REPORTLAB
# =============================================================================
class GasNumberedCanvas(canvas_lib.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748b"))
        page_width, page_height = self._pagesize
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.6)
        self.line(1.2 * cm, 1.1 * cm, page_width - 1.2 * cm, 1.1 * cm)
        self.drawString(1.2 * cm, 0.7 * cm, "Gestor 360º • Sistema Integrado de Gestão Municipal — Valença do Piauí / PNAE")
        texto_pag = f"Página {self._pageNumber} de {page_count}"
        self.drawRightString(page_width - 1.2 * cm, 0.7 * cm, texto_pag)
        self.restoreState()


def _criar_cabecalho_oficial_flowable(titulo, subtitulo, periodo_info, largura_total=27.0 * cm):
    """Cria cabeçalho institucional oficial para os relatórios em PDF."""
    styles = getSampleStyleSheet()
    cor_primaria = colors.HexColor("#072657")
    cor_cinza_texto = colors.HexColor("#475569")

    style_inst = ParagraphStyle(
        'InstCabGas', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=8.5, leading=11, textColor=cor_primaria, alignment=TA_CENTER
    )
    style_tit = ParagraphStyle(
        'TitCabGas', parent=styles['Heading1'], fontName='Helvetica-Bold',
        fontSize=11.5, leading=14, textColor=cor_primaria, alignment=TA_CENTER
    )
    style_sub = ParagraphStyle(
        'SubCabGas', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=8.5, leading=11, textColor=colors.HexColor("#b91c1c"), alignment=TA_CENTER
    )
    style_meta = ParagraphStyle(
        'MetaCabGas', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.5, leading=10, textColor=cor_cinza_texto, alignment=TA_CENTER
    )

    elementos = []
    timbre_path = os.path.join(current_app.root_path, 'static', 'timbre.jpg')
    tem_timbre = os.path.exists(timbre_path)

    textos_inst = (
        "<b>ESTADO DO PIAUÍ — PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ</b><br/>"
        "SECRETARIA MUNICIPAL DE EDUCAÇÃO • COORDENAÇÃO DE ALIMENTAÇÃO ESCOLAR<br/>"
        "<b>PROGRAMA NACIONAL DE ALIMENTAÇÃO ESCOLAR (PNAE)</b>"
    )

    if tem_timbre:
        img = RLImage(timbre_path, width=3.8 * cm, height=1.15 * cm)
        t_header = Table([[img, Paragraph(textos_inst, style_inst)]], colWidths=[4.0 * cm, largura_total - 4.0 * cm])
        t_header.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
        ]))
        elementos.append(t_header)
    else:
        elementos.append(Paragraph(textos_inst, style_inst))

    elementos.append(Spacer(1, 4))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=cor_primaria, spaceAfter=6))
    elementos.append(Paragraph(f"<b>{titulo.upper()}</b>", style_tit))
    if subtitulo:
        elementos.append(Paragraph(subtitulo, style_sub))
    elementos.append(Spacer(1, 2))
    meta_str = f"Referência: <b>{periodo_info}</b> | Emissão: <b>{obter_data_hora_br_str()}</b> | Emitido por: <b>{session.get('username', 'Admin')}</b>"
    elementos.append(Paragraph(meta_str, style_meta))
    elementos.append(Spacer(1, 8))
    return elementos


def _criar_bloco_assinaturas_institucionais(largura_total=27.0 * cm, incluir_secretario=False):
    """Gera bloco de assinaturas oficiais com formatação apropriada."""
    styles = getSampleStyleSheet()
    style_ass = ParagraphStyle(
        'AssinaturaGas', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.5, leading=10, textColor=colors.HexColor("#1e293b"), alignment=TA_CENTER
    )

    if incluir_secretario:
        col_w = largura_total / 3.0
        dados = [[
            Paragraph("____________________________________________<br/><b>Nutricionista Responsável Técnica</b><br/>Alimentação Escolar (PNAE) / CRN", style_ass),
            Paragraph("____________________________________________<br/><b>Coordenação de Merenda Escolar</b><br/>Secretaria Municipal de Educação", style_ass),
            Paragraph("____________________________________________<br/><b>Secretário(a) Municipal de Educação</b><br/>Prefeitura Municipal de Valença do Piauí", style_ass)
        ]]
        t = Table(dados, colWidths=[col_w, col_w, col_w])
    else:
        col_w = largura_total / 2.0
        dados = [[
            Paragraph("________________________________________________________<br/><b>Responsável Técnico / Conferência</b><br/>Programa de Alimentação Escolar (PNAE)", style_ass),
            Paragraph("________________________________________________________<br/><b>Coordenação da Merenda Escolar</b><br/>Secretaria Municipal de Educação", style_ass)
        ]]
        t = Table(dados, colWidths=[col_w, col_w])

    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    return t


# =============================================================================
# 7. RELATÓRIO DE ENTREGAS POR DIAS E POR ESCOLA EM PDF
# =============================================================================
@gas_bp.route('/relatorio/por-dias/pdf')
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def relatorio_entregas_dias_pdf():
    executar_migracao_gas_segura()
    hoje = date.today()

    data_fim_str = request.args.get('data_fim')
    data_inicio_str = request.args.get('data_inicio')
    escola_id = request.args.get('escola_id', type=int)

    if not data_fim_str:
        dt_fim = hoje
    else:
        try:
            dt_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
        except Exception:
            dt_fim = hoje

    if not data_inicio_str:
        dt_inicio = dt_fim - timedelta(days=30)
    else:
        try:
            dt_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
        except Exception:
            dt_inicio = dt_fim - timedelta(days=30)

    if dt_inicio > dt_fim:
        dt_inicio, dt_fim = dt_fim, dt_inicio

    query = EntregaGasEscola.query.filter(
        EntregaGasEscola.status != 'Cancelado',
        EntregaGasEscola.data_entrega >= dt_inicio,
        EntregaGasEscola.data_entrega <= dt_fim
    )

    escola_obj = None
    if escola_id and escola_id > 0:
        escola_obj = Escola.query.get(escola_id)
        if escola_obj:
            query = query.filter(EntregaGasEscola.escola_id == escola_obj.id)

    entregas = query.order_by(EntregaGasEscola.data_entrega.desc(), EntregaGasEscola.id.desc()).all()

    # Estatísticas
    total_botijoes = sum(e.quantidade for e in entregas)
    total_valor = sum(e.valor_total or 0.0 for e in entregas)
    qtd_p13 = sum(e.quantidade for e in entregas if e.tipo_botijao == 'P13')
    qtd_p20 = sum(e.quantidade for e in entregas if e.tipo_botijao == 'P20')
    qtd_p45 = sum(e.quantidade for e in entregas if e.tipo_botijao == 'P45')
    escolas_atendidas_ids = set(e.escola_id for e in entregas)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.4 * cm,
        title=f"Relatorio_Entregas_Gas_Periodo_{dt_inicio}_{dt_fim}"
    )

    styles = getSampleStyleSheet()
    cor_primaria = colors.HexColor("#072657")
    largura_util = 27.3 * cm

    style_th = ParagraphStyle(
        'THGas', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=7.2, leading=9, textColor=colors.white, alignment=TA_CENTER
    )
    style_td = ParagraphStyle(
        'TDGas', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.2, leading=9, textColor=colors.HexColor("#1e293b")
    )
    style_td_c = ParagraphStyle(
        'TDCGas', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.2, leading=9, textColor=colors.HexColor("#1e293b"), alignment=TA_CENTER
    )
    style_kpi_num = ParagraphStyle(
        'KPINumGas', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=12, leading=14, textColor=cor_primaria, alignment=TA_CENTER
    )
    style_kpi_lbl = ParagraphStyle(
        'KPILblGas', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.0, leading=9, textColor=colors.HexColor("#475569"), alignment=TA_CENTER
    )

    periodo_str = f"De {dt_inicio.strftime('%d/%m/%Y')} a {dt_fim.strftime('%d/%m/%Y')}"
    filtro_escola_str = f"Unidade: {escola_obj.nome}" if escola_obj else "Abrangência: Todas as Unidades Escolares"

    story = []
    story.extend(_criar_cabecalho_oficial_flowable(
        titulo="Relatório de Fornecimento de Gás por Dias e Unidade Escolar",
        subtitulo="Demonstrativo Cronológico de Entregas e Suprimento de Gás GLP",
        periodo_info=f"{periodo_str} • {filtro_escola_str}",
        largura_total=largura_util
    ))

    # Box de KPIs
    breakdown_bot = f"P13: {qtd_p13} | P20: {qtd_p20} | P45: {qtd_p45}"
    kpi_data = [
        [
            Paragraph(f"<b>{len(entregas)}</b>", style_kpi_num),
            Paragraph(f"<b>{total_botijoes} botijões</b>", style_kpi_num),
            Paragraph(f"<b>R$ {total_valor:,.2f}</b>".replace(',', 'X').replace('.', ',').replace('X', '.'), style_kpi_num),
            Paragraph(f"<b>{len(escolas_atendidas_ids)} unidade(s)</b>", style_kpi_num)
        ],
        [
            Paragraph("Total de Entregas Realizadas", style_kpi_lbl),
            Paragraph(f"Volume Fornecido ({breakdown_bot})", style_kpi_lbl),
            Paragraph("Investimento Total no Período", style_kpi_lbl),
            Paragraph("Escolas Atendidas no Intervalo", style_kpi_lbl)
        ]
    ]
    col_kpi = largura_util / 4.0
    t_kpi = Table(kpi_data, colWidths=[col_kpi, col_kpi, col_kpi, col_kpi])
    t_kpi.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t_kpi)
    story.append(Spacer(1, 8))

    # Tabela de Entregas
    cabecalho = [
        Paragraph("Data / Hora", style_th),
        Paragraph("Unidade Escolar", style_th),
        Paragraph("Comprovante", style_th),
        Paragraph("Qtd / Tipo", style_th),
        Paragraph("Lacre / NF", style_th),
        Paragraph("Fornecedor", style_th),
        Paragraph("Vlr Total (R$)", style_th),
        Paragraph("Recebedor na Escola", style_th),
        Paragraph("Previsão Fim", style_th)
    ]
    linhas = [cabecalho]

    if entregas:
        for ent in entregas:
            dt_ent = ent.data_entrega.strftime('%d/%m/%Y')
            hr_ent = f" às {ent.hora_entrega}" if ent.hora_entrega else ""
            dt_fim_prev = ent.data_prevista_fim.strftime('%d/%m/%Y') if ent.data_prevista_fim else '-'
            vlr_str = f"R$ {ent.valor_total:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if ent.valor_total else '-'

            linhas.append([
                Paragraph(f"{dt_ent}{hr_ent}", style_td_c),
                Paragraph(f"<b>{ent.escola.nome[:30]}</b>", style_td),
                Paragraph(f"#{ent.codigo_comprovante}", style_td_c),
                Paragraph(f"<b>{ent.quantidade}x {ent.tipo_botijao}</b>", style_td_c),
                Paragraph(ent.numero_lacre_nf or '-', style_td_c),
                Paragraph(ent.fornecedor or '-', style_td),
                Paragraph(vlr_str, style_td_c),
                Paragraph(f"{ent.recebedor_nome[:22]}<br/><font color='#64748b'>{ent.recebedor_cargo or 'Merendeira'}</font>", style_td),
                Paragraph(f"{dt_fim_prev}<br/><font color='#64748b'>{ent.dias_duracao_estimada} dias</font>", style_td_c)
            ])

        # Linha de Totais
        vlr_tot_str = f"R$ {total_valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        linhas.append([
            Paragraph("<b>TOTAIS</b>", style_td_c),
            Paragraph(f"<b>{len(escolas_atendidas_ids)} escola(s) atendida(s)</b>", style_td),
            Paragraph(f"<b>{len(entregas)} entregas</b>", style_td_c),
            Paragraph(f"<b>{total_botijoes} botijões</b>", style_td_c),
            Paragraph("-", style_td_c),
            Paragraph("-", style_td_c),
            Paragraph(f"<b>{vlr_tot_str}</b>", style_td_c),
            Paragraph("-", style_td_c),
            Paragraph("-", style_td_c)
        ])
    else:
        linhas.append([
            Paragraph("Nenhuma entrega de gás registrada para os filtros selecionados.", style_td_c),
            Paragraph("", style_td), Paragraph("", style_td), Paragraph("", style_td),
            Paragraph("", style_td), Paragraph("", style_td), Paragraph("", style_td),
            Paragraph("", style_td), Paragraph("", style_td)
        ])

    col_w = [2.4 * cm, 5.5 * cm, 2.6 * cm, 2.2 * cm, 2.8 * cm, 3.2 * cm, 2.5 * cm, 3.7 * cm, 2.4 * cm]
    t_ent = Table(linhas, colWidths=col_w)
    t_ent.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), cor_primaria),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2 if len(linhas) > 2 else -1), [colors.white, colors.HexColor("#f8fafc")]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    if not entregas:
        t_ent.setStyle(TableStyle([
            ('SPAN', (0, 1), (-1, 1)),
            ('ALIGN', (0, 1), (-1, 1), 'CENTER'),
        ]))

    story.append(t_ent)
    story.append(Spacer(1, 12))
    story.append(KeepTogether(_criar_bloco_assinaturas_institucionais(largura_total=largura_util)))

    doc.build(story, canvasmaker=GasNumberedCanvas)
    buffer.seek(0)

    registrar_log(f'Gerou Relatório de Entregas de Gás por Dias em PDF ({dt_inicio} a {dt_fim}).')
    resp = make_response(buffer.getvalue())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=Relatorio_Gas_Dias_{dt_inicio}_{dt_fim}.pdf'
    return resp


# =============================================================================
# 8. RELATÓRIO MENSAL DE ENTREGA DE GÁS POR ESCOLA EM PDF
# =============================================================================
@gas_bp.route('/relatorio/mensal/pdf')
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def relatorio_entregas_mensal_pdf():
    executar_migracao_gas_segura()
    hoje = date.today()
    mes = request.args.get('mes', hoje.month, type=int)
    ano = request.args.get('ano', hoje.year, type=int)
    escola_id = request.args.get('escola_id', type=int)

    if mes < 1 or mes > 12:
        mes = hoje.month

    MESES_NOMES = [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
    ]
    nome_mes = MESES_NOMES[mes - 1]

    # Mês anterior para cálculo comparativo
    if mes == 1:
        mes_ant = 12
        ano_ant = ano - 1
    else:
        mes_ant = mes - 1
        ano_ant = ano

    styles = getSampleStyleSheet()
    cor_primaria = colors.HexColor("#072657")

    style_th = ParagraphStyle(
        'THGM', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=7.2, leading=9, textColor=colors.white, alignment=TA_CENTER
    )
    style_td = ParagraphStyle(
        'TDGM', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.2, leading=9, textColor=colors.HexColor("#1e293b")
    )
    style_td_c = ParagraphStyle(
        'TDCGM', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.2, leading=9, textColor=colors.HexColor("#1e293b"), alignment=TA_CENTER
    )
    style_kpi_num = ParagraphStyle(
        'KPINumGM', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=12, leading=14, textColor=cor_primaria, alignment=TA_CENTER
    )
    style_kpi_lbl = ParagraphStyle(
        'KPILblGM', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.0, leading=9, textColor=colors.HexColor("#475569"), alignment=TA_CENTER
    )

    buffer = BytesIO()

    # CASO 1: UMA ESCOLA ESPECÍFICA (Relatório em Formato Retrato / Ficha Mensal)
    if escola_id and escola_id > 0:
        escola = Escola.query.get_or_404(escola_id)
        config = ConfiguracaoGasEscola.query.filter_by(escola_id=escola.id).first()
        info_gas = obter_status_gas_escola(escola)

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=1.5 * cm,
            rightMargin=1.5 * cm,
            topMargin=1.2 * cm,
            bottomMargin=1.4 * cm,
            title=f"Relatorio_Mensal_Gas_{escola.nome}_{mes}_{ano}"
        )
        largura_util = 18.0 * cm

        entregas_mes = EntregaGasEscola.query.filter(
            EntregaGasEscola.escola_id == escola.id,
            EntregaGasEscola.status != 'Cancelado',
            func.extract('month', EntregaGasEscola.data_entrega) == mes,
            func.extract('year', EntregaGasEscola.data_entrega) == ano
        ).order_by(EntregaGasEscola.data_entrega.asc()).all()

        total_bot_mes = sum(e.quantidade for e in entregas_mes)
        total_val_mes = sum(e.valor_total or 0.0 for e in entregas_mes)

        # Mês anterior
        total_bot_ant = (
            db.session.query(func.sum(EntregaGasEscola.quantidade))
            .filter(
                EntregaGasEscola.escola_id == escola.id,
                EntregaGasEscola.status != 'Cancelado',
                func.extract('month', EntregaGasEscola.data_entrega) == mes_ant,
                func.extract('year', EntregaGasEscola.data_entrega) == ano_ant
            ).scalar() or 0
        )

        variacao_str = "0% (Estável)"
        if total_bot_ant > 0:
            var_num = round(((total_bot_mes - total_bot_ant) / float(total_bot_ant)) * 100, 1)
            if var_num > 0:
                variacao_str = f"+{var_num}% (Aumento de Consumo)"
            elif var_num < 0:
                variacao_str = f"{var_num}% (Redução de Consumo)"
        elif total_bot_mes > 0:
            variacao_str = "Novo ciclo / Sem base anterior"

        story = []
        story.extend(_criar_cabecalho_oficial_flowable(
            titulo=f"Relatório Mensal de Fornecimento de Gás • {nome_mes}/{ano}",
            subtitulo=f"Unidade Escolar: {escola.nome}",
            periodo_info=f"Exercício {ano} • INEP: {escola.codigo_inep or 'N/I'} • Zona: {escola.zona or 'Urbana'}",
            largura_total=largura_util
        ))

        # Ficha Cadastral da Unidade
        ficha_data = [
            [
                Paragraph("<b>Unidade Escolar:</b>", style_td),
                Paragraph(escola.nome, style_td),
                Paragraph("<b>Diretor(a):</b>", style_td),
                Paragraph(escola.diretor_responsavel or 'Não informado', style_td)
            ],
            [
                Paragraph("<b>Endereço:</b>", style_td),
                Paragraph(escola.endereco or 'Valença do Piauí', style_td),
                Paragraph("<b>Estrutura Cadastrada:</b>", style_td),
                Paragraph(f"{config.qtd_fogoes if config else 1} fogão(ões) • {config.tipo_botijao_padrao if config else 'P13'}", style_td)
            ]
        ]
        t_ficha = Table(ficha_data, colWidths=[3.2 * cm, 6.0 * cm, 3.4 * cm, 5.4 * cm])
        t_ficha.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(t_ficha)
        story.append(Spacer(1, 8))

        # KPIs do Mês
        kpi_data = [
            [
                Paragraph(f"<b>{total_bot_mes} botijão(ões)</b>", style_kpi_num),
                Paragraph(f"<b>R$ {total_val_mes:,.2f}</b>".replace(',', 'X').replace('.', ',').replace('X', '.'), style_kpi_num),
                Paragraph(f"<b>{variacao_str}</b>", style_kpi_num),
                Paragraph(f"<b>{info_gas['status']} ({info_gas['dias_restantes']}d)</b>", style_kpi_num)
            ],
            [
                Paragraph("Entregas no Mês", style_kpi_lbl),
                Paragraph("Investimento no Mês", style_kpi_lbl),
                Paragraph(f"Variação vs Mês Anterior ({MESES_NOMES[mes_ant-1][:3]})", style_kpi_lbl),
                Paragraph("Situação Atual de Autonomia", style_kpi_lbl)
            ]
        ]
        col_k = largura_util / 4.0
        t_k = Table(kpi_data, colWidths=[col_k, col_k, col_k, col_k])
        t_k.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#ffffff")),
            ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor("#cbd5e1")),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(t_k)
        story.append(Spacer(1, 10))

        # Tabela com as entregas do mês
        cab_ent = [
            Paragraph("Data", style_th),
            Paragraph("Comprovante", style_th),
            Paragraph("Qtd / Tipo", style_th),
            Paragraph("Lacre / NF", style_th),
            Paragraph("Vlr Total", style_th),
            Paragraph("Recebedor na Escola", style_th),
            Paragraph("Previsão Término", style_th)
        ]
        lin_ent = [cab_ent]
        if entregas_mes:
            for e in entregas_mes:
                dt_fim_p = e.data_prevista_fim.strftime('%d/%m/%Y') if e.data_prevista_fim else '-'
                vlr_e = f"R$ {e.valor_total:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if e.valor_total else '-'
                lin_ent.append([
                    Paragraph(e.data_entrega.strftime('%d/%m/%Y'), style_td_c),
                    Paragraph(f"#{e.codigo_comprovante}", style_td_c),
                    Paragraph(f"<b>{e.quantidade}x {e.tipo_botijao}</b>", style_td_c),
                    Paragraph(e.numero_lacre_nf or '-', style_td_c),
                    Paragraph(vlr_e, style_td_c),
                    Paragraph(e.recebedor_nome, style_td),
                    Paragraph(dt_fim_p, style_td_c)
                ])
        else:
            lin_ent.append([
                Paragraph(f"Nenhum fornecimento de gás realizado para esta escola no mês de {nome_mes}.", style_td_c),
                Paragraph("", style_td), Paragraph("", style_td), Paragraph("", style_td),
                Paragraph("", style_td), Paragraph("", style_td), Paragraph("", style_td)
            ])

        col_w_esc = [2.2 * cm, 2.6 * cm, 2.4 * cm, 2.8 * cm, 2.4 * cm, 3.4 * cm, 2.2 * cm]
        t_tab_esc = Table(lin_ent, colWidths=col_w_esc)
        t_tab_esc.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), cor_primaria),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        if not entregas_mes:
            t_tab_esc.setStyle(TableStyle([
                ('SPAN', (0, 1), (-1, 1)),
                ('ALIGN', (0, 1), (-1, 1), 'CENTER'),
            ]))
        story.append(t_tab_esc)
        story.append(Spacer(1, 14))
        story.append(KeepTogether(_criar_bloco_assinaturas_institucionais(largura_total=largura_util)))

    # CASO 2: TODAS AS ESCOLAS (Relatório Consolidado Municipal em Paisagem)
    else:
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=1.2 * cm,
            rightMargin=1.2 * cm,
            topMargin=1.2 * cm,
            bottomMargin=1.4 * cm,
            title=f"Relatorio_Mensal_Gas_Rede_{mes}_{ano}"
        )
        largura_util = 27.3 * cm

        escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome.asc()).all()
        if not escolas:
            escolas = Escola.query.order_by(Escola.nome.asc()).all()

        story = []
        story.extend(_criar_cabecalho_oficial_flowable(
            titulo=f"Relatório Consolidado Mensal de Fornecimento de Gás • {nome_mes} de {ano}",
            subtitulo="Demonstrativo Comparativo de Consumo por Unidade Escolar da Rede Municipal",
            periodo_info=f"Mês de Referência: {nome_mes}/{ano} • Rede Municipal de Ensino",
            largura_total=largura_util
        ))

        cab_mes = [
            Paragraph("Unidade Escolar", style_th),
            Paragraph("INEP", style_th),
            Paragraph("Entregas Mês", style_th),
            Paragraph("Botijões Mês", style_th),
            Paragraph("Custo Total (R$)", style_th),
            Paragraph(f"Mês Ant. ({MESES_NOMES[mes_ant-1][:3]})", style_th),
            Paragraph("Variação %", style_th),
            Paragraph("Última Entrega", style_th),
            Paragraph("Situação Atual", style_th)
        ]
        linhas_m = [cab_mes]

        total_rede_bot = 0
        total_rede_val = 0.0
        total_rede_ent = 0
        total_rede_bot_ant = 0

        for esc in escolas:
            entregas_esc_mes = EntregaGasEscola.query.filter(
                EntregaGasEscola.escola_id == esc.id,
                EntregaGasEscola.status != 'Cancelado',
                func.extract('month', EntregaGasEscola.data_entrega) == mes,
                func.extract('year', EntregaGasEscola.data_entrega) == ano
            ).all()

            qtd_m = sum(e.quantidade for e in entregas_esc_mes)
            val_m = sum(e.valor_total or 0.0 for e in entregas_esc_mes)
            qtd_ent_m = len(entregas_esc_mes)

            qtd_ant = (
                db.session.query(func.sum(EntregaGasEscola.quantidade))
                .filter(
                    EntregaGasEscola.escola_id == esc.id,
                    EntregaGasEscola.status != 'Cancelado',
                    func.extract('month', EntregaGasEscola.data_entrega) == mes_ant,
                    func.extract('year', EntregaGasEscola.data_entrega) == ano_ant
                ).scalar() or 0
            )

            total_rede_bot += qtd_m
            total_rede_val += val_m
            total_rede_ent += qtd_ent_m
            total_rede_bot_ant += qtd_ant

            var_pct_str = "-"
            if qtd_ant > 0:
                diff = round(((qtd_m - qtd_ant) / float(qtd_ant)) * 100, 1)
                if diff > 0:
                    var_pct_str = f"+{diff}%"
                elif diff < 0:
                    var_pct_str = f"{diff}%"
                else:
                    var_pct_str = "0%"
            elif qtd_m > 0:
                var_pct_str = "+100%"

            info = obter_status_gas_escola(esc)
            u_ent_data = info['ultima_entrega'].data_entrega.strftime('%d/%m/%Y') if info['tem_entrega'] else '-'

            val_str = f"R$ {val_m:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if val_m > 0 else '-'

            linhas_m.append([
                Paragraph(f"<b>{esc.nome[:32]}</b>", style_td),
                Paragraph(esc.codigo_inep or '-', style_td_c),
                Paragraph(f"{qtd_ent_m} ent.", style_td_c),
                Paragraph(f"<b>{qtd_m} bot.</b>" if qtd_m > 0 else "-", style_td_c),
                Paragraph(val_str, style_td_c),
                Paragraph(f"{qtd_ant} bot." if qtd_ant > 0 else "-", style_td_c),
                Paragraph(var_pct_str, style_td_c),
                Paragraph(u_ent_data, style_td_c),
                Paragraph(f"<b>{info['status']}</b> ({info['dias_restantes']}d)" if info['tem_entrega'] else "Sem reg.", style_td_c)
            ])

        # Linha consolidada
        tot_val_str = f"R$ {total_rede_val:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        diff_rede = "-"
        if total_rede_bot_ant > 0:
            diff_r = round(((total_rede_bot - total_rede_bot_ant) / float(total_rede_bot_ant)) * 100, 1)
            diff_rede = f"+{diff_r}%" if diff_r > 0 else f"{diff_r}%"

        linhas_m.append([
            Paragraph("<b>TOTAL GERAL CONSOLIDADO</b>", style_td),
            Paragraph("-", style_td_c),
            Paragraph(f"<b>{total_rede_ent} entregas</b>", style_td_c),
            Paragraph(f"<b>{total_rede_bot} botijões</b>", style_td_c),
            Paragraph(f"<b>{tot_val_str}</b>", style_td_c),
            Paragraph(f"<b>{total_rede_bot_ant} bot.</b>", style_td_c),
            Paragraph(f"<b>{diff_rede}</b>", style_td_c),
            Paragraph("-", style_td_c),
            Paragraph("-", style_td_c)
        ])

        col_w_m = [6.5 * cm, 2.2 * cm, 2.2 * cm, 2.4 * cm, 2.8 * cm, 2.4 * cm, 2.4 * cm, 2.6 * cm, 3.8 * cm]
        t_tab_m = Table(linhas_m, colWidths=col_w_m)
        t_tab_m.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), cor_primaria),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8fafc")]),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(t_tab_m)
        story.append(Spacer(1, 14))
        story.append(KeepTogether(_criar_bloco_assinaturas_institucionais(largura_total=largura_util)))

    doc.build(story, canvasmaker=GasNumberedCanvas)
    buffer.seek(0)

    registrar_log(f'Gerou Relatório Mensal de Gás em PDF ({mes}/{ano}).')
    resp = make_response(buffer.getvalue())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=Relatorio_Mensal_Gas_{mes}_{ano}.pdf'
    return resp


# =============================================================================
# 9. RELATÓRIO ANUAL ANALÍTICO E INDICADORES DE CONSUMO DE GÁS EM PDF
# =============================================================================
@gas_bp.route('/relatorio/anual/pdf')
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def relatorio_entregas_anual_analitico_pdf():
    executar_migracao_gas_segura()
    ano = request.args.get('ano', datetime.now().year, type=int)
    escola_id = request.args.get('escola_id', type=int)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.4 * cm,
        title=f"Relatorio_Anual_Analitico_Gas_{ano}"
    )

    styles = getSampleStyleSheet()
    cor_primaria = colors.HexColor("#072657")
    largura_util = 27.3 * cm

    style_th = ParagraphStyle(
        'THGA', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=7.2, leading=9, textColor=colors.white, alignment=TA_CENTER
    )
    style_td = ParagraphStyle(
        'TDGA', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.2, leading=9, textColor=colors.HexColor("#1e293b")
    )
    style_td_c = ParagraphStyle(
        'TDCGA', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.2, leading=9, textColor=colors.HexColor("#1e293b"), alignment=TA_CENTER
    )
    style_kpi_num = ParagraphStyle(
        'KPINumGA', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=12, leading=14, textColor=cor_primaria, alignment=TA_CENTER
    )
    style_kpi_lbl = ParagraphStyle(
        'KPILblGA', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.0, leading=9, textColor=colors.HexColor("#475569"), alignment=TA_CENTER
    )
    style_secao = ParagraphStyle(
        'SecaoGA', parent=styles['Heading2'], fontName='Helvetica-Bold',
        fontSize=9.5, leading=12, textColor=cor_primaria
    )

    MESES_NOMES = [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
    ]

    escola_obj = None
    if escola_id and escola_id > 0:
        escola_obj = Escola.query.get(escola_id)

    entregas_ano = EntregaGasEscola.query.filter(
        EntregaGasEscola.status != 'Cancelado',
        func.extract('year', EntregaGasEscola.data_entrega) == ano
    )
    if escola_obj:
        entregas_ano = entregas_ano.filter(EntregaGasEscola.escola_id == escola_obj.id)
    entregas_ano = entregas_ano.all()

    total_botijoes_ano = sum(e.quantidade for e in entregas_ano)
    total_gasto_ano = sum(e.valor_total or 0.0 for e in entregas_ano)
    media_mensal_bot = round(total_botijoes_ano / 12.0, 1)
    custo_medio_bot = round(total_gasto_ano / float(total_botijoes_ano), 2) if total_botijoes_ano > 0 else 0.0

    duracoes = [e.dias_duracao_estimada for e in entregas_ano if e.dias_duracao_estimada and e.dias_duracao_estimada > 0]
    media_duracao = round(sum(duracoes) / float(len(duracoes)), 1) if duracoes else 20.0

    story = []
    subtitulo_str = f"Unidade Escolar: {escola_obj.nome}" if escola_obj else "Consolidado Geral de Toda a Rede Municipal"
    story.extend(_criar_cabecalho_oficial_flowable(
        titulo=f"Relatório Anual Analítico e Indicadores de Consumo de Gás • Exercício {ano}",
        subtitulo=subtitulo_str,
        periodo_info=f"Ano Civil / Letivo {ano} • Programa Nacional de Alimentação Escolar (PNAE)",
        largura_total=largura_util
    ))

    # 1. Indicadores Executivos Globais (KPIs Anuais)
    kpis_anuais = [
        [
            Paragraph(f"<b>{total_botijoes_ano} botijões</b>", style_kpi_num),
            Paragraph(f"<b>R$ {total_gasto_ano:,.2f}</b>".replace(',', 'X').replace('.', ',').replace('X', '.'), style_kpi_num),
            Paragraph(f"<b>{media_mensal_bot} bot./mês</b>", style_kpi_num),
            Paragraph(f"<b>R$ {custo_medio_bot:,.2f}</b>".replace(',', 'X').replace('.', ',').replace('X', '.'), style_kpi_num),
            Paragraph(f"<b>{media_duracao} dias</b>", style_kpi_num),
        ],
        [
            Paragraph("Total Fornecido no Ano", style_kpi_lbl),
            Paragraph("Investimento Total Anual", style_kpi_lbl),
            Paragraph("Média Mensal Consumida", style_kpi_lbl),
            Paragraph("Custo Médio p/ Botijão", style_kpi_lbl),
            Paragraph("Autonomia Média Estimada", style_kpi_lbl),
        ]
    ]
    col_kpi_a = largura_util / 5.0
    t_kpi_a = Table(kpis_anuais, colWidths=[col_kpi_a] * 5)
    t_kpi_a.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t_kpi_a)
    story.append(Spacer(1, 10))

    # 2. Matriz Analítica Mês a Mês (Jan a Dez)
    story.append(Paragraph("<b>1. MATRIZ ANALÍTICA DE CONSUMO E CUSTOS MÊS A MÊS</b>", style_secao))
    story.append(Spacer(1, 4))

    cab_matriz = [
        Paragraph("Mês de Referência", style_th),
        Paragraph("Qtd Entregas", style_th),
        Paragraph("Botijões Fornecidos", style_th),
        Paragraph("Investimento (R$)", style_th),
        Paragraph("% do Total Anual", style_th),
        Paragraph("Comparativo c/ Mês Anterior", style_th),
        Paragraph("Comportamento", style_th)
    ]
    linhas_matriz = [cab_matriz]
    totais_mes = [0] * 12

    for m in range(1, 13):
        ents_m = [e for e in entregas_ano if e.data_entrega.month == m]
        qtd_m = sum(e.quantidade for e in ents_m)
        val_m = sum(e.valor_total or 0.0 for e in ents_m)
        totais_mes[m - 1] = qtd_m

        pct_anual = round((val_m / total_gasto_ano) * 100, 1) if total_gasto_ano > 0 else 0.0
        val_m_str = f"R$ {val_m:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if val_m > 0 else '-'

        var_str = "-"
        status_comp = "Estável"
        if m > 1 and totais_mes[m - 2] > 0:
            diff = round(((qtd_m - totais_mes[m - 2]) / float(totais_mes[m - 2])) * 100, 1)
            if diff > 0:
                var_str = f"+{diff}%"
                status_comp = "Aumento"
            elif diff < 0:
                var_str = f"{diff}%"
                status_comp = "Redução"
            else:
                var_str = "0%"
                status_comp = "Estável"
        elif qtd_m > 0 and m > 1:
            var_str = "+100%"
            status_comp = "Aumento"
        elif qtd_m == 0:
            status_comp = "Sem Fornecimento (Recesso)"

        linhas_matriz.append([
            Paragraph(f"<b>{MESES_NOMES[m-1]}</b>", style_td),
            Paragraph(f"{len(ents_m)}", style_td_c),
            Paragraph(f"<b>{qtd_m} botijões</b>" if qtd_m > 0 else "-", style_td_c),
            Paragraph(val_m_str, style_td_c),
            Paragraph(f"{pct_anual}%" if pct_anual > 0 else "-", style_td_c),
            Paragraph(var_str, style_td_c),
            Paragraph(status_comp, style_td_c)
        ])

    tot_val_ano_str = f"R$ {total_gasto_ano:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    linhas_matriz.append([
        Paragraph("<b>CONSOLIDADO ANUAL</b>", style_td),
        Paragraph(f"<b>{len(entregas_ano)} entregas</b>", style_td_c),
        Paragraph(f"<b>{total_botijoes_ano} botijões</b>", style_td_c),
        Paragraph(f"<b>{tot_val_ano_str}</b>", style_td_c),
        Paragraph("<b>100.0%</b>", style_td_c),
        Paragraph("-", style_td_c),
        Paragraph(f"<b>Média: {media_mensal_bot} bot./mês</b>", style_td_c)
    ])

    col_w_mat = [5.0 * cm, 2.8 * cm, 3.8 * cm, 4.2 * cm, 3.2 * cm, 4.3 * cm, 4.0 * cm]
    t_mat = Table(linhas_matriz, colWidths=col_w_mat)
    t_mat.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), cor_primaria),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8fafc")]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t_mat)
    story.append(Spacer(1, 12))

    # 3. Ranking de Consumo por Unidade Escolar (se for Consolidado Municipal)
    if not escola_obj:
        story.append(Paragraph("<b>2. RANKING DE CONSUMO ANUAL POR UNIDADE ESCOLAR</b>", style_secao))
        story.append(Spacer(1, 4))

        escolas_ativas = Escola.query.filter_by(status='Ativa').all()
        if not escolas_ativas:
            escolas_ativas = Escola.query.all()

        ranking = []
        for esc in escolas_ativas:
            ents_esc = [e for e in entregas_ano if e.escola_id == esc.id]
            qtd_esc = sum(e.quantidade for e in ents_esc)
            val_esc = sum(e.valor_total or 0.0 for e in ents_esc)
            ranking.append({
                'escola': esc,
                'qtd': qtd_esc,
                'valor': val_esc,
                'entregas': len(ents_esc),
                'info': obter_status_gas_escola(esc)
            })

        ranking.sort(key=lambda x: x['qtd'], reverse=True)

        cab_rank = [
            Paragraph("Pos.", style_th),
            Paragraph("Unidade Escolar", style_th),
            Paragraph("INEP", style_th),
            Paragraph("Total Botijões", style_th),
            Paragraph("Valor Total (R$)", style_th),
            Paragraph("% da Rede", style_th),
            Paragraph("Média Mensal", style_th),
            Paragraph("Situação Atual", style_th)
        ]
        linhas_rank = [cab_rank]

        for pos, item in enumerate(ranking, 1):
            esc = item['escola']
            pct_rede = round((item['qtd'] / float(total_botijoes_ano)) * 100, 1) if total_botijoes_ano > 0 else 0.0
            med_mensal_esc = round(item['qtd'] / 12.0, 1)
            val_esc_str = f"R$ {item['valor']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if item['valor'] > 0 else '-'

            sit_str = f"{item['info']['status']} ({item['info']['dias_restantes']}d)" if item['info']['tem_entrega'] else "Sem reg."

            linhas_rank.append([
                Paragraph(f"<b>{pos}º</b>", style_td_c),
                Paragraph(f"<b>{esc.nome[:32]}</b>", style_td),
                Paragraph(esc.codigo_inep or '-', style_td_c),
                Paragraph(f"<b>{item['qtd']} bot.</b>", style_td_c),
                Paragraph(val_esc_str, style_td_c),
                Paragraph(f"{pct_rede}%", style_td_c),
                Paragraph(f"{med_mensal_esc} bot./mês", style_td_c),
                Paragraph(sit_str, style_td_c)
            ])

        col_w_rank = [1.5 * cm, 7.5 * cm, 2.4 * cm, 3.0 * cm, 3.4 * cm, 2.5 * cm, 3.2 * cm, 3.8 * cm]
        t_rank = Table(linhas_rank, colWidths=col_w_rank)
        t_rank.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), cor_primaria),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(t_rank)
        story.append(Spacer(1, 14))

    # Assinaturas Institucionais de Encerramento
    story.append(KeepTogether(_criar_bloco_assinaturas_institucionais(largura_total=largura_util, incluir_secretario=True)))

    doc.build(story, canvasmaker=GasNumberedCanvas)
    buffer.seek(0)

    registrar_log(f'Gerou Relatório Anual Analítico de Gás em PDF ({ano}).')
    resp = make_response(buffer.getvalue())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=Relatorio_Anual_Analitico_Gas_{ano}.pdf'
    return resp


# =============================================================================
# 10. ALIAS DE COMPATIBILIDADE PARA RELATÓRIO GERAL
# =============================================================================
@gas_bp.route('/relatorio/pdf')
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def relatorio_geral_gas_pdf():
    """Redireciona para o relatório anual analítico completo."""
    ano = request.args.get('ano', datetime.now().year, type=int)
    escola_id = request.args.get('escola_id', type=int)
    return redirect(url_for('gas.relatorio_entregas_anual_analitico_pdf', ano=ano, escola_id=escola_id))

