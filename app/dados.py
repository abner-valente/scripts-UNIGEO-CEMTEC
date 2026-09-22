"""Coleta com cache local, usada pelo explorador.

Os produtos continuam baixando direto da API. O cache existe porque no explorador a mesma
janela é consultada muitas vezes seguidas, a cada vez que se mexe num filtro, e baixar 60
estações de novo a cada clique tornaria a tela inutilizável.

O cache fica em `cache/`, fora do controle de versão, e pode ser apagado a qualquer momento:
o que faltar é baixado outra vez.
"""
from datetime import datetime
from pathlib import Path

import pandas as pd

from modulos import config, inmet

PASTA_CACHE = config.RAIZ / "cache"


def leituras(codigo: str, inicio: datetime, fim: datetime) -> pd.DataFrame:
    """Série horária de uma estação na janela (início, fim], vinda do cache quando ele já a cobre.

    As horas mais recentes do dia em curso podem não estar no cache: para o dia de hoje, prefira
    consultar de novo mais tarde.
    """
    guardadas = _ler(codigo)
    if not _cobre(guardadas, inicio, fim):
        guardadas = _juntar(guardadas, inmet.baixar_dados_estacao(codigo, inicio, fim))
        _gravar(codigo, guardadas)
    if guardadas is None:
        return pd.DataFrame()
    return guardadas[(guardadas["dt_utc"] > inicio) & (guardadas["dt_utc"] <= fim)].copy()


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


def _cobre(guardadas: pd.DataFrame | None, inicio: datetime, fim: datetime) -> bool:
    return (guardadas is not None and not guardadas.empty
            and guardadas["dt_utc"].min() <= inicio and guardadas["dt_utc"].max() >= fim)


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
    dados.to_pickle(_arquivo(codigo))
