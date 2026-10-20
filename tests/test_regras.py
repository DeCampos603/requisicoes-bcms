# -*- coding: utf-8 -*-
"""Regras de negócio contra um repositório sintético."""

from __future__ import annotations

import copy

from app import regras

from conftest import HOJE, codigos, docs_adesao, payload_requisicao


def av_req(**kw):
    return regras.avaliar_requisicao(payload_requisicao(**kw))


# ----------------------------------------------------------------------------- caminho feliz
def test_requisicao_valida_pode_gerar(repo):
    av = av_req()
    assert av.erros == [], [a.mensagem for a in av.erros]
    assert av.pode_gerar
    assert str(av.norm["total"]) == "1000.00"
    assert av.norm["fonte"]["nd"] == "339030" and av.norm["fonte"]["pi"] == "ABC123XYZ01"
    assert av.norm["licitacao"]["tipo_unidade"] == "GERENCIADORA"


def test_formato_nup_e_diex_bloqueiam(repo):
    p = payload_requisicao()
    p["cabecalho"]["nup"] = "123"
    p["cabecalho"]["diex"] = "sem-padrao"
    av = regras.avaliar_requisicao(p)
    assert {"cabecalho.nup", "cabecalho.diex"} <= {a.campo for a in av.erros}
    assert not av.pode_gerar


# ----------------------------------------------------------------------------- pregão × UG
def test_tipo_unidade_diverge_da_base(repo):
    p = payload_requisicao()
    p["licitacao"]["tipo_unidade"] = "PARTICIPANTE"
    assert "TIPO_UNIDADE_DIVERGE" in codigos(regras.avaliar_requisicao(p), "ERRO")


def test_gerenciadora_so_na_propria_uasg(repo):
    p = payload_requisicao()
    p["licitacao"] = {"manual": {"numero": "90019/2025", "uasg": "160106", "nome_om": "OUTRA OM", "tipo_unidade": "GERENCIADORA"}}
    av = regras.avaliar_requisicao(p)
    assert "GERENCIADORA_UASG_OUTRA" in codigos(av, "ERRO")
    assert "PREGAO_FORA_DA_BASE" in codigos(av, "ALERTA")


def test_pregao_proprio_nao_pode_ser_participante(repo):
    p = payload_requisicao()
    p["licitacao"] = {"manual": {"numero": "90077/2026", "uasg": "160329", "nome_om": "BCMS", "tipo_unidade": "PARTICIPANTE"}}
    assert "PROPRIA_UASG_NAO_GERENCIADORA" in codigos(regras.avaliar_requisicao(p), "ERRO")


def test_pregao_manual_ja_na_base_e_recusado(repo):
    p = payload_requisicao()
    p["licitacao"] = {"manual": {"numero": "90001/2026", "uasg": "160329", "nome_om": "BCMS", "tipo_unidade": "GERENCIADORA"}}
    assert "PREGAO_JA_NA_BASE" in codigos(regras.avaliar_requisicao(p), "ERRO")


def test_pregao_obrigatorio(repo):
    p = payload_requisicao()
    p["licitacao"] = {}
    assert "PREGAO_OBRIGATORIO" in codigos(regras.avaliar_requisicao(p), "ERRO")


def test_dispensa_exige_fundamento_e_e_da_propria_ug(repo):
    p = payload_requisicao(modalidade="DISPENSA", licitacao={"numero": "15/2026"})
    p["contrato"] = {"possui": False, "hipotese": "ART95_I"}
    av = regras.avaliar_requisicao(p)
    assert "FUNDAMENTO_DISPENSA" in codigos(av, "ERRO")
    p["licitacao"]["fundamento_dispensa"] = "ART75_II"
    p["fornecedores"][0]["itens"][0]["item"] = 7  # dispensa não cruza com ata
    av = regras.avaliar_requisicao(p)
    assert av.erros == [], [a.mensagem for a in av.erros]
    assert av.norm["licitacao"]["tipo_unidade"] == "GERENCIADORA" and av.norm["licitacao"]["ugg"] == "160329"


# ----------------------------------------------------------------------------- adesão (não participante)
def _carona(**adesao):
    p = payload_requisicao()
    p["licitacao"] = {"pregao_id": "160450:90008/2025:NAO_PARTICIPANTE"}
    p["adesao"] = {"autorizacao_data": "2026-09-20", "aceite_fornecedor": True, "ata_federal": True, "documentos": docs_adesao(), **adesao}
    p["fornecedores"][0]["itens"][0]["descricao"] = "ITEM DE CARONA"
    p["fornecedores"][0]["itens"][0]["qtde"] = "10"
    return p


def test_adesao_valida(repo):
    av = regras.avaliar_requisicao(_carona())
    assert av.erros == [], [a.mensagem for a in av.erros]


def test_adesao_sem_aceite_ou_federal_bloqueia(repo):
    av = regras.avaliar_requisicao(_carona(aceite_fornecedor=False, ata_federal=False))
    assert {"ADESAO_ACEITE", "ADESAO_ATA_FEDERAL"} <= codigos(av, "ERRO")


def test_adesao_prazo_de_90_dias(repo):
    assert "ADESAO_PRAZO_90_DIAS" in codigos(regras.avaliar_requisicao(_carona(autorizacao_data="2026-06-01")), "ERRO")
    assert "ADESAO_PRAZO_PROXIMO" in codigos(regras.avaliar_requisicao(_carona(autorizacao_data="2026-07-10")), "ALERTA")


def test_quantidade_acima_do_saldo_da_ata(repo):
    p = _carona()
    p["fornecedores"][0]["itens"][0]["qtde"] = "15"  # saldo do BCMS = 14
    assert "QTDE_ACIMA_SALDO" in codigos(regras.avaliar_requisicao(p), "ERRO")


# ----------------------------------------------------------------------------- itens × ata
def _item(av_kw):
    p = payload_requisicao()
    p["fornecedores"][0]["itens"][0].update(av_kw)
    return regras.avaliar_requisicao(p)


def test_valor_unitario_precisa_ser_o_da_ata(repo):
    assert "VU_DIVERGE_ATA" in codigos(_item({"valor_unit": "99,00"}), "ERRO")


def test_ata_por_maior_desconto(repo):
    base_item = {"item": 2, "descricao": "ITEM POR DESCONTO", "qtde": "1000", "valor_unit": "0,95"}
    av = _item(base_item)
    assert "VU_DIVERGE_ATA" not in codigos(av) and "DESCONTO_ATA" in codigos(av, "INFO")
    assert "VU_ACIMA_ATA" in codigos(_item({**base_item, "valor_unit": "1,05"}), "ERRO")


def test_item_inexistente_vencido_e_sem_ata(repo):
    assert "ITEM_FORA_DA_ATA" in codigos(_item({"item": 99}), "ERRO")
    assert "ATA_VENCIDA" in codigos(_item({"item": 3, "descricao": "ITEM VENCIDO"}), "ERRO")
    assert "SEM_ATA_FORMALIZADA" in codigos(_item({"item": 4, "descricao": "ITEM SEM ATA"}), "ERRO")


def test_fornecedor_diferente_do_registrado_e_quantidade_zero(repo):
    p = payload_requisicao()
    p["fornecedores"][0]["cnpj"] = "60.055.695/0001-30"
    assert "FORNECEDOR_DIVERGE_ATA" in codigos(regras.avaliar_requisicao(p), "ERRO")
    assert "VALOR_NAO_POSITIVO" in codigos(_item({"qtde": "0"}), "ERRO")


def test_descricao_alterada_gera_alerta(repo):
    av = _item({"descricao": "PECA DIFERENTE DA ATA"})
    assert "DESCRICAO_DIVERGE_ATA" in codigos(av, "ALERTA")
    assert not av.pode_gerar  # alerta sem ciência bloqueia


def test_ciencia_do_alerta_libera(repo):
    p = payload_requisicao()
    p["fornecedores"][0]["itens"][0]["descricao"] = "PECA DIFERENTE DA ATA"
    av = regras.avaliar_requisicao(p)
    p["alertas_ciente"] = [a.chave for a in av.alertas]
    assert regras.avaliar_requisicao(p).pode_gerar


def test_item_repetido_e_fornecedor_repetido(repo):
    p = payload_requisicao()
    p["fornecedores"][0]["itens"].append(copy.deepcopy(p["fornecedores"][0]["itens"][0]))
    assert "ITEM_REPETIDO" in codigos(regras.avaliar_requisicao(p), "ERRO")
    p = payload_requisicao()
    p["fornecedores"].append(copy.deepcopy(p["fornecedores"][0]))
    assert "FORNECEDOR_REPETIDO" in codigos(regras.avaliar_requisicao(p), "ERRO")


# ----------------------------------------------------------------------------- NC / anexos
def test_nc_inexistente_exige_upload(repo):
    av = av_req(fonte={"nc": "2026NC999999"})
    achado = next(a for a in av.erros if a.codigo == "NC_INEXISTENTE")
    assert achado.acao == "upload_nc" and not av.pode_gerar


def test_nc_so_no_dashboard_exige_pdf(repo, monkeypatch):
    from app import base
    monkeypatch.setattr(base, "candidatas_nc", lambda chave: [{"emitente": "160298", "nome_emitente": "X", "chave": chave, "arquivo_no_repositorio": False,
                                                               "no_dashboard": True, "tipo_nc": None, "status": None, "data_emissao": None,
                                                               "descricao": None, "total": None, "ug_pasta": None}])
    assert "NC_SEM_ANEXO" in codigos(av_req(), "ERRO")


def test_nc_de_anulacao_nao_fornece_credito(repo):
    assert "NC_DE_ANULACAO" in codigos(av_req(fonte={"nc": "2026NC000300"}), "ERRO")


def test_saldo_insuficiente_vira_alerta_com_ciencia(repo):
    p = payload_requisicao()
    p["fornecedores"][0]["itens"][0]["qtde"] = "400"  # R$ 40.000 > crédito de R$ 30.000
    av = regras.avaliar_requisicao(p)
    assert "SALDO_NC_INSUFICIENTE" in codigos(av, "ALERTA") and not av.pode_gerar


def test_prazo_de_empenho_da_nc_vencido(repo):
    assert "NC_PRAZO_VENCIDO" in codigos(av_req(fonte={"nc": "2026NC000400"}), "ALERTA")
    assert regras._prazo_empenho_da_nc("EMPENHAR ATÉ 31/MAR/26").isoformat() == "2026-03-31"
    assert regras._prazo_empenho_da_nc("Empenho até 30 JUN 26.").isoformat() == "2026-06-30"
    assert regras._prazo_empenho_da_nc("prazo ate 15/10/2026").isoformat() == "2026-10-15"
    assert regras._prazo_empenho_da_nc("sem prazo") is None


def test_prazo_de_empenho_nos_formatos_reais_das_nc():
    from datetime import date
    f = regras._prazo_empenho_da_nc
    assert f("(EMPENHAR ATE 19OUT26)").isoformat() == "2026-10-19"            # sem espaços entre dia, mês e ano
    assert f("PRAZO DE EMPENHO 27 FEV 26").isoformat() == "2026-02-27"        # "PRAZO DE EMPENHO", sem "ATÉ"
    assert f("PRAZO 30 ABR 26").isoformat() == "2026-04-30"
    assert f("EMPENHO ATÉ 30 DE JUNHO DE 2026").isoformat() == "2026-06-30"
    assert f("EMPENHADOS ATE 25 SETEMBRO SERAO RECOLHIDOS", date(2026, 8, 1)).isoformat() == "2026-09-25"  # sem ano: o da emissão
    assert f("EMPENHADOS ATE 25 SETEMBRO SERAO RECOLHIDOS") is None                                      # sem ano e sem emissão: não chuta
    assert f("EMPENHO ATE 31/03", date(2026, 2, 1)).isoformat() == "2026-03-31"
    assert f("EMPENHO ATE 31/01", date(2026, 12, 5)).isoformat() == "2027-01-31"                          # NC de dezembro, prazo em janeiro
    assert f("PRAZO PARA EMPENHO: 30 DIAS A CONTAR DESTA DATA", date(2026, 9, 1)).isoformat() == "2026-10-01"
    assert f("PRAZO PARA EMPENHO: 30 A CONTAR DESTA DATA", date(2026, 9, 1)).isoformat() == "2026-10-01"
    for sem_data in ("EMPENHO IMEDIATO", "PRAZO DE APLICACAO", "ATE 12 SETORES", "PRAZO PREVISTO"):
        assert f(sem_data, date(2026, 9, 1)) is None, sem_data
    import time
    t0 = time.time()
    f("ATE " + "1 " * 20000 + "PRAZO " * 5000, date(2026, 1, 1))
    assert time.time() - t0 < 1.0  # linear, sem retrocesso catastrófico


def test_nc_ambigua_pede_emitente(repo):
    import json
    from app import base, config
    arq = config.DADOS / "ncs.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    d["ncs"]["160504:2026NC000100"] = dict(d["ncs"]["160298:2026NC000100"], ug_emitente="160504")
    arq.write_text(json.dumps(d), encoding="utf-8")
    base.invalidar()
    av = av_req()
    assert "NC_AMBIGUA" in codigos(av, "ERRO")
    assert regras.avaliar_requisicao(payload_requisicao(fonte={"nc": "2026NC000100", "emitente": "160504"})).erros == []


# ----------------------------------------------------------------------------- ND 00 (detalhamento)
def _nd00(**fonte):
    p = payload_requisicao(objeto="SERVICO", finalidade={"id": "SV_MNT_VTR"})
    p["fonte"] = {"nc": "2026NC000200", **fonte}
    p["fornecedores"][0]["itens"][0]["descricao"] = "SERVICO DE MANUTENCAO DE VEICULO"
    p["fornecedores"][0]["itens"][0]["item"] = 7
    p["modalidade"] = "DISPENSA"
    p["licitacao"] = {"numero": "15/2026", "fundamento_dispensa": "ART75_I"}
    p["contrato"] = {"possui": False, "hipotese": "ART95_I"}
    return p


def test_nd_00_exige_nd_de_destino(repo):
    av = regras.avaliar_requisicao(_nd00())
    assert "ND_DESTINO_OBRIGATORIA" in codigos(av, "ERRO")
    assert av.norm["fonte"]["indetalhada"] and av.norm["fonte"]["nds_candidatas"]


def test_nd_00_com_destino_gera_alerta_de_detalhamento_pendente(repo):
    av = regras.avaliar_requisicao(_nd00(nd_destino="339039"))
    assert "ND_DESTINO_OBRIGATORIA" not in codigos(av)
    assert "DETALHAMENTO_PROGRAMADO" in codigos(av, "INFO")
    assert av.norm["fonte"]["nd"] == "339039" and av.norm["fonte"]["nd_origem"] == "339000"


def test_nd_00_com_nc_de_detalhamento_vinculada(repo):
    av = regras.avaliar_requisicao(_nd00(nd_destino="339039", nc_detalhamento="2026NC000031"))
    assert "DETALHAMENTO_PROGRAMADO" not in codigos(av)
    assert "DETALHAMENTO_VINCULADO" in codigos(av, "INFO")
    assert av.norm["fonte"]["detalhamento"]["nc"] == "2026NC000031"


def test_nd_destino_precisa_ser_da_mesma_categoria(repo):
    assert "ND_DESTINO_INVALIDA" in codigos(regras.avaliar_requisicao(_nd00(nd_destino="449052")), "ERRO")
    assert "ND_DESTINO_INVALIDA" in codigos(regras.avaliar_requisicao(_nd00(nd_destino="339000")), "ERRO")


# ----------------------------------------------------------------------------- condições
def test_contrato_pca_finalidade_tipo_empenho(repo):
    p = payload_requisicao()
    p["contrato"] = {"possui": True, "numero": "16"}
    p["pca"] = {"consta": False, "justificativa": ""}
    p["finalidade"] = {"id": ""}
    p["tipo_empenho"] = ""
    av = regras.avaliar_requisicao(p)
    assert {"FORMATO", "FINALIDADE", "TIPO_EMPENHO"} <= codigos(av, "ERRO")
    assert "FORA_DO_PCA" in codigos(av, "ALERTA")


def test_finalidade_outra_exige_texto_valido(repo):
    p = payload_requisicao(finalidade={"id": "OUTRA", "outra": "curto"})
    assert "FORMATO" in codigos(regras.avaliar_requisicao(p), "ERRO")
    p = payload_requisicao(finalidade={"id": "OUTRA", "outra": "manutenção de equipamentos de cozinha da OM"})
    assert regras.avaliar_requisicao(p).erros == []


def test_data_futura_e_retroativa(repo):
    p = payload_requisicao()
    p["cabecalho"]["data"] = "2026-12-01"
    assert "DATA_FUTURA" in codigos(regras.avaliar_requisicao(p), "ERRO")
    p["cabecalho"]["data"] = "2026-08-01"
    assert "DATA_RETROATIVA" in codigos(regras.avaliar_requisicao(p), "ALERTA")


# ----------------------------------------------------------------------------- reforço
def payload_reforco(**kw):
    p = {"hoje": HOJE,
         "cabecalho": {"diex": "29-ALMOX/S4/BCMS", "nup": "64619.004649/2026-61", "data": HOJE,
                       "requisitante": {"posto": "1º Ten", "nome": "Yuri Rodrigues Cavalcanti", "funcao": "Chefe do Almoxarifado"}},
         "empenho": {"ug": "160329", "ne": "2026NE000100"}, "itens": [{"seq": 1, "qtde": "2"}],
         "fonte": {}, "motivo": {"id": "DEMANDA_ADICIONAL"}}
    p.update(kw)
    return p


def test_reforco_valido_usa_nc_da_ne_e_preco_da_ne(repo):
    av = regras.avaliar_reforco(payload_reforco())
    assert av.erros == [], [a.mensagem for a in av.erros]
    assert str(av.norm["total"]) == "200.00"  # 2 × R$ 100,00 (preço da NE)
    assert av.norm["fonte"]["nc"] == "2026NC000100"


def test_reforco_credito_em_celula_diferente(repo):
    assert "NC_CELULA_DIFERENTE" in codigos(regras.avaliar_reforco(payload_reforco(fonte={"nc": "2026NC000500"})), "ERRO")


def test_reforco_ne_inexistente_exige_upload(repo):
    av = regras.avaliar_reforco(payload_reforco(empenho={"ug": "160329", "ne": "2026NE000999"}))
    assert next(a for a in av.erros if a.codigo == "NE_INEXISTENTE").acao == "upload_ne"


def test_reforco_acima_de_25_por_cento_gera_alerta(repo):
    av = regras.avaliar_reforco(payload_reforco(itens=[{"seq": 1, "qtde": "3"}]))  # 300 / 1000 = 30%
    assert "ACRESCIMO_ACIMA_25" in codigos(av, "ALERTA")


def test_reforco_item_e_motivo_obrigatorios(repo):
    av = regras.avaliar_reforco(payload_reforco(itens=[{"seq": 9, "qtde": "1"}], motivo={"id": ""}))
    assert {"ITEM_NAO_NA_NE", "MOTIVO"} <= codigos(av, "ERRO")


# ----------------------------------------------------------------------------- anulação
def payload_anulacao(**kw):
    p = {"hoje": HOJE,
         "cabecalho": {"diex": "79-Transporte/CCAp/Cmdo", "nup": "64619.004207/2026-15", "data": HOJE,
                       "requisitante": {"posto": "1º Sgt", "nome": "Heric Joaquim Linhares", "funcao": "Chefe da Seção de Transporte"}},
         "empenhos": [{"ug": "160329", "ne": "2026NE000100", "tipo": "PARCIAL", "valor": "400,00", "liquidado": "0"}],
         "consultou_siafi": True, "motivo": {"id": "SALDO_NAO_UTILIZADO"}}
    p.update(kw)
    return p


def test_anulacao_parcial_valida(repo):
    av = regras.avaliar_anulacao(payload_anulacao())
    assert av.erros == [], [a.mensagem for a in av.erros]
    assert str(av.norm["total"]) == "400.00"
    assert str(av.norm["empenhos"][0]["saldo_apos"]) == "600.00"


def test_anulacao_acima_do_saldo_a_liquidar(repo):
    p = payload_anulacao(empenhos=[{"ug": "160329", "ne": "2026NE000100", "tipo": "PARCIAL", "valor": "900,00", "liquidado": "300,00"}])
    assert "ANULACAO_ACIMA_SALDO" in codigos(regras.avaliar_anulacao(p), "ERRO")


def test_anulacao_total_com_liquidacao_nao_pode(repo):
    p = payload_anulacao(empenhos=[{"ug": "160329", "ne": "2026NE000100", "tipo": "TOTAL", "liquidado": "100,00"}])
    assert "TOTAL_COM_LIQUIDACAO" in codigos(regras.avaliar_anulacao(p), "ERRO")
    p = payload_anulacao(empenhos=[{"ug": "160329", "ne": "2026NE000100", "tipo": "TOTAL", "liquidado": "0"}])
    av = regras.avaliar_anulacao(p)
    assert av.erros == [] and str(av.norm["total"]) == "1000.00"


def test_anulacao_exige_conferencia_no_siafi_e_motivo(repo):
    av = regras.avaliar_anulacao(payload_anulacao(consultou_siafi=False, motivo={"id": ""}))
    assert {"CONFIRMAR_SIAFI", "MOTIVO"} <= codigos(av, "ERRO")


def test_anulacao_ne_repetida_e_exercicio_anterior(repo):
    p = payload_anulacao()
    p["empenhos"].append(dict(p["empenhos"][0]))
    assert "NE_REPETIDA" in codigos(regras.avaliar_anulacao(p), "ERRO")
    p = payload_anulacao()
    p["cabecalho"]["data"] = "2026-10-02"
    p["hoje"] = "2027-01-15"
    p["cabecalho"]["data"] = "2027-01-15"
    p["cabecalho"]["nup"] = "64619.004207/2027-15"
    assert "NE_EXERCICIO_ANTERIOR" in codigos(regras.avaliar_anulacao(p), "ALERTA")


# ----------------------------------------------------------------------------- histórico
def test_diex_duplicado_e_diex_com_outro_nup(repo):
    from app import historico
    historico.registrar("REQUISICAO", "27-COSM/CMDO/BCMS", "64619.004672/2026-56", 2026, "1", "x.pdf", [], [])
    assert "DIEX_JA_GERADO" in codigos(av_req(), "ALERTA")
    p = payload_requisicao()
    p["cabecalho"]["nup"] = "64619.000001/2026-00"
    assert "DIEX_OUTRO_NUP" in codigos(regras.avaliar_requisicao(p), "ALERTA")


# ----------------------------------------------------------------------------- limite da dispensa (Decreto 12.807/2025)
def test_dispensa_acima_do_limite_do_art_75(repo):
    p = payload_requisicao(modalidade="DISPENSA", licitacao={"numero": "15/2026", "fundamento_dispensa": "ART75_II"})
    p["contrato"] = {"possui": False, "hipotese": "ART95_I"}
    p["fornecedores"][0]["itens"][0].update({"item": 7, "qtde": "1", "valor_unit": "70.000,00"})
    av = regras.avaliar_requisicao(p)
    assert "DISPENSA_ACIMA_DO_LIMITE" in codigos(av, "ALERTA")
    p["fornecedores"][0]["itens"][0]["valor_unit"] = "65.000,00"
    av = regras.avaliar_requisicao(p)
    assert "DISPENSA_ACIMA_DO_LIMITE" not in codigos(av) and "DISPENSA_SOMATORIO_ANUAL" in codigos(av, "INFO")
    p["fornecedores"][0]["itens"][0]["valor_unit"] = "65.492,11"  # o limite é "inferior a"
    assert "DISPENSA_ACIMA_DO_LIMITE" in codigos(regras.avaliar_requisicao(p), "ALERTA")


# ----------------------------------------------------------------------------- QA: célula da NC
def _editar_nc(repo, chave, fn):
    import json
    from app import base
    arq = repo["dados"] / "ncs.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    fn(d["ncs"][chave])
    arq.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    base.invalidar()


def test_reforco_compara_ptres_e_fonte_alem_de_nd_e_pi(repo):
    _editar_nc(repo, "160298:2026NC000100", lambda nc: nc["itens"][0]["destino"][0].update(ptres="999999"))
    av = regras.avaliar_reforco(payload_reforco())
    assert "NC_CELULA_DIFERENTE" in codigos(av, "ERRO") and "PTRES 232144" in next(a.mensagem for a in av.erros if a.codigo == "NC_CELULA_DIFERENTE")
    _editar_nc(repo, "160298:2026NC000100", lambda nc: nc["itens"][0]["destino"][0].update(ptres="232144", fonte="1021000000"))
    assert "NC_CELULA_DIFERENTE" in codigos(regras.avaliar_reforco(payload_reforco()), "ERRO")


def test_reforco_zeros_a_esquerda_nao_diferenciam_e_ugr_diferente_so_informa(repo):
    _editar_nc(repo, "160298:2026NC000100", lambda nc: nc["itens"][0]["destino"][0].update(ptres="0232144", ugr="0160298"))
    av = regras.avaliar_reforco(payload_reforco())
    assert av.erros == [] and "NC_UGR_DIFERENTE_NE" not in codigos(av)
    _editar_nc(repo, "160298:2026NC000100", lambda nc: nc["itens"][0]["destino"][0].update(ugr="160505"))
    av = regras.avaliar_reforco(payload_reforco())  # na base real a UGR da NE ≠ UGR da NC em 6 de 269 empenhos
    assert av.erros == [] and "NC_UGR_DIFERENTE_NE" in codigos(av, "INFO")


def test_nc_com_varias_celulas_exige_escolha_explicita(repo):
    def duas(nc):
        nc["itens"][0]["destino"].append({"esfera": "1", "ptres": "232144", "fonte": "1000000000", "nd": "339039", "ugr": "160298", "pi": "OUTROPI0001", "valor": "100.00"})
    _editar_nc(repo, "160298:2026NC000100", duas)
    av = regras.avaliar_requisicao(payload_requisicao())
    assert "CELULA_OBRIGATORIA" in codigos(av, "ERRO") and av.norm["resumo"]["fonte"]["escolher_celula"] is True
    assert len(av.norm["resumo"]["fonte"]["celulas"]) == 2
    for vazio in ("", None):
        assert "CELULA_OBRIGATORIA" in codigos(regras.avaliar_requisicao(payload_requisicao(fonte={"nc": "2026NC000100", "celula": vazio})), "ERRO")
    ok = regras.avaliar_requisicao(payload_requisicao(fonte={"nc": "2026NC000100", "celula": 1}))
    assert "CELULA_OBRIGATORIA" not in codigos(ok) and ok.norm["fonte"]["pi"] == "OUTROPI0001"
    assert "CELULA_OBRIGATORIA" in codigos(regras.avaliar_requisicao(payload_requisicao(fonte={"nc": "2026NC000100", "celula": 7})), "ERRO")


# ----------------------------------------------------------------------------- QA: ciência presa ao conteúdo do alerta
def test_ciencia_nao_vale_quando_o_conteudo_do_alerta_muda(repo):
    p = payload_requisicao()
    p["cabecalho"]["data"] = "2026-08-01"
    av = regras.avaliar_requisicao(p)
    retro = next(a for a in av.alertas if a.codigo == "DATA_RETROATIVA")
    p["alertas_ciente"] = [retro.chave]
    assert "DATA_RETROATIVA" not in {a.codigo for a in regras.avaliar_requisicao(p).alertas_sem_ciencia}
    p["cabecalho"]["data"] = "2026-08-02"  # mesmo código e campo, mensagem diferente
    novo = regras.avaliar_requisicao(p)
    assert "DATA_RETROATIVA" in {a.codigo for a in novo.alertas_sem_ciencia}
    assert next(a for a in novo.alertas if a.codigo == "DATA_RETROATIVA").chave != retro.chave


# ----------------------------------------------------------------------------- adesão: documentos obrigatórios anexos
def test_adesao_exige_termo_pesquisa_de_precos_e_aceite_do_fornecedor(repo):
    av = regras.avaliar_requisicao(_carona(documentos={}))
    faltando = {a.campo for a in av.erros if a.codigo == "ADESAO_DOC_AUSENTE"}
    assert faltando == {"adesao.documentos.TERMO_JUSTIFICATIVA", "adesao.documentos.PESQUISA_PRECOS", "adesao.documentos.ACEITE_FORNECEDOR"}
    msg = {a.campo: a for a in av.erros if a.codigo == "ADESAO_DOC_AUSENTE"}
    assert "art. 86, § 2º, II" in msg["adesao.documentos.PESQUISA_PRECOS"].base_legal
    assert "art. 31" in msg["adesao.documentos.ACEITE_FORNECEDOR"].base_legal
    assert not av.pode_gerar
    parcial = regras.avaliar_requisicao(_carona(documentos=docs_adesao(("TERMO_JUSTIFICATIVA",))))
    assert {a.campo for a in parcial.erros if a.codigo == "ADESAO_DOC_AUSENTE"} == {"adesao.documentos.PESQUISA_PRECOS", "adesao.documentos.ACEITE_FORNECEDOR"}


def test_adesao_autorizacao_da_gerenciadora_e_opcional_e_entra_nos_anexos(repo):
    av = regras.avaliar_requisicao(_carona())
    assert av.erros == [] and [d["tipo"] for d in av.norm["adesao"]["documentos"]] == ["TERMO_JUSTIFICATIVA", "PESQUISA_PRECOS", "ACEITE_FORNECEDOR"]
    todos = ("TERMO_JUSTIFICATIVA", "PESQUISA_PRECOS", "ACEITE_FORNECEDOR", "AUTORIZACAO_GERENCIADORA")
    av = regras.avaliar_requisicao(_carona(documentos=docs_adesao(todos)))
    assert av.erros == [] and av.norm["adesao"]["documentos"][-1]["tipo"] == "AUTORIZACAO_GERENCIADORA"


def test_adesao_id_de_documento_invalido_nunca_vira_caminho(repo):
    ids = docs_adesao()
    for ruim in ("../../segredo", "x" * 32, ids["TERMO_JUSTIFICATIVA"][:-1], 12345, ["a"], "0" * 32):
        av = regras.avaliar_requisicao(_carona(documentos={**ids, "PESQUISA_PRECOS": ruim}))
        assert "ADESAO_DOC_ARQUIVO" in codigos(av, "ERRO"), ruim
    # o id de um tipo não serve para outro tipo (o nome do arquivo leva o tipo)
    trocado = regras.avaliar_requisicao(_carona(documentos={**ids, "PESQUISA_PRECOS": ids["TERMO_JUSTIFICATIVA"]}))
    assert "ADESAO_DOC_ARQUIVO" in codigos(trocado, "ERRO")
    assert not regras.avaliar_requisicao(_carona(documentos="texto")).pode_gerar  # formato errado não derruba a avaliação


def test_documentos_da_adesao_nao_se_aplicam_a_gerenciadora(repo):
    p = payload_requisicao()
    p["adesao"] = {"documentos": {}}
    av = regras.avaliar_requisicao(p)
    assert av.erros == [] and "ADESAO_DOC_AUSENTE" not in codigos(av)


# ----------------------------------------------------------------------------- QA: anulação (centavos e liquidado explícito)
def test_anulacao_exige_liquidado_explicito_e_so_centavos(repo):
    p = payload_anulacao()
    p["empenhos"][0].pop("liquidado")
    assert "LIQUIDADO_OBRIGATORIO" in codigos(regras.avaliar_anulacao(p), "ERRO")
    p["empenhos"][0]["liquidado"] = ""
    assert "LIQUIDADO_OBRIGATORIO" in codigos(regras.avaliar_anulacao(p), "ERRO")
    p["empenhos"][0]["liquidado"] = "0,00"
    assert regras.avaliar_anulacao(p).erros == []
    p["empenhos"][0]["valor"] = "400,005"
    assert "VALOR_CENTAVOS" in codigos(regras.avaliar_anulacao(p), "ERRO")
    p["empenhos"][0].update(valor="400,00", liquidado="10,123")
    assert "VALOR_CENTAVOS" in codigos(regras.avaliar_anulacao(p), "ERRO")


# ----------------------------------------------------------------------------- QA: reforço (preço, item de quantidade 1, ata)
def _editar_json(repo, arquivo, fn):
    import json
    from app import base
    arq = repo["dados"] / arquivo
    d = json.loads(arq.read_text(encoding="utf-8"))
    fn(d)
    arq.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    base.invalidar()


def _ops_ne(repo, ops):
    _editar_json(repo, "nes.json", lambda d: d["nes"]["160329:2026NE000100"]["itens"][0].update(operacoes=ops))


def _op(nome, qtd, vu):
    return {"data": "10/08/2026", "operacao": nome, "quantidade": qtd, "valor_unitario": vu, "valor_total": "0"}


def test_reforco_usa_o_preco_da_ultima_inclusao_ou_reforco_e_nao_o_de_uma_anulacao(repo):
    _ops_ne(repo, [_op("Inclusão", "10.00000", "100.0000"), _op("Reforço", "5.00000", "100.0000"), _op("Anulação", "3.00000", "50.0000")])
    av = regras.avaliar_reforco(payload_reforco())
    assert av.erros == [] and str(av.norm["total"]) == "200.00"  # 2 × 100,00, e não 2 × 50,00 da anulação


def test_reforco_item_de_quantidade_1_aceita_valor(repo):
    _ops_ne(repo, [_op("Inclusão", "1.00000", "1000.0000")])
    av = regras.avaliar_reforco(payload_reforco(itens=[{"seq": 1, "valor": "250,00"}]))
    assert av.erros == [] and str(av.norm["total"]) == "250.00" and str(av.norm["itens"][0]["qtde"]) == "1"
    assert "VALOR_CENTAVOS" in codigos(regras.avaliar_reforco(payload_reforco(itens=[{"seq": 1, "valor": "250,005"}])), "ERRO")
    assert "ITEM_QTDE_E_VALOR" in codigos(regras.avaliar_reforco(payload_reforco(itens=[{"seq": 1, "qtde": "1", "valor": "250,00"}])), "ERRO")


def test_reforco_por_valor_so_vale_para_item_de_quantidade_1(repo):
    av = regras.avaliar_reforco(payload_reforco(itens=[{"seq": 1, "valor": "250,00"}]))  # a NE do teste tem 10 unidades
    assert "ITEM_VALOR_SO_QTD1" in codigos(av, "ERRO")


def test_reforco_confere_saldo_vigencia_e_preco_na_ata(repo):
    assert "QTDE_ACIMA_SALDO" in codigos(regras.avaliar_reforco(payload_reforco(itens=[{"seq": 1, "qtde": "1001"}])), "ERRO")
    _editar_json(repo, "pregoes.json", lambda d: d["pregoes"]["160329:90001/2026:GERENCIADORA"]["itens"][0].update(vigencia_fim="2026-09-01"))
    assert "ATA_VENCIDA_NO_REFORCO" in codigos(regras.avaliar_reforco(payload_reforco()), "ALERTA")
    _editar_json(repo, "pregoes.json", lambda d: d["pregoes"]["160329:90001/2026:GERENCIADORA"]["itens"][0].update(vigencia_fim="2027-06-30", valor_unitario="120.00"))
    assert "PRECO_NE_DIVERGE_ATA" in codigos(regras.avaliar_reforco(payload_reforco()), "ALERTA")


# ----------------------------------------------------------------------------- QA: pequenos defeitos
def test_finalidade_outra_em_maiusculas_e_valida(repo):
    p = payload_requisicao(finalidade={"id": "OUTRA", "outra": "MANUTENCAO DE EQUIPAMENTOS DE COZINHA DA OM"})
    assert regras.avaliar_requisicao(p).erros == []


def test_booleanos_em_texto_nao_viram_verdadeiros(repo):
    p = payload_requisicao(contrato={"possui": "false"})
    av = regras.avaliar_requisicao(p)
    assert av.norm["contrato"]["possui"] is False and av.erros == []
    p = payload_requisicao(pca={"consta": "false", "justificativa": "Demanda surgida após a aprovação do plano"})
    assert regras.avaliar_requisicao(p).norm["pca"]["consta"] is False
    assert regras.avaliar_anulacao(payload_anulacao(consultou_siafi="false")).erros  # "false" não vale como confirmação do SIAFI
    assert "CONFIRMAR_SIAFI" in codigos(regras.avaliar_anulacao(payload_anulacao(consultou_siafi="false")), "ERRO")
    assert "CONFIRMAR_SIAFI" not in codigos(regras.avaliar_anulacao(payload_anulacao(consultou_siafi="true")))
    assert regras.avaliar_requisicao(payload_requisicao(anexar_nc="false")).norm["anexar_nc"] is False


def test_total_do_item_zerado_e_erro(repo):
    p = payload_requisicao()
    p["fornecedores"][0]["itens"][0].update(qtde="0,001", valor_unit="1,00")  # 0,001 × 1,00 = 0,00 em centavos
    assert "ITEM_TOTAL_ZERO" in codigos(regras.avaliar_requisicao(p), "ERRO")


def test_razao_social_truncada_pelo_siafi_e_completada_pela_ata(repo):
    completo = "AM21 AUTO PECAS E MOTORES COMERCIO E SERVICOS LTDA ME"
    assert len(completo) > 45
    def ata(d):
        for reg in d["pregoes"].values():
            for i in reg["itens"]:
                i["fornecedor_nome"] = completo
    _editar_json(repo, "pregoes.json", ata)
    _editar_json(repo, "nes.json", lambda d: d["nes"]["160329:2026NE000100"].update(favorecido_nome=completo[:45]))
    av = regras.avaliar_reforco(payload_reforco())
    assert av.norm["ne"]["favorecido_nome"] == completo and av.norm["ne"]["doc_favorecido"][1] == completo
    # nome de outro CNPJ ou que não é prefixo nunca é trocado
    _editar_json(repo, "nes.json", lambda d: d["nes"]["160329:2026NE000100"].update(favorecido_nome="OUTRA RAZAO SOCIAL QUALQUER COMERCIO LTDA XYZ"))
    assert regras.avaliar_reforco(payload_reforco()).norm["ne"]["favorecido_nome"].startswith("OUTRA RAZAO")
