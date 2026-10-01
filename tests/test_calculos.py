"""Cálculos compartilhados: recorte no tempo, extremos, acumulados e interpolação IDW."""
import numpy as np
import pandas as pd
import pytest

from modulos import calculos, config


def utc(texto):
    return pd.Timestamp(texto, tz="UTC")


def test_recorte_segue_a_hora_que_termina_na_leitura(serie):
    """A leitura das HH:00 fecha a hora anterior: entra a do fim da janela, não a do início."""
    dados = serie("2026-07-30", "2026-08-01")
    recorte = calculos.recortar(dados, utc("2026-07-30 04:00"), utc("2026-07-31 04:00"))
    assert len(recorte) == 24
    assert recorte["dt_utc"].min() == utc("2026-07-30 05:00")
    assert recorte["dt_utc"].max() == utc("2026-07-31 04:00")


def test_acumulado_de_chuva(serie):
    dados = serie("2026-07-30", "2026-07-31", CHUVA=0.5)
    dados.loc[3, "CHUVA"] = np.nan  # leitura sem dado conta como zero
    # Janela (00 UTC, 12 UTC]: leituras das 01 às 12 UTC, uma delas sem dado
    assert calculos.acumulado_chuva(dados, utc("2026-07-30 00:00"), utc("2026-07-30 12:00")) == 5.5
    assert calculos.acumulado_chuva(dados, utc("2026-08-01"), utc("2026-08-02")) == 0.0


def test_indice_do_extremo_e_data_hora(serie):
    dados = serie("2026-07-30", "2026-07-31")
    dados.loc[18, "TEM_MAX"] = 33.0  # leitura das 18 UTC
    indice = calculos.indice_extremo(dados, "TEM_MAX", minimo=False)
    assert dados.at[indice, "TEM_MAX"] == 33.0
    assert calculos.data_hora(dados, indice) == {"Data/Hora (UTC)": "30/07/2026 18:00", "Data/Hora (MS)": "30/07/2026 14:00"}


def test_a_coluna_de_horario_local_leva_a_sigla_do_estado(serie):
    """O valor sempre saiu do fuso certo; o rótulo é que dizia (MS) em qualquer estado.

    Uma planilha de SC com a coluna "Data/Hora (MS)" faz quem lê entender horário de Campo
    Grande onde está o de Florianópolis.
    """
    dados = serie("2026-07-30", "2026-07-31")
    indice = calculos.indice_extremo(dados, "TEM_MAX", minimo=False)

    assert "Data/Hora (SC)" in calculos.data_hora(dados, indice, "SC")
    assert "Data/Hora (MS)" in calculos.data_hora(dados, indice)   # sem sigla, o padrão do projeto


def test_extremo_sem_dados_validos(serie):
    dados = serie("2026-07-30", "2026-07-31", TEM_MIN=np.nan)
    assert calculos.indice_extremo(dados, "TEM_MIN", minimo=True) is None
    assert calculos.indice_extremo(dados, "COLUNA_INEXISTENTE", minimo=True) is None


def test_idw_reproduz_o_valor_na_posicao_da_estacao():
    lons, lats, valores = [-55.0, -53.0, -57.0], [-20.0, -22.0, -19.0], [10.0, 30.0, 20.0]
    resultado = calculos.interpolar_idw(lons, lats, valores, np.array([[-55.0, -53.0]]), np.array([[-20.0, -22.0]]))
    np.testing.assert_allclose(resultado, [[10.0, 30.0]])


def test_idw_mede_a_distancia_em_quilometros():
    """Estação A fica 1° a leste do ponto e B 1° ao norte: em graus, a mesma distância. Em MS,
    1° de longitude (~104 km) é mais curto que 1° de latitude (~111 km), então A pesa mais."""
    resultado = calculos.interpolar_idw([-54.0, -55.0], [-21.0, -20.0], [10.0, 0.0], np.array([[-55.0]]), np.array([[-21.0]]))
    assert resultado[0, 0] == pytest.approx(10 * 110.6**2 / (103.9**2 + 110.6**2), abs=0.02)  # ≈ 5,3 (em graus seria 5,0)


def test_idw_fica_entre_o_menor_e_o_maior_valor_das_estacoes():
    rng = np.random.default_rng(0)
    lons, lats, valores = rng.uniform(-58, -51, 20), rng.uniform(-24, -17.5, 20), rng.uniform(0, 40, 20)
    lon_grade, lat_grade = calculos.criar_grade((-58.5, -50.5, -24.5, -17.0), 50)
    resultado = calculos.interpolar_idw(lons, lats, valores, lon_grade, lat_grade)
    assert resultado.shape == (50, 50)
    assert valores.min() <= resultado.min() and resultado.max() <= valores.max()


def test_idw_de_campo_constante_e_constante():
    lon_grade, lat_grade = calculos.criar_grade((-58.5, -50.5, -24.5, -17.0), 20)
    resultado = calculos.interpolar_idw([-55, -53, -57, -54], [-20, -22, -19, -23], [7.0] * 4, lon_grade, lat_grade)
    assert resultado == pytest.approx(np.full((20, 20), 7.0))


# =====================================================
# PODA DAS ESTAÇÕES DE APOIO
# =====================================================
def estado_denso():
    """Um estado com 36 estações e a grade que vira desenho.

    Denso de propósito: com poucas estações, `k` mais próximas passa a ser "todas", e aí não há o
    que podar — o cenário precisa ter mais estações do que vizinhos para a poda significar algo.
    """
    lon, lat = calculos.criar_grade((-55.0, -54.0, -21.0, -20.0), 30)
    colunas, linhas = np.meshgrid(np.linspace(-54.95, -54.05, 6), np.linspace(-20.95, -20.05, 6))
    return lon, lat, colunas.ravel(), linhas.ravel()


def test_a_poda_guarda_quem_encosta_no_estado_e_larga_quem_esta_longe():
    """Estar dentro da margem não basta: é preciso chegar às mais próximas de alguma célula."""
    lon, lat, proprias_lon, proprias_lat = estado_denso()
    #                      logo além da divisa | 210 km a leste | 100 km a leste
    apoio_lon = np.array([-53.95, -52.00, -53.00])
    apoio_lat = np.array([-20.50, -20.50, -20.50])

    entram = calculos.apoio_que_entra(apoio_lon, apoio_lat, proprias_lon, proprias_lat,
                                      lon, lat, vizinhos=config.IDW_VIZINHOS)

    assert entram.tolist() == [True, False, False]


def test_a_poda_nao_muda_a_superficie_interpolada():
    """É a promessa da poda: corta requisição e deixa o desenho igual, bit a bit."""
    lon, lat, proprias_lon, proprias_lat = estado_denso()
    apoio_lon = np.array([-53.95, -53.90, -53.00, -52.00, -50.00, -56.50])
    apoio_lat = np.array([-20.50, -20.90, -20.50, -20.50, -20.50, -20.50])
    valores_proprias = np.linspace(20.0, 28.0, len(proprias_lon))
    valores_apoio = np.linspace(30.0, 35.0, len(apoio_lon))

    entram = calculos.apoio_que_entra(apoio_lon, apoio_lat, proprias_lon, proprias_lat,
                                      lon, lat, vizinhos=config.VIZINHOS_NA_PODA)

    def superficie(mascara):
        return calculos.interpolar_idw(
            np.concatenate([proprias_lon, apoio_lon[mascara]]),
            np.concatenate([proprias_lat, apoio_lat[mascara]]),
            np.concatenate([valores_proprias, valores_apoio[mascara]]),
            lon, lat, vizinhos=config.IDW_VIZINHOS)

    completa = superficie(np.ones(len(apoio_lon), dtype=bool))
    podada = superficie(entram)

    assert entram.sum() < len(apoio_lon)                 # a poda cortou alguma coisa
    assert np.array_equal(completa, podada)              # e mesmo assim o desenho é o mesmo


def test_a_folga_da_poda_so_acrescenta_estacao():
    """A folga existe para guardar quem só vira vizinha quando outra falta naquela hora."""
    lon, lat, proprias_lon, proprias_lat = estado_denso()
    apoio_lon = np.array([-53.95, -53.90, -53.85, -53.80, -53.70, -53.60])
    apoio_lat = np.full(len(apoio_lon), -20.50)

    justo = calculos.apoio_que_entra(apoio_lon, apoio_lat, proprias_lon, proprias_lat,
                                     lon, lat, vizinhos=config.IDW_VIZINHOS)
    folgado = calculos.apoio_que_entra(apoio_lon, apoio_lat, proprias_lon, proprias_lat,
                                       lon, lat, vizinhos=config.VIZINHOS_NA_PODA)

    assert config.VIZINHOS_NA_PODA > config.IDW_VIZINHOS
    assert (folgado | justo).tolist() == folgado.tolist()   # a folga nunca tira ninguém
    assert folgado.sum() > justo.sum()                      # e aqui ela acrescenta


def test_a_poda_sem_apoio_nenhum_nao_quebra():
    lon, lat, proprias_lon, proprias_lat = estado_denso()

    entram = calculos.apoio_que_entra([], [], proprias_lon, proprias_lat, lon, lat, vizinhos=8)

    assert entram.shape == (0,) and entram.dtype == bool


def test_a_poda_devolve_a_mascara_na_ordem_que_recebeu():
    """A máscara é posicional: quem chama filtra a tabela dele com ela."""
    lon, lat, proprias_lon, proprias_lat = estado_denso()
    apoio_lon = np.array([-50.00, -53.95, -50.50])
    apoio_lat = np.array([-20.50, -20.50, -20.50])

    entram = calculos.apoio_que_entra(apoio_lon, apoio_lat, proprias_lon, proprias_lat,
                                      lon, lat, vizinhos=config.IDW_VIZINHOS)

    assert entram.tolist() == [False, True, False]   # só a do meio encosta no estado
