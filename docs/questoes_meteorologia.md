# Questões para a equipe de meteorologia

Durante a reestruturação dos scripts (versão 0.1.1), alguns pontos ficaram em aberto porque **mudam os resultados** e dependem de uma decisão meteorológica. Para cada um estão a situação atual, o problema e as opções. As respostas podem ser anotadas no campo **Decisão**.

> **Situação em 15/09/2026:** todas as questões foram respondidas. As decisões estão anotadas abaixo e aplicadas no código (versão 0.1.1).

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
