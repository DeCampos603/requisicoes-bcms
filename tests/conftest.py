# -*- coding: utf-8 -*-
"""Fixtures: um repositório sintético mínimo (pregões, NC, NE, crédito) em pasta temporária.

Os testes de regras NÃO dependem dos dados reais do Drive; os de leitura de PDF real ficam em
test_leitores.py e são pulados se os arquivos não existirem.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app import base, config  # noqa: E402

HOJE = "2026-10-02"


def _pdf_vazio(caminho: Path, texto: str = "anexo") -> Path:
    from reportlab.pdfgen import canvas
    caminho.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(caminho))
    c.drawString(72, 720, texto)
    c.save()
    return caminho


def pdf_bytes(texto: str = "documento", paginas: int = 1) -> bytes:
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for k in range(paginas):
        c.drawString(72, 720, f"{texto} - pagina {k + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


DOCS_ADESAO_OBRIGATORIOS = ("TERMO_JUSTIFICATIVA", "PESQUISA_PRECOS", "ACEITE_FORNECEDOR")


def docs_adesao(tipos=DOCS_ADESAO_OBRIGATORIOS) -> dict:
    """Envia (de verdade, pelo mesmo caminho do /api/adesao/upload) os documentos da adesão e devolve {tipo: id}."""
    from app import anexos
    return {t: anexos.salvar_documento_adesao(pdf_bytes(f"doc {t}", 2), t)["id"] for t in tipos}


def _celula(nd="339030", pi="ABC123XYZ01", ptres="232144", ugr="160298", fonte="1000000000", valor="50000.00"):
    return {"esfera": "1", "ptres": ptres, "fonte": fonte, "nd": nd, "ugr": ugr, "pi": pi, "valor": valor}


def _nc(chave, emit, nd="339030", pi="ABC123XYZ01", tipo="Descentralização de Crédito", desc="Descentralização para manutenção. Empenho até 30 DEZ 26.",
        ug="160329", pdf=None, status="Contabilizada"):
    return {
        "chave": chave, "ug_emitente": emit, "nome_emitente": f"OM EMISSORA {emit}", "ano": int(chave[:4]), "numero": int(chave[-6:]),
        "tipo_nc": tipo, "status": status, "data_emissao": "05/08/2026", "descricao": desc, "total": "50000.00",
        "versao": "000", "ug_pasta": ug, "caminho": str(pdf) if pdf else None, "arquivo": "x.pdf", "avisos": [],
        "itens": [{"seq": 1, "situacao": "Contabilizado", "valor": "50000.00", "ug_favorecida": ug, "nome_favorecida": "BCMS",
                   "com_detalhamento": True, "origem": [], "destino": [_celula(nd=nd, pi=pi)]}],
    }


def _item_ata(n, desc, vu="100.00", saldo="1000", fim="2027-06-30", status="Vigente", cnpj="29.381.249/0001-76", nome="AM21 AUTO PECAS E MOTORES LTDA", ata="00010/2026"):
    return {"item": n, "ata": ata, "descricao": desc, "catmat": "123456", "catmat_nome": "X", "catalogo": "Material", "categoria": "Manutenção Automotiva",
            "nd_sugerida": "3.3.90.30.39", "subitem_sugerido": "30.39 - MATERIAL PARA MANUTENÇÃO DE VEÍCULOS", "fornecedor_cnpj": cnpj,
            "fornecedor_nome": nome, "vigencia_ini": "2026-01-01", "vigencia_fim": fim, "status": status, "valor_unitario": vu,
            "qtd_homologada": "2000", "qtd_autorizada": "1000", "qtd_saldo": saldo, "link": None}


def _pregao(ugg, numero, papel, itens):
    id_ = f"{ugg}:{numero}:{papel}"
    return {"id": id_, "ugg": ugg, "numero": numero, "modalidade": "PREGAO", "tipo_unidade": papel, "itens": itens, "n_itens": len(itens),
            "vigencia_fim_min": "2027-06-30", "vigencia_fim_max": "2027-06-30", "fornecedores": ["AM21 AUTO PECAS E MOTORES LTDA"]}


def _ne(chave, ug="160329", valor="1000.00", nd="339030", pi="ABC123XYZ01", pdf=None, emissao="10/08/2026", nc="2026NC000100", pregao="90001/2026"):
    return {
        "chave": chave, "ug_pasta": ug, "ug_emitente": ug, "ano": int(chave[:4]), "numero": int(chave[-6:]), "versao": "002",
        "esfera": "1", "ptres": "232144", "fonte": "1000000000", "nd": nd, "ugr": "160298", "pi": pi, "data_emissao": emissao,
        "tipo_empenho": "Global", "processo": "64619.003990/2026-08", "valor_atual": valor, "favorecido_codigo": "29.381.249/0001-76",
        "favorecido_nome": "AM21 AUTO PECAS E MOTORES LTDA", "descricao": f"AQS DE PECAS. {nc} - PREGAO {pregao} DO BCMS (160329) - UGG",
        "caminho": str(pdf) if pdf else None, "valor_incluido": valor, "valor_reforcado": "0", "valor_anulado": "0",
        "derivados": {"nc": nc, "pregao": pregao, "uasg_pregao": "160329", "papel": "UGG"},
        "itens": [{"seq": 1, "subelemento": "39", "item_compra": 1, "descricao": "PECA TESTE", "valor_item": valor, "valor_liquido": valor,
                   "operacoes": [{"data": emissao, "operacao": "Inclusão", "quantidade": "10.00000", "valor_unitario": "100.0000", "valor_total": valor}]}],
        "avisos": [],
    }


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """Nenhum teste fala com a API do Compras.gov.br: a rede aparece como indisponível (e o cache negativo é zerado)."""
    from app import pca

    def _offline(caminho, params, timeout=60, tentativas=True):
        _offline.chamadas += 1
        raise pca.ApiIndisponivel("rede bloqueada nos testes")
    _offline.chamadas = 0
    monkeypatch.setattr(pca, "_get_json", _offline)
    pca.reiniciar_cache_de_rede()
    yield _offline
    pca.reiniciar_cache_de_rede()


def pca_sintetico(dados: Path, catalogo: dict | None = None) -> None:
    """PCA 2025 do BCMS (2 contratações) e cache de classes do catálogo, no diretório de dados temporário."""
    from app import pca
    linhas = [
        {"numeroArtefato": 7, "anoArtefato": 2025, "descricaoObjetoDfd": "AQUISICAO DE PECAS PARA MANUTENCAO DE VIATURAS",
         "tituloProjetoCompra": "SECAO DE MANUTENCAO - VIATURAS", "codigoClasseMaterial": 2540, "nomeClasseMaterial": "PECAS DE VIATURAS",
         "tipoItem": "Material", "codigoItemCatalogo": 123456, "descricaoItemCatalogo": "PECA MECANICA", "ordemDfd": 1,
         "quantidadeItem": 10, "valorUnitarioItem": 100.0, "valorTotalItem": 1000.0, "dataHoraAtualizacaoItem": "2025-03-01T10:00:00"},
        {"numeroArtefato": 7, "anoArtefato": 2025, "descricaoObjetoDfd": "AQUISICAO DE PECAS PARA MANUTENCAO DE VIATURAS",
         "tituloProjetoCompra": "SECAO DE MANUTENCAO - VIATURAS", "codigoClasseMaterial": 2540, "nomeClasseMaterial": "PECAS DE VIATURAS",
         "tipoItem": "Material", "codigoItemCatalogo": 654321, "descricaoItemCatalogo": "FILTRO DE AR", "ordemDfd": 1,
         "quantidadeItem": 5, "valorUnitarioItem": 50.0, "valorTotalItem": 250.0, "dataHoraAtualizacaoItem": "2025-03-02T10:00:00"},
        {"numeroArtefato": 12, "anoArtefato": 2025, "descricaoObjetoDfd": "AQUISICAO DE MATERIAL DE ESCRITORIO",
         "tituloProjetoCompra": "SECAO DE LICITACOES - EXPEDIENTE", "codigoClasseMaterial": 7510, "nomeClasseMaterial": "ARTIGOS PARA ESCRITORIO",
         "tipoItem": "Material", "codigoItemCatalogo": 222222, "descricaoItemCatalogo": "CANETA", "ordemDfd": 2,
         "quantidadeItem": 100, "valorUnitarioItem": 2.0, "valorTotalItem": 200.0, "dataHoraAtualizacaoItem": "2025-03-03T10:00:00"},
    ]
    pca_dados = {"orgao": "00394452000103", "uasg": "160329", "anos": {
        "2025": {"posicao": "2025-03-03T10:00:00", "total_itens": len(linhas), "contratacoes": pca.normalizar_pca(linhas, "160329")},
        "2026": {"posicao": None, "total_itens": 0, "contratacoes": {}}}}
    (dados / "pca.json").write_text(json.dumps(pca_dados, ensure_ascii=False), encoding="utf-8")
    cat = catalogo if catalogo is not None else {"M:123456": {"classe": "2540", "nome": "PECAS DE VIATURAS"}, "M:654321": {"classe": "2540", "nome": "PECAS DE VIATURAS"},
                                                 "M:222222": {"classe": "7510", "nome": "ARTIGOS PARA ESCRITORIO"}}
    (dados / "catalogo.json").write_text(json.dumps({"itens": cat}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """Índices sintéticos e pastas temporárias; devolve um dict com atalhos úteis."""
    dados = tmp_path / "dados"
    dados.mkdir()
    for nome in ("DADOS", "SAIDA", "ANEXOS"):
        monkeypatch.setattr(config, nome, tmp_path / nome.lower())
    monkeypatch.setattr(config, "UPLOAD_NC_DIRS", {ug: tmp_path / "anexos" / "nc" / ug for ug in ("160329", "167329")})
    monkeypatch.setattr(config, "UPLOAD_NE_DIRS", {ug: tmp_path / "anexos" / "ne" / ug for ug in ("160329", "167329")})
    monkeypatch.setattr(config, "DADOS", dados)

    pdf_nc = _pdf_vazio(tmp_path / "pdf" / "nc.pdf", "NC")
    pdf_ne = _pdf_vazio(tmp_path / "pdf" / "ne.pdf", "NE")
    ncs = {
        "160298:2026NC000100": _nc("2026NC000100", "160298", pdf=pdf_nc),
        "160073:2026NC000200": _nc("2026NC000200", "160073", nd="339000", pi="PIENERGIA01", pdf=pdf_nc),
        "160298:2026NC000300": _nc("2026NC000300", "160298", tipo="Anulação de Descentralização de Crédito", pdf=pdf_nc),
        "160298:2026NC000400": _nc("2026NC000400", "160298", pdf=pdf_nc, desc="Empenho até 30 JUN 26."),
        "160298:2026NC000500": _nc("2026NC000500", "160298", nd="339039", pi="OUTROPI0001", pdf=pdf_nc),
    }
    credito = {
        "160298:2026NC000100": {"emit": "160298", "chave": "2026NC000100", "dia": "05/08/2026", "op": "DESCENTRALIZACAO DE CREDITO", "obj": "x",
                                "favorecidas": {"160329": {"status": "Disponível", "prov": 50000.0, "emp": 20000.0, "cred": 30000.0,
                                                           "celulas": [{"ptres": "2000", "pi": "ABC123XYZ01", "nd": "339030", "prov": 50000.0, "emp": 20000.0, "cred": 30000.0}]}}},
        "160329:2026NC000031": {"emit": "160329", "chave": "2026NC000031", "dia": "20/08/2026", "op": "DETALHAMENTO DE CREDITO", "obj": "MUDANCA DE ND",
                                "favorecidas": {"160329": {"status": "Disponível", "prov": 8000.0, "emp": 0.0, "cred": 8000.0,
                                                           "celulas": [{"ptres": "2000", "pi": "PIENERGIA01", "nd": "339000", "prov": 0.0, "emp": 0.0, "cred": -8000.0},
                                                                       {"ptres": "2000", "pi": "PIENERGIA01", "nd": "339039", "prov": 0.0, "emp": 0.0, "cred": 8000.0}]}}},
    }
    itens_90001 = [_item_ata(1, "PECA MECANICA ELETRICA VEICULO AUTOMOTIVO TESTE", vu="100.00"),
                   _item_ata(2, "ITEM POR DESCONTO", vu="1", saldo="500000"),
                   _item_ata(3, "ITEM VENCIDO", status="Vencida", fim="2026-01-01"),
                   _item_ata(4, "ITEM SEM ATA", status="Sem ata (fornecedor homologado)")]
    pregoes = {
        "posicao": "2026-09-30", "importado_em": "x", "total": 3,
        "pregoes": {p["id"]: p for p in (
            _pregao("160329", "90001/2026", "GERENCIADORA", itens_90001),
            _pregao("160298", "90004/2026", "PARTICIPANTE", [_item_ata(1, "OUTRO ITEM TESTE")]),
            _pregao("160450", "90008/2025", "NAO_PARTICIPANTE", [_item_ata(1, "ITEM DE CARONA", saldo="14")]),
        )},
    }
    nes = {"nes": {f"160329:{n['chave']}": n for n in (_ne("2026NE000100", pdf=pdf_ne), _ne("2026NE000101", valor="500.00", nc="2026NC000500", pi="OUTROPI0001", pdf=pdf_ne))}}
    uasgs = {"uasgs": {"160329": {"nome": "BATALHÃO CENTRAL DE MANUTENÇÃO E SUPRIMENTO", "sigla": "BCMS"}, "160298": {"nome": "COMANDO DA 1. REGIÃO MILITAR", "sigla": "CMDO 1ª RM"},
                       "160450": {"nome": "14º R C MEC", "sigla": "14 RCMEC"}}}
    for nome, conteudo in (("ncs.json", {"ncs": ncs, "total": len(ncs)}), ("credito.json", {"posicao": "01/10/2026 08:00", "ncs": credito, "total": len(credito)}),
                           ("pregoes.json", pregoes), ("nes.json", {**nes, "total": len(nes["nes"])}), ("uasgs.json", uasgs)):
        (dados / nome).write_text(json.dumps(conteudo, ensure_ascii=False), encoding="utf-8")
    base.invalidar()
    return {"dados": dados, "pdf_nc": pdf_nc, "pdf_ne": pdf_ne, "tmp": tmp_path}


def payload_requisicao(**sobrescrever) -> dict:
    """Requisição válida contra o repositório sintético (pregão 90001/2026 do BCMS, NC 2026NC000100)."""
    p = {
        "hoje": HOJE, "objeto": "MATERIAL", "modalidade": "PREGAO",
        "cabecalho": {"diex": "27-COSM/CMDO/BCMS", "nup": "64619.004672/2026-56", "data": HOJE,
                      "requisitante": {"posto": "Cap", "nome": "Fulano de Tal Silva", "funcao": "Chefe da Seção de Manutenção"}},
        "licitacao": {"pregao_id": "160329:90001/2026:GERENCIADORA"},
        "finalidade": {"id": "MNT_VTR_NBLD"},
        "fonte": {"nc": "2026NC000100"},
        "tipo_empenho": "GLOBAL", "contrato": {"possui": False},
        "pca": {"consta": True, "classe": "2540 - PECAS DE VIATURAS", "contratacao": "160329-7/2026"},
        "fornecedores": [{"cnpj": "29.381.249/0001-76", "nome": "AM21 AUTO PECAS E MOTORES LTDA", "itens": [
            {"item": 1, "si": "39", "descricao": "PECA MECANICA ELETRICA VEICULO AUTOMOTIVO TESTE", "und": "Und", "qtde": "10", "valor_unit": "100,00"}]}],
    }
    p.update(sobrescrever)
    return p


def codigos(av, nivel=None) -> set[str]:
    return {a.codigo for a in av.achados if nivel is None or a.nivel == nivel}


@pytest.fixture()
def client(repo):
    from fastapi.testclient import TestClient
    from app import main
    return TestClient(main.app, base_url="http://127.0.0.1:8765", headers={"X-Reqemp-Token": main.TOKEN})
