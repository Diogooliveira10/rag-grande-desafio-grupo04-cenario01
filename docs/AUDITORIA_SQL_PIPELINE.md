# Auditoria do SQL, analisador de filtros e pipeline

## Resumo executivo

**O problema não é apenas acrescentar duas regexes.** Há três interpretações parcialmente independentes da pergunta — análise, SQL e filtros vetoriais — e o pacote ativo local está incompatível com o código atual.

Principais conclusões verificadas:

1. **Bloqueio operacional:** o código exige schema SQL **7**, mas o banco ativo local declara **6**. `validar_banco()` rejeita esse banco; a consulta retorna `[]` e o pipeline tenta recuperação como fallback.
2. **R15 não é uma falha universal do SQL.** Com banco compatível, o SQL já reconhece o plano de contratações e resolve a unidade responsável. Reproduzi o pipeline sem modelos: respondeu ao PCA tanto no candidato quanto no controle v6/schema 7.
3. **R19 exige mais que corrigir município.** O analisador confunde localização com órgão; além disso, o SQL operacional não materializa itens/resultados nem suporta a métrica unitária pedida. Um total de contrato não pode substituí-la.
4. **O contrato de filtros não é uniforme.** Existem campos aceitos pelo schema que desaparecem na tradução para recuperação, campos que o SQL tipado rejeita e períodos que passam a significar coisas diferentes conforme o executor.
5. **Schema válido não assegura interpretação válida.** Sondas com respostas LLM simuladas aceitaram PCA sem referência temporal compilado como contratos, preço unitário sem marca de unidade e órgão inventado.
6. **A validação ampliada não está verde:** **697 testes passaram e 6 falharam**. As seis falhas repetiram isoladamente. Não alterei código da aplicação para escondê-las.

Nesta etapa foram criados apenas scripts/artefatos de auditoria e um banco de controle em diretório novo. **Não houve promoção, reconstrução do banco ativo, re-embedding, troca de modelo ou alteração do golden.** As conclusões operacionais dizem respeito ao código e à configuração local carregados na auditoria; não provam qual revisão um processo de servidor eventualmente já aberto mantém em memória.

## 1. Escopo e evidências

Artefatos locais: `data/runs/auditoria-sql-pipeline-2026-10-18/`.

- Varredura integral dos **10 JSONLs canônicos** em `logs/pipeline/`: **334,19 MiB**, **10.997 eventos**, **0 linhas JSON inválidas**. Não usei apenas `tail`, nem o índice derivado de traces.
- Varredura suplementar de **418 arquivos**, **356.053 linhas**, **44,36 MiB**: logs/textos/JSONLs de `data/runs/`, `eval/runs/` e logs diretamente em `.tmp/`, excluindo vetores, fontes arquivadas e clones/fixtures duplicados aninhados. Inventário e hashes em `supportive_log_inventory.json`.
- Inspeção do código, schemas e configuração efetiva, sem exibir credenciais.
- Bancos existentes abertos somente para leitura. Um controle v6/schema 7 foi construído apenas em `reproduction/v6_schema7_control.sqlite`.
- Reproduções reais de análise, SQL, suficiência e orquestração. Qualquer recuperação/modelo no teste ponta a ponta era bloqueado antes de tráfego externo.
- Sondas LLM usam respostas JSON **simuladas**: demonstram lacunas de validação, não a frequência de erros de um provedor real.

Arquivos principais:

| Artefato | Conteúdo |
| --- | --- |
| `logs/summary.json` | Estatísticas, inventário, hashes e cobertura integral |
| `logs/correlated_groups.json` | Agrupamentos por IDs utilizáveis, sem inventar envelopes |
| `logs/focal_events.jsonl` | Eventos completos disponíveis de R15/R19, com arquivo/linha e mascaramento |
| `audit_metadata.json` | Configuração pública, schemas, hashes e resultados dos testes |
| `reproduction/reproduction.json` | Planos, SQL, recortes e respostas das reproduções isoladas |
| `semantic_probes/semantic_probes.json` | Paridade de campos, planos simulados e colisão de papéis do CNPJ |
| `query_plans.json` | Índices existentes e `EXPLAIN QUERY PLAN` somente leitura |
| `unit_price_corpus_inventory.json` | Presença do campo unitário no corpus, distinta de suporte no SQL |
| `tests/` | Saída integral da seleção ampliada e do recheck das seis falhas |

## 2. Como o pipeline funciona hoje

```text
pergunta + histórico
  -> reformulação, se aplicável
  -> tratamento de conversa
  -> query_analysis
       regras: QueryFilters, sem intenções
       LLM: QueryIntent(s) + QueryFilters; fallback para regras
  -> roteamento por regras ou bases das intenções, preservando guardrails
  -> planejamento SQL, quando a operação é suportada
       resultado SQL suficiente -> pula dense/BM25/RRF/rerank
                                 -> formatação determinística com fontes
       não aplicável/insuficiente/erro -> recuperação híbrida
  -> SQL suplementar, quando aplicável
  -> seleção/mascaramento/orçamento das evidências
  -> resposta determinística SQL ou geração LLM + validação
  -> resumo e encerramento do trace
```

Referências: `src/pipeline.py:86,190`, `src/query_analysis.py:109,1709`, `src/router.py`, `src/generate.py:726,746`.

### SQL operacional versus compilador de fatos

O SQL em uso trabalha com `contratacoes`, `pca_planos` e `meta` (`src/structured_db.py`). As consultas são fixas, com valores parametrizados. SQLite é o destino efetivo local; `DATABASE_URL` poderia selecionar Postgres, mas **não conectei a banco remoto**.

- `detectar_consulta()` faz planejamento por regras (`src/structured_query.py:387`).
- `consulta_da_intencao()` faz planejamento pelo plano LLM tipado (`:581`).
- `_where()` compila o recorte operacional (`:639`).
- `executar_consulta()` resolve nomes/modalidades e executa, incluindo relaxamento de termos (`:1136`).
- Mediana operacional é calculada em Python sobre valores recuperados do banco. Não depende de busca vetorial.

Existe também `compilar_consulta_postgres()` (`:255`), com contratos de `contratos_fatos` e `pca_fatos`. **Essas tabelas não são criadas pelo construtor operacional, e esse compilador não é chamado pelo pipeline.** Encontrar um teste dele não demonstra suporte de produção a essas métricas. Mudar apenas o destino para Postgres não implementaria itens/resultados unitários.

## 3. Pacote ativo: incompatibilidade reproduzida

Configuração efetiva: análise por **regras**, SQL **SQLite**, documentos em `data/normalized/documents.jsonl`, índice em `index/`, reranker local com pool **10**, detalhamento de evidências **completo**.

| Componente | Estado observado |
| --- | --- |
| Runtime SQL | `SCHEMA_VERSION = "7"` |
| Banco ativo | `data/structured/base_estruturada.sqlite`: **schema 6**, 810 contratações, 1 PCA |
| Candidato separado | `candidato_07_reparo/base_estruturada.sqlite`: schema 7, 106.667 contratações, 1 PCA |
| Controle isolado | v6 reconstruída em caminho novo com schema 7: validação aprovada |

O banco ativo tem o hash correto dos documentos v6, mas **schema incompatível**. `validar_banco()` (`src/structured_db.py:451`) falha antes da consulta. `consultar_estruturado()` (`src/structured_query.py:1366`) registra o erro e retorna `[]`. O fallback protege a disponibilidade, mas não resolve o cálculo que dependia do SQL.

Há ainda inconsistência de proveniência: o manifesto global de `index/` declara origem `672137f4…`, enquanto os documentos atuais e o banco v6 declaram `d8a4f47f…`. As contagens por base coincidem, portanto **isso não prova que todos os vetores estejam incorretos**, mas impede assumir um pacote coerente só pela contagem. Não se deve “corrigir” apenas o texto do hash sem conferir os artefatos.

O `/health` atual é liveness (`app/api.py:296`): sempre retorna `status=ok`, sem validar prontidão SQL/índice. Recomenda-se um readiness separado, não transformar uma resposta de liveness em garantia de integridade semântica.

## 4. R15: o benchmark de recuperação não cobriu o caminho SQL

Pergunta: quanto a Secretaria de Estado de Saúde planejou gastar no plano de contratações de 2025.

### Análise e recuperação

O analisador extrai ano e órgão, mas não identifica `referencia_temporal=pca` quando se usa “plano de contratações” em vez de “PCA”. O tradutor genérico emite `data_vigencia`.

**Nuance importante:** na recuperação, vigência contratual é inaplicável à natureza agregada e pode ser ignorada. Não atribuo a perda do PCA somente a esse filtro. A incompatibilidade efetiva é também de papel: o plano tem `orgao_nome=ESTADO DO RIO DE JANEIRO`, e a Secretaria aparece em `unidade_nome=SECRET ESTADO SAÚDE`. O filtro vetorial de órgão não resolve essa unidade.

### SQL e reprodução do pipeline

O planejador SQL possui reconhecimento próprio de “plano de contratações”, seleciona `pca_planos`, usa `ano_pca=2025` e resolve o nome tanto em órgão quanto em unidade, incluindo abreviações.

| Banco usado explicitamente na reprodução | Resultado |
| --- | --- |
| Ativo/schema 6 | Rejeição na validação; seguiria para recuperação |
| Candidato/schema 7 | SQL suficiente; resposta sem modelos |
| Controle v6/schema 7 | SQL suficiente; resposta sem modelos |

Nos dois bancos compatíveis, a resposta apresentou **R$ 3.008.979.309,91**, com fonte SQL e aviso de que se trata de **um plano de unidade presente na amostra**, não do total estadual.

**Correção ao diagnóstico simplificado anterior:** R15 não exige necessariamente “ensinar o SQL a usar PCA”; esse caminho já funciona. É necessário remover a incompatibilidade do pacote ativo e unificar a interpretação usada pelos demais caminhos.

## 5. R19: localização, tipo de valor e grão dos dados

Pergunta: mediana do preço unitário homologado de serviço de engenharia no município do Rio.

Foram reproduzidos três impedimentos:

1. “No município do Rio” vira órgão `MUNICIPIO DE RIO DE JANEIRO`, em vez de localização. A extração de órgão pode suprimir o município em `filtros_analiticos` (`src/query_analysis.py:1756` em diante).
2. A regra de tipo unitário reconhece “por unidade/veículo/quilo…”, mas não a expressão literal “preço unitário” (`:1799`). Nesta pergunta, `tipo_valor` fica ausente.
3. “Homologado” gera referência `resultado`; o SQL operacional exclui consultas com essa referência ou com `tipo_valor=unitario`. `detectar_consulta()` e `consulta_da_intencao()` retornam `None`, inclusive com banco schema 7.

Não existe coluna de preço unitário, item ou resultado nas tabelas SQL operacionais. No corpus v6 há **um documento** com `valorUnitarioHomologado`, `quantidadeHomologada`, `numeroItem`, `sequencialResultado`, `dataResultado` e identificação da compra. A presença desse JSON na base agregada **não o transforma em tabela de fatos consultável**.

A solução deve materializar, quando houver cobertura, o grão **compra × item × resultado/fornecedor × versão**, ligar a descrição/unidade do item e a localização da compra, e conservar status/cancelamentos. Não usar localidade do fornecedor como município da contratação.

Também é preciso definir comparabilidade: mediana de preços por m² não se mistura automaticamente com preços por hora ou por serviço. Falta de fatos/unidade deve produzir “métrica indisponível” ou esclarecimento, nunca mediana de valores globais ou de um top-k recuperado.

## 6. Inconsistências semânticas verificadas

### Campos aceitos não equivalem a filtros executados

Sondas com o mesmo `QueryFilters`:

| Campo | Tradutor da recuperação | Plano SQL tipado |
| --- | --- | --- |
| esfera, município, UF, modalidade, fornecedor | Emite filtro | Aceita |
| `orgao_nome`, `unidade_nome` | **Não emite** | Aceita |
| `mes_assinatura`, `mes_publicacao` | **Não emite** | Aceita |
| categoria, classe | **Não emite** | Rejeita por ausência de coluna |
| CNPJ | Emite filtro genérico | Rejeita; `cnpj` não é nome físico de coluna |

O CNPJ é suportado no SQL por regras por um tratamento especial em `_where()`, mas não pelo mesmo caminho tipado. Assim, mudar de modo pode mudar a capacidade disponível sem mudar a pergunta.

### Papéis do CNPJ

Na fixture sintética, um registro tinha o CNPJ pedido como fornecedor; outro, como órgão. A pergunta especificava **fornecedor**. O SQL aplicou:

```sql
(orgao_cnpj = ? OR fornecedor_cnpj = ?)
```

e retornou os dois. O lookup exato vetorial já tem tratamento de fornecedor em parte desse fluxo, mas isso não corrige automaticamente a coorte SQL. É preciso representar o papel explicitamente no plano lógico.

### Referência temporal

Para “contratos de 2025”, sem referência explícita:

- recuperação emite `data_vigencia`;
- SQL infere assinatura e usa `ano_assinatura`.

O mesmo ano não representa a mesma população. O default precisa ser resolvido uma vez no domínio, antes de escolher executor.

### Limites da validação LLM

Respostas simuladas passaram como `metodo=llm` nos seguintes casos:

- pedido de PCA com base agregada e referência ausente → compilado para `contratacoes`;
- pedido de preço unitário com `tipo_valor` ausente → plano sobre valor global;
- `filtros.orgao` inventado → aceito, embora outros campos tenham checagens de presença na pergunta.

Isso é uma **demonstração de lacuna do validador**, não afirmação de que um modelo real produziu essas saídas nos logs analisados. Além disso, validar nome canônico por substring pode rejeitar uma abreviação legítima: menção original e entidade resolvida devem ser campos distintos.

## 7. O que os logs completos permitem concluir

Dos 10.997 eventos, **2.378** usam IDs padrão/ausentes e não permitem correlação segura. Entre pares com IDs utilizáveis:

- 272 envelopes fechados: **264 de consulta** e **8 offline**;
- 304 grupos só de eventos de módulos, sem envelope completo;
- 2 inícios sem encerramento, ambos offline.

As consultas fechadas têm mediana histórica de **3.114 ms** e p95 de **57.493 ms**. São versões/origens diferentes, não SLA da versão atual. O máximo global de aproximadamente 15 horas é operação offline; não é latência de chat.

Achados adicionais:

- Os **39 eventos exatos de R15** e **41 de R19** contêm recuperação e análise, **sem SQL/geração/envelope**. Não comprovam, isoladamente, o comportamento ponta a ponta.
- Há 243 eventos SQL `ok`, todos sobre `contratacoes`; nenhum evento canônico de SQL PCA nessa captura. Os testes/reproduções novos estão em logs isolados, não foram misturados à contagem histórica.
- 41 `ModuleNotFoundError` de rerank são de **09/10 e 13/10**, não evidência de dependência faltante nesta auditoria.
- 34 fallbacks antigos de análise aparecem com status `ok` em 30/09. Não tratar `status=ok` legado como prova de ausência de fallback.
- Ler o arquivo inteiro não recupera conteúdo que o logger original omitiu/cortou. A auditoria conserva contadores de candidatos/consultas omitidas e a distinção entre resumo e detalhamento completo.

## 8. Testes e desempenho

Seleção ampliada: **697 passed, 6 failed em 36,32 s**. Recheck dos seis, isoladamente: **6 failed em 1,76 s**, código de saída 1.

- Quatro falhas desempacotam exatamente um chunk SQL, mas o resultado atual vem em dois. São pressupostos de empacotamento a revisar; isso, por si só, não prova cálculo incorreto.
- Uma fixture espera construção implícita do banco no atendimento, removida pelo contrato atual de preparação explícita.
- Uma fixture de auditoria mocka `garantir_banco`, mas o runtime usa `validar_banco`; seu banco vazio é rejeitado.

Ajustar os testes ao contrato correto e conferir a integridade do chunking é necessário; não remover validação de snapshot para fazê-los passar. Em uma sonda adicional com o banco de controle **já preparado** e orçamento de 400 caracteres, o ranking produziu 16 chunks, máximo de 363 caracteres: não houve estouro nessa reprodução (`chunk_budget_probe.json`). Isso não prova todos os casos, mas separa falha de preparação de uma falha do particionamento. A seleção não é a suíte inteira do projeto.

`EXPLAIN QUERY PLAN` mostrou `SCAN contratacoes` para filtro por assinatura e por CNPJ de fornecedor no candidato. Há índice de publicação e de ano PCA, mas não os índices correspondentes a essas consultas. É otimização a dimensionar após corrigir a semântica, não prova de que todo `SCAN` é inadequado.

Na recuperação, `dense_retrieve()` relê JSONLs e pode carregar os mesmos vetores por mais de um caminho; com filtros, pode reconstruir FAISS em memória. BM25 tokeniza documentos novamente. O cache usado nos experimentos não existe automaticamente no pipeline de produção. Falha do SQL empurra cálculos para esse caminho mais caro, amplificando o bloqueio operacional.

## 9. Soluções pesquisadas e recomendadas

### P0 — Pacote coerente e prontidão verificável

- Preparar em caminho novo um bundle com documentos, índice, SQL, catálogo, schema e hashes compatíveis; executar validação antes da ativação.
- Não reconstruir banco durante a pergunta e não apenas reescrever hashes/version strings.
- Readiness deve indicar capacidades indisponíveis e motivo (`schema_incompativel`, `snapshot_divergente`), distinguindo-as de ausência de dados.
- A escolha de ativar v6 recomposta ou candidato V7 continua administrativa e requer aprovação. O candidato ainda tem limitações de proveniência/cobertura já documentadas.

### P1 — Um plano semântico compartilhado

Reaproveitar `QueryIntent` como ponto de partida, sem deixar o LLM escrever SQL. Acrescentar/resolver conceitos de dataset, grão, operação, medida, unidade, papel temporal, papéis de entidades, versão e evidência textual de cada restrição.

```text
regras OU LLM
  -> plano lógico com menções e papéis
  -> resolução contra catálogo/capacidades do snapshot
  -> validação semântica cruzada
  -> compilador SQL OU tradutor de filtros de recuperação
  -> execução + relatório de restrições aplicadas/não suportadas
```

Exemplo R15: fato PCA por unidade, soma de valor planejado, ano do plano 2025, Secretaria como unidade responsável. Exemplo R19: resultados por item, mediana de preço unitário homologado, unidade comparável e município como localização.

Cada restrição deve ficar como **aplicada, não suportada, inaplicável justificada ou ambígua**. Não pode simplesmente desaparecer. Campos ausentes não são zero; uma operação indisponível não é uma lista vazia.

Validadores Pydantic podem verificar combinações: `unitario` exige medida/grão de item; `pca` exige ano do plano; CNPJ exige papel ou ambiguidade explícita. A aderência ao JSON Schema não valida sozinha esses significados.

### P1 — Fatos de item e política de relaxamento

Materializar resultados unitários com chaves/versões e cobertura declarada antes de habilitar R19 no SQL. Manter medida contratada, estimada, homologada total e homologada unitária separadas. Não transportar automaticamente a regra atual de excluir valores abaixo de R$ 1,00 das estatísticas para preços unitários: centavos podem ser valores legítimos por item. Definir precisão/escala e arredondamento da medida antes de migrar o atual armazenamento `REAL`/`DOUBLE PRECISION` para fatos financeiros mais detalhados.

O relaxamento SQL atual tenta AND, termos parciais e OR. Há marca de execução parcial, o que é positivo, mas esse comportamento não equivale aos filtros obrigatórios da recuperação. Restrições explicitamente obrigatórias não devem virar uma coorte ampliada sem política visível.

### P2 — Observabilidade, cache e desempenho

- IDs únicos por execução/caso/snapshot nos benchmarks; não usar `sem-run-id` para reconstruir um trace completo.
- Guardar plano resolvido, capacidades, filtros efetivamente aplicados, revisão/catálogo/snapshot e motivos de fallback.
- Separar custo de análise, SQL, I/O de recuperação e inferência. O conceito de contexto de trace da OpenTelemetry serve de referência, sem exigir trocar o logger existente.
- Cache de análise deve incluir schema/plano/catálogo/snapshot e provedor/modelo efetivos. Hoje há referências fixas à v6 e a chave não inclui todos os componentes que podem mudar a análise.
- Reusar FAISS/binários e catálogo de documentos por snapshot imutável, mantendo mapeamento de IDs e filtros. Avaliar seletores de IDs conforme a versão/tipo do índice; não presumir mmap universal.
- Escolher índices SQL com planos e medição de carga; candidatos incluem data/ano de assinatura, tipo, CNPJ e vínculos. Não adicionar índices em produção nesta auditoria.

### Critérios de aceitação da próxima implementação

1. Pacote ativo passa na validação de schema/hash sem mutação durante consulta.
2. R15 responde por SQL determinístico, com ano do plano e unidade corretos.
3. R19 usa fatos unitários e unidades comparáveis; sem cobertura, explica indisponibilidade, não calcula sobre totais.
4. Testes mínimos distinguem “na cidade” de “da prefeitura”, CNPJ de órgão de fornecedor e assinatura de vigência/publicação.
5. Todo campo aceito pelo plano tem suporte explícito nos executores ou rejeição diagnosticada.
6. A seleção ampliada fica verde e os 47 casos voltam a ser avaliados com configurações comparáveis; relevância alternativa de R32/R33 continua separada do golden congelado.

## 10. Fontes primárias e limites da pesquisa

- [PNCP, manual 2.5, planos por órgão e ano](https://pncp.gov.br/manual/pt-br/2.5/plano_de_contratacao/consultar_plano_das_unidades_por_orgao_e_ano.html): distingue `anoPca`, `nomeUnidade` e `razaoSocial`; conteúdo consultado diretamente.
- [PNCP, resultado do item](https://pncp.gov.br/manual/pt-br/latest/contratacao/inserir_resultado_do_item_de_uma_contratacao.html): seção localizada na pesquisa sobre preço unitário/total. O fetch direto dessa página falhou; a existência dos campos no caso analisado foi verificada também no corpus local. Não tratei a página como integralmente lida.
- [dbt Semantic Models](https://docs.getdbt.com/docs/build/semantic-models) e [Measures](https://docs.getdbt.com/docs/build/measures): separação de entidades, dimensões, agregação e dimensão temporal. A documentação atual menciona mudança de especificação e depreciação de measures em favor de simple metrics; uso aqui os conceitos, não recomendo instalar dbt para este projeto.
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs): schema adherence não elimina mistakes; entrada sem relação com schema pode induzir hallucinations. Referência de limites, não sugestão de trocar o provedor.
- [Pydantic Validators](https://docs.pydantic.dev/latest/concepts/validators/): validação de modelo para regras entre campos.
- [OpenTelemetry Traces](https://opentelemetry.io/docs/concepts/signals/traces/): identidade e propagação de contexto, spans e relação pai/filho.
- [SQLite EXPLAIN QUERY PLAN](https://www.sqlite.org/eqp.html): inspecionar uso de índices; o formato é de depuração e pode variar entre versões.
- [FAISS Index I/O](https://github.com/facebookresearch/faiss/wiki/Index-IO,-cloning-and-hyper-parameter-tuning) e [Search parameters](https://github.com/facebookresearch/faiss/wiki/Setting-search-parameters-for-one-query): persistência e seleção de IDs, com limitações por tipo de índice.

Foram localizadas referências antigas de self-query do LangChain, mas os endereços atuais tentados retornaram 404. Não baseei o plano em uma API não verificada, nem proponho reescrever o projeto com outro framework.

## Reprodução

```powershell
py -m eval.auditar_logs_sql_pipeline --saida data/runs/NOVO_DESTINO/logs
py -m eval.reproduzir_auditoria_sql_pipeline --saida data/runs/NOVO_DESTINO/reproduction
```

Ambos recusam destino existente. A reprodução cria somente seu banco de controle e logs isolados. As sondas adicionais estão em `eval/sondar_semantica_sql_filtros.py`, com caminho fixo protegido contra sobrescrita. Arquivos e comandos usados estão arquivados no diretório da auditoria. Dados/logs locais e bancos de controle não são substituídos por documentos do Git.
