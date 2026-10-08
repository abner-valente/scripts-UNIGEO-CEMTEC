"""Fonte dos dados: a costura entre quem usa as leituras e de onde elas vêm."""
import ast
from pathlib import Path

import pytest

from modulos import config, fonte, inmet

RAIZ = Path(__file__).resolve().parent.parent


def test_cada_funcao_repassa_o_pedido_ao_inmet(monkeypatch):
    """Com a fonte "apis", o pedido chega ao inmet com os mesmos argumentos, e a resposta volta igual."""
    pedidos = {}

    def anotar(nome):
        def chamada(*args, **kwargs):
            pedidos[nome] = (args, kwargs)
            return nome
        return chamada

    for nome in ("listar_estacoes", "estacoes_de_apoio", "baixar_dados_estacao", "baixar_estacoes",
                 "baixar_apoio"):
        monkeypatch.setattr(inmet, nome, anotar(nome))
    fuso, recorte = config.FUSO_MS, config.RECORTE

    assert fonte.estacoes("SC") == "listar_estacoes"
    assert fonte.estacoes_de_apoio(recorte, [1], [2]) == "estacoes_de_apoio"
    assert fonte.leituras_da_estacao("A702", "inicio", "fim", fuso) == "baixar_dados_estacao"
    assert fonte.leituras_do_estado("inicio", "fim", uf="SC", fuso=fuso) == "baixar_estacoes"
    assert fonte.leituras_de_apoio(recorte, [1], [2], "inicio", "fim", fuso=fuso) == "baixar_apoio"

    assert pedidos["listar_estacoes"] == (("SC",), {})
    assert pedidos["baixar_dados_estacao"] == (("A702", "inicio", "fim", fuso), {})
    assert pedidos["baixar_estacoes"] == (("inicio", "fim"), {"uf": "SC", "fuso": fuso})
    assert pedidos["baixar_apoio"] == ((recorte, [1], [2], "inicio", "fim"), {"fuso": fuso})


def test_uma_fonte_que_nao_existe_para_com_o_nome_dela(monkeypatch):
    """Um FONTE_DADOS escrito errado no servidor não pode cair em silêncio para as APIs."""
    monkeypatch.setattr(config, "FONTE_DADOS", "bancoo")

    with pytest.raises(ValueError, match="FONTE_DADOS='bancoo'.*apis"):
        fonte.estacoes("MS")


def test_o_erro_da_fonte_e_o_que_os_produtos_tratam():
    """Enquanto a única fonte é o INMET, quem trata ErroFonte trata a falha da API."""
    assert issubclass(inmet.ErroINMET, fonte.ErroFonte)


def _quem_importa_o_inmet(pasta: Path) -> list[str]:
    culpados = []
    for arquivo in sorted(pasta.glob("*.py")):
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            importa = (isinstance(no, ast.ImportFrom) and any(nome.name == "inmet" for nome in no.names)) \
                or (isinstance(no, ast.ImportFrom) and (no.module or "").endswith("inmet")) \
                or (isinstance(no, ast.Import) and any(nome.name.endswith("inmet") for nome in no.names))
            if importa:
                culpados.append(str(arquivo.relative_to(RAIZ)))
                break
    return culpados


def test_produtos_e_painel_pedem_os_dados_a_fonte_e_nao_ao_inmet():
    """A costura só funciona se ninguém passar por fora dela.

    Quem importar o inmet direto continua lendo das APIs quando a instalação pedir o banco, e a
    diferença só aparece no servidor. O inmet é de quem busca: o fonte.py e, mais tarde, os
    coletores.
    """
    assert _quem_importa_o_inmet(RAIZ / "modulos" / "produtos") == []
    assert _quem_importa_o_inmet(RAIZ / "app") == []
