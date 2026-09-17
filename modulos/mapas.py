"""Mapas pontuais e interpolados (IDW) das variáveis meteorológicas."""
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")  # gera os arquivos sem abrir janelas (permite rodar agendado)

import matplotlib.path as mpath
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from . import calculos, config


@dataclass(frozen=True)
class EspecMapa:
    """O que plotar (aba e coluna) e como apresentar (títulos, cores, ranking)."""

    tabela: str
    coluna: str
    titulo: str
    subtitulo: str
    arquivo: str
    cmap: str
    unidade: str
    ranking: str
    maiores: bool = True         # ranking dos maiores (True) ou dos menores (False)
    direcao_vento: bool = False  # desenha as setas de direção do vento
    decimais: int = 1            # casas decimais dos rótulos e do ranking (0 para contagens, como horas)


@dataclass(frozen=True)
class EspecClasses:
    """Mapa de classes discretas (ex.: níveis de risco): uma cor e um rótulo para cada classe.

    Diferente do EspecMapa, o valor não vem de uma coluna contínua com barra de cores: as classes
    são desenhadas com cores fixas e uma legenda nomeada.
    """

    titulo: str
    subtitulo: str
    arquivo: str
    cores: list[str]
    rotulos: list[str]


@dataclass(frozen=True)
class Indicadores:
    """Pontos coloridos abaixo de cada estação, um para cada coluna verdadeira (ex.: condições atendidas).

    Cada indicador ocupa sempre a mesma posição, da esquerda para a direita na ordem de `colunas`.
    """

    colunas: list[str]  # colunas booleanas da tabela de estações
    rotulos: list[str]
    cores: list[str]
    titulo: str


# Pontos dos indicadores: distância entre posições e descida abaixo do rótulo (em graus) e área de cada ponto (pt²)
PASSO_INDICADORES = 0.08
DESCIDA_INDICADORES = 0.16
TAMANHO_INDICADORES = 30


@dataclass
class BaseCartografica:
    """Camadas, grade e logos carregados uma única vez e reaproveitados em todos os mapas."""

    uf: gpd.GeoDataFrame
    municipios: gpd.GeoDataFrame
    lon_grade: np.ndarray
    lat_grade: np.ndarray
    dentro_uf: np.ndarray    # máscara: pontos da grade dentro do estado, com margem de 2 células
    contorno_uf: mpath.Path  # contorno exato do estado, usado para recortar a superfície interpolada
    logos: list


def carregar_base() -> BaseCartografica:
    uf = gpd.read_file(config.SHAPE_UF).to_crs("EPSG:4326")
    municipios = gpd.read_file(config.SHAPE_MUN).to_crs("EPSG:4326")
    lon_grade, lat_grade = calculos.criar_grade(
        (config.LON_MIN, config.LON_MAX, config.LAT_MIN, config.LAT_MAX), config.RESOLUCAO_GRADE
    )
    # A superfície é calculada um pouco além da divisa (2 células da grade) e depois recortada
    # exatamente pelo contorno do estado: a cor chega até a divisa, sem falhas em degrau.
    estado = uf.geometry.union_all()
    passo = max(config.LON_MAX - config.LON_MIN, config.LAT_MAX - config.LAT_MIN) / (config.RESOLUCAO_GRADE - 1)
    dentro_uf = shapely.contains_xy(estado.buffer(2 * passo), lon_grade, lat_grade)

    logos = []
    for arquivo, retangulo in config.LOGOS:
        if arquivo.exists():
            logos.append((plt.imread(arquivo), retangulo))
        else:
            print(f"⚠️ Logo não encontrado: {arquivo}")
    return BaseCartografica(uf, municipios, lon_grade, lat_grade, dentro_uf, _caminho_matplotlib(estado), logos)


def gerar_mapas(tabelas: dict[str, pd.DataFrame], especificacoes: list[EspecMapa], pasta: Path,
                identificador: str) -> None:
    """Gera, na pasta indicada, os mapas descritos pelas especificações (identificador vai no nome dos arquivos)."""
    if not tabelas:
        print("⚠️ Nenhum dado foi coletado. Os mapas não serão gerados.")
        return
    try:
        base = carregar_base()
    except Exception as erro:
        print(f"⚠️ Não foi possível carregar os shapefiles: {erro}")
        return

    pasta.mkdir(parents=True, exist_ok=True)
    for espec in especificacoes:
        dados = _preparar_dados(tabelas.get(espec.tabela), espec)
        if dados is None:
            continue
        nome = f"{espec.arquivo}_{identificador}"
        if espec.direcao_vento:
            mapa_interpolado(dados, espec, base, pasta / f"{nome}.png")
        else:
            mapa_pontual(dados, espec, base, pasta / f"{nome}.png")
            mapa_interpolado(dados, espec, base, pasta / f"{nome}_interpolado.png")


def mapa_pontual(gdf: gpd.GeoDataFrame, espec: EspecMapa, base: BaseCartografica, caminho: Path) -> None:
    """Estações coloridas e rotuladas com o valor; tamanho do marcador proporcional ao valor."""
    fig, ax = _nova_figura((12, 12))
    _desenhar_limites(ax, base)

    valores = gdf[espec.coluna]
    amplitude = valores.max() - valores.min()
    tamanho = (valores - valores.min()) / amplitude * 150 + 50 if amplitude else 100
    vmin, vmax = _faixa_de_cores(valores)
    gdf.plot(ax=ax, column=espec.coluna, cmap=espec.cmap, vmin=vmin, vmax=vmax, markersize=tamanho,
             edgecolor="black", linewidth=0.8, legend=True, legend_kwds={"label": espec.unidade, "shrink": 0.75})

    _rotular(ax, gdf, valores.map(_formato(espec)), tamanho_fonte=8, cor="black",
             fundo="white", borda="none", opacidade=0.75)
    _ranking(ax, gdf, espec, tamanho_fonte=10)
    _finalizar(fig, ax, espec, base, caminho)


def mapa_interpolado(gdf: gpd.GeoDataFrame, espec: EspecMapa, base: BaseCartografica, caminho: Path) -> None:
    """Superfície IDW recortada ao estado, com as estações e (opcionalmente) a direção do vento."""
    if len(gdf) < config.MIN_ESTACOES_INTERPOLACAO:
        print(f"⚠️ Poucas estações para interpolar '{espec.titulo}' "
              f"({len(gdf)}; mínimo {config.MIN_ESTACOES_INTERPOLACAO})")
        return

    grade = calculos.interpolar_idw(gdf["Longitude"], gdf["Latitude"], gdf[espec.coluna],
                                    base.lon_grade, base.lat_grade, config.IDW_VIZINHOS, config.IDW_POTENCIA)
    valores = gdf[espec.coluna]
    niveis = 20 if valores.max() > valores.min() else np.linspace(*_faixa_de_cores(valores), 11)
    mapa_de_grade(grade, gdf, espec, base, caminho, niveis)


def mapa_de_grade(grade, gdf, espec: EspecMapa, base: BaseCartografica, caminho: Path, niveis=20) -> None:
    """Desenha uma superfície já calculada, recortada ao estado, com as estações por cima.

    Separado do mapa_interpolado porque nem toda superfície vem do IDW de uma única coluna: o
    risco de fogo, por exemplo, combina três variáveis interpoladas em cada hora.
    """
    fig, ax = _nova_figura((14, 12))
    superficie = ax.contourf(base.lon_grade, base.lat_grade, _recortar(grade, base),
                             levels=niveis, cmap=espec.cmap, alpha=0.8)
    superficie.set_clip_path(base.contorno_uf, transform=ax.transData)
    fig.colorbar(superficie, ax=ax, label=espec.unidade, shrink=0.75)
    _desenhar_limites(ax, base)
    gdf.plot(ax=ax, color="black", markersize=50, alpha=0.7, edgecolor="white", linewidth=1.5)

    rotulos = gdf[espec.coluna].map(_formato(espec))
    sufixos_ranking = None
    if espec.direcao_vento:
        _desenhar_setas_vento(ax, gdf)
        direcao = gdf["Direção (°)"]
        rotulos = rotulos + direcao.map(lambda d: "" if pd.isna(d) else f"\n{d:.0f}°")
        sufixos_ranking = direcao.map(lambda d: "" if pd.isna(d) else f" ({d:.0f}°)")

    _rotular(ax, gdf, rotulos, tamanho_fonte=9, cor="white", fundo="black", borda="white", opacidade=0.8)
    _ranking(ax, gdf, espec, tamanho_fonte=9, sufixos=sufixos_ranking)
    _finalizar(fig, ax, espec, base, caminho)


def mapa_classes_pontual(gdf, coluna: str, espec: EspecClasses, base: BaseCartografica, caminho: Path) -> None:
    """Estações coloridas pela classe, com legenda nomeada no lugar da barra de cores."""
    fig, ax = _nova_figura((12, 12))
    _desenhar_limites(ax, base)

    classes = _classes(gdf[coluna], espec)
    # Marcador grande e rótulo sem caixa: aqui a cor é a informação, e uma caixa de fundo a cobriria
    ax.scatter(gdf.geometry.x, gdf.geometry.y, c=[espec.cores[classe] for classe in classes],
               s=520, edgecolor="black", linewidth=1.0, zorder=5)
    _rotular(ax, gdf, classes.map(str), tamanho_fonte=11, cor="black",
             fundo="none", borda="none", opacidade=1.0)
    _legenda_classes(ax, espec, classes)
    _finalizar(fig, ax, espec, base, caminho)


def mapa_classes_interpolado(grade, gdf, coluna: str, espec: EspecClasses, base: BaseCartografica,
                             caminho: Path, indicadores: Indicadores | None = None) -> None:
    """Superfície já classificada (0 a n-1) recortada ao estado, com as estações por cima.

    Com `indicadores`, desenha também os pontos abaixo de cada estação e a legenda deles.
    """
    fig, ax = _nova_figura((14, 12))
    # Uma faixa por classe: as fronteiras ficam no meio do caminho entre dois níveis inteiros
    limites = np.arange(len(espec.cores) + 1) - 0.5
    superficie = ax.contourf(base.lon_grade, base.lat_grade, _recortar(grade, base),
                             levels=limites, colors=espec.cores, alpha=0.85)
    superficie.set_clip_path(base.contorno_uf, transform=ax.transData)
    _desenhar_limites(ax, base)
    gdf.plot(ax=ax, color="black", markersize=50, alpha=0.7, edgecolor="white", linewidth=1.5)

    classes = _classes(gdf[coluna], espec)
    _rotular(ax, gdf, classes.map(str), tamanho_fonte=9, cor="white",
             fundo="black", borda="white", opacidade=0.8)
    _legenda_classes(ax, espec, classes)
    if indicadores is not None:
        _desenhar_indicadores(ax, gdf, indicadores)
    _finalizar(fig, ax, espec, base, caminho)


# =====================================================
# ELEMENTOS COMUNS DOS MAPAS
# =====================================================
def preparar_pontos(tabela: pd.DataFrame | None, coluna: str, descricao: str = "") -> gpd.GeoDataFrame | None:
    """Tabela de estações como GeoDataFrame, sem as linhas a que falta o valor ou as coordenadas."""
    if tabela is None or tabela.empty:
        print(f"⚠️ Sem dados para o mapa: {descricao}")
        return None
    dados = tabela.dropna(subset=[coluna, "Latitude", "Longitude"])
    if dados.empty:
        print(f"⚠️ Sem valores para o mapa: {descricao}")
        return None
    return gpd.GeoDataFrame(dados, geometry=gpd.points_from_xy(dados["Longitude"], dados["Latitude"]),
                            crs="EPSG:4326")


def _preparar_dados(tabela: pd.DataFrame | None, espec: EspecMapa) -> gpd.GeoDataFrame | None:
    return preparar_pontos(tabela, espec.coluna, espec.titulo)


def _recortar(grade, base: BaseCartografica):
    """Esconde as células da grade que ficam fora do estado."""
    return np.ma.masked_where(~base.dentro_uf, grade)


def _classes(valores: pd.Series, espec: EspecClasses) -> pd.Series:
    """Valores convertidos em índice de classe, sem sair da faixa de cores disponível."""
    return valores.astype(int).clip(0, len(espec.cores) - 1)


def _legenda_classes(ax, espec: EspecClasses, classes: pd.Series) -> None:
    """Legenda com uma entrada por classe e quantas estações caíram em cada uma."""
    entradas = [
        Patch(facecolor=cor, edgecolor="black", label=f"{rotulo} — {int((classes == indice).sum())} estações")
        for indice, (cor, rotulo) in enumerate(zip(espec.cores, espec.rotulos))
    ]
    ax.legend(handles=entradas, loc="lower left", fontsize=10, framealpha=0.9)


def _desenhar_indicadores(ax, gdf, indicadores: Indicadores) -> None:
    """Um ponto por indicador verdadeiro, abaixo da estação, e a legenda dos indicadores no canto inferior direito.

    A posição de cada indicador é fixa: mesmo quem não distingue as cores identifica qual ponto é qual.
    """
    quantidade = len(indicadores.colunas)
    for posicao, (coluna, cor) in enumerate(zip(indicadores.colunas, indicadores.cores)):
        marcadas = gdf[gdf[coluna].astype(bool)]
        deslocamento = (posicao - (quantidade - 1) / 2) * PASSO_INDICADORES
        ax.scatter(marcadas.geometry.x + deslocamento, marcadas.geometry.y - DESCIDA_INDICADORES,
                   s=TAMANHO_INDICADORES, color=cor, linewidths=0, zorder=8)

    legenda_existente = ax.get_legend()
    if legenda_existente is not None:
        ax.add_artist(legenda_existente)  # sem isso, a legenda nova substituiria a das classes
    entradas = [Line2D([], [], linestyle="", marker="o", markersize=8, markerfacecolor=cor, markeredgewidth=0,
                       label=rotulo) for rotulo, cor in zip(indicadores.rotulos, indicadores.cores)]
    ax.legend(handles=entradas, loc="lower right", fontsize=10, title=indicadores.titulo, title_fontsize=10,
              framealpha=0.9)


def _formato(espec: EspecMapa):
    """Formatação dos números do mapa (rótulos e ranking), com as casas decimais da especificação."""
    return f"{{:.{espec.decimais}f}}".format


def _faixa_de_cores(valores: pd.Series) -> tuple[float, float]:
    """Mínimo e máximo da escala de cores. Se todos os valores forem iguais (ex.: dia sem chuva), abre a escala em 1."""
    vmin, vmax = float(valores.min()), float(valores.max())
    return (vmin, vmax) if vmax > vmin else (vmin, vmin + 1.0)


def _caminho_matplotlib(geometria) -> mpath.Path:
    """Converte um Polygon ou MultiPolygon em caminho do matplotlib, usado para recortar desenhos."""
    aneis = []
    for poligono in getattr(geometria, "geoms", [geometria]):
        aneis += [poligono.exterior, *poligono.interiors]
    return mpath.Path.make_compound_path(*(mpath.Path(np.asarray(anel.coords)[:, :2], closed=True) for anel in aneis))


def _nova_figura(tamanho: tuple[float, float]):
    fig, ax = plt.subplots(figsize=tamanho)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    return fig, ax


def _desenhar_limites(ax, base: BaseCartografica) -> None:
    base.municipios.boundary.plot(ax=ax, linewidth=0.35, color="gray")
    base.uf.boundary.plot(ax=ax, linewidth=1.8, color="black")


def _rotular(ax, gdf, textos, tamanho_fonte, cor, fundo, borda, opacidade) -> None:
    for (_, linha), texto in zip(gdf.iterrows(), textos):
        ax.text(linha.geometry.x, linha.geometry.y, texto, ha="center", va="center",
                fontsize=tamanho_fonte, fontweight="bold", color=cor, zorder=7,
                bbox=dict(boxstyle="round,pad=0.15", facecolor=fundo, edgecolor=borda, alpha=opacidade))


def _ranking(ax, gdf, espec: EspecMapa, tamanho_fonte, sufixos=None) -> None:
    """Caixa com as 5 estações de maiores (ou menores) valores."""
    extremos = gdf.nlargest(5, espec.coluna) if espec.maiores else gdf.nsmallest(5, espec.coluna)
    texto = f"{espec.ranking}:\n"
    for indice, linha in extremos.iterrows():
        sufixo = sufixos[indice] if sufixos is not None else ""
        texto += f"{linha['Estação']}: {_formato(espec)(linha[espec.coluna])}{sufixo}\n"
    ax.text(0.97, 0.04, texto, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=tamanho_fonte, weight="bold",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="white", edgecolor="black", alpha=0.9))


def _desenhar_setas_vento(ax, gdf) -> None:
    """Setas apontando para onde o vento sopra; comprimento proporcional à rajada."""
    for _, linha in gdf[gdf["Direção (°)"].notna()].iterrows():
        angulo = np.radians(linha["Direção (°)"])
        comprimento = 0.4 * min(linha["Rajada (km/h)"] / 50.0, 1.5)
        ax.arrow(linha.geometry.x, linha.geometry.y, -np.sin(angulo) * comprimento, -np.cos(angulo) * comprimento,
                 head_width=0.08, head_length=0.12, fc="red", ec="red", linewidth=2, alpha=0.8, zorder=6)
    legenda = Line2D([0], [0], color="red", linewidth=2, marker=">", markersize=10, alpha=0.8,
                     label="Direção do vento (comprimento ∝ velocidade)")
    ax.legend(handles=[legenda], loc="lower left", fontsize=10)


def _finalizar(fig, ax, espec: EspecMapa, base: BaseCartografica, caminho: Path) -> None:
    """Título, limites, logos e gravação do PNG."""
    ax.set_title(f"{espec.titulo}\n{espec.subtitulo} — INMET/SEMADESC", fontsize=16, weight="bold", pad=20)
    ax.set_xlim(config.LON_MIN, config.LON_MAX)
    ax.set_ylim(config.LAT_MIN, config.LAT_MAX)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    # Logos posicionados pela moldura do mapa: mesmo lugar e proporção em qualquer tamanho de figura
    for imagem, retangulo in base.logos:
        eixo_logo = ax.inset_axes(retangulo, zorder=10)
        eixo_logo.imshow(imagem)
        eixo_logo.set_anchor("NE")
        eixo_logo.axis("off")

    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(caminho, dpi=config.DPI, bbox_inches="tight", facecolor="white", edgecolor="none")
    plt.close(fig)
    print(f"🗺️ Mapa salvo: {caminho.name}")
