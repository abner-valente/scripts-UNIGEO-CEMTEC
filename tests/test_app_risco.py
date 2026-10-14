"""Risco de fogo no painel: a tradução de formato não pode mudar o que a regra diz."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app import risco
from modulos.config import Periodo
from modulos.produtos import risco_fogo

FUSO = "America/Campo_Grande"

ESTACOES = pd.DataFrame({
    "Estação": ["Bonito", "Corumba"],
    "CD_ESTACAO": ["A001", "A002"],
    "SG_ESTADO": ["MS", "MS"],
    "VL_LATITUDE": [-21.1, -19.0],
    "VL_LONGITUDE": [-56.5, -57.6],
})
DIA = Periodo.de_datas(date(2026, 9, 17), date(2026, 9, 17))


def leituras(estacao: str, **colunas) -> pd.DataFrame:
    marcas = pd.date_range("2026-09-17 01:00", periods=24, freq="h", tz=FUSO)
    return pd.DataFrame({"Estação": estacao, "dt_local": marcas,
                         "dt_utc": marcas.tz_convert("UTC"), **colunas})


def calmo(estacao="Bonito") -> pd.DataFrame:
    return leituras(estacao, TEM_MAX=[25.0] * 24, UMD_MIN=[60.0] * 24, VEN_RAJ=[10.0] * 24)


# =====================================================
# A TRADUÇÃO DE FORMATO
# =====================================================
def test_a_rajada_nao_e_convertida_de_novo():
    tabela = leituras("Bonito", TEM_MAX=[25.0] * 24, UMD_MIN=[60.0] * 24, VEN_RAJ=[36.0] * 24)

    (_, dados), = risco.por_estacao(tabela, ESTACOES)

    assert dados[risco.RAJADA].max() == pytest.approx(36.0)


def test_hora_sem_uma_das_tres_fica_de_fora():
    tabela = calmo()
    tabela.loc[tabela["dt_local"].dt.hour == 15, "UMD_MIN"] = np.nan

    (_, dados), = risco.por_estacao(tabela, ESTACOES)

    assert len(dados) == 23


def test_estacao_fora_do_cadastro_nao_entra():
    tabela = pd.concat([calmo("Bonito"), calmo("Fantasma")], ignore_index=True)

    assert [estacao["Estação"] for estacao, _ in risco.por_estacao(tabela, ESTACOES)] == ["Bonito"]


def test_sem_as_tres_variaveis_nao_ha_o_que_calcular():
    assert risco.por_estacao(leituras("Bonito", TEM_MAX=[30.0] * 24), ESTACOES) == []
    assert risco.por_estacao(pd.DataFrame(), ESTACOES) == []


# =====================================================
# A REGRA, VISTA PELA TELA
# =====================================================
def test_a_tabela_e_a_mesma_da_planilha_do_produto():
    quente = leituras("Bonito", TEM_MAX=[25.0] * 23 + [31.0], UMD_MIN=[60.0] * 23 + [20.0],
                      VEN_RAJ=[10.0] * 23 + [35.0])

    tabela = risco.resumo(risco.por_estacao(quente, ESTACOES), DIA)

    linha = tabela.iloc[0]
    assert linha[risco_fogo.COLUNA_NIVEL] == 3          # as três condições na mesma hora
    assert linha[risco_fogo.COLUNA_HORAS_ALTO] == 1
    assert linha["Temp. Máxima (°C)"] == pytest.approx(31.0)
    assert linha["Primeiro Horário em Risco Alto (MS)"].startswith("18/09/2026 00:00")


def test_extremos_em_horas_diferentes_nao_somam_risco():
    espalhado = leituras("Bonito", TEM_MAX=[25.0] * 24, UMD_MIN=[60.0] * 24, VEN_RAJ=[10.0] * 24)
    espalhado.loc[espalhado["dt_local"].dt.hour == 15, "TEM_MAX"] = 32.0
    espalhado.loc[espalhado["dt_local"].dt.hour == 18, "UMD_MIN"] = 28.0
    espalhado.loc[espalhado["dt_local"].dt.hour == 3, "VEN_RAJ"] = 35.0

    tabela = risco.resumo(risco.por_estacao(espalhado, ESTACOES), DIA)

    assert tabela.iloc[0][risco_fogo.COLUNA_NIVEL] == 1   # uma condição por vez, nunca três
    assert tabela.iloc[0][risco_fogo.COLUNA_HORAS_ALTO] == 0


def test_as_estacoes_saem_da_de_maior_risco_para_a_de_menor():
    tranquila = calmo("Bonito")
    arriscada = leituras("Corumba", TEM_MAX=[33.0] * 24, UMD_MIN=[20.0] * 24, VEN_RAJ=[40.0] * 24)

    tabela = risco.resumo(risco.por_estacao(pd.concat([tranquila, arriscada], ignore_index=True),
                                            ESTACOES), DIA)

    assert list(tabela["Estação"]) == ["Corumba", "Bonito"]


def test_a_tela_e_o_produto_dao_o_mesmo_nivel_hora_a_hora():
    cru = leituras("Bonito", TEM_MAX=[25.0, 31.0] * 12, UMD_MIN=[60.0, 20.0] * 12,
                   VEN_RAJ=[3.0, 9.0] * 12)          # m/s, como a API entrega
    periodo = Periodo.de_datas(date(2026, 9, 17), date(2026, 9, 17))

    do_produto = risco_fogo.niveis_por_hora(risco_fogo.leituras_validas(cru, periodo))
    (_, do_painel), = risco.por_estacao(cru.assign(VEN_RAJ=cru["VEN_RAJ"] * 3.6), ESTACOES)

    assert do_produto.sort_index().equals(risco_fogo.niveis_por_hora(do_painel).sort_index())
    assert do_produto.max() == 3          # 31 °C, 20 % e 32,4 km/h na mesma hora


def test_as_vizinhas_alimentam_a_grade_e_nao_a_tabela():
    cadastro = pd.concat([ESTACOES, pd.DataFrame({
        "Estação": ["Vizinha"], "CD_ESTACAO": ["A900"], "SG_ESTADO": ["GO"],
        "VL_LATITUDE": [-18.0], "VL_LONGITUDE": [-52.0]})], ignore_index=True)
    tabela = pd.concat([calmo("Bonito"), calmo("Vizinha")], ignore_index=True)

    completas = risco.por_estacao(tabela, cadastro.assign(SG_ESTADO=cadastro["SG_ESTADO"].fillna("MS")))
    do_produto = risco.da_uf(completas)

    assert len(completas) == 2                                  # as duas entram na conta
    assert [estacao["Estação"] for estacao, _ in do_produto] == ["Bonito"]
    assert list(risco.resumo(do_produto, DIA)["Estação"]) == ["Bonito"]


# =====================================================
# AS HORAS
# =====================================================
class GradeFalsa:
    def __init__(self, nivel_das_estacoes, grade):
        self.grade = np.asarray(grade)
        self.nivel_estacoes = nivel_das_estacoes


def test_so_interessam_as_horas_em_que_alguma_estacao_chegou_ao_nivel():
    avaliadas = {1: GradeFalsa(3, [[3]]), 2: GradeFalsa(1, [[2]]), 3: GradeFalsa(2, [[0]])}

    assert list(risco.horas_que_interessam(avaliadas)) == [1]
    assert list(risco.horas_que_interessam(avaliadas, nivel_minimo=2)) == [1, 3]


def test_a_sintese_do_periodo_sai_da_pilha_de_horas():
    avaliadas = {1: GradeFalsa(3, [[3, 0]]), 2: GradeFalsa(3, [[1, 3]]), 3: GradeFalsa(0, [[0, 0]])}

    np.testing.assert_array_equal(risco.pior_nivel(avaliadas), [[3, 3]])
    np.testing.assert_array_equal(risco.horas_em_risco_alto(avaliadas), [[1, 1]])


def test_sem_hora_avaliada_a_sintese_e_vazia():
    assert risco.pior_nivel({}) is None
    assert risco.horas_em_risco_alto({}) is None


def test_o_nivel_de_cada_estacao_no_dia_e_o_pior_das_horas():
    marcas = pd.date_range("2026-09-17 10:00", periods=2, freq="h", tz=FUSO).tz_convert("UTC")
    def estacoes_com(nivel):
        return pd.DataFrame({"Estação": ["Bonito"], "Latitude": [-21.1], "Longitude": [-56.5],
                             risco_fogo.COLUNA_NIVEL_HORA: [nivel]})
    avaliadas = {marcas[0]: GradeFalsa(1, [[1]]), marcas[1]: GradeFalsa(3, [[3]])}
    avaliadas[marcas[0]].estacoes = estacoes_com(1)
    avaliadas[marcas[1]].estacoes = estacoes_com(3)

    do_dia = risco.estacoes_do_dia(avaliadas, date(2026, 9, 17))

    assert do_dia[risco_fogo.COLUNA_NIVEL_HORA].iloc[0] == 3
    assert risco.estacoes_do_dia(avaliadas, date(2026, 9, 18)).empty


def test_os_pontos_das_condicoes_so_vao_no_mapa_de_uma_hora():
    do_dia = pd.DataFrame({"Estação": ["Bonito"], "Latitude": [-21.1], "Longitude": [-56.5],
                           risco_fogo.COLUNA_NIVEL_HORA: [3]})
    da_hora = do_dia.assign(**{coluna: [True] for coluna in risco_fogo.COLUNAS_CONDICOES})

    assert risco.indicadores(do_dia, detalhes=True) is None
    assert risco.indicadores(da_hora, detalhes=True) == risco_fogo.indicadores_condicoes()
    assert risco.indicadores(da_hora, detalhes=False) is None


def test_o_mapa_do_dia_com_detalhes_desenha_sem_os_pontos():
    from modulos import config, mapas
    base = mapas.carregar_base()
    do_dia = pd.DataFrame({"Estação": ["Bonito", "Corumba", "Dourados"],
                           "Latitude": [-21.1, -19.0, -22.2], "Longitude": [-56.5, -57.6, -54.8],
                           risco_fogo.COLUNA_NIVEL_HORA: [1, 3, 0]})
    gdf = mapas.preparar_pontos(do_dia, risco_fogo.COLUNA_NIVEL_HORA)

    figura = mapas.mapa_classes_interpolado(
        np.zeros(base.lon_grade.shape), gdf, risco_fogo.COLUNA_NIVEL_HORA,
        mapas.EspecClasses("Risco de fogo em MS", "30/09/2026", "", config.CORES_RISCO,
                           config.ROTULOS_RISCO), base,
        indicadores=risco.indicadores(do_dia, detalhes=True), tela=mapas.Tela(rotulos=True))

    assert len(figura.axes[0].texts) == len(do_dia)  # o nível de cada estação continua escrito


def test_o_pior_do_dia_respeita_o_fechamento_do_dia_do_projeto():
    marcas = pd.date_range("2026-09-17 23:00", periods=2, freq="h", tz=FUSO).tz_convert("UTC")
    avaliadas = {marcas[0]: GradeFalsa(1, [[1]]), marcas[1]: GradeFalsa(3, [[3]])}

    dias = risco.por_dia(avaliadas)

    assert list(dias) == [date(2026, 9, 17)]
    np.testing.assert_array_equal(dias[date(2026, 9, 17)], [[3]])
