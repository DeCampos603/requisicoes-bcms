# -*- coding: utf-8 -*-
"""Aritmética monetária exata (Decimal) e formatação BRL.

Regra do workspace (.agents/rules/financial-precision.md): nenhum float em valor monetário.
O total de cada item é Quantidade × Valor Unitário arredondado a centavos (ROUND_HALF_UP),
como o SIAFI faz (ex.: 170.380,01 × 0,95 = 161.861,0095 → 161.861,01).
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

CENTAVO = Decimal("0.01")
ZERO = Decimal("0")


class ValorInvalido(ValueError):
    """Texto que não representa um número no padrão pt-BR ou internacional."""


def q2(valor: Decimal) -> Decimal:
    """Arredonda para centavos (meio para cima)."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def parse_decimal(texto: object) -> Decimal:
    """Converte '1.234,56', '1234,56', '1234.56', 1234.56 ou Decimal em Decimal.

    Regra: se há vírgula, ela é o separador decimal e os pontos são milhares. Sem vírgula,
    um único ponto seguido de 1–4 dígitos é decimal ('0.95'); pontos em grupos de 3 dígitos
    ('1.234.567') são milhares.
    """
    return _limitar(_converter(texto), texto)


_MILHAR = re.compile(r"\d{1,3}(?:\.\d{3})+")  # 1.234 · 12.345.678: o ponto só vale como milhar em grupos de 3 dígitos
MAX_INTEIROS = 15  # até R$ 999 trilhões: acima disso é erro de digitação (e estouraria o contexto Decimal)
MAX_DECIMAIS = 6


def _limitar(v: Decimal, original: object) -> Decimal:
    """Recusa NaN/Infinity e valores com dígitos demais (entrada hostil ou erro de digitação)."""
    if not v.is_finite():
        raise ValorInvalido("o valor não é um número finito")
    t = v.as_tuple()
    if -t.exponent > MAX_DECIMAIS or len(t.digits) + t.exponent > MAX_INTEIROS:
        raise ValorInvalido(f"'{str(original)[:30]}' tem dígitos demais")
    return v


def _converter(texto: object) -> Decimal:
    if isinstance(texto, Decimal):
        return texto
    if isinstance(texto, bool):
        raise ValorInvalido("valor lógico não é número")
    if isinstance(texto, int):
        return Decimal(texto)
    if isinstance(texto, float):
        return Decimal(str(texto))
    s = str(texto if texto is not None else "").strip()
    if len(s) > 40:
        raise ValorInvalido("texto longo demais para ser um número")
    s = s.replace("R$", "").replace("\xa0", "").replace(" ", "")
    if not s:
        raise ValorInvalido("valor vazio")
    sinal = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    if not re.fullmatch(r"[\d.,]+", s):
        raise ValorInvalido(f"'{texto}' não é um número")
    if "," in s:
        if s.count(",") > 1:
            raise ValorInvalido(f"'{texto}' tem mais de uma vírgula")
        inteiro, frac = s.split(",")
        if not (inteiro or frac):
            raise ValorInvalido(f"'{texto}' não é um número")
        if "." in inteiro and not _MILHAR.fullmatch(inteiro):  # '1.23,45' ou '12.34.567,00' são erro de digitação, não 123,45
            raise ValorInvalido(f"'{texto}' tem pontos fora do padrão de milhar (1.234.567,89)")
        inteiro = inteiro.replace(".", "")
        if (inteiro and not re.fullmatch(r"\d+", inteiro)) or not re.fullmatch(r"\d*", frac):
            raise ValorInvalido(f"'{texto}' não é um número")
        s = f"{inteiro or '0'}.{frac or '0'}"
    elif "." in s:
        if _MILHAR.fullmatch(s):  # '1.234' ou '1.234.567': pontos como separador de milhar
            s = s.replace(".", "")
        elif not re.fullmatch(r"(?=.*\d)\d*\.\d*", s):  # um único ponto decimal ('0.95'); '1.23.4' e '12.34.567' são recusados
            raise ValorInvalido(f"'{texto}' tem pontos fora do padrão: use 1.234,56")
    try:
        return Decimal(s) * sinal
    except InvalidOperation as e:  # pragma: no cover - blindagem
        raise ValorInvalido(f"'{texto}' não é um número") from e


def total_item(quantidade: Decimal, valor_unitario: Decimal) -> Decimal:
    with localcontext() as ctx:  # precisão folgada: o produto de dois valores de até 21 dígitos não estoura o contexto padrão
        ctx.prec = 60
        return q2(quantidade * valor_unitario)


def _agrupar_milhar(inteiro: str) -> str:
    partes = []
    while len(inteiro) > 3:
        partes.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    partes.insert(0, inteiro)
    return ".".join(partes)


def fmt_num(valor: Decimal, casas: int = 2) -> str:
    """1234.5 → '1.234,50' (pt-BR)."""
    q = Decimal(1).scaleb(-casas)
    v = valor.quantize(q, rounding=ROUND_HALF_UP)
    sinal = "-" if v < 0 else ""
    inteiro, _, frac = f"{abs(v):f}".partition(".")
    frac = (frac + "0" * casas)[:casas]
    return f"{sinal}{_agrupar_milhar(inteiro)}" + (f",{frac}" if casas else "")


def brl(valor: Decimal) -> str:
    """Decimal → 'R$ 1.234,56'."""
    return f"R$ {fmt_num(valor, 2)}"


def fmt_qtd(valor: Decimal) -> str:
    """Quantidade sem zeros inúteis: 10 → '10'; 2.5 → '2,5'; 170380.01 → '170.380,01'."""
    n = valor.normalize()
    casas = max(0, -n.as_tuple().exponent) if n.as_tuple().exponent < 0 else 0
    return fmt_num(valor, min(casas, 5))


def para_str(valor: Decimal) -> str:
    """Representação canônica para JSON (string, ponto decimal)."""
    return f"{valor:f}"


def fmt_vu(valor: Decimal) -> str:
    """Valor unitário: de 2 a 4 casas (0,9500 → 'R$ 0,95'; 0,8651 → 'R$ 0,8651'), como no SIAFI."""
    casas = 2
    for c in (4, 3):
        if valor != valor.quantize(Decimal(1).scaleb(-(c - 1)), rounding=ROUND_HALF_UP):
            casas = c
            break
    return f"R$ {fmt_num(valor, casas)}"
