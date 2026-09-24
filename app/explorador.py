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
import streamlit as st
from matplotlib.figure import Figure

from app import dados as coleta
from app import qualidade
from app.series import FUNCOES, agregar
from modulos import config, inmet, mapas

# Nome que aparece na tela -> coluna da API. A ordem é a que aparece na lista.
VARIAVEIS = {
    "Temperatura (°C)": "TEM_INS",
    "Temperatura máxima (°C)": "TEM_MAX",
    "Temperatura mínima (°C)": "TEM_MIN",
    "Umidade relativa (%)": "UMD_INS",
    "Umidade mínima (%)": "UMD_MIN",
    "Chuva (mm)": "CHUVA",
    "Radiação global (kJ/m²)": "RAD_GLO",
    "Vento (km/h)": "VEN_VEL",
    "Rajada (km/h)": "VEN_RAJ",
    "Direção do vento (°)": "VEN_DIR",
    "Pressão (hPa)": "PRE_INS",
    "Ponto de orvalho (°C)": "PTO_INS",
}
# A API manda o vento em m/s; os produtos trabalham em km/h, e aqui seguimos a mesma unidade
CONVERSOES = {"VEN_VEL": 3.6, "VEN_RAJ": 3.6}
# Direção é ângulo: entre 350° e 10° o vento mal mudou, mas uma linha desceria o gráfico inteiro
COLUNAS_CIRCULARES = {"VEN_DIR"}
FAIXAS_VENTO = [(0, 10), (10, 20), (20, 30), (30, float("inf"))]
CORES_VENTO = ["#c6dbef", "#6baed6", "#2171b5", "#08306b"]  # sequencial: claro = fraco, escuro = forte
# Nomes curtos para o eixo dos mapas de calor da qualidade, onde os códigos da API não ajudam
NOMES_CURTOS = {
    "TEM_INS": "Temperatura", "TEM_MAX": "Temp. máx.", "TEM_MIN": "Temp. mín.", "PTO_INS": "Orvalho",
    "UMD_INS": "Umidade", "UMD_MAX": "Umid. máx.", "UMD_MIN": "Umid. mín.", "CHUVA": "Chuva",
    "RAD_GLO": "Radiação", "PRE_INS": "Pressão", "VEN_VEL": "Vento", "VEN_RAJ": "Rajada", "VEN_DIR": "Direção",
}
# Paleta de cada variável no mapa, seguindo a dos produtos: quem já conhece os relatórios lê o
# mapa da tela sem precisar reaprender as cores. A direção do vento não entra — interpolar ângulo
# entre 350° e 10° daria 180°, o rumo oposto.
PALETAS = {
    "Temperatura (°C)": "RdYlBu_r", "Temperatura máxima (°C)": "YlOrRd", "Temperatura mínima (°C)": "coolwarm",
    "Umidade relativa (%)": "YlGnBu", "Umidade mínima (%)": "YlGnBu", "Chuva (mm)": "Blues",
    "Radiação global (kJ/m²)": "YlOrRd", "Vento (km/h)": "turbo", "Rajada (km/h)": "turbo",
    "Pressão (hPa)": "viridis", "Ponto de orvalho (°C)": "YlGnBu",
}
DPI_MAPA = 150       # serve para a tela e para o PNG baixado: um desenho só, codificado uma vez
MAPAS_POR_LINHA = 3  # acima disso cada mapa fica estreito demais para se lerem os valores

# Variáveis em que o zero é uma referência de verdade (não chover é zero). Nas outras, forçar o
# eixo a começar no zero achataria a variação: 25 a 30 °C viraria um risco reto.
ZERO_NA_BASE = {"CHUVA", "RAD_GLO", "VEN_VEL", "VEN_RAJ"}

st.set_page_config(page_title="Explorador CEMTEC", page_icon="🌡️", layout="wide")


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


def desenhar(serie: pd.DataFrame, nome: str, zero_na_base: bool, circular: bool = False,
             por_dia: bool = False) -> alt.Chart:
    """Uma série por estação, com zoom por arrasto e valor ao passar o mouse.

    O traço leva um ponto em cada leitura, para dar para contar as horas medidas e enxergar
    falha no meio da série. Variáveis circulares (direção do vento) saem só em pontos: ligar
    350° a 10° com uma linha desenharia uma volta inteira que não aconteceu.
    """
    # Arrastar move e Shift+roda aproxima. Sem o Shift, a roda do mouse em cima do gráfico
    # aproximaria em vez de rolar a página, e quem passa por vários gráficos fica preso no
    # primeiro (é o que o .interactive() faz por padrão).
    navegar = alt.selection_interval(bind="scales", zoom="wheel![event.shiftKey]")
    longo = serie.reset_index().melt("dt_local", var_name="Estação", value_name="valor").dropna()
    base = alt.Chart(longo)
    marca = (base.mark_point(size=22, filled=True, opacity=0.75) if circular
             else base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(filled=True, size=32)))
    # Hora em cima e data embaixo, como nos meteogramas; por dia, só a data — e aí uma marca por
    # dia, senão o eixo marca de meio em meio dia e a mesma data aparece duas vezes. De hora em
    # hora o espaçamento fica por conta do eixo: pedir de 3 em 3 horas (tickCount={"interval":
    # "hour", "step": 3}) quebra o desenho na versão do Vega-Lite que o Streamlit embute.
    formato = ("[timeFormat(datum.value, '%d/%m')]" if por_dia
               else "[timeFormat(datum.value, '%H:%M'), timeFormat(datum.value, '%d/%m')]")
    marcas = "day" if por_dia else alt.Undefined
    # A direção vai de 0° a 360° e só isso: deixada solta, a escala sobraria até 400°
    escala = (alt.Scale(domain=[0, 360]) if circular else alt.Scale(zero=zero_na_base))
    rumos = [0, 90, 180, 270, 360] if circular else alt.Undefined
    return (marca
            .encode(x=alt.X("dt_local:T", title=None,
                            axis=alt.Axis(grid=True, gridOpacity=0.25, labelAngle=0, labelExpr=formato,
                                          labelFontSize=10, tickCount=marcas)),
                    y=alt.Y("valor:Q", title=nome, scale=escala,
                            axis=alt.Axis(grid=True, gridOpacity=0.25, values=rumos)),
                    color=alt.Color("Estação:N", title=None, legend=alt.Legend(orient="bottom")),
                    tooltip=[alt.Tooltip("Estação:N"),
                             alt.Tooltip("dt_local:T", title="Quando", format="%d/%m %H:%M"),
                             alt.Tooltip("valor:Q", title=nome, format=".1f")])
            .properties(height=300)
            .add_params(navegar))


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
    ax.set_title(nome_estacao, fontsize=8.5, color="#9a9a9a", pad=12)
    ax.tick_params(colors="#9a9a9a", labelsize=8)
    ax.grid(color="#9a9a9a", alpha=0.25)
    ax.spines["polar"].set_color("#9a9a9a")
    ax.spines["polar"].set_alpha(0.3)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    legenda = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.04), ncols=2, fontsize=7.5,
                        frameon=False, handlelength=1.0, columnspacing=1.0, title="km/h", title_fontsize=7.5)
    legenda.get_title().set_color("#9a9a9a")
    for texto in legenda.get_texts():
        texto.set_color("#9a9a9a")
    return fig


@st.cache_resource(show_spinner=False)
def base_cartografica() -> mapas.BaseCartografica:
    """Shapefiles, grade e máscara do estado: pesados de ler e iguais para todo mundo."""
    return mapas.carregar_base()


@st.cache_data(show_spinner=False, max_entries=30)
def mapa_do_instante(valores: pd.Series, nome_variavel: str, quando: str, rotulos: bool) -> bytes | None:
    """PNG do mapa interpolado de um instante, ou None se faltarem estações para interpolar.

    É o mesmo desenho dos produtos — mesmo IDW, mesmo recorte pelo estado, mesmas cores —, só que
    sem a moldura institucional: na tela, título e logos tomariam o lugar do mapa. Fica em cache
    porque cada passo do deslizante redesenha uma variável por vez: voltar a uma hora já vista não
    paga o desenho de novo.
    """
    coordenadas = (carregar_estacoes().set_index("Estação")[["VL_LATITUDE", "VL_LONGITUDE"]]
                   .rename(columns={"VL_LATITUDE": "Latitude", "VL_LONGITUDE": "Longitude"}))
    pontos = coordenadas.join(valores.rename(nome_variavel), how="inner").reset_index()
    gdf = mapas.preparar_pontos(pontos, nome_variavel, quando)
    if gdf is None or len(gdf) < config.MIN_ESTACOES_INTERPOLACAO:
        return None

    espec = mapas.EspecMapa(tabela="", coluna=nome_variavel, titulo=nome_variavel, subtitulo=quando,
                            arquivo="", cmap=PALETAS[nome_variavel], unidade=nome_variavel, ranking="")
    figura = mapas.mapa_interpolado(gdf, espec, base_cartografica(), tela=mapas.Tela(rotulos=rotulos))
    if figura is None:
        return None
    # Codifica uma vez só: os mesmos bytes vão para a tela e para o botão de baixar
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=DPI_MAPA, bbox_inches="tight", facecolor="white")
    return arquivo.getvalue()


def painel_do_mapa(nome_variavel: str, serie: pd.DataFrame, momento, rotulo: str, quando: str,
                   rotulos: bool) -> None:
    """Uma coluna da linha de mapas: nome da variável, desenho, a faixa de valores e o botão de baixar."""
    st.markdown(f"**{nome_variavel}**")
    medida = nome_variavel.split(" (")[0].lower()  # "Temperatura (°C)" -> "temperatura"
    if momento not in serie.index:
        st.info(f"Sem medição de {medida} {quando}.")
        return

    valores = serie.loc[momento]
    png = mapa_do_instante(valores, nome_variavel, rotulo, rotulos)
    if png is None:
        st.warning(f"Menos de {config.MIN_ESTACOES_INTERPOLACAO} estações mediram {medida} em {rotulo}: "
                   "com tão poucos pontos a superfície inventaria mais do que mostra.")
        return

    st.image(png, width="stretch")
    # Sem a barra de cores no desenho, é esta linha que diz o que as cores valem
    unidade = nome_variavel.split("(")[-1].rstrip(")")
    st.caption(f"{valores.min():.1f} a {valores.max():.1f} {unidade} · "
               f"{valores.notna().sum()} estações mediram {medida} {quando}.")
    carimbo = f"{momento:%Y%m%d}" if quando == "nesse dia" else f"{momento:%Y%m%d_%H}h"
    st.download_button(f"Baixar PNG — {medida}", png, mime="image/png", key=f"baixar_{nome_variavel}",
                       file_name=f"Mapa_{medida.replace(' ', '_')}_{carimbo}.png")


# =====================================================
# FILTROS
# =====================================================
st.title("Explorador — estações automáticas do INMET em MS")

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
                           help="Cada estação vira uma linha no gráfico.")
    escolhidas = st.multiselect("Variáveis", list(VARIAVEIS),
                                default=["Temperatura (°C)", "Chuva (mm)", "Rajada (km/h)"],
                                help="Cada variável ganha o seu próprio gráfico: escalas diferentes não se misturam.")
    por_dia = st.radio("Agregação", ["Hora a hora", "Por dia"], horizontal=True) == "Por dia"
    funcao = st.selectbox("Resumo do dia", list(FUNCOES), disabled=not por_dia,
                          help="Para chuva, use Soma; para as demais, Média, Máxima ou Mínima.")

    st.divider()
    guardadas, megabytes = coleta.tamanho_do_cache()
    st.caption(f"Cache local: {guardadas} estações, {megabytes:.1f} MB")
    if st.button("Limpar cache", width="stretch"):
        st.cache_data.clear()
        st.success(f"{coleta.limpar_cache()} arquivos removidos.")

if not nomes or not escolhidas:
    st.info("Escolha ao menos uma estação e uma variável na barra lateral.")
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
aba_series, aba_mapa, aba_qualidade = st.tabs(["Séries temporais", "Mapa", "Qualidade dos dados"])

# =====================================================
# SÉRIES TEMPORAIS
# =====================================================
with aba_series:
    st.caption(f"{len(tabela)} leituras de {tabela['Estação'].nunique()} estações · "
               f"{len(tabela) / (horas_esperadas * len(nomes)):.0%} das horas do período têm registro")

    for nome_variavel in escolhidas:
        coluna = VARIAVEIS[nome_variavel]
        if coluna not in tabela:
            st.warning(f"{nome_variavel}: a API não devolveu essa coluna no período.")
            continue
        circular = coluna in COLUNAS_CIRCULARES
        if circular and por_dia:
            st.subheader(nome_variavel)
            st.info("A direção não é resumida por dia: a média entre 350° e 10° daria 180°, que é o oposto. "
                    "Veja hora a hora ou use a rosa dos ventos abaixo.")
            continue
        serie = agregar(tabela, coluna, por_dia, funcao)
        if serie.empty:
            st.warning(f"{nome_variavel}: nenhuma estação escolhida tem essa medição no período.")
            continue
        st.subheader(nome_variavel)
        st.altair_chart(desenhar(serie, nome_variavel, coluna in ZERO_NA_BASE, circular, por_dia), width="stretch")

    if "VEN_DIR" in tabela and "VEN_VEL" in tabela:
        with st.expander("Rosa dos ventos do período"):
            st.caption("De onde o vento veio, em horas. Cada anel é uma faixa de velocidade; a faixa mais escura "
                       f"é a que interessa ao risco de fogo (≥ {FAIXAS_VENTO[-1][0]:g} km/h).")
            for coluna_tela, nome_estacao in zip(st.columns(min(len(nomes), 4)), nomes[:4]):
                with coluna_tela:
                    st.pyplot(rosa_dos_ventos(tabela, nome_estacao), width="stretch")
            if len(nomes) > 4:
                st.caption(f"Mostrando as 4 primeiras de {len(nomes)} estações escolhidas.")

    with st.expander("Ver e baixar os dados"):
        colunas = ["Estação", "dt_local"] + [VARIAVEIS[nome] for nome in escolhidas if VARIAVEIS[nome] in tabela]
        visivel = tabela[colunas].rename(columns={"dt_local": "Data/Hora (MS)"})
        st.dataframe(visivel, width="stretch", height=300)
        st.download_button("Baixar CSV", visivel.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"leituras_{intervalo[0]:%Y%m%d}_a_{intervalo[1]:%Y%m%d}.csv", mime="text/csv")

# =====================================================
# MAPA
# =====================================================
with aba_mapa:
    mapeaveis = [nome for nome in escolhidas if nome in PALETAS]
    if not mapeaveis:
        st.info("A direção do vento não entra no mapa: interpolar ângulo entre 350° e 10° daria 180°, "
                "o rumo oposto. Escolha outra variável na barra lateral.")
    elif not st.session_state.get("mapa_liberado"):
        st.info(f"O mapa interpola as {len(estacoes)} estações do estado, e não só as escolhidas na barra "
                "lateral. Na primeira vez a consulta demora alguns minutos; depois vem do cache.")
        if st.button("Carregar todas as estações de MS", type="primary"):
            st.session_state["mapa_liberado"] = True
            st.rerun()
    else:
        with st.spinner(f"Consultando as {len(estacoes)} estações do estado..."):
            leituras_mapa, ausentes_mapa = carregar_leituras(tuple(estacoes["CD_ESTACAO"]),
                                                             tuple(estacoes["Estação"]), inicio, fim)
        # Um mapa por variável escolhida, lado a lado: no mesmo instante, dá para ver a temperatura
        # alta bater com a umidade baixa sem trocar de tela.
        series = {nome: agregar(leituras_mapa, VARIAVEIS[nome], por_dia, funcao)
                  for nome in mapeaveis if VARIAVEIS[nome] in leituras_mapa}
        series = {nome: serie for nome, serie in series.items() if not serie.empty}
        sem_medicao = [nome for nome in mapeaveis if nome not in series]

        if not series:
            st.warning("A API não devolveu nenhuma dessas medições no período.")
        else:
            # O eixo do tempo é a união das variáveis: uma delas pode faltar em algumas horas
            momentos = sorted(set().union(*(serie.index for serie in series.values())))
            formatar = (lambda marca: f"{marca:%d/%m/%Y}") if por_dia else (lambda marca: f"{marca:%d/%m %H:%M}")
            # O deslizante anda no mesmo passo da barra lateral: de hora em hora ou de dia em dia,
            # e no modo diário o valor do mapa é o resumo escolhido (Média, Máxima, Mínima ou Soma).
            momento = st.select_slider("Quando", options=momentos, value=momentos[-1], format_func=formatar)
            rotulo, quando = formatar(momento), ("nesse dia" if por_dia else "nesse instante")
            rotulos = st.checkbox("Mostrar o valor de cada estação", value=False,
                                  help="Lado a lado os valores se cobrem. Ligue quando for ampliar um mapa "
                                       "ou baixar o PNG.")

            nomes = list(series)
            # Todas as linhas com a mesma quantidade de colunas: se a última fosse dimensionada
            # pelo que sobrou, os mapas dela sairiam maiores que os de cima. Com uma variável só,
            # a coluna é uma e o mapa ocupa a largura inteira.
            por_linha = min(len(nomes), MAPAS_POR_LINHA)
            with st.spinner("Desenhando os mapas..."):
                for primeiro in range(0, len(nomes), por_linha):
                    linha = nomes[primeiro:primeiro + por_linha]
                    for coluna_tela, nome_variavel in zip(st.columns(por_linha), linha):
                        with coluna_tela:
                            painel_do_mapa(nome_variavel, series[nome_variavel], momento, rotulo, quando,
                                           rotulos)

            st.caption(f"Interpolação IDW (potência {config.IDW_POTENCIA}, {config.IDW_VIZINHOS} vizinhos) sobre as "
                       f"{len(estacoes)} estações do estado, a mesma dos relatórios. Neste tamanho o mapa mostra "
                       "o padrão, e a faixa de valores vai escrita sob cada um; para ver estação por estação, "
                       "ligue a caixa acima e amplie o mapa no ícone de tela cheia.")
            if sem_medicao:
                st.caption(f"Fora do mapa, sem medição no período: {', '.join(sem_medicao)}")
            if ausentes_mapa:
                st.caption(f"Sem dados no período ({len(ausentes_mapa)}): {', '.join(ausentes_mapa)}")

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
