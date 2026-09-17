# Questões para a equipe de meteorologia

Durante a reestruturação dos scripts (versão 0.1.1), alguns pontos ficaram em aberto porque **mudam os resultados** e dependem de uma decisão meteorológica. Para cada um estão a situação atual, o problema e as opções. As respostas podem ser anotadas no campo **Decisão**.

> **Situação em 17/09/2026:** as questões 1 a 5 (versão 0.1.1) foram respondidas e já estão aplicadas no código. Nas questões 6 a 8, do produto `risco_fogo`, a equipe confirmou as aproximações de método (questões 6 e 7); continuam marcados com ⚠️ apenas os limiares e o critério de gerar mapa horário, na questão 8.

Nas descrições abaixo, os horários estão em UTC (horário de MS = UTC−4).

---

## 1. Estações com 0 mm nos mapas de chuva

**Como está hoje:** os mapas de chuva (pontual e interpolado) usam apenas as estações com acumulado maior que zero. As estações que registraram 0 mm são descartadas antes da interpolação.

**Problema:** sem os zeros, a interpolação não "sabe" onde não choveu e espalha a chuva das estações vizinhas sobre as áreas secas. Num dia com chuva em apenas 3 estações, por exemplo, o estado inteiro aparece colorido com os valores dessas 3. Com menos de 3 estações com chuva, o mapa interpolado nem é gerado.

**Pergunta:** as estações com 0 mm devem entrar nos mapas de chuva?

- **(a)** Incluir os zeros na interpolação — o mapa passa a mostrar onde não choveu.
- **(b)** Incluir os zeros e deixar sem cor as áreas com chuva interpolada abaixo de um limiar (ex.: 0,2 mm).
- **(c)** Manter como está.

E no mapa pontual: as estações com 0 mm devem aparecer (por exemplo, com um marcador cinza)?

**Decisão (15/09/2026):** opção (a) — as estações com 0 mm entram na interpolação e aparecem no mapa pontual.

---

## 2. Quais leituras horárias formam "o dia"

**Como está hoje:** nos modos data específica e período, o dia D é formado pelas leituras das 00 UTC até as 23 UTC do dia D. A mesma regra vale para chuva e temperaturas.

**Por que importa:** cada leitura horária do INMET se refere à hora que **termina** no horário indicado — a leitura das 00 UTC contém, por exemplo, a chuva entre 23 e 00 UTC do dia anterior (a confirmar). Pela regra atual, o dia D inclui a última hora do dia anterior e deixa de fora a última hora do próprio dia.

**Pergunta:** qual convenção de dia usar?

- **(a)** Manter: leituras das 00 às 23 UTC do dia D.
- **(b)** Leituras das 01 UTC do dia D às 00 UTC do dia D+1 — cobre exatamente as 24 h do dia D em UTC.
- **(c)** Dia no horário de MS: leituras das 05 UTC do dia D às 04 UTC do dia D+1 (da 00 h às 24 h locais).
- **(d)** Dia pluviométrico (12 às 12 UTC, horário de leitura das estações convencionais): leituras das 13 UTC do dia anterior às 12 UTC do dia D.

**Decisão (15/09/2026):** opção (c) — dia no horário de MS: leituras das 05 UTC do dia D às 04 UTC do dia D+1.

---

## 3. Janela da temperatura mínima no relatório diário (tempo real)

**Como está hoje:** no modo tempo real, a temperatura mínima é calculada desde as 00 UTC do dia atual (20 h do dia anterior no horário de MS) até o momento da execução. As demais variáveis (máxima, umidade e rajada) usam as últimas 24 horas. Essa é a regra do script original `coleta_diaria.py`.

**Problema:** o resultado depende do horário em que o relatório é gerado. Executado às 11 UTC (7 h em MS), a janela cobre a madrugada inteira; executado às 01 UTC (21 h em MS), cobre só uma hora.

**Pergunta:** qual janela usar para a temperatura mínima?

- **(a)** Manter "desde 00 UTC do dia atual".
- **(b)** Últimas 24 horas, como as demais variáveis.
- **(c)** Uma janela fixa, como a da climatologia (ex.: das 12 UTC do dia anterior às 12 UTC do dia).

Em que horário o relatório diário costuma ser gerado?

**Decisão (15/09/2026):** opção (b) — últimas 24 horas, como as demais variáveis, com os horários exibidos no horário de MS. Por coerência com a questão 2, a coluna "Chuva Hoje" passou a contar desde a 00 h de MS.

---

## 4. Distâncias na interpolação (IDW)

**Como está hoje:** a interpolação usa o inverso do quadrado da distância (IDW) com as 8 estações mais próximas, e a distância é calculada diretamente em graus de latitude e longitude.

**Problema:** em MS, 1° de longitude equivale a ~104 km e 1° de latitude a ~111 km. Medir em graus distorce as distâncias em ~7%: estações a leste e a oeste pesam um pouco mais do que deveriam em relação às estações ao norte e ao sul.

**Perguntas:**

- Podemos passar a calcular as distâncias em quilômetros? A mudança nos mapas é pequena, mas altera os valores interpolados.
- Os parâmetros atuais (potência 2, 8 vizinhos, grade de ~8 km) estão adequados, ou há outro método preferido (ex.: krigagem)?

**Decisão (15/09/2026):** medir as distâncias em quilômetros. Os demais parâmetros (potência 2, 8 vizinhos, grade de ~8 km) foram mantidos.

---

## 5. Confirmar mudanças já aplicadas

Estas mudanças já estão no código (detalhes no README, seção "Mudanças em relação aos scripts legados") e precisam só de confirmação:

- **Extremos do período:** o script original incluía o dia seguinte à data final no cálculo das temperaturas, da umidade e da rajada. Agora os extremos ficam restritos ao período.
- **Chuva na data específica:** as colunas "Acumulado 24h" e "Acumulado 48h" foram removidas — a de 48 h somava só cerca de 24 h de dados. Ficou apenas o "Acumulado Dia". Querem de volta um acumulado de 48 h (dia anterior + dia consultado) calculado corretamente?
- **Mapa de rajadas com direção:** agora é gerado em todos os modos (antes, só no diário). Faz sentido também para data específica e período?
- **Scripts originais:** estão em `legado/` para comparação. Depois da validação, podem ser removidos?

**Decisão (15/09/2026):** extremos do período confirmados; sem o acumulado de 48 h por enquanto; mapa de rajadas com direção confirmado; `legado/` mantido até decisão sobre a exclusão.

---

# Produto `risco_fogo` (versão 0.1.4)

Produto **novo**, pedido pela equipe de meteorologia: mapa de risco meteorológico de fogo pela regra apelidada de **30-30-30**. Não veio de script legado — foi desenhado do zero, então as questões abaixo registram o desenho, e não uma migração.

A regra: **temperatura máxima ≥ 30 °C**, **umidade relativa mínima ≤ 30 %** e **rajada ≥ 30 km/h**. Cada condição atendida soma um nível: 0 (sem condição), 1 (baixo), 2 (médio), 3 (alto).

As decisões foram tomadas com o programador em 16/09/2026. Os pontos marcados com ⚠️ **precisam de confirmação da meteorologia**. Nesta parte, os horários citados estão no horário de MS.

---

## 6. Simultaneidade das três condições

**O problema:** os extremos de um período acontecem em horários diferentes. Uma estação pode marcar 32 °C às 15 h, 28 % de umidade às 18 h e rajada de 35 km/h às 03 h da madrugada. Contando os extremos do dia, ela seria classificada como "risco alto" — mas em nenhum momento houve calor, seca e vento ao mesmo tempo, que é o que importa para fogo.

**Decisão (16/09/2026):** avaliar a regra **hora a hora**. Cada leitura horária do INMET traz a temperatura máxima, a umidade mínima e a rajada **daquela hora**; as três condições são testadas dentro de cada hora e só depois agregadas na janela consultada.

**Confirmado (17/09/2026):** a equipe aceita a aproximação. Dentro de uma mesma hora o resultado não é rigorosamente simultâneo — a máxima e a mínima daquela hora podem estar separadas por dezenas de minutos —, mas é a janela mais fina que a API oferece.

---

## 7. Como interpolar o nível de risco

**O problema:** interpolar diretamente o nível 0–3 de cada estação produz valores sem significado físico ("1,7 condições atendidas") e borra o mapa — uma estação em nível 3 ao lado de outra em nível 0 gera ~1,5, pintando "risco médio" num lugar onde nenhuma das duas indicou risco médio.

**Decisão (16/09/2026):** interpolar as **três variáveis separadamente** — cada uma é um campo físico contínuo, que é para o que o IDW serve — e aplicar a regra 30-30-30 **célula a célula** na grade. As fronteiras entre as classes passam a cair exatamente onde cada limiar é cruzado.

**Confirmado (17/09/2026):** a equipe aceita a limitação, e a condição de vento continua entrando na superfície interpolada. Fica o registro: vento de rajada é muito local (convectivo), então a superfície de vento é bastante menos confiável que as de temperatura e umidade.

**Consequência visual, para não ser confundida com erro:** o mapa pontual e o interpolado podem discordar. A superfície pode mostrar nível 2 numa célula onde nenhuma estação está em nível 2, porque ela deriva dos campos físicos interpolados, e não dos níveis das estações.

---

## 8. Modos, mapas e planilha

**Decisões (16/09/2026):**

- **Modos:** **tempo real** e **data específica**. *(Revisto em 17/09/2026: o período passou a ser aceito também — veja a decisão no fim desta questão.)*
- **Mapas de síntese** (sempre gerados): **nível máximo atingido** e **número de horas em risco alto**, cada um em versão pontual e interpolada.
- **Mapas horários:** apenas interpolados — gerar pontual e interpolado por hora dobraria para 48 arquivos num único dia. Por padrão, só são gerados nas horas em que **pelo menos uma estação** atingiu o nível 3 (risco alto); com a opção `--hrtodas`, saem todas as horas disponíveis, independentemente do nível. *(Até 17/09/2026 o critério era o nível 2; veja a decisão no fim desta questão.)*
- **Peso das condições:** as três pesam igual.
- **Estação sem uma das três variáveis:** fica de fora do mapa e é listada no resumo. Incluí-la subestimaria o risco, já que ela nunca poderia alcançar o nível 3.
- **Cores:** cinza (0), amarelo (1), laranja (2), vermelho (3). O par verde/vermelho foi evitado por causa de daltonismo.
- **Planilha:** por estação, o nível máximo, quantas horas em cada nível e os valores com horário que dispararam cada condição — é o que permite auditar por que uma estação ficou vermelha.

**Decisão (17/09/2026):** nos mapas horários, cada estação mostra o seu nível **naquela hora** e, abaixo do número, um ponto para cada condição atendida — roxo para temperatura, azul para umidade e verde para rajada —, sempre na mesma posição (temperatura à esquerda, umidade no meio, rajada à direita), para que a condição seja identificável mesmo por quem não distingue as cores. Os mapas de síntese (nível máximo e horas em risco alto) não recebem os pontos, porque juntam horas diferentes.

**Decisão (17/09/2026):** os mapas horários passam a ser gerados **só nas horas com alguma estação em risco alto** (nível 3) — antes bastava o nível 2. A opção `--hrtodas` continua gerando todas as horas. Em compensação, o acompanhamento dos níveis intermediários fica na planilha, que conta as horas nos três níveis e ganhou a coluna `Primeiro Horário em Risco Médio (MS)`, ao lado da que já existia para o risco alto: assim dá para ver quando o risco começou a subir, mesmo nos dias que não chegam ao nível 3. Os mapas de "horas em risco alto" continuam sendo gerados sempre, mesmo zerados.

**Decisão (17/09/2026):** o produto passa a aceitar **período** também. Tudo funciona como no dia, somando os dias: o mapa de nível máximo traz o pior nível de cada lugar em todo o período, o de horas agregadas soma as horas em risco alto, e os mapas horários continuam saindo em todas as horas com risco alto — um mês movimentado pode passar de 200 arquivos. A planilha ganha, só no período, as colunas `Dias com Risco Alto` e `Dias com Risco Médio`. Os gráficos quantitativos (estação × condições atendidas) continuam previstos, como uma entrega à parte.

⚠️ **A confirmar:** os limiares (≥ 30 °C, ≤ 30 %, ≥ 30 km/h) e o critério de gerar mapa horário só a partir do risco alto.
