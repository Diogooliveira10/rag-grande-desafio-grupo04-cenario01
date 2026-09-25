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

### OCR de PDFs escaneados (opcional)

Parte das atas do PNCP é PDF escaneado, sem camada de texto. Com o Tesseract
instalado, `prepare_documents` reconhece essas páginas (status `ok_ocr` no
relatório); sem ele, elas continuam como `sem_texto` e não geram chunks.

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
# Modelo de português (requer PowerShell como administrador):
Invoke-WebRequest https://github.com/tesseract-ocr/tessdata/raw/main/por.traineddata `
  -OutFile "C:\Program Files\Tesseract-OCR\tessdata\por.traineddata"
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs   # deve listar "por"
```

O executável é procurado no `PATH` e em `C:\Program Files\Tesseract-OCR`; outro
local vai em `TESSERACT_CMD` no `.env`. O OCR leva alguns segundos por página,
mas o resultado fica no cache incremental da preparação.

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

Para documentos oficiais, passe a pasta bruta e o inventário da mesma versão.
Isso preserva a origem e a data de coleta associadas a cada arquivo:

```powershell
py -m src.prepare_documents `
  --input data/raw/v2 `
  --inventory data/inventory/v2.json `
  --manifest corpus_manifest.yaml `
  --output data/normalized/v2/documents.jsonl `
  --report data/normalized/v2/extraction_report.json
```

`--inventory` é opcional na interface de `prepare_documents`, mas deve ser
informado para preparar uma versão oficial. `--dry-run` resume a preparação sem
gravar o JSONL de documentos.

`src.ingest` continua disponível como fluxo compatível. Ele exige `--inventory`
por padrão; `--allow-uninventoried` libera explicitamente o modo legado de
diagnóstico. Para gerar documentos oficiais, prefira `prepare_documents` com o
inventário correspondente.

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

Prefira caminhos separados por versão. Exemplo para os documentos preparados
acima:

```powershell
py -m src.create_embeddings `
  --input data/normalized/v2/documents.jsonl `
  --output data/normalized/v2/embeddings/embeddings.jsonl `
  --report data/normalized/v2/embeddings/embedding_report.json
```

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
