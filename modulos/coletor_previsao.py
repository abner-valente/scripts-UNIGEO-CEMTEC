"""O coletor da previsão: leva as rodadas dos modelos, do Open-Meteo, para o banco.

É o passo 5 de docs/plano_arquitetura.md. De hora em hora, o `coletar.py previsao`:
- confere a rodada mais recente do ECMWF, do GFS e do ICON; se ela é das 00Z ou das 12Z e ainda
  não está no banco, busca os 14 dias, hora a hora, na grade de MS e nos pontos das estações;
- faz o mesmo com o EC46, que roda uma vez por dia e chega por semana;
- apaga as partições da grade com mais de 21 dias.

O Open-Meteo completa as rodadas das 06Z e das 18Z com a anterior (em 14/10/2026, a do ICON das
06Z ia até o fim da das 00Z): por isso só as das 00Z e das 12Z, inteiras nos três modelos.
"""
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import psycopg

from . import banco, config, openmeteo

RODADAS_GUARDADAS = (0, 12)   # horas UTC
# A grade do coletor é mais fina que a do painel sob demanda: 607 pontos em vez de 178. Com as
# estações, duas rodadas por dia dos três modelos gastam ~4.100 das 10 mil chamadas diárias.
GRADE_DO_COLETOR = 0.25
GRADE_DAS_SEMANAS = config.GRADE_PREVISAO   # a mesma do painel
# Se outro programa no mesmo endereço gastou o minuto da cota, a busca espera ele virar, até tantas vezes
TENTATIVAS_NO_LIMITE_POR_MINUTO = 3
NOMES_DOS_CONJUNTOS = {"grade": "grade", "estacao": "estações"}

Conectar = Callable[[], AbstractContextManager[psycopg.Connection]]


@dataclass
class Coleta:
    buscas: int = 0         # rodadas buscadas, cada conjunto de pontos contando uma
    chamadas: float = 0     # o que as buscas gastaram da cota, pela regra medida
    linhas: int = 0
    gravadas: int = 0
    apagadas: list[str] = field(default_factory=list)   # partições da grade que passaram de 21 dias
    falhas: list[str] = field(default_factory=list)
    interrompida: bool = False   # parou porque a cota acabou


@dataclass
class Busca:
    modelo: str
    rodada: datetime
    conjunto: str
    pontos: pd.DataFrame
    semanal: bool

    @property
    def rotulo(self) -> str:
        return f"{openmeteo.NOMES[self.modelo]} {self.rodada:%d/%m %H}Z, {NOMES_DOS_CONJUNTOS[self.conjunto]}"

    @property
    def custo(self) -> float:
        if self.semanal:
            return openmeteo.custo(len(self.pontos), variaveis=len(openmeteo.VARIAVEIS_SEMANAIS), modelos=1,
                                   dias=config.DIAS_SEMANAS)
        return openmeteo.custo(len(self.pontos), modelos=1)


def _rodada(dicionario: dict, modelo: str) -> pd.Timestamp | None:
    rodada = dicionario.get(modelo)
    return None if rodada is None else pd.Timestamp(rodada)


def buscas_pendentes(conexao: psycopg.Connection, agora: datetime | None, coleta: Coleta) -> list[Busca]:
    estacoes = banco.pontos_das_estacoes(conexao, config.UF)
    if estacoes.empty:
        print(f"⚠️ O cadastro do banco não tem estações de {config.UF}: só a grade. "
              "O coletor do INMET é quem preenche o cadastro.")
    pontos = {
        False: {"grade": openmeteo.pontos_da_grade(passo=GRADE_DO_COLETOR), "estacao": estacoes},
        True: {"grade": openmeteo.pontos_da_grade(passo=GRADE_DAS_SEMANAS), "estacao": estacoes},
    }
    pendentes = []
    for modelo in (*config.MODELOS_PREVISAO, config.MODELO_SEMANAS):
        semanal = modelo == config.MODELO_SEMANAS
        nome = openmeteo.NOMES[modelo]
        try:
            rodada = pd.Timestamp(openmeteo.rodadas((modelo,), agora)[modelo])
        except openmeteo.ErroOpenMeteo as erro:
            print(f"   ❌ {nome}: sem a rodada mais recente ({erro})")
            coleta.falhas.append(nome)
            continue
        if not semanal and rodada.hour not in RODADAS_GUARDADAS:
            guardadas = " e das ".join(f"{hora:02d}Z" for hora in RODADAS_GUARDADAS)
            print(f"   ⏭️ {nome}: a mais recente é a de {rodada:%d/%m %H}Z; o coletor guarda só as das {guardadas}")
            continue
        for conjunto in banco.CONJUNTOS:
            if pontos[semanal][conjunto].empty:
                continue
            guardadas = (banco.rodadas_semanais_guardadas(conexao, conjunto) if semanal
                         else banco.rodadas_guardadas(conexao, conjunto))
            guardada = _rodada(guardadas, modelo)
            if guardada is not None and guardada >= rodada:
                print(f"   ✔️ {nome} {rodada:%d/%m %H}Z, {NOMES_DOS_CONJUNTOS[conjunto]}: já está no banco")
                continue
            pendentes.append(Busca(modelo, rodada.to_pydatetime(), conjunto, pontos[semanal][conjunto], semanal))
    return pendentes


def _buscar(busca: Busca) -> pd.DataFrame:
    for tentativa in range(1, TENTATIVAS_NO_LIMITE_POR_MINUTO + 1):
        try:
            if busca.semanal:
                return openmeteo.buscar_semanas(busca.pontos, busca.modelo)
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


def coletar(conectar: Conectar, agora: datetime | None = None) -> Coleta:
    comeco = time.monotonic()
    coleta = Coleta()
    print("🌦️ Conferindo as rodadas no Open-Meteo...")
    with conectar() as conexao:
        coleta.apagadas = banco.limpar_grade(conexao, agora)
        pendentes = buscas_pendentes(conexao, agora, coleta)

    # Cada busca pode gastar o minuto inteiro da cota (600 chamadas): a seguinte espera ele virar.
    # A conexão com o banco abre só para gravar, porque o servidor derruba a que fica parada
    for indice, busca in enumerate(pendentes):
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
        print(f"   {_milhar(len(tabela))} linhas, {_milhar(gravadas)} gravadas ({time.monotonic() - antes:.0f} s)")
    relatar(coleta, time.monotonic() - comeco)
    return coleta


def _milhar(numero: float) -> str:
    return f"{numero:,.0f}".replace(",", ".")


def relatar(coleta: Coleta, segundos: float) -> None:
    if coleta.apagadas:
        print(f"🧹 Grade: {len(coleta.apagadas)} partições com mais de {banco.DIAS_DA_GRADE} dias apagadas")
    print(f"✅ {coleta.buscas} buscas, ~{_milhar(coleta.chamadas)} chamadas da cota, {_milhar(coleta.gravadas)} "
          f"linhas novas no banco ({segundos:.0f} s)")
    if coleta.falhas:
        print(f"   ⚠️ {len(coleta.falhas)} falharam ({', '.join(coleta.falhas)}); a próxima execução tenta de novo")
