"""Mapas interativos: a superfície de classes como imagem, os pontos e o balão, os municípios e a série."""
import base64
import io
import json
from types import SimpleNamespace
from urllib.parse import unquote

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from PIL import Image

from app import interativo
from modulos import config
from modulos.produtos import risco_fogo


def pixels(uri: str) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))))


def test_a_imagem_leva_a_cor_de_cada_classe_com_o_norte_em_cima():
    grade = np.zeros((4, 4))
    grade[2:, :] = 3          # a metade norte em risco alto (a grade começa no sul)
    grade[0, 0] = np.nan      # sem dado no canto sudoeste
    dentro = np.ones((8, 8), dtype=bool)
    dentro[:, -1] = False     # a última coluna fora do estado

    imagem = pixels(interativo.imagem_de_classes(grade, dentro))

    assert imagem.shape == (8, 8, 4)
    assert list(imagem[0, 0, :3]) == interativo.rgba(config.CORES_RISCO[3])[:3]    # norte, em cima
    assert list(imagem[4, 3, :3]) == interativo.rgba(config.CORES_RISCO[0])[:3]
    assert imagem[-1, 0, 3] == 0 and imagem[0, -1, 3] == 0                          # sem dado e fora: transparentes
    assert imagem[0, 0, 3] == int(255 * interativo.OPACIDADE)


def estacoes_na_hora() -> pd.DataFrame:
    estacoes = pd.DataFrame({"Estação": ["Campo Grande", "Dourados", "Sem dado"],
                             "Longitude": [-54.7, -54.9, -55.0], "Latitude": [-20.4, -22.2, -21.0],
                             "TEM_MAX": [32.4, 25.0, np.nan], "UMD_MIN": [24.0, 60.0, np.nan],
                             "rajada_kmh": [41.0, 12.5, np.nan]})
    atendidas = risco_fogo.condicoes_atendidas(estacoes["TEM_MAX"], estacoes["UMD_MIN"], estacoes["rajada_kmh"])
    for coluna, atendida in zip(risco_fogo.COLUNAS_CONDICOES, atendidas):
        estacoes[coluna] = atendida
    estacoes[risco_fogo.COLUNA_NIVEL_HORA] = [3, 0, np.nan]
    return estacoes


def test_na_hora_o_balao_traz_o_nivel_e_as_tres_medidas():
    pontos = interativo.pontos_de_risco(estacoes_na_hora(), risco_fogo.COLUNA_NIVEL_HORA, "Na hora")

    assert pontos["Estação"].tolist() == ["Campo Grande", "Dourados"]      # a sem nível fica de fora
    assert pontos["cor"].tolist()[0] == interativo.rgba(config.CORES_RISCO[3])
    assert pontos["nivel"].tolist() == ["3", "0"]
    campo_grande = pontos["detalhe"].iloc[0]
    assert campo_grande.startswith("Na hora: Risco alto (3 de 3)")
    assert "✔ Temperatura máxima: 32,4 °C" in campo_grande and "✔ Umidade mínima: 24 %" in campo_grande
    assert "✘ Rajada" in pontos["detalhe"].iloc[1]


def test_por_dia_o_balao_traz_so_o_nivel():
    do_dia = pd.DataFrame({"Estação": ["Campo Grande"], "Longitude": [-54.7], "Latitude": [-20.4],
                           risco_fogo.COLUNA_NIVEL_HORA: [2]})
    pontos = interativo.pontos_de_risco(do_dia, risco_fogo.COLUNA_NIVEL_HORA, "Pior nível do dia")
    assert pontos["detalhe"].tolist() == ["Pior nível do dia: Risco médio"]


def test_os_municipios_vao_com_o_nome_no_balao():
    base = SimpleNamespace(municipios=gpd.GeoDataFrame(
        {interativo.COLUNA_DO_MUNICIPIO: ["Campo Grande"]}, geometry=[shapely.box(-55, -21, -54, -20)],
        crs="EPSG:4326"))
    feicao = interativo.municipios(base)["features"][0]
    assert feicao["properties"] == {"titulo": "Campo Grande", "detalhe": "Município"}


def test_a_serie_da_estacao_marca_as_horas_em_que_cada_condicao_valeu():
    horas = pd.date_range("2026-10-12 17:00", periods=2, freq="h", tz="UTC")
    leituras = pd.DataFrame({"dt_utc": horas, "TEM_MAX": [31.0, 29.0], "UMD_MIN": [28.0, 35.0],
                             "rajada_kmh": [30.0, 10.0]})
    completas = [(pd.Series({"Estação": "Campo Grande"}), leituras)]

    serie = interativo.serie_da_estacao(completas, "Campo Grande", config.FUSO_MS)

    assert serie["Medida"].unique().tolist() == ["Temperatura máxima (°C)", "Umidade mínima (%)", "Rajada (km/h)"]
    assert serie["Condição"].tolist() == ["atendida", "não"] * 3
    assert serie["dt_local"].iloc[0] == pd.Timestamp("2026-10-12 13:00")       # no horário de MS
    assert interativo.serie_da_estacao(completas, "Outra", config.FUSO_MS).empty


def test_o_estilo_do_mapa_nao_pede_nada_a_internet():
    assert interativo.ESTILO_SEM_FUNDO.startswith("data:application/json,")
    estilo = json.loads(unquote(interativo.ESTILO_SEM_FUNDO.split(",", 1)[1]))
    assert estilo["sources"] == {} and [camada["type"] for camada in estilo["layers"]] == ["background"]
