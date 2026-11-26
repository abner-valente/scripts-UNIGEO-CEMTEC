"""Os serviços de fora no mapa de camadas: o que cada um é, como vira camada e como se mostra.

Cada serviço é uma camada de pontos de um ArcGIS Feature Service (modulos/arcgis.py), descrita
aqui: o endereço, o papel de cada campo (o tipo, que escolhe o ícone; a descrição; a data da
observação), o ícone de cada tipo e os campos proibidos, que nunca são pedidos ao servidor. Pôr
outro serviço no mapa é acrescentar um item a SERVICOS.

A ficha de um ponto traz o tempo na hora em que ele foi observado: a estação do INMET mais
próxima, o pluviômetro do CEMADEN mais próximo que tenha leitura e os raios em volta. A carga
desses dados fica no explorador; aqui ficam as contas e o desenho. Sem tela.
"""
import base64
import html
import io
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pydeck as pdk
from PIL import Image, ImageOps

from app import camadas
from modulos import calculos, config

# Os raios em volta do ponto, nas horas antes da observação
RAIO_DOS_RAIOS_KM = 20
HORAS_DOS_RAIOS = 3
# O pluviômetro mais próximo pode estar parado: tenta-se este tanto de pluviômetros, do mais perto
# para o mais longe, até um com leitura. Mais longe que o limite, ele não diz nada do lugar (o de Bela
# Vista saía na ficha de um relato de Porto Murtinho, a 145 km): a ficha diz que não há nenhum
# (limite combinado com a equipe em 26/11/2026)
PLUVIOMETROS_TENTADOS = 3
DISTANCIA_MAXIMA_DO_PLUVIOMETRO_KM = 50
LADO_DA_FOTO = 900
TAMANHO_DO_ICONE = 30


@dataclass(frozen=True)
class Servico:
    chave: str                 # o id da camada no mapa
    nome: str                  # o nome no quadro de camadas
    titulo_da_ficha: str
    url: str                   # a camada do Feature Service (.../FeatureServer/0)
    credito: str
    campo_do_tipo: str         # um campo de domínio: o nome por extenso vem do próprio serviço
    campo_da_descricao: str
    campo_da_observacao: str   # data (milissegundos, UTC): quando a pessoa viu
    campos_da_atualizacao: tuple[str, ...]   # a última edição e, sem ela, a criação
    proibidos: tuple[str, ...]   # dado pessoal: nunca pedido ao servidor nem mostrado
    icones: tuple[tuple[str, str, str], ...]   # (código do tipo, cor, desenho SVG em branco)
    tipo_padrao: str           # o ícone de um tipo que não está na lista
    id_do_ponto: str = "objectid"
    plural: str = "pontos"     # sob o mapa: "10 relatos do CICOE"

    @property
    def campos(self) -> tuple[str, ...]:
        return (self.campo_do_tipo, self.campo_da_descricao, self.campo_da_observacao, *self.campos_da_atualizacao)


# Os relatos do formulário do CICOE (Survey123) no PIN MS, o portal de mapas do estado: item
# 98c3cbc9b1e14f5a803e67f91c45ec2f, público. O contato de quem respondeu e quem criou ou editou o
# registro são dados pessoais (decisão de 25/11/2026). Os ícones são nossos: o símbolo do Experience
# Builder vem do aplicativo dele, e a camada, sozinha, desenha um ponto simples
CICOE = Servico(
    chave="cicoe", nome="Relatos do CICOE (PIN MS)", titulo_da_ficha="Relato do CICOE",
    url=("https://www.pinms.ms.gov.br/arcgis/rest/services/Hosted/"
         "survey123_9c174f65f50e4ff3b4592407471ee4ce_results/FeatureServer/0"),
    credito="CICOE, PIN MS", campo_do_tipo="o_que_aconteceu", campo_da_descricao="descricao_acontecimento",
    campo_da_observacao="quando_voce_observou_a_situacao", campos_da_atualizacao=("last_edited_date", "created_date"),
    proibidos=("informacoes_contato", "created_user", "last_edited_user"),
    icones=(
        ("ALAGAMENTO_INUNDACAO", "#1e88e5",
         "<path d='M14 27 q6 -7 12 0 t12 0 t12 0' stroke='white' stroke-width='5' fill='none' "
         "stroke-linecap='round'/><path d='M14 40 q6 -7 12 0 t12 0 t12 0' stroke='white' stroke-width='5' "
         "fill='none' stroke-linecap='round'/>"),
        ("PONTE_DANIFICADA_DESTRUIDA", "#00897b",
         "<path d='M12 42 H52 M17 42 V36 A15 11 0 0 1 47 36 V42' stroke='white' stroke-width='5' fill='none' "
         "stroke-linecap='round'/><path d='M30 22 L34 30 L29 34' stroke='white' stroke-width='3' fill='none'/>"),
        ("RODOVIA_INTERDITADA", "#e53935", "<rect x='15' y='27' width='34' height='10' rx='2' fill='white'/>"),
        ("QUEDA_ARVORES", "#2e7d32",
         "<path d='M32 13 L47 38 H17 Z' fill='white'/><rect x='29' y='38' width='6' height='11' fill='white'/>"),
        ("VENDAVAL_GRANIZO", "#5e35b1",
         "<path d='M14 24 H38 A6 6 0 1 0 32 18 M14 33 H48 M14 42 H36 A6 6 0 1 1 30 48' stroke='white' "
         "stroke-width='4.5' fill='none' stroke-linecap='round'/>"),
        ("DESLIZAMENTO_TERRA", "#6d4c41",
         "<path d='M12 47 L29 19 L52 47 Z' fill='white'/><circle cx='44' cy='24' r='4' fill='white'/>"),
        ("INCENDIO", "#f4511e",
         "<path d='M32 11 C41 23 47 30 45 40 A13 13 0 0 1 19 40 C17 31 23 27 26 21 C28 28 30 30 32 30 C35 24 33 17 "
         "32 11 Z' fill='white'/>"),
        ("OUTRA_OCORRENCIA", "#616161",
         "<rect x='28' y='13' width='8' height='25' rx='3' fill='white'/><circle cx='32' cy='47' r='5' fill='white'/>"),
    ),
    tipo_padrao="OUTRA_OCORRENCIA", plural="relatos do CICOE",
)
SERVICOS = (CICOE,)
COLUNAS_DOS_PONTOS = ["objectid", "longitude", "latitude", "tipo", "titulo", "detalhe", "quando", "referencia",
                      "descricao", "atualizado", "icone"]
# O que vai ao navegador: a descrição e as datas só aparecem na ficha
COLUNAS_DA_CAMADA = ["objectid", "longitude", "latitude", "titulo", "detalhe", "icone"]


def por_chave(chave: str, servicos: tuple[Servico, ...] = SERVICOS) -> Servico:
    return next(servico for servico in servicos if servico.chave == chave)


def camadas_dos_servicos(servicos: tuple[Servico, ...] = SERVICOS) -> tuple[camadas.Camada, ...]:
    return tuple(camadas.Camada(servico.chave, servico.nome) for servico in servicos)


def cor_e_desenho(servico: Servico, tipo: str) -> tuple[str, str]:
    por_tipo = {codigo: (cor, desenho) for codigo, cor, desenho in servico.icones}
    return por_tipo.get(tipo, por_tipo[servico.tipo_padrao])


def icone_svg(servico: Servico, tipo: str) -> str:
    cor, desenho = cor_e_desenho(servico, tipo)
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>"
            f"<circle cx='32' cy='32' r='28' fill='{cor}' stroke='white' stroke-width='5'/>{desenho}</svg>")


def icone_uri(servico: Servico, tipo: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(icone_svg(servico, tipo).encode("utf-8")).decode("ascii")


def data_local(milissegundos, fuso) -> pd.Timestamp | None:
    if milissegundos is None or (isinstance(milissegundos, float) and np.isnan(milissegundos)) or milissegundos == "":
        return None
    return pd.Timestamp(milissegundos, unit="ms", tz="UTC").tz_convert(fuso)


def pontos_do_servico(servico: Servico, pontos: list[dict], nomes_dos_tipos: dict[str, str], fuso) -> pd.DataFrame:
    # Uma linha por ponto (de arcgis.pontos), do mais antigo ao mais novo: o mais novo fica por cima
    # no mapa. "detalhe" é o balão; "quando" vai em ISO, para ser chave de cache. "referencia" é o
    # momento que a janela de tempo filtra (veja desde): a observação, e sem ela o registro
    linhas = []
    for ponto in pontos:
        campos = ponto["campos"]
        tipo = str(campos.get(servico.campo_do_tipo) or servico.tipo_padrao)
        quando = data_local(campos.get(servico.campo_da_observacao), fuso)
        atualizado = next((data for data in (data_local(campos.get(campo), fuso)
                                             for campo in servico.campos_da_atualizacao) if data is not None), None)
        linhas.append({
            "objectid": int(campos[servico.id_do_ponto]), "longitude": ponto["lon"], "latitude": ponto["lat"],
            "tipo": tipo,
            "titulo": nomes_dos_tipos.get(tipo, tipo.replace("_", " ").capitalize()),
            "detalhe": (f"Observado em {quando:%d/%m/%Y %H:%M}" if quando is not None else "Sem a data da observação")
                       + f"\nClique para abrir a ficha. Fonte: {servico.credito}",
            "quando": quando.isoformat() if quando is not None else "",
            "referencia": next((data.isoformat() for data in (quando, atualizado) if data is not None), ""),
            "descricao": str(campos.get(servico.campo_da_descricao) or "").strip(),
            "atualizado": f"{atualizado:%d/%m/%Y %H:%M}" if atualizado is not None else "",
            "icone": {"url": icone_uri(servico, tipo), "width": 64, "height": 64, "anchorY": 32},
        })
    tabela = pd.DataFrame(linhas, columns=COLUNAS_DOS_PONTOS)
    return tabela.sort_values("referencia", kind="stable").reset_index(drop=True)


def desde(pontos: pd.DataFrame, inicio) -> pd.DataFrame:
    # Os pontos com o momento de referência a partir de `inicio` (a janela de tempo da página, que
    # conta para trás a partir de agora). Ponto sem data nenhuma fica de fora; com a data no futuro
    # (o relógio do celular adiantado), fica
    if pontos.empty:
        return pontos
    referencia = pd.to_datetime(pontos["referencia"].replace("", None), utc=True, format="ISO8601")
    return pontos[(referencia >= pd.Timestamp(inicio)).to_numpy()].reset_index(drop=True)


def camada(servico: Servico, pontos: pd.DataFrame, opacidade: float | None = None) -> pdk.Layer:
    # Um ícone por tipo, que o deck.gl junta num atlas no navegador
    extras = {} if opacidade is None else {"opacity": opacidade}
    return pdk.Layer("IconLayer", data=pontos[COLUNAS_DA_CAMADA], id=servico.chave,
                     get_position=["longitude", "latitude"], get_icon="icone", get_size=TAMANHO_DO_ICONE,
                     pickable=True, **extras)


def legenda_dos_tipos(servico: Servico, nomes_dos_tipos: dict[str, str]) -> str:
    linhas = "".join(f'<span style="display:flex;align-items:center;margin-top:0.25rem">'
                     f'<img src="{icone_uri(servico, codigo)}" style="width:1.2rem;height:1.2rem;margin-right:0.4rem">'
                     f'{html.escape(nomes_dos_tipos.get(codigo, codigo))}</span>' for codigo, _, _ in servico.icones)
    return (f'<div style="font-size:0.85rem;margin-bottom:0.6rem"><b>{html.escape(servico.nome)}</b>'
            f'{linhas}</div>')


def foto_sem_metadados(bruto: bytes | None, lado: int = LADO_DA_FOTO) -> bytes | None:
    # Endireitada pelo EXIF, reduzida e gravada de novo: sai sem os metadados da câmera (o GPS, o
    # aparelho). Um anexo que não é imagem que o Pillow leia fica de fora
    if not bruto:
        return None
    try:
        imagem = ImageOps.exif_transpose(Image.open(io.BytesIO(bruto))).convert("RGB")
    except (OSError, ValueError):
        return None
    imagem.thumbnail((lado, lado))
    saida = io.BytesIO()
    imagem.save(saida, format="JPEG", quality=85)
    return saida.getvalue()


# =====================================================
# O TEMPO NA HORA DA OBSERVAÇÃO
# =====================================================
def mais_perto(cadastro: pd.DataFrame, lon: float, lat: float, quantos: int = 1) -> list[tuple[pd.Series, float]]:
    # As estações do cadastro (com VL_LONGITUDE e VL_LATITUDE), da mais perto para a mais longe
    validas = cadastro.dropna(subset=["VL_LATITUDE", "VL_LONGITUDE"])
    if validas.empty:
        return []
    distancias = calculos.distancia_km(lon, lat, validas["VL_LONGITUDE"].astype(float),
                                       validas["VL_LATITUDE"].astype(float))
    return [(validas.iloc[int(posicao)], float(distancias[int(posicao)]))
            for posicao in np.argsort(distancias, kind="stable")[:quantos]]


def pluviometros_candidatos(cadastro: pd.DataFrame, lon: float, lat: float) -> list[tuple[pd.Series, float]]:
    # Os que a ficha tenta, do mais perto para o mais longe, só até o limite de distância
    return [(pluviometro, km) for pluviometro, km in mais_perto(cadastro, lon, lat, PLUVIOMETROS_TENTADOS)
            if km <= DISTANCIA_MAXIMA_DO_PLUVIOMETRO_KM]


def hora_da_leitura(quando: str, fuso) -> pd.Timestamp:
    # A leitura que cobre a observação: a das 14:00 é a hora das 13:00 às 14:00
    return pd.Timestamp(quando).tz_convert(fuso).ceil("h")


def chuva_ate(leituras: pd.DataFrame, hora: pd.Timestamp, horas: int, coluna: str = "CHUVA") -> float | None:
    if leituras.empty or coluna not in leituras:
        return None
    janela = leituras[(leituras["dt_local"] > hora - pd.Timedelta(hours=horas)) & (leituras["dt_local"] <= hora)]
    soma = janela[coluna].sum(min_count=1)
    return None if pd.isna(soma) else float(soma)


def leitura_da_hora(leituras: pd.DataFrame, hora: pd.Timestamp) -> pd.Series | None:
    # A última leitura com temperatura até a hora: se a da hora faltou, a anterior
    if leituras.empty or "TEM_INS" not in leituras:
        return None
    medidas = leituras[(leituras["dt_local"] <= hora) & leituras["TEM_INS"].notna()]
    return None if medidas.empty else medidas.sort_values("dt_local").iloc[-1]


def chuva_no_local(pontos: pd.DataFrame, lon: float, lat: float, centro: tuple[float, float]) -> dict:
    # A chuva interpolada no ponto do relato, na hora e nas 24 h antes, pelo mesmo IDW que pinta o mapa:
    # `pontos` são as estações (o estado, as vizinhas e os pluviômetros do CEMADEN) com Longitude,
    # Latitude, chuva_hora e chuva_24h, sem valor onde a estação não mediu; com o `centro` do recorte, o
    # número bate com a cor da superfície. Com menos de MIN_ESTACOES_INTERPOLACAO que mediram, fica sem
    # valor, como no mapa
    saida: dict = {}
    for coluna in ("chuva_hora", "chuva_24h"):
        mediram = pontos.dropna(subset=[coluna])
        saida[f"{coluna}_estacoes"] = len(mediram)
        if len(mediram) < config.MIN_ESTACOES_INTERPOLACAO:
            saida[coluna] = None
            continue
        saida[coluna] = float(calculos.interpolar_idw(
            mediram["Longitude"].to_numpy(dtype=float), mediram["Latitude"].to_numpy(dtype=float),
            mediram[coluna].to_numpy(dtype=float), np.array([float(lon)]), np.array([float(lat)]),
            config.IDW_VIZINHOS, config.IDW_POTENCIA, centro=centro)[0])
    return saida


def flashes_em_volta(celulas: pd.DataFrame, lon: float, lat: float, raio_km: float = RAIO_DOS_RAIOS_KM) -> int:
    # Os flashes das células de 0,1° (raios.densidade) com o centro a até raio_km do ponto
    if celulas.empty:
        return 0
    distancias = calculos.distancia_km(lon, lat, celulas["Longitude"], celulas["Latitude"])
    return int(celulas.loc[distancias <= raio_km, "flashes"].sum())


ESTILO_DA_FICHA = """
<style>
.ficha {border:1px solid rgba(127,127,127,.35); border-radius:10px; overflow:hidden; font-size:0.9rem;}
.ficha .topo {padding:0.8rem 1rem; background:rgba(0,137,123,.18);}
.ficha .rotulo {font-size:0.68rem; letter-spacing:0.08em; text-transform:uppercase; opacity:0.75; font-weight:700;}
.ficha .titulo {font-size:1.15rem; font-weight:700; margin-top:0.15rem;}
.ficha .bloco {padding:0.7rem 1rem; border-top:1px solid rgba(127,127,127,.25);}
.ficha .etiqueta {display:inline-block; color:white; font-weight:700; padding:0.3rem 0.7rem; border-radius:6px;
                  margin-top:0.35rem;}
.ficha .quando {border-left:4px solid #00897b; padding:0.4rem 0.7rem; background:rgba(127,127,127,.08);
                border-radius:4px; margin-top:0.35rem; font-weight:700;}
.ficha .miudo {font-size:0.78rem; opacity:0.8;}
.tempo {display:grid; grid-template-columns:repeat(auto-fill, minmax(6.2rem, 1fr)); gap:0.4rem; margin-top:0.4rem;}
.tempo div {border-left:3px solid var(--cor); background:rgba(127,127,127,.08); padding:0.3rem 0.5rem;
            border-radius:4px;}
.tempo span {display:block; font-size:0.72rem; opacity:0.8;}
.tempo b {font-size:1rem;}
</style>
"""


def _numero(valor, casas: int = 1, unidade: str = "") -> str:
    if valor is None or pd.isna(valor):
        return "—"
    return f"{float(valor):.{casas}f}".replace(".", ",") + (f" {unidade}" if unidade else "")


def _bloco_do_tempo(condicoes: dict) -> list[str]:
    partes = ['<div class="bloco"><div class="rotulo">Condições meteorológicas na hora da observação</div>']
    no_local = condicoes.get("no_local")
    if no_local and (no_local.get("chuva_hora") is not None or no_local.get("chuva_24h") is not None):
        estacoes = no_local.get("chuva_24h_estacoes", 0)
        partes.append(
            f'<div class="miudo" style="margin-top:0.3rem"><b>No local do relato</b>, interpolada: o IDW das '
            f'{estacoes} estações que mediram (as do INMET, as vizinhas e os pluviômetros do CEMADEN), a mesma conta '
            f'do mapa</div><div class="tempo">'
            f'<div style="--cor:#3949ab"><span>Chuva na hora</span>'
            f'<b>{_numero(no_local.get("chuva_hora"), 1, "mm")}</b></div>'
            f'<div style="--cor:#3949ab"><span>Chuva 24 h antes</span>'
            f'<b>{_numero(no_local.get("chuva_24h"), 1, "mm")}</b></div></div>')
    inmet = condicoes.get("inmet")
    if inmet:
        partes.append(
            f'<div class="miudo" style="margin-top:0.6rem">Estação do INMET mais próxima: '
            f'<b>{html.escape(str(inmet["nome"]))}</b> ({html.escape(str(inmet["codigo"]))}), a '
            f'{_numero(inmet["km"], 0)} km · leitura das {inmet["leitura"]:%H:%M} de {inmet["leitura"]:%d/%m}</div>'
            f'<div class="tempo">'
            f'<div style="--cor:#e53935"><span>Temperatura</span><b>{_numero(inmet["temperatura"], 1, "°C")}</b></div>'
            f'<div style="--cor:#00897b"><span>Umidade</span><b>{_numero(inmet["umidade"], 0, "%")}</b></div>'
            f'<div style="--cor:#5e35b1"><span>Rajada</span><b>{_numero(inmet["rajada"], 0, "km/h")}</b></div>'
            f'<div style="--cor:#1e88e5"><span>Chuva na hora</span><b>{_numero(inmet["chuva_hora"], 1, "mm")}</b></div>'
            f'<div style="--cor:#1e88e5"><span>Chuva 24 h antes</span><b>{_numero(inmet["chuva_24h"], 1, "mm")}</b>'
            f'</div></div>')
    else:
        partes.append('<div class="miudo" style="margin-top:0.3rem">Sem leitura do INMET perto desse horário.</div>')
    cemaden = condicoes.get("cemaden")
    if "cemaden" in condicoes and cemaden is None:
        partes.append(f'<div class="miudo" style="margin-top:0.6rem">Nenhum pluviômetro do CEMADEN com leitura a até '
                      f'{DISTANCIA_MAXIMA_DO_PLUVIOMETRO_KM} km.</div>')
    if cemaden:
        partes.append(
            f'<div class="miudo" style="margin-top:0.6rem">Pluviômetro do CEMADEN mais próximo com leitura: '
            f'<b>{html.escape(str(cemaden["nome"]).replace("CEMADEN · ", ""))}</b>, a {_numero(cemaden["km"], 0)} km'
            f'</div><div class="tempo"><div style="--cor:#ef6c00"><span>Chuva na hora</span>'
            f'<b>{_numero(cemaden["chuva_hora"], 1, "mm")}</b></div><div style="--cor:#ef6c00"><span>Chuva 24 h antes'
            f'</span><b>{_numero(cemaden["chuva_24h"], 1, "mm")}</b></div></div>')
    flashes = condicoes.get("raios")
    if flashes is not None:
        partes.append(f'<div class="miudo" style="margin-top:0.6rem">Raios (GLM) a até {RAIO_DOS_RAIOS_KM} km nas '
                      f'{HORAS_DOS_RAIOS} h antes: <b>{flashes} flash{"es" if flashes != 1 else ""}</b></div>')
    partes.append("</div>")
    return partes


def ficha(servico: Servico, ponto: pd.Series, nome_do_tipo: str, condicoes: dict) -> str:
    # O HTML da ficha de um ponto: o tipo, a descrição, a data da observação, o tempo naquela hora e a
    # última atualização. O texto que vem do serviço é escapado
    cor, _ = cor_e_desenho(servico, ponto["tipo"])
    quando = pd.Timestamp(ponto["quando"]) if ponto["quando"] else None
    descricao = html.escape(ponto["descricao"]) or "<i>Sem descrição.</i>"
    partes = [
        ESTILO_DA_FICHA, '<div class="ficha">',
        f'<div class="topo"><div class="rotulo">{html.escape(servico.titulo_da_ficha)}</div>'
        '<div class="titulo">Registro de ocorrência</div></div>',
        f'<div class="bloco"><div class="rotulo">Tipo de ocorrência</div>'
        f'<span class="etiqueta" style="background:{cor}">{html.escape(nome_do_tipo)}</span></div>',
        f'<div class="bloco"><div class="rotulo">Descrição</div><div style="margin-top:0.3rem">{descricao}</div>'
        f'<div class="quando"><span class="rotulo">Data e horário da observação</span><br>'
        f'{f"{quando:%d/%m/%Y %H:%M}" if quando is not None else "sem data"}</div></div>',
    ]
    if condicoes:
        partes += _bloco_do_tempo(condicoes)
    rodape = f"Fonte: {html.escape(servico.credito)}"
    if ponto["atualizado"]:
        rodape = f"Última atualização no serviço: {ponto['atualizado']} · " + rodape
    partes += [f'<div class="bloco miudo">{rodape}</div>', "</div>"]
    return "".join(partes)
