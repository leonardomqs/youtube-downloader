"""Uma rodada de download, sem interface.

A arquitetura em uma frase: **esta camada não sabe desenhar nada**. Ela recebe
links e opções e devolve o que foi baixado; quem imprime no terminal é o `cli`,
quem monta a página é o `webui`. As duas interfaces chamam `run` — a cadeia de
formatos, o histórico e a contagem de falhas existem em um lugar só.

O motivo é prático. A cadeia de formatos do MP4 (`bestvideo[ext=mp4]+...`) é
uma linha que decide a qualidade de tudo o que sai daqui; uma segunda cópia
dela dentro da tela funcionaria até a primeira correção feita de um lado só —
e quem usa a tela é exatamente quem não saberia dizer por que o vídeo saiu pior.

    service.py    o QUE acontece numa rodada        (sem interface)
    cli.py        interface 1: terminal
    webui/        interface 2: navegador

Nada aqui imprime e nada aqui chama `sys.exit`. O andamento sai pelo
`Observer`, que cada interface preenche como quiser: o terminal deixa o yt-dlp
desenhar a própria barra, a tela recolhe tudo e redesenha.

Sobre o idioma: identificadores em inglês, comentários e textos em português —
os textos porque são lidos por quem usa o programa, e essa pessoa é brasileira.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.cookies import SUPPORTED_BROWSERS, SUPPORTED_KEYRINGS
from yt_dlp.utils import DownloadCancelled, parse_bytes

__all__ = [
    "FORMATS",
    "BROWSERS",
    "InvalidInput",
    "Failure",
    "Item",
    "Progress",
    "Result",
    "Options",
    "Observer",
    "Downloader",
    "find_ffmpeg",
    "ffmpeg_available",
    "deno_available",
    "is_link",
    "read_links",
    "read_links_text",
    "output_template",
    "archive_path",
    "ydl_options",
    "count_result",
    "download",
    "run",
    "positive_int",
    "audio_quality",
    "rate_limit",
    "browser_cookies",
]

FORMATS = ("mp4", "mp3", "both")

#: Os navegadores de onde dá para ler cookies. Vem do yt-dlp para a lista da
#: tela nunca divergir da que a validação aceita.
BROWSERS = tuple(sorted(SUPPORTED_BROWSERS))

#: O nome de cada pós-processamento do yt-dlp na língua de quem está olhando.
#: Sem isto a barra fica parada em 100% enquanto o ffmpeg converte um MP3 de
#: uma hora, e a tela parece travada quando na verdade está trabalhando.
PHASE_LABELS = {
    "FFmpegMerger": "juntando vídeo e áudio",
    "FFmpegVideoRemuxer": "ajustando o arquivo",
    "FFmpegExtractAudio": "convertendo para MP3",
    "FFmpegMetadata": "gravando os metadados",
    "EmbedThumbnail": "embutindo a capa",
    "MoveFiles": "guardando na pasta",
}

#: As fases que não vêm de um pós-processador. Ficam aqui, e não espalhadas
#: pelo código, porque são o que a tela escreve embaixo do título.
PREPARING = "preparando"
DOWNLOADING = "baixando"
RECORDING = "gravando ao vivo"
PROCESSING = "processando"
FAILED = "erro"

#: Trecho da mensagem do yt-dlp quando o link não oferece a live desde o início.
#: É o que permite trocá-la por uma que diga o que fazer; se o yt-dlp mudar o
#: texto, a mensagem original continua aparecendo.
_NO_LIVE_START = "--live-from-start is passed"
NO_LIVE_START_REASON = (
    "Este link não pode ser baixado desde o início (só YouTube, Twitch, TVer e "
    "mellow-fan oferecem isso). Desligue a opção de baixar a live desde o início "
    "para gravar a partir de agora."
)


class InvalidInput(ValueError):
    """Um valor que veio do usuário e não serve.

    A mensagem é escrita para ele, não para quem mantém o programa: o terminal
    a mostra como erro de argumento e a tela, como aviso no formulário.
    """


# --------------------------------------------------------------- o que sai


@dataclass(slots=True)
class Failure:
    """Um link (ou um vídeo dentro dele) que não veio, e o motivo."""

    link: str
    reason: str


@dataclass(slots=True)
class Item:
    """Um vídeo que a rodada terminou de baixar."""

    id: str
    title: str
    target: str
    position: str = ""


@dataclass(slots=True)
class Progress:
    """O andamento de agora. Números crus: formatar é trabalho da interface."""

    phase: str = PREPARING
    title: str = ""
    percent: float = 0.0
    speed: float = 0.0
    eta: int | None = None
    target: str = ""
    current_link: int = 0
    total_links: int = 0


@dataclass(slots=True)
class Result:
    """O que uma passada de formato produziu."""

    target: str
    folder: Path
    downloaded: int = 0
    skipped: int = 0
    failures: list[Failure] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    cancelled: bool = False


# ------------------------------------------------------------ o que entra


@dataclass
class Options:
    """Uma rodada, do jeito que o usuário a descreve.

    Os nomes são os mesmos das opções de linha de comando — é o que permite ao
    `cli` montar isto direto do `argparse` e à tela montar do formulário, sem
    tradutor no meio.
    """

    format: str = "mp4"
    output: Path = Path("downloads")
    max_height: int | None = None
    audio_quality: str = "0"
    playlist: bool = False
    playlist_items: str | None = None
    playlist_reverse: bool = False
    playlist_folder: bool = True
    live_from_start: bool = False
    archive: Path | None = None
    dry_run: bool = False
    concurrent_fragments: int = 4
    limit_rate: float | None = None
    cookies_from_browser: tuple | None = None
    ffmpeg_location: str | None = None
    ascii: bool = False
    quiet: bool = False

    def __post_init__(self) -> None:
        if self.format not in FORMATS:
            raise InvalidInput(f"formato desconhecido: {self.format}")
        self.output = Path(self.output)
        if self.archive:
            self.archive = Path(self.archive)
        # Selecionar itens ou inverter a ordem só faz sentido com a lista
        # inteira; quem pediu um dos dois já disse que quer a playlist.
        if self.playlist_items or self.playlist_reverse:
            self.playlist = True

    @property
    def targets(self) -> tuple[str, ...]:
        return ("mp4", "mp3") if self.format == "both" else (self.format,)


class Observer:
    """O que uma interface quer saber enquanto a rodada acontece.

    Todos os métodos não fazem nada por padrão: cada interface sobrescreve os
    que usa. O terminal só quer saber quando um link começa — o resto ele
    deixa o próprio yt-dlp desenhar. A tela quer tudo, porque precisa
    redesenhar sozinha.
    """

    def link_started(self, index: int, total: int, url: str, target: str) -> None:
        """Um link da fila começou."""

    def progress(self, p: Progress) -> None:
        """Andamento do vídeo atual. Chamado muitas vezes por segundo."""

    def item(self, i: Item) -> None:
        """Um vídeo terminou."""

    def failure(self, f: Failure) -> None:
        """Um link, ou um vídeo dentro dele, não veio."""

    def link_finished(
        self, link: str, downloaded: int, skipped: int, failed: int, playlist: str
    ) -> None:
        """Um link da fila acabou. `playlist` é vazio para vídeo avulso."""

    def log(self, line: str) -> None:
        """Uma linha de saída do yt-dlp."""

    def should_stop(self) -> bool:
        """True quando a rodada deve ser interrompida assim que possível."""
        return False


class _Logger:
    """Logger do yt-dlp que entrega as linhas ao observador, não ao console.

    Existe só quando a interface pede (a tela). Sem ele, o yt-dlp escreve no
    terminal — que é o certo para o `cli` e inútil para quem está no navegador.
    """

    #: As linhas de progresso já viram barra na tela; no log seriam centenas
    #: de cópias quase iguais empurrando o resto para fora.
    _PROGRESS_LINE = re.compile(r"^\[download\]\s+\d")

    def __init__(self, observer: Observer) -> None:
        self.observer = observer

    def _send(self, message) -> None:
        line = str(message).strip()
        if line and not self._PROGRESS_LINE.match(line):
            self.observer.log(line)

    def debug(self, message) -> None:
        # O yt-dlp manda tudo por aqui, inclusive o que só interessa a quem
        # depura; as linhas de verdade vêm sem o prefixo.
        if not str(message).startswith("[debug] "):
            self._send(message)

    def info(self, message) -> None:
        self._send(message)

    def warning(self, message) -> None:
        self._send(message)

    def error(self, message) -> None:
        self._send(message)


class Downloader(YoutubeDL):
    """YoutubeDL que guarda os erros e conta os itens pulados pelo histórico.

    Como os erros de cada vídeo são ignorados (para que um item quebrado no
    meio de uma playlist não derrube o resto), é aqui que os motivos das
    falhas são coletados para o resumo final.

    Um item pulado pelo --archive volta como None no resultado, exatamente
    como um que falhou. Contar os pulados é o que permite separar os dois no
    resumo (sem isso, rodar de novo com histórico acusaria tudo como falha).

    É aqui também que uma live para sem perder o que já veio: ver
    `_until_stopped`.
    """

    def __init__(self, params=None, **kwargs):
        # Antes do super(): a inicialização do yt-dlp já pode reportar erro.
        self.errors: list[str] = []
        self.skipped = 0
        self.stop_requested = lambda: False
        super().__init__(params, **kwargs)

    def report_error(self, message, *args, **kwargs):
        self.errors.append(str(message).strip())
        return super().report_error(message, *args, **kwargs)

    def in_download_archive(self, info_dict):
        # O yt-dlp só consulta o histórico para decidir se pula o item, então
        # uma resposta positiva aqui equivale a um item pulado.
        skipped = super().in_download_archive(info_dict)
        if skipped:
            self.skipped += 1
        return skipped

    def process_info(self, info_dict):
        if self.stop_requested():
            raise DownloadCancelled("interrompido a pedido")
        # Vídeo e som são dois formatos, cada um com a sua sequência de trechos.
        for fmt in info_dict.get("requested_formats") or [info_dict]:
            if callable(fmt.get("fragments")):
                fmt["fragments"] = _until_stopped(fmt["fragments"], self.stop_requested)
        return super().process_info(info_dict)

    def record_download_archive(self, info_dict):
        # Uma gravação interrompida não é o vídeo inteiro.
        if not self.stop_requested():
            super().record_download_archive(info_dict)


def _until_stopped(fragments, should_stop):
    """Os trechos de uma live, até alguém pedir para parar.

    Interromper com exceção deixaria vídeo e som em arquivos `.part` soltos. Uma
    sequência que acaba é o que o yt-dlp vê quando a live termina: ele fecha os
    arquivos, junta e pós-processa o que já veio.
    """

    def generate(ctx):
        for fragment in fragments(ctx):
            if should_stop():
                return
            yield fragment

    return generate


# --------------------------------------------------------------- ambiente


@functools.cache
def find_ffmpeg(manual_path: str | None) -> str | None:
    """Localiza o ffmpeg: opção manual, PATH ou o binário embutido do imageio-ffmpeg.

    Devolve o caminho a passar para o yt-dlp (pasta ou executável), ou None
    quando o ffmpeg já está no PATH e o yt-dlp o encontra sozinho.
    """
    if manual_path:
        return manual_path

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


def ffmpeg_available(manual_path: str | None = None) -> bool:
    """True se há algum ffmpeg utilizável (informado, no PATH ou embutido)."""
    if manual_path or shutil.which("ffmpeg"):
        return True
    return find_ffmpeg(None) is not None


def deno_available() -> bool:
    """Sem um runtime JavaScript o YouTube esconde parte dos formatos.

    Não impede o download; custa qualidade. Só o Deno é habilitado por padrão
    pelo yt-dlp, então é ele que as duas interfaces procuram.
    """
    return shutil.which("deno") is not None


# ------------------------------------------------------------------ links


def is_link(text: str) -> bool:
    return text.startswith(("http://", "https://", "www."))


def read_links(entries) -> list[str]:
    """Junta os links das entradas, sem repetir e preservando a ordem.

    Cada entrada é um link solto ou um arquivo .txt com um link por linha;
    dentro do arquivo, linhas vazias e comentários (#) são ignorados.
    """
    links: list[str] = []
    seen: set[str] = set()

    def add(link: str) -> None:
        if link not in seen:
            seen.add(link)
            links.append(link)

    for entry in entries:
        text = str(entry).strip()
        if not text:
            continue
        if is_link(text):
            add(text)
            continue

        path = Path(text)
        if not path.is_file():
            raise InvalidInput(f"Não é um link nem um arquivo existente: {text}")

        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                add(line)

    if not links:
        raise InvalidInput("Nenhum link encontrado.")
    return links


def read_links_text(text: str) -> list[str]:
    """Os links de um campo de texto: um por linha, comentários ignorados.

    É o que a tela recebe. Um caminho de arquivo digitado ali também vale,
    porque `read_links` aceita os dois — quem tem a lista em disco não precisa
    copiá-la para dentro do formulário.
    """
    lines = [line.strip() for line in text.splitlines()]
    return read_links([line for line in lines if line and not line.startswith("#")])


def output_template(folder: Path, with_subfolder: bool) -> str:
    """Monta o outtmpl: vídeos de playlist ganham subpasta e numeração.

    Um vídeo avulso vira "Título [id].mp4" e um de playlist vira
    "Nome da playlist/007 - Título [id].mp4". A barra precisa ficar fora do
    campo (`%(playlist_title|)s/`): uma barra vinda de dentro do campo seria
    sanitizada e viraria parte do nome do arquivo em vez de criar a pasta.
    Sem playlist o campo fica vazio e o caminho colapsa para a pasta base.
    """
    parts = []
    if with_subfolder:
        parts.append("%(playlist_title|)s/")
    parts.append("%(playlist_index&{:03d} - |)s")
    parts.append("%(title)s [%(id)s].%(ext)s")
    return str(folder).replace("\\", "/") + "/" + "".join(parts)


def archive_path(base: Path, target: str) -> Path:
    """Separa o histórico por formato: baixados.txt -> baixados.mp4.txt.

    O histórico do yt-dlp guarda só o id do vídeo, sem o formato. Com um
    arquivo único, em --format both a passada de MP3 pularia tudo o que a de
    MP4 acabou de registrar e nenhum áudio seria gerado.
    """
    base = Path(base)
    return base.with_name(f"{base.stem}.{target}{base.suffix or '.txt'}")


# --------------------------------------------------------- opções do yt-dlp


def ydl_options(options: Options, target: str, observer: Observer | None = None) -> dict:
    """Traduz uma rodada para o dicionário que o yt-dlp entende."""
    folder = options.output / target
    ffmpeg_dir = find_ffmpeg(options.ffmpeg_location)

    opts = {
        "outtmpl": output_template(folder, options.playlist_folder),
        "noplaylist": not options.playlist,
        # Um vídeo indisponível não pode abortar o resto da playlist; as falhas
        # são recolhidas pelo Downloader e mostradas no resumo.
        "ignoreerrors": True,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
        "concurrent_fragment_downloads": options.concurrent_fragments,
        "quiet": options.quiet,
        "no_warnings": options.quiet,
        "consoletitle": False,
        "windowsfilenames": os.name == "nt",
        "restrictfilenames": options.ascii,
    }
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    if options.live_from_start:
        opts["live_from_start"] = True
        # Em paralelo, o yt-dlp pede todos os trechos que existem de uma vez e
        # só grava no fim: parar durante o atraso esperaria baixar a fila toda.
        opts["concurrent_fragment_downloads"] = 1
    if options.playlist_items:
        opts["playlist_items"] = options.playlist_items
    if options.playlist_reverse:
        opts["playlistreverse"] = True
    if options.archive:
        opts["download_archive"] = str(archive_path(options.archive, target))
    if options.cookies_from_browser:
        opts["cookiesfrombrowser"] = options.cookies_from_browser
    if options.limit_rate:
        opts["ratelimit"] = options.limit_rate
    if options.dry_run:
        # simulate resolve tudo (playlists inclusive) sem baixar nada; o
        # forceprint é o que transforma a simulação em uma lista legível.
        opts["simulate"] = True
        opts["forceprint"] = {"video": ["%(playlist_index&{:03d} - |)s%(title)s [%(id)s]"]}

    if target == "mp4":
        # Filtro estrito: com "<=?" o yt-dlp também aceitaria formatos sem o
        # campo height, furando o limite. Se nada couber, a cadeia cai no
        # "best" final.
        height = f"[height<={options.max_height}]" if options.max_height else ""
        opts.update(
            {
                "format": (
                    f"bestvideo{height}[ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo{height}+bestaudio/best{height}/best"
                ),
                "merge_output_format": "mp4",
                "postprocessors": [
                    {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                    {"key": "FFmpegMetadata", "add_metadata": True},
                ],
            }
        )
    else:
        opts.update(
            {
                "format": "bestaudio/best",
                "writethumbnail": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": options.audio_quality,
                    },
                    {"key": "FFmpegMetadata", "add_metadata": True},
                    {"key": "EmbedThumbnail", "already_have_thumbnail": False},
                ],
            }
        )

    if observer is not None:
        opts.update(_hooks(options, target, observer))
    return opts


def _hooks(options: Options, target: str, observer: Observer) -> dict:
    """Os ganchos de progresso do yt-dlp, traduzidos para o observador.

    São dois porque o trabalho tem duas metades bem diferentes: o download
    (bytes, velocidade, ETA) e o pós-processamento (o ffmpeg juntando ou
    convertendo, que em áudio longo é a parte demorada). Só o primeiro daria
    uma tela que congela em 100% e parece travada.
    """
    state = Progress(target=target)
    #: Em MP4 o yt-dlp baixa dois arquivos por vídeo — a imagem e o som, que
    #: só depois viram um só. São dois "finished" para o mesmo vídeo, e sem
    #: isto ele apareceria duas vezes na lista da tela.
    announced: set[str] = set()

    def recording(info: dict) -> bool:
        return options.live_from_start and bool(info.get("is_live"))

    def stop_if_asked(info: dict) -> None:
        # Uma gravação para pelo fim dos trechos (`_until_stopped`) e ainda
        # precisa terminar a junção; a exceção aqui jogaria isso fora.
        if observer.should_stop() and not recording(info):
            # O yt-dlp trata esta exceção como fim de rodada, não como erro do
            # vídeo: é a forma suportada de interromper no meio de um fragmento.
            raise DownloadCancelled("interrompido a pedido")

    def on_download(d: dict) -> None:
        info = d.get("info_dict") or {}
        stop_if_asked(info)
        state.title = info.get("title") or state.title
        status = d.get("status")

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            state.phase = RECORDING if recording(info) else DOWNLOADING
            state.percent = (done / total * 100) if total else 0.0
            state.speed = d.get("speed") or 0.0
            state.eta = d.get("eta")
        elif status == "finished":
            state.phase = PROCESSING
            state.percent = 100.0
            state.speed = 0.0
            state.eta = None
            identifier = str(info.get("id") or "")
            if identifier and identifier not in announced:
                announced.add(identifier)
                index = info.get("playlist_index")
                observer.item(
                    Item(
                        id=identifier,
                        title=info.get("title") or identifier,
                        target=target,
                        position=f"{index:03d}" if isinstance(index, int) else "",
                    )
                )
        elif status == "error":
            state.phase = FAILED

        observer.progress(state)

    def on_postprocessing(d: dict) -> None:
        stop_if_asked(d.get("info_dict") or {})
        name = d.get("postprocessor") or ""
        if d.get("status") == "started":
            state.phase = PHASE_LABELS.get(name, PROCESSING)
            observer.progress(state)

    hooks = {
        "progress_hooks": [on_download],
        "postprocessor_hooks": [on_postprocessing],
        "logger": _Logger(observer),
        # Com logger, o yt-dlp mandaria cada atualização da barra como uma
        # linha; a tela desenha a sua própria.
        "noprogress": True,
    }
    if options.dry_run:
        # Em simulação não há gancho de download: o que a tela mostra é o que
        # o forceprint imprime, e isso passa pelo logger.
        hooks.pop("progress_hooks")
    return hooks


# ---------------------------------------------------------------- a rodada


def count_result(info) -> tuple[int, int]:
    """Conta (baixados, falhados) no resultado de um link.

    Playlists trazem "entries" (que podem ter playlists aninhadas, no caso de
    canais); cada entrada que falhou vem como None.
    """
    if info is None:
        return 0, 1

    entries = info.get("entries")
    if entries is None:
        return 1, 0

    downloaded = failed = 0
    for entry in entries:
        ok, bad = count_result(entry)
        downloaded += ok
        failed += bad
    return downloaded, failed


def download(
    links: list[str],
    options: Options,
    target: str,
    observer: Observer | None = None,
) -> Result:
    """Baixa cada link (vídeo ou playlist) num formato. Não imprime nada."""
    observer = observer or Observer()
    result = Result(target=target, folder=options.output / target)
    total = len(links)

    class _Collector(Observer):
        """Fica entre a rodada e a interface para guardar o que foi acontecendo.

        Os itens e as falhas precisam existir no `Result` no fim, e a interface
        precisa vê-los aparecer durante — em vez de as duas coisas serem
        construídas separadamente, o que chega aqui é repassado adiante.
        """

        def link_started(self, index, total, url, target):
            observer.link_started(index, total, url, target)

        def progress(self, p):
            observer.progress(p)

        def item(self, i):
            result.items.append(i)
            observer.item(i)

        def failure(self, f):
            result.failures.append(f)
            observer.failure(f)

        def link_finished(self, link, downloaded, skipped, failed, playlist):
            observer.link_finished(link, downloaded, skipped, failed, playlist)

        def log(self, line):
            observer.log(line)

        def should_stop(self):
            return observer.should_stop()

    collector = _Collector()
    opts = ydl_options(options, target, collector)

    with Downloader(opts) as ydl:
        ydl.stop_requested = collector.should_stop
        for i, link in enumerate(links, start=1):
            observer.link_started(i, total, link, target)
            ydl.errors.clear()
            ydl.skipped = 0
            try:
                info = ydl.extract_info(link, download=True)
            except DownloadCancelled:
                result.cancelled = True
                break
            except KeyboardInterrupt:
                raise
            except Exception as error:  # não deixa um link derrubar a fila inteira
                collector.failure(Failure(link, f"{type(error).__name__}: {error}"))
                continue

            ok, bad = count_result(info)
            # Pulado pelo histórico e falhado chegam aqui do mesmo jeito (None);
            # só o que sobra depois de descontar os pulados é falha de verdade.
            already = min(ydl.skipped, bad)
            bad -= already
            result.downloaded += ok
            result.skipped += already

            if bad or (ok == 0 and already == 0):
                for reason in ydl.errors or ["motivo desconhecido"]:
                    collector.failure(Failure(link, _explain(reason)))

            if options.dry_run:
                _announce_simulated(info, target, collector)

            playlist = ""
            if info is not None and info.get("entries") is not None:
                playlist = info.get("title") or "playlist"
            collector.link_finished(link, ok, already, bad, playlist)

            # Uma gravação parada volta como um download normal: é aqui que a
            # rodada fica sabendo que não deve seguir para o próximo link.
            if collector.should_stop():
                result.cancelled = True
                break

    return result


def _explain(reason: str) -> str:
    """Troca por uma instrução o erro que depende de uma opção desta rodada."""
    return NO_LIVE_START_REASON if _NO_LIVE_START in reason else reason


def _announce_simulated(info, target: str, observer: Observer) -> None:
    """Conta ao observador os vídeos que uma simulação encontrou.

    Numa simulação não há download, e sem download o yt-dlp não chama gancho de
    progresso nenhum: os vídeos da lista só existem no resultado que ele
    devolve no fim do link. Sem isto, a tela de "o que será baixado" ficaria
    vazia justamente na parte que ela existe para mostrar.
    """
    if info is None:
        return

    entries = info.get("entries")
    if entries is None:
        entries = [info]

    for entry in entries:
        if entry is None:
            continue
        if entry.get("entries") is not None:  # canal: playlist de playlists
            _announce_simulated(entry, target, observer)
            continue

        index = entry.get("playlist_index")
        identifier = str(entry.get("id") or "")
        observer.item(
            Item(
                id=identifier,
                title=entry.get("title") or identifier,
                target=target,
                position=f"{index:03d}" if isinstance(index, int) else "",
            )
        )


def run(
    links: list[str],
    options: Options,
    observer: Observer | None = None,
) -> dict[str, Result]:
    """A rodada inteira: um resultado por formato pedido.

    É o que as duas interfaces chamam. Criar as pastas faz parte da rodada —
    menos em simulação, que não deve deixar pasta vazia para trás.
    """
    observer = observer or Observer()
    results: dict[str, Result] = {}

    for target in options.targets:
        if not options.dry_run:
            (options.output / target).mkdir(parents=True, exist_ok=True)
        result = download(links, options, target, observer)
        results[target] = result
        if result.cancelled:
            break

    return results


# ------------------------------------------------------------- validações


def positive_int(text: str) -> int:
    try:
        value = int(str(text))
    except ValueError:
        raise InvalidInput(f"esperado um número inteiro: {text}") from None
    if value < 1:
        raise InvalidInput(f"precisa ser maior que zero: {text}")
    return value


def audio_quality(text: str) -> str:
    """Aceita 0-10 (VBR do LAME) ou um bitrate fixo como 320 / 320K."""
    text = str(text)
    if not re.fullmatch(r"\d+[kK]?", text):
        raise InvalidInput(f"qualidade inválida: {text} (use 0-10 para VBR ou um bitrate como 320)")
    return text


def rate_limit(text: str) -> float:
    """Aceita 500K, 2M, 1.5M... (o mesmo formato do --limit-rate do yt-dlp)."""
    value = parse_bytes(str(text))
    if not value:
        raise InvalidInput(f"taxa inválida: {text} (use algo como 500K ou 2M)")
    return value


def browser_cookies(text: str) -> tuple[str, str | None, str | None, str | None]:
    """Traduz NAVEGADOR[+KEYRING][:PERFIL][::CONTAINER] na tupla que o yt-dlp espera.

    A API do yt-dlp (ao contrário da linha de comando dele) não faz esse
    parsing: ela recebe a tupla pronta, com o nome do navegador já validado e
    em minúsculas.
    """
    match = re.fullmatch(
        r"""(?x)
            (?P<name>[^+:]+)
            (?:\s*\+\s*(?P<keyring>[^:]+))?
            (?:\s*:\s*(?!:)(?P<profile>.+?))?
            (?:\s*::\s*(?P<container>.+))?
        """,
        str(text),
    )
    if match is None:
        raise InvalidInput(
            f"formato inválido: {text} (use NAVEGADOR[+KEYRING][:PERFIL][::CONTAINER])"
        )

    name, keyring, profile, container = match.group("name", "keyring", "profile", "container")
    name = name.strip().lower()
    if name not in SUPPORTED_BROWSERS:
        raise InvalidInput(
            f'navegador não suportado: "{name}" '
            f"(disponíveis: {', '.join(sorted(SUPPORTED_BROWSERS))})"
        )
    if keyring is not None:
        keyring = keyring.strip().upper()
        if keyring not in SUPPORTED_KEYRINGS:
            raise InvalidInput(
                f'keyring não suportado: "{keyring}" '
                f"(disponíveis: {', '.join(sorted(SUPPORTED_KEYRINGS))})"
            )
    return (name, profile, keyring, container)
