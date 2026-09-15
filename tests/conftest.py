"""Fixtures dos testes: dados horários sintéticos e API do INMET simulada (sem acesso à rede)."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from modulos import config, inmet

VALORES_PADRAO = {"TEM_MIN": 20.0, "TEM_MAX": 30.0, "UMD_MIN": 50.0, "VEN_RAJ": 5.0, "VEN_DIR": 90.0, "CHUVA": 1.0}

# Estações fictícias em cidades de MS: (código, nome, longitude, latitude)
ESTACOES = [
    ("A701", "CAMPO GRANDE", -54.62, -20.45),
    ("A702", "DOURADOS", -54.81, -22.22),
    ("A703", "CORUMBA", -57.65, -19.01),
    ("A704", "TRES LAGOAS", -51.70, -20.79),
    ("A705", "PONTA PORA", -55.73, -22.54),
    ("A706", "COXIM", -54.76, -18.51),
    ("A707", "AQUIDAUANA", -55.79, -20.47),
    ("A708", "NAVIRAI", -54.19, -23.06),
    ("A709", "PARANAIBA", -51.19, -19.68),
    ("A710", "BONITO", -56.48, -21.12),
    ("A711", "ESTACAO SEM DADOS", -53.50, -21.50),
]
CODIGO_SEM_DADOS = "A711"


def serie_horaria(inicio: str, fim: str, **valores) -> pd.DataFrame:
    """Registros horários em [início, fim), em UTC, no formato devolvido por inmet.baixar_dados_estacao."""
    horas = pd.date_range(pd.Timestamp(inicio, tz="UTC"), pd.Timestamp(fim, tz="UTC"), freq="h", inclusive="left")
    dados = pd.DataFrame({"dt_utc": horas, **{**VALORES_PADRAO, **valores}})
    dados["dt_local"] = dados["dt_utc"].dt.tz_convert(config.FUSO_MS)
    return dados


@pytest.fixture
def serie():
    return serie_horaria


@pytest.fixture
def api_simulada(monkeypatch, tmp_path):
    """Troca a API do INMET por dados sintéticos e grava as saídas numa pasta temporária."""
    estacoes = pd.DataFrame(ESTACOES, columns=["CD_ESTACAO", "DC_NOME", "VL_LONGITUDE", "VL_LATITUDE"])
    estacoes["SG_ESTADO"] = config.UF
    estacoes["Estação"] = estacoes["DC_NOME"].map(inmet.formatar_nome_estacao)

    def baixar(codigo, inicio, fim):
        if codigo == CODIGO_SEM_DADOS:
            return None
        n = int(codigo[1:]) - 700
        # Como a API: dias UTC inteiros, do dia do início ao dia do fim
        horas = pd.date_range(pd.Timestamp(inicio).floor("D"), pd.Timestamp(fim).floor("D") + pd.Timedelta(days=1),
                              freq="h", inclusive="left")
        hora = horas.hour.to_numpy()
        ciclo = np.sin((hora - 12) / 24 * 2 * np.pi)
        temperatura = 18 + n + 6 * ciclo
        dados = pd.DataFrame({
            "dt_utc": horas,
            "TEM_MIN": temperatura - 1,
            "TEM_MAX": temperatura + 1,
            "UMD_MIN": 60 - 2 * n - 15 * ciclo,
            "VEN_RAJ": 3 + n + 2 * (ciclo + 1),
            "VEN_DIR": (hora * 15 + 20 * n) % 360.0,
            "CHUVA": np.where(hora % 6 == 0, 0.2 * n, 0.0),  # chove em todas as estações a cada 6 h
        })
        dados["dt_local"] = dados["dt_utc"].dt.tz_convert(config.FUSO_MS)
        return dados

    monkeypatch.setattr(inmet, "listar_estacoes", lambda uf=config.UF: estacoes.copy())
    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar)
    monkeypatch.setattr(config, "TOKEN_INMET", "token-de-teste")
    monkeypatch.setattr(config, "PASTA_SAIDA", tmp_path)
    monkeypatch.setattr(config, "PAUSA_ENTRE_REQUISICOES", 0)
    monkeypatch.setattr(config, "DPI", 40)  # mapas pequenos, para o teste ser rápido
    return SimpleNamespace(estacoes_com_dados=len(ESTACOES) - 1)
