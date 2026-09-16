# Scripts UNIGEO / CEMTEC — Monitoramento meteorológico de Mato Grosso do Sul

Produtos meteorológicos gerados a partir dos dados horários das **estações automáticas do INMET** em Mato Grosso do Sul, com **planilhas Excel** e **mapas** (pontuais e interpolados).

| Produto | O que gera |
|---|---|
| `relatorio_inmet` | Extremos de temperatura, umidade e rajada e chuva acumulada de cada estação: planilha Excel e mapas |
| `risco_fogo` | Risco meteorológico de fogo pela regra 30-30-30, avaliada hora a hora: planilha Excel e mapas de nível de risco |

Desenvolvido pela equipe de meteorologia do **CEMTEC** — Centro de Monitoramento do Tempo e do Clima de Mato Grosso do Sul (SEMADESC).

---

## Como usar

1. Instale as dependências (veja [Instalação](#instalação)) e configure o token do INMET no arquivo `.env` (veja [Configuração](#configuração)).
2. Em [`main.py`](main.py), escolha o produto e ajuste as datas da consulta (dias no horário de MS):

   ```python
   PRODUTO = "relatorio_inmet"
   DATA_INICIAL = date(2026, 8, 1)
   DATA_FINAL = date(2026, 8, 31)
   HORA_INICIAL = None  # opcional: hora de início (0 a 24); None = 00 h
   HORA_FINAL = None    # opcional: hora de fim (0 a 24); None = 24 h
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

O produto e as datas também podem ser passados pela linha de comando, sem editar o arquivo:

```bash
python main.py --inicio 30/07/2026
python main.py --inicio 01/08/2026 --fim 31/08/2026
python main.py --inicio 14/09/2026 --hrini 8 --fim 15/09/2026 --hrfim 8
python main.py --tempo-real
python main.py --produto relatorio_inmet --tempo-real
```

`--hrini` e `--hrfim` (ou `HORA_INICIAL` e `HORA_FINAL`) definem a hora de início e de fim, em horas cheias de 0 a 24 no horário de MS — aceitam `8`, `08h` ou `08:00`. O exemplo acima cobre das 08 h de 14/09 às 08 h de 15/09. Sem elas, a consulta vai da 00 h da data inicial às 24 h da data final. Não valem com `--tempo-real`.

**`--produto` e o modo de tempo são independentes.** O `--produto` diz *o que* gerar; `--inicio`/`--fim` ou `--tempo-real` dizem *de quando* são os dados. Nenhum dos dois obriga o outro: sem `--produto`, vale o `PRODUTO` do arquivo; sem argumento de data, valem o `DATA_INICIAL` e o `DATA_FINAL` do arquivo. Por isso `--produto risco_fogo --tempo-real` é só uma das combinações possíveis — `--produto risco_fogo --inicio 16/09/2026` roda o mesmo produto para um dia específico.

`--hrtodas` vale só para o `risco_fogo`: gera o mapa de todas as horas, e não apenas das horas em que alguma estação chegou ao risco médio ou alto.

### O que o `relatorio_inmet` calcula

| Variável | Tempo real | Data específica / Período |
|---|---|---|
| Temperatura mínima | Últimas 24 h, com data/hora | No dia/período, com data/hora |
| Temperatura máxima | Últimas 24 h, com data/hora | No dia/período, com data/hora |
| Umidade relativa mínima | Últimas 24 h | No dia/período |
| Rajada máxima + direção | Últimas 24 h | No dia/período |
| Chuva acumulada | Hoje (desde a 00 h de MS), 12 h, 24 h, 48 h e 72 h | Total do dia/período |

Os dias, os horários dos títulos e os nomes das pastas seguem o horário de MS.

### O que o `risco_fogo` calcula

Conta, **em cada hora**, quantas das três condições da regra 30-30-30 são atendidas:

| Condição | Limiar |
|---|---|
| Temperatura máxima | ≥ 30 °C |
| Umidade relativa mínima | ≤ 30 % |
| Rajada de vento | ≥ 30 km/h |

O resultado é um nível de 0 a 3: **sem condição** (cinza), **risco baixo** (amarelo), **risco médio** (laranja) e **risco alto** (vermelho).

A regra é avaliada hora a hora, e não sobre os extremos do dia: os extremos acontecem em horários diferentes, e o que importa para fogo é calor, seca e vento **ao mesmo tempo**. No mapa interpolado, as três variáveis são interpoladas separadamente e a regra é aplicada célula a célula — interpolar o nível 0–3 diretamente produziria valores sem sentido físico ("1,7 condições") e espalharia risco médio onde nenhuma estação o registrou.

São gerados quatro mapas de síntese — nível máximo e horas em risco alto, cada um pontual e interpolado — e um mapa interpolado por hora. Por padrão só ganham mapa as horas em que **alguma estação** chegou ao laranja; com `--hrtodas`, todas as horas entram. O critério olha as estações, e não a superfície: a superfície interpolada pode mostrar nível 2 numa célula onde nenhuma estação chegou a 2.

Aceita **data específica** e **tempo real**. Período é recusado com mensagem — para período a equipe quer gráficos quantitativos, e não mapas.

Estações a que falte qualquer uma das três variáveis ficam de fora e são listadas ao rodar: incluí-las subestimaria o risco, já que nunca poderiam alcançar o nível 3.

As decisões de método estão registradas em [`docs/questoes_meteorologia.md`](docs/questoes_meteorologia.md) (questões 6 a 8).

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

Cada execução gera uma pasta própria dentro de `saida/`, separada por produto e por modo:

```
saida/
├── relatorio_inmet/
│   ├── dia/
│   │   └── 20260730/
│   │       ├── Relatorio_MS_20260730.xlsx
│   │       └── mapas/
│   │           ├── Mapa_Temp_Min_MS_20260730.png
│   │           ├── Mapa_Temp_Min_MS_20260730_interpolado.png
│   │           └── ...
│   ├── periodo/
│   │   └── 20260801_a_20260831/
│   └── tempo_real/
│       └── 20260914_0925/
└── risco_fogo/
    ├── dia/
    │   └── 20260916/
    │       ├── Risco_Fogo_MS_20260916.xlsx
    │       └── mapas/
    │           ├── Mapa_Risco_Fogo_Nivel_MS_20260916.png
    │           ├── Mapa_Risco_Fogo_Nivel_MS_20260916_interpolado.png
    │           ├── Mapa_Risco_Fogo_Horas_MS_20260916.png
    │           ├── Mapa_Risco_Fogo_Horas_MS_20260916_interpolado.png
    │           └── horas/
    │               ├── Mapa_Risco_Fogo_MS_20260916_14h.png
    │               └── ...
    └── tempo_real/
        └── 20260916_0925/
```

Quando a consulta usa horários, o nome da pasta inclui as horas — por exemplo, `periodo/20260914_08h_a_20260915_08h/` — e a chuva aparece como "Acumulado Período", com a duração em horas no título do mapa.

| Modo | Mapas do `relatorio_inmet` |
|---|---|
| Tempo real | 15 — temperatura mínima e máxima, umidade, chuva 24 h, 48 h e 72 h (pontual + interpolado), rajadas (pontual + interpolado) e rajadas com direção |
| Data específica / Período | 11 — os mesmos, com um único mapa de chuva (do dia ou do período) |

O `risco_fogo` gera sempre 4 mapas de síntese (nível máximo e horas em risco alto, cada um pontual e interpolado), mais um mapa interpolado por hora em `mapas/horas/`. A quantidade de mapas horários varia: por padrão, só as horas em que alguma estação chegou ao risco médio ou alto; com `--hrtodas`, até 24 num dia.

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

Ficam em [`modulos/config.py`](modulos/config.py): UF, pastas, endereços, tempos limite e tentativas da API, limites e resolução dos mapas, posição dos logos e parâmetros da interpolação.

### Quando a API do INMET falha

É comum a API encerrar a conexão sem responder no meio de uma consulta. Nesse caso, a estação é consultada de novo até `TENTATIVAS` vezes (padrão: 3), dobrando a espera a cada tentativa. Se ainda assim não der certo, a execução continua com as demais estações e o resumo do fim separa as que ficaram de fora:

- **Sem leituras no período** — a estação respondeu, mas não tem dados nessas horas.
- **Falha na consulta** — não foi possível ler a estação. Ela pode ter dados: vale repetir a consulta mais tarde.

Erros de token (HTTP 4xx) não são repetidos, porque novas tentativas não resolvem.

## Testes

Os testes usam uma API do INMET simulada, então não precisam de token nem de internet:

```bash
pip install -r requirements-dev.txt
pytest
```

Rode os testes antes de cada commit: eles conferem as janelas de tempo, os cálculos, o acesso à API e a execução completa (Excel e mapas) nos três modos.

Os testes também rodam automaticamente no GitHub (GitHub Actions, em Linux, com Python 3.10 e 3.14) a cada push e a cada pull request para a `main`. O resultado aparece como ✓ ou ✗ ao lado de cada commit e na aba **Actions** do repositório, onde também é possível rodá-los manualmente.

## Novos produtos

Os scripts da equipe estão sendo migrados aos poucos. Cada um vira um produto em `modulos/produtos/`, reaproveitando as peças compartilhadas (API do INMET, períodos, cálculos, mapas e Excel). O passo a passo está em [`docs/como_migrar_um_script.md`](docs/como_migrar_um_script.md).

## Estrutura do repositório

```
.
├── main.py               # Ponto de entrada: escolha do produto e das datas
├── modulos/              # Peças compartilhadas por todos os produtos
│   ├── config.py         # Configurações, leitura do .env e a classe Periodo (janelas de tempo)
│   ├── inmet.py          # Acesso à API do INMET (estações e dados horários)
│   ├── calculos.py       # Recorte no tempo, extremos, acumulados e interpolação IDW
│   ├── mapas.py          # Mapas pontuais e interpolados
│   ├── excel.py          # Planilha Excel
│   └── produtos/         # Um arquivo por produto
│       ├── relatorio_inmet.py  # Extremos e chuva das estações automáticas
│       └── risco_fogo.py       # Risco de fogo pela regra 30-30-30, hora a hora
├── ferramentas/          # Scripts auxiliares (ex.: gerar o shapefile simplificado dos municípios)
├── tests/                # Testes automatizados (pytest), com a API do INMET simulada
├── .github/workflows/    # Execução automática dos testes no GitHub (GitHub Actions)
├── docs/                 # Documentos da equipe (ex.: questões em aberto para a meteorologia)
├── shp/                  # Shapefiles: limite estadual e municípios (original e simplificado)
├── img/                  # Logos inseridos nos mapas (PNG com fundo transparente)
├── saida/                # Resultados gerados (fora do controle de versão)
├── legado/               # Scripts originais de cada produto (ex.: legado/relatorio_inmet/), para comparação
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

- **Horários:** a API do INMET retorna os dados em UTC. Os dias, as janelas, os títulos e os nomes das pastas seguem o horário de MS (`America/Campo_Grande`, UTC−4); a planilha traz a data/hora das temperaturas em UTC e em MS.
- **Leituras horárias:** cada leitura do INMET se refere à hora que termina no horário indicado (a das 05 UTC cobre das 04 às 05 UTC). Assim, o dia D — da 00 h às 24 h de MS — reúne as leituras das 05 UTC do dia D às 04 UTC do dia seguinte.
- **Rajada:** `VEN_RAJ` é convertida de m/s para km/h (× 3,6). A direção registrada é a do horário da rajada máxima.
- **Interpolação:** IDW (inverso do quadrado da distância) com os 8 vizinhos mais próximos, em uma grade de 100 × 100 pontos sobre longitude −58,5 a −50,5 e latitude −24,5 a −17,0. A distância é medida em quilômetros, numa projeção equidistante centrada em MS. São necessárias ao menos 3 estações. A superfície é calculada até um pouco além da divisa e recortada exatamente pelo contorno de MS.
- **Mapas de chuva:** incluem as estações com 0 mm, que aparecem no mapa pontual e entram na interpolação. Num dia sem chuva em nenhuma estação, os mapas mostram 0 mm em todo o estado.

## Mudanças em relação aos scripts legados

Os três scripts de `legado/relatorio_inmet/` foram unificados no produto `relatorio_inmet`. Diferenças nos resultados:

- **Período:** os extremos (temperaturas, umidade e rajada) agora ficam restritos ao período. Antes, o dia seguinte à data final também entrava no cálculo.
- **Data específica:** a aba `Chuva` traz apenas o `Acumulado Dia`. As colunas `Acumulado 24h` e `Acumulado 48h` foram removidas — a de 48 h somava só cerca de 24 h de dados.
- **Temperaturas:** sempre com data e hora completas, em UTC e em horário de MS.
- **Coordenadas:** `Latitude` e `Longitude` em todas as abas; a associação é feita pela própria estação, não pelo nome.
- **Mapa de rajadas com direção:** gerado em todos os modos (antes, só no tempo real).
- **Mapa de chuva de 72 h no tempo real:** novo (antes, só 24 h e 48 h).
- **Dia no horário de MS:** data específica e período usam o dia da 00 h às 24 h de MS (antes, o dia em UTC, com as leituras das 00 às 23 UTC).
- **Temperatura mínima no tempo real:** usa as últimas 24 horas, como as demais variáveis (antes, desde as 00 UTC do dia).
- **"Chuva Hoje" no tempo real:** conta desde a 00 h de MS (antes, desde as 00 UTC).
- **Estações com 0 mm nos mapas de chuva:** aparecem no mapa pontual e entram na interpolação (antes, eram descartadas).
- **Interpolação em quilômetros:** antes, a distância era medida em graus.
- **Horários nos títulos e nos nomes das pastas:** no horário de MS (antes, em UTC).
- **Logos:** em PNG com fundo transparente e posicionados em relação à moldura do mapa — mesmo lugar e proporção nos mapas pontuais e interpolados (antes, nos pontuais, um logo cobria o outro).
- **Bordas dos mapas interpolados:** a cor preenche o estado até a divisa. Antes, a superfície era cortada pela grade de cálculo (células de ~8 km) e deixava falhas em degrau junto às bordas.
- **Desempenho:** shapefiles, logos e máscara do estado são carregados uma única vez por execução, e os limites municipais usam uma versão simplificada (~5% dos vértices, sem diferença visível).

## Decisões da equipe de meteorologia

As regras de cálculo acima (dia no horário de MS, janela da temperatura mínima, estações com 0 mm nos mapas de chuva e distância em quilômetros) foram definidas pela equipe de meteorologia em 15/09/2026. As perguntas, as opções e as respostas estão registradas em [`docs/questoes_meteorologia.md`](docs/questoes_meteorologia.md).
