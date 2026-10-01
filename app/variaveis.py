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
# Uma paleta por grandeza: com escala fixa, máxima e mínima precisam das mesmas cores, senão os
# dois mapas continuam incomparáveis entre si.
PALETA_TEMPERATURA = "RdYlBu_r"
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
    direcao: str | None = None     # chave de DIRECOES: a seta que acompanha o valor no mapa


# =====================================================
# CÁLCULOS
# =====================================================
def _compensada_do_dia(dados: pd.DataFrame, colunas: tuple[str, ...]) -> float:
    """Média compensada do INMET de um dia: (T9 + 2·T21 + Tmín + Tmáx) / 5.

    As 9 h e as 21 h são horário do estado escolhido, como a equipe definiu — `dt_local` já vem
    no fuso dele. Ambas caem dentro do nosso dia (da leitura das 01 h à das 00 h), então não há
    deslocamento de dia nenhum.

    Sem a leitura das 9 h ou das 21 h a fórmula não fecha, e o dia fica em branco: um buraco no
    gráfico é honesto, um número inventado não.
    """
    inst, maxima, minima = colunas
    horas = dados["dt_local"].dt.hour
    t9, t21 = dados.loc[horas == 9, inst].dropna(), dados.loc[horas == 21, inst].dropna()
    if t9.empty or t21.empty:
        return float("nan")
    return (t9.iloc[0] + 2 * t21.iloc[0] + dados[minima].min() + dados[maxima].max()) / 5


def _compensada(dados: pd.DataFrame, colunas: tuple[str, ...]) -> float:
    """A compensada do dia; num período de vários dias, a média das compensadas diárias.

    É assim que se fecha a média de um mês. Aplicar a fórmula ao período inteiro casaria as 9 h
    do primeiro dia com a máxima de outro — um número que não é de dia nenhum. Dia incompleto sai
    da média em vez de zerá-la.
    """
    por_dia = dados.groupby(_fatias(dados, DIA), sort=False).apply(
        lambda dia: _compensada_do_dia(dia, colunas), include_groups=False)
    return por_dia.mean()


# =====================================================
# O QUE JÁ VEM ESCOLHIDO
# =====================================================
# O trio do boletim em cada agregação. Mora aqui, e não no painel, porque são nomes de
# produtos: um produto renomeado sem mexer nesta tabela deixaria o mapa abrir vazio, e o
# seletor não tem como avisar — ele só descarta o nome que não achou.
PADRAO_MAPA = {
    HORA: ("Temperatura na hora cheia", "Umidade mínima da hora", "Rajada na hora"),
    DIA: ("Temperatura média", "Umidade mínima", "Rajada máxima"),
    # No período não existe "Temperatura média": a média de vários dias é a compensada, como a
    # equipe definiu. É o mesmo produto, sob a regra que vale ali.
    PERIODO: ("Temperatura média compensada", "Umidade mínima", "Rajada máxima"),
}


# =====================================================
# DIREÇÃO DO VENTO
# =====================================================
# Direção não se interpola: entre 350° e 10° o vento mal mudou, mas a média daria 180°, o rumo
# oposto. Então ela não vira superfície — vai como seta sobre cada estação. E a seta precisa
# dizer a direção *daquele* número: na rajada, a da hora em que a rajada aconteceu; na
# velocidade média, a resultante das horas.
DIRECAO = "VEN_DIR"


def _direcao_da_maior(dados: pd.DataFrame, coluna: str) -> float:
    """A direção da hora em que aquela medida foi a maior — é a hora que o mapa está mostrando."""
    validos = dados.dropna(subset=[coluna, DIRECAO])
    if validos.empty:
        return float("nan")
    return float(validos.loc[validos[coluna].idxmax(), DIRECAO])


def _direcao_resultante(dados: pd.DataFrame, coluna: str) -> float:
    """A resultante das horas: soma vetorial, cada hora pesada pela sua velocidade.

    É a conta que responde "de onde veio o vento no período". A média dos ângulos não responde:
    12 h de norte e 12 h de sul dariam leste, um rumo que não soprou em hora nenhuma. Aqui os
    dois se cancelam e sobra o resto, que é o que de fato aconteceu.
    """
    validos = dados.dropna(subset=[coluna, DIRECAO])
    if validos.empty:
        return float("nan")
    angulos = np.radians(validos[DIRECAO])
    leste = (validos[coluna] * np.sin(angulos)).sum()
    norte = (validos[coluna] * np.cos(angulos)).sum()
    # Comparar com zero cravado não serve: o seno de 180° não dá 0 exato, e doze horas de norte
    # contra doze de sul sobrariam como um rumo qualquer. O que sobra tem de ser desprezível
    # perto do quanto ventou — aí não há rumo resultante, e a estação fica sem seta.
    if np.hypot(leste, norte) < 1e-9 * max(validos[coluna].sum(), 1.0):
        return float("nan")
    graus = float(np.degrees(np.arctan2(leste, norte)) % 360)
    return 0.0 if graus >= 360.0 else graus   # 360° é o mesmo norte que 0°


DIRECOES = {"da_maior": _direcao_da_maior, "resultante": _direcao_resultante}


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
            "°C", PALETA_TEMPERATURA, "maior valor medido dentro da hora"),
    Produto("Temperatura mínima da hora", "Temperatura", (HORA,), ("TEM_MIN",), "valor",
            "°C", PALETA_TEMPERATURA, "menor valor medido dentro da hora"),
    Produto("Temperatura na hora cheia", "Temperatura", (HORA,), ("TEM_INS",), "valor",
            "°C", PALETA_TEMPERATURA, "leitura do instante da hora cheia", onde=(MAPA,)),
    Produto("Temperatura média da hora", "Temperatura", (HORA,), ("TEM_MAX", "TEM_MIN"),
            "media_dos_extremos", "°C", PALETA_TEMPERATURA, "(máxima + mínima) da hora ÷ 2", onde=(GRAFICO,)),
    Produto("Temperatura máxima", "Temperatura", (DIA, PERIODO), ("TEM_MAX",), "maior",
            "°C", PALETA_TEMPERATURA, "maior máxima horária da janela"),
    Produto("Temperatura mínima", "Temperatura", (DIA, PERIODO), ("TEM_MIN",), "menor",
            "°C", PALETA_TEMPERATURA, "menor mínima horária da janela"),
    Produto("Temperatura média", "Temperatura", (DIA,), ("TEM_INS",), "media",
            "°C", PALETA_TEMPERATURA, "média das leituras das horas cheias"),
    # A média do período é a compensada, e não a média das horas cheias: é a que fecha a média de
    # um mês no INMET, e a que dá para comparar com a normal climatológica.
    Produto("Temperatura média compensada", "Temperatura", (DIA, PERIODO),
            ("TEM_INS", "TEM_MAX", "TEM_MIN"), "compensada", "°C", PALETA_TEMPERATURA,
            "(T9 + 2·T21 + Tmín + Tmáx) ÷ 5, no horário do estado; no período, a média das diárias"),

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
            "km/h", "turbo", "média da velocidade dentro da hora; a seta traz a direção da hora",
            zero_na_base=True, direcao="resultante"),
    Produto("Rajada na hora", "Vento", (HORA,), ("VEN_RAJ",), "valor",
            "km/h", "turbo",
            "maior velocidade instantânea dentro da hora; a seta traz a direção da hora",
            zero_na_base=True, direcao="da_maior"),
    # Só gráfico: interpolar ângulo não funciona — entre 350° e 10° a média daria 180°, o rumo
    # oposto. No mapa, direção se mostra com seta, como o relatório faz sobre a rajada.
    Produto("Direção na hora", "Vento", (HORA,), ("VEN_DIR",), "valor",
            "°", "twilight", "direção média dentro da hora", onde=(GRAFICO,), decimais=0),
    Produto("Rajada máxima", "Vento", (DIA, PERIODO), ("VEN_RAJ",), "maior",
            "km/h", "turbo", "maior rajada horária da janela; a seta traz a direção daquela hora",
            zero_na_base=True, direcao="da_maior"),
    Produto("Vento médio", "Vento", (DIA, PERIODO), ("VEN_VEL",), "media",
            "km/h", "turbo",
            "média das velocidades horárias; a seta traz a resultante das horas da janela",
            zero_na_base=True, direcao="resultante"),

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
# ESCALA DE CORES
# =====================================================
# A escala é da grandeza, não de cada mapa. Esticada ao dado do instante, a mesma cor quer dizer
# coisas diferentes em dias diferentes: num dia de 34 a 41 °C, os 36 °C saem azuis e parecem
# ameno. Com faixa fixa, azul é sempre frio — e quem passar do teto fica na cor do extremo.
FAIXAS_FIXAS = {
    "Temperatura": (0.0, 45.0),   # geada no sul e os 44 °C do oeste cabem dentro
    "Umidade": (0.0, 100.0),      # é o próprio domínio da variável
    "Vento": (0.0, 130.0),
}
# Chuva e radiação acumulam: o total depende do tamanho da janela, e uma faixa só não serve para
# uma hora e para um mês. Vão por classes, escolhidas pela duração da janela.
CLASSES_CHUVA_CURTA = (0.2, 1, 5, 10, 20, 30, 50, 75, 100)     # até 96 h
# Mensal e períodos longos. O teto é 300 mm porque é a máxima que o estado costuma alcançar no
# mês: parar aí dá contraste onde o dado de fato varia, e o que passar fica na cor do extremo.
CLASSES_CHUVA_LONGA = (1, 5, 25, 50, 100, 150, 200, 250, 300)
CLASSES_RADIACAO_HORA = (100, 500, 1000, 1500, 2000, 2500, 3000, 3500)
CLASSES_RADIACAO_DIA = (2500, 5000, 10000, 15000, 20000, 25000, 30000)
HORAS_JANELA_CURTA = 96


def escala(produto: Produto, horas_janela: float | None = None):
    """Como colorir este produto: `("faixa", (mín, máx))`, `("classes", níveis)` ou None.

    None quer dizer escala ajustada ao dado — é o caso da pressão, que a equipe preferiu manter
    assim porque a API manda a pressão da estação, sem redução ao nível do mar: entre estações de
    altitudes diferentes o mapa desenha mais o relevo do que o tempo.

    `horas_janela` é a duração do que está sendo somado, e só importa para chuva e radiação.
    """
    if produto.grandeza in FAIXAS_FIXAS:
        return "faixa", FAIXAS_FIXAS[produto.grandeza]
    curta = horas_janela is None or horas_janela <= HORAS_JANELA_CURTA
    if produto.grandeza == "Chuva":
        return "classes", CLASSES_CHUVA_CURTA if curta else CLASSES_CHUVA_LONGA
    if produto.grandeza == "Radiação":
        if horas_janela is not None and horas_janela <= 1:
            return "classes", CLASSES_RADIACAO_HORA
        return ("classes", CLASSES_RADIACAO_DIA) if curta else None
    return None


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


def direcoes(produto: Produto, leituras: pd.DataFrame) -> pd.Series:
    """De onde veio o vento em cada estação, para a seta sobre o mapa.

    Vazia quando o produto não leva seta — que é o caso de tudo o que não é vento.
    """
    if produto.direcao is None or leituras.empty or DIRECAO not in leituras:
        return pd.Series(dtype=float)
    regra = DIRECOES[produto.direcao]
    return (leituras.groupby("Estação", sort=False)
            .apply(lambda dados: regra(dados, produto.colunas[0]), include_groups=False)
            .rename("Direção (°)"))


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
