"""Testes das partes que não dependem da rede: leitura de links, opções e contagem."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import main as m

# ---------------------------------------------------------------- ler_links


def test_ler_links_ignora_vazios_comentarios_e_repetidos(tmp_path: Path):
    arquivo = tmp_path / "links.txt"
    arquivo.write_text(
        "﻿# comentario\n"
        "\n"
        "https://youtu.be/a\n"
        "   https://youtu.be/b   \n"
        "https://youtu.be/a\n"
        "# https://youtu.be/c\n",
        encoding="utf-8",
    )

    assert m.ler_links([str(arquivo)]) == ["https://youtu.be/a", "https://youtu.be/b"]


def test_ler_links_aceita_links_soltos_e_mistura_com_arquivo(tmp_path: Path):
    arquivo = tmp_path / "links.txt"
    arquivo.write_text("https://youtu.be/a\n", encoding="utf-8")

    links = m.ler_links(["https://youtu.be/z", str(arquivo), "https://youtu.be/z"])

    assert links == ["https://youtu.be/z", "https://youtu.be/a"]


def test_ler_links_reclama_do_que_nao_e_link_nem_arquivo(tmp_path: Path):
    with pytest.raises(SystemExit, match="Não é um link nem um arquivo"):
        m.ler_links([str(tmp_path / "nao_existe.txt")])


def test_ler_links_reclama_de_arquivo_sem_links(tmp_path: Path):
    arquivo = tmp_path / "vazio.txt"
    arquivo.write_text("# so comentario\n\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="Nenhum link encontrado"):
        m.ler_links([str(arquivo)])


# ------------------------------------------------------------ modelo_de_nome


def test_modelo_de_nome_com_subpasta_de_playlist():
    modelo = m.modelo_de_nome(Path("saida") / "mp4", com_subpasta=True)

    assert modelo == (
        "saida/mp4/%(playlist_title|)s/%(playlist_index&{:03d} - |)s%(title)s [%(id)s].%(ext)s"
    )


def test_modelo_de_nome_sem_subpasta_nao_agrupa_por_playlist():
    modelo = m.modelo_de_nome(Path("saida"), com_subpasta=False)

    assert "%(playlist_title" not in modelo
    assert modelo.startswith("saida/")


# ----------------------------------------------------------- caminho_archive


@pytest.mark.parametrize(
    ("base", "alvo", "esperado"),
    [
        ("baixados.txt", "mp4", "baixados.mp4.txt"),
        ("baixados.txt", "mp3", "baixados.mp3.txt"),
        ("baixados", "mp4", "baixados.mp4.txt"),
    ],
)
def test_caminho_archive_separa_por_formato(base: str, alvo: str, esperado: str):
    assert m.caminho_archive(Path(base), alvo).name == esperado


def test_archive_do_mp4_e_do_mp3_nao_se_misturam():
    # Regressão: com um histórico único, a passada de MP3 do --format both
    # pulava tudo o que a de MP4 acabara de registrar.
    args = m.parse_args(["links.txt", "--archive", "hist.txt"])

    mp4 = m.opcoes_mp4(args, Path("saida/mp4"))["download_archive"]
    mp3 = m.opcoes_mp3(args, Path("saida/mp3"))["download_archive"]

    assert mp4 != mp3


# ---------------------------------------------------------- contar_resultado


def test_contar_resultado_de_video_avulso():
    assert m.contar_resultado({"id": "a"}) == (1, 0)


def test_contar_resultado_de_link_que_falhou():
    assert m.contar_resultado(None) == (0, 1)


def test_contar_resultado_de_playlist_aninhada_com_falhas():
    canal = {
        "entries": [
            {"entries": [{"id": "a"}, None, {"id": "b"}]},
            {"id": "c"},
            None,
        ]
    }

    assert m.contar_resultado(canal) == (3, 2)


# ------------------------------------------------------------------- baixar


class BaixadorFalso:
    """Substitui o Baixador nos testes: devolve resultados prontos, sem rede."""

    def __init__(self, resultados, pulados_por_link=None, erros_por_link=None):
        self._resultados = list(resultados)
        self._pulados = list(pulados_por_link or [0] * len(self._resultados))
        self._erros = list(erros_por_link or [[]] * len(self._resultados))
        self.erros: list[str] = []
        self.pulados = 0
        self._i = -1

    def __call__(self, opts):  # usado no lugar da classe
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, link, download=True):
        self._i += 1
        self.pulados = self._pulados[self._i]
        self.erros = list(self._erros[self._i])
        resultado = self._resultados[self._i]
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


def test_baixar_conta_pulados_do_historico_em_vez_de_falhas(monkeypatch):
    # Regressão: itens já no histórico voltam como None, igual aos que falharam;
    # sem descontá-los, rodar de novo com --archive acusava tudo como falha.
    playlist = {"title": "lista", "entries": [{"id": "a"}, None, None]}
    falso = BaixadorFalso([playlist], pulados_por_link=[2])
    monkeypatch.setattr(m, "Baixador", falso)

    baixados, pulados, falhas = m.baixar(["https://youtu.be/lista"], {}, "MP4")

    assert (baixados, pulados, falhas) == (1, 2, [])


def test_baixar_ainda_reporta_as_falhas_reais(monkeypatch):
    playlist = {"title": "lista", "entries": [{"id": "a"}, None, None]}
    falso = BaixadorFalso([playlist], pulados_por_link=[1], erros_por_link=[["video removido"]])
    monkeypatch.setattr(m, "Baixador", falso)

    baixados, pulados, falhas = m.baixar(["https://youtu.be/lista"], {}, "MP4")

    assert (baixados, pulados) == (1, 1)
    assert falhas == [("https://youtu.be/lista", "video removido")]


def test_baixar_nao_deixa_um_link_derrubar_a_fila(monkeypatch):
    falso = BaixadorFalso([RuntimeError("boom"), {"id": "b"}])
    monkeypatch.setattr(m, "Baixador", falso)

    baixados, pulados, falhas = m.baixar(["https://youtu.be/a", "https://youtu.be/b"], {}, "MP4")

    assert (baixados, pulados) == (1, 0)
    assert falhas == [("https://youtu.be/a", "RuntimeError: boom")]


def test_baixar_registra_falha_quando_nada_veio(monkeypatch):
    falso = BaixadorFalso([None])
    monkeypatch.setattr(m, "Baixador", falso)

    baixados, pulados, falhas = m.baixar(["https://youtu.be/a"], {}, "MP4")

    assert (baixados, pulados) == (0, 0)
    assert falhas == [("https://youtu.be/a", "motivo desconhecido")]


# -------------------------------------------------------- Baixador (archive)


def test_baixador_conta_consulta_positiva_ao_historico(tmp_path: Path):
    historico = tmp_path / "hist.txt"
    historico.write_text("youtube ja_baixado\n", encoding="utf-8")

    with m.Baixador({"download_archive": str(historico), "quiet": True}) as ydl:
        assert ydl.in_download_archive({"id": "ja_baixado", "ie_key": "Youtube"}) is True
        assert ydl.in_download_archive({"id": "novo", "ie_key": "Youtube"}) is False

    assert ydl.pulados == 1


# ------------------------------------------------------------- cookies/args


def test_cookies_navegador_aceita_perfil_e_normaliza_o_nome():
    assert m.cookies_navegador("Chrome:Default") == ("chrome", "Default", None, None)


def test_cookies_navegador_aceita_so_o_navegador():
    assert m.cookies_navegador("firefox") == ("firefox", None, None, None)


def test_cookies_navegador_recusa_navegador_desconhecido():
    with pytest.raises(argparse.ArgumentTypeError, match="não suportado"):
        m.cookies_navegador("internet-explorer")


@pytest.mark.parametrize("valor", ["0", "9", "320", "320K"])
def test_qualidade_audio_aceita_vbr_e_bitrate(valor: str):
    assert m.qualidade_audio(valor) == valor


def test_qualidade_audio_recusa_texto():
    with pytest.raises(argparse.ArgumentTypeError):
        m.qualidade_audio("alta")


def test_taxa_maxima_entende_sufixos():
    assert m.taxa_maxima("2M") == 2 * 1024 * 1024


def test_taxa_maxima_recusa_lixo():
    with pytest.raises(argparse.ArgumentTypeError):
        m.taxa_maxima("rapido")


def test_parse_args_liga_playlist_ao_selecionar_itens():
    assert m.parse_args(["links.txt", "--playlist-items", "1-10"]).playlist is True
    assert m.parse_args(["links.txt", "--playlist-reverse"]).playlist is True
    assert m.parse_args(["links.txt"]).playlist is False


def test_parse_args_recusa_altura_invalida(capsys):
    with pytest.raises(SystemExit):
        m.parse_args(["links.txt", "--max-height", "0"])
    assert "maior que zero" in capsys.readouterr().err


# ------------------------------------------------------------------- opções


def test_opcoes_mp4_aplica_o_limite_de_altura_em_todos_os_ramos():
    args = m.parse_args(["links.txt", "--max-height", "1080"])

    formato = m.opcoes_mp4(args, Path("saida"))["format"]

    assert formato.count("[height<=1080]") == 3
    assert formato.endswith("/best")


def test_opcoes_mp3_embute_capa_e_metadados():
    args = m.parse_args(["links.txt", "--audio-quality", "320"])

    opts = m.opcoes_mp3(args, Path("saida"))
    chaves = [pp["key"] for pp in opts["postprocessors"]]

    assert opts["writethumbnail"] is True
    assert chaves == ["FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail"]
    assert opts["postprocessors"][0]["preferredquality"] == "320"


def test_dry_run_simula_e_nao_baixa():
    args = m.parse_args(["links.txt", "--dry-run"])

    opts = m.opcoes_mp4(args, Path("saida"))

    assert opts["simulate"] is True
    assert "forceprint" in opts


def test_sem_dry_run_nao_simula():
    opts = m.opcoes_mp4(m.parse_args(["links.txt"]), Path("saida"))

    assert "simulate" not in opts


def test_fragmentos_em_paralelo_por_padrao():
    assert (
        m.opcoes_mp4(m.parse_args(["links.txt"]), Path("s"))["concurrent_fragment_downloads"] == 4
    )
