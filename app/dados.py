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

from modulos import config, inmet

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
    """Série horária de uma estação na janela (início, fim], vinda do cache quando ele já a cobre.

    As horas mais recentes são sempre reconsultadas: elas chegam da API com a linha criada e as
    medidas ainda vazias, e só se enchem mais tarde.
    """
    guardadas = _ler(codigo)
    faltando = _faltando(guardadas, inicio, fim)
    for comeco, termino in faltando:
        guardadas = _juntar(guardadas, inmet.baixar_dados_estacao(codigo, comeco, termino))
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
    """Séries de várias estações e os nomes das que não vieram.

    É uma consulta por estação, e são 62: uma atrás da outra, uma semana levava ~50 s. Em
    paralelo, ~8 s. Quantas correm juntas é limite do processo inteiro (`modulos/inmet`), e não
    deste lote — o painel é público, e duas pessoas consultando ao mesmo tempo dobrariam o
    assédio à API.

    A tabela sai na ordem em que as estações foram pedidas, e não na ordem em que chegaram: a
    ordem de chegada muda a cada consulta, e com ela mudariam as cores do gráfico.
    """
    chegaram, falharam = {}, []
    with ThreadPoolExecutor(max_workers=config.DOWNLOADS_SIMULTANEOS) as equipe:
        tarefas = {equipe.submit(leituras, codigo, inicio, fim, fuso): nome
                   for codigo, nome in zip(codigos, nomes)}
        for concluidas, tarefa in enumerate(as_completed(tarefas), start=1):
            nome = tarefas[tarefa]
            try:
                dados = tarefa.result()
            except inmet.ErroINMET:
                falharam.append(nome)
            else:
                if dados.empty:
                    falharam.append(nome)
                else:
                    chegaram[nome] = dados
            if aviso is not None:
                aviso(concluidas, len(tarefas))

    series = [chegaram[nome].assign(Estação=nome) for nome in nomes if nome in chegaram]
    return (pd.concat(series, ignore_index=True) if series else pd.DataFrame()), sorted(falharam)


def planilha(leituras: pd.DataFrame, uf: str = config.UF) -> pd.DataFrame:
    """A tabela inteira, como a API a entrega, pronta para ver e baixar.

    **Todas** as colunas, e não só as das grandezas escolhidas na barra lateral: quem baixa o
    dado costuma querer o bruto — para conferir uma suspeita, levar para outro programa ou olhar
    a bateria da estação —, e ter de voltar ao filtro para isso é atrito à toa.

    `dt_utc` fica de fora porque DT_MEDICAO e HR_MEDICAO já dizem o mesmo, do jeito do INMET.
    """
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
    """Apaga o cache e devolve quantos arquivos foram removidos."""
    arquivos = list(PASTA_CACHE.glob("*.pkl")) if PASTA_CACHE.exists() else []
    for arquivo in arquivos:
        arquivo.unlink()
    return len(arquivos)


def tamanho_do_cache() -> tuple[int, float]:
    """Quantidade de estações no cache e o tamanho total em MB."""
    arquivos = list(PASTA_CACHE.glob("*.pkl")) if PASTA_CACHE.exists() else []
    return len(arquivos), sum(arquivo.stat().st_size for arquivo in arquivos) / (1024 * 1024)


def _faltando(guardadas: pd.DataFrame | None, inicio: datetime,
              fim: datetime) -> list[tuple[datetime, datetime]]:
    """Os pedaços da janela que o cache ainda não tem.

    Baixar só o que falta é o que faz alargar o período sair barato: antes, pedir duas semanas
    tendo uma guardada jogava a guardada fora e baixava as duas — o dobro do dado e, numa janela
    longa, o dobro do tempo de resposta da API.

    O cache é julgado pelos extremos, como sempre foi: um buraco no meio (estação fora do ar)
    não se distingue de ausência de leitura, e pedir de novo não o preencheria.
    """
    if guardadas is None or guardadas.empty:
        return [(inicio, fim)]
    tem_desde = guardadas["dt_utc"].min()
    pedacos = []
    if inicio < tem_desde:
        pedacos.append((inicio, min(fim, tem_desde)))

    # A ponta recente é sempre refeita. O INMET publica a linha da hora **antes** das medidas e
    # as preenche depois; julgar a cobertura pela última linha faz o vazio ficar guardado para
    # sempre — o mapa da tarde nasce em branco e continua em branco no dia seguinte, e consultar
    # de novo não adianta porque a linha está lá.
    confiavel = _ultima_com_medida(guardadas)
    piso = fim - timedelta(hours=HORAS_A_RECONFERIR)
    ponta = max(confiavel, piso) if confiavel is not None else piso
    if fim > ponta:
        pedacos.append((max(inicio, ponta), fim))
    return pedacos


def _ultima_com_medida(guardadas: pd.DataFrame):
    """A última hora guardada que trouxe alguma medição — até onde o cache é de confiança."""
    medidas = [coluna for coluna in guardadas.columns if coluna not in IDENTIFICACAO]
    if not medidas:
        return None
    preenchidas = guardadas[guardadas[medidas].notna().any(axis=1)]
    return None if preenchidas.empty else preenchidas["dt_utc"].max()


def _juntar(guardadas: pd.DataFrame | None, baixadas: pd.DataFrame | None) -> pd.DataFrame | None:
    """Junta o que já havia com o que acabou de ser baixado, sem repetir horas."""
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
