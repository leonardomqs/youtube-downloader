"""Porta de entrada do terminal: `uv run main.py links.txt`.

O programa tem três camadas — `service.py` (o que acontece numa rodada),
`cli.py` (esta interface) e `webui/` (a de navegador). Este arquivo continua
existindo porque `uv run main.py` é o comando que está no README, nos atalhos e
na memória de quem usa; mudá-lo não traria nada.
"""

from __future__ import annotations

import sys

from cli import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.", file=sys.stderr)
        raise SystemExit(130) from None
