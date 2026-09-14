"""Cálculos meteorológicos: recorte temporal, extremos, acumulados e interpolação IDW."""
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .config import Periodo


def recortar(dados: pd.DataFrame, inicio: datetime, fim: datetime) -> pd.DataFrame:
    """Registros com início <= horário (UTC) < fim."""
    return dados[(dados["dt_utc"] >= inicio) & (dados["dt_utc"] < fim)]


def acumulado_chuva(dados: pd.DataFrame, inicio: datetime, fim: datetime) -> float:
    """Soma da chuva (mm) no intervalo [início, fim)."""
    return round(float(recortar(dados, inicio, fim)["CHUVA"].fillna(0).sum()), 1)


def _indice_extremo(dados: pd.DataFrame, coluna: str, minimo: bool):
    """Índice do registro com o menor (ou maior) valor da coluna, ou None se não houver dado válido."""
    if coluna not in dados or dados[coluna].isna().all():
        return None
    return dados[coluna].idxmin() if minimo else dados[coluna].idxmax()


def _data_hora(dados: pd.DataFrame, indice) -> dict:
    return {
        "Data/Hora (UTC)": dados.at[indice, "dt_utc"].strftime("%d/%m/%Y %H:%M"),
        "Data/Hora (MS)": dados.at[indice, "dt_local"].strftime("%d/%m/%Y %H:%M"),
    }


def resumir_estacao(dados: pd.DataFrame, estacao: pd.Series, periodo: Periodo) -> dict[str, dict]:
    """Extremos e acumulados de uma estação no período.

    Retorna {aba do Excel: linha da tabela}. Variáveis sem nenhum dado válido ficam de fora.
    """
    nome = estacao["Estação"]
    coordenadas = {"Latitude": estacao["VL_LATITUDE"], "Longitude": estacao["VL_LONGITUDE"]}
    dados_tmin = recortar(dados, *periodo.janela_temp_min)
    dados_periodo = recortar(dados, *periodo.janela_extremos)
    linhas = {}

    indice = _indice_extremo(dados_tmin, "TEM_MIN", minimo=True)
    if indice is not None:
        linhas["Temp_Min"] = {
            "Estação": nome,
            "Temperatura Mínima (°C)": dados_tmin.at[indice, "TEM_MIN"],
            **_data_hora(dados_tmin, indice),
            **coordenadas,
        }

    indice = _indice_extremo(dados_periodo, "TEM_MAX", minimo=False)
    if indice is not None:
        linhas["Temp_Max"] = {
            "Estação": nome,
            "Temperatura Máxima (°C)": dados_periodo.at[indice, "TEM_MAX"],
            **_data_hora(dados_periodo, indice),
            **coordenadas,
        }

    indice = _indice_extremo(dados_periodo, "UMD_MIN", minimo=True)
    if indice is not None:
        linhas["Umidade"] = {
            "Estação": nome,
            "Umidade Mín (%)": dados_periodo.at[indice, "UMD_MIN"],
            **coordenadas,
        }

    indice = _indice_extremo(dados_periodo, "VEN_RAJ", minimo=False)
    if indice is not None:
        # Direção registrada no mesmo horário da rajada máxima
        direcao = dados_periodo.at[indice, "VEN_DIR"] if "VEN_DIR" in dados_periodo else np.nan
        linhas["Vento"] = {
            "Estação": nome,
            "Rajada (km/h)": round(dados_periodo.at[indice, "VEN_RAJ"] * 3.6, 1),  # m/s -> km/h
            "Direção (°)": direcao,
            **coordenadas,
        }

    linhas["Chuva"] = {
        "Estação": nome,
        **{coluna: acumulado_chuva(dados, inicio, fim) for coluna, (inicio, fim) in periodo.janelas_chuva.items()},
        **coordenadas,
    }
    return linhas


def montar_tabelas(resumos: list[dict[str, dict]], periodo: Periodo) -> dict[str, pd.DataFrame]:
    """Junta os resumos das estações em uma tabela ordenada para cada aba do Excel."""
    ordenacao = {  # aba: (coluna de ordenação, crescente?)
        "Temp_Min": ("Temperatura Mínima (°C)", True),
        "Temp_Max": ("Temperatura Máxima (°C)", False),
        "Umidade": ("Umidade Mín (%)", True),
        "Vento": ("Rajada (km/h)", False),
        "Chuva": (periodo.coluna_chuva_principal, False),
    }
    tabelas = {}
    for aba, (coluna, crescente) in ordenacao.items():
        linhas = [resumo[aba] for resumo in resumos if aba in resumo]
        if linhas:
            tabelas[aba] = pd.DataFrame(linhas).sort_values(coluna, ascending=crescente, ignore_index=True)
    return tabelas


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
