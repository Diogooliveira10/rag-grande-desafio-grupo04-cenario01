# Recuperação v7 — relatório parcial de execução (15/10/2026)

**Status: candidato de staging. Não promovido.**

A v6 permanece ativa. Nenhum commit, push, upload de dados, troca de branch ou
reconstrução do banco ativo foi realizado. Fontes adquiridas e alterações locais
preexistentes foram preservadas. Este relatório **não autoriza promoção**.

Estratégia e limitações: [Coleta das bases e recuperação](COLETA_DAS_BASES_E_RECUPERACAO_V7.md).

## 1. Preservação e rollback

Execução local: `data/v7_recuperacao/2026-10-15/` (ignorada pelo Git).

- `baseline/`: 33 arquivos ativos copiados, **224.068.731 bytes**, hashes
  conferidos antes/depois da cópia; manifesto dos snapshots e patch das
  alterações locais anteriores à correção.
- `ensaio_rollback/`: restauração em sandbox a partir dessas cópias.
- SQLite restaurado: `PRAGMA integrity_check = ok`, **810 contratações**;
  `meta.source_sha256` confere com os documentos restaurados.
- FAISS restaurado: **5.115 vetores**, contagens/dimensões e linhas dos payloads
  conferidas por base.
- Verificação final da baseline: **íntegra, sem erros**, em
  `2026-10-15T23:29:15+00:00`.

O ensaio abriu os artefatos restaurados, mas **não iniciou a aplicação nem
executou perguntas via API**. Rollback integrado segue pendente.

Hashes das fontes originais, inalterados:

| Fonte | SHA-256 |
|---|---|
| Contratos/publicações adquiridos | `715507f4d1654fa3840487d79cb65b34d71a3228818cccdf77a44108adf69b53` |
| Atas adquiridas | `354ba5148a5adc60e46fe1710fa2674b8b319eabb881b8891cdc561a1b17affb` |

## 2. Correções implementadas

- `src/coleta_policy.py`: funções puras de UF, assinatura, identidade da unidade,
  compra vinculada, classificação do instrumento, recorte e janelas ≤365 dias.
- Provider padrão e modo direto compartilham os helpers. O provider legado
  mantém sua seleção antiga por padrão; não houve alteração silenciosa da v6.
- Coletor direto passa a consumir assinatura/período do manifesto na CLI;
  publicações exigem catálogo oficial fornecido, não lista fixa implícita.
- Atas na CLI exigem cadastro de unidades verificado; nome do órgão não resolve UF.
- Fingerprint de janela, política, manifesto, base e chunking: retomada incompatível
  é recusada antes de fazer append. Checkpoint legado não certifica o novo recorte.
- Janela é acumulada em temporário em disco, não em uma lista gigante em RAM.
- Teto de páginas, paginação contraditória ou página vazia antes do fim declarado
  produzem falha, não uma certificação de completude. Página curta não encerra a
  coleta quando o total informado ainda exige páginas posteriores.
- Ritmo padrão da CLI passou a 5 s; limite oficial fixo por minuto não foi presumido.
- Verificação de unidades do provider não se limita mais à interseção com as
  unidades já observadas nas publicações.
- Carga SQL respeita `extras.tipo_instrumento` quando explicitamente fornecido;
  legado sem essa marca mantém a compatibilidade até a promoção.
- Hash dos documentos em streaming. FAISS recebe blocos de 1.024 vetores para
  evitar uma segunda matriz contígua do corpus inteiro. A indexação ainda tem
  estruturas grandes em RAM: **não foi validada em escala nesta execução**.
- Escrita vetorial usa provisório, preservando o checkpoint anterior em falha.
  Retomada verifica configuração/modelo; não relabela um índice de outro modelo.
- `.gitignore` passou a proteger atas recuperadas e a pasta de recuperação.

## 3. Reclassificação offline

Foram produzidos candidatos independentes, sem sobrescrever seus antecessores:

- `candidato_01/`: apenas snapshots anteriores.
- `candidato_02/`: snapshots + publicações complementares.
- `candidato_03/`: anteriores + dez compras auxiliares resolvidas.

### Último candidato: `candidato_03`

| Tipo principal | Registros | Com modalidade |
|---|---:|---:|
| Contratos (termo inicial/carta contrato) | 15.561 | 11.079 |
| Empenhos | 31.814 | 16.843 |
| Outros instrumentos | 869 | 560 |
| Publicações de compra | 58.245 | 58.245 |
| Atas com unidade RJ observada localmente | 32.919 | 26.553 |
| **Total de chunks principais** | **139.408** | — |

A localização de parte das atas é **observacional**, derivada do vínculo com
unidades no snapshot, não uma auditoria cadastral integral.

- **20.119 atas** permanecem com território pendente.
- **4.838 compras distintas** ainda faltam para **26.128 vínculos** dos registros
  selecionados. Inclui atas, empenhos e demais instrumentos: não é denominador
  exclusivo de contratos.
- Categoria do processo permanece oficial e separada da modalidade.
- Dez compras auxiliares não foram contadas como publicações principais.
- Texto, identidade e hashes dos chunks reaproveitados foram mantidos;
  enriquecimento foi feito em `extras`. Nenhuma chamada de embedding necessária.
- Duas identidades de ata tinham revisões diferentes: selecionada a revisão
  declarada mais recente, com IDs e datas em `drift.jsonl`. Isso explica a
  diferença de uma ata RJ em relação à contagem preliminar por chunk.
- Exclusões têm referência ao arquivo/linha original em `quarentena.jsonl`.
  Referência auxiliar fora do universo principal não significa evidência inválida.

SQL de staging: `schema_version = v7-staging-1`, integridade `ok`,
**106.489 linhas**. Não substitui automaticamente o schema/contrato da aplicação.

SHA-256 de `candidato_03/documents.jsonl`:
`05d3ab64a88c7ec0d120cf69aec8aeda0db871f17d7f24619bd5e4f28ef7cd36`.

## 4. Pilotos de complementação

Todos os pilotos tiveram teto explícito de consultas, intervalo de 5 s, sem
embeddings nem acesso ao banco ativo. Total: **37 consultas HTTP ao PNCP**.

### Publicações omitidas

- Códigos 12, 13 e 14: **572 registros adquiridos**, três janelas completas,
  zero falhas, 14 consultas (inclui catálogo).
- Reconciliados: **572 identidades novas** em relação ao snapshot anterior.
- Efeito: modalidade derivada em mais **166 instrumentos** sem buscar cada compra.
- Demais códigos omitidos do catálogo atual (2, 10, 15–19): sete janelas
  concluídas, **zero registros**, oito consultas (inclui catálogo).

Todos os dez códigos omitidos foram consultados para publicação RJ/2025.
Os nove códigos da aquisição original **não foram novamente sincronizados**;
esta união versionada não prova ausência de drift entre instantes de coleta.

### Unidades oficiais

- Cinco órgãos consultados, todos com resposta utilizável.
- Onze unidades RJ catalogadas oficialmente.
- Não marcado como cadastro completo ou cobertura institucional comprovada.
- A lista antiga de órgãos foi reconciliada com os registros observados:
  `alvos_reconciliacao/alvos.json` contém **524 CNPJs-semente**, incluindo os que
  não têm unidade/resultado observado no snapshot. Ainda não é cadastro de todos
  os órgãos do RJ.

### Compras vinculadas

- Dez compras distintas consultadas: **10 resolvidas, zero falhas**.
- Efeito: modalidade preenchida em mais **25 instrumentos**.
- As compras permanecem como evidência auxiliar, inclusive quando publicadas
  em ano anterior. Não inflaram a contagem principal de publicações.

## 5. Validação do código e incidente de isolamento

Comando final (a variável `PYTHONDONTWRITEBYTECODE=1` foi definida no shell):

```powershell
python -m pytest -q -p no:cacheprovider -p tests.offline_guard tests --basetemp=.tmp/v7-recuperacao-suite-validada
```

Resultado: **1.703 testes passaram em 73,57 s**. `git diff --check` sem erros.
O plugin `tests.offline_guard` bloqueia DNS e sockets externos, permitindo apenas
loopback. Não é um teste do golden real nem de cobertura institucional.

**Incidente registrado:** a primeira execução ampla herdou o `.env` local:
1. Um teste de embedding esperava ECOA, mas selecionou o modelo local.
2. Um teste de SDK de geração desviou para OpenRouter em vez de usar o SDK falso.

Essa primeira execução terminou com 1.697 testes aprovados e dois reprovados.
O caminho OpenRouter foi acionado por esse teste; uso/custo dessa chamada não
foi aferido pelo teste. A seleção do provedor foi fixada nos dois testes, sem
alterar o `.env` ou a configuração de produção, e as execuções finais foram
feitas com rede externa bloqueada. Os pilotos de aquisição só chamam APIs PNCP.

## 6. O que não foi concluído

- Reconciliação cadastral de todos os alvos e descoberta institucional completa.
- Resolução oficial das atas territorialmente pendentes.
- Complementação de contratos/empenhos das novas unidades e publicações até a
  data de corte (inclusive publicação em 2026 de assinatura em 2025).
- Backfill completo das 4.838 compras pendentes; número pode mudar com a expansão.
- Incorporação das bases herdadas e compatibilidade final de schema/filtros/API.
- Proveniência completa dos vetores antigos e decisão sobre homogeneidade Q4/Q8.
- Construção e validação do FAISS candidato em escala.
- Regressão de retrieval e re-auditoria do golden de 29 casos.
- Ensaio de rollback com a aplicação funcionando.
- Relatório final e autorização explícita para promoção.

## 7. Próxima execução

**Atualização:** a continuação foi iniciada e está registrada em
[Cadastro oficial e reconciliação territorial](RELATORIO_CADASTRO_V7_2026-10-16.md).
A lista abaixo é o plano histórico deste primeiro relatório; não significa que
o lote seguinte ainda não tenha sido iniciado.

1. Expandir a verificação oficial de unidades a partir de `alvos_reconciliacao`,
   reaproveitando o cache do piloto em **novo destino** e com teto de consultas.
2. Revisar cadastro/falhas e construir janelas faltantes, sem tratar os 524 CNPJs
   como população estadual completa.
3. Completar aquisição em destino novo/resumível e reconstruir candidato, mantendo
   cada antecessor para comparação.
4. Refazer fila de compras, usar cache e executar lotes orçados até resolver ou
   justificar as pendências.
5. Só então integrar bases/índices e medir o pacote de promoção.

Exemplo de lote cadastral seguinte (não executado neste relatório):

```powershell
python -m src.complementar_corpus_v7 unidades --observadas data/v7_recuperacao/2026-10-15/alvos_reconciliacao/alvos.json --cache data/v7_recuperacao/2026-10-15/piloto_unidades --destino data/v7_recuperacao/2026-10-15/unidades_lote_02 --max-consultas 25 --intervalo 5
```

**Promoção bloqueada: relatório final + rollback integrado validado + decisão
explícita do usuário.** Não há comando de promoção nas novas ferramentas.
