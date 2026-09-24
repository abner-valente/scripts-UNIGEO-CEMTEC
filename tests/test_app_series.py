"""Séries do explorador: resumo por dia respeitando o fechamento do dia em MS."""
import pandas as pd
import pytest

from app import series


def leituras(estacao: str, inicio: str, horas: int, valores) -> pd.DataFrame:
    """Leituras horárias de uma estação a partir de `inicio` (horário de MS)."""
    marcas = pd.date_range(inicio, periods=horas, freq="h", tz="America/Campo_Grande")
    return pd.DataFrame({"Estação": estacao, "dt_local": marcas, "TEM_INS": valores})


def test_hora_a_hora_mantem_uma_linha_por_leitura():
    tabela = leituras("Bonito", "2026-09-17 01:00", 24, range(24))
    serie = series.agregar(tabela, "TEM_INS", por_dia=False, funcao="Média")
    assert len(serie) == 24
    assert list(serie.columns) == ["Bonito"]


def test_periodo_de_sete_dias_resume_em_sete_dias():
    """A leitura das 00:00 fecha o dia anterior: sem isso, 7 dias virariam 8."""
    tabela = leituras("Bonito", "2026-09-17 01:00", 24 * 7, range(24 * 7))

    serie = series.agregar(tabela, "TEM_INS", por_dia=True, funcao="Média")

    assert len(serie) == 7
    assert [f"{dia:%d/%m}" for dia in serie.index] == [f"{17 + n}/09" for n in range(7)]


def test_meia_noite_entra_no_dia_que_termina():
    """A das 00:00 de 18/09 cobre das 23:00 às 00:00, ou seja, é a última hora do dia 17."""
    tabela = leituras("Bonito", "2026-09-17 23:00", 2, [10.0, 30.0])

    serie = series.agregar(tabela, "TEM_INS", por_dia=True, funcao="Máxima")

    assert len(serie) == 1
    assert f"{serie.index[0]:%d/%m}" == "17/09"
    assert serie.iloc[0]["Bonito"] == pytest.approx(30.0)


def test_soma_do_dia_para_chuva():
    tabela = leituras("Bonito", "2026-09-17 01:00", 24, [1.0] * 24).rename(columns={"TEM_INS": "CHUVA"})
    serie = series.agregar(tabela, "CHUVA", por_dia=True, funcao="Soma")
    assert serie.iloc[0]["Bonito"] == pytest.approx(24.0)


def test_estacoes_viram_colunas_lado_a_lado():
    tabela = pd.concat([leituras("Bonito", "2026-09-17 01:00", 24, range(24)),
                        leituras("Corumba", "2026-09-17 01:00", 24, range(100, 124))], ignore_index=True)

    serie = series.agregar(tabela, "TEM_INS", por_dia=True, funcao="Média")

    assert list(serie.columns) == ["Bonito", "Corumba"]
    assert serie.iloc[0]["Corumba"] - serie.iloc[0]["Bonito"] == pytest.approx(100.0)


def test_estacao_sem_a_medicao_no_periodo_nao_vira_linha_vazia():
    tabela = leituras("Bonito", "2026-09-17 01:00", 24, [None] * 24)
    assert series.agregar(tabela, "TEM_INS", por_dia=True, funcao="Média").empty
