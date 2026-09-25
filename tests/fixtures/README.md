# Dados de teste offline

Este diretório contém documentos pequenos e versionados para os testes da
extração e do inventário. Os testes não acessam a internet, não chamam LLMs e
não escrevem em `data/raw`.

Os 20 documentos usam o mesmo contrato mínimo esperado para as próximas
camadas: `id`, `base_id`, `doc_type`, `sensitivity` e `text`.
