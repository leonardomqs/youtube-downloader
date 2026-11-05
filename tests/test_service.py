"""A rodada, sem interface e sem rede.

Estes testes existem por cima do `service`, que é onde mora o que pode dar
errado de verdade: a cadeia de formatos, o histórico por formato e a diferença
entre "pulado" e "falhou".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from yt_dlp import YoutubeDL

import service
from service import InvalidInput, Options

# ---------------------------------------------------------------- read_links


def test_read_links_ignores_blanks_comments_and_repeats(tmp_path: Path):
    file = tmp_path / "links.txt"
    file.write_text(
        "﻿# comentario\n"
        "\n"
        "https://youtu.be/a\n"
        "   https://youtu.be/b   \n"
        "https://youtu.be/a\n"
        "# https://youtu.be/c\n",
        encoding="utf-8",
    )

    assert service.read_links([str(file)]) == ["https://youtu.be/a", "https://youtu.be/b"]


def test_read_links_accepts_bare_links_mixed_with_a_file(tmp_path: Path):
    file = tmp_path / "links.txt"
    file.write_text("https://youtu.be/a\n", encoding="utf-8")

    links = service.read_links(["https://youtu.be/z", str(file), "https://youtu.be/z"])

    assert links == ["https://youtu.be/z", "https://youtu.be/a"]


def test_read_links_complains_about_what_is_neither_link_nor_file(tmp_path: Path):
    with pytest.raises(InvalidInput, match="Não é um link nem um arquivo"):
        service.read_links([str(tmp_path / "nao_existe.txt")])


def test_read_links_complains_about_a_file_without_links(tmp_path: Path):
    file = tmp_path / "vazio.txt"
    file.write_text("# so comentario\n\n", encoding="utf-8")

    with pytest.raises(InvalidInput, match="Nenhum link encontrado"):
        service.read_links([str(file)])


def test_read_links_text_is_what_the_screen_sends():
    text = "# a fila\nhttps://youtu.be/a\n\nhttps://youtu.be/b\n"

    assert service.read_links_text(text) == ["https://youtu.be/a", "https://youtu.be/b"]


# ------------------------------------------------------------ output_template


def test_output_template_groups_playlists_in_a_subfolder():
    template = service.output_template(Path("saida") / "mp4", with_subfolder=True)

    assert template == (
        "saida/mp4/%(playlist_title|)s/%(playlist_index&{:03d} - |)s%(title)s [%(id)s].%(ext)s"
    )


def test_output_template_without_subfolder_does_not_group():
    template = service.output_template(Path("saida"), with_subfolder=False)

    assert "%(playlist_title" not in template
    assert template.startswith("saida/")


# ------------------------------------------------------------- archive_path


@pytest.mark.parametrize(
    ("base", "target", "expected"),
    [
        ("baixados.txt", "mp4", "baixados.mp4.txt"),
        ("baixados.txt", "mp3", "baixados.mp3.txt"),
        ("baixados", "mp4", "baixados.mp4.txt"),
    ],
)
def test_archive_path_splits_by_format(base: str, target: str, expected: str):
    assert service.archive_path(Path(base), target).name == expected


def test_mp4_and_mp3_archives_do_not_mix():
    # Regressão: com um histórico único, a passada de MP3 do formato "both"
    # pulava tudo o que a de MP4 acabara de registrar.
    options = Options(format="both", archive=Path("hist.txt"))

    mp4 = service.ydl_options(options, "mp4")["download_archive"]
    mp3 = service.ydl_options(options, "mp3")["download_archive"]

    assert mp4 != mp3


# ------------------------------------------------------------- count_result


def test_count_result_of_a_single_video():
    assert service.count_result({"id": "a"}) == (1, 0)


def test_count_result_of_a_link_that_failed():
    assert service.count_result(None) == (0, 1)


def test_count_result_of_a_nested_playlist_with_failures():
    channel = {
        "entries": [
            {"entries": [{"id": "a"}, None, {"id": "b"}]},
            {"id": "c"},
            None,
        ]
    }

    assert service.count_result(channel) == (3, 2)


# ----------------------------------------------------------------- download


class FakeDownloader:
    """Substitui o Downloader nos testes: devolve resultados prontos, sem rede."""

    def __init__(self, results, skipped_per_link=None, errors_per_link=None):
        self._results = list(results)
        self._skipped = list(skipped_per_link or [0] * len(self._results))
        self._errors = list(errors_per_link or [[]] * len(self._results))
        self.errors: list[str] = []
        self.skipped = 0
        self.opts = None
        self._i = -1

    def __call__(self, opts):  # usado no lugar da classe
        self.opts = opts
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, link, download=True):
        self._i += 1
        self.skipped = self._skipped[self._i]
        self.errors = list(self._errors[self._i])
        result = self._results[self._i]
        if isinstance(result, Exception):
            raise result
        return result


def test_download_counts_archive_skips_instead_of_failures(monkeypatch):
    # Regressão: itens já no histórico voltam como None, igual aos que falharam;
    # sem descontá-los, rodar de novo com histórico acusava tudo como falha.
    playlist = {"title": "lista", "entries": [{"id": "a"}, None, None]}
    monkeypatch.setattr(service, "Downloader", FakeDownloader([playlist], skipped_per_link=[2]))

    r = service.download(["https://youtu.be/lista"], Options(), "mp4")

    assert (r.downloaded, r.skipped, r.failures) == (1, 2, [])


def test_download_still_reports_real_failures(monkeypatch):
    playlist = {"title": "lista", "entries": [{"id": "a"}, None, None]}
    monkeypatch.setattr(
        service,
        "Downloader",
        FakeDownloader([playlist], skipped_per_link=[1], errors_per_link=[["video removido"]]),
    )

    r = service.download(["https://youtu.be/lista"], Options(), "mp4")

    assert (r.downloaded, r.skipped) == (1, 1)
    assert [(f.link, f.reason) for f in r.failures] == [
        ("https://youtu.be/lista", "video removido")
    ]


def test_one_bad_link_does_not_bring_the_queue_down(monkeypatch):
    monkeypatch.setattr(service, "Downloader", FakeDownloader([RuntimeError("boom"), {"id": "b"}]))

    r = service.download(["https://youtu.be/a", "https://youtu.be/b"], Options(), "mp4")

    assert (r.downloaded, r.skipped) == (1, 0)
    assert [f.reason for f in r.failures] == ["RuntimeError: boom"]


def test_download_records_a_failure_when_nothing_came(monkeypatch):
    monkeypatch.setattr(service, "Downloader", FakeDownloader([None]))

    r = service.download(["https://youtu.be/a"], Options(), "mp4")

    assert (r.downloaded, r.skipped) == (0, 0)
    assert [f.reason for f in r.failures] == ["motivo desconhecido"]


def test_download_tells_the_observer_about_start_and_finish(monkeypatch):
    playlist = {"title": "surf", "entries": [{"id": "a"}, {"id": "b"}]}
    monkeypatch.setattr(service, "Downloader", FakeDownloader([playlist]))

    seen = []

    class Spy(service.Observer):
        def link_started(self, index, total, url, target):
            seen.append(("start", index, total, target))

        def link_finished(self, link, downloaded, skipped, failed, playlist):
            seen.append(("finish", downloaded, playlist))

    service.download(["https://youtu.be/lista"], Options(), "mp4", Spy())

    assert seen == [("start", 1, 1, "mp4"), ("finish", 2, "surf")]


def test_run_makes_one_pass_per_format(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(service, "Downloader", FakeDownloader([{"id": "a"}, {"id": "a"}]))

    results = service.run(["https://youtu.be/a"], Options(format="both", output=tmp_path / "saida"))

    assert sorted(results) == ["mp3", "mp4"]
    assert (tmp_path / "saida" / "mp4").is_dir()
    assert (tmp_path / "saida" / "mp3").is_dir()


def test_a_simulation_creates_no_folder(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(service, "Downloader", FakeDownloader([{"id": "a"}]))

    service.run(["https://youtu.be/a"], Options(output=tmp_path / "saida", dry_run=True))

    assert not (tmp_path / "saida").exists()


def test_a_simulation_lists_the_videos_it_found(monkeypatch):
    # Sem download não há gancho de progresso: em simulação os itens têm de vir
    # do resultado do próprio link, ou a tela do "o que será baixado" fica vazia.
    playlist = {
        "title": "surf",
        "entries": [
            {"id": "a", "title": "Beautiful Day", "playlist_index": 1},
            {"id": "b", "title": "La Mar", "playlist_index": 2},
        ],
    }
    monkeypatch.setattr(service, "Downloader", FakeDownloader([playlist]))

    r = service.download(["https://youtu.be/lista"], Options(dry_run=True), "mp4")

    assert [(i.position, i.title) for i in r.items] == [
        ("001", "Beautiful Day"),
        ("002", "La Mar"),
    ]


def test_a_simulation_of_a_single_video_also_lists_it(monkeypatch):
    monkeypatch.setattr(
        service, "Downloader", FakeDownloader([{"id": "a", "title": "Me at the zoo"}])
    )

    r = service.download(["https://youtu.be/a"], Options(dry_run=True), "mp4")

    assert [i.title for i in r.items] == ["Me at the zoo"]


def test_a_real_download_does_not_use_the_simulation_path(monkeypatch):
    # Fora da simulação os itens vêm dos ganchos, um a um, enquanto baixa —
    # anunciá-los de novo no fim do link duplicaria a lista da tela.
    monkeypatch.setattr(
        service, "Downloader", FakeDownloader([{"id": "a", "title": "Me at the zoo"}])
    )

    r = service.download(["https://youtu.be/a"], Options(), "mp4")

    assert r.items == []


# ------------------------------------------------------ Downloader (archive)


def test_downloader_counts_a_positive_archive_lookup(tmp_path: Path):
    history = tmp_path / "hist.txt"
    history.write_text("youtube ja_baixado\n", encoding="utf-8")

    with service.Downloader({"download_archive": str(history), "quiet": True}) as ydl:
        assert ydl.in_download_archive({"id": "ja_baixado", "ie_key": "Youtube"}) is True
        assert ydl.in_download_archive({"id": "novo", "ie_key": "Youtube"}) is False

    assert ydl.skipped == 1


# ------------------------------------------------------------------- opções


def test_mp4_options_apply_the_height_limit_to_every_branch():
    chain = service.ydl_options(Options(max_height=1080), "mp4")["format"]

    assert chain.count("[height<=1080]") == 3
    assert chain.endswith("/best")


def test_mp3_options_embed_cover_and_metadata():
    opts = service.ydl_options(Options(audio_quality="320"), "mp3")
    keys = [pp["key"] for pp in opts["postprocessors"]]

    assert opts["writethumbnail"] is True
    assert keys == ["FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail"]
    assert opts["postprocessors"][0]["preferredquality"] == "320"


def test_dry_run_simulates_and_does_not_download():
    opts = service.ydl_options(Options(dry_run=True), "mp4")

    assert opts["simulate"] is True
    assert "forceprint" in opts


def test_without_dry_run_there_is_no_simulation():
    assert "simulate" not in service.ydl_options(Options(), "mp4")


def test_fragments_are_parallel_by_default():
    assert service.ydl_options(Options(), "mp4")["concurrent_fragment_downloads"] == 4


def test_hooks_only_exist_when_someone_is_watching():
    without = service.ydl_options(Options(), "mp4")
    watched = service.ydl_options(Options(), "mp4", service.Observer())

    assert "progress_hooks" not in without
    assert "logger" not in without
    assert watched["progress_hooks"] and watched["postprocessor_hooks"]
    assert watched["noprogress"] is True


def test_the_download_hook_translates_the_progress():
    seen = []

    class Spy(service.Observer):
        def progress(self, p):
            seen.append((p.phase, round(p.percent), p.speed, p.eta))

    opts = service.ydl_options(Options(), "mp4", Spy())
    opts["progress_hooks"][0](
        {
            "status": "downloading",
            "downloaded_bytes": 50,
            "total_bytes": 200,
            "speed": 1024.0,
            "eta": 12,
            "info_dict": {"id": "a", "title": "Me at the zoo"},
        }
    )

    assert seen == [("baixando", 25, 1024.0, 12)]


def test_the_hook_announces_the_item_when_the_file_is_done():
    items = []

    class Spy(service.Observer):
        def item(self, i):
            items.append((i.position, i.title))

    opts = service.ydl_options(Options(), "mp4", Spy())
    opts["progress_hooks"][0](
        {"status": "finished", "info_dict": {"id": "a", "title": "La Mar", "playlist_index": 7}}
    )

    assert items == [("007", "La Mar")]


def test_a_video_with_separate_audio_is_listed_once():
    # Em MP4 o yt-dlp baixa dois arquivos por vídeo e anuncia "finished" duas
    # vezes; na tela isso virava o mesmo vídeo listado em duplicata.
    items = []

    class Spy(service.Observer):
        def item(self, i):
            items.append(i.title)

    opts = service.ydl_options(Options(), "mp4", Spy())
    for _ in range(2):
        opts["progress_hooks"][0](
            {"status": "finished", "info_dict": {"id": "a", "title": "Me at the zoo"}}
        )
    opts["progress_hooks"][0]({"status": "finished", "info_dict": {"id": "b", "title": "La Mar"}})

    assert items == ["Me at the zoo", "La Mar"]


def test_the_postprocessing_hook_explains_the_wait():
    phases = []

    class Spy(service.Observer):
        def progress(self, p):
            phases.append(p.phase)

    opts = service.ydl_options(Options(), "mp3", Spy())
    opts["postprocessor_hooks"][0](
        {"status": "started", "postprocessor": "FFmpegExtractAudio", "info_dict": {}}
    )

    assert phases == ["convertendo para MP3"]


def test_stopping_interrupts_at_the_next_chunk():
    class Impatient(service.Observer):
        def should_stop(self):
            return True

    opts = service.ydl_options(Options(), "mp4", Impatient())

    with pytest.raises(service.DownloadCancelled):
        opts["progress_hooks"][0]({"status": "downloading", "info_dict": {}})


def test_a_cancelled_run_does_not_move_on_to_the_next_format(monkeypatch):
    class CancellingDownloader(FakeDownloader):
        def extract_info(self, link, download=True):
            raise service.DownloadCancelled("parou")

    monkeypatch.setattr(service, "Downloader", CancellingDownloader([None, None]))

    results = service.run(["https://youtu.be/a"], Options(format="both", dry_run=True))

    assert list(results) == ["mp4"]
    assert results["mp4"].cancelled


# -------------------------------------------------------------------- lives


class Stoppable(service.Observer):
    def __init__(self, stop=True):
        self.stop = stop
        self.progressed = []

    def should_stop(self):
        return self.stop

    def progress(self, p):
        self.progressed.append(p.phase)


def test_live_from_start_is_off_by_default():
    assert "live_from_start" not in service.ydl_options(Options(), "mp4")


def test_live_from_start_reaches_ytdlp_one_fragment_at_a_time():
    opts = service.ydl_options(Options(live_from_start=True, concurrent_fragments=8), "mp4")

    assert opts["live_from_start"] is True
    assert opts["concurrent_fragment_downloads"] == 1


def test_the_fragments_of_a_live_end_when_someone_stops():
    wanted = {"stop": False}

    def fragments(ctx):
        for index in range(10):
            if index == 3:
                wanted["stop"] = True
            yield index

    generate = service._until_stopped(fragments, lambda: wanted["stop"])

    assert list(generate({})) == [0, 1, 2]


def test_stopping_a_recording_does_not_raise_in_the_hooks():
    observer = Stoppable()
    opts = service.ydl_options(Options(live_from_start=True), "mp4", observer)
    live = {"is_live": True, "title": "ao vivo"}

    opts["progress_hooks"][0]({"status": "downloading", "info_dict": live})
    opts["postprocessor_hooks"][0](
        {"status": "started", "postprocessor": "FFmpegMerger", "info_dict": live}
    )

    assert observer.progressed == [service.RECORDING, "juntando vídeo e áudio"]


def test_stopping_a_common_video_still_raises_with_the_option_on():
    opts = service.ydl_options(Options(live_from_start=True), "mp4", Stoppable())

    with pytest.raises(service.DownloadCancelled):
        opts["progress_hooks"][0]({"status": "downloading", "info_dict": {"is_live": False}})


def test_a_live_without_the_option_is_not_treated_as_a_recording():
    opts = service.ydl_options(Options(), "mp4", Stoppable())

    with pytest.raises(service.DownloadCancelled):
        opts["progress_hooks"][0]({"status": "downloading", "info_dict": {"is_live": True}})


def test_a_stopped_recording_counts_and_ends_the_round(monkeypatch):
    monkeypatch.setattr(service, "Downloader", FakeDownloader([{"id": "a"}, {"id": "b"}]))

    result = service.download(
        ["https://youtu.be/a", "https://youtu.be/b"], Options(), "mp4", Stoppable()
    )

    assert result.downloaded == 1
    assert result.cancelled


def test_a_stopped_recording_does_not_start_the_mp3_pass(monkeypatch):
    monkeypatch.setattr(service, "Downloader", FakeDownloader([{"id": "a"}, {"id": "a"}]))

    results = service.run(["https://youtu.be/a"], Options(format="both", dry_run=True), Stoppable())

    assert list(results) == ["mp4"]


def test_a_link_without_the_live_start_says_what_to_do(monkeypatch):
    error = "ERROR: [generic] x: --live-from-start is passed, but there are no formats"
    monkeypatch.setattr(service, "Downloader", FakeDownloader([None], errors_per_link=[[error]]))

    result = service.download(["https://site/x"], Options(live_from_start=True), "mp4")

    assert [f.reason for f in result.failures] == [service.NO_LIVE_START_REASON]


def test_a_stopped_recording_stays_out_of_the_archive(tmp_path: Path):
    archive = tmp_path / "baixados.txt"
    info = {"id": "abc", "extractor_key": "Youtube", "extractor": "youtube"}

    with service.Downloader({"quiet": True, "download_archive": str(archive)}) as ydl:
        ydl.stop_requested = lambda: True
        ydl.record_download_archive(info)
        assert not archive.exists()

        ydl.stop_requested = lambda: False
        ydl.record_download_archive(info)
        assert "youtube abc" in archive.read_text(encoding="utf-8")


def fake_clock(monkeypatch, start=1000.0):
    """Um relógio que anda só no `sleep`: a espera de minutos passa num instante."""
    now = {"t": start}
    monkeypatch.setattr(service.time, "time", lambda: now["t"])
    monkeypatch.setattr(service.time, "sleep", lambda s: now.__setitem__("t", now["t"] + s))
    return now


UPCOMING = {"title": "Agendada", "live_status": "is_upcoming"}


def test_waiting_is_off_by_default_and_in_a_simulation():
    assert "wait_for_video" not in service.ydl_options(Options(), "mp4")
    simulated = service.ydl_options(Options(wait_for_live=True, dry_run=True), "mp4")
    assert "wait_for_video" not in simulated


def test_waiting_reaches_ytdlp():
    opts = service.ydl_options(Options(wait_for_live=True), "mp4")

    assert opts["wait_for_video"] == (service.WAIT_MIN, service.WAIT_MAX)


@pytest.mark.parametrize(
    ("starts_in", "expected"),
    [
        (None, service.WAIT_MIN),
        (-500, service.WAIT_MIN),
        (90, 90),
        (9000, service.WAIT_MAX),
    ],
)
def test_the_next_check_follows_the_schedule(starts_in, expected):
    assert service.next_live_check(starts_in) == expected


def test_ytdlp_still_has_the_wait_this_program_replaces():
    assert callable(YoutubeDL._wait_for_video)


@pytest.mark.parametrize(
    "info",
    [
        {"title": "Com formatos", "live_status": "is_upcoming", "formats": [{}]},
        {"title": "Acabou", "live_status": "was_live"},
        {"title": "Indisponível", "live_status": "not_live"},
    ],
)
def test_only_what_is_still_to_start_waits(monkeypatch, info):
    fake_clock(monkeypatch)

    with service.Downloader({"quiet": True, "wait_for_video": (60, 300)}) as ydl:
        assert ydl._wait_for_video(info) is None


def test_the_wait_tells_how_long_and_then_checks_again(monkeypatch):
    now = fake_clock(monkeypatch)
    heard = []

    with service.Downloader({"quiet": True, "wait_for_video": (60, 300)}) as ydl:
        ydl.on_waiting = lambda title, starts_in: heard.append((title, starts_in))
        with pytest.raises(service.ReExtractInfo):
            ydl._wait_for_video({**UPCOMING, "release_timestamp": now["t"] + 9000})

    assert heard[0] == ("Agendada", 9000.0)
    assert len(heard) == service.WAIT_MAX


def test_the_wait_without_a_known_time_also_checks_again(monkeypatch):
    fake_clock(monkeypatch)

    with (
        service.Downloader({"quiet": True, "wait_for_video": (60, 300)}) as ydl,
        pytest.raises(service.ReExtractInfo),
    ):
        ydl._wait_for_video({"title": "Twitch sem live"})


def test_stopping_ends_the_wait(monkeypatch):
    fake_clock(monkeypatch)
    calls = []

    with service.Downloader({"quiet": True, "wait_for_video": (60, 300)}) as ydl:
        ydl.stop_requested = lambda: len(calls) >= 3
        ydl.on_waiting = lambda title, starts_in: calls.append(starts_in)
        with pytest.raises(service.DownloadCancelled):
            ydl._wait_for_video(UPCOMING)

    assert len(calls) == 3


def test_the_wait_reaches_the_observer_as_progress(monkeypatch):
    class Waiting(FakeDownloader):
        def extract_info(self, link, download=True):
            self.on_waiting("Agendada", 125.7)
            self.on_waiting("Agendada", -3)
            return super().extract_info(link, download)

    class Watcher(service.Observer):
        seen = []

        def progress(self, p):
            self.seen.append((p.phase, p.title, p.eta))

    monkeypatch.setattr(service, "Downloader", Waiting([{"id": "a"}]))

    service.download(["https://youtu.be/a"], Options(), "mp4", Watcher())

    assert Watcher.seen == [
        (service.WAITING, "Agendada", 125),
        (service.WAITING, "Agendada", None),
    ]


# --------------------------------------------------------------- validações


def test_browser_cookies_accepts_a_profile_and_normalizes_the_name():
    assert service.browser_cookies("Chrome:Default") == ("chrome", "Default", None, None)


def test_browser_cookies_accepts_just_the_browser():
    assert service.browser_cookies("firefox") == ("firefox", None, None, None)


def test_browser_cookies_refuses_an_unknown_browser():
    with pytest.raises(InvalidInput, match="não suportado"):
        service.browser_cookies("internet-explorer")


@pytest.mark.parametrize("value", ["0", "9", "320", "320K"])
def test_audio_quality_accepts_vbr_and_bitrate(value: str):
    assert service.audio_quality(value) == value


def test_audio_quality_refuses_text():
    with pytest.raises(InvalidInput):
        service.audio_quality("alta")


def test_rate_limit_understands_suffixes():
    assert service.rate_limit("2M") == 2 * 1024 * 1024


def test_rate_limit_refuses_nonsense():
    with pytest.raises(InvalidInput):
        service.rate_limit("rapido")


def test_options_turn_on_playlist_when_items_are_selected():
    assert Options(playlist_items="1-10").playlist is True
    assert Options(playlist_reverse=True).playlist is True
    assert Options().playlist is False


def test_options_refuse_an_unknown_format():
    with pytest.raises(InvalidInput):
        Options(format="avi")
