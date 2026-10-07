# Escopo da v0.3.1 — aba de previsão para MS

> Escrito em 06/10/2026 e atualizado em 07/10, com as decisões tomadas pelo programador e pela
> equipe de meteorologia entre 05 e 07/10. Os custos foram **medidos** contra a API do
> Open-Meteo em 05 e 06/10, e não tirados da documentação, salvo onde dito. Este documento
> registra o que foi decidido e por quê; nada daqui está implementado ainda.

## O que muda, em uma frase

O painel passa a mostrar também o que **vai** acontecer: uma aba de previsão para Mato Grosso do
Sul, com três modelos até 14 dias e a anomalia semanal até 6 semanas.

## A ideia central

"Algumas semanas à frente" são dois produtos diferentes, porque a previsão muda de natureza no
meio do caminho:

| Horizonte | O que o modelo sabe dizer | O que a aba mostra |
|---|---|---|
| **Dias 1 a 14** | o valor de cada hora e de cada dia: "máxima de 34 °C na quinta" | mapas no padrão do boletim, dia a dia ou acumulados, e gráficos de linha por estação, um modelo ao lado do outro |
| **Semanas 1 a 6** | o desvio da semana em relação ao normal: "semana mais quente que o normal no sul do estado" | mapas de **anomalia semanal** |

**A aba não oferece mapa diário depois do dia 14.** Um mapa de "temperatura do dia 20" seria ruído
com cara de previsão.

O dado do modelo já vem em grade, então **não há interpolação**: a superfície é desenhada direto,
com o `mapas.mapa_de_grade` que já existe para "superfície pronta, recortada ao estado". O recorte
pelo contorno de MS, as cores, os logos e o botão do PNG do boletim continuam valendo.

## A fonte: Open-Meteo

Uma API só, em JSON e sem chave, dá acesso aos modelos que o Windy e o Zoom Earth exibem
(ECMWF, GFS, ICON e outros). Ela também tem o **EC46**, a previsão estendida do ECMWF, que já vem
com a anomalia semanal calculada contra a climatologia: era a parte mais trabalhosa do produto de
semanas, e ela chega pronta. É gratuita para uso não comercial, e o da equipe é. A licença dos
dados é CC BY 4.0, então **o crédito é obrigatório**: "Open-Meteo, ECMWF, NOAA, DWD" nos mapas e
na tela.

O que ficou de fora, e por quê:

- **Windy.** A chave gratuita devolve dados embaralhados de propósito, só para desenvolvimento.
  A profissional custa €990/ano, e a API de ponto não inclui o ECMWF. A de mapa só embute o
  visualizador deles, e não gera mapas no padrão do boletim.
- **Zoom Earth.** Não tem API oficial.
- **Arquivos direto do ECMWF e da NOAA (GRIB2).** Não têm cota, e um download cobre o Brasil
  inteiro. Mas pedem uma biblioteca a mais e mais código. É o caminho se um dia a previsão for
  para todos os estados.
- **CPTEC/INPE, previsão subsazonal.** Fica como reserva oficial para os mapas de semanas, caso o
  Open-Meteo falhe.

## As decisões

**1. Só MS.** Os 25 estados custariam ~11.700 chamadas por rodada, só nos mapas. Isso passa do
limite gratuito do dia inteiro (10 mil) numa única atualização.

**2. Três modelos: ECMWF IFS (0,25°), GFS e ICON**, escolhidos em 07/10 por serem os que a equipe
de meteorologia prefere. O ECMWF e o GFS vão até o dia 14 com todas as variáveis. **O ICON vai só
até o dia 7,5**: do dia 8 ao 14, os gráficos e o seletor de mapas mostram dois modelos.

Os outros candidatos, medidos num ponto de Campo Grande em 07/10:
- o **AIFS**, o modelo de IA do ECMWF, chega ao dia 14 mas **não traz rajada**, e parte do mesmo
  estado inicial do IFS;
- o **GEM** (Canadá) traz tudo, mas para no dia 10;
- o **JMA** (Japão) para no dia 11 e também não traz rajada.

**3. 14 dias, e não 15.** O ECMWF e o GFS chegam ao dia 15, mas o 15º dia faria cada ponto
pesar 15/14, porque passa do limite em que o Open-Meteo começa a cobrar pelos dias.

**4. Sete variáveis horárias, de onde saem as nove do boletim.** As variáveis do INMET, menos
radiação e pressão:

| Variável horária pedida | O que sai dela, por dia |
|---|---|
| temperatura | máxima, mínima e média |
| umidade relativa | mínima |
| ponto de orvalho | médio |
| chuva | soma do dia |
| velocidade do vento | máxima |
| rajada | máxima |
| direção do vento | dominante, como a resultante das horas já calculada em `app/variaveis.py` |

São 7 e não 9 porque, por hora, só existem 7 grandezas diferentes: máxima, mínima e média do dia
saem todas da mesma temperatura. Elas são exatamente as que o INMET mede, menos radiação e
pressão. Pedir 9 horárias repetiria variáveis e faria cada ponto custar 2,7 em vez de 2,1.

O dia é somado pela regra do projeto: a leitura das 00h fecha o dia anterior, como no INMET.
Assim previsto e observado ficam comparáveis. O diário pronto do Open-Meteo vai das 00h às 23h,
uma hora deslocado. Os nomes dos mapas seguem o catálogo do painel onde houver equivalente.

Um cuidado: o ECMWF só publica de 3 em 3 horas no começo e de 6 em 6 depois, e o Open-Meteo
preenche as horas do meio. Nos dias mais distantes, a máxima tirada das horas pode ficar um pouco
abaixo do pico. Isso é medido na primeira busca de verdade, comparando com a máxima diária do
próprio Open-Meteo.

**5. Grade de 0,25° no coletor agendado; 0,5° enquanto a busca for sob demanda.** A margem é
igual ao espaçamento: uma fileira de pontos além da divisa, sem a qual a cor não chega até a
borda.

| Pontos pedidos | Pontos | Comentário |
|---|---|---|
| retângulo inteiro do mapa (0,5°) | 272 | inclui cantos que o recorte esconde |
| só dentro do estado (0,5°) | 124 | a borda fica sem cor |
| estado + margem de 0,5°, grade de 0,5° | 178 | cabe num pedido só, de uns 2 segundos |
| **estado + margem de 0,25°, grade de 0,25°** | **607** | **o espaçamento do próprio ECMWF e do GFS** |

A 0,25° um mapa custa mais que o limite de 600 chamadas por minuto, então precisa ser dividido
em pedidos com um minuto entre eles. Num coletor agendado isso não incomoda ninguém; numa busca
sob demanda, a primeira pessoa esperaria uns 2 minutos. O espaçamento é uma constante em
`config.py`.

A diferença foi vista em 06/10, desenhando a mesma previsão do ECMWF para 10/10 de três jeitos,
com as cores e as escalas do painel:

- **Na chuva, a grade de 0,25° mostrou dois núcleos de até 38 mm no oeste do estado.** A de 0,5°
  os desfez numa mancha fraca, com máximo de 31 mm. Desenhada só nas estações, com IDW, sobrou um
  núcleo só, o que caía em cima de uma estação (34 mm), e o outro sumiu. A diferença média para a
  grade de 0,25° foi de 17% (0,5°) e 27% (estações) da chuva média.
- **Na temperatura máxima, os três deram o mesmo quadro.** A diferença média foi de 0,4 °C (0,5°)
  e 0,7 °C (estações), com as "bolhas" do IDW em volta de cada estação.

Por isso os mapas **não** são feitos interpolando a previsão nos pontos das estações: o IDW só
mostra um núcleo de chuva onde há estação. Os pontos das estações servem aos gráficos de linha e,
mais tarde, ao previsto contra observado.

**6. Guardar as horas, por rodada, só inserindo, e o diário tirado delas** (decidido em 07/10).
Duas tabelas, as duas com uma coluna por variável:

```
previsao_horaria
  modelo, rodada_utc, ponto, latitude, longitude, hora_prevista_utc,
  temperatura, umidade, orvalho, chuva, vento, rajada, direcao
  chave: (modelo, rodada_utc, ponto, hora_prevista_utc)

previsao_diaria
  modelo, rodada_utc, ponto, latitude, longitude, dia_previsto,
  temp_max, temp_min, temp_media, umid_min, orvalho_medio, chuva, vento_max, rajada_max,
  direcao_dominante, risco_max
  chave: (modelo, rodada_utc, ponto, dia_previsto)
```

- **Cada rodada nova é um insert; nada é atualizado.** A "previsão atual" é uma consulta, ou uma
  view, que pega a rodada mais recente de cada modelo. A virada do dia não pede tratamento
  nenhum, e uma busca que falhe no meio não mistura rodadas.
- **O diário é calculado das horas pelo coletor**, uma vez por rodada, pela regra da decisão 4, e
  gravado na tabela diária. A mesma função faz isso para todos os pontos: não há duas versões de
  "máxima do dia". Ele sobrevive à limpeza das horas antigas da grade.
- **O histórico das rodadas fica**: o que cada rodada previa para um dia que já passou. É a base
  do previsto contra observado.
- **Uma coluna por variável, e não uma linha por variável**, senão as linhas se multiplicam
  por 7.
- **Particionada por dia de coleta.** A limpeza vira apagar a partição mais antiga, o que é
  instantâneo.

Quanto guardar:

| O quê | Prazo | Por quê | Tamanho |
|---|---|---|---|
| Grade, horária e diária (0,25°) | **21 dias** | 14 de horizonte + uma semana de folga para conferir a semana que passou com todas as antecedências | ~21 milhões de linhas horárias, ~3 GB |
| Estações, horária | **para sempre** | permite conferir horários: "a chuva chegou na hora prevista?" | ~37 milhões de linhas e ~4 GB por ano |
| Estações, diária | **para sempre** | é o que a verificação do dia a dia mais usa | ~1,5 milhão de linhas por ano |

A previsão incha por um motivo próprio: **cada hora futura é guardada uma vez por rodada**. Com 14
dias de horizonte e duas rodadas por dia, cada hora real fica guardada umas 28 vezes, e esse é o
histórico de como a previsão mudou até a hora chegar. O observado do INMET não tem isso: o Brasil
inteiro dá uns 6,5 milhões de linhas por ano com as estações de hoje, e menos nos anos antigos,
quando havia menos estações. O banco da UNIGEO comporta as duas coisas com folga.

**7. A busca mora fora do painel**, num módulo novo, `modulos/openmeteo.py`, ao lado do
`inmet.py`: cada fonte tem o seu cliente, com o mesmo papel. As contas da aba que não são busca
(rodada atual de cada modelo, séries dos gráficos, os mapas) ficam em `app/previsao.py`, sem
tela, para poderem ser testadas. No servidor, um coletor agendado chama essa função e grava no banco. Enquanto o
servidor não existir, o painel chama a mesma função sob demanda, com a cópia guardada por rodada
e compartilhada por todos. A chave é a **rodada** ("ECMWF de 06/10, 00 UTC"), e não o relógio,
porque cada modelo publica a rodada horas depois do horário nominal. A aba mostra de que rodada é
cada modelo.

**8. Risco de fogo previsto, hora a hora, das mesmas horas.** A temperatura, a umidade e a
rajada da regra 30-30-30 estão entre as sete variáveis horárias: não há pedido separado. A regra é
aplicada **a cada hora**, como no produto, e o pior nível do dia vai para a tabela diária. Ela já
existe em `modulos/produtos/risco_fogo.py` e é usada como está, sem reescrever. Do dia 8 ao 14,
sem o ICON, sai de dois modelos.

Um cuidado conhecido: na estação, a regra usa a **máxima dentro da hora** (`TEM_MAX`) e a
**mínima dentro da hora** (`UMD_MIN`) do INMET. O modelo dá o **valor da hora cheia**, que fica
um pouco aquém dos extremos da hora. O risco previsto tende, então, a ser levemente mais
conservador que o observado. O previsto contra observado vai mostrar quanto.

**9. Escala de cores.** Os mapas de dias usam a mesma escala fixa dos mapas observados (0 a 45 °C
etc.), para previsto e observado serem comparáveis. Os de anomalia pedem uma escala nova,
**divergente**: azul abaixo do normal, branco no normal, vermelho acima.

## O custo, medido

Como o Open-Meteo conta, conforme os testes de 05 e 06/10:

- **Cada ponto é uma chamada.** Um pedido com 650 pontos passou, e o pedido seguinte do mesmo
  minuto foi recusado. O limite (600 por minuto) é conferido **antes** de somar o custo do pedido.
- **Num pedido com vários modelos, as variáveis contam por modelo.** 7 variáveis × 3 modelos
  pesam como 21, ou seja, 2,1 vezes. Pedir o ICON junto sai mais barato que separado, mesmo com
  ele parando no dia 7,5: 2,1 contra 2,4.
- **Mais de 14 dias pesa** na proporção. Por isso a aba usa 14 (decisão 3).
- A API não informa quanto já foi gasto. Só se descobre o limite ao chegar nele.

Em resumo, pela documentação e de acordo com o que foi medido:

```
custo = pontos × máx(1, variáveis × modelos ÷ 10) × máx(1, dias ÷ 14)
```

O que não entra na conta é quantos números voltam. **Que o dado horário custa o mesmo que o diário
vem da regra documentada e não foi medido**: confere-se na primeira busca de verdade.

Para MS, com 3 modelos, 7 variáveis horárias e 14 dias, cada ponto custa 2,1:

| Produto | Sob demanda, 0,5° | Agendado, 0,25° |
|---|---|---|
| Grade (mapas e risco de fogo), 2 rodadas | ~750 | ~2.550 |
| Gráficos de linha nas 59 estações, 2 rodadas | ~250 | ~250 |
| Anomalia semanal do EC46, 46 dias, a 0,5°, 1 por dia | ~590 | ~590 |
| **Total por dia** | **~1.600 (16% da cota)** | **~3.400 (34% da cota)** |

## Ordem de trabalho

1. **`modulos/openmeteo.py`.** A busca: os pontos da grade e as estações de MS, os três modelos, a
   identificação da rodada, a divisão em pedidos de até 600 chamadas por minuto, e o aviso quando
   o Open-Meteo não responder ou a cota acabar. Os testes usam um Open-Meteo simulado, como o
   INMET já é, sob a mesma trava que proíbe rede nos testes. Na primeira busca de verdade:
   conferir se o horário custa o mesmo que o diário, e quanto a máxima tirada das horas fica
   abaixo da do próprio Open-Meteo.
2. **A tabela e o coletor**, se o servidor e o banco já existirem. Se não, o cache por rodada no
   painel.
3. **Gráficos de linha por estação**, com as contas em `app/previsao.py` e a aba no
   `explorador.py`. Os três modelos lado a lado. É o passo mais barato, e o que
   mostra se a fonte serve.
4. **Mapas de dias**, de 1 a 14. Um modelo por vez, com um seletor para trocar, e o PNG do
   boletim.
5. **Mapas de anomalia semanal** do EC46.
6. **Risco de fogo previsto.**

## Encaixe com a ida para o servidor da UNIGEO

Em 06/10 ficou decidido levar o painel para um servidor da UNIGEO, num container Docker, e os
dados para o banco da unidade, administrado pelo programador. Cada fonte terá o seu coletor e a
sua tabela: o INMET do Brasil inteiro, de hora em hora, e o Open-Meteo de MS. O prazo do servidor
ainda está aberto.

Em 07/10 ficaram definidos mais dois pontos:
- **O banco é PostgreSQL.** O particionamento por dia de coleta usa o particionamento nativo dele.
  As rodadas entram em lote (`COPY`), porque são ~500 mil linhas cada. A atualização das últimas
  48 h do INMET usa `INSERT ... ON CONFLICT DO UPDATE`.
- **O `Dockerfile` e o `docker-compose.yml` ficam no repositório.** O compose sobe o painel e os
  coletores. O banco já existe na unidade e entra só pela conexão, lida do `.env` do servidor.

Os arquivos novos dessa parte: `modulos/banco.py` (o único que fala SQL), `modulos/fonte.py` (lê
da API ou do banco), `coletores/coletar_inmet.py`, `coletores/coletar_previsao.py`,
`banco/esquema.sql`, `Dockerfile` e `docker-compose.yml`.

- **A busca da decisão 7 é o coletor do Open-Meteo**, e a tabela da decisão 6 é a dele.
- **Se o servidor e o banco chegarem antes do passo 4**, a aba já nasce lendo do banco, e o cache
  sob demanda nem chega a ser escrito.
- **No servidor, a busca é agendada.** A primeira pessoa deixa de esperar, e a grade de 0,25°
  passa a caber.

## Fora do escopo

- **Outros estados**, por causa do custo (decisão 1).
- **Previsão na linha de comando** (`main.py`). Pode virar um produto depois que a aba estiver
  validada pela equipe.
- **Previsto contra observado.** A comparação com as estações do INMET, para a equipe saber em
  que modelo confiar em MS, fica para a versão seguinte. Os dados para ela começam a ser
  guardados já nesta (decisão 6).
- **Um visualizador animado como o do Windy.**

## Riscos

- **A cota pode estar sendo dividida com outros apps na nuvem.** O limite do Open-Meteo é por
  endereço de saída, e no Streamlit Community Cloud os apps provavelmente saem por endereços
  compartilhados. Isso não foi medido. Se acontecer, a aba recebe "limite excedido" sem ter
  gastado nada. No servidor da UNIGEO o risco diminui, porque o endereço é o da unidade.
- **O Open-Meteo gratuito não garante disponibilidade.** A aba avisa e mostra a última rodada
  guardada, com a data dela.
- **O crédito é obrigatório** (CC BY 4.0), nos mapas e na tela.
