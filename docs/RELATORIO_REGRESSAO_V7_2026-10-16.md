# Regressão de Recuperação v7 — 2026-10-16

## Contexto

Após construir o índice v7 (candidato_05, 142.026 vetores Q8_0 local), comparamos
a recuperação densa (dense-only) e híbrida (BM25 + RRF) contra a v6 de referência.

## Setup

- **Casos:** 47 em `eval/retrieval_set.jsonl` (16 normativa, 12 estruturada, 10 agregada, 9 conversacional)
- **v6 referência:** `eval/runs/projecao-v6-3modos-2026-10-14/index_completa` (5.115 vetores, Q8_0)
- **v7 candidato:** `data/v7_recuperacao/2026-10-15/candidato_05/index` (142.026 vetores, Q8_0)
- **Encoder:** Qwen3-Embedding-0.6B.Q8_0 local (llama-server, 127.0.0.1:18321)
- **Script:** `eval/medir_regressao_v7.py` (pré-carrega vetores, ~5s para 47 casos)

## Resultados

### Dense-only (v7)

| Corpus | hit@1 | hit@3 | hit@5 | hit@10 | mrr@10 |
|---|---|---|---|---|---|
| v6 | 0.553 | 0.702 | 0.766 | 0.787 | 0.640 |
| v7 (dense) | 0.447 | 0.532 | 0.575 | 0.617 | 0.501 |

Regressões: 10 · Melhorias: 1

### Hybrid (BM25+RRF, v7 only; v6 mantido dense)

| Corpus | hit@1 | hit@3 | hit@5 | hit@10 | mrr@10 |
|---|---|---|---|---|---|
| v6 | 0.553 | 0.702 | 0.766 | 0.787 | 0.640 |
| v7 (hybrid) | 0.404 | 0.575 | 0.638 | 0.702 | 0.504 |

Regressões: 17 · Melhorias: 7

## Análise das Regressões Dense-only

### Grupo 1: Cobertura do corpus (3 casos) — esperado

| Caso | v6 | v7 | Motivo |
|---|---|---|---|
| R24 | 1 | None | Contrato de tomógrafo fora do recorte RJ/2025 |
| R25 | 1 | None | Contrato de Volta Redonda fora do recorte RJ/2025 |
| R34 | 1 | None | Ata de forro PVC (pregão 62/2023) fora do recorte |

A v6 tinha 625 contratos curados. A v7 filtra RJ/2025 — contratos de outros
anos/estados foram removidos. **Estas regressões são aceitas.**

### Grupo 2: Qualidade da busca (7 casos) — os dados existem no v7

| Caso | v6 | v7 | Chunks no v7 | Problema |
|---|---|---|---|---|
| R29 | 2 | None | 2 | "vale transporte" — busca semântica não encontra |
| R32 | 1 | None | 7 | "equipamentos odontológicos" — 7 chunks, busca não acha |
| R33 | 1 | None | 0 | "cirurgia cardíaca" — nem v6 nem v7 têm, v6 acertava por similaridade |
| I02 | 1 | None | 27 | CNPJ 05340639000130 — busca identidade não funciona com dense |
| I03 | 5 | None | 27 | Mesmo CNPJ com pontuação |
| I04 | 2 | None | 1 | Contrato nº 75/2025 — busca identidade |
| I07 | 1 | None | 0 | Pregão 90022/2025 — busca identidade |

## Hybrid (BM25+RRF) vs Dense

### Ganhou com Hybrid (9 casos) — queries identidade

| Caso | Dense | Hybrid | Query |
|---|---|---|---|
| I04 | None | 9 | Contrato nº 75/2025 |
| I05 | None | 1 | CNPJ 40213951000163 |
| I06 | None | 2 | Contrato 01612355000102 |
| I11 | None | 2 | Compra 33663683000116 |
| I13 | None | 5 | Decreto 48.816 |
| R25 | None | 1 | Volta Redonda código-fonte |
| R29 | None | 7 | Vale transporte |
| R13 | 2 | 1 | Percentual garantia |
| R14 | 3 | 1 | Decreto PCA |

### Perdeu com Hybrid (10 casos) — queries semânticas

| Caso | Dense | Hybrid | Query |
|---|---|---|---|
| R07 | 1 | None | Punições infrações |
| R02 | 1 | 4 | Empresas licitação |
| R09 | 1 | 3 | Pagamento antecipado |
| R10 | 1 | 2 | Pequenos negócios |
| R12 | 1 | 3 | Contrato contínuo |
| R21 | 1 | 2 | Equipamentos radiação |
| R24 | 7 | None | Tomógrafo |
| R32 | 8 | None | Equipamentos dentistas |
| R04 | 4 | 5 | Estudo preliminar |
| R06 | 5 | 10 | Contratação sem licitar |

## Estratégia recomendada

**Detecção automática de query identidade → hybrid; query semântica → dense-only.**

Queries identidade contêm: CNPJ (14 dígitos), nº contrato, nº pregão, nomes
próprios de empresas. O `retrieve.py` já tem `_identificadores_pncp()` que
detecta padrões PNCP — pode ser reutilizado.

## Como rodar

```bash
# Regressão dense-only
python -u eval/medir_regressao_v7.py \
  --v6-index eval/runs/projecao-v6-3modos-2026-10-14/index_completa \
  --v6-documents data/normalized/documents.jsonl \
  --v7-index data/v7_recuperacao/2026-10-15/candidato_05/index \
  --v7-documents data/v7_recuperacao/2026-10-15/candidato_05/documents.jsonl \
  --saida eval/runs/regressao-v7-dense

# Regressão hybrid (BM25+RRF no v7, dense no v6)
python -u eval/medir_regressao_v7.py \
  --v6-index eval/runs/projecao-v6-3modos-2026-10-14/index_completa \
  --v6-documents data/normalized/documents.jsonl \
  --v7-index data/v7_recuperacao/2026-10-15/candidato_05/index \
  --v7-documents data/v7_recuperacao/2026-10-15/candidato_05/documents.jsonl \
  --saida eval/runs/regressao-v7-hybrid \
  --hybrid
```

## Artefatos

| Caminho | Descrição |
|---|---|
| `eval/medir_regressao_v7.py` | Script de regressão otimizado (pré-carrega vetores) |
| `eval/runs/regressao-v7-2026-10-16-v7/` | Resultado dense-only |
| `eval/runs/regressao-v7-hybrid-v2/` | Resultado hybrid |
| `src/reestruturar_indice_v7.py` | Reestrutura FAISS flat → layout por base |
| `src/montar_faiss_v7.py` | Monta FAISS com reaproveitamento de vetores |