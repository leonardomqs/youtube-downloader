# youtube-downloader

Lê uma lista de links do YouTube de um arquivo `.txt` — vídeos avulsos **ou playlists** — e baixa tudo em **MP4** e/ou **MP3**, sempre na melhor qualidade disponível.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (Python 3.12)
- `ffmpeg` — se não estiver instalado, o programa usa automaticamente o binário que vem com o pacote `imageio-ffmpeg` (já é dependência). Para usar o do sistema: `winget install Gyan.FFmpeg`.
- Recomendado: [Deno](https://deno.com/) (`winget install DenoLand.Deno`). Sem um runtime JavaScript, o YouTube não expõe todos os formatos ao yt-dlp e você pode perder qualidade. O programa avisa se não encontrar.

## Instalação

```bash
uv sync
```

## Arquivo de links

Um link por linha — vídeo ou playlist. Linhas vazias e começadas com `#` são ignoradas, e links repetidos são baixados só uma vez.

```
# minha lista
https://www.youtube.com/watch?v=aqz-KE-bpKQ
https://www.youtube.com/playlist?list=PLB29CbKaE2OY
https://youtu.be/jNQXAC9IVRw
```

## Uso

```bash
# vídeo em MP4 (padrão)
uv run main.py links.txt

# só o áudio em MP3
uv run main.py links.txt --format mp3

# os dois formatos
uv run main.py links.txt --format both

# escolhendo a pasta de destino
uv run main.py links.txt --format both -o "D:/Videos"
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

## Opções

| Opção | Descrição |
| --- | --- |
| `-f, --format {mp4,mp3,both}` | Formato de saída (padrão: `mp4`) |
| `-o, --output PASTA` | Pasta de destino (padrão: `downloads`) |
| `--max-height 1080` | Limita a resolução do vídeo; sem isso usa a melhor disponível |
| `--audio-quality 0` | Qualidade do MP3: `0` (melhor VBR) a `9`, ou bitrate fixo como `320` |
| `--playlist` | Baixa a lista inteira a partir de um link de vídeo que está numa playlist |
| `--playlist-items 1-10` | Quais itens baixar (ex.: `1-10`, `5,7`, `20-`) |
| `--playlist-reverse` | Baixa a playlist do fim para o começo |
| `--no-playlist-folder` | Não cria a subpasta com o nome da playlist |
| `--archive baixados.txt` | Guarda o histórico e não baixa de novo o que já foi baixado |
| `--cookies-from-browser chrome` | Usa cookies do navegador para vídeos restritos/com login |
| `--ffmpeg-location CAMINHO` | Pasta ou executável do ffmpeg, caso não esteja no PATH |
| `--ascii` | Usa apenas ASCII nos nomes dos arquivos |
| `-q, --quiet` | Menos mensagens |

## Comportamento

- **MP4**: melhor vídeo + melhor áudio, combinados em um único `.mp4` com metadados.
- **MP3**: melhor faixa de áudio convertida para MP3, com metadados e a capa (thumbnail) embutida.
- Erros não derrubam a fila: nem um link ruim no arquivo, nem um vídeo indisponível no meio de uma playlist. No fim aparece um resumo com o total baixado e as falhas; o código de saída é `1` se algo falhou.
- Downloads interrompidos são retomados de onde pararam ao rodar de novo.

## Aviso

Baixe apenas conteúdo que você tem direito de baixar (material próprio, de domínio público ou com licença que permita), respeitando os Termos de Serviço do YouTube.
