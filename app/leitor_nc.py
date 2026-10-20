# -*- coding: utf-8 -*-
"""Leitura das Notas de Crédito (NC) em PDF ("Nota de Crédito Orçamentária" do SIAFI).

Uma NC pode ter dezenas de UGs favorecidas (ex.: descentralização para todo o Exército); só os
itens cuja UG favorecida é do BCMS (160329 ou 167329) interessam ao sistema, mas o leitor devolve
todos para conferência. Nunca inventa dado: o que não for encontrado fica ausente e gera aviso.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pymupdf

from .dinheiro import ZERO, ValorInvalido, parse_decimal

LIMITE_PAGINAS = 300
LIMITE_CARACTERES = 2_000_000
RE_NC_NOME = re.compile(r"^NC_(\d{6})_(\d{4})NC(\d{6})_v(\d+)_(\d{14})\.pdf$", re.I)

# Esfera, PTRES, Fonte, ND, UGR, Plano Interno ('-' quando não há), Valor.
# O PI é UM token ou DOIS ('SEG ALOEXE'): sem "(.+?)", que era quadrático em PDF hostil (ReDoS).
RE_CELULA = re.compile(
    r"(\d)\s+([0-9A-Z]{4,6})\s+([0-9A-Z]{10})\s+(\d{6})\s+(\d{5,6})\s+(\S+(?: \S+)?)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})(?=\s|$)"
)


def _dec(txt: str) -> Decimal:
    try:
        return parse_decimal(txt)
    except ValorInvalido:
        return ZERO


def _celulas(trecho: str) -> list[dict]:
    celulas = []
    for m in RE_CELULA.finditer(trecho):
        pi = m.group(6).strip()
        celulas.append({
            "esfera": m.group(1), "ptres": m.group(2), "fonte": m.group(3), "nd": m.group(4),
            "ugr": m.group(5), "pi": None if pi == "-" else pi, "valor": str(_dec(m.group(7))),
        })
    return celulas


def ler_nc(caminho: Path) -> dict:
    caminho = Path(caminho)
    avisos: list[str] = []
    doc = pymupdf.open(caminho)
    try:
        if len(doc) > LIMITE_PAGINAS:
            raise ValueError(f"PDF com {len(doc)} páginas: acima do limite de {LIMITE_PAGINAS} para uma NC.")
        texto = "\n".join(p.get_text() for p in doc)
    finally:
        doc.close()
    if len(texto) > LIMITE_CARACTERES:
        raise ValueError("Texto do PDF acima do limite razoável para uma NC.")
    if "Nota de Crédito" not in texto:
        raise ValueError("O PDF não é uma Nota de Crédito do SIAFI (título 'Nota de Crédito' ausente).")
    plano = re.sub(r"\s+", " ", texto)

    def achar(padrao: str, flags: int = 0, grupo: int = 1):
        m = re.search(padrao, plano, flags)
        return m.group(grupo).strip() if m else None

    ug_emit = achar(r"UG Emitente Nome Moeda (\d{6}) ")
    nome_emit = achar(r"UG Emitente Nome Moeda \d{6} (.+?) (?:REAL|DOLAR|EURO)\b")
    ano = achar(r"Ano Tipo Número Tipo de NC Status (\d{4}) NC \d+")
    numero = achar(r"Ano Tipo Número Tipo de NC Status \d{4} NC (\d+)")
    tipo_nc = achar(r"Ano Tipo Número Tipo de NC Status \d{4} NC \d+ (.+?) (?:Contabilizada|Cancelada|Estornada|Pendente|Aguardando)")
    status = achar(r"Ano Tipo Número Tipo de NC Status \d{4} NC \d+ .+? (Contabilizada|Cancelada|Estornada|Pendente|Aguardando\w*)")
    data_emissao = achar(r"Sistema de Origem (\d{2}/\d{2}/\d{4})")
    m_desc = re.search(r"Descrição (.+?) Itens de Contabilização", plano)
    total = achar(r"Total da Lista ([\d\.]+,\d{2})")
    m_versao = re.search(r"Versão (\d{3}) Data/Hora (\d{2}/\d{2}/\d{4} \d{2}:\d{2}) Operação (\w+)", plano)

    nome = RE_NC_NOME.match(caminho.name)
    if nome and numero and int(numero) != int(nome.group(3)):
        avisos.append(f"Número lido no PDF ({numero}) difere do nome do arquivo ({int(nome.group(3))}).")
    numero_final = int(nome.group(3)) if nome else (int(numero) if numero else None)
    ano_final = int(nome.group(2)) if nome else (int(ano) if ano else None)
    ug_emit = ug_emit or (nome.group(1) if nome else None)

    itens = []
    marcadores = list(re.finditer(r"Sequencial (\d+) Situação (\S+)", plano))
    for k, mk in enumerate(marcadores):
        fim = marcadores[k + 1].start() if k + 1 < len(marcadores) else len(plano)
        seg = plano[mk.start():fim]
        m_it = re.search(r"Total do Item ([\d\.]+,\d{2}) UG Favorecida Nome Com Detalhamento (\d{6}) (.+?) (Sim|Não) Células de Origem", seg)
        if not m_it:
            avisos.append(f"Item {mk.group(1)} da NC com layout não reconhecido.")
            continue
        corpo = seg[m_it.end():]
        origem_txt, _, destino_txt = corpo.partition("Células de Destino")
        itens.append({
            "seq": int(mk.group(1)),
            "situacao": mk.group(2),
            "valor": str(_dec(m_it.group(1))),
            "ug_favorecida": m_it.group(2),
            "nome_favorecida": m_it.group(3),
            "com_detalhamento": m_it.group(4) == "Sim",
            "origem": _celulas(origem_txt),
            "destino": _celulas(destino_txt),
        })
    for it in itens:
        soma_dest = sum((Decimal(c["valor"]) for c in it["destino"]), ZERO)
        if it["destino"] and soma_dest != Decimal(it["valor"]):
            avisos.append(f"Item {it['seq']}: soma das células de destino ({soma_dest}) difere do valor do item ({it['valor']}).")
        if not it["destino"]:
            avisos.append(f"Item {it['seq']}: nenhuma célula de destino reconhecida.")
    if total and itens and sum((Decimal(i["valor"]) for i in itens), ZERO) != _dec(total):
        avisos.append("Soma dos itens difere do total da lista.")
    if not itens:
        avisos.append("Nenhum item de contabilização extraído.")

    return {
        "chave": f"{ano_final}NC{numero_final:06d}" if ano_final and numero_final else None,
        "ug_emitente": ug_emit,
        "nome_emitente": nome_emit,
        "ano": ano_final,
        "numero": numero_final,
        "tipo_nc": tipo_nc,
        "status": status,
        "data_emissao": data_emissao,
        "descricao": re.sub(r"\s+", " ", m_desc.group(1)).strip() if m_desc else None,
        "total": str(_dec(total)) if total else None,
        "versao": m_versao.group(1) if m_versao else (nome.group(4) if nome else None),
        "versao_data_hora": m_versao.group(2) if m_versao else None,
        "operacao_versao": m_versao.group(3) if m_versao else None,
        "itens": itens,
        "arquivo": caminho.name,
        "avisos": avisos,
    }


def itens_do_bcms(nc: dict, ugs: tuple[str, ...] = ("160329", "167329")) -> list[dict]:
    """Itens da NC favorecendo as UGs do BCMS."""
    return [i for i in nc["itens"] if i["ug_favorecida"] in ugs]
