import io
import zipfile
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely

from app import car, interativo


def partes_do_shapefile(tabela: gpd.GeoDataFrame, tmp_path, nome: str = "AREA_IMOVEL_1",
                        **opcoes) -> dict[str, bytes]:
    tabela.to_file(tmp_path / f"{nome}.shp", **opcoes)
    return {caminho.suffix: caminho.read_bytes() for caminho in tmp_path.glob(f"{nome}.*")}


def gravado(tabela: gpd.GeoDataFrame, caminho, **opcoes) -> bytes:
    tabela.to_file(caminho, **opcoes)
    return caminho.read_bytes()


def zipado(arquivos: dict[str, bytes]) -> bytes:
    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w") as saida:
        for nome, conteudo in arquivos.items():
            saida.writestr(nome, conteudo)
    return pacote.getvalue()


def imoveis_do_teste() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"COD_IMOVEL": ["MS-5002704-AAA", "MS-5002704-BBB"], "NUM_AREA": [120.5, 3400.0],
                             "NOM_MUNICI": ["Campo Grande", "Corumbá"]},
                            geometry=[shapely.box(-54.70, -20.50, -54.65, -20.45),
                                      shapely.box(-55.00, -21.00, -54.80, -20.80)], crs="EPSG:4674")


def lida(fontes: dict[str, car.Fonte], nome: str) -> gpd.GeoDataFrame:
    return car.ler(fontes[nome])


def test_o_zip_do_sicar_abre_com_pasta_e_com_outro_zip_dentro(tmp_path):
    partes = partes_do_shapefile(imoveis_do_teste(), tmp_path)
    de_dentro = zipado({f"AREA_IMOVEL_1{sufixo}": conteudo for sufixo, conteudo in partes.items()})
    pacote = zipado({"SHAPE_5002704/AREA_IMOVEL.zip": de_dentro, "SHAPE_5002704/leia-me.txt": b"x"})

    fontes, de_fora = car.fontes([("SHAPE_5002704.zip", pacote)])

    assert list(fontes) == ["AREA_IMOVEL_1 (shapefile)"]
    assert de_fora == ["leia-me.txt: não é um formato de mapa"]
    assert len(lida(fontes, "AREA_IMOVEL_1 (shapefile)")) == 2


def test_os_arquivos_soltos_valem_e_o_shapefile_incompleto_fica_de_fora(tmp_path):
    partes = partes_do_shapefile(imoveis_do_teste(), tmp_path)
    soltos = [(f"AREA_IMOVEL_1{sufixo.upper()}", conteudo) for sufixo, conteudo in partes.items()]

    assert list(car.fontes(soltos)[0]) == ["AREA_IMOVEL_1 (shapefile)"]
    fontes, de_fora = car.fontes([(nome, conteudo) for nome, conteudo in soltos if not nome.endswith(".SHX")])
    assert fontes == {} and de_fora == ["AREA_IMOVEL_1: shapefile sem o .shx"]


def test_o_tipo_de_cada_arquivo_sai_do_conteudo(tmp_path):
    # O caso da equipe: o mesmo imóvel em shapefile (sem o .cpg), GeoJSON e GeoPackage, de uma vez.
    # Um GeoJSON com outro nome também vale; o que não é mapa fica de fora, com o nome
    tabela = imoveis_do_teste()
    partes = partes_do_shapefile(tabela, tmp_path, "A")
    geojson = gravado(tabela, tmp_path / "A.geojson", driver="GeoJSON")
    arquivos = ([(f"A{sufixo}", conteudo) for sufixo, conteudo in partes.items() if sufixo != ".cpg"]
                + [("A.geojson", geojson), ("A.gpkg", gravado(tabela, tmp_path / "A.gpkg", driver="GPKG")),
                   ("exportado.txt", geojson), ("foto.jpg", b"\xff\xd8\xff\xe0 nao e mapa")])

    fontes, de_fora = car.fontes(arquivos)

    assert sorted(fontes) == ["A (GeoJSON)", "A (GeoPackage)", "A (shapefile)", "exportado (GeoJSON)"]
    assert de_fora == ["foto.jpg: não é um formato de mapa"]
    for nome in fontes:
        assert car.rotulos(lida(fontes, nome)) == ["MS-5002704-AAA · Campo Grande · 120,5 ha",
                                                   "MS-5002704-BBB · Corumbá · 3.400,0 ha"], nome


def test_sem_o_cpg_o_acento_sai_certo_em_utf8_e_em_latin1(tmp_path):
    for codificacao in ("UTF-8", "latin1"):
        pasta = tmp_path / codificacao
        pasta.mkdir()
        partes = partes_do_shapefile(imoveis_do_teste(), pasta, encoding=codificacao)
        fontes, _ = car.fontes([(f"A{sufixo}", conteudo) for sufixo, conteudo in partes.items() if sufixo != ".cpg"])

        assert lida(fontes, "A (shapefile)")["NOM_MUNICI"].tolist() == ["Campo Grande", "Corumbá"], codificacao


def test_o_geopackage_com_varias_camadas_vira_uma_fonte_por_camada(tmp_path):
    caminho = tmp_path / "imovel.gpkg"
    imoveis_do_teste().to_file(caminho, layer="area_imovel", driver="GPKG")
    imoveis_do_teste().iloc[:1].to_file(caminho, layer="app", driver="GPKG")

    fontes, _ = car.fontes([("imovel.gpkg", caminho.read_bytes())])

    assert sorted(fontes) == ["imovel (GeoPackage: app)", "imovel (GeoPackage: area_imovel)"]
    assert car.camada_padrao(list(fontes)) == "imovel (GeoPackage: area_imovel)"
    assert len(lida(fontes, "imovel (GeoPackage: app)")) == 1


def test_o_kml_e_o_kmz_do_google_earth(tmp_path):
    kml = gravado(imoveis_do_teste().to_crs("EPSG:4326"), tmp_path / "fazenda.kml", driver="KML")

    fontes, de_fora = car.fontes([("fazenda.kml", kml), ("fazenda.kmz", zipado({"doc.kml": kml}))])

    assert list(fontes) == ["fazenda (KML)"] and de_fora == []   # o .kmz leva o nome dele, e não "doc"
    tabela = lida(fontes, "fazenda (KML)")
    assert car.imovel_da_linha(tabela, 1).titulo == "MS-5002704-BBB"
    assert not tabela.geometry.has_z.any()


def test_um_zip_que_nao_abre_vira_erro_legivel():
    with pytest.raises(car.ErroDoArquivo, match="não é um .zip"):
        car.fontes([("imovel.zip", b"PK\x03\x04 cortado no meio")])
    assert car.fontes([("imovel.zip", b"isto nao e um zip")]) == ({}, ["imovel.zip: não é um formato de mapa"])


def test_a_camada_padrao_e_a_da_area_do_imovel():
    assert car.camada_padrao(["APP_1", "AREA_IMOVEL_1", "RESERVA_LEGAL_1"]) == "AREA_IMOVEL_1"
    assert car.camada_padrao(["lotes", "glebas"]) == "glebas"


def test_a_camada_sai_em_graus_e_sem_o_prj_vale_o_sirgas(tmp_path):
    partes = partes_do_shapefile(imoveis_do_teste(), tmp_path)
    com_prj, _ = car.fontes([(f"A{sufixo}", conteudo) for sufixo, conteudo in partes.items()])
    sem_prj, _ = car.fontes([(f"A{sufixo}", conteudo) for sufixo, conteudo in partes.items() if sufixo != ".prj"])

    tabelas = [lida(fontes, "A (shapefile)") for fontes in (com_prj, sem_prj)]

    assert [tabela.crs.to_epsg() for tabela in tabelas] == [4326, 4326]
    np.testing.assert_allclose(tabelas[0].total_bounds, [-55.0, -21.0, -54.65, -20.45], atol=1e-6)


def test_o_rotulo_traz_o_codigo_o_municipio_e_a_area_declarada(tmp_path):
    fontes, _ = car.fontes([(f"A{sufixo}", conteudo) for sufixo, conteudo in partes_do_shapefile(
        imoveis_do_teste(), tmp_path).items()])
    tabela = lida(fontes, "A (shapefile)")

    assert car.rotulos(tabela) == ["MS-5002704-AAA · Campo Grande · 120,5 ha",
                                   "MS-5002704-BBB · Corumbá · 3.400,0 ha"]
    imovel = car.imovel_da_linha(tabela, 1)
    assert imovel.titulo == "MS-5002704-BBB" and imovel.detalhe == "3.400,0 ha"
    assert imovel.centro == pytest.approx((-54.9, -20.9), abs=0.01)
    assert imovel.chave != car.imovel_da_linha(tabela, 0).chave


def test_sem_codigo_nem_area_o_imovel_leva_o_numero_e_a_area_do_desenho():
    # Um quadrado de 0,01° no sul de MS: cerca de 1 km por 1,1 km, uns 115 ha
    tabela = gpd.GeoDataFrame(geometry=[shapely.box(-54.0, -22.0, -53.99, -21.99)], crs="EPSG:4326")

    rotulo, = car.rotulos(tabela)

    assert rotulo.startswith("Imóvel 1 · ")
    assert 100 < float(rotulo.split(" · ")[1].removesuffix(" ha").replace(".", "").replace(",", ".")) < 130


@pytest.mark.parametrize("texto", ["-20.4697, -54.6201", "-20,4697; -54,6201", "-20,4697, -54,6201",
                                   "-20.4697 -54.6201", "−20.4697, −54.6201"])
def test_as_coordenadas_decimais_em_qualquer_escrita(texto):
    assert car.ler_coordenadas(texto) == pytest.approx((-20.4697, -54.6201))


@pytest.mark.parametrize("texto", ["20°28'10.92\" S 54°37'12.36\" O", "20°28'10,92'' S, 54°37'12,36'' W",
                                   "54°37'12.36\"O 20°28'10.92\"S", "20°28'10.92\" 54°37'12.36\""])
def test_as_coordenadas_em_graus_minutos_e_segundos(texto):
    assert car.ler_coordenadas(texto) == pytest.approx((-20.4697, -54.6201), abs=1e-4)


@pytest.mark.parametrize("texto", ["-20.4697", "abc", "-120.5, -54.6", "20°28' S 21°10' N"])
def test_coordenadas_que_nao_se_leem_viram_erro(texto):
    with pytest.raises(ValueError):
        car.ler_coordenadas(texto)


def grade_de_teste():
    # Uma célula a cada 0,1°, como na grade dos testes de interativo, com o estado no meio
    base = SimpleNamespace(recorte=SimpleNamespace(limites=(-56.0, -53.0, -22.0, -19.0)),
                           uf=gpd.GeoDataFrame(geometry=[shapely.box(-55.45, -21.45, -53.55, -19.55)], crs="EPSG:4326"),
                           municipios=gpd.GeoDataFrame({interativo.COLUNA_DO_MUNICIPIO: ["Todo"]},
                                                       geometry=[shapely.box(-55.45, -21.45, -53.55, -19.55)],
                                                       crs="EPSG:4326"))
    return interativo.grade_do_ponto(base, resolucao=31)


def centros(grade, indices) -> list[tuple[float, float]]:
    return sorted((round(float(np.ravel(grade.lon)[i]), 2), round(float(np.ravel(grade.lat)[i]), 2)) for i in indices)


def test_o_imovel_leva_todas_as_celulas_que_toca():
    grade = grade_de_teste()

    # De -54,62 a -54,48 e de -20,46 a -20,38: toca as células de centro -54,6 e -54,5 em -20,4 e -20,5
    indices = car.celulas_do_imovel(grade, shapely.box(-54.62, -20.46, -54.48, -20.38))

    assert centros(grade, indices) == [(-54.6, -20.5), (-54.6, -20.4), (-54.5, -20.5), (-54.5, -20.4)]


def test_o_ponto_fica_na_celula_dele_e_fora_do_estado_nao_ha_celula():
    grade = grade_de_teste()

    assert centros(grade, car.celulas_do_imovel(grade, shapely.Point(-54.52, -20.43))) == [(-54.5, -20.4)]
    assert len(car.celulas_do_imovel(grade, shapely.Point(-55.9, -21.9))) == 0
    assert len(car.celulas_do_imovel(grade, shapely.box(-55.95, -21.95, -55.85, -21.85))) == 0


def test_o_resumo_traz_a_media_e_a_faixa_das_celulas():
    assert car.resumo(pd.Series([10.0, 20.0, 30.0]), 1, "mm") == "20,0 mm em média (de 10,0 a 30,0 nas 3 células)"
    assert car.resumo(pd.Series([31.24]), 1, "°C") == "31,2 °C"
