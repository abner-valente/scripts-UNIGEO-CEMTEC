"""Produto risco_fogo: a regra 30-30-30 hora a hora, as grades horárias e a execução completa."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from modulos import config
from modulos.config import FUSO_UTC, Periodo
from modulos.produtos import risco_fogo

ESTACAO = pd.Series({"Estação": "Teste", "VL_LATITUDE": -20.0, "VL_LONGITUDE": -55.0})
DIA = Periodo.de_datas(date(2026, 9, 16), date(2026, 9, 16))
PERIODO = Periodo.de_datas(date(2026, 9, 14), date(2026, 9, 16))
MANHA = Periodo.de_datas(date(2026, 9, 16), date(2026, 9, 16), 6, 9)  # janela curta, para o teste ser rápido
AGORA = datetime(2026, 9, 14, 13, 25, tzinfo=FUSO_UTC)  # 9h25 em MS
TEMPO_REAL = Periodo.tempo_real(AGORA)

# Valores que atendem e que não atendem cada condição
QUENTE, FRIO = 32.0, 20.0
SECO, UMIDO = 25.0, 50.0
VENTOSO, CALMO = 12.0, 2.0  # m/s: 43,2 km/h e 7,2 km/h


def utc(texto):
    return pd.Timestamp(texto, tz="UTC")


# ---------- A regra ----------

@pytest.mark.parametrize("temperatura, umidade, rajada, nivel", [
    (QUENTE, SECO, 43.2, 3),
    (QUENTE, SECO, 7.2, 2),
    (QUENTE, UMIDO, 7.2, 1),
    (FRIO, UMIDO, 7.2, 0),
    (30.0, 30.0, 30.0, 3),  # os limiares entram na conta: >= 30, <= 30 e >= 30
    (29.9, 30.1, 29.9, 0),
])
def test_contagem_das_condicoes(temperatura, umidade, rajada, nivel):
    assert risco_fogo.contar_condicoes(temperatura, umidade, rajada) == nivel


# ---------- Hora a hora ----------

def test_condicoes_em_horas_diferentes_nao_viram_risco_alto(serie):
    """O ponto central do produto: calor às 14 h, seca às 17 h e vento às 05 h não é 30-30-30."""
    dados = serie("2026-09-16", "2026-09-17 05:00", TEM_MAX=FRIO, UMD_MIN=UMIDO, VEN_RAJ=CALMO)
    dados.loc[dados["dt_utc"] == utc("2026-09-16 18:00"), "TEM_MAX"] = QUENTE   # 14 h em MS
    dados.loc[dados["dt_utc"] == utc("2026-09-16 21:00"), "UMD_MIN"] = SECO     # 17 h em MS
    dados.loc[dados["dt_utc"] == utc("2026-09-16 09:00"), "VEN_RAJ"] = VENTOSO  # 05 h em MS

    niveis = risco_fogo.niveis_por_hora(risco_fogo.leituras_validas(dados, DIA))

    assert niveis.max() == 1


def test_tres_condicoes_na_mesma_hora_viram_risco_alto(serie):
    dados = serie("2026-09-16", "2026-09-17 05:00", TEM_MAX=FRIO, UMD_MIN=UMIDO, VEN_RAJ=CALMO)
    hora_critica = dados["dt_utc"] == utc("2026-09-16 18:00")
    dados.loc[hora_critica, ["TEM_MAX", "UMD_MIN", "VEN_RAJ"]] = [QUENTE, SECO, VENTOSO]

    niveis = risco_fogo.niveis_por_hora(risco_fogo.leituras_validas(dados, DIA))

    assert niveis.max() == 3
    assert (niveis == 3).sum() == 1


def test_horas_sem_as_tres_variaveis_ficam_de_fora(serie):
    dados = serie("2026-09-16", "2026-09-17 05:00")
    dados.loc[dados["dt_utc"] == utc("2026-09-16 18:00"), "UMD_MIN"] = None

    leituras = risco_fogo.leituras_validas(dados, DIA)

    assert len(leituras) == 23  # 24 horas do dia em MS, menos a hora incompleta
    assert utc("2026-09-16 18:00") not in leituras["dt_utc"].tolist()


def test_rajada_convertida_para_km_h(serie):
    dados = serie("2026-09-16", "2026-09-17 05:00", VEN_RAJ=10.0)
    assert risco_fogo.leituras_validas(dados, DIA)["rajada_kmh"].iloc[0] == 36.0


@pytest.mark.parametrize("periodo, quantidade, primeira, ultima", [
    (DIA, 24, "2026-09-16 05:00", "2026-09-17 04:00"),        # dia no horário de MS
    (MANHA, 3, "2026-09-16 11:00", "2026-09-16 13:00"),       # das 06 h às 09 h de MS, janela (início, fim]
    (TEMPO_REAL, 24, "2026-09-13 14:00", "2026-09-14 13:00"),  # pontas ajustadas para horas cheias
], ids=["dia", "janela_curta", "tempo_real"])
def test_horas_da_janela(periodo, quantidade, primeira, ultima):
    horas = risco_fogo.horas_da_janela(periodo)
    assert len(horas) == quantidade
    assert horas[0] == utc(primeira)
    assert horas[-1] == utc(ultima)


# ---------- Resumo por estação ----------

def test_resumo_conta_as_horas_de_cada_nivel(serie):
    dados = serie("2026-09-16", "2026-09-17 05:00", TEM_MAX=QUENTE, UMD_MIN=UMIDO, VEN_RAJ=CALMO)  # nível 1 o dia todo
    dados.loc[dados["dt_utc"] == utc("2026-09-16 16:00"), "UMD_MIN"] = SECO  # nível 2 às 12 h de MS
    duas_horas = dados["dt_utc"].isin([utc("2026-09-16 18:00"), utc("2026-09-16 19:00")])
    dados.loc[duas_horas, ["UMD_MIN", "VEN_RAJ"]] = [SECO, VENTOSO]

    linha = risco_fogo.resumir_estacao(risco_fogo.leituras_validas(dados, DIA), ESTACAO, DIA)

    assert linha["Nível Máximo"] == 3
    assert linha["Horas em Risco Alto"] == 2
    assert linha["Horas Nível 2"] == 1
    assert linha["Horas Nível 1"] == 21
    assert linha["Horas com Dados"] == 24
    assert linha["Primeiro Horário em Risco Médio (MS)"] == "16/09/2026 12:00"  # 16 UTC, quando começou a subir
    assert linha["Primeiro Horário em Risco Alto (MS)"] == "16/09/2026 14:00"  # 18 UTC = 14 h em MS


def test_estacao_sem_risco_alto_fica_sem_horario(serie):
    dados = serie("2026-09-16", "2026-09-17 05:00", TEM_MAX=FRIO, UMD_MIN=UMIDO, VEN_RAJ=CALMO)
    linha = risco_fogo.resumir_estacao(risco_fogo.leituras_validas(dados, DIA), ESTACAO, DIA)
    assert linha["Nível Máximo"] == 0
    assert linha["Primeiro Horário em Risco Médio (MS)"] == ""
    assert linha["Primeiro Horário em Risco Alto (MS)"] == ""


def test_tabela_ordena_do_maior_risco_para_o_menor():
    resumos = [
        {"Estação": "A", "Nível Máximo": 1, "Horas em Risco Alto": 0},
        {"Estação": "B", "Nível Máximo": 3, "Horas em Risco Alto": 2},
        {"Estação": "C", "Nível Máximo": 3, "Horas em Risco Alto": 7},
    ]
    assert risco_fogo.montar_tabela(resumos)["Estação"].tolist() == ["C", "B", "A"]


# ---------- Seleção das horas que ganham mapa ----------

def _hora(nivel_estacoes, nivel_grade):
    estacoes = pd.DataFrame({risco_fogo.COLUNA_NIVEL_HORA: [0, nivel_estacoes]})
    return risco_fogo.HoraAvaliada(np.full((2, 2), nivel_grade), estacoes)


def test_so_as_horas_com_risco_alto_ganham_mapa():
    horas = {1: _hora(0, 0), 2: _hora(2, 2), 3: _hora(3, 3)}
    assert sorted(risco_fogo.horas_para_mapear(horas)) == [3]


def test_hrtodas_mapeia_todas_as_horas():
    horas = {1: _hora(0, 0), 2: _hora(2, 2)}
    assert sorted(risco_fogo.horas_para_mapear(horas, todas_as_horas=True)) == [1, 2]


def test_criterio_olha_as_estacoes_e_nao_a_superficie():
    """A superfície pode chegar ao risco alto por interpolação sem que nenhuma estação tenha chegado."""
    horas = {1: _hora(nivel_estacoes=2, nivel_grade=3)}
    assert risco_fogo.horas_para_mapear(horas) == {}


# ---------- Mapas horários ----------

def test_estacoes_da_hora_trazem_as_condicoes_e_o_nivel_daquela_hora(serie):
    """Nos mapas horários, cada estação leva as condições e o nível da hora, e não o máximo do dia."""
    dados = serie("2026-09-16", "2026-09-17 05:00", TEM_MAX=FRIO, UMD_MIN=UMIDO, VEN_RAJ=CALMO)
    dados.loc[dados["dt_utc"] == utc("2026-09-16 18:00"), ["TEM_MAX", "UMD_MIN", "VEN_RAJ"]] = [QUENTE, SECO, VENTOSO]
    dados.loc[dados["dt_utc"] == utc("2026-09-16 20:00"), "UMD_MIN"] = SECO
    coletados = [(ESTACAO, risco_fogo.leituras_validas(dados, DIA))]

    def na_hora(texto):
        linha = risco_fogo.estacoes_na_hora(coletados, utc(texto)).iloc[0]
        return [bool(linha[coluna]) for coluna in risco_fogo.COLUNAS_CONDICOES], linha["Nível na Hora"]

    assert na_hora("2026-09-16 18:00") == ([True, True, True], 3)
    assert na_hora("2026-09-16 20:00") == ([False, True, False], 1)  # só a umidade
    assert na_hora("2026-09-16 15:00") == ([False, False, False], 0)


def test_mapa_horario_recebe_as_estacoes_da_hora(monkeypatch, tmp_path):
    """Rótulos e legenda do mapa horário vêm das estações daquela hora."""
    desenhados = []
    monkeypatch.setattr(risco_fogo.mapas, "mapa_classes_interpolado",
                        lambda grade, gdf, coluna, espec, base, caminho, indicadores=None:
                        desenhados.append(gdf[coluna].tolist()))
    estacoes = pd.DataFrame({"Estação": ["A", "B"], "Latitude": [-20.0, -21.0], "Longitude": [-55.0, -54.0],
                             "Nível na Hora": [0, 2]})
    horas = {utc("2026-09-16 15:00"): risco_fogo.HoraAvaliada(np.zeros((2, 2)), estacoes)}

    risco_fogo._mapas_horarios(horas, base=None, pasta=tmp_path, todas_as_horas=True)

    assert desenhados == [[0, 2]]


# ---------- Execução completa ----------

def test_periodo_conta_os_dias_de_cada_nivel(serie):
    """Num período, a planilha diz em quantos dias a estação chegou a cada nível."""
    dados = serie("2026-09-14", "2026-09-17 05:00", TEM_MAX=QUENTE, UMD_MIN=UMIDO, VEN_RAJ=CALMO)  # nível 1
    dados.loc[dados["dt_utc"] == utc("2026-09-14 18:00"), ["UMD_MIN", "VEN_RAJ"]] = [SECO, VENTOSO]  # 14/09: nível 3
    dados.loc[dados["dt_utc"] == utc("2026-09-15 18:00"), "UMD_MIN"] = SECO                          # 15/09: nível 2

    linha = risco_fogo.resumir_estacao(risco_fogo.leituras_validas(dados, PERIODO), ESTACAO, PERIODO)

    assert linha["Dias com Risco Alto"] == 1
    assert linha["Dias com Risco Médio"] == 1  # o terceiro dia ficou no nível 1


def test_dia_nao_traz_as_colunas_de_dias(serie):
    dados = serie("2026-09-16", "2026-09-17 05:00")
    linha = risco_fogo.resumir_estacao(risco_fogo.leituras_validas(dados, DIA), ESTACAO, DIA)
    assert "Dias com Risco Alto" not in linha


def test_execucao_completa_de_periodo(api_simulada):
    periodo = Periodo.de_datas(date(2026, 9, 14), date(2026, 9, 15))
    assert risco_fogo.executar(periodo) == 0

    pasta = periodo.pasta_saida(risco_fogo.NOME)
    tabela = pd.read_excel(pasta / f"Risco_Fogo_{config.UF}_{periodo.identificador}.xlsx")
    assert {"Dias com Risco Alto", "Dias com Risco Médio"} <= set(tabela.columns)
    assert tabela["Dias com Risco Alto"].max() <= periodo.num_dias
    assert (pasta / "mapas" / f"Mapa_Risco_Fogo_Nivel_{config.UF}_{periodo.identificador}_interpolado.png").exists()


def test_execucao_completa(api_simulada, tmp_path):
    assert risco_fogo.executar(MANHA, {"hrtodas": True}) == 0

    pasta = MANHA.pasta_saida(risco_fogo.NOME)
    tabela = pd.read_excel(pasta / f"Risco_Fogo_{config.UF}_{MANHA.identificador}.xlsx")
    assert len(tabela) == api_simulada.estacoes_com_dados
    assert tabela["Nível Máximo"].between(0, 3).all()

    mapas_gerados = sorted(p.name for p in (pasta / "mapas").glob("*.png"))
    assert mapas_gerados == [
        f"Mapa_Risco_Fogo_Horas_{config.UF}_{MANHA.identificador}.png",
        f"Mapa_Risco_Fogo_Horas_{config.UF}_{MANHA.identificador}_interpolado.png",
        f"Mapa_Risco_Fogo_Nivel_{config.UF}_{MANHA.identificador}.png",
        f"Mapa_Risco_Fogo_Nivel_{config.UF}_{MANHA.identificador}_interpolado.png",
    ]
    assert len(list((pasta / "mapas" / "horas").glob("*.png"))) == 3  # uma por hora, com --hrtodas
