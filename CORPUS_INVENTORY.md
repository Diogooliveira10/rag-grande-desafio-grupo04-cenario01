# Inventário do corpus — fotografia local da v2

Este documento resume `data/inventory/v2.json` disponível nesta cópia do
repositório. O JSON v2 e os dados brutos/normalizados não estão todos rastreados
pelo Git; uma clonagem limpa pode não conter esses arquivos.

- Tipo: `manifest`
- Oficial: `True`
- Vinculado ao manifesto: `True`
- Cenário: `contratacoes-rj`
- Versão: `v2`
- Coletado em: `2026-09-23T20:57:05+00:00`
- Completo: **True**
- Arquivos: **222**
- Itens/registros: **200**
- Bytes: **28934646**
- Falhas: **0**

## Como interpretar a contagem da coleta

**Arquivos** conta todos os artefatos coletados. **Itens/registros** conta as unidades lógicas do corpus; arquivos auxiliares ou formatos alternativos não são contados novamente como itens. Na base `atas_conversacional`, cada ata é contada pelo seu JSON de metadados; o JSON retornado pelo endpoint `/arquivos` correspondente também é um arquivo coletado, mas não uma segunda ata. Na base `pca_agregado`, cada registro consolidado em JSON conta como item; os CSVs de 2024 e 2025 são exportações alternativas dos dados e contam como arquivos, não como itens adicionais.

Por isso, o inventário tem **222 arquivos** e **200 itens/registros lógicos**.
Esses totais descrevem os artefatos e unidades selecionadas na coleta, não o
número de linhas geradas depois da extração. Um arquivo CSV pode produzir
dezenas de milhares de documentos/chunks na etapa seguinte.

## Bases

| Base | Escopo | Natureza | Status | Arquivos | Itens | Formatos | Bytes | Alvo | Divergência |
|---|---|---|---|---:|---:|---|---:|---:|---:|
| `contratos_estruturado` | oficial | estruturada | complete | 160 | 160 | json: 160 | 350001 | 160 | 0 |
| `editais_normativo` | oficial | normativa | complete | 4 | 4 | html: 3, pdf: 1 | 2535366 | 4 | 0 |
| `atas_conversacional` | oficial | conversacional | complete | 40 | 20 | json: 40 | 33397 | 20 | 0 |
| `pca_agregado` | oficial | agregada | complete | 18 | 16 | csv: 2, json: 16 | 26015882 | 16 | 0 |

## Falhas

Nenhuma falha registrada.

## Extração local da v2

Os relatórios atuais em `data/normalized/v2/<base_id>/extraction_report.json`
registram os seguintes totais. São unidades e chunks produzidos pela
normalização, portanto não são diretamente comparáveis aos itens do inventário
de coleta.

| Base | Documentos extraídos | Chunks | Observação |
|---|---:|---:|---|
| `contratos_estruturado` | 160 | 160 | Um chunk por registro estruturado nesta extração. |
| `editais_normativo` | 72 | 843 | HTML e PDF são divididos em unidades e chunks. |
| `atas_conversacional` | 40 | 40 | São respostas JSON de metadados e listagens `/arquivos`; não são o texto dos anexos. |
| `pca_agregado` | 68.059 | 73.780 | Inclui linhas dos dois CSVs (2024 e 2025), além dos registros JSON. |
| **Total local** | **68.331** | **74.823** | Contagem dos relatórios de extração presentes nesta cópia. |

O inventário marca a base conversacional como `complete` porque ela atingiu o
alvo de 20 atas declarado no manifesto; isso não significa que contenha os
e-mails sintéticos descritos no enunciado ou o texto dos PDFs das atas. Da mesma
forma, `pca_agregado` cobre um único CNPJ e os CSVs de planejamento contêm
valores estimados, não uma série de preços efetivamente homologados por vários
municípios.

`complete` e `falhas: 0` informam o resultado da coleta em relação aos alvos
declarados. Não medem fidelidade da extração, cobertura temática, equilíbrio
entre municípios nem qualidade de recuperação ou resposta.
