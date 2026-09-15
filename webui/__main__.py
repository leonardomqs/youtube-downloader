"""`python -m webui` — sobe a interface para uso local.

A porta e o host ficam aqui e em `executar/interface.bat`, e os dois precisam
concordar: o .bat abre o navegador na URL antes de o servidor subir. Mudar um
sem o outro dá uma página em branco que parece defeito do programa.

Fica em 127.0.0.1 e não se discute. Este servidor baixa da internet e escreve
em qualquer pasta da máquina; exposto na rede, vira um serviço de download para
quem chegar, com o seu IP e os seus cookies de navegador.
"""

from __future__ import annotations

import os

from . import create_app

HOST = os.environ.get("YTDL_HOST", "127.0.0.1")
PORT = int(os.environ.get("YTDL_PORT", "5300"))

if __name__ == "__main__":
    # Sem acento: estas linhas aparecem numa janela do cmd, que nem sempre
    # esta em UTF-8, e um titulo com mojibake e a primeira coisa que se ve.
    print("youtube-downloader - interface")
    print(f"abra no navegador:  http://{HOST}:{PORT}")
    print("para encerrar, feche esta janela ou pressione Ctrl+C")
    print("ATENCAO: fechar esta janela interrompe o download em andamento.")
    # debug=False de propósito: o recarregador do Flask sobe um segundo
    # processo, e cada download em andamento viraria dois.
    create_app().run(host=HOST, port=PORT, debug=False, threaded=True)
