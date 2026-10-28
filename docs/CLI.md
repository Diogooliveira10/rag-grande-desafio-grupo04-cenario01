# Guia da interface de linha de comando

Os comandos abaixo foram conferidos com as interfaces atuais em `src/`. Para
consultar todas as opções de um comando, acrescente `--help`.

Coleta, inventário, ingestão/preparação, embeddings e indexação registram o
contrato comum em `logs/pipeline/pipeline-AAAA-MM-DD.jsonl`, inclusive operações
dry-run válidas. Cada execução recebe IDs próprios (`query_id` sintético,
`origem=offline`), sem sessão de chat. Relatórios e logs de coleta em `data/runs/`
continuam existindo. Lotes de embedding indicam se os tokens foram reportados,
reutilizados por cache ou não informados. O logging não altera os retornos da
CLI nem dispara chamadas extras ao modelo. Veja [OBSERVABILIDADE.md](OBSERVABILIDADE.md).

## Snapshot oficial v6

A v6 é a versão ativa: 1.400 arquivos, cinco arquivos por órgão/base no máximo
e 5.090 chunks normalizados e indexados após a recuperação OCR de 2026-10-07,
com zero trechos ilegíveis (ver `py -m src.corpus_quality` no README).
Com os 25 chunks sintéticos, o índice ativo contém 5.115 chunks. As
referências a v3 nas seções de coleta abaixo são exemplos históricos; para
consultar o snapshot pronto, use a CLI existente:

```powershell
python app/cli.py rota "Quais fornecedores receberam o maior valor total em contratos no conjunto de dados de 2025?"
python app/cli.py perguntar "Quais fornecedores receberam o maior valor total em contratos no conjunto de dados de 2025?"
```

Configure as credenciais ECOA no `.env` a partir de `.env.example`. A recuperação
usa `index/`, `data/normalized/documents.jsonl` e o SQLite em `data/structured/`.
Os vetores documentais já estão armazenados: essas consultas enviam ao ECOA a
pergunta e o contexto para resposta, sem regerar os embeddings dos documentos.
O cache de queries é local e não versionado. O snapshot não comprova que uma
licitação de 2025 continue aberta hoje.

Para conferir o corpus materializado e compilar os exemplos PostgreSQL offline:

```powershell
python eval/runs/audit_v6.py
python eval/runs/compile_sql_v6.py
python -m pytest -q
```

A auditoria completa também exige os arquivos brutos referenciados pelo
inventário, que permanecem fora do Git. A compilação SQL não conecta ao
PostgreSQL, não executa comandos e não aplica DDL. O caminho de cálculo já
existente nas consultas usa SQLite. Para preservar esse comportamento local,
deixe a configuração de PostgreSQL/Neon desativada no ambiente.

Os scripts de execução foram preservados em `eval/runs/`: `discover_v6.py`
descobre fontes, `curate_v6.py` aplica a seleção e os limites, e `pipeline_v6.py`
prepara, embeda, indexa e avalia. São os scripts da execução registrada, com
pré-requisitos nos relatórios de descoberta/curadoria; não execute a curadoria
novamente sobre a v6 já promovida. Uma nova coleta precisa de nova versão e dos
arquivos brutos das fontes anteriores para reutilização. A avaliação ECOA é uma
integração explícita, separada dos testes offline, com pausas entre perguntas.

Para testar a compatibilidade local/ECOA antes de uma nova geração:

```powershell
python src/run_local_embeddings.py --compatibility-test `
  --input data/normalized/v6/contratos_estruturado/documents.jsonl `
  --threads 2 --gpu-layers all --context-size 4096 --ubatch-size 2048 `
  --report eval/runs/embedding-compatibility-v6.json
```

O modelo local padrão é `D:\LLM\Qwen3-Embedding-0.6B.Q8_0.gguf`; ajuste
`--model-path` e `--llama-exe` conforme o ambiente. Q8 local e Q4 ECOA são
artefatos distintos. O melhor perfil medido usou lote 16, duas threads e Vulkan;
os resultados são específicos da máquina registrada, não uma garantia de
velocidade em outro ambiente. Sem `--output`, a geração organiza os vetores em
pastas com data/hora e base. Para retomar, informe o JSONL já existente.

Os ZIPs versionados em `atas-zip/` contêm somente os 40 PDFs de atas da v6 e seus
40 Markdown correspondentes. Os scripts `export_atas_v6_md.py` e
`zip_atas_v6.ps1 -Markdown` preservam os caminhos relativos e recusam sobrescrever
uma entrega existente. Consulte [o registro da v6](registro_execucao_v6.md)
para distribuição, hashes, avaliação factual e pendências.

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

### Acentos no terminal do Windows

No Windows em português, o Python escreve em cp1252 e o PowerShell lê em cp850;
com pipe ou redirecionamento (`| Select-Object`, `> saida.txt`), "Preços" vira
"Pre�os". Use UTF-8 nos dois lados, na sessão atual:

```powershell
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8
```

Para deixar permanente, defina `PYTHONUTF8=1` nas variáveis de ambiente do
usuário e, no VS Code, crie um perfil de terminal PowerShell que rode a segunda
linha ao abrir (`terminal.integrated.profiles.windows`, com `-NoExit -Command`).

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

`fetch_corpus` tem os subcomandos `fetch`, `update`, `discover`, `discovery` e `inventory`.

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

Para consultar as referências que a descoberta já registrou no checkpoint, sem
fazer novas chamadas ao PNCP:

```powershell
py -m src.fetch_corpus discovery `
  --checkpoint-version v3 `
  --population contrato_assinado_rj_unidades `
  --query 42498600000171 `
  --limit 20 `
  --official
```

`discovery` retorna o estado do censo e referências PNCP que correspondem à
busca textual em seus IDs e campos. Sem `--query`, lista referências em páginas
de até 500 resultados; use `--offset` para avançar. O comando é somente leitura:
não chama a API, não baixa recursos e não promove versão. Ele não consulta o
conteúdo completo dos contratos; isso exige a coleta posterior.

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

Para promover uma atualização usando a seleção determinística de contratos já
encontrados pela descoberta RJ, sem esperar o censo completo:

```powershell
py -m src.fetch_corpus update `
  --manifest corpus_manifest.yaml `
  --official `
  --use-discovered-contracts
```

Essa opção exige ao menos o alvo de contratos no checkpoint retomável e escolhe
os IDs PNCP em ordem estável. A execução coleta os detalhes dos contratos e
reutiliza em staging os arquivos já promovidos das demais bases, sem refazer
suas consultas nem baixar vínculos opcionais do contrato. Como a descoberta pode estar incompleta, a v3 representa o conjunto
selecionado, não uma afirmação de cobertura integral do RJ. A promoção continua
sujeita à validação normal do manifesto e do inventário.

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

Para revisar as fontes da base Normativa existente e preparar também a coleta
de editais/termos de referência ligados a um inventário Estruturado:

```powershell
py -m src.source_catalog normative-proposal `
  --manifest corpus_manifest.yaml `
  --output data/proposals/normativa-proposal.json
py -m src.source_catalog manifest-diff `
  --manifest corpus_manifest.yaml `
  --proposal data/proposals/normativa-proposal.json
py -m src.source_catalog review `
  --proposal data/proposals/normativa-proposal.json
py -m src.source_catalog apply `
  --manifest corpus_manifest.yaml `
  --proposal data/proposals/normativa-proposal-reviewed.json
```

`normative-proposal` atualiza a base oficial existente, sem criar outra base.
Ela propõe Lei 14.133/2021 e LC 123/2006 do Planalto, os Decretos RJ
48.816/2023 e 48.820/2023 da CGE-RJ, seus metadados temporais e o filtro de
anexos PNCP para edital/termo de referência. A proposta também permite usar
`fetch` em modo experimental, limitado à base Normativa, com um inventário
Estruturado anterior como origem dos `SourceRef`s:

```powershell
py -m src.fetch_corpus fetch `
  --manifest corpus_manifest.yaml `
  --data-root data/attempts/normativa `
  --base-id editais_normativo `
  --source-inventory data/inventory/v2.json
```

Esse percurso baixa as quatro normas fixas e segue, pelo provider PNCP
existente, somente os anexos de edital/termo de referência dos registros
Estruturados encontrados no inventário informado. Ele grava uma tentativa
isolada, não promove uma versão oficial e não inicia o censo Estruturado. Sem
`--source-inventory`, o mesmo comando coleta apenas as quatro normas fixas.

O mesmo percurso isolado aceita `pca_agregado` para usar referências da base
Estruturada e, se presentes, referências PCA do inventário informado. Ele
consulta todos os SourceRefs de contratos e PCA presentes no inventário, busca
itens e resultados homologados das compras vinculadas e reaproveita os PCA já
inventariados como valores planejados. Também conserva o detalhe do contrato
como contexto não contável para manter a data de assinatura ligada aos
resultados. Esse percurso não exige que o cadastro
de CNPJs esteja marcado completo: cobre apenas as fontes presentes no inventário
e não executa o censo PCA. Também não promove versão para a árvore oficial; a
saída continua sendo uma tentativa experimental sob `--data-root`:

No `pca_agregado`, detalhes e itens consultam o PNCP e usam Dados Abertos do
Compras.gov.br como fallback para falhas de transporte. Resultados homologados
usam diretamente a consulta oficial por identificador da compra, que retorna
os resultados de todos os itens em lote; isso evita uma chamada separada por
item. O inventário registra `acquisition_backend` e a URL efetivamente usada.
Esse percurso cobre apenas os registros PNCP 14.133 disponíveis na API
alternativa para essa consulta em lote.

```powershell
py -m src.fetch_corpus fetch `
  --manifest corpus_manifest.yaml `
  --data-root data/attempts/agregada `
  --base-id pca_agregado `
  --source-inventory data/inventory/v3.json
```

Para esse percurso, o inventário precisa conter referências PNCP de
`contratos_estruturado` e pelo menos o número de `SourceRef`s PCA definido no
alvo de `pca_agregado`. Ao preparar essa tentativa, o `prepare_documents`
materializa indicadores de resultados homologados com média simples, média
ponderada pela quantidade homologada, mediana, quartis, mínimo, máximo,
quantidade total e valores totais informado/calculado. Os grupos preservam a
descrição e a unidade como publicadas, além de município, esfera, modalidade e
ano de assinatura do contrato; cada grupo também inclui uma série mensal por
data do resultado e contagens de observações, compras, itens, contratos
vinculados e fornecedores. Um
indicador separado informa quantas compras vinculadas chegaram a detalhes,
itens e resultados, distinguindo respostas vazias de falhas.

As médias e os demais preços são de **valores unitários homologados**, não
pagamentos. A média ponderada usa quantidade homologada; produtos com
descrições ou unidades diferentes não são convertidos nem tratados como
equivalentes. Os indicadores de PCA permanecem identificados como valores
planejados e não são misturados aos resultados homologados.

### Atas de registro de preço vinculadas à Estruturada

Para seguir as compras da Estruturada e coletar suas atas e anexos PNCP sem
iniciar uma busca global de atas, use o percurso isolado:

```powershell
py -m src.fetch_corpus fetch `
  --manifest corpus_manifest.yaml `
  --data-root data/attempts/conversacional-pncp `
  --base-id atas_conversacional `
  --source-inventory data/inventory/v3.json
```

O coletor usa os `SourceRef`s de `contratos_estruturado`, consulta as atas de
cada contratação vinculada, resolve a identidade PNCP de cada ata e coleta
metadados, listagem de arquivos, contratos vinculados e documentos anunciados
(incluindo PDFs). Os artefatos de ata preservam o `SourceRef` da própria ata;
os documentos preservam a relação com essa identidade. A execução é uma
tentativa isolada e retomável, não promove uma versão oficial. Ela não consulta
SIGA e não inclui e-mails sintéticos; estes precisam ser incorporados a partir
dos arquivos fornecidos pelo desafio.

### Materializar as camadas da Agregada

Para uma versão já coletada, materialize fatos e indicadores em JSONL usando
somente os arquivos apontados pelo inventário. O comando não acessa a rede nem
promove uma versão. `source/` continua sendo o conteúdo original em `raw/`;
este comando grava apenas as camadas derivadas sob um diretório da mesma versão:

```powershell
py -m src.materialize_pca_aggregate `
  --inventory data/inventory/v3.json `
  --project-root . `
  --output data/derived/v3/pca_agregado
```

O resultado contém `facts/pca_items.jsonl` e
`facts/homologated_items.jsonl`, além de `aggregates/pca_planned.jsonl` e
`aggregates/homologated_item_prices.jsonl`. O relatório registra contagens,
hashes e a versão/hash do manifesto de origem. PCA continua sendo valor
planejado; resultados são valores unitários homologados. As descrições e
unidades permanecem como publicadas.

Para preparar documentos de consulta a partir dos indicadores materializados,
aponte `prepare_documents` somente para a pasta `aggregates/`:

```powershell
py -m src.prepare_documents `
  --manifest corpus_manifest.yaml `
  --input data/derived/v3/pca_agregado/aggregates `
  --base-id pca_agregado `
  --output data/normalized/v3/pca_agregado/documents.jsonl `
  --report data/normalized/v3/pca_agregado/extraction_report.json
```

Assim, os fatos ficam auditáveis e os documentos preparados contêm os
indicadores, sem enviar cada linha de fonte bruta ao RAG. Use os caminhos da
versão candidata em `data/attempts/agregada/` antes da promoção, ou os caminhos
versionados de `data/` para uma versão já promovida. Este fluxo não gera
embeddings.

Quando uma coleta isolada da Agregada ou da Conversacional terminar e o inventário
experimental estiver concluído, promova seus artefatos para a próxima versão
oficial sem repetir a coleta das outras bases. Para a Agregada:

```powershell
py -m src.fetch_corpus update `
  --manifest corpus_manifest.yaml `
  --official `
  --adopt-attempt data/attempts/agregada_lote/inventory/v1.json
```

Para a Conversacional:

```powershell
py -m src.fetch_corpus update `
  --manifest corpus_manifest.yaml `
  --official `
  --adopt-attempt data/attempts/conversacional_pncp_v2/inventory/v1.json
```

O comando exige uma tentativa completa e confere no checkpoint a cobertura
específica da base. Na Agregada, cada contrato selecionado precisa ter detalhe,
itens e consulta de resultados bem-sucedidos. Na Conversacional, cada compra
selecionada precisa ter consulta de atas bem-sucedida; as atas encontradas devem
ter metadados e listagem de documentos, e todos os documentos listados precisam
ter arquivo baixado. Depois, os arquivos são copiados para o staging oficial,
as demais bases são carregadas da versão anterior e a validação/promoção normal
é executada. Uma tentativa incompleta não é promovida.

### Carregar a Agregada em PostgreSQL/Neon

Os JSONL derivados permanecem como snapshot versionado local. Para consultá-los
em PostgreSQL/Neon, use o cliente `psql` instalado no ambiente; o carregador não
adiciona driver Python e não substitui os arquivos da versão. Defina `DATABASE_URL`
no ambiente sem colocar a credencial no comando ou no repositório. A carga cria o
schema `pca_agregado`, preserva a versão em cada linha e é idempotente para IDs já
carregados. A view `pca_agregado.homologated_items` expõe campos tipados para SQL
analítico.

Valide localmente os arquivos antes de conectar:

```powershell
py -m src.load_pca_aggregate_postgres `
  --input data/derived/v4/pca_agregado `
  --dry-run
```

Depois da revisão do relatório, carregue na instância apontada por `DATABASE_URL`:

```powershell
py -m src.load_pca_aggregate_postgres `
  --input data/derived/v4/pca_agregado
```

Exemplo de consulta para inspecionar resultados homologados, sem interpretá-los
como pagamentos:

```sql
SELECT municipio, modalidade,
       COUNT(*) AS observacoes,
       AVG(valor_unitario_homologado) AS media_simples,
       SUM(valor_unitario_homologado * quantidade_homologada)
         / NULLIF(SUM(quantidade_homologada), 0)
         AS media_ponderada
FROM pca_agregado.homologated_items
WHERE corpus_version = 'v4'
  AND ano_assinatura = 2025
  AND unidade_medida ILIKE '%kg%'
  AND descricao ILIKE '%arroz%'
GROUP BY municipio, modalidade
ORDER BY municipio, modalidade;
```

O `--dry-run` valida os JSONL e mostra contagens sem conexão de rede. A carga
real no Neon é uma operação separada; ela não promove o corpus nem altera os
artefatos versionados.

Para extrair os documentos de uma tentativa, use os caminhos da versão e do
inventário informados no resumo de `fetch`:

```powershell
py -m src.prepare_documents `
  --manifest corpus_manifest.yaml `
  --input data/attempts/normativa/raw/v1 `
  --inventory data/attempts/normativa/inventory/v1.json `
  --base-id editais_normativo `
  --output data/attempts/normativa/normalized/v1/editais_normativo/documents.jsonl `
  --report data/attempts/normativa/normalized/v1/editais_normativo/extraction_report.json
```

Substitua `v1` pela versão experimental retornada pelo coletor. Para uma versão
oficial completa, `prepare_documents --latest` continua sendo o fluxo padrão.

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

Para gerar documentos localmente com o checkpoint dedicado, use uma base por
execução:

```powershell
python -m src.run_local_embeddings --run `
  --input data/normalized/v5/contratos_estruturado/documents.jsonl
```

Sem `--output`, esse comando grava em
`data/embeddings/AAAA-MM-DD_HH-MM-SS_microssegundos/<base>/embeddings.jsonl`.
O relatório e os checkpoints parciais ficam junto do JSONL. Para retomar uma
execução, informe em `--output` o mesmo caminho já criado. Para agrupar as quatro
bases na mesma pasta datada, informe esse caminho explicitamente para cada base.
O padrão local usa Qwen3-Embedding Q8, pooling `last`, contexto 4096, quatro
threads, lote de um texto e pausa zero. Todos os textos são validados contra o
limite de tokens antes da geração; documentos não recebem instrução de query.

A configuração mais rápida medida nesta máquina (RTX 3050 Laptop) foi:

```powershell
python -m src.run_local_embeddings --run `
  --input data/normalized/v5/contratos_estruturado/documents.jsonl `
  --threads 2 --gpu-layers all --parallel 1 --ubatch-size 2048 `
  --batch-size 16 --pause-seconds 0 --flash-attn auto
```

O contexto continua em 4096 tokens por slot, mas o lote interno de 2048 exige
textos com menos de 2048 tokens. `token_validation.json` registra a validação
completa da base; um texto fora do limite interrompe a geração antes do primeiro
lote. O benchmark e a comparação com ECOA estão em `eval/runs`.

## O que estas CLIs ainda não fazem

`corpus_search` faz busca em metadados; não é a busca híbrida do RAG.
Os comandos `python app/cli.py rota "Pergunta"` e
`python app/cli.py perguntar "Pergunta"` usam o pipeline de consulta existente.
Esta etapa ainda não entrega cross-encoder com ablação, histórico/feedback
persistidos, execução PostgreSQL ou a trilha real de e-mails do desafio. Veja o
[status da arquitetura](../ARQUITETURA.md).
