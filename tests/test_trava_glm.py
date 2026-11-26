"""A trava da gravação na tabela de raios do colega (ferramentas/trava_glm.py), no schema de teste."""
import psycopg
import pytest

from ferramentas import trava_glm
from tests.conftest import SCHEMA_DE_TESTE

# A fixture `conexao` vale para o módulo inteiro: cada teste recria as duas tabelas, sem gatilho
TABELAS = """
DROP TABLE IF EXISTS controle_processamento_glm, relampagos_glm_rt;
CREATE TABLE controle_processamento_glm (id bigint PRIMARY KEY, status text, concluido_em timestamptz, mensagem_erro text);
CREATE TABLE relampagos_glm_rt (id_registro bigserial PRIMARY KEY, id_cena bigint, flash_lat double precision);
"""


@pytest.fixture
def glm(conexao):
    conexao.rollback()
    conexao.execute(TABELAS)
    conexao.commit()
    return conexao


def gravar(conexao, cena: int) -> None:
    conexao.execute("INSERT INTO relampagos_glm_rt (id_cena, flash_lat) VALUES (%s, -20.4)", (cena,))
    conexao.commit()


def test_parar_recusa_a_gravacao_mas_nao_a_leitura(glm):
    gravar(glm, 1)
    trava_glm.parar(glm, SCHEMA_DE_TESTE)

    with pytest.raises(psycopg.errors.RaiseException, match="suspensa pelo CEMTEC"):
        gravar(glm, 2)
    glm.rollback()
    assert glm.execute("SELECT count(*) FROM relampagos_glm_rt").fetchone()[0] == 1
    assert trava_glm.estado(glm, SCHEMA_DE_TESTE)["trava"] == "ligada"


def test_a_recusa_vale_tambem_para_o_copy(glm):
    trava_glm.parar(glm, SCHEMA_DE_TESTE)
    with pytest.raises(psycopg.errors.RaiseException):
        with glm.cursor().copy("COPY relampagos_glm_rt (id_cena, flash_lat) FROM STDIN") as copia:
            copia.write_row((3, -20.4))
    glm.rollback()


def test_retomar_desliga_e_parar_de_novo_religa(glm):
    trava_glm.parar(glm, SCHEMA_DE_TESTE)
    trava_glm.retomar(glm, SCHEMA_DE_TESTE)
    gravar(glm, 4)
    assert trava_glm.estado(glm, SCHEMA_DE_TESTE)["trava"] == "desligada"

    trava_glm.parar(glm, SCHEMA_DE_TESTE)
    with pytest.raises(psycopg.errors.RaiseException):
        gravar(glm, 5)
    glm.rollback()
    assert trava_glm.estado(glm, SCHEMA_DE_TESTE)["trava"] == "ligada"


def test_estado_sem_trava_e_com_os_numeros_da_tabela(glm):
    gravar(glm, 6)
    glm.execute("INSERT INTO controle_processamento_glm VALUES (1, 'PENDENTE', NULL, %s)", (trava_glm.MOTIVO,))
    glm.commit()
    agora = trava_glm.estado(glm, SCHEMA_DE_TESTE)
    assert (agora["trava"], agora["recusados"], agora["ultimo_arquivo"]) == ("não existe", 1, None)
    assert agora["ultimo_id"] >= 1


def test_espera_a_gravacao_em_curso_sem_ficar_na_fila(glm, monkeypatch):
    # Uma gravação aberta em outra conexão segura a tabela: a trava desiste a cada ESPERA e tenta de novo
    monkeypatch.setattr(trava_glm, "ESPERA", "100ms")
    monkeypatch.setattr(trava_glm, "TENTATIVAS", 2)
    monkeypatch.setattr(trava_glm.time, "sleep", lambda segundos: None)
    with psycopg.connect(glm.info.dsn, password=glm.info.password,
                         options=f"-c search_path={SCHEMA_DE_TESTE}") as outra:
        outra.execute("INSERT INTO relampagos_glm_rt (id_cena, flash_lat) VALUES (7, -20.4)")
        with pytest.raises(trava_glm.banco.ErroBanco, match="ocupada"):
            trava_glm.parar(glm, SCHEMA_DE_TESTE)
        outra.rollback()
    trava_glm.parar(glm, SCHEMA_DE_TESTE)
    assert trava_glm.estado(glm, SCHEMA_DE_TESTE)["trava"] == "ligada"
