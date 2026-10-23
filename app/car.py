"""O imóvel do CAR nos mapas interativos: o shapefile (ou as coordenadas) que a pessoa sobe, e as
células da grade que ele ocupa.

Tudo na memória: o arquivo que vem do navegador não é gravado em disco. Só cálculo, sem tela,
para poder ser testado.
"""
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

CRS_DO_SICAR = "EPSG:4674"      # SIRGAS 2000, o dos arquivos do SICAR: vale quando vem sem o .prj
CRS_DA_AREA = "EPSG:5880"       # o Policônico SIRGAS, em metros, para a área quando ela não vem
PARTES = (".shp", ".shx", ".dbf", ".prj", ".cpg")
OBRIGATORIAS = {".shp", ".shx", ".dbf"}
PROFUNDIDADE_DOS_ZIPS = 2       # o .zip do SICAR pode trazer outro .zip dentro
# Os nomes das colunas do SICAR, em minúsculas (os arquivos vêm ora em maiúsculas, ora não)
COLUNAS_DO_CODIGO = ("cod_imovel", "cod_car", "car", "codigo", "recibo")
COLUNAS_DO_MUNICIPIO = ("nom_munici", "municipio", "nom_municipio")
COLUNAS_DA_AREA = ("num_area", "area_ha", "area")


class ErroDoArquivo(Exception):
    pass


@dataclass(frozen=True)
class Imovel:
    titulo: str
    detalhe: str
    forma: shapely.Geometry   # em graus (EPSG:4326)

    @property
    def chave(self) -> str:
        # Curta e estável, para as chaves dos mapas: imóvel novo, mapa novo, centrado nele
        return hashlib.md5(shapely.to_wkb(self.forma)).hexdigest()[:10]

    @property
    def ponto(self) -> bool:
        return isinstance(self.forma, shapely.Point)

    @property
    def onde(self) -> str:
        return "no ponto" if self.ponto else "no imóvel"

    @property
    def centro(self) -> tuple[float, float]:
        # Um ponto dentro do imóvel (o centroide de um imóvel em "U" cairia fora dele)
        ponto = self.forma.representative_point()
        return ponto.x, ponto.y


# =====================================================
# O SHAPEFILE
# =====================================================
def camadas(arquivos: list[tuple[str, bytes]]) -> dict[str, dict[str, bytes]]:
    # Os shapefiles que vieram, cada um com as suas partes (".shp", ".dbf"...): de um .zip, com
    # pastas ou com outro .zip dentro, ou dos arquivos soltos. Fica só o que tem as três obrigatórias
    partes: dict[str, dict[str, bytes]] = {}

    def guardar(nome: str, conteudo: bytes, profundidade: int = 0) -> None:
        caminho = PurePosixPath(nome.replace("\\", "/"))
        sufixo = caminho.suffix.lower()
        if sufixo == ".zip" and profundidade < PROFUNDIDADE_DOS_ZIPS:
            try:
                with zipfile.ZipFile(io.BytesIO(conteudo)) as pacote:
                    for membro in pacote.infolist():
                        if not membro.is_dir():
                            guardar(membro.filename, pacote.read(membro), profundidade + 1)
            except zipfile.BadZipFile as erro:
                raise ErroDoArquivo(f"{caminho.name} não é um .zip que se abra.") from erro
        elif sufixo in PARTES:
            partes.setdefault(caminho.stem, {})[sufixo] = conteudo

    for nome, conteudo in arquivos:
        guardar(nome, conteudo)
    return {nome: dele for nome, dele in partes.items() if OBRIGATORIAS <= set(dele)}


def camada_padrao(nomes: list[str]) -> str:
    # O .zip de um imóvel do SICAR traz uma camada por tema (APP, reserva legal...): a que interessa
    # é a da área do imóvel
    return next((nome for nome in sorted(nomes) if "area_imovel" in nome.lower()), sorted(nomes)[0])


def ler_camada(partes: dict[str, bytes]) -> gpd.GeoDataFrame:
    # Um shapefile lido da memória: as partes vão num .zip montado aqui, sem pasta, que é o que o
    # leitor abre direto dos bytes. Sai em graus, sem geometria vazia e com a inválida consertada
    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w") as saida:
        for sufixo, conteudo in partes.items():
            saida.writestr(f"camada{sufixo}", conteudo)
    try:
        tabela = gpd.read_file(io.BytesIO(pacote.getvalue()))
    except Exception as erro:
        raise ErroDoArquivo(f"O shapefile não se abriu: {erro}") from erro
    if tabela.crs is None:
        tabela = tabela.set_crs(CRS_DO_SICAR)
    tabela = tabela[tabela.geometry.notna() & ~tabela.geometry.is_empty].to_crs("EPSG:4326")
    return tabela.set_geometry(tabela.geometry.make_valid()).reset_index(drop=True)


def _coluna(tabela: pd.DataFrame, candidatas: tuple[str, ...]) -> str | None:
    minusculas = {str(coluna).lower(): coluna for coluna in tabela.columns}
    return next((minusculas[nome] for nome in candidatas if nome in minusculas), None)


def _numero(valor: float, casas: int = 1) -> str:
    # À brasileira: 3.400,5
    return f"{valor:,.{casas}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def areas_em_ha(tabela: gpd.GeoDataFrame) -> pd.Series:
    # A declarada no CAR quando vem; senão, a do desenho
    coluna = _coluna(tabela, COLUNAS_DA_AREA)
    declarada = pd.to_numeric(tabela[coluna], errors="coerce") if coluna else pd.Series(np.nan, index=tabela.index)
    return declarada.fillna(tabela.to_crs(CRS_DA_AREA).area / 10_000)


def rotulos(tabela: gpd.GeoDataFrame) -> list[str]:
    # O que aparece na escolha do imóvel: o código do CAR, o município e a área, o que vier
    codigo, municipio = _coluna(tabela, COLUNAS_DO_CODIGO), _coluna(tabela, COLUNAS_DO_MUNICIPIO)
    areas = areas_em_ha(tabela)
    saida = []
    for posicao, (indice, linha) in enumerate(tabela.iterrows()):
        partes = [str(linha[codigo]) if codigo and pd.notna(linha[codigo]) else f"Imóvel {posicao + 1}"]
        if municipio and pd.notna(linha[municipio]):
            partes.append(str(linha[municipio]))
        partes.append(f"{_numero(areas[indice])} ha")
        saida.append(" · ".join(partes))
    return saida


def imovel_da_linha(tabela: gpd.GeoDataFrame, posicao: int) -> Imovel:
    codigo = _coluna(tabela, COLUNAS_DO_CODIGO)
    linha = tabela.iloc[posicao]
    titulo = str(linha[codigo]) if codigo and pd.notna(linha[codigo]) else f"Imóvel {posicao + 1}"
    area = areas_em_ha(tabela.iloc[[posicao]]).iloc[0]
    return Imovel(titulo=titulo, detalhe=f"{_numero(area)} ha", forma=linha.geometry)


# =====================================================
# AS COORDENADAS
# =====================================================
# Graus, minutos e segundos, com o hemisfério: 20°28'11,5" S. Minutos e segundos são opcionais
_GMS = re.compile(r"(\d+(?:[.,]\d+)?)\s*°\s*(?:(\d+(?:[.,]\d+)?)\s*['′’]\s*)?"
                  r"(?:(\d+(?:[.,]\d+)?)\s*(?:\"|″|”|''|′′)\s*)?([NSLOEW])?", re.IGNORECASE)
_DECIMAL = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _real(texto: str | None) -> float:
    return float(texto.replace(",", ".")) if texto else 0.0


def ler_coordenadas(texto: str) -> tuple[float, float]:
    # A latitude e a longitude, nessa ordem, como saem do Google Maps (-20.4697, -54.6201), com
    # ponto ou vírgula decimal; ou em graus, minutos e segundos. Sem o hemisfério, os graus
    # minutos e segundos ficam no sul e no oeste, onde está o estado
    texto = texto.replace("−", "-").strip()
    if "°" in texto:
        achados = _GMS.findall(texto)
        if len(achados) != 2:
            raise ValueError("Não achei duas coordenadas em graus: escreva, por exemplo, 20°28'11\" S 54°37'12\" O.")
        latitude = longitude = None
        for ordem, (graus, minutos, segundos, hemisferio) in enumerate(achados):
            valor = _real(graus) + _real(minutos) / 60 + _real(segundos) / 3600
            hemisferio = hemisferio.upper()
            if hemisferio in ("N", "S") or (not hemisferio and ordem == 0 and latitude is None):
                latitude = -valor if hemisferio != "N" else valor
            else:
                longitude = valor if hemisferio in ("L", "E") else -valor
        if latitude is None or longitude is None:
            raise ValueError("As duas coordenadas vieram com o mesmo eixo: uma deve ser a latitude (N/S) e a "
                             "outra a longitude (L/O).")
    else:
        numeros = _DECIMAL.findall(texto)
        if len(numeros) != 2:
            raise ValueError("Escreva a latitude e a longitude, por exemplo -20.4697, -54.6201.")
        latitude, longitude = (_real(numero) for numero in numeros)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("A latitude vai de -90 a 90 e a longitude de -180 a 180: confira a ordem (latitude primeiro).")
    return latitude, longitude


def imovel_do_ponto(latitude: float, longitude: float) -> Imovel:
    return Imovel(titulo="Ponto informado", detalhe="", forma=shapely.Point(longitude, latitude))


# =====================================================
# AS CÉLULAS DO IMÓVEL
# =====================================================
def celulas_do_imovel(grade, forma: shapely.Geometry) -> np.ndarray:
    # As células da grade do ponto (interativo.GradeDoPonto) que o imóvel toca, nos índices
    # achatados de celulas_do_ponto, só as de dentro do estado. Um ponto fica na célula dele
    dentro = np.flatnonzero(grade.dentro)
    lon, lat = np.ravel(grade.lon)[dentro], np.ravel(grade.lat)[dentro]
    dx, dy = grade.meio_passo
    if isinstance(forma, shapely.Point):
        na_celula = (np.abs(lon - forma.x) <= dx) & (np.abs(lat - forma.y) <= dy)
        if not na_celula.any():
            return np.array([], dtype=int)
        candidatas = np.flatnonzero(na_celula)
        perto = candidatas[np.argmin(np.hypot(lon[candidatas] - forma.x, lat[candidatas] - forma.y))]
        return dentro[[perto]]
    oeste, sul, leste, norte = forma.bounds
    perto = (lon + dx >= oeste) & (lon - dx <= leste) & (lat + dy >= sul) & (lat - dy <= norte)
    quadrados = shapely.box(lon[perto] - dx, lat[perto] - dy, lon[perto] + dx, lat[perto] + dy)
    return dentro[perto][shapely.intersects(forma, quadrados)]


def resumo(valores: pd.Series, casas: int, unidade: str) -> str:
    # O valor do imóvel nas células dele: a média, e a faixa quando são várias
    media = _numero(valores.mean(), casas)
    if len(valores) == 1:
        return f"{media} {unidade}"
    return (f"{media} {unidade} em média (de {_numero(valores.min(), casas)} a {_numero(valores.max(), casas)} "
            f"nas {len(valores)} células)")
