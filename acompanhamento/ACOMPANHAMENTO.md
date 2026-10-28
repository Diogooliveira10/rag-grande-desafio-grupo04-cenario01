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

## Encontro 11 - 2026-10-07

## **Entregamos hoje:**

- Integramos ao `main` o contexto entre perguntas, as melhorias de qualidade do corpus e a correção de extração de PDFs com texto ilegível. Antes dos merges, a suíte foi executada com as contribuições combinadas. No conflito do SQLite, a base foi reconstruída usando o código e os dados integrados, em vez de escolher uma das versões.
- Concluímos o backend do item 11: o cliente pode enviar as últimas trocas no campo `historico`, o backend reescreve perguntas de continuação antes da análise e devolve o texto em `pergunta_reformulada`. O contrato foi atualizado em `docs/FRONTEND_API_CONTRACT.md`.
- Implementamos `src/qualidade_corpus.py` entre a extração e a indexação. A etapa atualiza status e metadados sem alterar o texto; o índice foi refeito reaproveitando os vetores existentes, sem gerar vetores novos. Nas atas, 819 chunks com glifos ilegíveis foram marcados como `texto_ilegivel` e removidos da busca, reduzindo os chunks indexados de 2.524 para 1.705; tabelas de lances foram preservadas.
- Corrigimos metadados de vigência de 1.498 dos 1.561 chunks normativos. A leitura incorreta das URLs entre aspas no manifesto impedia a coleta dos metadados; com a correção, a busca de normas vigentes em 2025 pode usar as datas de vigência e o prompt informa “vigente desde”. Também foram associados dados de modalidade a 3 dos 448 contratos com compra correspondente no corpus, e 44 valores inferiores a R$ 1,00 foram marcados como simbólicos.
- Corrigimos no evaluator a busca lexical de termos simples e explicitamos quando a validação de conteúdo é não aplicável ou não validável. Acrescentamos testes para os falsos matches de `TI`, `CRISTALIA`, identificadores PNCP e casos sem critério de conteúdo.
- Os testes focados do evaluator passaram (**34 testes**) e a suíte completa passou com **1.189 testes**; `git diff --check` também passou. Na rodada oficial do Golden Set `20261007T193322Z`, houve **12/26 acertos**. `P5.3` permaneceu falho porque a resposta não continha os quatro termos obrigatórios; nenhum resultado individual mudou frente ao matcher anterior. `P3.1` teve `expected_source_ok=true`.
- A alteração incidental da base SQLite durante a avaliação oficial foi restaurada; os hashes do Golden Set, corpus, índices, ZIP e requirements permaneceram iguais.

## **Ficou pendente:**

- Concluir o item 13 sobre autoria da base sintética: corrigir as duas linhas de `corpus_manifest.yaml`, atualizar `CORPUS_INVENTORY.md` e `docs/emails_sinteticos.md`.
- Finalizar a integração do front-end para enviar `historico` e apresentar `pergunta_reformulada`, e validar o fluxo completo com o backend.
- Validar a chamada ao endpoint `GET /api/v1/history`. Na tentativa local, não foi possível extrair `RAG_API_KEY` do arquivo `.env`, então a requisição não chegou ao endpoint.
- Avaliar separadamente as falhas restantes do Golden Set, sem ajustar perguntas ou respostas esperadas para aumentar o score.

## **Bloqueios em aberto:**

- A integração do contexto conversacional no front-end depende do envio do campo `historico` e da apresentação de `pergunta_reformulada`.
- A validação local do histórico da API depende de disponibilizar a chave `RAG_API_KEY` de forma acessível ao comando de teste.

### Uso de assistentes de IA:

- Utilizei assistentes de IA como apoio na inspeção do evaluator e dos testes, implementação da correção, execução dos testes e análise do relatório oficial.

## Encontro 12 — 2026-10-09

## **Entregamos hoje:**

- Corrigimos a rastreabilidade do pipeline após o merge das alterações da Eduarda. Subimos backend e frontend em conjunto e validamos uma conversa real com três perguntas, confirmando o funcionamento das duas telas de trace, do `session_id` e dos mecanismos de proteção. Também corrigimos erros HTTP 500 em 70 dos 169 logs consultados, causados por valores de latência com casas decimais gravados em 21 e 23/09. Para registros anteriores a 05/10, ajustamos o trace para recuperar as evidências SQL e vetoriais, a confiança e a recusa a partir das etapas já registradas nos logs.

- Corrigimos a extração de órgãos em `src/query_analysis.py` para compatibilizar expressões como “Prefeitura de Maricá” com a nomenclatura oficial do PNCP, utilizando o catálogo de órgãos do corpus. A normalização também contempla variações como “Prefeitura Municipal de X”, “Prefeitura da Cidade do X”, “Município de X” e “Prefeitura do Rio”. A consulta sobre Maricá passou a retornar apenas contratos do `MUNICIPIO DE MARICA`, sem ampliar a busca para outros órgãos, que antes resultava em 625 trechos. O resultado SQL permaneceu inalterado.

- Corrigimos bugs na interpretação de consultas SQL, incluindo a identificação indevida de palavras como “somando”, “celebrado” e “principais” como termos de objeto contratado. Ajustamos o stemmer para reconhecer gerúndios, adicionamos stop words, melhoramos a extração de órgãos pelo catálogo do corpus e corrigimos a detecção de métricas para reconhecer “valor” como indicador de soma. Com isso, a pergunta “Qual o valor de todos os contratos de 2025?” passou a retornar o total determinístico de **R$ 2.546.786.958,21**, em vez de aproximadamente R$ 673 milhões calculados pelo LLM a partir de apenas três contratos recuperados pela busca vetorial.

- Preparamos o frontend para novos clones do repositório, adicionando os scripts de diagnóstico `npm run check:backend` e `npm run check:query` e documentação de instalação. Também implementamos o envio de `session_id` e histórico nas consultas de continuação, permitindo reformular perguntas como “E em 2024?” com base no contexto anterior. Corrigimos ainda o botão “Consultar no NEXO”, na página de Contratações, para utilizar o identificador de controle PNCP, em vez de `numero_contrato`, que não existe no banco de dados.

- Finalizamos o item 13, referente à proveniência da base sintética. Corrigimos as informações de fonte e licença em `corpus_manifest.yaml`, `CORPUS_INVENTORY.md` e `docs/emails_sinteticos.md`, registrando que a base foi gerada pelo tutor e integrada pelo João em 05/10, com referência aos commits. Corrigimos também as informações divergentes no `README.md`, na tabela de bases, no `ARQUITETURA.md` e no docstring de `src/synthetic_conversations.py`.

- Validamos as alterações de proveniência com a suíte completa do backend, que passou com **1.261 testes aprovados**. As correções de rastreabilidade, consultas SQL e frontend também tiveram validações específicas: foram registrados **590 testes de backend e 36 de frontend aprovados**. As alterações correspondentes foram commitadas nas branches `origin/joao-reis` (backend) e `sevenRevy/frontendRAG/main` (frontend). A correção de proveniência foi enviada na branch `marco/proveniencia-base-sintetica`, no commit `1a05b92`.

- Iniciamos a investigação de um problema na geração de respostas SQL sem resultados. Inspecionamos `tests/test_numerical_validation.py`, `tests/test_pipeline.py` e `tests/test_structured_query.py`, identificando os testes e auxiliares relevantes. Confirmamos que `src/structured_query.py` representa consultas vazias com o marcador textual `"(nenhum registro)"`, o que exige distinguir esse marcador de resultados reais antes de definir a resposta e o nível de confiança.

## **Ficou pendente:**

- Concluir a correção em `src/generate.py` para tratar explicitamente consultas SQL sem resultados, informando que nenhum registro foi encontrado na amostra analisada de 809 registros, sem afirmar que não existem contratações em todo o estado do Rio de Janeiro.

- Garantir que respostas sem resultados tenham confiança limitada a `MEDIA`, sem alterar o comportamento determinístico atual quando houver registros.

- Criar testes de regressão para a resposta vazia e para o limite de confiança. Depois, executar os testes focados, a suíte completa e `git diff --check`, registrando os resultados efetivamente obtidos.

- Verificar se as alterações de backend e frontend estão integradas à branch de referência do projeto, distinguindo os commits enviados às branches de trabalho daqueles efetivamente incorporados ao `main`.

## **Bloqueios em aberto:**

- A correção do tratamento de consultas sem resultados depende da inspeção do fluxo de `_nivel_de_confianca` e `_resposta_sql_deterministica` em `src/generate.py`. A implementação e os testes de regressão ainda não foram concluídos.

## **### Uso de assistentes de IA:**

- Utilizei assistentes de IA como apoio à investigação de bugs, análise do pipeline SQL, normalização de órgãos, correção da rastreabilidade, preparação do frontend, documentação da proveniência da base sintética e inspeção dos testes.

- As sugestões foram utilizadas como apoio à implementação e à validação. Os resultados foram conferidos por meio de conversas reais, consultas ao corpus, traces, testes automatizados e registros de commits. A tarefa de tratamento de consultas SQL sem resultados permanece em andamento.

## Encontro 13 — 2026-10-14

## **Entregamos hoje:**

- Integramos à `main` as contribuições de Diogo, João, Marco e Pâmela, por meio dos PRs #36 a #41. Resolvi conflitos de merge, conferi as alterações contra as branches originais e preservei as correções de normalização de órgãos, os filtros de município e fornecedor e as opções de controle de acesso ao histórico. Durante a integração, ajustei o teste de query analysis que ainda esperava a nomenclatura antiga do órgão.

- Corrigi dois problemas na regra de confiança para consultas SQL vazias: a referência à amostra foi atualizada de 809 para **810 registros**, conforme o schema 7, e a verificação passou a considerar todas as partes da consulta. Assim, uma consulta com uma parte vazia e outra com resultados não recebe indevidamente confiança reduzida nem informa que não foram encontrados registros quando há resultados válidos. Na integração, prevaleceu a regra de confiança baseada no recorte da consulta, com a ressalva de que a ausência de resultados se refere à amostra analisada.

- Finalizei e validei o controle de acesso ao histórico de conversas, separando as permissões de administradores e usuários. Mantive os dois personas e a opção `HISTORY_PUBLIC`, com testes em conversas reais.

- Rodei novamente o benchmark do Demo Day, com **23/26 acertos**. Dois casos apresentaram oscilações associadas à instabilidade na recuperação de documentos (_top-k_), indicando que ainda é necessário investigar a reprodutibilidade desses resultados.

- Avancei na qualidade dos dados e na documentação técnica. Comparei o projeto com o ETL do DUOPEN, incorporei a validação dos dígitos verificadores dos CNPJs mencionados nas perguntas e confirmei que os 810 CNPJs de órgãos e os 432 de fornecedores da base passam nessa validação. Criei `docs/QUALIDADE_DOS_DADOS.md`, documentando a cobertura dos campos, as limitações identificadas e os riscos. A modalidade está preenchida em 3 dos 455 contratos, e o fornecedor, em 1 das 355 compras. Decidi não derivar a modalidade da compra, pois apenas seis contratos seriam beneficiados. Também incorporei a compressão zlib somente às respostas armazenadas no histórico, reduzindo seu tamanho em 46%, sem comprometer as buscas textuais no banco.

- Revisei `ARQUITETURA.md` em relação ao código, conferindo a ordem das etapas do pipeline, os filtros, o recorte de confiança, as 11 rotas da API, o estado do frontend, os números do corpus e do Golden Set e as pendências. Atualizei o `README.md` e removi do repositório o roteiro interno do Demo Day. Os commits `0ebae73` e `86c10f7` registram essas alterações, embora suas mensagens tenham ficado associadas às mudanças na ordem inversa.

- Consolidei as pendências até o Demo Day em um documento de 14/10, nos formatos Word e PDF, reunindo as pendências anteriores e as novas, com indicação do que já foi concluído. Distribuí as tarefas entre João (corpus v7), Diogo (nova rodada do Golden Set), Marco (F1), Pâmela (Golden Set dos e-mails sintéticos) e Eduarda (frontend oficial e publicação). Também preparei e compartilhei o roteiro de estudo para o Demo Day.

- Medi a latência das consultas previstas no roteiro. No ECOA, respostas produzidas por SQL ou regras levaram de 0,1 a 7,7 segundos, enquanto as que dependiam do LLM levaram de 44 a 400 segundos. A pergunta de continuação sobre fornecedores chegou a 330 segundos, acima do limite de 240 segundos do site. Para a reserva, configurei um modelo gratuito específico no OpenRouter com saída JSON, evitando a seleção aleatória de modelos do `openrouter/free`. Mantive o ECOA como provedor principal, com timeout de 60 segundos e OpenRouter como alternativa; as consultas lentas passaram a levar de 69 a 101 segundos.

- Executei três rodadas das consultas do roteiro no ECOA. As respostas baseadas em SQL ou regras permaneceram estáveis em texto, fontes e confiança, todas abaixo de cinco segundos. As respostas sobre a Lei 14.133 e a Infolog variaram entre execuções. O roteiro ao vivo passou a priorizar as cinco consultas estáveis, deixando as duas instáveis para demonstração com capturas prévias. Também confirmei que a Infolog não consta no corpus e que o resultado vazio da consulta é real.

- Corrigi o tratamento da pergunta “Qual foi o valor pago à empresa X?”. Ela era recusada por não corresponder ao roteador e passou a utilizar o SQL, informando que o PNCP não registra pagamentos. O tempo caiu de aproximadamente 68 segundos para 3,5 a 4,3 segundos. Também ajustei o carregamento do rerank na inicialização da API: o atraso inicial era causado pelo carregamento do modelo, e não pelo rerank em si, que leva aproximadamente 0,9 segundo.

- Publiquei o backend para acesso pelo site utilizando um túnel ngrok com domínio fixo gratuito, após avaliar alternativas como Vercel, Render e Hugging Face. A Vercel foi descartada pelas limitações de armazenamento temporário, tamanho e tempo de execução, incompatíveis com o trace. Validei externamente o endpoint `/health`, o CORS a partir do site da Eduarda e uma consulta real, que retornou HTTP 200 em 4,8 segundos. Ajustei a liberação do header exigido pelo ngrok e documentei o procedimento de inicialização na seção 9.1 do `README.md`. O site publicado ainda precisa ser configurado com o endereço do backend; encaminhei à Eduarda as instruções para isso.

- Validei as entregas de Marco e Pâmela com o avaliador e o LLM real. O item 15, referente aos e-mails sintéticos, foi concluído: E1, E2 e E3 passaram, e a suíte contém um teste que garante que as cinco conversas restritas nunca sejam incluídas como fontes ou enviadas ao prompt. No item 8, a correção de Marco para o termo “houver” foi integrada, mas a F1 ainda precisava da regra geral de insuficiência de dados para comparações entre grupos.

- Concluí a investigação e a correção da F1 na branch `marco/f1-termo-houver`. Após remover “houver” e suas flexões da extração de termos de objeto, identifiquei que a resposta ainda era produzida pelo atalho SQL com confiança alta, mesmo quando apenas uma esfera possuía registros com valores válidos para comparação. Implementei uma regra para que comparações entre esferas com menos de duas esferas contendo registros de valor igual ou superior a R$ 1,00 sejam classificadas como insuficientes, com confiança baixa. A regra foi implementada em `structured_query.py`, `schema.py` e `generate.py`, com testes para dados insuficientes, suficientes e perguntas que não envolvem comparação.

- Validei a correção da F1 em duas execuções: a primeira, `20261014T181928Z`, passou na branch de trabalho; após atualizar a branch com a `main`, a segunda, `20261014T204357Z`, também passou. A suíte passou com 1.607 testes na primeira validação e 1.662 na segunda. O commit `446bcb7`, referente à extração de “houver”, entrou na `main` pelo PR #41. A regra de comparação insuficiente, commit `64404cd`, foi enviada em um segundo PR para revisão do Renato, pois a alteração afeta a regra de confiança definida por João. Após o merge dessa regra, ficou prevista uma nova execução do Golden Set completo por Diogo.

- Finalizei o item 13, referente à proveniência da base sintética, iniciado no encontro anterior. Corrigi as informações de autoria e licença em `corpus_manifest.yaml`, `CORPUS_INVENTORY.md`, `docs/emails_sinteticos.md`, `src/synthetic_conversations.py`, `README.md` e `ARQUITETURA.md`. A documentação passou a registrar que a base foi gerada pelo tutor e integrada por João em 05/10/2026, com referência aos commits `d31925f` e `146ceea`. A suíte passou com 1.261 testes, e o commit `1a05b92` foi enviado na branch `marco/proveniencia-base-sintetica` para revisão do grupo.

- No corpus v7, o censo de mais de 168 mil registros foi concluído sem falhas. Como o ECOA estava indisponível, foi implementado um mecanismo de fallback para gerar embeddings localmente. O processamento continuava em execução, com aproximadamente 41% concluído no momento do relato. Também foi ampliada a coleta de atas de registro de preços para 522 órgãos/CNPJs do RJ, totalizando 11.689 atas únicas de 2025. Para recuperar modalidades de contratação diante das limitações da API do PNCP, foi desenvolvida uma estratégia de cruzamento entre contratos e publicações, permitindo classificar 28.664 registros. A taxonomia foi ajustada com base nos campos oficiais e validada por testes automatizados, todos aprovados.

- A suíte completa do projeto terminou com **1.653 testes aprovados** na validação geral registrada após as integrações. Esse resultado é distinto das execuções específicas da F1, que terminaram com 1.607 e 1.662 testes, e da validação anterior do item 13, com 1.261 testes.

## **Ficou pendente:**

- Executar o Golden Set completo após o merge da regra de comparação insuficiente, verificando o impacto da correção sobre os 29 casos e registrando o novo resultado oficial.

- Acompanhar a revisão e o merge do PR que contém o commit `64404cd`, garantindo que a regra geral de insuficiência de dados para comparações seja integrada à `main`.

- Concluir o processamento de embeddings locais do corpus v7 e validar o resultado final, pois o processamento estava em aproximadamente 41% no momento do acompanhamento.

- Configurar e republicar o frontend com o endereço correto do backend, conforme as instruções encaminhadas à Eduarda.

- Investigar as oscilações de dois casos do benchmark do Demo Day e a variação das respostas sobre a Lei 14.133 e a Infolog.

- Continuar o acompanhamento da latência das consultas que dependem do LLM, especialmente das perguntas de continuação, para garantir que o fluxo de demonstração respeite os limites de execução do site.

- Corrigir a F1 no sentido funcional e validar a nova regra de confiança em cenários gerais de comparação, além do caso específico que motivou a implementação.

## **Bloqueios em aberto:**

- A conclusão do corpus v7 depende do término do processamento local de embeddings e da validação do resultado final.

- A publicação definitiva do frontend depende da configuração do endereço do backend pela responsável pela interface.

- A consolidação da correção geral de confiança depende da revisão e do merge do segundo PR da F1, seguido da nova execução do Golden Set completo.

- A estabilidade de algumas consultas do Demo Day ainda depende da investigação das variações observadas na recuperação e nas respostas do LLM.

## **Uso de assistentes de IA:**

- Utilizei o Claude Code como apoio à resolução de conflitos de merge, comparação das contribuições entre branches, implementação da validação de CNPJ, compressão do histórico, correção do tratamento de pagamentos, carregamento antecipado do rerank, configuração do CORS para o ngrok, medição de latência e estabilidade com o LLM real, revisão da documentação, organização das pendências e preparação do roteiro de estudo.

- Também utilizei assistência de IA na investigação da F1, na implementação e nos testes da regra de insuficiência de dados, na preparação da documentação de qualidade dos dados e na revisão das alterações do corpus.

- As decisões de integração, as regras de confiança, a escolha do provedor principal e da alternativa, a retirada do roteiro interno do repositório público e a distribuição das tarefas foram revisadas e decididas por mim. Executei os commits, merges, pushes e testes correspondentes, conferindo os resultados antes de registrar as entregas.

## Encontro 14 — 2026-10-16

## Entregamos hoje:

- **Confiança das respostas (Diogo):** corrigida a combinação de confiança em `src/generate.py` para impedir que o recorte SQL eleve a confiança declarada pelo LLM. O ajuste mantém o caminho determinístico existente. Foram aprovados 113 testes em `test_generate.py` e `test_numerical_validation.py`, além de 9 testes relacionados a P4.2 e P4.3. A avaliação completa ainda não foi executada.

- **Golden Set e histórico (Pâmela):** adicionados os casos I3 e I4, relacionados a consultas por CNPJ, elevando o Golden Set de 29 para 31 casos. O avaliador passou a fornecer o histórico necessário às perguntas dependentes. P1.3, I3 e I4 passaram nos testes com LLM; P4.2 e P4.3 continuaram apresentando problemas de confiança. A suíte completa na `main` registrou 1.704 testes aprovados.

- **Corpus v7 e recuperação (João):** avançada a coleta e a reconciliação territorial, com 32.956 atas com evidência cadastral do RJ. O candidato v7 foi montado com 142.026 vetores, utilizando o modelo local Qwen3-Embedding-0.6B Q8_0. Na avaliação com 47 casos, a busca densa da v7 obteve hit@10 de 0,6170, contra 0,7872 da v6. A busca híbrida alcançou 0,7021, mas apresentou regressões. A v7 permaneceu em staging, sem substituir a base ativa.

- **Correções e desempenho do backend (Renato):** reduzido o tempo de leitura dos índices de aproximadamente 3,6 s para 0,35 s por consulta e o tempo do caminho SQL no Neon de 3,3 s para 2,1 s. Também foram corrigidos problemas na extração de termos SQL, na busca por variações de palavras e no guardrail de perguntas sobre legislação. A suíte registrou 1.704 testes aprovados.

- **Preparação para o Demo Day:** validado o acesso externo ao backend pelo túnel, incluindo `/health`, CORS e uma consulta real respondida em 4,3 s. Foi preparado um plano B com capturas das sete consultas do roteiro, incluindo respostas e traces, além de procedimentos de contingência para falhas de conexão, backend ou provedor de LLM.

- **Documentação e integração:** atualizados README, guia de execução local, índice de documentos, `.env.example` e `ARQUITETURA.md`. As alterações de coleta, indexação, avaliação e backend foram integradas conforme os respectivos históricos de branch e PR.

## Ficou pendente:

- Executar nova rodada do Golden Set após a correção de confiança e validar P4.2 e P4.3, garantindo que perguntas sobre modalidade acionem a consulta estruturada correta e não generalizem evidências de outros contratos.
- Investigar as regressões de recuperação da v7, auditar a consistência entre documentos, SQL, índices e vetores e reavaliar o Golden Set sobre o candidato.
- Validar o rollback com a aplicação funcionando e decidir explicitamente sobre a promoção da v7. A v6 continua ativa.
- Configurar o endereço do backend no deploy do frontend publicado.
- Investigar a instabilidade e a latência do LLM, especialmente nas consultas sobre a Lei 14.133.
- Concluir as validações finais antes do freeze do Demo Day.

## Bloqueios em aberto:

- **P4.2 e P4.3:** a consulta SQL não é acionada adequadamente; a recuperação traz documentos de outros contratos e a resposta generaliza a modalidade, podendo apresentar confiança alta indevida.
- **Corpus v7:** os resultados de recuperação ainda são inferiores aos da v6 em métricas importantes, sem evidência suficiente para autorizar a substituição da base ativa.
- **Publicação e demonstração:** o frontend publicado ainda precisa receber a configuração do backend, e consultas que dependem do LLM podem exceder o tempo limite.

### Uso de assistentes de IA:

- Foram utilizados assistentes de IA para apoiar a análise e implementação de correções, criação de testes, revisão de código e documentação, avaliação do corpus, comparação de estratégias de recuperação, investigação de desempenho e preparação do plano de contingência do Demo Day.
- As alterações e conclusões foram verificadas por meio do código, dos testes, dos logs e dos relatórios. As avaliações parciais foram mantidas distintas da suíte completa, e a v7 não foi promovida sem validação.

**Encontro 15 — 2026-10-19**

**Entregamos hoje:**

- **Correções no Golden Set:** Diogo corrigiu o caso P2.2 para recuperar o contrato de ambulância sem interpretar o valor global de R$ 0,00 como preço por veículo. A resposta agora reconhece a insuficiência de dados para calcular a mediana e limita a confiança a baixa. P2.1 permaneceu aprovado. A avaliação direcionada passou com **2/2 acertos**, e os testes relacionados totalizaram 705 aprovações.

- **Confiança e modalidade dos contratos:** Pâmela implementou uma regra para impedir que o LLM deduza a modalidade de um contrato a partir do amparo legal ou de documentos de outros contratos. P3.2, P4.2, P4.3 e P6.3 passaram nos testes com LLM na v7. Renato complementou a regra de confiança para limitar a média respostas que reconhecem falta de dados, por meio do PR #54.

- **Migração e desempenho da v7:** a versão mais recente do corpus foi integrada à `main`, com os dados mantidos fora do Git e distribuídos pelo Google Drive. Renato otimizou a recuperação para carregar somente as bases necessárias, evitando a leitura de todos os vetores e o recálculo integral do BM25 a cada pergunta. A busca por contratos caiu de 46–82 segundos para aproximadamente 6 segundos por consulta, com consumo de cerca de 4,6 GB de memória na inicialização do backend.

- **Avaliação do Golden Set na v7:** a primeira rodada obteve 19/31 acertos, contra 24/31 na v6. Duas falhas causadas por um filtro incorreto de fornecedor foram corrigidas, elevando o resultado para **21/31**. Quatro casos — P2.1, P4.1, I4 e F1 — possuem gabaritos associados aos dados da v6 e precisam ser revistos para a v7.

- **Backend, rastreabilidade e demonstração:** Renato corrigiu a montagem do trace para eliminar a repetição de etapas e tornar mais claros os motivos de etapas ignoradas. O backend e o frontend local foram reativados e as cinco consultas do roteiro foram repetidas com respostas equivalentes às da demonstração anterior. Na v7, as consultas ao vivo ficaram entre 0,4 e 4,9 segundos.

- **Interface e transparência:** foram otimizados o carregamento da tela **Explorar Acervo**, de 15,17 para 0,87 segundo na primeira chamada medida, e a exibição dos dados da página **Transparência da IA**, que passou a identificar a v7-reparo a partir de metadados verificados por hash. Os testes de backend passaram (78 testes), e o frontend teve 46 testes Vitest e 8 testes Node aprovados, além de build concluído sem erros.

- **Recuperação e qualidade das respostas:** foram implementadas melhorias na consulta SQL, apresentação determinística de tabelas, recuperação de normas, identificação de fornecedores e tratamento de perguntas sobre a origem dos dados. Também foram atualizadas documentações, contratos de API, variáveis de ambiente e testes de regressão.

- **Documentação e preparação para a defesa:** Marco investigou P6.3, que passou na execução completa, sem alteração de código. A equipe avançou na preparação do plano de contingência do Demo Day e do guia de leitura do código para a apresentação ao tutor.

**Ficou pendente:**

- Executar nova avaliação do Golden Set na v7 após revisar os gabaritos que dependem dos dados da v6 e investigar as falhas restantes.
- Continuar a validação de P2.2 e dos casos de modalidade na versão ativa, separando problemas de recuperação, conteúdo e confiança.
- Avaliar as regressões de recuperação da v7 em comparação com a v6 e verificar se o desempenho e o consumo de memória são aceitáveis para a demonstração.
- Concluir a configuração do backend no frontend publicado; o site ainda depende da integração pendente do endereço do backend.
- Avaliar a necessidade de gerar `data/inventory/v7.json` para manter o inventário máquina-legível alinhado à documentação.
- Atualizar o guia de leitura do código e fechar os itens restantes antes do freeze e do ensaio final do Demo Day.

**Bloqueios em aberto:**

- **Golden Set na v7:** o resultado de 21/31 ainda é inferior ao da v6, e parte dos gabaritos precisa ser atualizada para refletir o novo corpus.
- **Desempenho e recursos:** embora a busca tenha sido acelerada, a inicialização ainda consome cerca de 4,6 GB de memória e leva aproximadamente um minuto.
- **Publicação:** o frontend publicado ainda não está integrado ao backend ativo, exigindo uso local para a demonstração.
- **Confiabilidade das respostas:** as correções de confiança e de modalidade avançaram, mas os casos restantes precisam ser reavaliados na versão definitiva do corpus.

**Uso de assistentes de IA:**

- A equipe utilizou assistentes de IA para investigar falhas, implementar correções e testes, comparar respostas com e sem alterações, otimizar recuperação e desempenho, revisar documentação e preparar os materiais de apresentação.
- As alterações foram verificadas por testes automatizados, execução do pipeline, logs e avaliações do Golden Set. Os resultados da v6 e da v7 foram mantidos separados, e as decisões sobre promoção do corpus, escopo das correções e preparação da demonstração foram tomadas pela equipe.

## Encontro 16 — 2026-10-21

## Entregamos hoje:

- **Preparação para o Demo Day:** a equipe revisou os slides e o guia de estudos da defesa, organizando a apresentação em cinco partes: problema e motivação, dados utilizados, arquitetura e funcionamento, apresentação do sistema e demonstração prática. A duração prevista é de 15 minutos, com limite máximo de 20 minutos. As responsabilidades de apresentação foram distribuídas, e Eduarda ficou definida como apresentadora oficial.

- **Preparação individual:** Diogo estudou a arquitetura do NEXO RJ, revisou o fluxo de perguntas e respostas, os logs de execução e o conteúdo dos slides sob sua responsabilidade. Também validou o funcionamento do sistema com o frontend e o backend utilizando a v7. A consulta sobre contratos de ambulância firmados em 2025 retornou 31 registros via SQL em 1,7 segundo, em concordância com o plano de contingência.

- **Validação do Golden Set:** Renato executou a avaliação na v7, obtendo **35 acertos em 36 casos** após atualizar o gabarito de P4.2 para a nova versão do corpus e corrigir seu encaminhamento para SQL. A correção de confiança do Diogo e os três defeitos identificados anteriormente também já estão integrados à `main`.

- **Correção de cálculos SQL:** foram identificadas 358 compras anuladas que inflavam os totais financeiros. O filtro padrão passou a excluir esses registros, mantendo a possibilidade de consultá-los explicitamente. Com isso, o total de contratações de 2025 caiu de aproximadamente R$ 540 bilhões para R$ 152,4 bilhões, desconsiderando as compras anuladas.

- **Melhorias de interface e conversação:** foram corrigidos problemas de inicialização do atendimento conversacional e adicionada a possibilidade de abrir conversas a partir dos cards do histórico e preservar a sessão anterior ao iniciar uma nova conversa.

- **Validação funcional:** foram testadas 12 perguntas abrangendo consultas SQL, rankings, cálculos, busca normativa, apresentação do sistema, compras anuladas e recusas por previsão ou dados sensíveis. Todas passaram nos testes relatados.

## Ficou pendente:

- Finalizar os ensaios da apresentação e a preparação da demonstração para o dia 23/10/2026.
- Concluir a integração do frontend publicado com o backend ativo.
- Avaliar o ajuste de roteamento para perguntas de comparação entre esferas estadual e municipal.
- Manter o plano de contingência preparado para eventuais falhas de conexão, backend ou provedor de LLM.

## Bloqueios em aberto:

- **Publicação do sistema:** o frontend publicado ainda depende da configuração do endereço do backend.
- **Limitações conhecidas:** a demonstração deve explicitar que os dados representam o recorte disponível do corpus e que determinadas informações, como pagamentos efetivamente realizados, não podem ser confirmadas apenas pelos registros de contratação.

### Uso de assistentes de IA:

- Foram utilizados assistentes de IA para revisar as alterações integradas à `main`, apoiar a investigação de falhas, implementar e validar melhorias no backend e frontend, executar avaliações e preparar a documentação da defesa.
- Diogo utilizou o Claude Code para revisar as alterações integradas e se preparar para explicar os resultados, a arquitetura e as limitações do sistema.
- Conforme o planejamento da equipe, o código permanece congelado até o Demo Day, priorizando a estabilidade da versão apresentada.

## Encontro 17 — 2026-10-26

## Entregamos hoje:

- **Período coberto:** este registro reúne o que foi feito depois da apresentação do Demo Day, de 23/10 até hoje. A versão apresentada à banca foi preservada; as mudanças entraram depois, uma a uma, com testes e com o Golden Set rodado a cada etapa.

- **Golden Set em 36/36:** o resultado passou de 34/36 (23/10, logo após a apresentação) para 35/36 (24/10) e chegou a **36/36** hoje. Os ajustes foram: a resposta que conclui pela ausência de dados passou a citar os trechos examinados e a nomear a norma e o dispositivo; um trecho mencionado no texto ("o registro E1", "em E3") passou a virar fonte numerada (32 de 32 menções corrigidas); as chamadas ao LLM em JSON passaram a usar temperatura 0, e duas rodadas seguidas deram 35 de 36 respostas idênticas; o avaliador passou a reconhecer a negação por oração (608 respostas reavaliadas, nenhuma certa virou errada); e uma resposta sem nenhuma fonte pede a citação uma vez antes de ser aceita.

- **P4.3 resolvida pela Pâmela:** a resposta que reconhece evidência insuficiente deixou de ser tratada como recusa (`src/generate.py`). Era a única falha que restava no Golden Set; com o ajuste dela, a rodada completa deu 36/36.

- **Variedade na busca (Marco):** nas atas, cada compra tem muitos trechos (as páginas do PDF) e uma só compra podia ocupar o topo da busca. Agora entram no máximo 2 trechos por compra à frente; os demais vão para o fim, sem serem descartados (`RETRIEVAL_MAX_PER_PURCHASE`, 0 desliga). Nas 9 perguntas de ata do conjunto de busca, as compras distintas no top-10 passaram de 4,67 para 6,44 em média, e o primeiro trecho relevante continuou em 1º em todas. O Golden Set completo seguiu em 36/36.

- **Reformulação de perguntas de continuação (Diogo):** a etapa que reescreve perguntas como "E em 2025?" com o histórico era a mais lenta do pipeline depois da geração (23,6 s de mediana nos logs). O Diogo separou as pistas de continuação em fortes e fracas ("contratos encontrados em 2025" deixa de chamar o LLM, porque a pergunta já traz o próprio ano), criou um registro de regras que resolve sem LLM a troca de ano, de lugar ("E em Niterói?") e de esfera ("E só os da esfera federal?"), e deu à reformulação um prazo próprio (`REFORMULACAO_TIMEOUT`), em vez do prazo de 180 s da geração. Na revisão, o Golden Set mostrou a P4.3 falhando de novo, e Renato ajustou o prazo: ele estourava, mas a etapa continuava esperando o ECOA responder (na P4.3, prazo de 15 s e etapa de 43 s), e agora corta o tempo de fato; o padrão passou de 15 s para 60 s, porque nos logs só 43% das reescritas cabiam em 15 s e o restante caía no plano B (anexar a pergunta anterior), enquanto em 60 s cabem 91%. Uma rodada seguinte mostrou outra causa: o qwen3 raciocina antes de escrever a pergunta reescrita e chegou ao teto de 2.048 tokens sem terminar; o teto foi dobrado. Na rodada final, o Golden Set voltou a 36/36, e o log confirmou o prazo: a reescrita da P4.3 foi cortada aos 60 s, e a resposta passou mesmo pelo plano B. A P4.3 segue sensível à variação de tempo do ECOA.

- **Catálogo de fornecedores e vetores da v7 (João):** os fornecedores passaram a ser reconhecidos pela raiz do CNPJ e por apelidos, de modo que matriz e filiais com razões sociais diferentes entram no mesmo filtro. Foram gerados os 813 vetores que faltavam (749 contratos e 64 atas), e o índice ficou com 144.112 vetores, um por documento. Os dados da v7 passaram a ser distribuídos num pacote com conferência de hash (`runtime_bundle.json`), verificado pela rota `/ready`. Na integração, os testes apontaram uma regressão: "Casa da Moeda" deixou de ser reconhecida como fornecedor, e o ajuste ficou com o João.

- **Cálculos SQL:** compras revogadas e suspensas também saíram dos totais (as anuladas já tinham saído em 21/10), salvo quando a pergunta as cita; "canceladas" vale anulada e revogada. O total de contratações de 2025 passou de R$ 152,4 bilhões para R$ 146,4 bilhões. A consulta por número de controle continua mostrando o registro em qualquer situação.

- **Segurança:** a API ganhou limite de requisições por cliente (10 perguntas e 120 leituras por minuto, com resposta 429 e `Retry-After`), e o log passou a ter uma cadeia de hashes com chave (HMAC-SHA256), conferida por `python -m src.log_chain verificar`, que acusa qualquer linha alterada ou removida. O detector de injeção passou a recusar, sem chamar o LLM, pedidos de role-play e de vazamento de contexto ("finja que é o administrador", "modo DAN", "repita o texto acima"), sem falso positivo nas 167 perguntas reais e de avaliação. A chave da API foi trocada e a chave da cadeia de hashes foi gerada; as chaves do OpenRouter e do ngrok ficam como estão até o fim do projeto e só serão revogadas se o ECOA colocar o sistema em produção.

- **Desempenho** (na rodada do Golden Set com todas as mudanças, a etapa BM25 caiu de 100,3 s para 63,1 s nas 36 perguntas):
  - Mensagem com rota reconhecida não passa mais pelo LLM de identificação de intenção. Nas 36 perguntas do Golden Set, essa etapa caiu de 62,8 s para 0 s; em 165 mensagens reais, as chamadas ao LLM caíram de 20 para 11, sem mudar nenhuma resposta fixa.
  - O filtro do objeto no SQL passou a usar um índice de texto (FTS5 de trigramas) no SQLite. Nas 284 consultas reais com esse filtro, o tempo caiu de 94 s para 24 s; as 88 perguntas SQL dos logs deram respostas idênticas às de antes. Na rodada do Golden Set, a etapa SQL caiu de 39,1 s para 16,6 s. Quem usa a base baixada do Drive precisa rodar uma vez `python -m src.structured_db fts` (~5 s).
  - Na busca, os filtros de objeto e de órgão eram recalculados para cada um dos 107 mil contratos a cada pergunta, e os registros eram copiados antes de filtrar. O resultado desses cálculos passou a ser guardado e as cópias foram eliminadas. Nas 47 perguntas do conjunto de busca, o tempo total caiu de 179 s para 101 s e a mediana por busca de 2,9 s para 0,8 s, com resultados idênticos aos de antes (mesmos trechos, mesma ordem e mesmas notas).

- **Organização do código em camadas (Clean Architecture):** o caminho da pergunta foi reorganizado em camadas, com a dependência apontando só para dentro: regras de negócio puras em `src/domain/` (política de confiança, situação da compra nas contas, conta do RRF, normalização de texto e leitura de valores e períodos), contratos do caso de uso em `src/ports.py` e o pipeline recebendo as implementações por injeção de dependências, o que permite trocar a busca, o LLM ou o banco num teste sem `monkeypatch`. `query_analysis.py` (2.193 linhas) e `structured_query.py` (1.979 linhas) foram separados por assunto e por etapa (`src/analysis/`, `src/sql_query/`, com um executor por tipo de consulta), e as funções centrais foram divididas nas etapas que o log já nomeia: no caminho da pergunta, nenhuma passa de 100 linhas (a maior tinha 288). O comportamento ficou idêntico, conferido pela suíte, pelo Golden Set e pela comparação, antes e depois, da análise e do SQL gerado nas perguntas de avaliação. Um teste (`tests/test_architecture.py`) falha se o domínio passar a depender da infraestrutura. A estrutura está descrita em `ARQUITETURA.md`.

- **Auditoria pós-apresentação e divisão das tarefas:** Renato auditou o projeto e montou o documento de pendências (`Pendencias_Pos_Demo_Grupo04.docx`, fora do repositório), com 7 pendências e um roteiro de conferência. A divisão ficou: João, com o catálogo de fornecedores da v7 e os 813 trechos sem vetor (entregue, com um ajuste pendente); Marco, com a variedade na busca (entregue); Pâmela, com a P4.3 (entregue); Eduarda, com o frontend (selo "iniciado" no trace e exibição do limite de requisições); e Diogo, com a redução do tempo da reformulação de perguntas (entregue).

- **Documentação e entrega:** o contrato da API para o frontend (`docs/FRONTEND_API_CONTRACT.md`) ganhou a seção do limite de requisições e a lista dos status das etapas do trace ("iniciado" é marco de início, não erro). O README e o guia de integração ganharam o comando do índice SQL. Os arquivos novos da reformulação e da v7 passaram a ter nome em inglês (`src/reformulation.py`, `src/complete_vectors_v7.py`), como os demais criados depois da apresentação; os nomes antigos continuam funcionando. Os slides foram atualizados com os números novos, e foi preparado o e-mail ao tutor sobre a entrega deles. A suíte de testes passou de 2.116 para 2.301 testes aprovados.

## Ficou pendente:

- João: corrigir a regressão do reconhecimento de fornecedor ("Quais contratos têm como fornecedor a CASA DA MOEDA?" deixou de virar filtro; dois testes falham) e gerar o pacote do Drive já com o índice do SQL e o hash atualizado.
- Eduarda: selo "iniciado" no trace e exibição da mensagem do limite de requisições (429) no frontend.
- Fazer o merge das entregas de hoje na `main` (sem squash, para o arquivo de dados do Marco chegar a todos), reiniciar o backend e rodar `python -m src.structured_db fts` em cada máquina.
- Entrega final do projeto em 30/10/2026.

## Bloqueios em aberto:

- **Provedor de LLM:** o ECOA oscilou durante o dia (erros de provedor em parte das rodadas); o tempo de geração varia de uma rodada para outra sem mudança no código.
- **Recursos da máquina:** o backend continua ocupando cerca de 4,6 GB de memória e leva cerca de 1 minuto para subir.

### Uso de assistentes de IA:

- A equipe usou assistentes de IA para auditar o projeto, medir o tempo de cada etapa nos logs, implementar as correções com testes, comparar resultados antes e depois de cada mudança, rodar o Golden Set e escrever a documentação e as mensagens do grupo.
- As mudanças só entraram depois de conferidas por testes automatizados, pelo Golden Set e, nas de desempenho, pela comparação dos resultados com os de antes. As decisões sobre escopo, divisão das tarefas e manutenção das chaves foram tomadas pela equipe.

## Encontro 18 — 2026-10-28

## Entregamos hoje:

- **Período coberto:** este registro reúne o que entrou na `main` depois do registro de 26/10, até hoje. A partir de 26/10 a equipe passou a trabalhar direto na `main`, e cada mudança entrou com a suíte de testes e, no caminho da pergunta, com o Golden Set.

- **Golden Set e testes:** o Golden Set seguiu em **36/36**, com 100% de citação, nas rodadas que fecharam cada etapa de 27/10. A suíte de testes passou de 2.301 para **2.400 testes aprovados**.

- **Reconhecimento de fornecedores (João e Renato):** a regressão pendente de 26/10 foi resolvida. O João restaurou o reconhecimento de fornecedores de nome composto e atualizou o pacote da v7. Renato corrigiu três casos:
  - "Casa da Moeda", escrito como nome próprio, volta a virar fornecedor;
  - "Defensoria Pública do Estado do Rio de Janeiro" deixou de ser lida como empresa;
  - o filtro de fornecedor passou a comparar pela raiz do CNPJ também na igualdade exata e, quando o vetor não traz CNPJ (48 mil contratos só têm o nome), pela razão social sem pontuação nem forma jurídica ("S.A" = "SA").

  Em 435 perguntas reais e de avaliação, só houve melhoras.

- **Pacote da v7 com o índice do SQL:** o pacote publicado no Drive (`V7-qwen-q8-promovida-2026-10-26-fts.zip`) já traz a base SQL com o índice do filtro do objeto, e o `runtime_bundle.json` foi atualizado com o hash dela. Quem baixa o pacote não precisa mais rodar `python -m src.structured_db fts`, e a rota `/ready` responde 200 com o pacote extraído.

- **Correção na busca densa:** desde 19/10, toda pergunta com filtro (órgão, ano, fornecedor) ficava sem resultados da busca densa. O índice FAISS não chegava a esse caminho, e a resposta saía só com a busca por palavras. Com a correção, nas 36 perguntas do Golden Set:
  - a etapa de rerank caiu de 51,9 s para 17,5 s;
  - a busca densa caiu de 21,0 s para 6,1 s.

  No mesmo ajuste, um aviso do log deixou de listar os 106 mil contratos a cada pergunta. Ele tinha gerado 461 MB de log em 26/10, com cerca de 6 s por pergunta.

- **Desempenho e memória da busca:**
  - A busca por palavras (BM25) ganhou um índice invertido, com o mesmo ranking. Nas 47 perguntas do conjunto de busca, os trechos, a ordem e as notas ficaram idênticos; o tempo total caiu de 81,1 s para 53,9 s, e a memória da BM25, de 1.376 MB para 185 MB.
  - A lista de registros de cada base deixou de ser recalculada a cada pergunta. Nas mesmas 47 buscas, o tempo caiu de 59,3 s para 40,4 s, e a mediana por busca, de 0,59 s para 0,22 s.
  - Os metadados do índice passaram a ser lidos do próprio documento, sem uma segunda cópia na memória. A pré-carga caiu de 5.143 MB para 4.218 MB.
  - No SQL, a contagem do recorte passou a ser reaproveitada, e a completude dos campos passou a sair numa consulta só. Nas 89 perguntas SQL dos logs, as respostas ficaram idênticas, e as consultas caíram de 1.095 para 1.040.
  - Na rodada do Golden Set, a primeira pergunta pagava o carregamento do modelo de rerank (16 a 36 s). A rodada agora faz a mesma pré-carga do backend antes dos casos.

- **SOLID e Clean Code no projeto inteiro:** a organização em camadas descrita em 26/10 foi estendida para todo o código de `src/`. **Nenhuma função de `src/` passa mais de 100 linhas.**
  - **Busca e geração:** o `retrieve.py` (1.964 linhas) foi separado em `src/retrieval/` (filtros, busca por palavras, ranking, registros, identificadores, normas e evidências). O `generate.py` (1.502 linhas) foi separado em `src/generation/` (salvaguardas, contexto, avisos, respostas, resposta pelo SQL, leitura da resposta do LLM, log e citações). Os dois arquivos continuam como entrada e reexportam todos os nomes. Um teste falha se alguma parte passar a importar a sua entrada.
  - **Funções longas fora do caminho da pergunta:** foram divididas 31 funções em quatro grupos:
    - scripts de montagem da v7: 8;
    - índice, embeddings e indicadores: 8;
    - provedor PNCP e preparação dos documentos: 6;
    - coleta do corpus (`fetch_corpus.py`): 9, entre elas duas com cerca de 570 linhas cada.
  - **Duplicações removidas:** as buscas de publicações e de contratos no PNCP repetiam o mesmo esquema de paginação retomável, que virou um objeto único. Na preparação dos documentos, o trecho que transforma unidades em chunks e documentos aparecia duas vezes e virou um só.
  - **Nomes de arquivo em inglês:** 15 arquivos de `src/` com nome em português foram renomeados (por exemplo, `coletar_direto` → `direct_collection` e `qualidade_corpus` → `corpus_quality`). Os nomes antigos continuam importáveis, e um teste confere isso para cada um.
  - **Como o comportamento foi conferido:** no caminho da pergunta, pelas 47 buscas e pelas 90 perguntas SQL dos logs, com resultados idênticos, e pelo Golden Set. Fora dele:
    - pelos arquivos gerados nos testes, idênticos byte a byte antes e depois;
    - onde os testes cobriam pouco, comparando a versão antiga com a nova em milhares de entradas sintéticas aleatórias, com resultado, chamadas externas e estado gravado idênticos;
    - nas funções maiores, injetando defeitos de propósito na versão nova para confirmar que a comparação os detecta.

    Funções que não tinham nenhum teste ganharam testes que descrevem o comportamento atual.

- **Indisponibilidade do backend em 27/10 e correção do `/ready`:** na noite de 27/10, o backend parou de responder, embora o processo continuasse ativo.
  - **Causa:** desde 26/10, cada chamada ao `/ready` relia cerca de 290 mil linhas do índice e recompunha o catálogo de fornecedores, o que levava cerca de 55 s de CPU. Chamadas simultâneas repetiam esse trabalho em paralelo, e nenhuma terminava.
  - **Correção:** o resultado válido da validação fica guardado enquanto nenhum arquivo do pacote muda de tamanho ou de data, o mesmo critério do cache de hashes que já existia. Além disso, só uma validação roda por vez: quem chama durante ela recebe na hora "validação em andamento" (503).
  - **Resultado:** a primeira validação, ao subir o backend, leva cerca de 2 minutos, e as seguintes, de 5 a 25 ms. A regra de validação não mudou. O backend foi religado, e as perguntas pelo túnel voltaram a responder em menos de 1 s.

- **Cache do índice do SQL:** um teste do índice do filtro do objeto falhava em cerca de 1 de cada 3 execuções. O cache que diz se a base tem o índice usava como chave só a data e o tamanho do arquivo, e apagar e recriar o índice na mesma fração de segundo pode deixar os dois iguais. A chave passou a incluir o contador de alterações do cabeçalho do SQLite. O teste passou em 15 de 15 execuções, e as 90 perguntas SQL dos logs deram respostas idênticas.

## Ficou pendente:

- Eduarda: selo "iniciado" no trace e exibição da mensagem do limite de requisições (429) no frontend.
- Congelar o código na noite de 29/10 e rodar o Golden Set final antes da entrega.
- Entrega final do projeto em 30/10/2026.
- **Próximos passos depois da entrega:**
  - **Vetores separados dos metadados no disco:** o backend subiria cerca de 20 s mais rápido. Exige um pacote novo da v7, o aceite do operador, a publicação no Drive e o download por todos.
  - **Módulos grandes fora do caminho da pergunta:** dividir em pacotes, como foi feito com a busca e a geração, o `fetch_corpus.py` (4.735 linhas), o `prepare_documents.py` (1.716 linhas) e o `providers/pncp.py` (1.262 linhas). No `fetch_corpus.py`, os caminhos de dados, que hoje são variáveis globais reescritas em tempo de execução, passariam a ficar num objeto de configuração.
  - **Funções médias:** revisar as 47 funções entre 71 e 100 linhas.

## Bloqueios em aberto:

- **Provedor de LLM:** o tempo de resposta do ECOA continua variando entre rodadas. A P4.3 segue sensível a isso, porque a reescrita da pergunta pode atingir o prazo de 60 s.
- **Recursos da máquina:** o backend chegou a um pico de cerca de 4,4 GB de memória e, depois de subir, leva cerca de 2 minutos para a primeira validação do `/ready`.

### Uso de assistentes de IA:

- A equipe usou assistentes de IA para dividir os módulos e as funções longas, escrever os testes que descrevem o comportamento atual, montar as comparações entre a versão antiga e a nova, investigar a indisponibilidade do backend e o teste intermitente, medir o tempo e a memória de cada etapa e redigir as mensagens de commit e este registro.
- Cada mudança só entrou depois de conferida pela suíte de testes e pelas comparações com a versão anterior e, no caminho da pergunta, pelo Golden Set. Os commits e as decisões de escopo foram tomados pela equipe, entre elas a de deixar para depois da entrega a divisão dos módulos grandes e a separação dos vetores no disco.
