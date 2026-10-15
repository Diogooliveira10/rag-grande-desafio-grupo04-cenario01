# Qualidade dos dados

Como o corpus vira as bases que o RAG consulta, o que cada campo cobre de
verdade e o que o sistema faz quando um dado falta. Números medidos no
snapshot v6 (`data/structured/base_estruturada.sqlite`, schema 7) em
2026-10-14.

## ETL do projeto

```
Extract     fetch_corpus.py / pncp_client.py      PNCP → data/raw (versões v1…v6)
               │
Transform   prepare_documents.py                   normaliza → data/normalized/documents.jsonl
            ocr.py                                 recupera atas ilegíveis
            qualidade_corpus.py, corpus_validator.py  valida contra o manifest
               │
Load        structured_db.py                       SQLite (Git) e Neon (Postgres), mesmo schema
            create_embeddings.py, index.py         vetores + índice FAISS por base
```

O `corpus_manifest.yaml` faz o papel de mapa fonte → base: diz de onde vem
cada base, sua natureza (estruturada, normativa, conversacional, agregada) e
suas condições de uso.

## Cobertura dos campos

`contratacoes` tem 810 linhas: 455 contratos assinados e 355 compras
publicadas. Os dois tipos vêm de endpoints diferentes do PNCP, e cada um traz
campos que o outro não traz.

| Campo | Contratos (455) | Compras (355) | Motivo |
|---|---:|---:|---|
| `objeto`, `municipio`, `esfera`, `data_publicacao`, `valor` | 100% | 100% | — |
| `modalidade` | 3 (1%) | 100% | o contrato do PNCP não informa a modalidade; só a compra |
| `amparo_legal` | 0% | 100% | idem |
| `situacao` | 0% | 100% | idem |
| `fornecedor` | 100% | 1 (0%) | a compra é anterior à escolha do fornecedor |
| `fornecedor_cnpj` | 432 (95%) | 0% | os outros 23 têm 11 dígitos em `niFornecedor` (20 marcados como pessoa física): CPF não é guardado |
| `data_assinatura` | 100% | 0% | só existe no contrato |
| `valor_contratado` | 100% | 0% | `valorGlobal` do contrato |
| `valor_homologado` | 0% | 294 (83%) | só compras já homologadas |
| `valor_estimado` | 0% | 100% | — |

Outros pontos:

- **42 registros têm valor abaixo de R$ 1,00** (R$ 0,00 e R$ 0,01 simbólicos
  do PNCP). Entram nos totais, mas ficam fora de média, mediana e ranking.
- **`pca_planos` tem 1 plano** (Secretaria de Estado de Saúde, 2025). Os
  totais do PCA do Estado estão só nos indicadores da base agregada, e o
  resultado SQL avisa isso.
- **CNPJs da base:** os 810 de órgão e os 432 de fornecedor passam na
  conferência dos dígitos verificadores.

### Por que a modalidade dos contratos não é derivada

Todo contrato traz o número da compra que o originou
(`numero_controle_compra`), então dá para herdar a modalidade da compra
quando as duas estão no corpus. Medido: só **6** contratos sem modalidade têm
a compra de origem no corpus. A derivação não muda a cobertura, então a
lacuna fica documentada em vez de preenchida.

### Princípio: nulo honesto

Campo que a fonte não traz fica vazio; não recebe valor inventado nem zero.
É o que a resposta SQL diz ao usuário ("Campos ausentes não são zero. Esta
amostra não é um censo do RJ.") e o que explica respostas como "os registros
não informam a modalidade" nas perguntas P2.2, P4.2 e P4.3 do golden set: é
limitação da fonte, não falha da busca.

## Validação na consulta

- **CNPJ citado na pergunta:** aceito com ou sem pontuação, e os dígitos
  verificadores são conferidos (`structured_db.cnpj_valido`). Se não
  conferem, o cabeçalho do resultado SQL avisa ("dígito verificador inválido:
  confira o número"), para a resposta "nenhum registro" não parecer uma
  ausência real.
- **Resultado vazio:** a confiança vem do recorte da consulta. Um zero de
  execução completa sustenta "não há registros" na amostra; execução parcial
  limita a média e cálculo indisponível a baixa. O escopo vai em `avisos` e,
  no texto da resposta, a ressalva de que a ausência vale para a amostra, não
  para o universo do Estado.

## Compressão

O compressor do DUOPEN (zlib nível 6 em campos texto a partir de 64 bytes) foi
avaliado aqui, campo a campo:

| Onde | Medido | Decisão |
|---|---|---|
| `resposta_json` do histórico (`data/app_state/historico.sqlite`) | 35 respostas: 16,6 KB → 8,9 KB (−46%) | **aplicado** em `history_store.py`: o campo só é lido inteiro, nunca pesquisado |
| `objeto` em `contratacoes` | 706 campos: 202 KB → 138 KB (−32%), num banco de 991 KB | não aplicado: o SQL filtra com `objeto_normalizado LIKE ?` e o BM25 lê o texto; comprimido, a busca deixaria de encontrar |
| `documents.jsonl` | 19,1 MB → 2,1 MB (zlib) / 1,2 MB (XZ) | não aplicado: o Git já guarda os objetos comprimidos com zlib; só vale para entregar um pacote (`.tar.xz`) |
| `vectors.jsonl` (editais) | 38,6 MB → 15,1 MB (zlib) / 13,0 MB (XZ, 51 s) | não aplicado: para vetores, o caminho é quantizar (float16, PQ do FAISS), não compressão genérica |

O histórico grava a resposta já mascarada (CPF, e-mail, telefone) antes de
comprimir: compressão não é proteção de dado. Linhas antigas, gravadas como
texto, continuam legíveis.

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Endpoint do LLM da turma fora do ar | falha marcada como `erro_provedor`, separada de "sem evidência"; não conta como recusa no golden set |
| Limite de requisições do PNCP ou do endpoint | `retry.py`: backoff exponencial respeitando `Retry-After`; cache de embeddings em disco |
| Pergunta pedindo dado sensível (conta bancária, senha) | recusa por política (golden set G1 e G2) |
| Dado pessoal no histórico e no log | mascaramento antes de gravar; expurgo por `HISTORY_RETENTION_DAYS` e `LOG_RETENTION_DAYS` |
| Corpus mudar entre execuções | versões congeladas (v1…v6), `corpus_manifest.yaml` e relatórios de diferença em `data/inventory/` |
| SQLite e Neon divergirem | as duas bases são remontadas do mesmo `documents.jsonl` pelo mesmo código; na remontagem de 2026-10-09 foram comparadas linha a linha |
| Campo ausente lido como zero | nulo honesto (acima) e política de valores no resultado SQL |
