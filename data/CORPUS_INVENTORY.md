# Inventário do corpus coletado

Última atualização: `2026-09-16T19:19:17+00:00`

## Resumo

- Artefatos brutos: **22**
- PDFs coletados: **3**
- HTML/HTM: **11**
- JSON: **5**
- JSONL: **3**
- Hashes SHA-256 duplicados: **0**

Os arquivos brutos ficam em `data/raw/v1/arquivos/` e os relatórios de execução em `data/runs/`. O conteúdo bruto é identificado por SHA-256; uma nova coleta do mesmo conteúdo reutiliza o arquivo existente.

## PDFs coletados

| Arquivo | Base | Origem |
| --- | --- | --- |
| `raw/v1/arquivos/pdf/mppi_lei_14133_pdf_a5b21177bdc7.pdf` | Normativa | MPPI — Lei 14.133/2021 |
| `raw/v1/arquivos/pncp/edital_60fecce8c451.pdf` | Normativa | PNCP — edital de contratação |
| `raw/v1/arquivos/pncp/ata_fdab8b0008e8.pdf` | Conversacional | PNCP — ata de registro de preços |

## Estrutura

```text
data/
├── raw/
│   └── v1/
│       └── arquivos/
│           ├── html/
│           ├── json/
│           ├── jsonl/
│           ├── pdf/
│           └── pncp/
└── runs/
    └── <timestamp>/
```
