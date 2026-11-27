"""Os arquivos do GLM do GOES-19 no bucket público da NOAA: listar, baixar e ler.

O GLM-L2-LCFA sai a cada 20 s, com os flashes do disco inteiro, em netCDF. O nome diz o começo, o fim
e a criação do arquivo, em ano, dia do ano, hora, minuto, segundo e décimo:

    GLM-L2-LCFA/2026/331/19/OR_GLM-L2-LCFA_G19_s20263311901400_e20263311902000_c20263311902015.nc

A pasta é a hora do começo. Todo arquivo começa num múltiplo de 20 s (conferido nos 285 mil do
controle em 27/11/2026), e a referência dele no controle é esse começo.

A leitura dá as colunas da glm_rt como o script do colega as gravava: em 27/11/2026, dois arquivos de
20/11 lidos aqui deram, flash por flash, os mesmos valores que estavam na tabela dele, e o mesmo
número de flashes a até 1.500 km. O netCDF4 já desfaz a escala e os inteiros sem sinal do produto.
"""
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import netCDF4
import numpy as np
import pandas as pd
import requests

from . import calculos, config

NOME = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)_e\d{14}_c(\d{14})\.nc$")
ESPACO_DO_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"
# O zero do product_time do arquivo, que é o começo dele; os horários dos flashes contam a partir dali
EPOCA = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
# As colunas da glm_rt que vêm do arquivo, com os mesmos nomes das variáveis
COLUNAS = ["flash_id", "flash_time_offset_of_first_event", "flash_time_offset_of_last_event",
           "flash_frame_time_offset_of_first_event", "flash_frame_time_offset_of_last_event",
           "flash_lat", "flash_lon", "flash_area", "flash_energy", "flash_quality_flag"]
INTEIRAS = ("flash_id", "flash_quality_flag")

_LOCAL = threading.local()


class ErroGLM(Exception):
    pass


def _sessao() -> requests.Session:
    # Uma sessão por thread: a busca de um dia baixa em paralelo
    if not hasattr(_LOCAL, "sessao"):
        _LOCAL.sessao = requests.Session()
    return _LOCAL.sessao


def prefixo(hora) -> str:
    hora = pd.Timestamp(hora).tz_convert("UTC")
    return f"{config.PRODUTO_GLM}/{hora:%Y}/{hora.dayofyear:03d}/{hora:%H}/"


def inicio(chave: str) -> pd.Timestamp:
    achado = NOME.search(chave)
    if not achado:
        raise ErroGLM(f"nome de arquivo do GLM inesperado: {chave!r}")
    ano, dia, hora, minuto, segundo, decimo = map(int, achado.groups()[:6])
    return (pd.Timestamp(year=ano, month=1, day=1, hour=hora, minute=minuto, second=segundo, tz="UTC")
            + pd.Timedelta(days=dia - 1, milliseconds=100 * decimo))


def um_por_inicio(chaves: list[str]) -> dict[pd.Timestamp, str]:
    # Se a NOAA publicar de novo o mesmo trecho, vale o arquivo criado por último
    escolhidas: dict[pd.Timestamp, str] = {}
    for chave in sorted((c for c in chaves if NOME.search(c)), key=lambda c: NOME.search(c).group(7)):
        escolhidas[inicio(chave)] = chave
    return dict(sorted(escolhidas.items()))


def _pedir(url: str, parametros: dict | None = None) -> requests.Response:
    espera = config.PAUSA_ENTRE_TENTATIVAS
    for tentativa in range(1, config.TENTATIVAS + 1):
        try:
            resposta = _sessao().get(url, params=parametros, timeout=config.TIMEOUT_GLM)
            if resposta.status_code == 200:
                return resposta
            motivo = f"HTTP {resposta.status_code}"
        except requests.RequestException as erro:
            motivo = type(erro).__name__
        if tentativa == config.TENTATIVAS:
            raise ErroGLM(f"o bucket da NOAA não respondeu ({motivo}): {url}")
        time.sleep(espera)
        espera *= 2
    raise AssertionError("inalcançável")


def listar(hora) -> list[str]:
    # Os arquivos que começam na hora (UTC), pela listagem do S3, de mil em mil
    chaves, continuar = [], None
    while True:
        parametros = {"list-type": "2", "prefix": prefixo(hora)}
        if continuar:
            parametros["continuation-token"] = continuar
        try:
            raiz = ET.fromstring(_pedir(config.URL_BUCKET_GLM, parametros).content)
        except ET.ParseError as erro:
            raise ErroGLM(f"a listagem do bucket veio ilegível: {erro}") from None
        chaves += [elemento.text for elemento in raiz.iter(f"{ESPACO_DO_S3}Key")]
        if raiz.findtext(f"{ESPACO_DO_S3}IsTruncated") != "true":
            return chaves
        continuar = raiz.findtext(f"{ESPACO_DO_S3}NextContinuationToken")


def baixar(chave: str) -> bytes:
    return _pedir(f"{config.URL_BUCKET_GLM}/{chave}").content


def ler(conteudo: bytes) -> tuple[datetime, pd.DataFrame]:
    # O começo do arquivo e os flashes, com o horário de cada um: o começo mais o tempo do primeiro
    # evento. A soma é a do datetime, que arredonda ao microssegundo como a tabela do colega
    try:
        with netCDF4.Dataset("glm", memory=conteudo) as dados:
            comeco = EPOCA + timedelta(seconds=float(dados.variables["product_time"][:]))
            flashes = pd.DataFrame({coluna: np.ma.filled(dados.variables[coluna][:].astype("float64"), np.nan)
                                    for coluna in COLUNAS})
    except (OSError, KeyError, ValueError) as erro:
        raise ErroGLM(f"arquivo do GLM ilegível: {erro}") from None
    flashes = flashes.astype({coluna: "Int64" for coluna in INTEIRAS})
    flashes.insert(1, "time_utc", pd.to_datetime([comeco + timedelta(seconds=segundos) if np.isfinite(segundos)
                                                  else pd.NaT for segundos in flashes[COLUNAS[1]]], utc=True))
    return comeco, flashes


def no_circulo(flashes: pd.DataFrame, km: float = config.RAIO_DA_TABELA_GLM_KM) -> pd.DataFrame:
    # Os flashes a até `km` de Campo Grande, pela mesma conta do ST_DistanceSphere do PostGIS
    distancia = calculos.distancia_km(flashes["flash_lon"], flashes["flash_lat"], *config.CENTRO_DOS_RAIOS)
    return flashes[distancia <= km].reset_index(drop=True)
