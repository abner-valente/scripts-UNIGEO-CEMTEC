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
# Consultas seguidas sem resposta (cada uma já com as suas tentativas) que dizem que o INMET está
# fora do ar: a coleta para, e o resto fica para depois. Em 14/10/2026 ele respondeu 500 a tudo
# por horas, e o coletor insistia hora por hora
FALHAS_SEGUIDAS = 4

# As medidas: o código da API e o nome no banco, que é o mesmo em minúsculas
MEDIDAS = {coluna.upper(): coluna for coluna in banco.COLUNAS_INMET}
EXTRAS = {coluna.upper(): coluna for coluna in banco.COLUNAS_INMET_EXTRAS}


@dataclass
class Coleta:
    consultas: int = 0      # horas pedidas (ou estações, nos extras)
    leituras: int = 0       # linhas com alguma medida
    gravadas: int = 0       # as que entraram no banco ou mudaram nele
    estacoes_novas: int = 0
    falhas: list[str] = field(default_factory=list)   # as horas (ou estações) cuja consulta falhou
    interrompida: bool = False   # parou porque o INMET saiu do ar

    def somar(self, outra: "Coleta") -> None:
        self.consultas += outra.consultas
        self.leituras += outra.leituras
        self.gravadas += outra.gravadas
        self.estacoes_novas += outra.estacoes_novas
        self.falhas += outra.falhas
        self.interrompida = self.interrompida or outra.interrompida


def _fora_do_ar(coleta: Coleta, seguidas: int) -> bool:
    if seguidas < FALHAS_SEGUIDAS:
        return False
    print(f"    ⛔ {seguidas} consultas seguidas sem resposta: o INMET parece fora do ar, e o resto fica para depois")
    coleta.interrompida = True
    return True


# =====================================================
# DA API PARA O BANCO
# =====================================================
def _radiacao_com_uma_casa(valores: pd.Series) -> pd.Series:
    return valores.map(lambda valor: valor if pd.isna(valor) else
                       float(Decimal(str(float(valor))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)))


def leituras_para_o_banco(dados: pd.DataFrame, medidas: dict[str, str] = MEDIDAS) -> pd.DataFrame:
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
    agora = _hora_cheia(agora)
    return [agora - timedelta(hours=atras) for atras in range(quantas - 1, -1, -1)]


def _rotulo(hora: datetime) -> str:
    return f"{hora:%d/%m/%Y %H:%M}"


# =====================================================
# COLETAR
# =====================================================
def atualizar_cadastro(conexao: psycopg.Connection) -> int | None:
    try:
        lista = inmet.lista_do_inmet()
    except inmet.ErroINMET as erro:
        print(f"⚠️ Sem a lista de estações do INMET ({erro}); o cadastro fica como está.")
        return None
    return banco.gravar_estacoes(conexao, estacoes_da_lista(lista))


def coletar_horas(conexao: psycopg.Connection, horas: list[datetime]) -> Coleta:
    coleta = Coleta()
    partes = []
    seguidas = 0
    for hora in horas:
        _RITMO.esperar()
        coleta.consultas += 1
        try:
            partes.append(inmet.baixar_hora(hora))
            seguidas = 0
        except inmet.ErroINMET as erro:
            print(f"    ❌ {_rotulo(hora)} UTC: {erro}")
            coleta.falhas.append(_rotulo(hora))
            seguidas += 1
            if _fora_do_ar(coleta, seguidas):
                break
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
    # A que nem assim entrou (fora da lista e sem nome, UF ou coordenadas na consulta por hora)
    # fica de fora, com aviso: a leitura dela quebraria a chave do cadastro e derrubaria a gravação
    # de todas as outras. Em 09 e 13/10/2026, de 2010 a 2026, nenhuma estava nesse caso.
    sem_cadastro = banco.fora_do_cadastro(conexao, leituras["estacao"].unique())
    if sem_cadastro:
        print(f"    ⚠️ {len(sem_cadastro)} estações com leitura e sem como cadastrar (sem nome, UF ou "
              f"coordenadas) ficaram de fora: {', '.join(sem_cadastro[:10])}")
        leituras = leituras[~leituras["estacao"].isin(sem_cadastro)]
    coleta.leituras = len(leituras)
    coleta.gravadas = banco.gravar_inmet(conexao, leituras)
    return coleta


def coletar_ultimas(conexao: psycopg.Connection, agora: datetime | None = None,
                    quantas: int = HORAS_REBUSCADAS) -> Coleta:
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
    comeco = time.monotonic()
    fim = _hora_cheia(agora)
    inicio = fim - timedelta(hours=quantas)
    estacoes = banco.estacoes_com_leituras(conexao, inicio, fim)
    print(f"🔋 Buscando a sensação térmica, a bateria e o processador de {len(estacoes)} estações, "
          f"de {_rotulo(inicio)} a {_rotulo(fim)} UTC...")
    coleta = Coleta(consultas=len(estacoes))
    seguidas = 0
    for primeira in range(0, len(estacoes), ESTACOES_POR_LOTE):
        partes = []
        for codigo in estacoes[primeira:primeira + ESTACOES_POR_LOTE]:
            _RITMO.esperar()
            try:
                dados = inmet.baixar_dados_estacao(codigo, inicio, fim)
                seguidas = 0
            except inmet.ErroINMET as erro:
                print(f"    ❌ {codigo}: {erro}")
                coleta.falhas.append(codigo)
                seguidas += 1
                if _fora_do_ar(coleta, seguidas):
                    break
                continue
            if dados is not None:
                partes.append(leituras_para_o_banco(dados, EXTRAS))
        partes = [parte for parte in partes if not parte.empty]
        if partes:
            extras = pd.concat(partes, ignore_index=True)
            coleta.leituras += len(extras)
            coleta.gravadas += banco.gravar_extras_inmet(conexao, extras)
        if coleta.interrompida:
            break
    relatar(coleta, time.monotonic() - comeco, "estações")
    return coleta


def carregar_historico(conexao: psycopg.Connection, desde: datetime = INICIO_DO_HISTORICO,
                       ate: datetime | None = None) -> Coleta:
    comeco = time.monotonic()
    desde, ate = _hora_cheia(desde), min(_hora_cheia(ate), _hora_cheia(None))   # nada do futuro
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
            if coleta.interrompida:
                print(f"❌ O INMET parou de responder em {dia:%d/%m/%Y}: a carga para aqui.")
                break
        dia += timedelta(days=1)
    relatar(total, time.monotonic() - comeco, "horas")
    return total


def relatar(coleta: Coleta, segundos: float, o_que: str) -> None:
    print(f"✅ {coleta.consultas} {o_que} consultadas, {coleta.leituras} leituras: "
          f"{coleta.gravadas} novas ou mudadas no banco ({segundos:.0f} s)")
    if coleta.estacoes_novas:
        print(f"   🆕 {coleta.estacoes_novas} estações novas no cadastro, vindas da consulta por hora")
    if coleta.falhas:
        mostradas = ", ".join(coleta.falhas[:10]) + (", ..." if len(coleta.falhas) > 10 else "")
        print(f"   ⚠️ {len(coleta.falhas)} {o_que} falharam ({mostradas}); a próxima execução tenta de novo")
