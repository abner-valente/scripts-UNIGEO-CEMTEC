"""Fonte dos dados: a costura entre quem usa as leituras e de onde elas vêm."""
import ast
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest

from modulos import banco, calculos, coletor_inmet, config, fonte, inmet, openmeteo

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
    "inicio_operacao": pd.to_datetime(["2001-05-07T00:00Z", None, "2003-02-01T00:00Z", None, None], utc=True),
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
    # O começo da operação, que a página "Localização Estações" mostra, como na lista do INMET
    assert pd.Timestamp(estacoes.set_index("CD_ESTACAO").at["A702", "DT_INICIO_OPERACAO"]) == pd.Timestamp("2001-05-07", tz="UTC")


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


# =====================================================
# O CEMADEN PELA FONTE "BANCO"
# =====================================================
CADASTRO_CEMADEN = pd.DataFrame({
    "codigo": ["500270402A", "500270403A", "500830502A", "411069001A"],
    "id_estacao": [7931, 7932, 7929, 1],
    "nome": ["UPA Aparecida", "Centro", "São Carlos", "Escola"],
    "municipio": ["Campo Grande", "Campo Grande", "Três Lagoas", "Curitiba"],
    "codibge": [5002704, 5002704, 5008305, 4106902], "uf": ["MS", "MS", "MS", "PR"],
    "latitude": [-20.52545, -20.46, -20.80873, -25.43], "longitude": [-54.59831, -54.62, -51.71331, -49.27],
    "tipo": "Pluviométrica", "instalada_em": pd.Timestamp("2015-04-01"),
    # A de Três Lagoas parou em 21/10 e não mandou mais nada
    "ultima_parada": pd.to_datetime(["2026-01-22 12:59", "2026-03-01 10:00", "2026-10-21 16:46", "2025-01-01 08:00"]),
    "ultima_remessa": pd.to_datetime(["2026-11-03 17:00", "2026-11-03 17:10", "2026-10-21 14:45", "2026-11-03 17:00"]),
})
# Leituras da UPA, em UTC: a das 23:10 da véspera cai na hora das 00:00; a das 03:40 (seca) e a das
# 04:00 na das 04:00, que fecha o dia de MS anterior; a das 04:10 já é do dia 13 em MS; chovendo, de
# 10 em 10 minutos
LEITURAS_CEMADEN = [("2026-10-12 23:10", 1.0), ("2026-10-13 03:40", 0.0), ("2026-10-13 04:00", 0.4),
                    ("2026-10-13 04:10", 0.2), ("2026-10-13 13:10", 2.4), ("2026-10-13 13:20", 0.6),
                    ("2026-10-13 14:00", 0.2)]


@pytest.fixture
def cemaden_no_banco(limpa, monkeypatch):
    monkeypatch.setattr(config, "FONTE_DADOS", "banco")
    monkeypatch.setattr(fonte, "_conectar", lambda: nullcontext(limpa))
    banco.gravar_cemaden_estacoes(limpa, CADASTRO_CEMADEN)
    banco.gravar_cemaden_chuva(limpa, pd.DataFrame({
        "codigo": "500270402A", "leitura_utc": pd.to_datetime([quando for quando, _ in LEITURAS_CEMADEN], utc=True),
        "chuva": [mm for _, mm in LEITURAS_CEMADEN]}))
    # A parada mandou antes do período
    banco.gravar_cemaden_chuva(limpa, pd.DataFrame({"codigo": ["500830502A"], "chuva": [5.0],
                                                    "leitura_utc": [pd.Timestamp("2026-10-01 12:00", tz="UTC")]}))
    return limpa


def test_as_estacoes_do_cemaden_tem_as_colunas_do_inmet_e_o_municipio_no_nome(cemaden_no_banco):
    estacoes = fonte.estacoes_cemaden("MS")

    assert list(estacoes.columns) == fonte.COLUNAS_ESTACOES_CEMADEN
    assert estacoes["Estação"].tolist() == ["CEMADEN · Campo Grande (Centro)", "CEMADEN · Campo Grande (UPA Aparecida)",
                                            "CEMADEN · Três Lagoas (São Carlos)"]
    assert estacoes["CD_SITUACAO"].tolist() == ["Operante", "Operante", "Parada"]   # a parada fica na lista
    assert estacoes.at[1, "VL_LATITUDE"] == -20.52545


def test_cada_leitura_do_cemaden_entra_na_hora_que_termina_na_hora_cheia_seguinte(cemaden_no_banco):
    estacoes = fonte.estacoes_cemaden("MS")

    coletados = fonte.chuva_cemaden(estacoes[estacoes["CD_ESTACAO"] == "500270402A"], DIA, DIA)

    (estacao, dados), = coletados
    assert estacao["Estação"] == "CEMADEN · Campo Grande (UPA Aparecida)"
    assert len(dados) == 24 and dados["dt_utc"].iloc[0] == pd.Timestamp("2026-10-13 00:00", tz="UTC")
    chuva = dados.set_index(dados["dt_utc"].dt.hour)["CHUVA"]
    assert chuva[0] == 1.0                     # a das 23:10 da véspera
    assert chuva[4] == 0.4 and chuva[5] == 0.2
    assert chuva[14] == 3.2                    # 13:10 + 13:20 + 14:00
    assert np.isnan(chuva[6])                  # sem leitura é sem dado, e não 0 mm
    assert {"CD_ESTACAO", "DC_NOME", "UF", "VL_LATITUDE", "VL_LONGITUDE", "DT_MEDICAO", "HR_MEDICAO",
            "dt_local"} <= set(dados.columns)


def test_o_dia_de_ms_soma_a_chuva_do_cemaden_como_a_do_inmet(cemaden_no_banco):
    estacoes = fonte.estacoes_cemaden("MS")
    (_, dados), = fonte.chuva_cemaden(estacoes[estacoes["CD_ESTACAO"] == "500270402A"], DIA, DIA)
    dia_em_ms = datetime(2026, 10, 13, 4, tzinfo=timezone.utc)   # 00:00 de 13/10 em MS

    # A hora das 04:00 UTC (as leituras das 03:40 e das 04:00) é do dia 12 em MS
    assert calculos.acumulado_chuva(dados, dia_em_ms, dia_em_ms + timedelta(days=1)) == 3.4


def test_a_estacao_do_cemaden_sem_leitura_no_periodo_fica_de_fora_com_aviso(cemaden_no_banco, capsys):
    coletados = fonte.chuva_cemaden(fonte.estacoes_cemaden("MS"), DIA, DIA)

    assert [estacao["CD_ESTACAO"] for estacao, _ in coletados] == ["500270402A"]
    assert ("CEMADEN: 1 de 3 estações com leitura no período; sem leitura (2): CEMADEN · Campo Grande (Centro), "
            "CEMADEN · Três Lagoas (São Carlos)") in capsys.readouterr().out


def test_com_a_fonte_apis_nao_ha_cemaden_e_o_banco_nem_e_aberto(monkeypatch):
    monkeypatch.setattr(fonte, "_conectar", lambda: pytest.fail("não devia abrir o banco"))

    assert not fonte.tem_cemaden()
    assert fonte.estacoes_cemaden("MS").empty
    assert list(fonte.estacoes_cemaden("MS").columns) == fonte.COLUNAS_ESTACOES_CEMADEN
    assert fonte.chuva_cemaden(CADASTRO_CEMADEN.rename(columns={"codigo": "CD_ESTACAO"}), DIA, DIA) == []


# =====================================================
# A PREVISÃO PELA FONTE "BANCO"
# =====================================================
RODADAS = [datetime(2026, 10, 13, 12, tzinfo=timezone.utc), datetime(2026, 10, 14, 0, tzinfo=timezone.utc)]


def prevista(pontos: list[str], modelo: str, rodada: datetime) -> pd.DataFrame:
    horas = pd.date_range(rodada, periods=2, freq="h")
    return pd.DataFrame([{"modelo": modelo, "rodada_utc": pd.Timestamp(rodada), "ponto": ponto,
                          "latitude": -20.0, "longitude": -54.0, "hora_prevista_utc": hora,
                          **{coluna: 1.0 for coluna in openmeteo.VARIAVEIS.values()}}
                         for ponto in pontos for hora in horas])


@pytest.fixture
def previsao_no_banco(limpa, monkeypatch):
    monkeypatch.setattr(config, "FONTE_DADOS", "banco")
    monkeypatch.setattr(fonte, "_conectar", lambda: nullcontext(limpa))
    for rodada in RODADAS:
        banco.gravar_previsao(limpa, prevista(["A702", "A703", "A704"], "ecmwf_ifs025", rodada), "estacao")
    banco.gravar_previsao(limpa, prevista(["A702"], "gfs_seamless", RODADAS[0]), "estacao")
    semanal = pd.DataFrame([{"modelo": config.MODELO_SEMANAS, "rodada_utc": pd.Timestamp(RODADAS[1]), "ponto": ponto,
                             "latitude": -20.0, "longitude": -54.0, "semana": datetime(2026, 10, 19).date(),
                             **{coluna: 0.5 for coluna in openmeteo.VARIAVEIS_SEMANAIS.values()}}
                            for ponto in ("A702", "A703")])
    banco.gravar_semanas(limpa, semanal, "estacao")
    return limpa


def test_a_rodada_da_previsao_no_banco_e_a_ultima_guardada_de_cada_modelo(previsao_no_banco):
    assert fonte.rodadas_da_previsao("estacao", config.MODELOS_PREVISAO) == {
        "ecmwf_ifs025": RODADAS[1], "gfs_seamless": RODADAS[0]}      # o ICON não tem rodada
    assert fonte.rodada_das_semanas("estacao") == RODADAS[1]
    assert fonte.rodada_das_semanas("grade") is None
    assert fonte.origem_da_previsao() == "o banco"


def test_a_previsao_do_banco_traz_a_rodada_pedida_so_nos_pontos_pedidos(previsao_no_banco):
    pedidos = pd.DataFrame({"ponto": ["A702", "A704"]})
    tabela = fonte.previsao("estacao", pedidos, "ecmwf_ifs025", RODADAS[0])
    assert list(tabela.columns) == openmeteo.COLUNAS_HORARIAS
    assert sorted(tabela["ponto"].unique()) == ["A702", "A704"]
    assert set(tabela["rodada_utc"]) == {pd.Timestamp(RODADAS[0])} and len(tabela) == 4
    semanas = fonte.semanas("estacao", pedidos, RODADAS[1])
    assert semanas["ponto"].tolist() == ["A702"] and list(semanas.columns) == openmeteo.COLUNAS_SEMANAIS


def test_a_grade_da_previsao_e_a_do_banco_ou_a_da_busca_sob_demanda(monkeypatch):
    monkeypatch.setattr(openmeteo, "pontos_da_grade", lambda recorte=config.RECORTE, passo=None: passo)
    assert (fonte.pontos_da_grade(), fonte.pontos_da_grade_semanal()) == (0.5, 0.5)
    monkeypatch.setattr(config, "FONTE_DADOS", "banco")
    assert (fonte.pontos_da_grade(), fonte.pontos_da_grade_semanal()) == (0.25, 0.5)
