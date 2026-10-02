# Changelog

O que mudou em cada versão, do ponto de vista de quem usa: quem roda `python main.py` e quem abre o painel. A versão mais nova fica no topo; o detalhe técnico de cada mudança está nas mensagens dos commits.

Cada versão separa as mudanças em até quatro grupos:

- **Atenção ao atualizar** — o que pode quebrar um comando anotado ou mudar um resultado já conhecido. Vale ler antes de tudo.
- **Novo**, **Mudou** e **Corrigido**.

Para atualizar a sua cópia, veja [Como atualizar](README.md#como-atualizar).

## [0.2.3] — em andamento

Ainda não está na `main`: o que entra aqui chega com o próximo `git pull` depois do merge.

### Atenção ao atualizar

- **O painel pede o Streamlit 1.52 ou mais novo.** Quem roda o painel na própria máquina atualiza com `pip install -r app/requirements.txt`. A linha de comando não muda.

### Novo

- **Painel: botão "Baixar PNG do boletim"** em cada mapa das abas Mapas Boletim, Chuva e Risco de Fogo, a pedido da equipe. O mapa sai na moldura do relatório, como o `main.py` grava: título, subtítulo com a janela e o fuso, crédito INMET/SEMADESC, logos do CEMTEC e da SEMADESC, ranking das cinco estações e barra de cores, em 300 dpi.
  - **As cores e a escala são as da tela**, e não as do `main.py`: o que se baixa é o que se vê.
  - Os mapas de risco saem com os títulos e os nomes de arquivo do `risco_fogo`. O mapa horário traz o nível e as condições de cada estação, como o do produto, mesmo com a caixa de detalhes desligada na tela.
  - O botão "Baixar PNG" de antes continua, com o desenho da tela.
- `CHANGELOG.md`, e as versões viram tags (`0.1.1` a `0.2.2`): `git describe --tags` diz em que versão a cópia está.

### Corrigido

- **Painel, aba Risco de Fogo: "Por dia" e "Período inteiro" caíam com erro** (`KeyError`) quando a caixa "Mostrar o nível e as condições de cada estação" estava ligada. Vinha desde a 0.2.1. Agora esses dois mapas mostram só o nível de cada estação, e os pontos das condições ficam no hora a hora, como no produto: o dia e o período juntam horas diferentes, e as condições de uma hora não representam o dia.

## [0.2.2] — 01/10/2026

De "só MS" para "qualquer estado com estação". Entrou na `main` junto com a 0.2.1.

### Atenção ao atualizar

- **Python 3.14 ou superior.** Antes o mínimo declarado era o 3.10.
- **Os mapas interpolados mudam na borda do estado**, porque as estações vizinhas passaram a entrar na conta (abaixo). Em MS, num dia comum (29/09), mudam de 1,5% a 4% dos pixels, todos na borda. Planilhas e mapas pontuais saem iguais.
- **A linha de comando ficou mais lenta**: ela baixa também as vizinhas, ainda uma estação por vez. Em MS são 98 consultas em vez de 59.
- **A aba Chuva do `relatorio_inmet` tem 59 estações em MS, e não mais 62** (ver Corrigido).

### Novo

- **Qualquer um dos 25 estados com estação automática operante**: `--uf` na linha de comando e seletor no alto da barra lateral do painel. MS continua o padrão, e sem `--uf` os comandos de sempre funcionam igual. RR e SE ficam de fora porque todas as estações deles estão em pane.
  - Com o estado vêm junto as estações, os shapefiles e a sigla nos títulos, nos nomes de arquivo e nas colunas de horário: "Data/Hora (SC)", "Primeiro Horário em Risco Alto (SC)".
  - Vem junto também o **fuso**, que decide onde o dia começa: MS e MT ficam em GMT-04, SC e PR em GMT-03. `--hrini` e `--hrfim` passam a valer no horário do estado escolhido.
  - Os logos dos mapas continuam os de MS em todos os estados, porque quem produz o mapa é o CEMTEC. Em estados mais largos que MS, como SC, o bloco de logos cobre parte do mapa nos produtos; onde ele deve ficar ainda está em aberto.
- **Estações vizinhas na interpolação**, no painel e nos dois produtos. Entram as de fora do estado que podem estar entre as 8 mais próximas de alguma célula do mapa: em MS são 39, de MT, GO, PR, SP e MG. Sem elas, a superfície na divisa extrapolava como se não houvesse medição do outro lado.
  - Elas só entram na superfície interpolada. Planilha, ranking e mapa pontual continuam só com as estações do estado.
  - No `risco_fogo`, o mapa horário continua saindo só quando uma estação **do estado** chega ao risco alto: uma estação do Paraná em risco alto não gera mapa horário de MS.
  - Se a lista das vizinhas falhar, o produto avisa e sai como antes, só com as do estado.
- **Painel, aba Chuva:**
  - a cascata das estações escolhidas, que ficava em Séries Temporais, agora abre a aba, com o valor escrito nas barras em que choveu;
  - nova cascata do **estado inteiro**, que aparece sempre e acompanha o deslizante "Acumulados até". Ela soma os milímetros de todas as estações: serve para ver *quando* choveu e quanto do total veio de cada dia, e não quanto choveu num lugar.
- `ferramentas/simplificar_municipios.py --uf XX` baixa do IBGE a malha 2022 de um estado e grava os shapefiles que os mapas usam.

### Mudou

- **A escala de cores dos mapas interpolados dos produtos fica presa na faixa do estado.** Sem isso, uma vizinha mais fria que todo o estado esticava a régua, e o mapa inteiro mudava de cor sem o miolo ter mudado. A borda que passar da faixa fica com a cor da ponta.
- O painel abre com a caixa **"Mostrar o valor de cada estação" ligada**, nas quatro abas que a têm.
- A aba Mapas Boletim abre com mapas já escolhidos:
  - hora a hora: temperatura da hora cheia, umidade mínima e rajada;
  - por dia: temperatura média, umidade mínima e rajada máxima;
  - período: os mesmos do dia, com a compensada no lugar da temperatura média.
- O painel guarda na memória os mapas-base de até 3 estados. Ao trocar para um quarto, o primeiro mapa espera de 1 a 3 s para reler os shapefiles; os dados já baixados continuam no cache.

### Corrigido

- **Estações em pane deixam de ser consultadas.** Elas devolvem horas sem medida nenhuma, e a aba Chuva do `relatorio_inmet` somava isso como 0,0 mm. Como estação com 0 mm entra no mapa de chuva (decisão de 15/09), o mapa mostrava seca falsa em volta delas. Em MS, eram Coxim, Itaquiraí e Ribas do Rio Pardo.
- **Painel: os mapas da tarde podiam ficar em branco para sempre.** O INMET publica a linha da hora antes das medidas, e o cache guardava essa linha vazia como resposta final. Agora a hora sem medida é consultada de novo, e um cache que já estava assim se corrige sozinho.

## [0.2.1] — 01/10/2026

O Painel Meteorológico, em Streamlit. Desenvolvida de 22 a 28/09/2026 e publicada no Streamlit Cloud a partir da própria branch; chegou à `main` junto com a 0.2.2.

### Atenção ao atualizar

- **Nada muda no que `python main.py` produz.** O painel é uma ferramenta de análise, à parte: as entregas continuam saindo pelo `main.py`.
- O painel tem dependências próprias. Quem só gera os produtos não precisa instalar nada; quem quer o painel roda uma vez `pip install -r app/requirements.txt`.

### Novo

- **Painel Meteorológico** (`streamlit run app/explorador.py`), com seis abas:
  - **Estações: Séries Temporais** — um gráfico por grandeza, com máxima, mínima e média da hora (no diário, as do dia mais a compensada do INMET). O cursor em qualquer ponto mostra todas as estações daquela hora, e clicar na legenda isola uma série. Vento em km/h, direção em pontos e rosa dos ventos por estação.
  - **Mapas Boletim** — os mesmos mapas dos produtos (mesma interpolação, mesmo recorte, mesmas cores), hora a hora, por dia ou do período, com a regra de cada um escrita embaixo. Cada mapa tem um botão que monta o GIF do período.
  - **Chuva** — acumulados de 3 a 96 h e do mês, contados para trás a partir do instante escolhido. Barras com as 15 estações que mais choveram e mapas opcionais. Nesta versão, a cascata do acumulado ficava em Séries Temporais.
  - **Risco de Fogo** — a regra 30-30-30 do produto, importada e não reescrita, para a tela e o relatório nunca discordarem. Hora a hora, por dia e no período, com os três gráficos do produto.
  - **Mapa Navegação** — a mesma superfície sobre um mapa base que se aproxima e arrasta. Fica **em avaliação** até a equipe decidir se vai usá-la.
  - **Qualidade dos dados** — completude por dia e por variável, valores impossíveis, sensores travados e tensão da bateria.
- **A regra de cada variável é fixa e vem escrita na tela.** A máxima do dia é a maior das máximas horárias, nunca a média delas; a chuva é soma; e assim por diante, como a equipe definiu.
- **A escala de cores do painel é da grandeza, e não do dado do momento:** temperatura de 0 a 45 °C, umidade de 0 a 100%, vento de 0 a 130 km/h e chuva por classes. Assim a mesma cor quer dizer a mesma coisa em qualquer mapa. A pressão fica ajustada ao dado, por decisão da equipe.
- O CSV traz as 29 colunas que a API devolve, com os códigos do INMET.
- O que já foi baixado fica em `cache/`, e as estações são baixadas 8 de cada vez: uma semana de MS, que levava cerca de 50 s, sai em cerca de 4 s. Os produtos continuam baixando direto da API, sem cache, para nunca trabalharem com dado velho.

## [0.1.3] — 22/09/2026

### Atenção ao atualizar

- **`--inicio` e `--fim` passam a se chamar `--dataini` e `--datafim`**, na mesma família de `--hrini` e `--hrfim`. Os nomes antigos deixam de funcionar, então comandos anotados precisam ser atualizados.
- Os títulos dos mapas do `relatorio_inmet` mudaram (ver Mudou).

### Novo

- **Produto `risco_fogo`**, pela regra 30-30-30: temperatura máxima ≥ 30 °C, umidade relativa mínima ≤ 30% e rajada ≥ 30 km/h. O resultado é um nível de 0 (nenhuma condição) a 3 (todas).
  - A regra é avaliada **hora a hora**, porque os extremos de um dia acontecem em horários diferentes, e calor, seca e vento precisam acontecer juntos.
  - No mapa interpolado, as três variáveis são interpoladas separadamente e a regra é aplicada em cada célula.
  - Saem mapas de nível máximo e de horas em risco alto, pontuais e interpolados, e um mapa horário para cada hora em que alguma estação chegou ao risco alto. `--hrtodas` gera o mapa de todas as horas.
  - Nos mapas horários, cada estação ganha um ponto por condição atendida: roxo para temperatura, azul para umidade e verde para rajada. Cada condição fica sempre na mesma posição, para quem não distingue as cores.
  - A planilha traz o primeiro horário em risco médio e em risco alto.
  - Aceita data específica, tempo real e período. No período, a planilha ganha as colunas "Dias com Risco Alto" e "Dias com Risco Médio", e saem três gráficos: as horas em cada nível por estação, um calendário estação × dia e as condições atendidas em cada dia.
- **As consultas ao INMET se repetem sozinhas**, até 3 vezes, quando a falha é passageira (conexão encerrada ou erro do servidor). Antes, a estação afetada sumia do relatório.
- **Resumo das estações que ficaram de fora**, separado por motivo: "sem leituras no período" ou "falha na consulta". As do segundo grupo podem ter dado, e vale repetir mais tarde. Se nenhuma estação devolver dado, o produto para com erro, em vez de gerar planilha e mapas vazios.
- README organizado por produto, com mapas de exemplo, e a seção "Como atualizar".

### Mudou

- **Títulos dos mapas do `relatorio_inmet` na forma curta**, como os do `risco_fogo`: "Temperatura Mín. em MS", "Umidade Rel. Mín. em MS", "Chuva Acumulada em 24 h em MS", "Rajadas de Vento em MS".
- **Mesmo subtítulo nos dois produtos**: só as datas quando a janela cobre dias inteiros, e horário com fuso (GMT-04) quando ela começa ou termina no meio do dia.
- O crédito no título dos mapas passa a ser "INMET/SEMADESC".

## [0.1.2] — 15/09/2026

### Novo

- **`--hrini` e `--hrfim`** (ou `HORA_INICIAL` e `HORA_FINAL` no `main.py`): hora de início e de fim da consulta, em horas cheias de 0 a 24 no horário de MS. Servem, por exemplo, para consultar das 08 h de um dia às 08 h do seguinte. Não valem com `--tempo-real`.
  - Com horário, a pasta de saída leva as horas no nome, a chuva sai como "Acumulado Período" e o título do mapa traz a duração em horas.
- **O tempo real ganha o mapa de chuva de 72 h**, pontual e interpolado.

### Corrigido

- As mensagens de erro da linha de comando saem em UTF-8, com os acentos legíveis.

## [0.1.1] — 15/09/2026

Primeira versão estável, já com as decisões da equipe de meteorologia de 15/09.

### Atenção ao atualizar

- **O dia passa a ser o de MS, da 00 h às 24 h**, e não mais o dia em UTC. Uma consulta de data específica ou de período passa a pegar horas diferentes das de antes.
- **Os resultados mudam de pasta**: ficam em `saida/<produto>/<modo>/<datas>/`, e não mais em `saida/<modo>/<datas>/`.

### Novo

- `--produto` (ou `PRODUTO` no `main.py`), preparando a chegada de novos produtos. O relatório passa a se chamar `relatorio_inmet`, e os scripts originais ficam em `legado/`, para comparação.

### Mudou

- **Tempo real**: a temperatura mínima passa a ser das últimas 24 h, como as outras variáveis (antes contava desde as 00 UTC, 20 h da véspera em MS). "Chuva Hoje" conta desde a 00 h de MS, e títulos e pastas saem no horário de MS.
- **Os mapas de chuva incluem as estações com 0 mm**, no pontual e no interpolado. Um dia sem chuva em nenhuma estação continua gerando os mapas.
- **A interpolação mede distância em quilômetros**, e não em graus.
- Mapas mais rápidos: os limites municipais foram simplificados sem diferença visível, e os 11 mapas de um dia caíram de 11,6 s para 7,3 s.

### Corrigido

- **Os mapas interpolados vão até a divisa do estado.** A superfície deixava falhas em degrau junto às bordas: 448 das 5.282 células que tocam MS ficavam sem cor.
- Os logos dos mapas saem com fundo transparente e na mesma posição e proporção nos mapas pontuais e interpolados.

## Antes da 0.1.1

14/09/2026: os scripts do relatório, que eram arquivos soltos, viraram um programa só, com o `main.py` e a pasta `modulos/`.
