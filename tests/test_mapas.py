"""Mapas: base cartográfica (máscara da grade e recorte pelo estado), estações e indicadores."""
from dataclasses import replace
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


def test_casas_decimais_dos_rotulos():
    """Contagens (como horas) saem inteiras; medidas continuam com uma casa."""
    horas = mapas.EspecMapa("Risco", "Horas", "t", "s", "a", "YlOrRd", "h", "r", decimais=0)
    assert mapas._formato(horas)(5.0) == "5"
    assert mapas._formato(CHUVA)(5.0) == "5.0"


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


def test_mapa_devolve_a_figura_sem_gravar_nada(base):
    """O explorador desenha na tela: pede a figura e não passa caminho nenhum."""
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)

    figura = mapas.mapa_interpolado(gdf, CHUVA, base)

    assert figura is not None
    assert len(figura.axes) >= 2  # o mapa e a barra de cores


def test_na_tela_o_mapa_sai_sem_titulo_nem_logos(base, monkeypatch, tmp_path):
    """Numa miniatura, título e logos institucionais só tomariam o lugar do mapa."""
    monkeypatch.setattr(config, "DPI", 40)
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)

    relatorio = mapas.mapa_interpolado(gdf, CHUVA, base, tmp_path / "relatorio.png")
    na_tela = mapas.mapa_interpolado(gdf, CHUVA, base, tela=mapas.Tela())

    assert relatorio.axes[0].get_title().startswith(CHUVA.titulo)
    assert na_tela.axes[0].get_title() == ""
    assert len(relatorio.axes[0].child_axes) == len(config.LOGOS)  # cada logo é um eixo filho
    assert na_tela.axes[0].child_axes == []
    assert (tmp_path / "relatorio.png").exists()  # o relatório continua saindo com tudo


def test_na_tela_o_mapa_sai_sem_barra_de_cores_e_sem_valores(base):
    """A barra vertical e os valores de 62 estações não sobrevivem ao tamanho de uma coluna."""
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)

    relatorio = mapas.mapa_interpolado(gdf, CHUVA, base)
    na_tela = mapas.mapa_interpolado(gdf, CHUVA, base, tela=mapas.Tela())
    com_valores = mapas.mapa_interpolado(gdf, CHUVA, base, tela=mapas.Tela(rotulos=True))

    assert len(relatorio.axes) == 2 and len(na_tela.axes) == 1  # o eixo a mais é a barra de cores
    assert len(na_tela.axes[0].texts) == 0
    assert len(com_valores.axes[0].texts) == len(ESTACOES)
    assert len(relatorio.axes[0].texts) > len(ESTACOES)  # os valores mais o quadro do ranking


def test_na_tela_nao_sai_a_grade_de_latitude_e_longitude(base):
    """Numa miniatura a moldura de coordenadas toma a borda inteira do desenho e não se lê."""
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)

    relatorio = mapas.mapa_interpolado(gdf, CHUVA, base)
    na_tela = mapas.mapa_interpolado(gdf, CHUVA, base, tela=mapas.Tela())

    assert relatorio.axes[0].axison and not na_tela.axes[0].axison
    assert relatorio.axes[0].get_xlabel() == "Longitude"
    # o recorte continua o mesmo: some a moldura, não o enquadramento
    assert na_tela.axes[0].get_xlim() == relatorio.axes[0].get_xlim()


def test_na_tela_os_valores_saem_maiores(base):
    """Encolhido para uma coluna, o corpo do relatório vira borrão."""
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)
    escala = 1.5

    relatorio = mapas.mapa_interpolado(gdf, CHUVA, base)
    na_tela = mapas.mapa_interpolado(gdf, CHUVA, base, tela=mapas.Tela(rotulos=True, escala_rotulos=escala))

    assert na_tela.axes[0].texts[0].get_fontsize() == pytest.approx(
        relatorio.axes[0].texts[0].get_fontsize() * escala)


def test_na_tela_o_valor_vai_contornado_e_sem_caixa(base):
    """Num mapa do tamanho de uma coluna, o que cobre a estação vizinha é a caixa, não a letra."""
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)

    relatorio = mapas.mapa_interpolado(gdf, CHUVA, base)
    na_tela = mapas.mapa_interpolado(gdf, CHUVA, base, tela=mapas.Tela(rotulos=True))

    assert na_tela.axes[0].texts[0].get_bbox_patch() is None
    assert na_tela.axes[0].texts[0].get_path_effects()  # o contorno é o que segura a leitura
    assert relatorio.axes[0].texts[0].get_bbox_patch() is not None  # na folha inteira, cabe


def test_a_base_segue_o_enquadramento_do_recorte(base):
    """O recorte anda junto com a chamada: quem pedir outro enquadramento recebe outra grade."""
    apertado = replace(config.RECORTE, limites=(-56.0, -53.0, -22.0, -19.0))

    outra = mapas.carregar_base(apertado)

    assert outra.recorte is apertado
    assert base.recorte is config.RECORTE
    assert (outra.lon_grade.min(), outra.lon_grade.max()) == (-56.0, -53.0)
    oeste, leste, _, _ = config.RECORTE.limites
    assert (base.lon_grade.min(), base.lon_grade.max()) == (oeste, leste)


def test_o_apoio_entra_na_interpolacao(base, monkeypatch):
    """Se alguém tirar o apoio da conta, o desenho continua igual e ninguém percebe — daí o espião."""
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)
    vizinha = mapas._preparar_dados(
        pd.DataFrame({"Estação": ["Fora"], "Latitude": [-19.0], "Longitude": [-51.0],
                      "Chuva (mm)": [200.0]}), CHUVA)
    quantos = []
    original = mapas.calculos.interpolar_idw
    monkeypatch.setattr(mapas.calculos, "interpolar_idw",
                        lambda lons, *resto: quantos.append(len(lons)) or original(lons, *resto))

    mapas.mapa_interpolado(gdf, CHUVA, base)
    mapas.mapa_interpolado(gdf, CHUVA, base, apoio=vizinha)

    assert quantos == [len(gdf), len(gdf) + 1]


def test_o_apoio_muda_a_superficie_e_nao_o_desenho(base):
    """As vizinhas seguram a borda: entram no IDW, mas não viram ponto, rótulo nem ranking."""
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)
    vizinha = mapas._preparar_dados(
        pd.DataFrame({"Estação": ["Fora"], "Latitude": [-19.0], "Longitude": [-51.0],
                      "Chuva (mm)": [200.0]}), CHUVA)

    sozinho = mapas.mapa_interpolado(gdf, CHUVA, base)
    com_apoio = mapas.mapa_interpolado(gdf, CHUVA, base, apoio=vizinha)

    desenhados = [colecao.get_offsets().shape[0] for colecao in com_apoio.axes[0].collections
                  if colecao.get_offsets() is not None and len(colecao.get_offsets())]
    assert max(desenhados) == len(gdf)          # nenhum ponto a mais no mapa
    superficie_antes = sozinho.axes[0].collections[0].get_array()
    superficie_depois = com_apoio.axes[0].collections[0].get_array()
    assert (superficie_antes is None) == (superficie_depois is None)
    assert len(com_apoio.axes[0].texts) == len(sozinho.axes[0].texts)   # nem rótulo, nem ranking


def test_poucas_estacoes_nao_viram_superficie(base):
    """Com dois pontos a interpolação inventaria o estado inteiro: melhor não desenhar."""
    tabela = ESTACOES.head(2).assign(**{"Chuva (mm)": [1.0, 2.0]})
    gdf = mapas._preparar_dados(tabela, CHUVA)
    assert mapas.mapa_interpolado(gdf, CHUVA, base, tela=mapas.Tela()) is None
