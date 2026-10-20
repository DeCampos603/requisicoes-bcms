@echo off
rem Reimporta NE, NC, saldos de credito e pregoes (o botao "Atualizar dados" da tela faz o mesmo).
cd /d "%~dp0"
py -3 -m app.importadores todos
pause
