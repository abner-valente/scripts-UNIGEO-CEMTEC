"""As contas da página de previsão: a previsão guardada por rodada e as séries dos gráficos."""
import threading
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app import boletim, previsao
from modulos import config, mapas, openmeteo
from modulos.produtos import risco_fogo

RODADA_00 = datetime(2026, 10, 7, 0, tzinfo=timezone.utc)
RODADA_06 = datetime(2026, 10, 7, 6, tzinfo=timezone.utc)


def tabela_da_rodada(rodada: datetime, modelo: str = "ecmwf_ifs025") -> pd.DataFrame:
    return pd.DataFrame({"modelo": [modelo], "rodada_utc": [rodada], "ponto": ["A702"]})


class Busca:
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


# =====================================================
# OS MAPAS
# =====================================================
HOJE = date(2026, 10, 8)


def diaria(dias: dict, pontos=("A", "B"), coluna: str = "chuva", horas: dict | None = None) -> pd.DataFrame:
    linhas = [{"modelo": "ecmwf_ifs025", "ponto": ponto, "dia_previsto": dia, coluna: valor,
               "direcao_dominante": 90.0, "horas": (horas or {}).get(dia, 24)}
              for dia, valor in dias.items() for ponto in pontos]
    return pd.DataFrame(linhas)


def test_os_dias_do_deslizante_sao_os_inteiros_de_hoje_em_diante():
    tabela = diaria({date(2026, 10, 7): 1.0, HOJE: 1.0, date(2026, 10, 9): 1.0, date(2026, 10, 10): 1.0},
                    horas={date(2026, 10, 10): 20})

    assert previsao.dias_inteiros(tabela, HOJE) == [HOJE, date(2026, 10, 9)]


def test_o_dia_da_semana_vai_no_deslizante():
    assert previsao.nome_do_dia(date(2026, 10, 10)) == "sáb 10/10"


def test_o_mapa_do_dia_e_o_valor_daquele_dia():
    tabela = diaria({HOJE: 2.0, date(2026, 10, 9): 5.0})

    valores = previsao.valores_do_dia(tabela, previsao.MAPAS["Chuva do dia"], date(2026, 10, 9), HOJE)

    assert valores.to_dict() == {"A": 5.0, "B": 5.0}


def test_o_acumulado_soma_de_hoje_ate_o_dia_escolhido():
    tabela = diaria({date(2026, 10, 7): 100.0, HOJE: 2.0, date(2026, 10, 9): 5.0, date(2026, 10, 10): 7.0})

    valores = previsao.valores_do_dia(tabela, previsao.MAPAS["Chuva acumulada"], date(2026, 10, 9), HOJE)

    assert valores.to_dict() == {"A": 7.0, "B": 7.0}           # ontem e o dia seguinte ficam de fora


def test_no_acumulado_um_dia_faltando_deixa_o_ponto_sem_valor():
    tabela = diaria({HOJE: 2.0, date(2026, 10, 9): 5.0})
    tabela = tabela[~((tabela["ponto"] == "B") & (tabela["dia_previsto"] == HOJE))]

    valores = previsao.valores_do_dia(tabela, previsao.MAPAS["Chuva acumulada"], date(2026, 10, 9), HOJE)

    assert valores["A"] == 7.0 and np.isnan(valores["B"])


@pytest.mark.parametrize("nome, dias, primeiro, ultimo, quantos", [
    ("Temperatura máxima", 1, 0.0, 45.0, 21),      # a faixa fixa do observado
    ("Umidade mínima", 1, 0.0, 100.0, 21),
    ("Rajada máxima", 1, 0.0, 130.0, 21),
    ("Chuva do dia", 1, 0.2, 100, 9),              # as classes curtas
    ("Chuva acumulada", 4, 0.2, 100, 9),           # 96 h ainda é janela curta
    ("Chuva acumulada", 7, 1, 300, 9),             # acima disso, as longas
])
def test_a_escala_e_a_mesma_dos_mapas_do_observado(nome, dias, primeiro, ultimo, quantos):
    niveis = previsao.niveis(previsao.MAPAS[nome], dias)

    assert (niveis[0], niveis[-1], len(niveis)) == (primeiro, ultimo, quantos)


def test_ajustar_a_escala_deixa_os_niveis_para_o_dado():
    assert previsao.niveis(previsao.MAPAS["Temperatura máxima"], ajustar=True) is None


def rede(passo: float = 0.5, faltando: tuple = ()) -> pd.DataFrame:
    lons, lats = np.meshgrid(np.arange(-56.0, -53.9, passo), np.arange(-22.0, -19.9, passo))
    pontos = pd.DataFrame({"latitude": lats.ravel(), "longitude": lons.ravel()})
    pontos["ponto"] = [f"{lat:.2f};{lon:.2f}" for lat, lon in zip(pontos["latitude"], pontos["longitude"])]
    return pontos[~pontos["ponto"].isin(faltando)].reset_index(drop=True)


def test_a_superficie_bilinear_reproduz_um_campo_plano():
    pontos = rede()
    valores = pd.Series(2.0 * pontos["longitude"].to_numpy() + 3.0 * pontos["latitude"].to_numpy(),
                        index=pontos["ponto"].to_numpy())
    lon_fina, lat_fina = np.meshgrid(np.linspace(-55.8, -54.2, 9), np.linspace(-21.8, -20.2, 9))

    grade = previsao.superficie(valores, pontos, lon_fina, lat_fina)

    assert np.allclose(grade, 2.0 * lon_fina + 3.0 * lat_fina)


def test_a_superficie_tira_o_passo_da_grade_dos_pontos():
    # A grade do banco é de 0,25°, e a da busca sob demanda, de 0,5°
    pontos = rede(passo=0.25)
    assert (previsao.espacamento(pontos), previsao.espacamento(rede())) == (0.25, 0.5)
    valores = pd.Series(2.0 * pontos["longitude"].to_numpy() + 3.0 * pontos["latitude"].to_numpy(),
                        index=pontos["ponto"].to_numpy())
    lon_fina, lat_fina = np.meshgrid(np.linspace(-55.8, -54.2, 9), np.linspace(-21.8, -20.2, 9))

    assert np.allclose(previsao.superficie(valores, pontos, lon_fina, lat_fina), 2.0 * lon_fina + 3.0 * lat_fina)


def test_a_superficie_nao_passa_da_faixa_do_modelo():
    pontos = rede()
    valores = pd.Series(np.where(pontos["latitude"] > -21, 30.0, 10.0), index=pontos["ponto"].to_numpy())
    lon_fina, lat_fina = np.meshgrid(np.linspace(-57.0, -53.0, 20), np.linspace(-23.0, -19.0, 20))

    grade = previsao.superficie(valores, pontos, lon_fina, lat_fina)

    assert grade.min() >= 10.0 and grade.max() <= 30.0      # nem fora do retângulo dos pontos


def test_um_no_que_falta_na_grade_nao_abre_buraco():
    pontos = rede(faltando=("-21.00;-55.00",))
    valores = pd.Series(20.0, index=pontos["ponto"].to_numpy())
    lon_fina, lat_fina = np.meshgrid(np.linspace(-55.5, -54.5, 5), np.linspace(-21.5, -20.5, 5))

    assert np.allclose(previsao.superficie(valores, pontos, lon_fina, lat_fina), 20.0)


def test_sem_o_dado_no_dia_nao_ha_superficie():
    pontos = rede()
    valores = pd.Series(np.nan, index=pontos["ponto"].to_numpy())

    assert previsao.superficie(valores, pontos, np.zeros((2, 2)), np.zeros((2, 2))) is None


ESTACOES = pd.DataFrame({"CD_ESTACAO": ["A702", "A721"], "Estação": ["Campo Grande", "Dourados"],
                         "VL_LATITUDE": ["-20.45", "-22.19"], "VL_LONGITUDE": ["-54.61", "-54.91"]})


def test_nas_estacoes_vai_a_previsao_do_ponto_de_cada_uma_com_o_nome_do_mapa():
    tabela = diaria({HOJE: 31.5}, pontos=("A702", "A721"), coluna="rajada_max")

    pontos = previsao.nas_estacoes(tabela, ESTACOES, previsao.MAPAS["Rajada máxima"], HOJE, HOJE)

    assert list(pontos["Estação"]) == ["Campo Grande", "Dourados"]
    assert list(pontos["Rajada máxima"]) == [31.5, 31.5]
    assert list(pontos["Direção (°)"]) == [90.0, 90.0]              # a seta da direção dominante
    assert pontos["Latitude"].iloc[0] == -20.45


def test_a_rajada_sem_direcao_e_a_mesma_rajada_sem_seta_nem_graus():
    tabela = diaria({HOJE: 31.5}, pontos=("A702", "A721"), coluna="rajada_max")
    limpa = previsao.MAPAS["Rajada s/ direção"]

    pontos = previsao.nas_estacoes(tabela, ESTACOES, limpa, HOJE, HOJE)

    assert list(pontos["Rajada s/ direção"]) == [31.5, 31.5]
    assert "Direção (°)" not in pontos
    assert (limpa.setas, limpa.titulo) == (False, previsao.MAPAS["Rajada máxima"].titulo)


def test_sem_a_previsao_das_estacoes_o_mapa_sai_sem_numeros():
    pontos = previsao.nas_estacoes(pd.DataFrame(), ESTACOES, previsao.MAPAS["Chuva do dia"], HOJE, HOJE)

    assert pontos.empty and "Chuva do dia" in pontos


def test_o_subtitulo_diz_so_a_data_e_o_carimbo_o_modelo_e_a_rodada():
    rodada = datetime(2026, 10, 8, 6, tzinfo=timezone.utc)
    dia = previsao.MAPAS["Chuva do dia"]
    acumulada = previsao.MAPAS["Chuva acumulada"]

    assert previsao.subtitulo(dia, date(2026, 10, 10), HOJE) == "Previsão para 10/10/2026"
    assert previsao.subtitulo(acumulada, date(2026, 10, 14), HOJE) == "Previsão de 08/10 a 14/10/2026"
    assert previsao.subtitulo_do_risco(date(2026, 10, 10)) == "Previsão para 10/10/2026"
    assert (previsao.carimbo("icon_seamless", rodada, acumulada, date(2026, 10, 14), HOJE)
            == "ICON_20261008_a_20261014_rodada_20261008_06UTC")


def test_o_png_do_boletim_da_previsao_leva_o_credito_do_open_meteo_no_rodape():
    base = mapas.carregar_base()
    mapa = previsao.MAPAS["Temperatura máxima"]
    pontos = previsao.nas_estacoes(diaria({HOJE: 33.0}, pontos=("A702", "A721"), coluna="temp_max"),
                                   ESTACOES, mapa, HOJE, HOJE)
    espec = boletim.espec(mapa.titulo, mapa.grandeza, mapa.unidade, mapa.paleta, mapa.decimais, "MS",
                          "Previsão para 08/10/2026", credito=previsao.CREDITO, coluna=mapa.nome,
                          credito_no_rodape=True)

    figura = mapas.mapa_de_grade(np.full(base.lon_grade.shape, 30.0),
                                 mapas.preparar_pontos(pontos, mapa.nome), espec, base,
                                 niveis=previsao.niveis(mapa))

    assert figura.axes[0].get_title() == "Temperatura máxima prevista em MS\nPrevisão para 08/10/2026"
    assert "Dados: Open-Meteo (CC BY 4.0)/SEMADESC" in [texto.get_text() for texto in figura.axes[0].texts]
    assert mapas.EspecMapa("", "", "", "", "", "", "", "").credito == "INMET/SEMADESC"


# =====================================================
# AS SEMANAS
# =====================================================
RODADA_EC46 = datetime(2026, 10, 7, 0, tzinfo=timezone.utc)


def semanal(semanas: dict, pontos=("A", "B")) -> pd.DataFrame:
    return pd.DataFrame([{"modelo": "ecmwf_ec46_ensemble_mean", "rodada_utc": RODADA_EC46, "ponto": ponto,
                          "semana": semana, "anom_temperatura": valor, "anom_chuva": valor * 10}
                         for semana, valor in semanas.items() for ponto in pontos])


def test_so_entram_as_semanas_que_comecam_depois_da_rodada():
    tabela = semanal({date(2026, 10, 5): 1.0, date(2026, 10, 12): 2.0, date(2026, 10, 19): 3.0})

    assert previsao.semanas_inteiras(tabela, RODADA_EC46) == [date(2026, 10, 12), date(2026, 10, 19)]


def test_numa_segunda_a_semana_da_rodada_entra_inteira():
    tabela = semanal({date(2026, 10, 12): 2.0})

    assert previsao.semanas_inteiras(tabela, datetime(2026, 10, 12, tzinfo=timezone.utc)) == [date(2026, 10, 12)]


def test_a_semana_se_escreve_de_segunda_a_domingo():
    assert previsao.nome_da_semana(date(2026, 10, 12)) == "12/10 a 18/10"


def test_o_mapa_da_semana_e_a_anomalia_de_cada_ponto():
    tabela = semanal({date(2026, 10, 12): -1.8, date(2026, 10, 19): -1.0})

    valores = previsao.valores_da_semana(tabela, previsao.MAPAS_SEMANAIS["Anomalia da chuva"], date(2026, 10, 12))

    assert valores.to_dict() == {"A": -18.0, "B": -18.0}


def test_nas_estacoes_a_coluna_leva_o_titulo_que_nao_se_confunde_com_os_dias():
    tabela = semanal({date(2026, 10, 12): -1.8}, pontos=("A702", "A721"))
    mapa = previsao.MAPAS_SEMANAIS["Anomalia da temperatura média"]

    pontos = previsao.nas_estacoes_na_semana(tabela, ESTACOES, mapa, date(2026, 10, 12))

    assert list(pontos["Anomalia da temperatura média"]) == [-1.8, -1.8]
    assert "Temperatura média" not in pontos


def test_todo_mapa_das_semanas_diz_que_e_anomalia():
    assert all(nome.startswith("Anomalia") for nome in previsao.MAPAS_SEMANAIS)
    assert set(previsao.PADRAO_SEMANAIS) <= set(previsao.MAPAS_SEMANAIS)


@pytest.mark.parametrize("niveis", [previsao.NIVEIS_ANOMALIA_TEMPERATURA, previsao.NIVEIS_ANOMALIA_CHUVA])
def test_a_escala_da_anomalia_tem_o_normal_no_meio(niveis):
    abaixo = sum(1 for nivel in niveis if nivel < 0)
    acima = sum(1 for nivel in niveis if nivel > 0)
    assert abaixo == acima
    assert -niveis[abaixo - 1] == niveis[abaixo]                  # a faixa do normal é simétrica


def test_subtitulo_e_carimbo_da_semana():
    assert previsao.subtitulo_da_semana(date(2026, 10, 12)) == "Semana de 12/10 a 18/10/2026"
    assert previsao.carimbo_da_semana(RODADA_EC46, date(2026, 10, 12)) == "EC46_semana_20261012_rodada_20261007_00UTC"


# =====================================================
# O RISCO DE FOGO PREVISTO
# =====================================================
def test_o_nivel_de_cada_hora_e_o_da_regra_do_produto():
    horas = horaria("2026-10-08 01:00", 4, modelos=("ecmwf_ifs025",),
                    temperatura=[35.0, 35.0, 25.0, 35.0], umidade=[20.0, 40.0, 40.0, 20.0],
                    rajada=[40.0, 40.0, 10.0, 29.9])

    assert list(previsao.risco_por_hora(horas)["nivel"]) == [3, 2, 0, 2]


def test_hora_sem_uma_das_tres_variaveis_fica_fora_do_risco():
    horas = horaria("2026-10-08 01:00", 2, modelos=("ecmwf_ifs025",), temperatura=[35.0, np.nan],
                    umidade=[20.0, 20.0], rajada=[40.0, 40.0])

    assert len(previsao.risco_por_hora(horas)) == 1


def test_o_dia_do_risco_e_o_pior_nivel_e_as_horas_em_risco_alto():
    temperatura = [35.0] * 3 + [25.0] * 20 + [35.0] + [35.0]          # 01:00 do dia 8 à 01:00 do dia 9
    horas = horaria("2026-10-08 01:00", 25, modelos=("ecmwf_ifs025",), temperatura=temperatura,
                    umidade=[20.0] * 25, rajada=[40.0] * 25)

    dias = previsao.risco_por_dia(horas).set_index("dia_previsto")

    assert dias.loc[date(2026, 10, 8), "risco_max"] == 3
    assert dias.loc[date(2026, 10, 8), "horas_risco_alto"] == 4
    assert dias.loc[date(2026, 10, 9), "horas_risco_alto"] == 1


def test_a_tabela_diaria_e_a_planilha_levam_o_risco():
    horas = horaria("2026-10-08 01:00", 24, temperatura=[35.0] * 24, umidade=[20.0] * 24, rajada=[10.0] * 24)

    assert set(previsao.diario_com_risco(horas)["risco_max"]) == {2}
    assert {"risco_max", "horas_risco_alto"} <= set(previsao.planilha(horas).columns)


def test_na_grade_a_regra_e_aplicada_celula_a_celula_hora_a_hora():
    pontos = rede()
    linhas = []
    for hora in pd.date_range("2026-10-08 01:00", periods=24, freq="h", tz=config.FUSO_MS):
        for ponto in pontos.itertuples():
            linhas.append({"modelo": "ecmwf_ifs025", "rodada_utc": RODADA_00, "ponto": ponto.ponto,
                           "latitude": ponto.latitude, "longitude": ponto.longitude,
                           "hora_prevista_utc": hora.tz_convert("UTC"),
                           "temperatura": 35.0 if ponto.longitude < -55 else 25.0,
                           "umidade": 20.0, "rajada": 40.0})
    horas = pd.DataFrame(linhas)
    lon_fina, lat_fina = np.meshgrid(np.linspace(-55.9, -54.1, 10), np.linspace(-21.5, -20.5, 3))

    nivel, horas_alto = previsao.risco_na_grade(horas, pontos, date(2026, 10, 8), lon_fina, lat_fina)

    assert list(nivel[0, [0, -1]]) == [3, 2]                       # oeste no alto, leste no médio
    assert horas_alto[0, 0] == 24 and horas_alto[0, -1] == 0


def test_sem_as_tres_variaveis_no_dia_nao_ha_grade_de_risco():
    pontos = rede()
    horas = pd.DataFrame(columns=openmeteo.COLUNAS_HORARIAS)

    assert previsao.risco_na_grade(horas, pontos, date(2026, 10, 8), np.zeros((2, 2)), np.zeros((2, 2))) is None


def test_os_dois_mapas_do_risco_estao_no_catalogo():
    assert previsao.MAPAS["Risco de fogo"].risco == "nivel"
    assert previsao.MAPAS["Horas em risco alto"].risco == "horas"


def test_a_normal_da_semana_e_a_previsao_menos_a_anomalia():
    tabela = pd.DataFrame([
        {"ponto": "A702", "semana": date(2026, 10, 5), "temperatura": 24.9, "anom_temperatura": -0.8},
        {"ponto": "A702", "semana": date(2026, 10, 12), "temperatura": 23.8, "anom_temperatura": -1.8},
        {"ponto": "A702", "semana": date(2026, 10, 19), "temperatura": 24.1, "anom_temperatura": -1.0},
        {"ponto": "A721", "semana": date(2026, 10, 12), "temperatura": 22.6, "anom_temperatura": -2.2},
    ])

    serie = previsao.semana_contra_o_normal(tabela, "A702", "Temperatura média", RODADA_EC46)

    assert list(serie["rotulo"]) == ["12/10 a 18/10", "19/10 a 25/10"]      # a semana da rodada fica de fora
    assert list(serie["prevista"]) == [23.8, 24.1]
    assert list(serie["normal"]) == [25.6, 25.1]
    assert list(serie["anomalia"]) == [-1.8, -1.0]


def test_sem_as_medias_da_semana_o_grafico_fica_vazio():
    tabela = semanal({date(2026, 10, 12): -1.8})          # só as anomalias, como antes de 08/10

    assert previsao.semana_contra_o_normal(tabela, "A", "Chuva", RODADA_EC46).empty


# =====================================================
# O PONTO NO MAPA INTERATIVO
# =====================================================
def test_o_ponto_tem_a_mesma_bilinear_que_pinta_o_mapa():
    pontos = rede(passo=0.25)
    valores = pd.Series(np.random.default_rng(3).normal(25, 5, len(pontos)), index=pontos["ponto"].to_numpy())
    lons, lats = np.array([-55.83, -55.1, -54.37]), np.array([-21.61, -20.9, -20.12])

    no_ponto = previsao.no_ponto(valores.to_frame().T, previsao.vizinhos(pontos, lons, lats))

    assert np.allclose(no_ponto.iloc[0], previsao.superficie(valores, pontos, lons, lats))


def test_um_vizinho_sem_valor_deixa_o_peso_para_os_outros():
    pontos = rede()
    largo = pd.DataFrame([dict.fromkeys(pontos["ponto"], 10.0)])
    largo.loc[0, "-21.00;-55.00"] = np.nan

    assert previsao.no_ponto(largo, previsao.vizinhos(pontos, [-55.2], [-21.2])).iloc[0, 0] == pytest.approx(10.0)


def test_fora_da_grade_o_ponto_fica_sem_valor():
    pontos = rede()
    largo = pd.DataFrame([dict.fromkeys(pontos["ponto"], 10.0)])

    assert np.isnan(previsao.no_ponto(largo, previsao.vizinhos(pontos, [-60.0], [-21.0])).iloc[0, 0])


def diaria_na_rede(pontos: pd.DataFrame, dias: dict, horas: dict | None = None) -> pd.DataFrame:
    # Um campo plano por dia, somado a uma base que muda de dia para dia
    return pd.DataFrame([{"modelo": "ecmwf_ifs025", "ponto": ponto.ponto, "dia_previsto": dia,
                          "temp_max": base + 2.0 * ponto.longitude + 3.0 * ponto.latitude,
                          "horas": (horas or {}).get(dia, 24)}
                         for dia, base in dias.items() for ponto in pontos.itertuples()])


def test_o_dia_no_ponto_sai_dos_dias_inteiros_de_hoje_em_diante():
    tabela = diaria_na_rede(rede(), {date(2026, 10, 7): 0.0, HOJE: 100.0, date(2026, 10, 9): 200.0,
                                     date(2026, 10, 10): 300.0}, horas={date(2026, 10, 10): 20})

    serie = previsao.diario_no_ponto(tabela, rede(), -55.2, -21.3, "temp_max", HOJE)

    plano = 2.0 * -55.2 + 3.0 * -21.3
    assert list(serie.index) == [HOJE, date(2026, 10, 9)]
    assert np.allclose(serie.to_numpy(), [100.0 + plano, 200.0 + plano])


def test_no_imovel_o_dia_e_a_media_das_celulas():
    tabela = diaria_na_rede(rede(), {HOJE: 100.0})

    serie = previsao.diario_no_ponto(tabela, rede(), [-55.5, -54.5], [-21.0, -21.0], "temp_max", HOJE)

    assert serie.loc[HOJE] == pytest.approx(100.0 + 2.0 * -55.0 + 3.0 * -21.0)


def horaria_na_rede(pontos: pd.DataFrame, quantas: int = 24) -> pd.DataFrame:
    # O oeste (longitude abaixo de -55) quente, e o leste ameno; a umidade e a rajada valem em toda parte
    return pd.DataFrame([{"modelo": "ecmwf_ifs025", "rodada_utc": RODADA_00, "ponto": ponto.ponto,
                          "latitude": ponto.latitude, "longitude": ponto.longitude,
                          "hora_prevista_utc": hora.tz_convert("UTC"),
                          "temperatura": 35.0 if ponto.longitude < -55 else 25.0, "umidade": 20.0, "rajada": 40.0}
                         for hora in pd.date_range("2026-10-08 01:00", periods=quantas, freq="h", tz=config.FUSO_MS)
                         for ponto in pontos.itertuples()])


def test_o_risco_no_ponto_e_o_da_celula_no_mapa():
    pontos = rede()
    horas = horaria_na_rede(pontos)
    # Entre -55,5 (35 °C) e -55,0 (25 °C): a -55,4 a bilinear dá 33 °C, e a -55,2, 29 °C
    lons, lats = np.array([-55.9, -55.4, -55.2, -54.1]), np.full(4, -21.0)
    nivel, horas_alto = previsao.risco_na_grade(horas, pontos, date(2026, 10, 8), lons.reshape(1, -1),
                                                lats.reshape(1, -1))

    no_ponto = [previsao.risco_no_ponto(previsao.largos_do_risco(horas), pontos, lon, lat, date(2026, 10, 8))
                for lon, lat in zip(lons, lats)]

    assert [int(dia.loc[0, "risco_max"]) for dia in no_ponto] == list(nivel[0]) == [3, 3, 2, 2]
    assert [int(dia.loc[0, "horas_risco_alto"]) for dia in no_ponto] == list(horas_alto[0]) == [24, 24, 0, 0]


def test_no_imovel_o_risco_e_o_pior_das_celulas():
    pontos = rede()

    dia = previsao.risco_no_ponto(previsao.largos_do_risco(horaria_na_rede(pontos)), pontos, [-55.9, -54.1],
                                  [-21.0, -21.0], date(2026, 10, 8))

    assert (dia.loc[0, "risco_max"], dia.loc[0, "horas_risco_alto"]) == (3, 24)


def test_dia_incompleto_nao_entra_no_risco_do_ponto():
    pontos = rede()

    dias = previsao.risco_no_ponto(previsao.largos_do_risco(horaria_na_rede(pontos, quantas=12)), pontos, -55.9,
                                   -21.0, date(2026, 10, 8))

    assert dias.empty


def test_o_rumo_no_mapa_sai_das_componentes_e_nao_da_media_dos_graus():
    pontos = rede()
    valores = pd.Series(20.0, index=pontos["ponto"].to_numpy())
    direcoes = pd.Series(np.where(pontos["longitude"] < -55.2, 350.0, 10.0), index=pontos["ponto"].to_numpy())

    graus = previsao.rumo_na_grade(valores, direcoes, pontos, np.array([-55.25]), np.array([-21.0]))[0]

    assert min(graus, 360 - graus) < 1e-6           # o norte, e não os 180° da média de 350° e 10°


# --- hora a hora e semanas ------------------------------------------------------------------
def test_todo_mapa_da_hora_existe_na_previsao_horaria():
    colunas = set(openmeteo.COLUNAS_HORARIAS)

    assert all(mapa.coluna in colunas for mapa in previsao.MAPAS_DA_HORA.values() if not mapa.risco)
    assert previsao.MAPAS_DA_HORA["Risco de fogo"].risco == "hora"


def test_a_chuva_da_hora_usa_as_classes_curtas_do_observado():
    assert previsao.niveis(previsao.MAPAS_DA_HORA["Chuva na hora"], horas=1.0)[0] == 0.2
    assert previsao.niveis(previsao.MAPAS_DA_HORA["Temperatura"], horas=1.0)[-1] == 45.0


def test_as_horas_do_deslizante_sao_as_de_agora_em_diante_com_o_dia_da_semana():
    horas = horaria("2026-10-08 01:00", 5, modelos=("ecmwf_ifs025",))
    agora = pd.Timestamp("2026-10-08 03:00", tz=config.FUSO_MS).tz_convert("UTC")

    escolhidas = previsao.horas_previstas(horas, agora)

    assert len(escolhidas) == 3 and escolhidas[0] == agora
    assert previsao.nome_da_hora(escolhidas[0]) == "qui 08/10 03:00"


def test_nas_estacoes_a_hora_leva_o_valor_e_a_direcao_do_modelo():
    horas = horaria("2026-10-08 01:00", 2, modelos=("ecmwf_ifs025",), rajada=[40.0, 55.0], direcao=[90.0, 180.0])
    segunda = horas["hora_prevista_utc"].iloc[1]

    tabela = previsao.nas_estacoes_na_hora(horas, ESTACOES, previsao.MAPAS_DA_HORA["Rajada"], segunda)

    assert tabela.loc[0, ["Estação", "Rajada", "Direção (°)"]].tolist() == ["Campo Grande", 55.0, 180.0]


def test_no_risco_da_hora_a_estacao_leva_o_nivel_da_regra():
    horas = horaria("2026-10-08 01:00", 1, modelos=("ecmwf_ifs025",), temperatura=[35.0], umidade=[20.0],
                    rajada=[10.0])

    tabela = previsao.nas_estacoes_na_hora(horas, ESTACOES, previsao.MAPAS_DA_HORA["Risco de fogo"],
                                           horas["hora_prevista_utc"].iloc[0])

    assert tabela.loc[0, "Risco de fogo"] == 2


def test_a_hora_no_ponto_e_a_mesma_bilinear_do_mapa():
    pontos = rede()
    horas = horaria_na_rede(pontos, quantas=3)
    largo = previsao.por_hora_e_ponto(horas, "temperatura")

    serie = previsao.horario_no_ponto(largo, pontos, -55.4, -21.0)

    assert len(serie) == 3 and np.allclose(serie, 33.0)          # entre 35 °C a -55,5 e 25 °C a -55,0


def test_o_risco_da_hora_na_grade_sai_das_tres_medidas_interpoladas():
    pontos = rede()
    horas = horaria_na_rede(pontos, quantas=2)
    lons, lats = np.array([[-55.9, -55.2]]), np.array([[-21.0, -21.0]])

    campos = previsao.risco_da_hora(horas, pontos, horas["hora_prevista_utc"].iloc[0], lons, lats)

    assert np.allclose(campos[0], [[35.0, 29.0]])
    assert risco_fogo.contar_condicoes(*campos).tolist() == [[3, 2]]


def semanal_na_rede(pontos: pd.DataFrame, semanas: dict) -> pd.DataFrame:
    # A anomalia é um campo plano; a previsão, a anomalia somada a uma normal de 25 °C
    return pd.DataFrame([{"modelo": "ecmwf_ec46_ensemble_mean", "rodada_utc": RODADA_EC46, "ponto": ponto.ponto,
                          "semana": semana, "anom_temperatura": base + 0.5 * (ponto.longitude + 55.0),
                          "temperatura": 25.0 + base + 0.5 * (ponto.longitude + 55.0)}
                         for semana, base in semanas.items() for ponto in pontos.itertuples()])


def test_a_semana_no_ponto_traz_a_previsao_a_normal_e_a_anomalia_do_balao():
    pontos = rede()
    tabela = semanal_na_rede(pontos, {date(2026, 10, 5): 9.0, date(2026, 10, 12): 1.0, date(2026, 10, 19): -1.0})

    semana = previsao.semana_no_ponto(tabela, pontos, -54.6, -21.0, previsao.MAPAS_SEMANAIS[
        "Anomalia da temperatura média"], RODADA_EC46)

    assert list(semana["semana"]) == [date(2026, 10, 12), date(2026, 10, 19)]     # a da rodada fica de fora
    assert np.allclose(semana["anomalia"], [1.2, -0.8]) and np.allclose(semana["normal"], 25.0)
    assert semana.loc[0, "rotulo"] == "12/10 a 18/10"


def test_na_estacao_cada_anomalia_vai_com_a_previsao_dela():
    tabela = semanal({date(2026, 10, 12): 1.5}).assign(temperatura=26.5)

    semana = previsao.semana_na_estacao(tabela, "A", previsao.MAPAS_SEMANAIS["Anomalia da temperatura média"],
                                        RODADA_EC46)

    assert semana.loc[0, ["prevista", "normal", "anomalia"]].tolist() == [26.5, 25.0, 1.5]
