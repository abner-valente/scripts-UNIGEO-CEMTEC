"""O nosso coletor do GLM: o bucket da NOAA, a leitura do netCDF e a gravação na glm_rt.

Sem rede: os arquivos são netCDF sintéticos com as variáveis, os tipos e a escala do GLM-L2-LCFA, e a
listagem do bucket é simulada. Os testes do banco são pulados sem ele (a fixture `conexao`).
"""
from datetime import date, datetime, timedelta, timezone

import netCDF4
import numpy as np
import pandas as pd
import pytest

import coletar
from modulos import banco, coletor_glm, config, glm

UTC = timezone.utc
COMECO = datetime(2026, 11, 20, 18, 16, tzinfo=UTC)
CAMPO_GRANDE = (-54.57, -20.43)
PORTO_ALEGRE = (-51.23, -30.03)    # a ~1.120 km
LIMA = (-77.04, -12.05)            # a ~2.500 km
NO_CANTO = (-68.0, -32.0)          # a ~1.850 km
# A escala do produto, como vem nos arquivos de 2026
ESCALA_DO_TEMPO, DESLOCAMENTO_DO_TEMPO = np.float32(0.0003814756), np.float32(-5.0)
ESCALA_DA_AREA = np.float32(152601.86)
ESCALA_DA_ENERGIA, DESLOCAMENTO_DA_ENERGIA = np.float32(9.99996e-16), np.float32(2.8515e-16)


def chave(comeco: datetime) -> str:
    # O nome do arquivo de 20 s que começa ali, como a NOAA dá
    fim = comeco + timedelta(seconds=20)
    def marca(momento):
        return f"{momento:%Y}{momento.timetuple().tm_yday:03d}{momento:%H%M%S}{momento.microsecond // 100000}"
    return (f"GLM-L2-LCFA/{comeco:%Y}/{comeco.timetuple().tm_yday:03d}/{comeco:%H}/"
            f"OR_GLM-L2-LCFA_G19_s{marca(comeco)}_e{marca(fim)}_c{marca(fim)[:-1]}5.nc")


def _sem_sinal(valores) -> np.ndarray:
    # O GLM guarda inteiros sem sinal em int16, com o atributo _Unsigned
    return np.asarray(np.round(valores), dtype=np.int64).astype(np.uint16).view(np.int16)


def arquivo(flashes, comeco: datetime = COMECO, energia_ausente: bool = False) -> bytes:
    # Um GLM-L2-LCFA mínimo. Cada flash: (longitude, latitude) ou (longitude, latitude, segundos)
    flashes = [(*flash, 0.5) if len(flash) == 2 else flash for flash in flashes]
    dados = netCDF4.Dataset("glm.nc", "w", memory=4096)
    dados.createDimension("number_of_flashes", len(flashes))
    tempo = dados.createVariable("product_time", "f8")
    tempo.units = "seconds since 2000-01-01 12:00:00"
    tempo.assignValue((comeco - glm.EPOCA).total_seconds())

    def variavel(nome, tipo, valores, **atributos):
        cheia = atributos.pop("_FillValue", None)
        var = dados.createVariable(nome, tipo, ("number_of_flashes",), fill_value=cheia)
        var.setncatts(atributos)
        var.set_auto_maskandscale(False)
        var[:] = valores

    lons, lats, segundos = (np.array(coluna, dtype=float) for coluna in zip(*flashes)) if flashes else ([], [], [])
    variavel("flash_id", "i2", _sem_sinal(40000 + np.arange(len(flashes))), _Unsigned="true")
    cru = (np.asarray(segundos) - DESLOCAMENTO_DO_TEMPO) / ESCALA_DO_TEMPO
    for nome, extra in (("flash_time_offset_of_first_event", 0), ("flash_time_offset_of_last_event", 100),
                        ("flash_frame_time_offset_of_first_event", 10), ("flash_frame_time_offset_of_last_event", 90)):
        variavel(nome, "i2", _sem_sinal(cru + extra), scale_factor=ESCALA_DO_TEMPO,
                 add_offset=DESLOCAMENTO_DO_TEMPO, _Unsigned="true")
    variavel("flash_lat", "f4", np.asarray(lats, dtype=np.float32))
    variavel("flash_lon", "f4", np.asarray(lons, dtype=np.float32))
    variavel("flash_area", "i2", _sem_sinal(np.full(len(flashes), 1000)), _FillValue=np.int16(-1),
             scale_factor=ESCALA_DA_AREA, add_offset=np.float32(0), _Unsigned="true")
    energia = np.full(len(flashes), -1, dtype=np.int16) if energia_ausente else _sem_sinal(np.full(len(flashes), 50))
    variavel("flash_energy", "i2", energia, _FillValue=np.int16(-1), scale_factor=ESCALA_DA_ENERGIA,
             add_offset=DESLOCAMENTO_DA_ENERGIA, _Unsigned="true")
    variavel("flash_quality_flag", "i2", np.zeros(len(flashes), dtype=np.int16), _FillValue=np.int16(-1),
             _Unsigned="true")
    return bytes(dados.close())


class Bucket:
    # O bucket da NOAA simulado: os arquivos por chave, e as chaves que falham ao baixar
    def __init__(self, monkeypatch):
        self.arquivos: dict[str, bytes] = {}
        self.quebrados: set[str] = set()
        self.baixados: list[str] = []
        monkeypatch.setattr(glm, "listar", self.listar)
        monkeypatch.setattr(glm, "baixar", self.baixar)

    def publicar(self, comeco: datetime, flashes=()) -> str:
        nome = chave(comeco)
        self.arquivos[nome] = arquivo(flashes, comeco)
        return nome

    def listar(self, hora) -> list[str]:
        prefixo = glm.prefixo(hora)
        return [nome for nome in self.arquivos if nome.startswith(prefixo)]

    def baixar(self, nome: str) -> bytes:
        self.baixados.append(nome)
        if nome in self.quebrados:
            raise glm.ErroGLM("o bucket da NOAA não respondeu (HTTP 503)")
        return self.arquivos[nome]


@pytest.fixture
def bucket(monkeypatch):
    return Bucket(monkeypatch)


@pytest.fixture
def nossa(conexao, monkeypatch):
    monkeypatch.setattr(config, "FONTE_GLM", "nossa")
    conexao.execute("TRUNCATE controle_glm_rt, glm_rt, raios_glm_hora, raios_glm_cobertura, raios_glm_coleta")
    conexao.commit()
    return conexao


def controle(conexao) -> pd.DataFrame:
    tabela = banco._tabela(conexao, "SELECT id, data_hora_referencia AS referencia, source_key, status "
                                    "FROM controle_glm_rt ORDER BY data_hora_referencia")
    conexao.rollback()
    return banco._horas_em_utc(tabela, ["referencia"])


def flashes(conexao) -> pd.DataFrame:
    tabela = banco._tabela(conexao, "SELECT * FROM glm_rt ORDER BY id_registro")
    conexao.rollback()
    return tabela


# =====================================================
# O BUCKET E O ARQUIVO, SEM BANCO
# =====================================================
def test_o_prefixo_e_a_hora_pelo_dia_do_ano():
    assert glm.prefixo(pd.Timestamp("2026-11-27 19:40", tz="UTC")) == "GLM-L2-LCFA/2026/331/19/"
    assert glm.prefixo(pd.Timestamp("2026-01-01 00:00", tz="UTC")) == "GLM-L2-LCFA/2026/001/00/"


def test_o_comeco_do_arquivo_sai_do_nome():
    nome = "GLM-L2-LCFA/2026/331/19/OR_GLM-L2-LCFA_G19_s20263311901400_e20263311902000_c20263311902015.nc"
    assert glm.inicio(nome) == pd.Timestamp("2026-11-27 19:01:40", tz="UTC")
    assert glm.inicio(chave(COMECO)) == pd.Timestamp(COMECO)
    with pytest.raises(glm.ErroGLM):
        glm.inicio("GLM-L2-LCFA/2026/331/19/outro.nc")


def test_o_trecho_publicado_de_novo_vale_pelo_ultimo_criado():
    primeiro = "x/OR_GLM-L2-LCFA_G19_s20263311901400_e20263311902000_c20263311902015.nc"
    segundo = "x/OR_GLM-L2-LCFA_G19_s20263311901400_e20263311902000_c20263311903010.nc"
    outro = "x/OR_GLM-L2-LCFA_G19_s20263311901200_e20263311901400_c20263311901418.nc"
    escolhidos = glm.um_por_inicio([segundo, primeiro, outro, "x/leiame.txt"])
    assert list(escolhidos.values()) == [outro, segundo]


def test_a_listagem_do_s3_segue_as_paginas(monkeypatch):
    paginas = [
        "<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'><IsTruncated>true</IsTruncated>"
        "<Contents><Key>a.nc</Key></Contents><NextContinuationToken>T1</NextContinuationToken></ListBucketResult>",
        "<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'><IsTruncated>false</IsTruncated>"
        "<Contents><Key>b.nc</Key></Contents></ListBucketResult>",
    ]
    pedidos = []

    class Resposta:
        status_code = 200

        def __init__(self, texto):
            self.content = texto.encode()

    class Sessao:
        def get(self, url, params=None, timeout=None):
            pedidos.append(dict(params))
            return Resposta(paginas[len(pedidos) - 1])

    monkeypatch.setattr(glm, "_sessao", lambda: Sessao())
    assert glm.listar(pd.Timestamp("2026-11-27 19:00", tz="UTC")) == ["a.nc", "b.nc"]
    assert pedidos[0] == {"list-type": "2", "prefix": "GLM-L2-LCFA/2026/331/19/"}
    assert pedidos[1]["continuation-token"] == "T1"


def test_o_bucket_fora_do_ar_vira_erro_depois_das_tentativas(monkeypatch):
    class Sessao:
        def get(self, url, params=None, timeout=None):
            return type("Resposta", (), {"status_code": 503})()

    monkeypatch.setattr(glm, "_sessao", lambda: Sessao())
    with pytest.raises(glm.ErroGLM, match="HTTP 503"):
        glm.baixar("x.nc")


def test_le_os_flashes_com_a_escala_e_os_inteiros_sem_sinal():
    comeco, tabela = glm.ler(arquivo([(*CAMPO_GRANDE, -0.75), (*LIMA, 3.25)]))
    assert comeco == COMECO
    assert list(tabela.columns[:2]) == ["flash_id", "time_utc"]
    assert list(tabela["flash_id"]) == [40000, 40001]
    assert tabela["flash_time_offset_of_first_event"].tolist() == pytest.approx([-0.75, 3.25], abs=2e-4)
    esperado = [pd.Timestamp(COMECO) + pd.Timedelta(seconds=s) for s in tabela["flash_time_offset_of_first_event"]]
    assert (tabela["time_utc"] - pd.Series(esperado)).abs().max() <= pd.Timedelta(microseconds=1)
    assert tabela["flash_lon"].tolist() == pytest.approx([CAMPO_GRANDE[0], LIMA[0]], abs=1e-5)
    assert tabela["flash_area"].tolist() == pytest.approx([1000 * float(ESCALA_DA_AREA)] * 2, rel=1e-6)
    assert tabela["flash_quality_flag"].tolist() == [0, 0]


def test_a_energia_ausente_vira_vazio():
    _, tabela = glm.ler(arquivo([CAMPO_GRANDE], energia_ausente=True))
    assert tabela["flash_energy"].isna().all()


def test_o_arquivo_sem_flash_e_o_arquivo_quebrado():
    comeco, tabela = glm.ler(arquivo([]))
    assert comeco == COMECO and tabela.empty and "time_utc" in tabela
    with pytest.raises(glm.ErroGLM, match="ilegível"):
        glm.ler(b"isto nao e um netcdf")


def test_fica_so_o_que_cai_no_circulo():
    _, tabela = glm.ler(arquivo([CAMPO_GRANDE, PORTO_ALEGRE, LIMA, NO_CANTO]))
    assert glm.no_circulo(tabela)["flash_lon"].tolist() == pytest.approx([CAMPO_GRANDE[0], PORTO_ALEGRE[0]], abs=1e-5)


def test_as_horas_do_tempo_real_e_do_dia_de_ms():
    agora = pd.Timestamp("2026-11-27 19:02:30", tz="UTC")
    assert coletor_glm.horas_do_tempo_real(agora) == [pd.Timestamp("2026-11-27 18:00", tz="UTC"),
                                                      pd.Timestamp("2026-11-27 19:00", tz="UTC")]
    horas = coletor_glm.horas_do_dia(date(2026, 11, 26), agora)
    assert len(horas) == 24
    assert horas[0] == pd.Timestamp("2026-11-26 04:00", tz="UTC")
    assert horas[-1] == pd.Timestamp("2026-11-27 03:00", tz="UTC")
    hoje = coletor_glm.horas_do_dia(date(2026, 11, 27), agora)
    assert hoje[0] == pd.Timestamp("2026-11-27 04:00", tz="UTC") and hoje[-1] == pd.Timestamp("2026-11-27 19:00", tz="UTC")
    assert coletor_glm.horas_do_dia(date(2026, 11, 28), agora) == []


def test_o_trecho_perdido_so_conta_depois_de_a_hora_fechar():
    hora = pd.Timestamp("2026-11-27 18:00", tz="UTC")
    trechos = coletor_glm.trechos_da_hora(hora)
    assert len(trechos) == 180 and trechos[1] == hora + pd.Timedelta(seconds=20)
    publicados = trechos[:100] + trechos[101:]
    assert coletor_glm.perdidos(hora, publicados, hora + pd.Timedelta(minutes=70)) == []
    assert coletor_glm.perdidos(hora, publicados, hora + pd.Timedelta(minutes=80)) == [trechos[100]]
    assert coletor_glm.perdidos(hora, [], hora + pd.Timedelta(hours=5)) == []


def test_os_argumentos_do_glm():
    assert coletar.argumentos(["glm"]).dia is None
    dias = coletar.argumentos(["glm", "--dia", "2026-11-26", "--dia", "27/11/2026"]).dia
    assert [dia.date() for dia in dias] == [date(2026, 11, 26), date(2026, 11, 27)]


# =====================================================
# NO BANCO
# =====================================================
def test_grava_o_arquivo_com_os_flashes_do_circulo(nossa, bucket):
    bucket.publicar(COMECO, [CAMPO_GRANDE, PORTO_ALEGRE, LIMA])
    agora = pd.Timestamp(COMECO) + pd.Timedelta(seconds=40)
    coleta = coletor_glm.coletar(nossa, agora)
    assert (coleta.arquivos, coleta.flashes, coleta.flashes_no_disco, coleta.falhas) == (1, 2, 3, 0)
    linhas = controle(nossa)
    assert linhas["status"].tolist() == [banco.CONCLUIDO]
    assert linhas["source_key"].tolist() == [chave(COMECO)]
    assert linhas["id"].iloc[0] >= 1_000_000_000
    gravados = flashes(nossa)
    assert gravados["id_cena"].tolist() == [linhas["id"].iloc[0]] * 2
    assert gravados["flash_id"].tolist() == [40000, 40001]
    assert gravados["geom"].notna().all()


def test_rodar_de_novo_nao_baixa_nem_duplica(nossa, bucket):
    bucket.publicar(COMECO, [CAMPO_GRANDE])
    agora = pd.Timestamp(COMECO) + pd.Timedelta(seconds=40)
    coletor_glm.coletar(nossa, agora)
    bucket.publicar(COMECO + timedelta(seconds=20), [CAMPO_GRANDE, CAMPO_GRANDE])
    coleta = coletor_glm.coletar(nossa, agora + pd.Timedelta(seconds=60))
    assert coleta.arquivos == 1 and len(bucket.baixados) == 2
    assert len(flashes(nossa)) == 3


def test_o_arquivo_que_falha_fica_para_a_proxima(nossa, bucket, capsys):
    nome = bucket.publicar(COMECO, [CAMPO_GRANDE])
    bucket.quebrados.add(nome)
    agora = pd.Timestamp(COMECO) + pd.Timedelta(seconds=40)
    assert coletor_glm.coletar(nossa, agora).falhas == 1
    assert controle(nossa).empty
    assert "falhas" in capsys.readouterr().out
    bucket.quebrados.clear()
    assert coletor_glm.coletar(nossa, agora).arquivos == 1


def test_o_trecho_que_falta_vira_sem_cena_e_o_que_chega_depois_entra(nossa, bucket):
    hora = pd.Timestamp("2026-11-20 18:00", tz="UTC")
    trechos = coletor_glm.trechos_da_hora(hora)
    for trecho in trechos[:50] + trechos[51:]:
        bucket.publicar(trecho.to_pydatetime())
    coletor_glm.coletar(nossa, hora + pd.Timedelta(minutes=80))
    linhas = controle(nossa)
    assert len(linhas) == 180
    perdido = linhas[linhas["status"] == banco.SEM_CENA]
    assert perdido["referencia"].tolist() == [trechos[50]] and perdido["source_key"].isna().all()
    maior = linhas["id"].max()
    bucket.publicar(trechos[50].to_pydatetime(), [CAMPO_GRANDE])
    assert coletor_glm.coletar(nossa, hora + pd.Timedelta(minutes=85)).arquivos == 1
    linhas = controle(nossa)
    chegou = linhas[linhas["referencia"] == trechos[50]].iloc[0]
    assert len(linhas) == 180 and chegou["status"] == banco.CONCLUIDO and chegou["id"] > maior


def test_o_arquivo_gravado_por_outro_processo_nao_duplica(nossa):
    _, tabela = glm.ler(arquivo([CAMPO_GRANDE]))
    assert banco.gravar_arquivo_glm(nossa, COMECO, chave(COMECO), tabela) is not None
    assert banco.gravar_arquivo_glm(nossa, COMECO, chave(COMECO), tabela) is None
    assert len(flashes(nossa)) == 1


def test_o_dia_busca_so_o_que_falta(nossa, bucket):
    agora = pd.Timestamp("2026-11-27 12:00", tz="UTC")
    primeiro = datetime(2026, 11, 26, 4, 0, tzinfo=UTC)      # 00:00 de MS
    for segundos in (0, 20, 40):
        bucket.publicar(primeiro + timedelta(seconds=segundos), [CAMPO_GRANDE])
    bucket.publicar(datetime(2026, 11, 26, 3, 59, 40, tzinfo=UTC), [CAMPO_GRANDE])   # 23:59:40 de MS, do dia 25
    _, tabela = glm.ler(arquivo([CAMPO_GRANDE], primeiro))
    banco.gravar_arquivo_glm(nossa, primeiro, chave(primeiro), tabela)
    coleta = coletor_glm.coletar_dias(nossa, [date(2026, 11, 26)], agora)
    assert coleta.arquivos == 2 and len(bucket.baixados) == 2
    concluidos = controle(nossa).query("status == @banco.CONCLUIDO")
    assert concluidos["referencia"].min() == pd.Timestamp(primeiro)
    # A hora das 04:00 UTC tem 3 dos 180 arquivos: os outros são da NOAA, que não os publicou
    assert (controle(nossa)["status"] == banco.SEM_CENA).sum() == 177


def test_coletar_py_glm_no_banco_de_teste(nossa, bucket, capsys, monkeypatch):
    agora = pd.Timestamp.now(tz="UTC").floor("min").to_pydatetime() - timedelta(minutes=1)
    bucket.publicar(agora, [CAMPO_GRANDE, LIMA])
    assert coletar.main(["glm", "--teste"]) == 0
    saida = capsys.readouterr().out
    assert "1 arquivos novos" in saida and "1 flashes no círculo, de 2 no disco" in saida


def test_a_contagem_dos_raios_le_a_glm_rt(nossa, bucket):
    from modulos import coletor_raios
    bucket.publicar(COMECO, [CAMPO_GRANDE, CAMPO_GRANDE, LIMA])
    coletor_glm.coletar(nossa, pd.Timestamp(COMECO) + pd.Timedelta(seconds=40))
    coletor_raios.coletar(nossa)
    hora = pd.Timestamp("2026-11-20 19:00", tz="UTC")
    cobertura = banco.cobertura_dos_raios(nossa, horas=[hora])
    assert cobertura[["arquivos", "flashes"]].values.tolist() == [[1, 2]]


def test_o_circulo_e_o_mesmo_do_postgis(nossa):
    pontos = pd.DataFrame({"flash_lon": [-54.62, -40.0, -68.0, -70.5, -60.0], "flash_lat": [-20.47, -10.0, -32.0, -25.0, -33.9]})
    nossos = glm.no_circulo(pontos, 1500)
    distancias = nossa.execute(
        "SELECT ST_DistanceSphere(ST_SetSRID(ST_MakePoint(lon, lat), 4326), ST_SetSRID(ST_MakePoint(%s, %s), 4326)) "
        "FROM unnest(%s::float8[], %s::float8[]) AS p(lon, lat)",
        (*config.CENTRO_DOS_RAIOS, pontos["flash_lon"].tolist(), pontos["flash_lat"].tolist())).fetchall()
    nossa.rollback()
    do_postgis = pontos[[metros <= 1_500_000 for (metros,) in distancias]]
    assert nossos["flash_lon"].tolist() == do_postgis["flash_lon"].tolist()
