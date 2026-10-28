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


## Encontro 7 — 28/09/2026

Falta justificada via email.

## Encontro 8 — 30/09/2026

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

## Encontro 9 — 02/10/2026

Contexto: tarefa de hoje era atualizar o ARQUITETURA.md a partir de um checklist de revisão que comparava o documento, ainda descrevendo o estado de 28/09, sem rerank, sem SQL, índice v1  com o código e os dados reais do main.

## O que fiz:

Antes de aplicar qualquer correção do checklist, fui no código conferir cada ponto. Três afirmações do primeiro checklist não batiam com o que eu via no generate.py e no config.py (GENERATION_TIMEOUT, a conversão de recusa indevida, os números da ablação do rerank) — em vez de forçar isso no documento, suspeitei que meu main local estivesse desatualizado. Dei git fetch + git log main..origin/main e confirmei: 28 commits atrás, incluindo exatamente os três pontos que faltavam (6b60b6d, db851dd). Dei merge do main na minha branch antes de continuar.

Reescrevi o ARQUITETURA.md: pipeline de 7 pra 9 estágios (entrou consulta_sql e rerank deixou de ser "opcional, sem modelo ativo"), diagrama mermaid refeito com o branch de consulta_sql e o loop de nova busca sem filtros, descrições de cada estágio atualizadas, estado atual, corpus/índice v6 (1.400 arquivos, 5.621 vetores) e pendências reescritos do zero.

Recebi uma segunda revisão, mais detalhada, apontando a ordem real de execução (rerank roda antes de consulta_sql no pipeline.py, não em paralelo como eu tinha desenhado), dois erros factuais sobre o .gitignore/.gitattributes (data/app_state/ não é versionado à força — guarda o histórico de pergunta dos usuários; o -text preserva os bytes exatos pros hashes de proveniência, não é "CRLF conforme o caso") e pendências que faltavam (os erros de conteúdo do golden set por caso específico — P2.1, P4.2, P4.3, P5.3, F1 sem fonte — e o ajuste de filtros da Pâmela ainda numa branch separada).

Fui conferir cada ponto antes de aceitar: li structured_query.py (detectar_consulta, pra confirmar que consulta_sql só roda em pergunta de conta e também consulta pca_planos), query_analysis.py (confirmar que todo filtro é marcado opcional, sem exceção), pipeline.py (confirmar a ordem real das chamadas) e o registro_execucao_v6.md, que bateu palavra por palavra com os erros do golden set apontados na revisão.

Dois pontos eu não conseguia confirmar sem consultar o banco direto: a contagem exata de contratos com valor zero/simbólico e o registro único de "ambulância tipo D". Usei o CLI que já existe (py -m src.structured_db sql) e confirmei: 1 registro de ambulância tipo D, 21 contratos de R$ 0,00, 6 de R$ 0,01 — e descobri, nessa checagem, que esses valores simbólicos ainda entram sem filtro nas contas de média/mediana, uma pendência nova que nem o checklist tinha listado.

Rodei pytest -q pra confirmar o número real de testes: 793, que não batia nem com o que eu tinha escrito antes (783) nem com o que a segunda revisão apontava (799).

Fiz os commits e pushes de cada rodada de correção na branch marco/feedback-historico.

Uso de IA: usei o Claude pra comparar o documento com o código a cada rodada, rodar os diffs de git e redigir o texto de cada seção reescrita. Rodei os comandos, confirmei os resultados e decidi quando um ponto ficava "aceito" ou "sinalizado sem fonte" fui eu; os commits e pushes também.


## Encontro 10 — 05/10/2026

Contexto: a apresentação de hoje foi pro Felipe Rafael e, depois do feedback dele, o grupo passou a trabalhar numa lista de 16 correções (P0, P1 e P2) feita em cima do main no commit 146ceea, que inclui a rastreabilidade do fluxo de dados (onde a pergunta consulta a base SQL e a vetorial, com input e output reais de cada etapa) como ponto mais urgente. Fiquei com o item 9: erro de provedor aparecendo como "sem evidência".

O que fiz:


Entendi o problema do item 9 lendo o código: em generate.py, tanto a falha da chamada ao LLM (ECOA e fallback do OpenRouter, via llm_ecoa.completar_chat) quanto um JSON inválido do modelo viravam recusa com motivo SEM_EVIDENCIA. O usuário lia "não encontrei evidência" com o serviço fora do ar, e o avaliador contava como acerto uma recusa esperada causada por falha de rede.

A correção: criei o motivo ERRO_PROVEDOR em src/schema.py e fiz os dois caminhos de generate.py devolverem esse motivo, com a mensagem "tente novamente em instantes"; o log da etapa geracao registra "recusa: erro_provedor" junto com o erro original. Decidi que só o código pode marcar esse motivo: ele fica fora do schema JSON enviado ao LLM, se o modelo devolver esse valor em texto livre ele vira sem_evidencia, e ele fica fora das recusas convertíveis (_RECUSAS_CONVERTIVEIS), pra uma falha de infraestrutura nunca virar resposta.

No avaliador (eval/run_eval.py), erro_provedor nunca conta como acerto, nem de uma recusa esperada sem motivo definido; conteúdo e citação ficam como não avaliados, e o relatório ganhou o contador de falhas do provedor e o status ERRO_PROVEDOR. Escrevi 5 testes novos em tests/test_generate.py e 2 em tests/test_eval.py, e corrigi os 2 testes antigos que esperavam sem_evidencia nesses casos. Mantive o HTTP 200 na API (is_refusal=true com o motivo novo) pra não quebrar histórico e feedback; trocar por 503 fica como decisão do time.

Uso de IA: usei o Claude pra revisar o generate.py e o llm_ecoa.py atualizados.

## Encontro 11 — 07/10/2026

Contexto: o grupo seguiu com a lista de 16 correções do documento Correcoes_Grupo04_2026-10-05. Fiquei primeiro com o item 11, contexto entre perguntas, cujo detalhamento estava no documento enviado no grupo na segunda. Depois que concluí e o merge foi feito, recebi o item 13, proveniência da base sintética.

O que fiz:

Item 11 (concluído): o problema é que o QueryRequest só recebia query e top_k, então uma pergunta como "considerando os mesmos contratos…" (P4.3 do golden set) chegava ao pipeline sem as anteriores, e o backend não tem memória entre perguntas. Escolhi reescrever a pergunta antes da análise, com o cliente enviando as últimas trocas (campo historico), em vez de o backend guardar a conversa.

Criei o src/reformulacao.py: um filtro determinístico (parece_continuacao) decide se a pergunta depende do que veio antes, então perguntas que já se sustentam sozinhas não geram chamada ao LLM; só as últimas 3 trocas entram no prompt, com CPF, telefone e e-mail pessoal mascarados e o histórico tratado como dado, não como instrução; se o modelo falhar ou devolver fora do formato, segue com a pergunta original, e REFORMULACAO_MODE=regras desliga a etapa. A pergunta reescrita substitui a original em todas as etapas seguintes, então os guardrails e a política de sensibilidade valem sobre o texto que de fato é consultado.

Na API (app/api.py), o QueryRequest ganhou historico (até 10 trocas) e a resposta ganhou pergunta_reformulada, para o frontend poder mostrar o que foi consultado. A etapa nova aparece no log como reformulacao, e atualizei o FRONTEND_API_CONTRACT.md, o .env.example e o ARQUITETURA.md. Escrevi testes novos (reformulação, API e pipeline, inclusive guardrail sobre a pergunta reescrita) e a suíte completa passou com 1176 testes. O commit é o ea66a22, na branch marco/contexto-entre-perguntas.

O que falta do item 11 é no frontend: a Eduarda precisa enviar o historico e exibir a pergunta_reformulada.

Item 13 (em andamento): comecei hoje, mas ainda não terminei. O escopo é corrigir a proveniência em quatro pontos: o campo fonte e o campo licenca do corpus_manifest.yaml (linhas 315 e 323), a linha do CORPUS_INVENTORY.md que diz "conversacional_sintetica.zip fornecido pelo grupo" (linha 33) e o docs/emails_sinteticos.md, que diz só "arquivo fornecido", sem autor. Em todos eles o registro deve passar a dizer que a base foi gerada pelo tutor e integrada ao projeto pelo João, com a data de entrega e as condições de uso combinadas com o tutor.

Uso de IA: usei o Claude pra implementar os testes a partir da decisão que tomei (reescrever a pergunta, com o cliente mandando o histórico), e pra ler o item 13 e levantar quais arquivos precisam mudar. fiz o commit e o push;

## Encontro 12 — 09/10/2026

Contexto: terminei o item 13 da lista de correções (documento Correcoes_Grupo04_2026-10-05), proveniência da base sintética, que tinha começado no encontro anterior. O repositório dizia que a base sintética conversacional foi "fornecida pelo grupo", mas ela foi gerada pelo tutor e integrada ao projeto pelo João (commits d31925f e 146ceea).

O que fiz:

Atualizei a main, criei a branch marco/proveniencia-base-sintetica e corrigi o registro de autoria em seis arquivos: o campo fonte e o campo licenca da base emails_sinteticos no corpus_manifest.yaml, a linha de Fonte do CORPUS_INVENTORY.md, o docs/emails_sinteticos.md (ganhou um item de Autoria com a data e os dois commits, e "arquivo fornecido" virou "arquivo recebido do tutor"), o docstring de src/synthetic_conversations.py, que também dizia "fornecidas pelo grupo", e, depois do retorno do grupo, a tabela de bases do README.md e uma frase do ARQUITETURA.md, que tinham o mesmo erro.

A data de entrega (05/10/2026) vem do registro do João no acompanhamento dele, e as condições de uso foram combinadas com o tutor e passadas pelo grupo: a base é usada na apresentação, pode ficar no repositório, não tem licença nem restrição e não precisa citar autoria. Registrei isso no campo licenca do corpus_manifest.yaml e no docs/emails_sinteticos.md. Rodei a suíte (pytest -m "not llm") e passaram 1261 testes. O commit principal é o 1a05b92, com push da branch para revisão do grupo no PR.

Situação dos meus itens da lista de correções: o item 9 (erro de provedor aparecendo como "sem evidência") está concluído e mergeado na main; o item 11 (contexto entre perguntas) está concluído e mergeado, com o commit ea66a22; o item 13 (proveniência da base sintética) está concluído, com o commit 1a05b92 na branch marco/proveniencia-base-sintetica, aguardando revisão no PR.

Uso de IA: usei o Claude pra localizar as ocorrências de "fornecida pelo grupo" no repositório e aplicar as edições nos arquivos. Fiz o commit e o push.

## Encontro 13 — 14/10/2026

Contexto: com a lista de pendências até o Demo Day (23/10), escolhi o item 8 da parte de qualidade, a falha da pergunta F1 do golden set. A pergunta compara os valores de contratação entre órgãos estaduais e municipais, e a resposta saía com confiança alta, mesmo tendo só um registro municipal de R$ 0,00 como base.

O que fiz:

Atualizei a main, criei a branch marco/f1-termo-houver e rodei o F1 pra reproduzir. Achei duas causas. A primeira: o "houver" de "se não houver dados suficientes" entrava como termo do objeto em src/structured_query.py e virava filtro no SQL. Resolvi colocando as formas do verbo haver (houv, haver, havia, haven, haja) nos radicais genéricos, com testes novos pra "se houver", "havendo", "haja" e outras flexões. Isso fez o F1 deixar de filtrar por "houver", mas ele ainda falhava: a consulta caía no atalho do SQL e respondia com confiança alta, comparando uma esfera que só tinha um valor zerado.

Como isso era uma decisão de regra, decidi que: quando a pergunta compara esferas e menos de duas têm registros com valor maior ou igual a R$ 1,00, a resposta deve dizer que os dados são insuficientes pra comparar, com confiança baixa. Implementei com uma flag comparacao_esferas na Consulta, um tipo novo de resultado (comparacao_insuficiente) no schema.py, a verificação em structured_query.py e o tratamento no generate.py, pra confiança baixa valer também quando a resposta vem do LLM. Escrevi testes pros dois lados (dados insuficientes e dados suficientes) e pra uma pergunta que não é comparação. A suíte completa passou com 1607 testes e o F1 passou (OK, run 20261014T181928Z). O conserto do "houver" foi o commit 446bcb7, e a regra de comparação foi o 64404cd, os dois na branch marco/f1-termo-houver.

Só o 446bcb7 entrou na main (PR #41), porque o merge do PR foi feito antes do push do 64404cd. Quando o Renato rodou a F1 com o LLM real na main, ela continuou falhando, com confiança alta e sem nenhuma frase de insuficiência, e o feedback dele descreveu o mesmo problema que a regra de comparação resolve. Ele também pediu que a correção valesse pra qualquer comparação com um grupo vazio, e não só pra F1, com teste numa base montada no próprio teste, e que eu combinasse com o João antes, porque a mudança mexe na regra de confiança do recorte que ele escreveu. Atualizei a branch com a main (merge sem conflito), a suíte completa passou com 1662 testes e a F1 passou de novo (OK, run 20261014T204357Z). Abri um segundo PR só com o 64404cd, pro Renato revisar e fazer o merge.

Depois do merge, o Diogo roda o golden set completo (item 4) com essa correção.

Uso de IA: usei o Claude pra investigar a causa do F1 e propor uma correção. Rodei a suíte e o F1, fiz os commits e os pushes e abri o PR.

## Encontro 14 — 19/10/2026

Contexto: recebi o item 26 da lista de pendências (P6.3), que dizia que a LC 123 não chega ao prompt na pergunta que mistura norma e contratações. O item veio só com essa frase, sem run id, relatório nem diagnóstico, e ninguém do grupo tinha o run em que a falha apareceu.

O que fiz:

Li o caminho que a pergunta percorre (src/router.py, src/pipeline.py, src/retrieve.py e src/generate.py) e rodei a P6.3 com o pipeline completo (py .\eval\run_eval.py run --pipeline eval.adapters:pipeline_completo --ids P6.3), na main atual. A P6.3 passou (OK, run 20261019T192712Z, 404 s). Depois li o log da execução, estágio por estágio, pra ver o que de fato chegava ao prompt. O roteador mandou a pergunta pras bases normativa e estruturada, a busca vetorial devolveu só chunks da base normativa, e a parte de contratações veio pelo SQL, num chunk separado. No rerank ficaram 10 chunks normativos e só 3 couberam no prompt, mais o chunk do SQL: a janela de contexto é de 3500 caracteres (GENERATION_CONTEXT_CHARS no meu .env, modo local), então 7 dos 10 ficaram de fora. Os 3 que entraram são da LC 123: o art. 48 (licitação exclusiva até R$ 80.000, subcontratação e cota de 25%), o art. 44 (desempate) e o art. 74 (Juizado Especial, que não tem relação com licitação).

Minha primeira hipótese estava errada: achei que o documento principal da base normativa fosse a Lei 14.133 e que a LC 123 ficaria de fora por causa do orçamento do prompt. O texto do prompt mostrou que esse documento é a própria LC 123, então ela chega. Como o item não se reproduz, não alterei código. Anotei duas melhorias possíveis, que ainda não são um item: o art. 74 ocupa espaço no prompt sem ser relevante, e o art. 48 é cortado em 900 caracteres, no meio da cota de 25%.


## Encontro 15 — 21/10/2026

Contexto: dia da prévia da apresentação do Demo Day, que é oficial na sexta, 23/10, às 14h. O código está congelado até lá, então hoje não alterei nada nele. A orientação oficial pede 15 minutos (no máximo 20), que todos os integrantes apresentem pelo menos uma parte e que a apresentação siga cinco partes: problema e motivação, dados utilizados, arquitetura e funcionamento, apresentação do sistema e demonstração prática, com exemplos reais e também casos de falha e limitações.

O que fiz:

Li o deck do grupo (NEXO RJ, 16 slides) e o guia de estudos da defesa (versão 4.1, de 21/10, sobre a main depois do PR #65) e comparei com a orientação. O deck cobre as cinco partes e ainda reserva um minuto para resultados e limitações, somando 15 minutos, e a demonstração já mostra falhas e limites (aviso de pagamento, zero de 2024 com a ressalva do recorte, recusa de dado sensível e uma empresa fora do acervo). Estudei a arquitetura pelo guia: o caminho da pergunta (guardrails, análise por regras, roteador, SQL direto ou busca híbrida com rerank e LLM, validação e trace), onde fica cada etapa no código e as perguntas prováveis da banca.

A divisão das falas, proposta pelo Renato, ficou assim: Eduarda abre e conduz como host, depois Renato (dados), Diogo (arquitetura), eu (o sistema), João (demonstração, por ser quem opera o sistema) e Pâmela fecha com resultados e limitações. A minha parte são os slides 11 e 12, para quem é o NEXO RJ e como o usuário chega à resposta, com destaque para a conversa ("E em 2024?"), que depende da reformulação da pergunta (item 11). 


## Encontro 16 — 26/10/2026

**Contexto:** Pendência 3 do pós-Demo Day, variedade na busca: quando a pergunta cai nas atas, muitos trechos da mesma compra acabavam ocupando sozinhos as primeiras posições, e a resposta ficava baseada em uma compra só.

**O que fiz:** Criei a regra no domínio (`limitar_repeticoes`, em `src/domain/fusion.py`) e a apliquei no `_fundir` do `retrieve.py`, depois do RRF e antes do corte e do rerank. No máximo 2 trechos por compra ficam à frente, e os demais vão para o fim da lista, sem serem descartados, então o recall não cai. A regra só vale para atas (a compra é identificada por `numero_controle_compra`) e não atua quando a pergunta cita número do PNCP ou de contrato, porque aí a pessoa quer justamente aquele registro. Dá para ajustar ou desligar por `RETRIEVAL_MAX_PER_PURCHASE` (padrão 2, 0 desliga). Escrevi testes unitários e de integração e um script de medição, `eval/medir_variedade.py`, que compara a mesma busca com a regra desligada e ligada.

Nas 9 perguntas de ata do gabarito, as compras distintas no top-10 foram de 4,67 para 6,44, com a posição do primeiro trecho relevante inalterada, e o hit@10 nas 47 perguntas ficou em 0,9149, igual com e sem a regra. Os 5 casos do golden que usam atas (P1.2, A1, A2, I2, E1) passam com a regra ligada. O golden completo não deu para validar na minha máquina, porque a base estruturada oficial não está nela (a que consegui restaurar do histórico do Git tem 810 registros e é mais antiga que a esperada pelo gabarito), então isso ficou pendente para alguém com a base oficial rodar na branch. Abri o PR #77 para o Renato revisar. O CI tem 1 falha que já existe na main, o teste do router com "Cristália", que falha igual sem a minha alteração (confirmei com `git stash`).

Um problema meu no caminho: uma cópia local do `documents.jsonl` do v6 (19 MB, usada nas medições) entrou no PR por engano. Reescrevi o commit para tirá-la, adicionei `data/v6_docs/` ao `.gitignore` e conferi pelo diff que o PR ficou só com código, testes e o script. Fica de aprendizado conferir o `git status --short` antes de commitar quando há pastas de dados novas.

**Uso de IA:** Utilizei o claude para diagnosticar a falha do golden (base estruturada local desatualizada).


## Encontro 17 — 28/10/2026

**Contexto:** Nova atividade da residência: gravar um vídeo de até 5 minutos apresentando o projeto (problema, solução, tecnologias e uma demonstração do sistema rodando) e preparar um repositório individual e público no GitHub, com README claro o bastante para qualquer pessoa instalar e executar o sistema. O vídeo deve ser publicado no YouTube e linkado no README, que também deve citar os usernames dos colegas que contribuíram.

**O que fiz:** Comecei verificando o estado do Git na minha máquina: conferi a branch, que estava limpa e igual ao remoto, e voltei para a main atualizada antes de planejar o repositório individual. Revisei o README e a estrutura do projeto pensando em quem chega de fora, e identifiquei três pontos que precisam ficar explícitos: os dados da V7 (5,3 GB) ficam fora do Git e são baixados do Drive do grupo, o sistema depende de um provedor de LLM configurado no `.env`, e a interface fica em um repositório separado. Também listei o que deve sair da versão individual (arquivos de acompanhamento, logs e dados pesados de `eval/runs`) e decidi comparar duas opções, fork com histórico completo ou repositório novo com histórico limpo e créditos no README, depois de medir o tamanho do repositório.

Para o vídeo, estruturei o roteiro em 10 slides, com tempo por slide e o texto de fala nas notas: abertura, o problema do PNCP, o que é RAG explicado do zero, as quatro naturezas de conhecimento e o roteamento, o caminho de uma pergunta (análise, busca híbrida, rerank, geração, validação e o atalho SQL), confiança e proteção de dados, tecnologias, resultados com a minha parte (regra de comparação entre esferas, investigação do caso da LC 123 e a variedade na busca), a demonstração com três perguntas e os créditos do Grupo 04. O plano é gravar a tela com áudio, apresentando os slides e fechando com o sistema rodando na minha máquina com a base V7. Antes da gravação ainda preciso baixar a V7, subir a API com tempo de aquecimento, testar as três perguntas da demonstração, preencher o link do repositório e confirmar os usernames dos colegas.


