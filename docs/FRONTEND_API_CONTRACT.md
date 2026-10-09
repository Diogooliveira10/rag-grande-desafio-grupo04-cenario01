# Contrato HTTP do NEXO RJ

API local: `http://localhost:8080`. Frontend Vite: `http://localhost:5173`.
Schemas publicados no OpenAPI em `/docs` e nos modelos
[`app/catalog_models.py`](../app/catalog_models.py),
[`app/api.py`](../app/api.py) e [`src/schema.py`](../src/schema.py).
Os tipos correspondentes do frontend ficam em `src/types/api.ts` no repositório
`frontendRAG`. Não há dados mockados nem coleta remota nas rotas de catálogo.

## Rotas

| Método | Endpoint | Entrada | Resposta |
|---|---|---|---|
| GET | `/health` | — | `{ "status": "ok" }` |
| POST | `/api/v1/query` | `{ "query": "...", "top_k": 5, "session_id": "s_...", "historico": [{ "pergunta": "...", "resposta": "..." }] }` | `QueryResponse` |
| GET | `/api/v1/history` (chave) | `limit` (1–100), `session_id` | `HistoricoItem[]` |
| GET | `/api/v1/trace/{query_id}` (sessão ou chave) | — | `TraceResponse` |
| GET | `/api/v1/sessions/{session_id}/trace` (sessão ou chave) | — | `SessaoTraceResponse` |
| POST | `/api/v1/feedback` | `query_id`, `avaliacao`, `comentario` | `{ "status": "recebido" }` |
| GET | `/api/v1/explore/contratacoes` | `busca`, `orgao`, `limit`, `offset` | `ContratacaoRecord[]` |
| GET | `/api/v1/explore/documentos` | `busca`, `tipo`, `limit`, `offset` | `DocumentoRecord[]` |
| GET | `/api/v1/explore/orgaos` | `busca`, `limit`, `offset` | `OrgaoRecord[]` |
| GET | `/api/v1/explore/fontes` | — | `OfficialSource[]` |
| GET | `/api/v1/project/summary` | — | `ProjectSummary` |

Catálogo: `limit=24` por padrão, máximo 100; `offset>=0`. As listas são arrays
simples. Página menor que `limit` indica fim. A busca ignora caixa e acentos;
filtros vazios não restringem resultados. Tipos de documento: `Normativo`,
`Contrato`, `Ata de Registro`, `PCA` e `Conversa sintética`.

Os documentos são agrupados por arquivo de origem, não por página/chunk.
Campos não presentes nas fontes continuam `null`. Valores estimados não são
apresentados como valores globais; data de assinatura não vira homologação.
Órgãos e totais são derivados somente dos registros reais, sem somar conversas
sintéticas. Totais incompletos são `null`, não zero.

## Chat e conversas comuns

`QueryResponse` mantém: `query_id`, `session_id`, `query`, `answer`,
`bases_consultadas`, `sources_used`, `confidence_level`, `is_refusal`,
`refusal_reason`, `pergunta_reformulada` (`null` na maioria das respostas) e
`avisos` (lista, quase sempre vazia).

- `answer` não traz identificadores internos. Quando o modelo liga uma
  afirmação à sua fonte, o texto mostra `[1]`, `[2]`...: o número é a posição
  da fonte em `sources_used`, começando em 1. O frontend pode transformar cada
  `[n]` em link para a fonte correspondente.
- `avisos` traz notas sobre como a resposta foi obtida, separadas do texto. Hoje
  o único aviso é a busca ampliada: "Nenhum resultado atendeu a todos os filtros
  da pergunta; a busca foi ampliada em …". Mostrar como nota discreta abaixo da
  resposta.

- Saudações, agradecimentos, despedidas e ajuda de uso recebem resposta normal,
  sem acervo/LLM quando reconhecidos diretamente.
- Mensagens longas e mensagens sem rota reconhecida passam pela identificação
  de intenção (`CONVERSATION_MODE=llm`). Apresentações, contextos e elaboração
  de texto podem ser conversa. Pedidos de fatos existentes continuam pelo RAG.
- Conversa não é recusa: `is_refusal=false`, `refusal_reason=null`,
  `bases_consultadas=[]`, `sources_used=[]`, `confidence_level="baixa"`.
  O frontend apresenta essas respostas como **Conversa — sem consulta ao acervo**,
  não como evidência factual. Toda interação continua no Histórico/Feedback.
- Pedidos sensíveis, inclusive precedidos de saudação ou texto longo, não
  passam pelo atalho de conversa. Erro/saída inválida da identificação de
  intenção volta ao pipeline RAG, sem inventar resposta factual.
- CPF, telefone e e-mail pessoal são mascarados antes da interpretação por
  modelo e na resposta conversacional.

## Contexto entre perguntas

O backend não guarda memória entre as perguntas: quem dá contexto é o frontend,
reenviando as últimas trocas no campo opcional `historico` de
`POST /api/v1/query`.

- `historico`: lista de `{ "pergunta": "...", "resposta": "..." }`, da mais
  antiga para a mais recente, com no máximo 10 itens (a API usa só as 3 mais
  recentes). `resposta` é opcional; mande o texto da resposta anterior
  (`answer`). `pergunta` vazia, mais de 2.000 caracteres ou mais de 10 itens
  recebem `422`. Sem `historico` (ou lista vazia) nada muda.
- Quando a pergunta parece continuação ("considerando os mesmos contratos…",
  "E em 2024?", "desses, qual…"), a API a reescreve para se sustentar sozinha e
  consulta o acervo com a versão reescrita. Perguntas que já se sustentam
  sozinhas não são reescritas nem chamam o modelo.
- `query` na resposta continua sendo o texto digitado pelo usuário. Quando
  houve reescrita, `pergunta_reformulada` traz a pergunta de fato consultada;
  o frontend pode mostrá-la como "Entendi como: …". Sem reescrita é `null`.
- Se o modelo falhar ou devolver algo fora do formato, a pergunta original é
  consultada normalmente: a reescrita nunca derruba a consulta.
- CPF, telefone e e-mail pessoal do histórico são mascarados antes de irem ao
  modelo, e o histórico é tratado como dado: ordens escritas dentro dele são
  ignoradas. Os guardrails (dados bancários, credenciais, juízo de legalidade)
  valem sobre a pergunta reescrita.
- O trace mostra a etapa `reformulacao` (pergunta original, pergunta reescrita
  e tokens); quando não é necessária, registra `status=pulado` com o motivo.
- `REFORMULACAO_MODE=regras` desliga a reescrita (o `historico` é ignorado).

## Rastreabilidade (sessão e trace)

- `session_id` liga as perguntas de uma mesma conversa. Na primeira pergunta o
  frontend não envia o campo; a API cria um (`s_` + 22 caracteres aleatórios)
  e o devolve no `QueryResponse`. Nas perguntas seguintes o frontend reenvia
  esse valor. Aceita só letras, dígitos, `_` e `-`, de 16 a 64 caracteres;
  outro formato recebe `422`.
- `GET /api/v1/trace/{query_id}`: o caminho de uma pergunta, etapa por etapa
  (`etapas[]` com `stage`, `base` = `"sql"`, `"vetorial"` ou `null`, `input`,
  `output`, `latencia_ms`, `tokens` e `detalhes`: trechos com score, SQL e
  linhas devolvidas, prompt e resposta do LLM). `resumo` diz se a pergunta
  consultou a base SQL (`consultou_sql`) e a vetorial (`consultou_vetorial`),
  quantas evidências vieram de cada uma e quais fontes foram citadas. Nos logs
  novos, `consultou_*` significa tentativa; `sql_retornou_evidencia` e
  `vetorial_retornou_evidencia` distinguem retorno de chunks. Uma tentativa SQL
  com erro não aparece como SQL não consultado.
- Campos aditivos do trace: `status`, `completo`, `latencia_metodo` e
  `latencia_pipeline_ms`. O tempo total vem do envelope API/pipeline monotônico,
  sem somar etapas sobrepostas nem feedback posterior. Início sem fim é
  `incompleto`/`completo=false`; logs antigos sem envelope têm `completo=null`
  e `latencia_metodo=soma_etapas_legado` (estimativa).
- `etapas[]` pode conter `requisicao_inicio`, `pipeline_inicio`,
  `pipeline_fim`, `persistencia_historico` e `requisicao_fim`, além dos stages
  anteriores. Renderize stages desconhecidos de forma genérica. `pulado`
  significa não executado; `degradado` significa fallback tratado. Os detalhes
  incluem `outcome`, origem, sequência, motivo e metadados permitidos. O evento
  terminal da API confirma retorno preparado, não recebimento pelo navegador.
- `GET /api/v1/sessions/{session_id}/trace`: a conversa inteira, com as
  perguntas em ordem (`perguntas[]` com `query_id`, `pergunta`, `resumo` e os
  nomes das etapas) e quantas delas usaram SQL e busca vetorial. O detalhe de
  cada pergunta vem do trace por `query_id`. Cada pergunta também inclui
  `status`, `completo` e `latencia_metodo`. Para limites de texto, SQL/evidências,
  privacidade e interpretação dos eventos, veja [OBSERVABILIDADE.md](OBSERVABILIDADE.md).
- Os dois respondem `404` quando o id não está no log. Dados pessoais (CPF,
  telefone, e-mail) chegam mascarados, como no log.

## Proteção do histórico e do trace

Dois tipos de acesso:

- **Dono da conversa** (o site público): envia o header `X-Session-Id` com o
  `session_id` que recebeu no `QueryResponse`. Vê o trace das perguntas da
  própria conversa e o trace da sessão. Não vê conversas de outras pessoas nem
  o histórico geral.
- **Quem opera a demo**: envia `X-API-Key` igual a `RAG_API_KEY` do `.env` do
  servidor. Vê o histórico geral e o trace de qualquer conversa. A chave nunca
  vai no código do site: quem tiver o JavaScript teria a chave.

Respostas:

- Trace sem `X-Session-Id` da conversa e sem chave válida: `401`, mesmo quando
  o id não existe (não revela quais perguntas estão no log). Com acesso e id
  inexistente: `404`.
- Histórico sem chave válida: `401`; servidor sem `RAG_API_KEY`: `403`.
- `POST /api/v1/query` e `POST /api/v1/feedback` não pedem nada.
- O CORS libera os headers `X-API-Key` e `X-Session-Id`.

O histórico grava pergunta, resposta e comentário do feedback já mascarados
(CPF, telefone, e-mail pessoal) e apaga consultas com mais de
`HISTORY_RETENTION_DAYS` dias (padrão 30; `0` desliga o expurgo).

## Suplemento sintético e segurança

O resumo identifica `v6 + suplemento sintético`, 1.425 arquivos de origem,
cinco bases, sem renomear o snapshot público para v7. Inclui os 25 documentos
sintéticos nas contagens. O catálogo consultável mostra somente as **20 conversas
públicas**, com título `[Sintético]` e tipo `Conversa sintética`; os cinco
restritos não são expostos. Não há links de arquivos locais nem texto bruto
nas rotas de catálogo. O suplemento não comprova fatos reais do PNCP.

Erros: `422` para entrada inválida; `503` quando o acervo não pode ser lido;
`500` para falha na consulta. Não expor detalhes internos ou credenciais nem
substituir falhas por sucesso vazio/mock. CORS aceita `localhost:5173`,
`127.0.0.1:5173` e a origem GitHub Pages declarada em `app/api.py`.

## Executar em modo OpenRouter

PowerShell, com `OPENROUTER_API_KEY` já configurada no `.env`:

```powershell
$env:LLM_PROVIDER_MODE = "openrouter"
$env:CONVERSATION_MODE = "llm"
# Opcional para execução sem sentence-transformers instalado:
$env:RERANK_ENABLED = "false"
python -m uvicorn app.api:app --host 127.0.0.1 --port 8080
```

Equivale ao `--openrouter-only` da CLI para as chamadas de chat. Os embeddings
continuam no ECOA. `CONVERSATION_MODE=regras` desliga a identificação por modelo,
mas mantém respostas diretas de cortesia.

## Testes

Backend: `tests/test_catalog_api.py`, `tests/test_conversation.py`,
`tests/test_conversation_intent.py`, `tests/test_app_api.py` e suíte geral.
Frontend: testes de services, paginação, Documentos e integração de Consulta.
Validação em navegador real (Edge/Playwright) salva localmente em
`data/runs/frontend-local/browser-validation.json` e screenshots `browser-*.png`.
