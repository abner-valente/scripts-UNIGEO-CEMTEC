"""Execução completa do main.py (coleta, Excel e mapas) com a API do INMET simulada."""
from datetime import date, datetime

import pandas as pd
import pytest

import main
from modulos import config, inmet
from modulos.config import FUSO_UTC, Periodo

COLUNAS_TEMPO_REAL = ["Chuva Hoje (desde 00h UTC)", "Acumulado 12h", "Acumulado 24h", "Acumulado 48h", "Acumulado 72h"]


@pytest.mark.parametrize("periodo, colunas_chuva, mapas_esperados", [
    (Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30)), ["Acumulado Dia"], 11),
    (Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 3)), ["Acumulado Período"], 11),
    (Periodo.tempo_real(datetime(2026, 9, 14, 13, 25, tzinfo=FUSO_UTC)), COLUNAS_TEMPO_REAL, 13),
], ids=["dia", "periodo", "tempo_real"])
def test_execucao_completa(api_simulada, periodo, colunas_chuva, mapas_esperados):
    assert main.executar(periodo) == 0

    pasta = periodo.pasta_saida
    abas = pd.read_excel(pasta / f"Relatorio_{config.UF}_{periodo.identificador}.xlsx", sheet_name=None)
    assert list(abas) == ["Temp_Min", "Temp_Max", "Umidade", "Vento", "Chuva"]
    assert all(len(aba) == api_simulada.estacoes_com_dados for aba in abas.values())
    assert [c for c in abas["Chuva"].columns if c not in ("Estação", "Latitude", "Longitude")] == colunas_chuva
    assert len(list((pasta / "mapas").glob("*.png"))) == mapas_esperados


def test_sem_token_para_antes_de_chamar_a_api(api_simulada, monkeypatch):
    def api_proibida(*args, **kwargs):
        raise AssertionError("a API foi chamada sem token")

    monkeypatch.setattr(config, "TOKEN_INMET", "")
    monkeypatch.setattr(inmet, "listar_estacoes", api_proibida)
    assert main.executar(Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))) == 1
