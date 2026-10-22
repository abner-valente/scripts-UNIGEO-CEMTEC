"""Onde fica cada estação do INMET: a referência para quem não lida com elas todo dia.

Os mapas das variáveis mostram números em pontos, e para o meteorologista é óbvio de quem é cada
ponto; para o público, não (pedido de uma colega da equipe, 22/10/2026). Daqui sai a tabela que a
página de referência e o PNG numerado usam: a estação, o município, o código, a altitude e desde
quando ela opera.

Só cálculo, sem tela, para poder ser testado.
"""
import re
import unicodedata

import geopandas as gpd
import pandas as pd

COLUNA_DO_MUNICIPIO = "Municipio"   # a dos shapefiles simplificados de shp/
COLUNAS = ["Nº", "Estação", "Município", "Código", "Altitude (m)", "Em operação desde", "Latitude", "Longitude"]
# A projeção métrica do IBGE para o Brasil: é nela que se mede a distância ao município mais perto
PROJECAO_METRICA = "EPSG:5880"


def tabela_de_referencia(estacoes: pd.DataFrame, municipios: gpd.GeoDataFrame) -> pd.DataFrame:
    """Uma linha por estação, em ordem alfabética e numerada: é como se procura um nome numa lista.

    O município não vem na lista do INMET. Vale o do começo do nome da estação, quando ele é um
    município do estado; senão, o do ponto dela no shapefile, e a que cai fora de todos os polígonos
    (na divisa, onde o contorno simplificado corta um pouco) fica com o mais perto.
    """
    if estacoes.empty:
        return pd.DataFrame(columns=COLUNAS)
    pontos = gpd.GeoDataFrame(
        estacoes.reset_index(drop=True),
        geometry=gpd.points_from_xy(pd.to_numeric(estacoes["VL_LONGITUDE"]), pd.to_numeric(estacoes["VL_LATITUDE"])),
        crs="EPSG:4326")
    limites = municipios[[COLUNA_DO_MUNICIPIO, "geometry"]].to_crs("EPSG:4326")
    dentro = gpd.sjoin(pontos, limites, how="left", predicate="within")
    municipio = dentro[~dentro.index.duplicated()][COLUNA_DO_MUNICIPIO].copy()
    fora = municipio.isna()
    if fora.any():
        perto = gpd.sjoin_nearest(pontos[fora].to_crs(PROJECAO_METRICA), limites.to_crs(PROJECAO_METRICA), how="left")
        municipio[fora] = perto[~perto.index.duplicated()][COLUNA_DO_MUNICIPIO]
    # O nome da estação começa pelo município que ela representa ("Dourados", "Corumba (Faz. ...)",
    # "Amambai - Novo Horizonte"), e é esse que o público procura. Pelo ponto, umas poucas caem do
    # outro lado da divisa: a de Dourados em Itaporã, a de Miranda em Bodoquena (conferido em
    # 22/10/2026, também com a malha completa do IBGE). Quando o começo do nome é um município do
    # estado, vale ele; senão (Nhumirim, uma fazenda), vale o ponto
    pelo_nome = {sem_acento(nome): nome for nome in limites[COLUNA_DO_MUNICIPIO]}
    comeco = pontos["Estação"].map(lambda nome: sem_acento(re.split(r"\s+\(|\s+-\s+", str(nome))[0]))
    municipio = comeco.map(pelo_nome).fillna(municipio)

    # A altitude e o começo da operação vêm na lista do INMET; outra fonte pode não os ter
    vazia = pd.Series(index=estacoes.index, dtype=object)
    desde = pd.to_datetime(estacoes.get("DT_INICIO_OPERACAO", vazia), errors="coerce", utc=True)
    altitude = pd.to_numeric(estacoes.get("VL_ALTITUDE", vazia), errors="coerce")
    tabela = pd.DataFrame({
        "Estação": estacoes["Estação"].to_numpy(),
        "Município": municipio.to_numpy(),
        "Código": estacoes["CD_ESTACAO"].astype(str).to_numpy(),
        "Altitude (m)": altitude.round().astype("Int64").reset_index(drop=True),   # inteiro, com lacuna
        # A data vem com o fuso de Brasília ("2010-08-13T21:00:00-03:00"): o dia é o de lá
        "Em operação desde": desde.dt.tz_convert("America/Sao_Paulo").dt.strftime("%d/%m/%Y").to_numpy(),
        "Latitude": pd.to_numeric(estacoes["VL_LATITUDE"]).round(4).to_numpy(),
        "Longitude": pd.to_numeric(estacoes["VL_LONGITUDE"]).round(4).to_numpy(),
    })
    tabela = tabela.sort_values("Estação", key=lambda nomes: nomes.map(sem_acento)).reset_index(drop=True)
    tabela.insert(0, "Nº", range(1, len(tabela) + 1))
    return tabela[COLUNAS]


def sem_acento(texto) -> str:
    """O texto sem acento e em minúsculas: "Corumbá" e "CORUMBA" se encontram."""
    decomposto = unicodedata.normalize("NFKD", str(texto))
    return "".join(letra for letra in decomposto if not unicodedata.combining(letra)).casefold()


def procurar(tabela: pd.DataFrame, texto: str) -> pd.Series:
    """Quais estações batem com o que a pessoa digitou: no nome, no município ou no código."""
    procurado = sem_acento(texto.strip())
    if not procurado:
        return pd.Series(True, index=tabela.index)
    onde = tabela["Estação"].map(sem_acento) + " " + tabela["Município"].map(sem_acento) + " " + \
        tabela["Código"].map(sem_acento)
    return onde.str.contains(procurado, regex=False)
