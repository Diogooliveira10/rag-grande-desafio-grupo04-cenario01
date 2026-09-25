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
