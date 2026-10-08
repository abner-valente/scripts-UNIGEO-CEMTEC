# Escopo da v0.3.1 — previsão para MS

> Escrito em 06/10/2026 e atualizado até 08/10, com as decisões tomadas pelo programador e pela
> equipe de meteorologia entre 05 e 07/10. Os custos foram **medidos** contra a API do
> Open-Meteo em 05 e 06/10, e não tirados da documentação, salvo onde dito. Este documento
> registra o que foi decidido e por quê. O que já está feito vem marcado na
> [ordem de trabalho](#ordem-de-trabalho).

## O que muda, em uma frase

O painel passa a mostrar também o que **vai** acontecer: uma página de previsão para Mato Grosso
do Sul, com três modelos até 14 dias e a anomalia semanal até 6 semanas.

## A ideia central

"Algumas semanas à frente" são dois produtos diferentes, porque a previsão muda de natureza no
meio do caminho:

| Horizonte | O que o modelo sabe dizer | O que a página mostra |
|---|---|---|
| **Dias 1 a 14** | o valor de cada hora e de cada dia: "máxima de 34 °C na quinta" | mapas no padrão do boletim, dia a dia ou acumulados, e gráficos de linha por estação, um modelo ao lado do outro |
| **Semanas 1 a 6** | o desvio da semana em relação ao normal: "semana mais quente que o normal no sul do estado" | mapas de **anomalia semanal** |

**A página não oferece mapa diário depois do dia 14.** Um mapa de "temperatura do dia 20" seria ruído
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

O ECMWF só publica de 3 em 3 horas no começo e de 6 em 6 depois, e o Open-Meteo preenche as
horas do meio. Temia-se que, nos dias mais distantes, a máxima tirada das horas ficasse abaixo do
pico. **Medido em 07/10, não fica:** em 20 pontos de MS, nos 14 dias e nos três modelos, a máxima
diária do próprio Open-Meteo foi igual à máxima das horas, com diferença de 0,0 °C. O diário pronto
do Open-Meteo é tirado das mesmas horas; tirá-lo aqui não perde nada.

**5. Grade de 0,25° no coletor agendado; 0,5° enquanto a busca for sob demanda.** A margem é
igual ao espaçamento: uma fileira de pontos além da divisa, sem a qual a cor não chega até a
borda.

| Pontos pedidos | Pontos | Comentário |
|---|---|---|
| retângulo inteiro do mapa (0,5°) | 272 | inclui cantos que o recorte esconde |
| só dentro do estado (0,5°) | 124 | a borda fica sem cor |
| estado + margem de 0,5°, grade de 0,5° | 178 | cabe num pedido só: 12 segundos com as horas dos três modelos (07/10) |
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
`inmet.py`: cada fonte tem o seu cliente, com o mesmo papel. As contas da página que não são busca
(rodada atual de cada modelo, séries dos gráficos, os mapas) ficam em `app/previsao.py`, sem
tela, para poderem ser testadas. No servidor, um coletor agendado chama essa função e grava no banco. Enquanto o
servidor não existir, o painel chama a mesma função sob demanda, com a cópia guardada por rodada
e compartilhada por todos. A chave é a **rodada** ("ECMWF de 06/10, 00 UTC"), e não o relógio,
porque cada modelo publica a rodada horas depois do horário nominal. A página mostra de que rodada é
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

**10. Uma página própria, e não uma aba** (decidido em 07/10). A previsão não usa nenhum filtro do
observado (estado, período, estações). Numa aba, ela só apareceria depois de escolher estações do
passado, e o Streamlit executa todas as abas a cada clique: o Open-Meteo seria consultado até
por quem nunca olhasse a previsão. Como página, ao lado do "Observado (INMET)" na barra lateral,
ela tem os seus filtros e um endereço próprio (`…/previsao`), e só busca quando alguém a abre.

**11. Cada modelo é buscado sozinho** (decidido em 07/10). Os três atualizam de 6 em 6 horas, mas
cada rodada fica pronta numa hora diferente: em 07/10, a das 12 UTC ficou pronta às 15:35 UTC no
ICON, às 17:57 no GFS e às 19:42 no ECMWF. Num pedido só com os três, a rodada nova de qualquer um
refaria a busca inteira, a 2,1 chamadas por ponto. Sozinho, cada modelo custa 1 por ponto (7
variáveis não passam de 10) e só é buscado na vez dele. Na página, a cópia guardada é uma por
modelo (`app/previsao.py`, `Guarda`), e se o Open-Meteo falhar fica a última, com aviso.

## O custo, medido

Como o Open-Meteo conta, conforme os testes de 05 e 06/10:

- **Cada ponto é uma chamada.** Um pedido com 650 pontos passou, e o pedido seguinte do mesmo
  minuto foi recusado. O limite (600 por minuto) é conferido **antes** de somar o custo do pedido.
- **Num pedido com vários modelos, as variáveis contam por modelo.** 7 variáveis × 3 modelos
  pesam como 21, ou seja, 2,1 vezes. Pedir o ICON junto sai mais barato que separado, mesmo com
  ele parando no dia 7,5: 2,1 contra 2,4.
- **Mais de 14 dias pesa** na proporção. Por isso a página usa 14 (decisão 3).
- A API não informa quanto já foi gasto. Só se descobre o limite ao chegar nele.

Em resumo, pela documentação e de acordo com o que foi medido:

```
custo = pontos × máx(1, variáveis × modelos ÷ 10) × máx(1, dias ÷ 14)
```

O que não entra na conta é quantos números voltam. **O dado horário custa o mesmo que o diário,
medido em 07/10:** 280 pontos horários (588 pela regra) deixaram passar o pedido seguinte do
mesmo minuto, e 290 (609) o fizeram ser recusado, exatamente onde a regra põe o limite.

Para MS, com 3 modelos, 7 variáveis horárias e 14 dias, cada ponto custa 2,1:

| Produto | Sob demanda, 0,5° | Agendado, 0,25° |
|---|---|---|
| Grade de 0,5° dos mapas: cada modelo, a cada rodada dele | até ~2.100 | ~2.550 |
| Gráficos de linha nas 58 estações: cada modelo, a cada rodada dele | até ~700 | ~250 |
| Anomalia semanal do EC46, 46 dias, a 0,5°, 1 por dia | ~590 | ~590 |
| **Total por dia, no pior caso** | **~3.400 (34% da cota)** | **~3.400 (34% da cota)** |

As duas primeiras linhas mudaram em 08/10, com a decisão 11: são 178 (grade) e 58 (estações)
chamadas por modelo e rodada, e cada modelo tem até 4 rodadas por dia. É o pior caso: só gasta
isso se alguém abrir os mapas de cada modelo depois de cada rodada dele. A coluna do agendado
continua com as contas de 06/10 (duas rodadas, os três modelos juntos); o coletor da v0.4.x
decide de quais rodadas precisa.

## Ordem de trabalho

1. **`modulos/openmeteo.py`.** A busca: os pontos da grade e as estações de MS, os três modelos, a
   identificação da rodada, a divisão em pedidos de até 600 chamadas por minuto, e o aviso quando
   o Open-Meteo não responder ou a cota acabar. Os testes usam um Open-Meteo simulado, como o
   INMET já é, sob a mesma trava que proíbe rede nos testes. **Feito em 07/10.** A primeira
   busca de verdade de MS (rodadas de 07/10, 12 UTC): a grade de 0,5° (178 pontos) levou 12 s e
   deu 153.970 linhas horárias e 6.942 diárias; as 58 estações, 8 s, 50.170 e 2.262. O ICON dá
   pouco mais da metade das linhas dos outros dois, porque para no dia 7,5.
2. **A tabela e o coletor**, se o servidor e o banco já existirem. Se não, o cache por rodada no
   painel. **O cache ficou pronto em 08/10**, um por modelo (decisão 11); a tabela e o coletor
   ficam para a v0.4.x, com o banco.
3. **Gráficos de linha por estação**, com as contas em `app/previsao.py` e a página no
   `explorador.py`. Os três modelos lado a lado. É o passo mais barato, e o que
   mostra se a fonte serve. **Feito em 08/10**: temperatura, umidade, ponto de orvalho, vento,
   direção e chuva, por hora ou por dia, com a tabela diária em CSV.
4. **Mapas de dias**, de 1 a 14. Um modelo por vez, com um seletor para trocar, e o PNG do
   boletim. **Feito em 08/10**, numa página própria, ao lado da dos gráficos:
   - temperatura máxima, mínima e média, umidade mínima, chuva do dia, chuva acumulada de hoje
     até o dia escolhido, rajada e vento máximos com a seta da direção dominante;
   - **a superfície é a do modelo**, levada da grade de 0,5° à do mapa por interpolação
     **bilinear**. Comparadas no mesmo dia, a linear por triângulos deixava facetas retas e a
     cúbica inventava bolhas entre os pontos;
   - os números e o ranking são a previsão do mesmo modelo **no ponto de cada estação**;
   - o PNG do boletim leva "Open-Meteo (CC BY 4.0)/SEMADESC" no lugar do "INMET/SEMADESC".
5. **Mapas de anomalia semanal** do EC46.
6. **Risco de fogo previsto.**

## Encaixe com a ida para o servidor da UNIGEO

Em 06/10 ficou decidido levar o painel para um servidor da UNIGEO, num container Docker, e os
dados para o banco da unidade, administrado pelo programador. Cada fonte terá o seu coletor e a
sua tabela: o INMET do Brasil inteiro, de hora em hora, e o Open-Meteo de MS. O prazo do servidor
ainda está aberto.

Em 07/10 ficaram definidos mais dois pontos:
- **O banco é PostgreSQL**, versão 11.14, com PostGIS. O particionamento por dia de coleta usa o
  particionamento nativo dele, e é o coletor quem cria e apaga as partições, porque o banco não tem
  `pg_partman` nem `pg_cron` (detalhes em [`plano_arquitetura.md`](plano_arquitetura.md)).
  As rodadas entram em lote (`COPY`), porque são ~500 mil linhas cada. A atualização das últimas
  48 h do INMET usa `INSERT ... ON CONFLICT DO UPDATE`.
- **O `Dockerfile` e o `docker-compose.yml` ficariam no repositório**, com o painel e os
  coletores. **Adiado no mesmo dia:** a VM é Windows e o Docker não está rodando nela. Por ora, o
  painel e os coletores rodam direto no Windows, pelo Agendador de Tarefas (passo 8 do plano). O
  banco entra só pela conexão, lida do `.env` do servidor, nos dois casos.

A ordem de execução dessa parte está em [`plano_arquitetura.md`](plano_arquitetura.md). Os
arquivos novos dela: `modulos/banco.py` (o único que fala SQL), `modulos/fonte.py` (lê
da API ou do banco), `coletores/coletar_inmet.py`, `coletores/coletar_previsao.py`,
`banco/esquema.sql`, e o `Dockerfile` e o `docker-compose.yml` quando houver Docker no
servidor.

- **A busca da decisão 7 é o coletor do Open-Meteo**, e a tabela da decisão 6 é a dele.
- **Se o servidor e o banco chegarem antes do passo 4**, os mapas já nascem lendo do banco. O
  cache sob demanda, já escrito para os gráficos, continua valendo na nuvem.
- **No servidor, a busca é agendada.** A primeira pessoa deixa de esperar, e a grade de 0,25°
  passa a caber.

### Duas instalações: o servidor interno e o Streamlit Cloud

A ideia de partida da gerência é que o painel fique só na rede interna do IMASUL. O programador
prefere manter também uma versão na internet, e isso pode mudar. Por isso o código é pensado para
**rodar nos dois lugares ao mesmo tempo**: um repositório, duas instalações, cada uma com a sua
configuração. O problema é a versão da nuvem ler dados que estão atrás do firewall da unidade.
Foram consideradas quatro formas:

| Opção | Como a versão da nuvem lê os dados | Situação |
|---|---|---|
| 1. Direto das APIs | Como hoje: consulta o INMET e o Open-Meteo sozinha. Sem o histórico do banco, com a previsão a 0,5° sob demanda. | o ponto de partida: não pede nada novo |
| 2. O banco aberto para a internet | O app da nuvem faz consultas SQL no PostgreSQL da unidade | **descartada**: expõe o banco |
| **2 com API. Uma API só de leitura na frente do banco** | O app da nuvem pede à API, por HTTPS, com chave | **escolhida em 07/10**, se houver versão externa com os mesmos dados |
| 3. Uma cópia publicada na nuvem | O servidor envia uma cópia para fora, e a nuvem lê a cópia | a saída se a TI não aceitar nenhuma conexão de fora para dentro |

**Por que a API.** Ela já seria necessária de qualquer jeito: é o que deixa o `main.py`, nas
máquinas da equipe, ler do banco sem que cada máquina tenha a senha dele. Com a API pronta,
atender também o app da nuvem vira uma decisão de rede (deixá-la acessível de fora) e não um
software novo. E, comparada a abrir o banco:
- **o banco nunca fica exposto.** De fora, só se alcança a API, por HTTPS;
- **só existem as perguntas que foram escritas**, como "as leituras destas estações nesta janela"
  ou "a previsão atual deste modelo". Ninguém de fora manda SQL, e a API entra no banco com um
  usuário que só lê;
- **há controle de quem pede e quanto pede**: uma chave, guardada nos Secrets do Streamlit, um
  limite de pedidos e um registro dos acessos;
- **o banco pode mudar sem quebrar o painel**, porque o que o painel conhece é o formato das
  respostas.

**O que ela pede:**
- **A TI precisa liberar a API para a internet**, com HTTPS e um endereço próprio, e alguém
  cuida das atualizações e do monitoramento.
- **A chave é a principal proteção.** O Streamlit Cloud não sai por um endereço fixo, então não
  dá para liberar a API só para ele pelo endereço.
- **As respostas têm de ser enxutas.** A API entrega o que o painel vai desenhar, como o mapa de
  um dia (607 valores), e não a tabela crua de ~600 mil linhas de uma rodada.
- **A versão da nuvem passa a depender do servidor interno.** Se ele cair, ela cai junto. Na
  opção 3 isso não aconteceria.

**Como fica o código.** O `modulos/fonte.py` passa a ter três formas de buscar os dados, escolhidas
pela configuração de cada instalação:
- as APIs públicas, como hoje;
- o banco, no servidor interno;
- a API de leitura, no app da nuvem e no `main.py`.

O resto do código não sabe de onde os dados vêm. A API é criada e mantida pelo programador, na
máquina host, que alcança o banco e a VM. A tecnologia ainda vai ser escolhida; o candidato
natural é o FastAPI, por ser Python como o resto.

**Depende de:** a gerência aprovar uma versão externa, e a TI aceitar publicar um serviço por
HTTPS. Enquanto isso, a versão da nuvem segue pela opção 1.

## Fora do escopo

- **Outros estados**, por causa do custo (decisão 1).
- **Previsão na linha de comando** (`main.py`). Pode virar um produto depois que a página estiver
  validada pela equipe.
- **Previsto contra observado.** A comparação com as estações do INMET, para a equipe saber em
  que modelo confiar em MS, fica para a versão seguinte. Os dados para ela começam a ser
  guardados já nesta (decisão 6).
- **Um visualizador animado como o do Windy.**

## Riscos

- **A cota pode estar sendo dividida com outros apps na nuvem.** O limite do Open-Meteo é por
  endereço de saída, e no Streamlit Community Cloud os apps provavelmente saem por endereços
  compartilhados. Isso não foi medido. Se acontecer, a página recebe "limite excedido" sem ter
  gastado nada. No servidor da UNIGEO o risco diminui, porque o endereço é o da unidade.
- **O Open-Meteo gratuito não garante disponibilidade.** A página avisa e mostra a última rodada
  guardada, com a data dela.
- **O crédito é obrigatório** (CC BY 4.0), nos mapas e na tela.
