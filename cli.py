"""Interface 1: o terminal.

Argumentos, mensagens e o resumo final. O que acontece numa rodada está em
`service.py`, que esta camada apenas chama — e a interface de navegador chama o
mesmo. Nada aqui sabe montar uma cadeia de formatos do yt-dlp.

Uso:
    uv run main.py links.txt --format mp4
    uv run main.py links.txt --format both -o saida
    uv run main.py https://youtu.be/jNQXAC9IVRw --format mp3
    uv run main.py playlists.txt --playlist --playlist-items 1-10
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import sys
from pathlib import Path

from yt_dlp.version import __version__ as YTDLP_VERSION

import service
from service import InvalidInput, Options

__version__ = "0.3.0"

#: Quantas falhas o resumo mostra antes de resumir o resto. Uma playlist de
#: canal inteiro pode falhar em dezenas de vídeos, e a tela do terminal rola.
FAILURE_LIMIT = 15


class Terminal(service.Observer):
    """No terminal, quem desenha o andamento é o próprio yt-dlp.

    Por isso só dois métodos: o cabeçalho de cada link e a linha de fecho de
    uma playlist. A barra de progresso, os avisos e os erros de cada vídeo já
    saem do yt-dlp direto no console — recolhê-los para redesenhar aqui seria
    reimplementar o que ele faz melhor.
    """

    def link_started(self, index: int, total: int, url: str, target: str) -> None:
        print(f"\n[{target.upper()} {index}/{total}] {url}", flush=True)

    def link_finished(
        self, link: str, downloaded: int, skipped: int, failed: int, playlist: str
    ) -> None:
        if not playlist:
            return
        extra = f", {skipped} já no histórico" if skipped else ""
        extra += f", {failed} com erro" if failed else ""
        print(f'  -> playlist "{playlist}": {downloaded} vídeo(s){extra}', flush=True)


def validator(function):
    """Adapta um validador do serviço ao argparse.

    As mensagens moram no serviço, num lugar só, porque a tela precisa das
    mesmas. O que muda entre as duas interfaces é só o tipo de exceção que
    cada uma sabe exibir.
    """

    @functools.wraps(function)
    def check(text: str):
        try:
            return function(text)
        except InvalidInput as error:
            raise argparse.ArgumentTypeError(str(error)) from None

    return check


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="youtube-downloader",
        description="Baixa vídeos e playlists do YouTube, a partir de links soltos ou de "
        "um arquivo .txt, em MP4 e/ou MP3, na melhor qualidade disponível.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "entries",
        nargs="+",
        metavar="ENTRADA",
        help="arquivos .txt com um link por linha e/ou links soltos",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"youtube-downloader {__version__} (yt-dlp {YTDLP_VERSION})",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=service.FORMATS,
        default="mp4",
        help="formato de saída: mp4, mp3 ou both (os dois)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("downloads"),
        help="pasta de destino (subpastas mp4/ e mp3/ são criadas dentro dela)",
    )
    parser.add_argument(
        "--max-height",
        type=validator(service.positive_int),
        default=None,
        help="limita a resolução do vídeo (ex.: 1080); por padrão usa a melhor",
    )
    parser.add_argument(
        "--audio-quality",
        type=validator(service.audio_quality),
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
        help="arquivo de histórico para não baixar de novo o que já foi baixado "
        "(um por formato: baixados.txt vira baixados.mp4.txt e baixados.mp3.txt)",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="lista o que seria baixado, sem baixar nada",
    )
    parser.add_argument(
        "-j",
        "--concurrent-fragments",
        type=validator(service.positive_int),
        default=4,
        help="fragmentos baixados em paralelo por vídeo (1 desliga)",
    )
    parser.add_argument(
        "--limit-rate",
        type=validator(service.rate_limit),
        default=None,
        help="limita a velocidade de download, ex.: 2M ou 500K",
    )
    parser.add_argument(
        "--cookies-from-browser",
        type=validator(service.browser_cookies),
        default=None,
        metavar="NAVEGADOR[+KEYRING][:PERFIL][::CONTAINER]",
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

    return parser.parse_args(argv)


def options_from(args: argparse.Namespace) -> Options:
    """Monta as opções da rodada a partir do que o argparse entendeu.

    O casamento é por nome: cada opção de linha de comando tem um campo de
    mesmo nome em `Options`. É o que mantém as duas listas sincronizadas sem um
    tradutor no meio — acrescentar uma opção é acrescentar um campo lá e um
    `add_argument` aqui.
    """
    fields = {f.name for f in dataclasses.fields(Options)}
    return Options(**{k: v for k, v in vars(args).items() if k in fields})


def warn_about_environment(options: Options) -> None:
    """O que falta na máquina e custa qualidade (ou impede o trabalho)."""
    if not service.ffmpeg_available(options.ffmpeg_location):
        print(
            "AVISO: ffmpeg não encontrado. Ele é necessário para juntar vídeo+áudio "
            "em MP4 e para gerar MP3.\n"
            "       Instale com: winget install Gyan.FFmpeg\n"
            "       ou aponte a pasta com --ffmpeg-location.",
            file=sys.stderr,
        )

    if not service.deno_available() and not options.quiet:
        print(
            "DICA: nenhum runtime JavaScript encontrado. Sem ele o YouTube pode não "
            "oferecer todos os formatos (perde qualidade).\n"
            "      Instale com: winget install DenoLand.Deno",
            file=sys.stderr,
        )


def print_summary(results: dict[str, service.Result], options: Options) -> int:
    """O fecho da rodada. Devolve o código de saída."""
    print("\n" + "=" * 60)
    for target, result in results.items():
        parts = [f"{result.downloaded} vídeo(s)" + (" a baixar" if options.dry_run else "")]
        if result.skipped:
            parts.append(f"{result.skipped} já no histórico")
        if result.failures:
            parts.append(f"{len(result.failures)} falha(s)")
        print(f"{target.upper()}: {', '.join(parts)} em {result.folder}")

    failures = [(target, f) for target, r in results.items() for f in r.failures]
    if failures:
        print("\nFalhas:")
        for target, f in failures[:FAILURE_LIMIT]:
            print(f"  [{target}] {f.link}\n        {f.reason}")
        if len(failures) > FAILURE_LIMIT:
            print(f"  ... e mais {len(failures) - FAILURE_LIMIT}")
        return 1

    if any(r.cancelled for r in results.values()):
        print("Interrompido.")
        return 130

    print("Tudo pronto.")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args(argv)
    try:
        links = service.read_links(args.entries)
        options = options_from(args)
    except InvalidInput as error:
        raise SystemExit(str(error)) from None

    warn_about_environment(options)

    mode = "playlists completas" if options.playlist else "vídeos"
    prefix = "[simulação] " if options.dry_run else ""
    print(f"{prefix}{len(links)} link(s) ({mode}) -> {', '.join(options.targets)}")

    results = service.run(links, options, Terminal())
    return print_summary(results, options)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.", file=sys.stderr)
        raise SystemExit(130) from None
