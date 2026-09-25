"""Explorador de séries temporais das estações automáticas do INMET em MS.

Ferramenta de análise, separada dos produtos: aqui não se gera planilha nem mapa, se olha o dado
para entender tendências e conferir a qualidade da medição. Os produtos continuam sendo gerados
pelo main.py, e nada aqui altera o que eles produzem.

Para abrir:  streamlit run app/explorador.py
"""
import io
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
from matplotlib.figure import Figure

from app import chuva as chuva_calc
from app import dados as coleta
from app import qualidade
from app import superficie
from app import variaveis
from modulos import config, inmet, mapas

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
# O que já vem escolhido: o trio do boletim. Quem quiser outro troca no seletor.
PADRAO_MAPA = {
    variaveis.HORA: ["Temperatura na hora cheia", "Umidade na hora cheia", "Rajada na hora"],
    variaveis.DIA: ["Temperatura máxima", "Temperatura mínima", "Umidade mínima"],
    variaveis.PERIODO: ["Temperatura máxima", "Temperatura mínima", "Umidade mínima"],
}
# Acumulados que já vêm escolhidos na aba da chuva: os três do boletim
PADRAO_CHUVA = ["24 h", "48 h", "72 h"]
DPI_MAPA = 150       # serve para a tela e para o PNG baixado: um desenho só, codificado uma vez
MAPAS_POR_LINHA = 3  # acima disso cada mapa fica estreito demais para se lerem os valores
# Mapa navegável: enquadramento inicial em MS e o mapa base (Carto, sem chave de acesso)
VISAO_INICIAL = {"latitude": -20.5, "longitude": -54.5, "zoom": 5.9}
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

# O streamlit redireciona a saída dos módulos, e no Windows ela vai em cp1252: sem isto, o
# primeiro aviso com emoji (uma nova tentativa na API, por exemplo) derruba a tela inteira.
for _fluxo in (sys.stdout, sys.stderr):
    if hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(encoding="utf-8", errors="replace")

st.set_page_config(page_title="Painel Meteorológico", page_icon="🌡️", layout="wide")


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_estacoes() -> pd.DataFrame:
    return inmet.listar_estacoes()


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_leituras(codigos: tuple[str, ...], nomes: tuple[str, ...],
                      inicio: datetime, fim: datetime) -> tuple[pd.DataFrame, list[str]]:
    """Séries das estações escolhidas, com a lista das que falharam."""
    series, falharam = [], []
    for codigo, nome in zip(codigos, nomes):
        try:
            leituras = coleta.leituras(codigo, inicio, fim)
        except inmet.ErroINMET:
            falharam.append(nome)
            continue
        if leituras.empty:
            falharam.append(nome)
            continue
        series.append(leituras.assign(Estação=nome))
    tabela = pd.concat(series, ignore_index=True) if series else pd.DataFrame()
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


def desenhar(longo: pd.DataFrame, rotulo_y: str, zero_na_base: bool, modo: str, casas: int = 1) -> alt.Chart:
    """As séries de uma grandeza no tempo: cor separa a estação, traço separa a série.

    Cinco estações com três séries dariam quinze linhas iguais. Cor para a estação e traço para
    a série (máxima, mínima, média) deixa as duas leituras possíveis no mesmo desenho; clicar na
    legenda isola uma série.
    """
    # Arrastar move e Shift+roda aproxima. Sem o Shift, a roda do mouse em cima do gráfico
    # aproximaria em vez de rolar a página, e quem passa por vários gráficos fica preso no
    # primeiro (é o que o .interactive() faz por padrão).
    navegar = alt.selection_interval(bind="scales", zoom="wheel![event.shiftKey]")
    isolar = alt.selection_point(fields=["Série"], bind="legend")
    # Hora em cima e data embaixo, como nos meteogramas; por dia, só a data — e aí uma marca por
    # dia, senão o eixo marca de meio em meio dia e a mesma data aparece duas vezes.
    por_dia = modo == variaveis.DIA
    formato = ("[timeFormat(datum.value, '%d/%m')]" if por_dia
               else "[timeFormat(datum.value, '%H:%M'), timeFormat(datum.value, '%d/%m')]")
    return (alt.Chart(longo)
            .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(filled=True, size=32))
            .encode(x=alt.X("dt_local:T", title=None,
                            axis=alt.Axis(grid=True, gridOpacity=0.25, labelAngle=0, labelExpr=formato,
                                          labelFontSize=10, tickCount="day" if por_dia else alt.Undefined)),
                    y=alt.Y("valor:Q", title=rotulo_y, scale=alt.Scale(zero=zero_na_base),
                            axis=alt.Axis(grid=True, gridOpacity=0.25)),
                    color=alt.Color("Estação:N", title=None, legend=alt.Legend(orient="bottom")),
                    strokeDash=alt.StrokeDash("Série:N", title=None, legend=alt.Legend(orient="bottom")),
                    opacity=alt.condition(isolar, alt.value(1), alt.value(0.12)),
                    tooltip=[alt.Tooltip("Estação:N"), alt.Tooltip("Série:N"),
                             alt.Tooltip("dt_local:T", title="Quando",
                                         format="%d/%m" if por_dia else "%d/%m %H:%M"),
                             alt.Tooltip("valor:Q", title="Valor", format=f".{casas}f")])
            .properties(height=320)
            .add_params(navegar, isolar))


def cascata_da_chuva(barras: pd.DataFrame, por_dia: bool) -> alt.Chart:
    """Quanto choveu em cada passo, empilhado no que já tinha caído, e a barra do total.

    Cada barra começa onde a anterior terminou, então a altura da última diz o acumulado sem
    ninguém somar de cabeça. A do total sai do chão, para comparar de relance.
    """
    ordem = list(barras.sort_values("ordem")["Passo"].unique())
    return (alt.Chart(barras)
            .mark_bar(size=14 if por_dia else 8)
            .encode(x=alt.X("Passo:N", title=None, sort=ordem,
                            axis=alt.Axis(labelAngle=-45, labelFontSize=9)),
                    y=alt.Y("base:Q", title="Chuva (mm)",
                            axis=alt.Axis(grid=True, gridOpacity=0.25, format=".1f")),
                    y2="topo:Q",
                    color=alt.Color("tipo:N", title=None,
                                    scale=alt.Scale(domain=["passo", "total"], range=["#6baed6", "#08306b"]),
                                    legend=None),
                    tooltip=[alt.Tooltip("Estação:N"), alt.Tooltip("Passo:N", title="Quando"),
                             alt.Tooltip("valor:Q", title="Chuva (mm)", format=".1f"),
                             alt.Tooltip("topo:Q", title="Acumulado (mm)", format=".1f")])
            .properties(height=220)
            .facet(facet=alt.Facet("Estação:N", title=None), columns=2))


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


@st.cache_resource(show_spinner=False)
def base_cartografica() -> mapas.BaseCartografica:
    """Shapefiles, grade e máscara do estado: pesados de ler e iguais para todo mundo."""
    return mapas.carregar_base()


@st.cache_data(show_spinner=False, max_entries=30)
def mapa_do_instante(valores: pd.Series, titulo: str, unidade: str, paleta: str, decimais: int,
                     quando: str, rotulos: bool) -> bytes | None:
    """PNG do mapa interpolado de um instante, ou None se faltarem estações para interpolar.

    É o mesmo desenho dos produtos — mesmo IDW, mesmo recorte pelo estado, mesmas cores —, só que
    sem a moldura institucional: na tela, título e logos tomariam o lugar do mapa. Fica em cache
    porque cada passo do deslizante redesenha uma variável por vez: voltar a uma hora já vista não
    paga o desenho de novo.
    """
    gdf = mapas.preparar_pontos(_com_coordenadas(valores, titulo), titulo, quando)
    if gdf is None or len(gdf) < config.MIN_ESTACOES_INTERPOLACAO:
        return None

    espec = mapas.EspecMapa(tabela="", coluna=titulo, titulo=titulo, subtitulo=quando, arquivo="",
                            cmap=paleta, ranking="", decimais=decimais, unidade=f"{titulo} ({unidade})")
    figura = mapas.mapa_interpolado(gdf, espec, base_cartografica(), tela=mapas.Tela(rotulos=rotulos))
    if figura is None:
        return None
    # Codifica uma vez só: os mesmos bytes vão para a tela e para o botão de baixar
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=DPI_MAPA, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


@st.cache_resource(show_spinner=False)
def malha_fina() -> superficie.Malha:
    """Grade e máscara do estado do mapa navegável: dependem só da resolução, não do dado."""
    return superficie.malha(base_cartografica().uf.geometry.union_all())


@st.cache_data(show_spinner=False, max_entries=30)
def camada_superficie(valores: pd.Series, nome_produto: str, quando: str) -> str:
    """A superfície do instante como imagem, pronta para virar camada do mapa."""
    produto = variaveis.por_nome(nome_produto)
    pontos = _com_coordenadas(valores, nome_produto)
    return superficie.como_uri(
        superficie.superficie_png(pontos, nome_produto, malha_fina(), produto.paleta))


def _com_coordenadas(valores: pd.Series, nome_variavel: str) -> pd.DataFrame:
    """Valores de um instante com a latitude e a longitude de cada estação."""
    coordenadas = (carregar_estacoes().set_index("Estação")[["VL_LATITUDE", "VL_LONGITUDE"]]
                   .rename(columns={"VL_LATITUDE": "Latitude", "VL_LONGITUDE": "Longitude"}))
    return coordenadas.join(valores.rename(nome_variavel), how="inner").reset_index().dropna()


def painel_do_mapa(produto: variaveis.Produto, valores: pd.Series, rotulo: str, carimbo: str,
                   rotulos: bool) -> None:
    """Uma coluna da linha de mapas: nome do produto, desenho, a regra e o botão de baixar."""
    st.markdown(f"**{produto.nome}**")
    medida = produto.nome.lower()
    if valores.dropna().empty:
        st.info(f"Nenhuma estação mediu {medida} em {rotulo}.")
        return

    png = mapa_do_instante(valores, produto.nome, produto.unidade, produto.paleta,
                           produto.decimais, rotulo, rotulos)
    if png is None:
        st.warning(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram {medida} em {rotulo}: "
                   "com tão poucos pontos a superfície inventaria mais do que mostra.")
        return

    st.image(png, width="stretch")
    # Sem a barra de cores no desenho, é esta linha que diz o que as cores valem — e a regra
    # do produto vai junto, para ninguém precisar adivinhar que conta é aquela.
    casas = produto.decimais
    st.caption(f"{valores.min():.{casas}f} a {valores.max():.{casas}f} {produto.unidade} · "
               f"{valores.notna().sum()} estações · {produto.regra}.")
    arquivo = produto.nome.lower().replace(" ", "_")
    st.download_button(f"Baixar PNG — {medida}", png, mime="image/png", key=f"baixar_{produto.nome}",
                       file_name=f"Mapa_{arquivo}_{carimbo}.png")


def painel_da_chuva(rotulo: str, valores: pd.Series, janela: str, rotulos: bool) -> None:
    """Uma coluna da linha de acumulados: quanto choveu na janela que termina no fim do período."""
    st.markdown(f"**Chuva {rotulo}**")
    if valores.dropna().empty:
        st.info(f"Nenhuma estação mediu chuva em {janela}.")
        return

    png = mapa_do_instante(valores, f"Chuva {rotulo}", "mm", "Blues", 1, janela, rotulos)
    if png is None:
        st.warning(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram chuva em {janela}.")
        return

    st.image(png, width="stretch")
    st.caption(f"{valores.min():.1f} a {valores.max():.1f} mm · {valores.notna().sum()} estações · "
               f"soma das horas de {janela}.")
    st.download_button(f"Baixar PNG — chuva {rotulo}", png, mime="image/png",
                       key=f"baixar_chuva_{rotulo}",
                       file_name=f"Mapa_chuva_{rotulo.replace(' ', '')}.png")


# =====================================================
# FILTROS
# =====================================================
st.title("Painel Meteorológico")

if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
    st.error("Token do INMET não configurado. Preencha `TOKEN_INMET` no arquivo `.env` e recarregue a página.")
    st.stop()

with st.sidebar:
    st.header("Filtros")
    hoje = date.today()
    intervalo = st.date_input("Período", value=(hoje - timedelta(days=7), hoje - timedelta(days=1)),
                              max_value=hoje, format="DD/MM/YYYY")
    if len(intervalo) != 2:
        st.info("Escolha a data inicial e a final.")
        st.stop()

    estacoes = carregar_estacoes().sort_values("Estação")
    # Sem estação escolhida de saída: quem abre decide o que quer ver, e nenhuma consulta
    # à API acontece antes disso.
    nomes = st.multiselect("Estações", estacoes["Estação"].tolist(), default=[],
                           help="Cada estação vira uma linha no gráfico. Os mapas usam sempre as "
                                "62 estações do estado.")
    # Uma grandeza dá um gráfico, com as suas séries dentro (máxima, mínima, média): escalas
    # diferentes nunca se misturam num eixo só.
    escolhidas = st.multiselect("Grandezas", variaveis.grandezas(variaveis.HORA, variaveis.GRAFICO),
                                default=["Temperatura", "Chuva", "Vento"],
                                help="Cada grandeza ganha o seu gráfico, com as séries que a equipe "
                                     "de meteorologia definiu.")
    modo_grafico = MODOS_GRAFICO[st.radio("Agregação", list(MODOS_GRAFICO), horizontal=True)]

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
inicio = datetime.combine(intervalo[0], datetime.min.time(), tzinfo=config.FUSO_MS).astimezone(config.FUSO_UTC)
fim = (datetime.combine(intervalo[1], datetime.min.time(), tzinfo=config.FUSO_MS)
       + timedelta(days=1)).astimezone(config.FUSO_UTC)
codigos = tuple(estacoes.set_index("Estação").loc[nomes, "CD_ESTACAO"])

with st.spinner("Consultando o INMET (o que já estiver em cache não é baixado de novo)..."):
    tabela, falharam = carregar_leituras(codigos, tuple(nomes), inicio, fim)

if tabela.empty:
    st.warning("Nenhuma das estações escolhidas tem dados nesse período.")
    st.stop()
if falharam:
    st.warning(f"Sem dados ou falha na consulta: {', '.join(falharam)}")

horas_esperadas = int((fim - inicio).total_seconds() // 3600)
periodo_escolhido = (f"{intervalo[0]:%d/%m/%Y}" if intervalo[0] == intervalo[1]
                     else f"{intervalo[0]:%d/%m/%Y} a {intervalo[1]:%d/%m/%Y}")
aba_series, aba_mapa, aba_chuva, aba_navegavel, aba_qualidade = st.tabs(
    ["Estações: Séries Temporais", "Mapas Boletim", "Chuva", "Mapa Navegação", "Qualidade dos dados"])

# =====================================================
# SÉRIES TEMPORAIS
# =====================================================
with aba_series:
    st.caption(f"{len(tabela)} leituras de {tabela['Estação'].nunique()} estações · "
               f"{len(tabela) / (horas_esperadas * len(nomes)):.0%} das horas do período têm registro")
    if not escolhidas:
        st.info("Escolha ao menos uma variável na barra lateral.")

    for grandeza in escolhidas:
        if grandeza == "Chuva":
            # A chuva não sai em linha: o que se lê dela é quanto caiu em cada passo e quanto
            # somou no fim — é a cascata que o boletim usa.
            por_dia_chuva = modo_grafico == variaveis.DIA
            barras = chuva_calc.cascata(tabela, por_dia=por_dia_chuva)
            st.subheader("Chuva")
            if barras.empty:
                st.warning("Chuva: a API não devolveu essa medição no período.")
            elif barras["valor"].sum() == 0:
                # Um quadro vazio parece defeito; a frase deixa claro que o dado existe e é zero
                st.info("Não choveu em nenhuma das estações escolhidas " +
                        ("no período." if por_dia_chuva else "nas últimas 24 horas do período."))
            else:
                st.altair_chart(cascata_da_chuva(barras, por_dia_chuva), width="stretch")
                st.caption("Cada barra é a chuva daquele passo, empilhada no que já tinha caído; a barra "
                           "escura no fim é o total. " +
                           ("Um dia por barra." if por_dia_chuva
                            else "As últimas 24 horas do período — uma semana daria 168 barras."))
            continue

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
        # As colunas cruas por trás das grandezas escolhidas, sem repetir e na ordem do catálogo
        das_grandezas = [coluna for produto in variaveis.disponiveis(modo_grafico, variaveis.GRAFICO)
                         if produto.grandeza in escolhidas
                         for coluna in produto.colunas if coluna in tabela]
        colunas = ["Estação", "dt_local"] + list(dict.fromkeys(das_grandezas))
        visivel = tabela[colunas].rename(columns={"dt_local": "Data/Hora (MS)"})
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
        if st.button("Carregar todas as estações de MS", type="primary"):
            st.session_state["mapa_liberado"] = True
            st.rerun()
    else:
        with st.spinner(f"Consultando as {len(estacoes)} estações do estado..."):
            leituras_mapa, ausentes_mapa = carregar_leituras(tuple(estacoes["CD_ESTACAO"]),
                                                             tuple(estacoes["Estação"]), inicio, fim)
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
            default=[nome for nome in PADRAO_MAPA[modo] if nome in {p.nome for p in catalogo}],
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

                rotulos = st.checkbox("Mostrar o valor de cada estação", value=False,
                                      help="Lado a lado os valores se cobrem. Ligue quando for ampliar "
                                           "um mapa ou baixar o PNG.")

                fatia = variaveis.recorte(leituras_mapa, modo, momento)
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
                                painel_do_mapa(produto, variaveis.por_estacao(produto, fatia),
                                               rotulo, carimbo, rotulos)

                st.caption(f"Interpolação IDW (potência {config.IDW_POTENCIA}, {config.IDW_VIZINHOS} vizinhos) "
                           f"sobre as {len(estacoes)} estações do estado, a mesma dos relatórios. Neste tamanho "
                           "o mapa mostra o padrão, e a faixa de valores vai escrita sob cada um; para ver "
                           "estação por estação, ligue a caixa acima e amplie o mapa no ícone de tela cheia.")
                if ausentes_mapa:
                    st.caption(f"Sem dados no período ({len(ausentes_mapa)}): {', '.join(ausentes_mapa)}")

# =====================================================
# CHUVA
# =====================================================
# A chuva tem aba própria porque é a única que precisa de dado fora do período escolhido: o
# acumulado de 96 h, e o do mês, começam antes do início da janela da barra lateral.
with aba_chuva:
    inicio_chuva = chuva_calc.inicio_necessario(fim.astimezone(config.FUSO_MS))
    if not st.session_state.get("chuva_liberada"):
        st.info(f"Os acumulados olham para trás do período escolhido: para fechar o do mês, a consulta "
                f"vai até **{inicio_chuva:%d/%m}**. São as {len(estacoes)} estações do estado, e na "
                "primeira vez demora; depois vem do cache.")
        if st.button("Carregar a chuva do mês", type="primary"):
            st.session_state["chuva_liberada"] = True
            st.rerun()
    else:
        with st.spinner(f"Consultando a chuva desde {inicio_chuva:%d/%m}..."):
            leituras_chuva, ausentes_chuva = carregar_leituras(
                tuple(estacoes["CD_ESTACAO"]), tuple(estacoes["Estação"]),
                inicio_chuva.astimezone(config.FUSO_UTC), fim)

        if leituras_chuva.empty or chuva_calc.COLUNA not in leituras_chuva:
            st.warning("A API não devolveu chuva no período.")
        else:
            fim_local = leituras_chuva["dt_local"].max()
            modo_chuva = st.radio("Mapas", ["Acumulados", "Hora a hora", "Por dia"], horizontal=True,
                                  key="modo_chuva")

            if modo_chuva == "Acumulados":
                escolhidos = st.multiselect("Janelas", chuva_calc.rotulos(), default=PADRAO_CHUVA,
                                            key="janelas_chuva",
                                            help="Cada janela conta para trás a partir do fim do período. "
                                                 "O mensal começa no dia 1º.")
                curtas = [rotulo for rotulo in escolhidos
                          if not chuva_calc.cobre(leituras_chuva, fim_local, rotulo)]
                if curtas:
                    st.warning(f"Sem dado que alcance o começo de: {', '.join(curtas)}. "
                               "Esses acumulados mostrariam menos chuva do que caiu.")
                mostrar = [rotulo for rotulo in escolhidos if rotulo not in curtas]
                rotulos_chuva = st.checkbox("Mostrar o valor de cada estação", value=False,
                                            key="valores_chuva")

                por_linha = min(len(mostrar), MAPAS_POR_LINHA) if mostrar else 0
                with st.spinner("Desenhando os mapas..."):
                    for primeiro in range(0, len(mostrar), por_linha):
                        for coluna_tela, rotulo in zip(st.columns(por_linha),
                                                       mostrar[primeiro:primeiro + por_linha]):
                            with coluna_tela:
                                comeco, _ = chuva_calc.janela(fim_local, rotulo)
                                painel_da_chuva(rotulo,
                                                chuva_calc.acumulado(leituras_chuva, fim_local, rotulo),
                                                f"{comeco:%d/%m %H:%M} a {fim_local:%d/%m %H:%M}",
                                                rotulos_chuva)
                if mostrar:
                    st.caption(f"Acumulados até {fim_local:%d/%m %H:%M}, somando a chuva de cada hora. "
                               f"Interpolação IDW (potência {config.IDW_POTENCIA}, "
                               f"{config.IDW_VIZINHOS} vizinhos), a mesma dos relatórios.")
            else:
                # Hora a hora e por dia respeitam o período escolhido, como as outras abas
                modo = variaveis.HORA if modo_chuva == "Hora a hora" else variaveis.DIA
                do_periodo = leituras_chuva[leituras_chuva["dt_local"] > inicio.astimezone(config.FUSO_MS)]
                paradas = variaveis.momentos(do_periodo, modo)
                produto = variaveis.por_nome("Chuva na hora" if modo == variaveis.HORA else "Chuva acumulada")
                if not paradas:
                    st.warning("Sem leituras no período escolhido.")
                else:
                    formatar = ((lambda marca: f"{marca:%d/%m/%Y}") if modo == variaveis.DIA
                                else (lambda marca: f"{marca:%d/%m %H:%M}"))
                    momento = st.select_slider("Quando", options=paradas, value=paradas[-1],
                                               format_func=formatar, key=f"quando_chuva_{modo}")
                    rotulos_chuva = st.checkbox("Mostrar o valor de cada estação", value=False,
                                                key="valores_chuva_momento")
                    carimbo = (f"{momento:%Y%m%d}" if modo == variaveis.DIA else f"{momento:%Y%m%d_%H}h")
                    fatia = variaveis.recorte(do_periodo, modo, momento)
                    _, meio, _ = st.columns([1, 2, 1])
                    with meio:
                        painel_do_mapa(produto, variaveis.por_estacao(produto, fatia),
                                       formatar(momento), carimbo, rotulos_chuva)

            if ausentes_chuva:
                st.caption(f"Sem dados no período ({len(ausentes_chuva)}): {', '.join(ausentes_chuva)}")

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
        with st.spinner(f"Consultando as {len(estacoes)} estações do estado..."):
            leituras_navegavel, _ = carregar_leituras(tuple(estacoes["CD_ESTACAO"]),
                                                      tuple(estacoes["Estação"]), inicio, fim)
        modo_nav = MODOS_MAPA[st.radio("Agregação", list(MODOS_MAPA), horizontal=True, key="modo_navegavel")]
        catalogo_nav = [produto for produto in variaveis.disponiveis(modo_nav, variaveis.MAPA)
                        if produto.grandeza != "Chuva"
                        and all(coluna in leituras_navegavel for coluna in produto.colunas)]

        if not catalogo_nav:
            st.warning("A API não devolveu nenhuma dessas medições no período.")
        else:
            # Um mapa por vez: aqui a comparação lado a lado dá lugar ao zoom
            nome_nav = st.selectbox("Mapa", [produto.nome for produto in catalogo_nav], key="mapa_navegavel")
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
                with st.spinner("Desenhando o mapa..."):
                    imagem = camada_superficie(valores_nav, produto_nav.nome, rotulo_nav)
                pontos = _com_coordenadas(valores_nav, produto_nav.nome)
                pontos["Valor"] = pontos[produto_nav.nome].map(
                    lambda valor: f"{valor:.{produto_nav.decimais}f} {produto_nav.unidade}")
                # A imagem entra depois de criada a camada: passada no construtor, o pydeck a
                # trataria como expressão a ser avaliada no navegador ("@@=data:image/png;...").
                campo = pdk.Layer("BitmapLayer", data=None, bounds=superficie.limites())
                campo.image = imagem
                camadas = [
                    campo,
                    pdk.Layer("GeoJsonLayer", data=base_cartografica().uf.__geo_interface__,
                              stroked=True, filled=False, get_line_color=[40, 40, 40], line_width_min_pixels=1),
                    pdk.Layer("ScatterplotLayer", data=pontos, get_position=["Longitude", "Latitude"],
                              get_fill_color=[20, 20, 20, 200], get_line_color=[255, 255, 255],
                              line_width_min_pixels=1, stroked=True, radius_min_pixels=4,
                              get_radius=2500, pickable=True),
                ]
                if st.checkbox("Mostrar o valor de cada estação", value=False, key="valores_navegavel",
                               help="Aproxime para os valores deixarem de se cobrir."):
                    camadas.append(
                        pdk.Layer("TextLayer", data=pontos, get_position=["Longitude", "Latitude"],
                                  get_text="Valor", get_size=12,
                                  get_color=[20, 20, 20], get_pixel_offset=[0, -14],
                                  background=True, get_background_color=[255, 255, 255, 200]))

                st.markdown(CENTRALIZAR_MAPA, unsafe_allow_html=True)
                st.pydeck_chart(pdk.Deck(layers=camadas, map_style=MAPA_BASE,
                                         initial_view_state=pdk.ViewState(**VISAO_INICIAL),
                                         tooltip={"text": "{Estação} — {Valor}"}),
                                width=LARGURA_MAPA, height=ALTURA_MAPA)
                casas = produto_nav.decimais
                st.caption(f"{valores_nav.min():.{casas}f} a {valores_nav.max():.{casas}f} "
                           f"{produto_nav.unidade} · {valores_nav.notna().sum()} estações · "
                           f"{produto_nav.regra}, em {rotulo_nav}. O mapa base vem do Carto, fora da SEMADESC.")

# =====================================================
# QUALIDADE DOS DADOS
# =====================================================
with aba_qualidade:
    todas = st.checkbox("Analisar todas as estações de MS", value=False,
                        help="Ignora a seleção da barra lateral. Na primeira vez demora, porque baixa tudo; "
                             "depois vem do cache.")
    if todas:
        with st.spinner(f"Consultando as {len(estacoes)} estações do estado..."):
            base, ausentes = carregar_leituras(tuple(estacoes["CD_ESTACAO"]), tuple(estacoes["Estação"]), inicio, fim)
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
