# Guia da interface de linha de comando

Os comandos abaixo foram conferidos com as interfaces atuais em `src/`. Para
consultar todas as opções de um comando, acrescente `--help`.

## Preparar o ambiente

No PowerShell, na raiz do repositório:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

Typer e Rich fornecem a interface e a formatação de algumas CLIs. InquirerPy é
usado na revisão interativa de propostas. Para embeddings remotos, configure o
provedor por `.env` ou pelas opções `--provider`, `--endpoint` e `--model`.

## Coleta e versões do corpus

`fetch_corpus` tem os subcomandos `fetch`, `update` e `inventory`.

Para uma coleta inicial oficial ou para reutilizar uma versão completa com o
mesmo hash do manifesto:

```powershell
py -m src.fetch_corpus fetch `
  --manifest corpus_manifest.yaml `
  --official
```

Para consultar uma versão já coletada:

```powershell
py -m src.fetch_corpus inventory `
  --corpus-version v2 `
  --official
```

`update` requer uma versão oficial anterior e prepara a próxima. Primeiro, é
possível conferir a estimativa sem fazer requisições ou gravar arquivos:

```powershell
py -m src.fetch_corpus update `
  --manifest corpus_manifest.yaml `
  --official `
  --dry-run
```

Depois de revisar a estimativa, execute a atualização real:

```powershell
py -m src.fetch_corpus update `
  --manifest corpus_manifest.yaml `
  --official
```

O `dry-run` de `update` precisa de uma versão anterior. Sem `--official`,
`fetch` e `update` exigem `--data-root` e escrevem uma árvore isolada com
`raw/`, `inventory/` e `runs/` dentro dela. Por exemplo, uma tentativa pode usar
`data/attempts/demo`; esse caminho é opcional, ignorado pelo Git e não é criado
automaticamente como convenção. A coleta real consulta fontes remotas e grava
arquivos. `--official` opera em `data/` e não pode ser combinado com outro
`--data-root`.

As versões promovidas ficam em `data/raw/vN/`, com o inventário em
`data/inventory/vN.json`. `fetch` pode localizar e reutilizar uma versão
completa cujo hash do manifesto seja igual. `update` requer uma versão anterior e
cria a seguinte; a curadoria de uma proposta, por si só, altera somente o
manifesto, não cria uma versão do corpus.

## Curadoria e consulta de fontes

`src.source_catalog` encaminha para a CLI de curadoria. A descoberta e a busca
no PNCP fazem consultas remotas. Elas não incorporam resultados ao corpus por
conta própria.

Exemplo de busca remota:

```powershell
py -m src.source_catalog lookup pncp `
  --query "contratação de software" `
  --uf RJ `
  --year 2025 `
  --max-pages 5 `
  --max-results 20
```

Fluxo para propor uma fonte e revisar a mudança:

```powershell
py -m src.source_catalog discover --output data/proposals/discovery.json
py -m src.source_catalog propose `
  --manifest corpus_manifest.yaml `
  --discovery data/proposals/discovery.json `
  --output data/proposals/manifest-proposal.json
py -m src.source_catalog manifest-diff `
  --manifest corpus_manifest.yaml `
  --proposal data/proposals/manifest-proposal.json
py -m src.source_catalog review `
  --proposal data/proposals/manifest-proposal.json
```

`review` salva por padrão um arquivo terminado em `-reviewed.json`. Só use
`apply` depois de revisar a proposta: esse subcomando altera
`corpus_manifest.yaml`, pede confirmação interativa e verifica o hash do
manifesto. Para evitar o prompt existe `--yes`.

```powershell
py -m src.source_catalog apply `
  --manifest corpus_manifest.yaml `
  --proposal data/proposals/manifest-proposal-reviewed.json
```

`lookup` consulta somente o PNCP neste momento. O termo informado é usado para
filtrar localmente os resultados paginados. Os resultados não entram no corpus
até serem curados e coletados por `fetch_corpus` ou `update`.

## Preparar documentos

Para preparar uma base usando sempre a versão completa mais recente, informe
`--latest` e o identificador da base. A CLI seleciona o inventário de maior
versão em `data/inventory/`, valida seu vínculo com o manifesto atual, lê os
arquivos correspondentes de `data/raw/vN/` e grava os resultados em
`data/normalized/vN/<base-id>/`:

```powershell
py -m src.prepare_documents `
  --latest `
  --base-id contratos_estruturado `
  --manifest corpus_manifest.yaml
```

O relatório registra `corpus_version`, o inventário utilizado e o hash do
manifesto. Se a versão mais recente estiver incompleta, sem arquivos brutos ou
vinculada a outro hash de manifesto, a CLI para com uma mensagem clara; ela não
recua para uma versão antiga. `--dry-run` resume a preparação sem gravar os
arquivos normalizados.

Para reproduzir uma versão histórica específica, ainda é possível informar
`--input`, `--inventory`, `--output` e `--report` manualmente. Use sempre o
inventário e os arquivos brutos da mesma versão.

`src.ingest` continua disponível como fluxo compatível. Ele exige `--inventory`
por padrão; `--allow-uninventoried` libera explicitamente o modo legado de
diagnóstico. Para documentos oficiais, prefira `prepare_documents --latest`
com o `--base-id` da base desejada.

## Busca local no inventário

```powershell
py -m src.corpus_search search local `
  --corpus-version v2 `
  --query "edital 90012" `
  --base-id editais_normativo
```

Essa CLI pesquisa metadados do inventário — como `logical_id`, base, origem,
caminho, tipo e hash. Ela não pesquisa o texto extraído de PDFs ou chunks.

## Gerar embeddings

Use o mesmo valor de `corpus_version` registrado pelo comando
`prepare_documents --latest` e a pasta da base preparada. Exemplo:

```powershell
py -m src.create_embeddings `
  --input data/normalized/vN/contratos_estruturado/documents.jsonl `
  --output data/normalized/vN/contratos_estruturado/embeddings/embeddings.jsonl `
  --report data/normalized/vN/contratos_estruturado/embeddings/embedding_report.json
```

Substitua `vN` pelo valor informado na preparação. Assim, os embeddings e o
relatório permanecem junto da mesma versão dos documentos.

O arquivo informado em `--output` também é o cache. Vetores são reutilizados
quando o conteúdo, metadados relevantes, esquema, provedor, endpoint e modelo
correspondem. Uma execução concluída regrava esse JSONL com a materialização da
entrada atual; repetir um caminho de saída com outra entrada substitui o
conjunto anterior. O CLI não separa versões automaticamente.

Sem opções, a entrada padrão é `data/normalized/documents.jsonl` e a saída
padrão é `data/embeddings/embeddings.jsonl`, compartilhada entre execuções. O
relatório padrão fica ao lado da saída. Escolha caminhos explícitos para não
misturar materializações de versões diferentes.

`--dry-run` não chama o endpoint nem grava o JSONL de embeddings, mas grava um
relatório. O padrão usa o provedor `ecoa`, lotes de 64 e uma pausa de 10 segundos
entre lotes. Os provedores aceitos são `ecoa`, `ollama`, `openai` e `custom`.

## O que estas CLIs ainda não fazem

`corpus_search` faz busca em metadados; não é a busca híbrida do RAG. O
repositório ainda não tem uma CLI integrada de roteamento adaptativo, busca
densa + BM25, fusão RRF, reranking, geração de resposta com citações, aplicação
dos guardrails descritos no enunciado ou histórico/feedback do usuário. Veja o
[status da arquitetura](../ARQUITETURA.md).
