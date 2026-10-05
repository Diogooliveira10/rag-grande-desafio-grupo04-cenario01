# Inventário do corpus

- Tipo: `manifest`
- Oficial: `True`
- Vinculado ao manifesto: `True`
- Cenário: `contratacoes-rj`
- Versão: `v6`
- Coletado em: `2026-09-30T17:08:01+00:00`
- Completo: **True**
- Arquivos: **1400**
- Itens/registros: **1400**
- Bytes: **33466471**
- Falhas: **0**

## Bases

| Base | Escopo | Natureza | Status | Arquivos | Itens | Formatos | Bytes | Alvo | Divergência |
|---|---|---|---|---:|---:|---|---:|---:|---:|
| `contratos_estruturado` | oficial | estruturada | complete | 625 | 625 | json: 625 | 1335165 | 625 | 0 |
| `editais_normativo` | oficial | normativa | complete | 13 | 13 | pdf: 9, html: 4 | 8285989 | 13 | 0 |
| `atas_conversacional` | oficial | conversacional | complete | 376 | 376 | json: 336, pdf: 40 | 21953983 | 376 | 0 |
| `pca_agregado` | oficial | agregada | complete | 386 | 386 | json: 386 | 1891334 | 386 | 0 |

## Suplemento sintético conversacional

O inventário acima continua sendo o snapshot público **v6**, sem alterações.
A base ativa também inclui `emails_sinteticos`, separadamente:

| Base | Escopo | Natureza | Arquivos | Conversas | Mensagens | Chunks | Sensibilidade |
|---|---|---|---:|---:|---:|---:|---|
| `emails_sinteticos` | oficial | conversacional | 25 JSON | 25 | 45 | 25 | 20 públicas, 5 restritas |

- Fonte: `conversacional_sintetica.zip` fornecido pelo grupo (52.963 bytes nos JSONs canônicos).
- Canonização: somente JSON individual; TXT e JSONL consolidados são cópias, não novas conversas.
- Base ativa: **1.425 arquivos de origem** e **5.646 chunks** (5.621 da v6 + 25 sintéticos).
- Não é uma nova coleta PNCP nem uma versão v7. O suplemento tem coleta local própria.
- Proveniência, classificação e reconstrução: [docs/emails_sinteticos.md](docs/emails_sinteticos.md).

## Falhas

Nenhuma falha registrada.
