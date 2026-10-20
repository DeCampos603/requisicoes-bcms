# -*- coding: utf-8 -*-
"""Módulo de Geração de Relatório Executivo de Pregões Participantes (BCMS — UASG 160329).

Consolida e cruza:
  1. Pregões Homologados com Ata (ARP) ativa ou vencida (fonte: dados/pregoes.json / Comprasnet Contratos)
  2. Pregões em Andamento / Não Homologados (fonte: PNCP - Portal Nacional de Contratações Públicas, Lei 14.133/2021)
  3. Alertas Gerenciais (atas próximas ao vencimento, saldos zerados, oportunidades de participação)

Gera pasta de trabalho Excel (.xlsx) com 5 abas altamente estilizadas:
  - Painel Resumo & KPIs (Dashboard executivo)
  - Pregões em Andamento (PNCP)
  - Pregões Homologados (Atas)
  - Itens Detalhados (BCMS)
  - Cruzamento & Alertas
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import time
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import config
from .dinheiro import ZERO, ValorInvalido, brl, fmt_num, fmt_qtd, parse_decimal, total_item

log = logging.getLogger("relatorio_participantes")

UASG_BCMS = "160329"

# UASGs Gerenciadoras parceiras do ecossistema militar (1ª RM / COLOG)
UASGS_PARCEIRAS = [
    ("160238", "Base de Administração e Apoio do CML (BA Ap Log Ex)"),
    ("160246", "Depósito Central de Armamento (DCA)"),
    ("160200", "Parque Regional de Manutenção da 1ª RM (Pq R Mnt/1)"),
    ("160040", "Batalhão de Manutenção e Armamento Pesado"),
    ("160242", "1º Depósito de Suprimento (1º D Sup)"),
    ("160251", "Hospital Militar de Área do Rio de Janeiro (HMARJ)"),
    ("160254", "1º Batalhão de Infantaria Mecanizado (1º BI Mec)"),
    ("160263", "11º Grupo de Artilharia de Campanha (11º GAC)"),
    ("160264", "1º Batalhão de Polícia do Exército (1º BPE)"),
    ("160270", "Centro de Estudos de Pessoal (CEP)"),
    ("160274", "25º Batalhão Logístico (25º B Log)"),
    ("160277", "1º Batalhão de Engenharia de Combate (1º BE Cmb)"),
    ("160279", "1º Batalhão de Comunicações (1º B Com)"),
    ("160296", "Centro de Preparação de Oficiais da Reserva (CPOR/RJ)"),
    ("160297", "Comando da 1ª Divisão de Exército (Cmdo 1ª DE)"),
    ("160298", "Comando da 1ª Região Militar (Cmdo 1ª RM)"),
    ("160304", "Base de Material de Subsistência de Anápolis/Rio (BMSA)"),
    ("160307", "2º Centro de Telemática de Área (2º CTA)"),
    ("160311", "Hospital Central do Exército (HCE)"),
    ("160315", "Centro de Capacitação Física do Exército (CCFEx)"),
    ("160450", "Diretoria de Abastecimento (D Abst)"),
    ("160463", "Diretoria de Material (D Mat)"),
    ("160486", "Arsenal de Guerra do Rio (AGR)"),
    ("160329", "Batalhão Central de Manutenção e Suprimento (BCMS)"),
]

# Estilos Visuais
AZUL_ESCURO = "1B365D"   # Cabeçalhos principais
AZUL_MEDIO = "2E5B88"    # Subcabeçalhos
AZUL_CLARO = "EBF2FA"    # Zebras e destaques suaves
VERDE_MILITAR = "007A3D" # Homologado / Vigente
VERDE_CLARO = "E8F8F0"   # Fundo de status positivo
AMARELO_ALERTA = "D4AC0D"# Atenção / Julgamento
AMARELO_CLARO = "FEF9E7" # Fundo de atenção
VERMELHO_PERIGO = "C0392B" # Vencido / Anulado
VERMELHO_CLARO = "FDEDEC"# Fundo de perigo
CINZA_BORDA = "D5D8DC"   # Linhas de grade sutis
CINZA_TEXTO = "566573"   # Metadados e textos secundários

FONT_TITULO = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
FONT_SUBTITULO = Font(name="Calibri", size=11, bold=True, color="EAECEE")
FONT_HEADER = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
FONT_CORPO = Font(name="Calibri", size=10, color="000000")
FONT_CORPO_BOLD = Font(name="Calibri", size=10, bold=True, color="000000")
FONT_KPI_VALOR = Font(name="Calibri", size=16, bold=True, color=AZUL_ESCURO)
FONT_KPI_TITULO = Font(name="Calibri", size=9, bold=True, color=CINZA_TEXTO)
FONT_LINK = Font(name="Calibri", size=10, color="0044CC", underline="single")

FILL_TITULO = PatternFill("solid", fgColor=AZUL_ESCURO)
FILL_HEADER = PatternFill("solid", fgColor=AZUL_MEDIO)
FILL_ZEBRA = PatternFill("solid", fgColor="F8F9FA")
FILL_KPI_BG = PatternFill("solid", fgColor="F4F6F9")
FILL_VERDE = PatternFill("solid", fgColor=VERDE_CLARO)
FILL_AMARELO = PatternFill("solid", fgColor=AMARELO_CLARO)
FILL_VERMELHO = PatternFill("solid", fgColor=VERMELHO_CLARO)

BORDA_FINA = Border(
    left=Side(style="thin", color=CINZA_BORDA),
    right=Side(style="thin", color=CINZA_BORDA),
    top=Side(style="thin", color=CINZA_BORDA),
    bottom=Side(style="thin", color=CINZA_BORDA),
)
BORDA_KPI = Border(
    left=Side(style="medium", color=AZUL_MEDIO),
    right=Side(style="thin", color=CINZA_BORDA),
    top=Side(style="thin", color=CINZA_BORDA),
    bottom=Side(style="thin", color=CINZA_BORDA),
)


def _dias_restantes(data_iso: str | None) -> int | None:
    if not data_iso:
        return None
    try:
        dt = datetime.strptime(data_iso[:10], "%Y-%m-%d").date()
        return (dt - date.today()).days
    except Exception:
        return None


def _formatar_cnpj(cnpj: str | None) -> str:
    if not cnpj:
        return ""
    c = re.sub(r"\D", "", cnpj)
    if len(c) == 14:
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
    return cnpj


def carregar_catalogo_uasgs() -> dict[str, str]:
    """Mapeamento código UASG -> Nome / Sigla Oficial."""
    uasgs: dict[str, str] = {cod: nome for cod, nome in UASGS_PARCEIRAS}
    arq = config.DADOS / "uasgs.json"
    if arq.is_file():
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
            for cod, info in d.get("uasgs", {}).items():
                nome = info.get("sigla") or info.get("nome")
                if nome:
                    uasgs[cod] = nome
        except Exception as e:
            log.warning("Falha ao ler uasgs.json: %s", e)
    return uasgs


def carregar_pregoes_homologados() -> tuple[list[dict], list[dict]]:
    """Carrega dados locais de pregões homologados onde o BCMS é participante.

    Retorna: (lista_pregoes_sinteticos, lista_itens_detalhados)
    """
    arq = config.DADOS / "pregoes.json"
    if not arq.is_file():
        return [], []

    try:
        dados = json.loads(arq.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Erro ao abrir pregoes.json: %s", e)
        return [], []

    uasgs_map = carregar_catalogo_uasgs()
    pregoes_brutos = dados.get("pregoes", {})

    pregoes_sinteticos: list[dict] = []
    itens_detalhados: list[dict] = []

    for chave, p in pregoes_brutos.items():
        if p.get("tipo_unidade") != "PARTICIPANTE" and not chave.endswith(":PARTICIPANTE"):
            continue

        ugg = p.get("ugg", "")
        numero = p.get("numero", "")
        om_nome = uasgs_map.get(ugg, f"OM {ugg}")
        itens = p.get("itens", [])

        # Acumuladores do pregão
        v_cota_bcms_tot = ZERO
        v_consumido_bcms_tot = ZERO
        v_saldo_bcms_tot = ZERO
        v_global_ata_tot = ZERO
        atas_set = set()
        fornecedores_set = set()
        vigencias_fim = []

        for it in itens:
            vu = parse_decimal(it.get("valor_unitario") or "0")
            q_hom_global = parse_decimal(it.get("qtd_homologada") or "0")
            q_cota_bcms = parse_decimal(it.get("qtd_autorizada") or "0")
            q_sal_bcms = parse_decimal(it.get("qtd_saldo") or "0")
            q_cons_bcms = max(ZERO, q_cota_bcms - q_sal_bcms)

            v_global = total_item(q_hom_global, vu)
            v_cota = total_item(q_cota_bcms, vu)
            v_sal = total_item(q_sal_bcms, vu)
            v_cons = total_item(q_cons_bcms, vu)

            v_global_ata_tot += v_global
            v_cota_bcms_tot += v_cota
            v_saldo_bcms_tot += v_sal
            v_consumido_bcms_tot += v_cons

            ata_num = it.get("ata") or "-"
            if ata_num != "-":
                atas_set.add(ata_num)

            forn_nome = it.get("fornecedor_nome") or ""
            forn_cnpj = _formatar_cnpj(it.get("fornecedor_cnpj"))
            if forn_nome:
                fornecedores_set.add(f"{forn_nome} ({forn_cnpj})" if forn_cnpj else forn_nome)

            v_fim = it.get("vigencia_fim")
            if v_fim:
                vigencias_fim.append(v_fim)

            dias_rest = _dias_restantes(v_fim)
            if dias_rest is None:
                status_vig = "Sem Vigência"
            elif dias_rest < 0:
                status_vig = f"Vencida ({abs(dias_rest)}d atrás)"
            elif dias_rest <= 60:
                status_vig = f"Vence em {dias_rest}d"
            else:
                status_vig = f"Vigente ({dias_rest}d)"

            itens_detalhados.append({
                "ugg": ugg,
                "om_nome": om_nome,
                "pregao": numero,
                "ata": ata_num,
                "item": it.get("item"),
                "descricao": it.get("descricao", ""),
                "catmat": it.get("catmat") or "-",
                "catmat_nome": it.get("catmat_nome") or "-",
                "catalogo": it.get("catalogo") or "Material",
                "categoria": it.get("categoria") or "Geral",
                "nd_sugerida": it.get("nd_sugerida") or "-",
                "subitem_sugerido": it.get("subitem_sugerido") or "-",
                "fornecedor_cnpj": forn_cnpj,
                "fornecedor_nome": forn_nome,
                "vigencia_ini": it.get("vigencia_ini") or "-",
                "vigencia_fim": v_fim or "-",
                "dias_restantes": dias_rest,
                "status_vigencia": status_vig,
                "valor_unitario": float(vu),
                "qtd_homologada_global": float(q_hom_global),
                "qtd_cota_bcms": float(q_cota_bcms),
                "qtd_consumida_bcms": float(q_cons_bcms),
                "qtd_saldo_bcms": float(q_sal_bcms),
                "valor_global_ata": float(v_global),
                "valor_cota_bcms": float(v_cota),
                "valor_consumido_bcms": float(v_cons),
                "valor_saldo_bcms": float(v_sal),
                "link": it.get("link") or "",
            })

        vig_min = p.get("vigencia_fim_min")
        vig_max = p.get("vigencia_fim_max") or (max(vigencias_fim) if vigencias_fim else None)
        dias_max = _dias_restantes(vig_max)

        if dias_max is None:
            situacao_arp = "Indefinida"
            badge_cor = "CINZA"
        elif dias_max < 0:
            situacao_arp = f"Vencida ({abs(dias_max)}d atrás)"
            badge_cor = "VERMELHO"
        elif dias_max <= 60:
            situacao_arp = f"Vence em {dias_max}d (Atenção)"
            badge_cor = "AMARELO"
        else:
            situacao_arp = f"Vigente ({dias_max}d)"
            badge_cor = "VERDE"

        pct_exec = float((v_consumido_bcms_tot / v_cota_bcms_tot * 100).quantize(Decimal("0.1"))) if v_cota_bcms_tot > ZERO else 0.0

        pregoes_sinteticos.append({
            "ugg": ugg,
            "om_nome": om_nome,
            "pregao": numero,
            "atas": ", ".join(sorted(atas_set)) if atas_set else "Pendente",
            "n_itens": len(itens),
            "fornecedores": "; ".join(sorted(fornecedores_set)[:3]) + (f" (+{len(fornecedores_set)-3})" if len(fornecedores_set) > 3 else ""),
            "vigencia_fim": vig_max or "-",
            "dias_restantes": dias_max,
            "status_vigencia": situacao_arp,
            "badge_cor": badge_cor,
            "valor_cota_bcms": float(v_cota_bcms_tot),
            "valor_consumido_bcms": float(v_consumido_bcms_tot),
            "valor_saldo_bcms": float(v_saldo_bcms_tot),
            "valor_global_ata": float(v_global_ata_tot),
            "pct_execucao": pct_exec,
            "link": itens[0].get("link") if itens else "",
        })

    pregoes_sinteticos.sort(key=lambda x: (x["dias_restantes"] is not None and x["dias_restantes"] < 0, -(x["dias_restantes"] or -9999)))
    itens_detalhados.sort(key=lambda x: (x["ugg"], x["pregao"], x["item"] if isinstance(x["item"], int) else 9999))
    return pregoes_sinteticos, itens_detalhados


def consultar_pncp_em_andamento(forcar_atualizacao: bool = False, progresso: Callable[[str], None] = print) -> list[dict]:
    """Consulta licitações e pregões das OMs parceiras no PNCP (Lei 14.133/2021).

    Utiliza cache local em dados/pncp_cache.json (validade 24h a menos que forcar_atualizacao=True).
    """
    cache_path = config.DADOS / "pncp_cache.json"

    # Verifica cache existente
    if not forcar_atualizacao and cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            idade_h = (time.time() - cache.get("timestamp", 0)) / 3600
            if idade_h < 24 and cache.get("pregoes"):
                progresso(f"  PNCP: carregado do cache local ({len(cache['pregoes'])} contratações, idade {idade_h:.1f}h).")
                return cache["pregoes"]
        except Exception as e:
            log.warning("Falha ao ler cache PNCP: %s", e)

    progresso("  Consultando API pública do PNCP para 24 OMs parceiras (anos 2026/2025)...")
    uasgs_map = carregar_catalogo_uasgs()
    tarefas = []

    ano_atual = date.today().year
    hoje_iso = date.today().strftime("%Y%m%d")

    for uasg, nome in UASGS_PARCEIRAS:
        # Ano corrente
        tarefas.append((uasg, nome, str(ano_atual), f"{ano_atual}0101", hoje_iso))
        # Ano anterior (muitos pregões vigentes foram abertos no ano anterior)
        ano_ant = ano_atual - 1
        tarefas.append((uasg, nome, str(ano_ant), f"{ano_ant}0101", f"{ano_ant}1231"))

    def _consultar_uasg(args):
        u, n, ano, d_ini, d_fim = args
        url = (f"https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao?"
               f"dataInicial={d_ini}&dataFinal={d_fim}&codigoModalidadeContratacao=6&"
               f"cnpj=00394452000103&codigoUnidadeAdministrativa={u}&pagina=1&tamanhoPagina=20")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"})
        compras = []
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                d = json.loads(resp.read())
                for c in d.get("data", []):
                    seq = c.get("numeroSequencialCompra")
                    ano_c = c.get("anoCompra")
                    link_edital = f"https://pncp.gov.br/app/editais/00394452000103/{ano_c}/{seq}" if (seq and ano_c) else (c.get("linkSistemaOrigem") or "")
                    compras.append({
                        "ugg": u,
                        "om_nome": uasgs_map.get(u, n),
                        "numero": c.get("numeroCompra"),
                        "ano": ano_c,
                        "processo": c.get("processo") or "-",
                        "situacao": c.get("situacaoCompraNome") or "Publicada",
                        "srp": bool(c.get("srp")),
                        "objeto": c.get("objetoCompra") or "",
                        "valor_estimado": float(parse_decimal(c.get("valorTotalEstimado") or "0")),
                        "valor_homologado": float(parse_decimal(c.get("valorTotalHomologado") or "0")),
                        "data_publicacao": c.get("dataPublicacaoPncp")[:10] if c.get("dataPublicacaoPncp") else "-",
                        "data_abertura": c.get("dataAberturaProposta")[:16].replace("T", " ") if c.get("dataAberturaProposta") else "-",
                        "data_encerramento": c.get("dataEncerramentoProposta")[:16].replace("T", " ") if c.get("dataEncerramentoProposta") else "-",
                        "link_pncp": link_edital,
                        "link_origem": c.get("linkSistemaOrigem") or "",
                    })
        except Exception:
            pass  # timeout ou uasg sem licitação no ano é esperado
        return compras

    t0 = time.time()
    todos_pregoes: list[dict] = []
    # Concorrência rápida com 8 threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for lotes in executor.map(_consultar_uasg, tarefas):
            todos_pregoes.extend(lotes)

    progresso(f"  PNCP: {len(todos_pregoes)} licitações catalogadas em {time.time() - t0:.1f}s.")

    # Remove duplicados por chave (ugg:numero/ano)
    vistos = set()
    unicos = []
    for p in todos_pregoes:
        chave = f"{p['ugg']}:{p['numero']}/{p['ano']}"
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(p)

    # Grava cache local atômico
    try:
        cache_data = {"timestamp": time.time(), "data_consulta": datetime.now().strftime("%d/%m/%Y %H:%M"), "total": len(unicos), "pregoes": unicos}
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning("Falha ao gravar cache PNCP: %s", e)

    return unicos


def consolidar_dados(atualizar_pncp: bool = False, progresso: Callable[[str], None] = print) -> dict[str, Any]:
    """Consolida todas as informações de pregões homologados, itens e pregões em andamento."""
    pregoes_homologados, itens_detalhados = carregar_pregoes_homologados()
    pncp_bruto = consultar_pncp_em_andamento(forcar_atualizacao=atualizar_pncp, progresso=progresso)

    # Indexa os pregões homologados por chave flexível
    homologados_map = {}
    for p in pregoes_homologados:
        num_limpo = str(p["pregao"]).replace("_", "/").strip()
        homologados_map[f"{p['ugg']}:{num_limpo}"] = p
        # Variação sem zero à esquerda
        p_ano = num_limpo.split("/")
        if len(p_ano) == 2:
            homologados_map[f"{p['ugg']}:{int(p_ano[0]) if p_ano[0].isdigit() else p_ano[0]}/{p_ano[1]}"] = p

    # Separa pregões do PNCP em:
    # 1. Já homologados com Ata ativa na nossa base
    # 2. Em andamento / Não homologados (foco operacional)
    pncp_em_andamento: list[dict] = []
    pncp_ja_homologados: list[dict] = []

    for c in pncp_bruto:
        num_pncp = f"{c['numero']}/{c['ano']}"
        chave = f"{c['ugg']}:{num_pncp}"
        chave_int = f"{c['ugg']}:{int(c['numero']) if str(c['numero']).isdigit() else c['numero']}/{c['ano']}"

        ja_hom = homologados_map.get(chave) or homologados_map.get(chave_int)
        c_com_status = dict(c)

        if ja_hom:
            c_com_status["status_bcms"] = f"Homologado c/ Ata ({ja_hom['status_vigencia']})"
            c_com_status["ata_local"] = ja_hom["atas"]
            c_com_status["valor_bcms"] = ja_hom["valor_cota_bcms"]
            c_com_status["saldo_bcms"] = ja_hom["valor_saldo_bcms"]
            pncp_ja_homologados.append(c_com_status)
        else:
            sit = c["situacao"].lower()
            if "homolog" in sit or "adjudic" in sit:
                c_com_status["status_bcms"] = "Homologado no PNCP (Aguardando Registro de Ata)"
            elif "julgamento" in sit or "habilita" in sit:
                c_com_status["status_bcms"] = "Em Julgamento / Análise de Propostas"
            elif "proposta" in sit:
                c_com_status["status_bcms"] = "Recebendo Propostas"
            elif "disputa" in sit or "abert" in sit:
                c_com_status["status_bcms"] = "Em Disputa (Sessão Pública)"
            elif "suspens" in sit:
                c_com_status["status_bcms"] = "Licitação Suspensa"
            else:
                c_com_status["status_bcms"] = f"Em Tramitação ({c['situacao']})"

            c_com_status["ata_local"] = "-"
            c_com_status["valor_bcms"] = 0.0
            c_com_status["saldo_bcms"] = 0.0
            pncp_em_andamento.append(c_com_status)

    # Ordena pregões em andamento por data de abertura mais recente
    pncp_em_andamento.sort(key=lambda x: str(x.get("data_abertura") or x.get("data_publicacao") or ""), reverse=True)

    # Cálculo dos KPIs Globais
    tot_homologados = len(pregoes_homologados)
    tot_andamento = len(pncp_em_andamento)
    tot_mapeados = tot_homologados + tot_andamento
    tot_itens = len(itens_detalhados)

    v_cota_geral = sum(p["valor_cota_bcms"] for p in pregoes_homologados)
    v_consumido_geral = sum(p["valor_consumido_bcms"] for p in pregoes_homologados)
    v_saldo_geral = sum(p["valor_saldo_bcms"] for p in pregoes_homologados)
    v_global_geral = sum(p["valor_global_ata"] for p in pregoes_homologados)
    pct_saldo_consumido = (v_consumido_geral / v_cota_geral * 100) if v_cota_geral > 0 else 0.0

    # Contagem de status de vigência das atas
    atas_vigentes = sum(1 for p in pregoes_homologados if "Vigente" in p["status_vigencia"])
    atas_atencao = sum(1 for p in pregoes_homologados if "Atenção" in p["status_vigencia"])
    atas_vencidas = sum(1 for p in pregoes_homologados if "Vencida" in p["status_vigencia"])

    # Agrupamento sintético por OM Gerenciadora
    sintese_oms: dict[str, dict] = {}
    for p in pregoes_homologados:
        reg = sintese_oms.setdefault(p["ugg"], {
            "ugg": p["ugg"], "om_nome": p["om_nome"],
            "pregoes_hom": 0, "pregoes_andamento": 0, "itens_bcms": 0,
            "valor_cota_bcms": 0.0, "valor_consumido_bcms": 0.0, "valor_saldo_bcms": 0.0, "valor_global_ata": 0.0,
        })
        reg["pregoes_hom"] += 1
        reg["itens_bcms"] += p["n_itens"]
        reg["valor_cota_bcms"] += p["valor_cota_bcms"]
        reg["valor_consumido_bcms"] += p["valor_consumido_bcms"]
        reg["valor_saldo_bcms"] += p["valor_saldo_bcms"]
        reg["valor_global_ata"] += p["valor_global_ata"]

    for p in pncp_em_andamento:
        reg = sintese_oms.setdefault(p["ugg"], {
            "ugg": p["ugg"], "om_nome": p["om_nome"],
            "pregoes_hom": 0, "pregoes_andamento": 0, "itens_bcms": 0,
            "valor_cota_bcms": 0.0, "valor_consumido_bcms": 0.0, "valor_saldo_bcms": 0.0, "valor_global_ata": 0.0,
        })
        reg["pregoes_andamento"] += 1

    tabela_oms = sorted(sintese_oms.values(), key=lambda x: -(x["valor_cota_bcms"] + x["pregoes_andamento"] * 10000))

    # Alertas Gerenciais
    alertas_vencendo = [p for p in pregoes_homologados if p["dias_restantes"] is not None and 0 <= p["dias_restantes"] <= 60]
    alertas_saldo_zerado = [p for p in pregoes_homologados if p["valor_saldo_bcms"] == 0.0 and p["valor_cota_bcms"] > 0]

    return {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "kpis": {
            "total_pregoes_mapeados": tot_mapeados,
            "total_pregoes_homologados": tot_homologados,
            "total_pregoes_andamento": tot_andamento,
            "atas_vigentes": atas_vigentes,
            "atas_atencao": atas_atencao,
            "atas_vencidas": atas_vencidas,
            "total_itens_bcms": tot_itens,
            "valor_cota_geral": v_cota_geral,
            "valor_consumido_geral": v_consumido_geral,
            "valor_saldo_geral": v_saldo_geral,
            "valor_global_geral": v_global_geral,
            "pct_saldo_consumido": pct_saldo_consumido,
        },
        "pregoes_homologados": pregoes_homologados,
        "itens_detalhados": itens_detalhados,
        "pncp_em_andamento": pncp_em_andamento,
        "pncp_ja_homologados": pncp_ja_homologados,
        "tabela_oms": tabela_oms,
        "alertas_vencendo": alertas_vencendo,
        "alertas_saldo_zerado": alertas_saldo_zerado,
    }


def _ajustar_largura_colunas(ws, max_largura: int = 55):
    """Ajusta automaticamente a largura das colunas mantendo limites estéticos."""
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val = str(cell.value or "")
            if cell.number_format and "R$" in cell.number_format:
                val = f"R$ {val}00,00"
            lines = val.split("\n")
            max_line = max(len(l) for l in lines) if lines else 0
            if max_line > max_len:
                max_len = max_line
        largura = min(max(max_len + 3, 10), max_largura)
        ws.column_dimensions[col_letter].width = largura


def gerar_excel(dados: dict[str, Any], caminho_saida: Path | None = None) -> Path:
    """Monta e estiliza a pasta de trabalho Excel (.xlsx) completa com 5 abas."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove a aba padrão 'Sheet'

    # =========================================================================
    # ABA 1: PAINEL RESUMO & KPIS
    # =========================================================================
    ws1 = wb.create_sheet(title="Painel Resumo & KPIs")
    ws1.views.sheetView[0].showGridLines = True

    # Banner de Título
    ws1.merge_cells("A1:H1")
    cell_t1 = ws1["A1"]
    cell_t1.value = "BATALHÃO CENTRAL DE MANUTENÇÃO E SUPRIMENTO (UASG 160329)"
    cell_t1.font = FONT_TITULO
    cell_t1.fill = FILL_TITULO
    cell_t1.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[1].height = 32

    # Banner de Subtítulo
    ws1.merge_cells("A2:H2")
    cell_t2 = ws1["A2"]
    cell_t2.value = f"RELATÓRIO GERENCIAL DE PREGÕES COMO ÓRGÃO PARTICIPANTE (SRP) — Gerado em {dados['gerado_em']}"
    cell_t2.font = FONT_SUBTITULO
    cell_t2.fill = FILL_HEADER
    cell_t2.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[2].height = 24

    ws1.row_dimensions[3].height = 10  # espaçador

    # Cards de KPIs (Linhas 4 e 5)
    kpis = dados["kpis"]
    kpi_cards = [
        ("Total Pregões Mapeados", f"{kpis['total_pregoes_mapeados']}", "Homologados + Andamento", "A", "B"),
        ("Pregões Homologados (Atas)", f"{kpis['total_pregoes_homologados']}", f"{kpis['atas_vigentes']} vigentes | {kpis['atas_atencao']} atenção", "C", "D"),
        ("Pregões em Andamento (PNCP)", f"{kpis['total_pregoes_andamento']}", "Disputa / Julgamento / Propostas", "E", "F"),
        ("Total de Itens BCMS", f"{kpis['total_itens_bcms']:,}".replace(",", "."), "Itens com cota homologada", "G", "H"),
    ]

    for tit, val, sub, c_ini, c_fim in kpi_cards:
        ws1.merge_cells(f"{c_ini}4:{c_fim}4")
        ws1.merge_cells(f"{c_ini}5:{c_fim}5")

        c_top = ws1[f"{c_ini}4"]
        c_top.value = val
        c_top.font = FONT_KPI_VALOR
        c_top.alignment = Alignment(horizontal="center", vertical="center")
        c_top.fill = FILL_KPI_BG

        c_bot = ws1[f"{c_ini}5"]
        c_bot.value = f"{tit.upper()}\n({sub})"
        c_bot.font = FONT_KPI_TITULO
        c_bot.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c_bot.fill = FILL_KPI_BG

        # Bordas
        for row in range(4, 6):
            for col_idx in range(openpyxl.utils.column_index_from_string(c_ini), openpyxl.utils.column_index_from_string(c_fim) + 1):
                ws1.cell(row=row, column=col_idx).border = BORDA_FINA

    ws1.row_dimensions[4].height = 30
    ws1.row_dimensions[5].height = 28
    ws1.row_dimensions[6].height = 10  # espaçador

    # Linha 7 e 8: Cards Financeiros
    kpi_fin = [
        ("Cota Inicial Autorizada BCMS", f"R$ {fmt_num(Decimal(str(kpis['valor_cota_geral'])), 2)}", "100% da cota contratada", "A", "C"),
        ("Saldo Restante Disponível BCMS", f"R$ {fmt_num(Decimal(str(kpis['valor_saldo_geral'])), 2)}", f"Restante ({100 - kpis['pct_saldo_consumido']:.1f}% livre)", "D", "F"),
        ("Execução / Consumo de Saldo", f"{kpis['pct_saldo_consumido']:.1f}%", f"R$ {fmt_num(Decimal(str(kpis['valor_consumido_geral'])), 2)} empenhados", "G", "H"),
    ]

    for tit, val, sub, c_ini, c_fim in kpi_fin:
        ws1.merge_cells(f"{c_ini}7:{c_fim}7")
        ws1.merge_cells(f"{c_ini}8:{c_fim}8")

        c_top = ws1[f"{c_ini}7"]
        c_top.value = val
        c_top.font = Font(name="Calibri", size=15, bold=True, color=VERDE_MILITAR if "Saldo" in tit else AZUL_ESCURO)
        c_top.alignment = Alignment(horizontal="center", vertical="center")
        c_top.fill = FILL_KPI_BG

        c_bot = ws1[f"{c_ini}8"]
        c_bot.value = f"{tit.upper()}\n({sub})"
        c_bot.font = FONT_KPI_TITULO
        c_bot.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c_bot.fill = FILL_KPI_BG

        for row in range(7, 9):
            for col_idx in range(openpyxl.utils.column_index_from_string(c_ini), openpyxl.utils.column_index_from_string(c_fim) + 1):
                ws1.cell(row=row, column=col_idx).border = BORDA_FINA

    ws1.row_dimensions[7].height = 30
    ws1.row_dimensions[8].height = 28
    ws1.row_dimensions[9].height = 16

    # Seção Síntese por OM Gerenciadora
    ws1.merge_cells("A10:H10")
    c_sec = ws1["A10"]
    c_sec.value = "SÍNTESE POR ORGANIZAÇÃO MILITAR GERENCIADORA (PARCEIROS SRP)"
    c_sec.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    c_sec.fill = FILL_HEADER
    c_sec.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws1.row_dimensions[10].height = 22

    cab_oms = ["UASG", "Organização Militar Gerenciadora", "Pregões Homologados", "Pregões em Andamento", "Itens BCMS", "Cota BCMS (R$)", "Saldo Disponível (R$)", "% Consumido"]
    for i, h in enumerate(cab_oms, 1):
        c = ws1.cell(row=11, column=i, value=h)
        c.font = FONT_HEADER
        c.fill = PatternFill("solid", fgColor=AZUL_ESCURO)
        c.alignment = Alignment(horizontal="center" if i not in (2,) else "left", vertical="center")
        c.border = BORDA_FINA
    ws1.row_dimensions[11].height = 22

    linha = 12
    for om in dados["tabela_oms"]:
        pct = (om["valor_consumido_bcms"] / om["valor_cota_bcms"] * 100) if om["valor_cota_bcms"] > 0 else 0.0
        ws1.cell(row=linha, column=1, value=om["ugg"]).alignment = Alignment(horizontal="center")
        ws1.cell(row=linha, column=2, value=om["om_nome"]).alignment = Alignment(horizontal="left")
        ws1.cell(row=linha, column=3, value=om["pregoes_hom"]).alignment = Alignment(horizontal="center")
        ws1.cell(row=linha, column=4, value=om["pregoes_andamento"]).alignment = Alignment(horizontal="center")
        ws1.cell(row=linha, column=5, value=om["itens_bcms"]).alignment = Alignment(horizontal="center")

        c6 = ws1.cell(row=linha, column=6, value=om["valor_cota_bcms"])
        c6.number_format = 'R$ #,##0.00'
        c6.alignment = Alignment(horizontal="right")

        c7 = ws1.cell(row=linha, column=7, value=om["valor_saldo_bcms"])
        c7.number_format = 'R$ #,##0.00'
        c7.alignment = Alignment(horizontal="right")

        c8 = ws1.cell(row=linha, column=8, value=pct / 100)
        c8.number_format = '0.0%'
        c8.alignment = Alignment(horizontal="center")

        fill_linha = FILL_ZEBRA if linha % 2 == 0 else PatternFill(fill_type=None)
        for col_i in range(1, 9):
            cell = ws1.cell(row=linha, column=col_i)
            cell.font = FONT_CORPO
            cell.border = BORDA_FINA
            if fill_linha.fill_type:
                cell.fill = fill_linha

        linha += 1

    # Linha Totalizadora
    ws1.cell(row=linha, column=1, value="TOTAL").alignment = Alignment(horizontal="center")
    ws1.cell(row=linha, column=2, value=f"{len(dados['tabela_oms'])} Organizações Militares Parceiras").alignment = Alignment(horizontal="left")
    ws1.cell(row=linha, column=3, value=f"=SUM(C12:C{linha-1})").alignment = Alignment(horizontal="center")
    ws1.cell(row=linha, column=4, value=f"=SUM(D12:D{linha-1})").alignment = Alignment(horizontal="center")
    ws1.cell(row=linha, column=5, value=f"=SUM(E12:E{linha-1})").alignment = Alignment(horizontal="center")

    c_tot_hom = ws1.cell(row=linha, column=6, value=f"=SUM(F12:F{linha-1})")
    c_tot_hom.number_format = 'R$ #,##0.00'
    c_tot_hom.alignment = Alignment(horizontal="right")

    c_tot_sal = ws1.cell(row=linha, column=7, value=f"=SUM(G12:G{linha-1})")
    c_tot_sal.number_format = 'R$ #,##0.00'
    c_tot_sal.alignment = Alignment(horizontal="right")

    c_tot_pct = ws1.cell(row=linha, column=8, value=f"=(F{linha}-G{linha})/F{linha}")
    c_tot_pct.number_format = '0.0%'
    c_tot_pct.alignment = Alignment(horizontal="center")

    for col_i in range(1, 9):
        cell = ws1.cell(row=linha, column=col_i)
        cell.font = FONT_CORPO_BOLD
        cell.fill = PatternFill("solid", fgColor="E5E7E9")
        cell.border = Border(top=Side(style="double", color=AZUL_ESCURO), bottom=Side(style="double", color=AZUL_ESCURO), left=Side(style="thin", color=CINZA_BORDA), right=Side(style="thin", color=CINZA_BORDA))
    ws1.row_dimensions[linha].height = 24

    _ajustar_largura_colunas(ws1)

    # =========================================================================
    # ABA 2: PREGÕES EM ANDAMENTO (PNCP)
    # =========================================================================
    ws2 = wb.create_sheet(title="Pregões em Andamento (PNCP)")
    ws2.views.sheetView[0].showGridLines = True
    ws2.freeze_panes = "A2"

    cab_andamento = [
        "UASG", "OM Gerenciadora", "Pregão", "Ano", "NUP / Processo", "Fase / Situação Atual",
        "É SRP?", "Objeto da Contratação", "Data Publicação", "Abertura Propostas",
        "Limite Propostas", "Valor Total Estimado (R$)", "Situação no BCMS", "Link PNCP / Edital", "Link Disputa Comprasnet"
    ]
    for i, h in enumerate(cab_andamento, 1):
        c = ws2.cell(row=1, column=i, value=h)
        c.font = FONT_HEADER
        c.fill = FILL_HEADER
        c.alignment = Alignment(horizontal="center" if i not in (2, 8) else "left", vertical="center", wrap_text=True)
        c.border = BORDA_FINA
    ws2.row_dimensions[1].height = 26

    linha = 2
    for p in dados["pncp_em_andamento"]:
        ws2.cell(row=linha, column=1, value=p["ugg"]).alignment = Alignment(horizontal="center")
        ws2.cell(row=linha, column=2, value=p["om_nome"]).alignment = Alignment(horizontal="left")
        ws2.cell(row=linha, column=3, value=p["numero"]).alignment = Alignment(horizontal="center")
        ws2.cell(row=linha, column=4, value=p["ano"]).alignment = Alignment(horizontal="center")
        ws2.cell(row=linha, column=5, value=p["processo"]).alignment = Alignment(horizontal="center")

        c_sit = ws2.cell(row=linha, column=6, value=p["situacao"])
        c_sit.alignment = Alignment(horizontal="center")
        sit_lower = p["situacao"].lower()
        if "proposta" in sit_lower:
            c_sit.fill = FILL_AMARELO
        elif "julgamento" in sit_lower:
            c_sit.fill = PatternFill("solid", fgColor="E8F4F8")
        elif "homolog" in sit_lower:
            c_sit.fill = FILL_VERDE

        ws2.cell(row=linha, column=7, value="Sim" if p["srp"] else "Não").alignment = Alignment(horizontal="center")
        ws2.cell(row=linha, column=8, value=p["objeto"]).alignment = Alignment(horizontal="left")
        ws2.cell(row=linha, column=9, value=p["data_publicacao"]).alignment = Alignment(horizontal="center")
        ws2.cell(row=linha, column=10, value=p["data_abertura"]).alignment = Alignment(horizontal="center")
        ws2.cell(row=linha, column=11, value=p["data_encerramento"]).alignment = Alignment(horizontal="center")

        c_val = ws2.cell(row=linha, column=12, value=p["valor_estimado"])
        c_val.number_format = 'R$ #,##0.00'
        c_val.alignment = Alignment(horizontal="right")

        ws2.cell(row=linha, column=13, value=p["status_bcms"]).alignment = Alignment(horizontal="center")

        # Links
        c_l1 = ws2.cell(row=linha, column=14, value="Acessar PNCP" if p["link_pncp"] else "-")
        if p["link_pncp"]:
            c_l1.hyperlink = p["link_pncp"]
            c_l1.font = FONT_LINK
        c_l1.alignment = Alignment(horizontal="center")

        c_l2 = ws2.cell(row=linha, column=15, value="Sala de Disputa" if p["link_origem"] else "-")
        if p["link_origem"]:
            c_l2.hyperlink = p["link_origem"]
            c_l2.font = FONT_LINK
        c_l2.alignment = Alignment(horizontal="center")

        fill_linha = FILL_ZEBRA if linha % 2 == 0 else PatternFill(fill_type=None)
        for col_i in range(1, 16):
            cell = ws2.cell(row=linha, column=col_i)
            cell.font = FONT_CORPO if cell.font != FONT_LINK else FONT_LINK
            cell.border = BORDA_FINA
            if fill_linha.fill_type and col_i != 6:
                cell.fill = fill_linha

        linha += 1

    ws2.auto_filter.ref = f"A1:O{max(linha-1, 1)}"
    _ajustar_largura_colunas(ws2, max_largura=50)

    # =========================================================================
    # ABA 3: PREGÕES HOMOLOGADOS (ATAS)
    # =========================================================================
    ws3 = wb.create_sheet(title="Pregões Homologados (Atas)")
    ws3.views.sheetView[0].showGridLines = True
    ws3.freeze_panes = "A2"

    cab_homologados = [
        "UASG Gerenciadora", "OM Gerenciadora", "Pregão", "Nr Ata (ARP)", "Itens BCMS",
        "Fornecedores Vencedores Homologados", "Fim da Vigência", "Dias Restantes",
        "Status da Vigência", "Cota Inicial BCMS (R$)", "Empenhado BCMS (R$)",
        "Saldo Disponível BCMS (R$)", "Valor Global da Ata (R$)", "% Consumido", "Link Comprasnet"
    ]
    for i, h in enumerate(cab_homologados, 1):
        c = ws3.cell(row=1, column=i, value=h)
        c.font = FONT_HEADER
        c.fill = FILL_HEADER
        c.alignment = Alignment(horizontal="center" if i not in (2, 6) else "left", vertical="center", wrap_text=True)
        c.border = BORDA_FINA
    ws3.row_dimensions[1].height = 26

    linha = 2
    for p in dados["pregoes_homologados"]:
        ws3.cell(row=linha, column=1, value=p["ugg"]).alignment = Alignment(horizontal="center")
        ws3.cell(row=linha, column=2, value=p["om_nome"]).alignment = Alignment(horizontal="left")
        ws3.cell(row=linha, column=3, value=p["pregao"]).alignment = Alignment(horizontal="center")
        ws3.cell(row=linha, column=4, value=p["atas"]).alignment = Alignment(horizontal="center")
        ws3.cell(row=linha, column=5, value=p["n_itens"]).alignment = Alignment(horizontal="center")
        ws3.cell(row=linha, column=6, value=p["fornecedores"]).alignment = Alignment(horizontal="left")
        ws3.cell(row=linha, column=7, value=p["vigencia_fim"]).alignment = Alignment(horizontal="center")
        ws3.cell(row=linha, column=8, value=p["dias_restantes"] if p["dias_restantes"] is not None else "-").alignment = Alignment(horizontal="center")

        c_stat = ws3.cell(row=linha, column=9, value=p["status_vigencia"])
        c_stat.alignment = Alignment(horizontal="center")
        if p["badge_cor"] == "VERDE":
            c_stat.fill = FILL_VERDE
        elif p["badge_cor"] == "AMARELO":
            c_stat.fill = FILL_AMARELO
        elif p["badge_cor"] == "VERMELHO":
            c_stat.fill = FILL_VERMELHO

        c10 = ws3.cell(row=linha, column=10, value=p["valor_cota_bcms"])
        c10.number_format = 'R$ #,##0.00'
        c10.alignment = Alignment(horizontal="right")

        c11 = ws3.cell(row=linha, column=11, value=p["valor_consumido_bcms"])
        c11.number_format = 'R$ #,##0.00'
        c11.alignment = Alignment(horizontal="right")

        c12 = ws3.cell(row=linha, column=12, value=p["valor_saldo_bcms"])
        c12.number_format = 'R$ #,##0.00'
        c12.alignment = Alignment(horizontal="right")

        c13 = ws3.cell(row=linha, column=13, value=p["valor_global_ata"])
        c13.number_format = 'R$ #,##0.00'
        c13.alignment = Alignment(horizontal="right")

        c14 = ws3.cell(row=linha, column=14, value=p["pct_execucao"] / 100)
        c14.number_format = '0.0%'
        c14.alignment = Alignment(horizontal="center")

        c_link = ws3.cell(row=linha, column=15, value="Consultar Ata" if p["link"] else "-")
        if p["link"]:
            c_link.hyperlink = p["link"]
            c_link.font = FONT_LINK
        c_link.alignment = Alignment(horizontal="center")

        fill_linha = FILL_ZEBRA if linha % 2 == 0 else PatternFill(fill_type=None)
        for col_i in range(1, 16):
            cell = ws3.cell(row=linha, column=col_i)
            cell.font = FONT_CORPO if cell.font != FONT_LINK else FONT_LINK
            cell.border = BORDA_FINA
            if fill_linha.fill_type and col_i != 9:
                cell.fill = fill_linha

        linha += 1

    # Linha Totalizadora
    ws3.cell(row=linha, column=1, value="TOTAL").alignment = Alignment(horizontal="center")
    ws3.cell(row=linha, column=2, value=f"{len(dados['pregoes_homologados'])} Pregões Homologados com Ata").alignment = Alignment(horizontal="left")
    ws3.cell(row=linha, column=5, value=f"=SUM(E2:E{linha-1})").alignment = Alignment(horizontal="center")

    c_tot_cota = ws3.cell(row=linha, column=10, value=f"=SUM(J2:J{linha-1})")
    c_tot_cota.number_format = 'R$ #,##0.00'
    c_tot_cota.alignment = Alignment(horizontal="right")

    c_tot_aut = ws3.cell(row=linha, column=11, value=f"=SUM(K2:K{linha-1})")
    c_tot_aut.number_format = 'R$ #,##0.00'
    c_tot_aut.alignment = Alignment(horizontal="right")

    c_tot_sal = ws3.cell(row=linha, column=12, value=f"=SUM(L2:L{linha-1})")
    c_tot_sal.number_format = 'R$ #,##0.00'
    c_tot_sal.alignment = Alignment(horizontal="right")

    c_tot_glob = ws3.cell(row=linha, column=13, value=f"=SUM(M2:M{linha-1})")
    c_tot_glob.number_format = 'R$ #,##0.00'
    c_tot_glob.alignment = Alignment(horizontal="right")

    c_tot_pct = ws3.cell(row=linha, column=14, value=f"=K{linha}/J{linha}")
    c_tot_pct.number_format = '0.0%'
    c_tot_pct.alignment = Alignment(horizontal="center")

    for col_i in range(1, 16):
        cell = ws3.cell(row=linha, column=col_i)
        cell.font = FONT_CORPO_BOLD
        cell.fill = PatternFill("solid", fgColor="E5E7E9")
        cell.border = Border(top=Side(style="double", color=AZUL_ESCURO), bottom=Side(style="double", color=AZUL_ESCURO), left=Side(style="thin", color=CINZA_BORDA), right=Side(style="thin", color=CINZA_BORDA))
    ws3.row_dimensions[linha].height = 24

    ws3.auto_filter.ref = f"A1:O{max(linha-1, 1)}"
    _ajustar_largura_colunas(ws3, max_largura=45)

    # =========================================================================
    # ABA 4: ITENS DETALHADOS (BCMS)
    # =========================================================================
    ws4 = wb.create_sheet(title="Itens Detalhados (BCMS)")
    ws4.views.sheetView[0].showGridLines = True
    ws4.freeze_panes = "A2"

    cab_itens = [
        "UASG Gerenciadora", "OM Gerenciadora", "Pregão", "Nr Ata", "Nr Item",
        "Descrição Completa do Material / Serviço", "CATMAT / CATSER", "Nome do Catálogo",
        "Tipo", "Categoria Geral", "ND Sugerida", "Subitem da Despesa", "CNPJ Fornecedor",
        "Razão Social Fornecedor", "Início Vigência", "Fim Vigência", "Dias Restantes",
        "Status Vigência", "Valor Unitário (R$)", "Cota BCMS (Qtd)", "Consumido BCMS (Qtd)",
        "Saldo Restante BCMS (Qtd)", "Qtd Global Ata", "Valor Cota BCMS (R$)", "Saldo Disponível (R$)", "Valor Global Ata (R$)", "Link do Item"
    ]
    for i, h in enumerate(cab_itens, 1):
        c = ws4.cell(row=1, column=i, value=h)
        c.font = FONT_HEADER
        c.fill = FILL_HEADER
        c.alignment = Alignment(horizontal="center" if i not in (2, 6, 8, 14) else "left", vertical="center", wrap_text=True)
        c.border = BORDA_FINA
    ws4.row_dimensions[1].height = 26

    linha = 2
    for it in dados["itens_detalhados"]:
        ws4.cell(row=linha, column=1, value=it["ugg"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=2, value=it["om_nome"]).alignment = Alignment(horizontal="left")
        ws4.cell(row=linha, column=3, value=it["pregao"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=4, value=it["ata"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=5, value=it["item"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=6, value=it["descricao"]).alignment = Alignment(horizontal="left")
        ws4.cell(row=linha, column=7, value=it["catmat"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=8, value=it["catmat_nome"]).alignment = Alignment(horizontal="left")
        ws4.cell(row=linha, column=9, value=it["catalogo"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=10, value=it["categoria"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=11, value=it["nd_sugerida"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=12, value=it["subitem_sugerido"]).alignment = Alignment(horizontal="left")
        ws4.cell(row=linha, column=13, value=it["fornecedor_cnpj"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=14, value=it["fornecedor_nome"]).alignment = Alignment(horizontal="left")
        ws4.cell(row=linha, column=15, value=it["vigencia_ini"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=16, value=it["vigencia_fim"]).alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=17, value=it["dias_restantes"] if it["dias_restantes"] is not None else "-").alignment = Alignment(horizontal="center")
        ws4.cell(row=linha, column=18, value=it["status_vigencia"]).alignment = Alignment(horizontal="center")

        c19 = ws4.cell(row=linha, column=19, value=it["valor_unitario"])
        c19.number_format = 'R$ #,##0.00'
        c19.alignment = Alignment(horizontal="right")

        c20 = ws4.cell(row=linha, column=20, value=it["qtd_cota_bcms"])
        c20.number_format = '#,##0'
        c20.alignment = Alignment(horizontal="right")

        c21 = ws4.cell(row=linha, column=21, value=it["qtd_consumida_bcms"])
        c21.number_format = '#,##0'
        c21.alignment = Alignment(horizontal="right")

        c22 = ws4.cell(row=linha, column=22, value=it["qtd_saldo_bcms"])
        c22.number_format = '#,##0'
        c22.alignment = Alignment(horizontal="right")

        c23 = ws4.cell(row=linha, column=23, value=it["qtd_homologada_global"])
        c23.number_format = '#,##0'
        c23.alignment = Alignment(horizontal="right")

        c24 = ws4.cell(row=linha, column=24, value=it["valor_cota_bcms"])
        c24.number_format = 'R$ #,##0.00'
        c24.alignment = Alignment(horizontal="right")

        c25 = ws4.cell(row=linha, column=25, value=it["valor_saldo_bcms"])
        c25.number_format = 'R$ #,##0.00'
        c25.alignment = Alignment(horizontal="right")

        c26 = ws4.cell(row=linha, column=26, value=it["valor_global_ata"])
        c26.number_format = 'R$ #,##0.00'
        c26.alignment = Alignment(horizontal="right")

        c_link = ws4.cell(row=linha, column=27, value="Ficha do Item" if it["link"] else "-")
        if it["link"]:
            c_link.hyperlink = it["link"]
            c_link.font = FONT_LINK
        c_link.alignment = Alignment(horizontal="center")

        fill_linha = FILL_ZEBRA if linha % 2 == 0 else PatternFill(fill_type=None)
        for col_i in range(1, 28):
            cell = ws4.cell(row=linha, column=col_i)
            cell.font = FONT_CORPO if cell.font != FONT_LINK else FONT_LINK
            cell.border = BORDA_FINA
            if fill_linha.fill_type:
                cell.fill = fill_linha

        linha += 1

    ws4.auto_filter.ref = f"A1:AA{max(linha-1, 1)}"
    _ajustar_largura_colunas(ws4, max_largura=45)

    # =========================================================================
    # ABA 5: CRUZAMENTO & ALERTAS GERENCIAIS
    # =========================================================================
    ws5 = wb.create_sheet(title="Cruzamento & Alertas")
    ws5.views.sheetView[0].showGridLines = True

    # Seção 1: Atas Próximas ao Vencimento (< 60 dias)
    ws5.merge_cells("A1:G1")
    c_s1 = ws5["A1"]
    c_s1.value = "⚠️ ATAS COM VENCIMENTO EM MENOS DE 60 DIAS (RISCO DE PERDA DE SALDO)"
    c_s1.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    c_s1.fill = PatternFill("solid", fgColor=AMARELO_ALERTA)
    c_s1.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws5.row_dimensions[1].height = 24

    cab_a1 = ["UASG", "OM Gerenciadora", "Pregão", "Ata", "Fim da Vigência", "Dias Restantes", "Saldo Restante BCMS (R$)"]
    for i, h in enumerate(cab_a1, 1):
        c = ws5.cell(row=2, column=i, value=h)
        c.font = FONT_HEADER
        c.fill = FILL_HEADER
        c.alignment = Alignment(horizontal="center" if i != 2 else "left", vertical="center")
        c.border = BORDA_FINA
    ws5.row_dimensions[2].height = 22

    linha = 3
    if not dados["alertas_vencendo"]:
        ws5.merge_cells(f"A{linha}:G{linha}")
        ws5.cell(row=linha, column=1, value="Nenhuma ata ativa vence nos próximos 60 dias. Situação regular.").alignment = Alignment(horizontal="center")
        linha += 1
    else:
        for p in dados["alertas_vencendo"]:
            ws5.cell(row=linha, column=1, value=p["ugg"]).alignment = Alignment(horizontal="center")
            ws5.cell(row=linha, column=2, value=p["om_nome"]).alignment = Alignment(horizontal="left")
            ws5.cell(row=linha, column=3, value=p["pregao"]).alignment = Alignment(horizontal="center")
            ws5.cell(row=linha, column=4, value=p["atas"]).alignment = Alignment(horizontal="center")
            ws5.cell(row=linha, column=5, value=p["vigencia_fim"]).alignment = Alignment(horizontal="center")

            c_dias = ws5.cell(row=linha, column=6, value=f"{p['dias_restantes']} dias")
            c_dias.alignment = Alignment(horizontal="center")
            c_dias.fill = FILL_AMARELO

            c_sal = ws5.cell(row=linha, column=7, value=p["valor_saldo_bcms"])
            c_sal.number_format = 'R$ #,##0.00'
            c_sal.alignment = Alignment(horizontal="right")

            for col_i in range(1, 8):
                cell = ws5.cell(row=linha, column=col_i)
                cell.font = FONT_CORPO
                cell.border = BORDA_FINA
            linha += 1

    linha += 2  # espaçador

    # Seção 2: Atas com Saldo Esgotado (100% Empenhado)
    ws5.merge_cells(f"A{linha}:G{linha}")
    c_s2 = ws5[f"A{linha}"]
    c_s2.value = "🎯 PREGÕES COM SALDO TOTALMENTE CONSUMIDO (100% EMPENHADO)"
    c_s2.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    c_s2.fill = PatternFill("solid", fgColor=VERDE_MILITAR)
    c_s2.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws5.row_dimensions[linha].height = 24
    linha += 1

    cab_a2 = ["UASG", "OM Gerenciadora", "Pregão", "Ata", "Itens", "Cota BCMS (R$)", "Total Empenhado (R$)"]
    for i, h in enumerate(cab_a2, 1):
        c = ws5.cell(row=linha, column=i, value=h)
        c.font = FONT_HEADER
        c.fill = FILL_HEADER
        c.alignment = Alignment(horizontal="center" if i != 2 else "left", vertical="center")
        c.border = BORDA_FINA
    ws5.row_dimensions[linha].height = 22
    linha += 1

    if not dados["alertas_saldo_zerado"]:
        ws5.merge_cells(f"A{linha}:G{linha}")
        ws5.cell(row=linha, column=1, value="Nenhum pregão participante com saldo 100% esgotado no momento.").alignment = Alignment(horizontal="center")
        linha += 1
    else:
        for p in dados["alertas_saldo_zerado"]:
            ws5.cell(row=linha, column=1, value=p["ugg"]).alignment = Alignment(horizontal="center")
            ws5.cell(row=linha, column=2, value=p["om_nome"]).alignment = Alignment(horizontal="left")
            ws5.cell(row=linha, column=3, value=p["pregao"]).alignment = Alignment(horizontal="center")
            ws5.cell(row=linha, column=4, value=p["atas"]).alignment = Alignment(horizontal="center")
            ws5.cell(row=linha, column=5, value=p["n_itens"]).alignment = Alignment(horizontal="center")

            c_h = ws5.cell(row=linha, column=6, value=p["valor_cota_bcms"])
            c_h.number_format = 'R$ #,##0.00'
            c_h.alignment = Alignment(horizontal="right")

            c_e = ws5.cell(row=linha, column=7, value=p["valor_consumido_bcms"])
            c_e.number_format = 'R$ #,##0.00'
            c_e.alignment = Alignment(horizontal="right")

            for col_i in range(1, 8):
                cell = ws5.cell(row=linha, column=col_i)
                cell.font = FONT_CORPO
                cell.border = BORDA_FINA
            linha += 1

    _ajustar_largura_colunas(ws5)

    # Destino do arquivo
    if caminho_saida is None:
        data_sufixo = datetime.now().strftime("%Y%m%d")
        config.SAIDA.mkdir(parents=True, exist_ok=True)
        caminho_saida = config.SAIDA / f"relatorio_pregoes_participantes_BCMS_{data_sufixo}.xlsx"

    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    wb.save(caminho_saida)
    wb.close()
    return caminho_saida


def gerar_relatorio(caminho_saida: Path | None = None, atualizar_pncp: bool = False, progresso: Callable[[str], None] = print) -> Path:
    """Função mestre que orquestra a coleta, consolidação e geração do Excel."""
    t0 = time.time()
    progresso("=== Iniciando Geração do Relatório de Pregões Participantes ===")
    dados = consolidar_dados(atualizar_pncp=atualizar_pncp, progresso=progresso)
    progresso(f"  Consolidação concluída: {dados['kpis']['total_pregoes_homologados']} homologados, {dados['kpis']['total_pregoes_andamento']} em andamento, {dados['kpis']['total_itens_bcms']} itens.")
    progresso("  Montando e estilizando planilha executiva com openpyxl...")
    destino = gerar_excel(dados, caminho_saida=caminho_saida)
    progresso(f"=== Relatório gerado com sucesso em {time.time() - t0:.1f}s: {destino} ===")
    return destino
