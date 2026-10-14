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

from .. import calculos, config, excel, fonte, graficos, mapas
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
NIVEL_MEDIO = 2
NIVEL_ALTO = 3


# =====================================================
# A REGRA 30-30-30
# =====================================================
def condicoes_atendidas(temp_max, umidade_min, rajada_kmh) -> tuple:
    return (np.asarray(temp_max) >= config.LIMIAR_TEMP_MAX,
            np.asarray(umidade_min) <= config.LIMIAR_UMIDADE_MIN,
            np.asarray(rajada_kmh) >= config.LIMIAR_RAJADA)


def contar_condicoes(temp_max, umidade_min, rajada_kmh):
    return sum(condicao.astype(int) for condicao in condicoes_atendidas(temp_max, umidade_min, rajada_kmh))


def leituras_validas(dados: pd.DataFrame, periodo: Periodo) -> pd.DataFrame:
    recorte = calculos.recortar(dados, *periodo.janela)
    if not set(COLUNAS_NECESSARIAS) <= set(recorte.columns):
        return recorte.iloc[0:0]
    recorte = recorte.dropna(subset=COLUNAS_NECESSARIAS)
    return recorte.assign(rajada_kmh=recorte["VEN_RAJ"] * 3.6)


def niveis_por_hora(leituras: pd.DataFrame) -> pd.Series:
    niveis = contar_condicoes(leituras["TEM_MAX"], leituras["UMD_MIN"], leituras["rajada_kmh"])
    return pd.Series(niveis, index=pd.Index(leituras["dt_utc"]), dtype=int)


def dia_da_leitura(horas: pd.DatetimeIndex, fuso=None):
    return (horas - pd.Timedelta(hours=1)).tz_convert(fuso or config.FUSO_MS).date


def horas_da_janela(periodo: Periodo) -> pd.DatetimeIndex:
    inicio, fim = (pd.Timestamp(momento) for momento in periodo.janela)
    primeira = inicio.ceil("h")
    if primeira <= inicio:  # a janela é aberta no início: a leitura do próprio início não entra
        primeira += pd.Timedelta(hours=1)
    return pd.date_range(primeira, fim.floor("h"), freq="h")


# =====================================================
# CÁLCULOS POR ESTAÇÃO
# =====================================================
def resumir_estacao(leituras: pd.DataFrame, estacao: pd.Series, periodo: Periodo,
                    uf: str = config.UF) -> dict:
    niveis = niveis_por_hora(leituras)
    linha = {
        "Estação": estacao["Estação"],
        COLUNA_NIVEL: int(niveis.max()),
        COLUNA_HORAS_ALTO: int((niveis == NIVEL_ALTO).sum()),
        "Horas Nível 2": int((niveis == NIVEL_MEDIO).sum()),
        "Horas Nível 1": int((niveis == 1).sum()),
        "Horas com Dados": int(len(niveis)),
    }
    if periodo.modo == "periodo":
        linha.update(_dias_por_nivel(niveis, periodo.fuso))
    linha.update({
        "Temp. Máxima (°C)": round(float(leituras["TEM_MAX"].max()), 1),
        "Umidade Mínima (%)": round(float(leituras["UMD_MIN"].min()), 1),
        "Rajada Máxima (km/h)": round(float(leituras["rajada_kmh"].max()), 1),
        f"Primeiro Horário em Risco Médio ({uf})": _primeiro_horario(niveis, NIVEL_MEDIO, periodo.fuso),
        f"Primeiro Horário em Risco Alto ({uf})": _primeiro_horario(niveis, NIVEL_ALTO, periodo.fuso),
        "Latitude": estacao["VL_LATITUDE"],
        "Longitude": estacao["VL_LONGITUDE"],
    })
    return linha


def _dias_por_nivel(niveis: pd.Series, fuso=None) -> dict:
    por_dia = niveis.groupby(dia_da_leitura(pd.DatetimeIndex(niveis.index), fuso)).max()
    return {"Dias com Risco Alto": int((por_dia == NIVEL_ALTO).sum()),
            "Dias com Risco Médio": int((por_dia == NIVEL_MEDIO).sum())}


def montar_tabela(resumos: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(resumos).sort_values(
        [COLUNA_NIVEL, COLUNA_HORAS_ALTO], ascending=False, ignore_index=True
    )


def _primeiro_horario(niveis: pd.Series, nivel: int, fuso=None) -> str:
    alcancadas = niveis[niveis >= nivel]
    if alcancadas.empty:
        return ""
    return alcancadas.index.min().tz_convert(fuso or config.FUSO_MS).strftime("%d/%m/%Y %H:%M")


# =====================================================
# GRADES HORÁRIAS (as três variáveis interpoladas separadamente)
# =====================================================
def estacoes_na_hora(coletados: list, hora) -> pd.DataFrame:
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
    campos = [
        calculos.interpolar_idw(pontos["Longitude"], pontos["Latitude"], pontos[coluna],
                                base.lon_grade, base.lat_grade, config.IDW_VIZINHOS, config.IDW_POTENCIA)
        for coluna in ("TEM_MAX", "UMD_MIN", "rajada_kmh")
    ]
    # int8 basta para valores de 0 a 3 e mantém a memória baixa: um mês guarda 720 grades destas
    return contar_condicoes(*campos).astype(np.int8)


@dataclass(frozen=True)
class HoraAvaliada:
    grade: np.ndarray
    estacoes: pd.DataFrame

    @property
    def nivel_estacoes(self) -> int:
        return int(self.estacoes[COLUNA_NIVEL_HORA].max())


def analisar_horas(coletados: list, periodo: Periodo, base: BaseCartografica,
                   apoio: list = ()) -> dict:
    horas = {}
    for hora in horas_da_janela(periodo):
        estacoes = estacoes_na_hora(coletados, hora)
        if len(estacoes) >= config.MIN_ESTACOES_INTERPOLACAO:
            vizinhas = estacoes_na_hora(apoio, hora) if apoio else estacoes.iloc[0:0]
            conta = estacoes if vizinhas.empty else pd.concat([estacoes, vizinhas], ignore_index=True)
            horas[hora] = HoraAvaliada(grade_da_hora(conta, base), estacoes)
    return horas


def horas_para_mapear(horas: dict, todas_as_horas: bool = False) -> dict:
    if todas_as_horas:
        return dict(horas)
    return {hora: avaliada for hora, avaliada in horas.items()
            if avaliada.nivel_estacoes >= config.NIVEL_MAPA_HORARIO}


# =====================================================
# MAPAS
# =====================================================
def _espec_classes(titulo: str, subtitulo: str, arquivo: str) -> EspecClasses:
    return EspecClasses(titulo, subtitulo, arquivo, config.CORES_RISCO, config.ROTULOS_RISCO)


def espec_nivel_maximo(sigla: str, subtitulo: str) -> EspecClasses:
    return _espec_classes(f"Risco de Fogo — Nível Máx. em {sigla}", subtitulo,
                          f"Mapa_Risco_Fogo_Nivel_{sigla}")


def espec_horas_em_risco_alto(sigla: str, subtitulo: str) -> EspecMapa:
    return EspecMapa(ABA, COLUNA_HORAS_ALTO, f"Horas Agregadas de risco alto de fogo em {sigla}", subtitulo,
                     f"Mapa_Risco_Fogo_Horas_{sigla}", "YlOrRd", "Horas em risco alto",
                     "5 MAIORES EXPOSIÇÕES", decimais=0)  # horas são contagens: sem casas decimais


def espec_da_hora(local, sigla: str) -> EspecClasses:
    return _espec_classes(f"Risco de Fogo em {sigla}", f"{local:%d/%m/%Y %H:%M} {config._gmt(local)}",
                          f"Mapa_Risco_Fogo_{sigla}")


def rotulos_condicoes() -> list[str]:
    return [f"Temperatura máx. ≥ {config.LIMIAR_TEMP_MAX:g} °C",
            f"Umidade mín. ≤ {config.LIMIAR_UMIDADE_MIN:g} %",
            f"Rajada ≥ {config.LIMIAR_RAJADA:g} km/h"]


def indicadores_condicoes() -> Indicadores:
    return Indicadores(COLUNAS_CONDICOES, rotulos_condicoes(), config.CORES_CONDICOES,
                       "Condições atendidas (esquerda → direita)")


def _niveis_continuos(grade: np.ndarray):
    minimo, maximo = float(np.min(grade)), float(np.max(grade))
    return 20 if maximo > minimo else np.linspace(minimo, minimo + 1, 11)


def gerar_mapas(tabela: pd.DataFrame, horas: dict, periodo: Periodo, base: BaseCartografica,
                pasta: Path, todas_as_horas: bool = False) -> None:
    gdf = mapas.preparar_pontos(tabela, COLUNA_NIVEL, TITULO)
    if gdf is None:
        return
    pasta.mkdir(parents=True, exist_ok=True)
    identificador = periodo.identificador
    subtitulo = periodo.descrever_janela(*periodo.janela)

    sigla = base.recorte.uf
    espec = espec_nivel_maximo(sigla, subtitulo)
    mapas.mapa_classes_pontual(gdf, COLUNA_NIVEL, espec, base, pasta / f"{espec.arquivo}_{identificador}.png")

    exposicao = espec_horas_em_risco_alto(sigla, subtitulo)
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
    selecionadas = horas_para_mapear(horas, todas_as_horas)
    if not selecionadas:
        print(f"ℹ️ Nenhuma estação chegou ao {config.ROTULOS_RISCO[config.NIVEL_MAPA_HORARIO].lower()} "
              f"em nenhuma hora: sem mapas horários.")
        return

    pasta.mkdir(parents=True, exist_ok=True)
    print(f"\n🕐 Mapas horários: {len(selecionadas)} de {len(horas)} horas")
    for hora, avaliada in sorted(selecionadas.items()):
        local = hora.tz_convert(base.recorte.fuso)
        espec = espec_da_hora(local, base.recorte.uf)
        estacoes = mapas.preparar_pontos(avaliada.estacoes, COLUNA_NIVEL_HORA, espec.subtitulo)
        mapas.mapa_classes_interpolado(avaliada.grade, estacoes, COLUNA_NIVEL_HORA, espec, base,
                                       pasta / f"{espec.arquivo}_{local:%Y%m%d_%H}h.png", indicadores_condicoes())


# =====================================================
# GRÁFICOS (só no período)
# =====================================================
def tabela_horaria(completas: list, fuso=None) -> pd.DataFrame:
    quadros = []
    for estacao, leituras in completas:
        atendidas = condicoes_atendidas(leituras["TEM_MAX"], leituras["UMD_MIN"], leituras["rajada_kmh"])
        quadro = pd.DataFrame(dict(zip(COLUNAS_CONDICOES, atendidas)))
        quadro.insert(0, "Estação", estacao["Estação"])
        quadro["dia"] = dia_da_leitura(pd.DatetimeIndex(leituras["dt_utc"]), fuso)
        quadros.append(quadro)
    tabela = pd.concat(quadros, ignore_index=True)
    tabela[COLUNA_NIVEL_HORA] = tabela[COLUNAS_CONDICOES].sum(axis=1)
    return tabela


# Do pior nível para o mais brando, nas barras e nas legendas: a estação que chegou ao risco alto
# mostra o vermelho primeiro (pedido da equipe, 13/10/2026)
NIVEIS_DO_PIOR = [NIVEL_ALTO, NIVEL_MEDIO, 1]


def horas_por_nivel(horas: pd.DataFrame) -> pd.DataFrame:
    return (horas.pivot_table(index="Estação", columns=COLUNA_NIVEL_HORA, values="dia", aggfunc="count")
            .reindex(columns=NIVEIS_DO_PIOR).fillna(0))


def calendario_de_niveis(horas: pd.DataFrame, ordem) -> pd.DataFrame:
    return (horas.pivot_table(index="Estação", columns="dia", values=COLUNA_NIVEL_HORA, aggfunc="max")
            .reindex(ordem))


def gerar_graficos(horas: pd.DataFrame, periodo: Periodo, pasta: Path,
                   recorte: config.Recorte = config.RECORTE) -> None:
    titulo = f"Risco de Fogo por Estação em {recorte.uf}"
    janela = periodo.descrever_janela(*periodo.janela)

    # Ordem das estações: quem passou mais horas em risco alto primeiro. Vale para os três gráficos,
    # para dar para comparar um com o outro.
    por_nivel = horas_por_nivel(horas)
    ordem = por_nivel.sort_values([NIVEL_ALTO, NIVEL_MEDIO, 1]).index

    graficos.barras_empilhadas(
        por_nivel.loc[ordem], [config.CORES_RISCO[nivel] for nivel in NIVEIS_DO_PIOR],
        [config.ROTULOS_RISCO[nivel] for nivel in NIVEIS_DO_PIOR], titulo,
        f"Horas em cada nível de risco · {janela}", "Horas",
        pasta / f"Grafico_Risco_Fogo_Niveis_{recorte.uf}_{periodo.identificador}.png",
        cores_do_texto=["white", graficos.TINTA, graficos.TINTA])

    calendario = calendario_de_niveis(horas, ordem[::-1])
    calendario.columns = [f"{dia:%d/%m}" for dia in calendario.columns]
    graficos.calendario(
        calendario, config.CORES_RISCO, config.ROTULOS_RISCO, titulo, f"Pior nível de cada dia · {janela}",
        pasta / f"Grafico_Risco_Fogo_Calendario_{recorte.uf}_{periodo.identificador}.png",
        sem_dado="Sem dado (nenhuma hora com as três medidas)")

    for dia, leituras in horas.groupby("dia"):
        # Mesma ordem dos outros dois gráficos: barras horizontais crescem de baixo para cima
        condicoes = leituras.groupby("Estação")[COLUNAS_CONDICOES].sum().reindex(ordem).fillna(0)
        graficos.barras_agrupadas(
            condicoes, config.CORES_CONDICOES, rotulos_condicoes(), f"Condições Atendidas em {recorte.uf}",
            f"Horas de cada condição · {dia:%d/%m/%Y}", "Horas",
            pasta / "graficosDeCondicoes" / f"Grafico_Risco_Fogo_Condicoes_{recorte.uf}_{dia:%Y%m%d}.png")


# =====================================================
# EXECUÇÃO
# =====================================================
def executar(periodo: Periodo, opcoes: dict | None = None) -> int:
    opcoes = opcoes or {}
    recorte = opcoes.get("recorte", config.RECORTE)
    print("=" * 60)
    print(f"🔥 {TITULO} — {recorte.nome}")
    print(f"📅 Modo: {periodo.nome_modo}")
    print(f"📅 {periodo.descricao}")
    print("=" * 60)

    try:
        coletados = fonte.leituras_do_estado(*periodo.janela, uf=recorte.uf, fuso=periodo.fuso)
    except fonte.ErroFonte as erro:
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

    tabela = montar_tabela([resumir_estacao(leituras, estacao, periodo, recorte.uf)
                            for estacao, leituras in completas])
    print("\n🔥 Estações por nível de risco:")
    for nivel in range(len(config.ROTULOS_RISCO) - 1, -1, -1):
        print(f"   {config.ROTULOS_RISCO[nivel]}: {(tabela[COLUNA_NIVEL] == nivel).sum()} estações")

    pasta = periodo.pasta_saida(NOME)
    excel.salvar_relatorio({ABA: tabela}, pasta / f"Risco_Fogo_{recorte.uf}_{periodo.identificador}.xlsx")

    print("\n🗺️ Gerando mapas...")
    try:
        base = mapas.carregar_base(recorte)
    except Exception as erro:
        print(f"⚠️ Não foi possível carregar os shapefiles: {erro}")
        return 1
    apoio = []
    for estacao, dados in fonte.leituras_de_apoio(recorte, base.lon_grade[base.dentro_uf],
                                                  base.lat_grade[base.dentro_uf], *periodo.janela,
                                                  fuso=periodo.fuso):
        leituras = leituras_validas(dados, periodo)
        if not leituras.empty:
            apoio.append((estacao, leituras))
    horas = analisar_horas(completas, periodo, base, apoio)
    gerar_mapas(tabela, horas, periodo, base, pasta / "mapas", opcoes.get("hrtodas", False))

    if periodo.modo == "periodo":
        print("\n📈 Gerando gráficos...")
        gerar_graficos(tabela_horaria(completas, periodo.fuso), periodo, pasta, recorte)

    print("=" * 60)
    print(f"✅ Concluído. Arquivos em: {pasta}")
    print("=" * 60)
    return 0
