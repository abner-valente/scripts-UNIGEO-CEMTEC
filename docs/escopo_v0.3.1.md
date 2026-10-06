# Escopo da v0.3.1 — aba de previsão para MS

> Escrito em 06/10/2026, com as decisões tomadas pelo programador entre 05 e 06/10. Os custos
> foram **medidos** contra a API do Open-Meteo em 05/10, e não tirados da documentação. Este
> documento registra o que foi decidido e por quê; nada daqui está implementado ainda.

## O que muda, em uma frase

O painel passa a mostrar também o que **vai** acontecer: uma aba de previsão para Mato Grosso do
Sul, com três modelos até 14 dias e a anomalia semanal até 6 semanas.

## A ideia central

"Algumas semanas à frente" são dois produtos diferentes, porque a previsão muda de natureza no
meio do caminho:

| Horizonte | O que o modelo sabe dizer | O que a aba mostra |
|---|---|---|
| **Dias 1 a 14** | o valor de cada dia: "máxima de 34 °C na quinta" | mapas no padrão do boletim, dia a dia ou acumulados, e gráficos de linha por estação, um modelo ao lado do outro |
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

**2. Três modelos, até 14 dias.** O ECMWF IFS (0,25°) e o GFS chegam ao dia 15, mas a aba usa
14: o 15º dia faria cada ponto pesar 7% a mais, e passaria do limite em que o Open-Meteo começa a
cobrar pelos dias (decidido em 06/10). O ICON só vai até o dia 7: medido em 05/10, ele trouxe valor
até 11/10. O terceiro modelo até o dia 14 ainda precisa ser escolhido. O candidato é o AIFS, o
modelo de IA do ECMWF; se ele não servir, o ICON fica como terceiro, só até o dia 7.

**3. As variáveis do INMET, menos radiação e pressão.**
- temperatura: máxima, mínima e média;
- umidade mínima;
- ponto de orvalho;
- chuva: a do dia e os acumulados;
- vento: velocidade e rajada máximas, e direção dominante, desenhada com a seta que já existe.

Os nomes dos mapas seguem o catálogo do painel (`app/variaveis.py`) onde houver equivalente.

**4. Grade de 0,5° com margem de 0,5°: 178 pontos.** A margem é uma fileira de pontos além da
divisa, sem a qual a cor não chega até a borda. Comparação, para MS:

| Pontos pedidos | Pontos | Comentário |
|---|---|---|
| retângulo inteiro do mapa | 272 | inclui cantos que o recorte esconde |
| só dentro do estado | 124 | a borda fica sem cor |
| **estado + margem de 0,5°** | **178** | **escolhido** |
| grade de 0,25° com margem de 0,25° | 607 | o detalhe máximo desses modelos, ~3,4 vezes o custo |

0,5° são uns 55 km. Perde o detalhe fino de uma chuva concentrada, mas cabe num pedido só, de
uns 2 segundos. Para as semanas é o espaçamento certo, porque o EC46 trabalha a 36 km. O
espaçamento vira uma constante em `config.py`. 0,25° faz sentido quando a busca for **agendada**,
porque aí esperar minutos entre os pedidos não incomoda ninguém.

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

**5. Busca sob demanda, guardada por rodada do modelo e compartilhada.** A primeira pessoa que
abre a aba depois de uma rodada nova paga a busca; todas as outras usam a mesma cópia. A chave do
cache é a **rodada** ("ECMWF de 06/10, 00 UTC"), e não o relógio, porque cada modelo publica a
rodada horas depois do horário nominal. A aba mostra de que rodada é cada modelo.

**6. A busca mora fora do painel**, num módulo novo, `modulos/previsao.py`, ao lado do
`inmet.py`. O painel chama esse módulo hoje. Um agendamento (GitHub Actions, ou o cron do
servidor) ou um coletor que grava no banco chama **a mesma função** amanhã. Trocar quem aperta o
botão não pode exigir reescrever a busca.

**7. Risco de fogo previsto.** As três variáveis da regra 30-30-30 vêm na previsão horária. A
regra já existe em `modulos/produtos/risco_fogo.py` e é aplicada como está, sem reescrever. Fica
para o fim da versão.

**8. Escala de cores.** Os mapas de dias usam a mesma escala fixa dos mapas observados (0 a 45 °C
etc.), para previsto e observado serem comparáveis. Os de anomalia pedem uma escala nova,
**divergente**: azul abaixo do normal, branco no normal, vermelho acima.

## O custo, medido

Como o Open-Meteo conta, conforme os testes de 05/10:

- **Cada ponto é uma chamada.** Um pedido com 650 pontos passou, e o pedido seguinte do mesmo
  minuto foi recusado. O limite (600 por minuto) é conferido **antes** de somar o custo do pedido.
- **Num pedido com vários modelos, as variáveis contam por modelo.** 9 variáveis × 3 modelos
  pesam como 27, ou seja, 2,7 vezes. Ainda assim, juntar os modelos num pedido só economiza um
  pouco: 2,7 contra 3,0 em três pedidos separados de 9 variáveis.
- **Mais de 14 dias pesa** na proporção (15 dias pesam 15/14). Por isso a aba usa 14.
- A API não informa quanto já foi gasto. Só se descobre o limite ao chegar nele.

Em resumo, pela documentação e de acordo com o que foi medido:

```
custo = pontos × máx(1, variáveis × modelos ÷ 10) × máx(1, dias ÷ 14)
```

O que não entra na conta é quantos números voltam: um mapa de 0,25° devolve uns 230 mil valores,
e custa o mesmo que se devolvesse um por ponto. Que dado horário custa o mesmo que diário vem da
regra documentada; não foi medido.

Para MS, com 3 modelos, 9 variáveis e 14 dias, cada ponto custa 2,7:

| Produto | Chamadas por atualização | Por dia |
|---|---|---|
| Mapas de dias, 178 pontos, 2 rodadas | ~480 | ~960 |
| Gráficos de linha nas 59 estações, 2 rodadas | ~160 | ~320 |
| Anomalia semanal do EC46, 46 dias, 1 por dia | ~590 | ~590 |
| Risco de fogo previsto (3 variáveis horárias), 2 rodadas | ~180 | ~360 |
| **Total** | | **~2.230, cerca de 22% da cota** |

Com a grade de 0,25° nos mapas e no risco (só com a busca agendada), o total sobe para ~5.400 por
dia, cerca de 54% da cota.

## Ordem de trabalho

1. **`modulos/previsao.py`.** A busca: pontos e estações de MS, os modelos, a identificação da
   rodada, e o aviso quando o Open-Meteo não responder ou a cota acabar. Os testes usam um
   Open-Meteo simulado, como o INMET já é, sob a mesma trava que proíbe rede nos testes.
2. **Gráficos de linha por estação.** Os três modelos lado a lado. É o passo mais barato, e o que
   mostra se a fonte serve.
3. **Mapas de dias**, de 1 a 14. Um modelo por vez, com um seletor para trocar, e o PNG do
   boletim.
4. **Mapas de anomalia semanal** do EC46.
5. **Risco de fogo previsto.**

## Encaixe com a ida para o servidor da UNIGEO

Em 06/10 entrou em discussão levar o painel para um servidor da UNIGEO e os dados para o banco
da unidade, com um coletor por fonte (INMET e Open-Meteo), cada uma na sua tabela. Essa decisão
ainda está aberta, e esta versão foi desenhada para não atrapalhá-la:

- **A busca da decisão 6 é o futuro coletor do Open-Meteo.** "Guardar por rodada" vira uma
  tabela com modelo, rodada, ponto, data prevista e as variáveis.
- **Se o servidor e o banco chegarem antes do passo 3**, a aba já nasce lendo do banco, e o cache
  da decisão 5 nem chega a ser escrito.
- **No servidor, a busca passa a ser agendada.** A primeira pessoa deixa de esperar, e 0,25°
  passa a caber.

## Fora do escopo

- **Outros estados**, por causa do custo (decisão 1).
- **Grade de 0,25°**, que fica para quando a busca for agendada.
- **Previsão na linha de comando** (`main.py`). Pode virar um produto depois que a aba estiver
  validada pela equipe.
- **Previsto contra observado.** A comparação com as estações do INMET, para a equipe saber em
  que modelo confiar em MS, fica para a versão seguinte. Ela pede guardar as rodadas antigas, o
  que é natural num banco.
- **Um visualizador animado como o do Windy.**

## Riscos

- **A cota pode estar sendo dividida com outros apps na nuvem.** O limite do Open-Meteo é por
  endereço de saída, e no Streamlit Community Cloud os apps provavelmente saem por endereços
  compartilhados. Isso não foi medido. Se acontecer, a aba recebe "limite excedido" sem ter
  gastado nada. As saídas: a busca agendada, o servidor da UNIGEO, ou uma chave paga.
- **O Open-Meteo gratuito não garante disponibilidade.** A aba avisa e mostra a última rodada
  guardada, com a data dela.
- **O crédito é obrigatório** (CC BY 4.0), nos mapas e na tela.
