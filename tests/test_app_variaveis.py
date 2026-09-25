"""Catálogo de variáveis: cada produto resume a janela do jeito que a equipe definiu."""
import numpy as np
import pandas as pd
import pytest

from app import variaveis


def leituras(estacao: str, inicio: str, horas: int, **colunas) -> pd.DataFrame:
    """Leituras horárias de uma estação a partir de `inicio` (horário de MS)."""
    marcas = pd.date_range(inicio, periods=horas, freq="h", tz="America/Campo_Grande")
    return pd.DataFrame({"Estação": estacao, "dt_local": marcas, **colunas})


def um_dia(estacao: str = "Bonito", **colunas) -> pd.DataFrame:
    """As 24 horas do dia 17/09: da leitura das 01 h à das 00 h do dia seguinte."""
    return leituras(estacao, "2026-09-17 01:00", 24, **colunas)


# =====================================================
# AS REGRAS DE CADA PRODUTO
# =====================================================
def test_a_maxima_do_dia_e_a_maior_horaria_e_nao_a_media_delas():
    """O erro que motivou o catálogo: a média das máximas não é a máxima do dia."""
    horarias = [20.0] * 23 + [30.3]  # um dia morno com uma hora quente
    dia = um_dia(TEM_MAX=horarias)

    assert variaveis.calcular(variaveis.por_nome("Temperatura máxima"), dia) == pytest.approx(30.3)
    assert np.mean(horarias) == pytest.approx(20.4, abs=0.05)  # o que a tela mostrava antes


def test_a_minima_do_dia_e_a_menor_horaria():
    dia = um_dia(TEM_MIN=[18.0] * 23 + [11.5])
    assert variaveis.calcular(variaveis.por_nome("Temperatura mínima"), dia) == pytest.approx(11.5)


def test_a_media_do_dia_sai_das_leituras_da_hora_cheia():
    dia = um_dia(TEM_INS=[20.0] * 12 + [30.0] * 12, TEM_MAX=[40.0] * 24)
    assert variaveis.calcular(variaveis.por_nome("Temperatura média"), dia) == pytest.approx(25.0)


def test_a_media_da_hora_e_o_meio_dos_extremos():
    hora = leituras("Bonito", "2026-09-17 13:00", 1, TEM_MAX=[30.0], TEM_MIN=[20.0])
    media = variaveis.por_nome("Temperatura média da hora")
    assert variaveis.calcular(media, hora) == pytest.approx(25.0)


def test_umidade_do_mapa_e_a_menor_do_dia():
    dia = um_dia(UMD_MIN=[80.0] * 23 + [17.0])
    assert variaveis.calcular(variaveis.por_nome("Umidade mínima"), dia) == pytest.approx(17.0)


def test_rajada_do_dia_e_a_maior_rajada_horaria():
    dia = um_dia(VEN_RAJ=[10.0] * 23 + [83.9])
    assert variaveis.calcular(variaveis.por_nome("Rajada máxima"), dia) == pytest.approx(83.9)


def test_vento_medio_e_a_media_das_horarias():
    """VEN_VEL já é a média da hora, então a média do dia é a média das médias."""
    dia = um_dia(VEN_VEL=[10.0] * 12 + [20.0] * 12)
    assert variaveis.calcular(variaveis.por_nome("Vento médio"), dia) == pytest.approx(15.0)


def test_chuva_do_dia_soma_as_horas():
    dia = um_dia(CHUVA=[0.0] * 20 + [2.5, 10.0, 1.5, 0.0])
    assert variaveis.calcular(variaveis.por_nome("Chuva acumulada"), dia) == pytest.approx(14.0)


def test_radiacao_zera_o_ruido_negativo_antes_de_somar():
    """À noite o sensor devolve algo como -3,5: somado, viraria um desconto que não houve."""
    dia = um_dia(RAD_GLO=[-3.5] * 12 + [1000.0] * 12)
    acumulada = variaveis.por_nome("Radiação acumulada")
    assert variaveis.calcular(acumulada, dia) == pytest.approx(12000.0)


# =====================================================
# MÉDIA COMPENSADA
# =====================================================
def test_compensada_usa_as_9h_e_as_21h_do_horario_de_ms():
    dia = um_dia(TEM_INS=[15.0] * 24, TEM_MAX=[30.0] * 24, TEM_MIN=[10.0] * 24)
    dia.loc[dia["dt_local"].dt.hour == 9, "TEM_INS"] = 20.0
    dia.loc[dia["dt_local"].dt.hour == 21, "TEM_INS"] = 25.0

    compensada = variaveis.por_nome("Temperatura média compensada")

    # (20 + 2*25 + 10 + 30) / 5
    assert variaveis.calcular(compensada, dia) == pytest.approx(22.0)


def test_compensada_sem_a_leitura_das_21h_fica_em_branco():
    """Melhor um buraco no gráfico do que um número inventado."""
    dia = um_dia(TEM_INS=[15.0] * 24, TEM_MAX=[30.0] * 24, TEM_MIN=[10.0] * 24)
    dia.loc[dia["dt_local"].dt.hour == 21, "TEM_INS"] = np.nan

    assert np.isnan(variaveis.calcular(variaveis.por_nome("Temperatura média compensada"), dia))


def test_direcao_do_vento_nao_vira_mapa():
    """Interpolar ângulo não funciona: entre 350° e 10° a média daria 180°, o rumo oposto."""
    direcao = variaveis.por_nome("Direção na hora")
    assert direcao.onde == (variaveis.GRAFICO,)
    assert direcao not in variaveis.disponiveis(variaveis.HORA, variaveis.MAPA)


def test_compensada_so_aparece_em_grafico():
    """Ela é uma estatística do dia inteiro: como mapa, a equipe não quis."""
    compensada = variaveis.por_nome("Temperatura média compensada")
    assert compensada.onde == (variaveis.GRAFICO,)
    assert compensada not in variaveis.disponiveis(variaveis.DIA, variaveis.MAPA)


# =====================================================
# AGRUPAMENTOS
# =====================================================
def test_por_estacao_devolve_um_numero_por_estacao():
    tabela = pd.concat([um_dia("Bonito", TEM_MAX=[20.0] * 23 + [28.0]),
                        um_dia("Corumba", TEM_MAX=[30.0] * 23 + [39.0])], ignore_index=True)

    valores = variaveis.por_estacao(variaveis.por_nome("Temperatura máxima"), tabela)

    assert valores["Bonito"] == pytest.approx(28.0)
    assert valores["Corumba"] == pytest.approx(39.0)


def test_no_tempo_diario_respeita_o_fechamento_do_dia():
    """A leitura das 00:00 fecha o dia anterior: sete dias continuam sete."""
    semana = leituras("Bonito", "2026-09-17 01:00", 24 * 7, TEM_MAX=np.arange(24.0 * 7))

    serie = variaveis.no_tempo(variaveis.por_nome("Temperatura máxima"), semana, variaveis.DIA)

    assert len(serie) == 7
    assert [f"{dia:%d/%m}" for dia in serie.index] == [f"{17 + n}/09" for n in range(7)]
    assert serie.iloc[0]["Bonito"] == pytest.approx(23.0)  # a maior das 24 primeiras horas


def test_no_tempo_horario_mantem_uma_linha_por_hora():
    dia = um_dia(TEM_MAX=np.arange(24.0))
    serie = variaveis.no_tempo(variaveis.por_nome("Temperatura máxima da hora"), dia, variaveis.HORA)
    assert len(serie) == 24


def test_a_mesma_regra_serve_ao_mapa_e_ao_grafico():
    """É o ponto do catálogo: máxima do dia no mapa e no gráfico não podem discordar."""
    dia = um_dia(TEM_MAX=[20.0] * 23 + [31.4])
    maxima = variaveis.por_nome("Temperatura máxima")

    no_mapa = variaveis.por_estacao(maxima, dia)["Bonito"]
    no_grafico = variaveis.no_tempo(maxima, dia, variaveis.DIA).iloc[0]["Bonito"]

    assert no_mapa == pytest.approx(no_grafico)


# =====================================================
# O CATÁLOGO EM SI
# =====================================================
def test_cada_modo_oferece_os_produtos_daquele_modo():
    for modo in (variaveis.HORA, variaveis.DIA, variaveis.PERIODO):
        for produto in variaveis.disponiveis(modo):
            assert modo in produto.modos


def test_nao_existe_soma_de_temperatura():
    """A combinação errada não está escrita — é o que o catálogo garante."""
    calculos = {produto.calculo for produto in variaveis.PRODUTOS if produto.grandeza == "Temperatura"}
    assert "soma" not in calculos and "soma_sem_negativos" not in calculos


def test_todo_produto_tem_regra_calculo_conhecido_e_nome_unico():
    nomes = [produto.nome for produto in variaveis.PRODUTOS]
    assert len(nomes) == len(set(nomes))
    for produto in variaveis.PRODUTOS:
        assert produto.calculo in variaveis.CALCULOS
        assert produto.regra and produto.modos and produto.colunas and produto.onde


def test_grandezas_saem_na_ordem_do_catalogo_sem_repetir():
    assert variaveis.grandezas(variaveis.DIA) == ["Temperatura", "Umidade", "Pressão",
                                                  "Vento", "Radiação", "Chuva"]


def test_produto_desconhecido_avisa():
    with pytest.raises(KeyError):
        variaveis.por_nome("Temperatura do futuro")


# =====================================================
# RECORTE NO TEMPO (o deslizante do mapa)
# =====================================================
def test_momentos_do_modo_horario_sao_as_horas_da_janela():
    dia = um_dia(TEM_MAX=np.arange(24.0))
    assert len(variaveis.momentos(dia, variaveis.HORA)) == 24


def test_momentos_do_modo_diario_nao_inventam_um_oitavo_dia():
    semana = leituras("Bonito", "2026-09-17 01:00", 24 * 7, TEM_MAX=np.arange(24.0 * 7))
    dias = variaveis.momentos(semana, variaveis.DIA)
    assert [f"{dia:%d/%m}" for dia in dias] == [f"{17 + n}/09" for n in range(7)]


def test_o_periodo_inteiro_nao_tem_paradas():
    assert variaveis.momentos(um_dia(TEM_MAX=[1.0] * 24), variaveis.PERIODO) == []


def test_recorte_do_dia_pega_da_uma_da_manha_a_meia_noite_seguinte():
    """É a janela do dia em MS: a leitura das 00:00 do dia seguinte ainda é deste dia."""
    dois_dias = leituras("Bonito", "2026-09-17 01:00", 48, TEM_MAX=np.arange(48.0))
    primeiro = variaveis.momentos(dois_dias, variaveis.DIA)[0]

    fatia = variaveis.recorte(dois_dias, variaveis.DIA, primeiro)

    assert len(fatia) == 24
    assert f"{fatia['dt_local'].min():%d/%m %H:%M}" == "17/09 01:00"
    assert f"{fatia['dt_local'].max():%d/%m %H:%M}" == "18/09 00:00"


def test_recorte_do_periodo_devolve_tudo():
    dia = um_dia(TEM_MAX=[1.0] * 24)
    assert len(variaveis.recorte(dia, variaveis.PERIODO)) == 24


# =====================================================
# RÓTULOS DA LEGENDA
# =====================================================
def test_rotulo_curto_nao_repete_a_grandeza():
    """Num gráfico de Temperatura, a legenda não precisa dizer "Temperatura" quatro vezes."""
    assert variaveis.rotulo_curto(variaveis.por_nome("Temperatura máxima da hora")) == "Máxima da hora"
    assert variaveis.rotulo_curto(variaveis.por_nome("Temperatura média compensada")) == "Média compensada"


def test_rotulo_que_ficaria_so_em_preposicao_mantem_o_nome_inteiro():
    """"Chuva na hora" viraria "Na hora", que não nomeia coisa alguma."""
    assert variaveis.rotulo_curto(variaveis.por_nome("Chuva na hora")) == "Chuva na hora"


def test_rotulo_de_produto_que_nao_cita_a_grandeza_fica_igual():
    assert variaveis.rotulo_curto(variaveis.por_nome("Rajada máxima")) == "Rajada máxima"


def test_rotulos_curtos_nao_colidem_dentro_da_mesma_grandeza():
    """Duas séries com o mesmo rótulo se confundiriam na legenda."""
    for modo in (variaveis.HORA, variaveis.DIA):
        por_grandeza = {}
        for produto in variaveis.disponiveis(modo, variaveis.GRAFICO):
            por_grandeza.setdefault(produto.grandeza, []).append(variaveis.rotulo_curto(produto))
        for grandeza, rotulos in por_grandeza.items():
            assert len(rotulos) == len(set(rotulos)), f"{grandeza} em {modo}: {rotulos}"
