# Acompanhamento — João Pedro Félix Reis

**Grupo:** 4
**Data:** 14/09/2026

## Atividades realizadas — 14/09/2026

Durante o encontro, participei da discussão sobre a escolha do cenário do Grande Desafio. O grupo optou pelo **Desafio 1 — Contratações Públicas do Estado do Rio de Janeiro**.

Também realizei o setup inicial do projeto com o clone do repositório do grupo para o ambiente local.

Criei o grupo de WhatsApp da equipe para facilitar a comunicação e a organização das atividades ao longo do desafio.

Participei da discussão sobre o conteúdo inicial do `ARQUITETURA.md`, especialmente sobre a justificativa para a escolha do cenário. 

Também discutimos algumas experiências individuais dos integrantes do grupo, buscando identificar conhecimentos prévios e experiências que possam contribuir para a divisão futura das responsabilidades no projeto.

## Resultado do encontro

Ao final do encontro, o grupo havia definido o cenário de Contratações Públicas do RJ, iniciado a organização do repositório e avançado na discussão da arquitetura e da justificativa do projeto.

## Próximos passos

Os próximos passos são continuar a definição da arquitetura, dividir as responsabilidades entre os integrantes e avançar na construção dos arquivos iniciais do projeto, como `ARQUITETURA.md`, `PERSONAS.md` e `corpus_manifest.yaml`.

## Atualização — 16/09/2026

### Atividades realizadas

Nesta sessão, continuei a organização técnica do repositório e mantive as branches `main` e `joao-reis` sincronizadas com o remoto.

Organizei os artefatos coletados na pasta `data/`, separando arquivos brutos por tipo em `data/raw/v1/arquivos/` e relatórios de execução em `data/runs/`. Também garanti que os arquivos fossem identificados por hash SHA-256, evitando duplicações.

Implementei os scripts `src/fetch_corpus.py` e `src/ingest.py`. O coletor permite selecionar um tipo específico e uma quantidade exata de arquivos novos, salva cada arquivo imediatamente após o download e utiliza as fontes compatíveis com o tipo solicitado. A ingestão prepara os documentos para o corpus, gera chunks determinísticos e marca PDFs que ainda aguardam extração.

Também criei e estruturei `testes/test-fontes/artifact_downloader.py`, `testes/test-fontes/pncp_client.py`, `testes/test-fontes/testar_fontes.py`, `testes/test-fontes/testar_fontes_cenario01.py`, `testes/test-fontes/fontes_cenario01.md` e `data/CORPUS_INVENTORY.md`.

Utilizei o Codex como ferramenta de apoio para criar os esqueletos iniciais dos scripts e da estrutura de ingestão, gerar arquivos auxiliares e estruturar as métricas de disponibilidade, quantidade por tipo, arquivos novos e hashes únicos.

Validei as APIs e URLs das fontes, incluindo os endpoints do PNCP e as fontes normativas. O coletor foi ampliado para percorrer todos os arquivos retornados pelos endpoints de editais e atas, em vez de considerar somente o primeiro arquivo.

Ao final, foram coletados 20 PDFs novos, além de 2 arquivos HTML, 2 JSON e 2 JSONL. O diretório passou a conter 47 arquivos com hashes únicos. Os resultados das coletas foram salvos em relatórios versionados.

### Resultado da sessão

O fluxo de coleta ficou modularizado, seletivo por tipo e resiliente a falhas posteriores, preservando os arquivos que já foram baixados. Consultei o histórico de commits da sessão e registrei os principais commits realizados:

- `ebfe727` — `chore: organiza as saídas do corpus em data`
- `2335b94` — `refactor: modulariza os testes de fontes e o cliente PNCP`
- `672d35f` — `docs: documenta o inventário do corpus e as fontes iniciais`
- `2affbcb` — `chore: inclui dados coletados no repositório`
- `2563ea4` — `feat: amplia a coleta seletiva do corpus`
- `acfac9e` — `data: adiciona artefatos coletados por tipo`
- `e038a91` — `docs: atualiza o manifesto do corpus`
- `77e9668` — `docs: atualiza acompanhamento do João`

## Atualização — 18/09/2026

### Atividades realizadas

Reestruturei o `src/fetch_corpus.py` para realizar a coleta de arquivos HTML, PDF, JSON e JSONL, com seleção de uma quantidade exata de documentos novos e únicos. O fluxo passou a incluir deduplicação, tentativas configuráveis, timeout, checkpoints e retomada de execuções interrompidas.

Também implementei o armazenamento versionado e imutável dos resultados, com promoção da versão somente após a conclusão da coleta. Cada arquivo passou a ser registrado em um inventário com informações de origem, caminho, tamanho, status, data de coleta e hash SHA-256.

O coletor passou a oferecer os subcomandos `fetch`, `update` e `inventory`. Além da coleta, o inventário permite comparar versões do corpus e identificar arquivos adicionados, removidos, alterados ou mantidos iguais. Também foi incluída a opção de salvar relatórios de execução em JSON.

Criei o teste offline `testes/teste_extração.py` e o respectivo log de execução. A validação verificou o carregamento do manifesto, a disponibilidade dos subcomandos, o contrato do inventário e as regras de armazenamento dos dados brutos, sem realizar novos downloads durante o teste.

### Resultado da sessão

O fluxo de extração ficou mais controlado, reexecutável e rastreável, com suporte a versionamento e auditoria das alterações no corpus. A validação registrou um inventário com 47 arquivos distribuídos em 5 bases, sem falhas.

O principal commit realizado foi:

- `2a31f9a` — `feat: implementar extração de corpus orientada por manifesto`

Esse commit alterou o coletor, adicionou o teste offline e registrou o log de validação, totalizando 994 linhas adicionadas em três arquivos.

## Atualização — 21/09/2026

### Atividades realizadas

Nesta sessão, concentrei meu trabalho em uma rodada extensa de desenvolvimento,
testes, refatorações, documentação e preparação de dados na branch
`joao-reis`. Foram realizados 56 commits de minha autoria, envolvendo 87
arquivos únicos. A maior parte foi dedicada a testes e validações; os demais
commits trataram de funcionalidades, documentação, refatoração, configuração e
dados.

Na infraestrutura, organizei as dependências e configurações necessárias para
interfaces de linha de comando, processamento documental e geração de
embeddings. Também consolidei configurações de provedores, modelos, endpoints,
timeouts e demais parâmetros utilizados pelo pipeline.

Centralizei comportamentos de rede que estavam distribuídos na aplicação.
Implementei uma política comum de retry, tratamento de falhas e repetição de
requisições, além de componentes compartilhados para download e acesso a fontes
externas. Também refatorei a integração com fontes públicas para utilizar
clientes compartilhados e interfaces mais consistentes.

A coleta passou a seguir uma estrutura orientada por manifesto e por versões,
com separação entre staging, objetos deduplicados, inventário e resultados
experimentais. O coletor também passou a permitir a seleção de bases durante a
execução. Componentes legados que ficaram redundantes após essa reorganização
foram removidos ou substituídos por camadas compartilhadas.

### Manifesto, fontes e formatos

Atualizei o `corpus_manifest.yaml` para declarar explicitamente as bases
oficiais e os exemplos locais, suas naturezas, formatos, regras de seleção,
licenças e características de acesso. Também adicionei uma fonte CSV pública
real da SEPLAG-RJ para validar o pipeline com dados tabulares publicados, em vez
de depender apenas de dados artificiais.

O pipeline passou a tratar de maneira integrada documentos PDF, páginas HTML,
arquivos JSON, JSONL e CSV. Para os testes rápidos, foram versionados 20 PDFs e
fixtures reais de HTML, JSON, JSONL e CSV. Esses arquivos permitem reproduzir o
processamento sem depender continuamente de serviços externos.

### Preparação documental e metadados

Criei o fluxo de preparação em `src/prepare_documents.py`, responsável por
transformar os arquivos coletados em unidades adequadas para indexação e
recuperação. O processamento considera o formato original, a configuração do
manifesto e as particularidades da fonte.

Implementei chunking adaptativo: registros estruturados podem permanecer
inteiros, enquanto documentos normativos podem ser divididos por páginas,
cláusulas ou artigos quando esses elementos são identificados. Cada unidade
preserva informações de rastreabilidade como `base_id`, `natureza`, `doc_type`,
origem, SHA-256, vigência, sensibilidade, caminho relativo e hash do manifesto.

O contrato de schema foi alinhado para que `doc_type` represente o formato do
arquivo original, enquanto `natureza` representa a categoria semântica da base.
Também mantive uma fachada compatível em `src/ingest.py` para evitar que a
evolução do pipeline quebrasse os fluxos anteriores.

### Curadoria, busca e documentação

Estruturei ferramentas de curadoria do manifesto e descoberta de fontes,
separando a sugestão de novas fontes da incorporação oficial ao manifesto.
Também implementei busca remota no PNCP e busca local sobre o inventário já
coletado, criando dois fluxos complementares para localizar documentos.

Atualizei o README e a documentação visual em `docs/`, incluindo exemplos de
curadoria, busca remota, busca local e simulação de atualização. A documentação
passou a explicar o fluxo completo de coleta, preparação, busca, curadoria e
geração de embeddings.

### Embeddings e testes

Implementei a geração incremental de embeddings com cache. O processo verifica a
compatibilidade entre conteúdo, metadados, provedor, endpoint, modelo e dimensão
do vetor antes de reutilizar um resultado. Também adicionei validação explícita
da dimensionalidade para impedir a mistura silenciosa de espaços vetoriais
incompatíveis.

Ao final, foram publicados em `data/embeddings/` 516 embeddings versionados, com
dimensão 1024:

- 446 chunks provenientes de PDFs;
- 18 provenientes de HTML;
- 1 proveniente de JSON;
- 1 proveniente de JSONL;
- 50 provenientes de CSV.

Os testes foram ampliados para cobrir manifesto, coleta, curadoria, busca
remota, busca local, schema, preparação documental e geração de embeddings. A
validação final registrou 30 testes principais aprovados e 13 testes adicionais
do coletor aprovados.

### Resultado da sessão

Os principais commits da sessão foram organizados incrementalmente entre
infraestrutura, preparação documental, embeddings, suporte a CSV, documentação
e publicação dos dados:

- `8a20900` — configuração de provedores e dependências de embeddings;
- `df5f10f` — preparação de documentos e metadados;
- `1dba49d` — geração incremental de embeddings;
- `cf5e635` — suporte ao CSV real do corpus;
- `ee38992` — documentação dos embeddings versionados;
- `2c047f1` — publicação dos embeddings na árvore versionada.

A branch `joao-reis` foi atualizada com `pull` e `push` para
`origin/joao-reis`, sem enviar alterações para outra branch.

### Pendências

A nova versão oficial `v2` do corpus ainda não foi promovida para
`data/raw/v2/` e `data/inventory/v2.json`. Existe apenas uma coleta parcial na
área intermediária de staging, mantida fora do versionamento.

Os dados brutos permanecem fora do Git, enquanto os exemplos, os metadados de
preparação e os embeddings derivados estão disponíveis no repositório. As
alterações locais de `data/inventory/v1.json` e `data/CORPUS_INVENTORY.md` ainda
precisam ser reconciliadas antes de serem incorporadas.

De forma geral, a sessão consolidou a base técnica do pipeline: aquisição,
seleção por manifesto, versionamento, deduplicação, preparação documental,
metadados, rastreabilidade, curadoria, busca remota e local, geração incremental
de embeddings, testes automatizados e documentação.

### Uso de inteligência artificial

Utilizei ferramentas de inteligência artificial, especialmente o Codex, como
apoio ao desenvolvimento. A IA foi utilizada para auxiliar na análise da
estrutura do repositório, no planejamento de refatorações, na elaboração de
trechos de código, na criação de testes, na revisão de metadados, na
organização da documentação e na análise do histórico de commits.

As sugestões geradas foram revisadas e adaptadas ao projeto antes de serem
incorporadas. As alterações foram conferidas manualmente, comparadas com o
manifesto e com os requisitos do desafio, e validadas por meio da suíte de
testes. A decisão sobre o que seria incluído no corpus, quais fontes seriam
aceitas e quais arquivos seriam publicados permaneceu sob controle da equipe.
## Atualização — 23/09/2026

### Atividades realizadas

Testei a pipeline de processamento pela CLI utilizando o arquivo CSV real `licitacoes_siga_real.csv`, da base `licitacoes_siga_csv`. A execução do `prepare_documents` processou 1 arquivo, gerou 50 documentos e 50 chunks, sem falhas de extração.

Também validei a fachada `ingest`. A execução exigiu a opção explícita `--allow-uninventoried`, pois o CSV foi utilizado em uma área experimental sem inventário de coleta. Com essa opção, o processamento foi concluído com os mesmos 50 documentos e 50 chunks. O estágio de embeddings foi executado em modo `--dry-run`, identificando 50 chunks pendentes sem realizar chamadas externas.

Adicionei a suíte de testes do corpus, os fixtures de validação, o runner `tests/run_corpus_validation.py` e o relatório atual da execução. A validação registrou 65 testes aprovados e 1 falha, relacionada ao cálculo de caminho relativo no teste `test_official_run_excludes_experimental_bases` quando o pytest utiliza um diretório temporário fora da raiz do projeto.

### Uso de IA

Utilizei o Codex como apoio durante a sessão para localizar os comandos da pipeline, analisar a estrutura do projeto, executar e interpretar os testes da CLI, validar o processamento do CSV real e conferir os artefatos gerados. Também utilizei a ferramenta para organizar o escopo do commit, verificar o staged diff, realizar o commit, enviar as alterações para o repositório remoto e atualizar este acompanhamento. As decisões sobre o escopo, a revisão dos resultados e a validação final foram realizadas por mim.

### Resultado da sessão

O processamento de um CSV real foi validado de ponta a ponta nos estágios de preparação, ingestão e simulação de embeddings. Os testes, fixtures e o relatório atual foram registrados no commit:

- `2844328` — `teste(corpus): adiciona testes e relatório atual de validação`

O commit foi enviado para a branch remota `origin/joao-reis`.
