"""Cache local do explorador: quando ele evita a API e quando precisa baixar de novo."""
from datetime import date

import pandas as pd
import pytest

from app import dados as coleta
from modulos import inmet
from modulos.config import Periodo

DIA = Periodo.de_datas(date(2026, 9, 15), date(2026, 9, 15))


@pytest.fixture
def cache_temporario(monkeypatch, tmp_path):
    monkeypatch.setattr(coleta, "PASTA_CACHE", tmp_path / "cache")
    return tmp_path


@pytest.fixture
def api_contada(monkeypatch, serie):
    """Troca a API por dados sintéticos e conta quantas vezes ela foi chamada."""
    chamadas = []

    def baixar(codigo, inicio, fim):
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
    """Antes, pedir a semana tendo o dia guardado baixava a semana inteira de novo."""
    coleta.leituras("A702", *DIA.janela)
    semana = Periodo.de_datas(date(2026, 9, 9), date(2026, 9, 15))

    coleta.leituras("A702", *semana.janela)

    assert len(api_contada) == 2
    comeco, termino = api_contada[1]
    assert comeco == semana.janela[0]        # o pedaço que falta começa no início novo
    assert termino < semana.janela[1]        # e para onde o cache já alcançava


def test_varias_sai_na_ordem_pedida_e_separa_quem_falhou(cache_temporario, monkeypatch, serie):
    """Em paralelo a ordem de chegada muda a cada consulta; a do gráfico não pode mudar junto."""
    def baixar(codigo, inicio, fim):
        if codigo == "A999":
            raise inmet.ErroINMET("estação fora do ar")
        return serie("2026-09-15", "2026-09-16 05:00")

    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar)

    tabela, falharam = coleta.varias(("A702", "A999", "A703"),
                                     ("Campo Grande", "Fantasma", "Bonito"), *DIA.janela)

    assert list(dict.fromkeys(tabela["Estação"])) == ["Campo Grande", "Bonito"]
    assert falharam == ["Fantasma"]


def test_estacao_sem_dados_nao_deixa_arquivo(cache_temporario, monkeypatch):
    monkeypatch.setattr(inmet, "baixar_dados_estacao", lambda codigo, inicio, fim: None)

    assert coleta.leituras("A702", *DIA.janela).empty
    assert not (cache_temporario / "cache").exists()


def test_a_hora_local_sai_do_utc_e_nao_do_arquivo(cache_temporario, api_contada):
    """O cache guarda o que foi baixado uma vez; o fuso é de quem lê.

    Sem isso, o mesmo pickle serviria MS (GMT-04) e o Paraná (GMT-03) com a hora de quem gravou
    primeiro — uma hora errada em silêncio, que estragaria o fechamento do dia.
    """
    from zoneinfo import ZoneInfo

    em_ms = coleta.leituras("A702", *DIA.janela)
    em_sp = coleta.leituras("A702", *DIA.janela, fuso=ZoneInfo("America/Sao_Paulo"))

    assert len(api_contada) == 1                       # a segunda leitura veio do mesmo cache
    assert str(em_ms["dt_local"].dt.tz) == "America/Campo_Grande"
    assert str(em_sp["dt_local"].dt.tz) == "America/Sao_Paulo"
    assert em_ms["dt_utc"].equals(em_sp["dt_utc"])     # o instante é o mesmo; o relógio é que muda


def test_a_planilha_traz_tudo_o_que_a_api_devolveu():
    """Quem baixa quer o dado bruto, e não só as grandezas marcadas no filtro da tela."""
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


def test_planilha_de_tabela_vazia_nao_quebra():
    assert coleta.planilha(pd.DataFrame()).empty


def test_limpar_cache_remove_os_arquivos(cache_temporario, api_contada):
    coleta.leituras("A702", *DIA.janela)
    quantidade, megabytes = coleta.tamanho_do_cache()

    assert (quantidade, megabytes > 0) == (1, True)
    assert coleta.limpar_cache() == 1
    assert coleta.tamanho_do_cache()[0] == 0
