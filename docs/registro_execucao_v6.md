# Corpus v6 e avaliação final

Entregas: [ZIP dos PDFs](../atas-zip/atas_conversacional-v6.zip) e [ZIP dos Markdown](../atas-zip/atas_conversacional-v6-md.zip). Ambos validados, 40 entradas cada, preservando caminhos relativos. O Markdown corresponde aos mesmos 40 PDFs: 542 páginas extraídas, separadas por página, com URL pública e SHA-256 do PDF. Extração existente, sem geração por LLM; a disposição visual do PDF pode não ser preservada. Hashes e conferência com inventário em `zip-atas-v6*.json`.

## Distribuição e proveniência

Inventário `data/inventory/v6.json` completo, 1.400 arquivos: contratos 625, normas/editais 13, atas 376, agregada 386. Formatos: 1.347 JSON, 49 PDF, 4 HTML. Esferas: municipal 804, federal 513, estadual 83. 166 CNPJs e quatro identificadores de publicadores normativos. Limite máximo confirmado: cinco arquivos por órgão e por base, inclusive federal. JSONs das atas não estão nos ZIPs solicitados, restritos aos PDFs e seus Markdown.

A distribuição estadual permanece menor: não foi preenchida com repetição de unidades do mesmo CNPJ. Trata-se de amostra intencional, sem garantia de censo ou equilíbrio exato entre esferas. Agregada contém registros relacionados e indicadores; apenas um PCA confirmado, da Secretaria de Saúde estadual. Vários itens não fornecem preço unitário ou modalidade licitatória. Snapshot principalmente 2025; atas podem ter assinatura anterior e vigência em 2025. Não representa licitações abertas em setembro de 2026.

URLs HTTP públicas em `origem`; caminhos relativos `source_file` servem somente à rastreabilidade interna, não como links de conteúdo. Removidos aliases e repetições de metadados comerciais. Campos reais do JSON prevalecem sobre contexto herdado. Hashes, versão e relações preservados até recuperação. Fontes indisponíveis tiveram tentativas limitadas e foram registradas/puladas; detalhes em `v6-curation/coverage-warnings.jsonl`. Nenhuma exclusão de disco, instalação, envio ou upload efetuado nesta etapa. Versões anteriores e backups de índices, vetores, arquivos brutos e SQLite preservados.

## Embeddings e índices

Pasta `data/embeddings/2026-09-30_12-08-51`, organizada pelas quatro bases, com 5.621 vetores de dimensão 1.024, finitos e não nulos. Quantidades: contratos 625, normas 1.561, atas 2.524, agregada 911. Os relatórios iniciais registram 2.643 vetores novos e 2.976 reutilizados. Há uma diferença de dois vetores na contabilidade inicial de normas (1.561 chunks, mas 843 reutilizados + 716 gerados); a auditoria final verificou todos os 5.621 vetores, sem ausência. Essa discrepância do relatório inicial fica registrada. Reprocessamento dos metadados reutilizou todos os 5.621, sem novas chamadas de documentos. FAISS e BM25 persistidos a partir dos vetores armazenados.

Modelo local `D:\LLM\Qwen3-Embedding-0.6B.Q8_0.gguf`, SHA-256 `e40d4a708d0b324fc133bae547bd64dec79edb885410ef262c1e5c2efb2050b3`. Backend llama existente em localhost, Vulkan RTX 3050, duas threads, contexto 4.096, pooling last, paralelo 1, lote 16, ubatch 2.048. Melhor medição anterior: 5,29 documentos/s e 3.464,69 tokens/s no pequeno benchmark; velocidade do corpus depende do tamanho dos textos. Tokenização validada antes de geração, sem truncamento silencioso.

ECOA usa `xagi/qwen3-embedding:0.6b-q4_k_m`, artefato diferente do Q8 local. Quatro queries sobre 20 documentos: menor cosseno 0,9668659 e sobreposição média top-5 de 100%, acima dos critérios do projeto 0,95/80%. Essa amostra não prova identidade dos checkpoints nem compatibilidade universal. Documentos sem instrução de query; perguntas com instrução fixa, pergunta original mantida no roteamento/BM25. Auditoria offline em `v6-pipeline/offline-audit.json` passou.

## CLI, cálculos e qualidade

22 perguntas (18 personas, falha esperada e três exemplos) pela interface existente `rota`/`perguntar`, em sequência e com pausas, usando ECOA somente em queries e respostas. Acerto de rota/recusa: 13/22 (59.09%). Não é taxa de correção factual. Fontes citadas foram verificadas contra arquivos e chunks; evidências, filtros relaxados, confiança, recusas, latência e tokens em `cli-v6/source-audit.json`.

Uso das 22 respostas finais: 44815 tokens (31463 prompt e 13352 conclusão), 20 gerações, 20 reutilizações de query e 0 novas chamadas de embedding nessas respostas. Não inclui teste de compatibilidade e tentativa inicial substituída de P5.2. Gerador: `qcwind/qwen3-8b-instruct-Q4-K-M`.

Corrigido o cálculo de P5.2: soma por fornecedor e ordenação pelo total contratado, sem chamar LLM para calcular valores. Teste distingue fornecedor com muitos contratos pequenos de outro com um contrato maior; repetição pela CLI confirmou fornecedores em vez de órgãos. Consulta usa valores contratados, não pagamentos. Banco existente SQLite; consulta PostgreSQL é compilação pura, sem conexão, DDL ou execução. Cinco casos compilados em `sql-v6/personas-compilation.json`, com parâmetros, listas permitidas, versão v6 e referência temporal explícita.

Revisão em `cli-v6/factual-review.json` registra limites por caso. Falhas relevantes ainda presentes: categoria Compras tratada como modalidade (P2.1/P6.2); norma explícita ignorada (P3.2); recusa sem texto (P4.3); conclusão de tendência sem sustentação (P5.3); trechos pouco pertinentes sobre microempresas (P6.3). Preços unitários e equivalência técnica insuficientes impediram médias/medianas solicitadas. Não preencher lacunas com respostas inventadas. Vigência normativa histórica não comprovada integralmente. Não foram adicionados novos guardrails; comportamento e mascaramento originais permanecem.

## Verificação e pendências

511 testes offline passaram após a correção de fornecedor. Integração ECOA foi executada separadamente, com cache e pausas. ZIPs reabertos: extensão, 40 entradas e CRC íntegros; hashes por arquivo já conferidos contra inventário. Validação preserva versões anteriores, limite por órgão/base, URLs públicas e organização por data/hora.

Esta etapa não completa todos os requisitos do desafio: cross-encoder e ablação, feedback/histórico persistidos, execução PostgreSQL e trilha de e-mails continuam pendentes. Fixture sintético não substitui automaticamente os e-mails. A avaliação evidenciou os defeitos acima; corpus e ZIPs estão entregues, mas não se afirma que todas as respostas estejam corretas.

**Atualização 2026-10-02.** O parágrafo acima registra o estado no momento desta execução. Depois dela:

- Cross-encoder e ablação: concluídos. `src/rerank.py` (`mmarco-mMiniLMv2-L12`, CPU) reordena os 10 primeiros do RRF e está ligado no `responder` (`RERANK_ENABLED`, `RERANK_CANDIDATES=10`). Na ablação sobre a v6 (47 perguntas, 13 de identificador), RRF + rerank@10 teve hit@3 0,83 e MRR@10 0,78, contra 0,64/0,62 do RRF sem rerank; detalhes em [ABLACAO_RERANK.md](ABLACAO_RERANK.md).
- Feedback e histórico persistidos: concluídos. `POST /api/v1/feedback` e `GET /api/v1/history`, gravados em SQLite por `src/history_store.py`. O frontend, mantido em repositório separado, ainda não usa esses endpoints.
- Execução PostgreSQL: `src/structured_db.py` e `src/structured_query.py` executam as consultas no Postgres quando `DATABASE_URL` está definida e no SQLite da v6 quando não está. As avaliações abaixo usaram o SQLite.
- Golden set com o pipeline completo sobre a v6 (`eval/runs/golden-v6-final`): 22/22 em rota e recusa e citação 21/22, depois das correções de SQL, roteamento e recusas indevidas. Continua não sendo taxa de correção factual: a conferência contra o SQLite ainda encontra respostas erradas (P2.1, P4.2, P4.3, P5.3) e F1 sem fonte.
- Trilha de e-mails: continua pendente, aguardando a base sintética do desafio.



## Artefatos versionados e preservação

Os commits da v6 incluem o inventário, os vetores por base, FAISS, documentos
normalizados ativos, SQLite e os dois ZIPs em `atas-zip/`. Os inventários v4/v5
ficam como histórico e suporte aos testes. O ZIP e os embeddings completos da v5
permanecem apenas no disco. Não foi configurado Git LFS.

`.gitattributes` preserva os bytes dos snapshots para evitar que a conversão
CRLF/LF invalide os hashes de proveniência. Credenciais, cache de queries,
arquivos brutos, logs e backups de execução permanecem fora do Git. Os scripts
da execução e relatórios selecionados em `eval/runs/` foram adicionados
explicitamente; novas execuções continuam ignoradas por padrão.
