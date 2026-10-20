# -*- coding: utf-8 -*-
"""Testes das novas funcionalidades:
- Inexigibilidade de licitação (Lei 14.133/2021, art. 74)
- Objetos temáticos e categorias dos pregões na busca e seleção
- Alteração e persistência de autoridades (OD e Fiscal) com novo posto Cel
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from fastapi.testclient import TestClient

from app import base, config, main, regras, textos, validadores
from conftest import payload_requisicao


def test_ordenador_despesas_posto_cel():
    """O Ordenador de Despesas agora tem posto Cel."""
    unid = config.unidade()
    od = unid["ordenador_despesas"]
    assert od["posto"] == "Cel"
    assert "WANDERCLEIDSON" in od["nome"]


def test_validar_numero_inexigibilidade():
    """Valida numeração de inexigibilidade (N/AAAA)."""
    assert validadores.validar_numero_licitacao("5/2026", "INEXIGIBILIDADE", 2026) == "5/2026"
    assert validadores.validar_numero_licitacao(" 12 / 2025 ", "INEXIGIBILIDADE", 2026) == "12/2025"

    import pytest
    with pytest.raises(validadores.ErroFormato, match="não pode ser zero"):
        validadores.validar_numero_licitacao("0/2026", "INEXIGIBILIDADE", 2026)


def test_inexigibilidade_avaliacao_e_textos(repo):
    """Avaliação completa de requisição com modalidade INEXIGIBILIDADE (art. 74)."""
    dados = payload_requisicao(
        modalidade="INEXIGIBILIDADE",
        licitacao={
            "numero": "3/2026",
            "fundamento_inexigibilidade": "ART74_I",
            "tipo_unidade": "GERENCIADORA",
        },
        contrato={"possui": False, "hipotese": "ART95_II"},
    )
    av = regras.avaliar_requisicao(dados)
    assert not av.erros, f"Erros inesperados: {av.erros}"
    assert av.norm["licitacao"]["modalidade"] == "INEXIGIBILIDADE"
    assert av.norm["licitacao"]["fundamento"]["id"] == "ART74_I"

    # Verificação da redação gerada no DIEx
    req_texto = textos.montar_requisicao(av.norm)
    corpo = " ".join(req_texto["paragrafos"])
    assert "Inexigibilidade nº 3/2026" in corpo
    assert "art. 74, I, da Lei 14.133/2021" in corpo
    assert "art. 72 da Lei 14.133/2021" in corpo


def test_objetos_e_categorias_pregoes():
    """Pregões trazem objeto sintetizado e categorias, e a busca filtra por tema."""
    pregoes = base.buscar_pregoes("", limite=100)
    assert len(pregoes) > 0

    # Todo pregão tem objeto e categorias
    for p in pregoes:
        assert "objeto" in p and p["objeto"]
        assert "categorias" in p and isinstance(p["categorias"], list)

    # Busca por viaturas encontra pregões de viaturas
    viaturas = base.buscar_pregoes("viatura")
    assert len(viaturas) > 0
    assert any("Viaturas" in p["objeto"] for p in viaturas)

    # Busca por saúde encontra pregões de saúde
    saude = base.buscar_pregoes("saude")
    assert len(saude) > 0
    assert any("Saúde" in p["objeto"] for p in saude)

    # Busca por construção encontra pregões de construção
    construcao = base.buscar_pregoes("construcao")
    assert len(construcao) > 0
    assert any("Construção" in p["objeto"] for p in construcao)


def test_endpoint_salvar_autoridades(repo):
    """Testa atualização de autoridades via API POST /api/config/autoridades."""
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Reqemp-Token": main.TOKEN})

    res_index = client.get("/")
    assert res_index.status_code == 200

    # Atualiza mantendo dados válidos
    novo_payload = {
        "fiscal_administrativo": {
            "posto": "Cap",
            "nome": "LUIS HENRIQUE ARAÚJO FOWLER",
            "funcao": "Fiscal Administrativo",
        },
        "ordenador_despesas": {
            "posto": "Cel",
            "nome": "WANDERCLEIDSON DA SILVA RODRIGUES",
            "funcao": "Ordenador de Despesas",
        },
    }

    res = client.post("/api/config/autoridades", json=novo_payload)
    assert res.status_code == 200, res.text
    dados = res.json()
    assert dados["ok"] is True
    assert dados["od"]["posto"] == "Cel"
    assert dados["od"]["nome"] == "WANDERCLEIDSON DA SILVA RODRIGUES"

    # Confere na rota de configuração
    res_cfg = client.get("/api/config")
    cfg = res_cfg.json()
    assert cfg["unidade"]["od"]["posto"] == "Cel"
    assert "fundamentos_inexigibilidade" in cfg["catalogos"]
    assert len(cfg["catalogos"]["fundamentos_inexigibilidade"]) >= 5


def test_busca_nes_todas_e_filtros_ug():
    """Testa consulta completa de NEs, suporte a ambas as UGs e busca flexível."""
    # Busca vazia deve retornar todas as NEs da base (390+ NEs)
    todas = base.buscar_nes("", limite=500)
    assert len(todas) >= 390

    # Filtro específico por UG 160329 (OGU)
    ogu = base.buscar_nes("", limite=500, ug="160329")
    assert len(ogu) >= 285
    assert all(n["ug"] == "160329" for n in ogu)

    # Filtro específico por UG 167329 (FEx)
    fex = base.buscar_nes("", limite=500, ug="167329")
    assert len(fex) >= 105
    assert all(n["ug"] == "167329" for n in fex)

    # Busca textual por 'FEx' ou '167329' deve encontrar empenhos daquela UG
    busca_fex = base.buscar_nes("FEx")
    assert len(busca_fex) >= 105
    assert all(n["ug"] == "167329" for n in busca_fex)

    busca_cod = base.buscar_nes("167329")
    assert len(busca_cod) >= 105

    # Busca por número direto (ex: 277, 2026ne277 ou NE 277)
    busca_277 = base.buscar_nes("277")
    assert len(busca_277) > 0
    assert busca_277[0]["chave"] == "2026NE000277"

    busca_ne277 = base.buscar_nes("2026ne277")
    assert len(busca_ne277) > 0
    assert busca_ne277[0]["chave"] == "2026NE000277"


def test_api_ne_com_limite_e_filtro_ug():
    """Testa endpoint GET /api/ne com parâmetros q, ug e limite."""
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Reqemp-Token": main.TOKEN})

    # Consulta padrão sem filtros retorna todas as NEs (limite default 500)
    res = client.get("/api/ne")
    assert res.status_code == 200
    dados = res.json()
    assert len(dados) >= 390

    # Filtro por UG via query string
    res_fex = client.get("/api/ne?ug=167329")
    assert res_fex.status_code == 200
    dados_fex = res_fex.json()
    assert len(dados_fex) >= 105
    assert all(d["ug"] == "167329" for d in dados_fex)

    # Limite respeitado
    res_lim = client.get("/api/ne?limite=10")
    assert res_lim.status_code == 200
    assert len(res_lim.json()) == 10


def test_listar_empresas_nes():
    """Testa listagem consolidada de empresas com NE na base."""
    todas = base.listar_empresas_nes()
    assert len(todas) >= 180

    # Verifica estrutura
    tamandare = next((e for e in todas if "TAMANDARE" in e["nome"]), None)
    assert tamandare is not None
    assert "00.162.720/0001-53" in tamandare["codigo"]
    assert tamandare["total_nes"] >= 1
    assert "167329" in tamandare["ugs"]

    # Filtro por UG
    fex_emp = base.listar_empresas_nes(ug="167329")
    assert len(fex_emp) > 0
    assert all("167329" in e["ugs"] for e in fex_emp)


def test_buscar_nes_por_empresa():
    """Testa busca de NE filtrando ou pesquisando por empresa/favorecido."""
    # 1. Filtro estrito por empresa (nome ou CNPJ)
    nes_tamandare = base.buscar_nes(empresa="TAMANDARE")
    assert len(nes_tamandare) == 1
    assert "TAMANDARE" in nes_tamandare[0]["favorecido_nome"]

    nes_cnpj = base.buscar_nes(empresa="00.162.720/0001-53")
    assert len(nes_cnpj) == 1
    assert "TAMANDARE" in nes_cnpj[0]["favorecido_nome"]

    # 2. Busca textual direta por nome da empresa
    busca_textual = base.buscar_nes("TAMANDARE")
    assert len(busca_textual) >= 1
    assert "TAMANDARE" in busca_textual[0]["favorecido_nome"]


def test_buscar_pregao_por_empresa():
    """Testa busca de pregão pelo nome ou CNPJ de empresa fornecedora da ata."""
    # Busca por fornecedor de item da ata
    pregoes = base.buscar_pregoes("GUIMARAES")
    assert len(pregoes) > 0

    pregoes_cnpj = base.buscar_pregoes("60.682.532/0001-88")
    assert len(pregoes_cnpj) > 0


def test_api_empresas_e_filtro_empresa():
    """Testa rota GET /api/ne/empresas e filtro empresa na rota GET /api/ne."""
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Reqemp-Token": main.TOKEN})

    res_emp = client.get("/api/ne/empresas")
    assert res_emp.status_code == 200
    empresas = res_emp.json()
    assert len(empresas) >= 180

    # Consulta com parâmetro empresa
    res_ne = client.get("/api/ne?empresa=TAMANDARE")
    assert res_ne.status_code == 200
    dados = res_ne.json()
    assert len(dados) == 1
    assert "TAMANDARE" in dados[0]["favorecido_nome"]


def test_detalhamento_nd_candidatas_disponiveis():
    """Garante que a lista de NDs candidatas ao detalhamento de ND 00 nunca fique vazia."""
    from app import classificacao

    cands_mat = classificacao.nds_candidatas("339000", "MATERIAL")
    assert len(cands_mat) >= 1
    nds_mat = [c["nd"] for c in cands_mat]
    assert "339030" in nds_mat

    cands_serv = classificacao.nds_candidatas("339000", "SERVICO")
    assert len(cands_serv) >= 1
    nds_serv = [c["nd"] for c in cands_serv]
    assert "339039" in nds_serv or "339036" in nds_serv

    cands_invest = classificacao.nds_candidatas("449000", "MATERIAL")
    assert len(cands_invest) >= 1
    assert "449052" in [c["nd"] for c in cands_invest]


def test_modalidade_contrato_avaliacao_e_textos(repo):
    """Avaliação completa de requisição com modalidade CONTRATO (Lei 14.133/2021, art. 89)."""
    dados = payload_requisicao(
        modalidade="CONTRATO",
        licitacao={
            "numero": "05/2024",
            "objeto": "Prestação de serviços de telefonia móvel e fixa",
            "processo_origem": "64041.001234/2024-56",
        },
        contrato={"possui": True, "numero": "05/2024"},
    )
    av = regras.avaliar_requisicao(dados)
    assert not av.erros, f"Erros inesperados: {av.erros}"
    assert av.norm["licitacao"]["modalidade"] == "CONTRATO"
    assert av.norm["licitacao"]["numero"] == "05/2024"
    assert av.norm["contrato"]["possui"] is True
    assert av.norm["contrato"]["numero"] == "05/2024"

    # Verificação da redação gerada no DIEx
    req_texto = textos.montar_requisicao(av.norm)
    corpo = " ".join(req_texto["paragrafos"])
    assert "Contrato nº 05/2024" in corpo
    assert "art. 89 da Lei 14.133/2021" in corpo
    assert "64041.001234/2024-56" in corpo


def test_api_contratos():
    """Testa endpoint GET /api/contratos e base de contratos vigentes."""
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Reqemp-Token": main.TOKEN})

    res = client.get("/api/contratos")
    assert res.status_code == 200
    contratos = res.json()
    assert len(contratos) >= 1
    c05 = next((c for c in contratos if c["numero"] == "05/2024"), None)
    assert c05 is not None
    assert "telefonia" in c05["objeto_resumo"].lower()
    assert "IVM" in c05["fornecedor_nome"]

    # Busca com termo
    res_busca = client.get("/api/contratos?q=telefonia")
    assert res_busca.status_code == 200
    assert len(res_busca.json()) >= 1

    # Resumo
    res_resumo = client.get("/api/contratos/resumo")
    assert res_resumo.status_code == 200
    dados_resumo = res_resumo.json()
    assert dados_resumo["total"] == 24
    assert "6.275.335,94" in dados_resumo["total_valor_global_brl"]

    # Detalhe por número
    res_det = client.get("/api/contrato?numero=05/2024")
    assert res_det.status_code == 200
    assert res_det.json()["fornecedor_cnpj"] == "10.285.037/0001-67"


def test_quantidade_fracionada_6_casas_decimais_calculo_inverso(repo):
    """Testa suporte a 6 casas decimais na quantidade (SIAFI) e cálculo inverso exato."""
    from app.dinheiro import parse_decimal, total_item

    # Simulação real do usuário: Total R$ 65.000,00 e Unitário R$ 69.673,26
    # 65000 / 69673.26 = 0.932926073... -> 0,932926
    qtde = parse_decimal("0,932926")
    vu = parse_decimal("69673,26")
    tot = total_item(qtde, vu)
    assert tot == Decimal("65000.00")

    # Avaliação de requisição com quantidade de 6 casas decimais não gera erro
    dados = payload_requisicao(
        fornecedores=[{
            "cnpj": "29.381.249/0001-76",
            "nome": "FORNECEDOR TESTE LTDA",
            "itens": [{
                "item": "1",
                "si": "39",
                "descricao": "SERVICOS DE FORNECIMENTO DE AGUA TRATADA E TRATAMENTO DE ESGOTO",
                "und": "M3",
                "qtde": "0,932926",
                "valor_unit": "69673,26",
            }],
        }]
    )
    av = regras.avaliar_requisicao(dados)
    erros_qtde = [e for e in av.erros if e.codigo == "QTDE_CASAS"]
    assert len(erros_qtde) == 0
    assert av.norm["total"] == Decimal("65000.00")


def test_contratacao_18_pca_agua_e_saneamento(repo):
    """Testa que a contratação 18 do PCA (Água e Esgoto) é validada com sucesso."""
    import shutil
    shutil.copy("dados/pca.json", repo["dados"] / "pca.json")

    dados_2025 = payload_requisicao(
        pca={"consta": True, "classe": "692 - SERVIÇOS DE DISTRIBUIÇÃO DE ÁGUA ATRAVÉS DE TUBULAÇÃO", "contratacao": "160329-18/2025"}
    )
    av_2025 = regras.avaliar_requisicao(dados_2025)
    codigos_2025 = [a.codigo for a in av_2025.achados]
    assert "PCA_CONTRATACAO_OK" in codigos_2025
    assert "PCA_CONTRATACAO_INEXISTENTE" not in codigos_2025

    dados_2026 = payload_requisicao(
        pca={"consta": True, "classe": "692 - SERVIÇOS DE DISTRIBUIÇÃO DE ÁGUA ATRAVÉS DE TUBULAÇÃO", "contratacao": "160329-18/2026"}
    )
    av_2026 = regras.avaliar_requisicao(dados_2026)
    codigos_2026 = [a.codigo for a in av_2026.achados]
    assert "PCA_CONTRATACAO_OK" in codigos_2026
    assert "PCA_CONTRATACAO_INEXISTENTE" not in codigos_2026


def test_nd_00_detalhamento_sem_nc_obrigatoria(repo):
    """Testa que ND indetalhada gera DETALHAMENTO_PROGRAMADO (INFO) e não requer NC de detalhamento prévia."""
    from tests.test_regras import _nd00
    dados = _nd00(nd_destino="339039")
    av = regras.avaliar_requisicao(dados)
    codigos_achados = [a.codigo for a in av.achados]
    assert "DETALHAMENTO_PROGRAMADO" in codigos_achados
    assert "DETALHAMENTO_PENDENTE" not in codigos_achados
    assert "NC_DETALHAMENTO_NAO_ACHADA" not in codigos_achados


def test_reforco_motivo_personalizado_escrito(repo):
    """Testa que o usuário pode escrever um motivo livre no reforço."""
    from tests.test_regras import payload_reforco
    dados = payload_reforco(
        motivo={"id": "OUTRO", "texto": "Aumento extraordinário de demanda decorrente de operação militar em apoio à Brigada", "detalhe": "Conforme DIEx nº 456-EMG."}
    )
    av = regras.avaliar_reforco(dados)
    assert av.erros == []
    assert av.norm["motivo"]["id"] == "OUTRO"
    assert "Aumento extraordinário" in av.norm["motivo"]["texto"]
    assert "DIEx nº 456-EMG" in av.norm["motivo"]["detalhe"]


def test_anulacao_motivo_personalizado_escrito(repo):
    """Testa que o usuário pode escrever um motivo livre na anulação."""
    from tests.test_regras import payload_anulacao
    dados = payload_anulacao(
        motivo={"id": "OUTRO", "texto": "Rescisão consensual de ata por encerramento de atividades da empresa no município", "detalhe": "Processo administrativo 64619."}
    )
    av = regras.avaliar_anulacao(dados)
    assert av.erros == []
    assert av.norm["motivo"]["id"] == "OUTRO"
    assert "Rescisão consensual" in av.norm["motivo"]["texto"]


def test_anulacao_com_itens_discriminados(repo):
    """Testa que a anulação parcial aceita especificação dos itens a anular."""
    from tests.test_regras import payload_anulacao
    dados = payload_anulacao(
        empenhos=[{
            "ug": "160329",
            "ne": "2026NE000100",
            "tipo": "PARCIAL",
            "liquidado": "0,00",
            "itens": [{"seq": 1, "valor": "300,00"}]
        }]
    )
    av = regras.avaliar_anulacao(dados)
    assert av.erros == []
    assert av.norm["total"] == Decimal("300.00")
    assert len(av.norm["empenhos"][0]["itens_anulados"]) == 1
    assert av.norm["empenhos"][0]["itens_anulados"][0]["valor"] == Decimal("300.00")


def test_reforco_com_valor_e_qtde_compativeis(repo):
    """Testa que reforço aceita quantidade e valor consistentes simultaneamente."""
    from tests.test_regras import payload_reforco
    # seq 1 tem valor_unitario = 100.00
    dados = payload_reforco(
        itens=[{"seq": 1, "qtde": "2", "valor": "200,00"}]
    )
    av = regras.avaliar_reforco(dados)
    assert av.erros == []
    assert av.norm["total"] == Decimal("200.00")


def test_gerar_reforco_com_word_e_zip_anexos(client, repo):
    """Testa que ao gerar reforço, o Word e o ZIP com a Nota de Crédito e o Empenho a ser reforçado são produzidos."""
    import zipfile
    from tests.test_regras import payload_reforco
    r = client.post("/api/gerar/reforco", json=payload_reforco())
    assert r.status_code == 200, r.text
    dados = r.json()
    assert dados["ok"] is True
    assert dados["docx"] is not None
    assert dados["zip"] is not None

    # 1. Verifica download do DOCX
    resp_docx = client.get(f"/api/docx/{dados['docx']}")
    assert resp_docx.status_code == 200
    assert "wordprocessingml" in resp_docx.headers["content-type"]

    # 2. Verifica download do ZIP
    resp_zip = client.get(f"/api/zip/{dados['zip']}")
    assert resp_zip.status_code == 200
    assert resp_zip.headers["content-type"] == "application/zip"

    # 3. Inspeciona o conteúdo do ZIP: deve conter o .docx e os anexos (NE e NC)
    caminho_zip = config.SAIDA / dados["zip"]
    assert caminho_zip.is_file()
    with zipfile.ZipFile(caminho_zip, "r") as zf:
        nomes = zf.namelist()
        assert dados["docx"] in nomes
        assert any("NE" in n and n.endswith(".pdf") for n in nomes)
        assert any("NC" in n and n.endswith(".pdf") for n in nomes)



