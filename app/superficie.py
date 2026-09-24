"""Superfície interpolada como imagem, para o mapa navegável do explorador.

O meio é outro — aqui a superfície é uma camada sobre um mapa base, que a pessoa aproxima e
arrasta —, mas a conta é a mesma dos produtos: o IDW de `modulos/calculos.py`, com os mesmos
vizinhos e a mesma potência. O que muda é o desenho, não a ciência.

Só cálculo, sem tela, para poder ser testado.
"""
import base64
import io
from dataclasses import dataclass

import numpy as np
import pandas as pd
import shapely
from matplotlib import colormaps
from PIL import Image

from modulos import calculos, config

# Pontos por eixo. A grade dos produtos (100) basta para um PNG de página inteira, mas num mapa
# que se aproxima ela aparece em degraus: cada célula cobriria uns 7 km.
RESOLUCAO = 400
OPACIDADE = 0.72  # deixa as cidades e os rios do mapa base aparecerem por baixo


@dataclass(frozen=True)
class Malha:
    """Grade fina e a máscara do estado.

    Dependem só da resolução, não do dado: valem para qualquer variável e qualquer hora, e por
    isso são calculadas uma vez só. A máscara é a parte cara — daí guardá-la.
    """

    lon: np.ndarray
    lat: np.ndarray
    dentro: np.ndarray


def malha(estado, resolucao: int = RESOLUCAO) -> Malha:
    """Grade regular sobre o enquadramento do estado, com a máscara de quem cai dentro dele.

    `estado` é a geometria do contorno (o `uf` da base cartográfica, unido). O shapely resolve
    160 mil pontos em centésimos de segundo; o mesmo teste pelo caminho do matplotlib leva
    quase três segundos.
    """
    lon, lat = calculos.criar_grade(
        (config.LON_MIN, config.LON_MAX, config.LAT_MIN, config.LAT_MAX), resolucao)
    return Malha(lon, lat, shapely.contains_xy(estado, lon, lat))


def limites() -> list[float]:
    """Enquadramento da imagem como o BitmapLayer espera: oeste, sul, leste, norte."""
    return [config.LON_MIN, config.LAT_MIN, config.LON_MAX, config.LAT_MAX]


def superficie_png(pontos: pd.DataFrame, coluna: str, grade_fina: Malha, paleta: str,
                   opacidade: float = OPACIDADE) -> bytes:
    """PNG da superfície IDW, transparente fora de Mato Grosso do Sul."""
    grade = calculos.interpolar_idw(pontos["Longitude"], pontos["Latitude"], pontos[coluna],
                                    grade_fina.lon, grade_fina.lat,
                                    config.IDW_VIZINHOS, config.IDW_POTENCIA)
    cores = colormaps[paleta](_normalizar(grade, pontos[coluna]), alpha=opacidade, bytes=True)
    cores[~grade_fina.dentro] = 0  # transparente fora do estado

    # A primeira linha da imagem é o norte; a grade começa no sul
    return _png(Image.fromarray(np.flipud(cores), mode="RGBA"))


def como_uri(png: bytes) -> str:
    """O PNG embutido no próprio endereço: o deck.gl recebe a imagem junto com a camada."""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _normalizar(grade: np.ndarray, valores: pd.Series) -> np.ndarray:
    """Leva a superfície para 0–1 pela faixa medida nas estações.

    É a mesma faixa que a tela escreve embaixo do mapa. O IDW é uma média ponderada, então nunca
    sai dela. Quando todas as estações marcam o mesmo (chuva zero no estado inteiro, por
    exemplo), tudo vai para o pé da escala: não há variação para mostrar.
    """
    menor, maior = float(valores.min()), float(valores.max())
    if maior == menor:
        return np.zeros_like(grade, dtype=float)
    return np.clip((grade - menor) / (maior - menor), 0, 1)


def _png(imagem: Image.Image) -> bytes:
    arquivo = io.BytesIO()
    imagem.save(arquivo, format="PNG")
    return arquivo.getvalue()
