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

# Data/hora atual em UTC
agora_utc = datetime.now(FUSO_UTC)
data_ini_busca = agora_utc - timedelta(hours=96) 

PASTA_SAIDA = Path("saida")
PASTA_SAIDA.mkdir(exist_ok=True)

# Shapefiles usados no mapa de Mato Grosso do Sul
SHAPE_MUN = "MS_mun.shp"
SHAPE_UF = "MS_UF_2022.shp"

# Logos
LOGO_CEMTEC = "logo_cemtec.jpeg"
LOGO_SEMAGRO = "semagro_logo.jpeg"

# Usar UTC no nome do arquivo também
ARQUIVO_SAIDA = PASTA_SAIDA / f"Relatorio_Completo_{UF}_{agora_utc.strftime('%Y%m%d_%H%M')}_UTC.xlsx"

# =====================================================
# FUNÇÕES DE BUSCA E CÁLCULO
# =====================================================
def get_inmet_data(cod, data_ini, data_fim):
    """Busca dados horários do INMET e mantém TUDO em UTC"""
    url = f"https://apitempo.inmet.gov.br/token/estacao/{data_ini.strftime('%Y-%m-%d')}/{data_fim.strftime('%Y-%m-%d')}/{cod}/{TOKEN_INMET}"
    try:
        response = requests.get(url, timeout=30)
        
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
    return None

def calcular_acumulado(df, horas):
    """Soma a chuva das últimas X horas retroativas ao momento atual (TUDO em UTC)"""
    limite_utc = agora_utc - timedelta(hours=horas)
    sub_df = df[df['dt_utc'] >= limite_utc]
    return round(sub_df["CHUVA"].fillna(0).sum(), 1) if not sub_df.empty else 0.0

def calcular_chuva_hoje(df):
    """Soma a chuva desde as 00:00 UTC do dia atual"""
    inicio_dia_utc = agora_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    sub_df = df[df['dt_utc'] >= inicio_dia_utc]
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
    """Adiciona os logos do CEMTEC e SEMAGRO dentro da área do mapa, canto superior direito"""
    try:
        # Limites do mapa
        LON_MIN, LON_MAX = -58.5, -50.5
        LAT_MIN, LAT_MAX = -24.5, -17.0
        
        # =================================================
        # LOGO SEMAGRO - dentro do mapa, canto superior direito
        # =================================================
        if Path(LOGO_SEMAGRO).exists():
            im_semagro = plt.imread(LOGO_SEMAGRO)
            
            imagebox_semagro = OffsetImage(
                im_semagro,
                zoom=0.14
            )
            
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
        
        # =================================================
        # LOGO CEMTEC - dentro do mapa, canto superior direito
        # =================================================
        if Path(LOGO_CEMTEC).exists():
            im_cemtec = plt.imread(LOGO_CEMTEC)
            
            imagebox_cemtec = OffsetImage(
                im_cemtec,
                zoom=0.12
            )
            
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
        import traceback
        traceback.print_exc()

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

def gerar_mapa_pontual(df_dados, coluna_valor, titulo, nome_arquivo, cmap, unidade, agora_utc, ranking_titulo=None, ranking_ordem="maiores"):
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
    
    inicio_24h = agora_utc - timedelta(hours=24)
    titulo_completo = (
        f"{titulo}\n"
        f"Últimas 24 horas — dados INMET\n"
        f"{inicio_24h.strftime('%d/%m/%Y %H:%M UTC')} até "
        f"{agora_utc.strftime('%d/%m/%Y %H:%M UTC')}"
    )
    
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
    
    saida_mapa = PASTA_SAIDA / f"{nome_arquivo}_{agora_utc.strftime('%Y%m%d_%H%M')}_UTC.png"
    plt.savefig(saida_mapa, dpi=300, bbox_inches="tight", facecolor='white', edgecolor='none')
    plt.close()
    
    print(f"🗺️ Mapa pontual salvo em: {saida_mapa}")

def gerar_mapa_interpolado(df_dados, coluna_valor, titulo, nome_arquivo, cmap, unidade, agora_utc, ranking_titulo=None, ranking_ordem="maiores"):
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
    
    inicio_24h = agora_utc - timedelta(hours=24)
    titulo_completo = (
        f"{titulo}\n"
        f"{inicio_24h.strftime('%d/%m/%Y %H:%M UTC')} até "
        f"{agora_utc.strftime('%d/%m/%Y %H:%M UTC')}"
    )
    
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
    
    saida_mapa = PASTA_SAIDA / f"{nome_arquivo}_{agora_utc.strftime('%Y%m%d_%H%M')}_UTC_interpolado.png"
    plt.savefig(saida_mapa, dpi=300, bbox_inches="tight", facecolor='white', edgecolor='none')
    plt.close()
    
    print(f"🗺️ Mapa interpolado salvo em: {saida_mapa}")

def gerar_mapa_interpolado_com_vento(df_dados, coluna_valor, titulo, nome_arquivo, cmap, unidade, agora_utc, ranking_titulo=None, ranking_ordem="maiores", df_vento_direcao=None):
    """Gera mapa interpolado com vetores de direção do vento sobrepostos"""
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
    
    # Sobrepôr vetores de direção do vento
    if df_vento_direcao is not None and not df_vento_direcao.empty:
        gdf_vento = gpd.GeoDataFrame(
            df_vento_direcao,
            geometry=gpd.points_from_xy(
                df_vento_direcao["Longitude"],
                df_vento_direcao["Latitude"]
            ),
            crs="EPSG:4326"
        )
        
        gdf_vento_valid = gdf_vento[gdf_vento["Direção (°)"].notna()].copy()
        
        for _, row in gdf_vento_valid.iterrows():
            angulo_rad = np.radians(row["Direção (°)"])
            
            u = -np.sin(angulo_rad)
            v = -np.cos(angulo_rad)
            
            magnitude = np.sqrt(u**2 + v**2)
            if magnitude > 0:
                u = u / magnitude
                v = v / magnitude
            
            comprimento_base = 0.4
            fator_velocidade = min(row["Rajada (km/h)"] / 50.0, 1.5)
            comprimento_seta = comprimento_base * fator_velocidade
            
            ax.arrow(
                row.geometry.x,
                row.geometry.y,
                u * comprimento_seta,
                v * comprimento_seta,
                head_width=0.08,
                head_length=0.12,
                fc='red',
                ec='red',
                linewidth=2,
                alpha=0.8,
                zorder=6
            )
        
        legend_elements = [
            Line2D([0], [0], color='red', linewidth=2, marker='>', markersize=10,
                   label='Direção do vento (comprimento ∝ velocidade)', alpha=0.8)
        ]
        ax.legend(handles=legend_elements, loc='lower left', fontsize=10)
    
    # Adicionar valores nos pontos (SEM símbolo de seta)
    for _, row in gdf.iterrows():
        texto_valor = f'{row[coluna_valor]:.1f}'
        
        if df_vento_direcao is not None and not df_vento_direcao.empty:
            vento_row = df_vento_direcao[df_vento_direcao["Estação"] == row["Estação"]]
            if not vento_row.empty and "Direção (°)" in vento_row.columns:
                direcao = vento_row["Direção (°)"].values[0]
                if not pd.isna(direcao):
                    texto_valor = f'{row[coluna_valor]:.1f}\n{direcao:.0f}°'
        
        ax.text(
            row.geometry.x,
            row.geometry.y,
            texto_valor,
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
            ),
            zorder=7
        )
    
    inicio_24h = agora_utc - timedelta(hours=24)
    titulo_completo = (
        f"{titulo}\n"
        f"{inicio_24h.strftime('%d/%m/%Y %H:%M UTC')} até "
        f"{agora_utc.strftime('%d/%m/%Y %H:%M UTC')}"
    )
    
    configurar_eixo_mapa(ax, titulo_completo)
    
    if ranking_titulo and len(gdf) > 0:
        if ranking_ordem == "maiores":
            ranking = gdf.nlargest(5, coluna_valor)
        else:
            ranking = gdf.nsmallest(5, coluna_valor)
        
        texto_ranking = f"{ranking_titulo}:\n"
        for _, row in ranking.iterrows():
            texto_linha = f"{row['Estação']}: {row[coluna_valor]:.1f}"
            
            if df_vento_direcao is not None and not df_vento_direcao.empty:
                vento_row = df_vento_direcao[df_vento_direcao["Estação"] == row["Estação"]]
                if not vento_row.empty and "Direção (°)" in vento_row.columns:
                    direcao = vento_row["Direção (°)"].values[0]
                    if not pd.isna(direcao):
                        texto_linha += f" ({direcao:.0f}°)"
            
            texto_ranking += texto_linha + "\n"
        
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
    
    saida_mapa = PASTA_SAIDA / f"{nome_arquivo}_{agora_utc.strftime('%Y%m%d_%H%M')}_UTC_interpolado_vento.png"
    plt.savefig(saida_mapa, dpi=300, bbox_inches="tight", facecolor='white', edgecolor='none')
    plt.close()
    
    print(f"🗺️ Mapa interpolado com vento salvo em: {saida_mapa}")

# =====================================================
# FUNÇÕES ESPECÍFICAS PARA CADA VARIÁVEL
# =====================================================
def gerar_mapa_umidade(res_umid, agora_utc):
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
            agora_utc,
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
            agora_utc,
            "5 MENORES UMIDADES",
            "menores"
        )

def gerar_mapa_chuva(res_chuva, agora_utc):
    """Gera mapas pontuais e interpolados de chuva acumulada (24h e 48h)"""
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
                "Chuva 24h (mm)": chuva_row["Acumulado 24h"].values[0],
                "Chuva 48h (mm)": chuva_row["Acumulado 48h"].values[0],
                "Latitude": float(est["VL_LATITUDE"]),
                "Longitude": float(est["VL_LONGITUDE"])
            })
    
    if df_chuva_com_coord:
        df_chuva_plot = pd.DataFrame(df_chuva_com_coord)
        
        # CHUVA 24 HORAS
        df_chuva_24h = df_chuva_plot[df_chuva_plot["Chuva 24h (mm)"] > 0].copy()
        if not df_chuva_24h.empty:
            df_chuva_24h = df_chuva_24h[["Estação", "Chuva 24h (mm)", "Latitude", "Longitude"]]
            df_chuva_24h = df_chuva_24h.rename(columns={"Chuva 24h (mm)": "Chuva (mm)"})
            
            gerar_mapa_pontual(
                df_chuva_24h,
                "Chuva (mm)",
                "Chuva acumulada em 24 horas - Mato Grosso do Sul",
                "Mapa_Chuva_24h_MS",
                "Blues",
                "Chuva (mm)",
                agora_utc,
                "5 MAIORES ACUMULADOS",
                "maiores"
            )
            
            gerar_mapa_interpolado(
                df_chuva_24h,
                "Chuva (mm)",
                "Chuva acumulada em 24 horas - Mato Grosso do Sul",
                "Mapa_Chuva_24h_MS",
                "Blues",
                "Chuva (mm)",
                agora_utc,
                "5 MAIORES ACUMULADOS",
                "maiores"
            )
        else:
            print("⚠️ Não há chuva acumulada em 24h para gerar mapas")
        
        # CHUVA 48 HORAS
        df_chuva_48h = df_chuva_plot[df_chuva_plot["Chuva 48h (mm)"] > 0].copy()
        if not df_chuva_48h.empty:
            df_chuva_48h = df_chuva_48h[["Estação", "Chuva 48h (mm)", "Latitude", "Longitude"]]
            df_chuva_48h = df_chuva_48h.rename(columns={"Chuva 48h (mm)": "Chuva (mm)"})
            
            gerar_mapa_pontual(
                df_chuva_48h,
                "Chuva (mm)",
                "Chuva acumulada em 48 horas - Mato Grosso do Sul",
                "Mapa_Chuva_48h_MS",
                "Purples",
                "Chuva (mm)",
                agora_utc,
                "5 MAIORES ACUMULADOS",
                "maiores"
            )
            
            gerar_mapa_interpolado(
                df_chuva_48h,
                "Chuva (mm)",
                "Chuva acumulada em 48 horas - Mato Grosso do Sul",
                "Mapa_Chuva_48h_MS",
                "Purples",
                "Chuva (mm)",
                agora_utc,
                "5 MAIORES ACUMULADOS",
                "maiores"
            )
        else:
            print("⚠️ Não há chuva acumulada em 48h para gerar mapas")

def gerar_mapa_temperatura(res_temp_min, res_temp_max, agora_utc):
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
                agora_utc,
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
                agora_utc,
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
                agora_utc,
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
                agora_utc,
                "5 MAIORES TEMPERATURAS",
                "maiores"
            )

def gerar_mapa_rajadas(res_vento, agora_utc):
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

    # DataFrame básico sem direção
    df_vento_basico = df_vento[["Estação", "Rajada (km/h)", "Latitude", "Longitude"]].copy()
    
    # Mapa pontual normal
    gerar_mapa_pontual(
        df_vento_basico,
        "Rajada (km/h)",
        "Rajadas de vento em Mato Grosso do Sul",
        "Mapa_Rajadas_MS",
        "turbo",
        "Velocidade (km/h)",
        agora_utc,
        "5 MAIORES RAJADAS",
        "maiores"
    )
    
    # Mapa interpolado normal (sem vetores)
    gerar_mapa_interpolado(
        df_vento_basico,
        "Rajada (km/h)",
        "Rajadas de vento em Mato Grosso do Sul",
        "Mapa_Rajadas_Interpolado_MS",
        "turbo",
        "Velocidade (km/h)",
        agora_utc,
        "5 MAIORES RAJADAS",
        "maiores"
    )
    
    # Mapa interpolado com vetores de direção (se houver dados de direção)
    if "Direção (°)" in df_vento.columns:
        gerar_mapa_interpolado_com_vento(
            df_vento_basico,
            "Rajada (km/h)",
            "Rajadas de vento com direção em Mato Grosso do Sul",
            "Mapa_Rajadas_Direcao_MS",
            "turbo",
            "Velocidade (km/h)",
            agora_utc,
            "5 MAIORES RAJADAS",
            "maiores",
            df_vento
        )

# =====================================================
# PROCESSAMENTO
# =====================================================
print(f"🚀 Iniciando Relatório - {agora_utc.strftime('%d/%m/%Y %H:%M')} (UTC)")
print(f"📅 Horário local MS: {agora_utc.astimezone(FUSO_MS).strftime('%d/%m/%Y %H:%M')}")
print(f"📊 Período de busca: {data_ini_busca.strftime('%Y-%m-%d %H:%M')} até {agora_utc.strftime('%Y-%m-%d %H:%M')} (UTC)")

if TOKEN_INMET == "SEU_TOKEN_AQUI":
    print("❌ ERRO: Você precisa substituir 'SEU_TOKEN_AQUI' pelo seu token válido do INMET!")
    print("   Para obter um token, acesse: https://portal.inmet.gov.br/paginas/cadastro")
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

    df_full = get_inmet_data(cod, data_ini_busca, agora_utc)
    if df_full is None or df_full.empty:
        print(f"    ⚠️ Sem dados para esta estação")
        continue
    
    estacoes_com_dados += 1

    inicio_dia_utc = agora_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    
    df_hoje_utc = df_full[df_full['dt_utc'] >= inicio_dia_utc]
    
    limite_24h_utc = agora_utc - timedelta(hours=24)
    df_24h = df_full[df_full['dt_utc'] >= limite_24h_utc]

    print(f"    📊 Total de registros: {len(df_full)} | Hoje (UTC): {len(df_hoje_utc)} | Últimas 24h: {len(df_24h)}")

    if not df_hoje_utc.empty:
        tn = df_hoje_utc["TEM_MIN"].min()
        if not pd.isna(tn):
            idx_min = df_hoje_utc["TEM_MIN"].idxmin()
            hora_utc = df_hoje_utc.loc[idx_min, 'dt_utc'].strftime('%H:%M')
            hora_local = df_hoje_utc.loc[idx_min, 'dt_local'].strftime('%H:%M')
            
            res_temp_min.append({
                "Estação": nome,
                "Temperatura Mínima (°C)": tn,
                "Hora (UTC)": hora_utc,
                "Hora (MS)": hora_local
            })

    if not df_24h.empty:
        
        tx = df_24h["TEM_MAX"].max()
        if not pd.isna(tx):
            res_temp_max.append({
                "Estação": nome,
                "Temperatura Máxima (°C)": tx
            })
            
        un = df_24h["UMD_MIN"].min()
        if not pd.isna(un): 
            res_umid.append({"Estação": nome, "Umidade Mín (%)": un})
            
        vx = df_24h["VEN_RAJ"].max()
        if not pd.isna(vx):
            # Pegar a direção do vento no momento da rajada máxima
            idx_rajada = df_24h["VEN_RAJ"].idxmax()
            dir_vento = None
            if "VEN_DIR" in df_24h.columns:
                dir_vento = df_24h.loc[idx_rajada, "VEN_DIR"]
                if pd.isna(dir_vento):
                    dir_vento = None
            
            res_vento.append({
                "Estação": nome,
                "Rajada (km/h)": round(vx * 3.6, 1),
                "Direção (°)": dir_vento,
                "Latitude": est["VL_LATITUDE"],
                "Longitude": est["VL_LONGITUDE"]
            })

    c_hoje = calcular_chuva_hoje(df_full)
    c12 = calcular_acumulado(df_full, 12)
    c24 = calcular_acumulado(df_full, 24)
    c48 = calcular_acumulado(df_full, 48)
    c72 = calcular_acumulado(df_full, 72)

    res_chuva.append({
        "Estação": nome,
        "Chuva Hoje (desde 00h UTC)": c_hoje,
        "Acumulado 12h": c12,
        "Acumulado 24h": c24,
        "Acumulado 48h": c48,
        "Acumulado 72h": c72,
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
# GERAÇÃO DO EXCEL (apenas se houver dados)
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
            df_chuva.sort_values("Acumulado 24h", ascending=False)\
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

# Temperatura
gerar_mapa_temperatura(res_temp_min, res_temp_max, agora_utc)

# Umidade
gerar_mapa_umidade(res_umid, agora_utc)

# Chuva (24h e 48h)
gerar_mapa_chuva(res_chuva, agora_utc)

# Vento
gerar_mapa_rajadas(res_vento, agora_utc)

print(f"\n📊 Todas as datas/horas estão em UTC (com referência local MS)")
print(f"✅ Mapas gerados com sucesso!")
print(f"   - Temperatura mínima (pontual e interpolado)")
print(f"   - Temperatura máxima (pontual e interpolado)")
print(f"   - Umidade relativa mínima (pontual e interpolado)")
print(f"   - Chuva acumulada 24h (pontual e interpolado)")
print(f"   - Chuva acumulada 48h (pontual e interpolado)")
print(f"   - Rajadas de vento (pontual, interpolado e interpolado com direção)")