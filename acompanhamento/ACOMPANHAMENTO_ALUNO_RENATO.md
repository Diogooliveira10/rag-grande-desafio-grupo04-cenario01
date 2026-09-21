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