"""Cálculos compartilhados pelos produtos: recorte no tempo, extremos, acumulados e interpolação IDW."""
from datetime import datetime

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree


def recortar(dados: pd.DataFrame, inicio: datetime, fim: datetime) -> pd.DataFrame:
    """Leituras da janela (início, fim].

    Cada leitura do INMET se refere à hora que termina no horário indicado: a das 05 UTC cobre
    das 04 às 05 UTC. Por isso entra a leitura do fim da janela e não a do início.
    """
    return dados[(dados["dt_utc"] > inicio) & (dados["dt_utc"] <= fim)]


def acumulado_chuva(dados: pd.DataFrame, inicio: datetime, fim: datetime) -> float:
    """Soma da chuva (mm) na janela (início, fim]."""
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

    As distâncias são medidas em quilômetros, numa projeção equidistante centrada na grade.
    """
    centro = float(np.mean(lon_grade)), float(np.mean(lat_grade))
    pontos = _em_km(lons, lats, *centro)
    k = min(vizinhos, len(pontos))
    distancias, indices = cKDTree(pontos).query(_em_km(lon_grade, lat_grade, *centro), k=k)
    if k == 1:
        distancias, indices = distancias[:, None], indices[:, None]

    pesos = 1.0 / np.maximum(distancias, 1e-10) ** potencia
    interpolados = np.sum(np.asarray(valores)[indices] * pesos, axis=1) / np.sum(pesos, axis=1)
    return interpolados.reshape(np.shape(lon_grade))


def apoio_que_entra(lons_apoio, lats_apoio, lons_proprias, lats_proprias,
                    lon_grade, lat_grade, vizinhos: int) -> np.ndarray:
    """Quais estações de apoio chegam às `vizinhos` mais próximas de alguma célula da grade.

    O IDW olha só as 8 mais próximas de cada célula: uma estação de fora do estado pode estar
    dentro da margem do recorte e ainda assim não ser vizinha de célula nenhuma. Baixar os dados
    dela é requisição jogada fora, e isso se decide **antes de baixar qualquer coisa** — só
    depende de coordenadas, e a lista de estações é uma consulta só.

    `vizinhos` é maior que os 8 que o IDW usa, de propósito. Numa hora em que faltam estações, as
    8 mais próximas de uma célula passam a ser outras, e uma que nunca entrava passa a entrar. A
    folga é o que faz esta poda cortar requisição sem mudar mapa nenhum.

    A grade deve trazer só as células que viram desenho: as de fora do contorno são calculadas e
    descartadas, e deixá-las aqui manteria estações que ninguém vê.

    Devolve uma máscara booleana sobre as estações de apoio, na ordem em que vieram.
    """
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
    """Converte lon/lat (graus) em x/y (km) numa projeção azimutal equidistante centrada em (lon, lat).

    Na extensão de MS, as distâncias nessa projeção ficam praticamente exatas (erro abaixo de 0,2%).
    """
    projecao = f"+proj=aeqd +lat_0={lat_centro} +lon_0={lon_centro} +datum=WGS84 +units=km"
    transformador = Transformer.from_crs("EPSG:4326", projecao, always_xy=True)
    # Listas (e não arrays) fazem o pyproj usar sempre o cálculo vetorizado, mesmo com um único ponto
    x, y = transformador.transform(np.ravel(np.asarray(lons, dtype=float)).tolist(),
                                   np.ravel(np.asarray(lats, dtype=float)).tolist())
    return np.column_stack([x, y])
