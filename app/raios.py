"""Os raios do GOES-19 (GLM) no painel: as contas da página Raios, sem tela, para poder ser testado.

- **Agora**: os flashes recentes, cada um com a cor da idade, e a contagem a cada 5 minutos;
- **Densidade**: os flashes de um período por km², nas células de 0,1° que o coletor conta.

O GLM é um sensor óptico em órbita: vê os raios dentro da nuvem e os que chegam ao solo, sem
separar uns dos outros, com ~8 a 10 km de resolução, e não pega todos. O flash marca onde houve
atividade elétrica, não o ponto exato de uma descarga.
"""
import math
from datetime import datetime

import numpy as np
import pandas as pd
import shapely

from app import interativo
from modulos import config

CREDITO = "NOAA (GOES-19/GLM)/SEMADESC"
RAIO_DA_TERRA_KM = 6371.0088
# As idades do mapa do Agora, do flash mais novo ao mais velho: até tantos minutos, esta cor
IDADES = [(5, "#fff200"), (15, "#ff9900"), (30, "#ff2a00"), (60, "#a3007d")]
JANELAS_DO_AGORA = [15, 30, 60]   # minutos no mapa
HORAS_DO_GRAFICO = 3
MINUTOS_POR_BARRA = 5
# Flashes por km² no período: a célula sem flash fica sem cor; a primeira classe pega qualquer
# flash (um flash numa célula de 0,1° em MS dá ~0,009/km²)
NIVEIS_DENSIDADE = [0, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20]
CORES_DENSIDADE = ["#fff7bc", "#fee391", "#fec44f", "#fe9929", "#ec7014", "#cc4c02", "#d7301f", "#b30000",
                   "#7a0177", "#49006a"]
UNIDADE_DENSIDADE = "flashes/km²"


def idade_em_minutos(horas: pd.Series, agora: datetime) -> pd.Series:
    return (pd.Timestamp(agora) - horas).dt.total_seconds() / 60


def cor_da_idade(minutos: float) -> str | None:
    for limite, cor in IDADES:
        if minutos < limite:
            return cor
    return None


def rotulos_das_idades() -> list[tuple[str, str]]:
    # (cor, rótulo) para a legenda: "até 5 min", "5 a 15 min"...
    rotulos, anterior = [], 0
    for limite, cor in IDADES:
        rotulos.append((cor, f"até {limite} min" if anterior == 0 else f"{anterior} a {limite} min"))
        anterior = limite
    return rotulos


def pontos_por_idade(flashes: pd.DataFrame, agora: datetime, minutos: int, fuso) -> pd.DataFrame:
    # Os flashes dos últimos `minutos`, com a cor da idade e o balão do mapa. Os mais velhos vêm
    # primeiro, para o mais novo ficar por cima no desenho
    idade = idade_em_minutos(flashes["hora_utc"], agora)
    dentro = (idade >= 0) & (idade < minutos)
    pontos = flashes.loc[dentro, ["hora_utc", "longitude", "latitude"]].assign(idade=idade[dentro])
    pontos = pontos.sort_values("idade", ascending=False, kind="stable").reset_index(drop=True)
    pontos["cor"] = [interativo.rgba(cor_da_idade(valor)) for valor in pontos["idade"]]
    pontos["titulo"] = [f"Flash às {hora:%H:%M:%S}" for hora in pontos["hora_utc"].dt.tz_convert(fuso)]
    pontos["detalhe"] = [f"há {int(valor)} min" if valor >= 1 else "há menos de 1 min" for valor in pontos["idade"]]
    return pontos.drop(columns=["hora_utc"])


def no_estado(flashes: pd.DataFrame, estado) -> np.ndarray:
    if flashes.empty:
        return np.zeros(0, dtype=bool)
    return shapely.contains_xy(estado, flashes["longitude"].to_numpy(), flashes["latitude"].to_numpy())


def por_barra(flashes: pd.DataFrame, agora: datetime, fuso, horas: int = HORAS_DO_GRAFICO,
              minutos: int = MINUTOS_POR_BARRA) -> pd.DataFrame:
    # Os flashes a cada 5 minutos, com as barras vazias escritas: o gráfico mostra se a atividade
    # cresce ou enfraquece. A barra corrente vem pela metade. O começo de cada barra vai no horário
    # do estado, sem o fuso, como nos outros gráficos: o navegador não o converte
    passo = f"{minutos}min"
    ultima = pd.Timestamp(agora).tz_convert(fuso).floor(passo)
    inicios = pd.date_range(ultima - pd.Timedelta(hours=horas) + pd.Timedelta(minutes=minutos), ultima, freq=passo)
    contagem = flashes["hora_utc"].dt.tz_convert(fuso).dt.floor(passo).value_counts()
    return pd.DataFrame({"inicio": inicios.tz_localize(None),
                         "flashes": contagem.reindex(inicios, fill_value=0).to_numpy()})


# =====================================================
# DENSIDADE
# =====================================================
def celulas_da_caixa() -> tuple[np.ndarray, np.ndarray]:
    # Os índices das células (floor(grau × 10)) de oeste a leste e de sul a norte
    oeste, sul, leste, norte = (round(valor * config.CELULAS_POR_GRAU_RAIOS) for valor in config.CAIXA_RAIOS)
    return np.arange(oeste, leste), np.arange(sul, norte)


def area_da_celula_km2(lat_celula) -> np.ndarray:
    # A célula de 0,1° na esfera: encolhe com o cosseno da latitude (~116 km² no centro de MS)
    passo = 1 / config.CELULAS_POR_GRAU_RAIOS
    sul = np.radians(np.asarray(lat_celula, dtype=float) * passo)
    norte = np.radians((np.asarray(lat_celula, dtype=float) + 1) * passo)
    return RAIO_DA_TERRA_KM ** 2 * math.radians(passo) * (np.sin(norte) - np.sin(sul))


def densidade(por_celula: pd.DataFrame) -> pd.DataFrame:
    # A soma de cada célula (de fonte.raios_do_periodo) com o centro e os flashes por km²
    passo = 1 / config.CELULAS_POR_GRAU_RAIOS
    tabela = por_celula.copy()
    tabela["Longitude"] = (tabela["lon_celula"] + 0.5) * passo
    tabela["Latitude"] = (tabela["lat_celula"] + 0.5) * passo
    tabela["densidade"] = tabela["flashes"] / area_da_celula_km2(tabela["lat_celula"])
    return tabela


def matriz(por_celula: pd.DataFrame, coluna: str = "densidade") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    # A caixa inteira numa matriz (linhas de sul a norte), com as bordas das células para o
    # pcolormesh. A célula sem flash fica com 0
    xs, ys = celulas_da_caixa()
    valores = np.zeros((len(ys), len(xs)))
    if not por_celula.empty:
        dentro = por_celula["lon_celula"].between(xs[0], xs[-1]) & por_celula["lat_celula"].between(ys[0], ys[-1])
        tabela = por_celula[dentro]
        valores[tabela["lat_celula"] - ys[0], tabela["lon_celula"] - xs[0]] = tabela[coluna]
    passo = 1 / config.CELULAS_POR_GRAU_RAIOS
    return np.append(xs, xs[-1] + 1) * passo, np.append(ys, ys[-1] + 1) * passo, valores


def classe(valores) -> np.ndarray:
    # A classe de cada densidade: -1 sem flash; acima do último nível, a última classe
    valores = np.asarray(valores, dtype=float)
    classes = np.digitize(valores, NIVEIS_DENSIDADE[1:], right=True)
    return np.where(valores > 0, np.minimum(classes, len(CORES_DENSIDADE) - 1), -1)


def rotulos_das_classes() -> list[str]:
    rotulos = []
    for de, ate in zip(NIVEIS_DENSIDADE[:-1], NIVEIS_DENSIDADE[1:]):
        rotulos.append(f"até {_numero(ate)}" if de == 0 else f"{_numero(de)} a {_numero(ate)}")
    rotulos[-1] = f"acima de {_numero(NIVEIS_DENSIDADE[-2])}"
    return rotulos


def _numero(valor: float) -> str:
    return f"{valor:g}".replace(".", ",")


def celulas_do_mapa(por_celula: pd.DataFrame, minimo: float = 0.0) -> pd.DataFrame:
    # As células com flash para o mapa navegável: a cor da classe e o balão
    tabela = densidade(por_celula)
    tabela = tabela[tabela["flashes"] > minimo].reset_index(drop=True)
    classes = classe(tabela["densidade"])
    tabela["cor"] = [interativo.rgba(CORES_DENSIDADE[c], 200) for c in classes]
    tabela["titulo"] = [f"{n} flash" + ("es" if n != 1 else "") for n in tabela["flashes"]]
    tabela["detalhe"] = [f"{valor:.2f} flashes/km²".replace(".", ",") + f"\nCélula de 0,1° em "
                         f"{abs(lat):.2f}° S, {abs(lon):.2f}° O".replace(".", ",")
                         for valor, lon, lat in zip(tabela["densidade"], tabela["Longitude"], tabela["Latitude"])]
    return tabela


def horas_que_faltam(cobertura: pd.DataFrame, inicio: datetime, fim: datetime) -> dict[str, int]:
    # As horas de (início, fim] que não estão na contagem, e as que estão sem todos os arquivos
    esperadas = pd.date_range(pd.Timestamp(inicio).tz_convert("UTC") + pd.Timedelta(hours=1),
                              pd.Timestamp(fim).tz_convert("UTC"), freq="h")
    contadas = cobertura.set_index("hora_utc") if not cobertura.empty else None
    if contadas is None:
        return {"esperadas": len(esperadas), "ausentes": len(esperadas), "incompletas": 0}
    presentes = esperadas.isin(contadas.index)
    completas = contadas["arquivos"] + contadas["sem_arquivo"] >= config.ARQUIVOS_GLM_POR_HORA
    incompletas = int((~completas.reindex(esperadas[presentes]).fillna(False)).sum())
    return {"esperadas": len(esperadas), "ausentes": int((~presentes).sum()), "incompletas": incompletas}


def total_no_estado(por_celula: pd.DataFrame, estado) -> int:
    # Os flashes das células cujo centro cai dentro do estado
    if por_celula.empty:
        return 0
    tabela = densidade(por_celula)
    dentro = shapely.contains_xy(estado, tabela["Longitude"].to_numpy(), tabela["Latitude"].to_numpy())
    return int(tabela.loc[dentro, "flashes"].sum())


def nos_pixels(por_celula: pd.DataFrame, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    # A densidade em cada pixel de uma grade mais fina (a do GeoTIFF, de 0,01°): a da célula em que o
    # centro dele cai; fora da caixa, NaN. O pixel não cai na borda de uma célula: o centro dele fica
    # no meio do caminho entre duas bordas de 0,01°
    xs, ys = celulas_da_caixa()
    _, _, valores = matriz(densidade(por_celula))
    coluna = np.floor(np.asarray(lon) * config.CELULAS_POR_GRAU_RAIOS).astype(int) - xs[0]
    linha = np.floor(np.asarray(lat) * config.CELULAS_POR_GRAU_RAIOS).astype(int) - ys[0]
    dentro = (coluna >= 0) & (coluna < len(xs)) & (linha >= 0) & (linha < len(ys))
    campo = np.full(np.shape(lon), np.nan)
    campo[dentro] = valores[linha[dentro], coluna[dentro]]
    return campo
