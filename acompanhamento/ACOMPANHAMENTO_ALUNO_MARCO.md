# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Relato individual - Marco Aurélio

- Participei da discussão sobre qual dos três cenários escolher e defendi o Cenário 1 (Contratações Públicas do RJ) com base em três pontos: a verificabilidade das respostas, já que qualquer pessoa confere o resultado direto no PNCP, o que facilita a defesa técnica

### Encontro 2 - 2026/09/16

### Relato individual - Marco Aurélio
## Encontro 2 - 2026-09-16

### Relato individual - Marco Aurélio

- Revisei o `ARQUITETURA.md` contra o checklist do desafio e identifiquei que a seção de pipeline estava vazia e a divisão de responsabilidades ainda dizia "colaborativo, a definir".
- Resolvi um conflito de merge no `ARQUITETURA.md` (divergência trivial de formatação na seção de Stages) ao sincronizar com o que o time subiu em paralelo.
- Criei a branch `marco/schema` e implementei `src/schema.py` — os modelos Pydantic do pipeline (`ChunkMetadata`, `RouteDecision`, `RAGResponse`, e os enums de sensibilidade/motivo de recusa), estendendo o padrão de validação que já tinha usado no VendeFácil pra Cenário 1. Escrevi `tests/test_schema.py` com 7 testes de contrato (metadado obrigatório de chunk, consistência de recusa em `RAGResponse`) — todos passando via `pytest`.

Encontro 3 — 18/09/2026

Contexto: Renato (coordenador) me passou a responsabilidade pelo módulo fetch_corpus.py. Fiquei então com dois módulos: schema.py (já mergeado) e fetch_corpus.py.

O que fiz:

Planejei o fetch_corpus.py a partir do contrato da Parte II do desafio: um único script com três subcomandos (fetch, update, inventory), lendo corpus_manifest.yaml.
Implementei versionamento imutável (cada coleta grava em data/raw/v{n}/<base_id>/, nunca sobrescrevendo uma versão já concluída) e resumibilidade via checkpoint, pra garantir que interromper uma coleta no meio não duplica nem corrompe dados.
Implementei logging estruturado de falhas (JSONL em logs/) que não aborta a coleta das outras bases quando uma falha.
Decidi implementar o filtro local do manifesto (ex. UF == RJ) sem eval()/exec() — um mini-interpretador restrito a campo+operador+valor, pra uma resposta da API nunca poder injetar código. 
Atualizei o corpus_manifest.yaml com os parâmetros que já tínhamos fechado em conversas anteriores (contratos_estruturado via /v1/contratos, pca_agregado via /v1/pca com o código CATMAT 8920, editais_normativo com a Lei 14.133).
Criei data/config/cnpjs_rj.json como stub documentado: a API do PCA não tem campo de UF, então a única forma de restringir ao RJ é uma lista fixa de CNPJs, ainda pendente de preenchimento pelo time. Deixei isso registrado como TODO em vez de travar o script.
Escrevi tests/test_fetch_corpus.py (12 testes cobrindo filtro, versionamento e checkpoint) e rodei junto com os 7 de schema.py — 19 passando.

Fiz commit, push e abri o PR. Renato revisou e mergeou no main.

Uso de IA: usei o Claude para montar o esqueleto inicial do fetch_corpus.py e para revisar o design do filtro local sem eval(). Também usei para diagnosticar os erros topologia da branch, mas quem rodou os testes, confirmou os resultados, validou a estrutura final do corpus_manifest.yaml (com base na pesquisa que eu já tinha feito nas APIs do PNCP e no CATMAT em conversas anteriores) e fez o commit/push/PR fui eu.