# almoxarifado_routes.py
# -*- coding: utf-8 -*-
"""
Módulo Completo de Almoxarifado — Gestão Pública Municipal (SEME)
Compatível com Railway (Gunicorn) + Supabase (PostgreSQL e Storage) e SQLite local.
Conformidade: NBC TSP 04 (Custo Médio Ponderado), MCASP/PCASP (3.3.90.30) e Controle Interno/TCE.
"""
import base64
import csv
import io
import json
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

import qrcode
from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import and_, case, extract, func, or_
from sqlalchemy.orm import joinedload

from almoxarifado_services import (
    calcular_custo_medio_ponderado,
    executar_migracao_almoxarifado_segura,
    formatar_moeda_br,
    formatar_qtd,
    gerar_codigo_autenticacao,
    gerar_codigo_sequencial,
    gerar_excel_almoxarifado,
    gerar_pdf_ata_inventario,
    gerar_pdf_comprovante_entrada,
    gerar_pdf_guia_entrega,
    gerar_pdf_relatorio_tabela,
    parse_float_br,
    parse_xml_nfe,
    verificar_bloqueio_inventario,
)
from extensions import db
from models import (
    AcadMatricula,
    AcadTurma,
    AlmoxCategoria,
    AlmoxEntrega,
    AlmoxEntregaItem,
    AlmoxEntrada,
    AlmoxEntradaItem,
    AlmoxInventario,
    AlmoxInventarioItem,
    AlmoxLote,
    AlmoxSetor,
    Escola,
    FiscalContrato,
    Fornecedor,
    Material,
    MovimentoEstoque,
    Requisicao,
    RequisicaoItem,
    Secretaria,
    Servidor,
    User,
)
from utils import (
    login_required,
    registrar_log,
    role_required,
    upload_arquivo_para_nuvem,
)

almoxarifado_bp = Blueprint("almoxarifado", __name__, url_prefix="/almoxarifado")


@almoxarifado_bp.before_request
def _garantir_schema_almoxarifado():
    executar_migracao_almoxarifado_segura()


# =============================================================================
# HELPERS INTERNOS DE ESTOQUE (TRANSAÇÕES SEGURAS PARA POSTGRESQL / RAILWAY)
# =============================================================================

def _obter_usuario_id_atual():
    uid = session.get("user_id")
    if uid:
        return int(uid)
    username = session.get("username")
    if username:
        u = User.query.filter_by(username=username).first()
        if u:
            return u.id
    u_first = User.query.first()
    return u_first.id if u_first else 1


def _lock_material(material_id):
    """Obtém o material com bloqueio de linha (FOR UPDATE) no PostgreSQL para evitar corrida de estoque."""
    q = Material.query.filter_by(id=material_id)
    if "postgres" in str(db.engine.url).lower():
        q = q.with_for_update()
    return q.first()


def _parse_date_opt(val):
    if not val:
        return None
    try:
        return datetime.strptime(val.strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _baixar_lotes_fefo(material_id, quantidade, lote_id_forcado=None):
    """Deduz quantidade dos lotes disponíveis em ordem FEFO (menor validade primeiro)."""
    restante = float(quantidade)
    lote_principal_id = lote_id_forcado

    if lote_id_forcado:
        lote = AlmoxLote.query.filter_by(id=lote_id_forcado, material_id=material_id).first()
        if lote and lote.quantidade_atual > 0:
            deduzir = min(lote.quantidade_atual, restante)
            lote.quantidade_atual = round(lote.quantidade_atual - deduzir, 4)
            restante = round(restante - deduzir, 4)

    if restante > 0:
        lotes = (
            AlmoxLote.query.filter(
                AlmoxLote.material_id == material_id,
                AlmoxLote.quantidade_atual > 0,
            )
            .order_by(
                case((AlmoxLote.data_validade.is_(None), 1), else_=0),
                AlmoxLote.data_validade.asc(),
                AlmoxLote.id.asc(),
            )
            .all()
        )
        for l in lotes:
            if restante <= 0:
                break
            if lote_principal_id is None:
                lote_principal_id = l.id
            deduzir = min(l.quantidade_atual, restante)
            l.quantidade_atual = round(l.quantidade_atual - deduzir, 4)
            restante = round(restante - deduzir, 4)

    return lote_principal_id


def _registrar_movimento_entrada(
    material,
    quantidade_base,
    valor_unitario_base,
    tipo_movimento,
    usuario_id,
    documento_ref=None,
    nota_fiscal=None,
    justificativa=None,
    fornecedor_id=None,
    entrada_id=None,
    entrega_id=None,
    lote_id=None,
    escola_id=None,
    setor_id=None,
):
    saldo_ant = float(material.estoque_atual or 0.0)
    cm_ant = material.custo_referencia
    novo_cm = calcular_custo_medio_ponderado(saldo_ant, cm_ant, quantidade_base, valor_unitario_base)
    novo_saldo = round(saldo_ant + float(quantidade_base), 4)

    material.estoque_atual = novo_saldo
    material.custo_medio = novo_cm
    if valor_unitario_base and valor_unitario_base > 0:
        material.ultimo_custo = float(valor_unitario_base)

    mov = MovimentoEstoque(
        material_id=material.id,
        tipo_movimento=tipo_movimento,
        quantidade=float(quantidade_base),
        valor_unitario=float(valor_unitario_base or novo_cm),
        valor_total=round(float(quantidade_base) * float(valor_unitario_base or novo_cm), 2),
        saldo_anterior=saldo_ant,
        saldo_apos=novo_saldo,
        custo_medio_apos=novo_cm,
        nota_fiscal=nota_fiscal,
        documento_ref=documento_ref,
        justificativa=justificativa,
        usuario_id=usuario_id,
        fornecedor_id=fornecedor_id,
        entrada_id=entrada_id,
        entrega_id=entrega_id,
        lote_id=lote_id,
        escola_id=escola_id,
        setor_id=setor_id,
    )
    db.session.add(mov)
    return mov


def _registrar_movimento_saida(
    material,
    quantidade_base,
    tipo_movimento,
    usuario_id,
    documento_ref=None,
    justificativa=None,
    entrega_id=None,
    requisicao_item_id=None,
    lote_id=None,
    escola_id=None,
    setor_id=None,
):
    saldo_ant = float(material.estoque_atual or 0.0)
    if quantidade_base <= 0:
        raise ValueError(f'A quantidade de saída para "{material.descricao}" deve ser maior que zero.')
    if quantidade_base > saldo_ant + 1e-6:
        raise ValueError(
            f'Estoque insuficiente para "{material.descricao}" '
            f'(disponível: {formatar_qtd(saldo_ant)} {material.unidade_medida}, solicitado: {formatar_qtd(quantidade_base)}).'
        )

    cm = material.custo_referencia
    novo_saldo = round(max(0.0, saldo_ant - float(quantidade_base)), 4)
    material.estoque_atual = novo_saldo
    valor_tot = round(float(quantidade_base) * cm, 2)

    mov = MovimentoEstoque(
        material_id=material.id,
        tipo_movimento=tipo_movimento,
        quantidade=-float(quantidade_base),
        valor_unitario=cm,
        valor_total=valor_tot,
        saldo_anterior=saldo_ant,
        saldo_apos=novo_saldo,
        custo_medio_apos=cm,
        documento_ref=documento_ref,
        justificativa=justificativa,
        usuario_id=usuario_id,
        entrega_id=entrega_id,
        requisicao_item_id=requisicao_item_id,
        lote_id=lote_id,
        escola_id=escola_id,
        setor_id=setor_id,
    )
    db.session.add(mov)
    return mov, cm, valor_tot


def _resolver_destino(tipo_destino, escola_id, setor_id, outro_texto=None):
    """Valida e retorna (tipo_destino, escola_id, setor_id, nome_snapshot)."""
    if tipo_destino == "Escola":
        if not escola_id:
            raise ValueError("Selecione a Escola Municipal de destino.")
        escola = Escola.query.get(escola_id)
        if not escola:
            raise ValueError("Escola selecionada não encontrada.")
        return "Escola", escola.id, None, escola.nome
    elif tipo_destino == "Setor SEME":
        if not setor_id:
            raise ValueError("Selecione o Setor da SEME de destino.")
        setor = AlmoxSetor.query.get(setor_id)
        if not setor:
            raise ValueError("Setor da SEME selecionado não encontrado.")
        return "Setor SEME", None, setor.id, setor.nome
    elif tipo_destino == "Baixa Interna":
        return "Baixa Interna", None, None, (outro_texto or "Baixa Técnica / Almoxarifado SEME")
    else:
        desc = (outro_texto or "").strip()
        if not desc:
            raise ValueError("Informe a descrição do destino.")
        return "Outro", None, None, desc


# =============================================================================
# 1. DASHBOARD GERENCIAL (KPIs AGREGADOS NO POSTGRESQL + GRÁFICOS CHART.JS)
# =============================================================================

@almoxarifado_bp.route("/")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def dashboard():
    hoje = date.today()
    inicio_mes = hoje.replace(day=1)
    limite_30d = hoje + timedelta(days=30)

    materiais_ativos = (
        Material.query.options(joinedload(Material.categoria_rel))
        .filter(or_(Material.ativo.is_(True), Material.ativo.is_(None)))
        .order_by(Material.descricao)
        .all()
    )

    total_itens = len(materiais_ativos)
    valor_total_estoque = round(sum(m.valor_estoque_atual for m in materiais_ativos), 2)
    materiais_baixo_estoque = [
        m for m in materiais_ativos if (m.estoque_minimo or 0) > 0 and (m.estoque_atual or 0) <= (m.estoque_minimo or 0)
    ]
    materiais_zerados = [m for m in materiais_ativos if (m.estoque_atual or 0) <= 0]

    req_pendentes = Requisicao.query.filter_by(status="Pendente").count()
    req_aprovadas = Requisicao.query.filter(
        Requisicao.status.in_(["Aprovada", "Em Separação", "Atendida Parcialmente"])
    ).count()

    # Entregas do mês (agregação SQL)
    entregas_mes_stats = (
        db.session.query(
            func.count(AlmoxEntrega.id),
            func.coalesce(func.sum(AlmoxEntrega.valor_total), 0.0),
        )
        .filter(
            AlmoxEntrega.data_entrega >= inicio_mes,
            AlmoxEntrega.status != "Estornada",
            AlmoxEntrega.tipo_saida.in_(["Requisição", "Entrega Direta", "Distribuição em Lote"]),
        )
        .first()
    )
    qtd_entregas_mes = int(entregas_mes_stats[0] or 0)
    valor_entregas_mes = round(float(entregas_mes_stats[1] or 0.0), 2)

    # Entradas do mês (agregação SQL)
    valor_entradas_mes = round(
        float(
            db.session.query(func.coalesce(func.sum(AlmoxEntrada.valor_total), 0.0))
            .filter(AlmoxEntrada.data_recebimento >= inicio_mes, AlmoxEntrada.status == "Confirmada")
            .scalar()
            or 0.0
        ),
        2,
    )

    # Lotes próximos ao vencimento (30 dias ou vencidos)
    lotes_alerta = (
        AlmoxLote.query.options(joinedload(AlmoxLote.material))
        .filter(
            AlmoxLote.quantidade_atual > 0,
            AlmoxLote.data_validade.isnot(None),
            AlmoxLote.data_validade <= limite_30d,
        )
        .order_by(AlmoxLote.data_validade.asc())
        .limit(10)
        .all()
    )

    # Evolução dos últimos 6 meses (Entradas vs Saídas em R$)
    meses_labels = []
    meses_entradas = []
    meses_saidas = []
    for offset in range(5, -1, -1):
        ano_ref = hoje.year
        mes_ref = hoje.month - offset
        while mes_ref <= 0:
            mes_ref += 12
            ano_ref -= 1
        meses_labels.append(f"{mes_ref:02d}/{ano_ref}")

        v_ent = (
            db.session.query(func.coalesce(func.sum(AlmoxEntrada.valor_total), 0.0))
            .filter(
                extract("year", AlmoxEntrada.data_recebimento) == ano_ref,
                extract("month", AlmoxEntrada.data_recebimento) == mes_ref,
                AlmoxEntrada.status == "Confirmada",
            )
            .scalar()
            or 0.0
        )
        v_sai = (
            db.session.query(func.coalesce(func.sum(AlmoxEntrega.valor_total), 0.0))
            .filter(
                extract("year", AlmoxEntrega.data_entrega) == ano_ref,
                extract("month", AlmoxEntrega.data_entrega) == mes_ref,
                AlmoxEntrega.status != "Estornada",
                AlmoxEntrega.tipo_saida.in_(["Requisição", "Entrega Direta", "Distribuição em Lote"]),
            )
            .scalar()
            or 0.0
        )
        meses_entradas.append(round(float(v_ent), 2))
        meses_saidas.append(round(float(v_sai), 2))

    # Top 5 Escolas e Top 5 Setores da SEME no ano atual
    inicio_ano = date(hoje.year, 1, 1)
    top_escolas = (
        db.session.query(
            Escola.nome,
            func.count(AlmoxEntrega.id).label("qtd_guias"),
            func.coalesce(func.sum(AlmoxEntrega.valor_total), 0.0).label("total_valor"),
        )
        .join(AlmoxEntrega, AlmoxEntrega.escola_id == Escola.id)
        .filter(
            AlmoxEntrega.data_entrega >= inicio_ano,
            AlmoxEntrega.status != "Estornada",
            AlmoxEntrega.tipo_saida.in_(["Requisição", "Entrega Direta", "Distribuição em Lote"]),
        )
        .group_by(Escola.id, Escola.nome)
        .order_by(func.sum(AlmoxEntrega.valor_total).desc())
        .limit(5)
        .all()
    )

    top_setores = (
        db.session.query(
            AlmoxSetor.nome,
            func.count(AlmoxEntrega.id).label("qtd_guias"),
            func.coalesce(func.sum(AlmoxEntrega.valor_total), 0.0).label("total_valor"),
        )
        .join(AlmoxEntrega, AlmoxEntrega.setor_id == AlmoxSetor.id)
        .filter(
            AlmoxEntrega.data_entrega >= inicio_ano,
            AlmoxEntrega.status != "Estornada",
            AlmoxEntrega.tipo_saida.in_(["Requisição", "Entrega Direta", "Distribuição em Lote"]),
        )
        .group_by(AlmoxSetor.id, AlmoxSetor.nome)
        .order_by(func.sum(AlmoxEntrega.valor_total).desc())
        .limit(5)
        .all()
    )

    ultimas_entregas = (
        AlmoxEntrega.query.options(joinedload(AlmoxEntrega.escola), joinedload(AlmoxEntrega.setor))
        .order_by(AlmoxEntrega.data_emissao.desc())
        .limit(8)
        .all()
    )

    inv_bloqueio = verificar_bloqueio_inventario()

    return render_template(
        "almoxarifado/dashboard.html",
        total_itens=total_itens,
        materiais_baixo_estoque=materiais_baixo_estoque,
        materiais_zerados=materiais_zerados,
        valor_total_estoque=valor_total_estoque,
        req_pendentes=req_pendentes,
        req_aprovadas=req_aprovadas,
        qtd_entregas_mes=qtd_entregas_mes,
        valor_entregas_mes=valor_entregas_mes,
        valor_entradas_mes=valor_entradas_mes,
        lotes_alerta=lotes_alerta,
        meses_labels_json=json.dumps(meses_labels),
        meses_entradas_json=json.dumps(meses_entradas),
        meses_saidas_json=json.dumps(meses_saidas),
        top_escolas=top_escolas,
        top_setores=top_setores,
        ultimas_entregas=ultimas_entregas,
        inv_bloqueio=inv_bloqueio,
        hoje=hoje,
        formatar_moeda_br=formatar_moeda_br,
    )


# =============================================================================
# 2. CADASTROS BASE: MATERIAIS, CATEGORIAS (3.3.90.30), FORNECEDORES, SETORES SEME
# =============================================================================

@almoxarifado_bp.route("/materiais", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def gerenciar_materiais():
    if request.method == "POST":
        try:
            descricao = (request.form.get("descricao") or "").strip()
            unidade_medida = (request.form.get("unidade_medida") or "UN").strip().upper()
            if not descricao:
                flash("A descrição do material é obrigatória.", "warning")
                return redirect(url_for("almoxarifado.gerenciar_materiais"))

            categoria_id = request.form.get("categoria_id", type=int)
            cat_obj = AlmoxCategoria.query.get(categoria_id) if categoria_id else None
            categoria_txt = cat_obj.nome if cat_obj else (request.form.get("categoria") or "").strip()

            codigo_interno = (request.form.get("codigo_interno") or "").strip() or None
            codigo_barras = (request.form.get("codigo_barras") or "").strip() or None
            fator = parse_float_br(request.form.get("fator_conversao"), 1.0)
            if fator <= 0:
                fator = 1.0

            novo_material = Material(
                codigo_interno=codigo_interno,
                codigo_catmat=(request.form.get("codigo_catmat") or "").strip() or None,
                descricao=descricao,
                especificacao_tecnica=(request.form.get("especificacao_tecnica") or "").strip() or None,
                marca_referencia=(request.form.get("marca_referencia") or "").strip() or None,
                unidade_medida=unidade_medida,
                unidade_compra=(request.form.get("unidade_compra") or "").strip().upper() or unidade_medida,
                fator_conversao=fator,
                categoria=categoria_txt,
                categoria_id=categoria_id,
                estoque_minimo=parse_float_br(request.form.get("estoque_minimo"), 0.0),
                ponto_pedido=parse_float_br(request.form.get("ponto_pedido"), 0.0),
                estoque_maximo=parse_float_br(request.form.get("estoque_maximo"), 0.0),
                localizacao_fisica=(request.form.get("localizacao_fisica") or "").strip() or None,
                codigo_barras=codigo_barras,
                fornecedor_padrao_id=request.form.get("fornecedor_padrao_id", type=int),
                controla_validade=bool(request.form.get("controla_validade")),
                controla_lote=bool(request.form.get("controla_lote")),
                ativo=True,
            )
            db.session.add(novo_material)
            db.session.flush()
            if not novo_material.codigo_interno:
                novo_material.codigo_interno = f"MAT-{novo_material.id:04d}"
            db.session.commit()
            registrar_log(f'Almoxarifado: cadastrou material "{novo_material.descricao}" ({novo_material.codigo_interno}).')
            flash("Material cadastrado com sucesso!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar material: {e}", "danger")
        return redirect(url_for("almoxarifado.gerenciar_materiais"))

    busca = (request.args.get("busca") or "").strip()
    categoria_id_filtro = request.args.get("categoria_id", type=int)
    status_filtro = request.args.get("status", "ativos")

    query = Material.query.options(joinedload(Material.categoria_rel), joinedload(Material.fornecedor_padrao))
    if status_filtro == "ativos":
        query = query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None)))
    elif status_filtro == "inativos":
        query = query.filter(Material.ativo.is_(False))
    elif status_filtro == "baixo":
        query = query.filter(
            or_(Material.ativo.is_(True), Material.ativo.is_(None)),
            Material.estoque_minimo > 0,
            Material.estoque_atual <= Material.estoque_minimo,
        )
    elif status_filtro == "zerado":
        query = query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None)), Material.estoque_atual <= 0)

    if categoria_id_filtro:
        query = query.filter(Material.categoria_id == categoria_id_filtro)
    if busca:
        termo = f"%{busca}%"
        query = query.filter(
            or_(
                Material.descricao.ilike(termo),
                Material.codigo_interno.ilike(termo),
                Material.codigo_barras.ilike(termo),
                Material.localizacao_fisica.ilike(termo),
            )
        )

    materiais = query.order_by(Material.descricao).all()
    categorias = AlmoxCategoria.query.filter_by(ativo=True).order_by(AlmoxCategoria.codigo_subelemento, AlmoxCategoria.nome).all()
    fornecedores = Fornecedor.query.filter(or_(Fornecedor.ativo.is_(True), Fornecedor.ativo.is_(None))).order_by(Fornecedor.nome).all()

    return render_template(
        "almoxarifado/materiais.html",
        materiais=materiais,
        categorias=categorias,
        fornecedores=fornecedores,
        busca=busca,
        categoria_id_filtro=categoria_id_filtro,
        status_filtro=status_filtro,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/materiais/editar/<int:material_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def editar_material(material_id):
    material = Material.query.get_or_404(material_id)
    try:
        categoria_id = request.form.get("categoria_id", type=int)
        cat_obj = AlmoxCategoria.query.get(categoria_id) if categoria_id else None

        material.codigo_interno = (request.form.get("codigo_interno") or "").strip() or material.codigo_interno
        material.codigo_catmat = (request.form.get("codigo_catmat") or "").strip() or None
        material.descricao = (request.form.get("descricao") or material.descricao).strip()
        material.especificacao_tecnica = (request.form.get("especificacao_tecnica") or "").strip() or None
        material.marca_referencia = (request.form.get("marca_referencia") or "").strip() or None
        material.unidade_medida = (request.form.get("unidade_medida") or material.unidade_medida).strip().upper()
        material.unidade_compra = (request.form.get("unidade_compra") or material.unidade_medida).strip().upper()
        fator = parse_float_br(request.form.get("fator_conversao"), 1.0)
        material.fator_conversao = fator if fator > 0 else 1.0
        material.categoria_id = categoria_id
        material.categoria = cat_obj.nome if cat_obj else (request.form.get("categoria") or material.categoria)
        material.estoque_minimo = parse_float_br(request.form.get("estoque_minimo"), 0.0)
        material.ponto_pedido = parse_float_br(request.form.get("ponto_pedido"), 0.0)
        material.estoque_maximo = parse_float_br(request.form.get("estoque_maximo"), 0.0)
        material.localizacao_fisica = (request.form.get("localizacao_fisica") or "").strip() or None
        material.codigo_barras = (request.form.get("codigo_barras") or "").strip() or None
        material.fornecedor_padrao_id = request.form.get("fornecedor_padrao_id", type=int)
        material.controla_validade = bool(request.form.get("controla_validade"))
        material.controla_lote = bool(request.form.get("controla_lote"))
        db.session.commit()
        registrar_log(f'Almoxarifado: atualizou material "{material.descricao}" (#{material.id}).')
        flash("Material atualizado com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao atualizar o material: {e}", "danger")
    return redirect(url_for("almoxarifado.gerenciar_materiais"))


@almoxarifado_bp.route("/materiais/toggle-ativo/<int:material_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def toggle_ativo_material(material_id):
    material = Material.query.get_or_404(material_id)
    if material.ativo and (material.estoque_atual or 0) > 0:
        flash("Não é possível inativar um material com saldo em estoque. Realize a saída/ajuste primeiro.", "warning")
        return redirect(url_for("almoxarifado.gerenciar_materiais"))
    material.ativo = not bool(material.ativo)
    db.session.commit()
    acao = "reativou" if material.ativo else "inativou"
    registrar_log(f'Almoxarifado: {acao} material "{material.descricao}" (#{material.id}).')
    flash(f'Material "{material.descricao}" {"reativado" if material.ativo else "inativado"} com sucesso.', "info")
    return redirect(url_for("almoxarifado.gerenciar_materiais"))


@almoxarifado_bp.route("/materiais/excluir/<int:material_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def excluir_material(material_id):
    material = Material.query.get_or_404(material_id)
    if (material.estoque_atual or 0) > 0:
        flash("Não é possível excluir um material com estoque. Ajuste ou zere o saldo primeiro.", "danger")
        return redirect(url_for("almoxarifado.gerenciar_materiais"))
    tem_historico = MovimentoEstoque.query.filter_by(material_id=material.id).first() is not None
    if tem_historico:
        material.ativo = False
        db.session.commit()
        registrar_log(f'Almoxarifado: inativou material "{material.descricao}" (possui histórico contábil).')
        flash("Material possui histórico contábil (Kardex) e não pode ser excluído fisicamente — foi INATIVADO para preservar auditoria.", "warning")
        return redirect(url_for("almoxarifado.gerenciar_materiais"))
    try:
        nome_mat = material.descricao
        db.session.delete(material)
        db.session.commit()
        registrar_log(f'Almoxarifado: excluiu material "{nome_mat}" (#{material_id}).')
        flash("Material excluído com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir o material: {e}", "danger")
    return redirect(url_for("almoxarifado.gerenciar_materiais"))


@almoxarifado_bp.route("/materiais/qrcode/<int:material_id>")
@login_required
@role_required("admin", "almoxarifado")
def gerar_qrcode_material(material_id):
    material = Material.query.get_or_404(material_id)
    qr_content = f"SEME_ALMOX_MAT:{material.codigo_interno or material.id}"
    img = qrcode.make(qr_content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_str = base64.b64encode(buf.getvalue()).decode("utf-8")
    return render_template("almoxarifado/qrcode_material.html", material=material, qr_code_image=img_str)


@almoxarifado_bp.route("/materiais/ajustar/<int:material_id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def ajustar_estoque(material_id):
    material = Material.query.get_or_404(material_id)
    if request.method == "POST":
        inv_bloq = verificar_bloqueio_inventario()
        if inv_bloq:
            flash(f"Movimentações bloqueadas pelo Inventário {inv_bloq.codigo}. Realize o ajuste pelo próprio inventário.", "warning")
            return redirect(url_for("almoxarifado.ajustar_estoque", material_id=material.id))
        try:
            nova_quantidade = parse_float_br(request.form.get("nova_quantidade"), -1.0)
            justificativa = (request.form.get("justificativa") or "").strip()
            if nova_quantidade < 0 or not justificativa:
                flash("Informe uma nova quantidade válida (>= 0) e a justificativa obrigatória.", "warning")
                return redirect(url_for("almoxarifado.ajustar_estoque", material_id=material.id))

            mat = _lock_material(material.id)
            saldo_ant = float(mat.estoque_atual or 0.0)
            diferenca = round(nova_quantidade - saldo_ant, 4)
            if diferenca == 0:
                flash("A nova quantidade é igual ao estoque atual. Nenhum ajuste foi feito.", "info")
                return redirect(url_for("almoxarifado.gerenciar_materiais"))

            cm = mat.custo_referencia
            mat.estoque_atual = nova_quantidade
            tipo = "Ajuste Inventário (+)" if diferenca > 0 else "Ajuste Inventário (-)"
            mov = MovimentoEstoque(
                material_id=mat.id,
                tipo_movimento=tipo,
                quantidade=diferenca,
                valor_unitario=cm,
                valor_total=round(abs(diferenca) * cm, 2),
                saldo_anterior=saldo_ant,
                saldo_apos=nova_quantidade,
                custo_medio_apos=cm,
                justificativa=justificativa,
                usuario_id=_obter_usuario_id_atual(),
            )
            db.session.add(mov)
            db.session.commit()
            registrar_log(f'Almoxarifado: ajustou estoque de "{mat.descricao}" de {saldo_ant} para {nova_quantidade}. Motivo: {justificativa}')
            flash(f'Estoque de "{mat.descricao}" ajustado com sucesso!', "success")
            return redirect(url_for("almoxarifado.gerenciar_materiais"))
        except Exception as e:
            db.session.rollback()
            flash(f"Ocorreu um erro ao ajustar o estoque: {e}", "danger")

    return render_template("almoxarifado/ajuste_estoque.html", material=material)


# --- CATEGORIAS / SUBELEMENTOS 3.3.90.30 ---
@almoxarifado_bp.route("/categorias", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def gerenciar_categorias():
    if request.method == "POST":
        try:
            nome = (request.form.get("nome") or "").strip()
            if not nome:
                flash("O nome da categoria é obrigatório.", "warning")
                return redirect(url_for("almoxarifado.gerenciar_categorias"))
            nova = AlmoxCategoria(
                codigo_subelemento=(request.form.get("codigo_subelemento") or "").strip() or None,
                conta_contabil=(request.form.get("conta_contabil") or "").strip() or None,
                nome=nome,
                descricao=(request.form.get("descricao") or "").strip() or None,
                ativo=True,
            )
            db.session.add(nova)
            db.session.commit()
            registrar_log(f'Almoxarifado: cadastrou categoria contábil "{nova.nome}" ({nova.codigo_subelemento}).')
            flash("Categoria contábil cadastrada com sucesso!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar categoria: {e}", "danger")
        return redirect(url_for("almoxarifado.gerenciar_categorias"))

    categorias = AlmoxCategoria.query.order_by(AlmoxCategoria.codigo_subelemento, AlmoxCategoria.nome).all()
    return render_template("almoxarifado/categorias.html", categorias=categorias)


@almoxarifado_bp.route("/categorias/editar/<int:cat_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def editar_categoria(cat_id):
    cat = AlmoxCategoria.query.get_or_404(cat_id)
    try:
        cat.codigo_subelemento = (request.form.get("codigo_subelemento") or "").strip() or None
        cat.conta_contabil = (request.form.get("conta_contabil") or "").strip() or None
        cat.nome = (request.form.get("nome") or cat.nome).strip()
        cat.descricao = (request.form.get("descricao") or "").strip() or None
        db.session.commit()
        registrar_log(f'Almoxarifado: editou categoria "{cat.nome}".')
        flash("Categoria atualizada com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao editar categoria: {e}", "danger")
    return redirect(url_for("almoxarifado.gerenciar_categorias"))


# --- FORNECEDORES ---
@almoxarifado_bp.route("/fornecedores", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def gerenciar_fornecedores():
    if request.method == "POST":
        try:
            nome = (request.form.get("nome") or "").strip()
            if not nome:
                flash("O nome do fornecedor é obrigatório.", "warning")
                return redirect(url_for("almoxarifado.gerenciar_fornecedores"))
            novo_fornecedor = Fornecedor(
                nome=nome,
                razao_social=(request.form.get("razao_social") or "").strip() or None,
                cnpj=(request.form.get("cnpj") or "").strip() or None,
                inscricao_estadual=(request.form.get("inscricao_estadual") or "").strip() or None,
                endereco=(request.form.get("endereco") or "").strip() or None,
                telefone=(request.form.get("telefone") or "").strip() or None,
                email=(request.form.get("email") or "").strip() or None,
                contato_nome=(request.form.get("contato_nome") or "").strip() or None,
                dados_bancarios=(request.form.get("dados_bancarios") or "").strip() or None,
                ativo=True,
            )
            db.session.add(novo_fornecedor)
            db.session.commit()
            registrar_log(f'Almoxarifado: cadastrou fornecedor "{novo_fornecedor.nome}" ({novo_fornecedor.cnpj}).')
            flash("Fornecedor cadastrado com sucesso!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar fornecedor: {e}", "danger")
        return redirect(url_for("almoxarifado.gerenciar_fornecedores"))

    fornecedores = Fornecedor.query.order_by(Fornecedor.nome).all()
    return render_template("almoxarifado/fornecedores.html", fornecedores=fornecedores)


@almoxarifado_bp.route("/fornecedores/editar/<int:fornecedor_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def editar_fornecedor(fornecedor_id):
    fornecedor = Fornecedor.query.get_or_404(fornecedor_id)
    try:
        fornecedor.nome = (request.form.get("nome") or fornecedor.nome).strip()
        fornecedor.razao_social = (request.form.get("razao_social") or "").strip() or None
        fornecedor.cnpj = (request.form.get("cnpj") or "").strip() or None
        fornecedor.inscricao_estadual = (request.form.get("inscricao_estadual") or "").strip() or None
        fornecedor.endereco = (request.form.get("endereco") or "").strip() or None
        fornecedor.telefone = (request.form.get("telefone") or "").strip() or None
        fornecedor.email = (request.form.get("email") or "").strip() or None
        fornecedor.contato_nome = (request.form.get("contato_nome") or "").strip() or None
        fornecedor.dados_bancarios = (request.form.get("dados_bancarios") or "").strip() or None
        db.session.commit()
        registrar_log(f'Almoxarifado: atualizou fornecedor "{fornecedor.nome}".')
        flash("Fornecedor atualizado com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao atualizar o fornecedor: {e}", "danger")
    return redirect(url_for("almoxarifado.gerenciar_fornecedores"))


@almoxarifado_bp.route("/fornecedores/excluir/<int:fornecedor_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def excluir_fornecedor(fornecedor_id):
    fornecedor = Fornecedor.query.get_or_404(fornecedor_id)
    tem_entradas = AlmoxEntrada.query.filter_by(fornecedor_id=fornecedor.id).first() is not None
    if tem_entradas:
        fornecedor.ativo = False
        db.session.commit()
        registrar_log(f'Almoxarifado: inativou fornecedor "{fornecedor.nome}" (possui notas fiscais vinculadas).')
        flash("Fornecedor possui notas fiscais vinculadas e foi inativado para manter o histórico.", "warning")
        return redirect(url_for("almoxarifado.gerenciar_fornecedores"))
    try:
        nome = fornecedor.nome
        db.session.delete(fornecedor)
        db.session.commit()
        registrar_log(f'Almoxarifado: excluiu fornecedor "{nome}".')
        flash("Fornecedor excluído com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir fornecedor: {e}", "danger")
    return redirect(url_for("almoxarifado.gerenciar_fornecedores"))


# --- SETORES DA SEME + LISTA DE ESCOLAS DA REDE MUNICIPAL ---
@almoxarifado_bp.route("/setores", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def gerenciar_setores():
    if request.method == "POST":
        try:
            nome = (request.form.get("nome") or "").strip()
            if not nome:
                flash("O nome do setor da SEME é obrigatório.", "warning")
                return redirect(url_for("almoxarifado.gerenciar_setores"))
            novo = AlmoxSetor(
                codigo=(request.form.get("codigo") or "").strip().upper() or None,
                nome=nome,
                sigla=(request.form.get("sigla") or "").strip().upper() or None,
                responsavel_nome=(request.form.get("responsavel_nome") or "").strip() or None,
                telefone=(request.form.get("telefone") or "").strip() or None,
                email=(request.form.get("email") or "").strip() or None,
                observacoes=(request.form.get("observacoes") or "").strip() or None,
                secretaria_id=session.get("secretaria_id"),
                ativo=True,
            )
            db.session.add(novo)
            db.session.commit()
            registrar_log(f'Almoxarifado: cadastrou setor da SEME "{novo.nome}".')
            flash("Setor da SEME cadastrado com sucesso!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar setor: {e}", "danger")
        return redirect(url_for("almoxarifado.gerenciar_setores"))

    setores = AlmoxSetor.query.order_by(AlmoxSetor.ativo.desc(), AlmoxSetor.nome).all()
    escolas = Escola.query.order_by(Escola.nome).all()
    return render_template("almoxarifado/setores.html", setores=setores, escolas=escolas)


@almoxarifado_bp.route("/setores/editar/<int:setor_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def editar_setor(setor_id):
    setor = AlmoxSetor.query.get_or_404(setor_id)
    try:
        setor.codigo = (request.form.get("codigo") or "").strip().upper() or None
        setor.nome = (request.form.get("nome") or setor.nome).strip()
        setor.sigla = (request.form.get("sigla") or "").strip().upper() or None
        setor.responsavel_nome = (request.form.get("responsavel_nome") or "").strip() or None
        setor.telefone = (request.form.get("telefone") or "").strip() or None
        setor.email = (request.form.get("email") or "").strip() or None
        setor.observacoes = (request.form.get("observacoes") or "").strip() or None
        db.session.commit()
        registrar_log(f'Almoxarifado: editou setor da SEME "{setor.nome}".')
        flash("Setor atualizado com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao atualizar setor: {e}", "danger")
    return redirect(url_for("almoxarifado.gerenciar_setores"))


@almoxarifado_bp.route("/setores/toggle-ativo/<int:setor_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def toggle_ativo_setor(setor_id):
    setor = AlmoxSetor.query.get_or_404(setor_id)
    setor.ativo = not bool(setor.ativo)
    db.session.commit()
    registrar_log(f'Almoxarifado: {"reativou" if setor.ativo else "inativou"} setor "{setor.nome}".')
    flash(f'Setor "{setor.nome}" {"reativado" if setor.ativo else "inativado"}.', "info")
    return redirect(url_for("almoxarifado.gerenciar_setores"))


# =============================================================================
# 3. ENTRADAS EM LOTE (NOTA FISCAL, EMPENHO, XML NF-e, LOTES E CMPM)
# =============================================================================

@almoxarifado_bp.route("/entradas")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def listar_entradas():
    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")
    fornecedor_id = request.args.get("fornecedor_id", type=int)
    busca = (request.args.get("busca") or "").strip()
    status = request.args.get("status", "")

    query = AlmoxEntrada.query.options(
        joinedload(AlmoxEntrada.fornecedor),
        joinedload(AlmoxEntrada.usuario),
        joinedload(AlmoxEntrada.itens),
    )
    if data_inicio:
        d_i = _parse_date_opt(data_inicio)
        if d_i:
            query = query.filter(AlmoxEntrada.data_recebimento >= d_i)
    if data_fim:
        d_f = _parse_date_opt(data_fim)
        if d_f:
            query = query.filter(AlmoxEntrada.data_recebimento <= d_f)
    if fornecedor_id:
        query = query.filter(AlmoxEntrada.fornecedor_id == fornecedor_id)
    if status:
        query = query.filter(AlmoxEntrada.status == status)
    if busca:
        t = f"%{busca}%"
        query = query.filter(
            or_(
                AlmoxEntrada.numero_entrada.ilike(t),
                AlmoxEntrada.numero_nf.ilike(t),
                AlmoxEntrada.numero_empenho.ilike(t),
                AlmoxEntrada.numero_contrato_arp.ilike(t),
            )
        )

    entradas = query.order_by(AlmoxEntrada.data_recebimento.desc(), AlmoxEntrada.id.desc()).limit(200).all()
    total_valor = round(sum(e.valor_total or 0.0 for e in entradas if e.status == "Confirmada"), 2)
    fornecedores = Fornecedor.query.order_by(Fornecedor.nome).all()

    return render_template(
        "almoxarifado/entradas.html",
        entradas=entradas,
        fornecedores=fornecedores,
        total_valor=total_valor,
        data_inicio=data_inicio,
        data_fim=data_fim,
        fornecedor_id=fornecedor_id,
        busca=busca,
        status=status,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/entradas/importar-xml", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def importar_xml_nfe():
    """Recebe arquivo XML de NF-e via AJAX e devolve os dados estruturados + correspondência automática no catálogo."""
    arquivo = request.files.get("xml_file")
    if not arquivo or not arquivo.filename:
        return jsonify({"ok": False, "erro": "Selecione um arquivo XML de NF-e."}), 400
    try:
        dados = parse_xml_nfe(arquivo.read())

        # Tenta localizar fornecedor cadastrado pelo CNPJ
        forn_id = None
        cnpj_digits = dados["fornecedor"].get("cnpj_digits") or ""
        if cnpj_digits:
            for f in Fornecedor.query.all():
                if re.sub(r"\D", "", f.cnpj or "") == cnpj_digits:
                    forn_id = f.id
                    break
        dados["fornecedor_id_encontrado"] = forn_id

        # Tenta associar cada item ao catálogo por código de barras ou descrição
        materiais = Material.query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None))).all()
        mapa_ean = {(m.codigo_barras or "").strip(): m.id for m in materiais if m.codigo_barras}
        for item in dados["itens"]:
            mat_id = None
            ean = (item.get("codigo_barras") or "").strip()
            if ean and ean in mapa_ean:
                mat_id = mapa_ean[ean]
            else:
                desc_xml = (item.get("descricao") or "").lower()
                for m in materiais:
                    if m.descricao.lower() == desc_xml or (len(m.descricao) >= 6 and m.descricao.lower() in desc_xml):
                        mat_id = m.id
                        break
            item["material_id_sugerido"] = mat_id

        return jsonify({"ok": True, "dados": dados})
    except Exception as e:
        return jsonify({"ok": False, "erro": f"Não foi possível ler o XML da NF-e: {e}"}), 400


@almoxarifado_bp.route("/entradas/nova", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def nova_entrada():
    if request.method == "POST":
        inv_bloq = verificar_bloqueio_inventario()
        if inv_bloq:
            flash(f"Entradas temporariamente bloqueadas pelo Inventário {inv_bloq.codigo}.", "warning")
            return redirect(url_for("almoxarifado.listar_entradas"))
        try:
            material_ids = request.form.getlist("material_id[]")
            qtds_emb = request.form.getlist("quantidade[]")
            unidades_ent = request.form.getlist("unidade_entrada[]")
            fatores = request.form.getlist("fator_conversao[]")
            valores_unit = request.form.getlist("valor_unitario[]")
            lotes_num = request.form.getlist("lote[]")
            validades = request.form.getlist("data_validade[]")

            if not material_ids:
                flash("Adicione pelo menos um item à entrada.", "warning")
                return redirect(url_for("almoxarifado.nova_entrada"))

            fornecedor_id = request.form.get("fornecedor_id", type=int)
            forn_novo_nome = (request.form.get("fornecedor_novo_nome") or "").strip()
            forn_novo_cnpj = (request.form.get("fornecedor_novo_cnpj") or "").strip()

            # Cadastro rápido de fornecedor (ex: vindo do XML NF-e)
            if not fornecedor_id and forn_novo_nome:
                forn_exist = None
                if forn_novo_cnpj:
                    cnpj_dig = re.sub(r"\D", "", forn_novo_cnpj)
                    for f in Fornecedor.query.all():
                        if re.sub(r"\D", "", f.cnpj or "") == cnpj_dig:
                            forn_exist = f
                            break
                if forn_exist:
                    fornecedor_id = forn_exist.id
                else:
                    novo_f = Fornecedor(nome=forn_novo_nome, cnpj=forn_novo_cnpj or None, ativo=True)
                    db.session.add(novo_f)
                    db.session.flush()
                    fornecedor_id = novo_f.id

            forn_obj = Fornecedor.query.get(fornecedor_id) if fornecedor_id else None

            # Upload opcional do arquivo da NF-e para o Supabase Storage (nunca disco local na Railway)
            arquivo_nf_url = None
            arquivo_nf = request.files.get("arquivo_nf")
            if arquivo_nf and arquivo_nf.filename:
                arquivo_nf_url = upload_arquivo_para_nuvem(arquivo_nf, pasta="almoxarifado/notas_fiscais")

            numero_entrada = gerar_codigo_sequencial("ENT", AlmoxEntrada, "numero_entrada")
            data_rec = _parse_date_opt(request.form.get("data_recebimento")) or date.today()
            tipo_entrada = (request.form.get("tipo_entrada") or "Compra / Licitação").strip()
            numero_nf = (request.form.get("numero_nf") or "").strip() or None
            usuario_id = _obter_usuario_id_atual()

            entrada = AlmoxEntrada(
                numero_entrada=numero_entrada,
                tipo_entrada=tipo_entrada,
                numero_nf=numero_nf,
                serie_nf=(request.form.get("serie_nf") or "").strip() or None,
                chave_nfe=(request.form.get("chave_nfe") or "").strip() or None,
                data_emissao_nf=_parse_date_opt(request.form.get("data_emissao_nf")),
                data_recebimento=data_rec,
                fornecedor_id=fornecedor_id,
                fornecedor_nome_snapshot=forn_obj.nome if forn_obj else forn_novo_nome or None,
                contrato_fiscal_id=request.form.get("contrato_fiscal_id", type=int),
                numero_empenho=(request.form.get("numero_empenho") or "").strip() or None,
                numero_processo=(request.form.get("numero_processo") or "").strip() or None,
                modalidade_licitacao=(request.form.get("modalidade_licitacao") or "").strip() or None,
                numero_contrato_arp=(request.form.get("numero_contrato_arp") or "").strip() or None,
                fonte_recursos=(request.form.get("fonte_recursos") or "").strip() or None,
                arquivo_nf_url=arquivo_nf_url,
                observacoes=(request.form.get("observacoes") or "").strip() or None,
                conferido_por_nome=(request.form.get("conferido_por_nome") or "").strip() or None,
                status="Confirmada",
                usuario_id=usuario_id,
            )
            db.session.add(entrada)
            db.session.flush()

            valor_total_entrada = 0.0
            itens_validos = 0

            for idx, m_id_raw in enumerate(material_ids):
                if not m_id_raw:
                    continue
                mat = _lock_material(int(m_id_raw))
                if not mat:
                    continue
                qtd_emb = parse_float_br(qtds_emb[idx] if idx < len(qtds_emb) else 0, 0.0)
                if qtd_emb <= 0:
                    continue
                fator = parse_float_br(fatores[idx] if idx < len(fatores) else 1, 1.0)
                if fator <= 0:
                    fator = 1.0
                qtd_base = round(qtd_emb * fator, 4)
                # Valor unitário informado é por unidade de entrada (embalagem) -> converte para base
                v_unit_emb = parse_float_br(valores_unit[idx] if idx < len(valores_unit) else 0, 0.0)
                v_total_item = round(qtd_emb * v_unit_emb, 2)
                v_unit_base = round(v_total_item / qtd_base, 4) if qtd_base > 0 else 0.0
                unid_ent = (unidades_ent[idx] if idx < len(unidades_ent) else mat.unidade_medida) or mat.unidade_medida
                lote_str = (lotes_num[idx] if idx < len(lotes_num) else "").strip()
                dt_val = _parse_date_opt(validades[idx] if idx < len(validades) else "")

                ent_item = AlmoxEntradaItem(
                    entrada_id=entrada.id,
                    material_id=mat.id,
                    quantidade_embalagem=qtd_emb,
                    unidade_entrada=unid_ent[:20],
                    fator_conversao=fator,
                    quantidade_base=qtd_base,
                    valor_unitario_base=v_unit_base,
                    valor_total=v_total_item,
                    lote=lote_str or None,
                    data_validade=dt_val,
                )
                db.session.add(ent_item)
                db.session.flush()

                lote_id = None
                if lote_str or dt_val or mat.controla_lote or mat.controla_validade:
                    lote_obj = AlmoxLote(
                        material_id=mat.id,
                        entrada_item_id=ent_item.id,
                        numero_lote=lote_str or f"L-{numero_entrada}-{idx+1}",
                        data_validade=dt_val,
                        quantidade_inicial=qtd_base,
                        quantidade_atual=qtd_base,
                        custo_unitario=v_unit_base,
                    )
                    db.session.add(lote_obj)
                    db.session.flush()
                    lote_id = lote_obj.id

                tipo_mov = "Entrada Doação" if "Doação" in tipo_entrada else "Entrada NF"
                _registrar_movimento_entrada(
                    material=mat,
                    quantidade_base=qtd_base,
                    valor_unitario_base=v_unit_base,
                    tipo_movimento=tipo_mov,
                    usuario_id=usuario_id,
                    documento_ref=numero_entrada,
                    nota_fiscal=numero_nf,
                    justificativa=f"{tipo_entrada} — {numero_entrada}" + (f" (NF {numero_nf})" if numero_nf else ""),
                    fornecedor_id=fornecedor_id,
                    entrada_id=entrada.id,
                    lote_id=lote_id,
                )
                valor_total_entrada = round(valor_total_entrada + v_total_item, 2)
                itens_validos += 1

            if itens_validos == 0:
                db.session.rollback()
                flash("Nenhum item válido com quantidade maior que zero foi informado.", "warning")
                return redirect(url_for("almoxarifado.nova_entrada"))

            entrada.valor_total = valor_total_entrada
            db.session.commit()
            registrar_log(
                f"Almoxarifado: registrou entrada {numero_entrada} (NF {numero_nf or 'S/N'}, "
                f"{itens_validos} item(ns), total {formatar_moeda_br(valor_total_entrada)})."
            )
            flash(f"Entrada {numero_entrada} registrada com sucesso! Estoque e Custo Médio atualizados.", "success")
            return redirect(url_for("almoxarifado.detalhes_entrada", entrada_id=entrada.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar entrada: {e}", "danger")

    materiais = (
        Material.query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None)))
        .order_by(Material.descricao)
        .all()
    )
    fornecedores = Fornecedor.query.filter(or_(Fornecedor.ativo.is_(True), Fornecedor.ativo.is_(None))).order_by(Fornecedor.nome).all()
    contratos = FiscalContrato.query.order_by(FiscalContrato.ano.desc(), FiscalContrato.num_contrato.desc()).limit(100).all()

    return render_template(
        "almoxarifado/nova_entrada.html",
        materiais=materiais,
        fornecedores=fornecedores,
        contratos=contratos,
        hoje_iso=date.today().strftime("%Y-%m-%d"),
    )


@almoxarifado_bp.route("/entradas/<int:entrada_id>")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def detalhes_entrada(entrada_id):
    entrada = (
        AlmoxEntrada.query.options(
            joinedload(AlmoxEntrada.fornecedor),
            joinedload(AlmoxEntrada.usuario),
            joinedload(AlmoxEntrada.itens).joinedload(AlmoxEntradaItem.material),
        )
        .filter_by(id=entrada_id)
        .first_or_404()
    )
    return render_template(
        "almoxarifado/detalhes_entrada.html",
        entrada=entrada,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/entradas/<int:entrada_id>/pdf")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def comprovante_entrada_pdf(entrada_id):
    entrada = (
        AlmoxEntrada.query.options(
            joinedload(AlmoxEntrada.fornecedor),
            joinedload(AlmoxEntrada.usuario),
            joinedload(AlmoxEntrada.itens).joinedload(AlmoxEntradaItem.material),
        )
        .filter_by(id=entrada_id)
        .first_or_404()
    )
    pdf_buf = gerar_pdf_comprovante_entrada(entrada)
    return Response(
        pdf_buf.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline;filename=entrada_{entrada.numero_entrada}.pdf"},
    )


@almoxarifado_bp.route("/entradas/<int:entrada_id>/estornar", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def estornar_entrada(entrada_id):
    entrada = AlmoxEntrada.query.get_or_404(entrada_id)
    if entrada.status == "Estornada":
        flash("Esta entrada já foi estornada.", "warning")
        return redirect(url_for("almoxarifado.detalhes_entrada", entrada_id=entrada.id))

    motivo = (request.form.get("motivo_estorno") or "").strip()
    if not motivo:
        flash("O motivo do estorno é obrigatório para fins de auditoria.", "warning")
        return redirect(url_for("almoxarifado.detalhes_entrada", entrada_id=entrada.id))

    try:
        usuario_id = _obter_usuario_id_atual()
        for it in entrada.itens:
            mat = _lock_material(it.material_id)
            if (mat.estoque_atual or 0.0) < it.quantidade_base - 1e-6:
                raise ValueError(
                    f'Não é possível estornar: o item "{mat.descricao}" já foi parcialmente consumido '
                    f'(saldo atual: {formatar_qtd(mat.estoque_atual)}, entrada: {formatar_qtd(it.quantidade_base)}).'
                )
            saldo_ant = float(mat.estoque_atual or 0.0)
            novo_saldo = round(saldo_ant - it.quantidade_base, 4)
            mat.estoque_atual = novo_saldo

            # Zera/deduz lote correspondente se existir
            lote_rel = AlmoxLote.query.filter_by(entrada_item_id=it.id).first()
            if lote_rel:
                lote_rel.quantidade_atual = max(0.0, round(lote_rel.quantidade_atual - it.quantidade_base, 4))

            mov_est = MovimentoEstoque(
                material_id=mat.id,
                tipo_movimento="Estorno Entrada",
                quantidade=-it.quantidade_base,
                valor_unitario=it.valor_unitario_base,
                valor_total=it.valor_total,
                saldo_anterior=saldo_ant,
                saldo_apos=novo_saldo,
                custo_medio_apos=mat.custo_referencia,
                nota_fiscal=entrada.numero_nf,
                documento_ref=entrada.numero_entrada,
                justificativa=f"Estorno da entrada {entrada.numero_entrada}: {motivo}",
                usuario_id=usuario_id,
                fornecedor_id=entrada.fornecedor_id,
                entrada_id=entrada.id,
            )
            db.session.add(mov_est)

        entrada.status = "Estornada"
        entrada.motivo_estorno = motivo
        entrada.data_estorno = datetime.utcnow()
        db.session.commit()
        registrar_log(f"Almoxarifado: estornou entrada {entrada.numero_entrada}. Motivo: {motivo}")
        flash(f"Entrada {entrada.numero_entrada} estornada com sucesso.", "info")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao estornar entrada: {e}", "danger")
    return redirect(url_for("almoxarifado.detalhes_entrada", entrada_id=entrada.id))


# Rota legada mantida com compatibilidade total, criando AlmoxEntrada formal e CMPM
@almoxarifado_bp.route("/materiais/entrada/<int:material_id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def entrada_material(material_id):
    material = Material.query.get_or_404(material_id)
    if request.method == "POST":
        inv_bloq = verificar_bloqueio_inventario()
        if inv_bloq:
            flash(f"Movimentações bloqueadas pelo Inventário {inv_bloq.codigo}.", "warning")
            return redirect(url_for("almoxarifado.gerenciar_materiais"))
        try:
            quantidade = parse_float_br(request.form.get("quantidade"), 0.0)
            valor_unitario = parse_float_br(request.form.get("valor_unitario"), 0.0)
            if quantidade <= 0:
                flash("A quantidade deve ser maior que zero.", "warning")
                return redirect(url_for("almoxarifado.entrada_material", material_id=material.id))

            mat = _lock_material(material.id)
            fornecedor_id = request.form.get("fornecedor_id", type=int)
            forn_obj = Fornecedor.query.get(fornecedor_id) if fornecedor_id else None
            numero_entrada = gerar_codigo_sequencial("ENT", AlmoxEntrada, "numero_entrada")
            numero_nf = (request.form.get("nota_fiscal") or "").strip() or None
            justificativa = (request.form.get("justificativa") or "").strip() or None
            lote_str = (request.form.get("lote") or "").strip() or None
            dt_val = _parse_date_opt(request.form.get("data_validade"))
            usuario_id = _obter_usuario_id_atual()

            entrada = AlmoxEntrada(
                numero_entrada=numero_entrada,
                tipo_entrada=(request.form.get("tipo_entrada") or "Compra / Licitação").strip(),
                numero_nf=numero_nf,
                numero_empenho=(request.form.get("numero_empenho") or "").strip() or None,
                fonte_recursos=(request.form.get("fonte_recursos") or "").strip() or None,
                fornecedor_id=fornecedor_id,
                fornecedor_nome_snapshot=forn_obj.nome if forn_obj else None,
                data_recebimento=date.today(),
                valor_total=round(quantidade * valor_unitario, 2),
                observacoes=justificativa,
                status="Confirmada",
                usuario_id=usuario_id,
            )
            db.session.add(entrada)
            db.session.flush()

            ent_item = AlmoxEntradaItem(
                entrada_id=entrada.id,
                material_id=mat.id,
                quantidade_embalagem=quantidade,
                unidade_entrada=mat.unidade_medida,
                fator_conversao=1.0,
                quantidade_base=quantidade,
                valor_unitario_base=valor_unitario,
                valor_total=round(quantidade * valor_unitario, 2),
                lote=lote_str,
                data_validade=dt_val,
            )
            db.session.add(ent_item)
            db.session.flush()

            lote_id = None
            if lote_str or dt_val:
                lote_obj = AlmoxLote(
                    material_id=mat.id,
                    entrada_item_id=ent_item.id,
                    numero_lote=lote_str or f"L-{numero_entrada}",
                    data_validade=dt_val,
                    quantidade_inicial=quantidade,
                    quantidade_atual=quantidade,
                    custo_unitario=valor_unitario,
                )
                db.session.add(lote_obj)
                db.session.flush()
                lote_id = lote_obj.id

            _registrar_movimento_entrada(
                material=mat,
                quantidade_base=quantidade,
                valor_unitario_base=valor_unitario,
                tipo_movimento="Entrada NF",
                usuario_id=usuario_id,
                documento_ref=numero_entrada,
                nota_fiscal=numero_nf,
                justificativa=justificativa or f"Entrada rápida {numero_entrada}",
                fornecedor_id=fornecedor_id,
                entrada_id=entrada.id,
                lote_id=lote_id,
            )
            db.session.commit()
            registrar_log(f'Almoxarifado: entrada rápida {numero_entrada} de {quantidade} {mat.unidade_medida} de "{mat.descricao}".')
            flash(f'Entrada {numero_entrada} de {formatar_qtd(quantidade)} {mat.unidade_medida} de "{mat.descricao}" registrada!', "success")
            return redirect(url_for("almoxarifado.gerenciar_materiais"))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar entrada: {e}", "danger")

    fornecedores = Fornecedor.query.filter(or_(Fornecedor.ativo.is_(True), Fornecedor.ativo.is_(None))).order_by(Fornecedor.nome).all()
    return render_template("almoxarifado/entrada_material.html", material=material, fornecedores=fornecedores)


# =============================================================================
# 4. SAÍDAS, ENTREGAS DIRETAS, DISTRIBUIÇÃO EM LOTE, BAIXAS E DEVOLUÇÕES
# =============================================================================

@almoxarifado_bp.route("/entregas")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def listar_entregas():
    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")
    tipo_destino = request.args.get("tipo_destino", "")
    tipo_saida = request.args.get("tipo_saida", "")
    escola_id = request.args.get("escola_id", type=int)
    setor_id = request.args.get("setor_id", type=int)
    status = request.args.get("status", "")
    busca = (request.args.get("busca") or "").strip()

    query = AlmoxEntrega.query.options(
        joinedload(AlmoxEntrega.escola),
        joinedload(AlmoxEntrega.setor),
        joinedload(AlmoxEntrega.usuario_emissor),
        joinedload(AlmoxEntrega.itens),
    )
    if data_inicio:
        d = _parse_date_opt(data_inicio)
        if d:
            query = query.filter(AlmoxEntrega.data_entrega >= d)
    if data_fim:
        d = _parse_date_opt(data_fim)
        if d:
            query = query.filter(AlmoxEntrega.data_entrega <= d)
    if tipo_destino:
        query = query.filter(AlmoxEntrega.tipo_destino == tipo_destino)
    if tipo_saida:
        query = query.filter(AlmoxEntrega.tipo_saida == tipo_saida)
    if escola_id:
        query = query.filter(AlmoxEntrega.escola_id == escola_id)
    if setor_id:
        query = query.filter(AlmoxEntrega.setor_id == setor_id)
    if status:
        query = query.filter(AlmoxEntrega.status == status)
    if busca:
        t = f"%{busca}%"
        query = query.filter(
            or_(
                AlmoxEntrega.codigo_guia.ilike(t),
                AlmoxEntrega.destino_nome_snapshot.ilike(t),
                AlmoxEntrega.recebedor_nome.ilike(t),
                AlmoxEntrega.lote_distribuicao_codigo.ilike(t),
            )
        )

    entregas = query.order_by(AlmoxEntrega.data_entrega.desc(), AlmoxEntrega.id.desc()).limit(250).all()
    total_valor = round(sum(e.valor_total or 0.0 for e in entregas if e.status != "Estornada"), 2)
    escolas = Escola.query.order_by(Escola.nome).all()
    setores = AlmoxSetor.query.filter_by(ativo=True).order_by(AlmoxSetor.nome).all()

    return render_template(
        "almoxarifado/entregas.html",
        entregas=entregas,
        escolas=escolas,
        setores=setores,
        total_valor=total_valor,
        data_inicio=data_inicio,
        data_fim=data_fim,
        tipo_destino=tipo_destino,
        tipo_saida=tipo_saida,
        escola_id=escola_id,
        setor_id=setor_id,
        status=status,
        busca=busca,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/entregas/nova", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def nova_entrega():
    """Saída Direta / Entrega Avulsa para uma Escola Municipal ou Setor da SEME."""
    if request.method == "POST":
        inv_bloq = verificar_bloqueio_inventario()
        if inv_bloq:
            flash(f"Saídas bloqueadas pelo Inventário {inv_bloq.codigo}.", "warning")
            return redirect(url_for("almoxarifado.listar_entregas"))
        try:
            tipo_destino = request.form.get("tipo_destino", "Escola")
            escola_id = request.form.get("escola_id", type=int)
            setor_id = request.form.get("setor_id", type=int)
            outro_desc = request.form.get("destino_outro_descricao")
            tipo_dest, esc_id, set_id, nome_snap = _resolver_destino(tipo_destino, escola_id, setor_id, outro_desc)

            material_ids = request.form.getlist("material_id[]")
            quantidades = request.form.getlist("quantidade[]")
            if not material_ids:
                flash("Adicione pelo menos um material à entrega.", "warning")
                return redirect(url_for("almoxarifado.nova_entrega"))

            usuario_id = _obter_usuario_id_atual()
            codigo_guia = gerar_codigo_sequencial("GUIA", AlmoxEntrega, "codigo_guia")
            data_ent = _parse_date_opt(request.form.get("data_entrega")) or date.today()
            status_inicial = request.form.get("status") or "Entregue"

            entrega = AlmoxEntrega(
                codigo_guia=codigo_guia,
                codigo_autenticacao=gerar_codigo_autenticacao(),
                tipo_saida="Entrega Direta",
                tipo_destino=tipo_dest,
                escola_id=esc_id,
                setor_id=set_id,
                destino_nome_snapshot=nome_snap,
                data_entrega=data_ent,
                entregador_nome=(request.form.get("entregador_nome") or "").strip() or None,
                recebedor_nome=(request.form.get("recebedor_nome") or "").strip() or None,
                recebedor_cpf=(request.form.get("recebedor_cpf") or "").strip() or None,
                recebedor_cargo=(request.form.get("recebedor_cargo") or "").strip() or None,
                justificativa=(request.form.get("justificativa") or "").strip() or None,
                observacoes=(request.form.get("observacoes") or "").strip() or None,
                status=status_inicial,
                data_confirmacao_recebimento=datetime.utcnow() if status_inicial == "Entregue" else None,
                usuario_emissor_id=usuario_id,
            )
            db.session.add(entrega)
            db.session.flush()

            total_valor = 0.0
            qtd_itens = 0
            for idx, m_id_raw in enumerate(material_ids):
                if not m_id_raw:
                    continue
                qtd = parse_float_br(quantidades[idx] if idx < len(quantidades) else 0, 0.0)
                if qtd <= 0:
                    continue
                mat = _lock_material(int(m_id_raw))
                if not mat:
                    continue
                lote_id = _baixar_lotes_fefo(mat.id, qtd)
                _, cm, v_tot = _registrar_movimento_saida(
                    material=mat,
                    quantidade_base=qtd,
                    tipo_movimento="Saída Entrega Direta",
                    usuario_id=usuario_id,
                    documento_ref=codigo_guia,
                    justificativa=f"Entrega Direta {codigo_guia} para {nome_snap}",
                    entrega_id=entrega.id,
                    lote_id=lote_id,
                    escola_id=esc_id,
                    setor_id=set_id,
                )
                db.session.add(
                    AlmoxEntregaItem(
                        entrega_id=entrega.id,
                        material_id=mat.id,
                        lote_id=lote_id,
                        quantidade=qtd,
                        custo_unitario_medio=cm,
                        valor_total=v_tot,
                    )
                )
                total_valor = round(total_valor + v_tot, 2)
                qtd_itens += 1

            if qtd_itens == 0:
                db.session.rollback()
                flash("Informe pelo menos um item com quantidade maior que zero.", "warning")
                return redirect(url_for("almoxarifado.nova_entrega"))

            entrega.valor_total = total_valor
            db.session.commit()
            registrar_log(
                f'Almoxarifado: emitiu Guia {codigo_guia} (Entrega Direta) para "{nome_snap}" '
                f"({qtd_itens} item(ns), {formatar_moeda_br(total_valor)})."
            )
            flash(f"Guia de Entrega {codigo_guia} emitida com sucesso para {nome_snap}!", "success")
            return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar entrega: {e}", "danger")

    materiais = (
        Material.query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None)), Material.estoque_atual > 0)
        .order_by(Material.descricao)
        .all()
    )
    escolas = Escola.query.order_by(Escola.nome).all()
    setores = AlmoxSetor.query.filter_by(ativo=True).order_by(AlmoxSetor.nome).all()
    return render_template(
        "almoxarifado/nova_entrega.html",
        materiais=materiais,
        escolas=escolas,
        setores=setores,
        hoje_iso=date.today().strftime("%Y-%m-%d"),
    )


@almoxarifado_bp.route("/entregas/lote", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def distribuicao_lote():
    """Distribuição Programada em Lote: envia o mesmo conjunto de materiais/kit para várias Escolas e/ou Setores da SEME."""
    if request.method == "POST":
        inv_bloq = verificar_bloqueio_inventario()
        if inv_bloq:
            flash(f"Saídas bloqueadas pelo Inventário {inv_bloq.codigo}.", "warning")
            return redirect(url_for("almoxarifado.listar_entregas"))
        try:
            escola_ids = [int(x) for x in request.form.getlist("escola_ids[]") if x]
            setor_ids = [int(x) for x in request.form.getlist("setor_ids[]") if x]
            material_ids = request.form.getlist("material_id[]")
            quantidades_por_unidade = request.form.getlist("quantidade[]")

            destinos = []
            for eid in escola_ids:
                esc = Escola.query.get(eid)
                if esc:
                    destinos.append(("Escola", esc.id, None, esc.nome))
            for sid in setor_ids:
                st = AlmoxSetor.query.get(sid)
                if st:
                    destinos.append(("Setor SEME", None, st.id, st.nome))

            if not destinos:
                flash("Selecione pelo menos uma Escola ou Setor da SEME para a distribuição em lote.", "warning")
                return redirect(url_for("almoxarifado.distribuicao_lote"))

            itens_kit = []
            for idx, m_id_raw in enumerate(material_ids):
                if not m_id_raw:
                    continue
                q = parse_float_br(quantidades_por_unidade[idx] if idx < len(quantidades_por_unidade) else 0, 0.0)
                if q > 0:
                    itens_kit.append((int(m_id_raw), q))

            if not itens_kit:
                flash("Adicione pelo menos um material com quantidade maior que zero.", "warning")
                return redirect(url_for("almoxarifado.distribuicao_lote"))

            # Validação prévia de estoque consolidado antes de gerar qualquer guia
            num_destinos = len(destinos)
            for mat_id, q_un in itens_kit:
                mat_check = _lock_material(mat_id)
                qtd_total_necessaria = round(q_un * num_destinos, 4)
                if not mat_check or (mat_check.estoque_atual or 0.0) < qtd_total_necessaria - 1e-6:
                    raise ValueError(
                        f'Estoque insuficiente para distribuição em lote de "{mat_check.descricao if mat_check else mat_id}": '
                        f"necessário {formatar_qtd(qtd_total_necessaria)} ({num_destinos} unidades x {formatar_qtd(q_un)}), "
                        f"disponível {formatar_qtd(mat_check.estoque_atual if mat_check else 0)}."
                    )

            usuario_id = _obter_usuario_id_atual()
            lote_cod = f"DIST-{date.today().year}-{uuid.uuid4().hex[:5].upper()}"
            data_ent = _parse_date_opt(request.form.get("data_entrega")) or date.today()
            justificativa = (request.form.get("justificativa") or f"Distribuição programada em lote ({lote_cod})").strip()
            entregador_nome = (request.form.get("entregador_nome") or "").strip() or None
            status_inicial = request.form.get("status") or "Em Trânsito"

            guias_criadas = []
            for tipo_dest, esc_id, set_id, nome_snap in destinos:
                codigo_guia = gerar_codigo_sequencial("GUIA", AlmoxEntrega, "codigo_guia")
                entrega = AlmoxEntrega(
                    codigo_guia=codigo_guia,
                    codigo_autenticacao=gerar_codigo_autenticacao(),
                    tipo_saida="Distribuição em Lote",
                    lote_distribuicao_codigo=lote_cod,
                    tipo_destino=tipo_dest,
                    escola_id=esc_id,
                    setor_id=set_id,
                    destino_nome_snapshot=nome_snap,
                    data_entrega=data_ent,
                    entregador_nome=entregador_nome,
                    justificativa=justificativa,
                    status=status_inicial,
                    data_confirmacao_recebimento=datetime.utcnow() if status_inicial == "Entregue" else None,
                    usuario_emissor_id=usuario_id,
                )
                db.session.add(entrega)
                db.session.flush()

                v_total_guia = 0.0
                for mat_id, q_un in itens_kit:
                    mat = _lock_material(mat_id)
                    lote_id = _baixar_lotes_fefo(mat.id, q_un)
                    _, cm, v_tot = _registrar_movimento_saida(
                        material=mat,
                        quantidade_base=q_un,
                        tipo_movimento="Saída Distribuição Lote",
                        usuario_id=usuario_id,
                        documento_ref=codigo_guia,
                        justificativa=f"{justificativa} — {nome_snap}",
                        entrega_id=entrega.id,
                        lote_id=lote_id,
                        escola_id=esc_id,
                        setor_id=set_id,
                    )
                    db.session.add(
                        AlmoxEntregaItem(
                            entrega_id=entrega.id,
                            material_id=mat.id,
                            lote_id=lote_id,
                            quantidade=q_un,
                            custo_unitario_medio=cm,
                            valor_total=v_tot,
                        )
                    )
                    v_total_guia = round(v_total_guia + v_tot, 2)

                entrega.valor_total = v_total_guia
                guias_criadas.append(codigo_guia)

            db.session.commit()
            registrar_log(
                f"Almoxarifado: distribuição em lote {lote_cod} — {len(guias_criadas)} guias geradas ({justificativa})."
            )
            flash(
                f"Distribuição em lote {lote_cod} concluída! {len(guias_criadas)} Guia(s) de Entrega geradas com sucesso.",
                "success",
            )
            return redirect(url_for("almoxarifado.listar_entregas", busca=lote_cod))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro na distribuição em lote: {e}", "danger")

    materiais = (
        Material.query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None)), Material.estoque_atual > 0)
        .order_by(Material.descricao)
        .all()
    )
    escolas = Escola.query.order_by(Escola.nome).all()
    setores = AlmoxSetor.query.filter_by(ativo=True).order_by(AlmoxSetor.nome).all()
    return render_template(
        "almoxarifado/distribuicao_lote.html",
        materiais=materiais,
        escolas=escolas,
        setores=setores,
        hoje_iso=date.today().strftime("%Y-%m-%d"),
    )


@almoxarifado_bp.route("/entregas/baixa", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def baixa_tecnica():
    """Baixa técnica de materiais por avaria, vencimento, quebra ou inservibilidade."""
    if request.method == "POST":
        inv_bloq = verificar_bloqueio_inventario()
        if inv_bloq:
            flash(f"Baixas bloqueadas pelo Inventário {inv_bloq.codigo}.", "warning")
            return redirect(url_for("almoxarifado.listar_entregas"))
        try:
            motivo = (request.form.get("motivo_baixa") or "Avaria / Vencimento").strip()
            justificativa = (request.form.get("justificativa") or "").strip()
            num_proc = (request.form.get("numero_processo_baixa") or "").strip() or None
            if not justificativa:
                flash("A justificativa detalhada é obrigatória para baixa técnica.", "warning")
                return redirect(url_for("almoxarifado.baixa_tecnica"))

            material_ids = request.form.getlist("material_id[]")
            quantidades = request.form.getlist("quantidade[]")

            laudo_url = None
            arq = request.files.get("arquivo_laudo")
            if arq and arq.filename:
                laudo_url = upload_arquivo_para_nuvem(arq, pasta="almoxarifado/baixas")

            usuario_id = _obter_usuario_id_atual()
            codigo_guia = gerar_codigo_sequencial("BAIXA", AlmoxEntrega, "codigo_guia")
            entrega = AlmoxEntrega(
                codigo_guia=codigo_guia,
                codigo_autenticacao=gerar_codigo_autenticacao(),
                tipo_saida="Baixa Técnica",
                tipo_destino="Baixa Interna",
                destino_nome_snapshot=f"Baixa Técnica ({motivo})",
                data_entrega=_parse_date_opt(request.form.get("data_entrega")) or date.today(),
                numero_processo_baixa=num_proc,
                comprovante_assinado_url=laudo_url,
                justificativa=f"[{motivo}] {justificativa}",
                status="Entregue",
                usuario_emissor_id=usuario_id,
            )
            db.session.add(entrega)
            db.session.flush()

            total_val = 0.0
            qtd_it = 0
            for idx, m_id_raw in enumerate(material_ids):
                if not m_id_raw:
                    continue
                q = parse_float_br(quantidades[idx] if idx < len(quantidades) else 0, 0.0)
                if q <= 0:
                    continue
                mat = _lock_material(int(m_id_raw))
                lote_id = _baixar_lotes_fefo(mat.id, q)
                _, cm, v_tot = _registrar_movimento_saida(
                    material=mat,
                    quantidade_base=q,
                    tipo_movimento="Baixa Técnica",
                    usuario_id=usuario_id,
                    documento_ref=codigo_guia,
                    justificativa=f"Baixa Técnica ({motivo}): {justificativa}",
                    entrega_id=entrega.id,
                    lote_id=lote_id,
                )
                db.session.add(
                    AlmoxEntregaItem(
                        entrega_id=entrega.id,
                        material_id=mat.id,
                        lote_id=lote_id,
                        quantidade=q,
                        custo_unitario_medio=cm,
                        valor_total=v_tot,
                    )
                )
                total_val = round(total_val + v_tot, 2)
                qtd_it += 1

            if qtd_it == 0:
                db.session.rollback()
                flash("Informe pelo menos um material com quantidade válida.", "warning")
                return redirect(url_for("almoxarifado.baixa_tecnica"))

            entrega.valor_total = total_val
            db.session.commit()
            registrar_log(f"Almoxarifado: registrou {codigo_guia} (Baixa Técnica - {motivo}, {formatar_moeda_br(total_val)}).")
            flash(f"Termo de Baixa {codigo_guia} registrado com sucesso.", "success")
            return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar baixa técnica: {e}", "danger")

    materiais = (
        Material.query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None)), Material.estoque_atual > 0)
        .order_by(Material.descricao)
        .all()
    )
    return render_template("almoxarifado/baixa_tecnica.html", materiais=materiais, hoje_iso=date.today().strftime("%Y-%m-%d"))


@almoxarifado_bp.route("/entregas/devolucao", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def devolucao_unidade():
    """Registra devolução de materiais de uma Escola ou Setor da SEME retornando ao estoque."""
    if request.method == "POST":
        inv_bloq = verificar_bloqueio_inventario()
        if inv_bloq:
            flash(f"Movimentações bloqueadas pelo Inventário {inv_bloq.codigo}.", "warning")
            return redirect(url_for("almoxarifado.listar_entregas"))
        try:
            tipo_dest, esc_id, set_id, nome_snap = _resolver_destino(
                request.form.get("tipo_destino", "Escola"),
                request.form.get("escola_id", type=int),
                request.form.get("setor_id", type=int),
                request.form.get("destino_outro_descricao"),
            )
            justificativa = (request.form.get("justificativa") or "").strip()
            if not justificativa:
                flash("Informe o motivo da devolução.", "warning")
                return redirect(url_for("almoxarifado.devolucao_unidade"))

            material_ids = request.form.getlist("material_id[]")
            quantidades = request.form.getlist("quantidade[]")

            usuario_id = _obter_usuario_id_atual()
            codigo_guia = gerar_codigo_sequencial("DEV", AlmoxEntrega, "codigo_guia")
            entrega = AlmoxEntrega(
                codigo_guia=codigo_guia,
                codigo_autenticacao=gerar_codigo_autenticacao(),
                tipo_saida="Devolução de Unidade",
                tipo_destino=tipo_dest,
                escola_id=esc_id,
                setor_id=set_id,
                destino_nome_snapshot=nome_snap,
                data_entrega=_parse_date_opt(request.form.get("data_entrega")) or date.today(),
                recebedor_nome=(request.form.get("entregador_unidade") or "").strip() or None,
                justificativa=justificativa,
                status="Entregue",
                usuario_emissor_id=usuario_id,
            )
            db.session.add(entrega)
            db.session.flush()

            total_val = 0.0
            qtd_it = 0
            for idx, m_id_raw in enumerate(material_ids):
                if not m_id_raw:
                    continue
                q = parse_float_br(quantidades[idx] if idx < len(quantidades) else 0, 0.0)
                if q <= 0:
                    continue
                mat = _lock_material(int(m_id_raw))
                cm = mat.custo_referencia
                _registrar_movimento_entrada(
                    material=mat,
                    quantidade_base=q,
                    valor_unitario_base=cm,
                    tipo_movimento="Devolução Unidade",
                    usuario_id=usuario_id,
                    documento_ref=codigo_guia,
                    justificativa=f"Devolução de {nome_snap}: {justificativa}",
                    entrega_id=entrega.id,
                    escola_id=esc_id,
                    setor_id=set_id,
                )
                v_tot = round(q * cm, 2)
                db.session.add(
                    AlmoxEntregaItem(
                        entrega_id=entrega.id,
                        material_id=mat.id,
                        quantidade=q,
                        custo_unitario_medio=cm,
                        valor_total=v_tot,
                    )
                )
                total_val = round(total_val + v_tot, 2)
                qtd_it += 1

            if qtd_it == 0:
                db.session.rollback()
                flash("Informe pelo menos um item devolvido com quantidade válida.", "warning")
                return redirect(url_for("almoxarifado.devolucao_unidade"))

            entrega.valor_total = total_val
            db.session.commit()
            registrar_log(f'Almoxarifado: devolução {codigo_guia} da unidade "{nome_snap}" ({formatar_moeda_br(total_val)}).')
            flash(f"Devolução {codigo_guia} registrada e reincorporada ao estoque!", "success")
            return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao registrar devolução: {e}", "danger")

    materiais = Material.query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None))).order_by(Material.descricao).all()
    escolas = Escola.query.order_by(Escola.nome).all()
    setores = AlmoxSetor.query.filter_by(ativo=True).order_by(AlmoxSetor.nome).all()
    return render_template(
        "almoxarifado/devolucao_unidade.html",
        materiais=materiais,
        escolas=escolas,
        setores=setores,
        hoje_iso=date.today().strftime("%Y-%m-%d"),
    )


@almoxarifado_bp.route("/entregas/<int:entrega_id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def detalhes_entrega(entrega_id):
    entrega = (
        AlmoxEntrega.query.options(
            joinedload(AlmoxEntrega.escola),
            joinedload(AlmoxEntrega.setor),
            joinedload(AlmoxEntrega.requisicao),
            joinedload(AlmoxEntrega.usuario_emissor),
            joinedload(AlmoxEntrega.itens).joinedload(AlmoxEntregaItem.material),
            joinedload(AlmoxEntrega.itens).joinedload(AlmoxEntregaItem.lote),
        )
        .filter_by(id=entrega_id)
        .first_or_404()
    )

    if request.method == "POST":
        if session.get("role") not in ("admin", "almoxarifado"):
            flash("Apenas o Almoxarifado ou Administrador pode confirmar/editar o recebimento.", "danger")
            return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))
        try:
            entrega.recebedor_nome = (request.form.get("recebedor_nome") or entrega.recebedor_nome or "").strip() or None
            entrega.recebedor_cpf = (request.form.get("recebedor_cpf") or entrega.recebedor_cpf or "").strip() or None
            entrega.recebedor_cargo = (request.form.get("recebedor_cargo") or entrega.recebedor_cargo or "").strip() or None
            entrega.entregador_nome = (request.form.get("entregador_nome") or entrega.entregador_nome or "").strip() or None
            if request.form.get("confirmar_recebimento") == "1" and entrega.status != "Estornada":
                entrega.status = "Entregue"
                entrega.data_confirmacao_recebimento = datetime.utcnow()

            arq_guia = request.files.get("comprovante_assinado")
            if arq_guia and arq_guia.filename:
                url_nuvem = upload_arquivo_para_nuvem(arq_guia, pasta="almoxarifado/guias")
                if url_nuvem:
                    entrega.comprovante_assinado_url = url_nuvem

            db.session.commit()
            registrar_log(f"Almoxarifado: atualizou dados de recebimento da Guia {entrega.codigo_guia}.")
            flash("Dados de recebimento da Guia atualizados com sucesso!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao atualizar Guia: {e}", "danger")
        return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))

    return render_template("almoxarifado/detalhes_entrega.html", entrega=entrega, formatar_moeda_br=formatar_moeda_br)


@almoxarifado_bp.route("/entregas/<int:entrega_id>/pdf")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH", "operador")
def guia_entrega_pdf(entrega_id):
    entrega = (
        AlmoxEntrega.query.options(
            joinedload(AlmoxEntrega.escola),
            joinedload(AlmoxEntrega.setor),
            joinedload(AlmoxEntrega.requisicao),
            joinedload(AlmoxEntrega.usuario_emissor),
            joinedload(AlmoxEntrega.itens).joinedload(AlmoxEntregaItem.material),
            joinedload(AlmoxEntrega.itens).joinedload(AlmoxEntregaItem.lote),
        )
        .filter_by(id=entrega_id)
        .first_or_404()
    )
    pdf_buf = gerar_pdf_guia_entrega(entrega)
    return Response(
        pdf_buf.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline;filename=guia_{entrega.codigo_guia}.pdf"},
    )


@almoxarifado_bp.route("/entregas/<int:entrega_id>/estornar", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def estornar_entrega(entrega_id):
    entrega = AlmoxEntrega.query.get_or_404(entrega_id)
    if entrega.status == "Estornada":
        flash("Esta guia já foi estornada.", "warning")
        return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))

    motivo = (request.form.get("motivo_estorno") or "").strip()
    if not motivo:
        flash("O motivo do estorno é obrigatório.", "warning")
        return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))

    try:
        usuario_id = _obter_usuario_id_atual()
        eh_devolucao = entrega.tipo_saida == "Devolução de Unidade"

        for it in entrega.itens:
            mat = _lock_material(it.material_id)
            if eh_devolucao:
                # Estornar uma devolução significa subtrair novamente do estoque
                _registrar_movimento_saida(
                    material=mat,
                    quantidade_base=it.quantidade,
                    tipo_movimento="Estorno Devolução",
                    usuario_id=usuario_id,
                    documento_ref=entrega.codigo_guia,
                    justificativa=f"Estorno de devolução {entrega.codigo_guia}: {motivo}",
                    entrega_id=entrega.id,
                    escola_id=entrega.escola_id,
                    setor_id=entrega.setor_id,
                )
            else:
                # Estornar uma saída significa devolver o item ao estoque
                _registrar_movimento_entrada(
                    material=mat,
                    quantidade_base=it.quantidade,
                    valor_unitario_base=it.custo_unitario_medio or mat.custo_referencia,
                    tipo_movimento="Estorno Saída",
                    usuario_id=usuario_id,
                    documento_ref=entrega.codigo_guia,
                    justificativa=f"Estorno da guia {entrega.codigo_guia}: {motivo}",
                    entrega_id=entrega.id,
                    lote_id=it.lote_id,
                    escola_id=entrega.escola_id,
                    setor_id=entrega.setor_id,
                )
                if it.lote_id:
                    lote = AlmoxLote.query.get(it.lote_id)
                    if lote:
                        lote.quantidade_atual = round(lote.quantidade_atual + it.quantidade, 4)
                if it.requisicao_item_id:
                    req_it = RequisicaoItem.query.get(it.requisicao_item_id)
                    if req_it:
                        req_it.quantidade_atendida = max(0.0, round((req_it.quantidade_atendida or 0.0) - it.quantidade, 4))

        entrega.status = "Estornada"
        entrega.motivo_estorno = motivo
        entrega.data_estorno = datetime.utcnow()

        if entrega.requisicao:
            req = entrega.requisicao
            todos_zerados = all((ri.quantidade_atendida or 0.0) <= 0 for ri in req.itens)
            req.status = "Aprovada" if todos_zerados else "Atendida Parcialmente"

        db.session.commit()
        registrar_log(f"Almoxarifado: estornou Guia {entrega.codigo_guia}. Motivo: {motivo}")
        flash(f"Guia {entrega.codigo_guia} estornada e saldo recalculado com sucesso!", "info")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao estornar guia: {e}", "danger")
    return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))


# =============================================================================
# 5. REQUISIÇÕES INTERNAS (COM ESCOLA / SETOR SEME E ATENDIMENTO PARCIAL/TOTAL)
# =============================================================================

@almoxarifado_bp.route("/requisicoes")
@login_required
@role_required("admin", "almoxarifado", "operador", "RH", "Fiscal")
def minhas_requisicoes():
    status_filtro = request.args.get("status", "")
    tipo_destino = request.args.get("tipo_destino", "")

    query = Requisicao.query.options(
        joinedload(Requisicao.escola),
        joinedload(Requisicao.setor),
        joinedload(Requisicao.usuario_solicitante),
        joinedload(Requisicao.secretaria_solicitante),
    )
    if session.get("role") not in ("admin", "almoxarifado", "Fiscal"):
        query = query.filter(Requisicao.usuario_solicitante_id == _obter_usuario_id_atual())
    if status_filtro:
        query = query.filter(Requisicao.status == status_filtro)
    if tipo_destino:
        query = query.filter(Requisicao.tipo_destino == tipo_destino)

    requisicoes = query.order_by(Requisicao.data_solicitacao.desc()).limit(200).all()
    return render_template(
        "almoxarifado/minhas_requisicoes.html",
        requisicoes=requisicoes,
        status_filtro=status_filtro,
        tipo_destino=tipo_destino,
    )


@almoxarifado_bp.route("/requisicoes/nova", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado", "operador", "RH")
def nova_requisicao():
    if request.method == "POST":
        try:
            tipo_dest, esc_id, set_id, nome_snap = _resolver_destino(
                request.form.get("tipo_destino", "Escola"),
                request.form.get("escola_id", type=int),
                request.form.get("setor_id", type=int),
                request.form.get("destino_outro_descricao"),
            )
            justificativa = (request.form.get("justificativa") or "").strip()
            material_ids = request.form.getlist("material_id[]")
            quantidades = request.form.getlist("quantidade[]")

            if not material_ids:
                flash("Adicione pelo menos um item à requisição.", "warning")
                return redirect(url_for("almoxarifado.nova_requisicao"))

            sec_id = session.get("secretaria_id")
            if not sec_id:
                sec_padrao = Secretaria.query.first()
                sec_id = sec_padrao.id if sec_padrao else None

            num_req = gerar_codigo_sequencial("REQ", Requisicao, "numero_requisicao")
            nova_req = Requisicao(
                numero_requisicao=num_req,
                prioridade=request.form.get("prioridade", "Normal"),
                tipo_destino=tipo_dest,
                escola_id=esc_id,
                setor_id=set_id,
                destino_outro_descricao=nome_snap if tipo_dest == "Outro" else None,
                solicitante_nome_unidade=(request.form.get("solicitante_nome_unidade") or "").strip() or None,
                justificativa=justificativa,
                secretaria_solicitante_id=sec_id,
                usuario_solicitante_id=_obter_usuario_id_atual(),
                status="Pendente",
            )
            db.session.add(nova_req)
            db.session.flush()

            itens_ok = 0
            for idx, m_id_raw in enumerate(material_ids):
                if not m_id_raw:
                    continue
                q = parse_float_br(quantidades[idx] if idx < len(quantidades) else 0, 0.0)
                if q > 0:
                    db.session.add(
                        RequisicaoItem(
                            requisicao_id=nova_req.id,
                            material_id=int(m_id_raw),
                            quantidade_solicitada=q,
                            quantidade_atendida=0.0,
                        )
                    )
                    itens_ok += 1

            if itens_ok == 0:
                db.session.rollback()
                flash("Informe pelo menos um item com quantidade maior que zero.", "warning")
                return redirect(url_for("almoxarifado.nova_requisicao"))

            db.session.commit()
            registrar_log(f'Almoxarifado: abriu requisição {num_req} para "{nome_snap}" ({itens_ok} item(ns)).')
            flash(f"Requisição {num_req} enviada com sucesso!", "success")
            return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=nova_req.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao criar requisição: {e}", "danger")

    materiais = Material.query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None))).order_by(Material.descricao).all()
    escolas = Escola.query.order_by(Escola.nome).all()
    setores = AlmoxSetor.query.filter_by(ativo=True).order_by(AlmoxSetor.nome).all()
    return render_template(
        "almoxarifado/nova_requisicao.html",
        materiais=materiais,
        escolas=escolas,
        setores=setores,
    )


@almoxarifado_bp.route("/requisicoes/detalhes/<int:requisicao_id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado", "operador", "RH", "Fiscal")
def detalhes_requisicao(requisicao_id):
    requisicao = (
        Requisicao.query.options(
            joinedload(Requisicao.escola),
            joinedload(Requisicao.setor),
            joinedload(Requisicao.usuario_solicitante),
            joinedload(Requisicao.aprovador_usuario),
            joinedload(Requisicao.entregas),
        )
        .filter_by(id=requisicao_id)
        .first_or_404()
    )

    # Usuário comum só visualiza a própria requisição
    if session.get("role") not in ("admin", "almoxarifado", "Fiscal"):
        if requisicao.usuario_solicitante_id != _obter_usuario_id_atual():
            flash("Você não tem permissão para visualizar esta requisição.", "danger")
            return redirect(url_for("almoxarifado.minhas_requisicoes"))

    if request.method == "POST":
        if session.get("role") not in ("admin", "almoxarifado"):
            flash("Apenas o Almoxarifado ou Administrador pode atender requisições.", "danger")
            return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))

        # Proteção crítica contra dupla baixa ou atendimento de requisição não aprovada
        if requisicao.status not in ("Aprovada", "Em Separação", "Atendida Parcialmente"):
            flash(f'Requisição com status "{requisicao.status}" não pode ser atendida.', "warning")
            return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))

        inv_bloq = verificar_bloqueio_inventario()
        if inv_bloq:
            flash(f"Saídas bloqueadas pelo Inventário {inv_bloq.codigo}.", "warning")
            return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))

        try:
            item_ids = request.form.getlist("item_id[]")
            quantidades_atender = request.form.getlist("quantidade_atendida[]")
            atendimento_map = {}
            for i, raw_id in enumerate(item_ids):
                q = parse_float_br(quantidades_atender[i] if i < len(quantidades_atender) else 0, 0.0)
                if q < 0:
                    raise ValueError("Não é permitido informar quantidade negativa.")
                atendimento_map[int(raw_id)] = q

            usuario_id = _obter_usuario_id_atual()
            codigo_guia = gerar_codigo_sequencial("GUIA", AlmoxEntrega, "codigo_guia")
            status_guia = request.form.get("status_guia") or "Entregue"

            entrega = AlmoxEntrega(
                codigo_guia=codigo_guia,
                codigo_autenticacao=gerar_codigo_autenticacao(),
                tipo_saida="Requisição",
                requisicao_id=requisicao.id,
                tipo_destino=requisicao.tipo_destino or "Setor SEME",
                escola_id=requisicao.escola_id,
                setor_id=requisicao.setor_id,
                destino_nome_snapshot=requisicao.nome_destino_exibicao,
                data_entrega=_parse_date_opt(request.form.get("data_entrega")) or date.today(),
                entregador_nome=(request.form.get("entregador_nome") or "").strip() or None,
                recebedor_nome=(request.form.get("recebedor_nome") or requisicao.solicitante_nome_unidade or "").strip() or None,
                recebedor_cpf=(request.form.get("recebedor_cpf") or "").strip() or None,
                recebedor_cargo=(request.form.get("recebedor_cargo") or "").strip() or None,
                justificativa=f"Atendimento à {requisicao.codigo_exibicao}: {requisicao.justificativa or ''}",
                status=status_guia,
                data_confirmacao_recebimento=datetime.utcnow() if status_guia == "Entregue" else None,
                usuario_emissor_id=usuario_id,
            )
            db.session.add(entrega)
            db.session.flush()

            valor_total_guia = 0.0
            itens_atendidos_agora = 0

            for item in requisicao.itens:
                qtd_retirar = atendimento_map.get(item.id, 0.0)
                if qtd_retirar <= 0:
                    continue
                if qtd_retirar > item.saldo_pendente + 1e-6:
                    raise ValueError(
                        f'Quantidade informada para "{item.material.descricao}" ({formatar_qtd(qtd_retirar)}) '
                        f"excede o saldo pendente da requisição ({formatar_qtd(item.saldo_pendente)})."
                    )
                mat = _lock_material(item.material_id)
                lote_id = _baixar_lotes_fefo(mat.id, qtd_retirar)
                _, cm, v_tot = _registrar_movimento_saida(
                    material=mat,
                    quantidade_base=qtd_retirar,
                    tipo_movimento="Saída Requisição",
                    usuario_id=usuario_id,
                    documento_ref=codigo_guia,
                    justificativa=f"Atendimento {requisicao.codigo_exibicao} ({codigo_guia}) — {requisicao.nome_destino_exibicao}",
                    entrega_id=entrega.id,
                    requisicao_item_id=item.id,
                    lote_id=lote_id,
                    escola_id=requisicao.escola_id,
                    setor_id=requisicao.setor_id,
                )
                item.quantidade_atendida = round(float(item.quantidade_atendida or 0.0) + qtd_retirar, 4)
                db.session.add(
                    AlmoxEntregaItem(
                        entrega_id=entrega.id,
                        material_id=mat.id,
                        requisicao_item_id=item.id,
                        lote_id=lote_id,
                        quantidade=qtd_retirar,
                        custo_unitario_medio=cm,
                        valor_total=v_tot,
                    )
                )
                valor_total_guia = round(valor_total_guia + v_tot, 2)
                itens_atendidos_agora += 1

            if itens_atendidos_agora == 0:
                db.session.rollback()
                flash("Informe pelo menos um item com quantidade maior que zero para gerar a Guia de Entrega.", "warning")
                return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))

            entrega.valor_total = valor_total_guia

            encerrar_saldo = request.form.get("encerrar_saldo") == "1"
            tudo_atendido = all(it.saldo_pendente <= 1e-6 for it in requisicao.itens)
            requisicao.status = "Atendida" if (tudo_atendido or encerrar_saldo) else "Atendida Parcialmente"

            db.session.commit()
            registrar_log(
                f"Almoxarifado: atendeu {requisicao.codigo_exibicao} gerando Guia {codigo_guia} "
                f"({formatar_moeda_br(valor_total_guia)}). Status: {requisicao.status}."
            )
            flash(f"Guia de Entrega {codigo_guia} gerada com sucesso! Status da requisição: {requisicao.status}.", "success")
            return redirect(url_for("almoxarifado.detalhes_entrega", entrega_id=entrega.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao atender requisição: {e}", "danger")

    return render_template(
        "almoxarifado/detalhes_requisicao.html",
        requisicao=requisicao,
        hoje_iso=date.today().strftime("%Y-%m-%d"),
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/requisicoes/avaliar/<int:requisicao_id>", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def avaliar_requisicao(requisicao_id):
    requisicao = Requisicao.query.get_or_404(requisicao_id)
    acao = request.form.get("acao")
    parecer = (request.form.get("parecer_aprovacao") or "").strip()

    if acao in ("Aprovar", "Recusar") and requisicao.status != "Pendente":
        flash("Esta requisição já foi avaliada.", "warning")
        return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))

    if acao == "Aprovar":
        requisicao.status = "Aprovada"
        requisicao.aprovador_usuario_id = _obter_usuario_id_atual()
        requisicao.data_aprovacao = datetime.utcnow()
        requisicao.parecer_aprovacao = parecer or "Aprovada pelo Almoxarifado."
        flash("Requisição APROVADA com sucesso.", "info")
    elif acao == "Recusar":
        if not parecer:
            flash("Informe o parecer / justificativa para recusar a requisição.", "warning")
            return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))
        requisicao.status = "Recusada"
        requisicao.aprovador_usuario_id = _obter_usuario_id_atual()
        requisicao.data_aprovacao = datetime.utcnow()
        requisicao.parecer_aprovacao = parecer
        flash("Requisição RECUSADA.", "warning")
    elif acao == "Cancelar" and requisicao.status in ("Pendente", "Aprovada"):
        requisicao.status = "Cancelada"
        requisicao.parecer_aprovacao = parecer or "Cancelada."
        flash("Requisição CANCELADA.", "secondary")
    else:
        flash("Ação inválida.", "danger")
        return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))

    try:
        db.session.commit()
        registrar_log(f"Almoxarifado: alterou status da {requisicao.codigo_exibicao} para {requisicao.status}.")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao atualizar status: {e}", "danger")
    return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))


@almoxarifado_bp.route("/requisicoes/comprovante/<int:requisicao_id>")
@login_required
@role_required("admin", "almoxarifado", "operador", "RH", "Fiscal")
def comprovante_requisicao_pdf(requisicao_id):
    requisicao = Requisicao.query.get_or_404(requisicao_id)
    ultima_guia = (
        AlmoxEntrega.query.filter_by(requisicao_id=requisicao.id)
        .filter(AlmoxEntrega.status != "Estornada")
        .order_by(AlmoxEntrega.id.desc())
        .first()
    )
    if ultima_guia:
        return redirect(url_for("almoxarifado.guia_entrega_pdf", entrega_id=ultima_guia.id))
    flash("Nenhuma Guia de Entrega ativa encontrada para esta requisição.", "warning")
    return redirect(url_for("almoxarifado.detalhes_requisicao", requisicao_id=requisicao.id))


# =============================================================================
# 6. INVENTÁRIO FORMAL (COMISSÃO, PORTARIA SEME, CONTAGEM CEGA E ATA PDF)
# =============================================================================

@almoxarifado_bp.route("/inventario")
@login_required
@role_required("admin", "almoxarifado")
def iniciar_inventario():
    return redirect(url_for("almoxarifado.listar_inventarios"))


@almoxarifado_bp.route("/inventarios")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def listar_inventarios():
    inventarios = (
        AlmoxInventario.query.options(
            joinedload(AlmoxInventario.categoria),
            joinedload(AlmoxInventario.usuario_abertura),
            joinedload(AlmoxInventario.usuario_aprovador),
        )
        .order_by(AlmoxInventario.data_abertura.desc())
        .all()
    )
    return render_template(
        "almoxarifado/inventarios.html",
        inventarios=inventarios,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/inventarios/novo", methods=["GET", "POST"])
@login_required
@role_required("admin", "almoxarifado")
def novo_inventario():
    if request.method == "POST":
        try:
            tipo = (request.form.get("tipo") or "Geral Anual").strip()
            exercicio = request.form.get("exercicio", date.today().year, type=int)
            categoria_id = request.form.get("categoria_id", type=int)
            portaria = (request.form.get("portaria_comissao") or "").strip() or None
            presidente = (request.form.get("presidente_comissao") or "").strip() or None
            membros = (request.form.get("membros_comissao") or "").strip() or None
            justificativa = (request.form.get("justificativa_geral") or "").strip()
            contagem_cega = bool(request.form.get("contagem_cega"))
            bloquear_mov = bool(request.form.get("bloquear_movimentacoes"))

            query_mats = Material.query.filter(or_(Material.ativo.is_(True), Material.ativo.is_(None)))
            if categoria_id:
                query_mats = query_mats.filter(Material.categoria_id == categoria_id)
            materiais = query_mats.order_by(Material.localizacao_fisica, Material.descricao).all()

            if not materiais:
                flash("Nenhum material ativo encontrado no escopo selecionado.", "warning")
                return redirect(url_for("almoxarifado.novo_inventario"))

            codigo = gerar_codigo_sequencial("INV", AlmoxInventario, "codigo")
            inv = AlmoxInventario(
                codigo=codigo,
                exercicio=exercicio,
                tipo=tipo,
                categoria_id=categoria_id,
                portaria_comissao=portaria,
                presidente_comissao=presidente,
                membros_comissao=membros,
                contagem_cega=contagem_cega,
                bloquear_movimentacoes=bloquear_mov,
                status="Aberto",
                justificativa_geral=justificativa or f"Inventário {tipo} - Exercício {exercicio}",
                usuario_abertura_id=_obter_usuario_id_atual(),
            )
            db.session.add(inv)
            db.session.flush()

            valor_sist = 0.0
            for mat in materiais:
                saldo_m = float(mat.estoque_atual or 0.0)
                cm_m = mat.custo_referencia
                valor_sist += saldo_m * cm_m
                db.session.add(
                    AlmoxInventarioItem(
                        inventario_id=inv.id,
                        material_id=mat.id,
                        estoque_sistema_abertura=saldo_m,
                        custo_medio_abertura=cm_m,
                        conferido=False,
                    )
                )

            inv.valor_estoque_sistema = round(valor_sist, 2)
            db.session.commit()
            registrar_log(f"Almoxarifado: abriu Inventário {codigo} ({tipo}, {len(materiais)} itens, contagem cega={contagem_cega}).")
            flash(f"Inventário {codigo} aberto com sucesso com {len(materiais)} item(ns) congelados!", "success")
            return redirect(url_for("almoxarifado.detalhes_inventario", inv_id=inv.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao abrir inventário: {e}", "danger")

    categorias = AlmoxCategoria.query.filter_by(ativo=True).order_by(AlmoxCategoria.nome).all()
    return render_template("almoxarifado/novo_inventario.html", categorias=categorias, ano_atual=date.today().year)


@almoxarifado_bp.route("/inventarios/<int:inv_id>")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def detalhes_inventario(inv_id):
    inv = (
        AlmoxInventario.query.options(
            joinedload(AlmoxInventario.categoria),
            joinedload(AlmoxInventario.usuario_abertura),
            joinedload(AlmoxInventario.usuario_aprovador),
            joinedload(AlmoxInventario.itens).joinedload(AlmoxInventarioItem.material).joinedload(Material.categoria_rel),
        )
        .filter_by(id=inv_id)
        .first_or_404()
    )
    itens_ordenados = sorted(
        inv.itens,
        key=lambda x: ((x.material.localizacao_fisica or "ZZZ"), (x.material.descricao if x.material else "")),
    )
    apenas_divergentes = request.args.get("divergentes") == "1"
    if apenas_divergentes:
        itens_ordenados = [it for it in itens_ordenados if abs(it.diferenca or 0.0) > 1e-6]

    contados = sum(1 for it in inv.itens if it.quantidade_contada_1 is not None)
    divergentes = sum(1 for it in inv.itens if it.quantidade_contada_1 is not None and abs(it.diferenca or 0.0) > 1e-6)
    return render_template(
        "almoxarifado/inventario_detalhes.html",
        inv=inv,
        itens=itens_ordenados,
        total_itens=len(inv.itens),
        contados=contados,
        divergentes=divergentes,
        apenas_divergentes=apenas_divergentes,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/inventarios/<int:inv_id>/salvar-contagem", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def salvar_contagem_inventario(inv_id):
    inv = AlmoxInventario.query.get_or_404(inv_id)
    if inv.status not in ("Aberto", "Em Conferência"):
        flash("Este inventário não permite mais alterações de contagem.", "warning")
        return redirect(url_for("almoxarifado.detalhes_inventario", inv_id=inv.id))

    try:
        item_ids = request.form.getlist("inv_item_id[]")
        qtds_1 = request.form.getlist("qtd_1[]")
        qtds_2 = request.form.getlist("qtd_2[]")
        justificativas = request.form.getlist("justificativa_item[]")

        itens_map = {it.id: it for it in inv.itens}
        salvos = 0
        for idx, raw_id in enumerate(item_ids):
            it = itens_map.get(int(raw_id))
            if not it:
                continue
            raw_q1 = (qtds_1[idx] if idx < len(qtds_1) else "").strip()
            raw_q2 = (qtds_2[idx] if idx < len(qtds_2) else "").strip()
            just = (justificativas[idx] if idx < len(justificativas) else "").strip()

            if raw_q1 != "":
                q1 = parse_float_br(raw_q1, 0.0)
                if q1 < 0:
                    q1 = 0.0
                it.quantidade_contada_1 = q1
                salvos += 1
            if raw_q2 != "":
                q2 = parse_float_br(raw_q2, 0.0)
                if q2 < 0:
                    q2 = 0.0
                it.quantidade_contada_2 = q2

            if just:
                it.justificativa_item = just

            # Quantidade final prioriza 2ª contagem (recontagem) quando preenchida
            qtd_final = it.quantidade_contada_2 if it.quantidade_contada_2 is not None else it.quantidade_contada_1
            if qtd_final is not None:
                it.quantidade_final = qtd_final
                it.diferenca = round(qtd_final - it.estoque_sistema_abertura, 4)
                it.valor_diferenca = round(it.diferenca * it.custo_medio_abertura, 2)
                it.conferido = True

        # Recalcula totais do inventário
        inv.total_itens_contados = sum(1 for it in inv.itens if it.quantidade_final is not None)
        inv.total_divergencias = sum(1 for it in inv.itens if it.quantidade_final is not None and abs(it.diferenca or 0.0) > 1e-6)
        inv.valor_estoque_fisico = round(
            sum(
                (it.quantidade_final if it.quantidade_final is not None else it.estoque_sistema_abertura) * it.custo_medio_abertura
                for it in inv.itens
            ),
            2,
        )
        inv.valor_diferenca_liquida = round(inv.valor_estoque_fisico - inv.valor_estoque_sistema, 2)

        if request.form.get("avancar_conferencia") == "1":
            inv.status = "Em Conferência"
            flash(f"Contagem salva ({salvos} itens). Inventário avançado para CONFERÊNCIA DE DIVERGÊNCIAS!", "info")
        else:
            flash(f"Rascunho da contagem salvo com sucesso ({salvos} itens preenchidos).", "success")

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao salvar contagem: {e}", "danger")
    return redirect(url_for("almoxarifado.detalhes_inventario", inv_id=inv.id))


@almoxarifado_bp.route("/inventarios/<int:inv_id>/finalizar", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def finalizar_inventario(inv_id):
    inv = AlmoxInventario.query.get_or_404(inv_id)
    if inv.status == "Finalizado":
        flash("Este inventário já foi finalizado.", "warning")
        return redirect(url_for("almoxarifado.detalhes_inventario", inv_id=inv.id))

    try:
        obs_conc = (request.form.get("observacoes_conclusao") or "").strip()
        usuario_id = _obter_usuario_id_atual()
        ajustes = 0

        for it in inv.itens:
            if it.quantidade_final is None:
                continue
            mat = _lock_material(it.material_id)
            saldo_ant = float(mat.estoque_atual or 0.0)
            dif_real = round(it.quantidade_final - saldo_ant, 4)
            it.diferenca = round(it.quantidade_final - it.estoque_sistema_abertura, 4)
            it.valor_diferenca = round(it.diferenca * it.custo_medio_abertura, 2)

            if abs(dif_real) > 1e-6:
                ajustes += 1
                mat.estoque_atual = it.quantidade_final
                cm = mat.custo_referencia or it.custo_medio_abertura
                tipo_mov = "Ajuste Inventário (+)" if dif_real > 0 else "Ajuste Inventário (-)"
                db.session.add(
                    MovimentoEstoque(
                        material_id=mat.id,
                        tipo_movimento=tipo_mov,
                        quantidade=dif_real,
                        valor_unitario=cm,
                        valor_total=round(abs(dif_real) * cm, 2),
                        saldo_anterior=saldo_ant,
                        saldo_apos=it.quantidade_final,
                        custo_medio_apos=cm,
                        documento_ref=inv.codigo,
                        justificativa=it.justificativa_item or inv.justificativa_geral or f"Ajuste Inventário {inv.codigo}",
                        usuario_id=usuario_id,
                        inventario_id=inv.id,
                    )
                )

        inv.status = "Finalizado"
        inv.bloquear_movimentacoes = False
        inv.data_fechamento = datetime.utcnow()
        inv.usuario_aprovador_id = usuario_id
        inv.observacoes_conclusao = obs_conc or inv.observacoes_conclusao
        inv.total_itens_contados = sum(1 for it in inv.itens if it.quantidade_final is not None)
        inv.total_divergencias = sum(1 for it in inv.itens if it.quantidade_final is not None and abs(it.diferenca or 0.0) > 1e-6)
        inv.valor_estoque_fisico = round(
            sum(
                (it.quantidade_final if it.quantidade_final is not None else it.estoque_sistema_abertura) * it.custo_medio_abertura
                for it in inv.itens
            ),
            2,
        )
        inv.valor_diferenca_liquida = round(inv.valor_estoque_fisico - inv.valor_estoque_sistema, 2)

        db.session.commit()
        registrar_log(
            f"Almoxarifado: finalizou Inventário {inv.codigo} ({ajustes} ajuste(s), "
            f"diferença líquida {formatar_moeda_br(inv.valor_diferenca_liquida)})."
        )
        flash(f"Inventário {inv.codigo} FINALIZADO! {ajustes} item(ns) tiveram o estoque ajustado e registrado no Kardex.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao finalizar inventário: {e}", "danger")
    return redirect(url_for("almoxarifado.detalhes_inventario", inv_id=inv.id))


@almoxarifado_bp.route("/inventarios/<int:inv_id>/cancelar", methods=["POST"])
@login_required
@role_required("admin", "almoxarifado")
def cancelar_inventario(inv_id):
    inv = AlmoxInventario.query.get_or_404(inv_id)
    if inv.status == "Finalizado":
        flash("Inventários já finalizados não podem ser cancelados.", "danger")
        return redirect(url_for("almoxarifado.detalhes_inventario", inv_id=inv.id))
    inv.status = "Cancelado"
    inv.bloquear_movimentacoes = False
    db.session.commit()
    registrar_log(f"Almoxarifado: cancelou Inventário {inv.codigo}.")
    flash(f"Inventário {inv.codigo} cancelado.", "info")
    return redirect(url_for("almoxarifado.listar_inventarios"))


@almoxarifado_bp.route("/inventarios/<int:inv_id>/ata-pdf")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def inventario_ata_pdf(inv_id):
    inv = (
        AlmoxInventario.query.options(
            joinedload(AlmoxInventario.itens).joinedload(AlmoxInventarioItem.material),
        )
        .filter_by(id=inv_id)
        .first_or_404()
    )
    pdf_buf = gerar_pdf_ata_inventario(inv)
    return Response(
        pdf_buf.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline;filename=ata_inventario_{inv.codigo}.pdf"},
    )


@almoxarifado_bp.route("/inventarios/<int:inv_id>/excel")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def inventario_excel(inv_id):
    inv = (
        AlmoxInventario.query.options(joinedload(AlmoxInventario.itens).joinedload(AlmoxInventarioItem.material))
        .filter_by(id=inv_id)
        .first_or_404()
    )
    cab = ["Código", "Material", "Unidade", "Saldo Sistema", "Qtd. Física", "Diferença", "Custo Médio", "Impacto (R$)", "Justificativa"]
    linhas = []
    for it in sorted(inv.itens, key=lambda x: x.material.descricao if x.material else ""):
        linhas.append(
            [
                it.material.codigo_interno or it.material_id,
                it.material.descricao,
                it.material.unidade_medida,
                it.estoque_sistema_abertura,
                it.quantidade_final if it.quantidade_final is not None else "",
                it.diferenca,
                it.custo_medio_abertura,
                it.valor_diferenca,
                it.justificativa_item or "",
            ]
        )
    buf = gerar_excel_almoxarifado(
        f"Inventário {inv.codigo} - Exercício {inv.exercicio}",
        f"Tipo: {inv.tipo} | Portaria: {inv.portaria_comissao or 'N/I'} | Status: {inv.status}",
        cab,
        linhas,
        formatos_colunas={3: "qtd", 4: "qtd", 5: "qtd", 6: "moeda", 7: "moeda"},
    )
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment;filename=inventario_{inv.codigo}.xlsx"},
    )


# =============================================================================
# 7. CENTRAL DE RELATÓRIOS (ENTREGAS GERAL, ESCOLAS, SETORES SEME, RMA, KARDEX)
# =============================================================================

@almoxarifado_bp.route("/relatorios")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def central_relatorios():
    return render_template("almoxarifado/central_relatorios.html")


@almoxarifado_bp.route("/relatorios/entregas-geral")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_entregas_geral():
    data_inicio = request.args.get("data_inicio") or date.today().replace(day=1).strftime("%Y-%m-%d")
    data_fim = request.args.get("data_fim") or date.today().strftime("%Y-%m-%d")
    tipo_destino = request.args.get("tipo_destino", "")
    tipo_saida = request.args.get("tipo_saida", "")
    categoria_id = request.args.get("categoria_id", type=int)
    material_id = request.args.get("material_id", type=int)
    formato = request.args.get("formato", "html")

    d_ini = _parse_date_opt(data_inicio)
    d_fim = _parse_date_opt(data_fim)

    query = (
        db.session.query(AlmoxEntregaItem, AlmoxEntrega, Material)
        .join(AlmoxEntrega, AlmoxEntregaItem.entrega_id == AlmoxEntrega.id)
        .join(Material, AlmoxEntregaItem.material_id == Material.id)
        .filter(
            AlmoxEntrega.status != "Estornada",
            AlmoxEntrega.tipo_saida.in_(["Requisição", "Entrega Direta", "Distribuição em Lote"]),
        )
    )
    if d_ini:
        query = query.filter(AlmoxEntrega.data_entrega >= d_ini)
    if d_fim:
        query = query.filter(AlmoxEntrega.data_entrega <= d_fim)
    if tipo_destino:
        query = query.filter(AlmoxEntrega.tipo_destino == tipo_destino)
    if tipo_saida:
        query = query.filter(AlmoxEntrega.tipo_saida == tipo_saida)
    if categoria_id:
        query = query.filter(Material.categoria_id == categoria_id)
    if material_id:
        query = query.filter(Material.id == material_id)

    registros = query.order_by(AlmoxEntrega.data_entrega.desc(), AlmoxEntrega.id.desc()).all()

    guias_distintas = set()
    total_valor = 0.0
    total_qtd = 0.0
    por_tipo_destino = defaultdict(lambda: {"guias": set(), "valor": 0.0, "itens": 0})
    linhas_detalhe = []

    for item, ent, mat in registros:
        guias_distintas.add(ent.id)
        val = float(item.valor_total or 0.0)
        total_valor += val
        total_qtd += float(item.quantidade or 0.0)
        td = ent.tipo_destino or "Outro"
        por_tipo_destino[td]["guias"].add(ent.id)
        por_tipo_destino[td]["valor"] += val
        por_tipo_destino[td]["itens"] += 1
        linhas_detalhe.append(
            {
                "data": ent.data_entrega,
                "guia": ent.codigo_guia,
                "entrega_id": ent.id,
                "tipo_saida": ent.tipo_saida,
                "tipo_destino": td,
                "destino": ent.nome_destino_exibicao,
                "material": mat.descricao,
                "categoria": mat.nome_categoria_exibicao,
                "unidade": mat.unidade_medida,
                "quantidade": item.quantidade,
                "custo_unit": item.custo_unitario_medio or 0.0,
                "valor_total": val,
                "recebedor": ent.recebedor_nome or "-",
            }
        )

    subtitulo = f"Período: {d_ini.strftime('%d/%m/%Y') if d_ini else '-'} a {d_fim.strftime('%d/%m/%Y') if d_fim else '-'}" + (
        f" | Destino: {tipo_destino}" if tipo_destino else " | Todas as Unidades (Escolas e Setores SEME)"
    )

    if formato == "pdf":
        cab = ["Data", "Guia", "Tipo", "Destino (Escola / Setor SEME)", "Material", "Qtd.", "Vlr. Unit.", "Total (R$)"]
        rows_pdf = [
            [
                r["data"].strftime("%d/%m/%Y"),
                r["guia"],
                r["tipo_destino"],
                r["destino"],
                f"{r['material']} ({r['unidade']})",
                formatar_qtd(r["quantidade"]),
                formatar_moeda_br(r["custo_unit"]),
                formatar_moeda_br(r["valor_total"]),
            ]
            for r in linhas_detalhe
        ]
        kpis = [
            ("Guias Emitidas", str(len(guias_distintas))),
            ("Itens Entregues", str(len(linhas_detalhe))),
            ("Escolas (R$)", formatar_moeda_br(por_tipo_destino["Escola"]["valor"])),
            ("Setores SEME (R$)", formatar_moeda_br(por_tipo_destino["Setor SEME"]["valor"])),
            ("Valor Total Geral", formatar_moeda_br(total_valor)),
        ]
        buf = gerar_pdf_relatorio_tabela(
            "RELATÓRIO GERAL DE ENTREGAS DO ALMOXARIFADO",
            subtitulo,
            cab,
            rows_pdf,
            col_widths_cm=[2.0, 2.7, 2.3, 6.4, 7.2, 1.8, 2.3, 2.6],
            paisagem=True,
            kpis=kpis,
            rodape_totais=["", "TOTAL GERAL", "", f"{len(guias_distintas)} guia(s)", f"{len(linhas_detalhe)} lançamento(s)", formatar_qtd(total_qtd), "", formatar_moeda_br(total_valor)],
        )
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": "inline;filename=relatorio_entregas_geral.pdf"})

    if formato == "excel":
        cab = ["Data", "Código Guia", "Modalidade", "Tipo Destino", "Destino", "Material", "Categoria", "Unidade", "Quantidade", "Custo Médio", "Valor Total", "Recebedor"]
        rows_x = [
            [r["data"].strftime("%d/%m/%Y"), r["guia"], r["tipo_saida"], r["tipo_destino"], r["destino"], r["material"], r["categoria"], r["unidade"], r["quantidade"], r["custo_unit"], r["valor_total"], r["recebedor"]]
            for r in linhas_detalhe
        ]
        buf = gerar_excel_almoxarifado("Relatório Geral de Entregas", subtitulo, cab, rows_x, {8: "qtd", 9: "moeda", 10: "moeda"})
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment;filename=relatorio_entregas_geral.xlsx"},
        )

    categorias = AlmoxCategoria.query.order_by(AlmoxCategoria.nome).all()
    materiais = Material.query.order_by(Material.descricao).all()
    return render_template(
        "almoxarifado/relatorio_entregas_geral.html",
        linhas=linhas_detalhe,
        total_valor=round(total_valor, 2),
        total_guias=len(guias_distintas),
        valor_escolas=round(por_tipo_destino["Escola"]["valor"], 2),
        valor_setores=round(por_tipo_destino["Setor SEME"]["valor"], 2),
        data_inicio=data_inicio,
        data_fim=data_fim,
        tipo_destino=tipo_destino,
        tipo_saida=tipo_saida,
        categoria_id=categoria_id,
        material_id=material_id,
        categorias=categorias,
        materiais=materiais,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/relatorios/entregas-escolas")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_entregas_escolas():
    """Relatório de Entregas por Escola: consolidado (com Ranking + Custo por Aluno) ou extrato individual."""
    data_inicio = request.args.get("data_inicio") or date(date.today().year, 1, 1).strftime("%Y-%m-%d")
    data_fim = request.args.get("data_fim") or date.today().strftime("%Y-%m-%d")
    escola_id = request.args.get("escola_id", type=int)
    zona = (request.args.get("zona") or "").strip()
    categoria_id = request.args.get("categoria_id", type=int)
    formato = request.args.get("formato", "html")

    d_ini = _parse_date_opt(data_inicio)
    d_fim = _parse_date_opt(data_fim)

    # Contagem de alunos matriculados ativos por escola (integração com Módulo Acadêmico)
    alunos_por_escola = {}
    try:
        ano_ref = d_fim.year if d_fim else date.today().year
        rows_al = (
            db.session.query(AcadTurma.escola_id, func.count(AcadMatricula.id))
            .join(AcadMatricula, AcadMatricula.turma_id == AcadTurma.id)
            .filter(AcadTurma.ano_letivo == ano_ref, AcadMatricula.status == "Cursando")
            .group_by(AcadTurma.escola_id)
            .all()
        )
        alunos_por_escola = {eid: int(cnt) for eid, cnt in rows_al}
    except Exception:
        alunos_por_escola = {}

    query = (
        db.session.query(AlmoxEntregaItem, AlmoxEntrega, Material, Escola)
        .join(AlmoxEntrega, AlmoxEntregaItem.entrega_id == AlmoxEntrega.id)
        .join(Material, AlmoxEntregaItem.material_id == Material.id)
        .join(Escola, AlmoxEntrega.escola_id == Escola.id)
        .filter(
            AlmoxEntrega.status != "Estornada",
            AlmoxEntrega.tipo_saida.in_(["Requisição", "Entrega Direta", "Distribuição em Lote"]),
        )
    )
    if d_ini:
        query = query.filter(AlmoxEntrega.data_entrega >= d_ini)
    if d_fim:
        query = query.filter(AlmoxEntrega.data_entrega <= d_fim)
    if escola_id:
        query = query.filter(Escola.id == escola_id)
    if zona:
        query = query.filter(Escola.zona.ilike(f"%{zona}%"))
    if categoria_id:
        query = query.filter(Material.categoria_id == categoria_id)

    registros = query.order_by(Escola.nome.asc(), AlmoxEntrega.data_entrega.desc()).all()

    # Agrupamento consolidado por escola
    mapa_escolas = {}
    detalhes = []
    for item, ent, mat, esc in registros:
        val = float(item.valor_total or 0.0)
        if esc.id not in mapa_escolas:
            qtd_al = alunos_por_escola.get(esc.id, 0)
            mapa_escolas[esc.id] = {
                "escola_id": esc.id,
                "escola": esc.nome,
                "inep": esc.codigo_inep or "-",
                "zona": esc.zona or "Urbana",
                "diretor": esc.diretor_responsavel or "-",
                "guias": set(),
                "total_itens": 0,
                "total_qtd": 0.0,
                "valor_total": 0.0,
                "alunos": qtd_al,
            }
        m = mapa_escolas[esc.id]
        m["guias"].add(ent.id)
        m["total_itens"] += 1
        m["total_qtd"] += float(item.quantidade or 0.0)
        m["valor_total"] += val

        detalhes.append(
            {
                "data": ent.data_entrega,
                "guia": ent.codigo_guia,
                "entrega_id": ent.id,
                "escola": esc.nome,
                "inep": esc.codigo_inep or "-",
                "zona": esc.zona or "Urbana",
                "material": mat.descricao,
                "categoria": mat.nome_categoria_exibicao,
                "unidade": mat.unidade_medida,
                "quantidade": item.quantidade,
                "custo_unit": item.custo_unitario_medio or 0.0,
                "valor_total": val,
                "recebedor": ent.recebedor_nome or "-",
            }
        )

    resumo_escolas = sorted(mapa_escolas.values(), key=lambda x: x["valor_total"], reverse=True)
    for r in resumo_escolas:
        r["qtd_guias"] = len(r["guias"])
        r["custo_por_aluno"] = round(r["valor_total"] / r["alunos"], 2) if r["alunos"] > 0 else 0.0

    total_geral = round(sum(r["valor_total"] for r in resumo_escolas), 2)
    escola_selecionada = Escola.query.get(escola_id) if escola_id else None
    subtitulo = (
        f"Escola: {escola_selecionada.nome} (INEP: {escola_selecionada.codigo_inep or 'N/I'}) | "
        if escola_selecionada
        else f"Consolidado da Rede Municipal ({len(resumo_escolas)} escola(s) atendida(s)) | "
    ) + f"Período: {d_ini.strftime('%d/%m/%Y') if d_ini else '-'} a {d_fim.strftime('%d/%m/%Y') if d_fim else '-'}"

    if formato == "pdf":
        if escola_selecionada:
            cab = ["Data", "Guia", "Material", "Categoria", "Qtd.", "Unid.", "Custo Méd.", "Total (R$)", "Recebedor"]
            rows_pdf = [
                [d["data"].strftime("%d/%m/%Y"), d["guia"], d["material"], d["categoria"], formatar_qtd(d["quantidade"]), d["unidade"], formatar_moeda_br(d["custo_unit"]), formatar_moeda_br(d["valor_total"]), d["recebedor"]]
                for d in detalhes
            ]
            buf = gerar_pdf_relatorio_tabela(
                f"EXTRATO DE ENTREGAS — {escola_selecionada.nome.upper()}",
                subtitulo,
                cab,
                rows_pdf,
                col_widths_cm=[2.0, 2.7, 6.8, 4.0, 1.6, 1.3, 2.3, 2.5, 4.1],
                paisagem=True,
                kpis=[
                    ("Guias Entregues", str(resumo_escolas[0]["qtd_guias"] if resumo_escolas else 0)),
                    ("Itens Entregues", str(len(detalhes))),
                    ("Alunos Ativos", str(alunos_por_escola.get(escola_selecionada.id, 0))),
                    ("Valor Total Recebido", formatar_moeda_br(total_geral)),
                ],
                rodape_totais=["", "TOTAL", f"{len(detalhes)} lançamento(s)", "", "", "", "", formatar_moeda_br(total_geral), ""],
            )
        else:
            cab = ["Escola Municipal", "INEP", "Zona", "Guias", "Itens", "Alunos", "Valor Total (R$)", "Custo/Aluno"]
            rows_pdf = [
                [r["escola"], r["inep"], r["zona"], str(r["qtd_guias"]), str(r["total_itens"]), str(r["alunos"] or "-"), formatar_moeda_br(r["valor_total"]), formatar_moeda_br(r["custo_por_aluno"]) if r["alunos"] else "-"]
                for r in resumo_escolas
            ]
            buf = gerar_pdf_relatorio_tabela(
                "RELATÓRIO DE ENTREGAS POR ESCOLA — REDE MUNICIPAL (SEME)",
                subtitulo,
                cab,
                rows_pdf,
                col_widths_cm=[9.0, 2.5, 2.2, 1.8, 1.8, 2.0, 4.0, 4.0],
                paisagem=True,
                kpis=[
                    ("Escolas Atendidas", str(len(resumo_escolas))),
                    ("Total de Guias", str(sum(r["qtd_guias"] for r in resumo_escolas))),
                    ("Valor Total Distribuído", formatar_moeda_br(total_geral)),
                ],
                rodape_totais=["TOTAL REDE MUNICIPAL", "", "", str(sum(r["qtd_guias"] for r in resumo_escolas)), str(sum(r["total_itens"] for r in resumo_escolas)), str(sum(r["alunos"] for r in resumo_escolas)), formatar_moeda_br(total_geral), ""],
            )
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": "inline;filename=relatorio_entregas_escolas.pdf"})

    if formato == "excel":
        if escola_selecionada:
            cab = ["Data", "Guia", "Escola", "Material", "Categoria", "Unidade", "Quantidade", "Custo Médio", "Valor Total", "Recebedor"]
            rows_x = [[d["data"].strftime("%d/%m/%Y"), d["guia"], d["escola"], d["material"], d["categoria"], d["unidade"], d["quantidade"], d["custo_unit"], d["valor_total"], d["recebedor"]] for d in detalhes]
            buf = gerar_excel_almoxarifado(f"Entregas - {escola_selecionada.nome}", subtitulo, cab, rows_x, {6: "qtd", 7: "moeda", 8: "moeda"})
        else:
            cab = ["Escola Municipal", "INEP", "Zona", "Diretor", "Guias", "Itens", "Alunos Ativos", "Valor Total (R$)", "Custo por Aluno"]
            rows_x = [[r["escola"], r["inep"], r["zona"], r["diretor"], r["qtd_guias"], r["total_itens"], r["alunos"], round(r["valor_total"], 2), r["custo_por_aluno"]] for r in resumo_escolas]
            buf = gerar_excel_almoxarifado("Relatório de Entregas por Escola", subtitulo, cab, rows_x, {4: "int", 5: "int", 6: "int", 7: "moeda", 8: "moeda"})
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment;filename=relatorio_entregas_escolas.xlsx"},
        )

    escolas = Escola.query.order_by(Escola.nome).all()
    categorias = AlmoxCategoria.query.order_by(AlmoxCategoria.nome).all()
    return render_template(
        "almoxarifado/relatorio_entregas_escolas.html",
        resumo_escolas=resumo_escolas,
        detalhes=detalhes,
        total_geral=total_geral,
        escolas=escolas,
        categorias=categorias,
        escola_selecionada=escola_selecionada,
        escola_id=escola_id,
        zona=zona,
        categoria_id=categoria_id,
        data_inicio=data_inicio,
        data_fim=data_fim,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/relatorios/entregas-setores")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_entregas_setores():
    """Relatório de Entregas por Setor da SEME (Consolidado comparativo ou Extrato individual)."""
    data_inicio = request.args.get("data_inicio") or date(date.today().year, 1, 1).strftime("%Y-%m-%d")
    data_fim = request.args.get("data_fim") or date.today().strftime("%Y-%m-%d")
    setor_id = request.args.get("setor_id", type=int)
    categoria_id = request.args.get("categoria_id", type=int)
    formato = request.args.get("formato", "html")

    d_ini = _parse_date_opt(data_inicio)
    d_fim = _parse_date_opt(data_fim)

    query = (
        db.session.query(AlmoxEntregaItem, AlmoxEntrega, Material, AlmoxSetor)
        .join(AlmoxEntrega, AlmoxEntregaItem.entrega_id == AlmoxEntrega.id)
        .join(Material, AlmoxEntregaItem.material_id == Material.id)
        .join(AlmoxSetor, AlmoxEntrega.setor_id == AlmoxSetor.id)
        .filter(
            AlmoxEntrega.status != "Estornada",
            AlmoxEntrega.tipo_saida.in_(["Requisição", "Entrega Direta", "Distribuição em Lote"]),
        )
    )
    if d_ini:
        query = query.filter(AlmoxEntrega.data_entrega >= d_ini)
    if d_fim:
        query = query.filter(AlmoxEntrega.data_entrega <= d_fim)
    if setor_id:
        query = query.filter(AlmoxSetor.id == setor_id)
    if categoria_id:
        query = query.filter(Material.categoria_id == categoria_id)

    registros = query.order_by(AlmoxSetor.nome.asc(), AlmoxEntrega.data_entrega.desc()).all()

    mapa_setores = {}
    detalhes = []
    for item, ent, mat, st in registros:
        val = float(item.valor_total or 0.0)
        if st.id not in mapa_setores:
            mapa_setores[st.id] = {
                "setor_id": st.id,
                "codigo": st.codigo or "-",
                "sigla": st.sigla or "-",
                "setor": st.nome,
                "responsavel": st.responsavel_nome or "-",
                "guias": set(),
                "total_itens": 0,
                "total_qtd": 0.0,
                "valor_total": 0.0,
            }
        m = mapa_setores[st.id]
        m["guias"].add(ent.id)
        m["total_itens"] += 1
        m["total_qtd"] += float(item.quantidade or 0.0)
        m["valor_total"] += val

        detalhes.append(
            {
                "data": ent.data_entrega,
                "guia": ent.codigo_guia,
                "entrega_id": ent.id,
                "setor": st.nome,
                "material": mat.descricao,
                "categoria": mat.nome_categoria_exibicao,
                "unidade": mat.unidade_medida,
                "quantidade": item.quantidade,
                "custo_unit": item.custo_unitario_medio or 0.0,
                "valor_total": val,
                "recebedor": ent.recebedor_nome or "-",
            }
        )

    resumo_setores = sorted(mapa_setores.values(), key=lambda x: x["valor_total"], reverse=True)
    total_geral = round(sum(r["valor_total"] for r in resumo_setores), 2)
    for r in resumo_setores:
        r["qtd_guias"] = len(r["guias"])
        r["percentual"] = round((r["valor_total"] / total_geral) * 100.0, 1) if total_geral > 0 else 0.0

    setor_selecionado = AlmoxSetor.query.get(setor_id) if setor_id else None
    subtitulo = (
        f"Setor: {setor_selecionado.nome} | "
        if setor_selecionado
        else f"Consolidado de Setores da SEME ({len(resumo_setores)} setor(es)) | "
    ) + f"Período: {d_ini.strftime('%d/%m/%Y') if d_ini else '-'} a {d_fim.strftime('%d/%m/%Y') if d_fim else '-'}"

    if formato == "pdf":
        if setor_selecionado:
            cab = ["Data", "Guia", "Material", "Categoria", "Qtd.", "Unid.", "Custo Méd.", "Total (R$)", "Recebedor"]
            rows_pdf = [
                [d["data"].strftime("%d/%m/%Y"), d["guia"], d["material"], d["categoria"], formatar_qtd(d["quantidade"]), d["unidade"], formatar_moeda_br(d["custo_unit"]), formatar_moeda_br(d["valor_total"]), d["recebedor"]]
                for d in detalhes
            ]
            buf = gerar_pdf_relatorio_tabela(
                f"EXTRATO DE ENTREGAS — {setor_selecionado.nome.upper()}",
                subtitulo,
                cab,
                rows_pdf,
                col_widths_cm=[2.0, 2.7, 6.8, 4.0, 1.6, 1.3, 2.3, 2.5, 4.1],
                paisagem=True,
                rodape_totais=["", "TOTAL", f"{len(detalhes)} lançamento(s)", "", "", "", "", formatar_moeda_br(total_geral), ""],
            )
        else:
            cab = ["Código", "Setor da SEME", "Responsável", "Guias", "Itens", "Valor Total (R$)", "% Part."]
            rows_pdf = [
                [r["codigo"], r["setor"], r["responsavel"], str(r["qtd_guias"]), str(r["total_itens"]), formatar_moeda_br(r["valor_total"]), f"{r['percentual']:.1f}%"]
                for r in resumo_setores
            ]
            buf = gerar_pdf_relatorio_tabela(
                "RELATÓRIO DE ENTREGAS POR SETOR DA SEME",
                subtitulo,
                cab,
                rows_pdf,
                col_widths_cm=[2.6, 9.5, 5.0, 2.0, 2.0, 3.8, 2.4],
                paisagem=True,
                kpis=[
                    ("Setores Atendidos", str(len(resumo_setores))),
                    ("Total de Guias", str(sum(r["qtd_guias"] for r in resumo_setores))),
                    ("Valor Total Entregue", formatar_moeda_br(total_geral)),
                ],
                rodape_totais=["", "TOTAL SETORES SEME", "", str(sum(r["qtd_guias"] for r in resumo_setores)), str(sum(r["total_itens"] for r in resumo_setores)), formatar_moeda_br(total_geral), "100,0%"],
            )
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": "inline;filename=relatorio_entregas_setores_seme.pdf"})

    if formato == "excel":
        if setor_selecionado:
            cab = ["Data", "Guia", "Setor SEME", "Material", "Categoria", "Unidade", "Quantidade", "Custo Médio", "Valor Total", "Recebedor"]
            rows_x = [[d["data"].strftime("%d/%m/%Y"), d["guia"], d["setor"], d["material"], d["categoria"], d["unidade"], d["quantidade"], d["custo_unit"], d["valor_total"], d["recebedor"]] for d in detalhes]
            buf = gerar_excel_almoxarifado(f"Entregas - {setor_selecionado.nome}", subtitulo, cab, rows_x, {6: "qtd", 7: "moeda", 8: "moeda"})
        else:
            cab = ["Código", "Sigla", "Setor da SEME", "Responsável", "Guias", "Itens", "Valor Total (R$)", "% Participação"]
            rows_x = [[r["codigo"], r["sigla"], r["setor"], r["responsavel"], r["qtd_guias"], r["total_itens"], round(r["valor_total"], 2), r["percentual"]] for r in resumo_setores]
            buf = gerar_excel_almoxarifado("Relatório de Entregas por Setor da SEME", subtitulo, cab, rows_x, {4: "int", 5: "int", 6: "moeda"})
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment;filename=relatorio_entregas_setores_seme.xlsx"},
        )

    setores = AlmoxSetor.query.order_by(AlmoxSetor.nome).all()
    categorias = AlmoxCategoria.query.order_by(AlmoxCategoria.nome).all()
    return render_template(
        "almoxarifado/relatorio_entregas_setores.html",
        resumo_setores=resumo_setores,
        detalhes=detalhes,
        total_geral=total_geral,
        setores=setores,
        categorias=categorias,
        setor_selecionado=setor_selecionado,
        setor_id=setor_id,
        categoria_id=categoria_id,
        data_inicio=data_inicio,
        data_fim=data_fim,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/relatorios/rma")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_rma():
    """RMA — Relatório Mensal de Almoxarifado por Subelemento Contábil 3.3.90.30 (Exigência Contabilidade / TCE)."""
    hoje = date.today()
    mes = request.args.get("mes", hoje.month, type=int)
    ano = request.args.get("ano", hoje.year, type=int)
    formato = request.args.get("formato", "html")

    dt_inicio = datetime(ano, mes, 1, 0, 0, 0)
    if mes == 12:
        dt_fim = datetime(ano + 1, 1, 1, 0, 0, 0)
    else:
        dt_fim = datetime(ano, mes + 1, 1, 0, 0, 0)

    materiais = Material.query.options(joinedload(Material.categoria_rel)).all()
    movs_ate_fim = (
        MovimentoEstoque.query.filter(MovimentoEstoque.data_movimento < dt_fim)
        .order_by(MovimentoEstoque.data_movimento.asc(), MovimentoEstoque.id.asc())
        .all()
    )

    movs_por_mat = defaultdict(list)
    for m in movs_ate_fim:
        movs_por_mat[m.material_id].append(m)

    grupos = {}
    for mat in materiais:
        sub = mat.codigo_subelemento_exibicao
        nome_cat = mat.nome_categoria_exibicao
        conta = mat.categoria_rel.conta_contabil if mat.categoria_rel else "1.1.5.6.1.01.00"
        chave = (sub, nome_cat, conta)
        if chave not in grupos:
            grupos[chave] = {
                "subelemento": sub,
                "conta_contabil": conta or "-",
                "categoria": nome_cat,
                "saldo_anterior": 0.0,
                "entradas": 0.0,
                "saidas": 0.0,
                "ajustes": 0.0,
                "saldo_atual": 0.0,
            }

        movs = movs_por_mat.get(mat.id, [])
        # Calcula posição no início do mês e fluxos no mês
        saldo_qtd_ant = 0.0
        cm_ant = mat.custo_referencia
        ent_mes = 0.0
        sai_mes = 0.0
        aju_mes = 0.0

        for mv in movs:
            val_mv = float(mv.valor_total if mv.valor_total is not None else abs(mv.quantidade) * (mv.valor_unitario or mat.custo_referencia))
            if mv.data_movimento < dt_inicio:
                saldo_qtd_ant += float(mv.quantidade or 0.0)
                if mv.custo_medio_apos is not None:
                    cm_ant = float(mv.custo_medio_apos)
            else:
                tp = (mv.tipo_movimento or "").lower()
                if "ajuste" in tp or "inventário" in tp or "inventario" in tp:
                    aju_mes += val_mv if mv.quantidade >= 0 else -val_mv
                elif mv.quantidade >= 0:
                    ent_mes += val_mv
                else:
                    sai_mes += val_mv

        val_saldo_ant = round(max(0.0, saldo_qtd_ant) * cm_ant, 2)
        val_saldo_fim = round(max(0.0, val_saldo_ant + ent_mes - sai_mes + aju_mes), 2)

        g = grupos[chave]
        g["saldo_anterior"] = round(g["saldo_anterior"] + val_saldo_ant, 2)
        g["entradas"] = round(g["entradas"] + ent_mes, 2)
        g["saidas"] = round(g["saidas"] + sai_mes, 2)
        g["ajustes"] = round(g["ajustes"] + aju_mes, 2)
        g["saldo_atual"] = round(g["saldo_atual"] + val_saldo_fim, 2)

    linhas_rma = sorted(grupos.values(), key=lambda x: x["subelemento"])
    totais = {
        "saldo_anterior": round(sum(x["saldo_anterior"] for x in linhas_rma), 2),
        "entradas": round(sum(x["entradas"] for x in linhas_rma), 2),
        "saidas": round(sum(x["saidas"] for x in linhas_rma), 2),
        "ajustes": round(sum(x["ajustes"] for x in linhas_rma), 2),
        "saldo_atual": round(sum(x["saldo_atual"] for x in linhas_rma), 2),
    }

    competencia = f"{mes:02d}/{ano}"
    subtitulo = f"Competência: {competencia} | Critério de Avaliação: Custo Médio Ponderado Móvel (NBC TSP 04)"

    if formato == "pdf":
        cab = ["Subelemento", "Conta Contábil", "Especificação (3.3.90.30)", "Saldo Anterior", "Entradas (+)", "Saídas (-)", "Ajustes (±)", "Saldo Atual"]
        rows_pdf = [
            [
                x["subelemento"],
                x["conta_contabil"],
                x["categoria"],
                formatar_moeda_br(x["saldo_anterior"]),
                formatar_moeda_br(x["entradas"]),
                formatar_moeda_br(x["saidas"]),
                formatar_moeda_br(x["ajustes"]),
                formatar_moeda_br(x["saldo_atual"]),
            ]
            for x in linhas_rma
        ]
        buf = gerar_pdf_relatorio_tabela(
            f"RMA — RELATÓRIO MENSAL DE ALMOXARIFADO ({competencia})",
            subtitulo,
            cab,
            rows_pdf,
            col_widths_cm=[2.6, 3.0, 6.7, 3.0, 3.0, 3.0, 2.8, 3.2],
            paisagem=True,
            kpis=[
                ("Saldo Anterior", formatar_moeda_br(totais["saldo_anterior"])),
                ("Total Entradas", formatar_moeda_br(totais["entradas"])),
                ("Total Saídas", formatar_moeda_br(totais["saidas"])),
                ("Ajustes Líquidos", formatar_moeda_br(totais["ajustes"])),
                ("Saldo Atual (Contábil)", formatar_moeda_br(totais["saldo_atual"])),
            ],
            rodape_totais=["TOTAL", "", "CONSOLIDADO SEME", formatar_moeda_br(totais["saldo_anterior"]), formatar_moeda_br(totais["entradas"]), formatar_moeda_br(totais["saidas"]), formatar_moeda_br(totais["ajustes"]), formatar_moeda_br(totais["saldo_atual"])],
        )
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": f"inline;filename=RMA_{ano}_{mes:02d}.pdf"})

    if formato == "excel":
        cab = ["Subelemento", "Conta Contábil", "Categoria", "Saldo Anterior", "Entradas (+)", "Saídas (-)", "Ajustes (±)", "Saldo Atual"]
        rows_x = [[x["subelemento"], x["conta_contabil"], x["categoria"], x["saldo_anterior"], x["entradas"], x["saidas"], x["ajustes"], x["saldo_atual"]] for x in linhas_rma]
        rows_x.append(["TOTAL", "", "CONSOLIDADO SEME", totais["saldo_anterior"], totais["entradas"], totais["saidas"], totais["ajustes"], totais["saldo_atual"]])
        buf = gerar_excel_almoxarifado(f"RMA - Competência {competencia}", subtitulo, cab, rows_x, {3: "moeda", 4: "moeda", 5: "moeda", 6: "moeda", 7: "moeda"})
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment;filename=RMA_{ano}_{mes:02d}.xlsx"},
        )

    return render_template(
        "almoxarifado/relatorio_rma.html",
        linhas_rma=linhas_rma,
        totais=totais,
        mes=mes,
        ano=ano,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/relatorios/estoque")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_estoque():
    categoria_id = request.args.get("categoria_id", type=int)
    situacao = request.args.get("situacao", "todos")
    formato = request.args.get("formato", "html")

    query = Material.query.options(joinedload(Material.categoria_rel)).filter(
        or_(Material.ativo.is_(True), Material.ativo.is_(None))
    )
    if categoria_id:
        query = query.filter(Material.categoria_id == categoria_id)
    if situacao == "baixo":
        query = query.filter(Material.estoque_minimo > 0, Material.estoque_atual <= Material.estoque_minimo)
    elif situacao == "zerado":
        query = query.filter(Material.estoque_atual <= 0)
    elif situacao == "ponto_pedido":
        query = query.filter(Material.ponto_pedido > 0, Material.estoque_atual <= Material.ponto_pedido)

    materiais = query.order_by(Material.descricao).all()
    valor_total_estoque = round(sum(m.valor_estoque_atual for m in materiais), 2)

    if formato == "pdf":
        cab = ["Cód.", "Material", "Subelemento / Categoria", "Local", "Saldo", "Mín.", "Custo Méd.", "Valor Total"]
        rows_pdf = [
            [
                m.codigo_interno or str(m.id),
                f"{m.descricao} ({m.unidade_medida})",
                f"{m.codigo_subelemento_exibicao} - {m.nome_categoria_exibicao}",
                m.localizacao_fisica or "-",
                formatar_qtd(m.estoque_atual),
                formatar_qtd(m.estoque_minimo),
                formatar_moeda_br(m.custo_referencia),
                formatar_moeda_br(m.valor_estoque_atual),
            ]
            for m in materiais
        ]
        buf = gerar_pdf_relatorio_tabela(
            "POSIÇÃO ATUAL DE ESTOQUE — ALMOXARIFADO SEME",
            f"Filtro: {situacao.upper()} | Avaliação pelo Custo Médio Ponderado (CMPM)",
            cab,
            rows_pdf,
            col_widths_cm=[1.8, 7.6, 5.8, 2.5, 2.0, 1.8, 2.6, 3.2],
            paisagem=True,
            kpis=[("Total de Itens", str(len(materiais))), ("Valor Patrimonial (CMPM)", formatar_moeda_br(valor_total_estoque))],
            rodape_totais=["", "TOTAL GERAL", f"{len(materiais)} item(ns)", "", "", "", "", formatar_moeda_br(valor_total_estoque)],
        )
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": "inline;filename=posicao_estoque_seme.pdf"})

    if formato == "excel":
        cab = ["Código", "Descrição", "Subelemento", "Categoria", "Localização", "Unidade", "Estoque Atual", "Estoque Mínimo", "Ponto Pedido", "Custo Médio", "Valor Total"]
        rows_x = [
            [m.codigo_interno or m.id, m.descricao, m.codigo_subelemento_exibicao, m.nome_categoria_exibicao, m.localizacao_fisica or "", m.unidade_medida, m.estoque_atual, m.estoque_minimo or 0, m.ponto_pedido or 0, m.custo_referencia, m.valor_estoque_atual]
            for m in materiais
        ]
        buf = gerar_excel_almoxarifado("Posição de Estoque Atual", f"Filtro: {situacao}", cab, rows_x, {6: "qtd", 7: "qtd", 8: "qtd", 9: "moeda", 10: "moeda"})
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment;filename=posicao_estoque_seme.xlsx"},
        )

    categorias = AlmoxCategoria.query.order_by(AlmoxCategoria.nome).all()
    return render_template(
        "almoxarifado/relatorio_estoque.html",
        materiais=materiais,
        valor_total_estoque=valor_total_estoque,
        categorias=categorias,
        categoria_id=categoria_id,
        situacao=situacao,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/relatorios/estoque/exportar")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def exportar_relatorio_estoque_csv():
    materiais = Material.query.options(joinedload(Material.categoria_rel)).order_by(Material.descricao).all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Codigo", "Descricao", "Subelemento", "Categoria", "Unidade", "Estoque Atual", "Custo Medio", "Valor Total"])
    for m in materiais:
        writer.writerow(
            [
                m.codigo_interno or m.id,
                m.descricao,
                m.codigo_subelemento_exibicao,
                m.nome_categoria_exibicao,
                m.unidade_medida,
                str(m.estoque_atual).replace(".", ","),
                str(round(m.custo_referencia, 4)).replace(".", ","),
                str(m.valor_estoque_atual).replace(".", ","),
            ]
        )
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=relatorio_estoque_{date.today().strftime('%Y-%m-%d')}.csv"},
    )


@almoxarifado_bp.route("/relatorios/kardex")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_kardex():
    """Ficha de Prateleira (Kardex) cronológica por material."""
    material_id = request.args.get("material_id", type=int)
    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")
    formato = request.args.get("formato", "html")

    materiais = Material.query.order_by(Material.descricao).all()
    material = Material.query.get(material_id) if material_id else (materiais[0] if materiais else None)
    movimentos = []

    if material:
        q = (
            MovimentoEstoque.query.options(
                joinedload(MovimentoEstoque.usuario),
                joinedload(MovimentoEstoque.fornecedor),
                joinedload(MovimentoEstoque.escola),
                joinedload(MovimentoEstoque.setor),
            )
            .filter(MovimentoEstoque.material_id == material.id)
        )
        if data_inicio:
            d = _parse_date_opt(data_inicio)
            if d:
                q = q.filter(MovimentoEstoque.data_movimento >= datetime.combine(d, datetime.min.time()))
        if data_fim:
            d = _parse_date_opt(data_fim)
            if d:
                q = q.filter(MovimentoEstoque.data_movimento <= datetime.combine(d, datetime.max.time()))
        movimentos = q.order_by(MovimentoEstoque.data_movimento.asc(), MovimentoEstoque.id.asc()).all()

    if material and formato in ("pdf", "excel"):
        cab = ["Data/Hora", "Tipo Movimento", "Doc. Ref.", "Origem / Destino", "Entrada", "Saída", "Saldo Após", "Custo Méd.", "Usuário"]
        rows = []
        for mv in movimentos:
            orig_dest = (
                mv.escola.nome
                if mv.escola
                else (mv.setor.nome if mv.setor else (mv.fornecedor.nome if mv.fornecedor else (mv.justificativa or "-")))
            )
            ent_q = mv.quantidade if mv.quantidade > 0 else 0.0
            sai_q = abs(mv.quantidade) if mv.quantidade < 0 else 0.0
            if formato == "pdf":
                rows.append(
                    [
                        mv.data_movimento.strftime("%d/%m/%Y %H:%M"),
                        mv.tipo_movimento,
                        mv.documento_ref or mv.nota_fiscal or "-",
                        orig_dest[:55],
                        formatar_qtd(ent_q) if ent_q else "-",
                        formatar_qtd(sai_q) if sai_q else "-",
                        formatar_qtd(mv.saldo_apos) if mv.saldo_apos is not None else "-",
                        formatar_moeda_br(mv.custo_medio_apos or mv.valor_unitario or 0.0),
                        mv.usuario.username if mv.usuario else "-",
                    ]
                )
            else:
                rows.append(
                    [
                        mv.data_movimento.strftime("%d/%m/%Y %H:%M"),
                        mv.tipo_movimento,
                        mv.documento_ref or mv.nota_fiscal or "",
                        orig_dest,
                        ent_q,
                        sai_q,
                        mv.saldo_apos if mv.saldo_apos is not None else "",
                        mv.custo_medio_apos or mv.valor_unitario or 0.0,
                        mv.usuario.username if mv.usuario else "",
                    ]
                )
        sub = f"Material: {material.descricao} ({material.codigo_interno or material.id}) | Unid: {material.unidade_medida} | Saldo Atual: {formatar_qtd(material.estoque_atual)}"
        if formato == "pdf":
            buf = gerar_pdf_relatorio_tabela(
                "FICHA DE PRATELEIRA (KARDEX) — ALMOXARIFADO SEME",
                sub,
                cab,
                rows,
                col_widths_cm=[2.6, 3.2, 2.6, 6.7, 1.8, 1.8, 2.0, 2.6, 4.0],
                paisagem=True,
            )
            return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": f"inline;filename=kardex_mat_{material.id}.pdf"})
        buf = gerar_excel_almoxarifado(f"Kardex - {material.descricao}", sub, cab, rows, {4: "qtd", 5: "qtd", 6: "qtd", 7: "moeda"})
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment;filename=kardex_mat_{material.id}.xlsx"},
        )

    return render_template(
        "almoxarifado/relatorio_kardex.html",
        materiais=materiais,
        material=material,
        movimentos=movimentos,
        data_inicio=data_inicio,
        data_fim=data_fim,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/relatorios/validades")
@login_required
@role_required("admin", "almoxarifado", "Fiscal")
def relatorio_validades():
    dias = request.args.get("dias", 90, type=int)
    formato = request.args.get("formato", "html")
    hoje = date.today()
    limite = hoje + timedelta(days=dias)

    lotes = (
        AlmoxLote.query.options(joinedload(AlmoxLote.material).joinedload(Material.categoria_rel))
        .filter(AlmoxLote.quantidade_atual > 0, AlmoxLote.data_validade.isnot(None), AlmoxLote.data_validade <= limite)
        .order_by(AlmoxLote.data_validade.asc())
        .all()
    )

    if formato == "pdf":
        cab = ["Material", "Lote", "Validade", "Dias Rest.", "Saldo Lote", "Valor Estimado", "Situação"]
        rows = []
        for l in lotes:
            dr = (l.data_validade - hoje).days
            sit = "VENCIDO" if dr < 0 else ("CRÍTICO (<=30d)" if dr <= 30 else "ATENÇÃO")
            rows.append([l.material.descricao, l.numero_lote, l.data_validade.strftime("%d/%m/%Y"), str(dr), f"{formatar_qtd(l.quantidade_atual)} {l.material.unidade_medida}", formatar_moeda_br(l.quantidade_atual * (l.custo_unitario or l.material.custo_referencia)), sit])
        buf = gerar_pdf_relatorio_tabela("CONTROLE DE VALIDADE DE LOTES (FEFO)", f"Horizonte de alerta: até {dias} dias", cab, rows, paisagem=True)
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": "inline;filename=validades_lotes_seme.pdf"})

    return render_template("almoxarifado/relatorio_validades.html", lotes=lotes, dias=dias, hoje=hoje, formatar_moeda_br=formatar_moeda_br)


@almoxarifado_bp.route("/relatorios/curva-abc")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_curva_abc():
    data_inicio = request.args.get("data_inicio") or date(date.today().year, 1, 1).strftime("%Y-%m-%d")
    data_fim = request.args.get("data_fim") or date.today().strftime("%Y-%m-%d")
    formato = request.args.get("formato", "html")

    d_ini = _parse_date_opt(data_inicio)
    d_fim = _parse_date_opt(data_fim)

    q = (
        db.session.query(
            Material.id,
            Material.codigo_interno,
            Material.descricao,
            Material.unidade_medida,
            func.coalesce(func.sum(AlmoxEntregaItem.quantidade), 0.0).label("qtd"),
            func.coalesce(func.sum(AlmoxEntregaItem.valor_total), 0.0).label("valor"),
        )
        .join(AlmoxEntregaItem, AlmoxEntregaItem.material_id == Material.id)
        .join(AlmoxEntrega, AlmoxEntregaItem.entrega_id == AlmoxEntrega.id)
        .filter(
            AlmoxEntrega.status != "Estornada",
            AlmoxEntrega.tipo_saida.in_(["Requisição", "Entrega Direta", "Distribuição em Lote"]),
        )
    )
    if d_ini:
        q = q.filter(AlmoxEntrega.data_entrega >= d_ini)
    if d_fim:
        q = q.filter(AlmoxEntrega.data_entrega <= d_fim)

    rows_db = q.group_by(Material.id, Material.codigo_interno, Material.descricao, Material.unidade_medida).order_by(func.sum(AlmoxEntregaItem.valor_total).desc()).all()
    total_geral = sum(float(r.valor or 0.0) for r in rows_db)

    itens_abc = []
    acum = 0.0
    for r in rows_db:
        val = float(r.valor or 0.0)
        pct = (val / total_geral * 100.0) if total_geral > 0 else 0.0
        acum += pct
        classe = "A" if acum <= 80.0 else ("B" if acum <= 95.0 else "C")
        itens_abc.append(
            {
                "codigo": r.codigo_interno or r.id,
                "descricao": r.descricao,
                "unidade": r.unidade_medida,
                "qtd": float(r.qtd or 0.0),
                "valor": round(val, 2),
                "pct": round(pct, 2),
                "acum": round(min(acum, 100.0), 2),
                "classe": classe,
            }
        )

    sub = f"Período: {d_ini.strftime('%d/%m/%Y') if d_ini else '-'} a {d_fim.strftime('%d/%m/%Y') if d_fim else '-'}"
    if formato == "pdf":
        cab = ["Classe", "Código", "Material", "Unid.", "Qtd. Consumida", "Valor Total (R$)", "% Indiv.", "% Acum."]
        rows_pdf = [[i["classe"], str(i["codigo"]), i["descricao"], i["unidade"], formatar_qtd(i["qtd"]), formatar_moeda_br(i["valor"]), f"{i['pct']:.2f}%", f"{i['acum']:.2f}%"] for i in itens_abc]
        buf = gerar_pdf_relatorio_tabela("CURVA ABC DE CONSUMO — ALMOXARIFADO SEME", sub, cab, rows_pdf, col_widths_cm=[1.8, 2.2, 10.5, 1.6, 2.8, 3.6, 2.4, 2.4], paisagem=True)
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": "inline;filename=curva_abc_seme.pdf"})

    if formato == "excel":
        cab = ["Classe", "Código", "Material", "Unidade", "Qtd. Consumida", "Valor Total (R$)", "% Individual", "% Acumulado"]
        rows_x = [[i["classe"], i["codigo"], i["descricao"], i["unidade"], i["qtd"], i["valor"], i["pct"], i["acum"]] for i in itens_abc]
        buf = gerar_excel_almoxarifado("Curva ABC de Consumo", sub, cab, rows_x, {4: "qtd", 5: "moeda"})
        return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment;filename=curva_abc_seme.xlsx"})

    return render_template(
        "almoxarifado/relatorio_curva_abc.html",
        itens_abc=itens_abc,
        total_geral=round(total_geral, 2),
        data_inicio=data_inicio,
        data_fim=data_fim,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/relatorios/movimentacoes")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_movimentacoes():
    data_inicio_str = request.args.get("data_inicio")
    data_fim_str = request.args.get("data_fim")
    material_id = request.args.get("material_id", type=int)
    tipo_movimento = request.args.get("tipo_movimento")
    formato = request.args.get("formato", "html")

    query = MovimentoEstoque.query.options(
        joinedload(MovimentoEstoque.material),
        joinedload(MovimentoEstoque.usuario),
        joinedload(MovimentoEstoque.fornecedor),
        joinedload(MovimentoEstoque.escola),
        joinedload(MovimentoEstoque.setor),
    )

    if data_inicio_str:
        d = _parse_date_opt(data_inicio_str)
        if d:
            query = query.filter(MovimentoEstoque.data_movimento >= datetime.combine(d, datetime.min.time()))
    if data_fim_str:
        d = _parse_date_opt(data_fim_str)
        if d:
            query = query.filter(MovimentoEstoque.data_movimento <= datetime.combine(d, datetime.max.time()))
    if material_id:
        query = query.filter(MovimentoEstoque.material_id == material_id)
    if tipo_movimento:
        query = query.filter(MovimentoEstoque.tipo_movimento == tipo_movimento)

    movimentacoes = query.order_by(MovimentoEstoque.data_movimento.desc(), MovimentoEstoque.id.desc()).limit(500).all()

    if formato == "excel":
        cab = ["Data/Hora", "Material", "Tipo Movimento", "Doc. Ref.", "Origem/Destino", "Quantidade", "Valor Unit.", "Valor Total", "Saldo Após", "Usuário", "Justificativa"]
        rows_x = []
        for mv in movimentacoes:
            dest = mv.escola.nome if mv.escola else (mv.setor.nome if mv.setor else (mv.fornecedor.nome if mv.fornecedor else ""))
            rows_x.append([mv.data_movimento.strftime("%d/%m/%Y %H:%M"), mv.material.descricao if mv.material else "", mv.tipo_movimento, mv.documento_ref or mv.nota_fiscal or "", dest, mv.quantidade, mv.valor_unitario or 0.0, mv.valor_total or 0.0, mv.saldo_apos if mv.saldo_apos is not None else "", mv.usuario.username if mv.usuario else "", mv.justificativa or ""])
        buf = gerar_excel_almoxarifado("Movimentações de Estoque", f"Filtro: {tipo_movimento or 'Todos'}", cab, rows_x, {5: "qtd", 6: "moeda", 7: "moeda", 8: "qtd"})
        return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment;filename=movimentacoes_almoxarifado.xlsx"})

    materiais_para_filtro = Material.query.order_by(Material.descricao).all()
    tipos_disponiveis = [
        "Entrada NF",
        "Entrada Doação",
        "Devolução Unidade",
        "Saída Requisição",
        "Saída Entrega Direta",
        "Saída Distribuição Lote",
        "Baixa Técnica",
        "Ajuste Inventário (+)",
        "Ajuste Inventário (-)",
        "Ajuste Inventário",
        "Estorno Entrada",
        "Estorno Saída",
    ]
    return render_template(
        "almoxarifado/relatorio_movimentacoes.html",
        movimentacoes=movimentacoes,
        materiais=materiais_para_filtro,
        tipos_disponiveis=tipos_disponiveis,
        data_inicio=data_inicio_str,
        data_fim=data_fim_str,
        material_id_selecionado=material_id,
        tipo_movimento_selecionado=tipo_movimento,
        formatar_moeda_br=formatar_moeda_br,
    )


@almoxarifado_bp.route("/relatorios/entradas")
@login_required
@role_required("admin", "almoxarifado", "Fiscal", "RH")
def relatorio_entradas():
    """Relatório de Entradas por Fornecedor, Nota Fiscal, Empenho e Fonte de Recursos."""
    data_inicio = request.args.get("data_inicio") or date(date.today().year, 1, 1).strftime("%Y-%m-%d")
    data_fim = request.args.get("data_fim") or date.today().strftime("%Y-%m-%d")
    fornecedor_id = request.args.get("fornecedor_id", type=int)
    fonte_recursos = (request.args.get("fonte_recursos") or "").strip()
    formato = request.args.get("formato", "html")

    d_ini = _parse_date_opt(data_inicio)
    d_fim = _parse_date_opt(data_fim)

    q = AlmoxEntrada.query.options(
        joinedload(AlmoxEntrada.fornecedor),
        joinedload(AlmoxEntrada.itens),
    ).filter(AlmoxEntrada.status == "Confirmada")
    if d_ini:
        q = q.filter(AlmoxEntrada.data_recebimento >= d_ini)
    if d_fim:
        q = q.filter(AlmoxEntrada.data_recebimento <= d_fim)
    if fornecedor_id:
        q = q.filter(AlmoxEntrada.fornecedor_id == fornecedor_id)
    if fonte_recursos:
        q = q.filter(AlmoxEntrada.fonte_recursos.ilike(f"%{fonte_recursos}%"))

    entradas = q.order_by(AlmoxEntrada.data_recebimento.desc(), AlmoxEntrada.id.desc()).all()
    total_valor = round(sum(e.valor_total or 0.0 for e in entradas), 2)
    sub = f"Período: {d_ini.strftime('%d/%m/%Y') if d_ini else '-'} a {d_fim.strftime('%d/%m/%Y') if d_fim else '-'}"

    if formato == "pdf":
        cab = ["Receb.", "Código", "NF", "Fornecedor", "Empenho", "Contrato/ARP", "Fonte", "Itens", "Total (R$)"]
        rows_pdf = [
            [
                e.data_recebimento.strftime("%d/%m/%Y"),
                e.numero_entrada,
                e.numero_nf or "-",
                (e.fornecedor.nome if e.fornecedor else (e.fornecedor_nome_snapshot or "-"))[:40],
                e.numero_empenho or "-",
                e.numero_contrato_arp or "-",
                e.fonte_recursos or "-",
                str(len(e.itens)),
                formatar_moeda_br(e.valor_total),
            ]
            for e in entradas
        ]
        buf = gerar_pdf_relatorio_tabela(
            "RELATÓRIO DE ENTRADAS POR FORNECEDOR / NF / EMPENHO",
            sub,
            cab,
            rows_pdf,
            col_widths_cm=[2.0, 2.7, 2.2, 6.2, 2.6, 2.8, 3.6, 1.4, 3.8],
            paisagem=True,
            kpis=[("Notas / Documentos", str(len(entradas))), ("Valor Total Recebido", formatar_moeda_br(total_valor))],
            rodape_totais=["", "TOTAL", "", f"{len(entradas)} documento(s)", "", "", "", "", formatar_moeda_br(total_valor)],
        )
        return Response(buf.getvalue(), mimetype="application/pdf", headers={"Content-Disposition": "inline;filename=relatorio_entradas_seme.pdf"})

    if formato == "excel":
        cab = ["Data Receb.", "Código", "Tipo", "NF", "Fornecedor", "CNPJ", "Empenho", "Processo", "Contrato/ARP", "Fonte Recursos", "Qtd. Itens", "Valor Total"]
        rows_x = [
            [
                e.data_recebimento.strftime("%d/%m/%Y"),
                e.numero_entrada,
                e.tipo_entrada,
                e.numero_nf or "",
                e.fornecedor.nome if e.fornecedor else (e.fornecedor_nome_snapshot or ""),
                e.fornecedor.cnpj if e.fornecedor else "",
                e.numero_empenho or "",
                e.numero_processo or "",
                e.numero_contrato_arp or "",
                e.fonte_recursos or "",
                len(e.itens),
                e.valor_total or 0.0,
            ]
            for e in entradas
        ]
        buf = gerar_excel_almoxarifado("Entradas por Fornecedor e Empenho", sub, cab, rows_x, {10: "int", 11: "moeda"})
        return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment;filename=relatorio_entradas_seme.xlsx"})

    fornecedores = Fornecedor.query.order_by(Fornecedor.nome).all()
    return render_template(
        "almoxarifado/relatorio_entradas.html",
        entradas=entradas,
        fornecedores=fornecedores,
        total_valor=total_valor,
        data_inicio=data_inicio,
        data_fim=data_fim,
        fornecedor_id=fornecedor_id,
        fonte_recursos=fonte_recursos,
        formatar_moeda_br=formatar_moeda_br,
    )
