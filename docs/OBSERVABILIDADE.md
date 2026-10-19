# Observabilidade das consultas

Os eventos de execução ficam em `logs/pipeline/pipeline-AAAA-MM-DD.jsonl`.
O histórico de perguntas/respostas é separado: `data/app_state/historico.sqlite`.
Diagnósticos de manutenção não são consultas: ficam em `logs/maintenance/`.

O §2.4 do enunciado exemplifica `logs/pipeline-AAAA-MM-DD.jsonl`. Por decisão do
projeto, permanece o subdiretório `logs/pipeline/`, documentado e compartilhado
por consultas e operações offline. Não se movem ou reescrevem arquivos antigos;
leitores externos devem usar o caminho efetivo acima.

## Visão rápida: C, D e E

| Ajuste | O que muda | O que permanece |
| --- | --- | --- |
| C — SQL direto | Uma conta suficiente dispensa recuperação e reranker | Regras de cálculo, fontes e fallback |
| D — Auditoria opcional | Registra evidências completas quando habilitada | Resumo padrão, orçamento do LLM e mascaramento |
| E — Fila e índice | Grava eventos em lotes e consulta traces por índice | JSONL como fonte de verdade e contrato da API |

Esses ajustes valem para CLI e API/frontend, pois compartilham o pipeline.
Não exigem mudanças no frontend ou reconstrução do corpus.

## Caminho de uma pergunta

Uma chamada válida a `POST /api/v1/query` produz:

```text
requisicao_inicio
  pipeline_inicio
    reformulacao
    interacao
    query_analysis -> roteamento
    retrieval_denso -> retrieval_bm25 -> fusao_rrf -> rerank
    consulta_sql
    geracao -> validacao
    resumo_consulta
    [etapas opcionais não executadas: status=pulado]
  pipeline_fim
  persistencia_historico
requisicao_fim
```

O caminho varia: cortesia não consulta o acervo, guardrails podem recusar,
intenções podem repetir a busca/SQL e filtros podem exigir uma segunda busca.
Perguntas exclusivamente analíticas tentam SQL primeiro. Se o resultado é
suficiente para a resposta, busca densa, BM25, RRF e rerank ficam
com `status=pulado`, `attempted=false` e `motivo=calculo_sql_suficiente`;
`consultou_vetorial=false`. Nem embeddings nem cross-encoder são carregados
nesse caminho. SQL ausente/insuficiente mantém a busca como fallback; perguntas
mistas continuam recuperando evidências não calculáveis por SQL. Em múltiplas
intenções, o desvio é avaliado por subpergunta, sem descartar a outra intenção.
A ordem efetiva é a ordem dos eventos, não um checklist de etapas obrigatórias.
`interacao` registra também decisões por regras, com `metodo=regras` e
`llm_called=false`. `pipeline_fim` contém a resposta final, inclusive avisos e
pergunta reformulada; `requisicao_fim` contém a resposta pública preparada pela
API e o resultado da persistência.

Uma exceção propagada fecha os envelopes com `status=erro` e `error_type`.
Erro no histórico não derruba uma resposta calculada: a API retorna `200`,
registra `history_saved=false` e fecha com `status=degradado`. JSON/entrada
inválidos recebem `422` e um envelope `rejeitado`, sem gravar corpo não validado.

Uma chamada direta a `src.pipeline.responder` tem o envelope `pipeline_*`,
mas não `requisicao_*` nem persistência de histórico. Chamadas isoladas a módulos
não prometem uma execução completa do pipeline.

### SQL direto: exemplo de funcionamento (C)

Para **“Qual o valor total dos contratos de 2025 no corpus?”**, o caminho é:

```text
Pergunta -> análise dos filtros -> roteamento -> tentativa SQL
                                                   |
                                         Resultado suficiente?
                                          /                 \
                                        sim                 não
                                         |                   |
                              LLM: template {{sql}}     recuperação normal
                                         |              e geração/guardrails
                              backend insere resultado
                              com fontes SQL
```

`src/structured_query.py` identifica operações analíticas suportadas e executa
SQL fixo e parametrizado; o LLM não escreve a consulta. O resultado precisa
atender ao critério de suficiência compartilhado com `src/generate.py`.
Nesse caso, `src/pipeline.py` pula a recuperação. A geração chama a LLM apenas
para produzir uma apresentação neutra com `{{sql}}`, sem enviar os resultados.
O backend substitui o marcador literalmente pelos resultados completos com
citações. `extra.metodo=sql_template` identifica esse caminho;
`sql_injetado_chunk_ids` e `evidencias_usadas` identificam as fontes inseridas,
com `chunks_no_prompt=0`. Template inválido ou erro do modelo mantém o resultado
SQL determinístico, com `metodo=sql_template_fallback`, `fallback_motivo` e
`status=degradado`. Não há segunda chamada ao modelo para tratar o resultado.

As tabelas são Markdown GFM produzido pelo código. O log preserva o Markdown
completo e mascarado em `consulta_sql.extra.resultado_markdown` e no bloco final
`geracao.extra.resultado_sql_markdown`, independentemente dos limites do resumo
de linhas/`answer`. O trace expõe os mesmos campos em `detalhes`. Metacaracteres
nos dados são escapados; o Markdown não é gerado nem recalculado pelo modelo.

No exemplo, “contratos de 2025” considera assinatura; “publicados em 2025”
considera publicação. O resultado continua limitado à amostra presente no corpus,
não ao total de contratos do RJ.

Não basta a pergunta mencionar um número: listagens, métricas não suportadas e
perguntas mistas não ganham automaticamente o desvio. Se SQL falhar ou não
fornecer evidência suficiente, a recuperação continua disponível; isso não
garante uma resposta, pois a geração ainda aplica os guardrails e pode recusar.
Em uma pergunta com intenções analítica e normativa separadas, só a parte
analítica suficiente dispensa busca; a normativa continua recuperando evidências.

## Contrato dos eventos

Os campos de primeiro nível continuam compatíveis:

- `ts`, `run_id`, `query_id`, `session_id`;
- `stage`, `status`, `latencia_ms`;
- `input`, `output`, `tokens`, `modelo`, `extra`.

Os novos eventos identificam o contrato em `extra.schema_version=2`:

| Campo em `extra` | Significado |
| --- | --- |
| `origem` | `api`, `avaliacao`, `direto`, ou origem explicitamente fornecida |
| `sequencia` | Ordem dentro da execução correlacionada; presente em envelopes completos |
| `attempted` | `false` para etapa pulada; não implica chamada remota, pois pode haver cache/regras |
| `outcome` | Resultado normalizado: `ok`, `erro`, `degradado`, `pulado`, `recusa`, `iniciado`, `rejeitado` |
| `motivo` | Por que uma etapa não rodou ou uma busca foi repetida |
| `intencao` | Índice da subpergunta quando há múltiplas intenções |
| `tentativa_busca` | Primeira busca ou segunda busca sem filtros |
| `fallback` / `fallback_on_error` | Caminho alternativo usado ou previsto após falha |

`status` mantém estados específicos da geração, como `sem_chunks`, `conversa`
e `erro_llm`; `outcome` permite analisá-los sem confundir recusa com falha técnica.
Etapas medidas por span registram `erro` quando a exceção escapa. Fallbacks
tratados (análise por regras, pergunta original, BM25 sem embedding, reranker
local) registram `degradado`, em vez de aparentar sucesso normal.

Os envelopes de início trazem `extra.contexto_execucao`:

- revisão Git e SHA-256 dos arquivos de código de runtime, incluindo alterações
  ainda não commitadas;
- SHA-256 do manifesto do índice, data da construção, modelo, bases e versões;
- hash declarado do corpus fonte e SHA-256 do inventário v6;
- configurações efetivas permitidas: modelos, modos, rerank e orçamento de contexto.

Não são gravados `.env`, chaves, headers, URL do banco ou caminho do checkpoint.
Os fingerprints pequenos são cacheados por tamanho/mtime; não há hash completo
do corpus nem acesso de rede no caminho da consulta. O hash fonte do manifesto
é uma declaração da construção, **não** uma verificação de atualização do banco
SQL ou dos arquivos brutos. Esses metadados ajudam a comparar execuções, mas
não substituem o inventário/auditoria nem um ambiente de dependências fixado.

Os envelopes de fim trazem `etapas` (status, outcome, attempted, duração e
motivos por ocorrência), `tokens_reportados`, `logging_errors` e flags de
truncamento. `tokens_reportados` soma somente o uso disponível nas etapas;
não é estimativa de custo nem contabilização de tentativas internas do SDK.

### Modelo, tokens e cobertura das chamadas

`modelo` identifica o modelo efetivo retornado pelo provedor quando disponível,
inclusive em `interacao` e `reformulacao`. Sem chamada de modelo, `null` é normal;
uma leitura de cache pode identificar o modelo associado ao resultado em cache.
No caminho `sql_template`, `llm_called=true` indica uma chamada de apresentação:
a LLM não recebeu as evidências SQL. A ausência de prompt de evidências não é
ausência de fontes: elas são inseridas pelo backend. No fallback, o campo indica
se houve tentativa de chamar o cliente; falha na inicialização pode deixá-lo
`false`. Logs históricos com `sql_deterministico` e `llm_called=false` continuam
válidos para a implementação anterior.

Os contadores `tokens.prompt`, `completion` e `total` continuam inteiros,
compatíveis com os leitores existentes. Agora `extra.uso_tokens.status` distingue:

| Status | Significado |
| --- | --- |
| `reportado` | Uso conhecido nas respostas disponíveis, inclusive zero efetivamente reportado |
| `parcial` | Há consumo disponível, mas faltam campos ou uso de alguma chamada |
| `indisponivel` | Houve chamada/função de modelo sem consumo reportado; zero nos contadores não prova custo zero |
| `cache` | Resultado/vetor reutilizado, sem recontabilizar o consumo antigo |
| `nao_aplicavel` | A etapa não fez chamada de modelo, como SQL determinístico ou um envelope |

`campos_reportados` indica quais campos vieram do `response.usage`. Embeddings
não produzem tokens de conclusão; chamadas com informações parciais não recebem
estimativas inventadas. Funções injetadas que retornam apenas vetores/texto
ficam com consumo desconhecido. `model_called=null` num lote sem observações SDK
não prova chamada remota: pode ser uma função local.

`llm_ecoa.py` e os clientes de embedding registram `chamadas_modelo`: provedor,
modelo solicitado/efetivo, duração, status, tokens disponíveis e tipo/código de
erro. A troca explícita ECOA → OpenRouter conserva as duas observações. Se uma
chamada falha sem `usage` e a seguinte retorna consumo, a cobertura é `parcial`.
Credenciais, corpo da requisição e mensagem bruta de erro não entram nesse
registro de chamadas.

A unidade observada é a **chamada do SDK**, não cada tentativa HTTP interna.
`sdk_max_retries` registra o limite; `retries_internos_sdk=nao_observados` declara
a limitação quando há retries internos possíveis. A política de retries/fallback
não foi alterada. Não se promete identificar todo consumo cobrado pelo servidor.

Os envelopes de fim e o resumo do trace incluem `cobertura_tokens`, com contagem
de eventos por status. O uso dos lotes é contado nos eventos `embedding_lote`,
não novamente na etapa pai. Logs antigos não ganham essa classificação.

## Operações offline no mesmo contrato

`src/offline_observation.py` instrumenta as operações públicas sem mudar seus
retornos ou artefatos. Usa `origem=offline`, `tipo_pipeline=offline`, `modulo`,
`funcao`, IDs próprios e sequência. O `query_id` é sintético: serve para correlacionar
uma execução offline, não representa pergunta de usuário ou sessão de chat.

| Módulo/operação | Stage |
| --- | --- |
| Coleta, atualização, descoberta e planejamento dry-run | `coleta` |
| Inventário e consulta ao checkpoint de descoberta | `inventario` |
| Fachada `src.ingest.ingest` | `ingestao` |
| `src.prepare_documents.prepare_documents` | `preparacao` |
| `src.create_embeddings.create_embeddings` | `embeddings` |
| Construção do índice e seu dry-run | `indexacao` |
| Cada lote de vetores gerados ou reutilizados | `embedding_lote` |

```text
pipeline_inicio [tipo_pipeline=offline]
  ingestao / preparacao / coleta / inventario / embeddings / indexacao
    [embedding_lote, quando houver]
pipeline_fim [tipo_pipeline=offline]
```

Chamadas aninhadas compartilham os IDs e não abrem outro envelope. O fechamento
registra erro mesmo se uma exceção interromper a operação; código de saída
não zero também é erro. Falhas internas observadas e tratadas podem resultar em
envelope `degradado`. Os parâmetros registrados são permitidos explicitamente;
saídas são resumos, sem copiar documentos, vetores ou inventário inteiro para o
log. Esses artefatos continuam nos caminhos próprios.

A coleta conserva seu JSONL original em `data/runs/` e também emite o contrato
comum. `etapa_coleta`, `run_id_legado` e `log_legado` ligam os dois registros.
`started/completed/failed` são normalizados em `iniciado/ok/erro` no log comum.

No pipeline de consultas, o embedding da pergunta continua dentro de
`retrieval_denso`; não se acrescenta um lote offline nem se contam tokens duas
vezes. `--dry-run` e cache não causam chamadas adicionais ao modelo.

## SQL, evidências e resposta

- Recuperação: IDs, fonte, rank e scores de até 10 candidatos por etapa;
  `evidencias_fora_do_log` identifica candidatos omitidos.
- SQL: template final, cabeçalho, IDs dos chunks e até 20 linhas de resultado
  textual, com `linhas_fora_do_log`. `execucao_sql` registra templates,
  parâmetros, quantidade de linhas retornadas, duração e erros das consultas
  auxiliares e tentativas de relaxamento. Conserva as primeiras 10 e últimas
  10 consultas quando ultrapassa 20, com contador `fora_do_log`. Não inclui
  todos os comandos de construção/atualização da base.
- Geração: prompt mascarado, IDs selecionados/excluídos pelo orçamento,
  resposta, modelo, uso reportado e diagnósticos do provedor.
- Validação: citações aceitas/descartadas, JSON inválido e correções aplicadas.
- Resumo: `consultou_sql` e `consultou_vetorial` indicam **tentativa da etapa**,
  mesmo que tenha falhado ou não devolvido chunks. Os campos
  `sql_retornou_evidencia` e `vetorial_retornou_evidencia` distinguem retorno de
  evidência. `degradado` indica erros/fallbacks tratados.

No resumo padrão, textos são limitados a 8.000 caracteres por string. A geração informa
`prompt_truncated` e `answer_truncated`; os envelopes informam
`output_truncated` e `log_text_limit`. A resposta HTTP/histórico não é reduzida
por esses limites do log. CPF, telefone e e-mail pessoal são mascarados também
em `extra` (parâmetros SQL, resultados, erros), **antes** de cortar textos.
Não é gravada a saída bruta integral do provedor. No modo padrão, não se grava
o texto de cada candidato recuperado; o prompt mostra a evidência efetivamente
enviada à geração.

### Auditoria completa opcional (D)

`LOG_EVIDENCE_DETAIL=resumo` mantém os limites acima e é o padrão. Para investigar
novas execuções, altere no `.env`:

```dotenv
LOG_EVIDENCE_DETAIL=completo
```

**Reinicie o backend** para carregar a configuração. Para voltar ao modo compacto,
use `LOG_EVIDENCE_DETAIL=resumo` e reinicie novamente. O campo aditivo
`extra.auditoria_evidencias` registra, sem os cortes do resumo:

- todos os candidatos **retornados por cada etapa**, com texto, metadados,
  posição e scores, inclusive além dos primeiros 10; no rerank, também os
  candidatos pontuados que ficaram fora do `top_k`;
- todas as linhas do resultado SQL, além das primeiras 20 do resumo;
- evidências disponíveis à geração, texto original mascarado e texto selecionado,
  seleção/exclusão por orçamento e prompt completo mascarado;
- na geração SQL, evidências inseridas na resposta pelo backend e o prompt de
  apresentação com `{{sql}}`, que não contém essas evidências.

Isso não aumenta o contexto enviado ao modelo nem muda respostas. As mesmas
regras de mascaramento/sensibilidade continuam válidas; conteúdo restrito não
é copiado para a auditoria. Evidências da geração já passaram pelo filtro de
sensibilidade; os candidatos anteriores permanecem rastreáveis nas etapas de
recuperação. Não é uma cópia do corpus inteiro nem auditoria de todos os comandos
de construção do banco. Há mais serialização, I/O e espaço em disco; use o modo
completo quando precisar investigar. Logs antigos não ganham esses detalhes.

## Trace e tempo total

Os endpoints existentes continuam:

- `GET /api/v1/trace/{query_id}`;
- `GET /api/v1/sessions/{session_id}/trace`.

Mesma autorização: `X-Session-Id` da conversa ou `X-API-Key` administrativa.
Os campos novos são aditivos; nomes antigos e leitura de JSONL legado continuam.

### Persistência e índice local (E)

`src/log_store.py` mantém uma fila FIFO limitada a 256 eventos por processo,
com lotes de até 64. O contexto (sessão, origem e sequência) é capturado antes
de enfileirar. Fila cheia aplica contrapressão, sem descartar silenciosamente.
Eventos terminais/flush esperam a persistência; encerramento normal drena a fila.
Chamadas isoladas, sem contexto de execução, mantêm escrita imediata.

```text
Etapa da consulta
  -> captura contexto, mascara e serializa o evento
  -> coloca na fila limitada do processo
  -> worker reúne um lote
  -> obtém lock de escrita no SQLite
  -> acrescenta eventos ao JSONL e atualiza o índice
  -> libera o lock

Pedido de trace
  -> importa apenas bytes novos de JSONL, quando houver
  -> busca eventos pelo índice
  -> monta a resposta no contrato existente
```

A fila desloca a gravação para o worker, mas não elimina todo o custo da etapa:
captura de contexto, mascaramento e serialização continuam no chamador. Se a
fila estiver cheia, ele espera espaço. Antes de concluir os envelopes da
consulta/requisição, há espera pelos eventos pendentes; por isso, o trace pode
ser consultado após a resposta sem depender de uma gravação futura da fila.
Falhas de persistência são reportadas no logger Python e contabilizadas no
contexto, sem transformar uma resposta calculada em erro HTTP.

`logs/pipeline/pipeline-index.sqlite` é um **índice derivado**, em SQLite/WAL.
O lock transacional coordena append JSONL entre processos que usam esse mesmo
índice. Há índices por `query_id`, `session_id` e `run_id`. A consulta quente não
relê o conteúdo JSONL: verifica os arquivos e busca pelo índice. Arquivos legados
são importados incrementalmente, por offset; duplicatas são eliminadas.
Substituição, truncamento e remoção detectados atualizam o cache sem modificar os
logs. O índice acompanha o expurgo existente, sem mudar a política de retenção.

JSONL continua sendo a fonte de verdade. Se o append ocorrer antes de uma falha
na transação do índice, a leitura seguinte recupera os registros do JSONL. Com
a aplicação parada, pode-se apagar o índice e seus arquivos `-wal`/`-shm` para
reconstruí-lo na próxima leitura. Falha de SQLite/I/O permite a leitura linear
legada como fallback, quando os JSONL estiverem acessíveis.

Limites: filesystem local compartilhado entre os processos; não é um coletor
distribuído, nem se recomenda WAL em filesystem de rede. Escritores externos
que não usam o índice não participam do lock. Uma morte abrupta pode perder
eventos ainda na fila; não se promete `fsync` por evento ou transação atômica
entre JSONL e SQLite. Importação inicial/reconstrução ainda lê os arquivos,
respostas de trace continuam proporcionais ao número de eventos da consulta e
a descoberta dos arquivos continua proporcional à quantidade de arquivos.

| Campo do trace | Significado |
| --- | --- |
| `latencia_total_ms` | Tempo monotônico do handler API; na chamada direta, do pipeline |
| `latencia_pipeline_ms` | Tempo do envelope do pipeline, quando disponível |
| `latencia_metodo` | `requisicao_monotonica`, `pipeline_monotonico`, `parcial` ou `soma_etapas_legado` |
| `completo` | `true` se existe evento terminal, mesmo encerrado com erro; `false` se há início sem fim; `null` para legado sem envelope |
| `status` | Estado terminal, `incompleto` ou `null` para legado |
| `resumo` | Conclusão da pergunta (bases, evidências, fontes, confiança) e `situacao_das_etapas`, a contagem das etapas por status; a lista das etapas fica só em `etapas` |
| `etapas[].output` | Saída da etapa. Marco de início traz `{"marco", "versao_codigo"}`, etapa pulada traz `{"pulado": motivo}`, e o `resumo_consulta` aponta para o `resumo` do topo |

Não some durações de etapas: geração inclui validação e envelopes contêm as
etapas internas. Para legado sem envelope, a soma é mantida somente como
estimativa rotulada. `fim`/tempo total não incluem feedback enviado depois da
resposta. O trace parcial não representa uma requisição concluída. Um processo
morto não consegue escrever o evento final: o início restante revela essa lacuna.
`extra.completed=false` num evento terminal indica exceção propagada, enquanto
`completo=true` no trace indica que o encerramento foi observado.

O tempo API mede o handler instrumentado, incluindo pipeline e tentativa de
salvar histórico. Não mede envio pela rede, serialização final do framework,
validação anterior ao handler, CORS ou renderização no navegador. O evento
`requisicao_fim` prova que a API preparou o retorno, não que o frontend o recebeu.

## Operação e testes

A aplicação deve ser reiniciada para carregar o código atualizado. Não é
necessário migrar SQLite, reindexar o corpus ou reescrever logs antigos.
Os logs anteriores não ganham retroativamente prompt, resposta ou sessão.

`LOG_RETENTION_DAYS` controla a retenção diária (padrão 30; `0` mantém tudo).
Backups não participam do expurgo. Consulte `python -m src.log_maintenance cleanup`
para um plano sem alteração e acrescente `--apply` somente para executá-lo.

Falha de I/O ao gravar log produz diagnóstico no logger Python e não derruba a
consulta. Se o armazenamento inteiro estiver indisponível, não há garantia de
um trace persistido; alertas do processo continuam necessários. Não há backend
centralizado de logs, métricas de infraestrutura ou telemetria do navegador.
Coleta, preparação (incluindo extração/OCR), embeddings e indexação agora têm
envelopes offline no mesmo contrato. Seus relatórios detalhados continuam
separados; operações offline não são consultas de usuários.

Todos os testes isolam o logger e o histórico da API em diretórios temporários.
A cobertura inclui pipeline/SQL reais com fixtures e provedor injetado, sem
chamadas externas: `tests/test_pipeline_observability.py`, `tests/test_trace.py`
e `tests/test_pipeline_logs.py`. Os ajustes C/D/E são cobertos também por
`tests/test_sql_fast_path.py`, `tests/test_log_evidence.py` e
`tests/test_log_store.py` (incluindo processos concorrentes, contrapressão,
flush, importação incremental e reconstrução do índice).

### Validação dos ajustes C/D/E — 09/10/2026

- Backend: `python -m pytest -q` — **1.338 testes passaram**.
- Frontend: `npm test` em `../frontendRAG` — **36 testes passaram**, sem alterar
  arquivos do frontend.
- CLI e navegador Edge/Playwright: **20/20 verificações** nos dez casos de
  `eval/validate_numerical_corpus.py`; números iguais ao gabarito do corpus bruto,
  fontes SQL válidas e traces completos. Todos os dez traces da API indicaram
  `consultou_vetorial=false`, quatro etapas puladas por SQL suficiente,
  geração determinística e `logging_errors=0`.
- Nessa rodada local, pipeline de **57–96 ms**; CLI incluindo inicialização do
  processo de **0,61–0,67 s**. Não são benchmarks de produção/SLA.
- Auditoria completa verificada também por CLI real com total de contratos de
  2025; o resumo permanece o padrão. Fixtures verificam candidatos além de 10,
  resultado SQL além de 20 linhas, evidências excluídas e prompt além de 8.000
  caracteres, sem mudar o contexto enviado ao modelo.
- Concorrência: cinco execuções consecutivas com três processos e 90 eventos
  íntegros por execução. Trace quente testado sem abrir conteúdo JSONL; a leitura
  indexada foi comparada com a leitura legada no log real.

Artefatos locais (fora do Git): `data/runs/numerical-validation/cde-validation.json`,
`cde-audit-trace.json`, `validation.json`, `trace-*.json` e capturas do navegador.
O relatório não amplia a avaliação factual geral (A), proveniência (B), política
de privacidade/retenção (F) ou telemetria de frontend (G).

### Alinhamento ao §2.4 do desafio

As mudanças posteriores padronizam o logging offline e os metadados de uso,
sem mudar o caminho escolhido, respostas, dados do corpus ou política de retries.
`tests/test_logging_alignment.py` cobre contrato, operações reais com fixtures,
aninhamento, erros, dry-run, cache, modelo efetivo e consumo desconhecido/parcial.
Chamadas SDK nos testes são simuladas, sem tráfego de rede.

A auditoria distingue alinhamento do contrato de conformidade literal do caminho:
o subdiretório foi mantido explicitamente. Um avaliador que fixe o caminho da
raiz `logs/` precisa ser ajustado ou receber essa justificativa.
