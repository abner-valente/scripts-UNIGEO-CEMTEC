"""Janelas de tempo compartilhadas pelos produtos (config.Periodo)."""
from datetime import date, datetime, timedelta

import pytest

from modulos import config
from modulos.config import FUSO_UTC, Periodo


def utc(*partes):
    return datetime(*partes, tzinfo=FUSO_UTC)


def test_datas_iguais_sao_data_especifica():
    periodo = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))
    assert periodo.modo == "dia"
    assert periodo.janela == (utc(2026, 7, 30), utc(2026, 7, 31))
    assert periodo.janela_busca == periodo.janela
    assert periodo.identificador == "20260730"


def test_datas_diferentes_sao_periodo():
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    assert periodo.modo == "periodo"
    assert periodo.num_dias == 31
    assert periodo.ultimo_dia == date(2026, 8, 31)
    assert periodo.janela == (utc(2026, 8, 1), utc(2026, 9, 1))
    assert periodo.identificador == "20260801_a_20260831"


def test_data_final_anterior_a_inicial_e_rejeitada():
    with pytest.raises(ValueError):
        Periodo.de_datas(date(2026, 8, 31), date(2026, 8, 1))


def test_tempo_real():
    agora = utc(2026, 9, 14, 13, 25)
    periodo = Periodo.tempo_real(agora)
    assert periodo.modo == "tempo_real"
    assert periodo.janela == (agora - timedelta(hours=24), agora)
    assert periodo.inicio_do_dia == utc(2026, 9, 14)
    assert periodo.janela_busca == (agora - timedelta(hours=96), agora)
    assert periodo.identificador == "20260914_1325_UTC"


def test_pasta_de_saida_separada_por_produto_e_modo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PASTA_SAIDA", tmp_path)
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    assert periodo.pasta_saida("relatorio_inmet") == tmp_path / "relatorio_inmet" / "periodo" / "20260801_a_20260831"
