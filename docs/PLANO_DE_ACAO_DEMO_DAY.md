# Plano de ação — qualidade, classificação de contratos e Demo Day

## 1. Escopo e decisões

Este documento é o plano único de execução. Não representa funcionalidades já implementadas.

**Baseline inspecionada:** backend `joao-reis`, commit `435c9c0`; frontend local em `../frontendRAG`. Antes de executar, confirmar os commits finais dos dois projetos. Não trocar branches, fazer merge, atualizar serviços ou publicar sem autorização.

### Decisões confirmadas pelo usuário

- Reutilizar código existente; não criar um segundo pipeline, cliente HTTP/LLM, roteador ou framework de testes.
- Trabalhar em incrementos pequenos, com testes por módulo. **Não executar a suíte inteira em uma chamada.**
- Usar **MiMo-V2.6-Pro pago via OpenRouter**, modelo nomeado **`xiaomi/mimo-v2.6-pro`**, exclusivamente na classificação de contratos por assunto. Selecionar provedor/modelo por chamada, reutilizando o cliente existente sem alterar a geração/reformulação do RAG.
- Pilotar **taxonomia v1 com 10 categorias substantivas candidatas**, mais `outros` e `indeterminado`, antes de aprová-la. Um assunto principal, secundários opcionais; exemplos/fronteiras devem ser confrontados com os registros, não adotados apenas pela tabela.
- Renomear o assunto de TI para **`tecnologia_informacao`**, compatível com software, hardware, redes, comunicação, suporte e manutenção.
- Separar confiabilidade da resposta no recorte consultado, completude dos campos e cobertura do universo estadual. Zero correspondências no snapshot pode ser uma conclusão confiável; não reduzir confiança automaticamente.
- Reservar contratos revisados independentemente para avaliação final, sem usar esse holdout para exemplos, tuning de prompt, taxonomia, ranking ou thresholds.
- **Nunca enviar o contrato inteiro para classificação.** Usar primeiro somente o objeto/descrição do entregável, com seleção determinística, teto de tokens e saída curta; enriquecer apenas casos em que contexto adicional disponível possa resolver insuficiência.
- Manter estados de processamento (`pendente`, `concluido`, `falhou`, `restrito`) separados dos assuntos e da incerteza semântica.
- Começar com rótulos inferidos para descoberta e ranking, sem exclusão de candidatos. Filtros estritos exigem medição de perda de contratos relevantes e aprovação posterior.
- Serializar o contrato e integrar essa classificação à preparação de cada contrato recebido quando o marco independente estiver aprovado. O processamento inicial da base existente usa a mesma função.
- Não estimar nem preencher modalidade licitatória com o LLM. Assunto inferido não resolve a ausência de modalidade oficial.
- Classificação é **marco independente, não requisito de liberação da demo**: o desafio exige roteamento por natureza de evidência, não este enriquecimento de assunto.
- Iniciar ensaio fino frontend/backend com os dados existentes imediatamente após a baseline mínima da P0; não esperar P1–P7.
- **Política de acesso ao histórico: decisão pendente.** Não alterar a rota nesta etapa; bloquear exposição pública até resolver a decisão. Isso não bloqueia ensaio local isolado.
- **Feature freeze: sem data, por decisão do usuário.** Não atribuir prazo automaticamente; manter o checkpoint e suas regras abaixo.

### Limites e decisões ainda necessárias

- Revisar e aprovar exemplos/fronteiras da taxonomia v1 na P6 antes de implementar o classificador ou iniciar lote.
- Confirmar limites de tokens/chamadas/custo do modelo pago e orçamento aprovado antes de inferência real; escolher o modelo no plano não executa chamadas nem autoriza lote ilimitado.
- Confirmar formato da demo (local, túnel ou hospedagem), considerando o bloqueio de segurança do histórico.
- Aprovar mudanças de esquema e ativação dos artefatos derivados antes de reconstruir/publicar no Neon.
- Definir responsáveis por módulo com a equipe. Não inventar autoria nem escrever relatos individuais em nome dos integrantes.

## 2. Regras para manter a organização

1. Uma tarefa por incremento: problema → reprodução pequena → mudança mínima → teste do módulo → atualização do registro.
2. Estender contratos existentes antes de criar abstrações. Extrair helper compartilhado apenas quando dois consumidores realmente precisam da mesma semântica.
3. Manter o código de domínio em `src/`; a CLI é uma entrada fina para esse código, não uma cópia.
4. Não importar o backend a partir de `app/` para funções de preparação/classificação. Serviços frontend continuam passando pelo cliente HTTP existente.
5. Não alterar `data/raw/v6/`, inventário histórico ou hashes de origem para acomodar enriquecimento. Classificações são artefatos derivados, com versão própria.
6. Preservar IDs, conteúdo original, modelo de embedding e vetores de textos inalterados. Metadados novos não justificam reembeddar tudo.
7. Não duplicar regras de CPF, e-mail, CNPJ, filtros, retry, contagem de tokens ou autorização.
8. Falha de um módulo para a fase. Não continuar a promoção de dados ou ampliar testes para tentar esconder o erro.
9. Requisições externas e testes com modelo real são explícitos, limitados e separados dos testes offline.

## 3. Mapa de reutilização

| Necessidade | Reutilizar/estender | Evitar |
|---|---|---|
| MiMo-V2.6-Pro pago via OpenRouter | `llm_ecoa.completar_chat`, `config.py`, `src/model_observation.py`, `src/obs.py`; seleção de `xiaomi/mimo-v2.6-pro` por chamada e configuração exclusiva da classificação | Outro cliente OpenAI, alteração do modelo das demais etapas ou troca silenciosa para modelo diferente |
| Serialização de registro | Extração PNCP existente e projeção específica de objeto/descrição; compartilhar helpers com `src/generate.py:_representar_registro`/`src/analyze_structured.py:_contract_view` somente quando a semântica coincidir | Enviar a visão completa usada na geração, JSON/PDF integral ou usar conteúdo bruto como fallback |
| Classificação de assunto | Novo módulo coeso `src/classify_contracts.py`, modelos em `src/schema.py` e CLI no próprio módulo | Misturar classificação documental com `src/router.py`, que roteia perguntas |
| Preparação/cache | `src/prepare_documents.py`, fingerprints, retomada e escrita atômica existentes | Classificar novamente a cada consulta ou alterar o cache de embeddings |
| Segurança do contexto | `src/sensitivity.py` e classificação de sensibilidade existente | Enviar JSON bruto com dados pessoais ao provedor |
| Órgãos/municípios/fornecedores | Catálogo e helpers de `src/query_analysis.py`; resolução de valores em `src/structured_query.py` | Listas paralelas de órgãos e SQL novo fora do compilador existente |
| Filtros | `QueryFilters`, `QueryIntent`, `filtros_para_retrieve`, `_where`, `_resolver_valores` | Reimplementar busca ou montar SQL a partir de texto do LLM |
| Banco e proveniência | `Banco`, `construir_banco`, `garantir_banco`, `SCHEMA_VERSION` em `src/structured_db.py` | Scripts ad hoc de atualização e alteração silenciosa do Neon |
| Confiança/citações | `src/generate.py`, resultado estruturado SQL e validação atuais | Inferir ausência pelos textos da resposta ou inventar uma fonte |
| Avaliação | `eval/run_eval.py`, `eval/adapters.py`, golden/retrieval sets existentes | Segundo avaliador ou chamar ausência de critério de acerto factual |
| Frontend | `src/services/api.ts`, services de query/history/feedback, componentes e testes já presentes | Outro cliente fetch ou reconstruir telas existentes |

**Extrações permitidas, não refatoração ampla:** se geração e classificação precisarem da mesma visão de registro, mover apenas essa lógica para um pequeno `src/record_view.py`, preservar o comportamento do consumidor antigo e testar a equivalência. Não mover a formatação específica do rerank: ela tem orçamento e finalidade diferentes.

## 4. Ordem de execução e dependências

```text
P0 baseline mínima: commits, endpoints e dataset existentes
 ├─ P8A ensaio fino imediato, local e isolado
 │    ├─ repetir smoke após mudanças de API/sessão/timeout/histórico
 │    └─ P9 roteiro, documentação e plano B desde o primeiro ensaio
 ├─ P1 reformulação/logs
 ├─ P2 filtros canônicos
 ├─ P3 confiança/citações
 └─ P4 avaliação fiel
       └─ P5 baseline real em grupos pequenos

P0 + correções que alterem dados/esquema
 └─ P7A consistência dos artefatos/bancos existentes

P1–P5 + P7A aplicável + resultados de P8A
 └─ P8B validação final integrada
      └─ P9 ensaio final e defesa
          └─ demo; exposição pública exige política do histórico aprovada

Marco independente, fora do caminho crítico da demo:
P6A revisão manual de desenvolvimento → taxonomia v1 aprovada; holdout reservado
 └─ P6B serializer → cliente MiMo → classificador → preparação → piloto de desenvolvimento
      └─ congelar configuração → avaliação final no holdout independente
           └─ P7B artefatos enriquecidos opcionais
                └─ descoberta/ranking; filtros estritos somente após medir perda de recall
```

A demo deve funcionar com o dataset existente e a classificação desligada. P6/P7B não bloqueiam P8A, P8B, documentação ou apresentação. Rerank e outros checks da P0 seguem em paralelo se a baseline mínima já permitir o primeiro ensaio; registrar o backend/fallback efetivamente usado. O enriquecimento não substitui a medição do comportamento anterior.

### 4.1 Checkpoint de feature freeze — data não definida

Não há data de congelamento definida, conforme solicitado pelo usuário. A equipe deve confirmar explicitamente qualquer prazo antes de tratá-lo como compromisso.

Quando o checkpoint for aprovado:

- Identificar os commits/configuração/dataset da demo e manter o caminho existente estável.
- Parar expansão de funcionalidades, mudanças de taxonomia e rollout de classificação. Marco opcional incompleto fica desativado/adiado.
- Concentrar trabalho em correção factual/segurança, validação por módulo, documentação e ensaio; repetir o smoke integrado para cada correção relevante.
- Alterações de corpus/esquema após o freeze exigem justificativa de correção, autorização, rollback e reavaliação dos casos afetados; não usá-las para inserir funcionalidades novas.
- Registrar exceções aprovadas e o commit de referência do ensaio final.

## P0 — Fixar baseline e preparar o ambiente

- [ ] Confirmar backend e frontend usados na demo, branch, commit e árvore de trabalho.
- [ ] Verificar o ambiente Python recomendado pelo projeto (3.12), dependências e `sentence_transformers` nesse ambiente; não confundir com o Python 3.14 usado na auditoria.
- [ ] Reutilizar `src/rerank.py` e sua configuração atual. Pré-carregar o modelo em operação separada, autorizada; demonstrar que o backend real foi o cross-encoder, não fallback RRF.
- [ ] Capturar contagens, hashes e consultas de referência sem gravar no banco.

Baseline local observada: 810 registros estruturados, 455 contratos assinados, 3 com modalidade, 1 PCA válido. O hash do SQLite corresponde ao `documents.jsonl` ativo; **não reconstruir o SQLite apenas por causa do OCR**. Neon não foi verificado. Não fixar 809 registros ou 2 contratos da Cristália como expectativas permanentes.

**Testes, em chamadas separadas:** `tests/test_rerank.py`; depois `tests/test_ablacao_rerank.py`. Usar scorer falso nos testes offline; o smoke test real não entra no pytest padrão.

**Pronto:** ambiente da demo identificado e rerank real verificável, sem baixar/recarregar modelo durante cada teste.

## P8A — Ensaio fino imediatamente após P0

Este ensaio começa assim que commits, endpoints e dataset existentes estiverem identificados. Não espera correções P1–P7, classificação, novo corpus ou hospedagem. É um smoke do caminho real já disponível, não outra implementação de integração.

- [ ] Executar frontend e backend localmente, com dados existentes, sem túnel/publicação enquanto o histórico estiver pendente. Usar conversas de teste, não dados pessoais reais.
- [ ] Reusar `check:backend` e os services existentes; conferir URL da API, origem/CORS, health e forma de execução na máquina de apresentação.
- [ ] Fazer uma consulta pequena e uma continuação; verificar `session_id`, envio de histórico, resposta e nova conversa sem herdar a sessão anterior.
- [ ] Verificar carregamento, erro/timeout e recuperação da UI. Simular demora com o mecanismo de teste existente; não gastar minutos de endpoint compartilhado só para reproduzir timeout.
- [ ] Conferir acesso ao histórico e traces disponíveis, registrando exposição, incompatibilidades e funcionalidades ausentes sem presumir que já foram resolvidas.
- [ ] Registrar por falha o módulo responsável, reprodução, prioridade e tarefa P1–P5/P7A/P8B correspondente. Falha de API/sessão/deployment que inviabilize demo ganha prioridade antes de funcionalidades opcionais.
- [ ] Criar primeiro esboço de roteiro e plano B; repetir este mesmo smoke após alterações de contrato, sessão, timeout, histórico ou ambiente.

**Testes focados somente quando houver mudança/falha:** primeiro o caso específico; depois `tests/test_app_api.py` ou `tests/test_trace.py`, cada um em sua chamada. No frontend, executar apenas o alvo afetado de `src/services/api.test.ts`, `src/services/services.test.ts` ou `src/pages/Consulta/ConsultaPage.integration.test.tsx`, também separadamente.

**Pronto:** o primeiro caminho integrado foi observado e suas falhas estão registradas, não necessariamente todas corrigidas. Isso não equivale à validação final P8B nem autoriza exposição pública.

## P1 — Corrigir o contrato do teste de reformulação/logging

- [ ] Reproduzir apenas `test_reformulation_logs_effective_model` em `tests/test_logging_alignment.py`.
- [ ] A pergunta `E em 2024?` agora usa `troca_de_ano`, sem LLM: testar esse caminho esperando modelo ausente e tokens zero.
- [ ] Usar uma continuação que de fato exija LLM para testar modelo efetivo e uso de tokens com cliente falso.
- [ ] Preservar o atalho determinístico; não chamar LLM só para fazer o teste antigo passar.

**Testes, separadamente:** teste específico → `tests/test_reformulacao.py` → `tests/test_logging_alignment.py`.

**Pronto:** os dois caminhos observáveis têm testes coerentes, sem rede.

## P2 — Unificar filtros de órgão, município, fornecedor e CNPJ

- [ ] Reutilizar sinônimos de órgão e catálogo para resolver `Prefeitura de Maricá` para `MUNICIPIO DE MARICA` antes de adaptar os filtros.
- [ ] Preservar distinção entre prefeitura e outros órgãos do município: 8 registros da prefeitura não são os 23 registros de todos os órgãos de Maricá.
- [ ] Estender a geração do catálogo existente com municípios e fornecedores do snapshot, mantendo compatibilidade com nomes/siglas antigos e invalidando cache por versão/hash.
- [ ] Fazer `Volta Redonda` e `empresa Cristália` virarem filtros canônicos. Casos ambíguos não escolhem arbitrariamente o primeiro nome.
- [ ] Registrar os trechos reconhecidos para não serem reaproveitados como termos do objeto contratado.
- [ ] Normalizar CNPJ pontuado/dígitos e separar papel de órgão/fornecedor. Inicialmente aproveitar `orgao_cnpj`; adicionar fornecedor somente propagando um campo real do PNCP, se aprovado.
- [ ] Atualizar o contrato `QueryIntent`, schema de análise por LLM, adaptação para retrieval e whitelist SQL juntos; não aceitar filtro em um caminho e descartá-lo no outro.
- [ ] Verificar suporte dos metadados na preparação e índices; aplicar filtros consistentes em SQL e retrieval sem reproduzir o compilador SQL.
- [ ] Identificadores exatos não podem ser relaxados silenciosamente para responder sobre outro contrato. Usar a observabilidade/fallback existentes para explicar relaxamentos permitidos.

**Testes por módulo, nesta ordem e em chamadas separadas:**
`tests/test_query_analysis.py` → `tests/test_structured_query.py` → `tests/test_retrieve.py` → `tests/test_pipeline_query_analysis.py`.
Se houver campo novo: testar primeiro `tests/test_schema.py` (se aplicável), `tests/test_prepare_documents.py` e `tests/test_structured_db.py`, individualmente.

**Pronto:** perguntas equivalentes dão os mesmos filtros/registros; fixtures pequenas cobrem acentos, aliases, ambiguidade, CNPJ e SQL parametrizado. Conferir contagens reais separadamente, em leitura apenas.

## P3 — Confiabilidade no recorte, completude e cobertura separadas

Não reduzir confiança automaticamente porque o resultado é zero ou o snapshot é pequeno. A confiança se refere à **afirmação efetivamente sustentada**, não a uma promessa de cobertura estadual.

| Dimensão | Pergunta que responde | Exemplo/limite |
|---|---|---|
| Confiabilidade do resultado no recorte | A consulta terminou corretamente, com filtros/semântica válidos e evidência suficiente para esta afirmação? | SQL exaustivo no snapshot identificado pode sustentar alta confiança em “não há contratos correspondentes neste snapshot”. Ausência no top-k vetorial não prova ausência na base. |
| Completude dos campos | Quais registros possuem os dados necessários ao filtro/cálculo? | Zero registros com modalidade informada não descarta contratos cuja modalidade está ausente. Valores ausentes não são zero. |
| Cobertura do universo | O corpus cobre integralmente o Estado/período/órgãos necessários? | A amostra curada não é censo; resultado exato no snapshot não autoriza concluir “não houve contratos no RJ”. |

- [ ] Expor metadados estruturados de snapshot/hash, filtros efetivos/relaxados, execução completa/parcial, contagem de correspondências, denominadores e cálculo disponível; não deduzir essas condições lendo o texto gerado.
- [ ] Informar completude por campo relevante e quantidade de registros com campo desconhecido. Quando um filtro exige campo ausente, delimitar a conclusão aos registros em que o campo é conhecido; não esconder desconhecidos entre “não correspondentes”.
- [ ] Distinguir zero registros, soma de valores conhecidos igual a zero e cálculo indisponível por campos ausentes.
- [ ] Preservar confiança compatível com consulta válida e exaustiva no recorte, inclusive zero correspondências. Consulta incompleta, filtros ambíguos, relaxamentos não sustentados ou cálculo não verificável limitam confiabilidade por razões próprias, não por tamanho/representatividade da amostra.
- [ ] Declarar limitações de completude e cobertura separadamente da confiança. Uma afirmação limitada ao snapshot pode ser confiável mesmo com cobertura estadual insuficiente.
- [ ] Não extrapolar a amostra para ausência estadual, prevalência de modalidades ou comparação de preços sem equivalência técnica. Quando a pergunta exige essa conclusão mais ampla, explicar por que ela não está sustentada, ainda que o resultado local seja exato.
- [ ] Reusar a mesma política na resposta determinística e na validação LLM, sem introduzir redução genérica por zero.
- [ ] Verificar F1 e preservar somente fontes presentes/pertinentes; insuficiência de pares equivalentes é limitação da comparação, não erro automático do SQL. Não anexar qualquer fonte apenas para melhorar citação.
- [ ] Garantir que `is_refusal=True` continua implicando fontes vazias.

**Testes, separadamente:** `tests/test_structured_query.py` → `tests/test_generate.py` → casos afetados de `tests/test_pipeline.py`. Cobrir zero confiável no snapshot, busca top-k não exaustiva, campos ausentes, execução parcial, zero monetário e proibição de extrapolação estadual como casos distintos.

**Pronto:** confiabilidade, completude e cobertura são distinguíveis na resposta/trace; zero local pode ser confiável sem inventar cobertura estadual; números/citações preservam proveniência.

## P4 — Completar avaliação sem maquiar o gabarito

- [ ] Estender o avaliador existente com critérios numéricos tipados, tolerância por caso e normalização monetária. Reusar parsers adequados quando a semântica coincidir; não comparar `1196949440` literalmente com `R$ 1.196.949.440,00`.
- [ ] Separar termos obrigatórios de alternativas equivalentes; não exigir todas as frases sinônimas simultaneamente.
- [ ] Auditar os 16 casos com critérios e preencher os casos restantes de maneira fundamentada. Não tratar recusa válida como resposta numérica.
- [ ] Rever P5.3: alinhar sentido de tecnologia/TI à persona e à evidência, sem trocar o esperado para se ajustar à saída atual.
- [ ] Associar resultados numéricos ao SQL reproduzível, hash/versão da base e recorte temporal.
- [ ] Acrescentar ao harness um contexto opcional por caso/conversa e fazê-lo chegar a `responder(historico=...)`; preservar o adaptador de pergunta única.
- [ ] Adicionar casos específicos de e-mail público, conteúdo restrito, mascaramento e credenciais; reusar dados sintéticos e política de sensibilidade existentes.
- [ ] Incluir casos novos de município/fornecedor/CNPJ sem duplicar perguntas que já cobrem a mesma condição.
- [ ] Reportar separadamente rota, recusa, conteúdo, fonte, confiança, completude de campos, cobertura do recorte, não avaliados, latência e tokens. O total deixa de ser necessariamente 26 após incluir casos.
- [ ] Distinguir métricas de regressão/tuning do golden set de avaliação final intocada. Para o classificador, manter IDs/grupos/versões dos conjuntos de desenvolvimento e holdout da P6; testes e relatórios de desenvolvimento nunca consomem os rótulos reservados.

**Testes, separadamente:** `tests/test_eval.py` → `tests/test_eval_adapters.py` → `tests/test_synthetic_conversations.py` → `tests/test_sensitivity.py`.

**Pronto:** cada critério tem evidência; conversa é avaliada com histórico; ausência de gabarito não aumenta a taxa de correção factual.

## P5 — Medir baseline real em lotes pequenos

- [ ] Depois dos contratos offline estáveis, executar primeiro F1/P2.1 e as continuações, depois grupos pequenos por persona/guardrail.
- [ ] Usar `eval/run_eval.py --ids ...` e os adaptadores existentes; manter run IDs e contexto da conversa.
- [ ] Respeitar cache, pausas e o endpoint compartilhado. Não usar uma rodada de todas as perguntas como depuração inicial.
- [ ] Quando os grupos passarem, cobrir todo o golden set por grupos e consolidar relatórios com os mesmos commits/configuração/base; não selecionar só resultados favoráveis.
- [ ] Versionar resumo e revisão factual em `docs/registro_execucao_v6.md`, com detalhes locais em `eval/runs/` segundo a política atual.

**Pronto:** benchmark completo coberto, datado e rastreável; casos pendentes/falhos claramente registrados.

## P6 — Marco independente: classificação de assuntos com MiMo-V2.6-Pro pago

Não é requisito explícito do desafio nem gate da demo. Pode ser adiado integralmente, mantendo o caminho existente. **Não investir no serializer, classificador ou lote antes de aprovar P6A.**

### P6A — Fixar taxonomia v1 e exemplos antes de implementar

O eixo é o **assunto do objeto contratado**, não a forma de aquisição/prestação. Começar com **10 categorias substantivas candidatas** (dentro da faixa de 8–12), mais dois resultados semânticos especiais. Não existe uma categoria genérica `servicos`: serviços são classificados pelo domínio/finalidade. A tabela é proposta para revisão, **não taxonomia aprovada automaticamente**.

| ID candidato / assunto | Inclusão e exemplos | Exclusão e fronteira |
|---|---|---|
| `tecnologia_informacao` — Tecnologia da informação e comunicação | Software/assinatura, computadores, redes, conectividade, infraestrutura computacional, suporte e manutenção de TI. | Transferência de tecnologia farmacêutica não é TI. Treinamento acessório à implantação de software permanece TI; capacitação autônoma geral é serviço profissional. |
| `medicamentos` — Medicamentos e produtos farmacêuticos | Fármacos, fornecimento/produção farmacêutica e transferência de tecnologia explicitamente vinculada a medicamentos. | Equipamentos, materiais hospitalares e exames sem aquisição/produção de fármaco. |
| `equipamentos_materiais_saude` — Equipamentos e materiais de saúde | Equipamento médico/diagnóstico, insumos hospitalares e manutenção especializada desses equipamentos. | Medicamentos, ambulâncias e realização de exames/atendimento clínico. |
| `servicos_saude` — Serviços de saúde | Exames, atendimento clínico, diagnóstico e serviços laboratoriais prestados. | Compra de reagentes/equipamentos é material de saúde; fornecimento de fármacos é medicamento. |
| `transporte_veiculos` — Transporte e veículos | Veículos/ambulâncias, peças, manutenção, locação e serviços de transporte. | Software de frota é TI; construção de via/drenagem é infraestrutura. Atendimento clínico integrado exige examinar qual entregável predomina. |
| `alimentacao` — Alimentação | Alimentos, refeições, merenda e fornecimento/preparo de refeições. | Equipamentos gerais de cozinha sem fornecimento de alimentação. |
| `construcao_infraestrutura` — Construção e infraestrutura | Construção, reformas, vias, drenagem e reparos físicos como entregável principal. | Limpeza, vigilância e operação cotidiana são conservação/operação; infraestrutura de rede computacional é TI. |
| `operacao_conservacao_instalacoes` — Operação e conservação de instalações | Limpeza, segurança/vigilância patrimonial, conservação e operação predial contínua. | Obra/reparo físico autônomo é construção; suporte de sistemas ou equipamento médico segue o assunto especializado. |
| `materiais_mobiliario_equipamentos_gerais` — Materiais, mobiliário e equipamentos gerais | Expediente, papel, móveis e equipamentos gerais sem domínio especializado nas demais categorias. | Computadores, insumos médicos, veículos e alimentos usam seus assuntos próprios, mesmo comprados para um escritório. |
| `servicos_tecnicos_profissionais` — Serviços técnicos e profissionais | Consultoria, auditoria, capacitação autônoma, estudos e serviços profissionais especializados não absorvidos por outro domínio. | Treinamento/suporte acessório a uma entrega de TI continua TI; serviço clínico é saúde; execução física de obra é infraestrutura. |
| `outros` | Assunto claramente compreendido, mas fora das dez categorias, como uma atividade cultural não coberta. Registrar descrição/evidência, sem forçar um domínio genérico. | Não é categoria de erro, de objeto não lido nem escape automático para qualquer serviço. |
| `indeterminado` | Processamento concluído com evidência insuficiente para identificar o principal: objeto ausente/genérico ou predominância não demonstrada. | Não representa pendência, falha de API, JSON inválido nem bloqueio por sensibilidade. |

Regras do resultado semântico:

- **Um principal obrigatório em resultado concluído**, definido pelo entregável/finalidade predominante explícito. Não inferir predominância por valor monetário ausente ou palavra isolada.
- **Secundários opcionais** para entregáveis próprios de outros domínios; não repetir principal nem usar `outros`/`indeterminado` como secundários. Apoio/treinamento acessório não gera outro assunto automaticamente.
- Software com suporte/capacitação de implantação: principal `tecnologia_informacao`. Medicamentos e computadores com predominância farmacêutica explícita: principal `medicamentos`, secundário `tecnologia_informacao`.
- Contrato misto sem predominância demonstrada: principal `indeterminado`, motivo e secundários somente para assuntos sustentados; não forçar uma escolha.
- `outros` é compreensão positiva de assunto fora da taxonomia; `indeterminado` é limite de evidência. Processamento permanece em eixo separado.
- Domínios especializados prevalecem sobre categorias gerais quando o objeto os demonstra. Obras versus operação, produto versus serviço de saúde e treinamento acessório versus autônomo precisam de exemplos positivos/negativos na revisão.
- Taxonomia v1 organiza apenas os contratos disponíveis, não a distribuição de compras públicas do RJ. Manter pais estáveis; subcategorias futuras (software/hardware/conectividade/suporte, por exemplo) exigem necessidade demonstrada, sem substituir o pai.

### Revisão manual de desenvolvimento e holdout independente

- [ ] Fixar snapshot/hash, IDs e seed de seleção; confirmar a contagem (baseline atual: 455 contratos). Agrupar duplicatas, versões/retificações, representações do mesmo contrato/compra **e inputs de classificação idênticos** para evitar vazamento entre desenvolvimento e holdout.
- [ ] Inspecionar **40–60 registros de desenvolvimento** antes do lote: combinar seleção aleatória reproduzível com variação de órgãos, valores e descrições/entregáveis. Esta amostra orienta fronteiras, não estimativas de prevalência estadual.
- [ ] Criar referência manual de principal/secundários, evidência e incerteza; revisar concordância entre pessoas, casos recorrentes em `outros` e categorias amplas demais para descoberta. Não criar/remover categorias só para equilibrar contagens; assunto raro pode ser relevante e ausente pode surgir no corpus maior.
- [ ] Reservar **holdout pequeno adicional — proposta: 20 contratos**, disjunto por grupo/ID da amostra de desenvolvimento. Revisores independentes mantêm seus rótulos fora do processo de tuning; não copiar exemplos para prompts, fixtures de desenvolvimento ou regras.
- [ ] Antes de examinar resultados do classificador no holdout, fixar taxonomia v1, protocolo de anotação, métricas e limites de aceitação. Revisores adjudicam divergências sem consultar previsões do modelo; rótulos permanecem reservados até avaliação final. Para avaliar descoberta/ranking, reservar também perguntas e julgamentos de relevância independentes; assunto anotado sozinho não define relevância de busca.
- [ ] Ajustar prompt, seleção de campos, exemplos, ranking e thresholds **somente no desenvolvimento**. Manifesto de split registra versão/seed/grupos/hash; o harness deve rejeitar mistura dos splits e uso acidental do holdout no comando de tuning.
- [ ] Aprovar taxonomia/fronteiras usando somente desenvolvimento antes de implementar P6B. Não fundir assuntos para esconder erro do modelo nem ampliar além da faixa inicial sem evidência/revisão.

**Pronto P6A:** dez candidatos confrontados com 40–60 registros, taxonomia v1 aprovada/versionada, referência de desenvolvimento e holdout independente disjuntos; estados técnicos não aparecem como assuntos.

### P6B — Contrato de processamento, segurança e implementação

Manter dois eixos distintos no schema/cache/log:

| `status_processamento` | Resultado de assunto | Tratamento |
|---|---|---|
| `pendente` | Principal `null`, secundários vazios | Ainda não processado/desativado; não contabilizar como incerteza semântica. |
| `concluido` | Um principal da taxonomia; secundários opcionais | Pode ser `outros` ou `indeterminado` genuínos, com evidência/motivo aplicável. |
| `falhou` | Principal `null`, secundários vazios | API indisponível, chave ausente, timeout, saída inválida ou erro técnico; guardar erro e política de retry. |
| `restrito` | Principal `null`, secundários vazios | Bloqueado pela política de sensibilidade; não enviar ao provedor nem presumir assunto. |

- [ ] Validar invariantes com Pydantic: somente `concluido` recebe assunto; categoria substantiva/`outros` exige evidência válida. `indeterminado` exige motivo de insuficiência verificável no input, sem inventar trecho quando o objeto estiver vazio. Saída fora da taxonomia ou incompatível com essas regras é falha de validação, não `indeterminado` automático.
- [ ] Aplicar a estratégia de input mínimo abaixo: objeto/descrição primeiro, contexto complementar só quando necessário, jamais registro/PDF integral. ID/hash e proveniência ficam locais; mascarar via `src/sensitivity.py` e não enviar registros restritos.
- [ ] Preservar campos oficiais. Guardar principal, secundários, evidências/motivo, versão e estado em `extras.classificacao_assunto`, explicitamente inferido.
- [ ] Não confundir confiança do classificador com confiança RAG nem tratar autoavaliação LLM como probabilidade calibrada.
- [ ] Extrair a visão compacta somente se o compartilhamento justificar; reusar `llm_ecoa.completar_chat`, acrescentando **provedor e modelo nomeado por chamada**. Configuração exclusiva proposta: `CLASSIFICATION_PROVIDER=openrouter`, `CLASSIFICATION_MODEL=xiaomi/mimo-v2.6-pro`; não alterar o modelo/fallback geral das outras etapas nem a configuração global durante requisições.
- [ ] Validar disponibilidade/identificador e suporte real de saída estruturada antes do piloto; validar JSON/schema localmente mesmo quando anunciado pelo provedor. Não substituir silenciosamente MiMo por outro modelo; falha da chamada fica `falhou`. Se o budget impedir nova chamada, manter registros não tentados como `pendente`, com motivo de pausa e retomada explícitos.
- [ ] Acrescentar módulo coeso `src/classify_contracts.py`, função única para preparação e CLI fina com lote inicial/dry-run/retomada. Não criar roteador/cliente paralelo nem executar LLM em import.
- [ ] Cache separado dos embeddings, indexado por **hash do input seguro efetivamente enviado**, versões de serializer/taxonomia/prompt, provedor, modelo solicitado, parâmetros e namespace de avaliação. Manter associação local de cada ID/hash de origem ao resultado; inputs exatamente iguais podem compartilhar inferência sem perder proveniência por contrato. Não compartilhar cache entre desenvolvimento e holdout.
- [ ] Persistir modelo/provedor efetivos, tokens, estado e evidências; não reutilizar saída de outro modelo como se fosse MiMo. Resultado concluído, inclusive incerteza genuína, é cacheável; erro técnico não vira conclusão definitiva. Mudança de origem sem mudança de input seguro atualiza proveniência, não exige nova inferência.
- [ ] Reusar limites de chamadas, observabilidade e escrita atômica; deduplicar retomadas e limitar retry. Definir budgets de tokens, tentativas, chamadas e custo monetário aprovado **antes de chamadas pagas**; computar estimativas com preços vigentes, incluindo piloto, retries, holdout e lote.
- [ ] Preservar a saída aceita/proveniência do modelo nomeado. Mudança do input seguro, serializer, prompt, taxonomia, modelo ou parâmetros invalida o cache aplicável; mudança de metadado excluído do input não exige inferência nova. Não prometer execução idêntica por seed/nova chamada; tokens/estimativa não substituem faturamento real.
- [ ] Aplicar o hook na preparação de contratos, depois da sensibilidade e antes de propagar metadados; cobrir unidades/chunks cached e manter ativação explícita/desligável.
- [ ] `dry-run` não chama API nem escreve resultados. Classificação desativada permanece `pendente`; chave ausente/erro real fica `falhou`. Ingestão e consulta continuam funcionando sem rótulo, com estado observável.
- [ ] Registrar `classificacao_contrato` nos stages existentes, incluindo estado, cache hit, motivo/erro, modelo efetivo e tokens.

### Input mínimo e estratégia eficiente de tokens

**Escolha:** uma classificação por objeto/descrição único, não por documento inteiro nem por consulta de usuário. A projeção do classificador é menor que o registro compacto usado na geração de respostas.

Inspeção local, sem LLM: os 455 contratos atuais têm objeto; mediana **236 caracteres**, p90 **511**, máximo **1.173**, com **406 textos exatos distintos** antes de sanitização. Caracteres não são tokens: esses números apenas sustentam começar pelo objeto completo, que já é curto, em vez de carregar JSON/PDF. Não garantem número final de chamadas após splits, versões e complementação.

1. **Selecionar o núcleo sem outro LLM.** Ler `objetoContrato` pelo mapeamento existente; quando ausente, usar descrição equivalente realmente disponível e vinculada ao mesmo registro. Normalizar espaços e duplicações exatas sem remover negações, termos técnicos ou entregáveis secundários. Não pagar uma chamada para resumir antes de classificar. Um helper de geração que retorna conteúdo bruto quando o JSON é inválido não autoriza esse fallback no classificador: validar o registro e nunca enviar o bruto.
2. **Enviar apenas conteúdo semântico necessário.** Payload inicial exemplificado por `{"objeto":"Assinatura de software de gestão com suporte e treinamento de implantação"}`. Não enviar JSON integral, contrato/PDF, cláusulas padrão, assinaturas, CNPJ, fornecedor, órgão, município, datas, valores, histórico ou metadados de rastreabilidade. Esses campos não são necessários para definir o assunto e podem induzir vieses. Item/código especializado só entra se realmente desambiguar o objeto, não por estar disponível.
3. **Medir tokens e limitar input.** Proposta inicial: até **512 tokens de conteúdo** na primeira passagem, alvo de até **1.200 tokens de input total**, contando instruções, taxonomia e schema quando contabilizados pelo provedor. Usar contador compatível com o modelo se disponível; validar com `response.usage` no piloto. Estimativa por caracteres não é teto exato. Ajustar somente no desenvolvimento, preservando qualidade.
4. **Não cortar objetos curtos por padrão.** Se o objeto couber, enviá-lo inteiro, sem acrescentar campos. Se ultrapassar o budget, selecionar trechos/frases literalmente, de modo determinístico, preservando finalidade e diferentes entregáveis; guardar offsets/campos e `input_truncado` localmente. Não resumir com palavras inventadas nem classificar a ausência em um trecho como ausência no contrato.
5. **Complementação adaptativa e limitada.** No máximo **uma segunda passagem de contexto** quando o resultado indicar insuficiência que possa ser resolvida por informação adicional já disponível: trechos omitidos, descrição detalhada ou poucas descrições distintas de itens realmente vinculados. Teto proposto: **1.024 tokens de conteúdo** e alvo de **1.800 tokens totais**. Não buscar/enviar o documento inteiro, criar coleta remota no classificador ou repetir o mesmo input sem novidade. `outros` não dispara complementação; contrato misto sem predominância não exige outra chamada se não houver evidência adicional. Retries técnicos são separados e respeitam o budget global.
6. **Abster-se sem gastar quando não há evidência.** Sem objeto/descrição utilizável nem complemento disponível, registrar `concluido`/`indeterminado` com motivo verificável, sem chamar o LLM. Registro inválido é `falhou`; restrito é `restrito`; nenhum desses estados vira assunto. Se truncamento continuar impedindo decisão sustentada, manter incerteza e encaminhar para revisão, não aumentar contexto indefinidamente.
7. **Resposta curta e validável.** Pedir somente JSON com principal, secundários e evidência curta literal ou motivo de insuficiência. IDs, estado operacional, modelo, tokens e custo são preenchidos localmente. Proposta de teto inicial: **256 tokens de saída**, evidência/motivo de até **120 caracteres**; não pedir explicação extensa ou cadeia de pensamento. Saída cortada/inválida é falha, não incerteza genuína. Verificar se o limite inclui tokens de raciocínio; desativar/reduzir esse orçamento apenas se suportado pelo MiMo e validado no desenvolvimento, sem prometer controle que a API não oferece.
8. **Reutilizar e evitar chamadas duplicadas.** Consultar cache antes da inferência; inputs sanitizados idênticos reutilizam resultado no mesmo split/configuração, com vínculo individual ao contrato. Sem deduplicação aproximada/semântica que possa confundir objetos distintos. Prefixo estável de taxonomia/instruções pode aproveitar cache do provedor quando disponível, mas não pressupor desconto. Processar pequenos lotes de chamadas independentes, não concatenar todos os contratos em um prompt.
9. **Escolher a menor projeção que preserve qualidade.** Comparar no desenvolvimento objeto-only versus objeto+complemento necessário; medir acerto de principal/secundários, incerteza, recall na descoberta, taxa de segunda passagem, input/output/raciocínio, custo por contrato e cache hits. Se objeto-only sustentar o resultado, não pagar por mais campos. Congelar seleção/budgets antes do holdout; avaliar ali a mesma política adaptativa, sem tuning ou aumento de contexto após observar seus erros.

**Pronto:** primeira passagem usa somente o núcleo do objeto, maioria dos contratos não exige complementação, budgets e proveniência são observáveis e economia de tokens não vem de esconder perda de informação. Os limites acima são defaults de piloto, não qualidade já medida.

### Descoberta/ranking primeiro; filtros estritos são outro gate

- [ ] Propagar labels como metadados derivados, sem migração de banco obrigatória para o primeiro piloto; só materializar colunas/índices novos se o consumidor aprovado precisar.
- [ ] Primeiro uso: exibição/descoberta e sinal adicional limitado de ranking. Reusar retrieval/rerank existentes, sem excluir contratos por principal diferente, assunto desconhecido, falha ou restrição de processamento.
- [ ] Não mudar roteamento por natureza de evidência, whitelists de filtros estritos ou SQL de contagem por assunto no primeiro rollout. Rótulo é pista de busca, não prova factual ou equivalência técnica.
- [ ] Comparar ranking sem labels versus com labels em perguntas conhecidas, medindo recall@k e posição dos relevantes. Mesmo sem exclusão explícita, o ranking pode empurrar relevantes para fora do top-k.
- [ ] Medir candidatos a filtro estrito **offline**, com relevância julgada independentemente do classificador: número/proporção de contratos relevantes excluídos, recall antes/depois, primário versus primário+secundários e perda por categoria.
- [ ] Incluir no denominador relevantes sem rótulo ou com processamento pendente/falho; não ocultar essas perdas na métrica de incerteza. Restrições reais de acesso continuam regidas pela política de sensibilidade, não pelo assunto inferido.
- [ ] Definir orçamento máximo aceitável de perda de recall antes do experimento; aprovar explicitamente qualquer futuro filtro estrito. Sem medição/aprovação, manter apenas descoberta/ranking.
- [ ] Pilotar **somente desenvolvimento**, após P6A e budget aprovado: assinatura com suporte/treinamento, produção farmacêutica versus TI, materiais versus serviços de saúde, obras versus operação, objetos mistos, `outros`, objeto vazio e falha técnica.
- [ ] Congelar taxonomia, prompt, serializer, modelo/parâmetros, ranking e thresholds antes de usar o holdout. Avaliar o classificador final nos contratos reservados uma vez, em pequenos grupos com configuração idêntica; reportar todos os resultados, sem escolher runs favoráveis.
- [ ] Reportar desenvolvimento e holdout separados: distribuição dos estados; cobertura/abstenção entre concluídos; acerto/matriz de confusão do principal; precisão/recall dos secundários; erros por categoria e recall/exclusão na descoberta; latência, input/output/raciocínio, taxa de complementação, cache hits e custo estimado por contrato. Uma amostra pequena não demonstra desempenho universal nem cobertura de todas as categorias.
- [ ] Se houver ajustes após observar erros do holdout, ele deixa de ser avaliação intocada daquela nova versão. Registrar o consumo e reservar novo conjunto independente para uma futura avaliação final; não retunar e reapresentar o mesmo conjunto como confirmação independente.
- [ ] API indisponível não aumenta “assunto indeterminado”; estados falhos/pendentes e contratos relevantes sem label permanecem visíveis nos denominadores de avaliação.

**Testes em chamadas separadas, sem modelo real:**
1. `tests/test_schema.py`: invariantes de principal/secundários e separação entre resultado semântico e processamento.
2. `tests/test_record_view.py` (se houver extração); depois `tests/test_generate.py` para preservar o consumidor antigo.
3. `tests/test_llm_ecoa.py`: provedor/modelo MiMo por chamada, chave/budget ausente, não substituição silenciosa e nenhuma mudança no modelo das outras chamadas.
4. `tests/test_classify_contracts.py`: projeção só de objeto, campos proibidos ausentes, budgets/truncamento explícitos, complementação no máximo uma vez e com novidade, input vazio sem chamada, JSON curto, deduplicação exata com proveniência por ID, cache separado por split, além de taxonomia/mistos, `outros` versus `indeterminado`, falhas, retomada, prompt injection e restritos.
5. `tests/test_prepare_documents.py`: hook em novos/cached, sem rede quando desativado/dry-run, sem transformar falha em assunto.
6. `tests/test_index.py`: metadados novos com vetores preservados; depois `tests/test_retrieve.py` e `tests/test_rerank.py`, individualmente, para ranking sem descarte por label.
7. `tests/test_eval.py`: separação de splits/grupos, não vazamento de holdout, estados e denominadores de recall/exclusão; usar fixtures sintéticas/de desenvolvimento, nunca consumir o holdout final no pytest. Depois `tests/test_structured_db.py` somente se materialização aprovada. Filtros estritos ficam para gate futuro.

**Inferência real separada e paga:** pequenos grupos de contratos públicos sanitizados do desenvolvimento, após P6A e limites aprovados. O holdout só roda depois do congelamento do classificador; não é smoke test nem lote recorrente de tuning.

**Pronto P6B:** contratos auditáveis e cacheados, classificação com modelo MiMo explícito, piloto de desenvolvimento aprovado e avaliação final independente registrada; labels ajudam descoberta/ranking sem exclusão e campos oficiais permanecem intactos. Resultado inconclusivo ou marco incompleto pode ser adiado sem bloquear a demo.

## P7 — Consistência da base existente e artefatos opcionais

### P7A — Base existente, independente da classificação

- [x] Conferir hashes/esquema, PCA e cobertura de modalidade em SQLite e Neon; carga externa depende de autorização e conexão válida. *(2026-10-14: SQLite conferido — `schema_version 6`, `source_sha256 d8a4f47f…827504` igual ao `documents.jsonl`, 810 registros (455+355), 164 órgãos, 56 municípios, 1 PCA. Neon **não** tocado: sem autorização de carga externa — fallback local é o caminho da demo.)*
- [x] Reconstruir somente quando mudança de dados/esquema exigir; comparar contagens e consultas de referência antes/depois. A base já sincronizada da P0 não recebe reconstrução especulativa. *(nenhuma reconstrução executada; contagens/consultas de referência conferidas no estado atual.)*
- [x] Explicar reconstrução v6 com manifesto histórico correto e conferir hash antes de usar; recuperar/gerar diff v5→v6 com o mecanismo existente e inventários reais. *(gerado `data/inventory/DIFF_v5_v6.md` com `_diff_inventories` sobre v5.json/v6.json + nota de leitura: o `logical_id` é por geração, a v6 é recoleta curada e a sobreposição real são 89 arquivos idênticos por hash/origem; `manifest_hash sha256:26c19ee2…4094d` confere.)*
- [x] Preservar snapshot original e fallback local ensaiado se Neon não estiver disponível; registrar limitação de modalidade sem prometer resolução por labels. *(fallback SQLite ensaiado — `banco_padrao()` → `data/structured/base_estruturada.sqlite`, consulta de referência OK. Limitação registrada: contratos 3/455 com modalidade × publicações 355/355 — lacuna estrutural do PNCP, classificação P6 não a resolve.)*

**Testes, apenas módulos afetados e separadamente:** `tests/test_structured_db.py` → `tests/test_fetch_corpus.py` → casos afetados de `tests/test_structured_query.py`. Testar `tests/test_index.py` em chamada própria somente se houver alteração de índice.

**Pronto P7A:** artefatos existentes identificados e consistentes; paridade verificada ou fallback/limitação documentados. Não depende da P6.

### P7B — Promoção opcional, após desenvolvimento e avaliação final P6 aprovados

- [ ] Staging de metadados/índices enriquecidos, versão derivada própria, apontador ativo explícito e rollback.
- [ ] Reusar `src/index.py --merge-existing`, validando IDs, contagens, dimensão e igualdade dos vetores preservados.
- [ ] Materializar em banco apenas se necessário/aprovado para descoberta; não ativar filtro estrito pelo simples fato de uma coluna existir.
- [ ] Comparar ranking com e sem labels e reavaliar casos afetados; preservar o caminho sem classificação para a demo.
- [ ] Não iniciar rollout novo depois do checkpoint de freeze, quando aprovado. Trabalho incompleto permanece separado/desativado.

**Testes, cada módulo em sua chamada:** `tests/test_index.py` → `tests/test_retrieve.py` → `tests/test_rerank.py`; banco/SQL somente se efetivamente alterados.

**Pronto P7B:** enriquecimento aprovado, reversível e sem descarte por label; ou explicitamente adiado. Não é requisito de liberação da demo.

## P8B — Segurança pendente e validação final frontend/backend

A P8A já começa após P0 e se repete durante as correções. Esta fase é a validação final sobre os commits/configuração da demo, depois dos ajustes necessários, **sem esperar P6/P7B**.

**Bloqueio de exposição pública, não do ensaio local:** política do histórico ainda não aprovada. O frontend local já tem `history.service.ts`; não assumir que a rota não tem consumidor.

- [x] Escolher política do histórico antes de qualquer exposição pública. Reutilizar autorização API-key/session existente conforme a escolha, sem construir auth nova automaticamente. *(2026-10-14: dois personas — "admin" (X-API-Key = RAG_API_KEY) vê tudo; "novo usuário" (header X-Session-Id) vê só a própria conversa; sem credenciais, 401 sem revelar o log. Mesma autorização do trace, zero auth nova.)*
- [x] Se o contrato mudar, adaptar o service de histórico e o cliente HTTP único; nunca embutir chave administrativa em build público. *(adaptado: `api.ts` ganhou `sessionId` → header `X-Session-Id`; `history.service.ts` envia a sessão; `useRagWorkspace` persiste a sessão por aba (sessionStorage) e limpa na conversa nova; `useHistory` a repassa. Nenhuma chave no build público.)*
- [x] Conferir branch/commit do frontend: telas de trace podem estar em outra versão. Reutilizar serviços/componentes disponíveis, não duplicá-los. *(2026-10-14: `main` @ 91c0fbb; telas de trace **não estão** nesta versão (páginas: Consulta, Histórico e 4 catálogos) — caminho da pergunta/conversa validado por Consulta + Histórico. Adaptações da política ficaram como mudanças locais sem commit, como manda a disciplina.)*
- [x] Testar conversa de 3 turnos, feedback positivo/negativo, caminho da pergunta/conversa, quatro catálogos, nova sessão e timeout. *(3 turnos com continuação via `historico` (T2 trouxe os valores do contexto; T3 honesto — "Não há evidência nos registros disponíveis…"); feedback positivo e negativo recebidos; catálogos contratações/documentos/órgãos/fontes (5 fontes oficiais) + project/summary; nova sessão isola o histórico; timeout de 1s abandonado pelo cliente e a consulta **foi concluída e gravada** em segundo plano (q_543a46e2) — contrato cumprido.)*
- [x] Reusar `check:backend`/`check:query` existentes. O script com query real é separado dos testes offline. *(ambos passaram: API+corpus (1.425 docs, 5 bases)+banco; query real respondeu com 3 fontes, `q_d39161f4`.)*
- [x] Confirmar o formato investigado desde P8A e ensaiá-lo novamente; hospedagem/túnel dependem de política de segurança resolvida e compatibilidade HTTPS/CORS. Se estiverem bloqueados, validar a opção local sem publicar. *(formato **local** ensaiado: uvicorn 8080 + Vite 5173 + llama-server 18321; preflight CORS do 5173 libera `X-API-Key`/`X-Session-Id`; hospedagem/túnel seguem bloqueados por política — opção local validada sem publicar nada.)*

**Validação ao vivo da política (2026-10-14, após reinício autorizado do uvicorn):**
anônimo `401` · novo usuário vê só as 3 consultas da própria sessão · sessão
alheia via query param devolve 0 itens · admin com chave vê tudo (20) e filtra
por sessão · chave errada sem sessão `401`. Gap de setup fechado: `RAG_API_KEY`
(agora gerada e gravada no `.env`, nunca em código ou build) — era condição de
exposição do plano e estava faltando.

**Backend, separadamente:** `tests/test_app_api.py` → `tests/test_trace.py` → `tests/test_catalog_api.py`.

**Frontend, um alvo por comando em `../frontendRAG`:**
```bash
npx vitest run src/services/api.test.ts
npx vitest run src/services/services.test.ts
npx vitest run src/components/feedback/AnswerFeedback.test.tsx
npx vitest run src/pages/Consulta/ConsultaPage.integration.test.tsx
node --test scripts/check-backend.test.mjs
```
Não executar esses comandos em bloco para diagnosticar falhas; avançar somente após cada alvo passar.

**Pronto:** fluxo conjunto ensaiado no ambiente final; nenhuma suposição baseada apenas em teste unitário ou documentação antiga.

## P9 — Documentação, defesa e Demo Day, desde o primeiro ensaio

Roteiro inicial, plano B e registro de falhas começam na P8A; não esperar todas as correções nem o marco de classificação. A versão final acompanha o ambiente validado na P8B e o checkpoint de freeze quando a equipe o definir.

- [ ] Atualizar `README.md`, `ARQUITETURA.md`, `docs/registro_execucao_v6.md`, `docs/emails_sinteticos.md` e guia local com commits, contagens e paths reais; remover contradições entre concluído e pendente.
- [x] Se a classificação estiver ativa/aprovada, documentar taxonomia v1, principal/secundários, estados versus incerteza, MiMo pago/proveniência/budgets, desenvolvimento versus holdout e descoberta/ranking sem filtro estrito. Se adiada, registrar o marco como opcional não entregue, sem alegar seu uso na demo. *(adiada: classificação da v7 com orçamento próprio está fora do recorte do Demo Day; registrado como marco opcional **não entregue** em `docs/registro_execucao_v6.md` — a demo não usa classificação nem alega seu uso.)*
- [x] Na resposta/trace, distinguir confiabilidade da afirmação no snapshot, completude dos campos e cobertura estadual; não apresentar ausência local como ausência em todo o universo. *(respostas escopam o acervo — "Acervo consultado: 810 registros da amostra PNCP/RJ" — e ausência vem escopada: "Não há evidência nos registros disponíveis…" (validação T3 de 2026-10-14); `confianca` separa o julgamento da evidência.)*
- [ ] Registrar responsáveis atuais por módulo e coordenador, confirmados pela equipe.
- [ ] Conferir acompanhamentos: João já possui atualização em 09/10. Cada integrante escreve o próprio relato e uso de IA.
- [ ] Criar roteiro com tempos/falas e poucas consultas ao vivo: cálculo SQL, norma/ata, guardrail, ausência de evidência e continuação.
- [ ] Preparar respostas/traces gravados ou vídeo como plano B. Não apresentar replay como execução ao vivo.
- [ ] Finalizar guia de leitura após o último merge, preferindo símbolos/fluxos e commit de referência a números de linha frágeis.

**Pronto:** ensaio cronometrado, documentação consistente, defesa reproduzível e problemas remanescentes assumidos honestamente.

## 5. Disciplina de testes e registro

Padrão de chamada offline, **um arquivo de teste por execução**:

```bash
python -m pytest tests/test_query_analysis.py -m "not llm" -q
```

Para reproduzir primeiro um caso específico:

```bash
python -m pytest tests/test_logging_alignment.py::test_reformulation_logs_effective_model -q
```

Não rodar `pytest tests/`, `npm test` ou conjuntos gigantes como passo padrão deste plano. Ao final, percorrer os módulos afetados sequencialmente e registrar o conjunto de resultados; uma suíte monolítica só com autorização posterior. Os números da auditoria (1.372 passou/1 falhou) são históricos, não prova de que alterações futuras passaram.

Para cada incremento, registrar aqui ou no PR:

```text
Tarefa / commit:
Módulo alterado:
Código existente reutilizado:
Teste específico:
Teste do módulo:
Resultado real:
Artefatos / versão / hash (se houver):
Pendências / rollback:
```

## 6. Critérios de liberação

- [ ] Para exposição pública, política do histórico aprovada e código/documentação/frontend coerentes. Se a decisão continuar pendente, a demo permanece local/isolada, com risco registrado e sem publicação.
- [ ] Rerank real disponível no ambiente final.
- [ ] Cada módulo alterado aprovado nos próprios testes; nenhuma falha conhecida escondida.
- [ ] Filtros equivalentes, números SQL e citações corretos; confiabilidade no recorte separada de completude/cobertura. Zero local confiável não recebe redução automática nem vira ausência estadual.
- [ ] Golden set inteiro coberto por grupos, com correção factual e casos não avaliados explicitados.
- [ ] A demo funciona com classificação desligada. Se o marco opcional for incluído, exigir taxonomia v1 revisada, MiMo pago explícito/budget aprovado, desenvolvimento e avaliação final independente registrados, cache/proveniência e estados separados da incerteza. Somente descoberta/ranking; nunca alterar modalidade oficial nem exigir filtro estrito.
- [ ] Artefatos/bancos ativos identificados; Neon verificado ou fallback local ensaiado, sem dependência de novos artefatos de classificação.
- [ ] Ensaio fino P8A realizado desde a baseline; falhas relevantes corrigidas/verificadas e validação final P8B concluída, com plano B.
- [ ] Feature freeze sem data neste plano; quando explicitamente aprovado, registrar referência da demo e cumprir o checkpoint, sem promover funcionalidades opcionais incompletas.

## 7. Referências

- Requisitos: "C:\Users\REVY\Downloads\grande-desafio.txt" and "C:\Users\REVY\Downloads\Pendencias_Demo_Day_Grupo04.docx" fornecidos pelo usuário; requisitos também em [GRANDE_DESAFIO_CENARIOS.md](../GRANDE_DESAFIO_CENARIOS.md). Dont cite them anywhere.
- Arquitetura: [ARQUITETURA.md](../ARQUITETURA.md).
- Ablação existente: [ABLACAO_RERANK.md](ABLACAO_RERANK.md).
- Cliente existente: [llm_ecoa.py](../llm_ecoa.py), com transporte OpenRouter reutilizável; seleção por chamada do modelo pago ainda é tarefa planejada, não configuração já implementada.
- Modelo pago escolhido: [MiMo-V2.6-Pro no OpenRouter](https://openrouter.ai/xiaomi/mimo-v2.6-pro), identificador `xiaomi/mimo-v2.6-pro` conferido no catálogo. Conferir preços vigentes, disponibilidade e suporte real de saída estruturada antes do piloto; validar schema localmente, tratar rate limit/falhas e confirmar termos de tratamento dos dados. Não enviar contratos nem gastar inferência paga apenas para atualizar este plano.
