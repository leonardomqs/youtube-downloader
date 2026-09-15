@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal

rem ============================================================================
rem  Instalacao do youtube-downloader
rem
rem  Roda uma vez por maquina, por duplo clique. Cria o ambiente do projeto com
rem  as versoes exatas registradas em uv.lock e confere as duas ferramentas
rem  externas que mudam o resultado: o ffmpeg e o Deno.
rem
rem  Por que um .bat e nao "so rode uv sync": porque abrir um terminal na pasta
rem  certa ja e a etapa em que a maioria das pessoas para. O arquivo existe para
rem  que a instalacao inteira seja um duplo clique e uma leitura.
rem ============================================================================

set "RAIZ=%~dp0.."
cd /d "%RAIZ%"

echo.
echo  Instalando o youtube-downloader em: %RAIZ%
echo.

rem --------------------------------------------------------------- [1/3] uv
echo  [1/3] procurando o uv...
where uv >nul 2>nul
if errorlevel 1 goto :sem_uv
echo        ok

rem ------------------------------------------------------- [2/3] dependencias
echo.
echo  [2/3] criando o ambiente e instalando as dependencias...
echo        (inclui o Flask, que e o que faz a interface de navegador funcionar)
uv sync --group interface
if errorlevel 1 goto :recriar_ambiente
goto :ferramentas

:recriar_ambiente
rem O ambiente virtual guarda o caminho ABSOLUTO em que foi criado, dentro de
rem .venv\pyvenv.cfg e nos atalhos de .venv\Scripts. Se a pasta do projeto foi
rem movida ou copiada de outra maquina, esse caminho aponta para o lugar antigo
rem e o ambiente fica invalido -- o sintoma costuma ser um erro confuso de
rem interpretador, nao um "ambiente movido".
rem
rem Recriar e sempre seguro: as versoes vem travadas do uv.lock, entao o
rem ambiente reconstruido e identico. Nada de seu e apagado -- o .venv nao
rem contem configuracao, so bibliotecas.
echo.
echo        A primeira tentativa falhou. Se este projeto foi movido ou copiado,
echo        o ambiente antigo aponta para o caminho anterior.
echo        Recriando o ambiente do zero...
echo.
rmdir /s /q "%RAIZ%\.venv" 2>nul
uv sync --group interface
if errorlevel 1 goto :falhou

rem -------------------------------------------- [3/3] ffmpeg e runtime JS
:ferramentas
echo.
echo  [3/3] procurando o ffmpeg e um runtime JavaScript...

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo        ffmpeg: nao esta no PATH -- sera usado o que vem junto com o
    echo                pacote imageio-ffmpeg, que ja foi instalado acima.
    echo                Para usar o do sistema: winget install Gyan.FFmpeg
) else (
    echo        ffmpeg: ok
)

where deno >nul 2>nul
if errorlevel 1 (
    echo        Deno:   NAO ENCONTRADO.
    echo                Sem um runtime JavaScript o YouTube pode nao oferecer os
    echo                melhores formatos, e o video sai com qualidade menor.
    echo                Instale com: winget install DenoLand.Deno
) else (
    echo        Deno:   ok
)

echo.
echo  ============================================================
echo   Instalacao concluida.
echo.
echo   Para usar: de um duplo clique em  executar\interface.bat
echo   Uma pagina abre no navegador e o resto e colar os links.
echo  ============================================================
echo.
echo  Pressione qualquer tecla para fechar.
pause >nul
exit /b 0

:sem_uv
echo.
echo  ============================================================
echo   O uv nao esta instalado nesta maquina.
echo.
echo   Ele e quem baixa o Python e as bibliotecas do projeto.
echo   Instale com UM destes comandos, no PowerShell, e rode este
echo   arquivo de novo:
echo.
echo      winget install --id=astral-sh.uv -e
echo.
echo      powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
echo.
echo   Depois de instalar, FECHE esta janela e abra de novo: um
echo   terminal ja aberto nao enxerga o PATH novo.
echo  ============================================================
echo.
pause >nul
exit /b 1

:falhou
echo.
echo  ============================================================
echo   A instalacao FALHOU. A mensagem acima diz o motivo.
echo  ============================================================
echo.
pause >nul
exit /b 1
