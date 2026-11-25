"""Prepara `shp/brasil_e_vizinhos.gpkg`: os estados do Brasil e os países em volta, para os mapas
largos dos raios (o círculo de config.RAIO_DOS_RAIOS_KM em volta de Campo Grande).

São duas camadas, recortadas por uma caixa de -82° a -28° de longitude e de -46° a 6° de latitude:

- `estados` — as UFs do Brasil, dos `<UF>_UF_2022.shp` do IBGE que já estão em shp/. As que não
  estão lá (em 24/11/2026, RR e SE) saem do contorno do Brasil do Natural Earth, menos as UFs do
  IBGE: as divisas com os vizinhos são as do IBGE, e só a costa e a fronteira vêm do outro;
- `paises` — os países do Natural Earth (Admin 0, escala 1:10m), menos o Brasil.

As duas são simplificadas com tolerância de 0,005° (~500 m): no PNG do boletim, 1 pixel cobre ~1 km.
Servem para desenhar, não para medir.

O Natural Earth é de domínio público (https://www.naturalearthdata.com). O arquivo de origem é o
`ne_10m_admin_0_countries.zip` (~4,9 MB), de
https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip, descompactado em
qualquer pasta. Versionado fica só o GeoPackage, com algumas centenas de KB: o painel não baixa nada
na hora de desenhar.

Uso (a partir da raiz do projeto):
    python ferramentas/brasil_e_vizinhos.py --paises <pasta>/ne_10m_admin_0_countries.shp
"""
import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modulos import config  # noqa: E402

CAIXA = (-82.0, -46.0, -28.0, 6.0)
TOLERANCIA = 0.005           # graus
AREA_MINIMA_DA_UF = 0.5      # graus²: o que sobra do Brasil menos as UFs do IBGE e não é uma UF é fresta
UFS = ("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO").split()


def estados_do_ibge() -> gpd.GeoDataFrame:
    linhas = []
    for uf in UFS:
        arquivo = config.PASTA_SHP / f"{uf}_UF_2022.shp"
        if arquivo.exists():
            forma = gpd.read_file(arquivo).to_crs("EPSG:4326").geometry.union_all()
            linhas.append({"uf": uf, "geometry": forma})
    return gpd.GeoDataFrame(linhas, crs="EPSG:4326")


def ufs_que_faltam(brasil, ibge: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    # O contorno do Brasil menos as UFs do IBGE: sobram as UFs que faltam e frestas finas na costa
    # e na fronteira. Cada pedaço grande é uma UF que faltava, reconhecida pela posição
    resto = brasil.difference(ibge.geometry.union_all().buffer(0.001))
    pedacos = [p for p in getattr(resto, "geoms", [resto]) if p.area >= AREA_MINIMA_DA_UF]
    faltam = [uf for uf in UFS if uf not in set(ibge["uf"])]
    # Em 24/11/2026: RR, no norte (acima do equador), e SE, no litoral do Nordeste
    linhas = []
    for pedaco in pedacos:
        centro = pedaco.representative_point()
        uf = "RR" if centro.y > 0 else "SE" if centro.x > -40 else None
        if uf in faltam:
            linhas.append({"uf": uf, "geometry": pedaco})
    return gpd.GeoDataFrame(linhas, columns=["uf", "geometry"], crs="EPSG:4326")


def main(argumentos: list[str] | None = None) -> int:
    leitor = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    leitor.add_argument("--paises", type=Path, required=True,
                        help="o ne_10m_admin_0_countries.shp do Natural Earth, já descompactado")
    opcoes = leitor.parse_args(argumentos)
    caixa = shapely.box(*CAIXA)

    paises = gpd.read_file(opcoes.paises).to_crs("EPSG:4326")
    # Sem os territórios minúsculos (a "Ilha Brasileira", em disputa no rio Quaraí, tem ~2 km²)
    paises = paises[paises.geometry.intersects(caixa) & (paises.geometry.area > 0.01)]
    brasil = paises.loc[paises["ADM0_A3"] == "BRA"].geometry.union_all()
    vizinhos = gpd.GeoDataFrame({"pais": paises.loc[paises["ADM0_A3"] != "BRA", "NAME_PT"].to_numpy()},
                                geometry=paises.loc[paises["ADM0_A3"] != "BRA"].geometry.to_numpy(), crs="EPSG:4326")

    ibge = estados_do_ibge()
    completos = ufs_que_faltam(brasil, ibge)
    estados = gpd.GeoDataFrame(pd.concat([ibge, completos], ignore_index=True), crs="EPSG:4326")
    print(f"UFs do IBGE: {len(ibge)}; completadas pelo Natural Earth: {', '.join(completos['uf']) or 'nenhuma'}")

    saida = config.PASTA_SHP / "brasil_e_vizinhos.gpkg"
    saida.unlink(missing_ok=True)
    for nome, camada in (("estados", estados), ("paises", vizinhos)):
        camada = camada.assign(geometry=camada.geometry.intersection(caixa).simplify(TOLERANCIA))
        camada = camada[~camada.geometry.is_empty].sort_values(camada.columns[0]).reset_index(drop=True)
        camada.to_file(saida, layer=nome, driver="GPKG")
        print(f"  {nome}: {len(camada)} ({', '.join(camada[camada.columns[0]])})")
    print(f"✅ {saida.relative_to(config.RAIZ)}: {saida.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
