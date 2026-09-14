"""Relatório meteorológico das estações automáticas do INMET em Mato Grosso do Sul.

COMO USAR
    1. Configure o token do INMET no arquivo .env (modelo: .env.example).
    2. Ajuste DATA_INICIAL e DATA_FINAL logo abaixo (dias em UTC):
         - datas iguais      -> consulta de data específica
         - datas diferentes  -> consulta de período
         - ambas None        -> monitoramento em tempo real (últimas 24 h)
    3. Execute:  python main.py

    As datas também podem ser informadas na linha de comando, sem editar o arquivo:
        python main.py --inicio 30/07/2026                   # data específica
        python main.py --inicio 01/08/2026 --fim 31/08/2026  # período
        python main.py --tempo-real                          # monitoramento

Os resultados ficam em saida/<modo>/<datas>/ (planilha Excel + pasta mapas/).
"""
import argparse
import sys
import time
from datetime import date, datetime

from modulos import calculos, config, excel, inmet, mapas
from modulos.config import Periodo

# =====================================================
# DATAS DA CONSULTA (ALTERE AQUI)
# =====================================================
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


def definir_periodo(argumentos: list[str] | None = None) -> Periodo:
    """Período da linha de comando ou, se ela não tiver datas, das variáveis DATA_INICIAL/DATA_FINAL."""
    parser = argparse.ArgumentParser(description="Relatório meteorológico INMET — Mato Grosso do Sul.")
    parser.add_argument("--inicio", type=ler_data, help="data inicial (DD/MM/AAAA)")
    parser.add_argument("--fim", type=ler_data, help="data final (DD/MM/AAAA); se omitida, igual à inicial")
    parser.add_argument("--tempo-real", action="store_true", help="monitoramento das últimas 24 h")
    args = parser.parse_args(argumentos)

    if args.tempo_real:
        return Periodo.tempo_real()

    inicio, fim = (args.inicio, args.fim) if (args.inicio or args.fim) else (DATA_INICIAL, DATA_FINAL)
    if inicio is None and fim is None:
        return Periodo.tempo_real()
    try:
        return Periodo.de_datas(inicio or fim, fim or inicio)
    except ValueError as erro:
        parser.error(str(erro))


def executar(periodo: Periodo) -> int:
    """Coleta, cálculos, Excel e mapas. Retorna o código de saída do programa."""
    if config.TOKEN_INMET in ("", config.TOKEN_EXEMPLO):
        print("❌ Token do INMET não configurado.")
        print("   Copie o arquivo .env.example para .env e preencha TOKEN_INMET.")
        return 1

    print("=" * 60)
    print(f"📊 RELATÓRIO INMET — {config.NOME_UF.upper()}")
    print(f"📅 Modo: {periodo.nome_modo}")
    print(f"📅 {periodo.descricao}")
    print("=" * 60)

    try:
        estacoes = inmet.listar_estacoes()
    except inmet.ErroINMET as erro:
        print(f"❌ Erro ao listar estações: {erro}")
        return 1
    print(f"✅ Encontradas {len(estacoes)} estações em {config.UF}")

    busca_inicio, busca_fim = periodo.janela_busca
    resumos = []
    for _, estacao in estacoes.iterrows():
        print(f"🛰️ Lendo: {estacao['Estação']}...")
        dados = inmet.baixar_dados_estacao(estacao["CD_ESTACAO"], busca_inicio, busca_fim)
        time.sleep(config.PAUSA_ENTRE_REQUISICOES)
        if dados is None:
            continue
        print(f"    📊 {len(dados)} registros")
        resumos.append(calculos.resumir_estacao(dados, estacao, periodo))

    tabelas = calculos.montar_tabelas(resumos, periodo)
    print(f"\n📊 Estações com dados: {len(resumos)} de {len(estacoes)}")
    for aba, tabela in tabelas.items():
        print(f"   - {aba}: {len(tabela)} estações")

    pasta = periodo.pasta_saida
    excel.salvar_relatorio(tabelas, pasta / f"Relatorio_{config.UF}_{periodo.identificador}.xlsx")

    print("\n🗺️ Gerando mapas...")
    mapas.gerar_mapas(tabelas, periodo, pasta / "mapas")

    print("=" * 60)
    print(f"✅ Concluído. Arquivos em: {pasta}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # emojis também em logs redirecionados no Windows
    sys.exit(executar(definir_periodo()))
