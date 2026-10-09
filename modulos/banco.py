"""O banco da UNIGEO: a única peça do projeto que fala SQL.

PostgreSQL 16, com PostGIS 3.4, no schema `climageo`. O esquema está em `banco/esquema.sql`; as
decisões que o desenham, no passo 3 de docs/plano_arquitetura.md e na decisão 6 de
docs/escopo_v0.3.1.md. Em resumo: o banco guarda só as horas, do INMET e da previsão, e o dia, a
semana e o mês saem delas na leitura; o EC46 é a exceção e fica por semana.

Três coisas moram aqui:
- **conectar**, com o papel certo: o usuário que grava (os coletores), o que só lê (o painel e a
  API) e o dos testes. As conexões vêm do .env (modelo: .env.example) e nunca ficam no código;
- **as partições**: o banco não tem pg_partman nem pg_cron, e quem cria e apaga as partições é o
  coletor, por estas funções;
- **gravar em lote e ler de volta**, em tabelas do pandas com as colunas que o resto do projeto já
  usa (as do `openmeteo.py` para a previsão).

As horas entram e saem em UTC: a conexão já abre com o fuso UTC, e o fuso do servidor
(America/Cuiaba) não muda nada nelas.
"""
import os
import re
from collections.abc import Iterable, Iterator
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import psycopg
from psycopg import sql

from . import config, openmeteo

# Como cada tabela particionada é dividida, e por qual coluna (decisão de 09/10/2026)
PARTICOES = {
    "inmet_horaria": ("mes", "hora_utc"),
    "previsao_horaria_grade": ("dia", "rodada_utc"),
    "previsao_horaria_estacao": ("mes", "rodada_utc"),
}
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
    """Falha ao falar com o banco."""


# =====================================================
# CONEXÃO
# =====================================================
def _conferir_nome(nome: str) -> str:
    """Um nome de schema ou de tabela que pode ir no SQL: letras minúsculas, números e sublinhado."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", nome):
        raise ErroBanco(f"nome inválido para o banco: {nome!r}")
    return nome


def conectar(papel: str = "leitura", schema: str = config.BANCO_SCHEMA) -> psycopg.Connection:
    """Uma conexão com o papel pedido ("gravacao", "leitura" ou "teste"), já no schema certo.

    O schema vai na frente do `public`, onde mora o PostGIS. O fuso da conexão é UTC: as horas
    voltam do banco em UTC, como entraram.
    """
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
    """Cria as tabelas e a view que faltarem, pelo banco/esquema.sql. Pode rodar de novo."""
    conexao.execute(config.BANCO_ESQUEMA.read_text(encoding="utf-8"))
    conexao.commit()


def recriar_schema_de_teste(conexao: psycopg.Connection, schema: str) -> None:
    """Apaga e recria o schema dos testes, vazio. Recusa qualquer schema que não termine em _teste.

    É a única função que apaga um schema inteiro, e a trava no nome existe para ela nunca alcançar
    o `climageo` de verdade, nem por engano de configuração.
    """
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
    """O começo e o fim (exclusivo) da partição que contém o dia."""
    if divisao == "dia":
        return dia, dia + timedelta(days=1)
    comeco = dia.replace(day=1)
    return comeco, (comeco + timedelta(days=32)).replace(day=1)


def nome_da_particao(tabela: str, comeco: date) -> str:
    """inmet_horaria_202610, previsao_horaria_grade_20261009: o período está no nome."""
    divisao, _ = PARTICOES[tabela]
    return f"{tabela}_{comeco:%Y%m%d}" if divisao == "dia" else f"{tabela}_{comeco:%Y%m}"


def _meia_noite_utc(dia: date) -> datetime:
    return datetime.combine(dia, time(0), tzinfo=timezone.utc)


def garantir_particoes(conexao: psycopg.Connection, tabela: str, de, ate) -> list[str]:
    """Cria as partições que faltarem para cobrir de `de` a `ate` (UTC). Devolve as que criou.

    Não confirma a transação: quem chama grava em seguida e confirma tudo junto.
    """
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
    """As partições da tabela, cada uma com o dia em que começa (tirado do nome)."""
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
    """Apaga as partições que terminam até `antes_de` (UTC). Devolve as que apagou.

    Cada uma é desanexada sem travar quem está lendo (`DETACH ... CONCURRENTLY`, da versão 14) e
    só então apagada. O CONCURRENTLY não roda dentro de uma transação: a conexão passa um instante
    para o modo em que cada comando se confirma sozinho, e volta como estava.
    """
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
    """Apaga as rodadas da grade com mais de 21 dias (decisão 6 do escopo)."""
    agora = agora or datetime.now(timezone.utc)
    return apagar_particoes_antigas(conexao, "previsao_horaria_grade", agora - timedelta(days=DIAS_DA_GRADE))


# =====================================================
# GRAVAR
# =====================================================
def _linhas(tabela: pd.DataFrame, colunas: list[str]) -> Iterator[tuple]:
    """As linhas como tuplas que o psycopg entende: NaN vira NULL, e as horas vão em UTC."""
    dados = tabela[colunas].copy()
    for coluna in colunas:
        if isinstance(dados[coluna].dtype, pd.DatetimeTZDtype):
            dados[coluna] = dados[coluna].dt.tz_convert("UTC")
    dados = dados.astype(object).where(dados.notna(), None)
    return dados.itertuples(index=False, name=None)


def _carregar(conexao: psycopg.Connection, tabela: str, colunas: list[str], linhas: Iterable[tuple]) -> None:
    """Copia as linhas, em lote (COPY), para uma tabela temporária `carga` com as colunas da tabela.

    A temporária some no fim da transação. Ela existe porque o COPY não sabe pular uma linha que já
    está no banco, e o INSERT que vem depois sabe (ON CONFLICT).
    """
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
    """Grava a previsão horária da grade ou das estações. Devolve quantas linhas entraram.

    `horaria` é a tabela de `openmeteo.buscar`. Cada rodada é um insert: uma linha que já está no
    banco (a mesma rodada buscada de novo) é pulada, e nada é atualizado.
    """
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
    """Grava as semanas do EC46 (a tabela de `openmeteo.buscar_semanas`). Devolve as que entraram."""
    if semanal.empty:
        return 0
    colunas = ["conjunto", *openmeteo.COLUNAS_SEMANAIS]
    dados = semanal.assign(conjunto=_conferir_conjunto(conjunto))
    _carregar(conexao, "previsao_semanal", colunas, _linhas(dados, colunas))
    gravadas = conexao.execute(sql.SQL("INSERT INTO previsao_semanal ({}) SELECT {} FROM carga "
                                       "ON CONFLICT DO NOTHING").format(_lista(colunas), _lista(colunas))).rowcount
    conexao.commit()
    return gravadas


def gravar_estacoes(conexao: psycopg.Connection, estacoes: pd.DataFrame) -> int:
    """Grava ou atualiza o cadastro das estações (colunas de `COLUNAS_ESTACAO`, as que houver).

    Um valor que não veio (a consulta por hora não traz a altitude, por exemplo) não apaga o que
    já estava no cadastro.
    """
    if estacoes.empty:
        return 0
    colunas = [coluna for coluna in COLUNAS_ESTACAO if coluna in estacoes]
    _carregar(conexao, "estacao", colunas, _linhas(estacoes.drop_duplicates("codigo"), colunas))
    atualizar = sql.SQL(", ").join(
        sql.SQL("{c} = COALESCE(EXCLUDED.{c}, estacao.{c})").format(c=sql.Identifier(coluna))
        for coluna in colunas if coluna != "codigo")
    gravadas = conexao.execute(
        sql.SQL("INSERT INTO estacao ({}) SELECT {} FROM carga ON CONFLICT (codigo) DO UPDATE SET {}, "
                "atualizada_em = now()").format(_lista(colunas), _lista(colunas), atualizar)).rowcount
    conexao.commit()
    return gravadas


def _gravar_inmet(conexao: psycopg.Connection, leituras: pd.DataFrame, medidas: list[str]) -> int:
    """Grava as medidas dadas, atualizando a hora que já estiver no banco só se alguma mudou.

    As outras medidas da hora ficam como estavam: a consulta por hora não apaga a bateria que a
    consulta por estação gravou, e vice-versa.
    """
    if leituras.empty:
        return 0
    medidas = [medida for medida in medidas if medida in leituras]
    colunas = ["estacao", "hora_utc", *medidas]
    garantir_particoes(conexao, "inmet_horaria", leituras["hora_utc"].min(), leituras["hora_utc"].max())
    _carregar(conexao, "inmet_horaria", colunas, _linhas(leituras, colunas))
    novos = sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(medida)) for medida in medidas)
    antes = sql.SQL(", ").join(sql.SQL("inmet_horaria.{}").format(sql.Identifier(medida)) for medida in medidas)
    depois = sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(medida)) for medida in medidas)
    gravadas = conexao.execute(
        sql.SQL("INSERT INTO inmet_horaria ({}) SELECT {} FROM carga ON CONFLICT (estacao, hora_utc) "
                "DO UPDATE SET {}, coletada_em = now() WHERE ({}) IS DISTINCT FROM ({})")
        .format(_lista(colunas), _lista(colunas), novos, antes, depois)).rowcount
    conexao.commit()
    return gravadas


def gravar_inmet(conexao: psycopg.Connection, leituras: pd.DataFrame) -> int:
    """Grava as leituras da consulta por hora. Devolve quantas horas entraram ou mudaram.

    `leituras` tem `estacao`, `hora_utc` e as medidas de `COLUNAS_INMET`. As estações precisam
    estar no cadastro (`gravar_estacoes`). Rebuscar as últimas 48 h, como o coletor faz, só reescreve
    a hora que o INMET mudou.
    """
    return _gravar_inmet(conexao, leituras, COLUNAS_INMET)


def gravar_extras_inmet(conexao: psycopg.Connection, extras: pd.DataFrame) -> int:
    """Grava a sensação térmica, a bateria e a temperatura do processador, da consulta por estação."""
    return _gravar_inmet(conexao, extras, COLUNAS_INMET_EXTRAS)


# =====================================================
# LER
# =====================================================
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
    """As medidas como número: uma coluna toda vazia voltaria do banco como None, e não NaN."""
    for coluna in colunas:
        tabela[coluna] = pd.to_numeric(tabela[coluna]).astype(float)
    return tabela


def rodadas_guardadas(conexao: psycopg.Connection, conjunto: str) -> dict[str, datetime]:
    """A rodada mais nova de cada modelo que está no banco, para a grade ou para as estações."""
    tabela = sql.Identifier(f"previsao_horaria_{_conferir_conjunto(conjunto)}")
    linhas = conexao.execute(sql.SQL("SELECT modelo, max(rodada_utc) FROM {} GROUP BY modelo").format(tabela)).fetchall()
    return {modelo: pd.Timestamp(rodada).tz_convert("UTC") for modelo, rodada in linhas}


def ler_previsao(conexao: psycopg.Connection, conjunto: str, modelo: str, rodada=None) -> pd.DataFrame:
    """A previsão horária de um modelo, com as colunas de `openmeteo.buscar`. Sem rodada, a mais nova."""
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


def ler_semanas(conexao: psycopg.Connection, conjunto: str, rodada=None) -> pd.DataFrame:
    """As semanas do EC46, com as colunas de `openmeteo.buscar_semanas`. Sem rodada, a mais nova."""
    _conferir_conjunto(conjunto)
    if rodada is None:
        rodada = conexao.execute("SELECT max(rodada_utc) FROM previsao_semanal WHERE conjunto = %s",
                                 (conjunto,)).fetchone()[0]
        if rodada is None:
            return pd.DataFrame(columns=openmeteo.COLUNAS_SEMANAIS)
    colunas = list(openmeteo.COLUNAS_SEMANAIS)
    resultado = _tabela(conexao, sql.SQL("SELECT {} FROM previsao_semanal WHERE conjunto = %s AND rodada_utc = %s "
                                         "ORDER BY ponto, semana").format(_lista(colunas)),
                        (conjunto, _em_utc(rodada)))
    resultado = _numeros(resultado, ["latitude", "longitude", *openmeteo.VARIAVEIS_SEMANAIS.values()])
    return _horas_em_utc(resultado, ["rodada_utc"])[colunas]


def ler_inmet(conexao: psycopg.Connection, inicio, fim, estacoes: list[str] | None = None) -> pd.DataFrame:
    """As leituras horárias na janela (início, fim], em UTC, de todas as estações ou só das dadas.

    A janela é aberta no começo, como em todo o projeto: a leitura das 00:00 fecha o dia anterior.
    """
    colunas = ["estacao", "hora_utc", *COLUNAS_INMET, *COLUNAS_INMET_EXTRAS]
    filtro = sql.SQL(" AND estacao = ANY(%s)") if estacoes is not None else sql.SQL("")
    argumentos = (_em_utc(inicio), _em_utc(fim), *([list(estacoes)] if estacoes is not None else []))
    resultado = _tabela(conexao, sql.SQL("SELECT {} FROM inmet_horaria WHERE hora_utc > %s AND hora_utc <= %s{} "
                                         "ORDER BY estacao, hora_utc").format(_lista(colunas), filtro), argumentos)
    return _horas_em_utc(_numeros(resultado, [*COLUNAS_INMET, *COLUNAS_INMET_EXTRAS]), ["hora_utc"])
