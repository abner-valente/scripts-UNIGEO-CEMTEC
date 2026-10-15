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

from app import interativo, variaveis
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
    assert pontos["rotulo"].tolist() == ["3", "0"]
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


def test_na_chuva_o_ponto_e_escuro_e_leva_o_valor():
    acumulado = pd.DataFrame({"Estação": ["Campo Grande", "Dourados", "Sem dado"], "Latitude": [-20.4, -22.2, -21.0],
                              "Longitude": [-54.7, -54.9, -55.0], "Chuva acumulada": [12.44, 0.0, np.nan]})

    pontos = interativo.pontos_de_valores(acumulado, "Chuva acumulada", "Chuva 24 h", "mm", 1)

    assert pontos["Estação"].tolist() == ["Campo Grande", "Dourados"]
    assert pontos["rotulo"].tolist() == ["12,4", "0,0"]
    assert pontos["detalhe"].tolist() == ["Chuva 24 h: 12,4 mm", "Chuva 24 h: 0,0 mm"]
    assert pontos["cor"].iloc[0] == interativo.COR_DA_ESTACAO


def leituras_de_chuva() -> pd.DataFrame:
    horas = pd.date_range("2026-10-13 22:00", periods=4, freq="h", tz=config.FUSO_MS)   # 22h, 23h, 00h, 01h
    return pd.DataFrame({"Estação": "Campo Grande", "dt_local": horas, "CHUVA": [1.0, 2.5, np.nan, 4.0]})


def test_a_chuva_da_estacao_hora_a_hora_com_o_acumulado():
    leituras = leituras_de_chuva()
    serie = interativo.serie_da_chuva(leituras, "Campo Grande", leituras["dt_local"].min() - pd.Timedelta(hours=1),
                                      leituras["dt_local"].max(), por_dia=False)
    assert serie["chuva"].tolist()[:2] == [1.0, 2.5] and np.isnan(serie["chuva"].iloc[2])
    assert serie["acumulado"].tolist() == [1.0, 3.5, 3.5, 7.5]
    assert serie["quando"].iloc[0] == pd.Timestamp("2026-10-13 22:00")


def test_a_chuva_da_estacao_por_dia_com_a_00h_fechando_o_dia_anterior():
    leituras = leituras_de_chuva()
    serie = interativo.serie_da_chuva(leituras, "Campo Grande", leituras["dt_local"].min() - pd.Timedelta(hours=1),
                                      leituras["dt_local"].max(), por_dia=True)
    assert serie["quando"].tolist() == [pd.Timestamp("2026-10-13"), pd.Timestamp("2026-10-14")]
    assert serie["chuva"].tolist() == [3.5, 4.0]   # 22h, 23h e a 00h (vazia) no dia 13; a 01h no 14
    assert interativo.serie_da_chuva(leituras, "Outra", leituras["dt_local"].min(), leituras["dt_local"].max(),
                                     por_dia=False).empty


def test_o_rumo_do_vento_em_oito_direcoes():
    assert [interativo.rumo(graus) for graus in (0, 22, 23, 90, 135, 180, 270, 337, 338, 360)] ==         ["N", "N", "NE", "L", "SE", "S", "O", "NO", "N", "N"]


def test_o_vento_leva_a_seta_para_onde_vai_e_o_balao_de_onde_vem():
    pontos = interativo.pontos_de_valores(
        pd.DataFrame({"Estação": ["Campo Grande", "Dourados"], "Latitude": [-20.4, -22.2],
                      "Longitude": [-54.7, -54.9], "Rajada na hora": [41.0, 12.0]}),
        "Rajada na hora", "Rajada na hora", "km/h", 1)

    com_vento = interativo.com_direcao(pontos, pd.Series({"Campo Grande": 90.0}))

    # Do leste (90°) o vento vai para oeste: a seta "↑" gira 270° no sentido anti-horário
    assert com_vento["angulo"].iloc[0] == -270.0 and np.isnan(com_vento["angulo"].iloc[1])
    assert com_vento["detalhe"].iloc[0] == "Rajada na hora: 41,0 km/h<br/>de 90° (L)"
    assert com_vento["detalhe"].iloc[1] == "Rajada na hora: 12,0 km/h"


def test_a_serie_da_variavel_na_estacao_segue_a_regra_do_produto():
    horas = pd.date_range("2026-10-13 01:00", periods=48, freq="h", tz=config.FUSO_MS)
    leituras = pd.DataFrame({"Estação": "Campo Grande", "dt_local": horas, "TEM_MAX": np.arange(48.0)})
    maxima = variaveis.por_nome("Temperatura máxima")

    serie = interativo.serie_da_variavel(maxima, leituras, "Campo Grande", variaveis.DIA)

    assert serie["valor"].tolist() == [23.0, 47.0]   # a maior das máximas de cada dia (a 00h fecha o anterior)
    assert serie["quando"].dt.tz is None
    assert interativo.serie_da_variavel(maxima, leituras, "Outra", variaveis.DIA).empty
