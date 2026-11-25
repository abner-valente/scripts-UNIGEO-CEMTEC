"""Acesso à PED do CEMADEN, com as respostas HTTP simuladas."""
from datetime import date

import pandas as pd
import pytest
import requests

from modulos import cemaden, config

EMAIL, SENHA, TOKEN = "equipe@exemplo.gov.br", "senha-secreta", "cabecalho.carga.assinatura"

# Como a PED manda (conferido em 03/11/2026): o aviso do UTC, os nomes das colunas, a chuva e a
# intensidade de cada leitura, e o nível do rio na estação hidrológica
CSV_DO_DIA = """OBS.: Rede com horario UTC!
cod.estacao;nome;municipio;uf;latitude;longitude;datahora;sensor;valor;qualificacao;offset
500110201A;Centro;AQUIDAUANA;MS;-20.4826;-55.7851;2026-11-02 00:40:00;intensidade_precipitacao;0.0;0000;
500110201A;Centro;AQUIDAUANA;MS;-20.4826;-55.7851;2026-11-02 00:40:00;chuva;0.0;0000;
500568101A;Escola;MUNDO NOVO;MS;-23.9401;-54.2713;2026-11-02 13:10:00;chuva;2.4;0004;
500568101A;Escola;MUNDO NOVO;MS;-23.9401;-54.2713;2026-11-02 13:20:00;chuva;0.6;0004;
500270401H;Rio Anhanduí;CAMPO GRANDE;MS;-20.4630;-54.6120;2026-11-02 01:00:00;nível;1.2;0000;11.502
"""


class RespostaFalsa:
    def __init__(self, conteudo=None, status_code=200, texto=""):
        self.conteudo, self.status_code, self.text = conteudo, status_code, texto
        self.content = texto.encode("utf-8")

    def json(self):
        if self.conteudo is None:
            raise ValueError("não é JSON")
        return self.conteudo


class PED:
    # Uma PED falsa: o login devolve o token, e cada consulta, a próxima resposta da fila
    def __init__(self, monkeypatch, *respostas, login=None):
        self.respostas, self.logins, self.consultas = list(respostas), [], []
        self.login = login or RespostaFalsa({"token": TOKEN})
        monkeypatch.setattr(requests.Session, "post", self.post)
        monkeypatch.setattr(requests.Session, "get", self.get)

    def post(self, url, json, timeout):
        self.logins.append((url, json))
        return self.login

    def get(self, url, params, headers, timeout):
        self.consultas.append((url, params, headers))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


@pytest.fixture(autouse=True)
def esperas(monkeypatch):
    monkeypatch.setattr(config, "CEMADEN_EMAIL", EMAIL)
    monkeypatch.setattr(config, "CEMADEN_SENHA", SENHA)
    monkeypatch.setattr(config, "INTERVALO_CEMADEN", 0.0)
    monkeypatch.setattr(cemaden, "_ESTADO", {"sessao": None, "token": "", "obtido_em": 0.0,
                                             "ultima_consulta": float("-inf")})
    esperas = []
    monkeypatch.setattr(cemaden.time, "sleep", esperas.append)
    return esperas


def test_o_dia_vem_do_estado_inteiro_numa_consulta_so_e_fica_so_a_chuva(monkeypatch):
    ped = PED(monkeypatch, RespostaFalsa(texto=CSV_DO_DIA))

    chuva = cemaden.chuva_do_dia(date(2026, 11, 2))

    url, parametros, cabecalho = ped.consultas[0]
    assert url.endswith("/PED/rest/pcds/dados_rede") and cabecalho == {"token": TOKEN}
    assert parametros == {"uf": "MS", "rede": "11", "inicio": "202611020000", "fim": "202611022359"}
    assert list(chuva.columns) == cemaden.COLUNAS_DA_CHUVA
    assert chuva["codigo"].tolist() == ["500110201A", "500568101A", "500568101A"]   # sem o nível nem a intensidade
    assert chuva["chuva"].tolist() == [0.0, 2.4, 0.6]                                # o zero escrito fica zero
    assert chuva.loc[0, "leitura_utc"] == pd.Timestamp("2026-11-02 00:40", tz="UTC")
    # E a estação, como a leitura a descreve: é por aí que entra a que saiu do cadastro
    assert chuva.loc[1, ["nome", "municipio", "uf"]].tolist() == ["Escola", "MUNDO NOVO", "MS"]
    assert chuva.loc[1, ["latitude", "longitude"]].tolist() == [-23.9401, -54.2713]


def test_o_login_leva_a_conta_e_o_token_vale_para_as_consultas_seguintes(monkeypatch):
    ped = PED(monkeypatch, RespostaFalsa(texto=CSV_DO_DIA), RespostaFalsa(texto=CSV_DO_DIA))

    cemaden.chuva_do_dia(date(2026, 11, 2))
    cemaden.chuva_do_dia(date(2026, 11, 3))

    assert ped.logins == [(config.URL_CEMADEN_TOKEN, {"email": EMAIL, "password": SENHA})]


def test_numa_carga_longa_o_token_e_renovado_antes_das_quatro_horas(monkeypatch):
    relogio = {"agora": 0.0}
    monkeypatch.setattr(cemaden.time, "monotonic", lambda: relogio["agora"])
    ped = PED(monkeypatch, RespostaFalsa(texto=CSV_DO_DIA), RespostaFalsa(texto=CSV_DO_DIA))

    cemaden.chuva_do_dia(date(2026, 11, 2))
    relogio["agora"] = config.VALIDADE_TOKEN_CEMADEN + 1
    cemaden.chuva_do_dia(date(2026, 11, 3))

    assert len(ped.logins) == 2


def test_o_token_recusado_e_renovado_uma_vez(monkeypatch):
    ped = PED(monkeypatch, RespostaFalsa(status_code=401, texto="token expirado"), RespostaFalsa(texto=CSV_DO_DIA))

    assert len(cemaden.chuva_do_dia(date(2026, 11, 2))) == 3
    assert len(ped.logins) == 2


@pytest.mark.parametrize("texto", ["Alerta\nO Token informado é inválido!\n",
                                   '{"Alerta": "O Token informado é inválido!"}'])
def test_o_token_que_a_ped_recusa_com_http_400_tambem_e_renovado(monkeypatch, texto):
    # Assim ela recusou o token antes das 4 horas na carga do histórico (05/11/2026), em CSV e em JSON
    ped = PED(monkeypatch, RespostaFalsa(status_code=400, texto=texto), RespostaFalsa(texto=CSV_DO_DIA))

    assert len(cemaden.chuva_do_dia(date(2026, 11, 2))) == 3
    assert len(ped.logins) == 2


def test_o_token_recusado_de_novo_depois_do_login_novo_vira_erro_do_dia(monkeypatch):
    recusa = "Alerta\nO Token informado é inválido!\n"
    ped = PED(monkeypatch, RespostaFalsa(status_code=400, texto=recusa), RespostaFalsa(status_code=400, texto=recusa))

    with pytest.raises(cemaden.ErroCEMADEN, match="HTTP 400"):
        cemaden.chuva_do_dia(date(2026, 11, 2))
    assert len(ped.logins) == 2 and len(ped.consultas) == 2


def test_outro_erro_400_nao_gasta_um_login(monkeypatch):
    ped = PED(monkeypatch, RespostaFalsa(status_code=400, texto="Alerta\nData inicial maior que a final"))

    with pytest.raises(cemaden.ErroCEMADEN, match="Data inicial"):
        cemaden.chuva_do_dia(date(2026, 11, 2))
    assert len(ped.logins) == 1


def test_o_login_recusado_vira_erro_sem_mostrar_a_conta(monkeypatch):
    PED(monkeypatch, login=RespostaFalsa(status_code=401, texto=f"usuario {EMAIL} com a senha {SENHA} invalido"))

    with pytest.raises(cemaden.ErroCEMADEN) as erro:
        cemaden.chuva_do_dia(date(2026, 11, 2))

    assert EMAIL not in str(erro.value) and SENHA not in str(erro.value) and "***" in str(erro.value)


def test_sem_a_conta_no_env_o_erro_diz_o_que_falta(monkeypatch):
    monkeypatch.setattr(config, "CEMADEN_SENHA", "")
    PED(monkeypatch)

    with pytest.raises(cemaden.ErroCEMADEN, match="CEMADEN_EMAIL e CEMADEN_SENHA"):
        cemaden.cadastro()


def test_a_falha_passageira_e_repetida(monkeypatch, esperas, capsys):
    PED(monkeypatch, requests.ConnectionError(f"caiu a conexão do token {TOKEN}"), RespostaFalsa(texto=CSV_DO_DIA))

    assert len(cemaden.chuva_do_dia(date(2026, 11, 2))) == 3
    assert esperas == [config.PAUSA_ENTRE_TENTATIVAS]
    assert TOKEN not in capsys.readouterr().out                   # o aviso da tentativa não mostra o token


def test_o_erro_do_pedido_nao_e_repetido(monkeypatch):
    ped = PED(monkeypatch, RespostaFalsa(status_code=400, texto="parametro invalido"))

    with pytest.raises(cemaden.ErroCEMADEN, match="HTTP 400"):
        cemaden.chuva_do_dia(date(2026, 11, 2))
    assert len(ped.consultas) == 1


def test_a_recusa_pelo_limite_espera_o_minuto_virar(monkeypatch, esperas):
    PED(monkeypatch, RespostaFalsa(status_code=429), RespostaFalsa(texto=CSV_DO_DIA))

    assert len(cemaden.chuva_do_dia(date(2026, 11, 2))) == 3
    assert esperas == [config.PAUSA_NO_LIMITE_CEMADEN]


def test_o_ritmo_espaca_as_consultas(monkeypatch, esperas):
    monkeypatch.setattr(config, "INTERVALO_CEMADEN", 6.0)
    monkeypatch.setattr(cemaden.time, "monotonic", lambda: 100.0)
    PED(monkeypatch, RespostaFalsa(texto=CSV_DO_DIA), RespostaFalsa(texto=CSV_DO_DIA))

    cemaden.chuva_do_dia(date(2026, 11, 2))
    cemaden.chuva_do_dia(date(2026, 11, 3))

    assert esperas == [6.0]                                       # a primeira não espera; a segunda, o intervalo


@pytest.mark.parametrize("texto", [
    "OBS.: Rede com horario UTC!\n",
    "OBS.: Rede com horario UTC!\ncod.estacao;nome;municipio;uf;latitude;longitude;datahora;sensor;valor;"
    "qualificacao;offset\n",
    "",
    "Info\nNenhum resultado foi encontrado para essa requisição!\n",   # o dia antes da rede (HTTP 202)
])
def test_dia_sem_leitura_devolve_tabela_vazia(texto):
    chuva = cemaden.ler_chuva(texto)

    assert chuva.empty and list(chuva.columns) == cemaden.COLUNAS_DA_CHUVA


def test_resposta_em_outro_formato_vira_erro():
    with pytest.raises(cemaden.ErroCEMADEN, match="formato inesperado"):
        cemaden.ler_chuva("<html>Serviço indisponível</html>")


def test_a_leitura_repetida_fica_uma_so():
    repetida = CSV_DO_DIA + "500568101A;Escola;MUNDO NOVO;MS;-23.9401;-54.2713;2026-11-02 13:20:00;chuva;0.8;0004;\n"

    chuva = cemaden.ler_chuva(repetida)

    assert chuva.loc[chuva["leitura_utc"] == pd.Timestamp("2026-11-02 13:20", tz="UTC"), "chuva"].tolist() == [0.8]


def test_o_cadastro_traz_as_estacoes_com_o_municipio_e_quem_parou(monkeypatch):
    registros = [
        {"altitude": 0.0, "cidade": "TRÊS LAGOAS", "codestacao": "500830502A", "codibge": 5008305,
         "data_instalacao": "2015-04-01 13:43:30.134", "dh_inicio_inativo": "2026-10-21 16:46:23.509",
         "dh_ultima_remessa": "2026-10-21 14:45:18.000", "id_estacao": 7929, "id_rede": 11, "id_tipoestacao": 1,
         "latitude": -20.80873, "longitude": -51.71331, "nome": "São Carlos ", "rede_sigla": "CEMADEN",
         "tipoestacao_descricao": "Pluviométrica", "uf": "MS"},
        {"altitude": 0.0, "cidade": "SÃO GABRIEL DO OESTE", "codestacao": "500769501A", "codibge": 5007695,
         "data_instalacao": "2015-03-30 00:00:00", "dh_inicio_inativo": None,     # sem os milissegundos
         "dh_ultima_remessa": "2026-11-03 11:50:28.000", "id_estacao": 7927, "id_rede": 11, "id_tipoestacao": 1,
         "latitude": -19.40076, "longitude": -54.57602, "nome": "SAAE", "rede_sigla": "CEMADEN",
         "tipoestacao_descricao": "Pluviométrica", "uf": "MS"},
    ]
    ped = PED(monkeypatch, RespostaFalsa(registros))

    tabela = cemaden.cadastro("MS")

    assert ped.consultas[0][1] == {"uf": "MS", "formato": "JSON"}
    assert list(tabela.columns) == cemaden.COLUNAS_DO_CADASTRO
    assert tabela["municipio"].tolist() == ["Três Lagoas", "São Gabriel do Oeste"]
    assert tabela["nome"].tolist() == ["São Carlos", "SAAE"]
    assert tabela.loc[0, "ultima_parada"] == pd.Timestamp("2026-10-21 16:46:23.509")
    assert pd.isna(tabela.loc[1, "ultima_parada"])
    assert tabela["instalada_em"].tolist() == [pd.Timestamp("2015-04-01 13:43:30.134"), pd.Timestamp("2015-03-30")]


def test_parada_e_a_estacao_que_nao_mandou_nada_depois_da_ultima_parada():
    # Toda estação tem a data da última parada; a que mandou dado depois dela voltou
    # A última, sem data nenhuma, saiu do cadastro da PED e entrou no banco pelas leituras antigas
    cadastro = pd.DataFrame({
        "ultima_parada": pd.to_datetime(["2026-10-21 16:46", "2026-11-02 22:32", None, None]),
        "ultima_remessa": pd.to_datetime(["2026-10-21 14:45", "2026-11-03 11:50", "2026-11-03 11:00", None]),
    })

    assert cemaden.esta_parada(cadastro).tolist() == [True, False, False, True]


def test_a_chuva_entra_sem_o_ruido_de_conta_da_ped():
    ruidosa = CSV_DO_DIA.replace(";2.4;", ";2.4000000000000004;").replace(";0.6;", ";0.6000000000000001;")

    assert cemaden.ler_chuva(ruidosa)["chuva"].tolist() == [0.0, 2.4, 0.6]


def test_cadastro_em_outro_formato_vira_erro(monkeypatch):
    PED(monkeypatch, RespostaFalsa({"erro": "inesperado"}))

    with pytest.raises(cemaden.ErroCEMADEN, match="formato inesperado"):
        cemaden.cadastro("MS")
