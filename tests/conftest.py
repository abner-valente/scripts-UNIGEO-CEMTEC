"""Fixtures dos testes: dados horários sintéticos, API do INMET simulada (sem acesso à rede) e o banco de teste."""
import os
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import requests

from modulos import banco, config, inmet, mapas

# O schema dos testes do banco, apagado e recriado a cada módulo de testes que o usa
SCHEMA_DE_TESTE = "climageo_teste"

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

# Duas vizinhas de fora de MS, logo depois da divisa. Existem para o caminho das estações de
# apoio ser exercitado sem rede: sem elas o simulador só conhecia a lista do estado, e a lista
# nacional — que as vizinhas consultam — ia buscar a resposta no INMET de verdade.
VIZINHAS = [
    ("A712", "GUAIRA", -54.25, -24.08, "PR"),
    ("A713", "PRESIDENTE EPITACIO", -52.11, -21.77, "SP"),
]


def serie_horaria(inicio: str, fim: str, **valores) -> pd.DataFrame:
    horas = pd.date_range(pd.Timestamp(inicio, tz="UTC"), pd.Timestamp(fim, tz="UTC"), freq="h", inclusive="left")
    dados = pd.DataFrame({"dt_utc": horas, **{**VALORES_PADRAO, **valores}})
    dados["dt_local"] = dados["dt_utc"].dt.tz_convert(config.FUSO_MS)
    return dados


@pytest.fixture
def serie():
    return serie_horaria


@pytest.fixture(autouse=True)
def fonte_das_apis(monkeypatch):
    # Os testes não herdam o FONTE_DADOS do .env da máquina; os da fonte "banco" a pedem
    monkeypatch.setattr(config, "FONTE_DADOS", "apis")


@pytest.fixture
def mapas_sem_desenho(monkeypatch):
    # Para os testes que não são sobre o desenho: o mapa é montado inteiro, mas não passa pelo
    # ajuste da figura nem vira PNG de verdade, que levavam ~0,4 s por mapa. O arquivo sai vazio,
    # com o nome certo. Cada produto mantém uma execução completa que desenha de verdade.
    def finalizar(fig, ax, espec, base, caminho, tela=None):
        if caminho is not None:
            caminho.write_bytes(b"")
            print(f"🗺️ Mapa salvo: {caminho.name}")
        return fig
    monkeypatch.setattr(mapas, "_finalizar", finalizar)


@pytest.fixture(autouse=True)
def sem_esperas(monkeypatch):
    monkeypatch.setattr(config, "PAUSA_ENTRE_REQUISICOES", 0)
    monkeypatch.setattr(config, "PAUSA_ENTRE_TENTATIVAS", 0)
    monkeypatch.setattr(config, "PAUSA_NO_LIMITE", 0)
    monkeypatch.setattr(config, "INTERVALO_DO_COLETOR", 0)


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    def recusar(self, metodo, url, *args, **kwargs):
        raise RuntimeError(f"teste tentou acessar a rede: {metodo} {url.split('/token/')[0]}")

    monkeypatch.setattr(requests.Session, "request", recusar)


@pytest.fixture
def api_simulada(monkeypatch, tmp_path):
    estacoes = pd.DataFrame(ESTACOES, columns=["CD_ESTACAO", "DC_NOME", "VL_LONGITUDE", "VL_LATITUDE"])
    estacoes["SG_ESTADO"] = config.UF
    estacoes["Estação"] = estacoes["DC_NOME"].map(inmet.formatar_nome_estacao)

    def baixar(codigo, inicio, fim, fuso=config.FUSO_MS):
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
        dados["dt_local"] = dados["dt_utc"].dt.tz_convert(fuso)
        return dados

    monkeypatch.setattr(inmet, "listar_estacoes", lambda uf=config.UF: estacoes.copy())
    vizinhas = pd.DataFrame(VIZINHAS, columns=["CD_ESTACAO", "DC_NOME", "VL_LONGITUDE",
                                               "VL_LATITUDE", "SG_ESTADO"])
    vizinhas["Estação"] = vizinhas["DC_NOME"].map(inmet.formatar_nome_estacao)
    recorte = pd.concat([estacoes, vizinhas], ignore_index=True)
    monkeypatch.setattr(inmet, "estacoes_do_recorte", lambda *args, **kwargs: recorte.copy())
    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar)
    monkeypatch.setattr(config, "TOKEN_INMET", "token-de-teste")
    monkeypatch.setattr(config, "PASTA_SAIDA", tmp_path)
    monkeypatch.setattr(config, "DPI", 40)  # mapas pequenos, para o teste ser rápido
    return SimpleNamespace(estacoes_com_dados=len(ESTACOES) - 1,
                           vizinhas={nome for nome in vizinhas["Estação"]})


@pytest.fixture(scope="module")
def conexao():
    if not os.getenv("BANCO_TESTE", "").strip():
        pytest.skip("sem BANCO_TESTE no .env: os testes do banco ficam para a CI")
    with banco.conectar("teste", schema=SCHEMA_DE_TESTE) as aberta:
        banco.recriar_schema_de_teste(aberta, SCHEMA_DE_TESTE)
        banco.criar_esquema(aberta)
        yield aberta


@pytest.fixture
def limpa(conexao):
    for tabela in banco.PARTICOES:
        for nome in banco.particoes(conexao, tabela):
            conexao.execute(f'DROP TABLE "{nome}"')
    conexao.execute("TRUNCATE previsao_semanal, estacao CASCADE")
    conexao.commit()
    return conexao
