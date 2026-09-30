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
4. **Shapefiles das demais UFs**, sob demanda.

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

## Custo e risco

- O download de MS **dobra**: 59 → 113 estações, de ~4,4 s para ~9 s numa semana. Da segunda vez
  em diante vem do cache.
- A interpolação não fica mais cara: a grade é a mesma, muda o número de pontos de entrada, e o
  cKDTree resolve isso sem sentir.
- **Risco principal:** mexer em `carregar_base` e no enquadramento toca os produtos. Os mapas do
  boletim só passam a usar vizinhos quando a equipe olhar o efeito no painel e decidir — a
  capacidade entra agora, o uso nos produtos é uma decisão à parte.

## Fora do escopo

Mapa nacional único, escalas por região, regra de fogo de outros estados, e qualquer coisa que
dependa de interpolar onde a densidade não permite. Para dimensionar: o INMET tem 526 estações
operantes no país, mas o Amazonas tem uma a cada 156 mil km² e Roraima não tem nenhuma operante
— o mesmo IDW que descreve MS (uma a cada 6 mil km²) inventaria a Amazônia.

## De brinde

A lista de estações não filtra por situação: o painel pede **62 estações de MS, mas só 59 estão
operantes** — três em pane consultadas a cada carga, que caem na lista de "sem dados" da tela. O
filtro entra junto com esta mudança.
