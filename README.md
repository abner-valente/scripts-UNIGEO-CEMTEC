# Scripts UNIGEO / CEMTEC — Monitoramento meteorológico de Mato Grosso do Sul

Coleta os dados horários das **estações automáticas do INMET** em Mato Grosso do Sul e gera **relatórios em Excel** e **mapas** (pontuais e interpolados) de temperatura, umidade relativa, chuva e vento.

Desenvolvido pela equipe de meteorologia do **CEMTEC** — Centro de Monitoramento do Tempo e do Clima de Mato Grosso do Sul (SEMADESC).

---

## Como usar

1. Instale as dependências (veja [Instalação](#instalação)) e configure o token do INMET no arquivo `.env` (veja [Configuração](#configuração)).
2. Em [`main.py`](main.py), ajuste as datas da consulta (dias em UTC):

   ```python
   DATA_INICIAL = date(2026, 8, 1)
   DATA_FINAL = date(2026, 8, 31)
   ```

   | Datas | Modo | O que é calculado |
   |---|---|---|
   | `DATA_INICIAL == DATA_FINAL` | **Data específica** | Extremos e chuva do dia |
   | `DATA_INICIAL != DATA_FINAL` | **Período** | Extremos e chuva acumulada do período |
   | Ambas `None` | **Tempo real** | Extremos das últimas 24 h e chuva de hoje, 12, 24, 48 e 72 h |

3. Execute:

   ```bash
   python main.py
   ```

As datas também podem ser passadas pela linha de comando, sem editar o arquivo (útil para agendamentos):

```bash
python main.py --inicio 30/07/2026
python main.py --inicio 01/08/2026 --fim 31/08/2026
python main.py --tempo-real
```

### O que é calculado

| Variável | Tempo real | Data específica / Período |
|---|---|---|
| Temperatura mínima | Desde 00 UTC do dia atual, com data/hora | No dia/período, com data/hora |
| Temperatura máxima | Últimas 24 h, com data/hora | No dia/período, com data/hora |
| Umidade relativa mínima | Últimas 24 h | No dia/período |
| Rajada máxima + direção | Últimas 24 h | No dia/período |
| Chuva acumulada | Hoje (desde 00 UTC), 12 h, 24 h, 48 h e 72 h | Total do dia/período |

## Como funciona

1. Consulta a lista de estações automáticas do INMET e filtra as de MS.
2. Baixa a série horária de cada estação pela API do INMET.
3. Calcula os extremos e os acumulados de cada variável.
4. Gera a planilha Excel com as abas `Temp_Min`, `Temp_Max`, `Umidade`, `Vento` e `Chuva`, ordenadas pelo valor.
5. Gera os mapas em PNG (300 dpi) com o limite estadual, os municípios, o ranking dos 5 maiores/menores valores e os logos institucionais.

Tipos de mapa:

- **Pontual** — cada estação colorida e rotulada com o seu valor.
- **Interpolado** — superfície contínua (IDW) recortada ao contorno de MS.
- **Rajadas com direção** — superfície interpolada das rajadas com setas de direção do vento.

## Saídas

Cada execução gera uma pasta própria dentro de `saida/`, separada por modo:

```
saida/
├── dia/
│   └── 20260730/
│       ├── Relatorio_MS_20260730.xlsx
│       └── mapas/
│           ├── Mapa_Temp_Min_MS_20260730.png
│           ├── Mapa_Temp_Min_MS_20260730_interpolado.png
│           └── ...
├── periodo/
│   └── 20260801_a_20260831/
└── tempo_real/
    └── 20260914_1325_UTC/
```

| Modo | Mapas gerados |
|---|---|
| Tempo real | 13 — temperatura mínima e máxima, umidade, chuva 24 h e 48 h (pontual + interpolado), rajadas (pontual + interpolado) e rajadas com direção |
| Data específica / Período | 11 — os mesmos, com um único mapa de chuva (do dia ou do período) |

O conteúdo de `saida/` fica fora do controle de versão.

## Requisitos

- Python **3.10 ou superior**
- Token de acesso à API do INMET
- Conexão com a internet

Principais bibliotecas (lista completa em [`requirements.txt`](requirements.txt)):

| Biblioteca | Uso |
|---|---|
| `pandas` | Tabelas, datas e exportação para Excel |
| `requests` | Chamadas à API do INMET |
| `numpy` / `scipy` | Grade e interpolação IDW (`cKDTree`) |
| `geopandas` / `shapely` | Leitura dos shapefiles e recorte espacial |
| `matplotlib` | Geração dos mapas |
| `openpyxl` | Escrita e formatação da planilha Excel |
| `tzdata` | Fusos horários (obrigatório no Windows) |
| `python-dotenv` | Leitura do arquivo `.env` |

## Instalação

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

### Token do INMET

O token é lido do arquivo `.env`, na raiz do projeto:

1. Crie o `.env` a partir do modelo:

   ```powershell
   Copy-Item .env.example .env
   ```

   No Linux/macOS: `cp .env.example .env`.

2. Abra o `.env` e preencha `TOKEN_INMET` com o token da equipe:

   ```
   TOKEN_INMET=seu_token_aqui
   ```

Sem o token configurado, o `main.py` para com uma mensagem de erro antes de chamar a API.

> O `.env` contém credenciais e **nunca deve ser commitado** — ele já está no `.gitignore`. Em agendamentos ou servidores, o token também pode ser definido como variável de ambiente `TOKEN_INMET`, que tem prioridade sobre o `.env`.

### Logos

Os logos ficam em `img/`, em **PNG com fundo transparente** — uma imagem com fundo branco cobriria o mapa. A posição e o tamanho de cada um são definidos na lista `LOGOS`, em [`modulos/config.py`](modulos/config.py), como um retângulo `[x, y, largura, altura]` em fração da moldura do mapa: `(0, 0)` é o canto inferior esquerdo e `(1, 1)` o superior direito. O logo se ajusta ao retângulo mantendo a proporção e fica alinhado ao canto superior direito dele, no mesmo lugar em todos os mapas.

### Demais parâmetros

Ficam em [`modulos/config.py`](modulos/config.py): UF, pastas, endereços e tempos limite da API, limites e resolução dos mapas, posição dos logos e parâmetros da interpolação.

## Testes

Os testes usam uma API do INMET simulada, então não precisam de token nem de internet:

```bash
pip install -r requirements-dev.txt
pytest
```

Rode os testes antes de cada commit: eles conferem as janelas de tempo, os cálculos, o acesso à API e a execução completa (Excel e mapas) nos três modos.

## Estrutura do repositório

```
.
├── main.py               # Ponto de entrada: datas da consulta e execução
├── modulos/
│   ├── config.py         # Configurações, leitura do .env e a classe Periodo (janelas de tempo)
│   ├── inmet.py          # Acesso à API do INMET
│   ├── calculos.py       # Extremos, acumulados de chuva e interpolação IDW
│   ├── mapas.py          # Mapas pontuais e interpolados
│   └── excel.py          # Relatório Excel
├── ferramentas/          # Scripts auxiliares (ex.: gerar o shapefile simplificado dos municípios)
├── tests/                # Testes automatizados (pytest), com a API do INMET simulada
├── docs/                 # Documentos da equipe (ex.: questões em aberto para a meteorologia)
├── shp/                  # Shapefiles: limite estadual e municípios (original e simplificado)
├── img/                  # Logos inseridos nos mapas (PNG com fundo transparente)
├── saida/                # Resultados gerados (fora do controle de versão)
├── legado/               # Scripts originais, mantidos para comparação durante a validação
├── requirements.txt
├── requirements-dev.txt  # Dependências de desenvolvimento (testes)
├── pytest.ini            # Configuração dos testes
├── .env                  # Token do INMET (local, fora do controle de versão)
├── .env.example          # Modelo do arquivo .env
└── README.md
```

> Cada shapefile é formado por vários arquivos com o mesmo nome (`.shp`, `.shx`, `.dbf`, `.prj`, `.cpg`), que precisam ficar juntos na mesma pasta.

## Dados de referência

- **API do INMET** (`apitempo.inmet.gov.br`)
  - `/estacoes/T` — lista de estações automáticas (sem token)
  - `/token/estacao/{data_ini}/{data_fim}/{codigo}/{token}` — dados horários de uma estação
- **Shapefiles** (SIRGAS 2000, EPSG:4674, reprojetados para WGS 84/EPSG:4326 na execução):
  - `MS_UF_2022` — limite estadual (malha IBGE 2022).
  - `MS_mun` — limites dos 79 municípios, versão original e detalhada (~755 mil vértices).
  - `MS_mun_simplificado` — versão usada nos mapas, simplificada com tolerância de 0,001° (~100 m, menos de meio pixel): ~5% dos vértices e sem diferença visível. Serve só para desenho; para cálculos de área ou análises espaciais, use o original. Se a malha municipal for atualizada, substitua os arquivos `MS_mun.*` e gere a versão simplificada de novo com `python ferramentas/simplificar_municipios.py`.

## Notas metodológicas

- **Horários:** a API do INMET retorna os dados em UTC. As temperaturas mínima e máxima trazem a data/hora em UTC e no horário local de MS (`America/Campo_Grande`, UTC−4).
- **Janelas de tempo:** todos os recortes usam o intervalo `[início, fim)` — a leitura das 00 UTC do dia inicial entra e a das 00 UTC do dia seguinte ao final não entra.
- **Rajada:** `VEN_RAJ` é convertida de m/s para km/h (× 3,6). A direção registrada é a do horário da rajada máxima.
- **Interpolação:** IDW (inverso do quadrado da distância) com os 8 vizinhos mais próximos, em uma grade de 100 × 100 pontos sobre longitude −58,5 a −50,5 e latitude −24,5 a −17,0. A distância é calculada em graus. São necessárias ao menos 3 estações.
- **Mapas de chuva:** consideram somente as estações com acumulado maior que zero.

## Mudanças em relação aos scripts legados

Os três scripts de `legado/` foram unificados no `main.py`. Diferenças nos resultados:

- **Período:** os extremos (temperaturas, umidade e rajada) agora ficam restritos ao período. Antes, o dia seguinte à data final também entrava no cálculo.
- **Data específica:** a aba `Chuva` traz apenas o `Acumulado Dia`. As colunas `Acumulado 24h` e `Acumulado 48h` foram removidas — a de 48 h somava só cerca de 24 h de dados.
- **Temperaturas:** sempre com data e hora completas, em UTC e em horário de MS.
- **Coordenadas:** `Latitude` e `Longitude` em todas as abas; a associação é feita pela própria estação, não pelo nome.
- **Mapa de rajadas com direção:** gerado em todos os modos (antes, só no tempo real).
- **Títulos dos mapas no tempo real:** mostram a janela real de cada variável (ex.: a temperatura mínima indica "desde 00 UTC").
- **Logos:** em PNG com fundo transparente e posicionados em relação à moldura do mapa — mesmo lugar e proporção nos mapas pontuais e interpolados (antes, nos pontuais, um logo cobria o outro).
- **Desempenho:** shapefiles, logos e máscara do estado são carregados uma única vez por execução, e os limites municipais usam uma versão simplificada (~5% dos vértices, sem diferença visível).

## Limitações conhecidas (em revisão)

Estes pontos dependem de decisão da equipe de meteorologia e estão detalhados, com as opções, em [`docs/questoes_meteorologia.md`](docs/questoes_meteorologia.md).

- Os mapas de chuva interpolados excluem as estações sem chuva, o que pode espalhar chuva sobre áreas secas.
- A convenção de horário das leituras das 00 UTC (se pertencem ao dia anterior ou ao dia atual) precisa ser validada pela equipe de meteorologia.
- No tempo real, a temperatura mínima usa a janela "desde 00 UTC" (20 h do dia anterior no horário de MS), enquanto as demais variáveis usam "últimas 24 h".
