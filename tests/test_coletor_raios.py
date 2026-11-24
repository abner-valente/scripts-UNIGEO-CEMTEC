"""Os raios do GLM: as leituras das tabelas do colega, a contagem por hora e célula e o coletor.

As duas tabelas do colega são imitadas no schema de teste, só com as colunas que o projeto lê. Os
testes que usam o banco são pulados sem ele (a fixture `conexao`, no conftest.py).
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import coletar
from modulos import banco, coletor_raios, config

# O schema do banco de teste (o mesmo da fixture `conexao`), onde ficam também as tabelas imitadas
SCHEMA_DE_TESTE = coletar.SCHEMA_DE_TESTE

ESQUEMA_DO_GLM = """
CREATE TABLE IF NOT EXISTS controle_processamento_glm (
    id bigint PRIMARY KEY,
    data_hora_referencia timestamptz NOT NULL UNIQUE,
    status text NOT NULL,
    concluido_em timestamptz
);
CREATE TABLE IF NOT EXISTS relampagos_glm_rt (
    id_registro bigserial PRIMARY KEY,
    id_cena bigint NOT NULL,
    time_utc timestamptz,
    flash_lat double precision,
    flash_lon double precision,
    flash_quality_flag integer
);
CREATE INDEX IF NOT EXISTS relampagos_glm_rt_cena ON relampagos_glm_rt (id_cena);
"""
UTC = timezone.utc
# O começo da hora das 14:00 (a de 13:00 a 14:00 UTC)
TREZE = datetime(2026, 11, 18, 13, tzinfo=UTC)
CAMPO_GRANDE = (-54.57, -20.43)    # a célula (-546, -205)
DOURADOS = (-54.81, -22.22)        # a célula (-549, -223)
CUIABA = (-56.10, -15.60)          # fora da caixa (ao norte)


def hora(texto: str) -> pd.Timestamp:
    return pd.Timestamp(f"2026-11-18 {texto}", tz="UTC")


class GLM:
    # Escreve arquivos de 20 s nas tabelas imitadas, como o script do colega
    def __init__(self, conexao):
        self.conexao = conexao
        self.proximo_id = 1

    def arquivo(self, referencia: datetime, flashes=(), status: str = banco.CONCLUIDO, id_: int | None = None) -> int:
        id_ = id_ if id_ is not None else self.proximo_id
        self.proximo_id = max(self.proximo_id, id_) + 1
        self.conexao.execute("INSERT INTO controle_processamento_glm VALUES (%s, %s, %s, now())",
                             (id_, referencia, status))
        self.flashes(id_, referencia, flashes)
        self.conexao.commit()
        return id_

    def flashes(self, id_: int, referencia: datetime, flashes) -> None:
        # Cada flash: (longitude, latitude) ou (longitude, latitude, segundos depois da referência)
        for flash in flashes:
            lon, lat, segundos = (*flash, 5.0) if len(flash) == 2 else flash
            self.conexao.execute("INSERT INTO relampagos_glm_rt (id_cena, time_utc, flash_lat, flash_lon, "
                                 "flash_quality_flag) VALUES (%s, %s, %s, %s, 0)",
                                 (id_, referencia + timedelta(seconds=segundos), lat, lon))

    def concluir(self, id_: int, referencia: datetime, flashes=()) -> None:
        self.conexao.execute("UPDATE controle_processamento_glm SET status = %s WHERE id = %s", (banco.CONCLUIDO, id_))
        self.flashes(id_, referencia, flashes)
        self.conexao.commit()


@pytest.fixture
def glm(conexao, monkeypatch):
    monkeypatch.setattr(config, "GLM_SCHEMA", SCHEMA_DE_TESTE)
    conexao.execute(ESQUEMA_DO_GLM)
    conexao.execute("TRUNCATE controle_processamento_glm, relampagos_glm_rt, raios_glm_hora, raios_glm_cobertura, "
                    "raios_glm_coleta")
    conexao.commit()
    return GLM(conexao)


def contagem(conexao) -> dict[tuple, int]:
    linhas = conexao.execute("SELECT hora_utc, lon_celula, lat_celula, flashes FROM raios_glm_hora").fetchall()
    conexao.rollback()
    return {(pd.Timestamp(h).tz_convert("UTC"), x, y): n for h, x, y, n in linhas}


def cobertura(conexao) -> dict[pd.Timestamp, tuple[int, int, int]]:
    tabela = banco.cobertura_dos_raios(conexao, TREZE - timedelta(days=1), TREZE + timedelta(days=1))
    return {linha.hora_utc: (linha.arquivos, linha.sem_arquivo, linha.flashes) for linha in tabela.itertuples()}


# =====================================================
# AS CONTAS, SEM BANCO
# =====================================================
def test_o_arquivo_vai_para_a_hora_que_termina_depois_dele():
    referencias = pd.Series([hora("13:00:00"), hora("13:59:40"), hora("14:00:00")])
    assert list(coletor_raios.hora_do_arquivo(referencias)) == [hora("14:00"), hora("14:00"), hora("15:00")]


def test_as_horas_seguidas_viram_trechos_de_ate_tantas_horas():
    horas = [hora("01:00"), hora("02:00"), hora("03:00"), hora("05:00"), hora("06:00"), hora("09:00")]
    assert coletor_raios.trechos(horas, maximo=2) == [
        (hora("00:00"), hora("02:00")), (hora("02:00"), hora("03:00")), (hora("04:00"), hora("06:00")),
        (hora("08:00"), hora("09:00"))]
    assert coletor_raios.trechos(horas[:3], maximo=12) == [(hora("00:00"), hora("03:00"))]
    assert coletor_raios.trechos([]) == []


def test_reconta_so_a_hora_cujos_arquivos_mudaram():
    atuais = pd.DataFrame({"hora_utc": [hora("01:00"), hora("02:00"), hora("03:00"), hora("04:00")],
                           "arquivos": [180, 179, 100, 0], "sem_arquivo": [0, 1, 0, 0]})
    guardadas = pd.DataFrame({"hora_utc": [hora("01:00"), hora("02:00"), hora("03:00")],
                              "arquivos": [180, 180, 100], "sem_arquivo": [0, 0, 0], "flashes": [5, 5, 5]})
    assert coletor_raios.a_recontar(atuais, guardadas) == [hora("02:00"), hora("04:00")]


# =====================================================
# A CONTAGEM NO BANCO
# =====================================================
def test_conta_por_hora_e_celula_so_dentro_da_caixa(glm, conexao):
    glm.arquivo(TREZE, [CAMPO_GRANDE, CAMPO_GRANDE, DOURADOS, CUIABA])
    glm.arquivo(TREZE + timedelta(minutes=59, seconds=40), [CAMPO_GRANDE])
    glm.arquivo(TREZE + timedelta(hours=1), [DOURADOS])

    coleta = coletor_raios.coletar(conexao)

    assert contagem(conexao) == {(hora("14:00"), -546, -205): 3, (hora("14:00"), -549, -223): 1,
                                 (hora("15:00"), -549, -223): 1}
    assert coleta.arquivos_novos == 3
    assert coleta.horas == [hora("14:00"), hora("15:00")]
    assert coleta.flashes == 5
    assert cobertura(conexao) == {hora("14:00"): (2, 0, 4), hora("15:00"): (1, 0, 1)}
    assert banco.ultimo_id_dos_raios(conexao) == 3


def test_o_flash_que_comecou_antes_do_arquivo_fica_na_hora_do_arquivo(glm, conexao):
    # O GLM junta o flash ao arquivo em que ele termina: este começou meio segundo antes das 14:00
    glm.arquivo(TREZE + timedelta(hours=1), [(*CAMPO_GRANDE, -0.5)])
    coletor_raios.coletar(conexao)
    assert contagem(conexao) == {(hora("15:00"), -546, -205): 1}


def test_rodar_de_novo_sem_arquivo_novo_nao_reconta_nada(glm, conexao):
    glm.arquivo(TREZE, [CAMPO_GRANDE])
    coletor_raios.coletar(conexao)
    coleta = coletor_raios.coletar(conexao)
    assert coleta.arquivos_novos == 0
    assert coleta.horas == []
    assert contagem(conexao) == {(hora("14:00"), -546, -205): 1}


def test_o_arquivo_novo_reconta_a_hora_inteira_sem_duplicar(glm, conexao):
    glm.arquivo(TREZE, [CAMPO_GRANDE])
    glm.arquivo(TREZE + timedelta(hours=1), [DOURADOS])
    coletor_raios.coletar(conexao)

    glm.arquivo(TREZE + timedelta(minutes=20), [CAMPO_GRANDE, DOURADOS])
    coleta = coletor_raios.coletar(conexao)

    assert coleta.horas == [hora("14:00")]
    assert contagem(conexao) == {(hora("14:00"), -546, -205): 2, (hora("14:00"), -549, -223): 1,
                                 (hora("15:00"), -549, -223): 1}
    assert cobertura(conexao)[hora("14:00")] == (2, 0, 3)


def test_o_historico_que_chega_de_tras_para_frente_entra(glm, conexao):
    glm.arquivo(TREZE + timedelta(hours=1), [DOURADOS])
    coletor_raios.coletar(conexao)
    # O colega preenche o passado depois do tempo real: o id é maior, a hora é mais velha
    glm.arquivo(TREZE - timedelta(hours=5), [CAMPO_GRANDE])
    coleta = coletor_raios.coletar(conexao)
    assert coleta.horas == [hora("09:00")]
    assert contagem(conexao)[(hora("09:00"), -546, -205)] == 1


def test_o_arquivo_pendente_que_conclui_depois_reconta_a_hora(glm, conexao):
    pendente = glm.arquivo(TREZE, status="PENDENTE")
    glm.arquivo(TREZE + timedelta(seconds=20), [DOURADOS])
    glm.arquivo(TREZE + timedelta(seconds=40), status=banco.SEM_CENA)
    coletor_raios.coletar(conexao)
    assert cobertura(conexao)[hora("14:00")] == (1, 1, 1)
    assert banco.horas_de_raios_incompletas(conexao) == [hora("14:00")]

    # O id do pendente já ficou para trás: ele volta pela hora incompleta
    glm.concluir(pendente, TREZE, [CAMPO_GRANDE, CAMPO_GRANDE])
    coleta = coletor_raios.coletar(conexao)

    assert coleta.arquivos_novos == 0
    assert coleta.horas == [hora("14:00")]
    assert cobertura(conexao)[hora("14:00")] == (2, 1, 3)
    assert contagem(conexao)[(hora("14:00"), -546, -205)] == 2


def test_a_hora_completa_sai_da_lista_das_incompletas(glm, conexao):
    for n in range(config.ARQUIVOS_GLM_POR_HORA - 1):
        glm.arquivo(TREZE + timedelta(seconds=20 * n))
    glm.arquivo(TREZE + timedelta(seconds=20 * 179), status=banco.SEM_CENA)
    coletor_raios.coletar(conexao)
    assert banco.horas_de_raios_incompletas(conexao) == []
    assert cobertura(conexao)[hora("14:00")] == (179, 1, 0)


def test_cada_trecho_e_gravado_inteiro(glm, conexao, monkeypatch):
    monkeypatch.setattr(coletor_raios, "HORAS_POR_CONSULTA", 1)
    for horas in range(3):
        glm.arquivo(TREZE + timedelta(hours=horas), [CAMPO_GRANDE])
    coleta = coletor_raios.coletar(conexao)
    assert coleta.horas == [hora("14:00"), hora("15:00"), hora("16:00")]
    assert sorted(cobertura(conexao)) == coleta.horas


def test_coletar_py_raios_no_banco_de_teste(glm, conexao, capsys):
    glm.arquivo(TREZE, [CAMPO_GRANDE])
    assert coletar.main(["raios", "--teste"]) == 0
    saida = capsys.readouterr().out
    assert "18/11 13:00 a 18/11 14:00 UTC: 1 h, 1 flashes" in saida
    assert "1 horas recontadas, 1 flashes" in saida
    assert "o banco tem de 18/11/2026 13:00 a 18/11/2026 14:00 UTC" in saida


# =====================================================
# AS LEITURAS DO PAINEL
# =====================================================
def test_os_flashes_recentes_vem_pelo_horario_de_cada_um(glm, conexao):
    glm.arquivo(TREZE, [(*CAMPO_GRANDE, 5), CUIABA])
    # Começou antes das 13:00:20 e veio no arquivo seguinte: entra pelo horário dele
    glm.arquivo(TREZE + timedelta(seconds=20), [(*DOURADOS, -1), (*DOURADOS, 10)])
    glm.arquivo(TREZE + timedelta(minutes=10), [CAMPO_GRANDE])

    recentes = banco.ler_raios_recentes(conexao, TREZE + timedelta(seconds=10), TREZE + timedelta(minutes=10))

    assert list(recentes["hora_utc"]) == [hora("13:00:19"), hora("13:00:30")]
    assert recentes[["longitude", "latitude"]].round(2).values.tolist() == [list(DOURADOS), list(DOURADOS)]


def test_o_ultimo_arquivo_processado(glm, conexao):
    glm.arquivo(TREZE)
    glm.arquivo(TREZE + timedelta(seconds=20))
    glm.arquivo(TREZE + timedelta(seconds=40), status="PENDENTE")
    assert banco.ultimo_arquivo_do_glm(conexao, TREZE - timedelta(days=1)) == hora("13:00:20")
    assert banco.ultimo_arquivo_do_glm(conexao, TREZE + timedelta(hours=1)) is None


def test_a_soma_por_celula_e_o_alcance(glm, conexao):
    glm.arquivo(TREZE, [CAMPO_GRANDE, DOURADOS])
    glm.arquivo(TREZE + timedelta(hours=1), [CAMPO_GRANDE])
    glm.arquivo(TREZE + timedelta(hours=2), [CAMPO_GRANDE])
    coletor_raios.coletar(conexao)

    somas = banco.ler_raios_por_celula(conexao, TREZE, TREZE + timedelta(hours=2))
    assert somas.values.tolist() == [[-549, -223, 1], [-546, -205, 2]]
    assert banco.alcance_dos_raios(conexao) == (hora("14:00"), hora("16:00"))
    assert list(banco.cobertura_dos_raios(conexao, TREZE, TREZE + timedelta(hours=1))["hora_utc"]) == [hora("14:00")]


def test_sem_nada_contado_o_alcance_e_vazio(glm, conexao):
    assert banco.alcance_dos_raios(conexao) == (None, None)
    assert banco.ler_raios_por_celula(conexao, TREZE, TREZE + timedelta(hours=1)).empty
