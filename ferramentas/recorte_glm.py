"""Recorta os raios do colega a 1.500 km de Campo Grande e copia o que fica para a nossa tabela, a glm_rt.

Pedido de 26/11/2026. A tabela do colega (relampagos_glm_rt, no schema config.GLM_SCHEMA) guarda o
disco inteiro do GOES-19: em dois meses, 104 milhões de flashes e 69 GB, três quartos deles numa
coluna jsonb que repete as outras. A gravação nela está parada (ferramentas/trava_glm.py). Aqui:

- estimar: quanto fica e quanto sai, por uma amostra de blocos da tabela. Só lê;
- copiar: leva para a glm_rt e o controle_glm_rt (banco/esquema.sql, no schema do projeto) os
  flashes a até config.RAIO_DA_TABELA_GLM_KM, sem o jsonb e com os mesmos ids, e os arquivos do
  controle. Ficam de fora os pendentes e os que bateram na trava (o script do colega os encerrou
  como "sem cena", mas a NOAA os tem): o nosso coletor os busca. Pode rodar de novo: só copia o que
  falta;
- cortar: apaga da tabela do colega os flashes além do raio. Com --simular, só conta.

As gravações vão dia a dia, um dia por transação, pela rota dos raios: os arquivos do dia no
controle, e os flashes deles pelo id_cena (a tabela não tem índice no horário). Quem lê a tabela
enquanto isso não espera nada. A ordem entre copiar e cortar não muda o que vai para a glm_rt.

O corte não diminui o arquivo da tabela: o espaço apagado fica para ela mesma reaproveitar. Devolver
o disco pede um VACUUM FULL, que bloqueia até a leitura enquanto roda (combinar com o colega).

Quem roda o copiar e o cortar é o usuário, com o papel de gravação (o dono da tabela). Da raiz do projeto:

    python coletar.py esquema                            # antes de copiar: cria a glm_rt e o controle_glm_rt
    python ferramentas/recorte_glm.py estimar
    python ferramentas/recorte_glm.py copiar
    python ferramentas/recorte_glm.py cortar --simular
    python ferramentas/recorte_glm.py cortar
    python ferramentas/recorte_glm.py cortar --desde 2026-10-15   # retoma a partir de um dia
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as hora
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modulos import banco, config  # noqa: E402

FLASHES, CONTROLE = "relampagos_glm_rt", "controle_processamento_glm"
NOSSOS_FLASHES, NOSSO_CONTROLE = "glm_rt", "controle_glm_rt"
COLUNAS_DOS_FLASHES = ["id_registro", "id_cena", "flash_id", "time_utc", "flash_time_offset_of_first_event",
                       "flash_time_offset_of_last_event", "flash_frame_time_offset_of_first_event",
                       "flash_frame_time_offset_of_last_event", "flash_lat", "flash_lon", "flash_area",
                       "flash_energy", "flash_quality_flag"]
COLUNAS_DO_CONTROLE = ["id", "data_hora_referencia", "source_key", "status", "tentativas", "concluido_em",
                       "mensagem_erro"]
# Os arquivos recusados pela trava levam a mensagem dela (trava_glm.MOTIVO)
RECUSADOS = "%suspensa pelo CEMTEC%"
AMOSTRA = 0.3    # % dos blocos, no estimar: ~300 mil flashes em ~40 s (26/11/2026)
# Os índices da glm_rt, por flash: a chave e o horário como a chave do colega (21,5 bytes), o id_cena
# como o dele (8,6 bytes, que repete muito). Medido nos índices da tabela dele em 26/11/2026
BYTES_DE_INDICE_POR_FLASH = 52
LIMITE_DO_DIA = "15min"   # um dia tem até ~1,7 milhão de flashes, lidos em ~25 s
# Uma trava pesada na tabela (um ALTER, um VACUUM FULL) faria o dia esperar na fila e bloquear quem
# chegasse depois: a cada tentativa, no máximo ESPERA, e de novo daqui a pouco
ESPERA = "5s"
TENTATIVAS = 12
PAUSA_NA_TRAVA = 5        # segundos
# Entre um dia e o seguinte, para o servidor dar conta do log das transações (o WAL): apagar um dia
# reescreve ~1 GB de páginas da tabela
PAUSA = 5.0               # segundos
UM_DIA = timedelta(days=1)


def _dele(tabela: str) -> sql.Identifier:
    return sql.Identifier(config.GLM_SCHEMA, tabela)


def _circulo() -> dict:
    return banco.circulo_dos_raios(config.RAIO_DA_TABELA_GLM_KM)


def _n(numero: float) -> str:
    return f"{numero:,.0f}".replace(",", ".")


def _pct(fracao: float, casas: int = 0) -> str:
    return f"{fracao:.{casas}%}".replace(".", ",")


def _gb(bytes_: float) -> str:
    # Em GiB, como o pg_size_pretty do PostgreSQL
    return f"{bytes_ / 2**30:.1f}".replace(".", ",") + " GB"


def _limites(dia: date) -> dict:
    de = datetime.combine(dia, hora.min, tzinfo=timezone.utc)
    return {"de": de, "ate": de + UM_DIA}


def _preparar(conexao: psycopg.Connection) -> None:
    conexao.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(LIMITE_DO_DIA)))
    conexao.execute(sql.SQL("SET lock_timeout = {}").format(sql.Literal(ESPERA)))
    conexao.commit()


def _com_paciencia(conexao: psycopg.Connection, funcao, *argumentos):
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            return funcao(conexao, *argumentos)
        except psycopg.errors.LockNotAvailable:
            conexao.rollback()
            print(f"   a tabela estava travada por outra sessão (tentativa {tentativa} de {TENTATIVAS}); "
                  f"de novo em {PAUSA_NA_TRAVA} s", flush=True)
            time.sleep(PAUSA_NA_TRAVA)
    raise banco.ErroBanco(f"a tabela ficou travada nas {TENTATIVAS} tentativas: rode de novo daqui a pouco "
                          "(com --desde, para retomar do dia em que parou)")


def dias(conexao: psycopg.Connection, desde: date | None = None) -> list[date]:
    # Do primeiro ao último dia (UTC) do controle do colega. O índice da referência dá os dois na hora
    primeira, ultima = conexao.execute(sql.SQL("SELECT min(data_hora_referencia), max(data_hora_referencia) "
                                               "FROM {}").format(_dele(CONTROLE))).fetchone()
    conexao.rollback()
    if primeira is None:
        return []
    primeiro, ultimo = (momento.astimezone(timezone.utc).date() for momento in (primeira, ultima))
    primeiro = max(primeiro, desde) if desde else primeiro
    return [primeiro + timedelta(days=n) for n in range((ultimo - primeiro).days + 1)]


def _cenas(conexao: psycopg.Connection, dia: date) -> list[int]:
    linhas = conexao.execute(sql.SQL("SELECT id FROM {} WHERE data_hora_referencia >= %(de)s "
                                     "AND data_hora_referencia < %(ate)s").format(_dele(CONTROLE)),
                             _limites(dia)).fetchall()
    conexao.rollback()
    return [id_ for (id_,) in linhas]


def estimar(conexao: psycopg.Connection, amostra: float = AMOSTRA) -> dict:
    # A amostra é de blocos inteiros (rápida, e não lê a tabela toda): os flashes de um bloco são do
    # mesmo arquivo, então a fração varia mais que numa amostra de flashes soltos
    linhas = conexao.execute(sql.SQL(
        "SELECT date_trunc('week', r.time_utc) AS semana, count(*) AS flashes, count(*) FILTER (WHERE {}) AS dentro, "
        "sum(pg_column_size(r.*) - pg_column_size(r.atributos_originais)) AS bytes "
        "FROM {} r TABLESAMPLE SYSTEM (%(amostra)s::real) REPEATABLE (7) GROUP BY 1 ORDER BY 1")
        .format(banco.NO_CIRCULO, _dele(FLASHES)), {"amostra": amostra, **_circulo()}).fetchall()
    semanas = pd.DataFrame(linhas, columns=["semana", "flashes", "dentro", "bytes"])
    tabela = f"{config.GLM_SCHEMA}.{FLASHES}"
    linhas_da_tabela, tamanho = conexao.execute(
        "SELECT (SELECT reltuples::bigint FROM pg_class WHERE oid = %s::regclass), pg_total_relation_size(%s::regclass)",
        (tabela, tabela)).fetchone()
    conexao.rollback()
    vistos, dentro = int(semanas["flashes"].sum()), int(semanas["dentro"].sum())
    fracao = dentro / vistos if vistos else 0.0
    por_flash = float(semanas["bytes"].sum()) / vistos if vistos else 0.0
    linhas_da_tabela = max(int(linhas_da_tabela or 0), 0)
    ficam = round(linhas_da_tabela * fracao)
    return {"amostra": vistos, "dentro": dentro, "fracao": fracao, "linhas": linhas_da_tabela, "tamanho": tamanho,
            "ficam": ficam, "saem": linhas_da_tabela - ficam, "bytes_por_flash": por_flash,
            "dados_da_copia": ficam * por_flash, "indices_da_copia": ficam * BYTES_DE_INDICE_POR_FLASH,
            "semanas": semanas}


def exigir_as_nossas(conexao: psycopg.Connection) -> None:
    faltam = [nome for nome in (NOSSO_CONTROLE, NOSSOS_FLASHES)
              if conexao.execute("SELECT to_regclass(%s)", (nome,)).fetchone()[0] is None]
    conexao.rollback()
    if faltam:
        raise banco.ErroBanco(f"falta {' e '.join(faltam)} no schema do projeto: rode antes python coletar.py esquema")


def copiar_dia(conexao: psycopg.Connection, dia: date) -> tuple[int, int]:
    # Os arquivos do dia que ainda não estão no nosso controle e os flashes deles no círculo, numa
    # transação. O arquivo cuja referência o nosso coletor já gravou fica com o nosso (ON CONFLICT)
    colunas = sql.SQL(", ").join(map(sql.Identifier, COLUNAS_DO_CONTROLE))
    with conexao.transaction():
        arquivos = conexao.execute(sql.SQL(
            "INSERT INTO {} ({}) SELECT {} FROM {} WHERE data_hora_referencia >= %(de)s AND data_hora_referencia < %(ate)s "
            "AND (status = %(concluido)s OR (status = %(sem_cena)s AND coalesce(mensagem_erro, '') NOT LIKE %(recusados)s)) "
            "ON CONFLICT DO NOTHING RETURNING id, status")
            .format(sql.Identifier(NOSSO_CONTROLE), colunas, colunas, _dele(CONTROLE)),
            {**_limites(dia), "concluido": banco.CONCLUIDO, "sem_cena": banco.SEM_CENA, "recusados": RECUSADOS},
            prepare=False).fetchall()
        cenas = [id_ for id_, status in arquivos if status == banco.CONCLUIDO]
        flashes = 0
        if cenas:
            flashes = conexao.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} r WHERE r.id_cena = ANY(%(cenas)s) AND {}")
                .format(sql.Identifier(NOSSOS_FLASHES), sql.SQL(", ").join(map(sql.Identifier, COLUNAS_DOS_FLASHES)),
                        sql.SQL(", ").join(sql.Identifier("r", coluna) for coluna in COLUNAS_DOS_FLASHES),
                        _dele(FLASHES), banco.NO_CIRCULO),
                {"cenas": cenas, **_circulo()}, prepare=False).rowcount
    return len(arquivos), flashes


def copiar(conexao: psycopg.Connection, desde: date | None = None, pausa: float = PAUSA) -> tuple[int, int]:
    exigir_as_nossas(conexao)
    total_de_arquivos = total_de_flashes = 0
    for dia in dias(conexao, desde):
        antes = time.monotonic()
        arquivos, flashes = _com_paciencia(conexao, copiar_dia, dia)
        if not arquivos:
            continue
        total_de_arquivos += arquivos
        total_de_flashes += flashes
        print(f"   {dia:%d/%m/%Y}: {_n(arquivos)} arquivos, {_n(flashes)} flashes ({time.monotonic() - antes:.0f} s)",
              flush=True)
        time.sleep(pausa)
    conexao.execute(sql.SQL("ANALYZE {}, {}").format(sql.Identifier(NOSSO_CONTROLE), sql.Identifier(NOSSOS_FLASHES)))
    conexao.commit()
    return total_de_arquivos, total_de_flashes


def cortar_dia(conexao: psycopg.Connection, dia: date, simular: bool = False) -> tuple[int | None, int]:
    # Os flashes do dia e os que estão além do raio (apagados, ou que seriam). Os sem coordenada ficam
    cenas = _cenas(conexao, dia)
    if not cenas:
        return 0, 0
    argumentos = {"cenas": cenas, **_circulo()}
    if simular:
        total, fora = conexao.execute(sql.SQL("SELECT count(*), count(*) FILTER (WHERE NOT ({})) FROM {} r "
                                              "WHERE r.id_cena = ANY(%(cenas)s)").format(banco.NO_CIRCULO, _dele(FLASHES)),
                                      argumentos, prepare=False).fetchone()
        conexao.rollback()
        return total, fora
    with conexao.transaction():
        fora = conexao.execute(sql.SQL("DELETE FROM {} r WHERE r.id_cena = ANY(%(cenas)s) AND NOT ({})")
                               .format(_dele(FLASHES), banco.NO_CIRCULO), argumentos, prepare=False).rowcount
    return None, fora


def cortar(conexao: psycopg.Connection, simular: bool = False, desde: date | None = None,
           pausa: float = PAUSA) -> tuple[int, int]:
    total_lido = total_fora = 0
    for dia in dias(conexao, desde):
        antes = time.monotonic()
        lidos, fora = _com_paciencia(conexao, cortar_dia, dia, simular)
        total_lido += lidos or 0
        total_fora += fora
        if simular and lidos:
            print(f"   {dia:%d/%m/%Y}: {_n(lidos)} flashes, {_n(fora)} além de {_n(config.RAIO_DA_TABELA_GLM_KM)} km "
                  f"({_pct(fora / lidos)}) ({time.monotonic() - antes:.0f} s)", flush=True)
        elif fora:
            print(f"   {dia:%d/%m/%Y}: {_n(fora)} flashes apagados ({time.monotonic() - antes:.0f} s)", flush=True)
            time.sleep(pausa)
    return total_lido, total_fora


def _confirmado() -> bool:
    print(f"Isto apaga de {config.GLM_SCHEMA}.{FLASHES}, a tabela do colega, os flashes a mais de "
          f"{_n(config.RAIO_DA_TABELA_GLM_KM)} km de {config.NOME_DO_CENTRO_DOS_RAIOS}. Não tem volta.")
    return input("Para seguir, digite APAGAR: ").strip() == "APAGAR"


def argumentos(linha: list[str] | None = None) -> argparse.Namespace:
    partes = argparse.ArgumentParser(description="Recorta os raios do colega e copia o que fica para a glm_rt.")
    acoes = partes.add_subparsers(dest="acao", required=True)
    acoes.add_parser("estimar", help="quanto fica e quanto sai, por uma amostra (só lê)")
    copia = acoes.add_parser("copiar", help="copia para a glm_rt o que está dentro do raio, dia a dia")
    corte = acoes.add_parser("cortar", help="apaga da tabela do colega o que está além do raio, dia a dia")
    corte.add_argument("--simular", action="store_true", help="só conta, dia a dia, o que seria apagado")
    for acao in (copia, corte):
        acao.add_argument("--desde", type=date.fromisoformat, metavar="AAAA-MM-DD", help="começa neste dia (UTC)")
        acao.add_argument("--pausa", type=float, default=PAUSA, metavar="SEGUNDOS",
                          help=f"espera entre um dia e o seguinte (padrão: {PAUSA:.0f})")
    return partes.parse_args(linha)


def main(linha: list[str] | None = None) -> int:
    opcoes = argumentos(linha)
    simular = getattr(opcoes, "simular", False)
    if opcoes.acao == "cortar" and not simular and not _confirmado():
        print("Nada foi apagado.")
        return 1
    so_le = opcoes.acao == "estimar" or simular
    raio = f"{_n(config.RAIO_DA_TABELA_GLM_KM)} km de {config.NOME_DO_CENTRO_DOS_RAIOS}"
    try:
        with banco.conectar("leitura" if so_le else "gravacao") as conexao:
            _preparar(conexao)
            if opcoes.acao == "estimar":
                e = estimar(conexao)
                print(f"amostra: {_n(e['amostra'])} flashes ({str(AMOSTRA).replace('.', ',')}% dos blocos); {_pct(e['fracao'], 1)} "
                      f"a até {raio}")
                print("por semana: " + " · ".join(f"{linha.semana:%d/%m} {_pct(linha.dentro / linha.flashes)}"
                                                   for linha in e["semanas"].itertuples() if linha.flashes))
                print(f"na tabela, ~{_n(e['linhas'])} flashes ({_gb(e['tamanho'])}): ficam ~{_n(e['ficam'])}, "
                      f"saem ~{_n(e['saem'])}")
                print(f"a glm_rt teria ~{_gb(e['dados_da_copia'])} de dados ({e['bytes_por_flash']:.0f} bytes por flash, "
                      f"sem o jsonb) e ~{_gb(e['indices_da_copia'])} de índices")
            elif opcoes.acao == "copiar":
                print(f"Copiando para {config.BANCO_SCHEMA}.{NOSSOS_FLASHES} os flashes a até {raio}, dia a dia...", flush=True)
                arquivos, flashes = copiar(conexao, opcoes.desde, opcoes.pausa)
                print(f"Pronto: {_n(arquivos)} arquivos e {_n(flashes)} flashes copiados.")
            else:
                verbo = "Contando" if simular else "Apagando"
                print(f"{verbo} os flashes além de {raio} em {config.GLM_SCHEMA}.{FLASHES}, dia a dia...", flush=True)
                lidos, fora = cortar(conexao, simular, opcoes.desde, opcoes.pausa)
                if simular:
                    print(f"O corte apagaria {_n(fora)} dos {_n(lidos)} flashes ({_pct(fora / lidos if lidos else 0)}).")
                else:
                    print(f"Pronto: {_n(fora)} flashes apagados. O arquivo da tabela não diminui: o espaço fica "
                          "para ela reaproveitar, depois que o autovacuum passar.")
    except (banco.ErroBanco, psycopg.Error) as erro:
        print(f"Banco: {erro}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
