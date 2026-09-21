"""Gráficos dos produtos: barras empilhadas, barras agrupadas e calendário.

O módulo traz as formas e o estilo; o que cada barra significa fica no arquivo do produto,
como acontece com o mapas.py. Todos os gráficos saem em PNG, sem abrir janelas.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # gera os arquivos sem abrir janelas (permite rodar agendado)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

from . import config

TINTA = "#1a1a1a"        # títulos e números
TINTA_FRACA = "#5c5c5c"  # eixos, legendas e rótulos secundários
GRADE = "#e3e3e0"


def nova_figura(titulo: str, subtitulo: str, largura: float, altura: float):
    """Figura com o título, o subtítulo e os eixos discretos usados em todos os gráficos."""
    fig, ax = plt.subplots(figsize=(largura, altura))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_title(f"{titulo}\n", fontsize=15, weight="bold", color=TINTA, loc="left")
    ax.text(0, 1.008, subtitulo, transform=ax.transAxes, fontsize=10.5, color=TINTA_FRACA, va="bottom")
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.spines["bottom"].set_color(GRADE)
    ax.tick_params(colors=TINTA_FRACA, length=0)
    ax.set_axisbelow(True)
    return fig, ax


def salvar(fig, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(caminho, dpi=config.DPI_GRAFICOS, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"📈 Gráfico salvo: {caminho.name}")


def barras_empilhadas(tabela: pd.DataFrame, cores: list[str], rotulos: list[str], titulo: str, subtitulo: str,
                      eixo: str, caminho: Path, cores_do_texto: list[str] | None = None) -> None:
    """Uma barra horizontal por linha da tabela, dividida nas colunas dela.

    Cada trecho leva o seu valor dentro, e o total fica no fim da barra. Trechos pequenos demais
    para caber o número ficam sem ele, para o gráfico não virar um amontoado.
    """
    fig, ax = nova_figura(titulo, subtitulo, 11, 0.23 * len(tabela) + 2)
    cores_do_texto = cores_do_texto or [TINTA] * len(tabela.columns)
    limite = max(tabela.sum(axis=1).max(), 1) * 0.035  # trecho mínimo para caber um número

    esquerda = np.zeros(len(tabela))
    for coluna, cor, rotulo, cor_texto in zip(tabela.columns, cores, rotulos, cores_do_texto):
        valores = tabela[coluna].to_numpy()
        ax.barh(tabela.index, valores, left=esquerda, height=0.72, color=cor, label=rotulo,
                edgecolor="white", linewidth=1.2)
        for categoria, valor, inicio in zip(tabela.index, valores, esquerda):
            if valor >= limite:
                ax.text(inicio + valor / 2, categoria, f"{valor:.0f}", va="center", ha="center",
                        fontsize=8, color=cor_texto)
        esquerda += valores
    for categoria, total in zip(tabela.index, esquerda):
        ax.text(total + limite * 0.4, categoria, f"{total:.0f}", va="center", fontsize=8.5, color=TINTA_FRACA)

    ax.set_xlabel(eixo, color=TINTA_FRACA, fontsize=10)
    ax.xaxis.grid(True, color=GRADE, linewidth=0.8)
    ax.tick_params(axis="y", labelsize=8.5)
    ax.legend(loc="lower right", frameon=False, fontsize=10)
    salvar(fig, caminho)


def barras_agrupadas(tabela: pd.DataFrame, cores: list[str], rotulos: list[str], titulo: str, subtitulo: str,
                     eixo: str, caminho: Path) -> None:
    """Uma barra por coluna, lado a lado, para cada linha da tabela."""
    fig, ax = nova_figura(titulo, subtitulo, 11, 0.30 * len(tabela) + 2)
    posicoes = np.arange(len(tabela))
    espessura = 0.24
    for i, (coluna, cor, rotulo) in enumerate(zip(tabela.columns, cores, rotulos)):
        deslocamento = ((len(tabela.columns) - 1) / 2 - i) * espessura
        ax.barh(posicoes + deslocamento, tabela[coluna], height=espessura - 0.02, color=cor, label=rotulo)

    ax.set_yticks(posicoes, tabela.index, fontsize=8.5)
    ax.set_xlabel(eixo, color=TINTA_FRACA, fontsize=10)
    ax.xaxis.grid(True, color=GRADE, linewidth=0.8)
    ax.legend(loc="lower right", frameon=False, fontsize=10)
    salvar(fig, caminho)


def calendario(tabela: pd.DataFrame, cores: list[str], rotulos: list[str], titulo: str, subtitulo: str,
               caminho: Path) -> None:
    """Um quadrado para cada cruzamento de linha (ex.: estação) e coluna (ex.: dia), colorido pela classe."""
    fig, ax = nova_figura(titulo, subtitulo, 11, 0.21 * len(tabela) + 2)
    ax.imshow(tabela.to_numpy(), aspect="auto", cmap=ListedColormap(cores), vmin=-0.5, vmax=len(cores) - 0.5)

    ax.set_xticks(range(len(tabela.columns)), tabela.columns, fontsize=9.5)
    ax.set_yticks(range(len(tabela)), tabela.index, fontsize=8.5)
    # Linhas brancas entre os quadrados: separam sem desenhar bordas
    ax.set_xticks(np.arange(len(tabela.columns)) - 0.5, minor=True)
    ax.set_yticks(np.arange(len(tabela)) - 0.5, minor=True)
    ax.grid(which="minor", color="white", linewidth=1.5)
    ax.spines["bottom"].set_visible(False)
    ax.legend(handles=[Patch(facecolor=cor, label=rotulo) for cor, rotulo in zip(cores, rotulos)],
              loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=10)
    salvar(fig, caminho)
