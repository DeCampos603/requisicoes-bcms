@echo off
rem Requisicoes de Empenho - BCMS: inicia o servidor local e abre o navegador.
rem O servidor atende somente 127.0.0.1 (nao fica acessivel na rede).
cd /d "%~dp0"
if not exist dados\pregoes.json (
  echo Primeira execucao: importando NE, NC, creditos e pregoes do repositorio. Aguarde cerca de 2 minutos...
  py -3 -m app.importadores todos
)
echo.
echo Abrindo http://127.0.0.1:8765  ^(Ctrl+C para encerrar^)
start "" http://127.0.0.1:8765
py -3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765
