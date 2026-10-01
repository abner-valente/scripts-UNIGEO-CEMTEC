"""Verificações de qualidade das leituras das estações automáticas.

São conferências que nenhum produto faz: eles calculam em cima do que a API mandou. Aqui a
pergunta é outra — dá para confiar nesse dado? O módulo é só cálculo, sem tela, para poder
ser testado.

As faixas plausíveis abaixo são grosseiras de propósito: a ideia é pegar defeito de sensor,
não discutir recorde meteorológico.
"""
import pandas as pd

# coluna -> (mínimo plausível, máximo plausível). Vento em km/h, como o explorador exibe.
LIMITES = {
    "TEM_INS": (-10, 55), "TEM_MAX": (-10, 55), "TEM_MIN": (-10, 55), "PTO_INS": (-20, 40),
    "UMD_INS": (0, 100), "UMD_MIN": (0, 100), "UMD_MAX": (0, 100),
    "CHUVA": (0, 150), "RAD_GLO": (-50, 5000), "PRE_INS": (800, 1100),
    "VEN_VEL": (0, 150), "VEN_RAJ": (0, 200), "VEN_DIR": (0, 360),
}
# Colunas em que repetir o mesmo valor por horas seguidas é sinal de sensor travado. Chuva e
# vento ficam de fora: zero repetido é o normal em tempo seco e calmo.
COLUNAS_TRAVAMENTO = ["TEM_INS", "UMD_INS", "PRE_INS"]
HORAS_TRAVADO = 6
TENSAO_MINIMA = 11.5  # volts; abaixo disso a estação costuma sair do ar em pouco tempo


def dia_da_leitura(horas: pd.Series) -> pd.Series:
    """Dia a que a leitura pertence: a das 00:00 fecha o dia anterior, como nos produtos."""
    return (horas - pd.Timedelta(hours=1)).dt.date


def valores_impossiveis(tabela: pd.DataFrame) -> pd.DataFrame:
    """Leituras fora da faixa plausível de cada variável."""
    achados = []
    for coluna, (minimo, maximo) in LIMITES.items():
        if coluna not in tabela:
            continue
        fora = tabela[(tabela[coluna] < minimo) | (tabela[coluna] > maximo)]
        if fora.empty:
            continue
        achados.append(fora[["Estação", "dt_local", coluna]]
                       .rename(columns={"dt_local": "Quando", coluna: "Valor"})
                       .assign(Variável=coluna, Esperado=f"{minimo:g} a {maximo:g}"))
    if not achados:
        return pd.DataFrame(columns=["Estação", "Quando", "Variável", "Valor", "Esperado"])
    return (pd.concat(achados, ignore_index=True)[["Estação", "Quando", "Variável", "Valor", "Esperado"]]
            .sort_values(["Estação", "Quando"], ignore_index=True))


def sensores_travados(tabela: pd.DataFrame, horas_minimas: int = HORAS_TRAVADO) -> pd.DataFrame:
    """Trechos em que a mesma leitura se repete por várias horas seguidas."""
    achados = []
    for (estacao, coluna), serie in _series_por_estacao(tabela):
        valores = serie.dropna()
        if valores.empty:
            continue
        blocos = (valores != valores.shift()).cumsum()
        for _, bloco in valores.groupby(blocos):
            if len(bloco) >= horas_minimas:
                achados.append({"Estação": estacao, "Variável": coluna, "Valor": bloco.iloc[0],
                                "Horas": len(bloco), "De": bloco.index[0], "Até": bloco.index[-1]})
    return pd.DataFrame(achados, columns=["Estação", "Variável", "Valor", "Horas", "De", "Até"]) \
        .sort_values("Horas", ascending=False, ignore_index=True)


def completude_por_dia(tabela: pd.DataFrame) -> pd.DataFrame:
    """Percentual das 24 horas de cada dia em que a estação registrou leitura."""
    dias = dia_da_leitura(tabela["dt_local"])
    contagem = tabela.pivot_table(index="Estação", columns=dias, values="dt_utc", aggfunc="count")
    return (contagem.fillna(0) / 24 * 100).clip(upper=100).round(0)


def completude_por_variavel(tabela: pd.DataFrame, colunas: list[str] | None = None) -> pd.DataFrame:
    """Percentual das horas registradas em que cada variável veio com valor, por estação.

    É diferente da completude por dia: a estação pode registrar a hora e mesmo assim não medir
    tudo. É aqui que aparece o sensor que parou sozinho, sem a estação sair do ar.
    """
    medidas = [coluna for coluna in (colunas or LIMITES) if coluna in tabela]
    horas = tabela.groupby("Estação").size()
    return (tabela.groupby("Estação")[medidas].count().div(horas, axis=0) * 100).round(0)


def resumo_por_estacao(tabela: pd.DataFrame, horas_esperadas: int) -> pd.DataFrame:
    """Uma linha por estação, da pior para a melhor: é por onde se começa a olhar."""
    impossiveis = valores_impossiveis(tabela).groupby("Estação").size()
    travados = sensores_travados(tabela).groupby("Estação")["Horas"].sum()
    resumo = pd.DataFrame({
        "Horas com dado": tabela.groupby("Estação").size(),
        "Valores impossíveis": impossiveis,
        "Horas travadas": travados,
    })
    if "RAD_GLO" in tabela:
        resumo["Radiação negativa (h)"] = tabela[tabela["RAD_GLO"] < 0].groupby("Estação").size()
    if "TEN_BAT" in tabela:
        resumo["Bateria mín. (V)"] = tabela.groupby("Estação")["TEN_BAT"].min().round(1)

    resumo = resumo.fillna({coluna: 0 for coluna in resumo.columns if coluna != "Bateria mín. (V)"})
    resumo.insert(1, "Completude", (resumo["Horas com dado"] / max(horas_esperadas, 1) * 100).clip(upper=100).round(0))
    return resumo.sort_values(["Completude", "Valores impossíveis"], ascending=[True, False]).reset_index()


def _series_por_estacao(tabela: pd.DataFrame):
    """Percorre (estação, coluna) devolvendo a série no tempo, para as colunas de travamento."""
    for estacao, dados in tabela.groupby("Estação"):
        ordenados = dados.sort_values("dt_local")
        for coluna in COLUNAS_TRAVAMENTO:
            if coluna in ordenados:
                yield (estacao, coluna), ordenados.set_index("dt_local")[coluna]
