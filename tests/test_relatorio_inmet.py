"""Produto relatorio_inmet: janelas de tempo, cálculos por estação, tabelas e execução completa."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from modulos import config, inmet
from modulos.config import FUSO_UTC, Periodo
from modulos.produtos import relatorio_inmet

ESTACAO = pd.Series({"Estação": "Teste", "VL_LATITUDE": -20.0, "VL_LONGITUDE": -55.0})
DIA = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))
PERIODO = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
AGORA = datetime(2026, 9, 14, 13, 25, tzinfo=FUSO_UTC)  # 9h25 em MS
TEMPO_REAL = Periodo.tempo_real(AGORA)
COLUNAS_TEMPO_REAL = ["Chuva Hoje (desde 00h MS)", "Acumulado 12h", "Acumulado 24h", "Acumulado 48h", "Acumulado 72h"]


def utc(texto):
    return pd.Timestamp(texto, tz="UTC")


# ---------- Janelas de tempo ----------

def test_colunas_de_chuva_de_cada_modo():
    assert list(relatorio_inmet.janelas_chuva(DIA)) == ["Acumulado Dia"]
    assert list(relatorio_inmet.janelas_chuva(PERIODO)) == ["Acumulado Período"]
    assert list(relatorio_inmet.janelas_chuva(TEMPO_REAL)) == COLUNAS_TEMPO_REAL


# ---------- Cálculos por estação ----------

def test_dia_no_horario_de_ms(serie):
    """Dia 30/07 em MS = leituras das 05 UTC de 30/07 às 04 UTC de 31/07 (cada leitura fecha a hora anterior)."""
    dados = serie("2026-07-29", "2026-08-01", CHUVA=0.0)
    dados.loc[dados["dt_utc"] == utc("2026-07-30 04:00"), "CHUVA"] = 50.0  # 23 h–24 h de 29/07 em MS: fica de fora
    dados.loc[dados["dt_utc"] == utc("2026-07-30 05:00"), "CHUVA"] = 3.0   # 0 h–1 h de 30/07 em MS
    dados.loc[dados["dt_utc"] == utc("2026-07-31 04:00"), "CHUVA"] = 7.0   # 23 h–24 h de 30/07 em MS
    assert relatorio_inmet.resumir_estacao(dados, ESTACAO, DIA)["Chuva"]["Acumulado Dia"] == 10.0


def test_consulta_com_horarios(serie):
    """Das 08 h de 14/09 às 08 h de 15/09 em MS = leituras das 13 UTC de 14/09 às 12 UTC de 15/09."""
    periodo = Periodo.de_datas(date(2026, 9, 14), date(2026, 9, 15), 8, 8)
    dados = serie("2026-09-14", "2026-09-16", CHUVA=0.0)
    dados.loc[dados["dt_utc"] == utc("2026-09-14 12:00"), "CHUVA"] = 50.0  # 07 h–08 h de 14/09: fica de fora
    dados.loc[dados["dt_utc"] == utc("2026-09-14 13:00"), "CHUVA"] = 2.0   # 08 h–09 h de 14/09
    dados.loc[dados["dt_utc"] == utc("2026-09-15 12:00"), "CHUVA"] = 5.0   # 07 h–08 h de 15/09
    assert relatorio_inmet.resumir_estacao(dados, ESTACAO, periodo)["Chuva"]["Acumulado Período"] == 7.0


def test_dia_com_horarios_mostra_a_duracao_em_horas():
    periodo = Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15), 6, 18)
    assert list(relatorio_inmet.janelas_chuva(periodo)) == ["Acumulado Período"]
    titulos = [e.titulo for e in relatorio_inmet.especificacoes_mapas(periodo) if e.tabela == "Chuva"]
    assert titulos == ["Chuva acumulada em 12 horas - Mato Grosso do Sul"]


def test_chuva_do_dia_soma_24_leituras(serie):
    dados = serie("2026-07-29", "2026-08-01")
    assert relatorio_inmet.resumir_estacao(dados, ESTACAO, DIA)["Chuva"]["Acumulado Dia"] == 24.0


def test_extremos_e_chuva_ficam_restritos_ao_periodo(serie):
    dados = serie("2026-07-31", "2026-09-02")
    fora = (dados["dt_utc"] <= PERIODO.inicio) | (dados["dt_utc"] > PERIODO.fim)
    dados.loc[fora, ["TEM_MIN", "TEM_MAX", "VEN_RAJ", "CHUVA"]] = [-5.0, 45.0, 40.0, 100.0]
    dados.loc[dados["dt_utc"] == utc("2026-08-15 18:00"), "TEM_MAX"] = 38.5

    resumo = relatorio_inmet.resumir_estacao(dados, ESTACAO, PERIODO)

    assert resumo["Temp_Max"]["Temperatura Máxima (°C)"] == 38.5
    assert resumo["Temp_Max"]["Data/Hora (MS)"] == "15/08/2026 14:00"
    assert resumo["Temp_Min"]["Temperatura Mínima (°C)"] == 20.0
    assert resumo["Vento"]["Rajada (km/h)"] == 18.0
    assert resumo["Chuva"]["Acumulado Período"] == 31 * 24 * 1.0


def test_temperatura_minima_do_tempo_real_usa_as_ultimas_24_horas(serie):
    dados = serie("2026-09-10", "2026-09-14 14:00")
    dados.loc[dados["dt_utc"] == utc("2026-09-13 20:00"), "TEM_MIN"] = 5.0  # há 17 h: dentro das últimas 24 h
    dados.loc[dados["dt_utc"] == utc("2026-09-13 10:00"), "TEM_MIN"] = 1.0  # há 27 h: fora
    linha = relatorio_inmet.resumir_estacao(dados, ESTACAO, TEMPO_REAL)["Temp_Min"]
    assert linha["Temperatura Mínima (°C)"] == 5.0
    assert linha["Data/Hora (MS)"] == "13/09/2026 16:00"


def test_acumulados_de_chuva_do_tempo_real(serie):
    dados = serie("2026-09-10", "2026-09-14 14:00")  # 1 mm por hora; última leitura às 13 UTC (9 h em MS)
    chuva = relatorio_inmet.resumir_estacao(dados, ESTACAO, TEMPO_REAL)["Chuva"]
    assert chuva["Chuva Hoje (desde 00h MS)"] == 9.0  # leituras das 05 às 13 UTC (0 h às 9 h em MS)
    assert chuva["Acumulado 12h"] == 12.0
    assert chuva["Acumulado 24h"] == 24.0
    assert chuva["Acumulado 48h"] == 48.0
    assert chuva["Acumulado 72h"] == 72.0


def test_rajada_em_km_h_com_a_direcao_do_mesmo_horario(serie):
    dados = serie("2026-07-30", "2026-07-31")
    dados.loc[10, ["VEN_RAJ", "VEN_DIR"]] = [20.0, 225.0]
    linha = relatorio_inmet.resumir_estacao(dados, ESTACAO, DIA)["Vento"]
    assert linha["Rajada (km/h)"] == 72.0
    assert linha["Direção (°)"] == 225.0


def test_variavel_sem_dados_validos_fica_fora_do_resumo(serie):
    dados = serie("2026-07-30", "2026-07-31", TEM_MIN=np.nan)
    resumo = relatorio_inmet.resumir_estacao(dados, ESTACAO, DIA)
    assert "Temp_Min" not in resumo
    assert {"Temp_Max", "Umidade", "Vento", "Chuva"} <= set(resumo)


def test_tabelas_ordenadas_pelo_valor(serie):
    resumos = []
    for nome, minima in (("A", 15.0), ("B", 10.0), ("C", 12.0)):
        dados = serie("2026-07-30", "2026-07-31", TEM_MIN=minima, TEM_MAX=minima + 10)
        estacao = pd.Series({"Estação": nome, "VL_LATITUDE": -20.0, "VL_LONGITUDE": -55.0})
        resumos.append(relatorio_inmet.resumir_estacao(dados, estacao, DIA))

    tabelas = relatorio_inmet.montar_tabelas(resumos, DIA)

    assert list(tabelas) == ["Temp_Min", "Temp_Max", "Umidade", "Vento", "Chuva"]
    assert tabelas["Temp_Min"]["Estação"].tolist() == ["B", "C", "A"]
    assert tabelas["Temp_Max"]["Estação"].tolist() == ["A", "C", "B"]


# ---------- Execução completa ----------

@pytest.mark.parametrize("periodo, colunas_chuva, mapas_esperados", [
    (DIA, ["Acumulado Dia"], 11),
    (Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 3)), ["Acumulado Período"], 11),
    (Periodo.de_datas(date(2026, 9, 14), date(2026, 9, 15), 8, 8), ["Acumulado Período"], 11),
    (TEMPO_REAL, COLUNAS_TEMPO_REAL, 15),
], ids=["dia", "periodo", "periodo_com_horas", "tempo_real"])
def test_execucao_completa(api_simulada, periodo, colunas_chuva, mapas_esperados):
    assert relatorio_inmet.executar(periodo) == 0

    pasta = periodo.pasta_saida(relatorio_inmet.NOME)
    abas = pd.read_excel(pasta / f"Relatorio_{config.UF}_{periodo.identificador}.xlsx", sheet_name=None)
    assert list(abas) == ["Temp_Min", "Temp_Max", "Umidade", "Vento", "Chuva"]
    assert all(len(aba) == api_simulada.estacoes_com_dados for aba in abas.values())
    assert [c for c in abas["Chuva"].columns if c not in ("Estação", "Latitude", "Longitude")] == colunas_chuva
    assert len(list((pasta / "mapas").glob("*.png"))) == mapas_esperados


def test_falha_ao_listar_as_estacoes(api_simulada, monkeypatch):
    def fora_do_ar(uf=config.UF):
        raise inmet.ErroINMET("API fora do ar")

    monkeypatch.setattr(inmet, "listar_estacoes", fora_do_ar)
    assert relatorio_inmet.executar(DIA) == 1


def test_nenhuma_estacao_com_dados_interrompe_o_relatorio(api_simulada, monkeypatch):
    """Sem nenhuma estação (token errado, INMET instável) não faz sentido gerar planilha e mapas vazios."""
    monkeypatch.setattr(inmet, "baixar_estacoes", lambda inicio, fim, uf=config.UF: [])
    assert relatorio_inmet.executar(DIA) == 1
