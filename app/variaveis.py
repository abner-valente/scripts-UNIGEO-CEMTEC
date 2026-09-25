"""Catálogo do que se pode mapear e traçar, com a regra de cada coisa.

A agregação não é escolha de quem olha: é propriedade da variável. A estação mede de 10 em 10
minutos e transmite de hora em hora, e o que ela manda já vem resumido — MAX e MIN são os
extremos daquela hora, INS é a leitura da hora cheia, chuva e radiação são acumulados da hora,
e a velocidade do vento é a média da hora. Cada um desses resume o dia de um jeito diferente:
a máxima do dia é a *maior* das máximas horárias, nunca a média delas.

Enquanto a tela oferecia "Média, Máxima, Mínima ou Soma" para qualquer variável, dava para pedir
a média das máximas ou a soma das temperaturas — números que não existem em boletim nenhum. Aqui
cada produto traz a sua regra, e a combinação errada simplesmente não está escrita.

A regra diz só **como colapsar um punhado de leituras horárias num número**. Quem agrupa é quem
chama: por estação, e sai o mapa; por estação e por dia, e sai a série do gráfico. Assim a
máxima do dia no mapa e no gráfico são o mesmo código, e não podem discordar.

As leituras chegam aqui como o explorador as carrega — em particular, o vento já vem convertido
de m/s para km/h, que é a unidade dos produtos. O catálogo não converte nada.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Modos de agregação. HORA mostra a leitura como veio da API; DIA e PERIODO resumem a janela.
HORA, DIA, PERIODO = "hora", "dia", "periodo"
# Onde o produto pode aparecer. A compensada, por exemplo, só faz sentido como série no tempo.
MAPA, GRAFICO = "mapa", "grafico"


@dataclass(frozen=True)
class Produto:
    """Uma coisa que se escolhe na tela, com a regra que a define.

    `regra` não é comentário: vai impressa embaixo do mapa e do gráfico. É ela que responde à
    pergunta "que média é essa?" sem ninguém precisar abrir o código.
    """

    nome: str
    grandeza: str                  # agrupa os produtos no seletor (Temperatura, Vento, ...)
    modos: tuple[str, ...]
    colunas: tuple[str, ...]       # colunas da API de onde sai o valor
    calculo: str                   # chave de CALCULOS
    unidade: str
    paleta: str                    # mapa de cores do mapa
    regra: str                     # a frase que vai para a tela
    onde: tuple[str, ...] = (MAPA, GRAFICO)
    decimais: int = 1
    zero_na_base: bool = False     # o eixo do gráfico começa no zero (chuva, radiação, vento)


# =====================================================
# CÁLCULOS
# =====================================================
def _compensada(dados: pd.DataFrame, colunas: tuple[str, ...]) -> float:
    """Média compensada do INMET: (T9 + 2·T21 + Tmín + Tmáx) / 5.

    As 9 h e as 21 h são horário de MS, como a equipe definiu — ambas caem dentro do nosso dia
    (da leitura das 01 h à das 00 h), então não há deslocamento de dia nenhum.

    Sem a leitura das 9 h ou das 21 h a fórmula não fecha, e o dia fica em branco: um buraco no
    gráfico é honesto, um número inventado não.
    """
    inst, maxima, minima = colunas
    horas = dados["dt_local"].dt.hour
    t9, t21 = dados.loc[horas == 9, inst].dropna(), dados.loc[horas == 21, inst].dropna()
    if t9.empty or t21.empty:
        return float("nan")
    return (t9.iloc[0] + 2 * t21.iloc[0] + dados[minima].min() + dados[maxima].max()) / 5


# Cálculos que o pandas sabe fazer sozinho, direto na coluna. O gráfico horário pede um valor
# por estação e por hora — centenas de grupos —, e passar cada um por uma função Python custaria
# caro à toa. Os compostos (média dos extremos, compensada) continuam pela função.
ATALHOS = {"valor": "mean", "maior": "max", "menor": "min", "media": "mean", "soma": "sum"}

CALCULOS = {
    # Na hora cheia há uma leitura por estação: a média só protege de uma duplicata na API.
    "valor": lambda dados, colunas: dados[colunas[0]].mean(),
    "maior": lambda dados, colunas: dados[colunas[0]].max(),
    "menor": lambda dados, colunas: dados[colunas[0]].min(),
    "media": lambda dados, colunas: dados[colunas[0]].mean(),
    # Radiação à noite devolve valor levemente negativo: é ruído conhecido do sensor, e somado
    # ao longo do dia viraria um desconto que nunca houve.
    "soma_sem_negativos": lambda dados, colunas: dados[colunas[0]].clip(lower=0).sum(),
    "soma": lambda dados, colunas: dados[colunas[0]].sum(),
    "media_dos_extremos": lambda dados, colunas: (dados[colunas[0]].mean() + dados[colunas[1]].mean()) / 2,
    "compensada": _compensada,
}


# =====================================================
# CATÁLOGO
# =====================================================
PRODUTOS = [
    # --- Temperatura -------------------------------------------------------
    Produto("Temperatura máxima da hora", "Temperatura", (HORA,), ("TEM_MAX",), "valor",
            "°C", "YlOrRd", "maior valor medido dentro da hora"),
    Produto("Temperatura mínima da hora", "Temperatura", (HORA,), ("TEM_MIN",), "valor",
            "°C", "coolwarm", "menor valor medido dentro da hora"),
    Produto("Temperatura na hora cheia", "Temperatura", (HORA,), ("TEM_INS",), "valor",
            "°C", "RdYlBu_r", "leitura do instante da hora cheia", onde=(MAPA,)),
    Produto("Temperatura média da hora", "Temperatura", (HORA,), ("TEM_MAX", "TEM_MIN"),
            "media_dos_extremos", "°C", "RdYlBu_r", "(máxima + mínima) da hora ÷ 2", onde=(GRAFICO,)),
    Produto("Temperatura máxima", "Temperatura", (DIA, PERIODO), ("TEM_MAX",), "maior",
            "°C", "YlOrRd", "maior máxima horária da janela"),
    Produto("Temperatura mínima", "Temperatura", (DIA, PERIODO), ("TEM_MIN",), "menor",
            "°C", "coolwarm", "menor mínima horária da janela"),
    Produto("Temperatura média", "Temperatura", (DIA, PERIODO), ("TEM_INS",), "media",
            "°C", "RdYlBu_r", "média das leituras das horas cheias"),
    Produto("Temperatura média compensada", "Temperatura", (DIA,), ("TEM_INS", "TEM_MAX", "TEM_MIN"),
            "compensada", "°C", "RdYlBu_r", "(T9 + 2·T21 + Tmín + Tmáx) ÷ 5, horário de MS",
            onde=(GRAFICO,)),

    # --- Umidade -----------------------------------------------------------
    Produto("Umidade máxima da hora", "Umidade", (HORA,), ("UMD_MAX",), "valor",
            "%", "YlGnBu", "maior valor medido dentro da hora"),
    Produto("Umidade mínima da hora", "Umidade", (HORA,), ("UMD_MIN",), "valor",
            "%", "YlGnBu", "menor valor medido dentro da hora"),
    Produto("Umidade na hora cheia", "Umidade", (HORA,), ("UMD_INS",), "valor",
            "%", "YlGnBu", "leitura do instante da hora cheia", onde=(MAPA,)),
    Produto("Umidade média da hora", "Umidade", (HORA,), ("UMD_MAX", "UMD_MIN"),
            "media_dos_extremos", "%", "YlGnBu", "(máxima + mínima) da hora ÷ 2", onde=(GRAFICO,)),
    Produto("Umidade mínima", "Umidade", (DIA, PERIODO), ("UMD_MIN",), "menor",
            "%", "YlGnBu", "menor mínima horária da janela"),
    Produto("Umidade média", "Umidade", (DIA, PERIODO), ("UMD_INS",), "media",
            "%", "YlGnBu", "média das leituras das horas cheias"),
    # A máxima do dia não vai para o mapa: para a equipe, o que interessa no espaço é a mínima.
    Produto("Umidade máxima", "Umidade", (DIA,), ("UMD_MAX",), "maior",
            "%", "YlGnBu", "maior máxima horária do dia", onde=(GRAFICO,)),

    # --- Pressão -----------------------------------------------------------
    Produto("Pressão máxima da hora", "Pressão", (HORA,), ("PRE_MAX",), "valor",
            "hPa", "viridis", "maior valor medido dentro da hora"),
    Produto("Pressão mínima da hora", "Pressão", (HORA,), ("PRE_MIN",), "valor",
            "hPa", "viridis", "menor valor medido dentro da hora"),
    Produto("Pressão na hora cheia", "Pressão", (HORA,), ("PRE_INS",), "valor",
            "hPa", "viridis", "leitura do instante da hora cheia", onde=(MAPA,)),
    Produto("Pressão média da hora", "Pressão", (HORA,), ("PRE_MAX", "PRE_MIN"),
            "media_dos_extremos", "hPa", "viridis", "(máxima + mínima) da hora ÷ 2", onde=(GRAFICO,)),
    Produto("Pressão máxima", "Pressão", (DIA,), ("PRE_MAX",), "maior",
            "hPa", "viridis", "maior máxima horária do dia"),
    Produto("Pressão mínima", "Pressão", (DIA,), ("PRE_MIN",), "menor",
            "hPa", "viridis", "menor mínima horária do dia"),
    Produto("Pressão média", "Pressão", (DIA, PERIODO), ("PRE_INS",), "media",
            "hPa", "viridis", "média das leituras das horas cheias"),

    # --- Vento -------------------------------------------------------------
    # A API manda em m/s; o explorador converte para km/h ao carregar, como fazem os produtos.
    Produto("Velocidade na hora", "Vento", (HORA,), ("VEN_VEL",), "valor",
            "km/h", "turbo", "média da velocidade dentro da hora", zero_na_base=True),
    Produto("Rajada na hora", "Vento", (HORA,), ("VEN_RAJ",), "valor",
            "km/h", "turbo", "maior velocidade instantânea dentro da hora", zero_na_base=True),
    # Só gráfico: interpolar ângulo não funciona — entre 350° e 10° a média daria 180°, o rumo
    # oposto. No mapa, direção se mostra com seta, como o relatório faz sobre a rajada.
    Produto("Direção na hora", "Vento", (HORA,), ("VEN_DIR",), "valor",
            "°", "twilight", "direção média dentro da hora", onde=(GRAFICO,), decimais=0),
    Produto("Rajada máxima", "Vento", (DIA, PERIODO), ("VEN_RAJ",), "maior",
            "km/h", "turbo", "maior rajada horária da janela", zero_na_base=True),
    Produto("Vento médio", "Vento", (DIA, PERIODO), ("VEN_VEL",), "media",
            "km/h", "turbo", "média das velocidades horárias", zero_na_base=True),

    # --- Radiação ----------------------------------------------------------
    Produto("Radiação na hora", "Radiação", (HORA,), ("RAD_GLO",), "soma_sem_negativos",
            "kJ/m²", "YlOrRd", "acumulado da hora, com o ruído negativo zerado",
            decimais=0, zero_na_base=True),
    Produto("Radiação acumulada", "Radiação", (DIA, PERIODO), ("RAD_GLO",), "soma_sem_negativos",
            "kJ/m²", "YlOrRd", "soma das horas, com o ruído negativo zerado",
            decimais=0, zero_na_base=True),

    # --- Chuva -------------------------------------------------------------
    # Os acumulados de 3 a 96 h e o mensal ficam fora daqui: eles olham para trás do início do
    # período escolhido, e por isso têm aba própria, com a sua própria carga de dados.
    Produto("Chuva na hora", "Chuva", (HORA,), ("CHUVA",), "valor",
            "mm", "Blues", "acumulado da hora", zero_na_base=True),
    Produto("Chuva acumulada", "Chuva", (DIA, PERIODO), ("CHUVA",), "soma",
            "mm", "Blues", "soma das horas da janela", zero_na_base=True),
]


# =====================================================
# CONSULTA AO CATÁLOGO
# =====================================================
def disponiveis(modo: str, onde: str = MAPA) -> list[Produto]:
    """Produtos que existem nesse modo e nesse lugar da tela, na ordem do catálogo."""
    return [produto for produto in PRODUTOS if modo in produto.modos and onde in produto.onde]


def grandezas(modo: str, onde: str = MAPA) -> list[str]:
    """Grandezas com ao menos um produto nesse modo, sem repetir e na ordem do catálogo."""
    return list(dict.fromkeys(produto.grandeza for produto in disponiveis(modo, onde)))


def rotulo_curto(produto: Produto) -> str:
    """O nome sem repetir a grandeza — "Temperatura máxima da hora" vira "Máxima da hora".

    Num gráfico de Temperatura, a legenda não precisa dizer "Temperatura" quatro vezes. Mas se o
    que sobra começa por preposição ("Chuva na hora" viraria "Na hora"), o nome inteiro fica: um
    rótulo curto demais deixa de nomear coisa alguma.
    """
    curto = produto.nome.replace(produto.grandeza, "").strip()
    if not curto or curto.split()[0] in {"na", "no", "da", "do", "de", "em"}:
        return produto.nome
    return curto[0].upper() + curto[1:]


def por_nome(nome: str) -> Produto:
    for produto in PRODUTOS:
        if produto.nome == nome:
            return produto
    raise KeyError(f"produto desconhecido: {nome}")


# =====================================================
# CÁLCULO
# =====================================================
def calcular(produto: Produto, leituras: pd.DataFrame) -> float:
    """Colapsa um punhado de leituras horárias no número do produto."""
    if leituras.empty or any(coluna not in leituras for coluna in produto.colunas):
        return float("nan")
    return CALCULOS[produto.calculo](leituras, produto.colunas)


def _colapsar(produto: Produto, agrupado) -> pd.Series:
    """Aplica a regra do produto a cada grupo, pelo caminho rápido quando dá."""
    if produto.calculo in ATALHOS:
        return agrupado[produto.colunas[0]].agg(ATALHOS[produto.calculo])
    return agrupado.apply(lambda dados: calcular(produto, dados), include_groups=False)


def por_estacao(produto: Produto, leituras: pd.DataFrame) -> pd.Series:
    """Um número por estação para a janela inteira — é o que o mapa interpola."""
    if leituras.empty or any(coluna not in leituras for coluna in produto.colunas):
        return pd.Series(dtype=float)
    return _colapsar(produto, leituras.groupby("Estação", sort=False)).rename(produto.nome)


def _fatias(leituras: pd.DataFrame, modo: str) -> pd.Series:
    """A que hora ou a que dia pertence cada leitura.

    No modo diário a leitura das 00:00 fecha o dia anterior, como nos produtos: sem recuar essa
    hora, uma semana viraria oito dias, e o último teria uma leitura só.
    """
    marcas = leituras["dt_local"]
    return (marcas - pd.Timedelta(hours=1)).dt.normalize() if modo == DIA else marcas


def momentos(leituras: pd.DataFrame, modo: str) -> list:
    """As horas (ou os dias) que a janela contém — as paradas do deslizante do mapa."""
    if leituras.empty or modo == PERIODO:
        return []
    return sorted(_fatias(leituras, modo).unique())


def recorte(leituras: pd.DataFrame, modo: str, momento=None) -> pd.DataFrame:
    """Só as leituras daquela hora, daquele dia — ou todas, no período inteiro."""
    if modo == PERIODO or momento is None:
        return leituras
    return leituras[_fatias(leituras, modo) == momento]


def no_tempo(produto: Produto, leituras: pd.DataFrame, modo: str) -> pd.DataFrame:
    """Uma coluna por estação e uma linha por hora ou por dia — é o que o gráfico desenha."""
    if leituras.empty:
        return pd.DataFrame()
    if any(coluna not in leituras for coluna in produto.colunas):
        return pd.DataFrame()
    fatias = leituras.assign(_quando=_fatias(leituras, modo))
    largo = _colapsar(produto, fatias.groupby(["_quando", "Estação"], sort=True)).unstack("Estação")
    largo.index.name = "dt_local"
    return largo.dropna(how="all").replace([np.inf, -np.inf], np.nan)
