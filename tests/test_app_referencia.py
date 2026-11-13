"""A referência das estações: a tabela numerada, o município de cada uma e a busca."""
import geopandas as gpd
import pandas as pd
import shapely

from app import referencia


def municipios() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"Municipio": ["Corumbá", "Dourados"]},
                            geometry=[shapely.box(-58.0, -20.0, -56.0, -18.0), shapely.box(-55.5, -22.5, -54.5, -21.5)],
                            crs="EPSG:4326")


def estacoes() -> pd.DataFrame:
    # Fora de ordem, como vem do INMET; a de Rio Brilhante cai fora de todo polígono
    return pd.DataFrame({
        "CD_ESTACAO": ["A721", "A724", "S999"],
        "Estação": ["Dourados", "Corumba", "Rio Brilhante"],
        "VL_LATITUDE": ["-22.19", "-19.02", "-21.80"],
        "VL_LONGITUDE": ["-54.91", "-57.67", "-54.40"],
        "VL_ALTITUDE": ["469.66", "112.1", None],
        "DT_INICIO_OPERACAO": ["2001-12-20T21:00:00.000-03:00", "2000-12-14T21:00:00.000-03:00", None],
    }, index=[7, 3, 9])


def test_a_tabela_e_numerada_em_ordem_alfabetica_com_o_municipio_de_cada_estacao():
    tabela = referencia.tabela_de_referencia(estacoes(), municipios())

    assert list(tabela.columns) == referencia.COLUNAS
    assert tabela["Nº"].tolist() == [1, 2, 3]
    assert tabela["Estação"].tolist() == ["Corumba", "Dourados", "Rio Brilhante"]
    assert tabela["Município"].tolist() == ["Corumbá", "Dourados", "Dourados"]   # a de fora vai à mais perto
    assert tabela["Código"].tolist() == ["A724", "A721", "S999"]


def test_vale_o_municipio_do_comeco_do_nome_e_senao_o_do_ponto():
    # A estação de Miranda que cai do lado de Dourados é de Miranda; Nhumirim, que não é município, fica no do ponto.
    limites = pd.concat([municipios(), gpd.GeoDataFrame({"Municipio": ["Miranda"]},
                                                        geometry=[shapely.box(-57.0, -21.0, -56.5, -20.5)],
                                                        crs="EPSG:4326")], ignore_index=True)
    estacoes = pd.DataFrame({"CD_ESTACAO": ["A722", "A702", "A729"],
                             "Estação": ["Miranda", "Nhumirim", "Dourados - Embrapa"],
                             "VL_LATITUDE": [-22.0, -19.0, -19.5], "VL_LONGITUDE": [-55.0, -57.0, -57.5]})

    tabela = referencia.tabela_de_referencia(estacoes, limites)

    assert dict(zip(tabela["Estação"], tabela["Município"])) == {
        "Dourados - Embrapa": "Dourados", "Miranda": "Miranda", "Nhumirim": "Corumbá"}


def test_altitude_inteira_e_o_dia_do_comeco_em_brasilia():
    tabela = referencia.tabela_de_referencia(estacoes(), municipios())

    assert tabela["Altitude (m)"].iloc[1] == 470 and pd.isna(tabela["Altitude (m)"].iloc[2])
    assert tabela["Em operação desde"].tolist()[:2] == ["14/12/2000", "20/12/2001"]
    assert pd.isna(tabela["Em operação desde"].iloc[2])


def test_a_busca_ignora_acento_e_maiuscula_e_olha_nome_municipio_e_codigo():
    tabela = referencia.tabela_de_referencia(estacoes(), municipios())

    assert tabela[referencia.procurar(tabela, "CORUMBÁ")]["Estação"].tolist() == ["Corumba"]
    assert tabela[referencia.procurar(tabela, "dourados")]["Estação"].tolist() == ["Dourados", "Rio Brilhante"]
    assert tabela[referencia.procurar(tabela, "a721")]["Estação"].tolist() == ["Dourados"]
    assert referencia.procurar(tabela, "  ").all()


def pluviometros() -> pd.DataFrame:
    # Como fonte.estacoes_cemaden: o nome com o município, e a instalação sem fuso
    return pd.DataFrame({
        "CD_ESTACAO": ["500320701A", "500370201A"],
        "Estação": ["CEMADEN · Dourados (Centro)", "CEMADEN · Corumbá (Cravo Vermelho)"],
        "VL_LATITUDE": [-22.22, -19.00], "VL_LONGITUDE": [-54.80, -57.65],
        "DT_INICIO_OPERACAO": pd.to_datetime(["2015-04-01 00:00", "2015-03-30 14:14"]),
    })


def test_o_pluviometro_do_cemaden_leva_a_rede_e_o_municipio_do_nome():
    tabela = referencia.tabela_de_referencia(pluviometros(), municipios(), rede="CEMADEN")

    assert tabela["Estação"].tolist() == ["CEMADEN · Corumbá (Cravo Vermelho)", "CEMADEN · Dourados (Centro)"]
    assert tabela["Rede"].tolist() == ["CEMADEN", "CEMADEN"]
    assert tabela["Município"].tolist() == ["Corumbá", "Dourados"]
    assert tabela["Em operação desde"].tolist() == ["30/03/2015", "01/04/2015"]   # a data sem fuso é de Brasília
    assert tabela["Altitude (m)"].isna().all()


def test_as_duas_redes_numa_lista_so_o_inmet_primeiro_e_os_numeros_em_sequencia():
    inmet = referencia.tabela_de_referencia(estacoes(), municipios())
    cemaden = referencia.tabela_de_referencia(pluviometros(), municipios(), rede="CEMADEN")

    juntas = referencia.com_o_cemaden(inmet, cemaden)

    assert list(juntas.columns) == referencia.COLUNAS
    assert juntas["Nº"].tolist() == [1, 2, 3, 4, 5]
    assert juntas["Rede"].tolist() == ["INMET"] * 3 + ["CEMADEN"] * 2
    assert referencia.com_o_cemaden(inmet, cemaden.iloc[0:0]) is inmet
    assert referencia.procurar(juntas, "cemaden").tolist() == [False] * 3 + [True] * 2


def test_sem_estacoes_a_tabela_sai_vazia_com_as_colunas():
    tabela = referencia.tabela_de_referencia(estacoes().iloc[0:0], municipios())
    assert tabela.empty and list(tabela.columns) == referencia.COLUNAS
