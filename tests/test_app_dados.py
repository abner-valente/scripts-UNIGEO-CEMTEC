"""Cache local do explorador: quando ele evita a API e quando precisa baixar de novo."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app import chuva
from app import dados as coleta
from app import variaveis
from modulos import config, inmet
from modulos.config import Periodo

DIA = Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15))


@pytest.fixture
def cache_temporario(monkeypatch, tmp_path):
    monkeypatch.setattr(coleta, "PASTA_CACHE", tmp_path / "cache")
    return tmp_path


@pytest.fixture
def api_contada(monkeypatch, serie):
    chamadas = []

    def baixar(codigo, inicio, fim, fuso=None):
        chamadas.append((inicio, fim))
        return serie("2026-09-15", "2026-09-16 05:00")

    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar)
    return chamadas


def test_primeira_consulta_baixa_e_guarda(cache_temporario, api_contada):
    leituras = coleta.leituras("A702", *DIA.janela)

    assert len(chamadas := api_contada) == 1
    assert len(leituras) == 24  # o dia inteiro no horário de MS
    assert (cache_temporario / "cache" / "A702.pkl").exists()
    assert chamadas[0] == DIA.janela


def test_segunda_consulta_igual_nao_chama_a_api(cache_temporario, api_contada):
    coleta.leituras("A702", *DIA.janela)
    leituras = coleta.leituras("A702", *DIA.janela)

    assert len(api_contada) == 1  # a segunda veio do cache
    assert len(leituras) == 24


def test_janela_fora_do_cache_baixa_de_novo(cache_temporario, api_contada):
    coleta.leituras("A702", *DIA.janela)
    seguinte = Periodo.de_datas(date(2026, 9, 16), date(2026, 9, 16))

    coleta.leituras("A702", *seguinte.janela)

    assert len(api_contada) == 2


def test_alargar_a_janela_baixa_so_o_que_falta(cache_temporario, api_contada):
    coleta.leituras("A702", *DIA.janela)
    semana = Periodo.de_datas(date(2026, 9, 9), date(2026, 9, 15))

    coleta.leituras("A702", *semana.janela)

    assert len(api_contada) == 2
    comeco, termino = api_contada[1]
    assert comeco == semana.janela[0]        # o pedaço que falta começa no início novo
    assert termino < semana.janela[1]        # e para onde o cache já alcançava


def test_varias_sai_na_ordem_pedida_e_separa_quem_falhou(cache_temporario, monkeypatch, serie):
    def baixar(codigo, inicio, fim, fuso=None):
        if codigo == "A999":
            raise inmet.ErroINMET("estação fora do ar")
        return serie("2026-09-15", "2026-09-16 05:00")

    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar)

    tabela, falharam = coleta.varias(("A702", "A999", "A703"),
                                     ("Campo Grande", "Fantasma", "Bonito"), *DIA.janela)

    assert list(dict.fromkeys(tabela["Estação"])) == ["Campo Grande", "Bonito"]
    assert falharam == ["Fantasma"]


def test_hora_guardada_vazia_e_reconsultada(cache_temporario, monkeypatch, serie):
    chamadas = []
    resposta = serie("2026-09-15", "2026-09-16 05:00")
    medidas = [coluna for coluna in resposta.columns if coluna not in coleta.IDENTIFICACAO]
    resposta.loc[resposta["dt_utc"] >= pd.Timestamp("2026-09-15 20:00", tz="UTC"), medidas] = np.nan

    def baixar(codigo, inicio, fim, fuso=None):
        chamadas.append((inicio, fim))
        return resposta

    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar)

    coleta.leituras("A702", *DIA.janela)
    coleta.leituras("A702", *DIA.janela)

    assert len(chamadas) == 2                                          # a ponta foi pedida de novo
    assert chamadas[1][0] == pd.Timestamp("2026-09-15 19:00", tz="UTC")  # da última hora com medida


def test_a_reconferencia_nao_volta_alem_do_teto(cache_temporario, monkeypatch, serie):
    vazia = serie("2026-09-15", "2026-09-16 05:00")
    medidas = [coluna for coluna in vazia.columns if coluna not in coleta.IDENTIFICACAO]
    vazia[medidas] = np.nan
    monkeypatch.setattr(inmet, "baixar_dados_estacao", lambda codigo, inicio, fim, fuso=None: vazia)

    coleta.leituras("A702", *DIA.janela)
    faltando = coleta._faltando(coleta._ler("A702"), *DIA.janela)

    comeco, fim = faltando[0]
    assert (fim - comeco).total_seconds() / 3600 <= coleta.HORAS_A_RECONFERIR


@pytest.fixture
def api_da_janela(monkeypatch, serie):
    # A API que devolve as horas (início, fim] pedidas, como a do INMET, e conta os pedidos
    chamadas = []

    def baixar(codigo, inicio, fim, fuso=None):
        chamadas.append((inicio, fim))
        comeco, termino = (pd.Timestamp(momento).tz_convert("UTC") + pd.Timedelta(hours=1) for momento in (inicio, fim))
        return serie(f"{comeco:%Y-%m-%d %H:%M}", f"{termino:%Y-%m-%d %H:%M}")

    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar)
    return chamadas


def test_o_cache_que_ficou_dias_sem_uso_busca_o_trecho_do_meio(cache_temporario, api_da_janela):
    # 13/11/2026: o painel de teste guardou as leituras até 05/11; em 13/11, o mensal pedia só as
    # últimas 48 h, e a chuva de 05 a 11/11 ficava de fora (Fátima do Sul: 112,6 mm em vez de 164,4)
    ate_dia_5 = Periodo.de_datas(date(2026, 11, 1), date(2026, 11, 5))
    mes = Periodo.de_datas(date(2026, 11, 1), date(2026, 11, 12))
    coleta.leituras("S708", *ate_dia_5.janela)

    leituras = coleta.leituras("S708", *mes.janela)

    assert len(leituras) == 12 * 24                                  # o mês inteiro, sem buraco
    assert api_da_janela[1] == (ate_dia_5.janela[1], mes.janela[1])  # do fim do que havia ao fim pedido


def test_o_buraco_que_ja_esta_no_cache_e_preenchido(cache_temporario, api_da_janela):
    # Os caches da 0.3.6 que já ficaram furados se consertam na primeira consulta
    coleta.leituras("S708", *Periodo.de_datas(date(2026, 11, 1), date(2026, 11, 5)).janela)
    coleta.leituras("S708", *Periodo.de_datas(date(2026, 11, 11), date(2026, 11, 12)).janela)
    mes = Periodo.de_datas(date(2026, 11, 1), date(2026, 11, 12))

    leituras = coleta.leituras("S708", *mes.janela)

    assert len(leituras) == 12 * 24
    comeco, termino = api_da_janela[2]
    assert comeco == pd.Timestamp("2026-11-06 04:00", tz="UTC")    # a primeira hora que faltava, menos 1 h
    assert termino >= pd.Timestamp("2026-11-11 04:00", tz="UTC")


def test_a_hora_que_a_estacao_nao_mandou_e_pedida_de_novo_so_ela(cache_temporario, monkeypatch, serie):
    # Sem linha no meio (a estação ficou fora do ar das 10 às 14 h): o pedido seguinte é só desse
    # trecho, e não a janela inteira
    resposta = serie("2026-09-15", "2026-09-16 05:00")
    resposta = resposta[~resposta["dt_utc"].between(pd.Timestamp("2026-09-15 10:00", tz="UTC"),
                                                    pd.Timestamp("2026-09-15 14:00", tz="UTC"))]
    monkeypatch.setattr(inmet, "baixar_dados_estacao", lambda codigo, inicio, fim, fuso=None: resposta)
    coleta.leituras("A702", *DIA.janela)

    faltando = coleta._faltando(coleta._ler("A702"), *DIA.janela)

    assert faltando == [(pd.Timestamp("2026-09-15 09:00", tz="UTC"), pd.Timestamp("2026-09-15 14:00", tz="UTC"))]


def test_as_horas_vazias_antes_do_buraco_tambem_sao_pedidas_de_novo(cache_temporario, monkeypatch, serie):
    # Como o cache de Fátima do Sul em 13/11: guardado em 05/11 até as 23 h, mas com medida só até
    # o meio-dia (o INMET ainda não tinha preenchido a tarde), e nada depois. O pedido começa no
    # meio-dia, e não no fim das linhas vazias
    resposta = serie("2026-11-01", "2026-11-06")
    medidas = [coluna for coluna in resposta.columns if coluna not in coleta.IDENTIFICACAO]
    resposta.loc[resposta["dt_utc"] > pd.Timestamp("2026-11-05 12:00", tz="UTC"), medidas] = np.nan
    monkeypatch.setattr(inmet, "baixar_dados_estacao", lambda codigo, inicio, fim, fuso=None: resposta)
    mes = Periodo.de_datas(date(2026, 11, 1), date(2026, 11, 12))
    coleta.leituras("S708", *Periodo.de_datas(date(2026, 11, 1), date(2026, 11, 5)).janela)

    faltando = coleta._faltando(coleta._ler("S708"), *mes.janela)

    assert faltando == [(pd.Timestamp("2026-11-05 12:00", tz="UTC"), mes.janela[1])]


def test_os_pedacos_que_se_cobrem_viram_um_pedido_so():
    horas = [pd.Timestamp(f"2026-11-01 {hora:02d}:00", tz="UTC") for hora in range(24)]

    assert coleta._unir([(horas[5], horas[9]), (horas[1], horas[3]), (horas[3], horas[4]), (horas[8], horas[12])]) \
        == [(horas[1], horas[4]), (horas[5], horas[12])]


def test_estacao_sem_dados_nao_deixa_arquivo(cache_temporario, monkeypatch):
    monkeypatch.setattr(inmet, "baixar_dados_estacao", lambda codigo, inicio, fim, fuso=None: None)

    assert coleta.leituras("A702", *DIA.janela).empty
    assert not (cache_temporario / "cache").exists()


def test_a_hora_local_sai_do_utc_e_nao_do_arquivo(cache_temporario, api_contada):
    from zoneinfo import ZoneInfo

    em_ms = coleta.leituras("A702", *DIA.janela)
    em_sp = coleta.leituras("A702", *DIA.janela, fuso=ZoneInfo("America/Sao_Paulo"))

    assert len(api_contada) == 1                       # a segunda leitura veio do mesmo cache
    assert str(em_ms["dt_local"].dt.tz) == "America/Campo_Grande"
    assert str(em_sp["dt_local"].dt.tz) == "America/Sao_Paulo"
    assert em_ms["dt_utc"].equals(em_sp["dt_utc"])     # o instante é o mesmo; o relógio é que muda


def test_a_planilha_traz_tudo_o_que_a_api_devolveu():
    leituras = pd.DataFrame({
        "dt_utc": pd.to_datetime(["2026-09-15 12:00"], utc=True),
        "dt_local": pd.to_datetime(["2026-09-15 08:00"]),
        "Estação": ["Bonito"], "CHUVA": [0.0], "TEM_INS": [25.0],
        "TEN_BAT": [13.1], "TEM_SEN": [26.0], "COLUNA_NOVA_DA_API": [1],
    })

    planilha = coleta.planilha(leituras)

    assert {"CHUVA", "TEN_BAT", "TEM_SEN"} <= set(planilha.columns)   # nenhum filtro as tira
    assert list(planilha.columns[:2]) == ["Estação", "Data/Hora (MS)"]
    assert "dt_utc" not in planilha                                   # DT_MEDICAO já diz isso
    assert planilha.columns[-1] == "COLUNA_NOVA_DA_API"               # o que não conhecemos sai no fim


def test_o_csv_leva_a_sigla_do_estado_na_coluna_de_horario():
    leituras = pd.DataFrame({
        "dt_utc": pd.to_datetime(["2026-09-15 12:00"], utc=True),
        "dt_local": pd.to_datetime(["2026-09-15 09:00"]),
        "Estação": ["Itajai"], "TEM_INS": [21.0],
    })

    planilha = coleta.planilha(leituras, "SC")

    assert list(planilha.columns[:2]) == ["Estação", "Data/Hora (SC)"]
    assert "Data/Hora (MS)" not in planilha
    # e a coluna renomeada continua no lugar certo da ordem, não jogada no fim
    assert planilha.columns[-1] != "Data/Hora (SC)"


def test_planilha_de_tabela_vazia_nao_quebra():
    assert coleta.planilha(pd.DataFrame()).empty


def test_limpar_cache_remove_os_arquivos(cache_temporario, api_contada):
    coleta.leituras("A702", *DIA.janela)
    quantidade, megabytes = coleta.tamanho_do_cache()

    assert (quantidade, megabytes > 0) == (1, True)
    assert coleta.limpar_cache() == 1
    assert coleta.tamanho_do_cache()[0] == 0


@pytest.mark.parametrize("codigos, nomes", [((), ()), (("A702",), ("Campo Grande",))])
def test_sem_nenhuma_estacao_a_tabela_vazia_ainda_tem_as_horas(cache_temporario, monkeypatch, codigos,
                                                                  nomes):
    from app import variaveis
    monkeypatch.setattr(inmet, "baixar_dados_estacao", lambda codigo, inicio, fim, fuso=None: None)
    fuso = Periodo.de_datas(date(2026, 9, 17), date(2026, 9, 17)).fuso

    tabela, falharam = coleta.varias(codigos, nomes, *DIA.janela, fuso=fuso)

    assert tabela.empty and falharam == list(nomes)
    assert {"Estação", "dt_utc", "dt_local"} <= set(tabela.columns)
    assert str(tabela["dt_local"].dt.tz) == str(fuso)
    momento = pd.Timestamp("2026-09-17 12:00", tz=fuso)
    assert tabela[tabela["dt_local"] > momento].empty
    assert variaveis.recorte(tabela, variaveis.HORA, momento).empty
    assert variaveis.recorte(tabela, variaveis.DIA, momento.normalize()).empty


# =====================================================
# A CHUVA DO CEMADEN JUNTO DA DO INMET
# =====================================================
INICIO_UTC = pd.Timestamp("2026-11-02 04:00", tz="UTC")   # a meia-noite de 02/11 em MS
FIM_UTC = pd.Timestamp("2026-11-03 04:00", tz="UTC")


def horas_de_chuva(chuvas: dict[str, float], desde="2026-11-02 00:00", ate="2026-11-03 23:00") -> pd.DataFrame:
    # Uma tabela por hora cheia em UTC, como as de fonte.chuva_cemaden e as do INMET; as horas fora de
    # `chuvas` ficam secas
    horas = pd.date_range(desde, ate, freq="h", tz="UTC")
    tabela = pd.DataFrame({"dt_utc": horas,
                           "CHUVA": [chuvas.get(f"{hora:%Y-%m-%d %H:%M}", 0.0) for hora in horas]})
    return tabela.assign(dt_local=tabela["dt_utc"].dt.tz_convert(config.FUSO_MS))


def test_a_chuva_do_cemaden_vira_uma_tabela_so_com_as_horas_do_periodo_e_o_nome_da_estacao():
    coletados = [(pd.Series({"Estação": "CEMADEN · Bonito (Centro)"}), horas_de_chuva({})),
                 (pd.Series({"Estação": "CEMADEN · Campo Grande (Jardim Panamá)"}), horas_de_chuva({}))]

    tabela = coleta.juntar_cemaden(coletados, INICIO_UTC, FIM_UTC)

    assert tabela.groupby("Estação").size().to_dict() == {"CEMADEN · Bonito (Centro)": 24,
                                                          "CEMADEN · Campo Grande (Jardim Panamá)": 24}
    assert tabela["dt_utc"].min() == INICIO_UTC + pd.Timedelta(hours=1)   # (início, fim], como o INMET
    assert tabela["dt_utc"].max() == FIM_UTC


def test_sem_estacao_do_cemaden_a_tabela_sai_vazia_mas_com_as_colunas_do_inmet():
    tabela = coleta.juntar_cemaden([], INICIO_UTC, FIM_UTC)

    assert tabela.empty and {"Estação", "dt_utc", "dt_local"} <= set(tabela.columns)


def test_o_pluviometro_do_cemaden_entra_nos_acumulados_e_no_mapa_do_dia_com_o_mesmo_peso_do_inmet():
    # Campo Grande (INMET) choveu 6 mm no dia de MS; o Jardim Panamá (CEMADEN), 18,4 mm, um deles na
    # hora das 04:00 UTC, que ainda é o dia 02/11 em MS
    inmet_cg = horas_de_chuva({"2026-11-02 18:00": 6.0}).assign(Estação="Campo Grande")
    panama = horas_de_chuva({"2026-11-02 04:00": 0.4, "2026-11-02 05:00": 3.0, "2026-11-02 20:00": 15.0,
                             "2026-11-03 04:00": 0.4})
    juntas = pd.concat([inmet_cg[(inmet_cg["dt_utc"] > INICIO_UTC) & (inmet_cg["dt_utc"] <= FIM_UTC)],
                        coleta.juntar_cemaden([(pd.Series({"Estação": "CEMADEN · Jardim Panamá"}), panama)],
                                              INICIO_UTC, FIM_UTC)], ignore_index=True)
    fim_ms = FIM_UTC.tz_convert(config.FUSO_MS).to_pydatetime()

    acumulado = chuva.acumulado(juntas, fim_ms, "24 h")
    produto = variaveis.por_nome("Chuva acumulada")
    no_dia = variaveis.por_estacao(produto, variaveis.recorte(juntas, variaveis.DIA,
                                                              pd.Timestamp("2026-11-02", tz=config.FUSO_MS)))

    assert acumulado.sort_values(ascending=False).round(1).to_dict() == {"CEMADEN · Jardim Panamá": 18.4,
                                                                        "Campo Grande": 6.0}
    assert no_dia.round(1).to_dict() == {"Campo Grande": 6.0, "CEMADEN · Jardim Panamá": 18.4}
