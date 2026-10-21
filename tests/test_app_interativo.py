"""Mapas interativos: a superfície de classes como imagem, os pontos e o balão, os municípios e a série."""
import base64
import io
import json
from types import SimpleNamespace
from urllib.parse import unquote

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely
from PIL import Image

from app import interativo, variaveis
from modulos import calculos, config
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
    assert com_vento["detalhe"].iloc[0] == "Rajada na hora: 41,0 km/h\nde 90° (L)"
    assert com_vento["detalhe"].iloc[1] == "Rajada na hora: 12,0 km/h"


def test_a_serie_da_variavel_na_estacao_segue_a_regra_do_produto():
    horas = pd.date_range("2026-10-13 01:00", periods=48, freq="h", tz=config.FUSO_MS)
    leituras = pd.DataFrame({"Estação": "Campo Grande", "dt_local": horas, "TEM_MAX": np.arange(48.0)})
    maxima = variaveis.por_nome("Temperatura máxima")

    serie = interativo.serie_da_variavel(maxima, leituras, "Campo Grande", variaveis.DIA)

    assert serie["valor"].tolist() == [23.0, 47.0]   # a maior das máximas de cada dia (a 00h fecha o anterior)
    assert serie["quando"].dt.tz is None
    assert interativo.serie_da_variavel(maxima, leituras, "Outra", variaveis.DIA).empty


# =====================================================
# O VALOR EM QUALQUER PONTO DO ESTADO
# =====================================================
def base_de_teste():
    # Um "estado" de 2° × 2° com dois municípios, num recorte de 3° × 3°: a grade de 31 pontos
    # por eixo fica com uma célula a cada 0,1°
    municipios = gpd.GeoDataFrame({interativo.COLUNA_DO_MUNICIPIO: ["Oeste", "Leste"]},
                                  geometry=[shapely.box(-55.45, -21.45, -54.45, -19.55),
                                            shapely.box(-54.45, -21.45, -53.55, -19.55)], crs="EPSG:4326")
    estado = gpd.GeoDataFrame(geometry=[shapely.box(-55.45, -21.45, -53.55, -19.55)], crs="EPSG:4326")
    return SimpleNamespace(recorte=SimpleNamespace(limites=(-56.0, -53.0, -22.0, -19.0)),
                           uf=estado, municipios=municipios)


ESTACOES_DO_TESTE = pd.DataFrame({"Estação": ["A", "B", "C", "D"],
                                  "Longitude": [-55.2, -53.8, -54.6, -54.0],
                                  "Latitude": [-20.0, -20.2, -21.2, -21.0]})


def test_a_grade_do_ponto_cobre_o_estado_e_sabe_o_municipio():
    grade = interativo.grade_do_ponto(base_de_teste(), resolucao=31)

    assert grade.meio_passo == pytest.approx((0.05, 0.05))
    assert grade.centro == pytest.approx((-54.5, -20.5))
    assert grade.dentro.sum() == 19 * 19                      # as células de centro dentro do quadrado
    municipios = set(grade.municipio[grade.dentro])
    assert municipios == {"Oeste", "Leste"}


def test_o_balao_da_celula_traz_o_valor_interpolado_e_a_estacao_mais_proxima():
    grade = interativo.grade_do_ponto(base_de_teste(), resolucao=31)
    entrada = ESTACOES_DO_TESTE.assign(**{"Temperatura máxima": [30.0, 34.0, 28.0, 32.0]})

    celulas = interativo.celulas_do_ponto(grade, entrada, "Temperatura máxima", "°C", 1)

    superficie = calculos.interpolar_idw(entrada["Longitude"], entrada["Latitude"], entrada["Temperatura máxima"],
                                         grade.lon, grade.lat, config.IDW_VIZINHOS, config.IDW_POTENCIA)
    assert len(celulas) == grade.dentro.sum()
    np.testing.assert_allclose(celulas["valor"], np.ravel(superficie)[celulas["celula"]])   # a cor do mapa
    perto_de_a = celulas.iloc[(celulas["Longitude"] + 55.2).abs().add((celulas["Latitude"] + 20.0).abs()).argmin()]
    assert perto_de_a["titulo"] == "Oeste" and perto_de_a["mais_proxima"] == "A"
    assert perto_de_a["detalhe"].startswith("Temperatura máxima: 30,0 °C (interpolado)\nEstação mais próxima: A, a ")
    assert list(celulas[interativo.COLUNAS_DA_CAMADA].columns) == interativo.COLUNAS_DA_CAMADA


def test_o_rumo_no_ponto_sai_das_componentes_e_nao_da_media_dos_graus():
    grade = interativo.grade_do_ponto(base_de_teste(), resolucao=31)
    # Duas estações à mesma distância do centro, uma com vento de 350° e outra de 10°
    entrada = pd.DataFrame({"Estação": ["A", "B"], "Longitude": [-55.0, -54.0], "Latitude": [-20.5, -20.5],
                            "Vento médio": [10.0, 10.0]})

    graus = interativo.direcao_interpolada(entrada, "Vento médio", pd.Series({"A": 350.0, "B": 10.0}),
                                           np.array([[-54.5]]), np.array([[-20.5]]))

    assert min(graus[0, 0], 360 - graus[0, 0]) == pytest.approx(0.0, abs=1e-6)   # do norte, e não 180°


def test_a_serie_do_ponto_no_instante_do_mapa_e_o_valor_do_balao():
    grade = interativo.grade_do_ponto(base_de_teste(), resolucao=31)
    horas = pd.date_range("2026-10-13 01:00", periods=48, freq="h", tz=config.FUSO_MS)
    rng = np.random.default_rng(2)
    leituras = pd.concat([pd.DataFrame({"Estação": nome, "dt_local": horas, "TEM_MAX": rng.uniform(20, 35, 48)})
                          for nome in ESTACOES_DO_TESTE["Estação"]], ignore_index=True)
    maxima = variaveis.por_nome("Temperatura máxima")
    primeiro_dia = variaveis.recorte(leituras, variaveis.DIA, variaveis.momentos(leituras, variaveis.DIA)[0])
    entrada = ESTACOES_DO_TESTE.join(variaveis.por_estacao(maxima, primeiro_dia), on="Estação")
    celulas = interativo.celulas_do_ponto(grade, entrada, maxima.nome, maxima.unidade, maxima.decimais)
    celula = celulas.iloc[len(celulas) // 3]

    serie = interativo.serie_do_ponto(maxima, leituras, ESTACOES_DO_TESTE,
                                      float(np.ravel(grade.lon)[celula["celula"]]),
                                      float(np.ravel(grade.lat)[celula["celula"]]), variaveis.DIA, grade.centro)

    assert len(serie) == 2 and serie["quando"].dt.tz is None
    assert serie["valor"].iloc[0] == pytest.approx(celula["valor"])   # o clique e o balão dizem o mesmo


def test_a_serie_do_ponto_com_poucas_estacoes_fica_vazia():
    horas = pd.date_range("2026-10-13 01:00", periods=24, freq="h", tz=config.FUSO_MS)
    leituras = pd.concat([pd.DataFrame({"Estação": nome, "dt_local": horas, "TEM_MAX": 30.0}) for nome in ("A", "B")],
                         ignore_index=True)

    serie = interativo.serie_do_ponto(variaveis.por_nome("Temperatura máxima"), leituras, ESTACOES_DO_TESTE,
                                      -54.5, -20.5, variaveis.DIA, (-54.5, -20.5))

    assert serie.empty   # duas estações não bastam (config.MIN_ESTACOES_INTERPOLACAO)


def chuva_das_quatro() -> pd.DataFrame:
    horas = pd.date_range("2026-10-13 22:00", periods=4, freq="h", tz=config.FUSO_MS)   # 22h, 23h, 00h, 01h
    chuvas = {"A": [1.0, 2.5, 0.0, 4.0], "B": [0.0, 0.0, 3.0, 0.0], "C": [5.0, 0.0, 0.0, 0.0], "D": [0.0, 1.0, 1.0, 1.0]}
    return pd.concat([pd.DataFrame({"Estação": nome, "dt_local": horas, "CHUVA": valores})
                      for nome, valores in chuvas.items()], ignore_index=True)


def test_a_chuva_no_ponto_em_cima_de_uma_estacao_e_a_dela_com_o_acumulado():
    leituras = chuva_das_quatro()
    de, ate = leituras["dt_local"].min() - pd.Timedelta(hours=1), leituras["dt_local"].max()

    def no_ponto(por_dia, de=de):
        return interativo.serie_da_chuva_no_ponto(leituras, ESTACOES_DO_TESTE, -55.2, -20.0, de, ate, por_dia,
                                                  (-54.5, -20.5))

    por_hora = no_ponto(False)
    assert por_hora["chuva"].tolist() == pytest.approx([1.0, 2.5, 0.0, 4.0])
    assert por_hora["acumulado"].tolist() == pytest.approx([1.0, 3.5, 3.5, 7.5])
    assert por_hora["quando"].iloc[0] == pd.Timestamp("2026-10-13 22:00")
    por_dia = no_ponto(True)
    assert por_dia["quando"].tolist() == [pd.Timestamp("2026-10-13"), pd.Timestamp("2026-10-14")]
    assert por_dia["chuva"].tolist() == pytest.approx([3.5, 4.0])   # a 00h fecha o dia 13
    assert no_ponto(False, de=de + pd.Timedelta(hours=1))["quando"].iloc[0] == pd.Timestamp("2026-10-13 23:00")


def test_a_chuva_no_ponto_com_poucas_estacoes_fica_vazia():
    leituras = chuva_das_quatro()
    so_duas = leituras[leituras["Estação"].isin(["A", "B"])]

    serie = interativo.serie_da_chuva_no_ponto(so_duas, ESTACOES_DO_TESTE, -54.5, -20.5,
                                               leituras["dt_local"].min() - pd.Timedelta(hours=1),
                                               leituras["dt_local"].max(), False, (-54.5, -20.5))

    assert serie.empty and list(serie.columns) == ["quando", "chuva", "acumulado"]


def risco_das_quatro(rajada_de_b: float = 20.0) -> pd.DataFrame:
    # A com as três condições, B com nenhuma, C e D com duas
    return ESTACOES_DO_TESTE.assign(TEM_MAX=[33.0, 28.0, 31.0, 29.0], UMD_MIN=[25.0, 40.0, 35.0, 28.0],
                                    rajada_kmh=[35.0, rajada_de_b, 32.0, 31.0])


def test_o_balao_do_risco_na_hora_traz_as_medidas_que_dao_o_nivel_do_mapa():
    grade = interativo.grade_do_ponto(base_de_teste(), resolucao=31)
    estacoes = risco_das_quatro()
    niveis = risco_fogo.grade_da_hora(estacoes, SimpleNamespace(lon_grade=grade.lon, lat_grade=grade.lat))

    celulas = interativo.celulas_do_risco(grade, niveis, "Na hora", estacoes, com_medidas=True)

    assert len(celulas) == grade.dentro.sum()
    np.testing.assert_array_equal(celulas["nivel"], np.ravel(niveis)[celulas["celula"]])   # a cor do mapa
    assert (celulas["detalhe"].str.count("✔") == celulas["nivel"]).all()   # os ✔ somam o nível
    perto_de_a = celulas.iloc[(celulas["Longitude"] + 55.2).abs().add((celulas["Latitude"] + 20.0).abs()).argmin()]
    assert perto_de_a["detalhe"].startswith("Na hora: Risco alto (3 de 3, interpolado)\n✔ Temperatura máxima: ")
    assert perto_de_a["detalhe"].endswith(f"Estação mais próxima: A, a {perto_de_a['km']:.0f} km")


@pytest.mark.parametrize("valor, casas, esperado", [
    (29.96, 1, "29,96"),   # arredondado daria 30,0, que valeria
    (30.4, 0, "30,4"),     # na umidade, 30 valeria
    (30.04, 1, "30,04"),
    (31.26, 1, "31,3"),    # longe do limiar, como sempre
    (30.0, 1, "30,0"),     # em cima do limiar de verdade
])
def test_perto_do_limiar_o_balao_mostra_de_que_lado_o_valor_esta(valor, casas, esperado):
    assert interativo._numero_perto_do_limiar(valor, casas, 30.0) == esperado


def test_por_dia_o_balao_do_risco_traz_as_horas_em_risco_alto():
    grade = interativo.grade_do_ponto(base_de_teste(), resolucao=31)
    niveis = np.full(grade.lon.shape, 3, dtype=np.int8)

    celulas = interativo.celulas_do_risco(grade, niveis, "Pior nível do dia", risco_das_quatro(), com_medidas=False,
                                          horas_alto=np.full(grade.lon.shape, 4))

    assert celulas["detalhe"].str.contains("\nHoras em risco alto: 4\n").all()
    assert not celulas["detalhe"].str.contains("✔|✘").any()


def test_a_serie_do_risco_no_ponto_na_hora_do_mapa_e_o_balao():
    grade = interativo.grade_do_ponto(base_de_teste(), resolucao=31)
    base = SimpleNamespace(lon_grade=grade.lon, lat_grade=grade.lat)
    horas = pd.date_range("2026-10-13 15:00", periods=2, freq="h", tz="UTC")
    # Na segunda hora a rajada de B sobe e entra uma estação a menos (D não mandou)
    medidas = {horas[0]: risco_das_quatro(), horas[1]: risco_das_quatro(rajada_de_b=45.0).iloc[:3]}
    avaliadas = {hora: risco_fogo.HoraAvaliada(risco_fogo.grade_da_hora(estacoes, base), estacoes)
                 for hora, estacoes in medidas.items()}
    for hora in horas:
        celulas = interativo.celulas_do_risco(grade, avaliadas[hora].grade, "Na hora", medidas[hora], com_medidas=True)
        celula = celulas.iloc[len(celulas) // 2]

        serie = interativo.serie_do_risco_no_ponto(avaliadas, float(np.ravel(grade.lon)[celula["celula"]]),
                                                   float(np.ravel(grade.lat)[celula["celula"]]), grade.centro,
                                                   config.FUSO_MS)

        na_hora = serie[serie["dt_local"] == hora.tz_convert(config.FUSO_MS).tz_localize(None)]
        assert len(na_hora) == 3 and set(serie["Medida"]) == set(na_hora["Medida"])
        assert (na_hora["Condição"] == "atendida").sum() == celula["nivel"]   # o clique e o balão dizem o mesmo
