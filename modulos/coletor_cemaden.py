"""O coletor do CEMADEN: leva a chuva dos pluviômetros de MS, leitura por leitura, para o banco.

O `coletar.py` chama os dois trabalhos:

- **de hora em hora** (`coletar_ultimos`): atualiza o cadastro pela PED e busca os últimos 3 dias
  UTC (hoje e os dois anteriores), uma consulta por dia, com todas as estações do estado. Rebuscar
  pega a leitura que chegou atrasada (a estação sem sinal guarda e manda depois) ou que a PED
  corrigiu, e cobre o banco fora do ar por até dois dias; o banco só reescreve a leitura que mudou;
- **uma vez só** (`carregar_historico`): os dias desde março de 2015, quando a primeira estação de
  MS foi instalada, retomando de onde parou. O dia já guardado não é pedido de novo.

A PED aceita 12 consultas por minuto de cada conta, e o cliente (`cemaden.py`) faz no máximo 10: a
execução de hora em hora, com 4 consultas, leva uns 20 s; o histórico, ~4.300 dias, umas 8 horas
(medido em 05/11/2026: 5 dias em 36 s).
"""
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import psycopg

from . import banco, cemaden, inmet
from .coletor_inmet import Coleta

DIAS_REBUSCADOS = 3
# A primeira estação de MS no cadastro foi instalada em 06/03/2015; antes, a PED responde que não
# há resultado
INICIO_DO_HISTORICO = date(2015, 3, 1)
# No histórico, uma gravação a cada tantos dias: um minuto de consultas, no ritmo do cliente
DIAS_POR_LOTE = 10
# Consultas seguidas sem resposta (cada uma já com as suas tentativas) que dizem que a PED está fora
# do ar: a coleta para, e o resto fica para depois
FALHAS_SEGUIDAS = 4


def _hoje(agora: datetime | None = None) -> date:
    return (agora or datetime.now(timezone.utc)).astimezone(timezone.utc).date()


def ultimos_dias(agora: datetime | None = None, quantos: int = DIAS_REBUSCADOS) -> list[date]:
    hoje = _hoje(agora)
    return [hoje - timedelta(days=atras) for atras in range(quantos - 1, -1, -1)]


def _rotulo(dia: date) -> str:
    return f"{dia:%d/%m/%Y}"


def estacoes_das_leituras(leituras: pd.DataFrame) -> pd.DataFrame:
    # A estação que manda leitura e não está no cadastro da PED (a que foi desativada, no histórico)
    # entra pelo que a leitura diz dela: nome, município e coordenadas. O resto fica vazio
    tabela = leituras.dropna(subset=["codigo", "nome"]).drop_duplicates("codigo", keep="last")
    tabela = tabela.assign(municipio=tabela["municipio"].map(
        lambda nome: inmet.formatar_nome_estacao(nome) if isinstance(nome, str) else None))
    return tabela.reindex(columns=cemaden.COLUNAS_DO_CADASTRO).reset_index(drop=True)


def atualizar_cadastro(conexao: psycopg.Connection) -> int | None:
    try:
        cadastro = cemaden.cadastro()
    except cemaden.ErroCEMADEN as erro:
        print(f"⚠️ Sem o cadastro da PED ({erro}); o do banco fica como está.")
        return None
    return banco.gravar_cemaden_estacoes(conexao, cadastro)


def coletar_dias(conexao: psycopg.Connection, dias: list[date]) -> Coleta:
    coleta = Coleta()
    partes = []
    seguidas = 0
    for dia in dias:
        coleta.consultas += 1
        try:
            partes.append(cemaden.chuva_do_dia(dia))
            seguidas = 0
        except cemaden.ErroCEMADEN as erro:
            print(f"    ❌ {_rotulo(dia)}: {erro}")
            coleta.falhas.append(_rotulo(dia))
            seguidas += 1
            if seguidas >= FALHAS_SEGUIDAS:
                print(f"    ⛔ {seguidas} consultas seguidas sem resposta: a PED parece fora do ar, e o resto fica "
                      "para depois")
                coleta.interrompida = True
                break
    partes = [parte for parte in partes if not parte.empty]
    if not partes:
        return coleta
    leituras = pd.concat(partes, ignore_index=True)
    # Antes da chuva, a estação que ainda não está no banco: a leitura dela quebraria a chave do
    # cadastro e derrubaria a gravação de todas as outras
    coleta.estacoes_novas = banco.gravar_cemaden_estacoes(conexao, estacoes_das_leituras(leituras), so_as_novas=True)
    coleta.leituras = len(leituras)
    coleta.gravadas = banco.gravar_cemaden_chuva(conexao, leituras)
    return coleta


def coletar_ultimos(conexao: psycopg.Connection, agora: datetime | None = None,
                    quantos: int = DIAS_REBUSCADOS) -> Coleta:
    comeco = time.monotonic()
    mudadas = atualizar_cadastro(conexao)
    if mudadas is not None:
        print(f"🗂️ Cadastro: {mudadas} estações novas ou mudadas na PED")
    dias = ultimos_dias(agora, quantos)
    print(f"🌧️ Buscando a chuva de {len(dias)} dias, de {_rotulo(dias[0])} a {_rotulo(dias[-1])} UTC...")
    coleta = coletar_dias(conexao, dias)
    relatar(coleta, time.monotonic() - comeco)
    return coleta


def carregar_historico(conexao: psycopg.Connection, desde: date = INICIO_DO_HISTORICO,
                       ate: date | None = None) -> Coleta:
    comeco = time.monotonic()
    ate = min(ate or _hoje(), _hoje())   # nada do futuro
    mudadas = atualizar_cadastro(conexao)
    if mudadas is not None:
        print(f"🗂️ Cadastro: {mudadas} estações novas ou mudadas na PED")
    guardados = banco.dias_cemaden_guardados(conexao, desde, ate) if desde <= ate else set()
    faltam = [desde + timedelta(days=n) for n in range((ate - desde).days + 1)]
    faltam = [dia for dia in faltam if dia not in guardados]
    print(f"📚 Carregando os dias de {_rotulo(desde)} a {_rotulo(ate)} UTC: {len(faltam)} faltam no banco "
          f"(os que já estão ficam)...")
    total = Coleta()
    for primeiro in range(0, len(faltam), DIAS_POR_LOTE):
        lote = faltam[primeiro:primeiro + DIAS_POR_LOTE]
        antes = time.monotonic()
        coleta = coletar_dias(conexao, lote)
        total.somar(coleta)
        falhas = f", {len(coleta.falhas)} falharam" if coleta.falhas else ""
        print(f"   {_rotulo(lote[0])} a {_rotulo(lote[-1])}: {len(lote)} dias, {coleta.leituras} leituras{falhas} "
              f"({time.monotonic() - antes:.0f} s)", flush=True)
        if coleta.interrompida:
            print(f"❌ A PED parou de responder perto de {_rotulo(lote[0])}: a carga para aqui.")
            break
    relatar(total, time.monotonic() - comeco)
    return total


def relatar(coleta: Coleta, segundos: float) -> None:
    print(f"✅ {coleta.consultas} dias consultados na PED, {coleta.leituras} leituras: "
          f"{coleta.gravadas} novas ou mudadas no banco ({segundos:.0f} s)")
    if coleta.estacoes_novas:
        print(f"   🆕 {coleta.estacoes_novas} estações novas no cadastro, vindas das leituras (fora do cadastro da PED)")
    if coleta.falhas:
        mostradas = ", ".join(coleta.falhas[:10]) + (", ..." if len(coleta.falhas) > 10 else "")
        print(f"   ⚠️ {len(coleta.falhas)} dias falharam ({mostradas}); a próxima execução tenta de novo")
