# patrimonio_routes.py
import os
import re
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session,
    make_response, current_app, send_from_directory, send_file, jsonify
)
import qrcode
from models import (
    Secretaria, Patrimonio, MovimentacaoPatrimonio, Servidor,
    LocalPatrimonio, NotaFiscalPatrimonio, Fornecedor, Escola
)
from utils import role_required, login_required, registrar_log, upload_arquivo_para_nuvem, obter_data_hora_br_str
from extensions import db, bcrypt
from sqlalchemy import or_, func, text
from datetime import datetime, date
from io import BytesIO
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas as canvas_lib
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from flask_login import current_user

patrimonio_bp = Blueprint('patrimonio', __name__, url_prefix='/patrimonio')

_schema_patrimonio_verificado = False


def executar_migracao_patrimonio_segura():
    """Garante que as novas tabelas e colunas do módulo Patrimônio (Controle Interno / NF / Locais) existam no Supabase (PostgreSQL) e SQLite."""
    global _schema_patrimonio_verificado
    if _schema_patrimonio_verificado:
        return
    try:
        db.create_all()
    except Exception as e:
        print(f"Aviso db.create_all() patrimonio: {e}")

    # Criação explícita das novas tabelas para PostgreSQL (Supabase / Railway)
    tabelas_postgres_sql = [
        """
        CREATE TABLE IF NOT EXISTS patrimonio_local (
            id SERIAL PRIMARY KEY,
            nome VARCHAR(200) NOT NULL,
            predio_unidade VARCHAR(200),
            sala_setor VARCHAR(150),
            endereco VARCHAR(300),
            responsavel_setor VARCHAR(200),
            secretaria_id INTEGER,
            ativo BOOLEAN DEFAULT TRUE,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS patrimonio_nota_fiscal (
            id SERIAL PRIMARY KEY,
            numero_nf VARCHAR(50) NOT NULL,
            serie_nf VARCHAR(20),
            chave_acesso VARCHAR(60),
            data_emissao DATE,
            data_recebimento DATE,
            fornecedor_nome VARCHAR(200),
            fornecedor_cnpj VARCHAR(25),
            forma_aquisicao VARCHAR(80) DEFAULT 'Compra / Licitação',
            numero_processo VARCHAR(100),
            modalidade_licitacao VARCHAR(100),
            numero_contrato VARCHAR(80),
            numero_empenho VARCHAR(80),
            fonte_recursos VARCHAR(150),
            conta_contabil VARCHAR(120),
            valor_total_nf FLOAT DEFAULT 0.0,
            arquivo_nf_url VARCHAR(500),
            observacoes TEXT,
            usuario_registro VARCHAR(100),
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    ]

    for sql_create in tabelas_postgres_sql:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(sql_create))
        except Exception as e:
            print(f"Aviso ao criar tabela de patrimônio: {e}")

    # No PostgreSQL (Supabase), garante que numero_patrimonio permita NULL (para bens em depósito/sem tombamento imediato)
    try:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE patrimonio ALTER COLUMN numero_patrimonio DROP NOT NULL;"))
    except Exception:
        pass

    colunas_patrimonio = [
        ("estado_conservacao", "VARCHAR(50) DEFAULT 'Bom'"),
        ("situacao_uso", "VARCHAR(50) DEFAULT 'Em uso'"),
        ("marca", "VARCHAR(100)"),
        ("modelo", "VARCHAR(100)"),
        ("foto_url", "VARCHAR(500)"),
        ("data_cadastro", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ("secretaria_id", "INTEGER"),
        ("numero_serie", "VARCHAR(120)"),
        ("especificacao_tecnica", "TEXT"),
        ("local_id", "INTEGER"),
        ("nota_fiscal_id", "INTEGER"),
        ("forma_aquisicao", "VARCHAR(80)"),
        ("numero_nota_fiscal", "VARCHAR(50)"),
        ("serie_nota_fiscal", "VARCHAR(20)"),
        ("chave_nfe", "VARCHAR(60)"),
        ("data_nota_fiscal", "DATE"),
        ("fornecedor_nome", "VARCHAR(200)"),
        ("fornecedor_cnpj", "VARCHAR(25)"),
        ("numero_empenho", "VARCHAR(80)"),
        ("numero_processo", "VARCHAR(100)"),
        ("modalidade_licitacao", "VARCHAR(100)"),
        ("numero_contrato", "VARCHAR(80)"),
        ("fonte_recursos", "VARCHAR(150)"),
        ("conta_contabil", "VARCHAR(120)"),
        ("vida_util_anos", "INTEGER DEFAULT 5"),
        ("valor_residual", "FLOAT DEFAULT 0.0"),
        ("garantia_ate", "DATE"),
        ("data_atesto", "DATE"),
        ("arquivo_nf_url", "VARCHAR(500)"),
        ("responsavel_nome", "VARCHAR(200)"),
        ("responsavel_cargo", "VARCHAR(120)"),
        ("responsavel_matricula", "VARCHAR(60)"),
    ]

    for col_nome, col_tipo in colunas_patrimonio:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE patrimonio ADD COLUMN IF NOT EXISTS {col_nome} {col_tipo};"))
        except Exception:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE patrimonio ADD COLUMN {col_nome} {col_tipo};"))
            except Exception:
                pass

    colunas_movimentacao = [
        ("responsavel_anterior_nome", "VARCHAR(200)"),
        ("responsavel_novo_nome", "VARCHAR(200)"),
        ("motivo_transferencia", "TEXT"),
    ]

    for col_nome, col_tipo in colunas_movimentacao:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE movimentacao_patrimonio ADD COLUMN IF NOT EXISTS {col_nome} {col_tipo};"))
        except Exception:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE movimentacao_patrimonio ADD COLUMN {col_nome} {col_tipo};"))
            except Exception:
                pass

    _schema_patrimonio_verificado = True


@patrimonio_bp.before_app_request
def _garantir_schema_patrimonio_request():
    if not _schema_patrimonio_verificado and request.blueprint == 'patrimonio':
        executar_migracao_patrimonio_segura()


def _parse_moeda(valor_raw):
    """Converte string monetária (ex: '1.250,50' ou '1250.50') em float."""
    if not valor_raw:
        return 0.0
    s = str(valor_raw).strip().replace('R$', '').replace(' ', '')
    if not s:
        return 0.0
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_data(data_str):
    if not data_str or not str(data_str).strip():
        return None
    try:
        return datetime.strptime(str(data_str).strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def _incrementar_tombamento(tombamento_base, offset):
    """
    Incrementa o sufixo numérico de um código de tombamento preservando zeros à esquerda.
    Ex: ('20260001', 2) -> '20260003' | ('PAT-0010', 1) -> 'PAT-0011'
    """
    if not tombamento_base or not str(tombamento_base).strip():
        return None
    base = str(tombamento_base).strip()
    if offset == 0:
        return base
    match = re.search(r'(\d+)$', base)
    if match:
        num_str = match.group(1)
        prefix = base[:match.start(1)]
        novo_num = int(num_str) + offset
        return f"{prefix}{str(novo_num).zfill(len(num_str))}"
    return f"{base}-{offset + 1}"


def _obter_locais_disponiveis():
    """Retorna objetos LocalPatrimonio ativos e lista combinada de nomes de localização."""
    locais_db = LocalPatrimonio.query.filter_by(ativo=True).order_by(LocalPatrimonio.nome).all()
    nomes_cadastrados = {l.nome.strip() for l in locais_db if l.nome}

    # Também inclui locais já usados em patrimônios anteriores para não perder referências
    locais_existentes = db.session.query(Patrimonio.localizacao).distinct().all()
    locais_legados = sorted(
        {r[0].strip() for r in locais_existentes if r[0] and r[0].strip() and r[0].strip() not in nomes_cadastrados}
    )
    return locais_db, locais_legados


@patrimonio_bp.route('/')
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def listar_itens():
    import math
    executar_migracao_patrimonio_segura()

    # Remove eventuais registros órfãos de NotaFiscalPatrimonio sem nenhum bem vinculado
    try:
        notas_todas = NotaFiscalPatrimonio.query.all()
        removidas = False
        for nf_check in notas_todas:
            if not nf_check.bens:
                db.session.delete(nf_check)
                removidas = True
        if removidas:
            db.session.commit()
    except Exception:
        db.session.rollback()

    query = Patrimonio.query

    termo_busca = (request.args.get('termo') or request.args.get('q') or '').strip()
    secretaria_filtro = request.args.get('secretaria_id', '').strip()
    categoria_filtro = request.args.get('categoria', '').strip()
    estado_filtro = request.args.get('estado', '').strip()
    situacao_filtro = request.args.get('situacao', '').strip()
    nf_filtro = request.args.get('nota_fiscal', '').strip()
    aba_ativa = request.args.get('aba', '').strip()

    if termo_busca:
        search_pattern = f"%{termo_busca}%"
        query = query.filter(or_(
            Patrimonio.numero_patrimonio.ilike(search_pattern),
            Patrimonio.descricao.ilike(search_pattern),
            Patrimonio.localizacao.ilike(search_pattern),
            Patrimonio.responsavel_nome.ilike(search_pattern),
            Patrimonio.numero_nota_fiscal.ilike(search_pattern),
            Patrimonio.fornecedor_nome.ilike(search_pattern),
            Patrimonio.fornecedor_cnpj.ilike(search_pattern),
            Patrimonio.numero_empenho.ilike(search_pattern),
            Patrimonio.marca.ilike(search_pattern),
            Patrimonio.modelo.ilike(search_pattern),
            Patrimonio.numero_serie.ilike(search_pattern),
        ))

    if secretaria_filtro and secretaria_filtro.isdigit():
        query = query.filter(Patrimonio.secretaria_id == int(secretaria_filtro))
    if categoria_filtro:
        query = query.filter(Patrimonio.categoria == categoria_filtro)
    if estado_filtro:
        query = query.filter(Patrimonio.estado_conservacao == estado_filtro)
    if situacao_filtro:
        query = query.filter(Patrimonio.situacao_uso == situacao_filtro)
    if nf_filtro:
        query = query.filter(Patrimonio.numero_nota_fiscal.ilike(f"%{nf_filtro}%"))

    todos_itens_filtrados = query.order_by(Patrimonio.id.desc()).all()

    # KPIs globais sobre o conjunto filtrado
    kpis = {
        'total_bens': len(todos_itens_filtrados),
        'valor_acervo': sum((b.valor_aquisicao or 0.0) for b in todos_itens_filtrados),
        'total_locais': len({b.localizacao for b in todos_itens_filtrados if b.localizacao}),
        'total_com_nf': sum(1 for b in todos_itens_filtrados if b.numero_nota_fiscal),
    }

    # =========================================================================
    # AGRUPAMENTO POR FORNECEDOR -> NOTAS FISCAIS -> BENS PATRIMONIAIS
    # =========================================================================
    mapa_fornecedores = {}

    for bem in todos_itens_filtrados:
        nf_rel = bem.nota_fiscal_rel
        nome_forn = (
            (bem.fornecedor_nome or '').strip()
            or (nf_rel.fornecedor_nome.strip() if nf_rel and nf_rel.fornecedor_nome else '')
            or 'Incorporação Direta / Sem Fornecedor Informado'
        )
        cnpj_forn = (
            (bem.fornecedor_cnpj or '').strip()
            or (nf_rel.fornecedor_cnpj.strip() if nf_rel and nf_rel.fornecedor_cnpj else '')
        )
        chave_forn = nome_forn.lower()

        if chave_forn not in mapa_fornecedores:
            mapa_fornecedores[chave_forn] = {
                'nome': nome_forn,
                'cnpj': cnpj_forn,
                'notas_map': {},
                'qtd_bens': 0,
                'valor_total': 0.0,
                'ultima_data': None,
                'maior_id_bem': bem.id,
            }
        else:
            if not mapa_fornecedores[chave_forn]['cnpj'] and cnpj_forn:
                mapa_fornecedores[chave_forn]['cnpj'] = cnpj_forn
            if bem.id > mapa_fornecedores[chave_forn]['maior_id_bem']:
                mapa_fornecedores[chave_forn]['maior_id_bem'] = bem.id

        forn_entry = mapa_fornecedores[chave_forn]
        forn_entry['qtd_bens'] += 1
        forn_entry['valor_total'] += (bem.valor_aquisicao or 0.0)

        data_ref = bem.data_nota_fiscal or bem.data_aquisicao or (bem.data_cadastro.date() if bem.data_cadastro else None)
        if data_ref and (not forn_entry['ultima_data'] or data_ref > forn_entry['ultima_data']):
            forn_entry['ultima_data'] = data_ref

        num_nf = (
            (bem.numero_nota_fiscal or '').strip()
            or (nf_rel.numero_nf.strip() if nf_rel and nf_rel.numero_nf else '')
            or 'Sem Nº de Nota Fiscal'
        )
        chave_nf = f"{ bem.nota_fiscal_id or '' }_{num_nf.lower()}"

        if chave_nf not in forn_entry['notas_map']:
            forn_entry['notas_map'][chave_nf] = {
                'id': bem.nota_fiscal_id,
                'numero_nf': num_nf,
                'serie_nf': bem.serie_nota_fiscal or (nf_rel.serie_nf if nf_rel else None),
                'chave_nfe': bem.chave_nfe or (nf_rel.chave_acesso if nf_rel else None),
                'data_emissao': bem.data_nota_fiscal or (nf_rel.data_emissao if nf_rel else None) or bem.data_aquisicao,
                'numero_empenho': bem.numero_empenho or (nf_rel.numero_empenho if nf_rel else None),
                'numero_processo': bem.numero_processo or (nf_rel.numero_processo if nf_rel else None),
                'modalidade_licitacao': bem.modalidade_licitacao or (nf_rel.modalidade_licitacao if nf_rel else None),
                'numero_contrato': bem.numero_contrato or (nf_rel.numero_contrato if nf_rel else None),
                'fonte_recursos': bem.fonte_recursos or (nf_rel.fonte_recursos if nf_rel else None),
                'forma_aquisicao': bem.forma_aquisicao or (nf_rel.forma_aquisicao if nf_rel else 'Compra / Licitação'),
                'arquivo_nf_url': bem.arquivo_nf_url or (nf_rel.arquivo_nf_url if nf_rel else None),
                'valor_total': 0.0,
                'qtd_bens': 0,
                'bens': [],
            }

        nf_entry = forn_entry['notas_map'][chave_nf]
        nf_entry['bens'].append(bem)
        nf_entry['qtd_bens'] += 1
        nf_entry['valor_total'] += (bem.valor_aquisicao or 0.0)
        if not nf_entry['arquivo_nf_url'] and bem.arquivo_nf_url:
            nf_entry['arquivo_nf_url'] = bem.arquivo_nf_url

    lista_fornecedores_completa = []
    for idx_f, (_, dados_f) in enumerate(
        sorted(mapa_fornecedores.items(), key=lambda x: x[1]['maior_id_bem'], reverse=True), start=1
    ):
        notas_ordenadas = list(dados_f['notas_map'].values())
        lista_fornecedores_completa.append({
            'indice': idx_f,
            'nome': dados_f['nome'],
            'cnpj': dados_f['cnpj'],
            'qtd_notas': len(notas_ordenadas),
            'qtd_bens': dados_f['qtd_bens'],
            'valor_total': dados_f['valor_total'],
            'ultima_data': dados_f['ultima_data'],
            'notas': notas_ordenadas,
        })

    kpis['total_fornecedores'] = len(lista_fornecedores_completa)
    kpis['total_notas_fiscais'] = sum(f['qtd_notas'] for f in lista_fornecedores_completa)

    # Paginação de Fornecedores: Limite de 10 fornecedores por página
    per_page_forn = 10
    page_forn = max(1, request.args.get('page_forn', 1, type=int))
    total_fornecedores = len(lista_fornecedores_completa)
    total_pages_forn = max(1, math.ceil(total_fornecedores / per_page_forn))
    if page_forn > total_pages_forn:
        page_forn = total_pages_forn
    inicio_forn = (page_forn - 1) * per_page_forn
    fornecedores_paginados = lista_fornecedores_completa[inicio_forn: inicio_forn + per_page_forn]

    # Paginação de Bens Individuais: Limite de 10 bens por página
    per_page_bens = 10
    page_bens = max(1, request.args.get('page_bens', 1, type=int))
    total_bens = len(todos_itens_filtrados)
    total_pages_bens = max(1, math.ceil(total_bens / per_page_bens))
    if page_bens > total_pages_bens:
        page_bens = total_pages_bens
    inicio_bens = (page_bens - 1) * per_page_bens
    itens_paginados = todos_itens_filtrados[inicio_bens: inicio_bens + per_page_bens]

    # Se filtrou por NF específica ou clicou em paginação de bens, abre na aba de bens; caso contrário, abre em Fornecedores
    if not aba_ativa:
        if nf_filtro or request.args.get('page_bens'):
            aba_ativa = 'bens'
        else:
            aba_ativa = 'fornecedores'

    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    notas_recentes = NotaFiscalPatrimonio.query.order_by(NotaFiscalPatrimonio.id.desc()).limit(20).all()
    locais_db, locais_legados = _obter_locais_disponiveis()

    # =========================================================================
    # AGRUPAMENTO DE BENS POR LOCALIZAÇÃO (PARA O MODAL E PDF PADRÃO GOVERNO)
    # =========================================================================
    todos_bens_ativos = Patrimonio.query.filter(Patrimonio.status != 'Baixado').order_by(
        Patrimonio.localizacao, Patrimonio.descricao
    ).all()

    mapa_locais = {}
    for loc_cadastrado in locais_db:
        nome_l = (loc_cadastrado.nome or '').strip()
        if nome_l:
            mapa_locais[nome_l] = {
                'nome': nome_l,
                'secretaria_nome': loc_cadastrado.secretaria.nome if loc_cadastrado.secretaria else '',
                'responsavel_padrao': loc_cadastrado.responsavel_setor or '',
                'qtd_bens': 0,
                'valor_total': 0.0,
                'bens': []
            }

    for bem_loc in todos_bens_ativos:
        nome_l = (bem_loc.localizacao or 'Não informada').strip()
        if nome_l not in mapa_locais:
            mapa_locais[nome_l] = {
                'nome': nome_l,
                'secretaria_nome': bem_loc.secretaria.nome if bem_loc.secretaria else '',
                'responsavel_padrao': bem_loc.nome_responsavel_atual or '',
                'qtd_bens': 0,
                'valor_total': 0.0,
                'bens': []
            }
        entry_l = mapa_locais[nome_l]
        if not entry_l['secretaria_nome'] and bem_loc.secretaria:
            entry_l['secretaria_nome'] = bem_loc.secretaria.nome
        if not entry_l['responsavel_padrao'] and bem_loc.nome_responsavel_atual:
            entry_l['responsavel_padrao'] = bem_loc.nome_responsavel_atual

        entry_l['qtd_bens'] += 1
        entry_l['valor_total'] += (bem_loc.valor_aquisicao or 0.0)
        entry_l['bens'].append({
            'id': bem_loc.id,
            'tombamento': bem_loc.numero_patrimonio or 'S/N',
            'descricao': bem_loc.descricao or '',
            'categoria': bem_loc.categoria or '-',
            'marca_modelo': " / ".join([x for x in [bem_loc.marca, bem_loc.modelo] if x]) or '-',
            'numero_serie': bem_loc.numero_serie or '',
            'nota_fiscal': bem_loc.numero_nota_fiscal or '-',
            'empenho': bem_loc.numero_empenho or '-',
            'fornecedor': bem_loc.fornecedor_nome or '-',
            'estado': bem_loc.estado_conservacao or 'Bom',
            'situacao': bem_loc.situacao_uso or 'Em uso',
            'responsavel': bem_loc.nome_responsavel_atual or 'Não informado',
            'secretaria': bem_loc.secretaria.nome if bem_loc.secretaria else '-',
            'data_aquisicao': bem_loc.data_aquisicao.strftime('%d/%m/%Y') if bem_loc.data_aquisicao else '-',
            'valor': float(bem_loc.valor_aquisicao or 0.0),
            'valor_fmt': f"{(bem_loc.valor_aquisicao or 0.0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            'url_detalhes': url_for('patrimonio.detalhes_item', item_id=bem_loc.id),
        })

    locais_inventario = sorted(
        mapa_locais.values(),
        key=lambda x: (-x['qtd_bens'], x['nome'].lower())
    )

    # =========================================================================
    # AGRUPAMENTO DE LOTES DISPONÍVEIS PARA TRANSFERÊNCIA POR QUANTIDADE
    # (Ex: 100 Mesas no Depósito -> permite digitar "50" para enviar para Escola X)
    # =========================================================================
    mapa_lotes_transf = {}
    for bem_t in todos_bens_ativos:
        nf_rel_t = bem_t.nota_fiscal_rel
        desc_limpa = (bem_t.descricao or 'Bem sem descrição').strip()
        local_limpo = (bem_t.localizacao or 'Não informada').strip()
        nf_limpa = (
            (bem_t.numero_nota_fiscal or '').strip()
            or (nf_rel_t.numero_nf.strip() if nf_rel_t and nf_rel_t.numero_nf else '')
            or 'S/N'
        )
        forn_limpo = (
            (bem_t.fornecedor_nome or '').strip()
            or (nf_rel_t.fornecedor_nome.strip() if nf_rel_t and nf_rel_t.fornecedor_nome else '')
            or 'Incorporação Direta / Sem Fornecedor'
        )
        chave_lote = (desc_limpa.lower(), local_limpo.lower(), nf_limpa.lower(), forn_limpo.lower())

        if chave_lote not in mapa_lotes_transf:
            mapa_lotes_transf[chave_lote] = {
                'descricao': desc_limpa,
                'categoria': bem_t.categoria or '-',
                'marca_modelo': " / ".join([x for x in [bem_t.marca, bem_t.modelo] if x]) or '-',
                'local_atual': local_limpo,
                'secretaria_id': bem_t.secretaria_id or '',
                'fornecedor': forn_limpo,
                'nota_fiscal': nf_limpa,
                'empenho': bem_t.numero_empenho or (nf_rel_t.numero_empenho if nf_rel_t else None) or '-',
                'valor_unitario': float(bem_t.valor_aquisicao or 0.0),
                'valor_unitario_fmt': f"{(bem_t.valor_aquisicao or 0.0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                'bens_objs': [],
            }
        elif not mapa_lotes_transf[chave_lote]['secretaria_id'] and bem_t.secretaria_id:
            mapa_lotes_transf[chave_lote]['secretaria_id'] = bem_t.secretaria_id
        mapa_lotes_transf[chave_lote]['bens_objs'].append(bem_t)

    lotes_transferencia = []
    for idx_lote, (_, dados_lote) in enumerate(
        sorted(
            mapa_lotes_transf.items(),
            key=lambda item_kv: (-len(item_kv[1]['bens_objs']), item_kv[1]['descricao'].lower())
        ),
        start=1
    ):
        bens_ordenados = sorted(
            dados_lote['bens_objs'],
            key=lambda b: (str(b.numero_patrimonio or 'ZZZZZZZZ'), b.id)
        )
        itens_serializados = [
            {
                'id': b.id,
                'tombamento': b.numero_patrimonio or 'S/N',
                'estado': b.estado_conservacao or 'Bom',
                'serie': b.numero_serie or '',
                'responsavel': b.nome_responsavel_atual or 'Não informado',
                'valor': float(b.valor_aquisicao or 0.0),
            }
            for b in bens_ordenados
        ]
        tomb_ini = itens_serializados[0]['tombamento'] if itens_serializados else 'S/N'
        tomb_fim = itens_serializados[-1]['tombamento'] if itens_serializados else 'S/N'

        lotes_transferencia.append({
            'codigo_lote': f"lote_{idx_lote}",
            'descricao': dados_lote['descricao'],
            'categoria': dados_lote['categoria'],
            'marca_modelo': dados_lote['marca_modelo'],
            'local_atual': dados_lote['local_atual'],
            'secretaria_id': dados_lote['secretaria_id'],
            'fornecedor': dados_lote['fornecedor'],
            'nota_fiscal': dados_lote['nota_fiscal'],
            'empenho': dados_lote['empenho'],
            'qtd_disponivel': len(itens_serializados),
            'valor_unitario': dados_lote['valor_unitario'],
            'valor_unitario_fmt': dados_lote['valor_unitario_fmt'],
            'tombamento_inicial': tomb_ini,
            'tombamento_final': tomb_fim,
            'faixa_tombamento': tomb_ini if len(itens_serializados) == 1 else f"{tomb_ini} até {tomb_fim}",
            'itens': itens_serializados,
        })

    # Lista de locais de destino sugeridos (Locais Cadastrados + Escolas + Locais já usados)
    escolas_ativas = Escola.query.filter_by(status='Ativa').order_by(Escola.nome).all()
    nomes_locais_set = set()
    locais_destino_sugestoes = []

    for loc_c in locais_db:
        n_loc = (loc_c.nome or '').strip()
        if n_loc and n_loc.lower() not in nomes_locais_set:
            nomes_locais_set.add(n_loc.lower())
            locais_destino_sugestoes.append({
                'nome': n_loc,
                'tipo': 'Local Cadastrado',
                'responsavel': loc_c.responsavel_setor or '',
                'secretaria_id': loc_c.secretaria_id or '',
            })

    for esc in escolas_ativas:
        n_esc = (esc.nome or '').strip()
        if n_esc and n_esc.lower() not in nomes_locais_set:
            nomes_locais_set.add(n_esc.lower())
            locais_destino_sugestoes.append({
                'nome': n_esc,
                'tipo': 'Unidade Escolar',
                'responsavel': '',
                'secretaria_id': '',
            })

    for n_leg in locais_legados:
        if n_leg and n_leg.lower() not in nomes_locais_set:
            nomes_locais_set.add(n_leg.lower())
            locais_destino_sugestoes.append({
                'nome': n_leg,
                'tipo': 'Local em Uso',
                'responsavel': '',
                'secretaria_id': '',
            })

    return render_template(
        'patrimonio/lista.html',
        itens=itens_paginados,
        fornecedores_paginados=fornecedores_paginados,
        locais_inventario=locais_inventario,
        lotes_transferencia=lotes_transferencia,
        locais_destino_sugestoes=locais_destino_sugestoes,
        ultima_remessa=session.get('ultima_remessa_patrimonio'),
        kpis=kpis,
        aba_ativa=aba_ativa,
        paginacao_forn={
            'page': page_forn,
            'per_page': per_page_forn,
            'total': total_fornecedores,
            'total_pages': total_pages_forn,
            'inicio': inicio_forn + 1 if total_fornecedores > 0 else 0,
            'fim': min(inicio_forn + per_page_forn, total_fornecedores),
        },
        paginacao_bens={
            'page': page_bens,
            'per_page': per_page_bens,
            'total': total_bens,
            'total_pages': total_pages_bens,
            'inicio': inicio_bens + 1 if total_bens > 0 else 0,
            'fim': min(inicio_bens + per_page_bens, total_bens),
        },
        termo_busca=termo_busca,
        secretarias=secretarias,
        notas_recentes=notas_recentes,
        locais_db=locais_db,
        locais_legados=locais_legados,
        escolas=escolas_ativas,
        filtros={
            'secretaria_id': secretaria_filtro,
            'categoria': categoria_filtro,
            'estado': estado_filtro,
            'situacao': situacao_filtro,
            'nota_fiscal': nf_filtro,
        }
    )


@patrimonio_bp.route('/relatorio-local/pdf')
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def gerar_relatorio_local_pdf():
    """
    Gera o Relatório Oficial de Inventário Setorial e Termo de Guarda por Local (Padrão Governo / TCE / MCASP) em PDF.
    """
    executar_migracao_patrimonio_segura()

    local_selecionado = (request.args.get('local') or '').strip()
    estado_filtro = (request.args.get('estado') or '').strip()
    responsavel_assinatura = (request.args.get('responsavel_setor') or '').strip()
    cargo_assinatura = (request.args.get('cargo_responsavel') or '').strip()
    incluir_valores = request.args.get('incluir_valores', 'sim') != 'nao'

    query = Patrimonio.query.filter(Patrimonio.status != 'Baixado')
    if local_selecionado and local_selecionado != '__TODOS__':
        query = query.filter(Patrimonio.localizacao == local_selecionado)
    if estado_filtro:
        query = query.filter(Patrimonio.estado_conservacao == estado_filtro)

    bens = query.order_by(Patrimonio.localizacao, Patrimonio.descricao, Patrimonio.numero_patrimonio).all()

    titulo_local = local_selecionado if (local_selecionado and local_selecionado != '__TODOS__') else "TODOS OS LOCAIS / INVENTÁRIO GERAL"

    # Descobre secretaria e responsável padrão se não foram passados
    secretaria_nome = "PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ"
    for b in bens:
        if b.secretaria and b.secretaria.nome:
            secretaria_nome = b.secretaria.nome.upper()
            break
    if not responsavel_assinatura:
        for b in bens:
            if b.nome_responsavel_atual:
                responsavel_assinatura = b.nome_responsavel_atual
                if not cargo_assinatura and b.responsavel_cargo:
                    cargo_assinatura = b.responsavel_cargo
                break

    buffer = BytesIO()
    page_width, page_height = landscape(A4)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=3.4 * cm,
        bottomMargin=1.8 * cm,
        title=f"Inventário Patrimonial - {titulo_local}"
    )

    styles = getSampleStyleSheet()
    cor_gov_azul = colors.HexColor("#0f2942")
    cor_gov_secundaria = colors.HexColor("#1e4e79")
    cor_cinza_claro = colors.HexColor("#f1f5f9")
    cor_borda = colors.HexColor("#cbd5e1")

    style_titulo_doc = ParagraphStyle(
        'TituloDocGov',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=15,
        textColor=cor_gov_azul,
        alignment=TA_CENTER,
        spaceAfter=2
    )
    style_subtitulo_doc = ParagraphStyle(
        'SubtituloDocGov',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#475569"),
        alignment=TA_CENTER,
        spaceAfter=10
    )
    style_celula = ParagraphStyle(
        'CelulaPat',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#1e293b")
    )
    style_celula_bold = ParagraphStyle(
        'CelulaPatBold',
        parent=style_celula,
        fontName='Helvetica-Bold'
    )
    style_celula_center = ParagraphStyle(
        'CelulaPatCenter',
        parent=style_celula,
        alignment=TA_CENTER
    )
    style_celula_right = ParagraphStyle(
        'CelulaPatRight',
        parent=style_celula,
        alignment=TA_RIGHT
    )
    style_header_tabela = ParagraphStyle(
        'HeaderPat',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9.5,
        textColor=colors.white,
        alignment=TA_CENTER
    )
    style_termo = ParagraphStyle(
        'TermoJuridico',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11.5,
        alignment=TA_JUSTIFY,
        textColor=colors.HexColor("#1e293b")
    )

    data_emissao_str = obter_data_hora_br_str()
    protocolo_relatorio = f"INV-{datetime.now().strftime('%Y%m%d-%H%M')}"
    usuario_emissor = session.get('username') or getattr(current_user, 'username', 'Controle Interno')

    # Gera QR Code de verificação do relatório
    url_verificacao = url_for('patrimonio.listar_itens', _external=True)
    qr = qrcode.make(f"PREFEITURA DE VALENCA DO PIAUI | {protocolo_relatorio} | LOCAL: {titulo_local} | {url_verificacao}")
    qr_buffer = BytesIO()
    qr.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)
    qr_reader = ImageReader(qr_buffer)

    def desenhar_cabecalho_rodape_gov(canvas, doc_obj):
        canvas.saveState()
        basedir = current_app.root_path
        logo_path = os.path.join(basedir, 'static', 'logo.png')
        timbre_path = os.path.join(basedir, 'static', 'timbre.jpg')

        # Faixa superior institucional
        canvas.setFillColor(cor_gov_azul)
        canvas.rect(1.2 * cm, page_height - 1.0 * cm, page_width - 2.4 * cm, 0.15 * cm, fill=1, stroke=0)

        if os.path.exists(logo_path):
            try:
                canvas.drawImage(logo_path, 1.3 * cm, page_height - 3.0 * cm, width=1.8 * cm, height=1.8 * cm, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        elif os.path.exists(timbre_path):
            try:
                canvas.drawImage(timbre_path, 1.3 * cm, page_height - 2.9 * cm, width=3.5 * cm, height=1.6 * cm, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

        # Textos Oficiais do Governo Municipal
        canvas.setFillColor(cor_gov_azul)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawCentredString(page_width / 2.0, page_height - 1.55 * cm, "ESTADO DO PIAUÍ — PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ")

        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawCentredString(page_width / 2.0, page_height - 1.95 * cm, secretaria_nome)

        canvas.setFillColor(colors.HexColor("#334155"))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawCentredString(
            page_width / 2.0,
            page_height - 2.35 * cm,
            "CONTROLADORIA GERAL DO MUNICÍPIO • COORDENADORIA DE CONTROLE PATRIMONIAL E ALMOXARIFADO"
        )
        canvas.drawCentredString(
            page_width / 2.0,
            page_height - 2.70 * cm,
            "CNPJ: 06.095.146/0001-44 • Rua Epaminondas Nogueira, nº 1425, Centro - Valença do Piauí/PI"
        )

        # QR Code e protocolo no canto superior direito
        try:
            canvas.drawImage(qr_reader, page_width - 3.1 * cm, page_height - 3.0 * cm, width=1.8 * cm, height=1.8 * cm)
        except Exception:
            pass

        # Linha divisória dupla (Verde e Amarela / Institucional)
        canvas.setStrokeColor(colors.HexColor("#198754"))
        canvas.setLineWidth(1.5)
        canvas.line(1.2 * cm, page_height - 3.1 * cm, page_width - 1.2 * cm, page_height - 3.1 * cm)

        # Rodapé Oficial
        canvas.setStrokeColor(cor_borda)
        canvas.setLineWidth(0.7)
        canvas.line(1.2 * cm, 1.35 * cm, page_width - 1.2 * cm, 1.35 * cm)

        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(
            1.2 * cm,
            0.95 * cm,
            f"Gestor 360º - Sistema Integrado de Gestão Pública e Controle Interno Auditável | Protocolo: {protocolo_relatorio} | Emitido por: {usuario_emissor} em {data_emissao_str}"
        )
        canvas.drawRightString(
            page_width - 1.2 * cm,
            0.95 * cm,
            f"Página {doc_obj.page}"
        )
        canvas.restoreState()

    elementos = []

    # Título do Relatório
    elementos.append(Paragraph("RELATÓRIO OFICIAL DE INVENTÁRIO SETORIAL E TERMO DE GUARDA DE BENS PATRIMONIAIS", style_titulo_doc))
    elementos.append(Paragraph("Demonstrativo analítico de bens permanentes alocados por unidade/setor em conformidade com as normas do Controle Interno e MCASP/TCE", style_subtitulo_doc))

    # =========================================================================
    # QUADRO 1: METADADOS DO LOCAL / UNIDADE E DETENTOR DA GUARDA
    # =========================================================================
    qtd_total = len(bens)
    soma_total = sum((b.valor_aquisicao or 0.0) for b in bens)
    soma_total_fmt = f"R$ {soma_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    dados_quadro_local = [
        [
            Paragraph("<b>UNIDADE / LOCALIZAÇÃO FÍSICA:</b>", style_celula),
            Paragraph(f"<b>{titulo_local}</b>", style_celula_bold),
            Paragraph("<b>EXERCÍCIO / PROTOCOLO:</b>", style_celula),
            Paragraph(f"{datetime.now().year} / {protocolo_relatorio}", style_celula),
        ],
        [
            Paragraph("<b>ÓRGÃO / SECRETARIA GESTORA:</b>", style_celula),
            Paragraph(secretaria_nome, style_celula),
            Paragraph("<b>TOTAL DE BENS NO LOCAL:</b>", style_celula),
            Paragraph(f"<b>{qtd_total} item(ns) patrimonial(is)</b>", style_celula_bold),
        ],
        [
            Paragraph("<b>RESPONSÁVEL PELA GUARDA / SETOR:</b>", style_celula),
            Paragraph(
                f"{responsavel_assinatura or 'Não especificado'}"
                f"{' (' + cargo_assinatura + ')' if cargo_assinatura else ''}",
                style_celula
            ),
            Paragraph("<b>VALOR CONTÁBIL ACUMULADO:</b>", style_celula),
            Paragraph(f"<b>{soma_total_fmt if incluir_valores else 'Oculto'}</b>", style_celula_bold),
        ],
    ]

    tabela_meta = Table(dados_quadro_local, colWidths=[5.2 * cm, 11.3 * cm, 5.0 * cm, 5.8 * cm])
    tabela_meta.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), cor_cinza_claro),
        ('BACKGROUND', (2, 0), (2, -1), cor_cinza_claro),
        ('GRID', (0, 0), (-1, -1), 0.5, cor_borda),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elementos.append(tabela_meta)
    elementos.append(Spacer(1, 0.35 * cm))

    # =========================================================================
    # QUADRO 2: TABELA ANALÍTICA DE BENS PATRIMONIAIS DO LOCAL
    # =========================================================================
    cabecalho_tabela = [
        Paragraph("ITEM", style_header_tabela),
        Paragraph("Nº TOMBAMENTO", style_header_tabela),
        Paragraph("DESCRIÇÃO DO BEM / ESPECIFICAÇÃO TÉCNICA", style_header_tabela),
        Paragraph("MARCA / MODELO / SÉRIE", style_header_tabela),
        Paragraph("NOTA FISCAL / EMPENHO", style_header_tabela),
        Paragraph("RESPONSÁVEL DIRETO", style_header_tabela),
        Paragraph("ESTADO DO BEM / USO", style_header_tabela),
        Paragraph("VALOR (R$)", style_header_tabela),
        Paragraph("CONF.", style_header_tabela),
    ]
    linhas_tabela = [cabecalho_tabela]

    contagem_estados = {'Novo': 0, 'Bom': 0, 'Regular': 0, 'Ruim': 0, 'Inservível': 0}

    if bens:
        for idx, b in enumerate(bens, start=1):
            est = b.estado_conservacao or 'Bom'
            contagem_estados[est] = contagem_estados.get(est, 0) + 1

            desc_completa = f"<b>{b.descricao}</b>"
            if b.categoria:
                desc_completa += f"<br/><font size=6.5 color='#64748b'>Grupo: {b.categoria}</font>"
            if local_selecionado == '__TODOS__' and b.localizacao:
                desc_completa += f"<br/><font size=6.5 color='#0f4c81'>Local: {b.localizacao}</font>"

            marca_mod = " / ".join([x for x in [b.marca, b.modelo] if x]) or "-"
            if b.numero_serie:
                marca_mod += f"<br/><font size=6.5 color='#475569'>Série: {b.numero_serie}</font>"

            nf_emp = f"NF: {b.numero_nota_fiscal or 'S/N'}"
            if b.numero_empenho:
                nf_emp += f"<br/>NE: {b.numero_empenho}"
            if b.data_aquisicao:
                nf_emp += f"<br/><font size=6.5 color='#64748b'>Aq: {b.data_aquisicao.strftime('%d/%m/%Y')}</font>"

            estado_uso_str = f"<b>{est.upper()}</b><br/><font size=6.5 color='#475569'>{b.situacao_uso or 'Em uso'}</font>"

            val_fmt = f"{(b.valor_aquisicao or 0.0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

            linhas_tabela.append([
                Paragraph(f"{idx:02d}", style_celula_center),
                Paragraph(f"<b>{b.numero_patrimonio or 'S/N'}</b>", style_celula_center),
                Paragraph(desc_completa, style_celula),
                Paragraph(marca_mod, style_celula),
                Paragraph(nf_emp, style_celula_center),
                Paragraph(b.nome_responsavel_atual or responsavel_assinatura or "-", style_celula),
                Paragraph(estado_uso_str, style_celula_center),
                Paragraph(val_fmt if incluir_valores else "-", style_celula_right),
                Paragraph("[   ]", style_celula_center),
            ])
    else:
        linhas_tabela.append([
            Paragraph("-", style_celula_center),
            Paragraph("-", style_celula_center),
            Paragraph("Nenhum bem patrimonial ativo registrado para este local/filtro.", style_celula),
            Paragraph("-", style_celula_center),
            Paragraph("-", style_celula_center),
            Paragraph("-", style_celula_center),
            Paragraph("-", style_celula_center),
            Paragraph("0,00", style_celula_right),
            Paragraph("[   ]", style_celula_center),
        ])

    # Linha de Totalização Final da Tabela
    linhas_tabela.append([
        Paragraph("", style_celula),
        Paragraph("", style_celula),
        Paragraph(f"<b>TOTAL GERAL DE BENS RELACIONADOS NO SETOR: {qtd_total} UNIDADE(S)</b>", style_celula_bold),
        Paragraph("", style_celula),
        Paragraph("", style_celula),
        Paragraph("", style_celula),
        Paragraph("<b>VALOR TOTAL:</b>", style_celula_center),
        Paragraph(f"<b>{soma_total_fmt if incluir_valores else '-'}</b>", style_celula_right),
        Paragraph("", style_celula),
    ])

    larguras_colunas = [
        1.1 * cm,  # ITEM
        2.5 * cm,  # TOMBAMENTO
        7.3 * cm,  # DESCRIÇÃO
        3.5 * cm,  # MARCA/MODELO/SÉRIE
        2.8 * cm,  # NF/EMPENHO
        3.6 * cm,  # RESPONSÁVEL
        2.6 * cm,  # ESTADO DO BEM
        2.5 * cm,  # VALOR
        1.4 * cm,  # CONF.
    ]

    tabela_bens = Table(linhas_tabela, colWidths=larguras_colunas, repeatRows=1)
    estilo_tabela_bens = [
        ('BACKGROUND', (0, 0), (-1, 0), cor_gov_azul),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.45, cor_borda),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        # Linha final de totalização
        ('SPAN', (2, -1), (5, -1)),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
    ]
    # Zebra striping nas linhas de dados
    for row_idx in range(1, len(linhas_tabela) - 1):
        if row_idx % 2 == 0:
            estilo_tabela_bens.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor("#f8fafc")))

    tabela_bens.setStyle(TableStyle(estilo_tabela_bens))
    elementos.append(tabela_bens)
    elementos.append(Spacer(1, 0.35 * cm))

    # =========================================================================
    # QUADRO 3: SÍNTESE DO ESTADO DE CONSERVAÇÃO E TERMO DE GUARDA + ASSINATURAS
    # =========================================================================
    resumo_conservacao_data = [[
        Paragraph("<b>SÍNTESE POR ESTADO DE CONSERVAÇÃO:</b>", style_celula_bold),
        Paragraph(f"Novo / Excelente: <b>{contagem_estados.get('Novo', 0)}</b>", style_celula_center),
        Paragraph(f"Bom Estado: <b>{contagem_estados.get('Bom', 0)}</b>", style_celula_center),
        Paragraph(f"Estado Regular: <b>{contagem_estados.get('Regular', 0)}</b>", style_celula_center),
        Paragraph(f"Ruim / Manutenção: <b>{contagem_estados.get('Ruim', 0)}</b>", style_celula_center),
        Paragraph(f"Inservível: <b>{contagem_estados.get('Inservível', 0)}</b>", style_celula_center),
    ]]
    tabela_resumo_est = Table(
        resumo_conservacao_data,
        colWidths=[6.3 * cm, 4.2 * cm, 4.2 * cm, 4.2 * cm, 4.2 * cm, 4.2 * cm]
    )
    tabela_resumo_est.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), cor_cinza_claro),
        ('GRID', (0, 0), (-1, -1), 0.45, cor_borda),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))

    texto_declaracao = (
        "<b>TERMO DE GUARDA, CONFERÊNCIA E RESPONSABILIDADE PATRIMONIAL:</b> "
        "Pelo presente instrumento oficial de Controle Interno, declaramos que os bens permanentes acima discriminados encontram-se "
        f"alocados no setor/local <b>{titulo_local}</b>, tendo sido conferidos fisicamente quanto ao seu número de tombamento, especificações "
        "e estado de conservação. O responsável setorial abaixo assinado assume o compromisso de zelar pela guarda, conservação e uso "
        "exclusivo no interesse do serviço público, comunicando formalmente à Coordenadoria de Patrimônio qualquer movimentação, avaria ou extravio, "
        "em observância às normas da Administração Pública e do Tribunal de Contas."
    )

    nome_resp_assinatura = responsavel_assinatura or "___________________________________________________"
    sub_resp_assinatura = cargo_assinatura or "Responsável pelo Setor / Detentor da Carga Patrimonial"

    bloco_assinaturas = Table(
        [
            [
                Paragraph("___________________________________________________", style_celula_center),
                Paragraph("___________________________________________________", style_celula_center),
                Paragraph("___________________________________________________", style_celula_center),
            ],
            [
                Paragraph(f"<b>{nome_resp_assinatura}</b><br/><font size=7 color='#475569'>{sub_resp_assinatura}</font>", style_celula_center),
                Paragraph(f"<b>{usuario_emissor.upper()}</b><br/><font size=7 color='#475569'>Coordenadoria de Patrimônio / Emissor</font>", style_celula_center),
                Paragraph("<b>CONTROLADORIA GERAL / COMISSÃO</b><br/><font size=7 color='#475569'>Visto de Auditoria e Controle Interno</font>", style_celula_center),
            ],
        ],
        colWidths=[9.1 * cm, 9.1 * cm, 9.1 * cm]
    )
    bloco_assinaturas.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))

    elementos.append(KeepTogether([
        tabela_resumo_est,
        Spacer(1, 0.25 * cm),
        Paragraph(texto_declaracao, style_termo),
        Spacer(1, 0.85 * cm),
        bloco_assinaturas,
    ]))

    doc.build(elementos, onFirstPage=desenhar_cabecalho_rodape_gov, onLaterPages=desenhar_cabecalho_rodape_gov)
    buffer.seek(0)

    nome_limpo = re.sub(r'[^a-zA-Z0-9_-]', '_', titulo_local)[:40]
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f"Inventario_Patrimonial_{nome_limpo}_{datetime.now().strftime('%Y%m%d')}.pdf"
    )


@patrimonio_bp.route('/api/locais/novo', methods=['POST'])
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def api_novo_local():
    """Endpoint AJAX para cadastrar um novo Local de Patrimônio no ato do cadastro do bem."""
    executar_migracao_patrimonio_segura()
    data = request.get_json(silent=True) or request.form

    nome = (data.get('nome') or '').strip()
    predio_unidade = (data.get('predio_unidade') or '').strip()
    sala_setor = (data.get('sala_setor') or '').strip()
    endereco = (data.get('endereco') or '').strip()
    responsavel_setor = (data.get('responsavel_setor') or '').strip()
    sec_id = data.get('secretaria_id')
    secretaria_id = int(sec_id) if sec_id and str(sec_id).isdigit() else None

    if not nome:
        # Monta o nome automaticamente se informou prédio e sala
        partes = [p for p in [predio_unidade, sala_setor] if p]
        nome = " - ".join(partes)

    if not nome:
        return jsonify({'success': False, 'message': 'Informe o nome do local ou unidade/setor.'}), 400

    # Se informou prédio e sala separadamente e o nome não contém a sala, compõe uma descrição clara
    nome_formatado = nome
    if predio_unidade and sala_setor and predio_unidade.lower() not in nome.lower():
        nome_formatado = f"{predio_unidade} - { nome }"
    elif sala_setor and sala_setor.lower() not in nome_formatado.lower():
        nome_formatado = f"{nome_formatado} ({sala_setor})"

    try:
        existente = LocalPatrimonio.query.filter(
            func.lower(LocalPatrimonio.nome) == nome_formatado.lower()
        ).first()
        if existente:
            return jsonify({
                'success': True,
                'message': 'Local já existente selecionado.',
                'local': {
                    'id': existente.id,
                    'nome': existente.nome,
                    'predio_unidade': existente.predio_unidade or '',
                    'sala_setor': existente.sala_setor or '',
                    'responsavel_setor': existente.responsavel_setor or '',
                    'secretaria_id': existente.secretaria_id,
                }
            })

        novo_local = LocalPatrimonio(
            nome=nome_formatado,
            predio_unidade=predio_unidade or None,
            sala_setor=sala_setor or None,
            endereco=endereco or None,
            responsavel_setor=responsavel_setor or None,
            secretaria_id=secretaria_id,
            ativo=True
        )
        db.session.add(novo_local)
        db.session.commit()

        try:
            registrar_log(f"Cadastrou novo local patrimonial: {novo_local.nome}")
        except Exception:
            pass

        return jsonify({
            'success': True,
            'message': 'Local cadastrado com sucesso!',
            'local': {
                'id': novo_local.id,
                'nome': novo_local.nome,
                'predio_unidade': novo_local.predio_unidade or '',
                'sala_setor': novo_local.sala_setor or '',
                'responsavel_setor': novo_local.responsavel_setor or '',
                'secretaria_id': novo_local.secretaria_id,
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erro ao salvar local: {str(e)}'}), 500


@patrimonio_bp.route('/api/proximo-tombamento')
@login_required
def api_proximo_tombamento():
    """Sugere o próximo número de tombamento sequencial."""
    executar_migracao_patrimonio_segura()
    ano_atual = datetime.now().year
    prefixo = f"{ano_atual}"

    todos = db.session.query(Patrimonio.numero_patrimonio).filter(
        Patrimonio.numero_patrimonio.isnot(None)
    ).all()

    maior_num = 0
    for (num_pat,) in todos:
        if not num_pat:
            continue
        somente_digitos = re.sub(r'\D', '', str(num_pat))
        if somente_digitos.startswith(prefixo) and len(somente_digitos) >= 8:
            try:
                seq = int(somente_digitos[4:])
                if seq > maior_num:
                    maior_num = seq
            except ValueError:
                pass
        elif somente_digitos.isdigit():
            try:
                val = int(somente_digitos)
                if val < 999999 and val > maior_num:
                    maior_num = val
            except ValueError:
                pass

    sugestao = f"{prefixo}{str(maior_num + 1).zfill(4)}"
    while Patrimonio.query.filter_by(numero_patrimonio=sugestao).first() is not None:
        maior_num += 1
        sugestao = f"{prefixo}{str(maior_num + 1).zfill(4)}"

    return jsonify({'sugestao': sugestao})


@patrimonio_bp.route('/item/novo', methods=['GET', 'POST'])
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def novo_item():
    executar_migracao_patrimonio_segura()

    if request.method == 'POST':
        try:
            # =========================================================================
            # 1. DADOS GERAIS DA AQUISIÇÃO / NOTA FISCAL / CONTROLE INTERNO
            # =========================================================================
            forma_aquisicao = (request.form.get('forma_aquisicao') or 'Compra / Licitação').strip()
            numero_nf = (request.form.get('numero_nota_fiscal') or '').strip() or None
            serie_nf = (request.form.get('serie_nota_fiscal') or '').strip() or None
            chave_nfe = re.sub(r'\s+', '', (request.form.get('chave_nfe') or '').strip()) or None
            data_nf = _parse_data(request.form.get('data_nota_fiscal'))
            data_atesto = _parse_data(request.form.get('data_atesto')) or _parse_data(request.form.get('data_aquisicao'))
            data_aquisicao = data_nf or data_atesto or date.today()

            fornecedor_nome = (request.form.get('fornecedor_nome') or '').strip() or None
            fornecedor_cnpj = (request.form.get('fornecedor_cnpj') or '').strip() or None
            numero_processo = (request.form.get('numero_processo') or '').strip() or None
            modalidade_licitacao = (request.form.get('modalidade_licitacao') or '').strip() or None
            numero_contrato = (request.form.get('numero_contrato') or '').strip() or None
            numero_empenho = (request.form.get('numero_empenho') or '').strip() or None
            fonte_recursos = (request.form.get('fonte_recursos') or '').strip() or None
            conta_contabil = (request.form.get('conta_contabil') or '').strip() or None
            observacoes_nf = (request.form.get('observacoes_nf') or '').strip() or None

            # Upload opcional do PDF/Imagem da Nota Fiscal
            arquivo_nf_file = request.files.get('arquivo_nf')
            arquivo_nf_link = None
            if arquivo_nf_file and arquivo_nf_file.filename != '':
                arquivo_nf_link = upload_arquivo_para_nuvem(arquivo_nf_file, pasta="patrimonio_nf")

            # =========================================================================
            # 2. LOCALIZAÇÃO E RESPONSABILIDADE PADRÃO DO LOTE / CADASTRO
            # =========================================================================
            sec_id = request.form.get('secretaria_id')
            secretaria_id_padrao = int(sec_id) if sec_id and str(sec_id).isdigit() else None

            localizacao_padrao = (request.form.get('localizacao') or '').strip()
            local_id_raw = request.form.get('local_id')
            local_id_padrao = int(local_id_raw) if local_id_raw and str(local_id_raw).isdigit() else None

            if not localizacao_padrao and local_id_padrao:
                loc_obj = LocalPatrimonio.query.get(local_id_padrao)
                if loc_obj:
                    localizacao_padrao = loc_obj.nome
            elif localizacao_padrao and not local_id_padrao:
                loc_obj = LocalPatrimonio.query.filter(
                    func.lower(LocalPatrimonio.nome) == localizacao_padrao.lower()
                ).first()
                if loc_obj:
                    local_id_padrao = loc_obj.id

            if not localizacao_padrao:
                localizacao_padrao = "Não informada"

            # Responsável pelo bem: campo livre digitado pelo usuário
            responsavel_nome_padrao = (request.form.get('responsavel_nome') or '').strip() or None
            responsavel_cargo_padrao = (request.form.get('responsavel_cargo') or '').strip() or None
            responsavel_matricula_padrao = (request.form.get('responsavel_matricula') or '').strip() or None

            # =========================================================================
            # 3. COLETA DOS MÚLTIPLOS PRODUTOS DA NOTA FISCAL (OU ITEM ÚNICO)
            # =========================================================================
            descricoes = request.form.getlist('item_descricao[]')
            categorias = request.form.getlist('item_categoria[]')
            quantidades = request.form.getlist('item_quantidade[]')
            tombamentos = request.form.getlist('item_tombamento[]')
            marcas = request.form.getlist('item_marca[]')
            modelos = request.form.getlist('item_modelo[]')
            numeros_serie = request.form.getlist('item_numero_serie[]')
            valores_unitarios = request.form.getlist('item_valor_unitario[]')
            estados = request.form.getlist('item_estado_conservacao[]')
            situacoes = request.form.getlist('item_situacao_uso[]')
            vidas_uteis = request.form.getlist('item_vida_util[]')
            garantias = request.form.getlist('item_garantia_ate[]')
            locais_especificos = request.form.getlist('item_localizacao_especifica[]')
            responsaveis_especificos = request.form.getlist('item_responsavel_especifico[]')
            especificacoes = request.form.getlist('item_especificacao[]')

            # Fallback caso venha do formato antigo de campo simples
            if not descricoes and request.form.get('nome_bem'):
                descricoes = [request.form.get('nome_bem')]
                categorias = [request.form.get('categoria', '')]
                quantidades = ['1']
                tombamentos = [request.form.get('tombamento', '')]
                marcas = [request.form.get('marca', '')]
                modelos = [request.form.get('modelo', '')]
                numeros_serie = [request.form.get('numero_serie', '')]
                valores_unitarios = [request.form.get('valor_compra', '0')]
                estados = [request.form.get('estado_conservacao', 'Bom')]
                situacoes = [request.form.get('situacao_uso', 'Em uso')]
                vidas_uteis = ['5']
                garantias = ['']
                locais_especificos = ['']
                responsaveis_especificos = ['']
                especificacoes = [request.form.get('descricao', '')]

            produtos_validos = [d.strip() for d in descricoes if d and d.strip()]
            if not produtos_validos:
                flash("Informe pelo menos um produto/bem patrimonial para cadastrar.", "warning")
                return redirect(url_for('patrimonio.novo_item'))

            # Cria o registro mestre da Nota Fiscal se houver dados de Nota Fiscal ou múltiplos produtos
            nota_fiscal_obj = None
            if numero_nf or fornecedor_nome or numero_empenho or len(produtos_validos) > 1:
                nota_fiscal_obj = NotaFiscalPatrimonio(
                    numero_nf=numero_nf or f"S/NF-{datetime.now().strftime('%Y%m%d%H%M')}",
                    serie_nf=serie_nf,
                    chave_acesso=chave_nfe,
                    data_emissao=data_nf,
                    data_recebimento=data_atesto or date.today(),
                    fornecedor_nome=fornecedor_nome,
                    fornecedor_cnpj=fornecedor_cnpj,
                    forma_aquisicao=forma_aquisicao,
                    numero_processo=numero_processo,
                    modalidade_licitacao=modalidade_licitacao,
                    numero_contrato=numero_contrato,
                    numero_empenho=numero_empenho,
                    fonte_recursos=fonte_recursos,
                    conta_contabil=conta_contabil,
                    arquivo_nf_url=arquivo_nf_link,
                    observacoes=observacoes_nf,
                    usuario_registro=session.get('username') or getattr(current_user, 'username', 'Sistema'),
                    valor_total_nf=0.0
                )
                db.session.add(nota_fiscal_obj)
                db.session.flush()  # Obtém o ID da nota fiscal antes de criar os bens

            total_bens_criados = 0
            soma_valor_total_nf = 0.0
            tombamentos_usados_no_lote = set()

            for idx, desc_raw in enumerate(descricoes):
                nome_produto = (desc_raw or '').strip()
                if not nome_produto:
                    continue

                categoria_item = (categorias[idx] if idx < len(categorias) else '') or 'Outros'
                qtd_raw = quantidades[idx] if idx < len(quantidades) else '1'
                try:
                    qtd_unidades = max(1, min(500, int(qtd_raw)))
                except ValueError:
                    qtd_unidades = 1

                tombamento_base = (tombamentos[idx] if idx < len(tombamentos) else '').strip() or None
                marca_item = (marcas[idx] if idx < len(marcas) else '').strip() or None
                modelo_item = (modelos[idx] if idx < len(modelos) else '').strip() or None
                serie_raw = (numeros_serie[idx] if idx < len(numeros_serie) else '').strip()
                valor_unit = _parse_moeda(valores_unitarios[idx] if idx < len(valores_unitarios) else '0')
                estado_item = (estados[idx] if idx < len(estados) else 'Bom') or 'Bom'
                situacao_item = (situacoes[idx] if idx < len(situacoes) else 'Em uso') or 'Em uso'

                vida_raw = vidas_uteis[idx] if idx < len(vidas_uteis) else '5'
                try:
                    vida_util_item = int(vida_raw) if vida_raw else 5
                except ValueError:
                    vida_util_item = 5

                garantia_item = _parse_data(garantias[idx] if idx < len(garantias) else None)
                local_especifico = (locais_especificos[idx] if idx < len(locais_especificos) else '').strip()
                resp_especifico = (responsaveis_especificos[idx] if idx < len(responsaveis_especificos) else '').strip()
                espec_item = (especificacoes[idx] if idx < len(especificacoes) else '').strip() or None

                # Upload de foto específica deste produto (ou fallback foto_bem)
                foto_file = request.files.get(f'foto_bem_{idx}')
                if not foto_file and idx == 0:
                    foto_file = request.files.get('foto_bem')
                foto_link_item = None
                if foto_file and foto_file.filename != '':
                    foto_link_item = upload_arquivo_para_nuvem(foto_file, pasta="patrimonio")

                # Se o usuário informou múltiplos números de série separados por vírgula ou ponto-e-vírgula
                lista_series = [s.strip() for s in re.split(r'[,;\n]+', serie_raw) if s.strip()] if serie_raw else []

                # Define localização e responsável efetivos para este produto
                local_final = local_especifico if local_especifico else localizacao_padrao
                local_id_final = local_id_padrao if not local_especifico else None
                if local_especifico:
                    loc_esp_obj = LocalPatrimonio.query.filter(
                        func.lower(LocalPatrimonio.nome) == local_especifico.lower()
                    ).first()
                    if loc_esp_obj:
                        local_id_final = loc_esp_obj.id

                resp_nome_final = resp_especifico if resp_especifico else responsavel_nome_padrao

                for u in range(qtd_unidades):
                    tombamento_unidade = _incrementar_tombamento(tombamento_base, u) if tombamento_base else None

                    # Valida duplicidade de tombamento
                    if tombamento_unidade:
                        if tombamento_unidade in tombamentos_usados_no_lote:
                            raise ValueError(f"O Nº de Tombamento '{tombamento_unidade}' está repetido entre os itens informados.")
                        ja_existe = Patrimonio.query.filter_by(numero_patrimonio=tombamento_unidade).first()
                        if ja_existe:
                            raise ValueError(f"O Nº de Tombamento '{tombamento_unidade}' já existe cadastrado no bem '{ja_existe.descricao}'.")
                        tombamentos_usados_no_lote.add(tombamento_unidade)

                    serie_unidade = None
                    if u < len(lista_series):
                        serie_unidade = lista_series[u]
                    elif len(lista_series) == 1 and qtd_unidades == 1:
                        serie_unidade = lista_series[0]

                    novo_bem = Patrimonio(
                        numero_patrimonio=tombamento_unidade,
                        descricao=nome_produto,
                        categoria=categoria_item,
                        marca=marca_item,
                        modelo=modelo_item,
                        numero_serie=serie_unidade,
                        especificacao_tecnica=espec_item,
                        valor_aquisicao=valor_unit,
                        data_aquisicao=data_aquisicao,
                        estado_conservacao=estado_item,
                        situacao_uso=situacao_item,
                        status="Ativo",
                        localizacao=local_final,
                        local_id=local_id_final,
                        observacoes=espec_item,
                        foto_url=foto_link_item,
                        arquivo_nf_url=arquivo_nf_link,
                        responsavel_nome=resp_nome_final,
                        responsavel_cargo=responsavel_cargo_padrao,
                        responsavel_matricula=responsavel_matricula_padrao,
                        secretaria_id=secretaria_id_padrao,
                        nota_fiscal_id=nota_fiscal_obj.id if nota_fiscal_obj else None,
                        forma_aquisicao=forma_aquisicao,
                        numero_nota_fiscal=numero_nf,
                        serie_nota_fiscal=serie_nf,
                        chave_nfe=chave_nfe,
                        data_nota_fiscal=data_nf,
                        fornecedor_nome=fornecedor_nome,
                        fornecedor_cnpj=fornecedor_cnpj,
                        numero_empenho=numero_empenho,
                        numero_processo=numero_processo,
                        modalidade_licitacao=modalidade_licitacao,
                        numero_contrato=numero_contrato,
                        fonte_recursos=fonte_recursos,
                        conta_contabil=conta_contabil,
                        vida_util_anos=vida_util_item,
                        garantia_ate=garantia_item,
                        data_atesto=data_atesto,
                    )
                    db.session.add(novo_bem)
                    total_bens_criados += 1
                    soma_valor_total_nf += valor_unit

            if nota_fiscal_obj:
                valor_nf_informado = _parse_moeda(request.form.get('valor_total_nf'))
                nota_fiscal_obj.valor_total_nf = valor_nf_informado if valor_nf_informado > 0 else soma_valor_total_nf

            db.session.commit()

            try:
                resumo_nf = f" (NF: {numero_nf})" if numero_nf else ""
                registrar_log(
                    f"Incorporou {total_bens_criados} bem(ns) patrimonial(is){resumo_nf} - Total: R$ {soma_valor_total_nf:.2f}"
                )
            except Exception:
                pass

            if total_bens_criados == 1:
                flash("Bem patrimonial cadastrado e incorporado com sucesso!", "success")
            else:
                flash(
                    f"Incorporação concluída com sucesso! Foram cadastrados {total_bens_criados} bens patrimoniais "
                    f"{'vinculados à NF ' + numero_nf if numero_nf else 'no lote'}.",
                    "success"
                )
            return redirect(url_for('patrimonio.listar_itens'))

        except ValueError as ve:
            db.session.rollback()
            flash(f"Atenção: {str(ve)}", "warning")
        except Exception as e:
            db.session.rollback()
            erro_str = str(e)
            print(f"Erro ao cadastrar patrimônio: {erro_str}")
            if "UNIQUE constraint failed" in erro_str or "Duplicate" in erro_str:
                flash("Erro: O Número de Tombamento informado já existe cadastrado em outro item.", "danger")
            elif "NOT NULL constraint failed" in erro_str:
                flash("Erro: Preencha todos os campos obrigatórios (Nome do Bem e Localização).", "danger")
            else:
                flash(f"Falha ao salvar no banco de dados: {erro_str[:120]}", "danger")

    servidores = Servidor.query.order_by(Servidor.nome).all()
    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    fornecedores = Fornecedor.query.order_by(Fornecedor.nome).all()
    escolas = Escola.query.order_by(Escola.nome).all()
    locais_db, locais_legados = _obter_locais_disponiveis()

    return render_template(
        'patrimonio/form.html',
        servidores=servidores,
        secretarias=secretarias,
        fornecedores=fornecedores,
        escolas=escolas,
        locais_db=locais_db,
        locais_legados=locais_legados,
        patrimonio=None,
        item=None
    )


@patrimonio_bp.route('/item/editar/<int:item_id>', methods=['GET', 'POST'])
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def editar_item(item_id):
    executar_migracao_patrimonio_segura()
    item = Patrimonio.query.get_or_404(item_id)

    if request.method == 'POST':
        try:
            tombamento = (request.form.get('tombamento') or request.form.get('numero_patrimonio') or '').strip() or None
            if tombamento and tombamento != item.numero_patrimonio:
                conflito = Patrimonio.query.filter(
                    Patrimonio.numero_patrimonio == tombamento,
                    Patrimonio.id != item.id
                ).first()
                if conflito:
                    flash(f"O Nº de Tombamento '{tombamento}' já pertence a outro item ({conflito.descricao}).", "danger")
                    return redirect(url_for('patrimonio.editar_item', item_id=item.id))
            item.numero_patrimonio = tombamento

            nome_bem = (request.form.get('nome_bem') or request.form.get('descricao') or '').strip()
            if nome_bem:
                item.descricao = nome_bem

            item.categoria = (request.form.get('categoria') or '').strip() or item.categoria
            item.marca = (request.form.get('marca') or '').strip() or None
            item.modelo = (request.form.get('modelo') or '').strip() or None
            item.numero_serie = (request.form.get('numero_serie') or '').strip() or None
            item.especificacao_tecnica = (request.form.get('especificacao_tecnica') or request.form.get('observacoes') or '').strip() or None
            item.observacoes = (request.form.get('observacoes') or request.form.get('especificacao_tecnica') or '').strip() or None

            item.estado_conservacao = request.form.get('estado_conservacao') or item.estado_conservacao
            item.situacao_uso = request.form.get('situacao_uso') or item.situacao_uso
            if request.form.get('status'):
                item.status = request.form.get('status')

            # Localização e Secretaria
            localizacao_nova = (request.form.get('localizacao') or '').strip()
            if localizacao_nova:
                item.localizacao = localizacao_nova
            local_id_raw = request.form.get('local_id')
            if local_id_raw and str(local_id_raw).isdigit():
                item.local_id = int(local_id_raw)

            sec_id = request.form.get('secretaria_id')
            item.secretaria_id = int(sec_id) if sec_id and str(sec_id).isdigit() else None

            # Responsável (campo digitado)
            item.responsavel_nome = (request.form.get('responsavel_nome') or '').strip() or None
            item.responsavel_cargo = (request.form.get('responsavel_cargo') or '').strip() or None
            item.responsavel_matricula = (request.form.get('responsavel_matricula') or '').strip() or None

            # Dados Financeiros e Nota Fiscal
            valor_raw = request.form.get('valor_compra') or request.form.get('valor_aquisicao')
            if valor_raw is not None:
                item.valor_aquisicao = _parse_moeda(valor_raw)

            item.forma_aquisicao = (request.form.get('forma_aquisicao') or item.forma_aquisicao or 'Compra / Licitação').strip()
            item.numero_nota_fiscal = (request.form.get('numero_nota_fiscal') or '').strip() or None
            item.serie_nota_fiscal = (request.form.get('serie_nota_fiscal') or '').strip() or None
            item.chave_nfe = re.sub(r'\s+', '', (request.form.get('chave_nfe') or '').strip()) or None
            item.data_nota_fiscal = _parse_data(request.form.get('data_nota_fiscal'))
            item.data_atesto = _parse_data(request.form.get('data_atesto'))
            data_aq = _parse_data(request.form.get('data_aquisicao')) or item.data_nota_fiscal or item.data_atesto
            if data_aq:
                item.data_aquisicao = data_aq

            item.fornecedor_nome = (request.form.get('fornecedor_nome') or '').strip() or None
            item.fornecedor_cnpj = (request.form.get('fornecedor_cnpj') or '').strip() or None
            item.numero_empenho = (request.form.get('numero_empenho') or '').strip() or None
            item.numero_processo = (request.form.get('numero_processo') or '').strip() or None
            item.modalidade_licitacao = (request.form.get('modalidade_licitacao') or '').strip() or None
            item.numero_contrato = (request.form.get('numero_contrato') or '').strip() or None
            item.fonte_recursos = (request.form.get('fonte_recursos') or '').strip() or None
            item.conta_contabil = (request.form.get('conta_contabil') or '').strip() or None

            vida_raw = request.form.get('vida_util_anos')
            if vida_raw and str(vida_raw).isdigit():
                item.vida_util_anos = int(vida_raw)
            item.garantia_ate = _parse_data(request.form.get('garantia_ate'))

            # Uploads opcionais na edição
            foto_file = request.files.get('foto_bem')
            if foto_file and foto_file.filename != '':
                item.foto_url = upload_arquivo_para_nuvem(foto_file, pasta="patrimonio")

            arquivo_nf_file = request.files.get('arquivo_nf')
            if arquivo_nf_file and arquivo_nf_file.filename != '':
                item.arquivo_nf_url = upload_arquivo_para_nuvem(arquivo_nf_file, pasta="patrimonio_nf")

            db.session.commit()

            registrar_log(f'Editou o item patrimonial: "{item.descricao}" ({item.numero_patrimonio or "S/N"}).')
            flash("Patrimônio atualizado com sucesso!", "success")
            return redirect(url_for('patrimonio.detalhes_item', item_id=item.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar o item: {str(e)}', 'danger')

    servidores = Servidor.query.order_by(Servidor.nome).all()
    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    fornecedores = Fornecedor.query.order_by(Fornecedor.nome).all()
    escolas = Escola.query.order_by(Escola.nome).all()
    locais_db, locais_legados = _obter_locais_disponiveis()

    return render_template(
        'patrimonio/form.html',
        item=item,
        patrimonio=item,
        servidores=servidores,
        secretarias=secretarias,
        fornecedores=fornecedores,
        escolas=escolas,
        locais_db=locais_db,
        locais_legados=locais_legados
    )


@patrimonio_bp.route('/item/detalhes/<int:item_id>')
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def detalhes_item(item_id):
    executar_migracao_patrimonio_segura()
    item = Patrimonio.query.get_or_404(item_id)
    servidores = Servidor.query.order_by(Servidor.nome).all()
    secretarias = Secretaria.query.order_by(Secretaria.nome).all()
    locais_db, locais_legados = _obter_locais_disponiveis()
    movimentacoes = MovimentacaoPatrimonio.query.filter_by(patrimonio_id=item_id).order_by(
        MovimentacaoPatrimonio.data_movimentacao.desc()
    ).all()

    # Busca outros bens vinculados à mesma Nota Fiscal para auditoria/controle interno
    itens_mesma_nf = []
    if item.nota_fiscal_id:
        itens_mesma_nf = Patrimonio.query.filter(
            Patrimonio.nota_fiscal_id == item.nota_fiscal_id,
            Patrimonio.id != item.id
        ).order_by(Patrimonio.numero_patrimonio).all()
    elif item.numero_nota_fiscal:
        itens_mesma_nf = Patrimonio.query.filter(
            Patrimonio.numero_nota_fiscal == item.numero_nota_fiscal,
            Patrimonio.id != item.id
        ).order_by(Patrimonio.numero_patrimonio).all()

    return render_template(
        'patrimonio/detalhes.html',
        item=item,
        servidores=servidores,
        secretarias=secretarias,
        locais_db=locais_db,
        locais_legados=locais_legados,
        movimentacoes=movimentacoes,
        itens_mesma_nf=itens_mesma_nf
    )


@patrimonio_bp.route('/item/transferir/<int:item_id>', methods=['POST'])
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def transferir_item(item_id):
    executar_migracao_patrimonio_segura()
    item = Patrimonio.query.get_or_404(item_id)

    local_origem = item.localizacao
    responsavel_anterior_cpf = item.servidor_responsavel_cpf
    responsavel_anterior_nome = item.nome_responsavel_atual

    novo_local = (request.form.get('local_destino') or '').strip()
    novo_responsavel_nome = (request.form.get('responsavel_novo_nome') or '').strip() or None
    novo_responsavel_cargo = (request.form.get('responsavel_novo_cargo') or '').strip() or None
    motivo_transferencia = (request.form.get('motivo_transferencia') or '').strip() or None
    nova_secretaria_id = request.form.get('secretaria_destino_id')

    if not novo_local:
        flash('O novo local é obrigatório para a transferência.', 'warning')
        return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))

    try:
        movimentacao = MovimentacaoPatrimonio(
            patrimonio_id=item.id,
            local_origem=local_origem,
            responsavel_anterior_cpf=responsavel_anterior_cpf,
            responsavel_anterior_nome=responsavel_anterior_nome,
            local_destino=novo_local,
            responsavel_novo_cpf=None,
            responsavel_novo_nome=novo_responsavel_nome,
            motivo_transferencia=motivo_transferencia,
            usuario_registro=session.get('username') or getattr(current_user, 'username', 'Sistema')
        )
        db.session.add(movimentacao)

        item.localizacao = novo_local
        loc_obj = LocalPatrimonio.query.filter(
            func.lower(LocalPatrimonio.nome) == novo_local.lower()
        ).first()
        item.local_id = loc_obj.id if loc_obj else None

        if novo_responsavel_nome is not None:
            item.responsavel_nome = novo_responsavel_nome
            item.servidor_responsavel_cpf = None
        if novo_responsavel_cargo:
            item.responsavel_cargo = novo_responsavel_cargo
        if nova_secretaria_id and str(nova_secretaria_id).isdigit():
            item.secretaria_id = int(nova_secretaria_id)

        db.session.commit()
        registrar_log(f'Transferiu o item "{item.descricao}" para "{novo_local}" (Resp: {novo_responsavel_nome or "N/A"}).')
        flash('Item transferido e Termo de Transferência/Guarda registrado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao transferir o item: {e}', 'danger')

    return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))


@patrimonio_bp.route('/transferir-lote', methods=['POST'])
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def transferir_itens_lote():
    """
    Transfere múltiplos bens patrimoniais de uma só vez (ex: enviar 50 de 100 mesas de um depósito para uma escola).
    Registra histórico individual em MovimentacaoPatrimonio e permite emitir Guia/Termo de Remessa em PDF.
    """
    executar_migracao_patrimonio_segura()

    ids_raw = request.form.getlist('patrimonio_ids[]') or request.form.getlist('patrimonio_ids')
    ids_selecionados = [int(i) for i in ids_raw if str(i).isdigit()]

    novo_local = (request.form.get('local_destino') or '').strip()
    novo_responsavel_nome = (request.form.get('responsavel_novo_nome') or '').strip() or None
    novo_responsavel_cargo = (request.form.get('responsavel_novo_cargo') or '').strip() or None
    motivo_transferencia = (request.form.get('motivo_transferencia') or '').strip() or None
    nova_secretaria_id_raw = request.form.get('secretaria_origem_id') or request.form.get('secretaria_destino_id')
    nova_secretaria_id = int(nova_secretaria_id_raw) if nova_secretaria_id_raw and str(nova_secretaria_id_raw).isdigit() else None
    acao_apos = (request.form.get('acao_apos') or 'salvar').strip()
    incluir_valores = (request.form.get('incluir_valores') or 'sim').strip()

    if not ids_selecionados:
        flash('Informe a quantidade de bens (ou selecione os itens) que deseja transferir.', 'warning')
        return redirect(url_for('patrimonio.listar_itens'))

    if not novo_local:
        flash('Informe ou selecione o Local de Destino para realizar a transferência em lote.', 'warning')
        return redirect(url_for('patrimonio.listar_itens'))

    try:
        bens_transferir = Patrimonio.query.filter(
            Patrimonio.id.in_(ids_selecionados),
            Patrimonio.status != 'Baixado'
        ).order_by(Patrimonio.descricao, Patrimonio.numero_patrimonio, Patrimonio.id).all()

        if not bens_transferir:
            flash('Nenhum bem patrimonial ativo foi encontrado para a seleção informada.', 'warning')
            return redirect(url_for('patrimonio.listar_itens'))

        # Verifica ou cria automaticamente o LocalPatrimonio de destino para manter o cadastro padronizado
        loc_obj = LocalPatrimonio.query.filter(
            func.lower(LocalPatrimonio.nome) == novo_local.lower()
        ).first()
        if not loc_obj:
            loc_obj = LocalPatrimonio(
                nome=novo_local,
                responsavel_setor=novo_responsavel_nome,
                secretaria_id=nova_secretaria_id,
                ativo=True
            )
            db.session.add(loc_obj)
            db.session.flush()
        else:
            if novo_responsavel_nome and not loc_obj.responsavel_setor:
                loc_obj.responsavel_setor = novo_responsavel_nome
            if nova_secretaria_id and not loc_obj.secretaria_id:
                loc_obj.secretaria_id = nova_secretaria_id

        usuario_atual = session.get('username') or getattr(current_user, 'username', 'Sistema')
        locais_origem_set = set()
        ids_efetivados = []

        for item in bens_transferir:
            local_origem_item = item.localizacao or 'Não informada'
            locais_origem_set.add(local_origem_item)

            mov = MovimentacaoPatrimonio(
                patrimonio_id=item.id,
                local_origem=local_origem_item,
                responsavel_anterior_cpf=item.servidor_responsavel_cpf,
                responsavel_anterior_nome=item.nome_responsavel_atual,
                local_destino=novo_local,
                responsavel_novo_cpf=None,
                responsavel_novo_nome=novo_responsavel_nome,
                motivo_transferencia=motivo_transferencia or f'Transferência em lote ({len(bens_transferir)} unid.) para {novo_local}',
                usuario_registro=usuario_atual
            )
            db.session.add(mov)

            item.localizacao = novo_local
            item.local_id = loc_obj.id if loc_obj else None
            if novo_responsavel_nome is not None:
                item.responsavel_nome = novo_responsavel_nome
                item.servidor_responsavel_cpf = None
            if novo_responsavel_cargo:
                item.responsavel_cargo = novo_responsavel_cargo
            if nova_secretaria_id:
                item.secretaria_id = nova_secretaria_id
            elif loc_obj and loc_obj.secretaria_id and not item.secretaria_id:
                item.secretaria_id = loc_obj.secretaria_id

            ids_efetivados.append(item.id)

        db.session.commit()

        protocolo_remessa = f"REM-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        origem_resumo = ", ".join(sorted(locais_origem_set))

        session['ultima_remessa_patrimonio'] = {
            'protocolo': protocolo_remessa,
            'ids': ids_efetivados,
            'qtd': len(ids_efetivados),
            'local_origem': origem_resumo,
            'local_destino': novo_local,
            'responsavel_novo_nome': novo_responsavel_nome or '',
            'responsavel_novo_cargo': novo_responsavel_cargo or '',
            'motivo_transferencia': motivo_transferencia or '',
            'incluir_valores': incluir_valores,
            'data_hora': obter_data_hora_br_str(),
        }

        registrar_log(
            f'Transferência em Lote ({protocolo_remessa}): {len(ids_efetivados)} bem(ns) de "{origem_resumo}" '
            f'para "{novo_local}" (Resp: {novo_responsavel_nome or "N/A"}).'
        )

        flash(
            f'Transferência em lote concluída com sucesso! {len(ids_efetivados)} bem(ns) foram enviados para "{novo_local}".',
            'success'
        )

        if acao_apos == 'pdf':
            return redirect(url_for('patrimonio.gerar_termo_remessa_lote_pdf', incluir_valores=incluir_valores))

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao realizar transferência em lote: {str(e)}', 'danger')

    return redirect(url_for('patrimonio.listar_itens'))


@patrimonio_bp.route('/termo-remessa-lote/pdf')
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def gerar_termo_remessa_lote_pdf():
    """
    Gera a Guia de Remessa e Termo de Transferência/Guarda Coletiva em PDF (Padrão Governo)
    para os bens transferidos em lote.
    """
    executar_migracao_patrimonio_segura()

    dados_remessa = session.get('ultima_remessa_patrimonio') or {}
    ids_query = request.args.get('ids', '').strip()
    if ids_query:
        ids_lista = [int(x) for x in ids_query.split(',') if x.strip().isdigit()]
    else:
        ids_lista = dados_remessa.get('ids') or []

    if not ids_lista:
        flash('Nenhuma remessa em lote recente encontrada para gerar o PDF.', 'warning')
        return redirect(url_for('patrimonio.listar_itens'))

    bens = Patrimonio.query.filter(Patrimonio.id.in_(ids_lista)).order_by(
        Patrimonio.descricao, Patrimonio.numero_patrimonio, Patrimonio.id
    ).all()

    if not bens:
        flash('Os bens desta remessa não foram encontrados.', 'warning')
        return redirect(url_for('patrimonio.listar_itens'))

    local_origem = request.args.get('origem') or dados_remessa.get('local_origem') or 'Depósito / Origem'
    local_destino = request.args.get('destino') or dados_remessa.get('local_destino') or (bens[0].localizacao if bens else 'Destino')
    responsavel_destino = request.args.get('resp') or dados_remessa.get('responsavel_novo_nome') or (bens[0].nome_responsavel_atual if bens else '')
    cargo_destino = request.args.get('cargo') or dados_remessa.get('responsavel_novo_cargo') or (bens[0].responsavel_cargo if bens else '')
    motivo_remessa = request.args.get('motivo') or dados_remessa.get('motivo_transferencia') or 'Distribuição / Transferência de Bens Patrimoniais'
    protocolo = dados_remessa.get('protocolo') or f"REM-{datetime.now().strftime('%Y%m%d-%H%M')}"
    data_emissao_str = dados_remessa.get('data_hora') or obter_data_hora_br_str()
    usuario_emissor = session.get('username') or getattr(current_user, 'username', 'Controle Interno')

    secretaria_nome = "PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ"
    for b in bens:
        if b.secretaria and b.secretaria.nome:
            secretaria_nome = b.secretaria.nome.upper()
            break

    buffer = BytesIO()
    page_width, page_height = landscape(A4)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=3.4 * cm,
        bottomMargin=1.8 * cm,
        title=f"Guia de Remessa Patrimonial - {local_destino}"
    )

    styles = getSampleStyleSheet()
    cor_gov_azul = colors.HexColor("#0f2942")
    cor_cinza_claro = colors.HexColor("#f1f5f9")
    cor_borda = colors.HexColor("#cbd5e1")

    style_titulo_doc = ParagraphStyle(
        'TituloRemessaGov',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=15,
        textColor=cor_gov_azul,
        alignment=TA_CENTER,
        spaceAfter=2
    )
    style_subtitulo_doc = ParagraphStyle(
        'SubtituloRemessaGov',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#475569"),
        alignment=TA_CENTER,
        spaceAfter=10
    )
    style_celula = ParagraphStyle(
        'CelulaRemessa',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#1e293b")
    )
    style_celula_bold = ParagraphStyle(
        'CelulaRemessaBold',
        parent=style_celula,
        fontName='Helvetica-Bold'
    )
    style_celula_center = ParagraphStyle(
        'CelulaRemessaCenter',
        parent=style_celula,
        alignment=TA_CENTER
    )
    style_celula_right = ParagraphStyle(
        'CelulaRemessaRight',
        parent=style_celula,
        alignment=TA_RIGHT
    )
    style_header_tabela = ParagraphStyle(
        'HeaderRemessa',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9.5,
        textColor=colors.white,
        alignment=TA_CENTER
    )
    style_termo = ParagraphStyle(
        'TermoRemessaJuridico',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11.5,
        alignment=TA_JUSTIFY,
        textColor=colors.HexColor("#1e293b")
    )

    url_verificacao = url_for('patrimonio.listar_itens', _external=True)
    qr = qrcode.make(f"PREFEITURA DE VALENCA DO PIAUI | {protocolo} | DESTINO: {local_destino} | QTD: {len(bens)} | {url_verificacao}")
    qr_buffer = BytesIO()
    qr.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)
    qr_reader = ImageReader(qr_buffer)

    def desenhar_cabecalho_rodape_remessa(canvas, doc_obj):
        canvas.saveState()
        basedir = current_app.root_path
        logo_path = os.path.join(basedir, 'static', 'logo.png')
        timbre_path = os.path.join(basedir, 'static', 'timbre.jpg')

        canvas.setFillColor(cor_gov_azul)
        canvas.rect(1.2 * cm, page_height - 1.0 * cm, page_width - 2.4 * cm, 0.15 * cm, fill=1, stroke=0)

        if os.path.exists(logo_path):
            try:
                canvas.drawImage(logo_path, 1.3 * cm, page_height - 3.0 * cm, width=1.8 * cm, height=1.8 * cm, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        elif os.path.exists(timbre_path):
            try:
                canvas.drawImage(timbre_path, 1.3 * cm, page_height - 2.9 * cm, width=3.5 * cm, height=1.6 * cm, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

        canvas.setFillColor(cor_gov_azul)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawCentredString(page_width / 2.0, page_height - 1.55 * cm, "ESTADO DO PIAUÍ — PREFEITURA MUNICIPAL DE VALENÇA DO PIAUÍ")

        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawCentredString(page_width / 2.0, page_height - 1.95 * cm, secretaria_nome)

        canvas.setFillColor(colors.HexColor("#334155"))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawCentredString(
            page_width / 2.0,
            page_height - 2.35 * cm,
            "CONTROLADORIA GERAL DO MUNICÍPIO • COORDENADORIA DE CONTROLE PATRIMONIAL E ALMOXARIFADO"
        )
        canvas.drawCentredString(
            page_width / 2.0,
            page_height - 2.70 * cm,
            "CNPJ: 06.095.146/0001-44 • Rua Epaminondas Nogueira, nº 1425, Centro - Valença do Piauí/PI"
        )

        try:
            canvas.drawImage(qr_reader, page_width - 3.1 * cm, page_height - 3.0 * cm, width=1.8 * cm, height=1.8 * cm)
        except Exception:
            pass

        canvas.setStrokeColor(colors.HexColor("#198754"))
        canvas.setLineWidth(1.5)
        canvas.line(1.2 * cm, page_height - 3.1 * cm, page_width - 1.2 * cm, page_height - 3.1 * cm)

        canvas.setStrokeColor(cor_borda)
        canvas.setLineWidth(0.7)
        canvas.line(1.2 * cm, 1.35 * cm, page_width - 1.2 * cm, 1.35 * cm)

        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(
            1.2 * cm,
            0.95 * cm,
            f"Gestor 360º - Guia Oficial de Transferência em Lote | Protocolo: {protocolo} | Emitido por: {usuario_emissor} em {data_emissao_str}"
        )
        canvas.drawRightString(
            page_width - 1.2 * cm,
            0.95 * cm,
            f"Página {doc_obj.page}"
        )
        canvas.restoreState()

    elementos = []
    elementos.append(Paragraph("GUIA DE REMESSA E TERMO DE TRANSFERÊNCIA DE BENS PATRIMONIAIS EM LOTE", style_titulo_doc))
    elementos.append(Paragraph("Documento oficial de movimentação, distribuição física e transferência de responsabilidade patrimonial", style_subtitulo_doc))

    soma_total = sum((b.valor_aquisicao or 0.0) for b in bens)
    soma_total_fmt = f"R$ {soma_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    incluir_valores_param = (request.args.get('incluir_valores') or dados_remessa.get('incluir_valores') or 'sim').strip().lower()
    mostrar_valores = (incluir_valores_param != 'nao')

    dados_quadro_remessa = [
        [
            Paragraph("<b>LOCAL DE ORIGEM (SAÍDA):</b>", style_celula),
            Paragraph(f"{local_origem}", style_celula),
            Paragraph("<b>PROTOCOLO / DATA:</b>", style_celula),
            Paragraph(f"<b>{protocolo}</b> ({data_emissao_str})", style_celula),
        ],
        [
            Paragraph("<b>LOCAL DE DESTINO (RECEBEDOR):</b>", style_celula),
            Paragraph(f"<b>{local_destino}</b>", style_celula_bold),
            Paragraph("<b>QUANTIDADE TRANSFERIDA:</b>", style_celula),
            Paragraph(f"<b>{len(bens)} unidade(s) patrimonial(is)</b>", style_celula_bold),
        ],
        [
            Paragraph("<b>RESPONSÁVEL PELO RECEBIMENTO:</b>", style_celula),
            Paragraph(
                f"{responsavel_destino or 'Não informado'}"
                f"{' (' + cargo_destino + ')' if cargo_destino else ''}",
                style_celula
            ),
            Paragraph("<b>VALOR CONTÁBIL DA REMESSA:</b>" if mostrar_valores else "<b>TIPO DE GUIA:</b>", style_celula),
            Paragraph(f"<b>{soma_total_fmt}</b>" if mostrar_valores else "<b>Conferência Física de Bens</b>", style_celula_bold),
        ],
        [
            Paragraph("<b>MOTIVO / OBSERVAÇÃO DA REMESSA:</b>", style_celula),
            Paragraph(f"{motivo_remessa}", style_celula),
            Paragraph("<b>ÓRGÃO / SECRETARIA:</b>", style_celula),
            Paragraph(f"{secretaria_nome}", style_celula),
        ],
    ]

    tabela_meta = Table(dados_quadro_remessa, colWidths=[5.2 * cm, 11.3 * cm, 5.0 * cm, 5.8 * cm])
    tabela_meta.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), cor_cinza_claro),
        ('BACKGROUND', (2, 0), (2, -1), cor_cinza_claro),
        ('GRID', (0, 0), (-1, -1), 0.5, cor_borda),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elementos.append(tabela_meta)
    elementos.append(Spacer(1, 0.35 * cm))

    if mostrar_valores:
        cabecalho_tabela = [
            Paragraph("ITEM", style_header_tabela),
            Paragraph("Nº TOMBAMENTO", style_header_tabela),
            Paragraph("DESCRIÇÃO DO BEM PATRIMONIAL", style_header_tabela),
            Paragraph("MARCA / MODELO", style_header_tabela),
            Paragraph("NOTA FISCAL / FORNECEDOR", style_header_tabela),
            Paragraph("ESTADO DO BEM", style_header_tabela),
            Paragraph("VALOR UNIT. (R$)", style_header_tabela),
            Paragraph("CONF.", style_header_tabela),
        ]
    else:
        cabecalho_tabela = [
            Paragraph("ITEM", style_header_tabela),
            Paragraph("Nº TOMBAMENTO", style_header_tabela),
            Paragraph("DESCRIÇÃO DO BEM PATRIMONIAL", style_header_tabela),
            Paragraph("MARCA / MODELO", style_header_tabela),
            Paragraph("NOTA FISCAL / FORNECEDOR", style_header_tabela),
            Paragraph("ESTADO DO BEM", style_header_tabela),
            Paragraph("CONF.", style_header_tabela),
        ]

    linhas_tabela = [cabecalho_tabela]

    for idx, b in enumerate(bens, start=1):
        marca_mod = " / ".join([x for x in [b.marca, b.modelo] if x]) or "-"
        nf_forn = f"NF: {b.numero_nota_fiscal or 'S/N'}"
        if b.fornecedor_nome:
            nf_forn += f"<br/><font size=6.5 color='#475569'>{b.fornecedor_nome[:35]}</font>"
        val_fmt = f"{(b.valor_aquisicao or 0.0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        if mostrar_valores:
            linhas_tabela.append([
                Paragraph(f"{idx:02d}", style_celula_center),
                Paragraph(f"<b>{b.numero_patrimonio or 'S/N'}</b>", style_celula_center),
                Paragraph(f"<b>{b.descricao}</b>", style_celula),
                Paragraph(marca_mod, style_celula),
                Paragraph(nf_forn, style_celula),
                Paragraph(f"<b>{(b.estado_conservacao or 'Bom').upper()}</b>", style_celula_center),
                Paragraph(val_fmt, style_celula_right),
                Paragraph("[   ]", style_celula_center),
            ])
        else:
            linhas_tabela.append([
                Paragraph(f"{idx:02d}", style_celula_center),
                Paragraph(f"<b>{b.numero_patrimonio or 'S/N'}</b>", style_celula_center),
                Paragraph(f"<b>{b.descricao}</b>", style_celula),
                Paragraph(marca_mod, style_celula),
                Paragraph(nf_forn, style_celula),
                Paragraph(f"<b>{(b.estado_conservacao or 'Bom').upper()}</b>", style_celula_center),
                Paragraph("[   ]", style_celula_center),
            ])

    if mostrar_valores:
        linhas_tabela.append([
            Paragraph("", style_celula),
            Paragraph("", style_celula),
            Paragraph(f"<b>TOTAL DE BENS TRANSFERIDOS NESTA REMESSA: {len(bens)} UNIDADE(S)</b>", style_celula_bold),
            Paragraph("", style_celula),
            Paragraph("", style_celula),
            Paragraph("<b>TOTAL:</b>", style_celula_center),
            Paragraph(f"<b>{soma_total_fmt}</b>", style_celula_right),
            Paragraph("", style_celula),
        ])
        larguras_colunas = [
            1.2 * cm,  # ITEM
            2.8 * cm,  # TOMBAMENTO
            8.2 * cm,  # DESCRIÇÃO
            3.8 * cm,  # MARCA/MODELO
            5.2 * cm,  # NF/FORNECEDOR
            2.5 * cm,  # ESTADO
            2.4 * cm,  # VALOR
            1.2 * cm,  # CONF.
        ]
        span_final = ('SPAN', (2, -1), (4, -1))
    else:
        linhas_tabela.append([
            Paragraph("", style_celula),
            Paragraph("", style_celula),
            Paragraph(f"<b>TOTAL DE BENS TRANSFERIDOS NESTA REMESSA: {len(bens)} UNIDADE(S)</b>", style_celula_bold),
            Paragraph("", style_celula),
            Paragraph("", style_celula),
            Paragraph("", style_celula),
            Paragraph("", style_celula),
        ])
        larguras_colunas = [
            1.2 * cm,  # ITEM
            3.0 * cm,  # TOMBAMENTO
            9.6 * cm,  # DESCRIÇÃO
            4.4 * cm,  # MARCA/MODELO
            5.4 * cm,  # NF/FORNECEDOR
            2.5 * cm,  # ESTADO
            1.2 * cm,  # CONF.
        ]
        span_final = ('SPAN', (2, -1), (5, -1))

    tabela_bens = Table(linhas_tabela, colWidths=larguras_colunas, repeatRows=1)
    estilo_tabela_bens = [
        ('BACKGROUND', (0, 0), (-1, 0), cor_gov_azul),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.45, cor_borda),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        span_final,
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
    ]
    for row_idx in range(1, len(linhas_tabela) - 1):
        if row_idx % 2 == 0:
            estilo_tabela_bens.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor("#f8fafc")))

    tabela_bens.setStyle(TableStyle(estilo_tabela_bens))
    elementos.append(tabela_bens)
    elementos.append(Spacer(1, 0.35 * cm))

    texto_declaracao = (
        "<b>DECLARAÇÃO DE RECEBIMENTO E ASSUNÇÃO DE GUARDA PATRIMONIAL:</b> "
        f"Declaro que recebi da Coordenadoria de Controle Patrimonial os <b>{len(bens)} bem(ns) permanente(s)</b> acima relacionados, "
        f"oriundos de <b>{local_origem}</b> e destinados à unidade/local <b>{local_destino}</b>, conferidos fisicamente quanto ao seu número de tombamento "
        "e estado de conservação, assumindo a responsabilidade por sua guarda, zelo e uso exclusivo no interesse do serviço público municipal."
    )

    nome_resp_assinatura = responsavel_destino or "___________________________________________________"
    sub_resp_assinatura = cargo_destino or f"Responsável pelo Recebimento ({local_destino})"

    bloco_assinaturas = Table(
        [
            [
                Paragraph("___________________________________________________", style_celula_center),
                Paragraph("___________________________________________________", style_celula_center),
                Paragraph("___________________________________________________", style_celula_center),
            ],
            [
                Paragraph(f"<b>{usuario_emissor.upper()}</b><br/><font size=7 color='#475569'>Entregador / Setor de Patrimônio (Origem)</font>", style_celula_center),
                Paragraph(f"<b>{nome_resp_assinatura}</b><br/><font size=7 color='#475569'>{sub_resp_assinatura}</font>", style_celula_center),
                Paragraph("<b>CONTROLADORIA GERAL / VISTO</b><br/><font size=7 color='#475569'>Controle Interno Municipal</font>", style_celula_center),
            ],
        ],
        colWidths=[9.1 * cm, 9.1 * cm, 9.1 * cm]
    )
    bloco_assinaturas.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))

    elementos.append(KeepTogether([
        Paragraph(texto_declaracao, style_termo),
        Spacer(1, 0.85 * cm),
        bloco_assinaturas,
    ]))

    doc.build(elementos, onFirstPage=desenhar_cabecalho_rodape_remessa, onLaterPages=desenhar_cabecalho_rodape_remessa)
    buffer.seek(0)

    nome_limpo = re.sub(r'[^a-zA-Z0-9_-]', '_', local_destino)[:35]
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f"Guia_Remessa_Patrimonio_{nome_limpo}_{datetime.now().strftime('%Y%m%d')}.pdf"
    )


@patrimonio_bp.route('/item/<int:item_id>/termo-baixa')
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def termo_baixa(item_id):
    item = Patrimonio.query.get_or_404(item_id)
    return render_template('patrimonio/termo_baixa.html', item=item, usuario_logado=current_user)


@patrimonio_bp.route('/termos_responsabilidade')
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def listar_termos_responsabilidade():
    return render_template('patrimonio/termos_responsabilidade.html', termos=[])


@patrimonio_bp.route('/<int:id>/etiqueta')
@login_required
@role_required('Patrimonio', 'admin', 'RH', 'Fiscal')
def gerar_etiqueta_qr(id):
    bem = Patrimonio.query.get_or_404(id)

    try:
        buffer = BytesIO()
        p = canvas_lib.Canvas(buffer, pagesize=(200, 100))

        link_consulta = url_for('patrimonio.detalhes_item', item_id=bem.id, _external=True)
        qr = qrcode.make(link_consulta)
        qr_buffer = BytesIO()
        qr.save(qr_buffer, format='PNG')
        qr_buffer.seek(0)

        p.setFont("Helvetica-Bold", 8)
        p.drawString(10, 85, "PREFEITURA DE VALENÇA DO PIAUÍ")

        p.setFont("Helvetica", 7)
        p.drawString(10, 75, f"Bem: {bem.descricao[:30]}")

        p.setFont("Helvetica-Bold", 9)
        p.drawString(10, 60, f"PATRIMÔNIO: {bem.numero_patrimonio or 'S/N'}")

        if bem.numero_nota_fiscal:
            p.setFont("Helvetica", 6)
            p.drawString(10, 48, f"NF: {bem.numero_nota_fiscal} | {bem.localizacao[:22]}")

        qr_img = ImageReader(qr_buffer)
        p.drawImage(qr_img, 130, 10, width=60, height=60)

        p.setFont("Helvetica-Oblique", 6)
        p.drawString(10, 10, "Controle Interno - Escaneie o QR")

        p.showPage()
        p.save()

        buffer.seek(0)

        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=False,
            download_name=f'etiqueta_{bem.numero_patrimonio or bem.id}.pdf'
        )

    except Exception as e:
        flash(f"Erro ao gerar etiqueta: {str(e)}", "danger")
        return redirect(url_for('patrimonio.listar_itens'))


@patrimonio_bp.route('/movimentacao/<int:mov_id>/termo')
@login_required
def gerar_termo_recebimento(mov_id):
    mov = MovimentacaoPatrimonio.query.get_or_404(mov_id)
    bem = mov.patrimonio

    buffer = BytesIO()
    p = canvas_lib.Canvas(buffer, pagesize=A4)

    p.setFont("Helvetica-Bold", 14)
    p.drawCentredString(300, 800, "TERMO DE RESPONSABILIDADE E RECEBIMENTO")
    p.setFont("Helvetica", 10)
    p.drawCentredString(300, 785, "PREFEITURA DE VALENÇA DO PIAUÍ")

    p.setFont("Helvetica", 12)
    p.drawString(50, 730, "DADOS DO BEM:")
    p.setFont("Helvetica", 10)
    p.drawString(70, 710, f"Descrição: {bem.descricao}")
    p.drawString(70, 695, f"Tombamento: {bem.numero_patrimonio or 'Pendente'}")
    p.drawString(70, 680, f"Origem: {mov.local_origem} ({mov.nome_origem_exibicao})")
    p.drawString(70, 665, f"Destino: {mov.local_destino} ({mov.nome_destino_exibicao})")

    p.line(50, 500, 250, 500)
    p.drawString(80, 485, "Quem Entregou")

    p.line(350, 500, 550, 500)
    p.drawString(380, 485, f"Quem Recebeu ({mov.nome_destino_exibicao})")

    p.showPage()
    p.save()
    buffer.seek(0)
    return send_file(buffer, mimetype='application/pdf', download_name=f'termo_mov_{mov.id}.pdf')


@patrimonio_bp.route('/movimentacao/<int:mov_id>/imprimir')
@login_required
def imprimir_termo_transferencia(mov_id):
    movimentacao = MovimentacaoPatrimonio.query.get_or_404(mov_id)
    return render_template('patrimonio/termo_recebimento.html', mov=movimentacao)


@patrimonio_bp.route('/item/baixa/<int:item_id>', methods=['POST'])
@login_required
@role_required('admin', 'Patrimonio', 'Fiscal')
def dar_baixa_item(item_id):
    item = Patrimonio.query.get_or_404(item_id)

    motivo_baixa = request.form.get('motivo_baixa')
    recebedor = request.form.get('recebedor', 'Não informado')
    imprimir_termo = request.form.get('imprimir_termo')

    if not motivo_baixa:
        flash("A justificativa é obrigatória para dar baixa.", "warning")
        return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))

    try:
        item.status = "Baixado"
        item.situacao_uso = "Inservível / Baixado"

        data_atual = datetime.now().strftime('%d/%m/%Y')
        texto_baixa = f"BAIXA REALIZADA EM {data_atual} - Motivo: {motivo_baixa} | Recebedor: {recebedor}"

        if item.observacoes:
            item.observacoes = f"{item.observacoes}\n\n{texto_baixa}"
        else:
            item.observacoes = texto_baixa

        db.session.commit()
        registrar_log(f"Baixa efetuada - Item: {item.descricao} (Pat: {item.numero_patrimonio or 'S/N'}) - Recebedor: {recebedor}")

        flash("Baixa do patrimônio realizada com sucesso!", "success")

        if imprimir_termo == 'sim':
            return redirect(url_for('patrimonio.termo_baixa', item_id=item.id))
        else:
            return redirect(url_for('patrimonio.detalhes_item', item_id=item.id))

    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao processar a baixa: {str(e)}", "danger")
        return redirect(url_for('patrimonio.detalhes_item', item_id=item_id))


@patrimonio_bp.route('/item/excluir/<int:item_id>', methods=['POST'])
@login_required
@role_required('admin')
def excluir_item_patrimonio(item_id):
    item = Patrimonio.query.get_or_404(item_id)
    justificativa = request.form.get('justificativa_exclusao')

    try:
        descricao_log = f"EXCLUSÃO DEFINITIVA - Item: {item.descricao} (Tomb: {item.numero_patrimonio}) - Motivo: {justificativa}"
        db.session.delete(item)
        db.session.commit()

        registrar_log(descricao_log)
        flash("Item excluído permanentemente do sistema.", "warning")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir: {e}", "danger")

    return redirect(url_for('patrimonio.listar_itens'))


@patrimonio_bp.route('/itens-baixados')
@login_required
@role_required('admin', 'Patrimonio', 'Fiscal')
def listar_itens_baixados():
    executar_migracao_patrimonio_segura()
    itens = Patrimonio.query.filter_by(status='Baixado').order_by(Patrimonio.descricao).all()
    return render_template('patrimonio/itens_baixados.html', itens=itens)
