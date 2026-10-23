"""Explorador de séries temporais das estações automáticas do INMET, por estado.

Ferramenta de análise, separada dos produtos: aqui não se gera planilha nem mapa, se olha o dado
para entender tendências e conferir a qualidade da medição. Os produtos continuam sendo gerados
pelo main.py, e nada aqui altera o que eles produzem.

Para abrir:  streamlit run app/explorador.py
"""
import base64
import io
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# O streamlit coloca a pasta do script no sys.path, e não a raiz do projeto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt
import geopandas as gpd
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from matplotlib import colormaps
from matplotlib.cm import ScalarMappable
from matplotlib.colors import BoundaryNorm, Normalize
from matplotlib.figure import Figure

from app import animacao
from app import boletim
from app import car
from app import chuva as chuva_calc
from app import dados as coleta
from app import interativo
from app import previsao
from app import qualidade
from app import referencia
from app import risco
from app import superficie
from app import variaveis
from modulos import config, fonte, mapas, openmeteo
from modulos.produtos import risco_fogo

# A API manda o vento em m/s; os produtos trabalham em km/h, e aqui seguimos a mesma unidade
CONVERSOES = {"VEN_VEL": 3.6, "VEN_RAJ": 3.6}
# Direção é ângulo: entre 350° e 10° o vento mal mudou, mas uma linha desceria o gráfico inteiro
COLUNAS_CIRCULARES = {"VEN_DIR"}
FAIXAS_VENTO = [(0, 10), (10, 20), (20, 30), (30, float("inf"))]
ROSAS_MAXIMAS = 5  # rosas por linha: mais que isso e cada uma fica pequena demais para se ler
CORES_VENTO = ["#c6dbef", "#6baed6", "#2171b5", "#08306b"]  # sequencial: claro = fraco, escuro = forte
# Nomes curtos para o eixo dos mapas de calor da qualidade, onde os códigos da API não ajudam
NOMES_CURTOS = {
    "TEM_INS": "Temperatura", "TEM_MAX": "Temp. máx.", "TEM_MIN": "Temp. mín.", "PTO_INS": "Orvalho",
    "UMD_INS": "Umidade", "UMD_MAX": "Umid. máx.", "UMD_MIN": "Umid. mín.", "CHUVA": "Chuva",
    "RAD_GLO": "Radiação", "PRE_INS": "Pressão", "VEN_VEL": "Vento", "VEN_RAJ": "Rajada", "VEN_DIR": "Direção",
}
# Modos de agregação dos mapas. Cada um tem os seus produtos no catálogo (app/variaveis.py):
# a regra é da variável, não escolha de quem olha.
MODOS_MAPA = {"Hora a hora": variaveis.HORA, "Por dia": variaveis.DIA, "Período inteiro": variaveis.PERIODO}
# O gráfico não tem o período inteiro: um valor só não faz série no tempo.
MODOS_GRAFICO = {"Hora a hora": variaveis.HORA, "Por dia": variaveis.DIA}
# Acumulados que já vêm escolhidos na aba da chuva: os três do boletim
PADRAO_CHUVA = ["24 h", "48 h", "72 h"]
DPI_MAPA = 150       # serve para a tela e para o PNG baixado: um desenho só, codificado uma vez
# O GIF é feito de dezenas de quadros: em 150 dpi cada um sairia com 1780 px e o arquivo
# passaria de 20 MB. Em 72 dpi o quadro tem 854 px — legível na tela e ~68 KB no GIF.
DPI_GIF = 72
MAPAS_POR_LINHA = 3  # acima disso cada mapa fica estreito demais para se lerem os valores
# Quantos estados ficam com os shapefiles na memória ao mesmo tempo. Cada um prende ~25 MB de
# GeoDataFrame e grade, e esses caches são **do processo, não da sessão**: na nuvem um
# contêiner serve a equipe inteira, então quem passeia pelo seletor enche a memória de todos.
# Sem teto, 25 estados visitados prendiam ~740 MB — e o Streamlit Community Cloud corta perto
# de 1 GB. Com 3, quem compara um estado com os vizinhos não sente; quem volta a um antigo
# espera 1 a 3 s para reler os shapefiles, e as leituras continuam no cache de disco.
ESTADOS_NA_MEMORIA = 3
# Altura dos gráficos de série. Em 320 px, quatro estações com duas séries cada davam oito
# linhas quase coladas: não dava para dizer qual era qual.
ALTURA_GRAFICO = 420
ALTURA_GRAFICO_DO_RISCO = 260   # cada uma das três medidas do risco, uma embaixo da outra
NAVEGAR_NO_GRAFICO = (" Arraste o gráfico para deslocar e use **Shift + roda do mouse** para aproximar; o cursor "
                      "mostra os números do instante mais próximo.")
# O tamanho dos números em todos os gráficos, do observado e da previsão, a pedido da equipe em
# 16/10/2026, em pixels: os dos eixos, e os escritos sobre as barras e as linhas. Um só para todos:
# tamanhos diferentes fariam um gráfico parecer menos importante que o outro.
FONTE_DOS_EIXOS = 15
FONTE_DOS_ROTULOS = 12
# Mapas interativos: o enquadramento inicial sai do recorte — o centro do estado e um zoom que
# cabe o maior lado dele na tela.
ZOOM_POR_GRAU = 5.9 + 3.0  # calibrado em MS, que tem 8° de largura e abre bem no zoom 5,9
ALTURA_MAPA = 900
VISAO_3D = {"pitch": 52, "bearing": -18}   # a inclinação e o giro do protótipo que a equipe escolheu
PONTOS_NO_RELEVO = "pontos_no_relevo"     # a grade que responde ao mouse no 3D (no plano, "pontos")
# O imóvel do CAR: magenta não se confunde com o azul da chuva, com as cores do risco nem com o
# vermelho e o ciano da célula clicada
COR_DO_IMOVEL = [255, 0, 255]
ACIMA_DO_BLOCO = 1500           # metros: no 3D, o topo das células do imóvel fica à vista
LADO_MINIMO_DO_IMOVEL = 0.15    # graus: um imóvel pequeno (ou um ponto) abre com as células em volta
FOLGA_DO_IMOVEL = 2.5
ZOOM_DO_IMOVEL_EM_3D = 7.0
ESCOLHAS_INTERATIVAS = ("escolha_mapa_interativo", "escolha_chuva_interativa", "escolha_risco_interativo")
# Dois ajustes no balão dos gráficos. Ele é um elemento só, pendurado no corpo da página fora
# do gráfico, então as regras valem para todos eles; os seletores repetem o caminho inteiro do
# estilo do Streamlit porque um mais curto perde na especificidade e não pega.
#  1. O rótulo vinha cortado em 150 px: "Campo Grande · Média compens…".
#  2. Linha sem valor sai do balão. É assim que a legenda filtra o que ele mostra: as colunas
#     dele são fixas no spec, então a série escondida vira texto vazio em vez de sumir.
ESTILO_BALAO = """<style>
#vg-tooltip-element table tr td.key { max-width: 20rem; }
#vg-tooltip-element table tr:has(td.value:empty) { display: none; }
</style>"""
# A linha do tempo dos mapas (ver linha_do_tempo). O deslizante do Streamlit pinta o trilho do
# começo até o ponto escolhido, e a equipe lia isso como acumulado (13/10/2026). Aqui o trilho
# fica neutro, com um risco em cada virada de dia, e as datas das pontas ficam sempre à mostra (o
# Streamlit só as mostra com o mouse em cima). Vale só para os deslizantes com essas chaves: se um
# dia o Streamlit mudar o desenho dele, eles voltam a ser o deslizante de sempre, e nada quebra.
COR_DA_LINHA_DO_TEMPO = "rgba(128, 128, 128, 0.35)"   # cinza translúcido: serve ao tema claro e ao escuro
COR_DO_RISCO_DO_DIA = "rgba(128, 128, 128, 0.75)"


def _estilo_da_linha_do_tempo(chave: str, marcas: list[float]) -> str:
    deslizante = f".st-key-{chave} [data-testid='stSlider']"
    riscos = ", ".join(
        f"linear-gradient(to right, transparent calc({p:.3f}% - 1px), {COR_DO_RISCO_DO_DIA} calc({p:.3f}% - 1px), "
        f"{COR_DO_RISCO_DO_DIA} calc({p:.3f}% + 1px), transparent calc({p:.3f}% + 1px))" for p in marcas)
    # Os riscos vão no fundo da caixa do trilho, 14 px de altura em volta dele
    dias = (f"{deslizante} [role='group'] > div {{ background-image: {riscos}; background-size: 100% 14px; "
            "background-repeat: no-repeat; background-position: 0 13px; }") if marcas else ""
    return (f"<style>{deslizante} [role='group'] > div > div:first-child {{ background-image: none !important; "
            f"background-color: {COR_DA_LINHA_DO_TEMPO} !important; }} "
            f"{deslizante} [data-testid='stSliderTickBar'] {{ opacity: 1 !important; }} {dias}</style>")

# O streamlit redireciona a saída dos módulos, e no Windows ela vai em cp1252: sem isto, o
# primeiro aviso com emoji (uma nova tentativa na API, por exemplo) derruba a tela inteira.
for _fluxo in (sys.stdout, sys.stderr):
    if hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(encoding="utf-8", errors="replace")

st.set_page_config(page_title="Painel Meteorológico", page_icon="🌡️", layout="wide")

# O Altair recusa, por padrão, mais de 5 mil linhas num gráfico — um limite pensado para
# caderno de notas, onde o dado fica embutido no arquivo. Aqui o spec vai pelo websocket e
# pesa cerca de 0,4 MB a cada 2,5 mil linhas. Doze estações numa semana de dado horário, com
# três séries cada, dão 6 mil linhas: sem isto a aba caía com MaxRowsError. O limite real é a
# legibilidade — trinta linhas no mesmo desenho já não se leem —, e não o tamanho do spec.
alt.data_transformers.enable("default", max_rows=20000)


# A sigla da UF entra em toda função guardada em cache. Sem ela na chave, duas pessoas com
# estados diferentes no mesmo processo dividiriam o mesmo mapa — e a segunda veria o da primeira.
@st.cache_data(ttl=3600, show_spinner=False)
def carregar_estacoes(uf: str) -> pd.DataFrame:
    return fonte.estacoes(uf)


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_apoio(uf: str) -> pd.DataFrame:
    base = base_cartografica(uf)
    return fonte.estacoes_de_apoio(config.recorte_de(uf), base.lon_grade[base.dentro_uf],
                                   base.lat_grade[base.dentro_uf])


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_leituras(codigos: tuple[str, ...], nomes: tuple[str, ...],
                      inicio: datetime, fim: datetime, uf: str,
                      _mensagem: str = "Consultando o INMET") -> tuple[pd.DataFrame, list[str]]:
    barra = st.progress(0.0, text=_mensagem)

    def avancar(concluidas: int, total: int) -> None:
        barra.progress(concluidas / total, text=f"{_mensagem} — {concluidas} de {total} estações")

    tabela, falharam = coleta.varias(codigos, nomes, inicio, fim, avancar,
                                     config.recorte_de(uf).fuso)
    barra.empty()
    for coluna, fator in CONVERSOES.items():
        if coluna in tabela:
            tabela[coluna] = tabela[coluna] * fator
    return tabela, falharam


def series_da_grandeza(tabela: pd.DataFrame, grandeza: str, modo: str):
    pedacos, usados = [], []
    for produto in variaveis.disponiveis(modo, variaveis.GRAFICO):
        if produto.grandeza != grandeza:
            continue
        largo = variaveis.no_tempo(produto, tabela, modo)
        if largo.empty:
            continue
        pedacos.append(largo.reset_index()
                       .melt("dt_local", var_name="Estação", value_name="valor")
                       .assign(Série=variaveis.rotulo_curto(produto)))
        usados.append(produto)
    if not pedacos:
        return pd.DataFrame(columns=["dt_local", "Estação", "valor", "Série"]), []
    return pd.concat(pedacos, ignore_index=True).dropna(subset=["valor"]), usados


def _apelidos(longo: pd.DataFrame) -> dict[tuple[str, str], str]:
    pares = (longo[["Estação", "Série"]].drop_duplicates()
             .sort_values(["Estação", "Série"]).itertuples(index=False))
    return {(estacao, serie): f"s{indice}" for indice, (estacao, serie) in enumerate(pares)}


def _regua(dados: pd.DataFrame, linhas_do_balao: list[tuple[str, str, str]], casas: int,
           por_dia: bool, apontar: alt.Parameter) -> alt.Chart:
    grafico = alt.Chart(dados).transform_pivot("chave", value="valor", groupby=["dt_local"])
    balao = [alt.Tooltip("dt_local:T", title="Quando", format="%d/%m" if por_dia else "%d/%m %H:%M")]
    for nome, apelido, serie in linhas_do_balao:
        # Sem nada escolhido na legenda, tudo aparece; com uma série escolhida, só ela
        visivel = (f"!length(data('isolar_store')) "
                   f"|| vlSelectionTest('isolar_store', {{'Série': {serie!r}}})")
        grafico = grafico.transform_calculate(**{
            f"t{apelido}": f"({visivel}) && isValid(datum[{apelido!r}]) "
                           f"? format(datum[{apelido!r}], '.{casas}f') : ''"})
        balao.append(alt.Tooltip(f"t{apelido}:N", title=nome))

    return (grafico
            .mark_rule(color="#9a9a9a", strokeWidth=1)
            .encode(x=alt.X("dt_local:T", title=None),
                    opacity=alt.condition(apontar, alt.value(0.5), alt.value(0)),
                    tooltip=balao)
            .add_params(apontar))


def _escala_das_cores(cores: dict[str, str] | None, presentes: pd.Series):
    if not cores:
        return alt.Undefined
    nomes = [nome for nome in cores if nome in set(presentes)]
    return alt.Scale(domain=nomes, range=[cores[nome] for nome in nomes])


def desenhar(longo: pd.DataFrame, rotulo_y: str, zero_na_base: bool, modo: str, casas: int = 1,
             cores: dict[str, str] | None = None) -> alt.Chart:
    # Arrastar move e Shift+roda aproxima. Sem o Shift, a roda do mouse em cima do gráfico
    # aproximaria em vez de rolar a página, e quem passa por vários gráficos fica preso no
    # primeiro (é o que o .interactive() faz por padrão).
    navegar = alt.selection_interval(bind="scales", zoom="wheel![event.shiftKey]")
    # Nomeada porque o balão precisa perguntar, lá dentro, que série a legenda escolheu
    isolar = alt.selection_point(name="isolar", fields=["Série"], bind="legend")
    # O cursor em qualquer lugar do gráfico marca a hora mais próxima, e não só quando cai em
    # cima de um ponto: o Vega monta a vizinhança de cada instante e o balão traz todas as
    # estações daquela hora de uma vez — que é a comparação que se quer fazer.
    apontar = alt.selection_point(fields=["dt_local"], nearest=True, on="pointerover", empty=False)

    # Hora em cima e data embaixo, como nos meteogramas; por dia, só a data — e aí uma marca por
    # dia, senão o eixo marca de meio em meio dia e a mesma data aparece duas vezes.
    por_dia = modo == variaveis.DIA
    formato = ("[timeFormat(datum.value, '%d/%m')]" if por_dia
               else "[timeFormat(datum.value, '%H:%M'), timeFormat(datum.value, '%d/%m')]")
    apelidos = _apelidos(longo)
    chaves = pd.Series(list(zip(longo["Estação"], longo["Série"])), index=longo.index)
    dados = longo.assign(chave=chaves.map(apelidos))

    eixo_x = alt.X("dt_local:T", title=None,
                   axis=alt.Axis(grid=True, gridOpacity=0.25, labelAngle=0, labelExpr=formato,
                                 labelFontSize=FONTE_DOS_EIXOS, tickCount="day" if por_dia else alt.Undefined))
    base = alt.Chart(dados).encode(
        x=eixo_x,
        y=alt.Y("valor:Q", title=rotulo_y, scale=alt.Scale(zero=zero_na_base),
                axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS)),
        color=alt.Color("Estação:N", title=None, legend=alt.Legend(orient="bottom"),
                        scale=_escala_das_cores(cores, longo["Estação"])),
        opacity=alt.condition(isolar, alt.value(1), alt.value(0.12)))
    linhas = (base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(filled=True, size=32))
              .encode(strokeDash=alt.StrokeDash("Série:N", title=None,
                                                legend=alt.Legend(orient="bottom")))
              .add_params(navegar, isolar))
    # O ponto da hora apontada cresce, nas linhas que a legenda deixa à mostra: sem isto, o balão
    # diz números que quem olha não sabe a qual altura do gráfico pertencem.
    destaque = (base.mark_point(size=150, filled=True)
                .transform_filter(apontar).transform_filter(isolar))

    # Com uma série só (a direção do vento), repetir o nome dela em cada linha seria ruído
    series = list(dict.fromkeys(serie for _, serie in apelidos))
    linhas_do_balao = [(estacao if len(series) == 1 else f"{estacao} · {serie}", apelido, serie)
                       for (estacao, serie), apelido in apelidos.items()]
    regua = _regua(dados, linhas_do_balao, casas, por_dia, apontar)

    return alt.layer(linhas, destaque, regua).properties(height=ALTURA_GRAFICO)


def barras_do_acumulado(valores: pd.Series, unidade: str, quantas: int | None) -> alt.Chart:
    tabela = (valores.dropna().rename("valor").rename_axis("Estação").reset_index()
              .sort_values("valor", ascending=False))
    if quantas:
        # Só quem choveu: numa janela seca, quinze barras de 0,0 mm não dizem nada
        tabela = tabela[tabela["valor"] > 0].head(quantas)
    base = alt.Chart(tabela)
    # labelOverlap=False obriga o eixo a escrever todos os nomes. Com pouca altura ele descarta
    # um sim, um não — e aí uma lista de 15 estações parece uma de 8.
    eixo_estacao = alt.Y("Estação:N", sort="-x", title=None,
                         axis=alt.Axis(labelFontSize=FONTE_DOS_EIXOS, labelOverlap=False, labelLimit=230))
    # size, e não height: em barra deitada é ele que define a espessura. Com height a barra
    # engorda até preencher a faixa, e os nomes das estações ficam espremidos entre elas.
    barras = base.mark_bar(color="#2171b5", size=20).encode(
        x=alt.X("valor:Q", title=unidade,
                axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS, titleFontSize=13,
                              tickCount=8)),
        y=eixo_estacao,
        tooltip=[alt.Tooltip("Estação:N"), alt.Tooltip("valor:Q", title=unidade, format=".1f")])
    numeros = base.mark_text(align="left", dx=6, color="#d0d0d0", fontSize=FONTE_DOS_ROTULOS).encode(
        x=alt.X("valor:Q"), y=eixo_estacao, text=alt.Text("valor:Q", format=".1f"))
    # Altura por estação, mais uma folga para o eixo de baixo: é o que garante espaço para cada
    # nome. Com altura fixa, três estações ficavam espremidas e quinze viravam oito.
    return (barras + numeros).properties(height=34 * len(tabela) + 60)


def cascata_da_chuva(barras: pd.DataFrame, por_dia: bool, titulo: str = "", teto: float | None = None,
                     altura: int = 300) -> alt.Chart:
    ordem = list(barras.sort_values("ordem")["Passo"].unique())
    base = alt.Chart(barras).encode(
        x=alt.X("Passo:N", title=None, sort=ordem, axis=alt.Axis(labelAngle=-45, labelFontSize=FONTE_DOS_EIXOS)))
    desenho = base.mark_bar(size=14 if por_dia else 8).encode(
        y=alt.Y("base:Q", title="Chuva (mm)",
                axis=alt.Axis(grid=True, gridOpacity=0.25, format=".1f", labelFontSize=FONTE_DOS_EIXOS),
                # Uma folga acima do teto, para o número da barra mais alta caber
                scale=alt.Scale(domain=[0, teto * 1.12]) if teto else alt.Undefined),
        y2="topo:Q",
        color=alt.Color("tipo:N", title=None,
                        scale=alt.Scale(domain=["passo", "total"], range=["#6baed6", "#08306b"]),
                        legend=None),
        tooltip=[alt.Tooltip("Estação:N"), alt.Tooltip("Passo:N", title="Quando"),
                 alt.Tooltip("valor:Q", title="Chuva (mm)", format=".1f"),
                 alt.Tooltip("topo:Q", title="Acumulado (mm)", format=".1f")])
    # O número vai em cima da barra, e só onde choveu: a hora seca é a maioria das horas, e um
    # "0.0" em cada uma cobriria justamente as barras que interessam.
    numeros = (base.transform_filter(alt.datum.valor > 0)
               .mark_text(baseline="bottom", dy=-4, fontSize=FONTE_DOS_ROTULOS, color="#d0d0d0")
               .encode(y=alt.Y("topo:Q"), text=alt.Text("valor:Q", format=".1f")))
    return (desenho + numeros).properties(height=altura, title=titulo)


def rosa_dos_ventos(tabela: pd.DataFrame, nome_estacao: str):
    dados = tabela[tabela["Estação"] == nome_estacao].dropna(subset=["VEN_DIR", "VEN_VEL"])
    setores = ((dados["VEN_DIR"] % 360) / 22.5).round().astype(int) % 16
    rotulos = [f"{menor:g}–{maior:g}" if maior != float("inf") else f"≥ {menor:g}" for menor, maior in FAIXAS_VENTO]
    faixas = pd.cut(dados["VEN_VEL"], bins=[menor for menor, _ in FAIXAS_VENTO] + [float("inf")],
                    right=False, labels=rotulos)
    contagem = pd.crosstab(setores, faixas).reindex(range(16), fill_value=0).reindex(columns=rotulos, fill_value=0)

    # Figure direto, e não plt.figure: o pyplot guarda as figuras num estado global, que num
    # servidor com várias pessoas ao mesmo tempo vaza memória e pode embaralhar dois desenhos.
    # O corpo do texto é grande para o tamanho da figura porque ela é mostrada em cerca de 200 px,
    # uma de cada ROSAS_MAXIMAS colunas: no tamanho natural ficaria miúdo.
    fig = Figure(figsize=(3.2, 3.6))
    ax = fig.add_subplot(projection="polar")
    angulos, base = np.deg2rad(np.arange(16) * 22.5), np.zeros(16)
    for rotulo, cor in zip(rotulos, CORES_VENTO):
        alturas = contagem[rotulo].to_numpy()
        ax.bar(angulos, alturas, width=np.deg2rad(20), bottom=base, color=cor, edgecolor="none", label=rotulo)
        base += alturas

    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)  # norte no topo e sentido horário, como na bússola
    ax.set_xticks(np.deg2rad([0, 90, 180, 270]), ["N", "L", "S", "O"])
    ax.set_yticklabels([])
    ax.set_title(nome_estacao, fontsize=12, color="#9a9a9a", pad=12)
    ax.tick_params(colors="#9a9a9a", labelsize=11)
    ax.grid(color="#9a9a9a", alpha=0.25)
    ax.spines["polar"].set_color("#9a9a9a")
    ax.spines["polar"].set_alpha(0.3)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    legenda = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.04), ncols=2, fontsize=10,
                        frameon=False, handlelength=1.2, columnspacing=1.0, title="km/h", title_fontsize=10)
    legenda.get_title().set_color("#9a9a9a")
    for texto in legenda.get_texts():
        texto.set_color("#9a9a9a")
    return fig


def visao_inicial(recorte: config.Recorte) -> dict:
    oeste, leste, sul, norte = recorte.limites
    maior_lado = max(leste - oeste, norte - sul)
    return {"latitude": (sul + norte) / 2, "longitude": (oeste + leste) / 2,
            "zoom": round(ZOOM_POR_GRAU - math.log2(maior_lado), 1)}


def niveis_da_escala(produto: variaveis.Produto, horas_janela=None, ajustar: bool = False):
    if ajustar:
        return None
    escolhida = variaveis.escala(produto, horas_janela)
    if escolhida is None:
        return None
    tipo, valores = escolhida
    return np.linspace(*valores, 21) if tipo == "faixa" else list(valores)


@st.cache_data(show_spinner=False, max_entries=20)
def barra_de_escala(paleta: str, niveis: tuple, unidade: str, vertical: bool = False) -> bytes:
    so_acima = float(min(niveis)) > 0
    continua = len(niveis) > mapas.MAX_CLASSES  # faixa fixa vira 21 níveis; classes são poucas
    norma = (Normalize(vmin=min(niveis), vmax=max(niveis)) if continua
             else BoundaryNorm(niveis, ncolors=256, extend="max" if so_acima else "both"))

    # A vertical vai por cima do mapa interativo, num fundo escuro: letras mais claras
    fig = Figure(figsize=(0.9, 3.4) if vertical else (4.0, 0.62))
    eixo = fig.add_axes([0.05, 0.03, 0.3, 0.94] if vertical else [0.02, 0.55, 0.96, 0.38])
    cor = "#d6d6d6" if vertical else "#9a9a9a"
    barra = fig.colorbar(ScalarMappable(norm=norma, cmap=colormaps[paleta]), cax=eixo,
                         orientation="vertical" if vertical else "horizontal",
                         extend="max" if so_acima else "both",
                         ticks=None if continua else list(niveis))
    barra.set_label(unidade, color=cor, size=8, labelpad=2)
    barra.ax.tick_params(colors=cor, labelsize=7, length=2, pad=1)
    barra.outline.set_edgecolor(cor)
    barra.outline.set_alpha(0.4)
    fig.patch.set_alpha(0)

    arquivo = io.BytesIO()
    fig.savefig(arquivo, format="png", dpi=150, bbox_inches="tight", transparent=True)
    return arquivo.getvalue()


@st.cache_resource(show_spinner=False, max_entries=ESTADOS_NA_MEMORIA)
def base_cartografica(uf: str) -> mapas.BaseCartografica:
    return mapas.carregar_base(config.recorte_de(uf))


@st.cache_data(show_spinner=False, max_entries=30)
def mapa_do_instante(valores: pd.Series, titulo: str, unidade: str, paleta: str, decimais: int,
                     quando: str, rotulos: bool, uf: str, niveis=None, direcoes=None,
                     dpi: int = DPI_MAPA, apoio: pd.Series | None = None) -> bytes | None:
    gdf = _pontos_do_mapa(valores, titulo, uf, quando, direcoes)
    if gdf is None or len(gdf) < config.MIN_ESTACOES_INTERPOLACAO:
        return None

    espec = mapas.EspecMapa(tabela="", coluna=titulo, titulo=titulo, subtitulo=quando, arquivo="",
                            cmap=paleta, ranking="", decimais=decimais, unidade=f"{titulo} ({unidade})",
                            direcao_vento=direcoes is not None)
    figura = mapas.mapa_interpolado(gdf, espec, base_cartografica(uf), tela=mapas.Tela(rotulos=rotulos),
                                    niveis=niveis, apoio=_vizinhas_do_mapa(apoio, titulo, uf, quando))
    if figura is None:
        return None
    # Codifica uma vez só: os mesmos bytes vão para a tela e para o botão de baixar
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


@st.cache_resource(show_spinner=False, max_entries=ESTADOS_NA_MEMORIA)
def malha_fina(uf: str) -> superficie.Malha:
    recorte = config.recorte_de(uf)
    return superficie.malha(base_cartografica(uf).uf.geometry.union_all(), recorte=recorte)


@st.cache_data(show_spinner=False, max_entries=30)
def camada_superficie(valores: pd.Series, nome_produto: str, quando: str, uf: str, niveis=None,
                      apoio: pd.Series | None = None) -> str:
    produto = variaveis.por_nome(nome_produto)
    pontos = _com_coordenadas(valores, nome_produto, uf)
    vizinhas = (_com_coordenadas(apoio, nome_produto, uf)
                if apoio is not None and not apoio.dropna().empty else None)
    return superficie.como_uri(
        superficie.superficie_png(pontos, nome_produto, malha_fina(uf), produto.paleta, niveis,
                                  apoio=vizinhas))


def estacoes_vizinhas(uf: str, avisar: bool = True) -> pd.DataFrame:
    try:
        return carregar_apoio(uf)
    except fonte.ErroFonte:
        if avisar:
            st.warning("O INMET não devolveu agora a lista das estações vizinhas. Os mapas saem só com "
                       "as estações do estado, com a borda menos firme. Tente de novo em alguns minutos.")
        return carregar_estacoes(uf).iloc[0:0]


def sem_lista_de_estacoes(onde, uf: str) -> None:
    onde.error(f"O INMET não respondeu à lista de estações de {uf} agora. Costuma passar em alguns "
               "minutos.")
    onde.button("Tentar de novo", key="tentar_lista_de_novo")
    st.stop()


def _com_coordenadas(valores: pd.Series, nome_variavel: str, uf: str) -> pd.DataFrame:
    cadastro = pd.concat([carregar_estacoes(uf), estacoes_vizinhas(uf, avisar=False)], ignore_index=True)
    coordenadas = (cadastro.set_index("Estação")[["VL_LATITUDE", "VL_LONGITUDE"]]
                   .rename(columns={"VL_LATITUDE": "Latitude", "VL_LONGITUDE": "Longitude"}))
    return coordenadas.join(valores.rename(nome_variavel), how="inner").reset_index().dropna()


def _pontos_do_mapa(valores: pd.Series, nome: str, uf: str, quando: str, direcoes=None):
    pontos = _com_coordenadas(valores, nome, uf)
    if direcoes is not None:
        # A direção não vira superfície: vai como seta sobre a estação, e o valor do mapa dá o
        # comprimento dela.
        pontos["Direção (°)"] = pontos["Estação"].map(direcoes)
    return mapas.preparar_pontos(pontos, nome, quando)


def _vizinhas_do_mapa(apoio: pd.Series | None, nome: str, uf: str, quando: str):
    if apoio is None or apoio.dropna().empty:
        return None
    return mapas.preparar_pontos(_com_coordenadas(apoio, nome, uf), nome, quando)


def linha_do_tempo(rotulo: str, paradas: list, chave: str, formatar, dia_de=None):
    marcas = (variaveis.marcas_dos_dias(paradas, dia_de) if dia_de
              else variaveis.marcas_dos_dias(paradas))
    st.html(_estilo_da_linha_do_tempo(chave, marcas))   # só estilo: o Streamlit não reserva espaço para ele
    return st.select_slider(rotulo, options=paradas, value=paradas[-1], format_func=formatar, key=chave)


def botao_do_boletim(medida: str, chave: str, arquivo: str, desenhar) -> None:
    st.download_button(f"Baixar PNG do boletim — {medida}", lambda: boletim.png(desenhar()),
                       mime="image/png", key=f"boletim_{chave}", file_name=arquivo, on_click="ignore",
                       help="Como o main.py grava: título, logos do CEMTEC e da SEMADESC, ranking e "
                            "barra de cores, em 300 dpi. As cores e a escala são as da tela.")


def desenho_do_boletim(valores: pd.Series, nome: str, grandeza: str, unidade: str, paleta: str,
                       decimais: int, subtitulo: str, uf: str, niveis=None, direcoes=None, apoio=None):
    gdf = _pontos_do_mapa(valores, nome, uf, subtitulo, direcoes)
    vizinhas = _vizinhas_do_mapa(apoio, nome, uf, subtitulo)
    espec = boletim.espec(nome, grandeza, unidade, paleta, decimais, uf, subtitulo, direcoes is not None)
    base = base_cartografica(uf)
    return lambda: mapas.mapa_interpolado(gdf, espec, base, niveis=niveis, apoio=vizinhas)


def desenho_de_classes(grade, pontos: pd.DataFrame, coluna: str, espec: mapas.EspecClasses, uf: str,
                       indicadores: mapas.Indicadores | None = None):
    gdf = mapas.preparar_pontos(pontos, coluna, espec.subtitulo)
    base = base_cartografica(uf)
    return lambda: mapas.mapa_classes_interpolado(grade, gdf, coluna, espec, base, indicadores=indicadores)


def desenho_das_horas(grade, pontos: pd.DataFrame, espec: mapas.EspecMapa, uf: str):
    gdf = mapas.preparar_pontos(pontos, espec.coluna, espec.subtitulo)
    base = base_cartografica(uf)
    return lambda: mapas.mapa_de_grade(grade, gdf, espec, base, niveis=niveis_das_horas(grade))


def niveis_das_horas(grade):
    maximo = int(grade.max())
    return np.arange(0, max(maximo, 1) + 1) if maximo < 12 else 12


def painel_do_mapa(produto: variaveis.Produto, valores: pd.Series, rotulo: str, subtitulo: str,
                   carimbo: str, rotulos: bool, uf: str, niveis=None, direcoes=None, apoio=None) -> None:
    st.markdown(f"**{produto.nome}**")
    medida = produto.nome.lower()
    if valores.dropna().empty:
        st.info(f"Nenhuma estação mediu {medida} em {rotulo}.")
        return

    png = mapa_do_instante(valores, produto.nome, produto.unidade, produto.paleta,
                           produto.decimais, rotulo, rotulos, uf, niveis, direcoes, apoio=apoio)
    if png is None:
        st.warning(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram {medida} em {rotulo}: "
                   "com tão poucos pontos a superfície inventaria mais do que mostra.")
        return

    st.image(png, width="stretch")
    if niveis is not None:
        st.image(barra_de_escala(produto.paleta, tuple(niveis), produto.unidade), width="stretch")
    # A regra do produto vai junto, para ninguém precisar adivinhar que conta é aquela
    casas = produto.decimais
    st.caption(f"{valores.min():.{casas}f} a {valores.max():.{casas}f} {produto.unidade} · "
               f"{valores.notna().sum()} estações · {produto.regra}.")
    arquivo = produto.nome.lower().replace("/", "").replace(" ", "_")
    st.download_button(f"Baixar PNG — {medida}", png, mime="image/png", key=f"baixar_{produto.nome}",
                       file_name=f"Mapa_{arquivo}_{carimbo}.png")
    # O título do boletim pode não ser o nome da tela: "Rajada s/ direção" sai "Rajada máxima em MS"
    botao_do_boletim(medida, produto.nome, boletim.nome_do_arquivo(produto.nome, uf, carimbo),
                     desenho_do_boletim(valores, produto.titulo or produto.nome, produto.grandeza,
                                        produto.unidade, produto.paleta, produto.decimais, subtitulo, uf,
                                        niveis, direcoes, apoio))


def escala_do_periodo(produto: variaveis.Produto, leituras: pd.DataFrame, horas_janela):
    niveis = niveis_da_escala(produto, horas_janela)
    if niveis is not None:
        return niveis
    coluna = produto.colunas[0]
    if coluna not in leituras or leituras[coluna].dropna().empty:
        return None
    menor, maior = float(leituras[coluna].min()), float(leituras[coluna].max())
    return np.linspace(menor, maior, 21) if maior > menor else None


@st.cache_data(show_spinner=False, max_entries=3)
def gif_do_mapa(nome_produto: str, leituras: pd.DataFrame, modo: str, momentos: tuple,
                rotulos: bool, uf: str, niveis=None) -> bytes | None:
    produto = variaveis.por_nome(nome_produto)
    barra = st.progress(0.0, text=f"Desenhando o GIF de {produto.nome.lower()}")
    quadros = []
    for indice, momento in enumerate(momentos, start=1):
        fatia = variaveis.recorte(leituras, modo, momento)
        setas = variaveis.direcoes(produto, fatia)
        png = mapa_do_instante(variaveis.por_estacao(produto, fatia), produto.nome, produto.unidade,
                               produto.paleta, produto.decimais, animacao.carimbo(momento, modo == variaveis.DIA),
                               rotulos, uf, niveis, None if setas.empty else setas, DPI_GIF)
        if png is not None:
            quadros.append((png, animacao.carimbo(momento, modo == variaveis.DIA)))
        barra.progress(indice / len(momentos),
                       text=f"Desenhando o GIF de {produto.nome.lower()} — quadro {indice} de {len(momentos)}")
    barra.empty()
    return animacao.montar(quadros) if quadros else None


def painel_do_gif(produto: variaveis.Produto, leituras: pd.DataFrame, modo: str, paradas: list,
                  rotulos: bool, horas_janela, uf: str) -> None:
    momentos = animacao.passos(paradas)
    salto = animacao.intervalo(paradas, momentos)
    chave = f"gif_{produto.nome}"
    if st.button(f"Gerar GIF — {produto.nome.lower()}", key=f"botao_{chave}",
                 help=f"{len(momentos)} quadros, um a cada "
                      f"{salto} {'dia' if modo == variaveis.DIA else 'hora'}{'s' if salto > 1 else ''}."):
        st.session_state[chave] = True

    if not st.session_state.get(chave):
        return

    gif = gif_do_mapa(produto.nome, leituras, modo, tuple(momentos), rotulos, uf,
                      escala_do_periodo(produto, leituras, horas_janela))
    if gif is None:
        st.warning("Não houve dado suficiente para desenhar a sequência.")
        return

    st.image(gif, width="stretch")
    passo = f"{salto} {'dia' if modo == variaveis.DIA else 'hora'}{'s' if salto > 1 else ''}"
    st.caption(f"{len(momentos)} quadros, um a cada {passo}, com a escala de cores travada para "
               f"o período inteiro. {len(gif) / 1e6:.1f} MB.")
    st.download_button(f"Baixar GIF — {produto.nome.lower()}", gif, mime="image/gif",
                       key=f"baixar_{chave}",
                       file_name=f"Animacao_{produto.nome.lower().replace('/', '').replace(' ', '_')}_"
                                 f"{momentos[0]:%Y%m%d}_a_{momentos[-1]:%Y%m%d}.gif")


def painel_da_chuva(rotulo: str, valores: pd.Series, janela: str, subtitulo: str, carimbo: str,
                    rotulos: bool, uf: str, niveis=None, apoio=None) -> None:
    st.markdown(f"**Chuva {rotulo}**")
    if valores.dropna().empty:
        st.info(f"Nenhuma estação mediu chuva em {janela}.")
        return

    png = mapa_do_instante(valores, f"Chuva {rotulo}", "mm", "Blues", 1, janela, rotulos, uf,
                           niveis, apoio=apoio)
    if png is None:
        st.warning(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram chuva em {janela}.")
        return

    st.image(png, width="stretch")
    if niveis is not None:
        st.image(barra_de_escala("Blues", tuple(niveis), "mm"), width="stretch")
    st.caption(f"{valores.min():.1f} a {valores.max():.1f} mm · {valores.notna().sum()} estações · "
               f"soma das horas de {janela}.")
    st.download_button(f"Baixar PNG — chuva {rotulo}", png, mime="image/png",
                       key=f"baixar_chuva_{rotulo}",
                       file_name=f"Mapa_chuva_{rotulo.replace(' ', '')}.png")
    nome = boletim.nome_da_chuva(rotulo)
    botao_do_boletim(f"chuva {rotulo}", f"chuva_{rotulo}",
                     boletim.nome_do_arquivo(nome, uf, carimbo),
                     desenho_do_boletim(valores, nome, "Chuva", "mm", "Blues", 1, subtitulo, uf, niveis,
                                        apoio=apoio))


@st.cache_data(show_spinner=False, max_entries=2)
def risco_avaliado(leituras: pd.DataFrame, estacoes_do_estado: pd.DataFrame, uf: str):
    # `leituras` e `estacoes_do_estado` já vêm com as vizinhas dentro: a grade de risco de uma
    # célula da divisa depende do que acontece dos dois lados. Quem separa o produto do apoio é
    # `risco.da_uf`, na aba.
    completas = risco.por_estacao(leituras, estacoes_do_estado)
    if not completas:
        return [], {}
    quais = risco.horas(completas)
    barra = st.progress(0.0, text="Avaliando a regra 30-30-30 hora a hora")
    avaliadas = {}
    for indice, hora in enumerate(quais, start=1):
        avaliadas.update(risco.avaliar(completas, [hora], base_cartografica(uf)))
        barra.progress(indice / len(quais),
                       text=f"Avaliando a regra 30-30-30 — hora {indice} de {len(quais)}")
    barra.empty()
    return completas, avaliadas


@st.cache_data(show_spinner=False, max_entries=30)
def mapa_de_risco(grade, pontos: pd.DataFrame, coluna: str, quando: str, detalhes: bool, uf: str,
                  dpi: int = DPI_MAPA) -> bytes | None:
    gdf = mapas.preparar_pontos(pontos, coluna, quando)
    if gdf is None:
        return None
    espec = mapas.EspecClasses(f"Risco de fogo em {uf}", quando, "",
                               config.CORES_RISCO, config.ROTULOS_RISCO)
    figura = mapas.mapa_classes_interpolado(
        grade, gdf, coluna, espec, base_cartografica(uf),
        indicadores=risco.indicadores(pontos, detalhes), tela=mapas.Tela(rotulos=detalhes))
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


@st.cache_data(show_spinner=False, max_entries=6)
def mapa_de_horas_altas(grade, pontos: pd.DataFrame, quando: str, detalhes: bool,
                        uf: str) -> bytes | None:
    gdf = mapas.preparar_pontos(pontos, risco_fogo.COLUNA_HORAS_ALTO, quando)
    if gdf is None:
        return None
    espec = mapas.EspecMapa(tabela="", coluna=risco_fogo.COLUNA_HORAS_ALTO,
                            titulo=f"Horas em risco alto em {uf}", subtitulo=quando,
                            arquivo="", cmap="YlOrRd", ranking="",
                            unidade="Horas em risco alto", decimais=0)
    figura = mapas.mapa_de_grade(grade, gdf, espec, base_cartografica(uf), niveis=niveis_das_horas(grade),
                                 tela=mapas.Tela(rotulos=detalhes))
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=DPI_MAPA, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


@st.cache_data(show_spinner=False, max_entries=2)
def gif_do_risco(instantes: tuple, detalhes: bool, uf: str, _avaliadas: dict,
                 _do_estado: set | None = None) -> bytes | None:
    barra = st.progress(0.0, text="Desenhando o GIF do risco")
    quadros = []
    for indice, hora in enumerate(instantes, start=1):
        avaliada = _avaliadas[hora]
        carimbo = animacao.carimbo(hora.tz_convert(config.recorte_de(uf).fuso), por_dia=False)
        pontos = (avaliada.estacoes if _do_estado is None
                  else avaliada.estacoes[avaliada.estacoes["Estação"].isin(_do_estado)])
        png = mapa_de_risco(avaliada.grade, pontos, risco_fogo.COLUNA_NIVEL_HORA,
                            carimbo, detalhes, uf, DPI_GIF)
        if png is not None:
            quadros.append((png, carimbo))
        barra.progress(indice / len(instantes),
                       text=f"Desenhando o GIF do risco — quadro {indice} de {len(instantes)}")
    barra.empty()
    return animacao.montar(quadros) if quadros else None


def ordem_das_estacoes(horaria: pd.DataFrame) -> list[str]:
    por_nivel = (horaria.pivot_table(index="Estação", columns=risco_fogo.COLUNA_NIVEL_HORA,
                                     values="dia", aggfunc="count")
                 .reindex(columns=[1, risco.NIVEL_MEDIO, risco.NIVEL_ALTO]).fillna(0))
    return list(por_nivel.sort_values([risco.NIVEL_ALTO, risco.NIVEL_MEDIO, 1],
                                      ascending=False).index)


# Do pior nível para o mais brando, nas barras e nas legendas: a estação que chegou ao risco alto
# mostra o vermelho primeiro (pedido da equipe, 13/10/2026)
NIVEIS_DO_PIOR = [risco.NIVEL_ALTO, risco.NIVEL_MEDIO, 1]
NOMES_DOS_NIVEIS = {0: "Sem condição", 1: "Baixo", risco.NIVEL_MEDIO: "Médio", risco.NIVEL_ALTO: "Alto"}


def _legenda_dos_niveis(niveis: list[int]) -> alt.Legend:
    nomes = ", ".join(f"'{nivel}': '{NOMES_DOS_NIVEIS[nivel]}'" for nivel in niveis)
    return alt.Legend(orient="bottom", labelExpr=f"{{{nomes}}}[datum.label]")


def barras_de_niveis(horaria: pd.DataFrame, ordem: list[str]) -> alt.Chart:
    contagem = (horaria[horaria[risco_fogo.COLUNA_NIVEL_HORA] > 0]
                .groupby(["Estação", risco_fogo.COLUNA_NIVEL_HORA]).size().reset_index(name="Horas"))
    return (alt.Chart(contagem)
            .mark_bar()
            .encode(x=alt.X("Horas:Q", title="Horas",
                            axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS)),
                    y=alt.Y("Estação:N", sort=ordem, title=None,
                            axis=alt.Axis(labelFontSize=FONTE_DOS_EIXOS, labelOverlap=False, labelLimit=230)),
                    color=alt.Color(f"{risco_fogo.COLUNA_NIVEL_HORA}:O", title="Nível",
                                    scale=alt.Scale(domain=NIVEIS_DO_PIOR,
                                                    range=[config.CORES_RISCO[nivel] for nivel in NIVEIS_DO_PIOR]),
                                    legend=_legenda_dos_niveis(NIVEIS_DO_PIOR)),
                    # A ordem do empilhamento: o alto encostado no nome da estação
                    order=alt.Order(f"{risco_fogo.COLUNA_NIVEL_HORA}:Q", sort="descending"),
                    tooltip=["Estação", alt.Tooltip(f"{risco_fogo.COLUNA_NIVEL_HORA}:O", title="Nível"),
                             "Horas"])
            .properties(height=22 * max(contagem["Estação"].nunique(), 1) + 60))


def calendario_de_risco(horaria: pd.DataFrame, ordem: list[str]) -> alt.Chart:
    por_dia = (horaria.groupby(["Estação", "dia"])[risco_fogo.COLUNA_NIVEL_HORA].max()
               .reset_index().assign(dia=lambda tabela: tabela["dia"].astype(str)))
    return (alt.Chart(por_dia)
            .mark_rect(stroke="#111111", strokeWidth=1)
            .encode(x=alt.X("dia:O", title=None, axis=alt.Axis(labelAngle=-45, labelFontSize=FONTE_DOS_EIXOS)),
                    y=alt.Y("Estação:N", sort=ordem, title=None,
                            axis=alt.Axis(labelFontSize=FONTE_DOS_EIXOS, labelOverlap=False, labelLimit=230)),
                    color=alt.Color(f"{risco_fogo.COLUNA_NIVEL_HORA}:O", title="Pior nível do dia",
                                    scale=alt.Scale(domain=[*NIVEIS_DO_PIOR, 0],
                                                    range=[config.CORES_RISCO[nivel] for nivel in [*NIVEIS_DO_PIOR, 0]]),
                                    legend=_legenda_dos_niveis([*NIVEIS_DO_PIOR, 0])),
                    tooltip=["Estação", alt.Tooltip("dia:O", title="Dia"),
                             alt.Tooltip(f"{risco_fogo.COLUNA_NIVEL_HORA}:O", title="Pior nível")])
            .properties(height=20 * max(por_dia["Estação"].nunique(), 1) + 60))


def condicoes_do_dia(horaria: pd.DataFrame, dia, ordem: list[str]) -> alt.Chart:
    do_dia = horaria[horaria["dia"] == dia]
    somas = (do_dia.groupby("Estação")[risco_fogo.COLUNAS_CONDICOES].sum().reset_index()
             .melt("Estação", var_name="Condição", value_name="Horas"))
    somas = somas[somas["Horas"] > 0]
    rotulos = dict(zip(risco_fogo.COLUNAS_CONDICOES, risco_fogo.rotulos_condicoes()))
    somas["Condição"] = somas["Condição"].map(rotulos)
    return (alt.Chart(somas)
            .mark_bar()
            .encode(x=alt.X("Horas:Q", title="Horas",
                            axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS)),
                    y=alt.Y("Estação:N", sort=ordem, title=None,
                            axis=alt.Axis(labelFontSize=FONTE_DOS_EIXOS, labelOverlap=False, labelLimit=230)),
                    yOffset=alt.YOffset("Condição:N"),
                    color=alt.Color("Condição:N", title=None,
                                    scale=alt.Scale(domain=list(rotulos.values()),
                                                    range=config.CORES_CONDICOES),
                                    legend=alt.Legend(orient="bottom", columns=1)),
                    tooltip=["Estação", "Condição", "Horas"])
            .properties(height=30 * max(somas["Estação"].nunique(), 1) + 80))


# =====================================================
# PREVISÃO
# =====================================================
@st.cache_resource(show_spinner=False, max_entries=1)
def guarda_da_previsao() -> previsao.Guarda:
    return previsao.Guarda()


# O nome de cada conjunto de pontos na guarda da página e no banco
CONJUNTOS_NO_BANCO = {"grade": "grade", "estações": "estacao", "grade-semanas": "grade",
                      "estações-semanas": "estacao"}


@st.cache_data(ttl=300, show_spinner=False)
def rodadas_atuais(conjunto: str) -> tuple[dict | None, str | None]:
    try:
        return fonte.rodadas_da_previsao(conjunto, config.MODELOS_PREVISAO), None
    except fonte.ErroPrevisao as erro:
        return None, str(erro)


def previsao_dos_pontos(conjunto: str, pontos: pd.DataFrame,
                        modelos: tuple[str, ...]) -> tuple[list[pd.DataFrame], list[str]]:
    no_banco = CONJUNTOS_NO_BANCO[conjunto]
    rodadas, falha = rodadas_atuais(no_banco)
    tabelas, avisos = [], []
    for modelo in modelos:
        rodada = rodadas.get(modelo) if rodadas else None
        tabela = _obter_previsao(conjunto, modelo, rodada, falha,
                                 lambda modelo=modelo, rodada=rodada: fonte.previsao(no_banco, pontos, modelo, rodada),
                                 avisos)
        if tabela is not None:
            tabelas.append(tabela)
    return tabelas, avisos


@st.cache_data(ttl=300, show_spinner=False)
def rodada_semanal_atual(conjunto: str) -> tuple[datetime | None, str | None]:
    try:
        return fonte.rodada_das_semanas(conjunto), None
    except fonte.ErroPrevisao as erro:
        return None, str(erro)


def semanas_dos_pontos(conjunto: str, pontos: pd.DataFrame) -> tuple[pd.DataFrame | None, list[str]]:
    no_banco = CONJUNTOS_NO_BANCO[conjunto]
    rodada, falha = rodada_semanal_atual(no_banco)
    avisos = []
    tabela = _obter_previsao(conjunto, config.MODELO_SEMANAS, rodada, falha,
                             lambda: fonte.semanas(no_banco, pontos, rodada), avisos)
    return tabela, avisos


def _obter_previsao(conjunto: str, modelo: str, rodada: datetime | None, falha: str | None, buscar,
                    avisos: list[str]) -> pd.DataFrame | None:
    nome = openmeteo.NOMES[modelo]
    origem = fonte.origem_da_previsao()   # "o Open-Meteo", "o banco"
    guarda = guarda_da_previsao()
    try:
        if not guarda.precisa_buscar(conjunto, modelo, rodada):
            obtida = guarda.obter(conjunto, modelo, rodada, buscar)
        elif rodada is None:
            # Nada guardado e nem os metadados responderam: a busca falharia do mesmo jeito,
            # depois de esperar as tentativas. No banco, sem falha, é o modelo que não tem rodada
            motivo = f"{origem} não respondeu ({falha})" if falha else f"não há rodada dele n{origem}"
            avisos.append(f"**{nome}** ficou de fora: {motivo}.")
            return None
        else:
            with st.spinner(f"Buscando a previsão do {nome} ({conjunto}) n{origem}…"):
                obtida = guarda.obter(conjunto, modelo, rodada, buscar)
    except fonte.ErroPrevisao as erro:
        avisos.append(f"**{nome}** ficou de fora: {origem} não respondeu ({erro}).")
        return None
    guardada = previsao.rodada_da(obtida.tabela)
    if obtida.aviso and guardada is not None:
        avisos.append(f"**{nome}**: mostrando a rodada de {guardada:%d/%m %H} UTC, a última guardada, "
                      f"porque {obtida.aviso}.")
    return obtida.tabela


def barras_da_chuva_prevista(longo: pd.DataFrame) -> alt.Chart:
    base = alt.Chart(longo).encode(
        x=alt.X("yearmonthdate(dt_local):O", title=None,
                axis=alt.Axis(format="%d/%m", formatType="time", labelAngle=0,
                              labelFontSize=FONTE_DOS_EIXOS)),
        xOffset=alt.XOffset("Modelo:N", sort=list(previsao.CORES)),
        y=alt.Y("valor:Q", title="Chuva (mm)",
                axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS)))
    barras = base.mark_bar().encode(
        color=alt.Color("Modelo:N", title=None, legend=alt.Legend(orient="bottom"),
                        scale=_escala_das_cores(previsao.CORES, longo["Modelo"])),
        tooltip=[alt.Tooltip("yearmonthdate(dt_local):T", title="Dia", format="%d/%m"),
                 alt.Tooltip("Modelo:N"),
                 alt.Tooltip("valor:Q", title="Chuva (mm)", format=".1f")])
    # O número em cima de cada barra, e só onde choveu: os dias secos são a maioria, e um "0.0"
    # em cada um cobriria as barras que interessam
    numeros = (base.transform_filter(alt.datum.valor > 0)
               .mark_text(baseline="bottom", dy=-4, fontSize=FONTE_DOS_ROTULOS, color="#d0d0d0")
               .encode(text=alt.Text("valor:Q", format=".1f")))
    return (barras + numeros).properties(height=ALTURA_GRAFICO)


# O Streamlit apaga o que se escolheu num widget quando ele não aparece numa execução. Cada
# página aparece sozinha, então ir à previsão e voltar perderia as estações escolhidas no
# observado. Ficam só os filtros da barra lateral: as escolhas dentro das abas voltam ao padrão.
CHAVES_DOS_FILTROS = ("uf", "estacoes", "grandezas",
                      "previsao_estacao", "previsao_modelos", "previsao_grandezas", "previsao_modo",
                      "mapas_previstos_modelo", "mapas_previstos", "semanas_mapas", "semanas_estacao")


def lembrar_filtros() -> None:
    for chave in CHAVES_DOS_FILTROS:
        if chave in st.session_state:
            st.session_state[chave] = st.session_state[chave]


def pagina_previsao() -> None:
    st.title("Previsão do tempo — Mato Grosso do Sul")
    aviso = st.container()
    # A previsão é do Open-Meteo, mas a lista das estações é do INMET
    if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        st.error("Token do INMET não configurado. Preencha `TOKEN_INMET` no arquivo `.env` e recarregue a página.")
        st.stop()

    with st.sidebar:
        st.header("Filtros")
        try:
            estacoes = carregar_estacoes(previsao.UF).sort_values("Estação")
        except fonte.ErroFonte:
            sem_lista_de_estacoes(aviso, previsao.UF)
        nomes = estacoes["Estação"].tolist()
        codigos = estacoes["CD_ESTACAO"].astype(str).tolist()
        # Aqui a estação já vem escolhida, ao contrário do observado: a busca é das estações todas
        # de uma vez, e escolher uma ou outra não muda o custo
        if st.session_state.get("previsao_estacao") not in nomes:
            st.session_state["previsao_estacao"] = (nomes[codigos.index(previsao.ESTACAO_INICIAL)]
                                                    if previsao.ESTACAO_INICIAL in codigos else nomes[0])
        nome = st.selectbox("Estação", nomes, key="previsao_estacao",
                            help="A previsão é a do ponto da estação: é o que permite comparar, "
                                 "depois, o previsto com o que ela mediu.")
        st.session_state.setdefault("previsao_modelos", list(config.MODELOS_PREVISAO))
        modelos = st.multiselect("Modelos", list(config.MODELOS_PREVISAO),
                                 format_func=openmeteo.NOMES.get, key="previsao_modelos",
                                 help="O ECMWF e o GFS vão até o dia 14; o ICON, até o dia 7.")
        st.session_state.setdefault("previsao_grandezas", ["Temperatura", "Chuva"])
        escolhidas = st.multiselect("Grandezas", [*previsao.GRANDEZAS, previsao.GRANDEZA_RISCO],
                                    key="previsao_grandezas",
                                    help="Cada grandeza ganha o seu gráfico, com os modelos lado a lado.")

    if not modelos:
        st.info("Escolha ao menos um modelo na barra lateral.")
        st.stop()
    tabelas, avisos = previsao_dos_pontos("estações", openmeteo.pontos_das_estacoes(estacoes), tuple(modelos))
    for texto in avisos:
        aviso.warning(texto)
    if not tabelas:
        st.stop()
    codigo = str(estacoes.set_index("Estação").loc[nome, "CD_ESTACAO"])
    horaria = pd.concat([tabela[tabela["ponto"] == codigo] for tabela in tabelas], ignore_index=True)

    # Por dia de saída: catorze dias hora a hora são 336 pontos por linha
    st.session_state.setdefault("previsao_modo", "Por dia")
    modo = MODOS_GRAFICO[st.radio("Agregação", list(MODOS_GRAFICO), horizontal=True, key="previsao_modo")]
    rodadas = " · ".join(f"{openmeteo.NOMES[modelo]} {rodada:%d/%m %H} UTC" for modelo, rodada
                         in horaria.groupby("modelo")["rodada_utc"].first().items())
    st.caption(f"**Rodadas:** {rodadas}. Cada modelo é buscado de novo quando sai uma rodada dele, "
               "de 6 em 6 horas. O dia vai da 01:00 à 00:00 do dia seguinte, como no observado; no "
               "modo por dia entram só os dias inteiros. "
               "Previsão: [Open-Meteo.com](https://open-meteo.com/), com dados do ECMWF, da NOAA e "
               "do DWD ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).")
    st.markdown(ESTILO_BALAO, unsafe_allow_html=True)
    if not escolhidas:
        st.info("Escolha ao menos uma grandeza na barra lateral.")

    desde = pd.Timestamp.now(tz=config.FUSO_MS).floor("h")
    # Na ordem do catálogo, e não na da escolha: a direção fica logo depois do vento
    for grandeza in [nome for nome in previsao.GRANDEZAS if nome in escolhidas]:
        dados_grandeza = previsao.GRANDEZAS[grandeza]
        longo = previsao.series(horaria, grandeza, modo, desde)
        if longo.empty:
            st.warning(f"{grandeza}: nenhum modelo trouxe essa variável para {nome}.")
            continue
        st.subheader(grandeza)
        if grandeza == "Chuva" and modo == variaveis.DIA:
            grafico = barras_da_chuva_prevista(longo)
        else:
            grafico = desenhar(longo.rename(columns={"Modelo": "Estação"}),
                               f"{grandeza} ({dados_grandeza.unidade})", dados_grandeza.zero_na_base,
                               modo, dados_grandeza.decimais, cores=previsao.CORES)
        st.altair_chart(grafico, width="stretch")
        regras = " · ".join(f"**{serie.nome}**: {serie.regra}" for serie in dados_grandeza.series[modo])
        if dados_grandeza.circular:
            regras += (". A direção ligada por linha engana: entre 350° e 10° o vento mal mudou, mas o "
                       "traço desce o gráfico inteiro.")
        if grandeza == "Chuva":
            totais = previsao.chuva_total(horaria, desde)
            regras += ". **Total previsto:** " + " · ".join(
                f"{linha.Modelo} {linha.chuva:.1f} mm até {linha.ate:%d/%m}" for linha in totais.itertuples())
        st.caption(regras)

    if previsao.GRANDEZA_RISCO in escolhidas:
        st.subheader(previsao.GRANDEZA_RISCO)
        dias = previsao.diario_com_risco(horaria)
        dias = dias[(dias["horas"] == 24) & (dias["dia_previsto"] >= desde.date())].dropna(subset=["risco_max"])
        if dias.empty:
            st.warning(f"Nenhum modelo trouxe as três variáveis da regra para {nome}.")
        else:
            st.altair_chart(calendario_do_risco_previsto(dias), width="stretch")
            st.caption("O pior nível de cada dia pela regra 30-30-30 (temperatura ≥ 30 °C, umidade ≤ 30 %, "
                       "rajada ≥ 30 km/h), aplicada a cada hora prevista, como no produto. O modelo dá o valor "
                       "da hora cheia, e a estação mede a máxima e a mínima dentro da hora: o risco previsto "
                       "tende a sair um pouco abaixo do medido. No balão, as horas em risco alto.")

    with st.expander("Ver e baixar a previsão diária desta estação"):
        visivel = previsao.planilha(horaria)
        st.caption("Um dia por linha e modelo, pela regra do observado. `horas` diz quantas horas o "
                   "dia tem na previsão: menos de 24 no primeiro, no último e no fim do ICON.")
        st.dataframe(visivel, width="stretch", height=300, hide_index=True)
        st.download_button("Baixar CSV", visivel.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"previsao_{codigo}_{desde:%Y%m%d_%H}h.csv", mime="text/csv")


@st.cache_data(show_spinner=False, max_entries=12)
def diario_previsto(conjunto: str, modelo: str, rodada: datetime, _horaria: pd.DataFrame) -> pd.DataFrame:
    return previsao.diario_com_risco(_horaria, config.FUSO_MS)


@st.cache_data(show_spinner=False, max_entries=12)
def risco_na_grade_prevista(modelo: str, rodada: datetime, dia: date, _horaria: pd.DataFrame,
                            _pontos: pd.DataFrame) -> tuple[np.ndarray, np.ndarray] | None:
    base = base_cartografica(previsao.UF)
    return previsao.risco_na_grade(_horaria, _pontos, dia, base.lon_grade, base.lat_grade, config.FUSO_MS)


@st.cache_data(show_spinner=False, max_entries=20)
def mapa_de_risco_previsto(origem: tuple, nome: str, rotulos: bool, _grade: np.ndarray,
                           _estacoes: pd.DataFrame) -> bytes:
    espec = mapas.EspecClasses(nome, "", "", config.CORES_RISCO, config.ROTULOS_RISCO)
    figura = mapas.mapa_classes_interpolado(_grade, _pontos_previstos(_estacoes, nome), nome, espec,
                                            base_cartografica(previsao.UF), tela=mapas.Tela(rotulos=rotulos))
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=DPI_MAPA, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


@st.cache_data(show_spinner=False, max_entries=40)
def mapa_previsto(origem: tuple, nome: str, paleta: str, unidade: str, decimais: int, setas: bool,
                  rotulos: bool, niveis: tuple | None, _grade: np.ndarray, _estacoes: pd.DataFrame,
                  com_sinal: bool = False) -> bytes:
    espec = mapas.EspecMapa(tabela="", coluna=nome, titulo=nome, subtitulo="", arquivo="",
                            cmap=paleta, unidade=f"{nome} ({unidade})", ranking="",
                            decimais=decimais, direcao_vento=setas, com_sinal=com_sinal)
    figura = mapas.mapa_de_grade(_grade, _pontos_previstos(_estacoes, nome), espec,
                                 base_cartografica(previsao.UF), niveis=list(niveis) if niveis else 20,
                                 tela=mapas.Tela(rotulos=rotulos))
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=DPI_MAPA, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


def _pontos_previstos(estacoes: pd.DataFrame, nome: str):
    pontos = mapas.preparar_pontos(estacoes, nome, nome)
    if pontos is None:
        colunas = ["Estação", "Latitude", "Longitude", nome, "Direção (°)"]
        pontos = gpd.GeoDataFrame(pd.DataFrame(columns=colunas), geometry=gpd.points_from_xy([], []),
                                  crs="EPSG:4326")
    return pontos


def painel_do_mapa_previsto(mapa: previsao.MapaPrevisto, modelo: str, rodada: datetime, dia: date,
                            hoje: date, rotulos: bool, ajustar: bool, diaria_grade: pd.DataFrame,
                            pontos_grade: pd.DataFrame, diaria_estacoes: pd.DataFrame,
                            estacoes: pd.DataFrame, horaria_grade: pd.DataFrame) -> None:
    st.markdown(f"**{mapa.nome}**")
    if mapa.risco:
        painel_do_risco_previsto(mapa, modelo, rodada, dia, hoje, rotulos, horaria_grade, pontos_grade,
                                 diaria_estacoes, estacoes)
        return
    base = base_cartografica(previsao.UF)
    grade = previsao.superficie(previsao.valores_do_dia(diaria_grade, mapa, dia, hoje), pontos_grade,
                                base.lon_grade, base.lat_grade)
    if grade is None:
        st.info(f"O {openmeteo.NOMES[modelo]} não traz {mapa.nome.lower()} para {previsao.nome_do_dia(dia)}.")
        return
    pontos = previsao.nas_estacoes(diaria_estacoes, estacoes, mapa, dia, hoje)
    niveis = previsao.niveis(mapa, previsao.dias_somados(mapa, dia, hoje), ajustar)
    escala = None if niveis is None else tuple(float(nivel) for nivel in niveis)

    png = mapa_previsto(("dias", modelo, rodada, dia, hoje), mapa.nome, mapa.paleta, mapa.unidade,
                        mapa.decimais, mapa.setas, rotulos, escala, _grade=grade, _estacoes=pontos)
    st.image(png, width="stretch")
    if escala is not None:
        st.image(barra_de_escala(mapa.paleta, escala, mapa.unidade), width="stretch")
    no_estado = grade[base.dentro_uf]
    st.caption(f"{np.nanmin(no_estado):.{mapa.decimais}f} a {np.nanmax(no_estado):.{mapa.decimais}f} "
               f"{mapa.unidade} · {mapa.regra}.")

    carimbo = previsao.carimbo(modelo, rodada, mapa, dia, hoje)
    st.download_button(f"Baixar PNG — {mapa.nome.lower()}", png, mime="image/png",
                       key=f"baixar_previsto_{mapa.nome}",
                       file_name=f"Mapa_{mapa.titulo.replace(' ', '_')}_{carimbo}.png")
    espec = boletim.espec(mapa.titulo, mapa.grandeza, mapa.unidade, mapa.paleta, mapa.decimais,
                          previsao.UF, previsao.subtitulo(mapa, dia, hoje), mapa.setas,
                          credito=previsao.CREDITO, coluna=mapa.nome, credito_no_rodape=True)
    gdf = _pontos_previstos(pontos, mapa.nome)
    botao_do_boletim(mapa.nome.lower(), f"previsto_{mapa.nome}",
                     boletim.nome_do_arquivo(mapa.titulo, previsao.UF, carimbo),
                     lambda: mapas.mapa_de_grade(grade, gdf, espec, base,
                                                 niveis=list(escala) if escala else 20))


def painel_do_risco_previsto(mapa: previsao.MapaPrevisto, modelo: str, rodada: datetime, dia: date,
                             hoje: date, rotulos: bool, horaria_grade: pd.DataFrame,
                             pontos_grade: pd.DataFrame, diaria_estacoes: pd.DataFrame,
                             estacoes: pd.DataFrame) -> None:
    base = base_cartografica(previsao.UF)
    calculado = risco_na_grade_prevista(modelo, rodada, dia, _horaria=horaria_grade, _pontos=pontos_grade)
    if calculado is None:
        st.info(f"O {openmeteo.NOMES[modelo]} não traz as três variáveis da regra para {previsao.nome_do_dia(dia)}.")
        return
    nivel, horas_alto = calculado
    pontos = previsao.nas_estacoes(diaria_estacoes, estacoes, mapa, dia, hoje)
    carimbo = previsao.carimbo(modelo, rodada, mapa, dia, hoje)
    subtitulo = previsao.subtitulo_do_risco(dia)
    gdf = _pontos_previstos(pontos, mapa.nome)
    no_estado = (nivel if mapa.risco == "nivel" else horas_alto)[base.dentro_uf]

    if mapa.risco == "nivel":
        png = mapa_de_risco_previsto(("dias", modelo, rodada, dia), mapa.nome, rotulos, _grade=nivel,
                                     _estacoes=pontos)
        st.image(png, width="stretch")
        partes = [f"{config.ROTULOS_RISCO[classe].replace('Risco ', '').capitalize()} em "
                  f"{(no_estado == classe).mean():.0%}" for classe in range(len(config.CORES_RISCO) - 1, -1, -1)
                  if (no_estado == classe).any()]
        st.caption(f"{' · '.join(partes)} do estado · {mapa.regra}.")
        espec = mapas.EspecClasses(boletim.titulo(mapa.titulo, previsao.UF), subtitulo, "", config.CORES_RISCO,
                                   config.ROTULOS_RISCO, credito=previsao.CREDITO, credito_no_rodape=True)
        desenhar = lambda: mapas.mapa_classes_interpolado(nivel, gdf, mapa.nome, espec, base)  # noqa: E731
    else:
        niveis = niveis_das_horas(horas_alto)
        escala = tuple(float(valor) for valor in niveis) if np.ndim(niveis) else None
        png = mapa_previsto(("dias", modelo, rodada, dia, hoje), mapa.nome, mapa.paleta, mapa.unidade,
                            mapa.decimais, False, rotulos, escala, _grade=horas_alto, _estacoes=pontos)
        st.image(png, width="stretch")
        if escala is not None:
            st.image(barra_de_escala(mapa.paleta, escala, "horas"), width="stretch")
        st.caption(f"0 a {int(no_estado.max())} h · {mapa.regra}.")
        espec = mapas.EspecMapa("", mapa.nome, boletim.titulo(mapa.titulo, previsao.UF), subtitulo, "",
                                mapa.paleta, "Horas em risco alto", "5 MAIORES EXPOSIÇÕES", decimais=0,
                                credito=previsao.CREDITO, credito_no_rodape=True)
        desenhar = lambda: mapas.mapa_de_grade(horas_alto, gdf, espec, base, niveis=niveis)  # noqa: E731

    arquivo = mapa.titulo.replace(" — ", " ")   # sem o travessão no nome do arquivo
    st.download_button(f"Baixar PNG — {mapa.nome.lower()}", png, mime="image/png",
                       key=f"baixar_previsto_{mapa.nome}",
                       file_name=f"Mapa_{arquivo.replace(' ', '_')}_{carimbo}.png")
    botao_do_boletim(mapa.nome.lower(), f"previsto_{mapa.nome}",
                     boletim.nome_do_arquivo(arquivo, previsao.UF, carimbo), desenhar)


def calendario_do_risco_previsto(dias: pd.DataFrame) -> alt.Chart:
    dados = dias.assign(Modelo=dias["modelo"].map(openmeteo.NOMES),
                        dia=dias["dia_previsto"].map(previsao.nome_do_dia),
                        risco_max=dias["risco_max"].astype(int))
    ordem_dos_dias = list(dict.fromkeys(dados.sort_values("dia_previsto")["dia"]))
    return (alt.Chart(dados)
            .mark_rect(stroke="#111111", strokeWidth=1)
            .encode(x=alt.X("dia:O", sort=ordem_dos_dias, title=None, axis=alt.Axis(labelAngle=0, labelFontSize=FONTE_DOS_EIXOS)),
                    y=alt.Y("Modelo:N", sort=list(previsao.CORES), title=None,
                            axis=alt.Axis(labelFontSize=FONTE_DOS_EIXOS)),
                    color=alt.Color("risco_max:O", title="Pior nível do dia",
                                    scale=alt.Scale(domain=[0, 1, risco.NIVEL_MEDIO, risco.NIVEL_ALTO],
                                                    range=config.CORES_RISCO),
                                    legend=alt.Legend(orient="bottom",
                                                      labelExpr="{'0': 'Sem condição', '1': 'Baixo', "
                                                                "'2': 'Médio', '3': 'Alto'}[datum.label]")),
                    tooltip=[alt.Tooltip("Modelo:N"), alt.Tooltip("dia:O", title="Dia"),
                             alt.Tooltip("risco_max:O", title="Pior nível"),
                             alt.Tooltip("horas_risco_alto:Q", title="Horas em risco alto")])
            # A altura vai por linha, e não total: o Streamlit encaixa o gráfico na altura pedida, e o
            # eixo dos dias e a legenda tomam uns 120 px dela. Com um total de 45 px por modelo, um
            # modelo só ficava sem linha nenhuma, e dois, com linhas de 13 px.
            .properties(height=alt.Step(32)))


def pagina_mapas_previstos() -> None:
    st.title("Mapas da previsão — Mato Grosso do Sul")
    aviso = st.container()
    # A superfície é do Open-Meteo; os números sobre as estações precisam da lista do INMET
    if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        st.error("Token do INMET não configurado. Preencha `TOKEN_INMET` no arquivo `.env` e recarregue a página.")
        st.stop()

    with st.sidebar:
        st.header("Filtros")
        st.session_state.setdefault("mapas_previstos_modelo", config.MODELOS_PREVISAO[0])
        modelo = st.radio("Modelo", list(config.MODELOS_PREVISAO), format_func=openmeteo.NOMES.get,
                          key="mapas_previstos_modelo",
                          help="Um modelo por vez. O ECMWF e o GFS vão até o dia 14; o ICON, até o dia 7.")
        st.session_state.setdefault("mapas_previstos", list(previsao.PADRAO_MAPAS))
        escolhidos = st.multiselect("Mapas", list(previsao.MAPAS), key="mapas_previstos",
                                    help="A chuva acumulada soma de hoje até o dia escolhido.")
    try:
        estacoes = carregar_estacoes(previsao.UF).sort_values("Estação")
    except fonte.ErroFonte:
        sem_lista_de_estacoes(aviso, previsao.UF)
    if not escolhidos:
        st.info("Escolha ao menos um mapa na barra lateral.")
        st.stop()

    pontos_grade = fonte.pontos_da_grade()
    grades, avisos = previsao_dos_pontos("grade", pontos_grade, (modelo,))
    nas_estacoes, avisos_estacoes = previsao_dos_pontos("estações", openmeteo.pontos_das_estacoes(estacoes),
                                                        (modelo,))
    for texto in avisos + avisos_estacoes:
        aviso.warning(texto)
    if not grades:
        st.stop()
    rodada = previsao.rodada_da(grades[0])
    diaria_grade = diario_previsto("grade", modelo, rodada, grades[0])
    diaria_estacoes = (diario_previsto("estações", modelo, previsao.rodada_da(nas_estacoes[0]), nas_estacoes[0])
                       if nas_estacoes else pd.DataFrame())

    hoje = pd.Timestamp.now(tz=config.FUSO_MS).date()
    dias = previsao.dias_inteiros(diaria_grade, hoje)
    if not dias:
        st.warning(f"O {openmeteo.NOMES[modelo]} não trouxe nenhum dia inteiro de hoje em diante.")
        st.stop()
    # Trocando de modelo, o dia escolhido pode não existir no outro (o ICON para no dia 7): fica o
    # último que o modelo tem até ele, e não o primeiro, para quem olhava o fim da semana
    escolhido = st.session_state.get("mapas_previstos_dia")
    if escolhido not in dias:
        st.session_state["mapas_previstos_dia"] = max((dia for dia in dias if escolhido and dia <= escolhido),
                                                      default=dias[0])
    dia = st.select_slider("Dia", options=dias, format_func=previsao.nome_do_dia, key="mapas_previstos_dia")
    marcar, ajustar_escala = st.columns([1, 1])
    with marcar:
        rotulos = st.checkbox("Mostrar o valor de cada estação", value=True, key="mapas_previstos_rotulos",
                              help="A previsão do mesmo modelo no ponto de cada estação.")
    with ajustar_escala:
        ajustar = st.checkbox("Ajustar a escala ao dado", value=False, key="mapas_previstos_ajustar",
                              help="Desligada, a escala é a mesma dos mapas do observado: a mesma cor "
                                   "quer dizer o mesmo valor, previsto ou medido.")
    st.caption(f"**{openmeteo.NOMES[modelo]}**, rodada de {rodada:%d/%m %H} UTC. "
               "Previsão: [Open-Meteo.com](https://open-meteo.com/), com dados do ECMWF, da NOAA e do "
               "DWD ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).")

    # Na ordem do catálogo, e todas as linhas com a mesma quantidade de colunas, como no observado
    ordem = [nome for nome in previsao.MAPAS if nome in escolhidos]
    por_linha = min(len(ordem), MAPAS_POR_LINHA)
    with st.spinner("Desenhando os mapas..."):
        for primeiro in range(0, len(ordem), por_linha):
            for coluna_tela, nome in zip(st.columns(por_linha), ordem[primeiro:primeiro + por_linha]):
                with coluna_tela:
                    painel_do_mapa_previsto(previsao.MAPAS[nome], modelo, rodada, dia, hoje, rotulos, ajustar,
                                            diaria_grade, pontos_grade, diaria_estacoes, estacoes, grades[0])
    espacamento = f"{previsao.espacamento(pontos_grade):g}".replace(".", ",")
    st.caption(f"A superfície é a do próprio modelo, nos {len(pontos_grade)} pontos da grade de "
               f"{espacamento}° que cobrem o estado, levada à resolução do mapa por interpolação bilinear: "
               "não é IDW de estação. Os números sobre as estações são a previsão do mesmo modelo no ponto "
               "de cada uma, e são eles que entram no ranking do PNG do boletim.")


def painel_do_mapa_semanal(mapa: previsao.MapaSemanal, rodada: datetime, semana: date, rotulos: bool,
                           semanal_grade: pd.DataFrame, pontos_grade: pd.DataFrame,
                           semanal_estacoes: pd.DataFrame, estacoes: pd.DataFrame, titulo: str) -> None:
    st.markdown(f"**{titulo}**")
    base = base_cartografica(previsao.UF)
    grade = previsao.superficie(previsao.valores_da_semana(semanal_grade, mapa, semana), pontos_grade,
                                base.lon_grade, base.lat_grade)
    if grade is None:
        st.info(f"O EC46 não traz {mapa.nome.lower()} para a semana de {previsao.nome_da_semana(semana)}.")
        return
    pontos = previsao.nas_estacoes_na_semana(semanal_estacoes, estacoes, mapa, semana)
    escala = tuple(float(nivel) for nivel in mapa.niveis)

    png = mapa_previsto(("semanas", rodada, semana), mapa.titulo, mapa.paleta, mapa.unidade, mapa.decimais,
                        False, rotulos, escala, _grade=grade, _estacoes=pontos, com_sinal=True)
    st.image(png, width="stretch")
    st.image(barra_de_escala(mapa.paleta, escala, f"{mapa.unidade} em relação ao normal"), width="stretch")
    no_estado = grade[base.dentro_uf]
    st.caption(f"De {np.nanmin(no_estado):+.{mapa.decimais}f} a {np.nanmax(no_estado):+.{mapa.decimais}f} "
               f"{mapa.unidade} em relação ao normal · {mapa.regra}.")

    carimbo = previsao.carimbo_da_semana(rodada, semana)
    chave = f"{mapa.nome}_{semana:%Y%m%d}"
    st.download_button(f"Baixar PNG — {mapa.titulo.lower()}", png, mime="image/png",
                       key=f"baixar_semana_{chave}",
                       file_name=f"Mapa_{mapa.titulo.replace(' ', '_')}_{carimbo}.png")
    # O ranking é o dos maiores desvios, para cima ou para baixo, em qualquer mapa: o "mínima" do
    # nome, que nos outros mapas pede as menores, aqui não muda o que se procura
    espec = mapas.EspecMapa(tabela="", coluna=mapa.titulo, titulo=boletim.titulo(mapa.titulo, previsao.UF),
                            subtitulo=previsao.subtitulo_da_semana(semana), arquivo="",
                            cmap=mapa.paleta, unidade=f"{mapa.unidade} em relação ao normal",
                            ranking=previsao.RANKING_SEMANAS, decimais=mapa.decimais,
                            credito=previsao.CREDITO, credito_no_rodape=True, ranking_absoluto=True,
                            com_sinal=True)
    gdf = _pontos_previstos(pontos, mapa.titulo)
    botao_do_boletim(mapa.titulo.lower(), f"semana_{chave}",
                     boletim.nome_do_arquivo(mapa.titulo, previsao.UF, carimbo),
                     lambda: mapas.mapa_de_grade(grade, gdf, espec, base, niveis=list(escala)))


def semana_contra_o_normal(tabela: pd.DataFrame, grandeza: str) -> alt.Chart:
    _, _, unidade = previsao.SERIES_SEMANAIS[grandeza]
    chuva = grandeza == "Chuva"
    acima, abaixo = ("#1b7837", "#8c510a") if chuva else ("#d6604d", "#4393c3")
    ordem = list(tabela["rotulo"])
    # "12/10 a 18/10" em duas linhas: numa linha só, metade das semanas sumia do eixo por falta de lugar
    eixo_x = alt.X("rotulo:O", sort=ordem, title=None,
                   axis=alt.Axis(labelAngle=0, labelFontSize=FONTE_DOS_EIXOS, labelOverlap=False,
                                 labelExpr="[split(datum.label, ' a ')[0], 'a ' + split(datum.label, ' a ')[1]]"))
    series = [previsao.PREVISTA, previsao.NORMAL]
    longo = (tabela.melt(id_vars=["rotulo"], value_vars=["prevista", "normal"], var_name="qual", value_name="valor")
             .assign(Série=lambda dados: dados["qual"].map({"prevista": previsao.PREVISTA,
                                                           "normal": previsao.NORMAL})))
    linhas = (alt.Chart(longo).mark_line(point=alt.OverlayMarkDef(filled=True, size=60), strokeWidth=2.5)
              .encode(x=eixo_x,
                      y=alt.Y("valor:Q", title=f"{grandeza} ({unidade})", scale=alt.Scale(zero=chuva),
                              axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS)),
                      color=alt.Color("Série:N", title=None, legend=alt.Legend(orient="bottom"),
                                      scale=alt.Scale(domain=series, range=["#1f77b4", "#9e9e9e"])),
                      strokeDash=alt.StrokeDash("Série:N", legend=None,
                                                scale=alt.Scale(domain=series, range=[[1, 0], [6, 4]])),
                      tooltip=[alt.Tooltip("rotulo:O", title="Semana"), alt.Tooltip("Série:N"),
                               alt.Tooltip("valor:Q", title=unidade, format=".1f")]))
    com_sinal = tabela.assign(lado=np.where(tabela["anomalia"] >= 0, "acima", "abaixo"),
                              texto=tabela["anomalia"].map(lambda valor: f"{valor:+.1f}".replace(".", ",")),
                              meio=(tabela["prevista"] + tabela["normal"]) / 2)
    cor_do_lado = alt.Color("lado:N", legend=None, scale=alt.Scale(domain=["acima", "abaixo"], range=[acima, abaixo]))
    barras = (alt.Chart(com_sinal).mark_rule(strokeWidth=7, opacity=0.5)
              .encode(x=eixo_x, y="normal:Q", y2="prevista:Q", color=cor_do_lado,
                      tooltip=[alt.Tooltip("rotulo:O", title="Semana"),
                               alt.Tooltip("prevista:Q", title=previsao.PREVISTA, format=".1f"),
                               alt.Tooltip("normal:Q", title=previsao.NORMAL, format=".1f"),
                               alt.Tooltip("anomalia:Q", title="Anomalia", format="+.1f")]))
    numeros = (alt.Chart(com_sinal).mark_text(align="left", dx=8, fontSize=FONTE_DOS_ROTULOS,
                                              fontWeight="bold")
               .encode(x=eixo_x, y="meio:Q", text="texto:N", color=cor_do_lado))
    return alt.layer(barras, linhas, numeros).resolve_scale(color="independent").properties(height=320)


def pagina_semanas() -> None:
    st.title("Previsão por semana — Mato Grosso do Sul")
    aviso = st.container()
    # A superfície é do Open-Meteo; os números sobre as estações precisam da lista do INMET
    if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        st.error("Token do INMET não configurado. Preencha `TOKEN_INMET` no arquivo `.env` e recarregue a página.")
        st.stop()

    try:
        estacoes = carregar_estacoes(previsao.UF).sort_values("Estação")
    except fonte.ErroFonte:
        sem_lista_de_estacoes(aviso, previsao.UF)
    with st.sidebar:
        st.header("Filtros")
        nomes = estacoes["Estação"].tolist()
        codigos = estacoes["CD_ESTACAO"].astype(str).tolist()
        if st.session_state.get("semanas_estacao") not in nomes:
            st.session_state["semanas_estacao"] = (nomes[codigos.index(previsao.ESTACAO_INICIAL)]
                                                   if previsao.ESTACAO_INICIAL in codigos else nomes[0])
        nome_estacao = st.selectbox("Estação", nomes, key="semanas_estacao",
                                    help="A do gráfico da semana contra o normal, no topo da página.")
        st.session_state.setdefault("semanas_mapas", list(previsao.PADRAO_SEMANAIS))
        # Um nome que deixou de existir (os mapas foram renomeados em 08/10) seria recusado pelo widget
        st.session_state["semanas_mapas"] = [nome for nome in st.session_state["semanas_mapas"]
                                             if nome in previsao.MAPAS_SEMANAIS] or list(previsao.PADRAO_SEMANAIS)
        escolhidos = st.multiselect("Anomalias", list(previsao.MAPAS_SEMANAIS), key="semanas_mapas",
                                    help="A anomalia de cada grandeza: quanto a semana deve ficar acima ou "
                                         "abaixo do normal.")
    if not escolhidos:
        st.info("Escolha ao menos um mapa na barra lateral.")
        st.stop()

    pontos_grade = fonte.pontos_da_grade_semanal()
    semanal_grade, avisos = semanas_dos_pontos("grade-semanas", pontos_grade)
    semanal_estacoes, avisos_estacoes = semanas_dos_pontos("estações-semanas",
                                                           openmeteo.pontos_das_estacoes(estacoes))
    for texto in avisos + avisos_estacoes:
        aviso.warning(texto)
    if semanal_grade is None:
        st.stop()
    semanal_estacoes = pd.DataFrame() if semanal_estacoes is None else semanal_estacoes
    rodada = previsao.rodada_da(semanal_grade)
    semanas = previsao.semanas_inteiras(semanal_grade, rodada)
    if not semanas:
        st.warning("O EC46 não trouxe nenhuma semana inteira nesta rodada.")
        st.stop()

    codigo_estacao = str(estacoes.set_index("Estação").loc[nome_estacao, "CD_ESTACAO"])
    st.subheader(f"{nome_estacao}: a semana contra o normal")
    colunas_graficos = st.columns(2)
    for coluna_tela, grandeza in zip(colunas_graficos, previsao.SERIES_SEMANAIS):
        with coluna_tela:
            tabela = previsao.semana_contra_o_normal(semanal_estacoes, codigo_estacao, grandeza, rodada)
            if tabela.empty:
                st.info(f"O EC46 não trouxe {grandeza.lower()} para {nome_estacao}.")
            else:
                st.altair_chart(semana_contra_o_normal(tabela, grandeza), width="stretch")
    st.caption("A linha cinza tracejada é a **normal da época**: a do próprio modelo, tirada das reprevisões "
               "do ECMWF dos últimos anos para as mesmas semanas. A azul é a **previsão do EC46** para cada "
               "semana (na chuva, o total da semana). A barra entre as duas é a **anomalia**, o número dos "
               "mapas abaixo: o quanto a semana deve ficar acima ou abaixo do normal.")
    st.divider()

    ordem = [nome for nome in previsao.MAPAS_SEMANAIS if nome in escolhidos]
    ver = st.radio("Ver", ["Uma semana", "Todas as semanas"], horizontal=True, key="semanas_ver",
                   help="Todas as semanas mostra o primeiro mapa escolhido, uma semana em cada mapa: é "
                        "como se vê a tendência mudar.")
    if ver == "Uma semana":
        if st.session_state.get("semanas_semana") not in semanas:
            st.session_state["semanas_semana"] = semanas[0]
        semana = st.select_slider("Semana", options=semanas, format_func=previsao.nome_da_semana,
                                  key="semanas_semana")
        paineis = [(previsao.MAPAS_SEMANAIS[nome], semana, nome) for nome in ordem]
    else:
        mapa = previsao.MAPAS_SEMANAIS[ordem[0]]
        paineis = [(mapa, semana, f"{mapa.nome}, {previsao.nome_da_semana(semana)}") for semana in semanas]
    rotulos = st.checkbox("Mostrar o valor de cada estação", value=True, key="semanas_rotulos",
                          help="A anomalia do EC46 no ponto de cada estação.")
    st.info("**Os números destes mapas são desvios do normal, e não temperaturas ou chuvas.** −2,0 °C quer "
            "dizer uma semana 2 °C mais fria que o normal daquela época; +30 mm, 30 mm a mais de chuva que o "
            "normal da semana.")
    st.caption(f"**EC46**, a previsão estendida do ECMWF, na média dos membros; rodada de {rodada:%d/%m %H} UTC "
               "(sai uma por dia). **Anomalia** é a previsão da semana menos a normal do próprio modelo para a "
               "mesma época, tirada das reprevisões do ECMWF dos últimos anos: branco é o normal; azul, mais "
               "frio, e vermelho, mais quente; marrom, menos chuva, e verde, mais. Dá a tendência da semana, e "
               "não o tempo de um dia. A semana vai de segunda a domingo. Previsão: "
               "[Open-Meteo.com](https://open-meteo.com/), com dados do ECMWF "
               "([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).")

    por_linha = min(len(paineis), MAPAS_POR_LINHA)
    with st.spinner("Desenhando os mapas..."):
        for primeiro in range(0, len(paineis), por_linha):
            for coluna_tela, (mapa, semana, titulo) in zip(st.columns(por_linha),
                                                            paineis[primeiro:primeiro + por_linha]):
                with coluna_tela:
                    painel_do_mapa_semanal(mapa, rodada, semana, rotulos, semanal_grade, pontos_grade,
                                           semanal_estacoes, estacoes, titulo)
    espacamento = f"{previsao.espacamento(pontos_grade):g}".replace(".", ",")
    st.caption(f"A superfície é a do EC46 nos {len(pontos_grade)} pontos da grade de {espacamento}°, levada à "
               "resolução do mapa por interpolação bilinear, como nos mapas dos dias. Os números sobre as "
               "estações são a anomalia no ponto de cada uma, e são eles que entram no ranking do PNG do "
               "boletim.")


# =====================================================
# PÁGINAS
# =====================================================
# =====================================================
# FILTROS EM COMUM DAS PÁGINAS DO OBSERVADO
# =====================================================
def filtros_de_estado_e_periodo() -> tuple[str, config.Recorte, tuple]:
    # O estado vem primeiro porque tudo abaixo depende dele: as estações, o fuso que define o
    # dia, os shapefiles do mapa. Só aparecem as UFs que têm shapefile na pasta shp/.
    ufs = config.ufs_disponiveis()
    st.session_state.setdefault("uf", config.UF if config.UF in ufs else ufs[0])
    uf = st.selectbox("Estado", ufs, key="uf",
                      format_func=lambda sigla: f"{sigla} — {config.ESTADOS[sigla][0]}",
                      help="Para acrescentar um estado, rode "
                           "ferramentas/simplificar_municipios.py --uf SIGLA, que busca a "
                           "malha dele no IBGE.")
    recorte = config.recorte_de(uf)
    hoje = date.today()
    intervalo = st.date_input("Período", max_value=hoje, format="DD/MM/YYYY",
                              value=st.session_state.get("periodo_guardado",
                                                         (hoje - timedelta(days=7), hoje - timedelta(days=1))))
    if len(intervalo) != 2:
        st.info("Escolha a data inicial e a final.")
        st.stop()
    st.session_state["periodo_guardado"] = intervalo
    return uf, recorte, intervalo


def janela_do_periodo(intervalo: tuple, recorte: config.Recorte) -> tuple[datetime, datetime]:
    # O dia é o do estado escolhido: em MS ele começa às 04 h UTC, no Paraná às 03 h
    inicio = datetime.combine(intervalo[0], datetime.min.time(), tzinfo=recorte.fuso).astimezone(config.FUSO_UTC)
    fim = (datetime.combine(intervalo[1], datetime.min.time(), tzinfo=recorte.fuso)
           + timedelta(days=1)).astimezone(config.FUSO_UTC)
    return inicio, fim


# =====================================================
# MAPAS INTERATIVOS (pydeck, sem mapa de fundo da internet)
# =====================================================
@st.cache_resource(show_spinner=False, max_entries=ESTADOS_NA_MEMORIA)
def contornos_interativos(uf: str) -> tuple[dict, dict]:
    base = base_cartografica(uf)
    return base.uf.__geo_interface__, interativo.municipios(base)


@st.cache_resource(show_spinner=False, max_entries=ESTADOS_NA_MEMORIA)
def grade_do_ponto(uf: str) -> interativo.GradeDoPonto:
    return interativo.grade_do_ponto(base_cartografica(uf))


def coordenadas_das_estacoes(uf: str) -> pd.DataFrame:
    cadastro = pd.concat([carregar_estacoes(uf), estacoes_vizinhas(uf, avisar=False)], ignore_index=True)
    return (cadastro.rename(columns={"VL_LATITUDE": "Latitude", "VL_LONGITUDE": "Longitude"})
            [["Estação", "Longitude", "Latitude"]])


@st.cache_data(show_spinner=False, max_entries=30)
def celulas_do_ponto(valores: pd.Series, apoio: pd.Series, nome_produto: str, quando: str, uf: str,
                     direcoes: pd.Series | None = None, nome: str | None = None) -> pd.DataFrame:
    # As mesmas estações da superfície (as do estado e as vizinhas), para o balão bater com a cor.
    # `nome` é o que o balão diz quando não é o nome do produto (na chuva, a janela: "Chuva 24 h")
    produto = variaveis.por_nome(nome_produto)
    nome = nome or nome_produto
    entrada = pd.concat([_com_coordenadas(valores, nome, uf), _com_coordenadas(apoio, nome, uf)], ignore_index=True)
    return interativo.celulas_do_ponto(grade_do_ponto(uf), entrada, nome, produto.unidade, produto.decimais, direcoes)


@st.cache_data(show_spinner=False, max_entries=10)
def celulas_do_imovel(_imovel: car.Imovel, chave: str, uf: str) -> np.ndarray:
    # A chave identifica o imóvel no cache (a forma em si não entra na conta do cache)
    return car.celulas_do_imovel(grade_do_ponto(uf), _imovel.forma)


def visao_do_imovel(imovel: car.Imovel, em_3d: bool) -> dict:
    # Centrado no imóvel, com folga para as células vizinhas. No 3D, não tão perto: os blocos de até
    # 100 km encheriam a tela
    oeste, sul, leste, norte = imovel.forma.bounds
    zoom = ZOOM_POR_GRAU - math.log2(max(leste - oeste, norte - sul, LADO_MINIMO_DO_IMOVEL) * FOLGA_DO_IMOVEL)
    return {"latitude": (sul + norte) / 2, "longitude": (oeste + leste) / 2,
            "zoom": round(min(zoom, ZOOM_DO_IMOVEL_EM_3D) if em_3d else zoom, 1)}


def mapa_interativo(imagem: str | None, pontos: pd.DataFrame, uf: str, escritos: bool,
                    celulas: pd.DataFrame | None = None,
                    escolhido: list[int] = interativo.COR_DO_PONTO_ESCOLHIDO,
                    relevo: pd.DataFrame | None = None, imovel: car.Imovel | None = None) -> pdk.Deck:
    # Com o relevo (o 3D, de interativo.relevo ou relevo_de_classes), os blocos tomam o lugar da
    # imagem, e as estações sobem à altura que vem na coluna "altura" dos pontos. Com o imóvel do
    # CAR, o mapa abre nele, com o contorno e as células que ele toca
    recorte = config.recorte_de(uf)
    estado, municipios = contornos_interativos(uf)
    com_celulas = celulas is not None and not celulas.empty
    em_3d = com_celulas and relevo is not None
    # O quadrado de cada célula sai do centro no navegador, para não mandar quatro cantos por célula
    dx, dy = grade_do_ponto(uf).meio_passo
    quadrado = (f"[[Longitude - {dx:.6f}, Latitude - {dy:.6f}], [Longitude + {dx:.6f}, Latitude - {dy:.6f}], "
                f"[Longitude + {dx:.6f}, Latitude + {dy:.6f}], [Longitude - {dx:.6f}, Latitude + {dy:.6f}]]")
    camadas = []
    if em_3d:
        # Os blocos vão numa GridCellLayer porque, no clique, o Streamlit apaga para 40% as camadas que
        # conhece (PolygonLayer, ColumnLayer...), e o relevo inteiro ficaria translúcido; esta ele deixa
        # como está. A grade que responde ao mouse vai por cima, em colunas de quatro lados com a mesma
        # medida e a mesma altura dos blocos: o balão é o do bloco sob o cursor, e o escolhido fica na
        # cor dele a 50%, como no mapa plano (veja abaixo). O nome é outro que o da grade plana: com o
        # mesmo, ao ligar o 3D, o deck.gl passaria o estado da camada plana à de colunas, e ela quebra.
        # Os limites vêm depois, como sobre a imagem: no chão, por cima do que fica no nível 0
        lado = interativo.lado_do_bloco(grade_do_ponto(uf))
        camadas.append(pdk.Layer(
            "GridCellLayer", data=relevo.loc[relevo["pintada"], interativo.COLUNAS_DO_RELEVO], id="relevo",
            get_position=["Longitude", "Latitude"], cell_size=lado, offset=[0, 0], extruded=True,
            get_elevation="altura", get_fill_color="cor", pickable=False))
        camadas.append(pdk.Layer(
            "ColumnLayer", data=celulas[interativo.COLUNAS_DA_CAMADA].assign(altura=relevo["altura"].to_numpy()),
            id=PONTOS_NO_RELEVO, get_position=["Longitude", "Latitude"], disk_resolution=4, angle=45,
            radius=lado / math.sqrt(2), extruded=True, get_elevation="altura", get_fill_color=[*escolhido, 2],
            opacity=0.5, pickable=True))
    else:
        # A imagem entra depois de criada a camada: passada no construtor, o pydeck a trataria como
        # expressão a ser avaliada no navegador ("@@=data:image/png;...")
        campo = pdk.Layer("BitmapLayer", data=None, bounds=superficie.limites(recorte))
        campo.image = imagem
        camadas.append(campo)
    camadas += [
        # Preenchidos quase transparentes: é o preenchimento que responde ao mouse com o nome. Com a
        # grade do ponto por cima, quem responde é ela, que já traz o município; clicável, o
        # município inteiro ficaria pintado no clique (veja a camada da grade)
        pdk.Layer("GeoJsonLayer", data=municipios, id="municipios", stroked=True, filled=True,
                  get_fill_color=[255, 255, 255, 1], get_line_color=[70, 70, 70, 150],
                  line_width_min_pixels=0.6, pickable=not com_celulas),
        # O contorno do estado com uma borda clara por baixo: no mapa de fundo escuro, a linha preta sumiria
        pdk.Layer("GeoJsonLayer", data=estado, stroked=True, filled=False, get_line_color=[235, 235, 235, 220],
                  line_width_min_pixels=3.5),
        pdk.Layer("GeoJsonLayer", data=estado, stroked=True, filled=False, get_line_color=[25, 25, 25],
                  line_width_min_pixels=1.6),
    ]
    if com_celulas and not em_3d:
        # A grade invisível do valor em qualquer ponto, por cima dos municípios (o balão dela já traz
        # o nome do município) e por baixo das estações, que continuam respondendo ao clique.
        # No clique, o Streamlit pinta o objeto escolhido com a cor dele, mas opaca, e apaga os outros:
        # a célula leva o vermelho quase transparente, e a opacidade da camada deixa o escolhido em 50%
        camadas.append(pdk.Layer(
            "PolygonLayer", data=celulas[interativo.COLUNAS_DA_CAMADA], id="pontos", filled=True,
            stroked=False, get_fill_color=[*escolhido, 2], opacity=0.5, pickable=True, get_polygon=quadrado))
    if imovel is not None and com_celulas:
        # O imóvel do CAR: as células que ele toca em magenta translúcido (no 3D, um pouco acima dos
        # blocos, para o topo delas aparecer) e o contorno dele com uma borda clara por baixo. Nada
        # disso responde ao mouse: o balão continua sendo o da célula. Translúcidas, as células não
        # mudam quando o Streamlit apaga as camadas no clique (ele só baixa o que passa de 40%)
        dele = celulas["celula"].isin(celulas_do_imovel(imovel, imovel.chave, uf)).to_numpy()
        quadrados = celulas.loc[dele, ["Longitude", "Latitude"]].assign(
            altura=relevo["altura"].to_numpy()[dele] + ACIMA_DO_BLOCO if em_3d else 0.0)
        camadas.append(pdk.Layer(
            "PolygonLayer", data=quadrados, id="celulas_do_imovel", get_polygon=quadrado, filled=True,
            stroked=True, extruded=em_3d, wireframe=em_3d, get_elevation="altura",
            get_fill_color=[*COR_DO_IMOVEL, 70], get_line_color=[*COR_DO_IMOVEL, 230], line_width_min_pixels=1.5,
            pickable=False))
        contorno = gpd.GeoDataFrame(geometry=[imovel.forma], crs="EPSG:4326").__geo_interface__
        ponto = imovel.forma.geom_type == "Point"
        camadas += [
            pdk.Layer("GeoJsonLayer", data=contorno, id="imovel_borda", stroked=True, filled=ponto,
                      get_fill_color=[*COR_DO_IMOVEL, 255], get_line_color=[255, 255, 255, 230],
                      line_width_min_pixels=4, point_radius_min_pixels=7, pickable=False),
            pdk.Layer("GeoJsonLayer", data=contorno, id="imovel", stroked=True, filled=False,
                      get_line_color=[*COR_DO_IMOVEL, 255], line_width_min_pixels=2, point_radius_min_pixels=7,
                      pickable=False),
        ]
    # No 3D, as estações e os números ficam no alto e sempre na frente: no chão, sumiriam dentro dos blocos
    posicao, na_frente = ((["Longitude", "Latitude", "altura"], {"parameters": {"depthCompare": "always"}})
                          if em_3d and "altura" in pontos else (["Longitude", "Latitude"], {}))
    camadas += [
        pdk.Layer("ScatterplotLayer", data=pontos, id="estacoes", get_position=posicao,
                  get_fill_color="cor", get_line_color=[25, 25, 25], stroked=True, line_width_min_pixels=1,
                  get_radius=3000, radius_min_pixels=6, radius_max_pixels=14, pickable=True, **na_frente),
    ]
    if escritos:
        camadas.append(pdk.Layer("TextLayer", data=pontos, get_position=posicao,
                                 get_text="rotulo", get_size=12, get_color=[20, 20, 20],
                                 get_pixel_offset=[0, -17], background=True,
                                 get_background_color=[255, 255, 255, 215], **na_frente))
    if "angulo" in pontos:
        # As setas do vento, ao lado de cada estação
        setas = pdk.Layer("TextLayer", data=pontos.dropna(subset=["angulo"]).assign(seta="↑"),
                          get_position=["Longitude", "Latitude"], get_text="seta", get_angle="angulo",
                          get_size=22, get_color=[25, 25, 25], get_pixel_offset=[18, 0])
        # Como a imagem acima: passado no construtor, o "↑" viraria expressão ("@@=↑") e a seta não sairia
        setas.character_set = "↑"
        camadas.append(setas)
    visao = visao_inicial(recorte) if imovel is None else visao_do_imovel(imovel, em_3d)
    visao = pdk.ViewState(**visao, **(VISAO_3D if em_3d else {}))
    if st.session_state.get("fundo_interativo", True):
        # O fundo da Carto, claro ou escuro como o tema (é o que o Streamlit põe com map_style=None):
        # países, cidades, estradas e rios, da internet. Sem rede, só ele some
        return pdk.Deck(layers=camadas, map_style=None, initial_view_state=visao, tooltip=interativo.BALAO)
    return pdk.Deck(layers=camadas, map_provider=None, map_style=interativo.ESTILO_SEM_FUNDO,
                    initial_view_state=visao, tooltip=interativo.BALAO)


def legenda_do_risco(vertical: bool = False) -> str:
    # Do mais alto para o mais baixo; na vertical, uma classe por linha, como numa escala
    item = "display:flex;align-items:center;margin-bottom:0.3rem" if vertical else \
        "display:inline-flex;align-items:center;margin-right:1.1rem"
    itens = "".join(f'<span style="{item}">'
                    f'<span style="width:0.9rem;height:0.9rem;border-radius:2px;margin-right:0.35rem;'
                    f'background:{cor}"></span>{rotulo}</span>'
                    for cor, rotulo in zip(reversed(config.CORES_RISCO), reversed(config.ROTULOS_RISCO)))
    return f'<div style="font-size:{"0.8rem;color:#e0e0e0" if vertical else "0.85rem"}">{itens}</div>'


def escala_no_mapa(chave: str, conteudo: str) -> None:
    # A escala no canto de baixo à direita do mapa interativo, por cima dele e acima dos créditos do
    # mapa de fundo. É um bloco de altura zero logo depois do mapa, com o conteúdo puxado para cima
    # (medido no Streamlit 1.64: o pé da escala fica uns 48 px acima do pé do mapa); não recebe o
    # mouse, para o mapa continuar respondendo embaixo. Na tela cheia do mapa ela não aparece
    with st.container(key=chave):
        st.markdown(
            f"<style>.st-key-{chave} {{position: relative; height: 0; margin-bottom: -1rem; z-index: 3}}"
            f".st-key-{chave} [data-testid='stElementContainer'] {{position: absolute; right: 10px; "
            f"bottom: calc(1rem + 64px); width: auto !important; pointer-events: none}}</style>"
            f"<div style='background: rgba(14, 17, 23, 0.72); border-radius: 6px; padding: 6px 8px'>"
            f"{conteudo}</div>", unsafe_allow_html=True)


def barra_no_mapa(chave: str, paleta: str, niveis: tuple, unidade: str) -> None:
    imagem = base64.b64encode(barra_de_escala(paleta, niveis, unidade, vertical=True)).decode()
    escala_no_mapa(chave, f"<img src='data:image/png;base64,{imagem}' style='height: 300px; display: block'>")


# Os gráficos que abrem no clique dos mapas interativos (a estação ou o ponto) navegam como os das
# séries: arrastar move e Shift+roda aproxima, só no tempo, e o cursor aponta o instante mais
# próximo, com a régua e o balão. Sem o Shift, a roda aproximaria em vez de rolar a página.
def _eixo_do_tempo(campo: str, por_dia: bool = False) -> alt.X:
    # Hora em cima e data embaixo; por dia, só a data, com uma marca por dia
    formato = ("[timeFormat(datum.value, '%d/%m')]" if por_dia
               else "[timeFormat(datum.value, '%H:%M'), timeFormat(datum.value, '%d/%m')]")
    return alt.X(f"{campo}:T", title=None, axis=alt.Axis(
        grid=True, gridOpacity=0.25, labelAngle=0, labelExpr=formato, labelFontSize=FONTE_DOS_EIXOS,
        tickCount="day" if por_dia else alt.Undefined))


def _navegar_e_apontar(campo: str) -> tuple[alt.Parameter, alt.Parameter]:
    return (alt.selection_interval(bind="scales", encodings=["x"], zoom="wheel![event.shiftKey]"),
            alt.selection_point(fields=[campo], nearest=True, on="pointerover", empty=False))


def _regua_do_instante(base: alt.Chart, apontar: alt.Parameter, balao: list) -> alt.Chart:
    return (base.mark_rule(color="#9a9a9a", strokeWidth=1)
            .encode(opacity=alt.condition(apontar, alt.value(0.5), alt.value(0)), tooltip=balao)
            .add_params(apontar))


def _quando_no_balao(campo: str, por_dia: bool = False) -> alt.Tooltip:
    return alt.Tooltip(f"{campo}:T", title="Dia" if por_dia else "Hora",
                       format="%d/%m/%Y" if por_dia else "%d/%m %H:%M")


def graficos_do_risco(serie: pd.DataFrame, hora_local=None) -> list[alt.LayerChart]:
    # Um gráfico por medida, cada um com a sua tela cheia
    cores = alt.Scale(domain=["atendida", "não"], range=[config.CORES_RISCO[-1], "#9e9e9e"])
    graficos = []
    for medida, dela in serie.groupby("Medida", sort=False):
        navegar, apontar = _navegar_e_apontar("dt_local")
        base = alt.Chart(dela).encode(x=_eixo_do_tempo("dt_local"))
        cor = alt.Color("Condição:N", scale=cores, legend=alt.Legend(orient="bottom", title=None))
        camadas = [
            base.mark_line(color="#607d8b", strokeWidth=1.4).encode(
                y=alt.Y("valor:Q", title=medida, scale=alt.Scale(zero=False),
                        axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS)))
            .add_params(navegar),
            base.mark_circle(size=26).encode(y="valor:Q", color=cor),
            # O ponto da hora apontada cresce, para o número do balão ter onde morar no gráfico
            base.mark_circle(size=150).encode(y="valor:Q", color=cor).transform_filter(apontar),
            alt.Chart(dela.head(1)).mark_rule(color=config.CORES_RISCO[-1], strokeDash=[5, 4]).encode(y="limiar:Q"),
            _regua_do_instante(base, apontar, [
                _quando_no_balao("dt_local"), alt.Tooltip("valor:Q", title=medida, format=".1f"),
                alt.Tooltip("limiar:Q", title="Limiar", format=".0f"), alt.Tooltip("Condição:N")]),
        ]
        if hora_local is not None:
            camadas.append(alt.Chart(pd.DataFrame({"agora": [hora_local]})).mark_rule(color="#29b6f6", strokeWidth=2)
                           .encode(x="agora:T"))
        graficos.append(alt.layer(*camadas).properties(height=ALTURA_GRAFICO_DO_RISCO))
    return graficos


def grafico_da_chuva_na_estacao(serie: pd.DataFrame, por_dia: bool) -> alt.LayerChart:
    navegar, apontar = _navegar_e_apontar("quando")
    base = alt.Chart(serie).encode(x=_eixo_do_tempo("quando", por_dia))
    # A barra do instante apontado acende e o acumulado ganha um ponto: o balão diz de que altura fala.
    # As camadas ficam todas no mesmo nível (aninhadas, o Vega recusa o apontador repetido), e com o
    # eixo vertical de cada uma independente: o ponto leva a mesma escala da linha do acumulado
    acumulado = alt.Scale(domain=[0, max(float(serie["acumulado"].max()), 0.1)])
    return alt.layer(
        base.mark_bar(color="#1e88e5").encode(
            y=alt.Y("chuva:Q", title="Chuva (mm)",
                    axis=alt.Axis(orient="left", grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS)),
            opacity=alt.condition(apontar, alt.value(1), alt.value(0.8)))
        .add_params(navegar),
        _regua_do_instante(base, apontar, [
            _quando_no_balao("quando", por_dia), alt.Tooltip("chuva:Q", title="Chuva (mm)", format=".1f"),
            alt.Tooltip("acumulado:Q", title="Acumulado (mm)", format=".1f")]),
        base.mark_line(color="#90a4ae", strokeDash=[5, 3]).encode(
            y=alt.Y("acumulado:Q", title="Acumulado (mm)", scale=acumulado,
                    axis=alt.Axis(orient="right", labelFontSize=FONTE_DOS_EIXOS))),
        base.mark_circle(size=90, color="#cfd8dc").encode(y=alt.Y("acumulado:Q", scale=acumulado, axis=None))
        .transform_filter(apontar),
    ).resolve_scale(y="independent").properties(height=240)


def grafico_da_variavel_na_estacao(serie: pd.DataFrame, produto: variaveis.Produto, por_dia: bool,
                                    momento=None) -> alt.LayerChart:
    titulo = f"{produto.nome} ({produto.unidade})"
    navegar, apontar = _navegar_e_apontar("quando")
    base = alt.Chart(serie).encode(x=_eixo_do_tempo("quando", por_dia))
    camadas = [
        base.mark_line(color="#607d8b", strokeWidth=1.5).encode(
            y=alt.Y("valor:Q", title=titulo, scale=alt.Scale(zero=produto.zero_na_base),
                    axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=FONTE_DOS_EIXOS)))
        .add_params(navegar),
        base.mark_circle(size=24, color="#455a64").encode(y="valor:Q"),
        # O ponto do instante apontado cresce, para o número do balão ter onde morar no gráfico
        base.mark_circle(size=120, color="#cfd8dc").encode(y="valor:Q").transform_filter(apontar),
        _regua_do_instante(base, apontar, [
            _quando_no_balao("quando", por_dia),
            alt.Tooltip("valor:Q", title=titulo, format=f".{produto.decimais}f")]),
    ]
    if momento is not None:
        camadas.append(alt.Chart(pd.DataFrame({"agora": [momento]})).mark_rule(color="#29b6f6", strokeWidth=2)
                       .encode(x="agora:T"))
    return alt.layer(*camadas).properties(height=240)


def variaveis_interativas(uf: str, recorte: config.Recorte, intervalo: tuple, estacoes: pd.DataFrame,
                          inicio: datetime, fim: datetime, imovel: car.Imovel | None = None) -> None:
    if not st.session_state.get("mapa_liberado"):
        st.info(f"O mapa usa as {len(estacoes)} estações do estado, mais as vizinhas para a borda. Na "
                "primeira vez a consulta demora; depois vem do cache.")
        if st.button(f"Carregar todas as estações de {uf}", type="primary", key="liberar_mapas_interativos"):
            st.session_state["mapa_liberado"] = True
            st.rerun()
        return
    leituras, _ = carregar_leituras(tuple(estacoes["CD_ESTACAO"]), tuple(estacoes["Estação"]), inicio, fim, uf)
    vizinhas = estacoes_vizinhas(uf)
    apoio, _ = carregar_leituras(tuple(vizinhas["CD_ESTACAO"]), tuple(vizinhas["Estação"]), inicio, fim, uf,
                                 "Consultando as estações vizinhas")

    modo = MODOS_MAPA[st.radio("Agregação", list(MODOS_MAPA), horizontal=True, key="modo_mapa_interativo")]
    # A chuva tem aba própria: os acumulados dela olham para trás do período
    catalogo = [produto.nome for produto in variaveis.disponiveis(modo, variaveis.MAPA)
                if produto.grandeza != "Chuva" and all(coluna in leituras for coluna in produto.colunas)]
    if not catalogo:
        st.warning("Não veio nenhuma dessas medições no período.")
        return
    padrao = next((nome for nome in variaveis.PADRAO_MAPA[modo] if nome in catalogo), catalogo[0])
    nome = st.selectbox("Mapa", catalogo, index=catalogo.index(padrao), key=f"mapa_interativo_{modo}",
                        help="Um mapa por vez. Cada um traz a sua regra: a máxima do dia é a maior das "
                             "máximas horárias, nunca a média delas.")
    produto = variaveis.por_nome(nome)

    paradas = variaveis.momentos(leituras, modo)
    if modo == variaveis.PERIODO:
        momento, rotulo = None, (f"{intervalo[0]:%d/%m/%Y}" if intervalo[0] == intervalo[1]
                                 else f"{intervalo[0]:%d/%m/%Y} a {intervalo[1]:%d/%m/%Y}")
    elif not paradas:
        st.warning("Sem leituras no período escolhido.")
        return
    else:
        formatar = ((lambda marca: f"{marca:%d/%m/%Y}") if modo == variaveis.DIA
                    else (lambda marca: f"{marca:%d/%m %H:%M}"))
        momento = linha_do_tempo("Quando", paradas, f"quando_mapa_interativo_{modo}", formatar)
        rotulo = formatar(momento)

    marcar, ajustar_tela = st.columns([1, 1])
    with marcar:
        escritos = st.checkbox("Mostrar o valor de cada estação", value=True, key="valores_mapa_interativo",
                               help="O número sobre cada estação. Desligado, fica só o ponto.")
    with ajustar_tela:
        ajustar = st.checkbox("Ajustar a escala ao dado", value=False, key="ajustar_mapa_interativo",
                              help="Desligada, a escala é a do boletim: a mesma cor quer dizer o mesmo valor "
                                   "em qualquer dia.")
    fatia, fatia_apoio = (variaveis.recorte(dados, modo, momento) for dados in (leituras, apoio))
    valores = variaveis.por_estacao(produto, fatia)
    medidas = valores.dropna()
    if len(medidas) < config.MIN_ESTACOES_INTERPOLACAO:
        st.warning(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram {produto.nome.lower()} "
                   f"em {rotulo}.")
        return
    horas_janela = {variaveis.HORA: 1.0, variaveis.DIA: 24.0}.get(modo, (fim - inicio).total_seconds() / 3600)
    niveis = niveis_da_escala(produto, horas_janela, ajustar)
    valores_apoio = variaveis.por_estacao(produto, fatia_apoio)
    imagem = camada_superficie(valores, produto.nome, rotulo, uf, tuple(niveis) if niveis is not None else None,
                               valores_apoio)
    pontos = interativo.pontos_de_valores(_com_coordenadas(valores, produto.nome, uf), produto.nome, produto.nome,
                                          produto.unidade, produto.decimais)
    setas = variaveis.direcoes(produto, fatia)
    if not setas.empty:
        pontos = interativo.com_direcao(pontos, setas)
    rumos = pd.concat([setas, variaveis.direcoes(produto, fatia_apoio)]) if not setas.empty else None
    celulas = celulas_do_ponto(valores, valores_apoio, produto.nome, rotulo, uf, rumos)
    evento = st.pydeck_chart(mapa_interativo(imagem, pontos, uf, escritos, celulas, imovel=imovel),
                             on_select="rerun", selection_mode="single-object",
                             key=chave_do_mapa("mapa_variavel_interativo", imovel=imovel), height=ALTURA_MAPA)
    if niveis is not None:
        barra_no_mapa("escala_mapa_variavel_interativo", produto.paleta, tuple(niveis), produto.unidade)
    casas = produto.decimais
    st.caption(f"{produto.nome}, {rotulo}: de {medidas.min():.{casas}f} a {medidas.max():.{casas}f} "
               f"{produto.unidade} em {len(medidas)} estações · {produto.regra}. A superfície é a do mapa do "
               "boletim (o IDW das estações, com as vizinhas na borda)"
               + ("; a seta aponta para onde o vento vai" if not setas.empty else "")
               + ". **Passe o mouse** em qualquer ponto para ler o valor interpolado, e **clique** para ver a "
                 "série do ponto, ou a de uma estação.")

    # No período inteiro, a série é a do dia: o produto do período é o diário somado no tempo
    modo_serie = variaveis.DIA if modo == variaveis.PERIODO else modo
    marca = None if momento is None else pd.Timestamp(momento).tz_localize(None)
    if imovel is not None:
        painel_do_imovel(produto, celulas, imovel, pd.concat([leituras, apoio], ignore_index=True), uf, modo_serie,
                         marca)
    objetos = evento.selection.get("objects", {}) if evento is not None else {}
    if objetos.get("estacoes"):
        st.session_state["escolha_mapa_interativo"] = ("estacao", objetos["estacoes"][0]["titulo"])
    elif objetos.get("pontos"):
        st.session_state["escolha_mapa_interativo"] = ("ponto", int(objetos["pontos"][0]["celula"]))
    tipo, escolhida = st.session_state.get("escolha_mapa_interativo", (None, None))
    if tipo == "ponto":
        painel_do_ponto(produto, celulas, escolhida, pd.concat([leituras, apoio], ignore_index=True), uf,
                        modo_serie, marca)
    elif tipo == "estacao" and escolhida in set(estacoes["Estação"]):
        serie = (interativo.serie_da_variavel(produto, leituras, escolhida, modo_serie)
                 if modo_serie in produto.modos else pd.DataFrame())
        st.subheader(escolhida)
        if modo_serie not in produto.modos:
            st.info(f"{produto.nome} só existe para o período inteiro: não há série dia a dia.")
        elif serie.empty:
            st.info(f"A estação não mediu {produto.nome.lower()} no período.")
        else:
            st.altair_chart(grafico_da_variavel_na_estacao(serie, produto, modo_serie == variaveis.DIA, marca),
                            width="stretch")
            st.caption(f"{produto.nome} na estação, " + ("dia a dia" if modo_serie == variaveis.DIA else "hora a hora")
                       + " no período." + (" A linha azul é o instante do mapa." if marca is not None else "")
                       + NAVEGAR_NO_GRAFICO)


def ponto_clicado(celulas: pd.DataFrame, celula: int, uf: str) -> tuple[pd.Series, float, float] | None:
    escolhida = celulas[celulas["celula"] == celula]
    if escolhida.empty:
        return None
    grade = grade_do_ponto(uf)
    return escolhida.iloc[0], float(np.ravel(grade.lon)[celula]), float(np.ravel(grade.lat)[celula])


def cabecalho_do_ponto(ponto: pd.Series, lon: float, lat: float, linha_do_valor: str,
                       conta: str = "O valor é o IDW") -> None:
    st.subheader(f"{ponto['titulo']} · {abs(lat):.2f}° S, {abs(lon):.2f}° O".replace(".", ","))
    st.markdown(linha_do_valor)
    st.caption(f"Estação mais próxima: **{ponto['mais_proxima']}**, a {ponto['km']:.0f} km. {conta} das "
               f"{config.IDW_VIZINHOS} estações mais próximas que mediram, com peso pelo inverso do quadrado "
               "da distância: o mesmo que pinta o mapa. Quanto mais longe das estações, menos ele representa o lugar.")


def painel_do_ponto(produto: variaveis.Produto, celulas: pd.DataFrame, celula: int, leituras: pd.DataFrame,
                    uf: str, modo_serie: str, marca) -> None:
    # O ponto clicado: o valor interpolado do instante do mapa (o do balão), a estação mais próxima ao
    # lado, e a série do próprio ponto, com o IDW refeito a cada instante
    clicado = ponto_clicado(celulas, celula, uf)
    if clicado is None:
        return
    ponto, lon, lat = clicado
    valor = f"{ponto['valor']:.{produto.decimais}f}".replace(".", ",")
    rumo = (f", de {ponto['graus']:.0f}° ({interativo.rumo(ponto['graus'])})" if pd.notna(ponto["graus"]) else "")
    cabecalho_do_ponto(ponto, lon, lat, f"**{produto.nome} interpolado:** {valor} {produto.unidade}{rumo}")
    if modo_serie not in produto.modos:
        st.info(f"{produto.nome} só existe para o período inteiro: não há série dia a dia.")
        return
    serie = interativo.serie_do_ponto(produto, leituras, coordenadas_das_estacoes(uf), lon, lat, modo_serie,
                                      grade_do_ponto(uf).centro)
    if serie.empty:
        st.info(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram {produto.nome.lower()} no período.")
        return
    st.altair_chart(grafico_da_variavel_na_estacao(serie, produto, modo_serie == variaveis.DIA, marca),
                    width="stretch")
    st.caption(f"{produto.nome} interpolado no ponto, " + ("dia a dia" if modo_serie == variaveis.DIA else "hora a hora")
               + " no período." + (" A linha azul é o instante do mapa." if marca is not None else "")
                       + NAVEGAR_NO_GRAFICO)


def painel_do_risco_no_ponto(celulas: pd.DataFrame, celula: int, avaliadas: dict, uf: str, legenda: str,
                             fuso, hora_local) -> None:
    # O ponto clicado no risco: o nível do balão e as três medidas do próprio ponto, hora a hora no
    # período, no mesmo gráfico da estação
    clicado = ponto_clicado(celulas, celula, uf)
    if clicado is None:
        return
    ponto, lon, lat = clicado
    nivel = int(ponto["nivel"])
    cabecalho_do_ponto(ponto, lon, lat, f"**{legenda}:** {config.ROTULOS_RISCO[nivel]} ({nivel} de 3)",
                       conta="A regra 30-30-30 é aplicada no ponto, hora a hora, a cada uma das três medidas "
                             "interpoladas pelo IDW")
    serie = interativo.serie_do_risco_no_ponto(avaliadas, lon, lat, grade_do_ponto(uf).centro, fuso)
    if serie.empty:
        return
    for grafico in graficos_do_risco(serie, hora_local):
        st.altair_chart(grafico, width="stretch")
    st.caption(f"As três medidas interpoladas no ponto, hora a hora. A linha tracejada é o limiar de cada condição "
               f"({config.LIMIAR_TEMP_MAX:g} °C, {config.LIMIAR_UMIDADE_MIN:g} % e {config.LIMIAR_RAJADA:g} km/h); "
               "os pontos vermelhos, as horas em que ela valeu." + (" A linha azul é a hora do mapa."
                                                                     if hora_local is not None else "")
               + NAVEGAR_NO_GRAFICO)


@st.cache_data(show_spinner=False, max_entries=30)
def celulas_do_risco(niveis: np.ndarray, estacoes: pd.DataFrame, legenda: str, uf: str, com_medidas: bool,
                     horas_alto: np.ndarray | None = None) -> pd.DataFrame:
    return interativo.celulas_do_risco(grade_do_ponto(uf), niveis, legenda, estacoes, com_medidas, horas_alto)


def painel_da_chuva_no_ponto(celulas: pd.DataFrame, celula: int, leituras: pd.DataFrame, uf: str, legenda: str,
                             quando: str, de, ate, por_dia: bool, acumulado: bool) -> None:
    # O ponto clicado na chuva: o valor do balão e a chuva do próprio ponto na mesma janela da
    # estação, com o IDW refeito a cada hora (ou dia)
    clicado = ponto_clicado(celulas, celula, uf)
    if clicado is None:
        return
    ponto, lon, lat = clicado
    valor = f"{ponto['valor']:.1f}".replace(".", ",")
    cabecalho_do_ponto(ponto, lon, lat, f"**{legenda} interpolada, {quando}:** {valor} mm")
    serie = interativo.serie_da_chuva_no_ponto(leituras, coordenadas_das_estacoes(uf), lon, lat, de, ate, por_dia,
                                               grade_do_ponto(uf).centro)
    if serie.empty:
        st.info(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram a chuva nessa janela.")
        return
    total = f"{serie['acumulado'].iloc[-1]:.1f}".replace(".", ",")
    st.altair_chart(grafico_da_chuva_na_estacao(serie, por_dia), width="stretch")
    st.caption(f"A chuva interpolada no ponto em cada {'dia' if por_dia else 'hora'}, de {de:%d/%m %H:%M} a "
               f"{ate:%d/%m %H:%M}: {total} mm somados. A linha tracejada é o acumulado."
               + (" O mapa interpola o total de cada estação; quando alguma deixou de mandar horas, a soma das "
                  "barras sai um pouco diferente do valor dele." if acumulado else "") + NAVEGAR_NO_GRAFICO)


# O imóvel do CAR (ou o ponto informado) na barra lateral: nos três mapas, o painel dele abre
# embaixo do mapa, com as células que ele toca. Nas variáveis e na chuva, a média das células;
# no risco, o pior nível delas
def celulas_dele(celulas: pd.DataFrame, imovel: car.Imovel, uf: str) -> pd.DataFrame | None:
    dele = celulas[celulas["celula"].isin(celulas_do_imovel(imovel, imovel.chave, uf))]
    if dele.empty:
        st.warning(f"**{imovel.titulo}** não toca nenhuma célula de {uf}: fica fora do estado, ou a latitude e "
                   "a longitude vieram trocadas (a latitude vem primeiro).")
        return None
    return dele


def coordenadas_das_celulas(dele: pd.DataFrame, uf: str) -> tuple[np.ndarray, np.ndarray]:
    grade = grade_do_ponto(uf)
    indices = dele["celula"].to_numpy()
    return np.ravel(grade.lon)[indices], np.ravel(grade.lat)[indices]


def mais_perto_do_centro(dele: pd.DataFrame, imovel: car.Imovel) -> pd.Series:
    lon, lat = imovel.centro
    return dele.iloc[int(np.argmin(np.hypot(dele["Longitude"] - lon, dele["Latitude"] - lat)))]


def cabecalho_do_imovel(imovel: car.Imovel, dele: pd.DataFrame, linha_do_valor: str, uf: str, conta: str) -> None:
    lon, lat = imovel.centro
    perto = mais_perto_do_centro(dele, imovel)
    lado = 2 * grade_do_ponto(uf).meio_passo[1] * interativo.METROS_POR_GRAU / 1000
    coordenadas = f"{abs(lat):.2f}° S, {abs(lon):.2f}° O".replace(".", ",")
    st.subheader(f"{imovel.titulo} · {perto['titulo']} · {coordenadas}")
    st.markdown(linha_do_valor)
    quantas = f"{len(dele)} {'célula' if len(dele) == 1 else 'células'} da grade de ~{lado:.0f} km"
    de_onde = "do ponto" if imovel.ponto else "do centro do imóvel"
    st.caption(" · ".join(parte for parte in (imovel.detalhe, quantas) if parte)
               + f", em magenta no mapa. Estação mais próxima {de_onde}: **{perto['mais_proxima']}**, a "
                 f"{perto['km']:.0f} km. {conta}")


def painel_do_imovel(produto: variaveis.Produto, celulas: pd.DataFrame, imovel: car.Imovel, leituras: pd.DataFrame,
                     uf: str, modo_serie: str, marca) -> None:
    # Nas variáveis: a média das células no instante do mapa e a série da média, com o IDW refeito
    # em cada célula a cada instante
    dele = celulas_dele(celulas, imovel, uf)
    if dele is None:
        return
    cabecalho_do_imovel(imovel, dele, f"**{produto.nome} {imovel.onde}:** "
                                      f"{car.resumo(dele['valor'], produto.decimais, produto.unidade)}", uf,
                        conta="Cada célula leva o IDW das estações, o mesmo que pinta o mapa.")
    if modo_serie not in produto.modos:
        st.info(f"{produto.nome} só existe para o período inteiro: não há série dia a dia.")
        return
    lons, lats = coordenadas_das_celulas(dele, uf)
    serie = interativo.serie_do_ponto(produto, leituras, coordenadas_das_estacoes(uf), lons, lats, modo_serie,
                                      grade_do_ponto(uf).centro)
    if serie.empty:
        st.info(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram {produto.nome.lower()} no período.")
        return
    st.altair_chart(grafico_da_variavel_na_estacao(serie, produto, modo_serie == variaveis.DIA, marca),
                    width="stretch")
    st.caption(f"{produto.nome} {imovel.onde}, " + ("dia a dia" if modo_serie == variaveis.DIA else "hora a hora")
               + " no período" + (f": a média das {len(dele)} células." if len(dele) > 1 else ".")
               + (" A linha azul é o instante do mapa." if marca is not None else "") + NAVEGAR_NO_GRAFICO)


def painel_da_chuva_no_imovel(celulas: pd.DataFrame, imovel: car.Imovel, leituras: pd.DataFrame, uf: str,
                              legenda: str, quando: str, de, ate, por_dia: bool, acumulado: bool) -> None:
    dele = celulas_dele(celulas, imovel, uf)
    if dele is None:
        return
    cabecalho_do_imovel(imovel, dele, f"**{legenda} {imovel.onde}, {quando}:** {car.resumo(dele['valor'], 1, 'mm')}",
                        uf, conta="Cada célula leva o IDW das estações, o mesmo que pinta o mapa.")
    lons, lats = coordenadas_das_celulas(dele, uf)
    serie = interativo.serie_da_chuva_no_ponto(leituras, coordenadas_das_estacoes(uf), lons, lats, de, ate, por_dia,
                                               grade_do_ponto(uf).centro)
    if serie.empty:
        st.info(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram a chuva nessa janela.")
        return
    total = f"{serie['acumulado'].iloc[-1]:.1f}".replace(".", ",")
    st.altair_chart(grafico_da_chuva_na_estacao(serie, por_dia), width="stretch")
    st.caption(f"A chuva {imovel.onde} em cada {'dia' if por_dia else 'hora'}"
               + (f" (a média das {len(dele)} células)" if len(dele) > 1 else "")
               + f", de {de:%d/%m %H:%M} a {ate:%d/%m %H:%M}: {total} mm somados. A linha tracejada é o acumulado."
               + (" O mapa interpola o total de cada estação; quando alguma deixou de mandar horas, a soma das "
                  "barras sai um pouco diferente do valor dele." if acumulado else "") + NAVEGAR_NO_GRAFICO)


def painel_do_risco_no_imovel(celulas: pd.DataFrame, imovel: car.Imovel, avaliadas: dict, uf: str, legenda: str,
                              fuso, hora_local) -> None:
    # No risco, o imóvel leva o pior nível das células dele, e as três medidas são as da célula de
    # pior nível (entre as de mesmo nível, a mais perto do centro do imóvel)
    dele = celulas_dele(celulas, imovel, uf)
    if dele is None:
        return
    pior = int(dele["nivel"].max())
    com_o_pior = dele[dele["nivel"] == pior]
    escolhida = mais_perto_do_centro(com_o_pior, imovel)
    quantas = ("" if len(dele) == 1 else f", nas {len(dele)} células" if len(com_o_pior) == len(dele)
               else f", em {len(com_o_pior)} das {len(dele)} células")
    nivel = f"{config.ROTULOS_RISCO[pior]} ({pior} de 3){quantas}"
    cabecalho_do_imovel(imovel, dele, f"**{imovel.onde.capitalize()}, {legenda.lower()}:** {nivel}", uf,
                        conta="A regra 30-30-30 é aplicada célula a célula, às três medidas interpoladas pelo IDW"
                              + ("." if imovel.ponto else "; o imóvel leva o pior nível das células dele."))
    grade = grade_do_ponto(uf)
    serie = interativo.serie_do_risco_no_ponto(avaliadas, float(np.ravel(grade.lon)[escolhida["celula"]]),
                                               float(np.ravel(grade.lat)[escolhida["celula"]]), grade.centro, fuso)
    if serie.empty:
        return
    for grafico in graficos_do_risco(serie, hora_local):
        st.altair_chart(grafico, width="stretch")
    st.caption("As três medidas interpoladas hora a hora "
               + ("na célula do ponto" if imovel.ponto else "na célula de pior nível do imóvel")
               + (", a mais perto do centro dele entre as de mesmo nível" if len(com_o_pior) > 1 else "")
               + f". A linha tracejada é o limiar de cada condição ({config.LIMIAR_TEMP_MAX:g} °C, "
                 f"{config.LIMIAR_UMIDADE_MIN:g} % e {config.LIMIAR_RAJADA:g} km/h); os pontos vermelhos, as horas "
                 "em que ela valeu." + (" A linha azul é a hora do mapa." if hora_local is not None else "")
               + NAVEGAR_NO_GRAFICO)


@st.cache_data(show_spinner="Lendo o arquivo do imóvel", max_entries=3)
def camadas_do_car(arquivos: tuple[tuple[str, bytes], ...]) -> tuple[dict[str, tuple[gpd.GeoDataFrame, list[str]]],
                                                                     list[str]]:
    # As camadas com desenho, cada uma com os rótulos dos imóveis, e o que ficou de fora, com o porquê
    fontes, de_fora = car.fontes(list(arquivos))
    lidas = {}
    for nome, fonte in fontes.items():
        try:
            tabela = car.ler(fonte)
        except car.ErroDoArquivo as erro:
            de_fora.append(f"{nome}: {erro}")
            continue
        if tabela.empty:
            de_fora.append(f"{nome}: sem nenhum desenho")
            continue
        lidas[nome] = (tabela, car.rotulos(tabela))
    return lidas, de_fora


def imovel_da_barra() -> car.Imovel | None:
    # Na barra lateral: as coordenadas de um ponto ou os arquivos do imóvel (um imóvel por vez), com
    # o tipo de cada arquivo reconhecido pelo conteúdo. O arquivo fica só na memória: não é gravado
    entrada = st.radio("Imóvel do CAR", ["Nenhum", "Coordenadas", "Arquivos"], horizontal=True,
                       key="imovel_entrada",
                       help="Para ver as condições num imóvel: nos três mapas aparecem o contorno dele e as células "
                            "da grade que ele toca, e o painel do imóvel abre embaixo do mapa. O arquivo fica só "
                            "na memória: não é gravado.")
    imovel = None
    if entrada == "Arquivos":
        # Sem filtro de extensão: o tipo sai do conteúdo, e o que não for mapa fica de fora com aviso
        arquivos = st.file_uploader("Arquivos do imóvel", accept_multiple_files=True, key="imovel_arquivos",
                                    help="Shapefile (o .zip do SICAR, ou o .shp com o .dbf, o .shx e o .prj), "
                                         "GeoJSON, GeoPackage, KML ou KMZ, soltos ou num .zip. O tipo de cada um é "
                                         "reconhecido pelo conteúdo.")
        if arquivos:
            try:
                lidas, de_fora = camadas_do_car(tuple((arquivo.name, arquivo.getvalue()) for arquivo in arquivos))
            except car.ErroDoArquivo as erro:
                st.error(str(erro))
                lidas, de_fora = {}, []
            if de_fora:
                st.caption("Ficou de fora: " + "; ".join(de_fora) + ".")
            if not lidas:
                st.error("Nenhum mapa com desenho no que veio. Valem shapefile (com o .shp, o .shx e o .dbf), "
                         "GeoJSON, GeoPackage, KML e KMZ.")
            else:
                nomes = sorted(lidas)
                if len(nomes) == 1:
                    camada = nomes[0]
                    st.caption(f"Reconhecido: **{camada}**.")
                else:
                    camada = st.selectbox(
                        "Camada", nomes, index=nomes.index(car.camada_padrao(nomes)), key="imovel_camada",
                        help="Uma por arquivo (o GeoPackage e o KML podem ter várias), com o tipo reconhecido entre "
                             "parênteses. Do SICAR, a da área do imóvel vem primeiro.")
                tabela, rotulos = lidas[camada]
                posicao = 0 if len(tabela) == 1 else st.selectbox(
                    f"Imóvel ({len(tabela)} no arquivo)", range(len(tabela)), format_func=rotulos.__getitem__,
                    key=f"imovel_escolhido_{camada}", help="Digite parte do código do CAR para procurar.")
                imovel = car.imovel_da_linha(tabela, posicao)
    elif entrada == "Coordenadas":
        texto = st.text_input("Latitude e longitude", key="imovel_coordenadas", placeholder="-20.4697, -54.6201",
                              help="A latitude primeiro, como sai do Google Maps, com ponto ou vírgula decimal; ou "
                                   "em graus, minutos e segundos: 20°28'11\" S 54°37'12\" O.")
        if texto.strip():
            try:
                imovel = car.imovel_do_ponto(*car.ler_coordenadas(texto))
            except ValueError as erro:
                st.error(str(erro))
    # Imóvel novo: a escolha de antes (o ponto ou a estação clicada) sai, e o painel dele fica sozinho
    chave = imovel.chave if imovel is not None else None
    if chave != st.session_state.get("imovel_chave"):
        for nome in ESCOLHAS_INTERATIVAS:
            st.session_state.pop(nome, None)
        st.session_state["imovel_chave"] = chave
    return imovel


def chave_do_mapa(nome: str, em_3d: bool = False, imovel: car.Imovel | None = None) -> str:
    # Uma chave para cada modo e para cada imóvel: depois que a pessoa mexe no mapa, o Streamlit
    # guarda a câmera, e sem a chave nova o mapa não inclinaria, não voltaria a ficar plano nem
    # abriria no imóvel novo
    return nome + ("_3d" if em_3d else "") + (f"_{imovel.chave}" if imovel is not None else "")


def chuva_interativa(uf: str, recorte: config.Recorte, estacoes: pd.DataFrame, inicio: datetime,
                     fim: datetime, imovel: car.Imovel | None = None) -> None:
    horas_do_periodo = pd.date_range(inicio.astimezone(recorte.fuso) + pd.Timedelta(hours=1),
                                     fim.astimezone(recorte.fuso), freq="h")
    # Uma consulta só, que fecha o mensal de qualquer instante do período
    desde = chuva_calc.inicio_necessario(horas_do_periodo[0])
    if not st.session_state.get("chuva_liberada"):
        st.info(f"Os acumulados olham para trás: para fechar o mensal, a consulta vai até **{desde:%d/%m}**. "
                f"São as {len(estacoes)} estações do estado, mais as vizinhas para a borda. Na primeira vez "
                "demora; depois vem do cache.")
        if st.button("Carregar a chuva do mês", type="primary", key="liberar_chuva_interativa"):
            st.session_state["chuva_liberada"] = True
            st.rerun()
        return
    leituras, _ = carregar_leituras(tuple(estacoes["CD_ESTACAO"]), tuple(estacoes["Estação"]),
                                    desde.astimezone(config.FUSO_UTC), fim, uf,
                                    f"Consultando a chuva desde {desde:%d/%m}")
    vizinhas = estacoes_vizinhas(uf)
    apoio, _ = carregar_leituras(tuple(vizinhas["CD_ESTACAO"]), tuple(vizinhas["Estação"]),
                                 desde.astimezone(config.FUSO_UTC), fim, uf,
                                 "Consultando a chuva das estações vizinhas")
    if leituras.empty or chuva_calc.COLUNA not in leituras:
        st.warning("Não veio chuva de nenhuma estação no período.")
        return

    modo = st.radio("Mapa", ["Acumulado", "Hora a hora", "Por dia"], horizontal=True, key="modo_chuva_interativa")
    if modo == "Acumulado":
        janela_tela, ate_tela = st.columns([1, 3])
        with janela_tela:
            rotulos_janela = chuva_calc.rotulos()
            rotulo = st.selectbox("Janela", rotulos_janela, index=rotulos_janela.index("24 h"),
                                  key="janela_chuva_interativa", help="O mensal começa no dia 1º.")
        with ate_tela:
            ate = st.select_slider("Acumulado até", options=list(horas_do_periodo), value=horas_do_periodo[-1],
                                   format_func=lambda marca: f"{marca:%d/%m %H:%M}", key="ate_chuva_interativa",
                                   help="A janela conta para trás a partir deste instante.")
        if not chuva_calc.cobre(leituras, ate, rotulo):
            st.warning(f"Não há dado que alcance o começo da janela de {rotulo}: o acumulado mostraria menos "
                       "chuva do que caiu.")
            return
        produto = variaveis.por_nome("Chuva acumulada")
        valores, valores_apoio = (chuva_calc.acumulado(dados, ate, rotulo) for dados in (leituras, apoio))
        horas = chuva_calc.horas_da_janela(ate, rotulo)
        comeco, _ = chuva_calc.janela(ate, rotulo)
        quando, legenda = f"{comeco:%d/%m %H:%M} a {ate:%d/%m %H:%M}", f"Chuva {rotulo}"
        serie_de, serie_ate = comeco, ate
    else:
        tipo = variaveis.HORA if modo == "Hora a hora" else variaveis.DIA
        inicio_local = inicio.astimezone(recorte.fuso)
        do_periodo, apoio_periodo = (dados[dados["dt_local"] > inicio_local] for dados in (leituras, apoio))
        paradas = variaveis.momentos(do_periodo, tipo)
        if not paradas:
            st.warning("Sem leituras no período escolhido.")
            return
        formatar = ((lambda marca: f"{marca:%d/%m/%Y}") if tipo == variaveis.DIA
                    else (lambda marca: f"{marca:%d/%m %H:%M}"))
        momento = st.select_slider("Quando", options=paradas, value=paradas[-1], format_func=formatar,
                                   key=f"quando_chuva_interativa_{tipo}")
        produto = variaveis.por_nome("Chuva na hora" if tipo == variaveis.HORA else "Chuva acumulada")
        valores, valores_apoio = (variaveis.por_estacao(produto, variaveis.recorte(dados, tipo, momento))
                                  for dados in (do_periodo, apoio_periodo))
        horas = 1.0 if tipo == variaveis.HORA else 24.0
        quando = formatar(momento)
        legenda = "Chuva na hora" if tipo == variaveis.HORA else "Chuva do dia"
        serie_de, serie_ate = inicio_local, fim.astimezone(recorte.fuso)

    marcar, ajustar_tela, relevo_tela = st.columns([1, 1, 1])
    with marcar:
        escritos = st.checkbox("Mostrar o valor de cada estação", value=True, key="valores_chuva_interativa",
                               help="O número sobre cada estação. Desligado, fica só o ponto.")
    with ajustar_tela:
        ajustar = st.checkbox("Ajustar a escala ao dado", value=False, key="ajustar_chuva_interativa",
                              help="Desligada, as classes são as do boletim: 20 mm têm a mesma cor em "
                                   "qualquer mapa.")
    with relevo_tela:
        em_3d = st.checkbox("Relevo 3D", value=False, key="relevo_chuva_interativa",
                            help="A chuva vira altura: o topo da escala sobe 100 km, e o mapa abre inclinado. "
                                 "Arraste com o botão direito para inclinar e girar.")
    medidas = valores.dropna()
    if len(medidas) < config.MIN_ESTACOES_INTERPOLACAO:
        st.warning(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram a chuva em {quando}.")
        return
    niveis = niveis_da_escala(produto, horas, ajustar)
    niveis = tuple(niveis) if niveis is not None else None
    pontos = interativo.pontos_de_valores(_com_coordenadas(valores, produto.nome, uf), produto.nome, legenda,
                                          produto.unidade, produto.decimais)
    celulas = celulas_do_ponto(valores, valores_apoio, produto.nome, quando, uf, nome=legenda)
    if em_3d:
        por_unidade = interativo.metros_por_unidade(niveis, medidas)
        imagem, relevo = None, interativo.relevo(celulas, produto.paleta, niveis, medidas, por_unidade)
        pontos = pontos.assign(altura=pontos["Estação"].map(valores).fillna(0).to_numpy(dtype=float) * por_unidade)
    else:
        imagem, relevo = camada_superficie(valores, produto.nome, quando, uf, niveis, valores_apoio), None
    evento = st.pydeck_chart(mapa_interativo(imagem, pontos, uf, escritos, celulas, relevo=relevo, imovel=imovel),
                             on_select="rerun", selection_mode="single-object",
                             key=chave_do_mapa("mapa_chuva_interativa", em_3d, imovel), height=ALTURA_MAPA)
    if niveis is not None:
        barra_no_mapa("escala_mapa_chuva_interativa", produto.paleta, niveis, produto.unidade)
    if medidas.max() == 0:
        st.info(f"Não choveu em nenhuma estação em {quando}.")
    st.caption(f"{legenda}, {quando}: de {medidas.min():.1f} a {medidas.max():.1f} mm em {len(medidas)} estações. "
               "A superfície é a do mapa do boletim (o IDW das estações, com as vizinhas na borda), e abaixo "
               "da primeira classe não se pinta. **Passe o mouse** em qualquer ponto para ler a chuva interpolada, "
               "e **clique** para ver a chuva do ponto, ou a de uma estação."
               + (f" No relevo, {interativo.ALTURA_DO_TETO / por_unidade:g} mm sobem 100 km; **arraste com o "
                  "botão direito** para inclinar e girar." if em_3d and por_unidade else ""))

    objetos = evento.selection.get("objects", {}) if evento is not None else {}
    celula_clicada = objetos.get("pontos") or objetos.get(PONTOS_NO_RELEVO)
    if objetos.get("estacoes"):
        st.session_state["escolha_chuva_interativa"] = ("estacao", objetos["estacoes"][0]["titulo"])
    elif celula_clicada:
        st.session_state["escolha_chuva_interativa"] = ("ponto", int(celula_clicada[0]["celula"]))
    tipo, escolhida = st.session_state.get("escolha_chuva_interativa", (None, None))
    # Hora a hora até 4 dias; mais que isso, por dia, para as barras caberem
    por_dia = (serie_ate - serie_de) > timedelta(days=4)
    if imovel is not None:
        painel_da_chuva_no_imovel(celulas, imovel, pd.concat([leituras, apoio], ignore_index=True), uf, legenda,
                                  quando, serie_de, serie_ate, por_dia, modo == "Acumulado")
    if tipo == "ponto":
        painel_da_chuva_no_ponto(celulas, escolhida, pd.concat([leituras, apoio], ignore_index=True), uf, legenda,
                                 quando, serie_de, serie_ate, por_dia, modo == "Acumulado")
    elif tipo == "estacao" and escolhida in set(estacoes["Estação"]):
        serie = interativo.serie_da_chuva(leituras, escolhida, serie_de, serie_ate, por_dia)
        total = serie["chuva"].sum(min_count=1)
        st.subheader(f"{escolhida} — " + (f"{total:.1f} mm".replace(".", ",") if pd.notna(total) else "sem dado")
                     + f" de {serie_de:%d/%m %H:%M} a {serie_ate:%d/%m %H:%M}")
        if serie.empty or pd.isna(total):
            st.info("A estação não registrou chuva nessa janela.")
        else:
            st.altair_chart(grafico_da_chuva_na_estacao(serie, por_dia), width="stretch")
            st.caption("As barras são a chuva de cada " + ("dia" if por_dia else "hora")
                       + "; a linha tracejada, o acumulado ao longo da janela." + NAVEGAR_NO_GRAFICO)


def risco_interativo(uf: str, recorte: config.Recorte, intervalo: tuple, estacoes: pd.DataFrame,
                     inicio: datetime, fim: datetime, imovel: car.Imovel | None = None) -> None:
    if not st.session_state.get("mapa_liberado"):
        st.info(f"O mapa usa as {len(estacoes)} estações do estado, mais as vizinhas para a borda. Na "
                "primeira vez a consulta demora; depois vem do cache.")
        if st.button(f"Carregar todas as estações de {uf}", type="primary", key="liberar_interativo"):
            st.session_state["mapa_liberado"] = True
            st.rerun()
        return
    leituras, _ = carregar_leituras(tuple(estacoes["CD_ESTACAO"]), tuple(estacoes["Estação"]), inicio, fim, uf)
    vizinhas = estacoes_vizinhas(uf)
    apoio, _ = carregar_leituras(tuple(vizinhas["CD_ESTACAO"]), tuple(vizinhas["Estação"]), inicio, fim, uf,
                                 "Consultando as estações vizinhas")
    completas, avaliadas = risco_avaliado(pd.concat([leituras, apoio], ignore_index=True),
                                          pd.concat([estacoes, vizinhas], ignore_index=True), uf)
    do_produto = risco.da_uf(completas, uf)
    nomes_do_estado = {estacao["Estação"] for estacao, _ in do_produto}
    if not completas:
        st.warning("Nenhuma estação tem temperatura, umidade e rajada no período: sem as três "
                   "não dá para dizer quantas condições valeram.")
        return
    if not avaliadas:
        st.warning(f"Nenhuma hora teve {config.MIN_ESTACOES_INTERPOLACAO} estações com as três "
                   "medidas: com tão poucos pontos a superfície inventaria mais do que mostra.")
        return

    modo = MODOS_MAPA[st.radio("Agregação", list(MODOS_MAPA), horizontal=True, key="modo_risco_interativo")]
    hora_local = None
    if modo == variaveis.HORA:
        hora = linha_do_tempo("Quando", list(avaliadas), "quando_risco_interativo",
                              lambda marca: f"{marca.tz_convert(recorte.fuso):%d/%m %H:%M}",
                              dia_de=lambda marca: marca.tz_convert(recorte.fuso).date())
        grade, pontos = avaliadas[hora].grade, avaliadas[hora].estacoes
        coluna, legenda = risco_fogo.COLUNA_NIVEL_HORA, "Na hora"
        hora_local = hora.tz_convert(recorte.fuso).tz_localize(None)
        das_celulas, horas_alto = avaliadas[hora].estacoes, None
    elif modo == variaveis.DIA:
        dias = risco.por_dia(avaliadas)
        dia = linha_do_tempo("Dia", list(dias), "dia_risco_interativo", lambda data: f"{data:%d/%m/%Y}")
        grade, pontos = dias[dia], risco.estacoes_do_dia(avaliadas, dia)
        coluna, legenda = risco_fogo.COLUNA_NIVEL_HORA, "Pior nível do dia"
        horas_alto = risco.horas_em_risco_alto(risco.do_dia(avaliadas, dia))
    else:
        grade = risco.pior_nivel(avaliadas)
        pontos = risco.resumo(do_produto, config.Periodo.de_datas(*intervalo, fuso=recorte.fuso), uf)
        coluna, legenda = risco_fogo.COLUNA_NIVEL, "Pior nível do período"
        horas_alto = risco.horas_em_risco_alto(avaliadas)
    if modo != variaveis.HORA:
        das_celulas = pd.concat([avaliada.estacoes for avaliada in avaliadas.values()]).drop_duplicates("Estação")
    # As vizinhas já entraram na superfície; nos pontos, só as do estado
    do_estado = pontos[pontos["Estação"].isin(nomes_do_estado)]
    nivel_da_estacao = do_estado.drop_duplicates("Estação").set_index("Estação")[coluna]
    pontos = interativo.pontos_de_risco(do_estado, coluna, legenda)

    mostrar, relevo_tela = st.columns([1, 1])
    with mostrar:
        escritos = st.checkbox("Mostrar o nível de cada estação", value=True, key="niveis_risco_interativo",
                               help="O número sobre cada estação. Desligado, fica só a cor do ponto.")
    with relevo_tela:
        em_3d = st.checkbox("Relevo 3D", value=False, key="relevo_risco_interativo",
                            help="O nível vira altura: cada nível sobe um degrau, e o risco alto chega a 100 km; "
                                 "o mapa abre inclinado. Arraste com o botão direito para inclinar e girar.")
    com_medidas = modo == variaveis.HORA
    colunas = ["Estação", "Longitude", "Latitude"] + (list(interativo.MEDIDAS_DO_RISCO) if com_medidas else [])
    celulas = celulas_do_risco(grade, das_celulas[colunas].reset_index(drop=True), legenda, uf, com_medidas,
                               horas_alto)
    if em_3d:
        imagem, relevo = None, interativo.relevo_de_classes(celulas)
        pontos = pontos.assign(altura=pontos["Estação"].map(nivel_da_estacao).to_numpy(dtype=float)
                               * interativo.metros_por_nivel())
    else:
        imagem, relevo = interativo.imagem_de_classes(grade, malha_fina(uf).dentro), None
    mapa = mapa_interativo(imagem, pontos, uf, escritos, celulas, interativo.COR_DO_PONTO_ESCOLHIDO_NO_RISCO,
                           relevo, imovel)
    evento = st.pydeck_chart(mapa, on_select="rerun", selection_mode="single-object",
                             key=chave_do_mapa("mapa_risco_interativo", em_3d, imovel), height=ALTURA_MAPA)
    escala_no_mapa("escala_mapa_risco_interativo", legenda_do_risco(vertical=True))
    st.caption("Role para aproximar, arraste para deslocar, passe o mouse em qualquer ponto para ver o nível"
               + (" e as três medidas interpoladas" if com_medidas else " e as horas em risco alto")
               + ", e **clique** para ver as três medidas do ponto, ou as de uma estação, no período. A superfície "
               "é a mesma do mapa do boletim: as três variáveis interpoladas separadas e a regra aplicada célula a "
               "célula."
               + (f" No relevo, cada nível sobe {interativo.metros_por_nivel() / 1000:.0f} km; **arraste com o "
                  "botão direito** para inclinar e girar." if em_3d else ""))

    if imovel is not None:
        painel_do_risco_no_imovel(celulas, imovel, avaliadas, uf, legenda, recorte.fuso, hora_local)
    # O ponto ou a estação clicada fica escolhida enquanto o deslizante anda, até outro clique
    objetos = evento.selection.get("objects", {}) if evento is not None else {}
    celula_clicada = objetos.get("pontos") or objetos.get(PONTOS_NO_RELEVO)
    if objetos.get("estacoes"):
        st.session_state["escolha_risco_interativo"] = ("estacao", objetos["estacoes"][0]["titulo"])
    elif celula_clicada:
        st.session_state["escolha_risco_interativo"] = ("ponto", int(celula_clicada[0]["celula"]))
    tipo, escolhida = st.session_state.get("escolha_risco_interativo", (None, None))
    if tipo == "ponto":
        painel_do_risco_no_ponto(celulas, escolhida, avaliadas, uf, legenda, recorte.fuso, hora_local)
    elif tipo == "estacao" and escolhida in nomes_do_estado:
        st.subheader(escolhida)
        serie = interativo.serie_da_estacao(completas, escolhida, recorte.fuso)
        for grafico in graficos_do_risco(serie, hora_local):
            st.altair_chart(grafico, width="stretch")
        st.caption(f"A linha tracejada é o limiar de cada condição ({config.LIMIAR_TEMP_MAX:g} °C, "
                   f"{config.LIMIAR_UMIDADE_MIN:g} % e {config.LIMIAR_RAJADA:g} km/h); os pontos vermelhos, "
                   "as horas em que ela valeu." + (" A linha azul é a hora do mapa." if hora_local is not None
                                                   else "") + NAVEGAR_NO_GRAFICO)


def pagina_mapas_interativos() -> None:
    st.title("Mapas interativos")
    aviso = st.container()
    if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        st.error("Token do INMET não configurado. Preencha `TOKEN_INMET` no arquivo `.env` e recarregue a página.")
        st.stop()
    with st.sidebar:
        st.header("Filtros")
        uf, recorte, intervalo = filtros_de_estado_e_periodo()
        try:
            estacoes = carregar_estacoes(uf).sort_values("Estação")
        except fonte.ErroFonte:
            sem_lista_de_estacoes(aviso, uf)
        st.checkbox("Mapa de fundo", value=True, key="fundo_interativo",
                    help="Países, cidades, estradas e rios, para ver além do estado. Vem da internet: sem rede, o "
                         "fundo some e o resto do mapa continua. Desligado, ficam só os limites do estado e dos "
                         "municípios.")
        imovel = imovel_da_barra()
    inicio, fim = janela_do_periodo(intervalo, recorte)
    st.caption("Os mapas do boletim continuam no Painel. Aqui eles se aproximam, se arrastam e respondem "
               "ao clique. O mapa de fundo vem da internet e pode ser desligado na barra lateral; o resto é "
               "desenhado aqui, com os limites do estado e dos municípios.")
    aba_mapas_interativos, aba_chuva_interativa, aba_risco_interativo = st.tabs(["Mapas", "Chuva", "Risco de Fogo"])
    with aba_mapas_interativos:
        variaveis_interativas(uf, recorte, intervalo, estacoes, inicio, fim, imovel)
    with aba_chuva_interativa:
        chuva_interativa(uf, recorte, estacoes, inicio, fim, imovel)
    with aba_risco_interativo:
        risco_interativo(uf, recorte, intervalo, estacoes, inicio, fim, imovel)


@st.cache_data(ttl=3600, show_spinner=False)
def tabela_das_estacoes(uf: str) -> pd.DataFrame:
    # As estações do estado, numeradas, com o município, a altitude e desde quando operam.
    return referencia.tabela_de_referencia(carregar_estacoes(uf), base_cartografica(uf).municipios)


def mapa_das_estacoes(tabela: pd.DataFrame, destacadas: pd.Series, uf: str) -> pdk.Deck:
    # O mapa navegável com cada estação e o nome dela ao lado; as que a busca achou, em vermelho.
    base = base_cartografica(uf)
    pontos = pd.DataFrame({
        "nome": tabela["Estação"], "municipio": tabela["Município"].fillna("—"), "codigo": tabela["Código"],
        "altitude": tabela["Altitude (m)"].map(lambda metros: "—" if pd.isna(metros) else f"{metros} m"),
        "desde": tabela["Em operação desde"].fillna("—"),
        "Longitude": tabela["Longitude"], "Latitude": tabela["Latitude"],
        "cor": [[200, 30, 30, 235] if destacada else [31, 78, 121, 225] for destacada in destacadas],
    })
    nomes = pdk.Layer("TextLayer", data=pontos, get_position=["Longitude", "Latitude"], get_text="nome",
                      get_size=12, get_color=[20, 20, 20], get_pixel_offset=[9, 0],
                      background=True, get_background_color=[255, 255, 255, 200])
    # Como a imagem dos mapas: passados no construtor, o pydeck trataria os textos como expressões
    nomes.get_text_anchor = "start"
    nomes.get_alignment_baseline = "center"
    nomes.font_family = "'Source Sans Pro', Arial, sans-serif"   # a do painel, e não a de máquina de escrever
    camadas = [
        pdk.Layer("GeoJsonLayer", data=base.municipios[[referencia.COLUNA_DO_MUNICIPIO, "geometry"]].__geo_interface__,
                  stroked=True, filled=False, get_line_color=[110, 110, 110, 140], line_width_min_pixels=0.5),
        pdk.Layer("GeoJsonLayer", data=base.uf.__geo_interface__, stroked=True, filled=False,
                  get_line_color=[40, 40, 40], line_width_min_pixels=1.5),
        pdk.Layer("ScatterplotLayer", data=pontos, get_position=["Longitude", "Latitude"], get_fill_color="cor",
                  get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=1, get_radius=2500,
                  radius_min_pixels=5, pickable=True),
        nomes,
    ]
    # O mapa ocupa três quintos da largura: com o zoom do mapa largo, o leste do estado ficaria de fora
    visao = visao_inicial(config.recorte_de(uf))
    visao["zoom"] = round(visao["zoom"] - 0.7, 1)
    # O fundo claro da Carto, como era o do Mapa Navegação: os nomes escuros se leem nele em qualquer tema
    return pdk.Deck(layers=camadas, map_style=pdk.map_styles.LIGHT, initial_view_state=pdk.ViewState(**visao),
                    tooltip={"html": "<b>{nome}</b><br/>Município: {municipio}<br/>Código: {codigo}<br/>"
                                     "Altitude: {altitude}<br/>Em operação desde: {desde}"})


def pagina_estacoes() -> None:
    # Onde fica cada estação do INMET em MS: o mapa, a lista com busca e o PNG de referência.
    # Os mapas das variáveis mostram números em pontos. Para o meteorologista é óbvio de quem é cada
    # ponto; para o público, não (pedido de uma colega da equipe, 22/10/2026). Só as do estado, e só
    # as em operação, que são as dos mapas.
    uf = config.UF
    st.title("Estações meteorológicas automáticas - INMET+SEMADESC")
    aviso = st.container()
    if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        st.error("Token do INMET não configurado. Preencha `TOKEN_INMET` no arquivo `.env` e recarregue a página.")
        st.stop()
    try:
        tabela = tabela_das_estacoes(uf)
    except fonte.ErroFonte:
        sem_lista_de_estacoes(aviso, uf)
    st.caption(f"As {len(tabela)} estações automáticas do INMET em operação em {uf}. São os pontos dos mapas do "
               "painel: o número que cada mapa mostra num ponto é o que aquela estação mediu.")

    busca = st.text_input("Procurar", key="busca_estacoes",
                          placeholder="nome da estação, município ou código (ex.: Dourados, A702)")
    achadas = referencia.procurar(tabela, busca)
    procurando = bool(busca.strip())
    if procurando and not achadas.any():
        st.info(f"Nenhuma estação com \"{busca.strip()}\" no nome, no município ou no código.")

    mapa, lista = st.columns([3, 2])
    with mapa:
        st.pydeck_chart(mapa_das_estacoes(tabela, achadas & procurando, uf), height=ALTURA_MAPA)
        st.caption("Passe o mouse numa estação para ver o município, o código e a altitude; aproxime o mapa "
                   "para os nomes deixarem de se cobrir. As que a busca achou ficam em vermelho. O mapa base "
                   "vem do Carto, fora da SEMADESC.")
    with lista:
        colunas = ["Nº", "Estação", "Município", "Código", "Altitude (m)", "Em operação desde"]
        st.dataframe(tabela[achadas][colunas], hide_index=True, height=ALTURA_MAPA)

    # O PNG só é desenhado no clique, numa thread à parte: chega com a tabela e a base prontas
    hoje = date.today()
    base = base_cartografica(uf)
    titulo = f"Estações meteorológicas do INMET em {uf}"
    subtitulo = f"{len(tabela)} estações automáticas em operação em {hoje:%d/%m/%Y}"
    st.download_button("Baixar o mapa de referência (PNG)",
                       lambda: boletim.png(mapas.mapa_de_referencia(tabela, titulo, subtitulo, base)),
                       mime="image/png", key="baixar_referencia", on_click="ignore",
                       file_name=f"Mapa_Estacoes_INMET_{uf}_{hoje:%Y%m%d}.png",
                       help="As estações numeradas no mapa, com a lista dos nomes ao lado, no padrão do boletim "
                            "(título e logos), em 300 dpi: para boletins, relatórios e apresentações.")


def _observado() -> None:
    pass


# A previsão não usa nenhum filtro do observado (estado, período, estações), e numa aba ela ficaria
# atrás deles; como página, ela só consulta o Open-Meteo quando alguém a abre. Os gráficos e os
# mapas são páginas separadas pelo mesmo motivo: os mapas pedem a grade, os gráficos não.
observado = st.Page(_observado, title="Painel", icon=":material/history:", default=True)
lembrar_filtros()
pagina = st.navigation({
    "Observado (INMET)": [
        observado,
        st.Page(pagina_mapas_interativos, title="Mapas interativos", icon=":material/travel_explore:",
                url_path="mapas-interativos"),
        st.Page(pagina_estacoes, title="Localização Estações", icon=":material/location_on:", url_path="estacoes"),
    ],
    "Previsão (MS)": [
        st.Page(pagina_previsao, title="Estações", icon=":material/show_chart:", url_path="previsao"),
        st.Page(pagina_mapas_previstos, title="Mapas", icon=":material/map:", url_path="previsao-mapas"),
        st.Page(pagina_semanas, title="Semanas", icon=":material/date_range:", url_path="previsao-semanas"),
    ],
})
pagina.run()
if pagina is not observado:
    st.stop()

# =====================================================
# FILTROS
# =====================================================
st.title("Painel Meteorológico")
# Lugar na área principal para os avisos que nascem dentro da barra lateral: escritos lá, ficariam
# espremidos ao lado, onde quase ninguém olha.
aviso_principal = st.container()

if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
    st.error("Token do INMET não configurado. Preencha `TOKEN_INMET` no arquivo `.env` e recarregue a página.")
    st.stop()

with st.sidebar:
    st.header("Filtros")
    uf, recorte, intervalo = filtros_de_estado_e_periodo()

    try:
        estacoes = carregar_estacoes(uf).sort_values("Estação")
    except fonte.ErroFonte:
        sem_lista_de_estacoes(aviso_principal, uf)
    # Sem estação escolhida de saída: quem abre decide o que quer ver, e nenhuma consulta
    # à API acontece antes disso.
    # As escolhidas que não são do estado saem antes do widget: trocando o estado, as do anterior
    # não estão mais entre as opções, e o Streamlit recusaria o valor guardado
    st.session_state["estacoes"] = [nome for nome in st.session_state.get("estacoes", [])
                                    if nome in set(estacoes["Estação"])]
    nomes = st.multiselect("Estações", estacoes["Estação"].tolist(), key="estacoes",
                           help="Cada estação vira uma linha no gráfico. Os mapas usam sempre "
                                "todas as estações do estado.")
    # Uma grandeza dá um gráfico, com as suas séries dentro (máxima, mínima, média): escalas
    # diferentes nunca se misturam num eixo só. A chuva ficou de fora: ela não vira linha, vira
    # cascata, e a cascata mora na aba Chuva, junto do resto do que se lê dela.
    st.session_state.setdefault("grandezas", ["Temperatura", "Vento"])
    escolhidas = st.multiselect("Grandezas",
                                [nome for nome in variaveis.grandezas(variaveis.HORA, variaveis.GRAFICO)
                                 if nome != "Chuva"],
                                key="grandezas",
                                help="Cada grandeza ganha o seu gráfico, com as séries que a equipe "
                                     "de meteorologia definiu.")

    st.divider()
    guardadas, megabytes = coleta.tamanho_do_cache()
    st.caption(f"Cache local: {guardadas} estações, {megabytes:.1f} MB")
    if st.button("Limpar cache", width="stretch"):
        st.cache_data.clear()
        st.success(f"{coleta.limpar_cache()} arquivos removidos.")

if not nomes:
    st.info("Escolha ao menos uma estação na barra lateral.")
    st.stop()

# =====================================================
# DADOS
# =====================================================
inicio, fim = janela_do_periodo(intervalo, recorte)
codigos = tuple(estacoes.set_index("Estação").loc[nomes, "CD_ESTACAO"])

tabela, falharam = carregar_leituras(codigos, tuple(nomes), inicio, fim, uf)

if tabela.empty:
    st.warning("Nenhuma das estações escolhidas tem dados nesse período.")
    st.stop()
if falharam:
    st.warning(f"Sem dados ou falha na consulta: {', '.join(falharam)}")

horas_esperadas = int((fim - inicio).total_seconds() // 3600)
periodo_escolhido = (f"{intervalo[0]:%d/%m/%Y}" if intervalo[0] == intervalo[1]
                     else f"{intervalo[0]:%d/%m/%Y} a {intervalo[1]:%d/%m/%Y}")
# O mesmo Periodo que o main.py montaria para essas datas: é dele o texto das janelas nos mapas
# baixados para o boletim, e a tabela do risco sai com as colunas da planilha do produto.
periodo_painel = config.Periodo.de_datas(*intervalo, fuso=recorte.fuso)
aba_series, aba_mapa, aba_chuva, aba_risco, aba_qualidade = st.tabs(
    ["Estações: Séries Temporais", "Mapas Boletim", "Chuva", "Risco de Fogo", "Qualidade dos dados"])

# =====================================================
# SÉRIES TEMPORAIS
# =====================================================
with aba_series:
    # A agregação mora aqui, e não na barra lateral: esta é a única aba em que ela vale, e as
    # outras têm a sua própria. Na lateral ela parecia um filtro geral, e não era.
    modo_grafico = MODOS_GRAFICO[st.radio("Agregação", list(MODOS_GRAFICO), horizontal=True,
                                          key="modo_serie")]
    st.markdown(ESTILO_BALAO, unsafe_allow_html=True)
    st.caption(f"{len(tabela)} leituras de {tabela['Estação'].nunique()} estações · "
               f"{len(tabela) / (horas_esperadas * len(nomes)):.0%} das horas do período têm registro · "
               "Na legenda, **clique numa série** para deixar só ela no gráfico e no balão; "
               "**Shift+clique** na série destacada traz todas de volta.")
    if not escolhidas:
        st.info("Escolha ao menos uma variável na barra lateral.")

    for grandeza in escolhidas:
        longo, produtos = series_da_grandeza(tabela, grandeza, modo_grafico)
        if longo.empty:
            st.warning(f"{grandeza}: a API não devolveu essa medição no período.")
            continue

        st.subheader(grandeza)
        # A direção sai num gráfico próprio: em graus, ela não divide o eixo com km/h — juntar
        # duas unidades num eixo só é o tipo de gráfico que engana quem lê.
        circulares = [produto for produto in produtos if produto.colunas[0] in COLUNAS_CIRCULARES]
        for produto in circulares:
            rotulo = variaveis.rotulo_curto(produto)
            st.altair_chart(desenhar(longo[longo["Série"] == rotulo],
                                     f"{produto.nome} ({produto.unidade})", False, modo_grafico,
                                     produto.decimais), width="stretch")
            st.caption("A direção ligada por linha engana: entre 350° e 10° o vento mal mudou, mas o traço "
                       "desce o gráfico inteiro. Para o rumo do período, veja a rosa dos ventos abaixo.")
            longo = longo[longo["Série"] != rotulo]

        restantes = [produto for produto in produtos if produto not in circulares]
        if not restantes:
            continue
        primeiro = restantes[0]
        st.altair_chart(desenhar(longo, f"{grandeza} ({primeiro.unidade})", primeiro.zero_na_base,
                                 modo_grafico, primeiro.decimais), width="stretch")
        st.caption(" · ".join(f"**{variaveis.rotulo_curto(produto)}**: {produto.regra}"
                              for produto in restantes))

    if "VEN_DIR" in tabela and "VEN_VEL" in tabela:
        with st.expander("Rosa dos ventos do período"):
            st.caption("De onde o vento veio, em horas. Cada anel é uma faixa de velocidade; a faixa mais escura "
                       f"é a que interessa ao risco de fogo (≥ {FAIXAS_VENTO[-1][0]:g} km/h).")
            mostradas = nomes[:ROSAS_MAXIMAS]
            # Sempre ROSAS_MAXIMAS colunas, deixando vazias as das pontas: assim uma rosa sozinha
            # sai do mesmo tamanho das outras e no meio da tela, em vez de esticada na largura
            # inteira — desenhada com 3 polegadas, ela viraria um cartaz.
            colunas = st.columns(ROSAS_MAXIMAS)
            recuo = (ROSAS_MAXIMAS - len(mostradas)) // 2
            for coluna_tela, nome_estacao in zip(colunas[recuo:], mostradas):
                with coluna_tela:
                    st.pyplot(rosa_dos_ventos(tabela, nome_estacao), width="stretch")
            if len(nomes) > ROSAS_MAXIMAS:
                st.caption(f"Mostrando as {ROSAS_MAXIMAS} primeiras de {len(nomes)} estações escolhidas.")

    with st.expander("Ver e baixar os dados"):
        # Tudo o que a API devolveu, e não só as grandezas marcadas na barra lateral: quem baixa
        # quer o dado bruto, e os códigos são os do próprio INMET.
        visivel = coleta.planilha(tabela, uf)
        st.caption("Todas as colunas que a API do INMET devolve para as estações e o período "
                   "escolhidos, com os códigos dela — inclusive as que nenhum gráfico usa, como a "
                   "sensação térmica (`TEM_SEN`) e a tensão da bateria (`TEN_BAT`). "
                   "**Vento em km/h**: a API manda em m/s e o painel converte ao carregar, como "
                   f"fazem os produtos. `{coleta.DATA_LOCAL} ({uf})` é o horário do estado; "
                   "`DT_MEDICAO` e `HR_MEDICAO` são os da API, em UTC.")
        st.dataframe(visivel, width="stretch", height=300)
        st.download_button("Baixar CSV", visivel.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"leituras_{intervalo[0]:%Y%m%d}_a_{intervalo[1]:%Y%m%d}.csv", mime="text/csv")

# =====================================================
# MAPA
# =====================================================
with aba_mapa:
    if not st.session_state.get("mapa_liberado"):
        st.info(f"O mapa interpola as {len(estacoes)} estações do estado, e não só as escolhidas na barra "
                "lateral. Na primeira vez a consulta demora alguns minutos; depois vem do cache.")
        if st.button(f"Carregar todas as estações de {uf}", type="primary"):
            st.session_state["mapa_liberado"] = True
            st.rerun()
    else:
        leituras_mapa, ausentes_mapa = carregar_leituras(tuple(estacoes["CD_ESTACAO"]),
                                                         tuple(estacoes["Estação"]), inicio, fim, uf)
        # As vizinhas vêm numa consulta à parte para o caminho do produto ficar intocado: o que
        # sai delas só alimenta a interpolação da borda.
        vizinhas = estacoes_vizinhas(uf)
        leituras_apoio, _ = carregar_leituras(tuple(vizinhas["CD_ESTACAO"]), tuple(vizinhas["Estação"]),
                                              inicio, fim, uf, "Consultando as estações vizinhas")
        # O modo mora aqui, e não na barra lateral, porque o mapa tem um a mais que o gráfico: o
        # período inteiro, que é um mapa só para a janela toda, sem deslizante.
        modo = MODOS_MAPA[st.radio("Agregação", list(MODOS_MAPA), horizontal=True, key="modo_mapa")]
        # A chuva fica de fora: os acumulados dela olham para trás do período escolhido, e aqui
        # todo mapa respeita a janela da barra lateral. Ela tem aba própria.
        catalogo = [produto for produto in variaveis.disponiveis(modo, variaveis.MAPA)
                    if produto.grandeza != "Chuva"
                    and all(coluna in leituras_mapa for coluna in produto.colunas)]
        escolhidos = st.multiselect(
            "Mapas", [produto.nome for produto in catalogo],
            default=[nome for nome in variaveis.PADRAO_MAPA[modo] if nome in {p.nome for p in catalogo}],
            key=f"mapas_{modo}",
            help="Cada um traz a sua regra: a máxima do dia é a maior das máximas horárias, "
                 "nunca a média delas.")

        if not escolhidos:
            st.info("Escolha ao menos um mapa acima.")
        else:
            paradas = variaveis.momentos(leituras_mapa, modo)
            if modo != variaveis.PERIODO and not paradas:
                st.warning("Sem leituras no período escolhido.")
            else:
                if modo == variaveis.PERIODO:
                    momento, rotulo = None, periodo_escolhido
                    carimbo = f"{intervalo[0]:%Y%m%d}_a_{intervalo[1]:%Y%m%d}"
                else:
                    formatar = ((lambda marca: f"{marca:%d/%m/%Y}") if modo == variaveis.DIA
                                else (lambda marca: f"{marca:%d/%m %H:%M}"))
                    momento = linha_do_tempo("Quando", paradas, f"quando_{modo}", formatar)
                    rotulo = formatar(momento)
                    carimbo = (f"{momento:%Y%m%d}" if modo == variaveis.DIA else f"{momento:%Y%m%d_%H}h")

                marcar, ajustar_escala = st.columns([1, 1])
                with marcar:
                    rotulos = st.checkbox("Mostrar o valor de cada estação", value=True,
                                          help="Lado a lado os valores se cobrem: desligue para ler "
                                               "só o padrão das cores, ou amplie o mapa na tela cheia.")
                with ajustar_escala:
                    ajustar = st.checkbox("Ajustar a escala ao dado", value=False, key="ajustar_mapa",
                                          help="Desligada, a escala é fixa: a mesma cor quer dizer o "
                                               "mesmo valor em qualquer dia. Ligue para ver o padrão de "
                                               "um dia sem variação, sabendo que as cores mudam de "
                                               "significado.")
                horas_janela = {variaveis.HORA: 1.0, variaveis.DIA: 24.0}.get(
                    modo, (fim - inicio).total_seconds() / 3600)

                fatia = variaveis.recorte(leituras_mapa, modo, momento)
                fatia_apoio = variaveis.recorte(leituras_apoio, modo, momento)
                # Todas as linhas com a mesma quantidade de colunas: se a última fosse dimensionada
                # pelo que sobrou, os mapas dela sairiam maiores que os de cima. Com um mapa só, a
                # coluna é uma e ele ocupa a largura inteira.
                por_linha = min(len(escolhidos), MAPAS_POR_LINHA)
                with st.spinner("Desenhando os mapas..."):
                    for primeiro in range(0, len(escolhidos), por_linha):
                        linha = escolhidos[primeiro:primeiro + por_linha]
                        for coluna_tela, nome in zip(st.columns(por_linha), linha):
                            with coluna_tela:
                                produto = variaveis.por_nome(nome)
                                setas = variaveis.direcoes(produto, fatia)
                                painel_do_mapa(produto, variaveis.por_estacao(produto, fatia),
                                               rotulo, boletim.subtitulo(modo, momento, periodo_painel),
                                               carimbo, rotulos, uf,
                                               niveis_da_escala(produto, horas_janela, ajustar),
                                               None if setas.empty else setas,
                                               variaveis.por_estacao(produto, fatia_apoio))
                                # No período inteiro não há sequência: é um mapa só para a janela
                                if modo != variaveis.PERIODO:
                                    painel_do_gif(produto, leituras_mapa, modo, paradas, rotulos,
                                                  horas_janela, uf)

                st.caption(f"Interpolação IDW (potência {config.IDW_POTENCIA}, {config.IDW_VIZINHOS} vizinhos) "
                           f"sobre as {len(estacoes)} estações do estado mais {len(vizinhas)} de fora dele, "
                           "que seguram a superfície na borda sem entrar em tabela nem ranking. Neste tamanho "
                           "o mapa mostra o padrão, e a faixa de valores vai escrita sob cada um; para ler "
                           "estação por estação, amplie o mapa no ícone de tela cheia.")
                if ausentes_mapa:
                    st.caption(f"Sem dados no período ({len(ausentes_mapa)}): {', '.join(ausentes_mapa)}")

# =====================================================
# CHUVA
# =====================================================
# A chuva tem aba própria porque é a única que precisa de dado fora do período escolhido: o
# acumulado de 96 h, e o do mês, começam antes do início da janela da barra lateral.
with aba_chuva:
    # A cascata abre a aba porque é a única parte que não depende da consulta do mês: ela sai das
    # estações escolhidas na barra lateral e da janela dela, que já estão em mãos. Vinha da aba de
    # séries, onde a chuva era a única grandeza que não virava linha.
    st.subheader("Cascata das estações escolhidas")
    por_dia_cascata = MODOS_GRAFICO[st.radio("Agregação", list(MODOS_GRAFICO), horizontal=True,
                                             key="modo_cascata")] == variaveis.DIA
    barras = chuva_calc.cascata(tabela, por_dia=por_dia_cascata)
    if barras.empty:
        st.warning("A API não devolveu chuva nas estações escolhidas.")
    elif barras["valor"].sum() == 0:
        # Um quadro vazio parece defeito; a frase deixa claro que o dado existe e é zero
        st.info("Não choveu em nenhuma das estações escolhidas " +
                ("no período." if por_dia_cascata else "nas últimas 24 horas do período."))
    else:
        # Um gráfico por estação, e não um só com um painel para cada: assim cada um amplia sozinho
        # na tela cheia. O teto do eixo é o mesmo em todos, para lado a lado se compararem.
        nomes_cascata = sorted(barras["Estação"].unique())
        teto_cascata = float(barras["topo"].max())
        por_linha_cascata = min(len(nomes_cascata), 2)
        for primeiro in range(0, len(nomes_cascata), por_linha_cascata):
            linha = nomes_cascata[primeiro:primeiro + por_linha_cascata]
            for coluna_tela, nome in zip(st.columns(por_linha_cascata), linha):
                with coluna_tela:
                    st.altair_chart(cascata_da_chuva(barras[barras["Estação"] == nome], por_dia_cascata,
                                                     titulo=nome, teto=teto_cascata, altura=240),
                                    width="stretch")
        st.caption("Cada barra é a chuva daquele passo, empilhada no que já tinha caído; a barra "
                   "escura no fim é o total. O rótulo só vai onde choveu — num período seco seriam "
                   "dezenas de zeros cobrindo o desenho. " +
                   ("Um dia por barra." if por_dia_cascata
                    else "As últimas 24 horas do período — uma semana daria 168 barras."))

    st.divider()
    st.subheader("Todo o estado")
    # O deslizante vem das horas do período, e não do dado: é ele que decide até quando somar, e
    # portanto o que precisa ser baixado.
    horas_do_periodo = pd.date_range(inicio.astimezone(recorte.fuso) + pd.Timedelta(hours=1),
                                     fim.astimezone(recorte.fuso), freq="h")
    ate = st.select_slider("Acumulados até", options=list(horas_do_periodo),
                           value=horas_do_periodo[-1], key="referencia_chuva",
                           format_func=lambda marca: f"{marca:%d/%m %H:%M}",
                           help="Toda janela conta para trás a partir deste instante.")
    inicio_chuva = chuva_calc.inicio_necessario(ate)

    if not st.session_state.get("chuva_liberada"):
        st.info(f"Os acumulados olham para trás da data escolhida: para fechar o do mês, a consulta "
                f"vai até **{inicio_chuva:%d/%m}**. São as {len(estacoes)} estações do estado, e na "
                "primeira vez demora; depois vem do cache.")
        if st.button("Carregar a chuva do mês", type="primary"):
            st.session_state["chuva_liberada"] = True
            st.rerun()
    else:
        leituras_chuva, ausentes_chuva = carregar_leituras(
            tuple(estacoes["CD_ESTACAO"]), tuple(estacoes["Estação"]),
            inicio_chuva.astimezone(config.FUSO_UTC), ate.astimezone(config.FUSO_UTC), uf,
            f"Consultando a chuva desde {inicio_chuva:%d/%m}")
        vizinhas_chuva = estacoes_vizinhas(uf)
        chuva_apoio, _ = carregar_leituras(
            tuple(vizinhas_chuva["CD_ESTACAO"]), tuple(vizinhas_chuva["Estação"]),
            inicio_chuva.astimezone(config.FUSO_UTC), ate.astimezone(config.FUSO_UTC), uf,
            "Consultando a chuva das estações vizinhas")

        if leituras_chuva.empty or chuva_calc.COLUNA not in leituras_chuva:
            st.warning("A API não devolveu chuva no período.")
        else:
            # Sai do mesmo dado dos acumulados, então não custa consulta nenhuma: só reagrupa o
            # que já veio. Vem antes dos mapas porque responde outra pergunta — quando choveu no
            # período, e não onde —, e porque não depende de escolha nenhuma na barra lateral.
            st.markdown("**Cascata do estado**")
            por_dia_estado = MODOS_GRAFICO[st.radio("Agregação", list(MODOS_GRAFICO), horizontal=True,
                                                    key="modo_cascata_estado")] == variaveis.DIA
            barras_estado = chuva_calc.cascata_do_estado(leituras_chuva, por_dia=por_dia_estado)
            if barras_estado["valor"].sum() == 0:
                st.info(f"Nenhuma estação do estado registrou chuva até {ate:%d/%m %H:%M}.")
            else:
                st.altair_chart(cascata_da_chuva(barras_estado, por_dia_estado),
                                width="stretch")
                st.caption(f"**Soma das {len(estacoes)} estações** em cada passo, empilhada no que já "
                           "tinha caído. É o ritmo da chuva no período, e **não** quanto choveu num "
                           "lugar: milímetros de estações diferentes somados não descrevem ponto "
                           "nenhum do mapa. Para quanto caiu onde, os acumulados e o mapa abaixo. " +
                           (f"Um dia por barra, de {inicio_chuva:%d/%m} até {ate:%d/%m %H:%M}."
                            if por_dia_estado else
                            f"As últimas 24 horas até {ate:%d/%m %H:%M}."))
            st.divider()

            modo_chuva = st.radio("Mapas", ["Acumulados", "Hora a hora", "Por dia"], horizontal=True,
                                  key="modo_chuva")

            if modo_chuva == "Acumulados":
                escolhidos = st.multiselect("Janelas", chuva_calc.rotulos(), default=PADRAO_CHUVA,
                                            key="janelas_chuva",
                                            help="Cada janela conta para trás a partir do instante "
                                                 "escolhido acima. O mensal começa no dia 1º.")
                curtas = [rotulo for rotulo in escolhidos
                          if not chuva_calc.cobre(leituras_chuva, ate, rotulo)]
                if curtas:
                    st.warning(f"Sem dado que alcance o começo de: {', '.join(curtas)}. "
                               "Esses acumulados mostrariam menos chuva do que caiu.")
                mostrar = [rotulo for rotulo in escolhidos if rotulo not in curtas]

                somas = {rotulo: chuva_calc.acumulado(leituras_chuva, ate, rotulo) for rotulo in mostrar}
                chovidas = {rotulo: valores for rotulo, valores in somas.items()
                            if not valores.dropna().empty and valores.max() > 0}
                secas = [rotulo for rotulo in mostrar if rotulo not in chovidas]

                st.caption(f"Acumulados até **{ate:%d/%m %H:%M}**, somando a chuva de cada hora para "
                           "trás. A barra diz quanto choveu em cada estação; o mapa, onde choveu.")
                if secas:
                    st.info(f"Sem chuva em nenhuma estação em: {', '.join(secas)}.")

                if chovidas:
                    todas_estacoes = st.checkbox(f"Listar as {len(estacoes)} estações", value=False,
                                                 key="todas_chuva",
                                                 help="Desligada, a lista traz as 15 que mais choveram.")
                    # As barras vêm primeiro porque são a leitura mais usada — quanto choveu em
                    # cada estação, ordenado, que é o número que vai para o texto do boletim. O
                    # mapa responde outra pergunta (onde choveu) e custa um desenho por janela no
                    # servidor, então fica atrás de uma caixa.
                    nomes_chovidos = list(chovidas)
                    por_linha_barras = min(len(nomes_chovidos), 2)
                    for primeiro in range(0, len(nomes_chovidos), por_linha_barras):
                        linha = nomes_chovidos[primeiro:primeiro + por_linha_barras]
                        for coluna_tela, rotulo in zip(st.columns(por_linha_barras), linha):
                            with coluna_tela:
                                quantas = (chovidas[rotulo] > 0).sum()
                                st.markdown(f"**Chuva {rotulo}** — {quantas} de {len(estacoes)} "
                                            "estações com registro")
                                st.altair_chart(barras_do_acumulado(chovidas[rotulo], "mm",
                                                                    None if todas_estacoes else 15),
                                                width="stretch")

                    if st.checkbox("Gerar também os mapas destas janelas", value=False,
                                   key="mapas_chuva"):
                        marcar, ajustar_escala = st.columns([1, 1])
                        with marcar:
                            rotulos_chuva = st.checkbox("Mostrar o valor de cada estação", value=True,
                                                        key="valores_chuva")
                        with ajustar_escala:
                            ajustar_chuva = st.checkbox("Ajustar a escala ao dado", value=False,
                                                        key="ajustar_chuva",
                                                        help="Desligada, as classes são fixas: 20 mm "
                                                             "têm a mesma cor em qualquer mapa.")
                        acumulada = variaveis.por_nome("Chuva acumulada")
                        por_linha = min(len(nomes_chovidos), MAPAS_POR_LINHA)
                        with st.spinner("Desenhando os mapas..."):
                            for primeiro in range(0, len(nomes_chovidos), por_linha):
                                linha = nomes_chovidos[primeiro:primeiro + por_linha]
                                for coluna_tela, rotulo in zip(st.columns(por_linha), linha):
                                    with coluna_tela:
                                        comeco, _ = chuva_calc.janela(ate, rotulo)
                                        painel_da_chuva(
                                            rotulo, chovidas[rotulo],
                                            f"{comeco:%d/%m %H:%M} a {ate:%d/%m %H:%M}",
                                            periodo_painel.descrever_janela(comeco, ate),
                                            f"{ate:%Y%m%d_%H}h", rotulos_chuva, uf,
                                            niveis_da_escala(acumulada,
                                                             chuva_calc.horas_da_janela(ate, rotulo),
                                                             ajustar_chuva),
                                            chuva_calc.acumulado(chuva_apoio, ate, rotulo))
            else:
                # Hora a hora e por dia respeitam o período escolhido, como as outras abas
                modo = variaveis.HORA if modo_chuva == "Hora a hora" else variaveis.DIA
                do_periodo = leituras_chuva[leituras_chuva["dt_local"] > inicio.astimezone(recorte.fuso)]
                paradas = variaveis.momentos(do_periodo, modo)
                produto = variaveis.por_nome("Chuva na hora" if modo == variaveis.HORA else "Chuva acumulada")
                if not paradas:
                    st.warning("Sem leituras no período escolhido.")
                else:
                    formatar = ((lambda marca: f"{marca:%d/%m/%Y}") if modo == variaveis.DIA
                                else (lambda marca: f"{marca:%d/%m %H:%M}"))
                    momento = st.select_slider("Quando", options=paradas, value=paradas[-1],
                                               format_func=formatar, key=f"quando_chuva_{modo}")
                    rotulos_chuva = st.checkbox("Mostrar o valor de cada estação", value=True,
                                                key="valores_chuva_momento")
                    niveis_chuva = niveis_da_escala(produto, 1.0 if modo == variaveis.HORA else 24.0)
                    carimbo = (f"{momento:%Y%m%d}" if modo == variaveis.DIA else f"{momento:%Y%m%d_%H}h")
                    fatia = variaveis.recorte(do_periodo, modo, momento)
                    apoio_periodo = chuva_apoio[chuva_apoio["dt_local"] > inicio.astimezone(recorte.fuso)]
                    fatia_apoio = variaveis.recorte(apoio_periodo, modo, momento)
                    _, meio, _ = st.columns([1, 2, 1])
                    with meio:
                        painel_do_mapa(produto, variaveis.por_estacao(produto, fatia),
                                       formatar(momento), boletim.subtitulo(modo, momento, periodo_painel),
                                       carimbo, rotulos_chuva, uf, niveis_chuva,
                                       apoio=variaveis.por_estacao(produto, fatia_apoio))

            if ausentes_chuva:
                st.caption(f"Sem dados no período ({len(ausentes_chuva)}): {', '.join(ausentes_chuva)}")

# =====================================================
# RISCO DE FOGO
# =====================================================
# A regra 30-30-30 é a mesma dos produtos, importada de modulos/produtos/risco_fogo.py: a tela e
# o relatório não podem dizer números diferentes sobre a mesma hora. O dado também é o mesmo da
# aba dos mapas — mesma janela, mesma chave de cache —, então esta aba não custa consulta nenhuma
# a mais à API.
with aba_risco:
    st.caption(f"Conta quantas das três condições valem **em cada hora**: temperatura máxima "
               f"≥ {config.LIMIAR_TEMP_MAX:g} °C, umidade mínima ≤ {config.LIMIAR_UMIDADE_MIN:g} % e "
               f"rajada ≥ {config.LIMIAR_RAJADA:g} km/h. Hora a hora porque os extremos do dia "
               "acontecem em horários diferentes: 32 °C às 15 h, 28 % às 18 h e rajada às 03 h não "
               "são um dia de risco alto.")

    if not st.session_state.get("mapa_liberado"):
        st.info(f"Precisa das {len(estacoes)} estações do estado. Carregue-as na aba "
                "**Mapas Boletim** — o risco usa o mesmo dado, sem consultar de novo.")
    else:
        leituras_risco, _ = carregar_leituras(tuple(estacoes["CD_ESTACAO"]),
                                              tuple(estacoes["Estação"]), inicio, fim, uf)
        vizinhas_risco = estacoes_vizinhas(uf)
        risco_apoio, _ = carregar_leituras(tuple(vizinhas_risco["CD_ESTACAO"]),
                                           tuple(vizinhas_risco["Estação"]), inicio, fim, uf,
                                           "Consultando as estações vizinhas")
        completas, avaliadas = risco_avaliado(
            pd.concat([leituras_risco, risco_apoio], ignore_index=True),
            pd.concat([estacoes, vizinhas_risco], ignore_index=True), uf)
        do_produto = risco.da_uf(completas, uf)
        nomes_do_estado = {estacao["Estação"] for estacao, _ in do_produto}

        if not completas:
            st.warning("Nenhuma estação tem temperatura, umidade e rajada no período: sem as três "
                       "não dá para dizer quantas condições valeram.")
        elif not avaliadas:
            st.warning(f"Nenhuma hora teve {config.MIN_ESTACOES_INTERPOLACAO} estações com as três "
                       "medidas: com tão poucos pontos a superfície inventaria mais do que mostra.")
        else:
            horaria = risco.hora_a_hora(do_produto)
            ordem = ordem_das_estacoes(horaria)
            modo_risco = MODOS_MAPA[st.radio("Agregação", list(MODOS_MAPA), horizontal=True,
                                             key="modo_risco")]
            # Ligada por padrão desde 13/10/2026, a pedido da equipe: o nível de cada estação é o
            # que se procura primeiro no mapa
            detalhes = st.checkbox("Mostrar o nível e as condições de cada estação", value=True,
                                   key="detalhes_risco",
                                   help="O nível escrito em cada estação e, no hora a hora, um "
                                        "ponto abaixo dela para cada condição atendida. Por dia e "
                                        "no período inteiro sai só o nível: eles juntam horas "
                                        "diferentes, e as condições de uma hora não representam o "
                                        "dia. Lado a lado eles se cobrem: desligue para ler só as "
                                        "cores, ou amplie o mapa.")

            # --- hora a hora ---------------------------------------------------------------
            if modo_risco == variaveis.HORA:
                so_alto = st.checkbox(
                    f"Só as horas em que alguma estação chegou ao {config.ROTULOS_RISCO[risco.NIVEL_ALTO].lower()}",
                    value=False, key="so_risco_alto",
                    help="O deslizante passa a parar só nessas horas. O critério é o nível medido "
                         "nas estações, e não o da superfície: ela pode mostrar nível 2 onde "
                         "nenhuma estação chegou a 2.")
                mostradas = risco.horas_que_interessam(avaliadas) if so_alto else avaliadas

                if not mostradas:
                    st.info(f"Nenhuma estação chegou ao {config.ROTULOS_RISCO[risco.NIVEL_ALTO].lower()} "
                            "em hora nenhuma do período. Desligue a caixa acima para ver todas as horas.")
                else:
                    quando = list(mostradas)
                    hora = linha_do_tempo(
                        "Quando", quando, "quando_risco",
                        lambda marca: f"{marca.tz_convert(recorte.fuso):%d/%m %H:%M}",
                        dia_de=lambda marca: marca.tz_convert(recorte.fuso).date())
                    avaliada = mostradas[hora]
                    # As de apoio já entraram na grade; nos pontos e na contagem, só as do estado
                    do_estado_na_hora = avaliada.estacoes[avaliada.estacoes["Estação"].isin(nomes_do_estado)]
                    local = hora.tz_convert(recorte.fuso)
                    _, meio, _ = st.columns([1, 3, 1])
                    with meio:
                        png = mapa_de_risco(avaliada.grade, do_estado_na_hora,
                                            risco_fogo.COLUNA_NIVEL_HORA,
                                            f"{local:%d/%m/%Y %H:%M}", detalhes, uf)
                        if png is None:
                            st.warning("Sem estações com as três medidas nessa hora.")
                        else:
                            st.image(png, width="stretch")
                            quantas = (do_estado_na_hora[risco_fogo.COLUNA_NIVEL_HORA]
                                       == risco.NIVEL_ALTO).sum()
                            st.caption(f"{len(do_estado_na_hora)} estações do estado com as três medidas · "
                                       f"{quantas} no risco alto · as três variáveis são "
                                       "interpoladas separadas e a regra é aplicada célula a célula.")
                            st.download_button("Baixar PNG — risco de fogo", png, mime="image/png",
                                               key="baixar_risco",
                                               file_name=f"Mapa_risco_fogo_{local:%Y%m%d_%H}h.png")
                            # Com o nível e as condições de cada estação, como o mapa horário do
                            # main.py, mesmo com a caixa de detalhes desligada na tela
                            espec_hora = risco_fogo.espec_da_hora(local, uf)
                            botao_do_boletim("risco de fogo", "risco",
                                             f"{espec_hora.arquivo}_{local:%Y%m%d_%H}h.png",
                                             desenho_de_classes(avaliada.grade, do_estado_na_hora,
                                                                risco_fogo.COLUNA_NIVEL_HORA, espec_hora, uf,
                                                                risco_fogo.indicadores_condicoes()))
                            if st.button("Gerar GIF — risco de fogo", key="botao_gif_risco",
                                         help=f"{len(animacao.passos(quando))} quadros."):
                                st.session_state["gif_risco"] = True
                            if st.session_state.get("gif_risco"):
                                instantes = tuple(animacao.passos(quando))
                                gif = gif_do_risco(instantes, detalhes, uf, mostradas, nomes_do_estado)
                                if gif is not None:
                                    st.image(gif, width="stretch")
                                    st.caption(f"{len(instantes)} quadros. {len(gif) / 1e6:.1f} MB.")
                                    st.download_button("Baixar GIF — risco de fogo", gif,
                                                       mime="image/gif", key="baixar_gif_risco",
                                                       file_name=f"Animacao_risco_fogo_"
                                                                 f"{intervalo[0]:%Y%m%d}_a_{intervalo[1]:%Y%m%d}.gif")

            # --- por dia --------------------------------------------------------------------
            elif modo_risco == variaveis.DIA:
                dias = risco.por_dia(avaliadas)
                dia = linha_do_tempo("Dia", list(dias), "dia_risco", lambda data: f"{data:%d/%m/%Y}")
                _, meio, _ = st.columns([1, 3, 1])
                with meio:
                    do_dia = risco.estacoes_do_dia(avaliadas, dia)
                    png = mapa_de_risco(dias[dia], do_dia[do_dia["Estação"].isin(nomes_do_estado)],
                                        risco_fogo.COLUNA_NIVEL_HORA, f"{dia:%d/%m/%Y}", detalhes, uf)
                    if png is None:
                        st.warning("Sem estações com as três medidas nesse dia.")
                    else:
                        st.image(png, width="stretch")
                        st.caption("O **pior** nível que cada lugar alcançou no dia — e não o de "
                                   "uma hora específica. Um dia é de risco alto se houve uma hora "
                                   "em que as três condições valeram juntas.")
                        st.download_button("Baixar PNG — risco do dia", png, mime="image/png",
                                           key="baixar_risco_dia",
                                           file_name=f"Mapa_risco_fogo_{dia:%Y%m%d}.png")
                        espec_dia = risco_fogo.espec_nivel_maximo(uf, f"{dia:%d/%m/%Y}")
                        botao_do_boletim("risco do dia", "risco_dia",
                                         f"{espec_dia.arquivo}_{dia:%Y%m%d}_interpolado.png",
                                         desenho_de_classes(dias[dia], do_dia[do_dia["Estação"].isin(nomes_do_estado)],
                                                            risco_fogo.COLUNA_NIVEL_HORA, espec_dia, uf))

            # --- período inteiro ------------------------------------------------------------
            else:
                tabela_risco = risco.resumo(do_produto, periodo_painel, uf)
                # Os nomes de arquivo que o main.py daria a esses dois mapas, para o mesmo período
                sufixo = f"{periodo_painel.identificador}_interpolado.png"
                janela_risco = periodo_painel.descrever_janela(*periodo_painel.janela)
                esquerda, direita = st.columns(2)
                with esquerda:
                    st.markdown("**Pior nível do período**")
                    pior = risco.pior_nivel(avaliadas)
                    png = mapa_de_risco(pior, tabela_risco, risco_fogo.COLUNA_NIVEL, periodo_escolhido,
                                        detalhes, uf)
                    if png is not None:
                        st.image(png, width="stretch")
                        st.caption("O maior nível que cada lugar alcançou em alguma hora da janela.")
                        espec_pior = risco_fogo.espec_nivel_maximo(uf, janela_risco)
                        botao_do_boletim("pior nível", "risco_periodo", f"{espec_pior.arquivo}_{sufixo}",
                                         desenho_de_classes(pior, tabela_risco, risco_fogo.COLUNA_NIVEL,
                                                            espec_pior, uf))
                with direita:
                    st.markdown("**Horas em risco alto**")
                    exposicao = risco.horas_em_risco_alto(avaliadas)
                    png_horas = mapa_de_horas_altas(exposicao, tabela_risco, periodo_escolhido,
                                                    detalhes, uf)
                    if png_horas is not None:
                        st.image(png_horas, width="stretch")
                        st.caption(f"Em quantas horas cada lugar esteve no risco alto — no máximo "
                                   f"{int(exposicao.max())} h em alguma célula do estado.")
                        espec_horas = risco_fogo.espec_horas_em_risco_alto(uf, janela_risco)
                        botao_do_boletim("horas em risco alto", "risco_horas",
                                         f"{espec_horas.arquivo}_{sufixo}",
                                         desenho_das_horas(exposicao, tabela_risco, espec_horas, uf))

                st.subheader("Por estação")
                st.caption("Da estação de maior risco para a de menor. As colunas são as mesmas da "
                           "planilha que o `main.py` gera para o produto.")
                st.dataframe(tabela_risco, width="stretch", hide_index=True, height=340)
                st.download_button(
                    "Baixar CSV", tabela_risco.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"risco_fogo_{intervalo[0]:%Y%m%d}_a_{intervalo[1]:%Y%m%d}.csv",
                    mime="text/csv", key="baixar_tabela_risco")

            # --- gráficos, em qualquer modo --------------------------------------------------
            st.divider()
            st.subheader("Horas em cada nível")
            st.caption("Quantas horas cada estação passou em cada nível, no período inteiro. A "
                       "ordem das estações é a mesma nos três gráficos, para dar para comparar.")
            st.altair_chart(barras_de_niveis(horaria, ordem), width="stretch")

            st.subheader("Calendário")
            st.caption("O pior nível de cada estação em cada dia. **Dia vazio, sem cor:** a estação "
                       "não teve nenhuma hora com as três medidas (temperatura máxima, umidade mínima "
                       "e rajada) — ficou sem transmitir, ou um dos sensores ficou sem dado. É "
                       "diferente do **cinza**, em que ela mediu e nenhuma das condições valeu.")
            st.altair_chart(calendario_de_risco(horaria, ordem), width="stretch")

            st.subheader("Condições atendidas")
            dias_com_dado = sorted(horaria["dia"].unique())
            dia_condicoes = linha_do_tempo("Dia", dias_com_dado, "dia_condicoes",
                                           lambda data: f"{data:%d/%m/%Y}")
            st.caption("Quantas horas cada condição valeu naquele dia. É o gráfico que responde se "
                       "o risco veio do calor, da secura ou do vento — o que o nível sozinho não diz.")
            st.altair_chart(condicoes_do_dia(horaria, dia_condicoes, ordem), width="stretch")


# =====================================================
# QUALIDADE DOS DADOS
# =====================================================
with aba_qualidade:
    todas = st.checkbox(f"Analisar todas as estações de {uf}", value=False,
                        help="Ignora a seleção da barra lateral. Na primeira vez demora, porque baixa tudo; "
                             "depois vem do cache.")
    if todas:
        base, ausentes = carregar_leituras(tuple(estacoes["CD_ESTACAO"]), tuple(estacoes["Estação"]),
                                           inicio, fim, uf)
    else:
        base, ausentes = tabela, falharam

    if base.empty:
        st.warning("Sem dados para analisar nesse período.")
    else:
        resumo = qualidade.resumo_por_estacao(base, horas_esperadas)
        impossiveis = qualidade.valores_impossiveis(base)
        travados = qualidade.sensores_travados(base)

        metricas = st.columns(4)
        metricas[0].metric("Estações analisadas", f"{len(resumo)}")
        metricas[1].metric("Completude média", f"{resumo['Completude'].mean():.0f}%")
        metricas[2].metric("Valores impossíveis", f"{len(impossiveis)}")
        metricas[3].metric("Horas travadas", f"{int(resumo['Horas travadas'].sum())}")
        if ausentes:
            st.warning(f"Sem dados ou falha na consulta ({len(ausentes)}): {', '.join(ausentes)}")

        st.subheader("Por estação")
        st.caption("Da pior para a melhor. Completude é quanto das horas do período tem registro.")
        st.dataframe(resumo, width="stretch", hide_index=True, height=340, column_config={
            "Completude": st.column_config.ProgressColumn("Completude", format="%.0f%%", min_value=0, max_value=100),
        })

        st.subheader("Completude por dia")
        completude = qualidade.completude_por_dia(base)
        longo = (completude.reset_index()
                 .melt("Estação", var_name="Dia", value_name="Completude")
                 .assign(Dia=lambda tabela: tabela["Dia"].astype(str)))
        st.altair_chart(
            alt.Chart(longo).mark_rect().encode(
                x=alt.X("Dia:O", title=None, axis=alt.Axis(labelFontSize=FONTE_DOS_EIXOS)),
                y=alt.Y("Estação:N", title=None, sort=list(resumo["Estação"]),
                        axis=alt.Axis(labelFontSize=FONTE_DOS_EIXOS)),
                color=alt.Color("Completude:Q", title="% das horas",
                                scale=alt.Scale(scheme="blues", domain=[0, 100])),
                tooltip=["Estação", "Dia", alt.Tooltip("Completude:Q", format=".0f")],
            ).properties(height=max(18 * len(completude), 120)),
            width="stretch")

        st.subheader("Completude por variável")
        st.caption("A estação pode registrar a hora e mesmo assim não medir tudo. Aqui aparece o sensor que "
                   "parou sozinho, sem a estação sair do ar.")
        por_variavel = qualidade.completude_por_variavel(base).rename(columns=NOMES_CURTOS)
        longo_variavel = (por_variavel.reset_index()
                          .melt("Estação", var_name="Variável", value_name="Completude"))
        st.altair_chart(
            alt.Chart(longo_variavel).mark_rect().encode(
                x=alt.X("Variável:N", title=None, axis=alt.Axis(labelAngle=-45, labelFontSize=FONTE_DOS_EIXOS)),
                y=alt.Y("Estação:N", title=None, sort=list(resumo["Estação"]),
                        axis=alt.Axis(labelFontSize=FONTE_DOS_EIXOS)),
                color=alt.Color("Completude:Q", title="% das horas",
                                scale=alt.Scale(scheme="blues", domain=[0, 100])),
                tooltip=["Estação", "Variável", alt.Tooltip("Completude:Q", format=".0f")],
            ).properties(height=max(18 * len(por_variavel), 120)),
            width="stretch")

        with st.expander(f"Valores impossíveis ({len(impossiveis)})"):
            impossiveis = impossiveis.assign(
                Variável=impossiveis["Variável"].map(NOMES_CURTOS).fillna(impossiveis["Variável"]))
            st.caption("Leituras fora da faixa plausível da variável — costuma ser defeito de sensor.")
            st.dataframe(impossiveis, width="stretch", hide_index=True, height=260)

        with st.expander(f"Sensores travados ({len(travados)} trecho{'s' if len(travados) != 1 else ''})"):
            travados = travados.assign(Variável=travados["Variável"].map(NOMES_CURTOS).fillna(travados["Variável"]))
            st.caption(f"A mesma leitura repetida por {qualidade.HORAS_TRAVADO} horas ou mais, em temperatura, "
                       "umidade ou pressão. Chuva e vento ficam de fora: zero repetido ali é normal.")
            st.dataframe(travados, width="stretch", hide_index=True, height=260)

        st.caption(f"**Radiação negativa** à noite é ruído comum do sensor, não defeito — por isso aparece numa "
                   f"coluna própria, e não como valor impossível. **Bateria** abaixo de "
                   f"{qualidade.TENSAO_MINIMA:g} V costuma anteceder a estação sair do ar.")
