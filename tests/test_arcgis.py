"""ArcGIS Feature Service: os pontos com só os campos pedidos, as páginas, o domínio, a foto e os erros."""
import pytest
import requests

from modulos import arcgis

URL = "https://exemplo.ms.gov.br/arcgis/rest/services/Hosted/relatos/FeatureServer/0"


class Resposta:
    def __init__(self, corpo=None, conteudo: bytes = b"", status: int = 200):
        self._corpo = corpo
        self.content = conteudo
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if self._corpo is None:
            raise ValueError("não é JSON")
        return self._corpo


def ponto(objectid: int, lon: float = -54.6, lat: float = -20.4, **campos) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"objectid": objectid, **campos}}


@pytest.fixture
def servidor(monkeypatch):
    pedidos, respostas = [], []

    def falso(url, params=None, timeout=None):
        pedidos.append((url, dict(params or {})))
        resposta = respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    monkeypatch.setattr(arcgis.requests, "get", falso)
    return pedidos, respostas


def test_pede_so_os_campos_da_lista_e_descarta_o_que_vier_a_mais(servidor):
    pedidos, respostas = servidor
    # O servidor manda o contato mesmo sem ter sido pedido: ele não pode chegar a quem chamou
    respostas.append(Resposta({"type": "FeatureCollection",
                               "features": [ponto(7, o_que_aconteceu="INCENDIO", informacoes_contato="(67) 9999")]}))

    saida = arcgis.pontos(URL, ("o_que_aconteceu",), proibidos=("informacoes_contato",))

    url, parametros = pedidos[0]
    assert url == f"{URL}/query"
    assert parametros["outFields"] == "objectid,o_que_aconteceu"
    assert parametros["f"] == "geojson" and parametros["outSR"] == 4326 and parametros["where"] == "1=1"
    assert saida == [{"lon": -54.6, "lat": -20.4, "campos": {"objectid": 7, "o_que_aconteceu": "INCENDIO"}}]


@pytest.mark.parametrize("campo", ["informacoes_contato", "Informacoes_Contato"])
def test_um_campo_proibido_nunca_vai_ao_servidor(servidor, campo):
    pedidos, _ = servidor

    with pytest.raises(ValueError, match="proibidos"):
        arcgis.pontos(URL, ("o_que_aconteceu", campo), proibidos=("informacoes_contato",))

    assert pedidos == []


def test_ponto_sem_geometria_de_ponto_fica_de_fora(servidor):
    _, respostas = servidor
    respostas.append(Resposta({"features": [
        ponto(1),
        {"geometry": None, "properties": {"objectid": 2}},
        {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
         "properties": {"objectid": 3}},
    ]}))

    assert [linha["campos"]["objectid"] for linha in arcgis.pontos(URL, ())] == [1]


def test_acima_do_limite_de_uma_consulta_pede_a_pagina_seguinte(servidor):
    pedidos, respostas = servidor
    respostas += [Resposta({"features": [ponto(1), ponto(2)], "properties": {"exceededTransferLimit": True}}),
                  Resposta({"features": [ponto(3)]})]

    saida = arcgis.pontos(URL, ())

    assert [linha["campos"]["objectid"] for linha in saida] == [1, 2, 3]
    assert [parametros["resultOffset"] for _, parametros in pedidos] == [0, arcgis.POR_PAGINA]
    assert all(parametros["orderByFields"] == "objectid" for _, parametros in pedidos)


@pytest.mark.parametrize("resposta", [
    requests.ConnectionError("fora do ar"),
    Resposta(status=503),
    Resposta(corpo=None),
    Resposta({"error": {"code": 400, "message": "Invalid query"}}),
])
def test_falha_do_servico_vira_erro_do_arcgis(servidor, resposta):
    _, respostas = servidor
    respostas.append(resposta)

    with pytest.raises(arcgis.ErroArcgis):
        arcgis.pontos(URL, ())


def test_o_dominio_traz_o_nome_por_extenso_de_cada_codigo(servidor):
    _, respostas = servidor
    camada = {"fields": [{"name": "objectid", "domain": None},
                         {"name": "o_que_aconteceu", "domain": {"codedValues": [
                             {"code": "INCENDIO", "name": "Incêndio"},
                             {"code": "QUEDA_ARVORES", "name": "Queda de árvores"}]}}]}
    respostas += [Resposta(camada), Resposta(camada)]

    assert arcgis.dominio(URL, "o_que_aconteceu") == {"INCENDIO": "Incêndio", "QUEDA_ARVORES": "Queda de árvores"}
    assert arcgis.dominio(URL, "objectid") == {}


def test_a_foto_e_a_primeira_imagem_anexada(servidor):
    pedidos, respostas = servidor
    respostas += [Resposta({"attachmentInfos": [{"id": 4, "contentType": "application/pdf"},
                                                 {"id": 9, "contentType": "image/jpeg"},
                                                 {"id": 12, "contentType": "image/png"}]}),
                  Resposta(conteudo=b"jpeg")]

    assert arcgis.primeira_foto(URL, 7) == b"jpeg"
    assert [url for url, _ in pedidos] == [f"{URL}/7/attachments", f"{URL}/7/attachments/9"]


def test_sem_imagem_anexada_nao_ha_foto(servidor):
    pedidos, respostas = servidor
    respostas.append(Resposta({"attachmentInfos": [{"id": 4, "contentType": "application/pdf"}]}))

    assert arcgis.primeira_foto(URL, 7) is None
    assert len(pedidos) == 1


def test_foto_que_nao_vem_vira_erro_do_arcgis(servidor):
    _, respostas = servidor
    respostas += [Resposta({"attachmentInfos": [{"id": 9, "contentType": "image/jpeg"}]}),
                  requests.Timeout("lento")]

    with pytest.raises(arcgis.ErroArcgis):
        arcgis.primeira_foto(URL, 7)
