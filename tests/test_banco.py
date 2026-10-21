"""O banco: o esquema, as partições, a gravação em lote e a leitura de volta.

Rodam contra um PostgreSQL de verdade, no schema `climageo_teste`, que é apagado e recriado a cada
módulo de testes (as fixtures `conexao` e `limpa`, no conftest.py). Na máquina de quem programa, a
conexão vem do BANCO_TESTE do .env; na CI, de um PostgreSQL 16 com PostGIS que o GitHub Actions
sobe só para os testes. Sem a conexão, os testes são pulados.
"""
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from modulos import banco, openmeteo

SCHEMA = "climageo_teste"   # o da fixture `conexao`, no conftest.py

RODADA = datetime(2026, 10, 9, 0, tzinfo=timezone.utc)


def previsao(pontos=("A702", "A721"), horas=3, rodada=RODADA, modelos=("ecmwf_ifs025", "gfs_seamless")) -> pd.DataFrame:
    linhas = []
    for modelo in modelos:
        for ponto in pontos:
            for hora in range(horas):
                linhas.append({"modelo": modelo, "rodada_utc": pd.Timestamp(rodada), "ponto": ponto,
                               "latitude": -15.78944444, "longitude": -47.92583332,   # como na lista do INMET
                               "hora_prevista_utc": pd.Timestamp(rodada) + pd.Timedelta(hours=hora + 1),
                               "temperatura": 25.5 + hora, "umidade": 60.0, "orvalho": 15.2, "chuva": np.nan,
                               "vento": 10.8, "rajada": 32.4, "direcao": 90.0})
    return pd.DataFrame(linhas)[openmeteo.COLUNAS_HORARIAS]


def estacoes() -> pd.DataFrame:
    return pd.DataFrame({"codigo": ["A702", "A721"], "nome": ["CAMPO GRANDE", "DOURADOS"], "uf": ["MS", "MS"],
                         "latitude": [-20.4472, -22.1931], "longitude": [-54.6181, -54.9114],
                         "altitude": [530.0, 469.0], "situacao": ["Operante", "Operante"]})


def leituras(horas=3, tem_ins=30.0) -> pd.DataFrame:
    linhas = []
    for estacao in ("A702", "A721"):
        for hora in range(horas):
            linha = {"estacao": estacao, "hora_utc": pd.Timestamp("2026-10-08 13:00", tz="UTC") + pd.Timedelta(hours=hora)}
            linha.update(dict.fromkeys(banco.COLUNAS_INMET, 1.0))
            linha["tem_ins"] = tem_ins
            linhas.append(linha)
    return pd.DataFrame(linhas)


# =====================================================
# CONEXÃO E ESQUEMA
# =====================================================
def test_sem_a_conexao_no_env_o_erro_diz_qual_variavel_falta(monkeypatch):
    monkeypatch.delenv("BANCO_GRAVACAO", raising=False)

    with pytest.raises(banco.ErroBanco, match="BANCO_GRAVACAO"):
        banco.conectar("gravacao")


def test_nome_de_schema_estranho_nao_vai_para_o_sql(monkeypatch):
    monkeypatch.setenv("BANCO_TESTE", "host=nenhum")

    with pytest.raises(banco.ErroBanco, match="nome inválido"):
        banco.conectar("teste", schema="climageo; DROP TABLE x")


def test_so_um_schema_de_teste_pode_ser_apagado_e_recriado():
    with pytest.raises(banco.ErroBanco, match="só apaga schemas de teste"):
        banco.recriar_schema_de_teste(None, "climageo")


def test_o_esquema_pode_rodar_de_novo_sem_erro(conexao):
    banco.criar_esquema(conexao)

    tabelas = dict(conexao.execute("SELECT table_name, table_type FROM information_schema.tables "
                                   "WHERE table_schema = %s", (SCHEMA,)).fetchall())
    assert tabelas == {"estacao": "BASE TABLE", "inmet_horaria": "BASE TABLE",
                       "previsao_horaria_grade": "BASE TABLE", "previsao_horaria_estacao": "BASE TABLE",
                       "previsao_semanal": "BASE TABLE", "previsao_horaria": "VIEW"}


# =====================================================
# PARTIÇÕES
# =====================================================
def test_a_grade_tem_uma_particao_por_dia_e_as_estacoes_uma_por_mes(limpa):
    criadas_grade = banco.garantir_particoes(limpa, "previsao_horaria_grade", RODADA, RODADA + timedelta(days=2))
    criadas_estacao = banco.garantir_particoes(limpa, "previsao_horaria_estacao", RODADA, RODADA + timedelta(days=40))
    limpa.commit()

    assert criadas_grade == ["previsao_horaria_grade_20261009", "previsao_horaria_grade_20261010",
                             "previsao_horaria_grade_20261011"]
    assert criadas_estacao == ["previsao_horaria_estacao_202610", "previsao_horaria_estacao_202611"]
    assert banco.garantir_particoes(limpa, "previsao_horaria_grade", RODADA, RODADA) == []   # já existe


def test_a_limpeza_da_grade_apaga_so_as_rodadas_com_mais_de_21_dias(limpa):
    banco.gravar_previsao(limpa, previsao(rodada=RODADA - timedelta(days=25)), "grade")
    banco.gravar_previsao(limpa, previsao(rodada=RODADA - timedelta(days=21)), "grade")
    banco.gravar_previsao(limpa, previsao(rodada=RODADA), "grade")
    banco.gravar_previsao(limpa, previsao(rodada=RODADA - timedelta(days=25)), "estacao")

    apagadas = banco.limpar_grade(limpa, agora=RODADA + timedelta(hours=6))

    assert apagadas == ["previsao_horaria_grade_20260914"]
    assert list(banco.particoes(limpa, "previsao_horaria_grade")) == ["previsao_horaria_grade_20260918",
                                                                      "previsao_horaria_grade_20261009"]
    # As estações, com o mesmo dia, ficam: a limpeza é só da grade
    assert banco.particoes(limpa, "previsao_horaria_estacao") == {"previsao_horaria_estacao_202609": date(2026, 9, 1)}


# =====================================================
# A PREVISÃO
# =====================================================
def test_a_previsao_volta_do_banco_como_entrou(limpa):
    enviada = previsao()

    gravadas = banco.gravar_previsao(limpa, enviada, "estacao")
    lida = banco.ler_previsao(limpa, "estacao", "ecmwf_ifs025")

    assert gravadas == len(enviada)
    esperada = enviada[enviada["modelo"] == "ecmwf_ifs025"].sort_values(["ponto", "hora_prevista_utc"])
    pd.testing.assert_frame_equal(lida.reset_index(drop=True), esperada.reset_index(drop=True),
                                  check_dtype=False, check_exact=True)


def test_a_mesma_rodada_gravada_de_novo_nao_duplica(limpa):
    banco.gravar_previsao(limpa, previsao(), "grade")

    assert banco.gravar_previsao(limpa, previsao(), "grade") == 0


def test_a_rodada_mais_nova_de_cada_modelo(limpa):
    banco.gravar_previsao(limpa, previsao(rodada=RODADA), "grade")
    banco.gravar_previsao(limpa, previsao(rodada=RODADA + timedelta(hours=6), modelos=("gfs_seamless",)), "grade")

    assert banco.rodadas_guardadas(limpa, "grade") == {"ecmwf_ifs025": pd.Timestamp(RODADA),
                                                        "gfs_seamless": pd.Timestamp(RODADA + timedelta(hours=6))}


def test_a_view_junta_a_grade_e_as_estacoes(limpa):
    banco.gravar_previsao(limpa, previsao(pontos=("-20.50;-54.50",)), "grade")
    banco.gravar_previsao(limpa, previsao(pontos=("A702",)), "estacao")

    contagem = dict(limpa.execute("SELECT conjunto, count(*) FROM previsao_horaria GROUP BY conjunto").fetchall())

    assert contagem == {"grade": 6, "estacao": 6}


def test_as_semanas_do_ec46_voltam_como_entraram(limpa):
    semanal = pd.DataFrame({"modelo": "ecmwf_ec46_ensemble_mean", "rodada_utc": pd.Timestamp(RODADA - timedelta(days=2)),
                            "ponto": "A702", "latitude": -20.45, "longitude": -54.62,
                            "semana": [date(2026, 10, 12), date(2026, 10, 19)],
                            "temperatura": [23.8, 24.1], "anom_temperatura": [-1.8, -1.0],
                            "temp_max": [25.6, 26.0], "anom_temp_max": [-2.1, -1.2],
                            "temp_min": [21.0, 21.5], "anom_temp_min": [-1.3, -0.7],
                            "chuva": [63.9, 48.5], "anom_chuva": [34.8, 18.2]})[openmeteo.COLUNAS_SEMANAIS]

    assert banco.gravar_semanas(limpa, semanal, "estacao") == 2
    lida = banco.ler_semanas(limpa, "estacao")

    pd.testing.assert_frame_equal(lida, semanal, check_dtype=False, check_exact=True)
    assert banco.ler_semanas(limpa, "grade").empty


def semanas_do(modelo: str, rodada, pontos=("A702",)) -> pd.DataFrame:
    return pd.DataFrame([{"modelo": modelo, "rodada_utc": pd.Timestamp(rodada), "ponto": ponto, "latitude": -20.45,
                          "longitude": -54.62, "semana": date(2026, 10, 19),
                          **{coluna: 1.0 for coluna in openmeteo.VARIAVEIS_SEMANAIS.values()}}
                         for ponto in pontos])[openmeteo.COLUNAS_SEMANAIS]


def test_a_rodada_mais_nova_de_cada_modelo_atravessa_as_particoes(limpa):
    # O ICON tem a rodada mais nova noutro mês, numa partição só dele; o banco salta de modelo em
    # modelo pelo índice, sem varrer a tabela
    modelos = ("ecmwf_ifs025", "gfs_seamless", "icon_seamless")
    banco.gravar_previsao(limpa, previsao(rodada=RODADA, modelos=modelos), "estacao")
    banco.gravar_previsao(limpa, previsao(rodada=RODADA + timedelta(days=31), modelos=("icon_seamless",)), "estacao")
    banco.gravar_previsao(limpa, previsao(rodada=RODADA - timedelta(days=1), modelos=("gfs_seamless",)), "estacao")

    assert banco.rodadas_guardadas(limpa, "estacao") == {"ecmwf_ifs025": pd.Timestamp(RODADA),
                                                          "gfs_seamless": pd.Timestamp(RODADA),
                                                          "icon_seamless": pd.Timestamp(RODADA + timedelta(days=31))}
    assert banco.rodadas_guardadas(limpa, "grade") == {}


def test_as_rodadas_ausentes_sao_as_esperadas_que_nao_estao_no_conjunto(limpa):
    banco.gravar_previsao(limpa, previsao(rodada=RODADA), "estacao")   # o ECMWF e o GFS
    outra = RODADA + timedelta(hours=12)
    esperadas = [("ecmwf_ifs025", outra), ("ecmwf_ifs025", RODADA), ("icon_seamless", RODADA)]

    assert banco.rodadas_ausentes(limpa, "estacao", esperadas) == [("icon_seamless", pd.Timestamp(RODADA)),
                                                                    ("ecmwf_ifs025", pd.Timestamp(outra))]
    assert len(banco.rodadas_ausentes(limpa, "grade", esperadas)) == 3
    assert banco.rodadas_ausentes(limpa, "grade", []) == []


def test_a_ultima_rodada_das_semanas_e_a_do_conjunto_pedido(limpa):
    ec46 = "ecmwf_ec46_ensemble_mean"
    banco.gravar_semanas(limpa, semanas_do(ec46, RODADA), "estacao")
    banco.gravar_semanas(limpa, semanas_do(ec46, RODADA + timedelta(days=1)), "grade")

    assert banco.rodadas_semanais_guardadas(limpa, "estacao") == {ec46: pd.Timestamp(RODADA)}
    assert banco.rodadas_semanais_guardadas(limpa, "grade") == {ec46: pd.Timestamp(RODADA + timedelta(days=1))}


def test_as_semanas_sao_lidas_do_modelo_pedido(limpa):
    banco.gravar_semanas(limpa, semanas_do("ecmwf_ec46_ensemble_mean", RODADA, pontos=("A702", "A721")), "estacao")
    banco.gravar_semanas(limpa, semanas_do("outro_modelo", RODADA + timedelta(days=1)), "estacao")

    assert banco.ler_semanas(limpa, "estacao")["ponto"].tolist() == ["A702", "A721"]   # o EC46, na rodada dele
    assert banco.ler_semanas(limpa, "estacao", modelo="outro_modelo")["modelo"].unique().tolist() == ["outro_modelo"]


# =====================================================
# O INMET
# =====================================================
def test_o_cadastro_calcula_a_geometria_e_nao_perde_o_que_nao_veio(limpa):
    banco.gravar_estacoes(limpa, estacoes())
    # A consulta por hora traz nome, UF e coordenadas, mas não a altitude nem a situação
    banco.gravar_estacoes(limpa, estacoes().drop(columns=["altitude", "situacao"]).assign(nome="CAMPO GRANDE II"))

    linha = limpa.execute("SELECT nome, altitude, situacao, ST_X(geom), ST_Y(geom) FROM estacao "
                          "WHERE codigo = 'A702'").fetchone()

    assert linha[:3] == ("CAMPO GRANDE II", 530.0, "Operante")
    assert linha[3:] == pytest.approx((-54.6181, -20.4472))


def test_as_leituras_voltam_do_banco_na_janela_aberta_no_comeco(limpa):
    banco.gravar_estacoes(limpa, estacoes())
    banco.gravar_inmet(limpa, leituras(horas=3))

    lidas = banco.ler_inmet(limpa, pd.Timestamp("2026-10-08 13:00", tz="UTC"),
                            pd.Timestamp("2026-10-08 15:00", tz="UTC"), estacoes=["A702"])

    assert list(lidas["hora_utc"].dt.hour) == [14, 15]          # a das 13:00 fica de fora
    assert set(lidas["estacao"]) == {"A702"} and (lidas["tem_ins"] == 30.0).all()


def test_rebuscar_as_horas_so_reescreve_o_que_mudou(limpa):
    banco.gravar_estacoes(limpa, estacoes())
    assert banco.gravar_inmet(limpa, leituras(horas=3)) == 6

    assert banco.gravar_inmet(limpa, leituras(horas=3)) == 0      # nada mudou
    mudou = leituras(horas=3)
    mudou.loc[0, "tem_ins"] = 31.5
    assert banco.gravar_inmet(limpa, mudou) == 1


def test_a_bateria_e_a_consulta_por_hora_nao_se_apagam(limpa):
    banco.gravar_estacoes(limpa, estacoes())
    banco.gravar_inmet(limpa, leituras(horas=1))
    extras = leituras(horas=1)[["estacao", "hora_utc"]].assign(tem_sen=31.0, ten_bat=12.6, tem_cpu=40.0)
    banco.gravar_extras_inmet(limpa, extras)
    banco.gravar_inmet(limpa, leituras(horas=1, tem_ins=29.0))       # a consulta por hora volta, mudada

    linha = limpa.execute("SELECT tem_ins, ten_bat FROM inmet_horaria WHERE estacao = 'A702'").fetchone()

    assert linha == pytest.approx((29.0, 12.6))


def test_o_cadastro_so_reescreve_a_estacao_que_mudou(limpa):
    assert banco.gravar_estacoes(limpa, estacoes()) == 2
    antes = limpa.execute("SELECT atualizada_em FROM estacao WHERE codigo = 'A721'").fetchone()[0]

    assert banco.gravar_estacoes(limpa, estacoes()) == 0
    assert banco.gravar_estacoes(limpa, estacoes().assign(situacao=["Operante", "Pane"])) == 1
    depois = limpa.execute("SELECT situacao, atualizada_em FROM estacao WHERE codigo = 'A721'").fetchone()
    assert depois[0] == "Pane" and depois[1] > antes


def test_a_consulta_por_hora_so_acrescenta_estacoes_novas(limpa):
    banco.gravar_estacoes(limpa, estacoes())
    da_hora = pd.DataFrame({"codigo": ["A702", "A004"], "nome": ["OUTRO NOME", "NIQUELANDIA"], "uf": ["MS", "GO"],
                            "latitude": [-20.0, -14.47], "longitude": [-54.0, -48.46]})

    assert banco.gravar_estacoes(limpa, da_hora, so_as_novas=True) == 1

    nomes = dict(limpa.execute("SELECT codigo, nome FROM estacao").fetchall())
    assert nomes == {"A702": "CAMPO GRANDE", "A721": "DOURADOS", "A004": "NIQUELANDIA"}


def test_as_horas_guardadas_e_as_estacoes_com_leituras(limpa):
    banco.gravar_estacoes(limpa, estacoes())
    banco.gravar_inmet(limpa, leituras(horas=3).query("estacao == 'A702' or hora_utc.dt.hour == 13"))

    horas = banco.horas_guardadas(limpa, pd.Timestamp("2026-10-08 13:00", tz="UTC"),
                                  pd.Timestamp("2026-10-08 14:00", tz="UTC"))
    assert horas == {pd.Timestamp("2026-10-08 13:00", tz="UTC"), pd.Timestamp("2026-10-08 14:00", tz="UTC")}
    assert banco.estacoes_com_leituras(limpa, pd.Timestamp("2026-10-08 13:00", tz="UTC"),
                                       pd.Timestamp("2026-10-08 16:00", tz="UTC")) == ["A702"]


def test_a_leitura_nao_deixa_transacao_aberta(limpa):
    from psycopg.pq import TransactionStatus

    banco.horas_guardadas(limpa, RODADA, RODADA)
    banco.ler_previsao(limpa, "grade", "ecmwf_ifs025")
    assert limpa.info.transaction_status == TransactionStatus.IDLE

    # No meio de uma gravação, a leitura não confirma nem desfaz nada
    limpa.execute("INSERT INTO estacao (codigo, nome, uf, latitude, longitude) VALUES ('A999', 'X', 'MS', -20, -54)")
    banco.estacoes_com_leituras(limpa, RODADA, RODADA)
    assert limpa.info.transaction_status == TransactionStatus.INTRANS
    limpa.rollback()


def test_as_medidas_voltam_do_banco_exatas_ate_6_algarismos(limpa):
    banco.gravar_estacoes(limpa, estacoes())
    valores = [889.2, 1013.2, 3523.6, -3.5, 12.6, 0.2, 31.4, 359.0, 25.45, 123456.0, 0.123456]
    enviadas = pd.DataFrame({"estacao": "A702", "hora_utc": [pd.Timestamp("2026-10-08 13:00", tz="UTC")
                                                               + pd.Timedelta(hours=n) for n in range(len(valores))],
                             "pre_ins": valores})
    banco.gravar_inmet(limpa, enviadas)

    lidas = banco.ler_inmet(limpa, pd.Timestamp("2026-10-08 12:00", tz="UTC"), pd.Timestamp("2026-10-09", tz="UTC"))

    assert lidas["pre_ins"].tolist() == valores
