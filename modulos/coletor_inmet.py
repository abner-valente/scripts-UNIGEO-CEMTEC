"""O coletor do INMET: leva as leituras horárias de todas as estações do Brasil para o banco.

É o passo 4 de docs/plano_arquitetura.md, e o `coletar.py` chama cada um dos três trabalhos:

- **de hora em hora** (`coletar_ultimas`): atualiza o cadastro pela lista do INMET e busca os
  últimos 3 dias (72 horas), uma consulta por hora, com todas as estações em cada uma. Rebuscar
  os 3 dias pega a leitura que chegou atrasada ou que o INMET corrigiu; o banco só reescreve a
  hora que mudou;
- **uma vez por dia** (`coletar_extras`): a sensação térmica, a bateria e a temperatura do
  processador, que a consulta por hora não traz, numa consulta por estação;
- **uma vez só** (`carregar_historico`): as horas desde 2018, dia a dia, retomando de onde parou.

O INMET aceita 100 consultas por minuto de cada endereço de rede (medido em 09/10/2026), e o
coletor divide esse limite com a equipe, se ela sair pelo mesmo endereço. Por isso ele faz uma
consulta por vez, no máximo uma por segundo (`config.INTERVALO_DO_COLETOR`); a recusa por limite
é tratada no `inmet.py`. As medidas são gravadas como a API manda (UTC, vento em m/s), com uma
exceção: a radiação, arredondada como na consulta por estação.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import psycopg

from . import banco, config, inmet

# Os últimos 3 dias, rebuscados a cada execução (decidido em 09/10/2026; o plano previa 48 h).
# Custa 72 consultas por hora, ~75 s no ritmo de uma por segundo.
HORAS_REBUSCADAS = 72
INICIO_DO_HISTORICO = datetime(2018, 1, 1, tzinfo=timezone.utc)   # plano, passo 4
# Os extras gravam a cada tantas estações: uma conexão parada por 10 minutos o servidor derruba, e
# as ~500 consultas por estação levam uns 9
ESTACOES_POR_LOTE = 50

# As medidas: o código da API e o nome no banco, que é o mesmo em minúsculas
MEDIDAS = {coluna.upper(): coluna for coluna in banco.COLUNAS_INMET}
EXTRAS = {coluna.upper(): coluna for coluna in banco.COLUNAS_INMET_EXTRAS}


@dataclass
class Coleta:
    """O que uma coleta fez: para o relato e para o código de saída do `coletar.py`."""
    consultas: int = 0      # horas pedidas (ou estações, nos extras)
    leituras: int = 0       # linhas com alguma medida
    gravadas: int = 0       # as que entraram no banco ou mudaram nele
    estacoes_novas: int = 0
    falhas: list[str] = field(default_factory=list)   # as horas (ou estações) cuja consulta falhou

    def somar(self, outra: "Coleta") -> None:
        self.consultas += outra.consultas
        self.leituras += outra.leituras
        self.gravadas += outra.gravadas
        self.estacoes_novas += outra.estacoes_novas
        self.falhas += outra.falhas


# =====================================================
# DA API PARA O BANCO
# =====================================================
def _radiacao_com_uma_casa(valores: pd.Series) -> pd.Series:
    """A radiação arredondada como a consulta por estação a mostra: uma casa, com o meio para cima.

    A consulta por hora manda três casas (3523,619 kJ/m²), e a por estação, uma (3523,6). Gravando
    como a por estação, o banco devolve ao painel e aos produtos o mesmo número que eles recebem
    hoje, e o passo 7 do plano (o banco contra as APIs) pode exigir igualdade. Conferido em
    09/10/2026 em 108 horas de seis estações: é o "meio para cima" (1724,55 vira 1724,6), e não o
    `round` do Python, que leva o meio para o par.
    """
    return valores.map(lambda valor: valor if pd.isna(valor) else
                       float(Decimal(str(float(valor))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)))


def leituras_para_o_banco(dados: pd.DataFrame, medidas: dict[str, str] = MEDIDAS) -> pd.DataFrame:
    """As leituras da API (de `inmet.baixar_hora` ou de `baixar_dados_estacao`) com os nomes do banco.

    Sai a linha sem nenhuma das medidas: a consulta por hora traz todas as estações, até as em pane
    e as que já fecharam, e umas 30% vêm vazias.
    """
    presentes = {api: nome for api, nome in medidas.items() if api in dados}
    if dados.empty or not presentes:
        return pd.DataFrame(columns=["estacao", "hora_utc", *medidas.values()])
    tabela = dados.rename(columns={"CD_ESTACAO": "estacao", "dt_utc": "hora_utc", **presentes})
    tabela = tabela[["estacao", "hora_utc", *presentes.values()]]
    tabela = tabela.dropna(subset=list(presentes.values()), how="all").dropna(subset=["estacao"])
    if "rad_glo" in tabela:
        tabela["rad_glo"] = _radiacao_com_uma_casa(tabela["rad_glo"])
    return tabela.reset_index(drop=True)


def _coluna(tabela: pd.DataFrame, nome: str) -> pd.Series:
    return tabela[nome] if nome in tabela else pd.Series(None, index=tabela.index, dtype=object)


def estacoes_da_lista(lista: pd.DataFrame) -> pd.DataFrame:
    """O cadastro, com os nomes do banco, a partir da lista do INMET (`inmet.lista_do_inmet`)."""
    tabela = pd.DataFrame({
        "codigo": _coluna(lista, "CD_ESTACAO").str.strip(),
        "nome": _coluna(lista, "DC_NOME").str.strip(),
        "uf": _coluna(lista, "SG_ESTADO").str.strip(),
        "latitude": pd.to_numeric(_coluna(lista, "VL_LATITUDE"), errors="coerce"),
        "longitude": pd.to_numeric(_coluna(lista, "VL_LONGITUDE"), errors="coerce"),
        "altitude": pd.to_numeric(_coluna(lista, "VL_ALTITUDE"), errors="coerce"),
        "situacao": _coluna(lista, "CD_SITUACAO"),
        "inicio_operacao": pd.to_datetime(_coluna(lista, "DT_INICIO_OPERACAO"), errors="coerce", utc=True,
                                          format="ISO8601"),
    })
    return tabela.dropna(subset=["codigo", "nome", "uf", "latitude", "longitude"])


def estacoes_das_leituras(dados: pd.DataFrame) -> pd.DataFrame:
    """O cadastro mínimo (código, nome, UF e coordenadas) das estações da consulta por hora."""
    if dados.empty:
        return pd.DataFrame(columns=["codigo", "nome", "uf", "latitude", "longitude"])
    tabela = pd.DataFrame({
        "codigo": _coluna(dados, "CD_ESTACAO"),
        "nome": _coluna(dados, "DC_NOME").str.strip(),
        "uf": _coluna(dados, "UF"),
        "latitude": _coluna(dados, "VL_LATITUDE"),
        "longitude": _coluna(dados, "VL_LONGITUDE"),
    })
    return tabela.dropna().drop_duplicates("codigo")


class _Ritmo:
    """No máximo uma consulta a cada `config.INTERVALO_DO_COLETOR` segundos, no coletor inteiro."""

    def __init__(self):
        self.ultima = float("-inf")

    def esperar(self) -> None:
        falta = self.ultima + config.INTERVALO_DO_COLETOR - time.monotonic()
        if falta > 0:
            time.sleep(falta)
        self.ultima = time.monotonic()


_RITMO = _Ritmo()


# =====================================================
# AS HORAS
# =====================================================
def _hora_cheia(momento: datetime | None) -> datetime:
    momento = (momento or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return momento.replace(minute=0, second=0, microsecond=0)


def ultimas_horas(agora: datetime | None = None, quantas: int = HORAS_REBUSCADAS) -> list[datetime]:
    """As últimas horas cheias (UTC), da mais antiga até a corrente.

    A corrente entra: a leitura das 14:00, que fecha a hora das 13 às 14, chega aos poucos depois
    das 14:00 (a das 15:00 de 09/10/2026 tinha ~88% das estações às 15:26), e o que faltar dela a
    próxima execução completa.
    """
    agora = _hora_cheia(agora)
    return [agora - timedelta(hours=atras) for atras in range(quantas - 1, -1, -1)]


def _rotulo(hora: datetime) -> str:
    return f"{hora:%d/%m/%Y %H:%M}"


# =====================================================
# COLETAR
# =====================================================
def atualizar_cadastro(conexao: psycopg.Connection) -> int | None:
    """Grava a lista do INMET no cadastro. Devolve quantas estações entraram ou mudaram.

    Se a lista falhar, devolve None e a coleta segue: as estações novas entram pela consulta por
    hora, com o cadastro mínimo, e a lista completa o resto numa próxima execução.
    """
    try:
        lista = inmet.lista_do_inmet()
    except inmet.ErroINMET as erro:
        print(f"⚠️ Sem a lista de estações do INMET ({erro}); o cadastro fica como está.")
        return None
    return banco.gravar_estacoes(conexao, estacoes_da_lista(lista))


def coletar_horas(conexao: psycopg.Connection, horas: list[datetime]) -> Coleta:
    """Busca as horas dadas, uma consulta por hora, e grava tudo junto no fim.

    Uma hora que falha não derruba as outras: fica em `falhas`, e a próxima execução a busca de novo.
    """
    coleta = Coleta(consultas=len(horas))
    partes = []
    for hora in horas:
        _RITMO.esperar()
        try:
            partes.append(inmet.baixar_hora(hora))
        except inmet.ErroINMET as erro:
            print(f"    ❌ {_rotulo(hora)} UTC: {erro}")
            coleta.falhas.append(_rotulo(hora))
    partes = [parte for parte in partes if not parte.empty]
    if not partes:
        return coleta
    dados = pd.concat(partes, ignore_index=True)
    leituras = leituras_para_o_banco(dados)
    # A estação com leitura que não está na lista do INMET (as que fecharam) entra pelo que a
    # consulta por hora traz. As que vêm vazias não entram: o cadastro é a lista do INMET mais as
    # estações de que o banco tem dado.
    com_leitura = dados[dados["CD_ESTACAO"].isin(leituras["estacao"])]
    coleta.estacoes_novas = banco.gravar_estacoes(conexao, estacoes_das_leituras(com_leitura), so_as_novas=True)
    coleta.leituras = len(leituras)
    coleta.gravadas = banco.gravar_inmet(conexao, leituras)
    return coleta


def coletar_ultimas(conexao: psycopg.Connection, agora: datetime | None = None,
                    quantas: int = HORAS_REBUSCADAS) -> Coleta:
    """O trabalho de hora em hora: o cadastro e os últimos 3 dias."""
    comeco = time.monotonic()
    mudadas = atualizar_cadastro(conexao)
    if mudadas is not None:
        print(f"🗂️ Cadastro: {mudadas} estações novas ou mudadas na lista do INMET")
    horas = ultimas_horas(agora, quantas)
    print(f"🛰️ Buscando {len(horas)} horas, de {_rotulo(horas[0])} a {_rotulo(horas[-1])} UTC...")
    coleta = coletar_horas(conexao, horas)
    relatar(coleta, time.monotonic() - comeco, "horas")
    return coleta


def coletar_extras(conexao: psycopg.Connection, agora: datetime | None = None,
                   quantas: int = HORAS_REBUSCADAS) -> Coleta:
    """O trabalho de uma vez por dia: a sensação térmica, a bateria e a temperatura do processador.

    Uma consulta por estação, para os últimos 3 dias, e só das estações que têm leitura no banco
    nessas horas: as outras não têm o que responder. São umas 500 consultas, uns 9 minutos, que
    vão sendo gravadas de 50 em 50 estações. Só essas três medidas são gravadas; as outras
    continuam as da consulta por hora.
    """
    comeco = time.monotonic()
    fim = _hora_cheia(agora)
    inicio = fim - timedelta(hours=quantas)
    estacoes = banco.estacoes_com_leituras(conexao, inicio, fim)
    print(f"🔋 Buscando a sensação térmica, a bateria e o processador de {len(estacoes)} estações, "
          f"de {_rotulo(inicio)} a {_rotulo(fim)} UTC...")
    coleta = Coleta(consultas=len(estacoes))
    for primeira in range(0, len(estacoes), ESTACOES_POR_LOTE):
        partes = []
        for codigo in estacoes[primeira:primeira + ESTACOES_POR_LOTE]:
            _RITMO.esperar()
            try:
                dados = inmet.baixar_dados_estacao(codigo, inicio, fim)
            except inmet.ErroINMET as erro:
                print(f"    ❌ {codigo}: {erro}")
                coleta.falhas.append(codigo)
                continue
            if dados is not None:
                partes.append(leituras_para_o_banco(dados, EXTRAS))
        partes = [parte for parte in partes if not parte.empty]
        if partes:
            extras = pd.concat(partes, ignore_index=True)
            coleta.leituras += len(extras)
            coleta.gravadas += banco.gravar_extras_inmet(conexao, extras)
    relatar(coleta, time.monotonic() - comeco, "estações")
    return coleta


def carregar_historico(conexao: psycopg.Connection, desde: datetime = INICIO_DO_HISTORICO,
                       ate: datetime | None = None) -> Coleta:
    """As horas de `desde` até `ate` (UTC), dia a dia, pulando as que já estão no banco.

    Pode parar e retomar: cada dia é gravado quando termina, e a execução seguinte só busca as
    horas que faltam. Desde 2018 são umas 77 mil consultas, a uma por segundo: perto de 22 horas.
    Um dia em que todas as horas falham quer dizer que o INMET saiu do ar, e a carga para ali em
    vez de passar a noite falhando.
    """
    comeco = time.monotonic()
    desde, ate = _hora_cheia(desde), _hora_cheia(ate)
    mudadas = atualizar_cadastro(conexao)
    if mudadas is not None:
        print(f"🗂️ Cadastro: {mudadas} estações novas ou mudadas na lista do INMET")
    print(f"📚 Carregando as horas de {_rotulo(desde)} a {_rotulo(ate)} UTC (as que já estão no banco ficam)...")
    total = Coleta()
    dia = desde.replace(hour=0)
    while dia <= ate:
        horas = [hora for hora in (dia + timedelta(hours=n) for n in range(24)) if desde <= hora <= ate]
        guardadas = banco.horas_guardadas(conexao, horas[0], horas[-1]) if horas else set()
        faltam = [hora for hora in horas if pd.Timestamp(hora) not in guardadas]
        if faltam:
            antes = time.monotonic()
            coleta = coletar_horas(conexao, faltam)
            total.somar(coleta)
            falhas = f", {len(coleta.falhas)} falharam" if coleta.falhas else ""
            print(f"   {dia:%d/%m/%Y}: {len(faltam)} hora{'s' if len(faltam) > 1 else ''}, "
                  f"{coleta.leituras} leituras{falhas} ({time.monotonic() - antes:.0f} s)", flush=True)
            if len(coleta.falhas) == len(faltam):
                print(f"❌ O INMET não respondeu a nenhuma hora de {dia:%d/%m/%Y}: a carga para aqui. "
                      "Rode de novo mais tarde; ela retoma de onde parou.")
                break
        dia += timedelta(days=1)
    relatar(total, time.monotonic() - comeco, "horas")
    return total


def relatar(coleta: Coleta, segundos: float, o_que: str) -> None:
    """Fecha a coleta com os números e as falhas, que a próxima execução tenta de novo."""
    print(f"✅ {coleta.consultas} {o_que} consultadas, {coleta.leituras} leituras: "
          f"{coleta.gravadas} novas ou mudadas no banco ({segundos:.0f} s)")
    if coleta.estacoes_novas:
        print(f"   🆕 {coleta.estacoes_novas} estações novas no cadastro, vindas da consulta por hora")
    if coleta.falhas:
        mostradas = ", ".join(coleta.falhas[:10]) + (", ..." if len(coleta.falhas) > 10 else "")
        print(f"   ⚠️ {len(coleta.falhas)} {o_que} falharam ({mostradas}); a próxima execução tenta de novo")
