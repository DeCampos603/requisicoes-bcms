# -*- coding: utf-8 -*-
"""PCA (Plano de Contratações Anual) do BCMS: consulta na API do PNCP (Portal Nacional de Contratações Públicas)
e de dados abertos do Compras.gov.br (PGC), indexação local e sugestão inteligente de preenchimento.

Fontes:
  PNCP:
    /api/consulta/v1/pca/atualizacao           itens e contratações do PCA publicados no PNCP por UASG e CNPJ
  Compras.gov.br (legado / complementar):
    /modulo-pgc/1_consultarPgcDetalhe          itens do PCA por órgão, ano e UASG
    /modulo-material/4_consultarItemMaterial   classe do item (CATMAT)
    /modulo-servico/6_consultarItemServico     grupo/classe do serviço (CATSER)
"""

from __future__ import annotations

import json
import logging
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

log = logging.getLogger(__name__)

BASE_COMPRAS = "https://dadosabertos.compras.gov.br"
EP_PGC = "/modulo-pgc/1_consultarPgcDetalhe"
EP_MATERIAL = "/modulo-material/4_consultarItemMaterial"
EP_SERVICO = "/modulo-servico/6_consultarItemServico"

BASE_PNCP = "https://pncp.gov.br/api/consulta/v1"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RequisicoesEmpenhoBCMS/2.0 (+uso interno BCMS)"
TAM_PAGINA = 500

Progresso = Callable[[str], None]


class ApiIndisponivel(RuntimeError):
    """A API pública não respondeu (rede, timeout ou erro HTTP)."""


# ----------------------------------------------------------------------------- HTTP
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
def _get_com_retry(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(caminho: str, params: dict, timeout: float = 60, tentativas: bool = True) -> dict:
    url = f"{BASE_COMPRAS}{caminho}?{urllib.parse.urlencode(params)}"
    try:
        if tentativas:
            return _get_com_retry(url, timeout)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise ApiIndisponivel(f"{type(e).__name__}: {e}") from e


def _get_json_pncp(caminho: str, params: dict, timeout: float = 20) -> dict:
    url = f"{BASE_PNCP}{caminho}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise ApiIndisponivel(f"PNCP: {type(e).__name__}: {e}") from e


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


# ----------------------------------------------------------------------------- normalização e importação PNCP
def importar_pncp_pca(progresso: Progresso = print) -> dict:
    """Importa o PCA diretamente da API oficial do PNCP para a UASG configurada."""
    cfg = _config_pca()
    orgao_cnpj = cfg.get("orgao_cnpj") or "00394452000103"
    uasg = cfg.get("uasg") or config.unidade().get("uasg_propria", "160329")
    dados = _ler("pca.json", {"anos": {}})

    ano_atual = datetime.now().year
    anos = [ano_atual - 1, ano_atual, ano_atual + 1]
    totais = {}

    for a in anos:
        d_ini = f"{a}0101"
        d_fim = f"{a}1231"
        try:
            params = {
                "dataInicio": d_ini,
                "dataFim": d_fim,
                "cnpj": orgao_cnpj,
                "codigoUnidade": uasg,
                "pagina": 1,
                "tamanhoPagina": 100,
            }
            d = _get_json_pncp("/pca/atualizacao", params, timeout=15)
            blocos = d.get("data") or []
            total_pags = d.get("totalPaginas") or 1

            for p in range(2, min(total_pags + 1, 10)):
                time.sleep(0.5)
                params["pagina"] = p
                d_p = _get_json_pncp("/pca/atualizacao", params, timeout=15)
                blocos.extend(d_p.get("data") or [])

            if not blocos:
                continue

            for b in blocos:
                id_pncp = b.get("idPcaPncp")
                data_pub = b.get("dataPublicacaoPNCP")
                data_atlz = b.get("dataAtualizacaoGlobalPCA")

                for it in b.get("itens", []):
                    grp_cod = it.get("grupoContratacaoCodigo")
                    if not grp_cod:
                        continue
                    m = re.search(r"-(\d+)/(\d+)$", grp_cod)
                    num_contratacao = int(m.group(1)) if m else int(it.get("numeroItem") or 1)
                    ano_contratacao = int(m.group(2)) if m else int(b.get("anoPca") or a)

                    ano_str = str(ano_contratacao)
                    ano_dict = dados["anos"].setdefault(ano_str, {
                        "posicao": data_atlz or data_pub,
                        "total_itens": 0,
                        "importado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "fonte": "PNCP - Portal Nacional de Contratações Públicas (API /pca/atualizacao)",
                        "contratacoes": {},
                    })

                    c = ano_dict["contratacoes"].setdefault(str(num_contratacao), {
                        "numero": num_contratacao,
                        "ano": ano_contratacao,
                        "codigo": grp_cod,
                        "id_pncp": id_pncp,
                        "objeto_principal": (it.get("grupoContratacaoNome") or "").strip(),
                        "objetos": [],
                        "projetos": [],
                        "itens": [],
                        "classes": [],
                        "n_itens": 0,
                        "valor_total": 0.0,
                        "atualizado": data_atlz or data_pub,
                    })

                    obj_grp = (it.get("grupoContratacaoNome") or "").strip()
                    if obj_grp and obj_grp not in c["objetos"]:
                        c["objetos"].append(obj_grp)

                    desc_item = (it.get("descricaoItem") or "").strip()
                    classe_cod = str(it.get("classificacaoSuperiorCodigo") or "").strip()
                    classe_nome = (it.get("classificacaoSuperiorNome") or "").strip()

                    if classe_cod and not any(k["codigo"] == classe_cod for k in c["classes"]):
                        c["classes"].append({"codigo": classe_cod, "nome": classe_nome})

                    val_tot = float(it.get("valorTotal") or 0.0)
                    c["itens"].append({
                        "numero": it.get("numeroItem"),
                        "tipo": "S" if it.get("categoriaItemPcaNome") == "Serviço" else "M",
                        "catalogo": str(it.get("codigoItem")) if it.get("codigoItem") else None,
                        "descricao": desc_item or classe_nome or "Item sem descrição",
                        "qtd": float(it.get("quantidadeEstimada") or 0.0),
                        "valor_unitario": float(it.get("valorUnitario") or 0.0),
                        "valor_total": val_tot,
                        "classe": {"codigo": classe_cod, "nome": classe_nome} if classe_cod else None,
                        "data_desejada": it.get("dataDesejada"),
                    })

            totais[str(a)] = len(blocos)
        except Exception as e:
            log.warning("Consulta PNCP PCA para ano %s falhou ou sem dados: %s", a, e)

    # Recalcula totais
    for ano_str, v in dados["anos"].items():
        tot_ano = 0
        for c in v["contratacoes"].values():
            c["n_itens"] = len(c["itens"])
            c["valor_total"] = round(sum(i.get("valor_total") or 0.0 for i in c["itens"]), 2)
            tot_ano += c["n_itens"]
        v["total_itens"] = tot_ano

    _gravar("pca.json", dados)
    progresso(f"  PNCP PCA sincronizado: {list(dados['anos'].keys())}")
    return totais


def importar_pca(progresso: Progresso = print, anos: list[int] | None = None) -> dict:
    """Importa o PCA tanto do PNCP quanto do Compras.gov.br unificando as bases."""
    try:
        importar_pncp_pca(progresso=progresso)
    except Exception as e:
        log.warning("Importação PNCP PCA com ressalva: %s", e)

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
            try:
                d = _get_json(EP_PGC, {"pagina": pagina, "tamanhoPagina": TAM_PAGINA, "orgao": orgao, "anoPcaProjetoCompra": ano, "codigoUasg": uasg})
                total = d.get("totalPaginas", 0) or 0
                linhas += d.get("resultado", [])
                pagina += 1
                if total == 0:
                    break
            except Exception:
                break
        if linhas:
            contratacoes = normalizar_pca(linhas, uasg)
            posicao = max((c["atualizado"] for c in contratacoes.values() if c["atualizado"]), default=None)
            ano_existente = dados["anos"].get(str(ano), {})
            contratacoes_existentes = ano_existente.get("contratacoes", {})
            for k, c_nova in contratacoes.items():
                if k not in contratacoes_existentes:
                    contratacoes_existentes[k] = c_nova
            dados["anos"][str(ano)] = {
                "posicao": posicao or ano_existente.get("posicao"),
                "total_itens": sum(len(c["itens"]) for c in contratacoes_existentes.values()),
                "importado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "contratacoes": contratacoes_existentes,
            }
        resumo[str(ano)] = {"contratacoes": len(dados["anos"].get(str(ano), {}).get("contratacoes", {}))}
    _gravar("pca.json", dados)
    return resumo


def _classe_do_item(r: dict) -> dict | None:
    if r.get("codigoClasseMaterial"):
        return {"codigo": str(r["codigoClasseMaterial"]), "nome": r.get("nomeClasseMaterial")}
    if r.get("codigoClasseServico"):
        return {"codigo": str(r["codigoClasseServico"]), "nome": r.get("nomeClasseServico")}
    if r.get("codigoGrupoServico"):
        return {"codigo": str(r["codigoGrupoServico"]), "nome": r.get("nomeGrupoServico")}
    return None


def normalizar_pca(linhas: list[dict], uasg: str) -> dict:
    contratacoes: dict[str, dict] = {}
    for r in linhas:
        n = r.get("numeroArtefato")
        ano = r.get("anoArtefato")
        if n is None or ano is None:
            continue
        c = contratacoes.setdefault(str(n), {
            "numero": int(n), "ano": int(ano), "codigo": f"{uasg}-{n}/{ano}", "objeto_principal": "", "objetos": [], "projetos": [], "itens": [],
            "atualizado": None,
        })
        for chave, campo in (("objetos", "descricaoObjetoDfd"), ("projetos", "tituloProjetoCompra")):
            v = (r.get(campo) or "").strip()
            if v and v not in c[chave]:
                c[chave].append(v)
        if c["objetos"] and not c["objeto_principal"]:
            c["objeto_principal"] = c["objetos"][0]
        classe = _classe_do_item(r)
        c["itens"].append({
            "tipo": r.get("tipoItem"), "classe": classe, "catalogo": str(r["codigoItemCatalogo"]) if r.get("codigoItemCatalogo") else None,
            "descricao": (r.get("descricaoItemCatalogo") or r.get("descricaoObjetoDfd") or "").strip() or None, "dfd": r.get("ordemDfd"),
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
        c["valor_total"] = round(sum(i.get("valor_total") or 0 for i in c["itens"]), 2)
    return contratacoes


# ----------------------------------------------------------------------------- catálogo
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


_REDE_PAUSA_S = 300
_rede_pausada_ate = 0.0
_nao_encontrados: set[str] = set()


def reiniciar_cache_de_rede() -> None:
    global _rede_pausada_ate
    _rede_pausada_ate = 0.0
    _nao_encontrados.clear()


def tipo_catalogo(tipo: str | None) -> str:
    return _tipo_catalogo(tipo)


def classe_do_catalogo(tipo: str | None, codigo: str | None, rede: bool = False) -> dict | None:
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

    with ThreadPoolExecutor(max_workers=3) as ex:
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


# ----------------------------------------------------------------------------- consulta e listagem rica
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


def _resumo(c: dict, ano: int, itens_match: list[dict] | None = None) -> dict:
    itens_formatados = []
    for idx, it in enumerate(c.get("itens", []), 1):
        classe = it.get("classe") or {}
        cl_cod = classe.get("codigo") if isinstance(classe, dict) else str(classe or "")
        cl_nome = classe.get("nome") if isinstance(classe, dict) else None
        itens_formatados.append({
            "numero": it.get("numero") or idx,
            "tipo": it.get("tipo") or "M",
            "catalogo": it.get("catalogo"),
            "descricao": it.get("descricao") or cl_nome or "Item sem descrição",
            "qtd": float(it.get("qtd") or 0.0),
            "valor_unitario": float(it.get("valor_unitario") or 0.0),
            "valor_total": float(it.get("valor_total") or 0.0),
            "classe_codigo": cl_cod,
            "classe_nome": cl_nome,
        })

    obj_princ = c.get("objeto_principal") or (c.get("objetos") or ["—"])[0]
    return {
        "codigo": c["codigo"],
        "numero": c["numero"],
        "ano": ano,
        "id_pncp": c.get("id_pncp"),
        "objeto_principal": obj_princ,
        "objetos": c.get("objetos") or [obj_princ],
        "projetos": c.get("projetos", []),
        "n_itens": c.get("n_itens", len(c.get("itens", []))),
        "classes": c.get("classes", []),
        "valor_total": c.get("valor_total"),
        "itens": itens_formatados[:60],
        "itens_match": itens_match or [],
    }


def listar(ano: int = 0, q: str = "", limite: int = 40) -> list[dict]:
    """Lista contratações do PNCP / PCA com busca multi-termo, match por número e itens."""
    anos_dict = carregar().get("anos", {})
    if not anos_dict:
        return []

    if ano and str(ano) in anos_dict:
        anos_principais = [str(ano)]
        outros_anos = [str(a) for a in sorted((int(x) for x in anos_dict), reverse=True) if str(a) != str(ano)]
    else:
        anos_principais = [str(a) for a in sorted((int(x) for x in anos_dict), reverse=True)]
        outros_anos = []

    q_norm = _norm(q)
    raw_tokens = [t for t in re.findall(r"[a-z0-9]+", q_norm) if len(t) >= 1]
    eh_numero = len(raw_tokens) == 1 and raw_tokens[0].isdigit()
    num_buscado = int(raw_tokens[0]) if eh_numero else -1

    def buscar_em_ano(ano_str: str) -> list[dict]:
        cont = anos_dict.get(ano_str, {}).get("contratacoes", {})
        ano_int = int(ano_str)
        resultados = []
        for c in cont.values():
            if not raw_tokens:
                resultados.append(_resumo(c, ano_int))
                continue

            codigo = c.get("codigo") or ""
            id_pncp = c.get("id_pncp") or ""
            numero_str = str(c.get("numero") or "")
            obj_princ = c.get("objeto_principal") or ""
            objetos_str = " ".join(c.get("objetos", []))
            classes_str = " ".join(f"{k.get('codigo','')} {k.get('nome','')}" for k in c.get("classes", []))
            pool_header = _norm(f"{codigo} {numero_str} {id_pncp} {obj_princ} {objetos_str} {classes_str}")

            itens_casados = []
            for idx, it in enumerate(c.get("itens", []), 1):
                desc = it.get("descricao") or ""
                cat = str(it.get("catalogo") or "")
                cl = it.get("classe") or {}
                cl_txt = f"{cl.get('codigo','')} {cl.get('nome','')}" if isinstance(cl, dict) else str(cl)
                pool_item = _norm(f"{desc} {cat} {cl_txt}")

                if all(t in pool_item for t in raw_tokens):
                    itens_casados.append({
                        "numero": it.get("numero") or idx,
                        "descricao": desc or cl_txt or "Item",
                        "valor_total": float(it.get("valor_total") or 0.0),
                        "classe": cl_txt.strip(),
                    })
                    if len(itens_casados) >= 5:
                        break

            if eh_numero:
                header_casou = (c.get("numero") == num_buscado) or (f"-{num_buscado}/" in codigo) or (raw_tokens[0] in pool_header)
            else:
                header_casou = all(t in pool_header for t in raw_tokens)

            if header_casou or itens_casados:
                resultados.append(_resumo(c, ano_int, itens_match=itens_casados))

        return resultados

    saida = []
    for a_str in anos_principais:
        saida.extend(buscar_em_ano(a_str))

    if q and len(saida) < limite and outros_anos:
        vistos = {r["codigo"] for r in saida}
        for a_str in outros_anos:
            extras = buscar_em_ano(a_str)
            for ex in extras:
                if ex["codigo"] not in vistos:
                    vistos.add(ex["codigo"])
                    saida.append(ex)
                if len(saida) >= limite:
                    break

    def score_ordenacao(r):
        pts = 0
        if raw_tokens:
            cod_norm = _norm(r.get("codigo", ""))
            if eh_numero and r.get("numero") == num_buscado:
                pts += 1000
            elif any(t in cod_norm for t in raw_tokens):
                pts += 150
            if r.get("itens_match"):
                pts += 50 * len(r["itens_match"])
        return (pts, r.get("ano", 0), r.get("numero", 0))

    saida.sort(key=score_ordenacao, reverse=True)
    return saida[:limite]


# ----------------------------------------------------------------------------- sugestão automática
_STOP = {"para", "com", "dos", "das", "uma", "uns", "que", "por", "aos", "como", "mais", "sua", "seu", "geral", "item", "itens", "tipo",
         "material", "materiais", "servico", "servicos", "aquisicao", "contratacao", "manutencao", "unidade", "militar", "organizacao",
         "comum", "outros", "outras", "demais", "formato", "numero", "tamanho", "diametro", "comprimento", "modelo", "cor", "aplicacao",
         "caracteristicas", "adicionais", "necessidades", "atender", "empresa", "conforme", "especificacao", "especificacoes"}
MIN_SCORE_SUGESTAO = 12.0


def _norm(txt: str) -> str:
    s = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).lower().strip()


def _stem(t: str) -> str:
    return t[:-1] if len(t) > 4 and t.endswith("s") else t


def _tokens(txt: str) -> set[str]:
    return {_stem(t) for t in re.findall(r"[a-z0-9]{3,}", _norm(txt)) if t not in _STOP}


def classes_dos_itens(itens: list[dict], rede: bool = False) -> list[dict]:
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
        iguais = codigos_itens & {i.get("catalogo") for i in c.get("itens", []) if i.get("catalogo")}
        if iguais:
            score += min(100, 60 * len(iguais))
            motivos.append(f"item do catálogo no PCA ({', '.join(sorted(iguais)[:3])})")
        em_classe = codigos_classe & {k.get("codigo") for k in c.get("classes", []) if k.get("codigo")}
        if em_classe:
            score += min(60, 30 * len(em_classe))
            motivos.append("mesma classe: " + ", ".join(sorted(em_classe)[:3]))
        pool = _tokens(" ".join([c.get("objeto_principal") or "", *c.get("objetos", []), *c.get("projetos", []), *(i.get("descricao") or "" for i in c.get("itens", []))]))
        comuns = tokens_doc & pool
        if len(comuns) >= 2 or (comuns and (iguais or em_classe)):
            score += min(40.0, 40.0 * len(comuns) / max(4, min(len(tokens_doc), 12)))
            motivos.append("objeto semelhante: " + ", ".join(sorted(comuns)[:4]))
        projeto_tokens = _tokens(" ".join(c.get("projetos", [])))
        secao = tokens_funcao & projeto_tokens
        if secao:
            score += 15
            motivos.append("seção: " + ", ".join(sorted(secao)))
        if score >= MIN_SCORE_SUGESTAO:
            saida["sugestoes"].append({**_resumo(c, ano_usado), "score": round(score, 1), "motivos": motivos})
    saida["sugestoes"].sort(key=lambda s: -s["score"])
    saida["sugestoes"] = saida["sugestoes"][:top]
    return saida
