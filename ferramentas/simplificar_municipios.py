"""Prepara os shapefiles que os mapas de uma UF precisam, na pasta shp/.

São dois arquivos por estado, e é a existência deles que faz a UF aparecer no seletor do painel
(`config.ufs_disponiveis`):

- `<UF>_UF_2022.shp` — o contorno, que enquadra o desenho e recorta a superfície interpolada;
- `<UF>_mun_simplificado.shp` — as divisas municipais, usadas só para traçar as linhas cinzas.

Os dois vêm da malha territorial de 2022 do IBGE, que publica um arquivo por UF (dado público):
https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_municipais/municipio_2022/UFs/

O municipal passa por uma simplificação antes de entrar em shp/. Os limites originais de MS têm
~755 mil vértices, com pontos a cada poucos metros ao longo dos rios, e no mapa 1 pixel equivale
a ~300 m: a tolerância de 0,001° (~100 m, menos de meio pixel) mantém ~5% dos vértices sem
diferença visível. Cada município é simplificado por conta própria, então as divisas entre
vizinhos podem ficar com frestas de poucos metros — a versão simplificada serve para desenhar,
não para medir. Para cálculos de área, use o original.

O download bruto fica em `shp/fonte/`, fora do git: é grande e o IBGE o devolve quando precisar.
Versionados ficam só os dois arquivos que o painel lê — a nuvem do Streamlit clona o repositório
e não tem como baixar nada na hora de desenhar o mapa.

MS é a exceção: o municipal dele é um arquivo da casa (`shp/MS_mun.shp`, 79 municípios), e não a
malha do IBGE. Quando a fonte local existe, é ela que vale.

Uso (a partir da raiz do projeto):
    python ferramentas/simplificar_municipios.py --uf MT      # baixa do IBGE o que faltar
    python ferramentas/simplificar_municipios.py              # MS, do shp/MS_mun.shp que já existe
    python ferramentas/simplificar_municipios.py --uf MS --refazer
"""
import argparse
import io
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import requests
import shapely

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from modulos import config  # noqa: E402  (depende da raiz no sys.path, acima)

PASTA_FONTE = config.PASTA_SHP / "fonte"
URL_IBGE = ("https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
            "malhas_municipais/municipio_2022/UFs/{uf}/{uf}_{camada}_2022.zip")
TOLERANCIA = 0.001  # graus (~100 m)
TIMEOUT = 180       # o zip municipal de um estado grande passa de 20 MB


def fonte_ibge(uf: str, camada: str) -> Path:
    """O shapefile bruto de uma camada da UF, baixado do IBGE se ainda não estiver em shp/fonte/."""
    destino = PASTA_FONTE / uf / camada
    ja_baixado = sorted(destino.glob("*.shp"))
    if ja_baixado:
        return ja_baixado[0]

    url = URL_IBGE.format(uf=uf, camada=camada)
    print(f"baixando {url}")
    resposta = requests.get(url, timeout=TIMEOUT)
    resposta.raise_for_status()
    destino.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resposta.content)) as zipado:
        zipado.extractall(destino)

    encontrados = sorted(destino.glob("*.shp"))
    if not encontrados:
        raise SystemExit(f"o zip de {uf}/{camada} não trouxe nenhum .shp")
    return encontrados[0]


def fonte_municipal(uf: str) -> Path:
    """De onde saem as divisas municipais: o arquivo da casa, se houver, senão o do IBGE."""
    da_casa = config.PASTA_SHP / f"{uf}_mun.shp"
    return da_casa if da_casa.exists() else fonte_ibge(uf, "Municipios")


def _simplificada(camada: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """A mesma camada com a geometria simplificada na tolerância do projeto."""
    saida = camada.copy()
    saida["geometry"] = camada.geometry.simplify(TOLERANCIA, preserve_topology=True)
    return saida


def _vertices(camada: gpd.GeoDataFrame) -> int:
    return int(shapely.get_num_coordinates(camada.geometry.values).sum())


def gravar_contorno(uf: str, destino: Path) -> None:
    """Grava o contorno do estado, simplificado, com o nome que os mapas esperam.

    Simplificar importa mais aqui do que parece. O IBGE mapeia o litoral em detalhe fino: o
    contorno de SC tem 193 mil vértices contra 25 mil do de MT, que não tem costa. Isso são 3,1 MB
    de repositório — clonados pela nuvem do Streamlit a cada publicação — para uma diferença de
    **1 célula em 10.000** na máscara do recorte, e ainda deixa cada mapa 1,4× mais lento de
    desenhar, porque o contorno vira o caminho de corte de toda figura.

    Os contornos de MS e de MT já estão no repositório sem passar por aqui, e continuam como
    estão: são pequenos e já valeram por mapas publicados. Regerar o de MS com `--refazer` mudaria
    o corte dele em menos de 100 m — invisível, mas sem motivo.
    """
    contorno = gpd.read_file(fonte_ibge(uf, "UF"), encoding="utf-8")
    simplificado = _simplificada(contorno)
    simplificado.to_file(destino, encoding="utf-8")

    oeste, sul, leste, norte = simplificado.to_crs("EPSG:4326").total_bounds
    antes, depois = _vertices(contorno), _vertices(simplificado)
    print(f"{destino.name}: contorno de {oeste:.2f} a {leste:.2f} de longitude, "
          f"{sul:.2f} a {norte:.2f} de latitude — "
          f"{antes:,} vértices -> {depois:,} ({depois / antes:.1%})")


def simplificar(uf: str, destino: Path) -> None:
    """Grava a versão simplificada das divisas municipais e diz quanto sobrou."""
    origem = fonte_municipal(uf)
    municipios = gpd.read_file(origem, encoding="utf-8")
    simplificado = _simplificada(municipios)
    simplificado.to_file(destino, encoding="utf-8")

    antes, depois = _vertices(municipios), _vertices(simplificado)
    print(f"{origem.name}: {len(municipios)} municípios, {antes:,} vértices -> "
          f"{destino.name}: {depois:,} vértices ({depois / antes:.1%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uf", default=config.UF, type=str.upper, choices=sorted(config.ESTADOS),
                        help="sigla do estado (padrão: %(default)s)")
    parser.add_argument("--refazer", action="store_true",
                        help="grava de novo mesmo que os arquivos já existam")
    argumentos = parser.parse_args()
    uf = argumentos.uf

    for destino, gravar in zip(config.shapes_de(uf), (gravar_contorno, simplificar)):
        if destino.exists() and not argumentos.refazer:
            print(f"{destino.name}: já existe (--refazer para gravar de novo)")
            continue
        gravar(uf, destino)

    pronto = uf in config.ufs_disponiveis()
    print(f"\n{uf}: {'aparece' if pronto else 'NÃO aparece'} no seletor do painel")


if __name__ == "__main__":
    main()
