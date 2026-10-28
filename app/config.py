# -*- coding: utf-8 -*-
"""Caminhos e configuração do sistema.

Todas as fontes de dados vêm do repositório REPO e podem ser sobrescritas por variáveis
de ambiente (útil nos testes e em outra máquina):

  EMPENHOS_RAIZ        pasta REPO/EMPENHOS (NE, NC e processos escaneados)
  PREGOES_XLSX         Consolidado_Pregoes.xlsx do Robô Extrator (base do dashboard de capacidade)
  CREDITO_HTML         site/index.html do Dashboard-Credito-BCMS (saldos por NC)
  REQEMP_DADOS         pasta dos índices gerados (padrão: <projeto>/dados)
  REQEMP_ANEXOS        pasta dos anexos enviados (padrão: <projeto>/anexos)
  REQEMP_SAIDA         pasta dos PDFs gerados (padrão: <projeto>/saida)
"""

from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from pathlib import Path

# Um só lock para toda gravação de índice/log em dados/ (importação, upload e auditoria podem coincidir).
ARQUIVO_LOCK = threading.RLock()

PROJETO = Path(__file__).resolve().parent.parent
REPO = PROJETO.parent


def _pasta(env: str, padrao: Path) -> Path:
    valor = os.environ.get(env)
    return Path(valor) if valor else padrao


_padrao_empenhos = (
    REPO / "Acervo-Empenhos-BCMS-2026"
    if (REPO / "Acervo-Empenhos-BCMS-2026" / "Empenhos").is_dir()
    else REPO / "EMPENHOS"
)
EMPENHOS_RAIZ = _pasta("EMPENHOS_RAIZ", _padrao_empenhos)
NE_DIRS = {"160329": EMPENHOS_RAIZ / "Empenhos" / "160329", "167329": EMPENHOS_RAIZ / "Empenhos" / "167329"}
NC_DIRS = {"160329": EMPENHOS_RAIZ / "Notas de Crédito" / "160329", "167329": EMPENHOS_RAIZ / "Notas de Crédito" / "167329"}

PREGOES_XLSX = _pasta(
    "PREGOES_XLSX",
    REPO / "RoboExtratorPregoes_V2" / "Consultas realizadas" / "Consolidado_Pregoes.xlsx",
)
CREDITO_HTML = _pasta("CREDITO_HTML", REPO / "Dashboard-Credito-BCMS" / "site" / "index.html")

DADOS = _pasta("REQEMP_DADOS", PROJETO / "dados")
DADOS_NC_DIRS = {"160329": DADOS / "nc" / "160329", "167329": DADOS / "nc" / "167329"}
DADOS_NE_DIRS = {"160329": DADOS / "ne" / "160329", "167329": DADOS / "ne" / "167329"}

ANEXOS = _pasta("REQEMP_ANEXOS", PROJETO / "anexos")
SAIDA = _pasta("REQEMP_SAIDA", PROJETO / "saida")
CONFIG = PROJETO / "config"
ASSETS = PROJETO / "assets"
STATIC = PROJETO / "static"

# Onde o upload grava. Por padrão, direto nas pastas oficiais do repositório (EMPENHOS/Notas de Crédito/<UG> e
# EMPENHOS/Empenhos/<UG>), com a mesma convenção de nome do restante — assim a NC/NE enviada passa a existir "na base
# do repositório" para todos. No Docker/Cloud Run (sem EMPENHOS_RAIZ acessível), grava direto em dados/nc e dados/ne.
if os.environ.get("REQEMP_UPLOADS_EM", "").lower() == "local" or not EMPENHOS_RAIZ.is_dir():
    UPLOAD_NC_DIRS = {ug: DADOS_NC_DIRS[ug] for ug in NC_DIRS}
    UPLOAD_NE_DIRS = {ug: DADOS_NE_DIRS[ug] for ug in NE_DIRS}
else:
    UPLOAD_NC_DIRS = dict(NC_DIRS)
    UPLOAD_NE_DIRS = dict(NE_DIRS)

TAMANHO_MAX_UPLOAD = 15 * 1024 * 1024  # 15 MB


@lru_cache(maxsize=1)
def unidade() -> dict:
    return json.loads((CONFIG / "unidade.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def catalogos() -> dict:
    return json.loads((CONFIG / "catalogos.json").read_text(encoding="utf-8"))


def limpar_cache_config() -> None:
    unidade.cache_clear()
    catalogos.cache_clear()
