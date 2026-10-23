"""Os mapas interativos do observado (pydeck): o que vai em cada camada, sem tela.

A conta é a dos mapas do boletim (o IDW e a regra 30-30-30 de modulos/); daqui sai só a forma de
mostrar: a superfície como imagem sobre o mapa, as estações como pontos com balão, os limites dos
municípios, e uma grade invisível que dá o valor interpolado em qualquer ponto do estado. Tudo
local: nenhuma camada pede mapa de fundo à internet.
"""
import io
import json
from dataclasses import dataclass
from urllib.parse import quote

import numpy as np
import pandas as pd
import shapely
from PIL import Image

from app import superficie, variaveis
from modulos import calculos, config
from modulos.produtos import risco_fogo

OPACIDADE = 0.78
COR_DA_ESTACAO = [25, 25, 25, 215]   # nas variáveis contínuas o ponto é escuro: a cor é da superfície
# O quadrado da célula clicada, a 50% (a pedido da equipe). No risco, ciano: vermelho se confundiria
# com o risco alto
COR_DO_PONTO_ESCOLHIDO = [229, 57, 53]
COR_DO_PONTO_ESCOLHIDO_NO_RISCO = [0, 229, 255]
# O que toda camada de estações leva: posição, cor, o texto sobre o ponto e o balão
COLUNAS_DOS_PONTOS = ["Estação", "Longitude", "Latitude", "cor", "rotulo", "titulo", "detalhe"]
# Um estilo de mapa sem fonte nenhuma, só a cor de fundo, num endereço "data:" que o navegador lê sem
# ir à rede. Sem estilo, o Streamlit põe o mapa de fundo da Carto, buscado na internet; e ele só aceita
# o estilo como endereço, e não como dicionário (visto em 15/10/2026)
ESTILO_SEM_FUNDO = "data:application/json," + quote(json.dumps(
    {"version": 8, "sources": {},
     "layers": [{"id": "fundo", "type": "background", "paint": {"background-color": "#eef0f2"}}]}))
COLUNA_DO_MUNICIPIO = "Municipio"   # a dos shapefiles simplificados de shp/
# O Streamlit escapa o HTML dos campos do balão (visto na 1.64): um "<br/>" vindo do dado aparece
# escrito. A quebra vai como "\n", e o modelo do balão, que não é escapado, a respeita com pre-line
QUEBRA = "\n"
BALAO = {"html": "<b>{titulo}</b><div style='white-space: pre-line'>{detalhe}</div>"}
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


def _numero_perto_do_limiar(valor: float, casas: int, limiar: float) -> str:
    # O valor interpolado arredondado em cima do limiar não diria de que lado ele está: 29,96 °C
    # sairia "30,0" ao lado de um ✘. Nesse caso vão mais casas, até ficar claro
    while casas < 3 and round(valor, casas) == limiar != valor:
        casas += 1
    return _numero(valor, casas)


def _detalhe_da_hora(linha: pd.Series) -> str:
    partes = []
    for coluna, atendida in zip(MEDIDAS_DO_RISCO, risco_fogo.COLUNAS_CONDICOES):
        nome, unidade, _ = MEDIDAS_DO_RISCO[coluna]
        marca = "✔" if linha[atendida] else "✘"
        partes.append(f"{marca} {nome}: {_numero(linha[coluna], 0 if coluna == 'UMD_MIN' else 1)} {unidade}")
    return QUEBRA.join(partes)


def pontos_de_risco(estacoes: pd.DataFrame, coluna: str, legenda: str) -> pd.DataFrame:
    # Uma linha por estação: a posição, a cor do nível e o texto do balão. Com as condições (hora a
    # hora) o balão traz as três medidas; por dia e no período, só o nível, que junta horas diferentes
    tabela = estacoes.dropna(subset=[coluna]).copy()
    if tabela.empty:
        return pd.DataFrame(columns=COLUNAS_DOS_PONTOS)
    niveis = tabela[coluna].astype(int)
    tabela["cor"] = [rgba(config.CORES_RISCO[nivel]) for nivel in niveis]
    tabela["rotulo"] = niveis.astype(str)
    tabela["titulo"] = tabela["Estação"]
    com_condicoes = set(risco_fogo.COLUNAS_CONDICOES) <= set(tabela.columns)
    tabela["detalhe"] = [
        f"{legenda}: {config.ROTULOS_RISCO[nivel]}"
        + (f" ({nivel} de 3){QUEBRA}{_detalhe_da_hora(linha)}" if com_condicoes else "")
        for nivel, (_, linha) in zip(niveis, tabela.iterrows())]
    return tabela[COLUNAS_DOS_PONTOS].reset_index(drop=True)


def pontos_de_valores(pontos: pd.DataFrame, coluna: str, legenda: str, unidade: str, casas: int) -> pd.DataFrame:
    # As variáveis contínuas (a chuva): o ponto escuro, o valor escrito sobre ele e no balão
    tabela = pontos.dropna(subset=[coluna]).copy()
    if tabela.empty:
        return pd.DataFrame(columns=COLUNAS_DOS_PONTOS)
    texto = tabela[coluna].map(lambda valor: _numero(valor, casas))
    tabela["cor"] = [COR_DA_ESTACAO] * len(tabela)
    tabela["rotulo"] = texto
    tabela["titulo"] = tabela["Estação"]
    tabela["detalhe"] = f"{legenda}: " + texto + f" {unidade}"
    return tabela[COLUNAS_DOS_PONTOS].reset_index(drop=True)


def municipios(base) -> dict:
    # Os limites, com o nome no balão (os mesmos campos do balão das estações)
    tabela = base.municipios[[COLUNA_DO_MUNICIPIO, "geometry"]].rename(columns={COLUNA_DO_MUNICIPIO: "titulo"})
    return tabela.assign(detalhe="Município").__geo_interface__


RUMOS = ["N", "NE", "L", "SE", "S", "SO", "O", "NO"]


def rumo(graus: float) -> str:
    return RUMOS[int((graus % 360 + 22.5) // 45) % 8]


def com_direcao(pontos: pd.DataFrame, direcoes: pd.Series) -> pd.DataFrame:
    # O vento leva a direção: no balão, de onde ele sopra; na seta, para onde vai. A seta é o
    # caractere "↑" (o norte), e o TextLayer gira no sentido anti-horário
    tabela = pontos.copy()
    graus = tabela["Estação"].map(direcoes)
    tabela["angulo"] = -((graus + 180) % 360)
    tabela["detalhe"] = [detalhe + (f"{QUEBRA}de {direcao:.0f}° ({rumo(direcao)})" if pd.notna(direcao) else "")
                         for detalhe, direcao in zip(tabela["detalhe"], graus)]
    return tabela


# =====================================================
# O VALOR EM QUALQUER PONTO DO ESTADO
# =====================================================
# A grade que responde ao mouse é a dos produtos (cerca de 8 km em MS). A superfície desenhada é
# mais fina, mas uma célula por pixel pesaria demais no navegador: o ponto lido é o centro da
# célula, e o número é o IDW das estações ali, o mesmo que pinta o mapa
COLUNAS_DA_CAMADA = ["celula", "Longitude", "Latitude", "titulo", "detalhe"]


@dataclass(frozen=True)
class GradeDoPonto:
    lon: np.ndarray
    lat: np.ndarray
    dentro: np.ndarray
    municipio: np.ndarray
    meio_passo: tuple[float, float]   # meia célula, em graus de longitude e de latitude

    @property
    def centro(self) -> tuple[float, float]:
        # O mesmo da superfície (as duas grades cobrem o mesmo recorte): é ele que fixa a projeção
        # do IDW, e com ele o número do ponto bate com a cor do mapa
        return float(np.mean(self.lon)), float(np.mean(self.lat))


def grade_do_ponto(base, resolucao: int = config.RESOLUCAO_GRADE) -> GradeDoPonto:
    recorte = base.recorte
    lon, lat = calculos.criar_grade(recorte.limites, resolucao)
    municipio = np.full(lon.shape, "", dtype=object)
    for nome, forma in zip(base.municipios[COLUNA_DO_MUNICIPIO], base.municipios.geometry):
        municipio[shapely.contains_xy(forma, lon, lat)] = nome
    oeste, leste, sul, norte = recorte.limites
    meio_passo = ((leste - oeste) / (resolucao - 1) / 2, (norte - sul) / (resolucao - 1) / 2)
    return GradeDoPonto(lon, lat, shapely.contains_xy(base.uf.geometry.union_all(), lon, lat),
                        municipio, meio_passo)


def direcao_interpolada(entrada: pd.DataFrame, coluna: str, direcoes: pd.Series, lon_grade, lat_grade) -> np.ndarray:
    # O rumo não se interpola como número: entre 350° e 10° a média daria 180°, o rumo oposto. Vão
    # para o IDW as componentes leste e norte do vento de cada estação, e o rumo sai delas
    com_rumo = entrada.assign(_graus=entrada["Estação"].map(direcoes)).dropna(subset=["_graus", coluna])
    if com_rumo.empty:
        return np.full(np.shape(lon_grade), np.nan)
    radianos = np.deg2rad(com_rumo["_graus"].to_numpy(dtype=float))
    leste, norte = (calculos.interpolar_idw(com_rumo["Longitude"], com_rumo["Latitude"],
                                            com_rumo[coluna].to_numpy(dtype=float) * componente(radianos),
                                            lon_grade, lat_grade, config.IDW_VIZINHOS, config.IDW_POTENCIA)
                    for componente in (np.sin, np.cos))
    graus = np.degrees(np.arctan2(leste, norte)) % 360
    return np.where(np.hypot(leste, norte) < 1e-9, np.nan, graus)


def texto_do_ponto(nome: str, valor: float, unidade: str, casas: int, graus: float,
                   mais_proxima: str, km: float) -> str:
    texto = f"{nome}: {_numero(valor, casas)} {unidade} (interpolado)"
    if pd.notna(graus):
        texto += f"{QUEBRA}de {graus:.0f}° ({rumo(graus)})"
    return texto + f"{QUEBRA}Estação mais próxima: {mais_proxima}, a {km:.0f} km"


def celulas_do_ponto(grade: GradeDoPonto, entrada: pd.DataFrame, coluna: str, unidade: str, casas: int,
                     direcoes: pd.Series | None = None) -> pd.DataFrame:
    # Uma linha por célula dentro do estado, com o valor e o texto do balão. Ficam também os números
    # soltos, para o painel do clique: a camada leva ao navegador só COLUNAS_DA_CAMADA
    entrada = entrada.dropna(subset=[coluna])
    if entrada.empty:
        return pd.DataFrame(columns=COLUNAS_DA_CAMADA + ["valor", "graus", "mais_proxima", "km"])
    lons, lats = entrada["Longitude"].to_numpy(dtype=float), entrada["Latitude"].to_numpy(dtype=float)
    dentro = np.flatnonzero(grade.dentro)

    def no_estado(matriz):
        return np.ravel(matriz)[dentro]

    valores = no_estado(calculos.interpolar_idw(lons, lats, entrada[coluna].to_numpy(dtype=float),
                                                grade.lon, grade.lat, config.IDW_VIZINHOS, config.IDW_POTENCIA))
    indices, distancias = (no_estado(matriz) for matriz in calculos.mais_proxima(lons, lats, grade.lon, grade.lat))
    graus = (no_estado(direcao_interpolada(entrada, coluna, direcoes, grade.lon, grade.lat))
             if direcoes is not None and not direcoes.empty else np.full(len(dentro), np.nan))
    nomes = entrada["Estação"].to_numpy()[indices]
    return pd.DataFrame({
        "celula": dentro,
        "Longitude": np.round(no_estado(grade.lon), 4),
        "Latitude": np.round(no_estado(grade.lat), 4),
        "titulo": [municipio or "Ponto no mapa" for municipio in no_estado(grade.municipio)],
        "detalhe": [texto_do_ponto(coluna, valor, unidade, casas, grau, nome, km)
                    for valor, grau, nome, km in zip(valores, graus, nomes, distancias)],
        "valor": valores, "graus": graus, "mais_proxima": nomes, "km": distancias,
    })


# O relevo (o mapa em 3D): cada célula da grade do ponto vira um bloco com a altura do valor,
# pintado como a superfície. O topo da escala sobe 100 km, um oitavo da largura de MS: é a altura
# do protótipo que a equipe escolheu (a chuva do mês, com 300 mm no topo)
ALTURA_DO_TETO = 100_000
OPACIDADE_DO_RELEVO = 0.92
METROS_POR_GRAU = 111_319.5   # no equador; o grau de longitude encolhe com o cosseno da latitude
COLUNAS_DO_RELEVO = ["Longitude", "Latitude", "cor", "altura"]


def metros_por_unidade(niveis, medidas: pd.Series) -> float:
    # Com a escala fixa, o topo dela: 20 mm têm a mesma altura em qualquer instante, como têm a
    # mesma cor. Ajustada ao dado, o maior valor medido
    teto = float(np.max(niveis)) if niveis is not None else float(medidas.max())
    return ALTURA_DO_TETO / teto if teto > 0 else 0.0


def lado_do_bloco(grade: GradeDoPonto) -> float:
    # O bloco é um quadrado em metros, e a célula é em graus: o grau de longitude encolhe para o
    # sul. O lado cobre a célula no norte do recorte, onde ela é mais larga, para não abrir fresta
    # entre os blocos; no sul eles se sobrepõem um pouco, sem que se veja
    dx, dy = grade.meio_passo
    norte = float(np.max(grade.lat))
    return 2 * max(dx * np.cos(np.radians(norte)), dy) * METROS_POR_GRAU


def relevo(celulas: pd.DataFrame, paleta: str, niveis, medidas: pd.Series, por_unidade: float) -> pd.DataFrame:
    # Uma linha por célula, na ordem de `celulas`: a cor da superfície e a altura. Abaixo da
    # primeira classe a célula não se pinta, como no mapa plano, e fica no chão
    valores = celulas["valor"].to_numpy(dtype=float)
    pintura, vazio = superficie.cores(valores, paleta, niveis, medidas, OPACIDADE_DO_RELEVO)
    pintada = ~np.isnan(valores) if vazio is None else ~np.isnan(valores) & ~vazio
    return pd.DataFrame({
        "Longitude": celulas["Longitude"].to_numpy(), "Latitude": celulas["Latitude"].to_numpy(),
        "cor": pintura.tolist(), "altura": np.where(pintada, np.nan_to_num(valores) * por_unidade, 0.0),
        "pintada": pintada})


def metros_por_nivel(cores: list[str] = config.CORES_RISCO) -> float:
    # Nas classes (o risco), um degrau por nível, com o mais alto no topo
    return ALTURA_DO_TETO / (len(cores) - 1)


def relevo_de_classes(celulas: pd.DataFrame, cores: list[str] = config.CORES_RISCO) -> pd.DataFrame:
    # O mesmo formato de `relevo`, com a altura e a cor do nível de cada célula. O nível 0 fica no
    # chão, mas pintado, como no mapa plano
    nivel = celulas["nivel"].to_numpy(dtype=int)
    paleta = [rgba(cor, int(255 * OPACIDADE_DO_RELEVO)) for cor in cores]
    return pd.DataFrame({
        "Longitude": celulas["Longitude"].to_numpy(), "Latitude": celulas["Latitude"].to_numpy(),
        "cor": [paleta[valor] for valor in nivel], "altura": nivel * metros_por_nivel(cores),
        "pintada": np.ones(len(nivel), dtype=bool)})


def celulas_do_risco(grade: GradeDoPonto, niveis: np.ndarray, legenda: str, estacoes: pd.DataFrame,
                     com_medidas: bool, horas_alto: np.ndarray | None = None) -> pd.DataFrame:
    # O nível de cada célula é o que pinta o mapa. Hora a hora, o balão traz as três medidas
    # interpoladas que o deram: as mesmas estações e o mesmo IDW de risco_fogo.grade_da_hora, então
    # os ✔ somam o nível. Por dia e no período o nível junta horas diferentes, e no lugar das medidas
    # vão as horas em risco alto
    dentro = np.flatnonzero(grade.dentro)
    if estacoes.empty or len(dentro) == 0:
        return pd.DataFrame(columns=COLUNAS_DA_CAMADA + ["nivel", "mais_proxima", "km"])
    lons, lats = estacoes["Longitude"].to_numpy(dtype=float), estacoes["Latitude"].to_numpy(dtype=float)

    def no_estado(matriz):
        return np.ravel(matriz)[dentro]

    nivel = no_estado(niveis).astype(int)
    linhas = [[f"{legenda}: {config.ROTULOS_RISCO[valor]} ({valor} de 3, interpolado)"] for valor in nivel]
    if com_medidas:
        campos = [no_estado(calculos.interpolar_idw(lons, lats, estacoes[coluna].to_numpy(dtype=float), grade.lon,
                                                    grade.lat, config.IDW_VIZINHOS, config.IDW_POTENCIA))
                  for coluna in MEDIDAS_DO_RISCO]
        for (coluna, (nome, unidade, limiar)), campo, atendida in zip(MEDIDAS_DO_RISCO.items(), campos,
                                                                      risco_fogo.condicoes_atendidas(*campos)):
            casas = 0 if coluna == "UMD_MIN" else 1
            for linha, valor, valeu in zip(linhas, campo, atendida):
                linha.append(f"{'✔' if valeu else '✘'} {nome}: {_numero_perto_do_limiar(valor, casas, limiar)} "
                             f"{unidade}")
    if horas_alto is not None:
        for linha, horas in zip(linhas, no_estado(horas_alto)):
            linha.append(f"Horas em risco alto: {int(horas)}")
    indices, distancias = (no_estado(matriz) for matriz in calculos.mais_proxima(lons, lats, grade.lon, grade.lat))
    nomes = estacoes["Estação"].to_numpy()[indices]
    for linha, nome, km in zip(linhas, nomes, distancias):
        linha.append(f"Estação mais próxima: {nome}, a {km:.0f} km")
    return pd.DataFrame({
        "celula": dentro,
        "Longitude": np.round(no_estado(grade.lon), 4),
        "Latitude": np.round(no_estado(grade.lat), 4),
        "titulo": [municipio or "Ponto no mapa" for municipio in no_estado(grade.municipio)],
        "detalhe": [QUEBRA.join(linha) for linha in linhas],
        "nivel": nivel, "mais_proxima": nomes, "km": distancias,
    })


def serie_do_ponto(produto, leituras: pd.DataFrame, coordenadas: pd.DataFrame, lon: float, lat: float,
                   modo: str, centro: tuple[float, float]) -> pd.DataFrame:
    # A variável do mapa no ponto, hora a hora ou dia a dia: a cada instante, o IDW das estações que
    # mediram. Com menos de MIN_ESTACOES_INTERPOLACAO, o instante fica sem valor, como no mapa
    vazia = pd.DataFrame(columns=["quando", "valor"])
    largo = variaveis.no_tempo(produto, leituras, modo)
    posicoes = coordenadas.drop_duplicates("Estação").set_index("Estação")[["Longitude", "Latitude"]]
    if largo.empty:
        return vazia
    largo = largo[[estacao for estacao in largo.columns if estacao in posicoes.index]]
    if largo.shape[1] == 0:
        return vazia
    valores = calculos.idw_no_tempo(posicoes.loc[largo.columns, "Longitude"], posicoes.loc[largo.columns, "Latitude"],
                                    largo.to_numpy(dtype=float), lon, lat, centro,
                                    config.IDW_VIZINHOS, config.IDW_POTENCIA)
    valores[largo.notna().sum(axis=1).to_numpy() < config.MIN_ESTACOES_INTERPOLACAO] = np.nan
    quando = pd.to_datetime(largo.index)
    if quando.tz is not None:
        quando = quando.tz_localize(None)
    return pd.DataFrame({"quando": quando, "valor": valores}).dropna().reset_index(drop=True)


def serie_do_risco_no_ponto(avaliadas: dict, lon: float, lat: float, centro: tuple[float, float],
                            fuso) -> pd.DataFrame:
    # As três medidas da regra no ponto, hora a hora: a cada hora avaliada, o IDW das estações que
    # mediram as três, como na superfície. O formato é o da série da estação, para o mesmo gráfico
    vazia = pd.DataFrame(columns=["dt_local", "Medida", "valor", "limiar", "Condição"])
    if not avaliadas:
        return vazia
    horas = sorted(avaliadas)
    longo = pd.concat([avaliada.estacoes.assign(_hora=hora) for hora, avaliada in avaliadas.items()],
                      ignore_index=True)
    posicoes = longo.drop_duplicates("Estação").set_index("Estação")[["Longitude", "Latitude"]]
    campos = []
    for coluna in MEDIDAS_DO_RISCO:
        largo = longo.groupby(["_hora", "Estação"])[coluna].first().unstack("Estação").reindex(horas)
        campos.append(calculos.idw_no_tempo(posicoes.loc[largo.columns, "Longitude"],
                                            posicoes.loc[largo.columns, "Latitude"], largo.to_numpy(dtype=float),
                                            lon, lat, centro, config.IDW_VIZINHOS, config.IDW_POTENCIA))
    dt_local = pd.DatetimeIndex(horas).tz_convert(fuso).tz_localize(None)
    return pd.concat([pd.DataFrame({"dt_local": dt_local, "Medida": f"{rotulo} ({unidade})", "valor": campo,
                                    "limiar": limiar, "Condição": np.where(atendida, "atendida", "não")})
                      for (rotulo, unidade, limiar), campo, atendida
                      in zip(MEDIDAS_DO_RISCO.values(), campos, risco_fogo.condicoes_atendidas(*campos))],
                     ignore_index=True)


def serie_da_variavel(produto, leituras: pd.DataFrame, nome: str, modo: str) -> pd.DataFrame:
    # A variável do mapa numa estação, hora a hora ou dia a dia, pela regra do produto
    largo = variaveis.no_tempo(produto, leituras, modo)
    if largo.empty or nome not in largo:
        return pd.DataFrame(columns=["quando", "valor"])
    serie = largo[nome].dropna()
    quando = pd.to_datetime(serie.index)
    if quando.tz is not None:
        quando = quando.tz_localize(None)
    return pd.DataFrame({"quando": quando, "valor": serie.to_numpy(dtype=float)})


def serie_da_chuva(leituras: pd.DataFrame, nome: str, de, ate, por_dia: bool) -> pd.DataFrame:
    # A chuva de uma estação em (de, ate], por hora ou por dia (a leitura da 00h fecha o dia
    # anterior, como no resto do projeto), com o acumulado ao longo da janela
    dela = leituras[(leituras["Estação"] == nome) & (leituras["dt_local"] > de) & (leituras["dt_local"] <= ate)]
    if dela.empty or "CHUVA" not in dela:
        return pd.DataFrame(columns=["quando", "chuva", "acumulado"])
    if por_dia:
        quando = (dela["dt_local"] - pd.Timedelta(hours=1)).dt.date.map(pd.Timestamp)
    else:
        quando = dela["dt_local"].dt.tz_localize(None)
    somas = dela.groupby(quando.to_numpy(), sort=True)["CHUVA"].sum(min_count=1)
    return pd.DataFrame({"quando": somas.index, "chuva": somas.to_numpy(),
                         "acumulado": somas.fillna(0).cumsum().to_numpy()})


def serie_da_chuva_no_ponto(leituras: pd.DataFrame, coordenadas: pd.DataFrame, lon: float, lat: float, de, ate,
                            por_dia: bool, centro: tuple[float, float]) -> pd.DataFrame:
    # A chuva num ponto em (de, ate], por hora ou por dia, com o acumulado: a cada hora (ou dia), o IDW
    # das estações que mediram. O mapa interpola o total de cada estação; quando alguma deixou de
    # mandar horas, a soma das horas interpoladas sai um pouco diferente dele
    janela = leituras[(leituras["dt_local"] > de) & (leituras["dt_local"] <= ate)]
    produto, modo = ((variaveis.por_nome("Chuva acumulada"), variaveis.DIA) if por_dia
                     else (variaveis.por_nome("Chuva na hora"), variaveis.HORA))
    serie = serie_do_ponto(produto, janela, coordenadas, lon, lat, modo, centro)
    return pd.DataFrame({"quando": serie["quando"], "chuva": serie["valor"].astype(float),
                         "acumulado": serie["valor"].astype(float).cumsum()})


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
