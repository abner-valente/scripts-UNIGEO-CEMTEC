"""Acesso a camadas de pontos de um ArcGIS Feature Service público (o PIN MS, o portal do estado).

Só busca e devolve, como o inmet.py e o openmeteo.py: os pontos com os campos pedidos, os nomes
por extenso de um campo de domínio e a primeira foto anexada a um ponto. Nada aqui sabe o que o
ponto significa; isso fica em app/servicos.py, que descreve cada serviço.

Dado pessoal não sai do servidor: os campos proibidos de um serviço nunca entram no pedido, e um
campo que o servidor mande sem ter sido pedido é descartado antes de chegar a quem chamou.
"""
import requests

TEMPO_LIMITE = 20
TEMPO_LIMITE_DA_FOTO = 30
# O PIN MS devolve até 1.000 pontos por consulta (maxRecordCount da camada, 26/11/2026); acima
# disso, a consulta pede a página seguinte
POR_PAGINA = 1000
MAXIMO_DE_PAGINAS = 20


class ErroArcgis(Exception):
    pass


def _json(url: str, parametros: dict, tempo: int = TEMPO_LIMITE) -> dict:
    try:
        resposta = requests.get(url, params=parametros, timeout=tempo)
        resposta.raise_for_status()
        corpo = resposta.json()
    except (requests.RequestException, ValueError) as erro:
        raise ErroArcgis(f"o serviço não respondeu ({erro.__class__.__name__})") from erro
    if isinstance(corpo, dict) and "error" in corpo:
        detalhe = corpo["error"].get("message", "erro") if isinstance(corpo["error"], dict) else corpo["error"]
        raise ErroArcgis(f"o serviço recusou o pedido ({detalhe})")
    return corpo


def dominio(url: str, campo: str) -> dict[str, str]:
    # O nome por extenso de cada código de um campo de domínio, lido da própria camada
    for descricao in _json(url, {"f": "json"}).get("fields", []):
        if descricao.get("name") == campo and descricao.get("domain"):
            return {str(valor["code"]): str(valor["name"])
                    for valor in descricao["domain"].get("codedValues", [])}
    return {}


def pontos(url: str, campos: tuple[str, ...], proibidos: tuple[str, ...] = (), onde: str = "1=1",
           id_do_ponto: str = "objectid") -> list[dict]:
    # Uma linha por ponto: {"lon", "lat", "campos"}, só com os campos pedidos (e o id do ponto, que
    # ordena as páginas). Pontos sem geometria de ponto ficam de fora
    pedidos = list(dict.fromkeys([id_do_ponto, *campos]))
    barrados = sorted({campo.lower() for campo in pedidos} & {campo.lower() for campo in proibidos})
    if barrados:
        raise ValueError(f"campos proibidos no pedido: {', '.join(barrados)}")
    saida = []
    for pagina in range(MAXIMO_DE_PAGINAS):
        corpo = _json(f"{url}/query", {"where": onde, "outFields": ",".join(pedidos), "outSR": 4326,
                                       "returnGeometry": "true", "f": "geojson", "orderByFields": id_do_ponto,
                                       "resultOffset": pagina * POR_PAGINA, "resultRecordCount": POR_PAGINA})
        feicoes = corpo.get("features", [])
        for feicao in feicoes:
            geometria = feicao.get("geometry") or {}
            if geometria.get("type") != "Point" or len(geometria.get("coordinates") or []) < 2:
                continue
            propriedades = feicao.get("properties") or {}
            lon, lat = geometria["coordinates"][:2]
            saida.append({"lon": float(lon), "lat": float(lat),
                          "campos": {campo: propriedades.get(campo) for campo in pedidos}})
        mais = corpo.get("exceededTransferLimit") or (corpo.get("properties") or {}).get("exceededTransferLimit")
        if not mais or not feicoes:
            break
    return saida


def primeira_foto(url: str, objectid: int) -> bytes | None:
    # A primeira imagem anexada ao ponto, como veio do servidor (com os metadados da câmera: quem
    # mostra deve tirá-los, veja app/servicos.foto_sem_metadados)
    anexos = _json(f"{url}/{int(objectid)}/attachments", {"f": "json"}).get("attachmentInfos", [])
    fotos = [anexo for anexo in anexos if str(anexo.get("contentType", "")).startswith("image/")]
    if not fotos:
        return None
    try:
        resposta = requests.get(f"{url}/{int(objectid)}/attachments/{int(fotos[0]['id'])}",
                                timeout=TEMPO_LIMITE_DA_FOTO)
        resposta.raise_for_status()
    except requests.RequestException as erro:
        raise ErroArcgis(f"a foto não veio ({erro.__class__.__name__})") from erro
    return resposta.content
