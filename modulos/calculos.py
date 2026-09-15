"""Cálculos compartilhados pelos produtos: recorte no tempo, extremos, acumulados e interpolação IDW."""
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


def recortar(dados: pd.DataFrame, inicio: datetime, fim: datetime) -> pd.DataFrame:
    """Registros com início <= horário (UTC) < fim."""
    return dados[(dados["dt_utc"] >= inicio) & (dados["dt_utc"] < fim)]


def acumulado_chuva(dados: pd.DataFrame, inicio: datetime, fim: datetime) -> float:
    """Soma da chuva (mm) no intervalo [início, fim)."""
    return round(float(recortar(dados, inicio, fim)["CHUVA"].fillna(0).sum()), 1)


def indice_extremo(dados: pd.DataFrame, coluna: str, minimo: bool):
    """Índice do registro com o menor (ou maior) valor da coluna, ou None se não houver dado válido."""
    if coluna not in dados or dados[coluna].isna().all():
        return None
    return dados[coluna].idxmin() if minimo else dados[coluna].idxmax()


def data_hora(dados: pd.DataFrame, indice) -> dict:
    """Data e hora de um registro, em UTC e no horário de MS, prontas para a planilha."""
    return {
        "Data/Hora (UTC)": dados.at[indice, "dt_utc"].strftime("%d/%m/%Y %H:%M"),
        "Data/Hora (MS)": dados.at[indice, "dt_local"].strftime("%d/%m/%Y %H:%M"),
    }


def criar_grade(limites: tuple[float, float, float, float], resolucao: int) -> tuple[np.ndarray, np.ndarray]:
    """Grade regular (lon, lat) sobre os limites (lon_min, lon_max, lat_min, lat_max)."""
    lon_min, lon_max, lat_min, lat_max = limites
    return np.meshgrid(np.linspace(lon_min, lon_max, resolucao), np.linspace(lat_min, lat_max, resolucao))


def interpolar_idw(lons, lats, valores, lon_grade, lat_grade, vizinhos: int = 8, potencia: float = 2) -> np.ndarray:
    """Interpolação pelo inverso da distância (IDW) usando os vizinhos mais próximos.

    A distância é calculada diretamente em graus de latitude/longitude.
    """
    pontos = np.column_stack([lons, lats])
    k = min(vizinhos, len(pontos))
    distancias, indices = cKDTree(pontos).query(np.column_stack([lon_grade.ravel(), lat_grade.ravel()]), k=k)
    if k == 1:
        distancias, indices = distancias[:, None], indices[:, None]

    pesos = 1.0 / np.maximum(distancias, 1e-10) ** potencia
    interpolados = np.sum(np.asarray(valores)[indices] * pesos, axis=1) / np.sum(pesos, axis=1)
    return interpolados.reshape(lon_grade.shape)
