# Escopo da v0.2.2 — recorte por UF e vizinhos na interpolação

> Escrito em 28/09/2026, com as decisões tomadas pelo programador no mesmo dia. Este documento
> registra **o que foi decidido e por quê**, para quem pegar o código depois não precisar
> reconstituir o raciocínio.

## O que muda, em uma frase

Hoje o projeto **é** de Mato Grosso do Sul: a UF é uma constante. Ela passa a ser um **recorte
escolhido** — e a superfície interpolada passa a ser alimentada também pelas estações de fora
dele.

## A ideia central

Separar dois conjuntos que hoje são o mesmo:

- **Estações do produto** — as da UF. São as da tabela, do ranking, da lista da barra lateral e
  do CSV.
- **Estações do recorte** — todas as que caem no enquadramento ampliado, de qualquer UF. Só
  alimentam a interpolação.

Em MS são **59 do produto e 113 do recorte**: 54 vêm de PR (18), MT (14), GO (12), SP (8) e
MG (2). Na borda, a superfície deixa de extrapolar de dentro para fora — hoje os 8 vizinhos mais
próximos de uma célula da divisa estão todos do lado de cá, e o dado do outro lado existe.

O mapa continua recortado no contorno do estado. O que muda é a conta que chega até a borda,
não o que se desenha fora dela.

## Onde o estado está grudado hoje

56 ocorrências em 7 arquivos, e a concentração é boa notícia — não é um estado espalhado pelo
código, são seis constantes e quem as lê:

| arquivo | ocorrências | o quê |
|---|---|---|
| `modulos/config.py` | 16 | `UF`, `NOME_UF`, os dois shapefiles, o enquadramento, `FUSO_MS` |
| `modulos/produtos/risco_fogo.py` | 16 | títulos e a convenção de dia |
| `app/explorador.py` | 10 | títulos dos mapas |
| `modulos/mapas.py` | 6 | `carregar_base` lê os shapefiles e monta a grade |
| `modulos/inmet.py` | 3 | `listar_estacoes(uf=...)` |
| `relatorio_inmet.py` e `app/superficie.py` | 5 | títulos e enquadramento |

## As decisões

**1. O enquadramento de MS fica exatamente como está.** Hoje é fixo (`-58,5` a `-50,5`,
`-24,5` a `-17,0`), com cerca de 0,3° de margem sobre os limites reais do estado. Dá para
derivar de qualquer UF a partir da geometria mais uma margem, mas aí os números de MS mudariam
por arredondamento e **todos os mapas dos produtos mudariam junto**. Então: tabela por UF, com
os números atuais de MS preservados, e derivados para as outras.

**2. Os vizinhos não aparecem no mapa.** Eles entram na conta e só. Não viram ponto desenhado,
rótulo, linha de tabela, ranking nem opção na barra lateral. São insumo, não resultado.

**3. Os shapefiles das outras UFs saem sob demanda**, pela `ferramentas/simplificar_municipios.py`
que já existe, em vez de carregar um arquivo nacional com os 5.570 municípios do país — que
pesaria na VM a cada mapa. Os vizinhos não precisam de shapefile nenhum: deles só se usam as
estações.

**4. O que é de MS continua sendo de MS.** Apontar o painel para outro estado muda o fuso (é
dado, não opinião, e vem da UF), mas **as escalas de cor e a regra 30-30-30 ficam como estão**
nesta versão, com aviso na tela de que foram calibradas para MS. O teto de 300 mm no acumulado
mensal é a média máxima *deste* estado; o 30-30-30 é uma convenção do CEMTEC.

**5. Painel e produtos, com MS como padrão em tudo.** O `config` é compartilhado, então não faz
sentido fazer só de um lado. Mas quem roda o `main.py` hoje não deve notar diferença nenhuma.

## Ordem de trabalho

1. **Vizinhos na interpolação**, só em MS, sem mexer em UF. É a parte que melhora o produto
   atual. Inclui separar os dois conjuntos de estações e conferir que nada de fora do estado é
   desenhado.
2. **O recorte como parâmetro** — uma dataclass com sigla, nome, shapefiles, enquadramento e
   fuso, passada adiante em vez de lida do módulo. Sem variável global mutável: o painel serve
   várias pessoas no mesmo processo, e duas escolhendo UFs diferentes brigariam.
3. **Seletor de UF** na barra lateral (MS como padrão) e `--uf` no `main.py`.
4. **Shapefiles das demais UFs**, sob demanda. *(Feito em 30/09/2026: os 25 com estação.)*

## O que já está feito

Os quatro itens saíram. Sobre o item 4, o que se aprendeu fazendo:

- A `simplificar_municipios.py` passou a receber `--uf` e a **baixar sozinha** a malha de 2022 do
  IBGE, que publica um arquivo por UF. Antes os dois caminhos de MS estavam escritos no corpo
  dela, e a procedência do `MS_UF_2022.shp` não estava registrada em lugar nenhum — dava para
  deduzi-la pelo esquema de colunas, e só.
- O **bruto não é versionado** (`shp/fonte/`, no .gitignore): são 12 MB por estado que o IBGE
  devolve quando pedir. Versionados ficam os dois arquivos que os mapas leem — 1,8 MB no MT —,
  porque a nuvem do Streamlit clona o repositório e não baixa nada na hora de desenhar.
- **MS é a exceção e continua sendo:** o municipal dele é um arquivo da equipe, não do IBGE.
  A ferramenta prefere a fonte local `<UF>_mun.shp` quando ela existe, então rodar de novo não
  troca o dado de vocês pelo do IBGE.
- **MT ficou pronto** (141 municípios, 51 estações próprias e 55 de apoio, 21 delas de MS — a
  recíproca do item 1). Os 11 mapas do boletim saem em 23 s.
- **Os logos são de MS.** Todo mapa leva o brasão do Estado de Mato Grosso do Sul e o texto
  "CEMTEC — Centro de Monitoramento do Tempo e do Clima de Mato Grosso do Sul", inclusive o de
  MT. É decisão de identidade visual, não de código, e está em aberto.
- **SC entrou como terceiro estado** (30/09/2026) e trouxe duas coisas que MS e MT não tinham:
  - **Contorno com costa.** O litoral do IBGE tem 193 mil vértices em SC. A ferramenta passou a
    simplificar o contorno também, na mesma tolerância da malha municipal: 3,1 MB viram 67 KB e a
    máscara do recorte muda 1 célula em 10.000.
  - **Proporção 1,51** (6,21° × 4,10°), contra 1,07 de MS e 1,06 de MT. Nos produtos, o bloco de
    logos fica no alto à direita — que em MS e MT é vazio e em SC é estado: **os logos cobrem o
    nordeste do mapa**. O painel não tem esse problema, porque não desenha logo nenhum. Fica junto
    da questão de identidade visual acima, porque mexer no enquadramento dos produtos mexe nos
    mapas de MS.
  - **11 estações próprias e 88 de apoio** (RS 69, PR 16, SP 3). É o caso extremo do item 1: no
    painel a superfície de SC é praticamente desenhada pelos vizinhos. Nos produtos, que seguem
    sem vizinhos por decisão, o mapa sai de 11 pontos — a decisão de ligá-los lá foi tomada quando
    o único estado era MS, com 59, e em SC ela pesa muito mais.

## Custo e risco

- O download de MS **dobra**: 59 → 113 estações, de ~4,4 s para ~9 s numa semana. Da segunda vez
  em diante vem do cache.
- A interpolação não fica mais cara: a grade é a mesma, muda o número de pontos de entrada, e o
  cKDTree resolve isso sem sentir.
- **Risco principal:** mexer em `carregar_base` e no enquadramento toca os produtos. Os mapas do
  boletim só passam a usar vizinhos quando a equipe olhar o efeito no painel e decidir — a
  capacidade entra agora, o uso nos produtos é uma decisão à parte.

## Os 25 estados

Gerados em 30/09/2026. São **25, e não 27**: RR e SE têm todas as estações em pane, e estado sem
estação não rende mapa — o seletor não deve oferecer o que não desenha. Quando voltarem, é um
comando cada.

- **4,0 MB** no repositório para os 25 (contorno + municipal simplificado). O maior arquivo de
  `shp/` continua sendo o `MS_mun.shp` original, com 17,3 MB. O bruto do IBGE são 261 MB e fica
  em `shp/fonte/`, fora do git.
- **Ilha oceânica não entra no enquadramento** (`config.ILHA_DISTANTE = 1.0`). Trindade esticava
  o ES de 2,2° para 13° de largura; Fernando de Noronha fazia o mesmo com PE. O enquadramento
  passa a sair do corpo principal mais o que estiver a até 1° dele — Marajó, Ilha de Santa
  Catarina e Ilhabela continuam dentro. Dos 25, só ES e PE eram afetados.
- **As formas ficam muito mais variadas do que MS.** A proporção largura/altura vai de 0,61 (TO)
  a 2,96 (PE), contra 1,05 de MS. A figura aguenta — PE e TO foram conferidos —, mas sobra espaço
  vazio nas duas pontas, e o bloco de logos continua no alto à direita, onde em alguns estados há
  estado embaixo. Junto da questão de identidade visual, que segue aberta.

## O custo de carregar, e o caminho que não tomamos

Ligar as vizinhas dobra o número de estações consultadas, e em 30/09/2026 foram medidas três
saídas. Escolhida: a **poda** (a terceira). As outras ficam registradas porque continuam valendo.

**1. Endpoint em massa do INMET.** Existe `/token/estacao/dados/{data}/{hora}/{token}`, que
devolve **as 740 estações do país numa requisição só** — 139 KB, ~0,9 s. Um dia inteiro sai em
24 requisições, 4,1 s com 8 paralelas, e traz **todas as colunas que o projeto usa**. Com ele as
vizinhas passam a custar **zero**, e o cache deixa de ser por estado: a hora baixada serve para
qualquer UF.

O custo vira `24 × dias` requisições em vez de `N` estações, e os dois se cruzam perto de **4
dias**. Ou seja: ganha muito para um dia, tempo real e o boletim; perde feio para a aba de chuva,
que vai até o dia 1º do mês (720 requisições, uns 2 minutos). Seria preciso escolher o caminho
por consulta, e manter **dois formatos de cache**.

Duas ressalvas antes de apoiar o boletim nele: o endpoint foi achado sondando, não em
documentação, e não se sabe como ele responde na hora corrente — justamente a que chega com a
linha publicada e a medida vazia.

**2. Aumentar o paralelismo.** Já está em 8, e 16 faz o INMET derrubar conexões.

**3. Poda das vizinhas — implementada.** O IDW olha 8 por célula; estação que não chega às mais
próximas de célula nenhuma é requisição jogada fora, e isso se decide só com coordenadas, antes
de baixar. Corta 54→39 em MS, 54→49 em MT, 89→60 em SC. A poda olha o dobro de vizinhas que o IDW
usa, porque numa hora com estações faltando as 8 mais próximas são outras. Conferido nos três
estados e nas duas grades: nenhuma estação usada foi perdida e a superfície sai idêntica bit a
bit.

Descartadas por render pouco ou contornar regra: baixar em segundo plano (esconde a espera, não
reduz), reduzir a margem de 1,5° (versão cega da poda), versionar cache no git (envelhece e
incha) e manter o app da nuvem acordado à força.

## Fora do escopo

Mapa nacional único, escalas por região, regra de fogo de outros estados, e qualquer coisa que
dependa de interpolar onde a densidade não permite. Para dimensionar: o INMET tem 526 estações
operantes no país, mas o Amazonas tem uma a cada 156 mil km² e Roraima não tem nenhuma operante
— o mesmo IDW que descreve MS (uma a cada 6 mil km²) inventaria a Amazônia.

## De brinde

A lista de estações não filtra por situação: o painel pede **62 estações de MS, mas só 59 estão
operantes** — três em pane consultadas a cada carga, que caem na lista de "sem dados" da tela. O
filtro entra junto com esta mudança.
