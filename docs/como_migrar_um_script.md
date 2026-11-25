# Como migrar um script para o projeto

Os scripts da equipe de meteorologia estão sendo trazidos aos poucos para este projeto. Cada script vira um **produto**: um arquivo em `modulos/produtos/` que reaproveita as peças compartilhadas (a fonte dos dados, os períodos, os cálculos, os mapas e o Excel).

O primeiro foi o `relatorio_inmet`, a partir dos scripts em `legado/relatorio_inmet/`. Use-o como modelo.

---

## 1. Guardar o original

- Copie o script recebido, **sem alterações**, para `legado/<nome_do_produto>/`.
- Antes do commit, troque qualquer credencial (token, senha) por `SEU_TOKEN_AQUI`.
- Faça o commit antes de mexer: assim o ponto de partida fica registrado.

## 2. Entender o script

Anote, antes de programar:

- **Entrada:** quais estações, variáveis e janela de tempo (dia, período, últimas horas).
- **Cálculos:** o que é feito com os dados (extremos, somas, médias, limiares, índices).
- **Saídas:** planilhas, mapas ou textos, com nomes e formatos.
- **Dúvidas e possíveis erros:** registre em [`questoes_meteorologia.md`](questoes_meteorologia.md) o que depende de decisão da meteorologia. Não corrija regras meteorológicas por conta própria: migre como está e pergunte.

## 3. Ver o que já existe

Antes de escrever código novo, confira se a peça já existe:

| Preciso de... | Use |
|---|---|
| Datas da consulta (dia, período, tempo real) | `config.Periodo` — `janela`, `janela_busca`, `inicio_do_dia`, `identificador`, `pasta_saida(produto)` |
| O estado (sigla, shapefiles, enquadramento, fuso) | `opcoes["recorte"]`, um `config.Recorte` (MS quando não vem) |
| As estações do estado e os dados horários delas | `fonte.leituras_do_estado(inicio, fim, uf=recorte.uf, fuso=periodo.fuso)`, que devolve pares `(estação, leituras)`; as vizinhas, para a borda do mapa, em `fonte.leituras_de_apoio`. **Nunca direto do `inmet.py`**: é o `fonte.py` que decide se os dados vêm da API ou do banco (`FONTE_DADOS`), e um teste falha se um produto passar por fora. O erro a tratar é o `fonte.ErroFonte` |
| Recortar um intervalo, achar extremos, somar chuva | `calculos.recortar`, `calculos.indice_extremo`, `calculos.data_hora`, `calculos.acumulado_chuva` |
| Mapas pontuais e interpolados | `mapas.EspecMapa` + `mapas.gerar_mapas` (a interpolação IDW já vem junto) |
| Tirar dos mapas as estações retiradas (`--retirar`) | `opcoes["retirar"]` e `retiradas.retirar`: saem do mapa, do ranking e da interpolação, e a planilha fica com todas. Veja como o `relatorio_inmet` faz em `_sem_as_retiradas` |
| Gráficos de barras ou calendário | `graficos.barras_empilhadas`, `graficos.barras_agrupadas` e `graficos.calendario` |
| Planilha formatada | `excel.salvar_relatorio` |
| Pastas, limites do mapa, logos e parâmetros | `config` |

## 4. Criar o produto

1. Crie `modulos/produtos/<nome>.py`. Todo produto precisa ter:

   ```python
   NOME = "<nome>"   # igual ao nome do arquivo; usado no main.py e na pasta de saída
   TITULO = "..."    # descrição curta, exibida ao rodar

   # Gera o produto. Devolve 0 se deu certo e 1 se falhou
   def executar(periodo: Periodo, opcoes: dict | None = None) -> int:
       ...
   ```

   As funções do projeto não têm docstring: o porquê vai num comentário curto, quando precisa. Só o
   topo de cada arquivo tem uma docstring, dizendo para que ele serve.

   `opcoes` traz o estado (`"recorte"`), as estações retiradas (`"retirar"`) e os argumentos de linha
   de comando que valem só para alguns produtos (por exemplo, `--hrtodas`, do `risco_fogo`). Cada produto usa as opções que conhece e ignora o resto; um produto
   sem opções próprias apenas aceita o parâmetro e não o consulta.

2. Dentro de `executar`, siga o mesmo roteiro do `relatorio_inmet`: baixar os dados → calcular por estação → montar as tabelas → salvar a planilha e os mapas em `periodo.pasta_saida(NOME)`.
3. Registre o produto na lista `PRODUTOS`, no `main.py`.
4. Regras que só valem para este produto (variáveis, janelas especiais, colunas, lista de mapas) ficam no arquivo do produto. Uma peça nova que sirva para outros produtos vai para o módulo compartilhado correspondente (`calculos.py`, `mapas.py`...). Na dúvida, deixe no produto e mova quando um segundo produto precisar dela.

## 5. Escrever os testes

Crie `tests/test_<nome>.py`. As fixtures de `tests/conftest.py` já simulam a API do INMET:

- `serie` — dados horários sintéticos, para testar cálculos com valores conhecidos;
- `api_simulada` — 11 estações fictícias (uma delas sem dados) e duas vizinhas, sem token e sem internet, com as saídas numa pasta temporária.

Nenhum teste pode usar a rede: a fixture automática `sem_rede` derruba qualquer tentativa, com o endereço na mensagem.

Teste pelo menos as janelas de tempo do produto, os cálculos com valores conhecidos e a execução completa em cada modo (dia, período e tempo real). O `tests/test_main.py` já confere se o produto segue o padrão (`NOME`, `TITULO` e `executar`).

## 6. Comparar com o original

Rode o script original e o produto novo para as **mesmas datas** e compare as planilhas. Os valores devem ser iguais; cada diferença precisa de uma explicação (erro corrigido, convenção unificada) e deve ser registrada no README — e em `questoes_meteorologia.md`, se depender da meteorologia.

## 7. Documentar e entregar

- No README, acrescente o produto na tabela de produtos do topo (com link para a seção dele) e no sumário.
- Crie a seção do produto dentro de **Como usar**, copiando a estrutura das seções existentes, com as mesmas subseções e na mesma ordem:
  1. **O que calcula** — as variáveis, a regra e o passo a passo de uma execução;
  2. **Como rodar** — os comandos mais comuns;
  3. **Modos de tempo** — o que muda em cada modo aceito (tempo real, data específica, período) e quais não são aceitos;
  4. **Saídas** — a árvore de pastas, o que é cada arquivo e as colunas da planilha;
  5. **Exemplos** — mapas reais gerados pelo produto;
  6. **Dados de referência** — endpoints, variáveis da API e parâmetros de `config.py`;
  7. **Notas metodológicas** — convenções e limitações do cálculo;
  8. **Mudanças em relação aos scripts legados** — ou "não se aplica", se o produto for novo;
  9. **Decisões da equipe de meteorologia** — resumo, com link para `docs/questoes_meteorologia.md`.
- Para os exemplos, rode o produto com dados reais e copie alguns mapas de `saida/` para `docs/img/<nome>/`, reduzidos para cerca de 1000 px de largura (os originais têm ~1 MB e ficariam para sempre no histórico do git).
- Conte a novidade no [`CHANGELOG.md`](../CHANGELOG.md), na versão em andamento, do ponto de vista de quem roda o `main.py`.
- Trabalhe na branch da versão (`v0.x.y`), rode `pytest` e abra um pull request para a `main`. Os testes também rodam no GitHub.
- O original fica em `legado/<nome>/` até a equipe validar os resultados.

---

## Checklist

- [ ] Original em `legado/<nome>/`, sem credenciais
- [ ] Dúvidas registradas em `questoes_meteorologia.md`
- [ ] `modulos/produtos/<nome>.py` com `NOME`, `TITULO` e `executar`, pedindo os dados ao `fonte.py`
- [ ] Produto registrado em `PRODUTOS`, no `main.py`
- [ ] Estações retiradas (`opcoes["retirar"]`) fora dos mapas, se o produto tiver mapa
- [ ] `tests/test_<nome>.py` passando
- [ ] Resultados comparados com o original
- [ ] Seção do produto no README, com as subseções padrão e exemplos em `docs/img/<nome>/`
- [ ] Entrada no `CHANGELOG.md`
- [ ] Pull request com os testes passando
