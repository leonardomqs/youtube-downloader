# youtube-downloader

Baixa vídeos e playlists do YouTube em **MP4** e/ou **MP3**, sempre na melhor qualidade disponível — **pelo navegador** ou **pela linha de comando**.

- **[Interface de navegador](#interface-de-navegador-duplo-clique)** — dois duplo cliques, uma página: cola os links, escolhe o formato e acompanha o download.
- **[Linha de comando](#uso-pela-linha-de-comando)** — para quem já está no terminal, para listas grandes e para automatizar.

As duas fazem exatamente a mesma coisa: chamam o mesmo `service.py`.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (Python 3.12)
- `ffmpeg` — se não estiver instalado, o programa usa automaticamente o binário que vem com o pacote `imageio-ffmpeg` (já é dependência). Para usar o do sistema: `winget install Gyan.FFmpeg`.
- Recomendado: [Deno](https://deno.com/) (`winget install DenoLand.Deno`). Sem um runtime JavaScript, o YouTube não expõe todos os formatos ao yt-dlp e você pode perder qualidade. O programa avisa se não encontrar — no terminal e na tela.

## Instalação

Por duplo clique:

```
executar\instalar.bat
```

Ele procura o uv, cria o ambiente com as versões travadas em `uv.lock` (incluindo o Flask, que a interface usa) e confere o ffmpeg e o Deno — cada um deles, quando falta, é avisado com o comando de instalação.

Ou, no terminal:

```bash
uv sync --group interface
```

Sem o `--group interface`, tudo funciona igual, menos a interface de navegador.

## Interface de navegador (duplo clique)

```
executar\interface.bat
```

Uma página abre em `http://127.0.0.1:5300`. Nela:

- **Links** — um por linha, vídeos ou playlists. O campo já vem preenchido com o seu `links.txt`.
- **O que baixar** — MP4, MP3 ou os dois; resolução máxima ou qualidade do MP3.
- **Pasta de destino** — os arquivos vão direto para lá, sem passar por download do navegador.
- **Ver o que será baixado** — lista os vídeos, numerados e na ordem, sem baixar nada. É o `--dry-run` com botão.
- **Acompanhar** — barra de progresso, velocidade, tempo restante, o que já entrou e o que falhou. Inclusive durante a conversão para MP3, que é a parte em que a barra pareceria travada.
- **Parar** — interrompe no meio do arquivo, sem esperar o vídeo terminar.
- **Transmissões ao vivo** — marque a opção para baixar a live desde o início, alcançar o ponto atual e seguir gravando. Ver [Lives](#lives).

Uma rodada por vez: o yt-dlp já baixa fragmentos em paralelo, e dois downloads simultâneos só dividiriam a mesma banda.

> O servidor roda **só nesta máquina** (`127.0.0.1`). Não publique na rede: ele baixa da internet e escreve em qualquer pasta do disco.
>
> Fechar a janela do servidor **interrompe o download em andamento**.
>
> A porta 5300 está em dois lugares que precisam concordar: `executar\interface.bat`, que abre o navegador, e `webui\__main__.py`, que sobe o servidor.

## Uso pela linha de comando

```bash
# um link direto, em MP4 (padrão)
uv run main.py https://youtu.be/jNQXAC9IVRw

# uma lista de links
uv run main.py links.txt

# só o áudio em MP3
uv run main.py links.txt --format mp3

# os dois formatos
uv run main.py links.txt --format both

# escolhendo a pasta de destino
uv run main.py links.txt --format both -o "D:/Videos"

# ver o que seria baixado, sem baixar nada
uv run main.py links.txt --dry-run
```

Dá para misturar arquivos e links na mesma chamada (`uv run main.py links.txt https://youtu.be/...`); links repetidos são baixados uma vez só.

## Arquivo de links

Um link por linha — vídeo ou playlist. Linhas vazias e começadas com `#` são ignoradas.

```
# minha lista
https://www.youtube.com/watch?v=aqz-KE-bpKQ
https://www.youtube.com/playlist?list=PLB29CbKaE2OY
https://youtu.be/jNQXAC9IVRw
```

## Playlists

Links de playlist (`youtube.com/playlist?list=...`), canais e a aba de vídeos de um canal já baixam a lista inteira. Cada playlist ganha uma **subpasta com o nome dela** e os arquivos saem **numerados na ordem da lista**:

```
downloads/mp4/NASA Moon Base/001 - Moon Base... [tQcNSJc8gEg].mp4
downloads/mp4/NASA Moon Base/002 - Ignition... [yIlTwwJv1Ac].mp4
downloads/mp4/Me at the zoo [jNQXAC9IVRw].mp4        <- vídeo avulso fica na raiz
```

```bash
# baixa a playlist inteira em MP3
uv run main.py links.txt --format mp3

# só os itens 1 a 10 (liga o modo playlist sozinho)
uv run main.py links.txt --playlist-items 1-10

# itens específicos e intervalos: 5, 7 e do 20 em diante
uv run main.py links.txt --playlist-items 5,7,20-

# do fim para o começo
uv run main.py links.txt --playlist-reverse

# link de vídeo que está dentro de uma playlist: baixa a lista toda
uv run main.py links.txt --playlist

# tudo junto na mesma pasta, sem subpasta por playlist
uv run main.py links.txt --no-playlist-folder

# baixando aos poucos: o histórico evita repetir o que já veio
uv run main.py links.txt --archive baixados.txt
```

Um link de vídeo com `&list=...` baixa **só aquele vídeo** por padrão; use `--playlist` para pegar a lista inteira a partir dele.

Vídeos indisponíveis (privados, removidos, com restrição regional) **não interrompem a playlist** — os demais continuam e o resumo final mostra o que falhou.

## Lives

Um link de live é gravado do momento atual até ela acabar. Com `--live-from-start` (ou a opção na interface), o programa baixa desde o início, alcança o ponto atual e segue gravando:

```bash
uv run main.py https://youtu.be/AO_VIVO --live-from-start
```

- **Só YouTube, Twitch, TVer e mellow-fan**, e o yt-dlp ainda marca como experimental. Em outro site o link falha com um aviso; sem a opção, ele grava a partir de agora. Vídeos comuns na mesma rodada não mudam.
- **No YouTube**, volta no máximo 120 horas.
- **Para parar**, use **Parar** na interface ou Ctrl+C no terminal: o que já veio vira um arquivo normal. O segundo Ctrl+C sai sem esperar. Parar encerra a rodada: a passada de MP3 de `--format both` e os links seguintes não rodam, e a gravação parcial não entra no histórico.
- **Um fragmento por vez**: nesse modo o `-j` é ignorado, para que parar não dependa de baixar a fila inteira de trechos.
- **`--format both`** grava o MP4 até a live acabar e só então busca o MP3.
- **Sem a opção**, o Parar da interface não alcança a gravação: quem grava é o ffmpeg, e ele não dá notícias até terminar.

## Histórico (`--archive`)

O histórico guarda os vídeos já baixados para não repeti-los na próxima rodada. Como ele registra só o id do vídeo, **cada formato tem o seu arquivo**: `--archive baixados.txt` cria `baixados.mp4.txt` e `baixados.mp3.txt`. Sem isso, em `--format both` o MP3 pularia tudo o que o MP4 acabou de registrar.

O que foi pulado aparece no resumo como *já no histórico* — não conta como falha.

## Opções

| Opção | Descrição |
| --- | --- |
| `-f, --format {mp4,mp3,both}` | Formato de saída (padrão: `mp4`) |
| `-o, --output PASTA` | Pasta de destino (padrão: `downloads`) |
| `-n, --dry-run` | Lista o que seria baixado, sem baixar nada |
| `--max-height 1080` | Limita a resolução do vídeo; sem isso usa a melhor disponível |
| `--audio-quality 0` | Qualidade do MP3: `0` (melhor VBR) a `9`, ou bitrate fixo como `320` |
| `--playlist` | Baixa a lista inteira a partir de um link de vídeo que está numa playlist |
| `--playlist-items 1-10` | Quais itens baixar (ex.: `1-10`, `5,7`, `20-`) |
| `--playlist-reverse` | Baixa a playlist do fim para o começo |
| `--no-playlist-folder` | Não cria a subpasta com o nome da playlist |
| `--live-from-start` | Baixa a live desde o início e segue gravando até ela acabar |
| `--archive baixados.txt` | Guarda o histórico e não baixa de novo o que já foi baixado |
| `-j, --concurrent-fragments 4` | Fragmentos baixados em paralelo por vídeo (`1` desliga) |
| `--limit-rate 2M` | Limita a velocidade de download |
| `--cookies-from-browser chrome` | Usa cookies do navegador para vídeos restritos/com login. Aceita perfil e container: `chrome:Default`, `firefox::Pessoal` |
| `--ffmpeg-location CAMINHO` | Pasta ou executável do ffmpeg, caso não esteja no PATH |
| `--ascii` | Usa apenas ASCII nos nomes dos arquivos |
| `-q, --quiet` | Menos mensagens |
| `--version` | Mostra a versão do programa e do yt-dlp |

## Comportamento

- **MP4**: melhor vídeo + melhor áudio, combinados em um único `.mp4` com metadados.
- **MP3**: melhor faixa de áudio convertida para MP3, com metadados e a capa (thumbnail) embutida.
- Erros não derrubam a fila: nem um link ruim no arquivo, nem um vídeo indisponível no meio de uma playlist. No fim aparece um resumo com o total baixado, o que já estava no histórico e as falhas; na linha de comando o código de saída é `1` se algo falhou.
- Downloads interrompidos são retomados de onde pararam ao rodar de novo.

## Como está organizado

| Arquivo | O que é |
| --- | --- |
| `service.py` | O que acontece numa rodada — cadeia de formatos, histórico, contagem de falhas. Sem interface: não imprime nada e não conhece nem terminal nem tela. |
| `cli.py` | Interface 1: o terminal. Argumentos, mensagens e o resumo. |
| `main.py` | A porta de entrada de sempre: `uv run main.py links.txt`. |
| `webui/` | Interface 2: o navegador. Fábrica Flask, rotas, as rodadas em thread, `templates/` e `static/`. |
| `executar/` | Os dois `.bat` de duplo clique: instalar e abrir a interface. |
| `tests/` | Três suítes, nenhuma usando rede. |

**Idioma:** identificadores em inglês, comentários e documentação em português — e as mensagens de tela também, porque são lidas por quem usa o programa.

As duas interfaces chamam `service.run`. É de propósito: a cadeia de formatos do MP4 decide a qualidade de tudo o que sai daqui, e uma segunda cópia dela dentro da tela funcionaria até a primeira correção feita de um lado só.

## Desenvolvimento

```bash
uv sync --group dev
uv run pytest          # testes (não usam rede)
uv run ruff check .    # lint
uv run ruff format .   # formatação
```

A interface é testada com o download substituído — o executor é injetado em `create_app`, então a tela inteira (fila, andamento, interrupção, resumo) roda sem baixar um byte.

Para subir a interface sem o `.bat`:

```bash
uv run python -m webui          # http://127.0.0.1:5300
YTDL_PORT=5310 uv run python -m webui
```

## Aviso

Baixe apenas conteúdo que você tem direito de baixar (material próprio, de domínio público ou com licença que permita), respeitando os Termos de Serviço do YouTube.
