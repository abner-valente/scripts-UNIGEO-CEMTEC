"""Relatório INMET: extremos e chuva acumulada das estações automáticas de MS.

Primeiro produto migrado dos scripts da equipe (originais em legado/relatorio_inmet/). Para cada
estação calcula a temperatura mínima e máxima, a umidade relativa mínima, a rajada máxima (com a
direção) e a chuva acumulada, e gera uma planilha Excel com uma aba por variável e os mapas de cada uma.

As regras de cálculo seguem as decisões da equipe de meteorologia (docs/questoes_meteorologia.md):
dias no horário de MS, extremos do tempo real nas últimas 24 h e estações com 0 mm nos mapas de chuva.
"""
from dataclasses import replace
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .. import calculos, config, excel, inmet, mapas
from ..config import Periodo
from ..mapas import EspecMapa

NOME = "relatorio_inmet"
TITULO = "Relatório INMET — extremos e chuva das estações automáticas"


# =====================================================
# JANELAS DE TEMPO DESTE RELATÓRIO
# =====================================================
def janelas_chuva(periodo: Periodo) -> dict[str, tuple[datetime, datetime]]:
    """Colunas de chuva acumulada da planilha e o intervalo de cada uma."""
    if periodo.modo == "tempo_real":
        janelas = {"Chuva Hoje (desde 00h MS)": (periodo.inicio_do_dia, periodo.fim)}
        for horas in (12, 24, 48, 72):
            janelas[f"Acumulado {horas}h"] = (periodo.fim - timedelta(hours=horas), periodo.fim)
        return janelas
    rotulo = "Acumulado Dia" if periodo.modo == "dia" and periodo.dias_inteiros else "Acumulado Período"
    return {rotulo: periodo.janela}


def coluna_chuva_principal(periodo: Periodo) -> str:
    """Coluna de chuva usada para ordenar a aba Chuva."""
    return "Acumulado 24h" if periodo.modo == "tempo_real" else next(iter(janelas_chuva(periodo)))


# =====================================================
# CÁLCULOS POR ESTAÇÃO
# =====================================================
def resumir_estacao(dados: pd.DataFrame, estacao: pd.Series, periodo: Periodo) -> dict[str, dict]:
    """Extremos e acumulados de uma estação no período.

    Retorna {aba do Excel: linha da tabela}. Variáveis sem nenhum dado válido ficam de fora.
    """
    nome = estacao["Estação"]
    coordenadas = {"Latitude": estacao["VL_LATITUDE"], "Longitude": estacao["VL_LONGITUDE"]}
    dados_periodo = calculos.recortar(dados, *periodo.janela)
    linhas = {}

    indice = calculos.indice_extremo(dados_periodo, "TEM_MIN", minimo=True)
    if indice is not None:
        linhas["Temp_Min"] = {
            "Estação": nome,
            "Temperatura Mínima (°C)": dados_periodo.at[indice, "TEM_MIN"],
            **calculos.data_hora(dados_periodo, indice),
            **coordenadas,
        }

    indice = calculos.indice_extremo(dados_periodo, "TEM_MAX", minimo=False)
    if indice is not None:
        linhas["Temp_Max"] = {
            "Estação": nome,
            "Temperatura Máxima (°C)": dados_periodo.at[indice, "TEM_MAX"],
            **calculos.data_hora(dados_periodo, indice),
            **coordenadas,
        }

    indice = calculos.indice_extremo(dados_periodo, "UMD_MIN", minimo=True)
    if indice is not None:
        linhas["Umidade"] = {
            "Estação": nome,
            "Umidade Mín (%)": dados_periodo.at[indice, "UMD_MIN"],
            **coordenadas,
        }

    indice = calculos.indice_extremo(dados_periodo, "VEN_RAJ", minimo=False)
    if indice is not None:
        # Direção registrada no mesmo horário da rajada máxima
        direcao = dados_periodo.at[indice, "VEN_DIR"] if "VEN_DIR" in dados_periodo else np.nan
        linhas["Vento"] = {
            "Estação": nome,
            "Rajada (km/h)": round(dados_periodo.at[indice, "VEN_RAJ"] * 3.6, 1),  # m/s -> km/h
            "Direção (°)": direcao,
            **coordenadas,
        }

    linhas["Chuva"] = {
        "Estação": nome,
        **{coluna: calculos.acumulado_chuva(dados, inicio, fim) for coluna, (inicio, fim) in janelas_chuva(periodo).items()},
        **coordenadas,
    }
    return linhas


def montar_tabelas(resumos: list[dict[str, dict]], periodo: Periodo) -> dict[str, pd.DataFrame]:
    """Junta os resumos das estações em uma tabela ordenada para cada aba do Excel."""
    ordenacao = {  # aba: (coluna de ordenação, crescente?)
        "Temp_Min": ("Temperatura Mínima (°C)", True),
        "Temp_Max": ("Temperatura Máxima (°C)", False),
        "Umidade": ("Umidade Mín (%)", True),
        "Vento": ("Rajada (km/h)", False),
        "Chuva": (coluna_chuva_principal(periodo), False),
    }
    tabelas = {}
    for aba, (coluna, crescente) in ordenacao.items():
        linhas = [resumo[aba] for resumo in resumos if aba in resumo]
        if linhas:
            tabelas[aba] = pd.DataFrame(linhas).sort_values(coluna, ascending=crescente, ignore_index=True)
    return tabelas


# =====================================================
# MAPAS
# =====================================================
def especificacoes_mapas(periodo: Periodo) -> list[EspecMapa]:
    """Mapas gerados pelo relatório, conforme o modo da consulta."""
    uf, sigla = config.NOME_UF, config.UF
    subtitulo = periodo.descrever_janela(*periodo.janela)

    especificacoes = [
        EspecMapa("Temp_Min", "Temperatura Mínima (°C)", f"Temperatura mínima em {uf}", subtitulo,
                  f"Mapa_Temp_Min_{sigla}", "coolwarm", "Temperatura (°C)", "5 MENORES TEMPERATURAS", maiores=False),
        EspecMapa("Temp_Max", "Temperatura Máxima (°C)", f"Temperatura máxima em {uf}", subtitulo,
                  f"Mapa_Temp_Max_{sigla}", "YlOrRd", "Temperatura (°C)", "5 MAIORES TEMPERATURAS"),
        EspecMapa("Umidade", "Umidade Mín (%)", f"Umidade relativa mínima em {uf}", subtitulo,
                  f"Mapa_Umidade_{sigla}", "YlGnBu", "Umidade (%)", "5 MENORES UMIDADES", maiores=False),
    ]

    # (coluna, duração no título, sufixo do arquivo, paleta)
    if periodo.modo == "tempo_real":
        chuvas = [("Acumulado 24h", "24 horas", "24h", "Blues"), ("Acumulado 48h", "48 horas", "48h", "Purples"),
                  ("Acumulado 72h", "72 horas", "72h", "Blues")]
    elif periodo.modo == "dia" and periodo.dias_inteiros:
        chuvas = [(coluna_chuva_principal(periodo), "24 horas", "24h", "Blues")]
    else:
        duracao = f"{periodo.num_dias} dias" if periodo.dias_inteiros else f"{periodo.horas} horas"
        chuvas = [(coluna_chuva_principal(periodo), duracao, "Periodo", "Blues")]
    for coluna, duracao, sufixo, cmap in chuvas:
        especificacoes.append(EspecMapa(
            "Chuva", coluna, f"Chuva acumulada em {duracao} - {uf}",
            periodo.descrever_janela(*janelas_chuva(periodo)[coluna]), f"Mapa_Chuva_{sufixo}_{sigla}",
            cmap, "Chuva (mm)", "5 MAIORES ACUMULADOS",
        ))

    rajadas = EspecMapa("Vento", "Rajada (km/h)", f"Rajadas de vento em {uf}", subtitulo,
                        f"Mapa_Rajadas_{sigla}", "turbo", "Velocidade (km/h)", "5 MAIORES RAJADAS")
    especificacoes.append(rajadas)
    especificacoes.append(replace(rajadas, titulo=f"Rajadas de vento com direção em {uf}",
                                  arquivo=f"Mapa_Rajadas_Direcao_{sigla}", direcao_vento=True))
    return especificacoes


# =====================================================
# EXECUÇÃO
# =====================================================
def executar(periodo: Periodo) -> int:
    """Coleta os dados, calcula e grava a planilha e os mapas. Retorna 0 se deu certo e 1 se falhou."""
    print("=" * 60)
    print(f"📊 {TITULO} — {config.NOME_UF}")
    print(f"📅 Modo: {periodo.nome_modo}")
    print(f"📅 {periodo.descricao}")
    print("=" * 60)

    try:
        coletados = inmet.baixar_estacoes(*periodo.janela_busca)
    except inmet.ErroINMET as erro:
        print(f"❌ Erro ao listar estações: {erro}")
        return 1

    if not coletados:
        print("❌ Nenhuma estação retornou dados. Confira o token e a conexão e tente de novo.")
        return 1

    tabelas = montar_tabelas([resumir_estacao(dados, estacao, periodo) for estacao, dados in coletados], periodo)
    for aba, tabela in tabelas.items():
        print(f"   - {aba}: {len(tabela)} estações")

    pasta = periodo.pasta_saida(NOME)
    excel.salvar_relatorio(tabelas, pasta / f"Relatorio_{config.UF}_{periodo.identificador}.xlsx")

    print("\n🗺️ Gerando mapas...")
    mapas.gerar_mapas(tabelas, especificacoes_mapas(periodo), pasta / "mapas", periodo.identificador)

    print("=" * 60)
    print(f"✅ Concluído. Arquivos em: {pasta}")
    print("=" * 60)
    return 0
