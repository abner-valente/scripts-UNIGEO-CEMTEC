"""PNG do boletim: o mapa da tela na moldura do relatório, com os logos e as cores da tela."""
import io
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from app import boletim, chuva, variaveis
from modulos import config, mapas
from modulos.produtos import risco_fogo

ESTACOES = pd.DataFrame({
    "Estação": ["Campo Grande", "Dourados", "Corumba", "Tres Lagoas", "Coxim"],
    "Latitude": [-20.45, -22.22, -19.01, -20.79, -18.51],
    "Longitude": [-54.62, -54.81, -57.65, -51.70, -54.76],
})
MS = config.recorte_de("MS").fuso
SC = config.recorte_de("SC").fuso


@pytest.fixture(scope="module")
def base():
    return mapas.carregar_base()


# =====================================================
# O TEXTO DA MOLDURA
# =====================================================
def test_o_titulo_e_o_nome_da_tela_com_o_estado():
    assert boletim.titulo("Temperatura máxima", "SC") == "Temperatura máxima em SC"


@pytest.mark.parametrize("nome, grandeza, esperado", [
    ("Temperatura mínima", "Temperatura", ("5 MENORES TEMPERATURAS", False)),
    ("Umidade mínima da hora", "Umidade", ("5 MENORES UMIDADES", False)),
    ("Temperatura máxima", "Temperatura", ("5 MAIORES TEMPERATURAS", True)),
    ("Rajada máxima", "Vento", ("5 MAIORES RAJADAS", True)),
    ("Vento médio", "Vento", ("5 MAIORES VELOCIDADES", True)),
    ("Chuva acumulada em 24 h", "Chuva", ("5 MAIORES ACUMULADOS", True)),
    ("Algo novo", "Grandeza nova", ("5 MAIORES VALORES", True)),
])
def test_o_ranking_traz_as_menores_so_na_minima(nome, grandeza, esperado):
    """Como o main.py: a temperatura e a umidade mínimas ranqueiam as menores."""
    assert boletim.ranking(nome, grandeza) == esperado


def test_a_hora_vai_com_o_fuso_do_estado_como_no_mapa_horario_do_risco():
    """O mesmo texto que o main.py escreve nos mapas horários, para não haver duas formas."""
    em_ms = datetime(2026, 9, 3, 11, tzinfo=MS)
    em_sc = datetime(2026, 9, 3, 11, tzinfo=SC)

    assert boletim.instante(em_ms) == "03/09/2026 11:00 GMT-04"
    assert boletim.instante(em_sc) == "03/09/2026 11:00 GMT-03"
    assert boletim.instante(em_sc) == risco_fogo.espec_da_hora(em_sc, "SC").subtitulo


def test_o_subtitulo_de_cada_modo_e_o_que_o_main_py_escreveria():
    semana = config.Periodo.de_datas(date(2026, 9, 1), date(2026, 9, 7), fuso=MS)
    dia = pd.Timestamp("2026-09-03", tz=MS)
    hora = pd.Timestamp("2026-09-03 14:00", tz=MS)

    assert boletim.subtitulo(variaveis.PERIODO, None, semana) == "01/09/2026 a 07/09/2026"
    assert boletim.subtitulo(variaveis.PERIODO, None, semana) == semana.descrever_janela(*semana.janela)
    assert boletim.subtitulo(variaveis.DIA, dia, semana) == "03/09/2026"
    assert boletim.subtitulo(variaveis.HORA, hora, semana) == "03/09/2026 14:00 GMT-04"


def test_o_acumulado_da_chuva_ganha_nome_de_relatorio():
    assert boletim.nome_da_chuva("24 h") == "Chuva acumulada em 24 h"
    assert boletim.nome_da_chuva(chuva.MENSAL) == "Chuva acumulada no mês"


def test_a_moldura_leva_as_cores_da_tela_e_nao_as_do_main_py():
    """Decisão de 01/10/2026: o que se baixa é o que se vê. O main.py pintaria a mínima em coolwarm."""
    minima = variaveis.por_nome("Temperatura mínima")

    espec = boletim.espec(minima.nome, minima.grandeza, minima.unidade, minima.paleta, minima.decimais,
                          "MS", "03/09/2026")

    assert espec.cmap == minima.paleta == variaveis.PALETA_TEMPERATURA
    assert (espec.titulo, espec.subtitulo, espec.unidade) == (
        "Temperatura mínima em MS", "03/09/2026", "Temperatura (°C)")
    assert (espec.ranking, espec.maiores, espec.coluna) == ("5 MENORES TEMPERATURAS", False, minima.nome)


def test_o_arquivo_leva_a_sigla_do_estado():
    assert (boletim.nome_do_arquivo("Temperatura máxima", "SC", "20260903")
            == "Mapa_Temperatura_máxima_SC_20260903.png")


# =====================================================
# O DESENHO
# =====================================================
def test_o_mapa_do_boletim_sai_com_titulo_logos_ranking_e_a_escala_da_tela(base):
    """O caminho do relatório (sem Tela), com os níveis de cor que a tela estava usando."""
    nome = "Temperatura máxima"
    gdf = mapas.preparar_pontos(ESTACOES.assign(**{nome: [31.0, 29.5, 36.2, 33.1, 30.4]}), nome)
    espec = boletim.espec(nome, "Temperatura", "°C", variaveis.PALETA_TEMPERATURA, 1, "MS", "03/09/2026")
    escala_da_tela = np.linspace(0, 45, 21)

    figura = mapas.mapa_interpolado(gdf, espec, base, niveis=escala_da_tela)
    mapa = figura.axes[0]
    textos = [texto.get_text() for texto in mapa.texts]
    superficie = next(colecao for colecao in mapa.collections if hasattr(colecao, "levels"))

    assert mapa.get_title() == "Temperatura máxima em MS\n03/09/2026 — INMET/SEMADESC"
    assert len(mapa.child_axes) == len(config.LOGOS)                  # cada logo é um eixo filho
    assert any(texto.startswith("5 MAIORES TEMPERATURAS:\nCorumba: 36.2") for texto in textos)
    assert np.allclose(superficie.levels, escala_da_tela)             # a escala fixa da tela
    assert len(figura.axes) == 2                                      # o mapa e a barra de cores


@pytest.mark.parametrize("teto, esperadas", [
    (45, list(range(0, 50, 5))),     # temperatura: 21 níveis dariam marcas em 6,75, 13,50...
    (130, list(range(0, 140, 20))),  # vento: sem um 140 preso no topo da barra
])
def test_na_escala_fixa_a_barra_marca_numeros_redondos_e_dentro_da_faixa(base, teto, esperadas):
    nome = "Temperatura máxima"
    gdf = mapas.preparar_pontos(ESTACOES.assign(**{nome: [31.0, 29.5, 36.2, 33.1, 30.4]}), nome)
    espec = boletim.espec(nome, "Temperatura", "°C", variaveis.PALETA_TEMPERATURA, 1, "MS", "03/09/2026")

    barra = mapas.mapa_interpolado(gdf, espec, base, niveis=np.linspace(0, teto, 21)).axes[1]

    assert list(barra.get_yticks()) == esperadas


def test_o_png_sai_na_resolucao_do_relatorio(base, monkeypatch):
    """Grava como o main.py grava: a resolução é a de config.DPI."""
    nome = "Chuva acumulada em 24 h"
    gdf = mapas.preparar_pontos(ESTACOES.assign(**{nome: [0.0, 5.0, 12.0, 3.0, 8.0]}), nome)
    espec = boletim.espec(nome, "Chuva", "mm", "Blues", 1, "MS", "03/09/2026")

    larguras = []
    for dpi in (20, 40):
        monkeypatch.setattr(config, "DPI", dpi)
        dados = boletim.png(mapas.mapa_interpolado(gdf, espec, base))
        assert dados.startswith(b"\x89PNG")
        larguras.append(Image.open(io.BytesIO(dados)).width)

    assert larguras[1] == pytest.approx(2 * larguras[0], rel=0.05)
