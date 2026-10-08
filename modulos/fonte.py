"""De onde vêm os dados: hoje as APIs públicas; depois, o banco da UNIGEO ou a API de leitura.

Os produtos e o painel pedem as estações e as leituras a este módulo, e não direto a quem as
busca. Cada instalação escolhe a fonte pela configuração (`FONTE_DADOS`, no `.env` do servidor
ou nos Secrets do Streamlit Cloud), e o resto do código não muda: as contas, os mapas e as
planilhas não sabem de onde as leituras vieram. É a costura descrita em
docs/plano_arquitetura.md, que permite levar o painel para o servidor da unidade sem mexer nos
produtos.

Por enquanto há uma fonte só, "apis": cada função repassa o pedido ao `inmet.py`, exatamente
como o código fazia antes. As outras ("banco" e "api_leitura") entram nas próximas versões, com
as mesmas funções e o mesmo formato de resposta.

As funções procuram o `inmet` na hora de cada chamada, sem guardar referência às funções dele.
É o que deixa os testes trocarem a API do INMET por dados sintéticos, como sempre fizeram.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import config, inmet

# As fontes que esta versão sabe usar
FONTES = ("apis",)

# Por enquanto o único erro de fonte é o do INMET. Quando o banco chegar, este nome passa a ser a
# classe-mãe dos erros das duas fontes, e quem trata ErroFonte não precisa mudar.
ErroFonte = inmet.ErroINMET


def _conferir_fonte() -> None:
    """Para na primeira chamada se a configuração pedir uma fonte que esta versão não tem.

    Sem isto, um FONTE_DADOS escrito errado no servidor cairia em silêncio para as APIs, e
    ninguém saberia que o painel não está lendo de onde deveria.
    """
    if config.FONTE_DADOS not in FONTES:
        raise ValueError(f"FONTE_DADOS={config.FONTE_DADOS!r} não existe nesta versão; "
                         f"as fontes disponíveis são: {', '.join(FONTES)}")


def estacoes(uf: str = config.UF) -> pd.DataFrame:
    """As estações operantes da UF, com coordenadas numéricas e o nome formatado em 'Estação'."""
    _conferir_fonte()
    return inmet.listar_estacoes(uf)


def estacoes_de_apoio(recorte: config.Recorte, lon_celulas, lat_celulas) -> pd.DataFrame:
    """As estações de fora do estado que podem entrar na interpolação de alguma célula."""
    _conferir_fonte()
    return inmet.estacoes_de_apoio(recorte, lon_celulas, lat_celulas)


def leituras_da_estacao(codigo: str, inicio: datetime, fim: datetime,
                        fuso: ZoneInfo = config.FUSO_MS) -> pd.DataFrame | None:
    """As leituras horárias de uma estação na janela (início, fim], ou None se ela não tiver."""
    _conferir_fonte()
    return inmet.baixar_dados_estacao(codigo, inicio, fim, fuso)


def leituras_do_estado(inicio: datetime, fim: datetime, uf: str = config.UF,
                       fuso: ZoneInfo = config.FUSO_MS) -> list[tuple[pd.Series, pd.DataFrame]]:
    """As leituras de todas as estações da UF na janela, uma dupla (estação, leituras) por estação."""
    _conferir_fonte()
    return inmet.baixar_estacoes(inicio, fim, uf=uf, fuso=fuso)


def leituras_de_apoio(recorte: config.Recorte, lon_celulas, lat_celulas, inicio: datetime,
                      fim: datetime, fuso: ZoneInfo = config.FUSO_MS) -> list[tuple[pd.Series, pd.DataFrame]]:
    """As leituras das vizinhas que seguram a borda da interpolação, no mesmo formato."""
    _conferir_fonte()
    return inmet.baixar_apoio(recorte, lon_celulas, lat_celulas, inicio, fim, fuso=fuso)
