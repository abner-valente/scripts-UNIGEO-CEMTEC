"""Acesso ao Open-Meteo: a previsão dos modelos ECMWF, GFS e ICON, ponto a ponto.

É o cliente da segunda fonte do projeto, ao lado do inmet.py e com o mesmo papel: buscar e
devolver tabelas, sem conta de produto. As decisões que o desenham estão em
docs/escopo_v0.3.1.md: só MS, três modelos, 14 dias e sete variáveis horárias, das quais saem as
nove diárias do boletim.

Como o Open-Meteo cobra, medido em 05 e 06/10/2026:
- cada ponto pedido é uma chamada;
- as variáveis contam por modelo, e passar de 10 pesa na proporção: 7 variáveis × 3 modelos = 21,
  ou 2,1 chamadas por ponto;
- passar de 14 dias também pesa na proporção (dias ÷ 14);
- o limite de 600 chamadas por minuto é conferido **antes** de somar o pedido: um pedido maior
  passa, mas trava o resto do minuto. Por isso os pedidos grandes vão em lotes de até 600, com um
  minuto entre eles.

A previsão chega em UTC, hora a hora, e cada hora fecha o intervalo que termina nela, como no
INMET: a chuva das 15:00 é a que caiu entre 14:00 e 15:00. O dia se monta pela mesma regra do
projeto, em `diario`.
"""
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely

from . import config

# As sete variáveis horárias, com o nome que têm no Open-Meteo e o que têm nas nossas tabelas. São
# as grandezas que o INMET mede, menos radiação e pressão (decisão da equipe, 06/10/2026).
VARIAVEIS = {
    "temperature_2m": "temperatura",
    "relative_humidity_2m": "umidade",
    "dew_point_2m": "orvalho",
    "precipitation": "chuva",
    "wind_speed_10m": "vento",
    "wind_gusts_10m": "rajada",
    "wind_direction_10m": "direcao",
}
COLUNAS_HORARIAS = ["modelo", "rodada_utc", "ponto", "latitude", "longitude", "hora_prevista_utc",
                    *VARIAVEIS.values()]

# O EC46 por semana: a previsão (média dos membros; na chuva, o total da semana) e a anomalia, que é
# ela menos a normal do próprio modelo para a mesma semana. A normal sai da diferença das duas. A
# máxima e a mínima são as de cada 6 horas, na média da semana. Nomes medidos em 08/10/2026 (a
# documentação só traz os rótulos). São 8 variáveis: abaixo de 10, o custo por ponto é o de uma só.
VARIAVEIS_SEMANAIS = {
    "temperature_2m_mean": "temperatura",
    "temperature_2m_anomaly": "anom_temperatura",
    "temperature_max6h_2m_mean": "temp_max",
    "temperature_max6h_2m_anomaly": "anom_temp_max",
    "temperature_min6h_2m_mean": "temp_min",
    "temperature_min6h_2m_anomaly": "anom_temp_min",
    "precipitation_mean": "chuva",
    "precipitation_anomaly": "anom_chuva",
}
COLUNAS_SEMANAIS = ["modelo", "rodada_utc", "ponto", "latitude", "longitude", "semana",
                    *VARIAVEIS_SEMANAIS.values()]

# Como cada modelo aparece para quem lê
NOMES = {"ecmwf_ifs025": "ECMWF", "gfs_seamless": "GFS", "icon_seamless": "ICON",
         "ecmwf_ec46_ensemble_mean": "EC46"}

# De onde sai a hora da rodada de cada modelo. O Open-Meteo só publica os metadados das peças, e
# não das combinações "seamless". No Brasil, o GFS de superfície vem da grade de 0,11°
# (ncep_gfs013), e o ICON é só o global (dwd_icon), porque as grades finas dele são da Europa.
# A média dos membros do EC46 tem metadados próprios, mas com um intervalo de 744 h; os do EC46
# com os membros dizem a verdade (uma rodada por dia) e são da mesma rodada.
METADADOS = {"ecmwf_ifs025": "ecmwf_ifs025", "gfs_seamless": "ncep_gfs013", "icon_seamless": "dwd_icon",
             "ecmwf_ec46_ensemble_mean": "ecmwf_ec46"}
SAZONAIS = {"ecmwf_ec46_ensemble_mean"}   # os que moram na API sazonal, com outro endereço

# O Open-Meteo recomenda esperar 10 minutos depois de a rodada ficar disponível antes de usá-la
MARGEM_RODADA = timedelta(minutes=10)
ESPERA_ENTRE_LOTES = 61        # segundos: um minuto inteiro, para o limite por minuto zerar


class ErroOpenMeteo(Exception):
    """Falha ao consultar o Open-Meteo."""


def custo(pontos: int, variaveis: int = len(VARIAVEIS), modelos: int = len(config.MODELOS_PREVISAO),
          dias: int = config.DIAS_PREVISAO) -> float:
    """Quantas chamadas da cota um pedido gasta, pela regra medida em 05 e 06/10/2026."""
    return pontos * max(1.0, variaveis * modelos / 10) * max(1.0, dias / 14)


# =====================================================
# PONTOS
# =====================================================
@lru_cache(maxsize=8)
def _contorno(caminho_shape: str):
    return shapely.union_all(gpd.read_file(caminho_shape).to_crs("EPSG:4326").geometry.values)


def pontos_da_grade(recorte: config.Recorte = config.RECORTE,
                    passo: float = config.GRADE_PREVISAO) -> pd.DataFrame:
    """Os pontos da grade que cobrem o estado, com uma fileira além da divisa.

    A margem é igual ao espaçamento: sem nenhum ponto do lado de fora, a cor do mapa para no último
    ponto de dentro, que pode estar a meio passo da divisa, e a borda fica sem cor. Mais que uma
    fileira só gastaria cota, porque o mapa é recortado no contorno do estado. Em MS são 178 pontos
    a 0,5° e 607 a 0,25°.

    Os pontos ficam em múltiplos do espaçamento, que é onde o ECMWF e o GFS calculam: a 0,25°, o
    valor de cada ponto é o do próprio modelo, sem interpolação no meio.
    """
    contorno = _contorno(str(recorte.shape_uf))
    oeste, sul, leste, norte = contorno.bounds
    lons = np.arange(np.floor(oeste / passo) - 1, np.ceil(leste / passo) + 2) * passo
    lats = np.arange(np.floor(sul / passo) - 1, np.ceil(norte / passo) + 2) * passo
    x, y = (eixo.ravel() for eixo in np.meshgrid(lons, lats))
    dentro = shapely.contains_xy(contorno.buffer(passo), x, y)
    x, y = np.round(x[dentro], 4), np.round(y[dentro], 4)
    return pd.DataFrame({"ponto": [f"{lat:.2f};{lon:.2f}" for lat, lon in zip(y, x)],
                         "latitude": y, "longitude": x})


def pontos_das_estacoes(estacoes: pd.DataFrame) -> pd.DataFrame:
    """As estações como pontos de previsão, identificadas pelo código do INMET.

    É o que permite comparar, mais tarde, o previsto com o que a estação mediu no mesmo lugar.
    """
    return pd.DataFrame({"ponto": estacoes["CD_ESTACAO"].astype(str).to_numpy(),
                         "latitude": pd.to_numeric(estacoes["VL_LATITUDE"]).to_numpy(),
                         "longitude": pd.to_numeric(estacoes["VL_LONGITUDE"]).to_numpy()})


# =====================================================
# CONSULTAS
# =====================================================
def _motivo(resposta: requests.Response) -> str:
    try:
        return str(resposta.json().get("reason", "")) or resposta.text[:200]
    except ValueError:
        return resposta.text[:200]


def _pedir(metodo: str, url: str, **kwargs) -> dict | list:
    """Uma chamada ao Open-Meteo, repetida quando a falha é passageira.

    Conexão perdida, erro 5xx e resposta cortada valem nova tentativa, com a espera dobrando como
    no INMET. Cota esgotada (429) e pedido recusado (4xx) falham de primeira: repetir não resolve,
    e no caso da cota só gastaria mais.
    """
    motivo = ""
    for tentativa in range(1, config.TENTATIVAS + 1):
        try:
            resposta = requests.request(metodo, url, timeout=config.TIMEOUT_OPENMETEO, **kwargs)
        except requests.RequestException as erro:
            motivo = f"sem resposta ({erro.__class__.__name__})"
        else:
            if resposta.status_code == 429:
                raise ErroOpenMeteo(f"cota do Open-Meteo esgotada: {_motivo(resposta)}")
            if 400 <= resposta.status_code < 500:
                raise ErroOpenMeteo(f"pedido recusado (HTTP {resposta.status_code}): {_motivo(resposta)}")
            if resposta.status_code >= 500:
                motivo = f"HTTP {resposta.status_code}"
            else:
                try:
                    return resposta.json()
                except ValueError:
                    motivo = "resposta cortada"
        if tentativa < config.TENTATIVAS:
            time.sleep(config.PAUSA_ENTRE_TENTATIVAS * 2 ** (tentativa - 1))
    raise ErroOpenMeteo(f"{motivo} (depois de {config.TENTATIVAS} tentativas)")


def _dormir(segundos: float) -> None:
    """A espera entre lotes, separada para os testes poderem trocá-la."""
    time.sleep(segundos)


def rodadas(modelos: tuple[str, ...] = config.MODELOS_PREVISAO,
            agora: datetime | None = None) -> dict[str, datetime]:
    """A rodada que o Open-Meteo está servindo de cada modelo, em UTC.

    É a chave do que se guarda: a previsão só muda quando sai uma rodada nova, e não com o relógio.
    Os metadados não contam na cota.

    Enquanto a rodada mais nova não completa a margem de 10 minutos que o Open-Meteo recomenda, o
    que está servindo ainda é a anterior, um intervalo de atualização antes. O ECMWF publica de 6
    em 6 horas, e as rodadas das 06 e das 18 UTC vão só até alguns dias à frente; os dias seguintes
    seguem vindo da rodada anterior. A hora devolvida aqui é a da rodada mais nova.
    """
    agora = agora or datetime.now(timezone.utc)
    resultado = {}
    for modelo in modelos:
        endereco = config.URL_OPENMETEO_SAZONAL_RODADA if modelo in SAZONAIS else config.URL_OPENMETEO_RODADA
        meta = _pedir("GET", endereco.format(modelo=METADADOS[modelo]))
        inicio = datetime.fromtimestamp(meta["last_run_initialisation_time"], timezone.utc)
        pronta = datetime.fromtimestamp(meta["last_run_availability_time"], timezone.utc) + MARGEM_RODADA
        if agora < pronta:
            inicio -= timedelta(seconds=meta["update_interval_seconds"])
        resultado[modelo] = inicio
    return resultado


def _buscar_lote(lote: pd.DataFrame, modelos: tuple[str, ...], dias: int) -> pd.DataFrame:
    """Um pedido só, para os pontos do lote, com os modelos lado a lado."""
    resposta = _pedir("POST", config.URL_OPENMETEO, data={
        "latitude": ",".join(f"{valor:.4f}" for valor in lote["latitude"]),
        "longitude": ",".join(f"{valor:.4f}" for valor in lote["longitude"]),
        "hourly": ",".join(VARIAVEIS),
        "models": ",".join(modelos),
        "forecast_days": dias,
        "timezone": "GMT",
        "timeformat": "unixtime",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    })
    locais = resposta if isinstance(resposta, list) else [resposta]
    if len(locais) != len(lote):
        raise ErroOpenMeteo(f"o Open-Meteo devolveu {len(locais)} pontos para {len(lote)} pedidos")

    partes = []
    for ponto, local in zip(lote.itertuples(index=False), locais):
        horario = local["hourly"]
        horas = pd.to_datetime(horario["time"], unit="s", utc=True)
        for modelo in modelos:
            colunas = {}
            for nome, coluna in VARIAVEIS.items():
                bruto = horario.get(f"{nome}_{modelo}")
                if bruto is None and len(modelos) == 1:
                    bruto = horario.get(nome)   # com um modelo só, o nome vem sem o do modelo
                # None no meio da lista vira NaN: é a hora que o modelo não alcança
                colunas[coluna] = np.nan if bruto is None else np.asarray(bruto, dtype=float)
            tabela = pd.DataFrame({"modelo": modelo, "ponto": ponto.ponto, "latitude": ponto.latitude,
                                   "longitude": ponto.longitude, "hora_prevista_utc": horas, **colunas})
            # Horas sem nenhuma variável são as que o modelo não alcança: o ICON para no dia 7,5
            partes.append(tabela.dropna(subset=list(VARIAVEIS.values()), how="all"))
    return pd.concat(partes, ignore_index=True)


def previsao_horaria(pontos: pd.DataFrame, modelos: tuple[str, ...] = config.MODELOS_PREVISAO,
                     dias: int = config.DIAS_PREVISAO) -> pd.DataFrame:
    """A previsão hora a hora dos pontos, uma linha por modelo, ponto e hora, sem a rodada.

    Os pontos vão em lotes que cabem no limite por minuto, com um minuto inteiro entre um lote e
    outro. A 0,5°, os 178 pontos de MS cabem num lote só; a 0,25°, os 607 viram três, e a busca
    leva uns dois minutos, o que só serve para a busca agendada.
    """
    por_lote = max(1, int(config.OPENMETEO_POR_MINUTO // custo(1, modelos=len(modelos), dias=dias)))
    partes = []
    for inicio in range(0, len(pontos), por_lote):
        if inicio:
            _dormir(ESPERA_ENTRE_LOTES)
        partes.append(_buscar_lote(pontos.iloc[inicio:inicio + por_lote], modelos, dias))
    if not partes:
        return pd.DataFrame(columns=[coluna for coluna in COLUNAS_HORARIAS if coluna != "rodada_utc"])
    return pd.concat(partes, ignore_index=True)


def _na_mesma_rodada(modelos: tuple[str, ...], buscar_tabela: Callable[[], pd.DataFrame],
                     colunas: list[str]) -> pd.DataFrame:
    """A tabela que `buscar_tabela` trouxer, com a rodada de cada modelo carimbada em cada linha.

    A rodada é conferida antes e depois da busca. Se ela mudou no meio, parte dos números pode ser
    da rodada nova e parte da velha, e carimbar uma das duas seria mentir: busca de novo. Se mudar
    outra vez, desiste com erro, e quem chamou tenta mais tarde.
    """
    for _ in range(2):
        antes = rodadas(modelos)
        tabela = buscar_tabela()
        if rodadas(modelos) == antes:
            tabela.insert(1, "rodada_utc", tabela["modelo"].map(antes))
            return tabela[colunas]
    raise ErroOpenMeteo("a rodada dos modelos mudou durante a busca, duas vezes seguidas; "
                        "tente de novo em alguns minutos")


def buscar(pontos: pd.DataFrame, modelos: tuple[str, ...] = config.MODELOS_PREVISAO,
           dias: int = config.DIAS_PREVISAO) -> pd.DataFrame:
    """A previsão hora a hora dos pontos, com a rodada de cada modelo carimbada em cada linha."""
    return _na_mesma_rodada(modelos, lambda: previsao_horaria(pontos, modelos, dias), COLUNAS_HORARIAS)


# =====================================================
# AS SEMANAS
# =====================================================
def _buscar_lote_semanal(lote: pd.DataFrame, modelo: str, dias: int) -> pd.DataFrame:
    """Um pedido à API sazonal, para os pontos do lote: uma linha por ponto e semana."""
    resposta = _pedir("POST", config.URL_OPENMETEO_SAZONAL, data={
        "latitude": ",".join(f"{valor:.4f}" for valor in lote["latitude"]),
        "longitude": ",".join(f"{valor:.4f}" for valor in lote["longitude"]),
        "weekly": ",".join(VARIAVEIS_SEMANAIS),
        "models": modelo,
        "forecast_days": dias,
        "timezone": "GMT",
    })
    locais = resposta if isinstance(resposta, list) else [resposta]
    if len(locais) != len(lote):
        raise ErroOpenMeteo(f"o Open-Meteo devolveu {len(locais)} pontos para {len(lote)} pedidos")

    partes = []
    for ponto, local in zip(lote.itertuples(index=False), locais):
        semanal = local["weekly"]
        colunas = {coluna: np.asarray([np.nan if valor is None else valor for valor in
                                       semanal.get(nome) or [None] * len(semanal["time"])], dtype=float)
                   for nome, coluna in VARIAVEIS_SEMANAIS.items()}
        tabela = pd.DataFrame({"modelo": modelo, "ponto": ponto.ponto, "latitude": ponto.latitude,
                               "longitude": ponto.longitude,
                               "semana": pd.to_datetime(semanal["time"]).date, **colunas})
        # A semana que o EC46 não fecha (a última, que passa do dia 46) vem toda vazia
        partes.append(tabela.dropna(subset=list(VARIAVEIS_SEMANAIS.values()), how="all"))
    return pd.concat(partes, ignore_index=True)


def anomalias_semanais(pontos: pd.DataFrame, modelo: str = config.MODELO_SEMANAS,
                       dias: int = config.DIAS_SEMANAS) -> pd.DataFrame:
    """As anomalias semanais dos pontos, sem a rodada, em lotes que cabem no limite por minuto.

    A semana começa na segunda-feira, em UTC, e o valor vem pronto do Open-Meteo: a média da semana
    (ou a soma, na chuva) menos a normal do modelo para a mesma semana, tirada das reprevisões do
    ECMWF. Cada ponto custa 3,3 chamadas pela regra (46 dias pesam 46/14), e os 178 da grade de
    0,5° cabem num lote.
    """
    por_lote = max(1, int(config.OPENMETEO_POR_MINUTO
                          // custo(1, variaveis=len(VARIAVEIS_SEMANAIS), modelos=1, dias=dias)))
    partes = []
    for inicio in range(0, len(pontos), por_lote):
        if inicio:
            _dormir(ESPERA_ENTRE_LOTES)
        partes.append(_buscar_lote_semanal(pontos.iloc[inicio:inicio + por_lote], modelo, dias))
    if not partes:
        return pd.DataFrame(columns=[coluna for coluna in COLUNAS_SEMANAIS if coluna != "rodada_utc"])
    return pd.concat(partes, ignore_index=True)


def buscar_semanas(pontos: pd.DataFrame, modelo: str = config.MODELO_SEMANAS,
                   dias: int = config.DIAS_SEMANAS) -> pd.DataFrame:
    """As anomalias semanais dos pontos, com a rodada do EC46 carimbada em cada linha."""
    return _na_mesma_rodada((modelo,), lambda: anomalias_semanais(pontos, modelo, dias), COLUNAS_SEMANAIS)


# =====================================================
# O DIA
# =====================================================
def diario(horaria: pd.DataFrame, fuso: ZoneInfo = config.FUSO_MS) -> pd.DataFrame:
    """O dia de cada modelo, rodada e ponto, tirado das horas pela regra do projeto.

    A leitura das 00:00 fecha o dia anterior, como no INMET: o dia 17 vai da hora das 01:00 de 17
    à das 00:00 de 18, no fuso do estado. Somando as horas aqui, e não pedindo o diário pronto do
    Open-Meteo (que vai das 00 às 23 h), o previsto e o observado ficam comparáveis.

    Dias que não estão inteiros na previsão (o de hoje, que começou antes da rodada, e o último)
    saem com `horas` menor que 24, para quem usa decidir. A direção dominante é a resultante das
    horas, pesada pela velocidade, a mesma regra do painel (`app/variaveis._direcao_resultante`):
    a média dos ângulos daria rumos que não sopraram.
    """
    local = horaria["hora_prevista_utc"].dt.tz_convert(fuso)
    angulos = np.radians(horaria["direcao"])
    tabela = horaria.assign(dia_previsto=(local - pd.Timedelta(hours=1)).dt.date,
                            _leste=horaria["vento"] * np.sin(angulos),
                            _norte=horaria["vento"] * np.cos(angulos))
    chaves = [coluna for coluna in ("modelo", "rodada_utc", "ponto", "latitude", "longitude")
              if coluna in tabela] + ["dia_previsto"]
    grupos = tabela.groupby(chaves, sort=True)

    dias = grupos.agg(temp_max=("temperatura", "max"), temp_min=("temperatura", "min"),
                      temp_media=("temperatura", "mean"), umid_min=("umidade", "min"),
                      orvalho_medio=("orvalho", "mean"), vento_max=("vento", "max"),
                      rajada_max=("rajada", "max"), horas=("hora_prevista_utc", "size"))
    # Somas com min_count=1: um dia sem nenhuma hora de chuva é "sem dado", e não 0 mm
    somas = grupos[["chuva", "_leste", "_norte", "vento"]].sum(min_count=1)
    dias.insert(5, "chuva", somas["chuva"])

    graus = np.degrees(np.arctan2(somas["_leste"], somas["_norte"])) % 360
    graus = graus.mask(graus >= 360.0, 0.0)   # 360° é o mesmo norte que 0°
    # Ventos opostos que se cancelam não deixam rumo: o que sobra tem de ser desprezível perto do
    # quanto ventou, como no painel
    sem_rumo = np.hypot(somas["_leste"], somas["_norte"]) < 1e-9 * somas["vento"].clip(lower=1.0)
    dias.insert(len(dias.columns) - 1, "direcao_dominante", graus.mask(sem_rumo))
    return dias.reset_index()
