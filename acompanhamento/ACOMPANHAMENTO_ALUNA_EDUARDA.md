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

## Encontro 10 - 2026-10-05

### Relato individual - Eduarda

- No repositório do frontend, planejei como a rastreabilidade das consultas deverá ser apresentada ao usuário.
- Estruturei o fluxo para exibir de forma clara o caminho real de cada consulta, incluindo o uso de SQL, a busca vetorial e as principais etapas do pipeline, sempre vinculadas ao respectivo `query_id`.
- Preparei essa estrutura para consumir o trace real do backend quando ele estiver disponível, sem utilizar dados mockados ou simular informações que ainda não são fornecidas pela API.

## Encontro 11 - 2026-10-07

### Relato individual - Eduarda

- Atualizei o backend e o frontend com as versões mais recentes da `main` e validei a integração local entre os dois projetos.
- Implementei o controle de sessão e o envio do histórico da conversa, mantendo o contexto entre perguntas e descartando os dados ao iniciar uma nova conversa.
- Desenvolvi as telas "Ver caminho" e "Caminho da conversa", exibindo as bases consultadas, evidências, etapas do pipeline, tempo de execução e detalhes do trace.
- Corrigi a segurança do frontend, removendo o uso de `X-API-Key` e o histórico geral do site público, além de adicionar tratamentos para erros de sessão, trace antigo e falhas de rede.
- Melhorei o indicador de carregamento e validei a implementação com testes automatizados, build do frontend e chamadas reais aos endpoints de trace.

## Encontro 12 - 2026-10-09

### Relato individual - Eduarda

- Finalizei a integração do frontend com o fluxo de conversas do backend, armazenando o `session_id` em `sessionStorage`, enviando o histórico recente para manter o contexto e recuperando automaticamente sessões inválidas.
- Implementei a rastreabilidade das respostas e da conversa, com resumos das bases SQL e vetorial, evidências, fontes citadas, confiança, tempo de execução e linha do tempo das etapas do pipeline.
- Adicionei detalhes de auditoria ao trace, incluindo scores, movimentação no rerank, consultas SQL, resultados, entradas, saídas e prompts recolhíveis.
- Ajustei os estados de carregamento e erro para uma experiência mais clara e amigável, com tempo decorrido, previsão aproximada, nova tentativa e mensagens específicas para traces indisponíveis ou pertencentes a outra conversa.
- Reforcei a segurança do site ao remover a chave administrativa e o histórico geral do frontend público, utilizando apenas `X-Session-Id` para acessar os traces da própria conversa.
- Atualizei os tipos, serviços, documentação, testes automatizados e arquivos do GitHub Pages, validando o funcionamento integrado com a API real.

## Encontro 13 - 2026-10-14

### Relato individual - Eduarda

- Finalizei a integração do frontend para a demonstração, ajustando as telas e os serviços para utilizar o contrato atual da API e a URL do backend configurada por `VITE_API_BASE_URL`.
- Implementei a página de histórico da conversa atual, com estados de carregamento, vazio e erro, enviando o `X-Session-Id` para mostrar somente as consultas da própria sessão e sem expor a chave administrativa no site público.
- Ajustei o envio de feedback para utilizar a sessão do usuário e melhorei a experiência em consultas demoradas, exibindo uma mensagem amigável de timeout e a opção de tentar novamente.
- Adicionei comandos de diagnóstico para verificar a saúde do backend e executar uma consulta real no pipeline, além do suporte centralizado ao header necessário para acesso por túnel ngrok.
- Ampliei os testes automatizados do histórico, feedback, serviços de exploração, timeout e nova tentativa, e corrigi a sincronização dos arquivos gerados para o GitHub Pages no Windows.
- Atualizei a documentação de integração e o build publicado, registrando que uma demonstração pública precisa de um backend HTTPS e validando localmente o frontend integrado ao backend real.

## Encontro 14 - 2026-10-16

### Relato individual - Eduarda

- Dei continuidade à preparação do frontend para a demonstração pública, acompanhando os ajustes necessários para conectar o GitHub Pages ao backend da aplicação.
- Colaborei na validação da configuração de deploy, que passou a definir corretamente a URL pública da API durante o processo de build.
- Acompanhei a criação de uma verificação automatizada da configuração do GitHub Pages, com testes para evitar a publicação do frontend sem o endereço correto do backend.
- Revisei as atualizações da documentação de deploy e integração, incluindo as orientações para configurar a variável `VITE_API_BASE_URL` e validar a aplicação publicada.

## Encontro 15 - 2026-10-19

### Relato individual - Eduarda

- Revisei e integrei ao repositório principal do frontend as correções de configuração do GitHub Pages e as melhorias desenvolvidas para a demonstração.
- Integrei a versão final da visualização detalhada da rastreabilidade, com aprimoramentos na apresentação das evidências, filtros, uso de modelos e observações das etapas do pipeline.
- Revisei e incorporei as melhorias colaborativas da tela de transparência e do resumo do projeto, incluindo a exibição do corpus ativo, do snapshot anterior e a renderização das respostas em Markdown.
- Corrigi a declaração das dependências utilizadas pelo renderizador de Markdown, garantindo que o build e a instalação do frontend incluíssem todos os pacotes necessários.
- Consolidei as contribuições das branches de trabalho na `main` do frontend, deixando a versão integrada pronta para publicação e validação da equipe.
