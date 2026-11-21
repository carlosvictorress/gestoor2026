# auditoria_pnae_engine.py
# -*- coding: utf-8 -*-
"""
Motor de Auditoria Analítica — Agricultura Familiar / PNAE (FNDE)
Secretaria Municipal de Educação de Valença do Piauí (SEME)

Princípios arquiteturais:
- 100% SOMENTE LEITURA sobre contratos, entregas, documentos e cadastros originais do PNAE.
- Migração segura e idempotente para PostgreSQL (Supabase em produção na Railway) e SQLite local.
- Base normativa fundamentada estritamente na Lei nº 11.947/2009 (alterada pela Lei nº 15.226/2025),
  Resolução CD/FNDE nº 4, de 26/02/2026, e Resolução CD/FNDE nº 17, de 22/10/2026.
- Sem suposições: diferencia conformidade comprovada, indício de desconformidade, pendência documental,
  inconsistência financeira, risco administrativo e informação insuficiente.
"""
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    AgricultorFamiliar,
    ConfiguracaoPNAE,
    ContratoPNAE,
    DocumentoAgricultor,
    EntregaPNAE,
    ItemProjetoVenda,
    PnaeAssociadoGrupo,
    PnaeAuditoriaDocumentoStatus,
    PnaeAuditoriaHistorico,
    PnaeAuditoriaJustificativa,
    PnaeNormaRegra,
    PnaePagamentoRegistro,
)


_schema_auditoria_pnae_verificado = False
VERSAO_NORMATIVA_ATUAL = "2026.2-RES17"


# =============================================================================
# 1. BASE NORMATIVA VERIFICADA (FNDE / LEI 11.947/2009 / RES. 4/2026 / RES. 17/2026)
# =============================================================================

NORMAS_PADRAO_PNAE = [
    {
        "codigo": "LIM-IND-40K",
        "nome_exigencia": "Limite Individual de Comercialização da Agricultura Familiar",
        "numero_legislacao": "Resolução CD/FNDE nº 4, de 26/02/2026 (alterada pela Res. CD/FNDE nº 17/2026) e Lei nº 11.947/2009",
        "dispositivo": "Art. 38 da Res. CD/FNDE nº 4/2026; Art. 14 da Lei nº 11.947/2009",
        "descricao_obrigacao": (
            "O limite individual de comercialização do agricultor familiar e do empreendedor familiar rural "
            "para a alimentação escolar é de até R$ 40.000,00 (quarenta mil reais) por CAF (ou NIS, quando aplicável), "
            "por ano civil e por Entidade Executora (EEx), abrangendo todas as chamadas públicas e contratos do exercício."
        ),
        "criterio_verificacao": (
            "Somar todas as contratações e execuções do mesmo CPF/CAF no exercício financeiro na mesma EEx, sem duplicidade. "
            "Verde: executado < R$ 35.000,00; Amarelo: R$ 35.000,00 a R$ 39.999,99; Vermelho: executado > R$ 40.000,00; "
            "Cinza: CPF/CAF não informado ou não verificado."
        ),
        "dados_necessarios": "CPF, número e validade do CAF Pessoa Física, contratos do exercício, entregas aprovadas, notas fiscais e pagamentos.",
        "gravidade": "CRITICA",
        "orientacao_regularizacao": (
            "Conferir todas as notas fiscais, entregas, contratos e o registro do CAF do fornecedor no exercício; "
            "identificar a origem de eventual excedente (erro de lançamento, duplicidade ou extrapolação efetiva) "
            "e adotar providências administrativas junto ao Controle Interno e à Gestão do PNAE."
        ),
        "parametros_json": json.dumps({"limite_individual": 40000.0, "alerta_amarelo": 35000.0}),
    },
    {
        "codigo": "LIM-GF-VMC",
        "nome_exigencia": "Valor Máximo de Comercialização (VMC) para Grupos Formais (Associações e Cooperativas)",
        "numero_legislacao": "Resolução CD/FNDE nº 4, de 26/02/2026 (alterada pela Res. CD/FNDE nº 17/2026)",
        "dispositivo": "Art. 38 da Res. CD/FNDE nº 4/2026",
        "descricao_obrigacao": (
            "Para grupos formais (cooperativas/associações com CAF Pessoa Jurídica), o Valor Máximo de Comercialização "
            "corresponde a VMC = NAF × R$ 40.000,00, sendo NAF o número de agricultores familiares elegíveis com CAF Pessoa Física válido, "
            "inscritos no CAF Pessoa Jurídica, com produção própria dos alimentos contratados e integrantes do projeto de venda. "
            "Cabe à associação/cooperativa controlar os limites individuais de seus cooperados e à EEx controlar o limite total contratado."
        ),
        "criterio_verificacao": (
            "Verificar CNPJ, CAF PJ válido, relação nominal de associados, CAF PF válido de cada participante, vínculo ao CAF PJ, "
            "constância no projeto de venda e produção própria; calcular VMC = NAF × R$ 40.000,00 e confrontar com contratado e executado."
        ),
        "dados_necessarios": "CNPJ, CAF PJ e validade, relação de agricultores associados com CAF PF, vínculo CAF PJ, projeto de venda e contratos.",
        "gravidade": "CRITICA",
        "orientacao_regularizacao": (
            "Solicitar à associação/cooperativa a relação atualizada dos cooperados participantes do projeto de venda, "
            "com cópia/extrato do CAF PJ e dos CAFs PF ativos que comprovem a elegibilidade e a capacidade de comercialização."
        ),
        "parametros_json": json.dumps({"limite_por_associado": 40000.0}),
    },
    {
        "codigo": "PCT-MIN-45",
        "nome_exigencia": "Percentual Mínimo de Recursos Federais do PNAE na Agricultura Familiar (45%)",
        "numero_legislacao": "Lei nº 11.947/2009 (alterada pela Lei nº 15.226/2025) e Res. CD/FNDE nº 4/2026 (alterada pela Res. nº 17/2026)",
        "dispositivo": "Art. 14 da Lei nº 11.947/2009; Art. 29 da Res. CD/FNDE nº 4/2026",
        "descricao_obrigacao": (
            "Do total dos recursos financeiros federais repassados pelo FNDE no âmbito do PNAE, no mínimo 45% (a partir do exercício de 2026; "
            "30% nos exercícios anteriores) deverão ser utilizados na aquisição direta de gêneros alimentícios da agricultura familiar "
            "e do empreendedor familiar rural ou de suas organizações."
        ),
        "criterio_verificacao": (
            "Apurar o total de recursos federais repassados pelo FNDE no exercício (Configuração PNAE) e confrontar com as aquisições "
            "efetivamente executadas e elegíveis da agricultura familiar no exercício."
        ),
        "dados_necessarios": "Valor oficial do repasse federal FNDE no exercício, contratos e entregas aprovadas elegíveis no exercício.",
        "gravidade": "CRITICA",
        "orientacao_regularizacao": (
            "Confirmar o valor integral do repasse federal FNDE no exercício, acelerar chamadas públicas/entregas elegíveis "
            "ou formalizar justificativa técnica prevista nas hipóteses normativas de dispensa de atendimento na prestação de contas."
        ),
        "parametros_json": json.dumps({"percentual_minimo_2026": 45.0, "percentual_anterior": 30.0}),
    },
    {
        "codigo": "MULHER-50",
        "nome_exigencia": "Inclusão Produtiva de Mulheres Agricultoras (Cota Mínima de 50% por UFPA)",
        "numero_legislacao": "Resolução CD/FNDE nº 4, de 26/02/2026 (alterada pela Res. CD/FNDE nº 17, de 22/10/2026)",
        "dispositivo": "Art. 29, § 3º da Res. CD/FNDE nº 4/2026",
        "descricao_obrigacao": (
            "Nas aquisições de cada Unidade Familiar de Produção Agrária (UFPA) identificada pelo CAF, no mínimo 50% do valor "
            "dos alimentos adquiridos deverá corresponder a notas fiscais de venda emitidas em nome de mulher integrante da respectiva UFPA, "
            "com a indicação do seu CPF, observadas as regras de vigência e transição normativa."
        ),
        "criterio_verificacao": (
            "Quando houver registro de emissão das notas fiscais por gênero/titularidade da UFPA, verificar se o percentual atinge 50%; "
            "na ausência desse dado no sistema, registrar como pendência de informação complementar para conferência na pasta física."
        ),
        "dados_necessarios": "Notas fiscais de venda, CPF da emitente integrante da UFPA e composição do CAF.",
        "gravidade": "MEDIA",
        "orientacao_regularizacao": (
            "Conferir nas notas fiscais arquivadas e nos extratos do CAF a titularidade das vendas realizadas por mulheres integrantes da UFPA, "
            "registrando a informação nos controles do PNAE para atendimento à Resolução CD/FNDE nº 17/2026."
        ),
        "parametros_json": json.dumps({"percentual_mulheres": 50.0}),
    },
    {
        "codigo": "CHAM-PUB",
        "nome_exigencia": "Centralidade da Chamada Pública e Critérios de Seleção Territorial e Social (RITS)",
        "numero_legislacao": "Lei nº 11.947/2009 e Resolução CD/FNDE nº 4/2026 (alterada pela Res. CD/FNDE nº 17/2026)",
        "dispositivo": "Art. 14, § 1º da Lei nº 11.947/2009; Arts. 30 e 36 da Res. CD/FNDE nº 4/2026",
        "descricao_obrigacao": (
            "A aquisição de gêneros da agricultura familiar deve ocorrer prioritariamente por meio de Chamada Pública (dispensa de licitação), "
            "observando-se a ordem territorial (local, região imediata, região intermediária, estado e país) e os critérios de priorização "
            "para assentamentos da reforma agrária, comunidades indígenas e quilombolas, grupos de mulheres e jovens (RITS)."
        ),
        "criterio_verificacao": "Verificar se todos os contratos possuem identificação da Chamada Pública de origem e critérios de seleção registrados.",
        "dados_necessarios": "Número da Chamada Pública vinculada a cada contrato, edital e ata de classificação dos projetos de venda.",
        "gravidade": "ALTA",
        "orientacao_regularizacao": "Vincular o número da Chamada Pública em cada contrato e anexar a ata de habilitação/classificação ao processo administrativo.",
        "parametros_json": json.dumps({}),
    },
    {
        "codigo": "PESQ-PRECO",
        "nome_exigencia": "Formação e Pesquisa de Preços da Agricultura Familiar",
        "numero_legislacao": "Resolução CD/FNDE nº 4, de 26/02/2026 (alterada pela Res. CD/FNDE nº 17/2026)",
        "dispositivo": "Art. 31 da Res. CD/FNDE nº 4/2026",
        "descricao_obrigacao": (
            "A pesquisa de preços de referência para a Chamada Pública deve considerar a média de no mínimo três fornecedores locais "
            "da agricultura familiar (preferencialmente feira do produtor), incluindo custos de frete e embalagem, vedada a utilização "
            "de preços de atacadistas/intermediários que descaracterizem a agricultura familiar."
        ),
        "criterio_verificacao": "Verificar se há evidência de pesquisa de preços e definição de valores unitários nos itens do projeto de venda.",
        "dados_necessarios": "Mapa de pesquisa de preços da Chamada Pública e tabela de preços unitários dos itens contratados.",
        "gravidade": "MEDIA",
        "orientacao_regularizacao": "Manter no processo da Chamada Pública o mapa comparativo com mínimo de 3 cotações locais da agricultura familiar.",
        "parametros_json": json.dumps({"min_fornecedores_pesquisa": 3}),
    },
    {
        "codigo": "DOC-HAB",
        "nome_exigencia": "Habilitação Documental, Regularidade do CAF e Projeto de Venda",
        "numero_legislacao": "Resolução CD/FNDE nº 4, de 26/02/2026 (alterada pela Res. CD/FNDE nº 17/2026)",
        "dispositivo": "Arts. 35 a 39 da Res. CD/FNDE nº 4/2026",
        "descricao_obrigacao": (
            "Os fornecedores devem apresentar documentação de habilitação compatível com sua modalidade (Individual, Grupo Informal ou Grupo Formal), "
            "incluindo CPF/CNPJ, CAF válido, Projeto de Venda, certidões exigidas e declaração de produção própria."
        ),
        "criterio_verificacao": "Conferir o checklist documental por fornecedor conforme modalidade, sem presumir irregularidade apenas pela ausência de upload digital.",
        "dados_necessarios": "Cadastro do fornecedor, validade do CAF, documentos anexados e registros de conferência documental.",
        "gravidade": "ALTA",
        "orientacao_regularizacao": "Conferir a pasta física da Chamada Pública e digitalizar/anexar os documentos pendentes ou solicitar atualização de certidões vencidas.",
        "parametros_json": json.dumps({}),
    },
    {
        "codigo": "EXEC-FIN",
        "nome_exigencia": "Formalização Contratual, Vigência, Comprovação Fiscal e Regularidade Financeira",
        "numero_legislacao": "Resolução CD/FNDE nº 4/2026, Lei nº 11.947/2009 e Lei nº 4.320/1964",
        "dispositivo": "Art. 39 da Res. CD/FNDE nº 4/2026; Arts. 62 e 63 da Lei nº 4.320/1964",
        "descricao_obrigacao": (
            "O fornecimento deve ser formalizado por contrato administrativo vigente, com execução compatível com o valor contratado, "
            "comprovação documental de entrega, emissão de nota fiscal sem duplicidades e pagamento estritamente amparado em liquidação regular."
        ),
        "criterio_verificacao": (
            "Verificar: execução acima do contratado, saldo negativo, entregas fora da vigência contratual, entregas sem NF ou recibo, "
            "notas fiscais duplicadas, divergências entre contratado/executado/faturado/pago e pagamentos sem comprovação."
        ),
        "dados_necessarios": "Contratos (vigência e valor), entregas aprovadas, notas fiscais, comprovantes de recebimento e registros de pagamento.",
        "gravidade": "ALTA",
        "orientacao_regularizacao": (
            "Realizar conciliação entre contrato, aditivos, termos de recebimento, notas fiscais e ordens bancárias; "
            "corrigir eventuais duplicidades ou formalizar termo de conferência financeira."
        ),
        "parametros_json": json.dumps({}),
    },
    {
        "codigo": "CAF-PJ-700K",
        "nome_exigencia": "Parâmetro de Repasse Superior a R$ 700.000,00 para Exclusividade de CAF Pessoa Jurídica",
        "numero_legislacao": "Resolução CD/FNDE nº 4, de 26/02/2026",
        "dispositivo": "Art. 37 da Res. CD/FNDE nº 4/2026",
        "descricao_obrigacao": (
            "A Entidade Executora cujo valor total de repasse do FNDE para o PNAE seja superior a R$ 700.000,00/ano "
            "tem a prerrogativa (facultativa, desde que prevista em edital) de aceitar propostas exclusivamente de organizações "
            "detentoras de CAF Pessoa Jurídica."
        ),
        "criterio_verificacao": "Informativo gerencial baseado no valor anual de repasse FNDE cadastrado em ConfiguracaoPNAE.",
        "dados_necessarios": "Valor anual de repasse FNDE e perfil de fornecedores (individuais vs. grupos formais).",
        "gravidade": "BAIXA",
        "orientacao_regularizacao": "Avaliar no planejamento da próxima Chamada Pública conforme conveniência administrativa e oferta local.",
        "parametros_json": json.dumps({"limite_repasse_exclusividade_pj": 700000.0}),
    },
]


def executar_migracao_auditoria_pnae_segura():
    """
    Cria tabelas e índices do módulo de Auditoria Analítica PNAE de forma 100% idempotente
    no Supabase (PostgreSQL) e SQLite local, sem modificar tabelas existentes do PNAE.
    """
    global _schema_auditoria_pnae_verificado
    if _schema_auditoria_pnae_verificado:
        return

    try:
        db.create_all()
    except Exception as e:
        print(f"Aviso db.create_all() auditoria_pnae: {e}")

    is_postgres = "postgres" in str(db.engine.url).lower()

    if is_postgres:
        tabelas_pg = [
            """
            CREATE TABLE IF NOT EXISTS pnae_norma_regra (
                id SERIAL PRIMARY KEY,
                codigo VARCHAR(40) NOT NULL UNIQUE,
                nome_exigencia VARCHAR(220) NOT NULL,
                numero_legislacao VARCHAR(160) NOT NULL,
                dispositivo VARCHAR(120) NOT NULL,
                descricao_obrigacao TEXT NOT NULL,
                criterio_verificacao TEXT NOT NULL,
                dados_necessarios TEXT NOT NULL,
                gravidade VARCHAR(30) NOT NULL DEFAULT 'ALTA',
                orientacao_regularizacao TEXT NOT NULL,
                parametros_json TEXT,
                versao_normativa VARCHAR(40) NOT NULL DEFAULT '2026.2-RES17',
                ativa BOOLEAN NOT NULL DEFAULT TRUE,
                data_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS pnae_associado_grupo (
                id SERIAL PRIMARY KEY,
                agricultor_grupo_id INTEGER NOT NULL,
                nome VARCHAR(200) NOT NULL,
                cpf VARCHAR(20),
                caf_pf_numero VARCHAR(60),
                caf_pf_validade DATE,
                inscrito_caf_pj BOOLEAN NOT NULL DEFAULT FALSE,
                consta_projeto_venda BOOLEAN NOT NULL DEFAULT FALSE,
                producao_propria BOOLEAN NOT NULL DEFAULT FALSE,
                mulher_agricultora BOOLEAN NOT NULL DEFAULT FALSE,
                ativo BOOLEAN NOT NULL DEFAULT TRUE,
                observacoes TEXT,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS pnae_auditoria_documento_status (
                id SERIAL PRIMARY KEY,
                agricultor_id INTEGER NOT NULL,
                exercicio INTEGER NOT NULL,
                codigo_doc VARCHAR(60) NOT NULL,
                nome_documento VARCHAR(150) NOT NULL,
                situacao VARCHAR(40) NOT NULL DEFAULT 'DOCUMENTO PENDENTE DE CONFERÊNCIA',
                data_validade DATE,
                arquivo_url VARCHAR(500),
                observacao TEXT,
                usuario_conferencia VARCHAR(100),
                data_conferencia TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS pnae_pagamento_registro (
                id SERIAL PRIMARY KEY,
                agricultor_id INTEGER NOT NULL,
                contrato_id INTEGER,
                entrega_id INTEGER,
                exercicio INTEGER NOT NULL,
                data_pagamento DATE NOT NULL,
                numero_ordem_bancaria VARCHAR(80),
                numero_nota_fiscal VARCHAR(60),
                valor_pago DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                emitido_nome_mulher BOOLEAN DEFAULT FALSE,
                comprovante_url VARCHAR(500),
                observacoes TEXT,
                usuario_registro VARCHAR(100),
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS pnae_auditoria_justificativa (
                id SERIAL PRIMARY KEY,
                chave_pendencia VARCHAR(160) NOT NULL,
                exercicio INTEGER NOT NULL,
                agricultor_id INTEGER,
                contrato_id INTEGER,
                categoria VARCHAR(80) NOT NULL,
                grau_risco VARCHAR(30) NOT NULL DEFAULT 'AMARELO',
                descricao_pendencia TEXT NOT NULL,
                justificativa_administrativa TEXT NOT NULL,
                providencia_adotada TEXT,
                setor_responsavel VARCHAR(120),
                prazo_sugerido DATE,
                status_resolucao VARCHAR(40) NOT NULL DEFAULT 'Em Análise',
                documento_comprobatorio_url VARCHAR(500),
                usuario_registro VARCHAR(100) NOT NULL,
                data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                historico_json TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS pnae_auditoria_historico (
                id SERIAL PRIMARY KEY,
                codigo_relatorio VARCHAR(50) NOT NULL UNIQUE,
                tipo_relatorio VARCHAR(60) NOT NULL,
                exercicio INTEGER NOT NULL,
                usuario_responsavel VARCHAR(120) NOT NULL,
                ip_address VARCHAR(50),
                data_geracao TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                filtros_json TEXT,
                qtd_registros_analisados INTEGER DEFAULT 0,
                qtd_pendencias_total INTEGER DEFAULT 0,
                qtd_criticas INTEGER DEFAULT 0,
                versao_normativa VARCHAR(40) NOT NULL DEFAULT '2026.2-RES17',
                resumo_json TEXT,
                observacoes TEXT
            );
            """,
        ]
        for sql_cmd in tabelas_pg:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(sql_cmd))
            except Exception as e:
                print(f"Aviso tabela PG auditoria_pnae: {e}")

    indices = [
        "CREATE INDEX IF NOT EXISTS idx_pnae_assoc_grupo ON pnae_associado_grupo (agricultor_grupo_id);",
        "CREATE INDEX IF NOT EXISTS idx_pnae_docstat_agr_ex ON pnae_auditoria_documento_status (agricultor_id, exercicio);",
        "CREATE INDEX IF NOT EXISTS idx_pnae_pag_agr_ex ON pnae_pagamento_registro (agricultor_id, exercicio);",
        "CREATE INDEX IF NOT EXISTS idx_pnae_just_chave_ex ON pnae_auditoria_justificativa (chave_pendencia, exercicio);",
        "CREATE INDEX IF NOT EXISTS idx_pnae_hist_ex_dt ON pnae_auditoria_historico (exercicio, data_geracao);",
    ]
    for idx in indices:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(idx))
        except Exception:
            pass

    # Seed idempotente das normas oficiais
    try:
        codigos_existentes = {r.codigo for r in PnaeNormaRegra.query.with_entities(PnaeNormaRegra.codigo).all()}
        inseridos = 0
        for item in NORMAS_PADRAO_PNAE:
            if item["codigo"] not in codigos_existentes:
                db.session.add(PnaeNormaRegra(**item, versao_normativa=VERSAO_NORMATIVA_ATUAL, ativa=True))
                inseridos += 1
        if inseridos:
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Aviso seed normas PNAE: {e}")

    _schema_auditoria_pnae_verificado = True


# =============================================================================
# 2. UTILITÁRIOS DE FORMATAÇÃO E NORMALIZAÇÃO
# =============================================================================

def limpar_digitos(valor):
    return re.sub(r"\D", "", str(valor or "")).strip()


def normalizar_caf(valor):
    s = re.sub(r"\s+", "", str(valor or "")).upper()
    return s if s else ""


def formatar_cpf_cnpj(valor):
    d = limpar_digitos(valor)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return (valor or "").strip() or "Não informado"


def formatar_moeda_br(valor):
    try:
        v = float(valor or 0.0)
        sinal = "-" if v < 0 else ""
        v_abs = abs(v)
        return f"{sinal}R$ " + f"{v_abs:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def parse_float_br(valor_str, default=0.0):
    if valor_str is None:
        return default
    if isinstance(valor_str, (int, float)):
        return float(valor_str)
    s = str(valor_str).strip().replace("R$", "").replace(" ", "")
    if not s:
        return default
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return default


def is_grupo_formal(tipo_fornecedor):
    t = (tipo_fornecedor or "").strip().lower()
    return any(k in t for k in ("formal", "cooperativa", "associa", "jurid", "juríd", "efr"))


def is_grupo_informal(tipo_fornecedor):
    t = (tipo_fornecedor or "").strip().lower()
    return "informal" in t


def _gerar_chave_pendencia(*partes):
    bruto = "|".join(str(p or "").strip().upper() for p in partes)
    h = hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:12].upper()
    return f"PND-{h}"


def _obter_tipo_doc_seguro(doc):
    """Compatibilidade com pnae_documento.tipo (models.py) e eventual tipo_documento."""
    return (getattr(doc, "tipo", None) or getattr(doc, "tipo_documento", None) or "").strip()


def _carregar_parametros_normativos():
    regras = PnaeNormaRegra.query.filter_by(ativa=True).all()
    mapa = {r.codigo: r for r in regras}
    limite_ind = 40000.0
    alerta_amarelo = 35000.0
    pct_2026 = 45.0
    pct_ant = 30.0
    pct_mulher = 50.0

    if "LIM-IND-40K" in mapa and mapa["LIM-IND-40K"].parametros_json:
        try:
            p = json.loads(mapa["LIM-IND-40K"].parametros_json)
            limite_ind = float(p.get("limite_individual", 40000.0))
            alerta_amarelo = float(p.get("alerta_amarelo", 35000.0))
        except Exception:
            pass
    if "PCT-MIN-45" in mapa and mapa["PCT-MIN-45"].parametros_json:
        try:
            p = json.loads(mapa["PCT-MIN-45"].parametros_json)
            pct_2026 = float(p.get("percentual_minimo_2026", 45.0))
            pct_ant = float(p.get("percentual_anterior", 30.0))
        except Exception:
            pass
    if "MULHER-50" in mapa and mapa["MULHER-50"].parametros_json:
        try:
            p = json.loads(mapa["MULHER-50"].parametros_json)
            pct_mulher = float(p.get("percentual_mulheres", 50.0))
        except Exception:
            pass

    return {
        "regras": regras,
        "mapa_regras": mapa,
        "limite_individual": limite_ind,
        "alerta_amarelo": alerta_amarelo,
        "pct_minimo_2026": pct_2026,
        "pct_minimo_anterior": pct_ant,
        "pct_mulher": pct_mulher,
        "versao": regras[0].versao_normativa if regras else VERSAO_NORMATIVA_ATUAL,
    }


def _criar_alerta(
    exercicio,
    agricultor_obj,
    contrato_obj,
    categoria,
    codigo_regra,
    mapa_regras,
    cor,
    grau_risco,
    o_que_foi_identificado,
    dados_origem,
    calculo_realizado,
    risco_identificado,
    providencia_recomendada,
    setor_responsavel="Coordenação do PNAE / SEME",
    prazo_sugerido="15 dias",
    valor_diferenca=0.0,
    chave_extra="",
):
    regra = mapa_regras.get(codigo_regra)
    dispositivo = (
        f"{regra.numero_legislacao} — {regra.dispositivo}"
        if regra
        else "Lei nº 11.947/2009, art. 14; Res. CD/FNDE nº 4/2026 (alterada pela Res. nº 17/2026)"
    )
    regra_nome = regra.nome_exigencia if regra else codigo_regra
    chave = _gerar_chave_pendencia(
        exercicio,
        agricultor_obj.id if agricultor_obj else "GLOBAL",
        contrato_obj.id if contrato_obj else "GERAL",
        codigo_regra,
        categoria,
        chave_extra or o_que_foi_identificado[:50],
    )
    return {
        "chave_pendencia": chave,
        "exercicio": exercicio,
        "agricultor_id": agricultor_obj.id if agricultor_obj else None,
        "fornecedor_nome": agricultor_obj.razao_social if agricultor_obj else "Programa PNAE / SEME (Global)",
        "cpf_cnpj": formatar_cpf_cnpj(agricultor_obj.cpf_cnpj) if agricultor_obj else "-",
        "caf": (agricultor_obj.dap_caf_numero or "Não informado") if agricultor_obj else "-",
        "tipo_fornecedor": (agricultor_obj.tipo_fornecedor or "Individual") if agricultor_obj else "Programa PNAE",
        "contrato_id": contrato_obj.id if contrato_obj else None,
        "numero_contrato": contrato_obj.numero_contrato if contrato_obj else "-",
        "categoria": categoria,
        "codigo_regra": codigo_regra,
        "regra_aplicada": regra_nome,
        "dispositivo_normativo": dispositivo,
        "cor": cor,  # VERMELHO, LARANJA, AMARELO, VERDE, CINZA
        "grau_risco": grau_risco,  # CRITICO, ALTO, MEDIO, BAIXO, INFORMATIVO
        "o_que_foi_identificado": o_que_foi_identificado,
        "dados_origem": dados_origem,
        "calculo_realizado": calculo_realizado,
        "valor_diferenca": round(float(valor_diferenca or 0.0), 2),
        "risco_identificado": risco_identificado,
        "providencia_recomendada": providencia_recomendada,
        "setor_responsavel": setor_responsavel,
        "prazo_sugerido": prazo_sugerido,
        "justificativa": None,
        "status_resolucao": "Aberta",
    }


# =============================================================================
# 3. CHECKLIST DOCUMENTAL POR MODALIDADE (SEÇÃO 5)
# =============================================================================

ITENS_CHECKLIST_DOCUMENTAL = [
    ("CPF_CNPJ", "CPF ou CNPJ do Fornecedor", "todos"),
    ("CAF_PF", "CAF Pessoa Física (Ativo/Válido)", "individual_informal"),
    ("CAF_PJ", "CAF Pessoa Jurídica (Associação/Cooperativa)", "formal"),
    ("PROJETO_VENDA", "Projeto de Venda (PVAF)", "todos"),
    ("CONTRATO_ASSINADO", "Contrato Administrativo Assinado", "todos"),
    ("DOC_HABILITACAO", "Documentação de Habilitação / Certidões (CNDs / Residência)", "todos"),
    ("COMPROV_ENTREGA", "Comprovantes / Recibos de Entrega", "todos"),
    ("NOTAS_FISCAIS", "Notas Fiscais de Comercialização", "todos"),
    ("COMPROV_PAGAMENTO", "Comprovantes de Pagamento / Ordens Bancárias", "todos"),
    ("RELACAO_PRODUTOS", "Relação de Produtos Fornecidos (Itens do Projeto)", "todos"),
    ("ORIGEM_PRODUCAO", "Declaração / Comprovação de Produção Própria", "todos"),
    ("CHAMADA_DOCS", "Demais Documentos da Chamada Pública", "todos"),
]


def _avaliar_documentacao_fornecedor(
    agricultor,
    contratos_ex,
    entregas_ex,
    pagamentos_ex,
    docs_status_map,
    hoje,
):
    eh_formal = is_grupo_formal(agricultor.tipo_fornecedor)
    eh_informal = is_grupo_informal(agricultor.tipo_fornecedor)
    eh_individual = not eh_formal and not eh_informal

    docs_anexos = agricultor.documentos or []
    mapa_anexos_tipo = defaultdict(list)
    for d in docs_anexos:
        t = _obter_tipo_doc_seguro(d).lower()
        mapa_anexos_tipo[t].append(d)

    def _achar_anexo(*termos):
        for k, lista in mapa_anexos_tipo.items():
            if any(termo in k for termo in termos):
                return lista[0]
        return None

    resultado = []
    for codigo, nome_doc, escopo in ITENS_CHECKLIST_DOCUMENTAL:
        manual = docs_status_map.get((agricultor.id, codigo))
        situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
        detalhe = ""
        arquivo_url = None
        validade = None

        # Aplica escopo por modalidade
        if escopo == "formal" and not eh_formal:
            situacao = "DOCUMENTO NÃO APLICÁVEL"
            detalhe = "Exigido apenas para Grupos Formais (Associações/Cooperativas)."
        elif escopo == "individual_informal" and eh_formal:
            # Para grupo formal, CAF PF é exigido dos associados (verificado na seção de associações)
            assocs = agricultor.associados_grupo or []
            if assocs:
                validos = [a for a in assocs if a.caf_pf_numero and (not a.caf_pf_validade or a.caf_pf_validade >= hoje)]
                if len(validos) == len(assocs):
                    situacao = "DOCUMENTO REGULAR"
                    detalhe = f"{len(validos)}/{len(assocs)} associado(s) com CAF PF válido registrado."
                else:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = f"Apenas {len(validos)} de {len(assocs)} associado(s) possuem CAF PF válido."
            else:
                situacao = "DOCUMENTO AUSENTE"
                detalhe = "Relação de associados e respectivos CAFs Pessoa Física não cadastrados."
        else:
            # Avaliação automática não-presuntiva
            if codigo == "CPF_CNPJ":
                dig = limpar_digitos(agricultor.cpf_cnpj)
                if (eh_formal and len(dig) == 14) or (not eh_formal and len(dig) == 11):
                    situacao = "DOCUMENTO REGULAR"
                    detalhe = formatar_cpf_cnpj(dig)
                elif dig:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = f"Documento informado ({agricultor.cpf_cnpj}) diverge do formato esperado para {agricultor.tipo_fornecedor}."
                else:
                    situacao = "DOCUMENTO AUSENTE"
                    detalhe = "CPF/CNPJ não informado no cadastro."

            elif codigo in ("CAF_PF", "CAF_PJ"):
                caf = normalizar_caf(agricultor.dap_caf_numero)
                validade = agricultor.dap_caf_validade
                if not caf:
                    situacao = "DOCUMENTO AUSENTE"
                    detalhe = "Número do CAF/DAP não informado no cadastro."
                elif validade and validade < hoje:
                    situacao = "DOCUMENTO VENCIDO"
                    detalhe = f"CAF {caf} vencido em {validade.strftime('%d/%m/%Y')}."
                elif validade:
                    situacao = "DOCUMENTO REGULAR"
                    detalhe = f"CAF {caf} válido até {validade.strftime('%d/%m/%Y')}."
                else:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = f"CAF {caf} informado, porém sem data de validade registrada."

            elif codigo == "PROJETO_VENDA":
                anexo = _achar_anexo("projeto", "pvaf")
                tem_itens = any((c.itens and len(c.itens) > 0) for c in contratos_ex)
                if anexo:
                    situacao = "DOCUMENTO REGULAR"
                    arquivo_url = anexo.filename
                    detalhe = "Anexado no prontuário digital."
                elif tem_itens:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = "Itens do projeto registrados no contrato; arquivo digital do PVAF assinado não anexado."
                else:
                    situacao = "DOCUMENTO AUSENTE"
                    detalhe = "Projeto de Venda (PVAF) não anexado e sem itens vinculados no exercício."

            elif codigo == "CONTRATO_ASSINADO":
                anexo = _achar_anexo("contrato")
                if anexo and contratos_ex:
                    situacao = "DOCUMENTO REGULAR"
                    arquivo_url = anexo.filename
                    detalhe = f"{len(contratos_ex)} contrato(s) cadastrado(s) e anexo localizado."
                elif contratos_ex:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = f"{len(contratos_ex)} contrato(s) cadastrado(s) no sistema; conferir via assinada na pasta física."
                else:
                    situacao = "DOCUMENTO NÃO APLICÁVEL"
                    detalhe = "Fornecedor sem contrato ativo no exercício selecionado."

            elif codigo == "DOC_HABILITACAO":
                cnds = [d for d in docs_anexos if any(x in _obter_tipo_doc_seguro(d).lower() for x in ("cnd", "certid", "resid", "habilit", "estatuto", "ata"))]
                vencidos = [d for d in cnds if d.data_validade and d.data_validade < hoje]
                if vencidos:
                    situacao = "DOCUMENTO VENCIDO"
                    validade = vencidos[0].data_validade
                    detalhe = f"{len(vencidos)} certidão(ões) com validade expirada."
                elif len(cnds) >= (3 if eh_formal else 1):
                    situacao = "DOCUMENTO REGULAR"
                    arquivo_url = cnds[0].filename
                    detalhe = f"{len(cnds)} documento(s) de habilitação anexado(s)."
                else:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = f"{len(cnds)} anexo(s) digital(is); verificar documentação completa de habilitação na pasta física da Chamada Pública."

            elif codigo == "COMPROV_ENTREGA":
                if not entregas_ex:
                    situacao = "DOCUMENTO NÃO APLICÁVEL" if not contratos_ex else "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = "Sem entregas registradas no exercício."
                else:
                    com_recibo = [e for e in entregas_ex if e.recibo_filename]
                    if len(com_recibo) == len(entregas_ex):
                        situacao = "DOCUMENTO REGULAR"
                        detalhe = f"Todas as {len(entregas_ex)} entrega(s) possuem comprovante anexado."
                    else:
                        situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                        detalhe = f"{len(com_recibo)}/{len(entregas_ex)} entrega(s) com arquivo digital anexado (conferir termos físicos)."

            elif codigo == "NOTAS_FISCAIS":
                if not entregas_ex:
                    situacao = "DOCUMENTO NÃO APLICÁVEL" if not contratos_ex else "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = "Sem entregas no exercício."
                else:
                    com_nf = [e for e in entregas_ex if (e.numero_nota_fiscal or "").strip()]
                    if len(com_nf) == len(entregas_ex):
                        situacao = "DOCUMENTO REGULAR"
                        detalhe = f"{len(com_nf)} entrega(s) com nº de Nota Fiscal registrado."
                    elif len(com_nf) == 0:
                        situacao = "DOCUMENTO AUSENTE"
                        detalhe = f"Nenhuma das {len(entregas_ex)} entrega(s) aprovadas possui número de Nota Fiscal registrado."
                    else:
                        situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                        detalhe = f"{len(entregas_ex) - len(com_nf)} de {len(entregas_ex)} entrega(s) sem número de Nota Fiscal vinculado."

            elif codigo == "COMPROV_PAGAMENTO":
                if not entregas_ex:
                    situacao = "DOCUMENTO NÃO APLICÁVEL"
                    detalhe = "Sem execução financeira no exercício."
                elif pagamentos_ex:
                    com_arq = [p for p in pagamentos_ex if p.comprovante_url or p.numero_ordem_bancaria]
                    if len(com_arq) == len(pagamentos_ex):
                        situacao = "DOCUMENTO REGULAR"
                        detalhe = f"{len(pagamentos_ex)} registro(s) de pagamento com ordem bancária/comprovante."
                    else:
                        situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                        detalhe = f"{len(pagamentos_ex) - len(com_arq)} pagamento(s) sem número de ordem bancária ou comprovante."
                else:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = "Registros de pagamento/tesouraria não vinculados ao módulo PNAE (conferir processo de pagamento na Tesouraria)."

            elif codigo == "RELACAO_PRODUTOS":
                itens_all = [it for c in contratos_ex for it in (c.itens or [])]
                if itens_all:
                    situacao = "DOCUMENTO REGULAR"
                    detalhe = f"{len(itens_all)} item(ns) de produto cadastrado(s) nos contratos."
                elif contratos_ex:
                    situacao = "DOCUMENTO AUSENTE"
                    detalhe = "Contrato sem itens de produtos cadastrados no Projeto de Venda."
                else:
                    situacao = "DOCUMENTO NÃO APLICÁVEL"
                    detalhe = "Sem contrato no exercício."

            elif codigo == "ORIGEM_PRODUCAO":
                anexo = _achar_anexo("produ", "origem", "declara")
                if anexo:
                    situacao = "DOCUMENTO REGULAR"
                    arquivo_url = anexo.filename
                    detalhe = "Declaração de produção própria anexada."
                elif eh_formal and (agricultor.associados_grupo or []):
                    com_pp = sum(1 for a in agricultor.associados_grupo if a.producao_propria)
                    situacao = "DOCUMENTO REGULAR" if com_pp == len(agricultor.associados_grupo) else "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = f"{com_pp}/{len(agricultor.associados_grupo)} associado(s) com produção própria confirmada."
                else:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = "Conferir Declaração de Produção Própria na pasta de habilitação da Chamada Pública."

            elif codigo == "CHAMADA_DOCS":
                sem_chamada = [c for c in contratos_ex if not (c.chamada_publica or "").strip()]
                if contratos_ex and not sem_chamada:
                    situacao = "DOCUMENTO REGULAR"
                    detalhe = "Todos os contratos vinculam a Chamada Pública correspondente."
                elif sem_chamada:
                    situacao = "DOCUMENTO PENDENTE DE CONFERÊNCIA"
                    detalhe = f"{len(sem_chamada)} contrato(s) sem número de Chamada Pública preenchido."
                else:
                    situacao = "DOCUMENTO NÃO APLICÁVEL"
                    detalhe = "Sem contratos no exercício."

        # Se houve conferência manual registrada na auditoria, prevalece a situação auditada
        if manual:
            situacao = manual.situacao
            if manual.observacao:
                detalhe = f"{manual.observacao} (Conferido por {manual.usuario_conferencia or 'Auditor'})"
            if manual.arquivo_url:
                arquivo_url = manual.arquivo_url
            if manual.data_validade:
                validade = manual.data_validade

        resultado.append(
            {
                "codigo_doc": codigo,
                "nome_documento": nome_doc,
                "situacao": situacao,
                "detalhe": detalhe,
                "arquivo_url": arquivo_url,
                "data_validade": validade,
                "conferido_manual": manual is not None,
            }
        )

    return resultado


# =============================================================================
# 4. MOTOR PRINCIPAL DE AUDITORIA ANALÍTICA (MODO SOMENTE LEITURA)
# =============================================================================

def executar_auditoria_pnae(
    exercicio=None,
    data_inicio=None,
    data_fim=None,
    filtro_fornecedor="",
    filtro_cpf_cnpj="",
    filtro_caf="",
    filtro_tipo="",
    filtro_associacao_id=None,
    filtro_situacao="",
    filtro_chamada="",
    filtro_contrato_id=None,
    incluir_comparativo=True,
):
    """
    Executa a análise analítica completa da Agricultura Familiar / PNAE no exercício selecionado.
    Retorna dicionário estruturado pronto para o Painel Web, Dossiê Individual, PDF e Excel.
    """
    executar_migracao_auditoria_pnae_segura()

    hoje = date.today()
    exercicio = int(exercicio or hoje.year)
    normas_ctx = _carregar_parametros_normativos()
    mapa_regras = normas_ctx["mapa_regras"]
    limite_ind = normas_ctx["limite_individual"]
    faixa_atencao = normas_ctx["alerta_amarelo"]

    # Carregamento otimizado (evita N+1 no Supabase)
    agricultores = (
        AgricultorFamiliar.query.options(
            joinedload(AgricultorFamiliar.documentos),
            joinedload(AgricultorFamiliar.associados_grupo),
            joinedload(AgricultorFamiliar.contratos).joinedload(ContratoPNAE.itens),
            joinedload(AgricultorFamiliar.contratos).joinedload(ContratoPNAE.entregas),
        )
        .order_by(AgricultorFamiliar.razao_social.asc())
        .all()
    )

    pagamentos_db = PnaePagamentoRegistro.query.filter_by(exercicio=exercicio).all()
    pagamentos_por_agr = defaultdict(list)
    pagamentos_por_contrato = defaultdict(list)
    for pg in pagamentos_db:
        if data_inicio and pg.data_pagamento < data_inicio:
            continue
        if data_fim and pg.data_pagamento > data_fim:
            continue
        pagamentos_por_agr[pg.agricultor_id].append(pg)
        if pg.contrato_id:
            pagamentos_por_contrato[pg.contrato_id].append(pg)

    docs_status_db = PnaeAuditoriaDocumentoStatus.query.filter_by(exercicio=exercicio).all()
    docs_status_map = {(d.agricultor_id, d.codigo_doc): d for d in docs_status_db}

    justificativas_db = PnaeAuditoriaJustificativa.query.filter_by(exercicio=exercicio).all()
    just_por_chave = {j.chave_pendencia: j for j in justificativas_db}

    # --- Passo A: Mapeia contratos e entregas do exercício SEM duplicidade ---
    contratos_por_agr = {}
    entregas_por_agr = {}
    entregas_por_contrato = {}
    chamadas_disponiveis = set()
    contratos_disponiveis = []

    for agr in agricultores:
        c_ex = []
        e_ex = []
        ids_entregas_vistas = set()

        for ct in (agr.contratos or []):
            if ct.chamada_publica and ct.chamada_publica.strip():
                chamadas_disponiveis.add(ct.chamada_publica.strip())

            # Um contrato pertence ao exercício se inicia no exercício, termina no exercício ou vigora nele
            pertence_ex = False
            if ct.data_inicio and ct.data_termino:
                pertence_ex = (ct.data_inicio.year <= exercicio <= ct.data_termino.year)
            elif ct.data_inicio:
                pertence_ex = (ct.data_inicio.year == exercicio)
            elif ct.data_termino:
                pertence_ex = (ct.data_termino.year == exercicio)

            # Entregas aprovadas no exercício vinculadas ao contrato
            entregas_ct_ex = []
            for ent in (ct.entregas or []):
                if ent.status != "Aprovado":
                    continue
                if ent.data_entrega and ent.data_entrega.year != exercicio:
                    continue
                if data_inicio and ent.data_entrega and ent.data_entrega < data_inicio:
                    continue
                if data_fim and ent.data_entrega and ent.data_entrega > data_fim:
                    continue
                if ent.id not in ids_entregas_vistas:
                    ids_entregas_vistas.add(ent.id)
                    entregas_ct_ex.append(ent)

            if pertence_ex or entregas_ct_ex:
                if filtro_chamada and (ct.chamada_publica or "").strip().lower() != filtro_chamada.strip().lower():
                    continue
                if filtro_contrato_id and ct.id != int(filtro_contrato_id):
                    continue
                c_ex.append(ct)
                contratos_disponiveis.append(ct)
                entregas_por_contrato[ct.id] = entregas_ct_ex
                e_ex.extend(entregas_ct_ex)

        contratos_por_agr[agr.id] = c_ex
        entregas_por_agr[agr.id] = e_ex

    # --- Passo B: Consolidação por CAF para detectar CAFs compartilhados / duplicados entre cadastros ---
    caf_para_agricultores = defaultdict(list)
    caf_totais = defaultdict(lambda: {"contratado": 0.0, "executado": 0.0, "faturado": 0.0, "pago": 0.0, "ids": []})

    for agr in agricultores:
        caf_norm = normalizar_caf(agr.dap_caf_numero)
        if caf_norm and not is_grupo_formal(agr.tipo_fornecedor):
            caf_para_agricultores[caf_norm].append(agr)
            v_cont = sum(float(c.valor_total or 0.0) for c in contratos_por_agr.get(agr.id, []))
            v_exec = sum(float(e.valor_total or 0.0) for e in entregas_por_agr.get(agr.id, []))
            v_fat = sum(float(e.valor_total or 0.0) for e in entregas_por_agr.get(agr.id, []) if (e.numero_nota_fiscal or "").strip())
            v_pag = sum(float(p.valor_pago or 0.0) for p in pagamentos_por_agr.get(agr.id, []))
            caf_totais[caf_norm]["contratado"] += v_cont
            caf_totais[caf_norm]["executado"] += v_exec
            caf_totais[caf_norm]["faturado"] += v_fat
            caf_totais[caf_norm]["pago"] += v_pag
            caf_totais[caf_norm]["ids"].append(agr.id)

    # --- Passo C: Análise Individual Minuciosa por Fornecedor ---
    analises_fornecedores = []
    analises_associacoes = []
    todas_pendencias = []
    evolucao_mensal = {m: {"executado": 0.0, "faturado": 0.0, "pago": 0.0} for m in range(1, 13)}

    for agr in agricultores:
        contratos_ex = contratos_por_agr.get(agr.id, [])
        entregas_ex = entregas_por_agr.get(agr.id, [])
        pagamentos_ex = pagamentos_por_agr.get(agr.id, [])

        # Se filtro de chamada ou contrato estiver ativo, ignora fornecedores sem contratos correspondentes
        if (filtro_chamada or filtro_contrato_id) and not contratos_ex:
            continue

        eh_formal = is_grupo_formal(agr.tipo_fornecedor)
        eh_informal = is_grupo_informal(agr.tipo_fornecedor)
        eh_individual = not eh_formal and not eh_informal

        cpf_limpo = limpar_digitos(agr.cpf_cnpj)
        caf_norm = normalizar_caf(agr.dap_caf_numero)

        valor_contratado = round(sum(float(c.valor_total or 0.0) for c in contratos_ex), 2)
        valor_executado = round(sum(float(e.valor_total or 0.0) for e in entregas_ex), 2)
        valor_faturado = round(sum(float(e.valor_total or 0.0) for e in entregas_ex if (e.numero_nota_fiscal or "").strip()), 2)
        valor_pago = round(sum(float(p.valor_pago or 0.0) for p in pagamentos_ex), 2)
        saldo_contratual = round(valor_contratado - valor_executado, 2)

        # Acumula evolução mensal
        for ent in entregas_ex:
            if ent.data_entrega:
                m_idx = ent.data_entrega.month
                evolucao_mensal[m_idx]["executado"] += float(ent.valor_total or 0.0)
                if (ent.numero_nota_fiscal or "").strip():
                    evolucao_mensal[m_idx]["faturado"] += float(ent.valor_total or 0.0)
        for pg in pagamentos_ex:
            if pg.data_pagamento:
                evolucao_mensal[pg.data_pagamento.month]["pago"] += float(pg.valor_pago or 0.0)

        pendencias_forn = []

        # ------------------------------------------------------------------
        # C.1 — REGRA DE LIMITE DE COMERCIALIZAÇÃO (INDIVIDUAL vs ASSOCIAÇÃO)
        # ------------------------------------------------------------------
        limite_aplicavel = None
        excedente_executado = 0.0
        excedente_contratado = 0.0
        status_limite = "CINZA"
        label_limite = "DADOS INSUFICIENTES"
        percentual_limite = 0.0
        valor_base_caf_consolidado = valor_executado
        caf_duplicado = False
        info_associacao = None

        if eh_formal:
            associados = agr.associados_grupo or []
            elegiveis = [a for a in associados if a.is_elegivel(hoje)]
            naf = len(elegiveis)
            vmc = round(naf * limite_ind, 2) if naf > 0 else None
            limite_aplicavel = vmc

            info_associacao = {
                "agricultor_id": agr.id,
                "razao_social": agr.razao_social,
                "cnpj": formatar_cpf_cnpj(agr.cpf_cnpj),
                "caf_pj": agr.dap_caf_numero or "Não informado",
                "caf_pj_validade": agr.dap_caf_validade,
                "total_associados": len(associados),
                "naf_elegiveis": naf,
                "inelegiveis": len(associados) - naf,
                "vmc_calculado": vmc,
                "valor_contratado": valor_contratado,
                "valor_executado": valor_executado,
                "valor_faturado": valor_faturado,
                "valor_pago": valor_pago,
                "associados": associados,
                "alertas_associacao": [],
            }

            if len(cpf_limpo) != 14:
                al = _criar_alerta(
                    exercicio, agr, None, "Associação / Grupo Formal", "LIM-GF-VMC", mapa_regras,
                    "CINZA", "MEDIO",
                    "CNPJ da associação/cooperativa ausente ou inválido no cadastro.",
                    f"Fornecedor #{agr.id} ({agr.razao_social}) — CPF/CNPJ cadastrado: {agr.cpf_cnpj or 'vazio'}.",
                    "Grupo Formal exige CNPJ válido (14 dígitos).",
                    "Impossibilidade de validação cadastral da pessoa jurídica.",
                    "Atualizar o CNPJ oficial da cooperativa/associação conforme Cartão CNPJ e CAF PJ.",
                    setor_responsavel="Coordenação do PNAE / Compras", prazo_sugerido="10 dias", chave_extra="CNPJ_INVALIDO"
                )
                pendencias_forn.append(al)
                info_associacao["alertas_associacao"].append(al)

            if not caf_norm:
                al = _criar_alerta(
                    exercicio, agr, None, "Associação / Grupo Formal", "LIM-GF-VMC", mapa_regras,
                    "LARANJA", "ALTO",
                    "CAF Pessoa Jurídica não cadastrado para a associação/cooperativa.",
                    f"Fornecedor #{agr.id} ({agr.razao_social}).",
                    "VMC depende de CAF PJ ativo.",
                    "Risco de inabilitação ou ausência de comprovação da condição de Grupo Formal.",
                    "Anexar extrato ativo do CAF Pessoa Jurídica e registrar número e validade no sistema.",
                    prazo_sugerido="10 dias", chave_extra="SEM_CAF_PJ"
                )
                pendencias_forn.append(al)
                info_associacao["alertas_associacao"].append(al)
            elif agr.dap_caf_validade and agr.dap_caf_validade < hoje:
                al = _criar_alerta(
                    exercicio, agr, None, "Associação / Grupo Formal", "LIM-GF-VMC", mapa_regras,
                    "LARANJA", "ALTO",
                    f"CAF Pessoa Jurídica vencido em {agr.dap_caf_validade.strftime('%d/%m/%Y')}.",
                    f"CAF PJ {caf_norm} — validade {agr.dap_caf_validade.strftime('%d/%m/%Y')}.",
                    f"Vencido há {(hoje - agr.dap_caf_validade).days} dia(s).",
                    "Necessidade de conferência da vigência do CAF PJ na data da chamada pública e das entregas.",
                    "Solicitar extrato atualizado do CAF Pessoa Jurídica junto à organização fornecedora.",
                    prazo_sugerido="10 dias", chave_extra="CAF_PJ_VENCIDO"
                )
                pendencias_forn.append(al)
                info_associacao["alertas_associacao"].append(al)

            if len(associados) == 0:
                status_limite = "CINZA"
                label_limite = "DADOS INSUFICIENTES (SEM ASSOCIADOS)"
                al = _criar_alerta(
                    exercicio, agr, None, "Associação / Grupo Formal", "LIM-GF-VMC", mapa_regras,
                    "CINZA", "ALTO",
                    "Ausência de relação de agricultores associados para cálculo normativo do VMC (VMC = NAF × R$ 40.000,00).",
                    f"Grupo Formal {agr.razao_social}: 0 associados cadastrados; Contratado={formatar_moeda_br(valor_contratado)}; Executado={formatar_moeda_br(valor_executado)}.",
                    "Não é possível presumir limite de R$ 40.000,00 nem validar a capacidade de comercialização sem NAF comprovado.",
                    "Impossibilidade de certificar se o valor contratado/executado respeita o Valor Máximo de Comercialização (VMC).",
                    "Cadastrar no Dossiê da Associação a relação de cooperados do Projeto de Venda com CAF PF válido, vínculo no CAF PJ e produção própria.",
                    setor_responsavel="Coordenação do PNAE / CAE", prazo_sugerido="15 dias", chave_extra="SEM_ASSOCIADOS"
                )
                pendencias_forn.append(al)
                info_associacao["alertas_associacao"].append(al)
            else:
                # Verifica problemas específicos nos associados
                sem_caf_pf = [a for a in associados if not (a.caf_pf_numero or "").strip() or (a.caf_pf_validade and a.caf_pf_validade < hoje)]
                sem_vinculo = [a for a in associados if not a.inscrito_caf_pj]
                sem_pv = [a for a in associados if not a.consta_projeto_venda]
                sem_pp = [a for a in associados if not a.producao_propria]

                if sem_caf_pf:
                    al = _criar_alerta(
                        exercicio, agr, None, "Associação / Grupo Formal", "LIM-GF-VMC", mapa_regras,
                        "AMARELO", "MEDIO",
                        f"{len(sem_caf_pf)} associado(s) sem CAF Pessoa Física válido/vigente.",
                        f"Associados: {', '.join(a.nome for a in sem_caf_pf[:5])}.",
                        f"Excluídos do NAF elegível ({naf}/{len(associados)} elegíveis).",
                        "Redução do NAF elegível e possível impacto no VMC da associação.",
                        "Solicitar atualização do CAF Pessoa Física dos cooperados pendentes.",
                        chave_extra="ASSOC_SEM_CAF_PF"
                    )
                    pendencias_forn.append(al)
                    info_associacao["alertas_associacao"].append(al)
                if sem_vinculo:
                    al = _criar_alerta(
                        exercicio, agr, None, "Associação / Grupo Formal", "LIM-GF-VMC", mapa_regras,
                        "AMARELO", "MEDIO",
                        f"{len(sem_vinculo)} associado(s) sem comprovação de inscrição no CAF Pessoa Jurídica.",
                        f"Associados: {', '.join(a.nome for a in sem_vinculo[:5])}.",
                        f"Não computados no NAF (NAF atual = {naf}).",
                        "Participante sem vínculo comprovado ao CAF PJ não pode ampliar o VMC.",
                        "Conferir o extrato do CAF Jurídico para confirmar o vínculo dos cooperados.",
                        chave_extra="ASSOC_SEM_VINCULO_PJ"
                    )
                    pendencias_forn.append(al)
                    info_associacao["alertas_associacao"].append(al)
                if sem_pv or sem_pp:
                    al = _criar_alerta(
                        exercicio, agr, None, "Associação / Grupo Formal", "LIM-GF-VMC", mapa_regras,
                        "AMARELO", "MEDIO",
                        f"Pendência de comprovação no Projeto de Venda ({len(sem_pv)}) ou Produção Própria ({len(sem_pp)}) entre associados.",
                        f"Total de associados cadastrados: {len(associados)}; Elegíveis (NAF): {naf}.",
                        f"VMC calculado considerando estritamente NAF = {naf} → {formatar_moeda_br(vmc or 0)}.",
                        "Somente produtores constantes no Projeto de Venda com produção própria compõem o NAF.",
                        "Conferir a relação nominal no Projeto de Venda e declarações de produção própria.",
                        chave_extra="ASSOC_SEM_PV_PP"
                    )
                    pendencias_forn.append(al)
                    info_associacao["alertas_associacao"].append(al)

                if vmc is None or vmc <= 0:
                    status_limite = "CINZA"
                    label_limite = "DADOS INSUFICIENTES (NAF = 0)"
                else:
                    percentual_limite = round((valor_executado / vmc) * 100.0, 2)
                    excedente_executado = round(max(0.0, valor_executado - vmc), 2)
                    excedente_contratado = round(max(0.0, valor_contratado - vmc), 2)

                    if valor_executado > vmc:
                        status_limite = "VERMELHO"
                        label_limite = "VMC ULTRAPASSADO"
                        al = _criar_alerta(
                            exercicio, agr, None, "Limite de Comercialização (Associação)", "LIM-GF-VMC", mapa_regras,
                            "VERMELHO", "CRITICO",
                            f"Valor executado ultrapassou o VMC calculado da associação em {formatar_moeda_br(excedente_executado)}.",
                            f"Grupo Formal {agr.razao_social} | NAF={naf} | Contratado={formatar_moeda_br(valor_contratado)} | Executado={formatar_moeda_br(valor_executado)}.",
                            f"VMC = {naf} × {formatar_moeda_br(limite_ind)} = {formatar_moeda_br(vmc)} | Excedente executado = {formatar_moeda_br(excedente_executado)}.",
                            "Indício relevante de extrapolação do Valor Máximo de Comercialização previsto na Res. CD/FNDE nº 4/2026, art. 38.",
                            "Revisar imediatamente o NAF comprovado no Projeto de Venda, as notas fiscais emitidas e o controle individual de cooperados sob responsabilidade da cooperativa e da EEx.",
                            setor_responsavel="SEME / Controle Interno / CAE", prazo_sugerido="5 dias",
                            valor_diferenca=excedente_executado, chave_extra="VMC_EXEC_EXC"
                        )
                        pendencias_forn.append(al)
                        info_associacao["alertas_associacao"].append(al)
                    elif valor_contratado > vmc:
                        status_limite = "LARANJA"
                        label_limite = "CONTRATAÇÃO ACIMA DO VMC"
                        al = _criar_alerta(
                            exercicio, agr, None, "Limite de Comercialização (Associação)", "LIM-GF-VMC", mapa_regras,
                            "LARANJA", "ALTO",
                            f"Valor contratado ({formatar_moeda_br(valor_contratado)}) supera o VMC calculado ({formatar_moeda_br(vmc)}) em {formatar_moeda_br(excedente_contratado)}.",
                            f"Grupo Formal {agr.razao_social} | NAF={naf} | Contratado={formatar_moeda_br(valor_contratado)}.",
                            f"VMC = {naf} × {formatar_moeda_br(limite_ind)} = {formatar_moeda_br(vmc)} | Excesso contratual = {formatar_moeda_br(excedente_contratado)}.",
                            "Risco iminente de execução acima do teto normativo; a EEx é responsável pelo controle do limite total contratado.",
                            "Conferir se há cooperados elegíveis ainda não cadastrados ou adequar/remanejar o limite contratual antes de novas entregas.",
                            setor_responsavel="SEME / Compras / PNAE", prazo_sugerido="10 dias",
                            valor_diferenca=excedente_contratado, chave_extra="VMC_CONT_EXC"
                        )
                        pendencias_forn.append(al)
                        info_associacao["alertas_associacao"].append(al)
                    elif percentual_limite >= 87.5:
                        status_limite = "AMARELO"
                        label_limite = "ATENÇÃO (PRÓXIMO AO VMC)"
                    else:
                        status_limite = "VERDE"
                        label_limite = "REGULAR"

            info_associacao["status_limite"] = status_limite
            info_associacao["label_limite"] = label_limite
            info_associacao["excedente_executado"] = excedente_executado
            info_associacao["excedente_contratado"] = excedente_contratado
            analises_associacoes.append(info_associacao)

        else:
            # AGRICULTOR INDIVIDUAL OU GRUPO INFORMAL -> Limite individual R$ 40.000,00 por CAF/NIS
            limite_aplicavel = limite_ind
            if not cpf_limpo or not caf_norm:
                status_limite = "CINZA"
                label_limite = "DADOS INSUFICIENTES"
                faltando = []
                if not cpf_limpo:
                    faltando.append("CPF")
                if not caf_norm:
                    faltando.append("CAF/DAP")
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, None, "Limite Individual (CAF)", "LIM-IND-40K", mapa_regras,
                        "CINZA", "ALTO",
                        f"Dados cadastrais obrigatórios ausentes ({', '.join(faltando)}) para validação do limite individual de R$ 40.000,00.",
                        f"Fornecedor #{agr.id} ({agr.razao_social}) | Contratado={formatar_moeda_br(valor_contratado)} | Executado={formatar_moeda_br(valor_executado)}.",
                        "Sem CPF e CAF cadastrados não é possível consolidar vendas por UFPA/CAF na EEx.",
                        "Impossibilidade de certificar conformidade com o art. 38 da Res. CD/FNDE nº 4/2026.",
                        f"Completar o cadastro com {', '.join(faltando)} válido e anexar o extrato do CAF.",
                        prazo_sugerido="10 dias", chave_extra="DADOS_INSUF_CAF"
                    )
                )
            else:
                # Verifica se o mesmo CAF aparece em mais de um cadastro de agricultor
                mesmo_caf_lista = caf_para_agricultores.get(caf_norm, [])
                if len(mesmo_caf_lista) > 1:
                    caf_duplicado = True
                    valor_base_caf_consolidado = round(caf_totais[caf_norm]["executado"], 2)
                    nomes_dup = ", ".join(a.razao_social for a in mesmo_caf_lista)
                    pendencias_forn.append(
                        _criar_alerta(
                            exercicio, agr, None, "Duplicidade de CAF", "LIM-IND-40K", mapa_regras,
                            "LARANJA", "ALTO",
                            f"Mesmo número de CAF ({caf_norm}) vinculado a {len(mesmo_caf_lista)} cadastros distintos na EEx.",
                            f"Cadastros compartilhando CAF {caf_norm}: {nomes_dup}.",
                            f"Executado consolidado no CAF {caf_norm} = {formatar_moeda_br(valor_base_caf_consolidado)} (Limite UFPA/CAF: {formatar_moeda_br(limite_ind)}).",
                            "O teto de R$ 40.000,00 é por CAF/UFPA por ano civil; cadastros múltiplos podem mascarar extrapolação do limite.",
                            "Unificar ou vincular os cadastros da mesma UFPA e somar integralmente todas as entregas sob o mesmo CAF.",
                            setor_responsavel="Coordenação do PNAE / Controle Interno", prazo_sugerido="10 dias",
                            valor_diferenca=max(0.0, valor_base_caf_consolidado - limite_ind), chave_extra=f"CAF_DUP_{caf_norm}"
                        )
                    )
                else:
                    valor_base_caf_consolidado = valor_executado

                valor_contratado_caf = round(caf_totais[caf_norm]["contratado"], 2) if caf_duplicado else valor_contratado
                percentual_limite = round((valor_base_caf_consolidado / limite_ind) * 100.0, 2) if limite_ind > 0 else 0.0
                excedente_executado = round(max(0.0, valor_base_caf_consolidado - limite_ind), 2)
                excedente_contratado = round(max(0.0, valor_contratado_caf - limite_ind), 2)

                if valor_base_caf_consolidado > limite_ind:
                    status_limite = "VERMELHO"
                    label_limite = "LIMITE ULTRAPASSADO"
                    pendencias_forn.append(
                        _criar_alerta(
                            exercicio, agr, None, "Limite Individual de Comercialização", "LIM-IND-40K", mapa_regras,
                            "VERMELHO", "CRITICO",
                            f"ALERTA CRÍTICO — Limite individual de comercialização ultrapassado em {formatar_moeda_br(excedente_executado)}.",
                            f"Agricultor: {agr.razao_social} | CPF: {formatar_cpf_cnpj(agr.cpf_cnpj)} | CAF: {caf_norm} | Contratos no exercício: {len(contratos_ex)} | Contratado: {formatar_moeda_br(valor_contratado)} | Executado: {formatar_moeda_br(valor_base_caf_consolidado)} | Faturado: {formatar_moeda_br(valor_faturado)} | Pago: {formatar_moeda_br(valor_pago)}.",
                            f"Executado ({formatar_moeda_br(valor_base_caf_consolidado)}) − Limite normativo ({formatar_moeda_br(limite_ind)}) = Excedente de {formatar_moeda_br(excedente_executado)} ({percentual_limite:.1f}% do teto).",
                            "Indício relevante de descumprimento do teto legal de R$ 40.000,00 por CAF/ano civil/EEx (Res. CD/FNDE nº 4/2026, art. 38), passível de glosa na prestação de contas do PNAE.",
                            "Realizar conferência imediata das notas fiscais, entregas, contratos e CAF do fornecedor, identificando a origem do excedente (erro de lançamento, duplicidade ou comercialização acima do teto) e adotando as providências administrativas cabíveis junto à SEME e ao Controle Interno.",
                            setor_responsavel="SEME / Controle Interno / CAE", prazo_sugerido="5 dias",
                            valor_diferenca=excedente_executado, chave_extra="LIM_IND_ULTRAPASSADO"
                        )
                    )
                elif valor_base_caf_consolidado >= faixa_atencao:
                    status_limite = "AMARELO"
                    label_limite = "ATENÇÃO (PRÓXIMO AO LIMITE)"
                    saldo_ate_teto = round(limite_ind - valor_base_caf_consolidado, 2)
                    pendencias_forn.append(
                        _criar_alerta(
                            exercicio, agr, None, "Limite Individual de Comercialização", "LIM-IND-40K", mapa_regras,
                            "AMARELO", "MEDIO",
                            f"Execução próxima ao limite individual de R$ 40.000,00 (utilizado {percentual_limite:.1f}%; saldo disponível até o teto: {formatar_moeda_br(saldo_ate_teto)}).",
                            f"Agricultor: {agr.razao_social} | CAF: {caf_norm} | Executado: {formatar_moeda_br(valor_base_caf_consolidado)}.",
                            f"{formatar_moeda_br(limite_ind)} − {formatar_moeda_br(valor_base_caf_consolidado)} = {formatar_moeda_br(saldo_ate_teto)} restantes no teto anual.",
                            "Risco de ultrapassar o limite normativo nas próximas entregas do exercício.",
                            "Monitorar estritamente as próximas ordens de fornecimento e notas fiscais deste CAF para não exceder R$ 40.000,00.",
                            setor_responsavel="Coordenação do PNAE", prazo_sugerido="Monitoramento contínuo",
                            valor_diferenca=0.0, chave_extra="LIM_IND_ATENCAO"
                        )
                    )
                else:
                    status_limite = "VERDE"
                    label_limite = "REGULAR"

                # Alerta separado se o CONTRATADO exceder R$ 40.000 mesmo quando o EXECUTADO ainda não excedeu
                if valor_contratado_caf > limite_ind and valor_base_caf_consolidado <= limite_ind:
                    pendencias_forn.append(
                        _criar_alerta(
                            exercicio, agr, None, "Excesso de Contratação", "LIM-IND-40K", mapa_regras,
                            "LARANJA", "ALTO",
                            f"Valor contratado ({formatar_moeda_br(valor_contratado_caf)}) supera o limite individual de {formatar_moeda_br(limite_ind)} em {formatar_moeda_br(excedente_contratado)}.",
                            f"Agricultor: {agr.razao_social} | CAF: {caf_norm} | {len(contratos_ex)} contrato(s) no exercício.",
                            f"Contratado ({formatar_moeda_br(valor_contratado_caf)}) − Limite ({formatar_moeda_br(limite_ind)}) = {formatar_moeda_br(excedente_contratado)} (Executado atual: {formatar_moeda_br(valor_base_caf_consolidado)}).",
                            "Risco administrativo de execução acima do teto legal caso o contrato seja integralmente demandado.",
                            "Restringir a execução efetiva ao limite de R$ 40.000,00 e avaliar termo de supressão/adequação contratual.",
                            setor_responsavel="SEME / Compras / Jurídico", prazo_sugerido="10 dias",
                            valor_diferenca=excedente_contratado, chave_extra="LIM_IND_CONTRATO_EXC"
                        )
                    )

                # Alerta de CAF vencido (para individual/informal)
                if agr.dap_caf_validade and agr.dap_caf_validade < hoje and (contratos_ex or entregas_ex):
                    pendencias_forn.append(
                        _criar_alerta(
                            exercicio, agr, None, "Documentação / CAF", "DOC-HAB", mapa_regras,
                            "LARANJA", "ALTO",
                            f"CAF/DAP ({caf_norm}) com validade expirada em {agr.dap_caf_validade.strftime('%d/%m/%Y')}.",
                            f"Fornecedor #{agr.id} ({agr.razao_social}) | Contratos ativos: {len(contratos_ex)}.",
                            f"Expirado há {(hoje - agr.dap_caf_validade).days} dia(s).",
                            "Necessidade de confirmar se o CAF estava vigente na assinatura do contrato e solicitar renovação.",
                            "Requisitar novo extrato de CAF ativo junto ao agricultor e atualizar o prontuário digital.",
                            prazo_sugerido="15 dias", chave_extra="CAF_PF_VENCIDO"
                        )
                    )

            # Alerta informativo quando agricultor possui múltiplos contratos no mesmo exercício
            if len(contratos_ex) > 1:
                nums = ", ".join(c.numero_contrato for c in contratos_ex)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, None, "Múltiplos Contratos no Exercício", "LIM-IND-40K", mapa_regras,
                        "AMARELO", "MEDIO",
                        f"Agricultor possui {len(contratos_ex)} contratos no exercício {exercicio} ({nums}).",
                        f"Contratos consolidados: {nums} | Soma contratada: {formatar_moeda_br(valor_contratado)} | Soma executada: {formatar_moeda_br(valor_executado)}.",
                        "Os limites do art. 38 aplicam-se sobre a soma de todos os contratos do mesmo CAF no ano civil.",
                        "Risco operacional de controle isolado por contrato ultrapassar o teto global do CAF.",
                        "Manter controle unificado por CAF nas ordens de fornecimento de todos os contratos.",
                        prazo_sugerido="Monitoramento contínuo", chave_extra="MULTIPLOS_CONTRATOS"
                    )
                )

        # ------------------------------------------------------------------
        # C.2 — AUDITORIA FINANCEIRA E CONTRATUAL (12 VERIFICAÇÕES — SEÇÃO 6)
        # ------------------------------------------------------------------
        nfs_vistas_forn = defaultdict(list)

        for ct in contratos_ex:
            ent_ct = entregas_por_contrato.get(ct.id, [])
            pag_ct = pagamentos_por_contrato.get(ct.id, [])
            v_ct = round(float(ct.valor_total or 0.0), 2)
            v_exec_ct = round(sum(float(e.valor_total or 0.0) for e in ent_ct), 2)
            v_fat_ct = round(sum(float(e.valor_total or 0.0) for e in ent_ct if (e.numero_nota_fiscal or "").strip()), 2)
            v_pag_ct = round(sum(float(p.valor_pago or 0.0) for p in pag_ct), 2)
            saldo_ct = round(v_ct - v_exec_ct, 2)

            # (1) Contrato isolado acima do limite individual aplicável (se individual)
            if not eh_formal and v_ct > limite_ind:
                dif = round(v_ct - limite_ind, 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Contratual", "LIM-IND-40K", mapa_regras,
                        "LARANJA", "ALTO",
                        f"Contrato {ct.numero_contrato} possui valor ({formatar_moeda_br(v_ct)}) superior ao limite individual de {formatar_moeda_br(limite_ind)}.",
                        f"Contrato #{ct.id} ({ct.numero_contrato}) | Vigência: {ct.data_inicio} a {ct.data_termino}.",
                        f"{formatar_moeda_br(v_ct)} − {formatar_moeda_br(limite_ind)} = Excesso contratual de {formatar_moeda_br(dif)}.",
                        "Contratação acima do teto individual do PNAE por CAF/EEx.",
                        "Revisar o valor contratual e limitar a execução/empenho ao teto normativo.",
                        valor_diferenca=dif, chave_extra=f"CT_ACIMA_LIM_{ct.id}"
                    )
                )

            # (2) e (3) Execução superior ao valor contratado / Saldo negativo
            if v_exec_ct > v_ct + 0.01:
                dif = round(v_exec_ct - v_ct, 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Contratual", "EXEC-FIN", mapa_regras,
                        "VERMELHO", "CRITICO",
                        f"Execução ({formatar_moeda_br(v_exec_ct)}) superior ao valor contratado ({formatar_moeda_br(v_ct)}) no contrato {ct.numero_contrato} — saldo negativo de {formatar_moeda_br(saldo_ct)}.",
                        f"Contrato #{ct.id} ({ct.numero_contrato}) | {len(ent_ct)} entrega(s) aprovada(s).",
                        f"Contratado ({formatar_moeda_br(v_ct)}) − Executado ({formatar_moeda_br(v_exec_ct)}) = {formatar_moeda_br(saldo_ct)} (diferença: {formatar_moeda_br(dif)}).",
                        "Inconsistência financeira relevante: saldo negativo pode decorrer de entrega sem cobertura contratual, aditivo não cadastrado ou lançamento duplicado.",
                        "Conferir todas as entregas do contrato, verificar existência de termo aditivo e estornar eventuais duplicidades.",
                        setor_responsavel="SEME / Controle Interno / Contabilidade", prazo_sugerido="5 dias",
                        valor_diferenca=dif, chave_extra=f"EXEC_ACIMA_CT_{ct.id}"
                    )
                )

            # (4) Pagamento superior ao valor das notas fiscais (faturado)
            if v_pag_ct > 0 and v_pag_ct > v_fat_ct + 0.01:
                dif = round(v_pag_ct - v_fat_ct, 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Contratual", "EXEC-FIN", mapa_regras,
                        "VERMELHO", "CRITICO",
                        f"Valor pago ({formatar_moeda_br(v_pag_ct)}) excede o valor faturado em notas fiscais ({formatar_moeda_br(v_fat_ct)}) no contrato {ct.numero_contrato}.",
                        f"Contrato #{ct.id} ({ct.numero_contrato}) | {len(pag_ct)} pagamento(s) registrado(s).",
                        f"Pago ({formatar_moeda_br(v_pag_ct)}) − Faturado ({formatar_moeda_br(v_fat_ct)}) = {formatar_moeda_br(dif)}.",
                        "Indício de pagamento sem lastro integral em nota fiscal cadastrada (art. 63, Lei nº 4.320/1964).",
                        "Conciliar ordens bancárias com as notas fiscais liquidadas na Tesouraria.",
                        setor_responsavel="Tesouraria / Contabilidade / SEME", prazo_sugerido="5 dias",
                        valor_diferenca=dif, chave_extra=f"PAG_ACIMA_NF_{ct.id}"
                    )
                )

            # (11) Contrato sem identificação de chamada pública ou vigência inconsistente
            if not (ct.chamada_publica or "").strip():
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Planejamento / Chamada Pública", "CHAM-PUB", mapa_regras,
                        "AMARELO", "MEDIO",
                        f"Contrato {ct.numero_contrato} sem identificação do número da Chamada Pública.",
                        f"Contrato #{ct.id} ({ct.numero_contrato}) — campo chamada_publica vazio.",
                        "Ausência de vinculação formal ao edital de Chamada Pública.",
                        "Fragilidade de rastreabilidade do processo de dispensa de licitação (art. 30, Res. CD/FNDE nº 4/2026).",
                        "Informar o número/ano da Chamada Pública no cadastro do contrato.",
                        prazo_sugerido="15 dias", chave_extra=f"SEM_CHAMADA_{ct.id}"
                    )
                )

            if not ct.data_inicio or not ct.data_termino or ct.data_termino < ct.data_inicio:
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Contratual", "EXEC-FIN", mapa_regras,
                        "LARANJA", "ALTO",
                        f"Vigência contratual ausente ou inconsistente no contrato {ct.numero_contrato}.",
                        f"Contrato #{ct.id}: início={ct.data_inicio}, término={ct.data_termino}.",
                        "Data de término anterior à data de início ou ausente.",
                        "Impossibilita validar a tempestividade das entregas no exercício.",
                        "Corrigir as datas de vigência conforme o contrato assinado.",
                        prazo_sugerido="10 dias", chave_extra=f"VIG_INCONS_{ct.id}"
                    )
                )

            # Verifica entregas individuais do contrato
            ent_sem_nf = []
            ent_fora_vigencia = []
            ent_sem_itens = []
            assinaturas_ent = defaultdict(list)

            for ent in ent_ct:
                nf_num = (ent.numero_nota_fiscal or "").strip()
                if not nf_num:
                    ent_sem_nf.append(ent)
                else:
                    nfs_vistas_forn[nf_num.upper()].append((ct.numero_contrato, ent))

                if ct.data_termino and ent.data_entrega and ent.data_entrega > ct.data_termino:
                    ent_fora_vigencia.append(ent)
                elif ct.data_inicio and ent.data_entrega and ent.data_entrega < ct.data_inicio:
                    ent_fora_vigencia.append(ent)

                # Verifica se itens_json está vazio (entrega sem detalhamento / nota sem entrega detalhada)
                itens_ok = False
                if ent.itens_json:
                    try:
                        parsed_it = json.loads(ent.itens_json)
                        itens_ok = isinstance(parsed_it, list) and len(parsed_it) > 0
                    except Exception:
                        itens_ok = False
                if not itens_ok:
                    ent_sem_itens.append(ent)

                # Detecção de possível duplicidade de lançamento (mesmo contrato + mesma data + mesmo valor)
                if ent.data_entrega and (ent.valor_total or 0) > 0:
                    chave_dup = (ent.data_entrega.isoformat(), round(float(ent.valor_total), 2))
                    assinaturas_ent[chave_dup].append(ent)

            # (6) Entregas sem documentação fiscal vinculada
            if ent_sem_nf:
                val_sem_nf = round(sum(float(e.valor_total or 0.0) for e in ent_sem_nf), 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Fiscal", "EXEC-FIN", mapa_regras,
                        "LARANJA", "ALTO",
                        f"{len(ent_sem_nf)} entrega(s) aprovada(s) no contrato {ct.numero_contrato} sem número de Nota Fiscal vinculado ({formatar_moeda_br(val_sem_nf)}).",
                        f"Entregas IDs: {', '.join('#' + str(e.id) for e in ent_sem_nf[:8])}.",
                        f"Valor executado sem documentação fiscal registrada = {formatar_moeda_br(val_sem_nf)}.",
                        "Ausência de comprovação fiscal vinculada impede a correta liquidação da despesa e prestação de contas ao FNDE.",
                        "Vincular os números e arquivos das Notas Fiscais às entregas correspondentes.",
                        valor_diferenca=val_sem_nf, prazo_sugerido="10 dias", chave_extra=f"ENT_SEM_NF_{ct.id}"
                    )
                )

            # (8) Contratos vencidos com execução registrada
            if ent_fora_vigencia:
                val_fv = round(sum(float(e.valor_total or 0.0) for e in ent_fora_vigencia), 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Contratual", "EXEC-FIN", mapa_regras,
                        "VERMELHO", "CRITICO",
                        f"{len(ent_fora_vigencia)} entrega(s) registrada(s) fora do período de vigência do contrato {ct.numero_contrato} ({formatar_moeda_br(val_fv)}).",
                        f"Vigência: {ct.data_inicio.strftime('%d/%m/%Y') if ct.data_inicio else '?'} a {ct.data_termino.strftime('%d/%m/%Y') if ct.data_termino else '?'} | Entregas: {', '.join(e.data_entrega.strftime('%d/%m/%Y') for e in ent_fora_vigencia[:6])}.",
                        f"Valor entregue fora da vigência contratual = {formatar_moeda_br(val_fv)}.",
                        "Execução sem cobertura contratual vigente ou ausência de registro de termo aditivo de prorrogação.",
                        "Verificar existência de termo aditivo de prazo ou corrigir data de entrega se houve erro de digitação.",
                        setor_responsavel="SEME / Jurídico / Controle Interno", prazo_sugerido="5 dias",
                        valor_diferenca=val_fv, chave_extra=f"ENT_FORA_VIG_{ct.id}"
                    )
                )

            # (5) Entregas/Notas sem itens discriminados
            if ent_sem_itens:
                val_si = round(sum(float(e.valor_total or 0.0) for e in ent_sem_itens), 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Entrega", "EXEC-FIN", mapa_regras,
                        "AMARELO", "MEDIO",
                        f"{len(ent_sem_itens)} entrega(s) no contrato {ct.numero_contrato} sem detalhamento de itens entregues.",
                        f"Entregas IDs: {', '.join('#' + str(e.id) for e in ent_sem_itens[:8])}.",
                        f"Valor sem discriminação de produtos = {formatar_moeda_br(val_si)}.",
                        "Dificulta a conferência de quantidades por gênero alimentício contra o Projeto de Venda.",
                        "Conferir os recibos físicos e complementar o detalhamento dos produtos entregues.",
                        valor_diferenca=val_si, chave_extra=f"ENT_SEM_ITENS_{ct.id}"
                    )
                )

            # (12a) Duplicidade suspeita de entregas (mesmo contrato + data + valor)
            for (dt_iso, val_e), grupo_dup in assinaturas_ent.items():
                if len(grupo_dup) > 1:
                    val_dup = round(val_e * (len(grupo_dup) - 1), 2)
                    pendencias_forn.append(
                        _criar_alerta(
                            exercicio, agr, ct, "Possível Duplicidade", "EXEC-FIN", mapa_regras,
                            "LARANJA", "ALTO",
                            f"Possível duplicidade de entrega no contrato {ct.numero_contrato}: {len(grupo_dup)} lançamentos idênticos em {dt_iso} no valor de {formatar_moeda_br(val_e)}.",
                            f"Entregas IDs: {', '.join('#' + str(e.id) for e in grupo_dup)}.",
                            f"Possível valor duplicado estimado: {formatar_moeda_br(val_dup)}.",
                            "Lançamentos repetidos distorcem o valor executado, o limite individual e o percentual do PNAE.",
                            "Conferir se correspondem a entregas distintas ou estornar o lançamento duplicado.",
                            valor_diferenca=val_dup, prazo_sugerido="5 dias", chave_extra=f"DUP_ENT_{ct.id}_{dt_iso}_{val_e}"
                        )
                    )

            # (7) Pagamentos sem documentação comprobatória
            pags_sem_comp = [p for p in pag_ct if not (p.numero_ordem_bancaria or "").strip() and not p.comprovante_url]
            if pags_sem_comp:
                v_psc = round(sum(float(p.valor_pago or 0.0) for p in pags_sem_comp), 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Pagamento", "EXEC-FIN", mapa_regras,
                        "AMARELO", "MEDIO",
                        f"{len(pags_sem_comp)} pagamento(s) no contrato {ct.numero_contrato} sem ordem bancária ou comprovante anexado ({formatar_moeda_br(v_psc)}).",
                        f"Pagamentos IDs: {', '.join('#' + str(p.id) for p in pags_sem_comp)}.",
                        f"Valor pago sem comprovante digital = {formatar_moeda_br(v_psc)}.",
                        "Necessidade de vinculação do comprovante bancário para prestação de contas.",
                        "Informar o número da ordem bancária ou anexar o comprovante de pagamento.",
                        valor_diferenca=v_psc, chave_extra=f"PAG_SEM_COMP_{ct.id}"
                    )
                )

            # (5b) Pagamentos com NF informada que não existe em nenhuma entrega
            nfs_ent_ct = {(e.numero_nota_fiscal or "").strip().upper() for e in ent_ct if (e.numero_nota_fiscal or "").strip()}
            pags_nf_orfa = [
                p for p in pag_ct
                if (p.numero_nota_fiscal or "").strip() and p.numero_nota_fiscal.strip().upper() not in nfs_ent_ct
            ]
            if pags_nf_orfa:
                v_orfa = round(sum(float(p.valor_pago or 0.0) for p in pags_nf_orfa), 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, ct, "Financeiro / Fiscal", "EXEC-FIN", mapa_regras,
                        "LARANJA", "ALTO",
                        f"{len(pags_nf_orfa)} nota(s) fiscal(is) em registros de pagamento sem entrega correspondente cadastrada no contrato {ct.numero_contrato}.",
                        f"NFs sem entrega: {', '.join(p.numero_nota_fiscal for p in pags_nf_orfa)}.",
                        f"Valor total sem registro de entrega correspondente = {formatar_moeda_br(v_orfa)}.",
                        "Risco de liquidação sem atesto formal de recebimento dos gêneros alimentícios.",
                        "Conferir os termos de recebimento nas escolas/depósito e regularizar o registro da entrega.",
                        valor_diferenca=v_orfa, prazo_sugerido="5 dias", chave_extra=f"NF_SEM_ENT_{ct.id}"
                    )
                )

        # (12b) Duplicidade de Nota Fiscal no mesmo fornecedor
        for nf_num, lista_nf in nfs_vistas_forn.items():
            if len(lista_nf) > 1:
                v_nf_dup = round(sum(float(e.valor_total or 0.0) for _, e in lista_nf[1:]), 2)
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, None, "Duplicidade de Nota Fiscal", "EXEC-FIN", mapa_regras,
                        "VERMELHO", "CRITICO",
                        f"Nota Fiscal nº {nf_num} registrada {len(lista_nf)} vezes para o fornecedor {agr.razao_social}.",
                        f"Entregas envolvidas: {', '.join(f'#{e.id} ({c_num})' for c_num, e in lista_nf)}.",
                        f"Possível duplicidade fiscal estimada em {formatar_moeda_br(v_nf_dup)}.",
                        "Risco grave de contagem duplicada na execução física/financeira e pagamento em duplicidade.",
                        "Auditar imediatamente a Nota Fiscal física/eletrônica e excluir/estornar lançamentos repetidos.",
                        setor_responsavel="SEME / Contabilidade / Controle Interno", prazo_sugerido="5 dias",
                        valor_diferenca=v_nf_dup, chave_extra=f"NF_DUP_{nf_num}"
                    )
                )

        # (10) Divergência consolidada entre Contratado, Executado, Faturado e Pago
        if valor_executado > 0 and valor_faturado < valor_executado - 0.01:
            dif_ef = round(valor_executado - valor_faturado, 2)
            # Evita duplicar se já apontou entrega sem NF, mas resume no consolidado quando útil
            if not any(p["categoria"] == "Financeiro / Fiscal" for p in pendencias_forn):
                pendencias_forn.append(
                    _criar_alerta(
                        exercicio, agr, None, "Divergência Financeira", "EXEC-FIN", mapa_regras,
                        "AMARELO", "MEDIO",
                        f"Diferença de {formatar_moeda_br(dif_ef)} entre valor executado ({formatar_moeda_br(valor_executado)}) e faturado ({formatar_moeda_br(valor_faturado)}).",
                        f"Fornecedor {agr.razao_social} — Exercício {exercicio}.",
                        f"Executado ({formatar_moeda_br(valor_executado)}) − Faturado ({formatar_moeda_br(valor_faturado)}) = {formatar_moeda_br(dif_ef)}.",
                        "Parte das entregas ainda não possui documento fiscal associado.",
                        "Conferir emissão das notas fiscais pendentes.",
                        valor_diferenca=dif_ef, chave_extra="DIV_EXEC_FAT"
                    )
                )

        # ------------------------------------------------------------------
        # C.3 — AUDITORIA DOCUMENTAL (CHECKLIST 12 ITENS)
        # ------------------------------------------------------------------
        checklist_docs = _avaliar_documentacao_fornecedor(
            agr, contratos_ex, entregas_ex, pagamentos_ex, docs_status_map, hoje
        )
        qtd_docs_regulares = sum(1 for d in checklist_docs if d["situacao"] == "DOCUMENTO REGULAR")
        qtd_docs_ausentes = sum(1 for d in checklist_docs if d["situacao"] == "DOCUMENTO AUSENTE")
        qtd_docs_vencidos = sum(1 for d in checklist_docs if d["situacao"] == "DOCUMENTO VENCIDO")
        qtd_docs_pendentes = sum(1 for d in checklist_docs if d["situacao"] == "DOCUMENTO PENDENTE DE CONFERÊNCIA")

        if (qtd_docs_ausentes > 0 or qtd_docs_vencidos > 0) and (contratos_ex or entregas_ex):
            docs_prob = [d["nome_documento"] for d in checklist_docs if d["situacao"] in ("DOCUMENTO AUSENTE", "DOCUMENTO VENCIDO")]
            pendencias_forn.append(
                _criar_alerta(
                    exercicio, agr, None, "Pendência Documental", "DOC-HAB", mapa_regras,
                    "AMARELO", "MEDIO",
                    f"Documentação ausente ({qtd_docs_ausentes}) ou vencida ({qtd_docs_vencidos}) no prontuário digital: {', '.join(docs_prob[:4])}.",
                    f"Fornecedor #{agr.id} ({agr.razao_social}) | Checklist com {qtd_docs_regulares} regulares e {qtd_docs_pendentes} pendentes de conferência.",
                    "A ausência de arquivo digital não presume irregularidade material, mas requer conferência na pasta física e regularização.",
                    "Risco de ressalva em auditoria documental do CAE, FNDE ou TCE.",
                    "Conferir os documentos físicos na pasta da Chamada Pública e registrar a regularidade ou solicitar atualização ao fornecedor.",
                    setor_responsavel="Coordenação do PNAE / Licitações", prazo_sugerido="15 dias",
                    chave_extra="DOC_PENDENTE_GERAL"
                )
            )

        # Vincula justificativas administrativas existentes às pendências
        for p in pendencias_forn:
            j = just_por_chave.get(p["chave_pendencia"])
            if j:
                p["justificativa"] = j
                p["status_resolucao"] = j.status_resolucao

        # Determina situação global consolidada do fornecedor (pior gravidade ativa)
        cores_ativas = [p["cor"] for p in pendencias_forn if p["status_resolucao"] != "Regularizada"]
        if status_limite == "VERMELHO" or "VERMELHO" in cores_ativas:
            situacao_global = "VERMELHO"
            situacao_global_label = "CRÍTICO"
        elif status_limite == "LARANJA" or "LARANJA" in cores_ativas:
            situacao_global = "LARANJA"
            situacao_global_label = "INCONSISTÊNCIA IMPORTANTE"
        elif status_limite == "CINZA" or "CINZA" in cores_ativas:
            situacao_global = "CINZA"
            situacao_global_label = "DADOS INSUFICIENTES"
        elif status_limite == "AMARELO" or "AMARELO" in cores_ativas:
            situacao_global = "AMARELO"
            situacao_global_label = "ATENÇÃO / PENDÊNCIA"
        else:
            situacao_global = "VERDE"
            situacao_global_label = "REGULAR"

        ficha = {
            "agricultor": agr,
            "agricultor_id": agr.id,
            "razao_social": agr.razao_social,
            "cpf_cnpj": formatar_cpf_cnpj(agr.cpf_cnpj),
            "cpf_cnpj_digitos": cpf_limpo,
            "caf": agr.dap_caf_numero or "Não informado",
            "caf_norm": caf_norm,
            "caf_validade": agr.dap_caf_validade,
            "caf_duplicado": caf_duplicado,
            "tipo_fornecedor": agr.tipo_fornecedor or "Individual",
            "eh_formal": eh_formal,
            "eh_informal": eh_informal,
            "eh_individual": eh_individual,
            "qtd_contratos": len(contratos_ex),
            "contratos": contratos_ex,
            "qtd_entregas": len(entregas_ex),
            "entregas": entregas_ex,
            "pagamentos": pagamentos_ex,
            "dados_pagamento_disponiveis": len(pagamentos_ex) > 0,
            "valor_contratado": valor_contratado,
            "valor_executado": valor_executado,
            "valor_executado_caf_consolidado": valor_base_caf_consolidado,
            "valor_faturado": valor_faturado,
            "valor_pago": valor_pago,
            "saldo_contratual": saldo_contratual,
            "limite_aplicavel": limite_aplicavel,
            "excedente_executado": excedente_executado,
            "excedente_contratado": excedente_contratado,
            "percentual_limite": percentual_limite,
            "status_limite": status_limite,
            "label_limite": label_limite,
            "situacao_global": situacao_global,
            "situacao_global_label": situacao_global_label,
            "checklist_docs": checklist_docs,
            "qtd_docs_regulares": qtd_docs_regulares,
            "qtd_docs_ausentes": qtd_docs_ausentes,
            "qtd_docs_vencidos": qtd_docs_vencidos,
            "qtd_docs_pendentes": qtd_docs_pendentes,
            "pendencias": pendencias_forn,
            "info_associacao": info_associacao,
        }

        # Aplica filtros do painel
        if filtro_fornecedor and filtro_fornecedor.strip().lower() not in (agr.razao_social or "").lower():
            continue
        if filtro_cpf_cnpj and limpar_digitos(filtro_cpf_cnpj) not in cpf_limpo:
            continue
        if filtro_caf and normalizar_caf(filtro_caf) not in caf_norm:
            continue
        if filtro_tipo:
            ft = filtro_tipo.strip().lower()
            if ft == "individual" and not eh_individual:
                continue
            elif ft == "grupo_formal" and not eh_formal:
                continue
            elif ft == "grupo_informal" and not eh_informal:
                continue
        if filtro_associacao_id and agr.id != int(filtro_associacao_id):
            continue
        if filtro_situacao and situacao_global != filtro_situacao.upper():
            continue

        analises_fornecedores.append(ficha)
        todas_pendencias.extend(pendencias_forn)

    # --- Passo D: Auditoria da Execução do PNAE — Percentual Mínimo de 45% (Art. 29) ---
    config_pnae = ConfiguracaoPNAE.query.filter_by(ano=exercicio).first()
    repasse_federal = round(float(config_pnae.valor_total_repasse or 0.0), 2) if config_pnae else 0.0
    meta_pct = normas_ctx["pct_minimo_2026"] if exercicio >= 2026 else normas_ctx["pct_minimo_anterior"]
    valor_meta_minima = round(repasse_federal * (meta_pct / 100.0), 2)

    # Total executado geral vs. total executado elegível (fornecedores com CAF cadastrado)
    total_contratado_geral = round(sum(f["valor_contratado"] for f in analises_fornecedores), 2)
    total_executado_geral = round(sum(f["valor_executado"] for f in analises_fornecedores), 2)
    total_faturado_geral = round(sum(f["valor_faturado"] for f in analises_fornecedores), 2)
    total_pago_geral = round(sum(f["valor_pago"] for f in analises_fornecedores), 2)

    total_executado_elegivel = round(
        sum(f["valor_executado"] for f in analises_fornecedores if f["caf_norm"]), 2
    )
    total_executado_sem_caf = round(total_executado_geral - total_executado_elegivel, 2)

    pct_alcancado_elegivel = round((total_executado_elegivel / repasse_federal) * 100.0, 2) if repasse_federal > 0 else 0.0
    pct_alcancado_bruto = round((total_executado_geral / repasse_federal) * 100.0, 2) if repasse_federal > 0 else 0.0
    falta_meta = round(max(0.0, valor_meta_minima - total_executado_elegivel), 2)

    # Análise de cota de 50% de mulheres (Res. 17/2026, art. 29 § 3º)
    total_pago_mulher = round(sum(float(p.valor_pago or 0.0) for p in pagamentos_db if p.emitido_nome_mulher), 2)
    pct_mulheres_informado = round((total_pago_mulher / total_pago_geral) * 100.0, 2) if total_pago_geral > 0 else None

    analise_pnae_45 = {
        "exercicio": exercicio,
        "repasse_federal_configurado": config_pnae is not None and repasse_federal > 0,
        "repasse_federal": repasse_federal,
        "meta_percentual": meta_pct,
        "valor_meta_minima": valor_meta_minima,
        "total_contratado": total_contratado_geral,
        "total_executado_bruto": total_executado_geral,
        "total_executado_elegivel": total_executado_elegivel,
        "total_executado_sem_caf": total_executado_sem_caf,
        "percentual_elegivel": pct_alcancado_elegivel,
        "percentual_bruto": pct_alcancado_bruto,
        "falta_para_meta": falta_meta,
        "atingiu_meta": (repasse_federal > 0 and total_executado_elegivel >= valor_meta_minima),
        "elegivel_exclusividade_pj_700k": repasse_federal > 700000.0,
        "pct_mulheres_informado": pct_mulheres_informado,
        "total_pago_mulher": total_pago_mulher,
        "status_texto": (
            "INFORMAÇÃO INSUFICIENTE (Repasse FNDE não configurado)"
            if repasse_federal <= 0
            else ("META ATINGIDA" if total_executado_elegivel >= valor_meta_minima else f"ABAIXO DA META ({pct_alcancado_elegivel:.2f}% / {meta_pct:.0f}%)")
        ),
    }

    # Alertas globais do programa PNAE (45%)
    alertas_programa = []
    if repasse_federal <= 0:
        alertas_programa.append(
            _criar_alerta(
                exercicio, None, None, "Execução PNAE (Meta 45%)", "PCT-MIN-45", mapa_regras,
                "CINZA", "ALTO",
                f"Repasse federal do FNDE para o exercício {exercicio} não cadastrado — impossível apurar o percentual de {meta_pct:.0f}%.",
                f"Tabela pnae_configuracao sem registro ativo para o ano {exercicio}.",
                "Metodologia normativa exige o total efetivo dos recursos federais repassados pelo FNDE no exercício.",
                "Impossibilidade de monitoramento preventivo do art. 14 da Lei nº 11.947/2009 e art. 29 da Res. CD/FNDE nº 4/2026.",
                "Cadastrar o valor oficial do repasse federal do FNDE na tela 'Orçamento Base' da Agricultura Familiar.",
                setor_responsavel="SEME / Coordenação do PNAE / Financeiro", prazo_sugerido="5 dias",
                chave_extra=f"SEM_REPASSE_{exercicio}"
            )
        )
    elif total_executado_elegivel < valor_meta_minima:
        cor_meta = "VERMELHO" if pct_alcancado_elegivel < (meta_pct * 0.7) else "LARANJA"
        alertas_programa.append(
            _criar_alerta(
                exercicio, None, None, "Execução PNAE (Meta 45%)", "PCT-MIN-45", mapa_regras,
                cor_meta, "CRITICO" if cor_meta == "VERMELHO" else "ALTO",
                f"Percentual elegível executado ({pct_alcancado_elegivel:.2f}%) encontra-se abaixo do mínimo normativo de {meta_pct:.0f}%.",
                f"Repasse FNDE {exercicio}: {formatar_moeda_br(repasse_federal)} | Mínimo obrigatório ({meta_pct:.0f}%): {formatar_moeda_br(valor_meta_minima)} | Executado elegível (com CAF): {formatar_moeda_br(total_executado_elegivel)}.",
                f"Déficit para cumprimento da meta = {formatar_moeda_br(falta_meta)}"
                + (f" (Nota: há {formatar_moeda_br(total_executado_sem_caf)} executados em cadastros sem CAF informado, excluídos do cálculo elegível)." if total_executado_sem_caf > 0 else "."),
                "Risco de descumprimento do art. 29 da Res. CD/FNDE nº 4/2026 (alterada pela Res. nº 17/2026), podendo gerar diligência ou solicitação de devolução de recursos na prestação de contas ao FNDE.",
                "Ampliar a execução das chamadas públicas vigentes, regularizar CAFs ausentes nos fornecedores já contratados ou formalizar justificativa normativa nos casos previstos em lei.",
                setor_responsavel="Secretaria Municipal de Educação (SEME) / PNAE / CAE", prazo_sugerido="Imediato / Mensal",
                valor_diferenca=falta_meta, chave_extra=f"ABAIXO_META_{exercicio}"
            )
        )

    if total_executado_geral > 0 and total_pago_geral == 0:
        alertas_programa.append(
            _criar_alerta(
                exercicio, None, None, "Conciliação Financeira Global", "EXEC-FIN", mapa_regras,
                "CINZA", "MEDIO",
                "Não há registros de pagamentos (ordens bancárias) vinculados à auditoria no exercício — conciliação Pago × Faturado pendente de conferência na Tesouraria.",
                f"Total executado: {formatar_moeda_br(total_executado_geral)} | Total faturado: {formatar_moeda_br(total_faturado_geral)} | Total pago registrado: R$ 0,00.",
                "A auditoria não presume que valor executado equivale a valor pago.",
                "Impossibilidade de certificar automaticamente divergências entre notas fiscais e pagamentos bancários.",
                "Registrar as ordens bancárias nos dossiês dos fornecedores ou anexar o extrato de pagamentos da Tesouraria ao relatório final.",
                setor_responsavel="Tesouraria / Contabilidade / SEME", prazo_sugerido="15 dias",
                chave_extra=f"SEM_PAGTOS_{exercicio}"
            )
        )

    for ap in alertas_programa:
        j = just_por_chave.get(ap["chave_pendencia"])
        if j:
            ap["justificativa"] = j
            ap["status_resolucao"] = j.status_resolucao
    todas_pendencias = alertas_programa + todas_pendencias

    # Ordena pendências por gravidade (VERMELHO -> LARANJA -> AMARELO -> CINZA -> VERDE)
    ordem_cor = {"VERMELHO": 1, "LARANJA": 2, "AMARELO": 3, "CINZA": 4, "VERDE": 5}
    todas_pendencias.sort(key=lambda x: (ordem_cor.get(x["cor"], 9), -float(x["valor_diferenca"] or 0.0)))

    # --- Passo E: Indicadores do Painel Geral (Seção 8) ---
    indicadores = {
        "total_fornecedores": len(analises_fornecedores),
        "total_individuais": sum(1 for f in analises_fornecedores if f["eh_individual"]),
        "total_associacoes": sum(1 for f in analises_fornecedores if f["eh_formal"]),
        "total_grupos_informais": sum(1 for f in analises_fornecedores if f["eh_informal"]),
        "total_contratado": total_contratado_geral,
        "total_executado": total_executado_geral,
        "total_faturado": total_faturado_geral,
        "total_pago": total_pago_geral,
        "qtd_regulares": sum(1 for f in analises_fornecedores if f["situacao_global"] == "VERDE"),
        "qtd_atencao": sum(1 for f in analises_fornecedores if f["situacao_global"] == "AMARELO"),
        "qtd_com_pendencias": sum(1 for f in analises_fornecedores if f["situacao_global"] in ("VERMELHO", "LARANJA", "AMARELO", "CINZA")),
        "qtd_criticos": sum(1 for f in analises_fornecedores if f["situacao_global"] == "VERMELHO"),
        "qtd_laranja": sum(1 for f in analises_fornecedores if f["situacao_global"] == "LARANJA"),
        "qtd_cinza": sum(1 for f in analises_fornecedores if f["situacao_global"] == "CINZA"),
        "qtd_limites_ultrapassados": sum(1 for f in analises_fornecedores if f["status_limite"] == "VERMELHO"),
        "qtd_pendencias_documentais": sum(1 for p in todas_pendencias if "Document" in p["categoria"] or p["codigo_regra"] == "DOC-HAB"),
        "qtd_inconsistencias_financeiras": sum(1 for p in todas_pendencias if any(k in p["categoria"] for k in ("Financeiro", "Duplicidade", "Divergência", "Excesso"))),
        "qtd_pendencias_total": len(todas_pendencias),
        "qtd_ocorrencias_criticas": sum(1 for p in todas_pendencias if p["cor"] == "VERMELHO"),
    }

    # Pendências agrupadas por categoria (para gráfico)
    pend_por_categoria = defaultdict(int)
    for p in todas_pendencias:
        pend_por_categoria[p["categoria"]] += 1

    # Comparativo com exercício anterior (opcional e rápido)
    comparativo = None
    if incluir_comparativo and exercicio > 2020:
        ano_ant = exercicio - 1
        cfg_ant = ConfiguracaoPNAE.query.filter_by(ano=ano_ant).first()
        cts_ant = ContratoPNAE.query.options(joinedload(ContratoPNAE.entregas)).all()
        v_cont_ant = 0.0
        v_exec_ant = 0.0
        for ct in cts_ant:
            if ct.data_inicio and ct.data_inicio.year == ano_ant:
                v_cont_ant += float(ct.valor_total or 0.0)
            for ent in (ct.entregas or []):
                if ent.status == "Aprovado" and ent.data_entrega and ent.data_entrega.year == ano_ant:
                    v_exec_ant += float(ent.valor_total or 0.0)
        rep_ant = float(cfg_ant.valor_total_repasse or 0.0) if cfg_ant else 0.0
        comparativo = {
            "ano_anterior": ano_ant,
            "repasse_anterior": round(rep_ant, 2),
            "contratado_anterior": round(v_cont_ant, 2),
            "executado_anterior": round(v_exec_ant, 2),
            "percentual_anterior": round((v_exec_ant / rep_ant) * 100.0, 2) if rep_ant > 0 else 0.0,
            "variacao_executado": round(total_executado_geral - v_exec_ant, 2),
        }

    # --- Passo F: Dados para Gráficos (Chart.js) ---
    meses_nomes = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    top_forn_grafico = sorted(analises_fornecedores, key=lambda x: max(x["valor_contratado"], x["valor_executado"]), reverse=True)[:10]

    graficos = {
        "situacao_labels": ["Regular (Verde)", "Atenção (Amarelo)", "Inconsistência (Laranja)", "Crítico (Vermelho)", "Dados Insuf. (Cinza)"],
        "situacao_valores": [
            indicadores["qtd_regulares"],
            indicadores["qtd_atencao"],
            indicadores["qtd_laranja"],
            indicadores["qtd_criticos"],
            indicadores["qtd_cinza"],
        ],
        "forn_labels": [f["razao_social"][:24] for f in top_forn_grafico],
        "forn_contratado": [f["valor_contratado"] for f in top_forn_grafico],
        "forn_executado": [f["valor_executado"] for f in top_forn_grafico],
        "forn_pct_limite": [min(f["percentual_limite"], 200.0) for f in top_forn_grafico],
        "meses_labels": meses_nomes,
        "meses_executado": [round(evolucao_mensal[m]["executado"], 2) for m in range(1, 13)],
        "meses_faturado": [round(evolucao_mensal[m]["faturado"], 2) for m in range(1, 13)],
        "meses_pago": [round(evolucao_mensal[m]["pago"], 2) for m in range(1, 13)],
        "cat_pend_labels": list(pend_por_categoria.keys()),
        "cat_pend_valores": list(pend_por_categoria.values()),
    }

    return {
        "exercicio": exercicio,
        "data_geracao": datetime.now(),
        "versao_normativa": normas_ctx["versao"],
        "regras_normativas": normas_ctx["regras"],
        "limite_individual": limite_ind,
        "faixa_atencao": faixa_atencao,
        "indicadores": indicadores,
        "fornecedores": analises_fornecedores,
        "associacoes": [f["info_associacao"] for f in analises_fornecedores if f["info_associacao"]],
        "pendencias": todas_pendencias,
        "analise_pnae_45": analise_pnae_45,
        "comparativo": comparativo,
        "graficos": graficos,
        "chamadas_disponiveis": sorted(chamadas_disponiveis),
        "contratos_disponiveis": contratos_disponiveis,
        "associacoes_disponiveis": [a for a in agricultores if is_grupo_formal(a.tipo_fornecedor)],
    }
