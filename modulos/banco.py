"""O banco da UNIGEO: a única peça do projeto que fala SQL.

PostgreSQL 16, com PostGIS 3.4, no schema `climageo`. O esquema está em `banco/esquema.sql`; as
decisões que o desenham, no passo 3 de docs/plano_arquitetura.md e na decisão 6 de
docs/escopo_v0.3.1.md. Em resumo: o banco guarda só as horas, do INMET e da previsão, e o dia, a
semana e o mês saem delas na leitura; o EC46 é a exceção e fica por semana, e a chuva do CEMADEN fica
por leitura, como a PED manda.

Três coisas moram aqui:
- **conectar**, com o papel certo: o usuário que grava (os coletores), o que só lê (o painel e a
  API) e o dos testes. As conexões vêm do .env (modelo: .env.example) e nunca ficam no código;
- **as partições**: o banco não tem pg_partman nem pg_cron, e quem cria e apaga as partições é o
  coletor, por estas funções;
- **gravar em lote e ler de volta**, em tabelas do pandas com as colunas que o resto do projeto já
  usa (as do `openmeteo.py` para a previsão).

As horas entram e saem em UTC: a conexão já abre com o fuso UTC, e o fuso do servidor
(America/Cuiaba) não muda nada nelas.

O servidor derruba a conexão que fica parada no meio de uma transação por 5 minutos, e a que fica
parada à toa por 10 (`idle_in_transaction_session_timeout` e `idle_session_timeout`, vistos em
09/10/2026). Os coletores passam minutos nas APIs entre uma consulta ao banco e a gravação: por
isso as leituras daqui não deixam transação aberta, e quem grava confirma logo.
"""
import functools
import os
import re
from collections.abc import Iterable, Iterator
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

from . import calculos, cemaden, config, openmeteo

# Como cada tabela particionada é dividida, e por qual coluna (decisão de 09/10/2026)
PARTICOES = {
    "inmet_horaria": ("mes", "hora_utc"),
    "previsao_horaria_grade": ("dia", "rodada_utc"),
    "previsao_horaria_estacao": ("mes", "rodada_utc"),
    "cemaden_chuva": ("mes", "leitura_utc"),
}
COLUNAS_CEMADEN_CHUVA = ["estacao", "leitura_utc", "chuva"]
DIAS_DA_GRADE = 21   # a grade guarda 21 dias de rodadas: 14 de horizonte e uma semana de folga
CONJUNTOS = ("grade", "estacao")

# As medidas do INMET que vêm na consulta por hora, e as três que só a consulta por estação traz
# (o coletor as busca uma vez por dia). Os nomes são os códigos da API, em minúsculas.
COLUNAS_INMET = ["tem_ins", "tem_max", "tem_min", "umd_ins", "umd_max", "umd_min", "pto_ins", "pto_max",
                 "pto_min", "pre_ins", "pre_max", "pre_min", "ven_vel", "ven_raj", "ven_dir", "rad_glo",
                 "chuva"]
COLUNAS_INMET_EXTRAS = ["tem_sen", "ten_bat", "tem_cpu"]
COLUNAS_ESTACAO = ["codigo", "nome", "uf", "latitude", "longitude", "altitude", "situacao", "inicio_operacao"]
TEMPO_DE_CONEXAO = 30   # segundos


class ErroBanco(Exception):
    pass


# =====================================================
# CONEXÃO
# =====================================================
def _conferir_nome(nome: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", nome):
        raise ErroBanco(f"nome inválido para o banco: {nome!r}")
    return nome


def conectar(papel: str = "leitura", schema: str = config.BANCO_SCHEMA) -> psycopg.Connection:
    variavel = config.BANCO_CONEXOES[papel]
    texto = os.getenv(variavel, "").strip()
    if not texto:
        raise ErroBanco(f"a conexão do banco ({variavel}) não está no .env: veja o .env.example")
    _conferir_nome(schema)
    try:
        return psycopg.connect(texto, connect_timeout=TEMPO_DE_CONEXAO,
                               options=f"-c search_path={schema},public -c TimeZone=UTC")
    except psycopg.OperationalError as erro:
        # A mensagem do PostgreSQL não traz a senha, mas pode trazer o resto da conexão: fica só a
        # primeira linha, que diz o motivo
        motivo = str(erro).strip().splitlines()[0] if str(erro).strip() else type(erro).__name__
        raise ErroBanco(f"não foi possível conectar ao banco ({variavel}): {motivo}") from None


def criar_esquema(conexao: psycopg.Connection) -> None:
    conexao.execute(config.BANCO_ESQUEMA.read_text(encoding="utf-8"))
    conexao.commit()


def recriar_schema_de_teste(conexao: psycopg.Connection, schema: str) -> None:
    if not _conferir_nome(schema).endswith("_teste"):
        raise ErroBanco(f"recriar_schema_de_teste só apaga schemas de teste (…_teste), e não {schema!r}")
    conexao.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    conexao.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conexao.commit()


# =====================================================
# PARTIÇÕES
# =====================================================
def _em_utc(momento) -> datetime:
    return pd.Timestamp(momento).tz_convert("UTC").to_pydatetime() if pd.Timestamp(momento).tzinfo \
        else pd.Timestamp(momento).tz_localize("UTC").to_pydatetime()


def _periodo(divisao: str, dia: date) -> tuple[date, date]:
    if divisao == "dia":
        return dia, dia + timedelta(days=1)
    comeco = dia.replace(day=1)
    return comeco, (comeco + timedelta(days=32)).replace(day=1)


def nome_da_particao(tabela: str, comeco: date) -> str:
    divisao, _ = PARTICOES[tabela]
    return f"{tabela}_{comeco:%Y%m%d}" if divisao == "dia" else f"{tabela}_{comeco:%Y%m}"


def _meia_noite_utc(dia: date) -> datetime:
    return datetime.combine(dia, time(0), tzinfo=timezone.utc)


def garantir_particoes(conexao: psycopg.Connection, tabela: str, de, ate) -> list[str]:
    divisao, _ = PARTICOES[tabela]
    dia, ultimo = _em_utc(de).date(), _em_utc(ate).date()
    existentes = set(particoes(conexao, tabela))
    criadas = []
    while dia <= ultimo:
        comeco, fim = _periodo(divisao, dia)
        nome = nome_da_particao(tabela, comeco)
        if nome not in existentes:
            conexao.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} PARTITION OF {} FOR VALUES FROM ({}) TO ({})")
                            .format(sql.Identifier(nome), sql.Identifier(tabela),
                                    sql.Literal(_meia_noite_utc(comeco)), sql.Literal(_meia_noite_utc(fim))))
            criadas.append(nome)
        dia = fim
    return criadas


def particoes(conexao: psycopg.Connection, tabela: str) -> dict[str, date]:
    linhas = conexao.execute(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = to_regclass(%s)", (tabela,)).fetchall()
    resultado = {}
    for (nome,) in linhas:
        sufixo = nome.removeprefix(f"{tabela}_")
        formato = "%Y%m%d" if len(sufixo) == 8 else "%Y%m"
        resultado[nome] = datetime.strptime(sufixo, formato).date()
    return dict(sorted(resultado.items(), key=lambda item: item[1]))


def apagar_particoes_antigas(conexao: psycopg.Connection, tabela: str, antes_de) -> list[str]:
    divisao, _ = PARTICOES[tabela]
    limite = _em_utc(antes_de).date()
    velhas = [nome for nome, comeco in particoes(conexao, tabela).items()
              if _periodo(divisao, comeco)[1] <= limite]
    if not velhas:
        return []
    conexao.commit()
    modo_anterior = conexao.autocommit
    conexao.autocommit = True
    try:
        for nome in velhas:
            conexao.execute(sql.SQL("ALTER TABLE {} DETACH PARTITION {} CONCURRENTLY")
                            .format(sql.Identifier(tabela), sql.Identifier(nome)))
            conexao.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(nome)))
    finally:
        conexao.autocommit = modo_anterior
    return velhas


def limpar_grade(conexao: psycopg.Connection, agora: datetime | None = None) -> list[str]:
    agora = agora or datetime.now(timezone.utc)
    return apagar_particoes_antigas(conexao, "previsao_horaria_grade", agora - timedelta(days=DIAS_DA_GRADE))


# =====================================================
# GRAVAR
# =====================================================
def _linhas(tabela: pd.DataFrame, colunas: list[str]) -> Iterator[tuple]:
    dados = tabela[colunas].copy()
    for coluna in colunas:
        if isinstance(dados[coluna].dtype, pd.DatetimeTZDtype):
            dados[coluna] = dados[coluna].dt.tz_convert("UTC")
    dados = dados.astype(object).where(dados.notna(), None)
    return dados.itertuples(index=False, name=None)


def _carregar(conexao: psycopg.Connection, tabela: str, colunas: list[str], linhas: Iterable[tuple]) -> None:
    conexao.execute(sql.SQL("CREATE TEMP TABLE carga (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP")
                    .format(sql.Identifier(tabela)))
    with conexao.cursor() as cursor:
        comando = sql.SQL("COPY carga ({}) FROM STDIN").format(sql.SQL(", ").join(map(sql.Identifier, colunas)))
        with cursor.copy(comando) as copia:
            for linha in linhas:
                copia.write_row(linha)


def _lista(colunas: list[str]) -> sql.Composed:
    return sql.SQL(", ").join(map(sql.Identifier, colunas))


def _conferir_conjunto(conjunto: str) -> str:
    if conjunto not in CONJUNTOS:
        raise ErroBanco(f"conjunto desconhecido: {conjunto!r} (são {', '.join(CONJUNTOS)})")
    return conjunto


def gravar_previsao(conexao: psycopg.Connection, horaria: pd.DataFrame, conjunto: str) -> int:
    if horaria.empty:
        return 0
    tabela = f"previsao_horaria_{_conferir_conjunto(conjunto)}"
    colunas = list(openmeteo.COLUNAS_HORARIAS)
    garantir_particoes(conexao, tabela, horaria["rodada_utc"].min(), horaria["rodada_utc"].max())
    _carregar(conexao, tabela, colunas, _linhas(horaria, colunas))
    gravadas = conexao.execute(sql.SQL("INSERT INTO {} ({}) SELECT {} FROM carga ON CONFLICT DO NOTHING")
                               .format(sql.Identifier(tabela), _lista(colunas), _lista(colunas))).rowcount
    conexao.commit()
    return gravadas


def gravar_semanas(conexao: psycopg.Connection, semanal: pd.DataFrame, conjunto: str) -> int:
    if semanal.empty:
        return 0
    colunas = ["conjunto", *openmeteo.COLUNAS_SEMANAIS]
    dados = semanal.assign(conjunto=_conferir_conjunto(conjunto))
    _carregar(conexao, "previsao_semanal", colunas, _linhas(dados, colunas))
    gravadas = conexao.execute(sql.SQL("INSERT INTO previsao_semanal ({}) SELECT {} FROM carga "
                                       "ON CONFLICT DO NOTHING").format(_lista(colunas), _lista(colunas))).rowcount
    conexao.commit()
    return gravadas


def gravar_estacoes(conexao: psycopg.Connection, estacoes: pd.DataFrame, so_as_novas: bool = False) -> int:
    if estacoes.empty:
        return 0
    colunas = [coluna for coluna in COLUNAS_ESTACAO if coluna in estacoes]
    _carregar(conexao, "estacao", colunas, _linhas(estacoes.drop_duplicates("codigo"), colunas))
    if so_as_novas:
        conflito = sql.SQL("DO NOTHING")
    else:
        outras = [coluna for coluna in colunas if coluna != "codigo"]
        novos = [sql.SQL("COALESCE(EXCLUDED.{c}, estacao.{c})").format(c=sql.Identifier(c)) for c in outras]
        conflito = sql.SQL("DO UPDATE SET {}, atualizada_em = now() WHERE ({}) IS DISTINCT FROM ({})").format(
            sql.SQL(", ").join(sql.SQL("{} = {}").format(sql.Identifier(c), novo) for c, novo in zip(outras, novos)),
            sql.SQL(", ").join(sql.SQL("estacao.{}").format(sql.Identifier(c)) for c in outras),
            sql.SQL(", ").join(novos))
    gravadas = conexao.execute(sql.SQL("INSERT INTO estacao ({}) SELECT {} FROM carga ON CONFLICT (codigo) {}")
                               .format(_lista(colunas), _lista(colunas), conflito)).rowcount
    conexao.commit()
    return gravadas


def _gravar_inmet(conexao: psycopg.Connection, leituras: pd.DataFrame, medidas: list[str]) -> int:
    if leituras.empty:
        return 0
    medidas = [medida for medida in medidas if medida in leituras]
    colunas = ["estacao", "hora_utc", *medidas]
    garantir_particoes(conexao, "inmet_horaria", leituras["hora_utc"].min(), leituras["hora_utc"].max())
    _carregar(conexao, "inmet_horaria", colunas, _linhas(leituras, colunas))
    novos = sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(medida)) for medida in medidas)
    antes = sql.SQL(", ").join(sql.SQL("inmet_horaria.{}").format(sql.Identifier(medida)) for medida in medidas)
    depois = sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(medida)) for medida in medidas)
    # Em ordem: duas gravações ao mesmo tempo (a carga do histórico e o coletor de hora em hora)
    # travam as linhas na mesma sequência, e uma espera a outra em vez de as duas se travarem
    gravadas = conexao.execute(
        sql.SQL("INSERT INTO inmet_horaria ({}) SELECT {} FROM carga ORDER BY estacao, hora_utc "
                "ON CONFLICT (estacao, hora_utc) DO UPDATE SET {}, coletada_em = now() WHERE ({}) IS DISTINCT FROM ({})")
        .format(_lista(colunas), _lista(colunas), novos, antes, depois)).rowcount
    conexao.commit()
    return gravadas


def gravar_inmet(conexao: psycopg.Connection, leituras: pd.DataFrame) -> int:
    return _gravar_inmet(conexao, leituras, COLUNAS_INMET)


def gravar_extras_inmet(conexao: psycopg.Connection, extras: pd.DataFrame) -> int:
    return _gravar_inmet(conexao, extras, COLUNAS_INMET_EXTRAS)


def _atribuicoes(colunas: list[str], tabela: str) -> tuple[sql.Composed, sql.Composed, sql.Composed]:
    # O "SET c = EXCLUDED.c" e os dois lados do "só se mudou" de um upsert
    return (sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in colunas),
            sql.SQL(", ").join(sql.SQL("{}.{}").format(sql.Identifier(tabela), sql.Identifier(c)) for c in colunas),
            sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(c)) for c in colunas))


def gravar_cemaden_estacoes(conexao: psycopg.Connection, cadastro: pd.DataFrame, so_as_novas: bool = False) -> int:
    # O cadastro da PED vale como vem, sem guardar o que ela deixou vazio. Só a estação que mudou é
    # reescrita. Com so_as_novas (a estação descrita pelas leituras), a que já está fica como está
    if cadastro.empty:
        return 0
    colunas = list(cemaden.COLUNAS_DO_CADASTRO)
    _carregar(conexao, "cemaden_estacao", colunas, _linhas(cadastro.drop_duplicates("codigo"), colunas))
    novos, antes, depois = _atribuicoes(colunas[1:], "cemaden_estacao")
    conflito = sql.SQL("DO NOTHING") if so_as_novas else sql.SQL(
        "DO UPDATE SET {}, atualizada_em = now() WHERE ({}) IS DISTINCT FROM ({})").format(novos, antes, depois)
    gravadas = conexao.execute(
        sql.SQL("INSERT INTO cemaden_estacao ({}) SELECT {} FROM carga ON CONFLICT (codigo) {}")
        .format(_lista(colunas), _lista(colunas), conflito)).rowcount
    conexao.commit()
    return gravadas


def gravar_cemaden_chuva(conexao: psycopg.Connection, leituras: pd.DataFrame) -> int:
    # As leituras de cemaden.chuva_do_dia. A que a PED corrigiu depois é reescrita; a que veio igual,
    # não. Em ordem, como no INMET: a carga do histórico e o coletor de hora em hora não se travam
    if leituras.empty:
        return 0
    dados = leituras.rename(columns={"codigo": "estacao"})
    garantir_particoes(conexao, "cemaden_chuva", dados["leitura_utc"].min(), dados["leitura_utc"].max())
    _carregar(conexao, "cemaden_chuva", COLUNAS_CEMADEN_CHUVA, _linhas(dados, COLUNAS_CEMADEN_CHUVA))
    gravadas = conexao.execute(
        sql.SQL("INSERT INTO cemaden_chuva ({}) SELECT {} FROM carga ORDER BY estacao, leitura_utc "
                "ON CONFLICT (estacao, leitura_utc) DO UPDATE SET chuva = EXCLUDED.chuva, coletada_em = now() "
                "WHERE cemaden_chuva.chuva IS DISTINCT FROM EXCLUDED.chuva")
        .format(_lista(COLUNAS_CEMADEN_CHUVA), _lista(COLUNAS_CEMADEN_CHUVA))).rowcount
    conexao.commit()
    return gravadas


# =====================================================
# LER
# =====================================================
def _sem_deixar_transacao(funcao):
    @functools.wraps(funcao)
    def ler(conexao: psycopg.Connection, *args, **kwargs):
        parada = conexao.info.transaction_status == TransactionStatus.IDLE
        try:
            return funcao(conexao, *args, **kwargs)
        finally:
            if parada:
                conexao.rollback()
    return ler


def _tabela(conexao: psycopg.Connection, consulta, argumentos=()) -> pd.DataFrame:
    with conexao.cursor() as cursor:
        cursor.execute(consulta, argumentos)
        colunas = [descricao.name for descricao in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=colunas)


def _horas_em_utc(tabela: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    for coluna in colunas:
        tabela[coluna] = pd.to_datetime(tabela[coluna], utc=True)
    return tabela


def _numeros(tabela: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    for coluna in colunas:
        tabela[coluna] = pd.to_numeric(tabela[coluna]).astype(float)
    return tabela


def _ultimas_rodadas(conexao: psycopg.Connection, tabela: str, filtro: sql.Composable = sql.SQL(""),
                     argumentos: tuple = ()) -> dict[str, datetime]:
    # A última rodada de cada modelo pela chave primária, que começa por (modelo, rodada_utc). Num
    # "GROUP BY modelo" o PostgreSQL 16 não usa o índice e varre a tabela inteira, e a das estações
    # cresce para sempre. Aqui a recursão salta de um modelo ao seguinte pelo índice, e o máximo de
    # cada um é uma leitura só (medido em 15/10/2026: 0,2 ms contra 129 ms, e crescendo).
    consulta = sql.SQL("""
        WITH RECURSIVE modelos AS (
            (SELECT modelo FROM {tabela} ORDER BY modelo LIMIT 1)
            UNION ALL
            SELECT (SELECT t.modelo FROM {tabela} t WHERE t.modelo > m.modelo ORDER BY t.modelo LIMIT 1)
            FROM modelos m WHERE m.modelo IS NOT NULL
        )
        SELECT modelo, (SELECT max(t.rodada_utc) FROM {tabela} t WHERE t.modelo = modelos.modelo{filtro})
        FROM modelos WHERE modelo IS NOT NULL""").format(tabela=sql.Identifier(tabela), filtro=filtro)
    linhas = conexao.execute(consulta, argumentos).fetchall()
    return {modelo: pd.Timestamp(rodada).tz_convert("UTC") for modelo, rodada in linhas if rodada is not None}


@_sem_deixar_transacao
def rodadas_guardadas(conexao: psycopg.Connection, conjunto: str) -> dict[str, datetime]:
    return _ultimas_rodadas(conexao, f"previsao_horaria_{_conferir_conjunto(conjunto)}")


@_sem_deixar_transacao
def rodadas_semanais_guardadas(conexao: psycopg.Connection, conjunto: str) -> dict[str, datetime]:
    return _ultimas_rodadas(conexao, "previsao_semanal", sql.SQL(" AND t.conjunto = %s"),
                            (_conferir_conjunto(conjunto),))


@_sem_deixar_transacao
def rodadas_ausentes(conexao: psycopg.Connection, conjunto: str,
                     esperadas: list[tuple[str, datetime]]) -> list[tuple[str, pd.Timestamp]]:
    # Uma leitura do índice por rodada esperada. Com "NOT EXISTS", o PostgreSQL junta as esperadas
    # com a tabela inteira e varre a grade toda (medido em 21/10/2026: 1,7 s contra 2 ms)
    if not esperadas:
        return []
    modelos, rodadas = zip(*esperadas)
    consulta = sql.SQL("""
        SELECT e.modelo, e.rodada_utc FROM unnest(%s::text[], %s::timestamptz[]) AS e(modelo, rodada_utc)
        WHERE (SELECT 1 FROM {tabela} t WHERE t.modelo = e.modelo AND t.rodada_utc = e.rodada_utc LIMIT 1) IS NULL
        ORDER BY e.rodada_utc, e.modelo""").format(
        tabela=sql.Identifier(f"previsao_horaria_{_conferir_conjunto(conjunto)}"))
    linhas = conexao.execute(consulta, (list(modelos), [pd.Timestamp(rodada).to_pydatetime() for rodada in rodadas]))
    return [(modelo, pd.Timestamp(rodada).tz_convert("UTC")) for modelo, rodada in linhas.fetchall()]


@_sem_deixar_transacao
def ler_estacoes(conexao: psycopg.Connection) -> pd.DataFrame:
    tabela = _tabela(conexao, sql.SQL("SELECT {} FROM estacao ORDER BY codigo").format(_lista(COLUNAS_ESTACAO)))
    return _numeros(tabela, ["latitude", "longitude", "altitude"])


@_sem_deixar_transacao
def pontos_das_estacoes(conexao: psycopg.Connection, uf: str) -> pd.DataFrame:
    # As que estão na lista do INMET, em operação ou em pane; as que fecharam não têm situação
    tabela = _tabela(conexao, "SELECT codigo AS ponto, latitude, longitude FROM estacao "
                              "WHERE uf = %s AND situacao IS NOT NULL ORDER BY codigo", (uf,))
    return _numeros(tabela, ["latitude", "longitude"])


@_sem_deixar_transacao
def ler_previsao(conexao: psycopg.Connection, conjunto: str, modelo: str, rodada=None) -> pd.DataFrame:
    tabela = f"previsao_horaria_{_conferir_conjunto(conjunto)}"
    if rodada is None:
        rodada = rodadas_guardadas(conexao, conjunto).get(modelo)
        if rodada is None:
            return pd.DataFrame(columns=openmeteo.COLUNAS_HORARIAS)
    colunas = list(openmeteo.COLUNAS_HORARIAS)
    resultado = _tabela(conexao, sql.SQL("SELECT {} FROM {} WHERE modelo = %s AND rodada_utc = %s "
                                         "ORDER BY ponto, hora_prevista_utc")
                        .format(_lista(colunas), sql.Identifier(tabela)), (modelo, _em_utc(rodada)))
    resultado = _numeros(resultado, ["latitude", "longitude", *openmeteo.VARIAVEIS.values()])
    return _horas_em_utc(resultado, ["rodada_utc", "hora_prevista_utc"])[colunas]


@_sem_deixar_transacao
def ler_semanas(conexao: psycopg.Connection, conjunto: str, rodada=None,
                modelo: str = config.MODELO_SEMANAS) -> pd.DataFrame:
    _conferir_conjunto(conjunto)
    if rodada is None:
        rodada = rodadas_semanais_guardadas(conexao, conjunto).get(modelo)
        if rodada is None:
            return pd.DataFrame(columns=openmeteo.COLUNAS_SEMANAIS)
    colunas = list(openmeteo.COLUNAS_SEMANAIS)
    # Com o modelo no filtro, a leitura vai pela chave primária (modelo, rodada_utc, conjunto, ...)
    resultado = _tabela(conexao, sql.SQL("SELECT {} FROM previsao_semanal WHERE modelo = %s AND rodada_utc = %s "
                                         "AND conjunto = %s ORDER BY ponto, semana").format(_lista(colunas)),
                        (modelo, _em_utc(rodada), conjunto))
    resultado = _numeros(resultado, ["latitude", "longitude", *openmeteo.VARIAVEIS_SEMANAIS.values()])
    return _horas_em_utc(resultado, ["rodada_utc"])[colunas]


@_sem_deixar_transacao
def horas_guardadas(conexao: psycopg.Connection, inicio, fim) -> set[pd.Timestamp]:
    linhas = conexao.execute("SELECT DISTINCT hora_utc FROM inmet_horaria WHERE hora_utc >= %s AND hora_utc <= %s",
                             (_em_utc(inicio), _em_utc(fim))).fetchall()
    return {pd.Timestamp(hora).tz_convert("UTC") for (hora,) in linhas}


@_sem_deixar_transacao
def fora_do_cadastro(conexao: psycopg.Connection, codigos: Iterable[str]) -> list[str]:
    linhas = conexao.execute("SELECT codigo FROM unnest(%s::text[]) AS codigo "
                             "WHERE codigo NOT IN (SELECT codigo FROM estacao) ORDER BY codigo",
                             (list(codigos),)).fetchall()
    return [codigo for (codigo,) in linhas]


@_sem_deixar_transacao
def estacoes_com_leituras(conexao: psycopg.Connection, inicio, fim) -> list[str]:
    linhas = conexao.execute("SELECT DISTINCT estacao FROM inmet_horaria WHERE hora_utc > %s AND hora_utc <= %s "
                             "ORDER BY estacao", (_em_utc(inicio), _em_utc(fim))).fetchall()
    return [estacao for (estacao,) in linhas]


@_sem_deixar_transacao
def dias_cemaden_guardados(conexao: psycopg.Connection, desde: date, ate: date) -> set[date]:
    # Os dias UTC, de desde a ate inclusive, com alguma leitura no banco: a consulta à PED é por dia
    linhas = conexao.execute("SELECT DISTINCT (leitura_utc AT TIME ZONE 'UTC')::date FROM cemaden_chuva "
                             "WHERE leitura_utc >= %s AND leitura_utc < %s",
                             (_meia_noite_utc(desde), _meia_noite_utc(ate + timedelta(days=1)))).fetchall()
    return {dia for (dia,) in linhas}


@_sem_deixar_transacao
def ler_cemaden_estacoes(conexao: psycopg.Connection, uf: str | None = None) -> pd.DataFrame:
    colunas = list(cemaden.COLUNAS_DO_CADASTRO)
    filtro, argumentos = (sql.SQL(" WHERE uf = %s"), (uf,)) if uf else (sql.SQL(""), ())
    tabela = _tabela(conexao, sql.SQL("SELECT {} FROM cemaden_estacao{} ORDER BY codigo").format(_lista(colunas), filtro),
                     argumentos)
    for coluna in ("instalada_em", "ultima_parada", "ultima_remessa"):
        tabela[coluna] = pd.to_datetime(tabela[coluna])
    return _numeros(tabela, ["latitude", "longitude"])


@_sem_deixar_transacao
def ler_cemaden_chuva(conexao: psycopg.Connection, inicio, fim, estacoes: list[str] | None = None) -> pd.DataFrame:
    # As leituras de (início, fim], como no INMET: começando numa hora cheia, são as das horas que
    # terminam depois do início
    filtro = sql.SQL(" AND estacao = ANY(%s)") if estacoes is not None else sql.SQL("")
    argumentos = (_em_utc(inicio), _em_utc(fim), *([list(estacoes)] if estacoes is not None else []))
    resultado = _tabela(conexao, sql.SQL("SELECT {} FROM cemaden_chuva WHERE leitura_utc > %s AND leitura_utc <= %s{} "
                                         "ORDER BY estacao, leitura_utc")
                        .format(_lista(COLUNAS_CEMADEN_CHUVA), filtro), argumentos)
    return _horas_em_utc(_numeros(resultado, ["chuva"]), ["leitura_utc"])


@_sem_deixar_transacao
def ler_inmet(conexao: psycopg.Connection, inicio, fim, estacoes: list[str] | None = None) -> pd.DataFrame:
    colunas = ["estacao", "hora_utc", *COLUNAS_INMET, *COLUNAS_INMET_EXTRAS]
    filtro = sql.SQL(" AND estacao = ANY(%s)") if estacoes is not None else sql.SQL("")
    argumentos = (_em_utc(inicio), _em_utc(fim), *([list(estacoes)] if estacoes is not None else []))
    resultado = _tabela(conexao, sql.SQL("SELECT {} FROM inmet_horaria WHERE hora_utc > %s AND hora_utc <= %s{} "
                                         "ORDER BY estacao, hora_utc").format(_lista(colunas), filtro), argumentos)
    return _horas_em_utc(_numeros(resultado, [*COLUNAS_INMET, *COLUNAS_INMET_EXTRAS]), ["hora_utc"])


# =====================================================
# OS RAIOS DO GLM
# =====================================================
# Os flashes vêm da glm_rt, que o nosso coletor alimenta, ou das tabelas do colega (config.FONTE_GLM):
# as colunas e a ligação com o controle são as mesmas. A dele não tem índice no horário dos flashes,
# mas o controle tem, na referência de cada arquivo de 20 s, e os flashes têm no arquivo (id_cena é o
# id do controle). Toda leitura vai por ali: uma hora fora do cache sai em ~1 s; pelo horário dos
# flashes, 24 h levavam ~1 min na dele, e piorava conforme a tabela cresce (medido em 19 e 23/11/2026).
# Os arquivos vão num CTE materializado: sozinho, o planejador lia o controle inteiro pela chave
# (~2 s a mais por consulta, crescendo com o histórico até 2018)
CONCLUIDO, SEM_CENA = "CONCLUIDO", "ENCERRADO_SEM_CENA"
NA_CAIXA = sql.SQL("r.flash_lon >= %(oeste)s AND r.flash_lon < %(leste)s "
                   "AND r.flash_lat >= %(sul)s AND r.flash_lat < %(norte)s")
ARQUIVOS_DO_TRECHO = ("WITH c AS MATERIALIZED (SELECT id, data_hora_referencia FROM {} "
                      "WHERE data_hora_referencia >= %(de)s AND data_hora_referencia < %(ate)s) ")
# O círculo do Agora (config.RAIO_DOS_RAIOS_KM em volta de config.CENTRO_DOS_RAIOS): primeiro a caixa
# dele, que é só comparar números, e a distância na esfera do PostGIS só para quem está dentro dela.
# Para o banco, o círculo custa o mesmo que a caixa do estado: o caro é ler os arquivos do disco
# inteiro, e isso acontece de qualquer jeito (medido em 24/11/2026)
NO_CIRCULO = sql.SQL("r.flash_lon >= %(c_oeste)s AND r.flash_lon <= %(c_leste)s AND r.flash_lat >= %(c_sul)s "
                     "AND r.flash_lat <= %(c_norte)s AND ST_DistanceSphere(ST_SetSRID(ST_MakePoint(r.flash_lon, "
                     "r.flash_lat), 4326), ST_SetSRID(ST_MakePoint(%(c_lon)s, %(c_lat)s), 4326)) <= %(c_metros)s")
FOLGA_DA_CAIXA_DO_CIRCULO = 0.01   # graus: quem decide é a distância


def _do_glm(tabela: str) -> sql.Identifier:
    return sql.Identifier(_conferir_nome(config.GLM_SCHEMA), tabela)


def _tabelas_do_glm() -> tuple[sql.Identifier, sql.Identifier]:
    # O controle e os flashes. Os nossos ficam no schema da conexão (o climageo, ou o de teste)
    if config.FONTE_GLM == "nossa":
        return sql.Identifier("controle_glm_rt"), sql.Identifier("glm_rt")
    if config.FONTE_GLM == "colega":
        return _do_glm("controle_processamento_glm"), _do_glm("relampagos_glm_rt")
    raise ErroBanco(f"FONTE_GLM inválida: {config.FONTE_GLM!r} (é nossa ou colega)")


def _controle_do_glm() -> sql.Identifier:
    return _tabelas_do_glm()[0]


def _flashes_do_glm() -> sql.Identifier:
    return _tabelas_do_glm()[1]


def _caixa_dos_raios() -> dict:
    oeste, sul, leste, norte = config.CAIXA_RAIOS
    return {"oeste": oeste, "sul": sul, "leste": leste, "norte": norte}


def circulo_dos_raios(km: float | None = None) -> dict:
    # Os parâmetros de NO_CIRCULO: o do Agora, ou outro raio (o da glm_rt, config.RAIO_DA_TABELA_GLM_KM)
    km = config.RAIO_DOS_RAIOS_KM if km is None else km
    oeste, sul, leste, norte = calculos.caixa_do_circulo(config.CENTRO_DOS_RAIOS, km)
    folga = FOLGA_DA_CAIXA_DO_CIRCULO
    lon, lat = config.CENTRO_DOS_RAIOS
    return {"c_oeste": oeste - folga, "c_sul": sul - folga, "c_leste": leste + folga, "c_norte": norte + folga,
            "c_lon": lon, "c_lat": lat, "c_metros": km * 1000.0}


@_sem_deixar_transacao
def controle_do_glm(conexao: psycopg.Connection, depois_do_id: int) -> pd.DataFrame:
    # Os arquivos que entraram no controle depois do id. O id cresce na ordem em que entram; no
    # nosso, o arquivo que chega depois de dado por perdido ganha um id novo
    tabela = _tabela(conexao, sql.SQL("SELECT id, data_hora_referencia AS referencia, status FROM {} "
                                      "WHERE id > %s ORDER BY id").format(_controle_do_glm()),
                     (depois_do_id,))
    return _horas_em_utc(tabela, ["referencia"])


@_sem_deixar_transacao
def arquivos_do_glm(conexao: psycopg.Connection, horas: Iterable) -> pd.DataFrame:
    # Os arquivos de cada hora (pelo fim dela, como na chuva) que já estão no controle: os
    # processados e os que a NOAA não publicou. A hora sem arquivo nenhum vem com zeros
    horas = sorted({_em_utc(hora) for hora in horas})
    tabela = _tabela(conexao, sql.SQL(
        "SELECT h.hora AS hora_utc, count(c.id) FILTER (WHERE c.status = %s) AS arquivos, "
        "count(c.id) FILTER (WHERE c.status = %s) AS sem_arquivo "
        "FROM unnest(%s::timestamptz[]) AS h(hora) LEFT JOIN {} c "
        "ON c.data_hora_referencia >= h.hora - interval '1 hour' AND c.data_hora_referencia < h.hora "
        "GROUP BY h.hora ORDER BY h.hora").format(_controle_do_glm()),
        (CONCLUIDO, SEM_CENA, horas))
    return _horas_em_utc(tabela, ["hora_utc"]).astype({"arquivos": int, "sem_arquivo": int})


def contar_raios(conexao: psycopg.Connection, de, ate, arquivos: pd.DataFrame) -> int:
    # Refaz a contagem das horas que terminam em (de, ate], com os arquivos de [de, ate), e grava a
    # cobertura delas (`arquivos`, de arquivos_do_glm). Uma transação só: quem lê no meio vê a
    # contagem velha ou a nova, nunca a hora vazia. Devolve os flashes contados
    de, ate = _em_utc(de), _em_utc(ate)
    conexao.execute("DELETE FROM raios_glm_hora WHERE hora_utc > %s AND hora_utc <= %s", (de, ate))
    contadas = conexao.execute(sql.SQL(ARQUIVOS_DO_TRECHO +
        "INSERT INTO raios_glm_hora (hora_utc, lon_celula, lat_celula, flashes) "
        "SELECT date_trunc('hour', c.data_hora_referencia) + interval '1 hour', "
        "floor(r.flash_lon * %(celulas)s)::smallint, floor(r.flash_lat * %(celulas)s)::smallint, count(*) "
        "FROM c JOIN {} r ON r.id_cena = c.id WHERE {} GROUP BY 1, 2, 3 RETURNING hora_utc, flashes")
        .format(_controle_do_glm(), _flashes_do_glm(), NA_CAIXA),
        {"celulas": config.CELULAS_POR_GRAU_RAIOS, "de": de, "ate": ate, **_caixa_dos_raios()}).fetchall()
    por_hora: dict[pd.Timestamp, int] = {}
    for hora, flashes in contadas:
        chave = pd.Timestamp(hora).tz_convert("UTC")
        por_hora[chave] = por_hora.get(chave, 0) + flashes
    dentro = arquivos[(arquivos["hora_utc"] > de) & (arquivos["hora_utc"] <= ate)]
    linhas = [(hora.to_pydatetime(), int(feitos), int(sem), por_hora.get(hora, 0))
              for hora, feitos, sem in dentro[["hora_utc", "arquivos", "sem_arquivo"]].itertuples(index=False)]
    with conexao.cursor() as cursor:
        cursor.executemany("INSERT INTO raios_glm_cobertura (hora_utc, arquivos, sem_arquivo, flashes) "
                           "VALUES (%s, %s, %s, %s) ON CONFLICT (hora_utc) DO UPDATE SET "
                           "arquivos = EXCLUDED.arquivos, sem_arquivo = EXCLUDED.sem_arquivo, "
                           "flashes = EXCLUDED.flashes, contada_em = now()", linhas)
    conexao.commit()
    return sum(por_hora.values())


@_sem_deixar_transacao
def ultimo_id_dos_raios(conexao: psycopg.Connection) -> int:
    linha = conexao.execute("SELECT ultimo_id_controle FROM raios_glm_coleta").fetchone()
    return linha[0] if linha else 0


def guardar_ultimo_id_dos_raios(conexao: psycopg.Connection, ultimo_id: int) -> None:
    conexao.execute("INSERT INTO raios_glm_coleta (id, ultimo_id_controle) VALUES (1, %s) ON CONFLICT (id) "
                    "DO UPDATE SET ultimo_id_controle = EXCLUDED.ultimo_id_controle, atualizada_em = now()",
                    (int(ultimo_id),))
    conexao.commit()


@_sem_deixar_transacao
def horas_de_raios_incompletas(conexao: psycopg.Connection) -> list[pd.Timestamp]:
    # As horas contadas sem os 180 arquivos: um arquivo pendente pode chegar depois
    linhas = conexao.execute("SELECT hora_utc FROM raios_glm_cobertura WHERE arquivos + sem_arquivo < %s "
                             "ORDER BY hora_utc", (config.ARQUIVOS_GLM_POR_HORA,)).fetchall()
    return [pd.Timestamp(hora).tz_convert("UTC") for (hora,) in linhas]


@_sem_deixar_transacao
def cobertura_dos_raios(conexao: psycopg.Connection, inicio=None, fim=None,
                        horas: Iterable | None = None) -> pd.DataFrame:
    # As horas de (início, fim], ou só as pedidas, com os arquivos que estavam na contagem
    if horas is not None:
        filtro, argumentos = sql.SQL("hora_utc = ANY(%s)"), ([_em_utc(hora) for hora in horas],)
    else:
        filtro, argumentos = sql.SQL("hora_utc > %s AND hora_utc <= %s"), (_em_utc(inicio), _em_utc(fim))
    tabela = _tabela(conexao, sql.SQL("SELECT hora_utc, arquivos, sem_arquivo, flashes FROM raios_glm_cobertura "
                                      "WHERE {} ORDER BY hora_utc").format(filtro), argumentos)
    return _horas_em_utc(tabela, ["hora_utc"]).astype({"arquivos": int, "sem_arquivo": int, "flashes": int})


@_sem_deixar_transacao
def alcance_dos_raios(conexao: psycopg.Connection) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    primeira, ultima = conexao.execute("SELECT min(hora_utc), max(hora_utc) FROM raios_glm_cobertura").fetchone()
    return tuple(pd.Timestamp(hora).tz_convert("UTC") if hora is not None else None for hora in (primeira, ultima))


@_sem_deixar_transacao
def ler_raios_por_celula(conexao: psycopg.Connection, inicio, fim) -> pd.DataFrame:
    # A soma dos flashes de cada célula nas horas de (início, fim]
    tabela = _tabela(conexao, "SELECT lon_celula, lat_celula, sum(flashes)::bigint AS flashes FROM raios_glm_hora "
                              "WHERE hora_utc > %s AND hora_utc <= %s GROUP BY 1, 2 ORDER BY 1, 2",
                     (_em_utc(inicio), _em_utc(fim)))
    return tabela.astype({"lon_celula": int, "lat_celula": int, "flashes": int})


@_sem_deixar_transacao
def ler_raios_recentes(conexao: psycopg.Connection, desde, ate) -> pd.DataFrame:
    # Os flashes de [desde, ate) na caixa, cada um no seu horário, direto da tabela dos flashes. O
    # flash que começou pouco antes de um arquivo vem nele: os arquivos começam 1 minuto antes
    desde, ate = _em_utc(desde), _em_utc(ate)
    tabela = _tabela(conexao, sql.SQL(ARQUIVOS_DO_TRECHO +
        "SELECT r.time_utc AS hora_utc, r.flash_lon AS longitude, r.flash_lat AS latitude "
        "FROM c JOIN {} r ON r.id_cena = c.id "
        "WHERE r.time_utc >= %(desde)s AND r.time_utc < %(ate)s AND {} ORDER BY r.time_utc")
        .format(_controle_do_glm(), _flashes_do_glm(), NA_CAIXA),
        {"de": desde - timedelta(minutes=1), "desde": desde, "ate": ate, **_caixa_dos_raios()})
    return _numeros(_horas_em_utc(tabela, ["hora_utc"]), ["longitude", "latitude"])


@_sem_deixar_transacao
def ler_celulas_do_circulo(conexao: psycopg.Connection, desde, ate, idades: list[int]) -> pd.DataFrame:
    # Os flashes de [desde, ate) no círculo e fora da caixa do estado, contados por célula de 0,1° e
    # por faixa de idade (a 0 vai até idades[0] minutos antes de `ate`, a 1 até idades[1]...), com o
    # horário do mais novo. O Agora desenha um quadradinho por célula, e não um ponto por flash: no
    # pico são ~80 mil flashes por hora no círculo. Os da caixa vêm um a um, em ler_raios_recentes
    desde, ate = _em_utc(desde), _em_utc(ate)
    tabela = _tabela(conexao, sql.SQL(ARQUIVOS_DO_TRECHO +
        "SELECT floor(r.flash_lon * %(celulas)s)::int AS lon_celula, floor(r.flash_lat * %(celulas)s)::int AS lat_celula, "
        "width_bucket(extract(epoch FROM %(ate)s - r.time_utc) / 60, %(idades)s::float8[]) AS faixa, "
        "count(*)::int AS flashes, max(r.time_utc) AS mais_novo FROM c JOIN {} r ON r.id_cena = c.id "
        "WHERE r.time_utc >= %(desde)s AND r.time_utc < %(ate)s AND {} AND NOT ({}) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")
        .format(_controle_do_glm(), _flashes_do_glm(), NO_CIRCULO, NA_CAIXA),
        {"de": desde - timedelta(minutes=1), "desde": desde, "ate": ate, "celulas": config.CELULAS_POR_GRAU_RAIOS,
         "idades": [float(minutos) for minutos in idades[:-1]], **_caixa_dos_raios(), **circulo_dos_raios()})
    tabela = _horas_em_utc(tabela, ["mais_novo"])
    return tabela.astype({"lon_celula": int, "lat_celula": int, "faixa": int, "flashes": int})


@_sem_deixar_transacao
def ler_raios_do_circulo(conexao: psycopg.Connection, desde, ate) -> pd.DataFrame:
    # Os flashes de [desde, ate) no círculo inteiro, um a um: o PNG do boletim desenha todos
    desde, ate = _em_utc(desde), _em_utc(ate)
    tabela = _tabela(conexao, sql.SQL(ARQUIVOS_DO_TRECHO +
        "SELECT r.time_utc AS hora_utc, r.flash_lon AS longitude, r.flash_lat AS latitude "
        "FROM c JOIN {} r ON r.id_cena = c.id WHERE r.time_utc >= %(desde)s AND r.time_utc < %(ate)s AND {} "
        "ORDER BY r.time_utc").format(_controle_do_glm(), _flashes_do_glm(), NO_CIRCULO),
        {"de": desde - timedelta(minutes=1), "desde": desde, "ate": ate, **circulo_dos_raios()})
    return _numeros(_horas_em_utc(tabela, ["hora_utc"]), ["longitude", "latitude"])


@_sem_deixar_transacao
def ultimo_arquivo_do_glm(conexao: psycopg.Connection, desde) -> pd.Timestamp | None:
    # A referência do arquivo processado mais novo desde então: até ali, os flashes estão no banco
    (ultimo,) = conexao.execute(sql.SQL("SELECT max(data_hora_referencia) FROM {} WHERE status = %s "
                                        "AND data_hora_referencia >= %s").format(_controle_do_glm()),
                                (CONCLUIDO, _em_utc(desde))).fetchone()
    return pd.Timestamp(ultimo).tz_convert("UTC") if ultimo is not None else None


# =====================================================
# A GLM_RT, QUE O NOSSO COLETOR ALIMENTA
# =====================================================
COLUNAS_GLM_RT = ["flash_id", "time_utc", "flash_time_offset_of_first_event", "flash_time_offset_of_last_event",
                  "flash_frame_time_offset_of_first_event", "flash_frame_time_offset_of_last_event",
                  "flash_lat", "flash_lon", "flash_area", "flash_energy", "flash_quality_flag"]
PERDIDO_PELA_NOAA = "O arquivo não apareceu no bucket da NOAA depois de a hora fechar"


@_sem_deixar_transacao
def arquivos_da_glm_rt(conexao: psycopg.Connection, de, ate) -> dict[pd.Timestamp, str]:
    # Os arquivos de [de, ate) no nosso controle, pela referência, com o status de cada um
    linhas = conexao.execute("SELECT data_hora_referencia, status FROM controle_glm_rt "
                             "WHERE data_hora_referencia >= %s AND data_hora_referencia < %s",
                             (_em_utc(de), _em_utc(ate))).fetchall()
    return {pd.Timestamp(referencia).tz_convert("UTC"): status for referencia, status in linhas}


def gravar_arquivo_glm(conexao: psycopg.Connection, referencia, chave: str, flashes: pd.DataFrame) -> int | None:
    # Um arquivo de 20 s numa transação: a linha dele no controle e os flashes. A linha é sempre nova,
    # mesmo no lugar de um "sem cena": o coletar.py raios acha o que mudou pelos ids novos. Se outro
    # processo gravou o arquivo antes (o tempo real e a busca de um dia juntos), não faz nada e
    # devolve None; senão, o id do arquivo
    referencia = _em_utc(referencia)
    conexao.execute("DELETE FROM controle_glm_rt WHERE data_hora_referencia = %s AND status <> %s",
                    (referencia, CONCLUIDO))
    linha = conexao.execute("INSERT INTO controle_glm_rt (data_hora_referencia, source_key, status, tentativas, "
                            "concluido_em) VALUES (%s, %s, %s, 1, now()) ON CONFLICT (data_hora_referencia) "
                            "DO NOTHING RETURNING id", (referencia, chave, CONCLUIDO)).fetchone()
    if linha is None:
        conexao.rollback()
        return None
    (id_cena,) = linha
    with conexao.cursor() as cursor:
        comando = sql.SQL("COPY glm_rt (id_cena, {}) FROM STDIN").format(_lista(COLUNAS_GLM_RT))
        with cursor.copy(comando) as copia:
            for valores in _linhas(flashes, COLUNAS_GLM_RT):
                copia.write_row((id_cena, *valores))
    conexao.commit()
    return id_cena


def marcar_sem_cena(conexao: psycopg.Connection, referencias: Iterable) -> None:
    # Os trechos de 20 s que a NOAA não publicou, se ainda não estão no controle
    with conexao.cursor() as cursor:
        cursor.executemany("INSERT INTO controle_glm_rt (data_hora_referencia, status, tentativas, mensagem_erro) "
                           "VALUES (%s, %s, 1, %s) ON CONFLICT (data_hora_referencia) DO NOTHING",
                           [(_em_utc(referencia), SEM_CENA, PERDIDO_PELA_NOAA) for referencia in referencias])
    conexao.commit()
