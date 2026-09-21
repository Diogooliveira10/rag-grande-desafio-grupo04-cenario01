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
