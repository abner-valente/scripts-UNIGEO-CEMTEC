"""O recorte dos raios do colega a 1.500 km e a cópia para a glm_rt (ferramentas/recorte_glm.py), no schema de teste."""
import builtins
from datetime import date, datetime, timedelta, timezone

import pytest

from ferramentas import recorte_glm, trava_glm
from modulos import banco, config
from tests.conftest import SCHEMA_DE_TESTE

# As duas tabelas do colega, com todas as colunas. A fixture `conexao` vale para o módulo inteiro:
# cada teste as recria, e esvazia as nossas
TABELAS = """
DROP TABLE IF EXISTS relampagos_glm_rt, controle_processamento_glm;
CREATE TABLE controle_processamento_glm (
    id bigint PRIMARY KEY, data_hora_referencia timestamptz NOT NULL UNIQUE, source_key text UNIQUE,
    status text NOT NULL DEFAULT 'PENDENTE', tentativas integer NOT NULL DEFAULT 0, concluido_em timestamptz,
    mensagem_erro text);
CREATE TABLE relampagos_glm_rt (
    id_registro bigserial PRIMARY KEY, id_cena bigint NOT NULL REFERENCES controle_processamento_glm (id),
    flash_id bigint, time_utc timestamptz, flash_time_offset_of_first_event double precision,
    flash_time_offset_of_last_event double precision, flash_frame_time_offset_of_first_event double precision,
    flash_frame_time_offset_of_last_event double precision, flash_lat double precision, flash_lon double precision,
    flash_area double precision, flash_energy double precision, flash_quality_flag integer,
    geom geometry(Point, 4326), atributos_originais jsonb NOT NULL);
CREATE INDEX ON relampagos_glm_rt (id_cena);
TRUNCATE glm_rt, controle_glm_rt;
"""
UTC = timezone.utc
VINTE = datetime(2026, 11, 20, 12, tzinfo=UTC)
VINTE_E_UM = datetime(2026, 11, 21, tzinfo=UTC)
# A distância de Campo Grande entre parênteses
CAMPO_GRANDE = (-54.62, -20.47)    # (0 km)
PORTO_ALEGRE = (-51.23, -30.03)    # (1.116 km)
RIO = (-43.20, -22.90)             # (1.210 km)
VITORIA = (-40.30, -20.32)         # (1.492 km): dentro, por pouco
ROSARIO = (-60.64, -32.95)         # (1.510 km): fora, por pouco, mas dentro da caixa do círculo
RECIFE = (-34.88, -8.05)           # (2.532 km)
BOGOTA = (-74.07, 4.71)            # (3.514 km): fora até da caixa do círculo
SEM_CENA_DE_VERDADE = "Limite de tentativas atingido; cena não localizada no S3."


class Colega:
    # Escreve nas tabelas do colega como o script dele
    def __init__(self, conexao):
        self.conexao = conexao
        self.proximo_flash = 1

    def arquivo(self, id_: int, referencia: datetime, flashes=(), status: str = banco.CONCLUIDO,
                mensagem: str | None = None) -> None:
        chave = f"GLM-L2-LCFA/{referencia:%Y/%j/%H}/OR_GLM-L2-LCFA_G19_s{referencia:%Y%j%H%M%S}0.nc"
        concluido = referencia + timedelta(seconds=40) if status == banco.CONCLUIDO else None
        self.conexao.execute("INSERT INTO controle_processamento_glm VALUES (%s, %s, %s, %s, 1, %s, %s)",
                             (id_, referencia, chave, status, concluido, mensagem))
        for lon, lat in flashes:
            self.conexao.execute(
                "INSERT INTO relampagos_glm_rt (id_cena, flash_id, time_utc, flash_time_offset_of_first_event, "
                "flash_time_offset_of_last_event, flash_lat, flash_lon, flash_area, flash_energy, flash_quality_flag, "
                "geom, atributos_originais) VALUES (%s, %s, %s, -1.5, -1.2, %s, %s, 4.2e8, 2e-14, 0, "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326), '{\"flash_id\": 1}')",
                (id_, self.proximo_flash, referencia - timedelta(seconds=1.5), lat, lon, lon, lat))
            self.proximo_flash += 1
        self.conexao.commit()


@pytest.fixture
def colega(conexao, monkeypatch):
    monkeypatch.setattr(config, "GLM_SCHEMA", SCHEMA_DE_TESTE)
    conexao.rollback()
    conexao.execute(TABELAS)
    conexao.commit()
    return Colega(conexao)


@pytest.fixture
def dois_dias(colega):
    colega.arquivo(10, VINTE, [CAMPO_GRANDE, PORTO_ALEGRE, RIO, VITORIA, ROSARIO, RECIFE, BOGOTA])
    colega.arquivo(11, VINTE + timedelta(seconds=20), status=banco.SEM_CENA, mensagem=SEM_CENA_DE_VERDADE)
    colega.arquivo(12, VINTE + timedelta(seconds=40), status=banco.SEM_CENA,
                   mensagem=f"RaiseException: {trava_glm.MOTIVO}\nCONTEXT:  ")
    colega.arquivo(13, VINTE + timedelta(seconds=60), status="PENDENTE",
                   mensagem="Cena ainda não localizada no S3; aguardando nova tentativa.")
    colega.arquivo(20, VINTE_E_UM, [CAMPO_GRANDE, BOGOTA])
    return colega


def todos(conexao, consulta: str) -> list[tuple]:
    linhas = conexao.execute(consulta).fetchall()
    conexao.rollback()
    return linhas


def test_copiar_leva_os_flashes_ate_1500_km_com_os_mesmos_ids_e_sem_o_jsonb(conexao, dois_dias):
    assert recorte_glm.copiar(conexao, pausa=0) == (3, 5)

    copiados = todos(conexao, "SELECT g.id_registro, g.id_cena, g.flash_lon, g.flash_lat, g.time_utc, "
                              "g.flash_time_offset_of_last_event, g.flash_area, ST_X(g.geom), ST_Y(g.geom) "
                              "FROM glm_rt g ORDER BY g.id_registro")
    originais = todos(conexao, "SELECT id_registro, id_cena, flash_lon, flash_lat, time_utc, "
                               "flash_time_offset_of_last_event, flash_area, flash_lon, flash_lat "
                               "FROM relampagos_glm_rt WHERE id_registro IN (1, 2, 3, 4, 8) ORDER BY id_registro")
    assert copiados == originais     # Campo Grande, Porto Alegre, Rio, Vitória e Campo Grande do dia 21
    colunas = {nome for (nome,) in todos(conexao, "SELECT column_name FROM information_schema.columns "
                                                  f"WHERE table_schema = '{SCHEMA_DE_TESTE}' AND table_name = 'glm_rt'")}
    assert "atributos_originais" not in colunas and "geom" in colunas


def test_copiar_leva_os_arquivos_menos_os_que_bateram_na_trava_e_os_pendentes(conexao, dois_dias):
    recorte_glm.copiar(conexao, pausa=0)

    assert todos(conexao, "SELECT id, status, mensagem_erro FROM controle_glm_rt ORDER BY id") == [
        (10, banco.CONCLUIDO, None), (11, banco.SEM_CENA, SEM_CENA_DE_VERDADE), (20, banco.CONCLUIDO, None)]


def test_copiar_de_novo_nao_duplica_e_traz_o_que_chegou_depois(conexao, dois_dias):
    recorte_glm.copiar(conexao, pausa=0)
    assert recorte_glm.copiar(conexao, pausa=0) == (0, 0)

    dois_dias.arquivo(14, VINTE + timedelta(seconds=80), [PORTO_ALEGRE, RECIFE])
    assert recorte_glm.copiar(conexao, pausa=0) == (1, 1)
    assert todos(conexao, "SELECT count(*) FROM glm_rt") == [(6,)]


def test_copiar_cede_a_referencia_que_o_nosso_coletor_ja_gravou(conexao, dois_dias):
    conexao.execute("INSERT INTO controle_glm_rt (data_hora_referencia, source_key, status) VALUES (%s, 'nossa', %s)",
                    (VINTE, banco.CONCLUIDO))
    conexao.commit()

    assert recorte_glm.copiar(conexao, pausa=0) == (2, 1)
    assert todos(conexao, "SELECT DISTINCT id_cena FROM glm_rt") == [(20,)]
    (nosso,) = todos(conexao, "SELECT id FROM controle_glm_rt WHERE source_key = 'nossa'")[0]
    assert nosso >= 1_000_000_000    # os ids do nosso coletor começam em 1 bilhão


def test_copiar_pede_o_esquema_antes(conexao, colega):
    conexao.execute("DROP TABLE glm_rt, controle_glm_rt")
    conexao.commit()
    try:
        with pytest.raises(banco.ErroBanco, match="coletar.py esquema"):
            recorte_glm.copiar(conexao, pausa=0)
    finally:
        banco.criar_esquema(conexao)


def test_cortar_simulando_so_conta(conexao, dois_dias):
    assert recorte_glm.cortar(conexao, simular=True, pausa=0) == (9, 4)
    assert todos(conexao, "SELECT count(*) FROM relampagos_glm_rt") == [(9,)]


def test_cortar_apaga_so_o_que_esta_alem_de_1500_km_mesmo_com_a_trava_ligada(conexao, dois_dias):
    trava_glm.parar(conexao, SCHEMA_DE_TESTE)

    assert recorte_glm.cortar(conexao, pausa=0) == (0, 4)
    assert todos(conexao, "SELECT id_registro FROM relampagos_glm_rt ORDER BY 1") == [(1,), (2,), (3,), (4,), (8,)]
    assert todos(conexao, "SELECT count(*) FROM controle_processamento_glm") == [(5,)]
    # Cortar de novo não acha mais nada, e o corte de um dia só
    assert recorte_glm.cortar(conexao, pausa=0) == (0, 0)
    assert recorte_glm.cortar(conexao, simular=True, desde=date(2026, 11, 21), pausa=0) == (1, 0)


def test_estimar_pela_amostra(conexao, dois_dias):
    conexao.execute("ANALYZE relampagos_glm_rt")
    conexao.commit()

    estimativa = recorte_glm.estimar(conexao, amostra=100)

    assert (estimativa["amostra"], estimativa["dentro"], estimativa["linhas"]) == (9, 5, 9)
    assert (estimativa["ficam"], estimativa["saem"]) == (5, 4)
    assert 100 < estimativa["bytes_por_flash"] < 250     # sem o jsonb
    assert list(estimativa["semanas"]["flashes"]) == [9]


def test_cortar_pede_confirmacao_antes_de_conectar(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda pergunta: "sim")
    monkeypatch.setattr(banco, "conectar", lambda *args, **kwargs: pytest.fail("conectou sem confirmação"))

    assert recorte_glm.main(["cortar"]) == 1
    assert "Nada foi apagado" in capsys.readouterr().out


def test_os_argumentos():
    opcoes = recorte_glm.argumentos(["cortar", "--simular", "--desde", "2026-10-15", "--pausa", "0"])
    assert (opcoes.acao, opcoes.simular, opcoes.desde, opcoes.pausa) == ("cortar", True, date(2026, 10, 15), 0)
    assert recorte_glm.argumentos(["copiar"]).desde is None


def test_compactar_reescreve_a_tabela_com_os_flashes_que_ficaram(conexao, dois_dias):
    recorte_glm.cortar(conexao, pausa=0)
    conexao.execute("ANALYZE relampagos_glm_rt")
    conexao.commit()
    tamanho = recorte_glm.tamanho(conexao)
    assert tamanho["vivos"] == 5
    assert tamanho["total"] >= tamanho["dados"] > 0
    assert 0 < tamanho["total_depois"] <= tamanho["total"]

    recorte_glm.compactar(conexao)

    assert not conexao.autocommit
    assert todos(conexao, "SELECT count(*) FROM relampagos_glm_rt") == [(5,)]


def test_compactar_pede_confirmacao_antes_de_gravar(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda pergunta: "sim")
    papeis = []

    class Conexao:
        def __enter__(self):
            return self

        def __exit__(self, *erro):
            return None

    def conectar(papel="leitura", **argumentos):
        papeis.append(papel)
        return Conexao()

    monkeypatch.setattr(banco, "conectar", conectar)
    monkeypatch.setattr(recorte_glm, "tamanho", lambda conexao: dict.fromkeys(
        ("total", "dados", "indices", "vivos", "dados_depois", "indices_depois", "total_depois"), 2**30))

    assert recorte_glm.main(["compactar"]) == 1
    assert papeis == ["leitura"]
    assert "Nada foi feito" in capsys.readouterr().out
    assert recorte_glm.argumentos(["compactar", "--simular"]).simular
