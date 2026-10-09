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
