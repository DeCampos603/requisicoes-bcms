# -*- coding: utf-8 -*-
"""Histórico dos documentos gerados (SQLite local).

Serve a três coisas: (1) detectar DIEx/NUP repetido, (2) registrar a ciência dos alertas que o
usuário aceitou (rastreabilidade), (3) alimentar as sugestões de seção/origem do DIEx.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime

from . import config

_SQL = """
CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    diex TEXT NOT NULL,
    nup TEXT NOT NULL,
    ano INTEGER NOT NULL,
    total TEXT,
    pdf TEXT,
    alertas_ciente TEXT,
    achados TEXT,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS ix_doc_diex ON documentos (diex, ano);
"""


def _con() -> sqlite3.Connection:
    config.DADOS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DADOS / "historico.db", timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(_SQL)
    return con


def registrar(tipo: str, diex: str, nup: str, ano: int, total: str | None, pdf: str, ciente: list[str],
              codigos_achados: list[str]) -> int:
    """Grava só o necessário (minimização, LGPD): identificação do documento, ciência dos alertas e códigos dos achados.
    O conteúdo do formulário (nomes, CNPJ/CPF, itens) NÃO é guardado: está no próprio PDF gerado."""
    with closing(_con()) as con, con:
        cur = con.execute(
            "INSERT INTO documentos (tipo, criado_em, diex, nup, ano, total, pdf, alertas_ciente, achados, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,NULL)",
            (tipo, datetime.now().strftime("%d/%m/%Y %H:%M"), diex, nup, ano, total, pdf,
             json.dumps(ciente, ensure_ascii=False), json.dumps(sorted(set(codigos_achados)), ensure_ascii=False)))
        return int(cur.lastrowid)


def buscar_duplicado(tipo: str, diex: str, nup: str) -> dict | None:
    with closing(_con()) as con:
        r = con.execute("SELECT * FROM documentos WHERE tipo=? AND diex=? AND nup=? ORDER BY id DESC LIMIT 1",
                        (tipo, diex, nup)).fetchone()
        return dict(r) if r else None


def buscar_diex_outro_nup(diex: str, nup: str, ano: int) -> dict | None:
    with closing(_con()) as con:
        r = con.execute("SELECT * FROM documentos WHERE diex=? AND ano=? AND nup<>? ORDER BY id DESC LIMIT 1",
                        (diex, ano, nup)).fetchone()
        return dict(r) if r else None


def listar(limite: int = 30) -> list[dict]:
    with closing(_con()) as con:
        linhas = con.execute("SELECT id, tipo, criado_em, diex, nup, total, pdf FROM documentos ORDER BY id DESC LIMIT ?",
                             (limite,)).fetchall()
        return [dict(r) for r in linhas]


def origens_diex() -> list[str]:
    """Trechos '<Seção>/<Escalão>/<OM>' dos DIEx já gerados + os do catálogo (sugestões do campo)."""
    origens: list[str] = [r["origem"] for r in config.catalogos().get("requisitantes", [])]
    with closing(_con()) as con:
        for r in con.execute("SELECT DISTINCT diex FROM documentos ORDER BY id DESC LIMIT 200"):
            m = re.match(r"\d+-(.+)", r["diex"])
            if m and m.group(1) not in origens:
                origens.append(m.group(1))
    return origens
