"""As páginas da interface.

São duas telas sobre a mesma rodada:

    /            o que baixar: links, formato, pasta
    /run/<id>    o andamento, e o resumo quando acaba

O que a tela resolve e o terminal não resolvia: **acompanhar**. Pela linha de
comando, uma playlist de quarenta vídeos é meia hora de texto rolando, em que
saber quanto falta exige ler a barra do vídeo atual e contar o resto de cabeça.
Aqui a mesma rodada tem um número, uma barra e uma lista do que já entrou — e
um botão para parar, que no terminal era um Ctrl+C no meio de um arquivo.

A página não guarda estado: ela pergunta `/run/<id>/status` de segundo em
segundo e redesenha. Escolha deliberada sobre SSE ou WebSocket — é um servidor
local, um usuário e uma rodada por vez; polling não tem o que reconectar.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

import service
from service import InvalidInput, Options

from .jobs import RunInProgress

bp = Blueprint("ytdl", __name__)

#: As resoluções que a tela oferece. Mais que isto é lista de especialista;
#: menos, e some o caso real de segurar o tamanho de um vídeo longo.
HEIGHTS = [
    ("", "A melhor disponível"),
    ("2160", "até 4K (2160p)"),
    ("1440", "até 1440p"),
    ("1080", "até 1080p"),
    ("720", "até 720p"),
    ("480", "até 480p"),
]

#: Qualidade do MP3. O 0 é o VBR mais alto do LAME e é o padrão do programa;
#: os bitrates fixos existem para quem precisa de tamanho previsível.
QUALITIES = [
    ("0", "A melhor (VBR)"),
    ("320", "320 kbps"),
    ("256", "256 kbps"),
    ("192", "192 kbps"),
    ("128", "128 kbps"),
]

FORMATS = [
    ("mp4", "Vídeo (MP4)"),
    ("mp3", "Só o áudio (MP3)"),
    ("both", "Os dois"),
]

#: O estado da rodada em português. Os identificadores vivem em `jobs`, em
#: inglês; a tradução para quem lê a tela fica aqui, que é a camada que fala
#: com o usuário — e é o único lugar a mexer se o texto mudar.
STATE_LABELS = {
    "queued": "na fila",
    "downloading": "baixando",
    "done": "concluída",
    "cancelled": "cancelada",
    "failed": "erro",
}

#: Quem fez e onde o código mora. Aqui, e não escrito à mão em `base.html`,
#: porque o endereço do repositório é a primeira coisa a mudar se o projeto
#: for renomeado — e ninguém vai lembrar de procurá-lo dentro de um template.
AUTHOR = "Leonardo Garcia Marques"
REPO_URL = "https://github.com/leonardomqs/youtube-downloader"


@bp.app_context_processor
def _credits() -> dict:
    """O crédito do rodapé, disponível em toda página sem cada rota repassá-lo."""
    return {"author": AUTHOR, "repo_url": REPO_URL}


# ----------------------------------------------------------------- a tela


@bp.get("/")
def index():
    cfg = current_app.config["SETTINGS"]
    active = current_app.config["RUNS"].active()
    return render_template(
        "index.html",
        links=cfg.suggested_links(),
        output=str(cfg.suggested_output_dir),
        formats=FORMATS,
        heights=HEIGHTS,
        qualities=QUALITIES,
        browsers=service.BROWSERS,
        active=active,
        environment=_environment(),
    )


@bp.post("/download")
def start_download():
    registry = current_app.config["RUNS"]

    try:
        links, options = _from_form(request.form)
    except InvalidInput as error:
        flash(str(error), "error")
        return redirect(url_for("ytdl.index"))

    try:
        run = registry.open(links, options)
    except RunInProgress as error:
        flash("Já existe uma rodada em andamento — espere ou interrompa.", "error")
        return redirect(url_for("ytdl.run", ident=error.run.id))

    return redirect(url_for("ytdl.run", ident=run.id))


@bp.get("/run/<ident>")
def run(ident: str):
    r = current_app.config["RUNS"].get(ident)
    if r is None:
        flash("Essa rodada não existe mais.", "error")
        return redirect(url_for("ytdl.index"))
    return render_template("run.html", run=r, status=_status(r))


@bp.get("/run/<ident>/status")
def status(ident: str):
    """O andamento, em JSON. É o que a página pergunta de segundo em segundo."""
    r = current_app.config["RUNS"].get(ident)
    if r is None:
        return jsonify({"error": "rodada desconhecida"}), 404
    return jsonify(_status(r))


@bp.post("/run/<ident>/stop")
def stop(ident: str):
    r = current_app.config["RUNS"].get(ident)
    if r is None:
        return jsonify({"error": "rodada desconhecida"}), 404
    r.cancel()
    return jsonify(_status(r))


@bp.post("/run/<ident>/open/<target>")
def open_folder(ident: str, target: str):
    """Abre a pasta de destino no explorador de arquivos.

    Faz sentido porque este servidor roda na máquina do próprio usuário: a
    pasta do servidor e a dele são a mesma. Só abre pastas que saíram das
    opções da rodada — o caminho nunca vem do pedido.
    """
    r = current_app.config["RUNS"].get(ident)
    if r is None or target not in r.options.targets:
        flash("Essa rodada não existe mais.", "error")
        return redirect(url_for("ytdl.index"))

    folder = r.options.output / target
    if not folder.is_dir():
        flash(f"A pasta {folder} ainda não existe.", "error")
    else:
        _open_in_system(folder)

    return redirect(url_for("ytdl.run", ident=ident))


@bp.get("/runs")
def runs():
    registry = current_app.config["RUNS"]
    return render_template("runs.html", runs=[_short_summary(r) for r in registry.all()])


# ------------------------------------------------------------ o formulário


def _from_form(form) -> tuple[list[str], Options]:
    """Traduz o formulário em links e opções, validando com as regras do serviço.

    Cada campo passa pelo mesmo validador que a linha de comando usa, então a
    mensagem de erro é a mesma nos dois lugares — e existe em um lugar só.
    """
    links = service.read_links_text(form.get("links", ""))

    chosen = form.get("format", "mp4")
    if chosen not in service.FORMATS:
        raise InvalidInput(f"formato desconhecido: {chosen}")

    return links, Options(
        format=chosen,
        output=_output_folder(form.get("output", "")),
        max_height=_optional(form.get("max_height"), service.positive_int),
        audio_quality=service.audio_quality(form.get("audio_quality") or "0"),
        playlist=_checked(form, "playlist"),
        playlist_items=(form.get("playlist_items") or "").strip() or None,
        playlist_reverse=_checked(form, "playlist_reverse"),
        playlist_folder=_checked(form, "playlist_folder"),
        live_from_start=_checked(form, "live_from_start"),
        archive=Path(form["archive"].strip()) if (form.get("archive") or "").strip() else None,
        dry_run=form.get("action") == "simulate",
        concurrent_fragments=service.positive_int(form.get("concurrent_fragments") or 4),
        limit_rate=_optional(form.get("limit_rate"), service.rate_limit),
        cookies_from_browser=_optional(form.get("cookies"), service.browser_cookies),
    )


def _checked(form, name: str) -> bool:
    return bool(form.get(name))


def _optional(value, check):
    """Campo em branco quer dizer "não use isto" — só o preenchido é validado."""
    text = (value or "").strip()
    return check(text) if text else None


def _output_folder(text: str) -> Path:
    """A pasta escolhida, conferida antes de a rodada começar.

    Um erro de digitação aqui só apareceria meia hora depois, com os arquivos
    numa pasta que ninguém procura. A pasta ainda não precisa existir — a
    rodada a cria —, mas o caminho até ela, sim.
    """
    text = (text or "").strip().strip('"')
    if not text:
        raise InvalidInput("Escolha a pasta de destino.")

    folder = Path(text).expanduser()
    if folder.is_dir() or folder.parent.is_dir():
        return folder
    raise InvalidInput(f"A pasta '{folder.parent}' não existe. Confira o caminho.")


# ------------------------------------------------- o que a página desenha


def _status(r) -> dict:
    """O instantâneo da rodada, já em texto de tela.

    A página não recebe objetos do serviço: recebe número redondo e texto
    pronto. Assim o JavaScript não precisa saber o que é um `Progress`, e
    mudar a aparência não esbarra em regra nenhuma.
    """
    s = r.snapshot()
    now = s["current"]
    options = s["options"]
    # A última fase descreve um trabalho que já acabou; quem diz como terminou é o
    # estado da rodada.
    phase = "" if s["is_over"] else now.phase

    return {
        "id": s["id"],
        "state": s["state"],
        "state_label": STATE_LABELS.get(s["state"], s["state"]),
        "is_over": s["is_over"],
        "simulation": s["dry_run"],
        "phase": phase,
        "title": now.title,
        "target": now.target.upper(),
        "percent": round(now.percent, 1),
        "recording": phase == service.RECORDING,
        "speed": _speed(now.speed),
        "eta": _duration(now.eta),
        "current_link": now.current_link,
        "total_links": now.total_links,
        "items": [
            {"position": i.position, "title": i.title, "target": i.target.upper()}
            for i in s["items"]
        ],
        "failures": [{"link": f.link, "reason": f.reason} for f in s["failures"]],
        "summary": [
            {
                "target": target,
                "label": target.upper(),
                "downloaded": result.downloaded,
                "skipped": result.skipped,
                "failures": len(result.failures),
                "folder": str(result.folder),
                "verdict": _verdict(result, s["dry_run"]),
            }
            for target, result in s["results"].items()
        ],
        "lines": s["lines"][-40:],
        "error": s["error"],
        "destination": str(options.output),
        "targets": list(options.targets),
    }


def _short_summary(r) -> dict:
    """Uma linha do histórico da sessão."""
    s = r.snapshot()
    return {
        "id": s["id"],
        "state": s["state"],
        "state_label": STATE_LABELS.get(s["state"], s["state"]),
        "time": s["created_at"].strftime("%H:%M"),
        "links": len(s["links"]),
        "format": s["options"].format.upper(),
        "simulation": s["dry_run"],
        "downloaded": sum(r.downloaded for r in s["results"].values()),
        "failures": sum(len(r.failures) for r in s["results"].values()),
        "destination": str(s["options"].output),
    }


def _verdict(result: service.Result, simulation: bool) -> str:
    parts = [f"{result.downloaded} vídeo(s)" + (" a baixar" if simulation else "")]
    if result.skipped:
        parts.append(f"{result.skipped} já no histórico")
    if result.failures:
        parts.append(f"{len(result.failures)} falha(s)")
    return ", ".join(parts)


def _speed(bytes_per_second: float) -> str:
    """Em português: vírgula decimal e a unidade que couber."""
    if not bytes_per_second:
        return ""
    if bytes_per_second >= 1024 * 1024:
        return f"{bytes_per_second / (1024 * 1024):.1f} MB/s".replace(".", ",")
    return f"{bytes_per_second / 1024:.0f} KB/s"


def _duration(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _environment() -> dict:
    """O que falta na máquina, para a tela avisar antes e não no meio.

    São os mesmos dois avisos que o terminal dá em stderr — onde, na prática,
    ninguém lê.
    """
    return {
        "ffmpeg": service.ffmpeg_available(),
        "deno": service.deno_available(),
    }


def _open_in_system(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606 - caminho vem das opções, não do pedido
    elif sys.platform == "darwin":
        subprocess.run(["open", str(folder)], check=False)
    else:
        subprocess.run(["xdg-open", str(folder)], check=False)
