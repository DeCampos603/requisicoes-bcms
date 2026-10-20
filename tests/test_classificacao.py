# -*- coding: utf-8 -*-
"""Sugestão de subitem pela ND da NC (usa o classificador do Robô Extrator, se disponível)."""

import pytest

from app import classificacao

pytestmark = pytest.mark.skipif(not classificacao.disponivel(), reason="classificador do RoboExtratorPregoes_V2 indisponível")


def test_nd_indetalhada():
    assert classificacao.nd_indetalhada("339000") and classificacao.nd_indetalhada("449000")
    assert not classificacao.nd_indetalhada("339030")


def test_subitem_restrito_ao_elemento_da_nd():
    r = classificacao.sugerir_subitens("ACESSORIO CARRO BLINDADO, TIPO CENTRAL ELETRONICA ABS E CAMBIO, VBTP GUARANI", "339030", "MATERIAL")
    assert r["elemento"] == "30" and r["recomendada"] == "39"
    assert all(s["subitem"] for s in r["sugestoes"])


def test_mesma_descricao_em_nd_00_nao_sugere_subitem_mas_indica_a_nd_de_destino():
    r = classificacao.sugerir_subitens("PREGO COM CABEÇA, MATERIAL FERRO COMUM, TIPO CABEÇA LISO", "339000", "MATERIAL")
    assert r["sugestoes"] == [] and r["global_"]["nd"] == "339030"
    alvo = classificacao.nd_de_destino_sugerida(["PREGO COM CABEÇA, MATERIAL FERRO COMUM, TIPO CABEÇA LISO"], "339000", "MATERIAL")
    assert alvo["nd"] == "339030" and any(c["nd"] == "339030" for c in alvo["candidatas"])


def test_verifica_subitem_informado():
    desc = "PREGO COM CABEÇA, MATERIAL FERRO COMUM, TIPO CABEÇA LISO"
    ok = classificacao.verificar_subitem(desc, "24", "339030", "MATERIAL")
    assert ok["si_existe"] and not ok["si_diverge"]
    diverge = classificacao.verificar_subitem(desc, "39", "339030", "MATERIAL")
    assert diverge["si_existe"] and diverge["si_diverge"]
    assert classificacao.verificar_subitem(desc, "98", "339030", "MATERIAL")["si_existe"] is False


def test_candidatas_de_detalhamento_por_objeto():
    mat = {c["nd"] for c in classificacao.nds_candidatas("339000", "MATERIAL")}
    srv = {c["nd"] for c in classificacao.nds_candidatas("339000", "SERVICO")}
    assert "339030" in mat and "339039" in srv and "339030" not in srv
    assert classificacao.nds_candidatas("339030", "MATERIAL") == []
