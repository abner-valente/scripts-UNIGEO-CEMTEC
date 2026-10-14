"""Acesso à API do INMET (apitempo.inmet.gov.br)."""
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from . import calculos, config

# Uma conexão por thread, reaproveitada entre as consultas. Abrir conexão nova a cada estação
# custava 0,34 s das 0,83 s de cada uma — quase metade da espera era aperto de mão.
_CONEXAO = threading.local()
# Teto de consultas simultâneas do processo inteiro, e não de cada lote: ver config.
_VAGAS = threading.Semaphore(config.DOWNLOADS_SIMULTANEOS)


def _sessao() -> requests.Session:
    return _CONEXAO.__dict__.setdefault("sessao", requests.Session())

# A API devolve tudo como texto. Estas são as colunas que continuam texto; todas as outras
# (temperatura, umidade, chuva, vento, pressão, radiação, ponto de orvalho...) viram número.
COLUNAS_TEXTO = ["DC_NOME", "UF", "CD_ESTACAO", "DT_MEDICAO", "HR_MEDICAO"]
PALAVRAS_MINUSCULAS = {"da", "das", "de", "do", "dos", "e"}
# A API marca cada estação como "Operante" ou "Pane"; só a primeira tem o que responder.
SITUACAO_OPERANTE = "operante"


class ErroINMET(Exception):
    pass


def _sem_token(texto) -> str:
    texto = str(texto)
    return texto.replace(config.TOKEN_INMET, "***") if config.TOKEN_INMET else texto


def _recusada_por_limite(resposta: requests.Response) -> bool:
    texto = resposta.text
    return len(texto) < 200 and config.AVISO_DE_LIMITE in texto


def _consultar(url: str, timeout: int) -> list | dict:
    espera = config.PAUSA_ENTRE_TENTATIVAS
    espera_no_limite = config.PAUSA_NO_LIMITE
    tentativa = recusas = 0
    while True:
        try:
            # A espera entre tentativas fica fora da vaga: segurá-la enquanto se dorme tiraria
            # do ar uma das oito faixas justamente quando a API está ruim.
            with _VAGAS:
                resposta = _sessao().get(url, timeout=timeout)
            if 400 <= resposta.status_code < 500:  # token inválido, estação inexistente: repetir não resolve
                raise ErroINMET(f"HTTP {resposta.status_code}: {_sem_token(resposta.text[:100])}")
            resposta.raise_for_status()
            if not _recusada_por_limite(resposta):
                return resposta.json()
        except (requests.RequestException, ValueError) as erro:
            motivo = _sem_token(erro)
        else:
            recusas += 1
            if recusas > config.RECUSAS_NO_LIMITE:
                raise ErroINMET(f"o INMET recusou a consulta {recusas} vezes seguidas pelo limite de consultas")
            time.sleep(espera_no_limite)
            espera_no_limite = min(espera_no_limite * 2, config.PAUSA_MAXIMA_NO_LIMITE)
            continue
        tentativa += 1
        if tentativa == config.TENTATIVAS:
            raise ErroINMET(f"{motivo} (depois de {tentativa} tentativas)")
        print(f"    ⚠️ {motivo} — tentando de novo em {espera:g} s ({tentativa}/{config.TENTATIVAS})")
        time.sleep(espera)
        espera *= 2


def formatar_nome_estacao(nome) -> str:
    palavras = str(nome).strip().title().split()
    return " ".join(
        palavra.lower() if i > 0 and palavra.lower() in PALAVRAS_MINUSCULAS else palavra
        for i, palavra in enumerate(palavras)
    )


def lista_do_inmet() -> pd.DataFrame:
    return pd.DataFrame(_consultar(config.URL_ESTACOES, config.TIMEOUT_ESTACOES))


def _todas_as_estacoes() -> pd.DataFrame:
    estacoes = lista_do_inmet()
    if "CD_SITUACAO" in estacoes:
        estacoes = estacoes[estacoes["CD_SITUACAO"].str.strip().str.casefold() == SITUACAO_OPERANTE]
    estacoes = estacoes.copy()
    for coluna in ("VL_LATITUDE", "VL_LONGITUDE"):
        estacoes[coluna] = pd.to_numeric(estacoes[coluna], errors="coerce")
    estacoes["Estação"] = estacoes["DC_NOME"].map(formatar_nome_estacao)
    return estacoes.dropna(subset=["VL_LATITUDE", "VL_LONGITUDE"])


def _no_recorte(estacoes: pd.DataFrame, recorte: config.Recorte, margem: float) -> pd.DataFrame:
    oeste, leste, sul, norte = recorte.limites
    dentro = (estacoes["VL_LONGITUDE"].between(oeste - margem, leste + margem)
              & estacoes["VL_LATITUDE"].between(sul - margem, norte + margem))
    return estacoes[dentro].copy()


def estacoes_do_recorte(recorte: config.Recorte = config.RECORTE,
                        margem: float = config.MARGEM_RECORTE) -> pd.DataFrame:
    return _no_recorte(_todas_as_estacoes(), recorte, margem)


def listar_estacoes(uf: str = config.UF) -> pd.DataFrame:
    estacoes = _todas_as_estacoes()
    estacoes = estacoes[estacoes["SG_ESTADO"] == uf].copy()
    return estacoes


def baixar_dados_estacao(codigo: str, inicio: datetime, fim: datetime,
                         fuso: ZoneInfo = config.FUSO_MS) -> pd.DataFrame | None:
    dia_inicial = inicio.astimezone(config.FUSO_UTC).date().isoformat()
    dia_final = fim.astimezone(config.FUSO_UTC).date().isoformat()
    url = config.URL_DADOS.format(inicio=dia_inicial, fim=dia_final, codigo=codigo, token=config.TOKEN_INMET)

    registros = _consultar(url, config.TIMEOUT_DADOS)
    if not isinstance(registros, list) or not registros:
        return None
    return _tabelar(registros, fuso)


def baixar_hora(momento: datetime) -> pd.DataFrame:
    momento = momento.astimezone(config.FUSO_UTC)
    url = config.URL_DADOS_DA_HORA.format(data=f"{momento:%Y-%m-%d}", hora=f"{momento:%H%M}",
                                          token=config.TOKEN_INMET)
    registros = _consultar(url, config.TIMEOUT_DADOS)
    if not isinstance(registros, list) or not registros:
        return pd.DataFrame(columns=["CD_ESTACAO", "dt_utc"])
    return _tabelar(registros)


def _tabelar(registros: list[dict], fuso: ZoneInfo | None = None) -> pd.DataFrame:
    try:
        dados = pd.DataFrame(registros)
        for coluna in dados.columns.difference(COLUNAS_TEXTO):
            dados[coluna] = pd.to_numeric(dados[coluna].astype(str).str.replace(",", "."), errors="coerce")

        # HR_MEDICAO vem como "HHMM" (ex.: "1300"), em UTC
        horas = dados["HR_MEDICAO"].map(lambda hora: f"{str(hora).zfill(4)[:2]}:00")
        dados["dt_utc"] = pd.to_datetime(dados["DT_MEDICAO"] + " " + horas, errors="coerce", utc=True)
        if fuso is not None:
            dados["dt_local"] = dados["dt_utc"].dt.tz_convert(fuso)
    except (KeyError, TypeError, ValueError) as erro:
        raise ErroINMET(f"resposta em formato inesperado ({erro})") from erro

    return dados.dropna(subset=["dt_utc"])


def baixar_estacoes(inicio: datetime, fim: datetime, uf: str = config.UF,
                    fuso: ZoneInfo = config.FUSO_MS) -> list[tuple[pd.Series, pd.DataFrame]]:
    estacoes = listar_estacoes(uf)
    print(f"✅ Encontradas {len(estacoes)} estações em {uf}")
    coletados, sem_dados, com_falha = baixar_lista(estacoes, inicio, fim, fuso)
    resumir_coleta(len(estacoes), len(coletados), sem_dados, com_falha)
    return coletados


def baixar_lista(estacoes: pd.DataFrame, inicio: datetime, fim: datetime,
                 fuso: ZoneInfo = config.FUSO_MS, detalhar: bool = True) -> tuple[list, list, list]:
    coletados, sem_dados, com_falha = [], [], []
    for _, estacao in estacoes.iterrows():
        nome = estacao["Estação"]
        if detalhar:
            print(f"🛰️ Lendo: {nome}...")
        try:
            dados = baixar_dados_estacao(estacao["CD_ESTACAO"], inicio, fim, fuso=fuso)
            time.sleep(config.PAUSA_ENTRE_REQUISICOES)
        except ErroINMET as erro:
            if detalhar:
                print(f"    ❌ Não foi possível ler: {erro}")
            com_falha.append(nome)
            continue
        if dados is None:
            if detalhar:
                print("    ⚠️ Sem leituras no período")
            sem_dados.append(nome)
            continue
        if detalhar:
            print(f"    📊 {len(dados)} registros")
        coletados.append((estacao, dados))
    return coletados, sem_dados, com_falha


def estacoes_de_apoio(recorte: config.Recorte, lon_celulas, lat_celulas,
                      estacoes: pd.DataFrame | None = None) -> pd.DataFrame:
    # `estacoes` é a lista de onde escolher, quando ela não vem do INMET (a fonte "banco")
    vizinhanca = (estacoes_do_recorte(recorte) if estacoes is None
                  else _no_recorte(estacoes, recorte, config.MARGEM_RECORTE))
    fora = vizinhanca[vizinhanca["SG_ESTADO"] != recorte.uf]
    dele = vizinhanca[vizinhanca["SG_ESTADO"] == recorte.uf]
    entram = calculos.apoio_que_entra(fora["VL_LONGITUDE"].values, fora["VL_LATITUDE"].values,
                                      dele["VL_LONGITUDE"].values, dele["VL_LATITUDE"].values,
                                      lon_celulas, lat_celulas, config.VIZINHOS_NA_PODA)
    return fora[entram].sort_values("Estação").reset_index(drop=True)


def baixar_apoio(recorte: config.Recorte, lon_celulas, lat_celulas, inicio: datetime, fim: datetime,
                 fuso: ZoneInfo = config.FUSO_MS) -> list[tuple[pd.Series, pd.DataFrame]]:
    try:
        estacoes = estacoes_de_apoio(recorte, lon_celulas, lat_celulas)
    except ErroINMET as erro:
        print(f"⚠️ Sem as estações vizinhas ({erro}); a borda sai só com as do estado.")
        return []
    if estacoes.empty:
        return []
    estados = ", ".join(estacoes["SG_ESTADO"].value_counts().index)
    print(f"🧭 Lendo {len(estacoes)} estações vizinhas para a borda ({estados})...")
    coletados, sem_dados, com_falha = baixar_lista(estacoes, inicio, fim, fuso, detalhar=False)
    print(f"   {len(coletados)} com dados"
          + (f", {len(sem_dados) + len(com_falha)} sem" if sem_dados or com_falha else ""))
    return coletados


def resumir_coleta(total: int, com_dados: int, sem_dados: list[str], com_falha: list[str]) -> None:
    print(f"\n📊 Estações com dados: {com_dados} de {total}")
    if sem_dados:
        print(f"   ⚠️ Sem leituras no período ({len(sem_dados)}): {', '.join(sem_dados)}")
    if com_falha:
        print(f"   ❌ Falha na consulta ({len(com_falha)}): {', '.join(com_falha)}")
        print("      Estas estações podem ter dados: vale repetir a consulta mais tarde.")
