"""Mapas: base cartográfica (máscara da grade e recorte pelo estado), estações e indicadores."""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
import shapely

from modulos import config, mapas

ESTACOES = pd.DataFrame({
    "Estação": ["Campo Grande", "Dourados", "Corumba", "Tres Lagoas", "Coxim"],
    "Latitude": [-20.45, -22.22, -19.01, -20.79, -18.51],
    "Longitude": [-54.62, -54.81, -57.65, -51.70, -54.76],
})
CHUVA = mapas.EspecMapa("Chuva", "Chuva (mm)", "Chuva de teste", "subtítulo", "Mapa_Teste", "Blues",
                        "Chuva (mm)", "5 MAIORES ACUMULADOS")


@pytest.fixture(scope="module")
def base():
    return mapas.carregar_base()


def test_superficie_cobre_todas_as_celulas_que_tocam_o_estado(base):
    """Sem isso, a cor para antes da divisa e deixa falhas em degrau nas bordas do mapa interpolado."""
    lon, lat, dentro = base.lon_grade, base.lat_grade, base.dentro_uf
    estado = base.uf.geometry.union_all()
    shapely.prepare(estado)
    celulas = shapely.box(lon[:-1, :-1], lat[:-1, :-1], lon[1:, 1:], lat[1:, 1:])
    tocam_o_estado = shapely.intersects(celulas, estado)
    quatro_cantos_calculados = dentro[:-1, :-1] & dentro[1:, :-1] & dentro[:-1, 1:] & dentro[1:, 1:]
    assert np.all(quatro_cantos_calculados[tocam_o_estado])


def test_recorte_segue_o_contorno_do_estado(base):
    assert base.contorno_uf.contains_point((-54.62, -20.45))    # Campo Grande
    assert base.contorno_uf.contains_point((-57.65, -19.01))    # Corumbá, na divisa oeste
    assert not base.contorno_uf.contains_point((-56.10, -15.60))  # Cuiabá (MT)
    assert not base.contorno_uf.contains_point((-51.0, -17.3))    # Goiás, sob os logos


def test_estacoes_com_zero_entram_no_mapa():
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 0.0, 12.5, 0.0]})
    assert len(mapas._preparar_dados(tabela, CHUVA)) == 5


def test_indicadores_ocupam_sempre_a_mesma_posicao():
    """Cada indicador tem posição fixa sob a estação, mesmo quando os outros não aparecem."""
    tabela = pd.DataFrame({
        "Latitude": [-20.0, -22.0], "Longitude": [-55.0, -53.0], "Nível": [2, 1],
        "A": [True, False], "B": [False, True], "C": [True, False],
    })
    indicadores = mapas.Indicadores(["A", "B", "C"], ["a", "b", "c"], ["purple", "blue", "green"], "teste")
    fig, ax = plt.subplots()
    mapas._desenhar_indicadores(ax, mapas.preparar_pontos(tabela, "Nível"), indicadores)
    posicoes = [colecao.get_offsets() for colecao in ax.collections]
    plt.close(fig)

    passo, descida = mapas.PASSO_INDICADORES, mapas.DESCIDA_INDICADORES
    # A primeira estação tem A e C: se os pontos fossem centralizados, ficariam a meio passo da estação,
    # e não a um passo inteiro para cada lado
    esperadas = [
        [(-55.0 - passo, -20.0 - descida)],  # A: só a primeira estação, à esquerda
        [(-53.0, -22.0 - descida)],          # B: só a segunda, no meio
        [(-55.0 + passo, -20.0 - descida)],  # C: só a primeira, à direita
    ]
    assert len(posicoes) == 3
    for obtida, esperada in zip(posicoes, esperadas):
        np.testing.assert_allclose(obtida, esperada)


def test_dia_sem_chuva_em_nenhuma_estacao_ainda_gera_os_mapas(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DPI", 40)
    tabela = ESTACOES.assign(**{"Chuva (mm)": 0.0})
    mapas.gerar_mapas({"Chuva": tabela}, [CHUVA], tmp_path, "teste")
    assert sorted(p.name for p in tmp_path.glob("*.png")) == ["Mapa_Teste_teste.png", "Mapa_Teste_teste_interpolado.png"]
