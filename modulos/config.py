"""Configurações do projeto e definição do período de consulta.

Tudo o que pode precisar de ajuste (pastas, API, mapas, interpolação) está
concentrado aqui. As datas da consulta são definidas em main.py.
"""
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# =====================================================
# GERAL
# =====================================================
UF = "MS"
NOME_UF = "Mato Grosso do Sul"

FUSO_UTC = ZoneInfo("UTC")
FUSO_MS = ZoneInfo("America/Campo_Grande")

# =====================================================
# PASTAS E ARQUIVOS
# =====================================================
RAIZ = Path(__file__).resolve().parent.parent
PASTA_SHP = RAIZ / "shp"
PASTA_IMG = RAIZ / "img"
PASTA_SAIDA = RAIZ / "saida"

SHAPE_UF = PASTA_SHP / "MS_UF_2022.shp"
SHAPE_MUN = PASTA_SHP / "MS_mun.shp"

# =====================================================
# CREDENCIAIS
# =====================================================
# O token é lido do arquivo .env na raiz do projeto (modelo: .env.example).
# Uma variável de ambiente TOKEN_INMET já definida no sistema tem prioridade sobre o .env.
load_dotenv(RAIZ / ".env")
TOKEN_INMET = os.getenv("TOKEN_INMET", "").strip()
TOKEN_EXEMPLO = "seu_token_aqui"  # valor do .env.example, tratado como "não configurado"

# =====================================================
# API DO INMET
# =====================================================
URL_ESTACOES = "https://apitempo.inmet.gov.br/estacoes/T"
URL_DADOS = "https://apitempo.inmet.gov.br/token/estacao/{inicio}/{fim}/{codigo}/{token}"
TIMEOUT_ESTACOES = 20          # segundos
TIMEOUT_DADOS = 60             # segundos
PAUSA_ENTRE_REQUISICOES = 0.1  # segundos
HORAS_BUSCA_TEMPO_REAL = 96    # histórico baixado no modo tempo real (cobre o acumulado de 72 h)

# =====================================================
# MAPAS
# =====================================================
LON_MIN, LON_MAX = -58.5, -50.5
LAT_MIN, LAT_MAX = -24.5, -17.0
DPI = 300

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


# =====================================================
# PERÍODO DA CONSULTA
# =====================================================
NOMES_MODO = {"dia": "Data específica", "periodo": "Período", "tempo_real": "Tempo real"}


@dataclass(frozen=True)
class Periodo:
    """Janela de tempo de uma consulta, sempre em UTC.

    Crie com um dos construtores:
        Periodo.de_datas(date(2026, 7, 30), date(2026, 7, 30))  -> data específica
        Periodo.de_datas(date(2026, 8, 1), date(2026, 8, 31))   -> período
        Periodo.tempo_real()                                     -> últimas 24 h

    Todas as janelas são intervalos [início, fim): o início entra, o fim não.
    """

    inicio: datetime
    fim: datetime
    modo: str  # "dia", "periodo" ou "tempo_real"

    @classmethod
    def de_datas(cls, data_inicial: date, data_final: date) -> "Periodo":
        """Dias inteiros, da 00 UTC da data inicial até o fim da data final."""
        if data_final < data_inicial:
            raise ValueError("A data final deve ser igual ou posterior à data inicial.")
        inicio = datetime.combine(data_inicial, time.min, tzinfo=FUSO_UTC)
        fim = datetime.combine(data_final + timedelta(days=1), time.min, tzinfo=FUSO_UTC)
        return cls(inicio, fim, "dia" if data_inicial == data_final else "periodo")

    @classmethod
    def tempo_real(cls, agora: datetime | None = None) -> "Periodo":
        """Últimas 24 horas até o momento atual."""
        agora = agora or datetime.now(FUSO_UTC)
        return cls(agora - timedelta(hours=24), agora, "tempo_real")

    @property
    def nome_modo(self) -> str:
        return NOMES_MODO[self.modo]

    @property
    def num_dias(self) -> int:
        return (self.fim - self.inicio).days

    @property
    def ultimo_dia(self) -> date:
        return (self.fim - timedelta(days=1)).date()

    @property
    def _inicio_do_dia(self) -> datetime:
        return self.fim.replace(hour=0, minute=0, second=0, microsecond=0)

    # ---------- Janelas ----------

    @property
    def janela_busca(self) -> tuple[datetime, datetime]:
        """Intervalo baixado da API."""
        if self.modo == "tempo_real":
            return self.fim - timedelta(hours=HORAS_BUSCA_TEMPO_REAL), self.fim
        return self.inicio, self.fim

    @property
    def janela_extremos(self) -> tuple[datetime, datetime]:
        """Intervalo da temperatura máxima, umidade mínima e rajada máxima."""
        return self.inicio, self.fim

    @property
    def janela_temp_min(self) -> tuple[datetime, datetime]:
        """Intervalo da temperatura mínima (no tempo real: desde 00 UTC do dia atual)."""
        if self.modo == "tempo_real":
            return self._inicio_do_dia, self.fim
        return self.inicio, self.fim

    @property
    def janelas_chuva(self) -> dict[str, tuple[datetime, datetime]]:
        """Colunas de chuva acumulada do relatório e o intervalo de cada uma."""
        if self.modo == "tempo_real":
            janelas = {"Chuva Hoje (desde 00h UTC)": (self._inicio_do_dia, self.fim)}
            for horas in (12, 24, 48, 72):
                janelas[f"Acumulado {horas}h"] = (self.fim - timedelta(hours=horas), self.fim)
            return janelas
        rotulo = "Acumulado Dia" if self.modo == "dia" else "Acumulado Período"
        return {rotulo: (self.inicio, self.fim)}

    @property
    def coluna_chuva_principal(self) -> str:
        """Coluna de chuva usada para ordenar a aba do Excel."""
        return "Acumulado 24h" if self.modo == "tempo_real" else next(iter(self.janelas_chuva))

    # ---------- Textos e saída ----------

    @property
    def identificador(self) -> str:
        """Trecho usado nos nomes de pastas e arquivos."""
        if self.modo == "tempo_real":
            return f"{self.fim:%Y%m%d_%H%M}_UTC"
        if self.modo == "dia":
            return f"{self.inicio:%Y%m%d}"
        return f"{self.inicio:%Y%m%d}_a_{self.ultimo_dia:%Y%m%d}"

    @property
    def descricao(self) -> str:
        if self.modo == "tempo_real":
            return f"Últimas 24 horas: {self.descrever_janela(self.inicio, self.fim)}"
        if self.modo == "dia":
            return f"{self.inicio:%d/%m/%Y} (UTC)"
        return (
            f"Período: {self.inicio:%d/%m/%Y} a {self.ultimo_dia:%d/%m/%Y} "
            f"({self.num_dias} dias, UTC)"
        )

    def descrever_janela(self, inicio: datetime, fim: datetime) -> str:
        """Texto de uma janela para os subtítulos dos mapas."""
        if self.modo == "tempo_real":
            return f"{inicio:%d/%m/%Y %H:%M} até {fim:%d/%m/%Y %H:%M} UTC"
        return self.descricao

    @property
    def pasta_saida(self) -> Path:
        return PASTA_SAIDA / self.modo / self.identificador
