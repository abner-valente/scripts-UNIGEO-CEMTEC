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
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patheffects import withStroke
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


@dataclass(frozen=True)
class Tela:
    """Ajustes de quem vai ver o mapa pequeno, do tamanho de uma coluna, e ampliar se quiser.

    Nesse tamanho o mapa mostra o padrão, não os números: título, logos e ranking viram borrão,
    e os valores de 62 estações se cobrem. O que sobra vai com o texto maior, proporcional à
    figura, para continuar legível depois de encolhido. Sem `Tela`, sai o mapa do relatório.
    """

    rotulos: bool = False         # o valor escrito em cada estação
    barra_de_cores: bool = False
    escala_texto: float = 2.2     # sobre o corpo padrão (10 pt), para a barra de cores
    escala_rotulos: float = 1.5   # sobre o corpo do valor de cada estação


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
    recorte: config.Recorte  # de que estado é tudo isto: viaja junto para ninguém precisar supor


def carregar_base(recorte: config.Recorte = config.RECORTE) -> BaseCartografica:
    """Camadas, grade e logos de um recorte. Sem recorte, o padrão — que é MS."""
    oeste, leste, sul, norte = recorte.limites
    uf = gpd.read_file(recorte.shape_uf).to_crs("EPSG:4326")
    municipios = gpd.read_file(recorte.shape_mun).to_crs("EPSG:4326")
    lon_grade, lat_grade = calculos.criar_grade((oeste, leste, sul, norte), config.RESOLUCAO_GRADE)
    # A superfície é calculada um pouco além da divisa (2 células da grade) e depois recortada
    # exatamente pelo contorno do estado: a cor chega até a divisa, sem falhas em degrau.
    estado = uf.geometry.union_all()
    passo = max(leste - oeste, norte - sul) / (config.RESOLUCAO_GRADE - 1)
    dentro_uf = shapely.contains_xy(estado.buffer(2 * passo), lon_grade, lat_grade)

    logos = []
    for arquivo, retangulo in config.LOGOS:
        if arquivo.exists():
            logos.append((plt.imread(arquivo), retangulo))
        else:
            print(f"⚠️ Logo não encontrado: {arquivo}")
    return BaseCartografica(uf, municipios, lon_grade, lat_grade, dentro_uf,
                            _caminho_matplotlib(estado), logos, recorte)


def gerar_mapas(tabelas: dict[str, pd.DataFrame], especificacoes: list[EspecMapa], pasta: Path,
                identificador: str, recorte: config.Recorte = config.RECORTE) -> None:
    """Gera, na pasta indicada, os mapas descritos pelas especificações (identificador vai no nome dos arquivos)."""
    if not tabelas:
        print("⚠️ Nenhum dado foi coletado. Os mapas não serão gerados.")
        return
    try:
        base = carregar_base(recorte)
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


def mapa_pontual(gdf: gpd.GeoDataFrame, espec: EspecMapa, base: BaseCartografica,
                 caminho: Path | None = None, tela: Tela | None = None) -> Figure:
    """Estações coloridas e rotuladas com o valor; tamanho do marcador proporcional ao valor."""
    fig, ax = _nova_figura((12, 12))
    _desenhar_limites(ax, base)

    valores = gdf[espec.coluna]
    amplitude = valores.max() - valores.min()
    tamanho = (valores - valores.min()) / amplitude * 150 + 50 if amplitude else 100
    vmin, vmax = _faixa_de_cores(valores)
    com_barra = tela is None or tela.barra_de_cores
    gdf.plot(ax=ax, column=espec.coluna, cmap=espec.cmap, vmin=vmin, vmax=vmax, markersize=tamanho,
             edgecolor="black", linewidth=0.8, legend=com_barra,
             legend_kwds={"label": espec.unidade, "shrink": 0.75} if com_barra else None)

    if tela is None or tela.rotulos:
        _rotular(ax, gdf, valores.map(_formato(espec)), tamanho_fonte=_corpo_rotulo(8, tela), cor="black",
                 fundo="white", borda="none", opacidade=0.75, halo=tela is not None)
    if tela is None:
        _ranking(ax, gdf, espec, tamanho_fonte=10)
    return _finalizar(fig, ax, espec, base, caminho, tela)


def mapa_interpolado(gdf: gpd.GeoDataFrame, espec: EspecMapa, base: BaseCartografica,
                     caminho: Path | None = None, tela: Tela | None = None,
                     niveis=None, apoio=None) -> Figure | None:
    """Superfície IDW recortada ao estado, com as estações e (opcionalmente) a direção do vento.

    `apoio` são pontos que **só alimentam a interpolação** — estações de fora do recorte, que
    seguram a superfície na borda. Elas não viram ponto desenhado, rótulo nem ranking, e não
    mexem na escala de cores: o produto é do estado, e o que está fora dele é insumo da conta.
    Sem elas, os 8 vizinhos que o IDW enxerga numa célula da divisa estão todos para dentro, e a
    superfície extrapola tendo dado do outro lado.

    Devolve None quando há estações de menos para interpolar: com poucos pontos a superfície
    inventa mais do que mostra.
    """
    if len(gdf) < config.MIN_ESTACOES_INTERPOLACAO:
        print(f"⚠️ Poucas estações para interpolar '{espec.titulo}' "
              f"({len(gdf)}; mínimo {config.MIN_ESTACOES_INTERPOLACAO})")
        return None

    entrada = gdf if apoio is None or len(apoio) == 0 else pd.concat([gdf, apoio], ignore_index=True)
    grade = calculos.interpolar_idw(entrada["Longitude"], entrada["Latitude"], entrada[espec.coluna],
                                    base.lon_grade, base.lat_grade, config.IDW_VIZINHOS, config.IDW_POTENCIA)
    valores = gdf[espec.coluna]
    if niveis is None:
        # Sem níveis dados, a escala se estica ao dado — como os relatórios sempre fizeram
        niveis = 20 if valores.max() > valores.min() else np.linspace(*_faixa_de_cores(valores), 11)
    return mapa_de_grade(grade, gdf, espec, base, caminho, niveis, tela)


def mapa_de_grade(grade, gdf, espec: EspecMapa, base: BaseCartografica, caminho: Path | None = None,
                  niveis=20, tela: Tela | None = None) -> Figure:
    """Desenha uma superfície já calculada, recortada ao estado, com as estações por cima.

    Separado do mapa_interpolado porque nem toda superfície vem do IDW de uma única coluna: o
    risco de fogo, por exemplo, combina três variáveis interpoladas em cada hora.
    """
    fig, ax = _nova_figura((14, 12))
    # `extend` pinta o que passa das pontas com a cor do extremo. Sem isso, numa escala fixa, um
    # valor acima do teto sairia branco no mapa — como se não houvesse medição ali.
    extremos = "max" if _so_acima(niveis) else "both"
    superficie = ax.contourf(base.lon_grade, base.lat_grade, _recortar(grade, base),
                             levels=niveis, cmap=espec.cmap, alpha=0.8,
                             extend=extremos if np.ndim(niveis) else "neither")
    superficie.set_clip_path(base.contorno_uf, transform=ax.transData)
    if tela is None or tela.barra_de_cores:
        barra = fig.colorbar(superficie, ax=ax, label=espec.unidade, shrink=0.75)
        barra.set_label(espec.unidade, size=_corpo(tela))
        barra.ax.tick_params(labelsize=_corpo(tela))
    _desenhar_limites(ax, base)
    gdf.plot(ax=ax, color="black", markersize=50, alpha=0.7, edgecolor="white", linewidth=1.5)

    rotulos = gdf[espec.coluna].map(_formato(espec))
    sufixos_ranking = None
    if espec.direcao_vento:
        _desenhar_setas_vento(ax, gdf, espec.coluna, tela)
        direcao = gdf["Direção (°)"]
        rotulos = rotulos + direcao.map(lambda d: "" if pd.isna(d) else f"\n{d:.0f}°")
        sufixos_ranking = direcao.map(lambda d: "" if pd.isna(d) else f" ({d:.0f}°)")

    if tela is None or tela.rotulos:
        _rotular(ax, gdf, rotulos, tamanho_fonte=_corpo_rotulo(9, tela), cor="white", fundo="black",
                 borda="white", opacidade=0.8, halo=tela is not None)
    if tela is None:
        _ranking(ax, gdf, espec, tamanho_fonte=9, sufixos=sufixos_ranking)
    return _finalizar(fig, ax, espec, base, caminho, tela)


def mapa_classes_pontual(gdf, coluna: str, espec: EspecClasses, base: BaseCartografica,
                         caminho: Path | None = None, tela: Tela | None = None) -> Figure:
    """Estações coloridas pela classe, com legenda nomeada no lugar da barra de cores."""
    fig, ax = _nova_figura((12, 12))
    _desenhar_limites(ax, base)

    classes = _classes(gdf[coluna], espec)
    # Marcador grande e rótulo sem caixa: aqui a cor é a informação, e uma caixa de fundo a cobriria
    ax.scatter(gdf.geometry.x, gdf.geometry.y, c=[espec.cores[classe] for classe in classes],
               s=520, edgecolor="black", linewidth=1.0, zorder=5)
    _rotular(ax, gdf, classes.map(str), tamanho_fonte=_corpo_rotulo(11, tela), cor="black",
             fundo="none", borda="none", opacidade=1.0)
    _legenda_classes(ax, espec, classes)
    return _finalizar(fig, ax, espec, base, caminho, tela)


def mapa_classes_interpolado(grade, gdf, coluna: str, espec: EspecClasses, base: BaseCartografica,
                             caminho: Path | None = None, indicadores: Indicadores | None = None,
                             tela: Tela | None = None) -> Figure:
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
    if tela is None or tela.rotulos:
        # Na tela o nível de cada estação entra junto com os outros valores, pela mesma caixa:
        # 62 dígitos num mapa do tamanho de uma coluna cobrem a superfície que eles explicam.
        _rotular(ax, gdf, classes.map(str), tamanho_fonte=_corpo_rotulo(9, tela), cor="white",
                 fundo="black", borda="white", opacidade=0.8, halo=tela is not None)
    _legenda_classes(ax, espec, classes)
    if indicadores is not None:
        _desenhar_indicadores(ax, gdf, indicadores)
    return _finalizar(fig, ax, espec, base, caminho, tela)


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


def _so_acima(niveis) -> bool:
    """Se a escala só estende para cima — é o caso da chuva, onde abaixo da primeira classe não
    choveu, e pintar isso de azul claro inventaria chuva que não houve."""
    return bool(np.ndim(niveis)) and float(np.min(niveis)) > 0


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
    # Figure direto, e não plt.subplots: o pyplot guarda as figuras num estado global, que num
    # servidor com várias pessoas ao mesmo tempo vaza memória e pode embaralhar dois desenhos.
    # Sem esse registro, a figura é liberada sozinha quando ninguém mais precisa dela.
    fig = Figure(figsize=tamanho)
    ax = fig.add_subplot()
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    return fig, ax


def _desenhar_limites(ax, base: BaseCartografica) -> None:
    base.municipios.boundary.plot(ax=ax, linewidth=0.35, color="gray")
    base.uf.boundary.plot(ax=ax, linewidth=1.8, color="black")


def _rotular(ax, gdf, textos, tamanho_fonte, cor, fundo, borda, opacidade, halo=False) -> None:
    """Escreve o valor sobre cada estação.

    Com `halo`, o texto vai sem caixa, contornado pela cor que seria o fundo. É o que a tela
    pede: num mapa do tamanho de uma coluna, com 62 estações, o que cobre a estação vizinha é a
    **caixa**, não a letra — o contorno segura a leitura e devolve o mapa por baixo. No
    relatório, onde o mapa ocupa a folha inteira, a caixa continua.
    """
    contorno = [withStroke(linewidth=max(2.0, tamanho_fonte / 3), foreground=fundo)] if halo else None
    caixa = (None if halo else
             dict(boxstyle="round,pad=0.15", facecolor=fundo, edgecolor=borda, alpha=opacidade))
    for (_, linha), texto in zip(gdf.iterrows(), textos):
        ax.text(linha.geometry.x, linha.geometry.y, texto, ha="center", va="center",
                fontsize=tamanho_fonte, fontweight="bold", color=cor, zorder=7,
                bbox=caixa, path_effects=contorno)


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


def _desenhar_setas_vento(ax, gdf, coluna: str, tela: "Tela | None" = None) -> None:
    """Setas apontando para onde o vento sopra; comprimento proporcional ao valor do mapa.

    A medida é a coluna que o próprio mapa mostra — a rajada no mapa de rajadas, a velocidade no
    de velocidade —, para a seta não crescer por um número que não está ali.
    """
    for _, linha in gdf[gdf["Direção (°)"].notna()].iterrows():
        angulo = np.radians(linha["Direção (°)"])
        comprimento = 0.4 * min(linha[coluna] / 50.0, 1.5)
        ax.arrow(linha.geometry.x, linha.geometry.y, -np.sin(angulo) * comprimento, -np.cos(angulo) * comprimento,
                 head_width=0.08, head_length=0.12, fc="red", ec="red", linewidth=2, alpha=0.8, zorder=6)
    legenda = Line2D([0], [0], color="red", linewidth=2, marker=">", markersize=10, alpha=0.8,
                     label="Direção do vento (comprimento ∝ velocidade)")
    ax.legend(handles=[legenda], loc="lower left", fontsize=_corpo(tela))


def _corpo(tela: "Tela | None") -> float:
    """Corpo do texto que sobra no mapa: maior na tela, porque a figura vai ser encolhida."""
    return 10 * (tela.escala_texto if tela else 1)


def _corpo_rotulo(tamanho: float, tela: "Tela | None") -> float:
    """Corpo do valor escrito em cada estação, ampliado quando o mapa vai ser visto pequeno."""
    return tamanho * (tela.escala_rotulos if tela else 1)


def _finalizar(fig, ax, espec, base: BaseCartografica, caminho: Path | None,
               tela: "Tela | None" = None) -> Figure:
    """Limites, moldura (título e logos) e, se vier caminho, gravação do PNG.

    Com `tela`, sai só o mapa: título e logos institucionais tomariam o lugar do desenho numa
    miniatura. O relatório, que não passa `tela`, continua saindo com tudo.
    """
    if tela is None:
        ax.set_title(f"{espec.titulo}\n{espec.subtitulo} — INMET/SEMADESC", fontsize=16, weight="bold", pad=20)
    # O enquadramento vem da base, e não do módulo: é a base que sabe de que recorte ela é
    oeste, leste, sul, norte = base.recorte.limites
    ax.set_xlim(oeste, leste)
    ax.set_ylim(sul, norte)
    if tela is None:
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
    else:
        # Sem a grade de latitude e longitude: numa miniatura ela toma a borda do desenho inteira
        # e não se lê. Quem precisa de coordenada está olhando o mapa do relatório.
        ax.set_axis_off()

    # Logos posicionados pela moldura do mapa: mesmo lugar e proporção em qualquer tamanho de figura
    for imagem, retangulo in (base.logos if tela is None else []):
        eixo_logo = ax.inset_axes(retangulo, zorder=10)
        eixo_logo.imshow(imagem)
        eixo_logo.set_anchor("NE")
        eixo_logo.axis("off")

    fig.tight_layout(rect=[0, 0, 1, 0.93] if tela is None else None)
    if caminho is not None:
        fig.savefig(caminho, dpi=config.DPI, bbox_inches="tight", facecolor="white", edgecolor="none")
        print(f"🗺️ Mapa salvo: {caminho.name}")
    return fig
