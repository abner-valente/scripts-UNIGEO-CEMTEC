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


def _consultar(url: str, timeout: int) -> list | dict:
    """Consulta a API e devolve a resposta já convertida de JSON.

    Falhas passageiras (conexão encerrada, erro 5xx, resposta cortada) são repetidas até
    config.TENTATIVAS vezes, dobrando a espera a cada uma. Levanta ErroINMET se nenhuma der certo.
    """
    espera = config.PAUSA_ENTRE_TENTATIVAS
    for tentativa in range(1, config.TENTATIVAS + 1):
        try:
            resposta = requests.get(url, timeout=timeout)
            if 400 <= resposta.status_code < 500:  # token inválido, estação inexistente: repetir não resolve
                raise ErroINMET(f"HTTP {resposta.status_code}: {_sem_token(resposta.text[:100])}")
            resposta.raise_for_status()
            return resposta.json()
        except (requests.RequestException, ValueError) as erro:
            motivo = _sem_token(erro)
        if tentativa == config.TENTATIVAS:
            raise ErroINMET(f"{motivo} (depois de {tentativa} tentativas)")
        print(f"    ⚠️ {motivo} — tentando de novo em {espera:g} s ({tentativa}/{config.TENTATIVAS})")
        time.sleep(espera)
        espera *= 2


def formatar_nome_estacao(nome) -> str:
    """Padroniza o nome da estação (ex.: 'PONTA PORA' -> 'Ponta Pora', 'SAO GABRIEL DO OESTE' -> 'Sao Gabriel do Oeste')."""
    palavras = str(nome).strip().title().split()
    return " ".join(
        palavra.lower() if i > 0 and palavra.lower() in PALAVRAS_MINUSCULAS else palavra
        for i, palavra in enumerate(palavras)
    )


def listar_estacoes(uf: str = config.UF) -> pd.DataFrame:
    """Estações automáticas da UF, com coordenadas numéricas e a coluna 'Estação' (nome formatado)."""
    estacoes = pd.DataFrame(_consultar(config.URL_ESTACOES, config.TIMEOUT_ESTACOES))
    estacoes = estacoes[estacoes["SG_ESTADO"] == uf].copy()
    for coluna in ("VL_LATITUDE", "VL_LONGITUDE"):
        estacoes[coluna] = pd.to_numeric(estacoes[coluna], errors="coerce")
    estacoes["Estação"] = estacoes["DC_NOME"].map(formatar_nome_estacao)
    return estacoes


def baixar_dados_estacao(codigo: str, inicio: datetime, fim: datetime) -> pd.DataFrame | None:
    """Dados horários de uma estação com as leituras da janela (início, fim].

    A API trabalha com dias UTC inteiros (data final inclusiva); o recorte exato por
    horário é feito depois, em calculos.recortar. Retorna None quando a estação não tem
    leituras no período; levanta ErroINMET quando a consulta em si não dá certo.
    """
    dia_inicial = inicio.astimezone(config.FUSO_UTC).date().isoformat()
    dia_final = fim.astimezone(config.FUSO_UTC).date().isoformat()
    url = config.URL_DADOS.format(inicio=dia_inicial, fim=dia_final, codigo=codigo, token=config.TOKEN_INMET)

    registros = _consultar(url, config.TIMEOUT_DADOS)
    if not isinstance(registros, list) or not registros:
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
        raise ErroINMET(f"resposta em formato inesperado ({erro})") from erro

    return dados.dropna(subset=["dt_utc"])


def baixar_estacoes(inicio: datetime, fim: datetime, uf: str = config.UF) -> list[tuple[pd.Series, pd.DataFrame]]:
    """Lista as estações da UF e baixa os dados horários de cada uma para a janela (início, fim].

    Retorna (estação, dados) das estações que têm dados e, no fim, mostra quais ficaram de fora
    e por quê. Levanta ErroINMET se a lista de estações falhar.
    """
    estacoes = listar_estacoes(uf)
    print(f"✅ Encontradas {len(estacoes)} estações em {uf}")

    coletados, sem_dados, com_falha = [], [], []
    for _, estacao in estacoes.iterrows():
        nome = estacao["Estação"]
        print(f"🛰️ Lendo: {nome}...")
        try:
            dados = baixar_dados_estacao(estacao["CD_ESTACAO"], inicio, fim)
            time.sleep(config.PAUSA_ENTRE_REQUISICOES)
        except ErroINMET as erro:
            print(f"    ❌ Não foi possível ler: {erro}")
            com_falha.append(nome)
            continue
        if dados is None:
            print("    ⚠️ Sem leituras no período")
            sem_dados.append(nome)
            continue
        print(f"    📊 {len(dados)} registros")
        coletados.append((estacao, dados))

    _resumir_coleta(len(estacoes), len(coletados), sem_dados, com_falha)
    return coletados


def _resumir_coleta(total: int, com_dados: int, sem_dados: list[str], com_falha: list[str]) -> None:
    """Fecha a coleta dizendo quantas estações entraram e quais ficaram de fora, separadas por motivo."""
    print(f"\n📊 Estações com dados: {com_dados} de {total}")
    if sem_dados:
        print(f"   ⚠️ Sem leituras no período ({len(sem_dados)}): {', '.join(sem_dados)}")
    if com_falha:
        print(f"   ❌ Falha na consulta ({len(com_falha)}): {', '.join(com_falha)}")
        print("      Estas estações podem ter dados: vale repetir a consulta mais tarde.")
