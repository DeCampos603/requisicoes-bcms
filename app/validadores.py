# -*- coding: utf-8 -*-
"""Validação estrita de formato (máscaras) dos identificadores usados nos DIEx.

Cada função devolve o valor NORMALIZADO (forma canônica) ou levanta ErroFormato com uma
mensagem pronta para o usuário. O navegador aplica máscara só por conveniência: a verdade
é este módulo, que roda no servidor em toda requisição.

Sobre o NUP: a Portaria Interministerial MJ/MP 2.321/2014 descreve dígitos verificadores
por módulo 97 base 10, mas os NUP reais do SPED do Exército (17 amostras de 2024 e 2026
conferidas em set/2026) NÃO obedecem a essa fórmula. Por isso o NUP é validado só por
máscara + coerência de ano + sequencial não nulo; não se bloqueia por dígito verificador.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

LETRAS = "A-Za-zÀ-ÖØ-öø-ÿ"


class ErroFormato(ValueError):
    """Valor fora do padrão exigido; a mensagem é exibida ao usuário."""


def _s(valor: object) -> str:
    return unicodedata.normalize("NFC", str(valor if valor is not None else "")).strip()


def so_digitos(valor: object) -> str:
    return re.sub(r"\D", "", _s(valor))


# ----------------------------------------------------------------------------- NUP / DIEx
def validar_nup(valor: object, ano_referencia: int | None = None) -> str:
    """NUP no padrão 99999.999999/AAAA-DD."""
    s = _s(valor)
    d = so_digitos(s)
    if len(d) != 17:
        raise ErroFormato("NUP deve ter 17 dígitos no padrão 99999.999999/AAAA-DD (ex.: 64619.003990/2026-08).")
    if not re.fullmatch(r"\d{5}[.\s]?\d{6}[/\s]?\d{4}[-\s]?\d{2}", s):
        raise ErroFormato("NUP fora do padrão 99999.999999/AAAA-DD (ex.: 64619.003990/2026-08).")
    orgao, seq, ano, dv = d[:5], d[5:11], int(d[11:15]), d[15:]
    nup = f"{orgao}.{seq}/{ano:04d}-{dv}"
    atual = ano_referencia or date.today().year
    if seq == "000000":
        raise ErroFormato("NUP com número sequencial zerado.")
    if not (2000 <= ano <= atual + 1):
        raise ErroFormato(f"Ano do NUP ({ano}) fora do intervalo plausível (2000 a {atual + 1}).")
    return nup


_RE_DIEX = re.compile(
    rf"^(?P<num>\d{{1,5}})\s*-\s*(?P<origem>[{LETRAS}0-9º°ª.]+(?:[ \-][{LETRAS}0-9º°ª.]+)*"
    rf"(?:\s*/\s*[{LETRAS}0-9º°ª.]+(?:[ \-][{LETRAS}0-9º°ª.]+)*){{1,4}})$"
)


def validar_diex(valor: object) -> str:
    """Número do DIEx do SPED: <número>-<Seção>/<Escalão>[/<OM>] (ex.: 71-Transporte/CCAp/Cmdo).

    Aceita o prefixo 'DIEx nº' colado por engano e normaliza espaços em volta de '-' e '/'.
    """
    s = _s(valor)
    if len(s) > 90:
        raise ErroFormato("Nº do DIEx longo demais (máximo de 90 caracteres).")
    s = re.sub(r"^(?i:di\s*ex\.?)\s*(?i:requisit[óo]rio)?\s*(?:n[ºo°.]*)?\s*", "", s).strip()
    m = _RE_DIEX.match(s)
    if not m:
        raise ErroFormato(
            "Nº do DIEx fora do padrão: use <número>-<Seção>/<Escalão>/<OM>, "
            "ex.: 71-Transporte/CCAp/Cmdo ou 22-ALMOX/S4/BCMS."
        )
    num = int(m.group("num"))
    if num == 0:
        raise ErroFormato("O número do DIEx não pode ser zero.")
    origem = re.sub(r"\s*/\s*", "/", m.group("origem"))
    origem = re.sub(r"\s{2,}", " ", origem)
    return f"{m.group('num')}-{origem}"


# ----------------------------------------------------------------------------- documentos de pessoas
def _dv_cnpj(base: str) -> str:
    """Dígitos verificadores do CNPJ (numérico ou alfanumérico, IN RFB 2.229/2024)."""
    def calc(corpo: str) -> int:
        pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if len(corpo) == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        soma = sum((ord(c) - 48) * p for c, p in zip(corpo, pesos))
        r = soma % 11
        return 0 if r < 2 else 11 - r
    d1 = calc(base)
    d2 = calc(base + str(d1))
    return f"{d1}{d2}"


def _dv_cpf(base: str) -> str:
    def calc(corpo: str) -> int:
        soma = sum(int(c) * p for c, p in zip(corpo, range(len(corpo) + 1, 1, -1)))
        r = (soma * 10) % 11
        return 0 if r == 10 else r
    d1 = calc(base)
    d2 = calc(base + str(d1))
    return f"{d1}{d2}"


def validar_cnpj(valor: object) -> str:
    """CNPJ (numérico ou alfanumérico) → 'XX.XXX.XXX/XXXX-DD' com dígito verificador conferido."""
    bruto = re.sub(r"[.\-/\s]", "", _s(valor)).upper()
    if not re.fullmatch(r"[0-9A-Z]{12}\d{2}", bruto):
        raise ErroFormato("CNPJ deve ter 14 posições (ex.: 29.381.249/0001-76).")
    if bruto[:12] == bruto[0] * 12 and bruto[0].isdigit():
        raise ErroFormato("CNPJ inválido (todos os dígitos iguais).")
    if _dv_cnpj(bruto[:12]) != bruto[12:]:
        raise ErroFormato("CNPJ inválido: dígito verificador não confere.")
    return f"{bruto[:2]}.{bruto[2:5]}.{bruto[5:8]}/{bruto[8:12]}-{bruto[12:]}"


def validar_cpf(valor: object) -> str:
    d = so_digitos(valor)
    if len(d) != 11 or d == d[0] * 11:
        raise ErroFormato("CPF deve ter 11 dígitos.")
    if _dv_cpf(d[:9]) != d[9:]:
        raise ErroFormato("CPF inválido: dígito verificador não confere.")
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def validar_documento_favorecido(valor: object) -> str:
    """CNPJ ou CPF do favorecido (a NE também admite pessoa física)."""
    s = _s(valor)
    n = len(re.sub(r"[.\-/\s]", "", s))
    return validar_cpf(s) if n == 11 else validar_cnpj(s)


# ----------------------------------------------------------------------------- SIAFI
def validar_ne(valor: object, ano_padrao: int | None = None) -> str:
    """Número de empenho → 'AAAANE999999' (aceita '2026NE165', '2026 NE 000165')."""
    s = _s(valor).upper().replace(" ", "")
    m = re.fullmatch(r"(?:(\d{4}))?NE(\d{1,6})", s)
    if not m:
        raise ErroFormato("Nº do empenho deve ser AAAANE999999 (ex.: 2026NE000165).")
    ano = m.group(1) or str(ano_padrao or date.today().year)
    num = int(m.group(2))
    if num == 0:
        raise ErroFormato("Nº do empenho não pode ser zero.")
    return f"{ano}NE{num:06d}"


def validar_nc(valor: object, ano_padrao: int | None = None) -> str:
    """Número de NC → 'AAAANC999999'. Aceita também a chave longa UG(6)+gestão(5)+AAAANC999999."""
    s = _s(valor).upper().replace(" ", "")
    m = re.fullmatch(r"(?:\d{11})?(?:(\d{4}))?NC(\d{1,6})", s)
    if not m:
        raise ErroFormato("Nº da NC deve ser AAAANC999999 (ex.: 2026NC415767).")
    ano = m.group(1) or str(ano_padrao or date.today().year)
    num = int(m.group(2))
    if num == 0:
        raise ErroFormato("Nº da NC não pode ser zero.")
    return f"{ano}NC{num:06d}"


def validar_uasg(valor: object) -> str:
    d = _s(valor)
    if not re.fullmatch(r"\d{6}", d):
        raise ErroFormato("UASG deve ter exatamente 6 dígitos (ex.: 160329).")
    return d


def validar_nd(valor: object) -> str:
    """Natureza da despesa de 6 dígitos (categoria 3 ou 4): 339030."""
    d = so_digitos(valor)
    if not re.fullmatch(r"[34]\d{5}", d):
        raise ErroFormato("ND deve ter 6 dígitos, iniciando por 3 ou 4 (ex.: 339030).")
    return d


def validar_subitem(valor: object) -> str:
    d = so_digitos(valor)
    if not re.fullmatch(r"\d{1,2}", d) or int(d) == 0:
        raise ErroFormato("Subitem (SI) deve ter 2 dígitos, de 01 a 99.")
    return f"{int(d):02d}"


def validar_pi(valor: object) -> str:
    s = re.sub(r"\s+", " ", _s(valor).upper())
    if not re.fullmatch(r"[A-Z0-9]{6,11}|[A-Z0-9]{3,10} [A-Z0-9]{3,10}", s):
        raise ErroFormato("Plano Interno (PI) deve ter de 6 a 11 caracteres alfanuméricos (ex.: E5MMPDRDEGE).")
    return s


def validar_ptres(valor: object) -> str:
    s = _s(valor).upper()
    if not re.fullmatch(r"[0-9A-Z]{4,6}", s):
        raise ErroFormato("PTRES deve ter de 4 a 6 caracteres (ex.: 232144).")
    return s


def validar_ugr(valor: object) -> str:
    d = so_digitos(valor)
    if not re.fullmatch(r"\d{5,6}", d):
        raise ErroFormato("UGR deve ter 5 ou 6 dígitos (ex.: 160298).")
    return d


def validar_fonte(valor: object) -> str:
    s = _s(valor).upper()
    if not re.fullmatch(r"[0-9A-Z]{10}", s):
        raise ErroFormato("Fonte de recurso deve ter 10 caracteres (ex.: 1000000000).")
    return s


# ----------------------------------------------------------------------------- licitação / PCA
def validar_numero_licitacao(valor: object, modalidade: str, ano_atual: int | None = None) -> str:
    """Pregão: 9XXXX/AAAA (numeração do Compras.gov.br na Lei 14.133). Dispensa: N/AAAA."""
    s = _s(valor).replace(" ", "")
    m = re.fullmatch(r"(\d{1,5})/(\d{4})", s)
    if not m:
        raise ErroFormato("Número da licitação deve ser NNNNN/AAAA (ex.: 90007/2026).")
    num, ano = m.group(1), int(m.group(2))
    atual = ano_atual or date.today().year
    ano_min = 2018 if modalidade == "CONTRATO" else 2021
    if not (ano_min <= ano <= atual + 1):
        rotulo_ano = "contrato" if modalidade == "CONTRATO" else "licitação"
        raise ErroFormato(f"Ano da {rotulo_ano} ({ano}) fora do intervalo plausível ({ano_min} a {atual + 1}).")
    if modalidade == "PREGAO":
        if not re.fullmatch(r"9\d{4}", num):
            raise ErroFormato("Pregão eletrônico tem número no formato 9XXXX/AAAA (ex.: 90007/2026).")
    elif modalidade == "DISPENSA":
        if int(num) == 0:
            raise ErroFormato("Número da dispensa não pode ser zero.")
    elif modalidade == "INEXIGIBILIDADE":
        if int(num) == 0:
            raise ErroFormato("Número da inexigibilidade não pode ser zero.")
    elif modalidade == "CONTRATO":
        if int(num) == 0:
            raise ErroFormato("Número do contrato não pode ser zero.")
    else:
        if int(num) == 0:
            raise ErroFormato("Número da licitação não pode ser zero.")
    return f"{num}/{ano}"


def validar_pca_contratacao(valor: object) -> str:
    s = _s(valor).replace(" ", "")
    if not re.fullmatch(r"\d{6}-\d{1,4}/\d{4}", s):
        raise ErroFormato("Nº da contratação no PCA deve ser UASG-N/AAAA (ex.: 160329-7/2025).")
    return s


def validar_pca_classe(valor: object) -> str:
    s = re.sub(r"\s+", " ", _s(valor))
    # classe de material (CATMAT) tem 4 dígitos; grupo/classe de serviço (CATSER) tem 3 ou 4 (ex.: 873 - SERVIÇOS DE INSTALAÇÃO)
    if not re.fullmatch(r"\d{3,4}(?: ?- ?[^\n]{3,80})?", s):
        raise ErroFormato("Classe/Grupo do PCA deve começar pelo código de 3 ou 4 dígitos (ex.: 7510 - ARTIGOS PARA ESCRITÓRIO).")
    return re.sub(r"\s*-\s*", " - ", s, count=1)


def validar_contrato(valor: object) -> str:
    s = _s(valor).replace(" ", "")
    if not re.fullmatch(r"\d{1,4}/\d{4}", s):
        raise ErroFormato("Nº do contrato deve ser N/AAAA (ex.: 16/2024).")
    return s


# ----------------------------------------------------------------------------- pessoas / textos
def validar_nome_militar(valor: object) -> str:
    s = re.sub(r"\s+", " ", _s(valor))
    if not re.fullmatch(rf"[{LETRAS}][{LETRAS}'.\- ]{{3,58}}[{LETRAS}.]", s) or " " not in s:
        raise ErroFormato("Informe o nome completo (somente letras, 2 ou mais palavras).")
    return s.upper()


def validar_texto_curto(valor: object, rotulo: str, minimo: int = 0, maximo: int = 200) -> str:
    """Texto livre estritamente limitado: sem quebras de linha, sem caracteres de controle."""
    s = re.sub(r"\s+", " ", _s(valor))
    if any(unicodedata.category(c).startswith("C") for c in s):
        raise ErroFormato(f"{rotulo} contém caracteres não permitidos.")
    if len(s) < minimo:
        raise ErroFormato(f"{rotulo}: mínimo de {minimo} caracteres.")
    if len(s) > maximo:
        raise ErroFormato(f"{rotulo}: máximo de {maximo} caracteres.")
    return s


def validar_data_iso(valor: object, rotulo: str = "Data") -> date:
    s = _s(valor)
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise ErroFormato(f"{rotulo} inválida (use AAAA-MM-DD).") from e


def mascarar_cpf(valor: object) -> str:
    """CPF no estilo do SIAFI (***.999.999-**); qualquer outro código (CNPJ, UG) volta como veio."""
    d = so_digitos(valor)
    s = _s(valor)
    if len(d) == 11 and "/" not in s:
        return f"***.{d[3:6]}.{d[6:9]}-**"
    return s
