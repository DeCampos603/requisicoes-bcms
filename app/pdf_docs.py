# -*- coding: utf-8 -*-
"""Geração do PDF (ReportLab) no padrão dos DIEx do BCMS, pronto para impressão.

Layout reproduzido dos modelos oficiais (DOCX) e de requisições reais impressas do processo:
  * folha com margem esquerda de 2,0 cm (encadernação) e 1,27 cm nas demais; Times New Roman 12;
  * brasão das Armas Nacionais e cabeçalho institucional centralizado em negrito;
  * DIEx/NUP à esquerda e data à direita; Do / Ao / Assunto / Anexos;
  * corpo justificado; tabelas de itens com a grade ITEM | SI | DESCRIÇÃO | UND | QTDE | VALOR UNIT | VALOR TOTAL;
  * assinatura do requisitante, "DESPACHO" e caixa de duas colunas (Fiscal Administrativo / Ordenador de Despesas)
    com data em branco para assinatura manuscrita;
  * a partir da 2ª folha, rodapé com o DIEx/NUP e "Página X de Y" (a 1ª folha do modelo não tem rodapé).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image as RLImage, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.pdfgen import canvas as rl_canvas

from . import config

_FONTES_WIN = Path("C:/Windows/Fonts")
_FAMILIAS = [
    ("TimesNewRoman", ["times.ttf", "Times New Roman.ttf"], ["timesbd.ttf", "Times New Roman Bold.ttf"],
     ["timesi.ttf", "Times New Roman Italic.ttf"], ["timesbi.ttf", "Times New Roman Bold Italic.ttf"]),
    ("LiberationSerif", ["LiberationSerif-Regular.ttf"], ["LiberationSerif-Bold.ttf"],
     ["LiberationSerif-Italic.ttf"], ["LiberationSerif-BoldItalic.ttf"]),
]
_fonte_cache: dict[str, tuple[str, str]] = {}


def _achar(pastas: list[Path], nomes: list[str]) -> Path | None:
    for pasta in pastas:
        for nome in nomes:
            p = pasta / nome
            if p.is_file():
                return p
    return None


def _registrar_fontes() -> tuple[str, str]:
    """Times New Roman (como nos modelos) se instalada; senão Liberation Serif; senão Times-Roman interna
    (as três têm a mesma métrica, então a quebra de linha não muda)."""
    if "f" in _fonte_cache:
        return _fonte_cache["f"]
    pastas = [_FONTES_WIN, Path("/usr/share/fonts/truetype/liberation"), Path("/usr/share/fonts/truetype/msttcorefonts"),
              Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
    for familia, reg, neg, ita, negita in _FAMILIAS:
        a, b, c, d = (_achar(pastas, x) for x in (reg, neg, ita, negita))
        if a and b:
            pdfmetrics.registerFont(TTFont(familia, str(a)))
            pdfmetrics.registerFont(TTFont(f"{familia}-Bold", str(b)))
            pdfmetrics.registerFont(TTFont(f"{familia}-Italic", str(c or a)))
            pdfmetrics.registerFont(TTFont(f"{familia}-BoldItalic", str(d or b)))
            pdfmetrics.registerFontFamily(familia, normal=familia, bold=f"{familia}-Bold", italic=f"{familia}-Italic",
                                          boldItalic=f"{familia}-BoldItalic")
            _fonte_cache["f"] = (familia, f"{familia}-Bold")
            return _fonte_cache["f"]
    pdfmetrics.registerFontFamily("Times-Roman", normal="Times-Roman", bold="Times-Bold", italic="Times-Italic",
                                  boldItalic="Times-BoldItalic")
    _fonte_cache["f"] = ("Times-Roman", "Times-Bold")
    return _fonte_cache["f"]


def _brasao_png() -> bytes | None:
    arq = config.ASSETS / "brasao_armas.png"
    if not arq.is_file():
        return None
    im = Image.open(arq).convert("RGBA")
    caixa = im.getchannel("A").getbbox()
    if caixa:
        im = im.crop(caixa)
    fundo = Image.new("RGB", im.size, (255, 255, 255))
    fundo.paste(im, mask=im.getchannel("A"))
    fundo.thumbnail((420, 420))
    buf = io.BytesIO()
    fundo.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


# ----------------------------------------------------------------------------- estilos
def _estilos(fonte: str) -> dict[str, ParagraphStyle]:
    base = dict(fontName=fonte, fontSize=12, leading=14.4, textColor=colors.black)
    return {
        "corpo": ParagraphStyle("corpo", alignment=TA_JUSTIFY, spaceAfter=5, **base),
        "centro": ParagraphStyle("centro", alignment=TA_CENTER, **base),
        "centro_neg": ParagraphStyle("centro_neg", alignment=TA_CENTER, **{**base, "fontName": f"{fonte}-Bold" if fonte != "Times-Roman" else "Times-Bold"}),
        "esq": ParagraphStyle("esq", alignment=TA_LEFT, **base),
        "dir": ParagraphStyle("dir", alignment=TA_RIGHT, **base),
        "cel": ParagraphStyle("cel", alignment=TA_CENTER, **{**base, "fontSize": 10.5, "leading": 12.2}),
        "cel_neg": ParagraphStyle("cel_neg", alignment=TA_CENTER, **{**base, "fontSize": 10.5, "leading": 12.2,
                                                                      "fontName": f"{fonte}-Bold" if fonte != "Times-Roman" else "Times-Bold"}),
        "cel_esq": ParagraphStyle("cel_esq", alignment=TA_LEFT, **{**base, "fontSize": 10.5, "leading": 12.2}),
        "cel_peq": ParagraphStyle("cel_peq", alignment=TA_CENTER, **{**base, "fontSize": 9, "leading": 10.5, "textColor": colors.HexColor("#333333")}),
        "cel_dir": ParagraphStyle("cel_dir", alignment=TA_RIGHT, **{**base, "fontSize": 10.5, "leading": 12.2}),
        "cel_neg_dir": ParagraphStyle("cel_neg_dir", alignment=TA_RIGHT, **{**base, "fontSize": 10.5, "leading": 12.2,
                                                                          "fontName": f"{fonte}-Bold" if fonte != "Times-Roman" else "Times-Bold"}),
        "desp": ParagraphStyle("desp", alignment=TA_JUSTIFY, **{**base, "fontSize": 10.5, "leading": 12.6, "spaceAfter": 3}),
        "desp_c": ParagraphStyle("desp_c", alignment=TA_CENTER, **{**base, "fontSize": 10.5, "leading": 12.6}),
        "cred_th": ParagraphStyle("cred_th", alignment=TA_CENTER, **{**base, "fontSize": 8.5, "leading": 10.5,
                                                                      "fontName": f"{fonte}-Bold" if fonte != "Times-Roman" else "Times-Bold",
                                                                      "textColor": colors.HexColor("#1A202C")}),
        "cred_td": ParagraphStyle("cred_td", alignment=TA_CENTER, **{**base, "fontSize": 9.5, "leading": 11.5,
                                                                      "textColor": colors.HexColor("#2D3748")}),
        "cred_td_neg": ParagraphStyle("cred_td_neg", alignment=TA_CENTER, **{**base, "fontSize": 9.5, "leading": 11.5,
                                                                              "fontName": f"{fonte}-Bold" if fonte != "Times-Roman" else "Times-Bold",
                                                                              "textColor": colors.HexColor("#1A202C")}),
        "cred_td_dir": ParagraphStyle("cred_td_dir", alignment=TA_RIGHT, **{**base, "fontSize": 9.5, "leading": 11.5,
                                                                             "fontName": f"{fonte}-Bold" if fonte != "Times-Roman" else "Times-Bold",
                                                                             "textColor": colors.HexColor("#1A202C")}),
        "cred_detalhe": ParagraphStyle("cred_detalhe", alignment=TA_LEFT, **{**base, "fontSize": 8.5, "leading": 11.0,
                                                                              "textColor": colors.HexColor("#4A5568")}),
    }


def _neg(fonte: str) -> str:
    return "Times-Bold" if fonte == "Times-Roman" else f"{fonte}-Bold"


# ----------------------------------------------------------------------------- rodapé numerado
class _CanvasNumerado(rl_canvas.Canvas):
    """Canvas de duas passagens para escrever 'Página X de Y' (Y só é conhecido ao final)."""

    rodape_texto = ""
    fonte = "Times-Roman"
    marca_dagua: str | None = None  # prévia: "RASCUNHO" em diagonal, para ninguém imprimir/assinar a prévia por engano
    modo_assinatura = "DIGITAL"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estados: list[dict] = []
        if getattr(self, "modo_assinatura", "DIGITAL") == "DIGITAL":
            try:
                self.setKeywords("DIEx, BCMS, Lei 14.133/2021, Assinatura Digital, Gov.br, SPED, ICP-Brasil")
            except Exception:
                pass

    def showPage(self):
        self._estados.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._estados)
        if getattr(self, "modo_assinatura", "DIGITAL") == "DIGITAL":
            try:
                self._doc.info.keywords = "DIEx, BCMS, Lei 14.133/2021, Assinatura Digital, Gov.br, SPED, ICP-Brasil"
            except Exception:
                pass
        for estado in self._estados:
            self.__dict__.update(estado)
            self._desenhar_rodape(total)
            super().showPage()
        super().save()

    def _desenhar_marca(self):
        if not self.marca_dagua:
            return
        largura, altura = self._pagesize
        self.saveState()
        self.setFillColor(colors.Color(0.78, 0.1, 0.1))
        self.setFillAlpha(0.13)
        self.setFont(_neg(self.fonte), 84)
        self.translate(largura / 2, altura / 2)
        self.rotate(52)
        self.drawCentredString(0, 0, self.marca_dagua)
        self.restoreState()

    def _desenhar_rodape(self, total: int):
        self._desenhar_marca()
        pagina = self._pageNumber
        largura, _ = self._pagesize
        esq = 2.0 * cm
        dire = largura - 1.27 * cm

        self.saveState()
        # Linha divisória fina e elegante de rodapé
        self.setStrokeColor(colors.HexColor("#777777"))
        self.setLineWidth(0.5)
        self.line(esq, 1.85 * cm, dire, 1.85 * cm)

        # Informações do DIEx e NUP à esquerda
        self.setFont(self.fonte, 9.5)
        self.setFillColor(colors.HexColor("#333333"))
        if self.rodape_texto:
            self.drawString(esq, 1.35 * cm, self.rodape_texto)

        # Ordem das páginas à direita
        self.setFont(_neg(self.fonte), 9.5)
        self.drawRightString(dire, 1.35 * cm, f"Página {pagina} de {total}")
        self.restoreState()


# ----------------------------------------------------------------------------- blocos
def _cabecalho(estilos: dict, fonte: str, compacto: bool = False) -> list:
    unid = config.unidade()
    fl: list = []
    png = _brasao_png()
    if png:
        im = Image.open(io.BytesIO(png))
        altura = (1.75 if compacto else 2.0) * cm
        largura = altura * im.width / im.height
        fl.append(RLImage(io.BytesIO(png), width=largura, height=altura, hAlign="CENTER"))
        fl.append(Spacer(1, 3))
    for linha in unid["cabecalho"]:
        fl.append(Paragraph(f"<b>{linha.replace(chr(32) * 2, '&nbsp;&nbsp;')}</b>", estilos["centro"]))
    fl.append(Spacer(1, 10 if compacto else 16))
    return fl


def _identificacao(doc: dict, estilos: dict) -> list:
    ident = doc["identificacao"]
    esq = [Paragraph(f"<b>{_x(ident['titulo'])}</b>", estilos["esq"]), Paragraph(f"<b>NUP:</b> <b>{_x(ident['nup'])}</b>", estilos["esq"])]
    data = Paragraph(_x(f"Rio de Janeiro-RJ, {ident['data']}"), estilos["dir"])
    t = Table([[esq, data]], colWidths=["58%", "42%"])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    compacto = doc.get("layout") == "novo"
    fl = [t, Spacer(1, 10 if compacto else 14)]
    fl.append(Paragraph(f"<b>Do</b> {_x(ident['do'])}", estilos["esq"]))
    fl.append(Paragraph(f"<b>Ao</b> {_x(ident['ao'].removeprefix('Ao '))}", estilos["esq"]))
    fl.append(Paragraph(f"<b>Assunto:</b> {_x(ident['assunto'])}", estilos["esq"]))
    if doc.get("anexos"):
        fl.append(Spacer(1, 6))
        fl.append(Paragraph("<b>Anexos:</b>", estilos["esq"]))
        for k, a in enumerate(doc["anexos"], 1):
            numero = f"{k}) " if len(doc["anexos"]) > 1 else ""
            fl.append(Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;{numero}{_x(a)}", estilos["esq"]))
    fl.append(Spacer(1, 8 if compacto else 12))
    return fl


def _x(txt: object) -> str:
    return str(txt).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("R$ ", "R$ ")


def _mk(markup: str) -> str:
    """Marcação já escapada (vinda de textos.py): só impede quebra de linha entre 'R$' e o valor."""
    return markup.replace("R$ ", "R$ ")


# Marcadores de campo pendente (prévia): [[PREENCHER: x]] vira texto com fundo amarelo. A troca passa por caracteres
# invisíveis (U+2063/U+2064) para não depender de um estado global — o servidor atende várias requisições ao mesmo tempo.
_RE_PENDENTE = re.compile(r"\[\[PREENCHER:[^\]]*\]\]")
_ABRE, _FECHA = "⁣", "⁤"
_x_base, _mk_base = _x, _mk


def _sentinelas(s: str) -> str:
    return s.replace(_ABRE, '<font backColor="#FFE680">').replace(_FECHA, "</font>")


def _x(txt: object) -> str:  # noqa: F811 - envolve a versão acima
    return _sentinelas(_x_base(txt))


def _mk(markup: str) -> str:  # noqa: F811
    return _sentinelas(_mk_base(markup))


def _preparar_marcas(obj, rascunho: bool):
    """Prévia: marca os pendentes. PDF final: remove qualquer caractere de marcação que tenha vindo digitado."""
    if isinstance(obj, str):
        limpo = obj.replace(_ABRE, "").replace(_FECHA, "")
        return _RE_PENDENTE.sub(lambda m: f"{_ABRE}{m.group(0)}{_FECHA}", limpo) if rascunho else limpo
    if isinstance(obj, dict):
        return {k: _preparar_marcas(v, rascunho) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_preparar_marcas(v, rascunho) for v in obj]
    return obj


GRADE = colors.HexColor("#333333")


def _tabela_itens(tab: dict, estilos: dict, largura: float, novo: bool = False) -> Table:
    p = lambda txt, est="cel": Paragraph(_x(txt), estilos[est])  # noqa: E731
    # layout novo: descrição à esquerda (texto longo lê-se melhor) e números à direita (conferem-se em coluna)
    est_desc, est_num, est_tot = ("cel_esq", "cel_dir", "cel_neg_dir") if novo else ("cel", "cel", "cel_neg")
    cab = [[p(tab["cabecalho"], "cel_neg")] + [""] * 6]
    cab.append([p(h, "cel_neg") for h in ("ITEM", "SI", "DESCRIÇÃO", "UND", "QTDE", "VALOR UNIT", "VALOR TOTAL")])
    corpo = [[p(l["item"]), p(l["si"]), p(l["descricao"], est_desc), p(l["und"]), p(l["qtde"], est_num), p(l["valor_unit"], est_num),
              p(l["total"], est_num)] for l in tab["linhas"]]
    total = [[p("TOTAL DO FORNECEDOR", "cel_neg")] + [""] * 5 + [p(tab["total"], est_tot)]]
    dados = cab + corpo + total
    pesos = [780, 560, 3300, 720, 1250, 1500, 1650]
    k = largura / sum(pesos)
    # splitInRow: uma descrição enorme (até 2500 caracteres, coluna estreita) pode ser mais alta que a página; sem isto o ReportLab aborta
    t = Table(dados, colWidths=[w * k for w in pesos], repeatRows=2, splitInRow=1)
    ult = len(dados) - 1
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.75, GRADE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("SPAN", (0, 0), (-1, 0)), ("SPAN", (0, ult), (5, ult)),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _tabela_anulacao(tab: dict, estilos: dict, largura: float) -> Table:
    p = lambda txt, est="cel": Paragraph(_x(txt), estilos[est])  # noqa: E731
    dados = [[p(tab["cabecalho"], "cel_neg"), ""],
             [p("NOTA DE EMPENHO", "cel_neg"), p("VALOR A ANULAR", "cel_neg")]]
    for l in tab["linhas"]:
        dados.append([[Paragraph(f"<b>{_x(l['ne'])}</b>", estilos["cel"]), Paragraph(_x(l["detalhe"]), estilos["cel_peq"])], p(l["valor"], "cel")])
    dados.append([p("TOTAL DO FORNECEDOR", "cel_neg"), p(tab["total"], "cel_neg")])
    t = Table(dados, colWidths=[largura * 0.64, largura * 0.36], repeatRows=2, splitInRow=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.75, GRADE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("SPAN", (0, 0), (-1, 0)),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _tabela_credito(tab: dict, estilos: dict, largura: float) -> Table:
    p_th = lambda txt: Paragraph(_x(txt), estilos["cred_th"])
    p_td = lambda txt: Paragraph(_x(txt), estilos["cred_td"])
    p_neg = lambda txt: Paragraph(f"<b>{_x(txt)}</b>", estilos["cred_td_neg"])
    p_dir = lambda txt: Paragraph(f"<b>{_x(txt)}</b>", estilos["cred_td_dir"])

    cab = [
        p_th("NOTA DE CRÉDITO"),
        p_th("UG EMIT"),
        p_th("UG EXEC"),
        p_th("ND"),
        p_th("PLANO INTERNO (PI)"),
        p_th("PTRES"),
        p_th("UGR"),
        p_th("VALOR SOLICITADO"),
    ]
    linha = [
        p_neg(tab.get("nc", "—")),
        p_td(tab.get("emitente", "—")),
        p_td(tab.get("ug", "—")),
        p_neg(tab.get("nd", "—")),
        p_td(tab.get("pi", "—")),
        p_td(tab.get("ptres", "—")),
        p_td(tab.get("ugr", "—")),
        p_dir(tab.get("valor", "—")),
    ]
    dados = [cab, linha]
    pesos = [78, 46, 46, 50, 96, 46, 46, 94]
    k = largura / sum(pesos)
    col_widths = [w * k for w in pesos]

    estilos_tab = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2F6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#718096")),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]

    detalhe = tab.get("detalhamento")
    if detalhe:
        p_det = Paragraph(f"<b>Detalhamento de Crédito:</b> {_mk(detalhe)}", estilos["cred_detalhe"])
        dados.append([p_det] + [""] * 7)
        estilos_tab.extend([
            ("SPAN", (0, 2), (-1, 2)),
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F8FAFC")),
            ("TOPPADDING", (0, 2), (-1, 2), 4),
            ("BOTTOMPADDING", (0, 2), (-1, 2), 4),
            ("LEFTPADDING", (0, 2), (-1, 2), 6),
            ("RIGHTPADDING", (0, 2), (-1, 2), 6),
        ])

    t = Table(dados, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle(estilos_tab))
    return t


def _quadro(doc: dict, estilos: dict, largura: float) -> list:
    """Itens 3 a 6 da requisição em duas colunas (layout novo)."""
    linhas = doc.get("quadro") or []
    if not linhas:
        return []
    dados, estilo = [], [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94A3B8")),
    ]
    for k, (esq, dir_) in enumerate(linhas):
        dados.append([Paragraph(_mk(esq), estilos["esq"]), Paragraph(_mk(dir_), estilos["esq"]) if dir_ else ""])
        if not dir_:
            estilo.append(("SPAN", (0, k), (1, k)))
    t = Table(dados, colWidths=[largura * 0.46, largura * 0.54], hAlign="LEFT")
    t.setStyle(TableStyle(estilo))
    return [Spacer(1, 4), t, Spacer(1, 10)]


def _total_geral(doc: dict, estilos: dict, largura: float) -> list:
    if not doc.get("total_geral"):
        return []
    if doc.get("layout") != "novo":
        return [Paragraph(f"<b>TOTAL GERAL: {_x(doc['total_geral'])}</b>", estilos["dir"])]
    t = Table([[Paragraph("<b>TOTAL GERAL</b>", estilos["cel_neg"]), Paragraph(f"<b>{_x(doc['total_geral'])}</b>", estilos["cel_neg_dir"])]],
              colWidths=[largura * 0.22, largura * 0.22], hAlign="RIGHT")
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.0, GRADE), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDEDED")),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return [t]


def _assinatura(doc: dict, estilos: dict, fonte: str) -> list:
    a = doc["assinatura"]
    folga = 1.15 * cm if doc.get("layout") == "novo" else 1.7 * cm
    itens = [Spacer(1, folga)]
    if doc.get("modo_assinatura") == "DIGITAL":
        itens.append(Paragraph("<font size=8 color='#555555'><i>[ Espaço para Assinatura Digital do Requisitante ]</i></font>", estilos["centro"]))
        itens.append(Spacer(1, 4))
    nome = (a.get("nome") or "").strip()
    funcao = (a.get("funcao") or "").strip()
    if nome and funcao and nome.upper() == funcao.upper():
        itens.append(Paragraph(f"<b>{_x(funcao or nome)}</b>", estilos["centro"]))
    else:
        if nome:
            itens.append(Paragraph(f"<b>{_x(nome)}</b>", estilos["centro"]))
        if funcao:
            itens.append(Paragraph(_x(funcao), estilos["centro"]))
    return itens


def _caixa_despacho(doc: dict, estilos: dict, largura: float) -> list:
    desp = doc["despacho"]
    meia = largura / 2
    interna = meia - 20
    novo = doc.get("layout") == "novo"
    altura_min = (4.9 if novo else 6.4) * cm
    digital = doc.get("modo_assinatura") == "DIGITAL"

    def partes(d: dict) -> tuple[list, list]:
        topo = [Paragraph(f"<u><b>{_x(d['titulo'])}</b></u>", estilos["desp_c"]), Spacer(1, 8)]
        topo += [Paragraph(_x(t), estilos["desp"]) for t in d["texto"]]
        pe = [Paragraph("Rio de Janeiro-RJ, ___ / ___ / ___.", estilos["desp_c"])]
        if digital:
            pe += [Spacer(1, 3), Paragraph("<font size=7.5 color='#555555'><i>[ Espaço para Assinatura Digital Gov.br / SPED ]</i></font>", estilos["desp_c"])]
        pe += [Spacer(1, (0.85 if novo else 1.2) * cm),
               Paragraph(f"<b>{_x(d['nome'])}</b>", estilos["desp_c"]), Paragraph(_x(d["funcao"]), estilos["desp_c"])]
        if digital:
            pe.append(Paragraph("<font size=6.5 color='#666666'>Assinatura eletrônica avançada (Lei nº 14.063/2020)</font>", estilos["desp_c"]))
        return topo, pe

    def altura(fl: list) -> float:
        """Altura que a lista ocupa na célula, com o espaço entre parágrafos (spaceAfter) que o ReportLab acrescenta."""
        h = sum(f.wrap(interna, 1000)[1] for f in fl)
        h += sum(max(a.getSpaceAfter(), b.getSpaceBefore()) for a, b in zip(fl, fl[1:]))
        return h + (fl[-1].getSpaceAfter() if fl else 0)

    # As duas assinaturas têm de ficar na mesma altura, qualquer que seja o tamanho do texto de cada despacho: o bloco
    # "data + assinatura" de ambas as colunas começa na mesma linha, logo abaixo do mais alto dos dois textos.
    colunas = [partes(desp["fiscal"]), partes(desp["od"])]
    h_topo_max = max(altura(topo) for topo, _ in colunas)
    h_pe_max = max(altura(pe) for _, pe in colunas)
    respiro = max(altura_min - 20 - h_topo_max - h_pe_max, 8)

    def coluna(topo: list, pe: list) -> list:
        return topo + [Spacer(1, h_topo_max - altura(topo) + respiro)] + pe

    t = Table([[coluna(*colunas[0]), coluna(*colunas[1])]], colWidths=[meia, meia])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("LINEAFTER", (0, 0), (0, 0), 0.5, colors.black),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 10),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    return [Spacer(1, 12 if novo else 18), KeepTogether([Paragraph("<u><b>DESPACHO</b></u>", estilos["centro"]), Spacer(1, 8), t])]


# ----------------------------------------------------------------------------- API
def gerar_pdf_documento(doc: dict, destino: Path | io.BytesIO, rascunho: bool = False) -> Path | io.BytesIO:
    """Gera o PDF do DIEx (sem anexos) em `destino` (arquivo ou memória).

    rascunho=True (prévia ao vivo): campos pendentes `[[PREENCHER: x]]` ganham fundo amarelo e cada folha leva a marca
    "RASCUNHO"; nunca é gravado em saida/ nem entra no histórico."""
    fonte, _ = _registrar_fontes()
    estilos = _estilos(fonte)
    doc = _preparar_marcas(doc, rascunho)
    novo = doc.get("layout") == "novo"
    papel = A4 if config.unidade().get("papel", "A4").upper() == "A4" else letter
    largura_pagina, altura_pagina = papel
    esq, dire, topo, base = 2.0 * cm, 1.27 * cm, 1.27 * cm, 2.4 * cm
    largura_util = largura_pagina - esq - dire

    if isinstance(destino, Path):
        destino.parent.mkdir(parents=True, exist_ok=True)

    class Canvas(_CanvasNumerado):
        rodape_texto = doc["rodape"]
        marca_dagua = "RASCUNHO" if rascunho else None
        modo_assinatura = doc.get("modo_assinatura", "DIGITAL")

    Canvas.fonte = fonte
    pdf = BaseDocTemplate(destino if isinstance(destino, io.BytesIO) else str(destino), pagesize=papel, leftMargin=esq, rightMargin=dire, topMargin=topo, bottomMargin=base,
                          title=doc["identificacao"]["titulo"], author=config.unidade()["nome_om"],
                          subject=doc["identificacao"]["assunto"], creator="Requisições de Empenho — BCMS")
    quadro = Frame(esq, base, largura_util, altura_pagina - topo - base, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    pdf.addPageTemplates([PageTemplate(id="p", frames=[quadro])])

    fl: list = []
    fl += _cabecalho(estilos, fonte, compacto=novo)
    fl += _identificacao(doc, estilos)
    tab_credito = doc.get("tabela_credito")
    for par in doc["paragrafos"]:
        fl.append(Paragraph(_mk(par), estilos["corpo"]))
        if tab_credito and ("Fonte de Recurso" in par or "crédito orçamentário" in par):
            fl.append(Spacer(1, 4))
            fl.append(_tabela_credito(tab_credito, estilos, largura_util))
            fl.append(Spacer(1, 8))
            tab_credito = None
    fl += _quadro(doc, estilos, largura_util)

    # Padronização: A tabela fica sempre na segunda página, com a assinatura do requisitante
    if doc.get("tabelas"):
        fl.append(PageBreak())
        for tab in doc["tabelas"]:
            fl.append(_tabela_anulacao(tab, estilos, largura_util) if doc["tipo"] == "ANULACAO" else _tabela_itens(tab, estilos, largura_util, novo))
            fl.append(Spacer(1, 6 if novo else 8))
        fl += _total_geral(doc, estilos, largura_util)
    else:
        fl.append(Spacer(1, 6 if novo else 8))

    # Assinatura do requisitante acompanhando a tabela
    fl.append(KeepTogether(_assinatura(doc, estilos, fonte)))

    # Padronização: O despacho das autoridades (Fiscal e OD) fica na próxima página
    if doc.get("despacho"):
        fl.append(PageBreak())
        fl += _caixa_despacho(doc, estilos, largura_util)
    pdf.build(fl, canvasmaker=Canvas)
    return destino
