"""Os coletores do banco da UNIGEO: buscam nas APIs e gravam no PostgreSQL.

Feitos para o Agendador de Tarefas do Windows, pelo servidor\\coletar.bat, e rodam também à mão:

    python coletar.py esquema                        # cria as tabelas que faltam (na instalação, e quando o esquema mudar)
    python coletar.py inmet                          # de hora em hora: o cadastro e as últimas 48 h
    python coletar.py inmet --extras                 # uma vez por dia: a sensação térmica, a bateria e o processador
    python coletar.py inmet --historico 2018-01-01   # uma vez só: as horas desde a data, retomando de onde parou

A conexão é a BANCO_GRAVACAO do .env, no schema BANCO_SCHEMA (padrão: climageo). Com --teste
(`python coletar.py inmet --teste`), é a BANCO_TESTE, no schema climageo_teste, que os testes
apagam e recriam: para experimentar sem tocar no banco de verdade.

Sai com código 1 quando o banco ou alguma consulta falham, para o Agendador marcar a execução.
O plano está em docs/plano_arquitetura.md (passos 4 e 5).
"""
import argparse
import sys
from datetime import datetime, timezone

import psycopg

from modulos import banco, coletor_inmet, config, inmet

SCHEMA_DE_TESTE = "climageo_teste"


def _saida_em_utf8() -> None:
    """Pelo Agendador, a saída vai para um arquivo em cp1252, que não escreve os emojis (ver main.py)."""
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")


def ler_data(texto: str) -> datetime:
    """'AAAA-MM-DD' ou 'DD/MM/AAAA' como a 00:00 UTC do dia."""
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"data inválida: {texto!r} (use AAAA-MM-DD ou DD/MM/AAAA)")


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
    modo.add_argument("--historico", type=ler_data, metavar="DATA",
                      help="as horas desde DATA (UTC), pulando as que já estão no banco")
    do_inmet.add_argument("--ate", type=ler_data, metavar="DATA",
                          help="com --historico: até a 00:00 UTC de DATA (padrão: agora)")
    opcoes = parser.parse_args(linha)
    if getattr(opcoes, "ate", None) and not opcoes.historico:
        parser.error("--ate só vale com --historico")
    return opcoes


def executar(opcoes: argparse.Namespace) -> int:
    """Roda o trabalho pedido e devolve o código de saída: 0 deu tudo certo, 1 algo falhou."""
    if opcoes.trabalho == "inmet" and config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        print("❌ O token do INMET não está configurado no .env (modelo: .env.example).")
        return 1
    papel, schema = ("teste", SCHEMA_DE_TESTE) if opcoes.teste else ("gravacao", config.BANCO_SCHEMA)
    try:
        with banco.conectar(papel, schema=schema) as conexao:
            if opcoes.trabalho == "esquema":
                banco.criar_esquema(conexao)
                print(f"✅ Esquema conferido no schema {schema}: as tabelas que faltavam foram criadas.")
                return 0
            if opcoes.extras:
                coleta = coletor_inmet.coletar_extras(conexao)
            elif opcoes.historico:
                coleta = coletor_inmet.carregar_historico(conexao, opcoes.historico, opcoes.ate)
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
    return executar(argumentos(linha))


if __name__ == "__main__":
    sys.exit(main())
