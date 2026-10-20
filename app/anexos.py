# -*- coding: utf-8 -*-
"""Gestão de anexos: upload validado de NC/NE para o repositório e montagem do PDF final com os anexos.

Regra do sistema: o documento só é gerado se a NC (e, em reforço/anulação, a NE) existir no repositório.
Se não existir, o usuário envia o PDF; ele é conferido antes de ser gravado com o nome padrão do repositório
e de entrar no índice. Defesas aplicadas a todo PDF que chega:

  * só %PDF-, até 15 MB, até 300 páginas, sem senha;
  * conteúdo ativo (JavaScript, arquivos embutidos, links, formulários) é REMOVIDO antes de gravar e de mesclar;
  * é a NC/NE que o usuário disse que é (número, emitente/UG), e o valor da NC confere com o dashboard de crédito;
  * NUNCA substitui NC/NE que já existe no repositório (409): atualizar um documento oficial é ato manual na pasta;
  * o nome gravado nasce do conteúdo conferido (nunca do nome enviado), em criação exclusiva;
  * cada upload deixa uma linha em dados/uploads.jsonl (quando, o quê, sha256).
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pymupdf
from pypdf import PdfReader, PdfWriter

from . import base, config
from .leitor_nc import ler_nc
from .leitor_ne import completar_registro, ler_ne
from .validadores import ErroFormato, validar_nc, validar_ne

UGS_BCMS = ("160329", "167329")
LIMITE_PAGINAS = 300
# chave de ação/arquivo ativo AINDA com valor (o scrub deixa "/JavaScript null", que é a chave já anulada)
MARCAS_ATIVAS = re.compile(rb"/(?:JavaScript|JS|Launch|EmbeddedFile|RichMedia|SubmitForm|ImportData)\b(?!\s*null)")


class UploadInvalido(ValueError):
    """Arquivo enviado não serve (não é PDF, não é a NC/NE esperada, ilegível...)."""

    status = 422


class UploadConflito(UploadInvalido):
    """A NC/NE já existe no repositório: o upload não a substitui."""

    status = 409


def _checar_pdf(conteudo: bytes) -> None:
    if not conteudo:
        raise UploadInvalido("Arquivo vazio.")
    if len(conteudo) > config.TAMANHO_MAX_UPLOAD:
        raise UploadInvalido(f"Arquivo maior que {config.TAMANHO_MAX_UPLOAD // (1024 * 1024)} MB.")
    if not conteudo.startswith(b"%PDF-"):
        raise UploadInvalido("O arquivo enviado não é um PDF.")


def _remover_acoes_do_documento(doc) -> None:
    """O scrub limpa anotações e formulários; aqui saem as ações do catálogo (OpenAction, AA, Names/JavaScript) e das páginas."""
    raiz = doc.pdf_catalog()
    for chave in ("OpenAction", "AA"):
        doc.xref_set_key(raiz, chave, "null")
    tipo, valor = doc.xref_get_key(raiz, "Names")
    if tipo == "xref":
        doc.xref_set_key(int(valor.split()[0]), "JavaScript", "null")
    elif tipo == "dict":
        doc.xref_set_key(raiz, "Names/JavaScript", "null")
    for pagina in doc:
        doc.xref_set_key(pagina.xref, "AA", "null")


def sanear_pdf(conteudo: bytes) -> bytes:
    """Remove JavaScript, arquivos embutidos, links e formulários; recusa o que ainda restar de conteúdo ativo."""
    try:
        doc = pymupdf.open(stream=conteudo, filetype="pdf")
    except Exception as e:
        raise UploadInvalido("O arquivo não pôde ser lido como PDF.") from e
    try:
        if doc.needs_pass:
            raise UploadInvalido("PDF protegido por senha.")
        if len(doc) == 0 or len(doc) > LIMITE_PAGINAS:
            raise UploadInvalido(f"PDF com {len(doc)} página(s): fora do limite aceito (1 a {LIMITE_PAGINAS}).")
        doc.scrub(attached_files=True, clean_pages=False, embedded_files=True, hidden_text=False, javascript=True,
                  metadata=False, redactions=False, redact_images=0, remove_links=True, reset_fields=True,
                  reset_responses=True, thumbnails=True, xml_metadata=False)
        _remover_acoes_do_documento(doc)
        limpo = doc.tobytes(garbage=3, deflate=True)
    except UploadInvalido:
        raise
    except Exception as e:
        raise UploadInvalido("O PDF não pôde ser higienizado e foi recusado.") from e
    finally:
        doc.close()
    if MARCAS_ATIVAS.search(limpo):
        raise UploadInvalido("O PDF contém conteúdo ativo que não pôde ser removido e foi recusado.")
    return limpo


def _gravar_temp(conteudo: bytes) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(conteudo)
    tmp.close()
    return Path(tmp.name)


def _apagar(caminho: Path) -> None:
    """Remove o temporário; no Windows um PDF malformado pode manter o arquivo aberto — não é motivo de falha."""
    try:
        caminho.unlink(missing_ok=True)
    except OSError:
        pass


def _carimbo() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _gravar_exclusivo(diretorio: Path, nome: str, conteudo: bytes) -> Path:
    """Cria o arquivo sem jamais sobrescrever outro (dois uploads no mesmo segundo ganham sufixo)."""
    diretorio.mkdir(parents=True, exist_ok=True)
    for k in range(100):
        destino = diretorio / (nome if k == 0 else nome.replace(".pdf", f"_{k}.pdf"))
        try:
            with open(destino, "xb") as f:
                f.write(conteudo)
            return destino
        except FileExistsError:
            continue
    raise UploadInvalido("Não foi possível gravar o arquivo com nome único.")


def _auditar(tipo: str, chave: str, ug: str, destino: Path, bruto: bytes) -> None:
    linha = {"quando": datetime.now().isoformat(timespec="seconds"), "tipo": tipo, "chave": chave, "ug": ug,
             "arquivo": destino.name, "bytes_enviados": len(bruto), "sha256_enviado": hashlib.sha256(bruto).hexdigest()}
    config.DADOS.mkdir(parents=True, exist_ok=True)
    with config.ARQUIVO_LOCK, (config.DADOS / "uploads.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")


def _ler(leitor, tmp: Path, rotulo: str) -> dict:
    try:
        return leitor(tmp)
    except ValueError as e:  # mensagens nossas (limites, título ausente): seguras para o usuário
        raise UploadInvalido(f"Não foi possível ler o PDF como {rotulo} do SIAFI: {e}") from e
    except Exception as e:
        raise UploadInvalido(f"Não foi possível ler o PDF como {rotulo} do SIAFI.") from e


def salvar_nc(conteudo: bytes, chave_esperada: str, emitente_esperado: str | None = None) -> dict:
    """Valida e grava o PDF de uma NC. Devolve o registro indexado."""
    _checar_pdf(conteudo)
    try:
        esperada = validar_nc(chave_esperada)
    except ErroFormato as e:
        raise UploadInvalido(str(e)) from e
    limpo = sanear_pdf(conteudo)
    tmp = _gravar_temp(limpo)
    try:
        nc = _ler(ler_nc, tmp, "Nota de Crédito")
        if not nc.get("ug_emitente") or not nc.get("numero") or not nc.get("ano"):
            raise UploadInvalido("Não foi possível identificar o número e a UG emitente da NC no PDF.")
        chave_pdf = f"{nc['ano']}NC{int(nc['numero']):06d}"
        if chave_pdf != esperada:
            raise UploadInvalido(f"O PDF enviado é da NC {chave_pdf}, não da NC {esperada} informada.")
        if emitente_esperado and nc["ug_emitente"] != emitente_esperado:
            raise UploadInvalido(f"O PDF é de NC emitida pela UG {nc['ug_emitente']}, não pela UG {emitente_esperado}.")
        if (nc.get("status") or "") != "Contabilizada":
            raise UploadInvalido(f"A NC está com situação '{nc.get('status')}': só NC contabilizada pode ser anexada.")
        if nc.get("avisos"):
            raise UploadInvalido("O PDF da NC foi lido com divergências internas e foi recusado: " + " ".join(nc["avisos"][:2]))
        ugs = [i["ug_favorecida"] for i in nc["itens"] if i["ug_favorecida"] in UGS_BCMS]
        if not ugs:
            raise UploadInvalido("A NC não tem crédito para o BCMS (UG 160329 ou 167329).")
        existente = base.nc_pdf(nc["ug_emitente"], chave_pdf)
        if existente and existente.get("caminho") and Path(existente["caminho"]).is_file():
            raise UploadConflito(f"A NC {chave_pdf} (UG {nc['ug_emitente']}) já existe no repositório e não é substituída por upload. "
                                 f"Para atualizar um documento oficial, troque o arquivo direto na pasta e use \"Atualizar dados\".")
        # o valor do PDF tem de bater com o do dashboard de crédito (conferido: 156 de 156 NC que não são de anulação)
        cr = base.nc_credito(nc["ug_emitente"], chave_pdf)
        if cr and not (nc.get("tipo_nc") or "").startswith("Anulação"):
            for ug in set(ugs):
                fav = cr["favorecidas"].get(ug)
                if fav is None:
                    continue
                soma = sum((Decimal(i["valor"]) for i in nc["itens"] if i["ug_favorecida"] == ug), Decimal(0))
                if abs(soma - Decimal(str(fav["prov"]))) > Decimal("0.01"):
                    raise UploadInvalido(f"O valor da NC no PDF para a UG {ug} (R$ {soma}) não confere com o dashboard de crédito "
                                         f"(R$ {fav['prov']}). Confira se este é o documento certo.")
        ug_pasta = ugs[0]
        nome = f"NC_{nc['ug_emitente']}_{chave_pdf}_v{nc.get('versao') or '000'}_{_carimbo()}.pdf"
        destino = _gravar_exclusivo(config.UPLOAD_NC_DIRS[ug_pasta], nome, limpo)
        nc["arquivo"], nc["caminho"], nc["ug_pasta"], nc["chave"] = destino.name, str(destino), ug_pasta, chave_pdf
        base.registrar_nc(nc)
        _auditar("NC", chave_pdf, nc["ug_emitente"], destino, conteudo)
        return nc
    finally:
        _apagar(tmp)


def salvar_ne(conteudo: bytes, chave_esperada: str, ug_esperada: str | None = None) -> dict:
    """Valida e grava o PDF de uma NE ('Impressão Completa' do SIAFI)."""
    _checar_pdf(conteudo)
    try:
        esperada = validar_ne(chave_esperada)
    except ErroFormato as e:
        raise UploadInvalido(str(e)) from e
    limpo = sanear_pdf(conteudo)
    tmp = _gravar_temp(limpo)
    try:
        ne = _ler(ler_ne, tmp, "Nota de Empenho")
        if not ne.get("chave"):
            raise UploadInvalido("Não foi possível identificar o número da NE no PDF.")
        if ne["chave"] != esperada:
            raise UploadInvalido(f"O PDF enviado é da NE {ne['chave']}, não da NE {esperada} informada.")
        ug_pdf = ne.get("ug_emitente")
        ug = ug_esperada if ug_esperada in UGS_BCMS else (ug_pdf if ug_pdf in UGS_BCMS else None)
        if ug is None:
            raise UploadInvalido("A NE não é de UG do BCMS (160329 ou 167329): informe a UG emitente correta.")
        if ug_pdf and ug_pdf != ug:
            raise UploadInvalido(f"O PDF é de NE emitida pela UG {ug_pdf}, não pela UG {ug}.")
        if ne.get("avisos"):
            raise UploadInvalido("O PDF da NE foi lido com divergências: " + " ".join(ne["avisos"][:2]))
        existente = base.ne(ug, ne["chave"])
        if existente and existente.get("caminho") and Path(existente["caminho"]).is_file():
            raise UploadConflito(f"A NE {ne['chave']} (UG {ug}) já existe no repositório e não é substituída por upload. "
                                 f"Para atualizar um documento oficial, troque o arquivo direto na pasta e use \"Atualizar dados\".")
        favorecido = re.sub(r"\D", "", ne.get("favorecido_codigo") or "") or "0"
        nome = f"NE_{ug}_{ne['chave']}_v{ne.get('versao') or '001'}_{favorecido}_{_carimbo()}.pdf"
        destino = _gravar_exclusivo(config.UPLOAD_NE_DIRS[ug], nome, limpo)
        ne["arquivo"], ne["caminho"], ne["ug_pasta"], ne["ug_emitente"] = destino.name, str(destino), ug, ug
        completar_registro(ne)
        base.registrar_ne(ne)
        _auditar("NE", ne["chave"], ug, destino, conteudo)
        return ne
    finally:
        _apagar(tmp)


_ID_DOC_ADESAO = re.compile(r"[0-9a-f]{32}")


def _tipos_doc_adesao() -> dict[str, dict]:
    return {d["id"]: d for d in config.catalogos()["documentos_adesao"]}


def salvar_documento_adesao(conteudo: bytes, tipo: str) -> dict:
    """Grava um documento da instrução da adesão (termo de justificativa, pesquisa de preços, aceite do fornecedor...).

    Diferente de NC/NE, o conteúdo não é interpretado (pesquisa de preços e aceite costumam ser digitalizações): vale o
    mesmo funil de segurança (só PDF, tamanho/páginas limitados, conteúdo ativo removido). O nome nasce do hash do arquivo
    enviado — enviar o mesmo arquivo duas vezes não duplica nada — e o usuário só devolve o `id`."""
    tipos = _tipos_doc_adesao()
    if tipo not in tipos:
        raise UploadInvalido("Tipo de documento da adesão desconhecido.")
    _checar_pdf(conteudo)
    limpo = sanear_pdf(conteudo)
    with pymupdf.open(stream=limpo, filetype="pdf") as doc:
        paginas = len(doc)
    id_ = hashlib.sha256(conteudo).hexdigest()[:32]  # do arquivo enviado: a higienização do PyMuPDF não é determinística (muda o /ID do PDF)
    destino = config.ANEXOS / "adesao" / f"{tipo}_{id_}.pdf"
    if not destino.is_file():
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(destino, "xb") as f:
                f.write(limpo)
        except FileExistsError:  # gravado por outro pedido ao mesmo tempo: mesmo conteúdo
            pass
    _auditar(f"ADESAO_{tipo}", id_, "", destino, conteudo)
    return {"id": id_, "tipo": tipo, "paginas": paginas, "bytes": len(limpo), "rotulo": tipos[tipo]["rotulo"]}


def caminho_documento_adesao(tipo: str, id_: object) -> Path | None:
    """Caminho do documento enviado antes, ou None (id malformado, tipo desconhecido ou arquivo ausente). Nunca monta caminho com texto livre."""
    if tipo not in _tipos_doc_adesao() or not isinstance(id_, str) or not _ID_DOC_ADESAO.fullmatch(id_):
        return None
    caminho = config.ANEXOS / "adesao" / f"{tipo}_{id_}.pdf"
    return caminho if caminho.is_file() else None


def montar_pdf_final(pdf_documento: Path, anexos: list[tuple[str, Path]], destino: Path) -> Path:
    """Concatena o DIEx e os PDFs anexos (NC, NE, documentos da adesão) em um único arquivo, com marcadores e sem conteúdo ativo.

    O DIEx lista os anexos: se um deles não puder ser lido o PDF NÃO sai (listar um anexo que não está no arquivo seria falso)."""
    escritor = PdfWriter()
    leitor = PdfReader(str(pdf_documento))
    for pag in leitor.pages:
        escritor.add_page(pag)
    escritor.add_outline_item("DIEx", 0)
    for rotulo, caminho in anexos:
        try:
            r = PdfReader(str(caminho))
        except Exception as e:
            raise UploadInvalido(f"O anexo \"{rotulo}\" não pôde ser lido. Substitua o arquivo e gere de novo.") from e
        inicio = len(escritor.pages)
        for pag in r.pages:
            escritor.add_page(pag)
        escritor.add_outline_item(rotulo, inicio)
    escritor.add_metadata({"/Title": leitor.metadata.title if leitor.metadata else "DIEx", "/Author": config.unidade()["nome_om"]})
    destino.parent.mkdir(parents=True, exist_ok=True)
    import io
    buf = io.BytesIO()
    escritor.write(buf)
    # os anexos vêm do repositório (que outras pessoas alimentam): o PDF que sai para terceiros é higienizado
    destino.write_bytes(sanear_pdf(buf.getvalue()))
    return destino


def localizar_anexo_nc(fonte: dict) -> Path | None:
    """Localiza o PDF da Nota de Crédito de forma resiliente tanto localmente quanto no Docker/Cloud Run."""
    if not fonte:
        return None
    caminho = fonte.get("caminho") or fonte.get("arquivo")
    if caminho and Path(caminho).is_file():
        return Path(caminho)
    nome = Path(caminho).name if caminho else None
    ug = fonte.get("ug") or fonte.get("ug_pasta") or "160329"
    chave = fonte.get("nc")

    pastas = [
        config.DADOS_NC_DIRS.get(ug),
        config.NC_DIRS.get(ug),
        config.UPLOAD_NC_DIRS.get(ug),
        config.ANEXOS / "nc" / ug,
    ]
    outra_ug = "167329" if ug == "160329" else "160329"
    pastas.extend([
        config.DADOS_NC_DIRS.get(outra_ug),
        config.NC_DIRS.get(outra_ug),
        config.UPLOAD_NC_DIRS.get(outra_ug),
    ])

    for pasta in pastas:
        if not pasta or not pasta.is_dir():
            continue
        if nome:
            p = pasta / nome
            if p.is_file():
                return p
        if chave:
            matches = sorted(pasta.glob(f"*{chave}*.pdf"), reverse=True)
            if matches:
                return matches[0]
    return None


def localizar_anexo_ne(ne_dict: dict) -> Path | None:
    """Localiza o PDF da Nota de Empenho de forma resiliente tanto localmente quanto no Docker/Cloud Run."""
    if not ne_dict:
        return None
    caminho = ne_dict.get("caminho") or ne_dict.get("arquivo")
    if caminho and Path(caminho).is_file():
        return Path(caminho)
    nome = Path(caminho).name if caminho else None
    ug = ne_dict.get("ug") or ne_dict.get("ug_pasta") or "160329"
    chave = ne_dict.get("chave") or ne_dict.get("ne")

    pastas = [
        config.DADOS_NE_DIRS.get(ug),
        config.NE_DIRS.get(ug),
        config.UPLOAD_NE_DIRS.get(ug),
        config.ANEXOS / "ne" / ug,
    ]
    outra_ug = "167329" if ug == "160329" else "160329"
    pastas.extend([
        config.DADOS_NE_DIRS.get(outra_ug),
        config.NE_DIRS.get(outra_ug),
        config.UPLOAD_NE_DIRS.get(outra_ug),
    ])

    for pasta in pastas:
        if not pasta or not pasta.is_dir():
            continue
        if nome:
            p = pasta / nome
            if p.is_file():
                return p
        if chave:
            matches = sorted(pasta.glob(f"*{chave}*.pdf"), reverse=True)
            if matches:
                return matches[0]
    return None

