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
# Versão simplificada (~100 m) de MS_mun.shp, gerada por ferramentas/simplificar_municipios.py:
# visualmente idêntica nos mapas e bem mais leve para desenhar.
SHAPE_MUN = PASTA_SHP / "MS_mun_simplificado.shp"

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
TENTATIVAS = 3                 # tentativas por requisição quando a API falha por um instante
PAUSA_ENTRE_TENTATIVAS = 2     # segundos antes de repetir; dobra a cada tentativa (2 s, 4 s, ...)
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

    @classmethod
    def de_datas(cls, data_inicial: date, data_final: date, hora_inicial: int = 0, hora_final: int = 24) -> "Periodo":
        """Da hora inicial da data inicial até a hora final da data final, no horário de MS.

        Sem horas, são dias inteiros: da 00 h da data inicial às 24 h da data final.
        """
        for hora in (hora_inicial, hora_final):
            if not 0 <= hora <= 24:
                raise ValueError(f"Hora inválida: {hora} (use uma hora cheia de 0 a 24).")
        if data_final < data_inicial:
            raise ValueError("A data final deve ser igual ou posterior à data inicial.")
        inicio = datetime.combine(data_inicial, time.min, tzinfo=FUSO_MS) + timedelta(hours=hora_inicial)
        fim = datetime.combine(data_final, time.min, tzinfo=FUSO_MS) + timedelta(hours=hora_final)
        if fim <= inicio:
            raise ValueError("O fim da consulta precisa ser depois do início.")
        return cls(inicio.astimezone(FUSO_UTC), fim.astimezone(FUSO_UTC),
                   "dia" if data_inicial == data_final else "periodo")

    @classmethod
    def tempo_real(cls, agora: datetime | None = None) -> "Periodo":
        """Últimas 24 horas até o momento atual."""
        agora = (agora or datetime.now(FUSO_UTC)).astimezone(FUSO_UTC)
        return cls(agora - timedelta(hours=24), agora, "tempo_real")

    @property
    def nome_modo(self) -> str:
        return NOMES_MODO[self.modo]

    @property
    def primeiro_dia(self) -> date:
        """Primeiro dia da consulta, no horário de MS."""
        return self.inicio.astimezone(FUSO_MS).date()

    @property
    def ultimo_dia(self) -> date:
        """Último dia da consulta, no horário de MS."""
        return (self.fim.astimezone(FUSO_MS) - timedelta(microseconds=1)).date()

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
        return all(momento.astimezone(FUSO_MS).time() == time.min for momento in (self.inicio, self.fim))

    @property
    def inicio_do_dia(self) -> datetime:
        """00 h (horário de MS) do dia em que a consulta termina, em UTC. No tempo real: a 00 h de hoje."""
        fim_local = self.fim.astimezone(FUSO_MS)
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
            return f"{self.fim.astimezone(FUSO_MS):%Y%m%d_%H%M}"
        if not self.dias_inteiros:
            inicio, fim = self.inicio.astimezone(FUSO_MS), self.fim.astimezone(FUSO_MS)
            return f"{inicio:%Y%m%d_%H}h_a_{fim:%Y%m%d_%H}h"
        if self.modo == "dia":
            return f"{self.primeiro_dia:%Y%m%d}"
        return f"{self.primeiro_dia:%Y%m%d}_a_{self.ultimo_dia:%Y%m%d}"

    @property
    def descricao(self) -> str:
        if self.modo == "tempo_real":
            return f"Últimas 24 horas: {self.descrever_janela(self.inicio, self.fim)}"
        if not self.dias_inteiros:
            inicio, fim = self.inicio.astimezone(FUSO_MS), self.fim.astimezone(FUSO_MS)
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
        inicio, fim = inicio.astimezone(FUSO_MS), fim.astimezone(FUSO_MS)
        if inicio.time() == time.min and fim.time() == time.min:
            primeiro, ultimo = inicio.date(), (fim - timedelta(microseconds=1)).date()
            return f"{primeiro:%d/%m/%Y}" if primeiro == ultimo else f"{primeiro:%d/%m/%Y} a {ultimo:%d/%m/%Y}"
        return f"{inicio:%d/%m/%Y %H:%M} a {fim:%d/%m/%Y %H:%M} GMT-04"

    def pasta_saida(self, produto: str) -> Path:
        """Pasta dos resultados: saida/<produto>/<modo>/<identificador>/."""
        return PASTA_SAIDA / produto / self.modo / self.identificador
