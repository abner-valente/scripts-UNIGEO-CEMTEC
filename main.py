"""Produtos meteorológicos a partir das estações automáticas do INMET em Mato Grosso do Sul.

COMO USAR
    1. Configure o token do INMET no arquivo .env (modelo: .env.example).
    2. Escolha o produto em PRODUTO e ajuste DATA_INICIAL e DATA_FINAL logo abaixo (dias no horário de MS):
         - datas iguais      -> consulta de data específica
         - datas diferentes  -> consulta de período
         - ambas None        -> monitoramento em tempo real (últimas 24 h)
       Para começar ou terminar fora da 00 h, use HORA_INICIAL e HORA_FINAL (horas cheias de MS).
    3. Execute:  python main.py

    Produto e datas também podem ser informados na linha de comando, sem editar o arquivo:
        python main.py --dataini 30/07/2026                        # data específica
        python main.py --dataini 01/08/2026 --datafim 31/08/2026   # período
        python main.py --dataini 14/09/2026 --hrini 8 --datafim 15/09/2026 --hrfim 8  # das 08 h às 08 h
        python main.py --tempo-real                                # monitoramento
        python main.py --produto relatorio_inmet --tempo-real

Os resultados ficam em saida/<produto>/<modo>/<datas>/.
"""
import argparse
import sys
from datetime import date, datetime
from types import ModuleType

from modulos import config
from modulos.config import Periodo
from modulos.produtos import relatorio_inmet, risco_fogo

# Produtos disponíveis: um arquivo em modulos/produtos/ para cada um
PRODUTOS = {produto.NOME: produto for produto in (relatorio_inmet, risco_fogo)}

# =====================================================
# CONSULTA (ALTERE AQUI)
# =====================================================
PRODUTO = "relatorio_inmet"
DATA_INICIAL = date(2026, 8, 1)
DATA_FINAL = date(2026, 8, 31)
HORA_INICIAL = None  # hora de início (0 a 24, horário de MS); None = 00 h da data inicial
HORA_FINAL = None    # hora de fim (0 a 24, horário de MS); None = 24 h da data final


def ler_data(texto: str) -> date:
    """Converte 'DD/MM/AAAA' ou 'AAAA-MM-DD' em data."""
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"data inválida: {texto!r} (use DD/MM/AAAA ou AAAA-MM-DD)")


def ler_hora(texto: str) -> int:
    """Converte '8', '08', '8h', '08h' ou '08:00' em hora cheia (0 a 24)."""
    limpo = texto.strip().lower().removesuffix("h").removesuffix(":00")
    if limpo.isdigit() and 0 <= int(limpo) <= 24:
        return int(limpo)
    raise argparse.ArgumentTypeError(f"hora inválida: {texto!r} (use uma hora cheia de 0 a 24, ex.: 8, 08h ou 08:00)")


def ler_consulta(argumentos: list[str] | None = None) -> tuple[ModuleType, Periodo, dict]:
    """Produto, período e opções da linha de comando ou, no que ela não informar, das variáveis acima.

    As opções são os argumentos que valem só para alguns produtos (ex.: --hrtodas, do risco_fogo);
    cada produto usa as que conhece e ignora o resto.
    """
    parser = argparse.ArgumentParser(description="Produtos meteorológicos INMET — Mato Grosso do Sul.")
    parser.add_argument("--produto", choices=sorted(PRODUTOS), help=f"produto a gerar (padrão: {PRODUTO})")
    parser.add_argument("--dataini", type=ler_data, help="data inicial (DD/MM/AAAA)")
    parser.add_argument("--datafim", type=ler_data, help="data final (DD/MM/AAAA); se omitida, igual à inicial")
    parser.add_argument("--hrini", type=ler_hora, help="hora de início, horário de MS (0 a 24; padrão: 0)")
    parser.add_argument("--hrfim", type=ler_hora, help="hora de fim, horário de MS (0 a 24; padrão: 24)")
    parser.add_argument("--tempo-real", action="store_true", help="monitoramento das últimas 24 h")
    parser.add_argument("--hrtodas", action="store_true",
                        help="risco_fogo: gera o mapa de todas as horas, e não só as de risco alto")
    args = parser.parse_args(argumentos)

    nome_produto = args.produto or PRODUTO
    if nome_produto not in PRODUTOS:
        parser.error(f"produto desconhecido: {nome_produto!r} (disponíveis: {', '.join(sorted(PRODUTOS))})")
    produto = PRODUTOS[nome_produto]

    opcoes = {"hrtodas": args.hrtodas}

    inicio, fim = (args.dataini, args.datafim) if (args.dataini or args.datafim) else (DATA_INICIAL, DATA_FINAL)
    if args.tempo_real or (inicio is None and fim is None):
        if args.hrini is not None or args.hrfim is not None:
            parser.error("--hrini e --hrfim só valem com datas (--dataini e --datafim), não no tempo real")
        return produto, Periodo.tempo_real(), opcoes

    hora_inicial = args.hrini if args.hrini is not None else HORA_INICIAL
    hora_final = args.hrfim if args.hrfim is not None else HORA_FINAL
    try:
        return produto, Periodo.de_datas(inicio or fim, fim or inicio,
                                         0 if hora_inicial is None else hora_inicial,
                                         24 if hora_final is None else hora_final), opcoes
    except ValueError as erro:
        parser.error(str(erro))


def executar(produto: ModuleType, periodo: Periodo, opcoes: dict | None = None) -> int:
    """Confere o token e gera o produto. Retorna o código de saída do programa."""
    if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        print("❌ Token do INMET não configurado.")
        print("   Copie o arquivo .env.example para .env e preencha TOKEN_INMET.")
        return 1
    return produto.executar(periodo, opcoes or {})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # emojis e acentos também em logs redirecionados no Windows
    sys.stderr.reconfigure(encoding="utf-8")  # idem para as mensagens de erro da linha de comando
    sys.exit(executar(*ler_consulta()))
