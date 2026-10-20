"""Cálculos compartilhados pelos produtos: recorte no tempo, extremos, acumulados e interpolação IDW."""
from datetime import datetime

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

from . import config


def recortar(dados: pd.DataFrame, inicio: datetime, fim: datetime) -> pd.DataFrame:
    return dados[(dados["dt_utc"] > inicio) & (dados["dt_utc"] <= fim)]


def acumulado_chuva(dados: pd.DataFrame, inicio: datetime, fim: datetime) -> float:
    # min_count=1: a hora sem leitura não soma nada, mas a janela sem nenhuma leitura de chuva
    # (a estação que só mandou bateria) fica sem dado (NaN), e não com 0 mm
    return round(float(recortar(dados, inicio, fim)["CHUVA"].sum(min_count=1)), 1)


def indice_extremo(dados: pd.DataFrame, coluna: str, minimo: bool):
    if coluna not in dados or dados[coluna].isna().all():
        return None
    return dados[coluna].idxmin() if minimo else dados[coluna].idxmax()


def data_hora(dados: pd.DataFrame, indice, uf: str = config.UF) -> dict:
    return {
        "Data/Hora (UTC)": dados.at[indice, "dt_utc"].strftime("%d/%m/%Y %H:%M"),
        f"Data/Hora ({uf})": dados.at[indice, "dt_local"].strftime("%d/%m/%Y %H:%M"),
    }


def criar_grade(limites: tuple[float, float, float, float], resolucao: int) -> tuple[np.ndarray, np.ndarray]:
    lon_min, lon_max, lat_min, lat_max = limites
    return np.meshgrid(np.linspace(lon_min, lon_max, resolucao), np.linspace(lat_min, lat_max, resolucao))


def interpolar_idw(lons, lats, valores, lon_grade, lat_grade, vizinhos: int = 8, potencia: float = 2) -> np.ndarray:
    centro = float(np.mean(lon_grade)), float(np.mean(lat_grade))
    pontos = _em_km(lons, lats, *centro)
    k = min(vizinhos, len(pontos))
    distancias, indices = cKDTree(pontos).query(_em_km(lon_grade, lat_grade, *centro), k=k)
    if k == 1:
        distancias, indices = distancias[:, None], indices[:, None]

    pesos = 1.0 / np.maximum(distancias, 1e-10) ** potencia
    interpolados = np.sum(np.asarray(valores)[indices] * pesos, axis=1) / np.sum(pesos, axis=1)
    return interpolados.reshape(np.shape(lon_grade))


def mais_proxima(lons, lats, lon_grade, lat_grade,
                 centro: tuple[float, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
    # A estação mais perto de cada ponto da grade e a distância até ela, em km, na projeção do IDW
    centro = centro or (float(np.mean(lon_grade)), float(np.mean(lat_grade)))
    distancias, indices = cKDTree(_em_km(lons, lats, *centro)).query(_em_km(lon_grade, lat_grade, *centro))
    return indices.reshape(np.shape(lon_grade)), distancias.reshape(np.shape(lon_grade))


def idw_no_tempo(lons, lats, valores, lon: float, lat: float, centro: tuple[float, float],
                 vizinhos: int = 8, potencia: float = 2) -> np.ndarray:
    # O IDW de um ponto só em cada linha de `valores` (tempos × estações, NaN onde a estação não
    # mediu): a cada tempo entram as `vizinhos` mais próximas entre as que mediram, como no
    # interpolar_idw. Com o `centro` da grade do mapa, o número é o mesmo da superfície
    pontos = _em_km(lons, lats, *centro)
    distancias = np.hypot(*(pontos - _em_km([lon], [lat], *centro)[0]).T)
    ordem = np.argsort(distancias, kind="stable")
    medidas = np.atleast_2d(np.asarray(valores, dtype=float))[:, ordem]
    mediram = ~np.isnan(medidas)
    entram = mediram & (np.cumsum(mediram, axis=1) <= vizinhos)
    pesos = np.where(entram, 1.0 / np.maximum(distancias[ordem], 1e-10) ** potencia, 0.0)
    soma = pesos.sum(axis=1)
    ponderada = np.sum(np.where(entram, medidas, 0.0) * pesos, axis=1)
    return np.divide(ponderada, soma, out=np.full(len(soma), np.nan), where=soma > 0)


def apoio_que_entra(lons_apoio, lats_apoio, lons_proprias, lats_proprias,
                    lon_grade, lat_grade, vizinhos: int) -> np.ndarray:
    apoio = np.column_stack([np.ravel(lons_apoio), np.ravel(lats_apoio)])
    if len(apoio) == 0:
        return np.zeros(0, dtype=bool)

    centro = float(np.mean(lon_grade)), float(np.mean(lat_grade))
    proprias = _em_km(lons_proprias, lats_proprias, *centro)
    todas = np.vstack([proprias, _em_km(apoio[:, 0], apoio[:, 1], *centro)])
    _, indices = cKDTree(todas).query(_em_km(lon_grade, lat_grade, *centro),
                                      k=min(vizinhos, len(todas)))

    alcancadas = np.unique(indices)
    mascara = np.zeros(len(apoio), dtype=bool)
    mascara[alcancadas[alcancadas >= len(proprias)] - len(proprias)] = True
    return mascara


def _em_km(lons, lats, lon_centro: float, lat_centro: float) -> np.ndarray:
    projecao = f"+proj=aeqd +lat_0={lat_centro} +lon_0={lon_centro} +datum=WGS84 +units=km"
    transformador = Transformer.from_crs("EPSG:4326", projecao, always_xy=True)
    # Listas (e não arrays) fazem o pyproj usar sempre o cálculo vetorizado, mesmo com um único ponto
    x, y = transformador.transform(np.ravel(np.asarray(lons, dtype=float)).tolist(),
                                   np.ravel(np.asarray(lats, dtype=float)).tolist())
    return np.column_stack([x, y])
