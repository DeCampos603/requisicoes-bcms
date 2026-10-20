# -*- coding: utf-8 -*-
"""API local (FastAPI) do Sistema de Requisições de Empenho do BCMS.

Executar:  py -3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765
(o iniciar.bat faz isso). O servidor atende apenas 127.0.0.1 e, mesmo assim, só responde a quem prova que é a
própria interface: Host de loopback (barra DNS rebinding), Origin/Sec-Fetch-Site do mesmo site e um token
aleatório por execução (barra CSRF de outra aba do navegador). Os dados de crédito e de empenho não devem ser
alcançáveis por página alheia.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pymupdf

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from reportlab.platypus.doctemplate import LayoutError

from . import __version__, anexos, base, classificacao, config, docx_docs, historico, importadores, pca, pdf_docs, rascunho, regras, relatorio_participantes, textos

log = logging.getLogger("reqemp")
app = FastAPI(title="Requisições de Empenho — BCMS", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

TIPOS = {"requisicao": "REQUISICAO", "reforco": "REFORCO", "anulacao": "ANULACAO"}
TOKEN = secrets.token_urlsafe(24)
HOSTS_PERMITIDOS = {"127.0.0.1", "localhost", "[::1]"} | {h.strip() for h in os.environ.get("REQEMP_HOSTS", "").split(",") if h.strip()}
LIMITE_JSON = 2 * 1024 * 1024
LIMITE_UPLOAD = config.TAMANHO_MAX_UPLOAD + 256 * 1024
CSP_HTML = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'none'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

_atualizacao = {"rodando": False, "log": [], "resultado": None, "inicio": None}
_trava_atualizacao = threading.Lock()


def _host_sem_porta(valor: str) -> str:
    valor = valor.strip().lower()
    return valor.split("]")[0] + "]" if valor.startswith("[") else valor.split(":")[0]


# ----------------------------------------------------------------------------- proteções
@app.middleware("http")
async def _protecoes(request: Request, call_next):
    host_bruto = request.headers.get("host", "")
    if "*" not in HOSTS_PERMITIDOS and _host_sem_porta(host_bruto) not in HOSTS_PERMITIDOS:
        return JSONResponse(status_code=403, content={"erro": "Host não permitido."})
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sfs = request.headers.get("sec-fetch-site")
        origem = request.headers.get("origin")
        if (sfs and sfs not in ("same-origin", "none")) or (origem and urlparse(origem).netloc.lower() != host_bruto.lower()):
            return JSONResponse(status_code=403, content={"erro": "Origem não permitida."})
        if not secrets.compare_digest(request.headers.get("x-reqemp-token", ""), TOKEN):
            return JSONResponse(status_code=403, content={"erro": "Sessão inválida: recarregue a página."})
        tamanho = request.headers.get("content-length", "")
        limite = LIMITE_UPLOAD if request.url.path.endswith("/upload") else LIMITE_JSON
        if tamanho.isdigit() and int(tamanho) > limite:
            return JSONResponse(status_code=413, content={"erro": "Requisição grande demais."})
    resposta = await call_next(request)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        resposta.headers["Cache-Control"] = "no-store"
    if request.url.path.startswith("/api/pdf/"):
        resposta.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resposta


# ----------------------------------------------------------------------------- erros
@app.exception_handler(anexos.UploadInvalido)
async def _upload_invalido(_: Request, exc: anexos.UploadInvalido):
    return JSONResponse(status_code=exc.status, content={"erro": str(exc)})


@app.exception_handler(Exception)
async def _erro_geral(_: Request, exc: Exception):
    log.exception("Erro não tratado", exc_info=exc)  # detalhe só no log do servidor
    return JSONResponse(status_code=500, content={"erro": "Erro interno ao processar o pedido. Veja o log do servidor."})


# ----------------------------------------------------------------------------- configuração
@app.get("/api/config")
def api_config() -> dict:
    cat = config.catalogos()
    unid = config.unidade()
    return {
        "versao": __version__,
        "hoje": date.today().isoformat(),
        "unidade": {"nome": unid["nome_om"], "sigla": unid["sigla_om"], "uasg": unid["uasg_propria"],
                    "ugs": unid["ugs_executoras"], "fiscal": unid["fiscal_administrativo"], "od": unid["ordenador_despesas"]},
        "catalogos": {
            "tipos_unidade": cat["tipos_unidade"], "modalidades": cat["modalidades"], "objetos": cat["objetos"],
            "tipos_empenho": cat["tipos_empenho"], "hipoteses_sem_contrato": cat["hipoteses_sem_contrato"],
            "fundamentos_dispensa": cat["fundamentos_dispensa"],
            "fundamentos_inexigibilidade": cat.get("fundamentos_inexigibilidade", []),
            "finalidades": base.finalidades(),
            "motivos_reforco": cat["motivos_reforco"], "motivos_anulacao": cat["motivos_anulacao"],
            "unidades_medida": cat["unidades_medida"], "postos": cat["postos"], "requisitantes": cat["requisitantes"],
            "documentos_adesao": cat["documentos_adesao"],
        },
        "origens_diex": historico.origens_diex(),
        "classificador": classificacao.disponivel(),
        "status": base.status(),
    }


@app.post("/api/config/autoridades")
def api_salvar_autoridades(dados: dict) -> dict:
    fiscal = dados.get("fiscal_administrativo") or dados.get("fiscal")
    od = dados.get("ordenador_despesas") or dados.get("od")
    if not isinstance(fiscal, dict) or not isinstance(od, dict):
        raise HTTPException(400, "Dados de Fiscal Administrativo e Ordenador de Despesas são obrigatórios.")

    validados: dict[str, dict[str, str]] = {}
    for chave, papel, obj in [
        ("fiscal_administrativo", "Fiscal Administrativo", fiscal),
        ("ordenador_despesas", "Ordenador de Despesas", od),
    ]:
        nome = str(obj.get("nome") or "").strip()
        posto = str(obj.get("posto") or "").strip()
        funcao = str(obj.get("funcao") or "").strip()
        if len(nome) < 3 or len(nome) > 100:
            raise HTTPException(400, f"Nome do {papel} deve ter entre 3 e 100 caracteres.")
        if len(posto) < 1 or len(posto) > 30:
            raise HTTPException(400, f"Posto/Graduação do {papel} deve ter entre 1 e 30 caracteres.")
        if len(funcao) < 3 or len(funcao) > 80:
            raise HTTPException(400, f"Função do {papel} deve ter entre 3 e 80 caracteres.")
        validados[chave] = {
            "nome": nome.upper(),
            "posto": posto,
            "funcao": funcao,
        }

    arq_unidade = config.CONFIG / "unidade.json"
    with config.ARQUIVO_LOCK:
        conteudo = json.loads(arq_unidade.read_text(encoding="utf-8"))
        conteudo["fiscal_administrativo"] = validados["fiscal_administrativo"]
        conteudo["ordenador_despesas"] = validados["ordenador_despesas"]
        tmp = arq_unidade.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(arq_unidade)
        config.limpar_cache_config()

    unid = config.unidade()
    return {
        "ok": True,
        "mensagem": "Autoridades atualizadas com sucesso.",
        "fiscal": unid["fiscal_administrativo"],
        "od": unid["ordenador_despesas"],
    }


# ----------------------------------------------------------------------------- pregões
@app.get("/api/pregoes")
def api_pregoes(q: str = "") -> list[dict]:
    return base.buscar_pregoes(q[:80])


@app.get("/api/pregao")
def api_pregao(id: str) -> dict:
    p = base.pregao(id)
    if not p:
        raise HTTPException(404, "Pregão não encontrado na base.")
    return {**base._resumo_pregao(p), "itens": p["itens"], "posicao": base.posicao_pregoes()}


# ----------------------------------------------------------------------------- contratos vigentes
@app.get("/api/contratos")
def api_contratos(q: str = "", categoria: str = "") -> list[dict]:
    """Lista contratos vigentes do BCMS para seleção ou consulta na modalidade CONTRATO."""
    return base.buscar_contratos(q[:80], categoria=categoria[:40])


@app.get("/api/contratos/resumo")
def api_contratos_resumo() -> dict:
    """Retorna consolidação e totais dos contratos vigentes do BCMS."""
    return base.resumo_contratos()


@app.get("/api/contrato")
def api_contrato(numero: str) -> dict:
    c = base.contrato_por_numero(numero)
    if not c:
        raise HTTPException(404, "Contrato não encontrado.")
    return c




# ----------------------------------------------------------------------------- relatório pregões participantes
@app.get("/api/relatorio/participantes/resumo")
def api_relatorio_participantes_resumo() -> dict:
    """Retorna dados consolidados dos pregões onde o BCMS é participante (Homologados e PNCP)."""
    return relatorio_participantes.consolidar_dados(atualizar_pncp=False)


@app.get("/api/relatorio/participantes/excel")
def api_relatorio_participantes_excel(online: bool = False) -> FileResponse:
    """Gera e faz o download da pasta de trabalho Excel (.xlsx) executiva com 5 abas."""
    caminho = relatorio_participantes.gerar_relatorio(atualizar_pncp=online)
    return FileResponse(
        path=caminho,
        filename=caminho.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ----------------------------------------------------------------------------- NC / NE (busca e upload)
def _ler_upload(arquivo: UploadFile) -> bytes:
    """Lê no máximo o limite + 1 byte: arquivo maior é recusado sem ocupar a memória inteira."""
    return arquivo.file.read(config.TAMANHO_MAX_UPLOAD + 1)


@app.get("/api/ncs")
def api_ncs(q: str = "", ug: str = "", apenas_com_saldo: bool = False, limite: int = 80) -> list[dict]:
    """Busca Notas de Crédito por palavras da descrição (PDF + SIAFI), número da NC, PI, ND ou UG emitente."""
    return base.buscar_ncs(q[:120], limite=min(max(1, limite), 200), ug=ug[:6] or None, apenas_com_saldo=apenas_com_saldo)


@app.post("/api/nc/upload")
def api_nc_upload(arquivo: UploadFile = File(...), numero: str = Form(...), emitente: str = Form("")) -> dict:
    nc = anexos.salvar_nc(_ler_upload(arquivo), numero[:20], emitente[:6] or None)
    return {"ok": True, "chave": nc["chave"], "emitente": nc["ug_emitente"], "arquivo": nc["arquivo"],
            "candidatas": base.candidatas_nc(nc["chave"])}


@app.post("/api/adesao/upload")
def api_adesao_upload(arquivo: UploadFile = File(...), tipo: str = Form(...)) -> dict:
    """Documento da instrução da adesão (termo de justificativa, pesquisa de preços, aceite do fornecedor, autorização)."""
    return {"ok": True, "documento": anexos.salvar_documento_adesao(_ler_upload(arquivo), tipo[:40])}


@app.get("/api/ne/empresas")
def api_ne_empresas(ug: str = "") -> list[dict]:
    """Lista empresas/favorecidos com NE na base para seleção e filtragem."""
    return base.listar_empresas_nes(ug=ug or None)


@app.get("/api/ne")
def api_ne(q: str = "", ug: str = "", empresa: str = "", limite: int = 500) -> list[dict]:
    return base.buscar_nes(q[:80], limite=min(max(1, limite), 1000), ug=ug or None, empresa=empresa[:80] or None)


@app.post("/api/ne/upload")
def api_ne_upload(arquivo: UploadFile = File(...), numero: str = Form(...), ug: str = Form("")) -> dict:
    ne = anexos.salvar_ne(_ler_upload(arquivo), numero[:20], ug[:6] or None)
    return {"ok": True, "chave": ne["chave"], "ug": ne["ug_pasta"], "arquivo": ne["arquivo"]}


# ----------------------------------------------------------------------------- PCA
@app.get("/api/pca")
def api_pca(ano: int = 0, q: str = "") -> dict:
    """Contratações do PCA do BCMS (para pesquisar e preencher "Nr da contratação"/"Classe"). Sem o ano do PCA, usa o mais recente anterior."""
    ano = ano or date.today().year
    if not 2000 <= ano <= 2100:
        raise HTTPException(422, "Ano inválido.")
    anos = pca.anos_disponiveis()
    usado = ano if ano in anos else max((a for a in anos if a <= ano), default=(max(anos) if anos else None))
    return {"ano": ano, "ano_usado": usado, "anos": anos, "uasg": config.unidade()["pca"]["uasg"],
            "aviso": (f"O PCA {ano} do BCMS não está publicado no PGC/PNCP: lista do PCA {usado}." if usado and usado != ano else None),
            "contratacoes": pca.listar(usado, q[:80]) if usado else []}


# ----------------------------------------------------------------------------- classificação
@app.post("/api/classificar")
def api_classificar(dados: dict) -> dict:
    nd = str(dados.get("nd") or "").strip() or None
    if nd and not re.fullmatch(r"[34]\d{5}", nd):
        raise HTTPException(422, "ND inválida.")
    return classificacao.sugerir_subitens(str(dados.get("descricao") or "")[:2500], nd, dados.get("objeto"))


@app.get("/api/nd/{nd}/subitens")
def api_subitens(nd: str) -> dict:
    if not re.fullmatch(r"[34]\d{5}", nd):
        raise HTTPException(422, "ND inválida.")
    return {"nd": nd, "elemento": classificacao.nome_elemento(nd), "indetalhada": classificacao.nd_indetalhada(nd),
            "subitens": classificacao.subitens_do_elemento(nd)}


# ----------------------------------------------------------------------------- validação e geração
def _avaliar(tipo: str, dados: dict) -> regras.Avaliacao:
    if tipo not in TIPOS:
        raise HTTPException(404, "Tipo de documento desconhecido.")
    try:
        return regras.AVALIADORES[tipo](dados)
    except (TypeError, AttributeError, KeyError, ValueError, IndexError, ArithmeticError) as exc:
        # JSON com tipo trocado (texto onde se esperava lista/objeto etc.): é erro do pedido, não do servidor
        log.warning("Formulário em formato inesperado (%s): %s", tipo, f"{type(exc).__name__}: {str(exc)[:200]}", exc_info=True)
        raise HTTPException(422, "Os dados do formulário estão em formato inválido. Recarregue a página e preencha de novo.") from exc


@app.post("/api/validar/{tipo}")
def api_validar(tipo: str, dados: dict) -> dict:
    return _avaliar(tipo, dados).to_dict()


@app.post("/api/previa-viva/{tipo}")
def api_previa_viva(tipo: str, dados: dict) -> dict:
    """Prévia do DIEx enquanto se preenche: o mesmo documento do PDF final, com campos pendentes em amarelo e marca de rascunho.

    Não grava nada (nem em saida/ nem no histórico) e não libera a geração: quem manda é /api/validar."""
    av = _avaliar(tipo, dados)
    try:
        return {"ok": True, **rascunho.renderizar(av, dados)}
    except rascunho.PreviaIndisponivel as e:
        return {"ok": False, "motivo": str(e), "paginas": [], "pendentes": []}
    except Exception as exc:  # noqa: BLE001 - a prévia nunca pode derrubar o formulário
        log.warning("Prévia ao vivo indisponível (%s): %s", tipo, f"{type(exc).__name__}: {str(exc)[:200]}", exc_info=True)
        return {"ok": False, "motivo": "A prévia ainda não pôde ser montada com os dados atuais.", "paginas": [], "pendentes": []}


def _nome_arquivo(base_nome: str) -> str:
    limpo = re.sub(r"[^\w\-]+", "-", base_nome, flags=re.UNICODE).strip("-")[:90]
    return f"{limpo}_{time.strftime('%Y%m%d-%H%M%S')}"


def _anexos_do_documento(av: regras.Avaliacao) -> list[tuple[str, Path]]:
    n = av.norm
    saida: list[tuple[str, Path]] = []
    if av.tipo == "REQUISICAO":
        for doc in (n.get("adesao") or {}).get("documentos") or []:  # instrução da adesão, antes da NC (mesma ordem do "Anexos:" do DIEx)
            p = Path(doc["caminho"]) if doc.get("caminho") else None
            if p and p.is_file():
                saida.append((doc["rotulo"], p))
        if n.get("anexar_nc") and n.get("fonte"):
            arq_nc = anexos.localizar_anexo_nc(n["fonte"])
            if arq_nc:
                saida.append((f"NC {n['fonte']['nc']}", arq_nc))
    if av.tipo == "REFORCO":
        if n.get("ne"):
            arq_ne = anexos.localizar_anexo_ne(n["ne"])
            if arq_ne:
                saida.append((f"NE {n['ne']['chave']}", arq_ne))
        if n.get("fonte"):
            arq_nc = anexos.localizar_anexo_nc(n["fonte"])
            if arq_nc:
                saida.append((f"NC {n['fonte']['nc']}", arq_nc))
    if av.tipo == "ANULACAO":
        for r in n.get("empenhos") or []:
            arq_ne = anexos.localizar_anexo_ne(r)
            if arq_ne:
                saida.append((f"NE {r['chave']}", arq_ne))
    return [(r, p) for r, p in saida if p.is_file()]



@app.post("/api/gerar/{tipo}")
def api_gerar(tipo: str, dados: dict) -> JSONResponse:
    av = _avaliar(tipo, dados)
    if not av.pode_gerar:
        return JSONResponse(status_code=422, content={"erro": "O documento ainda tem pendências.", **av.to_dict()})
    doc = textos.MONTADORES[av.tipo](av.norm)
    nome = _nome_arquivo(doc["arquivo_base"])
    config.SAIDA.mkdir(parents=True, exist_ok=True)
    anx = _anexos_do_documento(av)
    try:
        pdf_doc = pdf_docs.gerar_pdf_documento(doc, config.SAIDA / f"{nome}.pdf")
    except LayoutError as exc:  # texto que não cabe na página, mesmo quebrando a linha
        log.warning("Falha de diagramação do PDF: %s", str(exc)[:300])
        raise HTTPException(422, "O texto de algum campo é grande demais para caber na página do DIEx. Resuma-o e tente de novo.") from exc
    try:
        docx_doc = docx_docs.gerar_docx_documento(doc, config.SAIDA / f"{nome}.docx", anexos=anx)
    except Exception as exc:
        log.warning("Falha ao gerar documento Word (.docx): %s", exc)
        docx_doc = None
    completo = None
    if anx:
        completo = anexos.montar_pdf_final(pdf_doc, anx, config.SAIDA / f"{nome}_com_anexos.pdf")
    # Pacote ZIP contendo o docx editável + PDFs dos anexos originais (NC, NE, adesão, etc.)
    zip_doc = None
    if docx_doc and docx_doc.is_file():
        try:
            import zipfile
            nome_zip = f"{nome}_word_com_anexos.zip"
            caminho_zip = config.SAIDA / nome_zip
            with zipfile.ZipFile(caminho_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(docx_doc, arcname=docx_doc.name)
                nomes_usados = {docx_doc.name}
                for rotulo, arq_anx in anx:
                    p_anx = Path(arq_anx)
                    if p_anx.is_file():
                        limpo_rotulo = re.sub(r"[^\w\-]+", "_", rotulo).strip("_")
                        nome_no_zip = f"{limpo_rotulo}{p_anx.suffix.lower()}"
                        contador = 1
                        while nome_no_zip in nomes_usados:
                            nome_no_zip = f"{limpo_rotulo}_{contador}{p_anx.suffix.lower()}"
                            contador += 1
                        nomes_usados.add(nome_no_zip)
                        zf.write(p_anx, arcname=nome_no_zip)
            zip_doc = caminho_zip
        except Exception as exc_zip:
            log.warning("Falha ao criar arquivo ZIP com anexos: %s", exc_zip)
            zip_doc = None
    cab = av.norm["cabecalho"]
    fin = av.norm.get("finalidade")
    if fin and fin.get("id") == "OUTRA" and fin.get("texto"):
        base.salvar_finalidade_extra(fin["texto"], av.norm.get("objeto") or "MATERIAL")
    # a ciência só vale para os alertas que realmente existem agora
    ciente = sorted(a.chave for a in av.alertas if a.chave in av.ciente)
    doc_id = historico.registrar(av.tipo, cab["diex"], cab["nup"], cab["data"].year, str(av.norm.get("total")),
                                 (completo or pdf_doc).name, ciente, [a.codigo for a in av.achados])
    from pypdf import PdfReader
    return JSONResponse(content={
        "ok": True, "id": doc_id, "documento": pdf_doc.name, "completo": completo.name if completo else None,
        "docx": docx_doc.name if docx_doc else None,
        "zip": zip_doc.name if zip_doc else None,
        "paginas_diex": len(PdfReader(str(pdf_doc)).pages),
        "anexos": [r for r, _ in anx], "achados": [a.to_dict() for a in av.achados],
    })


@app.post("/api/gerar-word/{tipo}")
def api_gerar_word(tipo: str, dados: dict) -> JSONResponse:
    tipo_norm = tipo.lower()
    if tipo_norm not in ("requisicao", "reforco", "anulacao"):
        raise HTTPException(400, "Tipo de documento inválido.")
    av = _avaliar(tipo, dados)
    if av.pode_gerar:
        doc = textos.MONTADORES[av.tipo](av.norm)
    else:
        try:
            doc, _ = rascunho.montar_doc(av, dados)
        except rascunho.PreviaIndisponivel:
            cab = rascunho._cabecalho(dados, av)
            if av.tipo == "REFORCO":
                ne_dummy = {
                    "chave": "160329000012026NE000000", "ug": "160329", "ne": "2026NE000000",
                    "data_emissao": str(date.today()), "doc_favorecido": ("00.000.000/0001-00", "EMPRESA FAVORECIDA EXEMPLO"),
                    "valor_atual": rascunho.ZERO, "itens": [], "derivados": {}, "tipo_empenho": "ORDINÁRIO",
                }
                norm = {
                    "cabecalho": cab, "ne": ne_dummy, "itens": [], "total": rascunho.ZERO,
                    "fonte": {"ug": "160329", "nc": "[[PREENCHER: NC]]", "nd": "[[PREENCHER: ND]]", "pi": "[[PREENCHER: PI]]", "ptres": "[[PREENCHER: PTRES]]", "ugr": "[[PREENCHER: UGR]]"},
                    "motivo": {"texto": "[[PREENCHER: motivo do reforço]]", "id": None, "detalhe": ""},
                }
                doc = textos.montar_reforco(norm)
            elif av.tipo == "ANULACAO":
                norm = {
                    "cabecalho": cab,
                    "empenhos": [{
                        "chave": "160329000012026NE000000", "ug": "160329", "ne": "2026NE000000",
                        "doc_favorecido": ("00.000.000/0001-00", "EMPRESA FAVORECIDA"),
                        "valor_atual": rascunho.ZERO,
                        "liquidado": rascunho.ZERO,
                        "itens_anulados": [],
                        "tipo": "TOTAL",
                        "valor_anular": rascunho.ZERO,
                        "itens": [],
                    }],
                    "total": rascunho.ZERO,
                    "reempenho": False,
                    "motivo": {"texto": "[[PREENCHER: motivo da anulação]]", "id": None, "detalhe": ""},
                }
                doc = textos.montar_anulacao(norm)
            else:
                doc, _ = rascunho.montar_doc(av, dados)

    nome = _nome_arquivo(doc.get("arquivo_base") or f"DIEx_{tipo_norm}_word")
    config.SAIDA.mkdir(parents=True, exist_ok=True)
    anx = _anexos_do_documento(av)
    try:
        docx_doc = docx_docs.gerar_docx_documento(doc, config.SAIDA / f"{nome}.docx", anexos=anx)
    except Exception as exc:
        log.warning("Falha ao gerar documento Word (.docx): %s", exc)
        raise HTTPException(500, f"Falha ao gerar arquivo Word (.docx): {exc}") from exc

    zip_doc = None
    if anx and docx_doc and docx_doc.is_file():
        try:
            import zipfile
            nome_zip = f"{nome}_word_com_anexos.zip"
            caminho_zip = config.SAIDA / nome_zip
            with zipfile.ZipFile(caminho_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(docx_doc, arcname=docx_doc.name)
                nomes_usados = {docx_doc.name}
                for rotulo, arq_anx in anx:
                    p_anx = Path(arq_anx)
                    if p_anx.is_file():
                        limpo_rotulo = re.sub(r"[^\w\-]+", "_", rotulo).strip("_")
                        nome_no_zip = f"{limpo_rotulo}{p_anx.suffix.lower()}"
                        contador = 1
                        while nome_no_zip in nomes_usados:
                            nome_no_zip = f"{limpo_rotulo}_{contador}{p_anx.suffix.lower()}"
                            contador += 1
                        nomes_usados.add(nome_no_zip)
                        zf.write(p_anx, arcname=nome_no_zip)
            zip_doc = caminho_zip
        except Exception as exc_zip:
            log.warning("Falha ao criar arquivo ZIP com anexos: %s", exc_zip)
            zip_doc = None

    return JSONResponse(content={
        "ok": True,
        "documento": docx_doc.name,
        "docx": docx_doc.name,
        "zip": zip_doc.name if zip_doc else None,
        "anexos": [r for r, _ in anx],
        "pendencias": not av.pode_gerar,
    })


def _caminho_pdf(nome: str) -> Path:
    """Resolve um PDF gerado: só nomes simples, só dentro de saida/ (sem traversal nem symlink para fora)."""
    if not re.fullmatch(r"[\w\-]+\.pdf", nome):
        raise HTTPException(400, "Nome de arquivo inválido.")
    caminho = (config.SAIDA / nome).resolve()
    if config.SAIDA.resolve() not in caminho.parents or not caminho.is_file():
        raise HTTPException(404, "Arquivo não encontrado.")
    return caminho


@app.get("/api/pdf/{nome}")
def api_pdf(nome: str) -> FileResponse:
    return FileResponse(_caminho_pdf(nome), media_type="application/pdf", filename=nome)


def _caminho_docx(nome: str) -> Path:
    """Resolve um DOCX gerado: só nomes simples, só dentro de saida/."""
    if not re.fullmatch(r"[\w\-]+\.docx", nome):
        raise HTTPException(400, "Nome de arquivo inválido.")
    caminho = (config.SAIDA / nome).resolve()
    if config.SAIDA.resolve() not in caminho.parents or not caminho.is_file():
        raise HTTPException(404, "Arquivo não encontrado.")
    return caminho


@app.get("/api/docx/{nome}")
def api_docx(nome: str) -> FileResponse:
    return FileResponse(_caminho_docx(nome),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        filename=nome)


def _caminho_zip(nome: str) -> Path:
    """Resolve um ZIP gerado: só nomes simples, só dentro de saida/."""
    if not re.fullmatch(r"[\w\-]+\.zip", nome):
        raise HTTPException(400, "Nome de arquivo inválido.")
    caminho = (config.SAIDA / nome).resolve()
    if config.SAIDA.resolve() not in caminho.parents or not caminho.is_file():
        raise HTTPException(404, "Arquivo não encontrado.")
    return caminho


@app.get("/api/zip/{nome}")
def api_zip(nome: str) -> FileResponse:
    return FileResponse(_caminho_zip(nome), media_type="application/zip", filename=nome)


@app.get("/api/previa/{nome}")
def api_previa(nome: str) -> dict:
    """Quantas páginas tem o PDF (a pré-visualização é feita por imagens, sem depender do leitor de PDF do navegador)."""
    with pymupdf.open(_caminho_pdf(nome)) as doc:
        return {"paginas": len(doc)}


@app.get("/api/previa/{nome}/{pagina}")
def api_previa_pagina(nome: str, pagina: int, dpi: int = 110) -> Response:
    with pymupdf.open(_caminho_pdf(nome)) as doc:
        if not 1 <= pagina <= len(doc):
            raise HTTPException(404, "Página inexistente.")
        png = doc[pagina - 1].get_pixmap(dpi=max(60, min(dpi, 160))).tobytes("png")
    return Response(png, media_type="image/png")


@app.get("/api/historico")
def api_historico() -> list[dict]:
    return historico.listar()


# ----------------------------------------------------------------------------- atualização dos dados
def _rodar_atualizacao() -> None:
    def registrar(msg: str) -> None:
        _atualizacao["log"].append(msg)

    try:
        _atualizacao["resultado"] = importadores.atualizar_tudo(registrar)
    except Exception as e:  # noqa: BLE001
        log.exception("Falha na atualização dos dados", exc_info=e)
        _atualizacao["resultado"] = {"erro": "Falha na atualização dos dados. Veja o log do servidor."}
    finally:
        base.invalidar()
        _atualizacao["rodando"] = False


@app.post("/api/atualizar")
def api_atualizar() -> dict:
    with _trava_atualizacao:
        if _atualizacao["rodando"]:
            return {"rodando": True, "mensagem": "Atualização já em andamento."}
        _atualizacao.update({"rodando": True, "log": [], "resultado": None, "inicio": time.time()})
    threading.Thread(target=_rodar_atualizacao, daemon=True).start()
    return {"rodando": True}


@app.get("/api/atualizar/status")
def api_atualizar_status() -> dict:
    return {"rodando": _atualizacao["rodando"], "log": _atualizacao["log"][-12:], "resultado": _atualizacao["resultado"],
            "status": base.status()}


# ----------------------------------------------------------------------------- interface estática
@app.get("/")
def raiz() -> HTMLResponse:
    html = (config.STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Content-Security-Policy": CSP_HTML})


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(config.STATIC / "img" / "brasao-bcms-64.png", media_type="image/png")


app.mount("/static", StaticFiles(directory=str(config.STATIC)), name="static")
