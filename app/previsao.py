"""As contas da página de previsão, sem tela.

A busca é do `modulos/openmeteo.py`. Aqui fica o que a página faz com o que voltou: guardar a
previsão de cada modelo até sair a rodada seguinte, para todos os que abrem a página, e montar as
séries dos gráficos de cada estação, um modelo ao lado do outro.

As decisões que desenham a página estão em docs/escopo_v0.3.1.md.
"""
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app import variaveis
from modulos import config, openmeteo

HORA, DIA = variaveis.HORA, variaveis.DIA

UF = "MS"                  # a previsão é só de MS (decisão 1 do escopo)
ESTACAO_INICIAL = "A702"   # Campo Grande, a que a página abre mostrando

# Uma cor por modelo, a mesma em todo gráfico. Pela ordem alfabética, as cores mudariam quando um
# modelo ficasse de fora, e quem olha se guia pela cor.
CORES = {"ECMWF": "#1f77b4", "GFS": "#ff7f0e", "ICON": "#2ca02c"}


# =====================================================
# A PREVISÃO GUARDADA
# =====================================================
@dataclass(frozen=True)
class Obtida:
    """A previsão de um modelo, e o aviso quando ela não é a rodada mais nova."""

    tabela: pd.DataFrame
    aviso: str | None = None


def rodada_da(tabela: pd.DataFrame) -> datetime | None:
    """A rodada carimbada na tabela, que é uma só: cada tabela guardada é de um modelo."""
    return None if tabela.empty else tabela["rodada_utc"].iloc[0]


class Guarda:
    """A última previsão de cada modelo, guardada no processo e dividida por todos.

    O painel roda o script a cada clique, mas a previsão só muda quando sai uma rodada nova, de 6
    em 6 horas. Guardada aqui, a primeira pessoa depois da rodada espera a busca, e as outras
    recebem a mesma tabela. A chave é a rodada, e não o relógio (decisão 7 do escopo).

    Cada modelo é buscado e guardado sozinho. As rodadas saem em horas diferentes (o ICON fica
    pronto umas 3 h e meia depois do horário dela, o ECMWF quase 8 h depois), e um pedido com
    os três modelos teria de ser refeito inteiro a cada uma: 2,1 chamadas por ponto contra 1.

    Se o Open-Meteo falhar, a página segue com a rodada guardada e um aviso com a data dela, como
    pede o escopo. Uma trava por modelo impede que duas pessoas busquem a mesma rodada ao mesmo
    tempo, o que gastaria a cota duas vezes.
    """

    def __init__(self) -> None:
        self._tabelas: dict[tuple[str, str], pd.DataFrame] = {}
        self._travas: dict[tuple[str, str], threading.Lock] = {}
        self._trava_das_travas = threading.Lock()

    def _trava(self, chave: tuple[str, str]) -> threading.Lock:
        with self._trava_das_travas:
            return self._travas.setdefault(chave, threading.Lock())

    def precisa_buscar(self, pontos: str, modelo: str, rodada: datetime | None) -> bool:
        """Se a próxima `obter` vai ao Open-Meteo: não há nada guardado, ou saiu rodada nova."""
        guardada = self._tabelas.get((pontos, modelo))
        if guardada is None:
            return True
        return rodada is not None and (rodada_da(guardada) is None or rodada_da(guardada) < rodada)

    def obter(self, pontos: str, modelo: str, rodada: datetime | None,
              buscar: Callable[[], pd.DataFrame]) -> Obtida:
        """A previsão do modelo, buscando só se a rodada guardada ficou para trás.

        `pontos` nomeia o conjunto de pontos ("estações", "grade"), e `rodada` é a que o
        Open-Meteo diz estar servindo, ou None quando nem isso respondeu. A comparação é "ficou
        para trás", e não "é diferente": a busca pode trazer uma rodada mais nova que a conferida
        minutos antes, e essa não deve ser buscada de novo.
        """
        chave = (pontos, modelo)
        with self._trava(chave):
            # Conferido dentro da trava: quem esperou a busca de outra pessoa já sai com ela
            guardada = self._tabelas.get(chave)
            if not self.precisa_buscar(pontos, modelo, rodada):
                if rodada is None:
                    return Obtida(guardada, "não deu para conferir se há rodada nova no Open-Meteo")
                return Obtida(guardada)
            try:
                nova = buscar()
            except openmeteo.ErroOpenMeteo as erro:
                if guardada is None:
                    raise
                return Obtida(guardada, str(erro))
            self._tabelas[chave] = nova
            return Obtida(nova)


# =====================================================
# AS SÉRIES DOS GRÁFICOS
# =====================================================
@dataclass(frozen=True)
class Serie:
    """Uma linha do gráfico: de que coluna sai e a regra impressa embaixo dele."""

    coluna: str        # da tabela horária do openmeteo, ou da diária, conforme o modo
    nome: str
    regra: str


@dataclass(frozen=True)
class Grandeza:
    """Um gráfico da página, com as séries de cada modo."""

    nome: str
    unidade: str
    decimais: int
    zero_na_base: bool
    series: dict[str, tuple[Serie, ...]]
    circular: bool = False   # a direção: em graus, ela não divide o eixo com nada


# As do INMET, menos radiação e pressão, que a previsão não pede (decisão 4 do escopo). O dia
# segue a regra do projeto: a leitura das 00:00 fecha o dia anterior.
GRANDEZAS = {g.nome: g for g in (
    Grandeza("Temperatura", "°C", 1, False, {
        HORA: (Serie("temperatura", "Na hora", "a temperatura a 2 m na hora cheia"),),
        DIA: (Serie("temp_max", "Máxima", "a maior das 24 horas do dia"),
              Serie("temp_min", "Mínima", "a menor das 24 horas"),
              Serie("temp_media", "Média", "a média das 24 horas")),
    }),
    Grandeza("Umidade", "%", 0, False, {
        HORA: (Serie("umidade", "Na hora", "a umidade relativa a 2 m na hora cheia"),),
        DIA: (Serie("umid_min", "Mínima", "a menor das 24 horas do dia"),),
    }),
    Grandeza("Ponto de orvalho", "°C", 1, False, {
        HORA: (Serie("orvalho", "Na hora", "o ponto de orvalho a 2 m na hora cheia"),),
        DIA: (Serie("orvalho_medio", "Médio", "a média das 24 horas do dia"),),
    }),
    Grandeza("Vento", "km/h", 1, True, {
        HORA: (Serie("vento", "Velocidade", "a velocidade a 10 m na hora cheia"),
               Serie("rajada", "Rajada", "a rajada mais forte da hora que termina ali")),
        DIA: (Serie("vento_max", "Velocidade máxima", "a maior velocidade das 24 horas do dia"),
              Serie("rajada_max", "Rajada máxima", "a maior rajada das 24 horas")),
    }),
    Grandeza("Direção do vento", "°", 0, False, {
        HORA: (Serie("direcao", "Na hora", "de onde o vento sopra, na hora cheia"),),
        DIA: (Serie("direcao_dominante", "Dominante",
                    "a resultante das 24 horas, pesada pela velocidade, como no observado"),),
    }, circular=True),
    Grandeza("Chuva", "mm", 1, True, {
        HORA: (Serie("chuva", "Na hora", "a chuva da hora que termina ali"),),
        DIA: (Serie("chuva", "Acumulada", "a soma das 24 horas do dia"),),
    }),
)}


def _por_modo(horaria: pd.DataFrame, modo: str, fuso, desde: pd.Timestamp) -> pd.DataFrame:
    """A tabela do modo, com o instante local em `dt_local`, só do momento `desde` em diante.

    No diário entram só os dias inteiros: o último da previsão e o último do ICON param no meio, e
    uma máxima de meio dia pareceria uma queda de temperatura que nenhum modelo previu.
    """
    if modo == HORA:
        tabela = horaria.assign(dt_local=horaria["hora_prevista_utc"].dt.tz_convert(fuso))
        return tabela[tabela["dt_local"] >= desde]
    dias = openmeteo.diario(horaria, fuso)
    dias = dias[dias["horas"] == 24]
    dias = dias.assign(dt_local=pd.to_datetime(dias["dia_previsto"]).dt.tz_localize(fuso))
    return dias[dias["dt_local"] >= desde.normalize()]


def series(horaria: pd.DataFrame, grandeza: str, modo: str, desde: pd.Timestamp,
           fuso=config.FUSO_MS) -> pd.DataFrame:
    """As linhas do gráfico de uma grandeza num ponto: `dt_local`, `Modelo`, `valor` e `Série`.

    `horaria` é a previsão do ponto, com um ou mais modelos. `desde` corta o que já passou: no
    modo horário, a hora em curso; no diário, o dia de hoje inteiro.
    """
    colunas = ["dt_local", "Modelo", "valor", "Série"]
    if horaria.empty:
        return pd.DataFrame(columns=colunas)
    tabela = _por_modo(horaria, modo, fuso, desde)
    pedacos = [tabela.assign(Modelo=tabela["modelo"].map(openmeteo.NOMES), valor=tabela[serie.coluna],
                             Série=serie.nome)[colunas]
               for serie in GRANDEZAS[grandeza].series[modo]]
    longo = pd.concat(pedacos, ignore_index=True).dropna(subset=["valor"])
    return longo.sort_values(["Modelo", "Série", "dt_local"], ignore_index=True)


def chuva_total(horaria: pd.DataFrame, desde: pd.Timestamp, fuso=config.FUSO_MS) -> pd.DataFrame:
    """Quanto cada modelo prevê de chuva somando de `desde` até onde ele alcança, e até quando."""
    tabela = _por_modo(horaria, HORA, fuso, desde)
    return (tabela.groupby("modelo")
            .agg(chuva=("chuva", lambda valores: valores.sum(min_count=1)), ate=("dt_local", "max"))
            .rename(index=openmeteo.NOMES).reset_index(names="Modelo"))


def planilha(horaria: pd.DataFrame, fuso=config.FUSO_MS) -> pd.DataFrame:
    """A previsão diária do ponto como vai para o CSV: um dia por linha e modelo."""
    dias = openmeteo.diario(horaria, fuso)
    dias = dias.assign(modelo=dias["modelo"].map(openmeteo.NOMES),
                       rodada_utc=dias["rodada_utc"].dt.strftime("%Y-%m-%d %H:%M"))
    return dias.drop(columns=["latitude", "longitude"]).round(1)
