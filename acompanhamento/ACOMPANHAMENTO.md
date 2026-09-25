# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Resumo do dia (escrito em conjunto)

## **Entregamos hoje:**

- Hoje iniciamos o Grande Desafio da Residência, definindo o **Cenário 1 - Contratações Públicas do RJ** como tema do projeto. A escolha foi baseada na disponibilidade de informações verificáveis e na diversidade de fontes, que permite explorar o conceito de Adaptive RAG.

- Também realizamos a organização inicial do projeto, incluindo a criação do repositório no GitHub, definição da arquitetura inicial da solução, elaboração das personas e perguntas de referência, além do planejamento inicial do corpus e das quatro bases de conhecimento: normativa, estruturada, conversacional e agregada.

- Durante a atividade, também alinhamos a divisão das responsabilidades entre os integrantes e os próximos passos para a implementação do pipeline de RAG, incluindo análise da consulta, roteamento, filtros de metadados, recuperação híbrida, reranking, geração e validação das respostas.

## **Ficou pendente:**

- Sem pendências.

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

- Sem pendências.

## **Bloqueios em aberto:**

- Sem bloqueios.

## Encontro 3 - 2026-09-18

## **Entregamos hoje:**

- Hoje foram adicionadas e estruturadas as personas do projeto, incluindo a criação de três novas personas e suas respectivas perguntas de referência, seguindo o padrão definido no PERSONAS.md. Também foi implementada a extração do corpus orientada por manifesto, utilizando o corpus_manifest.yaml como fonte única de configuração, com suporte a múltiplos formatos e fontes, controle de tentativas, checkpoints, retomada de processos e versionamento imutável.

- Foi desenvolvido o config.py para centralização de credenciais e parâmetros, além da geração de inventário com hash SHA-256 e comparação entre versões do corpus. Também foram adicionados testes offline para validação da extração e dos contratos do inventário, bem como a documentação inicial no README.md e a revisão do projeto no Notion para acompanhamento das entregas.

## **Ficou pendente:**

- Sem pendências.

## **Bloqueios em aberto:**

- Sem bloqueios.

## **Uso de assistentes de IA:**

- Foram utilizados assistentes de IA para apoio em correções pontuais no código, revisão de implementação e esclarecimento de dúvidas técnicas durante o desenvolvimento.

## Encontro 4 - 2026-09-21

## **Entregamos hoje:**

- A equipe avançou significativamente na implementação do pipeline de RAG, com o desenvolvimento dos principais módulos de roteamento, indexação, recuperação e geração. Também foram criados e executados testes para validar os componentes desenvolvidos.

- Foi implementado o src/router.py, responsável pela análise das perguntas e definição das bases de conhecimento que devem ser consultadas. O roteador contempla as quatro naturezas previstas no projeto, normativa, estruturada, conversacional e agregada, permite rotas combinadas, normaliza diferenças de maiúsculas, minúsculas e acentuação e trata casos de recusa, como solicitações envolvendo dados sensíveis, fraude ou ilegalidade, além de perguntas fora do escopo. Foram criados 7 testes para validação do módulo.

- Também foi desenvolvido o index.py, responsável pela estruturação dos índices utilizados na recuperação dos dados, juntamente com seus testes e os fixtures do projeto. Foram realizados 12 testes, todos executados com sucesso. Além disso, foi iniciada a estrutura de avaliação (Eval) para apoiar as próximas etapas de validação do sistema.

- O módulo src/retrieve.py foi desenvolvido e revisado, contemplando recuperação híbrida com busca densa e BM25, filtros de metadata, fusão por RRF e preparação para integração futura com rerank. Também foram corrigidos problemas identificados durante a auditoria técnica, incluindo tratamento de queries vazias, deduplicação de chunks, normalização de tipos e suporte a datas e valores monetários em diferentes formatos. O módulo foi validado com testes específicos e verificações de compatibilidade com os componentes existentes.

- A equipe também avançou na implementação do generate.py, responsável pela geração da resposta final a partir dos chunks recuperados. Foram adicionados guardrails para bloqueio de solicitações envolvendo dados bancários e credenciais, além de parsing defensivo para lidar com respostas do LLM fora do formato esperado, utilizando recusa segura nesses casos. Foram realizados 9 testes, todos passando.

- De forma geral, o encontro marcou um avanço na integração das principais etapas do pipeline, aproximando o projeto de um fluxo completo de análise da consulta → roteamento → indexação/recuperação → geração e validação da resposta.

## **Ficou pendente:**

- Continuidade da integração entre os módulos desenvolvidos e evolução da estrutura de avaliação do sistema.

- Finalização e validação das etapas restantes do pipeline, conforme os contratos definidos no projeto.

## **Bloqueios em aberto:**

- Sem bloqueios críticos identificados no encontro.

## **Uso de assistentes de IA:**

- Foram utilizados assistentes de IA como apoio ao desenvolvimento, revisão de código, identificação e correção de problemas, elaboração de testes e esclarecimento de dúvidas técnicas. As implementações foram analisadas e validadas pela equipe de acordo com os requisitos e a arquitetura do projeto.

## Encontro 5 - 2026-09-23

## **Entregamos hoje:**

- Foi realizada uma nova análise do projeto atualizado, com foco na compatibilidade do `src/retrieve.py` com os novos arquivos, módulos, contratos e integrações adicionados ao projeto.

- Foi desenvolvida a suíte de testes automatizados `tests/test_retrieve.py`, utilizando mocks, dados controlados e testes isolados para validar o comportamento do módulo sem dependência de serviços externos, APIs ou modelos reais. Foram contemplados cenários de query vazia, recuperação densa, BM25, recuperação híbrida, fusão por RRF, ranking, deduplicação por `chunk_id`, filtros de metadata, diferentes formatos de datas e valores monetários brasileiros, leitura de valores em `metadata.extras`, operadores inválidos, validação de `k`, reranking opcional e limite de `top_k`.

- A suíte de testes do `retrieve.py` foi estruturada seguindo princípios de Clean Code, com funções auxiliares para criação de registros controlados, parametrização de cenários, nomes descritivos e testes isolados e determinísticos. Foram validados **26 testes específicos do módulo**.

- Foi criada a suíte `tests/test_ingest.py`, com **20 testes**, cobrindo a divisão dos documentos em chunks, geração e validação do hash SHA-256, geração do JSONL e do relatório, funcionamento do modo `--dry-run`, validação da linha de comando e verificação dos metadados obrigatórios em todos os chunks, conforme definido no tópico 2.5 do projeto.

- Foi implementada a política de sensibilidade em `src/sensitivity.py`, contemplando o mascaramento de CPF, telefone e e-mail pessoal, com preservação de e-mails pertencentes a órgãos públicos, além do descarte de chunks classificados como restritos. A política foi integrada ao `src/generate.py` em dois pontos do fluxo: antes do envio do contexto ao LLM e novamente sobre a resposta final. Foram criados **27 testes em `tests/test_sensitivity.py`** e a política foi documentada no README.

- Foi concluída a implementação do logging do pipeline, conforme o tópico 2.4. As etapas de roteamento, busca densa, BM25, fusão por RRF, reranking e geração passaram a registrar informações utilizando o formato definido em `src/obs.py`, com as etapas vinculadas pelo mesmo `run_id`. Na etapa de geração, também são registrados modelo e tokens, mantendo o mascaramento de dados pessoais nos logs.

- Durante a implementação do logging, foi identificado e corrigido um problema no `generate.py`: o módulo utilizava uma função `chat_completion` que não existia no `llm_ecoa.py`, fazendo com que o pipeline real terminasse em recusa. Após a correção, foram criados **9 testes em `tests/test_pipeline_logs.py`** e um `tests/conftest.py` para impedir que os testes gravassem informações no diretório real de logs.

- Foi realizada a validação integrada das alterações ao longo do dia. Ao final, a suíte ficou com **237 testes aprovados e 13 pulados** (os pulados aguardam código ainda não publicado em `src/`).

- Durante a validação das bases, foi identificado que a base agregada (`pca_agregado`) não está presente no manifesto principal e que o `retrieve.py` procura uma base conversacional (`atas_conversacional`) que também não está disponível atualmente. Foram realizadas tentativas de consulta aos endpoints de PCA e de atas do PNCP, porém as APIs apresentaram instabilidade, com respostas HTTP 500 e 504. As tentativas foram registradas manualmente no `CORPUS_INVENTORY.md`, incluindo horário e resultado. Posteriormente, foi verificado que o serviço `/api/pncp/v1` do PNCP respondia normalmente, e as duas bases passaram a ser coletadas por ele.

- Também foram comparados os dois arquivos `CORPUS_INVENTORY.md` existentes no projeto, um na raiz e outro em `data/`. Foi identificado que os arquivos possuem conteúdos diferentes e que o inventário da raiz corresponde ao `data/inventory/v1.json`. Ao final, o inventário da raiz foi definido como oficial e o de `data/`, uma coleta antiga sem vínculo com o manifesto, foi removido.

- Foi atualizada a seção de divisão de responsabilidades e coordenação do `ARQUITETURA.md`, registrando que a distribuição das tarefas ocorre a cada encontro de acordo com as necessidades e revisões do projeto, além do registro das pendências atualmente identificadas.

- Foi implementada uma CLI simples para execução e validação do projeto pelo terminal, disponibilizando os comandos `rota` e `perguntar`. O comando `rota` permite validar quais bases devem ser consultadas para uma determinada pergunta, enquanto `perguntar` executa o pipeline completo. A CLI também permite executar múltiplas perguntas em uma mesma execução e foi validada diretamente no terminal.

- Também foi adicionada a suíte offline de validação do corpus, incluindo fixtures em JSON, um manifesto de smoke test e testes para `fetch_corpus`, ingestão, providers, curadoria do manifesto e preparação dos documentos. Foi criado ainda o runner `tests/run_corpus_validation.py` e realizada a documentação dos relatórios de validação.

- Foi concluída a implementação dos testes do `fetch_corpus.py`. Foi criado o `tests/test_fetch_corpus.py` do zero, considerando a versão reescrita do módulo, com **20 testes aprovados** cobrindo o parsing do manifesto, deduplicação por conteúdo utilizando SHA-256, imutabilidade de versões promovidas, associação de checkpoints e resumos ao hash do manifesto e os coletores `arquivo_local`, `http_pdf` e `rest_api`.

- Foram feitos os merges das branches da Pâmela, do João, do Marco e do Diogo no `main`. Nos conflitos de `tests/test_ingest.py` e `tests/test_fetch_corpus.py`, criados em mais de uma branch, os testes dos dois lados foram mantidos. Um teste do João revelou um laço infinito no `chunk_text` do `src/ingest.py`, que foi corrigido. Os testes que dependem de código ainda não publicado foram marcados com `skip` e o motivo.

- Foi feita a revisão do `src/retrieve.py` com os dados reais: a busca associava cada natureza a uma única base fixa, e só 19 de 516 chunks eram alcançáveis. A seleção passou a usar a natureza de cada chunk, e foi criado o filtro por `escopo`, para que o pipeline consulte só as bases oficiais. O BM25 passou a ignorar acentos, e foi criado o parâmetro `fetch_k`, para que o chunk certo chegue à fusão RRF.

- O `corpus_manifest.yaml` passou a declarar as quatro bases oficiais, uma por natureza, com o campo `escopo`. As bases `atas_conversacional` e `pca_agregado` foram declaradas com endereços fixos do PNCP, e a coleta de verificação baixou os arquivos sem falhas. O `src/artifact_downloader.py` foi corrigido para reconhecer arquivos CSV.

- Foi implementada a base agregada. Em vez de um chunk por item do PCA (seriam 42.758), o novo `src/pca_indicators.py` calcula na ingestão cerca de 1.000 indicadores (visão geral, totais por categoria, unidade, classe e mês, rankings e resumos), cada um com os valores já calculados. O roteador passou a reconhecer perguntas sobre o PCA, e foi corrigido um bug no padrão de "maior", sem mudança no resultado do golden set (15 de 19).

- Foi realizado o primeiro teste do pipeline completo com dados e LLM reais, numa área de teste: 5 de 5 perguntas sobre o PCA trouxeram o indicador certo em primeiro lugar na busca híbrida, e as respostas geradas trouxeram os valores corretos citando as fontes (ex.: valor total do PCA 2025 do Estado do RJ, R$ 61.119.549.484,04).

## **Ficou pendente:**

- Avaliação posterior dos dois comportamentos identificados durante os testes do `src/retrieve.py`, relacionados a embeddings com dimensões incompatíveis e comparação lexical em determinados valores monetários inválidos.

- Continuidade da integração e validação dos módulos do pipeline, incluindo os testes e mecanismos de avaliação do sistema.

- Foi iniciada a implementação da parte de **front-end** do projeto, que terá continuidade nos próximos encontros.

- A **base de e-mails sintética** ainda não foi disponibilizada e será enviada pelo tutor. A integração e validação dessa base ficarão para a próxima aula.

- Rodar a coleta oficial e gerar o índice das bases `pca_agregado` e `atas_conversacional`, já declaradas no manifesto e verificadas numa área de teste.

- Converter os embeddings publicados em `data/embeddings/` para o formato do índice lido pelo `retrieve.py`, ou reindexar.

- Publicar o código de `src/` de que dependem os 13 testes pulados, unificar as pastas `testes/` e `tests/` e fazer o `fetch_corpus` gravar o inventário na raiz.

## **Bloqueios em aberto:**

- A API de consulta do PNCP (`/api/consulta/v1`) apresentou instabilidade nas consultas de PCA e de atas, com retornos HTTP 500 e 504. O bloqueio foi contornado com o serviço `/api/pncp/v1`, que respondia normalmente. As tentativas estão registradas no `CORPUS_INVENTORY.md`.

## **Uso de assistentes de IA:**

- Foram utilizados assistentes de IA como apoio à análise do código, desenvolvimento e revisão das suítes de testes, identificação de cenários de validação, esclarecimento de dúvidas técnicas, investigação de problemas de integração e apoio na estruturação das implementações. As alterações e resultados foram analisados e validados por meio dos testes automatizados e da execução do projeto.
