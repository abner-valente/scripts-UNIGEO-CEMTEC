"""Explorador de séries temporais das estações automáticas do INMET, por estado.

Ferramenta de análise, separada dos produtos: aqui não se gera planilha nem mapa, se olha o dado
para entender tendências e conferir a qualidade da medição. Os produtos continuam sendo gerados
pelo main.py, e nada aqui altera o que eles produzem.

Para abrir:  streamlit run app/explorador.py
"""
import io
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# O streamlit coloca a pasta do script no sys.path, e não a raiz do projeto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import altair as alt
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from matplotlib import colormaps
from matplotlib.cm import ScalarMappable
from matplotlib.colors import BoundaryNorm, Normalize
from matplotlib.figure import Figure

from app import animacao
from app import chuva as chuva_calc
from app import dados as coleta
from app import qualidade
from app import risco
from app import superficie
from app import variaveis
from modulos import calculos, config, inmet, mapas
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
# Corpo do número escrito sobre a barra. Vale para a barra deitada e para a cascata: são a mesma
# leitura em dois desenhos, e tamanhos diferentes fariam um parecer menos importante que o outro.
CORPO_ROTULO = 13
# Mapa navegável: o mapa base é o Carto (sem chave de acesso) e o enquadramento inicial sai do
# recorte — o centro do estado e um zoom que cabe o maior lado dele na tela.
ZOOM_POR_GRAU = 5.9 + 3.0  # calibrado em MS, que tem 8° de largura e abre bem no zoom 5,9
MAPA_BASE = pdk.map_styles.LIGHT
# MS é quase quadrado: ocupando a largura inteira da tela, o mapa sairia três vezes mais largo
# que alto e o estado nadaria no meio de São Paulo e da Bolívia. Em tela menor que isso o
# Streamlit encolhe o mapa até o espaço que houver, e o zoom inicial abaixo ainda o enquadra.
LARGURA_MAPA, ALTURA_MAPA = 1200, 780
# O Streamlit alinha tudo à esquerda; com largura fixa, sobraria um vão à direita nas telas
# largas. A regra abaixo centraliza só o mapa. Se um dia o Streamlit mudar esse identificador,
# o mapa volta a ficar à esquerda — e nada mais quebra.
CENTRALIZAR_MAPA = """<style>
[data-testid="stElementContainer"]:has([data-testid="stDeckGlJsonChart"]) {
  margin-left: auto; margin-right: auto; align-self: center;
}
</style>"""
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
    """As estações do estado — as do produto: tabelas, listas, rankings e CSV saem daqui."""
    return inmet.listar_estacoes(uf)


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_apoio(uf: str) -> pd.DataFrame:
    """As estações de fora do estado que ajudam a interpolar a borda.

    Elas não são do produto: não entram em lista, tabela nem ranking, e não aparecem desenhadas.
    Servem para o IDW ter dado dos dois lados da divisa — sem elas, os 8 vizinhos que ele enxerga
    numa célula da fronteira estão todos para dentro, e a superfície extrapola tendo medição do
    outro lado.

    Da vizinhança inteira sobram só as que podem entrar na conta de alguma célula: estar dentro
    da margem não basta, é preciso chegar às mais próximas de algum lugar que vira desenho. Isso
    corta 54 para 39 em MS e 89 para 60 em SC — requisição a menos em toda consulta, sem mudar
    mapa, porque a poda tem folga (ver config.VIZINHOS_NA_PODA).
    """
    vizinhanca = inmet.estacoes_do_recorte(config.recorte_de(uf))
    fora = vizinhanca[vizinhanca["SG_ESTADO"] != uf]
    dele = vizinhanca[vizinhanca["SG_ESTADO"] == uf]
    base = base_cartografica(uf)
    entram = calculos.apoio_que_entra(
        fora["VL_LONGITUDE"].values, fora["VL_LATITUDE"].values,
        dele["VL_LONGITUDE"].values, dele["VL_LATITUDE"].values,
        base.lon_grade[base.dentro_uf], base.lat_grade[base.dentro_uf],
        config.VIZINHOS_NA_PODA)
    return fora[entram].sort_values("Estação").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_leituras(codigos: tuple[str, ...], nomes: tuple[str, ...],
                      inicio: datetime, fim: datetime, uf: str,
                      _mensagem: str = "Consultando o INMET") -> tuple[pd.DataFrame, list[str]]:
    """Séries das estações escolhidas, com a lista das que falharam.

    A espera é de rede: uma consulta por estação, e são 62. A barra diz quantas já chegaram —
    um giro sem número não diz se falta muito, e numa janela longa a pessoa desiste antes de o
    mapa aparecer.

    A barra nasce **aqui dentro** de propósito. O Streamlit grava os elementos desenhados dentro
    de uma função cacheada e os redesenha quando a resposta vem do cache; uma barra criada fora
    quebra esse redesenho, porque o lugar dela pode não existir mais. Como ela é apagada antes
    do retorno, o redesenho não mostra nada — que é o certo, já que não houve espera.

    `_mensagem` começa com sublinhado para ficar fora da chave do cache: duas abas pedindo a
    mesma janela com textos diferentes baixariam tudo duas vezes.
    """
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
    """As séries daquela grandeza no tempo, e os produtos que as geraram.

    É aqui que o catálogo entra no gráfico: a máxima do dia sai da mesma regra que alimenta o
    mapa, e as duas telas não podem discordar.
    """
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
    """Um apelido curto e seguro para cada linha do gráfico: `s0`, `s1`...

    O balão junta várias linhas num quadro só, e para isso o Vega transforma esses apelidos em
    **nomes de campo**. Ponto em nome de campo ali quer dizer caminho aninhado, e meia dúzia de
    estações se chamam "Faz. Alvorada" ou parecido: o nome viraria dois níveis e o balão
    mostraria vazio. Daí o apelido no dado e o nome legível só no rótulo.
    """
    pares = (longo[["Estação", "Série"]].drop_duplicates()
             .sort_values(["Estação", "Série"]).itertuples(index=False))
    return {(estacao, serie): f"s{indice}" for indice, (estacao, serie) in enumerate(pares)}


def _regua(dados: pd.DataFrame, linhas_do_balao: list[tuple[str, str, str]], casas: int,
           por_dia: bool, apontar: alt.Parameter) -> alt.Chart:
    """Régua vertical e balão com as linhas do instante mais próximo do cursor.

    O balão tem as colunas fixas no spec: esconder uma linha tirando o dado dela deixaria "NaN"
    no lugar. Então cada linha vira um **texto pronto**, vazio quando a legenda escondeu aquela
    série ou quando a estação não mediu naquela hora — e linha vazia some do balão por CSS.

    Uma régua só, e não uma por estado da legenda: o Vega-Lite constrói uma única vizinhança de
    cursor por gráfico, e as outras camadas ficariam sem nenhuma.
    """
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


def desenhar(longo: pd.DataFrame, rotulo_y: str, zero_na_base: bool, modo: str, casas: int = 1) -> alt.Chart:
    """As séries de uma grandeza no tempo: cor separa a estação, traço separa a série.

    Cinco estações com três séries dariam quinze linhas iguais. Cor para a estação e traço para
    a série (máxima, mínima, média) deixa as duas leituras possíveis no mesmo desenho; clicar na
    legenda isola uma série, no desenho e também no balão.
    """
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
                                 labelFontSize=10, tickCount="day" if por_dia else alt.Undefined))
    base = alt.Chart(dados).encode(
        x=eixo_x,
        y=alt.Y("valor:Q", title=rotulo_y, scale=alt.Scale(zero=zero_na_base),
                axis=alt.Axis(grid=True, gridOpacity=0.25)),
        color=alt.Color("Estação:N", title=None, legend=alt.Legend(orient="bottom")),
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
    """Quanto choveu em cada estação na janela, da maior para a menor.

    É a leitura que vai para o texto do boletim — "Dourados registrou 78 mm em 24 h" —, e o mapa
    fica para quando a pergunta for *onde* choveu.
    """
    tabela = (valores.dropna().rename("valor").rename_axis("Estação").reset_index()
              .sort_values("valor", ascending=False))
    if quantas:
        # Só quem choveu: numa janela seca, quinze barras de 0,0 mm não dizem nada
        tabela = tabela[tabela["valor"] > 0].head(quantas)
    base = alt.Chart(tabela)
    # labelOverlap=False obriga o eixo a escrever todos os nomes. Com pouca altura ele descarta
    # um sim, um não — e aí uma lista de 15 estações parece uma de 8.
    eixo_estacao = alt.Y("Estação:N", sort="-x", title=None,
                         axis=alt.Axis(labelFontSize=14, labelOverlap=False, labelLimit=230))
    # size, e não height: em barra deitada é ele que define a espessura. Com height a barra
    # engorda até preencher a faixa, e os nomes das estações ficam espremidos entre elas.
    barras = base.mark_bar(color="#2171b5", size=20).encode(
        x=alt.X("valor:Q", title=unidade,
                axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=12, titleFontSize=13,
                              tickCount=8)),
        y=eixo_estacao,
        tooltip=[alt.Tooltip("Estação:N"), alt.Tooltip("valor:Q", title=unidade, format=".1f")])
    numeros = base.mark_text(align="left", dx=6, color="#d0d0d0", fontSize=CORPO_ROTULO).encode(
        x=alt.X("valor:Q"), y=eixo_estacao, text=alt.Text("valor:Q", format=".1f"))
    # Altura por estação, mais uma folga para o eixo de baixo: é o que garante espaço para cada
    # nome. Com altura fixa, três estações ficavam espremidas e quinze viravam oito.
    return (barras + numeros).properties(height=34 * len(tabela) + 60)


def cascata_da_chuva(barras: pd.DataFrame, por_dia: bool, facetar: bool = True) -> alt.Chart:
    """Quanto choveu em cada passo, empilhado no que já tinha caído, e a barra do total.

    Cada barra começa onde a anterior terminou, então a altura da última diz o acumulado sem
    ninguém somar de cabeça. A do total sai do chão, para comparar de relance.

    Com `facetar`, um painel por estação; sem, um gráfico só — que é como sai a do estado, já
    somada numa linha única.
    """
    ordem = list(barras.sort_values("ordem")["Passo"].unique())
    base = alt.Chart(barras).encode(
        x=alt.X("Passo:N", title=None, sort=ordem, axis=alt.Axis(labelAngle=-45, labelFontSize=9)))
    desenho = base.mark_bar(size=14 if por_dia else 8).encode(
        y=alt.Y("base:Q", title="Chuva (mm)", axis=alt.Axis(grid=True, gridOpacity=0.25, format=".1f")),
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
               .mark_text(baseline="bottom", dy=-4, fontSize=CORPO_ROTULO, color="#d0d0d0")
               .encode(y=alt.Y("topo:Q"), text=alt.Text("valor:Q", format=".1f")))
    empilhado = (desenho + numeros).properties(height=220 if facetar else 300)
    if not facetar:
        return empilhado
    return empilhado.facet(facet=alt.Facet("Estação:N", title=None), columns=2)


def rosa_dos_ventos(tabela: pd.DataFrame, nome_estacao: str):
    """Horas em que o vento veio de cada direção, separadas por faixa de velocidade.

    É a forma certa de resumir direção num período: cada pétala é um rumo, e o comprimento
    diz por quantas horas o vento veio de lá.
    """
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
    """Centro e zoom de abertura do mapa navegável, a partir do enquadramento do estado.

    O zoom foi calibrado em MS (8° de largura abrem bem em 5,9) e escala pelo maior lado: um
    estado pequeno abre mais perto, um grande mais longe, sem ninguém ajustar número nenhum.
    """
    oeste, leste, sul, norte = recorte.limites
    maior_lado = max(leste - oeste, norte - sul)
    return {"latitude": (sul + norte) / 2, "longitude": (oeste + leste) / 2,
            "zoom": round(ZOOM_POR_GRAU - math.log2(maior_lado), 1)}


def niveis_da_escala(produto: variaveis.Produto, horas_janela=None, ajustar: bool = False):
    """Os níveis de cor deste mapa, ou None para deixar a escala se ajustar ao dado."""
    if ajustar:
        return None
    escolhida = variaveis.escala(produto, horas_janela)
    if escolhida is None:
        return None
    tipo, valores = escolhida
    return np.linspace(*valores, 21) if tipo == "faixa" else list(valores)


@st.cache_data(show_spinner=False, max_entries=20)
def barra_de_escala(paleta: str, niveis: tuple, unidade: str) -> bytes:
    """Régua horizontal das cores, para ir embaixo do mapa.

    Com escala fixa a barra passa a valer para todos os mapas daquela grandeza — e sem ela a cor
    não diz número nenhum. Fica fina e deitada para não roubar largura do desenho, ao contrário
    da barra vertical que tiramos.
    """
    so_acima = float(min(niveis)) > 0
    continua = len(niveis) > 12  # faixa fixa vira 21 níveis; classes são poucas
    norma = (Normalize(vmin=min(niveis), vmax=max(niveis)) if continua
             else BoundaryNorm(niveis, ncolors=256, extend="max" if so_acima else "both"))

    fig = Figure(figsize=(4.0, 0.62))
    eixo = fig.add_axes([0.02, 0.55, 0.96, 0.38])
    barra = fig.colorbar(ScalarMappable(norm=norma, cmap=colormaps[paleta]), cax=eixo,
                         orientation="horizontal",
                         extend="max" if so_acima else "both",
                         ticks=None if continua else list(niveis))
    barra.set_label(unidade, color="#9a9a9a", size=8, labelpad=2)
    barra.ax.tick_params(colors="#9a9a9a", labelsize=7, length=2, pad=1)
    barra.outline.set_edgecolor("#9a9a9a")
    barra.outline.set_alpha(0.4)
    fig.patch.set_alpha(0)

    arquivo = io.BytesIO()
    fig.savefig(arquivo, format="png", dpi=150, bbox_inches="tight", transparent=True)
    return arquivo.getvalue()


@st.cache_resource(show_spinner=False, max_entries=ESTADOS_NA_MEMORIA)
def base_cartografica(uf: str) -> mapas.BaseCartografica:
    """Shapefiles, grade e máscara do estado: pesados de ler e iguais para todo mundo.

    `cache_resource` devolve **a mesma instância**, sem copiar — é o certo aqui, porque
    serializar 24 MB de GeoDataFrame a cada chamada custaria mais que a leitura que o cache
    poupa. O preço é que a instância vive enquanto a entrada viver, daí o teto.
    """
    return mapas.carregar_base(config.recorte_de(uf))


@st.cache_data(show_spinner=False, max_entries=30)
def mapa_do_instante(valores: pd.Series, titulo: str, unidade: str, paleta: str, decimais: int,
                     quando: str, rotulos: bool, uf: str, niveis=None, direcoes=None,
                     dpi: int = DPI_MAPA, apoio: pd.Series | None = None) -> bytes | None:
    """PNG do mapa interpolado de um instante, ou None se faltarem estações para interpolar.

    É o mesmo desenho dos produtos — mesmo IDW, mesmo recorte pelo estado, mesmas cores —, só que
    sem a moldura institucional: na tela, título e logos tomariam o lugar do mapa. Fica em cache
    porque cada passo do deslizante redesenha uma variável por vez: voltar a uma hora já vista não
    paga o desenho de novo.
    """
    pontos = _com_coordenadas(valores, titulo, uf)
    if direcoes is not None:
        # A direção não vira superfície: vai como seta sobre a estação, e o valor do mapa dá o
        # comprimento dela.
        pontos["Direção (°)"] = pontos["Estação"].map(direcoes)
    gdf = mapas.preparar_pontos(pontos, titulo, quando)
    if gdf is None or len(gdf) < config.MIN_ESTACOES_INTERPOLACAO:
        return None

    espec = mapas.EspecMapa(tabela="", coluna=titulo, titulo=titulo, subtitulo=quando, arquivo="",
                            cmap=paleta, ranking="", decimais=decimais, unidade=f"{titulo} ({unidade})",
                            direcao_vento=direcoes is not None)
    # As de apoio entram na conta e só: nada do que sai delas é desenhado, rotulado ou ranqueado
    vizinhas = (mapas.preparar_pontos(_com_coordenadas(apoio, titulo, uf), titulo, quando)
                if apoio is not None and not apoio.dropna().empty else None)
    figura = mapas.mapa_interpolado(gdf, espec, base_cartografica(uf), tela=mapas.Tela(rotulos=rotulos),
                                    niveis=niveis, apoio=vizinhas)
    if figura is None:
        return None
    # Codifica uma vez só: os mesmos bytes vão para a tela e para o botão de baixar
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


@st.cache_resource(show_spinner=False, max_entries=ESTADOS_NA_MEMORIA)
def malha_fina(uf: str) -> superficie.Malha:
    """Grade e máscara do estado do mapa navegável: dependem só da resolução, não do dado."""
    recorte = config.recorte_de(uf)
    return superficie.malha(base_cartografica(uf).uf.geometry.union_all(), recorte=recorte)


@st.cache_data(show_spinner=False, max_entries=30)
def camada_superficie(valores: pd.Series, nome_produto: str, quando: str, uf: str, niveis=None,
                      apoio: pd.Series | None = None) -> str:
    """A superfície do instante como imagem, pronta para virar camada do mapa."""
    produto = variaveis.por_nome(nome_produto)
    pontos = _com_coordenadas(valores, nome_produto, uf)
    vizinhas = (_com_coordenadas(apoio, nome_produto, uf)
                if apoio is not None and not apoio.dropna().empty else None)
    return superficie.como_uri(
        superficie.superficie_png(pontos, nome_produto, malha_fina(uf), produto.paleta, niveis,
                                  apoio=vizinhas))


def _com_coordenadas(valores: pd.Series, nome_variavel: str, uf: str) -> pd.DataFrame:
    """Valores de um instante com a latitude e a longitude de cada estação.

    O cadastro reúne as duas listas — as do estado e as de apoio —, e quem manda é o índice da
    série: entra o que estiver nela, saia de onde sair.
    """
    cadastro = pd.concat([carregar_estacoes(uf), carregar_apoio(uf)], ignore_index=True)
    coordenadas = (cadastro.set_index("Estação")[["VL_LATITUDE", "VL_LONGITUDE"]]
                   .rename(columns={"VL_LATITUDE": "Latitude", "VL_LONGITUDE": "Longitude"}))
    return coordenadas.join(valores.rename(nome_variavel), how="inner").reset_index().dropna()


def painel_do_mapa(produto: variaveis.Produto, valores: pd.Series, rotulo: str, carimbo: str,
                   rotulos: bool, uf: str, niveis=None, direcoes=None, apoio=None) -> None:
    """Uma coluna da linha de mapas: nome do produto, desenho, a regra e o botão de baixar."""
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
    arquivo = produto.nome.lower().replace(" ", "_")
    st.download_button(f"Baixar PNG — {medida}", png, mime="image/png", key=f"baixar_{produto.nome}",
                       file_name=f"Mapa_{arquivo}_{carimbo}.png")


def escala_do_periodo(produto: variaveis.Produto, leituras: pd.DataFrame, horas_janela):
    """Os níveis de cor que valem para todos os quadros do GIF.

    Num GIF a escala **tem** de ser a mesma do começo ao fim: esticada a cada quadro, a mancha
    fica igual e só as cores piscam, e quem olha vê variação onde não houve. Quando o produto não
    tem escala fixa — a pressão —, a faixa sai dos extremos da janela inteira.
    """
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
    """O mapa quadro a quadro, do começo ao fim da janela, como GIF.

    Guarda poucos na memória (`max_entries`) de propósito: cada um pesa alguns MB, e o painel
    pode estar servindo várias pessoas ao mesmo tempo.
    """
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
    """Botão que monta a animação do período, e a animação quando ela fica pronta.

    Atrás de um botão porque custa: um quadro por hora da janela, desenhados um a um. Quem só
    quer o mapa da hora não deve pagar por isso.
    """
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
                       file_name=f"Animacao_{produto.nome.lower().replace(' ', '_')}_"
                                 f"{momentos[0]:%Y%m%d}_a_{momentos[-1]:%Y%m%d}.gif")


def painel_da_chuva(rotulo: str, valores: pd.Series, janela: str, rotulos: bool, uf: str,
                    niveis=None, apoio=None) -> None:
    """Uma coluna da linha de acumulados: quanto choveu na janela que termina no fim do período."""
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


@st.cache_data(show_spinner=False, max_entries=2)
def risco_avaliado(leituras: pd.DataFrame, estacoes_do_estado: pd.DataFrame, uf: str):
    # `leituras` e `estacoes_do_estado` já vêm com as vizinhas dentro: a grade de risco de uma
    # célula da divisa depende do que acontece dos dois lados. Quem separa o produto do apoio é
    # `risco.da_uf`, na aba.
    """As horas da janela avaliadas pela regra 30-30-30, com a grade de cada uma.

    São três interpolações por hora — 31 ms cada, ~5 s numa semana —, e por isso fica em cache e
    com barra: quem mexe no deslizante não pode pagar isso de novo a cada passo.
    """
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
    """PNG do mapa de níveis de risco: a superfície em quatro classes e as estações por cima.

    Com `detalhes`, cada estação leva o seu nível escrito e os pontinhos das condições atendidas
    — temperatura, umidade e rajada, da esquerda para a direita.
    """
    gdf = mapas.preparar_pontos(pontos, coluna, quando)
    if gdf is None:
        return None
    espec = mapas.EspecClasses(f"Risco de fogo em {uf}", quando, "",
                               config.CORES_RISCO, config.ROTULOS_RISCO)
    figura = mapas.mapa_classes_interpolado(
        grade, gdf, coluna, espec, base_cartografica(uf),
        indicadores=risco_fogo.indicadores_condicoes() if detalhes else None,
        tela=mapas.Tela(rotulos=detalhes))
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


@st.cache_data(show_spinner=False, max_entries=6)
def mapa_de_horas_altas(grade, pontos: pd.DataFrame, quando: str, detalhes: bool,
                        uf: str) -> bytes | None:
    """PNG do mapa de exposição: em quantas horas cada lugar esteve no risco alto."""
    gdf = mapas.preparar_pontos(pontos, risco_fogo.COLUNA_HORAS_ALTO, quando)
    if gdf is None:
        return None
    espec = mapas.EspecMapa(tabela="", coluna=risco_fogo.COLUNA_HORAS_ALTO,
                            titulo=f"Horas em risco alto em {uf}", subtitulo=quando,
                            arquivo="", cmap="YlOrRd", ranking="",
                            unidade="Horas em risco alto", decimais=0)
    maximo = int(grade.max())
    figura = mapas.mapa_de_grade(grade, gdf, espec, base_cartografica(uf),
                                 niveis=np.arange(0, max(maximo, 1) + 1) if maximo < 12 else 12,
                                 tela=mapas.Tela(rotulos=detalhes))
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=DPI_MAPA, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


@st.cache_data(show_spinner=False, max_entries=2)
def gif_do_risco(instantes: tuple, detalhes: bool, uf: str, _avaliadas: dict,
                 _do_estado: set | None = None) -> bytes | None:
    """A sequência das horas de risco, com a data e a hora escritas em cada quadro.

    `_avaliadas` não entra na chave do cache (é um dicionário de grades, caro de resumir): as
    horas escolhidas já identificam o que está sendo desenhado.
    """
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
    """As estações da que passou mais horas em risco alto para a que passou menos.

    A mesma ordem nos três gráficos: sem isso, comparar um com o outro vira caça ao nome.
    """
    por_nivel = (horaria.pivot_table(index="Estação", columns=risco_fogo.COLUNA_NIVEL_HORA,
                                     values="dia", aggfunc="count")
                 .reindex(columns=[1, risco.NIVEL_MEDIO, risco.NIVEL_ALTO]).fillna(0))
    return list(por_nivel.sort_values([risco.NIVEL_ALTO, risco.NIVEL_MEDIO, 1],
                                      ascending=False).index)


def barras_de_niveis(horaria: pd.DataFrame, ordem: list[str]) -> alt.Chart:
    """Quantas horas cada estação passou em cada nível, empilhadas."""
    contagem = (horaria[horaria[risco_fogo.COLUNA_NIVEL_HORA] > 0]
                .groupby(["Estação", risco_fogo.COLUNA_NIVEL_HORA]).size().reset_index(name="Horas"))
    return (alt.Chart(contagem)
            .mark_bar()
            .encode(x=alt.X("Horas:Q", title="Horas",
                            axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=12)),
                    y=alt.Y("Estação:N", sort=ordem, title=None,
                            axis=alt.Axis(labelFontSize=13, labelOverlap=False, labelLimit=230)),
                    color=alt.Color(f"{risco_fogo.COLUNA_NIVEL_HORA}:O", title="Nível",
                                    scale=alt.Scale(domain=[1, risco.NIVEL_MEDIO, risco.NIVEL_ALTO],
                                                    range=config.CORES_RISCO[1:]),
                                    legend=alt.Legend(orient="bottom",
                                                      labelExpr="{'1': 'Baixo', '2': 'Médio', '3': 'Alto'}[datum.label]")),
                    tooltip=["Estação", alt.Tooltip(f"{risco_fogo.COLUNA_NIVEL_HORA}:O", title="Nível"),
                             "Horas"])
            .properties(height=22 * max(contagem["Estação"].nunique(), 1) + 60))


def calendario_de_risco(horaria: pd.DataFrame, ordem: list[str]) -> alt.Chart:
    """O pior nível de cada estação em cada dia — a leitura de relance da semana."""
    por_dia = (horaria.groupby(["Estação", "dia"])[risco_fogo.COLUNA_NIVEL_HORA].max()
               .reset_index().assign(dia=lambda tabela: tabela["dia"].astype(str)))
    return (alt.Chart(por_dia)
            .mark_rect(stroke="#111111", strokeWidth=1)
            .encode(x=alt.X("dia:O", title=None, axis=alt.Axis(labelAngle=-45, labelFontSize=11)),
                    y=alt.Y("Estação:N", sort=ordem, title=None,
                            axis=alt.Axis(labelFontSize=12, labelOverlap=False, labelLimit=230)),
                    color=alt.Color(f"{risco_fogo.COLUNA_NIVEL_HORA}:O", title="Pior nível do dia",
                                    scale=alt.Scale(domain=[0, 1, risco.NIVEL_MEDIO, risco.NIVEL_ALTO],
                                                    range=config.CORES_RISCO),
                                    legend=alt.Legend(orient="bottom",
                                                      labelExpr="{'0': 'Sem condição', '1': 'Baixo', "
                                                                "'2': 'Médio', '3': 'Alto'}[datum.label]")),
                    tooltip=["Estação", alt.Tooltip("dia:O", title="Dia"),
                             alt.Tooltip(f"{risco_fogo.COLUNA_NIVEL_HORA}:O", title="Pior nível")])
            .properties(height=20 * max(por_dia["Estação"].nunique(), 1) + 60))


def condicoes_do_dia(horaria: pd.DataFrame, dia, ordem: list[str]) -> alt.Chart:
    """Quantas horas cada condição valeu, estação por estação, naquele dia.

    É o gráfico que responde "o risco veio do calor, da secura ou do vento?" — a pergunta que o
    nível sozinho não responde.
    """
    do_dia = horaria[horaria["dia"] == dia]
    somas = (do_dia.groupby("Estação")[risco_fogo.COLUNAS_CONDICOES].sum().reset_index()
             .melt("Estação", var_name="Condição", value_name="Horas"))
    somas = somas[somas["Horas"] > 0]
    rotulos = dict(zip(risco_fogo.COLUNAS_CONDICOES, risco_fogo.rotulos_condicoes()))
    somas["Condição"] = somas["Condição"].map(rotulos)
    return (alt.Chart(somas)
            .mark_bar()
            .encode(x=alt.X("Horas:Q", title="Horas",
                            axis=alt.Axis(grid=True, gridOpacity=0.25, labelFontSize=12)),
                    y=alt.Y("Estação:N", sort=ordem, title=None,
                            axis=alt.Axis(labelFontSize=13, labelOverlap=False, labelLimit=230)),
                    yOffset=alt.YOffset("Condição:N"),
                    color=alt.Color("Condição:N", title=None,
                                    scale=alt.Scale(domain=list(rotulos.values()),
                                                    range=config.CORES_CONDICOES),
                                    legend=alt.Legend(orient="bottom", columns=1)),
                    tooltip=["Estação", "Condição", "Horas"])
            .properties(height=30 * max(somas["Estação"].nunique(), 1) + 80))


# =====================================================
# FILTROS
# =====================================================
st.title("Painel Meteorológico")

if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
    st.error("Token do INMET não configurado. Preencha `TOKEN_INMET` no arquivo `.env` e recarregue a página.")
    st.stop()

with st.sidebar:
    st.header("Filtros")
    # O estado vem primeiro porque tudo abaixo depende dele: as estações, o fuso que define o
    # dia, os shapefiles do mapa. Só aparecem as UFs que têm shapefile na pasta shp/.
    ufs = config.ufs_disponiveis()
    uf = st.selectbox("Estado", ufs, index=ufs.index(config.UF) if config.UF in ufs else 0,
                      format_func=lambda sigla: f"{sigla} — {config.ESTADOS[sigla][0]}",
                      help="Para acrescentar um estado, rode "
                           "ferramentas/simplificar_municipios.py --uf SIGLA, que busca a "
                           "malha dele no IBGE.")
    recorte = config.recorte_de(uf)
    hoje = date.today()
    intervalo = st.date_input("Período", value=(hoje - timedelta(days=7), hoje - timedelta(days=1)),
                              max_value=hoje, format="DD/MM/YYYY")
    if len(intervalo) != 2:
        st.info("Escolha a data inicial e a final.")
        st.stop()

    estacoes = carregar_estacoes(uf).sort_values("Estação")
    # Sem estação escolhida de saída: quem abre decide o que quer ver, e nenhuma consulta
    # à API acontece antes disso.
    nomes = st.multiselect("Estações", estacoes["Estação"].tolist(), default=[],
                           help="Cada estação vira uma linha no gráfico. Os mapas usam sempre "
                                "todas as estações do estado.")
    # Uma grandeza dá um gráfico, com as suas séries dentro (máxima, mínima, média): escalas
    # diferentes nunca se misturam num eixo só. A chuva ficou de fora: ela não vira linha, vira
    # cascata, e a cascata mora na aba Chuva, junto do resto do que se lê dela.
    escolhidas = st.multiselect("Grandezas",
                                [nome for nome in variaveis.grandezas(variaveis.HORA, variaveis.GRAFICO)
                                 if nome != "Chuva"],
                                default=["Temperatura", "Vento"],
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
# O dia é o do estado escolhido: em MS ele começa às 04 h UTC, no Paraná às 03 h
inicio = datetime.combine(intervalo[0], datetime.min.time(), tzinfo=recorte.fuso).astimezone(config.FUSO_UTC)
fim = (datetime.combine(intervalo[1], datetime.min.time(), tzinfo=recorte.fuso)
       + timedelta(days=1)).astimezone(config.FUSO_UTC)
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
aba_series, aba_mapa, aba_chuva, aba_risco, aba_navegavel, aba_qualidade = st.tabs(
    ["Estações: Séries Temporais", "Mapas Boletim", "Chuva", "Risco de Fogo", "Mapa Navegação",
     "Qualidade dos dados"])

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
        vizinhas = carregar_apoio(uf)
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
                    momento = st.select_slider("Quando", options=paradas, value=paradas[-1],
                                               format_func=formatar, key=f"quando_{modo}")
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
                                               rotulo, carimbo, rotulos, uf,
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
        st.altair_chart(cascata_da_chuva(barras, por_dia_cascata), width="stretch")
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
        vizinhas_chuva = carregar_apoio(uf)
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
                st.altair_chart(cascata_da_chuva(barras_estado, por_dia_estado, facetar=False),
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
                                            f"{comeco:%d/%m %H:%M} a {ate:%d/%m %H:%M}", rotulos_chuva,
                                            uf,
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
                                       formatar(momento), carimbo, rotulos_chuva, uf, niveis_chuva,
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
        vizinhas_risco = carregar_apoio(uf)
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
            detalhes = st.checkbox("Mostrar o nível e as condições de cada estação", value=False,
                                   key="detalhes_risco",
                                   help="O nível escrito em cada estação e, abaixo dela, um ponto "
                                        "para cada condição atendida. Lado a lado eles se cobrem; "
                                        "ligue ao ampliar um mapa.")

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
                    hora = st.select_slider(
                        "Quando", options=quando, value=quando[-1], key="quando_risco",
                        format_func=lambda marca: f"{marca.tz_convert(recorte.fuso):%d/%m %H:%M}")
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
                dia = st.select_slider("Dia", options=list(dias), value=list(dias)[-1],
                                       key="dia_risco", format_func=lambda data: f"{data:%d/%m/%Y}")
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

            # --- período inteiro ------------------------------------------------------------
            else:
                tabela_risco = risco.resumo(
                    do_produto, config.Periodo.de_datas(*intervalo, fuso=recorte.fuso), uf)
                esquerda, direita = st.columns(2)
                with esquerda:
                    st.markdown("**Pior nível do período**")
                    png = mapa_de_risco(risco.pior_nivel(avaliadas), tabela_risco,
                                        risco_fogo.COLUNA_NIVEL, periodo_escolhido, detalhes, uf)
                    if png is not None:
                        st.image(png, width="stretch")
                        st.caption("O maior nível que cada lugar alcançou em alguma hora da janela.")
                with direita:
                    st.markdown("**Horas em risco alto**")
                    exposicao = risco.horas_em_risco_alto(avaliadas)
                    png_horas = mapa_de_horas_altas(exposicao, tabela_risco, periodo_escolhido,
                                                    detalhes, uf)
                    if png_horas is not None:
                        st.image(png_horas, width="stretch")
                        st.caption(f"Em quantas horas cada lugar esteve no risco alto — no máximo "
                                   f"{int(exposicao.max())} h em alguma célula do estado.")

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
            st.caption("O pior nível de cada estação em cada dia.")
            st.altair_chart(calendario_de_risco(horaria, ordem), width="stretch")

            st.subheader("Condições atendidas")
            dias_com_dado = sorted(horaria["dia"].unique())
            dia_condicoes = st.select_slider(
                "Dia", options=dias_com_dado, value=dias_com_dado[-1], key="dia_condicoes",
                format_func=lambda data: f"{data:%d/%m/%Y}")
            st.caption("Quantas horas cada condição valeu naquele dia. É o gráfico que responde se "
                       "o risco veio do calor, da secura ou do vento — o que o nível sozinho não diz.")
            st.altair_chart(condicoes_do_dia(horaria, dia_condicoes, ordem), width="stretch")


# =====================================================
# MAPA NAVEGÁVEL (EM TESTE)
# =====================================================
# A mesma superfície da aba anterior, mas sobre um mapa base que se aproxima e arrasta. Aqui o
# desenho não é o do relatório: a interpolação é a mesma (modulos/calculos.py), o desenho é do
# deck.gl. Em teste para decidir se substitui, complementa ou não vale a manutenção.
with aba_navegavel:
    st.caption("Em teste. A conta é a mesma dos relatórios; o desenho é outro — aproxime com a roda "
               "do mouse, arraste para deslocar e passe o mouse numa estação para ver o valor.")
    if not st.session_state.get("mapa_liberado"):
        st.info(f"Precisa das {len(estacoes)} estações do estado. Carregue-as na aba **Mapas Boletim**.")
    else:
        leituras_navegavel, _ = carregar_leituras(tuple(estacoes["CD_ESTACAO"]),
                                                  tuple(estacoes["Estação"]), inicio, fim, uf)
        vizinhas_nav = carregar_apoio(uf)
        apoio_navegavel, _ = carregar_leituras(tuple(vizinhas_nav["CD_ESTACAO"]),
                                               tuple(vizinhas_nav["Estação"]), inicio, fim, uf,
                                               "Consultando as estações vizinhas")
        modo_nav = MODOS_MAPA[st.radio("Agregação", list(MODOS_MAPA), horizontal=True, key="modo_navegavel")]
        catalogo_nav = [produto for produto in variaveis.disponiveis(modo_nav, variaveis.MAPA)
                        if produto.grandeza != "Chuva"
                        and all(coluna in leituras_navegavel for coluna in produto.colunas)]

        if not catalogo_nav:
            st.warning("A API não devolveu nenhuma dessas medições no período.")
        else:
            # Um mapa por vez: aqui a comparação lado a lado dá lugar ao zoom
            escolher, ajustar_nav_col = st.columns([2, 1])
            with escolher:
                nome_nav = st.selectbox("Mapa", [produto.nome for produto in catalogo_nav],
                                        key="mapa_navegavel")
            with ajustar_nav_col:
                ajustar_nav = st.checkbox("Ajustar a escala ao dado", value=False, key="ajustar_nav",
                                          help="Desligada, a escala é a mesma do mapa do boletim.")
            produto_nav = variaveis.por_nome(nome_nav)
            paradas_nav = variaveis.momentos(leituras_navegavel, modo_nav)

            if modo_nav == variaveis.PERIODO:
                momento_nav, rotulo_nav = None, periodo_escolhido
            elif not paradas_nav:
                momento_nav, rotulo_nav = None, periodo_escolhido
                st.warning("Sem leituras no período escolhido.")
            else:
                formatar_nav = ((lambda marca: f"{marca:%d/%m/%Y}") if modo_nav == variaveis.DIA
                                else (lambda marca: f"{marca:%d/%m %H:%M}"))
                momento_nav = st.select_slider("Quando", options=paradas_nav, value=paradas_nav[-1],
                                               format_func=formatar_nav, key=f"quando_nav_{modo_nav}")
                rotulo_nav = formatar_nav(momento_nav)

            fatia_nav = variaveis.recorte(leituras_navegavel, modo_nav, momento_nav)
            valores_nav = variaveis.por_estacao(produto_nav, fatia_nav)

            if valores_nav.notna().sum() < config.MIN_ESTACOES_INTERPOLACAO:
                st.warning(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram "
                           f"{produto_nav.nome.lower()} em {rotulo_nav}.")
            else:
                horas_nav = {variaveis.HORA: 1.0, variaveis.DIA: 24.0}.get(
                    modo_nav, (fim - inicio).total_seconds() / 3600)
                niveis_nav = niveis_da_escala(produto_nav, horas_nav, ajustar_nav)
                with st.spinner("Desenhando o mapa..."):
                    imagem = camada_superficie(valores_nav, produto_nav.nome, rotulo_nav, uf,
                                               tuple(niveis_nav) if niveis_nav is not None else None,
                                               variaveis.por_estacao(
                                                   produto_nav,
                                                   variaveis.recorte(apoio_navegavel, modo_nav, momento_nav)))
                pontos = _com_coordenadas(valores_nav, produto_nav.nome, uf)
                pontos["Valor"] = pontos[produto_nav.nome].map(
                    lambda valor: f"{valor:.{produto_nav.decimais}f} {produto_nav.unidade}")
                # A imagem entra depois de criada a camada: passada no construtor, o pydeck a
                # trataria como expressão a ser avaliada no navegador ("@@=data:image/png;...").
                campo = pdk.Layer("BitmapLayer", data=None, bounds=superficie.limites(recorte))
                campo.image = imagem
                camadas = [
                    campo,
                    pdk.Layer("GeoJsonLayer", data=base_cartografica(uf).uf.__geo_interface__,
                              stroked=True, filled=False, get_line_color=[40, 40, 40], line_width_min_pixels=1),
                    pdk.Layer("ScatterplotLayer", data=pontos, get_position=["Longitude", "Latitude"],
                              get_fill_color=[20, 20, 20, 200], get_line_color=[255, 255, 255],
                              line_width_min_pixels=1, stroked=True, radius_min_pixels=4,
                              get_radius=2500, pickable=True),
                ]
                if st.checkbox("Mostrar o valor de cada estação", value=True, key="valores_navegavel",
                               help="Aproxime para os valores deixarem de se cobrir."):
                    camadas.append(
                        pdk.Layer("TextLayer", data=pontos, get_position=["Longitude", "Latitude"],
                                  get_text="Valor", get_size=12,
                                  get_color=[20, 20, 20], get_pixel_offset=[0, -14],
                                  background=True, get_background_color=[255, 255, 255, 200]))

                st.markdown(CENTRALIZAR_MAPA, unsafe_allow_html=True)
                st.pydeck_chart(pdk.Deck(layers=camadas, map_style=MAPA_BASE,
                                         initial_view_state=pdk.ViewState(**visao_inicial(recorte)),
                                         tooltip={"text": "{Estação} — {Valor}"}),
                                width=LARGURA_MAPA, height=ALTURA_MAPA)
                if niveis_nav is not None:
                    st.image(barra_de_escala(produto_nav.paleta, tuple(niveis_nav), produto_nav.unidade),
                             width="content")
                casas = produto_nav.decimais
                st.caption(f"{valores_nav.min():.{casas}f} a {valores_nav.max():.{casas}f} "
                           f"{produto_nav.unidade} · {valores_nav.notna().sum()} estações · "
                           f"{produto_nav.regra}, em {rotulo_nav}. O mapa base vem do Carto, fora da SEMADESC.")

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
                x=alt.X("Dia:O", title=None),
                y=alt.Y("Estação:N", title=None, sort=list(resumo["Estação"])),
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
                x=alt.X("Variável:N", title=None, axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("Estação:N", title=None, sort=list(resumo["Estação"])),
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
