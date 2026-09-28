"""Coleta com cache local, usada pelo explorador.

Os produtos continuam baixando direto da API. O cache existe porque no explorador a mesma
janela é consultada muitas vezes seguidas, a cada vez que se mexe num filtro, e baixar 60
estações de novo a cada clique tornaria a tela inutilizável.

O cache fica em `cache/`, fora do controle de versão, e pode ser apagado a qualquer momento:
o que faltar é baixado outra vez.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

from modulos import config, inmet

PASTA_CACHE = config.RAIZ / "cache"

# Ordem em que as colunas saem na tabela para ver e baixar. A API devolve as chaves em ordem
# arbitrária (é JSON), e uma planilha com PRE_MAX antes de TEM_INS não se lê. O que não estiver
# nesta lista sai no fim, como veio: se o INMET publicar uma coluna nova, ela aparece sozinha.
ORDEM_DAS_COLUNAS = [
    "Estação", "CD_ESTACAO", "DC_NOME", "UF", "VL_LATITUDE", "VL_LONGITUDE",
    "Data/Hora (MS)", "DT_MEDICAO", "HR_MEDICAO",
    "TEM_INS", "TEM_MAX", "TEM_MIN", "TEM_SEN",
    "UMD_INS", "UMD_MAX", "UMD_MIN",
    "PTO_INS", "PTO_MAX", "PTO_MIN",
    "PRE_INS", "PRE_MAX", "PRE_MIN",
    "VEN_VEL", "VEN_DIR", "VEN_RAJ",
    "RAD_GLO", "CHUVA",
    "TEN_BAT", "TEM_CPU",
]


def leituras(codigo: str, inicio: datetime, fim: datetime) -> pd.DataFrame:
    """Série horária de uma estação na janela (início, fim], vinda do cache quando ele já a cobre.

    As horas mais recentes do dia em curso podem não estar no cache: para o dia de hoje, prefira
    consultar de novo mais tarde.
    """
    guardadas = _ler(codigo)
    faltando = _faltando(guardadas, inicio, fim)
    for comeco, termino in faltando:
        guardadas = _juntar(guardadas, inmet.baixar_dados_estacao(codigo, comeco, termino))
    if faltando:
        _gravar(codigo, guardadas)
    if guardadas is None:
        return pd.DataFrame()
    return guardadas[(guardadas["dt_utc"] > inicio) & (guardadas["dt_utc"] <= fim)].copy()


def varias(codigos: tuple[str, ...], nomes: tuple[str, ...], inicio: datetime, fim: datetime,
           aviso=None) -> tuple[pd.DataFrame, list[str]]:
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
        tarefas = {equipe.submit(leituras, codigo, inicio, fim): nome
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


def planilha(leituras: pd.DataFrame) -> pd.DataFrame:
    """A tabela inteira, como a API a entrega, pronta para ver e baixar.

    **Todas** as colunas, e não só as das grandezas escolhidas na barra lateral: quem baixa o
    dado costuma querer o bruto — para conferir uma suspeita, levar para outro programa ou olhar
    a bateria da estação —, e ter de voltar ao filtro para isso é atrito à toa.

    `dt_utc` fica de fora porque DT_MEDICAO e HR_MEDICAO já dizem o mesmo, do jeito do INMET.
    """
    if leituras.empty:
        return leituras
    tabela = (leituras.rename(columns={"dt_local": "Data/Hora (MS)"})
              .drop(columns=["dt_utc"], errors="ignore"))
    conhecidas = [coluna for coluna in ORDEM_DAS_COLUNAS if coluna in tabela]
    demais = [coluna for coluna in tabela.columns if coluna not in ORDEM_DAS_COLUNAS]
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
    tem_desde, tem_ate = guardadas["dt_utc"].min(), guardadas["dt_utc"].max()
    pedacos = []
    if inicio < tem_desde:
        pedacos.append((inicio, min(fim, tem_desde)))
    if fim > tem_ate:
        pedacos.append((max(inicio, tem_ate), fim))
    return pedacos


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
