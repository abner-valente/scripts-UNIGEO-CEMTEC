"""Acesso à API do INMET, com as respostas HTTP simuladas."""
from datetime import date, datetime

import pandas as pd
import pytest
import requests

from modulos import config, inmet
from modulos.config import FUSO_UTC, Periodo

DIA = Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))


class RespostaFalsa:
    def __init__(self, conteudo=None, status_code=200, text=""):
        self.conteudo, self.status_code, self.text = conteudo, status_code, text

    def json(self):
        if isinstance(self.conteudo, Exception):
            raise self.conteudo
        return self.conteudo

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def trocar_get(monkeypatch, resposta) -> None:
    """Troca a consulta HTTP pelos dados do teste.

    O inmet passou a falar por uma sessão reaproveitada (uma conexão por thread, em vez de uma
    por estação), então é o `get` dela que os testes precisam interceptar.
    """
    monkeypatch.setattr(requests.Session, "get", lambda self, url, timeout: resposta(url, timeout))


@pytest.mark.parametrize("original, formatado", [
    ("CAMPO GRANDE", "Campo Grande"),
    ("  SAO GABRIEL DO OESTE ", "Sao Gabriel do Oeste"),
    ("DOIS IRMAOS DO BURITI", "Dois Irmaos do Buriti"),
])
def test_formatar_nome_estacao(original, formatado):
    assert inmet.formatar_nome_estacao(original) == formatado


def estacao(codigo, nome, uf, lat, lon, situacao="Operante") -> dict:
    return {"CD_ESTACAO": codigo, "DC_NOME": nome, "SG_ESTADO": uf, "CD_SITUACAO": situacao,
            "VL_LATITUDE": str(lat), "VL_LONGITUDE": str(lon)}


def test_estacoes_em_pane_ficam_de_fora(monkeypatch):
    """Pedir dados de uma estação em pane é consulta que sempre volta vazia. Em MS são 3 das 62."""
    trocar_get(monkeypatch, lambda url, timeout: RespostaFalsa([
        estacao("A702", "CAMPO GRANDE", "MS", -20.4, -54.7),
        estacao("A999", "ESTACAO MORTA", "MS", -21.0, -55.0, situacao="Pane"),
    ]))

    assert inmet.listar_estacoes("MS")["CD_ESTACAO"].tolist() == ["A702"]


def test_o_recorte_traz_as_vizinhas_de_outros_estados(monkeypatch):
    """A estação do outro lado da divisa descreve a borda tão bem quanto a de cá."""
    trocar_get(monkeypatch, lambda url, timeout: RespostaFalsa([
        estacao("A702", "CAMPO GRANDE", "MS", -20.4, -54.7),
        estacao("A001", "VIZINHA DE GOIAS", "GO", -18.0, -52.0),      # logo acima do enquadramento
        estacao("A002", "LONGE DEMAIS", "AM", -3.0, -60.0),
    ]))

    recorte = inmet.estacoes_do_recorte()

    assert sorted(recorte["CD_ESTACAO"]) == ["A001", "A702"]
    assert set(recorte["SG_ESTADO"]) == {"MS", "GO"}      # a coluna separa produto de apoio


def test_o_recorte_respeita_a_margem(monkeypatch):
    """Sem margem, os 8 vizinhos de uma célula da divisa ficam todos para dentro."""
    logo_fora = {"CD_ESTACAO": "A003", "DC_NOME": "PERTO", "SG_ESTADO": "PR", "CD_SITUACAO": "Operante",
                 "VL_LATITUDE": "-25.5", "VL_LONGITUDE": "-53.0"}   # 1° abaixo do enquadramento
    trocar_get(monkeypatch, lambda url, timeout: RespostaFalsa([logo_fora]))

    assert len(inmet.estacoes_do_recorte()) == 1                     # com a margem de 1,5°, entra
    assert inmet.estacoes_do_recorte(margem=0.0).empty               # sem margem, fica de fora


def test_listar_estacoes_filtra_a_uf_e_converte_coordenadas(monkeypatch):
    lista = [
        {"CD_ESTACAO": "A702", "DC_NOME": "CAMPO GRANDE", "SG_ESTADO": "MS", "VL_LATITUDE": "-20.44", "VL_LONGITUDE": "-54.72"},
        {"CD_ESTACAO": "A901", "DC_NOME": "CUIABA", "SG_ESTADO": "MT", "VL_LATITUDE": "-15.6", "VL_LONGITUDE": "-56.1"},
    ]
    trocar_get(monkeypatch, lambda url, timeout: RespostaFalsa(lista))
    estacoes = inmet.listar_estacoes("MS")
    assert estacoes["CD_ESTACAO"].tolist() == ["A702"]
    assert estacoes["Estação"].tolist() == ["Campo Grande"]
    assert estacoes["VL_LATITUDE"].iloc[0] == pytest.approx(-20.44)


def test_listar_estacoes_com_falha_levanta_erro(monkeypatch):
    trocar_get(monkeypatch, lambda url, timeout: RespostaFalsa(status_code=503))
    with pytest.raises(inmet.ErroINMET):
        inmet.listar_estacoes()


def test_baixar_dados_converte_valores_e_horarios(monkeypatch):
    registros = [
        {"DT_MEDICAO": "2026-07-30", "HR_MEDICAO": "0000", "TEM_MIN": "18,5", "CHUVA": "0.2", "RAD_GLO": "-3,5"},
        {"DT_MEDICAO": "2026-07-30", "HR_MEDICAO": "1300", "TEM_MIN": None, "CHUVA": "0", "RAD_GLO": "1520"},
    ]
    trocar_get(monkeypatch, lambda url, timeout: RespostaFalsa(registros))
    dados = inmet.baixar_dados_estacao("A702", *DIA.janela_busca)

    assert dados["dt_utc"].tolist() == [pd.Timestamp("2026-07-30 00:00", tz="UTC"), pd.Timestamp("2026-07-30 13:00", tz="UTC")]
    assert dados["dt_local"].iloc[1].hour == 9  # 13 UTC = 9 h em MS
    assert dados["TEM_MIN"].iloc[0] == 18.5
    assert pd.isna(dados["TEM_MIN"].iloc[1])
    assert dados["CHUVA"].tolist() == [0.2, 0.0]
    assert dados["RAD_GLO"].tolist() == [-3.5, 1520.0]  # toda coluna que não é texto vira número


@pytest.mark.parametrize("periodo, trecho_da_url", [
    # Dia 30/07 em MS = leituras das 05 UTC de 30/07 às 04 UTC de 31/07
    (Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30)), "/2026-07-30/2026-07-31/A702/"),
    (Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31)), "/2026-08-01/2026-09-01/A702/"),
    (Periodo.tempo_real(datetime(2026, 9, 14, 13, 25, tzinfo=FUSO_UTC)), "/2026-09-10/2026-09-14/A702/"),
], ids=["dia", "periodo", "tempo_real"])
def test_baixar_dados_pede_apenas_os_dias_necessarios(monkeypatch, periodo, trecho_da_url):
    urls = []

    def get_falso(url, timeout):
        urls.append(url)
        return RespostaFalsa([])

    trocar_get(monkeypatch, get_falso)
    inmet.baixar_dados_estacao("A702", *periodo.janela_busca)
    assert trecho_da_url in urls[0]


@pytest.mark.parametrize("falha", ["http_500", "json_invalido", "sem_conexao"])
def test_falhas_da_api_viram_erro_sem_expor_o_token(monkeypatch, capsys, falha):
    monkeypatch.setattr(config, "TOKEN_INMET", "TOKEN-SECRETO")

    def get_falso(url, timeout):
        if falha == "sem_conexao":
            raise requests.ConnectionError(f"falha ao acessar {url}")
        if falha == "http_500":
            return RespostaFalsa(status_code=500, text=f"erro interno em {url}")
        return RespostaFalsa(ValueError(f"resposta não é JSON: {url}"))

    trocar_get(monkeypatch, get_falso)
    with pytest.raises(inmet.ErroINMET) as erro:
        inmet.baixar_dados_estacao("A702", *DIA.janela_busca)
    assert "TOKEN-SECRETO" not in str(erro.value)
    assert "TOKEN-SECRETO" not in capsys.readouterr().out


def test_estacao_sem_leituras_no_periodo_retorna_none(monkeypatch):
    trocar_get(monkeypatch, lambda url, timeout: RespostaFalsa([]))
    assert inmet.baixar_dados_estacao("A702", *DIA.janela_busca) is None


def test_falha_passageira_e_repetida(monkeypatch):
    """A API do INMET às vezes encerra a conexão sem responder; a tentativa seguinte costuma funcionar."""
    tentativas = []

    def get_falso(url, timeout):
        tentativas.append(url)
        if len(tentativas) < config.TENTATIVAS:
            raise requests.ConnectionError("Remote end closed connection without response")
        return RespostaFalsa([{"DT_MEDICAO": "2026-07-30", "HR_MEDICAO": "1300", "CHUVA": "1,0"}])

    trocar_get(monkeypatch, get_falso)
    dados = inmet.baixar_dados_estacao("A702", *DIA.janela_busca)

    assert len(tentativas) == config.TENTATIVAS
    assert dados["CHUVA"].tolist() == [1.0]


def test_erro_de_token_nao_e_repetido(monkeypatch):
    """Erros 4xx (token inválido, estação inexistente) não melhoram com novas tentativas."""
    tentativas = []

    def get_falso(url, timeout):
        tentativas.append(url)
        return RespostaFalsa(status_code=401, text="token inválido")

    trocar_get(monkeypatch, get_falso)
    with pytest.raises(inmet.ErroINMET):
        inmet.baixar_dados_estacao("A702", *DIA.janela_busca)
    assert len(tentativas) == 1


def test_resumo_da_coleta_separa_sem_dados_de_falha(monkeypatch, capsys):
    estacoes = pd.DataFrame({"CD_ESTACAO": ["A1", "A2", "A3"], "Estação": ["Um", "Dois", "Tres"]})

    def baixar(codigo, inicio, fim):
        if codigo == "A2":
            return None
        if codigo == "A3":
            raise inmet.ErroINMET("conexão encerrada")
        return pd.DataFrame({"dt_utc": [1]})

    monkeypatch.setattr(inmet, "listar_estacoes", lambda uf=config.UF: estacoes)
    monkeypatch.setattr(inmet, "baixar_dados_estacao", baixar)

    coletados = inmet.baixar_estacoes(*DIA.janela_busca)
    saida = capsys.readouterr().out

    assert [estacao["CD_ESTACAO"] for estacao, _ in coletados] == ["A1"]
    assert "Estações com dados: 1 de 3" in saida
    assert "Sem leituras no período (1): Dois" in saida
    assert "Falha na consulta (1): Tres" in saida
