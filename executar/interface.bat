@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal

rem ============================================================================
rem  Interface de navegador - duplo clique.
rem
rem  Abre uma pagina onde a pessoa cola os links, escolhe o formato e acompanha
rem  o download. Nenhum terminal, nenhum comando, nenhum --playlist-items.
rem
rem  O servidor roda SO nesta maquina (127.0.0.1). Nada e publicado na rede.
rem
rem  ATENCAO: fechar esta janela encerra o servidor E o download em andamento.
rem ============================================================================

set "RAIZ=%~dp0.."
cd /d "%RAIZ%"

where uv >nul 2>nul
if errorlevel 1 goto :sem_uv

echo.
echo  Preparando a interface...
uv sync --group interface >nul 2>nul
if errorlevel 1 goto :sem_ambiente

rem  A porta 5000 ja esta ocupada nas maquinas da equipe, a 5100 e do
rem  mira-carga e a 5200 e do ajeita-pdf; daqui sai a URL e a variavel que o
rem  servidor le, para os dois nunca discordarem. Quem le YTDL_PORT e
rem  webui\__main__.py.
set YTDL_PORT=5300
start "" http://127.0.0.1:%YTDL_PORT%
uv run python -m webui

echo.
echo  Interface encerrada. Pressione qualquer tecla para fechar.
pause >nul
exit /b 0

:sem_uv
echo.
echo  O uv nao esta instalado nesta maquina.
echo  Rode primeiro:  executar\instalar.bat
echo.
pause >nul
exit /b 1

:sem_ambiente
echo.
echo  O ambiente do projeto nao esta pronto.
echo  Rode primeiro:  executar\instalar.bat
echo.
pause >nul
exit /b 1
