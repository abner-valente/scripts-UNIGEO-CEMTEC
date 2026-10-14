"""Risco de fogo na tela: a mesma regra 30-30-30 dos produtos, sobre o dado do painel.

A regra **não** é reescrita aqui. Ela mora em `modulos/produtos/risco_fogo.py` e é importada:
duas implementações do 30-30-30 divergiriam com o tempo, e aí a tela e o relatório diriam
números diferentes sobre a mesma hora — exatamente a discordância que o catálogo de variáveis
veio matar. Pelo mesmo motivo a tabela por estação é a da planilha do produto, coluna por coluna.

O que este módulo faz é traduzir **formato**: o produto trabalha com uma lista de
`(estação, leituras)`, uma estação por vez, e o painel tem uma tabela longa com todas juntas.

Só cálculo, sem tela, para poder ser testado.
"""
import numpy as np
import pandas as pd

from modulos import config
from modulos.produtos import risco_fogo

# As três da regra, como a API as nomeia. A rajada vira `rajada_kmh` porque é assim que o
# produto a procura.
COLUNAS = tuple(risco_fogo.COLUNAS_NECESSARIAS)
RAJADA = "rajada_kmh"
NIVEL_ALTO = risco_fogo.NIVEL_ALTO
NIVEL_MEDIO = risco_fogo.NIVEL_MEDIO


def por_estacao(leituras: pd.DataFrame, estacoes: pd.DataFrame) -> list:
    if leituras.empty or not set(COLUNAS) <= set(leituras.columns):
        return []
    validas = leituras.dropna(subset=list(COLUNAS))
    if validas.empty:
        return []
    validas = validas.assign(**{RAJADA: validas["VEN_RAJ"]})
    cadastro = {linha["Estação"]: linha for _, linha in estacoes.iterrows()}
    return [(cadastro[nome], desta)
            for nome, desta in validas.groupby("Estação", sort=False) if nome in cadastro]


def da_uf(completas: list, uf: str = config.UF) -> list:
    return [(estacao, leituras) for estacao, leituras in completas
            if estacao.get("SG_ESTADO") == uf]


def resumo(completas: list, periodo: config.Periodo, uf: str = config.UF) -> pd.DataFrame:
    if not completas:
        return pd.DataFrame()
    return risco_fogo.montar_tabela([risco_fogo.resumir_estacao(leituras, estacao, periodo, uf)
                                     for estacao, leituras in completas])


def horas(completas: list) -> list:
    if not completas:
        return []
    return sorted(pd.unique(pd.concat([leituras["dt_utc"] for _, leituras in completas])))


def avaliar(completas: list, quais_horas: list, base) -> dict:
    avaliadas = {}
    for hora in quais_horas:
        estacoes = risco_fogo.estacoes_na_hora(completas, hora)
        if len(estacoes) >= config.MIN_ESTACOES_INTERPOLACAO:
            avaliadas[hora] = risco_fogo.HoraAvaliada(risco_fogo.grade_da_hora(estacoes, base),
                                                      estacoes)
    return avaliadas


def horas_que_interessam(avaliadas: dict, nivel_minimo: int = NIVEL_ALTO) -> dict:
    return {hora: avaliada for hora, avaliada in avaliadas.items()
            if avaliada.nivel_estacoes >= nivel_minimo}


def pior_nivel(avaliadas: dict) -> np.ndarray | None:
    return np.maximum.reduce([avaliada.grade for avaliada in avaliadas.values()]) if avaliadas else None


def horas_em_risco_alto(avaliadas: dict) -> np.ndarray | None:
    if not avaliadas:
        return None
    return sum((avaliada.grade == NIVEL_ALTO).astype(int) for avaliada in avaliadas.values())


def por_dia(avaliadas: dict) -> dict:
    dias = {}
    for hora, avaliada in avaliadas.items():
        dia = risco_fogo.dia_da_leitura(pd.DatetimeIndex([hora]))[0]
        dias.setdefault(dia, []).append(avaliada.grade)
    return {dia: np.maximum.reduce(grades) for dia, grades in sorted(dias.items())}


def estacoes_do_dia(avaliadas: dict, dia) -> pd.DataFrame:
    das_horas = [avaliada.estacoes for hora, avaliada in avaliadas.items()
                 if risco_fogo.dia_da_leitura(pd.DatetimeIndex([hora]))[0] == dia]
    if not das_horas:
        return pd.DataFrame()
    return (pd.concat(das_horas, ignore_index=True)
            .groupby(["Estação", "Latitude", "Longitude"], as_index=False)
            [risco_fogo.COLUNA_NIVEL_HORA].max())


def indicadores(pontos: pd.DataFrame, detalhes: bool):
    if not detalhes or not set(risco_fogo.COLUNAS_CONDICOES) <= set(pontos.columns):
        return None
    return risco_fogo.indicadores_condicoes()


def hora_a_hora(completas: list) -> pd.DataFrame:
    return risco_fogo.tabela_horaria(completas) if completas else pd.DataFrame()
