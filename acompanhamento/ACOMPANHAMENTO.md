# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Resumo do dia (escrito em conjunto)

## **Entregamos hoje:**

- Hoje iniciamos o Grande Desafio da Residência, definindo o **Cenário 1 - Contratações Públicas do RJ** como tema do projeto. A escolha foi baseada na disponibilidade de informações verificáveis e na diversidade de fontes, que permite explorar o conceito de Adaptive RAG.

- Também realizamos a organização inicial do projeto, incluindo a criação do repositório no GitHub, definição da arquitetura inicial da solução, elaboração das personas e perguntas de referência, além do planejamento inicial do corpus e das quatro bases de conhecimento: normativa, estruturada, conversacional e agregada.

- Durante a atividade, também alinhamos a divisão das responsabilidades entre os integrantes e os próximos passos para a implementação do pipeline de RAG, incluindo análise da consulta, roteamento, filtros de metadados, recuperação híbrida, reranking, geração e validação das respostas.

## **Ficou pendente:**

- Sem pendencias.

## **Bloqueios em aberto:**

- Sem bloqueios.

## Encontro 2 - 2026-09-16

## **Entregamos hoje:**

Durante o encontro, a equipe avançou na definição inicial do projeto do Grande Desafio, tanto na parte de organização do repositório quanto na estrutura conceitual do sistema.

Foram discutidas e encaminhadas as seguintes atividades:

- definição de como organizar o trabalho entre os integrantes, incluindo estratégia de branches e commits individuais;
- finalização do cenário escolhido, _Contratações Públicas do Estado do Rio de Janeiro_, incluindo as duas frases de justificativa;
- discussão e definição dos principais _stages do pipeline_, como análise da consulta, roteamento, recuperação, fusão, reranking, geração, validação e feedback;
- identificação das principais fontes de dados, com destaque para PNCP, Planalto e fontes oficiais do Governo do Estado do Rio de Janeiro;
- discussão sobre a diferença entre dados coletados por API, documentos obtidos por URL e dados posteriormente armazenados localmente;
- análise da estratégia de embeddings, considerando a necessidade de geração inicial, cache em disco, persistência dos índices e reutilização posterior para evitar reprocessamento desnecessário;
- início da coleta efetiva de fontes e criação de scripts para tornar essa coleta reproduzível e seletiva.

O grupo também avançou na compreensão dos entregáveis iniciais, principalmente ARQUITETURA.md, PERSONAS.md, corpus_manifest.yaml, organização dos dados e divisão de responsabilidades.

## **Ficou pendente:**

- Sem pendencias.

## **Bloqueios em aberto:**

- Sem bloqueios.
