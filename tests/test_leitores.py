# -*- coding: utf-8 -*-
"""Leitura de PDFs reais do SIAFI (pulados se o Drive do repositório não estiver disponível)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.leitor_nc import itens_do_bcms, ler_nc
from app.leitor_ne import derivados_da_descricao, ler_ne

RAIZ = Path(__file__).resolve().parents[2] / "EMPENHOS"


def _achar(pasta: Path, padrao: str) -> Path:
    achados = sorted(pasta.glob(padrao)) if pasta.is_dir() else []
    if not achados:
        pytest.skip(f"PDF real não disponível: {pasta / padrao}")
    return achados[0]


def test_ne_real_com_inclusao_e_anulacao():
    ne = ler_ne(_achar(RAIZ / "Empenhos" / "160329", "NE_160329_2026NE000277_*.pdf"))
    assert ne["chave"] == "2026NE000277" and ne["ug_emitente"] == "160329"
    assert (ne["nd"], ne["ptres"], ne["pi"], ne["tipo_empenho"]) == ("339030", "167864", "SEG ALOEXE", "Global")
    assert Decimal(ne["valor_atual"]) == Decimal("117746.44")
    item = ne["itens"][0]
    assert "Inclusão" in [o["operacao"] for o in item["operacoes"]]
    assert Decimal(item["operacoes"][0]["quantidade"]) == Decimal("170380.01")
    assert Decimal(item["valor_liquido"]) == Decimal("117746.44") and ne["avisos"] == []
    der = derivados_da_descricao(ne["descricao"])
    assert der["nc"] == "2026NC415767" and der["pregao"] == "90007/2026" and der["papel"] == "UGG"


def test_ne_com_item_que_atravessa_a_quebra_de_pagina():
    ne = ler_ne(_achar(RAIZ / "Empenhos" / "160329", "NE_160329_2026NE000211_*.pdf"))
    assert ne["avisos"] == [] and len(ne["itens"]) == 10  # itens 3, 6 e 9 repetiam o cabeçalho na página seguinte
    assert Decimal(ne["valor_atual"]) == Decimal("20155.00")


def test_nc_real_e_celula_destino():
    nc = ler_nc(_achar(RAIZ / "Notas de Crédito" / "160329", "NC_160035_2026NC400182_*.pdf"))
    assert nc["chave"] == "2026NC400182" and nc["tipo_nc"] == "Descentralização de Crédito" and nc["avisos"] == []
    item = itens_do_bcms(nc)[0]
    cel = item["destino"][0]
    assert (cel["nd"], cel["pi"], cel["ptres"], cel["ugr"]) == ("339015", "K8COMSIDIAR", "232135", "160329")
    assert item["origem"][0]["nd"] == "339000"  # origem sem elemento; destino detalhado pelo emitente


def test_nc_com_muitas_ug_favorecidas():
    nc = ler_nc(_achar(RAIZ / "Notas de Crédito" / "167329", "NC_167086_2026NC000070_*.pdf"))
    assert len(nc["itens"]) == 29 and nc["avisos"] == []
    assert [i["ug_favorecida"] for i in itens_do_bcms(nc)] == ["167329"]
