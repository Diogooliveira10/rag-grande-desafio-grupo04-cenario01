# Inventário do corpus

## Snapshot: `v7-qwen-q8-2026-10-26`

[Baixar o pacote V7 no Google Drive](https://drive.google.com/file/d/1Zgt4b57sXZeGI0M6qz61ePk5h5SWh0vx/view?usp=drive_link).

- Snapshot: `v7-qwen-q8-2026-10-26`
- Versão SQL/corpus: `v7-reparo` (schema `7`).
- Construído em: `2026-10-26T17:45:00+00:00`
- Chunks: **144.112**; vetores: **144.112**; trechos sem vetor: **0**.
- `documents.jsonl`: **659.128.703 bytes** (aprox. 629 MB).
- SHA-256 dos documentos: `7c147a373c72c1ef35b5fe2a080ebb4e495b336d3555674c93f9dd08519a5290`.
- Banco: 106.667 linhas em `contratacoes` e 1 em `pca_planos`; SHA-256
  `0894af7df70c814d317208013fbe7abb61e1f6c59c4a95b4062a482710e1bac8`.
- Catálogo: `data/config/orgaos_corpus.json`, versão 3.
- Modelo da release: **Qwen3-Embedding-0.6B.Q8_0**, conforme confirmação do
  operador e comparação amostral de re-embedding. O aceite operacional da
  proveniência histórica está vinculado ao hash exato do índice no bundle;
  os registros históricos de auditoria não foram reescritos.
- `promocao_autorizada: true` nos manifests da release. O validador mantém as
  verificações de hashes, cobertura, IDs, FAISS, SQL e catálogo; sem o aceite
  vinculado ao índice, a exigência original de comprovação permanece.

## Chunks e vetores por base

| Base | Chunks | Vetores | FAISS aprox. | Formatos |
|---|---:|---:|---:|---|
| `contratos_estruturado` | 106.666 | 106.666 | 416,7 MiB | json |
| `atas_conversacional` | 34.953 | 34.953 | 136,5 MiB | json, pdf |
| `editais_normativo` | 1.557 | 1.557 | 6,1 MiB | html, pdf |
| `pca_agregado` | 911 | 911 | 3,6 MiB | json |
| `emails_sinteticos` | 25 | 25 | 0,1 MiB | json |
| **Total** | **144.112** | **144.112** | **563,0 MiB** | |

## Catálogo de órgãos e fornecedores

- **87 municípios** e 1.724 nomes de órgão/unidade.
- 12.798 grafias brutas de fornecedor (12.757 após normalização de espaços),
  agrupadas por raiz de CNPJ quando disponível. Há 10.345 raízes utilizáveis e
  1.106 identidades mantidas separadas sem raiz utilizável.
- **4.256 apelidos inequívocos** foram auditados; nomes comuns como “tecnologia”
  e “limpeza” não viram fornecedores isoladamente. CEDAE não é apelido de
  fornecedor, pois também identifica um órgão.
- As 4.256 formas foram exercitadas em perguntas de reconhecimento: nenhuma falha.
  Nos 47 casos de recuperação, nenhum nome de fornecedor espúrio foi introduzido.

## Integridade e avaliações

- O complemento adicionou **813 embeddings Q8 na GPU** (749 contratos e 64 atas)
  ao candidato anterior: 143.299 + 813 = 144.112. A checagem conferiu os textos,
  hashes, IDs, posições FAISS, SQL e catálogo, sem divergências.
- Na comparação isolada candidato_07 × candidato_08, com o mesmo catálogo e
  configuração de busca, os **47 rankings foram idênticos**. Hit@10: 0,8723;
  MRR@10: 0,7926. Isso não mede qualidade ponta a ponta nem compara com a v6.
- O golden original foi **35/36**. Após aprovação, somente a expectativa V1 foi
  corrigida para incluir 80 registros da mesma filial Cristália; a execução
  direcionada de V1 passou (1/1). **O golden completo não foi reexecutado após a
  alteração de V1.** Os 36 casos foram estruturalmente validados.
- 829 testes direcionados passaram; uma rodada separada de avaliação/catálogo
  também teve 125 testes aprovados. A suíte completa não foi executada.
- Evidências, limites, hash do pacote e instruções de reprodução:
  [`docs/REPARO_V7.md`](docs/REPARO_V7.md).

## Distribuição e ativação

Referência do pacote `V7-qwen-q8-promovida-2026-10-26.zip`:
3.101.448.480 bytes, 25 arquivos e SHA-256
`27423850743d43ce763e4fcebe93bcec6db0197c29582d2ad7d8eaa31f7b1b25`.
CRC verificado. Contém os caminhos de runtime completos, incluindo
`runtime_bundle.json`; não contém `.env` nem credenciais. Extrair na raiz, configurar
`SNAPSHOT_MANIFEST=runtime_bundle.json` no `.env` e reiniciar o backend.

Validação do bundle, teste de busca Q8, consulta SQL e ensaio de rollback
estão documentados em [`docs/REPARO_V7.md`](docs/REPARO_V7.md).

O `corpus_manifest.yaml` permanece byte a byte igual à política de coleta
`2026-10-13`; ele não seleciona snapshots de runtime. Como o coletor calcula o
hash sobre os bytes do YAML, a cópia de referência também está em
[`archive/corpus_manifest_v7_2026-10-13.yaml`](archive/corpus_manifest_v7_2026-10-13.yaml),
SHA-256 `63fcc506120968af65c0872684d7c33486b727a57741dcb64fecd55f1d921995`.
Não reescreva hashes históricos dos documentos ou inventários.
