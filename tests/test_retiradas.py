"""Estações que a equipe tira dos mapas de propósito, uma lista por variável."""
import pandas as pd
import pytest

from modulos import retiradas

ESTACOES = pd.DataFrame({"Estação": ["Campo Grande", "Ponta Porã", "Três Lagoas", "Guaíra"],
                         "CD_ESTACAO": ["A702", "A703", "A704", None]})


@pytest.mark.parametrize("texto, esperado", [
    ("chuva", "Chuva"), ("TEMPERATURA", "Temperatura"), ("pressao", "Pressão"), ("Radiacao", "Radiação"),
    ("risco", "Risco de Fogo"), ("Risco de fogo", "Risco de Fogo"), ("rajada", "Vento"), (" Umidade ", "Umidade"),
])
def test_a_variavel_vale_em_qualquer_caixa_com_ou_sem_acento(texto, esperado):
    assert retiradas.variavel(texto) == esperado


def test_variavel_desconhecida_diz_quais_valem():
    with pytest.raises(ValueError, match="valem: Temperatura"):
        retiradas.variavel("granizo")


def test_os_pedidos_da_linha_de_comando():
    pedidos = retiradas.ler_pedidos(["Chuva: Campo Grande; Dourados", "temperatura:A702", "chuva: Coxim, Navirai"])

    assert pedidos == {"Chuva": ["Campo Grande", "Dourados", "Coxim", "Navirai"], "Temperatura": ["A702"]}


@pytest.mark.parametrize("texto, motivo", [
    ("Campo Grande", "escreva a variável"),
    ("Chuva:", "nenhuma estação"),
    ("Granizo: Campo Grande", "variável desconhecida"),
])
def test_pedido_mal_escrito_vira_erro_legivel(texto, motivo):
    with pytest.raises(ValueError, match=motivo):
        retiradas.ler_pedidos([texto])


def test_a_estacao_vale_pelo_nome_sem_acento_ou_pelo_codigo():
    achadas, faltaram = retiradas.resolver(["ponta pora", "A704", "TRES  LAGOAS", "Guaira", "Itaquiraí"], ESTACOES)

    assert achadas == {"Ponta Porã", "Três Lagoas", "Guaíra"}
    assert faltaram == ["Itaquiraí"]


def test_os_pedidos_resolvidos_e_os_avisos_do_que_nao_se_achou():
    achadas, avisos = retiradas.resolver_pedidos({"Chuva": ["Campo Grande", "Xyz"], "Umidade": ["Abc"]}, ESTACOES)

    assert achadas == {"Chuva": {"Campo Grande"}}       # a umidade não achou nenhuma e fica de fora
    assert len(avisos) == 2 and "'Xyz' (Chuva)" in avisos[0] and "código do INMET" in avisos[0]


def test_retirar_de_uma_tabela_e_de_uma_serie_por_estacao():
    tabela = pd.DataFrame({"Estação": ["Campo Grande", "Ponta Porã", "Três Lagoas"], "Chuva": [1.0, 2.0, 3.0]},
                          index=[5, 6, 7])
    serie = tabela.set_index("Estação")["Chuva"]

    assert retiradas.retirar(tabela, {"Ponta Porã"})["Estação"].tolist() == ["Campo Grande", "Três Lagoas"]
    assert retiradas.retirar(serie, {"Ponta Porã", "Fora da lista"}).to_dict() == {"Campo Grande": 1.0,
                                                                                  "Três Lagoas": 3.0}
    assert retiradas.retirar(tabela, set()) is tabela   # sem retirada, o mesmo objeto (e o mesmo cache)
    assert retiradas.retirar(None, {"Ponta Porã"}) is None


def test_para_o_produto_avisa_o_que_nao_muda_nada_ali(capsys):
    pedidos = {"Chuva": ["Campo Grande"], "Risco de Fogo": ["A703"], "Pressão": ["Três Lagoas"]}

    fora = retiradas.para_o_produto(pedidos, ESTACOES, {"Chuva", "Temperatura"}, "relatorio_inmet")

    assert fora == {"Chuva": {"Campo Grande"}}
    saida = capsys.readouterr().out
    assert "Fora dos mapas, a pedido" in saida and "Chuva: Campo Grande" in saida
    assert "O relatorio_inmet não faz mapa de Risco de Fogo" in saida
    assert "O relatorio_inmet não faz mapa de Pressão" in saida
    assert retiradas.para_o_produto({}, ESTACOES, {"Chuva"}, "relatorio_inmet") == {}
