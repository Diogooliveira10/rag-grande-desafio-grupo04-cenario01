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