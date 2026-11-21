# auditoria_pnae_reports.py
# -*- coding: utf-8 -*-
"""
Geradores de Relatório Analítico Minucioso (PDF Institucional — 12 Seções + Capa + Sumário)
e Planilha Excel Multi-Aba (.xlsx) para o Módulo de Auditoria Analítica PNAE / Agricultura Familiar.
100% em memória (BytesIO) — compatível com Railway (filesystem efêmero) + Supabase.
"""
import io
import os
from xml.sax.saxutils import escape

import qrcode
from flask import current_app
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from auditoria_pnae_engine import formatar_moeda_br
from utils import obter_data_hora_br_str


# Paleta institucional de Controle Interno
COR_NAVY = colors.HexColor("#0b2545")
COR_AZUL = colors.HexColor("#134074")
COR_VERDE_INST = colors.HexColor("#14532d")
COR_FUNDO_CAB = colors.HexColor("#eef4fb")
COR_CINZA_CLARO = colors.HexColor("#f8fafc")
COR_BORDA = colors.HexColor("#cbd5e1")
COR_TEXTO_SUAVE = colors.HexColor("#475569")

CORES_RISCO_BG = {
    "VERMELHO": colors.HexColor("#fee2e2"),
    "LARANJA": colors.HexColor("#ffedd5"),
    "AMARELO": colors.HexColor("#fef9c3"),
    "VERDE": colors.HexColor("#dcfce7"),
    "CINZA": colors.HexColor("#e2e8f0"),
}

CORES_RISCO_TXT = {
    "VERMELHO": "#991b1b",
    "LARANJA": "#9a3412",
    "AMARELO": "#854d0e",
    "VERDE": "#166534",
    "CINZA": "#334155",
}


def _esc(texto):
    return escape(str(texto if texto is not None else "-"))


def _estilos():
    base = getSampleStyleSheet()
    return {
        "capa_orgao": ParagraphStyle("CapaOrgao", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=11, leading=15, alignment=TA_CENTER, textColor=COR_NAVY),
        "capa_sub": ParagraphStyle("CapaSub", parent=base["Normal"], fontName="Helvetica", fontSize=9.5, leading=13, alignment=TA_CENTER, textColor=COR_TEXTO_SUAVE),
        "capa_titulo": ParagraphStyle("CapaTitulo", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=18, leading=23, alignment=TA_CENTER, textColor=COR_NAVY),
        "capa_badge": ParagraphStyle("CapaBadge", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=10, leading=14, alignment=TA_CENTER, textColor=colors.white),
        "h1": ParagraphStyle("AudH1", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=COR_NAVY, spaceBefore=10, spaceAfter=5),
        "h2": ParagraphStyle("AudH2", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9.5, leading=12.5, textColor=COR_AZUL, spaceBefore=6, spaceAfter=3),
        "body": ParagraphStyle("AudBody", parent=base["Normal"], fontName="Helvetica", fontSize=8.5, leading=12, alignment=TA_JUSTIFY, textColor=colors.HexColor("#1e293b")),
        "body_sm": ParagraphStyle("AudBodySm", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, leading=10, alignment=TA_LEFT, textColor=colors.HexColor("#1e293b")),
        "bold_sm": ParagraphStyle("AudBoldSm", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=10, alignment=TA_LEFT, textColor=colors.HexColor("#0f172a")),
        "center_sm": ParagraphStyle("AudCenterSm", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, leading=10, alignment=TA_CENTER, textColor=colors.HexColor("#1e293b")),
        "right_sm": ParagraphStyle("AudRightSm", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, leading=10, alignment=TA_RIGHT, textColor=colors.HexColor("#1e293b")),
        "right_bold": ParagraphStyle("AudRightBold", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=10, alignment=TA_RIGHT, textColor=colors.HexColor("#0f172a")),
        "th": ParagraphStyle("AudTH", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.2, leading=9.5, alignment=TA_CENTER, textColor=colors.white),
        "th_left": ParagraphStyle("AudTHL", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.2, leading=9.5, alignment=TA_LEFT, textColor=colors.white),
        "alerta_critico": ParagraphStyle("AudCrit", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11.5, alignment=TA_LEFT, textColor=colors.HexColor("#991b1b")),
        "alerta_info": ParagraphStyle("AudInfo", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=11, alignment=TA_JUSTIFY, textColor=colors.HexColor("#1e293b")),
    }


def _header_footer_interno(canvas_obj, doc, codigo_relatorio, exercicio, data_emissao_str):
    canvas_obj.saveState()
    w, h = doc.pagesize
    if doc.page > 1:
        # Cabeçalho institucional discreto nas páginas internas
        canvas_obj.setFillColor(COR_NAVY)
        canvas_obj.setFont("Helvetica-Bold", 7.5)
        canvas_obj.drawString(doc.leftMargin, h - 1.05 * cm, "PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ · SEME · AUDITORIA ANALÍTICA PNAE")
        canvas_obj.setFont("Helvetica", 7.5)
        canvas_obj.setFillColor(COR_TEXTO_SUAVE)
        canvas_obj.drawRightString(w - doc.rightMargin, h - 1.05 * cm, f"Exercício {exercicio} | Protocolo: {codigo_relatorio}")
        canvas_obj.setStrokeColor(COR_NAVY)
        canvas_obj.setLineWidth(0.7)
        canvas_obj.line(doc.leftMargin, h - 1.2 * cm, w - doc.rightMargin, h - 1.2 * cm)

    # Rodapé
    canvas_obj.setStrokeColor(COR_BORDA)
    canvas_obj.setLineWidth(0.6)
    canvas_obj.line(doc.leftMargin, 1.35 * cm, w - doc.rightMargin, 1.35 * cm)
    canvas_obj.setFont("Helvetica", 7.2)
    canvas_obj.setFillColor(COR_TEXTO_SUAVE)
    canvas_obj.drawString(
        doc.leftMargin,
        0.95 * cm,
        f"Gestoor360 · Controle Interno PNAE/SEME · Emitido em {data_emissao_str} · Ref: {codigo_relatorio}",
    )
    canvas_obj.drawRightString(w - doc.rightMargin, 0.95 * cm, f"Página {doc.page}")
    canvas_obj.restoreState()


def _badge_html(cor, texto):
    hex_c = CORES_RISCO_TXT.get(cor, "#334155")
    return f"<font color='{hex_c}'><b>{_esc(texto)}</b></font>"


def gerar_pdf_relatorio_auditoria_pnae(
    auditoria,
    codigo_relatorio,
    usuario_emissor,
    modo="completo",
    incluir_anexos=True,
    periodo_texto=None,
):
    """
    Gera o Relatório Analítico de Conformidade da Agricultura Familiar (PNAE) em PDF institucional (BytesIO).
    Modos suportados: 'completo', 'pendencias', 'fornecedor', 'associacoes'.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.4 * cm,
        rightMargin=1.4 * cm,
        topMargin=1.65 * cm,
        bottomMargin=1.8 * cm,
        title=f"Relatorio Analitico PNAE {auditoria['exercicio']} - {codigo_relatorio}",
    )
    est = _estilos()
    larg = A4[0] - 2.8 * cm
    data_emissao_str = obter_data_hora_br_str()
    exercicio = auditoria["exercicio"]
    ind = auditoria["indicadores"]
    pnae45 = auditoria["analise_pnae_45"]
    fornecedores = auditoria["fornecedores"]
    associacoes = auditoria["associacoes"]
    pendencias = auditoria["pendencias"]

    if modo == "pendencias":
        fornecedores_exibir = [f for f in fornecedores if f["pendencias"]]
        titulo_sub = "EXTRATO FOCALIZADO DE PENDÊNCIAS E OCORRÊNCIAS"
    elif modo == "associacoes":
        fornecedores_exibir = [f for f in fornecedores if f["eh_formal"]]
        titulo_sub = "RELATÓRIO ESPECÍFICO DE ASSOCIAÇÕES E COOPERATIVAS (GRUPOS FORMAIS)"
    elif modo == "fornecedor" and len(fornecedores) == 1:
        fornecedores_exibir = fornecedores
        titulo_sub = f"DOSSIÊ ANALÍTICO INDIVIDUAL — {fornecedores[0]['razao_social'].upper()}"
    else:
        fornecedores_exibir = fornecedores
        titulo_sub = "RELATÓRIO ANALÍTICO COMPLETO DE CONFORMIDADE — AGRICULTURA FAMILIAR / PNAE"

    story = []

    # =========================================================================
    # CAPA INSTITUCIONAL
    # =========================================================================
    try:
        timbre_path = os.path.join(current_app.root_path, "static", "timbre.jpg")
        if os.path.exists(timbre_path):
            img = RLImage(timbre_path, width=15.5 * cm, height=2.2 * cm, kind="proportional")
            img.hAlign = "CENTER"
            story.append(img)
            story.append(Spacer(1, 0.6 * cm))
    except Exception:
        story.append(Spacer(1, 0.8 * cm))

    story.append(Paragraph("ESTADO DO PIAUÍ", est["capa_sub"]))
    story.append(Paragraph("PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ", est["capa_orgao"]))
    story.append(Paragraph("SECRETARIA MUNICIPAL DE EDUCAÇÃO (SEME)", est["capa_orgao"]))
    story.append(Paragraph("COORDENAÇÃO DO PROGRAMA NACIONAL DE ALIMENTAÇÃO ESCOLAR (PNAE) / CONTROLE INTERNO", est["capa_sub"]))
    story.append(Spacer(1, 1.5 * cm))
    story.append(HRFlowable(width="100%", thickness=2.2, color=COR_NAVY, spaceAfter=18))

    faixa_tit = Table(
        [[Paragraph("RELATÓRIO ANALÍTICO DE CONFORMIDADE<br/>DA AGRICULTURA FAMILIAR — PNAE", est["capa_titulo"])]],
        colWidths=[larg],
    )
    faixa_tit.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COR_FUNDO_CAB),
                ("BOX", (0, 0), (-1, -1), 1.0, COR_NAVY),
                ("TOPPADDING", (0, 0), (-1, -1), 18),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
            ]
        )
    )
    story.append(faixa_tit)
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(f"<b>{_esc(titulo_sub)}</b>", est["capa_sub"]))
    story.append(Spacer(1, 1.2 * cm))

    # Ficha técnica da Capa + QR Code de rastreabilidade
    qr_img = qrcode.make(f"SEME-PNAE-AUD|{codigo_relatorio}|EX:{exercicio}|{data_emissao_str}")
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_buf.seek(0)

    ficha_capa = [
        [Paragraph("<b>Exercício Financeiro Analisado:</b>", est["bold_sm"]), Paragraph(f"<b>{exercicio}</b>" + (f" ({_esc(periodo_texto)})" if periodo_texto else ""), est["body_sm"])],
        [Paragraph("<b>Protocolo de Rastreabilidade:</b>", est["bold_sm"]), Paragraph(f"<b>{_esc(codigo_relatorio)}</b>", est["body_sm"])],
        [Paragraph("<b>Data e Hora da Geração:</b>", est["bold_sm"]), Paragraph(_esc(data_emissao_str), est["body_sm"])],
        [Paragraph("<b>Responsável pela Emissão:</b>", est["bold_sm"]), Paragraph(_esc(usuario_emissor), est["body_sm"])],
        [Paragraph("<b>Base Normativa Aplicada:</b>", est["bold_sm"]), Paragraph(f"Lei nº 11.947/2009 (art. 14), Lei nº 15.226/2025, Res. CD/FNDE nº 4/2026 e Res. CD/FNDE nº 17/2026 (v. {_esc(auditoria['versao_normativa'])})", est["body_sm"])],
        [Paragraph("<b>Abrangência Analisada:</b>", est["bold_sm"]), Paragraph(f"{ind['total_fornecedores']} fornecedor(es) · {ind['qtd_pendencias_total']} ocorrência(s) mapeada(s) · {ind['qtd_ocorrencias_criticas']} crítica(s)", est["body_sm"])],
    ]
    t_ficha = Table(ficha_capa, colWidths=[5.2 * cm, larg - 8.2 * cm])
    t_ficha.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, COR_BORDA),
                ("BOX", (0, 0), (-1, -1), 0.6, COR_NAVY),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    t_capa_bloco = Table([[t_ficha, RLImage(qr_buf, width=2.7 * cm, height=2.7 * cm)]], colWidths=[larg - 3.0 * cm, 3.0 * cm])
    t_capa_bloco.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "CENTER")]))
    story.append(t_capa_bloco)
    story.append(Spacer(1, 1.8 * cm))

    aviso_capa = Paragraph(
        "<i>Documento técnico de controle interno elaborado em modo somente leitura sobre a base oficial do Gestoor360. "
        "As constatações aqui apresentadas possuem natureza preventiva e analítica, não constituindo juízo definitivo de "
        "irregularidade ou responsabilização pessoal, devendo subsidiar a conferência documental pela SEME, CAE e Controle Interno.</i>",
        est["capa_sub"],
    )
    story.append(aviso_capa)
    story.append(PageBreak())

    # =========================================================================
    # SUMÁRIO
    # =========================================================================
    story.append(Paragraph("SUMÁRIO DO RELATÓRIO ANALÍTICO", est["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.0, color=COR_NAVY, spaceAfter=6))
    secoes_sumario = [
        ("1.", "Objetivo, Abrangência e Metodologia de Verificação"),
        ("2.", "Fundamentação Legal e Base Normativa Aplicada"),
        ("3.", "Resumo Executivo e Indicadores Consolidados"),
        ("4.", "Análise Individual dos Fornecedores (Agricultores e Organizações)"),
        ("5.", "Quadro Geral de Pendências e Classificação de Risco"),
        ("6.", "Análise Específica das Associações e Cooperativas (VMC = NAF × R$ 40.000,00)"),
        ("7.", "Análise Documental (Checklist por Fornecedor e Modalidade)"),
        ("8.", "Análise Financeira e Contratual (Contratado × Executado × Faturado × Pago)"),
        ("9.", "Análise do Percentual de Agricultura Familiar (Meta Normativa de 45% — Art. 29)"),
        ("10.", "Plano de Providências e Registro de Justificativas Administrativas"),
        ("11.", "Conclusão Técnica de Controle Interno"),
        ("12.", "Anexos — Demonstrativos, Memória de Cálculo e Registros de Origem"),
    ]
    t_sum = Table(
        [[Paragraph(f"<b>{num}</b>", est["bold_sm"]), Paragraph(desc, est["body_sm"])] for num, desc in secoes_sumario],
        colWidths=[1.0 * cm, larg - 1.0 * cm],
    )
    t_sum.setStyle(
        TableStyle(
            [
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, COR_CINZA_CLARO]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, COR_BORDA),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ]
        )
    )
    story.append(t_sum)
    story.append(Spacer(1, 0.5 * cm))

    # =========================================================================
    # 1. OBJETIVO
    # =========================================================================
    story.append(Paragraph("1. OBJETIVO, ABRANGÊNCIA E METODOLOGIA", est["h1"]))
    story.append(
        Paragraph(
            f"O presente Relatório Analítico tem por objetivo apresentar o diagnóstico técnico e preventivo da conformidade "
            f"das contratações, entregas, documentos de habilitação e execução financeira da <b>Agricultura Familiar no âmbito do PNAE</b> "
            f"no Município de Valença do Piauí – PI, referente ao exercício financeiro de <b>{exercicio}</b>. "
            f"A análise foi executada em modo exclusivamente de leitura sobre os registros existentes no sistema Gestoor360, "
            f"consolidando contratos, itens de projeto de venda, entregas aprovadas, notas fiscais, documentos anexados e "
            f"registros complementares de pagamentos e associados, sem alterar quaisquer dados financeiros ou contratuais.",
            est["body"],
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "<b>Critério Metodológico:</b> Cada verificação baseia-se em regra normativa rastreável, separando expressamente "
            "<i>Valor Contratado</i>, <i>Valor Executado</i> (entregas aprovadas), <i>Valor Faturado</i> (entregas com nota fiscal) e "
            "<i>Valor Pago</i> (ordens bancárias registradas). Na ausência de dados suficientes no sistema para certificar determinado "
            "requisito, o relatório classifica a situação como <b>Informação Insuficiente / Pendência de Conferência</b>, sem presumir "
            "irregularidade ou fraude.",
            est["body"],
        )
    )

    # =========================================================================
    # 2. FUNDAMENTAÇÃO LEGAL
    # =========================================================================
    story.append(Paragraph("2. FUNDAMENTAÇÃO LEGAL E BASE NORMATIVA", est["h1"]))
    rows_norm = [
        [Paragraph("Cód.", est["th"]), Paragraph("Exigência Normativa", est["th_left"]), Paragraph("Legislação / Dispositivo", est["th_left"]), Paragraph("Grav.", est["th"])]
    ]
    for r in auditoria["regras_normativas"]:
        rows_norm.append(
            [
                Paragraph(_esc(r.codigo), est["center_sm"]),
                Paragraph(f"<b>{_esc(r.nome_exigencia)}</b><br/><font size='6.8' color='#475569'>{_esc(r.descricao_obrigacao[:210])}</font>", est["body_sm"]),
                Paragraph(f"{_esc(r.numero_legislacao)}<br/><b>{_esc(r.dispositivo)}</b>", est["body_sm"]),
                Paragraph(_esc(r.gravidade), est["center_sm"]),
            ]
        )
    t_norm = Table(rows_norm, colWidths=[2.1 * cm, 8.7 * cm, 5.7 * cm, 1.7 * cm], repeatRows=1)
    t_norm.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), COR_NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]),
                ("GRID", (0, 0), (-1, -1), 0.3, COR_BORDA),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ]
        )
    )
    story.append(t_norm)

    # =========================================================================
    # 3. RESUMO EXECUTIVO
    # =========================================================================
    story.append(Paragraph("3. RESUMO EXECUTIVO", est["h1"]))
    kpi_rows = [
        [
            Paragraph(f"<font size='7' color='#475569'>Fornecedores Analisados</font><br/><font size='11' color='#0b2545'><b>{ind['total_fornecedores']}</b></font><br/><font size='6.5'>{ind['total_individuais']} Ind. · {ind['total_associacoes']} Assoc. · {ind['total_grupos_informais']} Inf.</font>", est["center_sm"]),
            Paragraph(f"<font size='7' color='#475569'>Total Contratado</font><br/><font size='10' color='#0b2545'><b>{formatar_moeda_br(ind['total_contratado'])}</b></font>", est["center_sm"]),
            Paragraph(f"<font size='7' color='#475569'>Total Executado (Entregue)</font><br/><font size='10' color='#14532d'><b>{formatar_moeda_br(ind['total_executado'])}</b></font>", est["center_sm"]),
            Paragraph(f"<font size='7' color='#475569'>Total Faturado / Pago</font><br/><font size='8.5' color='#134074'><b>Fat: {formatar_moeda_br(ind['total_faturado'])}</b></font><br/><font size='8'>Pago: {formatar_moeda_br(ind['total_pago'])}</font>", est["center_sm"]),
        ],
        [
            Paragraph(f"<font size='7' color='#166534'>Regulares (Verde)</font><br/><font size='11' color='#166534'><b>{ind['qtd_regulares']}</b></font>", est["center_sm"]),
            Paragraph(f"<font size='7' color='#854d0e'>Em Atenção / Pendência</font><br/><font size='11' color='#854d0e'><b>{ind['qtd_atencao'] + ind['qtd_laranja'] + ind['qtd_cinza']}</b></font>", est["center_sm"]),
            Paragraph(f"<font size='7' color='#991b1b'>Ocorrências Críticas</font><br/><font size='11' color='#991b1b'><b>{ind['qtd_ocorrencias_criticas']}</b></font><br/><font size='6.5'>{ind['qtd_limites_ultrapassados']} limite(s) ultrapassado(s)</font>", est["center_sm"]),
            Paragraph(f"<font size='7' color='#475569'>Meta PNAE ({pnae45['meta_percentual']:.0f}%)</font><br/><font size='10' color='#0b2545'><b>{pnae45['percentual_elegivel']:.2f}%</b></font><br/><font size='6.5'>{_esc(pnae45['status_texto'][:28])}</font>", est["center_sm"]),
        ],
    ]
    t_kpi = Table(kpi_rows, colWidths=[larg / 4] * 4)
    t_kpi.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
                ("GRID", (0, 0), (-1, -1), 0.5, COR_BORDA),
                ("BOX", (0, 0), (-1, -1), 0.8, COR_NAVY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(t_kpi)

    comp = auditoria.get("comparativo")
    if comp:
        story.append(Spacer(1, 5))
        story.append(
            Paragraph(
                f"<b>Comparativo entre Exercícios ({comp['ano_anterior']} × {exercicio}):</b> "
                f"No exercício de {comp['ano_anterior']}, o valor contratado foi de {formatar_moeda_br(comp['contratado_anterior'])} "
                f"e o executado de {formatar_moeda_br(comp['executado_anterior'])} ({comp['percentual_anterior']:.2f}% do repasse anterior). "
                f"Em {exercicio}, registra-se variação de <b>{formatar_moeda_br(comp['variacao_executado'])}</b> na execução acumulada.",
                est["body"],
            )
        )

    # =========================================================================
    # 4. ANÁLISE INDIVIDUAL DOS FORNECEDORES
    # =========================================================================
    story.append(PageBreak())
    story.append(Paragraph("4. ANÁLISE INDIVIDUAL DOS FORNECEDORES", est["h1"]))
    story.append(
        Paragraph(
            f"Apresentam-se abaixo os dossiês individuais de <b>{len(fornecedores_exibir)} fornecedor(es)</b> abrangidos "
            f"nesta auditoria, com memória de cálculo do limite normativo, separação entre valores contratados, executados, "
            f"faturados e pagos, constatações e recomendações.",
            est["body"],
        )
    )
    story.append(Spacer(1, 4))

    if not fornecedores_exibir:
        story.append(Paragraph("Nenhum fornecedor enquadrado nos filtros selecionados.", est["body"]))

    for f in fornecedores_exibir:
        bloco = []
        cor_st = f["situacao_global"]
        bg_cab = CORES_RISCO_BG.get(cor_st, COR_FUNDO_CAB)
        lim_txt = formatar_moeda_br(f["limite_aplicavel"]) if f["limite_aplicavel"] is not None else "Não verificável (sem NAF/CAF)"

        hdr_forn = Table(
            [
                [
                    Paragraph(f"<b>FORNECEDOR: {_esc(f['razao_social'].upper())}</b>", est["bold_sm"]),
                    Paragraph(_badge_html(cor_st, f"SITUAÇÃO: {f['situacao_global_label']}"), est["right_bold"]),
                ]
            ],
            colWidths=[larg * 0.68, larg * 0.32],
        )
        hdr_forn.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), bg_cab),
                    ("BOX", (0, 0), (-1, -1), 0.7, COR_NAVY),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        bloco.append(hdr_forn)

        dados_f = [
            [
                Paragraph(f"<b>CPF/CNPJ:</b> {_esc(f['cpf_cnpj'])}", est["body_sm"]),
                Paragraph(f"<b>CAF/DAP:</b> {_esc(f['caf'])}", est["body_sm"]),
                Paragraph(f"<b>TIPO:</b> {_esc(f['tipo_fornecedor'])}", est["body_sm"]),
            ],
            [
                Paragraph(f"<b>VALOR CONTRATADO:</b> {formatar_moeda_br(f['valor_contratado'])}", est["body_sm"]),
                Paragraph(f"<b>VALOR EXECUTADO:</b> <b>{formatar_moeda_br(f['valor_executado'])}</b>", est["body_sm"]),
                Paragraph(f"<b>FATURADO / PAGO:</b> {formatar_moeda_br(f['valor_faturado'])} / {formatar_moeda_br(f['valor_pago']) if f['dados_pagamento_disponiveis'] else 'N/I'}", est["body_sm"]),
            ],
            [
                Paragraph(f"<b>LIMITE NORMATIVO:</b> {lim_txt}", est["body_sm"]),
                Paragraph(f"<b>EXCEDENTE EXECUTADO:</b> {_badge_html('VERMELHO' if f['excedente_executado'] > 0 else 'VERDE', formatar_moeda_br(f['excedente_executado']))}", est["body_sm"]),
                Paragraph(f"<b>CONTRATOS / ENTREGAS:</b> {f['qtd_contratos']} contrato(s) · {f['qtd_entregas']} entrega(s)", est["body_sm"]),
            ],
        ]
        t_df = Table(dados_f, colWidths=[larg / 3] * 3)
        t_df.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.3, COR_BORDA),
                    ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        bloco.append(t_df)

        # Destaque visual VERMELHO para pendências críticas + análise técnica e recomendação
        if f["pendencias"]:
            for p in f["pendencias"]:
                bg_p = CORES_RISCO_BG.get(p["cor"], COR_CINZA_CLARO)
                borda_p = colors.HexColor("#dc2626") if p["cor"] == "VERMELHO" else (colors.HexColor("#ea580c") if p["cor"] == "LARANJA" else COR_BORDA)
                etiqueta_p = f"[{p['cor']} — {p['categoria'].upper()}]"
                txt_p = (
                    f"{_badge_html(p['cor'], etiqueta_p)} {_esc(p['o_que_foi_identificado'])}<br/>"
                    f"<b>Fundamentação:</b> {_esc(p['dispositivo_normativo'])}<br/>"
                    f"<b>Análise Técnica / Cálculo:</b> {_esc(p['calculo_realizado'])} | <i>Origem:</i> {_esc(p['dados_origem'])}<br/>"
                    f"<b>Recomendação:</b> {_esc(p['providencia_recomendada'])}"
                )
                if p.get("justificativa"):
                    txt_p += f"<br/><b>Justificativa Administrativa ({_esc(p['status_resolucao'])}):</b> {_esc(p['justificativa'].justificativa_administrativa)}"
                t_al = Table([[Paragraph(txt_p, est["body_sm"])]], colWidths=[larg])
                t_al.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), bg_p),
                            ("BOX", (0, 0), (-1, -1), 0.8 if p["cor"] == "VERMELHO" else 0.4, borda_p),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                            ("LEFTPADDING", (0, 0), (-1, -1), 6),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ]
                    )
                )
                bloco.append(t_al)
        else:
            t_ok = Table(
                [[Paragraph(_badge_html("VERDE", "CONFORMIDADE VERIFICADA NOS DADOS CADASTRADOS: ") + "Nenhuma inconsistência de limite ou financeira detectada nos registros deste fornecedor no exercício.", est["body_sm"])]],
                colWidths=[larg],
            )
            t_ok.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CORES_RISCO_BG["VERDE"]), ("BOX", (0, 0), (-1, -1), 0.4, COR_BORDA), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
            bloco.append(t_ok)

        bloco.append(Spacer(1, 7))
        story.append(KeepTogether(bloco[:3]))
        for elem_extra in bloco[3:]:
            story.append(elem_extra)

    # =========================================================================
    # 5. QUADRO GERAL DE PENDÊNCIAS
    # =========================================================================
    story.append(PageBreak())
    story.append(Paragraph("5. QUADRO GERAL DE PENDÊNCIAS E CLASSIFICAÇÃO DE RISCO", est["h1"]))
    rows_pend = [
        [
            Paragraph("Risco", est["th"]),
            Paragraph("Fornecedor / CPF-CNPJ / CAF", est["th_left"]),
            Paragraph("Contrat. / Exec. / Limite", est["th"]),
            Paragraph("Descrição da Pendência e Fundamentação Legal", est["th_left"]),
            Paragraph("Providência Recomendada", est["th_left"]),
        ]
    ]
    mapa_f = {f["agricultor_id"]: f for f in fornecedores}
    estilo_p = [
        ("BACKGROUND", (0, 0), (-1, 0), COR_NAVY),
        ("GRID", (0, 0), (-1, -1), 0.3, COR_BORDA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for idx, p in enumerate(pendencias, start=1):
        f_ref = mapa_f.get(p["agricultor_id"])
        vals_txt = "-"
        if f_ref:
            lim_s = formatar_moeda_br(f_ref["limite_aplicavel"]) if f_ref["limite_aplicavel"] is not None else "N/V"
            vals_txt = (
                f"C: {formatar_moeda_br(f_ref['valor_contratado'])}<br/>"
                f"E: {formatar_moeda_br(f_ref['valor_executado'])}<br/>"
                f"L: {lim_s}"
                + (f"<br/><b>Exc: {formatar_moeda_br(f_ref['excedente_executado'])}</b>" if f_ref["excedente_executado"] > 0 else "")
            )
        rows_pend.append(
            [
                Paragraph(_badge_html(p["cor"], f"{p['cor']}<br/>{p['grau_risco']}"), est["center_sm"]),
                Paragraph(f"<b>{_esc(p['fornecedor_nome'])}</b><br/>{_esc(p['cpf_cnpj'])}<br/>CAF: {_esc(p['caf'])}<br/><i>{_esc(p['tipo_fornecedor'])}</i>", est["body_sm"]),
                Paragraph(vals_txt, est["body_sm"]),
                Paragraph(f"<b>{_esc(p['categoria'])}:</b> {_esc(p['o_que_foi_identificado'])}<br/><font size='6.8' color='#475569'>Base: {_esc(p['dispositivo_normativo'])}</font>", est["body_sm"]),
                Paragraph(f"{_esc(p['providencia_recomendada'])}<br/><i>Status: {_esc(p['status_resolucao'])}</i>", est["body_sm"]),
            ]
        )
        estilo_p.append(("BACKGROUND", (0, idx), (0, idx), CORES_RISCO_BG.get(p["cor"], colors.white)))

    if len(rows_pend) == 1:
        rows_pend.append([Paragraph("VERDE", est["center_sm"]), Paragraph("Geral", est["body_sm"]), Paragraph("-", est["center_sm"]), Paragraph("Nenhuma pendência identificada nos registros analisados.", est["body_sm"]), Paragraph("Manter monitoramento periódico.", est["body_sm"])])

    t_pend = Table(rows_pend, colWidths=[1.8 * cm, 3.7 * cm, 2.9 * cm, 5.7 * cm, 4.1 * cm], repeatRows=1)
    t_pend.setStyle(TableStyle(estilo_p))
    story.append(t_pend)

    # =========================================================================
    # 6. ANÁLISE DAS ASSOCIAÇÕES E COOPERATIVAS (VMC = NAF × R$ 40.000,00)
    # =========================================================================
    story.append(Paragraph("6. ANÁLISE DAS ASSOCIAÇÕES E COOPERATIVAS (GRUPOS FORMAIS)", est["h1"]))
    story.append(
        Paragraph(
            "Conforme o art. 38 da Resolução CD/FNDE nº 4/2026 (alterada pela Res. nº 17/2026), o Valor Máximo de Comercialização (VMC) "
            "para organizações detentoras de CAF Pessoa Jurídica é calculado por <b>VMC = NAF × R$ 40.000,00</b>, onde NAF corresponde "
            "estritamente aos agricultores familiares elegíveis com CAF PF ativo, inscritos no CAF PJ, integrantes do Projeto de Venda e "
            "com produção própria. Compete à associação o controle do limite individual de cada associado e à Entidade Executora o controle do limite total contratado.",
            est["body"],
        )
    )
    story.append(Spacer(1, 4))
    if associacoes:
        rows_ass = [
            [Paragraph("Associação / Cooperativa", est["th_left"]), Paragraph("CNPJ / CAF PJ", est["th_left"]), Paragraph("Assoc. / NAF", est["th"]), Paragraph("VMC Calculado", est["th"]), Paragraph("Contrat. / Exec.", est["th"]), Paragraph("Situação", est["th"])]
        ]
        for a in associacoes:
            rows_ass.append(
                [
                    Paragraph(f"<b>{_esc(a['razao_social'])}</b>", est["body_sm"]),
                    Paragraph(f"{_esc(a['cnpj'])}<br/>CAF PJ: {_esc(a['caf_pj'])}", est["body_sm"]),
                    Paragraph(f"Cad: {a['total_associados']}<br/><b>NAF: {a['naf_elegiveis']}</b>", est["center_sm"]),
                    Paragraph(formatar_moeda_br(a["vmc_calculado"]) if a["vmc_calculado"] is not None else "Não verificável", est["right_bold"]),
                    Paragraph(f"C: {formatar_moeda_br(a['valor_contratado'])}<br/>E: {formatar_moeda_br(a['valor_executado'])}", est["right_sm"]),
                    Paragraph(_badge_html(a["status_limite"], a["label_limite"]), est["center_sm"]),
                ]
            )
        t_ass = Table(rows_ass, colWidths=[4.5 * cm, 3.6 * cm, 2.0 * cm, 2.7 * cm, 3.0 * cm, 2.4 * cm], repeatRows=1)
        t_ass.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), COR_NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]), ("GRID", (0, 0), (-1, -1), 0.3, COR_BORDA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(t_ass)
    else:
        story.append(Paragraph("Não há associações ou cooperativas (Grupos Formais) no escopo filtrado deste relatório.", est["body"]))

    # =========================================================================
    # 7. ANÁLISE DOCUMENTAL
    # =========================================================================
    story.append(Paragraph("7. ANÁLISE DOCUMENTAL POR FORNECEDOR", est["h1"]))
    story.append(
        Paragraph(
            "A tabela abaixo sintetiza a situação do checklist documental de 12 itens exigidos conforme a modalidade de participação. "
            "<i>Ressalva metodológica:</i> a ausência de arquivo digitalizado no sistema é tratada como pendência de conferência ou "
            "ausência digital, não presumindo irregularidade material sem prévia verificação da pasta física da Chamada Pública.",
            est["body"],
        )
    )
    story.append(Spacer(1, 4))
    rows_doc = [
        [Paragraph("Fornecedor", est["th_left"]), Paragraph("Regulares", est["th"]), Paragraph("Ausentes", est["th"]), Paragraph("Vencidos", est["th"]), Paragraph("Pend. Conf.", est["th"]), Paragraph("Principais Documentos Ausentes / Vencidos", est["th_left"])]
    ]
    for f in fornecedores_exibir:
        prob_docs = [d["nome_documento"] for d in f["checklist_docs"] if d["situacao"] in ("DOCUMENTO AUSENTE", "DOCUMENTO VENCIDO")]
        rows_doc.append(
            [
                Paragraph(f"<b>{_esc(f['razao_social'])}</b>", est["body_sm"]),
                Paragraph(str(f["qtd_docs_regulares"]), est["center_sm"]),
                Paragraph(_badge_html("VERMELHO" if f["qtd_docs_ausentes"] else "VERDE", str(f["qtd_docs_ausentes"])), est["center_sm"]),
                Paragraph(_badge_html("LARANJA" if f["qtd_docs_vencidos"] else "VERDE", str(f["qtd_docs_vencidos"])), est["center_sm"]),
                Paragraph(str(f["qtd_docs_pendentes"]), est["center_sm"]),
                Paragraph(_esc(", ".join(prob_docs) if prob_docs else "Sem documentos ausentes ou vencidos"), est["body_sm"]),
            ]
        )
    t_doc = Table(rows_doc, colWidths=[4.7 * cm, 1.5 * cm, 1.5 * cm, 1.5 * cm, 1.7 * cm, 7.3 * cm], repeatRows=1)
    t_doc.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), COR_NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]), ("GRID", (0, 0), (-1, -1), 0.3, COR_BORDA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
    story.append(t_doc)

    # =========================================================================
    # 8. ANÁLISE FINANCEIRA E CONTRATUAL
    # =========================================================================
    story.append(Paragraph("8. ANÁLISE FINANCEIRA E CONTRATUAL", est["h1"]))
    rows_fin = [
        [Paragraph("Fornecedor", est["th_left"]), Paragraph("Contratado", est["th"]), Paragraph("Executado", est["th"]), Paragraph("Faturado (NF)", est["th"]), Paragraph("Pago (OB)", est["th"]), Paragraph("Saldo Contr.", est["th"])]
    ]
    for f in fornecedores_exibir:
        rows_fin.append(
            [
                Paragraph(f"<b>{_esc(f['razao_social'])}</b>", est["body_sm"]),
                Paragraph(formatar_moeda_br(f["valor_contratado"]), est["right_sm"]),
                Paragraph(formatar_moeda_br(f["valor_executado"]), est["right_bold"]),
                Paragraph(formatar_moeda_br(f["valor_faturado"]), est["right_sm"]),
                Paragraph(formatar_moeda_br(f["valor_pago"]) if f["dados_pagamento_disponiveis"] else "N/I", est["right_sm"]),
                Paragraph(_badge_html("VERMELHO" if f["saldo_contratual"] < 0 else "VERDE", formatar_moeda_br(f["saldo_contratual"])), est["right_bold"]),
            ]
        )
    rows_fin.append(
        [
            Paragraph("<b>TOTAL CONSOLIDADO</b>", est["bold_sm"]),
            Paragraph(f"<b>{formatar_moeda_br(ind['total_contratado'])}</b>", est["right_bold"]),
            Paragraph(f"<b>{formatar_moeda_br(ind['total_executado'])}</b>", est["right_bold"]),
            Paragraph(f"<b>{formatar_moeda_br(ind['total_faturado'])}</b>", est["right_bold"]),
            Paragraph(f"<b>{formatar_moeda_br(ind['total_pago'])}</b>", est["right_bold"]),
            Paragraph(f"<b>{formatar_moeda_br(ind['total_contratado'] - ind['total_executado'])}</b>", est["right_bold"]),
        ]
    )
    t_fin = Table(rows_fin, colWidths=[6.2 * cm, 2.4 * cm, 2.4 * cm, 2.4 * cm, 2.4 * cm, 2.4 * cm], repeatRows=1)
    t_fin.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), COR_NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, COR_CINZA_CLARO]), ("BACKGROUND", (0, -1), (-1, -1), COR_FUNDO_CAB), ("GRID", (0, 0), (-1, -1), 0.3, COR_BORDA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
    story.append(t_fin)

    # =========================================================================
    # 9. ANÁLISE DO PERCENTUAL DE AGRICULTURA FAMILIAR (45%)
    # =========================================================================
    story.append(Paragraph(f"9. ANÁLISE DO PERCENTUAL DE AGRICULTURA FAMILIAR (META {pnae45['meta_percentual']:.0f}% — ART. 29)", est["h1"]))
    mem_45 = [
        [Paragraph("<b>Parâmetro de Cálculo (Art. 14 Lei nº 11.947/2009 · Art. 29 Res. CD/FNDE nº 4/2026 c/c Res. nº 17/2026)</b>", est["bold_sm"]), Paragraph("<b>Valor / Resultado</b>", est["right_bold"])],
        [Paragraph("Total de Recursos Federais Repassados pelo FNDE no Exercício (ConfiguracaoPNAE)", est["body_sm"]), Paragraph(formatar_moeda_br(pnae45["repasse_federal"]), est["right_bold"])],
        [Paragraph(f"Obrigação Legal Mínima ({pnae45['meta_percentual']:.0f}% sobre o repasse federal)", est["body_sm"]), Paragraph(formatar_moeda_br(pnae45["valor_meta_minima"]), est["right_bold"])],
        [Paragraph("Aquisições Executadas Elegíveis (Entregas Aprovadas de Fornecedores com CAF)", est["body_sm"]), Paragraph(formatar_moeda_br(pnae45["total_executado_elegivel"]), est["right_bold"])],
        [Paragraph("Aquisições Executadas em Cadastros sem CAF Informado (Pendentes de Regularização)", est["body_sm"]), Paragraph(formatar_moeda_br(pnae45["total_executado_sem_caf"]), est["right_sm"])],
        [Paragraph("Percentual Elegível Alcançado sobre o Repasse Federal", est["body_sm"]), Paragraph(_badge_html("VERDE" if pnae45["atingiu_meta"] else ("CINZA" if not pnae45["repasse_federal_configurado"] else "VERMELHO"), f"{pnae45['percentual_elegivel']:.2f}%"), est["right_bold"])],
        [Paragraph("Saldo Faltante para Atingimento da Meta Mínima", est["body_sm"]), Paragraph(formatar_moeda_br(pnae45["falta_para_meta"]), est["right_bold"])],
        [Paragraph("Cota de Inclusão Produtiva de Mulheres Agricultoras (50% por UFPA — Art. 29, § 3º)", est["body_sm"]), Paragraph(f"{pnae45['pct_mulheres_informado']:.2f}% (sobre pagamentos registrados)" if pnae45["pct_mulheres_informado"] is not None else "Informação insuficiente — conferir NFs na pasta física", est["right_sm"])],
    ]
    t_45 = Table(mem_45, colWidths=[larg - 4.5 * cm, 4.5 * cm])
    t_45.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), COR_FUNDO_CAB), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]), ("GRID", (0, 0), (-1, -1), 0.35, COR_BORDA), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(t_45)

    # =========================================================================
    # 10. PLANO DE PROVIDÊNCIAS
    # =========================================================================
    story.append(Paragraph("10. PLANO DE PROVIDÊNCIAS ADMINISTRATIVAS", est["h1"]))
    rows_pp = [
        [Paragraph("Prior.", est["th"]), Paragraph("Fornecedor", est["th_left"]), Paragraph("Problema Identificado", est["th_left"]), Paragraph("Providência Necessária", est["th_left"]), Paragraph("Setor / Prazo / Situação", est["th_left"])]
    ]
    for p in pendencias[:60]:
        rows_pp.append(
            [
                Paragraph(_badge_html(p["cor"], p["grau_risco"]), est["center_sm"]),
                Paragraph(_esc(p["fornecedor_nome"]), est["body_sm"]),
                Paragraph(_esc(p["o_que_foi_identificado"]), est["body_sm"]),
                Paragraph(_esc(p["providencia_recomendada"]), est["body_sm"]),
                Paragraph(f"<b>Setor:</b> {_esc(p['setor_responsavel'])}<br/><b>Prazo:</b> {_esc(p['prazo_sugerido'])}<br/><b>Sit.:</b> {_esc(p['status_resolucao'])}", est["body_sm"]),
            ]
        )
    if len(rows_pp) == 1:
        rows_pp.append([Paragraph("BAIXA", est["center_sm"]), Paragraph("Geral", est["body_sm"]), Paragraph("Sem ocorrências abertas.", est["body_sm"]), Paragraph("Manter rotina de conferência mensal.", est["body_sm"]), Paragraph("PNAE / Contínuo / Regular", est["body_sm"])])

    t_pp = Table(rows_pp, colWidths=[1.6 * cm, 3.3 * cm, 4.5 * cm, 5.2 * cm, 3.6 * cm], repeatRows=1)
    t_pp.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), COR_NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]), ("GRID", (0, 0), (-1, -1), 0.3, COR_BORDA), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]))
    story.append(t_pp)

    # =========================================================================
    # 11. CONCLUSÃO TÉCNICA
    # =========================================================================
    story.append(Paragraph("11. CONCLUSÃO TÉCNICA DE CONTROLE INTERNO", est["h1"]))
    story.append(
        Paragraph(
            f"Com base na análise analítica realizada sobre os registros do exercício de <b>{exercicio}</b> no módulo de Agricultura Familiar/PNAE "
            f"do sistema Gestoor360, sintetiza-se tecnicamente o quadro de conformidade observado, diferenciando-se expressamente a natureza "
            f"de cada achado, sem emissão de juízo de valor sobre conduta individual ou caracterização automática de irregularidade definitiva:",
            est["body"],
        )
    )
    story.append(Spacer(1, 4))
    itens_conclusao = [
        f"<b>Conformidade Comprovada nos Dados Cadastrados:</b> {ind['qtd_regulares']} fornecedor(es) apresentam-se integralmente regulares quanto aos limites e registros financeiros analisados.",
        f"<b>Indícios Relevantes de Desconformidade Normativa (Críticos):</b> {ind['qtd_ocorrencias_criticas']} ocorrência(s) crítica(s), incluindo {ind['qtd_limites_ultrapassados']} fornecedor(es) com indicação de extrapolação do limite normativo de comercialização, que demandam conferência prioritária de notas fiscais, CAF e entregas.",
        f"<b>Inconsistências Financeiras e Contratuais:</b> {ind['qtd_inconsistencias_financeiras']} apontamento(s) envolvendo saldos negativos, execução fora de vigência, entregas sem nota fiscal vinculada ou possíveis duplicidades.",
        f"<b>Pendências Documentais:</b> {ind['qtd_pendencias_documentais']} alerta(s) relacionados à ausência ou vencimento de documentos no prontuário digital, sujeitos à validação na pasta física da Chamada Pública.",
        f"<b>Situações Não Verificáveis por Falta de Dados:</b> {ind['qtd_cinza']} fornecedor(es) classificados em cinza (dados insuficientes — ex.: ausência de CAF/CPF ou relação de associados em grupos formais) e pendência de integração/lançamento de ordens bancárias quando o valor pago registrado for zero.",
    ]
    for ic in itens_conclusao:
        story.append(Paragraph(f"• {ic}", est["body"]))
    story.append(Spacer(1, 14))

    t_ass = Table(
        [
            [
                Paragraph(f"__________________________________________<br/><b>{_esc(usuario_emissor)}</b><br/>Responsável pela Emissão Técnica", est["center_sm"]),
                Paragraph("__________________________________________<br/><b>Coordenação do PNAE / SEME</b><br/>Ciência e Encaminhamento", est["center_sm"]),
                Paragraph("__________________________________________<br/><b>Controle Interno / CAE</b><br/>Recebimento / Análise", est["center_sm"]),
            ]
        ],
        colWidths=[larg / 3] * 3,
    )
    story.append(KeepTogether([t_ass]))

    # =========================================================================
    # 12. ANEXOS — DEMONSTRATIVOS E MEMÓRIA DE CÁLCULO
    # =========================================================================
    if incluir_anexos:
        story.append(PageBreak())
        story.append(Paragraph("12. ANEXOS — DEMONSTRATIVO DE CONTRATOS E REGISTROS DE ORIGEM", est["h1"]))
        rows_anx = [
            [Paragraph("Contrato", est["th"]), Paragraph("Chamada Pública", est["th"]), Paragraph("Fornecedor", est["th_left"]), Paragraph("Vigência", est["th"]), Paragraph("Contratado", est["th"]), Paragraph("Executado", est["th"]), Paragraph("Entregas", est["th"])]
        ]
        for f in fornecedores_exibir:
            for ct in f["contratos"]:
                exec_ct = sum(float(e.valor_total or 0.0) for e in f["entregas"] if e.contrato_id == ct.id)
                qtd_ent_ct = sum(1 for e in f["entregas"] if e.contrato_id == ct.id)
                vig = f"{ct.data_inicio.strftime('%d/%m/%Y') if ct.data_inicio else '?'} a {ct.data_termino.strftime('%d/%m/%Y') if ct.data_termino else '?'}"
                rows_anx.append(
                    [
                        Paragraph(_esc(ct.numero_contrato), est["center_sm"]),
                        Paragraph(_esc(ct.chamada_publica or "N/I"), est["center_sm"]),
                        Paragraph(_esc(f["razao_social"]), est["body_sm"]),
                        Paragraph(vig, est["center_sm"]),
                        Paragraph(formatar_moeda_br(ct.valor_total), est["right_sm"]),
                        Paragraph(formatar_moeda_br(exec_ct), est["right_bold"]),
                        Paragraph(str(qtd_ent_ct), est["center_sm"]),
                    ]
                )
        if len(rows_anx) == 1:
            rows_anx.append([Paragraph("-", est["center_sm"]), Paragraph("-", est["center_sm"]), Paragraph("Nenhum contrato registrado no exercício.", est["body_sm"]), Paragraph("-", est["center_sm"]), Paragraph("R$ 0,00", est["right_sm"]), Paragraph("R$ 0,00", est["right_sm"]), Paragraph("0", est["center_sm"])])

        t_anx = Table(rows_anx, colWidths=[2.3 * cm, 2.5 * cm, 5.2 * cm, 3.0 * cm, 2.0 * cm, 2.0 * cm, 1.2 * cm], repeatRows=1)
        t_anx.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), COR_NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]), ("GRID", (0, 0), (-1, -1), 0.3, COR_BORDA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(t_anx)

    doc.build(
        story,
        onFirstPage=lambda c, d: _header_footer_interno(c, d, codigo_relatorio, exercicio, data_emissao_str),
        onLaterPages=lambda c, d: _header_footer_interno(c, d, codigo_relatorio, exercicio, data_emissao_str),
    )
    buf.seek(0)
    return buf


# =============================================================================
# EXPORTAÇÃO EXCEL MULTI-ABA (.xlsx EM MEMÓRIA — OPENPYXL)
# =============================================================================

def gerar_excel_auditoria_pnae(auditoria, codigo_relatorio, usuario_emissor):
    wb = Workbook()
    thin = Side(style="thin", color="CBD5E1")
    borda = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill_hdr = PatternFill("solid", fgColor="0B2545")
    fill_sub = PatternFill("solid", fgColor="EEF4FB")
    fill_zebra = PatternFill("solid", fgColor="F8FAFC")
    font_hdr = Font(name="Calibri", size=10, bold=True, color="FFFFFF")

    def _montar_aba(ws, titulo, cabecalhos, linhas, formatos=None):
        formatos = formatos or {}
        ncols = max(1, len(cabecalhos))
        ult = get_column_letter(ncols)
        ws.merge_cells(f"A1:{ult}1")
        ws["A1"] = f"SEME VALENÇA DO PIAUÍ — AUDITORIA ANALÍTICA PNAE {auditoria['exercicio']} — {titulo.upper()}"
        ws["A1"].font = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
        ws["A1"].fill = fill_hdr
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 24

        ws.merge_cells(f"A2:{ult}2")
        ws["A2"] = f"Protocolo: {codigo_relatorio} | Emitido por: {usuario_emissor} | Data: {obter_data_hora_br_str()} | Normas v.{auditoria['versao_normativa']}"
        ws["A2"].font = Font(name="Calibri", size=9.5, italic=True, color="334155")
        ws["A2"].fill = fill_sub
        ws["A2"].alignment = Alignment(horizontal="center", vertical="center")

        for c_idx, h in enumerate(cabecalhos, start=1):
            c = ws.cell(row=4, column=c_idx, value=h)
            c.font = font_hdr
            c.fill = PatternFill("solid", fgColor="134074")
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = borda
        ws.row_dimensions[4].height = 22

        for r_off, row in enumerate(linhas, start=1):
            r_num = 4 + r_off
            for c_idx, val in enumerate(row, start=1):
                c = ws.cell(row=r_num, column=c_idx, value=val)
                c.font = Font(name="Calibri", size=10)
                c.border = borda
                if r_off % 2 == 0:
                    c.fill = fill_zebra
                fmt = formatos.get(c_idx - 1)
                if fmt == "moeda" and isinstance(val, (int, float)):
                    c.number_format = 'R$ #,##0.00'
                    c.alignment = Alignment(horizontal="right", vertical="center")
                elif fmt == "pct" and isinstance(val, (int, float)):
                    c.number_format = '0.00"%"'
                    c.alignment = Alignment(horizontal="right", vertical="center")
                elif fmt == "int" and isinstance(val, (int, float)):
                    c.number_format = '#,##0'
                    c.alignment = Alignment(horizontal="right", vertical="center")

        for col_idx in range(1, ncols + 1):
            letra = get_column_letter(col_idx)
            mlen = len(str(cabecalhos[col_idx - 1]))
            for r_i in range(5, 5 + len(linhas)):
                v = ws.cell(row=r_i, column=col_idx).value
                if v is not None:
                    mlen = max(mlen, len(str(v)))
            ws.column_dimensions[letra].width = min(max(mlen + 4, 12), 50)
        ws.freeze_panes = "A5"

    # Aba 1: Resumo Executivo
    ws1 = wb.active
    ws1.title = "Resumo Executivo"
    ind = auditoria["indicadores"]
    p45 = auditoria["analise_pnae_45"]
    _montar_aba(
        ws1,
        "Resumo Executivo",
        ["Indicador", "Valor"],
        [
            ["Exercício Analisado", auditoria["exercicio"]],
            ["Total de Fornecedores Analisados", ind["total_fornecedores"]],
            ["Agricultores Individuais", ind["total_individuais"]],
            ["Associações / Cooperativas (Grupos Formais)", ind["total_associacoes"]],
            ["Grupos Informais", ind["total_grupos_informais"]],
            ["Total Contratado (R$)", ind["total_contratado"]],
            ["Total Executado (R$)", ind["total_executado"]],
            ["Total Faturado em NFs (R$)", ind["total_faturado"]],
            ["Total Pago Registrado (R$)", ind["total_pago"]],
            ["Fornecedores Regulares (Verde)", ind["qtd_regulares"]],
            ["Fornecedores em Atenção (Amarelo)", ind["qtd_atencao"]],
            ["Fornecedores com Limite Ultrapassado (Vermelho)", ind["qtd_limites_ultrapassados"]],
            ["Total de Ocorrências / Pendências", ind["qtd_pendencias_total"]],
            ["Ocorrências Críticas", ind["qtd_ocorrencias_criticas"]],
            ["Repasse Federal FNDE (R$)", p45["repasse_federal"]],
            [f"Meta Mínima Legal ({p45['meta_percentual']:.0f}%) (R$)", p45["valor_meta_minima"]],
            ["Executado Elegível com CAF (R$)", p45["total_executado_elegivel"]],
            ["Percentual Elegível Alcançado (%)", p45["percentual_elegivel"]],
        ],
    )

    # Aba 2: Fornecedores e Limites
    ws2 = wb.create_sheet("Fornecedores e Limites")
    _montar_aba(
        ws2,
        "Fornecedores e Limites",
        ["Fornecedor", "CPF/CNPJ", "CAF/DAP", "Tipo", "Contratos", "Entregas", "Contratado", "Executado", "Faturado", "Pago", "Saldo Contr.", "Limite Aplicável", "Excedente Exec.", "% Limite", "Status Limite", "Situação Global"],
        [
            [
                f["razao_social"], f["cpf_cnpj"], f["caf"], f["tipo_fornecedor"], f["qtd_contratos"], f["qtd_entregas"],
                f["valor_contratado"], f["valor_executado"], f["valor_faturado"], f["valor_pago"], f["saldo_contratual"],
                f["limite_aplicavel"] if f["limite_aplicavel"] is not None else "N/V",
                f["excedente_executado"], f["percentual_limite"], f["label_limite"], f["situacao_global_label"],
            ]
            for f in auditoria["fornecedores"]
        ],
        {4: "int", 5: "int", 6: "moeda", 7: "moeda", 8: "moeda", 9: "moeda", 10: "moeda", 11: "moeda", 12: "moeda", 13: "pct"},
    )

    # Aba 3: Quadro de Pendências
    ws3 = wb.create_sheet("Quadro de Pendencias")
    _montar_aba(
        ws3,
        "Quadro Geral de Pendências",
        ["Chave", "Cor", "Grau Risco", "Categoria", "Fornecedor", "CPF/CNPJ", "CAF", "O que foi identificado", "Dados de Origem", "Cálculo Realizado", "Diferença (R$)", "Dispositivo Normativo", "Providência Recomendada", "Setor", "Prazo", "Status"],
        [
            [
                p["chave_pendencia"], p["cor"], p["grau_risco"], p["categoria"], p["fornecedor_nome"], p["cpf_cnpj"], p["caf"],
                p["o_que_foi_identificado"], p["dados_origem"], p["calculo_realizado"], p["valor_diferenca"],
                p["dispositivo_normativo"], p["providencia_recomendada"], p["setor_responsavel"], p["prazo_sugerido"], p["status_resolucao"],
            ]
            for p in auditoria["pendencias"]
        ],
        {10: "moeda"},
    )

    # Aba 4: Associações (VMC)
    ws4 = wb.create_sheet("Associacoes (VMC)")
    _montar_aba(
        ws4,
        "Associações e Cooperativas (VMC = NAF x R$ 40.000)",
        ["Associação / Cooperativa", "CNPJ", "CAF PJ", "Total Associados", "NAF Elegíveis", "Inelegíveis", "VMC Calculado", "Contratado", "Executado", "Excedente Exec.", "Situação"],
        [
            [
                a["razao_social"], a["cnpj"], a["caf_pj"], a["total_associados"], a["naf_elegiveis"], a["inelegiveis"],
                a["vmc_calculado"] if a["vmc_calculado"] is not None else "Não verificável",
                a["valor_contratado"], a["valor_executado"], a["excedente_executado"], a["label_limite"],
            ]
            for a in auditoria["associacoes"]
        ],
        {3: "int", 4: "int", 5: "int", 6: "moeda", 7: "moeda", 8: "moeda", 9: "moeda"},
    )

    # Aba 5: Checklist Documental
    ws5 = wb.create_sheet("Checklist Documental")
    linhas_doc = []
    for f in auditoria["fornecedores"]:
        for d in f["checklist_docs"]:
            linhas_doc.append([f["razao_social"], f["cpf_cnpj"], f["tipo_fornecedor"], d["nome_documento"], d["situacao"], d["detalhe"]])
    _montar_aba(ws5, "Auditoria Documental", ["Fornecedor", "CPF/CNPJ", "Modalidade", "Documento", "Situação", "Detalhamento"], linhas_doc)

    # Aba 6: Contratos e Execução
    ws6 = wb.create_sheet("Contratos e Execucao")
    linhas_ct = []
    for f in auditoria["fornecedores"]:
        for ct in f["contratos"]:
            exec_ct = sum(float(e.valor_total or 0.0) for e in f["entregas"] if e.contrato_id == ct.id)
            linhas_ct.append([
                ct.numero_contrato, ct.chamada_publica or "N/I", f["razao_social"], f["cpf_cnpj"],
                ct.data_inicio.strftime("%d/%m/%Y") if ct.data_inicio else "",
                ct.data_termino.strftime("%d/%m/%Y") if ct.data_termino else "",
                ct.valor_total or 0.0, exec_ct, (ct.valor_total or 0.0) - exec_ct,
            ])
    _montar_aba(ws6, "Contratos e Execução", ["Contrato", "Chamada Pública", "Fornecedor", "CPF/CNPJ", "Início", "Término", "Valor Contratado", "Valor Executado", "Saldo"], linhas_ct, {6: "moeda", 7: "moeda", 8: "moeda"})

    # Aba 7: Base Normativa
    ws7 = wb.create_sheet("Base Normativa")
    _montar_aba(
        ws7,
        "Base Normativa Aplicada",
        ["Código", "Exigência", "Legislação", "Dispositivo", "Gravidade", "Critério de Verificação", "Orientação"],
        [[r.codigo, r.nome_exigencia, r.numero_legislacao, r.dispositivo, r.gravidade, r.criterio_verificacao, r.orientacao_regularizacao] for r in auditoria["regras_normativas"]],
    )

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
