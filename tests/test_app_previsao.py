"""As contas da página de previsão: a previsão guardada por rodada e as séries dos gráficos."""
import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app import previsao
from modulos import config, openmeteo

RODADA_00 = datetime(2026, 10, 7, 0, tzinfo=timezone.utc)
RODADA_06 = datetime(2026, 10, 7, 6, tzinfo=timezone.utc)


def tabela_da_rodada(rodada: datetime, modelo: str = "ecmwf_ifs025") -> pd.DataFrame:
    return pd.DataFrame({"modelo": [modelo], "rodada_utc": [rodada], "ponto": ["A702"]})


class Busca:
    """Uma busca de mentira, que conta quantas vezes foi ao "Open-Meteo"."""

    def __init__(self, rodada: datetime = RODADA_00, erro: str | None = None):
        self.rodada, self.erro, self.vezes = rodada, erro, 0

    def __call__(self) -> pd.DataFrame:
        self.vezes += 1
        if self.erro:
            raise openmeteo.ErroOpenMeteo(self.erro)
        return tabela_da_rodada(self.rodada)


# =====================================================
# A PREVISÃO GUARDADA
# =====================================================
def test_a_mesma_rodada_e_buscada_uma_vez_so():
    guarda, busca = previsao.Guarda(), Busca()

    primeira = guarda.obter("estações", "ecmwf_ifs025", RODADA_00, busca)
    segunda = guarda.obter("estações", "ecmwf_ifs025", RODADA_00, busca)

    assert busca.vezes == 1
    assert segunda.tabela is primeira.tabela and segunda.aviso is None


def test_rodada_nova_e_buscada_de_novo():
    guarda = previsao.Guarda()
    guarda.obter("estações", "ecmwf_ifs025", RODADA_00, Busca(RODADA_00))
    nova = Busca(RODADA_06)

    obtida = guarda.obter("estações", "ecmwf_ifs025", RODADA_06, nova)

    assert nova.vezes == 1
    assert previsao.rodada_da(obtida.tabela) == RODADA_06


def test_guardada_mais_nova_que_a_conferida_nao_e_buscada_de_novo():
    """A conferência fica 5 minutos no cache; a busca pode ter trazido a rodada seguinte."""
    guarda = previsao.Guarda()
    guarda.obter("estações", "ecmwf_ifs025", RODADA_00, Busca(RODADA_06))
    outra = Busca(RODADA_06)

    guarda.obter("estações", "ecmwf_ifs025", RODADA_00, outra)

    assert outra.vezes == 0


def test_cada_modelo_e_cada_conjunto_de_pontos_sao_guardados_a_parte():
    guarda, busca = previsao.Guarda(), Busca()

    guarda.obter("estações", "ecmwf_ifs025", RODADA_00, busca)
    guarda.obter("estações", "gfs_seamless", RODADA_00, busca)
    guarda.obter("grade", "ecmwf_ifs025", RODADA_00, busca)

    assert busca.vezes == 3


def test_se_o_open_meteo_falha_fica_a_rodada_guardada_com_aviso():
    guarda = previsao.Guarda()
    guardada = guarda.obter("estações", "ecmwf_ifs025", RODADA_00, Busca(RODADA_00)).tabela

    obtida = guarda.obter("estações", "ecmwf_ifs025", RODADA_06, Busca(erro="cota do Open-Meteo esgotada"))

    assert obtida.tabela is guardada
    assert obtida.aviso == "cota do Open-Meteo esgotada"


def test_sem_nada_guardado_a_falha_chega_a_quem_pediu():
    with pytest.raises(openmeteo.ErroOpenMeteo):
        previsao.Guarda().obter("estações", "ecmwf_ifs025", RODADA_00, Busca(erro="sem resposta"))


def test_sem_conferir_a_rodada_fica_a_guardada_com_aviso_e_sem_buscar():
    guarda = previsao.Guarda()
    guarda.obter("estações", "ecmwf_ifs025", RODADA_00, Busca())
    busca = Busca()

    obtida = guarda.obter("estações", "ecmwf_ifs025", None, busca)

    assert busca.vezes == 0
    assert "conferir" in obtida.aviso


def test_precisa_buscar_diz_o_que_a_obter_vai_fazer():
    guarda = previsao.Guarda()
    assert guarda.precisa_buscar("estações", "ecmwf_ifs025", RODADA_00)
    guarda.obter("estações", "ecmwf_ifs025", RODADA_00, Busca())

    assert not guarda.precisa_buscar("estações", "ecmwf_ifs025", RODADA_00)
    assert not guarda.precisa_buscar("estações", "ecmwf_ifs025", None)
    assert guarda.precisa_buscar("estações", "ecmwf_ifs025", RODADA_06)


def test_duas_pessoas_ao_mesmo_tempo_buscam_uma_vez_so():
    """A segunda espera a busca da primeira e sai com ela, em vez de gastar a cota de novo."""
    guarda, liberar, buscando = previsao.Guarda(), threading.Event(), threading.Event()
    vezes = []

    def busca_demorada() -> pd.DataFrame:
        vezes.append(1)
        buscando.set()
        liberar.wait(5)
        return tabela_da_rodada(RODADA_00)

    resultados = []
    primeira = threading.Thread(target=lambda: resultados.append(
        guarda.obter("estações", "ecmwf_ifs025", RODADA_00, busca_demorada)))
    segunda = threading.Thread(target=lambda: resultados.append(
        guarda.obter("estações", "ecmwf_ifs025", RODADA_00, busca_demorada)))
    primeira.start()
    buscando.wait(5)
    segunda.start()
    liberar.set()
    primeira.join(5)
    segunda.join(5)

    assert len(vezes) == 1
    assert len(resultados) == 2 and resultados[0].tabela is resultados[1].tabela


# =====================================================
# AS SÉRIES DOS GRÁFICOS
# =====================================================
def horaria(inicio: str, quantas: int, modelos=("ecmwf_ifs025", "gfs_seamless"), **colunas) -> pd.DataFrame:
    """Horas seguidas no horário de MS, já em UTC, como a busca devolve, para cada modelo."""
    locais = pd.date_range(inicio, periods=quantas, freq="h", tz=config.FUSO_MS)
    padrao = {nome: 1.0 for nome in openmeteo.VARIAVEIS.values()}
    return pd.concat([pd.DataFrame({
        "modelo": modelo, "rodada_utc": RODADA_00, "ponto": "A702", "latitude": -20.45, "longitude": -54.62,
        "hora_prevista_utc": locais.tz_convert("UTC"), **{**padrao, **colunas}}) for modelo in modelos],
        ignore_index=True)[openmeteo.COLUNAS_HORARIAS]


def test_todas_as_colunas_do_catalogo_existem_na_previsao():
    horas = horaria("2026-10-07 01:00", 24)
    dias = openmeteo.diario(horas)
    for grandeza in previsao.GRANDEZAS.values():
        assert {serie.coluna for serie in grandeza.series[previsao.HORA]} <= set(horas.columns)
        assert {serie.coluna for serie in grandeza.series[previsao.DIA]} <= set(dias.columns)


def test_por_hora_uma_linha_por_modelo_e_hora_a_partir_de_agora():
    horas = horaria("2026-10-07 01:00", 48)
    desde = pd.Timestamp("2026-10-08 01:00", tz=config.FUSO_MS)

    longo = previsao.series(horas, "Temperatura", previsao.HORA, desde)

    assert list(longo.columns) == ["dt_local", "Modelo", "valor", "Série"]
    assert set(longo["Modelo"]) == {"ECMWF", "GFS"}
    assert len(longo) == 2 * 24
    assert longo["dt_local"].min() == desde


def test_por_dia_so_entram_os_dias_inteiros():
    """O dia 7 começa 01:00 e está inteiro; o 8 fica pela metade e sairia como uma queda falsa."""
    temperatura = [20.0] * 23 + [35.0] + [10.0] * 12           # o 7 fecha com a das 00:00 de 8
    horas = horaria("2026-10-07 01:00", 36, temperatura=temperatura)
    desde = pd.Timestamp("2026-10-07 10:00", tz=config.FUSO_MS)

    longo = previsao.series(horas, "Temperatura", previsao.DIA, desde)

    assert set(longo["dt_local"]) == {pd.Timestamp("2026-10-07", tz=config.FUSO_MS)}
    assert longo.set_index(["Modelo", "Série"]).loc[("ECMWF", "Máxima"), "valor"] == 35.0
    assert set(longo["Série"]) == {"Máxima", "Mínima", "Média"}


def test_por_dia_o_dia_de_hoje_entra_mesmo_com_horas_passadas():
    horas = horaria("2026-10-07 01:00", 48)
    desde = pd.Timestamp("2026-10-07 15:00", tz=config.FUSO_MS)

    longo = previsao.series(horas, "Chuva", previsao.DIA, desde)

    assert longo["dt_local"].min() == pd.Timestamp("2026-10-07", tz=config.FUSO_MS)


def test_hora_sem_valor_nao_vira_ponto_no_grafico():
    horas = horaria("2026-10-07 01:00", 24, modelos=("icon_seamless",), chuva=[np.nan] * 12 + [1.0] * 12)
    desde = pd.Timestamp("2026-10-07 01:00", tz=config.FUSO_MS)

    assert len(previsao.series(horas, "Chuva", previsao.HORA, desde)) == 12


def test_sem_previsao_as_series_saem_vazias_com_as_colunas():
    vazia = pd.DataFrame(columns=openmeteo.COLUNAS_HORARIAS)

    longo = previsao.series(vazia, "Vento", previsao.HORA, pd.Timestamp.now(tz=config.FUSO_MS))

    assert longo.empty and list(longo.columns) == ["dt_local", "Modelo", "valor", "Série"]


def test_o_total_de_chuva_soma_de_agora_ate_onde_cada_modelo_vai():
    horas = pd.concat([horaria("2026-10-07 01:00", 48, modelos=("ecmwf_ifs025",), chuva=[0.5] * 48),
                       horaria("2026-10-07 01:00", 24, modelos=("icon_seamless",), chuva=[2.0] * 24)],
                      ignore_index=True)
    desde = pd.Timestamp("2026-10-07 13:00", tz=config.FUSO_MS)

    totais = previsao.chuva_total(horas, desde).set_index("Modelo")

    assert totais.loc["ECMWF", "chuva"] == pytest.approx(0.5 * 36)
    assert totais.loc["ICON", "chuva"] == pytest.approx(2.0 * 12)
    assert totais.loc["ICON", "ate"] == pd.Timestamp("2026-10-08 00:00", tz=config.FUSO_MS)


def test_a_planilha_tem_um_dia_por_linha_e_modelo_com_o_nome_do_modelo():
    planilha = previsao.planilha(horaria("2026-10-07 01:00", 24))

    assert list(planilha["modelo"]) == ["ECMWF", "GFS"]
    assert planilha["rodada_utc"].iloc[0] == "2026-10-07 00:00"
    assert "latitude" not in planilha
