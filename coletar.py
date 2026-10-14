"""Os coletores do banco da UNIGEO: buscam nas APIs e gravam no PostgreSQL.

Feitos para o Agendador de Tarefas do Windows, pelo servidor\\coletar.bat, e rodam também à mão:

    python coletar.py esquema                        # cria as tabelas que faltam (na instalação, e quando o esquema mudar)
    python coletar.py inmet                          # de hora em hora: o cadastro e os últimos 3 dias
    python coletar.py inmet --extras                 # uma vez por dia: a sensação térmica, a bateria e o processador
    python coletar.py inmet --revisar 30             # uma vez por semana: os últimos 30 dias, com as correções do INMET
    python coletar.py inmet --historico 2018-01-01   # uma vez só: as horas desde a data, retomando de onde parou
    python coletar.py inmet --historico 2018-01-01 --ate 2018-12-31   # só um trecho (o último dia entra inteiro)
    python coletar.py previsao                       # de hora em hora: as rodadas novas dos modelos e do EC46

A conexão é a BANCO_GRAVACAO do .env, no schema BANCO_SCHEMA (padrão: climageo). Com --teste
(`python coletar.py inmet --teste`), é a BANCO_TESTE, no schema climageo_teste, que os testes
apagam e recriam: para experimentar sem tocar no banco de verdade.

Sai com código 1 quando o banco ou alguma consulta falham, para o Agendador marcar a execução.
A carga do histórico pode ser interrompida com Ctrl+C e retomada com o mesmo comando; se a conexão
com o banco cair no meio dela, ela reconecta e retoma sozinha.
O plano está em docs/plano_arquitetura.md (passos 4 e 5).
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg

from modulos import banco, coletor_inmet, coletor_previsao, config, inmet

SCHEMA_DE_TESTE = "climageo_teste"
# A carga do histórico leva ~22 horas sem ninguém olhando. Se a conexão cair (o servidor derruba a
# parada por 10 minutos, e o INMET pode demorar isso numa fase ruim; a máquina pode dormir), ela
# reconecta e retoma pelas horas que faltam, até tantas vezes
RECONEXOES_NO_HISTORICO = 5
ESPERA_PARA_RECONECTAR = 60    # segundos
# Com o INMET fora do ar, a carga do histórico espera e retoma sozinha, por até 2 horas
ESPERAS_COM_O_INMET_FORA = 8
ESPERA_COM_O_INMET_FORA = 15 * 60   # segundos


def _saida_em_utf8() -> None:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")


def ler_data(texto: str) -> datetime:
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"data inválida: {texto!r} (use AAAA-MM-DD ou DD/MM/AAAA)")


def dias_positivos(texto: str) -> int:
    if not texto.isdigit() or int(texto) < 1:
        raise argparse.ArgumentTypeError(f"número de dias inválido: {texto!r}")
    return int(texto)


def argumentos(linha: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coletores do banco da UNIGEO.")
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--teste", action="store_true",
                       help=f"grava no {SCHEMA_DE_TESTE}, com a conexão BANCO_TESTE, e não no banco de verdade")
    trabalhos = parser.add_subparsers(dest="trabalho", required=True)
    trabalhos.add_parser("esquema", parents=[comum], help="cria as tabelas que faltam (banco/esquema.sql)")
    do_inmet = trabalhos.add_parser("inmet", parents=[comum],
                                    help="as leituras das estações do INMET (de hora em hora)")
    modo = do_inmet.add_mutually_exclusive_group()
    modo.add_argument("--extras", action="store_true",
                      help="a sensação térmica, a bateria e o processador, por estação (uma vez por dia)")
    modo.add_argument("--revisar", type=dias_positivos, metavar="DIAS",
                      help="rebusca os últimos DIAS dias, para o banco receber o que o INMET corrigiu "
                           "(uma vez por semana, 30 dias)")
    modo.add_argument("--historico", type=ler_data, metavar="DATA",
                      help="as horas desde DATA (UTC), pulando as que já estão no banco")
    do_inmet.add_argument("--ate", type=ler_data, metavar="DATA",
                          help="com --historico: até o fim do dia DATA (UTC), inclusive (padrão: agora)")
    trabalhos.add_parser("previsao", parents=[comum],
                         help="as rodadas das 00Z e 12Z dos modelos e o EC46, do Open-Meteo (de hora em hora)")
    opcoes = parser.parse_args(linha)
    if getattr(opcoes, "ate", None) and not opcoes.historico:
        parser.error("--ate só vale com --historico")
    return opcoes


def _primeira_linha(erro: Exception) -> str:
    texto = str(erro).strip()
    return texto.splitlines()[0] if texto else type(erro).__name__


def carregar_historico(papel: str, schema: str, desde: datetime, ate: datetime | None) -> coletor_inmet.Coleta:
    total = coletor_inmet.Coleta()
    quedas = esperas = 0
    while True:
        try:
            with banco.conectar(papel, schema=schema) as conexao:
                coleta = coletor_inmet.carregar_historico(conexao, desde, ate)
        except (banco.ErroBanco, psycopg.OperationalError) as erro:
            quedas += 1
            if quedas > RECONEXOES_NO_HISTORICO:
                raise
            print(f"⚠️ A conexão com o banco caiu ({_primeira_linha(erro)}). Reconectando em "
                  f"{ESPERA_PARA_RECONECTAR} s para retomar de onde parou ({quedas}/{RECONEXOES_NO_HISTORICO})...")
            time.sleep(ESPERA_PARA_RECONECTAR)
            continue
        total.somar(coleta)
        # As horas que falharam antes voltam a ser pedidas na retomada: valem as falhas da última volta
        total.falhas, total.interrompida = coleta.falhas, coleta.interrompida
        if not coleta.interrompida:
            return total
        esperas += 1
        if esperas > ESPERAS_COM_O_INMET_FORA:
            print("❌ O INMET seguiu fora do ar. Rode de novo mais tarde; a carga retoma de onde parou.")
            return total
        print(f"⏸️ Tento de novo em {ESPERA_COM_O_INMET_FORA // 60} min ({esperas}/{ESPERAS_COM_O_INMET_FORA})...",
              flush=True)
        time.sleep(ESPERA_COM_O_INMET_FORA)


def executar(opcoes: argparse.Namespace) -> int:
    if opcoes.trabalho == "inmet" and config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        print("❌ O token do INMET não está configurado no .env (modelo: .env.example).")
        return 1
    papel, schema = ("teste", SCHEMA_DE_TESTE) if opcoes.teste else ("gravacao", config.BANCO_SCHEMA)
    try:
        if opcoes.trabalho == "inmet" and opcoes.historico:
            # --ate é o último dia, inteiro: até a 00:00 do dia seguinte, que fecha a última hora dele
            ate = opcoes.ate + timedelta(days=1) if opcoes.ate else None
            return 1 if carregar_historico(papel, schema, opcoes.historico, ate).falhas else 0
        if opcoes.trabalho == "previsao":
            coleta = coletor_previsao.coletar(lambda: banco.conectar(papel, schema=schema))
            return 1 if coleta.falhas else 0
        with banco.conectar(papel, schema=schema) as conexao:
            if opcoes.trabalho == "esquema":
                banco.criar_esquema(conexao)
                print(f"✅ Esquema conferido no schema {schema}: as tabelas que faltavam foram criadas.")
                return 0
            if opcoes.extras:
                coleta = coletor_inmet.coletar_extras(conexao)
            elif opcoes.revisar:
                coleta = coletor_inmet.revisar(conexao, opcoes.revisar)
            else:
                coleta = coletor_inmet.coletar_ultimas(conexao)
    except (banco.ErroBanco, psycopg.Error) as erro:
        print(f"❌ Banco: {erro}")
        return 1
    except inmet.ErroINMET as erro:
        print(f"❌ INMET: {erro}")
        return 1
    return 1 if coleta.falhas else 0


def main(linha: list[str] | None = None) -> int:
    _saida_em_utf8()
    try:
        return executar(argumentos(linha))
    except KeyboardInterrupt:
        print("\n⏹️ Interrompido. O que já foi gravado fica no banco; a hora ou o dia que estava no "
              "meio é desfeito. Rodando o mesmo comando, a carga do histórico retoma de onde parou.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
