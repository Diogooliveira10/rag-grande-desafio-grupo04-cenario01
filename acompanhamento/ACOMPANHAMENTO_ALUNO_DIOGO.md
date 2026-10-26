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

## Encontro 11 — 2026-10-07

### Relato individual — Diogo Oliveira

**Tarefa do dia:**

Corrigir e testar o matching de conteúdo do evaluator, especialmente o falso matching do termo `TI` no caso `P5.3`, e distinguir casos sem critério de conteúdo definido sem alterar o Golden Set ou o pipeline de RAG.

**O que fiz:**

- Inspecionei `eval/run_eval.py`, `eval/golden_set.jsonl` e `tests/test_eval.py`. Confirmei que o caso `P5.3` proíbe os termos `tecnologia da informação`, `TI` e `contrato de TI`, e exige `CRISTALIA`, `DARUNAVIR`, `TRANSFERÊNCIA DE TECNOLOGIA` e `1196949440`. O matcher anterior fazia busca por substring e podia encontrar `TI` dentro de palavras maiores.

- Implementei no evaluator matching por limites lexicais para termos simples alfanuméricos, preservando a busca por substring em expressões compostas ou com pontuação, inclusive identificadores PNCP.

- Registrei separadamente o estado da validação de conteúdo: validado, falhou, não aplicável ou não validável. Para manter compatibilidade, `content_ok` continua neutro quando a resposta não tem critério textual, mas esses casos não são tratados como conteúdo validado nem entram no cálculo de `content_accuracy`. Não alterei a fórmula de `acertou`.

- Acrescentei testes para `ti` em `estimativa`, `partir` e `último`, para o termo isolado `TI`, para `CRISTALIA`, para um identificador PNCP completo e para casos com `deve_conter` vazio, ausente ou sem resposta para validar.

- Executei os testes focados, com **34 aprovados**, a suíte completa, com **1.189 aprovados**, e `git diff --check`, sem apontamentos.

- Executei o Golden Set oficial com `src.pipeline:responder`. A rodada `20261007T193322Z` terminou com **12/26 acertos**. O `P5.3` permaneceu como falha: a resposta não continha nenhum dos quatro termos obrigatórios. O matcher novo não considerou `TI` dentro de “último”; a comparação com o matcher anterior e as mesmas respostas não apontou mudanças em `content_ok` ou `acertou`. A métrica `content_accuracy` mudou porque os casos sem critério deixaram de ser contabilizados como conteúdo validado.

- No `P3.1`, `expected_source_ok=true`. A execução oficial alterou incidentalmente a base SQLite; restaurei-a ao estado anterior e conferi que os hashes do Golden Set, corpus, índices, ZIP e requirements permaneceram iguais.

- Tentei chamar `http://localhost:8080/api/v1/history` usando a chave `RAG_API_KEY` de `.env`. A chamada não chegou ao endpoint porque não foi possível extrair a chave do arquivo `.env`; nenhuma alteração de código foi feita nessa tentativa.

**Resultado:**

A correção genérica do matching e a representação explícita da ausência de critério foram implementadas e cobertas por testes, sem alteração do Golden Set ou do pipeline de RAG. O score oficial não aumentou: `P5.3` continuou falhando por falta dos termos obrigatórios, e nenhum resultado individual mudou em relação ao matching anterior aplicado às mesmas respostas. A tentativa de consultar o histórico da API ficou pendente por ausência de uma chave extraível de `.env`.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio na inspeção do evaluator e dos testes, implementação da correção, execução dos testes e análise do relatório oficial.

- Conferi os resultados com o Golden Set, o relatório gerado, os hashes dos artefatos e os testes locais. O Golden Set e o pipeline de RAG não foram alterados.

## Encontro 12 — 2026-10-09

### Relato individual — Diogo Oliveira

Tarefa do dia:

Investigar e preparar a correção do tratamento de consultas SQL sem resultados em `src/generate.py`, garantindo que o sistema informe corretamente a ausência de registros na amostra analisada de 809 registros, sem generalizar a conclusão para todas as contratações do estado do Rio de Janeiro, e que a confiança da resposta não seja classificada como `ALTA` nesse cenário.

O que fiz até o momento:

- Inspecionei o conteúdo de `tests/test_numerical_validation.py`, identificando a função auxiliar `sql_chunk()`, que permite construir chunks SQL para testar a geração determinística de respostas. O arquivo já contém testes para totais, estatísticas e situações em que as evidências SQL são insuficientes para dispensar a geração pelo modelo.

- Analisei um trecho de `tests/test_pipeline.py` para compreender como os testes existentes verificam a confiança da resposta e a integração entre a consulta estruturada, a recuperação de informações e a geração.

- Inspecionei um trecho de `tests/test_structured_query.py`, identificando os testes de falha na consulta ao banco, ausência do arquivo de documentos e integração entre resultados SQL e evidências recuperadas de outras bases.

- Investiguei `src/structured_query.py` e confirmei que a função `\_chunks()` utiliza o marcador `"(nenhum registro)"` quando a consulta não retorna linhas. Esse detalhe exige cuidado, pois a lista `resultado_sql["linhas"]` pode conter esse marcador mesmo quando nenhum registro real foi encontrado.

- Identifiquei que a implementação já possui um mecanismo para gerar respostas determinísticas a partir de resultados SQL. A próxima etapa é verificar como `src/generate.py` identifica esses resultados e calcula o nível de confiança, para implementar a correção sem comprometer os cenários que já funcionam.

**Resultado:**

A investigação inicial identificou os testes mais relevantes e esclareceu um detalhe importante da representação de consultas vazias: a ausência de resultados reais pode estar representada por um marcador textual, e não por uma lista vazia. A implementação da correção ainda está pendente, assim como a criação dos testes de regressão e a execução da suíte de testes.

Próximas etapas:

- Inspecionar as chamadas de `\_nivel_de_confianca` e `\_resposta_sql_deterministica` em `src/generate.py`.

- Implementar o tratamento explícito de consultas sem resultados, mantendo as respostas determinísticas atuais quando houver registros.

- Garantir que o sistema informe a ausência de registros na amostra analisada de 809 registros e limite a confiança a MEDIA nesse cenário.

- Criar testes de regressão para a resposta sem resultados e para o limite de confiança.

- Executar os testes focados, a suíte completa e `git diff --check`, registrando os resultados efetivamente obtidos.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio à análise dos testes existentes, à identificação dos arquivos relevantes e à compreensão do tratamento de resultados SQL vazios.

- As sugestões foram utilizadas para orientar a investigação e definir os próximos passos da implementação. A correção ainda não foi aplicada nem validada por testes nesta etapa.

## Encontro (FERIADO) — 2026-10-12

### Relato individual — Diogo Oliveira

**Tarefa do dia:**

Implementar e validar a correção para respostas de consultas SQL sem registros reais, impedindo confiança `ALTA` e deixando claro que a ausência se refere à amostra analisada de 809 registros, sem concluir que não existam contratações correspondentes em todo o Estado.

**O que fiz hoje:**

- Analisei integralmente `_resposta_sql_deterministica()` e `_nivel_de_confianca()` em `src/generate.py`, suas chamadas e a propagação de `confidence_level` até a resposta final. Também examinei como `src/structured_query.py` constrói os chunks e registra `resultado_sql["linhas"]`.

- Confirmei que uma consulta sem linhas pode ser representada pelo marcador `"(nenhum registro)"`. Ajustei a identificação de resultados reais para que o marcador e as mensagens de ausência não sejam tratados como registros.

- Corrigi o caminho determinístico para responder consultas vazias com uma ressalva explícita: os filtros não encontraram registros nos dados disponíveis, que correspondem a uma amostra de 809 registros do corpus, e isso não comprova inexistência no universo completo do Estado. A fonte SQL é preservada.

- Limitei a confiança `ALTA` a `MEDIA` quando a ausência de resultados está explicitamente identificada, preservando `BAIXA` e a conversão preexistente de valores numéricos e textuais. A mesma proteção foi aplicada ao caminho de geração por LLM que utiliza metadados SQL de ausência.

- Preservei a resposta determinística para consultas com registros reais, incluindo os valores calculados, as fontes e a confiança alta. Também tratei partes SQL vazias de resultados divididos para que não sejam confundidas com uma consulta inteira sem resultados.

- Adicionei testes de regressão em `tests/test_numerical_validation.py` para consultas vazias e com resultados, os dois cenários de perguntas sobre ambulâncias, conversão e limitação da confiança, e o caminho LLM com metadados de ausência.

**Resultado:**

A correção foi implementada e validada. Os testes focados de geração, consulta estruturada e pipeline passaram: **205 aprovados**. A suíte completa passou com **1.416 testes aprovados**. `git diff --check` também foi executado sem apontar problemas.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio à inspeção do fluxo de geração e dos testes, implementação da correção e criação dos testes de regressão.

- Executei e conferi os testes locais focados e a suíte completa. Não foi necessária chamada real ao LLM nem dependência de banco remoto para validar os cenários unitários.

## Encontro 13 — 2026-10-14

### Relato individual — Diogo Oliveira

**Tarefa do dia:**

Executar novamente o Golden Set completo no estado atual do repositório, registrar o resultado oficial atualizado e analisar as falhas com base nos critérios do benchmark, sem alterar código de produção, o Golden Set ou os critérios de avaliação.

**O que fiz hoje:**

- Verifiquei o estado do repositório com `git status --short --branch`, `git branch --show-current` e `git log -5 --oneline`, confirmando que a branch ativa era `main` e que a mesma estava sincronizada com `origin/main` por atualização segura.

- Inspecionei os commits recentes para confirmar que a base avaliada incorporava as alterações implementadas desde a rodada de 09/10, incluindo filtros por município e fornecedor, validação de CNPJ, ajustes no pipeline, refinamentos de recorte e correções de SQL.

- Confirmei que o Golden Set continha as 26 perguntas esperadas, validando a contagem do arquivo `eval/golden_set.jsonl` e o ambiente Python do projeto.

- Ajustei o ambiente do projeto no virtualenv para garantir que as dependências necessárias estivessem disponíveis para a execução do benchmark, resolvendo a ausência de bibliotecas e a mensagem de `python-dotenv` observada durante a primeira tentativa.

- Executei o comando oficial solicitado pelo projeto:
  `py .\eval\run_eval.py run --pipeline eval.adapters:pipeline_completo`
  usando o ambiente do repositório, preservando o Golden Set e os artefatos produzidos.

- Registrei a execução real do benchmark: `run_id = 20261014T191537Z`, data `2026-10-14T19:15:37+00:00`, commit `0e4cd73` e relatório gerado em `eval/runs/20261014T191537Z/REPORT.md`.

- Examinei o relatório oficial e os resultados detalhados do JSON de execução para validar o total de perguntas, as métricas por critério e as falhas individuais.

- Classifiquei as falhas conforme o padrão do acompanhamento: identifiquei casos de problema do avaliador, ausência de dados no corpus e erros de geração, além de registrar também os casos de erro do provedor de LLM e as falhas por incompletude de resposta.

- Concluí a análise comparando o resultado atual com a rodada anterior citada no material: o sistema atual apresentou **13/26 (50%)**, a rota manteve-se estável em **25/26 (96,15%)**, mas o conteúdo ainda roda com instabilidade e há falhas relevantes em casos de insuficiência de dados, comparação temporal e composição final das respostas.

**Resultado:**

A execução do Golden Set atual foi concluída e documentada com resultados oficiais. O benchmark mostrou:

- **13/26** acertos (50%)
- **25/26** de roteamento correto
- **24/26** de recusa correta
- **22/24** citações corretas avaliadas
- **conteúdo em 15/26** por validação do relatório gerado

Os principais pontos observados foram:

- manutenção da estabilidade do roteamento;
- quedas em casos de comparação técnica/estatística e de insuficiência de evidência;
- falhas de composição em respostas que exigem campos obrigatórios ou comparação de múltiplos valores;
- dois casos de erro do provedor de geração (`A1` e `A2`), sem alteração do código de produção.

A rodada foi registrada e preservada em artefatos do projeto, sem alteração de produção, dos critérios ou do Golden Set.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio na verificação do estado do repositório, validação dos artefatos do benchmark, leitura do relatório, análise das falhas e organização do resumo de acompanhamento.

- As conclusões finais foram conferidas contra o relatório oficial gerado pelo próprio runner do Golden Set e contra o estado atual do repositório, sem modificar a lógica produzida ou o benchmark avaliado.

## Encontro 14 — 2026-10-16

### Relato individual — Diogo Oliveira

**Tarefa do dia:**

Corrigir o aumento indevido da confiança em respostas com evidência parcial e investigar a falha do caso `P4.2` na rodada `20261018T231542Z`, sem alterar o Golden Set, os critérios de avaliação ou os relatórios históricos.

**O que fiz hoje:**

- Inspecionei o estado do Git e confirmei a branch `main`, preservando alterações preexistentes de outros arquivos.

- Analisei o fluxo de confiança em `src/generate.py`. Confirmei que `_aplicar_recorte()` combinava a confiança do LLM com a do recorte SQL usando `max()` quando o recorte era considerado sólido, permitindo que o resultado SQL elevasse uma confiança baixa ou média do LLM.

- Alterei a combinação para usar o menor nível entre o declarado pelo LLM e o limite do recorte. Assim, o SQL pode limitar a confiança quando a execução é parcial ou o cálculo não é verificável, mas não eleva a confiança declarada pelo modelo. O caminho determinístico permanece inalterado e continua usando a confiança calculada diretamente do recorte.

- Atualizei e acrescentei testes de regressão em `tests/test_generate.py`, cobrindo LLM com confiança baixa e recorte completo de confiança alta, resposta que declara evidência parcial, execução SQL parcial, recusa e ausência de recorte. Os testes existentes também verificam que resultados determinísticos com dados continuam podendo ter confiança alta.

- Executei `tests/test_generate.py` e `tests/test_numerical_validation.py`: **113 testes aprovados**. Executei também os testes de P4.2 e P4.3 em `tests/test_eval.py`: **9 aprovados**. `git diff --check` passou. Não executei uma avaliação completa.

- Investiguei os relatórios e os logs da rodada `20261018T231542Z`. A rodada teve **2/3 acertos**; P4.1 e P4.3 passaram e P4.2 falhou na validação de conteúdo. Rota e citações foram aceitas, mas a resposta de P4.2 afirmou que pregão era predominante e saiu com confiança `alta`, contrariando os critérios do caso.

- Confirmei nos logs que a etapa SQL de P4.1 filtrou contratos assinados em 2025 com os termos `manutencao` e `computador`, retornando os três registros esperados, todos com modalidade não informada nos resultados registrados.

- Para P4.2, confirmei que a etapa de consulta SQL foi pulada como `nao_aplicavel`. A busca recuperou três documentos sobre outros objetos — arquivos deslizantes, instalações civis/elétricas e centrais telefônicas — cujos amparos legais mencionavam pregão. O LLM usou esses documentos para atribuir modalidade aos contratos de manutenção de computadores, embora não fossem os mesmos contratos.

- Verifiquei que o detector atual não reconhece “predominante” como pedido de agrupamento e, para a pergunta exata, não gera consulta estruturada. A alteração de termos SQL do Renato já está integrada no histórico da branch; ela funciona para P4.1, mas não resolve a ausência de consulta em P4.2.

- Não alterei `structured_query.py`, o Golden Set, os critérios ou os relatórios históricos. Não fiz commit nem push.

**Resultado:**

A correção da confiança foi implementada e passou nos testes pertinentes. A falha de P4.2 foi diagnosticada como falha de consulta/escopo e geração, não como falha de roteamento ou citação: o SQL não foi executado, a recuperação trouxe evidências de outros contratos e a resposta generalizou indevidamente a modalidade. A correção de confiança não intercepta esse caminho sem SQL e, por si só, não impede que um LLM declare confiança alta.

O próximo teste recomendado é fazer com que a pergunta de P4.2 acione uma consulta agrupada por modalidade, com os mesmos filtros de P4.1, e validar que os três registros retornados mostram modalidade ausente, que a resposta reconhece a insuficiência e que a confiança não ultrapassa `MEDIA`.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio à análise do fluxo de confiança, implementação da alteração e elaboração dos testes de regressão.

- Também utilizei apoio para correlacionar o relatório da rodada P4.2 com os logs de recuperação, consulta SQL e geração. As conclusões foram conferidas contra o código, os testes e os artefatos locais; não houve alteração do Golden Set ou dos relatórios históricos.

## Encontro 15 — 2026-10-19

### Relato individual — Diogo Oliveira

**Tarefa do dia:**

Corrigir o caso `P2.2` do Golden Set para que a solicitação de mediana do valor por veículo recupere os registros relevantes sem tratar o valor global do contrato como preço unitário, declare a insuficiência dos dados e limite adequadamente a confiança, preservando o comportamento do caso `P2.1`.

**O que fiz hoje:**

- Inspecionei o estado do Git antes de editar e confirmei que o worktree estava limpo. Revisei os casos `P2.1` e `P2.2` do Golden Set e os fluxos de análise da pergunta, construção da consulta estruturada, execução SQL e definição da confiança.

- Confirmei a causa raiz: quando `query_analysis` identificava `tipo_valor="unitario"`, `detectar_consulta()` retornava `None`. Dessa forma, a execução estruturada era descartada, mesmo havendo no corpus um contrato municipal de ambulância tipo D com valor global registrado de R$ 0,00.

- Ajustei a consulta para recuperar uma listagem dos registros relevantes quando o pedido é de valor unitário indisponível. A alteração preserva os filtros de ano e objeto, remove termos da solicitação de cálculo que não descrevem o objeto contratado e não calcula a mediana a partir do valor global.

- Propaguei a indisponibilidade do cálculo até a geração da resposta e implementei uma resposta determinística para esse cenário: ela reconhece o registro encontrado, apresenta R$ 0,00 somente como valor total/global efetivamente retornado pelo SQL, esclarece que esse valor não é o preço por veículo e informa que a mediana solicitada não pode ser calculada. A confiança final fica baixa por causa da insuficiência de dados para a agregação.

- Ajustei o caminho padrão dos documentos para usar o snapshot local `data/normalized/v6/documents.jsonl` quando o arquivo normalizado sem versão estiver ausente. Preparei a base SQLite local para executar a avaliação; essa etapa resolveu a ausência do corpus apontada na primeira execução.

- Acrescentei testes de regressão para classificação e construção da consulta unitária, recuperação do registro de ambulância tipo D com valor global zero, distinção entre registro existente e preço unitário indisponível, resposta determinística sem chamada ao LLM e confiança baixa. Também mantive verificações para consultas unitárias que não devem passar a agregar valores globais.

- Executei os testes relacionados de consulta estruturada, análise de perguntas, pipeline, geração e rota rápida: **705 testes aprovados**. Foi emitido um aviso não bloqueante durante a limpeza de um arquivo SQLite temporário em uso; os testes foram concluídos com sucesso. `git diff --check` também passou.

- Executei a avaliação direcionada `P2.1`/`P2.2`. A primeira execução não passou porque o caminho configurado para os documentos não existia e o banco local não estava preparado; após corrigir o fallback para o snapshot e construir a base local, repeti a avaliação e obtive **2/2 acertos**. `P2.1` permaneceu aprovado com confiança alta. `P2.2` reconheceu um registro correspondente, distinguiu o valor total de R$ 0,00 do preço por veículo, declarou que a mediana não pode ser calculada e recebeu confiança baixa.

- Mantive `eval/golden_set.jsonl`, os critérios de avaliação e os relatórios históricos sem alterações. Não fiz commit nem push.

**Resultado:**

O caso `P2.2` passou a recuperar o contrato existente e a explicitar que os dados disponíveis não informam o preço por veículo, portanto não sustentam a mediana solicitada. O valor total registrado de R$ 0,00 não é interpretado como preço unitário. O caso `P2.1` continuou aprovado. A avaliação direcionada terminou com **2/2 acertos**.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio à inspeção do código, análise da causa raiz, implementação da correção e elaboração dos testes de regressão.

- Conferi os resultados contra o SQL executado sobre o snapshot local, os testes automatizados e o relatório da avaliação direcionada. Não alterei o Golden Set, os critérios ou os relatórios históricos.

## Encontro 16 — 2026-10-21

### Relato individual — Diogo Oliveira

**Tarefa do dia:**

Preparar a apresentação do projeto para o Demo Day, previsto para 23/10/2026, estudando o material elaborado pela equipe e organizando a explicação da arquitetura do sistema para a banca avaliadora. A preparação deve seguir as orientações oficiais quanto ao tempo de apresentação, à participação de todos os integrantes e à demonstração do funcionamento, incluindo exemplos reais, falhas e limitações.

**O que fiz hoje:**

- Acompanhei a preparação da prévia da apresentação do projeto para o Demo Day, previsto para o dia 23/10/2026.

- Li os slides preparados pela equipe e o guia de estudos para a defesa, buscando compreender a organização da apresentação, os principais pontos técnicos do projeto e a melhor forma de comunicá-los à banca avaliadora.

- Revisei as orientações oficiais para a apresentação, que estabelecem uma duração de 15 minutos, com limite máximo de 20 minutos, e exigem a participação de todos os integrantes da equipe em pelo menos uma parte da exposição.

- Considerei a estrutura proposta para a apresentação, organizada em cinco partes: problema e motivação, dados utilizados, arquitetura e funcionamento, apresentação do sistema e demonstração prática, contemplando exemplos reais, casos de falha e limitações conhecidas.

- Fiquei responsável pela explicação da arquitetura do sistema, correspondente aos slides 7, 8 e 9. Estou preparando minha fala com foco em apresentar a organização dos componentes, o funcionamento da solução e a relação entre as partes da arquitetura de maneira clara, objetiva e tecnicamente correta.

- Direcionei a preparação para uma explicação acessível à banca, buscando traduzir os aspectos técnicos em uma narrativa compreensível, sem perder a precisão necessária para demonstrar o funcionamento da solução desenvolvida pela equipe.

- Não realizei alterações no código-fonte nesta etapa. Conforme o planejamento da equipe, o código permanecerá congelado até o dia da apresentação, priorizando a preparação da defesa e a estabilidade da versão que será demonstrada.

**Resultado:**

A prévia da apresentação foi preparada e as responsabilidades individuais foram distribuídas. Fiquei responsável pela apresentação da arquitetura do sistema nos slides 7, 8 e 9 e iniciei a preparação da minha fala com base nos slides e no guia de estudos da defesa. O foco atual está na clareza da explicação técnica e na preparação para o Demo Day de 23/10/2026, mantendo o código congelado até a apresentação.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio à organização e à redação do relato individual de acompanhamento das atividades do encontro.

- A preparação da apresentação está sendo orientada pelo material elaborado pela equipe e pelas instruções oficiais do Demo Day, com foco na compreensão da arquitetura e na comunicação clara dos aspectos técnicos do projeto.

## Encontro 17 — 2026-10-26

### Relato individual — Diogo Oliveira

**Tarefa do dia:**

Investigar a alteração de `src/reformulacao.py` sem implementar correções novas, validando a falha de `P4.3`, o resultado `17/36` do Golden Set, o comportamento das regras determinísticas e o timeout efetivo.

**O que fiz hoje:**

- Revisei o diff completo dos arquivos `src/reformulacao.py`, `config.py`, `tests/test_reformulacao.py` e `.env.example`, confirmando que a mudança ficou restrita ao escopo esperado de reformulação e que o timeout foi ajustado para 15 segundos.

- Verifiquei o mecanismo de timeout em `src/reformulacao.py` e confirmei que a chamada ao LLM usa `future.result(timeout=REFORMULACAO_TIMEOUT)`. Em caso de falha, o código preserva o contexto com `_com_contexto()` e registra `llm_falhou` ou `saida_invalida`.

- Inspecionei o relatório oficial da execução `golden-2026-10-26-v7-36-reformulacao-check` em `eval/runs/golden-2026-10-26-v7-36-reformulacao-check/report.json` e o gabarito em `eval/golden_set.jsonl`. O caso `P4.3` foi avaliado como `content_ok=false`, `acertou=false`, com recusa genérica e latência de `44.097 ms`.

- Constatei que a pergunta reformulada de `P4.3` é uma continuação forte (“mesmos contratos…”) e não um caso de `troca_de_lugar`, `troca_de_esfera` ou troca de ano. A falha mais provável está no caminho de continuação/LLM e no tratamento do histórico, e não na regra determinística.

- Verifiquei que as regras de lugar e esfera continuam sendo resolvidas sem chamar o LLM, enquanto a pergunta completa com “encontrados em 2025” não dispara a reformulação.

- Executei a suíte focada de reformulação: `./.venv/Scripts/python.exe -m pytest -q tests/test_reformulacao.py`, obtendo **48 testes aprovados**.

- Tentei a execução de testes relacionados a análise de perguntas, roteamento e SQL, mas o snapshot atual não contém `tests/test_routing.py`, então não houve evidência válida adicional nesse escopo.

- Constatei que o relatório do resultado `17/36` não pode ser atribuído diretamente à mudança de reformulação, porque o benchmark comparável solicitado (`golden-2026-10-27-v7-36-interacao`) não está disponível neste repositório e o ambiente atual não está totalmente verde fora do escopo de reformulação.

- Mantive o Golden Set, os critérios de avaliação e os relatórios históricos intactos e não realizei `commit`, `push` nem alterações de código nesta etapa.

**Resultado:**

A investigação confirmou que o timeout efetivo da reformulação é de 15 segundos, que o fallback preserva contexto e que os controles de regras determinísticas continuam funcionando como esperado. A falha de `P4.3` permanece como um caso de continuação complexa que precisa de investigação específica antes de qualquer alteração do prompt ou filtro compartilhado. O conjunto de testes focados em reformulação passou e o resultado de `17/36` foi documentado como evidência do estado atual do ambiente, sem comparativo válido de antes/depois.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio na revisão do diff, análise do relatório de avaliação do Golden Set, diferenciação entre evidências e hipóteses, e organização da investigação final.

- Mantive o foco na documentação do estado atual do projeto, sem alterar regras, Golden Set, critérios de avaliação, relatórios históricos ou o código-fonte.
