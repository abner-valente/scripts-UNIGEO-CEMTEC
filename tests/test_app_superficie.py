"""Superfície interpolada como imagem: recorte, orientação e faixa de cores."""
import io

import numpy as np
import pandas as pd
import pytest
import shapely
from PIL import Image

from app import superficie
from modulos import config

# Um "estado" retangular no meio do enquadramento: os testes não precisam do shapefile real, só
# de uma geometria que tenha dentro e fora.
RETANGULO = shapely.box(-56.0, -22.0, -53.0, -19.0)

ESTACOES = pd.DataFrame({
    "Estação": ["Norte", "Sul", "Leste", "Oeste", "Centro"],
    "Latitude": [-19.5, -21.5, -20.5, -20.5, -20.5],
    "Longitude": [-54.5, -54.5, -53.5, -55.5, -54.5],
    "Temperatura": [30.0, 20.0, 28.0, 22.0, 25.0],
})


@pytest.fixture(scope="module")
def grade():
    return superficie.malha(RETANGULO, resolucao=60)


def imagem(png: bytes) -> np.ndarray:
    return np.array(Image.open(io.BytesIO(png)))


def test_a_malha_marca_quem_cai_dentro_do_estado(grade):
    assert grade.dentro.shape == (60, 60)
    assert 0 < grade.dentro.sum() < grade.dentro.size  # tem dentro e tem fora
    dentro = shapely.contains_xy(RETANGULO, grade.lon, grade.lat)
    np.testing.assert_array_equal(grade.dentro, dentro)


def test_fora_do_estado_a_imagem_e_transparente(grade):
    png = imagem(superficie.superficie_png(ESTACOES, "Temperatura", grade, "RdYlBu_r"))

    alfa = png[:, :, 3]
    dentro = np.flipud(grade.dentro)  # a imagem começa pelo norte
    assert (alfa[~dentro] == 0).all()
    assert (alfa[dentro] > 0).all()


def test_o_norte_fica_no_alto_da_imagem(grade):
    """Trocar a orientação deixaria o mapa de cabeça para baixo sobre o mapa base."""
    png = imagem(superficie.superficie_png(ESTACOES, "Temperatura", grade, "RdYlBu_r"))

    dentro = np.flipud(grade.dentro)
    linhas_com_dado = np.flatnonzero(dentro.any(axis=1))
    vermelho = png[:, :, 0].astype(int)
    alto = vermelho[linhas_com_dado[0]][dentro[linhas_com_dado[0]]].mean()
    baixo = vermelho[linhas_com_dado[-1]][dentro[linhas_com_dado[-1]]].mean()
    assert alto > baixo  # a estação quente (30 °C) está ao norte, e a paleta esquenta no vermelho


def test_a_escala_vai_do_menor_ao_maior_valor_medido():
    grade_menor = superficie.malha(RETANGULO, resolucao=30)
    quente = superficie.superficie_png(ESTACOES, "Temperatura", grade_menor, "RdYlBu_r")
    # As mesmas medidas 10 graus acima: o desenho é o mesmo, porque a escala acompanha o dado
    deslocado = ESTACOES.assign(Temperatura=ESTACOES["Temperatura"] + 10)
    assert superficie.superficie_png(deslocado, "Temperatura", grade_menor, "RdYlBu_r") == quente


def test_estado_todo_com_o_mesmo_valor_nao_quebra(grade):
    """Chuva zero no estado inteiro é o caso comum: sem variação, tudo vai para o pé da escala."""
    parado = ESTACOES.assign(Temperatura=0.0)
    png = imagem(superficie.superficie_png(parado, "Temperatura", grade, "RdYlBu_r"))
    dentro = np.flipud(grade.dentro)
    assert len(np.unique(png[:, :, :3][dentro], axis=0)) == 1  # uma cor só


def test_os_limites_seguem_o_enquadramento_dos_mapas():
    assert superficie.limites() == [config.LON_MIN, config.LAT_MIN, config.LON_MAX, config.LAT_MAX]


def test_o_uri_carrega_o_png():
    uri = superficie.como_uri(b"\x89PNG\r\n\x1a\n")
    assert uri.startswith("data:image/png;base64,")
