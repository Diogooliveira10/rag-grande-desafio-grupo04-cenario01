# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Relato individual - Renato Lemos

- Me voluntariei como coordenador do projeto pelo menos nessa primeira semana. Também participei da estruturação do acompanhamento das atividades e das discussões para definição e organização da arquitetura do projeto.

# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 2 - 2026-09-16

### Relato individual - Renato Lemos

-Criei o arquivo corpus_manifest inicial do projeto e o requiremts.txt, coordenei o time nas tarefas colaborativas e dividi o time para fazer as pesquisas das fontes pra usar no projeto. Fiquei responsável por fazer os merges nas branches durante o projeto.

# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 3 - 2026-09-18

### Relato individual - Renato Lemos

-Criei o arquivo README inicial do projeto e fiz as atualizações nele de acordo com o que estava sendo feito, coordenei o time nas tarefas colaborativas e dividi o time para fazer algumas tarefas pendentes hoje no projeto. Fiquei responsável por fazer os merges nas branches durante o projeto. Corrigi os conflitos de merge no github, revisei a arquitetura e organização do projeto junto com o Diogo para ter ciência do que ficou faaltando para ser feito no próximo encontro.

## Encontro 4 - 2026-09-21

### Relato individual - Renato Lemos

-Criei o arquivo README index.py e os teste relacionados a ele, também criei a estrututura do eval e a de fixtures e os testes relacionados a eles, fiz as atualizações no readme de acordo com o que estava sendo feito, coordenei o time nas tarefas colaborativas e dividi o time para fazer algumas tarefas pendentes hoje no projeto. Fiquei responsável por fazer os merges nas branches durante o projeto. Corrigi os conflitos de merge no github, revisei a arquitetura e organização do projeto junto com o Diogo para ter ciência do que ficou faaltando para ser feito no próximo encontro.

- Depois que o Diogo finalizou o `src/retrieve.py`, integrei os três estágios já implementados num pipeline único: criei o `src/pipeline.py` (função `responder`), unindo roteamento (`router.py`, de autoria da Pâmela) + recuperação híbrida (`retrieve.py`) + geração (`generate.py`, de autoria do Marco), já compatível com o `eval/run_eval.py` via `src.pipeline:responder`. Ajustei o `src/router.py` (nova `inferir_motivo_recusa`) e o `src/generate.py` (parâmetro `refusal_reason_hint`) para a recusa carregar o motivo certo (fora de escopo, juízo de legalidade etc.) em vez de sempre cair em "sem evidência" quando nenhuma base é consultada. Atualizei `eval/adapters.py` e `eval/run_eval.py`, removendo as notas de que `retrieve.py` ainda não existia, e o README para refletir que `retrieval_denso`/`bm25`/`fusao_rrf` e o pipeline completo já estão implementados. Criei `tests/test_pipeline.py` cobrindo essa integração — suíte completa (`pytest tests/ -m "not llm"`) seguiu passando (71/71).

## Encontro 5 - 2026-09-23

### Relato individual - Renato Lemos

- Conferi o repositório contra a estrutura exigida no tópico 2.6 e os demais requisitos do desafio para levantar o que faltava. Com base nisso, dividi as tarefas do dia: a Pâmela ficou com o `app/`, o Diogo com os testes do `src/retrieve.py`, e o Marco e o João com os testes do `fetch_corpus` e a junção da pasta `testes/` com `tests/`. Eu fiquei com os testes do `ingest`, a política de sensibilidade e os logs do pipeline.
- Criei `tests/test_ingest.py` (20 testes), cobrindo a divisão em chunks, o hash SHA-256, a geração do JSONL e do relatório, o modo `--dry-run`, a validação da linha de comando e o teste de metadados obrigatórios de todo chunk pedido no tópico 2.5.
- Implementei a política de sensibilidade em `src/sensitivity.py`: mascaramento de CPF de pessoa física, telefone e e-mail pessoal (e-mails de órgãos públicos são preservados), além do descarte de chunks marcados como `restrito`. Integrei ao `src/generate.py` em dois pontos: o contexto é mascarado antes de ir para o LLM e a resposta final é mascarada de novo. Criei `tests/test_sensitivity.py` (27 testes) e documentei a política no README.
- Completei o logging do pipeline (tópico 2.4). Antes, só a geração gravava log. Agora roteamento, busca densa, BM25, fusão RRF, rerank e geração gravam no formato do `src/obs.py`, todos ligados pelo mesmo `run_id`, com tokens e modelo na geração e dados pessoais mascarados no log. Nesse trabalho encontrei e corrigi um bug: o `generate.py` chamava uma função que não existe no `llm_ecoa.py` (`chat_completion`), então o pipeline real sempre terminava em recusa. Criei `tests/test_pipeline_logs.py` (9 testes) e um `tests/conftest.py` para os testes não gravarem no `logs/` real. A suíte ficou com 127 testes passando.
- Identifiquei que a base agregada (`pca_agregado`) não está no manifesto do `main` e que o `retrieve.py` procura uma base conversacional (`atas_conversacional`) que também não existe. Tentei consultar os endpoints de PCA e de atas do PNCP, mas a API está instável para todas as turmas (HTTP 500 e 504). Registrei as tentativas, com horário e resultado, numa seção manual do `CORPUS_INVENTORY.md`. Mais tarde, a partir de recursos que o João já tinha validado, verifiquei que o serviço `/api/pncp/v1` do PNCP respondia normalmente, e foi por ele que as duas bases passaram a ser coletadas (ver abaixo).
- Comparei os dois `CORPUS_INVENTORY.md` (raiz e `data/`). Eles não são iguais, e o da raiz é o que corresponde ao `data/inventory/v1.json`. No fim do dia, defini o da raiz como oficial e removi o de `data/`, que era uma coleta antiga sem vínculo com o manifesto.
- Atualizei a seção de divisão de responsabilidades e coordenação do `ARQUITETURA.md`, registrando que as tarefas são distribuídas a cada encontro conforme as necessidades do projeto e as revisões, e listando as pendências em aberto.
- Como responsável pelos merges, integrei ao `main` as branches do time. Fiz o merge da branch da Pâmela (`app/cli.py`), testei o app no terminal e completei a entrega com `tests/test_app_cli.py` (8 testes), a seção de uso no README e o `run_id` no comando `rota`, para que ele apareça certo no log.
- Fiz o merge da branch do João (`joao-reis`) e resolvi o conflito em `tests/test_ingest.py`, que tinha sido criado nos dois lados: mantive os meus testes e acrescentei os dele. Um teste dele revelou um laço infinito no `chunk_text` do `src/ingest.py` quando `overlap >= max_chars`, e corrigi. Os testes dele dependem de código que ainda não foi publicado em `src/` (como `src/providers/`), então marquei 13 testes com `skip` e o motivo, para o `main` continuar sem falhas até esse código entrar.
- Fiz o merge da branch do Marco (`marco/test-fetch-corpus`) e resolvi o conflito em `tests/test_fetch_corpus.py`, criado também na branch do João, juntando os testes dos dois no mesmo arquivo. Ajustei o fixture `project` para restaurar as variáveis globais do `fetch_corpus` ao fim de cada teste, evitando que um teste afetasse o seguinte. A suíte terminou com 176 testes passando e 13 pulados.
- Fiz o merge da branch do Diogo (testes do `src/retrieve.py`) e revisei o módulo com os dados reais. Encontrei dois problemas: a busca associava cada natureza a uma única base fixa, e duas delas nem existiam (só 19 de 516 chunks eram alcançáveis), e os embeddings publicados em `data/embeddings/` não estão no formato que o `retrieve` lê. Mudei a seleção para usar a natureza de cada chunk, com o manifesto como apoio, e criei o filtro por `escopo`: o pipeline consulta só as bases oficiais.
- Atualizei o `corpus_manifest.yaml`: todas as bases ganharam `escopo` (oficial ou experimental, seguindo o desenho do João), e declarei `atas_conversacional` (Ata de Registro de Preços 00250/2025) e `pca_agregado` (PCA 2025 do Estado do RJ) com endereços fixos do serviço `/api/pncp/v1`. Para a coleta funcionar, corrigi o `src/artifact_downloader.py`, que não reconhecia CSV (salvava como `.bin`, inclusive a base do SIGA). Uma coleta de verificação fora da área oficial baixou os 5 arquivos das duas bases sem falhas.
- Implementei a base agregada. O CSV do PCA tem 39.506 itens e geraria 42.758 chunks, então criei o `src/pca_indicators.py`, que calcula na ingestão cerca de 1.000 indicadores (visão geral, totais por categoria, unidade, classe e mês, rankings e resumos curtos), cada um virando um chunk com os valores já calculados, e integrei ao `src/prepare_documents.py`. Também corrigi a normalização de datas (ano sozinho e data com hora ISO quebravam o `ChunkMetadata`), um bug no roteador (o padrão de "maior" nunca casava) e acrescentei o vocabulário do PCA às perguntas agregadas, sem alterar o resultado do golden set (15 de 19). No `retrieve`, o BM25 passou a ignorar acentos e criei o `fetch_k`, para que o chunk certo chegue à fusão RRF.
- Testei a busca híbrida com embeddings reais do endpoint da turma, numa área de teste: 5 de 5 perguntas sobre o PCA trouxeram o indicador certo em primeiro lugar. Rodei o pipeline completo com o LLM real pela primeira vez, e as respostas saíram com os valores corretos e citando as fontes (ex.: valor total do PCA 2025, R$ 61.119.549.484,04). A suíte terminou com 237 testes passando e 13 pulados.


### Uso de assistentes de IA

- Usei o Claude (Claude Code) para levantar o que faltava no repositório em relação ao desafio e para implementar os testes do `ingest`, o módulo `src/sensitivity.py`, os logs do pipeline e seus testes, a seção do inventário e o texto do `ARQUITETURA.md`. Também usei na resolução dos conflitos de merge, nos testes do app, na revisão e correção do `retrieve`, no manifesto e na implementação e medição da base agregada.
- Defini as tarefas e as prioridades, tomei as decisões e revisei os resultados antes de cada commit. Por exemplo: escolhi quais itens eram meus e quais ficavam com o restante do time, determinei que o inventário de `data/` só seria removido se fosse idêntico ao da raiz (não era) e, depois da análise, decidi removê-lo e manter o da raiz como oficial e pedi para suavizar o `ARQUITETURA.md`, sem atribuir tarefas fixas a cada membro, já que elas são redistribuídas a cada encontro. Nos merges, decidi a ordem (primeiro o João, depois o Marco) e aprovei concluir o do João marcando com `skip` os testes que dependem de código ainda não publicado, em vez de trazer testes falhando para o `main`. Na base agregada, pedi a análise do projeto antes da implementação e acompanhei a medição com perguntas reais, que levou a ajustes nos textos dos indicadores até as 5 perguntas acertarem.
