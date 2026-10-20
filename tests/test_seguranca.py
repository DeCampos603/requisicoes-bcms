# -*- coding: utf-8 -*-
"""Endurecimento: CSRF/DNS rebinding, upload hostil, ReDoS, valores monetários absurdos, CPF mascarado."""

from __future__ import annotations

import io
import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import anexos, base, config, dinheiro, main, pdf_docs, regras, textos
from app.leitor_nc import RE_CELULA
from app.validadores import ErroFormato, mascarar_cpf, validar_diex

from conftest import payload_requisicao

REAL = Path(__file__).resolve().parents[2] / "EMPENHOS"
LOCAL = "http://127.0.0.1:8765"


@pytest.fixture()
def cliente_ok(repo):
    return TestClient(main.app, base_url=LOCAL, headers={"X-Reqemp-Token": main.TOKEN})


def _pdf(js: bool = False, paginas: int = 1) -> bytes:
    w = PdfWriter()
    for _ in range(paginas):
        w.add_blank_page(200, 200)
    if js:
        w.add_js("app.alert('x')")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _real(sub: str, padrao: str) -> bytes:
    achados = sorted((REAL / sub).glob(padrao)) if (REAL / sub).is_dir() else []
    if not achados:
        pytest.skip(f"PDF real indisponível: {sub}/{padrao}")
    return achados[0].read_bytes()


# ----------------------------------------------------------------------------- CSRF e DNS rebinding
def test_host_alheio_e_recusado_dns_rebinding(repo):
    c = TestClient(main.app, base_url="http://evil.example:8765")
    assert c.get("/api/config").status_code == 403
    assert c.get("/").status_code == 403


def test_post_sem_token_ou_de_outra_origem_e_recusado(cliente_ok, repo):
    sem_token = TestClient(main.app, base_url=LOCAL)
    assert sem_token.post("/api/validar/requisicao", json={}).status_code == 403
    assert sem_token.post("/api/atualizar").status_code == 403
    assert cliente_ok.post("/api/validar/requisicao", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert cliente_ok.post("/api/validar/requisicao", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert cliente_ok.post("/api/validar/requisicao", json={}, headers={"Origin": LOCAL, "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_upload_multipart_de_outra_origem_e_recusado(repo):
    c = TestClient(main.app, base_url=LOCAL)
    r = c.post("/api/nc/upload", data={"numero": "2026NC000001"}, files={"arquivo": ("x.pdf", _pdf(), "application/pdf")},
               headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_pagina_inicial_leva_token_csp_e_sem_docs(cliente_ok):
    r = cliente_ok.get("/")
    assert main.TOKEN in r.text and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    for rota in ("/api/docs", "/openapi.json", "/docs", "/api/nc", "/api/status", "/api/ne/detalhe"):
        assert cliente_ok.get(rota).status_code in (404, 405, 422), rota
    assert cliente_ok.get("/api/config").headers["cache-control"] == "no-store"


def test_corpo_json_gigante_e_recusado(cliente_ok):
    r = cliente_ok.post("/api/validar/requisicao", content=b"{" + b" " * (3 * 1024 * 1024) + b"}",
                        headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_erro_interno_nao_vaza_detalhe(repo, monkeypatch):
    def explode(_):
        raise RuntimeError("segredo-interno")

    monkeypatch.setitem(regras.AVALIADORES, "requisicao", explode)
    c = TestClient(main.app, base_url=LOCAL, headers={"X-Reqemp-Token": main.TOKEN}, raise_server_exceptions=False)
    r = c.post("/api/validar/requisicao", json={})
    assert r.status_code == 500 and "segredo-interno" not in r.text


# ----------------------------------------------------------------------------- upload hostil
def test_sanear_remove_javascript():
    com_js = _pdf(js=True)
    assert b"/JavaScript" in com_js
    limpo = anexos.sanear_pdf(com_js)
    # a chave pode restar como "/JavaScript null" (já anulada); o que não pode restar é ação com valor
    assert not anexos.MARCAS_ATIVAS.search(limpo) and b"alert(" not in limpo and limpo.startswith(b"%PDF-")
    assert anexos.MARCAS_ATIVAS.search(b"<</S/JavaScript/JS(app.alert(1))>>")  # o detector de fato detecta


def test_sanear_recusa_pdf_corrompido_e_gigante():
    for ruim in (b"%PDF-1.4 lixo", b"%PDF-"):
        with pytest.raises(anexos.UploadInvalido):
            anexos.sanear_pdf(ruim)
    with pytest.raises(anexos.UploadInvalido):
        anexos.sanear_pdf(_pdf(paginas=anexos.LIMITE_PAGINAS + 1))


def test_upload_acima_do_limite_e_recusado(cliente_ok, monkeypatch):
    monkeypatch.setattr(config, "TAMANHO_MAX_UPLOAD", 1000)
    r = cliente_ok.post("/api/nc/upload", data={"numero": "2026NC000001"},
                        files={"arquivo": ("x.pdf", b"%PDF-" + b"0" * 5000, "application/pdf")})
    assert r.status_code == 422 and "MB" in r.json()["erro"]


def test_upload_nao_substitui_nc_nem_ne_existente(cliente_ok, repo):
    nc = _real("Notas de Crédito/160329", "NC_160035_2026NC400182_*.pdf")
    ok = cliente_ok.post("/api/nc/upload", data={"numero": "2026NC400182"}, files={"arquivo": ("a.pdf", nc, "application/pdf")})
    assert ok.status_code == 200, ok.text
    de_novo = cliente_ok.post("/api/nc/upload", data={"numero": "2026NC400182"}, files={"arquivo": ("b.pdf", nc, "application/pdf")})
    assert de_novo.status_code == 409 and "não é substituída" in de_novo.json()["erro"]
    assert len(list(config.UPLOAD_NC_DIRS["160329"].glob("NC_160035_2026NC400182_*"))) == 1
    ne = _real("Empenhos/160329", "NE_160329_2026NE000277_*.pdf")
    primeiro = cliente_ok.post("/api/ne/upload", data={"numero": "2026NE000277", "ug": "160329"}, files={"arquivo": ("a.pdf", ne, "application/pdf")})
    assert primeiro.status_code == 200, primeiro.text
    segundo = cliente_ok.post("/api/ne/upload", data={"numero": "2026NE000277", "ug": "160329"}, files={"arquivo": ("b.pdf", ne, "application/pdf")})
    assert segundo.status_code == 409
    linhas = (config.DADOS / "uploads.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 2 and '"sha256_enviado"' in linhas[0]


def test_upload_de_nc_com_valor_diferente_do_dashboard_e_recusado(cliente_ok, repo):
    """O dashboard de crédito diz que a NC vale R$ 1,00; o PDF diz R$ 51.405,00: não é aceito."""
    nc = _real("Notas de Crédito/160329", "NC_160035_2026NC400182_*.pdf")
    arq = config.DADOS / "credito.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    d["ncs"]["160035:2026NC400182"] = {"emit": "160035", "chave": "2026NC400182", "dia": "05/02/2026", "op": "DESCENTRALIZACAO DE CREDITO", "obj": "",
                                       "favorecidas": {"160329": {"status": "x", "prov": 1.0, "emp": 0.0, "cred": 1.0, "celulas": []}}}
    arq.write_text(json.dumps(d), encoding="utf-8")
    base.invalidar()
    r = cliente_ok.post("/api/nc/upload", data={"numero": "2026NC400182"}, files={"arquivo": ("a.pdf", nc, "application/pdf")})
    assert r.status_code == 422 and "não confere com o dashboard" in r.json()["erro"]
    assert not list(config.UPLOAD_NC_DIRS["160329"].glob("NC_160035_2026NC400182_*"))


def test_gravacao_exclusiva_nunca_sobrescreve(tmp_path):
    a = anexos._gravar_exclusivo(tmp_path, "NC_x.pdf", b"um")
    b = anexos._gravar_exclusivo(tmp_path, "NC_x.pdf", b"dois")
    assert a != b and a.read_bytes() == b"um" and b.read_bytes() == b"dois"


def test_pdf_final_com_anexo_malicioso_sai_higienizado(repo, tmp_path):
    av = regras.avaliar_requisicao(payload_requisicao())
    doc = pdf_docs.gerar_pdf_documento(textos.montar_requisicao(av.norm), tmp_path / "d.pdf")
    mal = tmp_path / "anexo.pdf"
    mal.write_bytes(_pdf(js=True))
    final = anexos.montar_pdf_final(doc, [("NC x", mal)], tmp_path / "final.pdf")
    conteudo = final.read_bytes()
    assert not anexos.MARCAS_ATIVAS.search(conteudo) and b"alert(" not in conteudo and conteudo.startswith(b"%PDF-")


# ----------------------------------------------------------------------------- ReDoS e valores absurdos
def test_regex_de_celula_do_nc_e_linear():
    hostil = "1 232135 1000000000 339030 160329 " + ("A " * 40000)  # sem valor no final: o padrão antigo era quadrático
    t0 = time.perf_counter()
    assert RE_CELULA.search(hostil) is None
    assert time.perf_counter() - t0 < 1.0
    ok = RE_CELULA.search("1 232135 1000000000 339030 160329 SEG ALOEXE 1.234,56")
    assert ok and ok.group(6) == "SEG ALOEXE"
    assert RE_CELULA.search("1 232135 1000000000 339030 160329 - 10,00").group(6) == "-"


@pytest.mark.parametrize("valor", ["NaN", "Infinity", "-inf", "9" * 40, "1" * 20, "0," + "1" * 9, "1e400", {"x": 1}, [1]])
def test_decimais_absurdos_viram_erro_e_nao_excecao_inesperada(valor):
    with pytest.raises((dinheiro.ValorInvalido, ValueError, TypeError)):
        dinheiro.parse_decimal(valor)


def test_item_com_numeros_absurdos_vira_erro_de_validacao_e_nao_500(cliente_ok):
    p = payload_requisicao()
    p["fornecedores"][0]["itens"][0].update({"qtde": "9" * 30, "valor_unit": "NaN"})
    r = cliente_ok.post("/api/validar/requisicao", json=p)
    assert r.status_code == 200 and r.json()["pode_gerar"] is False
    p["fornecedores"][0]["itens"][0].update({"qtde": 1e300, "valor_unit": "1"})
    assert cliente_ok.post("/api/validar/requisicao", json=p).status_code == 200


def test_total_item_nao_estoura_o_contexto_decimal():
    assert dinheiro.total_item(Decimal("123456789012345.12345"), Decimal("123456789012345.1234")) > 0


def test_diex_com_tamanho_absurdo_e_recusado():
    with pytest.raises(ErroFormato):
        validar_diex("1-" + "A" * 400 + "/B")


# ----------------------------------------------------------------------------- privacidade
def test_cpf_e_mascarado_nas_listagens_e_no_diex(cliente_ok, repo):
    assert mascarar_cpf("529.982.247-25") == "***.982.247-**" and mascarar_cpf("52998224725") == "***.982.247-**"
    assert mascarar_cpf("29.381.249/0001-76") == "29.381.249/0001-76" and mascarar_cpf("160329") == "160329"
    arq = config.DADOS / "nes.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    d["nes"]["160329:2026NE000100"]["favorecido_codigo"] = "529.982.247-25"
    arq.write_text(json.dumps(d), encoding="utf-8")
    base.invalidar()
    lista = cliente_ok.get("/api/ne", params={"q": "2026NE000100"}).json()
    assert lista[0]["favorecido_codigo"] == "***.982.247-**" and "529.982" not in json.dumps(lista)
    av = regras.avaliar_anulacao({"hoje": "2026-10-02", "cabecalho": {"diex": "79-Transporte/CCAp/Cmdo", "nup": "64619.004207/2026-15", "data": "2026-10-02",
                                  "requisitante": {"posto": "1º Sgt", "nome": "Heric Joaquim Linhares", "funcao": "Chefe da Seção de Transporte"}},
                                  "empenhos": [{"ug": "160329", "ne": "2026NE000100", "tipo": "TOTAL", "liquidado": "0"}],
                                  "consultou_siafi": True, "motivo": {"id": "SALDO_NAO_UTILIZADO"}})
    doc = textos.montar_anulacao(av.norm)
    assert "***.982.247-**" in doc["tabelas"][0]["cabecalho"] and "529.982" not in doc["tabelas"][0]["cabecalho"]


def test_historico_nao_guarda_o_conteudo_do_formulario(cliente_ok, repo):
    r = cliente_ok.post("/api/gerar/requisicao", json=payload_requisicao())
    assert r.status_code == 200, r.text
    con = sqlite3.connect(config.DADOS / "historico.db")
    payload, achados = con.execute("SELECT payload, achados FROM documentos").fetchone()
    con.close()
    assert payload is None and "Fulano" not in (achados or "") and "29.381" not in (achados or "")
