# Inventário do corpus

> Inventário do corpus ativo **v7-reparo** (censo de contratos e publicações
> de 2025 do RJ), vinculado ao manifesto
> [`corpus_manifest.yaml`](corpus_manifest.yaml) versão `2026-10-13`.
> O snapshot v6 original (1.400 arquivos) foi preservado em
> `data/backup_v6_20261018_224104/`.

- Tipo: `manifest`
- Oficial: `True`
- Vinculado ao manifesto: `True`
- Cenário: `contratacoes-rj`
- Versão: `v7-reparo`
- Coletado em: `2026-10-18T21:14:05+00:00`
- Completo: **True**
- Chunks normalizados: **144.112**
- Bytes (documents.jsonl): **629 MB**
- Vetores FAISS: **143.299**
- Schema SQL: **7**

## Bases

| Base | Escopo | Natureza | Chunks | Vetores | FAISS | Formatos |
|---|---|---|---:|---:|---:|---|
| `contratos_estruturado` | oficial | estruturada | 106.666 | 105.917 | 414 MB | json |
| `atas_conversacional` | oficial | conversacional | 34.953 | 34.889 | 136 MB | json, pdf |
| `editais_normativo` | oficial | normativa | 1.557 | 1.557 | 6 MB | html, pdf |
| `pca_agregado` | oficial | agregada | 911 | 911 | 4 MB | json |
| `emails_sinteticos` | oficial | conversacional | 25 | 25 | 0 MB | json |

## Base estruturada SQL

| Tabela | Registros | Schema |
|---|---:|---|
| `contratacoes` | 106.667 | v7 |
| `pca_planos` | 1 | v7 |

## Suplemento sintético conversacional

25 e-mails sintéticos (20 públicas, 5 restritas), integrados em 2026-10-05.
Fonte: `conversacional_sintetica.zip`, gerado pelo tutor.
Proveniência: [docs/emails_sinteticos.md](docs/emails_sinteticos.md).

## Diferenças em relação à v6

| Métrica | v6 | v7 |
|---|---:|---:|
| Contratos | 810 | 106.667 |
| PCA | 1 | 1 |
| Chunks totais | 5.115 | 144.112 |
| Vetores | ~5.000 | 143.299 |

## Limitações conhecidas

- **Vetores com linhagem mista:** Q4_K_M (105.917) + Q8_0 (37.382). Busca semântica
  funciona, mas encoding não é homogêneo.
- **813 documentos sem vetor:** ficam fora da busca densa (BM25 ainda encontra).
- **Encoding provenance não comprovado:** `snapshot.py` rejeitaria o bundle por
  `proveniencia_incompleta`. Pipeline direto funciona normalmente.
- **PCA:** apenas 1 plano (Secretaria de Estado de Saúde, 2025). Censo completo
  depende de nova coleta.

## Como reconstruir

```bash
# 1. Baixar dados do PNCP (lento, ~2h+)
python -m src.fetch_corpus fetch --manifest corpus_manifest.yaml

# 2. Construir SQLite
python -m src.structured_db build

# 3. Gerar embeddings (requer endpoint ECOA)
python -m src.create_embeddings
```

Ou copiar os arquivos pré-construídos de `data/v7_recuperacao/2026-10-15/candidato_07_reparo/`.

## Falhas

Nenhuma falha registrada na coleta v7.