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


def test_estacao_sem_dados_nao_deixa_arquivo(cache_temporario, monkeypatch):
    monkeypatch.setattr(inmet, "baixar_dados_estacao", lambda codigo, inicio, fim: None)

    assert coleta.leituras("A702", *DIA.janela).empty
    assert not (cache_temporario / "cache").exists()


def test_limpar_cache_remove_os_arquivos(cache_temporario, api_contada):
    coleta.leituras("A702", *DIA.janela)
    quantidade, megabytes = coleta.tamanho_do_cache()

    assert (quantidade, megabytes > 0) == (1, True)
    assert coleta.limpar_cache() == 1
    assert coleta.tamanho_do_cache()[0] == 0
