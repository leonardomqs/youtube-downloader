"""As rodadas em andamento: uma de cada vez, numa thread, com o estado em memória.

Aqui está a diferença entre esta interface e a do ajeita-pdf, que é o mesmo
desenho para um trabalho de outra natureza. Comprimir um PDF cabe dentro de uma
requisição HTTP: o navegador manda os arquivos, espera dois segundos e recebe a
página de resultado. Baixar uma playlist de quarenta vídeos, não — e uma
requisição que demora meia hora não é uma requisição, é uma tela branca.

Então a rodada vira uma thread, e a página pergunta o andamento de segundo em
segundo. Duas decisões que valem o comentário:

**Em memória, não em disco.** Diferente do ajeita-pdf, aqui não há arquivo
temporário nenhum: o vídeo já nasce na pasta que o usuário escolheu, porque o
servidor roda na máquina dele. O que precisa sobreviver entre uma requisição e
outra é só o andamento — pequeno, e sem valor nenhum depois que o processo
morre.

**Uma rodada por vez.** O yt-dlp já baixa fragmentos em paralelo (`-j 4`). Dois
downloads simultâneos dividem a mesma banda, terminam no mesmo tempo somados e
transformam o andamento em duas barras que não explicam nada.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import threading
import uuid
from collections import deque

import service

__all__ = ["Run", "RunRegistry", "RunInProgress", "QUEUED", "DOWNLOADING", "DONE"]

#: Quantas linhas de saída do yt-dlp a tela guarda. O suficiente para explicar
#: o que acabou de acontecer, e não tanto que a página fique pesada.
LOG_LIMIT = 200

#: Quantas rodadas ficam no histórico da sessão.
HISTORY_LIMIT = 20

#: Os estados de uma rodada. Em inglês porque são identificadores e viajam no
#: JSON; quem os traduz para a tela é o `routes`, que é quem fala com o usuário.
QUEUED, DOWNLOADING, DONE, CANCELLED, FAILED = (
    "queued",
    "downloading",
    "done",
    "cancelled",
    "failed",
)


class RunInProgress(Exception):
    """Já existe uma rodada rodando. Carrega qual, para a tela poder apontá-la."""

    def __init__(self, run: Run) -> None:
        super().__init__("Já existe uma rodada em andamento.")
        self.run = run


class Run(service.Observer):
    """Uma rodada de download, e o observador que o serviço usa para contá-la.

    A rodada *é* o observador: os retornos do serviço escrevem direto no
    objeto que a página lê. Uma classe a menos entre o download e a tela, e
    nenhuma chance de as duas versões do andamento discordarem.

    Todo acesso passa pelo lock porque são dois mundos: a thread que baixa
    escreve, e cada consulta do navegador lê.
    """

    def __init__(self, ident: str, links: list[str], options: service.Options) -> None:
        self.id = ident
        self.links = links
        self.options = options
        self.created_at = dt.datetime.now()
        self.state = QUEUED
        self.current = service.Progress(total_links=len(links))
        self.items: list[service.Item] = []
        self.failures: list[service.Failure] = []
        self.lines: deque[str] = deque(maxlen=LOG_LIMIT)
        self.results: dict[str, service.Result] = {}
        self.error = ""
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------ o que o serviço conta

    def link_started(self, index: int, total: int, url: str, target: str) -> None:
        with self._lock:
            self.current.current_link = index
            self.current.total_links = total
            self.current.target = target
            self.current.title = ""
            self.current.phase = service.PREPARING
            self.current.percent = 0.0
            self.current.speed = 0.0
            self.current.eta = None
        self.log(f"[{target.upper()} {index}/{total}] {url}")

    def progress(self, p: service.Progress) -> None:
        with self._lock:
            self.current = dataclasses.replace(p)

    def item(self, i: service.Item) -> None:
        with self._lock:
            self.items.append(i)

    def failure(self, f: service.Failure) -> None:
        with self._lock:
            self.failures.append(f)

    def link_finished(
        self, link: str, downloaded: int, skipped: int, failed: int, playlist: str
    ) -> None:
        if not playlist:
            return
        extra = f", {skipped} já no histórico" if skipped else ""
        extra += f", {failed} com erro" if failed else ""
        self.log(f'playlist "{playlist}": {downloaded} vídeo(s){extra}')

    def log(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)

    def should_stop(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------- ciclo de vida

    def started(self) -> None:
        with self._lock:
            self.state = DOWNLOADING

    def finished(self, results: dict[str, service.Result]) -> None:
        with self._lock:
            self.results = results
            cancelled = self._stop.is_set() or any(r.cancelled for r in results.values())
            self.state = CANCELLED if cancelled else DONE

    def broke(self, reason: str) -> None:
        with self._lock:
            self.state = FAILED
            self.error = reason

    def cancel(self) -> None:
        """Pede parada. O serviço interrompe no próximo pedaço de arquivo."""
        self._stop.set()
        self.log("interrompendo a pedido...")

    @property
    def is_over(self) -> bool:
        return self.state in (DONE, CANCELLED, FAILED)

    # ------------------------------------------------------------ leitura

    def snapshot(self) -> dict:
        """Uma foto consistente do estado, para a página desenhar.

        Cru de propósito: números são números e caminhos são caminhos. Quem
        transforma isso em "8,2 MB/s" é o `routes`, que é quem conhece a tela.
        """
        with self._lock:
            return {
                "id": self.id,
                "state": self.state,
                "is_over": self.is_over,
                "dry_run": self.options.dry_run,
                "created_at": self.created_at,
                "current": dataclasses.replace(self.current),
                "items": list(self.items),
                "failures": list(self.failures),
                "results": dict(self.results),
                "lines": list(self.lines),
                "error": self.error,
                "links": list(self.links),
                "options": self.options,
            }


class RunRegistry:
    """As rodadas desta sessão do servidor.

    O executor é injetável porque é o único ponto que fala com a rede: o teste
    passa o seu e exercita a tela inteira — fila, andamento, cancelamento —
    sem baixar um byte.
    """

    def __init__(self, executor=None) -> None:
        self._executor = executor or service.run
        self._runs: dict[str, Run] = {}
        self._order: deque[str] = deque()
        self._active: Run | None = None
        self._lock = threading.Lock()

    def open(self, links: list[str], options: service.Options) -> Run:
        """Cria a rodada e larga a thread. Recusa se já houver uma rodando."""
        with self._lock:
            if self._active is not None and not self._active.is_over:
                raise RunInProgress(self._active)

            run = Run(uuid.uuid4().hex[:12], links, options)
            self._runs[run.id] = run
            self._order.append(run.id)
            self._active = run
            self._forget_old()

        # daemon: fechar a janela do servidor encerra o download junto, que é
        # o que a tela promete e o que a pessoa espera ao fechar a janela.
        threading.Thread(target=self._execute, args=(run,), daemon=True).start()
        return run

    def _execute(self, run: Run) -> None:
        run.started()
        try:
            results = self._executor(run.links, run.options, run)
        except Exception as error:  # a thread não pode morrer calada
            run.broke(f"{type(error).__name__}: {error}")
        else:
            run.finished(results)

    def _forget_old(self) -> None:
        while len(self._order) > HISTORY_LIMIT:
            self._runs.pop(self._order.popleft(), None)

    def get(self, ident: str) -> Run | None:
        with self._lock:
            return self._runs.get(ident)

    def active(self) -> Run | None:
        with self._lock:
            if self._active is not None and not self._active.is_over:
                return self._active
            return None

    def all(self) -> list[Run]:
        """Da mais recente para a mais antiga."""
        with self._lock:
            return [self._runs[i] for i in reversed(self._order) if i in self._runs]
