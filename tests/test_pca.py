# -*- coding: utf-8 -*-
"""PCA do BCMS: consulta local, conferência do nº/classe informados e sugestão de preenchimento (sem rede)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import pca, regras

from conftest import codigos, payload_requisicao, pca_sintetico


def _payload(contratacao="160329-7/2025", classe="2540 - PECAS DE VIATURAS"):
    return payload_requisicao(pca={"consta": True, "classe": classe, "contratacao": contratacao})


@pytest.fixture()
def client(repo):
    from app import main
    return TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Reqemp-Token": main.TOKEN})


def test_normalizar_agrupa_linhas_por_contratacao(repo):
    pca_sintetico(repo["dados"])
    c7 = pca.contratacao(2025, 7)
    assert c7["codigo"] == "160329-7/2025" and c7["n_itens"] == 2 and c7["classes"] == [{"codigo": "2540", "nome": "PECAS DE VIATURAS"}]
    assert pca.contratacao(2025, 99) is None and pca.anos_disponiveis() == [2025]  # 2026 sem dados não conta


def test_sugestao_ranqueia_pelo_item_do_catalogo_e_cai_para_o_ano_anterior(repo):
    pca_sintetico(repo["dados"])
    r = pca.sugerir(2026, [{"tipo": "M", "codigo": "123456", "descricao": "PECA MECANICA PARA VIATURA", "valor": 1000}], "Chefe da Seção de Manutenção", "manutenção de viaturas")
    assert r["ano_usado"] == 2025 and "2025" in r["aviso"]
    assert r["classes"][0]["rotulo"] == "2540 - PECAS DE VIATURAS"
    assert [s["codigo"] for s in r["sugestoes"]][0] == "160329-7/2025"
    assert r["sugestoes"][0]["score"] > r["sugestoes"][-1]["score"] or len(r["sugestoes"]) == 1


def test_palavra_solta_nao_vira_sugestao(repo):
    pca_sintetico(repo["dados"])
    # classe e itens que não existem no PCA, com uma palavra genérica em comum ("comum"/"material"): nada a sugerir
    r = pca.sugerir(2025, [{"tipo": "M", "codigo": "999999", "descricao": "CIMENTO PORTLAND, TIPO COMUM", "valor": 10}], None, None, rede=False)
    assert r["sugestoes"] == []
    # duas palavras do objeto já bastam
    r = pca.sugerir(2025, [{"tipo": "M", "codigo": "999999", "descricao": "CANETA ESFEROGRAFICA PARA ESCRITORIO", "valor": 10}], None, None, rede=False)
    assert [s["codigo"] for s in r["sugestoes"]] == ["160329-12/2025"]


def test_sem_pca_importado_so_avisa(repo):
    r = pca.sugerir(2026, [{"tipo": "M", "codigo": "123456"}])
    assert r["disponivel"] is False and "Atualizar" in r["aviso"] and r["sugestoes"] == []


def test_requisicao_confere_numero_e_classe_do_pca(repo):
    pca_sintetico(repo["dados"])
    av = regras.avaliar_requisicao(_payload())
    assert av.erros == [] and "PCA_CONTRATACAO_OK" in codigos(av, "INFO") and "PCA_CLASSE_DIVERGE_ITEM" not in codigos(av)
    sug = av.norm["resumo"]["pca"]
    assert sug["classes"][0]["codigo"] == "2540" and sug["sugestoes"][0]["codigo"] == "160329-7/2025" and sug["contratacao"]["codigo"] == "160329-7/2025"


def test_alertas_de_pca(repo):
    pca_sintetico(repo["dados"])
    assert "PCA_CONTRATACAO_INEXISTENTE" in codigos(regras.avaliar_requisicao(_payload("160329-99/2025")), "ALERTA")
    assert "PCA_UASG_DIFERENTE" in codigos(regras.avaliar_requisicao(_payload("160238-7/2025")), "ALERTA")
    assert "PCA_CLASSE_DIVERGE_ITEM" in codigos(regras.avaliar_requisicao(_payload(classe="7510 - ARTIGOS PARA ESCRITORIO")), "ALERTA")


def test_ano_sem_dados_nao_bloqueia(repo):
    pca_sintetico(repo["dados"])
    av = regras.avaliar_requisicao(_payload("160329-7/2026"))  # 2026 ainda não publicado para o BCMS
    assert av.erros == [] and "PCA_ANO_SEM_DADOS" in codigos(av, "INFO")
    assert av.norm["resumo"]["pca"]["ano_usado"] == 2025


def test_rede_indisponivel_entra_em_pausa(repo, sem_rede):
    pca_sintetico(repo["dados"], catalogo={})  # nada no cache: precisaria consultar a API
    assert pca.classe_do_catalogo("M", "999001", rede=True) is None
    assert sem_rede.chamadas == 1
    assert pca.classe_do_catalogo("M", "999002", rede=True) is None
    assert sem_rede.chamadas == 1  # em pausa: não tenta de novo por alguns minutos
    av = regras.avaliar_requisicao(_payload())  # e a validação segue sem travar
    assert av.erros == [] and sem_rede.chamadas == 1


def test_classe_consultada_na_api_entra_no_cache(repo, monkeypatch):
    pca_sintetico(repo["dados"], catalogo={})
    monkeypatch.setattr(pca, "_get_json", lambda caminho, params, timeout=60, tentativas=True: {"resultado": [{"codigoClasse": 2540, "nomeClasse": "PECAS DE VIATURAS", "codigoGrupo": 25, "nomeGrupo": "VEICULOS", "descricaoItem": "X"}]})
    assert pca.classe_do_catalogo("M", "123456", rede=True) == {"codigo": "2540", "nome": "PECAS DE VIATURAS"}
    monkeypatch.setattr(pca, "_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("deveria vir do cache")))
    assert pca.classe_do_catalogo("M", "123456", rede=True)["codigo"] == "2540"


def test_api_pca_lista_e_pesquisa(client, repo):
    pca_sintetico(repo["dados"])
    r = client.get("/api/pca", params={"ano": 2026}).json()
    assert r["ano_usado"] == 2025 and r["aviso"] and {c["codigo"] for c in r["contratacoes"]} == {"160329-7/2025", "160329-12/2025"}
    achou = client.get("/api/pca", params={"ano": 2025, "q": "escritorio"}).json()
    assert [c["codigo"] for c in achou["contratacoes"]] == ["160329-12/2025"] and achou["aviso"] is None
    assert client.get("/api/pca", params={"ano": 1900}).status_code == 422
