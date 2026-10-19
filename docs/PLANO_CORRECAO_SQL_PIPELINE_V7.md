# Plano de correção do SQL e pipeline — alvo V7

**Estado: V7 promovido para demo; sem ativação de modelos externos.**

**Registro de execução:** E0 concluída; validador/readiness de E1 e partes de E3 implementados, sem roteamento de runtime para o bundle; testes offline atuais: **1.861 passaram**, `eval.run_eval check`: **31 casos** válidos. A regra aprovada para Secretaria no PCA e a métrica de média unitária (R19 aproximado) foram implementadas nos caminhos de regras, LLM, SQL e recuperação. **V7 promovido para demo** em 2026-10-18: documents.jsonl (629 MB, 144.112 chunks), SQLite (121 MB, 106.667 contratações + 1 PCA) e índice FAISS (143.299 vetores) copiados para os caminhos padrão; backup V6 preservado em `data/backup_v6_*`. Limitações conhecidas: vetores com linhagem Q4/Q8 mista (não homogênea), 813 documentos sem vetor, encoding provenance não comprovado. Proveniência/embedding, fatos unitários granulares, avaliação integral e rollback permanecem gates. Nenhuma coleta, re-embedding, chamada a provedor de modelo ou promoção de produção foi executada.

Base: [auditoria SQL/pipeline](AUDITORIA_SQL_PIPELINE.md), [reparo V7](REPARO_V7.md), [diagnóstico de regressões](DIAGNOSTICO_REGRESSOES_V7.md) e código atual.

## 1. Decisão e limites

O usuário escolheu **preparar a V7 como alvo único da primeira entrega operacional**. Não haverá uma entrega intermediária de recuperação da v6. Isso **não autoriza promoção**, re-embedding, coleta adicional, consumo de inferência paga nem alterações do golden.

- Usar `data/v7_recuperacao/2026-10-15/candidato_07_reparo/` como origem imutável; produzir derivados em destinos novos, recusando sobrescrita.
- Preservar v6, candidatos anteriores, logs e relatórios. O working tree já contém alterações; não usar `reset`, `checkout` de arquivos ou reconstrução por cima para obter uma baseline artificialmente limpa.
- Manter SQLite como alvo local; não migrar para Postgres, trocar modelos/reranker ou aumentar o pool padrão de 10 neste escopo.
- Reutilizar as ferramentas administrativas existentes. Não introduzir outro framework de RAG nem permitir SQL escrito pelo LLM.
- Os desenhos abaixo são propostas de implementação. Decisões de domínio já aprovadas estão registradas no estado da aplicação; encoding e armazenamento financeiro ainda exigem aprovação nos respectivos gates antes de mudanças de contrato/schema.

### Estado conhecido, não revalidado por execução de testes neste planejamento

| Área | Evidência da auditoria |
| --- | --- |
| Pacote ativo local | SQL schema 6 versus runtime 7; manifesto global do índice com origem divergente dos documentos |
| Candidato V7 | SQL schema 7 e hash dos documentos compatível, mas isso não certifica todo o pacote |
| Vetores V7 | 144.112 documentos, 143.299 vetores identificados; faltam 64 atas e 749 contratos; linhagem Q4/Q8 mista e encoding não integralmente atestado |
| R15 | SQL já funciona em banco compatível; valor reproduzido de R$ 3.008.979.309,91 para um plano de unidade de 2025 |
| R19 | Sem fatos SQL unitários; presença de um resultado no corpus v6 não demonstra cobertura da V7 nem comparabilidade |
| Testes | Seleção auditada: 697 passaram, 6 falharam; não é a suíte completa |
| Recuperação | 47 casos avaliados com rotas do gabarito; R32/R33 continuam regressões estritas no golden original |

## Estado da aplicação até aqui

- **E0:** baseline e seis falhas preservadas em `data/runs/implementacao-sql-pipeline-*/`; fixtures agora conferem chunks SQL completos e preparam o banco explicitamente. A validação de snapshot não foi relaxada.
- **E1:** `src/snapshot.py` valida hashes do bundle, SQL somente leitura/schema, catálogo contra a origem SQL, proveniência/cobertura por índice, payloads e cardinalidade FAISS. `GET /ready` é separado do liveness `/health`; sem manifesto, readiness retorna 503. O contrato não troca caminhos de runtime automaticamente.
- **E3 (parcial):** `analisar_query` materializa referência temporal antes dos executores: contratos → assinatura; compras/contratações → publicação; PCA/plano → ano do PCA; vigência expressa conserva sua semântica. SQL/retrieval recebem a mesma referência. CNPJ explicitamente ligado a fornecedor/órgão fica restrito à coluna/papel correspondente. Para PCA, Secretaria sem papel explícito resolve para `unidade_nome`; menção explícita a “órgão” resolve para `orgao_nome`. A métrica `media_unitaria` (preço médio por item do PCA) é calculada como `SUM(valor_total) / SUM(quantidade_itens)` quando a pergunta é explícita sobre medida unitária no PCA. Medida unitária em contratos individuais continua sem suporte (sem fatos de item). “No município do Rio” não se transforma em filtro de prefeitura.
- **E3 ainda parcial:** apesar da decisão de papel do PCA e dos defaults temporais aprovados, o plano semântico compartilhado ainda não cobre todas as capacidades/estados de restrição, nem resolve todas as ambiguidades e a política de relaxamento. Exemplo auditado: `orgao_nome=ESTADO DO RIO DE JANEIRO` e `unidade_nome=SECRET ESTADO SAÚDE`.
- **E2/E4/E6:** nenhum artefato do candidato foi reconstruído/alterado. Não houve coleta, re-embedding, DDL de fatos unitários, ativação ou rollback de produção. A certificação V7 e o cálculo numérico R19 permanecem bloqueados.
- **E5 parcial:** `python -m pytest tests -m 'not llm' -q`: **1.861 passed**; `python -m eval.run_eval check`: **31 casos**. Não foram chamados modelos reais; a regressão de 47 perguntas e o golden completo não foram executados.

Os resultados foram registrados sob `data/runs/implementacao-sql-pipeline-*/`; a execução não altera a seleção golden nem ativa modelos externos.

## 2. Ordem e dependências

```text
E0 Baseline e testes de contrato
  -> E1 Contrato do bundle, runtime e prontidão
  -> E2 Fechamento do pacote V7: documentos, vetores e proveniência
  -> E3 Plano semântico único e adaptadores
  -> E4 Fatos de item/resultado e R19
  -> E5 Validação integrada, relevância e reauditoria
  -> E6 Aprovação, ativação e ensaio operacional

P2 Desempenho: após correção funcional e medições comparáveis.
```

Testes acompanham cada entrega; E5 é a validação final, não o primeiro momento de testar. Após estabilizar o contrato E1, o trabalho de dados/encoding de E2 pode correr em paralelo com E3. E4 depende das decisões semânticas de E3; a certificação final do bundle depende também do schema produzido em E4.

## 3. Entregas executáveis

### E0 — Preservar a baseline e corrigir a dívida de testes conhecida

**Objetivo:** distinguir defeitos preexistentes de regressões introduzidas pela solução.

1. Registrar revisão e diff do código, configuração pública efetiva, versões, hashes dos artefatos e dos dois conjuntos de avaliação. Guardar evidências em diretório novo de execução.
2. Reproduzir as seis falhas conhecidas em ambiente isolado, sem modificar o banco ativo ou chamar provedores.
3. Em `tests/test_structured_query.py`, substituir quatro pressupostos de chunk único por verificações do resultado completo. Conferir ordem, conteúdo, recorte, fontes, ausência de perda/duplicação e limite de tamanho; não apenas concatenar texto e apagar asserções.
4. Na fixture de resultado grande, preparar explicitamente o banco antes da consulta. Manter um teste separado comprovando que consulta **não constrói** banco ausente/incompatível.
5. Em `tests/test_log_evidence.py`, usar banco preparado ou mockar conscientemente o contrato de `validar_banco`; manter cobertura real da rejeição de banco inválido.
6. Acrescentar testes sentinela sintéticos de R15/R19, colisão de CNPJ, município versus prefeitura e referência temporal. Não inserir IDs/perguntas do benchmark nas regras de produção.

**Aceite:** as seis falhas ficam resolvidas por adequação ao contrato ou correção demonstrada, sem afrouxar schema/hash, sem construção durante atendimento e sem ocultar falhas com skips. Guardar o resultado antes/depois.

### E1 — Resolver e validar um único bundle por processo

**Objetivo:** impedir SQL, catálogo e recuperação de usarem snapshots diferentes.

**Pontos de alteração:** `config.py`, `src/structured_db.py`, `src/query_analysis.py`, `src/retrieve.py`, `src/pipeline.py`, `app/api.py` e ferramentas administrativas de composição.

1. Definir descritor versionado do bundle: documentos, SQL, índice global e por base, catálogo, hashes, schema SQL/semântico, cobertura, capacidades, revisão de construção e configuração de embeddings.
2. Resolver esse descritor uma vez na inicialização e passá-lo aos componentes. Revisar caminhos/defaults capturados no import; mudar somente `config.INDEX_DIR` depois de importar módulos não garante troca do pacote.
3. Tornar o catálogo de órgãos/unidades/fornecedores/territórios específico do bundle. Remover a dependência implícita do catálogo global e as referências fixas à v6 no prompt/cache de análise.
4. Validar em preparação/startup: schema e hash SQL, origem documental dos índices, IDs, hashes de conteúdo, metadados obrigatórios/sensibilidade, dimensões, contagens e correspondência payload–posição FAISS. Contagem ou manifesto isolado não certifica vetor.
5. Reutilizar `validar_banco()`; nunca reconstruir na pergunta, reescrever apenas version strings ou editar hashes para obter aprovação.
6. Propor um resultado interno de execução explícito, reaproveitando `RecorteConsulta`: sucesso, zero correspondências, não aplicável, cálculo indisponível, ambiguidade e erro operacional. Conservar motivo e restrições; não reduzir todos a `[]`/`bool(chunks)`.
7. Preservar `/health` como liveness e adicionar readiness separado. Falha de integridade/capacidade obrigatória deve torná-lo não pronto; capacidade sem cobertura deve aparecer com motivo próprio, não como zero resultados. Definir quais capacidades são obrigatórias da release.
8. Executar verificações caras na preparação/startup, não recalcular hashes de todo o corpus por probe. Ativação por configuração/ponteiro único e reinício controlado, sem hot-swap entre arquivos de uma mesma consulta. Expor apenas diagnóstico público seguro.

**Aceite:** qualquer mistura de SQL/documentos/índice/catálogo é detectada; consultas são somente leitura; o pipeline diferencia indisponibilidade operacional de falta de dados. Bundle ainda em staging, sem ativação.

### E2 — Fechar as pendências próprias da V7

**Objetivo:** construir um pacote promovível, e não apenas um SQLite compatível.

1. Reconciliar novamente os 813 documentos sem vetor usando ID, conteúdo e posição de origem, sem atribuir IDs por ordem presumida. Não apagar os documentos para igualar contagens.
2. Fechar a proveniência do encoding por lote. Igual dimensão e nome do modelo não provam compatibilidade de encoders/configurações.
3. **Recomendação se a evidência continuar insuficiente:** autorizar e produzir um índice homogêneo com encoding reproduzível, alinhando também os embeddings de consulta. Completar somente 813 vetores não resolve a proveniência dos demais. Orçar antes de executar; sem autorização/evidência, a promoção fica bloqueada.
4. Exigir cobertura de todos os chunks destinados à recuperação densa. Qualquer exclusão de modalidade/base precisa de justificativa e aprovação explícitas; manter lexical não equivale a declarar cobertura densa completa.
5. Reutilizar `src.reparar_candidato_v7`, `src.montar_faiss_v7`, `src.reestruturar_indice_v7` e adaptadores de `src.index`, acrescentando validações onde faltarem. Rodar apenas em destino novo.
6. Preservar a semântica SQL e os campos adicionais recuperados do staging. Conferir os 106.667 registros e o PCA contra a origem; toda mudança posterior de contagem deve ter diff explicado. Não presumir que um rebuild genérico preserve as oito colunas adicionais e os vínculos históricos.
7. Auditar vínculos sem fabricar pais ausentes: os 26.209 vínculos históricos não são prova de pares completos no candidato. Preservar contratos/empenhos distintos e a deduplicação condicionada ao mesmo recorte.
8. Gerar relatório de cobertura por base, município, período, tipo de instrumento e relação. A expansão da V7 não a certifica automaticamente como censo.

**Aceite:** zero lacunas vetoriais não aprovadas, proveniência verificável ou re-embedding autorizado e validado, integridade documental/SQL/índice/catálogo aprovada e limitações de relações/cobertura declaradas. Ausência de evidência mantém o gate fechado.

### E3 — Introduzir um plano semântico compartilhado

**Objetivo:** interpretar a pergunta uma vez, antes de escolher executor.

**Pontos de alteração:** `src/schema.py`, `src/query_analysis.py`, `src/structured_query.py`, `src/retrieve.py`, `src/pipeline.py` e validação/formatação em `src/generate.py`.

1. Evoluir `QueryIntent` com dataset/grão, operação, medida, unidade, período/referência temporal, papéis de entidades, versão do snapshot e evidência textual das restrições.
2. Separar **tipo de medida** de **etapa do valor**: preço `unitário` e valor `homologado` precisam coexistir. O enum atual que escolhe um ou outro não representa R19 por inteiro.
3. Fazer regras e LLM convergirem ao mesmo plano/resolvedor. Durante transição, manter adaptadores de compatibilidade, mas os planejadores antigos devem delegar ao plano; não continuar reinterpretando livremente o texto em cada executor.
4. Resolver menção original e entidade canônica separadamente, incluindo abreviações legítimas. Validar evidência da menção, não exigir que o nome expandido esteja literalmente na pergunta. Rejeitar órgão inventado e combinações incoerentes, inclusive nos mocks LLM já demonstrados pela auditoria.
5. Representar explicitamente órgão, unidade responsável, fornecedor e localização da contratação. CNPJ exige papel resolvido ou ambiguidade explícita; fornecedor não pode virar `(orgao_cnpj = ? OR fornecedor_cnpj = ?)`.
6. Resolver o período uma vez. Assinatura, publicação, vigência, resultado e ano do PCA são dimensões distintas. **Política proposta:** inferir apenas com pista de domínio inequívoca; nos demais casos pedir esclarecimento ou aplicar default de produto aprovado e comunicado. Nenhum executor escolhe outro default.
7. Criar matriz de capacidades por dataset/executor para todo campo aceito, incluindo órgão/unidade, meses, categoria/classe e CNPJ. Campo sem suporte não desaparece silenciosamente.
8. Cada restrição deve terminar como `aplicada`, `nao_suportada`, `inaplicavel_justificada` ou `ambigua`. Quando uma restrição obrigatória não puder ser aplicada, não executar cálculo sobre coorte ampliada. Evidência auxiliar de recuperação não substitui a operação indisponível.
9. Unificar a política de relaxamento: termos/restrições obrigatórios não mudam de AND para OR para produzir uma estatística. Ampliação opcional exige registro e aviso, sem apresentar execução parcial como resposta exata.
10. Invalidar caches por snapshot, catálogo, versão de plano/prompt, modo de análise e provedor/modelo efetivos. Testar troca entre dois bundles.
11. Preservar guardrails, escopos/sensibilidade e aplicação das restrições em dense, BM25, lookup exato e rerank. Usar o mesmo mecanismo no avaliador comum.

**R15 como aceite vertical:** “plano de contratações de 2025” resolve PCA/ano do plano e a Secretaria como unidade responsável, sem exigir que ela seja o órgão do registro. Em modo regras, SQL suficiente não chama recuperação, embedding, rerank nem geração LLM. No PCA reproduzido, responde R$ 3.008.979.309,91 com fonte e aviso de plano de unidade/amostra, não total estadual. Se o PCA mudar no novo snapshot, reauditar o esperado.

**Aceite geral:** executores selecionam a mesma população quando ambos suportam o plano; divergências de capacidade são explícitas. Sondas LLM semanticamente inválidas não passam apenas por aderirem ao JSON Schema.

### E4 — Materializar itens/resultados e habilitar R19 com segurança

**Objetivo:** calcular a medida pedida sobre fatos apropriados, ou explicar por que ela não pode ser calculada.

1. Inventariar a cobertura **real da V7** de compras, itens e resultados; distinguir dado bruto ausente de campo não materializado. Não transportar para a V7 a contagem observada na v6.
2. Conferir o contrato PNCP de itens/resultados e os payloads locais antes de fechar DDL. Se faltar descrição, unidade, resultado ou vínculo, planejar enriquecimento administrativo versionado; coleta nova depende de aprovação e nunca ocorre dentro da pergunta.
3. Propor tabelas operacionais de item e resultado, ligadas à compra. Identidade/versão devem preservar compra × item × resultado/fornecedor × versão de origem, com chaves estáveis, hashes e fontes. Não replicar preços pela quantidade de contratos/instrumentos filhos.
4. Separar preços unitários homologados, totais homologados, valores estimados e contratados; conservar quantidade, moeda, unidade original/canônica, descrição/classificação do item, fornecedor, datas e situação/cancelamento.
5. Definir versão vigente e elegibilidade dos resultados antes de agregar: reprocessamento idempotente, retificações sem dupla contagem, cancelados fora da coorte conforme política aprovada, status desconhecido diagnosticado. Não agregar todas as versões do mesmo resultado.
6. Resolver localização pela compra/unidade contratante, preferencialmente com código IBGE. Nunca usar endereço do fornecedor como município da contratação. Identificar serviço de engenharia no item, com regra/classificação auditável, não somente por uma palavra no cabeçalho da compra.
7. Aprovar precisão/escala e representação monetária exata antes da migração: `Decimal` no processamento/cálculo e armazenamento compatível, sem novo uso inadvertido de `REAL` para preços detalhados. Não migrar todos os fatos antigos sem necessidade. Preços positivos abaixo de R$ 1,00 devem permanecer válidos; zeros/nulos/status inválidos têm tratamento explícito.
8. Aprovar comparabilidade e estatística: unidade/moeda e objeto comparáveis; conversões somente com regra rastreável; mediana por resultado elegível, sem ponderar por quantidade implicitamente. “Serviços de engenharia” pode ser heterogêneo mesmo com a mesma unidade. Pedir recorte/esclarecimento ou apresentar grupos comparáveis, não uma mediana arbitrária entre m², hora e serviço.
9. Incrementar a versão real do schema quando as tabelas forem implementadas; gerar novo banco/bundle e revalidá-lo. Integrar o compilador ao caminho operacional `consultar_estruturado()`. O compilador isolado de `contratos_fatos`/`pca_fatos` não conta como implementação em produção.
10. Calcular sobre a coorte completa elegível no snapshot, com denominadores, exclusões, desconhecidos e cobertura declarados. Nunca sobre top-k vetorial, valores globais ou duplicações de join. Arredondar apenas na apresentação conforme a política aprovada.
11. Propagar resultado e limites a `RecorteConsulta`, suficiência e resposta determinística. Sem cobertura suficiente: `calculo_indisponivel` com motivo; com unidade/recorte ambíguos: esclarecimento. Zero correspondências só quando os dados/campos necessários permitem essa conclusão no recorte.

**Aceite:** fixtures com resultados unitários comparáveis produzem mediana exata; versões, cancelamentos, relações 1:N e centavos são tratados corretamente. A V7 real só produz número quando sua cobertura e comparabilidade o permitem. Uma resposta correta de indisponibilidade não certifica R19 como capacidade numérica habilitada.

### E5 — Validar o pipeline completo e a qualidade da V7

1. Rodar os testes focados a cada entrega e a suíte offline completa ao final. Reexecutar a seleção ampliada original, registrando comando, contagens e falhas novas; não prometer previamente a mesma quantidade de testes.
2. Rodar integrações isoladas contra o bundle preparado, com chamadas de modelo bloqueadas nos caminhos SQL determinísticos. Exercitar regras, LLM simulado, fallback, multi-intenção, recusas, fontes e orçamento de chunks.
3. Reavaliar os **47 casos de recuperação** com opções comparáveis, análise/filtros reais e snapshot/configuração congelados. Preservar a ablação densa separada e reportar efeitos de código versus mudanças de índice. Não confundir rotas do gabarito com teste do roteador.
4. Exercitar R15/R19 também por `src.pipeline.responder`, incluindo roteamento, suficiência e resposta final. Se criar casos funcionais novos, versioná-los separadamente; não assumir que IDs R15/R19 existem no golden de respostas.
5. Rodar o golden funcional com o adaptador completo, histórico e guardrails, mediante orçamento/aprovação das chamadas externas. Distinguir erro de provedor de erro de conteúdo.
6. Reauditar fatos numéricos afetados pela expansão do corpus. Preservar golden original e resultados estritos; mudanças aprovadas geram versão nova com diff e justificativa.
7. Submeter R32/R33 à revisão humana da auditoria de relevância. Se alternativas forem aprovadas, publicar avaliação adicional versionada, mantendo o resultado original. Não declarar “zero regressões” no golden congelado.
8. Identificar cada execução por run/query/caso/snapshot, registrando plano resolvido, restrições aplicadas/não suportadas, capacidades, SQL, suficiência, fallback e revisão. Todos os casos devem ter envelope completo ou erro de infraestrutura explicado, com mascaramento e indicação de dados omitidos.
9. Medir tempos de análise, SQL, I/O, recuperação, rerank e geração, com cache frio/quente. Comparar configurações equivalentes; o p95 histórico de logs heterogêneos não é SLA da release.

**Aceite:** suíte offline e seleção ampliada verdes; nenhuma regressão crítica de semântica, cálculo, guardrail ou integridade; toda divergência de qualidade/cobertura tem disposição explícita aprovada. Publicar relatório e limitações, não apenas métricas agregadas.

### E6 — Promover somente após aprovação e rollback ensaiado

1. Congelar o bundle final, manifesto de capacidades e par código–schema; repetir o validador após a inclusão dos fatos de E4.
2. Ensaiar em ambiente isolado a ativação por referência única, reinício, readiness, R15, R19, fontes e recusas. Simular falha de schema/hash e comprovar que o pacote inválido não entra em serviço.
3. Preparar rollback do **conjunto** código/configuração/bundle/caches, nunca apenas do SQLite. Guardar a referência anterior e um destino de retorno validado.
4. Atenção à primeira promoção: o pacote ativo v6/schema 6 não é um rollback saudável para o runtime atual. Se não houver par anterior comprovadamente compatível, ensaiar retorno a estado de manutenção/degradação explícita, sem respostas analíticas incorretas. Não reconstruir v6 implicitamente nem prometer recuperação SQL por simples reversão de caminho.
5. Registrar gatilhos de retorno: integridade/readiness inválidos, falha de cálculo/suficiência, perda de guardrail ou regressão crítica. Guardar evidências do ensaio de ida e volta.
6. Solicitar aprovação explícita com relatório de E5, limitações e resultado do rollback. Só então ativar e executar smoke tests; observar traces da nova release e reverter se os gatilhos ocorrerem.

**Aceite:** promoção autorizada, pacote ativo integralmente identificado, sem mistura entre snapshots, testes de fumaça aprovados e rollback/degradação seguro demonstrado. A escolha “V7 como alvo” não substitui esta aprovação.

## 4. Matriz mínima de regressão

| Caso | Resultado obrigatório |
| --- | --- |
| Schema/hash/documento divergente | Não pronto/erro operacional explícito; nenhuma migração durante consulta |
| SQL inexistente | Não é tratado como zero correspondências |
| R15 via regras | PCA + ano do plano + unidade; SQL suficiente sem modelos |
| “na cidade/no município” versus “da prefeitura” | Localização versus entidade administrativa, sem troca de população |
| CNPJ colidindo em órgão/fornecedor | Filtro somente no papel solicitado; papel desconhecido é ambiguidade |
| Assinatura em 2024, publicação em 2025, vigência em 2026 | Cada referência seleciona apenas o período correto em ambos os executores |
| PCA/unitário/órgão incoerentes em JSON LLM | Rejeição semântica ou esclarecimento, não compilação para outra pergunta |
| Campos de plano sem suporte | Diagnóstico explícito; restrição obrigatória não é descartada |
| R19 comparável, quantidade desigual, preço abaixo de R$ 1 | Mediana unitária exata, sem ponderação implícita ou exclusão indevida |
| R19 sem unidade, moeda, cobertura ou objeto comparável | Esclarecimento/indisponibilidade; nunca total de contrato ou top-k |
| Resultado retificado/cancelado, compra com vários instrumentos | Elegibilidade/versionamento corretos; sem multiplicação dos fatos |
| Restrição obrigatória sem resultados | Sem ampliação silenciosa por OR ou remoção do filtro |
| Resultado em vários chunks | Integridade de linhas/fontes/recorte, ordenação e orçamento preservados |
| Troca de bundle com cache preenchido | Nenhuma análise, entidade ou índice do snapshot anterior reutilizado indevidamente |
| Multi-intenção e conteúdo restrito | Uma resposta SQL não elimina outra intenção nem contorna os guardrails |

## 5. Validação realizada e pendente

**Executado nesta rodada:** `python -m pytest tests -m 'not llm' -q` (**1.861 passaram**), `python -m eval.run_eval check` (**31 casos**), `python -m compileall -q src app tests` e `git diff --check`. Os testes focados de análise, SQL, recuperação, preparação de documentos, snapshot e API também passaram. A métrica `media_unitaria` (preço médio por item do PCA) foi validada com teste dedicado. Nenhum provedor de modelo foi chamado.

**Pendente:** integrações contra um bundle candidato isolado, regressão de 47 perguntas, golden funcional completo e benchmark com modelos reais. Só executar os comandos de integração após preparar o bundle e autorizar inferência/orçamento; escrever saídas em diretórios novos.

```powershell
# Somente depois de configurar o bundle isolado e aprovar inferência.
py -m eval.run_eval run --pipeline eval.adapters:pipeline_completo
```

Para os 47 casos, reutilizar `eval.medir_regressao_v7` com `--hybrid --query-filters`, pool 10, caminhos explícitos de referência/candidato e `--saida` novo. A v6 pode ser controle de recuperação, não alvo operacional. Se o encoder mudar, registrar o fator e não comparar caches incompatíveis. O avaliador deve consumir o novo resolvedor compartilhado, não conservar uma interpretação legada paralela.

## 6. Gates que exigem decisão antes de executar

| Gate | Decisão/insumo necessário | O que fica bloqueado |
| --- | --- | --- |
| Encoding V7 | Evidência suficiente por lote ou autorização de re-embedding com custo/modelo/configuração | Certificação do índice e promoção |
| Semântica | Defaults temporais e papel da Secretaria no PCA decididos; falta fechar ambiguidades residuais e política de relaxamento | Capacidades/estados restantes do plano semântico em E3 |
| Fatos R19 | Cobertura pretendida, autorização de eventual coleta, comparabilidade, elegibilidade, escala monetária e unidade estatística | DDL definitivo e habilitação numérica de R19 |
| Relevância/golden | Revisão humana de R32/R33 e fatos alterados pela V7 | Aprovação da qualidade; nenhuma edição silenciosa do golden |
| Avaliação externa | Endpoint/modelos, orçamento e janela de execução | Evidência funcional com modelos reais |
| Operação | Capacidades obrigatórias, par de rollback ou manutenção segura, responsável e janela | Ativação |

Não há estimativa responsável para re-embedding/enriquecimento antes de fechar inventário, configuração e orçamento. Cada entrega pode ser uma PR revisável; E4 deve separar materialização, cálculo e integração. Responsáveis e prazos devem ser atribuídos pela equipe.

## 7. Fora do caminho crítico: desempenho

Após a correção funcional, usar `EXPLAIN QUERY PLAN` e carga representativa para decidir índices de assinatura, papéis de CNPJ e chaves de vínculo/item. Medir seletividade antes de adicionar índices. Avaliar reúso de FAISS/documentos e BM25 por snapshot imutável, com invalidação e limites de memória. Não relaxar filtros, reduzir evidência ou trocar modelo para esconder custo de fallback SQL.

## 8. Evidências e conclusão esperada

Cada entrega deve anexar diff, manifesto/hash do bundle, comandos/resultados e restrições ainda abertas. A entrega final precisa provar:

1. **V7 coerente e verificável**, não apenas schema 7.
2. **R15 correto por SQL** e demais caminhos alinhados à mesma semântica.
3. **R19 correto sobre fatos unitários comparáveis**, ou indisponibilidade/esclarecimento honestos quando o snapshot não suporta o número.
4. **Nenhum filtro aceito descartado silenciosamente** e nenhuma mudança implícita de coorte entre executores.
5. **Testes, avaliação integral, revisão de relevância e rollback** concluídos antes da ativação autorizada.

As fontes primárias estão na [auditoria](AUDITORIA_SQL_PIPELINE.md#10-fontes-primárias-e-limites-da-pesquisa). A tentativa de reabrir as duas páginas PNCP neste planejamento falhou; não se afirma nova verificação do contrato externo. E4 inclui essa conferência antes de definir as tabelas.
