"""Os serviços de fora no mapa de camadas: o CICOE, os pontos, o ícone, a ficha e o tempo na hora."""
import base64
import io
import json
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pydeck as pdk
import pytest
from PIL import Image

from app import camadas, servicos
from modulos import calculos, config

FUSO = ZoneInfo("America/Campo_Grande")
# 25/11/2026 13:44 em MS (UTC-4) e duas edições depois
OBSERVADO_MS = int(pd.Timestamp("2026-11-25 17:44", tz="UTC").timestamp() * 1000)
EDITADO_MS = int(pd.Timestamp("2026-11-25 19:00", tz="UTC").timestamp() * 1000)
CRIADO_MS = int(pd.Timestamp("2026-11-25 18:00", tz="UTC").timestamp() * 1000)
NOMES = {"RODOVIA_INTERDITADA": "Rodovia interditada", "INCENDIO": "Incêndio"}


def do_servidor(objectid: int, tipo: str | None, observado: int | None, editado: int | None = None,
                criado: int | None = None, descricao: str | None = "BR-163 fechada") -> dict:
    return {"lon": -54.69, "lat": -22.43,
            "campos": {"objectid": objectid, "o_que_aconteceu": tipo, "quando_voce_observou_a_situacao": observado,
                       "descricao_acontecimento": descricao, "last_edited_date": editado, "created_date": criado}}


def test_o_cicoe_nunca_pede_o_contato_nem_quem_editou():
    pedidos = {campo.lower() for campo in servicos.CICOE.campos}

    assert {"informacoes_contato", "created_user", "last_edited_user"} <= set(servicos.CICOE.proibidos)
    assert not pedidos & {campo.lower() for campo in servicos.CICOE.proibidos}


def test_cada_servico_vira_uma_camada_do_quadro():
    assert [(camada.chave, camada.nome) for camada in servicos.camadas_dos_servicos()] == [
        ("cicoe", "Relatos do CICOE (PIN MS)")]
    assert servicos.por_chave("cicoe") is servicos.CICOE


def test_os_pontos_do_servico_com_o_nome_do_tipo_e_as_datas_em_ms():
    tabela = servicos.pontos_do_servico(servicos.CICOE, [
        do_servidor(8, "INCENDIO", OBSERVADO_MS + 3_600_000, criado=CRIADO_MS),
        do_servidor(7, "RODOVIA_INTERDITADA", OBSERVADO_MS, editado=EDITADO_MS, criado=CRIADO_MS),
    ], NOMES, FUSO)

    assert list(tabela.columns) == servicos.COLUNAS_DOS_PONTOS
    assert tabela["objectid"].tolist() == [7, 8]                      # do mais antigo ao mais novo
    rodovia = tabela.iloc[0]
    assert rodovia["titulo"] == "Rodovia interditada"
    assert pd.Timestamp(rodovia["quando"]) == pd.Timestamp("2026-11-25 13:44", tz=FUSO)
    assert rodovia["detalhe"].startswith("Observado em 25/11/2026 13:44\n")
    assert "CICOE, PIN MS" in rodovia["detalhe"]
    assert rodovia["atualizado"] == "25/11/2026 15:00"                # a última edição
    assert tabela.iloc[1]["atualizado"] == "25/11/2026 14:00"         # sem edição, a criação


def test_tipo_desconhecido_ou_vazio_e_data_que_falta():
    tabela = servicos.pontos_do_servico(servicos.CICOE, [
        do_servidor(1, "ARVORE_NA_REDE", None, descricao=None),
        do_servidor(2, None, OBSERVADO_MS),
    ], NOMES, FUSO)

    sem_data = tabela.set_index("objectid").loc[1]
    assert sem_data["titulo"] == "Arvore na rede" and sem_data["quando"] == "" and sem_data["descricao"] == ""
    assert sem_data["detalhe"].startswith("Sem a data da observação")
    assert sem_data["icone"]["url"] == servicos.icone_uri(servicos.CICOE, "OUTRA_OCORRENCIA")
    assert tabela.set_index("objectid").loc[2, "tipo"] == "OUTRA_OCORRENCIA"


def test_a_referencia_da_janela_e_a_observacao_e_sem_ela_o_registro():
    tabela = servicos.pontos_do_servico(servicos.CICOE, [
        do_servidor(1, "INCENDIO", OBSERVADO_MS, editado=EDITADO_MS),
        do_servidor(2, "INCENDIO", None, editado=EDITADO_MS, criado=CRIADO_MS),
        do_servidor(3, "INCENDIO", None),
    ], NOMES, FUSO).set_index("objectid")

    assert pd.Timestamp(tabela.loc[1, "referencia"]) == pd.Timestamp("2026-11-25 13:44", tz=FUSO)
    assert pd.Timestamp(tabela.loc[2, "referencia"]) == pd.Timestamp("2026-11-25 15:00", tz=FUSO)
    assert tabela.loc[3, "referencia"] == ""


def test_a_janela_de_tempo_conta_a_partir_de_agora_pela_observacao():
    # A captura de 26/11: com a janela de 6 h às 08:27, o relato observado às 13:11 da véspera sai do mapa
    agora = pd.Timestamp("2026-11-26 08:27", tz=FUSO)
    observado = {nome: int(momento.tz_convert("UTC").timestamp() * 1000) for nome, momento in {
        "vespera": pd.Timestamp("2026-11-25 13:11", tz=FUSO), "madrugada": pd.Timestamp("2026-11-26 03:05", tz=FUSO),
        "adiantado": agora + pd.Timedelta(minutes=20)}.items()}
    tabela = servicos.pontos_do_servico(servicos.CICOE, [
        do_servidor(1, "OUTRA_OCORRENCIA", observado["vespera"]),
        do_servidor(2, "ALAGAMENTO_INUNDACAO", observado["madrugada"]),
        do_servidor(3, "INCENDIO", observado["adiantado"]),       # o relógio do celular adiantado
        do_servidor(4, "INCENDIO", None),                           # sem data nenhuma
    ], NOMES, FUSO)

    na_janela = servicos.desde(tabela, agora - pd.Timedelta(hours=6))

    assert na_janela["objectid"].tolist() == [2, 3]
    assert servicos.desde(tabela, agora - pd.Timedelta(hours=96))["objectid"].tolist() == [1, 2, 3]
    assert servicos.desde(tabela.iloc[0:0], agora).empty


def test_o_icone_e_um_svg_na_cor_do_tipo():
    svg = base64.b64decode(servicos.icone_uri(servicos.CICOE, "INCENDIO").split(",", 1)[1]).decode("utf-8")

    assert svg.startswith("<svg") and "fill='#f4511e'" in svg


def test_a_camada_leva_ao_navegador_so_o_icone_e_o_balao():
    tabela = servicos.pontos_do_servico(servicos.CICOE, [do_servidor(7, "INCENDIO", OBSERVADO_MS)], NOMES, FUSO)
    deck = camadas.deck([servicos.camada(servicos.CICOE, tabela, opacidade=0.7)],
                        pdk.ViewState(latitude=-20.5, longitude=-54.6, zoom=5.5), com_fundo=False)

    camada, = json.loads(deck.to_json())["layers"]

    assert camada["@@type"] == "IconLayer" and camada["id"] == "cicoe" and camada["opacity"] == 0.7
    assert camada["getIcon"] == "@@=icone" and camada["pickable"] is True
    assert set(camada["data"][0]) == set(servicos.COLUNAS_DA_CAMADA)    # a descrição só vai na ficha


def test_a_legenda_dos_tipos_escapa_o_nome_que_vem_do_servico():
    texto = servicos.legenda_dos_tipos(servicos.CICOE, {"INCENDIO": "<b>Fogo</b>"})

    assert "&lt;b&gt;Fogo&lt;/b&gt;" in texto
    assert texto.count("<img ") == len(servicos.CICOE.icones)


def jpeg_de_celular(largura: int, altura: int) -> bytes:
    # Como sai de um celular de pé: a imagem deitada, com a orientação 6 (girar 90°) e o GPS no EXIF
    imagem = Image.new("RGB", (largura, altura), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x8825] = {1: "S", 2: (22.0, 25.0, 0.0)}
    arquivo = io.BytesIO()
    imagem.save(arquivo, format="JPEG", exif=exif)
    return arquivo.getvalue()


def test_a_foto_sai_de_pe_reduzida_e_sem_os_metadados_da_camera():
    foto = servicos.foto_sem_metadados(jpeg_de_celular(2000, 1500))

    imagem = Image.open(io.BytesIO(foto))
    assert imagem.size == (675, 900)                       # girada e com o lado maior em 900 px
    assert len(imagem.getexif()) == 0


@pytest.mark.parametrize("bruto", [None, b"", b"%PDF-1.7 nao e imagem"])
def test_anexo_que_nao_e_imagem_nao_vira_foto(bruto):
    assert servicos.foto_sem_metadados(bruto) is None


def test_as_estacoes_mais_perto_em_ordem():
    cadastro = pd.DataFrame({"Estação": ["Longe", "Perto", "Sem lugar", "Meio"],
                             "VL_LONGITUDE": [-57.0, -54.70, np.nan, -55.0],
                             "VL_LATITUDE": [-19.0, -22.40, -22.4, -22.5]})

    perto = servicos.mais_perto(cadastro, -54.69, -22.43, quantos=2)

    assert [estacao["Estação"] for estacao, _ in perto] == ["Perto", "Meio"]
    assert perto[0][1] < 5 < perto[1][1]
    assert servicos.mais_perto(cadastro.iloc[0:0], -54.69, -22.43) == []


def test_so_entra_pluviometro_a_ate_50_km():
    # Em volta de Porto Murtinho (-57,88; -21,70): um a ~10 km, outro a ~55 km, e o de Bela Vista a ~145 km
    cadastro = pd.DataFrame({"Estação": ["Bela Vista", "Perto", "Quase"],
                             "VL_LONGITUDE": [-56.52, -57.80, -57.40], "VL_LATITUDE": [-22.11, -21.65, -21.60]})

    candidatos = servicos.pluviometros_candidatos(cadastro, -57.88, -21.70)

    assert [pluviometro["Estação"] for pluviometro, _ in candidatos] == ["Perto"]
    assert servicos.pluviometros_candidatos(cadastro.iloc[[0]], -57.88, -21.70) == []


def test_a_leitura_que_cobre_a_observacao_e_a_da_hora_cheia_seguinte():
    assert servicos.hora_da_leitura("2026-11-25T13:44:00-04:00", FUSO) == pd.Timestamp("2026-11-25 14:00", tz=FUSO)
    assert servicos.hora_da_leitura("2026-11-25T14:00:00-04:00", FUSO) == pd.Timestamp("2026-11-25 14:00", tz=FUSO)


def leituras_da_estacao() -> pd.DataFrame:
    horas = pd.date_range("2026-11-24 15:00", "2026-11-25 14:00", freq="h", tz=FUSO)
    tabela = pd.DataFrame({"dt_local": horas, "CHUVA": 0.5, "TEM_INS": 25.0})
    tabela.loc[tabela.index[-1], "TEM_INS"] = np.nan           # a das 14:00 veio sem temperatura
    tabela.loc[tabela.index[-1], "CHUVA"] = 2.0
    return tabela


def test_a_chuva_na_hora_e_nas_24_horas_antes():
    hora = pd.Timestamp("2026-11-25 14:00", tz=FUSO)

    assert servicos.chuva_ate(leituras_da_estacao(), hora, 1) == 2.0
    assert servicos.chuva_ate(leituras_da_estacao(), hora, 24) == pytest.approx(2.0 + 23 * 0.5)
    assert servicos.chuva_ate(leituras_da_estacao().assign(CHUVA=np.nan), hora, 1) is None
    assert servicos.chuva_ate(pd.DataFrame(), hora, 1) is None


def test_sem_temperatura_na_hora_vale_a_leitura_anterior():
    linha = servicos.leitura_da_hora(leituras_da_estacao(), pd.Timestamp("2026-11-25 14:00", tz=FUSO))

    assert linha["dt_local"] == pd.Timestamp("2026-11-25 13:00", tz=FUSO)
    assert servicos.leitura_da_hora(pd.DataFrame(), pd.Timestamp("2026-11-25 14:00", tz=FUSO)) is None


def estacoes_em_volta() -> pd.DataFrame:
    # O pluviômetro do CEMADEN só mede chuva: a temperatura, a umidade e a rajada dele ficam sem valor
    return pd.DataFrame({"Longitude": [-54.70, -54.80, -54.60, -55.50], "Latitude": [-22.40, -22.50, -22.30, -21.00],
                         "chuva_hora": [2.0, 0.0, np.nan, 10.0], "chuva_24h": [20.0, 10.0, 30.0, 0.0],
                         "temperatura": [30.0, np.nan, 28.0, 25.0], "umidade": [40.0, np.nan, 50.0, 70.0],
                         "rajada": [20.0, np.nan, 35.0, np.nan]},
                        index=["Dourados", "CEMADEN · Dourados (Centro)", "Douradina", "Vizinha"])


def test_no_local_e_a_conta_da_superficie_no_ponto():
    centro = (-54.0, -20.5)                                   # o do recorte, que fixa a projeção do mapa
    pontos = estacoes_em_volta()

    saida = servicos.no_local(pontos, -54.69, -22.43, centro)

    lon_grade, lat_grade = np.meshgrid([-54.69, -53.0], [-22.43, -20.0])
    superficie = calculos.interpolar_idw(pontos["Longitude"], pontos["Latitude"], pontos["chuva_24h"], lon_grade,
                                         lat_grade, config.IDW_VIZINHOS, config.IDW_POTENCIA, centro=centro)
    assert saida["chuva_24h"] == pytest.approx(superficie[0, 0])   # o mesmo número da cor do mapa ali
    assert saida["chuva_24h_estacoes"] == 4 and saida["chuva_hora_estacoes"] == 3   # quem não mediu fica fora
    assert 0 < saida["chuva_hora"] < 10
    assert saida["temperatura_estacoes"] == 3 and 25 < saida["temperatura"] < 30   # sem o pluviômetro
    assert 40 < saida["umidade"] < 70


def test_com_poucas_estacoes_a_medida_no_local_fica_sem_valor():
    saida = servicos.no_local(estacoes_em_volta(), -54.69, -22.43, (-54.0, -20.5))

    assert saida["rajada"] is None and saida["rajada_estacoes"] == 2        # só duas mediram a rajada
    assert servicos.no_local(estacoes_em_volta().iloc[:2], -54.69, -22.43, (-54.0, -20.5))["chuva_24h"] is None


def test_medida_que_nao_veio_fica_sem_valor_no_local():
    saida = servicos.no_local(estacoes_em_volta()[["Longitude", "Latitude", "chuva_24h"]], -54.69, -22.43,
                              (-54.0, -20.5))

    assert saida["temperatura"] is None and saida["temperatura_estacoes"] == 0
    assert saida["chuva_24h"] is not None


def test_o_interpolado_usa_a_hora_que_ja_chegou_da_maioria_das_estacoes():
    # Às 15:00 só duas das seis mandaram; às 14:00, todas: vale a das 14:00. A das 16:00 é depois da observação
    linhas = [(nome, hora) for nome in "ABCDEF" for hora in ("13:00", "14:00")] + [("A", "15:00"), ("B", "15:00"),
                                                                                   ("A", "16:00")]
    leituras = pd.DataFrame({"Estação": [nome for nome, _ in linhas],
                             "dt_local": [pd.Timestamp(f"2026-11-26 {hora}", tz=FUSO) for _, hora in linhas],
                             "TEM_INS": 25.0})
    hora = pd.Timestamp("2026-11-26 15:00", tz=FUSO)

    assert servicos.hora_das_medidas(leituras, hora) == pd.Timestamp("2026-11-26 14:00", tz=FUSO)
    assert servicos.hora_das_medidas(leituras.assign(TEM_INS=np.nan), hora) is None
    assert servicos.hora_das_medidas(pd.DataFrame(), hora) is None


def test_os_flashes_das_celulas_a_ate_20_km():
    celulas = pd.DataFrame({"Longitude": [-54.65, -54.95, -56.0], "Latitude": [-22.45, -22.45, -22.4],
                            "flashes": [3, 4, 50]})

    assert servicos.flashes_em_volta(celulas, -54.69, -22.43) == 3      # a de ~27 km e a de ~135 km ficam fora
    assert servicos.flashes_em_volta(celulas, -54.69, -22.43, raio_km=30) == 7
    assert servicos.flashes_em_volta(celulas.iloc[0:0], -54.69, -22.43) == 0


def ponto_aberto(descricao: str = "BR-163 fechada no km 270") -> pd.Series:
    tabela = servicos.pontos_do_servico(servicos.CICOE, [
        do_servidor(7, "RODOVIA_INTERDITADA", OBSERVADO_MS, editado=EDITADO_MS, descricao=descricao)], NOMES, FUSO)
    return tabela.iloc[0]


def test_a_ficha_traz_o_relato_e_o_tempo_na_hora_da_observacao():
    condicoes = {
        "inmet": {"nome": "CAARAPO", "codigo": "S706", "km": 29.2, "leitura": pd.Timestamp("2026-11-25 14:00", tz=FUSO),
                  "temperatura": 24.2, "umidade": 84.0, "rajada": 11.2, "chuva_hora": 0.0, "chuva_24h": 19.8},
        "cemaden": {"nome": "CEMADEN · Dourados (Centro)", "km": 26.1, "chuva_hora": 0.0, "chuva_24h": 17.2},
        "raios": 0,
    }

    texto = servicos.ficha(servicos.CICOE, ponto_aberto(), "Rodovia interditada", condicoes)

    assert "Relato do CICOE" in texto and "Rodovia interditada" in texto and "BR-163 fechada no km 270" in texto
    assert "25/11/2026 13:44" in texto and "Última atualização no serviço: 25/11/2026 15:00" in texto
    assert "<b>CAARAPO</b> (S706), a 29 km · leitura das 14:00 de 25/11" in texto
    assert "24,2 °C" in texto and "84 %" in texto and "11 km/h" in texto and "19,8 mm" in texto
    assert "<b>Dourados (Centro)</b>, a 26 km" in texto and "17,2 mm" in texto
    assert "a até 20 km nas 3 h antes: <b>0 flashes</b>" in texto


def test_a_ficha_escapa_a_descricao_e_diz_o_que_faltou():
    texto = servicos.ficha(servicos.CICOE, ponto_aberto("<script>alert(1)</script>"), "Rodovia interditada",
                           {"raios": None})

    assert "<script>" not in texto and "&lt;script&gt;" in texto
    assert "Sem leitura do INMET perto desse horário." in texto
    assert "CEMADEN" not in texto and "Raios" not in texto                # sem pluviômetro e sem o banco dos raios


def test_a_ficha_traz_as_medidas_interpoladas_no_local_antes_das_estacoes():
    condicoes = {"no_local": {"temperatura": 24.64, "umidade": 81.4, "rajada": 18.6, "chuva_hora": 1.23,
                              "chuva_24h": 18.46, "chuva_hora_estacoes": 79, "chuva_24h_estacoes": 80},
                 "inmet": {"nome": "FATIMA DO SUL", "codigo": "S708", "km": 21.0,
                           "leitura": pd.Timestamp("2026-11-25 14:00", tz=FUSO), "temperatura": 25.0, "umidade": 86.0,
                           "rajada": 15.0, "chuva_hora": 0.0, "chuva_24h": 23.8}}

    texto = servicos.ficha(servicos.CICOE, ponto_aberto(), "Rodovia interditada", condicoes)

    assert "<b>No local do relato</b>, interpolado: o IDW das estações que mediram cada medida" in texto
    local = texto[texto.index("No local do relato"):texto.index("Estação do INMET mais próxima")]
    assert "24,6 °C" in local and "81 %" in local and "19 km/h" in local and "1,2 mm" in local
    assert "18,5 mm" in local
    assert texto.index("No local do relato") < texto.index("Estação do INMET mais próxima")


def test_sem_estacoes_para_interpolar_a_ficha_nao_mostra_o_local():
    condicoes = {"no_local": {"temperatura": None, "umidade": None, "rajada": None, "chuva_hora": None,
                              "chuva_24h": None, "chuva_hora_estacoes": 1, "chuva_24h_estacoes": 2}}

    assert "No local do relato" not in servicos.ficha(servicos.CICOE, ponto_aberto(), "Rodovia interditada", condicoes)


def test_sem_pluviometro_perto_a_ficha_diz_que_nao_ha():
    texto = servicos.ficha(servicos.CICOE, ponto_aberto(), "Rodovia interditada", {"cemaden": None, "raios": 0})

    assert "Nenhum pluviômetro do CEMADEN com leitura a até 50 km." in texto


def test_sem_data_a_ficha_sai_sem_o_bloco_do_tempo():
    ponto = ponto_aberto()
    ponto["quando"] = ""

    texto = servicos.ficha(servicos.CICOE, ponto, "Rodovia interditada", {})

    assert "sem data" in texto and "Condições meteorológicas" not in texto
