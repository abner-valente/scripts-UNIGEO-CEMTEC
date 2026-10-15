"""Os mapas interativos do observado (pydeck): o que vai em cada camada, sem tela.

A conta é a dos mapas do boletim (o IDW e a regra 30-30-30 de modulos/); daqui sai só a forma de
mostrar: a superfície como imagem sobre o mapa, as estações como pontos com balão, os limites dos
municípios. Tudo local: nenhuma camada pede mapa de fundo à internet.
"""
import io
import json
from urllib.parse import quote

import numpy as np
import pandas as pd
from PIL import Image

from app import superficie
from modulos import config
from modulos.produtos import risco_fogo

OPACIDADE = 0.78
# Um estilo de mapa sem fonte nenhuma, só a cor de fundo, num endereço "data:" que o navegador lê sem
# ir à rede. Sem estilo, o Streamlit põe o mapa de fundo da Carto, buscado na internet; e ele só aceita
# o estilo como endereço, e não como dicionário (visto em 15/10/2026)
ESTILO_SEM_FUNDO = "data:application/json," + quote(json.dumps(
    {"version": 8, "sources": {},
     "layers": [{"id": "fundo", "type": "background", "paint": {"background-color": "#eef0f2"}}]}))
COLUNA_DO_MUNICIPIO = "Municipio"   # a dos shapefiles simplificados de shp/
# As três medidas da regra, na ordem de risco_fogo.condicoes_atendidas, com o limiar de cada uma
MEDIDAS_DO_RISCO = {
    "TEM_MAX": ("Temperatura máxima", "°C", config.LIMIAR_TEMP_MAX),
    "UMD_MIN": ("Umidade mínima", "%", config.LIMIAR_UMIDADE_MIN),
    "rajada_kmh": ("Rajada", "km/h", config.LIMIAR_RAJADA),
}


def rgba(cor: str, alfa: int = 255) -> list[int]:
    cor = cor.lstrip("#")
    return [int(cor[i:i + 2], 16) for i in (0, 2, 4)] + [alfa]


def _como_uri(imagem: np.ndarray) -> str:
    arquivo = io.BytesIO()
    Image.fromarray(imagem, mode="RGBA").save(arquivo, format="PNG")
    return superficie.como_uri(arquivo.getvalue())


def imagem_de_classes(grade: np.ndarray, dentro_fino: np.ndarray, cores: list[str] = config.CORES_RISCO,
                      opacidade: float = OPACIDADE) -> str:
    # A grade do cálculo (a dos produtos) levada à malha fina do mapa pelo vizinho mais próximo, sem
    # inventar nível entre as classes; o recorte pelo estado é o da malha fina, sem degraus na divisa
    grade = np.asarray(grade, dtype=float)
    linhas = np.rint(np.linspace(0, grade.shape[0] - 1, dentro_fino.shape[0])).astype(int)
    colunas = np.rint(np.linspace(0, grade.shape[1] - 1, dentro_fino.shape[1])).astype(int)
    fina = grade[np.ix_(linhas, colunas)]
    validos = dentro_fino & ~np.isnan(fina)
    paleta = np.array([rgba(cor, int(255 * opacidade)) for cor in cores], dtype=np.uint8)
    imagem = paleta[np.where(validos, fina, 0).astype(int).clip(0, len(cores) - 1)]
    imagem[~validos] = 0
    return _como_uri(np.flipud(imagem))   # a primeira linha da imagem é o norte; a grade começa no sul


def _numero(valor: float, casas: int = 1) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _detalhe_da_hora(linha: pd.Series) -> str:
    partes = []
    for coluna, atendida in zip(MEDIDAS_DO_RISCO, risco_fogo.COLUNAS_CONDICOES):
        nome, unidade, _ = MEDIDAS_DO_RISCO[coluna]
        marca = "✔" if linha[atendida] else "✘"
        partes.append(f"{marca} {nome}: {_numero(linha[coluna], 0 if coluna == 'UMD_MIN' else 1)} {unidade}")
    return "<br/>".join(partes)


def pontos_de_risco(estacoes: pd.DataFrame, coluna: str, legenda: str) -> pd.DataFrame:
    # Uma linha por estação: a posição, a cor do nível e o texto do balão. Com as condições (hora a
    # hora) o balão traz as três medidas; por dia e no período, só o nível, que junta horas diferentes
    tabela = estacoes.dropna(subset=[coluna]).copy()
    if tabela.empty:
        return pd.DataFrame(columns=["Estação", "Longitude", "Latitude", "cor", "nivel", "titulo", "detalhe"])
    niveis = tabela[coluna].astype(int)
    tabela["cor"] = [rgba(config.CORES_RISCO[nivel]) for nivel in niveis]
    tabela["nivel"] = niveis.astype(str)
    tabela["titulo"] = tabela["Estação"]
    com_condicoes = set(risco_fogo.COLUNAS_CONDICOES) <= set(tabela.columns)
    tabela["detalhe"] = [
        f"{legenda}: {config.ROTULOS_RISCO[nivel]}"
        + (f" ({nivel} de 3)<br/>{_detalhe_da_hora(linha)}" if com_condicoes else "")
        for nivel, (_, linha) in zip(niveis, tabela.iterrows())]
    return tabela[["Estação", "Longitude", "Latitude", "cor", "nivel", "titulo", "detalhe"]].reset_index(drop=True)


def municipios(base) -> dict:
    # Os limites, com o nome no balão (os mesmos campos do balão das estações)
    tabela = base.municipios[[COLUNA_DO_MUNICIPIO, "geometry"]].rename(columns={COLUNA_DO_MUNICIPIO: "titulo"})
    return tabela.assign(detalhe="Município").__geo_interface__


def serie_da_estacao(completas: list, nome: str, fuso) -> pd.DataFrame:
    # As três medidas da regra, hora a hora, no formato longo dos gráficos, com o limiar de cada uma
    for estacao, leituras in completas:
        if estacao["Estação"] != nome:
            continue
        atendidas = risco_fogo.condicoes_atendidas(*(leituras[coluna] for coluna in MEDIDAS_DO_RISCO))
        partes = []
        for (coluna, (rotulo, unidade, limiar)), atendida in zip(MEDIDAS_DO_RISCO.items(), atendidas):
            valores = leituras[coluna]
            partes.append(pd.DataFrame({"dt_local": leituras["dt_utc"].dt.tz_convert(fuso).dt.tz_localize(None),
                                        "Medida": f"{rotulo} ({unidade})", "valor": valores.to_numpy(),
                                        "limiar": limiar, "Condição": np.where(atendida, "atendida", "não")}))
        return pd.concat(partes, ignore_index=True)
    return pd.DataFrame(columns=["dt_local", "Medida", "valor", "limiar", "Condição"])
