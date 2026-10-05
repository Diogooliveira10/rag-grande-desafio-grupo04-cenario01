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
| POST | `/api/v1/query` | `{ "query": "...", "top_k": 5 }` | `QueryResponse` |
| GET | `/api/v1/history` | `limit` (1–100) | `HistoricoItem[]` |
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
# Opcional para execução sem sentence-transformers instalado:
$env:RERANK_ENABLED = "false"
python -m uvicorn app.api:app --host 127.0.0.1 --port 8080
```

Equivale ao `--openrouter-only` da CLI para as chamadas de chat. Os embeddings
continuam no ECOA.

## Testes

Backend: `tests/test_catalog_api.py`, `tests/test_app_api.py` e suíte geral.
Frontend: testes de services, paginação, Documentos e integração de Consulta.
Validação em navegador real (Edge/Playwright) salva localmente em
`data/runs/frontend-local/browser-validation.json` e screenshots `browser-*.png`.
