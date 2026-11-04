"""A interface de navegador, com o download substituído.

O executor é injetado na fábrica da aplicação, então a tela inteira — fila,
andamento, interrupção, resumo — é exercitada sem baixar um byte. É também o
que garante que a página não saiba baixar nada sozinha: se soubesse, estes
testes não passariam.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import service
from webui import WebConfig, create_app


def wait_for(condition, timeout=3.0):
    """Espera a thread da rodada chegar onde o teste precisa."""
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if condition():
            return True
        time.sleep(0.01)
    return False


def build(tmp_path: Path, executor=None):
    cfg = WebConfig(
        suggested_output_dir=tmp_path / "downloads",
        links_file=None,
        session_key="teste",
    )
    app = create_app(cfg, executor)
    app.config["TESTING"] = True
    return app


def simple_executor(links, options, observer):
    """Uma rodada que "baixa" um vídeo por link e conta isso ao observador."""
    observer.link_started(1, len(links), links[0], options.targets[0])
    observer.item(service.Item(id="a", title="Me at the zoo", target=options.targets[0]))
    observer.log("[youtube] a: Downloading webpage")
    return {
        target: service.Result(target=target, folder=options.output / target, downloaded=len(links))
        for target in options.targets
    }


@pytest.fixture
def app(tmp_path):
    return build(tmp_path, simple_executor)


# ------------------------------------------------------------------ a tela


def test_the_home_page_opens(app):
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert "Baixar do YouTube" in response.get_data(as_text=True)


def test_the_home_page_brings_the_links_from_the_file(tmp_path: Path):
    listing = tmp_path / "links.txt"
    listing.write_text("https://youtu.be/guardado\n", encoding="utf-8")
    cfg = WebConfig(suggested_output_dir=tmp_path, links_file=listing)
    app = create_app(cfg, simple_executor)

    body = app.test_client().get("/").get_data(as_text=True)

    assert "https://youtu.be/guardado" in body


def test_without_links_the_screen_complains_and_opens_no_run(app):
    client = app.test_client()

    response = client.post("/download", data={"links": "  ", "output": "x"}, follow_redirects=True)

    assert "Nenhum link encontrado" in response.get_data(as_text=True)
    assert app.config["RUNS"].all() == []


def test_a_missing_folder_is_refused_before_starting(app, tmp_path: Path):
    client = app.test_client()

    response = client.post(
        "/download",
        data={"links": "https://youtu.be/a", "output": str(tmp_path / "nao" / "existe")},
        follow_redirects=True,
    )

    assert "não existe" in response.get_data(as_text=True)
    assert app.config["RUNS"].all() == []


# --------------------------------------------------------------- a rodada


def test_downloading_opens_a_run_and_goes_to_it(app, tmp_path: Path):
    client = app.test_client()

    response = client.post(
        "/download",
        data={"links": "https://youtu.be/a", "output": str(tmp_path), "format": "mp4"},
    )

    assert response.status_code == 302
    run = app.config["RUNS"].all()[0]
    assert response.headers["Location"].endswith(f"/run/{run.id}")
    assert wait_for(lambda: run.is_over)


def test_the_status_tells_what_the_screen_draws(app, tmp_path: Path):
    client = app.test_client()
    client.post("/download", data={"links": "https://youtu.be/a", "output": str(tmp_path)})
    run = app.config["RUNS"].all()[0]
    assert wait_for(lambda: run.is_over)

    status = client.get(f"/run/{run.id}/status").get_json()

    assert status["state"] == "done"
    assert status["state_label"] == "concluída"
    assert status["is_over"] is True
    assert status["items"] == [{"position": "", "title": "Me at the zoo", "target": "MP4"}]
    assert status["summary"][0]["verdict"] == "1 vídeo(s)"
    assert "[youtube] a: Downloading webpage" in status["lines"]


def test_the_run_page_draws_without_javascript(app, tmp_path: Path):
    client = app.test_client()
    client.post("/download", data={"links": "https://youtu.be/a", "output": str(tmp_path)})
    run = app.config["RUNS"].all()[0]
    assert wait_for(lambda: run.is_over)

    body = client.get(f"/run/{run.id}").get_data(as_text=True)

    assert "Me at the zoo" in body
    assert str(tmp_path) in body


def test_simulating_does_not_download_and_the_screen_says_so(app, tmp_path: Path):
    client = app.test_client()

    client.post(
        "/download",
        data={"links": "https://youtu.be/a", "output": str(tmp_path), "action": "simulate"},
    )
    run = app.config["RUNS"].all()[0]

    assert run.options.dry_run is True
    assert wait_for(lambda: run.is_over)
    assert client.get(f"/run/{run.id}/status").get_json()["simulation"] is True


def test_an_unknown_run_goes_back_to_the_start(app):
    response = app.test_client().get("/run/nao-existe", follow_redirects=True)

    assert "Essa rodada não existe mais." in response.get_data(as_text=True)


def test_the_status_of_an_unknown_run_is_404(app):
    assert app.test_client().get("/run/nao-existe/status").status_code == 404


# ------------------------------------------------ uma de cada vez, e parar


def test_one_run_at_a_time(tmp_path: Path):
    """A segunda tentativa não abre fila: leva para a rodada que está rodando."""
    release = threading.Event()

    def slow_executor(links, options, observer):
        release.wait(3)
        return {"mp4": service.Result(target="mp4", folder=options.output / "mp4")}

    app = build(tmp_path, slow_executor)
    client = app.test_client()
    data = {"links": "https://youtu.be/a", "output": str(tmp_path)}

    client.post("/download", data=data)
    first = app.config["RUNS"].all()[0]
    assert wait_for(lambda: first.state == "downloading")

    second = client.post("/download", data=data, follow_redirects=True)

    assert "Já existe uma rodada em andamento" in second.get_data(as_text=True)
    assert len(app.config["RUNS"].all()) == 1
    release.set()
    assert wait_for(lambda: first.is_over)


def test_stopping_interrupts_the_run(tmp_path: Path):
    """O executor pergunta ao observador se deve parar — como o serviço faz."""

    def obedient_executor(links, options, observer):
        for _ in range(300):
            if observer.should_stop():
                return {
                    "mp4": service.Result(
                        target="mp4", folder=options.output / "mp4", cancelled=True
                    )
                }
            time.sleep(0.01)
        return {"mp4": service.Result(target="mp4", folder=options.output / "mp4")}

    app = build(tmp_path, obedient_executor)
    client = app.test_client()
    client.post("/download", data={"links": "https://youtu.be/a", "output": str(tmp_path)})
    run = app.config["RUNS"].all()[0]
    assert wait_for(lambda: run.state == "downloading")

    status = client.post(f"/run/{run.id}/stop").get_json()

    assert status["id"] == run.id
    assert wait_for(lambda: run.state == "cancelled")


def test_an_error_in_the_thread_does_not_vanish(tmp_path: Path):
    def broken_executor(links, options, observer):
        raise RuntimeError("o ffmpeg sumiu")

    app = build(tmp_path, broken_executor)
    client = app.test_client()
    client.post("/download", data={"links": "https://youtu.be/a", "output": str(tmp_path)})
    run = app.config["RUNS"].all()[0]
    assert wait_for(lambda: run.is_over)

    status = client.get(f"/run/{run.id}/status").get_json()

    assert status["state"] == "failed"
    assert "o ffmpeg sumiu" in status["error"]


# -------------------------------------------------------------- histórico


def test_the_history_lists_the_runs_of_the_session(app, tmp_path: Path):
    client = app.test_client()
    client.post("/download", data={"links": "https://youtu.be/a", "output": str(tmp_path)})
    assert wait_for(lambda: app.config["RUNS"].all()[0].is_over)

    body = client.get("/runs").get_data(as_text=True)

    assert "Rodadas desta sessão" in body
    assert str(tmp_path) in body


def test_an_empty_history_does_not_lie(app):
    assert "Nada ainda." in app.test_client().get("/runs").get_data(as_text=True)


# ----------------------------------------------------------------- abrir


def test_opening_a_folder_only_accepts_a_target_of_the_run(app, tmp_path: Path, monkeypatch):
    opened = []
    monkeypatch.setattr("webui.routes._open_in_system", opened.append)

    client = app.test_client()
    client.post(
        "/download",
        data={"links": "https://youtu.be/a", "output": str(tmp_path), "format": "mp4"},
    )
    run = app.config["RUNS"].all()[0]
    assert wait_for(lambda: run.is_over)
    (tmp_path / "mp4").mkdir(exist_ok=True)

    client.post(f"/run/{run.id}/open/mp4")
    assert opened == [tmp_path / "mp4"]

    # mp3 não faz parte desta rodada: a rota não abre pasta que não é dela.
    client.post(f"/run/{run.id}/open/mp3")
    assert opened == [tmp_path / "mp4"]


def test_the_form_turns_on_live_from_start(app, tmp_path: Path):
    client = app.test_client()
    form = {"links": "https://youtu.be/a", "output": str(tmp_path), "action": "download"}

    client.post("/download", data={**form, "live_from_start": "on"})
    registry = app.config["RUNS"]
    run = registry.all()[0]
    assert wait_for(lambda: run.is_over)
    assert run.options.live_from_start

    client.post("/download", data=form)
    run = registry.all()[0]
    assert wait_for(lambda: run.is_over)
    assert not run.options.live_from_start


def test_the_status_says_when_it_is_recording(app, tmp_path: Path):
    from webui import routes

    run = app.config["RUNS"].open(["https://youtu.be/a"], service.Options(output=tmp_path))
    assert wait_for(lambda: run.is_over)

    assert routes._status(run)["recording"] is False
    run.state = "downloading"
    run.current.phase = service.RECORDING
    assert routes._status(run)["recording"] is True


def test_a_finished_run_shows_no_phase(app, tmp_path: Path):
    from webui import routes

    run = app.config["RUNS"].open(["https://youtu.be/a"], service.Options(output=tmp_path))
    assert wait_for(lambda: run.is_over)
    run.current.phase = "guardando na pasta"

    assert routes._status(run)["phase"] == ""
