"""Ponto de entrada: escolha do produto e das datas, e verificação do token."""
from datetime import date

import pytest

import main
from modulos import config, inmet
from modulos.config import Periodo
from modulos.produtos import relatorio_inmet


@pytest.mark.parametrize("argumentos, modo", [
    (["--inicio", "30/07/2026"], "dia"),
    (["--inicio", "2026-08-01", "--fim", "31/08/2026"], "periodo"),
    (["--tempo-real"], "tempo_real"),
])
def test_datas_pela_linha_de_comando(argumentos, modo):
    _, periodo = main.ler_consulta(argumentos)
    assert periodo.modo == modo


def test_sem_argumentos_usa_as_variaveis_do_main(monkeypatch):
    monkeypatch.setattr(main, "DATA_INICIAL", date(2026, 7, 1))
    monkeypatch.setattr(main, "DATA_FINAL", date(2026, 7, 1))
    produto, periodo = main.ler_consulta([])
    assert produto is relatorio_inmet
    assert periodo.identificador == "20260701"

    monkeypatch.setattr(main, "DATA_INICIAL", None)
    monkeypatch.setattr(main, "DATA_FINAL", None)
    assert main.ler_consulta([])[1].modo == "tempo_real"


def test_produto_pela_linha_de_comando():
    produto, _ = main.ler_consulta(["--produto", "relatorio_inmet", "--tempo-real"])
    assert produto is relatorio_inmet


@pytest.mark.parametrize("argumentos", [
    ["--inicio", "31/02/2026"],
    ["--inicio", "31/08/2026", "--fim", "01/08/2026"],
    ["--produto", "nao_existe"],
])
def test_argumentos_invalidos(argumentos):
    with pytest.raises(SystemExit):
        main.ler_consulta(argumentos)


def test_produto_desconhecido_na_variavel(monkeypatch):
    monkeypatch.setattr(main, "PRODUTO", "nao_existe")
    with pytest.raises(SystemExit):
        main.ler_consulta([])


def test_todos_os_produtos_seguem_o_padrao():
    """Todo produto em modulos/produtos/ precisa de NOME (igual à chave), TITULO e executar(periodo)."""
    for nome, produto in main.PRODUTOS.items():
        assert produto.NOME == nome
        assert produto.TITULO
        assert callable(produto.executar)


def test_sem_token_para_antes_de_gerar_o_produto(api_simulada, monkeypatch):
    def api_proibida(*args, **kwargs):
        raise AssertionError("a API foi chamada sem token")

    monkeypatch.setattr(config, "TOKEN_INMET", "")
    monkeypatch.setattr(inmet, "listar_estacoes", api_proibida)
    assert main.executar(relatorio_inmet, Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))) == 1
