"""GeoTIFF dos mapas: a grade do padrão do PIN MS, as contas da tela e as tags que o GDAL lê."""
import io

import numpy as np
import pytest
import shapely
from PIL import Image

from modulos import calculos, config, geotiff

# Um "estado" retangular com as bordas fora do passo de 0,01°: a grade tem de arredondar para fora
ESTADO = shapely.box(-56.004, -22.003, -53.996, -19.998)
RECORTE = config.RECORTE
LONS = np.array([-55.5, -54.5, -54.0, -55.0, -54.2])
LATS = np.array([-20.5, -21.5, -20.2, -19.5, -21.0])
VALORES = np.array([30.0, 20.0, 28.0, 22.0, 25.0])


@pytest.fixture(scope="module")
def grade():
    return geotiff.grade_do_estado(ESTADO, RECORTE)


def _abrir(arquivo: bytes) -> Image.Image:
    imagem = Image.open(io.BytesIO(arquivo))
    imagem.load()
    return imagem


def test_a_grade_cobre_o_estado_com_bordas_no_passo(grade):
    assert (grade.oeste, grade.norte) == (-56.01, -19.99)
    assert grade.lon.shape == (202, 202)   # de -56,01 a -53,99 e de -19,99 a -22,01
    assert grade.lon[0, 0] == pytest.approx(-56.005) and grade.lat[0, 0] == pytest.approx(-19.995)
    assert grade.lat[-1, 0] == pytest.approx(-22.005)   # a primeira linha é o norte
    assert not grade.dentro[0, 0] and grade.dentro[100, 100]


def test_o_centro_da_projecao_e_o_do_recorte(grade):
    oeste, leste, sul, norte = RECORTE.limites
    assert grade.centro == ((oeste + leste) / 2, (sul + norte) / 2)


def test_o_idw_da_os_numeros_da_grade_da_tela(grade):
    # A tela interpola a grade inteira do recorte; o pixel do GeoTIFF tem de dar o mesmo número que a
    # conta da tela daria naquele ponto, com a projeção centrada no recorte
    campo = geotiff.idw(LONS, LATS, VALORES, grade)
    lon_tela, lat_tela = calculos.criar_grade(RECORTE.limites, config.RESOLUCAO_GRADE)
    centro = float(np.mean(lon_tela)), float(np.mean(lat_tela))
    linha, coluna = 100, 120
    esperado = calculos.interpolar_idw(LONS, LATS, VALORES, np.array([grade.lon[linha, coluna]]),
                                       np.array([grade.lat[linha, coluna]]), centro=centro)
    assert campo[linha, coluna] == pytest.approx(esperado[0])
    assert np.isnan(campo[~grade.dentro]).all() and not np.isnan(campo[grade.dentro]).any()


def test_o_vizinho_mais_proximo_nao_inventa_classe(grade):
    lon_malha, lat_malha = calculos.criar_grade(RECORTE.limites, config.RESOLUCAO_GRADE)
    classes = (lon_malha > -55.0).astype(int) + (lat_malha < -21.0).astype(int)   # 0, 1 e 2
    campo = geotiff.da_malha(classes, lon_malha, lat_malha, grade)
    dentro = campo[grade.dentro]
    assert set(np.unique(dentro)) == {0.0, 1.0, 2.0}
    assert campo[grade.dentro & (grade.lon < -55.2) & (grade.lat > -20.8)].max() == 0.0
    assert np.isnan(campo[~grade.dentro]).all()


def test_o_geotiff_traz_a_posicao_o_sistema_e_o_nodata(grade):
    campo = geotiff.idw(LONS, LATS, VALORES, grade)
    imagem = _abrir(geotiff.gravar(campo, grade, "Temperatura máxima (°C)", "°C", {"QUANDO": "17/11/2026"}))
    assert imagem.mode == "F" and imagem.size == (202, 202)
    assert imagem.tag_v2[33550] == (0.01, 0.01, 0.0)
    assert imagem.tag_v2[33922] == (0.0, 0.0, 0.0, -56.01, -19.99, 0.0)
    chaves = imagem.tag_v2[34735]
    assert chaves[:4] == (1, 1, 0, 4) and (2048, 0, 1, 4674) == chaves[12:16]
    assert imagem.tag_v2[42113].strip("\0") == "-9999"
    dados = np.asarray(imagem)
    assert (dados[~grade.dentro] == geotiff.NODATA).all()
    assert dados[grade.dentro] == pytest.approx(campo[grade.dentro].astype(np.float32))


def test_os_metadados_guardam_os_acentos(grade):
    imagem = _abrir(geotiff.gravar(np.zeros(grade.lon.shape), grade, "Chuva acumulada no período (mm)", "mm",
                                   {"FONTE": "INMET/SEMADESC"}))
    texto = imagem.tag_v2[42112]
    texto = texto.encode("latin-1").decode("utf-8") if isinstance(texto, str) else texto.decode("utf-8")
    assert 'role="description">Chuva acumulada no período (mm)<' in texto
    assert 'role="unittype">mm<' in texto and '<Item name="FONTE">INMET/SEMADESC</Item>' in texto


def test_o_de_classes_leva_a_paleta_do_painel(grade):
    campo = np.where(grade.lon > -55.0, 3.0, 1.0)
    campo[0, :] = np.nan
    imagem = _abrir(geotiff.gravar_classes(campo, grade, config.CORES_RISCO, config.ROTULOS_RISCO, "Risco de fogo"))
    assert imagem.mode == "P"
    paleta = imagem.getpalette()
    assert paleta[9:12] == [0xd3, 0x2f, 0x2f]   # classe 3, o vermelho do risco alto
    dados = np.asarray(imagem)
    assert (dados[~grade.dentro] == geotiff.NODATA_CLASSES).all()
    assert set(np.unique(dados[grade.dentro])) == {1, 3}
    assert imagem.tag_v2[42113].strip("\0") == "255"
    assert "CLASSE_3" in str(imagem.tag_v2[42112])
