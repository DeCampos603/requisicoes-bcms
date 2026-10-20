# -*- coding: utf-8 -*-
"""CLI: Gerador do Relatório Executivo de Pregões Participantes (BCMS — UASG 160329).

Execução:
    py -3 gerar_relatorio_participantes.py
    py -3 gerar_relatorio_participantes.py --online
    py -3 gerar_relatorio_participantes.py --saida "meu_relatorio.xlsx" --abrir
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Garante que o diretório raiz está no sys.path
PROJETO = Path(__file__).resolve().parent
if str(PROJETO) not in sys.path:
    sys.path.insert(0, str(PROJETO))

from app.relatorio_participantes import gerar_relatorio


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Gera o Relatório Executivo em Excel (.xlsx) de pregões onde o BCMS (UASG 160329) é participante (Homologados e Não Homologados)."
    )
    parser.add_argument(
        "--saida",
        "-s",
        type=str,
        default=None,
        help="Caminho do arquivo Excel de saída (.xlsx). Padrão: saida/relatorio_pregoes_participantes_BCMS_AAAAMMDD.xlsx",
    )
    parser.add_argument(
        "--online",
        "--atualizar-pncp",
        action="store_true",
        help="Força uma nova varredura online na API pública do PNCP para as 24 OMs parceiras.",
    )
    parser.add_argument(
        "--abrir",
        "-a",
        action="store_true",
        help="Abre o arquivo Excel automaticamente após a geração no visualizador padrão do Windows.",
    )

    args = parser.parse_args(argv)
    caminho_saida = Path(args.saida) if args.saida else None

    try:
        destino = gerar_relatorio(
            caminho_saida=caminho_saida,
            atualizar_pncp=args.online,
            progresso=print,
        )
        print(f"\n[SUCESSO] Relatório salvo em: {destino.resolve()}")

        if args.abrir:
            print("[ABRINDO] Abrindo planilha no visualizador padrão do Windows...")
            os.startfile(str(destino.resolve()))

        return 0
    except Exception as e:
        print(f"\n[ERRO] Falha ao gerar relatório: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
