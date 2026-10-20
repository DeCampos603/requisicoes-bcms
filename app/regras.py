# -*- coding: utf-8 -*-
"""Motor de regras: valida o formulário e cruza com as bases do repositório.

Três níveis de achado:
  ERRO     impede gerar o PDF (formato inválido, incoerência com a base, documento obrigatório ausente);
  ALERTA   permite gerar apenas se o usuário registrar ciência (fica no histórico);
  INFO     informação de contexto.

A verdade é sempre este módulo (o navegador só aplica máscara). Nada aqui inventa dado: o que a
base não traz vira ALERTA/INFO de "não conferido", nunca "conferido".
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from . import anexos, base, classificacao, config
from . import pca as modulo_pca
from .dinheiro import ZERO, ValorInvalido, brl, fmt_vu, parse_decimal, q2, total_item
from .validadores import (ErroFormato, mascarar_cpf, validar_contrato, validar_diex, validar_documento_favorecido,
                          validar_data_iso, validar_ne, validar_nc, validar_nd, validar_nome_militar,
                          validar_numero_licitacao, validar_nup, validar_pca_classe,
                          validar_pca_contratacao, validar_subitem, validar_texto_curto, validar_uasg)

PRAZO_ADESAO_DIAS = 90  # Decreto 11.462/2023, art. 31, § 2º
SALDO_DEFASADO_DIAS = 3
UGS_BCMS = ("160329", "167329")
MESES = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}


# ----------------------------------------------------------------------------- tipos
@dataclass
class Achado:
    nivel: str
    codigo: str
    campo: str
    mensagem: str
    base_legal: str | None = None
    acao: str | None = None  # "upload_nc" | "upload_ne" — a interface mostra o envio de arquivo

    @property
    def chave(self) -> str:
        """Identifica o alerta E o seu conteúdo: se os números da mensagem mudam (ex.: valor × saldo), a ciência anterior deixa de valer."""
        return f"{self.codigo}|{self.campo}|{hashlib.sha1(self.mensagem.encode('utf-8'), usedforsecurity=False).hexdigest()[:8]}"

    def to_dict(self) -> dict:
        return {"nivel": self.nivel, "codigo": self.codigo, "campo": self.campo, "mensagem": self.mensagem,
                "base_legal": self.base_legal, "acao": self.acao, "chave": self.chave}


@dataclass
class Avaliacao:
    tipo: str
    achados: list[Achado] = field(default_factory=list)
    norm: dict[str, Any] = field(default_factory=dict)
    ciente: set[str] = field(default_factory=set)

    def erro(self, codigo: str, campo: str, msg: str, base_legal: str | None = None, acao: str | None = None):
        self.achados.append(Achado("ERRO", codigo, campo, msg, base_legal, acao))

    def alerta(self, codigo: str, campo: str, msg: str, base_legal: str | None = None):
        self.achados.append(Achado("ALERTA", codigo, campo, msg, base_legal))

    def info(self, codigo: str, campo: str, msg: str, base_legal: str | None = None):
        self.achados.append(Achado("INFO", codigo, campo, msg, base_legal))

    @property
    def erros(self) -> list[Achado]:
        return [a for a in self.achados if a.nivel == "ERRO"]

    @property
    def alertas(self) -> list[Achado]:
        return [a for a in self.achados if a.nivel == "ALERTA"]

    @property
    def alertas_sem_ciencia(self) -> list[Achado]:
        return [a for a in self.alertas if a.chave not in self.ciente]

    @property
    def pode_gerar(self) -> bool:
        return not self.erros and not self.alertas_sem_ciencia

    def to_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "achados": [a.to_dict() for a in self.achados],
            "n_erros": len(self.erros), "n_alertas": len(self.alertas),
            "n_alertas_pendentes": len(self.alertas_sem_ciencia),
            "pode_gerar": self.pode_gerar,
            "resumo": self.norm.get("resumo", {}),
        }


def _try(av: Avaliacao, campo: str, fn, *args, codigo: str = "FORMATO", **kw):
    """Executa um validador de formato; em caso de erro registra ERRO no campo e devolve None."""
    try:
        return fn(*args, **kw)
    except ErroFormato as e:
        av.erro(codigo, campo, str(e))
    return None


def _dec(av: Avaliacao, campo: str, valor, rotulo: str, positivo: bool = True) -> Decimal | None:
    try:
        v = parse_decimal(valor)
    except ValorInvalido:
        av.erro("VALOR_INVALIDO", campo, f"{rotulo}: informe um número (ex.: 1.234,56).")
        return None
    if positivo and v <= 0:
        av.erro("VALOR_NAO_POSITIVO", campo, f"{rotulo} deve ser maior que zero.")
        return None
    return v


def _bool(v: object, padrao: bool = False) -> bool:
    """Booleano vindo do JSON: o texto "false"/"0"/"nao" é falso (bool("false") seria verdadeiro, e a checagem de ciência/aceite passaria)."""
    if v is None or v == "":
        return padrao
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "sim", "s", "yes", "on")
    return bool(v)


def _centavos(av: Avaliacao, campo: str, v: Decimal | None, rotulo: str) -> Decimal | None:
    """Valor em reais com no máximo 2 casas: o SIAFI não aceita fração de centavo (arredondar em silêncio mudaria o valor anulado)."""
    if v is not None and v.as_tuple().exponent < -2:
        av.erro("VALOR_CENTAVOS", campo, f"{rotulo}: use no máximo 2 casas decimais (centavos).")
        return None
    return v


def _sem_acento(txt: str) -> str:
    return unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode("ascii")


def _cat(chave: str, id_: str | None, campo: str = "id") -> dict | None:
    for e in config.catalogos().get(chave, []):
        if e.get(campo) == id_:
            return e
    return None


def _data_br_iso(txt: str | None) -> date | None:
    if not txt:
        return None
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", txt)
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _fmt_data(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _iso_para_data(txt: str | None) -> date | None:
    try:
        return date.fromisoformat(txt) if txt else None
    except ValueError:
        return None


# ----------------------------------------------------------------------------- cabeçalho comum
def _avaliar_cabecalho(av: Avaliacao, d: dict, hoje: date) -> dict | None:
    c = d.get("cabecalho") or {}
    diex = _try(av, "cabecalho.diex", validar_diex, c.get("diex"))
    nup = _try(av, "cabecalho.nup", validar_nup, c.get("nup"), hoje.year)
    data = _try(av, "cabecalho.data", validar_data_iso, c.get("data"), "Data do documento")
    if data:
        if data > hoje:
            av.erro("DATA_FUTURA", "cabecalho.data", "A data do documento não pode ser futura.")
        elif data.year < 2021:
            av.erro("DATA_ANTIGA", "cabecalho.data", "Data anterior à vigência da Lei 14.133/2021.")
        elif (hoje - data).days > 30:
            av.alerta("DATA_RETROATIVA", "cabecalho.data", f"Documento datado de {_fmt_data(data)}, há mais de 30 dias. Confirme a data.")
        if nup and int(nup[13:17]) != data.year:
            av.alerta("NUP_ANO_DIFERENTE", "cabecalho.nup",
                      f"O NUP é de {nup[13:17]} e o documento é de {data.year}. Confirme que o processo é o mesmo.")
    rq = c.get("requisitante") or {}
    posto = rq.get("posto")
    if posto not in config.catalogos()["postos"]:
        av.erro("POSTO_INVALIDO", "cabecalho.requisitante.posto", "Selecione o posto/graduação na lista.")
    nome = _try(av, "cabecalho.requisitante.nome", validar_nome_militar, rq.get("nome"))
    funcao = _try(av, "cabecalho.requisitante.funcao", validar_texto_curto, rq.get("funcao"), "Função", 5, 80)
    if not (diex and nup and data and nome and funcao and posto in config.catalogos()["postos"]):
        return None
    # DIEx já gerado antes?
    from . import historico
    anterior = historico.buscar_duplicado(av.tipo, diex, nup)
    if anterior:
        av.alerta("DIEX_JA_GERADO", "cabecalho.diex",
                  f"Já foi gerado um documento deste tipo com o DIEx {diex} / NUP {nup} em {anterior['criado_em']}.")
    outro = historico.buscar_diex_outro_nup(diex, nup, data.year)
    if outro:
        av.alerta("DIEX_OUTRO_NUP", "cabecalho.diex",
                  f"O DIEx {diex} já foi usado com outro NUP ({outro['nup']}) neste ano. Confira o número.")
    modo_ass = str(c.get("modo_assinatura") or "DIGITAL").upper().strip()
    if modo_ass not in ("DIGITAL", "FISICA"):
        modo_ass = "DIGITAL"
    return {"diex": diex, "nup": nup, "data": data, "modo_assinatura": modo_ass,
            "requisitante": {"posto": posto, "nome": nome, "funcao": funcao,
                             "respondendo": _bool(rq.get("respondendo"))}}


# ----------------------------------------------------------------------------- NC / fonte de recurso
def _norm_cod(v: object) -> str:
    """Código de dotação sem espaços e sem zeros à esquerda ("0160505" == "160505"), para comparar NE × NC."""
    return re.sub(r"^0+(?=\d)", "", str(v if v is not None else "").strip().upper())


_MES_RE = ("JAN(?:EIRO)?|FEV(?:EREIRO)?|MAR(?:CO)?|ABR(?:IL)?|MAI(?:O)?|JUN(?:HO)?|JUL(?:HO)?|AGO(?:STO)?|SET(?:EMBRO)?|OUT(?:UBRO)?"
           "|NOV(?:EMBRO)?|DEZ(?:EMBRO)?")
_SEP = r"[\s/.\-]*"
# "ATÉ", "ATÉ O DIA", "PRAZO", "PRAZO DE EMPENHO", "PRAZO PARA EMPENHAR" (+ ":" opcional); o texto já vem sem acento e em maiúsculas
_GATILHO = r"(?:\bATE?(?:\s+O\s+DIA|\s+DIA)?|\bPRAZO(?:\s+DE|\s+PARA)?(?:\s+EMPENHAR|\s+EMPENHO)?)\s*:?\s*"
_RE_PRAZO_EXTENSO = re.compile(rf"{_GATILHO}(\d{{1,2}}){_SEP}(?:DE{_SEP})?({_MES_RE})(?![A-Z]){_SEP}(?:DE{_SEP})?(\d{{4}}|\d{{2}})?(?!\d)")
_RE_PRAZO_NUMERICO = re.compile(rf"{_GATILHO}(\d{{1,2}})/(\d{{1,2}})(?:/(\d{{4}}|\d{{2}}))?(?![\d/])")
_RE_PRAZO_RELATIVO = re.compile(r"\bPRAZO[^.;]{0,30}?\b(\d{1,3})\s*(?:DIAS?)?[^.;\d]{0,25}?A CONTAR\s+(?:DESTA|DA)\s+DATA")


def _prazo_empenho_da_nc(descricao: str | None, emissao: date | None = None) -> date | None:
    """Prazo de empenho citado na descrição da NC.

    Formatos reais: "EMPENHO ATÉ 30 JUN 26", "EMPENHAR ATE 30JUN26", "PRAZO DE EMPENHO 27 FEV 26", "ATÉ 30 DE JUNHO DE 2026",
    "ATE 31/03/26", "EMPENHADOS ATÉ 25 SETEMBRO" (sem ano: vale o ano da emissão da NC) e "PRAZO PARA EMPENHO: 30 DIAS A CONTAR DESTA DATA"
    (emissão + 30 dias). "EMPENHO IMEDIATO" e afins não trazem data: devolve None. Havendo mais de uma data, vale a primeira do texto."""
    t = re.sub(r"\s+", " ", _sem_acento(descricao or "").upper())
    achados: list[tuple[int, date]] = []

    def montar(pos: int, dia: int, mes: int, ano_txt: str | None) -> None:
        if ano_txt:
            ano = int(ano_txt) + (2000 if len(ano_txt) == 2 else 0)
        elif emissao:
            ano = emissao.year
        else:
            return
        try:
            d = date(ano, mes, dia)
        except ValueError:
            return
        if not ano_txt and emissao and d < emissao - timedelta(days=30):  # "31/01" numa NC de dezembro é do ano seguinte
            d = d.replace(year=d.year + 1)
        achados.append((pos, d))

    for m in _RE_PRAZO_EXTENSO.finditer(t):
        montar(m.start(), int(m.group(1)), MESES[m.group(2)[:3]], m.group(3))
    for m in _RE_PRAZO_NUMERICO.finditer(t):
        montar(m.start(), int(m.group(1)), int(m.group(2)), m.group(3))
    m = _RE_PRAZO_RELATIVO.search(t)
    if m and emissao:
        achados.append((m.start(), emissao + timedelta(days=int(m.group(1)))))
    return min(achados, key=lambda x: x[0])[1] if achados else None


def _avaliar_nc(av: Avaliacao, fonte: dict, total: Decimal | None, objeto: str | None, hoje: date,
                campo: str = "fonte", celula_esperada: dict | None = None, ug_padrao: str | None = None,
                descricoes: list[str] | None = None) -> dict | None:
    """Localiza a NC no repositório, escolhe a célula de destino do BCMS e confere saldo/prazo/ND.

    NC com ND 00 (ex.: 339000, sem elemento de despesa) não pode ser empenhada como está: o crédito
    precisa ser detalhado para uma ND de destino. Esse caminho é tratado aqui (ND de destino, NC de
    detalhamento existente no dashboard de crédito, ciência quando o detalhamento ainda está pendente).
    """
    chave = _try(av, f"{campo}.nc", validar_nc, fonte.get("nc"), hoje.year)
    if not chave:
        return None
    cands = base.candidatas_nc(chave)
    if not cands:
        av.erro("NC_INEXISTENTE", f"{campo}.nc",
                f"A NC {chave} não consta no repositório de Notas de Crédito nem no dashboard de crédito. "
                f"Envie o PDF da NC (Impressão do SIAFI) para continuar.", acao="upload_nc")
        return None
    emit = fonte.get("emitente")
    if len(cands) > 1 and emit not in {c["emitente"] for c in cands}:
        lista = "; ".join(f"UG {c['emitente']} ({c['nome_emitente'] or 'sem nome'})" for c in cands)
        av.erro("NC_AMBIGUA", f"{campo}.emitente", f"Há mais de uma NC {chave}: {lista}. Informe a UG emitente.")
        return {"candidatas": cands}
    cand = next((c for c in cands if c["emitente"] == emit), None) or cands[0]
    emit = cand["emitente"]
    if not cand["arquivo_no_repositorio"]:
        av.erro("NC_SEM_ANEXO", f"{campo}.nc",
                f"A NC {chave} (UG {emit}) consta no dashboard de crédito, mas o PDF não está no repositório. "
                f"Envie o PDF da NC antes de gerar o documento.", acao="upload_nc")
        return None
    nc = base.nc_pdf(emit, chave)
    if (nc.get("status") or "") != "Contabilizada":
        av.erro("NC_NAO_CONTABILIZADA", f"{campo}.nc", f"A NC {chave} está com situação '{nc.get('status')}': só NC contabilizada fornece crédito.")
        return None
    if (nc.get("tipo_nc") or "").startswith("Anulação"):
        av.erro("NC_DE_ANULACAO", f"{campo}.nc", f"A NC {chave} é de anulação de descentralização: reduz crédito, não o fornece.")
        return None

    ugs_nc = sorted({i["ug_favorecida"] for i in nc["itens"] if i["ug_favorecida"] in UGS_BCMS})
    ug = fonte.get("ug") or ug_padrao
    if ug not in UGS_BCMS:
        if len(ugs_nc) == 1:
            ug = ugs_nc[0]
        else:
            av.erro("UG_FONTE_OBRIGATORIA", f"{campo}.ug", "Escolha a UG executora do crédito (160329 ou 167329).")
            return None
    itens_ug = [i for i in nc["itens"] if i["ug_favorecida"] == ug]
    if not itens_ug:
        av.erro("NC_NAO_FAVORECE_UG", f"{campo}.ug", f"A NC {chave} não tem crédito para a UG {ug}.")
        return None
    celulas = [(i["seq"], c) for i in itens_ug for c in i["destino"]]
    if not celulas:
        av.erro("NC_SEM_CELULA", f"{campo}.nc", "Não foi possível ler a célula orçamentária de destino da NC.")
        return None
    idx: int | None
    try:  # célula escolhida pelo usuário; vazio = ainda não escolheu
        idx = int(fonte.get("celula")) if fonte.get("celula") not in (None, "") else None
    except (TypeError, ValueError):
        idx = None
    if celula_esperada:  # reforço: o crédito tem de estar na mesma célula do empenho (ou detalhável para ela)
        pi_esp = (celula_esperada.get("pi") or "").strip().upper()
        nd_esp = celula_esperada.get("nd") or ""

        def mesma_dotacao(c: dict) -> bool:  # PI, PTRES e fonte; a UGR pode divergir legitimamente entre NE e NC (ver info abaixo)
            if (c["pi"] or "").strip().upper() != pi_esp:
                return False
            return all(_norm_cod(c.get(k)) == _norm_cod(celula_esperada.get(k)) for k in ("ptres", "fonte") if _norm_cod(celula_esperada.get(k)))

        casadas = [k for k, (_, c) in enumerate(celulas) if c["nd"] == nd_esp and mesma_dotacao(c)]
        if not casadas:
            casadas = [k for k, (_, c) in enumerate(celulas)
                       if classificacao.nd_indetalhada(c["nd"]) and c["nd"][:4] == nd_esp[:4] and mesma_dotacao(c)]
        if not casadas:
            esp = ", ".join(f"{r} {celula_esperada[k]}" for r, k in (("ND", "nd"), ("PI", "pi"), ("PTRES", "ptres"), ("fonte", "fonte")) if celula_esperada.get(k))
            av.erro("NC_CELULA_DIFERENTE", f"{campo}.nc",
                    f"O crédito da NC {chave} não está na célula do empenho ({esp}). "
                    f"O reforço só pode usar crédito da mesma célula orçamentária.",
                    "Lei 4.320/1964, art. 59; Decreto 93.872/1986, art. 26")
            return None
        if idx is None or idx not in casadas:
            idx = casadas[0]
        ugr_esp = _norm_cod(celula_esperada.get("ugr"))
        ugr_nc = _norm_cod(celulas[idx][1].get("ugr"))
        if ugr_esp and ugr_nc and ugr_esp != ugr_nc:
            av.info("NC_UGR_DIFERENTE_NE", f"{campo}.nc",
                    f"A UGR da NE ({celula_esperada['ugr']}) é diferente da UGR da NC ({celulas[idx][1].get('ugr')}); PI, PTRES e fonte conferem.")
    if len(celulas) > 1 and (idx is None or not 0 <= idx < len(celulas)):
        # nunca assume a primeira: ND/PI/PTRES do empenho saem dessa célula
        av.erro("CELULA_OBRIGATORIA", f"{campo}.celula", "A NC tem mais de uma célula de destino: escolha qual será usada.")
        return {"escolher_celula": True, "ug": ug, "nc": chave, "emitente": emit, "nome_emitente": cand["nome_emitente"],
                "celulas": [dict(c, seq=s) for s, c in celulas]}
    idx = idx if idx is not None and 0 <= idx < len(celulas) else 0
    seq, cel = celulas[idx]
    out = {
        "ug": ug, "nc": chave, "emitente": emit, "nome_emitente": cand["nome_emitente"],
        "nd": cel["nd"], "nd_origem": cel["nd"], "pi": cel["pi"], "ptres": cel["ptres"], "ugr": cel["ugr"],
        "fonte_rec": cel["fonte"], "esfera": cel["esfera"], "valor_celula": Decimal(cel["valor"]),
        "descricao_nc": nc.get("descricao"), "data_emissao_nc": nc.get("data_emissao"), "tipo_nc": nc.get("tipo_nc"),
        "arquivo": nc.get("caminho"), "celulas": [dict(c, seq=s) for s, c in celulas], "celula_idx": idx,
        "indetalhada": classificacao.nd_indetalhada(cel["nd"]), "detalhamento": None,
    }

    # --- ND 00: detalhamento para uma ND de destino ---------------------------------------------
    if out["indetalhada"]:
        sug = classificacao.nd_de_destino_sugerida(descricoes or [], cel["nd"], objeto) or {"nd": None, "candidatas": []}
        out["nds_candidatas"] = sug["candidatas"]
        out["nd_destino_sugerida"] = sug["nd"]
        destino = fonte.get("nd_destino") or (celula_esperada or {}).get("nd")
        if not destino:
            dica = f" Sugestão pelos itens: {sug['nd']}." if sug["nd"] else ""
            av.erro("ND_DESTINO_OBRIGATORIA", f"{campo}.nd_destino",
                    f"A NC está com ND {cel['nd']} (sem elemento de despesa): o crédito precisa ser detalhado para uma ND antes do empenho. "
                    f"Escolha a ND de destino do detalhamento.{dica}",
                    "Decreto 93.872/1986, art. 26 (empenho na dotação adequada)")
        else:
            destino = _try(av, f"{campo}.nd_destino", validar_nd, destino)
            if destino and (destino[:4] != cel["nd"][:4] or classificacao.nd_indetalhada(destino)):
                av.erro("ND_DESTINO_INVALIDA", f"{campo}.nd_destino",
                        f"A ND de destino deve ser da mesma categoria/grupo/modalidade da ND da NC ({cel['nd'][:4]}XX) e ter elemento de despesa.")
                destino = None
            if destino and out["nds_candidatas"] and destino not in {c["nd"] for c in out["nds_candidatas"]}:
                av.alerta("ND_DESTINO_FORA_DO_OBJETO", f"{campo}.nd_destino",
                          f"A ND {destino} não é a usual para o tipo de objeto informado. Confirme.")
            if destino:
                out["nd"] = destino
                nome_el = classificacao.nome_elemento(destino)
                det_ncs = base.detalhamentos(ug, cel["pi"], destino)
                escolhida = None
                if fonte.get("nc_detalhamento"):
                    ch_det = _try(av, f"{campo}.nc_detalhamento", validar_nc, fonte.get("nc_detalhamento"), hoje.year)
                    escolhida = next((d_ for d_ in det_ncs if d_["chave"] == ch_det), None)
                    if ch_det and not escolhida:
                        av.erro("NC_DETALHAMENTO_NAO_ACHADA", f"{campo}.nc_detalhamento",
                                f"A NC de detalhamento {ch_det} não consta no dashboard de crédito para o PI {cel['pi']} → ND {destino}.")
                out["detalhamento"] = {"nd_origem": cel["nd"], "nd_destino": destino, "nome_elemento": nome_el,
                                       "nc": escolhida["chave"] if escolhida else None, "candidatas": det_ncs}
                if escolhida:
                    av.info("DETALHAMENTO_VINCULADO", f"{campo}.nc_detalhamento",
                            f"Crédito já detalhado pela NC {escolhida['chave']} ({escolhida['dia']}): ND {cel['nd']} → {destino}.")
                else:
                    av.info("DETALHAMENTO_PROGRAMADO", f"{campo}.nd_destino",
                            f"Crédito na ND {cel['nd']} será detalhado no SIAFI para a ND {destino}"
                            f"{' – ' + nome_el if nome_el else ''} antes do empenho.")
                    if det_ncs:
                        av.info("DETALHAMENTOS_EXISTENTES", f"{campo}.nd_destino",
                                "NCs de detalhamento anteriores para o mesmo PI → ND " + destino + ": "
                                + "; ".join(f"{d_['chave']} ({d_['dia']}, residual {brl(Decimal(str(d_['residual'])))})" for d_ in det_ncs[:4]) + ".")
    # prazo de empenho citado na NC
    prazo = _prazo_empenho_da_nc(nc.get("descricao"), _data_br_iso(nc.get("data_emissao")))
    if prazo:
        out["prazo_empenho"] = prazo
        if prazo < hoje:
            av.alerta("NC_PRAZO_VENCIDO", f"{campo}.nc",
                      f"A descrição da NC indica prazo de empenho até {_fmt_data(prazo)}, já vencido. Confirme com o emitente da NC.")
        elif (prazo - hoje).days <= 10:
            av.info("NC_PRAZO_PROXIMO", f"{campo}.nc", f"Prazo de empenho da NC: {_fmt_data(prazo)} ({(prazo - hoje).days} dia(s)).")
    # ND x objeto (heurística conservadora, só os casos claros), sobre a ND efetiva
    nd_ef = out["nd"]
    if not out["indetalhada"] or out["detalhamento"]:
        if objeto == "MATERIAL" and nd_ef.startswith("33903") and nd_ef[5] == "9":
            av.alerta("ND_OBJETO", f"{campo}.nc", f"Objeto é material, mas a ND é {nd_ef} (serviços de terceiros – PJ).")
        if objeto == "SERVICO" and nd_ef in ("339030", "449052"):
            av.alerta("ND_OBJETO", f"{campo}.nc", f"Objeto é serviço, mas a ND é {nd_ef} (material).")
    # saldo no dashboard de crédito (célula de ORIGEM da NC; com detalhamento vinculado vale o residual dele)
    cr = base.nc_credito(emit, chave)
    posicao = base.posicao_credito()
    out["credito"] = None
    cred_base = None
    if out["detalhamento"] and out["detalhamento"]["nc"]:
        det = next(d_ for d_ in out["detalhamento"]["candidatas"] if d_["chave"] == out["detalhamento"]["nc"])
        cred_base = Decimal(str(det["residual"]))
        out["credito"] = {"disponivel": cred_base, "provisionado": Decimal(str(det["valor"])), "empenhado": ZERO,
                          "posicao": posicao, "origem": f"NC de detalhamento {det['chave']}"}
    elif cr and ug in cr["favorecidas"]:
        celulas_cr = cr["favorecidas"][ug]["celulas"]
        par = next((c for c in celulas_cr if c["nd"] == cel["nd"] and (c["pi"] or "") == (cel["pi"] or "")), None)
        if par:
            cred_base = Decimal(str(par["cred"]))
            out["credito"] = {"disponivel": cred_base, "provisionado": Decimal(str(par["prov"])), "empenhado": Decimal(str(par["emp"])),
                              "posicao": posicao, "origem": f"NC {chave}"}
    if cred_base is not None:
        idade = None
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", posicao or "")
        if m:
            idade = (hoje - date(int(m.group(3)), int(m.group(2)), int(m.group(1)))).days
        if total is not None and total > cred_base:
            av.alerta("SALDO_NC_INSUFICIENTE", f"{campo}.nc",
                      f"Valor solicitado ({brl(total)}) maior que o crédito disponível no dashboard ({brl(cred_base)}, posição {posicao}). "
                      f"Confirme o saldo atual no Tesouro Gerencial/SIAFI antes de seguir.",
                      "Lei 4.320/1964, art. 59; Decreto 93.872/1986, art. 26")
        elif idade is not None and idade > SALDO_DEFASADO_DIAS:
            av.info("SALDO_DEFASADO", f"{campo}.nc",
                    f"Crédito disponível no dashboard: {brl(cred_base)} (posição {posicao}, {idade} dias atrás). Confirme no TG.")
        else:
            av.info("SALDO_OK", f"{campo}.nc", f"Crédito disponível no dashboard: {brl(cred_base)} (posição {posicao}).")
    else:
        av.info("SALDO_NAO_CONFERIDO", f"{campo}.nc",
                "O saldo desta NC não consta no dashboard de crédito (NC recente?). Confirme o crédito disponível no Tesouro Gerencial.")
    return out


# ----------------------------------------------------------------------------- REQUISIÇÃO
def _avaliar_licitacao(av: Avaliacao, d: dict, hoje: date) -> dict | None:
    unid = config.unidade()
    propria = unid["uasg_propria"]
    modalidade = d.get("modalidade")
    if modalidade not in ("PREGAO", "DISPENSA", "INEXIGIBILIDADE", "CONTRATO"):
        av.erro("MODALIDADE", "modalidade", "Selecione a modalidade (Pregão, Dispensa, Inexigibilidade ou Contrato).")
        return None
    lic = d.get("licitacao") or {}
    out: dict = {"modalidade": modalidade, "origem": None}
    tipo = lic.get("tipo_unidade")
    if modalidade == "CONTRATO":
        numero = _try(av, "licitacao.numero", validar_numero_licitacao, lic.get("numero"), "CONTRATO", hoje.year)
        objeto_contrato = _try(av, "licitacao.objeto", validar_texto_curto, lic.get("objeto") or "Execução de contrato vigente", "Objeto do contrato", 3, 120)
        processo_origem = lic.get("processo_origem") or ""
        if tipo not in (None, "", "GERENCIADORA"):
            av.erro("CONTRATO_TIPO_UNIDADE", "licitacao.tipo_unidade",
                    "Contrato é celebrado e gerido pela própria UG: o tipo de unidade deve ser UG Gerenciadora.")
        out.update({
            "ugg": propria, "numero": numero, "tipo_unidade": "GERENCIADORA", "nome_om": unid["nome_om"],
            "sigla_om": unid["sigla_om"], "origem": "contrato",
            "objeto_contrato": objeto_contrato, "processo_origem": processo_origem,
            "fundamento": {"id": "ART89", "rotulo": "Contrato administrativo (Lei 14.133/2021, art. 89)", "frase": "art. 89 da Lei 14.133/2021"}
        })
        return out if numero else None

    if modalidade == "DISPENSA":
        numero = _try(av, "licitacao.numero", validar_numero_licitacao, lic.get("numero"), "DISPENSA", hoje.year)
        fund = _cat("fundamentos_dispensa", lic.get("fundamento_dispensa"))
        if not fund:
            av.erro("FUNDAMENTO_DISPENSA", "licitacao.fundamento_dispensa", "Selecione o fundamento da dispensa (art. 75 da Lei 14.133/2021).")
        detalhe = ""
        if lic.get("fundamento_dispensa") == "ART75_OUTRO":
            detalhe = _try(av, "licitacao.fundamento_detalhe", validar_texto_curto, lic.get("fundamento_detalhe"), "Inciso do art. 75", 3, 60) or ""
        if tipo not in (None, "", "GERENCIADORA"):
            av.erro("DISPENSA_TIPO_UNIDADE", "licitacao.tipo_unidade",
                    "Dispensa é conduzida pela própria UG: o tipo de unidade só pode ser UG Gerenciadora.")
        out.update({"ugg": propria, "numero": numero, "tipo_unidade": "GERENCIADORA", "nome_om": unid["nome_om"],
                    "sigla_om": unid["sigla_om"], "origem": "dispensa", "fundamento": fund, "fundamento_detalhe": detalhe})
        return out if numero and fund else None

    if modalidade == "INEXIGIBILIDADE":
        numero = _try(av, "licitacao.numero", validar_numero_licitacao, lic.get("numero"), "INEXIGIBILIDADE", hoje.year)
        fund = _cat("fundamentos_inexigibilidade", lic.get("fundamento_inexigibilidade"))
        if not fund:
            av.erro("FUNDAMENTO_INEXIGIBILIDADE", "licitacao.fundamento_inexigibilidade", "Selecione o fundamento da inexigibilidade (art. 74 da Lei 14.133/2021).")
        detalhe = ""
        if lic.get("fundamento_inexigibilidade") == "ART74_OUTRO":
            detalhe = _try(av, "licitacao.fundamento_detalhe", validar_texto_curto, lic.get("fundamento_detalhe"), "Inciso do art. 74", 3, 60) or ""
        if tipo not in (None, "", "GERENCIADORA"):
            av.erro("INEXIGIBILIDADE_TIPO_UNIDADE", "licitacao.tipo_unidade",
                    "Inexigibilidade é conduzida pela própria UG: o tipo de unidade só pode ser UG Gerenciadora.")
        out.update({"ugg": propria, "numero": numero, "tipo_unidade": "GERENCIADORA", "nome_om": unid["nome_om"],
                    "sigla_om": unid["sigla_om"], "origem": "inexigibilidade", "fundamento": fund, "fundamento_detalhe": detalhe})
        return out if numero and fund else None

    pid = lic.get("pregao_id")
    manual = lic.get("manual")
    if pid:
        p = base.pregao(pid)
        if not p:
            av.erro("PREGAO_NAO_ENCONTRADO", "licitacao.pregao_id", "Pregão não encontrado na base do dashboard. Atualize os dados ou cadastre o pregão.")
            return None
        out.update({"ugg": p["ugg"], "numero": p["numero"], "tipo_unidade": p["tipo_unidade"],
                    "nome_om": base.nome_uasg(p["ugg"]) or base.sigla_uasg(p["ugg"]) or f"UASG {p['ugg']}",
                    "sigla_om": base.sigla_uasg(p["ugg"]), "origem": "dashboard", "registro": p})
        if tipo and tipo != p["tipo_unidade"]:
            rotulo = _cat("tipos_unidade", p["tipo_unidade"])["rotulo"]
            av.erro("TIPO_UNIDADE_DIVERGE", "licitacao.tipo_unidade",
                    f"A base do dashboard indica que o BCMS é '{rotulo}' neste pregão. O tipo informado não confere.")
        status_ata = {i["status"] for i in p["itens"]}
        if status_ata == {"Vencida"}:
            av.erro("ATA_VENCIDA", "licitacao.pregao_id", "Todas as atas deste pregão constam como vencidas na base.")
        base_info = base.posicao_pregoes()
        out["posicao_base"] = base_info
        dias_pregao = base.calcular_dias_defasagem(base_info, hoje)
        if dias_pregao is not None and dias_pregao > 15:
            av.info("BASE_PREGOES_DEFASADA", "licitacao.pregao_id",
                    f"A base de pregões é de {base_info} ({dias_pregao} dias atrás). "
                    "Confirme no Compras.gov.br (Gestão de Atas) se não houve reajuste de valor, prorrogação ou nova ordem de fornecimento.")
        return out
    if manual:
        numero = _try(av, "licitacao.manual.numero", validar_numero_licitacao, manual.get("numero"), "PREGAO", hoje.year)
        uasg = _try(av, "licitacao.manual.uasg", validar_uasg, manual.get("uasg"))
        nome_om = _try(av, "licitacao.manual.nome_om", validar_texto_curto, manual.get("nome_om"), "Nome da OM gerenciadora", 3, 90)
        tipo_m = manual.get("tipo_unidade")
        if not _cat("tipos_unidade", tipo_m):
            av.erro("TIPO_UNIDADE", "licitacao.manual.tipo_unidade", "Selecione o tipo de unidade.")
        if numero and uasg:
            existente = base.pregao_por_numero(uasg, numero)
            if existente:
                av.erro("PREGAO_JA_NA_BASE", "licitacao.manual.numero",
                        f"O pregão {numero} da UASG {uasg} já consta na base do dashboard (BCMS como "
                        f"{_cat('tipos_unidade', existente[0]['tipo_unidade'])['rotulo']}). Selecione-o na lista em vez de cadastrar.")
        out.update({"ugg": uasg, "numero": numero, "tipo_unidade": tipo_m, "nome_om": nome_om, "sigla_om": None,
                    "origem": "manual"})
        av.alerta("PREGAO_FORA_DA_BASE", "licitacao.manual",
                  "Pregão fora da base do dashboard: preços, saldos e vigência da ata NÃO foram conferidos. Confira no Compras.gov.br (Gestão de Atas).")
        return out if (numero and uasg and nome_om and _cat("tipos_unidade", tipo_m)) else None
    av.erro("PREGAO_OBRIGATORIO", "licitacao.pregao_id", "Selecione o pregão na lista ou cadastre um pregão novo (número e UASG).")
    return None


def _regra_tipo_unidade(av: Avaliacao, lic: dict) -> None:
    propria = config.unidade()["uasg_propria"]
    if not lic or not lic.get("ugg") or not lic.get("tipo_unidade"):
        return
    ugg, tipo = lic["ugg"], lic["tipo_unidade"]
    if tipo == "GERENCIADORA" and ugg != propria:
        av.erro("GERENCIADORA_UASG_OUTRA", "licitacao",
                f"O BCMS (UASG {propria}) só é UG Gerenciadora de pregão da própria UASG; este pregão é da UASG {ugg}. "
                f"Se o BCMS aderiu à ata, o tipo correto é UG Participante ou Não Participante.",
                "Decreto 11.462/2023, art. 2º, III")
    if tipo != "GERENCIADORA" and ugg == propria:
        av.erro("PROPRIA_UASG_NAO_GERENCIADORA", "licitacao",
                f"Pregão da UASG {propria} (própria): o BCMS é UG Gerenciadora, não {_cat('tipos_unidade', tipo)['rotulo']}.")


def _avaliar_adesao(av: Avaliacao, d: dict, lic: dict, hoje: date, data_doc: date | None) -> dict | None:
    if not lic or lic.get("tipo_unidade") != "NAO_PARTICIPANTE":
        return None
    a = d.get("adesao") or {}
    aut = None
    if not a.get("autorizacao_data"):
        av.erro("ADESAO_AUTORIZACAO", "adesao.autorizacao_data",
                "Informe a data da autorização do órgão gerenciador (Compras.gov.br – Gestão de Atas).",
                "Lei 14.133/2021, art. 86, § 2º, III; Decreto 11.462/2023, art. 31, § 1º")
    else:
        aut = _try(av, "adesao.autorizacao_data", validar_data_iso, a.get("autorizacao_data"), "Data da autorização")
    if not _bool(a.get("aceite_fornecedor")):
        av.erro("ADESAO_ACEITE", "adesao.aceite_fornecedor",
                "Confirme que o fornecedor aceitou a adesão antes da autorização da gerenciadora.",
                "Decreto 11.462/2023, art. 31, § 1º")
    if not _bool(a.get("ata_federal")):
        av.erro("ADESAO_ATA_FEDERAL", "adesao.ata_federal",
                "Confirme que a ata é de órgão ou entidade gerenciadora federal (a Administração federal não adere a ata estadual ou municipal).",
                "Lei 14.133/2021, art. 86, § 8º; Decreto 11.462/2023, art. 33")
    if aut and data_doc:
        if aut > data_doc:
            av.erro("ADESAO_AUTORIZACAO_FUTURA", "adesao.autorizacao_data", "A autorização da gerenciadora é posterior à data do documento.")
        elif (data_doc - aut).days > PRAZO_ADESAO_DIAS:
            av.erro("ADESAO_PRAZO_90_DIAS", "adesao.autorizacao_data",
                    f"Passaram {(data_doc - aut).days} dias desde a autorização ({_fmt_data(aut)}); o prazo para contratar é de 90 dias. "
                    f"Só prorrogação aceita pela gerenciadora permite seguir.", "Decreto 11.462/2023, art. 31, §§ 2º e 3º")
        elif (data_doc - aut).days > PRAZO_ADESAO_DIAS - 15:
            av.alerta("ADESAO_PRAZO_PROXIMO", "adesao.autorizacao_data",
                      f"Restam {PRAZO_ADESAO_DIAS - (data_doc - aut).days} dia(s) do prazo de 90 dias contado da autorização.",
                      "Decreto 11.462/2023, art. 31, § 2º")
    return {"autorizacao_data": aut, "documentos": _avaliar_documentos_adesao(av, a)}


def _avaliar_documentos_adesao(av: Avaliacao, a: dict) -> list[dict]:
    """Instrução obrigatória da adesão: termo de justificativa, pesquisa de preços e aceite do fornecedor vão anexos à requisição.

    O front só devolve o `id` do arquivo enviado antes (/api/adesao/upload); o caminho nunca vem do usuário."""
    enviados = a.get("documentos") if isinstance(a.get("documentos"), dict) else {}
    saida: list[dict] = []
    for tp in config.catalogos()["documentos_adesao"]:
        campo = f"adesao.documentos.{tp['id']}"
        ref = enviados.get(tp["id"])
        id_ = ref.get("id") if isinstance(ref, dict) else ref
        if not id_:
            if tp["obrigatorio"]:
                av.erro("ADESAO_DOC_AUSENTE", campo, f"Anexe o documento obrigatório da adesão: {tp['rotulo']}. {tp['ajuda']}", tp["base"])
            continue
        caminho = anexos.caminho_documento_adesao(tp["id"], id_)
        if caminho is None:
            av.erro("ADESAO_DOC_ARQUIVO", campo, f"{tp['rotulo']}: o arquivo enviado não foi encontrado no servidor. Envie-o novamente.", tp["base"])
            continue
        saida.append({"tipo": tp["id"], "rotulo": tp["rotulo"], "anexo": tp["anexo"], "caminho": str(caminho)})
    return saida


def _avaliar_finalidade(av: Avaliacao, d: dict, objeto: str | None) -> dict | None:
    f = d.get("finalidade") or {}
    texto = None
    fid = f.get("id")
    if fid and fid != "OUTRA":
        cat = next((x for x in base.finalidades() if x["id"] == fid), None)
        if not cat:
            av.erro("FINALIDADE", "finalidade.id", "Finalidade inválida: escolha uma opção da lista.")
        else:
            texto = cat["texto"]
            if objeto and cat.get("objeto") and cat["objeto"] != objeto:
                av.alerta("FINALIDADE_OBJETO", "finalidade.id", "A finalidade escolhida é típica de outro tipo de objeto (material × serviço).")
    elif fid == "OUTRA":
        texto = _try(av, "finalidade.outra", validar_texto_curto, f.get("outra"), "Finalidade", 15, 160)
        if texto and not re.search(r"[a-zà-ÿ]", texto, re.I):
            av.erro("FINALIDADE", "finalidade.outra", "Descreva a finalidade em texto corrido (ex.: manutenção das viaturas…).")
    else:
        av.erro("FINALIDADE", "finalidade.id", "Selecione a finalidade da aquisição.")
    detalhe = _try(av, "finalidade.detalhe", validar_texto_curto, f.get("detalhe") or "", "Detalhamento", 0, 140) or ""
    return {"id": fid, "texto": texto.rstrip(". ") if texto else None, "detalhe": detalhe.rstrip(". ")} if texto else None


def _avaliar_contrato_pca(av: Avaliacao, d: dict, modalidade: str | None, data_doc: date | None) -> tuple[dict | None, dict | None]:
    c = d.get("contrato") or {}
    lic = d.get("licitacao") or {}
    if modalidade == "CONTRATO":
        num_c = c.get("numero") or lic.get("numero")
        num_c = _try(av, "contrato.numero", validar_contrato, num_c)
        out_c = {"possui": True, "numero": num_c} if num_c else None
    else:
        out_c = {"possui": _bool(c.get("possui"))}
        if out_c["possui"]:
            out_c["numero"] = _try(av, "contrato.numero", validar_contrato, c.get("numero"))
            if not out_c["numero"]:
                out_c = None
        else:
            hip = c.get("hipotese")
            if modalidade == "PREGAO":
                hip = "ATA_SRP"
            h = _cat("hipoteses_sem_contrato", hip)
            if not h or (modalidade in ("DISPENSA", "INEXIGIBILIDADE") and hip == "ATA_SRP"):
                av.erro("HIPOTESE_SEM_CONTRATO", "contrato.hipotese",
                        "Sem contrato, a Nota de Empenho substitui o instrumento: indique a hipótese do art. 95 da Lei 14.133/2021.",
                        "Lei 14.133/2021, art. 95")
                out_c = None
            else:
                out_c["hipotese"] = h
    p = d.get("pca") or {}
    out_p: dict | None = {"consta": _bool(p.get("consta"))}
    if out_p["consta"]:
        classe = _try(av, "pca.classe", validar_pca_classe, p.get("classe"))
        nr = _try(av, "pca.contratacao", validar_pca_contratacao, p.get("contratacao"))
        if classe and nr:
            ano_pca = int(nr.split("/")[1])
            if data_doc and not (data_doc.year - 1 <= ano_pca <= data_doc.year + 1):
                av.alerta("PCA_ANO", "pca.contratacao", f"O PCA informado é de {ano_pca}, e o documento é de {data_doc.year}. Confirme o exercício do plano.")
            out_p.update({"classe": classe, "contratacao": nr, "ano": ano_pca})
        else:
            out_p = None
    else:
        just = _try(av, "pca.justificativa", validar_texto_curto, p.get("justificativa"), "Justificativa", 15, 200)
        av.alerta("FORA_DO_PCA", "pca.consta",
                  "Contratação não prevista no Plano de Contratações Anual: registre a justificativa e a ciência.",
                  "Lei 14.133/2021, arts. 12, VII, e 18")
        out_p = {"consta": False, "justificativa": just} if just else None
    return out_c, out_p


def _checar_subitem(av: Avaliacao, pi: str, n_item, desc: str | None, si: str | None, fonte: dict | None,
                    objeto: str | None) -> dict | None:
    """Sugere o subitem pela ND da NC (ou pela ND de destino do detalhamento) e confere o SI informado."""
    if not desc or not fonte or not classificacao.disponivel():
        return None
    nd = fonte["nd"]
    if classificacao.nd_indetalhada(nd):  # ND de destino ainda não escolhida: nada a sugerir por elemento
        return None
    r = classificacao.verificar_subitem(desc, si or "", nd, objeto)
    if si:
        if r["si_existe"] is False:
            av.alerta("SI_FORA_DO_MANUAL", f"{pi}.si",
                      f"Item {n_item}: o subitem {si} não existe no Manual de Classificação da Despesa para a ND {nd}"
                      f" ({classificacao.nome_elemento(nd) or 'elemento'}).")
        elif r["si_diverge"]:
            rec = next((x for x in r["sugestoes"] if x["subitem"] == r["recomendada"]), None)
            av.alerta("SI_DIVERGE_SUGESTAO", f"{pi}.si",
                      f"Item {n_item}: o subitem sugerido para a ND {nd} é {r['recomendada']} – {rec['nome'] if rec else ''} "
                      f"({rec['confianca'] if rec else ''}); foi informado {si}.")
    if r["nd_diverge"]:
        g = r["global_"]
        av.alerta("ND_ITEM_DIVERGE_NC", f"{pi}.item",
                  f"Item {n_item}: pela descrição, o item é classificado na ND {g['nd']} (subitem {g.get('subitem')} – {g.get('nome')}), "
                  f"mas a NC está na ND {nd}. Verifique se é preciso outra NC ou o detalhamento/remanejamento de ND.")
    return {"sugestoes": r["sugestoes"], "recomendada": r["recomendada"], "si_existe": r["si_existe"],
            "global": r["global_"]}


def _avaliar_itens_requisicao(av: Avaliacao, d: dict, lic: dict | None, fonte: dict | None, hoje: date,
                              data_doc: date | None) -> list[dict] | None:
    fornecedores = d.get("fornecedores") or []
    if not fornecedores:
        av.erro("SEM_ITENS", "fornecedores", "Inclua ao menos um fornecedor com um item.")
        return None
    registro = (lic or {}).get("registro")
    catalogo: dict | None = None
    if registro:  # um item pode ter mais de um fornecedor/ata (cadastro de reserva): guarda todos e escolhe pelo CNPJ informado
        catalogo = {}
        for i in registro["itens"]:
            catalogo.setdefault(i["item"], []).append(i)

    def ref_da_ata(n: int | None, cnpj_: str | None) -> dict | None:
        lista = (catalogo or {}).get(n) or []
        alvo = re.sub(r"\W", "", cnpj_ or "").upper()
        return next((r for r in lista if alvo and re.sub(r"\W", "", r.get("fornecedor_cnpj") or "").upper() == alvo), lista[0] if lista else None)

    unidades = config.catalogos()["unidades_medida"]
    vistos_forn: set[str] = set()
    vistos_item: set[tuple[str, int]] = set()
    saida = []
    ok_geral = True
    atas: dict[str, str | None] = {}
    itens_cat: list[dict] = []  # CATMAT/CATSER dos itens (alimenta a sugestão de classe do PCA)
    for fi, f in enumerate(fornecedores):
        pf = f"fornecedores[{fi}]"
        cnpj = _try(av, f"{pf}.cnpj", validar_documento_favorecido, f.get("cnpj"))
        nome = _try(av, f"{pf}.nome", validar_texto_curto, f.get("nome"), "Razão social", 3, 120)
        if cnpj and cnpj in vistos_forn:
            av.erro("FORNECEDOR_REPETIDO", f"{pf}.cnpj", "Fornecedor repetido: agrupe todos os itens dele em um só bloco.")
        if cnpj:
            vistos_forn.add(cnpj)
        itens_in = f.get("itens") or []
        if not itens_in:
            av.erro("FORNECEDOR_SEM_ITENS", f"{pf}.itens", "Fornecedor sem itens.")
        itens = []
        for ii, it in enumerate(itens_in):
            pi = f"{pf}.itens[{ii}]"
            try:
                n_item = int(it.get("item"))
                if n_item < 1:
                    raise ValueError
            except (TypeError, ValueError):
                av.erro("ITEM_NUMERO", f"{pi}.item", "Nº do item inválido.")
                n_item = None
            si = _try(av, f"{pi}.si", validar_subitem, it.get("si"))
            desc = _try(av, f"{pi}.descricao", validar_texto_curto, it.get("descricao"), "Descrição", 5, 2500)
            und = it.get("und")
            if und not in unidades:
                av.erro("UND_INVALIDA", f"{pi}.und", "Selecione a unidade de fornecimento na lista.")
            qtde = _dec(av, f"{pi}.qtde", it.get("qtde"), "Quantidade")
            vu = _dec(av, f"{pi}.valor_unit", it.get("valor_unit"), "Valor unitário")
            if qtde is not None and (qtde.as_tuple().exponent < -6):
                av.erro("QTDE_CASAS", f"{pi}.qtde", "Quantidade com mais de 6 casas decimais.")
            if vu is not None and (vu.as_tuple().exponent < -4):
                av.erro("VU_CASAS", f"{pi}.valor_unit", "Valor unitário com mais de 4 casas decimais.")
            if n_item is not None and cnpj:
                if (cnpj, n_item) in vistos_item:
                    av.erro("ITEM_REPETIDO", f"{pi}.item", f"Item {n_item} repetido para este fornecedor.")
                vistos_item.add((cnpj, n_item))
            total = total_item(qtde, vu) if (qtde is not None and vu is not None) else None
            if total is not None and total <= 0:
                av.erro("ITEM_TOTAL_ZERO", f"{pi}.qtde", "O total do item ficou em R$ 0,00 (quantidade × valor unitário, arredondado em centavos): confira os valores.")
            # cruzamento com a ata (pregão da base)
            ref = ref_da_ata(n_item, cnpj) if (catalogo is not None and n_item is not None) else None
            if catalogo is not None and n_item is not None:
                if not ref:
                    av.erro("ITEM_FORA_DA_ATA", f"{pi}.item", f"O item {n_item} não existe neste pregão na base do dashboard.")
                else:
                    atas[ref.get("ata") or "sem nº"] = ref.get("vigencia_fim")
                    if cnpj and ref.get("fornecedor_cnpj"):
                        try:
                            if validar_documento_favorecido(ref["fornecedor_cnpj"]) != cnpj:
                                av.erro("FORNECEDOR_DIVERGE_ATA", f"{pf}.cnpj",
                                        f"O item {n_item} está registrado em ata para {ref.get('fornecedor_nome')} ({ref['fornecedor_cnpj']}), não para este fornecedor.")
                        except ErroFormato:
                            pass
                    ref_vu = Decimal(ref["valor_unitario"]) if ref.get("valor_unitario") not in (None, "None") else None
                    if vu is not None and ref_vu is not None:
                        if ref_vu == Decimal(1):
                            # ata por MAIOR DESCONTO: o item é registrado a R$ 1,00 (unidade de valor) e o preço do
                            # empenho é o da tabela menos o desconto da proposta (ex.: 0,9500 = 5% de desconto)
                            if vu > ref_vu:
                                av.erro("VU_ACIMA_ATA", f"{pi}.valor_unit",
                                        "Ata por maior desconto (item registrado a R$ 1,00): o valor unitário não pode ser maior que R$ 1,00.",
                                        "Lei 14.133/2021, art. 82, V")
                            elif vu < ref_vu:
                                av.info("DESCONTO_ATA", f"{pi}.valor_unit",
                                        f"Item {n_item}: ata por maior desconto — preço líquido de {fmt_vu(vu)} por unidade de valor "
                                        f"({(1 - vu) * 100:.2f}% de desconto). Confira com a proposta do fornecedor.")
                        elif ref_vu != vu:
                            av.erro("VU_DIVERGE_ATA", f"{pi}.valor_unit",
                                    f"Valor unitário diferente do registrado em ata ({fmt_vu(ref_vu)}).",
                                    "Lei 14.133/2021, art. 82 (preços registrados)")
                    if desc and ref.get("descricao") and re.sub(r"\s+", " ", ref["descricao"]).strip().upper() != re.sub(r"\s+", " ", desc).strip().upper():
                        av.alerta("DESCRICAO_DIVERGE_ATA", f"{pi}.descricao", f"Item {n_item}: a descrição foi alterada em relação à ata. Mantenha a descrição idêntica à da ata.")
                    saldo = Decimal(ref["qtd_saldo"]) if ref.get("qtd_saldo") not in (None, "None") else None
                    if saldo is not None and qtde is not None and qtde > saldo:
                        av.erro("QTDE_ACIMA_SALDO", f"{pi}.qtde", f"Item {n_item}: quantidade ({qtde}) maior que o saldo do BCMS na ata ({saldo}).",
                                "Decreto 11.462/2023, arts. 31 e 32")
                    if saldo is None:
                        av.info("SALDO_ATA_NAO_CONFERIDO", f"{pi}.qtde", f"Item {n_item}: saldo na ata não disponível na base.")
                    fim = _iso_para_data(ref.get("vigencia_fim"))
                    st = ref.get("status") or ""
                    if st.startswith("Sem ata"):
                        av.erro("SEM_ATA_FORMALIZADA", f"{pi}.item", f"Item {n_item}: fornecedor homologado, mas sem ata de registro de preços formalizada.")
                    elif st == "Vencida" or (fim and data_doc and fim < data_doc):
                        av.erro("ATA_VENCIDA", f"{pi}.item", f"Item {n_item}: ata vencida em {_fmt_data(fim)}. A contratação deve ocorrer na vigência da ata.",
                                "Decreto 11.462/2023, art. 34, parágrafo único")
                    elif fim and (fim - (data_doc or hoje)).days <= 30:
                        av.alerta("ATA_VENCE_EM_30_DIAS", f"{pi}.item", f"Item {n_item}: a ata vence em {_fmt_data(fim)}. O empenho deve ser emitido até essa data.",
                                  "Decreto 11.462/2023, art. 34, parágrafo único")
            if desc is None or und not in unidades or qtde is None or vu is None or si is None or n_item is None:
                ok_geral = False
            sugestao = _checar_subitem(av, pi, n_item, desc, si, fonte, d.get("objeto"))
            ref_cat = ref
            if ref_cat and ref_cat.get("catmat"):
                itens_cat.append({"tipo": modulo_pca.tipo_catalogo(ref_cat.get("catalogo")), "codigo": ref_cat["catmat"],
                                  "descricao": ref_cat.get("catmat_nome") or (desc or "")[:100], "valor": float(total) if total is not None else 0.0})
            elif it.get("catmat"):
                cod = re.sub(r"\D", "", str(it.get("catmat")))[:10]
                if cod:
                    itens_cat.append({"tipo": "S" if d.get("objeto") == "SERVICO" else "M", "codigo": cod, "descricao": (desc or "")[:100],
                                      "valor": float(total) if total is not None else 0.0})
            itens.append({"item": n_item, "si": si, "descricao": desc, "und": und, "qtde": qtde, "valor_unit": vu, "total": total,
                          "sugestao_si": sugestao})
        if not (cnpj and nome):
            ok_geral = False
        saida.append({"cnpj": cnpj, "nome": nome, "itens": itens,
                      "total": sum((i["total"] for i in itens if i["total"] is not None), ZERO)})
    if atas:
        av.norm["atas"] = atas
    av.norm["itens_catalogo"] = itens_cat
    return saida if ok_geral else None


def _resumo_fonte_pendente(fonte: dict) -> dict:
    """Fonte ainda sem escolha do usuário: várias NC de mesmo número (candidatas) ou várias células de destino."""
    if "candidatas" in fonte:
        return {"candidatas": fonte["candidatas"]}
    return {"escolher_celula": True, "ug": fonte["ug"], "nc": fonte["nc"], "emitente": fonte["emitente"], "nome_emitente": fonte["nome_emitente"],
            "celulas": [{k: c[k] for k in ("seq", "nd", "pi", "ptres", "ugr", "fonte", "valor")} for c in fonte["celulas"]]}


def _resumo_fonte_extra(r: dict, fonte: dict) -> None:
    """Campos de detalhamento (ND 00) e de crédito que a interface exibe junto da fonte."""
    r["nd_origem"] = fonte.get("nd_origem")
    r["indetalhada"] = fonte.get("indetalhada", False)
    r["celula_idx"] = fonte.get("celula_idx", 0)
    if fonte.get("indetalhada"):
        r["nds_candidatas"] = fonte.get("nds_candidatas", [])
        r["nd_destino_sugerida"] = fonte.get("nd_destino_sugerida")
        det = fonte.get("detalhamento")
        if det:
            r["detalhamento"] = {"nd_origem": det["nd_origem"], "nd_destino": det["nd_destino"], "nome_elemento": det["nome_elemento"],
                                 "nc": det["nc"], "candidatas": det["candidatas"]}
    if fonte.get("credito"):
        r["credito"] = {"disponivel": str(fonte["credito"]["disponivel"]), "posicao": fonte["credito"]["posicao"],
                        "origem": fonte["credito"].get("origem")}


def _avaliar_limite_dispensa(av: Avaliacao, lic: dict | None, total: Decimal, data_doc: date | None) -> None:
    """Dispensa por valor (art. 75, I e II, da Lei 14.133/2021): confere o valor DESTA requisição com o limite vigente."""
    if not lic or lic.get("modalidade") != "DISPENSA" or not total:
        return
    fund = (lic.get("fundamento") or {}).get("id")
    lim = config.catalogos().get("limites_legais") or {}
    chave = {"ART75_I": "art75_i", "ART75_II": "art75_ii"}.get(fund)
    if not chave or chave not in lim:
        return
    limite = Decimal(lim[chave])
    inciso = "I" if chave == "art75_i" else "II"
    if data_doc and data_doc.year != lim.get("ano"):
        av.alerta("LIMITE_DISPENSA_DESATUALIZADO", "licitacao.fundamento_dispensa",
                  f"Os limites do art. 75 cadastrados no sistema são de {lim.get('ano')} ({lim.get('decreto')}); o documento é de {data_doc.year}. "
                  f"Atualize config/catalogos.json (limites_legais) antes de confiar na conferência.", "Lei 14.133/2021, art. 182")
    elif total >= limite:
        av.alerta("DISPENSA_ACIMA_DO_LIMITE", "licitacao.fundamento_dispensa",
                  f"O valor da requisição ({brl(total)}) alcança ou supera o limite do art. 75, {inciso} ({brl(limite)}, {lim.get('decreto')}). "
                  f"A dispensa por valor exige valor inferior ao limite.", "Lei 14.133/2021, art. 75, " + inciso)
    av.info("DISPENSA_SOMATORIO_ANUAL", "licitacao.fundamento_dispensa",
            f"Limite do art. 75, {inciso}: {brl(limite)} ({lim.get('decreto')}). Só o valor desta requisição foi conferido; a Lei exige aferir o somatório "
            f"do exercício da UG com objetos de mesma natureza (art. 75, § 1º).", "Lei 14.133/2021, art. 75, § 1º")


def _conferir_pca(av: Avaliacao, pca_norm: dict | None, d: dict, data_doc: date | None, itens_cat: list[dict],
                  finalidade_texto: str | None) -> dict:
    """Cruza o PCA informado com o PCA do BCMS publicado no PGC/PNCP e monta as sugestões de preenchimento."""
    ano_doc = data_doc.year if data_doc else date.today().year
    funcao = ((d.get("cabecalho") or {}).get("requisitante") or {}).get("funcao")
    sug = modulo_pca.sugerir(ano_doc, itens_cat, funcao if isinstance(funcao, str) else None, finalidade_texto)
    resumo = {k: sug[k] for k in ("ano_documento", "ano_usado", "disponivel", "posicao", "aviso", "classes", "sugestoes")}
    resumo["contratacao"] = None
    if not (pca_norm and pca_norm.get("consta")):
        return resumo
    anos = modulo_pca.anos_disponiveis()
    ano_pca = pca_norm["ano"]
    uasg_nr, resto = pca_norm["contratacao"].split("-", 1)
    numero = int(resto.split("/")[0])
    if uasg_nr != config.unidade()["uasg_propria"]:
        av.alerta("PCA_UASG_DIFERENTE", "pca.contratacao",
                  f"O nº da contratação começa por {uasg_nr}, e a UASG do BCMS é {config.unidade()['uasg_propria']}. Confira o número.")
    if ano_pca in anos:
        c = modulo_pca.contratacao(ano_pca, numero)
        posicao = (modulo_pca.carregar().get("anos", {}).get(str(ano_pca), {}) or {}).get("posicao") or "—"
        if not c:
            av.alerta("PCA_CONTRATACAO_INEXISTENTE", "pca.contratacao",
                      f"A contratação {numero}/{ano_pca} não consta no PCA {ano_pca} do BCMS publicado no PGC/PNCP (posição {str(posicao)[:10]}). "
                      f"Confira o número ou use as sugestões.", "Lei 14.133/2021, arts. 12, VII, e 18")
        else:
            resumo["contratacao"] = {"codigo": c["codigo"], "objetos": c["objetos"][:2], "projetos": c["projetos"][:2], "classes": c["classes"][:8]}
            av.info("PCA_CONTRATACAO_OK", "pca.contratacao", f"PCA {ano_pca}, contratação {numero}: {(c['objetos'] or ['—'])[0][:140]} ({', '.join(c['projetos'][:2]) or '—'}).")
    else:
        av.info("PCA_ANO_SEM_DADOS", "pca.contratacao", f"O PCA {ano_pca} do BCMS não está publicado no PGC/PNCP (anos disponíveis: {', '.join(map(str, anos)) or 'nenhum'}): número não conferido.")
    cod = re.match(r"\d{3,4}", pca_norm.get("classe") or "")
    if cod and sug["classes"]:
        codigos = {c["codigo"] for c in sug["classes"]}
        if cod.group() not in codigos:
            av.alerta("PCA_CLASSE_DIVERGE_ITEM", "pca.classe",
                      "A classe informada (" + cod.group() + ") não é a do catálogo para os itens: " + "; ".join(c["rotulo"] for c in sug["classes"][:3]) + ".")
    elif sug["classes"] == [] and itens_cat:
        av.info("PCA_CLASSE_NAO_CONFERIDA", "pca.classe", "A classe dos itens no catálogo não pôde ser consultada: classe do PCA não conferida.")
    return resumo


def avaliar_requisicao(d: dict) -> Avaliacao:
    hoje = _iso_para_data(d.get("hoje")) or date.today()
    av = Avaliacao("REQUISICAO", ciente=set(d.get("alertas_ciente") or []))
    cab = _avaliar_cabecalho(av, d, hoje)
    data_doc = cab["data"] if cab else None
    objeto = d.get("objeto")
    if objeto not in ("MATERIAL", "SERVICO"):
        av.erro("OBJETO", "objeto", "Selecione o objeto: aquisição de material ou contratação de serviço.")
        objeto = None
    lic = _avaliar_licitacao(av, d, hoje)
    _regra_tipo_unidade(av, lic or {})
    adesao = _avaliar_adesao(av, d, lic or {}, hoje, data_doc)
    fin = _avaliar_finalidade(av, d, objeto)
    te = _cat("tipos_empenho", d.get("tipo_empenho"))
    if not te:
        av.erro("TIPO_EMPENHO", "tipo_empenho", "Selecione o tipo de empenho (Ordinário, Global ou Estimativo).")
    contrato, pca = _avaliar_contrato_pca(av, d, (lic or {}).get("modalidade"), data_doc)

    # total primeiro (a NC confere saldo contra ele), mas a NC é necessária para o cruzamento de ND nos itens
    total_prev = ZERO
    for f in d.get("fornecedores") or []:
        for it in f.get("itens") or []:
            try:
                total_prev += total_item(parse_decimal(it.get("qtde")), parse_decimal(it.get("valor_unit")))
            except (ValorInvalido, TypeError, ArithmeticError):
                pass
    descricoes = [str(it.get("descricao") or "") for f in d.get("fornecedores") or [] for it in f.get("itens") or []]
    fonte = _avaliar_nc(av, d.get("fonte") or {}, total_prev or None, objeto, hoje, descricoes=descricoes)
    fonte_ok = fonte if fonte and "nd" in fonte else None
    fornecedores = _avaliar_itens_requisicao(av, d, lic, fonte_ok, hoje, data_doc)
    total = sum((f["total"] for f in fornecedores), ZERO) if fornecedores else total_prev
    _avaliar_limite_dispensa(av, lic, total, data_doc)
    resumo_pca = _conferir_pca(av, pca, d, data_doc, av.norm.get("itens_catalogo", []), (fin or {}).get("texto"))

    resumo = {"total": str(total), "total_fmt": brl(total) if total else None, "pca": resumo_pca}
    if lic:
        resumo["licitacao"] = {"numero": lic.get("numero"), "ugg": lic.get("ugg"), "nome_om": lic.get("nome_om"),
                               "tipo_unidade": lic.get("tipo_unidade"), "origem": lic.get("origem")}
    if fonte_ok:
        resumo["fonte"] = {k: fonte_ok[k] for k in ("ug", "nc", "emitente", "nome_emitente", "nd", "pi", "ptres", "ugr", "fonte_rec")}
        resumo["fonte"]["celulas"] = [{k: c[k] for k in ("seq", "nd", "pi", "ptres", "ugr", "fonte", "valor")} for c in fonte_ok["celulas"]]
        _resumo_fonte_extra(resumo["fonte"], fonte_ok)
        if fonte_ok.get("prazo_empenho"):
            resumo["fonte"]["prazo_empenho"] = fonte_ok["prazo_empenho"].isoformat()
    elif fonte:
        resumo["fonte"] = _resumo_fonte_pendente(fonte)
    av.norm.update({
        "resumo": resumo, "cabecalho": cab, "objeto": objeto, "licitacao": lic, "adesao": adesao, "finalidade": fin,
        "tipo_empenho": te, "contrato": contrato, "pca": pca, "fonte": fonte_ok, "fornecedores": fornecedores,
        "total": total, "anexar_nc": _bool(d.get("anexar_nc"), padrao=True),
    })
    return av


# ----------------------------------------------------------------------------- REFORÇO
def _localizar_ne(av: Avaliacao, ug: str | None, chave_txt: str | None, campo: str, hoje: date) -> dict | None:
    chave = _try(av, f"{campo}.ne", validar_ne, chave_txt, hoje.year)
    if not chave:
        return None
    cands = base.candidatas_ne(chave)
    if ug in UGS_BCMS:
        cands = [c for c in cands if c["ug"] == ug] or []
    if not cands:
        av.erro("NE_INEXISTENTE", f"{campo}.ne",
                f"A NE {chave}{' (UG ' + ug + ')' if ug else ''} não consta na pasta de empenhos do repositório. "
                f"Envie o PDF da Nota de Empenho (Impressão Completa do SIAFI) para continuar.", acao="upload_ne")
        return None
    if len(cands) > 1:
        av.erro("NE_AMBIGUA", f"{campo}.ug", f"A NE {chave} existe nas UGs {', '.join(c['ug'] for c in cands)}. Informe a UG.")
        return None
    return base.ne(cands[0]["ug"], chave)


def _quantidade_atual(item_ne: dict) -> Decimal:
    """Quantidade vigente de um item da NE: inclusões e reforços somam, anulações subtraem."""
    total = ZERO
    for o in item_ne["operacoes"]:
        if o["quantidade"] == "None":
            continue
        sinal = -1 if _sem_acento(o["operacao"]).lower().startswith("anul") else 1
        total += Decimal(o["quantidade"]) * sinal
    return total


def _preco_item_ne(it_ne: dict) -> Decimal | None:
    """Preço unitário registrado na NE: o da última operação de inclusão/reforço (ou última operação com preço)."""
    ops = [o for o in (it_ne.get("operacoes") or []) if o.get("valor_unitario") not in (None, "None")]
    ops_preco = [o for o in ops if _sem_acento(o.get("operacao") or "").lower().startswith(("inclus", "refor"))] or ops
    return Decimal(ops_preco[-1]["valor_unitario"]) if ops_preco else None


def _conferir_ata_no_reforco(av: Avaliacao, nr: dict, itens_out: list[dict], data_doc: date | None) -> None:
    """Reforço de NE de pregão: o item reforçado ainda tem saldo, vigência e preço compatíveis com a ata (quando a ata está na base)?"""
    ug_p, numero = nr["derivados"].get("uasg_pregao"), nr["derivados"].get("pregao")
    regs = base.pregao_por_numero(ug_p, numero) if (ug_p and numero) else []
    if not regs:
        return
    alvo = re.sub(r"\W", "", nr.get("favorecido_codigo") or "").upper()
    for k, it in enumerate(itens_out):
        candidatos = [i for r in regs for i in r["itens"] if i["item"] == it["item_compra"]]
        ref = next((i for i in candidatos if alvo and re.sub(r"\W", "", i.get("fornecedor_cnpj") or "").upper() == alvo), None)
        if not ref:
            continue
        pi = f"itens[{k}]"
        saldo = Decimal(ref["qtd_saldo"]) if ref.get("qtd_saldo") not in (None, "None") else None
        if saldo is not None and it["qtde"] > saldo:
            av.erro("QTDE_ACIMA_SALDO", f"{pi}.qtde", f"Item {it['item_compra']}: o reforço ({it['qtde']:f}) é maior que o saldo do BCMS na ata ({saldo:f}).",
                    "Decreto 11.462/2023, arts. 31 e 32")
        fim = _iso_para_data(ref.get("vigencia_fim"))
        if fim and data_doc and fim < data_doc:
            av.alerta("ATA_VENCIDA_NO_REFORCO", f"{pi}.qtde",
                      f"Item {it['item_compra']}: a ata venceu em {_fmt_data(fim)}, antes da data deste reforço. Confirme que o acréscimo ainda pode ser empenhado.",
                      "Decreto 11.462/2023, art. 34, parágrafo único")
        ref_vu = Decimal(ref["valor_unitario"]) if ref.get("valor_unitario") not in (None, "None") else None
        if ref_vu is not None and ref_vu != Decimal(1) and ref_vu != it["valor_unit"] and it["qtde"] != 1:
            av.alerta("PRECO_NE_DIVERGE_ATA", f"{pi}.qtde",
                      f"Item {it['item_compra']}: o valor unitário da NE ({fmt_vu(it['valor_unit'])}) é diferente do registrado na ata ({fmt_vu(ref_vu)}).",
                      "Lei 14.133/2021, art. 82 (preços registrados)")


def _dados_ne_resumo(ne: dict) -> dict:
    return {"ug": ne["ug_pasta"], "chave": ne["chave"], "favorecido_codigo": mascarar_cpf(ne.get("favorecido_codigo")),
            "favorecido_nome": base.nome_completo_fornecedor(ne.get("favorecido_codigo"), ne.get("favorecido_nome")), "data_emissao": ne.get("data_emissao"),
            "tipo_empenho": ne.get("tipo_empenho"), "nd": ne.get("nd"), "pi": ne.get("pi"), "ptres": ne.get("ptres"),
            "ugr": ne.get("ugr"), "fonte": ne.get("fonte"), "processo": ne.get("processo"),
            "valor_atual": Decimal(ne["valor_atual"]), "valor_incluido": Decimal(ne["valor_incluido"]),
            "valor_reforcado": Decimal(ne["valor_reforcado"]), "valor_anulado": Decimal(ne["valor_anulado"]),
            "descricao": ne.get("descricao"), "derivados": ne.get("derivados") or {}, "arquivo": ne.get("caminho"),
            "itens": ne["itens"]}


def _doc_favorecido(ne: dict) -> tuple[str, str]:
    """('29.381.249/0001-76', 'NOME') — se o favorecido for UG (código de 6 dígitos), devolve 'UG 160329'."""
    cod = ne.get("favorecido_codigo") or ""
    nome = base.nome_completo_fornecedor(cod, ne.get("favorecido_nome")) or ""
    try:
        doc = validar_documento_favorecido(cod)
        return (mascarar_cpf(doc) if len(doc) == 14 else doc), nome  # CPF (14 caracteres formatado) sai mascarado; CNPJ tem 18
    except ErroFormato:
        return (f"UG {cod}" if re.fullmatch(r"\d{6}", cod) else cod), nome


def avaliar_reforco(d: dict) -> Avaliacao:
    hoje = _iso_para_data(d.get("hoje")) or date.today()
    av = Avaliacao("REFORCO", ciente=set(d.get("alertas_ciente") or []))
    cab = _avaliar_cabecalho(av, d, hoje)
    data_doc = cab["data"] if cab else None
    ne_in = d.get("empenho") or {}
    ne = _localizar_ne(av, ne_in.get("ug"), ne_in.get("ne"), "empenho", hoje)
    if ne:
        nr = _dados_ne_resumo(ne)
        nr["doc_favorecido"] = _doc_favorecido(ne)
        if nr["valor_atual"] <= 0:
            av.erro("NE_ZERADA", "empenho.ne", f"A NE {nr['chave']} está com valor zero (totalmente anulada): não há o que reforçar. Emita nova requisição.")
        if data_doc and nr["chave"][:4] != str(data_doc.year):
            av.alerta("NE_EXERCICIO_ANTERIOR", "empenho.ne",
                      f"A NE é de {nr['chave'][:4]} e o documento é de {data_doc.year}: empenho de exercício encerrado não admite reforço (restos a pagar).",
                      "Decreto 93.872/1986, arts. 67 e 68")
        em = _data_br_iso(nr["data_emissao"])
        if em and data_doc and data_doc < em:
            av.erro("DATA_ANTES_NE", "cabecalho.data", f"A data do documento é anterior à emissão da NE ({_fmt_data(em)}).")
    else:
        nr = None
    # itens a reforçar
    linhas = d.get("itens") or []
    if not linhas:
        av.erro("SEM_ITENS", "itens", "Selecione ao menos um item da NE e informe a quantidade a reforçar.")
    itens_out = []
    total = ZERO
    vistos: set[int] = set()
    for k, ln in enumerate(linhas):
        pi = f"itens[{k}]"
        try:
            seq = int(ln.get("seq"))
        except (TypeError, ValueError):
            av.erro("ITEM_SEQ", f"{pi}.seq", "Item da NE inválido.")
            continue
        if seq in vistos:
            av.erro("ITEM_REPETIDO", f"{pi}.seq", f"Item {seq} da NE repetido.")
        vistos.add(seq)
        tem_valor = ln.get("valor") not in (None, "")
        tem_qtde = ln.get("qtde") not in (None, "")
        if tem_valor and tem_qtde:
            qtd_in = _dec(av, f"{pi}.qtde", ln.get("qtde"), "Quantidade a reforçar")
            val_in = _centavos(av, f"{pi}.valor", _dec(av, f"{pi}.valor", ln.get("valor"), "Valor a reforçar"), "Valor a reforçar")
        else:
            qtd_in = None if tem_valor else _dec(av, f"{pi}.qtde", ln.get("qtde"), "Quantidade a reforçar")
            val_in = _centavos(av, f"{pi}.valor", _dec(av, f"{pi}.valor", ln.get("valor"), "Valor a reforçar"), "Valor a reforçar") if tem_valor else None
        if not nr:
            continue
        it = next((i for i in nr["itens"] if i["seq"] == seq), None)
        if not it:
            av.erro("ITEM_NAO_NA_NE", f"{pi}.seq", f"A NE {nr['chave']} não tem o item {seq}.")
            continue
        preco_ne = _preco_item_ne(it)

        if tem_valor and tem_qtde:
            vu = preco_ne
            if qtd_in is not None and val_in is not None and vu is not None:
                calc_val = total_item(qtd_in, vu)
                if abs(calc_val - val_in) > Decimal("0.05"):
                    av.erro("ITEM_QTDE_E_VALOR", f"{pi}.qtde", "Informe a quantidade OU o valor a reforçar, não os dois.")
                    continue
            qtde = qtd_in
        elif tem_valor:  # item de quantidade 1 (serviço/global) ou informado por valor
            if _quantidade_atual(it) != 1 and not ln.get("converter_valor"):
                av.erro("ITEM_VALOR_SO_QTD1", f"{pi}.valor",
                        f"Informar o valor só vale para item de quantidade 1; o item {seq} tem quantidade {_quantidade_atual(it).normalize():f}. Informe a quantidade.")
                continue
            if _quantidade_atual(it) == 1:
                vu = val_in
                qtde = Decimal(1)
            else:
                vu = preco_ne
                qtde = (val_in / vu).quantize(Decimal("0.000001")) if vu and vu > 0 else Decimal(1)
        else:
            vu = preco_ne
            qtde = qtd_in

        if vu is None or qtde is None:
            continue
        if qtde.as_tuple().exponent < -6:
            av.erro("QTDE_CASAS", f"{pi}.qtde", "Quantidade com mais de 6 casas decimais.")
        valor = total_item(qtde, vu)
        total += valor
        itens_out.append({"seq": seq, "item_compra": it.get("item_compra"), "si": it.get("subelemento"),
                          "descricao": it["descricao"], "und": ln.get("und") if ln.get("und") in config.catalogos()["unidades_medida"] else "Und",
                          "qtde": qtde, "valor_unit": vu, "total": valor})
    # crédito
    fonte_in = d.get("fonte") or {}
    if nr and not fonte_in.get("nc") and nr["derivados"].get("nc"):
        fonte_in = dict(fonte_in, nc=nr["derivados"]["nc"])
    celula_esperada = {k: nr.get(k) for k in ("nd", "pi", "ptres", "fonte", "ugr")} if nr else None
    fonte = _avaliar_nc(av, fonte_in, total or None, None, hoje, "fonte", celula_esperada, ug_padrao=nr["ug"] if nr else None)
    fonte_ok = fonte if fonte and "nd" in fonte else None
    if nr and fonte_ok and fonte_ok["ug"] != nr["ug"]:
        av.erro("UG_NC_DIFERENTE_NE", "fonte.ug", f"A NE é da UG {nr['ug']}, mas a NC indicada favorece a UG {fonte_ok['ug']}.")
    # motivo
    motivo_in = d.get("motivo") or {}
    mot_id = motivo_in.get("id")
    mot_texto = (motivo_in.get("texto") or motivo_in.get("outro") or "").strip()
    if mot_id == "OUTRO" or (not mot_id and mot_texto):
        if not mot_texto:
            av.erro("MOTIVO_TEXTO", "motivo.texto", "Escreva o motivo do reforço.")
            motivo = None
        else:
            motivo = {"id": "OUTRO", "texto": mot_texto}
    else:
        motivo = _cat("motivos_reforco", mot_id)
        if not motivo:
            av.erro("MOTIVO", "motivo.id", "Selecione o motivo do reforço.")
    detalhe = _try(av, "motivo.detalhe", validar_texto_curto, motivo_in.get("detalhe") or "", "Detalhamento", 0, 500) or ""
    # limites
    if nr and total and nr["valor_incluido"] > 0:
        acumulado = nr["valor_reforcado"] + total
        pct = acumulado / nr["valor_incluido"] * 100
        if pct > 25:
            av.alerta("ACRESCIMO_ACIMA_25", "itens",
                      f"Os reforços acumulados ({brl(acumulado)}) correspondem a {pct:.1f}% do valor inicial da NE ({brl(nr['valor_incluido'])}); "
                      f"o limite de acréscimos nos contratos e compras é de 25%. Verifique a aplicabilidade e a justificativa.",
                      "Lei 14.133/2021, art. 125")
    if nr and nr["derivados"].get("pregao"):
        ug_p = nr["derivados"].get("uasg_pregao")
        regs = base.pregao_por_numero(ug_p, nr["derivados"]["pregao"]) if ug_p else []
        if regs:
            av.info("ATA_DA_NE", "empenho.ne", f"NE vinculada ao pregão {nr['derivados']['pregao']} (UASG {ug_p}).")
            _conferir_ata_no_reforco(av, nr, itens_out, data_doc)
        else:
            av.info("ATA_NAO_CONFERIDA", "empenho.ne", "Pregão da NE não localizado na base do dashboard: preço e saldo da ata não conferidos.")
    resumo = {"total": str(total), "total_fmt": brl(total) if total else None}
    if nr:
        resumo["ne"] = {"chave": nr["chave"], "ug": nr["ug"], "valor_atual": str(nr["valor_atual"]), "favorecido": nr["favorecido_nome"],
                        "favorecido_codigo": nr["favorecido_codigo"], "nd": nr["nd"], "pi": nr["pi"], "tipo_empenho": nr["tipo_empenho"],
                        "valor_incluido": str(nr["valor_incluido"]), "nc_da_ne": nr["derivados"].get("nc"),
                        "ncs_da_celula": base.ncs_por_celula(nr["ug"], nr["nd"], nr["pi"]),
                        "valor_apos": str(nr["valor_atual"] + total),
                        "itens": [{"seq": i["seq"], "item_compra": i.get("item_compra"), "subelemento": i.get("subelemento"),
                                   "descricao": i["descricao"], "valor_liquido": i["valor_liquido"],
                                   "valor_unitario": str(_preco_item_ne(i) or ''),
                                   "quantidade_atual": str(sum((Decimal(o["quantidade"]) * (1 if o["operacao"].lower() != "anulação" else -1)
                                                                for o in i["operacoes"] if o["quantidade"] != "None"), ZERO))}
                                  for i in nr["itens"]]}
    if fonte_ok:
        resumo["fonte"] = {k: fonte_ok[k] for k in ("ug", "nc", "emitente", "nome_emitente", "nd", "pi", "ptres", "ugr", "fonte_rec")}
        resumo["fonte"]["celulas"] = [{k: c[k] for k in ("seq", "nd", "pi", "ptres", "ugr", "fonte", "valor")} for c in fonte_ok["celulas"]]
        _resumo_fonte_extra(resumo["fonte"], fonte_ok)
    elif fonte:
        resumo["fonte"] = _resumo_fonte_pendente(fonte)
    av.norm.update({"resumo": resumo, "cabecalho": cab, "ne": nr, "itens": itens_out, "fonte": fonte_ok,
                    "motivo": {"texto": motivo["texto"] if motivo else None, "id": (motivo or {}).get("id"), "detalhe": detalhe.rstrip(". ")},
                    "total": total})
    return av


# ----------------------------------------------------------------------------- ANULAÇÃO
def avaliar_anulacao(d: dict) -> Avaliacao:
    hoje = _iso_para_data(d.get("hoje")) or date.today()
    av = Avaliacao("ANULACAO", ciente=set(d.get("alertas_ciente") or []))
    cab = _avaliar_cabecalho(av, d, hoje)
    data_doc = cab["data"] if cab else None
    linhas = d.get("empenhos") or []
    if not linhas:
        av.erro("SEM_EMPENHOS", "empenhos", "Inclua ao menos um empenho a anular.")
    vistos: set[tuple[str, str]] = set()
    registros = []
    total = ZERO
    for k, ln in enumerate(linhas):
        pf = f"empenhos[{k}]"
        ne = _localizar_ne(av, ln.get("ug"), ln.get("ne"), pf, hoje)
        if not ne:
            continue
        nr = _dados_ne_resumo(ne)
        if (nr["ug"], nr["chave"]) in vistos:
            av.erro("NE_REPETIDA", f"{pf}.ne", f"A NE {nr['chave']} aparece mais de uma vez.")
        vistos.add((nr["ug"], nr["chave"]))
        tipo = ln.get("tipo")
        if tipo not in ("TOTAL", "PARCIAL"):
            av.erro("TIPO_ANULACAO", f"{pf}.tipo", "Informe se a anulação é total ou parcial.")
        if ln.get("liquidado") in (None, ""):  # nada de assumir "0": o liquidado vem do SIAFI e quem requisita precisa tê-lo consultado
            av.erro("LIQUIDADO_OBRIGATORIO", f"{pf}.liquidado",
                    "Informe o valor já liquidado desta NE no SIAFI (digite 0,00 se nada foi liquidado).", "Decreto 93.872/1986, art. 28")
            liq = None
        else:
            liq = _centavos(av, f"{pf}.liquidado", _dec(av, f"{pf}.liquidado", ln.get("liquidado"), "Valor já liquidado", positivo=False), "Valor já liquidado")
        if liq is not None and liq < 0:
            av.erro("VALOR_NAO_POSITIVO", f"{pf}.liquidado", "Valor liquidado não pode ser negativo.")
            liq = None
        saldo = nr["valor_atual"] - (liq or ZERO)
        if liq is not None and liq > nr["valor_atual"]:
            av.erro("LIQUIDADO_MAIOR_NE", f"{pf}.liquidado", f"O valor liquidado ({brl(liq)}) é maior que o valor da NE ({brl(nr['valor_atual'])}).")
        if nr["valor_atual"] <= 0:
            av.erro("NE_ZERADA", f"{pf}.ne", f"A NE {nr['chave']} já está com valor zero: não há o que anular.")
        valor = None
        itens_anulados = []
        if ln.get("itens"):
            for j, it_in in enumerate(ln["itens"]):
                p_it = f"{pf}.itens[{j}]"
                try:
                    seq = int(it_in.get("seq"))
                except (TypeError, ValueError):
                    continue
                it_ne = next((i for i in nr["itens"] if i["seq"] == seq), None)
                if not it_ne:
                    continue
                vu = _preco_item_ne(it_ne) or ZERO
                val_it = _centavos(av, f"{p_it}.valor", _dec(av, f"{p_it}.valor", it_in.get("valor"), "Valor a anular do item", positivo=False), "Valor a anular do item") if it_in.get("valor") not in (None, "") else None
                qtd_it = _dec(av, f"{p_it}.qtde", it_in.get("qtde"), "Quantidade a anular", positivo=False) if it_in.get("qtde") not in (None, "") else None
                if val_it is None and qtd_it is not None and vu > 0:
                    val_it = total_item(qtd_it, vu)
                elif val_it is not None and qtd_it is None and vu > 0:
                    qtd_it = (val_it / vu).quantize(Decimal("0.000001"))
                if val_it is not None and val_it > 0:
                    saldo_it = Decimal(it_ne.get("valor_liquido") or "0")
                    if val_it > saldo_it:
                        av.alerta("ITEM_ANULACAO_ACIMA_SALDO", f"{p_it}.valor", f"Valor a anular do item {seq} ({brl(val_it)}) excede o saldo atual do item ({brl(saldo_it)}).")
                    itens_anulados.append({
                        "seq": seq, "item_compra": it_ne.get("item_compra") or seq,
                        "subelemento": it_ne.get("subelemento"), "descricao": it_ne.get("descricao"),
                        "qtde": qtd_it, "valor_unit": vu, "valor": val_it,
                    })

        if tipo == "TOTAL" and liq is not None:
            if liq > 0:
                av.erro("TOTAL_COM_LIQUIDACAO", f"{pf}.tipo",
                        f"A NE {nr['chave']} tem {brl(liq)} já liquidado: só é possível anular o saldo a liquidar ({brl(saldo)}), isto é, anulação parcial.",
                        "Decreto 93.872/1986, art. 28")
            else:
                valor = nr["valor_atual"]
        elif tipo == "PARCIAL":
            if ln.get("valor") in (None, "") and itens_anulados:
                valor = sum(x["valor"] for x in itens_anulados)
            else:
                valor = _centavos(av, f"{pf}.valor", _dec(av, f"{pf}.valor", ln.get("valor"), "Valor a anular"), "Valor a anular")
            if valor is not None and liq is not None:
                if valor > saldo:
                    av.erro("ANULACAO_ACIMA_SALDO", f"{pf}.valor",
                            f"O valor a anular ({brl(valor)}) excede o saldo a liquidar da NE ({brl(saldo)} = {brl(nr['valor_atual'])} − {brl(liq)} liquidado).",
                            "Decreto 93.872/1986, art. 28")
                elif valor == nr["valor_atual"]:
                    av.alerta("PARCIAL_IGUAL_TOTAL", f"{pf}.tipo", "O valor informado é igual ao valor da NE: trata-se de anulação total.")
        if data_doc:
            em = _data_br_iso(nr["data_emissao"])
            if em and data_doc < em:
                av.erro("DATA_ANTES_NE", "cabecalho.data", f"A data do documento é anterior à emissão da NE {nr['chave']} ({_fmt_data(em)}).")
            if nr["chave"][:4] != str(data_doc.year):
                av.alerta("NE_EXERCICIO_ANTERIOR", f"{pf}.ne",
                          f"A NE {nr['chave']} é de {nr['chave'][:4]}: anulação após o encerramento do exercício não reverte à dotação — o valor é receita do exercício em que se efetivar.",
                          "Lei 4.320/1964, art. 38")
        if valor is not None:
            total += valor
        registros.append({**nr, "tipo": tipo, "liquidado": liq, "valor_anular": valor,
                          "saldo_apos": (nr["valor_atual"] - valor) if valor is not None else None,
                          "doc_favorecido": _doc_favorecido(ne), "itens_anulados": itens_anulados})
    if not _bool(d.get("consultou_siafi")):
        av.erro("CONFIRMAR_SIAFI", "consultou_siafi",
                "Confirme que o valor já liquidado de cada NE foi conferido no SIAFI/Tesouro Gerencial: a anulação não pode alcançar parcela liquidada.",
                "Decreto 93.872/1986, art. 28")
    motivo_in = d.get("motivo") or {}
    mot_id = motivo_in.get("id")
    mot_texto = (motivo_in.get("texto") or motivo_in.get("outro") or "").strip()
    if mot_id == "OUTRO" or (not mot_id and mot_texto):
        if not mot_texto:
            av.erro("MOTIVO_TEXTO", "motivo.texto", "Escreva o motivo da anulação.")
            motivo = None
        else:
            motivo = {"id": "OUTRO", "texto": mot_texto}
    else:
        motivo = _cat("motivos_anulacao", mot_id)
        if not motivo:
            av.erro("MOTIVO", "motivo.id", "Selecione o motivo da anulação.")
    detalhe = _try(av, "motivo.detalhe", validar_texto_curto, motivo_in.get("detalhe") or "", "Detalhamento", 0, 500) or ""
    if motivo and motivo["id"] == "REEMPENHO_PI" and not _bool(d.get("reempenho")):
        av.alerta("REEMPENHO_NAO_MARCADO", "reempenho", "O motivo é reempenho, mas não foi marcado que haverá nova requisição de empenho.")
    resumo = {"total": str(total), "total_fmt": brl(total) if total else None,
              "empenhos": [{"chave": r["chave"], "ug": r["ug"], "favorecido": r["favorecido_nome"], "valor_atual": str(r["valor_atual"]),
                            "valor_anular": str(r["valor_anular"]) if r["valor_anular"] is not None else None,
                            "saldo_apos": str(r["saldo_apos"]) if r["saldo_apos"] is not None else None,
                            "nc": r["derivados"].get("nc"),
                            "itens": [{"seq": it.get("seq"), "item_compra": it.get("item_compra") or it.get("seq"),
                                       "subelemento": it.get("subelemento"), "descricao": it.get("descricao"),
                                       "quantidade_atual": str(_quantidade_atual(it)),
                                       "valor_unitario": str(_preco_item_ne(it) or ''),
                                       "valor_liquido": str(it.get("valor_liquido"))}
                                      for it in r.get("itens", [])]} for r in registros]}
    av.norm.update({"resumo": resumo, "cabecalho": cab, "empenhos": registros, "total": total,
                    "motivo": {"texto": motivo["texto"] if motivo else None, "id": (motivo or {}).get("id"), "detalhe": detalhe.rstrip(". ")},
                    "reempenho": _bool(d.get("reempenho"))})
    return av


AVALIADORES = {"requisicao": avaliar_requisicao, "reforco": avaliar_reforco, "anulacao": avaliar_anulacao}
