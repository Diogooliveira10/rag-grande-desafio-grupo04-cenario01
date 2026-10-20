# Registro da execução do corpus v3

## Resumo

Em 28/09/2026 foi concluído o `update --official --use-discovered-contracts`, promovendo a versão **v3**. A execução materializou **1.200 contratos únicos** da seleção determinística disponível no checkpoint da descoberta RJ. A promoção passou pela validação oficial do manifesto e do inventário. **Embeddings e índices não foram executados.**

## Linha do tempo

Horários abaixo em UTC; entre parênteses, horário local de Rio Branco (UTC−5).

| Data e hora | Evento | Resultado |
|---|---|---|
| 28/09/2026 14:46:09 (09:46:09) | Início do `update` | Retomada do staging e checkpoint da v3 |
| 28/09/2026 14:46:14 (09:46:14) | Seleção fixada | 1.200 contratos; descoberta completa do universo: `false` |
| 28/09/2026 16:03:07 (11:03:07) | Coleta estruturada concluída | 1.201 artefatos estruturados no inventário; validação aprovada |
| 28/09/2026 16:03:28 (11:03:28) | Promoção concluída | Inventário oficial v3 completo, sem falhas |

A coleta da base Estruturada levou cerca de **1h16min53s** (`duration_ms=4.613.235`); o fluxo completo, do início à promoção, levou cerca de **1h17min19s**.

## Estratégia usada para contornar os impedimentos

- **Seleção sem esperar o censo RJ terminar:** foram usados os `SourceRef`s elegíveis já registrados, ordenados deterministicamente. A seleção ficou gravada no checkpoint e não mudou durante a retomada.
- **Escopo da coleta:** nesta execução, o provider buscou o detalhe `contrato` para cada ID selecionado. Consultas opcionais de anexos de contrato e de compra vinculada não foram necessárias para contar os contratos e haviam produzido indisponibilidades/redirecionamentos em tentativas anteriores.
- **Bases fora do escopo:** os arquivos aprovados de Normativa, Conversacional e Agregada foram reutilizados em staging a partir da v2, evitando repetir consultas e evitando depender do cadastro PCA ainda marcado `completo: false`.
- **Retomada:** o checkpoint da v3 preservou a seleção e os downloads já concluídos; registros com erro de tentativas anteriores foram descartados antes da validação.
- **Caminhos no Windows:** nomes físicos longos causaram `WinError 3`. O coletor passou a limitar o componente do nome do arquivo, mantendo o hash SHA-256, o identificador lógico e os metadados de origem. A identidade dos registros não depende do nome físico.

## Resultado do inventário

| Base | Estado v3 | Unidade lógica | Artefatos/arquivos | Volume observado |
|---|---|---:|---:|---:|
| `contratos_estruturado` | completa | **1.200 contratos únicos** | 1.201 | 1.203 registros contabilizados pelo inventário |
| `editais_normativo` | completa | 4 fontes normativas | 4 | 4 |
| `atas_conversacional` | completa | 20 atas | 40 | 20 |
| `pca_agregado` | completa | 16 itens PCA | 50 | 16 |
| **Total** | **completo** | — | **1.295 arquivos** | **1.243 itens** |

O inventário estruturado também contém uma listagem JSON de arquivos de contrato, retida de uma tentativa anterior, com três registros e zero unidades lógicas. Por isso o campo genérico `volume_observado` da base mostra 1.203; ele **não** representa 1.203 contratos. A contagem de contratos é feita pelas 1.200 unidades lógicas com `resource_kind=contrato`.

Na base Agregada, além dos 18 artefatos PCA (16 unidades lógicas e duas exportações CSV), ficaram 15 artefatos de itens e 17 de resultados que já haviam sido coletados com sucesso numa tentativa parcial anterior. Esses artefatos não aumentam o alvo lógico de 16 PCA.

Normativa e Conversacional foram preservadas da v2 nesta execução: as quatro fontes normativas continuam sendo quatro documentos gerais, e as 20 atas continuam representadas por metadados/listagens JSON. A promoção não significa que editais vinculados ou PDFs de anexos das atas tenham sido coletados agora.

## Conferências e validação

- 1.200 IDs na seleção persistida e 1.200 detalhes válidos com unidade lógica.
- 1.200 IDs PNCP distintos; os `SourceRef`s dos detalhes correspondem exatamente aos da seleção (nenhum ausente ou extra).
- Quatro bases oficiais presentes e marcadas como `complete`; zero falhas no inventário.
- A rotina de promoção verificou a presença dos arquivos e os hashes SHA-256 antes de mover o staging para `data/raw/v3`.
- `py -m py_compile src/fetch_corpus.py`, ajuda do subcomando `update` e `git diff --check` foram executados. O diff-check emitiu apenas avisos de conversão LF/CRLF.
- A suíte de testes não foi executada nesta tarefa.

## Limitações que permanecem

- A descoberta do universo RJ continua incompleta (`scan_complete=false`). Os 1.200 contratos são únicos e elegíveis dentro do conjunto descoberto e verificado, mas a v3 **não comprova cobertura de todos os contratos do RJ**.
- O alvo v3 das outras bases foi preservado; esta execução não ampliou sistematicamente as normas, os anexos das atas ou o censo PCA.
- Itens e resultados homologados não comprovam pagamento. Este corpus, por si só, não resolve uma pergunta sobre valores efetivamente pagos.
- Não houve extração/normalização nesta execução; a promoção refere-se ao corpus bruto validado.

## Arquivos de evidência

- Inventário promovido: [`data/inventory/v3.json`](../data/inventory/v3.json)
- Diferença v2→v3: [`data/inventory/DIFF_v2_v3.md`](../data/inventory/DIFF_v2_v3.md)
- Log da execução: `data/runs/update-20260928T144609Z-436900.jsonl` (arquivo local, fora do Git)
- Checkpoint: `data/inventory/checkpoints/v3.json` (arquivo local, fora do Git)
- Coletor: [`src/fetch_corpus.py`](../src/fetch_corpus.py)
