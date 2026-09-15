"""Interface de navegador: a mesma rodada da linha de comando, com tela.

A arquitetura em uma frase: **esta camada não sabe baixar vídeo**. Ela recebe
links, monta as opções e chama `service.run` — o mesmo que o terminal chama.
A cadeia de formatos, o histórico por formato e a contagem de falhas são os
mesmos objetos, não uma segunda implementação.

Isso não é purismo. A cadeia de formatos do MP4 é uma linha que decide a
qualidade de tudo o que sai daqui, e o defeito do histórico compartilhado levou
dois meses para aparecer porque só se manifestava na segunda execução. Uma tela
com a sua própria cópia dessas regras funcionaria até a primeira correção feita
de um lado só — e quem usa a tela é exatamente quem não vai saber dizer por que
o vídeo saiu pior.

    service.py            o QUE acontece numa rodada        (sem interface)
    cli.py                interface 1: terminal
    webui/
      __init__.py         fábrica da aplicação
      routes.py           as páginas
      jobs.py             as rodadas em andamento, numa thread
      templates/          HTML
      static/             CSS e JS

A fábrica (`create_app`) existe para que a aplicação seja construída com
configuração explícita — em teste, apontando para pastas temporárias e um
executor falso; em uso, para as pastas reais e o download de verdade. Sem
estado global, sem import com efeito colateral.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["create_app", "WebConfig"]


def create_app(config: WebConfig | None = None, executor=None):
    """Monta a aplicação Flask. Importar este módulo não liga servidor nenhum."""
    from flask import Flask

    from .jobs import RunRegistry
    from .routes import bp

    cfg = config or WebConfig.default()
    app = Flask(__name__)
    app.config["SETTINGS"] = cfg
    app.config["RUNS"] = RunRegistry(executor)
    app.secret_key = cfg.session_key
    app.register_blueprint(bp)
    return app


class WebConfig:
    """Onde a aplicação trabalha e o que ela sugere ao usuário.

    Explícito de propósito: nada aqui é descoberto na hora do import. O teste
    monta a sua instância apontando para `tmp_path`, e a execução real usa a
    raiz do projeto.
    """

    def __init__(
        self,
        *,
        suggested_output_dir: Path,
        links_file: Path | None = None,
        session_key: str = "youtube-downloader-local",
    ) -> None:
        self.suggested_output_dir = Path(suggested_output_dir)
        #: A mesma lista que a linha de comando usa. A tela abre com ela
        #: dentro do campo, para quem já mantém a fila em disco não ter de
        #: copiá-la para dentro do navegador.
        self.links_file = Path(links_file) if links_file else None
        self.session_key = session_key

    @classmethod
    def default(cls) -> WebConfig:
        root = _project_root()
        return cls(
            suggested_output_dir=root / "downloads",
            links_file=root / "links.txt",
        )

    def suggested_links(self) -> str:
        """O conteúdo do links.txt, ou vazio se ele não existir."""
        if self.links_file and self.links_file.is_file():
            return self.links_file.read_text(encoding="utf-8-sig")
        return ""


def _project_root() -> Path:
    """A pasta que contém `pyproject.toml`, a partir do pacote ou do cwd."""
    here = Path(__file__).resolve().parent.parent
    if (here / "pyproject.toml").is_file():
        return here
    current = Path.cwd()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return here
