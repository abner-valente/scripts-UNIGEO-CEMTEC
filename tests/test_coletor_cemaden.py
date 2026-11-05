"""O coletor do CEMADEN: da PED para o banco, de hora em hora e o histórico.

A PED é simulada (`cemaden.cadastro` e `cemaden.chuva_do_dia` trocados por dados do teste). Os testes
que gravam usam o banco de teste (as fixtures `conexao` e `limpa`, no conftest.py) e são pulados sem ele.
"""
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

import coletar
from modulos import banco, cemaden, coletor_cemaden, config

# 22:30 de 04/11 em MS: em UTC já é o dia 05
AGORA = datetime(2026, 11, 5, 2, 30, tzinfo=timezone.utc)

# Duas estações no cadastro da PED e uma que saiu dele, mas ainda aparece nas leituras antigas
ESTACOES = {"500110201A": ("Centro", "AQUIDAUANA", -20.4826, -55.7851),
            "500568101A": ("Escola", "MUNDO NOVO", -23.9401, -54.2713),
            "500999901A": ("Antiga", "BELA VISTA", -22.1083, -56.5211)}
NO_CADASTRO = ["500110201A", "500568101A"]


def cadastro_da_ped() -> pd.DataFrame:
    linhas = [{"codigo": codigo, "id_estacao": n, "nome": ESTACOES[codigo][0],
               "municipio": ESTACOES[codigo][1].title(), "codibge": 5001102 + n, "uf": "MS",
               "latitude": ESTACOES[codigo][2], "longitude": ESTACOES[codigo][3], "tipo": "Pluviométrica",
               "instalada_em": pd.Timestamp("2015-03-30"), "ultima_parada": pd.Timestamp("2025-01-10 08:00"),
               "ultima_remessa": pd.Timestamp("2026-11-05 02:40")}
              for n, codigo in enumerate(NO_CADASTRO)]
    return pd.DataFrame(linhas, columns=cemaden.COLUNAS_DO_CADASTRO)


def chuva_do_dia(dia: date, codigos=NO_CADASTRO, mm: float = 2.4) -> pd.DataFrame:
    # Duas leituras por estação: o zero da madrugada e a chuva da tarde
    linhas = []
    for codigo in codigos:
        nome, municipio, latitude, longitude = ESTACOES[codigo]
        for hora, valor in ((0, 0.0), (13, mm)):
            linhas.append({"codigo": codigo, "leitura_utc": pd.Timestamp(dia, tz="UTC") + pd.Timedelta(hours=hora,
                                                                                                         minutes=40),
                           "chuva": valor, "nome": nome, "municipio": municipio, "uf": "MS",
                           "latitude": latitude, "longitude": longitude})
    return pd.DataFrame(linhas, columns=cemaden.COLUNAS_DA_CHUVA)


@pytest.fixture
def ped(monkeypatch):
    simulada = {"dias": [], "falhar": set(), "mm": {}, "codigos": NO_CADASTRO, "sem_cadastro": False}

    def cadastro(uf=config.UF):
        if simulada["sem_cadastro"]:
            raise cemaden.ErroCEMADEN("a PED não respondeu (depois de 3 tentativas)")
        return cadastro_da_ped()

    def chuva(dia, uf=config.UF):
        simulada["dias"].append(dia)
        if dia in simulada["falhar"]:
            raise cemaden.ErroCEMADEN("HTTP 503 (depois de 3 tentativas)")
        return chuva_do_dia(dia, simulada["codigos"], simulada["mm"].get(dia, 2.4))

    monkeypatch.setattr(cemaden, "cadastro", cadastro)
    monkeypatch.setattr(cemaden, "chuva_do_dia", chuva)
    monkeypatch.setattr(config, "CEMADEN_EMAIL", "equipe@exemplo.gov.br")
    monkeypatch.setattr(config, "CEMADEN_SENHA", "senha-de-teste")
    return simulada


def _contar(conexao, tabela: str) -> int:
    return conexao.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]


# =====================================================
# SEM O BANCO
# =====================================================
def test_os_ultimos_dias_sao_os_tres_dias_utc_ate_hoje():
    assert coletor_cemaden.ultimos_dias(AGORA) == [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)]


def test_a_estacao_das_leituras_vem_uma_vez_com_o_que_a_leitura_diz_dela():
    estacoes = coletor_cemaden.estacoes_das_leituras(chuva_do_dia(date(2016, 5, 1), ["500999901A"]))

    assert list(estacoes.columns) == cemaden.COLUNAS_DO_CADASTRO
    assert len(estacoes) == 1
    assert estacoes.loc[0, ["codigo", "nome", "municipio", "uf"]].tolist() == ["500999901A", "Antiga", "Bela Vista", "MS"]
    assert estacoes.loc[0, ["id_estacao", "tipo", "instalada_em"]].isna().all()


# =====================================================
# DE HORA EM HORA
# =====================================================
def test_a_coleta_grava_o_cadastro_e_os_tres_dias_e_so_reescreve_o_que_mudou(limpa, ped):
    primeira = coletor_cemaden.coletar_ultimos(limpa, agora=AGORA)

    assert ped["dias"] == [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)]
    assert (primeira.consultas, primeira.leituras, primeira.gravadas) == (3, 12, 12)
    assert _contar(limpa, "cemaden_estacao") == 2
    assert not primeira.falhas

    ped["mm"][date(2026, 11, 4)] = 3.0   # a PED corrigiu a chuva da tarde de 04/11
    segunda = coletor_cemaden.coletar_ultimos(limpa, agora=AGORA)

    assert segunda.gravadas == 2
    guardada = banco.ler_cemaden_chuva(limpa, datetime(2026, 11, 4, tzinfo=timezone.utc),
                                       datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert guardada.loc[guardada["leitura_utc"].dt.hour == 13, "chuva"].tolist() == [3.0, 3.0]


def test_a_estacao_fora_do_cadastro_entra_pelas_leituras_sem_mexer_nas_outras(limpa, ped, capsys):
    ped["codigos"] = [*NO_CADASTRO, "500999901A"]

    coleta = coletor_cemaden.coletar_ultimos(limpa, agora=AGORA)

    assert coleta.estacoes_novas == 1 and coleta.gravadas == 18
    cadastro = banco.ler_cemaden_estacoes(limpa).set_index("codigo")
    assert cadastro.loc["500999901A", "municipio"] == "Bela Vista"
    assert cadastro.loc["500110201A", "codibge"] == 5001102   # o que a leitura não traz fica como a PED disse
    assert "1 estações novas no cadastro" in capsys.readouterr().out


def test_sem_o_cadastro_da_ped_a_chuva_entra_assim_mesmo(limpa, ped, capsys):
    ped["sem_cadastro"] = True

    coleta = coletor_cemaden.coletar_ultimos(limpa, agora=AGORA)

    assert coleta.gravadas == 12 and coleta.estacoes_novas == 2 and not coleta.falhas
    assert "Sem o cadastro da PED" in capsys.readouterr().out


def test_o_dia_que_falha_fica_para_a_proxima_e_os_outros_entram(limpa, ped, capsys):
    ped["falhar"].add(date(2026, 11, 4))

    coleta = coletor_cemaden.coletar_ultimos(limpa, agora=AGORA)

    assert coleta.falhas == ["04/11/2026"] and coleta.gravadas == 8
    assert "1 dias falharam (04/11/2026)" in capsys.readouterr().out


def test_consultas_seguidas_sem_resposta_param_a_coleta(limpa, ped, monkeypatch):
    monkeypatch.setattr(coletor_cemaden, "FALHAS_SEGUIDAS", 2)
    ped["falhar"].update({date(2026, 11, 3), date(2026, 11, 4)})

    coleta = coletor_cemaden.coletar_ultimos(limpa, agora=AGORA)

    assert ped["dias"] == [date(2026, 11, 3), date(2026, 11, 4)]   # o 05 nem é pedido
    assert coleta.interrompida


def test_os_dias_guardados_sao_os_dias_utc_das_leituras(limpa, ped):
    leituras = chuva_do_dia(date(2026, 10, 1)).assign(leitura_utc=[
        pd.Timestamp("2026-09-30 23:59", tz="UTC"), pd.Timestamp("2026-10-01 00:00", tz="UTC"),
        pd.Timestamp("2026-10-03 23:50", tz="UTC"), pd.Timestamp("2026-10-04 00:10", tz="UTC")])
    banco.gravar_cemaden_estacoes(limpa, cadastro_da_ped())
    banco.gravar_cemaden_chuva(limpa, leituras)

    guardados = banco.dias_cemaden_guardados(limpa, date(2026, 10, 1), date(2026, 10, 3))

    assert guardados == {date(2026, 10, 1), date(2026, 10, 3)}


# =====================================================
# O HISTÓRICO
# =====================================================
def test_o_historico_busca_em_lotes_e_retoma_so_o_que_falta(limpa, ped, monkeypatch):
    monkeypatch.setattr(coletor_cemaden, "DIAS_POR_LOTE", 4)
    ped["falhar"].add(date(2026, 10, 5))

    primeira = coletor_cemaden.carregar_historico(limpa, date(2026, 10, 1), date(2026, 10, 10))

    assert len(ped["dias"]) == 10 and primeira.falhas == ["05/10/2026"]
    assert primeira.gravadas == 9 * 4

    ped["dias"].clear()
    ped["falhar"].clear()
    segunda = coletor_cemaden.carregar_historico(limpa, date(2026, 10, 1), date(2026, 10, 10))

    assert ped["dias"] == [date(2026, 10, 5)] and not segunda.falhas


def test_o_historico_nao_pede_o_futuro(limpa, ped):
    hoje = datetime.now(timezone.utc).date()

    coletor_cemaden.carregar_historico(limpa, hoje - timedelta(days=1), hoje + timedelta(days=5))

    assert ped["dias"] == [hoje - timedelta(days=1), hoje]


def test_o_historico_para_quando_a_ped_sai_do_ar(limpa, ped, monkeypatch, capsys):
    monkeypatch.setattr(coletor_cemaden, "DIAS_POR_LOTE", 3)
    monkeypatch.setattr(coletor_cemaden, "FALHAS_SEGUIDAS", 2)
    ped["falhar"].update({date(2026, 10, 4), date(2026, 10, 5)})

    coleta = coletor_cemaden.carregar_historico(limpa, date(2026, 10, 1), date(2026, 10, 10))

    assert ped["dias"][-1] == date(2026, 10, 5) and coleta.interrompida
    assert "a carga para aqui" in capsys.readouterr().out


# =====================================================
# O COLETAR.PY
# =====================================================
def test_o_coletar_sem_a_conta_da_ped_nao_conecta(monkeypatch, capsys):
    monkeypatch.setattr(config, "CEMADEN_EMAIL", "")
    monkeypatch.setattr(banco, "conectar", lambda *args, **kwargs: pytest.fail("não devia conectar"))

    assert coletar.main(["cemaden"]) == 1
    assert "CEMADEN_EMAIL" in capsys.readouterr().out


def test_o_coletar_com_ate_sem_historico_e_recusado():
    with pytest.raises(SystemExit):
        coletar.argumentos(["cemaden", "--ate", "2026-10-01"])


def test_o_coletar_no_banco_de_teste(conexao, limpa, ped, monkeypatch):
    dias = [date(2026, 11, 4), date(2026, 11, 5)]
    monkeypatch.setattr(coletor_cemaden, "ultimos_dias", lambda agora=None, quantos=3: dias)

    assert coletar.main(["cemaden", "--teste"]) == 0
    ped["falhar"].add(date(2026, 11, 5))
    assert coletar.main(["cemaden", "--teste"]) == 1
    assert _contar(limpa, "cemaden_chuva") == 8


def test_o_historico_do_coletar_vai_do_primeiro_ao_ultimo_dia_e_reconecta(ped, monkeypatch, capsys):
    monkeypatch.setattr(coletar, "ESPERA_PARA_RECONECTAR", 0)
    monkeypatch.setattr(banco, "conectar", lambda *args, **kwargs: nullcontext("conexão"))
    pedidas = []

    def carregar(conexao, desde, ate):
        pedidas.append((desde, ate))
        if len(pedidas) == 1:
            raise banco.ErroBanco("não foi possível conectar ao banco (BANCO_GRAVACAO): connection timeout expired")
        return coletor_cemaden.Coleta(consultas=365)

    monkeypatch.setattr(coletor_cemaden, "carregar_historico", carregar)

    assert coletar.main(["cemaden", "--historico", "2015-01-01", "--ate", "31/12/2015"]) == 0
    assert pedidas == [(date(2015, 1, 1), date(2015, 12, 31))] * 2
    assert "Reconectando" in capsys.readouterr().out
