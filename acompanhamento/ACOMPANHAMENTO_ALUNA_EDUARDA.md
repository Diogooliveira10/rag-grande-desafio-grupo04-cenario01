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

## Encontro 5 - 2026-09-23

### Relato individual - Eduarda

- Estruturei as partes do frontend que serão implementadas no projeto, definindo os principais elementos e funcionalidades da interface.
- Organizei o fluxo de interação do usuário, considerando a realização de perguntas, a apresentação das respostas geradas pelo sistema e a exibição das fontes consultadas.
- Validei a estrutura planejada de acordo com os requisitos do projeto e com as funcionalidades já disponíveis no backend.
- Registrei as definições realizadas para orientar a etapa de implementação e garantir a integração do frontend com o pipeline de Adaptive RAG.

## Encontro 6 - 2026-09-25

### Relato individual - Eduarda

- Desenvolvi o frontend e a interface do projeto.
- Realizei uma prévia da conexão local entre o frontend e o backend.
- A integração completa com o pipeline de Adaptive RAG ainda está pendente.
- Disponibilizei uma versão do frontend em: [https://etuarda.github.io/frontendRAG/](https://etuarda.github.io/frontendRAG/).

## Encontro 7 - 2026-09-28

### Relato individual - Eduarda

- Atualizei o arquivo `ARQUITETURA.md`.

## Encontro 8 - 2026-09-30

### Relato individual - Eduarda


- Concluí a integração do frontend com a API do projeto, incluindo os fluxos de consulta, exibição das respostas, fontes consultadas e histórico.
- Organizei e versionei o código do frontend exclusivamente na branch `developerEduarda`, mantendo a branch `main` sem essa implementação. 
- Como próxima atividade, vou revisar e atualizar o arquivo `ARQUITETURA.md` para documentar a integração do frontend, o estado atual da solução e as próximas etapas do projeto.

## Encontro 9 - 2026-10-02

### Relato individual - Eduarda

- Não pude participar do encontro por motivo de mal-estar.
- Mesmo ausente, dei continuidade ao projeto e integrei o frontend NEXO aos dados reais do backend. No frontend, removi mocks, dados fixos e respostas simuladas, passando as telas de consulta, histórico, feedback, contratações, documentos, órgãos, fontes e transparência a consumir a API real, com estados de carregamento, vazio e erro.
- No backend, disponibilizei os endpoints necessários para o frontend sem alterar o pipeline RAG existente, utilizando o histórico e o feedback persistidos, a base estruturada, o inventário e o manifesto reais do projeto. Também documentei os schemas e o contrato HTTP em [`docs/FRONTEND_API_CONTRACT.md`](../docs/FRONTEND_API_CONTRACT.md).
- Concluí e validei localmente a integração, com o React na porta 5173 e a API FastAPI na porta 8080. Realizei consultas reais ao pipeline RAG, validei CORS, histórico, feedback e telas de exploração, além dos testes automatizados e do build do frontend.
- Registrei o passo a passo em [`docs/GUIA_INTEGRACAO_LOCAL_FRONTEND_BACKEND.md`](../docs/GUIA_INTEGRACAO_LOCAL_FRONTEND_BACKEND.md), incluindo o clone do repositório [`Etuarda/frontendRAG`](https://github.com/Etuarda/frontendRAG), a configuração dos arquivos `.env`, a inicialização dos dois projetos e os testes de conexão.
