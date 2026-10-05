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

## Encontro 6 - 2026-09-25

## **Entregamos hoje:**

- Foi realizada uma revisão final do módulo `src/retrieve.py` após as correções anteriores, com identificação e correção de comportamentos relacionados ao tratamento de valores numéricos e embeddings inválidos ou ausentes. O parser numérico passou a diferenciar corretamente valores decimais iniciados por zero, como `0.125` e `0.500`, de valores que utilizam ponto como separador de milhar, como `1.500` e `1.234.567`, mantendo também o suporte a formatos como `1500.50` e `1500,50`.

- Também foi ajustada a propagação de `run_id` e `query_id` na recuperação densa, permitindo que os avisos relacionados a embeddings incompatíveis sejam associados corretamente à execução e à consulta. Registros com `embedding=None` passaram a ser ignorados pela busca densa, sem geração de vetor ou cálculo de score, enquanto embeddings válidos continuam sendo processados normalmente. Para esses casos, também foi adicionado aviso estruturado na observabilidade, contendo os identificadores da execução, da consulta e do chunk.

- A suíte `tests/test_retrieve.py` foi ampliada para contemplar os novos casos de borda e regressão. A suíte específica do módulo foi finalizada com **50 testes aprovados**, enquanto a suíte completa do projeto chegou a **274 testes aprovados**. Também foi executado `git diff --check`, sem identificação de problemas, e as alterações dessa etapa ficaram restritas aos arquivos `src/retrieve.py` e `tests/test_retrieve.py`.

- Foi implementada a nova etapa `src/query_analysis.py`, responsável pela análise estruturada da consulta antes do roteamento. A extração foi desenvolvida de forma determinística, utilizando expressões regulares e heurísticas, sem dependência de LLM. A etapa identifica informações como **ano, valores monetários e órgão**, incluindo valores mínimos, máximos e faixas e formatos monetários em reais, como `R$ 500.000,00`, `1,5 milhão` e `200 mil`. Para identificação de órgãos, foram utilizadas palavras-gatilho como secretaria, prefeitura e tribunal.

- Foram adicionados os modelos `QueryFilters` e `QueryAnalysisResult` ao `src/schema.py`, estruturando os filtros extraídos da consulta e permitindo sua utilização pelas demais etapas do pipeline.

- A etapa de análise foi integrada ao `src/pipeline.py`. O fluxo de `responder()` passou a executar `analisar_query()` antes do roteamento e a repassar os filtros extraídos para o `retrieve()`, permitindo que informações presentes na pergunta sejam utilizadas efetivamente durante a recuperação.

- O módulo `src/retrieve.py` também foi ampliado para permitir múltiplas condições sobre a mesma chave durante a aplicação dos filtros. Isso possibilita representar faixas de valores e tratar o filtro de ano como um intervalo aplicado sobre `data_vigencia`, mantendo o comportamento dos filtros já existentes.

- Foram criados novos testes para a funcionalidade, incluindo `tests/test_query_analysis.py`, `tests/test_pipeline_query_analysis.py` e quatro novos testes de faixa em `tests/test_retrieve.py`, validando a extração dos filtros, sua integração ao pipeline e o comportamento da recuperação.

- Durante a implementação do `query_analysis`, foi necessário reconciliar a branch de desenvolvimento com alterações que estavam avançando paralelamente na `main`, incluindo a reestruturação do `retrieve.py`, utilização de escopos e manifesto, alterações relacionadas ao `fetch_k` e novos campos no `schema.py`. A integração foi realizada preservando as funcionalidades desenvolvidas pelos diferentes integrantes.

- A branch `marco/query-analysis` foi integrada à `main` atual, recebeu push e teve o respectivo Pull Request aberto. Na validação após a integração, a suíte completa apresentou **322 testes aprovados e 1 falha pré-existente**, relacionada ao teste `test_prepare_documents_extracts_pdf_text_and_required_metadata`. Essa falha já estava presente na `main` antes da integração e não possui relação com as alterações do `query_analysis`, sendo possivelmente relacionada à versão do Python utilizada no ambiente local.

- Foi realizada uma nova validação do inventário do corpus, com regeneração do `CORPUS_INVENTORY.md` e comparação com o manifesto. O inventário da versão 2 foi considerado coerente, enquanto foram identificadas diferenças no manifesto após a coleta, incluindo a declaração das atas como PDF quando inicialmente apenas os JSONs eram coletados. O `corpus_manifest.yaml` foi atualizado para refletir a versão e os formatos efetivamente utilizados.

- Durante a validação da base conversacional e das coletas reais do PNCP, foram identificados e tratados problemas relacionados ao ambiente e à coleta: suporte a caminhos longos no Windows, contagem incorreta de arquivos locais que não estavam em JSONL e filtragem das atas do PNCP. Foi identificado que o endpoint de atas ignorava o filtro por UF, fazendo com que registros de outros estados fossem retornados. A coleta passou a utilizar o CNPJ do órgão do Estado do Rio de Janeiro como filtro adicional, com atualização do `src/providers/pncp.py`, do manifesto e dos respectivos testes. Na nova coleta, foram obtidas **20 atas do RJ**.

- Foi implementada a coleta dos PDFs associados às atas do PNCP. O provedor passou a identificar os arquivos disponíveis em cada ata e realizar o download dos PDFs correspondentes. A coleta real resultou em **20 atas e 33 PDFs, sem falhas**.

- Como parte dos PDFs estava digitalizada sem camada de texto, foi implementado o `src/ocr.py` e integrado o OCR ao `src/prepare_documents.py` para processamento das páginas sem texto. Foram processadas **176 páginas escaneadas** com OCR em português. Também foi ajustada a preparação documental para que os chunks dos PDFs herdassem informações da ata, como vigência e título do documento. Com isso, as 20 atas passaram de **372 para 956 chunks**, com os chunks provenientes dos PDFs contendo essas informações no cabeçalho.

- Foi criado um índice de desenvolvimento a partir dos embeddings já disponíveis em `data/embeddings/`, permitindo reaproveitar vetores existentes sem chamar novamente o modelo de embeddings. Para recuperar os textos correspondentes, os PDFs das fixtures foram preparados novamente e os **446 chunks** foram conferidos por SHA-256. Também foram adicionadas opções ao `src/index.py`, ao fluxo de resposta e à CLI para permitir a utilização desses índices experimentais.

- Com o índice de desenvolvimento, foi executado um teste real de ponta a ponta do pipeline utilizando o LLM real. Durante essa validação, foi identificado que o modelo da turma retornava as informações de `sources` e `confidence` em formatos diferentes dos campos originalmente esperados pelo parser. O `src/generate.py` foi ajustado para aceitar essas variações, converter a confiança numérica para o nível correspondente e registrar a ocorrência nos logs. Após a correção, a mesma consulta passou a retornar **3 fontes e confiança alta**.

- Também foi analisado o desempenho da execução real, sendo observado que aproximadamente três minutos por pergunta estavam relacionados principalmente ao servidor compartilhado utilizado pela turma, e não ao processamento local do projeto.

- Foi concluída a coleta oficial das quatro bases. Durante o processo, foi necessário corrigir um problema de certificado SSL utilizando o bundle `certifi` do Python. A coleta foi concluída com **222 arquivos, 200 itens e zero falhas**.

- As bases foram preparadas e foi gerado um índice local contendo **3.130 chunks**, todos com vetores de dimensão 1024. Também foi realizado um teste real de recuperação, que retornou três resultados. A avaliação completa do golden set ainda não foi executada.

- Foi implementado cache incremental na preparação documental, permitindo reaproveitar resultados quando os documentos e suas configurações não foram alterados. O fluxo também foi ajustado para localizar e preparar a versão mais recente do corpus, em vez de depender de uma versão fixa. Foram atualizados os testes e a documentação para contemplar esse comportamento.

- Foi avançada a organização da coleta declarativa do corpus a partir do manifesto, com criação de uma interface comum para provedores e integração específica com o PNCP. A configuração passou a descrever parâmetros de consulta e paginação, enquanto a coleta passou a enumerar documentos e registrar os resultados no inventário. Também foram ampliados os testes relacionados à coleta e aos providers.

- Foram consolidados os testes do corpus na pasta `tests/`, com organização das fixtures e arquivos de validação e ajustes nos testes de manifesto, coleta, preparação, providers, indexação e recuperação. A documentação da CLI e do inventário do corpus v2 também foi atualizada.

- Foram integradas as alterações da branch `main` à branch de trabalho e realizadas as revisões necessárias no manifesto, testes de coleta e ingestão e documentação após o merge. Também foi adicionado um exemplo sintético de e-mail em Markdown para demonstração.

- A equipe deu continuidade ao desenvolvimento do **front-end** e ao trabalho de **query analysis**, avançando na integração das etapas necessárias para o fluxo completo do sistema.

## **Ficou pendente:**

- Continuidade do desenvolvimento do **front-end** e evolução das demais etapas do pipeline.

- Execução da avaliação completa do **golden set** para validação mais ampla do comportamento do sistema.

- Continuidade da validação e integração das quatro bases no fluxo completo do pipeline.

- A **base de e-mails sintética** continua pendente e será construída/disponibilizada em conjunto com o tutor. A integração e os testes dessa base serão realizados posteriormente.

- Permanece identificado um comportamento no `update --dry-run`: quando a pasta `data/raw/vN/` não existe na máquina, o comando não reconhece corretamente o arquivo `data/inventory/vN.json`. O ponto permanece para avaliação.

## **Bloqueios em aberto:**

- Sem impedimentos técnicos críticos identificados no encontro.

- A base de e-mails sintética permanece como uma **dependência externa do projeto**, aguardando construção/disponibilização junto ao tutor.

## **Uso de assistentes de IA:**

- Foram utilizados assistentes de IA como apoio à revisão técnica do projeto após os merges, análise de problemas encontrados durante as coletas e preparações, desenvolvimento e revisão de testes, investigação de comportamentos de recuperação e geração, implementação de correções, configuração do OCR e atualização da documentação.

- As sugestões e implementações foram revisadas antes das integrações, com validação por meio de testes automatizados, coletas reais, execução do pipeline, conferência dos dados produzidos e verificações de consistência do projeto. As decisões sobre comportamento esperado e critérios de validação foram tomadas com base nos requisitos do projeto e nos resultados observados durante os testes.

## Encontro 7 - 2026-09-28

## **Entregamos hoje:**

- Foi realizada a validação das pré-condições para execução do **Golden Set**, incluindo a análise do pacote `indice_rag.zip` disponibilizado pela equipe. A base foi verificada e apresentou **3.130 chunks**, distribuídos entre atas, contratos, editais e PCA, com consistência entre os documentos normalizados, índices vetoriais, IDs e checksum SHA-256.

- Durante a preparação para execução do Golden Set, foram identificados problemas no ambiente Python, incluindo dependências ausentes (`fastapi` e `certifi`) e utilização do interpretador Python global em vez do ambiente virtual `.venv`. Por esse motivo, a execução oficial dos **19 casos do Golden Set** ficou pendente até a correção do ambiente e organização dos arquivos nas estruturas esperadas pelo pipeline.

- Foi concluída uma primeira versão do **front-end** e realizadas atualizações na documentação de arquitetura do projeto.

- Também foram consolidadas as alterações relacionadas ao corpus e ao processo de ingestão da versão 3. Durante a integração, foi resolvido um conflito no `src/retrieve.py` para conciliar as alterações da `main` com a implementação existente.

- A versão 3 do corpus foi promovida com **1.200 contratos únicos**, seus respectivos detalhes e sem falhas no inventário. O inventário registrou **1.295 arquivos e 1.243 itens lógicos** nas quatro bases. A cobertura completa do universo de contratos do RJ ainda não foi confirmada, pois o censo permanece incompleto.

- Foram identificados e corrigidos problemas que faziam o pipeline terminar em recusa mesmo quando existiam informações relevantes para responder à pergunta. Um dos problemas estava relacionado aos filtros de recuperação, que podiam eliminar todos os chunks quando determinada base não possuía o campo utilizado pelo filtro. O comportamento foi ajustado para permitir a recuperação dos documentos e registrar no log quando um filtro é ignorado.

- Também foi corrigida a geração das respostas. O prompt utilizado pelo LLM ultrapassava o limite do servidor compartilhado da turma, fazendo com que a instrução para responder em JSON fosse cortada. O contexto foi compactado e limitado, a instrução de formato passou a ser reforçada no final do prompt e foram adicionados limites configuráveis para o tamanho do contexto e dos chunks.

- Foram realizados novos ajustes no `query_analysis` para evitar que os filtros escondessem informações relevantes. O sistema passou a tratar os filtros como opcionais quando determinado campo não existe no chunk, além de melhorar a identificação de valores, anos, períodos, contagens e valores em bilhões. Também foram corrigidos casos como períodos entre dois anos, números presentes em identificadores de leis e editais e expressões que poderiam ser confundidas com filtros monetários.

- Foram adicionados novos testes para validar essas correções. A suíte do projeto chegou a **406 testes aprovados**, incluindo os novos cenários relacionados ao `query_analysis` e ao comportamento dos filtros e da geração.

- Também foi realizado um ajuste adicional no `query_analysis` para diferenciar números com aparência de ano de valores monetários, evitando interpretar como ano números acompanhados de `R$`, `reais`, `mil`, `milhão` ou `bilhão`. Foram adicionados mais **5 casos de teste** para validar esse comportamento.

## **Ficou pendente:**

- Correção e preparação do ambiente `.venv`, incluindo a instalação das dependências necessárias e confirmação de que o projeto está utilizando o interpretador correto.

- Organização dos arquivos `documents.jsonl` e `index/` nas estruturas esperadas pelo pipeline e nova validação do checksum da base.

* Execução oficial dos **19 casos do Golden Set** utilizando o pipeline real.

- Continuidade do desenvolvimento do **front-end** e das demais etapas de integração do sistema.

- Continuidade da validação do corpus v3, considerando que o censo do universo de contratos do RJ ainda não está completo.

## **Bloqueios em aberto:**

- A execução do Golden Set permanece temporariamente bloqueada pela configuração do ambiente Python e pela preparação dos arquivos do índice na estrutura esperada pelo pipeline.

- A base de e-mails sintéticos continua pendente. A base da estruturada em SQL está sendo construída para poder rodar.

## **Uso de assistentes de IA:**

- Foram utilizados assistentes de IA como apoio à análise do código, investigação de problemas no pipeline, desenvolvimento e revisão de testes, ajustes no query_analysis, recuperação, geração de respostas e documentação.

- As alterações foram revisadas e validadas por meio da execução dos testes automatizados, análise dos resultados do pipeline e verificação dos comportamentos esperados antes da integração.

## Encontro 8 - 2026-09-30

## **Entregamos hoje:**

- Foi executado o Golden Set com o pipeline real. Após instalar no ambiente virtual a dependência `faiss-cpu`, que já constava em `requirements.txt`, a execução `20260930T192922Z` processou os **22 casos atuais** sem erros: **14/22 acertos**. No conjunto original de 19 casos, foram **11 acertos e 8 falhas**. A análise indicou divergências de roteamento e consultas SQL, limitações de cobertura e aspectos que os logs não permitem determinar com segurança.

- Foi registrado que o avaliador mede principalmente a correspondência entre as bases roteadas e a recusa esperada; ele não verifica se as respostas estão factualmente corretas nem se as evidências são suficientes. Também foi observada uma diferença entre a contagem de chunks esperada anteriormente e a base disponível: os artefatos verificados totalizam **5.621 chunks**, não 3.130.

- Foram concluídas melhorias na análise de consultas e nos filtros usados pela busca e pelas consultas SQL. As alterações tratam, entre outros casos, valores confundidos com anos, períodos, contagens e filtros monetários. A abordagem adotada evita aplicar um filtro quando a interpretação não é clara. A validação da branch da Pâmela incluiu mais de 130 testes de regressão, 3.840 combinações de valores e períodos sem erros e 1.656 acertos em 1.663 perguntas adversariais.

- Foram adicionadas à API funcionalidades de feedback e histórico: `POST /api/v1/feedback`, `GET /api/v1/history` e um `query_id` na resposta de `/api/v1/query` para relacionar cada feedback à consulta correspondente. O histórico é salvo em SQLite local; a validação reportada pelo Marco teve **556 testes aprovados**.

- Houve avanços na recuperação e na avaliação: foram realizados testes de compatibilidade de consultas e ampliadas as consultas estruturadas, incluindo SQL parametrizado para PostgreSQL e correção do agrupamento de valores por fornecedor. Uma avaliação de 22 perguntas mediu 59,09% no critério de rota/recusa, sem representar uma avaliação factual completa da resposta.

- Foi realizada uma ablação do reranking com 47 perguntas. O conjunto RRF com reranking obteve o trecho relevante entre os três primeiros em 83% dos casos, ante 64% sem reranking. Também foram revisados os artefatos da versão 6 do corpus; foram apontadas limitações na base agregada e problemas de legibilidade por OCR em parte dos chunks de atas.

- Foram integradas alterações de API, filtros, recuperação e corpus, além de ajustes na avaliação do Golden Set e na documentação. Também houve continuidade da implementação e dos testes do front-end.

## **Ficou pendente:**

- Investigar e tratar as oito falhas do conjunto original do Golden Set, priorizando as divergências de roteamento e consultas estruturadas identificadas. As respostas e a suficiência das evidências precisam de avaliação própria, pois não são verificadas pelo critério atual do avaliador.

- Revisar a cobertura e os campos disponíveis nas bases, especialmente modalidades, especificações técnicas e informações necessárias para comparações entre contratações.

- Continuar a validação da cobertura do corpus v6 e avaliar a qualidade do OCR nos documentos de atas.

- Prosseguir com a implementação e validação do front-end.

## **Bloqueios em aberto:**

- A base de **e-mails sintéticos** continua pendente, aguardando disponibilização ou construção junto ao tutor.

- A análise do Golden Set não determinou com segurança a causa de todas as falhas; os logs atuais não comprovam suficiência de evidência em todos os casos.

## **Uso de assistentes de IA:**

- Foram utilizados assistentes de IA como apoio ao diagnóstico e às correções de filtros e consultas, à elaboração de testes, à revisão das alterações, à análise do reranking e à avaliação do Golden Set.

- Os resultados foram conferidos com testes, relatórios, logs e artefatos locais. A execução diagnóstica do Golden Set não levou a alterações no código, nos testes ou nos dados.

## Encontro 9 - 2026-10-02

João Pedro Félix Reis e Eduarda não participaram deste encontro por questões médicas. Mesmo ausente, a Eduarda continuou a integração do front-end com os dados reais do backend, registrada no acompanhamento individual dela.

### Relato individual - Diogo Oliveira

- Validamos documentalmente os casos `P3.1`, `P3.2` e `P6.1` diretamente contra o corpus do projeto, sem alterar o Golden Set, a avaliação, o pipeline, o SQLite, o índice ou quaisquer dados do corpus.
- Revisei a redação atual dos casos em `eval/golden_set.jsonl` e confirmei o que o projeto espera para cada caso, incluindo `bases_esperadas`, `is_refusal_esperado` e os termos esperados.
- Inspecionei o corpus normalizado em `data/normalized/documents.jsonl` e os documentos normativos em `data/normalized/editais_normativo/documents.jsonl`, procurando evidência direta sobre pesquisa de preços, vigência, modalidade, pregão eletrônico e microempresa.
- Consultei a base estruturada em `data/structured/base_estruturada.sqlite` para verificar se havia amostras de contratações em 2025 no Rio de Janeiro e se a modalidade aparecia com evidência rastreável.
- Concluí que o corpus comprova o procedimento relacionado à pesquisa de preços, mas não comprova documentalmente a vigência em 2025 da regra; a classificação correta foi `PARCIALMENTE VALIDADO`.
- Mantive a conclusão de que `P3.2` possui amostra e normas relacionadas, mas não possui vínculo direto e explícito suficiente para uma relação unívoca entre contrato, modalidade e norma aplicável; a classificação foi `PARCIALMENTE VALIDADO`.
- Confirmei que `P6.1` não está sustentado pelo corpus de forma direta, porque microempresa e pregão aparecem separadamente, mas a regra de participação de ME/EPP em pregão eletrônico não está comprovada no corpus; a classificação foi `NÃO VALIDADO`.
- Registrei a evidência rastreável em conjunto com `document_id`, `chunk_id`, base, trecho mínimo e o que cada evidência comprova.
- Mantive o relatório estritamente dentro do que o corpus consegue sustentar, sem preencher lacunas com conhecimento jurídico externo.
- A atividade foi apenas leitura e análise documental; não alterei nenhum arquivo do projeto, nenhum dado nem a implementação.

### Relato individual - Renato Lemos

- Como responsável pelos merges, integrei ao main a separação do front-end, as correções do `query_analysis`, a nova versão da `ARQUITETURA.md` e os relatos do dia.
- Revisei o Golden Set contra a base SQL da v6, não apenas a rota e a recusa, e confirmei inconsistências reais: a P2.1 admitia que não havia ambulância tipo D, quando há registro; a P5.3 era um contrato de transferência de tecnologia de medicamento; a P4.2 respondia "pregão" mesmo sem modalidade correta; e a P4.3 apontava "não informado" como modalidade mais usada.
- Constatei que o Golden Set não tinha gabarito de conteúdo e que havia perguntas dependentes da pergunta anterior; também identifiquei que a base de atas quase não era testada.
- Distribuí as tarefas do ajuste do benchmark entre a equipe: Diogo ficou com o gabarito e revisão do Golden Set, Pâmela com o `query_analysis` e Marco com a `ARQUITETURA.md`.
- Revisei a arquitetura do projeto e passei uma lista de ajustes para o Marco. Também validei a correção do `query_analysis` após os ajustes da Pâmela, incluindo melhorias em filtros por órgão, esfera e modalidade.
- Corrigi o roteador e a consulta SQL, ajustando casos em que perguntas com termos como "comprou", "contratou" ou "compras" ficavam sem base, além de tratar corretamente valores simbólicos, contagens de contratações e a interseção com a categoria tecnologia.
- Atualizei o ambiente de dados para a versão v6 no Neon e verifiquei que o SQLite e o Neon produzem os mesmos resultados nas mesmas perguntas. O SQLite foi mantido para quem não tem acesso ao Neon, para testes e avaliações.
- Atualizei o README e o registro da execução v6 e finalizei a suíte com 895 testes aprovados.

### Relato individual - Marco Araújo

- Atualizei a `ARQUITETURA.md` para refletir o estado real do projeto: entrada de rerank e consulta SQL no pipeline, atualização do corpus e do índice para a v6, além de correção das pendências com números reais e detalhamento do estado do sistema.
- Mantive a documentação alinhada com a base atual e com a execução do projeto em produção local.

### Relato individual - Pâmela de Freitas

- Trabalhei na extração de órgão, esfera e modalidade no `query_analysis`, a partir do que o Renato pediu.
- Corrigi a forma de reconhecer nomes de órgão, removendo partículas como "no", "na" e "em" e normalizando maiúsculas/minúsculas para evitar falsos positivos.
- Aumentei a cobertura de palavras-gatilho e siglas de órgãos, como Fundo, Comando, Banco, DPE, FIOCRUZ, SES, SEEDUC e outras siglas do sistema.
- Ajustei a identificação de esfera federal e modalidades como pregão, dispensa, concorrência e inexigibilidade, sem transformar textos genéricos em filtros.
- Corrigi casos de falso positivo em expressões como "no município do Rio", "legislação federal" e termos como "dispensa médica" ou "não foram por dispensa".

## **Ficou pendente:**

- Definir se o Golden Set deve ser ajustado após esta validação factual, considerando a diferença entre o que o corpus sustenta e as expectativas atuais.
- Revisar a cobertura e os campos disponíveis nas bases, especialmente modalidades, especificações técnicas e informações necessárias para comparações entre contratações.
- Continuar a validação das evidências e a preparação da documentação final para a decisão de benchmark.

## **Bloqueios em aberto:**

- O corpus continua limitando a comprovação temporal e a regra direta em alguns casos, especialmente `P3.1` e `P6.1`, mesmo com os dados disponíveis.
- O benchmark ainda precisa ser revisado em pontos em que o corpus não sustenta a expectativa atual, mas a decisão final depende do fechamento do relatório factual.

## **Uso de assistentes de IA:**

- Utilizei assistentes de IA como apoio na organização da evidência, revisão dos trechos normativos, separação entre menção e regra efetiva e consolidação do relatório final da etapa de validação factual.
- As conclusões foram verificadas com o corpus, os documentos normativos e os dados estruturados do projeto. Não houve alteração de código, dados ou avaliação durante a atividade.

## Encontro 10 - 2026-10-05

### Relato individual - Diogo Oliveira

- Concluí a investigação dos casos `A1`, `A2`, `I1` e `I2` do Golden Set, relacionando relatórios e logs às etapas de roteamento, análise de consulta, recuperação, consulta estruturada e geração. `A1`, `A2` e `I1` apresentaram rotas e evidências compatíveis; em `I2`, a resposta trazia os dados da ata correta, mas não repetia literalmente o identificador PNCP exigido pelo Golden Set. Como não foi encontrada uma regra funcional que obrigasse essa repetição, mantive o critério de `I2` e não alterei o pipeline.
- Investiguei o falso negativo de `expected_source_ok` em `P3.1`: o Golden Set usa `normativa`, enquanto a base tem o `base_id` canônico `editais_normativo`. Acrescentei ao evaluator essa equivalência explícita, preservei a comparação por prefixo dos demais casos e adicionei testes de regressão.
- Os testes focados passaram (**20 testes**) e a suíte completa passou (**1.074 testes**). Na avaliação oficial `20261005T202613Z`, o resultado foi **13/26**. `P3.1` teve rota, conteúdo e recusa corretos, mas não retornou fontes (`n_fontes=0`), portanto `citacao_ok` e `expected_source_ok` permaneceram falsos. O teste isolado confirmou que o mapeamento funciona quando uma fonte com `base_id=editais_normativo` é citada.
- Mantive o Golden Set inalterado e não alterei router, retrieval, geração ou corpus. Restaurei a base SQLite após uma alteração incidental durante a avaliação.

### Relato individual - Renato Lemos

- Integrei ao `main` as contribuições que estavam fora dele: a API ampliada para o front-end, o evaluator atualizado pelo Diogo, as conversas sintéticas e melhorias de análise e atendimento conversacional do João, além das correções do Marco e da Pâmela.
- No conflito do arquivo de acompanhamento da Eduarda, mantive a versão mais recente, escrita por ela após o último commit da branch. Após as integrações, a suíte offline foi reportada com **1.081 testes aprovados**.
- O tutor apontou que não era possível acompanhar o caminho de uma pergunta dentro do sistema: quando ela consulta a base SQL e a base vetorial, e o que entra e sai de cada etapa. Conferi os logs e confirmei que cada etapa era registrada, mas com a pergunta original como entrada de todas e um resumo em texto como saída. Esse ponto virou o item 1, de prioridade máxima, de uma lista de 16 correções (P0, P1 e P2) que montei para a equipe a partir de uma auditoria geral do projeto, conferida contra o código.
- Implementei a parte de backend da rastreabilidade. Ao fim de cada pergunta, o log ganhou a etapa `resumo_consulta`, que diz se ela consultou a base SQL e a base vetorial, quantas evidências vieram de cada uma e quais fontes foram citadas. A consulta SQL passou a registrar as linhas devolvidas, e a geração, o prompt enviado ao LLM e a resposta, ambos mascarados. O endpoint `GET /api/v1/trace/{query_id}` devolve o caminho da pergunta etapa por etapa, indicando em cada uma se ela usou a base SQL ou a vetorial, para o front-end da Eduarda exibir.
- Resolvi também dois itens rápidos da lista: removi o arquivo vazio `$db` e criei o CI do backend no GitHub Actions, com um `requirements-lock.txt` de versões exatas e torch só de CPU. Resolvi ainda o conflito de merge entre o evaluator do Diogo e a correção de erro de provedor do Marco, mantendo as duas alterações. A suíte terminou com **1.104 testes aprovados**.

### Relato individual - Pâmela de Freitas

- Implementei ajustes de rastreabilidade no `query_analysis`: a decisão de conversa ou consulta passou a ser registrada em uma nova etapa de log chamada `interacao`, separada da análise da pergunta. O status `geracao: conversa` foi preservado para manter compatibilidade com o evaluator.
- Ajustei também o tratamento dos logs no modo IA para mascarar CPF, telefone e e-mail, não repetir a pergunta original e limitar textos registrados. Atualizei o README e a arquitetura e adicionei seis testes.

### Relato individual - João Pedro Félix Reis

- Consolidei melhorias no RAG e integrei as mudanças recentes da `main`, incluindo suporte ao OpenRouter com fallback, análise de consultas, filtros, tratamento de evidências e geração.
- Integrei 25 conversas sintéticas ao corpus com controle de sensibilidade, mantendo-as identificadas como conteúdo fictício. O índice passou a conter **5.646 chunks**: 5.621 da v6 e 25 conversas sintéticas.
- Avancei na API e no fluxo conversacional, com endpoints de catálogo, documentos, órgãos e fontes, além de tratamento de saudações, agradecimentos e pedidos de ajuda sem acionar o RAG desnecessariamente. Também revisei Git, hashes, índices, vetores e artefatos.
- Reportei **1.074 testes aprovados** e `git diff --check` sem erros. Permaneceram pendentes ajustes locais, nova avaliação completa das respostas, correções de qualidade/metadados do corpus e validação integrada do front-end com o backend.

### Relato individual - Marco Araújo

- Criei o motivo `ERRO_PROVEDOR` em `src/schema.py` e ajustei os dois caminhos de `src/generate.py` para retorná-lo com uma mensagem de tentativa posterior. A etapa de geração registra o motivo e o erro original.
- Mantive esse motivo sob controle exclusivo do código: ele não é oferecido ao LLM nem convertido em uma recusa comum, para que falhas de infraestrutura não sejam apresentadas como respostas de conteúdo.

### Relato individual - Eduarda Silva Santos

- Estruturei no front-end como apresentar a rastreabilidade de cada consulta, incluindo uso de SQL, busca vetorial e etapas relevantes do pipeline relacionadas ao `query_id`.
- Preparei a interface para consumir o trace real do backend quando estiver disponível, sem preencher essa visualização com dados simulados.

## **Ficou pendente:**

- Publicar ajustes locais, refazer a avaliação completa das respostas, corrigir alguns problemas de qualidade/metadados do corpus e validar frontend + backend juntos.
- Concluir a rastreabilidade pedida pelo tutor: `session_id` e a tela do front-end consumindo o `GET /api/v1/trace/{query_id}`.
- Proteger os endpoints de histórico e de trace antes de qualquer demonstração pública: hoje não exigem autenticação nem mascaram o histórico.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio para percorrer o fluxo do evaluator e do pipeline, correlacionar logs com a implementação, analisar o contrato funcional de `I2` e localizar o ponto de comparação de fontes do evaluator.

- Revisei as alterações, executei os testes e validei os resultados da avaliação oficial. As conclusões foram baseadas nos dados, código, logs e testes locais, mantendo o Golden Set inalterado.
