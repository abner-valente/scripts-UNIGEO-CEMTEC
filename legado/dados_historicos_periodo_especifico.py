import pandas as pd
import requests
import time
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from openpyxl.styles import PatternFill, Font, Alignment
import geopandas as gpd
import numpy as np
import matplotlib.pyplot as plt
from scipy.spatial import cKDTree
from shapely.geometry import Point, box
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.lines import Line2D
import warnings
warnings.filterwarnings('ignore')

# =====================================================
# CONFIGURAÇÕES
# =====================================================
UF = "MS"
TOKEN_INMET = "SEU_TOKEN_AQUI"  # token removido do código: use o arquivo .env do projeto

# TUDO em UTC
FUSO_UTC = ZoneInfo("UTC")
FUSO_MS = ZoneInfo("America/Campo_Grande")

# =====================================================
# CONFIGURAÇÃO DO PERÍODO DE BUSCA (ALTERE AQUI)
# =====================================================
# Data INICIAL do período (inclusiva)
DATA_INICIO = datetime(2026, 8, 1, 0, 0, 0, tzinfo=FUSO_UTC)

# Data FINAL do período (inclusiva - todo o dia até 23:59:59)
DATA_FINAL = datetime(2026, 8, 31, 0, 0, 0, tzinfo=FUSO_UTC)

# Ajuste automático: DATA_FIM = DATA_FINAL + 1 dia (00:00) para incluir todo o último dia
DATA_INI = DATA_INICIO
DATA_FIM = DATA_FINAL + timedelta(days=1)

# Número de dias do período (informativo)
NUM_DIAS = (DATA_FINAL.date() - DATA_INICIO.date()).days + 1

# Texto formatado do período (para títulos)
TEXTO_PERIODO = (
    f"{DATA_INICIO.strftime('%d/%m/%Y')} a "
    f"{DATA_FINAL.strftime('%d/%m/%Y')} "
    f"({NUM_DIAS} dias)"
)

# =====================================================
# CONFIGURAÇÃO DE SAÍDA
# =====================================================
PASTA_SAIDA = Path("saida_periodo")
PASTA_SAIDA.mkdir(exist_ok=True)

# Shapefiles usados no mapa de Mato Grosso do Sul
SHAPE_MUN = "MS_mun.shp"
SHAPE_UF = "MS_UF_2022.shp"

# Logos
LOGO_CEMTEC = "logo_cemtec.jpeg"
LOGO_SEMAGRO = "semagro_logo.jpeg"

# Nome do arquivo com o período
PERIODO_STR = (
    f"{DATA_INICIO.strftime('%Y%m%d')}_a_"
    f"{DATA_FINAL.strftime('%Y%m%d')}"
)
ARQUIVO_SAIDA = PASTA_SAIDA / f"Relatorio_Periodo_{UF}_{PERIODO_STR}.xlsx"

# =====================================================
# FUNÇÕES DE BUSCA E CÁLCULO
# =====================================================
def get_inmet_data(cod, data_ini, data_fim):
    """Busca dados horários do INMET e mantém TUDO em UTC"""
    url = f"https://apitempo.inmet.gov.br/token/estacao/{data_ini.strftime('%Y-%m-%d')}/{data_fim.strftime('%Y-%m-%d')}/{cod}/{TOKEN_INMET}"
    try:
        response = requests.get(url, timeout=60)

        if response.status_code != 200:
            print(f"    ⚠️ Erro HTTP {response.status_code}: {response.text[:100]}")
            return None

        r = response.json()
        if isinstance(r, list) and len(r) > 0:
            df = pd.DataFrame(r)

            cols_num = ["TEM_MAX", "TEM_MIN", "UMD_MIN", "UMD_MAX", "VEN_RAJ", "VEN_DIR", "CHUVA"]
            for c in cols_num:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "."), errors="coerce")

            df['dt_utc'] = pd.to_datetime(
                df['DT_MEDICAO'] + ' ' +
                df['HR_MEDICAO'].apply(lambda x: f"{str(x).zfill(4)[:2]}:00"),
                errors='coerce',
                utc=True
            )

            df['dt_local'] = df['dt_utc'].dt.tz_convert(FUSO_MS)

            return df.dropna(subset=['dt_utc'])
        else:
            print(f"    ⚠️ Resposta vazia ou inválida para estação {cod}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"    ⚠️ Erro de conexão: {e}")
        return None
    except Exception as e:
        print(f"    ⚠️ Erro ao processar dados: {e}")
        return None

def calcular_acumulado_periodo(df, data_ini, data_fim):
    """Soma a chuva entre duas datas específicas (TUDO em UTC)"""
    sub_df = df[(df['dt_utc'] >= data_ini) & (df['dt_utc'] < data_fim)]
    return round(sub_df["CHUVA"].fillna(0).sum(), 1) if not sub_df.empty else 0.0

def formatar_nome_estacao(nome):
    """Padroniza o nome da estação"""
    nome = str(nome).strip().title()
    palavras_minusculas = {"da", "das", "de", "do", "dos", "e"}
    partes = nome.split()
    partes = [
        palavra.lower()
        if palavra.lower() in palavras_minusculas and i > 0
        else palavra
        for i, palavra in enumerate(partes)
    ]
    return " ".join(partes)

# =====================================================
# FUNÇÕES PARA GERAÇÃO DE MAPAS
# =====================================================
def carregar_shapefiles():
    """Carrega os shapefiles necessários para os mapas"""
    try:
        uf = gpd.read_file(SHAPE_UF).to_crs("EPSG:4326")
        mun = gpd.read_file(SHAPE_MUN).to_crs("EPSG:4326")
        return uf, mun
    except Exception as e:
        print(f"⚠️ Não foi possível carregar os shapefiles: {e}")
        return None, None

def plotar_base_mapa(ax, mun, uf):
    """Plota a base do mapa (municípios e estado)"""
    mun.boundary.plot(ax=ax, linewidth=0.35, color="gray")
    uf.boundary.plot(ax=ax, linewidth=1.8, color="black")

def adicionar_logos(fig, ax):
    """Adiciona os logos do CEMTEC e SEMAGRO dentro da área do mapa"""
    try:
        LON_MIN, LON_MAX = -58.5, -50.5
        LAT_MIN, LAT_MAX = -24.5, -17.0

        if Path(LOGO_SEMAGRO).exists():
            im_semagro = plt.imread(LOGO_SEMAGRO)
            imagebox_semagro = OffsetImage(im_semagro, zoom=0.14)
            ab_semagro = AnnotationBbox(
                imagebox_semagro,
                (LON_MAX - 2.7, LAT_MAX - 0.08),
                xycoords='data',
                frameon=False,
                box_alignment=(0, 1),
                zorder=10
            )
            ax.add_artist(ab_semagro)
            print(f"✅ Logo SEMAGRO adicionado dentro do mapa")
        else:
            print(f"⚠️ Logo SEMAGRO não encontrado: {Path(LOGO_SEMAGRO).resolve()}")

        if Path(LOGO_CEMTEC).exists():
            im_cemtec = plt.imread(LOGO_CEMTEC)
            imagebox_cemtec = OffsetImage(im_cemtec, zoom=0.12)
            ab_cemtec = AnnotationBbox(
                imagebox_cemtec,
                (LON_MAX - 0.3, LAT_MAX - 1.0),
                xycoords='data',
                frameon=False,
                box_alignment=(1, 1),
                zorder=10
            )
            ax.add_artist(ab_cemtec)
            print(f"✅ Logo CEMTEC adicionado dentro do mapa")
        else:
            print(f"⚠️ Logo CEMTEC não encontrado: {Path(LOGO_CEMTEC).resolve()}")

    except Exception as e:
        print(f"⚠️ Não foi possível adicionar os logos: {e}")

def configurar_eixo_mapa(ax, titulo):
    """Configura os limites e labels do eixo do mapa"""
    LON_MIN, LON_MAX = -58.5, -50.5
    LAT_MIN, LAT_MAX = -24.5, -17.0

    ax.set_title(titulo, fontsize=16, weight="bold", pad=20)
    ax.set_xlim(LON_MIN, LON_MAX)
    ax.set_ylim(LAT_MIN, LAT_MAX)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

def criar_grid_interpolacao(resolucao=100):
    """Cria um grid para interpolação"""
    lon_min, lon_max = -58.5, -50.5
    lat_min, lat_max = -24.5, -17.0

    lon_grid = np.linspace(lon_min, lon_max, resolucao)
    lat_grid = np.linspace(lat_min, lat_max, resolucao)

    lon_mesh, lat_mesh = np.meshgrid(lon_grid, lat_grid)

    return lon_mesh, lat_mesh

def interpolar_dados(gdf, coluna_valor, resolucao=100):
    """Realiza interpolação IDW (Inverse Distance Weighting)"""
    coords = np.array([(geom.x, geom.y) for geom in gdf.geometry])
    valores = gdf[coluna_valor].values

    if len(coords) < 3:
        print(f"⚠️ Muito poucos pontos para interpolação (mínimo 3, tem {len(coords)})")
        return None, None, None

    lon_mesh, lat_mesh = criar_grid_interpolacao(resolucao)

    pontos_grid = np.column_stack([lon_mesh.ravel(), lat_mesh.ravel()])

    tree = cKDTree(coords)

    k = min(8, len(coords))

    if k > 1:
        distancias, indices = tree.query(pontos_grid, k=k)
    else:
        distancias, indices = tree.query(pontos_grid, k=1)
        distancias = distancias.reshape(-1, 1)
        indices = indices.reshape(-1, 1)

    distancias = np.maximum(distancias, 1e-10)

    pesos = 1.0 / (distancias ** 2)

    valores_vizinhos = valores[indices]

    valores_interpolados = np.sum(valores_vizinhos * pesos, axis=1) / np.sum(pesos, axis=1)

    grid_valores = valores_interpolados.reshape(lon_mesh.shape)

    return lon_mesh, lat_mesh, grid_valores

def gerar_mapa_pontual(df_dados, coluna_valor, titulo, nome_arquivo, cmap, unidade, subtitulo, ranking_titulo=None, ranking_ordem="maiores"):
    """Gera mapa pontual com os dados"""
    if df_dados.empty:
        print(f"⚠️ Não há dados para gerar o mapa: {titulo}")
        return

    uf, mun = carregar_shapefiles()
    if uf is None or mun is None:
        return

    gdf = gpd.GeoDataFrame(
        df_dados,
        geometry=gpd.points_from_xy(
            df_dados["Longitude"],
            df_dados["Latitude"]
        ),
        crs="EPSG:4326"
    )

    fig, ax = plt.subplots(figsize=(12, 12))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    plotar_base_mapa(ax, mun, uf)

    valores = gdf[coluna_valor]

    if valores.min() != valores.max():
        tamanho = np.clip((valores - valores.min()) / (valores.max() - valores.min()) * 150 + 50, 50, 200)
    else:
        tamanho = 100

    gdf.plot(
        ax=ax,
        column=coluna_valor,
        cmap=cmap,
        markersize=tamanho,
        edgecolor="black",
        linewidth=0.8,
        legend=True,
        legend_kwds={
            "label": unidade,
            "shrink": 0.75
        }
    )

    for _, row in gdf.iterrows():
        ax.text(
            row.geometry.x,
            row.geometry.y,
            f'{row[coluna_valor]:.1f}',
            ha="center",
            va="center",
            fontsize=8,
            fontweight="bold",
            color="black",
            bbox=dict(
                boxstyle="round,pad=0.15",
                facecolor="white",
                edgecolor="none",
                alpha=0.75
            )
        )

    titulo_completo = f"{titulo}\n{subtitulo}"

    configurar_eixo_mapa(ax, titulo_completo)

    if ranking_titulo and len(gdf) > 0:
        if ranking_ordem == "maiores":
            ranking = gdf.nlargest(5, coluna_valor)
        else:
            ranking = gdf.nsmallest(5, coluna_valor)

        texto_ranking = f"{ranking_titulo}:\n"
        for _, row in ranking.iterrows():
            texto_ranking += f"{row['Estação']}: {row[coluna_valor]:.1f}\n"

        ax.text(
            0.97, 0.04,
            texto_ranking,
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=10,
            weight="bold",
            bbox=dict(
                boxstyle="round,pad=0.5",
                facecolor="white",
                edgecolor="black",
                alpha=0.9
            )
        )

    adicionar_logos(fig, ax)

    plt.tight_layout(rect=[0, 0, 1, 0.93])

    saida_mapa = PASTA_SAIDA / f"{nome_arquivo}_{PERIODO_STR}.png"
    plt.savefig(saida_mapa, dpi=300, bbox_inches="tight", facecolor='white', edgecolor='none')
    plt.close()

    print(f"🗺️ Mapa pontual salvo em: {saida_mapa}")

def gerar_mapa_interpolado(df_dados, coluna_valor, titulo, nome_arquivo, cmap, unidade, subtitulo, ranking_titulo=None, ranking_ordem="maiores"):
    """Gera mapa interpolado com os dados, recortado para os limites do MS"""
    if df_dados.empty:
        print(f"⚠️ Não há dados para gerar o mapa interpolado: {titulo}")
        return

    if len(df_dados) < 3:
        print(f"⚠️ Muito poucas estações com dados para interpolar ({len(df_dados)}). Mínimo necessário: 3")
        return

    uf, mun = carregar_shapefiles()
    if uf is None or mun is None:
        return

    gdf = gpd.GeoDataFrame(
        df_dados,
        geometry=gpd.points_from_xy(
            df_dados["Longitude"],
            df_dados["Latitude"]
        ),
        crs="EPSG:4326"
    )

    resultado_interp = interpolar_dados(gdf, coluna_valor)
    if resultado_interp[0] is None:
        return

    lon_mesh, lat_mesh, grid_valores = resultado_interp

    ms_polygon = uf.geometry.union_all()

    pontos_grid = np.column_stack([lon_mesh.ravel(), lat_mesh.ravel()])

    mascara_dentro = np.array([ms_polygon.contains(Point(p[0], p[1])) for p in pontos_grid])
    mascara_dentro = mascara_dentro.reshape(lon_mesh.shape)

    grid_valores_mascarado = np.ma.masked_where(~mascara_dentro, grid_valores)

    fig, ax = plt.subplots(figsize=(14, 12))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    limites_mapa = box(-58.5, -24.5, -50.5, -17.0)
    area_fora = limites_mapa.difference(ms_polygon)
    if not area_fora.is_empty:
        gpd.GeoSeries([area_fora]).plot(ax=ax, color='white', alpha=0.3, zorder=0)

    im = ax.contourf(lon_mesh, lat_mesh, grid_valores_mascarado, levels=20, cmap=cmap, alpha=0.8)

    cbar = plt.colorbar(im, ax=ax, label=unidade, shrink=0.75)

    plotar_base_mapa(ax, mun, uf)

    gdf.plot(ax=ax, color='black', markersize=50, alpha=0.7, edgecolor='white', linewidth=1.5)

    for _, row in gdf.iterrows():
        ax.text(
            row.geometry.x,
            row.geometry.y,
            f'{row[coluna_valor]:.1f}',
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
            color="white",
            bbox=dict(
                boxstyle="round,pad=0.15",
                facecolor="black",
                edgecolor="white",
                alpha=0.8
            )
        )

    titulo_completo = f"{titulo}\n{subtitulo}"

    configurar_eixo_mapa(ax, titulo_completo)

    if ranking_titulo and len(gdf) > 0:
        if ranking_ordem == "maiores":
            ranking = gdf.nlargest(5, coluna_valor)
        else:
            ranking = gdf.nsmallest(5, coluna_valor)

        texto_ranking = f"{ranking_titulo}:\n"
        for _, row in ranking.iterrows():
            texto_ranking += f"{row['Estação']}: {row[coluna_valor]:.1f}\n"

        ax.text(
            0.97, 0.04,
            texto_ranking,
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=9,
            weight="bold",
            bbox=dict(
                boxstyle="round,pad=0.5",
                facecolor="white",
                edgecolor="black",
                alpha=0.9
            )
        )

    adicionar_logos(fig, ax)

    plt.tight_layout(rect=[0, 0, 1, 0.93])

    saida_mapa = PASTA_SAIDA / f"{nome_arquivo}_{PERIODO_STR}_interpolado.png"
    plt.savefig(saida_mapa, dpi=300, bbox_inches="tight", facecolor='white', edgecolor='none')
    plt.close()

    print(f"🗺️ Mapa interpolado salvo em: {saida_mapa}")

# =====================================================
# FUNÇÕES ESPECÍFICAS PARA CADA VARIÁVEL
# =====================================================
def gerar_mapa_umidade(res_umid, subtitulo):
    """Gera mapas pontuais e interpolados de umidade relativa"""
    if not res_umid:
        print("⚠️ Não há dados de umidade para gerar os mapas.")
        return

    df_umidade = pd.DataFrame(res_umid).copy()

    df_umidade_com_coord = []
    for _, est in df_estacoes.iterrows():
        nome_est = formatar_nome_estacao(est["DC_NOME"])
        if nome_est in df_umidade["Estação"].values:
            df_umidade_com_coord.append({
                "Estação": nome_est,
                "Umidade Mín (%)": df_umidade[df_umidade["Estação"] == nome_est]["Umidade Mín (%)"].values[0],
                "Latitude": float(est["VL_LATITUDE"]),
                "Longitude": float(est["VL_LONGITUDE"])
            })

    if df_umidade_com_coord:
        df_umidade_plot = pd.DataFrame(df_umidade_com_coord)

        gerar_mapa_pontual(
            df_umidade_plot,
            "Umidade Mín (%)",
            "Umidade relativa mínima em Mato Grosso do Sul",
            "Mapa_Umidade_MS",
            "YlGnBu",
            "Umidade (%)",
            subtitulo,
            "5 MENORES UMIDADES",
            "menores"
        )

        gerar_mapa_interpolado(
            df_umidade_plot,
            "Umidade Mín (%)",
            "Umidade relativa mínima em Mato Grosso do Sul",
            "Mapa_Umidade_MS",
            "YlGnBu",
            "Umidade (%)",
            subtitulo,
            "5 MENORES UMIDADES",
            "menores"
        )

def gerar_mapa_chuva(res_chuva, subtitulo):
    """Gera mapas pontuais e interpolados de chuva acumulada no período"""
    if not res_chuva:
        print("⚠️ Não há dados de chuva para gerar os mapas.")
        return

    df_chuva = pd.DataFrame(res_chuva).copy()

    df_chuva_com_coord = []
    for _, est in df_estacoes.iterrows():
        nome_est = formatar_nome_estacao(est["DC_NOME"])
        if nome_est in df_chuva["Estação"].values:
            chuva_row = df_chuva[df_chuva["Estação"] == nome_est]
            df_chuva_com_coord.append({
                "Estação": nome_est,
                "Chuva Período (mm)": chuva_row["Acumulado Período"].values[0],
                "Latitude": float(est["VL_LATITUDE"]),
                "Longitude": float(est["VL_LONGITUDE"])
            })

    if df_chuva_com_coord:
        df_chuva_plot = pd.DataFrame(df_chuva_com_coord)

        # CHUVA DO PERÍODO (apenas estações com chuva > 0)
        df_chuva_periodo = df_chuva_plot[df_chuva_plot["Chuva Período (mm)"] > 0].copy()
        if not df_chuva_periodo.empty:
            df_chuva_periodo = df_chuva_periodo[["Estação", "Chuva Período (mm)", "Latitude", "Longitude"]]
            df_chuva_periodo = df_chuva_periodo.rename(columns={"Chuva Período (mm)": "Chuva (mm)"})

            gerar_mapa_pontual(
                df_chuva_periodo,
                "Chuva (mm)",
                f"Chuva acumulada em {NUM_DIAS} dias - Mato Grosso do Sul",
                "Mapa_Chuva_Periodo_MS",
                "Blues",
                "Chuva (mm)",
                subtitulo,
                "5 MAIORES ACUMULADOS",
                "maiores"
            )

            gerar_mapa_interpolado(
                df_chuva_periodo,
                "Chuva (mm)",
                f"Chuva acumulada em {NUM_DIAS} dias - Mato Grosso do Sul",
                "Mapa_Chuva_Periodo_MS",
                "Blues",
                "Chuva (mm)",
                subtitulo,
                "5 MAIORES ACUMULADOS",
                "maiores"
            )
        else:
            print(f"⚠️ Não há chuva acumulada no período para gerar mapas")

def gerar_mapa_temperatura(res_temp_min, res_temp_max, subtitulo):
    """Gera mapas de Mato Grosso do Sul com valores de temperatura"""
    if not res_temp_min and not res_temp_max:
        print("⚠️ Não há dados de temperatura para gerar os mapas.")
        return

    # TEMPERATURA MÍNIMA
    if res_temp_min:
        df_temp_min = pd.DataFrame(res_temp_min).copy()

        df_temp_min_com_coord = []
        for _, est in df_estacoes.iterrows():
            nome_est = formatar_nome_estacao(est["DC_NOME"])
            if nome_est in df_temp_min["Estação"].values:
                df_temp_min_com_coord.append({
                    "Estação": nome_est,
                    "Temperatura Mínima (°C)": df_temp_min[df_temp_min["Estação"] == nome_est]["Temperatura Mínima (°C)"].values[0],
                    "Latitude": float(est["VL_LATITUDE"]),
                    "Longitude": float(est["VL_LONGITUDE"])
                })

        if df_temp_min_com_coord:
            df_temp_min_plot = pd.DataFrame(df_temp_min_com_coord)

            gerar_mapa_pontual(
                df_temp_min_plot,
                "Temperatura Mínima (°C)",
                "Temperatura mínima em Mato Grosso do Sul",
                "Mapa_Temp_Min_MS",
                "coolwarm",
                "Temperatura (°C)",
                subtitulo,
                "5 MENORES TEMPERATURAS",
                "menores"
            )

            gerar_mapa_interpolado(
                df_temp_min_plot,
                "Temperatura Mínima (°C)",
                "Temperatura mínima em Mato Grosso do Sul",
                "Mapa_Temp_Min_MS",
                "coolwarm",
                "Temperatura (°C)",
                subtitulo,
                "5 MENORES TEMPERATURAS",
                "menores"
            )

    # TEMPERATURA MÁXIMA
    if res_temp_max:
        df_temp_max = pd.DataFrame(res_temp_max).copy()

        df_temp_max_com_coord = []
        for _, est in df_estacoes.iterrows():
            nome_est = formatar_nome_estacao(est["DC_NOME"])
            if nome_est in df_temp_max["Estação"].values:
                df_temp_max_com_coord.append({
                    "Estação": nome_est,
                    "Temperatura Máxima (°C)": df_temp_max[df_temp_max["Estação"] == nome_est]["Temperatura Máxima (°C)"].values[0],
                    "Latitude": float(est["VL_LATITUDE"]),
                    "Longitude": float(est["VL_LONGITUDE"])
                })

        if df_temp_max_com_coord:
            df_temp_max_plot = pd.DataFrame(df_temp_max_com_coord)

            gerar_mapa_pontual(
                df_temp_max_plot,
                "Temperatura Máxima (°C)",
                "Temperatura máxima em Mato Grosso do Sul",
                "Mapa_Temp_Max_MS",
                "YlOrRd",
                "Temperatura (°C)",
                subtitulo,
                "5 MAIORES TEMPERATURAS",
                "maiores"
            )

            gerar_mapa_interpolado(
                df_temp_max_plot,
                "Temperatura Máxima (°C)",
                "Temperatura máxima em Mato Grosso do Sul",
                "Mapa_Temp_Max_MS",
                "YlOrRd",
                "Temperatura (°C)",
                subtitulo,
                "5 MAIORES TEMPERATURAS",
                "maiores"
            )

def gerar_mapa_rajadas(res_vento, subtitulo):
    """Gera mapas de rajadas de vento"""
    if not res_vento:
        print("⚠️ Não há dados de vento para gerar os mapas.")
        return

    df_vento = pd.DataFrame(res_vento).copy()

    for coluna in ["Latitude", "Longitude", "Rajada (km/h)", "Direção (°)"]:
        if coluna in df_vento.columns:
            df_vento[coluna] = pd.to_numeric(df_vento[coluna], errors="coerce")

    df_vento = df_vento.dropna(
        subset=["Latitude", "Longitude", "Rajada (km/h)"]
    )

    if df_vento.empty:
        print("⚠️ Não há coordenadas válidas para gerar os mapas de vento.")
        return

    df_vento_basico = df_vento[["Estação", "Rajada (km/h)", "Latitude", "Longitude"]].copy()

    gerar_mapa_pontual(
        df_vento_basico,
        "Rajada (km/h)",
        "Rajadas de vento em Mato Grosso do Sul",
        "Mapa_Rajadas_MS",
        "turbo",
        "Velocidade (km/h)",
        subtitulo,
        "5 MAIORES RAJADAS",
        "maiores"
    )

    gerar_mapa_interpolado(
        df_vento_basico,
        "Rajada (km/h)",
        "Rajadas de vento em Mato Grosso do Sul",
        "Mapa_Rajadas_Interpolado_MS",
        "turbo",
        "Velocidade (km/h)",
        subtitulo,
        "5 MAIORES RAJADAS",
        "maiores"
    )

# =====================================================
# PROCESSAMENTO
# =====================================================
print("=" * 60)
print("📊 RELATÓRIO DE PERÍODO INMET - MATO GROSSO DO SUL")
print("=" * 60)
print(f"📅 Período: {TEXTO_PERIODO}")
print(f"📅 Início: {DATA_INICIO.strftime('%d/%m/%Y %H:%M')} (UTC)")
print(f"📅 Fim:    {DATA_FINAL.strftime('%d/%m/%Y %H:%M')} (UTC)")
print("=" * 60)

if TOKEN_INMET == "SEU_TOKEN_AQUI":
    print("❌ ERRO: Você precisa substituir 'SEU_TOKEN_AQUI' pelo seu token válido do INMET!")
    print("   Para obter um token, acesse: https://portal.inmet.gov.br/paginas/cadastro")
    exit()

# Validação das datas
if DATA_FINAL < DATA_INICIO:
    print("❌ ERRO: A DATA_FINAL deve ser maior ou igual à DATA_INICIO!")
    exit()

try:
    estacoes_raw = requests.get("https://apitempo.inmet.gov.br/estacoes/T", timeout=20).json()
    df_estacoes = pd.DataFrame(estacoes_raw)
    df_estacoes = df_estacoes[df_estacoes["SG_ESTADO"] == UF]
    print(f"✅ Encontradas {len(df_estacoes)} estações em {UF}")
except Exception as e:
    print(f"❌ Erro ao listar estações: {e}")
    exit()

res_temp_min, res_temp_max = [], []
res_umid, res_vento, res_chuva = [], [], []

estacoes_com_dados = 0

for _, est in df_estacoes.iterrows():
    cod = est["CD_ESTACAO"]
    nome = formatar_nome_estacao(est["DC_NOME"])
    print(f"🛰️ Lendo: {nome}...")

    df_full = get_inmet_data(cod, DATA_INI, DATA_FIM)
    if df_full is None or df_full.empty:
        print(f"    ⚠️ Sem dados para esta estação")
        continue

    estacoes_com_dados += 1
    print(f"    📊 Total de registros no período: {len(df_full)}")

    # ---------- TEMPERATURA MÍNIMA DO PERÍODO ----------
    tn = df_full["TEM_MIN"].min()
    if not pd.isna(tn):
        idx_min = df_full["TEM_MIN"].idxmin()
        dt_min_utc = df_full.loc[idx_min, 'dt_utc']
        dt_min_local = df_full.loc[idx_min, 'dt_local']

        res_temp_min.append({
            "Estação": nome,
            "Temperatura Mínima (°C)": tn,
            "Data/Hora (UTC)": dt_min_utc.strftime('%d/%m/%Y %H:%M'),
            "Data/Hora (MS)": dt_min_local.strftime('%d/%m/%Y %H:%M')
        })

    # ---------- TEMPERATURA MÁXIMA DO PERÍODO ----------
    tx = df_full["TEM_MAX"].max()
    if not pd.isna(tx):
        idx_max = df_full["TEM_MAX"].idxmax()
        dt_max_utc = df_full.loc[idx_max, 'dt_utc']
        dt_max_local = df_full.loc[idx_max, 'dt_local']

        res_temp_max.append({
            "Estação": nome,
            "Temperatura Máxima (°C)": tx,
            "Data/Hora (UTC)": dt_max_utc.strftime('%d/%m/%Y %H:%M'),
            "Data/Hora (MS)": dt_max_local.strftime('%d/%m/%Y %H:%M')
        })

    # ---------- UMIDADE MÍNIMA DO PERÍODO ----------
    un = df_full["UMD_MIN"].min()
    if not pd.isna(un):
        res_umid.append({
            "Estação": nome,
            "Umidade Mín (%)": un
        })

    # ---------- RAJADA MÁXIMA DO PERÍODO ----------
    vx = df_full["VEN_RAJ"].max()
    if not pd.isna(vx):
        idx_rajada = df_full["VEN_RAJ"].idxmax()
        dir_vento = None
        if "VEN_DIR" in df_full.columns:
            dir_vento = df_full.loc[idx_rajada, "VEN_DIR"]
            if pd.isna(dir_vento):
                dir_vento = None

        res_vento.append({
            "Estação": nome,
            "Rajada (km/h)": round(vx * 3.6, 1),
            "Direção (°)": dir_vento,
            "Latitude": est["VL_LATITUDE"],
            "Longitude": est["VL_LONGITUDE"]
        })

    # ---------- CHUVA ACUMULADA NO PERÍODO ----------
    c_periodo = calcular_acumulado_periodo(df_full, DATA_INI, DATA_FIM)

    res_chuva.append({
        "Estação": nome,
        "Acumulado Período": c_periodo,
        "Latitude": est["VL_LATITUDE"],
        "Longitude": est["VL_LONGITUDE"]
    })

    time.sleep(0.1)

print(f"\n📊 Resumo do processamento:")
print(f"   - Estações com dados: {estacoes_com_dados}")
print(f"   - Registros de temperatura mínima: {len(res_temp_min)}")
print(f"   - Registros de temperatura máxima: {len(res_temp_max)}")
print(f"   - Registros de umidade: {len(res_umid)}")
print(f"   - Registros de vento: {len(res_vento)}")
print(f"   - Registros de chuva: {len(res_chuva)}")

# =====================================================
# GERAÇÃO DO EXCEL
# =====================================================
if any([res_temp_min, res_temp_max, res_umid, res_vento, res_chuva]):
    with pd.ExcelWriter(ARQUIVO_SAIDA, engine="openpyxl") as writer:
        if res_temp_min:
            pd.DataFrame(res_temp_min)\
                .sort_values("Temperatura Mínima (°C)")\
                .to_excel(writer, sheet_name="Temp_Min", index=False)

        if res_temp_max:
            pd.DataFrame(res_temp_max)\
                .sort_values("Temperatura Máxima (°C)", ascending=False)\
                .to_excel(writer, sheet_name="Temp_Max", index=False)

        if res_umid:
            pd.DataFrame(res_umid)\
                .sort_values("Umidade Mín (%)")\
                .to_excel(writer, sheet_name="Umidade", index=False)

        if res_vento:
            pd.DataFrame(res_vento)\
                .sort_values("Rajada (km/h)", ascending=False)\
                .to_excel(writer, sheet_name="Vento", index=False)

        if res_chuva:
            df_chuva = pd.DataFrame(res_chuva)
            df_chuva.sort_values("Acumulado Período", ascending=False)\
                .to_excel(writer, sheet_name="Chuva", index=False)

        header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)

        for sheet in writer.sheets:
            ws = writer.sheets[sheet]
            for cell in ws[1]:
                cell.fill, cell.font, cell.alignment = header_fill, header_font, Alignment(horizontal="center")
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = 25

    print(f"\n✅ Relatório Excel salvo em: {ARQUIVO_SAIDA}")
else:
    print("\n⚠️ Nenhum dado foi coletado. O arquivo Excel não será gerado.")

# =====================================================
# GERAÇÃO DOS MAPAS
# =====================================================
print("\n🗺️ Gerando mapas...")

SUBTITULO = f"Período: {TEXTO_PERIODO} (UTC)"

# Temperatura
gerar_mapa_temperatura(res_temp_min, res_temp_max, SUBTITULO)

# Umidade
gerar_mapa_umidade(res_umid, SUBTITULO)

# Chuva
gerar_mapa_chuva(res_chuva, SUBTITULO)

# Vento
gerar_mapa_rajadas(res_vento, SUBTITULO)

print("=" * 60)
print(f"✅ PROCESSAMENTO CONCLUÍDO PARA O PERÍODO")
print(f"📅 {TEXTO_PERIODO}")
print(f"📁 Todos os arquivos salvos em: {PASTA_SAIDA}")
print("=" * 60)