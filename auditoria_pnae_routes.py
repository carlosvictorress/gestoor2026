# auditoria_pnae_routes.py
# -*- coding: utf-8 -*-
"""
Rotas do Módulo de Auditoria Analítica — Agricultura Familiar / PNAE (FNDE)
Blueprint: auditoria_pnae (/merenda/agricultura/auditoria)
Permissões: admin, Merenda Escolar, Fiscal
"""
import json
import uuid
from datetime import date, datetime

from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auditoria_pnae_engine import (
    VERSAO_NORMATIVA_ATUAL,
    executar_auditoria_pnae,
    executar_migracao_auditoria_pnae_segura,
    formatar_moeda_br,
    parse_float_br,
)
from auditoria_pnae_reports import (
    gerar_excel_auditoria_pnae,
    gerar_pdf_relatorio_auditoria_pnae,
)
from extensions import db
from models import (
    AgricultorFamiliar,
    ContratoPNAE,
    PnaeAssociadoGrupo,
    PnaeAuditoriaDocumentoStatus,
    PnaeAuditoriaHistorico,
    PnaeAuditoriaJustificativa,
    PnaeNormaRegra,
    PnaePagamentoRegistro,
)
from utils import (
    login_required,
    obter_data_hora_br_str,
    registrar_log,
    role_required,
    upload_arquivo_para_nuvem,
)

auditoria_pnae_bp = Blueprint(
    "auditoria_pnae",
    __name__,
    url_prefix="/merenda/agricultura/auditoria",
)


@auditoria_pnae_bp.before_request
def _garantir_schema():
    executar_migracao_auditoria_pnae_segura()


def _parse_date_opt(val):
    if not val:
        return None
    try:
        return datetime.strptime(val.strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _extrair_filtros_request():
    hoje = date.today()
    exercicio = request.args.get("exercicio", hoje.year, type=int)
    data_inicio_str = request.args.get("data_inicio", "")
    data_fim_str = request.args.get("data_fim", "")
    return {
        "exercicio": exercicio,
        "data_inicio_str": data_inicio_str,
        "data_fim_str": data_fim_str,
        "data_inicio": _parse_date_opt(data_inicio_str),
        "data_fim": _parse_date_opt(data_fim_str),
        "filtro_fornecedor": (request.args.get("fornecedor") or "").strip(),
        "filtro_cpf_cnpj": (request.args.get("cpf_cnpj") or "").strip(),
        "filtro_caf": (request.args.get("caf") or "").strip(),
        "filtro_tipo": (request.args.get("tipo_fornecedor") or "").strip(),
        "filtro_associacao_id": request.args.get("associacao_id", type=int),
        "filtro_situacao": (request.args.get("situacao") or "").strip(),
        "filtro_chamada": (request.args.get("chamada_publica") or "").strip(),
        "filtro_contrato_id": request.args.get("contrato_id", type=int),
    }


def _registrar_historico_auditoria(tipo_relatorio, auditoria, filtros_dict):
    ano = auditoria["exercicio"]
    cod = f"AUD-PNAE-{ano}-{uuid.uuid4().hex[:6].upper()}"
    usuario = session.get("username", "Sistema")
    filtros_serial = {
        k: (v.isoformat() if isinstance(v, (date, datetime)) else v)
        for k, v in (filtros_dict or {}).items()
        if v not in (None, "")
    }
    resumo = {
        "total_fornecedores": auditoria["indicadores"]["total_fornecedores"],
        "total_contratado": auditoria["indicadores"]["total_contratado"],
        "total_executado": auditoria["indicadores"]["total_executado"],
        "total_faturado": auditoria["indicadores"]["total_faturado"],
        "total_pago": auditoria["indicadores"]["total_pago"],
        "limites_ultrapassados": auditoria["indicadores"]["qtd_limites_ultrapassados"],
        "percentual_pnae": auditoria["analise_pnae_45"]["percentual_elegivel"],
    }
    hist = PnaeAuditoriaHistorico(
        codigo_relatorio=cod,
        tipo_relatorio=tipo_relatorio,
        exercicio=ano,
        usuario_responsavel=usuario,
        ip_address=request.remote_addr,
        filtros_json=json.dumps(filtros_serial, ensure_ascii=False),
        qtd_registros_analisados=auditoria["indicadores"]["total_fornecedores"],
        qtd_pendencias_total=auditoria["indicadores"]["qtd_pendencias_total"],
        qtd_criticas=auditoria["indicadores"]["qtd_ocorrencias_criticas"],
        versao_normativa=auditoria["versao_normativa"],
        resumo_json=json.dumps(resumo, ensure_ascii=False),
    )
    db.session.add(hist)
    db.session.commit()
    registrar_log(
        f"Auditoria PNAE: emitiu {tipo_relatorio} ({cod}) - Exercício {ano}, "
        f"{auditoria['indicadores']['total_fornecedores']} fornecedores, "
        f"{auditoria['indicadores']['qtd_ocorrencias_criticas']} crítica(s)."
    )
    return cod


# =============================================================================
# 1. PAINEL GERAL DE AUDITORIA ANALÍTICA PNAE
# =============================================================================

@auditoria_pnae_bp.route("/")
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def painel():
    f = _extrair_filtros_request()
    auditoria = executar_auditoria_pnae(
        exercicio=f["exercicio"],
        data_inicio=f["data_inicio"],
        data_fim=f["data_fim"],
        filtro_fornecedor=f["filtro_fornecedor"],
        filtro_cpf_cnpj=f["filtro_cpf_cnpj"],
        filtro_caf=f["filtro_caf"],
        filtro_tipo=f["filtro_tipo"],
        filtro_associacao_id=f["filtro_associacao_id"],
        filtro_situacao=f["filtro_situacao"],
        filtro_chamada=f["filtro_chamada"],
        filtro_contrato_id=f["filtro_contrato_id"],
        incluir_comparativo=True,
    )

    historico_recente = (
        PnaeAuditoriaHistorico.query.order_by(PnaeAuditoriaHistorico.data_geracao.desc()).limit(15).all()
    )

    ano_corrente = date.today().year
    exercicios_lista = sorted(list({ano_corrente - 2, ano_corrente - 1, ano_corrente, ano_corrente + 1, f["exercicio"]}), reverse=True)

    return render_template(
        "merenda/agricultura/auditoria/painel.html",
        auditoria=auditoria,
        filtros=f,
        exercicios_lista=exercicios_lista,
        historico_recente=historico_recente,
        graficos_json=json.dumps(auditoria["graficos"], ensure_ascii=False),
        ultima_atualizacao=obter_data_hora_br_str(),
        formatar_moeda_br=formatar_moeda_br,
    )


# =============================================================================
# 2. DOSSIÊ ANALÍTICO INDIVIDUAL DO FORNECEDOR
# =============================================================================

@auditoria_pnae_bp.route("/fornecedor/<int:agricultor_id>")
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def dossie_fornecedor(agricultor_id):
    agr = AgricultorFamiliar.query.get_or_404(agricultor_id)
    exercicio = request.args.get("exercicio", date.today().year, type=int)

    auditoria = executar_auditoria_pnae(exercicio=exercicio, incluir_comparativo=False)
    ficha = next((x for x in auditoria["fornecedores"] if x["agricultor_id"] == agr.id), None)
    if not ficha:
        flash("Fornecedor não encontrado na análise do exercício selecionado.", "warning")
        return redirect(url_for("auditoria_pnae.painel", exercicio=exercicio))

    return render_template(
        "merenda/agricultura/auditoria/dossie_fornecedor.html",
        ficha=ficha,
        agr=agr,
        exercicio=exercicio,
        auditoria=auditoria,
        hoje_iso=date.today().strftime("%Y-%m-%d"),
        formatar_moeda_br=formatar_moeda_br,
    )


@auditoria_pnae_bp.route("/fornecedor/<int:agricultor_id>/associado", methods=["POST"])
@login_required
@role_required("admin", "Merenda Escolar")
def salvar_associado_grupo(agricultor_id):
    agr = AgricultorFamiliar.query.get_or_404(agricultor_id)
    exercicio = request.form.get("exercicio", date.today().year, type=int)
    try:
        nome = (request.form.get("nome") or "").strip()
        if not nome:
            flash("O nome do agricultor associado é obrigatório.", "warning")
            return redirect(url_for("auditoria_pnae.dossie_fornecedor", agricultor_id=agr.id, exercicio=exercicio))

        assoc = PnaeAssociadoGrupo(
            agricultor_grupo_id=agr.id,
            nome=nome,
            cpf=(request.form.get("cpf") or "").strip() or None,
            caf_pf_numero=(request.form.get("caf_pf_numero") or "").strip() or None,
            caf_pf_validade=_parse_date_opt(request.form.get("caf_pf_validade")),
            inscrito_caf_pj=bool(request.form.get("inscrito_caf_pj")),
            consta_projeto_venda=bool(request.form.get("consta_projeto_venda")),
            producao_propria=bool(request.form.get("producao_propria")),
            mulher_agricultora=bool(request.form.get("mulher_agricultora")),
            ativo=True,
            observacoes=(request.form.get("observacoes") or "").strip() or None,
        )
        db.session.add(assoc)
        db.session.commit()
        registrar_log(f'Auditoria PNAE: vinculou associado "{assoc.nome}" ao grupo "{agr.razao_social}".')
        flash(f'Associado "{assoc.nome}" cadastrado! VMC recalculado automaticamente.', "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao cadastrar associado: {e}", "danger")
    return redirect(url_for("auditoria_pnae.dossie_fornecedor", agricultor_id=agr.id, exercicio=exercicio))


@auditoria_pnae_bp.route("/associado/<int:assoc_id>/excluir", methods=["POST"])
@login_required
@role_required("admin", "Merenda Escolar")
def excluir_associado_grupo(assoc_id):
    assoc = PnaeAssociadoGrupo.query.get_or_404(assoc_id)
    agr_id = assoc.agricultor_grupo_id
    exercicio = request.form.get("exercicio", date.today().year, type=int)
    try:
        nome = assoc.nome
        db.session.delete(assoc)
        db.session.commit()
        registrar_log(f'Auditoria PNAE: removeu associado "{nome}" do grupo #{agr_id}.')
        flash(f'Associado "{nome}" removido.', "info")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao remover associado: {e}", "danger")
    return redirect(url_for("auditoria_pnae.dossie_fornecedor", agricultor_id=agr_id, exercicio=exercicio))


@auditoria_pnae_bp.route("/fornecedor/<int:agricultor_id>/documento-status", methods=["POST"])
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def atualizar_documento_status(agricultor_id):
    agr = AgricultorFamiliar.query.get_or_404(agricultor_id)
    exercicio = request.form.get("exercicio", date.today().year, type=int)
    codigo_doc = (request.form.get("codigo_doc") or "").strip()
    nome_documento = (request.form.get("nome_documento") or codigo_doc).strip()
    situacao = (request.form.get("situacao") or "DOCUMENTO PENDENTE DE CONFERÊNCIA").strip()

    try:
        reg = PnaeAuditoriaDocumentoStatus.query.filter_by(
            agricultor_id=agr.id, exercicio=exercicio, codigo_doc=codigo_doc
        ).first()
        if not reg:
            reg = PnaeAuditoriaDocumentoStatus(
                agricultor_id=agr.id,
                exercicio=exercicio,
                codigo_doc=codigo_doc,
                nome_documento=nome_documento,
            )
            db.session.add(reg)

        reg.situacao = situacao
        reg.data_validade = _parse_date_opt(request.form.get("data_validade"))
        reg.observacao = (request.form.get("observacao") or "").strip() or None
        reg.usuario_conferencia = session.get("username", "Sistema")
        reg.data_conferencia = datetime.utcnow()

        arq = request.files.get("arquivo_doc")
        if arq and arq.filename:
            url_cloud = upload_arquivo_para_nuvem(arq, pasta="pnae_auditoria_docs")
            if url_cloud:
                reg.arquivo_url = url_cloud

        db.session.commit()
        registrar_log(f'Auditoria PNAE: conferiu documento "{nome_documento}" ({situacao}) de "{agr.razao_social}".')
        flash(f'Situação do documento "{nome_documento}" atualizada para {situacao}.', "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao salvar conferência documental: {e}", "danger")
    return redirect(url_for("auditoria_pnae.dossie_fornecedor", agricultor_id=agr.id, exercicio=exercicio))


@auditoria_pnae_bp.route("/fornecedor/<int:agricultor_id>/pagamento", methods=["POST"])
@login_required
@role_required("admin", "Merenda Escolar")
def registrar_pagamento_auditoria(agricultor_id):
    """Registra ordem bancária / pagamento para conferência cruzada (não altera contratos/entregas originais)."""
    agr = AgricultorFamiliar.query.get_or_404(agricultor_id)
    exercicio = request.form.get("exercicio", date.today().year, type=int)
    try:
        dt_pag = _parse_date_opt(request.form.get("data_pagamento")) or date.today()
        valor_pago = parse_float_br(request.form.get("valor_pago"), 0.0)
        if valor_pago <= 0:
            flash("O valor pago deve ser maior que zero.", "warning")
            return redirect(url_for("auditoria_pnae.dossie_fornecedor", agricultor_id=agr.id, exercicio=exercicio))

        comp_url = None
        arq = request.files.get("comprovante_pagamento")
        if arq and arq.filename:
            comp_url = upload_arquivo_para_nuvem(arq, pasta="pnae_auditoria_pagamentos")

        pag = PnaePagamentoRegistro(
            agricultor_id=agr.id,
            contrato_id=request.form.get("contrato_id", type=int),
            exercicio=dt_pag.year,
            data_pagamento=dt_pag,
            numero_ordem_bancaria=(request.form.get("numero_ordem_bancaria") or "").strip() or None,
            numero_nota_fiscal=(request.form.get("numero_nota_fiscal") or "").strip() or None,
            valor_pago=valor_pago,
            emitido_nome_mulher=bool(request.form.get("emitido_nome_mulher")),
            comprovante_url=comp_url,
            observacoes=(request.form.get("observacoes") or "").strip() or None,
            usuario_registro=session.get("username", "Sistema"),
        )
        db.session.add(pag)
        db.session.commit()
        registrar_log(f'Auditoria PNAE: registrou pagamento {formatar_moeda_br(valor_pago)} (OB {pag.numero_ordem_bancaria or "S/N"}) de "{agr.razao_social}".')
        flash("Registro de pagamento salvo para conciliação financeira!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao registrar pagamento: {e}", "danger")
    return redirect(url_for("auditoria_pnae.dossie_fornecedor", agricultor_id=agr.id, exercicio=exercicio))


@auditoria_pnae_bp.route("/pagamento/<int:pag_id>/excluir", methods=["POST"])
@login_required
@role_required("admin", "Merenda Escolar")
def excluir_pagamento_auditoria(pag_id):
    pag = PnaePagamentoRegistro.query.get_or_404(pag_id)
    agr_id = pag.agricultor_id
    exercicio = pag.exercicio
    try:
        db.session.delete(pag)
        db.session.commit()
        registrar_log(f"Auditoria PNAE: excluiu registro complementar de pagamento #{pag_id}.")
        flash("Registro de pagamento removido.", "info")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir registro: {e}", "danger")
    return redirect(url_for("auditoria_pnae.dossie_fornecedor", agricultor_id=agr_id, exercicio=exercicio))


# =============================================================================
# 3. JUSTIFICATIVA ADMINISTRATIVA E PLANO DE PROVIDÊNCIAS
# =============================================================================

@auditoria_pnae_bp.route("/pendencia/justificar", methods=["POST"])
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def justificar_pendencia():
    chave = (request.form.get("chave_pendencia") or "").strip()
    exercicio = request.form.get("exercicio", date.today().year, type=int)
    agricultor_id = request.form.get("agricultor_id", type=int)
    justificativa_txt = (request.form.get("justificativa_administrativa") or "").strip()
    if not chave or not justificativa_txt:
        flash("A justificativa administrativa é obrigatória.", "warning")
        return redirect(request.referrer or url_for("auditoria_pnae.painel", exercicio=exercicio))

    try:
        reg = PnaeAuditoriaJustificativa.query.filter_by(chave_pendencia=chave, exercicio=exercicio).first()
        usuario = session.get("username", "Sistema")
        status_novo = (request.form.get("status_resolucao") or "Em Análise").strip()
        providencia = (request.form.get("providencia_adotada") or "").strip() or None

        doc_url = None
        arq = request.files.get("documento_comprobatorio")
        if arq and arq.filename:
            doc_url = upload_arquivo_para_nuvem(arq, pasta="pnae_auditoria_justificativas")

        evento = {
            "data": obter_data_hora_br_str(),
            "usuario": usuario,
            "status": status_novo,
            "justificativa": justificativa_txt,
            "providencia": providencia or "",
        }

        if not reg:
            reg = PnaeAuditoriaJustificativa(
                chave_pendencia=chave,
                exercicio=exercicio,
                agricultor_id=agricultor_id,
                contrato_id=request.form.get("contrato_id", type=int),
                categoria=(request.form.get("categoria") or "Auditoria PNAE").strip(),
                grau_risco=(request.form.get("grau_risco") or "AMARELO").strip(),
                descricao_pendencia=(request.form.get("descricao_pendencia") or "").strip(),
                justificativa_administrativa=justificativa_txt,
                providencia_adotada=providencia,
                setor_responsavel=(request.form.get("setor_responsavel") or "Coordenação do PNAE / SEME").strip(),
                prazo_sugerido=_parse_date_opt(request.form.get("prazo_sugerido")),
                status_resolucao=status_novo,
                documento_comprobatorio_url=doc_url,
                usuario_registro=usuario,
                historico_json=json.dumps([evento], ensure_ascii=False),
            )
            db.session.add(reg)
        else:
            hist_list = []
            if reg.historico_json:
                try:
                    hist_list = json.loads(reg.historico_json)
                except Exception:
                    hist_list = []
            hist_list.append(evento)
            reg.justificativa_administrativa = justificativa_txt
            reg.providencia_adotada = providencia or reg.providencia_adotada
            reg.setor_responsavel = (request.form.get("setor_responsavel") or reg.setor_responsavel or "").strip()
            if request.form.get("prazo_sugerido"):
                reg.prazo_sugerido = _parse_date_opt(request.form.get("prazo_sugerido"))
            reg.status_resolucao = status_novo
            if doc_url:
                reg.documento_comprobatorio_url = doc_url
            reg.usuario_registro = usuario
            reg.data_registro = datetime.utcnow()
            reg.historico_json = json.dumps(hist_list, ensure_ascii=False)

        db.session.commit()
        registrar_log(f"Auditoria PNAE: registrou justificativa/providência para pendência {chave} (status: {status_novo}).")
        flash(f"Justificativa administrativa registrada para a ocorrência {chave}!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao salvar justificativa: {e}", "danger")

    return redirect(request.referrer or url_for("auditoria_pnae.painel", exercicio=exercicio))


# =============================================================================
# 4. BASE NORMATIVA CONFIGURÁVEL
# =============================================================================

@auditoria_pnae_bp.route("/normas", methods=["GET", "POST"])
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def gerenciar_normas():
    if request.method == "POST":
        if session.get("role") != "admin":
            flash("Somente o perfil Administrador pode alterar parâmetros da Base Normativa.", "danger")
            return redirect(url_for("auditoria_pnae.gerenciar_normas"))
        try:
            regra_id = request.form.get("regra_id", type=int)
            regra = PnaeNormaRegra.query.get_or_404(regra_id)
            regra.numero_legislacao = (request.form.get("numero_legislacao") or regra.numero_legislacao).strip()
            regra.dispositivo = (request.form.get("dispositivo") or regra.dispositivo).strip()
            regra.gravidade = (request.form.get("gravidade") or regra.gravidade).strip()
            regra.orientacao_regularizacao = (request.form.get("orientacao_regularizacao") or regra.orientacao_regularizacao).strip()
            params_raw = (request.form.get("parametros_json") or "").strip()
            if params_raw:
                json.loads(params_raw)  # Valida JSON antes de salvar
                regra.parametros_json = params_raw
            regra.ativa = bool(request.form.get("ativa"))
            regra.data_atualizacao = datetime.utcnow()
            db.session.commit()
            registrar_log(f"Auditoria PNAE: atualizou regra normativa {regra.codigo}.")
            flash(f"Regra normativa {regra.codigo} atualizada com sucesso!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao atualizar regra normativa: {e}", "danger")
        return redirect(url_for("auditoria_pnae.gerenciar_normas"))

    regras = PnaeNormaRegra.query.order_by(PnaeNormaRegra.id.asc()).all()
    return render_template("merenda/agricultura/auditoria/normas.html", regras=regras, versao_atual=VERSAO_NORMATIVA_ATUAL)


# =============================================================================
# 5. EMISSÃO DE RELATÓRIOS OFICIAIS (PDF, EXCEL, IMPRESSÃO) E HISTÓRICO
# =============================================================================

@auditoria_pnae_bp.route("/relatorio/pdf")
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def relatorio_pdf():
    f = _extrair_filtros_request()
    modo = request.args.get("modo", "completo")  # completo, pendencias, fornecedor, associacoes
    fornecedor_id = request.args.get("fornecedor_id", type=int)
    incluir_anexos = request.args.get("incluir_anexos", "1") != "0"

    if modo == "fornecedor" and fornecedor_id:
        auditoria = executar_auditoria_pnae(
            exercicio=f["exercicio"],
            data_inicio=f["data_inicio"],
            data_fim=f["data_fim"],
            incluir_comparativo=False,
        )
        auditoria["fornecedores"] = [x for x in auditoria["fornecedores"] if x["agricultor_id"] == fornecedor_id]
        auditoria["associacoes"] = [x["info_associacao"] for x in auditoria["fornecedores"] if x["info_associacao"]]
        auditoria["pendencias"] = [p for p in auditoria["pendencias"] if p["agricultor_id"] == fornecedor_id]
    else:
        auditoria = executar_auditoria_pnae(
            exercicio=f["exercicio"],
            data_inicio=f["data_inicio"],
            data_fim=f["data_fim"],
            filtro_fornecedor=f["filtro_fornecedor"],
            filtro_cpf_cnpj=f["filtro_cpf_cnpj"],
            filtro_caf=f["filtro_caf"],
            filtro_tipo=("grupo_formal" if modo == "associacoes" else f["filtro_tipo"]),
            filtro_associacao_id=f["filtro_associacao_id"],
            filtro_situacao=f["filtro_situacao"],
            filtro_chamada=f["filtro_chamada"],
            filtro_contrato_id=f["filtro_contrato_id"],
            incluir_comparativo=(modo == "completo"),
        )

    tipo_map = {
        "completo": "PDF Completo",
        "pendencias": "PDF Somente Pendências",
        "fornecedor": "PDF Individual Fornecedor",
        "associacoes": "PDF Associações/Cooperativas",
    }
    codigo_rel = _registrar_historico_auditoria(tipo_map.get(modo, "PDF Completo"), auditoria, {**f, "modo": modo, "incluir_anexos": incluir_anexos})

    periodo_txt = None
    if f["data_inicio"] or f["data_fim"]:
        periodo_txt = f"{f['data_inicio'].strftime('%d/%m/%Y') if f['data_inicio'] else 'Início'} a {f['data_fim'].strftime('%d/%m/%Y') if f['data_fim'] else 'Fim'}"

    pdf_buf = gerar_pdf_relatorio_auditoria_pnae(
        auditoria=auditoria,
        codigo_relatorio=codigo_rel,
        usuario_emissor=session.get("username", "Sistema"),
        modo=modo,
        incluir_anexos=incluir_anexos,
        periodo_texto=periodo_txt,
    )
    return Response(
        pdf_buf.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline;filename=Relatorio_Analitico_PNAE_{f['exercicio']}_{codigo_rel}.pdf"},
    )


@auditoria_pnae_bp.route("/relatorio/excel")
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def relatorio_excel():
    f = _extrair_filtros_request()
    auditoria = executar_auditoria_pnae(
        exercicio=f["exercicio"],
        data_inicio=f["data_inicio"],
        data_fim=f["data_fim"],
        filtro_fornecedor=f["filtro_fornecedor"],
        filtro_cpf_cnpj=f["filtro_cpf_cnpj"],
        filtro_caf=f["filtro_caf"],
        filtro_tipo=f["filtro_tipo"],
        filtro_associacao_id=f["filtro_associacao_id"],
        filtro_situacao=f["filtro_situacao"],
        filtro_chamada=f["filtro_chamada"],
        filtro_contrato_id=f["filtro_contrato_id"],
        incluir_comparativo=True,
    )
    codigo_rel = _registrar_historico_auditoria("Excel Detalhado (7 Abas)", auditoria, f)
    xlsx_buf = gerar_excel_auditoria_pnae(auditoria, codigo_rel, session.get("username", "Sistema"))
    return Response(
        xlsx_buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment;filename=Auditoria_PNAE_{f['exercicio']}_{codigo_rel}.xlsx"},
    )


@auditoria_pnae_bp.route("/relatorio/imprimir")
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def relatorio_imprimir():
    f = _extrair_filtros_request()
    modo = request.args.get("modo", "completo")
    auditoria = executar_auditoria_pnae(
        exercicio=f["exercicio"],
        data_inicio=f["data_inicio"],
        data_fim=f["data_fim"],
        filtro_fornecedor=f["filtro_fornecedor"],
        filtro_cpf_cnpj=f["filtro_cpf_cnpj"],
        filtro_caf=f["filtro_caf"],
        filtro_tipo=("grupo_formal" if modo == "associacoes" else f["filtro_tipo"]),
        filtro_associacao_id=f["filtro_associacao_id"],
        filtro_situacao=f["filtro_situacao"],
        filtro_chamada=f["filtro_chamada"],
        filtro_contrato_id=f["filtro_contrato_id"],
        incluir_comparativo=True,
    )
    codigo_rel = _registrar_historico_auditoria("Impressão Direta Web", auditoria, {**f, "modo": modo})
    return render_template(
        "merenda/agricultura/auditoria/relatorio_imprimir.html",
        auditoria=auditoria,
        codigo_relatorio=codigo_rel,
        modo=modo,
        usuario_emissor=session.get("username", "Sistema"),
        data_emissao=obter_data_hora_br_str(),
        formatar_moeda_br=formatar_moeda_br,
    )


@auditoria_pnae_bp.route("/historico")
@login_required
@role_required("admin", "Merenda Escolar", "Fiscal")
def historico():
    registros = PnaeAuditoriaHistorico.query.order_by(PnaeAuditoriaHistorico.data_geracao.desc()).limit(150).all()
    return render_template("merenda/agricultura/auditoria/historico.html", registros=registros)
