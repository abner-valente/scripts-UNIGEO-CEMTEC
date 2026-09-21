# Como migrar um script para o projeto

Os scripts da equipe de meteorologia estão sendo trazidos aos poucos para este projeto. Cada script vira um **produto**: um arquivo em `modulos/produtos/` que reaproveita as peças compartilhadas (API do INMET, períodos, cálculos, mapas e Excel).

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
| Estações de MS e seus dados horários | `inmet.baixar_estacoes(inicio, fim)` |
| Recortar um intervalo, achar extremos, somar chuva | `calculos.recortar`, `calculos.indice_extremo`, `calculos.data_hora`, `calculos.acumulado_chuva` |
| Mapas pontuais e interpolados | `mapas.EspecMapa` + `mapas.gerar_mapas` (a interpolação IDW já vem junto) |
| Gráficos de barras ou calendário | `graficos.barras_empilhadas`, `graficos.barras_agrupadas` e `graficos.calendario` |
| Planilha formatada | `excel.salvar_relatorio` |
| Pastas, limites do mapa, logos e parâmetros | `config` |

## 4. Criar o produto

1. Crie `modulos/produtos/<nome>.py`. Todo produto precisa ter:

   ```python
   NOME = "<nome>"   # igual ao nome do arquivo; usado no main.py e na pasta de saída
   TITULO = "..."    # descrição curta, exibida ao rodar

   def executar(periodo: Periodo, opcoes: dict | None = None) -> int:
       """Gera o produto. Retorna 0 se deu certo e 1 se falhou."""
   ```

   `opcoes` traz os argumentos de linha de comando que valem só para alguns produtos (por exemplo,
   `--hrtodas`, do `risco_fogo`). Cada produto usa as opções que conhece e ignora o resto; um produto
   sem opções próprias apenas aceita o parâmetro e não o consulta.

2. Dentro de `executar`, siga o mesmo roteiro do `relatorio_inmet`: baixar os dados → calcular por estação → montar as tabelas → salvar a planilha e os mapas em `periodo.pasta_saida(NOME)`.
3. Registre o produto na lista `PRODUTOS`, no `main.py`.
4. Regras que só valem para este produto (variáveis, janelas especiais, colunas, lista de mapas) ficam no arquivo do produto. Uma peça nova que sirva para outros produtos vai para o módulo compartilhado correspondente (`calculos.py`, `mapas.py`...). Na dúvida, deixe no produto e mova quando um segundo produto precisar dela.

## 5. Escrever os testes

Crie `tests/test_<nome>.py`. As fixtures de `tests/conftest.py` já simulam a API do INMET:

- `serie` — dados horários sintéticos, para testar cálculos com valores conhecidos;
- `api_simulada` — 10 estações fictícias, sem token e sem internet, com as saídas numa pasta temporária.

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
- Trabalhe numa branch, rode `pytest` e abra um pull request para a `main`. Os testes também rodam no GitHub.
- O original fica em `legado/<nome>/` até a equipe validar os resultados.

---

## Checklist

- [ ] Original em `legado/<nome>/`, sem credenciais
- [ ] Dúvidas registradas em `questoes_meteorologia.md`
- [ ] `modulos/produtos/<nome>.py` com `NOME`, `TITULO` e `executar`
- [ ] Produto registrado em `PRODUTOS`, no `main.py`
- [ ] `tests/test_<nome>.py` passando
- [ ] Resultados comparados com o original
- [ ] Seção do produto no README, com as subseções padrão e exemplos em `docs/img/<nome>/`
- [ ] Pull request com os testes passando
