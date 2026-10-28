# -*- coding: utf-8 -*-
"""Acesso somente-leitura (com recarga automática) aos índices em dados/.

Os índices são gerados por app.importadores a partir do repositório. Aqui ficam as consultas:
cruzamento pregão ↔ UASG gerenciadora, localização de NC (com desambiguação por emitente) e de NE.
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from datetime import date
from pathlib import Path

from . import config
from .validadores import mascarar_cpf

_trava = config.ARQUIVO_LOCK
_cache: dict[str, tuple[float, dict]] = {}


def calcular_dias_defasagem(data_str: str | None, hoje: date | None = None) -> int | None:
    """Calcula a diferença em dias entre a data da base e a data de referência (padrão hoje)."""
    if not data_str:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})|(\d{4})-(\d{2})-(\d{2})", str(data_str))
    if not m:
        return None
    try:
        ref = hoje or date.today()
        if m.group(1):
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        else:
            d = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        return (ref - d).days
    except Exception:
        return None


def _carregar(nome: str, padrao: dict) -> dict:
    caminho = config.DADOS / nome
    try:
        mtime = caminho.stat().st_mtime
    except FileNotFoundError:
        return padrao
    with _trava:
        item = _cache.get(str(caminho))
        if item and item[0] == mtime:
            return item[1]
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        _cache[str(caminho)] = (mtime, dados)
        return dados


def invalidar() -> None:
    with _trava:
        _cache.clear()


def _norm(txt: str) -> str:
    s = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).lower().strip()


# ----------------------------------------------------------------------------- status
def _pca_status() -> dict:
    from . import pca
    return pca.status()


def status() -> dict:
    p = _carregar("pregoes.json", {})
    c = _carregar("credito.json", {})
    n = _carregar("nes.json", {})
    k = _carregar("ncs.json", {})
    dias_p = calcular_dias_defasagem(p.get("posicao"))
    dias_c = calcular_dias_defasagem(c.get("posicao"))
    defasado_p = dias_p is not None and dias_p > 15
    defasado_c = dias_c is not None and dias_c > 7
    return {
        "pregoes": {"posicao": p.get("posicao"), "total": p.get("total", 0), "importado_em": p.get("importado_em"), "origem": p.get("origem"), "dias": dias_p, "defasado": defasado_p},
        "credito": {"posicao": c.get("posicao"), "total": c.get("total", 0), "dias": dias_c, "defasado": defasado_c},
        "nes": {"total": n.get("total", 0), "gerado_em": n.get("gerado_em")},
        "ncs": {"total": k.get("total", 0), "gerado_em": k.get("gerado_em")},
        "pca": _pca_status(),
        "indices_ok": bool(p and n and k),
        "defasagem": {
            "defasado": defasado_p or defasado_c,
            "dias_pregoes": dias_p,
            "dias_credito": dias_c,
            "defasado_pregoes": defasado_p,
            "defasado_credito": defasado_c,
            "limite_pregoes": 15,
            "limite_credito": 7,
        },
    }


# ----------------------------------------------------------------------------- UASG
def nome_uasg(cod: str | None) -> str | None:
    if not cod:
        return None
    reg = _carregar("uasgs.json", {}).get("uasgs", {}).get(cod)
    if not reg:
        return None
    return reg.get("nome") or reg.get("sigla")


def sigla_uasg(cod: str | None) -> str | None:
    reg = _carregar("uasgs.json", {}).get("uasgs", {}).get(cod or "")
    return (reg or {}).get("sigla")


# ----------------------------------------------------------------------------- pregões
MAPA_CATEGORIAS = {
    "Manutenção Automotiva": "Manutenção de Viaturas",
    "Manutenção Predial": "Material de Construção",
    "Material Elétrico": "Material de Construção / Elétrico",
    "Manutenção e Reparos": "Material de Construção e Reparos",
    "Material Médico-Hospitalar": "Material de Saúde",
    "Material Odontológico": "Material de Saúde",
    "Material Laboratorial": "Material de Saúde",
    "Combustíveis e Lubrificantes": "Combustíveis e Lubrificantes",
    "Limpeza e Conservação": "Material de Limpeza",
    "Material de Expediente": "Material de Expediente",
    "Informática / TI": "Informática e TI",
    "Serviços de TI": "Serviços de TI",
    "Fardamento e Vestuário": "Fardamento e Vestuário",
    "Gênero Alimentício": "Alimentação e Copa",
    "Copa e Cozinha": "Alimentação e Copa",
    "Armamento e Munição": "Armamento e Munição",
    "Ferramentas": "Ferramentas",
    "Sinalização": "Segurança e Sinalização",
    "Vigilância e Segurança": "Segurança e Vigilância",
    "Material Permanente (diversos)": "Material Permanente",
    "Material de Consumo (diversos)": "Material de Consumo",
    "Insumo p/ Animais": "Insumo p/ Animais",
    "Passagens e Locomoção": "Passagens e Locomoção",
    "Serviços - Pessoa Jurídica": "Serviços Terceirizados",
    "Serviços - Pessoa Física": "Serviços Pessoa Física",
}


def sintetizar_objeto_pregao(p: dict) -> tuple[str, list[str]]:
    """Sintetiza um rótulo de objeto amigável e as categorias padronizadas do pregão."""
    if p.get("objeto") and p.get("categorias"):
        return p["objeto"], p["categorias"]
    cats_brutas = [i.get("categoria") for i in p.get("itens", []) if i.get("categoria")]
    padronizadas: list[str] = []
    for c in cats_brutas:
        c_norm = MAPA_CATEGORIAS.get(c, c)
        if c_norm and c_norm not in padronizadas:
            padronizadas.append(c_norm)
    if not padronizadas:
        textos = " ".join(
            f"{i.get('descricao') or ''} {i.get('catmat_nome') or ''}" for i in p.get("itens", [])
        ).upper()
        if any(w in textos for w in ("COMPRIMIDO", "MEDICAMENTO", "MG", "FARMAC", "SAUDE", "CAPSULA", "SORO", "AGULHA")):
            padronizadas.append("Material de Saúde")
        elif any(w in textos for w in ("VIATURA", "VEICULO", "AUTOMOTIV", "PNEU", "FREIO", "MOTOR", "OLEO")):
            padronizadas.append("Manutenção de Viaturas")
        elif any(w in textos for w in ("OBRA", "CIMENTO", "TINTA", "PREDIAL", "CONSTRUCAO", "ELETRIC", "HIDRAULIC")):
            padronizadas.append("Material de Construção")
        elif any(w in textos for w in ("COMPUTADOR", "NOTEBOOK", "IMPRESSORA", "TONER", "TI", "SOFTWARE")):
            padronizadas.append("Informática e TI")
        elif any(w in textos for w in ("ALIMENTO", "CAFE", "ACUCAR", "ARROZ", "FEIJAO", "CARNE", "RANCHO")):
            padronizadas.append("Alimentação e Copa")
        else:
            padronizadas.append("Material de Consumo")
    resumo_partes: list[str] = []
    if any("Viaturas" in c for c in padronizadas):
        resumo_partes.append("Manutenção de Viaturas")
    if any("Construção" in c for c in padronizadas):
        resumo_partes.append("Material de Construção")
    if any("Saúde" in c for c in padronizadas):
        resumo_partes.append("Material de Saúde")
    for c in padronizadas:
        if c not in ("Manutenção de Viaturas", "Material de Construção", "Material de Saúde",
                     "Material de Construção / Elétrico", "Material de Construção e Reparos"):
            if c not in resumo_partes:
                resumo_partes.append(c)
    objeto = " / ".join(resumo_partes[:2]) if resumo_partes else "Material Diverso"
    return objeto, padronizadas


def _resumo_pregao(p: dict) -> dict:
    obj, cats = sintetizar_objeto_pregao(p)
    return {
        "id": p["id"], "ugg": p["ugg"], "numero": p["numero"], "modalidade": p["modalidade"],
        "tipo_unidade": p["tipo_unidade"], "n_itens": p["n_itens"],
        "vigencia_fim_min": p.get("vigencia_fim_min"), "vigencia_fim_max": p.get("vigencia_fim_max"),
        "nome_ugg": nome_uasg(p["ugg"]) or sigla_uasg(p["ugg"]),
        "sigla_ugg": sigla_uasg(p["ugg"]),
        "fornecedores": p.get("fornecedores", [])[:3],
        "objeto": p.get("objeto") or obj,
        "categorias": p.get("categorias") or cats,
    }


def buscar_pregoes(q: str = "", limite: int = 40) -> list[dict]:
    todos = _carregar("pregoes.json", {}).get("pregoes", {})
    alvo = _norm(q)
    digitos = re.sub(r"\D", "", q or "")
    saida = []
    for p in todos.values():
        obj, cats = sintetizar_objeto_pregao(p)
        itens_match: list[dict] = []
        if alvo:
            forns_nomes: list[str] = []
            forns_cnpjs: list[str] = []
            for it in p.get("itens", []):
                fn = it.get("fornecedor_nome")
                fc = it.get("fornecedor_cnpj")
                if fn and fn not in forns_nomes:
                    forns_nomes.append(fn)
                if fc and fc not in forns_cnpjs:
                    forns_cnpjs.append(fc)
                desc_it = it.get("descricao") or ""
                catmat_it = it.get("catmat_nome") or ""
                if alvo in _norm(desc_it) or (catmat_it and alvo in _norm(catmat_it)):
                    itens_match.append({
                        "item": it.get("item"),
                        "descricao": desc_it or catmat_it,
                        "fornecedor": fn or fc or "",
                        "valor_unitario": it.get("valor_unitario"),
                    })
            pool = " ".join([
                p["numero"], p["ugg"], nome_uasg(p["ugg"]) or "", sigla_uasg(p["ugg"]) or "",
                " ".join(p.get("fornecedores", [])),
                " ".join(forns_nomes),
                " ".join(forns_cnpjs),
                obj, " ".join(cats)
            ])
            pool_norm = _norm(pool)
            match_texto = pool_norm.find(alvo) >= 0
            match_digitos = False
            if digitos:
                dig_num = p["numero"].replace("/", "")
                if digitos in dig_num or digitos in p["ugg"]:
                    match_digitos = True
                elif len(digitos) >= 8 and any(digitos in re.sub(r"\D", "", f) for f in forns_cnpjs):
                    match_digitos = True
            if not (match_texto or match_digitos or itens_match):
                continue
        resumo = _resumo_pregao(p)
        if itens_match:
            resumo["itens_match"] = itens_match[:3]
            resumo["total_itens_match"] = len(itens_match)
        saida.append(resumo)
    saida.sort(key=lambda r: (r["tipo_unidade"] != "GERENCIADORA", r["numero"].split("/")[1] if "/" in r["numero"] else "", r["numero"]), reverse=False)
    return saida[:limite]


def pregao(id_: str) -> dict | None:
    p = _carregar("pregoes.json", {}).get("pregoes", {}).get(id_)
    if p:
        obj, cats = sintetizar_objeto_pregao(p)
        if "objeto" not in p:
            p["objeto"] = obj
        if "categorias" not in p:
            p["categorias"] = cats
    return p


def pregao_por_numero(ugg: str, numero: str) -> list[dict]:
    """Registros do pregão (um por papel do BCMS) para a UASG gerenciadora e número informados."""
    return [p for p in _carregar("pregoes.json", {}).get("pregoes", {}).values()
            if p["ugg"] == ugg and p["numero"] == numero]


def nome_completo_fornecedor(cnpj: str | None, nome: str | None) -> str | None:
    """O SIAFI corta a razão social do favorecido em 45 caracteres; a ata traz o nome inteiro, que é o que o DIEx deve mostrar.

    Só estende quando o nome da NE é prefixo do nome da ata para o MESMO CNPJ (nunca troca um nome por outro)."""
    if not nome or not cnpj:
        return nome
    alvo = re.sub(r"\W", "", cnpj).upper()
    prefixo = _norm(nome)
    melhor = nome
    for reg in _carregar("pregoes.json", {}).get("pregoes", {}).values():
        for i in reg["itens"]:
            completo = i.get("fornecedor_nome") or ""
            if (len(completo) > len(melhor) and re.sub(r"\W", "", i.get("fornecedor_cnpj") or "").upper() == alvo
                    and _norm(completo).startswith(prefixo)):
                melhor = completo
    return melhor


def posicao_pregoes() -> str | None:
    return _carregar("pregoes.json", {}).get("posicao")


# ----------------------------------------------------------------------------- NC
def _chave_nc(chave: str) -> str:
    return chave[-12:]


def candidatas_nc(chave: str) -> list[dict]:
    """Todas as NC com este número (AAAANC999999), uma por UG emitente, com a situação do anexo e do saldo."""
    chave = _chave_nc(chave)
    ncs = _carregar("ncs.json", {}).get("ncs", {})
    cred = _carregar("credito.json", {}).get("ncs", {})
    achadas: dict[str, dict] = {}
    for k, nc in ncs.items():
        if nc["chave"] == chave:
            emit = nc["ug_emitente"]
            achadas[emit] = {
                "emitente": emit, "nome_emitente": nc.get("nome_emitente") or nome_uasg(emit),
                "chave": chave, "tipo_nc": nc.get("tipo_nc"), "status": nc.get("status"),
                "data_emissao": nc.get("data_emissao"), "descricao": nc.get("descricao"),
                "total": nc.get("total"), "arquivo_no_repositorio": True,
                "ug_pasta": nc.get("ug_pasta"), "no_dashboard": False,
            }
    for k, c in cred.items():
        if c["chave"] == chave:
            emit = c["emit"]
            reg = achadas.setdefault(emit, {
                "emitente": emit, "nome_emitente": nome_uasg(emit), "chave": chave,
                "tipo_nc": c.get("op"), "status": None, "data_emissao": c.get("dia"),
                "descricao": c.get("obj"), "total": None, "arquivo_no_repositorio": False, "ug_pasta": None,
            })
            reg["no_dashboard"] = True
            if c.get("obj") and c["obj"] not in (reg.get("descricao") or ""):
                reg["descricao"] = f"{reg.get('descricao') or ''} · {c['obj']}".strip(" ·")
    return sorted(achadas.values(), key=lambda r: r["emitente"])


_SINONIMOS_NC: dict[str, list[str]] = {
    "aqs": ["aquisicao"], "aquisicao": ["aqs"],
    "mnt": ["manutencao"], "manut": ["manutencao", "mnt"], "manutencao": ["mnt", "manut"],
    "vtr": ["viatura", "veiculo", "vtne"], "viatura": ["vtr", "vtne", "veiculo"],
    "sv": ["servico"], "serv": ["servico", "sv"], "servico": ["sv", "serv"],
    "servicos": ["sv", "serv", "servico"],
    "trnp": ["transporte"], "transp": ["transporte", "trnp"], "transporte": ["trnp", "transp"],
    "psg": ["passagem", "passagens"], "passagem": ["psg", "passagens"], "passagens": ["psg", "passagem"],
    "di": ["diaria", "diarias"], "diaria": ["di", "diarias"], "diarias": ["di", "diaria"],
    "sup": ["suprimento"], "supr": ["suprimento", "sup"], "suprimento": ["sup", "supr"],
    "eqp": ["equipamento"], "equip": ["equipamento", "eqp"], "equipamento": ["eqp", "equip"],
    "almt": ["alimentacao", "rancho", "qr", "qs"], "alimentacao": ["almt", "rancho", "qr", "qs"],
    "rancho": ["alimentacao", "almt", "qr", "qs"],
    "comb": ["combustivel", "lubrificante"], "combustivel": ["comb"],
    "ind": ["indenizacao"], "indenizacao": ["ind"],
    "pgto": ["pagamento"], "pagamento": ["pgto"],
    "rclh": ["recolhimento", "anulacao"], "recolhimento": ["rclh"],
    "hosp": ["hospitalar", "saude", "medico", "oncologico"],
}


def buscar_ncs(q: str = "", limite: int = 80, ug: str | None = None, apenas_com_saldo: bool = False) -> list[dict]:
    """Busca Notas de Crédito por palavras da descrição (PDF + SIAFI), número da NC, PI, ND ou UG emitente."""
    ncs = _carregar("ncs.json", {}).get("ncs", {})
    cred = _carregar("credito.json", {}).get("ncs", {})
    consolidado: dict[str, dict] = {}

    for k, nc in ncs.items():
        emit = str(nc.get("ug_emitente") or "")
        chave = str(nc.get("chave") or "")
        if not chave:
            continue
        id_unico = f"{emit}:{chave}"
        nds: list[str] = []
        pis: list[str] = []
        ptres_list: list[str] = []
        ugs_fav: list[str] = []
        for it in nc.get("itens") or []:
            uf = str(it.get("ug_favorecida") or "")
            if uf and uf not in ugs_fav:
                ugs_fav.append(uf)
            for dest in it.get("destino") or []:
                if dest.get("nd") and dest["nd"] not in nds:
                    nds.append(dest["nd"])
                if dest.get("pi") and dest["pi"] not in pis:
                    pis.append(dest["pi"])
                if dest.get("ptres") and dest["ptres"] not in ptres_list:
                    ptres_list.append(dest["ptres"])
        if nc.get("ug_pasta") and nc["ug_pasta"] not in ugs_fav:
            ugs_fav.append(nc["ug_pasta"])

        consolidado[id_unico] = {
            "chave": chave,
            "emitente": emit,
            "nome_emitente": nc.get("nome_emitente") or nome_uasg(emit) or sigla_uasg(emit) or "",
            "sigla_emitente": sigla_uasg(emit) or "",
            "data_emissao": nc.get("data_emissao") or "",
            "tipo_nc": nc.get("tipo_nc") or "",
            "descricao": nc.get("descricao") or "",
            "descricao_siafi": "",
            "nds": nds,
            "pis": pis,
            "ptres": ptres_list,
            "ugs_favorecidas": ugs_fav,
            "valor_total": float(nc.get("total") or 0.0) if str(nc.get("total") or "").replace(".", "", 1).isdigit() else 0.0,
            "saldo_disponivel": 0.0,
            "tem_pdf": True,
            "no_dashboard": False,
        }

    for k, c in cred.items():
        emit = str(c.get("emit") or "")
        chave = str(c.get("chave") or "")
        if not chave:
            continue
        id_unico = f"{emit}:{chave}"
        reg = consolidado.get(id_unico)
        if not reg:
            reg = {
                "chave": chave,
                "emitente": emit,
                "nome_emitente": nome_uasg(emit) or sigla_uasg(emit) or "",
                "sigla_emitente": sigla_uasg(emit) or "",
                "data_emissao": c.get("dia") or "",
                "tipo_nc": c.get("op") or "",
                "descricao": c.get("obj") or "",
                "descricao_siafi": c.get("obj") or "",
                "nds": [],
                "pis": [],
                "ptres": [],
                "ugs_favorecidas": [],
                "valor_total": 0.0,
                "saldo_disponivel": 0.0,
                "tem_pdf": False,
                "no_dashboard": True,
            }
            consolidado[id_unico] = reg
        else:
            reg["no_dashboard"] = True
            reg["descricao_siafi"] = c.get("obj") or ""
            if not reg["data_emissao"] and c.get("dia"):
                reg["data_emissao"] = c["dia"]
            if not reg["descricao"] and c.get("obj"):
                reg["descricao"] = c["obj"]

        saldo_tot = 0.0
        prov_tot = 0.0
        for uf, fav_d in (c.get("favorecidas") or {}).items():
            if uf not in reg["ugs_favorecidas"]:
                reg["ugs_favorecidas"].append(uf)
            saldo_tot += float(fav_d.get("cred") or 0.0)
            prov_tot += float(fav_d.get("prov") or 0.0)
            for cel in fav_d.get("celulas") or []:
                if cel.get("nd") and cel["nd"] not in reg["nds"]:
                    reg["nds"].append(cel["nd"])
                if cel.get("pi") and cel["pi"] not in reg["pis"]:
                    reg["pis"].append(cel["pi"])
                if cel.get("ptres") and cel["ptres"] not in reg["ptres"]:
                    reg["ptres"].append(cel["ptres"])
        reg["saldo_disponivel"] = round(saldo_tot, 2)
        if reg["valor_total"] <= 0 and prov_tot > 0:
            reg["valor_total"] = round(prov_tot, 2)

    tokens = [t for t in _norm(q).split(" ") if t]
    saida: list[dict] = []
    for reg in consolidado.values():
        if ug and ug not in reg["ugs_favorecidas"]:
            continue
        if apenas_com_saldo and reg["saldo_disponivel"] <= 0.005:
            continue
        if tokens:
            hay = _norm(" ".join([
                reg["chave"],
                reg["emitente"],
                reg["nome_emitente"],
                reg["sigla_emitente"],
                reg["tipo_nc"],
                reg["descricao"],
                reg["descricao_siafi"],
                " ".join(reg["nds"]),
                " ".join(reg["pis"]),
                " ".join(reg["ptres"]),
                " ".join(reg["ugs_favorecidas"]),
            ]))
            ok = True
            for tk in tokens:
                variantes = [tk] + _SINONIMOS_NC.get(tk, [])
                if not any(v in hay for v in variantes):
                    ok = False
                    break
            if not ok:
                continue
        saida.append(reg)

    saida.sort(key=lambda r: (
        r["saldo_disponivel"] <= 0.005,
        not r["tem_pdf"],
        -r["saldo_disponivel"],
        r["chave"],
    ))
    return saida[:limite]


def nc_pdf(emit: str, chave: str) -> dict | None:
    return _carregar("ncs.json", {}).get("ncs", {}).get(f"{emit}:{_chave_nc(chave)}")


def nc_credito(emit: str, chave: str) -> dict | None:
    return _carregar("credito.json", {}).get("ncs", {}).get(f"{emit}:{_chave_nc(chave)}")


def ncs_por_celula(ug: str, nd: str, pi: str | None, limite: int = 8) -> list[dict]:
    """NC (PDF do repositório) que levaram crédito à célula ND/PI da UG — para o reforço sugerir de onde tirar o crédito."""
    from datetime import datetime
    achadas = []
    for nc in _carregar("ncs.json", {}).get("ncs", {}).values():
        if (nc.get("tipo_nc") or "").startswith("Anulação"):
            continue
        for it in nc["itens"]:
            if it["ug_favorecida"] != ug:
                continue
            for c in it["destino"]:
                mesma_nd = c["nd"] == nd or (c["nd"][4:6] == "00" and c["nd"][:4] == nd[:4])
                if mesma_nd and (c["pi"] or "") == (pi or ""):
                    try:
                        dt = datetime.strptime(nc.get("data_emissao") or "", "%d/%m/%Y")
                    except ValueError:
                        dt = datetime.min
                    achadas.append((dt, {"chave": nc["chave"], "emitente": nc["ug_emitente"], "nome_emitente": nc.get("nome_emitente"),
                                         "data": nc.get("data_emissao"), "valor": c["valor"], "nd": c["nd"]}))
    achadas.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in achadas[:limite]]


def posicao_credito() -> str | None:
    return _carregar("credito.json", {}).get("posicao")


def detalhamentos(ug: str, pi: str | None, nd_destino: str) -> list[dict]:
    """NC internas de DETALHAMENTO DE CRÉDITO (emitente = favorecida = UG do BCMS) que levaram crédito
    do mesmo PI para a ND de destino. Vêm do dashboard de crédito (não há PDF delas no repositório)."""
    saida = []
    for c in _carregar("credito.json", {}).get("ncs", {}).values():
        if c["emit"] != ug or not (c.get("op") or "").startswith("DETALHAMENTO"):
            continue
        fav = c["favorecidas"].get(ug)
        if not fav:
            continue
        for cel in fav["celulas"]:
            if cel["nd"] == nd_destino and (cel["pi"] or "") == (pi or "") and cel["cred"] > 0:
                saida.append({"chave": c["chave"], "dia": c["dia"], "obj": c.get("obj"), "valor": cel["cred"],
                              "residual": fav["cred"], "status": fav.get("status")})
    return sorted(saida, key=lambda d: d["chave"])


def registrar_nc(nc: dict) -> None:
    """Inclui no índice uma NC recém-enviada (upload) sem reler todo o repositório."""
    with _trava:
        arq = config.DADOS / "ncs.json"
        dados = json.loads(arq.read_text(encoding="utf-8")) if arq.is_file() else {"ncs": {}, "falhas": []}
        dados.setdefault("ncs", {})[f"{nc['ug_emitente']}:{nc['chave']}"] = nc
        dados["total"] = len(dados["ncs"])
        arq.parent.mkdir(parents=True, exist_ok=True)
        tmp = arq.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(arq)
        _cache.pop(str(arq), None)


# ----------------------------------------------------------------------------- NE
def listar_empresas_nes(ug: str | None = None) -> list[dict]:
    """Lista consolidada de todas as empresas (favorecidos) com NE na base, para filtros e consultas rápidas."""
    nes = _carregar("nes.json", {}).get("nes", {})
    agrupado: dict[str, dict] = {}
    for ne in nes.values():
        ug_cod = str(ne.get("ug_pasta") or "")
        if ug and ug_cod != ug:
            continue
        cod = str(ne.get("favorecido_codigo") or "").strip()
        nome = str(ne.get("favorecido_nome") or "").strip()
        if not (cod or nome):
            continue
        chave = cod or nome
        if chave not in agrupado:
            agrupado[chave] = {
                "codigo": mascarar_cpf(cod),
                "codigo_puro": re.sub(r"\D", "", cod),
                "nome": nome,
                "total_nes": 0,
                "ugs": set(),
            }
        agrupado[chave]["total_nes"] += 1
        if ug_cod:
            agrupado[chave]["ugs"].add(ug_cod)
        if nome and not agrupado[chave]["nome"]:
            agrupado[chave]["nome"] = nome

    lista = []
    for item in agrupado.values():
        item["ugs"] = sorted(list(item["ugs"]))
        lista.append(item)
    lista.sort(key=lambda x: _norm(x["nome"] or x["codigo"]))
    return lista


def buscar_nes(q: str = "", limite: int = 500, ug: str | None = None, empresa: str | None = None) -> list[dict]:
    nes = _carregar("nes.json", {}).get("nes", {})
    alvo = _norm(q)
    alvo_empresa = _norm(empresa) if empresa else ""
    digitos = re.sub(r"\D", "", q or "")
    dig_empresa = re.sub(r"\D", "", empresa or "")
    termos = alvo.split()
    saida = []
    for k, ne in nes.items():
        ug_cod = str(ne.get("ug_pasta") or "")
        if ug and ug_cod != ug:
            continue

        fav_nome = str(ne.get("favorecido_nome") or "")
        fav_cod = str(ne.get("favorecido_codigo") or "")
        fav_cod_puro = re.sub(r"\D", "", fav_cod)

        # Filtro estrito de empresa se selecionada uma empresa específica
        if alvo_empresa:
            match_emp = False
            if dig_empresa and (dig_empresa in fav_cod_puro or fav_cod_puro in dig_empresa):
                match_emp = True
            elif alvo_empresa in _norm(fav_nome) or alvo_empresa in _norm(fav_cod):
                match_emp = True
            if not match_emp:
                continue

        if alvo:
            ug_rotulo = "ogu orcamento geral uniao" if ug_cod == "160329" else "fex fundo exercito" if ug_cod == "167329" else ""
            nr_puro = str(ne.get("numero") or "")
            ano_nr = f"{ne.get('ano')}ne{nr_puro}"
            ne_espaco = f"ne {nr_puro}"

            pool = " ".join([
                ne.get("chave", ""),
                ug_cod,
                ug_rotulo,
                nr_puro,
                ano_nr,
                ne_espaco,
                fav_nome,
                fav_cod,
                fav_cod_puro,
                ne.get("processo") or "",
                ne.get("descricao") or "",
                ne.get("nd") or "",
                ne.get("pi") or "",
                (ne.get("derivados") or {}).get("pregao") or "",
                (ne.get("derivados") or {}).get("nc") or "",
            ])
            pool_norm = _norm(pool)

            match_termos = all(t in pool_norm for t in termos) if termos else True
            match_num = False
            if digitos:
                dig_sem_zero = digitos.lstrip("0") or "0"
                if nr_puro == dig_sem_zero:
                    match_num = True
                elif ne.get("chave", "").endswith(digitos):
                    match_num = True
                elif len(digitos) >= 4 and digitos in fav_cod_puro:
                    match_num = True

            if not (match_termos or match_num):
                continue
        saida.append(resumo_ne(ne))

    dig_sem_zero = digitos.lstrip("0") or "0" if digitos else ""
    def _ordem(r):
        # 1. Correspondência exata no número da NE se foi digitado número curto (1 a 4 dígitos)
        match_exato_nr = (str(r.get("numero")) == dig_sem_zero) if dig_sem_zero and len(dig_sem_zero) <= 4 else False
        # 2. Correspondência direta na empresa se houver busca textual
        match_emp = (alvo and alvo in _norm(r.get("favorecido_nome") or "")) or (digitos and len(digitos) >= 6 and digitos in re.sub(r"\D", "", r.get("favorecido_codigo") or ""))
        return (not match_exato_nr, not match_emp, -(r.get("ano") or 0), -(r.get("numero") or 0))

    saida.sort(key=_ordem)
    return saida[:limite]


def resumo_ne(ne: dict) -> dict:
    v_atual = str(ne.get("valor_atual") or "0")
    sit = ne.get("situacao")
    rot = ne.get("situacao_rotulo")
    if not sit:
        if v_atual in ("0", "0.00", "0,00", 0):
            sit = "TOTALMENTE_ANULADO"
            rot = "Totalmente Anulada"
        elif float(ne.get("valor_reforcado") or 0) > 0 and float(ne.get("valor_anulado") or 0) > 0:
            sit = "MISTO"
            rot = "Reforçada e Parcialmente Anulada"
        elif float(ne.get("valor_reforcado") or 0) > 0:
            sit = "REFORCADO"
            rot = "Reforçada"
        elif float(ne.get("valor_anulado") or 0) > 0:
            sit = "PARCIALMENTE_ANULADO"
            rot = "Parcialmente Anulada"
        else:
            sit = "NORMAL"
            rot = "Empenho Inicial"

    return {
        "ug": ne["ug_pasta"], "chave": ne["chave"], "ano": ne["ano"], "numero": ne["numero"],
        "favorecido_codigo": mascarar_cpf(ne.get("favorecido_codigo")), "favorecido_nome": ne.get("favorecido_nome"),
        "valor_atual": ne.get("valor_atual"),
        "valor_incluido": ne.get("valor_incluido"),
        "valor_reforcado": ne.get("valor_reforcado"),
        "valor_anulado": ne.get("valor_anulado"),
        "situacao": sit,
        "situacao_rotulo": rot,
        "versao": ne.get("versao"),
        "tem_pdf": ne.get("tem_pdf", True),
        "tipo_empenho": ne.get("tipo_empenho"),
        "data_emissao": ne.get("data_emissao"), "nd": ne.get("nd"), "pi": ne.get("pi"),
        "n_itens": len(ne.get("itens", [])), "pregao": (ne.get("derivados") or {}).get("pregao"),
        "nc": (ne.get("derivados") or {}).get("nc"),
    }


def ne(ug: str, chave: str) -> dict | None:
    return _carregar("nes.json", {}).get("nes", {}).get(f"{ug}:{chave}")


def candidatas_ne(chave: str) -> list[dict]:
    """A NE pode existir nas duas UGs (160329 e 167329) com o mesmo número."""
    return [resumo_ne(ne_) for k, ne_ in _carregar("nes.json", {}).get("nes", {}).items() if ne_["chave"] == chave]


def registrar_ne(ne_: dict) -> None:
    with _trava:
        arq = config.DADOS / "nes.json"
        dados = json.loads(arq.read_text(encoding="utf-8")) if arq.is_file() else {"nes": {}, "falhas": []}
        dados.setdefault("nes", {})[f"{ne_['ug_pasta']}:{ne_['chave']}"] = ne_
        dados["total"] = len(dados["nes"])
        arq.parent.mkdir(parents=True, exist_ok=True)
        tmp = arq.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(arq)
        _cache.pop(str(arq), None)


# ----------------------------------------------------------------------------- catálogos dinâmicos
def finalidades() -> list[dict]:
    fixas = list(config.catalogos()["finalidades"])
    extra_arq = config.DADOS / "finalidades_extra.json"
    if extra_arq.is_file():
        try:
            extra = json.loads(extra_arq.read_text(encoding="utf-8"))
            fixas += [dict(e, extra=True) for e in extra]
        except (OSError, ValueError):
            pass
    return fixas


def salvar_finalidade_extra(texto: str, objeto: str) -> str:
    """Guarda finalidade nova digitada pelo usuário (fica marcada como extra até ser promovida)."""
    arq = config.DADOS / "finalidades_extra.json"
    with _trava:
        atual = json.loads(arq.read_text(encoding="utf-8")) if arq.is_file() else []
        for e in atual:
            if _norm(e["texto"]) == _norm(texto):
                return e["id"]
        novo_id = f"EXTRA_{len(atual) + 1:03d}"
        atual.append({"id": novo_id, "objeto": objeto, "texto": texto})
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(json.dumps(atual, ensure_ascii=False, indent=1), encoding="utf-8")
        return novo_id


def caminho_pdf(caminho: str | Path) -> Path | None:
    p = Path(caminho)
    return p if p.is_file() else None


# ----------------------------------------------------------------------------- contratos vigentes
def carregar_contratos() -> list[dict]:
    return _carregar("contratos.json", {"contratos": []}).get("contratos", [])


def resumo_contratos() -> dict:
    dados = _carregar("contratos.json", {"contratos": [], "total": 0})
    tot_brl = dados.get("total_valor_global_brl")
    if not tot_brl and "total_valor_global" in dados:
        try:
            val_f = float(dados["total_valor_global"])
            tot_brl = f"R$ {val_f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            tot_brl = "R$ 0,00"
    return {
        "posicao": dados.get("posicao"),
        "total": dados.get("total", len(dados.get("contratos", []))),
        "total_valor_global": dados.get("total_valor_global", "0"),
        "total_valor_global_brl": tot_brl or "R$ 0,00",
        "fonte": dados.get("fonte", "Contratos.gov.br"),
    }


def contrato_por_numero(numero: str) -> dict | None:
    norm_num = re.sub(r"\D", "", str(numero))
    for c in carregar_contratos():
        c_num = re.sub(r"\D", "", c.get("numero", ""))
        c_comp = re.sub(r"\D", "", c.get("numero_completo", ""))
        if (norm_num and norm_num in (c_num, c_comp)) or str(numero).strip().upper() in (
            c.get("numero", "").upper(),
            c.get("numero_completo", "").upper(),
            c.get("numero_simples", "").upper(),
        ):
            return c
    return None


def buscar_contratos(q: str = "", categoria: str = "") -> list[dict]:
    termo = _norm(q)
    cat_filtro = _norm(categoria)
    todos = carregar_contratos()
    res = []
    for c in todos:
        if cat_filtro and cat_filtro not in _norm(c.get("categoria", "")) and cat_filtro not in _norm(c.get("objeto_tipo", "")):
            continue
        if not termo:
            res.append(c)
            continue
        candidatos = [
            c.get("numero", ""),
            c.get("numero_simples", ""),
            c.get("numero_completo", ""),
            c.get("objeto", ""),
            c.get("objeto_resumo", ""),
            c.get("fornecedor_nome", ""),
            c.get("fornecedor_cnpj", ""),
            c.get("processo", ""),
            c.get("modalidade_compra", ""),
            " ".join(c.get("temas", [])),
        ]
        if any(termo in _norm(campo) for campo in candidatos):
            res.append(c)
    return res


