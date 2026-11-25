"""O coletor da previsão: leva as rodadas dos modelos, do Open-Meteo, para o banco.

É o passo 5 de docs/plano_arquitetura.md. De hora em hora, o `coletar.py previsao`:
- confere a rodada mais recente do ECMWF, do GFS e do ICON; se ela é das 00Z ou das 12Z e ainda
  não está no banco, busca os 14 dias, hora a hora, na grade de MS e nos pontos das estações;
- faz o mesmo com o EC46, que roda uma vez por dia e chega por semana;
- recupera a rodada das 00Z ou das 12Z dos últimos 7 dias que passou sem entrar no banco;
- apaga as partições da grade com mais de 21 dias.

O Open-Meteo completa as rodadas das 06Z e das 18Z com a anterior (em 14/10/2026, a do ICON das
06Z ia até o fim da das 00Z): por isso só as das 00Z e das 12Z, inteiras nos três modelos.

Por que de hora em hora: a busca normal só alcança a rodada enquanto ela é a mais recente do
modelo, e cada modelo publica numa hora. Em 21/10/2026, a das 00Z do ICON ficou disponível das
~23:30 da véspera às 05:30 (horário de MS), a do GFS das ~02:00 às ~08:00 e a do ECMWF a partir das
05:00: juntas, só por meia hora. A conferência que não acha rodada nova lê só os metadados, que
não contam na cota.

A rodada que passa assim mesmo (o Open-Meteo fora do ar, a máquina desligada) vem depois da API das
rodadas avulsas, que entrega qualquer uma pela hora dela. O EC46 não tem essa API, e não é recuperado.
"""
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import psycopg

from . import banco, config, openmeteo

RODADAS_GUARDADAS = (0, 12)   # horas UTC
# A grade do coletor é mais fina que a do painel sob demanda: 607 pontos em vez de 178. Com as
# estações, duas rodadas por dia dos três modelos e o EC46 gastam ~4.800 das 10 mil chamadas diárias.
GRADE_DO_COLETOR = config.GRADE_PREVISAO_BANCO
GRADE_DAS_SEMANAS = config.GRADE_PREVISAO   # a do painel
# Se outro programa no mesmo endereço gastou o minuto da cota, a busca espera ele virar, até tantas vezes
TENTATIVAS_NO_LIMITE_POR_MINUTO = 3
NOMES_DOS_CONJUNTOS = {"grade": "grade", "estacao": "estações"}
# A recuperação: a rodada de um modelo por vez (a grade e as estações, ~670 chamadas), a mais nova
# primeiro, e só nas execuções das 00, 04, 08, 12, 16 e 20 UTC. No pior caso, 6 × 670 somadas às
# ~4.800 do dia dão ~8.800 das 10 mil: sobra cota para as rodadas novas, que vêm antes.
DIAS_DE_RECUPERACAO = 7
RECUPERA_A_CADA_HORAS = 4
RODADAS_RECUPERADAS_POR_VEZ = 1

Conectar = Callable[[], AbstractContextManager[psycopg.Connection]]


@dataclass
class Coleta:
    buscas: int = 0         # rodadas buscadas, cada conjunto de pontos contando uma
    chamadas: float = 0     # o que as buscas gastaram da cota, pela regra medida
    linhas: int = 0
    gravadas: int = 0
    apagadas: list[str] = field(default_factory=list)   # partições da grade que passaram de 21 dias
    falhas: list[str] = field(default_factory=list)
    faltam: list[str] = field(default_factory=list)     # rodadas dos últimos 7 dias que não estão no banco
    interrompida: bool = False   # parou porque a cota acabou


@dataclass
class Busca:
    modelo: str
    rodada: datetime
    conjunto: str
    pontos: pd.DataFrame
    semanal: bool
    recuperacao: bool = False

    @property
    def rotulo(self) -> str:
        rotulo = f"{openmeteo.NOMES[self.modelo]} {self.rodada:%d/%m %H}Z, {NOMES_DOS_CONJUNTOS[self.conjunto]}"
        return f"{rotulo} (recuperação)" if self.recuperacao else rotulo

    @property
    def custo(self) -> float:
        if self.semanal:
            return openmeteo.custo(len(self.pontos), variaveis=len(openmeteo.VARIAVEIS_SEMANAIS), modelos=1,
                                   dias=config.DIAS_SEMANAS)
        return openmeteo.custo(len(self.pontos), modelos=1)


Pontos = dict[bool, dict[str, pd.DataFrame]]   # semanal → conjunto → pontos


def _rodada(dicionario: dict, modelo: str) -> pd.Timestamp | None:
    rodada = dicionario.get(modelo)
    return None if rodada is None else pd.Timestamp(rodada)


def _pontos(conexao: psycopg.Connection) -> Pontos:
    estacoes = banco.pontos_das_estacoes(conexao, config.UF)
    if estacoes.empty:
        print(f"⚠️ O cadastro do banco não tem estações de {config.UF}: só a grade. "
              "O coletor do INMET é quem preenche o cadastro.")
    return {
        False: {"grade": openmeteo.pontos_da_grade(passo=GRADE_DO_COLETOR), "estacao": estacoes},
        True: {"grade": openmeteo.pontos_da_grade(passo=GRADE_DAS_SEMANAS), "estacao": estacoes},
    }


def buscas_pendentes(conexao: psycopg.Connection, agora: datetime | None, coleta: Coleta,
                     pontos: Pontos) -> tuple[list[Busca], dict[str, pd.Timestamp]]:
    pendentes, ultimas, no_banco, fora = [], {}, [], []
    for modelo in (*config.MODELOS_PREVISAO, config.MODELO_SEMANAS):
        semanal = modelo == config.MODELO_SEMANAS
        nome = openmeteo.NOMES[modelo]
        try:
            rodada = pd.Timestamp(openmeteo.rodadas((modelo,), agora)[modelo])
        except openmeteo.ErroOpenMeteo as erro:
            print(f"   ❌ {nome}: sem a rodada mais recente ({erro})")
            coleta.falhas.append(nome)
            continue
        ultimas[modelo] = rodada
        if not semanal and rodada.hour not in RODADAS_GUARDADAS:
            fora.append(f"{nome} {rodada:%d/%m %H}Z")
            continue
        antes = len(pendentes)
        for conjunto in banco.CONJUNTOS:
            if pontos[semanal][conjunto].empty:
                continue
            guardadas = (banco.rodadas_semanais_guardadas(conexao, conjunto) if semanal
                         else banco.rodadas_guardadas(conexao, conjunto))
            guardada = _rodada(guardadas, modelo)
            if guardada is None or guardada < rodada:
                pendentes.append(Busca(modelo, rodada.to_pydatetime(), conjunto, pontos[semanal][conjunto], semanal))
        if len(pendentes) == antes:
            no_banco.append(f"{nome} {rodada:%d/%m %H}Z")
    if no_banco:
        print(f"   ✔️ Já no banco: {', '.join(no_banco)}")
    if fora:
        guardadas = " e das ".join(f"{hora:02d}Z" for hora in RODADAS_GUARDADAS)
        print(f"   ⏭️ Fora das {guardadas}, as únicas que o coletor guarda: {', '.join(fora)}")
    return pendentes, ultimas


def rodadas_esperadas(ultimas: dict[str, pd.Timestamp], agora: datetime) -> list[tuple[str, pd.Timestamp]]:
    # As das 00Z e das 12Z dos últimos dias, de antes da mais recente: a mais recente é da busca normal
    desde = (pd.Timestamp(agora) - pd.Timedelta(days=DIAS_DE_RECUPERACAO)).ceil("12h")
    return [(modelo, rodada) for modelo in config.MODELOS_PREVISAO if modelo in ultimas
            for rodada in pd.date_range(desde, ultimas[modelo], freq="12h", inclusive="left")]


def rodadas_que_faltam(conexao: psycopg.Connection, ultimas: dict[str, pd.Timestamp], agora: datetime,
                       pontos: Pontos) -> list[tuple[str, pd.Timestamp, str]]:
    esperadas = rodadas_esperadas(ultimas, agora)
    return [(modelo, rodada, conjunto) for conjunto in banco.CONJUNTOS if not pontos[False][conjunto].empty
            for modelo, rodada in banco.rodadas_ausentes(conexao, conjunto, esperadas)]


def recuperacoes(faltam: list[tuple[str, pd.Timestamp, str]], agora: datetime, pontos: Pontos) -> list[Busca]:
    if pd.Timestamp(agora).hour % RECUPERA_A_CADA_HORAS:
        return []
    # A mais nova primeiro, que é a que ainda serve ao painel; na mesma hora, na ordem dos modelos
    ordem = sorted({(modelo, rodada) for modelo, rodada, _ in faltam},
                   key=lambda par: (-par[1].value, config.MODELOS_PREVISAO.index(par[0])))
    escolhidas = ordem[:RODADAS_RECUPERADAS_POR_VEZ]
    return [Busca(modelo, rodada.to_pydatetime(), conjunto, pontos[False][conjunto], False, recuperacao=True)
            for modelo, rodada in escolhidas
            for conjunto in banco.CONJUNTOS if (modelo, rodada, conjunto) in faltam]


def _buscar(busca: Busca) -> pd.DataFrame:
    for tentativa in range(1, TENTATIVAS_NO_LIMITE_POR_MINUTO + 1):
        try:
            if busca.semanal:
                return openmeteo.buscar_semanas(busca.pontos, busca.modelo)
            if busca.recuperacao:
                return openmeteo.buscar_rodada(busca.pontos, busca.modelo, busca.rodada)
            return openmeteo.buscar(busca.pontos, (busca.modelo,))
        except openmeteo.LimitePorMinuto:
            if tentativa == TENTATIVAS_NO_LIMITE_POR_MINUTO:
                raise
            print(f"   ⏳ O minuto da cota já estava gasto: tento de novo em {openmeteo.ESPERA_ENTRE_LOTES} s "
                  f"({tentativa}/{TENTATIVAS_NO_LIMITE_POR_MINUTO - 1})", flush=True)
            openmeteo._dormir(openmeteo.ESPERA_ENTRE_LOTES)


def _gravar(conexao: psycopg.Connection, busca: Busca, tabela: pd.DataFrame) -> int:
    if busca.semanal:
        return banco.gravar_semanas(conexao, tabela, busca.conjunto)
    return banco.gravar_previsao(conexao, tabela, busca.conjunto)


def _rotulos_das_que_faltam(faltam: list[tuple[str, pd.Timestamp, str]], pontos: Pontos) -> list[str]:
    conjuntos = [conjunto for conjunto in banco.CONJUNTOS if not pontos[False][conjunto].empty]
    rotulos = []
    for modelo, rodada in sorted({(modelo, rodada) for modelo, rodada, _ in faltam},
                                 key=lambda par: (par[1].value, config.MODELOS_PREVISAO.index(par[0]))):
        sem = [conjunto for conjunto in conjuntos if (modelo, rodada, conjunto) in faltam]
        parte = "" if sem == conjuntos else f" ({', '.join(NOMES_DOS_CONJUNTOS[conjunto] for conjunto in sem)})"
        rotulos.append(f"{openmeteo.NOMES[modelo]} {rodada:%d/%m %H}Z{parte}")
    return rotulos


def coletar(conectar: Conectar, agora: datetime | None = None) -> Coleta:
    comeco = time.monotonic()
    agora = agora or datetime.now(timezone.utc)
    coleta = Coleta()
    print("🌦️ Conferindo as rodadas no Open-Meteo...")
    with conectar() as conexao:
        coleta.apagadas = banco.limpar_grade(conexao, agora)
        pontos = _pontos(conexao)
        pendentes, ultimas = buscas_pendentes(conexao, agora, coleta, pontos)
        faltam = rodadas_que_faltam(conexao, ultimas, agora, pontos)

    # As rodadas novas antes das recuperadas: se a cota acabar, perde-se a que ainda pode ser recuperada.
    # Cada busca pode gastar o minuto inteiro da cota (600 chamadas): a seguinte espera ele virar.
    # A conexão com o banco abre só para gravar, porque o servidor derruba a que fica parada
    for indice, busca in enumerate(pendentes + recuperacoes(faltam, agora, pontos)):
        if indice:
            openmeteo._dormir(openmeteo.ESPERA_ENTRE_LOTES)
        print(f"🛰️ {busca.rotulo} ({len(busca.pontos)} pontos, ~{_milhar(busca.custo)} chamadas)...", flush=True)
        antes = time.monotonic()
        try:
            tabela = _buscar(busca)
        except openmeteo.CotaEsgotada as erro:
            print(f"   ⛔ {erro}: o resto fica para a próxima execução")
            coleta.falhas.append(busca.rotulo)
            coleta.interrompida = True
            break
        except openmeteo.ErroOpenMeteo as erro:
            print(f"   ❌ {erro}")
            coleta.falhas.append(busca.rotulo)
            continue
        coleta.buscas += 1
        coleta.chamadas += busca.custo
        veio = tabela["rodada_utc"].iloc[0] if not tabela.empty else busca.rodada
        if pd.Timestamp(veio) != pd.Timestamp(busca.rodada):
            print(f"   ⏭️ Chegou a rodada de {veio:%d/%m %H}Z no meio da busca: fica para a próxima execução")
            continue
        with conectar() as conexao:
            gravadas = _gravar(conexao, busca, tabela)
        coleta.linhas += len(tabela)
        coleta.gravadas += gravadas
        if busca.recuperacao:
            faltam.remove((busca.modelo, pd.Timestamp(busca.rodada), busca.conjunto))
        print(f"   {_milhar(len(tabela))} linhas, {_milhar(gravadas)} gravadas ({time.monotonic() - antes:.0f} s)")
    coleta.faltam = _rotulos_das_que_faltam(faltam, pontos)
    relatar(coleta, time.monotonic() - comeco)
    return coleta


def _milhar(numero: float) -> str:
    return f"{numero:,.0f}".replace(",", ".")


def relatar(coleta: Coleta, segundos: float) -> None:
    if coleta.apagadas:
        print(f"🧹 Grade: {len(coleta.apagadas)} partições com mais de {banco.DIAS_DA_GRADE} dias apagadas")
    if coleta.buscas or coleta.falhas:
        print(f"✅ {coleta.buscas} buscas, ~{_milhar(coleta.chamadas)} chamadas da cota, "
              f"{_milhar(coleta.gravadas)} linhas novas no banco ({segundos:.0f} s)")
    else:
        print(f"✅ Nada novo para buscar ({segundos:.0f} s)")
    if coleta.falhas:
        print(f"   ⚠️ {len(coleta.falhas)} falharam ({', '.join(coleta.falhas)}): a próxima execução tenta de "
              "novo, e a rodada que passar sem entrar no banco fica para a recuperação")
    if coleta.faltam:
        print(f"🕳️ Rodadas dos últimos {DIAS_DE_RECUPERACAO} dias que faltam no banco: {', '.join(coleta.faltam)}. "
              f"A recuperação busca uma a cada {RECUPERA_A_CADA_HORAS} horas, a mais nova primeiro")
