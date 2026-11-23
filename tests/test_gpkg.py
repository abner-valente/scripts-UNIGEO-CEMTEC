import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing

import pandas as pd
import pyogrio

from modulos import config, gpkg

PONTOS = pd.DataFrame({
    "Estação": ["CAMPO GRANDE", f"{config.PREFIXO_CEMADEN}Campo Grande (Centro)", "DOURADOS", "CASSILÂNDIA"],
    "CD_ESTACAO": ["A702", "500270101A", "A721", "A742"],
    "SG_ESTADO": ["MS", "MS", "MS", "GO"],
    "Latitude": [-20.45, -20.46, -22.19, -19.12],
    "Longitude": [-54.72, -54.61, -54.91, -51.72],
    "Chuva": [12.400000000000002, 30.25, 0.0, 1.2]})


def test_tabela_vai_do_maior_ao_menor_com_a_rede_e_o_rotulo_da_tela():
    tabela = gpkg.tabela_das_estacoes(PONTOS, "Chuva", 1, "mm")
    assert tabela["estacao"].tolist() == ["Campo Grande (Centro)", "CAMPO GRANDE", "CASSILÂNDIA", "DOURADOS"]
    assert tabela["rede"].tolist() == ["CEMADEN", "INMET", "INMET", "INMET"]
    assert tabela["uf"].tolist() == ["MS", "MS", "GO", "MS"]
    assert tabela["valor"].tolist() == [30.25, 12.4, 1.2, 0.0]
    assert tabela["rotulo"].tolist() == ["30,2", "12,4", "1,2", "0,0"]
    assert set(tabela["unidade"]) == {"mm"}
    assert tabela.crs.to_epsg() == 4674
    assert (tabela.geometry.x.iloc[0], tabela.geometry.y.iloc[0]) == (-54.61, -20.46)


def test_rotulo_segue_as_casas_do_produto():
    tabela = gpkg.tabela_das_estacoes(PONTOS.assign(Chuva=[-1.26, 2.0, 3.5, 4.44]), "Chuva", 0, "°C")
    assert tabela["rotulo"].tolist() == ["4", "4", "2", "-1"]


def test_geopackage_volta_com_os_pontos_e_o_estilo_do_qgis(tmp_path):
    dados = gpkg.gravar(gpkg.tabela_das_estacoes(PONTOS, "Chuva", 1, "mm"), "Estações — Chuva",
                        "Chuva (mm) · 23/11/2026 · INMET/SEMADESC/CEMADEN")
    arquivo = tmp_path / "estacoes.gpkg"
    arquivo.write_bytes(dados)
    lida = pyogrio.read_dataframe(arquivo, layer=gpkg.CAMADA)
    assert lida.crs.to_epsg() == 4674
    assert lida["estacao"].tolist()[0] == "Campo Grande (Centro)"
    assert lida["valor"].tolist() == [30.25, 12.4, 1.2, 0.0]

    with closing(sqlite3.connect(arquivo)) as banco:
        assert banco.execute("SELECT identifier, description FROM gpkg_contents WHERE table_name = ?",
                             (gpkg.CAMADA,)).fetchone() == ("Estações — Chuva",
                                                            "Chuva (mm) · 23/11/2026 · INMET/SEMADESC/CEMADEN")
        assert banco.execute("SELECT data_type FROM gpkg_contents WHERE table_name = 'layer_styles'").fetchone() \
            == ("attributes",)
        tabela, coluna, padrao, qml = banco.execute(
            "SELECT f_table_name, f_geometry_column, useAsDefault, styleQML FROM layer_styles").fetchone()
    assert (tabela, coluna, padrao, qml) == (gpkg.CAMADA, "geom", 1, gpkg.ESTILO_QML)


def test_estilo_tem_o_ponto_preto_de_borda_branca_e_os_rotulos_desligados():
    raiz = ET.fromstring(gpkg.ESTILO_QML.split("\n", 1)[1])
    assert raiz.get("labelsEnabled") == "0"
    opcoes = {o.get("name"): o.get("value") for o in raiz.iter("Option") if o.get("name")}
    assert (opcoes["name"], opcoes["color"], opcoes["outline_color"]) == ("circle", "0,0,0,255", "255,255,255,255")
    assert raiz.find("labeling/settings/text-style").get("fieldName") == "rotulo"
