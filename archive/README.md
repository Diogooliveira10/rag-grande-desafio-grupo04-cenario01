# Arquivo — definições históricas de corpus

- `corpus_manifest_v6.yaml` — cópia **byte a byte** do manifesto da v6 (amostra
  de 5 arquivos por órgão por base, 1.400 arquivos). Movido para cá em
  2026-10-13, quando `corpus_manifest.yaml` passou a descrever o **censo v7**
  (ver `docs/PLANO_POSSIVEL_CORPUS_V7.md`). O SHA-256 do conteúdo é o mesmo
  registrado nos inventários v6 (`manifest_hash`), então a proveniência da v6
  segue íntegra.

Para reproduzir a coleta da v6:

```bash
py -m src.fetch_corpus fetch --manifest archive/corpus_manifest_v6.yaml --official
```
