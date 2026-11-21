# almoxarifado_services.py
# -*- coding: utf-8 -*-
"""
Serviços, migração segura (Supabase/PostgreSQL + SQLite), cálculos contábeis (NBC TSP 04),
importação XML NF-e e geração de relatórios oficiais (PDF/Excel) do Almoxarifado da SEME.
"""
import io
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, date

import qrcode
from flask import current_app
from sqlalchemy import text
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from extensions import db
from models import (
    AlmoxCategoria,
    AlmoxEntrega,
    AlmoxEntrada,
    AlmoxInventario,
    AlmoxSetor,
    Material,
)
from utils import obter_data_hora_br_str


_schema_almox_verificado = False

CATEGORIAS_PADRAO_SEME = [
    ("3.3.90.30.16", "1.1.5.6.1.01.01", "Material de Expediente", "Papel A4, canetas, pastas, grampos, cadernos administrativos e escritório"),
    ("3.3.90.30.22", "1.1.5.6.1.01.02", "Material de Limpeza e Higienização", "Detergentes, desinfetantes, água sanitária, sabão, vassouras, sacos de lixo"),
    ("3.3.90.30.14", "1.1.5.6.1.01.03", "Material Educativo e Esportivo", "Lápis, giz, pincel quadro branco, cartolinas, jogos pedagógicos, bolas e kits escolares"),
    ("3.3.90.30.17", "1.1.5.6.1.01.04", "Material de Processamento de Dados (TI)", "Toners, cartuchos, mouses, teclados, cabos, pendrives e suprimentos de informática"),
    ("3.3.90.30.21", "1.1.5.6.1.01.05", "Material de Copa e Cozinha", "Copos descartáveis, guardanapos, coadores, utensílios de consumo não alimentício"),
    ("3.3.90.30.23", "1.1.5.6.1.01.06", "Uniformes, Tecidos e Aviamentos", "Fardamentos escolares, camisetas, jalecos e tecidos"),
    ("3.3.90.30.24", "1.1.5.6.1.01.07", "Material para Manutenção Predial", "Tintas, fechaduras, torneiras, parafusos, materiais hidráulicos e pequenos reparos"),
    ("3.3.90.30.26", "1.1.5.6.1.01.08", "Material Elétrico e Eletrônico", "Lâmpadas LED, tomadas, interruptores, disjuntores, fios e pilhas"),
    ("3.3.90.30.28", "1.1.5.6.1.01.09", "Material de Proteção e Segurança (EPI)", "Luvas, botas, máscaras, aventais e itens de segurança do trabalho"),
    ("3.3.90.30.99", "1.1.5.6.1.01.99", "Outros Materiais de Consumo", "Demais materiais de consumo não enquadrados nos subelementos anteriores"),
]

SETORES_PADRAO_SEME = [
    ("SEME-GAB", "Gabinete da Secretaria Municipal de Educação", "GAB"),
    ("SEME-PED", "Diretoria / Coordenação Pedagógica", "PED"),
    ("SEME-ADM", "Diretoria Administrativa e Financeira", "ADM"),
    ("SEME-RH", "Setor de Recursos Humanos / Pessoal", "RH"),
    ("SEME-TRA", "Setor de Transporte Escolar", "TRANS"),
    ("SEME-MER", "Setor de Merenda Escolar / Nutrição", "MER"),
    ("SEME-INF", "Setor de Infraestrutura e Manutenção Escolar", "INFRA"),
    ("SEME-TI", "Setor de Tecnologia da Informação (TI)", "TI"),
    ("SEME-CAEE", "CAEE - Atendimento Educacional Especializado", "CAEE"),
    ("SEME-PRO", "Protocolo, Arquivo e Documentação", "PROT"),
    ("SEME-PAT", "Setor de Patrimônio", "PAT"),
    ("SEME-ALM", "Almoxarifado Central da SEME", "ALMOX"),
]


def executar_migracao_almoxarifado_segura():
    """
    Garante que todas as tabelas, colunas e índices do Almoxarifado existam
    tanto no Supabase (PostgreSQL em produção na Railway) quanto no SQLite local,
    sem perda de dados existentes.
    """
    global _schema_almox_verificado
    if _schema_almox_verificado:
        return

    try:
        db.create_all()
    except Exception as e:
        print(f"Aviso db.create_all() almoxarifado: {e}")

    is_postgres = "postgres" in str(db.engine.url).lower()

    if is_postgres:
        tabelas_pg = [
            """
            CREATE TABLE IF NOT EXISTS almox_categoria (
                id SERIAL PRIMARY KEY,
                codigo_subelemento VARCHAR(30),
                conta_contabil VARCHAR(50),
                nome VARCHAR(150) NOT NULL UNIQUE,
                descricao VARCHAR(300),
                ativo BOOLEAN NOT NULL DEFAULT TRUE,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS almox_setor (
                id SERIAL PRIMARY KEY,
                codigo VARCHAR(30) UNIQUE,
                nome VARCHAR(180) NOT NULL UNIQUE,
                sigla VARCHAR(30),
                responsavel_nome VARCHAR(200),
                responsavel_cpf VARCHAR(14),
                telefone VARCHAR(30),
                email VARCHAR(120),
                observacoes TEXT,
                secretaria_id INTEGER,
                ativo BOOLEAN NOT NULL DEFAULT TRUE,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS almox_entrada (
                id SERIAL PRIMARY KEY,
                numero_entrada VARCHAR(40) NOT NULL UNIQUE,
                tipo_entrada VARCHAR(80) NOT NULL DEFAULT 'Compra / Licitação',
                numero_nf VARCHAR(60),
                serie_nf VARCHAR(20),
                chave_nfe VARCHAR(60),
                data_emissao_nf DATE,
                data_recebimento DATE NOT NULL DEFAULT CURRENT_DATE,
                fornecedor_id INTEGER,
                fornecedor_nome_snapshot VARCHAR(220),
                contrato_fiscal_id INTEGER,
                numero_empenho VARCHAR(80),
                numero_processo VARCHAR(100),
                modalidade_licitacao VARCHAR(100),
                numero_contrato_arp VARCHAR(80),
                fonte_recursos VARCHAR(120),
                valor_total DOUBLE PRECISION DEFAULT 0.0,
                arquivo_nf_url VARCHAR(500),
                observacoes TEXT,
                conferido_por_nome VARCHAR(150),
                status VARCHAR(30) NOT NULL DEFAULT 'Confirmada',
                motivo_estorno TEXT,
                data_estorno TIMESTAMP,
                usuario_id INTEGER NOT NULL,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS almox_entrada_item (
                id SERIAL PRIMARY KEY,
                entrada_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL,
                quantidade_embalagem DOUBLE PRECISION NOT NULL,
                unidade_entrada VARCHAR(20),
                fator_conversao DOUBLE PRECISION DEFAULT 1.0,
                quantidade_base DOUBLE PRECISION NOT NULL,
                valor_unitario_base DOUBLE PRECISION DEFAULT 0.0,
                valor_total DOUBLE PRECISION DEFAULT 0.0,
                lote VARCHAR(60),
                data_fabricacao DATE,
                data_validade DATE
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS almox_lote (
                id SERIAL PRIMARY KEY,
                material_id INTEGER NOT NULL,
                entrada_item_id INTEGER,
                numero_lote VARCHAR(60) NOT NULL,
                data_fabricacao DATE,
                data_validade DATE,
                quantidade_inicial DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                quantidade_atual DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                custo_unitario DOUBLE PRECISION DEFAULT 0.0,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS almox_entrega (
                id SERIAL PRIMARY KEY,
                codigo_guia VARCHAR(40) NOT NULL UNIQUE,
                codigo_autenticacao VARCHAR(40),
                tipo_saida VARCHAR(60) NOT NULL DEFAULT 'Entrega Direta',
                requisicao_id INTEGER,
                lote_distribuicao_codigo VARCHAR(40),
                tipo_destino VARCHAR(30) NOT NULL DEFAULT 'Escola',
                escola_id INTEGER,
                setor_id INTEGER,
                destino_nome_snapshot VARCHAR(220),
                data_emissao TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                data_entrega DATE NOT NULL DEFAULT CURRENT_DATE,
                entregador_nome VARCHAR(160),
                recebedor_nome VARCHAR(180),
                recebedor_cpf VARCHAR(25),
                recebedor_cargo VARCHAR(120),
                recebedor_servidor_cpf VARCHAR(14),
                data_confirmacao_recebimento TIMESTAMP,
                valor_total DOUBLE PRECISION DEFAULT 0.0,
                numero_processo_baixa VARCHAR(100),
                comprovante_assinado_url VARCHAR(500),
                justificativa TEXT,
                observacoes TEXT,
                status VARCHAR(40) NOT NULL DEFAULT 'Entregue',
                motivo_estorno TEXT,
                data_estorno TIMESTAMP,
                usuario_emissor_id INTEGER NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS almox_entrega_item (
                id SERIAL PRIMARY KEY,
                entrega_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL,
                requisicao_item_id INTEGER,
                lote_id INTEGER,
                quantidade DOUBLE PRECISION NOT NULL,
                custo_unitario_medio DOUBLE PRECISION DEFAULT 0.0,
                valor_total DOUBLE PRECISION DEFAULT 0.0,
                observacao VARCHAR(250)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS almox_inventario (
                id SERIAL PRIMARY KEY,
                codigo VARCHAR(40) NOT NULL UNIQUE,
                exercicio INTEGER NOT NULL,
                tipo VARCHAR(60) NOT NULL DEFAULT 'Geral Anual',
                categoria_id INTEGER,
                portaria_comissao VARCHAR(150),
                presidente_comissao VARCHAR(180),
                membros_comissao TEXT,
                contagem_cega BOOLEAN DEFAULT TRUE,
                bloquear_movimentacoes BOOLEAN DEFAULT FALSE,
                data_abertura TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                data_fechamento TIMESTAMP,
                status VARCHAR(40) NOT NULL DEFAULT 'Aberto',
                justificativa_geral TEXT,
                observacoes_conclusao TEXT,
                total_itens_contados INTEGER DEFAULT 0,
                total_divergencias INTEGER DEFAULT 0,
                valor_estoque_sistema DOUBLE PRECISION DEFAULT 0.0,
                valor_estoque_fisico DOUBLE PRECISION DEFAULT 0.0,
                valor_diferenca_liquida DOUBLE PRECISION DEFAULT 0.0,
                usuario_abertura_id INTEGER NOT NULL,
                usuario_aprovador_id INTEGER
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS almox_inventario_item (
                id SERIAL PRIMARY KEY,
                inventario_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL,
                estoque_sistema_abertura DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                custo_medio_abertura DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                quantidade_contada_1 DOUBLE PRECISION,
                quantidade_contada_2 DOUBLE PRECISION,
                quantidade_final DOUBLE PRECISION,
                diferenca DOUBLE PRECISION DEFAULT 0.0,
                valor_diferenca DOUBLE PRECISION DEFAULT 0.0,
                justificativa_item VARCHAR(300),
                conferido BOOLEAN DEFAULT FALSE
            );
            """,
        ]
        for sql_cmd in tabelas_pg:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(sql_cmd))
            except Exception as e:
                print(f"Aviso tabela PG almoxarifado: {e}")

        # Permite secretaria_solicitante_id nulo em almox_requisicao no PostgreSQL
        try:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE almox_requisicao ALTER COLUMN secretaria_solicitante_id DROP NOT NULL;"))
        except Exception:
            pass

    # Colunas incrementais nas tabelas já existentes (PostgreSQL + SQLite)
    alteracoes = {
        "almox_fornecedor": [
            ("razao_social", "VARCHAR(220)"),
            ("inscricao_estadual", "VARCHAR(40)"),
            ("contato_nome", "VARCHAR(150)"),
            ("ativo", "BOOLEAN DEFAULT TRUE"),
            ("data_cadastro", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ],
        "almox_material": [
            ("codigo_catmat", "VARCHAR(40)"),
            ("especificacao_tecnica", "TEXT"),
            ("marca_referencia", "VARCHAR(100)"),
            ("unidade_compra", "VARCHAR(20)"),
            ("fator_conversao", "FLOAT DEFAULT 1.0"),
            ("categoria_id", "INTEGER"),
            ("ponto_pedido", "FLOAT DEFAULT 0.0"),
            ("custo_medio", "FLOAT DEFAULT 0.0"),
            ("controla_validade", "BOOLEAN DEFAULT FALSE"),
            ("controla_lote", "BOOLEAN DEFAULT FALSE"),
            ("ativo", "BOOLEAN DEFAULT TRUE"),
            ("data_cadastro", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ],
        "almox_requisicao": [
            ("numero_requisicao", "VARCHAR(40)"),
            ("prioridade", "VARCHAR(20) DEFAULT 'Normal'"),
            ("tipo_destino", "VARCHAR(30) DEFAULT 'Setor SEME'"),
            ("escola_id", "INTEGER"),
            ("setor_id", "INTEGER"),
            ("destino_outro_descricao", "VARCHAR(200)"),
            ("solicitante_nome_unidade", "VARCHAR(180)"),
            ("aprovador_usuario_id", "INTEGER"),
            ("data_aprovacao", "TIMESTAMP"),
            ("parecer_aprovacao", "TEXT"),
            ("observacoes_almoxarifado", "TEXT"),
        ],
        "almox_movimento_estoque": [
            ("valor_total", "FLOAT DEFAULT 0.0"),
            ("saldo_anterior", "FLOAT"),
            ("saldo_apos", "FLOAT"),
            ("custo_medio_apos", "FLOAT"),
            ("documento_ref", "VARCHAR(80)"),
            ("entrada_id", "INTEGER"),
            ("entrega_id", "INTEGER"),
            ("inventario_id", "INTEGER"),
            ("lote_id", "INTEGER"),
            ("escola_id", "INTEGER"),
            ("setor_id", "INTEGER"),
            ("movimento_estornado_id", "INTEGER"),
        ],
    }

    for tabela, colunas in alteracoes.items():
        for col_nome, col_tipo in colunas:
            try:
                with db.engine.begin() as conn:
                    if is_postgres:
                        conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {col_nome} {col_tipo};"))
                    else:
                        conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {col_nome} {col_tipo};"))
            except Exception:
                pass

    # Índices de performance (crucial para baixa latência Railway <-> Supabase)
    indices_sql = [
        "CREATE INDEX IF NOT EXISTS idx_almox_entrega_filtro ON almox_entrega (data_entrega, tipo_destino, status);",
        "CREATE INDEX IF NOT EXISTS idx_almox_entrega_escola ON almox_entrega (escola_id);",
        "CREATE INDEX IF NOT EXISTS idx_almox_entrega_setor ON almox_entrega (setor_id);",
        "CREATE INDEX IF NOT EXISTS idx_almox_mov_mat_data ON almox_movimento_estoque (material_id, data_movimento);",
        "CREATE INDEX IF NOT EXISTS idx_almox_mov_escola_setor ON almox_movimento_estoque (escola_id, setor_id);",
        "CREATE INDEX IF NOT EXISTS idx_almox_entrada_data_forn ON almox_entrada (data_recebimento, fornecedor_id);",
        "CREATE INDEX IF NOT EXISTS idx_almox_lote_mat_val ON almox_lote (material_id, data_validade);",
    ]
    for idx_sql in indices_sql:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(idx_sql))
        except Exception:
            pass

    # Backfill: inicializa custo_medio a partir do ultimo_custo nos materiais antigos
    try:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE almox_material SET custo_medio = ultimo_custo "
                    "WHERE (custo_medio IS NULL OR custo_medio = 0) AND ultimo_custo IS NOT NULL AND ultimo_custo > 0;"
                )
            )
            conn.execute(
                text("UPDATE almox_material SET ativo = TRUE WHERE ativo IS NULL;")
                if is_postgres
                else text("UPDATE almox_material SET ativo = 1 WHERE ativo IS NULL;")
            )
            conn.execute(
                text("UPDATE almox_fornecedor SET ativo = TRUE WHERE ativo IS NULL;")
                if is_postgres
                else text("UPDATE almox_fornecedor SET ativo = 1 WHERE ativo IS NULL;")
            )
    except Exception:
        pass

    # Seed inicial de Categorias 3.3.90.30 e Setores Padrão da SEME
    try:
        if AlmoxCategoria.query.count() == 0:
            for cod, conta, nome, desc in CATEGORIAS_PADRAO_SEME:
                db.session.add(
                    AlmoxCategoria(
                        codigo_subelemento=cod,
                        conta_contabil=conta,
                        nome=nome,
                        descricao=desc,
                        ativo=True,
                    )
                )
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Aviso seed categorias almoxarifado: {e}")

    try:
        if AlmoxSetor.query.count() == 0:
            for cod, nome, sigla in SETORES_PADRAO_SEME:
                db.session.add(
                    AlmoxSetor(
                        codigo=cod,
                        nome=nome,
                        sigla=sigla,
                        ativo=True,
                    )
                )
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Aviso seed setores SEME almoxarifado: {e}")

    _schema_almox_verificado = True


# =============================================================================
# UTILITÁRIOS NUMÉRICOS, CÓDIGOS E CÁLCULO CONTÁBIL (NBC TSP 04)
# =============================================================================

def parse_float_br(valor_str, default=0.0):
    """Converte string numérica BR ('1.234,56' ou '12,50' ou '12.50') para float."""
    if valor_str is None:
        return default
    if isinstance(valor_str, (int, float)):
        return float(valor_str)
    s = str(valor_str).strip()
    if not s:
        return default
    s = s.replace("R$", "").replace(" ", "")
    if "," in s and "." in s:
        # Assume formato brasileiro 1.234,56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return default


def formatar_moeda_br(valor):
    try:
        v = float(valor or 0.0)
        return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def formatar_qtd(valor):
    try:
        v = float(valor or 0.0)
        if v.is_integer():
            return f"{int(v):,}".replace(",", ".")
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0"


def calcular_custo_medio_ponderado(qtd_anterior, custo_medio_anterior, qtd_entrada, custo_unitario_entrada):
    """
    Custo Médio Ponderado Móvel (CMPM) conforme NBC TSP 04 (Estoques).
    Para entradas com custo 0 (ex: doação sem valor declarado), preserva o CMPM vigente se houver.
    """
    q_ant = max(0.0, float(qtd_anterior or 0.0))
    c_ant = max(0.0, float(custo_medio_anterior or 0.0))
    q_ent = max(0.0, float(qtd_entrada or 0.0))
    c_ent = max(0.0, float(custo_unitario_entrada or 0.0))

    if q_ent <= 0:
        return round(c_ant, 4)
    if c_ent == 0 and c_ant > 0:
        return round(c_ant, 4)
    total_qtd = q_ant + q_ent
    if total_qtd <= 0:
        return round(c_ent, 4)
    novo_cm = ((q_ant * c_ant) + (q_ent * c_ent)) / total_qtd
    return round(novo_cm, 4)


def gerar_codigo_sequencial(prefixo, model_cls, campo_codigo):
    """Gera código único anual no padrão PREFIXO-2026-0001."""
    ano = date.today().year
    padrao = f"{prefixo}-{ano}-"
    ultimo = (
        model_cls.query.filter(getattr(model_cls, campo_codigo).like(f"{padrao}%"))
        .order_by(model_cls.id.desc())
        .first()
    )
    proximo_num = 1
    if ultimo:
        cod_atual = getattr(ultimo, campo_codigo) or ""
        m = re.search(r"(\d+)$", cod_atual)
        if m:
            proximo_num = int(m.group(1)) + 1
    for _ in range(100):
        candidato = f"{padrao}{proximo_num:04d}"
        if not model_cls.query.filter(getattr(model_cls, campo_codigo) == candidato).first():
            return candidato
        proximo_num += 1
    return f"{padrao}{uuid.uuid4().hex[:6].upper()}"


def gerar_codigo_autenticacao():
    return uuid.uuid4().hex[:10].upper()


def verificar_bloqueio_inventario():
    """Retorna o inventário ativo com bloqueio de movimentação, se existir."""
    return AlmoxInventario.query.filter(
        AlmoxInventario.status.in_(["Aberto", "Em Conferência"]),
        AlmoxInventario.bloquear_movimentacoes.is_(True),
    ).first()


# =============================================================================
# IMPORTADOR DE XML DE NF-e (PADRÃO SEFAZ 3.10 / 4.00)
# =============================================================================

def parse_xml_nfe(file_bytes):
    """
    Extrai cabeçalho fiscal, emitente e lista de produtos de um arquivo XML de NF-e.
    Retorna dict estruturado pronto para preencher a tela de Nova Entrada.
    """
    root = ET.fromstring(file_bytes)

    def _strip_ns(tag):
        return tag.split("}", 1)[-1] if "}" in tag else tag

    def _find_first(parent, tag_name):
        if parent is None:
            return None
        for elem in parent.iter():
            if _strip_ns(elem.tag) == tag_name:
                return elem
        return None

    def _find_child(parent, tag_name):
        if parent is None:
            return None
        for child in parent:
            if _strip_ns(child.tag) == tag_name:
                return child
        return None

    def _text(parent, tag_name, default=""):
        el = _find_child(parent, tag_name)
        return (el.text or "").strip() if el is not None and el.text else default

    inf_nfe = _find_first(root, "infNFe")
    if inf_nfe is None:
        raise ValueError("Arquivo XML não parece ser uma NF-e válida (tag <infNFe> não encontrada).")

    chave_nfe = (inf_nfe.attrib.get("Id") or "").replace("NFe", "").strip()
    ide = _find_child(inf_nfe, "ide")
    emit = _find_child(inf_nfe, "emit")
    ender_emit = _find_child(emit, "enderEmit") if emit is not None else None

    numero_nf = _text(ide, "nNF")
    serie_nf = _text(ide, "serie")
    dh_emi = _text(ide, "dhEmi") or _text(ide, "dEmi")
    data_emissao = dh_emi[:10] if len(dh_emi) >= 10 else ""

    cnpj_raw = re.sub(r"\D", "", _text(emit, "CNPJ") or _text(emit, "CPF"))
    cnpj_fmt = cnpj_raw
    if len(cnpj_raw) == 14:
        cnpj_fmt = f"{cnpj_raw[:2]}.{cnpj_raw[2:5]}.{cnpj_raw[5:8]}/{cnpj_raw[8:12]}-{cnpj_raw[12:]}"

    fornecedor_nome = _text(emit, "xFant") or _text(emit, "xNome")
    fornecedor_razao = _text(emit, "xNome")
    endereco_parts = [
        _text(ender_emit, "xLgr"),
        _text(ender_emit, "nro"),
        _text(ender_emit, "xBairro"),
        _text(ender_emit, "xMun"),
        _text(ender_emit, "UF"),
    ]
    endereco_forn = ", ".join([p for p in endereco_parts if p])

    itens = []
    for child in inf_nfe:
        if _strip_ns(child.tag) == "det":
            prod = _find_child(child, "prod")
            if prod is None:
                continue
            rastro = _find_child(prod, "rastro")
            c_ean = _text(prod, "cEAN")
            if c_ean.upper() == "SEM GTIN":
                c_ean = ""
            itens.append(
                {
                    "codigo_produto": _text(prod, "cProd"),
                    "codigo_barras": c_ean,
                    "descricao": _text(prod, "xProd"),
                    "unidade": (_text(prod, "uCom") or "UN").upper()[:20],
                    "quantidade": parse_float_br(_text(prod, "qCom"), 0.0),
                    "valor_unitario": parse_float_br(_text(prod, "vUnCom"), 0.0),
                    "valor_total": parse_float_br(_text(prod, "vProd"), 0.0),
                    "lote": _text(rastro, "nLote") if rastro is not None else "",
                    "data_fabricacao": _text(rastro, "dFab")[:10] if rastro is not None and _text(rastro, "dFab") else "",
                    "data_validade": _text(rastro, "dVal")[:10] if rastro is not None and _text(rastro, "dVal") else "",
                }
            )

    return {
        "numero_nf": numero_nf,
        "serie_nf": serie_nf,
        "chave_nfe": chave_nfe,
        "data_emissao_nf": data_emissao,
        "fornecedor": {
            "nome": fornecedor_nome,
            "razao_social": fornecedor_razao,
            "cnpj": cnpj_fmt,
            "cnpj_digits": cnpj_raw,
            "endereco": endereco_forn,
            "telefone": _text(ender_emit, "fone") if ender_emit is not None else "",
        },
        "itens": itens,
    }


# =============================================================================
# INFRAESTRUTURA DE PDF OFICIAL (PREFEITURA / SEME — REPORTLAB EM MEMÓRIA)
# =============================================================================

COR_PRIMARIA = colors.HexColor("#072657")
COR_SECUNDARIA = colors.HexColor("#0f4c81")
COR_FUNDO_CAB = colors.HexColor("#eef3f9")
COR_CINZA_CLARO = colors.HexColor("#f8fafc")
COR_BORDA = colors.HexColor("#cbd5e1")
COR_TEXTO_SUAVE = colors.HexColor("#475569")


def _estilos_pdf():
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("AlmoxTitulo", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=COR_PRIMARIA, alignment=TA_CENTER),
        "subtitulo": ParagraphStyle("AlmoxSubtitulo", parent=base["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=COR_TEXTO_SUAVE, alignment=TA_CENTER),
        "secao": ParagraphStyle("AlmoxSecao", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=COR_PRIMARIA, spaceBefore=6, spaceAfter=3),
        "normal": ParagraphStyle("AlmoxNormal", parent=base["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.black),
        "normal_sm": ParagraphStyle("AlmoxNormalSm", parent=base["Normal"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=colors.black),
        "bold": ParagraphStyle("AlmoxBold", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=colors.black),
        "bold_sm": ParagraphStyle("AlmoxBoldSm", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=colors.black),
        "right": ParagraphStyle("AlmoxRight", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10, alignment=TA_RIGHT),
        "right_bold": ParagraphStyle("AlmoxRightBold", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_RIGHT),
        "center": ParagraphStyle("AlmoxCenter", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10, alignment=TA_CENTER),
        "center_bold": ParagraphStyle("AlmoxCenterBold", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_CENTER),
        "termo": ParagraphStyle("AlmoxTermo", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=11, alignment=TA_JUSTIFY, textColor=colors.HexColor("#1e293b")),
        "th": ParagraphStyle("AlmoxTH", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=colors.white, alignment=TA_CENTER),
        "th_left": ParagraphStyle("AlmoxTHLeft", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=colors.white, alignment=TA_LEFT),
        "th_right": ParagraphStyle("AlmoxTHRight", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=colors.white, alignment=TA_RIGHT),
    }


def _rodape_oficial_seme(canvas_obj, doc):
    canvas_obj.saveState()
    w, h = doc.pagesize
    canvas_obj.setStrokeColor(COR_PRIMARIA)
    canvas_obj.setLineWidth(0.8)
    canvas_obj.line(doc.leftMargin, 1.35 * cm, w - doc.rightMargin, 1.35 * cm)
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.setFillColor(COR_TEXTO_SUAVE)
    canvas_obj.drawString(
        doc.leftMargin,
        0.95 * cm,
        f"Gestoor360 | SEME - Almoxarifado Central | Emitido em {obter_data_hora_br_str()}",
    )
    canvas_obj.drawRightString(w - doc.rightMargin, 0.95 * cm, f"Página {doc.page}")
    canvas_obj.restoreState()


def _bloco_cabecalho_institucional(titulo_doc, subtitulo_doc="", codigo_doc=None, largura_total=18 * cm, qr_texto=None):
    est = _estilos_pdf()
    elementos = []

    # Timbre institucional se disponível em static/timbre.jpg
    try:
        timbre_path = os.path.join(current_app.root_path, "static", "timbre.jpg")
        if os.path.exists(timbre_path):
            img = RLImage(timbre_path, width=min(14.5 * cm, largura_total), height=1.8 * cm, kind="proportional")
            img.hAlign = "CENTER"
            elementos.append(img)
            elementos.append(Spacer(1, 4))
    except Exception:
        pass

    cab_esq = Paragraph(
        "<b>SECRETARIA MUNICIPAL DE EDUCAÇÃO (SEME)</b><br/>"
        "<font size='8' color='#475569'>ALMOXARIFADO CENTRAL — GESTÃO DE MATERIAIS DE CONSUMO</font><br/>"
        f"<font size='11' color='#072657'><b>{titulo_doc}</b></font>"
        + (f"<br/><font size='8' color='#475569'>{subtitulo_doc}</font>" if subtitulo_doc else ""),
        est["normal"],
    )

    if qr_texto or codigo_doc:
        col_dir = []
        if codigo_doc:
            col_dir.append(Paragraph(f"<font size='8.5' color='#072657'><b>{codigo_doc}</b></font>", est["center"]))
        if qr_texto:
            qr_img = qrcode.make(qr_texto)
            qr_buf = io.BytesIO()
            qr_img.save(qr_buf, format="PNG")
            qr_buf.seek(0)
            col_dir.append(RLImage(qr_buf, width=1.9 * cm, height=1.9 * cm))
        t_cab = Table([[cab_esq, col_dir]], colWidths=[largura_total - 3.6 * cm, 3.6 * cm])
        t_cab.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("BACKGROUND", (0, 0), (-1, -1), COR_FUNDO_CAB),
                    ("BOX", (0, 0), (-1, -1), 0.7, COR_PRIMARIA),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elementos.append(t_cab)
    else:
        t_cab = Table([[cab_esq]], colWidths=[largura_total])
        t_cab.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), COR_FUNDO_CAB),
                    ("BOX", (0, 0), (-1, -1), 0.7, COR_PRIMARIA),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        elementos.append(t_cab)

    elementos.append(Spacer(1, 8))
    return elementos


def gerar_pdf_guia_entrega(entrega):
    """Gera a Guia de Entrega / Termo de Recebimento Oficial da SEME em PDF (BytesIO)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.4 * cm, bottomMargin=1.9 * cm
    )
    est = _estilos_pdf()
    story = []

    eh_baixa = entrega.tipo_saida == "Baixa Técnica"
    eh_dev = entrega.tipo_saida == "Devolução de Unidade"
    titulo = (
        "TERMO DE BAIXA DE MATERIAL DE CONSUMO"
        if eh_baixa
        else ("TERMO DE DEVOLUÇÃO DE MATERIAL AO ALMOXARIFADO" if eh_dev else "GUIA DE ENTREGA E TERMO DE RECEBIMENTO")
    )
    subtitulo = f"Modalidade: {entrega.tipo_saida} | Status: {entrega.status}"
    qr_payload = f"SEME-ALMOX|{entrega.codigo_guia}|{entrega.codigo_autenticacao or ''}|{entrega.data_entrega.strftime('%Y-%m-%d')}"

    story.extend(
        _bloco_cabecalho_institucional(
            titulo, subtitulo, codigo_doc=entrega.codigo_guia, largura_total=18 * cm, qr_texto=qr_payload
        )
    )

    # 1. Identificação do Destino / Unidade
    detalhe_extra = ""
    if entrega.escola:
        detalhe_extra = f"INEP: {entrega.escola.codigo_inep or 'N/I'} | Zona: {entrega.escola.zona or 'Urbana'}"
    elif entrega.setor:
        detalhe_extra = f"Sigla: {entrega.setor.sigla or '-'} | Resp.: {entrega.setor.responsavel_nome or 'N/I'}"

    info_rows = [
        [
            Paragraph("<b>Tipo de Unidade:</b>", est["bold_sm"]),
            Paragraph(entrega.tipo_destino or "-", est["normal_sm"]),
            Paragraph("<b>Data da Entrega:</b>", est["bold_sm"]),
            Paragraph(entrega.data_entrega.strftime("%d/%m/%Y") if entrega.data_entrega else "-", est["normal_sm"]),
        ],
        [
            Paragraph("<b>Unidade / Destino:</b>", est["bold_sm"]),
            Paragraph(f"<b>{entrega.nome_destino_exibicao}</b>", est["normal_sm"]),
            Paragraph("<b>Requisição Ref.:</b>", est["bold_sm"]),
            Paragraph(entrega.requisicao.codigo_exibicao if entrega.requisicao else "Entrega Direta / Avulsa", est["normal_sm"]),
        ],
    ]
    if detalhe_extra or entrega.numero_processo_baixa:
        info_rows.append(
            [
                Paragraph("<b>Detalhes Unidade:</b>", est["bold_sm"]),
                Paragraph(detalhe_extra or "-", est["normal_sm"]),
                Paragraph("<b>Processo / Lote Dist.:</b>", est["bold_sm"]),
                Paragraph(entrega.numero_processo_baixa or entrega.lote_distribuicao_codigo or "-", est["normal_sm"]),
            ]
        )
    info_rows.append(
        [
            Paragraph("<b>Recebedor:</b>", est["bold_sm"]),
            Paragraph(
                f"{entrega.recebedor_nome or 'A preencher na entrega'}"
                + (f" — {entrega.recebedor_cargo}" if entrega.recebedor_cargo else "")
                + (f" (CPF: {entrega.recebedor_cpf})" if entrega.recebedor_cpf else ""),
                est["normal_sm"],
            ),
            Paragraph("<b>Entregador / Resp.:</b>", est["bold_sm"]),
            Paragraph(entrega.entregador_nome or (entrega.usuario_emissor.username if entrega.usuario_emissor else "-"), est["normal_sm"]),
        ]
    )

    t_info = Table(info_rows, colWidths=[3.1 * cm, 7.6 * cm, 3.1 * cm, 4.2 * cm])
    t_info.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
                ("BOX", (0, 0), (-1, -1), 0.5, COR_BORDA),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(Paragraph("1. IDENTIFICAÇÃO DA UNIDADE E DA MOVIMENTAÇÃO", est["secao"]))
    story.append(t_info)

    if entrega.justificativa or entrega.observacoes:
        story.append(Spacer(1, 5))
        txt_obs = " | ".join([x for x in [entrega.justificativa, entrega.observacoes] if x])
        story.append(Paragraph(f"<b>Justificativa / Observações:</b> {txt_obs}", est["normal_sm"]))

    # 2. Itens Entregues
    story.append(Spacer(1, 6))
    story.append(Paragraph("2. RELAÇÃO DE MATERIAIS", est["secao"]))

    dados_itens = [
        [
            Paragraph("#", est["th"]),
            Paragraph("Material / Especificação", est["th_left"]),
            Paragraph("Unid.", est["th"]),
            Paragraph("Lote / Val.", est["th"]),
            Paragraph("Qtd.", est["th_right"]),
            Paragraph("Custo Méd.", est["th_right"]),
            Paragraph("Subtotal", est["th_right"]),
        ]
    ]
    total_geral = 0.0
    for idx, item in enumerate(entrega.itens, start=1):
        lote_txt = "-"
        if item.lote:
            val_s = item.lote.data_validade.strftime("%d/%m/%Y") if item.lote.data_validade else "S/V"
            lote_txt = f"{item.lote.numero_lote}<br/>{val_s}"
        sub = float(item.valor_total or (item.quantidade * (item.custo_unitario_medio or 0.0)))
        total_geral += sub
        dados_itens.append(
            [
                Paragraph(str(idx), est["center"]),
                Paragraph(
                    f"<b>{item.material.descricao}</b>"
                    + (f"<br/><font size='6.5' color='#475569'>Cód: {item.material.codigo_interno or item.material.id} | {item.material.nome_categoria_exibicao}</font>" if item.material else ""),
                    est["normal_sm"],
                ),
                Paragraph(item.material.unidade_medida if item.material else "-", est["center"]),
                Paragraph(lote_txt, est["center"]),
                Paragraph(formatar_qtd(item.quantidade), est["right_bold"]),
                Paragraph(formatar_moeda_br(item.custo_unitario_medio), est["right"]),
                Paragraph(formatar_moeda_br(sub), est["right_bold"]),
            ]
        )

    dados_itens.append(
        [
            Paragraph("", est["normal_sm"]),
            Paragraph(f"<b>TOTAL GERAL ({len(entrega.itens)} item(ns))</b>", est["bold_sm"]),
            "",
            "",
            "",
            "",
            Paragraph(f"<b>{formatar_moeda_br(total_geral)}</b>", est["right_bold"]),
        ]
    )

    t_itens = Table(dados_itens, colWidths=[0.8 * cm, 7.6 * cm, 1.4 * cm, 2.2 * cm, 1.6 * cm, 2.1 * cm, 2.3 * cm], repeatRows=1)
    t_itens.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, COR_CINZA_CLARO]),
                ("BACKGROUND", (0, -1), (-1, -1), COR_FUNDO_CAB),
                ("SPAN", (1, -1), (5, -1)),
                ("BOX", (0, 0), (-1, -1), 0.6, COR_PRIMARIA),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, COR_BORDA),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t_itens)
    story.append(Spacer(1, 10))

    # 3. Termo de Responsabilidade e Assinaturas
    termo_txt = (
        "<b>DECLARAÇÃO DE RECEBIMENTO E CONFERÊNCIA:</b> Declaro que conferi e recebi integralmente os materiais de consumo "
        "discriminados nesta Guia de Entrega, em perfeitas condições de uso e conservação, assumindo a responsabilidade pela sua "
        "guarda e correta destinação nas atividades oficiais da unidade escolar / setor da Secretaria Municipal de Educação (SEME)."
    )
    if eh_baixa:
        termo_txt = (
            "<b>TERMO DE BAIXA:</b> Atesto que os materiais acima relacionados foram baixados do estoque do Almoxarifado Central da SEME "
            "mediante justificativa técnica registrada neste documento, para fins de controle interno e conciliação contábil (NBC TSP 04)."
        )
    story.append(Paragraph(termo_txt, est["termo"]))
    story.append(Spacer(1, 22))

    bloco_assinaturas = Table(
        [
            [
                Paragraph(
                    "_______________________________________<br/>"
                    "<b>Responsável pelo Almoxarifado (SEME)</b><br/>"
                    f"Emissor: {entrega.usuario_emissor.username if entrega.usuario_emissor else '-'}",
                    est["center"],
                ),
                Paragraph(
                    "_______________________________________<br/>"
                    "<b>Entregador / Transportador</b><br/>"
                    f"Nome: {entrega.entregador_nome or '_____________________'}",
                    est["center"],
                ),
                Paragraph(
                    "_______________________________________<br/>"
                    "<b>Recebedor na Escola / Setor</b><br/>"
                    f"{entrega.recebedor_nome or '_____________________'}<br/>"
                    f"CPF: {entrega.recebedor_cpf or '____.____.____-__'} | Data: ___/___/_____",
                    est["center"],
                ),
            ]
        ],
        colWidths=[6.0 * cm, 5.5 * cm, 6.5 * cm],
    )
    bloco_assinaturas.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(KeepTogether([bloco_assinaturas]))

    if entrega.codigo_autenticacao:
        story.append(Spacer(1, 10))
        story.append(
            Paragraph(
                f"<font size='7' color='#64748b'>Código de Autenticação Interna: <b>{entrega.codigo_autenticacao}</b> | Guia: {entrega.codigo_guia}</font>",
                est["center"],
            )
        )

    doc.build(story, onFirstPage=_rodape_oficial_seme, onLaterPages=_rodape_oficial_seme)
    buf.seek(0)
    return buf


def gerar_pdf_comprovante_entrada(entrada):
    """Gera o Termo de Recebimento de Nota Fiscal / Entrada em Estoque (BytesIO)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.4 * cm, bottomMargin=1.9 * cm
    )
    est = _estilos_pdf()
    story = []

    story.extend(
        _bloco_cabecalho_institucional(
            "COMPROVANTE DE ENTRADA E RECEBIMENTO DE MATERIAIS",
            f"Tipo: {entrada.tipo_entrada} | Status: {entrada.status}",
            codigo_doc=entrada.numero_entrada,
            largura_total=18 * cm,
            qr_texto=f"SEME-ENT|{entrada.numero_entrada}|NF:{entrada.numero_nf or ''}",
        )
    )

    forn_nome = entrada.fornecedor.nome if entrada.fornecedor else (entrada.fornecedor_nome_snapshot or "Não informado")
    forn_cnpj = entrada.fornecedor.cnpj if entrada.fornecedor else "-"

    dados_cab = [
        [
            Paragraph("<b>Fornecedor:</b>", est["bold_sm"]),
            Paragraph(forn_nome, est["normal_sm"]),
            Paragraph("<b>CNPJ:</b>", est["bold_sm"]),
            Paragraph(forn_cnpj or "-", est["normal_sm"]),
        ],
        [
            Paragraph("<b>Nota Fiscal / Série:</b>", est["bold_sm"]),
            Paragraph(f"{entrada.numero_nf or '-'} / {entrada.serie_nf or '-'}", est["normal_sm"]),
            Paragraph("<b>Emissão / Receb.:</b>", est["bold_sm"]),
            Paragraph(
                f"{entrada.data_emissao_nf.strftime('%d/%m/%Y') if entrada.data_emissao_nf else '-'} | "
                f"{entrada.data_recebimento.strftime('%d/%m/%Y') if entrada.data_recebimento else '-'}",
                est["normal_sm"],
            ),
        ],
        [
            Paragraph("<b>Nº Empenho / Processo:</b>", est["bold_sm"]),
            Paragraph(f"Emp: {entrada.numero_empenho or '-'} | Proc: {entrada.numero_processo or '-'}", est["normal_sm"]),
            Paragraph("<b>Contrato / ARP:</b>", est["bold_sm"]),
            Paragraph(entrada.numero_contrato_arp or "-", est["normal_sm"]),
        ],
        [
            Paragraph("<b>Fonte de Recursos:</b>", est["bold_sm"]),
            Paragraph(entrada.fonte_recursos or "-", est["normal_sm"]),
            Paragraph("<b>Modalidade:</b>", est["bold_sm"]),
            Paragraph(entrada.modalidade_licitacao or "-", est["normal_sm"]),
        ],
    ]
    if entrada.chave_nfe:
        dados_cab.append(
            [
                Paragraph("<b>Chave NF-e:</b>", est["bold_sm"]),
                Paragraph(entrada.chave_nfe, est["normal_sm"]),
                "",
                "",
            ]
        )

    t_cab = Table(dados_cab, colWidths=[3.2 * cm, 7.3 * cm, 3.0 * cm, 4.5 * cm])
    estilo_cab = [
        ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.5, COR_BORDA),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if entrada.chave_nfe:
        estilo_cab.append(("SPAN", (1, -1), (3, -1)))
    t_cab.setStyle(TableStyle(estilo_cab))
    story.append(t_cab)
    story.append(Spacer(1, 8))

    # Tabela de itens
    rows = [
        [
            Paragraph("#", est["th"]),
            Paragraph("Material", est["th_left"]),
            Paragraph("Entrada (Emb.)", est["th"]),
            Paragraph("Qtd. Base", est["th_right"]),
            Paragraph("Lote / Validade", est["th"]),
            Paragraph("Vlr. Unit. Base", est["th_right"]),
            Paragraph("Vlr. Total", est["th_right"]),
        ]
    ]
    for idx, it in enumerate(entrada.itens, start=1):
        val_txt = it.data_validade.strftime("%d/%m/%Y") if it.data_validade else "-"
        lote_txt = f"{it.lote or '-'}<br/>{val_txt}" if (it.lote or it.data_validade) else "-"
        rows.append(
            [
                Paragraph(str(idx), est["center"]),
                Paragraph(f"<b>{it.material.descricao}</b>", est["normal_sm"]),
                Paragraph(f"{formatar_qtd(it.quantidade_embalagem)} {it.unidade_entrada or it.material.unidade_medida}", est["center"]),
                Paragraph(f"{formatar_qtd(it.quantidade_base)} {it.material.unidade_medida}", est["right_bold"]),
                Paragraph(lote_txt, est["center"]),
                Paragraph(formatar_moeda_br(it.valor_unitario_base), est["right"]),
                Paragraph(formatar_moeda_br(it.valor_total), est["right_bold"]),
            ]
        )
    rows.append(
        [
            "",
            Paragraph(f"<b>TOTAL DA ENTRADA ({len(entrada.itens)} item(ns))</b>", est["bold_sm"]),
            "",
            "",
            "",
            "",
            Paragraph(f"<b>{formatar_moeda_br(entrada.valor_total)}</b>", est["right_bold"]),
        ]
    )

    t_itens = Table(rows, colWidths=[0.8 * cm, 6.5 * cm, 2.2 * cm, 2.0 * cm, 2.1 * cm, 2.2 * cm, 2.2 * cm], repeatRows=1)
    t_itens.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, COR_CINZA_CLARO]),
                ("BACKGROUND", (0, -1), (-1, -1), COR_FUNDO_CAB),
                ("SPAN", (1, -1), (5, -1)),
                ("BOX", (0, 0), (-1, -1), 0.6, COR_PRIMARIA),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, COR_BORDA),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t_itens)
    story.append(Spacer(1, 20))

    t_ass = Table(
        [
            [
                Paragraph(
                    f"__________________________________________<br/><b>Responsável pelo Recebimento (Almoxarifado)</b><br/>Usuário: {entrada.usuario.username if entrada.usuario else '-'}",
                    est["center"],
                ),
                Paragraph(
                    f"__________________________________________<br/><b>Conferente / Atesto (SEME)</b><br/>{entrada.conferido_por_nome or '____________________________'}",
                    est["center"],
                ),
            ]
        ],
        colWidths=[9.0 * cm, 9.0 * cm],
    )
    story.append(KeepTogether([t_ass]))

    doc.build(story, onFirstPage=_rodape_oficial_seme, onLaterPages=_rodape_oficial_seme)
    buf.seek(0)
    return buf


def gerar_pdf_ata_inventario(inventario):
    """Gera a Ata / Termo Oficial de Encerramento de Inventário (Paisagem A4)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=1.3 * cm, rightMargin=1.3 * cm, topMargin=1.3 * cm, bottomMargin=1.8 * cm
    )
    est = _estilos_pdf()
    larg = landscape(A4)[0] - 2.6 * cm
    story = []

    story.extend(
        _bloco_cabecalho_institucional(
            f"ATA DE INVENTÁRIO FÍSICO-FINANCEIRO DE ALMOXARIFADO — EXERCÍCIO {inventario.exercicio}",
            f"Tipo: {inventario.tipo} | Status: {inventario.status} | Portaria: {inventario.portaria_comissao or 'Não informada'}",
            codigo_doc=inventario.codigo,
            largura_total=larg,
        )
    )

    # Resumo Executivo
    resumo_data = [
        [
            Paragraph("<b>Abertura:</b>", est["bold_sm"]),
            Paragraph(inventario.data_abertura.strftime("%d/%m/%Y %H:%M") if inventario.data_abertura else "-", est["normal_sm"]),
            Paragraph("<b>Fechamento:</b>", est["bold_sm"]),
            Paragraph(inventario.data_fechamento.strftime("%d/%m/%Y %H:%M") if inventario.data_fechamento else "Em aberto", est["normal_sm"]),
            Paragraph("<b>Presidente da Comissão:</b>", est["bold_sm"]),
            Paragraph(inventario.presidente_comissao or "-", est["normal_sm"]),
        ],
        [
            Paragraph("<b>Valor Contábil (Sistema):</b>", est["bold_sm"]),
            Paragraph(formatar_moeda_br(inventario.valor_estoque_sistema), est["bold_sm"]),
            Paragraph("<b>Valor Inventariado (Físico):</b>", est["bold_sm"]),
            Paragraph(formatar_moeda_br(inventario.valor_estoque_fisico), est["bold_sm"]),
            Paragraph("<b>Diferença Líquida:</b>", est["bold_sm"]),
            Paragraph(formatar_moeda_br(inventario.valor_diferenca_liquida), est["bold_sm"]),
        ],
    ]
    t_res = Table(resumo_data, colWidths=[3.4 * cm, 4.8 * cm, 3.6 * cm, 4.8 * cm, 4.2 * cm, larg - 20.8 * cm])
    t_res.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
                ("BOX", (0, 0), (-1, -1), 0.5, COR_PRIMARIA),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, COR_BORDA),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t_res)
    story.append(Spacer(1, 8))

    rows = [
        [
            Paragraph("Cód.", est["th"]),
            Paragraph("Material / Categoria", est["th_left"]),
            Paragraph("Unid.", est["th"]),
            Paragraph("Saldo Sistema", est["th_right"]),
            Paragraph("Qtd. Física", est["th_right"]),
            Paragraph("Diferença", est["th_right"]),
            Paragraph("Custo Méd.", est["th_right"]),
            Paragraph("Impacto (R$)", est["th_right"]),
            Paragraph("Justificativa / Situação", est["th_left"]),
        ]
    ]
    for it in sorted(inventario.itens, key=lambda x: (x.material.descricao if x.material else "")):
        qtd_f = it.quantidade_final if it.quantidade_final is not None else it.quantidade_contada_1
        dif = float(it.diferenca or 0.0)
        cor_dif = "#15803d" if dif > 0 else ("#b91c1c" if dif < 0 else "#334155")
        rows.append(
            [
                Paragraph(str(it.material.codigo_interno or it.material_id), est["center"]),
                Paragraph(f"<b>{it.material.descricao}</b>", est["normal_sm"]),
                Paragraph(it.material.unidade_medida, est["center"]),
                Paragraph(formatar_qtd(it.estoque_sistema_abertura), est["right"]),
                Paragraph(formatar_qtd(qtd_f) if qtd_f is not None else "Não contado", est["right_bold"]),
                Paragraph(f"<font color='{cor_dif}'><b>{formatar_qtd(dif)}</b></font>", est["right"]),
                Paragraph(formatar_moeda_br(it.custo_medio_abertura), est["right"]),
                Paragraph(f"<font color='{cor_dif}'><b>{formatar_moeda_br(it.valor_diferenca)}</b></font>", est["right"]),
                Paragraph(it.justificativa_item or ("Conforme" if dif == 0 else "Divergência apurada"), est["normal_sm"]),
            ]
        )

    col_w = [1.5 * cm, 7.6 * cm, 1.3 * cm, 2.2 * cm, 2.2 * cm, 2.0 * cm, 2.2 * cm, 2.4 * cm, larg - 21.4 * cm]
    t_it = Table(rows, colWidths=col_w, repeatRows=1)
    t_it.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]),
                ("BOX", (0, 0), (-1, -1), 0.6, COR_PRIMARIA),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, COR_BORDA),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ]
        )
    )
    story.append(t_it)
    story.append(Spacer(1, 14))

    membros = [m.strip() for m in re.split(r"[\n,;]+", inventario.membros_comissao or "") if m.strip()]
    assinantes = [
        Paragraph(f"___________________________________<br/><b>{inventario.presidente_comissao or 'Presidente da Comissão'}</b><br/>Presidente da Comissão de Inventário", est["center"])
    ]
    for m in membros[:2]:
        assinantes.append(Paragraph(f"___________________________________<br/><b>{m}</b><br/>Membro da Comissão", est["center"]))
    assinantes.append(Paragraph("___________________________________<br/><b>Secretário(a) Municipal de Educação</b><br/>SEME — Ciência / Aprovação", est["center"]))

    t_ass = Table([assinantes], colWidths=[larg / len(assinantes)] * len(assinantes))
    story.append(KeepTogether([t_ass]))

    doc.build(story, onFirstPage=_rodape_oficial_seme, onLaterPages=_rodape_oficial_seme)
    buf.seek(0)
    return buf


def gerar_pdf_relatorio_tabela(titulo, subtitulo, cabecalhos, linhas, col_widths_cm=None, paisagem=True, kpis=None, rodape_totais=None):
    """Gerador padronizado de relatórios oficiais SEME em PDF (A4 Retrato ou Paisagem)."""
    buf = io.BytesIO()
    page_size = landscape(A4) if paisagem else A4
    doc = SimpleDocTemplate(
        buf, pagesize=page_size, leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.3 * cm, bottomMargin=1.8 * cm
    )
    est = _estilos_pdf()
    larg = page_size[0] - 2.4 * cm
    story = []

    story.extend(_bloco_cabecalho_institucional(titulo, subtitulo, largura_total=larg))

    if kpis:
        kpi_cells = []
        for rotulo, val in kpis:
            kpi_cells.append(
                Paragraph(
                    f"<font size='7' color='#475569'>{rotulo}</font><br/><font size='10' color='#072657'><b>{val}</b></font>",
                    est["center"],
                )
            )
        t_kpi = Table([kpi_cells], colWidths=[larg / len(kpi_cells)] * len(kpi_cells))
        t_kpi.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
                    ("BOX", (0, 0), (-1, -1), 0.5, COR_PRIMARIA),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, COR_BORDA),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(t_kpi)
        story.append(Spacer(1, 8))

    header_row = [Paragraph(str(c), est["th"]) for c in cabecalhos]
    table_data = [header_row]
    for row in linhas:
        table_data.append([Paragraph(str(cell if cell is not None else "-"), est["normal_sm"]) for cell in row])

    if rodape_totais:
        table_data.append([Paragraph(f"<b>{cell}</b>" if cell != "" else "", est["bold_sm"]) for cell in rodape_totais])

    if col_widths_cm:
        col_widths = [w * cm for w in col_widths_cm]
    else:
        col_widths = [larg / max(1, len(cabecalhos))] * len(cabecalhos)

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2 if rodape_totais else -1), [colors.white, COR_CINZA_CLARO]),
        ("BOX", (0, 0), (-1, -1), 0.6, COR_PRIMARIA),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, COR_BORDA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    if rodape_totais:
        style_cmds.append(("BACKGROUND", (0, -1), (-1, -1), COR_FUNDO_CAB))
    t.setStyle(TableStyle(style_cmds))
    story.append(t)

    doc.build(story, onFirstPage=_rodape_oficial_seme, onLaterPages=_rodape_oficial_seme)
    buf.seek(0)
    return buf


# =============================================================================
# GERADOR DE EXCEL FORMATADO (.xlsx EM MEMÓRIA — OPENPYXL)
# =============================================================================

def gerar_excel_almoxarifado(titulo, subtitulo, cabecalhos, linhas, formatos_colunas=None):
    """
    Gera planilha Excel (.xlsx) formatada em memória (BytesIO) para qualquer relatório do Almoxarifado.
    formatos_colunas: dict opcional {indice_coluna_0_based: 'moeda' | 'qtd' | 'int'}
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório SEME"
    formatos_colunas = formatos_colunas or {}

    num_cols = max(1, len(cabecalhos))
    ultima_col_letra = get_column_letter(num_cols)

    # Cabeçalho institucional
    ws.merge_cells(f"A1:{ultima_col_letra}1")
    ws["A1"] = f"SECRETARIA MUNICIPAL DE EDUCAÇÃO (SEME) — {titulo.upper()}"
    ws["A1"].font = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="072657")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26

    ws.merge_cells(f"A2:{ultima_col_letra}2")
    ws["A2"] = f"{subtitulo} | Emitido em {obter_data_hora_br_str()}"
    ws["A2"].font = Font(name="Calibri", size=10, italic=True, color="334155")
    ws["A2"].fill = PatternFill("solid", fgColor="EEF3F9")
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    thin_side = Side(style="thin", color="CBD5E1")
    borda = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    # Linha de cabeçalhos
    row_hdr = 4
    ws.row_dimensions[row_hdr].height = 22
    for c_idx, texto in enumerate(cabecalhos, start=1):
        cell = ws.cell(row=row_hdr, column=c_idx, value=texto)
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0F4C81")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = borda

    # Dados
    fill_zebra = PatternFill("solid", fgColor="F8FAFC")
    for r_offset, row_vals in enumerate(linhas, start=1):
        r_num = row_hdr + r_offset
        for c_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=r_num, column=c_idx, value=val)
            cell.font = Font(name="Calibri", size=10)
            cell.border = borda
            if r_offset % 2 == 0:
                cell.fill = fill_zebra
            fmt = formatos_colunas.get(c_idx - 1)
            if fmt == "moeda" and isinstance(val, (int, float)):
                cell.number_format = 'R$ #,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif fmt == "qtd" and isinstance(val, (int, float)):
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif fmt == "int" and isinstance(val, (int, float)):
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(vertical="center")

    # Auto-largura
    for col_idx in range(1, num_cols + 1):
        col_letra = get_column_letter(col_idx)
        max_len = len(str(cabecalhos[col_idx - 1]))
        for r_idx in range(row_hdr + 1, row_hdr + len(linhas) + 1):
            v = ws.cell(row=r_idx, column=col_idx).value
            if v is not None:
                max_len = max(max_len, len(str(v)))
        ws.column_dimensions[col_letra].width = min(max(max_len + 4, 12), 48)

    ws.freeze_panes = "A5"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
