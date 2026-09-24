"""Séries no tempo das leituras, prontas para o gráfico e para o mapa.

Só cálculo, sem tela, para poder ser testado — como o dados.py e o qualidade.py. O explorador
usa este módulo tanto no gráfico (uma linha por estação) quanto no mapa (uma coluna por estação
num instante só).
"""
import pandas as pd

FUNCOES = {"Média": "mean", "Máxima": "max", "Mínima": "min", "Soma": "sum"}


def agregar(tabela: pd.DataFrame, coluna: str, por_dia: bool, funcao: str) -> pd.DataFrame:
    """Uma coluna por estação, no tempo — de hora em hora ou resumida por dia."""
    largo = tabela.pivot_table(index="dt_local", columns="Estação", values=coluna, aggfunc="mean")
    if por_dia:
        # A leitura das 00:00 fecha o dia anterior (ela cobre das 23:00 às 00:00), como nos
        # produtos. Sem recuar essa hora, um período de 7 dias vira 8, e o último deles teria
        # uma leitura só — no mapa, um dia inteiro desenhado a partir de um instante.
        largo.index = largo.index - pd.Timedelta(hours=1)
        largo = largo.resample("D").agg(FUNCOES[funcao])
    return largo.dropna(how="all")
