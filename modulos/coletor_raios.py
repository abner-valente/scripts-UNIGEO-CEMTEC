"""O coletor dos raios: conta os flashes do GLM (GOES-19) do estado por hora e por célula de 0,1°.

Os flashes vêm da glm_rt, que o nosso coletor do GLM alimenta (coletor_glm.py), ou das tabelas de um
colega da unidade (config.FONTE_GLM): uma linha por flash. O painel não lê um mês dali; lê a contagem
que este coletor guarda em `raios_glm_hora` (veja banco/esquema.sql).

O `coletar.py raios` roda de 10 em 10 minutos. Cada execução:

- lê no controle os arquivos de 20 s que entraram desde a anterior: os do tempo real e os de um dia
  buscado à mão, ou os do histórico que o colega preenchia de trás para frente;
- junta as horas que já estavam contadas sem os 180 arquivos: o arquivo pendente pode chegar depois;
- dessas horas, recontar só as que têm arquivo diferente do que estava na contagem.

A contagem de uma hora sai de uma consulta no próprio servidor (~3 s para 24 h, medido em 19/11/2026),
que apaga e grava a hora inteira: rodar de novo não duplica nada.
"""
import time
from dataclasses import dataclass, field

import pandas as pd
import psycopg

from . import banco, config

# O controle é relido com esta folga de ids: dois arquivos processados ao mesmo tempo podem
# entrar fora da ordem dos ids. O que já estava contado não é recontado
FOLGA_DE_IDS = 1000
HORAS_POR_CONSULTA = 12
# Se o plano da consulta sair ruim, ela para antes de pesar no banco de todo mundo
LIMITE_DA_CONSULTA = "5min"
UMA_HORA = pd.Timedelta(hours=1)


@dataclass
class ColetaDeRaios:
    arquivos_novos: int = 0
    horas: list[pd.Timestamp] = field(default_factory=list)
    flashes: int = 0
    incompletas: int = 0


def hora_do_arquivo(referencias: pd.Series) -> pd.Series:
    # A hora de cada arquivo pelo fim dela, como na chuva: o das 13:59:40 vai para a das 14:00
    return referencias.dt.floor("h") + UMA_HORA


def trechos(horas: list[pd.Timestamp], maximo: int = HORAS_POR_CONSULTA) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    # As horas seguidas viram trechos (de, até], de até `maximo` horas, uma consulta cada
    trechos_, comeco, anterior = [], None, None
    for hora in sorted(set(horas)):
        if comeco is not None and hora - anterior == UMA_HORA and (hora - comeco) / UMA_HORA < maximo:
            anterior = hora
            continue
        if comeco is not None:
            trechos_.append((comeco - UMA_HORA, anterior))
        comeco = anterior = hora
    if comeco is not None:
        trechos_.append((comeco - UMA_HORA, anterior))
    return trechos_


def a_recontar(atuais: pd.DataFrame, guardadas: pd.DataFrame) -> list[pd.Timestamp]:
    # As horas cujos arquivos mudaram desde a última contagem, ou que nunca foram contadas
    juntas = atuais.merge(guardadas, on="hora_utc", how="left", suffixes=("", "_contados"))
    mudou = ((juntas["arquivos"] != juntas["arquivos_contados"])
             | (juntas["sem_arquivo"] != juntas["sem_arquivo_contados"]))
    return list(juntas.loc[mudou, "hora_utc"])


def coletar(conexao: psycopg.Connection) -> ColetaDeRaios:
    comeco = time.monotonic()
    conexao.execute(f"SET statement_timeout = '{LIMITE_DA_CONSULTA}'")
    coleta = ColetaDeRaios()
    ultimo = banco.ultimo_id_dos_raios(conexao)
    novos = banco.controle_do_glm(conexao, max(ultimo - FOLGA_DE_IDS, 0))
    coleta.arquivos_novos = int((novos["id"] > ultimo).sum())
    candidatas = set(hora_do_arquivo(novos["referencia"])) | set(banco.horas_de_raios_incompletas(conexao))
    if candidatas:
        atuais = banco.arquivos_do_glm(conexao, candidatas)
        guardadas = banco.cobertura_dos_raios(conexao, horas=candidatas)
        coleta.horas = sorted(a_recontar(atuais, guardadas))
        print(f"⚡ {coleta.arquivos_novos} arquivos novos no controle do GLM; {len(coleta.horas)} horas para "
              f"recontar", flush=True)
        for de, ate in trechos(coleta.horas):
            antes = time.monotonic()
            flashes = banco.contar_raios(conexao, de, ate, atuais)
            coleta.flashes += flashes
            horas = round((ate - de) / UMA_HORA)
            print(f"   {de:%d/%m %H:%M} a {ate:%d/%m %H:%M} UTC: {horas} h, {flashes} flashes "
                  f"({time.monotonic() - antes:.1f} s)", flush=True)
    if not novos.empty:
        banco.guardar_ultimo_id_dos_raios(conexao, max(int(novos["id"].max()), ultimo))
    coleta.incompletas = len(banco.horas_de_raios_incompletas(conexao))
    relatar(conexao, coleta, time.monotonic() - comeco)
    return coleta


def relatar(conexao: psycopg.Connection, coleta: ColetaDeRaios, segundos: float) -> None:
    primeira, ultima = banco.alcance_dos_raios(conexao)
    alcance = (f"; o banco tem de {primeira - UMA_HORA:%d/%m/%Y %H:%M} a {ultima:%d/%m/%Y %H:%M} UTC"
               if primeira is not None else "")
    print(f"✅ {len(coleta.horas)} horas recontadas, {coleta.flashes} flashes na caixa ({segundos:.0f} s){alcance}")
    if coleta.incompletas:
        print(f"   ⏳ {coleta.incompletas} horas sem os {config.ARQUIVOS_GLM_POR_HORA} arquivos (a hora corrente, "
              "o começo do histórico, os pendentes e os que o tempo real do GLM perdeu): são conferidas a cada "
              "execução e recontadas se o arquivo chegar")
