"""Ponto de entrada: escolha do produto e das datas, e verificação do token."""
import sys
from datetime import date, datetime

import pytest

import main
from modulos import config, inmet
from modulos.config import FUSO_UTC, Periodo
from modulos.produtos import relatorio_inmet, risco_fogo


@pytest.mark.parametrize("argumentos, modo", [
    (["--dataini", "30/07/2026"], "dia"),
    (["--dataini", "2026-08-01", "--datafim", "31/08/2026"], "periodo"),
    (["--tempo-real"], "tempo_real"),
])
def test_datas_pela_linha_de_comando(argumentos, modo):
    _, periodo, _ = main.ler_consulta(argumentos)
    assert periodo.modo == modo


def test_sem_argumentos_usa_as_variaveis_do_main(monkeypatch):
    monkeypatch.setattr(main, "DATA_INICIAL", date(2026, 7, 1))
    monkeypatch.setattr(main, "DATA_FINAL", date(2026, 7, 1))
    produto, periodo, _ = main.ler_consulta([])
    assert produto is relatorio_inmet
    assert periodo.identificador == "20260701"

    monkeypatch.setattr(main, "DATA_INICIAL", None)
    monkeypatch.setattr(main, "DATA_FINAL", None)
    assert main.ler_consulta([])[1].modo == "tempo_real"


def test_produto_pela_linha_de_comando():
    produto, _, _ = main.ler_consulta(["--produto", "relatorio_inmet", "--tempo-real"])
    assert produto is relatorio_inmet
    assert main.ler_consulta(["--produto", "risco_fogo", "--tempo-real"])[0] is risco_fogo


def test_hrtodas_chega_ao_produto_como_opcao():
    _, _, opcoes = main.ler_consulta(["--produto", "risco_fogo", "--dataini", "16/09/2026", "--hrtodas"])
    assert opcoes["hrtodas"] is True
    assert main.ler_consulta(["--dataini", "16/09/2026"])[2]["hrtodas"] is False


def test_horas_pela_linha_de_comando():
    _, periodo, _ = main.ler_consulta(["--dataini", "14/09/2026", "--hrini", "8", "--datafim", "15/09/2026", "--hrfim", "08h"])
    assert periodo.janela == (datetime(2026, 9, 14, 12, tzinfo=FUSO_UTC), datetime(2026, 9, 15, 12, tzinfo=FUSO_UTC))


def test_so_a_hora_inicial_vai_ate_o_fim_do_dia():
    _, periodo, _ = main.ler_consulta(["--dataini", "15/09/2026", "--hrini", "06:00"])
    assert periodo.horas == 18


def test_horas_pelas_variaveis_do_main(monkeypatch):
    variaveis = {"DATA_INICIAL": date(2026, 9, 14), "DATA_FINAL": date(2026, 9, 15), "HORA_INICIAL": 12, "HORA_FINAL": 12}
    for nome, valor in variaveis.items():
        monkeypatch.setattr(main, nome, valor)
    assert main.ler_consulta([])[1].identificador == "20260914_12h_a_20260915_12h"
    assert main.ler_consulta(["--tempo-real"])[1].modo == "tempo_real"  # horas do arquivo não atrapalham o tempo real


@pytest.mark.parametrize("argumentos", [
    ["--dataini", "31/02/2026"],
    ["--dataini", "31/08/2026", "--datafim", "01/08/2026"],
    ["--produto", "nao_existe"],
    ["--dataini", "15/09/2026", "--hrini", "25"],
    ["--dataini", "15/09/2026", "--hrini", "8:30"],
    ["--dataini", "15/09/2026", "--hrini", "18", "--hrfim", "6"],
    ["--tempo-real", "--hrini", "8"],
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


class FluxoFalso:
    """Faz as vezes da saída padrão, guardando como foi reconfigurada."""

    def __init__(self):
        self.encoding = "cp1252"
        self.erros = None

    def reconfigure(self, encoding, errors):
        self.encoding, self.erros = encoding, errors


def test_saida_vai_para_utf8(monkeypatch):
    """Redirecionada, a saída do Windows vai em cp1252 e o primeiro emoji derrubaria a execução."""
    saida, erro = FluxoFalso(), FluxoFalso()
    monkeypatch.setattr(sys, "stdout", saida)
    monkeypatch.setattr(sys, "stderr", erro)

    main._saida_em_utf8()

    assert (saida.encoding, saida.erros) == ("utf-8", "replace")
    assert (erro.encoding, erro.erros) == ("utf-8", "replace")


def test_saida_sem_reconfigure_nao_quebra(monkeypatch):
    """Sob outro executor a saída pode não ser reconfigurável: só não fazemos nada."""
    monkeypatch.setattr(sys, "stdout", object())
    monkeypatch.setattr(sys, "stderr", object())
    main._saida_em_utf8()  # não levanta
