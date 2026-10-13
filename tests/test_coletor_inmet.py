"""O coletor do INMET: da API para o banco, de hora em hora, uma vez por dia e o histórico.

O INMET é simulado (`inmet.baixar_hora`, `lista_do_inmet` e `baixar_dados_estacao` trocados por
dados do teste). Os testes que gravam usam o banco de teste (as fixtures `conexao` e `limpa`, no
conftest.py) e são pulados sem ele.
"""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import psycopg
import pytest

import coletar
from modulos import banco, coletor_inmet, config, inmet

AGORA = datetime(2026, 10, 9, 14, 20, tzinfo=timezone.utc)

LISTA = pd.DataFrame([
    {"CD_ESTACAO": "A702", "DC_NOME": "CAMPO GRANDE", "SG_ESTADO": "MS", "VL_LATITUDE": "-20.44",
     "VL_LONGITUDE": "-54.72", "VL_ALTITUDE": "530", "CD_SITUACAO": "Operante",
     "DT_INICIO_OPERACAO": "2001-09-07T21:00:00.000-03:00"},
    {"CD_ESTACAO": "A721", "DC_NOME": "DOURADOS", "SG_ESTADO": "MS", "VL_LATITUDE": "-22.19",
     "VL_LONGITUDE": "-54.91", "VL_ALTITUDE": "469", "CD_SITUACAO": "Pane", "DT_INICIO_OPERACAO": None},
])


def hora_do_inmet(momento: datetime, tem_ins: float = 30.0) -> pd.DataFrame:
    """Uma hora como `inmet.baixar_hora` devolve: duas da lista, uma que fechou (A004) e uma vazia."""
    linhas = [("A702", "CAMPO GRANDE", "MS", -20.44, -54.72, tem_ins, 1234.567),
              ("A721", "DOURADOS", "MS", -22.19, -54.91, tem_ins - 2, 1000.05),
              ("A004", "NIQUELANDIA", "GO", -14.47, -48.46, tem_ins + 1, np.nan),
              ("A999", "SEM NADA", "GO", -15.0, -48.0, np.nan, np.nan)]
    dados = pd.DataFrame(linhas, columns=["CD_ESTACAO", "DC_NOME", "UF", "VL_LATITUDE", "VL_LONGITUDE",
                                          "TEM_INS", "RAD_GLO"])
    for coluna in banco.COLUNAS_INMET:
        if coluna.upper() not in dados:
            dados[coluna.upper()] = np.where(dados["TEM_INS"].notna(), 1.0, np.nan)
    dados["dt_utc"] = pd.Timestamp(momento).tz_convert("UTC")
    return dados


@pytest.fixture
def inmet_simulado(monkeypatch):
    """O INMET do teste: anota as horas e as estações pedidas, e falha nas que o teste mandar."""
    pedidas = {"horas": [], "estacoes": [], "falhar": set(), "tem_ins": 30.0}

    def baixar_hora(momento):
        pedidas["horas"].append(momento)
        if momento in pedidas["falhar"]:
            raise inmet.ErroINMET("conexão encerrada")
        return hora_do_inmet(momento, pedidas["tem_ins"])

    def baixar_dados_estacao(codigo, inicio, fim, fuso=config.FUSO_MS):
        pedidas["estacoes"].append(codigo)
        if codigo in pedidas["falhar"]:
            raise inmet.ErroINMET("conexão encerrada")
        horas = pd.date_range(pd.Timestamp(inicio).floor("D"), pd.Timestamp(fim).floor("D") + pd.Timedelta(days=1),
                              freq="h", inclusive="left")
        return pd.DataFrame({"CD_ESTACAO": codigo, "dt_utc": horas, "TEM_INS": 99.0, "TEM_SEN": 31.0,
                             "TEN_BAT": 12.6, "TEM_CPU": 40.0})

    monkeypatch.setattr(inmet, "baixar_hora", baixar_hora)
    monkeypatch.setattr(inmet, "lista_do_inmet", lambda: LISTA.copy())
    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar_dados_estacao)
    return pedidas


# =====================================================
# DA API PARA O BANCO
# =====================================================
@pytest.mark.parametrize("da_hora, da_estacao", [
    (3523.619, 3523.6), (1724.528, 1724.5), (2494.277, 2494.3),   # pares vistos em 08/10/2026
    (1724.55, 1724.6), (135.25, 135.3),                           # o meio sobe; o round do Python o levaria ao par
    (-3.54, -3.5), (0.0, 0.0),
])
def test_a_radiacao_fica_com_uma_casa_como_na_consulta_por_estacao(da_hora, da_estacao):
    assert coletor_inmet._radiacao_com_uma_casa(pd.Series([da_hora]))[0] == da_estacao


def test_as_leituras_vao_com_os_nomes_do_banco_e_sem_as_linhas_vazias():
    leituras = coletor_inmet.leituras_para_o_banco(hora_do_inmet(AGORA))

    assert list(leituras.columns) == ["estacao", "hora_utc", *banco.COLUNAS_INMET]
    assert leituras["estacao"].tolist() == ["A702", "A721", "A004"]      # a A999 veio toda vazia
    assert leituras["rad_glo"].tolist()[:2] == [1234.6, 1000.1]


def test_os_extras_levam_so_as_tres_medidas_da_consulta_por_estacao():
    dados = pd.DataFrame({"CD_ESTACAO": "A702", "dt_utc": [pd.Timestamp(AGORA)], "TEM_INS": 30.0,
                          "TEM_SEN": 31.0, "TEN_BAT": np.nan, "TEM_CPU": 40.0})

    extras = coletor_inmet.leituras_para_o_banco(dados, coletor_inmet.EXTRAS)

    assert list(extras.columns) == ["estacao", "hora_utc", "tem_sen", "ten_bat", "tem_cpu"]


def test_o_cadastro_da_lista():
    cadastro = coletor_inmet.estacoes_da_lista(LISTA)

    assert cadastro["altitude"].tolist() == [530.0, 469.0]
    assert cadastro["inicio_operacao"].iloc[0] == pd.Timestamp("2001-09-08 00:00", tz="UTC")
    assert pd.isna(cadastro["inicio_operacao"].iloc[1])


def test_as_ultimas_horas_vao_ate_a_hora_corrente():
    horas = coletor_inmet.ultimas_horas(AGORA, 48)

    assert len(horas) == 48
    assert horas[-1] == datetime(2026, 10, 9, 14, tzinfo=timezone.utc)
    assert horas[0] == datetime(2026, 10, 7, 15, tzinfo=timezone.utc)


# =====================================================
# GRAVANDO
# =====================================================
def test_a_coleta_de_hora_em_hora_grava_o_cadastro_e_os_3_dias(limpa, inmet_simulado):
    coleta = coletor_inmet.coletar_ultimas(limpa, agora=AGORA)

    assert coletor_inmet.HORAS_REBUSCADAS == 72
    assert len(inmet_simulado["horas"]) == 72 and coleta.falhas == []
    assert inmet_simulado["horas"][0] == datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    assert coleta.leituras == coleta.gravadas == 72 * 3
    assert coleta.estacoes_novas == 1                     # a A004, que não está na lista
    cadastro = dict(limpa.execute("SELECT codigo, situacao FROM estacao").fetchall())
    assert cadastro == {"A702": "Operante", "A721": "Pane", "A004": None}


def test_rebuscar_as_horas_so_reescreve_o_que_o_inmet_mudou(limpa, inmet_simulado):
    coletor_inmet.coletar_ultimas(limpa, agora=AGORA)
    assert coletor_inmet.coletar_ultimas(limpa, agora=AGORA).gravadas == 0

    # Uma hora depois: a hora nova entra, e a mais antiga sai da janela sem ser apagada
    coleta = coletor_inmet.coletar_ultimas(limpa, agora=AGORA + timedelta(hours=1))

    assert coleta.gravadas == 3
    assert limpa.execute("SELECT count(*) FROM inmet_horaria").fetchone()[0] == 73 * 3


def test_a_hora_que_falha_fica_para_a_proxima(limpa, inmet_simulado):
    falha = datetime(2026, 10, 9, 3, tzinfo=timezone.utc)
    inmet_simulado["falhar"].add(falha)

    coleta = coletor_inmet.coletar_ultimas(limpa, agora=AGORA)

    assert coleta.falhas == ["09/10/2026 03:00"]
    assert coleta.gravadas == 71 * 3
    inmet_simulado["falhar"].clear()
    assert coletor_inmet.coletar_ultimas(limpa, agora=AGORA).gravadas == 3


def test_sem_a_lista_a_coleta_segue(limpa, inmet_simulado, monkeypatch, capsys):
    def falhar():
        raise inmet.ErroINMET("HTTP 503")

    monkeypatch.setattr(inmet, "lista_do_inmet", falhar)

    coleta = coletor_inmet.coletar_ultimas(limpa, agora=AGORA, quantas=2)

    assert coleta.gravadas == 6 and coleta.estacoes_novas == 3
    assert "Sem a lista" in capsys.readouterr().out


def test_a_estacao_sem_como_cadastrar_fica_de_fora_e_nao_derruba_as_outras(limpa, inmet_simulado, monkeypatch,
                                                                            capsys):
    """Fora da lista e sem UF na consulta por hora: a leitura dela quebraria a chave do cadastro, e
    a gravação das outras cairia junto, toda hora, até o INMET mudar."""
    simulada = inmet.baixar_hora

    def com_uma_sem_uf(momento):
        dados = simulada(momento)
        return pd.concat([dados, dados.iloc[[0]].assign(CD_ESTACAO="A555", UF=None)], ignore_index=True)

    monkeypatch.setattr(inmet, "baixar_hora", com_uma_sem_uf)

    coleta = coletor_inmet.coletar_ultimas(limpa, agora=AGORA, quantas=2)

    assert coleta.gravadas == 6
    assert "ficaram de fora: A555" in capsys.readouterr().out


def test_os_extras_vem_por_estacao_e_nao_apagam_a_consulta_por_hora(limpa, inmet_simulado):
    coletor_inmet.coletar_ultimas(limpa, agora=AGORA, quantas=2)

    coleta = coletor_inmet.coletar_extras(limpa, agora=AGORA)

    assert sorted(inmet_simulado["estacoes"]) == ["A004", "A702", "A721"]   # só as que têm leitura
    assert coleta.gravadas > 0
    linha = limpa.execute("SELECT tem_ins, ten_bat FROM inmet_horaria WHERE estacao = 'A702' "
                          "AND hora_utc = '2026-10-09 14:00+00'").fetchone()
    assert linha == pytest.approx((30.0, 12.6))     # a temperatura é a da consulta por hora, e não 99


def test_os_extras_gravam_em_lotes(limpa, inmet_simulado, monkeypatch):
    """Uma conexão parada por 10 minutos o servidor derruba, e as ~500 estações levam uns 9."""
    coletor_inmet.coletar_ultimas(limpa, agora=AGORA, quantas=2)
    gravacoes = []
    gravar = banco.gravar_extras_inmet
    monkeypatch.setattr(banco, "gravar_extras_inmet", lambda conexao, extras: gravacoes.append(len(extras))
                        or gravar(conexao, extras))
    monkeypatch.setattr(coletor_inmet, "ESTACOES_POR_LOTE", 2)
    inmet_simulado["falhar"].add("A702")

    coleta = coletor_inmet.coletar_extras(limpa, agora=AGORA)

    assert len(gravacoes) == 2                           # A004 (a A702 falhou) num lote, a A721 no outro
    assert coleta.falhas == ["A702"] and coleta.gravadas == sum(gravacoes)


def test_o_coletor_faz_no_maximo_uma_consulta_por_segundo(monkeypatch):
    relogio = [100.0]
    esperas = []
    monkeypatch.setattr(config, "INTERVALO_DO_COLETOR", 1.0)
    monkeypatch.setattr(coletor_inmet.time, "monotonic", lambda: relogio[0])

    def dormir(segundos):
        esperas.append(round(segundos, 3))
        relogio[0] += segundos

    monkeypatch.setattr(coletor_inmet.time, "sleep", dormir)
    ritmo = coletor_inmet._Ritmo()

    ritmo.esperar()               # a primeira não espera
    relogio[0] += 0.3             # a consulta levou 0,3 s
    ritmo.esperar()
    relogio[0] += 1.5             # esta levou 1,5 s: a seguinte sai na hora
    ritmo.esperar()

    assert esperas == [0.7]


def test_o_historico_retoma_de_onde_parou(limpa, inmet_simulado):
    desde = datetime(2026, 9, 30, 22, tzinfo=timezone.utc)
    ate = datetime(2026, 10, 2, 1, tzinfo=timezone.utc)
    inmet_simulado["falhar"].add(datetime(2026, 10, 1, 5, tzinfo=timezone.utc))

    primeira = coletor_inmet.carregar_historico(limpa, desde, ate)

    assert primeira.consultas == 28 and primeira.falhas == ["01/10/2026 05:00"]
    inmet_simulado["falhar"].clear()
    inmet_simulado["horas"].clear()
    segunda = coletor_inmet.carregar_historico(limpa, desde, ate)
    assert inmet_simulado["horas"] == [datetime(2026, 10, 1, 5, tzinfo=timezone.utc)]
    assert segunda.gravadas == 3
    # Duas partições de mês, criadas pelo caminho
    assert list(banco.particoes(limpa, "inmet_horaria")) == ["inmet_horaria_202609", "inmet_horaria_202610"]


def test_o_historico_nao_pede_horas_do_futuro(limpa, inmet_simulado):
    agora = coletor_inmet._hora_cheia(None)

    coletor_inmet.carregar_historico(limpa, agora - timedelta(hours=2), agora + timedelta(days=2))

    assert max(inmet_simulado["horas"]) == agora


def test_o_historico_para_quando_o_inmet_sai_do_ar(limpa, inmet_simulado, monkeypatch, capsys):
    def fora_do_ar(momento):
        inmet_simulado["horas"].append(momento)
        raise inmet.ErroINMET("conexão recusada")

    monkeypatch.setattr(inmet, "baixar_hora", fora_do_ar)

    coleta = coletor_inmet.carregar_historico(limpa, datetime(2026, 10, 1, tzinfo=timezone.utc),
                                              datetime(2026, 10, 5, tzinfo=timezone.utc))

    assert len(inmet_simulado["horas"]) == 24          # um dia inteiro sem resposta, e para
    assert len(coleta.falhas) == 24
    assert "a carga para aqui" in capsys.readouterr().out


# =====================================================
# O COLETAR.PY
# =====================================================
def test_o_coletar_sem_token_nao_conecta(monkeypatch, capsys):
    monkeypatch.setattr(config, "TOKEN_INMET", config.TOKEN_EXEMPLO)
    monkeypatch.setattr(banco, "conectar", lambda *args, **kwargs: pytest.fail("não devia conectar"))

    assert coletar.main(["inmet"]) == 1
    assert "token do INMET" in capsys.readouterr().out


def test_o_coletar_sem_banco_sai_com_erro(monkeypatch, capsys):
    monkeypatch.setattr(config, "TOKEN_INMET", "token-de-teste")
    monkeypatch.delenv("BANCO_GRAVACAO", raising=False)

    assert coletar.main(["inmet"]) == 1
    assert "BANCO_GRAVACAO" in capsys.readouterr().out


def test_o_coletar_com_ate_sem_historico_e_recusado():
    with pytest.raises(SystemExit):
        coletar.argumentos(["inmet", "--ate", "2026-10-01"])


def test_o_coletar_no_banco_de_teste(conexao, limpa, inmet_simulado, monkeypatch):
    """O caminho inteiro, como o Agendador o chama: sai 0 quando tudo deu certo e 1 quando algo falhou."""
    monkeypatch.setattr(config, "TOKEN_INMET", "token-de-teste")
    monkeypatch.setattr(coletor_inmet, "HORAS_REBUSCADAS", 3)
    monkeypatch.setattr(coletor_inmet, "ultimas_horas",
                        lambda agora=None, quantas=3: [datetime(2026, 10, 9, h, tzinfo=timezone.utc) for h in (12, 13, 14)])

    assert coletar.main(["inmet", "--teste"]) == 0
    inmet_simulado["falhar"].add(datetime(2026, 10, 9, 13, tzinfo=timezone.utc))
    assert coletar.main(["inmet", "--teste"]) == 1
    assert limpa.execute("SELECT count(*) FROM inmet_horaria").fetchone()[0] == 9


@pytest.fixture
def historico_simulado(monkeypatch):
    """O coletar.py sem banco: a conexão é de mentira, e a carga anota com que datas foi chamada."""
    monkeypatch.setattr(config, "TOKEN_INMET", "token-de-teste")
    monkeypatch.setattr(coletar, "ESPERA_PARA_RECONECTAR", 0)
    monkeypatch.setattr(banco, "conectar", lambda *args, **kwargs: nullcontext("conexão"))
    chamadas = {"datas": [], "quedas": 0}

    def carregar(conexao, desde, ate):
        chamadas["datas"].append((desde, ate))
        if len(chamadas["datas"]) <= chamadas["quedas"]:
            raise psycopg.OperationalError("terminating connection due to idle-session timeout")
        return coletor_inmet.Coleta(consultas=24)

    monkeypatch.setattr(coletor_inmet, "carregar_historico", carregar)
    return chamadas


def test_o_historico_reconecta_e_retoma_quando_a_conexao_cai(historico_simulado, capsys):
    """São ~22 horas sem ninguém olhando: o servidor derruba a conexão parada por 10 minutos."""
    historico_simulado["quedas"] = 1

    assert coletar.main(["inmet", "--historico", "2018-01-01", "--ate", "2018-01-01"]) == 0

    assert len(historico_simulado["datas"]) == 2
    assert "Reconectando" in capsys.readouterr().out


def test_o_historico_desiste_depois_de_tantas_quedas(historico_simulado, monkeypatch, capsys):
    monkeypatch.setattr(coletar, "RECONEXOES_NO_HISTORICO", 2)
    historico_simulado["quedas"] = 99

    assert coletar.main(["inmet", "--historico", "2018-01-01"]) == 1

    assert len(historico_simulado["datas"]) == 3
    assert "❌ Banco" in capsys.readouterr().out


def test_o_ate_inclui_o_ultimo_dia_inteiro(historico_simulado):
    """--ate 2018-12-31 é até o fim do dia 31: a 00:00 de 01/01 fecha a última hora dele."""
    coletar.main(["inmet", "--historico", "2018-01-01", "--ate", "2018-12-31"])

    assert historico_simulado["datas"] == [(datetime(2018, 1, 1, tzinfo=timezone.utc),
                                            datetime(2019, 1, 1, tzinfo=timezone.utc))]


def test_o_ctrl_c_diz_como_retomar(monkeypatch, capsys):
    def interromper(opcoes):
        raise KeyboardInterrupt

    monkeypatch.setattr(coletar, "executar", interromper)

    assert coletar.main(["inmet"]) == 130
    assert "retoma de onde parou" in capsys.readouterr().out
