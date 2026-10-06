# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Relato individual - Diogo Oliveira

- Fui responsável pela criação e configuração inicial do repositório, incluindo a adição de todos os integrantes da equipe. Também participei da estruturação do acompanhamento das atividades e das discussões para definição e organização da arquitetura do projeto.

## Encontro 2 - 2026-09-16

### Relato individual - Diogo Oliveira

- Fui responsável pela elaboração do plano de arquitetura do projeto, documentado no arquivo `ARQUITETURA.md`, contribuindo para a definição do cenário escolhido, do pipeline e das etapas da solução. Também participei ativamente do acompanhamento e apoio às soluções desenvolvidas pelos demais integrantes nas outras atividades realizadas durante o dia.

## Encontro 3 - 2026-09-18

### Relato individual - Diogo Oliveira

- Realizei o acompanhamento do andamento do projeto, revisando as atividades e pendências registradas no Notion e alinhando os próximos passos da equipe. Também participei dos testes da aplicação e da revisão do arquivo `PERSONAS.md`, verificando a consistência das entregas com os requisitos definidos para o projeto.

## Encontro 4 - 2026-09-21

### Relato individual - Diogo Oliveira

- Realizei o desenvolvimento, a revisão e a adequação do módulo `src/retrieve.py` ao projeto, após analisar os arquivos e contratos existentes.
- Trabalhei na recuperação híbrida com busca densa e BM25, filtros de metadata, fusão por RRF e preparação para integração futura com rerank, sem inventar infraestrutura inexistente.
- Também corrigi problemas identificados na auditoria técnica, incluindo tratamento de query vazia, deduplicação de chunks, normalização de tipos nos filtros e suporte a datas em diferentes formatos e valores monetários brasileiros, como `R$ 500.000,00`.
- Validei o código com testes específicos, `py_compile` e `git diff --check`, conferi sua compatibilidade com `schema.py`, `ingest.py`, `index.py`, `README.md`, `ARQUITETURA.md` e `corpus_manifest.yaml`, e registrei limitações relacionadas à ausência de `router.py`, `generate.py` e `obs.py` no checkout analisado.
- Por fim, realizei uma auditoria final para verificar o alinhamento do módulo com a arquitetura atual e identificar possíveis problemas de integração.
- Resumi o acompanhamento de todos os desenvolvedores e atualizei do `ACOMPANHAMENTO.md`.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio durante o desenvolvimento e revisão do módulo src/retrieve.py, principalmente para auxiliar na análise do código, identificação de possíveis problemas, revisão da implementação, sugestões de melhorias de Clean Code e validação de casos específicos.
- As decisões sobre a implementação, adequação à arquitetura existente, correções realizadas e validações finais foram analisadas e verificadas no contexto do projeto.

## Encontro 5 - 2026-09-23

### Relato individual - Diogo Oliveira

- Realizei uma nova análise do projeto atualizado para verificar a compatibilidade do `src/retrieve.py` com os novos arquivos, módulos, contratos e integrações adicionados ao projeto.
- Desenvolvi a suíte de testes automatizados `tests/test_retrieve.py`, utilizando mocks, dados controlados e testes isolados para validar o comportamento do módulo sem depender de serviços externos, APIs ou modelos reais.
- Implementei testes para os principais componentes da recuperação, incluindo query vazia, recuperação densa, BM25, recuperação híbrida, fusão por RRF, ranking, deduplicação por `chunk_id`, filtros de metadata, datas em diferentes formatos, valores monetários brasileiros, leitura de valores em `metadata.extras`, operadores inválidos, validação de `k`, reranking opcional e limite de `top_k`.
- Apliquei princípios de Clean Code na construção da suíte de testes, utilizando funções auxiliares para criação de registros controlados, parametrização para diferentes operadores, formatos de datas e valores monetários, nomes de testes descritivos e cenários isolados e determinísticos.
- A suíte foi estruturada sem dependências externas, utilizando dados locais e `tmp_path`, com cobertura focada nos comportamentos reais do `src/retrieve.py`.
- Validei a suíte específica do módulo com **26 testes aprovados** e, posteriormente, executei a suíte completa do projeto, totalizando **157 testes aprovados**.
- Também executei `git diff --check`, sem identificação de erros de whitespace, e confirmei que somente o arquivo `tests/test_retrieve.py` foi criado, sem alterações nos arquivos de produção ou nos demais arquivos do projeto.
- Durante a execução dos testes, identifiquei e registrei dois comportamentos potencialmente problemáticos no `src/retrieve.py` o tratamento de embeddings com dimensões incompatíveis, que atualmente resulta em score `0.0`, e a possibilidade de comparação lexical em determinados casos de valores monetários inválidos.
- Esses pontos foram registrados para avaliação posterior, sem alteração do código de produção durante a atividade.
- Resumi o acompanhamento de todos os desenvolvedores e atualizei do `ACOMPANHAMENTO.md`.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio na análise do projeto atualizado, na identificação dos comportamentos que deveriam ser cobertos pelos testes automatizados e na elaboração e revisão da suíte `tests/test_retrieve.py`.
- Os testes, mocks, cenários e critérios de validação foram analisados em conjunto com o código e os contratos existentes no projeto, sendo executados e verificados localmente. As decisões finais sobre os casos testados e a interpretação dos resultados foram realizadas com base no comportamento real da implementação.

## Encontro 6 - 2026-09-25

### Relato individual - Diogo Oliveira

- Realizei uma revisão final do módulo `src/retrieve.py` após as correções anteriores, identificando e corrigindo comportamentos relacionados ao tratamento de valores numéricos e embeddings inválidos ou ausentes.
- Ajustei o parser numérico para diferenciar corretamente valores decimais iniciados por zero, como `0.125` e `0.500`, de valores que utilizam ponto como separador de milhar, como `1.500` e `1.234.567`. Também preservei o tratamento de formatos decimais como `1500.50` e `1500,50`.
- Ajustei a propagação de `run_id` e `query_id` na recuperação densa, fazendo com que `dense_retrieve()` receba os identificadores da execução e que `retrieve()` os repasse corretamente. Com isso, os avisos de embeddings incompatíveis passam a utilizar os IDs reais da execução e da consulta.
- Corrigi também o tratamento de registros que possuem `embedding=None`. Esses registros agora são ignorados pela busca densa, sem geração de vetor ou cálculo de score, enquanto embeddings válidos continuam sendo processados normalmente.
- Para o caso de embedding ausente ou nulo, adicionei um aviso estruturado utilizando a observabilidade existente, contendo `run_id`, `query_id`, identificação do `chunk` e a indicação de que o embedding está ausente ou nulo.
- Ampliei a suíte `tests/test_retrieve.py` para cobrir os novos casos de borda e validar as correções de parsing, recuperação e observabilidade.
- A suíte específica de `tests/test_retrieve.py` foi finalizada com **50 testes aprovados**.
- A suíte completa do projeto foi executada com **274 testes aprovados**.
- Executei `git diff --check`, sem identificação de problemas.
- As alterações ficaram restritas aos arquivos `src/retrieve.py` e `tests/test_retrieve.py`.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio na revisão técnica das correções, identificação de casos de borda e elaboração e revisão dos testes de regressão.
- As alterações foram verificadas em conjunto com a implementação existente e validadas por meio da execução dos testes específicos, da suíte completa e do `git diff --check`.
- As decisões finais sobre as correções, o comportamento esperado e os critérios de validação foram baseadas no comportamento real do código e nos requisitos definidos para o projeto.

### Encontro 7 — 2026-09-28

**Tarefa do dia:**
Validar as pré-condições para execução do Golden Set completo com o pipeline real, verificando a disponibilidade e integridade do índice/base fornecido pela equipe, o ambiente Python e a execução da suíte de testes.

**O que fiz:**

- Baixei e avaliei o pacote `indice_rag.zip` disponibilizado pela equipe.
- Validei a estrutura interna do pacote e a consistência entre a base normalizada e os índices vetoriais.
- Confirmei a presença dos **3.130 chunks**, distribuídos entre:
  - 40 atas;
  - 160 contratos;
  - 843 editais;
  - 2.087 PCA.

- Verifiquei a consistência dos IDs entre os documentos normalizados e os arquivos de índice.
- Validei o checksum SHA-256 informado no manifesto da base.
- Analisei a configuração do projeto para confirmar que o pipeline utiliza `data/normalized/documents.jsonl` e `index/`.
- Verifiquei o README e confirmei que a orientação para não sobrescrever a base oficial com os arquivos de `exemplos_teste` está documentada.
- Executei a suíte de testes com `-m "not llm"` e identifiquei erros de dependências durante a coleta, relacionados à ausência de `fastapi` e `certifi` no ambiente virtual.
- Também identifiquei que o terminal estava utilizando o Python global em vez do interpretador do `.venv`, apesar do ambiente aparecer ativado.
- Com isso, a execução do Golden Set completo ficou temporariamente bloqueada até a correção do ambiente e a disponibilização dos arquivos nas estruturas esperadas pelo pipeline.

**Resultado:**
A validação mostrou que o pacote do índice está internamente consistente, mas o ambiente local ainda precisa ser ajustado antes da execução oficial do Golden Set. O próximo passo é instalar as dependências no `.venv`, posicionar corretamente `documents.jsonl` e `index/`, validar o checksum e executar novamente a suíte de testes. Após essas verificações, será possível executar os **19 casos do Golden Set pelo pipeline real**.

## Encontro 8 — 2026-09-30

### Relato individual - Diogo Oliveira

**Tarefa do dia:**
Executar o Golden Set completo pelo pipeline real e analisar os resultados por caso, sem alterar a implementação, os testes, o Golden Set ou os dados.

**O que fiz:**

- Executei o comando oficial `eval/run_eval.py run --pipeline src.pipeline:responder`. A primeira tentativa foi interrompida em 17 casos por ausência do módulo `faiss` no ambiente virtual; confirmei que `faiss-cpu` já estava declarado em `requirements.txt`, instalei a dependência no `.venv` e repeti a execução.
- Analisei a execução completa `20260930T192922Z`: foram processados os 22 casos presentes no Golden Set atual, sem erros de execução, com 14 acertos no critério do avaliador. O arquivo contém os 19 casos principais e mais três casos G1–G3, que também passaram.
- Nos 19 casos principais, identifiquei 11 acertos e 8 falhas: P1.1, P2.2, P2.3, P3.1, P4.2, P4.3, P5.1 e F1.
- Cruzei resultados com o relatório e os logs de observabilidade, identificando divergências de roteamento, comportamento das consultas SQL, recusas da geração, filtros relaxados por falta de metadados e casos em que o conteúdo disponível não comprova a resposta esperada.
- Verifiquei que a métrica `acertou` do avaliador compara roteamento e recusa; ela não valida a correção factual da resposta nem a suficiência das evidências. Registrei essa limitação ao interpretar os acertos e as falhas.
- Confirmei que os artefatos disponíveis totalizam **5.621 chunks**, e não os 3.130 inicialmente esperados, e que essa diferença precisa ser considerada ao comparar a base atual com registros anteriores.
- Não alterei código, testes, Golden Set ou dados. `git diff --check` não apontou problemas; `git status --short` continuou mostrando somente `indice_rag.zip` como arquivo não rastreado. Não repeti a suíte completa, previamente informada como **444 testes aprovados**.

**Resultado:**
A execução integral do pipeline real foi concluída, com 14/22 acertos no Golden Set atual e 11/19 no conjunto principal. A análise demonstrou problemas de roteamento e de consulta estruturada, além de limitações de cobertura dos dados e do critério de avaliação. Nem todas as falhas podem ser explicadas conclusivamente apenas pelos logs, e nenhuma correção foi implementada nesta atividade.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio na execução controlada do Golden Set, na correlação entre relatórios, logs e código do pipeline e na organização do diagnóstico por caso.
- As conclusões foram conferidas com os artefatos locais da execução e com o comportamento observado no código. Não implementei correções durante esta atividade.

## Encontro 9 - 2026-10-02

### Relato individual - Diogo Oliveira

**Tarefa do dia:**
Validar documentalmente os casos `P3.1`, `P3.2` e `P6.1` diretamente contra o corpus real do projeto, sem alterar o Golden Set, a avaliação, o pipeline, o SQLite, o índice ou os dados do corpus.

**O que fiz:**

- Revisei a redação atual dos casos em `eval/golden_set.jsonl` e confirmei o que o projeto espera para `P3.1`, `P3.2` e `P6.1`.
- Inspecionei o corpus normalizado em `data/normalized/documents.jsonl` e os documentos normativos em `data/normalized/editais_normativo/documents.jsonl`, procurando evidência direta sobre pesquisa de preços, vigência, modalidade e microempresa/pregão.
- Consultei a base estruturada em `data/structured/base_estruturada.sqlite` para verificar se havia amostras de contratações em 2025 no Rio de Janeiro e se a modalidade aparecia com evidência rastreável.
- Tatei a distinção entre `MENÇÃO A PREGÃO` e `REGRA SOBRE PARTICIPAÇÃO DE ME/EPP EM PREGÃO`, a regra principal exigida para validar `P6.1`.
- Concluí que o corpus comprova o procedimento relacionado à pesquisa de preços, mas não comprova documentalmente a vigência em 2025 para a regra; a classificação correta foi `PARCIALMENTE VALIDADO`.
- Mantive a conclusão de que `P3.2` possui amostra e normas relacionadas, mas não possui vínculo direto e explícito suficiente para uma relação unívoca entre contrato, modalidade e norma aplicável; a classificação foi `PARCIALMENTE VALIDADO`.
- Confirmamos que `P6.1` não está sustentado pelo corpus de forma direta, porque microempresa e pregão aparecem separadamente, mas a regra de participação de ME/EPP em pregão eletrônico não está comprovada no corpus; a classificação foi `NÃO VALIDADO`.
- Registrei a evidência em formato rastreável, com `document_id`, `chunk_id`, base, trecho mínimo e o que cada evidência comprova.
- Mantive o relatório estritamente dentro do que o corpus consegue sustentar, sem preencher lacunas com conhecimento jurídico externo.
- Não alterei nenhum arquivo do projeto, nenhum dado, nem a implementação. A atividade foi somente leitura e análise documental.

**Resultado:**
A validação factual do dia confirmou que o Golden Set atual reflete apenas parte do que o corpus sustenta e que, em pontos-chave, o corpus não comprova a vigência temporal nem a regra direta exigida para os casos `P3.1` e `P6.1`. O diagnóstico foi registrado e segue como evidência factual, sem alterações no benchmark nem no pipeline.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio na organização da evidência, na revisão dos trechos normativos, na separação entre menção e regra efetiva e na consolidação do relatório final da fase de validação factual.
- As conclusões foram verificadas com o corpus, os documentos normativos e os dados estruturados disponíveis no projeto. Não houve alteração de código, dados ou avaliação durante a atividade.

## Encontro 10 — 2026-10-05

### Relato individual — Diogo Oliveira

**Tarefa do dia:**

Concluir a investigação dos casos `A1`, `A2`, `I1` e `I2` do Golden Set e realizar a correção técnica identificada no caso `P3.1`, sem alterar o Golden Set, o corpus ou as etapas principais do pipeline.

**O que fiz:**

- Analisei o relatório `eval/runs/20261004T153157Z/report.json` e os logs disponíveis, relacionando os resultados às etapas de roteamento, análise de consulta, recuperação, consulta estruturada e geração.

- Comparei os casos `A1`, `A2`, `I1` e `I2` com os registros das atas e os identificadores presentes nos dados. Confirmei que `A1`, `A2` e `I1` apresentavam rotas e evidências compatíveis com suas perguntas.

- Reconstruí o caso `I2` pelo pipeline oficial e concluí que a resposta continha os dados da ata correta, mas não reproduzia literalmente o identificador PNCP exigido pelo critério `deve_conter`. Como não havia regra funcional explícita exigindo essa repetição literal, classifiquei o comportamento como válido diante de um critério literal do Golden Set, sem alterar esse critério.

- Investiguei o falso negativo de `expected_source_ok` em `P3.1`. Identifiquei que o Golden Set utiliza o prefixo lógico `normativa`, enquanto a base documental utiliza o `base_id` `editais_normativo`.

- Inspecionei `eval/run_eval.py` e `eval/golden_set.jsonl` e implementei no evaluator somente a equivalência explícita `normativa` → `editais_normativo`, preservando a comparação por prefixo já existente para os demais casos.

- Adicionei testes de regressão para validar a equivalência, rejeitar as bases `conversacional` e `estruturada` e preservar as correspondências que já funcionavam.

- Executei os testes focados, com **20 aprovados**, e posteriormente a suíte completa, com **1.074 aprovados**.

- Executei o pipeline oficial com o Golden Set. A execução `20261005T202613Z` terminou com **13/26 acertos**. No `P3.1`, `route_ok=true`, `content_ok=true` e `refusal_ok=true`, porém não houve fonte retornada (`n_fontes=0`), mantendo `citacao_ok=false` e `expected_source_ok=false`. O teste isolado do evaluator confirmou que o novo mapeamento funciona quando uma fonte com `base_id=editais_normativo` é efetivamente citada.

- Mantive o Golden Set inalterado e não alterei router, retrieval, geração ou corpus. A alteração incidental na base SQLite durante a avaliação foi restaurada.

**Resultado:**

A investigação permitiu separar problemas de conteúdo, literalidade e avaliação. No caso `I2`, não foi identificada justificativa técnica para alterar o pipeline, pois a falha estava relacionada à exigência literal do critério do Golden Set. No `P3.1`, foi corrigida a divergência entre os identificadores `normativa` e `editais_normativo` no evaluator, com cobertura de testes e sem alteração do benchmark.

A execução oficial não permitiu observar o efeito da equivalência em uma resposta real do `P3.1`, pois aquela rodada não retornou nenhuma fonte. Portanto, a correção elimina a divergência de nomenclatura quando a fonte normativa é efetivamente citada, mas não resolve a ausência de citação nem outros critérios do caso.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio para percorrer o fluxo do evaluator e do pipeline, correlacionar logs com a implementação, analisar o contrato funcional de `I2` e localizar o ponto de comparação de fontes do evaluator.

- Revisei as alterações, executei os testes e validei os resultados da avaliação oficial. As conclusões foram baseadas nos dados, código, logs e testes locais, mantendo o Golden Set inalterado.
