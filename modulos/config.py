"""Configurações do projeto e definição do período de consulta.

Tudo o que pode precisar de ajuste (pastas, API, mapas, interpolação) está
concentrado aqui. As datas da consulta são definidas em main.py.
"""
import os
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# =====================================================
# GERAL
# =====================================================
FUSO_UTC = ZoneInfo("UTC")

# =====================================================
# PASTAS E ARQUIVOS
# =====================================================
RAIZ = Path(__file__).resolve().parent.parent
PASTA_SHP = RAIZ / "shp"
PASTA_IMG = RAIZ / "img"
PASTA_SAIDA = RAIZ / "saida"


# =====================================================
# RECORTE — o estado que está sendo mapeado
# =====================================================
@dataclass(frozen=True)
class Recorte:
    """Quem é o estado, onde ele fica e em que hora ele vive.

    Existe para a UF deixar de ser uma constante do módulo. O painel serve várias pessoas no
    mesmo processo: se a escolha de uma trocasse um valor global, a outra veria o mapa do estado
    errado no meio da própria consulta. Então o recorte **anda junto com a chamada**, e quem não
    passa nenhum recebe o padrão.

    `limites` é (oeste, leste, sul, norte) em graus, com folga sobre o contorno real: é o
    enquadramento do desenho, não a divisa.
    """

    uf: str
    nome: str
    shape_uf: Path
    shape_mun: Path
    limites: tuple[float, float, float, float]
    fuso: ZoneInfo


# Os números de MS são os que sempre valeram, escritos à mão: derivá-los da geometria mudaria o
# enquadramento por arredondamento, e com ele todos os mapas já publicados. Para as próximas UFs
# o caminho é a mesma tabela, com os limites vindos do contorno mais uma margem.
# O municipal é a versão simplificada (~100 m) gerada por ferramentas/simplificar_municipios.py:
# visualmente idêntica nos mapas e bem mais leve para desenhar.
RECORTES = {
    "MS": Recorte(uf="MS", nome="Mato Grosso do Sul",
                  shape_uf=PASTA_SHP / "MS_UF_2022.shp",
                  shape_mun=PASTA_SHP / "MS_mun_simplificado.shp",
                  limites=(-58.5, -50.5, -24.5, -17.0),
                  fuso=ZoneInfo("America/Campo_Grande")),
}
RECORTE = RECORTES["MS"]

# Nome e fuso de cada estado, para montar o recorte de quem ainda não tem entrada escrita à mão.
# Um fuso por estado é aproximação onde o estado tem mais de um — o oeste do Amazonas é GMT-05 e
# o resto GMT-04 —, mas o produto é estadual e o dia é o da capital.
ESTADOS = {
    "AC": ("Acre", "America/Rio_Branco"),            "AL": ("Alagoas", "America/Maceio"),
    "AP": ("Amapá", "America/Belem"),                "AM": ("Amazonas", "America/Manaus"),
    "BA": ("Bahia", "America/Bahia"),                "CE": ("Ceará", "America/Fortaleza"),
    "DF": ("Distrito Federal", "America/Sao_Paulo"), "ES": ("Espírito Santo", "America/Sao_Paulo"),
    "GO": ("Goiás", "America/Sao_Paulo"),            "MA": ("Maranhão", "America/Fortaleza"),
    "MT": ("Mato Grosso", "America/Cuiaba"),         "MS": ("Mato Grosso do Sul", "America/Campo_Grande"),
    "MG": ("Minas Gerais", "America/Sao_Paulo"),     "PA": ("Pará", "America/Belem"),
    "PB": ("Paraíba", "America/Fortaleza"),          "PR": ("Paraná", "America/Sao_Paulo"),
    "PE": ("Pernambuco", "America/Recife"),          "PI": ("Piauí", "America/Fortaleza"),
    "RJ": ("Rio de Janeiro", "America/Sao_Paulo"),   "RN": ("Rio Grande do Norte", "America/Fortaleza"),
    "RS": ("Rio Grande do Sul", "America/Sao_Paulo"), "RO": ("Rondônia", "America/Porto_Velho"),
    "RR": ("Roraima", "America/Boa_Vista"),          "SC": ("Santa Catarina", "America/Sao_Paulo"),
    "SP": ("São Paulo", "America/Sao_Paulo"),        "SE": ("Sergipe", "America/Maceio"),
    "TO": ("Tocantins", "America/Araguaina"),
}
# Folga entre o contorno do estado e a borda do desenho, ao derivar o enquadramento de uma UF
# nova. É a que MS tem: cerca de 0,3° sobrando de cada lado.
MARGEM_ENQUADRAMENTO = 0.35
# Ilha mais longe que isto do corpo principal do estado não conta para o enquadramento. A malha
# do IBGE traz o território inteiro: Trindade fica a 1.100 km do Espírito Santo e Fernando de
# Noronha a 545 km de Pernambuco. Enquadrar por elas dá um mapa de oceano com o estado no canto —
# o do ES sairia com 13° de largura para 3,4° de altura, quando o continental tem 2,2°. Ilha
# perto da costa continua entrando: Marajó, a Ilha de Santa Catarina, Ilhabela.
ILHA_DISTANTE = 1.0            # graus (~110 km)


def shapes_de(uf: str) -> tuple[Path, Path]:
    """Os dois shapefiles de uma UF, na convenção de nome da pasta shp/."""
    return PASTA_SHP / f"{uf}_UF_2022.shp", PASTA_SHP / f"{uf}_mun_simplificado.shp"


def ufs_disponiveis() -> list[str]:
    """As UFs que têm shapefile na pasta — é o que limita quais estados dá para mapear.

    Os arquivos saem da ferramenta `ferramentas/simplificar_municipios.py`, sob demanda: carregar
    os 5.570 municípios do país a cada mapa pesaria na máquina sem servir para nada.
    """
    disponiveis = []
    for uf in sorted(ESTADOS):
        contorno, municipios = shapes_de(uf)
        if contorno.exists() and municipios.exists():
            disponiveis.append(uf)
    return disponiveis


@lru_cache(maxsize=None)
def recorte_de(uf: str) -> Recorte:
    """O recorte de uma UF: escrito à mão quando existe, derivado do shapefile quando não.

    MS é escrito à mão de propósito — derivar o enquadramento da geometria mudaria os números
    por arredondamento, e com eles todo mapa já publicado. Para as demais, o enquadramento sai do
    contorno mais uma margem, que é como os de MS foram escolhidos na origem.
    """
    if uf in RECORTES:
        return RECORTES[uf]
    if uf not in ESTADOS:
        raise KeyError(f"UF desconhecida: {uf}")
    contorno, municipios = shapes_de(uf)
    if not contorno.exists():
        raise FileNotFoundError(f"Sem shapefile para {uf}: esperado {contorno.name} em shp/")

    import geopandas as gpd  # só aqui: quem não desenha mapa não precisa carregar geopandas
    import shapely

    inteiro = shapely.union_all(gpd.read_file(contorno).to_crs("EPSG:4326").geometry.values)
    partes = list(shapely.get_parts(inteiro))
    principal = max(partes, key=lambda parte: parte.area)
    # O enquadramento sai do corpo principal mais o que estiver perto dele; ilha oceânica
    # distante continua no shapefile, só não estica o desenho (ver ILHA_DISTANTE).
    perto = [parte for parte in partes if parte.distance(principal) <= ILHA_DISTANTE]
    oeste, sul, leste, norte = shapely.union_all(perto).bounds
    nome, fuso = ESTADOS[uf]
    margem = MARGEM_ENQUADRAMENTO
    return Recorte(uf=uf, nome=nome, shape_uf=contorno, shape_mun=municipios,
                   # float() porque total_bounds devolve np.float64, e o enquadramento entra
                   # na chave de cache do painel: melhor que seja o tipo nativo.
                   limites=(round(float(oeste) - margem, 2), round(float(leste) + margem, 2),
                            round(float(sul) - margem, 2), round(float(norte) + margem, 2)),
                   fuso=ZoneInfo(fuso))

# Atalhos para o recorte padrão, nos lugares em que só ele faz sentido: o nome do estado nos
# títulos dos produtos e o fuso de quem não recebe recorte nenhum. O enquadramento e os
# shapefiles não têm atalho de propósito — quem desenha um mapa recebe o recorte e lê de lá,
# senão o parâmetro vira enfeite e o módulo volta a mandar no estado.
UF = RECORTE.uf
NOME_UF = RECORTE.nome
FUSO_MS = RECORTE.fuso

# =====================================================
# CREDENCIAIS
# =====================================================
# O token é lido do arquivo .env na raiz do projeto (modelo: .env.example).
# Uma variável de ambiente TOKEN_INMET já definida no sistema tem prioridade sobre o .env.
load_dotenv(RAIZ / ".env")
TOKEN_INMET = os.getenv("TOKEN_INMET", "").strip()
TOKEN_EXEMPLO = "seu_token_aqui"  # valor do .env.example, tratado como "não configurado"

# De onde vêm os dados (modulos/fonte.py), escolhido por instalação: no .env do servidor ou nos
# Secrets do Streamlit Cloud. Por enquanto só existe "apis", as APIs públicas; o banco da UNIGEO
# e a API de leitura chegam nas próximas versões (docs/plano_arquitetura.md).
FONTE_DADOS = os.getenv("FONTE_DADOS", "apis").strip().lower()

# =====================================================
# API DO INMET
# =====================================================
URL_ESTACOES = "https://apitempo.inmet.gov.br/estacoes/T"
URL_DADOS = "https://apitempo.inmet.gov.br/token/estacao/{inicio}/{fim}/{codigo}/{token}"
TIMEOUT_ESTACOES = 20          # segundos
TIMEOUT_DADOS = 60             # segundos
PAUSA_ENTRE_REQUISICOES = 0.1  # segundos
# Quantas consultas podem correr juntas, no processo inteiro. Medido contra a API: com 8 as
# 62 estações de uma semana saem em ~8 s (em fila levavam ~50 s); com 16 o INMET derruba a
# conexão. O limite é global de propósito — o painel é público, e cada pessoa que abre
# dispara o seu lote.
DOWNLOADS_SIMULTANEOS = 8
TENTATIVAS = 3                 # tentativas por requisição quando a API falha por um instante
PAUSA_ENTRE_TENTATIVAS = 2     # segundos antes de repetir; dobra a cada tentativa (2 s, 4 s, ...)
HORAS_BUSCA_TEMPO_REAL = 96    # histórico baixado no modo tempo real (cobre o acumulado de 72 h)

# =====================================================
# MAPAS
# =====================================================
# Até que distância do enquadramento uma estação de outro estado ainda ajuda a interpolar. Em MS
# as estações ficam a ~78 km umas das outras, e o IDW olha para as 8 mais próximas: numa célula
# da divisa, uma estação a 165 km do enquadramento pode estar entre elas. Sem essa margem, os 8
# vizinhos de quem está na borda ficam todos do lado de cá, e a superfície extrapola tendo dado
# do outro lado. Em MS isso traz 54 estações de PR, MT, GO, SP e MG.
MARGEM_RECORTE = 1.5           # graus (~165 km)
DPI = 300
DPI_GRAFICOS = 150  # gráficos são texto e barras: 150 dpi basta e deixa os arquivos leves

# Logos: (arquivo PNG com fundo transparente, retângulo [x, y, largura, altura] em fração da
# moldura do mapa — (0, 0) é o canto inferior esquerdo e (1, 1) o superior direito).
# Cada logo é ajustado ao seu retângulo mantendo a proporção e alinhado ao canto superior direito dele.
LOGOS = [
    (PASTA_IMG / "logo_semadesc.png", [0.705, 0.898, 0.28, 0.087]),
    (PASTA_IMG / "logo_cemtec.png", [0.845, 0.793, 0.14, 0.085]),
]

# =====================================================
# INTERPOLAÇÃO (IDW)
# =====================================================
RESOLUCAO_GRADE = 100          # pontos por eixo
IDW_VIZINHOS = 8
IDW_POTENCIA = 2
MIN_ESTACOES_INTERPOLACAO = 3
# Folga da poda das estações de apoio: quantas mais próximas olhar ao decidir se uma
# estação de fora pode entrar na conta de alguma célula. O IDW usa 8; olhar o dobro
# guarda a que só vira vizinha quando outra falta naquela hora — e falta: num dia comum
# até um quinto das estações fica sem alguma das variáveis. Sem essa folga a poda
# mudaria mapa; com ela, só corta requisição.
VIZINHOS_NA_PODA = 2 * IDW_VIZINHOS

# =====================================================
# RISCO DE FOGO (regra 30-30-30)
# =====================================================
# Cada condição atendida soma um nível: 0 (nenhuma), 1 (baixo), 2 (médio) e 3 (alto).
LIMIAR_TEMP_MAX = 30.0     # °C   — condição atendida com temperatura máxima >= este valor
LIMIAR_UMIDADE_MIN = 30.0  # %    — condição atendida com umidade relativa mínima <= este valor
LIMIAR_RAJADA = 30.0       # km/h — condição atendida com rajada >= este valor

CORES_RISCO = ["#bdbdbd", "#ffd54f", "#fb8c00", "#d32f2f"]  # cinza, amarelo, laranja, vermelho
ROTULOS_RISCO = ["Sem condição", "Risco baixo", "Risco médio", "Risco alto"]
NIVEL_MAPA_HORARIO = 3     # nível mínimo, em alguma estação, para gerar o mapa daquela hora (3 = risco alto)
# Pontos das condições atendidas nos mapas horários, na ordem temperatura, umidade e rajada (fora da paleta de risco)
CORES_CONDICOES = ["#7b1fa2", "#1565c0", "#1b5e20"]  # roxo, azul, verde-escuro


# =====================================================
# PERÍODO DA CONSULTA
# =====================================================
NOMES_MODO = {"dia": "Data específica", "periodo": "Período", "tempo_real": "Tempo real"}


def _gmt(momento: datetime) -> str:
    """O fuso como ele aparece nos subtítulos: GMT-04 em MS, GMT-03 no Paraná."""
    return f"GMT{momento.utcoffset().total_seconds() / 3600:+03.0f}"


@dataclass(frozen=True)
class Periodo:
    """Janela de tempo de uma consulta.

    Crie com um dos construtores:
        Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))  -> data específica
        Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))   -> período
        Periodo.de_datas(date(2026, 9, 14), date(2026, 9, 15), 8, 8)  -> das 08 h de 14/09 às 08 h de 15/09
        Periodo.tempo_real()                                     -> últimas 24 h

    Os dias seguem o horário de MS (da 00 h às 24 h locais); início e fim ficam guardados em
    UTC, como as leituras do INMET. Cada leitura horária se refere à hora que termina no horário
    indicado, então a janela (início, fim) reúne as leituras com início < horário <= fim.
    Regras que valem só para um produto ficam no arquivo do produto, em modulos/produtos/.
    """

    inicio: datetime  # UTC
    fim: datetime     # UTC
    modo: str         # "dia", "periodo" ou "tempo_real"
    # O fuso do recorte. Ele decide onde o dia começa e termina, então não pode ser uma
    # constante do módulo: um período de MT (GMT-04) e um do PR (GMT-03) partem o mesmo dia em
    # horas diferentes. Quem não informa recebe o do recorte padrão.
    fuso: ZoneInfo = FUSO_MS

    @classmethod
    def de_datas(cls, data_inicial: date, data_final: date, hora_inicial: int = 0, hora_final: int = 24,
                 fuso: ZoneInfo = FUSO_MS) -> "Periodo":
        """Da hora inicial da data inicial até a hora final da data final, no horário do recorte.

        Sem horas, são dias inteiros: da 00 h da data inicial às 24 h da data final.
        """
        for hora in (hora_inicial, hora_final):
            if not 0 <= hora <= 24:
                raise ValueError(f"Hora inválida: {hora} (use uma hora cheia de 0 a 24).")
        if data_final < data_inicial:
            raise ValueError("A data final deve ser igual ou posterior à data inicial.")
        inicio = datetime.combine(data_inicial, time.min, tzinfo=fuso) + timedelta(hours=hora_inicial)
        fim = datetime.combine(data_final, time.min, tzinfo=fuso) + timedelta(hours=hora_final)
        if fim <= inicio:
            raise ValueError("O fim da consulta precisa ser depois do início.")
        return cls(inicio.astimezone(FUSO_UTC), fim.astimezone(FUSO_UTC),
                   "dia" if data_inicial == data_final else "periodo", fuso)

    @classmethod
    def tempo_real(cls, agora: datetime | None = None, fuso: ZoneInfo = FUSO_MS) -> "Periodo":
        """Últimas 24 horas até o momento atual."""
        agora = (agora or datetime.now(FUSO_UTC)).astimezone(FUSO_UTC)
        return cls(agora - timedelta(hours=24), agora, "tempo_real", fuso)

    @property
    def nome_modo(self) -> str:
        return NOMES_MODO[self.modo]

    @property
    def primeiro_dia(self) -> date:
        """Primeiro dia da consulta, no horário de MS."""
        return self.inicio.astimezone(self.fuso).date()

    @property
    def ultimo_dia(self) -> date:
        """Último dia da consulta, no horário de MS."""
        return (self.fim.astimezone(self.fuso) - timedelta(microseconds=1)).date()

    @property
    def num_dias(self) -> int:
        return (self.ultimo_dia - self.primeiro_dia).days + 1

    @property
    def horas(self) -> int:
        """Duração da consulta, em horas."""
        return round((self.fim - self.inicio).total_seconds() / 3600)

    @property
    def dias_inteiros(self) -> bool:
        """True quando a consulta começa e termina à 00 h de MS (sem horários informados)."""
        return all(momento.astimezone(self.fuso).time() == time.min for momento in (self.inicio, self.fim))

    @property
    def inicio_do_dia(self) -> datetime:
        """00 h (horário de MS) do dia em que a consulta termina, em UTC. No tempo real: a 00 h de hoje."""
        fim_local = self.fim.astimezone(self.fuso)
        return fim_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(FUSO_UTC)

    # ---------- Janelas ----------

    @property
    def janela(self) -> tuple[datetime, datetime]:
        """Intervalo da consulta: (início, fim]."""
        return self.inicio, self.fim

    @property
    def janela_busca(self) -> tuple[datetime, datetime]:
        """Intervalo baixado da API (no tempo real, inclui o histórico usado nos acumulados)."""
        if self.modo == "tempo_real":
            return self.fim - timedelta(hours=HORAS_BUSCA_TEMPO_REAL), self.fim
        return self.inicio, self.fim

    # ---------- Textos e saída (no horário de MS) ----------

    @property
    def identificador(self) -> str:
        """Trecho usado nos nomes de pastas e arquivos."""
        if self.modo == "tempo_real":
            return f"{self.fim.astimezone(self.fuso):%Y%m%d_%H%M}"
        if not self.dias_inteiros:
            inicio, fim = self.inicio.astimezone(self.fuso), self.fim.astimezone(self.fuso)
            return f"{inicio:%Y%m%d_%H}h_a_{fim:%Y%m%d_%H}h"
        if self.modo == "dia":
            return f"{self.primeiro_dia:%Y%m%d}"
        return f"{self.primeiro_dia:%Y%m%d}_a_{self.ultimo_dia:%Y%m%d}"

    @property
    def descricao(self) -> str:
        if self.modo == "tempo_real":
            return f"Últimas 24 horas: {self.descrever_janela(self.inicio, self.fim)}"
        if not self.dias_inteiros:
            inicio, fim = self.inicio.astimezone(self.fuso), self.fim.astimezone(self.fuso)
            return f"{inicio:%d/%m/%Y %H}h a {fim:%d/%m/%Y %H}h ({self.horas} horas, horário de MS)"
        if self.modo == "dia":
            return f"{self.primeiro_dia:%d/%m/%Y} (horário de MS)"
        return (
            f"Período: {self.primeiro_dia:%d/%m/%Y} a {self.ultimo_dia:%d/%m/%Y} "
            f"({self.num_dias} dias, horário de MS)"
        )

    def descrever_janela(self, inicio: datetime, fim: datetime) -> str:
        """Texto de uma janela para os subtítulos dos mapas, igual em todos os produtos.

        Só as datas quando a janela cobre dias inteiros; com horário e fuso (GMT-04, o horário
        de MS) quando começa ou termina no meio de um dia, que é quando a hora faz diferença.
        """
        inicio, fim = inicio.astimezone(self.fuso), fim.astimezone(self.fuso)
        if inicio.time() == time.min and fim.time() == time.min:
            primeiro, ultimo = inicio.date(), (fim - timedelta(microseconds=1)).date()
            return f"{primeiro:%d/%m/%Y}" if primeiro == ultimo else f"{primeiro:%d/%m/%Y} a {ultimo:%d/%m/%Y}"
        return f"{inicio:%d/%m/%Y %H:%M} a {fim:%d/%m/%Y %H:%M} {_gmt(inicio)}"

    def pasta_saida(self, produto: str) -> Path:
        """Pasta dos resultados: saida/<produto>/<modo>/<identificador>/."""
        return PASTA_SAIDA / produto / self.modo / self.identificador
