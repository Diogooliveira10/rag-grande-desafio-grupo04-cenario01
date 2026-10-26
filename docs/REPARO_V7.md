# Reparo incremental V7

## Pacote com índice SQL FTS

- [Baixar o ZIP com FTS e seu checksum no Google Drive](https://drive.google.com/drive/folders/14K1K-q9kze5qivG__b0KvkJV4L7buc1r?usp=drive_link).
- Arquivo local: `data/releases/V7-qwen-q8-promovida-2026-10-26-fts.zip`
  (**2.141.345.218 bytes**, 25 arquivos; CRC e hashes internos conferidos).
- SHA-256: `6a629419cf6ab18beb59b20835a45a78b7d027bfc6f761ff4cd31083d01165e5`.
  Checksum também em `data/releases/V7-qwen-q8-promovida-2026-10-26-fts.zip.sha256`.
- SQLite com FTS pronto e hash atualizado no `runtime_bundle.json` do pacote.
  Bundle validado com `ready: true`; documentos, vetores e dados SQL preservados.
- Extrair o ZIP na raiz do backend, configurar `SNAPSHOT_MANIFEST=runtime_bundle.json`
  e reiniciar a API; não é necessário executar `structured_db fts` nesse pacote.

## Release — `v7-qwen-q8-2026-10-26`

- Metadados da release original em `data/releases/v7-qwen-q8-2026-10-26/`,
  originada do candidato_08. Dados completos no pacote com FTS acima.
  Índice ativo em `index/`: **144.112 documentos e vetores**, sem lacunas.
  Modelo: **Qwen3-Embedding-0.6B.Q8_0**, confirmado pelo operador.
- A comparação amostral re-embedou 40 contratos com o checkpoint Q8 local:
  28/30 históricos e 10/10 controles foram idênticos; os outros dois históricos
  tiveram cosseno 0,999964 e 0,999955. Amostra aleatória com seed 20261027,
  estratificada entre 15 vetores do candidato, 15 recuperados e 10 complementares;
  hashes de texto conferidos. Relatório local:
  `.tmp/v7-q8-provenance-probe/comparison.json`.
- `runtime_bundle.json` identifica os documentos, SQLite, catálogo e todos os
  artefatos do índice por SHA-256. O aceite do operador é limitado à proveniência
  histórica desse snapshot e vinculado ao hash do manifesto do índice.
  Não modifica os resultados históricos de comprovação; sem esse aceite, o
  validador mantém a política estrita. Integridade e cobertura continuam obrigatórias.
- O bundle da release e o bundle ativo foram validados. `.env` local:
  `SNAPSHOT_MANIFEST=runtime_bundle.json`. `/ready` retornou **HTTP 200** em teste
  ASGI, identificando `v7-qwen-q8-2026-10-26`. A busca Q8 retornou três fontes
  normativas sem degradação; a consulta SQL de total em 2025 respondeu com fonte.
- **106 testes direcionados passaram**: snapshot, catálogo, observabilidade,
  integração da análise e logs. O golden completo não foi repetido por decisão
  do operador; os resultados anteriores permanecem abaixo.
- Backup verificável em `data/backups/promocao-v7-qwen-q8-2026-10-26/`.
  `backup.json` registra hashes e o ensaio de restauração em sandbox de índice
  e configuração. Para rollback com a API parada: preservar o índice promovido,
  restaurar `rollback/index/` e `rollback/.env` e remover `runtime_bundle.json`
  (não existia antes). SQLite, catálogo e documentos não mudaram na ativação.
  O backup de `.env` contém configuração privada e não deve ser distribuído.
- Pacote original sem FTS (referência histórica):
  `V7-qwen-q8-promovida-2026-10-26.zip`, **3.101.448.480 bytes**,
  25 arquivos, CRC conferido; SHA-256
  `27423850743d43ce763e4fcebe93bcec6db0197c29582d2ad7d8eaa31f7b1b25`.
  Extrair na raiz do backend, configurar `SNAPSHOT_MANIFEST=runtime_bundle.json`
  e iniciar ou reiniciar a API. O ZIP não inclui `.env`.

## Histórico — catálogo e cobertura vetorial (26/10/2026, antes da promoção)

O registro de 18/10 abaixo é histórico. Seu candidato original permanece intacto.
O complemento foi produzido separadamente em
`data/v7_recuperacao/2026-10-15/candidato_08_vetores_completos/`, **sem promoção**.
Nesse diretório permanecem os manifestos e relatórios; os dados completos
estão no pacote com FTS.

### Catálogo v3

- `data/config/orgaos_corpus.json` foi regenerado do SQLite v7 explicitamente,
  sem reconstruir o banco nem consultar o Neon. São 87 municípios e 1.724 nomes
  de órgão/unidade. As 12.798 grafias brutas de fornecedor tornam-se **12.757**
  após normalizar espaços, preservando o formato anterior da lista.
- Há 10.572 identificadores distintos de 14 dígitos; 45 têm raiz zero e não
  servem para agrupar empresas. O catálogo reúne **10.345 raízes de CNPJ**, com
  suas grafias e filiais; mantém separadas 1.106 identidades sem raiz utilizável
  (incluindo nomes sem documento). Não agrupa CPF, identificador estrangeiro com
  raiz zero nem homônimos de raízes distintas.
- Há **4.256 apelidos inequívocos** derivados da base, além das grafias completas
  por identidade. Todos foram exercitados em perguntas `Contratos da <apelido>`:
  **zero falhas de reconhecimento**. Marcas conhecidas mantêm compatibilidade;
  CEDAE fica fora dos apelidos e continua sendo órgão.
- Palavras genéricas/de objeto não disparam filtro de fornecedor sozinhas,
  mesmo após `empresa`. A estatística objeto × fornecedor fica congelada no
  catálogo, sem depender do banco escolhido pelo runtime. SQL e recuperação
  resolvem grafias da mesma raiz; CNPJ explicitamente citado continua exato.
- Nos 36 casos do golden, só V1 cita fornecedor pelo nome (Cristália), antes e
  depois. Nos 47 casos de recuperação, nenhum nome espúrio passou a ser fornecedor;
  deixou de ocorrer o falso filtro em R19 (`serviço` → SEBRAE). I03/I08 citam
  identificadores, não nomes. O cache de análise passa a incluir a assinatura
  das identidades/apelidos. A validação de catálogo confere também esses campos.

### Complemento de embeddings

- Foram gerados **813 vetores novos: 749 contratos e 64 atas**, usando
  `src.run_local_embeddings`, Qwen3-Embedding-0.6B.Q8_0 local, llama.cpp/Vulkan,
  `--gpu-layers all`, pooling `last`, sem instrução de documento, contexto/ubatch
  8.192 e 1.024 dimensões. O encoder de consultas confirmou **29/29 camadas na
  GPU**. O reranker permanece CPU (PyTorch instalado sem CUDA).
- SHA-256 do GGUF: `e40d4a708d0b324fc133bae547bd64dec79edb885410ef262c1e5c2efb2050b3`.
  O preflight dos 813 textos encontrou no máximo 1.601 tokens, sem truncamento.
  Entrada, cache, relatório e configuração estão em
  `data/v7_recuperacao/pendencias_v7_embeddings/`.
- `src.complete_vectors_v7` exige cobertura exata das lacunas, texto/hash,
  dimensão, finitude/norma, modelo/configuração e hash do checkpoint. Copia para
  destino novo, acrescenta os registros ao JSONL e ao FAISS na mesma ordem, sem
  re-embedar vetores anteriores. Recusa sobrescrita do destino.
- Resultado: **143.299 + 813 = 144.112 vetores**, um por documento. Atas:
  34.953; contratos: 106.666; normas: 1.557; e-mails: 25; PCA: 911.
  Conferência de todos os IDs, hashes dos arquivos e posições FAISS, SQL e catálogo
  **sem divergências**; os hashes de texto dos 144.112 documentos também foram
  conferidos. Evidência: `candidato_08_vetores_completos/complemento_vetores.json`.
- A lacuna de cobertura foi resolvida, **não a proveniência histórica incompleta**.
  A mistura anterior Q4/Q8 continua declarada; não se marca homogeneidade nem
  `proveniencia_encoding_completa` como verdadeiras. Isto não autoriza promoção.

### Auditoria local de proveniência (opção 1)

- A comparação por `chunk_id` entre os vetores do candidato_08 e uma cópia local
  preservada da referência v6 encontrou **4.490 vetores idênticos valor a valor**:
  1.997 atas, 1.557 editais normativos, 911 PCA e 25 e-mails. Os manifests dessas
  bases declaram Qwen3-Embedding-0.6B.Q8_0 e configuração disponível; todos
  apontam para o checkpoint SHA-256
  `e40d4a708d0b324fc133bae547bd64dec79edb885410ef262c1e5c2efb2050b3`.
  Hash do manifesto raiz da cópia: `ed472edc63edef66098f5447d0a36a4a239af1321093a6c5680e1d39093e074e`.
  Essa igualdade confirma os valores contra a referência preservada, mas não é
  atestação criptográfica independente do processo original de geração.
- Evidência por base (SHA-256 do manifesto de origem): atas
  `e30329063ec522b7e7e14c349f641572b86ac56a829c9514b1a140d5ef711634`;
  editais `fc0674e4fedfc967cefdb5943e26852b317c9983db4af07089c30a986bb701c9`;
  PCA `fccb83377b87522aec7022fd73a1eb9a155aba6bce2ab5ef27a160da6146fc7d`;
  e-mails `49430250bb30e2af55dfead7b700d68757ab89717a28d38de12dc3a16b37808b`.
  A cópia está em `.tmp/cde-commits/tree-C-4ae356d6/` e é local/ignorada pelo
  Git; os hashes registram o que foi inspecionado, mas não tornam os vetores
  fonte parte do pacote distribuível.
- Os 813 vetores novos também têm configuração identificada, porém diferente da
  referência v6: ambos usam o mesmo checkpoint Q8 e pooling `last`, mas os novos
  usaram contexto/ubatch 8.192, `flash-attn on` e 4 threads; a referência v6
  usou contexto 4.096, ubatch 2.048, `flash-attn auto` e 2 threads.
- Restam **138.809 vetores históricos sem configuração de encoding completa**:
  105.917 declarados como Q4_K_M, originários de `data/v7_direto/index`, e 32.892
  declarados Q8, originários do candidato_05. Os diretórios dessas fontes não
  estão presentes neste checkout; o manifesto do candidato registra
  `encoding_config` ausente para esses lotes. Logs disponíveis confirmam nomes
  de modelo/contagens, mas não vinculam a configuração completa a cada lote.
- Resultado: a opção 1 recuperou evidência parcial, mas **não libera o gate**.
  Permanecem `homogeneidade_modelo_comprovada: false`,
  `homogeneidade_configuracao_comprovada: false`,
  `proveniencia_encoding_completa: false` e `promocao_autorizada: false` no
  candidato. Não alterei o manifesto nem promovi a base. Para satisfazer a
  homogeneidade estrita, será necessário re-embedding dos lotes sem configuração
  suficiente e harmonização dos vetores com configurações diferentes, ou uma
  decisão formal de relaxar esse requisito, que não foi tomada.

### Avaliações e distribuição

- **829 testes direcionados passaram** na rodada final (62,09 s): catálogo,
  complemento, análise de perguntas, SQL, recuperação, snapshot e reparo.
  Não foi executada a suíte inteira.
- A comparação de busca usa o candidato_07 × candidato_08, **o mesmo catálogo
  novo** e configuração híbrida/filtros por regras, top/fetch 10, reranker local
  e consultas Q8 na GPU. Isola o acréscimo dos vetores; não compara contra a v6.
- **Busca: 47/47 empates**, sem regressões nem mudança nos rankings. Hit@1:
  0,7447; hit@10: **0,8723**; MRR@10: 0,7926, antes e depois. Relatórios:
  `eval/runs/pendencias-v7-busca-antes-2026-10-26/`,
  `eval/runs/pendencias-v7-busca-depois-2026-10-26/` e resumo/auditorias em
  `eval/runs/pendencias-v7-regressao-2026-10-26/`.
- **Golden completo original: 35/36**, com OpenRouter configurado
  (`xiaomi/mimo-v2.6-flash`). P4.3 passou. A única divergência foi V1: a união
  por CNPJ incluiu 80 registros acentuados da mesma filial Cristália
  (`44734671002286`) que o antigo filtro textual omitia. Conferência independente
  no SQLite, somando com Decimal: **842 registros, R$ 1.593.059.934,13**;
  antes, 762 e R$ 1.589.378.312,40. A diferença é R$ 3.681.621,73.
- Após aprovação explícita, **somente os dois números de V1 foram re-auditados**
  em `eval/golden_set.jsonl` (e o rótulo monetário correspondente). Alerta de
  qualidade, tolerância, fontes e outros 35 casos não mudaram. V1 foi executado
  novamente e **passou (1/1)**. Não houve nova rodada completa após a revisão;
  não apresentar isto como uma nova execução de 36/36. Mais 125 testes de avaliação
  e catálogo passaram; `eval.run_eval check` validou os 36 casos.
- Rodada original preservada em `eval/runs/pendencias-v7-golden-2026-10-26/`;
  rechecagem em `eval/runs/pendencias-v7-golden-v1-reauditado-2026-10-26/`.
  O golden original e a auditoria SQL estão em
  `eval/runs/pendencias-v7-regressao-2026-10-26/golden-original.jsonl` e
  `auditoria-v1.json`. A avaliação usa os documentos/índices do derivado e o
  SQLite v7 local (idêntico à cópia no derivado).
- Pacote anterior à release (histórico): `V7-dados-vetores-completos-2026-10-26.zip`
  (3.101.489.656 bytes; 25 arquivos de dados e um manifesto de hashes).
  Conferência CRC de todas as entradas: sem erro. SHA-256:
  `b7109e78073cf6c3177279c90dc86129c9919310980b55a4ef1ee4e2e6949c1c`.
  Dentro do ZIP, o catálogo já se chama `orgaos_corpus.json`. Para instalação, use o pacote da
  release indicado no início deste documento.

### Reprodução do complemento

Use destinos novos (os caminhos de saída abaixo são exemplos, não artefatos já gerados):

```powershell
python -m src.complete_vectors_v7 prepare `
  --candidato data/v7_recuperacao/2026-10-15/candidato_07_reparo `
  --destino data/v7_recuperacao/lote_novo
python -m src.run_local_embeddings --run `
  --input data/v7_recuperacao/lote_novo/faltantes.jsonl `
  --output data/v7_recuperacao/lote_novo/embeddings.jsonl `
  --model-path D:/LLM/Qwen3-Embedding-0.6B.Q8_0.gguf `
  --llama-exe D:/LLM/llama.cpp-vulkan/llama.exe `
  --gpu-layers all --context-size 8192 --ubatch-size 8192 --flash-attn on
python -m src.complete_vectors_v7 complete `
  --candidato data/v7_recuperacao/2026-10-15/candidato_07_reparo `
  --destino data/v7_recuperacao/candidato_novo `
  --embeddings data/v7_recuperacao/lote_novo/embeddings.jsonl `
  --catalogo data/config/orgaos_corpus.json
python -m src.complete_vectors_v7 verify `
  --candidato data/v7_recuperacao/candidato_novo
```

## Resultado dos seis itens (18/10, antes do complemento)

| Item | Resultado |
|---|---|
| 1. Metadados do candidato | Candidato separado em `data/v7_recuperacao/2026-10-15/candidato_07_reparo/`. Payloads usam o adaptador canônico de `src.index`, preservando sensibilidade, fontes, extras, vigência e hashes. |
| 2. Documentos × vetores | 144.112 documentos e 143.299 vetores identificados. Cada vetor usado foi comparado com sua posição FAISS de origem. **813 documentos ainda não têm vetor identificado: 64 atas e 749 contratos.** Permanecem no JSONL e podem participar da recuperação lexical; a cobertura densa não está completa. |
| 3. Herança | Reaproveitados 1.997 chunks históricos de atas e 25 e-mails sintéticos, além das normas/PCA já presentes. Sensibilidade dos e-mails preservada; seleção de herança vem do manifesto. |
| 4. SQL | Banco novo, schema `7`, corpus `v7-reparo`: 106.667 contratações, 1 PCA. Os 26.209 vínculos pendentes são a tabela histórica copiada do staging: 4.863 compras, 0 compras-pai presentes no candidato, 19.806 registros-filhos presentes e 0 pares pai/filho presentes juntos; `structured_query` não consulta essa tabela. Separadamente, 48.422 registros atuais têm `numero_controle_compra`: 28.590 encontram a compra-pai no candidato e 19.832 não. A deduplicação usa esse campo entre registros presentes, mas não recompõe os pais ausentes. O construtor preservou os 106.666 registros do staging e acrescentou 1 contrato da base agregada; oito colunas adicionais também foram copiadas. Atendimento valida/abre o banco sem reconstruí-lo durante a pergunta. |
| 5. Avaliação | Densa original preservada: 6 regressões. Estado atual: híbrido com query_analysis por regras e pool 10; **R24/R29/I03/I04 no top 1**. Restam 2 regressões estritas (R32/R33), com alternativas válidas descritas na [auditoria](AUDITORIA_RELEVANCIA_V7.md), sem alterar o golden. Nos 47 casos, hit@10 v6 0,8511 × v7 0,8085. O recorte também expôs limitações de interpretação em R15/R19. Relatório: `eval/runs/regressao-v7-candidato-07-hybrid-constraints-2026-10-18/REPORT.md`; [diagnóstico completo e experimentos](DIAGNOSTICO_REGRESSOES_V7.md). O perfil global de 50 não foi ativado. Rotas são as do gabarito, sem geração; não comparar configurações diferentes como se isolassem só o efeito do código. |
| 6. Filtros e relações 1:N | Filtros marcados como obrigatórios não são removidos quando zeram a busca. A supressão de compra/instrumento duplicado considera o mesmo recorte, sem apagar compra por vínculo fora do período; contratos e empenhos distintos permanecem separados. Fixtures cobrem 1:N e SQL suficiente sem chamada de modelo. |

### Problema adicional encontrado e recuperação

O payload de contratos do `candidato_05` tem **66.430 linhas**, embora seu FAISS contenha **106.666 posições**. Foram recuperados **39.487 vetores** de `data/v7_direto/index/`, usando ID, hash de conteúdo e comparação com a posição FAISS da fonte. As posições restantes não receberam IDs por suposição.

O manifesto do candidato_05 declara Q8, mas a linhagem do build confirma uma mistura: dos 108.385 vetores reaproveitados, 105.917 vieram de `v7_direto` (manifest declara Q4_K_M) e 2.468 da v6 (Q8); outros 33.641 foram embedados localmente com servidor Q8. No reparo atual, os 749 vetores novos de contratos não foram identificados; por isso o conjunto final contém 105.917 vetores de contratos da linhagem Q4, 32.892 atas embedadas localmente Q8 e 4.490 referências v6 Q8. Esses rótulos refletem manifest/logs de origem, não atestação criptográfica de cada vetor. A config da fonte Q4 está ausente e a config Q8 não está vinculada a todos os lotes. Não houve conversão nem re-embedding. O gerador agora declara o índice montado como `mixed`, registra contagens/fontes e distingue configuração disponível de encoding verificado. Inventário e evidências em `candidato_07_reparo/reparo.json`, `index/manifest.json`, `src/montar_faiss_v7.py` e `.tmp/v7-faiss-build-09.log`.

A primeira tentativa em `candidato_06_reparo` parou ao detectar o payload truncado. Ela é incompleta; **não usar**. O `candidato_05` e a v6 não foram sobrescritos.

## Código reaproveitado

- `src.index`: conversão de metadados, escrita FAISS e hash em streaming.
- `src.recuperar_corpus_v7`: destino novo protegido e relatórios com escrita atômica.
- `src.fetch_corpus.load_manifest`: política declarada de herança.
- `src.structured_db.construir_banco`: construção administrativa do SQLite, sem Neon.
- `eval.medir_regressao`, `eval.ablacao_rerank` e `src.retrieve`: execução/métricas comuns, sem recuperação concorrente exclusiva da V7.

A ferramenta administrativa `src.reparar_candidato_v7` faz a composição e reconciliação desses componentes; não cria outro coletor, cliente de modelos ou caminho de promoção.

## Reprodução — sempre em destino novo

```powershell
py -m src.reparar_candidato_v7 `
  --candidato data/v7_recuperacao/2026-10-15/candidato_05 `
  --destino data/v7_recuperacao/2026-10-15/candidato_08_reparo `
  --referencia-documents data/normalized/documents.jsonl `
  --referencia-index index `
  --manifest corpus_manifest.yaml `
  --fontes-index data/v7_direto/index `
  --modelo-declarado Qwen3-Embedding-0.6B.Q8_0 `
  --preparar-sql
```

O comando lê arquivos grandes, mas não coleta nem chama modelos. Recusa destino existente. O banco é criado em arquivo separado e validado contra o hash dos documentos; preserva campos/vínculos do staging e recusa divergências nos campos críticos.

O pacote pronto está em `candidato_07_reparo`; não é necessário executar novamente para utilizá-lo. O reparo depende também das fontes locais declaradas acima — o Git sozinho não contém todos esses artefatos.

## Validação realizada e limites

- No reparo inicial, **26 testes direcionados passaram em 3,99 s**. Nesta continuação, **8 testes focados passaram em 1,53 s** (filtros normativos/CNPJ, proveniência por origem e diagnóstico SQL); não foi rodada a suíte completa.
- Construção e conferência do SQLite real separado: **22,1 s**; a v6 e o staging original foram preservados.
- Inventário técnico e hashes: `candidato_07_reparo/reparo.json`, `sql_reparo.json` e `index/*/manifest.json`. O gerador `src.reparar_candidato_v7` agora registra vetores por origem e separa disponibilidade da configuração de verificação do encoding.
- Filtros extraídos da pergunta são passados ao executor como obrigatórios. Se não houver o campo aplicável, o chunk não satisfaz o filtro; filtros fora da natureza da base são ignorados (por exemplo, valor contratual em norma). Filtros manuais sem `optional: false` mantêm comportamento estrito já existente.
- Contagem por instrumento não é contagem de processos. A interpretação de perguntas ambíguas e a suficiência por intenção ainda exigem avaliação funcional; as fixtures não aprovam a qualidade de todas as respostas.
- O benchmark usa rotas do gabarito de recuperação; não mede roteamento, geração ou qualidade ponta a ponta. O novo caminho prioriza equivalência com produção; não há promessa de aceleração ou carregamento único dos índices.
- **Não promover:** faltam decidir o tratamento das 813 lacunas vetoriais, obter evidência externa de proveniência/configuração (ou re-embed autorizado), tratar as limitações de interpretação de R15/R19 e aprovar a auditoria de relevância de R32/R33 no `candidato_07_reparo`, ensaiar rollback integrado e aprovar a troca. A comparação híbrida com rerank já foi executada; ela não elimina as regressões restantes. A regressão densa atual não valida roteamento, geração nem recusas. Os 26.209 vínculos copiados são históricos e não aparecem como relações válidas entre pai/filho neste candidato; sua tabela preservada não é usada pelas consultas atuais.

[Plano do corpus V7](PLANO_POSSIVEL_CORPUS_V7.md) · [Índice da documentação](README.md)
