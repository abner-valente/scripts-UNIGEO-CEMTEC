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
    oeste, leste, sul, norte = config.RECORTE.limites
    assert superficie.limites() == [oeste, sul, leste, norte]


def test_o_uri_carrega_o_png():
    uri = superficie.como_uri(b"\x89PNG\r\n\x1a\n")
    assert uri.startswith("data:image/png;base64,")


# =====================================================
# ESCALA FIXA
# =====================================================
def test_escala_fixa_da_a_mesma_cor_ao_mesmo_valor():
    """É o ponto da escala fixa: 25 °C tem a mesma cor num dia frio e num dia quente."""
    faixa = np.linspace(0, 45, 21)
    frio = np.array([[10.0, 25.0, 12.0]])
    quente = np.array([[40.0, 25.0, 41.0]])

    assert superficie._pela_escala(frio, faixa)[0][0, 1] == superficie._pela_escala(quente, faixa)[0][0, 1]


def test_sem_escala_fixa_a_mesma_temperatura_muda_de_cor():
    """O problema que a escala fixa resolve: a cor dependia do resto do mapa."""
    frio = pd.Series([10.0, 25.0, 12.0])
    quente = pd.Series([40.0, 25.0, 41.0])

    assert (superficie._normalizar(np.array([25.0]), frio)[0]
            != superficie._normalizar(np.array([25.0]), quente)[0])


def test_classes_agrupam_valores_da_mesma_faixa():
    """Dentro de uma classe a cor é uma só: 12 mm e 18 mm caem na mesma faixa de 10 a 20."""
    classes = np.array([0.2, 1, 5, 10, 20])
    dentro_da_faixa = superficie._pela_escala(np.array([[12.0, 18.0, 25.0]]), classes)[0]

    assert dentro_da_faixa[0, 0] == dentro_da_faixa[0, 1]
    assert dentro_da_faixa[0, 2] != dentro_da_faixa[0, 1]


def test_abaixo_da_primeira_classe_de_chuva_nao_se_pinta(grade):
    """Pintar 0 mm de azul claro inventaria chuva que não houve."""
    classes = [0.2, 1, 5, 10, 20]
    seco = ESTACOES.assign(Chuva=[0.0, 0.0, 0.0, 0.0, 0.0])

    png = imagem(superficie.superficie_png(seco, "Chuva", grade, "Blues", classes))

    assert (png[:, :, 3] == 0).all()  # imagem inteira transparente


def test_valor_acima_do_teto_fica_na_cor_do_extremo(grade):
    """Numa escala fixa, o que passa do teto não pode sumir do mapa."""
    faixa = np.linspace(0, 45, 21)
    quente = ESTACOES.assign(Temperatura=[60.0] * 5)  # acima do teto da escala

    png = imagem(superficie.superficie_png(quente, "Temperatura", grade, "RdYlBu_r", faixa))

    dentro = np.flipud(grade.dentro)
    assert (png[:, :, 3][dentro] > 0).all()
