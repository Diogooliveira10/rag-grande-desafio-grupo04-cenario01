# Qualidade de consultas e respostas da v6 — 30/09/2026

Meta de pelo menos 20 rotas atingida: **22/22 na CLI com `--openrouter-only`**, no
modo padrão de análise por regras. O acerto conjunto de rota/recusa foi **15/22**;
isso não mede correção factual. A implementação está em `joao-reis`, com corpus,
vetores, FAISS, ZIPs e gabarito original preservados. As tentativas anteriores e a
rodada completa permanecem separadas dos quatro retestes de reparos.

## Situação em 07/10/2026

Este documento registra a rodada de 30/09 e fica como estava. Várias pendências
citadas abaixo foram resolvidas depois:

| Pendência citada em 30/09 | Situação em 07/10 |
| --- | --- |
| Reranking real com ablação | Cross-encoder local no pipeline, com ablação em [ABLACAO_RERANK.md](ABLACAO_RERANK.md). O endpoint `/api/rerank` do servidor da turma entrou como opção em 07/10 e acertou menos que o local. |
| Feedback e histórico persistidos | `POST /api/v1/feedback` e `GET /api/v1/history`, com SQLite em [src/history_store.py](../src/history_store.py). Desde 07/10 o histórico grava textos mascarados e exige `X-API-Key`. |
| PostgreSQL executável | A base estruturada roda no Postgres do Neon (`DATABASE_URL`) e no SQLite local, com os mesmos números ([src/structured_db.py](../src/structured_db.py)). |
| Trilha conversacional de e-mails | Base `emails_sinteticos`, com 25 conversas, integrada em 05/10 ([emails_sinteticos.md](emails_sinteticos.md)). |
| Contexto entre perguntas | Perguntas de continuação são reescritas com as mensagens anteriores enviadas pelo front-end ([src/reformulation.py](../src/reformulation.py), 07/10). |
| Vigência normativa | 1.498 das 1.561 normas têm data de início de vigência desde o controle de qualidade de 07/10 ([qualidade_v6.json](../data/inventory/qualidade_v6.json)). |

Continuam abertos: preço unitário e equivalência técnica, que dependem de dados que
o corpus não tem. Os números de avaliação deste documento também são de 30/09: a
avaliação oficial mais recente é a de 05/10 (13/26, com critério de conteúdo e
fonte), e o corpus e a base SQL mudaram depois dela.

## Mudanças implementadas

- Análise opcional por LLM com intenções, bases, filtros, métrica, agrupamentos,
  período, referência temporal e tipo de valor. Uma chamada de análise por execução
  do pipeline, com cache separado por modelo, prompt, corpus e data; saída JSON estruturada,
  validação do contrato e fallback para o analisador existente. Operações analíticas
  incompletas e filtros categóricos ausentes da pergunta acionam o fallback.
- Recuperação e consulta SQL por intenção, intercalando evidências e eliminando
  duplicatas pelo hash do conteúdo. O texto original permanece no caminho de regras.
- SQL com limites exclusivos, períodos, filtros por órgão, agrupamentos compostos,
  contagem incluindo registros sem valor e mediana por grupo. Evolução informa
  média ponderada pelo número de registros, comparação primeiro/último mês e
  ausência de meses; não transforma meses ausentes em zero. Ranking apresenta
  o valor usado na ordenação e a data correspondente à referência temporal.
- Cálculo indisponível é indicado no contexto; o LLM recebe registros como exemplos,
  sem autorização para transformá-los em estatísticas. Valores globais não são
  usados para responder preço unitário. PostgreSQL continua limitado à compilação
  parametrizada offline, sem conexão, DDL ou execução do novo caminho.
- Contexto reserva evidência por intenção/base e representa registros JSON completos
  de forma compacta, preservando referências legais, modalidade explícita, valores
  zero, fornecedores e datas. Registros não são cortados no meio para caber no orçamento.
  IDs incluídos e descartados ficam registrados nos eventos.
- A geração diferencia categoria de processo e modalidade, cláusula prevista e
  ocorrência comprovada, média e tendência, snapshot e oportunidade aberta hoje.
  Essas instruções orientam o modelo; não constituem prova automática de correção.
- Fontes podem incluir URL pública de origem. O campo é omitido quando ausente,
  preservando respostas antigas. Recusas recebem texto não vazio. As políticas
  originais de recusa e mascaramento foram preservadas; não foram adicionadas
  novas políticas de bloqueio de conteúdo.
- O hook de reranking recebe candidatos antes do corte final e retorna ao RRF em
  caso de falha. Endpoint oficial de cross-encoder não foi fornecido: este hook
  testado não equivale à ablação real exigida pelo desafio. *(Resolvido depois:
  ver "Situação em 07/10/2026".)*

## Verificação e limites

526 testes offline passaram em 31,52 segundos. A checagem de whitespace passou.
Testes cobrem fallback, cache, contrato analítico, escopo SQL, média ponderada,
mediana, contagem, valores unitários indisponíveis, fontes públicas, recusas vazias,
orçamento de contexto e reranking antes do corte. Nenhuma dependência foi instalada.

A avaliação anterior da v6 permanece em `eval/runs/cli-v6`: 17/22 rotas,
15/22 decisões de recusa e 13/22 acertos conjuntos. Esses números não medem
correção factual. A nova tentativa salvou seis casos completos antes da interrupção;
não deve ser comparada como uma avaliação final, pois inclui revisões anteriores
às últimas correções do contrato.

| Caso parcial | Observação factual ou operacional |
|---|---|
| P2.1 | Diferenciou modalidade ausente de categoria e apresentou contratos; filtros inventados foram observados no plano anterior e motivaram validação adicional. |
| P3.2 | Persistiu associação inadequada de normas e faltou vincular a norma à modalidade do processo. Falha ainda aberta; rota correta não comprova resposta correta. |
| P4.3 | Informou falta de contexto para determinar a modalidade mais frequente, sem resposta vazia. |
| P5.3 | Recusou concluir aumento, porém o plano anterior tinha filtro de modalidade inadequado. Deve ser reavaliado após o fallback corrigido. |
| P5.2 | Geração falhou com `APITimeoutError: Request timed out.`; recusa de fallback não comprova falta de dados. |
| Cenário Cláudia | Análise e geração tiveram timeout; resultado não é avaliação válida da cobertura ou da qualidade. |

Os resultados parciais estão em `eval/runs/quality-v6/partial-responses.jsonl`;
as primeiras tentativas foram preservadas separadamente. Há eventos de rota,
análise, recuperação, SQL, geração e validação, com tokens, latência, cache e erros.
Uma chamada que termina em timeout pode não informar tokens consumidos no servidor;
não é possível calcular o custo total exato a partir desses eventos.

A ablação offline usa somente embeddings de perguntas já no cache e os índices
persistidos, sem chamadas ao ECOA. Duas âncoras foram conferidas no texto real:
contrato de ambulância tipo D e passagem normativa sobre pesquisa de preços.
O segundo julgamento avalia pertinência textual, **não vigência histórica**.

| Variante | Recall@10 médio das âncoras | nDCG@10 médio das âncoras |
|---|---:|---:|
| Densa | 1,000 | 0,356 |
| BM25 + densa + RRF | 1,000 | 0,815 |

São apenas duas perguntas, com julgamentos parciais e as mesmas bases da avaliação
anterior, sem filtros. Não representam Recall global nem demonstram melhora de
respostas. Material de escritório citado anteriormente era, em um caso, material
de acesso à internet: essa evidência foi excluída dos julgamentos pertinentes.
Resultado reproduzível: `eval/runs/quality-v6/retrieval-ablation.json`.

## Uso e retomada

O modo padrão continua `QUERY_ANALYSIS_MODE=regras` enquanto a análise por LLM
não passa pela avaliação completa. A geração de respostas segue usando o modelo
ECOA existente. Para avaliar o novo analisador explicitamente:

```powershell
$env:QUERY_ANALYSIS_MODE = "llm"
python app/cli.py rota "Pergunta de referência"
python app/cli.py perguntar "Pergunta de referência"
```

`python eval/evaluate_quality_v6.py` executa os 22 casos pela CLI usando LLM apenas
no OpenRouter Free e embeddings no ECOA/cache, de maneira
sequencial, com pausa de dez segundos entre casos e intervalo mínimo entre chamadas
reais. Reutiliza caches e retoma casos concluídos somente se o fingerprint do código
for o mesmo. Mudanças de código exigem preservar a tentativa anterior e começar outra.
Os comandos `rota` e `perguntar` compartilham a análise quando ela foi armazenada
com sucesso; análise que falha não é armazenada e pode ser tentada novamente pelo
segundo comando.
`python eval/evaluate_retrieval_v6.py` reproduz a ablação offline de âncoras.
`python -m pytest -q` mantém o modo por regras e cache temporário, sem ECOA.

O próximo passo após as primeiras tentativas era retomar a integração,
executar os 22 casos com a mesma revisão e conferir cada afirmação contra fonte real
e cálculo SQL. Separar falhas de cobertura, extração, metadados, recuperação,
geração e cálculo indisponível; não alterar o gabarito para favorecer resultados.
Ainda precisam ser medidos a taxa de fallback do analisador e o ganho factual real.

## Relação com o desafio

As mudanças reforçam roteamento, busca híbrida, proveniência, resposta estruturada,
observabilidade e cálculos determinísticos. Continuam pendentes: cross-encoder com
ablação real, feedback e histórico persistidos, execução PostgreSQL e a trilha
conversacional de e-mails. Atas e fixtures não substituem essa trilha automaticamente.
Vigência normativa e preço unitário continuam dependentes de cobertura adequada.
*(Situação em 30/09. Todas, menos preço unitário, foram resolvidas depois: ver
"Situação em 07/10/2026".)*

O esquema JSON usa o suporte descrito na documentação de
[compatibilidade OpenAI do Ollama](https://docs.ollama.com/api/openai-compatibility)
e de [saídas estruturadas](https://docs.ollama.com/capabilities/structured-outputs).
Compatibilidade sintática não garante que os filtros ou a interpretação sejam corretos.

## Fallback OpenRouter e modo exclusivo para testes

O cliente compartilhado em `llm_ecoa.py` mantém ECOA como principal no modo `auto`.
Timeout, erro de conexão, HTTP 404/408/429 ou 5xx acionam uma tentativa no OpenRouter,
quando `OPENROUTER_API_KEY` está configurada. Erros internos, HTTP 400/401/403 e
chave ausente não acionam essa troca. Cada provedor tem timeout de 60 segundos,
sem retries automáticos. Se ambos falharem, permanece o tratamento existente.

O modelo solicitado é exclusivamente `openrouter/free`, com endpoint oficial;
nenhum modelo pago é configurado como fallback. O roteador escolhe um modelo gratuito
compatível com os recursos solicitados, segundo sua
[documentação oficial](https://openrouter.ai/openrouter/free).
A implementação reaproveita o SDK OpenAI existente e o padrão de fallback do
projeto `rag-vendefacil-06-reis-gregorio`, sem incorporar suas dependências LangChain.

```powershell
python app/cli.py --openrouter-only perguntar "Pergunta de referência"
python app/cli.py rota --openrouter-only "Pergunta de referência"
```

O flag aceita as duas posições e impede chamadas ao **LLM** do ECOA. A análise
continua por regras por padrão; para testar a análise por LLM, definir também
`QUERY_ANALYSIS_MODE=llm`. Para outras entradas do projeto, `LLM_PROVIDER_MODE=openrouter`
tem o mesmo efeito. A variável `OPENROUTER_API_KEY` fica somente no ambiente ou `.env`
local; não é escrita nos logs. Os eventos registram provedor, modelo solicitado,
modelo efetivo, modo, uso do fallback e tipo/status do erro primário.

Os embeddings das perguntas continuam no modelo ECOA existente ou em seu cache.
Não são substituídos por embeddings OpenRouter, pois isso alteraria o espaço
vetorial da v6. Se a API de embeddings ficar indisponível, a recuperação continua
com BM25 nos mesmos documentos, bases e filtros, registrando `degraded=true` e
`recuperacao_degradada=bm25_sem_embedding`. Isso tem qualidade diferente de busca
híbrida e não é apresentado como uma execução densa bem-sucedida.

Verificação desta alteração: 543 testes offline passaram. Dois testes reais usaram
somente OpenRouter para LLM: geração pela CLI com
`nvidia/nemotron-3-super-120b-a12b:free` (3.759 tokens, cerca de 32 segundos) e análise
com `dots-studio/dots-3-note-preview:free` (1.439 tokens, cerca de 9 segundos).
A geração respondeu com citação e URL pública, sem inventar modalidade ausente.
A análise retornou plano incompleto sem métrica, rejeitado pelo contrato e substituído
pelo fallback por regras. Isso verifica conectividade e tratamento do plano inválido,
não melhora factual global. Modelos efetivos podem mudar entre chamadas ao roteador.
Os registros estão em `eval/runs/quality-v6/openrouter-smoke.txt`,
`openrouter-analysis-smoke.txt` e nos eventos de observabilidade.

## Rodada completa solicitada com `--openrouter-only`

A avaliação usa agora explicitamente o flag nos comandos `rota` e `perguntar`,
além do modo de provedor no ambiente. Nenhuma chamada de LLM desta rodada foi
encaminhada ao ECOA. Embeddings continuaram no ECOA/cache, conforme solicitado.

A primeira tentativa salvou três casos. Houve geração sem objeto JSON, consumindo
todo o limite de 1.536 tokens. Não há registro do raciocínio dessa resposta: a
hipótese de consumo do orçamento por raciocínio é compatível com a
[documentação do OpenRouter](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens),
mas não foi comprovada para aquele retorno.

Foi testado `reasoning.enabled=false`. A segunda tentativa salvou sete casos,
porém o roteador selecionou endpoints que exigem raciocínio e retornou HTTP 400:
`Reasoning is mandatory for this endpoint and cannot be disabled.` Quatro desses
casos tiveram falha de geração; sua recusa de fallback não significa falta de
dados. Conforme o protocolo de falha, as chamadas externas foram interrompidas.
O parâmetro incompatível foi retirado do código; a configuração atual volta a
aceitar modelos com raciocínio obrigatório. Os resultados das duas tentativas
foram preservados, sem serem promovidos a avaliação final de 22 perguntas.

As correções locais que permaneceram são:

- Esquema JSON explícito para a resposta, incluindo texto não vazio, campos
  esperados e citações restritas aos IDs realmente fornecidos ao modelo.
- Registro do motivo de término, tamanho do conteúdo e quantidade de tokens de
  raciocínio quando informada pelo provedor, sem gravar o texto do raciocínio.
- Pergunta de lista publicada **por órgãos** não aciona agrupamento SQL por uma
  correspondência parcial de `por orgao`. Plano analítico sem solicitação de
  cálculo é rejeitado e usa as regras existentes.
- Plano inválido tem fallback por regras reutilizado por cinco minutos, evitando
  repetir imediatamente a análise nos comandos `rota` e `perguntar`. Erros de
  transporte/API não são armazenados nesse cache temporário.
- O cache considera o orçamento de saída. O manifesto da execução considera
  modelo, orçamento e modo do provedor; a retomada não mistura configurações.

Entre os casos gerados com sucesso, P2.1 distinguiu modalidade ausente, valor
inicial e valor global, com duas fontes públicas. P4.3 informou ausência de contexto
para determinar a modalidade mais usada. P5.3 recebeu SQL filtrado por objeto e
não transformou a comparação entre primeiro/último mês em prova de crescimento
monotônico. Isso é evidência parcial; não resolve a avaliação factual de todo o conjunto.

As tentativas estão em `eval/runs/quality-v6/openrouter-initial-responses.jsonl`
e `openrouter-reasoning-disabled-responses.jsonl`, com logs e manifestos separados.
O caminho `responses.jsonl` ficou livre para uma execução nova na revisão atual.

Próximo passo recomendado: testar orçamento de saída maior, mantendo o raciocínio
aceito pelo modelo e os dados da v6 congelados, e então executar os 22 casos:

```powershell
$env:OPENROUTER_MAX_TOKENS = "4096"
python eval/evaluate_quality_v6.py
```

O aumento do orçamento foi verificado na rodada completa descrita abaixo. Não
foram instaladas dependências, alterados artefatos do corpus ou publicados commits desta etapa.

## Meta de 20 rotas: resultado completo

As cinco lacunas corrigidas nas regras eram gerais: editais/especificações,
valor médio, ranking de registros individuais e pergunta normativa sobre pesquisa
de preços. Os testes usam exemplos adicionais que não são cópias das PERSONAS.
O analisador por LLM continua opcional; quando usado, a referência temporal
explícita de publicação/assinatura completa o campo omitido pelo modelo.

| Medida | v6 anterior | Rodada OpenRouter padrão |
|---|---:|---:|
| Rota correta | 17/22 | **22/22** |
| Decisão de recusa igual ao gabarito | 15/22 | 15/22 |
| Acerto conjunto de rota/recusa | 13/22 | 15/22 |

Foram executados os comandos `rota` e `perguntar` para todos os 22 casos,
incluindo as 18 perguntas das personas, a falha esperada e os três exemplos do
desafio. Configuração: análise por regras, LLM exclusivamente OpenRouter Free,
orçamento de saída de 4.096 tokens, intervalo de dez segundos e corpus v6 congelado.
O orçamento de 4.096 tornou-se o padrão OpenRouter; ECOA mantém seu próprio caminho.

Os eventos registraram 68.871 tokens retornados pelo SDK e latência mediana
combinada dos dois comandos de 14,59 segundos, excluindo as pausas do avaliador.
Houve 20 reutilizações de embeddings de perguntas e zero chamadas novas de
embedding nessa rodada. Nenhum LLM ECOA foi chamado. Um caso teve HTTP 429 no
pool compartilhado do provedor gratuito; esse erro foi classificado como API,
não como ausência de evidência. Não houve retry automático nem fallback pago.

A auditoria validou hashes de 14 arquivos brutos e 16 chunks citados; fontes SQL
foram associadas aos eventos de cálculo. A revisão factual identificou limitações
mesmo em respostas que acertaram rota e recusa. Ela está em
`eval/runs/quality-v6/factual-review.json`, separada da pontuação do gabarito.

Reparos repetidos em quatro casos, sem misturar resultados na rodada completa:

- P2.1 passou a informar separadamente `valorGlobal=0` e `valorInicial=1950000`,
  sem interpretar zero como ausência. A fonte de locação genérica de ambulância
  não foi apresentada como comprovação de tipo D.
- P4.1 passou a reconhecer que manutenção de sistemas não comprova manutenção
  de computadores, em vez de afirmar equivalência técnica.
- F1 identificou pedido de preço por veículo: não recebeu SQL de mediana global
  e recusou a comparação sem unidade, pagamento e equivalência técnica comprovados.
- Cláudia recebeu uma explicação completa da insuficiência de evidência, mas a
  recuperação ainda não resolve a parte normativa sobre MEI. Abertura atual de
  pregões não pode ser confirmada pelo snapshot histórico.

Os pedidos por veículo, ambulância, quilograma ou unidade agora são classificados
como unitários também no modo por regras. Quando o cálculo não está disponível,
isso é informado no contexto. Limites monetários acima/abaixo são exclusivos
também nesse modo. A validação final e os testes de fronteira ficam nos relatórios
de testes da rodada, com cache temporário e sem rede. A suíte final passou com
**558 testes**, incluindo os limites exclusivos de valores; `git diff --check`
também passou. A branch permaneceu `joao-reis`, sem novos commits ou push.

O gabarito original marca `is_refusal=false` inclusive em F1. Ele foi mantido;
recusar uma conta sem dados pode diminuir essa pontuação sem piorar a fidelidade.
**22 rotas corretas não significa 22 respostas factualmente corretas.** Permanecem
pendências de recuperação em material de escritório, contexto entre perguntas,
vigência normativa e dados de preços unitários/equivalência técnica. Reranking real,
feedback/histórico persistidos, PostgreSQL executável e e-mails continuam pendentes.
*(Situação em 30/09. Todas, menos preços unitários e equivalência técnica, foram
resolvidas depois: ver "Situação em 07/10/2026".)*

Artefatos: `eval/runs/quality-v6/{responses.jsonl,report.json,REPORT.md,source-audit.json}`
e `eval/runs/quality-v6-repairs/`. Reteste reproduzível:

```powershell
python eval/evaluate_quality_v6.py --case F1 --case P2.1 --output outra-tentativa
```

Por padrão, o avaliador usa análise por regras. Para um experimento separado do
analisador LLM, definir `QUERY_ANALYSIS_MODE=llm` e usar outro diretório de saída.
O manifesto impede retomar uma execução com outra revisão/configuração. A coleta,
os embeddings de documentos, índices e ZIPs não foram modificados nesta etapa.
