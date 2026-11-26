"""Trava a gravação na tabela de raios do colega (relampagos_glm_rt), sem renomeá-la.

Pedido de 26/11/2026: o coletor do colega grava o disco inteiro do GOES-19, e não só a região. Em dois
meses foram 104 milhões de flashes e 69 GB, e a carga do histórico, que volta até 2018, soma uns 7 GB
por dia. Ele está de férias e o script dele não pode ser mudado agora. A tabela vai ser recortada a
2.500 km de Campo Grande; até lá, a gravação para.

A trava é um gatilho BEFORE INSERT, por comando, que recusa a gravação com um erro. Para o coletor
do colega é como se o banco recusasse o INSERT: o arquivo não entra e fica para outra tentativa. Por
que não renomear a tabela:

- renomear pede trava exclusiva, que espera qualquer leitura longa (um pg_dump, uma sessão do
  ArcGIS) e, enquanto espera, bloqueia todas as outras leituras; criar ou ligar o gatilho não
  espera as leituras, só as gravações em curso;
- o painel e o nosso coletor de raios leem a tabela pelo nome e continuam lendo;
- um script que cria a tabela quando não a encontra criaria outra, vazia, e seguiria gravando.

Quem roda é o usuário, com o papel de gravação (o dono da tabela). Uso, da raiz do projeto:

    python ferramentas/trava_glm.py estado     # a trava, o tamanho, e se ainda entra flash
    python ferramentas/trava_glm.py parar      # liga a trava
    python ferramentas/trava_glm.py retomar    # desliga a trava (o gatilho fica, desligado)
"""
import argparse
import sys
import time
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modulos import banco, config  # noqa: E402

TABELA = "relampagos_glm_rt"
CONTROLE = "controle_processamento_glm"
GATILHO = "trava_da_gravacao_glm"
FUNCAO = "recusar_gravacao_glm"
MOTIVO = ("Gravação em relampagos_glm_rt suspensa pelo CEMTEC em 26/11/2026: a tabela vai ser recortada "
          "a 2.500 km de Campo Grande. Para voltar: python ferramentas/trava_glm.py retomar")
# Ligar e desligar o gatilho esperam as gravações em curso (um INSERT do coletor dura segundos). A
# cada tentativa, no máximo ESPERA; se não der, desiste e tenta de novo, para não ficar na fila
# bloqueando quem chegar depois
ESPERA = "5s"
TENTATIVAS = 12


def _com_paciencia(conexao: psycopg.Connection, passos) -> None:
    # Sem transação aberta antes (a leitura do gatilho abre uma): assim a de baixo é de verdade, e não
    # um savepoint que um rollback depois desfaria
    conexao.commit()
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            with conexao.transaction():
                conexao.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(ESPERA)))
                for passo in passos:
                    conexao.execute(passo)
            return
        except psycopg.errors.LockNotAvailable:
            print(f"  a tabela estava ocupada por uma gravação (tentativa {tentativa} de {TENTATIVAS}); de novo em 5 s")
            time.sleep(5)
    raise banco.ErroBanco(f"a tabela ficou ocupada nas {TENTATIVAS} tentativas: rode de novo daqui a pouco")


def _gatilho(conexao: psycopg.Connection, schema: str) -> str | None:
    # "O" é ligado, "D" é desligado; None, sem gatilho
    linha = conexao.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = %s AND tgrelid = %s::regclass",
                            (GATILHO, f"{schema}.{TABELA}")).fetchone()
    return linha[0] if linha else None


def parar(conexao: psycopg.Connection, schema: str = config.GLM_SCHEMA) -> None:
    tabela, funcao = sql.Identifier(schema, TABELA), sql.Identifier(schema, FUNCAO)
    gatilho = sql.Identifier(GATILHO)
    recusa = sql.SQL("CREATE OR REPLACE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                     "BEGIN RAISE EXCEPTION {}; END $$").format(funcao, sql.Literal(MOTIVO))
    if _gatilho(conexao, schema) is None:
        passos = [recusa, sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON {} FOR EACH STATEMENT EXECUTE FUNCTION {}()")
                  .format(gatilho, tabela, funcao)]
    else:
        passos = [recusa, sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(tabela, gatilho)]
    _com_paciencia(conexao, passos)


def retomar(conexao: psycopg.Connection, schema: str = config.GLM_SCHEMA) -> None:
    # Desligar, e não apagar: apagar o gatilho pede trava exclusiva, desligar não
    if _gatilho(conexao, schema) is not None:
        _com_paciencia(conexao, [sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}")
                                 .format(sql.Identifier(schema, TABELA), sql.Identifier(GATILHO))])


def estado(conexao: psycopg.Connection, schema: str = config.GLM_SCHEMA) -> dict:
    tabela, controle = f"{schema}.{TABELA}", sql.Identifier(schema, CONTROLE)
    situacao = {"O": "ligada", "D": "desligada"}.get(_gatilho(conexao, schema), "não existe")
    tamanho, linhas = conexao.execute("SELECT pg_size_pretty(pg_total_relation_size(%s::regclass)), "
                                      "(SELECT reltuples::bigint FROM pg_class WHERE oid = %s::regclass)",
                                      (tabela, tabela)).fetchone()
    # O maior id sai do índice da chave, na hora: se crescer entre duas consultas, ainda entra flash
    ultimo_id = conexao.execute(sql.SQL("SELECT max(id_registro) FROM {}").format(sql.Identifier(schema, TABELA))).fetchone()[0]
    ultimo_arquivo, recusados = conexao.execute(
        sql.SQL("SELECT max(concluido_em), count(*) FILTER (WHERE mensagem_erro LIKE %s) FROM {}").format(controle),
        ("%suspensa pelo CEMTEC%",)).fetchone()
    conexao.rollback()
    return {"trava": situacao, "tamanho": tamanho, "linhas": linhas, "ultimo_id": ultimo_id,
            "ultimo_arquivo": ultimo_arquivo, "recusados": recusados}


def main(argv=None) -> int:
    partes = argparse.ArgumentParser(description="Trava a gravação na tabela de raios do colega.")
    partes.add_argument("acao", choices=("estado", "parar", "retomar"))
    acao = partes.parse_args(argv).acao
    with banco.conectar("leitura" if acao == "estado" else "gravacao") as conexao:
        if acao == "parar":
            parar(conexao)
            print("Trava ligada: o INSERT no relampagos_glm_rt agora é recusado.")
        elif acao == "retomar":
            retomar(conexao)
            print("Trava desligada: o relampagos_glm_rt volta a aceitar gravação.")
        agora = estado(conexao)
    print(f"trava: {agora['trava']} · tabela: {agora['tamanho']}, ~{agora['linhas']:,} flashes · "
          f"maior id: {agora['ultimo_id'] or 0:,}")
    ultimo = f"{agora['ultimo_arquivo']:%d/%m/%Y %H:%M} UTC" if agora["ultimo_arquivo"] else "nenhum"
    print(f"último arquivo concluído pelo coletor do colega: {ultimo} · arquivos que bateram na trava: "
          f"{agora['recusados']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
