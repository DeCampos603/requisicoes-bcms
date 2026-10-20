# -*- coding: utf-8 -*-
"""Prévia ao vivo do DIEx: mesmo documento do PDF final, com pendências marcadas e sem gravar nada."""

from __future__ import annotations

import base64

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import config, pdf_docs, rascunho, regras, textos

from conftest import payload_requisicao
from test_regras import payload_anulacao, payload_reforco


@pytest.fixture()
def client(repo):
    from app import main
    return TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Reqemp-Token": main.TOKEN})


def _texto_png_pdf(r) -> str:
    assert r["ok"] and r["paginas"]
    assert r["paginas"][0].startswith("data:image/png;base64,")
    return ""


def test_formulario_vazio_gera_previa_com_tudo_pendente(client):
    r = client.post("/api/previa-viva/requisicao", json={}).json()
    assert r["ok"] and len(r["paginas"]) >= 1
    pend = {x.lower() for x in r["pendentes"]}  # "tipo de empenho" sai em maiúsculas no texto do DIEx
    for esperado in ("nº do diex", "nup", "objeto", "finalidade da aquisição", "nº da nc", "tipo de empenho", "cnpj/cpf do fornecedor"):
        assert esperado in pend, esperado
    png = base64.b64decode(r["paginas"][0].split(",", 1)[1])
    assert png.startswith(b"\x89PNG")


def test_previa_de_documento_valido_e_identica_ao_pdf_final_e_sem_pendencias(repo):
    p = payload_requisicao()
    av = regras.avaliar_requisicao(p)
    assert av.erros == []
    doc_previa, pendentes = rascunho.montar_doc(av, p)
    assert pendentes == []
    assert doc_previa == textos.montar_requisicao(av.norm)  # o que se vê na prévia é exatamente o que vai para o PDF


def test_marca_de_rascunho_so_na_previa(repo):
    p = payload_requisicao()
    av = regras.avaliar_requisicao(p)
    doc = textos.montar_requisicao(av.norm)
    import io
    prev, final = io.BytesIO(), io.BytesIO()
    pdf_docs.gerar_pdf_documento(doc, prev, rascunho=True)
    pdf_docs.gerar_pdf_documento(doc, final, rascunho=False)
    texto = lambda b: " ".join(pg.get_text() for pg in pymupdf.open(stream=b.getvalue(), filetype="pdf"))  # noqa: E731
    assert "RASCUNHO" in texto(prev) and "RASCUNHO" not in texto(final)


def test_pendente_digitado_pelo_usuario_nao_e_destacado_no_pdf_final_e_marcas_invisiveis_sao_removidas(repo):
    assert pdf_docs._preparar_marcas("a\u2063b\u2064c", False) == "abc"
    assert "\u2063[[PREENCHER: x]]\u2064" in pdf_docs._preparar_marcas("[[PREENCHER: x]]", True)
    assert pdf_docs._preparar_marcas("[[PREENCHER: x]]", False) == "[[PREENCHER: x]]"  # no PDF final é só texto


def test_previa_parcial_mostra_o_digitado_e_marca_o_que_falta(client):
    p = {"objeto": "MATERIAL", "cabecalho": {"diex": "27-COSM/CMDO/BCMS", "nup": ""}, "fornecedores": [
        {"cnpj": "29.381.249/0001-76", "nome": "", "itens": [{"item": 1, "descricao": "PECA TESTE", "qtde": "2", "valor_unit": "10,00"}]}]}
    r = client.post("/api/previa-viva/requisicao", json=p).json()
    assert r["ok"] and "NUP" in r["pendentes"] and "razão social" in r["pendentes"]
    assert "nº do DIEx" not in r["pendentes"] and "objeto" not in r["pendentes"]


def test_reforco_e_anulacao_exigem_a_ne_para_a_previa(client, repo):
    r = client.post("/api/previa-viva/reforco", json={}).json()
    assert r["ok"] is False and "NE" in r["motivo"]
    r = client.post("/api/previa-viva/anulacao", json={}).json()
    assert r["ok"] is False and "NE" in r["motivo"]
    ok = client.post("/api/previa-viva/reforco", json=payload_reforco(motivo={"id": ""})).json()
    assert ok["ok"] and "motivo do reforço" in ok["pendentes"]
    ok = client.post("/api/previa-viva/anulacao", json=payload_anulacao(motivo={"id": ""}, empenhos=[{"ug": "160329", "ne": "2026NE000100"}])).json()
    assert ok["ok"] and "motivo da anulação" in ok["pendentes"]


def test_previa_nunca_devolve_500_nem_grava_arquivos(client, repo):
    for corpo in ({"fornecedores": "x", "cabecalho": 5}, {"licitacao": [], "pca": "x", "fonte": 3}, {"cabecalho": {"requisitante": "x"}}):
        for tipo in ("requisicao", "reforco", "anulacao"):
            r = client.post(f"/api/previa-viva/{tipo}", json=corpo)
            assert r.status_code in (200, 422), (tipo, corpo, r.status_code)
    assert not list(config.SAIDA.glob("*")) if config.SAIDA.exists() else True
    assert client.get("/api/historico").json() == []


# ----------------------------------------------------------------------------- layout novo × modelo
def test_layout_novo_separa_1_1_e_agrupa_tipo_de_empenho_e_contrato(repo, monkeypatch):
    av = regras.avaliar_requisicao(payload_requisicao())
    novo = textos.montar_requisicao(av.norm)
    assert novo["layout"] == "novo" and novo["paragrafos"][1].startswith("1.1 A requisição importa a responsabilidade")
    assert [len(l) for l in novo["quadro"]] == [2, 2, 2] and novo["quadro"][0][0].startswith("3. Tipo de Empenho") and novo["quadro"][0][1].startswith("4. Contrato")
    assert novo["quadro"][1][1] is None and novo["quadro"][2][0].startswith("6. Nr da contratação PCA")  # PCA em linhas próprias
    with monkeypatch.context() as m:
        m.setattr(textos, "layout", lambda: "modelo")
        antigo = textos.montar_requisicao(av.norm)
    assert antigo["layout"] == "modelo" and antigo["quadro"] is None
    assert antigo["paragrafos"][0].endswith("(art. 13, caput, das IG 12-02).") and antigo["paragrafos"][-1].startswith("6. Nr da contratação PCA")


def test_pdf_do_layout_novo_mantem_todos_os_itens_do_modelo(client):
    r = client.post("/api/gerar/requisicao", json=payload_requisicao()).json()
    texto = " ".join(" ".join(pg.get_text() for pg in pymupdf.open(config.SAIDA / r["documento"])).replace("\xa0", " ").split())
    for trecho in ("1.1 A requisição importa a responsabilidade", "3. Tipo de Empenho: GLOBAL.", "4. Contrato: NÃO.",
                   "5. Classe/Grupo PCA 2026: 2540 - PECAS DE VIATURAS.", "6. Nr da contratação PCA 2026: 160329-7/2026.",
                   "2. Fonte de Recurso (indicação de crédito orçamentário, art. 150 da Lei 14.133/2021)"):
        assert trecho in texto, trecho


def test_adesao_numera_1_2(repo):
    from conftest import docs_adesao
    p = payload_requisicao()
    p["licitacao"] = {"pregao_id": "160450:90008/2025:NAO_PARTICIPANTE"}
    p["adesao"] = {"autorizacao_data": "2026-09-20", "aceite_fornecedor": True, "ata_federal": True, "documentos": docs_adesao()}
    p["fornecedores"][0]["itens"][0]["descricao"] = "ITEM DE CARONA"
    av = regras.avaliar_requisicao(p)
    pars = textos.montar_requisicao(av.norm)["paragrafos"]
    assert pars[2].startswith("1.2 Adesão à ata autorizada pelo órgão gerenciador")


def test_assinaturas_do_despacho_ficam_na_mesma_altura(repo):
    """Fiscal (texto curto) e Ordenador de Despesas (texto longo, nome em 2 linhas): data e assinatura alinhadas."""
    import io
    av = regras.avaliar_requisicao(payload_requisicao())
    for lay in ("novo", "modelo"):
        with pytest.MonkeyPatch.context() as m:
            m.setattr(textos, "layout", lambda lay=lay: lay)
            doc = textos.montar_requisicao(av.norm)
        buf = io.BytesIO()
        pdf_docs.gerar_pdf_documento(doc, buf)
        pdf = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
        achados = {}
        for pagina in pdf:
            datas = [w for w in pagina.get_text("words") if w[4] == "Janeiro-RJ,"]
            nomes = [w for w in pagina.get_text("words") if w[4] in ("LUIS", "WANDERCLEIDSON")]
            if len(datas) == 2 and len(nomes) == 2:
                achados = {"datas": sorted(round(w[1], 1) for w in datas), "nomes": sorted(round(w[1], 1) for w in nomes)}
        assert achados, lay
        assert achados["datas"][0] == achados["datas"][1], (lay, achados)   # a data ("Rio de Janeiro-RJ, ___/___/___") na mesma linha
        assert achados["nomes"][0] == achados["nomes"][1], (lay, achados)   # e o nome do assinante também
