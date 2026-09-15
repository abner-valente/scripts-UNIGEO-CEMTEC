"""Produtos meteorológicos a partir das estações automáticas do INMET em Mato Grosso do Sul.

COMO USAR
    1. Configure o token do INMET no arquivo .env (modelo: .env.example).
    2. Escolha o produto em PRODUTO e ajuste DATA_INICIAL e DATA_FINAL logo abaixo (dias no horário de MS):
         - datas iguais      -> consulta de data específica
         - datas diferentes  -> consulta de período
         - ambas None        -> monitoramento em tempo real (últimas 24 h)
    3. Execute:  python main.py

    Produto e datas também podem ser informados na linha de comando, sem editar o arquivo:
        python main.py --inicio 30/07/2026                   # data específica
        python main.py --inicio 01/08/2026 --fim 31/08/2026  # período
        python main.py --tempo-real                          # monitoramento
        python main.py --produto relatorio_inmet --tempo-real

Os resultados ficam em saida/<produto>/<modo>/<datas>/.
"""
import argparse
import sys
from datetime import date, datetime
from types import ModuleType

from modulos import config
from modulos.config import Periodo
from modulos.produtos import relatorio_inmet

# Produtos disponíveis: um arquivo em modulos/produtos/ para cada um
PRODUTOS = {produto.NOME: produto for produto in (relatorio_inmet,)}

# =====================================================
# CONSULTA (ALTERE AQUI)
# =====================================================
PRODUTO = "relatorio_inmet"
DATA_INICIAL = date(2026, 8, 1)
DATA_FINAL = date(2026, 8, 31)


def ler_data(texto: str) -> date:
    """Converte 'DD/MM/AAAA' ou 'AAAA-MM-DD' em data."""
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"data inválida: {texto!r} (use DD/MM/AAAA ou AAAA-MM-DD)")


def ler_consulta(argumentos: list[str] | None = None) -> tuple[ModuleType, Periodo]:
    """Produto e período da linha de comando ou, no que ela não informar, das variáveis acima."""
    parser = argparse.ArgumentParser(description="Produtos meteorológicos INMET — Mato Grosso do Sul.")
    parser.add_argument("--produto", choices=sorted(PRODUTOS), help=f"produto a gerar (padrão: {PRODUTO})")
    parser.add_argument("--inicio", type=ler_data, help="data inicial (DD/MM/AAAA)")
    parser.add_argument("--fim", type=ler_data, help="data final (DD/MM/AAAA); se omitida, igual à inicial")
    parser.add_argument("--tempo-real", action="store_true", help="monitoramento das últimas 24 h")
    args = parser.parse_args(argumentos)

    nome_produto = args.produto or PRODUTO
    if nome_produto not in PRODUTOS:
        parser.error(f"produto desconhecido: {nome_produto!r} (disponíveis: {', '.join(sorted(PRODUTOS))})")
    produto = PRODUTOS[nome_produto]

    if args.tempo_real:
        return produto, Periodo.tempo_real()
    inicio, fim = (args.inicio, args.fim) if (args.inicio or args.fim) else (DATA_INICIAL, DATA_FINAL)
    if inicio is None and fim is None:
        return produto, Periodo.tempo_real()
    try:
        return produto, Periodo.de_datas(inicio or fim, fim or inicio)
    except ValueError as erro:
        parser.error(str(erro))


def executar(produto: ModuleType, periodo: Periodo) -> int:
    """Confere o token e gera o produto. Retorna o código de saída do programa."""
    if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        print("❌ Token do INMET não configurado.")
        print("   Copie o arquivo .env.example para .env e preencha TOKEN_INMET.")
        return 1
    return produto.executar(periodo)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # emojis também em logs redirecionados no Windows
    sys.exit(executar(*ler_consulta()))
