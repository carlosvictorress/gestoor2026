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
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, HRFlowable
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
# 7. RELATÓRIO GERAL CONSOLIDADO DE GÁS EM PDF
# =============================================================================
@gas_bp.route('/relatorio/pdf')
@login_required
@role_required('admin', 'Merenda Escolar', 'RH')
def relatorio_geral_gas_pdf():
    executar_migracao_gas_segura()
    ano = request.args.get('ano', datetime.now().year, type=int)

    escolas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome.asc()).all()
    if not escolas:
        escolas = Escola.query.order_by(Escola.nome.asc()).all()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=f"Relatorio_Geral_Gas_{ano}"
    )

    styles = getSampleStyleSheet()
    cor_primaria = colors.HexColor("#072657")

    style_titulo = ParagraphStyle(
        'TituloRel', parent=styles['Heading1'], fontName='Helvetica-Bold',
        fontSize=13, leading=16, textColor=cor_primaria, alignment=TA_CENTER
    )
    style_sub = ParagraphStyle(
        'SubRel', parent=styles['Normal'], fontName='Helvetica',
        fontSize=8.5, leading=11, textColor=colors.HexColor("#475569"), alignment=TA_CENTER
    )
    style_th = ParagraphStyle(
        'TH', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=7.5, leading=9, textColor=colors.white, alignment=TA_CENTER
    )
    style_td = ParagraphStyle(
        'TD', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.5, leading=9, textColor=colors.HexColor("#1e293b")
    )
    style_td_center = ParagraphStyle(
        'TDC', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7.5, leading=9, textColor=colors.HexColor("#1e293b"), alignment=TA_CENTER
    )

    story = [
        Paragraph("<b>ESTADO DO PIAUÍ — PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ</b>", style_sub),
        Paragraph(f"<b>RELATÓRIO CONSOLIDADO DE FORNECIMENTO E PREVISÃO DE GÁS ESCOLAR — {ano}</b>", style_titulo),
        Paragraph(f"Emissão em: {obter_data_hora_br_str()} | Módulo de Merenda e Alimentação Escolar", style_sub),
        Spacer(1, 10)
    ]

    cabecalho = [
        Paragraph("Unidade Escolar", style_th),
        Paragraph("INEP", style_th),
        Paragraph("Última Entrega", style_th),
        Paragraph("Qtd / Tipo", style_th),
        Paragraph("Autonomia Est.", style_th),
        Paragraph("Previsão Esgotamento", style_th),
        Paragraph("Dias Restantes", style_th),
        Paragraph("Situação", style_th),
        Paragraph(f"Total {ano}", style_th)
    ]

    linhas_tabela = [cabecalho]
    total_botijoes_geral = 0

    for esc in escolas:
        info = obter_status_gas_escola(esc)
        total_ano_esc = (
            db.session.query(func.sum(EntregaGasEscola.quantidade))
            .filter(
                EntregaGasEscola.escola_id == esc.id,
                db.extract('year', EntregaGasEscola.data_entrega) == ano,
                EntregaGasEscola.status != 'Cancelado'
            ).scalar() or 0
        )
        total_botijoes_geral += total_ano_esc

        if info['tem_entrega']:
            u = info['ultima_entrega']
            dt_ent = u.data_entrega.strftime('%d/%m/%Y')
            qtd_tp = f"{u.quantidade}x {u.tipo_botijao}"
            auton = f"{u.dias_duracao_estimada} dias"
            dt_prev = info['data_prevista'].strftime('%d/%m/%Y')
            dias_rest = f"{info['dias_restantes']} d"
            sit = info['status']
        else:
            dt_ent = "-"
            qtd_tp = "-"
            auton = "-"
            dt_prev = "-"
            dias_rest = "-"
            sit = "Sem Entrega"

        linhas_tabela.append([
            Paragraph(esc.nome[:32], style_td),
            Paragraph(esc.codigo_inep or '-', style_td_center),
            Paragraph(dt_ent, style_td_center),
            Paragraph(qtd_tp, style_td_center),
            Paragraph(auton, style_td_center),
            Paragraph(dt_prev, style_td_center),
            Paragraph(dias_rest, style_td_center),
            Paragraph(sit, style_td_center),
            Paragraph(f"{total_ano_esc} bot.", style_td_center)
        ])

    # Linha de total
    linhas_tabela.append([
        Paragraph("<b>TOTAL GERAL CONSOLIDADO</b>", style_td),
        Paragraph("-", style_td_center),
        Paragraph("-", style_td_center),
        Paragraph("-", style_td_center),
        Paragraph("-", style_td_center),
        Paragraph("-", style_td_center),
        Paragraph("-", style_td_center),
        Paragraph("-", style_td_center),
        Paragraph(f"<b>{total_botijoes_geral} botijões</b>", style_td_center)
    ])

    t_rel = Table(
        linhas_tabela,
        colWidths=[6.5 * cm, 2.2 * cm, 2.5 * cm, 2.4 * cm, 2.5 * cm, 3.0 * cm, 2.2 * cm, 2.8 * cm, 2.4 * cm]
    )
    t_rel.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), cor_primaria),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8fafc")]),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))

    story.append(t_rel)
    doc.build(story)
    buffer.seek(0)

    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=Relatorio_Consolidado_Gas_{ano}.pdf'
    return response
