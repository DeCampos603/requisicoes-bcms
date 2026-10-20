# -*- coding: utf-8 -*-
"""API e geração de PDF ponta a ponta (repositório sintético)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import config, textos
from app.main import app

from conftest import docs_adesao, payload_requisicao, pdf_bytes
from test_regras import payload_anulacao, payload_reforco


@pytest.fixture()
def client(repo):
    from app import main
    return TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Reqemp-Token": main.TOKEN})


def _texto_pdf(caminho) -> str:
    doc = pymupdf.open(caminho)
    try:
        bruto = "\n".join(p.get_text() for p in doc)
        return " ".join(bruto.replace("\xa0", " ").split())
    finally:
        doc.close()


def test_config_e_estaticos(client):
    r = client.get("/api/config").json()
    assert {"tipos_unidade", "finalidades", "motivos_anulacao"} <= set(r["catalogos"])
    assert r["unidade"]["uasg"] == "160329"
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_pesquisa_de_pregao_cruza_numero_e_ugg(client):
    lista = client.get("/api/pregoes", params={"q": "90001"}).json()
    assert [p["ugg"] for p in lista] == ["160329"] and lista[0]["tipo_unidade"] == "GERENCIADORA"
    assert client.get("/api/pregoes", params={"q": "160450"}).json()[0]["tipo_unidade"] == "NAO_PARTICIPANTE"
    detalhe = client.get("/api/pregao", params={"id": lista[0]["id"]}).json()
    assert detalhe["n_itens"] == 4 and detalhe["nome_ugg"].startswith("BATALHÃO")
    assert client.get("/api/pregao", params={"id": "nao:existe"}).status_code == 404


def test_validar_devolve_achados_por_campo(client):
    r = client.post("/api/validar/requisicao", json={"hoje": "2026-10-02"}).json()
    campos = {a["campo"] for a in r["achados"] if a["nivel"] == "ERRO"}
    assert "cabecalho.diex" in campos and "cabecalho.nup" in campos and r["pode_gerar"] is False


def test_gerar_requisicao_gera_pdf_com_anexo_da_nc(client, repo):
    r = client.post("/api/gerar/requisicao", json=payload_requisicao())
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["ok"] and corpo["anexos"] == ["NC 2026NC000100"] and corpo["completo"]
    texto = _texto_pdf(config.SAIDA / corpo["documento"])
    for trecho in ("DIEx Requisitório nº 27-COSM/CMDO/BCMS", "NUP: 64619.004672/2026-56", "IG 12-02", "UG GERENCIADORA",
                   "Pregão nº 90001/2026", "TOTAL DO FORNECEDOR", "DESPACHO DO ORDENADOR DE DESPESAS", "FISCAL ADMINISTRATIVO"):
        assert trecho in texto, trecho
    assert "R$\xa01.000,00" in texto or "R$ 1.000,00" in texto
    completo = pymupdf.open(config.SAIDA / corpo["completo"])
    assert len(completo) == corpo["paginas_diex"] + 1  # + a NC
    # o download passa pela rota segura
    assert client.get(f"/api/pdf/{corpo['completo']}").headers["content-type"] == "application/pdf"
    assert client.get("/api/historico").json()[0]["diex"] == "27-COSM/CMDO/BCMS"


def test_gerar_recusa_documento_com_erros_ou_alerta_sem_ciencia(client):
    ruim = payload_requisicao()
    ruim["cabecalho"]["nup"] = "123"
    r = client.post("/api/gerar/requisicao", json=ruim)
    assert r.status_code == 422 and r.json()["n_erros"] >= 1
    com_alerta = payload_requisicao()
    com_alerta["fornecedores"][0]["itens"][0]["descricao"] = "PECA DIFERENTE DA ATA"
    r = client.post("/api/gerar/requisicao", json=com_alerta)
    assert r.status_code == 422 and r.json()["n_alertas_pendentes"] == 1
    chave = next(a["chave"] for a in r.json()["achados"] if a["nivel"] == "ALERTA")
    com_alerta["alertas_ciente"] = [chave]
    assert client.post("/api/gerar/requisicao", json=com_alerta).status_code == 200


def test_pdf_nao_serve_arquivo_fora_da_saida(client):
    assert client.get("/api/pdf/..%2F..%2Fsegredo.pdf").status_code in (400, 404)
    assert client.get("/api/pdf/inexistente.pdf").status_code == 404
    assert client.get("/api/pdf/arquivo.txt").status_code == 400


def test_gerar_reforco_e_anulacao(client):
    r = client.post("/api/gerar/reforco", json=payload_reforco())
    assert r.status_code == 200, r.text
    assert r.json()["anexos"] == ["NE 2026NE000100", "NC 2026NC000100"]
    texto = _texto_pdf(config.SAIDA / r.json()["documento"])
    assert "reforço do empenho nº 2026NE000100" in texto and "Solicitação de reforço de empenho." in texto
    r = client.post("/api/gerar/anulacao", json=payload_anulacao())
    assert r.status_code == 200, r.text
    texto = _texto_pdf(config.SAIDA / r.json()["documento"])
    assert "anulação parcial do empenho" in texto and "art. 28 do Decreto 93.872/1986" in texto and "VALOR A ANULAR" in texto


def test_upload_de_nc_rejeita_arquivo_que_nao_e_pdf(client):
    r = client.post("/api/nc/upload", data={"numero": "2026NC999999"}, files={"arquivo": ("x.pdf", b"nao e pdf", "application/pdf")})
    assert r.status_code == 422 and "PDF" in r.json()["erro"]
    r = client.post("/api/nc/upload", data={"numero": "2026NC999999"}, files={"arquivo": ("x.pdf", b"%PDF-1.4 lixo", "application/pdf")})
    assert r.status_code == 422
    r = client.post("/api/ne/upload", data={"numero": "x"}, files={"arquivo": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 422


def test_classificar_e_subitens(client):
    r = client.post("/api/classificar", json={"descricao": "PREGO COM CABEÇA, MATERIAL FERRO COMUM", "nd": "339030", "objeto": "MATERIAL"})
    assert r.status_code == 200
    if r.json()["disponivel"]:
        assert r.json()["recomendada"] == "24"
    assert client.post("/api/classificar", json={"descricao": "x", "nd": "abc"}).status_code == 422
    s = client.get("/api/nd/339030/subitens").json()
    if s["subitens"]:
        assert any(x["subitem"] == "39" for x in s["subitens"])
    assert client.get("/api/nd/339000/subitens").json()["indetalhada"] is True


# ----------------------------------------------------------------------------- textos
def test_texto_nao_participante_cita_artigos_da_adesao(repo):
    from app import regras
    p = payload_requisicao()
    p["licitacao"] = {"pregao_id": "160450:90008/2025:NAO_PARTICIPANTE"}
    p["adesao"] = {"autorizacao_data": "2026-09-20", "aceite_fornecedor": True, "ata_federal": True, "documentos": docs_adesao()}
    p["fornecedores"][0]["itens"][0]["descricao"] = "ITEM DE CARONA"
    av = regras.avaliar_requisicao(p)
    assert av.erros == []
    todos = " ".join(textos.montar_requisicao(av.norm)["paragrafos"])
    assert "art. 86, § 2º, da Lei 14.133/2021" in todos and "UG NÃO PARTICIPANTE" in todos and "20/09/2026" in todos


def test_assinatura_respondendo(repo):
    from app import regras
    p = payload_anulacao()
    p["cabecalho"]["requisitante"]["respondendo"] = True
    doc = textos.montar_anulacao(regras.avaliar_anulacao(p).norm)
    assert doc["assinatura"]["funcao"] == "Respondendo pela Chefia da Seção de Transporte"


# ----------------------------------------------------------------------------- upload com PDFs reais do SIAFI
_REAL = Path(__file__).resolve().parents[2] / "EMPENHOS"


def _real(sub: str, padrao: str) -> Path:
    achados = sorted((_REAL / sub).glob(padrao)) if (_REAL / sub).is_dir() else []
    if not achados:
        pytest.skip(f"PDF real indisponível: {sub}/{padrao}")
    return achados[0]


def test_upload_de_nc_real_entra_na_base_e_numero_errado_e_recusado(client, repo):
    pdf = _real("Notas de Crédito/160329", "NC_160035_2026NC400182_*.pdf")
    errado = client.post("/api/nc/upload", data={"numero": "2026NC400183"}, files={"arquivo": ("nc.pdf", pdf.read_bytes(), "application/pdf")})
    assert errado.status_code == 422 and "2026NC400182" in errado.json()["erro"]
    wrong_emit = client.post("/api/nc/upload", data={"numero": "2026NC400182", "emitente": "160999"}, files={"arquivo": ("nc.pdf", pdf.read_bytes(), "application/pdf")})
    assert wrong_emit.status_code == 422
    ok = client.post("/api/nc/upload", data={"numero": "2026NC400182"}, files={"arquivo": ("qualquer-nome.pdf", pdf.read_bytes(), "application/pdf")})
    assert ok.status_code == 200, ok.text
    cand = ok.json()["candidatas"][0]
    assert cand["emitente"] == "160035" and cand["arquivo_no_repositorio"] is True
    gravado = list(config.UPLOAD_NC_DIRS["160329"].glob("NC_160035_2026NC400182_v000_*.pdf"))
    assert len(gravado) == 1  # nome padrão do repositório, não o nome enviado pelo usuário


def test_upload_de_ne_real_e_validacao_da_ug(client, repo):
    pdf = _real("Empenhos/160329", "NE_160329_2026NE000277_*.pdf")
    r = client.post("/api/ne/upload", data={"numero": "2026NE000277", "ug": "167329"}, files={"arquivo": ("ne.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 422 and "UG 160329" in r.json()["erro"]
    r = client.post("/api/ne/upload", data={"numero": "2026NE000277", "ug": "160329"}, files={"arquivo": ("ne.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 200, r.text
    nes = client.get("/api/ne", params={"q": "2026NE000277"}).json()
    assert nes and nes[0]["chave"] == "2026NE000277" and nes[0]["nd"] == "339030"


# ----------------------------------------------------------------------------- pré-visualização por imagens e texto enxuto
def test_previa_devolve_paginas_como_png_e_nao_sai_de_saida(client):
    corpo = client.post("/api/gerar/requisicao", json=payload_requisicao()).json()
    nome = corpo["completo"]
    meta = client.get(f"/api/previa/{nome}").json()
    assert meta["paginas"] == corpo["paginas_diex"] + 1
    png = client.get(f"/api/previa/{nome}/1", params={"dpi": 90})
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content.startswith(b"\x89PNG")
    assert client.get(f"/api/previa/{nome}/{meta['paginas'] + 1}").status_code == 404
    assert client.get(f"/api/previa/{nome}/0").status_code == 404
    assert client.get("/api/previa/inexistente.pdf").status_code == 404
    assert client.get("/api/previa/arquivo.txt").status_code == 400


def test_requisicao_sem_citacoes_retiradas_a_pedido_do_usuario(client):
    r = client.post("/api/gerar/requisicao", json=payload_requisicao())
    texto = _texto_pdf(config.SAIDA / r.json()["documento"])
    for retirada in ("Os valores unitários são os registrados", "Lei 4.320/1964", "substituirá o instrumento de contrato",
                     "arts. 12, VII, e 18", "Painel de Capacidade de Empenho"):
        assert retirada not in texto, retirada
    assert "3. Tipo de Empenho: GLOBAL." in texto and "4. Contrato: NÃO." in texto
    assert "5. Classe/Grupo PCA 2026: 2540 - PECAS DE VIATURAS." in texto and "6. Nr da contratação PCA 2026: 160329-7/2026." in texto
    assert "art. 150 da Lei 14.133/2021" in texto and "IG 12-02" in texto  # as demais citações permanecem


# ----------------------------------------------------------------------------- QA: descrição enorme não derruba a geração
def test_descricao_longa_quebra_a_linha_da_tabela_em_vez_de_dar_erro_500(client):
    p = payload_requisicao()
    p["fornecedores"][0]["itens"][0]["descricao"] = ("PECA MECANICA ELETRICA VEICULO AUTOMOTIVO " * 70)[:2500].strip()
    r = client.post("/api/validar/requisicao", json=p).json()
    chave = next(a["chave"] for a in r["achados"] if a["codigo"] == "DESCRICAO_DIVERGE_ATA")
    p["alertas_ciente"] = [chave]
    resp = client.post("/api/gerar/requisicao", json=p)
    assert resp.status_code == 200, resp.text
    assert resp.json()["paginas_diex"] >= 2


# ----------------------------------------------------------------------------- adesão: upload e anexos no PDF final
def _carona_payload():
    p = payload_requisicao()
    p["licitacao"] = {"pregao_id": "160450:90008/2025:NAO_PARTICIPANTE"}
    p["adesao"] = {"autorizacao_data": "2026-09-20", "aceite_fornecedor": True, "ata_federal": True}
    p["fornecedores"][0]["itens"][0]["descricao"] = "ITEM DE CARONA"
    return p


def _enviar(client, tipo, conteudo=None, nome="doc.pdf"):
    return client.post("/api/adesao/upload", data={"tipo": tipo}, files={"arquivo": (nome, conteudo if conteudo is not None else pdf_bytes(tipo, 3), "application/pdf")})


def test_upload_de_documento_da_adesao(client, repo):
    conteudo = pdf_bytes("PESQUISA_PRECOS", 3)
    r = _enviar(client, "PESQUISA_PRECOS", conteudo)
    assert r.status_code == 200, r.text
    doc = r.json()["documento"]
    assert doc["tipo"] == "PESQUISA_PRECOS" and doc["paginas"] == 3 and len(doc["id"]) == 32
    assert _enviar(client, "PESQUISA_PRECOS", conteudo).json()["documento"]["id"] == doc["id"]  # mesmo arquivo, mesmo id (não duplica)
    assert len(list((config.ANEXOS / "adesao").glob("PESQUISA_PRECOS_*.pdf"))) == 1
    assert _enviar(client, "NAO_EXISTE").status_code == 422
    assert _enviar(client, "ACEITE_FORNECEDOR", b"isto nao e pdf").status_code == 422
    assert _enviar(client, "ACEITE_FORNECEDOR", b"%PDF-1.4 lixo").status_code == 422
    assert _enviar(client, "../../x").status_code == 422


def test_upload_remove_conteudo_ativo_do_documento_da_adesao(client, repo):
    import pymupdf
    ativo = pdf_bytes("termo", 1)
    d = pymupdf.open(stream=ativo, filetype="pdf")
    d.xref_set_key(d.pdf_catalog(), "OpenAction", r"<</S/JavaScript/JS(app.alert\(1\))>>")
    ativo = d.tobytes()
    d.close()
    assert b"/JavaScript" in ativo
    r = _enviar(client, "TERMO_JUSTIFICATIVA", ativo)
    assert r.status_code == 200, r.text
    gravado = next((config.ANEXOS / "adesao").glob("TERMO_JUSTIFICATIVA_*.pdf")).read_bytes()
    assert b"/JavaScript" not in gravado.replace(b"/JavaScript null", b"")


def test_requisicao_de_adesao_anexa_os_documentos_na_ordem_do_diex(client, repo):
    p = _carona_payload()
    sem = client.post("/api/gerar/requisicao", json=p)
    assert sem.status_code == 422 and sum(a["codigo"] == "ADESAO_DOC_AUSENTE" for a in sem.json()["achados"]) == 3
    p["adesao"]["documentos"] = {t: _enviar(client, t).json()["documento"]["id"]
                                 for t in ("TERMO_JUSTIFICATIVA", "PESQUISA_PRECOS", "ACEITE_FORNECEDOR", "AUTORIZACAO_GERENCIADORA")}
    r = client.post("/api/gerar/requisicao", json=p)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["anexos"] == ["Termo de Justificativa de Adesão", "Pesquisa de preços", "Aceite do fornecedor", "Autorização do órgão gerenciador", "NC 2026NC000100"]
    texto = _texto_pdf(config.SAIDA / corpo["documento"])
    assert "Anexos:" in texto and "1) Termo de Justificativa de Adesão." in texto and "3) Aceite do fornecedor." in texto
    assert "5) Cópia da Nota de Crédito." in texto
    completo = pymupdf.open(config.SAIDA / corpo["completo"])
    assert len(completo) == corpo["paginas_diex"] + 4 * 3 + 1  # DIEx + 4 documentos de 3 páginas + NC
    titulos = [t[1] for t in completo.get_toc()]
    assert titulos == ["DIEx", *corpo["anexos"]]


def test_anexo_que_nao_pode_ser_lido_impede_o_pdf_em_vez_de_omitir(client, repo):
    p = _carona_payload()
    p["adesao"]["documentos"] = {t: _enviar(client, t).json()["documento"]["id"] for t in ("TERMO_JUSTIFICATIVA", "PESQUISA_PRECOS", "ACEITE_FORNECEDOR")}
    for arq in (config.ANEXOS / "adesao").glob("ACEITE_FORNECEDOR_*.pdf"):
        arq.write_bytes(b"%PDF-1.4\nlixo sem estrutura")
    r = client.post("/api/gerar/requisicao", json=p)
    assert r.status_code == 422 and "Aceite do fornecedor" in r.json()["erro"]


def test_json_com_tipo_trocado_devolve_422_e_nao_500(client):
    for corpo in ({"fornecedores": "texto", "cabecalho": "x"}, {"cabecalho": {"requisitante": "x"}, "fonte": 7}, {"licitacao": [], "adesao": "x", "pca": 5}):
        for tipo in ("requisicao", "reforco", "anulacao"):
            r = client.post(f"/api/validar/{tipo}", json=corpo)
            assert r.status_code in (200, 422), (tipo, corpo, r.status_code, r.text[:200])


def test_modo_assinatura_digital_e_fisica(client, repo):
    p_dig = payload_requisicao()
    p_dig["cabecalho"]["modo_assinatura"] = "DIGITAL"
    r_dig = client.post("/api/gerar/requisicao", json=p_dig)
    assert r_dig.status_code == 200
    pdf_dig = pymupdf.open(config.SAIDA / r_dig.json()["documento"])
    txt_dig = " ".join(" ".join(p.get_text().split()) for p in pdf_dig)
    assert "Assinatura Digital" in txt_dig or "Lei nº 14.063/2020" in txt_dig
    metas = pdf_dig.metadata
    assert "Assinatura Digital" in metas.get("keywords", "")

    p_fis = payload_requisicao()
    p_fis["cabecalho"]["diex"] = "28-COSM/CMDO/BCMS"
    p_fis["cabecalho"]["modo_assinatura"] = "FISICA"
    r_fis = client.post("/api/gerar/requisicao", json=p_fis)
    assert r_fis.status_code == 200
    pdf_fis = pymupdf.open(config.SAIDA / r_fis.json()["documento"])
    txt_fis = " ".join(" ".join(p.get_text().split()) for p in pdf_fis)
    assert "Espaço para Assinatura Digital" not in txt_fis


def test_defasagem_status_e_calculo(client):
    st = client.get("/api/config").json()["status"]
    assert "defasagem" in st
    assert "defasado" in st["defasagem"]
    assert "dias_pregoes" in st["defasagem"]
    assert "dias_credito" in st["defasagem"]
    from app import base
    from datetime import date
    assert base.calcular_dias_defasagem("2026-09-01", date(2026, 9, 11)) == 10
    assert base.calcular_dias_defasagem("01/09/2026 10:00", date(2026, 9, 21)) == 20
    assert base.calcular_dias_defasagem(None) is None

