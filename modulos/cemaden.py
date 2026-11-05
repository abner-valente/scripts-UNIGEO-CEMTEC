"""Acesso à PED, a Plataforma de Entrega de Dados do CEMADEN: os pluviômetros automáticos.

A rede do CEMADEN em MS tem uns 28 pluviômetros, quatro deles só em Campo Grande: mais pontos de
chuva para os mapas, ao lado das estações do INMET. A conta da PED (e-mail e senha no .env) dá um
token de 4 horas, e com ele uma consulta traz um dia inteiro do estado.

Conferido contra a API em 03/11/2026: as horas vêm em UTC, e o valor da chuva é a chuva desde a
leitura anterior. Sem chuva, a estação manda uma leitura por hora, com o zero escrito, num minuto que
é dela (a de Aquidauana, sempre aos 40); chovendo, de 10 em 10 minutos. Estação parada não manda
nada: hora sem leitura é hora sem dado, e não seca. A única estação hidrológica de MS manda também o
nível do rio, que fica de fora.
"""
import io
import time
from datetime import date

import pandas as pd
import requests

from . import config, inmet

# A leitura, e a estação como a própria leitura a descreve: a que saiu do cadastro da PED entra no
# banco por aí
COLUNAS_DA_CHUVA = ["codigo", "leitura_utc", "chuva", "nome", "municipio", "uf", "latitude", "longitude"]
COLUNAS_DO_CADASTRO = ["codigo", "id_estacao", "nome", "municipio", "codibge", "uf", "latitude", "longitude", "tipo",
                       "instalada_em", "ultima_parada", "ultima_remessa"]
CASAS_DA_CHUVA = 2   # o pluviômetro mede de 0,2 em 0,2 mm, e a PED manda 0.6000000000000001
CABECALHO_DA_REDE = "cod.estacao"   # a linha dos nomes das colunas no CSV, depois do aviso do UTC
# O dia sem leitura nenhuma no estado (antes da rede existir, em 2014) vem com HTTP 202 e só este
# aviso: "Info", e na linha de baixo "Nenhum resultado foi encontrado para essa requisição!"
SEM_RESULTADO = "Nenhum resultado"
# A citação que os termos de uso da PED pedem em todo produto feito com os dados dela, como está lá
CITACAO = "DATA FROM THE CEMADEN/MCTIC OBSERVATIONAL NETWORK"

# Uma sessão só, e o token dela, para o processo inteiro: o coletor é um só e consulta em fila
_ESTADO = {"sessao": None, "token": "", "obtido_em": 0.0, "ultima_consulta": float("-inf")}


class ErroCEMADEN(Exception):
    pass


def _sessao() -> requests.Session:
    if _ESTADO["sessao"] is None:
        _ESTADO["sessao"] = requests.Session()
    return _ESTADO["sessao"]


def _sem_credenciais(texto) -> str:
    texto = str(texto)
    for segredo in (config.CEMADEN_EMAIL, config.CEMADEN_SENHA, _ESTADO["token"]):
        if segredo:
            texto = texto.replace(segredo, "***")
    return texto


def _novo_token() -> str:
    if not config.CEMADEN_EMAIL or not config.CEMADEN_SENHA:
        raise ErroCEMADEN("a conta da PED não está configurada: preencha CEMADEN_EMAIL e CEMADEN_SENHA no .env")
    try:
        resposta = _sessao().post(config.URL_CEMADEN_TOKEN, timeout=config.TIMEOUT_CEMADEN,
                                  json={"email": config.CEMADEN_EMAIL, "password": config.CEMADEN_SENHA})
    except requests.RequestException as erro:
        raise ErroCEMADEN(f"a PED não respondeu ao login ({_sem_credenciais(erro)})") from None
    try:
        token = resposta.json().get("token") if resposta.status_code == 200 else None
    except (ValueError, AttributeError):
        token = None
    if not token:
        raise ErroCEMADEN(f"a PED recusou o login (HTTP {resposta.status_code}): "
                          f"{_sem_credenciais(resposta.text[:150])}")
    _ESTADO.update(token=token, obtido_em=time.monotonic())
    return token


def _token() -> str:
    # O de antes enquanto valer; numa carga longa, um novo antes das 4 horas
    if not _ESTADO["token"] or time.monotonic() - _ESTADO["obtido_em"] > config.VALIDADE_TOKEN_CEMADEN:
        return _novo_token()
    return _ESTADO["token"]


def _no_ritmo() -> None:
    espera = _ESTADO["ultima_consulta"] + config.INTERVALO_CEMADEN - time.monotonic()
    if espera > 0:
        time.sleep(espera)
    _ESTADO["ultima_consulta"] = time.monotonic()


def _consultar(rota: str, parametros: dict) -> requests.Response:
    # Como no INMET: a falha passageira é repetida, com a espera dobrando; o erro do pedido (4xx), não.
    # Token recusado é renovado uma vez; a recusa pelo limite espera o minuto virar
    espera, tentativa, recusas, renovado = config.PAUSA_ENTRE_TENTATIVAS, 0, 0, False
    while True:
        _no_ritmo()
        try:
            resposta = _sessao().get(f"{config.URL_CEMADEN}/{rota}", params=parametros, headers={"token": _token()},
                                     timeout=config.TIMEOUT_CEMADEN)
        except requests.RequestException as erro:
            motivo = _sem_credenciais(erro)
        else:
            if resposta.status_code in (401, 403) and not renovado:
                renovado, _ESTADO["token"] = True, ""
                continue
            if resposta.status_code == 429:
                recusas += 1
                if recusas > config.RECUSAS_NO_LIMITE_CEMADEN:
                    raise ErroCEMADEN(f"a PED recusou a consulta {recusas} vezes seguidas pelo limite de consultas")
                time.sleep(config.PAUSA_NO_LIMITE_CEMADEN)
                continue
            if 400 <= resposta.status_code < 500:
                raise ErroCEMADEN(f"HTTP {resposta.status_code}: {_sem_credenciais(resposta.text[:150])}")
            if resposta.status_code < 400:
                return resposta
            motivo = f"HTTP {resposta.status_code}"
        tentativa += 1
        if tentativa == config.TENTATIVAS:
            raise ErroCEMADEN(f"{motivo} (depois de {tentativa} tentativas)")
        print(f"    ⚠️ {motivo} — tentando de novo em {espera:g} s ({tentativa}/{config.TENTATIVAS})")
        time.sleep(espera)
        espera *= 2


def cadastro(uf: str = config.UF) -> pd.DataFrame:
    # As estações do CEMADEN no estado, as ativas e as paradas (veja esta_parada). As datas vêm sem
    # fuso declarado e ficam como vieram
    registros = _consultar("pcds-cadastro/dados-cadastrais", {"uf": uf, "formato": "JSON"}).json()
    if not isinstance(registros, list):
        raise ErroCEMADEN("o cadastro veio em formato inesperado")
    if not registros:
        return pd.DataFrame(columns=COLUNAS_DO_CADASTRO)
    tabela = pd.DataFrame(registros).rename(columns={
        "codestacao": "codigo", "cidade": "municipio", "tipoestacao_descricao": "tipo",
        "data_instalacao": "instalada_em", "dh_inicio_inativo": "ultima_parada", "dh_ultima_remessa": "ultima_remessa"})
    tabela["nome"] = tabela["nome"].str.strip()
    tabela["municipio"] = tabela["municipio"].map(inmet.formatar_nome_estacao)
    for coluna in ("instalada_em", "ultima_parada", "ultima_remessa"):
        # ISO: com o formato tirado da primeira linha, uma data sem os milissegundos viraria vazia
        tabela[coluna] = pd.to_datetime(tabela[coluna], errors="coerce", format="ISO8601")
    for coluna in ("latitude", "longitude"):
        tabela[coluna] = pd.to_numeric(tabela[coluna], errors="coerce")
    return tabela.dropna(subset=["latitude", "longitude"])[COLUNAS_DO_CADASTRO].reset_index(drop=True)


def esta_parada(cadastro: pd.DataFrame) -> pd.Series:
    # A PED preenche "dh_inicio_inativo" em toda estação: é o começo da última parada, e não quer
    # dizer que ela esteja parada agora. Parada é a que não mandou nada depois dele (conferido em
    # 03/11/2026: dá as 6 de 28 que não mandavam dado havia mais de um dia)
    return (cadastro["ultima_parada"] >= cadastro["ultima_remessa"]).fillna(False).astype(bool)


def chuva_do_dia(dia: date, uf: str = config.UF) -> pd.DataFrame:
    # As leituras de chuva de todas as estações do estado num dia UTC, da 00:00 às 23:59: a da 00:00
    # seguinte vem na consulta do dia seguinte. Uma leitura por linha, como a estação mandou
    texto = _consultar("pcds/dados_rede", {"uf": uf, "rede": config.REDE_CEMADEN, "inicio": f"{dia:%Y%m%d}0000",
                                            "fim": f"{dia:%Y%m%d}2359"}).content.decode("utf-8")
    return ler_chuva(texto)


def ler_chuva(texto: str) -> pd.DataFrame:
    # O CSV da PED começa com um aviso ("OBS.: Rede com horario UTC!"); a tabela vem da linha dos nomes
    linhas = texto.splitlines()
    inicio = next((indice for indice, linha in enumerate(linhas) if linha.startswith(CABECALHO_DA_REDE)), None)
    if inicio is None:
        if texto.strip() and not texto.lstrip().startswith("OBS") and SEM_RESULTADO not in texto:
            raise ErroCEMADEN("a chuva veio em formato inesperado")
        return pd.DataFrame(columns=COLUNAS_DA_CHUVA)
    try:
        tabela = pd.read_csv(io.StringIO("\n".join(linhas[inicio:])), sep=";",
                             dtype={coluna: str for coluna in ("cod.estacao", "nome", "municipio", "uf")})
        chuva = tabela[tabela["sensor"] == config.SENSOR_CHUVA_CEMADEN]
        leituras = pd.DataFrame({
            "codigo": chuva["cod.estacao"].str.strip(),
            "leitura_utc": pd.to_datetime(chuva["datahora"], errors="coerce", utc=True),
            "chuva": pd.to_numeric(chuva["valor"], errors="coerce").round(CASAS_DA_CHUVA),
            **{coluna: chuva[coluna].str.strip() for coluna in ("nome", "municipio", "uf")},
            **{coluna: pd.to_numeric(chuva[coluna], errors="coerce") for coluna in ("latitude", "longitude")},
        })
    except (KeyError, ValueError) as erro:
        raise ErroCEMADEN(f"a chuva veio em formato inesperado ({erro})") from None
    leituras = leituras.dropna(subset=["leitura_utc"]).drop_duplicates(["codigo", "leitura_utc"], keep="last")
    return leituras.sort_values(["codigo", "leitura_utc"]).reset_index(drop=True)
