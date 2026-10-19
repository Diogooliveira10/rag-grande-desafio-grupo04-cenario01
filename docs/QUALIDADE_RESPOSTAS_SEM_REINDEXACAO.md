# Qualidade das respostas sem modificar o snapshot

## Restrição

Estas correções alteram somente código, configuração de exemplo e testes. Não
reconstroem nem corrigem `data/normalized/documents.jsonl`,
`data/structured/base_estruturada.sqlite` ou qualquer arquivo de `index/`.
As consultas ao SQLite são abertas em `mode=ro` por `Banco`.

O manifesto local de hashes anterior à intervenção fica em
`.tmp/protected-artifacts-before-response-fixes.json`. A conferência posterior
fica em `.tmp/protected-artifacts-verification.json`. Esses arquivos são evidência
local, não novos dados do corpus.

## Correções no caminho de consulta

- **Objeto versus linguagem da pergunta:** “quanto vale” e “no acervo” deixam de
  produzir filtros por `vale`/`acervo`. Vale-transporte continua sendo assunto.
- **Papel da entidade:** artigos/preposições de nomes como Casa da Moeda não
  interrompem o reconhecimento do fornecedor. Um fornecedor reconhecido não
  pode ser reinterpretado como órgão pelo fallback de termos do objeto.
- **Ranking:** “o maior/o menor contrato” limita a um registro; limites explícitos
  continuam preservados. A lista identifica PNCP e fornecedor e não imprime
  `None` nos campos opcionais.
- **Listagem:** esfera, período, fornecedor ou valor explícitos já podem
  justificar SQL. Não é necessário inventar um assunto para consultar um recorte.
  A resposta informa total correspondente e quantidade efetivamente exibida.
- **Órgãos e total:** a formulação “quais órgãos ... e qual o total” agrupa por
  órgão e calcula também o total geral de todo o recorte, não só dos dez exibidos.
- **Apresentação:** contagem e soma simples exibem somente a métrica pedida;
  as estatísticas integrais permanecem nas evidências/logs. Comparações entre
  esferas preservam os detalhes. Ausência de registros é diferente de soma zero
  e de registros com valor desconhecido.
- **Normas:** referências numeradas priorizam a identidade declarada da norma,
  não apenas documentos que a mencionam. O BM25 temático dentro dessa seleção
  evita que número/ano favoreçam capas. Visão geral prioriza disposições iniciais;
  perguntas sobre fases priorizam a enumeração; artigos explicitamente pedidos
  têm preferência. Duplicações textuais são removidas somente dos candidatos
  de consulta, sem excluir documentos. Rerank, filtros e escopo continuam ativos.
- **Confiança:** todos os recortes SQL são considerados, não apenas o primeiro.
  Ausência de fonte, busca degradada e intenções sem evidência/citação geram
  avisos e limitam confiança. Isso não é uma prova automática de veracidade de
  cada frase do LLM; recusa de guardrail não é convertida por esses avisos.
- **Prompt:** distingue data de captura de data de publicação, modalidade da
  compra vinculada e modalidade ausente no contrato, além de ausência no trecho
  versus ausência no corpus. A projeção do registro não contradiz a modalidade
  herdada da compra vinculada.
- **Conversação:** a origem dos dados tem resposta determinística, distinguindo
  PNCP, normas públicas, snapshot e conversas sintéticas. Pergunta factual fora
  do domínio não é tratada como simples falta de evidência.
- **Valores extremos:** um aviso de conferência é anexado às contas monetárias
  com valor acima do limiar configurado. O número não é corrigido nem removido;
  valor alto, sozinho, não comprova erro. Confiança na conta do snapshot não
  equivale à validação econômica dos valores publicados na origem.

## Apresentação SQL com marcador `{{sql}}`

Quando o SQL é suficiente para responder todas as intenções analíticas, o pipeline
continua sem executar embeddings, BM25, RRF ou rerank. A geração agora chama a LLM
uma única vez, apenas para escrever uma apresentação neutra com o marcador literal
`{{sql}}` em linha própria. O modelo recebe a pergunta mascarada e as instruções,
não as linhas calculadas. Depois da chamada, o backend faz uma substituição
literal e não recursiva pelo bloco determinístico completo, preservando números,
fontes, confiança, filtros e avisos de todos os recortes. Não há engine de
templates, execução de comandos vindos do modelo nem segunda chamada à LLM.

O template tem limite de tamanho, exige exatamente um marcador e rejeita outros
marcadores, código/HTML, citações, dígitos e expressões numéricas comuns por extenso
fora do marcador. Essas verificações são sintáticas: não garantem por si só que
toda frase seja semanticamente neutra. O prompt proíbe fatos e interpretações
na narrativa. Falha do cliente/provedor, JSON inválido ou template rejeitado
preservam o resultado determinístico, com aviso de apresentação indisponível.
Guardrails e mascaramento continuam ativos antes/depois da geração.

Logs distinguem `metodo=sql_template` de `sql_template_fallback`, com
`fallback_motivo`, `llm_called`, uso de tokens quando reportado,
`sql_injetado_chunk_ids` e `evidencias_usadas`. `chunks_no_prompt=0` é intencional:
as evidências foram inseridas na resposta pelo backend, não enviadas ao modelo.
O fallback fica `degradado`, sem virar falsa ausência de evidência. Perguntas
mistas/incompletas mantêm o fluxo normal de recuperação e geração.

Testes específicos: `tests/test_sql_template.py`; regressões de SQL, cálculos,
API e trace também verificam o novo caminho. Clientes são simulados nos testes
offline; isso não mede latência nem aderência do modelo real ao template.
Não há alteração em JSONL/SQLite/índices nem necessidade de novos dados em disco.

A checagem posterior pelo frontend real encontrou incompatibilidade do provedor
com `minLength`/`maxLength` na string de saída: o modelo omitia o marcador.
O esquema remoto usa apenas `type=string`; todos os limites continuam sendo
validados localmente. O teste de regressão impede reintroduzir esses limites
no esquema enviado ao provedor. Templates rejeitados registram `template_erro`.

## Tabelas Markdown na resposta, frontend e logs

A apresentação SQL é construída em Markdown GFM pelo código, a partir das linhas
SQL e cálculos determinísticos já disponíveis, sem consultas extras para montar
a tabela e sem pedir números à LLM. `src/sql_markdown.py` escapa barras, HTML e
metacaracteres em valores de fonte; não separa colunas reconstruindo strings com
`|`. Listagens/rankings, contagem/soma, estatísticas, agrupamentos e evolução têm
cabeçalhos/células próprios. Notas, total geral, limites da listagem, zeros,
centavos e ausência de campos são preservados. Zero correspondências continua
como ausência na amostra, não tabela financeira com soma zero.

A primeira evidência de cada consulta inclui a apresentação completa no campo
`resultado_sql.markdown`. A geração insere a tabela uma única vez com a fonte
correspondente, sem cortes pelo orçamento do prompt. As linhas legadas continuam
nas evidências para auditoria e compatibilidade.

Os logs JSONL carregam o Markdown completo e mascarado em:
- `consulta_sql.extra.resultado_markdown`: tabela e notas da consulta;
- `geracao.extra.resultado_sql_markdown`: bloco SQL efetivamente inserido, com
  fontes, mesmo quando o resumo de `answer` é truncado.

No frontend, `AnswerMarkdown.tsx` usa `react-markdown` e `remark-gfm`, sem HTML
bruto nem imagens externas, com tabela semântica, cabeçalhos e contêiner rolável
por teclado/mobile. Chat e histórico usam o mesmo componente. O contrato da API
permanece `answer: string`; não há novos campos, migração de histórico ou alteração
no snapshot. Respostas antigas sem Markdown continuam em texto.

Validação posterior: **2.001 testes backend**, **42 Vitest + oito Node no frontend**
e build de produção aprovados. No Edge com provedor real, passaram cinco consultas
(contagem, soma, fornecedor, órgãos/total e ano sem registros) e a reabertura de
uma tabela pelo histórico. Tabelas HTML reais, valores, rolagem mobile e o mesmo
Markdown nos dois estágios de log foram conferidos; sem erros JavaScript ou
requisições falhas. Evidência local: `.tmp/sql-template-frontend/markdown/`.

`npm audit` reporta nove vulnerabilidades preexistentes do frontend (uma moderada,
seis altas e duas críticas). Comparação com o lockfile anterior confirmou as mesmas
nove, sem pacote vulnerável novo sinalizado pelas dependências Markdown. Não foi
aplicado upgrade automático/`--force`; isso exige tratamento separado.

## Embeddings: contingência sem encoder incompatível

Novas opções documentadas em `.env.example`:

```dotenv
QUERY_EMBEDDING_TIMEOUT=8
QUERY_EMBEDDING_COOLDOWN=30
DENSE_RETRIEVAL_ENABLED=true
VALUE_REVIEW_THRESHOLD=1000000000
```

O timeout de conexão é no máximo três segundos; o timeout de leitura é o
configurado. Não é um prazo total de ponta a ponta: fila de modelos, leitura de
índices, BM25 e geração têm custos próprios. Não há retry do SDK no embedding.
Após erro de disponibilidade, novas consultas ao mesmo endpoint/modelo evitam
repetir a conexão durante o cooldown, por processo. Cache de perguntas já
conhecidas continua disponível. Erro de entrada não abre o circuito.

Isso **não liga um servidor parado nem corrige sua instalação**. Para restabelecer
a busca densa, é necessário iniciar/reparar o endpoint configurado e confirmar o
encoder compatível com o índice. Não trocar automaticamente para OpenAI ou outro
checkpoint: mesma dimensão não significa mesmo espaço vetorial.

Enquanto o serviço estiver indisponível, `DENSE_RETRIEVAL_ENABLED=false` permite
operar explicitamente com BM25 + rerank e SQL, com aviso de cobertura limitada.
Reinicie a aplicação para carregar mudanças de configuração e código. O `.env`
local não é alterado por esta intervenção.

## O que exigiria mudar os artefatos — e alternativas

| Necessidade | Mudança definitiva | Alternativa que preserva os arquivos atuais |
|---|---|---|
| Corrigir valor financeiro suspeito, data, modalidade ou fornecedor errado na origem | Conferir PNCP e documento original; registrar proveniência e correção em novo snapshot, atualizar JSONL, derivar novo SQLite e índices afetados | Manter o dado e exibir alerta; registrar conferências em arquivo lateral versionado. Qualquer valor corrigido precisa ser identificado como tal, nunca aplicado silenciosamente |
| Responder itens, quantidades, cláusulas ou fornecedores que não constam dos metadados das atas | Coletar anexos, extrair/OCR, incorporar documentos e reconstruir índices afetados; SQLite somente se houver novos fatos estruturados | Consultar o anexo publicamente, sob demanda e em memória ou em diretório separado, citando URL/data e distinguindo essa evidência do snapshot. Não foi implementada coleta online automática |
| Recuperar artigos inteiros quando o trecho existente corta uma disposição | Novo chunking por artigo/parágrafo, com sobreposição e vínculo com documento-pai; novos embeddings/índices dos textos alterados | Priorizar trechos existentes, ampliar candidatos e orçamento dentro da janela real do LLM, ou agregar trechos adjacentes em memória com proveniência. A agregação completa por artigo não foi implementada aqui |
| Trocar o modelo/checkpoint ou a instrução de embedding dos documentos | Recalcular vetores e reconstruir índices com identidade/configuração registrada | Restaurar o encoder compatível atual ou usar BM25. Avaliar compatibilidade real antes de qualquer troca de backend; dimensão isolada não basta |
| Cobrir 2024, anos ausentes ou oportunidades abertas hoje | Coletar novo recorte e gerar snapshot/SQLite/índices novos | Declarar a cobertura disponível e consultar PNCP ao vivo em uma rota separada, com data e proveniência; não inferir ausência no universo |
| Eliminar duplicatas reais ou corrigir vínculos compra/contrato/ata | Auditar identidade, atualizar normalização e derivar snapshot consistente | Exibir identificadores e proveniência; usar regras de consulta documentadas. Não fundir contratos diferentes só porque valor e descrição coincidem |

Não é necessário alterar os três artefatos para toda correção: uma mudança apenas
na extração de texto/chunking exige reindexar os textos afetados; uma nova coluna
analítica derivável pode justificar somente outro SQLite. Ainda assim, mantenha
versões coerentes e nunca sobrescreva os arquivos protegidos sem autorização.

## Validação

Validação após as tabelas Markdown: **2.001 testes offline aprovados em cinco processos/lotes**
(resumo local em `.tmp/response-quality-suite-summary.json`). Os hashes SHA-256
confirmaram os **23 arquivos protegidos idênticos**, sem adição ou remoção em
`index/`; evidência em `.tmp/protected-artifacts-verification.json`.

Os novos testes usam JSONL, catálogos, SQLite e índices temporários, além de
clientes falsos, sem downloads nem chamadas de geração reais:

```bash
python -m pytest -p tests.offline_guard tests/test_sql_template.py tests/test_response_quality_regressions.py tests/test_retrieval_runtime_quality.py -q
```

O replay local `.tmp/check_response_fixes_readonly.py` exercita sete consultas SQL
reais dos logs sobre o SQLite em modo somente leitura, sem LLM/API e sem reconstruir
banco. Também verifica a seleção BM25 de normas primárias existentes. Resultados
em `.tmp/response-quality-readonly-replay.json`; isso não é uma avaliação online
completa da redação produzida por um modelo real.

O replay `.tmp/check_sql_template_readonly.py` também validou sete consultas dos
logs no SQLite real, em modo somente leitura, simulando apenas a saída da LLM.
Todos os blocos SQL, fontes, confiança e avisos foram preservados após a inserção
no template, com uma chamada de cliente por pergunta. Evidência local:
`.tmp/sql-template-readonly-replay.json`. Não houve chamada a provedor real nesse replay.

### Frontend com provedor real

Depois da correção de compatibilidade do esquema, sete perguntas foram enviadas
pelo frontend Vite real, via Edge headless, ao backend local e ao modelo configurado
`xiaomi/mimo-v2.6-flash` no OpenRouter, sem mocks/interceptação. Todas tiveram
HTTP 200, números/fontes renderizados corretamente e ausência de marcador vazado.
Seis usaram `sql_template`; uma continuação sobre 2024 teve o template rejeitado
e usou o fallback seguro. Portanto, a aderência do modelo ao template não foi
100%, embora todas as verificações de dados/renderização tenham passado.
Não houve erro JavaScript, requisição de navegador falha ou HTTP de erro.
A apresentação de contagem também foi conferida em viewport mobile.

Após essa correção, os 1.983 testes backend passaram novamente; no frontend,
passaram 37 testes Vitest e oito testes Node. Os 23 arquivos protegidos continuaram
idênticos. Relatório, capturas e traces: `.tmp/sql-template-frontend/REPORT.md`.
A execução usa logs/histórico isolados e não altera `.env` nem o código do frontend.
O frontend existente não mostra separadamente o array `avisos` da API; o fallback
fica identificável no payload e no trace. A checagem estrita de zero fallbacks
permanece reprovada no relatório, sem ser confundida com falha de renderização.
