"""As contas da página Raios: a idade dos flashes, a contagem a cada 5 minutos e a densidade por km²."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
import shapely

from app import raios
from modulos import calculos, config, fonte

AGORA = datetime(2026, 11, 18, 17, 2, 30, tzinfo=timezone.utc)
MS = config.FUSO_MS
# Um quadrado no meio de MS, para o "dentro do estado"
ESTADO = shapely.box(-56.0, -22.0, -53.0, -19.0)


def flashes(*idades_e_lugares) -> pd.DataFrame:
    # Cada flash: (minutos antes de AGORA, longitude, latitude)
    return pd.DataFrame({"hora_utc": [pd.Timestamp(AGORA) - pd.Timedelta(minutes=m) for m, _, _ in idades_e_lugares],
                         "longitude": [lon for _, lon, _ in idades_e_lugares],
                         "latitude": [lat for _, _, lat in idades_e_lugares]})


def test_a_cor_de_cada_idade():
    assert [raios.cor_da_idade(m) for m in (0, 4.9, 5, 14, 29, 59, 60)] == [
        "#fff200", "#fff200", "#ff9900", "#ff9900", "#ff2a00", "#a3007d", None]
    assert [rotulo for _, rotulo in raios.rotulos_das_idades()] == [
        "até 5 min", "5 a 15 min", "15 a 30 min", "30 a 60 min"]


def test_os_pontos_da_janela_vem_do_mais_velho_ao_mais_novo():
    pontos = raios.pontos_por_idade(flashes((2, -54.6, -20.4), (40, -54.6, -20.4), (0.5, -55, -21), (70, -55, -21)),
                                    AGORA, 60, MS)
    assert pontos["idade"].round(1).tolist() == [40.0, 2.0, 0.5]
    assert pontos["cor"].tolist() == [[163, 0, 125, 255], [255, 242, 0, 255], [255, 242, 0, 255]]
    # 17:02:30 UTC é 13:02:30 em MS
    assert pontos["titulo"].tolist() == ["Flash às 12:22:30", "Flash às 13:00:30", "Flash às 13:02:00"]
    assert pontos["detalhe"].tolist() == ["há 40 min", "há 2 min", "há menos de 1 min"]
    assert len(raios.pontos_por_idade(flashes((2, -54.6, -20.4), (20, -54.6, -20.4)), AGORA, 15, MS)) == 1


def test_dentro_do_estado():
    tabela = flashes((1, -54.6, -20.4), (1, -57.5, -20.4), (1, -54.0, -23.0))
    assert raios.no_estado(tabela, ESTADO).tolist() == [True, False, False]
    assert raios.no_estado(tabela.iloc[:0], ESTADO).tolist() == []


def test_a_contagem_a_cada_5_minutos_escreve_as_barras_vazias():
    barras = raios.por_barra(flashes((0.5, -54.6, -20.4), (1, -54.6, -20.4), (12, -54.6, -20.4)), AGORA, MS, horas=1)
    assert len(barras) == 12
    # No horário de MS, sem o fuso: a última barra é a das 13:00 (em andamento)
    assert barras["inicio"].iloc[-1] == pd.Timestamp("2026-11-18 13:00")
    assert barras["inicio"].dt.tz is None
    assert barras.set_index("inicio")["flashes"].loc[[pd.Timestamp("2026-11-18 13:00"),
                                                       pd.Timestamp("2026-11-18 12:50"),
                                                       pd.Timestamp("2026-11-18 12:45")]].tolist() == [2, 1, 0]


def test_a_area_da_celula_encolhe_para_o_sul():
    centro, sul = raios.area_da_celula_km2([-205, -240])
    assert centro == pytest.approx(11.13 * 11.13 * np.cos(np.radians(-20.45)), rel=0.01)
    assert sul < centro


def test_a_densidade_e_a_matriz_da_caixa():
    por_celula = pd.DataFrame({"lon_celula": [-546, -592, -500], "lat_celula": [-205, -251, -162],
                               "flashes": [116, 1, 3]})
    tabela = raios.densidade(por_celula)
    assert tabela.loc[0, ["Longitude", "Latitude"]].tolist() == pytest.approx([-54.55, -20.45])
    assert tabela.loc[0, "densidade"] == pytest.approx(116 / raios.area_da_celula_km2(-205))
    lon, lat, valores = raios.matriz(tabela)
    # A caixa de -59,2 a -49,9 e de -25,1 a -16,1: 93 × 90 células, as bordas com uma a mais
    assert valores.shape == (90, 93)
    assert (lon[0], lon[-1], lat[0], lat[-1]) == pytest.approx((-59.2, -49.9, -25.1, -16.1))
    assert valores[-205 + 251, -546 + 592] == pytest.approx(tabela.loc[0, "densidade"])
    assert valores[0, 0] > 0 and valores[-1, -1] > 0
    assert np.count_nonzero(valores) == 3


def test_a_celula_fora_da_caixa_nao_quebra_a_matriz():
    por_celula = pd.DataFrame({"lon_celula": [-700], "lat_celula": [-205], "flashes": [5]})
    assert np.count_nonzero(raios.matriz(raios.densidade(por_celula))[2]) == 0


def test_as_classes_da_densidade():
    assert raios.classe([0, 0.001, 0.02, 0.021, 1, 10.5, 20, 500]).tolist() == [-1, 0, 0, 1, 5, 9, 9, 9]
    rotulos = raios.rotulos_das_classes()
    assert len(rotulos) == len(raios.CORES_DENSIDADE)
    assert rotulos[0] == "até 0,02" and rotulos[1] == "0,02 a 0,05" and rotulos[-1] == "acima de 10"


def test_as_celulas_do_mapa_navegavel():
    celulas = raios.celulas_do_mapa(pd.DataFrame({"lon_celula": [-546, -547], "lat_celula": [-205, -205],
                                                  "flashes": [1, 300]}))
    assert celulas["titulo"].tolist() == ["1 flash", "300 flashes"]
    assert celulas["detalhe"][0].startswith("0,01 flashes/km²\nCélula de 0,1° em 20,45° S, 54,55° O")
    assert celulas["cor"][1][3] == 200


def test_as_horas_que_faltam_na_contagem():
    inicio = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
    fim = datetime(2026, 11, 18, 14, tzinfo=timezone.utc)
    cobertura = pd.DataFrame({"hora_utc": pd.to_datetime(["2026-11-18 12:00", "2026-11-18 13:00",
                                                          "2026-11-18 14:00"], utc=True),
                              "arquivos": [180, 177, 40], "sem_arquivo": [0, 1, 0], "flashes": [1, 1, 1]})
    # As 11:00 não estão; as 13:00 perderam 2 arquivos; as 14:00 têm 40
    assert raios.horas_que_faltam(cobertura, inicio, fim) == {"esperadas": 4, "ausentes": 1, "faltando": 142,
                                                              "previstos": 540, "ultima": None}
    # Com a última hora ainda chegando, ela fica à parte
    assert raios.horas_que_faltam(cobertura, inicio, fim, chegando=True) == {
        "esperadas": 4, "ausentes": 1, "faltando": 2, "previstos": 360, "ultima": 40}
    # Uma última hora completa entra com as outras
    assert raios.horas_que_faltam(cobertura, inicio, fim - timedelta(hours=2), chegando=True) == {
        "esperadas": 2, "ausentes": 1, "faltando": 0, "previstos": 180, "ultima": None}
    assert raios.horas_que_faltam(cobertura.iloc[:0], inicio, fim, chegando=True) == {
        "esperadas": 4, "ausentes": 4, "faltando": 0, "previstos": 0, "ultima": None}


def test_o_total_no_estado_e_os_pixels_do_geotiff():
    por_celula = pd.DataFrame({"lon_celula": [-546, -580], "lat_celula": [-205, -205], "flashes": [10, 7]})
    assert raios.total_no_estado(por_celula, ESTADO) == 10
    assert raios.total_no_estado(por_celula.iloc[:0], ESTADO) == 0
    lon, lat = np.meshgrid([-54.545, -54.455, -60.005], [-20.405])
    campo = raios.nos_pixels(por_celula, lon, lat)
    assert campo[0, 0] == pytest.approx(10 / raios.area_da_celula_km2(-205))
    assert campo[0, 1] == 0
    assert np.isnan(campo[0, 2])


def test_sem_banco_a_fonte_nao_tem_raios():
    # Os testes rodam com a fonte "apis" (conftest): no Streamlit Cloud a página nem aparece
    assert not fonte.tem_raios()
    recentes, ultimo = fonte.raios_recentes(AGORA, AGORA)
    assert recentes.empty and ultimo is None
    por_celula, cobertura = fonte.raios_do_periodo(AGORA, AGORA)
    assert list(por_celula.columns) == fonte.COLUNAS_RAIOS_POR_CELULA and cobertura.empty
    assert fonte.alcance_dos_raios() == (None, None)
    assert list(fonte.celulas_do_circulo(AGORA, AGORA, [5, 60]).columns) == fonte.COLUNAS_CELULAS_DO_CIRCULO
    assert fonte.raios_do_circulo(AGORA, AGORA).empty


# =====================================================
# O CÍRCULO DE 2.000 KM DE CAMPO GRANDE
# =====================================================
def test_os_flashes_da_ultima_hora_em_quadradinhos_por_faixa_de_idade():
    celulas = raios.em_celulas(flashes((1, -54.55, -20.45), (3, -54.56, -20.44), (10, -54.55, -20.45),
                                       (70, -54.55, -20.45), (-1, -54.55, -20.45)), AGORA)
    # As de 70 min (fora da hora) e a do futuro ficam de fora
    assert {(l.lon_celula, l.lat_celula, l.faixa): l.flashes for l in celulas.itertuples()} == {
        (-546, -205, 0): 2, (-546, -205, 1): 1}
    assert celulas.loc[celulas["faixa"] == 0, "mais_novo"].iloc[0] == pd.Timestamp(AGORA) - pd.Timedelta(minutes=1)
    assert list(raios.faixa_da_idade([0, 4.9, 5, 14.9, 15, 29, 30, 59])) == [0, 0, 1, 1, 2, 2, 3, 3]
    assert list(raios.em_celulas(flashes((1, -54.5, -20.5)).iloc[:0], AGORA).columns) == raios.COLUNAS_DAS_CELULAS


def test_cada_quadradinho_soma_as_faixas_da_janela_e_tem_a_cor_do_mais_novo():
    do_banco = pd.DataFrame({"lon_celula": [-600], "lat_celula": [-300], "faixa": [3], "flashes": [4],
                             "mais_novo": [pd.Timestamp(AGORA) - pd.Timedelta(minutes=40)]})
    celulas = pd.concat([raios.em_celulas(flashes((1, -54.55, -20.45), (10, -54.55, -20.45)), AGORA), do_banco])
    quinze = raios.celulas_da_janela(celulas, AGORA, 15, MS)
    assert len(quinze) == 1
    assert (quinze.loc[0, "Longitude"], quinze.loc[0, "Latitude"], quinze.loc[0, "flashes"]) == (-54.55, -20.45, 2)
    assert quinze.loc[0, "cor"] == [255, 242, 0, 230]
    assert quinze.loc[0, "titulo"] == "2 flashes"
    assert quinze.loc[0, "detalhe"] == "o mais novo às 13:01:30 (há 1 min)\nCélula de 0,1° em 20,45° S, 54,55° O"
    hora = raios.celulas_da_janela(celulas, AGORA, 60, MS)
    # O mais velho primeiro, como os pontos
    assert list(hora["titulo"]) == ["4 flashes", "2 flashes"]
    assert hora.loc[0, "cor"] == [163, 0, 125, 230]
    assert raios.celulas_da_janela(celulas.iloc[:0], AGORA, 60, MS).empty


def test_no_circulo_o_estado_vai_um_a_um_e_o_resto_em_quadradinhos(monkeypatch):
    caixa = flashes((2, -54.5, -20.5), (2, -57.55, -20.45), (20, -57.55, -20.45))
    dentro = raios.no_estado(caixa, ESTADO)
    de_fora = pd.DataFrame({"lon_celula": [-600], "lat_celula": [-300], "faixa": [1], "flashes": [5],
                            "mais_novo": [pd.Timestamp(AGORA) - pd.Timedelta(minutes=6)]})
    pontos, celulas, muitos = raios.agora_no_circulo(caixa, dentro, de_fora, AGORA, 60, MS)
    assert (len(pontos), muitos) == (1, False)
    assert {(l.lon_celula, l.lat_celula): l.flashes for l in celulas.itertuples()} == {(-576, -205): 2, (-600, -300): 5}
    pontos, celulas, _ = raios.agora_no_circulo(caixa, dentro, de_fora, AGORA, 15, MS)
    assert len(pontos) + celulas["flashes"].sum() == 7
    # Com flashes demais no estado, ele também vai em quadradinhos, e nenhum flash se perde
    monkeypatch.setattr(raios, "LIMITE_DE_PONTOS", 0)
    pontos, celulas, muitos = raios.agora_no_circulo(caixa, dentro, de_fora, AGORA, 60, MS)
    assert (len(pontos), muitos, celulas["flashes"].sum()) == (0, True, 8)
    vazio = raios.agora_no_circulo(caixa.iloc[:0], dentro[:0], de_fora.iloc[:0], AGORA, 60, MS)
    assert vazio[0].empty and vazio[1].empty and not vazio[2]


def test_os_aneis_de_distancia_em_volta_de_campo_grande():
    aneis = raios.aneis()
    assert list(aneis["km"]) == list(config.ANEIS_DOS_RAIOS_KM)
    assert list(aneis["rotulo"]) == ["500 km", "1.000 km", "1.500 km", "2.000 km"]
    lon, lat = config.CENTRO_DOS_RAIOS
    for anel in aneis.itertuples():
        caminho = np.asarray(anel.caminho)
        distancias = calculos.distancia_km(lon, lat, caminho[:, 0], caminho[:, 1])
        assert distancias == pytest.approx(anel.km, abs=0.05)
        assert caminho[0].tolist() == caminho[-1].tolist()          # fecha o anel
        assert anel.lat_rotulo == pytest.approx(caminho[:, 1].min())   # o rótulo vai no sul
    assert raios.em_km(500) == "500 km"


def test_o_quadro_do_png_e_o_meio_da_tela_do_circulo():
    oeste, leste, sul, norte = raios.quadro_do_circulo(margem=1.0)
    c_oeste, c_sul, c_leste, c_norte = calculos.caixa_do_circulo(config.CENTRO_DOS_RAIOS, config.RAIO_DOS_RAIOS_KM)
    assert (oeste, leste, sul, norte) == pytest.approx((c_oeste - 1, c_leste + 1, c_sul - 1, c_norte + 1))
    assert raios.latitude_do_meio(-10, 10) == pytest.approx(0)
    # No Mercator o sul estica mais: o meio da tela fica ao sul do centro do círculo
    assert c_sul < raios.latitude_do_meio(c_sul, c_norte) < config.CENTRO_DOS_RAIOS[1]


def test_os_flashes_da_janela_para_o_png():
    janela = raios.flashes_da_janela(flashes((1, -54.5, -20.5), (20, -60.0, -30.0), (70, -54.5, -20.5)), AGORA, 30)
    assert list(janela.columns) == ["longitude", "latitude", "idade"]
    assert janela["idade"].round(3).tolist() == [1.0, 20.0]
    assert raios.flashes_da_janela(flashes((1, -54.5, -20.5)).iloc[:0], AGORA, 30).empty
