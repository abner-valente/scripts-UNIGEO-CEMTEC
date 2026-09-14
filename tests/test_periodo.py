"""Janelas de tempo da consulta (config.Periodo) e leitura das datas no main.py."""
from datetime import date, datetime, timedelta

import pytest

import main
from modulos import config
from modulos.config import FUSO_UTC, Periodo


def utc(*partes):
    return datetime(*partes, tzinfo=FUSO_UTC)


def test_datas_iguais_sao_data_especifica():
    periodo = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))
    assert periodo.modo == "dia"
    assert (periodo.inicio, periodo.fim) == (utc(2026, 7, 30), utc(2026, 7, 31))
    assert periodo.identificador == "20260730"
    assert list(periodo.janelas_chuva) == ["Acumulado Dia"]


def test_datas_diferentes_sao_periodo():
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    assert periodo.modo == "periodo"
    assert periodo.num_dias == 31
    assert periodo.ultimo_dia == date(2026, 8, 31)
    assert periodo.fim == utc(2026, 9, 1)
    assert periodo.identificador == "20260801_a_20260831"
    assert list(periodo.janelas_chuva) == ["Acumulado Período"]


def test_data_final_anterior_a_inicial_e_rejeitada():
    with pytest.raises(ValueError):
        Periodo.de_datas(date(2026, 8, 31), date(2026, 8, 1))


def test_tempo_real():
    agora = utc(2026, 9, 14, 13, 25)
    periodo = Periodo.tempo_real(agora)
    assert periodo.modo == "tempo_real"
    assert periodo.janela_extremos == (agora - timedelta(hours=24), agora)
    assert periodo.janela_temp_min == (utc(2026, 9, 14), agora)
    assert periodo.janela_busca == (agora - timedelta(hours=96), agora)
    assert list(periodo.janelas_chuva) == [
        "Chuva Hoje (desde 00h UTC)", "Acumulado 12h", "Acumulado 24h", "Acumulado 48h", "Acumulado 72h",
    ]
    assert periodo.identificador == "20260914_1325_UTC"


def test_pasta_de_saida_separada_por_modo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PASTA_SAIDA", tmp_path)
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    assert periodo.pasta_saida == tmp_path / "periodo" / "20260801_a_20260831"


@pytest.mark.parametrize("argumentos, modo", [
    (["--inicio", "30/07/2026"], "dia"),
    (["--inicio", "2026-08-01", "--fim", "31/08/2026"], "periodo"),
    (["--tempo-real"], "tempo_real"),
])
def test_datas_pela_linha_de_comando(argumentos, modo):
    assert main.definir_periodo(argumentos).modo == modo


def test_sem_argumentos_usa_as_variaveis_do_main(monkeypatch):
    monkeypatch.setattr(main, "DATA_INICIAL", date(2026, 7, 1))
    monkeypatch.setattr(main, "DATA_FINAL", date(2026, 7, 1))
    assert main.definir_periodo([]).identificador == "20260701"

    monkeypatch.setattr(main, "DATA_INICIAL", None)
    monkeypatch.setattr(main, "DATA_FINAL", None)
    assert main.definir_periodo([]).modo == "tempo_real"


@pytest.mark.parametrize("argumentos", [
    ["--inicio", "31/02/2026"],
    ["--inicio", "31/08/2026", "--fim", "01/08/2026"],
])
def test_datas_invalidas_na_linha_de_comando(argumentos):
    with pytest.raises(SystemExit):
        main.definir_periodo(argumentos)
