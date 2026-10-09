# Observabilidade das consultas

Os eventos de execução ficam em `logs/pipeline/pipeline-AAAA-MM-DD.jsonl`.
O histórico de perguntas/respostas é separado: `data/app_state/historico.sqlite`.

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

Textos são limitados a 8.000 caracteres por string. A geração informa
`prompt_truncated` e `answer_truncated`; os envelopes informam
`output_truncated` e `log_text_limit`. A resposta HTTP/histórico não é reduzida
por esses limites do log. CPF, telefone e e-mail pessoal são mascarados também
em `extra` (parâmetros SQL, resultados, erros), **antes** de cortar textos.
Não é gravada a saída bruta integral do provedor, nem o texto de cada candidato
recuperado; o prompt mostra a evidência efetivamente enviada à geração.

## Trace e tempo total

Os endpoints existentes continuam:

- `GET /api/v1/trace/{query_id}`;
- `GET /api/v1/sessions/{session_id}/trace`.

Mesma autorização: `X-Session-Id` da conversa ou `X-API-Key` administrativa.
Os campos novos são aditivos; nomes antigos e leitura de JSONL legado continuam.

| Campo do trace | Significado |
| --- | --- |
| `latencia_total_ms` | Tempo monotônico do handler API; na chamada direta, do pipeline |
| `latencia_pipeline_ms` | Tempo do envelope do pipeline, quando disponível |
| `latencia_metodo` | `requisicao_monotonica`, `pipeline_monotonico`, `parcial` ou `soma_etapas_legado` |
| `completo` | `true` se existe evento terminal, mesmo encerrado com erro; `false` se há início sem fim; `null` para legado sem envelope |
| `status` | Estado terminal, `incompleto` ou `null` para legado |

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

Falha de I/O ao gravar log produz diagnóstico no logger Python e não derruba a
consulta. Se o armazenamento inteiro estiver indisponível, não há garantia de
um trace persistido; alertas do processo continuam necessários. Não há backend
centralizado de logs, métricas de infraestrutura ou telemetria do navegador.
Coleta, OCR, normalização e construção de índices têm relatórios separados,
não o envelope das consultas.

Todos os testes isolam o logger e o histórico da API em diretórios temporários.
A cobertura inclui pipeline/SQL reais com fixtures e provedor injetado, sem
chamadas externas: `tests/test_pipeline_observability.py`, `tests/test_trace.py`
e `tests/test_pipeline_logs.py`.
