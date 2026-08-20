"""Baixa vídeos e playlists do YouTube (lista em arquivo .txt) em MP4 e/ou MP3.

Uso:
    uv run main.py links.txt --format mp4
    uv run main.py links.txt --format mp3
    uv run main.py links.txt --format both -o saida
    uv run main.py playlists.txt --playlist --playlist-items 1-10
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from yt_dlp import YoutubeDL


FORMATOS = ("mp4", "mp3", "both")


class Baixador(YoutubeDL):
    """YoutubeDL que guarda as mensagens de erro além de exibi-las.

    Como os erros de cada vídeo são ignorados (para que um item quebrado no
    meio de uma playlist não derrube o resto), é aqui que os motivos das
    falhas são coletados para o resumo final.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.erros: list[str] = []

    def report_error(self, message, *args, **kwargs):
        self.erros.append(str(message).strip())
        return super().report_error(message, *args, **kwargs)


def encontrar_ffmpeg(caminho_manual: str | None) -> str | None:
    """Localiza o ffmpeg: opção manual, PATH ou o binário embutido do imageio-ffmpeg.

    Devolve o caminho a passar para o yt-dlp (pasta ou executável), ou None
    quando o ffmpeg já está no PATH e o yt-dlp o encontra sozinho.
    """
    if caminho_manual:
        return caminho_manual

    if shutil.which("ffmpeg"):
        return None

    try:
        import imageio_ffmpeg

        # O binário do imageio se chama ffmpeg-win-x86_64-vX.Y.exe; o yt-dlp
        # aceita o caminho completo do arquivo, mas não acharia a pasta sozinho.
        exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
        return str(exe) if exe.is_file() else None
    except Exception:
        return None


def ler_links(caminho: Path) -> list[str]:
    """Lê os links do arquivo, ignorando linhas vazias, comentários (#) e repetidos."""
    if not caminho.is_file():
        raise SystemExit(f"Arquivo não encontrado: {caminho}")

    links: list[str] = []
    vistos: set[str] = set()
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        link = linha.strip()
        if not link or link.startswith("#"):
            continue
        if link in vistos:
            continue
        vistos.add(link)
        links.append(link)

    if not links:
        raise SystemExit(f"Nenhum link encontrado em {caminho}")
    return links


def modelo_de_nome(pasta: Path, com_subpasta: bool) -> str:
    """Monta o outtmpl: vídeos de playlist ganham subpasta e numeração.

    Um vídeo avulso vira "Título [id].mp4" e um de playlist vira
    "Nome da playlist/007 - Título [id].mp4". A barra precisa ficar fora do
    campo (`%(playlist_title|)s/`): uma barra vinda de dentro do campo seria
    sanitizada e viraria parte do nome do arquivo em vez de criar a pasta.
    Sem playlist o campo fica vazio e o caminho colapsa para a pasta base.
    """
    partes = []
    if com_subpasta:
        partes.append("%(playlist_title|)s/")
    partes.append("%(playlist_index&{:03d} - |)s")
    partes.append("%(title)s [%(id)s].%(ext)s")
    return str(pasta).replace("\\", "/") + "/" + "".join(partes)


def opcoes_base(args, pasta: Path) -> dict:
    ffmpeg_dir = encontrar_ffmpeg(args.ffmpeg_location)
    opts = {
        "outtmpl": modelo_de_nome(pasta, args.playlist_folder),
        "noplaylist": not args.playlist,
        # Um vídeo indisponível não pode abortar o resto da playlist; as falhas
        # são recolhidas pelo Baixador e mostradas no resumo.
        "ignoreerrors": True,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
        "quiet": args.quiet,
        "no_warnings": args.quiet,
        "consoletitle": False,
        "windowsfilenames": os.name == "nt",
        "restrictfilenames": args.ascii,
    }
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    if args.playlist_items:
        opts["playlist_items"] = args.playlist_items
    if args.playlist_reverse:
        opts["playlistreverse"] = True
    if args.archive:
        opts["download_archive"] = str(args.archive)
    if args.cookies_from_browser:
        opts["cookiesfrombrowser"] = (args.cookies_from_browser,)
    return opts


def opcoes_mp4(args, pasta: Path) -> dict:
    """Melhor vídeo + melhor áudio disponíveis, combinados em MP4."""
    opts = opcoes_base(args, pasta)
    # Filtro estrito: com "<=?" o yt-dlp também aceitaria formatos sem o campo
    # height, furando o limite. Se nada couber, a cadeia cai no "best" final.
    altura = f"[height<={args.max_height}]" if args.max_height else ""
    opts.update(
        {
            "format": (
                f"bestvideo{altura}[ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo{altura}+bestaudio/best{altura}/best"
            ),
            "merge_output_format": "mp4",
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                {"key": "FFmpegMetadata", "add_metadata": True},
            ],
        }
    )
    return opts


def opcoes_mp3(args, pasta: Path) -> dict:
    """Melhor áudio disponível convertido para MP3 (com capa e metadados)."""
    opts = opcoes_base(args, pasta)
    opts.update(
        {
            "format": "bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": args.audio_quality,
                },
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ],
        }
    )
    return opts


def contar_resultado(info) -> tuple[int, int]:
    """Conta (baixados, falhados) no resultado de um link.

    Playlists trazem "entries" (que podem ter playlists aninhadas, no caso de
    canais); cada entrada que falhou vem como None.
    """
    if info is None:
        return 0, 1

    entradas = info.get("entries")
    if entradas is None:
        return 1, 0

    baixados = falhados = 0
    for entrada in entradas:
        ok, ruins = contar_resultado(entrada)
        baixados += ok
        falhados += ruins
    return baixados, falhados


def baixar(links: list[str], opts: dict, rotulo: str) -> tuple[int, list[tuple[str, str]]]:
    """Baixa cada link (vídeo ou playlist). Retorna (total baixado, falhas)."""
    falhas: list[tuple[str, str]] = []
    baixados = 0
    total = len(links)

    with Baixador(opts) as ydl:
        for i, link in enumerate(links, start=1):
            print(f"\n[{rotulo} {i}/{total}] {link}", flush=True)
            ydl.erros.clear()
            try:
                info = ydl.extract_info(link, download=True)
            except KeyboardInterrupt:
                raise
            except Exception as erro:  # não deixa um link derrubar a fila inteira
                falhas.append((link, f"{type(erro).__name__}: {erro}"))
                print(f"  -> falhou: {erro}", file=sys.stderr, flush=True)
                continue

            ok, ruins = contar_resultado(info)
            baixados += ok

            if info is not None and info.get("entries") is not None:
                nome = info.get("title") or "playlist"
                extra = f", {ruins} com erro" if ruins else ""
                print(f'  -> playlist "{nome}": {ok} vídeo(s){extra}', flush=True)

            if ruins or ok == 0:
                for motivo in ydl.erros or ["motivo desconhecido"]:
                    falhas.append((link, motivo))

    return baixados, falhas


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Baixa vídeos e playlists do YouTube listados em um arquivo .txt, "
        "em MP4 e/ou MP3, na melhor qualidade disponível.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("arquivo", type=Path, help="arquivo .txt com um link por linha")
    parser.add_argument(
        "-f",
        "--format",
        dest="formato",
        choices=FORMATOS,
        default="mp4",
        help="formato de saída: mp4, mp3 ou both (os dois)",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="saida",
        type=Path,
        default=Path("downloads"),
        help="pasta de destino (subpastas mp4/ e mp3/ são criadas dentro dela)",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=None,
        help="limita a resolução do vídeo (ex.: 1080); por padrão usa a melhor",
    )
    parser.add_argument(
        "--audio-quality",
        default="0",
        help="qualidade do MP3: 0 (melhor VBR) a 9, ou bitrate fixo como 320",
    )
    parser.add_argument(
        "--playlist",
        action="store_true",
        help="baixa a lista inteira quando o link for de um vídeo dentro de uma "
        "playlist (links de playlist pura já baixam tudo por padrão)",
    )
    parser.add_argument(
        "--playlist-items",
        default=None,
        help="quais itens da playlist baixar, ex.: 1-10, 5, 7, 20-",
    )
    parser.add_argument(
        "--playlist-reverse",
        action="store_true",
        help="baixa a playlist do fim para o começo",
    )
    parser.add_argument(
        "--no-playlist-folder",
        dest="playlist_folder",
        action="store_false",
        help="não cria uma subpasta com o nome da playlist",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="arquivo de histórico para não baixar de novo o que já foi baixado",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="usa cookies do navegador (chrome, firefox, edge...) para vídeos restritos",
    )
    parser.add_argument(
        "--ffmpeg-location",
        default=None,
        help="pasta do ffmpeg, caso não esteja no PATH",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="usa apenas ASCII nos nomes dos arquivos",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="menos mensagens")

    args = parser.parse_args(argv)
    # Selecionar itens ou inverter a ordem só faz sentido com a lista inteira.
    if args.playlist_items or args.playlist_reverse:
        args.playlist = True
    return args


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args(argv)
    links = ler_links(args.arquivo)

    if encontrar_ffmpeg(args.ffmpeg_location) is None and not shutil.which("ffmpeg"):
        print(
            "AVISO: ffmpeg não encontrado. Ele é necessário para juntar vídeo+áudio "
            "em MP4 e para gerar MP3.\n"
            "       Instale com: winget install Gyan.FFmpeg\n"
            "       ou aponte a pasta com --ffmpeg-location.",
            file=sys.stderr,
        )

    # Sem um runtime JS o YouTube esconde parte dos formatos, o que pode custar
    # qualidade. Só o Deno é habilitado por padrão pelo yt-dlp.
    if not shutil.which("deno") and not args.quiet:
        print(
            "DICA: nenhum runtime JavaScript encontrado. Sem ele o YouTube pode não "
            "oferecer todos os formatos (perde qualidade).\n"
            "      Instale com: winget install DenoLand.Deno",
            file=sys.stderr,
        )

    alvos = ("mp4", "mp3") if args.formato == "both" else (args.formato,)
    modo = "playlists completas" if args.playlist else "vídeos"
    print(f"{len(links)} link(s) em {args.arquivo} ({modo}) -> {', '.join(alvos)}")

    resultados: dict[str, tuple[int, list[tuple[str, str]]]] = {}
    for alvo in alvos:
        pasta = args.saida / alvo
        pasta.mkdir(parents=True, exist_ok=True)
        opts = opcoes_mp4(args, pasta) if alvo == "mp4" else opcoes_mp3(args, pasta)
        resultados[alvo] = baixar(links, opts, alvo.upper())

    print("\n" + "=" * 60)
    for alvo in alvos:
        baixados, falhas = resultados[alvo]
        ruins = f" ({len(falhas)} falha(s))" if falhas else ""
        print(f"{alvo.upper()}: {baixados} vídeo(s){ruins} em {args.saida / alvo}")

    todas_falhas = [(a, *f) for a, (_, fs) in resultados.items() for f in fs]
    if todas_falhas:
        print("\nFalhas:")
        for alvo, link, motivo in todas_falhas[:15]:
            print(f"  [{alvo}] {link}\n        {motivo}")
        if len(todas_falhas) > 15:
            print(f"  ... e mais {len(todas_falhas) - 15}")
        return 1

    print("Tudo pronto.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.", file=sys.stderr)
        raise SystemExit(130)
