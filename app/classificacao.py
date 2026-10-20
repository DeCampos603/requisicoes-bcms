# -*- coding: utf-8 -*-
"""Sugestão de subitem e natureza de despesa (ND) para os itens da requisição.

Reaproveita o classificador do Robô Extrator de Pregões (RoboExtratorPregoes_V2/robo), que usa o
Manual de Classificação da Despesa Pública: regras curadas (regex) + similaridade ponderada, sempre
offline e com rastro dos termos que dispararam a sugestão.

O que este módulo acrescenta ao classificador:
  * restringe a sugestão ao ELEMENTO da ND da Nota de Crédito (ND 339030 → elemento 30);
  * quando a NC vem com ND 00 (ex.: 339000, "aplicações diretas" sem elemento), propõe as NDs de
    destino do detalhamento a partir do tipo do objeto e da descrição dos itens;
  * valida se o subitem informado existe no Manual para aquele elemento.

Se o classificador não for encontrado, o sistema segue funcionando sem sugestões (nada é inventado).
Variável de ambiente CLASSIFICADOR_DIR aponta para a pasta `robo` em outro local.
"""

from __future__ import annotations

import csv
import importlib
import os
import sys
import threading
import types
from functools import lru_cache
from pathlib import Path

from . import config

_trava = threading.Lock()
_mods: dict = {}

ELEMENTOS_MATERIAL = ["30", "52"]
ELEMENTOS_SERVICO = ["39", "37", "40", "35", "36", "33"]


def _pasta_robo() -> Path:
    env = os.environ.get("CLASSIFICADOR_DIR")
    return Path(env) if env else config.REPO / "RoboExtratorPregoes_V2" / "robo"


def _modulos():
    """Carrega classificador_nd e nd_referencia sem executar o __init__ do pacote do robô (Playwright/Tk)."""
    with _trava:
        if "cls" in _mods:
            return _mods.get("cls"), _mods.get("ref")
        pasta = _pasta_robo()
        if not (pasta / "classificador_nd.py").is_file():
            _mods["cls"] = _mods["ref"] = None
            return None, None
        try:
            pkg = types.ModuleType("robo_nd")
            pkg.__path__ = [str(pasta)]  # type: ignore[attr-defined]
            sys.modules["robo_nd"] = pkg
            ref = importlib.import_module("robo_nd.nd_referencia")
            cls = importlib.import_module("robo_nd.classificador_nd")
            _mods["cls"], _mods["ref"] = cls, ref
        except Exception:  # classificador quebrado não derruba o sistema
            _mods["cls"] = _mods["ref"] = None
        return _mods["cls"], _mods["ref"]


def disponivel() -> bool:
    return _modulos()[0] is not None


# ----------------------------------------------------------------------------- ND
def elemento_da_nd(nd: str) -> str:
    return nd[4:6]


def nd_indetalhada(nd: str | None) -> bool:
    """ND sem elemento de despesa (…00): 339000, 449000 — precisa de detalhamento antes do empenho."""
    return bool(nd) and len(nd) == 6 and nd[4:6] == "00"


def _fmt_nd(nd: str) -> str:
    return f"{nd[0]}.{nd[1]}.{nd[2:4]}.{nd[4:6]}"


MAPA_ELEMENTOS_PADRAO = {
    ("3", "30"): "MATERIAL DE CONSUMO",
    ("3", "39"): "OUTROS SERVIÇOS DE TERCEIROS - PESSOA JURÍDICA",
    ("3", "36"): "OUTROS SERVIÇOS DE TERCEIROS - PESSOA FÍSICA",
    ("3", "37"): "LOCAÇÃO DE MÃO-DE-OBRA",
    ("3", "40"): "SERVIÇOS DE TECNOLOGIA DA INFORMAÇÃO E COMUNICAÇÃO - PJ",
    ("3", "33"): "PASSAGENS E DESPESAS COM LOCOMOÇÃO",
    ("3", "35"): "SERVIÇOS DE CONSULTORIA",
    ("3", "14"): "DIÁRIAS - CIVIL",
    ("3", "15"): "DIÁRIAS - MILITAR",
    ("3", "92"): "DESPESAS DE EXERCÍCIOS ANTERIORES",
    ("3", "93"): "INDENIZAÇÕES E RESTITUIÇÕES",
    ("4", "52"): "EQUIPAMENTOS E MATERIAL PERMANENTE",
    ("4", "51"): "OBRAS E INSTALAÇÕES",
    ("4", "39"): "OUTROS SERVIÇOS DE TERCEIROS - PJ (CAPITAL)",
    ("4", "40"): "SERVIÇOS DE TECNOLOGIA DA INFORMAÇÃO E COMUNICAÇÃO - PJ (CAPITAL)",
}


@lru_cache(maxsize=1)
def _referencia_completa() -> tuple[dict, ...]:
    locais = [
        config.DADOS / "referencia_nd_completa.csv",
        Path(__file__).parent.parent / "dados" / "referencia_nd_completa.csv",
        _pasta_robo() / "dados" / "referencia_nd_completa.csv",
    ]
    for pasta in locais:
        if pasta.is_file():
            try:
                with pasta.open("r", encoding="utf-8-sig", newline="") as f:
                    return tuple(csv.DictReader(f, delimiter=";"))
            except Exception:
                continue
    return tuple()


def subitens_do_elemento(nd: str) -> list[dict]:
    """Subitens do Manual para o elemento da ND (grupo = 2º dígito, elemento = 5º e 6º)."""
    grupo, elem = nd[1], elemento_da_nd(nd)
    return [{"subitem": r["subitem"].zfill(2), "nome": r["subitem_nome"], "elemento_nome": r["elemento_nome"]}
            for r in _referencia_completa() if r["grupo"] == grupo and r["elemento"] == elem]


def nome_elemento(nd: str) -> str | None:
    for r in _referencia_completa():
        if r["grupo"] == nd[1] and r["elemento"] == elemento_da_nd(nd):
            return r["elemento_nome"]
    return MAPA_ELEMENTOS_PADRAO.get((nd[1], elemento_da_nd(nd)))


def nds_candidatas(nd_nc: str, objeto: str | None) -> list[dict]:
    """NDs de destino possíveis para detalhar uma NC com ND 00 (mesma categoria/grupo/modalidade)."""
    if not nd_indetalhada(nd_nc):
        return []
    elementos = {"MATERIAL": ELEMENTOS_MATERIAL, "SERVICO": ELEMENTOS_SERVICO}.get(objeto or "", ELEMENTOS_MATERIAL + ELEMENTOS_SERVICO)
    saida = []
    for e in elementos:
        nd = nd_nc[:4] + e
        nome = nome_elemento(nd) or f"ELEMENTO DE DESPESA {e}"
        saida.append({"nd": nd, "elemento": e, "nome": nome})
    return saida


# ----------------------------------------------------------------------------- sugestões
def _tipo(objeto: str | None) -> str | None:
    return {"MATERIAL": "Material", "SERVICO": "Serviço"}.get(objeto or "")


@lru_cache(maxsize=512)
def _classificar(descricao: str, tipo_item: str | None) -> tuple:
    cls, _ = _modulos()
    if cls is None:
        return tuple()
    return tuple(cls.classificar(descricao, tipo_item, top=600))


def _regra(descricao: str):
    cls, _ = _modulos()
    return cls.aplicar_regra(descricao) if cls else None


def sugerir_subitens(descricao: str, nd: str | None, objeto: str | None = None, top: int = 4) -> dict:
    """Sugere subitens para `descricao` dentro do elemento da `nd` (ND da NC ou ND de destino).

    Devolve {disponivel, nd, elemento, sugestoes[], recomendada, global_} onde `global_` é a melhor
    classificação SEM restringir ao elemento (usada para avisar quando o item parece de outra ND).
    """
    cls, _ = _modulos()
    saida: dict = {"disponivel": cls is not None, "nd": nd, "elemento": elemento_da_nd(nd) if nd else None,
                   "sugestoes": [], "recomendada": None, "global_": None}
    if cls is None or not descricao or not descricao.strip():
        return saida
    tipo = _tipo(objeto)
    # melhor classificação global (restrita só pelo tipo do objeto)
    glob = cls.sugerir(descricao, tipo)
    if glob.get("ND_Sugerida"):
        nd_g = "".join(ch for ch in glob["ND_Sugerida"] if ch.isdigit())[:6]
        m = glob["Subitem_Sugerido"].split(" - ", 1)
        sub_g = m[0].split(".")[-1] if m and "." in m[0] else None
        saida["global_"] = {"nd": nd_g, "subitem": sub_g, "nome": m[1] if len(m) > 1 else None,
                            "confianca": glob.get("Confianca_ND"), "termos": glob.get("ND_Termos")}
    if not nd or nd_indetalhada(nd):
        return saida
    elem = elemento_da_nd(nd)
    sugestoes: list[dict] = []
    regra = _regra(descricao)
    if regra and regra["elemento"] == elem:
        ref_ = _modulos()[1].buscar(regra["elemento"], regra["subitem"])
        if ref_:
            sugestoes.append({"subitem": ref_["subitem"], "nome": ref_["subitem_nome"], "score": None,
                              "confianca": "Alta (regra)", "termos": [regra["obs"] or "regra curada"], "origem": "regra"})
    candidatos = [r for r in _classificar(descricao, None) if r["elemento"] == elem]
    conf = cls._confianca(candidatos[:3]) if candidatos else "Baixa"
    for k, r in enumerate(candidatos[:top]):
        if any(s["subitem"] == r["subitem"] for s in sugestoes):
            continue
        sugestoes.append({"subitem": r["subitem"], "nome": r["subitem_nome"], "score": r["score"],
                          "confianca": conf if k == 0 else "Alternativa", "termos": r["termos"], "origem": "similaridade"})
    saida["sugestoes"] = sugestoes[:top]
    if sugestoes and sugestoes[0]["confianca"] in ("Alta (regra)", "Alta", "Média"):
        saida["recomendada"] = sugestoes[0]["subitem"]
    return saida


def verificar_subitem(descricao: str, si: str, nd: str, objeto: str | None = None) -> dict:
    """Confere o SI informado: existe no Manual? coincide com a sugestão? o item parece de outra ND?"""
    s = sugerir_subitens(descricao, nd, objeto)
    validos = {x["subitem"] for x in subitens_do_elemento(nd)}
    s["si_existe"] = (si in validos) if validos else None
    s["si_informado"] = si
    rec = s.get("recomendada")
    s["si_diverge"] = bool(rec and rec != si and s["sugestoes"] and s["sugestoes"][0]["confianca"] in ("Alta (regra)", "Alta"))
    g = s.get("global_")
    # só avisa de "outra ND" quando a classificação global é Alta (regra curada ou similaridade forte): com
    # confiança Média o classificador erra com frequência (ex.: serviço de manutenção lido como locação)
    s["nd_diverge"] = bool(g and g.get("nd") and g["nd"] != nd and (g.get("confianca") or "").startswith("Alta"))
    return s


def nd_de_destino_sugerida(descricoes: list[str], nd_nc: str, objeto: str | None) -> dict | None:
    """Para NC com ND 00: ND de destino mais frequente entre as classificações dos itens (e as alternativas)."""
    candidatas = nds_candidatas(nd_nc, objeto)
    if not candidatas:
        return None
    votos: dict[str, int] = {}
    for d in descricoes:
        s = sugerir_subitens(d, None, objeto)
        g = s.get("global_")
        if g and g.get("nd") and g["nd"][:4] == nd_nc[:4] and (g.get("confianca") or "").startswith("Alta"):
            votos[g["nd"]] = votos.get(g["nd"], 0) + 1
    if votos:
        melhor = max(votos, key=votos.get)
        return {"nd": melhor, "votos": votos, "candidatas": candidatas}
    return {"nd": None, "votos": {}, "candidatas": candidatas}
