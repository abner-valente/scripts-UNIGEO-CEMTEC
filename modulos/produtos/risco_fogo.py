"""Risco meteorológico de fogo pela regra 30-30-30, a partir das estações automáticas do INMET.

Produto pedido pela equipe de meteorologia (não veio de script legado). A regra conta quantas das
três condições são atendidas — temperatura máxima >= 30 °C, umidade relativa mínima <= 30 % e
rajada >= 30 km/h — resultando num nível de 0 (nenhuma) a 3 (todas).

Duas decisões de método estão registradas em docs/questoes_meteorologia.md (questões 6 a 8):

1. A regra é avaliada **hora a hora**, e não sobre os extremos do período. Os extremos de um dia
   acontecem em horários diferentes: uma estação pode ter 32 °C às 15 h, 28 % às 18 h e rajada de
   35 km/h às 03 h sem que calor, seca e vento tenham coincidido em momento algum.
2. No mapa interpolado, as **três variáveis são interpoladas separadamente** e a regra é aplicada
   célula a célula. Interpolar o nível 0–3 direto produziria valores sem sentido físico
   ("1,7 condições") e espalharia risco médio onde nenhuma estação o registrou.

Consultas de período são recusadas: para período a equipe quer gráficos quantitativos, não mapas.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .. import calculos, config, excel, inmet, mapas
from ..config import Periodo
from ..mapas import BaseCartografica, EspecClasses, EspecMapa, Indicadores

NOME = "risco_fogo"
TITULO = "Risco meteorológico de fogo — regra 30-30-30"

COLUNAS_NECESSARIAS = ["TEM_MAX", "UMD_MIN", "VEN_RAJ"]
ABA = "Risco"
COLUNA_NIVEL = "Nível Máximo"
COLUNA_HORAS_ALTO = "Horas em Risco Alto"
COLUNA_NIVEL_HORA = "Nível na Hora"
COLUNAS_CONDICOES = ["Temperatura Atendida", "Umidade Atendida", "Rajada Atendida"]
NIVEL_ALTO = 3


# =====================================================
# A REGRA 30-30-30
# =====================================================
def condicoes_atendidas(temp_max, umidade_min, rajada_kmh) -> tuple:
    """Cada uma das três condições (True onde é atendida), na ordem temperatura, umidade e rajada."""
    return (np.asarray(temp_max) >= config.LIMIAR_TEMP_MAX,
            np.asarray(umidade_min) <= config.LIMIAR_UMIDADE_MIN,
            np.asarray(rajada_kmh) >= config.LIMIAR_RAJADA)


def contar_condicoes(temp_max, umidade_min, rajada_kmh):
    """Quantas das três condições são atendidas (0 a 3). Funciona com números, séries ou grades."""
    return sum(condicao.astype(int) for condicao in condicoes_atendidas(temp_max, umidade_min, rajada_kmh))


def leituras_validas(dados: pd.DataFrame, periodo: Periodo) -> pd.DataFrame:
    """Leituras da janela que têm as três variáveis, com a rajada já convertida para km/h.

    Horas a que falta alguma das três ficam de fora: sem elas não dá para dizer quantas condições
    valeram naquela hora.
    """
    recorte = calculos.recortar(dados, *periodo.janela)
    if not set(COLUNAS_NECESSARIAS) <= set(recorte.columns):
        return recorte.iloc[0:0]
    recorte = recorte.dropna(subset=COLUNAS_NECESSARIAS)
    return recorte.assign(rajada_kmh=recorte["VEN_RAJ"] * 3.6)


def niveis_por_hora(leituras: pd.DataFrame) -> pd.Series:
    """Nível de risco (0 a 3) de cada hora, indexado pelo horário UTC da leitura."""
    niveis = contar_condicoes(leituras["TEM_MAX"], leituras["UMD_MIN"], leituras["rajada_kmh"])
    return pd.Series(niveis, index=pd.Index(leituras["dt_utc"]), dtype=int)


def horas_da_janela(periodo: Periodo) -> pd.DatetimeIndex:
    """Horas cheias contidas na janela (início, fim].

    No tempo real o fim cai no meio de uma hora (ex.: 13h25), por isso as pontas são ajustadas
    para horas cheias, que é como as leituras do INMET chegam.
    """
    inicio, fim = (pd.Timestamp(momento) for momento in periodo.janela)
    primeira = inicio.ceil("h")
    if primeira <= inicio:  # a janela é aberta no início: a leitura do próprio início não entra
        primeira += pd.Timedelta(hours=1)
    return pd.date_range(primeira, fim.floor("h"), freq="h")


# =====================================================
# CÁLCULOS POR ESTAÇÃO
# =====================================================
def resumir_estacao(leituras: pd.DataFrame, estacao: pd.Series) -> dict:
    """Linha da planilha: nível máximo, horas em cada nível e os valores que dispararam as condições."""
    niveis = niveis_por_hora(leituras)
    em_risco_alto = niveis[niveis == NIVEL_ALTO]
    return {
        "Estação": estacao["Estação"],
        COLUNA_NIVEL: int(niveis.max()),
        COLUNA_HORAS_ALTO: int((niveis == 3).sum()),
        "Horas Nível 2": int((niveis == 2).sum()),
        "Horas Nível 1": int((niveis == 1).sum()),
        "Horas com Dados": int(len(niveis)),
        "Temp. Máxima (°C)": round(float(leituras["TEM_MAX"].max()), 1),
        "Umidade Mínima (%)": round(float(leituras["UMD_MIN"].min()), 1),
        "Rajada Máxima (km/h)": round(float(leituras["rajada_kmh"].max()), 1),
        "Primeiro Horário em Risco Alto (MS)": _hora_ms(em_risco_alto.index.min() if len(em_risco_alto) else None),
        "Latitude": estacao["VL_LATITUDE"],
        "Longitude": estacao["VL_LONGITUDE"],
    }


def montar_tabela(resumos: list[dict]) -> pd.DataFrame:
    """Tabela da planilha, das estações de maior para as de menor risco."""
    return pd.DataFrame(resumos).sort_values(
        [COLUNA_NIVEL, COLUNA_HORAS_ALTO], ascending=False, ignore_index=True
    )


def _hora_ms(momento) -> str:
    """Horário de uma leitura no fuso de MS, pronto para a planilha."""
    return "" if momento is None else momento.tz_convert(config.FUSO_MS).strftime("%d/%m/%Y %H:%M")


# =====================================================
# GRADES HORÁRIAS (as três variáveis interpoladas separadamente)
# =====================================================
def estacoes_na_hora(coletados: list, hora) -> pd.DataFrame:
    """Estações com leitura completa naquela hora, com os valores e o nível de risco **da hora**."""
    linhas = []
    for estacao, leituras in coletados:
        leitura = leituras[leituras["dt_utc"] == hora]
        if leitura.empty:
            continue
        linhas.append({
            "Estação": estacao["Estação"],
            "Longitude": estacao["VL_LONGITUDE"],
            "Latitude": estacao["VL_LATITUDE"],
            "TEM_MAX": leitura["TEM_MAX"].iloc[0],
            "UMD_MIN": leitura["UMD_MIN"].iloc[0],
            "rajada_kmh": leitura["rajada_kmh"].iloc[0],
        })
    estacoes = pd.DataFrame(linhas)
    if not estacoes.empty:
        atendidas = condicoes_atendidas(estacoes["TEM_MAX"], estacoes["UMD_MIN"], estacoes["rajada_kmh"])
        for coluna, atendida in zip(COLUNAS_CONDICOES, atendidas):
            estacoes[coluna] = atendida
        estacoes[COLUNA_NIVEL_HORA] = estacoes[COLUNAS_CONDICOES].sum(axis=1)
    return estacoes


def grade_da_hora(pontos: pd.DataFrame, base: BaseCartografica) -> np.ndarray:
    """Grade de níveis (0 a 3) daquela hora: interpola as três variáveis e aplica a regra por célula."""
    campos = [
        calculos.interpolar_idw(pontos["Longitude"], pontos["Latitude"], pontos[coluna],
                                base.lon_grade, base.lat_grade, config.IDW_VIZINHOS, config.IDW_POTENCIA)
        for coluna in ("TEM_MAX", "UMD_MIN", "rajada_kmh")
    ]
    return contar_condicoes(*campos)


@dataclass(frozen=True)
class HoraAvaliada:
    """O que foi calculado para uma hora: a grade de níveis e as estações com o nível daquela hora."""

    grade: np.ndarray
    estacoes: pd.DataFrame

    @property
    def nivel_estacoes(self) -> int:
        """Maior nível observado nas estações naquela hora."""
        return int(self.estacoes[COLUNA_NIVEL_HORA].max())


def analisar_horas(coletados: list, periodo: Periodo, base: BaseCartografica) -> dict:
    """Avalia cada hora da janela. Horas com poucas estações para interpolar ficam de fora."""
    horas = {}
    for hora in horas_da_janela(periodo):
        estacoes = estacoes_na_hora(coletados, hora)
        if len(estacoes) >= config.MIN_ESTACOES_INTERPOLACAO:
            horas[hora] = HoraAvaliada(grade_da_hora(estacoes, base), estacoes)
    return horas


def horas_para_mapear(horas: dict, todas_as_horas: bool = False) -> dict:
    """Horas que ganham mapa próprio.

    O critério é o nível observado **nas estações**, não o da superfície interpolada: a estação é
    o dado medido, e a superfície pode mostrar nível 2 onde nenhuma estação chegou a 2.
    """
    if todas_as_horas:
        return dict(horas)
    return {hora: avaliada for hora, avaliada in horas.items()
            if avaliada.nivel_estacoes >= config.NIVEL_MAPA_HORARIO}


# =====================================================
# MAPAS
# =====================================================
def _espec_classes(titulo: str, subtitulo: str, arquivo: str) -> EspecClasses:
    return EspecClasses(titulo, subtitulo, arquivo, config.CORES_RISCO, config.ROTULOS_RISCO)


def _indicadores_condicoes() -> Indicadores:
    """Pontos das condições atendidas nos mapas horários: temperatura à esquerda, umidade no meio, rajada à direita."""
    rotulos = [f"Temperatura máx. ≥ {config.LIMIAR_TEMP_MAX:g} °C",
               f"Umidade mín. ≤ {config.LIMIAR_UMIDADE_MIN:g} %",
               f"Rajada ≥ {config.LIMIAR_RAJADA:g} km/h"]
    return Indicadores(COLUNAS_CONDICOES, rotulos, config.CORES_CONDICOES, "Condições atendidas (esquerda → direita)")


def _niveis_continuos(grade: np.ndarray):
    """Faixas da barra de cores; se a grade for constante, abre a escala para o contourf funcionar."""
    minimo, maximo = float(np.min(grade)), float(np.max(grade))
    return 20 if maximo > minimo else np.linspace(minimo, minimo + 1, 11)


def gerar_mapas(tabela: pd.DataFrame, horas: dict, periodo: Periodo, base: BaseCartografica,
                pasta: Path, todas_as_horas: bool = False) -> None:
    """Mapas de síntese (nível máximo e horas em risco alto) e os mapas horários."""
    gdf = mapas.preparar_pontos(tabela, COLUNA_NIVEL, TITULO)
    if gdf is None:
        return
    pasta.mkdir(parents=True, exist_ok=True)
    identificador = periodo.identificador
    subtitulo = periodo.descrever_janela(*periodo.janela)

    espec = _espec_classes(f"Risco de fogo — nível máximo em {config.NOME_UF}", subtitulo,
                           f"Mapa_Risco_Fogo_Nivel_{config.UF}")
    mapas.mapa_classes_pontual(gdf, COLUNA_NIVEL, espec, base, pasta / f"{espec.arquivo}_{identificador}.png")

    exposicao = EspecMapa(ABA, COLUNA_HORAS_ALTO, f"Horas em risco alto de fogo em {config.NOME_UF}", subtitulo,
                          f"Mapa_Risco_Fogo_Horas_{config.UF}", "YlOrRd", "Horas em risco alto",
                          "5 MAIORES EXPOSIÇÕES")
    mapas.mapa_pontual(gdf, exposicao, base, pasta / f"{exposicao.arquivo}_{identificador}.png")

    if horas:
        # As duas superfícies saem da mesma pilha horária: o máximo célula a célula e a contagem
        # de horas em que a célula chegou ao nível alto.
        grades = [avaliada.grade for avaliada in horas.values()]
        mapas.mapa_classes_interpolado(np.maximum.reduce(grades), gdf, COLUNA_NIVEL, espec, base,
                                       pasta / f"{espec.arquivo}_{identificador}_interpolado.png")
        contagem = sum((grade == NIVEL_ALTO).astype(int) for grade in grades)
        mapas.mapa_de_grade(contagem, gdf, exposicao, base,
                            pasta / f"{exposicao.arquivo}_{identificador}_interpolado.png",
                            _niveis_continuos(contagem))

    _mapas_horarios(horas, base, pasta / "horas", todas_as_horas)


def _mapas_horarios(horas: dict, base: BaseCartografica, pasta: Path, todas_as_horas: bool) -> None:
    """Um mapa interpolado por hora, nas horas selecionadas por horas_para_mapear.

    As estações de cada mapa (rótulos e contagem da legenda) são as daquela hora, com o nível
    daquela hora — e não a tabela do dia, que traz o nível máximo.
    """
    selecionadas = horas_para_mapear(horas, todas_as_horas)
    if not selecionadas:
        print(f"ℹ️ Nenhuma estação chegou ao {config.ROTULOS_RISCO[config.NIVEL_MAPA_HORARIO].lower()} "
              f"em nenhuma hora: sem mapas horários.")
        return

    pasta.mkdir(parents=True, exist_ok=True)
    print(f"\n🕐 Mapas horários: {len(selecionadas)} de {len(horas)} horas")
    for hora, avaliada in sorted(selecionadas.items()):
        local = hora.tz_convert(config.FUSO_MS)
        espec = _espec_classes(f"Risco de fogo em {config.NOME_UF}",
                               f"{local:%d/%m/%Y %H:%M} (horário de MS)",
                               f"Mapa_Risco_Fogo_{config.UF}")
        estacoes = mapas.preparar_pontos(avaliada.estacoes, COLUNA_NIVEL_HORA, espec.subtitulo)
        mapas.mapa_classes_interpolado(avaliada.grade, estacoes, COLUNA_NIVEL_HORA, espec, base,
                                       pasta / f"{espec.arquivo}_{local:%Y%m%d_%H}h.png", _indicadores_condicoes())


# =====================================================
# EXECUÇÃO
# =====================================================
def executar(periodo: Periodo, opcoes: dict | None = None) -> int:
    """Coleta os dados, calcula o risco e grava a planilha e os mapas. Retorna 0 se deu certo."""
    opcoes = opcoes or {}
    print("=" * 60)
    print(f"🔥 {TITULO} — {config.NOME_UF}")
    print(f"📅 Modo: {periodo.nome_modo}")
    print(f"📅 {periodo.descricao}")
    print("=" * 60)

    if periodo.modo == "periodo":
        print("❌ Este produto gera mapas de um dia ou do tempo real, não de período.")
        print("   Use uma data específica (--inicio) ou --tempo-real.")
        return 1

    try:
        coletados = inmet.baixar_estacoes(*periodo.janela)
    except inmet.ErroINMET as erro:
        print(f"❌ Erro ao listar estações: {erro}")
        return 1

    completas, incompletas = [], []
    for estacao, dados in coletados:
        leituras = leituras_validas(dados, periodo)
        (completas if not leituras.empty else incompletas).append((estacao, leituras))
    if incompletas:
        nomes = ", ".join(estacao["Estação"] for estacao, _ in incompletas)
        print(f"\n⚠️ Fora do mapa por faltar temperatura, umidade ou rajada ({len(incompletas)}): {nomes}")

    if not completas:
        print("❌ Nenhuma estação tem as três variáveis no período. Nada a calcular.")
        return 1

    tabela = montar_tabela([resumir_estacao(leituras, estacao) for estacao, leituras in completas])
    print("\n🔥 Estações por nível de risco:")
    for nivel in range(len(config.ROTULOS_RISCO) - 1, -1, -1):
        print(f"   {config.ROTULOS_RISCO[nivel]}: {(tabela[COLUNA_NIVEL] == nivel).sum()} estações")

    pasta = periodo.pasta_saida(NOME)
    excel.salvar_relatorio({ABA: tabela}, pasta / f"Risco_Fogo_{config.UF}_{periodo.identificador}.xlsx")

    print("\n🗺️ Gerando mapas...")
    try:
        base = mapas.carregar_base()
    except Exception as erro:
        print(f"⚠️ Não foi possível carregar os shapefiles: {erro}")
        return 1
    horas = analisar_horas(completas, periodo, base)
    gerar_mapas(tabela, horas, periodo, base, pasta / "mapas", opcoes.get("hrtodas", False))

    print("=" * 60)
    print(f"✅ Concluído. Arquivos em: {pasta}")
    print("=" * 60)
    return 0
