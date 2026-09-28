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


## Encontro 5 - 2026-09-23

### Relato individual - Pâmela de Freitas

- Ela possui os comandos rota e perguntar.
- Implementei uma CLI simples para testar o projeto em terminal.
- rota valida em quais bases a pergunta deve buscar.
- perguntar executa o pipeline completo.
- Também aceita várias perguntas em uma mesma execução.
- A validação foi confirmada no terminal

Exemplos de uso:

- python app/cli.py --help

- python app/cli.py rota 
- e aí abre um input pra fazer a pergunta no terminal

- python app/cli.py perguntar
- e aí abre um input pra fazer a pergunta no terminal

Ou direto com a pergunta:

- python app/cli.py rota "Quais contratos de software foram assinados acima de 500 mil?"
- python app/cli.py perguntar "Quais contratos de software foram assinados acima de 500 mil?"

## Encontro 6 - 2026-09-25

### Relato individual - Pâmela de Freitas

- Concluí a coleta oficial das quatro bases. Tive resolver um erro de certificado SSL usando o bundle certifi do Python, a coleta terminou completa com 222 arquivos, 200 itens e zero falhas.

- Preparei as bases e gerei um índice local com 3.130 chunks, todos com vetores de dimensão 1024.
Fiz um teste real com retrieve, que retornou três resultados.Não rodei a avaliação completa do golden set.

- Os dados brutos, os documentos normalizados e o índice continuam locais e ignorados pelo Git. Portanto, o commit dos inventários não entrega o índice pra vcs, para usarem o retrieve, cada um precisa gerar os artefatos na própria máquina

### Uso de assistentes de IA:

- Usei para ajudar no erro de certificado que estava dando em um dos documentos