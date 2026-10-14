"""Fonte dos dados: a costura entre quem usa as leituras e de onde elas vêm."""
import ast
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest

from modulos import banco, coletor_inmet, config, fonte, inmet

RAIZ = Path(__file__).resolve().parent.parent


def test_cada_funcao_repassa_o_pedido_ao_inmet(monkeypatch):
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
    monkeypatch.setattr(config, "FONTE_DADOS", "bancoo")

    with pytest.raises(ValueError, match="FONTE_DADOS='bancoo'.*apis"):
        fonte.estacoes("MS")


def test_o_erro_da_fonte_e_o_que_os_produtos_tratam():
    assert issubclass(inmet.ErroINMET, fonte.ErroFonte)
    assert issubclass(banco.ErroBanco, fonte.ErroFonte)


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
    assert _quem_importa_o_inmet(RAIZ / "modulos" / "produtos") == []
    assert _quem_importa_o_inmet(RAIZ / "app") == []


# =====================================================
# A FONTE "BANCO"
# =====================================================
CADASTRO = pd.DataFrame({
    "codigo": ["A702", "A703", "A704", "A807", "A001"],
    "nome": ["CAMPO GRANDE", "PONTA PORA", "AQUIDAUANA", "ICARAIMA", "BRASILIA"],
    "uf": ["MS", "MS", "MS", "PR", "DF"],
    "latitude": [-20.44361111, -22.55, -20.79, -23.39, -15.79],
    "longitude": [-54.72166666, -55.72, -51.71, -53.64, -47.93],
    "situacao": ["Operante", "Pane", "Operante", "Operante", "Operante"],
})
DIA = datetime(2026, 10, 13, tzinfo=timezone.utc)


def registros_da_api(codigo: str, nome: str, horas_vazias=(5,)) -> list[dict]:
    # Como a consulta por estação responde: texto, uma linha por hora, e as horas sem medida vazias
    registros = []
    for hora in range(24):
        medidas = {coluna.upper(): None if hora in horas_vazias else f"{20 + hora / 10 + i:.1f}"
                   for i, coluna in enumerate(banco.COLUNAS_INMET)}
        registros.append({"DC_NOME": nome, "UF": "MS", "CD_ESTACAO": codigo, "DT_MEDICAO": "2026-10-13",
                          "HR_MEDICAO": f"{hora:02d}00", "VL_LATITUDE": "-20.44361111",
                          "VL_LONGITUDE": "-54.72166666", "TEM_SEN": None, "TEN_BAT": None, "TEM_CPU": None,
                          **medidas})
    return registros


@pytest.fixture
def no_banco(limpa, monkeypatch):
    monkeypatch.setattr(config, "FONTE_DADOS", "banco")
    monkeypatch.setattr(fonte, "_conectar", lambda: nullcontext(limpa))
    banco.gravar_estacoes(limpa, CADASTRO)
    for codigo in ("A702", "A807"):
        api = inmet._tabelar(registros_da_api(codigo, "X"))
        banco.gravar_inmet(limpa, coletor_inmet.leituras_para_o_banco(api))
    return limpa


def test_a_fonte_banco_devolve_a_tabela_que_a_api_devolveria(no_banco):
    da_api = inmet._tabelar(registros_da_api("A702", "CAMPO GRANDE"), config.FUSO_MS)

    do_banco = fonte.leituras_da_estacao("A702", DIA, DIA, config.FUSO_MS)

    assert sorted(do_banco.columns) == sorted(da_api.columns)
    assert len(do_banco) == 24 and do_banco.iloc[5][list(map(str.upper, banco.COLUNAS_INMET))].isna().all()
    pd.testing.assert_frame_equal(do_banco[sorted(da_api.columns)], da_api[sorted(da_api.columns)],
                                  check_dtype=False)


def test_a_estacao_sem_leitura_no_periodo_vem_vazia_como_na_api(no_banco):
    assert fonte.leituras_da_estacao("A704", DIA, DIA) is None
    assert fonte.leituras_da_estacao("A999", DIA, DIA) is None
    assert fonte.leituras_da_estacao("A702", datetime(2026, 9, 1, tzinfo=timezone.utc),
                                     datetime(2026, 9, 2, tzinfo=timezone.utc)) is None


def test_as_estacoes_do_banco_sao_as_operantes_da_uf_como_na_lista_do_inmet(no_banco):
    estacoes = fonte.estacoes("MS")
    # Na ordem da lista do INMET, pelo nome, e não pelo código: é ela que desempata os rankings
    assert estacoes["CD_ESTACAO"].tolist() == ["A704", "A702"]
    assert estacoes["Estação"].tolist() == ["Aquidauana", "Campo Grande"]
    assert {"CD_ESTACAO", "DC_NOME", "SG_ESTADO", "VL_LATITUDE", "VL_LONGITUDE"} <= set(estacoes.columns)
    assert estacoes.set_index("CD_ESTACAO").at["A702", "VL_LATITUDE"] == -20.44361111


def test_as_leituras_do_estado_vem_por_estacao(no_banco, capsys):
    coletados = fonte.leituras_do_estado(DIA, DIA)
    assert [estacao["CD_ESTACAO"] for estacao, _ in coletados] == ["A702"]
    assert len(coletados[0][1]) == 24
    assert "Sem leituras no período (1): Aquidauana" in capsys.readouterr().out


def test_as_de_apoio_saem_do_cadastro_do_banco(no_banco, monkeypatch):
    monkeypatch.setattr(inmet.calculos, "apoio_que_entra", lambda lon, *resto: np.ones(len(lon), dtype=bool))
    vizinhas = fonte.estacoes_de_apoio(config.RECORTE, [], [])
    assert vizinhas["CD_ESTACAO"].tolist() == ["A807"]          # a do DF fica longe demais de MS
    coletados = fonte.leituras_de_apoio(config.RECORTE, [], [], DIA, DIA)
    assert [estacao["CD_ESTACAO"] for estacao, _ in coletados] == ["A807"]


def test_o_banco_fora_do_ar_e_um_erro_da_fonte(monkeypatch, capsys):
    monkeypatch.setattr(config, "FONTE_DADOS", "banco")

    def cair():
        raise psycopg.OperationalError("server closed the connection unexpectedly\nmais detalhes")
    monkeypatch.setattr(fonte, "_conectar", cair)

    with pytest.raises(fonte.ErroFonte, match="falha ao ler o banco: server closed the connection unexpectedly$"):
        fonte.estacoes("MS")
    assert fonte.leituras_de_apoio(config.RECORTE, [], [], DIA, DIA) == []
    assert "a borda sai só com as do estado" in capsys.readouterr().out
