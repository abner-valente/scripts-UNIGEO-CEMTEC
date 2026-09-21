"""Janelas de tempo compartilhadas pelos produtos (config.Periodo)."""
from datetime import date, datetime, timedelta

import pytest

from modulos import config
from modulos.config import FUSO_UTC, Periodo


def utc(*partes):
    return datetime(*partes, tzinfo=FUSO_UTC)


def test_datas_iguais_sao_um_dia_no_horario_de_ms():
    periodo = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))
    assert periodo.modo == "dia"
    assert periodo.janela == (utc(2026, 7, 30, 4), utc(2026, 7, 31, 4))  # 00 h às 24 h em MS (UTC−4)
    assert periodo.janela_busca == periodo.janela
    assert periodo.identificador == "20260730"
    assert periodo.descricao == "30/07/2026 (horário de MS)"


def test_datas_diferentes_sao_periodo():
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    assert periodo.modo == "periodo"
    assert (periodo.primeiro_dia, periodo.ultimo_dia, periodo.num_dias) == (date(2026, 8, 1), date(2026, 8, 31), 31)
    assert periodo.janela == (utc(2026, 8, 1, 4), utc(2026, 9, 1, 4))
    assert periodo.identificador == "20260801_a_20260831"
    assert periodo.descricao == "Período: 01/08/2026 a 31/08/2026 (31 dias, horário de MS)"


def test_data_final_anterior_a_inicial_e_rejeitada():
    with pytest.raises(ValueError):
        Periodo.de_datas(date(2026, 8, 31), date(2026, 8, 1))


def test_dias_inteiros_sem_horas():
    periodo = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))
    assert periodo.dias_inteiros and periodo.horas == 24


def test_horarios_definem_a_janela():
    periodo = Periodo.de_datas(date(2026, 9, 14), date(2026, 9, 15), 8, 8)
    assert periodo.modo == "periodo"
    assert periodo.janela == (utc(2026, 9, 14, 12), utc(2026, 9, 15, 12))  # 08 h às 08 h em MS
    assert not periodo.dias_inteiros and periodo.horas == 24
    assert periodo.identificador == "20260914_08h_a_20260915_08h"
    assert periodo.descricao == "14/09/2026 08h a 15/09/2026 08h (24 horas, horário de MS)"


def test_horarios_no_mesmo_dia():
    periodo = Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15), 6, 18)
    assert periodo.modo == "dia"
    assert periodo.horas == 12
    assert periodo.identificador == "20260915_06h_a_20260915_18h"


@pytest.mark.parametrize("horas", [(18, 6), (25, 24), (0, -1)])
def test_horarios_invalidos_sao_rejeitados(horas):
    with pytest.raises(ValueError):
        Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15), *horas)


def test_tempo_real():
    agora = utc(2026, 9, 14, 13, 25)  # 9h25 em MS
    periodo = Periodo.tempo_real(agora)
    assert periodo.modo == "tempo_real"
    assert periodo.janela == (agora - timedelta(hours=24), agora)
    assert periodo.janela_busca == (agora - timedelta(hours=96), agora)
    assert periodo.inicio_do_dia == utc(2026, 9, 14, 4)  # 00 h de hoje em MS
    assert periodo.identificador == "20260914_0925"
    assert periodo.descricao == "Últimas 24 horas: 13/09/2026 09:25 a 14/09/2026 09:25 GMT-04"


def test_hoje_do_tempo_real_segue_o_horario_de_ms():
    """Às 02 UTC ainda são 22 h do dia anterior em MS."""
    assert Periodo.tempo_real(utc(2026, 9, 14, 2, 0)).inicio_do_dia == utc(2026, 9, 13, 4)


@pytest.mark.parametrize("periodo, texto", [
    (Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15)), "15/09/2026"),
    (Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31)), "01/08/2026 a 31/08/2026"),
    (Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15), 6, 18), "15/09/2026 06:00 a 15/09/2026 18:00 GMT-04"),
    (Periodo.tempo_real(utc(2026, 9, 14, 13, 25)), "13/09/2026 09:25 a 14/09/2026 09:25 GMT-04"),
], ids=["dia", "periodo", "com_horas", "tempo_real"])
def test_subtitulo_dos_mapas_traz_o_fuso_so_quando_ha_horario(periodo, texto):
    """Subtítulo igual em todos os produtos: datas sozinhas sem fuso, horários com GMT-04."""
    assert periodo.descrever_janela(*periodo.janela) == texto


def test_pasta_de_saida_separada_por_produto_e_modo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PASTA_SAIDA", tmp_path)
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    assert periodo.pasta_saida("relatorio_inmet") == tmp_path / "relatorio_inmet" / "periodo" / "20260801_a_20260831"
