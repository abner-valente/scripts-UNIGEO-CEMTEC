"""As contas da página de previsão, sem tela.

A busca é do `modulos/openmeteo.py`. Aqui fica o que as páginas fazem com o que voltou: guardar
a previsão de cada modelo até sair a rodada seguinte, para todos os que abrem o painel; montar as
séries dos gráficos de cada estação, um modelo ao lado do outro; preparar os mapas dos dias e os
das semanas; e aplicar a regra do risco de fogo sobre as horas previstas.

As decisões que desenham a página estão em docs/escopo_v0.3.1.md.
"""
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from scipy.interpolate import RegularGridInterpolator, griddata

from app import variaveis
from modulos import config, openmeteo
from modulos.produtos import risco_fogo

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
    tabela: pd.DataFrame
    aviso: str | None = None


def rodada_da(tabela: pd.DataFrame) -> datetime | None:
    return None if tabela.empty else tabela["rodada_utc"].iloc[0]


class Guarda:
    def __init__(self) -> None:
        self._tabelas: dict[tuple[str, str], pd.DataFrame] = {}
        self._travas: dict[tuple[str, str], threading.Lock] = {}
        self._trava_das_travas = threading.Lock()

    def _trava(self, chave: tuple[str, str]) -> threading.Lock:
        with self._trava_das_travas:
            return self._travas.setdefault(chave, threading.Lock())

    def precisa_buscar(self, pontos: str, modelo: str, rodada: datetime | None) -> bool:
        guardada = self._tabelas.get((pontos, modelo))
        if guardada is None:
            return True
        return rodada is not None and (rodada_da(guardada) is None or rodada_da(guardada) < rodada)

    def obter(self, pontos: str, modelo: str, rodada: datetime | None,
              buscar: Callable[[], pd.DataFrame]) -> Obtida:
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
    coluna: str        # da tabela horária do openmeteo, ou da diária, conforme o modo
    nome: str
    regra: str


@dataclass(frozen=True)
class Grandeza:
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
    if modo == HORA:
        tabela = horaria.assign(dt_local=horaria["hora_prevista_utc"].dt.tz_convert(fuso))
        return tabela[tabela["dt_local"] >= desde]
    dias = openmeteo.diario(horaria, fuso)
    dias = dias[dias["horas"] == 24]
    dias = dias.assign(dt_local=pd.to_datetime(dias["dia_previsto"]).dt.tz_localize(fuso))
    return dias[dias["dt_local"] >= desde.normalize()]


def series(horaria: pd.DataFrame, grandeza: str, modo: str, desde: pd.Timestamp,
           fuso=config.FUSO_MS) -> pd.DataFrame:
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
    tabela = _por_modo(horaria, HORA, fuso, desde)
    return (tabela.groupby("modelo")
            .agg(chuva=("chuva", lambda valores: valores.sum(min_count=1)), ate=("dt_local", "max"))
            .rename(index=openmeteo.NOMES).reset_index(names="Modelo"))


def planilha(horaria: pd.DataFrame, fuso=config.FUSO_MS) -> pd.DataFrame:
    dias = diario_com_risco(horaria, fuso)
    dias = dias.assign(modelo=dias["modelo"].map(openmeteo.NOMES),
                       rodada_utc=dias["rodada_utc"].dt.strftime("%Y-%m-%d %H:%M"))
    return dias.drop(columns=["latitude", "longitude"]).round(1)


# =====================================================
# OS MAPAS
# =====================================================
# A licença do Open-Meteo (CC BY 4.0) obriga o crédito. Ele vai numa linha no rodapé do PNG do
# boletim, e não no subtítulo, que fica só com a data (veja `subtitulo`)
CREDITO = "Open-Meteo (CC BY 4.0)/SEMADESC"
DIAS_DA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


@dataclass(frozen=True)
class MapaPrevisto:
    nome: str          # na tela
    titulo: str        # no PNG do boletim
    coluna: str        # do dia tirado das horas (`openmeteo.diario`)
    grandeza: str      # dá a escala de cores do observado e o plural do ranking
    unidade: str
    paleta: str
    decimais: int
    regra: str
    setas: bool = False       # a direção dominante, em seta sobre cada estação
    acumulado: bool = False   # soma os dias, de hoje até o escolhido
    risco: str = ""           # "nivel" ou "horas": a superfície sai da regra aplicada hora a hora


# As paletas são as dos mapas do observado, grandeza por grandeza
MAPAS = {mapa.nome: mapa for mapa in (
    MapaPrevisto("Temperatura máxima", "Temperatura máxima prevista", "temp_max", "Temperatura", "°C",
                 "RdYlBu_r", 1, "a maior das 24 horas do dia"),
    MapaPrevisto("Temperatura mínima", "Temperatura mínima prevista", "temp_min", "Temperatura", "°C",
                 "RdYlBu_r", 1, "a menor das 24 horas do dia"),
    MapaPrevisto("Temperatura média", "Temperatura média prevista", "temp_media", "Temperatura", "°C",
                 "RdYlBu_r", 1, "a média das 24 horas do dia"),
    MapaPrevisto("Umidade mínima", "Umidade mínima prevista", "umid_min", "Umidade", "%",
                 "YlGnBu", 0, "a menor das 24 horas do dia"),
    MapaPrevisto("Chuva do dia", "Chuva prevista no dia", "chuva", "Chuva", "mm",
                 "Blues", 1, "a soma das 24 horas do dia"),
    MapaPrevisto("Chuva acumulada", "Chuva acumulada prevista", "chuva", "Chuva", "mm",
                 "Blues", 1, "a soma dos dias, de hoje até o escolhido", acumulado=True),
    MapaPrevisto("Rajada máxima", "Rajada máxima prevista", "rajada_max", "Vento", "km/h",
                 "turbo", 1, "a maior rajada das 24 horas do dia; a seta é a direção dominante",
                 setas=True),
    # A mesma, sem a seta e sem os graus da direção: o mapa limpo, para o público (22/10/2026)
    MapaPrevisto("Rajada s/ direção", "Rajada máxima prevista", "rajada_max", "Vento", "km/h",
                 "turbo", 1, "a maior rajada das 24 horas do dia, sem a direção"),
    MapaPrevisto("Vento máximo", "Vento máximo previsto", "vento_max", "Vento", "km/h",
                 "turbo", 1, "a maior velocidade das 24 horas do dia; a seta é a direção dominante",
                 setas=True),
    MapaPrevisto("Risco de fogo", "Risco de fogo previsto — pior nível do dia", "risco_max", "Risco", "",
                 "", 0, "o pior nível das 24 horas do dia pela regra 30-30-30, aplicada a cada hora",
                 risco="nivel"),
    MapaPrevisto("Horas em risco alto", "Horas previstas em risco alto de fogo", "horas_risco_alto", "Risco",
                 "h", "YlOrRd", 0, "quantas das 24 horas do dia têm as três condições da regra 30-30-30",
                 risco="horas"),
)}
PADRAO_MAPAS = ("Temperatura máxima", "Temperatura mínima", "Chuva do dia")


def nome_do_dia(dia: date) -> str:
    return f"{DIAS_DA_SEMANA[dia.weekday()]} {dia:%d/%m}"


def dias_inteiros(diaria: pd.DataFrame, hoje: date) -> list[date]:
    dias = diaria.loc[diaria["horas"] == 24, "dia_previsto"]
    return sorted(dia for dia in dias.unique() if dia >= hoje)


def dias_somados(mapa: MapaPrevisto, dia: date, hoje: date) -> int:
    return (dia - hoje).days + 1 if mapa.acumulado else 1


def valores_do_dia(diaria: pd.DataFrame, mapa: MapaPrevisto, dia: date, hoje: date) -> pd.Series:
    inteiros = diaria[diaria["horas"] == 24]
    if not mapa.acumulado:
        return inteiros[inteiros["dia_previsto"] == dia].set_index("ponto")[mapa.coluna]
    janela = inteiros[(inteiros["dia_previsto"] >= hoje) & (inteiros["dia_previsto"] <= dia)]
    grupos = janela.groupby("ponto")[mapa.coluna]
    soma = grupos.sum(min_count=1)
    return soma.where(grupos.count() == dias_somados(mapa, dia, hoje))


def niveis(mapa: MapaPrevisto, dias: int = 1, ajustar: bool = False):
    if ajustar:
        return None
    escolhida = variaveis.escala(mapa, 24.0 * dias)
    if escolhida is None:
        return None
    tipo, valores = escolhida
    return np.linspace(*valores, 21) if tipo == "faixa" else list(valores)


def superficie(valores: pd.Series, pontos: pd.DataFrame, lon_grade: np.ndarray, lat_grade: np.ndarray,
               passo: float = config.GRADE_PREVISAO) -> np.ndarray | None:
    tabela = pontos.set_index("ponto").join(valores.rename("valor"), how="inner").dropna(subset=["valor"])
    if len(tabela) < 4:
        return None
    lons = np.arange(tabela["longitude"].min(), tabela["longitude"].max() + passo / 2, passo)
    lats = np.arange(tabela["latitude"].min(), tabela["latitude"].max() + passo / 2, passo)
    if len(lons) < 2 or len(lats) < 2:
        return None
    rede = np.full((len(lats), len(lons)), np.nan)
    linhas = np.rint((tabela["latitude"].to_numpy() - lats[0]) / passo).astype(int)
    colunas = np.rint((tabela["longitude"].to_numpy() - lons[0]) / passo).astype(int)
    numeros = tabela["valor"].to_numpy(dtype=float)
    rede[linhas, colunas] = numeros
    faltam = np.isnan(rede)
    if faltam.any():
        lon_rede, lat_rede = np.meshgrid(lons, lats)
        rede[faltam] = griddata(np.column_stack([tabela["longitude"], tabela["latitude"]]), numeros,
                                (lon_rede[faltam], lat_rede[faltam]), method="nearest")
    # Fora do retângulo dos pontos (os cantos do enquadramento, longe da divisa) ela estende a
    # última inclinação; o recorte pelo estado esconde isso, e a faixa do modelo segura o resto
    bilinear = RegularGridInterpolator((lats, lons), rede, method="linear", bounds_error=False,
                                       fill_value=None)
    grade = bilinear(np.column_stack([lat_grade.ravel(), lon_grade.ravel()])).reshape(lon_grade.shape)
    return np.clip(grade, numeros.min(), numeros.max())


def nas_estacoes(diaria: pd.DataFrame, estacoes: pd.DataFrame, mapa: MapaPrevisto, dia: date,
                 hoje: date) -> pd.DataFrame:
    if diaria.empty:
        return _estacoes_com(pd.Series(dtype=float), estacoes, mapa.nome)
    direcoes = None
    if mapa.setas:
        direcoes = valores_do_dia(diaria, replace(mapa, coluna="direcao_dominante", acumulado=False), dia, hoje)
    return _estacoes_com(valores_do_dia(diaria, mapa, dia, hoje), estacoes, mapa.nome, direcoes)


def _estacoes_com(valores: pd.Series, estacoes: pd.DataFrame, nome: str,
                  direcoes: pd.Series | None = None) -> pd.DataFrame:
    tabela = pd.DataFrame({"Estação": estacoes["Estação"].to_numpy(),
                           "Latitude": pd.to_numeric(estacoes["VL_LATITUDE"]).to_numpy(),
                           "Longitude": pd.to_numeric(estacoes["VL_LONGITUDE"]).to_numpy()},
                          index=estacoes["CD_ESTACAO"].astype(str).to_numpy())
    tabela[nome] = valores
    if direcoes is not None:
        tabela["Direção (°)"] = direcoes
    return tabela.dropna(subset=[nome]).reset_index(drop=True)


def subtitulo(mapa: MapaPrevisto, dia: date, hoje: date) -> str:
    # Só o dia (ou a janela): o modelo e a rodada ficam no nome do arquivo, porque ao público geral
    # eles não dizem nada (pedido dos meteorologistas, 22/10/2026)
    quando = (f"de {hoje:%d/%m} a {dia:%d/%m/%Y}" if mapa.acumulado and dia > hoje
              else f"para {dia:%d/%m/%Y}")
    return f"Previsão {quando}"


def carimbo(modelo: str, rodada: datetime, mapa: MapaPrevisto, dia: date, hoje: date) -> str:
    quando = f"{hoje:%Y%m%d}_a_{dia:%Y%m%d}" if mapa.acumulado and dia > hoje else f"{dia:%Y%m%d}"
    return f"{openmeteo.NOMES[modelo]}_{quando}_rodada_{rodada:%Y%m%d_%H}UTC"


# =====================================================
# AS SEMANAS
# =====================================================
# Escalas divergentes, fixas e simétricas (decisão 9 do escopo): a faixa do meio é o normal e sai
# branca, o frio azul e o calor vermelho; na chuva, o seco marrom e o úmido verde. Fixas pelo mesmo
# motivo das do observado: a mesma cor quer dizer o mesmo desvio em qualquer semana.
NIVEIS_ANOMALIA_TEMPERATURA = (-5, -4, -3, -2, -1, -0.5, 0.5, 1, 2, 3, 4, 5)
# Na chuva, o lado seco não passa da normal da semana (uns 30 a 50 mm em MS), e o chuvoso passa
# de 80 mm: a primeira semana medida (12/10/2026) saturava metade do estado numa escala até 50.
# Quatro classes de cada lado, para o branco continuar no meio da paleta.
NIVEIS_ANOMALIA_CHUVA = (-50, -30, -20, -10, -5, 5, 10, 25, 50, 100)   # mm na semana
# O ranking é pelo tamanho do desvio, para cima ou para baixo: "maiores anomalias", numa semana
# fria, listaria as estações menos frias
RANKING_SEMANAS = "5 MAIORES DESVIOS DO NORMAL"


@dataclass(frozen=True)
class MapaSemanal:
    nome: str
    titulo: str        # no PNG do boletim
    coluna: str        # da tabela de `openmeteo.buscar_semanas`
    unidade: str
    paleta: str
    niveis: tuple
    regra: str
    decimais: int = 1


# Os nomes dizem "anomalia" na tela inteira: só "Temperatura média" sobre um -2,4 se lia como uma
# temperatura de -2,4 °C (08/10/2026). A máxima e a mínima do EC46 são as de cada 6 horas, quatro por
# dia, na média da semana: em Campo Grande, na semana de 12/10, a "máxima" deu 25,6 °C e a máxima do
# dia do ECMWF de 14 dias, 26,7 °C. Por isso o nome diz "de 6 h", e não "do dia".
MAPAS_SEMANAIS = {mapa.nome: mapa for mapa in (
    MapaSemanal("Anomalia da temperatura média", "Anomalia da temperatura média", "anom_temperatura", "°C",
                "RdBu_r", NIVEIS_ANOMALIA_TEMPERATURA,
                "quanto a média da semana fica acima (+) ou abaixo (−) da normal do modelo para a mesma semana"),
    MapaSemanal("Anomalia da máxima de 6 h", "Anomalia da máxima de 6 h", "anom_temp_max", "°C", "RdBu_r",
                NIVEIS_ANOMALIA_TEMPERATURA,
                "o mesmo para a máxima de cada 6 horas (quatro por dia), que fica entre a média e a máxima do dia"),
    MapaSemanal("Anomalia da mínima de 6 h", "Anomalia da mínima de 6 h", "anom_temp_min", "°C", "RdBu_r",
                NIVEIS_ANOMALIA_TEMPERATURA,
                "o mesmo para a mínima de cada 6 horas (quatro por dia), que fica entre a mínima do dia e a média"),
    MapaSemanal("Anomalia da chuva", "Anomalia da chuva", "anom_chuva", "mm", "BrBG", NIVEIS_ANOMALIA_CHUVA,
                "quanto a chuva da semana fica acima (+) ou abaixo (−) da normal do modelo para a mesma semana"),
)}
PADRAO_SEMANAIS = ("Anomalia da temperatura média", "Anomalia da chuva")


def semanas_inteiras(semanal: pd.DataFrame, rodada: datetime) -> list[date]:
    return sorted(semana for semana in semanal["semana"].unique() if semana >= rodada.date())


def nome_da_semana(semana: date) -> str:
    return f"{semana:%d/%m} a {semana + timedelta(days=6):%d/%m}"


def valores_da_semana(semanal: pd.DataFrame, mapa: MapaSemanal, semana: date) -> pd.Series:
    return semanal[semanal["semana"] == semana].set_index("ponto")[mapa.coluna]


def nas_estacoes_na_semana(semanal: pd.DataFrame, estacoes: pd.DataFrame, mapa: MapaSemanal,
                           semana: date) -> pd.DataFrame:
    valores = pd.Series(dtype=float) if semanal.empty else valores_da_semana(semanal, mapa, semana)
    return _estacoes_com(valores, estacoes, mapa.titulo)


def subtitulo_da_semana(semana: date) -> str:
    return f"Semana de {semana:%d/%m} a {semana + timedelta(days=6):%d/%m/%Y}"


def carimbo_da_semana(rodada: datetime, semana: date) -> str:
    return f"EC46_semana_{semana:%Y%m%d}_rodada_{rodada:%Y%m%d_%H}UTC"


# =====================================================
# O RISCO DE FOGO PREVISTO
# =====================================================
# A regra é a do produto (`risco_fogo.condicoes_atendidas`), usada como está (decisão 8 do escopo):
# temperatura, umidade e rajada estão entre as sete variáveis horárias, e não há pedido a mais. Um
# cuidado conhecido: na estação, a regra usa a máxima e a mínima **dentro** da hora (TEM_MAX,
# UMD_MIN); o modelo dá o valor da hora cheia, que fica um pouco aquém dos extremos. O risco
# previsto tende a sair um pouco abaixo do que a estação vai medir.
VARIAVEIS_DO_RISCO = ("temperatura", "umidade", "rajada")
GRANDEZA_RISCO = "Risco de fogo"   # na página das estações, o calendário dos piores níveis
CHAVES_DO_DIA = ["modelo", "rodada_utc", "ponto", "latitude", "longitude", "dia_previsto"]


def risco_por_hora(horaria: pd.DataFrame) -> pd.DataFrame:
    completas = horaria.dropna(subset=list(VARIAVEIS_DO_RISCO))
    atendidas = risco_fogo.condicoes_atendidas(*(completas[coluna] for coluna in VARIAVEIS_DO_RISCO))
    tabela = completas.assign(**dict(zip(risco_fogo.COLUNAS_CONDICOES, atendidas)))
    return tabela.assign(nivel=tabela[risco_fogo.COLUNAS_CONDICOES].sum(axis=1).astype(int))


def _dia_previsto(horaria: pd.DataFrame, fuso) -> pd.Series:
    return pd.Series(risco_fogo.dia_da_leitura(pd.DatetimeIndex(horaria["hora_prevista_utc"]), fuso),
                     index=horaria.index)


def risco_por_dia(horaria: pd.DataFrame, fuso=config.FUSO_MS) -> pd.DataFrame:
    horas = risco_por_hora(horaria)
    if horas.empty:
        return pd.DataFrame(columns=CHAVES_DO_DIA + ["risco_max", "horas_risco_alto"])
    horas = horas.assign(dia_previsto=_dia_previsto(horas, fuso),
                         alto=(horas["nivel"] == risco_fogo.NIVEL_ALTO).astype(int))
    return (horas.groupby(CHAVES_DO_DIA, sort=True)
            .agg(risco_max=("nivel", "max"), horas_risco_alto=("alto", "sum")).reset_index())


def diario_com_risco(horaria: pd.DataFrame, fuso=config.FUSO_MS) -> pd.DataFrame:
    dias = openmeteo.diario(horaria, fuso)
    risco = risco_por_dia(horaria, fuso)
    if dias.empty:
        return dias.assign(risco_max=pd.Series(dtype=float), horas_risco_alto=pd.Series(dtype=float))
    return dias.merge(risco, on=CHAVES_DO_DIA, how="left")


def risco_na_grade(horaria: pd.DataFrame, pontos: pd.DataFrame, dia: date, lon_grade: np.ndarray,
                   lat_grade: np.ndarray, fuso=config.FUSO_MS) -> tuple[np.ndarray, np.ndarray] | None:
    completas = horaria.dropna(subset=list(VARIAVEIS_DO_RISCO))
    if completas.empty:
        return None
    do_dia = completas[_dia_previsto(completas, fuso) == dia]
    niveis = []
    for _, hora in do_dia.groupby("hora_prevista_utc", sort=True):
        por_ponto = hora.set_index("ponto")
        campos = [superficie(por_ponto[coluna], pontos, lon_grade, lat_grade) for coluna in VARIAVEIS_DO_RISCO]
        if any(campo is None for campo in campos):
            continue
        niveis.append(risco_fogo.contar_condicoes(*campos))
    if not niveis:
        return None
    pilha = np.stack(niveis)
    return pilha.max(axis=0).astype(np.int8), (pilha == risco_fogo.NIVEL_ALTO).sum(axis=0)


def subtitulo_do_risco(dia: date) -> str:
    return f"Previsão para {dia:%d/%m/%Y}"


# A previsão da semana contra a normal da época, numa estação: é o gráfico que explica a anomalia.
# A normal não vem pronta: é a previsão menos a anomalia, que o Open-Meteo devolve as duas.
SERIES_SEMANAIS = {
    "Temperatura média": ("temperatura", "anom_temperatura", "°C"),
    "Chuva": ("chuva", "anom_chuva", "mm"),
}
PREVISTA, NORMAL = "Prevista (EC46)", "Normal da época"


def semana_contra_o_normal(semanal: pd.DataFrame, ponto: str, grandeza: str, rodada: datetime) -> pd.DataFrame:
    coluna, anomalia, _ = SERIES_SEMANAIS[grandeza]
    colunas = ["semana", "rotulo", "prevista", "normal", "anomalia"]
    if semanal.empty or coluna not in semanal:
        return pd.DataFrame(columns=colunas)
    semanas = semanas_inteiras(semanal, rodada)
    tabela = semanal[(semanal["ponto"] == ponto) & semanal["semana"].isin(semanas)].sort_values("semana")
    return pd.DataFrame({"semana": tabela["semana"], "rotulo": tabela["semana"].map(nome_da_semana),
                         "prevista": tabela[coluna], "normal": (tabela[coluna] - tabela[anomalia]).round(1),
                         "anomalia": tabela[anomalia]}).dropna().reset_index(drop=True)[colunas]

