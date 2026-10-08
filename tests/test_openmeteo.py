"""Open-Meteo: os pontos, a cota, a rodada, a previsão hora a hora e o dia tirado das horas."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from modulos import config, openmeteo

HORAS_ICON = 180  # o ICON para no dia 7,5


class Resposta:
    def __init__(self, status: int, corpo):
        self.status_code = status
        self._corpo = corpo
        self.text = str(corpo)

    def json(self):
        if isinstance(self._corpo, Exception):
            raise self._corpo
        return self._corpo


def corpo_da_previsao(dados: dict) -> list:
    """O que o Open-Meteo devolve para um pedido de vários pontos e vários modelos."""
    latitudes = [float(v) for v in dados["latitude"].split(",")]
    modelos = dados["models"].split(",")
    horas = int(dados["forecast_days"]) * 24
    inicio = int(datetime(2026, 10, 7, tzinfo=timezone.utc).timestamp())
    tempos = [inicio + 3600 * h for h in range(horas)]
    locais = []
    for latitude in latitudes:
        horario = {"time": tempos}
        for modelo in modelos:
            alcance = HORAS_ICON if modelo == "icon_seamless" else horas
            for nome in openmeteo.VARIAVEIS:
                valores = [round(-latitude + h % 24, 1) for h in range(horas)]
                horario[f"{nome}_{modelo}"] = [v if h < alcance else None for h, v in enumerate(valores)]
        locais.append({"hourly": horario})
    return locais


SEGUNDAS = ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"]


def corpo_das_semanas(dados: dict) -> list:
    """O que a API sazonal devolve: uma lista por variável, com a última semana vazia (passa do dia 46)."""
    latitudes = [float(v) for v in dados["latitude"].split(",")]
    locais = []
    for latitude in latitudes:
        semanal = {"time": SEGUNDAS}
        for indice, nome in enumerate(dados["weekly"].split(",")):
            semanal[nome] = [round(-latitude / 10 + indice + semana, 1) for semana in range(6)] + [None]
        locais.append({"weekly": semanal})
    return locais


@pytest.fixture
def open_meteo(monkeypatch):
    """Troca o Open-Meteo por respostas sintéticas e anota cada pedido."""
    pedidos = []

    def falso(metodo, url, timeout=None, data=None, **kwargs):
        pedidos.append((metodo, url, data))
        if url.endswith("meta.json"):
            return Resposta(200, {"last_run_initialisation_time": 1791374400,     # 07/10 12:00 UTC
                                  "last_run_availability_time": 1791381600,       # 07/10 14:00 UTC
                                  "update_interval_seconds": 21600})
        if url == config.URL_OPENMETEO_SAZONAL:
            return Resposta(200, corpo_das_semanas(data))
        return Resposta(200, corpo_da_previsao(data))

    monkeypatch.setattr(openmeteo.requests, "request", falso)
    esperas = []
    monkeypatch.setattr(openmeteo, "_dormir", esperas.append)
    return pedidos, esperas


def pontos(quantos: int) -> pd.DataFrame:
    return pd.DataFrame({"ponto": [f"p{i}" for i in range(quantos)],
                         "latitude": [-20.0 - i * 0.01 for i in range(quantos)],
                         "longitude": [-54.0] * quantos})


# =====================================================
# A COTA E OS PONTOS
# =====================================================
def test_o_custo_segue_a_regra_medida():
    assert openmeteo.custo(178) == pytest.approx(178 * 2.1)          # 7 variáveis × 3 modelos
    assert openmeteo.custo(178, dias=15) == pytest.approx(178 * 2.1 * 15 / 14)
    assert openmeteo.custo(1, modelos=1) == 1                        # até 10 variáveis, até 14 dias


@pytest.mark.parametrize("passo, esperados", [(0.5, 178), (0.25, 607)])
def test_a_grade_de_ms_tem_os_pontos_medidos(passo, esperados):
    """Os números do escopo: 178 pontos a 0,5° e 607 a 0,25°, com uma fileira além da divisa."""
    grade = openmeteo.pontos_da_grade(config.recorte_de("MS"), passo)

    assert len(grade) == esperados
    assert grade["ponto"].is_unique
    assert np.allclose(grade["latitude"] / passo, np.round(grade["latitude"] / passo))
    assert np.allclose(grade["longitude"] / passo, np.round(grade["longitude"] / passo))


def test_as_estacoes_viram_pontos_pelo_codigo_do_inmet():
    estacoes = pd.DataFrame({"CD_ESTACAO": ["A702"], "VL_LATITUDE": ["-20.45"], "VL_LONGITUDE": ["-54.62"]})

    assert openmeteo.pontos_das_estacoes(estacoes).to_dict("records") == [
        {"ponto": "A702", "latitude": -20.45, "longitude": -54.62}]


# =====================================================
# A BUSCA
# =====================================================
def test_uma_linha_por_modelo_ponto_e_hora_com_a_rodada(open_meteo):
    pedidos, _ = open_meteo

    tabela = openmeteo.buscar(pontos(2))

    assert list(tabela.columns) == openmeteo.COLUNAS_HORARIAS
    contagem = tabela.groupby("modelo").size().to_dict()
    assert contagem == {"ecmwf_ifs025": 2 * 336, "gfs_seamless": 2 * 336, "icon_seamless": 2 * HORAS_ICON}
    assert (tabela["rodada_utc"] == datetime(2026, 10, 7, 12, tzinfo=timezone.utc)).all()
    assert str(tabela["hora_prevista_utc"].dt.tz) == "UTC"
    assert not tabela[list(openmeteo.VARIAVEIS.values())].isna().all(axis=1).any()


def test_o_pedido_leva_as_variaveis_os_modelos_e_as_unidades(open_meteo):
    pedidos, _ = open_meteo

    openmeteo.previsao_horaria(pontos(1))

    (_, url, dados), = [pedido for pedido in pedidos if not pedido[1].endswith("meta.json")]
    assert url == config.URL_OPENMETEO
    assert dados["hourly"].split(",") == list(openmeteo.VARIAVEIS)
    assert dados["models"].split(",") == list(config.MODELOS_PREVISAO)
    assert (dados["forecast_days"], dados["timezone"], dados["timeformat"]) == (14, "GMT", "unixtime")
    assert (dados["wind_speed_unit"], dados["precipitation_unit"]) == ("kmh", "mm")


def test_pedidos_grandes_vao_em_lotes_que_cabem_no_minuto(open_meteo):
    """607 pontos a 2,1 por ponto não cabem em 600 por minuto: três lotes, um minuto entre eles."""
    pedidos, esperas = open_meteo

    tabela = openmeteo.previsao_horaria(pontos(607))

    lotes = [len(dados["latitude"].split(",")) for _, url, dados in pedidos if not url.endswith("meta.json")]
    assert sum(lotes) == 607 and len(lotes) == 3
    assert all(openmeteo.custo(tamanho) <= config.OPENMETEO_POR_MINUTO for tamanho in lotes)
    assert esperas == [openmeteo.ESPERA_ENTRE_LOTES] * 2
    assert tabela["ponto"].nunique() == 607


def test_a_rodada_e_a_anterior_ate_completar_a_margem(open_meteo):
    """Disponível às 14:00 UTC: até 14:10 o que está servindo ainda é a rodada das 06."""
    antes = openmeteo.rodadas(("ecmwf_ifs025",), agora=datetime(2026, 10, 7, 14, 5, tzinfo=timezone.utc))
    depois = openmeteo.rodadas(("ecmwf_ifs025",), agora=datetime(2026, 10, 7, 14, 11, tzinfo=timezone.utc))

    assert antes["ecmwf_ifs025"] == datetime(2026, 10, 7, 6, tzinfo=timezone.utc)
    assert depois["ecmwf_ifs025"] == datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def test_se_a_rodada_muda_no_meio_busca_de_novo(open_meteo, monkeypatch):
    """Parte dos números da rodada velha e parte da nova não pode levar o carimbo de nenhuma."""
    pedidos, _ = open_meteo
    velha, nova = (datetime(2026, 10, 7, hora, tzinfo=timezone.utc) for hora in (6, 12))
    sequencia = iter([velha, nova, nova, nova])
    monkeypatch.setattr(openmeteo, "rodadas",
                        lambda modelos=config.MODELOS_PREVISAO, agora=None: dict.fromkeys(modelos, next(sequencia)))

    tabela = openmeteo.buscar(pontos(1))

    assert (tabela["rodada_utc"] == nova).all()
    assert len([p for p in pedidos if not p[1].endswith("meta.json")]) == 2


def test_se_a_rodada_muda_duas_vezes_desiste(open_meteo, monkeypatch):
    horas = iter(range(6, 24))
    monkeypatch.setattr(openmeteo, "rodadas", lambda modelos=config.MODELOS_PREVISAO, agora=None:
                        dict.fromkeys(modelos, datetime(2026, 10, 7, next(horas), tzinfo=timezone.utc)))

    with pytest.raises(openmeteo.ErroOpenMeteo, match="mudou durante a busca"):
        openmeteo.buscar(pontos(1))


def test_cota_esgotada_falha_de_primeira(monkeypatch):
    """Repetir um 429 não resolve, e ainda gastaria mais da cota."""
    chamadas = []

    def falso(*args, **kwargs):
        chamadas.append(1)
        return Resposta(429, {"error": True, "reason": "Minutely API request limit exceeded."})

    monkeypatch.setattr(openmeteo.requests, "request", falso)

    with pytest.raises(openmeteo.ErroOpenMeteo, match="cota do Open-Meteo esgotada.*Minutely"):
        openmeteo.previsao_horaria(pontos(1))
    assert len(chamadas) == 1


def test_falha_passageira_e_repetida(monkeypatch, open_meteo):
    respostas = iter([Resposta(502, "Bad Gateway"), Resposta(200, ValueError("cortada"))])
    original = openmeteo.requests.request

    def falso(metodo, url, **kwargs):
        try:
            return next(respostas)
        except StopIteration:
            return original(metodo, url, **kwargs)

    monkeypatch.setattr(openmeteo.requests, "request", falso)

    tabela = openmeteo.previsao_horaria(pontos(1))

    assert not tabela.empty   # a terceira tentativa foi atendida


# =====================================================
# O DIA
# =====================================================
def horas_locais(inicio: str, quantas: int, **colunas) -> pd.DataFrame:
    """Horas seguidas a partir de `inicio`, no horário de MS, já em UTC como vem do Open-Meteo."""
    locais = pd.date_range(inicio, periods=quantas, freq="h", tz=config.FUSO_MS)
    padrao = {nome: 0.0 for nome in openmeteo.VARIAVEIS.values()}
    return pd.DataFrame({"modelo": "ecmwf_ifs025", "ponto": "A702", "latitude": -20.45, "longitude": -54.62,
                         "hora_prevista_utc": locais.tz_convert("UTC"), **{**padrao, **colunas}})


def test_a_leitura_da_meia_noite_fecha_o_dia_anterior():
    """Da hora das 01:00 de 17/09 à das 00:00 de 18/09 é o dia 17, como no INMET."""
    temperatura = [20.0] * 23 + [35.0]                       # a mais quente é a das 00:00 de 18/09
    horas = horas_locais("2026-09-17 01:00", 24, temperatura=temperatura, chuva=[1.0] * 24)

    dia = openmeteo.diario(horas).iloc[0]

    assert str(dia["dia_previsto"]) == "2026-09-17"
    assert (dia["temp_max"], dia["chuva"], dia["horas"]) == (35.0, 24.0, 24)


def test_dias_pela_metade_saem_marcados():
    horas = horas_locais("2026-09-17 13:00", 24)               # metade do 17, metade do 18

    dias = openmeteo.diario(horas)

    assert dias.set_index("dia_previsto")["horas"].to_dict() == {
        pd.Timestamp("2026-09-17").date(): 12, pd.Timestamp("2026-09-18").date(): 12}


def test_dia_sem_nenhuma_hora_de_chuva_e_sem_dado_e_nao_zero():
    horas = horas_locais("2026-09-17 01:00", 24, chuva=[np.nan] * 24)

    assert np.isnan(openmeteo.diario(horas)["chuva"].iloc[0])


@pytest.mark.parametrize("direcoes, esperada", [
    ([0.0] * 12 + [180.0] * 12, np.nan),     # norte e sul se cancelam: não há rumo
    ([80.0] * 12 + [100.0] * 12, 90.0),      # um pouco de cada lado do leste
    ([350.0] * 12 + [10.0] * 12, 0.0),       # em volta do norte: a média dos ângulos daria 180°
])
def test_a_direcao_do_dia_e_a_resultante_das_horas(direcoes, esperada):
    horas = horas_locais("2026-09-17 01:00", 24, vento=[10.0] * 24, direcao=direcoes)

    obtida = openmeteo.diario(horas)["direcao_dominante"].iloc[0]

    if np.isnan(esperada):
        assert np.isnan(obtida)
    else:
        assert obtida == pytest.approx(esperada, abs=1e-6)


# =====================================================
# AS SEMANAS
# =====================================================
def test_as_semanas_vem_uma_linha_por_ponto_e_semana_com_a_rodada(open_meteo):
    tabela = openmeteo.buscar_semanas(pontos(2))

    assert list(tabela.columns) == openmeteo.COLUNAS_SEMANAIS
    assert len(tabela) == 2 * 6                                  # a sétima semana, vazia, sai
    assert str(tabela["semana"].min()) == "2026-10-05" and str(tabela["semana"].max()) == "2026-11-09"
    assert (tabela["rodada_utc"] == datetime(2026, 10, 7, 12, tzinfo=timezone.utc)).all()
    assert (tabela["modelo"] == config.MODELO_SEMANAS).all()


def test_o_pedido_semanal_leva_as_quatro_anomalias_e_os_46_dias(open_meteo):
    pedidos, _ = open_meteo

    openmeteo.buscar_semanas(pontos(1))

    semanais = [dados for _, url, dados in pedidos if url == config.URL_OPENMETEO_SAZONAL]
    assert len(semanais) == 1
    assert semanais[0]["weekly"].split(",") == list(openmeteo.VARIAVEIS_SEMANAIS)
    assert semanais[0]["forecast_days"] == 46                    # sem isto, a API devolve 27 semanas
    assert semanais[0]["models"] == "ecmwf_ec46_ensemble_mean"


def test_a_rodada_do_ec46_vem_dos_metadados_da_api_sazonal(open_meteo):
    """A média dos membros tem metadados com intervalo de 744 h; vale o do EC46 com os membros."""
    pedidos, _ = open_meteo

    openmeteo.rodadas((config.MODELO_SEMANAS,))

    assert pedidos[-1][1] == "https://seasonal-api.open-meteo.com/data/ecmwf_ec46/static/meta.json"


def test_muitos_pontos_nas_semanas_vao_em_lotes_que_cabem_no_minuto(open_meteo):
    """Cada ponto custa 3,3 pela regra (46 dias pesam 46/14): 400 pontos viram três lotes."""
    pedidos, esperas = open_meteo

    tabela = openmeteo.anomalias_semanais(pontos(400))

    lotes = [len(dados["latitude"].split(",")) for _, url, dados in pedidos if url == config.URL_OPENMETEO_SAZONAL]
    assert sum(lotes) == 400 and len(lotes) == 3
    assert all(openmeteo.custo(tamanho, variaveis=4, modelos=1, dias=46) <= config.OPENMETEO_POR_MINUTO
               for tamanho in lotes)
    assert esperas == [openmeteo.ESPERA_ENTRE_LOTES] * 2
    assert tabela["ponto"].nunique() == 400
