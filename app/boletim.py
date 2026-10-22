"""A moldura do relatório para os mapas que o painel deixa baixar.

Na tela o mapa sai sem título, logos e ranking: numa coluna estreita eles tomariam o lugar do
desenho. Para o boletim a equipe quer o mapa como o `main.py` entrega — título, subtítulo com o
crédito INMET/SEMADESC, os logos do CEMTEC e da SEMADESC, o ranking das cinco estações e a barra
de cores —, mas com **as cores da tela** (decisão de 01/10/2026): a mesma paleta e a mesma
escala, fixa ou ajustada ao dado, que a pessoa estava vendo quando clicou. O que se baixa é o
que se vê, pronto para ir ao boletim.

O desenho não é refeito aqui: é o de `modulos/mapas.py` chamado sem `Tela`, que é o caminho do
relatório — o mesmo código, com os mesmos logos no mesmo lugar. Este módulo só escreve o texto
da moldura e grava o PNG como o `main.py` grava, sem Streamlit, para poder ser testado.
"""
import io
from datetime import datetime

from app import chuva, variaveis
from modulos import config, mapas

# Como o ranking chama os valores de cada grandeza — "5 MAIORES TEMPERATURAS", como no main.py.
# A radiação vai com a chuva porque as duas são acumuladas.
PLURAIS = {"Temperatura": "TEMPERATURAS", "Umidade": "UMIDADES", "Pressão": "PRESSÕES",
           "Vento": "VELOCIDADES", "Radiação": "ACUMULADOS", "Chuva": "ACUMULADOS"}


def titulo(nome: str, uf: str) -> str:
    return f"{nome} em {uf}"


def ranking(nome: str, grandeza: str) -> tuple[str, bool]:
    menores = "mínima" in nome.lower()
    plural = "RAJADAS" if "rajada" in nome.lower() else PLURAIS.get(grandeza, "VALORES")
    return f"5 {'MENORES' if menores else 'MAIORES'} {plural}", not menores


def instante(momento: datetime) -> str:
    return f"{momento:%d/%m/%Y %H:%M} {config._gmt(momento)}"


def subtitulo(modo: str, momento, periodo: config.Periodo) -> str:
    if modo == variaveis.PERIODO:
        return periodo.descrever_janela(*periodo.janela)
    if modo == variaveis.DIA:
        return f"{momento:%d/%m/%Y}"
    return instante(momento)


def nome_da_chuva(rotulo: str) -> str:
    return "Chuva acumulada no mês" if rotulo == chuva.MENSAL else f"Chuva acumulada em {rotulo}"


def espec(nome: str, grandeza: str, unidade: str, paleta: str, decimais: int, uf: str,
          subtitulo: str, direcao_vento: bool = False, credito: str = mapas.CREDITO,
          coluna: str | None = None, credito_no_rodape: bool = False) -> mapas.EspecMapa:
    texto, maiores = ranking(nome, grandeza)
    return mapas.EspecMapa(tabela="", coluna=coluna or nome, titulo=titulo(nome, uf), subtitulo=subtitulo,
                           arquivo="", cmap=paleta, unidade=f"{grandeza} ({unidade})", ranking=texto,
                           maiores=maiores, direcao_vento=direcao_vento, decimais=decimais,
                           credito=credito, credito_no_rodape=credito_no_rodape)


def nome_do_arquivo(nome: str, uf: str, carimbo: str) -> str:
    # Sem barra, que não pode haver em nome de arquivo ("Rajada s/ direção" vira "Rajada_s_direção")
    return f"Mapa_{nome.replace('/', '').replace(' ', '_')}_{uf}_{carimbo}.png"


def png(figura) -> bytes:
    arquivo = io.BytesIO()
    figura.savefig(arquivo, format="png", dpi=config.DPI, bbox_inches="tight", facecolor="white",
                   edgecolor="none")
    return arquivo.getvalue()
