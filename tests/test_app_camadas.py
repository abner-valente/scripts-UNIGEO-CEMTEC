"""O mapa de camadas: as peças de pydeck dos mapas navegáveis, as legendas e o quadro que liga, desliga,
dá a opacidade e empilha as camadas."""
import json

import numpy as np
import pandas as pd
import pydeck as pdk
import pytest

from app import camadas, interativo, superficie

VISAO = pdk.ViewState(latitude=-20.5, longitude=-54.6, zoom=5.5)


def no_navegador(*pilha: pdk.Layer) -> list[dict]:
    # As camadas como o navegador as recebe: o JSON do deck.gl, com os nomes em camelCase
    return json.loads(camadas.deck(list(pilha), VISAO, com_fundo=False).to_json())["layers"]


def celulas_dos_raios() -> pd.DataFrame:
    return pd.DataFrame({"Longitude": [-54.65, -54.55], "Latitude": [-20.45, -20.45], "cor": [[255, 0, 0]] * 2,
                         "titulo": ["Campo Grande"] * 2, "detalhe": ["3 flashes", "1 flash"], "flashes": [3, 1]})


def test_o_quadrado_sai_do_centro_da_celula():
    assert camadas.quadrado(0.05) == (
        "[[Longitude - 0.050000, Latitude - 0.050000], [Longitude + 0.050000, Latitude - 0.050000], "
        "[Longitude + 0.050000, Latitude + 0.050000], [Longitude - 0.050000, Latitude + 0.050000]]")
    assert "Longitude + 0.040000, Latitude + 0.030000" in camadas.quadrado(0.04, 0.03)


def test_a_imagem_da_superficie_vai_como_imagem_e_nao_como_expressao():
    uri = "data:image/png;base64,AAAA"

    camada, = no_navegador(camadas.imagem(uri, [-58.0, -24.0, -50.0, -17.0], id="chuva", opacidade=0.4))

    assert camada["@@type"] == "BitmapLayer" and camada["id"] == "chuva"
    assert camada["image"] == uri
    assert camada["bounds"] == [-58.0, -24.0, -50.0, -17.0] and camada["opacity"] == 0.4


def test_sem_opacidade_a_camada_sai_como_sempre_saiu():
    camada, = no_navegador(camadas.imagem("data:image/png;base64,AAAA", [0, 0, 1, 1]))

    assert "opacity" not in camada


def test_os_municipios_so_respondem_ao_mouse_quando_clicaveis():
    contornos = {"type": "FeatureCollection", "features": []}

    parados, clicaveis = no_navegador(camadas.municipios(contornos), camadas.municipios(contornos, clicaveis=True))

    assert parados["filled"] is False and "pickable" not in parados
    assert clicaveis["filled"] is True and clicaveis["pickable"] is True
    assert clicaveis["getFillColor"] == [255, 255, 255, 1]        # quase transparente: só responde ao mouse
    assert parados["id"] == clicaveis["id"] == "municipios"


def test_os_limites_sao_os_municipios_e_o_estado_com_a_borda_clara_por_baixo():
    vazio = {"type": "FeatureCollection", "features": []}

    pilha = no_navegador(*camadas.limites(vazio, vazio, opacidade=0.6))

    assert [camada["id"] for camada in pilha] == ["municipios", "estado_borda", "estado"]
    assert pilha[1]["getLineColor"] == camadas.BORDA_DO_ESTADO and pilha[2]["getLineColor"] == camadas.COR_DO_ESTADO
    assert pilha[1]["lineWidthMinPixels"] > pilha[2]["lineWidthMinPixels"]
    assert all(camada["opacity"] == 0.6 for camada in pilha)


def test_o_rotulo_leva_o_estilo_do_painel_e_a_fonte_pelo_nome():
    dados = pd.DataFrame({"Longitude": [-54.6], "Latitude": [-20.4], "rotulo": ["12,4"]})

    camada, = no_navegador(camadas.rotulos(dados, "rotulo", ["Longitude", "Latitude"], [0, -18], get_size=4000,
                                           size_units="meters"))

    assert camada["fontFamily"] == camadas.FONTE_DO_PAINEL               # o nome, e não "@@=..."
    assert camada["getText"] == "@@=rotulo" and camada["getPixelOffset"] == [0, -18]
    assert camada["getSize"] == 4000                                     # o que vem a mais vale sobre o estilo
    assert camada["background"] is True and camada["fontWeight"] == 600


def test_os_textos_puros_do_rotulo_nao_viram_expressao():
    # Viravam: os números em metros da chuva saíam com os 12 px de sempre
    dados = pd.DataFrame({"Longitude": [-54.6], "Latitude": [-20.4], "nome": ["Campo Grande"]})

    camada, = no_navegador(camadas.rotulos(dados, "nome", ["Longitude", "Latitude"], [11, 0], size_units="meters",
                                           get_text_anchor="start", get_alignment_baseline="center"))

    assert camada["sizeUnits"] == "meters"
    assert camada["getTextAnchor"] == "start" and camada["getAlignmentBaseline"] == "center"


def test_as_estacoes_e_os_pluviometros_em_camadas_proprias():
    pontos = pd.DataFrame({"Estação": ["A"], "Longitude": [-54.6], "Latitude": [-20.4], "cor": [[25, 25, 25, 215]],
                           "altura": [1000.0]})

    estacoes, pluviometros = no_navegador(
        camadas.estacoes(pontos, ["Longitude", "Latitude", "altura"], parameters={"depthCompare": "always"}),
        camadas.pluviometros(pontos))

    assert estacoes["id"] == "estacoes" and pluviometros["id"] == "cemaden"
    assert estacoes["getPosition"] == "@@=[Longitude, Latitude, altura]"
    assert estacoes["parameters"] == {"depthCompare": "always"}
    assert pluviometros["getPosition"] == "@@=[Longitude, Latitude]"
    assert pluviometros["getLineColor"] == interativo.COR_DO_CEMADEN
    assert pluviometros["radiusMaxPixels"] < estacoes["radiusMaxPixels"]
    assert estacoes["pickable"] is pluviometros["pickable"] is True


def test_os_quadradinhos_dos_raios_levam_ao_navegador_so_o_que_desenham():
    camada, = no_navegador(camadas.quadradinhos(celulas_dos_raios(), "densidade", opacidade=0.8))

    assert camada["id"] == "densidade" and camada["opacity"] == 0.8
    assert set(camada["data"][0]) == set(camadas.COLUNAS_DOS_QUADRADINHOS)     # sem a contagem de flashes
    assert camada["getPolygon"] == "@@=" + camadas.quadrado(0.05)             # meia célula de 0,1°


def test_os_flashes_um_a_um():
    pontos = pd.DataFrame({"longitude": [-54.6], "latitude": [-20.4], "cor": [[255, 242, 0]], "titulo": ["Flash"],
                           "detalhe": ["há 3 min"], "hora_utc": [pd.Timestamp("2026-11-26 12:00", tz="UTC")]})

    camada, = no_navegador(camadas.flashes(pontos))

    assert camada["id"] == "flashes" and camada["getPosition"] == "@@=[longitude, latitude]"
    assert set(camada["data"][0]) == set(camadas.COLUNAS_DOS_FLASHES)


def test_sem_o_mapa_de_fundo_nada_vem_da_internet():
    com_fundo = json.loads(camadas.deck([], VISAO, com_fundo=True).to_json())
    sem_fundo = json.loads(camadas.deck([], VISAO, com_fundo=False).to_json())

    assert com_fundo["mapProvider"] == "carto" and com_fundo.get("mapStyle") is None
    assert "mapProvider" not in sem_fundo and sem_fundo["mapStyle"] == interativo.ESTILO_SEM_FUNDO
    assert com_fundo["layers"] == sem_fundo["layers"] == []


def test_a_legenda_por_cima_do_mapa_e_a_de_sempre():
    # A dos Raios, que ia por cima do mapa antes de vir para cá
    itens = [("#fff200", "até 5 min"), ("#ff9900", "5 a 15 min")]
    antes = ('<div style="font-size:0.8rem;color:#e0e0e0"><b>Tempo</b>'
             + "".join(f'<span style="display:flex;align-items:center;margin-top:0.25rem">'
                       f'<span style="width:0.9rem;height:0.9rem;border-radius:2px;margin-right:0.35rem;'
                       f'background:{cor}"></span>{rotulo}</span>' for cor, rotulo in itens) + "</div>")

    assert camadas.legenda(itens, "Tempo", sobre_o_mapa=True) == antes


def test_a_legenda_ao_lado_do_mapa_mostra_o_branco_e_escapa_o_texto():
    texto = camadas.legenda([("rgb(255,255,255)", "abaixo de 1 mm"), ("#1e88e5", "<b>Alagamento</b>")],
                            "Chuva & raios")

    assert "border:1px solid" in texto and "color:#e0e0e0" not in texto
    assert "&lt;b&gt;Alagamento&lt;/b&gt;" in texto and "Chuva &amp; raios" in texto


def test_a_escala_da_chuva_nas_cores_da_superficie():
    niveis = [1, 5, 10, 25]

    itens = camadas.itens_da_escala(niveis, "Blues", "mm")

    assert [rotulo for _, rotulo in itens] == ["abaixo de 1 mm", "1 a 5 mm", "5 a 10 mm", "10 a 25 mm",
                                              "acima de 25 mm"]
    assert itens[0][0] == "rgb(255,255,255)"                                   # o seco, em branco
    pintura, _ = superficie.cores(np.array([3.0, 7.5, 17.5, 26.0]), "Blues", niveis, opacidade=1.0)
    assert [cor for cor, _ in itens[1:]] == [f"rgb({r},{g},{b})" for r, g, b, _ in pintura.tolist()]


def test_a_escala_que_comeca_no_zero_nao_tem_o_branco():
    itens = camadas.itens_da_escala([0, 0.5, 1.5], "YlOrRd", "flashes/km²")

    assert [rotulo for _, rotulo in itens] == ["0 a 0,5 flashes/km²", "0,5 a 1,5 flashes/km²",
                                              "acima de 1,5 flashes/km²"]


def test_o_quadro_abre_com_o_catalogo_de_cima_para_baixo():
    quadro = camadas.quadro_inicial()

    assert list(quadro.columns) == camadas.COLUNAS_DO_QUADRO
    assert quadro["Camada"].tolist() == [camada.nome for camada in camadas.CATALOGO]
    assert quadro["Ordem"].tolist() == list(range(1, len(camadas.CATALOGO) + 1))
    assert quadro.set_index("Camada").loc["Densidade de raios", "Opacidade"] == 80
    assert quadro["Ver"].all()


def chaves(pilha) -> list[str]:
    return [camada.chave for camada, _ in pilha]


def test_as_camadas_vao_ao_mapa_de_baixo_para_cima():
    pilha = camadas.empilhadas(camadas.quadro_inicial())

    assert chaves(pilha) == [camada.chave for camada in reversed(camadas.CATALOGO)]
    assert dict((camada.chave, opacidade) for camada, opacidade in pilha)["densidade_raios"] == 0.8


def test_desmarcada_a_camada_sai_do_mapa_e_a_ordem_do_quadro_vale():
    quadro = camadas.quadro_inicial().set_index("Camada")
    quadro.loc["Raios da última hora", "Ver"] = False
    quadro.loc["Chuva acumulada", "Ordem"] = 1           # a chuva por cima de tudo
    quadro.loc["Estações (chuva)", "Ordem"] = 1          # empata com ela, e vem antes no quadro

    pilha = chaves(camadas.empilhadas(quadro.reset_index()))

    assert "raios_recentes" not in pilha
    assert pilha[-2:] == ["chuva", "estacoes_chuva"]     # no empate, por cima a de cima no quadro


def test_uma_celula_esvaziada_no_quadro_volta_ao_padrao():
    # Como a tabela do Streamlit devolve uma célula apagada: vazia, numa coluna que deixa de ser inteira
    quadro = camadas.quadro_inicial().astype({"Ver": object, "Opacidade": float, "Ordem": float}).set_index("Camada")
    quadro.loc["Densidade de raios", "Opacidade"] = np.nan
    quadro.loc["Raios da última hora", "Ordem"] = np.nan
    quadro.loc["Limites (estado e municípios)", "Ver"] = None
    quadro.loc["Chuva acumulada", "Opacidade"] = 250

    pilha = camadas.empilhadas(quadro.reset_index())

    opacidades = {camada.chave: opacidade for camada, opacidade in pilha}
    assert opacidades["densidade_raios"] == 0.8                # a do catálogo
    assert chaves(pilha)[0] == "raios_recentes"                # sem ordem, por baixo de todas
    assert "limites" not in opacidades                         # sem marca, desligada
    assert opacidades["chuva"] == 1.0                          # nunca passa de 100%


def test_um_nome_que_o_catalogo_nao_tem_e_ignorado():
    quadro = pd.concat([camadas.quadro_inicial(),
                        pd.DataFrame([{"Camada": "Outra", "Ver": True, "Opacidade": 100, "Ordem": 1}])])

    assert "Outra" not in [camada.nome for camada, _ in camadas.empilhadas(quadro)]


def test_so_a_camada_ligada_e_construida_com_a_sua_opacidade():
    chamadas = []

    def construtor(chave):
        def construir(opacidade):
            chamadas.append((chave, opacidade))
            return [f"{chave}_1", f"{chave}_2"] if chave == "limites" else [chave]
        return construir

    quadro = camadas.quadro_inicial().set_index("Camada")
    quadro.loc["Chuva acumulada", "Ver"] = False
    pilha = camadas.empilhadas(quadro.reset_index())

    montadas = camadas.montar(pilha, {chave: construtor(chave) for chave in ("chuva", "densidade_raios", "limites")})

    assert montadas == ["densidade_raios", "limites_1", "limites_2"]   # de baixo para cima; sem construtor, fora
    assert chamadas == [("densidade_raios", 0.8), ("limites", 1.0)]    # a chuva desligada nem foi construída


@pytest.mark.parametrize("valor, marcado", [(True, True), (False, False), (None, False), (np.nan, False)])
def test_a_marca_do_ver(valor, marcado):
    assert camadas._marcado(valor) is marcado
