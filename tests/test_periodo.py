"""Janelas de tempo compartilhadas pelos produtos (config.Periodo)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from modulos import config
from modulos.config import FUSO_UTC, Periodo


def utc(*partes):
    return datetime(*partes, tzinfo=FUSO_UTC)


def test_datas_iguais_sao_um_dia_no_horario_de_ms():
    periodo = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))
    assert periodo.modo == "dia"
    assert periodo.janela == (utc(2026, 7, 30, 4), utc(2026, 7, 31, 4))  # 00 h às 24 h em MS (UTC−4)
    assert periodo.janela_busca == periodo.janela
    assert periodo.identificador == "20260730"
    assert periodo.descricao == "30/07/2026 (horário de MS)"


def test_datas_diferentes_sao_periodo():
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    assert periodo.modo == "periodo"
    assert (periodo.primeiro_dia, periodo.ultimo_dia, periodo.num_dias) == (date(2026, 8, 1), date(2026, 8, 31), 31)
    assert periodo.janela == (utc(2026, 8, 1, 4), utc(2026, 9, 1, 4))
    assert periodo.identificador == "20260801_a_20260831"
    assert periodo.descricao == "Período: 01/08/2026 a 31/08/2026 (31 dias, horário de MS)"


def test_data_final_anterior_a_inicial_e_rejeitada():
    with pytest.raises(ValueError):
        Periodo.de_datas(date(2026, 8, 31), date(2026, 8, 1))


def test_dias_inteiros_sem_horas():
    periodo = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))
    assert periodo.dias_inteiros and periodo.horas == 24


def test_horarios_definem_a_janela():
    periodo = Periodo.de_datas(date(2026, 9, 14), date(2026, 9, 15), 8, 8)
    assert periodo.modo == "periodo"
    assert periodo.janela == (utc(2026, 9, 14, 12), utc(2026, 9, 15, 12))  # 08 h às 08 h em MS
    assert not periodo.dias_inteiros and periodo.horas == 24
    assert periodo.identificador == "20260914_08h_a_20260915_08h"
    assert periodo.descricao == "14/09/2026 08h a 15/09/2026 08h (24 horas, horário de MS)"


def test_horarios_no_mesmo_dia():
    periodo = Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15), 6, 18)
    assert periodo.modo == "dia"
    assert periodo.horas == 12
    assert periodo.identificador == "20260915_06h_a_20260915_18h"


@pytest.mark.parametrize("horas", [(18, 6), (25, 24), (0, -1)])
def test_horarios_invalidos_sao_rejeitados(horas):
    with pytest.raises(ValueError):
        Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15), *horas)


def test_tempo_real():
    agora = utc(2026, 9, 14, 13, 25)  # 9h25 em MS
    periodo = Periodo.tempo_real(agora)
    assert periodo.modo == "tempo_real"
    assert periodo.janela == (agora - timedelta(hours=24), agora)
    assert periodo.janela_busca == (agora - timedelta(hours=96), agora)
    assert periodo.inicio_do_dia == utc(2026, 9, 14, 4)  # 00 h de hoje em MS
    assert periodo.identificador == "20260914_0925"
    assert periodo.descricao == "Últimas 24 horas: 13/09/2026 09:25 a 14/09/2026 09:25 GMT-04"


def test_hoje_do_tempo_real_segue_o_horario_de_ms():
    """Às 02 UTC ainda são 22 h do dia anterior em MS."""
    assert Periodo.tempo_real(utc(2026, 9, 14, 2, 0)).inicio_do_dia == utc(2026, 9, 13, 4)


@pytest.mark.parametrize("periodo, texto", [
    (Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15)), "15/09/2026"),
    (Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31)), "01/08/2026 a 31/08/2026"),
    (Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15), 6, 18), "15/09/2026 06:00 a 15/09/2026 18:00 GMT-04"),
    (Periodo.tempo_real(utc(2026, 9, 14, 13, 25)), "13/09/2026 09:25 a 14/09/2026 09:25 GMT-04"),
], ids=["dia", "periodo", "com_horas", "tempo_real"])
def test_subtitulo_dos_mapas_traz_o_fuso_so_quando_ha_horario(periodo, texto):
    """Subtítulo igual em todos os produtos: datas sozinhas sem fuso, horários com GMT-04."""
    assert periodo.descrever_janela(*periodo.janela) == texto


def test_pasta_de_saida_separada_por_produto_e_modo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PASTA_SAIDA", tmp_path)
    periodo = Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))
    assert periodo.pasta_saida("relatorio_inmet") == tmp_path / "relatorio_inmet" / "periodo" / "20260801_a_20260831"


# =====================================================
# O RECORTE
# =====================================================
def test_o_recorte_padrao_e_ms_e_os_nomes_antigos_apontam_para_ele():
    """Uma verdade só sobre onde MS fica: os apelidos saem do recorte, não de outra constante."""
    assert config.RECORTE is config.RECORTES["MS"]
    assert (config.UF, config.NOME_UF) == (config.RECORTE.uf, config.RECORTE.nome)
    assert config.RECORTE.shape_uf.name.startswith("MS_")
    assert config.FUSO_MS is config.RECORTE.fuso


def test_o_enquadramento_de_ms_e_o_que_sempre_foi():
    """Derivá-lo da geometria mudaria por arredondamento, e com ele todo mapa já publicado."""
    assert config.RECORTES["MS"].limites == (-58.5, -50.5, -24.5, -17.0)


def test_o_fuso_do_recorte_decide_onde_o_dia_comeca():
    """MT e PR partem o mesmo dia em horas diferentes: o fuso não pode ser do módulo."""
    em_ms = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))
    em_sp = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30), fuso=ZoneInfo("America/Sao_Paulo"))

    assert em_ms.janela == (utc(2026, 7, 30, 4), utc(2026, 7, 31, 4))
    assert em_sp.janela == (utc(2026, 7, 30, 3), utc(2026, 7, 31, 3))   # uma hora antes
    assert em_sp.primeiro_dia == date(2026, 7, 30)                      # e o dia continua sendo o dele


def test_o_subtitulo_traz_o_fuso_do_recorte():
    em_sp = Periodo.de_datas(date(2026, 9, 14), date(2026, 9, 15), 8, 8,
                             fuso=ZoneInfo("America/Sao_Paulo"))
    assert em_sp.descrever_janela(*em_sp.janela).endswith("GMT-03")


def test_so_aparecem_as_ufs_que_tem_shapefile():
    """O seletor não pode oferecer um estado que o mapa não consegue desenhar."""
    for uf in config.ufs_disponiveis():
        contorno, municipios = config.shapes_de(uf)
        assert contorno.exists() and municipios.exists()
    assert "MS" in config.ufs_disponiveis()


def test_uf_sem_shapefile_avisa_em_vez_de_quebrar_no_meio(monkeypatch, tmp_path):
    """Sem os arquivos, o recorte diz qual falta em vez de estourar no meio do desenho.

    A pasta vazia entra por monkeypatch de propósito: apontar para uma UF que hoje não tem
    shapefile faria o teste parar de testar no dia em que ela ganhasse um.
    """
    monkeypatch.setattr(config, "PASTA_SHP", tmp_path)
    config.recorte_de.cache_clear()

    with pytest.raises(FileNotFoundError, match="Sem shapefile"):
        config.recorte_de("AC")
    with pytest.raises(KeyError, match="UF desconhecida"):
        config.recorte_de("XX")

    config.recorte_de.cache_clear()


def test_o_recorte_derivado_enquadra_o_estado_com_folga(monkeypatch, tmp_path):
    """Para as UFs sem entrada escrita à mão, o enquadramento sai do contorno mais uma margem."""
    import geopandas as gpd
    import shapely

    contorno = tmp_path / "PR_UF_2022.shp"
    gpd.GeoDataFrame(geometry=[shapely.box(-54.6, -26.7, -48.0, -22.5)],
                     crs="EPSG:4326").to_file(contorno)
    monkeypatch.setattr(config, "PASTA_SHP", tmp_path)
    config.recorte_de.cache_clear()

    derivado = config.recorte_de("PR")

    margem = config.MARGEM_ENQUADRAMENTO
    assert derivado.limites == (round(-54.6 - margem, 2), round(-48.0 + margem, 2),
                                round(-26.7 - margem, 2), round(-22.5 + margem, 2))
    assert derivado.nome == "Paraná"
    assert str(derivado.fuso) == "America/Sao_Paulo"
    # total_bounds devolve np.float64, e o enquadramento entra na chave de cache do painel
    assert all(type(limite) is float for limite in derivado.limites)
    config.recorte_de.cache_clear()


def test_todo_estado_disponivel_cabe_no_proprio_enquadramento():
    """Vale para cada UF acrescentada: o que a ferramenta gerou abre, e o estado aparece inteiro.

    Um contorno que passa da borda sai cortado no mapa sem ninguém avisar — o desenho continua,
    só que faltando pedaço de estado. O que se exige é o **corpo principal**: ilha oceânica
    distante fica fora do enquadramento de propósito, senão o mapa do ES viraria oceano.
    """
    import geopandas as gpd
    import shapely

    for uf in config.ufs_disponiveis():
        recorte = config.recorte_de(uf)
        assert recorte.uf == uf and recorte.nome == config.ESTADOS[uf][0]

        contorno = gpd.read_file(recorte.shape_uf).to_crs("EPSG:4326")
        municipios = gpd.read_file(recorte.shape_mun).to_crs("EPSG:4326")
        assert not contorno.empty and not municipios.empty, uf

        partes = list(shapely.get_parts(shapely.union_all(contorno.geometry.values)))
        principal = max(partes, key=lambda parte: parte.area)
        perto = shapely.union_all([parte for parte in partes
                                   if parte.distance(principal) <= config.ILHA_DISTANTE])

        oeste, leste, sul, norte = recorte.limites
        oeste_real, sul_real, leste_real, norte_real = perto.bounds
        assert oeste <= oeste_real and leste_real <= leste, uf
        assert sul <= sul_real and norte_real <= norte, uf


def test_a_ilha_oceanica_nao_estica_o_enquadramento():
    """Trindade fica a 1.100 km do ES: enquadrar por ela daria um mapa de oceano."""
    recorte = config.recorte_de("ES")
    largura = recorte.limites[1] - recorte.limites[0]
    altura = recorte.limites[3] - recorte.limites[2]

    assert largura < 3.5, f"ES saiu com {largura:.2f}° de largura — a ilha voltou"
    assert largura < altura          # o ES continental é mais alto que largo

