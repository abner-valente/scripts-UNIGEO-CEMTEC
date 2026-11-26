"""O mapa de camadas: os produtos do painel empilhados como num SIG, e as peças de pydeck que os mapas
navegáveis dividem.

Cada item do catálogo é uma ou mais camadas do deck.gl com um nome só no quadro: ligar, desligar, a
opacidade e a ordem (1 fica por cima de todas, como no painel de camadas do QGIS) valem para o item
inteiro. As peças (os limites, as estações, a imagem da superfície, os quadradinhos dos raios) são as
mesmas dos Mapas, da Chuva e dos Raios: uma camada desenhada num lugar sai igual no outro.

Sem tela: o que é Streamlit fica no explorador.
"""
import html
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pydeck as pdk

from app import interativo, superficie
from modulos import config

# Os rótulos dos mapas navegáveis (o número sobre cada estação, o nome no mapa das estações): caixa
# branca de cantos arredondados, borda fina e o texto em seminegrito, na fonte do painel. A equipe o
# escolheu entre seis estilos testados lado a lado (03/11/2026)
ESTILO_DOS_ROTULOS = dict(get_size=12, get_color=[20, 20, 20], background=True,
                          get_background_color=[255, 255, 255, 235], get_border_color=[60, 60, 60, 200],
                          get_border_width=1, background_padding=[4, 1, 4, 1], background_border_radius=4,
                          font_weight=600)
FONTE_DO_PAINEL = "'Source Sans Pro', Arial, sans-serif"
# O que no rótulo é texto puro, e não coluna do dado: passado no construtor, o pydeck o trataria como
# expressão ("@@=meters"), e o deck.gl o leria como função. Foi assim que os números em metros da chuva
# saíram com os 12 px de sempre de 24 a 26/11/2026, embolados atrás do maior do grupo
TEXTOS_PUROS_DO_ROTULO = ("size_units", "get_text_anchor", "get_alignment_baseline")
COR_DOS_MUNICIPIOS = [70, 70, 70, 150]
# O contorno do estado vai em duas linhas, uma clara e larga por baixo de uma escura: no mapa de fundo
# escuro, a linha preta sozinha sumiria
BORDA_DO_ESTADO = [235, 235, 235, 220]
COR_DO_ESTADO = [25, 25, 25]
# Os quadradinhos dos raios: células de 0,1°, desenhadas a partir do centro
MEIA_CELULA_DOS_RAIOS = 0.5 / config.CELULAS_POR_GRAU_RAIOS
COLUNAS_DOS_QUADRADINHOS = ["Longitude", "Latitude", "cor", "titulo", "detalhe"]
COLUNAS_DOS_FLASHES = ["longitude", "latitude", "cor", "titulo", "detalhe"]


def _opacidade(opacidade: float | None) -> dict:
    # Sem opacidade, a camada sai como sempre saiu, sem a chave: a de 1 é o padrão do deck.gl
    return {} if opacidade is None else {"opacity": opacidade}


def quadrado(dx: float, dy: float | None = None) -> str:
    # O quadrado de cada célula sai do centro no navegador, para não mandar quatro cantos por célula.
    # `dx` e `dy` são meia célula, em graus de longitude e de latitude
    dy = dx if dy is None else dy
    return (f"[[Longitude - {dx:.6f}, Latitude - {dy:.6f}], [Longitude + {dx:.6f}, Latitude - {dy:.6f}], "
            f"[Longitude + {dx:.6f}, Latitude + {dy:.6f}], [Longitude - {dx:.6f}, Latitude + {dy:.6f}]]")


def imagem(uri: str | None, limites: list[float], id: str | None = None,
           opacidade: float | None = None) -> pdk.Layer:
    # A imagem entra depois de criada a camada: passada no construtor, o pydeck a trataria como
    # expressão a ser avaliada no navegador ("@@=data:image/png;...")
    camada = pdk.Layer("BitmapLayer", data=None, id=id, bounds=limites, **_opacidade(opacidade))
    camada.image = uri
    return camada


def municipios(contornos: dict, clicaveis: bool = False, opacidade: float | None = None) -> pdk.Layer:
    # Clicáveis, eles levam um preenchimento quase transparente: é ele que responde ao mouse com o nome
    preenchimento = ({"filled": True, "get_fill_color": [255, 255, 255, 1], "pickable": True} if clicaveis
                     else {"filled": False})
    return pdk.Layer("GeoJsonLayer", data=contornos, id="municipios", stroked=True, get_line_color=COR_DOS_MUNICIPIOS,
                     line_width_min_pixels=0.6, **preenchimento, **_opacidade(opacidade))


def contorno_do_estado(estado: dict, opacidade: float | None = None) -> list[pdk.Layer]:
    return [pdk.Layer("GeoJsonLayer", data=estado, id="estado_borda", stroked=True, filled=False,
                      get_line_color=BORDA_DO_ESTADO, line_width_min_pixels=3.5, **_opacidade(opacidade)),
            pdk.Layer("GeoJsonLayer", data=estado, id="estado", stroked=True, filled=False,
                      get_line_color=COR_DO_ESTADO, line_width_min_pixels=1.6, **_opacidade(opacidade))]


def limites(estado: dict, contornos_dos_municipios: dict, clicaveis: bool = False,
            opacidade: float | None = None) -> list[pdk.Layer]:
    return [municipios(contornos_dos_municipios, clicaveis, opacidade), *contorno_do_estado(estado, opacidade)]


def rotulos(dados: pd.DataFrame, coluna: str, posicao: list[str], deslocamento: list[int], **extras) -> pdk.Layer:
    puros = {chave: extras.pop(chave) for chave in TEXTOS_PUROS_DO_ROTULO if chave in extras}
    camada = pdk.Layer("TextLayer", data=dados, get_position=posicao, get_text=coluna, get_pixel_offset=deslocamento,
                       **{**ESTILO_DOS_ROTULOS, **extras})
    # Como a imagem: passados no construtor, a fonte e os textos puros virariam expressões
    camada.font_family = FONTE_DO_PAINEL
    for chave, valor in puros.items():
        setattr(camada, chave, valor)
    return camada


def estacoes(pontos: pd.DataFrame, posicao: list[str] | None = None, id: str = "estacoes",
             opacidade: float | None = None, **extras) -> pdk.Layer:
    # As estações do INMET (interativo.pontos_de_valores e pontos_de_risco): a cor vem com cada ponto
    return pdk.Layer("ScatterplotLayer", data=pontos, id=id, get_position=posicao or ["Longitude", "Latitude"],
                     get_fill_color="cor", get_line_color=[25, 25, 25], stroked=True, line_width_min_pixels=1,
                     get_radius=3000, radius_min_pixels=6, radius_max_pixels=14, pickable=True,
                     **_opacidade(opacidade), **extras)


def pluviometros(pontos: pd.DataFrame, posicao: list[str] | None = None, opacidade: float | None = None,
                 **extras) -> pdk.Layer:
    # Os do CEMADEN (interativo.pontos_do_cemaden): menores que as estações do INMET e com a borda
    # laranja, porque Campo Grande tem quatro, a poucos km
    return pdk.Layer("ScatterplotLayer", data=pontos, id="cemaden", get_position=posicao or ["Longitude", "Latitude"],
                     get_fill_color="cor", get_line_color=interativo.COR_DO_CEMADEN, stroked=True,
                     line_width_min_pixels=2, get_radius=2000, radius_min_pixels=5, radius_max_pixels=10,
                     pickable=True, **_opacidade(opacidade), **extras)


def quadradinhos(celulas: pd.DataFrame, id: str, opacidade: float | None = None) -> pdk.Layer:
    # As células de 0,1° dos raios (a densidade, e os flashes recentes quando são muitos), cada uma na
    # cor que vem com ela
    return pdk.Layer("PolygonLayer", data=celulas[COLUNAS_DOS_QUADRADINHOS], id=id,
                     get_polygon=quadrado(MEIA_CELULA_DOS_RAIOS), filled=True, stroked=False, get_fill_color="cor",
                     pickable=True, **_opacidade(opacidade))


def flashes(pontos: pd.DataFrame, opacidade: float | None = None) -> pdk.Layer:
    # Os flashes um a um (raios.pontos_por_idade), na cor do tempo desde cada um
    return pdk.Layer("ScatterplotLayer", data=pontos[COLUNAS_DOS_FLASHES], id="flashes",
                     get_position=["longitude", "latitude"], get_fill_color="cor", get_line_color=[40, 40, 40, 200],
                     stroked=True, line_width_min_pixels=0.5, get_radius=1500, radius_min_pixels=2.5,
                     radius_max_pixels=8, pickable=True, **_opacidade(opacidade))


def deck(pilha: list[pdk.Layer], visao: pdk.ViewState, com_fundo: bool, balao: dict = interativo.BALAO) -> pdk.Deck:
    # Com o fundo, o da Carto, claro ou escuro como o tema (é o que o Streamlit põe com map_style=None):
    # países, cidades, estradas e rios, da internet. Sem ele, nada vem da rede
    if com_fundo:
        return pdk.Deck(layers=pilha, map_style=None, initial_view_state=visao, tooltip=balao)
    return pdk.Deck(layers=pilha, map_provider=None, map_style=interativo.ESTILO_SEM_FUNDO, initial_view_state=visao,
                    tooltip=balao)


# =====================================================
# LEGENDAS
# =====================================================
def legenda(itens: list[tuple[str, str]], titulo: str, sobre_o_mapa: bool = False) -> str:
    # Uma linha por cor, na ordem dos itens. Por cima do mapa navegável (escala_no_mapa, no
    # explorador), em letras claras, no fundo escuro dele; ao lado do mapa, na cor do tema, e o
    # quadrinho com uma borda fina, para o branco aparecer
    if sobre_o_mapa:
        caixa, linha, quadrinho = "font-size:0.8rem;color:#e0e0e0", "margin-top:0.25rem", "margin-right:0.35rem"
    else:
        caixa, linha, quadrinho = ("font-size:0.85rem;margin-bottom:0.6rem", "margin-top:0.2rem",
                                   "margin-right:0.4rem;border:1px solid #8885")
    linhas = "".join(f'<span style="display:flex;align-items:center;{linha}">'
                     f'<span style="width:0.9rem;height:0.9rem;border-radius:2px;{quadrinho};'
                     f'background:{cor}"></span>{html.escape(rotulo)}</span>' for cor, rotulo in itens)
    return f'<div style="{caixa}"><b>{html.escape(titulo)}</b>{linhas}</div>'


def _br(valor: float) -> str:
    return f"{valor:g}".replace(".", ",")


def itens_da_escala(niveis, paleta: str, unidade: str) -> list[tuple[str, str]]:
    # (cor, rótulo) de cada classe de uma superfície de escala fixa em classes (não numa faixa), do
    # menor para o maior, nas cores com que a superfície as pinta. Abaixo da primeira classe, quando
    # ela não começa no zero, o branco do seco (superficie.BRANCO_DO_SECO)
    niveis = [float(nivel) for nivel in niveis]
    meios = [(de + ate) / 2 for de, ate in zip(niveis[:-1], niveis[1:])] + [niveis[-1] + 1]
    pintura, _ = superficie.cores(np.asarray(meios), paleta, niveis, opacidade=1.0)
    rotulos_ = ([f"{_br(de)} a {_br(ate)} {unidade}" for de, ate in zip(niveis[:-1], niveis[1:])]
                + [f"acima de {_br(niveis[-1])} {unidade}"])
    itens = [(f"rgb({r},{g},{b})", rotulo) for (r, g, b, _), rotulo in zip(pintura.tolist(), rotulos_)]
    if niveis[0] > 0:
        itens.insert(0, ("rgb({},{},{})".format(*superficie.BRANCO_DO_SECO), f"abaixo de {_br(niveis[0])} {unidade}"))
    return itens


# =====================================================
# O CATÁLOGO E O QUADRO DE CAMADAS
# =====================================================
@dataclass(frozen=True)
class Camada:
    chave: str
    nome: str            # o que o quadro mostra
    opacidade: int = 100   # %, a de quando a página abre
    ligada: bool = True


# Na ordem em que o quadro abre, de cima para baixo: o que é ponto por cima, o que é superfície por
# baixo, como se empilha num SIG. A densidade de raios a 80% deixa ver a chuva por baixo
CATALOGO = (
    Camada("raios_recentes", "Raios da última hora"),
    Camada("estacoes_chuva", "Estações (chuva)"),
    Camada("limites", "Limites (estado e municípios)"),
    Camada("densidade_raios", "Densidade de raios", opacidade=80),
    Camada("chuva", "Chuva acumulada"),
)
COLUNAS_DO_QUADRO = ["Camada", "Ver", "Opacidade", "Ordem"]


def quadro_inicial(catalogo: tuple[Camada, ...] = CATALOGO) -> pd.DataFrame:
    return pd.DataFrame([{"Camada": camada.nome, "Ver": camada.ligada, "Opacidade": camada.opacidade,
                          "Ordem": posicao} for posicao, camada in enumerate(catalogo, start=1)],
                        columns=COLUNAS_DO_QUADRO)


def _marcado(valor) -> bool:
    return bool(valor) if pd.notna(valor) else False


def empilhadas(quadro: pd.DataFrame, catalogo: tuple[Camada, ...] = CATALOGO) -> list[tuple[Camada, float]]:
    # As camadas marcadas no quadro, de baixo para cima (a ordem em que o deck.gl desenha), com a
    # opacidade de 0 a 1. Uma célula esvaziada na tabela não quebra nada: sem opacidade, a do
    # catálogo; sem ordem, a camada vai para baixo de todas. No empate da ordem, fica por cima a que
    # vem antes no quadro
    por_nome = {camada.nome: camada for camada in catalogo}
    marcadas = []
    for posicao, linha in enumerate(quadro.itertuples(index=False)):
        camada = por_nome.get(linha.Camada)
        if camada is None or not _marcado(linha.Ver):
            continue
        opacidade = camada.opacidade if pd.isna(linha.Opacidade) else float(linha.Opacidade)
        ordem = float("inf") if pd.isna(linha.Ordem) else float(linha.Ordem)
        marcadas.append((ordem, posicao, camada, min(max(opacidade, 0.0), 100.0) / 100))
    marcadas.sort(key=lambda item: (item[0], item[1]))
    return [(camada, opacidade) for _, _, camada, opacidade in reversed(marcadas)]


def montar(pilha: list[tuple[Camada, float]],
           construtores: dict[str, Callable[[float], list[pdk.Layer]]]) -> list[pdk.Layer]:
    # As camadas do deck.gl, de baixo para cima. Cada construtor recebe a opacidade e só é chamado se
    # a camada dele está ligada: o dado de uma camada desligada nem é carregado
    return [peca for camada, opacidade in pilha if camada.chave in construtores
            for peca in construtores[camada.chave](opacidade)]
