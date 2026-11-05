"""A interface de terminal: argumentos, tradução para opções e resumo."""

from __future__ import annotations

from pathlib import Path

import pytest

import cli
import service


def test_parse_args_turns_on_playlist_when_items_are_selected():
    assert cli.options_from(cli.parse_args(["links.txt", "--playlist-items", "1-10"])).playlist
    assert cli.options_from(cli.parse_args(["links.txt", "--playlist-reverse"])).playlist
    assert not cli.options_from(cli.parse_args(["links.txt"])).playlist


def test_parse_args_refuses_an_invalid_height(capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(["links.txt", "--max-height", "0"])

    assert "maior que zero" in capsys.readouterr().err


def test_parse_args_refuses_an_unknown_browser(capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(["links.txt", "--cookies-from-browser", "explorer"])

    assert "não suportado" in capsys.readouterr().err


def test_option_names_match_the_service_field_names():
    args = cli.parse_args(
        [
            "links.txt",
            "--format",
            "both",
            "-o",
            "saida",
            "--max-height",
            "720",
            "-j",
            "8",
            "--limit-rate",
            "2M",
            "--archive",
            "hist.txt",
            "--dry-run",
        ]
    )

    options = cli.options_from(args)

    assert options.format == "both"
    assert options.output == Path("saida")
    assert options.max_height == 720
    assert options.concurrent_fragments == 8
    assert options.limit_rate == 2 * 1024 * 1024
    assert options.archive == Path("hist.txt")
    assert options.dry_run is True
    assert options.targets == ("mp4", "mp3")


def test_main_complains_about_an_entry_that_does_not_exist(tmp_path: Path):
    with pytest.raises(SystemExit, match="Não é um link nem um arquivo"):
        cli.main([str(tmp_path / "nao_existe.txt")])


def test_main_runs_and_summarizes(monkeypatch, capsys, tmp_path: Path):
    """A rodada inteira pelo terminal, com o serviço substituído."""
    calls = {}

    def fake_run(links, options, observer):
        calls["links"] = links
        observer.link_started(1, 1, links[0], "mp4")
        observer.link_finished(links[0], 3, 1, 0, "Surf Music")
        return {
            "mp4": service.Result(
                target="mp4", folder=options.output / "mp4", downloaded=3, skipped=1
            )
        }

    monkeypatch.setattr(service, "run", fake_run)

    code = cli.main(["https://youtu.be/a", "-o", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert calls["links"] == ["https://youtu.be/a"]
    assert "[MP4 1/1] https://youtu.be/a" in out
    assert 'playlist "Surf Music": 3 vídeo(s), 1 já no histórico' in out
    assert "MP4: 3 vídeo(s), 1 já no histórico" in out
    assert "Tudo pronto." in out


def test_main_exits_with_error_when_something_failed(monkeypatch, capsys, tmp_path: Path):
    def fake_run(links, options, observer):
        return {
            "mp4": service.Result(
                target="mp4",
                folder=options.output / "mp4",
                failures=[service.Failure("https://youtu.be/a", "indisponível")],
            )
        }

    monkeypatch.setattr(service, "run", fake_run)

    code = cli.main(["https://youtu.be/a", "-o", str(tmp_path)])

    assert code == 1
    assert "indisponível" in capsys.readouterr().out


def test_summary_of_an_interrupted_run(tmp_path: Path, capsys):
    results = {"mp4": service.Result(target="mp4", folder=tmp_path, downloaded=2, cancelled=True)}

    code = cli.print_summary(results, service.Options(output=tmp_path))

    assert code == 130
    assert "Interrompido." in capsys.readouterr().out


def test_summary_of_a_simulation_speaks_in_the_future(tmp_path: Path, capsys):
    results = {"mp4": service.Result(target="mp4", folder=tmp_path, downloaded=4)}

    cli.print_summary(results, service.Options(output=tmp_path, dry_run=True))

    assert "4 vídeo(s) a baixar" in capsys.readouterr().out


def test_live_from_start_reaches_the_options():
    assert cli.options_from(cli.parse_args(["links.txt", "--live-from-start"])).live_from_start
    assert not cli.options_from(cli.parse_args(["links.txt"])).live_from_start


def test_the_first_ctrl_c_finishes_the_recording_and_the_second_leaves(capsys):
    terminal = cli.Terminal()
    assert not terminal.should_stop()

    terminal.finish_recording()

    assert terminal.should_stop()
    assert "guardando" in capsys.readouterr().err
    with pytest.raises(KeyboardInterrupt):
        terminal.finish_recording()


def test_wait_for_live_reaches_the_options():
    assert cli.options_from(cli.parse_args(["links.txt", "--wait-for-live"])).wait_for_live
    assert not cli.options_from(cli.parse_args(["links.txt"])).wait_for_live


def test_the_terminal_announces_each_wait_once(capsys):
    terminal = cli.Terminal()
    waiting = service.Progress(phase=service.WAITING, title="Agendada")

    terminal.progress(waiting)
    terminal.progress(waiting)

    assert capsys.readouterr().out.count("Agendada") == 1
