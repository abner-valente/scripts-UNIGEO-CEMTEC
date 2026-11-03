"""As estações que a equipe tira dos mapas de propósito, uma lista por variável.

Às vezes uma estação manda um valor que não é o do tempo: o sensor sujo, o pluviômetro
entupido, o abrigo ao sol. A equipe vê o erro e pede a correção ao INMET, que leva até dois
dias. Enquanto isso, o boletim precisa de um mapa que sirva a quem lê: a estação sai do mapa
daquela variável (do ponto, do ranking e da interpolação) e continua nas tabelas, nas planilhas
e nos gráficos, que é onde se enxerga o erro.

A lista vale por variável porque quem falha é um sensor: um pluviômetro entupido não estraga a
temperatura da mesma estação. No risco de fogo, a estação sai das três medidas da regra.

Usado pelo painel (a lista ao lado dos mapas) e pelo main.py (a opção --retirar).
"""
import re
import unicodedata

import pandas as pd

# As variáveis que têm lista própria: as grandezas do painel (app/variaveis.py) e o risco
VARIAVEIS = ("Temperatura", "Umidade", "Pressão", "Vento", "Radiação", "Chuva", "Risco de Fogo")
# Outros nomes que valem na linha de comando, além dos de cima com ou sem acento
APELIDOS = {"risco": "Risco de Fogo", "rajada": "Vento"}


def sem_acento(texto: str) -> str:
    """Minúsculas e sem acento, para comparar nomes escritos à mão: "Ponta Pora" acha "Ponta Porã"."""
    decomposto = unicodedata.normalize("NFKD", str(texto))
    return " ".join("".join(letra for letra in decomposto if not unicodedata.combining(letra)).casefold().split())


def variavel(texto: str) -> str:
    """O nome da variável como está em VARIAVEIS, escrito em qualquer caixa e com ou sem acento."""
    chave = sem_acento(texto)
    nomes = {sem_acento(nome): nome for nome in VARIAVEIS} | APELIDOS
    if chave not in nomes:
        raise ValueError(f"variável desconhecida: {texto.strip()!r} (valem: {', '.join(VARIAVEIS)})")
    return nomes[chave]


def ler_pedidos(textos: list[str]) -> dict[str, list[str]]:
    """As retiradas pedidas na linha de comando, como "Chuva: Campo Grande; A702".

    Uma variável por texto, com as estações por nome ou pelo código do INMET, separadas por vírgula
    ou ponto e vírgula. A mesma variável em dois textos junta as duas listas.
    """
    pedidos: dict[str, list[str]] = {}
    for texto in textos:
        nome, separador, estacoes = texto.partition(":")
        if not separador:
            raise ValueError(f"escreva a variável e as estações, como \"Chuva: Campo Grande\" (veio {texto!r})")
        lista = [parte.strip() for parte in re.split(r"[;,]", estacoes) if parte.strip()]
        if not lista:
            raise ValueError(f"nenhuma estação depois de {nome.strip()!r}:")
        pedidos.setdefault(variavel(nome), []).extend(lista)
    return pedidos


def resolver(pedidas: list[str], estacoes: pd.DataFrame) -> tuple[set[str], list[str]]:
    """Os nomes das estações pedidas, como estão em `estacoes`, e as que não se acharam.

    `estacoes` tem a coluna "Estação" e, quando houver, a "CD_ESTACAO": a estação pedida vale pelo
    nome (sem acento, em qualquer caixa) ou pelo código do INMET.
    """
    por_nome = {sem_acento(nome): nome for nome in estacoes["Estação"]}
    por_codigo = ({sem_acento(codigo): nome for codigo, nome in zip(estacoes["CD_ESTACAO"], estacoes["Estação"])
                   if pd.notna(codigo)} if "CD_ESTACAO" in estacoes else {})
    achadas, faltaram = set(), []
    for pedida in pedidas:
        chave = sem_acento(pedida)
        nome = por_nome.get(chave) or por_codigo.get(chave)
        if nome is None:
            faltaram.append(pedida)
        else:
            achadas.add(nome)
    return achadas, faltaram


def resolver_pedidos(pedidos: dict[str, list[str]], estacoes: pd.DataFrame) -> tuple[dict[str, set[str]], list[str]]:
    """`resolver` para todas as variáveis: {variável: nomes} e os avisos do que não se achou."""
    retiradas, avisos = {}, []
    for nome_variavel, pedidas in pedidos.items():
        achadas, faltaram = resolver(pedidas, estacoes)
        if achadas:
            retiradas[nome_variavel] = achadas
        avisos += [f"não achei a estação {pedida!r} ({nome_variavel}): confira o nome, ou use o código do INMET"
                   for pedida in faltaram]
    return retiradas, avisos


def retirar(dados, nomes: set[str]):
    """Os dados sem as estações de `nomes`: uma tabela com a coluna "Estação", ou uma série por estação."""
    if not nomes or dados is None:
        return dados
    if isinstance(dados, pd.Series):
        return dados.drop(list(nomes), errors="ignore")
    return dados[~dados["Estação"].isin(nomes)].reset_index(drop=True)


def descrever(retiradas: dict[str, set[str]]) -> list[str]:
    """Uma linha por variável, para o terminal: "Chuva: Campo Grande, Dourados"."""
    return [f"{nome}: {', '.join(sorted(nomes))}" for nome, nomes in retiradas.items() if nomes]


def para_o_produto(pedidos: dict[str, list[str]], estacoes: pd.DataFrame, do_produto: set[str],
                   produto: str) -> dict[str, set[str]]:
    """As retiradas que valem para um produto do main.py, já com o aviso no terminal.

    `estacoes` são as que o produto baixou (do estado e as vizinhas) e `do_produto`, as variáveis
    que ele mapeia: um pedido de outra variável não muda nada ali, e o terminal diz isso.
    """
    if not pedidos:
        return {}
    avisos = [f"o {produto} não faz mapa de {nome}: essa retirada não muda nada aqui"
              for nome in pedidos if nome not in do_produto]
    achadas, faltaram = resolver_pedidos({nome: pedidas for nome, pedidas in pedidos.items()
                                          if nome in do_produto}, estacoes)
    if achadas:
        print("\n🚫 Fora dos mapas, a pedido (continuam na planilha):")
        for linha in descrever(achadas):
            print(f"   {linha}")
    for aviso in avisos + faltaram:
        print(f"⚠️ {aviso[0].upper()}{aviso[1:]}")
    return achadas
