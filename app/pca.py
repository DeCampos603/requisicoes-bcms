# -*- coding: utf-8 -*-
"""PCA (Plano de Contratações Anual) do BCMS: consulta na API pública do Compras.gov.br e sugestão de preenchimento.

Fontes (API de dados abertos, sem autenticação; o parâmetro `orgao` é o CNPJ do órgão):
  /modulo-pgc/1_consultarPgcDetalhe          itens do PCA publicados no PGC/PNCP, por órgão, ano e UASG
  /modulo-material/4_consultarItemMaterial   classe do item (CATMAT)
  /modulo-servico/6_consultarItemServico     grupo/classe do serviço (CATSER)

O nº da contratação do modelo ("160329-7/2025") é UASG-numeroArtefato/anoArtefato, e a "Classe/Grupo" é a
classe do material (ou o grupo/classe do serviço) no catálogo — por isso o sistema consegue SUGERIR os dois campos:
a classe vem do próprio item (CATMAT/CATSER) e a contratação é ranqueada por classe, item e semelhança de objeto.

Observação: o Decreto 10.947/2022 (art. 1º, p.ú.) dispensa os Comandos Militares do PGC, mas o Comando do Exército
publica o PCA (2025: 204 itens do BCMS em 16 contratações). Se um ano não estiver publicado para o BCMS (ex.: 2026 em
out/2026), o sistema avisa e sugere a partir do ano anterior.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from tenacity import retry, stop_after_attempt, wait_exponential

from . import config

BASE = "https://dadosabertos.compras.gov.br"
EP_PGC = "/modulo-pgc/1_consultarPgcDetalhe"
EP_MATERIAL = "/modulo-material/4_consultarItemMaterial"
EP_SERVICO = "/modulo-servico/6_consultarItemServico"
USER_AGENT = "RequisicoesEmpenhoBCMS/1.0 (+uso interno BCMS)"
TAM_PAGINA = 500

Progresso = Callable[[str], None]


class ApiIndisponivel(RuntimeError):
    """A API pública não respondeu (rede, timeout ou erro HTTP)."""


# ----------------------------------------------------------------------------- HTTP
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
def _get_com_retry(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - URL de base fixa (https)
        return json.loads(r.read().decode("utf-8"))


def _get_json(caminho: str, params: dict, timeout: float = 60, tentativas: bool = True) -> dict:
    url = f"{BASE}{caminho}?{urllib.parse.urlencode(params)}"
    try:
        if tentativas:
            return _get_com_retry(url, timeout)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ApiIndisponivel(f"{type(e).__name__}: {e}") from e


# ----------------------------------------------------------------------------- armazenamento
def _arquivo(nome: str) -> Path:
    return config.DADOS / nome


def _ler(nome: str, padrao: dict) -> dict:
    arq = _arquivo(nome)
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def _gravar(nome: str, dados: dict) -> None:
    import os
    import threading
    config.DADOS.mkdir(parents=True, exist_ok=True)
    destino = _arquivo(nome)
    with config.ARQUIVO_LOCK:
        tmp = destino.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(destino)


def _config_pca() -> dict:
    return config.unidade().get("pca", {})


# ----------------------------------------------------------------------------- importação do PCA
def _classe_do_item(r: dict) -> dict | None:
    """Classe do material, ou classe/grupo do serviço, de uma linha do PCA."""
    if r.get("codigoClasseMaterial"):
        return {"codigo": str(r["codigoClasseMaterial"]), "nome": r.get("nomeClasseMaterial")}
    if r.get("codigoClasseServico"):
        return {"codigo": str(r["codigoClasseServico"]), "nome": r.get("nomeClasseServico")}
    if r.get("codigoGrupoServico"):
        return {"codigo": str(r["codigoGrupoServico"]), "nome": r.get("nomeGrupoServico")}
    return None


def normalizar_pca(linhas: list[dict], uasg: str) -> dict:
    """Agrupa as linhas do PGC por contratação (numeroArtefato)."""
    contratacoes: dict[str, dict] = {}
    for r in linhas:
        n = r.get("numeroArtefato")
        ano = r.get("anoArtefato")
        if n is None or ano is None:
            continue
        c = contratacoes.setdefault(str(n), {
            "numero": int(n), "ano": int(ano), "codigo": f"{uasg}-{n}/{ano}", "objetos": [], "projetos": [], "itens": [],
            "atualizado": None,
        })
        for chave, campo in (("objetos", "descricaoObjetoDfd"), ("projetos", "tituloProjetoCompra")):
            v = (r.get(campo) or "").strip()
            if v and v not in c[chave]:
                c[chave].append(v)
        classe = _classe_do_item(r)
        c["itens"].append({
            "tipo": r.get("tipoItem"), "classe": classe, "catalogo": str(r["codigoItemCatalogo"]) if r.get("codigoItemCatalogo") else None,
            "descricao": (r.get("descricaoItemCatalogo") or "").strip() or None, "dfd": r.get("ordemDfd"),
            "qtd": r.get("quantidadeItem"), "valor_unitario": r.get("valorUnitarioItem"), "valor_total": r.get("valorTotalItem"),
        })
        atual = r.get("dataHoraAtualizacaoItem") or r.get("dataHoraAtualizacaoArtefato")
        if atual and (c["atualizado"] is None or atual > c["atualizado"]):
            c["atualizado"] = atual
    for c in contratacoes.values():
        vistos: dict[str, dict] = {}
        for it in c["itens"]:
            if it["classe"]:
                vistos.setdefault(it["classe"]["codigo"], it["classe"])
        c["classes"] = list(vistos.values())
        c["n_itens"] = len(c["itens"])
        c["valor_total"] = round(sum(i["valor_total"] or 0 for i in c["itens"]), 2)
    return contratacoes


def importar_pca(progresso: Progresso = print, anos: list[int] | None = None) -> dict:
    cfg = _config_pca()
    orgao, uasg = cfg.get("orgao_cnpj"), cfg.get("uasg") or config.unidade()["uasg_propria"]
    if not orgao:
        raise ValueError("config/unidade.json › pca.orgao_cnpj não configurado")
    atual = datetime.now().year
    anos = anos or [atual - 1, atual]
    dados = _ler("pca.json", {"anos": {}})
    resumo: dict = {}
    for ano in anos:
        linhas: list[dict] = []
        pagina, total = 1, 1
        while pagina <= total:
            d = _get_json(EP_PGC, {"pagina": pagina, "tamanhoPagina": TAM_PAGINA, "orgao": orgao, "anoPcaProjetoCompra": ano, "codigoUasg": uasg})
            total = d.get("totalPaginas", 0) or 0
            linhas += d.get("resultado", [])
            pagina += 1
            if total == 0:
                break
        contratacoes = normalizar_pca(linhas, uasg)
        posicao = max((c["atualizado"] for c in contratacoes.values() if c["atualizado"]), default=None)
        if contratacoes:
            dados["anos"][str(ano)] = {"posicao": posicao, "total_itens": len(linhas), "importado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                       "contratacoes": contratacoes}
        else:  # ano ainda não publicado para a UASG: registra a consulta, sem apagar o que havia
            dados["anos"].setdefault(str(ano), {"posicao": None, "total_itens": 0, "contratacoes": {}})
            dados["anos"][str(ano)]["consultado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        resumo[str(ano)] = {"contratacoes": len(contratacoes), "itens": len(linhas)}
        progresso(f"  PCA {ano}: {len(contratacoes)} contratação(ões), {len(linhas)} item(ns)" + ("" if contratacoes else " — não publicado para a UASG"))
    dados.update({"orgao": orgao, "uasg": uasg, "fonte": f"{BASE}{EP_PGC}"})
    _gravar("pca.json", dados)
    return resumo


# ----------------------------------------------------------------------------- catálogo (classe pelo CATMAT/CATSER)
def _tipo_catalogo(tipo: str | None) -> str:
    return "S" if (tipo or "").upper().startswith("S") else "M"


def _consultar_catalogo(tipo: str, codigo: str, timeout: float = 20, tentativas: bool = True) -> dict | None:
    if tipo == "M":
        d = _get_json(EP_MATERIAL, {"pagina": 1, "tamanhoPagina": 10, "codigoItem": codigo}, timeout, tentativas)
        r = (d.get("resultado") or [None])[0]
        if not r:
            return None
        return {"classe": str(r["codigoClasse"]), "nome": r["nomeClasse"], "grupo": str(r.get("codigoGrupo") or ""), "nome_grupo": r.get("nomeGrupo"),
                "descricao": r.get("descricaoItem")}
    d = _get_json(EP_SERVICO, {"pagina": 1, "tamanhoPagina": 10, "codigoServico": codigo}, timeout, tentativas)
    r = (d.get("resultado") or [None])[0]
    if not r:
        return None
    classe = r.get("codigoClasse") or r.get("codigoGrupo")
    nome = r.get("nomeClasse") if r.get("codigoClasse") else r.get("nomeGrupo")
    return {"classe": str(classe) if classe else None, "nome": nome, "grupo": str(r.get("codigoGrupo") or ""), "nome_grupo": r.get("nomeGrupo"),
            "descricao": r.get("nomeServico")}


_REDE_PAUSA_S = 300  # após uma falha de rede, não tenta de novo por 5 min (a validação roda a cada edição do formulário)
_rede_pausada_ate = 0.0
_nao_encontrados: set[str] = set()


def reiniciar_cache_de_rede() -> None:
    global _rede_pausada_ate
    _rede_pausada_ate = 0.0
    _nao_encontrados.clear()


def tipo_catalogo(tipo: str | None) -> str:
    return _tipo_catalogo(tipo)


def classe_do_catalogo(tipo: str | None, codigo: str | None, rede: bool = False) -> dict | None:
    """{'codigo','nome'} da classe (material) ou classe/grupo (serviço) do código CATMAT/CATSER.

    Lê do cache (dados/catalogo.json). Com `rede=True` consulta a API quando o código ainda não está no cache;
    se a API falhar, a rede fica em pausa por alguns minutos para não travar a tela."""
    global _rede_pausada_ate
    codigo = re.sub(r"\D", "", str(codigo or ""))
    if not codigo:
        return None
    t = _tipo_catalogo(tipo)
    chave = f"{t}:{codigo}"
    cache = _ler("catalogo.json", {"itens": {}})
    reg = cache["itens"].get(chave)
    if reg is None and rede and chave not in _nao_encontrados and time.monotonic() >= _rede_pausada_ate:
        try:
            reg = _consultar_catalogo(t, codigo, timeout=6, tentativas=False)
        except ApiIndisponivel:
            _rede_pausada_ate = time.monotonic() + _REDE_PAUSA_S
            return None
        if reg:
            cache["itens"][chave] = reg
            _gravar("catalogo.json", cache)
        else:
            _nao_encontrados.add(chave)
    if not reg or not reg.get("classe"):
        return None
    return {"codigo": reg["classe"], "nome": reg.get("nome")}


def importar_catalogo(progresso: Progresso = print, limite: int | None = None) -> dict:
    """Preenche o cache de classes para todos os CATMAT/CATSER das atas do BCMS (só o que ainda não está no cache)."""
    pregoes = _ler("pregoes.json", {"pregoes": {}})["pregoes"]
    pedidos = {(_tipo_catalogo(i.get("catalogo")), re.sub(r"\D", "", i["catmat"])) for p in pregoes.values() for i in p["itens"] if i.get("catmat")}
    cache = _ler("catalogo.json", {"itens": {}})
    faltam = sorted(c for c in pedidos if c[1] and f"{c[0]}:{c[1]}" not in cache["itens"])
    if limite:
        faltam = faltam[:limite]
    progresso(f"  Catálogo: {len(pedidos)} código(s) nas atas, {len(faltam)} a consultar")
    novos = falhas = 0

    def buscar(par):
        try:
            return par, _consultar_catalogo(par[0], par[1], timeout=30)
        except ApiIndisponivel:
            return par, "FALHA"

    with ThreadPoolExecutor(max_workers=3) as ex:  # a API limita rajadas: poucos em paralelo
        for par, reg in ex.map(buscar, faltam):
            if reg == "FALHA":
                falhas += 1
            elif reg:
                cache["itens"][f"{par[0]}:{par[1]}"] = reg
                novos += 1
    cache["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    _gravar("catalogo.json", cache)
    progresso(f"  Catálogo: {novos} classe(s) novas, {falhas} falha(s) de rede")
    return {"novos": novos, "falhas": falhas, "total_cache": len(cache["itens"])}


# ----------------------------------------------------------------------------- consulta
def carregar() -> dict:
    return _ler("pca.json", {"anos": {}})


def anos_disponiveis() -> list[int]:
    return sorted(int(a) for a, v in carregar().get("anos", {}).items() if v.get("contratacoes"))


def status() -> dict:
    d = carregar()
    return {"anos": {a: {"contratacoes": len(v.get("contratacoes", {})), "posicao": v.get("posicao"), "itens": v.get("total_itens", 0)}
                     for a, v in d.get("anos", {}).items()}, "uasg": d.get("uasg")}


def contratacao(ano: int, numero: int) -> dict | None:
    anos_dict = carregar().get("anos", {})
    direto = anos_dict.get(str(ano), {}).get("contratacoes", {}).get(str(numero))
    if direto:
        return direto
    for a, v in anos_dict.items():
        if str(a) != str(ano):
            c = v.get("contratacoes", {}).get(str(numero))
            if c:
                return c
    return None


def _resumo(c: dict, ano: int) -> dict:
    return {"codigo": c["codigo"], "numero": c["numero"], "ano": ano, "objetos": c["objetos"][:3], "projetos": c["projetos"][:3],
            "n_itens": c["n_itens"], "classes": c["classes"][:12], "valor_total": c.get("valor_total")}


def listar(ano: int, q: str = "", limite: int = 40) -> list[dict]:
    cont = carregar().get("anos", {}).get(str(ano), {}).get("contratacoes", {})
    alvo = _tokens(q)
    saida = []
    for c in cont.values():
        pool = " ".join([c["codigo"], *c["objetos"], *c["projetos"], *(f"{k['codigo']} {k['nome']}" for k in c["classes"]),
                         *(i["descricao"] or "" for i in c["itens"])])
        if alvo and not alvo <= _tokens(pool) and _norm(q) not in _norm(pool):
            continue
        saida.append(_resumo(c, ano))
    saida.sort(key=lambda r: -r["numero"])
    return saida[:limite]


# ----------------------------------------------------------------------------- sugestão
_STOP = {"para", "com", "dos", "das", "uma", "uns", "que", "por", "aos", "como", "mais", "sua", "seu", "geral", "item", "itens", "tipo",
         "material", "materiais", "servico", "servicos", "aquisicao", "contratacao", "manutencao", "unidade", "militar", "organizacao",
         "comum", "outros", "outras", "demais", "formato", "numero", "tamanho", "diametro", "comprimento", "modelo", "cor", "aplicacao",
         "caracteristicas", "adicionais", "necessidades", "atender", "empresa", "conforme", "especificacao", "especificacoes"}
MIN_SCORE_SUGESTAO = 12.0  # abaixo disso a coincidência é só de palavras soltas: não vira sugestão


def _norm(txt: str) -> str:
    s = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).lower().strip()


def _stem(t: str) -> str:
    return t[:-1] if len(t) > 4 and t.endswith("s") else t


def _tokens(txt: str) -> set[str]:
    return {_stem(t) for t in re.findall(r"[a-z0-9]{4,}", _norm(txt)) if t not in _STOP}


def classes_dos_itens(itens: list[dict], rede: bool = False) -> list[dict]:
    """Classes do catálogo dos itens (ordenadas pelo valor): [{'codigo','nome','rotulo','n_itens','valor'}]."""
    acum: dict[str, dict] = {}
    for it in itens:
        classe = classe_do_catalogo(it.get("tipo"), it.get("codigo"), rede=rede)
        if not classe:
            continue
        a = acum.setdefault(classe["codigo"], {"codigo": classe["codigo"], "nome": classe["nome"], "n_itens": 0, "valor": 0.0})
        a["n_itens"] += 1
        a["valor"] += float(it.get("valor") or 0)
    saida = sorted(acum.values(), key=lambda a: (-a["valor"], -a["n_itens"]))
    for a in saida:
        a["rotulo"] = f"{a['codigo']} - {a['nome']}" if a.get("nome") else a["codigo"]
    return saida


def sugerir(ano: int, itens: list[dict], funcao: str | None = None, finalidade: str | None = None, top: int = 5, rede: bool = True) -> dict:
    """Sugere classe/grupo e contratação do PCA para os itens da requisição.

    itens: [{'tipo': 'M'|'S', 'codigo': CATMAT/CATSER, 'descricao': str, 'valor': número}]
    """
    d = carregar()
    anos = {int(a): v for a, v in d.get("anos", {}).items() if v.get("contratacoes")}
    saida: dict = {"ano_documento": ano, "ano_usado": None, "disponivel": bool(anos), "posicao": None, "aviso": None,
                   "classes": classes_dos_itens(itens, rede=rede), "sugestoes": []}
    if not anos:
        saida["aviso"] = "PCA do BCMS ainda não importado (use \"Atualizar dados\")."
        return saida
    ano_usado = ano if ano in anos else max((a for a in anos if a <= ano), default=max(anos))
    saida["ano_usado"] = ano_usado
    saida["posicao"] = anos[ano_usado].get("posicao")
    if ano_usado != ano:
        saida["aviso"] = f"O PCA {ano} do BCMS não está publicado no PGC/PNCP: as sugestões vêm do PCA {ano_usado}."
    codigos_classe = {c["codigo"] for c in saida["classes"]}
    codigos_itens = {re.sub(r"\D", "", str(i.get("codigo") or "")) for i in itens if i.get("codigo")}
    tokens_doc = _tokens(" ".join([finalidade or "", *(i.get("descricao") or "" for i in itens)]))
    tokens_funcao = _tokens(funcao or "")
    for c in anos[ano_usado]["contratacoes"].values():
        motivos, score = [], 0.0
        iguais = codigos_itens & {i["catalogo"] for i in c["itens"] if i.get("catalogo")}
        if iguais:
            score += min(100, 60 * len(iguais))
            motivos.append(f"item do catálogo no PCA ({', '.join(sorted(iguais)[:3])})")
        em_classe = codigos_classe & {k["codigo"] for k in c["classes"]}
        if em_classe:
            score += min(60, 30 * len(em_classe))
            motivos.append("mesma classe: " + ", ".join(sorted(em_classe)[:3]))
        pool = _tokens(" ".join([*c["objetos"], *c["projetos"], *(i["descricao"] or "" for i in c["itens"])]))
        comuns = tokens_doc & pool
        if len(comuns) >= 2 or (comuns and (iguais or em_classe)):  # uma palavra solta, sozinha, não indica o objeto
            score += min(40.0, 40.0 * len(comuns) / max(4, min(len(tokens_doc), 12)))
            motivos.append("objeto semelhante: " + ", ".join(sorted(comuns)[:4]))
        projeto_tokens = _tokens(" ".join(c["projetos"]))
        secao = tokens_funcao & projeto_tokens
        if secao:
            score += 15
            motivos.append("seção: " + ", ".join(sorted(secao)))
        if score >= MIN_SCORE_SUGESTAO:
            saida["sugestoes"].append({**_resumo(c, ano_usado), "score": round(score, 1), "motivos": motivos})
    saida["sugestoes"].sort(key=lambda s: -s["score"])
    saida["sugestoes"] = saida["sugestoes"][:top]
    return saida
