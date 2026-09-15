"""Extremos, acumulados de chuva, montagem das tabelas e interpolação IDW."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from modulos import calculos
from modulos.config import FUSO_UTC, Periodo

ESTACAO = pd.Series({"Estação": "Teste", "VL_LATITUDE": -20.0, "VL_LONGITUDE": -55.0})
DIA = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))


def test_extremos_e_chuva_ficam_restritos_ao_periodo(serie):
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    dados = serie("2026-07-31", "2026-09-02")
    fora = (dados["dt_utc"] < periodo.inicio) | (dados["dt_utc"] >= periodo.fim)
    dados.loc[fora, ["TEM_MIN", "TEM_MAX", "VEN_RAJ", "CHUVA"]] = [-5.0, 45.0, 40.0, 100.0]
    dados.loc[dados["dt_utc"] == pd.Timestamp("2026-08-15 18:00", tz="UTC"), "TEM_MAX"] = 38.5

    resumo = calculos.resumir_estacao(dados, ESTACAO, periodo)

    assert resumo["Temp_Max"]["Temperatura Máxima (°C)"] == 38.5
    assert resumo["Temp_Min"]["Temperatura Mínima (°C)"] == 20.0
    assert resumo["Vento"]["Rajada (km/h)"] == 18.0
    assert resumo["Chuva"]["Acumulado Período"] == 31 * 24 * 1.0


def test_horario_do_extremo_em_utc_e_em_ms(serie):
    dados = serie("2026-07-30", "2026-07-31")
    dados.loc[dados["dt_utc"] == pd.Timestamp("2026-07-30 18:00", tz="UTC"), "TEM_MAX"] = 33.0
    linha = calculos.resumir_estacao(dados, ESTACAO, DIA)["Temp_Max"]
    assert linha["Data/Hora (UTC)"] == "30/07/2026 18:00"
    assert linha["Data/Hora (MS)"] == "30/07/2026 14:00"


def test_rajada_em_km_h_com_a_direcao_do_mesmo_horario(serie):
    dados = serie("2026-07-30", "2026-07-31")
    dados.loc[10, ["VEN_RAJ", "VEN_DIR"]] = [20.0, 225.0]
    linha = calculos.resumir_estacao(dados, ESTACAO, DIA)["Vento"]
    assert linha["Rajada (km/h)"] == 72.0
    assert linha["Direção (°)"] == 225.0


def test_chuva_do_dia_soma_os_24_registros(serie):
    dados = serie("2026-07-29", "2026-08-01")
    assert calculos.resumir_estacao(dados, ESTACAO, DIA)["Chuva"]["Acumulado Dia"] == 24.0


def test_acumulados_de_chuva_do_tempo_real(serie):
    agora = datetime(2026, 9, 14, 13, 25, tzinfo=FUSO_UTC)
    dados = serie("2026-09-10", "2026-09-14 14:00")  # 1 mm por hora; última leitura às 13 UTC
    chuva = calculos.resumir_estacao(dados, ESTACAO, Periodo.tempo_real(agora))["Chuva"]
    assert chuva["Chuva Hoje (desde 00h UTC)"] == 14.0  # leituras das 00 às 13 UTC
    assert chuva["Acumulado 12h"] == 12.0
    assert chuva["Acumulado 24h"] == 24.0
    assert chuva["Acumulado 48h"] == 48.0
    assert chuva["Acumulado 72h"] == 72.0


def test_variavel_sem_dados_validos_fica_fora_do_resumo(serie):
    dados = serie("2026-07-30", "2026-07-31", TEM_MIN=np.nan)
    resumo = calculos.resumir_estacao(dados, ESTACAO, DIA)
    assert "Temp_Min" not in resumo
    assert {"Temp_Max", "Umidade", "Vento", "Chuva"} <= set(resumo)


def test_tabelas_ordenadas_pelo_valor(serie):
    resumos = []
    for nome, minima in (("A", 15.0), ("B", 10.0), ("C", 12.0)):
        dados = serie("2026-07-30", "2026-07-31", TEM_MIN=minima, TEM_MAX=minima + 10)
        estacao = pd.Series({"Estação": nome, "VL_LATITUDE": -20.0, "VL_LONGITUDE": -55.0})
        resumos.append(calculos.resumir_estacao(dados, estacao, DIA))

    tabelas = calculos.montar_tabelas(resumos, DIA)

    assert list(tabelas) == ["Temp_Min", "Temp_Max", "Umidade", "Vento", "Chuva"]
    assert tabelas["Temp_Min"]["Estação"].tolist() == ["B", "C", "A"]
    assert tabelas["Temp_Max"]["Estação"].tolist() == ["A", "C", "B"]


def test_idw_reproduz_o_valor_na_posicao_da_estacao():
    lons, lats, valores = [-55.0, -53.0, -57.0], [-20.0, -22.0, -19.0], [10.0, 30.0, 20.0]
    resultado = calculos.interpolar_idw(lons, lats, valores, np.array([[-55.0, -53.0]]), np.array([[-20.0, -22.0]]))
    np.testing.assert_allclose(resultado, [[10.0, 30.0]])


def test_idw_fica_entre_o_menor_e_o_maior_valor_das_estacoes():
    rng = np.random.default_rng(0)
    lons, lats, valores = rng.uniform(-58, -51, 20), rng.uniform(-24, -17.5, 20), rng.uniform(0, 40, 20)
    lon_grade, lat_grade = calculos.criar_grade((-58.5, -50.5, -24.5, -17.0), 50)
    resultado = calculos.interpolar_idw(lons, lats, valores, lon_grade, lat_grade)
    assert resultado.shape == (50, 50)
    assert valores.min() <= resultado.min() and resultado.max() <= valores.max()


def test_idw_de_campo_constante_e_constante():
    lon_grade, lat_grade = calculos.criar_grade((-58.5, -50.5, -24.5, -17.0), 20)
    resultado = calculos.interpolar_idw([-55, -53, -57, -54], [-20, -22, -19, -23], [7.0] * 4, lon_grade, lat_grade)
    assert resultado == pytest.approx(np.full((20, 20), 7.0))
