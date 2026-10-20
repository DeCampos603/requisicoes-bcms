@echo off
chcp 65001 > nul
title Gerador de Relatório de Pregões Participantes - BCMS

echo ======================================================================
echo    BATALHÃO CENTRAL DE MANUTENÇÃO E SUPRIMENTO (UASG 160329)
echo    RELATÓRIO GERENCIAL DE PREGÕES PARTICIPANTES (HOMOLOGADOS E PNCP)
echo ======================================================================
echo.

py -3 gerar_relatorio_participantes.py --abrir

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERRO] Ocorreu uma falha durante a geração do relatório.
    pause
) else (
    echo.
    echo [OK] Processo finalizado com sucesso!
    timeout /t 5 > nul
)
