"""Mapas pontuais e interpolados (IDW) das variáveis meteorológicas."""
from dataclasses import dataclass, replace
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")  # gera os arquivos sem abrir janelas (permite rodar agendado)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from matplotlib.lines import Line2D

from . import calculos, config
from .config import Periodo


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
    maiores: bool = True             # ranking dos maiores (True) ou dos menores (False)
    somente_positivos: bool = False  # descarta valores <= 0 (usado na chuva)
    direcao_vento: bool = False      # desenha as setas de direção do vento


@dataclass
class BaseCartografica:
    """Camadas, grade e logos carregados uma única vez e reaproveitados em todos os mapas."""

    uf: gpd.GeoDataFrame
    municipios: gpd.GeoDataFrame
    lon_grade: np.ndarray
    lat_grade: np.ndarray
    dentro_uf: np.ndarray  # máscara: pontos da grade dentro do estado
    logos: list


def carregar_base() -> BaseCartografica:
    uf = gpd.read_file(config.SHAPE_UF).to_crs("EPSG:4326")
    municipios = gpd.read_file(config.SHAPE_MUN).to_crs("EPSG:4326")
    lon_grade, lat_grade = calculos.criar_grade(
        (config.LON_MIN, config.LON_MAX, config.LAT_MIN, config.LAT_MAX), config.RESOLUCAO_GRADE
    )
    dentro_uf = shapely.contains_xy(uf.geometry.union_all(), lon_grade, lat_grade)

    logos = []
    for arquivo, retangulo in config.LOGOS:
        if arquivo.exists():
            logos.append((plt.imread(arquivo), retangulo))
        else:
            print(f"⚠️ Logo não encontrado: {arquivo}")
    return BaseCartografica(uf, municipios, lon_grade, lat_grade, dentro_uf, logos)


def especificacoes(periodo: Periodo) -> list[EspecMapa]:
    """Mapas gerados para o período."""
    uf, sigla = config.NOME_UF, config.UF
    subtitulo = periodo.descrever_janela(*periodo.janela_extremos)

    mapas = [
        EspecMapa("Temp_Min", "Temperatura Mínima (°C)", f"Temperatura mínima em {uf}",
                  periodo.descrever_janela(*periodo.janela_temp_min), f"Mapa_Temp_Min_{sigla}",
                  "coolwarm", "Temperatura (°C)", "5 MENORES TEMPERATURAS", maiores=False),
        EspecMapa("Temp_Max", "Temperatura Máxima (°C)", f"Temperatura máxima em {uf}", subtitulo,
                  f"Mapa_Temp_Max_{sigla}", "YlOrRd", "Temperatura (°C)", "5 MAIORES TEMPERATURAS"),
        EspecMapa("Umidade", "Umidade Mín (%)", f"Umidade relativa mínima em {uf}", subtitulo,
                  f"Mapa_Umidade_{sigla}", "YlGnBu", "Umidade (%)", "5 MENORES UMIDADES", maiores=False),
    ]

    # (coluna, duração no título, sufixo do arquivo, paleta)
    if periodo.modo == "tempo_real":
        chuvas = [("Acumulado 24h", "24 horas", "24h", "Blues"), ("Acumulado 48h", "48 horas", "48h", "Purples")]
    elif periodo.modo == "dia":
        chuvas = [(periodo.coluna_chuva_principal, "24 horas", "24h", "Blues")]
    else:
        chuvas = [(periodo.coluna_chuva_principal, f"{periodo.num_dias} dias", "Periodo", "Blues")]
    for coluna, duracao, sufixo, cmap in chuvas:
        mapas.append(EspecMapa(
            "Chuva", coluna, f"Chuva acumulada em {duracao} - {uf}",
            periodo.descrever_janela(*periodo.janelas_chuva[coluna]), f"Mapa_Chuva_{sufixo}_{sigla}",
            cmap, "Chuva (mm)", "5 MAIORES ACUMULADOS", somente_positivos=True,
        ))

    rajadas = EspecMapa("Vento", "Rajada (km/h)", f"Rajadas de vento em {uf}", subtitulo,
                        f"Mapa_Rajadas_{sigla}", "turbo", "Velocidade (km/h)", "5 MAIORES RAJADAS")
    mapas.append(rajadas)
    mapas.append(replace(rajadas, titulo=f"Rajadas de vento com direção em {uf}",
                         arquivo=f"Mapa_Rajadas_Direcao_{sigla}", direcao_vento=True))
    return mapas


def gerar_mapas(tabelas: dict[str, pd.DataFrame], periodo: Periodo, pasta: Path) -> None:
    """Gera todos os mapas do período na pasta indicada."""
    if not tabelas:
        print("⚠️ Nenhum dado foi coletado. Os mapas não serão gerados.")
        return
    try:
        base = carregar_base()
    except Exception as erro:
        print(f"⚠️ Não foi possível carregar os shapefiles: {erro}")
        return

    pasta.mkdir(parents=True, exist_ok=True)
    for espec in especificacoes(periodo):
        dados = _preparar_dados(tabelas.get(espec.tabela), espec)
        if dados is None:
            continue
        nome = f"{espec.arquivo}_{periodo.identificador}"
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
    gdf.plot(ax=ax, column=espec.coluna, cmap=espec.cmap, markersize=tamanho, edgecolor="black",
             linewidth=0.8, legend=True, legend_kwds={"label": espec.unidade, "shrink": 0.75})

    _rotular(ax, gdf, valores.map("{:.1f}".format), tamanho_fonte=8, cor="black",
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
    grade = np.ma.masked_where(~base.dentro_uf, grade)

    fig, ax = _nova_figura((14, 12))
    superficie = ax.contourf(base.lon_grade, base.lat_grade, grade, levels=20, cmap=espec.cmap, alpha=0.8)
    fig.colorbar(superficie, ax=ax, label=espec.unidade, shrink=0.75)
    _desenhar_limites(ax, base)
    gdf.plot(ax=ax, color="black", markersize=50, alpha=0.7, edgecolor="white", linewidth=1.5)

    rotulos = gdf[espec.coluna].map("{:.1f}".format)
    sufixos_ranking = None
    if espec.direcao_vento:
        _desenhar_setas_vento(ax, gdf)
        direcao = gdf["Direção (°)"]
        rotulos = rotulos + direcao.map(lambda d: "" if pd.isna(d) else f"\n{d:.0f}°")
        sufixos_ranking = direcao.map(lambda d: "" if pd.isna(d) else f" ({d:.0f}°)")

    _rotular(ax, gdf, rotulos, tamanho_fonte=9, cor="white", fundo="black", borda="white", opacidade=0.8)
    _ranking(ax, gdf, espec, tamanho_fonte=9, sufixos=sufixos_ranking)
    _finalizar(fig, ax, espec, base, caminho)


# =====================================================
# ELEMENTOS COMUNS DOS MAPAS
# =====================================================
def _preparar_dados(tabela: pd.DataFrame | None, espec: EspecMapa) -> gpd.GeoDataFrame | None:
    if tabela is None or tabela.empty:
        print(f"⚠️ Sem dados para o mapa: {espec.titulo}")
        return None
    dados = tabela.dropna(subset=[espec.coluna, "Latitude", "Longitude"])
    if espec.somente_positivos:
        dados = dados[dados[espec.coluna] > 0]
    if dados.empty:
        print(f"⚠️ Sem valores para o mapa: {espec.titulo}")
        return None
    return gpd.GeoDataFrame(dados, geometry=gpd.points_from_xy(dados["Longitude"], dados["Latitude"]),
                            crs="EPSG:4326")


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
        texto += f"{linha['Estação']}: {linha[espec.coluna]:.1f}{sufixo}\n"
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
    ax.set_title(f"{espec.titulo}\n{espec.subtitulo} — dados INMET", fontsize=16, weight="bold", pad=20)
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
