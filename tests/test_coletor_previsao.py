"""O coletor da previsão: as rodadas novas do Open-Meteo no banco, contra o climageo_teste."""
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import coletar
from modulos import banco, coletor_previsao, config, openmeteo

RODADA = datetime(2026, 10, 14, 0, tzinfo=timezone.utc)
RODADA_EC46 = datetime(2026, 10, 13, 0, tzinfo=timezone.utc)
AGORA = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)
ECMWF, GFS, ICON = config.MODELOS_PREVISAO
EC46 = config.MODELO_SEMANAS
BUSCAS_DE_UMA_RODADA = 8   # os três modelos e o EC46, na grade e nas estações
GRADE = pd.DataFrame({"ponto": ["-20.00;-54.00", "-20.50;-54.50"], "latitude": [-20.0, -20.5], "longitude": [-54.0, -54.5]})


def horaria(pontos: pd.DataFrame, modelo: str, rodada: datetime) -> pd.DataFrame:
    horas = pd.date_range(rodada, periods=3, freq="h")
    linhas = [{"modelo": modelo, "rodada_utc": pd.Timestamp(rodada), "ponto": ponto.ponto,
               "latitude": ponto.latitude, "longitude": ponto.longitude, "hora_prevista_utc": hora,
               **{coluna: 1.0 for coluna in openmeteo.VARIAVEIS.values()}}
              for ponto in pontos.itertuples(index=False) for hora in horas]
    return pd.DataFrame(linhas, columns=openmeteo.COLUNAS_HORARIAS)


def semanal(pontos: pd.DataFrame, modelo: str, rodada: datetime) -> pd.DataFrame:
    linhas = [{"modelo": modelo, "rodada_utc": pd.Timestamp(rodada), "ponto": ponto.ponto,
               "latitude": ponto.latitude, "longitude": ponto.longitude, "semana": semana,
               **{coluna: 0.5 for coluna in openmeteo.VARIAVEIS_SEMANAIS.values()}}
              for ponto in pontos.itertuples(index=False) for semana in (date(2026, 10, 19), date(2026, 10, 26))]
    return pd.DataFrame(linhas, columns=openmeteo.COLUNAS_SEMANAIS)


@pytest.fixture
def open_meteo(monkeypatch):
    estado = SimpleNamespace(rodadas={**{modelo: RODADA for modelo in config.MODELOS_PREVISAO}, EC46: RODADA_EC46},
                             buscas=[], esperas=[], passos=[], falhas={}, sem_metadados=set(), chega=None,
                             recuperadas=[], falha_na_recuperacao=None)

    def rodadas(modelos, agora=None):
        for modelo in modelos:
            if modelo in estado.sem_metadados:
                raise openmeteo.ErroOpenMeteo("HTTP 502 (depois de 3 tentativas)")
        return {modelo: estado.rodadas[modelo] for modelo in modelos}

    def buscar(pontos, modelos):
        (modelo,) = modelos
        estado.buscas.append((modelo, list(pontos["ponto"])))
        falha = estado.falhas.get(modelo)
        if isinstance(falha, list) and falha:
            raise falha.pop(0)
        if isinstance(falha, Exception):
            raise falha
        return horaria(pontos, modelo, estado.chega or estado.rodadas[modelo])

    def buscar_semanas(pontos, modelo):
        estado.buscas.append((modelo, list(pontos["ponto"])))
        return semanal(pontos, modelo, estado.rodadas[modelo])

    def buscar_rodada(pontos, modelo, rodada):
        estado.buscas.append((modelo, list(pontos["ponto"])))
        estado.recuperadas.append((modelo, pd.Timestamp(rodada)))
        if estado.falha_na_recuperacao:
            raise estado.falha_na_recuperacao
        return horaria(pontos, modelo, rodada)

    def pontos_da_grade(recorte=config.RECORTE, passo=config.GRADE_PREVISAO):
        estado.passos.append(passo)
        return GRADE.copy()

    monkeypatch.setattr(openmeteo, "rodadas", rodadas)
    monkeypatch.setattr(openmeteo, "buscar", buscar)
    monkeypatch.setattr(openmeteo, "buscar_rodada", buscar_rodada)
    monkeypatch.setattr(openmeteo, "buscar_semanas", buscar_semanas)
    monkeypatch.setattr(openmeteo, "pontos_da_grade", pontos_da_grade)
    monkeypatch.setattr(openmeteo, "_dormir", estado.esperas.append)
    return estado


@pytest.fixture
def cadastro(limpa):
    banco.gravar_estacoes(limpa, pd.DataFrame({
        "codigo": ["A702", "A703", "A799", "A701"],
        "nome": ["CAMPO GRANDE", "PONTA PORA", "FECHADA", "SAO PAULO"],
        "uf": ["MS", "MS", "MS", "SP"],
        "latitude": [-20.45, -22.55, -21.0, -23.5],
        "longitude": [-54.72, -55.72, -55.0, -46.62],
        "situacao": ["Operante", "Pane", None, "Operante"],
    }))
    return limpa


def coletar_no(conexao, agora=AGORA) -> coletor_previsao.Coleta:
    return coletor_previsao.coletar(lambda: nullcontext(conexao), agora)


def modelos_buscados(estado, conjunto_de_pontos=None) -> list[str]:
    return [modelo for modelo, pontos in estado.buscas
            if conjunto_de_pontos is None or pontos == conjunto_de_pontos]


def test_grava_as_rodadas_novas_na_grade_e_nas_estacoes(cadastro, open_meteo):
    coleta = coletar_no(cadastro)
    assert (coleta.buscas, coleta.falhas) == (BUSCAS_DE_UMA_RODADA, [])
    for conjunto in banco.CONJUNTOS:
        assert banco.rodadas_guardadas(cadastro, conjunto) == {modelo: RODADA for modelo in config.MODELOS_PREVISAO}
        assert banco.rodadas_semanais_guardadas(cadastro, conjunto) == {EC46: RODADA_EC46}
    previsao = banco.ler_previsao(cadastro, "estacao", ECMWF)
    assert sorted(previsao["ponto"].unique()) == ["A702", "A703"]
    assert coleta.gravadas == coleta.linhas == 3 * (6 + 6) + (4 + 4)   # 2 pontos × 3 horas; 2 pontos × 2 semanas


def test_a_rodada_que_ja_esta_no_banco_nao_e_buscada_de_novo(cadastro, open_meteo):
    coletar_no(cadastro)
    coleta = coletar_no(cadastro)
    assert (coleta.buscas, coleta.gravadas) == (0, 0)
    assert len(open_meteo.buscas) == BUSCAS_DE_UMA_RODADA


def test_so_as_rodadas_das_00z_e_das_12z(cadastro, open_meteo, capsys):
    open_meteo.rodadas[ECMWF] = RODADA + timedelta(hours=6)
    open_meteo.rodadas[GFS] = RODADA + timedelta(hours=12)
    coletar_no(cadastro)
    assert ECMWF not in modelos_buscados(open_meteo)
    assert banco.rodadas_guardadas(cadastro, "grade") == {GFS: RODADA + timedelta(hours=12), ICON: RODADA}
    assert "Fora das 00Z e das 12Z, as únicas que o coletor guarda: ECMWF 14/10 06Z" in capsys.readouterr().out


def test_as_estacoes_sao_as_de_ms_na_lista_do_inmet(cadastro, open_meteo):
    coletar_no(cadastro)
    assert modelos_buscados(open_meteo, ["A702", "A703"]) == [ECMWF, GFS, ICON, EC46]
    assert banco.pontos_das_estacoes(cadastro, "MS")["ponto"].tolist() == ["A702", "A703"]


def test_a_grade_hora_a_hora_e_de_025_e_a_das_semanas_a_do_painel(cadastro, open_meteo):
    coletar_no(cadastro)
    assert open_meteo.passos == [0.25, config.GRADE_PREVISAO]


def test_um_minuto_entre_uma_busca_e_outra(cadastro, open_meteo):
    coletar_no(cadastro)
    assert open_meteo.esperas == [openmeteo.ESPERA_ENTRE_LOTES] * (BUSCAS_DE_UMA_RODADA - 1)


def test_a_cota_esgotada_para_a_coleta(cadastro, open_meteo):
    open_meteo.falhas[GFS] = openmeteo.CotaEsgotada("cota do Open-Meteo esgotada: Daily API request limit exceeded")
    coleta = coletar_no(cadastro)
    assert modelos_buscados(open_meteo) == [ECMWF, ECMWF, GFS]
    assert (coleta.interrompida, coleta.falhas) == (True, ["GFS 14/10 00Z, grade"])
    assert banco.rodadas_guardadas(cadastro, "grade") == {ECMWF: RODADA}


def test_com_o_minuto_da_cota_gasto_espera_e_tenta_de_novo(cadastro, open_meteo):
    open_meteo.falhas[GFS] = [openmeteo.LimitePorMinuto("cota do Open-Meteo esgotada: Minutely API request limit")]
    coleta = coletar_no(cadastro)
    assert (coleta.buscas, coleta.falhas) == (BUSCAS_DE_UMA_RODADA, [])
    assert modelos_buscados(open_meteo).count(GFS) == 3
    assert open_meteo.esperas == [openmeteo.ESPERA_ENTRE_LOTES] * BUSCAS_DE_UMA_RODADA


def test_se_o_minuto_da_cota_segue_gasto_para_a_coleta(cadastro, open_meteo):
    open_meteo.falhas[GFS] = openmeteo.LimitePorMinuto("cota do Open-Meteo esgotada: Minutely API request limit")
    coleta = coletar_no(cadastro)
    assert modelos_buscados(open_meteo) == [ECMWF, ECMWF, *[GFS] * coletor_previsao.TENTATIVAS_NO_LIMITE_POR_MINUTO]
    assert (coleta.interrompida, coleta.falhas) == (True, ["GFS 14/10 00Z, grade"])


def test_a_falha_de_um_modelo_nao_para_os_outros(cadastro, open_meteo):
    open_meteo.falhas[GFS] = openmeteo.ErroOpenMeteo("HTTP 502 (depois de 3 tentativas)")
    coleta = coletar_no(cadastro)
    assert coleta.falhas == ["GFS 14/10 00Z, grade", "GFS 14/10 00Z, estações"]
    assert set(banco.rodadas_guardadas(cadastro, "estacao")) == {ECMWF, ICON}
    # A próxima execução busca só o que falhou
    del open_meteo.falhas[GFS]
    open_meteo.buscas.clear()
    assert coletar_no(cadastro).falhas == []
    assert modelos_buscados(open_meteo) == [GFS, GFS]


def test_sem_os_metadados_de_um_modelo_os_outros_seguem(cadastro, open_meteo):
    open_meteo.sem_metadados.add(ICON)
    coleta = coletar_no(cadastro)
    assert coleta.falhas == ["ICON"]
    assert ICON not in modelos_buscados(open_meteo)
    assert set(banco.rodadas_guardadas(cadastro, "grade")) == {ECMWF, GFS}


def test_a_rodada_que_chega_no_meio_da_busca_fica_para_a_proxima(cadastro, open_meteo, capsys):
    open_meteo.chega = RODADA + timedelta(hours=6)
    coleta = coletar_no(cadastro)
    assert banco.rodadas_guardadas(cadastro, "grade") == {}
    assert coleta.falhas == []
    assert "no meio da busca" in capsys.readouterr().out


def test_apaga_a_grade_com_mais_de_21_dias(cadastro, open_meteo):
    velha = AGORA - timedelta(days=30)
    banco.gravar_previsao(cadastro, horaria(openmeteo.pontos_da_grade(), ECMWF, velha), "grade")
    banco.gravar_previsao(cadastro, horaria(openmeteo.pontos_da_grade(), ECMWF, velha), "estacao")
    coleta = coletar_no(cadastro)
    assert coleta.apagadas == [banco.nome_da_particao("previsao_horaria_grade", velha.date())]
    assert banco.ler_previsao(cadastro, "estacao", ECMWF, velha).shape[0] == 6   # as estações ficam para sempre


def test_sem_estacoes_no_cadastro_busca_so_a_grade(limpa, open_meteo, capsys):
    coleta = coletar_no(limpa)
    assert coleta.buscas == BUSCAS_DE_UMA_RODADA // 2
    assert "não tem estações de MS" in capsys.readouterr().out


def test_sem_nada_novo_o_log_fica_curto(cadastro, open_meteo, monkeypatch, capsys):
    monkeypatch.setattr(coletor_previsao, "DIAS_DE_RECUPERACAO", 0)
    coletar_no(cadastro)
    capsys.readouterr()
    coletar_no(cadastro)
    saida = capsys.readouterr().out.splitlines()
    assert len(saida) == 3
    assert "✔️ Já no banco: ECMWF 14/10 00Z, GFS 14/10 00Z, ICON 14/10 00Z, EC46 13/10 00Z" in saida[1]
    assert "Nada novo para buscar" in saida[2]


# =====================================================
# A RECUPERAÇÃO
# =====================================================
# Nestes testes, a mais recente dos três modelos é a de 14/10 12Z, e a recuperação olha 2 dias para
# trás: as esperadas de cada modelo são as de 13/10 00Z, 13/10 12Z e 14/10 00Z
NOVA = RODADA + timedelta(hours=12)
HORA_DE_RECUPERAR = datetime(2026, 10, 15, 0, tzinfo=timezone.utc)
ESPERADAS = [RODADA - timedelta(hours=24), RODADA - timedelta(hours=12), RODADA]


@pytest.fixture
def recuperacao(cadastro, open_meteo, monkeypatch):
    monkeypatch.setattr(coletor_previsao, "DIAS_DE_RECUPERACAO", 2)
    for modelo in config.MODELOS_PREVISAO:
        open_meteo.rodadas[modelo] = NOVA
    return cadastro


def guardar(conexao, menos=()):
    estacoes = banco.pontos_das_estacoes(conexao, "MS")
    for modelo in config.MODELOS_PREVISAO:
        for rodada in ESPERADAS:
            if (modelo, rodada) not in menos:
                banco.gravar_previsao(conexao, horaria(GRADE, modelo, rodada), "grade")
                banco.gravar_previsao(conexao, horaria(estacoes, modelo, rodada), "estacao")


def test_as_esperadas_sao_as_das_00z_e_12z_de_antes_da_mais_recente():
    ultimas = {ECMWF: pd.Timestamp(NOVA), GFS: pd.Timestamp("2026-10-15 06:00", tz="UTC")}   # o ICON sem metadados
    por_modelo = {}
    for modelo, rodada in coletor_previsao.rodadas_esperadas(ultimas, datetime(2026, 10, 15, 9, tzinfo=timezone.utc)):
        por_modelo.setdefault(modelo, []).append(rodada)

    assert set(por_modelo) == {ECMWF, GFS}
    assert por_modelo[ECMWF][0] == por_modelo[GFS][0] == pd.Timestamp("2026-10-08 12:00", tz="UTC")   # 7 dias
    assert por_modelo[ECMWF][-1] == pd.Timestamp(RODADA)   # a de 14/10 12Z é da busca normal
    assert por_modelo[GFS][-1] == pd.Timestamp("2026-10-15 00:00", tz="UTC")   # a mais recente é a das 06Z
    assert all(rodada.hour in (0, 12) for rodadas in por_modelo.values() for rodada in rodadas)


def test_recupera_a_rodada_que_passou_sem_entrar_no_banco(recuperacao, open_meteo, capsys):
    guardar(recuperacao, menos={(ECMWF, ESPERADAS[1])})

    coleta = coletar_no(recuperacao, HORA_DE_RECUPERAR)

    assert open_meteo.recuperadas == [(ECMWF, pd.Timestamp(ESPERADAS[1]))] * 2   # a grade e as estações
    assert modelos_buscados(open_meteo)[BUSCAS_DE_UMA_RODADA:] == [ECMWF, ECMWF]   # depois das novas
    assert (coleta.buscas, coleta.falhas, coleta.faltam) == (BUSCAS_DE_UMA_RODADA + 2, [], [])
    for conjunto in banco.CONJUNTOS:
        assert banco.rodadas_ausentes(recuperacao, conjunto, [(ECMWF, ESPERADAS[1])]) == []
    assert "ECMWF 13/10 12Z, grade (recuperação)" in capsys.readouterr().out


def test_uma_rodada_por_vez_a_mais_nova_primeiro(recuperacao, open_meteo):
    coleta = coletar_no(recuperacao, HORA_DE_RECUPERAR)   # nenhuma das esperadas no banco

    assert open_meteo.recuperadas == [(ECMWF, pd.Timestamp(RODADA))] * 2
    assert len(coleta.faltam) == 3 * len(ESPERADAS) - 1
    assert coleta.faltam[0] == "ECMWF 13/10 00Z" and "ECMWF 14/10 00Z" not in coleta.faltam


def test_fora_da_hora_de_recuperar_so_avisa_o_que_falta(recuperacao, open_meteo, capsys):
    guardar(recuperacao, menos={(GFS, ESPERADAS[2])})

    coleta = coletar_no(recuperacao, HORA_DE_RECUPERAR + timedelta(hours=1))

    assert open_meteo.recuperadas == []
    assert coleta.faltam == ["GFS 14/10 00Z"]
    assert "Rodadas dos últimos 2 dias que faltam no banco: GFS 14/10 00Z" in capsys.readouterr().out


def test_recupera_so_o_conjunto_que_falta(recuperacao, open_meteo):
    guardar(recuperacao, menos={(ICON, ESPERADAS[2])})
    banco.gravar_previsao(recuperacao, horaria(GRADE, ICON, ESPERADAS[2]), "grade")

    assert coletar_no(recuperacao, HORA_DE_RECUPERAR + timedelta(hours=1)).faltam == ["ICON 14/10 00Z (estações)"]
    assert coletar_no(recuperacao, HORA_DE_RECUPERAR).faltam == []
    assert open_meteo.recuperadas == [(ICON, pd.Timestamp(ESPERADAS[2]))]


def test_a_recuperacao_que_falha_segue_na_lista(recuperacao, open_meteo):
    guardar(recuperacao, menos={(ECMWF, ESPERADAS[1])})
    open_meteo.falha_na_recuperacao = openmeteo.ErroOpenMeteo(
        "pedido recusado (HTTP 400): The requested model run is not available")

    coleta = coletar_no(recuperacao, HORA_DE_RECUPERAR)

    assert coleta.falhas == ["ECMWF 13/10 12Z, grade (recuperação)", "ECMWF 13/10 12Z, estações (recuperação)"]
    assert coleta.faltam == ["ECMWF 13/10 12Z"]


def test_com_a_cota_esgotada_nas_novas_nao_recupera(recuperacao, open_meteo):
    open_meteo.falhas[GFS] = openmeteo.CotaEsgotada("cota do Open-Meteo esgotada: Daily API request limit exceeded")

    coleta = coletar_no(recuperacao, HORA_DE_RECUPERAR)

    assert coleta.interrompida and open_meteo.recuperadas == []


def test_o_coletar_previsao_no_banco_de_teste(conexao, cadastro, open_meteo):
    assert coletar.main(["previsao", "--teste"]) == 0
    assert set(banco.rodadas_guardadas(cadastro, "estacao")) == set(config.MODELOS_PREVISAO)
    open_meteo.rodadas[ICON] = RODADA + timedelta(hours=12)
    open_meteo.falhas[ICON] = openmeteo.ErroOpenMeteo("HTTP 502 (depois de 3 tentativas)")
    assert coletar.main(["previsao", "--teste"]) == 1
