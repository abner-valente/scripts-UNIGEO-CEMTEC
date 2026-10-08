"""As contas da página de previsão, sem tela.

A busca é do `modulos/openmeteo.py`. Aqui fica o que as páginas fazem com o que voltou: guardar
a previsão de cada modelo até sair a rodada seguinte, para todos os que abrem o painel; montar as
séries dos gráficos de cada estação, um modelo ao lado do outro; e preparar os mapas dos dias e
os das semanas.

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


# =====================================================
# OS MAPAS
# =====================================================
# A licença do Open-Meteo (CC BY 4.0) obriga o crédito, que vai no subtítulo do PNG do boletim
CREDITO = "Open-Meteo (CC BY 4.0)/SEMADESC"
DIAS_DA_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


@dataclass(frozen=True)
class MapaPrevisto:
    """Um mapa da página: de que coluna do dia ele sai, e como se colore e se nomeia."""

    nome: str          # na tela
    titulo: str        # no PNG do boletim
    coluna: str        # da tabela diária do openmeteo
    grandeza: str      # dá a escala de cores do observado e o plural do ranking
    unidade: str
    paleta: str
    decimais: int
    regra: str
    setas: bool = False       # a direção dominante, em seta sobre cada estação
    acumulado: bool = False   # soma os dias, de hoje até o escolhido


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
    MapaPrevisto("Vento máximo", "Vento máximo previsto", "vento_max", "Vento", "km/h",
                 "turbo", 1, "a maior velocidade das 24 horas do dia; a seta é a direção dominante",
                 setas=True),
)}
PADRAO_MAPAS = ("Temperatura máxima", "Temperatura mínima", "Chuva do dia")


def nome_do_dia(dia: date) -> str:
    """Como o deslizante escreve o dia: "qui 09/10". O dia da semana é o que se procura."""
    return f"{DIAS_DA_SEMANA[dia.weekday()]} {dia:%d/%m}"


def dias_inteiros(diaria: pd.DataFrame, hoje: date) -> list[date]:
    """Os dias que a previsão tem inteiros, de hoje em diante: são as paradas do deslizante.

    Um dia pela metade (o último, e o do fim do ICON) faria um mapa de máxima de meio dia, que
    pareceria uma queda de temperatura que nenhum modelo previu.
    """
    dias = diaria.loc[diaria["horas"] == 24, "dia_previsto"]
    return sorted(dia for dia in dias.unique() if dia >= hoje)


def dias_somados(mapa: MapaPrevisto, dia: date, hoje: date) -> int:
    """Quantos dias o mapa soma: um, ou de hoje até o escolhido no acumulado."""
    return (dia - hoje).days + 1 if mapa.acumulado else 1


def valores_do_dia(diaria: pd.DataFrame, mapa: MapaPrevisto, dia: date, hoje: date) -> pd.Series:
    """O valor de cada ponto no dia, indexado pelo ponto.

    No acumulado, a soma de hoje até o dia escolhido, e só onde todos esses dias estão inteiros:
    somar o que houver daria menos chuva onde faltou um dia, e não onde choveu menos.
    """
    inteiros = diaria[diaria["horas"] == 24]
    if not mapa.acumulado:
        return inteiros[inteiros["dia_previsto"] == dia].set_index("ponto")[mapa.coluna]
    janela = inteiros[(inteiros["dia_previsto"] >= hoje) & (inteiros["dia_previsto"] <= dia)]
    grupos = janela.groupby("ponto")[mapa.coluna]
    soma = grupos.sum(min_count=1)
    return soma.where(grupos.count() == dias_somados(mapa, dia, hoje))


def niveis(mapa: MapaPrevisto, dias: int = 1, ajustar: bool = False):
    """Os níveis de cor, os mesmos dos mapas do observado (decisão 9 do escopo), ou None.

    None deixa a escala se ajustar ao dado. A regra é a de `variaveis.escala`, que só olha a
    grandeza: o mapa previsto passa por ela como um produto do observado, e uma temperatura
    prevista de 34 °C sai com a mesma cor de uma medida. Na chuva, a duração conta: até 4 dias,
    as classes curtas (até 100 mm); acima, as longas (até 300 mm).
    """
    if ajustar:
        return None
    escolhida = variaveis.escala(mapa, 24.0 * dias)
    if escolhida is None:
        return None
    tipo, valores = escolhida
    return np.linspace(*valores, 21) if tipo == "faixa" else list(valores)


def superficie(valores: pd.Series, pontos: pd.DataFrame, lon_grade: np.ndarray, lat_grade: np.ndarray,
               passo: float = config.GRADE_PREVISAO) -> np.ndarray | None:
    """A previsão dos pontos da grade levada à grade fina do mapa, por interpolação bilinear.

    Não é interpolação de estação: a superfície é a do modelo, que já calculou cada ponto, e aqui
    ela só muda de resolução, de 0,5° para a do mapa. Bilinear, como fazem os visualizadores de
    previsão: comparada em 08/10, a linear por triângulos deixava facetas retas no mapa, e a cúbica
    inventava bolhas entre os pontos. A bilinear nunca passa dos valores do modelo.

    Os pontos só cobrem o estado e uma fileira além da divisa; os nós que faltam no retângulo
    recebem o ponto mais próximo, para a conta não esbarrar em buraco. None se o modelo não trouxe
    o dado nesse dia.
    """
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
    """A previsão do mesmo modelo no ponto de cada estação, para os números e o ranking do mapa.

    Os números sobre o mapa não são lidos da superfície: são a previsão pedida no ponto exato da
    estação, que é a que se compara depois com o que ela mediu. A coluna do valor leva o nome do
    mapa, como os pontos do observado.
    """
    if diaria.empty:
        return _estacoes_com(pd.Series(dtype=float), estacoes, mapa.nome)
    direcoes = None
    if mapa.setas:
        direcoes = valores_do_dia(diaria, replace(mapa, coluna="direcao_dominante", acumulado=False), dia, hoje)
    return _estacoes_com(valores_do_dia(diaria, mapa, dia, hoje), estacoes, mapa.nome, direcoes)


def _estacoes_com(valores: pd.Series, estacoes: pd.DataFrame, nome: str,
                  direcoes: pd.Series | None = None) -> pd.DataFrame:
    """As estações com o valor de cada uma (indexado pelo código), prontas para o mapa."""
    tabela = pd.DataFrame({"Estação": estacoes["Estação"].to_numpy(),
                           "Latitude": pd.to_numeric(estacoes["VL_LATITUDE"]).to_numpy(),
                           "Longitude": pd.to_numeric(estacoes["VL_LONGITUDE"]).to_numpy()},
                          index=estacoes["CD_ESTACAO"].astype(str).to_numpy())
    tabela[nome] = valores
    if direcoes is not None:
        tabela["Direção (°)"] = direcoes
    return tabela.dropna(subset=[nome]).reset_index(drop=True)


def subtitulo(modelo: str, rodada: datetime, mapa: MapaPrevisto, dia: date, hoje: date) -> str:
    """A janela, o modelo e a rodada no subtítulo do PNG do boletim."""
    quando = (f"de {hoje:%d/%m} a {dia:%d/%m/%Y}" if mapa.acumulado and dia > hoje
              else f"para {dia:%d/%m/%Y}")
    return f"Previsão {quando} · {openmeteo.NOMES[modelo]}, rodada de {rodada:%d/%m %H} UTC"


def carimbo(modelo: str, rodada: datetime, mapa: MapaPrevisto, dia: date, hoje: date) -> str:
    """O fim do nome do arquivo: o modelo, o dia (ou a janela) e a rodada."""
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
    """Um mapa da página das semanas: uma anomalia do EC46, com a escala e o texto dela."""

    nome: str
    titulo: str        # no PNG do boletim
    coluna: str        # da tabela de `openmeteo.buscar_semanas`
    unidade: str
    paleta: str
    niveis: tuple
    regra: str
    decimais: int = 1


MAPAS_SEMANAIS = {mapa.nome: mapa for mapa in (
    MapaSemanal("Temperatura média", "Anomalia da temperatura média", "anom_temperatura", "°C", "RdBu_r",
                NIVEIS_ANOMALIA_TEMPERATURA, "a média da semana menos a normal do modelo para a mesma semana"),
    MapaSemanal("Temperatura máxima", "Anomalia da temperatura máxima", "anom_temp_max", "°C", "RdBu_r",
                NIVEIS_ANOMALIA_TEMPERATURA,
                "a máxima de cada 6 horas, na média da semana, menos a normal do modelo"),
    MapaSemanal("Temperatura mínima", "Anomalia da temperatura mínima", "anom_temp_min", "°C", "RdBu_r",
                NIVEIS_ANOMALIA_TEMPERATURA,
                "a mínima de cada 6 horas, na média da semana, menos a normal do modelo"),
    MapaSemanal("Chuva", "Anomalia da chuva", "anom_chuva", "mm", "BrBG", NIVEIS_ANOMALIA_CHUVA,
                "a chuva da semana menos a normal do modelo para a mesma semana"),
)}
PADRAO_SEMANAIS = ("Temperatura média", "Chuva")


def semanas_inteiras(semanal: pd.DataFrame, rodada: datetime) -> list[date]:
    """As semanas que o EC46 cobre inteiras: as que começam no dia da rodada ou depois.

    A semana vai de segunda a domingo. A que já tinha começado quando a rodada saiu só tem os dias
    que faltavam, e a média de três dias passaria por média de semana. A que passa do dia 46 o
    Open-Meteo já devolve vazia.
    """
    return sorted(semana for semana in semanal["semana"].unique() if semana >= rodada.date())


def nome_da_semana(semana: date) -> str:
    """Como o deslizante e o título escrevem a semana: "12/10 a 18/10"."""
    return f"{semana:%d/%m} a {semana + timedelta(days=6):%d/%m}"


def valores_da_semana(semanal: pd.DataFrame, mapa: MapaSemanal, semana: date) -> pd.Series:
    """A anomalia de cada ponto na semana, indexada pelo ponto."""
    return semanal[semanal["semana"] == semana].set_index("ponto")[mapa.coluna]


def nas_estacoes_na_semana(semanal: pd.DataFrame, estacoes: pd.DataFrame, mapa: MapaSemanal,
                           semana: date) -> pd.DataFrame:
    """A anomalia do EC46 no ponto de cada estação, para os números e o ranking do mapa.

    A coluna do valor leva o título ("Anomalia da temperatura média"), e não o nome curto, que é o
    mesmo de um mapa dos dias.
    """
    valores = pd.Series(dtype=float) if semanal.empty else valores_da_semana(semanal, mapa, semana)
    return _estacoes_com(valores, estacoes, mapa.titulo)


def subtitulo_da_semana(rodada: datetime, semana: date) -> str:
    """A semana e a rodada no subtítulo do PNG do boletim."""
    return (f"Semana de {semana:%d/%m} a {semana + timedelta(days=6):%d/%m/%Y} · "
            f"EC46 (média dos membros), rodada de {rodada:%d/%m %H} UTC")


def carimbo_da_semana(rodada: datetime, semana: date) -> str:
    """O fim do nome do arquivo: a semana e a rodada."""
    return f"EC46_semana_{semana:%Y%m%d}_rodada_{rodada:%Y%m%d_%H}UTC"
