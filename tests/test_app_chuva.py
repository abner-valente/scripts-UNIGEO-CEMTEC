"""Chuva: acumulados que olham para trás do período e a cascata do boletim."""
from datetime import datetime

import pandas as pd
import pytest

from app import chuva

FUSO = "America/Campo_Grande"


def leituras(estacao: str, inicio: str, horas: int, milimetros) -> pd.DataFrame:
    marcas = pd.date_range(inicio, periods=horas, freq="h", tz=FUSO)
    return pd.DataFrame({"Estação": estacao, "dt_local": marcas, "CHUVA": milimetros})


def instante(texto: str) -> datetime:
    return pd.Timestamp(texto, tz=FUSO)


# =====================================================
# JANELAS
# =====================================================
def test_janela_de_horas_conta_para_tras_do_fim():
    comeco, fim = chuva.janela(instante("2026-09-25 00:00"), "24 h")
    assert f"{comeco:%d/%m %H:%M}" == "24/09 00:00"
    assert f"{fim:%d/%m %H:%M}" == "25/09 00:00"


def test_janela_mensal_comeca_no_primeiro_dia_do_mes():
    comeco, _ = chuva.janela(instante("2026-09-25 00:00"), chuva.MENSAL)
    assert f"{comeco:%d/%m %H:%M}" == "01/09 00:00"


def test_carga_necessaria_alcanca_o_comeco_do_mes():
    """A aba da chuva precisa de mais dado que o período escolhido — é a razão de ser separada."""
    assert f"{chuva.inicio_necessario(instante('2026-09-25 00:00')):%d/%m}" == "01/09"


def test_no_comeco_do_mes_a_carga_vai_alem_dele():
    """Dia 2, o acumulado de 96 h ainda alcança o mês anterior."""
    assert f"{chuva.inicio_necessario(instante('2026-10-02 00:00')):%d/%m}" == "28/09"


# =====================================================
# ACUMULADOS
# =====================================================
def test_acumulado_soma_so_as_horas_da_janela():
    # 48 horas de 1 mm, terminando às 00:00 de 25/09
    tabela = leituras("Bonito", "2026-09-23 01:00", 48, 1.0)

    somas = chuva.acumulado(tabela, instante("2026-09-25 00:00"), "24 h")

    assert somas["Bonito"] == pytest.approx(24.0)


def test_acumulado_entra_a_leitura_do_fim_e_nao_a_do_inicio():
    """Convenção do projeto: a leitura de uma hora cobre a hora que termina nela."""
    tabela = leituras("Bonito", "2026-09-24 00:00", 25, [100.0] + [1.0] * 24)

    somas = chuva.acumulado(tabela, instante("2026-09-25 00:00"), "24 h")

    assert somas["Bonito"] == pytest.approx(24.0)  # os 100 mm das 00:00 ficam de fora


def test_acumulado_mensal_pega_o_mes_inteiro_ate_o_fim():
    tabela = leituras("Bonito", "2026-08-30 01:00", 24 * 10, 1.0)  # atravessa a virada do mês
    somas = chuva.acumulado(tabela, instante("2026-09-05 00:00"), chuva.MENSAL)
    assert somas["Bonito"] == pytest.approx(24 * 4)  # só os dias 1 a 4 de setembro


def test_avisa_quando_o_dado_carregado_nao_cobre_a_janela():
    """96 h calculadas com 48 h de dado mostrariam metade da chuva como se fosse o total."""
    tabela = leituras("Bonito", "2026-09-23 01:00", 48, 1.0)
    fim = instante("2026-09-25 00:00")

    assert chuva.cobre(tabela, fim, "24 h")
    assert not chuva.cobre(tabela, fim, "96 h")
    assert not chuva.cobre(tabela, fim, chuva.MENSAL)


def test_rotulos_saem_na_ordem_pedida():
    assert chuva.rotulos() == ["3 h", "6 h", "12 h", "24 h", "48 h", "72 h", "96 h", "Mensal"]


# =====================================================
# CASCATA
# =====================================================
def test_cascata_empilha_cada_passo_no_acumulado_anterior():
    tabela = leituras("Bonito", "2026-09-24 01:00", 24, [0.0] * 21 + [2.0, 3.0, 5.0])

    barras = chuva.cascata(tabela, por_dia=False)

    passos = barras[barras["tipo"] == "passo"].sort_values("ordem")
    ultimos = passos.tail(3)
    assert list(ultimos["valor"]) == [2.0, 3.0, 5.0]
    assert list(ultimos["base"]) == [0.0, 2.0, 5.0]   # cada um começa onde o anterior parou
    assert list(ultimos["topo"]) == [2.0, 5.0, 10.0]


def test_cascata_termina_com_a_barra_de_total():
    tabela = leituras("Bonito", "2026-09-24 01:00", 24, 0.5)

    barras = chuva.cascata(tabela, por_dia=False)

    total = barras[barras["tipo"] == "total"].iloc[0]
    assert total["Passo"] == "Total"
    assert total["valor"] == pytest.approx(12.0)
    assert total["base"] == 0.0                      # a barra do total sai do chão
    assert total["ordem"] == barras["ordem"].max()   # e fica por último


def test_cascata_horaria_mostra_so_as_ultimas_24_horas():
    """Uma semana daria 168 barras: ilegível."""
    tabela = leituras("Bonito", "2026-09-18 01:00", 24 * 7, 1.0)

    barras = chuva.cascata(tabela, por_dia=False)

    assert len(barras[barras["tipo"] == "passo"]) == 24
    assert barras[barras["tipo"] == "total"].iloc[0]["valor"] == pytest.approx(24.0)


def test_cascata_diaria_usa_o_fechamento_do_dia_do_projeto():
    """A leitura das 00:00 fecha o dia anterior: sete dias continuam sete barras."""
    tabela = leituras("Bonito", "2026-09-18 01:00", 24 * 7, 1.0)

    barras = chuva.cascata(tabela, por_dia=True)
    passos = barras[barras["tipo"] == "passo"]

    assert len(passos) == 7
    assert list(passos["Passo"])[:2] == ["18/09", "19/09"]
    assert passos["valor"].iloc[0] == pytest.approx(24.0)


def test_cascata_separa_as_estacoes():
    tabela = pd.concat([leituras("Bonito", "2026-09-24 01:00", 24, 1.0),
                        leituras("Corumba", "2026-09-24 01:00", 24, 2.0)], ignore_index=True)

    barras = chuva.cascata(tabela, por_dia=False)
    totais = barras[barras["tipo"] == "total"].set_index("Estação")["valor"]

    assert totais["Bonito"] == pytest.approx(24.0)
    assert totais["Corumba"] == pytest.approx(48.0)


def test_sem_leituras_a_cascata_sai_vazia_e_nao_quebra():
    assert chuva.cascata(pd.DataFrame(), por_dia=True).empty
