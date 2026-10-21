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

## Atualização — 25/09/2026

### Atividades realizadas

Integrei as alterações da branch `main` à branch de trabalho e, após o merge, revisei o manifesto e os testes de coleta e ingestão para adequá-los à versão integrada. Também organizei ajustes de documentação da CLI e do inventário do corpus v2 e adicionei um exemplo sintético de e-mail em Markdown para servir como dado de demonstração.

Avancei na coleta declarativa do corpus a partir do manifesto. Foi criada uma interface comum para provedores e implementada a integração com o PNCP, separando a lógica específica dessa fonte da lógica geral de coleta. A configuração passou a descrever parâmetros de consulta e paginação, e a coleta foi atualizada para enumerar documentos e registrar os resultados no inventário. Também foram ampliados os testes de coleta e dos provedores.

Na preparação documental, implementei cache incremental para reaproveitar resultados quando os documentos e suas configurações não mudam. Depois, ajustei o fluxo para localizar e preparar a versão mais recente do corpus, em vez de depender de uma versão fixa. Atualizei a documentação de uso e acrescentei cobertura de teste para esse comportamento.

Também consolidei os testes do corpus sob a pasta `tests/`, organizei fixtures e arquivos de validação e ajustei testes de manifesto, coleta, preparação, provedores, indexação e recuperação. Os guias da CLI foram atualizados para refletir os comandos e o inventário v2.


### Uso de inteligência artificial

Utilizei o Codex como apoio para explorar o histórico e a estrutura do repositório, planejar e implementar partes da coleta por provedores e da preparação incremental, elaborar e atualizar testes e revisar a documentação. Também usei a ferramenta para resumir as alterações e manter este acompanhamento atualizado. Revisei as sugestões e os diffs antes de incorporá-los; as decisões sobre o escopo, as fontes e os critérios do corpus permaneceram sob minha responsabilidade.

### Resultado da sessão

Os principais commits realizados em 25/09 registram a integração de fontes PNCP por provedores, a preparação incremental, a consolidação dos testes e a atualização da documentação:

- `b3bef09` — `feat: adiciona coleta declarativa por provedores PNCP`;
- `8bd97ba` — `feat: adiciona cache incremental na preparação de documentos`;
- `13f9613` — `test: consolida suíte em tests e arquiva testes legados`;
- `a7cae5e` — `Adiciona exemplo de email sintético em Markdown`;
- `618a734` — `Corrige manifesto e testes após merge da main`;
- `fe02d9c` — `docs: atualiza guias da CLI e inventário v2`;
- `8f73716` — `test: ativa testes dos módulos de corpus`;
- `20628e5` — `feat: vincula preparação à versão mais recente do corpus`.

## Atualização — 28/09/2026

### Atividades realizadas

Consolidei as alterações do corpus e dos percursos de ingestão no commit `15d41ac` (`feat(corpus): preparar ingestão das bases v3`) e enviei esse commit somente para `origin/joao-reis`.

Durante a integração, resolvi um conflito em `src/retrieve.py` para conciliar as alterações recebidas da `main` com a implementação existente na branch.

### Resultado da execução v3

Conforme o [registro da execução v3](../docs/registro_execucao_v3.md), o comando `update --official --use-discovered-contracts` promoveu a v3 em 28/09. A seleção determinística foi fixada às 14:46 e a promoção terminou às 16:03, com 1.200 contratos únicos, 1.200 detalhes correspondentes aos IDs selecionados e nenhuma falha no inventário. O inventário contabilizou 1.295 arquivos e 1.243 itens lógicos nas quatro bases. Embeddings e índices não foram executados.

O censo do universo do RJ continua incompleto (`scan_complete=false`); portanto, os 1.200 contratos não demonstram cobertura de todos os contratos do estado. A promoção foi do corpus bruto: extração e normalização ainda não tinham sido executadas.

### Validação

`py -m py_compile src/retrieve.py src/index.py` terminou sem erros e `git diff --check` não apontou problemas. A suíte de testes não foi executada.

### Uso de IA (inteligência artificial)

Utilizei o Codex como apoio para conferir o estado das branches, identificar os commits recebidos de `main`, integrar as alterações, resolver o conflito em `src/retrieve.py`, validar a sintaxe e atualizar este registro. Revisei o resultado da integração e confirmei que apenas `joao-reis` foi atualizada.

### Resultado da sessão

- Commit do corpus: `15d41ac` — `feat(corpus): preparar ingestão das bases v3`;
- merge da `main` em `joao-reis`: `36cb53b` — branch local/remota sincronizada;
- branch `main`: sem commit ou push feito nesta sessão;

## Atualização — 30/09/2026

### Atividades realizadas desde o último registro

Dei continuidade à preparação do corpus após o registro de 28/09, passando pelas versões intermediárias v4 e v5 até consolidar a **v6 oficial**. A v5 chegou a 8.636 arquivos, mas sua distribuição e seu tamanho motivaram uma seleção menor, orientada às perguntas de `PERSONAS.md`, com dados municipais, estaduais e federais do Rio de Janeiro.

Reutilizei os módulos de coleta e preparação do repositório e os scripts de descoberta e avaliação de escopo. A curadoria passou a limitar a seleção a **cinco arquivos por órgão/CNPJ em cada base**, inclusive na esfera federal, evitando concentrar o corpus em muitas unidades do mesmo órgão. As consultas às fontes tiveram tentativas e pausas limitadas; arquivos indisponíveis ou respostas que não eram PDFs foram registrados e pulados, sem interromper toda a seleção nem preencher lacunas com conteúdo inventado.

### Corpus, preparação e proveniência

O inventário v6 está completo e vinculado ao manifesto, com **1.400 arquivos**:

| Base oficial | Arquivos |
|---|---:|
| `contratos_estruturado` | 625 |
| `editais_normativo` | 13 |
| `atas_conversacional` | 376 |
| `pca_agregado` | 386 |

A distribuição ficou em **804 arquivos municipais, 513 federais e 83 estaduais**, abrangendo 166 CNPJs e quatro identificadores de publicadores normativos. O limite por órgão/base foi verificado. A cobertura estadual permaneceu menor; a seleção é uma amostra intencional, não um censo nem uma distribuição perfeitamente equilibrada. Os formatos são 1.347 JSON, 49 PDF e quatro HTML.

Corrigi a preparação para preservar versão, hashes, origem e relações entre documentos até a recuperação. Os metadados passaram a usar links públicos do conteúdo em `origem`, mantendo `source_file` apenas como caminho relativo interno de rastreabilidade. Removi aliases redundantes e repetições de campos, e fiz os dados reais dos JSONs prevalecerem sobre informações herdadas do contexto da coleta. Mantive descrições, unidades e valores originais.

Os PDFs foram processados pelo fluxo existente de extração textual, com reutilização do cache de páginas e suporte ao OCR quando necessário. As quatro bases oficiais foram normalizadas separadamente; as bases experimentais e o fixture de e-mails permaneceram separados. Os indicadores continuaram calculados pelo código existente, sem depender do LLM para produzir estatísticas.

### Embeddings locais e recuperação

Implementei o fluxo com o executável `llama.exe` e o checkpoint local **Qwen3-Embedding-0.6B Q8**, incluindo validação de tokens antes da geração e retomada por cache. Organizei os embeddings da v6 em `data/embeddings/2026-09-30_12-08-51/`, com uma subpasta por base. A auditoria confirmou **5.621 vetores de dimensão 1.024**, finitos e não nulos, com proveniência da v6.

Comparei configurações de geração local. No pequeno benchmark, o melhor perfil medido utilizou Vulkan na GPU, duas threads e lote 16, atingindo aproximadamente **5,29 documentos por segundo**. Esse resultado é específico da máquina e da amostra testadas. O reprocessamento dos metadados reutilizou os vetores existentes, sem recalcular todos os embeddings.

Mantive o modelo de queries exigido no ECOA, **Qwen3-Embedding-0.6B Q4**, distinguindo-o do artefato Q8 local. O teste de compatibilidade com quatro perguntas e 20 documentos obteve menor cosseno de aproximadamente **0,967** e sobreposição média de **100% no top-5**. Esses resultados atenderam aos critérios definidos para o projeto, sem demonstrar identidade entre os checkpoints ou compatibilidade universal.

Corrigi a leitura de `chunk_content_sha256` na indexação, preservando compatibilidade com `content_sha256`, e construí os índices FAISS a partir dos vetores armazenados. Ajustei a recuperação para carregar os índices persistidos, preservar os metadados e informar filtros relaxados na busca ampliada. O ECOA foi usado para embeddings das perguntas e geração das respostas, com cache de queries, sem novas chamadas para embedar os documentos.

### Consultas analíticas e avaliação

Ampliei os modelos de análise da pergunta com métrica, agrupamentos, filtros analíticos e referência temporal. Estendi o módulo `structured_query` com compilação PostgreSQL pura, parametrizada e limitada a datasets, campos e operações permitidos. Registrei cinco exemplos relevantes das personas, distinguindo datas de publicação, assinatura, resultado e PCA. **Não conectei nem executei esse novo caminho em PostgreSQL e não apliquei DDL**; o cálculo já existente nas consultas continuou usando SQLite.

Na revisão de respostas, encontrei um erro na pergunta sobre fornecedores com maior valor total: a consulta retornava maiores contratos por órgão. Corrigi para somar os valores contratados por fornecedor e ordenar pela soma. Um teste de regressão distinguiu vários contratos pequenos de um contrato maior, e a repetição do caso pela CLI com ECOA confirmou a correção.

Executei pela CLI existente **22 perguntas**: as 18 de `PERSONAS.md`, a falha esperada e os três exemplos do Cenário 1. As chamadas foram sequenciais, com pausas e reutilização de cache. Registrei rotas, evidências, fontes, filtros, confiança, recusas, latências e tokens. O avaliador de **rota/recusa** marcou **13 acertos em 22 casos, ou 59,09%**; esse resultado não mede correção factual das respostas.

Conferi as evidências citadas e registrei as falhas por caso. Permaneceram problemas como interpretar a categoria “Compras” como modalidade licitatória, ignorar uma norma explícita, devolver uma recusa sem texto e concluir aumento ao longo do ano apenas com picos de valores. Comparações de preços unitários e de equivalência técnica ficaram limitadas pela cobertura disponível. Preservei os mecanismos originais de recusa e mascaramento; os guardrails adicionais introduzidos durante a sessão foram retirados conforme solicitado.

### Entregas, versionamento e validação

Gerei e validei dois arquivos: [ZIP com os PDFs das atas v6](../atas-zip/atas_conversacional-v6.zip), com 40 PDFs, e [ZIP com os Markdown correspondentes](../atas-zip/atas_conversacional-v6-md.zip), com 40 arquivos e texto extraído de 542 páginas. Preservei caminhos relativos, conferi integridade e hashes e incluí links públicos das fontes nos Markdown. Os ZIPs da v6 foram incluídos no repositório e enviados ao GitHub após autorização; não enviei e-mail de entrega.

Organizei as alterações em cinco commits, todos de autoria registrada como João Pedro Félix Reis e enviados somente para `origin/joao-reis`:

- `ce522f1` — `feat(corpus): amplia coleta PNCP e preserva proveniência dos documentos`;
- `5c04774` — `feat(embeddings): gera vetores locais e reutiliza índices persistidos nas consultas`;
- `7e090b7` — `feat(sql): compila consultas PostgreSQL e soma contratos por fornecedor`;
- `bb5aa49` — `feat(corpus): publica snapshot consultável da v6 e ZIPs das atas`;
- `bafd418` — `docs(v6): alinha guias e registra curadoria, avaliação e entregas`.

Versionei os vetores da v6, FAISS, documentos normalizados, SQLite, inventários, scripts e relatórios selecionados. Os embeddings e o ZIP da v5 ficaram preservados apenas no disco, sem Git LFS. Ajustei `.gitignore` para manter credenciais, cache e artefatos locais fora dos commits, e `.gitattributes` para preservar os bytes dos snapshots, evitando que a conversão CRLF/LF invalide seus hashes.

Antes desta atualização, conferi o histórico e a única worktree: a branch `joao-reis` estava limpa e sincronizada com o remoto em `bafd418`. A `main` não recebeu alterações ou push nessa entrega. A suíte offline passou com **511 testes**, a auditoria v6 confirmou arquivos, vetores, URLs públicas e limites por órgão/base, e `git diff --check` não apontou problemas. Os detalhes estão no [registro da execução v6](../docs/registro_execucao_v6.md) e nos relatórios selecionados em `eval/runs/`.

### Pendências

- Corrigir as falhas de recuperação e geração identificadas nos 22 casos e repetir os casos afetados.
- Ampliar a cobertura estadual, de PCA e de preços unitários comparáveis, respeitando os limites de seleção; a vigência normativa histórica ainda não foi comprovada integralmente.
- Completar cross-encoder com ablação, persistência de feedback e histórico, execução PostgreSQL e a trilha de e-mails prevista no desafio. O fixture sintético não foi promovido para substituí-la.
- O snapshot principalmente de 2025 não comprova que uma licitação continue aberta em setembro de 2026; a cobertura temporal precisa ser considerada nas respostas.

### Uso de IA (inteligência artificial)

Utilizei o Codex como apoio à investigação das fontes, implementação de correções modulares, configuração e comparação de embeddings locais, execução dos testes, revisão das respostas contra evidências, preparação dos ZIPs e organização dos commits. O ECOA foi usado explicitamente na avaliação de queries e respostas. As entregas foram verificadas por testes automatizados, auditorias de arquivos e vetores, conferência dos hashes e inspeção do histórico Git; as limitações observadas foram registradas, sem tratar acerto de rota como prova de correção factual.

## Atualização — 05/10/2026

### Atividades realizadas

Nesta sessão, consolidei as melhorias locais do RAG na branch `joao-reis`, integrei as alterações disponíveis em `origin/main` e avancei na utilização do acervo pelo frontend. Também revisei o projeto com base no histórico Git, no código, nos acompanhamentos e nos artefatos, separando implementação publicada, trabalho local e limitações ainda abertas.

### Provedores de LLM e tratamento das consultas

- Adicionei ao cliente compartilhado o modo OpenRouter e o fallback de provedor, mantendo ECOA como principal no modo automático. O fallback usa `openrouter/free`, quando configurado, sem definir modelo pago como alternativa.
- Disponibilizei o flag `--openrouter-only` na CLI e registrei provedor, modelo, uso do fallback e falhas na observabilidade. Os embeddings das perguntas continuam no ECOA ou no cache existente, sem misturar outro espaço vetorial com a v6.
- Consolidei a análise opcional por LLM, com intenções e filtros estruturados, validação do plano, cache e fallback para as regras existentes. O modo por regras permanece como padrão da análise de consultas.
- Ampliei o tratamento de evidências e das consultas analíticas: limites monetários exclusivos, distinção entre valor contratado, estimado, homologado e unitário, referências temporais, agrupamentos e indicação de cálculo indisponível. Evitei tratar valor global como preço por unidade.
- Ajustei a preparação do contexto e a validação da geração para preservar evidências das diferentes bases/intenções, referências públicas e respostas de insuficiência de dados. Essas melhorias não constituem comprovação automática da correção factual.

Os principais arquivos dessa frente são `llm_ecoa.py`, `config.py`, `app/cli.py`, `src/query_analysis.py`, `src/schema.py`, `src/structured_query.py`, `src/retrieve.py`, `src/pipeline.py` e `src/generate.py`.

### Suplemento conversacional sintético

Integrei o material fornecido ao projeto como `emails_sinteticos`, preservando a distinção entre cenário fictício e fatos do PNCP. Usei os **25 JSONs individuais** como fonte canônica, sem duplicar os TXT equivalentes nem o JSONL consolidado. O conjunto contém 25 conversas e 45 mensagens, incluindo e-mails, ouvidoria, pareceres e transcrição.

Implementei `src/synthetic_conversations.py` e adaptei a preparação para manter uma conversa inteira por chunk, com cabeçalhos, identificadores, hashes e metadados. A classificação de sensibilidade é obrigatória: **20 conversas públicas e 5 restritas**. Dados bancários e credenciais forçam classificação restrita; a retomada por cache preserva essa classificação. Os trechos públicos utilizam o mascaramento já existente, e o contexto identifica expressamente o conteúdo como sintético.

Ajustei manifesto, validadores e testes para admitir o suplemento sem substituir as quatro bases históricas da v6. Documentei a origem, a licença não informada, a reconstrução e as verificações em [`docs/emails_sinteticos.md`](../docs/emails_sinteticos.md).

Preparei e disponibilizei o índice do suplemento com o mesmo modelo/configuração dos índices existentes: **Qwen3-Embedding-0.6B Q8, dimensão 1.024**. A indexação aditiva com `--merge-existing` preserva as outras bases. O arquivo ativo passou a ter **5.646 chunks: 5.621 da v6 e 25 sintéticos**, com IDs únicos; a comparação confirmou os documentos anteriores intactos. O suplemento não foi apresentado como uma nova coleta PNCP v7.

O relatório local de validação registra a conversa sobre papel A4 da DPRJ em primeiro lugar no BM25 e na busca densa. Esse resultado verifica recuperação de um exemplo, não qualidade geral de respostas do LLM.

### API e interação conversacional

- Disponibilizei e ajustei os endpoints de exploração de contratações, documentos, órgãos e fontes, além do resumo do projeto, utilizando manifesto, inventários e documentos ativos, sem mocks nem novas coletas nessa camada.
- Organizei `app/catalog.py`, `app/catalog_models.py` e os testes de catálogo. A API mantém leitura filtrada por publicidade, mascaramento e tratamento de indisponibilidade, sem expor texto bruto de registros restritos ou caminhos locais.
- Atualizei o README e o contrato HTTP do frontend, preservando e reconciliando os contratos desenvolvidos pela equipe.
- Implementei `src/conversation.py` para responder saudações, agradecimentos e pedidos de ajuda sem busca factual desnecessária. Para mensagens mais longas ou indiretas, acrescentei classificação de intenção com fallback para o pipeline RAG.
- Mantive consultas factuais e pedidos sujeitos a guardrails no fluxo apropriado. Uma saudação junto de um pedido de senha ou dado bancário não deve desviar das recusas existentes. Esse tratamento de interação não equivale a memória persistente de conversa entre perguntas.

### Revisão técnica e validação

Revisei as contribuições do grupo com Git e blame e gerei um relatório externo ao repositório, `../RESUMO_AUDITORIA_GRUPO04_CENARIO01.md`. Conferi hashes dos 1.400 arquivos brutos da v6, textos e IDs dos 5.621 chunks originais, dimensão e integridade dos vetores, índices FAISS, ZIPs e SQLite. A revisão também identificou problemas de qualidade textual nas atas, ausência de vigência nos metadados normativos e classificação inadequada de recursos agregados como planos PCA.

Os resultados intermediários foram tratados conforme a revisão testada: o snapshot inicial commitado passou com 895 testes, e uma cópia local da integração sintética apresentou falhas de contrato de manifesto e parser YAML. Essas falhas não foram tratadas como entrega concluída. Após os avanços e o merge, executei novamente `python -m pytest tests/ -m "not llm" -q --disable-warnings`: **1.074 testes passaram em 33,39 segundos**. `git diff --check` também terminou sem problemas.

Essa última execução incluiu a correção local que resolve o reranker padrão antes dos caminhos convencional e por intenções, com testes para reranker padrão, explícito e desligado. Na conferência deste registro, `src/pipeline.py`, `tests/test_pipeline_query_analysis.py` e o SQLite derivado ainda tinham alterações locais, sem commit. Não apresentei o resultado do checkout como uma validação isolada do commit publicado.

Não repeti uma rodada completa de geração das respostas com ECOA/OpenRouter nem a ablação real do cross-encoder nesta atualização. Os relatórios anteriores continuam ligados às suas respectivas configurações e gabaritos; acerto de rota/recusa não foi tratado como prova de correção factual.

### Commits e estado da branch

Os commits de minha autoria registrados em 05/10 foram:

- `e557a1a` — `feat(llm): adicionar modo OpenRouter e fallback de provedor`;
- `614a94e` — `fix(rag): melhorar análise de consultas e tratamento de evidências`;
- `d31925f` — `feat(corpus): integrar conversas sintéticas com controle de sensibilidade`;
- `4fd8a06` — `feat(api): disponibilizar endpoints de catálogo e resumo do projeto`;
- `d4a14fb` — `fix(chat): aceitar saudações e mensagens conversacionais longas`;
- `5c69b86` — `merge: integrar origin/main em joao-reis`;
- `146ceea` — `data: versionar indice ativo de emails sinteticos`.

Na conferência local, `HEAD` e `origin/joao-reis` estavam alinhados em `146ceea`. O merge trouxe as alterações integradas pelo coordenador, incluindo trabalho de API, avaliação, recuperação e geração dos colegas. Não atribuí essas implementações compartilhadas como autoria exclusiva minha. O presente acompanhamento e as correções locais posteriores ainda precisam ser versionados.

### Pendências

- Revisar e publicar as alterações locais restantes, mantendo consistência entre código, testes e SQLite derivado.
- Reexecutar a avaliação completa na revisão integrada, separando rota, recusa, conteúdo, cálculo e citação; revisar as expectativas do gabarito contra o corpus.
- Corrigir a materialização de `pca_planos`, que ainda inclui recursos sem ano e valor de PCA, e melhorar a qualidade dos chunks de atas e a comprovação de vigência normativa.
- Validar frontend e backend juntos, incluindo histórico, feedback, catálogo, tempos de espera e comportamento de perguntas dependentes de contexto.
- Documentar condições de uso/licença do suplemento sintético e manter a distinção entre conteúdo fictício e evidência de contratação real.
- Tratar autenticação, privacidade e retenção do histórico antes de exposição pública; preparar os exemplos e o roteiro do Demo Day.

### Uso de IA (inteligência artificial)

Utilizei assistentes de IA como apoio à análise do histórico e do código, à consolidação das correções, à elaboração de testes e documentação, à revisão da integração e à preparação de explicações sobre as estratégias e os arquivos do projeto. As verificações foram feitas com comandos Git, testes offline, leitura dos artefatos, conferência de hashes e comparação com os contratos existentes. Solicitei a separação entre autoria individual e trabalho compartilhado e entre resultados históricos, alterações publicadas e pendências locais, sem registrar como concluída uma avaliação factual que não foi executada nesta atualização.


## Atualização — 09/10/2026

### Atividades realizadas

Nesta sessão, trabalhei na correção de bugs do pipeline SQL, na preparação do repositório para novos clones e na integração do frontend com o backend.

**Backend (branch `joao-reis`):**

Corrigi o pipeline SQL que estava filtrando incorretamente termos de objeto. Palavras como "somando", "celebrado", "principais" e termos de reformulação ("continuação", "pergunta", "anterior") estavam sendo tratadas como descritores de objeto contratado, gerando filtros vazios ou incorretos. Adicionei essas formas às stop words e ajustei o stemmer para capturar gerúndios.

Implementei a extração de órgão por catálogo do corpus. O sistema agora reconhece "BANCO CENTRAL DO BRASIL" como órgão, mesmo quando "central" não está na lista de qualificadores. Adicionei um fallback que consulta o catálogo de órgãos do banco quando a heurística de tokens não forma o nome completo.

Ajustei a detecção de métrica para reconhecer "valor" como trigger de soma. Perguntas como "Qual o valor de todos os contratos de 2025?" agora são roteadas corretamente para o SQL determinístico, em vez de irem para a busca vetorial onde o LLM somava apenas os3 contratos recuperados.

Incluí o `numero_controle_pncp` na listagem SQL para que o LLM possa identificar contratos específicos. O output agora mostra o PNCP control number junto com valor, data e órgão.

Abri temporariamente o endpoint de histórico (`GET /api/v1/history`) mantendo os traces protegidos por `X-API-Key` ou `X-Session-Id`. O histórico agora é acessível sem autenticação para facilitar a demonstração.

Integrei o merge de `origin/main` preservando as correções de reformulação de continuação e o novo comportamento de contexto anexado quando o modelo falha.

**Frontend (fork `sevenRevy/frontendRAG`, branch `main`):**

Preparei o repositório para novos clones com documentação completa de instalação e scripts de diagnóstico. Adicionei `npm run check:backend` e `npm run check:query` para verificar a API, o corpus e o banco estruturado antes de abrir a interface.

Implementei o envio de `session_id` e `histórico` nas consultas de continuação. O frontend agora mantém o ID de sessão entre perguntas e envia as trocas anteriores para que o backend possa reformular continuções como "E em 2024?".

Corrigi o botão "Consultar no NEXO" da página de Contratações para usar o PNCP control number em vez do `numero_contrato`. O `numeroContratoEmpenho` não está no banco de dados, então a consulta falhava. Agora gera queries como "Quais são os detalhes do contrato PNCP 00038166000105-2-000007/2025?" que encontram o contrato corretamente.

### Resultado da sessão

O pipeline SQL agora responde corretamente:
- "Qual o valor de todos os contratos de 2025?" → R$2.546.786.958,21 (SQL completo, antes: R$673M de3 contratos)
- "Quais contratos do BANCO CENTRAL DO BRASIL?" →5 contratos listados com PNCP number
- "Quais são as principais contratações de BANCO CENTRAL DO BRASIL?" →3 fontes, confiança média (antes: 0 fontes, confiança baixa)
- "E em 2024?" → Reformulada corretamente com contexto da pergunta anterior

O frontend agora envia histórico nas continuações e os atalhos da página inicial funcionam corretamente. Os scripts de diagnóstico validam a API antes de abrir a interface.

Foram executados590 testes do backend e36 testes do frontend, todos passando.

### Commits realizados

Backend (`origin/joao-reis`):
- `32b1045` — `fix(api): abrir historico temporariamente mantendo traces protegidos`
- `423f182` — `feat(observabilidade): alinhar logs offline e cobertura de uso dos modelos`
- `b36ae90` — `merge: integrar main preservando contexto, logs e historico aberto`
- `77f3e4a` — `fix(sql): filtrar gerundios, extrair orgao por catalogo e detectar valor como soma`
- `505fac0` — `fix(sql): filtrar termos de reformulacao do contexto`
- `6c8e5f3` — `fix(sql): incluir numero_controle_pncp na listagem para identificar contratos`
- `527d7e9` — `fix(sql): filtrar principal/principais dos termos de objeto`

Frontend (`sevenRevy/frontendRAG`, `main`):
- `21ab3a9` — `feat(setup): adicionar diagnostico do backend e pergunta real`
- `dde41be` — `docs(setup): preparar execucao apos clone da fork com backend joao-reis`
- `8d315a9` — `feat(chat): enviar historico e session_id nas continuacoes`
- `91c0fbb` — `fix(contratacoes): usar PNCP number na consulta para encontrar contrato`

### Pendências

- Instalar `sentence_transformers` no ambiente do servidor para habilitar o rerank por cross-encoder (atualmente usa fallback RRF).
- Avaliar se o endpoint de histórico deve permanecer aberto ou voltar a exigir autenticação após a demonstração.
- Testar o fluxo completo com o frontend conectado ao backend, incluindo feedback e histórico persistido.
- Revisar se o SQL de listagem deveria incluir também o `numero_contrato` (numeroContratoEmpenho) quando disponível no payload do PNCP.

### Uso de IA

Utilizei assistentes de IA para análise de logs de pipeline, diagnóstico de queries SQL com resultado incorreto, implementação de correções em stop words e stemmer, criação de scripts de diagnóstico do frontend, e elaboração de documentação de instalação. As correções foram validadas com testes automatizados e consultas reais ao endpoint da API.

## Atualização — 14/10/2026

### Atividades realizadas

Nesta sessão, concluí a política de acesso do histórico, rodei o golden set do Demo Day, coloquei o build do corpus v7 em execução com fallback de embeddings e comecei o censo de atas de registro de preços.

**Histórico — política de dois personas (P8B):**

Substituí a abertura temporária do histórico pela política definitiva de dois personas: administrador (cabeçalho `X-API-Key`) vê todas as conversas e pode filtrar por `session_id`; o usuário comum (`X-Session-Id`) vê apenas a própria conversa. Sem credenciais, a resposta é 401 com instrução de envio, e o parâmetro de consulta nunca autentica. O frontend passou a manter o `session_id` por aba (sessionStorage) e a enviar o cabeçalho nas consultas de histórico.

Gerei a `RAG_API_KEY` do demo (em `.env`, fora do Git) e validei ao vivo: anônimo 401, dono vê 3/3 mensagens, sessão alheia retorna 0 itens, admin vê tudo (20) e filtra (3), chave errada 401, trace 200 para o dono e 401 para sessão alheia. Testei uma conversa de 3 turnos com continuação ("E em 2024?"), feedback positivo e negativo, caso de timeout (o cliente desiste em 1 s, a consulta termina e é registrada como `q_543a46e2`) e os catálogos de contratações, documentos, órgãos e fontes. `check:backend` e `check:query` passaram.

**Golden set — rodada única do Demo Day:**

Rodei o golden (`benchmark-demo-day-2026-10-14`): 23/26, contra 25/26 na véspera. Os casos P2.1 e P4.2 variaram — instabilidade de top-k, já conhecida e registrada. A decisão sobre o número do demo (corrigir o top-k e rodar uma vez, aceitar 23/26 ou adiar para a re-auditoria pós-demo) ficou adiada.

**Corpus v7 — build e fallback de embeddings:**

O censo de contratos e publicações fechou em 272/272 janelas, 168.074 registros e 0 falhas: `documents.jsonl` com 168.051 chunks (110.378 contratos e 57.673 publicações). Começou a build de embeddings na GPU local (llama-server, RTX 3050). Com o ECOA fora do ar, implementei o encadeamento ECOA primário + encoder local como reserva (`embed_fn_com_fallback`), com pausa entre lotes para o endpoint compartilhado, contagem de vetores por origem no inventário e retomada por `chunk_id` (vetores já gravados são reaproveitados). Corrigi um defeito em que a função de lote roubava o decorador de observação da indexação. Testes: `test_embed_fallback` 6, `test_index` 12, `test_build_direto` 5 e `test_projecao` 12 passando.

**Censo de atas de registro de preços:**

Levantei o comportamento da API de atas do PNCP: filtra por período de vigência (não por ano da ata), rejeita períodos maiores que 365 dias (HTTP 422) e responde 429 em HTML a partir de ~15 chamadas em rajada — o ritmo seguro medido é 12 requisições por minuto. Implementei janelas anuais por CNPJ (vigência `ano` e `ano + 1`) com o filtro declarativo `anoAta = 2025` fechando o corte, e o modo `--so-atas` na CLI de coleta, com testes (12 passando).

Derivei a lista de órgãos do RJ do próprio censo: 522 CNPJs com atividade em 2025 (`orgaos_rj_2025.json`), contra os 164 da curadoria v6. A coleta roda em destino separado (`data/v7_direto_atas`), retomável por janela; até o fim da sessão, 11.689 atas únicas de 2025 (100% `anoAta = 2025`, deduplicadas por `chunk_id`).

**Modalidade — o que a API tem e o que não tem:**

Verifiquei os 110.378 registros de contrato do censo: o endpoint `/v1/contratos` não expõe modalidade nenhuma — não é falha de extração. As publicações trazem `modalidadeId`/`modalidadeNome` em 100% (Dispensa 28.610, Pregão Eletrônico 15.643, Inexigibilidade 12.142, Concorrência Eletrônica 964, entre outras). A modalidade do contrato é derivável pela compra vinculada (`numeroControlePncpCompra`): 28.664 por join local, sem chamadas de API. Os contratos citam 276 atas distintas por `numeroControlePncpAta` (400 contratos com o campo) — serão buscadas por fetch direto, sem janelas.

Reformulei a taxonomia planejada ancorada em campos oficiais (modalidade derivada, `categoriaProcesso`, `amparoLegal` como referência, não como assunto) e holdout agrupado por compra, para evitar vazamento entre desenvolvimento e avaliação.

### Resultado da sessão

- Política do histórico validada ao vivo com os dois personas e conversa real de 3 turnos.
- Golden do Demo Day: 23/26 (variação de 2 casos pela instabilidade de top-k).
- v7: 168.051 chunks no censo; embeddings em ~41% no fim da sessão (ETA ~8 h).
- Atas: 11.689 únicas de 2025 em coleta contínua; estimativa de 15–35 mil no total.
- Modalidade: ausente nos contratos (API), 100% nas publicações, 28.664 deriváveis por join.
- Testes do backend passando: `test_embed_fallback` 6, `test_index` 12, `test_build_direto` 5, `test_projecao` 12, `test_coletar_direto` 12.

### Commits realizados

Backend (`origin/joao-reis`):
- `73f2bdb` — `fix(consulta): qualidade das respostas nos defeitos do ensaio P5`
- `cdd5dc2` — `feat(avaliacao): criterios de conteudo e avaliador sensivel a negacao`
- `c3d904d` — `feat(corpus): censo v7 em modo direto com DIFF, projecao e regressao`
- `8432c56` — `feat(api): historico com dois personas, admin e novo usuario`
- `2642485` — `feat(setup): instalacao e modelos para clonar e rodar o RAG`
- `046f4e0` — `docs: roteiro do Demo Day, plano de acao e documentos alinhados`

Frontend (`sevenRevy/frontendRAG`, `main`):
- `0e3ee82` — `feat(historico): dois personas no acesso ao historico`
### Pendências

- Decidir o número do golden para o Demo Day (corrigir o top-k e rodar uma vez, aceitar 23/26).
- Fechar o censo de atas pelas janelas e rodar as embeddings delas com o ECOA primário.
- Buscar por fetch direto as 276 atas citadas pelos contratos do censo, com prioridade.
- Conferir conflitos do join de modalidade e aplicar o preenchimento com proveniência.
- Iniciar o taxonomia reformulada (taxonomia ancorada + holdout por compra).
- Top-k exaustivo (ranking instável entre rodadas) e filtro de fornecedor/município (apelido "Infolog" cai no filtro de termos de objeto).
- Ao fim das embeddings do v7: regressão (referência `projecao-v6-3modos-2026-10-14/index_completa`) e golden sobre o v7 antes de qualquer promoção.

### Uso de IA

Utilizei assistentes de IA para investigar o comportamento da API de atas do PNCP (parâmetros, limites de período e de taxa), implementar o fallback de embeddings com retomada, analisar a cobertura de modalidade nos registros e preparar os incrementos do censo. As decisões foram validadas com testes automatizados, medições locais e consultas pontuais à API.

## Atualização — 16/10/2026

### Atividades realizadas desde o último registro

Dei continuidade à recuperação e à validação do corpus v7, com foco na correção do recorte territorial e temporal, na preservação dos dados e na comparação da recuperação com a v6. Mantive os candidatos em staging, sem substituir a base ativa nem promover a v7.

**Coleta, preservação e retomada:**

Consolidei a política compartilhada de coleta para UF, data de assinatura, identidade da unidade, compra vinculada e classificação dos instrumentos. Separei contratos, empenhos e outros instrumentos, evitando contabilizá-los indistintamente como contratos. A retomada passou a verificar a compatibilidade entre janela, política, manifesto e chunking; paginação incompleta ou contraditória não recebe certificação de coleta concluída.

Implementei melhorias de cache, checkpoints atômicos, limites de consultas e pausas para respostas HTTP 429/503, respeitando `Retry-After`. Também ajustei a deduplicação e a escrita de documentos e vetores para usar arquivos provisórios, preservando os resultados anteriores em caso de falha e reduzindo o risco de perda por falta de memória.

Preservei uma baseline dos artefatos ativos e ensaiei sua restauração em sandbox. A conferência dos arquivos restaurados validou SQLite e FAISS, mas não incluiu a aplicação funcionando nem perguntas pela API; portanto, o rollback integrado continua pendente.

**Cadastro oficial e complementação das bases:**

A verificação cadastral alcançou os **524 CNPJs-semente**, com **5.049 unidades RJ e zero falhas**, conforme a consolidação da execução. Usei o cadastro oficial para reconciliar o território das atas, em vez de inferir a UF pelo nome ou pelo CNPJ do órgão.

O candidato_04 passou a reunir **32.956 atas com evidência territorial cadastral do RJ**, sem pendências de UF dentro da seleção. A recuperação também incorporou 572 publicações de modalidades omitidas da aquisição anterior e dez compras auxiliares. Esses complementos permitiram preencher modalidade por vínculo com a compra, mantendo a proveniência e sem contar compras auxiliares como publicações principais.

Avancei na complementação de instrumentos assinados em 2025 e publicados em 2026, com cache e retomada por janela. Incorporei ao candidato_05 as bases herdadas normativa e agregada: **1.557 chunks normativos e 911 de PCA**, preservando texto, hashes e identidades.

**Embeddings e montagem do FAISS:**

Implementei ferramentas de montagem e reestruturação do índice candidato, com reaproveitamento de vetores e organização por base. Corrigi a validação do modelo na retomada para impedir que vetores de outro modelo fossem apenas reidentificados como se fossem compatíveis.

O manifesto da montagem do candidato_05 registra **142.026 vetores de dimensão 1.024**, com **Qwen3-Embedding-0.6B Q8_0 local**: **108.385 reaproveitados e 33.641 gerados**.

### Avaliação de recuperação

Implementei `eval/medir_regressao_v7.py` e comparei o candidato com o índice v6 de referência usando os **47 casos** de `eval/retrieval_set.jsonl` e o encoder local Q8_0.

| Configuração | hit@3 | hit@10 | mrr@10 |
|---|---:|---:|---:|
| v6 — busca densa de referência | 0,7021 | 0,7872 | 0,6401 |
| v7 — busca densa | 0,5319 | 0,6170 | 0,5011 |
| v7 — BM25 + RRF | 0,5745 | 0,7021 | 0,5036 |

A comparação densa registrou **10 regressões e uma melhoria**. A comparação da v7 híbrida com a v6 densa registrou **17 regressões e sete melhorias**; ela não isola apenas o efeito do corpus, pois também muda a estratégia de recuperação.

A busca híbrida ajudou em parte das consultas por identificadores, mas prejudicou algumas perguntas semânticas. Registrei como proposta avaliar uma seleção de estratégia conforme o tipo de pergunta, sem apresentá-la como correção já implementada. Também mantive a necessidade de revisar relevância e cobertura por caso: posição no ranking não comprova suficiência de evidência nem correção factual da resposta. Não tratei essas medições como uma nova aprovação do Golden Set ou como autorização de promoção.

Os detalhes estão no [relatório de regressão v7](../docs/RELATORIO_REGRESSAO_V7_2026-10-16.md), no [relatório de execução](../docs/RELATORIO_EXECUCAO_V7_2026-10-16.md) e nos registros de [recuperação](../docs/RELATORIO_RECUPERACAO_V7_2026-10-15.md) e [reconciliação cadastral](../docs/RELATORIO_CADASTRO_V7_2026-10-16.md).

### Validação e integração

O log da suíte associada à correção de modelo registra **1.731 testes aprovados em 79,29 segundos**. As validações offline da recuperação utilizaram bloqueio de rede externa para evitar chamadas involuntárias a provedores. Esses resultados são das execuções registradas nos artefatos; não executei novamente a suíte completa apenas para redigir este acompanhamento.

Integrei as alterações da `main` à branch `joao-reis`, incluindo contribuições da equipe para avaliação e perguntas dependentes de histórico. Mantive a distinção entre o trabalho compartilhado recebido pelo merge e minhas implementações de coleta, recuperação, indexação e regressão.

### Commits já existentes no histórico

Os principais commits de minha autoria em 16/10, anteriores à edição deste registro, são:

- `bb63704` — `feat(coleta): infraestrutura de coleta configurável v7`;
- `bb3d118` — `feat(recuperacao): scripts de recuperação e complementação do corpus v7`;
- `799ab39` — `feat(index): montagem e reestruturação FAISS v7, fix embedding model`;
- `a754ed3` — `feat(eval): regressão de recuperação v7 com dense e hybrid`;
- `2712b9e` — `docs(v7): relatórios de recuperação, cadastro, execução e regressão`;
- `e7e51cf` — `data(v6): adiciona índice e documents v6 de referência para regressão`.

### Pendências

- Auditar a consistência final entre documentos, SQL, índices e origem dos vetores do candidato.
- Investigar as regressões e validar uma estratégia de recuperação para consultas semânticas e por identificadores.
- Reauditar o Golden Set atual, de 29 casos, sobre o candidato, separando recuperação, conteúdo, cálculos, citações e recusas.
- Validar o rollback com a aplicação funcionando e preparar o relatório final. A v6 permanece ativa; a promoção da v7 depende de decisão explícita.

### Uso de IA

Utilizei assistentes de IA como apoio à análise do corpus e do código, às correções de coleta e retomada, à reconciliação cadastral, à montagem dos índices, à elaboração de testes e à comparação dos resultados de recuperação. Para este acompanhamento, conferi o histórico Git, os relatórios, o manifesto do índice e os logs de testes, distinguindo checkpoints intermediários de resultados posteriores e resultados medidos de propostas ainda pendentes.

## Atualização — 19/10/2026

### Atividades realizadas desde o último registro

Em qualidade de respostas (sem reindexar), foram feitas correções no caminho de consulta para proteger os artefatos do snapshot (documents.jsonl, SQLite, índice), além da expansão do prompt no generate.py (datas de publicação, modalidade de compra, apresentação curta e formatação em R$), tudo documentado em docs/QUALIDADE_RESPOSTAS_SEM_REINDEXACAO.md. Para SQL e apresentação determinística, o novo módulo src/sql_markdown.py passou a gerar tabelas GFM sem acionar LLM, enquanto o structured_query.py agora reconhece consultas do tipo "quais órgãos e total", limita "maior/menor contrato" a um registro e permite listas diretas com filtros analíticos, valor ou ano. Na recuperação de normas, o retrieve.py recebeu ordenação aprimorada com preferência para identidade declarada sobre menção incidental, visão geral focada nas disposições iniciais, artigos explicitamente pedidos e deduplicação por número/ano. Na etapa de fornecedor e consulta, o query_analysis.py passa a ignorar artigos e preposições em nomes de fornecedores (como Casa da Moeda), garantindo que fornecedores reconhecidos não virem órgãos no fallback. Em conversação, o conversation.py responde sobre a origem dos dados sem buscar factual e exige confiança maior ou igual a 0.9 para recusas de FORA_DE_ESCOPO. A refatoração do catálogo em catalog.py agora carrega apenas o inventário oficial mais recente, compacta metadados por arquivo/contrato, aplica mascaramento de dados sensíveis exclusivamente na página publicada, protege a carga concorrente com RLock e aciona o aquecimento em thread no boot (catalog.prewarm em _ciclo_de_vida). A documentação e contratos foram atualizados em FRONTEND_API_CONTRACT.md, OBSERVABILIDADE.md e NUMERICAL_VALIDATION.md, acompanhados de novas variáveis no .env.example. A suíte de testes novos foi expandida com test_catalog_api.py, test_sql_markdown.py, test_sql_template.py, test_response_quality_regressions.py e test_retrieval_runtime_quality.py.

Nesta sessão, trabalhei em duas correções de interface: o carregamento da tela **EXPLORAR ACERVO** e a atualização da página **TRANSPARÊNCIA DA IA**. Também investiguei um teste intermitente relatado por um colega.

**EXPLORAR ACERVO — carregamento:**

A tela estava estourando o timeout de 20 s do navegador. A causa medida era o servidor: a primeira chamada a `GET /api/v1/explore/contratacoes` levava **15,17 s**, porque `app/catalog.py` carregava na memória todos os inventários históricos (até 19 MB) e desserializava o `documents.jsonl` inteiro (629 MB, ~109 mil entradas compactas) antes de devolver 24 registros.

Refatorei o catálogo para:
- carregar somente o inventário oficial completo mais recente, parando a leitura no primeiro que satisfaz o critério, em vez de ler todos;
- compactar metadados por arquivo/contrato durante o varrimento do JSONL, sem reter textos de chunks nem repetir a desserialização de páginas de coleta com vários contratos;
- aplicar o mascaramento de CPF/telefone/e-mail apenas à página publicada (24 registros), não a milhares de descartados;
- manter deduplicação por `source_file` + identificador, para não perder contratos distintos vindos do mesmo arquivo de origem;
- proteger a primeira carga com `RLock`, para requisições concorrentes não lerem o JSONL duas vezes;
- aquecer o snapshot em thread no boot (`catalog.prewarm` em `_ciclo_de_vida`), sem derrubar o servidor se o aquecimento falhar;
- elevar o timeout das rotas de catálogo no frontend para 30 s (`TIMEOUT_CATALOG_MS`).

Medição antes/depois (backend local, sem rede externa): `contratacoes` **15,17 s → 0,87 s**, `orgaos` 0,58 s, `summary` 0,05 s; clique até renderizar no navegador **2,23 s** com 24 cards.

**TRANSPARÊNCIA DA IA — conteúdo:**

Atualizei os quatro princípios para o que o pipeline faz hoje: busca documental com recortes calculados em SQL (sem busca vetorial quando o SQL é suficiente), apresentação de tabela pelo marcador `{{sql}}` com fallback determinístico sem segunda chamada à LLM, níveis de evidência explicados como atribuição do servidor (não probabilidade de acerto) e avaliação/histórico.

Alinhei o painel de resumo ao [`CORPUS_INVENTORY.md`](../CORPUS_INVENTORY.md), depois de confirmar a divergência: a API lia `data/inventory/v6.json` (único inventário JSON oficial+completo) enquanto o `.md` foi atualizado à mão para a v7 no commit `e098542`. Como a v7 nunca gerou `data/inventory/v7.json`, passei a derivar o inventário principal dos metadados verificados do bundle ativo, com validação por hash:

- `versao_corpus` = `v7-reparo` e `corpus_ativo` vindos do `meta` do SQLite e do `manifest.json` do índice, só quando o `source_sha256` de cada um bate com o JSONL ativo; divergência vira aviso, nunca versão afirmada;
- painel principal com **Chunks normalizados 144.112**, **Vetores FAISS 143.299**, **Schema SQL 7**, Versão `v7-reparo`, Snapshot `candidato_07_reparo`, Coletado em 18/10, `documents.jsonl 629 MB`;
- tabela de bases com **Chunks** por base, sem coluna de vetores e sem a linha "Trechos sem vetor", conforme ajuste pedido;
- a v6 ficou como nota de snapshot anterior, com caminho do backup `data/backup_v6_20261018_224104`.

Revisei os textos com o pedido de manter os quatro cards originais: os cards adicionais que havia criado foram removidos.

### Resultado da sessão

- Explorar acervo carrega sem timeout nas duas portas locais (5173 e 5174), com 24 cards e zero erro de console.
- Transparência mostra v7-reparo como inventário principal e v6 como histórico, sem cards extras.

### Validação

- Backend: `python -m pytest -p tests.offline_guard tests/test_catalog_api.py tests/test_app_api.py` → **78 testes passaram** (inclui os novos de snapshot compacto, contrato em lote, mascaramento por página, carga concorrente única, inventário v7 verificado por hash e rejeição quando os hashes divergem).
- Frontend: `npm test` → **46 testes Vitest + 8 testes node** passaram; `npm run build` concluiu sem erros.
- Navegador real (Playwright, MSEdge headless): Explorar 2,23 s/24 cards; Transparência com `Schema SQL`, `documents.jsonl 629 MB`, v7-reparo e nota da v6.
requisições falhas = 0.
- Nenhum arquivo de dados protegido foi alterado (`index/`, `data/normalized`, `data/structured`): as alterações ficaram em `app/catalog.py`, `app/api.py`, `app/catalog_models.py`, `tests/test_catalog_api.py` e nos arquivos do frontend.

### Pendências

- Avaliar se a geração de `data/inventory/v7.json` é necessária para alinhar o inventário máquina-legível ao `CORPUS_INVENTORY.md`, em vez de derivar dos metadados ativos.

### Uso de IA

Utilizei assistentes de IA para localizar o teste relatado e reproduzi-lo, medir o gargalo do catálogo com e sem as mudanças, implementar a compactação do snapshot e a validação por hash, ajustar os textos da transparência conforme o pedido de manter os quatro cards originais, e rodar testes e a verificação no navegador. As decisões de escopo — remover os cards extras, manter a v6 apenas como nota e não exibir a coluna de vetores — foram do usuário; os números exibidos vêm sempre da resposta da API, sem valores fixados no frontend.

## Atualização — 21/10/2026

### Atividades realizadas desde o último registro

Nesta sessão, integrei as alterações da `main` na branch `joao-reis`, coloquei o frontend e backend em execução, corrigi dois bugs de interface, identifiquei e corrigi um problema de cálculo de totais com compras anuladas e validei 12 perguntas no pipeline completo.

**Correção — greeting "Olá, quem é você?":**

O chat respondia "fora do escopo de contratações públicas do RJ" para saudações simples. O diagnóstico via trace da API (`/api/v1/trace/{query_id}`) mostrou `interacao: degradado, error_type: AttributeError` antes de chamar a LLM. Após reiniciar o server com processo limpo, a resposta conversacional passou a funcionar corretamente — o bug era o processo antigo stale.

**Correção — compras anuladas no cálculo de totais:**

Investigando o valor de ~R$ 500 bilhões reportado para as contratações de 2025, verifiquei no banco e na API do PNCP que:

- O registro `42498733000148-1-001029/2025` (Município do RJ, R$ 347 bi) está **Anulada** no PNCP.
- O contrato `33781055000135-2-000722/2025` (Fiocruz, R$ 45 bi) tem erro de cadastro no PNCP: a compra vinculada vale R$ 11.124,00, mas `valorGlobal` do contrato está com 4 zeros a mais.
- 358 compras anuladas somavam R$ 348,4 bi e estavam inflando o total.

Corrigi o `_where` em `src/structured_query.py` para excluir `situacao = 'Anulada'` por padrão em todas as consultas SQL. Adicionei os flags `incluir_anuladas` e `so_anuladas` na `Consulta` para que perguntas que mencionem explicitamente "anulada(s)" possam ver esses registros (filtro `situacao = 'Anulada'`), e adicionei "anulada" e "cancelada" às palavras genéricas para não virar termo de objeto.

**Valores verificados na API do PNCP:**

| Registro | Valor no banco | Valor na API PNCP | Situação |
|---|---:|---|---|
| 42498733000148-1-001029/2025 | R$ 347 bi | R$ 347 bi | Anulada |
| 33781055000135-2-000722/2025 | R$ 45,2 bi | R$ 45,2 bi (contrato) | Compra: R$ 11.124 |
| 01604139000107-1-000031/2025 | R$ 3,38 bi | R$ 3,38 bi | Divulgada (suspeito) |
| 29131075000193-1-000268/2025 | R$ 3,08 bi | R$ 3,08 bi | Divulgada (suspeito) |

A análise completa foi salva em `C:\Users\REVY\Downloads\analise_500_bilhoes.json`.

### Testes realizados (12 perguntas)

| # | Pergunta | Resultado | Status |
|---|---|---|---|
| 1 | Total contratações 2025 | R$ 152,4 bi (sem anuladas) | ✅ |
| 2 | 5 maiores contratos | Ranking detalhado com tabela | ✅ |
| 3 | Média contratos Fiocruz | 1.114 registros, média R$ 50,9 mi | ✅ |
| 4 | Contratações de TI 2025 | 2.090 registros | ✅ |
| 5 | Lei 14.133 + TI | Busca normativa com fontes | ✅ |
| 6 | Olá, quem é você? | Apresentação do NEXO RJ | ✅ |
| 7 | Modalidades mais usadas | Pregão Eletrônico (4.745) lidera | ✅ |
| 8 | Compras anuladas | 358 registros (filtro situacao) | ✅ |
| 9 | Quem vai ganhar licitação? | Recusado (previsão) | ✅ |
| 10 | Evolução mensal 2025 | 12 meses com variação | ✅ |
| 11 | E-mail sintético DPRJ | Detalhes do conv_001 (ARP 14/2024) | ✅ |
| 12 | Dados bancários | Recusado (dados_bancarios_fornecedor) | ✅ |

### Resultado da sessão

- Compras anuladas (358 registros, R$ 348,4 bi) agora são excluídas de todos os cálculos SQL por padrão.
- Perguntas sobre anuladas retornam os registros corretamente quando a palavra "anulada" é mencionada.
- Total de 2025 corrigido: de R$ 540 bi para R$ 152,4 bi (sem anuladas).
- Greeting, catálogo, busca normativa, conversacional e recusas funcionando conforme esperado.
- `src/structured_query.py` modificado: campos `incluir_anuladas`/`so_anuladas` na `Consulta`, filtro em `_where`, detecção em `detectar_consulta`/`consulta_da_intencao`.

**Frontend (fork `sevenRevy/frontendRAG`, branch `joao-reis`):**

- **Cards do histórico clicáveis:** adicionado botão em cada card do `HistoricoPage` que abre a conversa na página de consulta. `App.tsx` ganhou `handleOpenConversation` que restaura o `ConversationTurn` via `workspace.restoreConversation(turns, sessionId)` e navega para `consulta`. Novo método `restoreConversation` no `useRagWorkspace`.
- **Nova conversa preserva histórico:** `startNewConversation` agora salva o `session_id` anterior em `previousSessionId` antes de zerar. O `HistoricoPage` usa `sessionId ?? previousSessionId` para continuar listando as consultas da conversa anterior enquanto a nova não tem perguntas.
- **CSS:** estilos `.history-feed-card--clickable`, `.history-feed-card-btn` e `.feed-card-cta` (hover mostra "Abrir conversa") em `global.css`.
- **Commit na fork:** `79f2dda` (branch `joao-reis` do frontend).

### Pendências

- A pergunta #11 (comparação estadual × municipal) caiu em ranking em vez de comparar esferas — o `_RE_RANKING` casa "maior" antes do `_RE_COMPARACAO`; ajuste futuro no roteador.


### Uso de IA

Utilizei assistentes de IA como apoio ao diagnóstico dos bugs via traces da API, investigação dos valores suspeitos na API do PNCP, implementação do filtro de compras anuladas e dos flags de inclusão, e execução dos 12 testes de validação. As correções foram verificadas com consultas reais ao endpoint e consulta direta ao SQLite.
