# Embeddings versionados

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
