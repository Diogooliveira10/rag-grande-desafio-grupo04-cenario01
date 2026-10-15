# Embeddings versionados

A versão oficial ativa é a **v6 após a recuperação OCR**, na pasta
`2026-10-07_21-18-41_ocr-recovery/`, com quatro subpastas por base e 5.090 vetores
de 1.024 dimensões. A geração original da v6 (5.621 vetores, antes do OCR) fica em
`2026-09-30_12-08-51/` como histórico. Cada base contém os vetores,
o relatório de geração e o resultado da validação de tokens. O arquivo
`embeddings.jsonl` na raiz deste diretório é uma materialização histórica e não
deve substituir os índices ativos da v6.

O modelo local Qwen3-Embedding-0.6B Q8 e o modelo ECOA Q4 são artefatos distintos.
Checkpoint, hash e configuração constam nos relatórios de cada base. Os índices
em `index/` foram construídos com esses vetores, sem embedar documentos no ECOA.
As perguntas usam o modelo ECOA configurado, com instrução de recuperação e cache
local não versionado. Embeddings da v5 são preservados somente no disco.

Este diretório contém uma materialização pública dos embeddings gerados a
partir dos documentos preparados pelo pipeline do repositório.

- `embeddings.jsonl`: um registro por chunk, com vetor e metadados de origem;
- `embedding_report.json`: resumo da execução, modelo, provedor, dimensão e
  quantidade de chunks.

Os registros podem ser consumidos diretamente por scripts de busca ou indexação
sem depender de `data/attempts/`. Os caminhos registrados nos metadados são
relativos ao repositório e o `manifest_hash` identifica a configuração usada na
preparação.

Para gerar uma nova versão de desenvolvimento, use `data/attempts/`; somente
publique em `data/embeddings/` uma execução validada e compatível com o contrato
de metadados do projeto.
