"""As contas da página Raios: a idade dos flashes, a contagem a cada 5 minutos e a densidade por km²."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
import shapely

from app import raios
from modulos import config, fonte

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
