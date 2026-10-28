# -*- coding: utf-8 -*-
"""Leitura das Notas de Empenho (NE) em PDF emitidas pelo SIAFI ("Impressão Completa").

O layout é o do relatório do SIAFI: página 1 com cabeçalho/classificação/favorecido/descrição e
páginas seguintes com a lista de itens e as operações (Inclusão, Reforço, Anulação). A extração
usa as coordenadas das palavras (PyMuPDF) porque o texto corrido embaralha as colunas.

Nunca inventa dado: campo não encontrado fica None e o arquivo é marcado em `avisos`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pymupdf

from .dinheiro import ZERO, ValorInvalido, parse_decimal

LIMITE_PAGINAS = 300
RE_NE_NOME = re.compile(r"^NE_(\d{6})_(\d{4})NE(\d{6})_v(\d+)_(\w+?)_(\d{14})\.pdf$", re.I)
RE_DATA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_DINHEIRO = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$")
OPERACOES_POSITIVAS = {"inclusão", "inclusao", "reforço", "reforco"}
OPERACOES_NEGATIVAS = {"anulação", "anulacao", "cancelamento"}


def _palavras(pagina) -> list[tuple[float, float, float, float, str]]:
    return [(w[0], w[1], w[2], w[3], w[4]) for w in pagina.get_text("words")]


def _linhas(palavras, tol: float = 2.5) -> list[list[tuple[float, float, float, float, str]]]:
    """Agrupa palavras em linhas por coordenada y."""
    ordenadas = sorted(palavras, key=lambda w: (round(w[1], 1), w[0]))
    linhas: list[list] = []
    for w in ordenadas:
        if linhas and abs(linhas[-1][0][1] - w[1]) <= tol:
            linhas[-1].append(w)
        else:
            linhas.append([w])
    return [sorted(l, key=lambda w: w[0]) for l in linhas]


def _achar_rotulo(palavras, *tokens: str, y_min: float = 0.0):
    """Primeira ocorrência da sequência de palavras `tokens` (mesma linha). Devolve (x0, y0)."""
    for linha in _linhas(palavras):
        if linha[0][1] < y_min:
            continue
        textos = [w[4] for w in linha]
        for i in range(len(textos) - len(tokens) + 1):
            if textos[i:i + len(tokens)] == list(tokens):
                return linha[i][0], linha[i][1]
    return None


def _colunas(palavras, rotulos: dict[str, tuple[float, float]], dy_min=8.0, dy_max=20.0) -> dict[str, str]:
    """Valores logo abaixo de uma linha de rótulos, atribuídos à coluna do rótulo mais à esquerda."""
    if not rotulos:
        return {}
    y_rot = min(y for _, y in rotulos.values())
    xs = sorted((x, nome) for nome, (x, _) in rotulos.items())
    saida: dict[str, list[str]] = defaultdict(list)
    for w in sorted(palavras, key=lambda w: (round(w[1], 1), w[0])):
        if not (y_rot + dy_min <= w[1] <= y_rot + dy_max):
            continue
        nome = None
        for x, n in xs:
            if w[0] >= x - 3:
                nome = n
        if nome:
            saida[nome].append(w[4])
    return {k: " ".join(v) for k, v in saida.items()}


def _dec(txt: str | None) -> Decimal | None:
    if not txt:
        return None
    try:
        return parse_decimal(txt)
    except ValorInvalido:
        return None


def _qtd(txt: str) -> Decimal | None:
    """'662.791,00000' → Decimal. A quantidade vem quebrada em duas linhas no PDF."""
    try:
        return parse_decimal(txt)
    except ValorInvalido:
        return None


def _extrair_itens(paginas_palavras: list[list]) -> list[dict]:
    itens: list[dict] = []
    subelemento = None
    sub_nome = None
    atual: dict | None = None
    for palavras in paginas_palavras:
        linhas = _linhas(palavras)
        i = 0
        estado = "fora"
        while i < len(linhas):
            linha = linhas[i]
            textos = [w[4] for w in linha]
            linha_txt = " ".join(textos)
            x0 = linha[0][0]
            if textos[0] == "Subelemento" and len(textos) >= 2:
                subelemento = textos[1]
                sub_nome = " ".join(textos[3:]) if len(textos) > 3 else None
                i += 1
                continue
            # cabeçalho de item: "001 ... Item compra: 00001 - DESCRIÇÃO ... 570.000,26"
            if re.fullmatch(r"\d{3}", textos[0]) and x0 < 60 and RE_DINHEIRO.match(textos[-1] if len(textos) > 1 else ""):
                if itens and itens[-1]["seq"] == int(textos[0]):
                    # item que atravessa a quebra de página: o cabeçalho se repete; funde em vez de duplicar
                    atual = itens[-1]
                    atual["_continuacao"] = True
                    corpo = " ".join(w[4] for w in linha[1:-1])
                    m = re.match(r"Item compra:\s*(\d+)\s*-\s*(.*)", corpo)
                    novo = m.group(2) if m else corpo
                    if novo and novo not in atual["descricao"] and atual["descricao"] not in novo:
                        atual["descricao"] = (atual["descricao"] + " " + novo).strip()
                    elif novo and atual["descricao"] in novo:
                        atual["descricao"] = novo
                    estado = "descricao"
                    i += 1
                    continue
                atual = {
                    "seq": int(textos[0]),
                    "subelemento": subelemento,
                    "subelemento_nome": sub_nome,
                    "valor_item": str(_dec(textos[-1])),
                    "item_compra": None,
                    "descricao": "",
                    "operacoes": [],
                }
                corpo = [w for w in linha[1:-1]]
                texto = " ".join(w[4] for w in corpo)
                m = re.match(r"Item compra:\s*(\d+)\s*-\s*(.*)", texto)
                if m:
                    atual["item_compra"] = int(m.group(1))
                    atual["descricao"] = m.group(2)
                else:
                    atual["descricao"] = texto
                itens.append(atual)
                estado = "descricao"
                i += 1
                continue
            if atual is not None and estado == "descricao":
                if textos[0] == "Data" and "Operação" in textos:
                    estado = "operacoes"
                    i += 1
                    continue
                if x0 >= 85 and not RE_DATA.match(textos[0]):
                    atual["descricao"] = (atual["descricao"] + " " + linha_txt).strip()
                    i += 1
                    continue
            if atual is not None and estado == "operacoes":
                if RE_DATA.match(textos[0]) and len(textos) >= 5:
                    op = textos[1]
                    # linha: data, operação, quantidade, valor unitário, valor total
                    qtd_txt, vu_txt, vt_txt = textos[2], textos[3], textos[4]
                    # a "cauda" da quantidade (zeros) cai na linha seguinte, na mesma coluna
                    if i + 1 < len(linhas):
                        prox = linhas[i + 1]
                        if len(prox) == 1 and re.fullmatch(r"\d{1,3}", prox[0][4]) and abs(prox[0][0] - linha[2][0]) < 6:
                            qtd_txt += prox[0][4]
                            i += 1
                    nova = {
                        "data": textos[0], "operacao": op,
                        "quantidade": str(_qtd(qtd_txt)),
                        "valor_unitario": str(_dec(vu_txt)),
                        "valor_total": str(_dec(vt_txt)),
                    }
                    # só descarta repetida quando o cabeçalho do item se repetiu na quebra de página
                    if not (atual.get("_continuacao") and nova in atual["operacoes"]):
                        atual["operacoes"].append(nova)
                    i += 1
                    continue
                if textos[0] in ("Assinaturas", "Ordenador", "Responsável"):
                    estado = "fora"
            i += 1
    return itens


def _liquido(item: dict) -> Decimal:
    total = ZERO
    for op in item["operacoes"]:
        v = Decimal(op["valor_total"]) if op["valor_total"] != "None" else ZERO
        nome = op["operacao"].lower()
        if nome in OPERACOES_NEGATIVAS:
            total -= v
        elif nome in OPERACOES_POSITIVAS:
            total += v
    return total


def ler_ne(caminho: Path) -> dict:
    """Lê uma NE e devolve um dicionário serializável em JSON."""
    caminho = Path(caminho)
    avisos: list[str] = []
    doc = pymupdf.open(caminho)
    try:
        if len(doc) > LIMITE_PAGINAS:
            raise ValueError(f"PDF com {len(doc)} páginas: acima do limite de {LIMITE_PAGINAS} para uma NE.")
        paginas = [_palavras(p) for p in doc]
        texto_total = "\n".join(p.get_text() for p in doc)
    finally:
        doc.close()
    if "Nota de Empenho" not in texto_total:
        raise ValueError("O PDF não é uma Nota de Empenho do SIAFI (título 'Nota de Empenho' ausente).")

    p0 = paginas[0]
    rot = {}
    for nome, tokens in {
        "esfera": ("Esfera",), "ptres": ("PTRES",), "fonte": ("Fonte", "de", "Recurso"),
        "nd": ("Natureza", "da", "Despesa"), "ugr": ("UGR",), "pi": ("Plano", "Interno"),
    }.items():
        achado = _achar_rotulo(p0, *tokens)
        if achado and (nome != "nd" or abs(achado[1] - (_achar_rotulo(p0, "Esfera") or achado)[1]) < 3):
            rot[nome] = achado
    cel = _colunas(p0, rot)

    rot2 = {}
    for nome, tokens in {
        "emissao": ("Data", "de", "Emissão"), "tipo": ("Tipo",), "processo": ("Processo",),
        "cambio": ("Taxa", "de", "Câmbio"), "valor": ("Valor",),
    }.items():
        pos = _achar_rotulo(p0, *tokens, y_min=340)  # ignora "Tipo"/"Valor" do cabeçalho
        if pos:
            rot2[nome] = pos
    topo = _colunas(p0, rot2)

    # cabeçalho: número/ano/tipo da NE (linha "Ano Tipo Número")
    m_ano = _achar_rotulo(p0, "Ano")
    cab = _colunas(p0, {"ano": m_ano, "tipo": _achar_rotulo(p0, "Tipo"), "numero": _achar_rotulo(p0, "Número")}) if m_ano else {}

    # favorecido: abaixo do 2º "Código"/"Nome" (primeiro é a UG emitente)
    favorecido_cod = favorecido_nome = None
    rotulo_fav = _achar_rotulo(p0, "Favorecido")
    if rotulo_fav:
        y0 = rotulo_fav[1]
        faixa = [w for w in p0 if y0 + 22 <= w[1] <= y0 + 36]
        faixa.sort(key=lambda w: w[0])
        if faixa:
            favorecido_cod = faixa[0][4]
            favorecido_nome = " ".join(w[4] for w in faixa[1:])

    # descrição (texto livre da NE) entre os rótulos "Descrição" e "Local da Entrega"
    descricao = None
    r_desc = _achar_rotulo(p0, "Descrição")
    r_local = _achar_rotulo(p0, "Local", "da", "Entrega")
    r_info = _achar_rotulo(p0, "Informação", "Complementar")
    if r_desc and r_local:
        banda = [w for w in p0 if r_desc[1] + 8 <= w[1] < r_local[1] - 2]
        descricao = " ".join(" ".join(w[4] for w in l) for l in _linhas(banda))
    local = info = None
    if r_local and r_info:
        local = " ".join(" ".join(w[4] for w in l) for l in _linhas([w for w in p0 if r_local[1] + 8 <= w[1] < r_info[1] - 2]))
        info = " ".join(" ".join(w[4] for w in l) for l in _linhas([w for w in p0 if w[1] >= r_info[1] + 8 and w[1] < 790]))
        info = re.split(r"\s*Sistema de Origem", info)[0]

    m_versao = re.search(r"(\d{3})\s*\n?\s*Versão", texto_total)
    m_dh = re.search(r"Versão\s+Data/Hora\s*\n?\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})", texto_total) or \
        re.search(r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\s*\nOperação\s*\n(\w+)", texto_total)
    m_op = re.search(r"Operação\s*\n\s*(Inclusão|Alteração|Anulação|Reforço|Cancelamento)\b", texto_total)
    m_mod = re.search(r"\n(PREGAO|DISPENSA[^\n]*|INEXIGIBILIDADE[^\n]*|CONCORRENCIA[^\n]*|ADESAO[^\n]*)\n", texto_total)
    m_ato = re.search(r"LEI\s+[\d\.]+/\d{4}|LEI\s+\d+", texto_total)
    m_cons = re.search(r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2})(?!:)", texto_total)
    m_ordenador = re.search(r"Ordenador de Despesa\s*\n([^\n*]+)\n", texto_total)
    m_resp = re.search(r"Responsável pela Nota de Empenho\s*\n([^\n*]+)\n", texto_total)

    itens = _extrair_itens(paginas[1:] if len(paginas) > 1 else [])
    for it in itens:
        it.pop("_continuacao", None)
        it["valor_liquido"] = str(_liquido(it))

    nome = RE_NE_NOME.match(caminho.name)
    ug_pdf = None
    r_cod = _achar_rotulo(p0, "Código", y_min=150)  # bloco "UG Emitente": rótulo Código e, abaixo, o código da UG
    if r_cod:
        faixa = sorted((w for w in p0 if r_cod[1] + 8 <= w[1] <= r_cod[1] + 20), key=lambda w: w[0])
        if faixa and re.fullmatch(r"\d{6}", faixa[0][4]):
            ug_pdf = faixa[0][4]
    if nome and ug_pdf and nome.group(1) != ug_pdf:
        avisos.append(f"UG emitente lida no PDF ({ug_pdf}) difere do nome do arquivo ({nome.group(1)}).")
    ug_nome = ug_pdf or (nome.group(1) if nome else None)
    ano_nome = nome.group(2) if nome else None
    num_nome = nome.group(3) if nome else None

    numero_txt = cab.get("numero") or ""
    numero = int(re.sub(r"\D", "", numero_txt) or (num_nome or 0)) if (numero_txt or num_nome) else None
    ano = int(re.sub(r"\D", "", cab.get("ano") or "") or (ano_nome or 0)) or None
    if nome and numero is not None and num_nome and numero != int(num_nome):
        avisos.append(f"Número lido no PDF ({numero}) difere do nome do arquivo ({int(num_nome)}).")
        numero = int(num_nome)
    chave = f"{ano}NE{numero:06d}" if ano and numero else None

    valor_atual = _dec(topo.get("valor"))
    soma_liq = sum((Decimal(it["valor_liquido"]) for it in itens), ZERO)
    if valor_atual is not None and itens and valor_atual != soma_liq:
        avisos.append(f"Soma líquida dos itens ({soma_liq}) difere do valor da NE ({valor_atual}).")
    if not itens:
        avisos.append("Nenhum item de NE extraído.")

    def _lim(s):
        return re.sub(r"\s+", " ", s).strip() if s else None

    return {
        "chave": chave,
        "ug_emitente": ug_nome,
        "ano": ano,
        "numero": numero,
        "versao": m_versao.group(1) if m_versao else (nome.group(4) if nome else None),
        "versao_data_hora": (m_dh.group(1) if m_dh else None),
        "operacao_versao": m_op.group(1) if m_op else None,
        "esfera": cel.get("esfera"), "ptres": cel.get("ptres"), "fonte": cel.get("fonte"),
        "nd": cel.get("nd"), "ugr": cel.get("ugr"), "pi": _lim(cel.get("pi")),
        "data_emissao": topo.get("emissao"),
        "tipo_empenho": topo.get("tipo"),
        "processo": topo.get("processo"),
        "valor_atual": str(valor_atual) if valor_atual is not None else None,
        "favorecido_codigo": favorecido_cod,
        "favorecido_nome": _lim(favorecido_nome),
        "descricao": _lim(descricao),
        "local_entrega": _lim(local),
        "minuta": _lim(info),
        "modalidade_licitacao": m_mod.group(1).strip() if m_mod else None,
        "amparo": m_ato.group(0) if m_ato else None,
        "ordenador": _lim(m_ordenador.group(1)) if m_ordenador else None,
        "responsavel": _lim(m_resp.group(1)) if m_resp else None,
        "consulta_em": m_cons.group(1) if m_cons else None,
        "itens": itens,
        "arquivo": caminho.name,
        "avisos": avisos,
    }


# ----------------------------------------------------------------------------- derivados da descrição
RE_NC_NA_DESC = re.compile(r"(\d{4})\s?NC\s?(\d{6})", re.I)
RE_PREGAO_NA_DESC = re.compile(r"PREG[ÃA]O\.?\s*(?:UG[GPN]+\.?\s*)?(\d{1,5})\s*/\s*(\d{4})", re.I)
RE_UGG_UGP = re.compile(r"\b(UGG|UGP|UGNP|UGN)\b", re.I)


def derivados_da_descricao(descricao: str | None) -> dict:
    """NC, pregão e papel da UG mencionados no texto livre da NE (padrão da SALC)."""
    d = descricao or ""
    nc = RE_NC_NA_DESC.search(d)
    pr = RE_PREGAO_NA_DESC.search(d)
    papel = RE_UGG_UGP.search(d)
    # UASG citada junto ao pregão: "UASG 160298", "(160329)" ou "UGG 90007/2026 160329"
    uasg = None
    if pr:
        resto = d[pr.end(): pr.end() + 60]
        m = re.search(r"\b(1\d{5}|\d{6})\b", resto)
        if m:
            uasg = m.group(1)
    return {
        "nc": f"{nc.group(1)}NC{nc.group(2)}" if nc else None,
        "pregao": f"{pr.group(1)}/{pr.group(2)}" if pr else None,
        "uasg_pregao": uasg,
        "papel": papel.group(1).upper() if papel else None,
    }


def completar_registro(ne: dict) -> dict:
    """Acrescenta ao registro lido da NE o que o sistema deriva dela: NC/pregão citados e totais por operação."""
    ne["derivados"] = derivados_da_descricao(ne.get("descricao"))
    inc = refo = anu = ZERO
    for it in ne.get("itens", []):
        for op in it.get("operacoes", []):
            v = Decimal(op["valor_total"]) if op.get("valor_total") and op["valor_total"] != "None" else ZERO
            nome = str(op.get("operacao") or "").lower()
            if nome.startswith("inclus"):
                inc += v
            elif nome.startswith("refor"):
                refo += v
            elif nome.startswith(("anula", "cancel")):
                anu += v
    ne["valor_incluido"], ne["valor_reforcado"], ne["valor_anulado"] = str(inc), str(refo), str(anu)
    ne["tem_pdf"] = True

    # Situação contábil e operacional em vigor
    v_atual = Decimal(str(ne.get("valor_atual") or "0"))
    if v_atual <= ZERO:
        ne["situacao"] = "TOTALMENTE_ANULADO"
        ne["situacao_rotulo"] = "Totalmente Anulada"
    elif refo > ZERO and anu > ZERO:
        ne["situacao"] = "MISTO"
        ne["situacao_rotulo"] = "Reforçada e Parcialmente Anulada"
    elif refo > ZERO:
        ne["situacao"] = "REFORCADO"
        ne["situacao_rotulo"] = "Reforçada"
    elif anu > ZERO:
        ne["situacao"] = "PARCIALMENTE_ANULADO"
        ne["situacao_rotulo"] = "Parcialmente Anulada"
    else:
        ne["situacao"] = "NORMAL"
        ne["situacao_rotulo"] = "Empenho Inicial"

    return ne
