"""Baixa vídeos e playlists do YouTube (links em arquivo .txt ou na linha de comando).

Uso:
    uv run main.py links.txt --format mp4
    uv run main.py links.txt --format mp3
    uv run main.py links.txt --format both -o saida
    uv run main.py https://youtu.be/jNQXAC9IVRw --format mp3
    uv run main.py playlists.txt --playlist --playlist-items 1-10
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import shutil
import sys
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.cookies import SUPPORTED_BROWSERS, SUPPORTED_KEYRINGS
from yt_dlp.utils import parse_bytes
from yt_dlp.version import __version__ as YTDLP_VERSION

__version__ = "0.2.0"

FORMATOS = ("mp4", "mp3", "both")


class Baixador(YoutubeDL):
    """YoutubeDL que guarda os erros e conta os itens pulados pelo histórico.

    Como os erros de cada vídeo são ignorados (para que um item quebrado no
    meio de uma playlist não derrube o resto), é aqui que os motivos das
    falhas são coletados para o resumo final.

    Um item pulado pelo --archive volta como None no resultado, exatamente
    como um que falhou. Contar os pulados é o que permite separar os dois no
    resumo (sem isso, rodar de novo com histórico acusaria tudo como falha).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.erros: list[str] = []
        self.pulados = 0

    def report_error(self, message, *args, **kwargs):
        self.erros.append(str(message).strip())
        return super().report_error(message, *args, **kwargs)

    def in_download_archive(self, info_dict):
        # O yt-dlp só consulta o histórico para decidir se pula o item, então
        # uma resposta positiva aqui equivale a um item pulado.
        pulado = super().in_download_archive(info_dict)
        if pulado:
            self.pulados += 1
        return pulado


@functools.cache
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


def ffmpeg_disponivel(caminho_manual: str | None) -> bool:
    """True se há algum ffmpeg utilizável (informado, no PATH ou embutido)."""
    if caminho_manual or shutil.which("ffmpeg"):
        return True
    return encontrar_ffmpeg(None) is not None


def eh_link(texto: str) -> bool:
    return texto.startswith(("http://", "https://", "www."))


def ler_links(entradas: list[str]) -> list[str]:
    """Junta os links das entradas, sem repetir e preservando a ordem.

    Cada entrada é um link solto ou um arquivo .txt com um link por linha;
    dentro do arquivo, linhas vazias e comentários (#) são ignorados.
    """
    links: list[str] = []
    vistos: set[str] = set()

    def adicionar(link: str) -> None:
        if link not in vistos:
            vistos.add(link)
            links.append(link)

    for entrada in entradas:
        texto = str(entrada)
        if eh_link(texto):
            adicionar(texto)
            continue

        caminho = Path(texto)
        if not caminho.is_file():
            raise SystemExit(f"Não é um link nem um arquivo existente: {texto}")

        for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#"):
                adicionar(linha)

    if not links:
        raise SystemExit(f"Nenhum link encontrado em {', '.join(map(str, entradas))}")
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


def caminho_archive(base: Path, alvo: str) -> Path:
    """Separa o histórico por formato: baixados.txt -> baixados.mp4.txt.

    O histórico do yt-dlp guarda só o id do vídeo, sem o formato. Com um
    arquivo único, em --format both a passada de MP3 pularia tudo o que a de
    MP4 acabou de registrar e nenhum áudio seria gerado.
    """
    return base.with_name(f"{base.stem}.{alvo}{base.suffix or '.txt'}")


def opcoes_base(args, pasta: Path, alvo: str) -> dict:
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
        "concurrent_fragment_downloads": args.concurrent_fragments,
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
        opts["download_archive"] = str(caminho_archive(args.archive, alvo))
    if args.cookies_from_browser:
        opts["cookiesfrombrowser"] = args.cookies_from_browser
    if args.limit_rate:
        opts["ratelimit"] = args.limit_rate
    if args.dry_run:
        # simulate resolve tudo (playlists inclusive) sem baixar nada; o
        # forceprint é o que transforma a simulação em uma lista legível.
        opts["simulate"] = True
        opts["forceprint"] = {"video": ["%(playlist_index&{:03d} - |)s%(title)s [%(id)s]"]}
    return opts


def opcoes_mp4(args, pasta: Path) -> dict:
    """Melhor vídeo + melhor áudio disponíveis, combinados em MP4."""
    opts = opcoes_base(args, pasta, "mp4")
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
    opts = opcoes_base(args, pasta, "mp3")
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


def baixar(links: list[str], opts: dict, rotulo: str) -> tuple[int, int, list[tuple[str, str]]]:
    """Baixa cada link (vídeo ou playlist). Retorna (baixados, pulados, falhas)."""
    falhas: list[tuple[str, str]] = []
    baixados = pulados = 0
    total = len(links)

    with Baixador(opts) as ydl:
        for i, link in enumerate(links, start=1):
            print(f"\n[{rotulo} {i}/{total}] {link}", flush=True)
            ydl.erros.clear()
            ydl.pulados = 0
            try:
                info = ydl.extract_info(link, download=True)
            except KeyboardInterrupt:
                raise
            except Exception as erro:  # não deixa um link derrubar a fila inteira
                falhas.append((link, f"{type(erro).__name__}: {erro}"))
                print(f"  -> falhou: {erro}", file=sys.stderr, flush=True)
                continue

            ok, ruins = contar_resultado(info)
            # Pulado pelo histórico e falhado chegam aqui do mesmo jeito (None);
            # só o que sobra depois de descontar os pulados é falha de verdade.
            ja_baixados = min(ydl.pulados, ruins)
            ruins -= ja_baixados
            baixados += ok
            pulados += ja_baixados

            if info is not None and info.get("entries") is not None:
                nome = info.get("title") or "playlist"
                extra = f", {ja_baixados} já no histórico" if ja_baixados else ""
                extra += f", {ruins} com erro" if ruins else ""
                print(f'  -> playlist "{nome}": {ok} vídeo(s){extra}', flush=True)

            if ruins or (ok == 0 and ja_baixados == 0):
                for motivo in ydl.erros or ["motivo desconhecido"]:
                    falhas.append((link, motivo))

    return baixados, pulados, falhas


def inteiro_positivo(texto: str) -> int:
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(f"esperado um número inteiro: {texto}") from None
    if valor < 1:
        raise argparse.ArgumentTypeError(f"precisa ser maior que zero: {texto}")
    return valor


def qualidade_audio(texto: str) -> str:
    """Aceita 0-10 (VBR do LAME) ou um bitrate fixo como 320 / 320K."""
    if not re.fullmatch(r"\d+[kK]?", texto):
        raise argparse.ArgumentTypeError(
            f"qualidade inválida: {texto} (use 0-10 para VBR ou um bitrate como 320)"
        )
    return texto


def taxa_maxima(texto: str) -> float:
    """Aceita 500K, 2M, 1.5M... (o mesmo formato do --limit-rate do yt-dlp)."""
    valor = parse_bytes(texto)
    if not valor:
        raise argparse.ArgumentTypeError(f"taxa inválida: {texto} (use algo como 500K ou 2M)")
    return valor


def cookies_navegador(texto: str) -> tuple[str, str | None, str | None, str | None]:
    """Traduz NAVEGADOR[+KEYRING][:PERFIL][::CONTAINER] na tupla que o yt-dlp espera.

    A API do yt-dlp (ao contrário da linha de comando dele) não faz esse
    parsing: ela recebe a tupla pronta, com o nome do navegador já validado e
    em minúsculas.
    """
    mobj = re.fullmatch(
        r"""(?x)
            (?P<name>[^+:]+)
            (?:\s*\+\s*(?P<keyring>[^:]+))?
            (?:\s*:\s*(?!:)(?P<profile>.+?))?
            (?:\s*::\s*(?P<container>.+))?
        """,
        texto,
    )
    if mobj is None:
        raise argparse.ArgumentTypeError(
            f"formato inválido: {texto} (use NAVEGADOR[+KEYRING][:PERFIL][::CONTAINER])"
        )

    nome, keyring, perfil, container = mobj.group("name", "keyring", "profile", "container")
    nome = nome.strip().lower()
    if nome not in SUPPORTED_BROWSERS:
        raise argparse.ArgumentTypeError(
            f'navegador não suportado: "{nome}" '
            f"(disponíveis: {', '.join(sorted(SUPPORTED_BROWSERS))})"
        )
    if keyring is not None:
        keyring = keyring.strip().upper()
        if keyring not in SUPPORTED_KEYRINGS:
            raise argparse.ArgumentTypeError(
                f'keyring não suportado: "{keyring}" '
                f"(disponíveis: {', '.join(sorted(SUPPORTED_KEYRINGS))})"
            )
    return (nome, perfil, keyring, container)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Baixa vídeos e playlists do YouTube, a partir de links soltos ou de "
        "um arquivo .txt, em MP4 e/ou MP3, na melhor qualidade disponível.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "entradas",
        nargs="+",
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
        type=inteiro_positivo,
        default=None,
        help="limita a resolução do vídeo (ex.: 1080); por padrão usa a melhor",
    )
    parser.add_argument(
        "--audio-quality",
        type=qualidade_audio,
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
        type=inteiro_positivo,
        default=4,
        help="fragmentos baixados em paralelo por vídeo (1 desliga)",
    )
    parser.add_argument(
        "--limit-rate",
        type=taxa_maxima,
        default=None,
        help="limita a velocidade de download, ex.: 2M ou 500K",
    )
    parser.add_argument(
        "--cookies-from-browser",
        type=cookies_navegador,
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
    links = ler_links(args.entradas)

    if not ffmpeg_disponivel(args.ffmpeg_location):
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
    prefixo = "[simulação] " if args.dry_run else ""
    print(f"{prefixo}{len(links)} link(s) ({modo}) -> {', '.join(alvos)}")

    resultados: dict[str, tuple[int, int, list[tuple[str, str]]]] = {}
    for alvo in alvos:
        pasta = args.saida / alvo
        if not args.dry_run:
            pasta.mkdir(parents=True, exist_ok=True)
        opts = opcoes_mp4(args, pasta) if alvo == "mp4" else opcoes_mp3(args, pasta)
        resultados[alvo] = baixar(links, opts, alvo.upper())

    print("\n" + "=" * 60)
    for alvo in alvos:
        baixados, pulados, falhas = resultados[alvo]
        partes = [f"{baixados} vídeo(s)" + (" a baixar" if args.dry_run else "")]
        if pulados:
            partes.append(f"{pulados} já no histórico")
        if falhas:
            partes.append(f"{len(falhas)} falha(s)")
        print(f"{alvo.upper()}: {', '.join(partes)} em {args.saida / alvo}")

    todas_falhas = [(a, *f) for a, (_, _, fs) in resultados.items() for f in fs]
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
        raise SystemExit(130) from None
