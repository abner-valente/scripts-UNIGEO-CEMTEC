"""GeoPackage das estações dos mapas, para pôr os pontos sobre o GeoTIFF no QGIS e no ArcGIS.

Uma camada de pontos em SIRGAS 2000 (EPSG:4674), a referência do GeoTIFF, com a estação, o código, a
rede, o estado, o valor que entrou no mapa, a unidade e o rótulo escrito como na tela (vírgula
decimal, as casas do produto). Entram também as estações dos estados vizinhos, que firmam a borda do
IDW: a coluna `uf` as separa.

O estilo do QGIS vai dentro do arquivo, na tabela `layer_styles`, que o QGIS lê sozinho ao abrir a
camada: os pontos pretos de borda branca dos mapas do painel, e os rótulos com o valor já prontos,
mas desligados. O ArcGIS não lê essa tabela e desenha os pontos com o símbolo dele.

Gravado com o pyogrio, que o geopandas do painel já usa; a tabela do estilo, com o sqlite3. Só
cálculo, sem tela, para poder ser testado.
"""
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from . import config
from .geotiff import EPSG_SIRGAS_2000

MIME = "application/geopackage+sqlite3"
CAMADA = "estacoes"

# O estilo, no formato do QGIS (QML): só o símbolo e os rótulos. Os rótulos ficam desligados
# (labelsEnabled="0"), mas configurados: ligá-los já mostra o valor ao lado de cada ponto
ESTILO_QML = """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis labelsEnabled="0" styleCategories="Symbology|Labeling" version="3.34.0">
 <renderer-v2 type="singleSymbol" symbollevels="0" forceraster="0" enableorderby="0">
  <symbols>
   <symbol type="marker" name="0" alpha="1" clip_to_extent="1" force_rhr="0">
    <layer class="SimpleMarker" enabled="1" locked="0" pass="0">
     <Option type="Map">
      <Option name="name" type="QString" value="circle"/>
      <Option name="color" type="QString" value="0,0,0,255"/>
      <Option name="outline_color" type="QString" value="255,255,255,255"/>
      <Option name="outline_style" type="QString" value="solid"/>
      <Option name="outline_width" type="QString" value="0.4"/>
      <Option name="outline_width_unit" type="QString" value="MM"/>
      <Option name="size" type="QString" value="2.4"/>
      <Option name="size_unit" type="QString" value="MM"/>
     </Option>
    </layer>
   </symbol>
  </symbols>
 </renderer-v2>
 <labeling type="simple">
  <settings calloutType="simple">
   <text-style fieldName="rotulo" isExpression="0" fontSize="8" fontSizeUnit="Point" textColor="0,0,0,255">
    <text-buffer bufferDraw="1" bufferSize="0.8" bufferSizeUnits="MM" bufferColor="255,255,255,255"/>
   </text-style>
   <placement placement="6" predefinedPositionOrder="TR,TL,BR,BL,R,L,TSR,BSR" dist="0.5" distUnits="MM"/>
   <rendering drawLabels="1" obstacle="1"/>
  </settings>
 </labeling>
 <layerGeometryType>0</layerGeometryType>
</qgis>
"""

# A tabela em que o próprio QGIS guarda os estilos num GeoPackage, com os mesmos nomes de coluna
_TABELA_DO_ESTILO = """CREATE TABLE layer_styles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT(256), f_table_schema TEXT(256),
    f_table_name TEXT(256), f_geometry_column TEXT(256), styleName TEXT(30), styleQML TEXT, styleSLD TEXT,
    useAsDefault BOOLEAN, description TEXT, owner TEXT(30), ui TEXT(30),
    update_time DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"""


def tabela_das_estacoes(pontos: pd.DataFrame, coluna: str, casas: int, unidade: str) -> gpd.GeoDataFrame:
    # `pontos`: Estação, CD_ESTACAO, SG_ESTADO, Longitude, Latitude e a coluna do valor, como o painel
    # junta o cadastro às medidas. Do maior valor ao menor, como o ranking dos mapas
    pontos = pontos.sort_values(coluna, ascending=False, kind="stable")
    nomes = pontos["Estação"].astype(str)
    valores = pontos[coluna].astype(float)
    return gpd.GeoDataFrame(
        {"estacao": nomes.str.removeprefix(config.PREFIXO_CEMADEN).to_numpy(),
         "codigo": pontos["CD_ESTACAO"].fillna("").astype(str).to_numpy(),
         "rede": np.where(nomes.str.startswith(config.PREFIXO_CEMADEN), "CEMADEN", "INMET"),
         "uf": pontos["SG_ESTADO"].fillna("").astype(str).to_numpy(),
         "valor": valores.round(2).to_numpy(),
         "unidade": unidade,
         "rotulo": [f"{valor:.{casas}f}".replace(".", ",") for valor in valores]},
        geometry=gpd.points_from_xy(pontos["Longitude"], pontos["Latitude"]), crs=EPSG_SIRGAS_2000)


def gravar(tabela: gpd.GeoDataFrame, titulo: str, descricao: str) -> bytes:
    # O título vira o nome da camada no GeoPackage (o identificador) e a descrição vai junto
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / f"{CAMADA}.gpkg"
        tabela.to_file(caminho, layer=CAMADA, driver="GPKG", engine="pyogrio",
                       layer_options={"IDENTIFIER": titulo, "DESCRIPTION": descricao})
        _embutir_o_estilo(caminho)
        return caminho.read_bytes()


def _embutir_o_estilo(caminho: Path) -> None:
    with closing(sqlite3.connect(caminho)) as banco, banco:   # o segundo "banco" faz o commit no fim
        coluna = banco.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?",
                               (CAMADA,)).fetchone()[0]
        banco.execute(_TABELA_DO_ESTILO)
        banco.execute("INSERT INTO layer_styles (f_table_catalog, f_table_schema, f_table_name, f_geometry_column, "
                      "styleName, styleQML, styleSLD, useAsDefault, description, owner, ui) "
                      "VALUES ('', '', ?, ?, ?, ?, '', 1, ?, '', '')",
                      (CAMADA, coluna, "Estações do painel", ESTILO_QML,
                       "Pontos pretos de borda branca, como nos mapas do painel; rótulos com o valor, desligados"))
        # Registrada como tabela de atributos, para o GDAL (e o QGIS, por ele) enxergá-la
        banco.execute("INSERT INTO gpkg_contents (table_name, data_type, identifier) "
                      "VALUES ('layer_styles', 'attributes', 'layer_styles')")
