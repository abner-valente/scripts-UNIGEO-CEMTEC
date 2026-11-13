"""Coleta com cache local, usada pelo explorador.

Os produtos continuam baixando direto da API. O cache existe porque no explorador a mesma
janela é consultada muitas vezes seguidas, a cada vez que se mexe num filtro, e baixar 60
estações de novo a cada clique tornaria a tela inutilizável.

O cache fica em `cache/`, fora do controle de versão, e pode ser apagado a qualquer momento:
o que faltar é baixado outra vez.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from modulos import config, fonte

PASTA_CACHE = config.RAIZ / "cache"

# Colunas que toda linha tem mesmo quando nada foi medido: elas dizem de que estação e de que
# hora a linha é, e por isso não servem para saber se a medição já chegou.
IDENTIFICACAO = {"DC_NOME", "UF", "CD_ESTACAO", "DT_MEDICAO", "HR_MEDICAO",
                 "VL_LATITUDE", "VL_LONGITUDE", "dt_utc", "dt_local", "Estação"}
# Teto de quanto se reconfere para trás. Existe para uma janela antiga com buraco permanente —
# estação que ficou fora do ar em julho — não ser rebaixada inteira a cada consulta.
HORAS_A_RECONFERIR = 48

# Nome da coluna de horário local, sem a sigla. Ela entra na hora de montar a tabela: é a
# sigla que diz de que fuso se fala, e "Data/Hora (MS)" num CSV de SC seria mentira.
DATA_LOCAL = "Data/Hora"


# Ordem em que as colunas saem na tabela para ver e baixar. A API devolve as chaves em ordem
# arbitrária (é JSON), e uma planilha com PRE_MAX antes de TEM_INS não se lê. O que não estiver
# nesta lista sai no fim, como veio: se o INMET publicar uma coluna nova, ela aparece sozinha.
ORDEM_DAS_COLUNAS = [
    "Estação", "CD_ESTACAO", "DC_NOME", "UF", "VL_LATITUDE", "VL_LONGITUDE",
    DATA_LOCAL, "DT_MEDICAO", "HR_MEDICAO",
    "TEM_INS", "TEM_MAX", "TEM_MIN", "TEM_SEN",
    "UMD_INS", "UMD_MAX", "UMD_MIN",
    "PTO_INS", "PTO_MAX", "PTO_MIN",
    "PRE_INS", "PRE_MAX", "PRE_MIN",
    "VEN_VEL", "VEN_DIR", "VEN_RAJ",
    "RAD_GLO", "CHUVA",
    "TEN_BAT", "TEM_CPU",
]


def leituras(codigo: str, inicio: datetime, fim: datetime,
             fuso: ZoneInfo = config.FUSO_MS) -> pd.DataFrame:
    guardadas = _ler(codigo)
    faltando = _faltando(guardadas, inicio, fim)
    for comeco, termino in faltando:
        guardadas = _juntar(guardadas, fonte.leituras_da_estacao(codigo, comeco, termino))
    if faltando:
        _gravar(codigo, guardadas)
    if guardadas is None:
        return pd.DataFrame()
    dentro = guardadas[(guardadas["dt_utc"] > inicio) & (guardadas["dt_utc"] <= fim)].copy()
    # A hora local é recalculada a partir do UTC em vez de vir do arquivo: assim o mesmo pickle
    # serve a MS (GMT-04) e ao Paraná (GMT-03), e ninguém precisa lembrar em que fuso ele foi
    # gravado — que é o tipo de detalhe que vira uma hora errada em silêncio.
    return dentro.assign(dt_local=dentro["dt_utc"].dt.tz_convert(fuso))


def varias(codigos: tuple[str, ...], nomes: tuple[str, ...], inicio: datetime, fim: datetime,
           aviso=None, fuso: ZoneInfo = config.FUSO_MS) -> tuple[pd.DataFrame, list[str]]:
    chegaram, falharam = {}, []
    with ThreadPoolExecutor(max_workers=config.DOWNLOADS_SIMULTANEOS) as equipe:
        tarefas = {equipe.submit(leituras, codigo, inicio, fim, fuso): nome
                   for codigo, nome in zip(codigos, nomes)}
        for concluidas, tarefa in enumerate(as_completed(tarefas), start=1):
            nome = tarefas[tarefa]
            try:
                dados = tarefa.result()
            except fonte.ErroFonte:
                falharam.append(nome)
            else:
                if dados.empty:
                    falharam.append(nome)
                else:
                    chegaram[nome] = dados
            if aviso is not None:
                aviso(concluidas, len(tarefas))

    series = [chegaram[nome].assign(Estação=nome) for nome in nomes if nome in chegaram]
    if not series:
        return _sem_leituras(fuso), sorted(falharam)
    return pd.concat(series, ignore_index=True), sorted(falharam)


def _sem_leituras(fuso: ZoneInfo) -> pd.DataFrame:
    return pd.DataFrame({"Estação": pd.Series(dtype=object),
                         "dt_utc": pd.Series(dtype=pd.DatetimeTZDtype(tz="UTC")),
                         "dt_local": pd.Series(dtype=pd.DatetimeTZDtype(tz=fuso))})


def planilha(leituras: pd.DataFrame, uf: str = config.UF) -> pd.DataFrame:
    if leituras.empty:
        return leituras
    data_local = f"{DATA_LOCAL} ({uf})"
    tabela = (leituras.rename(columns={"dt_local": data_local})
              .drop(columns=["dt_utc"], errors="ignore"))
    ordem = [data_local if coluna == DATA_LOCAL else coluna for coluna in ORDEM_DAS_COLUNAS]
    conhecidas = [coluna for coluna in ordem if coluna in tabela]
    demais = [coluna for coluna in tabela.columns if coluna not in ordem]
    return tabela[conhecidas + demais]


def limpar_cache() -> int:
    arquivos = list(PASTA_CACHE.glob("*.pkl")) if PASTA_CACHE.exists() else []
    for arquivo in arquivos:
        arquivo.unlink()
    return len(arquivos)


def tamanho_do_cache() -> tuple[int, float]:
    arquivos = list(PASTA_CACHE.glob("*.pkl")) if PASTA_CACHE.exists() else []
    return len(arquivos), sum(arquivo.stat().st_size for arquivo in arquivos) / (1024 * 1024)


def _faltando(guardadas: pd.DataFrame | None, inicio: datetime,
              fim: datetime) -> list[tuple[datetime, datetime]]:
    # Os pedaços da janela que o cache ainda não tem. Baixar só o que falta é o que faz alargar o
    # período sair barato. Falta toda hora da janela sem linha no cache, onde quer que ela esteja:
    # antes da primeira guardada, depois da última ou num buraco no meio. Até a 0.3.6 o cache era
    # julgado pelos extremos, e o buraco no meio ficava para sempre; ele nasce também do cache que
    # fica dias sem uso: guardado até 05/11 e consultado em 13/11, ele pedia só as últimas 48 horas,
    # e a chuva do meio ficava de fora sem aviso (o mensal de Fátima do Sul saiu com 112,6 mm em vez
    # de 164,4). A hora que a estação não mandou volta a ser pedida a cada consulta: é um pedido a
    # mais por estação, e só do trecho que falta
    if guardadas is None or guardadas.empty:
        return [(inicio, fim)]
    pedacos = []
    comeco_utc, fim_utc = pd.Timestamp(inicio).tz_convert("UTC"), pd.Timestamp(fim).tz_convert("UTC")
    horas = pd.date_range(comeco_utc.floor("h"), fim_utc, freq="h", inclusive="right")
    faltam = horas.difference(pd.DatetimeIndex(guardadas["dt_utc"]).tz_convert("UTC"))
    if len(faltam):
        # Um pedido só, da primeira à última hora que falta (a janela de cada hora é (h - 1 h, h]),
        # começando na última hora com medida antes do buraco: as horas vazias logo antes dele são
        # as que o INMET ainda não tinha preenchido quando o cache as guardou
        comeco = faltam.min() - timedelta(hours=1)
        medida_antes = _ultima_com_medida(guardadas[guardadas["dt_utc"] < faltam.min()])
        if medida_antes is not None:
            comeco = min(comeco, medida_antes)
        pedacos.append((max(inicio, comeco), faltam.max()))

    # A ponta recente é sempre refeita. O INMET publica a linha da hora **antes** das medidas e
    # as preenche depois; julgar a cobertura pela última linha faz o vazio ficar guardado para
    # sempre — o mapa da tarde nasce em branco e continua em branco no dia seguinte, e consultar
    # de novo não adianta porque a linha está lá.
    confiavel = _ultima_com_medida(guardadas)
    piso = fim - timedelta(hours=HORAS_A_RECONFERIR)
    ponta = max(confiavel, piso) if confiavel is not None else piso
    if fim > ponta:
        pedacos.append((max(inicio, ponta), fim))
    return _unir(pedacos)


def _unir(pedacos: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    # Os pedaços que se encostam ou se cobrem viram um pedido só
    unidos = []
    for comeco, termino in sorted(pedacos, key=lambda pedaco: pedaco[0]):
        if unidos and comeco <= unidos[-1][1]:
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], termino))
        else:
            unidos.append((comeco, termino))
    return unidos


def _ultima_com_medida(guardadas: pd.DataFrame):
    medidas = [coluna for coluna in guardadas.columns if coluna not in IDENTIFICACAO]
    if not medidas:
        return None
    preenchidas = guardadas[guardadas[medidas].notna().any(axis=1)]
    return None if preenchidas.empty else preenchidas["dt_utc"].max()


def _juntar(guardadas: pd.DataFrame | None, baixadas: pd.DataFrame | None) -> pd.DataFrame | None:
    partes = [parte for parte in (guardadas, baixadas) if parte is not None and not parte.empty]
    if not partes:
        return None
    return (pd.concat(partes, ignore_index=True)
            .drop_duplicates(subset="dt_utc", keep="last")
            .sort_values("dt_utc", ignore_index=True))


def _arquivo(codigo: str) -> Path:
    return PASTA_CACHE / f"{codigo}.pkl"


def _ler(codigo: str) -> pd.DataFrame | None:
    caminho = _arquivo(codigo)
    return pd.read_pickle(caminho) if caminho.exists() else None


def _gravar(codigo: str, dados: pd.DataFrame | None) -> None:
    if dados is None or dados.empty:
        return
    PASTA_CACHE.mkdir(parents=True, exist_ok=True)
    # Grava num arquivo à parte e só então o põe no lugar: com as estações baixando em paralelo,
    # duas gravações da mesma estação ao mesmo tempo deixariam um pickle pela metade, que na
    # próxima leitura quebraria a tela inteira.
    provisorio = _arquivo(codigo).with_suffix(".parcial")
    dados.to_pickle(provisorio)
    provisorio.replace(_arquivo(codigo))
