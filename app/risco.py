"""Risco de fogo na tela: a mesma regra 30-30-30 dos produtos, sobre o dado do painel.

A regra **não** é reescrita aqui. Ela mora em `modulos/produtos/risco_fogo.py` e é importada:
duas implementações do 30-30-30 divergiriam com o tempo, e aí a tela e o relatório diriam
números diferentes sobre a mesma hora — exatamente a discordância que o catálogo de variáveis
veio matar. Pelo mesmo motivo a tabela por estação é a da planilha do produto, coluna por coluna.

O que este módulo faz é traduzir **formato**: o produto trabalha com uma lista de
`(estação, leituras)`, uma estação por vez, e o painel tem uma tabela longa com todas juntas.

Só cálculo, sem tela, para poder ser testado.
"""
import numpy as np
import pandas as pd

from modulos import config
from modulos.produtos import risco_fogo

# As três da regra, como a API as nomeia. A rajada vira `rajada_kmh` porque é assim que o
# produto a procura.
COLUNAS = tuple(risco_fogo.COLUNAS_NECESSARIAS)
RAJADA = "rajada_kmh"
NIVEL_ALTO = risco_fogo.NIVEL_ALTO
NIVEL_MEDIO = risco_fogo.NIVEL_MEDIO


def por_estacao(leituras: pd.DataFrame, estacoes: pd.DataFrame) -> list:
    """As leituras no formato do produto: uma dupla (estação, leituras) por estação.

    Horas a que falta alguma das três variáveis ficam de fora: sem elas não dá para dizer
    quantas condições valeram naquela hora — e contar como "não atendida" o que não foi medido
    inventaria calma onde pode ter havido vento.

    A rajada **não** é convertida aqui. O painel já a recebe em km/h (converte ao carregar, como
    fazem os produtos); converter de novo daria 130 km/h num dia calmo, e risco alto onde não
    houve.
    """
    if leituras.empty or not set(COLUNAS) <= set(leituras.columns):
        return []
    validas = leituras.dropna(subset=list(COLUNAS))
    if validas.empty:
        return []
    validas = validas.assign(**{RAJADA: validas["VEN_RAJ"]})
    cadastro = {linha["Estação"]: linha for _, linha in estacoes.iterrows()}
    return [(cadastro[nome], desta)
            for nome, desta in validas.groupby("Estação", sort=False) if nome in cadastro]


def da_uf(completas: list, uf: str = config.UF) -> list:
    """Só as estações do produto.

    As de apoio — de fora do estado — alimentam as grades horárias, porque o risco de uma célula
    da divisa depende do que acontece dos dois lados. Mas elas não entram na tabela, nos gráficos
    nem na contagem de estações em risco: o produto é do estado.
    """
    return [(estacao, leituras) for estacao, leituras in completas
            if estacao.get("SG_ESTADO") == uf]


def resumo(completas: list, periodo: config.Periodo) -> pd.DataFrame:
    """Uma linha por estação, da de maior risco para a de menor — a tabela da planilha do produto."""
    if not completas:
        return pd.DataFrame()
    return risco_fogo.montar_tabela([risco_fogo.resumir_estacao(leituras, estacao, periodo)
                                     for estacao, leituras in completas])


def horas(completas: list) -> list:
    """As horas que têm leitura completa em alguma estação, em ordem."""
    if not completas:
        return []
    return sorted(pd.unique(pd.concat([leituras["dt_utc"] for _, leituras in completas])))


def avaliar(completas: list, quais_horas: list, base) -> dict:
    """Grade de níveis e estações de cada hora.

    Hora com estações de menos fica de fora: com três pontos a superfície inventaria o estado
    inteiro. As três variáveis são interpoladas **separadas** e a regra é aplicada célula a
    célula — interpolar o nível 0–3 direto produziria "1,7 condições" e espalharia risco médio
    onde estação nenhuma o registrou.
    """
    avaliadas = {}
    for hora in quais_horas:
        estacoes = risco_fogo.estacoes_na_hora(completas, hora)
        if len(estacoes) >= config.MIN_ESTACOES_INTERPOLACAO:
            avaliadas[hora] = risco_fogo.HoraAvaliada(risco_fogo.grade_da_hora(estacoes, base),
                                                      estacoes)
    return avaliadas


def horas_que_interessam(avaliadas: dict, nivel_minimo: int = NIVEL_ALTO) -> dict:
    """Só as horas em que alguma **estação** chegou àquele nível.

    O critério é o nível medido, e não o da superfície interpolada: a estação é o dado, e a
    superfície pode mostrar nível 2 numa célula onde nenhuma estação chegou a 2.
    """
    return {hora: avaliada for hora, avaliada in avaliadas.items()
            if avaliada.nivel_estacoes >= nivel_minimo}


def pior_nivel(avaliadas: dict) -> np.ndarray | None:
    """O pior nível que cada célula alcançou na janela."""
    return np.maximum.reduce([avaliada.grade for avaliada in avaliadas.values()]) if avaliadas else None


def horas_em_risco_alto(avaliadas: dict) -> np.ndarray | None:
    """Em quantas horas cada célula esteve no nível alto."""
    if not avaliadas:
        return None
    return sum((avaliada.grade == NIVEL_ALTO).astype(int) for avaliada in avaliadas.values())


def por_dia(avaliadas: dict) -> dict:
    """O pior nível de cada célula em cada dia, na convenção de dia do projeto.

    A leitura das 00:00 fecha o dia anterior — sem isso, uma semana viraria oito dias, e o último
    com uma hora só.
    """
    dias = {}
    for hora, avaliada in avaliadas.items():
        dia = risco_fogo.dia_da_leitura(pd.DatetimeIndex([hora]))[0]
        dias.setdefault(dia, []).append(avaliada.grade)
    return {dia: np.maximum.reduce(grades) for dia, grades in sorted(dias.items())}


def estacoes_do_dia(avaliadas: dict, dia) -> pd.DataFrame:
    """O pior nível de cada estação naquele dia, com as coordenadas — é o que vai sobre o mapa.

    Sai das horas já avaliadas, e não de uma conta nova: o nível de cada estação em cada hora já
    foi calculado para desenhar os mapas horários.
    """
    das_horas = [avaliada.estacoes for hora, avaliada in avaliadas.items()
                 if risco_fogo.dia_da_leitura(pd.DatetimeIndex([hora]))[0] == dia]
    if not das_horas:
        return pd.DataFrame()
    return (pd.concat(das_horas, ignore_index=True)
            .groupby(["Estação", "Latitude", "Longitude"], as_index=False)
            [risco_fogo.COLUNA_NIVEL_HORA].max())


def hora_a_hora(completas: list) -> pd.DataFrame:
    """Uma linha por estação e hora, com as condições atendidas, o nível e o dia — o que os
    gráficos comem."""
    return risco_fogo.tabela_horaria(completas) if completas else pd.DataFrame()
