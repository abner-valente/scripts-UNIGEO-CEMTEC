"""Cálculos compartilhados: recorte no tempo, extremos, acumulados e interpolação IDW."""
import numpy as np
import pandas as pd
import pytest

from modulos import calculos


def utc(texto):
    return pd.Timestamp(texto, tz="UTC")


def test_recorte_segue_a_hora_que_termina_na_leitura(serie):
    """A leitura das HH:00 fecha a hora anterior: entra a do fim da janela, não a do início."""
    dados = serie("2026-07-30", "2026-08-01")
    recorte = calculos.recortar(dados, utc("2026-07-30 04:00"), utc("2026-07-31 04:00"))
    assert len(recorte) == 24
    assert recorte["dt_utc"].min() == utc("2026-07-30 05:00")
    assert recorte["dt_utc"].max() == utc("2026-07-31 04:00")


def test_acumulado_de_chuva(serie):
    dados = serie("2026-07-30", "2026-07-31", CHUVA=0.5)
    dados.loc[3, "CHUVA"] = np.nan  # leitura sem dado conta como zero
    # Janela (00 UTC, 12 UTC]: leituras das 01 às 12 UTC, uma delas sem dado
    assert calculos.acumulado_chuva(dados, utc("2026-07-30 00:00"), utc("2026-07-30 12:00")) == 5.5
    assert calculos.acumulado_chuva(dados, utc("2026-08-01"), utc("2026-08-02")) == 0.0


def test_indice_do_extremo_e_data_hora(serie):
    dados = serie("2026-07-30", "2026-07-31")
    dados.loc[18, "TEM_MAX"] = 33.0  # leitura das 18 UTC
    indice = calculos.indice_extremo(dados, "TEM_MAX", minimo=False)
    assert dados.at[indice, "TEM_MAX"] == 33.0
    assert calculos.data_hora(dados, indice) == {"Data/Hora (UTC)": "30/07/2026 18:00", "Data/Hora (MS)": "30/07/2026 14:00"}


def test_extremo_sem_dados_validos(serie):
    dados = serie("2026-07-30", "2026-07-31", TEM_MIN=np.nan)
    assert calculos.indice_extremo(dados, "TEM_MIN", minimo=True) is None
    assert calculos.indice_extremo(dados, "COLUNA_INEXISTENTE", minimo=True) is None


def test_idw_reproduz_o_valor_na_posicao_da_estacao():
    lons, lats, valores = [-55.0, -53.0, -57.0], [-20.0, -22.0, -19.0], [10.0, 30.0, 20.0]
    resultado = calculos.interpolar_idw(lons, lats, valores, np.array([[-55.0, -53.0]]), np.array([[-20.0, -22.0]]))
    np.testing.assert_allclose(resultado, [[10.0, 30.0]])


def test_idw_mede_a_distancia_em_quilometros():
    """Estação A fica 1° a leste do ponto e B 1° ao norte: em graus, a mesma distância. Em MS,
    1° de longitude (~104 km) é mais curto que 1° de latitude (~111 km), então A pesa mais."""
    resultado = calculos.interpolar_idw([-54.0, -55.0], [-21.0, -20.0], [10.0, 0.0], np.array([[-55.0]]), np.array([[-21.0]]))
    assert resultado[0, 0] == pytest.approx(10 * 110.6**2 / (103.9**2 + 110.6**2), abs=0.02)  # ≈ 5,3 (em graus seria 5,0)


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
