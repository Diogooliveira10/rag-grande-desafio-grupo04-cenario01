# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Relato individual - Eduarda

- Participei da escolha do cenário e decidimos em grupo pelo Cenário 1.
- Ajudei na discussão sobre a organização do projeto.
- Também participei da definição dos próximos passos do grupo.

## Encontro 2 - 2026-09-16

### Relato individual - Eduarda

- Estive presente na aula e acompanhei as atividades do grupo. Hoje não realizei contribuições individuais para o projeto.

## Encontro 3 - 2026-09-18

### Relato individual - Eduarda

- Desenvolvi o documento `PERSONAS.md` para o Cenário 1, definindo seis personas com perfil, contexto de uso, necessidade e urgência.
- Elaborei 18 perguntas de referência, três por persona, com tipo de pergunta e rota esperada para orientar a avaliação do Adaptive RAG.
- Incluí uma pergunta de falha esperada sobre a comparação de contratações de ambulâncias, com hipótese de falha e comportamento esperado do sistema.
- Organizei um resumo das perguntas e da cobertura de tipos e rotas no documento.

## Encontro 4 - 2026-09-21

### Relato individual - Eduarda

- Desenvolvi o módulo `src/obs.py` para a observabilidade do pipeline, com geração de `run_id` e `query_id`, registro estruturado em JSONL, medição de latência e captura do consumo de tokens das chamadas ao LLM.
- Implementei suporte a context manager e decorator para registrar as etapas do pipeline, incluindo execuções concluídas com sucesso e falhas sem ocultar as exceções.
- Criei o arquivo `tests/test_obs.py` com testes para o contrato do log, contagem de tokens, registro de erros, validação dos estágios e geração dos identificadores.
- Refatorei o módulo para deixar o código e as docstrings mais diretos e legíveis, removendo comentários excessivos sem alterar o comportamento.
- Executei a suíte de testes do projeto e confirmei que os 11 testes passaram.


### Uso de assistente de IA

- Utilizei um assistente de IA para apoiar a implementação inicial, a revisão das docstrings, a criação dos testes e a validação do módulo. Revisei o resultado, solicitei a simplificação dos comentários e executei os testes antes de versionar o código.
