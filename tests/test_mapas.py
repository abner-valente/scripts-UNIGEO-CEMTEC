"""Mapas: base cartográfica (máscara da grade e recorte pelo estado), estações e indicadores."""
from dataclasses import replace
import geopandas as gpd
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

    # Com apoio são duas contas: a superfície, com a vizinha, e a do estado sozinho, que dá a faixa
    # em que a escala de cores fica presa (ver test_a_vizinha_nao_estica_a_escala_de_cores).
    assert quantos == [len(gdf), len(gdf) + 1, len(gdf)]


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



def test_as_vizinhas_entram_so_no_mapa_interpolado(monkeypatch, tmp_path, base):
    """O mapa pontual é o do estado; as vizinhas só seguram a borda da superfície."""
    recebidos = {}
    monkeypatch.setattr(mapas, "mapa_pontual",
                        lambda dados, espec, base, caminho: recebidos.setdefault("pontual", len(dados)))
    monkeypatch.setattr(mapas, "mapa_interpolado",
                        lambda dados, espec, base, caminho, apoio=None:
                        recebidos.setdefault("interpolado", (len(dados), 0 if apoio is None else len(apoio))))
    tabela = ESTACOES.assign(**{"Chuva (mm)": [0.0, 5.0, 12.0, 3.0, 8.0]})
    vizinhas = pd.DataFrame({"Estação": ["V1", "V2"], "Latitude": [-24.2, -21.8],
                             "Longitude": [-54.2, -52.1], "Chuva (mm)": [4.0, 7.0]})

    mapas.gerar_mapas({"Chuva": tabela}, [CHUVA], tmp_path, "teste", base=base,
                      apoio={"Chuva": vizinhas})

    assert recebidos["pontual"] == len(tabela)                 # o pontual não vê as vizinhas
    assert recebidos["interpolado"] == (len(tabela), 2)        # a superfície vê as duas


def test_a_vizinha_nao_estica_a_escala_de_cores(monkeypatch, base):
    """Uma vizinha fora da faixa do estado não pode mudar a cor do miolo.

    Sem níveis dados, a escala se estica à superfície. Sem a trava, a borda puxada pela vizinha
    levava a régua junto: em MS, em 29/09/2026, a mínima foi de 18,3 para 16,4 °C por causa de uma
    estação do lado de lá, e um terço dos pixels do mapa mudou de cor sem o miolo ter mudado.
    """
    vistas = []
    monkeypatch.setattr(mapas, "mapa_de_grade", lambda grade, *args, **kwargs: vistas.append(grade))
    gdf = mapas._preparar_dados(ESTACOES.assign(**{"Chuva (mm)": [10.0, 12.0, 14.0, 16.0, 18.0]}), CHUVA)
    seca = mapas._preparar_dados(pd.DataFrame({"Estação": ["V"], "Latitude": [-24.3], "Longitude": [-54.5],
                                               "Chuva (mm)": [1.0]}), CHUVA)

    mapas.mapa_interpolado(gdf, CHUVA, base)
    mapas.mapa_interpolado(gdf, CHUVA, base, apoio=seca)

    sem, com = (grade[base.dentro_uf] for grade in vistas)
    assert not np.array_equal(sem, com)                     # a vizinha mexeu na superfície
    assert com.min() >= sem.min() and com.max() <= sem.max()   # mas dentro da faixa do estado


# =====================================================
# AS CLASSES DESIGUAIS E OS MAPAS DA PREVISÃO
# =====================================================
def _sem_estacoes(coluna: str = "v"):
    return gpd.GeoDataFrame(pd.DataFrame({"Estação": [], coluna: []}), geometry=gpd.points_from_xy([], []),
                            crs="EPSG:4326")


def _cor_de_cada_classe(figura, niveis):
    from matplotlib.colors import to_hex
    superficie = next(colecao for colecao in figura.axes[0].collections if hasattr(colecao, "levels"))
    meios = [(a + b) / 2 for a, b in zip(niveis[:-1], niveis[1:])]
    return [to_hex(superficie.cmap(superficie.norm(meio))) for meio in meios]


def test_classes_desiguais_tem_uma_cor_por_classe_como_a_barra_da_tela():
    """Pela régua do valor, as classes de 1 a 50 mm da chuva saíam quase brancas no mapa, e a barra
    da tela (que pinta por classe) mostrava azuis bem mais fortes: quem lia pela barra errava."""
    from matplotlib import colormaps
    from matplotlib.colors import BoundaryNorm, to_hex
    base = mapas.carregar_base()
    niveis = [0.2, 1, 5, 10, 20, 30, 50, 75, 100]
    grade = (base.lon_grade - base.lon_grade.min()) / np.ptp(base.lon_grade) * 100
    espec = mapas.EspecMapa("", "v", "t", "", "", "Blues", "mm", "")

    figura = mapas.mapa_de_grade(grade, _sem_estacoes(), espec, base, niveis=niveis, tela=mapas.Tela())

    por_classe = BoundaryNorm(niveis, ncolors=256, extend="max")
    barra = [to_hex(colormaps["Blues"](por_classe((a + b) / 2))) for a, b in zip(niveis[:-1], niveis[1:])]
    assert _cor_de_cada_classe(figura, niveis) == barra


def test_niveis_iguais_seguem_pintados_pela_regua_como_no_main_py():
    from matplotlib.colors import BoundaryNorm
    base = mapas.carregar_base()
    grade = (base.lon_grade - base.lon_grade.min()) / np.ptp(base.lon_grade) * 10
    espec = mapas.EspecMapa("", "v", "t", "", "", "Blues", "h", "")

    figura = mapas.mapa_de_grade(grade, _sem_estacoes(), espec, base, niveis=np.arange(0, 11), tela=mapas.Tela())

    superficie = next(colecao for colecao in figura.axes[0].collections if hasattr(colecao, "levels"))
    assert not isinstance(superficie.norm, BoundaryNorm)


def test_na_anomalia_o_normal_sai_branco_mesmo_com_a_escala_assimetrica():
    base = mapas.carregar_base()
    niveis = [-50, -30, -20, -10, -5, 5, 10, 25, 50, 100]
    grade = (base.lon_grade - base.lon_grade.min()) / np.ptp(base.lon_grade) * 150 - 50
    espec = mapas.EspecMapa("", "v", "t", "", "", "BrBG", "mm", "")

    cores = _cor_de_cada_classe(mapas.mapa_de_grade(grade, _sem_estacoes(), espec, base, niveis=niveis,
                                                    tela=mapas.Tela()), niveis)

    vermelho, verde, azul = (int(cores[4][i:i + 2], 16) for i in (1, 3, 5))
    assert min(vermelho, verde, azul) > 235                        # a classe de -5 a 5 é quase branca


def test_o_mapa_de_grade_sai_sem_estacoes():
    """A superfície da previsão vem do modelo: sem a previsão das estações, o mapa sai sem os pontos."""
    base = mapas.carregar_base()
    espec = mapas.EspecMapa("", "v", "t", "s", "", "Blues", "mm", "5 MAIORES ACUMULADOS")

    figura = mapas.mapa_de_grade(np.zeros(base.lon_grade.shape), _sem_estacoes(), espec, base, niveis=[1, 5, 25])

    assert figura is not None


def test_o_ranking_das_anomalias_e_pelo_tamanho_do_desvio():
    """Numa semana fria, "as maiores" seriam as menos frias; o que se procura é o maior desvio."""
    base = mapas.carregar_base()
    pontos = gpd.GeoDataFrame(pd.DataFrame({"Estação": list("ABCDEF"), "v": [0.4, -3.4, -0.3, -2.9, 1.0, -0.1]}),
                              geometry=gpd.points_from_xy([-54.0] * 6, [-20.0 - i * 0.3 for i in range(6)]),
                              crs="EPSG:4326")
    espec = mapas.EspecMapa("", "v", "t", "s", "", "RdBu_r", "°C", "5 MAIORES DESVIOS DO NORMAL",
                            ranking_absoluto=True)

    figura = mapas.mapa_de_grade(np.zeros(base.lon_grade.shape), pontos, espec, base,
                                 niveis=[-5, -1, -0.5, 0.5, 1, 5])

    ranking = next(texto.get_text() for texto in figura.axes[0].texts if texto.get_text().startswith("5 MAIORES"))
    assert ranking.splitlines()[1:6] == ["B: -3.4", "D: -2.9", "E: 1.0", "A: 0.4", "C: -0.3"]


def test_com_classes_a_barra_marca_todas_as_fronteiras():
    base = mapas.carregar_base()
    niveis = [-5, -4, -3, -2, -1, -0.5, 0.5, 1, 2, 3, 4, 5]
    espec = mapas.EspecMapa("", "v", "t", "s", "", "RdBu_r", "°C", "")

    figura = mapas.mapa_de_grade(np.zeros(base.lon_grade.shape), _sem_estacoes(), espec, base, niveis=niveis)

    assert list(figura.axes[1].get_yticks()) == niveis
