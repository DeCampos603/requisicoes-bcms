# -*- coding: utf-8 -*-
"""Textos dos três documentos (requisição, reforço, anulação) — redação revisada à luz da Lei 14.133/2021.

Cada função recebe o dicionário NORMALIZADO produzido por app.regras (já validado) e devolve uma
estrutura de blocos que app.pdf_docs desenha. A fundamentação de cada trecho está em
docs/REVISAO-LEGAL.md; os dispositivos citados foram conferidos no texto do Planalto (Lei 14.133/2021,
Lei 4.320/1964, Decreto 93.872/1986, Decreto 11.462/2023) e na Portaria Ministerial 305/1995 (IG 12-02).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape as _esc

from . import config
from .dinheiro import ZERO, brl, fmt_qtd, fmt_vu

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
IG_1202 = "art. 13 e seu parágrafo único das IG 12-02 (Portaria nº 305-Min Ex, de 24 de maio de 1995)"


def pendente(campo: str) -> str:
    """Marcador de campo ainda não preenchido (só aparece na prévia; o PDF final nunca sai com ele)."""
    return f"[[PREENCHER: {campo}]]"


def layout() -> str:
    """"novo" (numeração 1/1.1/1.2, quadro de condições, tabela alinhada, página mais compacta) ou "modelo" (cópia fiel do DOCX)."""
    return "modelo" if config.unidade().get("layout_diex") == "modelo" else "novo"


def data_extenso(d: date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}."


def data_br(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _e(txt: object) -> str:
    return _esc(str(txt if txt is not None else ""))


def _b(txt: object) -> str:
    return f"<b>{_e(txt)}</b>"


def _dt_iso(txt: str | None) -> str:
    if not txt:
        return ""
    return f"{txt[8:10]}/{txt[5:7]}/{txt[:4]}"


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


# ----------------------------------------------------------------------------- partes comuns
def _identificacao(norm: dict, rotulo_doc: str, assunto: str, do_funcao: str | None = None) -> dict:
    cab = norm["cabecalho"]
    rq = cab["requisitante"]
    return {
        "titulo": f"DIEx {rotulo_doc} nº {cab['diex']}",
        "nup": cab["nup"],
        "data": data_extenso(cab["data"]),
        "do": do_funcao or rq["funcao"],
        "ao": config.unidade()["destinatario"],
        "assunto": assunto,
    }


def _assinatura(norm: dict) -> dict:
    rq = norm["cabecalho"]["requisitante"]
    funcao = rq["funcao"]
    if rq.get("respondendo"):
        for ini, novo in (("Chefe da ", "Respondendo pela Chefia da "), ("Chefe do ", "Respondendo pela Chefia do "),
                          ("Chefe de ", "Respondendo pela Chefia de ")):
            if funcao.startswith(ini):
                funcao = novo + funcao[len(ini):]
                break
        else:
            funcao = f"Respondendo pela função de {funcao}"
    posto = rq.get("posto")
    nome = f"{rq['nome']} – {posto}" if posto else rq["nome"]
    if nome.strip().upper() == (funcao or "").strip().upper():
        return {"nome": "Requisitante", "funcao": ""}
    return {"nome": nome, "funcao": funcao}


def _autoridade(chave: str) -> dict:
    a = config.unidade()[chave]
    return {"nome": f"{a['nome']} – {a['posto']}", "funcao": a["funcao"]}


def _despacho(texto_fiscal: list[str], texto_od: list[str]) -> dict:
    return {
        "fiscal": {"titulo": "APROVAÇÃO DO FISCAL ADMINISTRATIVO", "texto": texto_fiscal, **_autoridade("fiscal_administrativo")},
        "od": {"titulo": "DESPACHO DO ORDENADOR DE DESPESAS", "texto": texto_od, **_autoridade("ordenador_despesas")},
    }


def _linha_fonte(f: dict) -> str:
    nd = f["nd"]
    partes = f"{_b(f['ug'])} - {_b(f['nc'])} – ND: {_b(nd)} – PI: {_b(f['pi'] or '—')} PTRES: {_b(f['ptres'])} UGR: {_b(f['ugr'])}"
    return partes


def _texto_detalhamento(f: dict, valor: Decimal) -> str | None:
    det = f.get("detalhamento")
    if not f.get("indetalhada") or not det:
        return None
    el = f" – {det['nome_elemento']}" if det.get("nome_elemento") else ""
    if det.get("nc"):
        como = f"já detalhado pela NC {_b(det['nc'])}"
    else:
        como = "mediante NC de detalhamento de crédito, a ser emitida antes do empenho"
    return (f"O crédito da NC encontra-se na ND {_b(det['nd_origem'])} (sem elemento de despesa) e será detalhado para a ND "
            f"{_b(det['nd_destino'])}{_e(el)}, no valor de {_b(brl(valor))}, {como}.")


def _tabela_credito_dados(f: dict, total: Decimal | None) -> dict:
    det = _texto_detalhamento(f, total) if total is not None and f.get("indetalhada") else None
    return {
        "nc": f.get("nc") or pendente("nº da NC"),
        "emitente": f.get("emitente") or f.get("ug") or pendente("UG emitente"),
        "ug": f.get("ug") or pendente("UG executora"),
        "nd": f.get("nd") or pendente("ND"),
        "pi": f.get("pi") or "—",
        "ptres": f.get("ptres") or pendente("PTRES"),
        "ugr": f.get("ugr") or pendente("UGR"),
        "fonte_rec": f.get("fonte_rec") or "—",
        "valor": brl(total) if total is not None else "—",
        "detalhamento": det,
    }


def _tabela_itens(fornecedores: list[dict]) -> list[dict]:
    tabelas = []
    for f in fornecedores:
        linhas = []
        for it in f["itens"]:
            linhas.append({
                "item": f"{it['item']:02d}" if isinstance(it["item"], int) else str(it["item"] if it["item"] is not None else "—"),
                "si": it["si"] or "—", "descricao": it["descricao"], "und": it["und"] or "—",
                # na prévia, valor ainda não digitado aparece como "—" (o PDF final só sai com tudo preenchido)
                "qtde": fmt_qtd(it["qtde"]) if it["qtde"] is not None else "—",
                "valor_unit": fmt_vu(it["valor_unit"]) if it["valor_unit"] is not None else "—",
                "total": brl(it["total"]) if it["total"] is not None else "—",
            })
        tabelas.append({"cabecalho": f"{f['cnpj']} - {f['nome']}", "linhas": linhas, "total": brl(f["total"]) if f["total"] is not None else "—"})
    return tabelas


# ----------------------------------------------------------------------------- REQUISIÇÃO
def _frase_licitacao(lic: dict) -> str:
    num = lic["numero"]
    if lic["modalidade"] == "DISPENSA":
        fund = lic.get("fundamento") or {}
        base_ = fund.get("frase", "art. 75 da Lei 14.133/2021")
        if lic.get("fundamento_detalhe"):
            base_ = f"art. 75, {lic['fundamento_detalhe']}, da Lei 14.133/2021"
        return f"{_b('Dispensa nº ' + num)} ({_e(base_)})"
    if lic["modalidade"] == "INEXIGIBILIDADE":
        fund = lic.get("fundamento") or {}
        base_ = fund.get("frase", "art. 74 da Lei 14.133/2021")
        if lic.get("fundamento_detalhe"):
            base_ = f"art. 74, {lic['fundamento_detalhe']}, da Lei 14.133/2021"
        return f"{_b('Inexigibilidade nº ' + num)} ({_e(base_)})"
    if lic["modalidade"] == "CONTRATO":
        return f"{_b('Contrato nº ' + num)} (art. 89 da Lei 14.133/2021)"
    return _b(f"Pregão nº {num}")


def _anexos_requisicao(norm: dict) -> list[str] | None:
    """Anexos na mesma ordem em que o PDF final os junta: instrução da adesão (se houver) e a NC."""
    lista = [d["anexo"] for d in ((norm.get("adesao") or {}).get("documentos") or [])]
    if norm.get("anexar_nc"):
        lista.append("Cópia da Nota de Crédito.")
    return lista or None


def montar_requisicao(norm: dict) -> dict:
    lic = norm["licitacao"]
    novo = layout() == "novo"
    obj_txt = {"MATERIAL": "AQUISIÇÃO DE MATERIAL", "SERVICO": "CONTRATAÇÃO DE SERVIÇO"}.get(norm["objeto"], pendente("objeto"))
    tipo_frase = next((t["frase"] for t in config.catalogos()["tipos_unidade"] if t["id"] == lic["tipo_unidade"]), pendente("tipo de unidade"))
    fin = norm["finalidade"]
    necessidade = fin["texto"] + (f" ({fin['detalhe']})" if fin.get("detalhe") else "")
    om = lic.get("nome_om") or ""
    ugg = lic["ugg"]
    pars: list[str] = []
    if lic["modalidade"] == "CONTRATO":
        abertura = (
            f"1. Nos termos do {IG_1202}, solicito autorização para a emissão de Nota de Empenho para {_b(obj_txt)}, "
            f"em execução do {_frase_licitacao(lic)}, da UASG {_b(ugg)} – {_b(om)}, a fim de atender à necessidade de {_e(necessidade)} "
            f"do Batalhão Central de Manutenção e Suprimento."
        )
    else:
        abertura = (
            f"1. Nos termos do {IG_1202}, solicito autorização para {_b(obj_txt)}, como {_b(tipo_frase)}, "
            f"do {_frase_licitacao(lic)}, da UASG {_b(ugg)} – {_b(om)}, a fim de atender à necessidade de {_e(necessidade)} "
            f"do Batalhão Central de Manutenção e Suprimento."
        )
    responsabilidade = ("A requisição importa a responsabilidade da autoridade requisitante "
                        "quanto à quantidade solicitada e à necessidade do objeto (art. 13, caput, das IG 12-02).")
    if novo:  # a solicitação (1) e a responsabilidade da requisição (1.1) em itens separados
        pars += [abertura, f"1.1 {responsabilidade}"]
    else:
        pars.append(f"{abertura} {responsabilidade}")
    extras = []
    if lic["modalidade"] in ("DISPENSA", "INEXIGIBILIDADE"):
        extras.append("A contratação direta será instruída nos termos do art. 72 da Lei 14.133/2021.")
    elif lic["modalidade"] == "CONTRATO":
        if lic.get("processo_origem"):
            extras.append(f"Processo administrativo de origem: {_e(lic['processo_origem'])}.")
    adesao = norm.get("adesao")
    if lic["tipo_unidade"] == "NAO_PARTICIPANTE":
        aut = _dt_iso(adesao["autorizacao_data"].isoformat()) if adesao and adesao.get("autorizacao_data") else ""
        extras.append(
            f"Adesão à ata autorizada pelo órgão gerenciador (UASG {_e(ugg)}) em {_e(aut)}, após o aceite prévio do fornecedor "
            f"(art. 86, § 2º, da Lei 14.133/2021 e art. 31 do Decreto 11.462/2023); a contratação será efetivada em até 90 dias "
            f"da autorização, observada a vigência da ata (art. 31, § 2º)."
        )
    if extras:
        if novo:  # 1.2, 1.3...: cada providência/condição da contratação em item próprio
            pars += [f"1.{k} {ex}" for k, ex in enumerate(extras, 2)]
        else:
            pars.append(" ".join(extras))

    f = norm.get("fonte")
    tab_credito = None
    if novo and f:
        l2 = "2. Fonte de Recurso (indicação de crédito orçamentário, art. 150 da Lei 14.133/2021):"
        pars.append(l2)
        tab_credito = _tabela_credito_dados(f, norm.get("total"))
    else:
        l2 = f"2. Fonte de Recurso (indicação de crédito orçamentário, art. 150 da Lei 14.133/2021): {_linha_fonte(f)}." if f else f"2. Fonte de Recurso (indicação de crédito orçamentário, art. 150 da Lei 14.133/2021): {pendente('crédito orçamentário')}."
        det = _texto_detalhamento(f, norm["total"]) if f else None
        pars.append(l2 + (" " + det if det else ""))

    # Itens 3 a 6 (tipo de empenho, contrato, PCA). Contrato e PCA: sem citação de dispositivo no PDF (decisão do usuário,
    # 02/10/2026); as conferências seguem na tela.
    te = norm["tipo_empenho"]
    i_tipo = f"3. Tipo de Empenho: {_b(te['rotulo'].upper())}."
    c = norm["contrato"]
    i_contrato = (f"4. Contrato: {_b('SIM')} – nº {_b(c['numero'])}." if c["possui"] else f"4. Contrato: {_b('NÃO')}.")
    p = norm["pca"]
    if p["consta"]:
        i_pca = [f"5. Classe/Grupo PCA {p['ano']}: {_b(p['classe'])}.", f"6. Nr da contratação PCA {p['ano']}: {_b(p['contratacao'])}."]
    else:
        i_pca = [f"5. Plano de Contratações Anual: {_b('contratação não prevista no PCA')} – justificativa: {_e(p['justificativa'])}."]
    quadro = None
    if novo:  # tipo de empenho e contrato (curtos) lado a lado; PCA em linha própria (a classe pode ser um nome longo)
        quadro = [(i_tipo, i_contrato)] + [(x, None) for x in i_pca]
    else:
        pars += [i_tipo, i_contrato, *i_pca]

    n_itens = sum(len(x["itens"]) for x in norm["fornecedores"])
    if norm["objeto"] not in ("MATERIAL", "SERVICO"):  # objeto ainda não escolhido (só na prévia)
        objeto_pl, verbo = f"do {pendente('objeto')} solicitado", f"{pendente('aquisição/contratação')}"
    else:
        objeto_pl = "do material solicitado" if norm["objeto"] == "MATERIAL" else "do serviço solicitado"
        if n_itens > 1:
            objeto_pl = "dos materiais solicitados" if norm["objeto"] == "MATERIAL" else "dos serviços solicitados"
        verbo = "aquisição" if norm["objeto"] == "MATERIAL" else "contratação"
    fiscal = [f"Aprovo a {verbo} {objeto_pl}, conforme descrito na requisição."]
    od = [f"1. Autorizo a {verbo} {objeto_pl}, conforme descrito;",
          "2. Determino a abertura do processo administrativo (IG 12-02, art. 13, parágrafo único) e que a requisição seja enviada à "
          "SALC desta UG, para que adote as providências cabíveis, de acordo com as normas em vigor;"]
    return {
        "tipo": "REQUISICAO",
        "identificacao": _identificacao(norm, "Requisitório", "Solicitação de empenho."),
        "layout": layout(),
        "modo_assinatura": norm["cabecalho"].get("modo_assinatura", "DIGITAL"),
        "anexos": _anexos_requisicao(norm),
        "paragrafos": pars,
        "tabela_credito": tab_credito,
        "quadro": quadro,
        "tabelas": _tabela_itens(norm["fornecedores"]),
        "total_geral": brl(norm["total"]) if len(norm["fornecedores"]) > 1 else None,
        "assinatura": _assinatura(norm),
        "despacho": _despacho(fiscal, od),
        "rodape": f"DIEx Requisitório nº {norm['cabecalho']['diex']} – NUP: {norm['cabecalho']['nup']}",
        "arquivo_base": f"Requisicao_{norm['cabecalho']['diex']}",
    }


# ----------------------------------------------------------------------------- REFORÇO
def montar_reforco(norm: dict) -> dict:
    ne = norm["ne"]
    f = norm.get("fonte")
    novo = layout() == "novo"
    doc, nome = ne["doc_favorecido"]
    cnpj_nome = f"{doc} - {nome}"
    total = norm["total"]
    apos = ne["valor_atual"] + total
    mot = norm["motivo"]
    necessidade = mot["texto"] + (f" ({mot['detalhe']})" if mot.get("detalhe") else "")
    pars = [
        f"1. Solicito autorização para o reforço do empenho nº {_b(ne['chave'])} (UG {_e(ne['ug'])}), emitido em "
        f"{_e(ne['data_emissao'][:10] if ne.get('data_emissao') else '')} em favor de {_b(cnpj_nome)}, no valor de {_b(brl(total))}, "
        f"conforme a tabela abaixo, pelo seguinte motivo: {_e(necessidade)}.",
        f"2. Valor atual do empenho: {_b(brl(ne['valor_atual']))}; valor do reforço: {_b(brl(total))}; valor após o reforço: {_b(brl(apos))}.",
    ]
    tab_credito = None
    if novo and f:
        l3 = "3. Fonte de Recurso (indicação de crédito orçamentário, art. 150 da Lei 14.133/2021):"
        pars.append(l3)
        tab_credito = _tabela_credito_dados(f, total)
        pars.append("O reforço será emitido na mesma célula orçamentária do empenho original e limitado ao crédito disponível "
                    "(art. 59 da Lei 4.320/1964; art. 26 do Decreto 93.872/1986).")
    else:
        l3 = f"3. Fonte de Recurso (indicação de crédito orçamentário, art. 150 da Lei 14.133/2021): {_linha_fonte(f)}." if f else f"3. Fonte de Recurso (indicação de crédito orçamentário, art. 150 da Lei 14.133/2021): {pendente('crédito orçamentário')}."
        det = _texto_detalhamento(f, total) if f else None
        pars.append(l3 + (" " + det if det else "") +
                    " O reforço será emitido na mesma célula orçamentária do empenho original e limitado ao crédito disponível "
                    "(art. 59 da Lei 4.320/1964; art. 26 do Decreto 93.872/1986).")
    if ne.get("tipo_empenho"):
        pars.append(f"4. Tipo de Empenho (original): {_b(ne['tipo_empenho'].upper())}.")
    der = ne.get("derivados") or {}
    if der.get("pregao"):
        ug_p = f" da UASG {der['uasg_pregao']}" if der.get("uasg_pregao") else ""
        pars.append(f"5. Vinculação: Pregão nº {_b(der['pregao'])}{_e(ug_p)}, conforme a descrição do empenho original.")
    fiscal = [f"Aprovo o reforço do empenho nº {ne['chave']}, conforme descrito na requisição."]
    od = [f"1. Autorizo o reforço do empenho nº {ne['chave']}, conforme descrito;",
          "2. Seja enviada a requisição à SALC desta UG, para que adote as providências cabíveis, de acordo com as normas em vigor;"]
    anexos = [f"Cópia da Nota de Empenho nº {ne['chave']}."]
    if f and f.get("nc") and f["nc"] != (ne.get("derivados") or {}).get("nc"):
        anexos.append(f"Cópia da Nota de Crédito nº {f['nc']}.")
    elif f and f.get("nc"):
        anexos.append(f"Cópia da Nota de Crédito nº {f['nc']}.")
    itens = [{"item": it.get("item_compra") or it["seq"], "si": it["si"], "descricao": it["descricao"], "und": it["und"],
              "qtde": it["qtde"], "valor_unit": it["valor_unit"], "total": it["total"]} for it in norm["itens"]]
    tabela = _tabela_itens([{"cnpj": doc, "nome": nome, "itens": itens, "total": total}])
    return {
        "tipo": "REFORCO",
        "layout": layout(),
        "identificacao": _identificacao(norm, "Requisitório", "Solicitação de reforço de empenho."),
        "modo_assinatura": norm["cabecalho"].get("modo_assinatura", "DIGITAL"),
        "anexos": anexos,
        "paragrafos": pars,
        "tabela_credito": tab_credito,
        "tabelas": tabela,
        "total_geral": None,
        "assinatura": _assinatura(norm),
        "despacho": _despacho(fiscal, od),
        "rodape": f"DIEx Requisitório nº {norm['cabecalho']['diex']} – NUP: {norm['cabecalho']['nup']}",
        "arquivo_base": f"Reforco_{norm['cabecalho']['diex']}",
    }


# ----------------------------------------------------------------------------- ANULAÇÃO
def montar_anulacao(norm: dict) -> dict:
    regs = norm["empenhos"]
    mot = norm["motivo"]
    necessidade = mot["texto"] + (f" ({mot['detalhe']})" if mot.get("detalhe") else "")
    n = len(regs)
    todas_totais = all(r["tipo"] == "TOTAL" for r in regs)
    qual = "total" if todas_totais else ("parcial" if all(r["tipo"] == "PARCIAL" for r in regs) else "total ou parcial")
    pars = [
        f"1. Solicito autorização para a anulação {qual} {_plural(n, 'do empenho abaixo relacionado', 'dos empenhos abaixo relacionados')}, "
        f"no valor total de {_b(brl(norm['total']))}, pelo seguinte motivo: {_e(necessidade)}.",
        "2. A anulação importa a redução ou o cancelamento do compromisso que originou o empenho, revertendo a importância à respectiva "
        "dotação e desonerando o limite de saques da UG (art. 28 do Decreto 93.872/1986; art. 38 da Lei 4.320/1964).",
        "3. O valor a anular não alcança parcela já liquidada: o saldo a liquidar de cada empenho foi conferido no SIAFI, conforme a tabela abaixo.",
    ]
    if norm.get("reempenho"):
        pars.append("4. Será encaminhada nova requisição para o reempenho do crédito devolvido à Nota de Crédito de origem.")
    # agrupa por favorecido, mantendo a ordem
    grupos: dict[str, dict] = {}
    for r in regs:
        doc, nome = r["doc_favorecido"]
        g = grupos.setdefault(f"{doc}|{nome}", {"cabecalho": f"{doc} - {nome}", "linhas": [], "total": ZERO})
        detalhe = f"UG {r['ug']} · {r['tipo'].capitalize()} · saldo atual {brl(r['valor_atual'])}"
        if r["liquidado"]:
            detalhe += f" · já liquidado {brl(r['liquidado'])}"
        if r.get("itens_anulados"):
            itens_str = "; ".join(f"Item {it['item_compra']}: {brl(it['valor'])}" for it in r["itens_anulados"])
            detalhe += f" · Itens a anular: {itens_str}"
        g["linhas"].append({"ne": r["chave"], "detalhe": detalhe, "valor": brl(r["valor_anular"]) if r["valor_anular"] is not None else "—"})
        g["total"] += r["valor_anular"] or ZERO  # valor ainda não informado (só na prévia): não entra na soma
    tabelas = [{"cabecalho": g["cabecalho"], "linhas": g["linhas"], "total": brl(g["total"])} for g in grupos.values()]
    fiscal = [f"Aprovo a anulação {_plural(n, 'do empenho solicitado', 'dos empenhos solicitados')}, conforme descrito na requisição."]
    od = [f"1. Autorizo a anulação {_plural(n, 'do empenho', 'dos empenhos')}, conforme descrito;",
          "2. Seja enviada a requisição à SALC desta UG, para que adote as providências cabíveis, de acordo com as normas em vigor;"]
    return {
        "tipo": "ANULACAO",
        "layout": layout(),
        "identificacao": _identificacao(norm, "Requisitório", "Solicitação de anulação de empenho."),
        "modo_assinatura": norm["cabecalho"].get("modo_assinatura", "DIGITAL"),
        "anexos": [_plural(n, "Cópia da Nota de Empenho.", "Cópias das Notas de Empenho.")],
        "paragrafos": pars,
        "tabelas": tabelas,
        "total_geral": brl(norm["total"]) if len(tabelas) > 1 else None,
        "assinatura": _assinatura(norm),
        "despacho": _despacho(fiscal, od),
        "rodape": f"DIEx Requisitório nº {norm['cabecalho']['diex']} – NUP: {norm['cabecalho']['nup']}",
        "arquivo_base": f"Anulacao_{norm['cabecalho']['diex']}",
    }


MONTADORES = {"REQUISICAO": montar_requisicao, "REFORCO": montar_reforco, "ANULACAO": montar_anulacao}
