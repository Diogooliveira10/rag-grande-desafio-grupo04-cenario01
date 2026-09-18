# Inventário do corpus

- Cenário: `contratacoes-rj`
- Versão: `v1`
- Coletado em: `2026-09-18T19:50:34+00:00`
- Arquivos: **1**
- Bytes: **107985**
- Falhas: **3**

## Bases

| Base | Natureza | Arquivos | Formatos | Bytes | Alvo | Divergência |
|---|---|---:|---|---:|---:|---:|
| `contratos_estruturado` | estruturada | 1 | json | 107985 | None | None |
| `editais_normativo` | normativa | 0 | pdf | 0 | 20 | -20 |

## Falhas

- `https://pncp.gov.br/api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20250331&pagina=2&tamanhoPagina=50`: The read operation timed out
- `https://exemplo.gov.br/edital-001-2025.pdf`: [Errno 11001] getaddrinfo failed
- `https://exemplo.gov.br/edital-014-2025.pdf`: [Errno 11001] getaddrinfo failed
