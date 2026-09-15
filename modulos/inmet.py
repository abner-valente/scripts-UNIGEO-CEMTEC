"""Acesso à API do INMET (apitempo.inmet.gov.br)."""
import time
from datetime import datetime

import pandas as pd
import requests

from . import config

COLUNAS_NUMERICAS = ["TEM_MAX", "TEM_MIN", "UMD_MIN", "UMD_MAX", "VEN_RAJ", "VEN_DIR", "CHUVA"]
PALAVRAS_MINUSCULAS = {"da", "das", "de", "do", "dos", "e"}


class ErroINMET(Exception):
    """Falha ao consultar a API do INMET."""


def _sem_token(texto) -> str:
    """Remove o token de mensagens de erro antes de exibi-las."""
    texto = str(texto)
    return texto.replace(config.TOKEN_INMET, "***") if config.TOKEN_INMET else texto


def formatar_nome_estacao(nome) -> str:
    """Padroniza o nome da estação (ex.: 'PONTA PORA' -> 'Ponta Pora', 'SAO GABRIEL DO OESTE' -> 'Sao Gabriel do Oeste')."""
    palavras = str(nome).strip().title().split()
    return " ".join(
        palavra.lower() if i > 0 and palavra.lower() in PALAVRAS_MINUSCULAS else palavra
        for i, palavra in enumerate(palavras)
    )


def listar_estacoes(uf: str = config.UF) -> pd.DataFrame:
    """Estações automáticas da UF, com coordenadas numéricas e a coluna 'Estação' (nome formatado)."""
    try:
        resposta = requests.get(config.URL_ESTACOES, timeout=config.TIMEOUT_ESTACOES)
        resposta.raise_for_status()
        estacoes = pd.DataFrame(resposta.json())
    except (requests.RequestException, ValueError) as erro:
        raise ErroINMET(_sem_token(erro)) from erro

    estacoes = estacoes[estacoes["SG_ESTADO"] == uf].copy()
    for coluna in ("VL_LATITUDE", "VL_LONGITUDE"):
        estacoes[coluna] = pd.to_numeric(estacoes[coluna], errors="coerce")
    estacoes["Estação"] = estacoes["DC_NOME"].map(formatar_nome_estacao)
    return estacoes


def baixar_dados_estacao(codigo: str, inicio: datetime, fim: datetime) -> pd.DataFrame | None:
    """Dados horários de uma estação com as leituras da janela (início, fim].

    A API trabalha com dias UTC inteiros (data final inclusiva); o recorte exato por
    horário é feito depois, em calculos.recortar. Retorna None se não houver dados.
    """
    dia_inicial = inicio.astimezone(config.FUSO_UTC).date().isoformat()
    dia_final = fim.astimezone(config.FUSO_UTC).date().isoformat()
    url = config.URL_DADOS.format(inicio=dia_inicial, fim=dia_final, codigo=codigo, token=config.TOKEN_INMET)

    try:
        resposta = requests.get(url, timeout=config.TIMEOUT_DADOS)
    except requests.RequestException as erro:
        print(f"    ⚠️ Erro de conexão: {_sem_token(erro)}")
        return None
    if resposta.status_code != 200:
        print(f"    ⚠️ Erro HTTP {resposta.status_code}: {_sem_token(resposta.text[:100])}")
        return None

    try:
        registros = resposta.json()
    except ValueError:
        registros = None
    if not isinstance(registros, list) or not registros:
        print(f"    ⚠️ Sem dados para a estação {codigo}")
        return None

    try:
        dados = pd.DataFrame(registros)
        for coluna in COLUNAS_NUMERICAS:
            if coluna in dados:
                dados[coluna] = pd.to_numeric(dados[coluna].astype(str).str.replace(",", "."), errors="coerce")

        # HR_MEDICAO vem como "HHMM" (ex.: "1300"), em UTC
        horas = dados["HR_MEDICAO"].map(lambda hora: f"{str(hora).zfill(4)[:2]}:00")
        dados["dt_utc"] = pd.to_datetime(dados["DT_MEDICAO"] + " " + horas, errors="coerce", utc=True)
        dados["dt_local"] = dados["dt_utc"].dt.tz_convert(config.FUSO_MS)
    except (KeyError, TypeError, ValueError) as erro:
        print(f"    ⚠️ Erro ao processar dados da estação {codigo}: {erro}")
        return None

    return dados.dropna(subset=["dt_utc"])


def baixar_estacoes(inicio: datetime, fim: datetime, uf: str = config.UF) -> list[tuple[pd.Series, pd.DataFrame]]:
    """Lista as estações da UF e baixa os dados horários de cada uma para a janela (início, fim].

    Retorna (estação, dados) das estações que têm dados. Levanta ErroINMET se a lista de estações falhar.
    """
    estacoes = listar_estacoes(uf)
    print(f"✅ Encontradas {len(estacoes)} estações em {uf}")

    coletados = []
    for _, estacao in estacoes.iterrows():
        print(f"🛰️ Lendo: {estacao['Estação']}...")
        dados = baixar_dados_estacao(estacao["CD_ESTACAO"], inicio, fim)
        time.sleep(config.PAUSA_ENTRE_REQUISICOES)
        if dados is not None:
            print(f"    📊 {len(dados)} registros")
            coletados.append((estacao, dados))

    print(f"\n📊 Estações com dados: {len(coletados)} de {len(estacoes)}")
    return coletados
