"""Chuva: acumulados em janelas que olham para trás, e a cascata do período.

A chuva é a única grandeza que precisa de dado **fora** do período escolhido: um acumulado de
96 h, ou o do mês, começa antes do início da janela que a pessoa marcou na barra lateral. Por
isso ela não mora no catálogo de `app/variaveis.py`, onde todo produto respeita a janela — e por
isso tem aba própria, com a sua própria carga de dados.

As janelas seguem a convenção do projeto, `(início, fim]`: a leitura de uma hora cobre a hora
que termina nela, então entra a leitura do fim e não a do início.

Só cálculo, sem tela, para poder ser testado.
"""
from datetime import datetime, timedelta

import pandas as pd

# Janelas que a equipe pediu, em horas, mais o acumulado do mês corrente.
JANELAS_HORAS = (3, 6, 12, 24, 48, 72, 96)
MENSAL = "Mensal"
COLUNA = "CHUVA"
# Nome que a cascata do estado usa no lugar da estação, por ela juntar todas numa linha só.
TODO_O_ESTADO = "Todo o estado"


def rotulos() -> list[str]:
    return [f"{horas} h" for horas in JANELAS_HORAS] + [MENSAL]


def inicio_necessario(fim: datetime) -> datetime:
    return min(_comeco_do_mes(fim), fim - timedelta(hours=max(JANELAS_HORAS)))


def _comeco_do_mes(fim: datetime) -> datetime:
    return (fim - timedelta(microseconds=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def janela(fim: datetime, rotulo: str) -> tuple[datetime, datetime]:
    if rotulo == MENSAL:
        return _comeco_do_mes(fim), fim
    return fim - timedelta(hours=int(rotulo.split()[0])), fim


def horas_da_janela(fim: datetime, rotulo: str) -> float:
    comeco, termino = janela(fim, rotulo)
    return (termino - comeco).total_seconds() / 3600


def acumulado(leituras: pd.DataFrame, fim: datetime, rotulo: str) -> pd.Series:
    if leituras.empty or COLUNA not in leituras:
        return pd.Series(dtype=float)
    comeco, termino = janela(fim, rotulo)
    dentro = leituras[(leituras["dt_local"] > comeco) & (leituras["dt_local"] <= termino)]
    if dentro.empty:
        return pd.Series(dtype=float)
    return dentro.groupby("Estação", sort=False)[COLUNA].sum().rename(rotulo)


def cobre(leituras: pd.DataFrame, fim: datetime, rotulo: str) -> bool:
    if leituras.empty:
        return False
    comeco, _ = janela(fim, rotulo)
    return leituras["dt_local"].min() <= comeco + timedelta(hours=1)


def cascata(leituras: pd.DataFrame, por_dia: bool, ultimas_horas: int = 24) -> pd.DataFrame:
    if leituras.empty or COLUNA not in leituras:
        return pd.DataFrame(columns=["Estação", "Passo", "ordem", "valor", "base", "topo", "tipo"])

    marcas = leituras["dt_local"]
    # No diário a leitura das 00:00 fecha o dia anterior, como nos produtos e no catálogo
    quando = (marcas - pd.Timedelta(hours=1)).dt.normalize() if por_dia else marcas
    passos = leituras.assign(_quando=quando)
    if not por_dia:
        ultimos = sorted(passos["_quando"].unique())[-ultimas_horas:]
        passos = passos[passos["_quando"].isin(ultimos)]

    somas = (passos.groupby(["Estação", "_quando"], sort=True)[COLUNA].sum()
             .reset_index().rename(columns={COLUNA: "valor"}))
    # Só a hora nas barras horárias: são todas do mesmo dia ou do anterior, e a data completa
    # em 24 rótulos inclinados não caberia.
    formato = "%d/%m" if por_dia else "%H:%M"
    somas["Passo"] = somas["_quando"].dt.strftime(formato)
    somas["base"] = somas.groupby("Estação")["valor"].cumsum() - somas["valor"]
    somas["topo"] = somas["base"] + somas["valor"]
    somas["ordem"] = somas.groupby("Estação").cumcount()
    somas["tipo"] = "passo"

    totais = (somas.groupby("Estação", sort=False)
              .agg(valor=("valor", "sum"), ordem=("ordem", "max"))
              .reset_index()
              .assign(Passo="Total", base=0.0, tipo="total"))
    totais["topo"] = totais["valor"]
    totais["ordem"] = totais["ordem"] + 1

    colunas = ["Estação", "Passo", "ordem", "valor", "base", "topo", "tipo"]
    return pd.concat([somas[colunas], totais[colunas]], ignore_index=True)


def cascata_do_estado(leituras: pd.DataFrame, por_dia: bool, ultimas_horas: int = 24) -> pd.DataFrame:
    if leituras.empty or COLUNA not in leituras:
        return cascata(leituras, por_dia, ultimas_horas)
    return cascata(leituras.assign(**{"Estação": TODO_O_ESTADO}), por_dia, ultimas_horas)
