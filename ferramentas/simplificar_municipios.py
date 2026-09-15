"""Gera a versão simplificada do shapefile de municípios usada nos mapas.

Os limites municipais originais (shp/MS_mun.shp) têm ~755 mil vértices, com pontos a
cada poucos metros ao longo dos rios. No mapa, 1 pixel equivale a ~300 m, então a maior
parte desses pontos não aparece. A simplificação com tolerância de 0,001° (~100 m, menos
de meio pixel) mantém ~5% dos vértices, sem diferença visível nos mapas.

A versão simplificada serve só para desenhar os mapas: cada município é simplificado
separadamente, então as divisas entre vizinhos podem ficar com frestas ou sobreposições
de poucos metros. Para cálculos de área ou análises espaciais, use o original.

Uso (a partir da raiz do projeto), sempre que shp/MS_mun.* for atualizado:
    python ferramentas/simplificar_municipios.py
"""
from pathlib import Path

import geopandas as gpd
import shapely

PASTA_SHP = Path(__file__).resolve().parent.parent / "shp"
ORIGINAL = PASTA_SHP / "MS_mun.shp"
SIMPLIFICADO = PASTA_SHP / "MS_mun_simplificado.shp"
TOLERANCIA = 0.001  # graus (~100 m)


def main() -> None:
    municipios = gpd.read_file(ORIGINAL, encoding="utf-8")
    simplificado = municipios.copy()
    simplificado["geometry"] = municipios.geometry.simplify(TOLERANCIA, preserve_topology=True)
    simplificado.to_file(SIMPLIFICADO, encoding="utf-8")

    antes = int(shapely.get_num_coordinates(municipios.geometry.values).sum())
    depois = int(shapely.get_num_coordinates(simplificado.geometry.values).sum())
    print(f"{ORIGINAL.name}: {antes:,} vértices -> {SIMPLIFICADO.name}: {depois:,} vértices ({depois / antes:.1%})")


if __name__ == "__main__":
    main()
