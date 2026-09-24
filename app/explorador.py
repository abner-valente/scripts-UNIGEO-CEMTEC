"""Explorador de séries temporais das estações automáticas do INMET em MS.

Ferramenta de análise, separada dos produtos: aqui não se gera planilha nem mapa, se olha o dado
para entender tendências e conferir a qualidade da medição. Os produtos continuam sendo gerados
pelo main.py, e nada aqui altera o que eles produzem.

Para abrir:  streamlit run app/explorador.py
"""
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
from modulos import config, inmet

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
FUNCOES = {"Média": "mean", "Máxima": "max", "Mínima": "min", "Soma": "sum"}
# Nomes curtos para o eixo dos mapas de calor da qualidade, onde os códigos da API não ajudam
NOMES_CURTOS = {
    "TEM_INS": "Temperatura", "TEM_MAX": "Temp. máx.", "TEM_MIN": "Temp. mín.", "PTO_INS": "Orvalho",
    "UMD_INS": "Umidade", "UMD_MAX": "Umid. máx.", "UMD_MIN": "Umid. mín.", "CHUVA": "Chuva",
    "RAD_GLO": "Radiação", "PRE_INS": "Pressão", "VEN_VEL": "Vento", "VEN_RAJ": "Rajada", "VEN_DIR": "Direção",
}
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


def desenhar(serie: pd.DataFrame, nome: str, zero_na_base: bool, circular: bool = False) -> alt.Chart:
    """Uma série por estação, com zoom por arrasto e valor ao passar o mouse.

    Variáveis circulares (direção do vento) saem em pontos: ligar 350° a 10° com uma linha
    desenharia uma volta inteira que não aconteceu.
    """
    longo = serie.reset_index().melt("dt_local", var_name="Estação", value_name="valor").dropna()
    base = alt.Chart(longo)
    marca = base.mark_point(size=18, filled=True, opacity=0.7) if circular else base.mark_line(strokeWidth=2)
    return (marca
            .encode(x=alt.X("dt_local:T", title=None),
                    y=alt.Y("valor:Q", title=nome, scale=alt.Scale(zero=zero_na_base)),
                    color=alt.Color("Estação:N", title=None, legend=alt.Legend(orient="bottom")),
                    tooltip=[alt.Tooltip("Estação:N"),
                             alt.Tooltip("dt_local:T", title="Quando", format="%d/%m %H:%M"),
                             alt.Tooltip("valor:Q", title=nome, format=".1f")])
            .properties(height=280)
            .interactive())


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


def agregar(tabela: pd.DataFrame, coluna: str, por_dia: bool, funcao: str) -> pd.DataFrame:
    """Uma coluna por estação, no tempo — de hora em hora ou resumida por dia."""
    largo = tabela.pivot_table(index="dt_local", columns="Estação", values=coluna, aggfunc="mean")
    if por_dia:
        largo = largo.resample("D").agg(FUNCOES[funcao])
    return largo.dropna(how="all")


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
    nomes = st.multiselect("Estações", estacoes["Estação"].tolist(),
                           default=estacoes["Estação"].tolist()[:3],
                           help="Cada estação vira uma linha no gráfico.")
    escolhidas = st.multiselect("Variáveis", list(VARIAVEIS), default=["Temperatura (°C)", "Umidade relativa (%)"],
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
aba_series, aba_qualidade = st.tabs(["Séries temporais", "Qualidade dos dados"])

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
        st.altair_chart(desenhar(serie, nome_variavel, coluna in ZERO_NA_BASE, circular), width="stretch")

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
