# Inventário do corpus

- Cenário: `contratacoes-rj`
- Versão: `v1`
- Coletado em: `2026-09-18T19:54:39+00:00`
- Arquivos: **12**
- Bytes: **1329680**
- Falhas: **3**

## Bases

| Base | Natureza | Arquivos | Formatos | Bytes | Alvo | Divergência |
|---|---|---:|---|---:|---:|---:|
| `contratos_estruturado` | estruturada | 12 | json | 1329680 | None | None |
| `editais_normativo` | normativa | 0 | pdf | 0 | 20 | -20 |

## Falhas

- `https://pncp.gov.br/api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20250331&pagina=13&tamanhoPagina=50`: The read operation timed out
- `https://exemplo.gov.br/edital-001-2025.pdf`: [Errno 11001] getaddrinfo failed
- `https://exemplo.gov.br/edital-014-2025.pdf`: [Errno 11001] getaddrinfo failed

## Bases bloqueadas pela fonte (registro manual — 2026-09-23)

> Seção escrita à mão, **não** gerada pelo `fetch_corpus`. As tentativas abaixo foram feitas com requisições diretas (`curl`), fora do `fetch_corpus`, para diagnosticar a fonte. Por isso não entram em `data/inventory/v1.json`: a v1 é imutável e não deve registrar coletas que não aconteceram por ela. As falhas vão para o inventário oficial na próxima execução do `fetch`, depois que as bases forem declaradas no manifesto.

O cenário exige quatro bases (normativa, estruturada, conversacional e agregada). Duas delas ainda não têm dados coletados, porque a API de consulta do PNCP está instável. A API é compartilhada por todas as turmas do desafio, e a instabilidade não é específica deste projeto.

| Base prevista | Natureza | Situação no manifesto | Situação da coleta |
|---|---|---|---|
| `pca_agregado` | agregada | Declarada na branch `marco/fetch-corpus`, ausente do `main` | Bloqueada pela fonte |
| `atas_conversacional` | conversacional | Não declarada; o `main` tem `respostas_api_jsonl` | Bloqueada pela fonte (atas); e-mails sintéticos ainda não localizados |

### Tentativas registradas

| Horário (UTC) | Endpoint | Resultado | Tempo |
|---|---|---|---:|
| 2026-09-16T19:19:17 | `/api/consulta/v1/atas?dataInicial=20250101&dataFinal=20250131&pagina=1&tamanhoPagina=10` | Timeout de leitura | — |
| 2026-09-23, tarde | `/api/consulta/v1/pca/?anoPca=2025&codigoClassificacaoSuperior=8920&pagina=1&tamanhoPagina=10` | HTTP 500 — "Erro na comunicação com o banco de dados." | 51,7 s |
| 2026-09-23T18:06:00 | `/api/consulta/v1/pca/?anoPca=2025&codigoClassificacaoSuperior=8920&pagina=1&tamanhoPagina=10` | HTTP 504 Gateway Time-out | 70,1 s |
| 2026-09-23T18:07:10 | `/api/consulta/v1/pca/atualizacao?dataInicio=20250101&dataFim=20250131&pagina=1&tamanhoPagina=10` | HTTP 504 Gateway Time-out | 70,2 s |
| 2026-09-23T18:08:20 | `/api/consulta/v1/atas?dataInicial=20250101&dataFinal=20250131&pagina=1&tamanhoPagina=10` | HTTP 504 Gateway Time-out | 70,2 s |
| 2026-09-23T18:09:31 | `/api/consulta/v1/atas?dataInicial=20250101&dataFinal=20250107&pagina=1&tamanhoPagina=10` (período reduzido a 1 semana) | HTTP 504 Gateway Time-out | 70,1 s |

Todas as URLs têm como prefixo `https://pncp.gov.br`. A especificação da API (`/api/consulta/v3/api-docs`) respondia HTTP 200 no mesmo momento, então o serviço estava no ar, mas as consultas de PCA e de atas não retornavam.

> Na verificação de 2026-09-16 (`testes/test-fontes/fontes_cenario01.md`), a consulta de PCA retornou HTTP 404. Esse erro **não** foi instabilidade: a URL usava `/v1/pca` sem a barra final, e o caminho correto é `/v1/pca/`.

### Pendências para fechar as duas bases

1. ~~Declarar `pca_agregado` e `atas_conversacional` no `corpus_manifest.yaml`.~~ **Feito em 2026-09-23.** As duas bases usam recursos fixos do serviço `/api/pncp/v1` (PCA 2025 do Estado do RJ e a Ata de Registro de Preços 00250/2025), que respondia normalmente enquanto `/api/consulta/v1` estava fora do ar.
2. ~~Preencher `data/config/cnpjs_rj.json`.~~ **Não é mais necessário para o PCA:** a base fixa o órgão (Estado do RJ, CNPJ 42498600000171).
3. Localizar a base sintética de e-mails de compras fornecida pelo desafio. Não há URL pública indicada.
4. Rodar a coleta oficial (`py -m src.fetch_corpus fetch --manifest corpus_manifest.yaml --official`) e gerar o inventário da nova versão. Uma coleta de verificação das duas bases novas, fora da árvore oficial (`data/attempts/`), baixou os 5 arquivos sem falhas em 2026-09-23.
