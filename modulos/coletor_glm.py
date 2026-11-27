"""O nosso coletor do GLM: leva os flashes do GOES-19 do bucket da NOAA para a glm_rt.

Fica só o que cai a até config.RAIO_DA_TABELA_GLM_KM de Campo Grande, e cada arquivo de 20 s entra
numa transação, com a linha dele no controle_glm_rt. Dois trabalhos, pelo coletar.py:

- **tempo real** (`coletar.py glm`, a cada minuto): a hora corrente e a anterior, que pega o arquivo
  da virada da hora. São ~3 arquivos novos por minuto, em poucos segundos;
- **um dia** (`coletar.py glm --dia 2026-11-26`): o dia de MS inteiro (das 04:00 às 04:00 UTC), só
  o que falta, com 8 downloads por vez. É para quando a máquina ou a rede ficaram fora por mais de
  uma hora: o tempo real não volta atrás.

A hora que fechou há mais de config.MINUTOS_PARA_DAR_O_ARQUIVO_POR_PERDIDO e não tem no bucket o
arquivo de algum trecho de 20 s fica com o trecho como "sem cena": a NOAA não o publicou. Se ele
aparecer depois, entra no lugar. A hora sem arquivo nenhum no bucket não é marcada: pode ser a
listagem, e não a NOAA. O arquivo que falha (a rede, o bucket) fica para a próxima execução.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import pandas as pd
import psycopg

from . import banco, config, glm

UMA_HORA = pd.Timedelta(hours=1)
ARQUIVOS_POR_AVISO = 500    # na busca de um dia, uma linha de progresso a cada tantos arquivos


@dataclass
class ColetaDoGLM:
    arquivos: int = 0
    flashes_no_disco: int = 0
    flashes: int = 0
    sem_cena: int = 0
    falhas: int = 0
    primeiro: pd.Timestamp | None = None
    ultimo: pd.Timestamp | None = None


def horas_do_tempo_real(agora) -> list[pd.Timestamp]:
    hora = pd.Timestamp(agora).tz_convert("UTC").floor("h")
    return [hora - UMA_HORA, hora]


def horas_do_dia(dia: date, agora) -> list[pd.Timestamp]:
    # As 24 horas UTC do dia de MS, até a corrente
    comeco = pd.Timestamp(dia).tz_localize(config.FUSO_MS).tz_convert("UTC")
    corrente = pd.Timestamp(agora).tz_convert("UTC").floor("h")
    return [hora for hora in (comeco + UMA_HORA * n for n in range(24)) if hora <= corrente]


def trechos_da_hora(hora: pd.Timestamp) -> list[pd.Timestamp]:
    passo = pd.Timedelta(seconds=config.SEGUNDOS_DO_ARQUIVO_GLM)
    return [hora + passo * n for n in range(int(UMA_HORA / passo))]


def perdidos(hora: pd.Timestamp, publicados, agora) -> list[pd.Timestamp]:
    # Os trechos da hora sem arquivo no bucket, se ela já fechou há tempo e tem algum arquivo
    fechou = hora + UMA_HORA + pd.Timedelta(minutes=config.MINUTOS_PARA_DAR_O_ARQUIVO_POR_PERDIDO)
    if pd.Timestamp(agora).tz_convert("UTC") < fechou or not publicados:
        return []
    publicados = set(publicados)
    return [trecho for trecho in trechos_da_hora(hora) if trecho not in publicados]


def _ler(item: tuple[pd.Timestamp, str]) -> tuple[pd.Timestamp, str, pd.DataFrame | None, int, str | None]:
    # Roda nas threads: baixa, lê e recorta um arquivo. A gravação fica na thread principal
    referencia, chave = item
    try:
        _, flashes = glm.ler(glm.baixar(chave))
    except glm.ErroGLM as erro:
        return referencia, chave, None, 0, str(erro)
    return referencia, chave, glm.no_circulo(flashes), len(flashes), None


def coletar_horas(conexao: psycopg.Connection, horas: list[pd.Timestamp], agora=None,
                  progresso: bool = False) -> ColetaDoGLM:
    agora = pd.Timestamp.now(tz="UTC") if agora is None else pd.Timestamp(agora).tz_convert("UTC")
    coleta = ColetaDoGLM()
    a_buscar, sem_cena = [], []
    for hora in horas:
        try:
            publicados = glm.um_por_inicio(glm.listar(hora))
        except glm.ErroGLM as erro:
            coleta.falhas += 1
            print(f"   ⚠️ {hora:%d/%m %H:%M} UTC: {erro}", flush=True)
            continue
        no_controle = banco.arquivos_da_glm_rt(conexao, hora, hora + UMA_HORA)
        a_buscar += [(referencia, chave) for referencia, chave in publicados.items()
                     if no_controle.get(referencia) != banco.CONCLUIDO]
        sem_cena += [trecho for trecho in perdidos(hora, publicados, agora) if trecho not in no_controle]
    if progresso:
        print(f"   {len(a_buscar)} arquivos para buscar no bucket", flush=True)
    with ThreadPoolExecutor(config.DOWNLOADS_SIMULTANEOS_GLM) as threads:
        for feitos, (referencia, chave, flashes, no_disco, erro) in enumerate(threads.map(_ler, a_buscar), 1):
            if erro:
                coleta.falhas += 1
                print(f"   ⚠️ {referencia:%d/%m %H:%M:%S} UTC: {erro}", flush=True)
            elif banco.gravar_arquivo_glm(conexao, referencia, chave, flashes) is not None:
                coleta.arquivos += 1
                coleta.flashes_no_disco += no_disco
                coleta.flashes += len(flashes)
                coleta.primeiro = min(coleta.primeiro or referencia, referencia)
                coleta.ultimo = max(coleta.ultimo or referencia, referencia)
            if progresso and feitos % ARQUIVOS_POR_AVISO == 0:
                print(f"   {feitos} de {len(a_buscar)} arquivos (até {referencia:%d/%m %H:%M} UTC)", flush=True)
    if sem_cena:
        banco.marcar_sem_cena(conexao, sem_cena)
        coleta.sem_cena = len(sem_cena)
    return coleta


def coletar(conexao: psycopg.Connection, agora=None) -> ColetaDoGLM:
    comeco = time.monotonic()
    coleta = coletar_horas(conexao, horas_do_tempo_real(agora or pd.Timestamp.now(tz="UTC")), agora)
    relatar(coleta, time.monotonic() - comeco)
    return coleta


def coletar_dias(conexao: psycopg.Connection, dias: list[date], agora=None) -> ColetaDoGLM:
    agora = agora or pd.Timestamp.now(tz="UTC")
    total = ColetaDoGLM()
    for dia in sorted(set(dias)):
        comeco = time.monotonic()
        horas = horas_do_dia(dia, agora)
        print(f"⚡ GLM, dia {dia:%d/%m/%Y} (horário de MS): {len(horas)} horas", flush=True)
        if not horas:
            print("   o dia ainda não começou")
            continue
        coleta = coletar_horas(conexao, horas, agora, progresso=True)
        relatar(coleta, time.monotonic() - comeco)
        for campo in ("arquivos", "flashes_no_disco", "flashes", "sem_cena", "falhas"):
            setattr(total, campo, getattr(total, campo) + getattr(coleta, campo))
    return total


def relatar(coleta: ColetaDoGLM, segundos: float) -> None:
    if coleta.arquivos:
        trecho = (f"das {coleta.primeiro:%d/%m %H:%M:%S} às {coleta.ultimo:%d/%m %H:%M:%S} UTC"
                  if coleta.primeiro != coleta.ultimo else f"das {coleta.primeiro:%d/%m %H:%M:%S} UTC")
        texto = (f"✅ GLM: {coleta.arquivos} arquivos novos ({trecho}), {coleta.flashes} flashes no círculo, "
                 f"de {coleta.flashes_no_disco} no disco")
    else:
        texto = "✅ GLM: nenhum arquivo novo"
    if coleta.sem_cena:
        texto += f"; {coleta.sem_cena} trechos sem arquivo no bucket"
    if coleta.falhas:
        texto += f"; {coleta.falhas} falhas, que ficam para a próxima execução"
    print(f"{texto} ({segundos:.0f} s)", flush=True)
