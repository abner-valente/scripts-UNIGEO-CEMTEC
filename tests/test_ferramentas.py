"""A ferramenta que prepara os shapefiles de uma UF (ferramentas/simplificar_municipios.py)."""
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

from ferramentas import simplificar_municipios as ferramenta
from modulos import config


def test_a_ferramenta_grava_onde_os_mapas_procuram():
    """Se os dois nomes divergirem, o estado é gerado e mesmo assim não aparece no seletor."""
    for uf in ("MS", "MT", "BA"):
        contorno, municipios = config.shapes_de(uf)
        assert contorno.name == f"{uf}_UF_2022.shp"
        assert municipios.name == f"{uf}_mun_simplificado.shp"
        assert contorno.parent == municipios.parent == config.PASTA_SHP


def test_a_fonte_da_casa_ganha_do_ibge(monkeypatch, tmp_path):
    """O municipal de MS é da equipe, com 79 municípios: baixar por cima trocaria o dado deles."""
    monkeypatch.setattr(config, "PASTA_SHP", tmp_path)
    monkeypatch.setattr(ferramenta, "fonte_ibge", lambda uf, camada: Path("baixado-do-ibge"))
    (tmp_path / "MS_mun.shp").touch()

    assert ferramenta.fonte_municipal("MS") == tmp_path / "MS_mun.shp"
    assert ferramenta.fonte_municipal("MT") == Path("baixado-do-ibge")  # sem fonte local, vai buscar


def test_o_zip_do_ibge_e_baixado_uma_vez_so(monkeypatch, tmp_path):
    """Extraído, o bruto fica em disco: gerar de novo não volta a puxar 9 MB da rede."""
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w") as zipado:
        zipado.writestr("MT_UF_2022.shp", b"geometria")
        zipado.writestr("MT_UF_2022.prj", b"projecao")

    idas = []

    def falso_get(url, timeout):
        idas.append(url)
        return SimpleNamespace(content=memoria.getvalue(), raise_for_status=lambda: None)

    monkeypatch.setattr(ferramenta, "PASTA_FONTE", tmp_path)
    monkeypatch.setattr(ferramenta.requests, "get", falso_get)

    primeiro = ferramenta.fonte_ibge("MT", "UF")
    segundo = ferramenta.fonte_ibge("MT", "UF")

    assert primeiro == segundo == tmp_path / "MT" / "UF" / "MT_UF_2022.shp"
    assert primeiro.read_bytes() == b"geometria"
    assert idas == [ferramenta.URL_IBGE.format(uf="MT", camada="UF")]


def test_o_endereco_do_ibge_aponta_para_a_malha_de_2022():
    url = ferramenta.URL_IBGE.format(uf="MT", camada="Municipios")
    assert url.startswith("https://geoftp.ibge.gov.br/")
    assert url.endswith("/municipio_2022/UFs/MT/MT_Municipios_2022.zip")
