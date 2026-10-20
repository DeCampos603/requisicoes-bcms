# -*- coding: utf-8 -*-
from decimal import Decimal

import pytest

from app import dinheiro
from app.validadores import (ErroFormato, validar_cnpj, validar_cpf, validar_diex, validar_documento_favorecido, validar_nc,
                             validar_nd, validar_ne, validar_nome_militar, validar_numero_licitacao, validar_nup,
                             validar_pca_classe, validar_pca_contratacao, validar_pi, validar_subitem, validar_texto_curto,
                             validar_uasg)


# ----------------------------------------------------------------------------- NUP
@pytest.mark.parametrize("nup", ["64619.003990/2026-08", "64619003990202608", "64619.001246/2024-07"])
def test_nup_valido(nup):
    assert validar_nup(nup, 2026).count(".") == 1


@pytest.mark.parametrize("nup", ["", "64619.003990/2026", "6461.003990/2026-08", "64619.000000/2026-08", "64619.003990/1999-08", "64619.003990/2099-08", "abc"])
def test_nup_invalido(nup):
    with pytest.raises(ErroFormato):
        validar_nup(nup, 2026)


def test_nup_nao_exige_digito_verificador():
    """Os NUP reais do SPED não seguem o módulo 97 da Portaria 2.321/2014; só a máscara vale."""
    assert validar_nup("64619.003990/2026-99", 2026) == "64619.003990/2026-99"


# ----------------------------------------------------------------------------- DIEx
@pytest.mark.parametrize("entrada,esperado", [
    ("71-Transporte/CCAp/Cmdo", "71-Transporte/CCAp/Cmdo"),
    ("71 - Transporte / CCAp / Cmdo", "71-Transporte/CCAp/Cmdo"),
    ("DIEx nº 22 - ALMOX/S4/BCMS", "22-ALMOX/S4/BCMS"),
    ("DIEx Requisitório nº 79-Transporte/CCAp/Cmdo", "79-Transporte/CCAp/Cmdo"),
    ("01-SEÇ TRNP/Cmdo/BCMS", "01-SEÇ TRNP/Cmdo/BCMS"),
    ("384-DM7/CCOMGEX", "384-DM7/CCOMGEX"),
])
def test_diex_valido(entrada, esperado):
    assert validar_diex(entrada) == esperado


@pytest.mark.parametrize("entrada", ["", "71", "71-Transporte", "Transporte/CCAp/Cmdo", "0-ABC/DEF", "2185 APROV", "71-<b>/x", "123456-ABC/DEF"])
def test_diex_invalido(entrada):
    with pytest.raises(ErroFormato):
        validar_diex(entrada)


# ----------------------------------------------------------------------------- CNPJ / CPF
def test_cnpj_do_modelo_oficial_e_normalizado():
    # o DIEx modelo trazia "29.381.249.0001-76" (ponto no lugar da barra) e "13.378.981/000157" (sem hífen)
    assert validar_cnpj("29.381.249.0001-76") == "29.381.249/0001-76"
    assert validar_cnpj("13.378.981/000157") == "13.378.981/0001-57"


def test_cnpj_digito_verificador():
    with pytest.raises(ErroFormato):
        validar_cnpj("29.381.249/0001-77")
    with pytest.raises(ErroFormato):
        validar_cnpj("11.111.111/1111-11")
    with pytest.raises(ErroFormato):
        validar_cnpj("123")


def test_cnpj_alfanumerico_da_receita():
    # exemplo da documentação da RFB (CNPJ alfanumérico, vigente desde jul/2026)
    assert validar_cnpj("12.ABC.345/01DE-35") == "12.ABC.345/01DE-35"
    with pytest.raises(ErroFormato):
        validar_cnpj("12.ABC.345/01DE-36")


def test_cpf_e_documento_favorecido():
    assert validar_cpf("529.982.247-25") == "529.982.247-25"
    assert validar_documento_favorecido("52998224725") == "529.982.247-25"
    assert validar_documento_favorecido("29.381.249/0001-76").startswith("29.")
    with pytest.raises(ErroFormato):
        validar_cpf("111.111.111-11")


# ----------------------------------------------------------------------------- SIAFI
def test_ne_e_nc_normalizados():
    assert validar_ne("2026NE165") == "2026NE000165"
    assert validar_ne("2026 NE 000165") == "2026NE000165"
    assert validar_nc("2026nc415767") == "2026NC415767"
    assert validar_nc("160504000012026NC415767") == "2026NC415767"
    for ruim in ("NE165x", "2026NE0", "2026XX000001", ""):
        with pytest.raises(ErroFormato):
            validar_ne(ruim)


def test_uasg_nd_si_pi():
    assert validar_uasg("160329") == "160329"
    with pytest.raises(ErroFormato):
        validar_uasg("16032")
    assert validar_nd("339030") == "339030"
    with pytest.raises(ErroFormato):
        validar_nd("159030")
    assert validar_subitem("9") == "09"
    for ruim in ("00", "100", "ab"):
        with pytest.raises(ErroFormato):
            validar_subitem(ruim)
    assert validar_pi("e5mmpdrdege") == "E5MMPDRDEGE"
    assert validar_pi("SEG ALOEXE") == "SEG ALOEXE"


def test_numero_licitacao_por_modalidade():
    assert validar_numero_licitacao("90007/2026", "PREGAO", 2026) == "90007/2026"
    with pytest.raises(ErroFormato):
        validar_numero_licitacao("07/2024", "PREGAO", 2026)  # pregão da Lei 14.133 é 9XXXX
    assert validar_numero_licitacao("15/2026", "DISPENSA", 2026) == "15/2026"
    with pytest.raises(ErroFormato):
        validar_numero_licitacao("15/2019", "DISPENSA", 2026)


def test_pca_campos():
    assert validar_pca_contratacao("160329-7/2025") == "160329-7/2025"
    with pytest.raises(ErroFormato):
        validar_pca_contratacao("7/2025")
    assert validar_pca_classe("7510 - ARTIGOS PARA ESCRITÓRIO") == "7510 - ARTIGOS PARA ESCRITÓRIO"
    assert validar_pca_classe("7510") == "7510"
    assert validar_pca_classe("873 - SERVIÇOS DE INSTALAÇÃO") == "873 - SERVIÇOS DE INSTALAÇÃO"  # grupo de serviço (CATSER) com 3 dígitos
    with pytest.raises(ErroFormato):
        validar_pca_classe("75 - CURTO")
    with pytest.raises(ErroFormato):
        validar_pca_classe("ARTIGOS")


def test_nome_e_texto_curto():
    assert validar_nome_militar("heric joaquim linhares") == "HERIC JOAQUIM LINHARES"
    for ruim in ("Heric", "H3ric Silva", ""):
        with pytest.raises(ErroFormato):
            validar_nome_militar(ruim)
    assert validar_texto_curto("  texto   com   espaços ", "x", 1, 50) == "texto com espaços"
    with pytest.raises(ErroFormato):
        validar_texto_curto("a" * 300, "x", 0, 100)
    with pytest.raises(ErroFormato):
        validar_texto_curto("com\x00controle", "x", 0, 100)


# ----------------------------------------------------------------------------- dinheiro
@pytest.mark.parametrize("txt,esperado", [("1.234,56", "1234.56"), ("1234,56", "1234.56"), ("1234.56", "1234.56"), ("0,95", "0.95"),
                                          ("R$ 1.755,00", "1755.00"), ("1.234.567", "1234567"), ("1.234", "1234"), ("10", "10"), ("0.95", "0.95")])
def test_parse_decimal(txt, esperado):
    assert dinheiro.parse_decimal(txt) == Decimal(esperado)


@pytest.mark.parametrize("txt", ["", "abc", "1,2,3", "12,3x"])
def test_parse_decimal_invalido(txt):
    with pytest.raises(dinheiro.ValorInvalido):
        dinheiro.parse_decimal(txt)


def test_total_item_arredonda_como_o_siafi():
    # NE 2026NE000277: 170.380,01 × 0,95 = 161.861,0095 → 161.861,01
    assert dinheiro.total_item(Decimal("170380.01"), Decimal("0.95")) == Decimal("161861.01")
    assert dinheiro.total_item(Decimal("662791"), Decimal("0.86")) == Decimal("570000.26")
    assert dinheiro.total_item(Decimal("3"), Decimal("0.335")) == Decimal("1.01")  # 1,005 → meio para cima


def test_formatacao_brl():
    assert dinheiro.brl(Decimal("1234567.5")) == "R$ 1.234.567,50"
    assert dinheiro.brl(Decimal("-5")) == "R$ -5,00"
    assert dinheiro.fmt_vu(Decimal("0.9500")) == "R$ 0,95"
    assert dinheiro.fmt_vu(Decimal("0.8651")) == "R$ 0,8651"
    assert dinheiro.fmt_qtd(Decimal("170380.01000")) == "170.380,01"
    assert dinheiro.fmt_qtd(Decimal("10.000")) == "10"


def test_parse_decimal_milhar_estrito():
    from app.dinheiro import ValorInvalido, parse_decimal
    for ok, esperado in (("1.234,56", "1234.56"), ("1234,56", "1234.56"), ("0.95", "0.95"), ("1.234", "1234"), ("1.234.567", "1234567"),
                         ("12.345.678,90", "12345678.90"), ("10.5", "10.5"), (".5", "0.5"), ("1,", "1.0"), ("R$ 1.000,00", "1000.00")):
        assert parse_decimal(ok) == Decimal(esperado), ok
    for ruim in ("1.23,45", "12.34.567,00", "1.23.4", "1,2.3", "1.2345.678", "1..234", ".", ",", "1.234,5,6", "abc"):
        with pytest.raises(ValorInvalido):
            parse_decimal(ruim)
