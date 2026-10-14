"""De onde vêm os dados: as APIs públicas ou o banco da UNIGEO; depois, a API de leitura.

Os produtos e o painel pedem as estações e as leituras a este módulo, e não direto a quem as
busca. Cada instalação escolhe a fonte pela configuração (`FONTE_DADOS`, no `.env` do servidor
ou nos Secrets do Streamlit Cloud), e o resto do código não muda: as contas, os mapas e as
planilhas não sabem de onde as leituras vieram. É a costura descrita em
docs/plano_arquitetura.md (passo 7).

- "apis": cada função repassa o pedido ao `inmet.py`.
- "banco": lê o cadastro, as leituras e a previsão do `climageo`, com o usuário que só lê, e
  devolve as mesmas tabelas que o `inmet.py` e o `openmeteo.py` devolveriam, coluna por coluna. A
  grade da previsão é a do coletor (0,25°), e não a da busca sob demanda (0,5°).

As funções procuram o `inmet` na hora de cada chamada, sem guardar referência às funções dele.
É o que deixa os testes trocarem a API do INMET por dados sintéticos, como sempre fizeram.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg

from . import banco, config, inmet, openmeteo

# As fontes que esta versão sabe usar
FONTES = ("apis", "banco")

# O erro que os produtos e o painel tratam, venha de qual fonte vier
ErroFonte = (inmet.ErroINMET, banco.ErroBanco)
ErroPrevisao = (openmeteo.ErroOpenMeteo, banco.ErroBanco)


def _conferir_fonte() -> None:
    if config.FONTE_DADOS not in FONTES:
        raise ValueError(f"FONTE_DADOS={config.FONTE_DADOS!r} não existe nesta versão; "
                         f"as fontes disponíveis são: {', '.join(FONTES)}")


def _do_banco() -> bool:
    _conferir_fonte()
    return config.FONTE_DADOS == "banco"


def estacoes(uf: str = config.UF) -> pd.DataFrame:
    if _do_banco():
        todas = _estacoes_do_banco()
        return todas[todas["SG_ESTADO"] == uf].reset_index(drop=True)
    return inmet.listar_estacoes(uf)


def estacoes_de_apoio(recorte: config.Recorte, lon_celulas, lat_celulas) -> pd.DataFrame:
    if _do_banco():
        return inmet.estacoes_de_apoio(recorte, lon_celulas, lat_celulas, estacoes=_estacoes_do_banco())
    return inmet.estacoes_de_apoio(recorte, lon_celulas, lat_celulas)


def leituras_da_estacao(codigo: str, inicio: datetime, fim: datetime,
                        fuso: ZoneInfo = config.FUSO_MS) -> pd.DataFrame | None:
    if _do_banco():
        cadastro = _estacoes_do_banco(so_em_operacao=False)
        return _leituras_do_banco(cadastro[cadastro["CD_ESTACAO"] == codigo], inicio, fim, fuso).get(codigo)
    return inmet.baixar_dados_estacao(codigo, inicio, fim, fuso)


def leituras_do_estado(inicio: datetime, fim: datetime, uf: str = config.UF,
                       fuso: ZoneInfo = config.FUSO_MS) -> list[tuple[pd.Series, pd.DataFrame]]:
    if _do_banco():
        do_estado = estacoes(uf)
        print(f"✅ Encontradas {len(do_estado)} estações em {uf} (no banco)")
        coletados, sem_dados = _por_estacao(do_estado, inicio, fim, fuso)
        inmet.resumir_coleta(len(do_estado), len(coletados), sem_dados, [])
        return coletados
    return inmet.baixar_estacoes(inicio, fim, uf=uf, fuso=fuso)


def leituras_de_apoio(recorte: config.Recorte, lon_celulas, lat_celulas, inicio: datetime,
                      fim: datetime, fuso: ZoneInfo = config.FUSO_MS) -> list[tuple[pd.Series, pd.DataFrame]]:
    if _do_banco():
        try:
            vizinhas = estacoes_de_apoio(recorte, lon_celulas, lat_celulas)
            if vizinhas.empty:
                return []
            coletados, sem_dados = _por_estacao(vizinhas, inicio, fim, fuso)
        except banco.ErroBanco as erro:
            print(f"⚠️ Sem as estações vizinhas ({erro}); a borda sai só com as do estado.")
            return []
        estados = ", ".join(vizinhas["SG_ESTADO"].value_counts().index)
        print(f"🧭 {len(vizinhas)} estações vizinhas para a borda ({estados}), do banco: {len(coletados)} com dados"
              + (f", {len(sem_dados)} sem" if sem_dados else ""))
        return coletados
    return inmet.baixar_apoio(recorte, lon_celulas, lat_celulas, inicio, fim, fuso=fuso)


# =====================================================
# A PREVISÃO
# =====================================================
# Os conjuntos de pontos têm o nome do banco: "grade" e "estacao"
def origem_da_previsao() -> str:
    return "o banco" if _do_banco() else "o Open-Meteo"


def pontos_da_grade() -> pd.DataFrame:
    return openmeteo.pontos_da_grade(passo=config.GRADE_PREVISAO_BANCO if _do_banco() else config.GRADE_PREVISAO)


def pontos_da_grade_semanal() -> pd.DataFrame:
    return openmeteo.pontos_da_grade(passo=config.GRADE_PREVISAO)   # a do coletor e a do painel são a mesma


def rodadas_da_previsao(conjunto: str, modelos: tuple[str, ...]) -> dict[str, datetime]:
    if _do_banco():
        with _conexao() as conexao:
            guardadas = banco.rodadas_guardadas(conexao, conjunto)
        return {modelo: guardadas[modelo].to_pydatetime() for modelo in modelos if modelo in guardadas}
    return openmeteo.rodadas(modelos)


def previsao(conjunto: str, pontos: pd.DataFrame, modelo: str, rodada: datetime | None) -> pd.DataFrame:
    if _do_banco():
        with _conexao() as conexao:
            tabela = banco.ler_previsao(conexao, conjunto, modelo, rodada)
        return tabela[tabela["ponto"].isin(pontos["ponto"])].reset_index(drop=True)
    return openmeteo.buscar(pontos, (modelo,))


def rodada_das_semanas(conjunto: str) -> datetime | None:
    if _do_banco():
        with _conexao() as conexao:
            rodada = banco.rodadas_semanais_guardadas(conexao, conjunto).get(config.MODELO_SEMANAS)
        return None if rodada is None else rodada.to_pydatetime()
    return openmeteo.rodadas((config.MODELO_SEMANAS,))[config.MODELO_SEMANAS]


def semanas(conjunto: str, pontos: pd.DataFrame, rodada: datetime | None) -> pd.DataFrame:
    if _do_banco():
        with _conexao() as conexao:
            tabela = banco.ler_semanas(conexao, conjunto, rodada)
        return tabela[tabela["ponto"].isin(pontos["ponto"])].reset_index(drop=True)
    return openmeteo.buscar_semanas(pontos)


# =====================================================
# A FONTE "BANCO"
# =====================================================
def _conectar() -> psycopg.Connection:
    return banco.conectar("leitura")


@contextmanager
def _conexao() -> Iterator[psycopg.Connection]:
    try:
        with _conectar() as conexao:
            yield conexao
    except psycopg.Error as erro:
        motivo = str(erro).strip().splitlines()[0] if str(erro).strip() else type(erro).__name__
        raise banco.ErroBanco(f"falha ao ler o banco: {motivo}") from None


def _estacoes_do_banco(so_em_operacao: bool = True) -> pd.DataFrame:
    with _conexao() as conexao:
        cadastro = banco.ler_estacoes(conexao)
    # Na ordem da lista do INMET, pelo nome: é ela que desempata os rankings dos produtos
    cadastro = cadastro.sort_values(["nome", "codigo"], kind="stable")
    # As colunas da lista do INMET que o painel e os produtos usam
    tabela = pd.DataFrame({
        "CD_ESTACAO": cadastro["codigo"], "DC_NOME": cadastro["nome"], "SG_ESTADO": cadastro["uf"],
        "VL_LATITUDE": cadastro["latitude"], "VL_LONGITUDE": cadastro["longitude"],
        "VL_ALTITUDE": cadastro["altitude"], "CD_SITUACAO": cadastro["situacao"],
    })
    if so_em_operacao:   # como o inmet.listar_estacoes: só as que a lista dá como operantes
        tabela = tabela[tabela["CD_SITUACAO"].fillna("").str.strip().str.casefold() == inmet.SITUACAO_OPERANTE]
    tabela = tabela.assign(Estação=tabela["DC_NOME"].map(inmet.formatar_nome_estacao))
    return tabela.reset_index(drop=True)


def _leituras_do_banco(estacoes: pd.DataFrame, inicio: datetime, fim: datetime,
                       fuso: ZoneInfo) -> dict[str, pd.DataFrame]:
    # Como a consulta por estação do INMET: os dias UTC inteiros, de inicio a fim, com uma linha
    # por hora, mesmo as que não chegaram (vazias). A estação sem nenhuma leitura fica de fora.
    primeiro = pd.Timestamp(inicio.astimezone(config.FUSO_UTC).date(), tz="UTC")
    ultimo = pd.Timestamp(fim.astimezone(config.FUSO_UTC).date(), tz="UTC") + pd.Timedelta(hours=23)
    if estacoes.empty:
        return {}
    with _conexao() as conexao:
        lidas = banco.ler_inmet(conexao, primeiro - timedelta(hours=1), ultimo, list(estacoes["CD_ESTACAO"]))
    horas = pd.DataFrame({"dt_utc": pd.date_range(primeiro, ultimo, freq="h")})
    medidas = {coluna: coluna.upper() for coluna in [*banco.COLUNAS_INMET, *banco.COLUNAS_INMET_EXTRAS]}
    resultado = {}
    for estacao in estacoes.itertuples(index=False):
        dela = lidas[lidas["estacao"] == estacao.CD_ESTACAO]
        if dela.empty:
            continue
        dados = horas.merge(dela.drop(columns="estacao").rename(columns={"hora_utc": "dt_utc", **medidas}),
                            on="dt_utc", how="left")
        dados = dados.assign(CD_ESTACAO=estacao.CD_ESTACAO, DC_NOME=estacao.DC_NOME, UF=estacao.SG_ESTADO,
                             VL_LATITUDE=estacao.VL_LATITUDE, VL_LONGITUDE=estacao.VL_LONGITUDE,
                             DT_MEDICAO=dados["dt_utc"].dt.strftime("%Y-%m-%d"),
                             HR_MEDICAO=dados["dt_utc"].dt.strftime("%H%M"),
                             dt_local=dados["dt_utc"].dt.tz_convert(fuso))
        resultado[estacao.CD_ESTACAO] = dados
    return resultado


def _por_estacao(estacoes: pd.DataFrame, inicio: datetime, fim: datetime,
                 fuso: ZoneInfo) -> tuple[list[tuple[pd.Series, pd.DataFrame]], list[str]]:
    lidas = _leituras_do_banco(estacoes, inicio, fim, fuso)
    coletados, sem_dados = [], []
    for _, estacao in estacoes.iterrows():
        dados = lidas.get(estacao["CD_ESTACAO"])
        if dados is None:
            sem_dados.append(estacao["Estação"])
        else:
            coletados.append((estacao, dados))
    return coletados, sem_dados
