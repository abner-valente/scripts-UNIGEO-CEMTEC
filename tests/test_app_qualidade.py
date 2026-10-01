"""Verificações de qualidade das leituras: valores impossíveis, sensor travado e completude."""
import numpy as np
import pandas as pd
import pytest

from app import qualidade


def leituras(estacao: str, quantidade: int = 24, **valores) -> pd.DataFrame:
    """Leituras horárias de uma estação, começando à 01 h de 15/09 (horário de MS).

    Temperatura, umidade e pressão mudam de hora em hora, como num dado real: se ficassem
    paradas, a verificação de sensor travado apontaria a estação inteira.
    """
    horas = pd.date_range("2026-09-15 01:00", periods=quantidade, freq="h", tz="America/Campo_Grande")
    passo = np.arange(quantidade) * 0.3
    padrao = {"TEM_INS": 20 + passo, "UMD_INS": 50 + passo, "PRE_INS": 965 + passo,
              "RAD_GLO": 500.0, "TEN_BAT": 13.1}
    return pd.DataFrame({"Estação": estacao, "dt_local": horas, "dt_utc": horas.tz_convert("UTC"),
                         **{coluna: valores.get(coluna, valor) for coluna, valor in padrao.items()}})


def test_valor_fora_da_faixa_e_apontado():
    tabela = leituras("Bonito")
    tabela.loc[3, "UMD_INS"] = 130.0  # umidade acima de 100%

    achados = qualidade.valores_impossiveis(tabela)

    assert len(achados) == 1
    assert (achados.iloc[0]["Variável"], achados.iloc[0]["Valor"]) == ("UMD_INS", 130.0)


def test_radiacao_levemente_negativa_nao_e_valor_impossivel():
    """À noite o sensor devolve algo como -3,5: é ruído conhecido, não defeito."""
    tabela = leituras("Bonito", RAD_GLO=-3.5)
    assert qualidade.valores_impossiveis(tabela).empty


def test_radiacao_muito_negativa_e_apontada():
    tabela = leituras("Bonito", RAD_GLO=-900.0)
    assert len(qualidade.valores_impossiveis(tabela)) == len(tabela)


def test_sensor_repetindo_o_mesmo_valor_e_apontado():
    tabela = leituras("Dourados")
    tabela.loc[5:12, "TEM_INS"] = 31.4  # oito horas seguidas com o mesmo valor

    travados = qualidade.sensores_travados(tabela)

    assert len(travados) == 1
    assert (travados.iloc[0]["Variável"], travados.iloc[0]["Horas"]) == ("TEM_INS", 8)


def test_repeticao_curta_nao_conta_como_travado():
    tabela = leituras("Dourados")
    tabela.loc[5:9, "TEM_INS"] = 31.4  # cinco horas, abaixo do limite de seis

    assert qualidade.sensores_travados(tabela).empty


def test_chuva_zerada_nao_conta_como_travada():
    """Zero repetido em chuva é tempo seco, não sensor travado."""
    tabela = leituras("Dourados").assign(CHUVA=0.0)
    assert qualidade.sensores_travados(tabela).empty


def test_completude_por_dia_em_percentual():
    tabela = pd.concat([leituras("Bonito", quantidade=24), leituras("Corumba", quantidade=12)], ignore_index=True)

    completude = qualidade.completude_por_dia(tabela)

    assert completude.loc["Bonito"].iloc[0] == 100
    assert completude.loc["Corumba"].iloc[0] == 50


def test_completude_por_variavel_enxerga_sensor_parado():
    """A estação registra a hora, mas um sensor específico não mede: a completude por dia não veria."""
    tabela = leituras("Bonito", quantidade=10)
    tabela.loc[5:9, "UMD_INS"] = np.nan

    por_variavel = qualidade.completude_por_variavel(tabela)

    assert por_variavel.loc["Bonito", "TEM_INS"] == 100
    assert por_variavel.loc["Bonito", "UMD_INS"] == 50
    assert qualidade.completude_por_dia(tabela).loc["Bonito"].iloc[0] > 0  # a hora foi registrada


def test_resumo_traz_a_pior_estacao_primeiro():
    boa = leituras("Bonito", quantidade=24)
    ruim = leituras("Corumba", quantidade=6)
    ruim.loc[2, "UMD_INS"] = 130.0

    resumo = qualidade.resumo_por_estacao(pd.concat([boa, ruim], ignore_index=True), horas_esperadas=24)

    assert list(resumo["Estação"]) == ["Corumba", "Bonito"]
    assert list(resumo["Completude"]) == [25, 100]
    assert resumo.set_index("Estação").loc["Corumba", "Valores impossíveis"] == 1
    assert resumo.set_index("Estação").loc["Bonito", "Bateria mín. (V)"] == pytest.approx(13.1)
