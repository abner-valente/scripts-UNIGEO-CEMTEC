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
import pandas as pd
import streamlit as st

from app import dados as coleta
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
    "Vento (m/s)": "VEN_VEL",
    "Rajada (m/s)": "VEN_RAJ",
    "Pressão (hPa)": "PRE_INS",
    "Ponto de orvalho (°C)": "PTO_INS",
}
FUNCOES = {"Média": "mean", "Máxima": "max", "Mínima": "min", "Soma": "sum"}
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
    return (pd.concat(series, ignore_index=True) if series else pd.DataFrame()), falharam


def desenhar(serie: pd.DataFrame, nome: str, zero_na_base: bool) -> alt.Chart:
    """Uma linha por estação, com zoom por arrasto e valor ao passar o mouse."""
    longo = serie.reset_index().melt("dt_local", var_name="Estação", value_name="valor").dropna()
    return (alt.Chart(longo)
            .mark_line(strokeWidth=2)
            .encode(x=alt.X("dt_local:T", title=None),
                    y=alt.Y("valor:Q", title=nome, scale=alt.Scale(zero=zero_na_base)),
                    color=alt.Color("Estação:N", title=None, legend=alt.Legend(orient="bottom")),
                    tooltip=[alt.Tooltip("Estação:N"),
                             alt.Tooltip("dt_local:T", title="Quando", format="%d/%m %H:%M"),
                             alt.Tooltip("valor:Q", title=nome, format=".1f")])
            .properties(height=280)
            .interactive())


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
    if st.button("Limpar cache", use_container_width=True):
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

esperadas = int((fim - inicio).total_seconds() // 3600) * len(nomes)
st.caption(f"{len(tabela)} leituras de {tabela['Estação'].nunique()} estações · "
           f"{len(tabela) / esperadas:.0%} das horas do período têm registro")

# =====================================================
# GRÁFICOS
# =====================================================
for nome_variavel in escolhidas:
    coluna = VARIAVEIS[nome_variavel]
    if coluna not in tabela:
        st.warning(f"{nome_variavel}: a API não devolveu essa coluna no período.")
        continue
    serie = agregar(tabela, coluna, por_dia, funcao)
    if serie.empty:
        st.warning(f"{nome_variavel}: nenhuma estação escolhida tem essa medição no período.")
        continue
    st.subheader(nome_variavel)
    st.altair_chart(desenhar(serie, nome_variavel, coluna in ZERO_NA_BASE), use_container_width=True)

with st.expander("Ver e baixar os dados"):
    colunas = ["Estação", "dt_local"] + [VARIAVEIS[nome] for nome in escolhidas if VARIAVEIS[nome] in tabela]
    visivel = tabela[colunas].rename(columns={"dt_local": "Data/Hora (MS)"})
    st.dataframe(visivel, use_container_width=True, height=300)
    st.download_button("Baixar CSV", visivel.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"leituras_{intervalo[0]:%Y%m%d}_a_{intervalo[1]:%Y%m%d}.csv", mime="text/csv")
