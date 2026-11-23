"""GeoTIFF dos mapas, para abrir no QGIS e no ArcGIS, ou publicar no PIN MS.

A grade segue o padrão das camadas do PIN MS: SIRGAS 2000 (EPSG:4674), pixel de 0,01° (~1 km), no
retângulo do estado; fora do contorno, NoData. Cada mapa entra com a conta da tela:

- o IDW das estações, refeito em cada pixel (mesmos vizinhos, potência e projeção);
- a superfície da previsão, pela mesma bilinear, calculada direto nos pixels (em `app/previsao.py`);
- o risco de fogo, que nasce na grade dos produtos (~8 km), levado aos pixels pelo vizinho mais
  próximo, como no mapa navegável: sem inventar nível entre as classes.

Gravado só com o Pillow, que o painel já tem. As tags do GeoTIFF (a posição, o sistema de referência)
e as do GDAL (o NoData, a descrição da banda) são escritas à mão; o GDAL, que é o leitor do QGIS e do
ArcGIS, as lê como as dele.

Só cálculo, sem tela, para poder ser testado.
"""
import io
import math
from dataclasses import dataclass
from xml.sax.saxutils import escape

import numpy as np
import shapely
from matplotlib import colors as mcolors
from PIL import Image, TiffImagePlugin, TiffTags
from scipy.interpolate import RegularGridInterpolator

from . import calculos, config

PASSO = 0.01
EPSG_SIRGAS_2000 = 4674
NODATA = -9999.0
NODATA_CLASSES = 255
MIME = "image/tiff"
METODO_IDW = (f"IDW das estações ({config.IDW_VIZINHOS} vizinhas, potência {config.IDW_POTENCIA:g}, distâncias "
              "em km), com as estações dos estados vizinhos na borda")

# As tags do GeoTIFF (OGC GeoTIFF 1.1) e as do GDAL para os metadados e o NoData
_ESCALA_DO_PIXEL, _AMARRACAO, _CHAVES_GEO = 33550, 33922, 34735
_METADADOS_GDAL, _NODATA_GDAL = 42112, 42113
_CHAVES = (1, 1, 0, 4,                       # versão 1.1.0, quatro chaves
           1024, 0, 1, 2,                    # GTModelTypeGeoKey: coordenadas geográficas
           1025, 0, 1, 1,                    # GTRasterTypeGeoKey: o valor vale para a área do pixel
           2048, 0, 1, EPSG_SIRGAS_2000,     # GeographicTypeGeoKey
           2054, 0, 1, 9102)                 # GeogAngularUnitsGeoKey: grau


@dataclass(frozen=True)
class Grade:
    oeste: float
    norte: float
    passo: float
    lon: np.ndarray                # o centro de cada pixel; a primeira linha é a do norte
    lat: np.ndarray
    dentro: np.ndarray             # o centro do pixel cai dentro do estado
    centro: tuple[float, float]    # o da projeção do IDW, o mesmo dos mapas da tela


def grade_do_estado(estado, recorte: config.Recorte, passo: float = PASSO) -> Grade:
    # O retângulo do estado, com as bordas em múltiplos do passo: os pixels de dois mapas caem sempre
    # nos mesmos lugares, e um se sobrepõe ao outro no SIG sem reamostrar
    oeste_estado, sul_estado, leste_estado, norte_estado = estado.bounds
    oeste, sul = (math.floor(round(valor / passo, 6)) for valor in (oeste_estado, sul_estado))
    leste, norte = (math.ceil(round(valor / passo, 6)) for valor in (leste_estado, norte_estado))
    lon, lat = np.meshgrid((np.arange(oeste, leste) + 0.5) * passo, (np.arange(norte, sul, -1) - 0.5) * passo)
    limite_oeste, limite_leste, limite_sul, limite_norte = recorte.limites
    return Grade(round(oeste * passo, 6), round(norte * passo, 6), passo, lon, lat,
                 shapely.contains_xy(estado, lon, lat),
                 ((limite_oeste + limite_leste) / 2, (limite_sul + limite_norte) / 2))


def idw(lons, lats, valores, grade: Grade) -> np.ndarray:
    # O IDW dos mapas da tela, só nos pixels do estado; fora, NaN
    campo = np.full(grade.lon.shape, np.nan)
    campo[grade.dentro] = calculos.interpolar_idw(lons, lats, np.asarray(valores, dtype=float),
                                                  grade.lon[grade.dentro], grade.lat[grade.dentro],
                                                  config.IDW_VIZINHOS, config.IDW_POTENCIA, grade.centro)
    return campo


def da_malha(valores: np.ndarray, lon_malha: np.ndarray, lat_malha: np.ndarray, grade: Grade) -> np.ndarray:
    # Uma grade já calculada (a dos produtos, que começa no sul) levada aos pixels pelo vizinho mais
    # próximo; fora do estado, NaN
    vizinho = RegularGridInterpolator((lat_malha[:, 0], lon_malha[0, :]), np.asarray(valores, dtype=float),
                                      method="nearest", bounds_error=False, fill_value=np.nan)
    campo = vizinho(np.column_stack([grade.lat.ravel(), grade.lon.ravel()])).reshape(grade.lon.shape)
    campo[~grade.dentro] = np.nan
    return campo


def gravar(campo: np.ndarray, grade: Grade, descricao: str, unidade: str = "",
           metadados: dict[str, str] | None = None) -> bytes:
    # Uma banda de float32 com o valor de cada pixel; fora do estado e onde não há valor, NoData
    dados = np.where(grade.dentro & np.isfinite(campo), campo, NODATA).astype(np.float32)
    return _tiff(Image.fromarray(dados), grade, f"{NODATA:g}", descricao, unidade, metadados)


def gravar_classes(campo: np.ndarray, grade: Grade, cores: list[str], rotulos: list[str], descricao: str,
                   metadados: dict[str, str] | None = None) -> bytes:
    # Uma banda de bytes com a classe de cada pixel e a paleta do painel embutida: o QGIS e o ArcGIS
    # abrem já com as cores. O nome de cada classe vai nos metadados
    validos = grade.dentro & np.isfinite(campo)
    classes = np.clip(np.nan_to_num(campo), 0, len(cores) - 1)
    dados = np.where(validos, classes, NODATA_CLASSES).astype(np.uint8)
    imagem = Image.frombytes("P", dados.shape[::-1], dados.tobytes())
    paleta = [255] * 768
    for indice, cor in enumerate(cores):
        paleta[3 * indice:3 * indice + 3] = [round(255 * canal) for canal in mcolors.to_rgb(cor)]
    imagem.putpalette(paleta)
    nomes = {f"CLASSE_{indice}": rotulo for indice, rotulo in enumerate(rotulos)}
    return _tiff(imagem, grade, str(NODATA_CLASSES), descricao, "", {**nomes, **(metadados or {})})


def _tiff(imagem: Image.Image, grade: Grade, nodata: str, descricao: str, unidade: str,
          metadados: dict[str, str] | None) -> bytes:
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    for numero, valor, tipo in ((_ESCALA_DO_PIXEL, (grade.passo, grade.passo, 0.0), TiffTags.DOUBLE),
                                (_AMARRACAO, (0.0, 0.0, 0.0, grade.oeste, grade.norte, 0.0), TiffTags.DOUBLE),
                                (_CHAVES_GEO, _CHAVES, TiffTags.SHORT),
                                (_NODATA_GDAL, nodata, TiffTags.ASCII),
                                # Em bytes: em texto, o Pillow trocaria por "?" o que não é ASCII (°, ç, ã)
                                (_METADADOS_GDAL, _xml(descricao, unidade, metadados).encode("utf-8"),
                                 TiffTags.ASCII)):
        tags[numero] = valor
        tags.tagtype[numero] = tipo
    arquivo = io.BytesIO()
    imagem.save(arquivo, format="TIFF", tiffinfo=tags, compression="tiff_adobe_deflate")
    return arquivo.getvalue()


def _xml(descricao: str, unidade: str, metadados: dict[str, str] | None) -> str:
    # O formato do GDAL: os itens sem `sample` são do arquivo; os com `sample="0"`, da banda
    itens = [f'<Item name="{nome}">{escape(str(valor))}</Item>' for nome, valor in (metadados or {}).items()]
    itens.append(f'<Item name="DESCRIPTION" sample="0" role="description">{escape(descricao)}</Item>')
    if unidade:
        itens.append(f'<Item name="UNITTYPE" sample="0" role="unittype">{escape(unidade)}</Item>')
    return "<GDALMetadata>" + "".join(itens) + "</GDALMetadata>"
