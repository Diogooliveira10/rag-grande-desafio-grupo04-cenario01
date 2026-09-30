# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Relato individual - Marco Aurélio

- Participei da discussão sobre qual dos três cenários escolher e defendi o Cenário 1 (Contratações Públicas do RJ) com base em três pontos: a verificabilidade das respostas, já que qualquer pessoa confere o resultado direto no PNCP, o que facilita a defesa técnica

## Encontro 2 - 2026-09-16

### Relato individual - Marco Aurélio

- Revisei o `ARQUITETURA.md` contra o checklist do desafio e identifiquei que a seção de pipeline estava vazia e a divisão de responsabilidades ainda dizia "colaborativo, a definir".
- Resolvi um conflito de merge no `ARQUITETURA.md` (divergência trivial de formatação na seção de Stages) ao sincronizar com o que o time subiu em paralelo.
- Criei a branch `marco/schema` e implementei `src/schema.py` — os modelos Pydantic do pipeline (`ChunkMetadata`, `RouteDecision`, `RAGResponse`, e os enums de sensibilidade/motivo de recusa), estendendo o padrão de validação que já tinha usado no VendeFácil pra Cenário 1. Escrevi `tests/test_schema.py` com 7 testes de contrato (metadado obrigatório de chunk, consistência de recusa em `RAGResponse`) — todos passando via `pytest`.

## Encontro 3 — 18/09/2026

Contexto: Renato (coordenador) me passou a responsabilidade pelo módulo `fetch_corpus.py`. Fiquei então com dois módulos: `schema.py` (já mergeado) e `fetch_corpus.py`.

O que fiz:

Planejei o `fetch_corpus.py` a partir do contrato da Parte II do desafio: um único script com três subcomandos (`fetch`, `update`, `inventory`), lendo `corpus_manifest.yaml`.
Implementei versionamento imutável (cada coleta grava em `data/raw/v{n}/<base_id>/`, nunca sobrescrevendo uma versão já concluída) e resumibilidade via checkpoint, pra garantir que interromper uma coleta no meio não duplica nem corrompe dados.
Implementei logging estruturado de falhas (JSONL em `logs/`) que não aborta a coleta das outras bases quando uma falha.
Decidi implementar o filtro local do manifesto (ex. `UF == RJ`) sem `eval()`/`exec()` — um mini-interpretador restrito a campo+operador+valor, pra uma resposta da API nunca poder injetar código. 
Atualizei o `corpus_manifest.yaml` com os parâmetros que já tínhamos fechado em conversas anteriores (`contratos_estruturado` via `/v1/contratos`, `pca_agregado` via `/v1/pca` com o código CATMAT 8920, `editais_normativo` com a Lei 14.133).
Criei `data/config/cnpjs_rj.json` como stub documentado: a API do PCA não tem campo de UF, então a única forma de restringir ao RJ é uma lista fixa de CNPJs, ainda pendente de preenchimento pelo time. Deixei isso registrado como TODO em vez de travar o script.
Escrevi `tests/test_fetch_corpus.py` (12 testes cobrindo filtro, versionamento e checkpoint) e rodei junto com os 7 de `schema.py` — 19 passando.

Fiz commit, push e abri o PR. Renato revisou e mergeou no main.

Uso de IA: usei o Claude para montar o esqueleto inicial do `fetch_corpus.py` e para revisar o design do filtro local sem `eval()`. Também usei para diagnosticar os erros topologia da branch, mas quem rodou os testes, confirmou os resultados, validou a estrutura final do `corpus_manifest.yaml` (com base na pesquisa que eu já tinha feito nas APIs do PNCP e no CATMAT em conversas anteriores) e fez o commit/push/PR fui eu.


## Encontro 4 — 21/09/2026

Contexto: tarefa designada pra mim hoje foi o generate.py, o estágio de geração da pipeline (recebe os chunks já recuperados/rerankeados e produz a resposta final).

O que fiz:

Defini a interface de entrada como list[Chunk] (modelo já existente em schema.py), já que retrieve.py/rerank ainda não existiam no repositorio deixei isso registrado como contrato pro time seguir.
Implementei guardrails determinísticos que rodam antes de chamar o LLM: pergunta sem nenhum chunk recuperado vira recusa automática, e um filtro por padrão de texto bloqueia pedidos óbvios de dados bancários/credenciais sem gastar uma chamada de modelo.
Implementei parsing defensivo da resposta do LLM: se ele devolver algo fora do JSON esperado, ou quebrar a consistência do contrato (ex.: dizer que é recusa mas ainda citar fontes), o módulo corrige ou cai numa recusa segura, nunca deixa uma exception subir pro resto da pipeline.
Decidi nunca confiar em metadado que o próprio LLM cite (base/arquivo de origem), só no chunk_id, usado pra buscar o metadado real nos chunks que a gente forneceu. Uma citação inventada é descartada.
Escrevi 9 testes cobrindo esses casos, todos passando, sem tocar rede (uso um cliente fake injetado).

Uso de IA: usei o Claude pra montar o esqueleto do generate.py. Rodei os testes, decidi a interface de entrada, corrigi o fluxo de branch e revisei cada ponto do design antes de subir, a decisão de nunca confiar em metadado citado pelo LLM, por exemplo, eu discuti e confirmei que fazia sentido antes de seguir.

## Encontro 5 — 23/09/2026

Contexto: tarefa designada pra mim hoje foi o test_fetch_corpus. Antes de escrever qualquer coisa, precisei esclarecer se o teste antigo (feito junto com a primeira versão do fetch_corpus.py, lá no Encontro 3) ainda valia.

O que fiz:

Chequei no repositório e confirmei que não tinha nenhum tests/test_fetch_corpus.py na main, o João Reis reescreveu o fetch_corpus.py inteiro com uma arquitetura bem diferente (armazenamento por conteúdo com dedup SHA-256, staging/checkpoint por versão, descoberta de documentos via source_catalog.py), então o teste antigo não fazia mais sentido nem existia mais.
Escrevi o tests/test_fetch_corpus.py do zero contra o código atual: 20 testes cobrindo parse do manifest, dedup por conteúdo, imutabilidade de versão promovida (não pode sobrescrever uma versão já concluída), checkpoint/resumo vinculado ao hash do manifesto, e os coletores por tipo de acesso (arquivo_local, http_pdf, rest_api).
Isolei os testes de rede/disco real usando monkeypatch nas funções request_url/download_url do source_catalog.py, em vez de mockar as classes internas.
Resolvi um ModuleNotFoundError da dependência rich que faltava no ambiente.
Rodei os 20 testes, todos passando, fiz commit e push na branch marco/test-fetch-corpus e abri o PR.

Uso de IA: usei o Claude pra montar o esqueleto dos testes e pra revisar o corpus_manifest.yaml contra o código. Quem rodou os testes, confirmou os resultados e decidiu o que citar no PR fui eu.


## Encontro 6 — 25/09/2026

Contexto: tarefa designada pra mim hoje foi o query_analysis (item 6 das pendências do time: o roteador não gerava filtros de ano, valor e órgão para o retrieve).

O que fiz:

Implementei o src/query_analysis.py: extração determinística por regex (sem LLM), seguindo o mesmo padrão do router.py/generate.py. Extrai ano, valor (mínimo/máximo/faixa, formatos monetários em BRL — "R$ 500.000,00", "1,5 milhão", "200 mil") e órgão (heurística por palavras-gatilho como "secretaria", "prefeitura", "tribunal").
Adicionei QueryFilters e QueryAnalysisResult em src/schema.py.
Pluguei a etapa no src/pipeline.py: responder() agora roda analisar_query() antes do roteamento e repassa os filtros extraídos pro retrieve().
Estendi _matches_filters em src/retrieve.py pra aceitar múltiplas condições sobre a mesma chave (faixa de valor, e "ano" virando um intervalo em data_vigencia), sem alterar o comportamento já existente.
Escrevi testes novos: tests/test_query_analysis.py, tests/test_pipeline_query_analysis.py, e 4 testes de faixa em tests/test_retrieve.py.
Durante o dia, o main do time avançou bastante em paralelo (reescrita de retrieve.py com escopos/manifesto/fetch_k pelo Diogo, novos campos em schema.py). Precisei reconciliar minha branch (marco/query-analysis) com essas mudanças antes de abrir o PR, preservando as features dos colegas.
Rodei a suíte completa localmente (322 passed, 1 failed pré-existente e sem relação com meu trabalho — falha de extração de PDF já presente no main antes do meu merge), fiz commit, push e abri o PR.

Uso de IA: usei o Claude pra desenhar a extração por regex do query_analysis, pra estender os filtros de retrieve.py em cima do código novo do Diogo sem quebrar as features dele, e pra investigar o bug de dependência (pypdf faltando no meu ambiente) que apareceu depois. Rodei os testes, decidi o formato de saída dos filtros e confirmei cada mudança antes de commitar fui eu.


## Encontro 7 — 30/09/2026

Contexto: tarefa designada pra mim hoje veio da lista de pendências levantada pelo time: feedback do usuário e histórico de consultas persistido, que a especificação exige e a API ainda não tinha (só existiam /health e /api/v1/query).

O que fiz:

Implementei src/history_store.py: histórico de consultas e feedback em SQLite local (data/app_state/, fora do Git). É estado da própria aplicação, diferente da base estruturada derivada do corpus (structured_db.py) — não pode ser apagado/remontado.
Estendi app/api.py: POST /api/v1/query agora devolve um query_id e salva a pergunta+resposta no histórico; criei POST /api/v1/feedback (avaliação positivo/negativo + comentário opcional, associado ao query_id da consulta original, 404 se ele não existir) e GET /api/v1/history?limit=N (consultas recentes com o feedback recebido).
Pluguei o feedback no log estruturado: o estágio "feedback" já existia reservado no Stage enum do obs.py mas não estava integrado a nada — agora POST /api/v1/feedback grava o evento amarrado ao mesmo run_id da consulta original.
Estendi src/pipeline.py com um parâmetro query_id opcional em responder() (mesmo padrão que já existia pra run_id), pra API gerar o id antes de chamar o pipeline e usar o mesmo pra salvar o histórico. Mudança aditiva, não quebra quem chama responder() sem esse argumento.
Escrevi tests/test_history_store.py (7 testes novos) e atualizei tests/test_app_api.py (precisei ajustar o fake_responder dos testes existentes pra aceitar os novos argumentos run_id/query_id).
Atualizei README.md e ARQUITETURA.md pra documentar os dois endpoints novos e tirar "feedback" da lista de estágios não implementados, e o .gitignore pra ignorar data/app_state/.
Rodei a suíte local antes de abrir a PR (462 passed, 2 failed pré-existentes e sem relação — os dois itens do Diogo sobre censo nacional/unidades RJ no fetch_corpus, já sinalizados na lista de pendências), fiz commit, push e abri a PR #11.

Imprevisto: depois de abrir a PR, dei git pull numa hora ruim (estando na minha branch, então só atualizou o tracking dela, não o main local) e o Renato tinha avançado bastante o main em paralelo (recoleta v6 do corpus, rerank). Reconciliei dando merge do main atualizado na minha branch (fast-forward) e confirmei com git log/git branch --contains que meu commit já estava no main — a PR só ficou marcada como "Closed" em vez de "Merged" porque o merge foi feito direto por fora do botão do GitHub. Suíte final depois da reconciliação: 556 passed.

Uso de IA: usei o Claude pra revisar a arquitetura do histórico (decidir por que um SQLite separado fazia mais sentido do que reaproveitar o structured_db.py ou o log JSONL do obs.py), na construção do esqueleto do history_store.py e dos endpoints novos. Rodei os testes, decidi o formato dos endpoints (avaliação positivo/negativo, 404 pra query_id desconhecido) e revisei cada mudança antes de commitar fui eu.