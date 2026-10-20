# -*- coding: utf-8 -*-
"""Prévia ao vivo do DIEx: monta o MESMO documento do PDF final (app.textos + app.pdf_docs), mesmo com o formulário incompleto.

O que ainda falta vira `[[PREENCHER: campo]]` (destacado em amarelo e com marca "RASCUNHO" na folha). Nada aqui é gravado:
a prévia não vai para saida/, não entra no histórico e não substitui a validação — o PDF final continua exigindo zero erros.

Regra de ouro (.agents/rules/anti-hallucination.md): a prévia nunca inventa dado; o que o sistema não sabe aparece como pendente.
"""

from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal

import pymupdf

from . import base, config, pdf_docs, textos
from .dinheiro import ZERO, ValorInvalido, parse_decimal, total_item
from .regras import Avaliacao, _bool, _iso_para_data

PH = textos.pendente
_RE_PEND = re.compile(r"\[\[PREENCHER: ([^\]]*)\]\]")


class PreviaIndisponivel(Exception):
    """Ainda não há o mínimo para montar a prévia (ex.: reforço sem NE escolhida)."""


def _dict(v: object) -> dict:
    return v if isinstance(v, dict) else {}


def _s(v: object) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v not in (None, "") and not isinstance(v, (dict, list)) else ""


def _dec(v: object) -> Decimal | None:
    try:
        return parse_decimal(v) if _s(v) else None
    except (ValorInvalido, ArithmeticError):
        return None


# ----------------------------------------------------------------------------- partes comuns
def _cabecalho(d: dict, av: Avaliacao) -> dict:
    if av.norm.get("cabecalho"):
        return av.norm["cabecalho"]
    c = _dict(d.get("cabecalho"))
    rq = _dict(c.get("requisitante"))
    return {"diex": _s(c.get("diex")) or PH("nº do DIEx"), "nup": _s(c.get("nup")) or PH("NUP"),
            "data": _iso_para_data(_s(c.get("data"))) or date.today(),
            "requisitante": {"posto": _s(rq.get("posto")) or PH("posto/graduação"), "nome": _s(rq.get("nome")).upper() or PH("nome do requisitante"),
                             "funcao": _s(rq.get("funcao")) or PH("função do requisitante"), "respondendo": _bool(rq.get("respondendo"))}}


def _licitacao(d: dict, av: Avaliacao) -> dict:
    if av.norm.get("licitacao"):
        return av.norm["licitacao"]
    modalidade = d.get("modalidade") if d.get("modalidade") in ("PREGAO", "DISPENSA", "INEXIGIBILIDADE", "CONTRATO") else "PREGAO"
    lic, manual = _dict(d.get("licitacao")), _dict(_dict(d.get("licitacao")).get("manual"))
    pregao = base.pregao(_s(lic.get("pregao_id"))) if _s(lic.get("pregao_id")) else None
    propria = config.unidade()["uasg_propria"]
    if modalidade == "CONTRATO":
        return {"modalidade": modalidade, "numero": _s(lic.get("numero")) or PH("nº do contrato"), "ugg": propria, "tipo_unidade": "GERENCIADORA",
                "nome_om": config.unidade()["nome_om"], "objeto_contrato": _s(lic.get("objeto")) or "Execução de contrato vigente",
                "processo_origem": _s(lic.get("processo_origem"))}
    if modalidade == "DISPENSA":
        return {"modalidade": modalidade, "numero": _s(lic.get("numero")) or PH("nº da dispensa"), "ugg": propria, "tipo_unidade": "GERENCIADORA",
                "nome_om": config.unidade()["nome_om"], "fundamento": next((f for f in config.catalogos()["fundamentos_dispensa"]
                                                                              if f["id"] == lic.get("fundamento_dispensa")), None) or {"frase": PH("fundamento da dispensa")},
                "fundamento_detalhe": _s(lic.get("fundamento_detalhe"))}
    if modalidade == "INEXIGIBILIDADE":
        return {"modalidade": modalidade, "numero": _s(lic.get("numero")) or PH("nº da inexigibilidade"), "ugg": propria, "tipo_unidade": "GERENCIADORA",
                "nome_om": config.unidade()["nome_om"], "fundamento": next((f for f in config.catalogos().get("fundamentos_inexigibilidade", [])
                                                                              if f["id"] == lic.get("fundamento_inexigibilidade")), None) or {"frase": PH("fundamento da inexigibilidade")},
                "fundamento_detalhe": _s(lic.get("fundamento_detalhe"))}
    if pregao:
        return {"modalidade": modalidade, "numero": pregao["numero"], "ugg": pregao["ugg"], "tipo_unidade": pregao["tipo_unidade"],
                "nome_om": base.nome_uasg(pregao["ugg"]) or ""}
    return {"modalidade": modalidade, "numero": _s(manual.get("numero")) or PH("pregão"), "ugg": _s(manual.get("uasg")) or PH("UASG gerenciadora"),
            "tipo_unidade": _s(manual.get("tipo_unidade")) or None, "nome_om": _s(manual.get("nome_om")) or PH("OM gerenciadora")}


def _finalidade(d: dict, av: Avaliacao) -> dict:
    if av.norm.get("finalidade"):
        return av.norm["finalidade"]
    f = _dict(d.get("finalidade"))
    texto = None
    if f.get("id") and f.get("id") != "OUTRA":
        texto = next((x["texto"] for x in base.finalidades() if x["id"] == f["id"]), None)
    elif f.get("id") == "OUTRA":
        texto = _s(f.get("outra")) or None
    return {"id": f.get("id"), "texto": (texto or PH("finalidade da aquisição")).rstrip(". "), "detalhe": _s(f.get("detalhe")).rstrip(". ")}


def _fonte(d: dict, av: Avaliacao, padrao: dict | None = None) -> dict:
    if av.norm.get("fonte"):
        return av.norm["fonte"]
    f = _dict(d.get("fonte"))
    p = padrao or {}
    return {"ug": _s(f.get("ug")) or p.get("ug") or PH("UG executora"), "nc": _s(f.get("nc")) or p.get("nc") or PH("nº da NC"),
            "nd": p.get("nd") or PH("ND"), "pi": p.get("pi") or PH("PI"), "ptres": p.get("ptres") or PH("PTRES"), "ugr": p.get("ugr") or PH("UGR")}


# ----------------------------------------------------------------------------- requisição
def _fornecedores(d: dict, av: Avaliacao) -> list[dict]:
    if av.norm.get("fornecedores"):
        return av.norm["fornecedores"]
    saida = []
    for f in d.get("fornecedores") if isinstance(d.get("fornecedores"), list) else []:
        f = _dict(f)
        itens, soma = [], None
        for it in f.get("itens") if isinstance(f.get("itens"), list) else []:
            it = _dict(it)
            qtde, vu = _dec(it.get("qtde")), _dec(it.get("valor_unit"))
            total = total_item(qtde, vu) if qtde is not None and vu is not None and qtde > 0 and vu > 0 else None
            if total is not None:
                soma = (soma or ZERO) + total
            num = _s(it.get("item"))
            itens.append({"item": int(num) if num.isdigit() else (num or None), "si": _s(it.get("si")), "und": _s(it.get("und")),
                          "descricao": _s(it.get("descricao")) or PH("descrição do item"), "qtde": qtde, "valor_unit": vu, "total": total})
        if not itens:
            itens = [{"item": None, "si": "", "und": "", "descricao": PH("itens do fornecedor"), "qtde": None, "valor_unit": None, "total": None}]
        saida.append({"cnpj": _s(f.get("cnpj")) or PH("CNPJ/CPF do fornecedor"), "nome": _s(f.get("nome")) or PH("razão social"), "itens": itens, "total": soma})
    if not saida:
        saida = [{"cnpj": PH("CNPJ/CPF do fornecedor"), "nome": PH("razão social"), "total": None,
                  "itens": [{"item": None, "si": "", "und": "", "descricao": PH("itens (escolha na ata ou inclua manualmente)"),
                             "qtde": None, "valor_unit": None, "total": None}]}]
    return saida


def _requisicao(d: dict, av: Avaliacao) -> dict:
    n = av.norm
    cab = _cabecalho(d, av)
    c, p = _dict(d.get("contrato")), _dict(d.get("pca"))
    ano = cab["data"].year if hasattr(cab["data"], "year") else date.today().year
    if d.get("modalidade") == "CONTRATO":
        contrato = n.get("contrato") or {"possui": True, "numero": _s(c.get("numero")) or _s(_dict(d.get("licitacao")).get("numero")) or PH("nº do contrato")}
    else:
        contrato = n.get("contrato") or ({"possui": True, "numero": _s(c.get("numero")) or PH("nº do contrato")} if _bool(c.get("possui")) else {"possui": False})
    if n.get("pca"):
        pca = n["pca"]
    elif _bool(p.get("consta"), padrao=True):
        nr = _s(p.get("contratacao"))
        m = re.search(r"/(\d{4})$", nr)
        pca = {"consta": True, "ano": int(m.group(1)) if m else ano, "classe": _s(p.get("classe")) or PH("classe/grupo do PCA"),
               "contratacao": nr or PH("nº da contratação no PCA")}
    else:
        pca = {"consta": False, "justificativa": _s(p.get("justificativa")) or PH("justificativa de contratação fora do PCA")}
    te = n.get("tipo_empenho") or next((t for t in config.catalogos()["tipos_empenho"] if t["id"] == d.get("tipo_empenho")), None) \
        or {"rotulo": PH("tipo de empenho")}
    fornecedores = _fornecedores(d, av)
    norm = {
        "cabecalho": cab, "objeto": n.get("objeto"), "licitacao": _licitacao(d, av), "adesao": n.get("adesao"), "finalidade": _finalidade(d, av),
        "tipo_empenho": te, "contrato": contrato, "pca": pca, "fonte": _fonte(d, av), "fornecedores": fornecedores,
        "total": n.get("total") or sum((f["total"] or ZERO for f in fornecedores), ZERO), "anexar_nc": n.get("anexar_nc", True),
    }
    return textos.montar_requisicao(norm)


# ----------------------------------------------------------------------------- reforço e anulação
def _motivo(av: Avaliacao, d: dict, rotulo: str) -> dict:
    m = av.norm.get("motivo") or {}
    return {"texto": m.get("texto") or PH(rotulo), "id": m.get("id"), "detalhe": m.get("detalhe") or _s(_dict(d.get("motivo")).get("detalhe")).rstrip(". ")}


def _reforco(d: dict, av: Avaliacao) -> dict:
    n = av.norm
    if not n.get("ne"):
        raise PreviaIndisponivel("Escolha a NE a reforçar para ver a prévia do DIEx.")
    ne = n["ne"]
    norm = {"cabecalho": _cabecalho(d, av), "ne": ne, "itens": n.get("itens") or [], "total": n.get("total") or ZERO,
            "fonte": _fonte(d, av, {"ug": ne["ug"], "nc": (ne.get("derivados") or {}).get("nc"), "nd": ne.get("nd"), "pi": ne.get("pi"),
                                    "ptres": ne.get("ptres"), "ugr": ne.get("ugr")}),
            "motivo": _motivo(av, d, "motivo do reforço")}
    return textos.montar_reforco(norm)


def _anulacao(d: dict, av: Avaliacao) -> dict:
    n = av.norm
    regs = [dict(r, tipo=r["tipo"] if r.get("tipo") in ("TOTAL", "PARCIAL") else "—") for r in (n.get("empenhos") or [])]
    if not regs:
        raise PreviaIndisponivel("Escolha a(s) NE a anular para ver a prévia do DIEx.")
    norm = {"cabecalho": _cabecalho(d, av), "empenhos": regs, "total": n.get("total") or ZERO, "reempenho": n.get("reempenho"),
            "motivo": _motivo(av, d, "motivo da anulação")}
    return textos.montar_anulacao(norm)


_MONTADORES = {"REQUISICAO": _requisicao, "REFORCO": _reforco, "ANULACAO": _anulacao}


def montar_doc(av: Avaliacao, dados: dict) -> tuple[dict, list[str]]:
    """(documento no formato de app.textos, lista dos campos pendentes). Levanta PreviaIndisponivel se ainda não dá."""
    doc = _MONTADORES[av.tipo](dados, av)
    pendentes = sorted({m.group(1) for m in _RE_PEND.finditer(repr(doc))})
    return doc, pendentes


def renderizar(av: Avaliacao, dados: dict, dpi: int = 100) -> dict:
    """Páginas da prévia como PNG em base64 (data URL) + pendentes. Tudo em memória."""
    import base64
    doc, pendentes = montar_doc(av, dados)
    buf = io.BytesIO()
    pdf_docs.gerar_pdf_documento(doc, buf, rascunho=True)
    paginas = []
    with pymupdf.open(stream=buf.getvalue(), filetype="pdf") as pdf:
        for pag in pdf:
            png = pag.get_pixmap(dpi=dpi).tobytes("png")
            paginas.append("data:image/png;base64," + base64.b64encode(png).decode("ascii"))
    return {"paginas": paginas, "pendentes": pendentes, "layout": doc.get("layout")}
