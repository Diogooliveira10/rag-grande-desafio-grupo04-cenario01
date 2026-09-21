# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Relato individual - Pâmela de Freitas

- Por hoje participei na discussão para escolha do cenário, escolhemos o cenário 1
- Resolvemos a arquitetura em grupo
- Fiz as anotações do time 4 no notion
- Dei git clone e preparei o ambiente para iniciar o projeto


## Encontro 2 - 2026-09-16

### Relato individual - Pâmela de Freitas

- Hoje fizemos o arquivo de arquitetura, eu fiz a parte de pipeline, o diagrama e stage. Ainda vamos discutir sobre os nomes se vão mudar ou ficarão esses mesmos do arquivo.


## Encontro 3 - 2026-09-18

### Relato individual - Pâmela de Freitas

- Minha parte individual hoje foi fazer o arquivo config.py

## Encontro 4 - 2026-09-21

### Relato individual - Pâmela de Freitas

- Criado src/router.py.
- Criada a função rotear_pergunta(query).
- A função retorna RouteDecision, conforme definido em src/schema.py.
- O roteador identifica as quatro naturezas:
    - 1 - normativa;
    - 2 - estruturada;
    - 3 - conversacional;
    - 4 - agregada.
- Permite rotas combinadas, como estruturada + agregada.
- Trata perguntas sobre fraude, ilegalidade e dados sensíveis como recusa.
- Trata perguntas fora do escopo, como previsões futuras.
- Normaliza letras maiúsculas, minúsculas e acentos.
- Não acessa os índices nem chama o LLM. Ele apenas decide para quais bases a busca deverá ir.
Também foi criado tests/test_router.py, com 7 testes cobrindo esses comportamentos.