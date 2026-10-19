# Documentação

Este diretório reúne guias de uso e referências do projeto.

- [Plano de correção SQL/pipeline — alvo V7](PLANO_CORRECAO_SQL_PIPELINE_V7.md):
  etapas, dependências, critérios de aceite, status de aplicação e gates de promoção.
- [Reparo incremental V7](REPARO_V7.md): candidato separado, vetores recuperados,
  herança, SQLite compatível, testes curtos e lacunas ainda abertas; sem promoção.
- [Plano do corpus v7](PLANO_POSSIVEL_CORPUS_V7.md): censo dos contratos e
  publicações de 2025 em construção paralela (modo direto, projeção
  `completa`), com DIFF, regressão e re-auditoria do golden set como etapas
  de promoção; a v6 permanece a base ativa.
- [Coleta das bases e recuperação do v7](COLETA_DAS_BASES_E_RECUPERACAO_V7.md):
  fontes, limitações das APIs, recortes, instrumentos, modalidade vinculada,
  staging e bloqueios de promoção.
- [Relatório parcial da recuperação v7](RELATORIO_RECUPERACAO_V7_2026-10-15.md):
  baseline, reclassificação, pilotos, testes e pendências; sem promoção.
- [Cadastro oficial e reconciliação territorial](RELATORIO_CADASTRO_V7_2026-10-16.md):
  continuação em lotes, acompanhamento em segundo plano e janelas propostas.
- [Execução v7 — consolidação atualizada](RELATORIO_EXECUCAO_V7_2026-10-16.md):
  candidato_04, complementação de contratos, atas com território oficial,
  1.731 testes, pendências e bloqueios de promoção.
- [Guia de comandos da CLI](CLI.md): instalação, coleta, inventário, curadoria,
  preparação, busca local e embeddings.
- [Guia de execução local](GUIA_INTEGRACAO_LOCAL_FRONTEND_BACKEND.md): backend e
  frontend rodando juntos numa máquina Windows nova.
- [Contrato HTTP](FRONTEND_API_CONTRACT.md): endpoints, schemas e erros que o
  frontend consome.
- [Suplemento conversacional sintético](emails_sinteticos.md): as 25 conversas
  fictícias do tutor (20 públicas e 5 restritas), proveniência, segurança,
  reconstrução e casos do golden set.
- [Qualidade dos dados](QUALIDADE_DOS_DADOS.md): cobertura de cada campo, o que
  falta na fonte e o que o sistema faz quando o dado falta.
- [Validação numérica](NUMERICAL_VALIDATION.md): números da CLI e do frontend
  conferidos contra os JSONs brutos.
- [Ablação do rerank](ABLACAO_RERANK.md): medição do estágio de rerank e a
  decisão tomada.
- [Email sintético em Markdown](#email-sintético-em-markdown): exemplo de formato.
- [Arquitetura atual e alvo](../ARQUITETURA.md): o que já está implementado e o
  que ainda é parte do desenho do Adaptive RAG.
- [Observabilidade das consultas](OBSERVABILIDADE.md): funcionamento do SQL
  direto (C), auditoria completa opcional (D), fila e índice de traces (E),
  configuração, exemplos e limites.
- [Inventário do corpus](../CORPUS_INVENTORY.md): contagens da v6, a base
  ativa; o censo v7 tem inventário próprio em
  `data/v7_direto/inventario_direto.json`.
- [Registro da v6](registro_execucao_v6.md): distribuição, embeddings, avaliação
  e limitações das respostas; [registro da v3](registro_execucao_v3.md).
- Análises de 30/09/2026 (histórico): [cobertura do corpus v6 para as
  personas](analise_corpus_v6_personas.md) e [qualidade das consultas da
  v6](qualidade_consultas_v6.md).
- [Personas e perguntas de referência](../PERSONAS.md).
- [Enunciado completo do Grande Desafio](../GRANDE_DESAFIO_CENARIOS.md).

## Email sintético em Markdown

O arquivo [email_sintetico_exemplo.md](email_sintetico_exemplo.md) mostra um
email fictício com cabeçalho, corpo e mensagem citada. Use endereços reservados
como `example.com` e deixe claro que o conteúdo é sintético.

É só um exemplo de formato. A base `emails_sinteticos` do projeto não usa
Markdown: ela é feita das 25 conversas em JSON entregues pelo tutor
(`data/synthetic/conversacional_sintetica/`), e a preparação dessa base só
aceita esse formato. Um arquivo `.md` preparado com `--base-id
emails_sinteticos` falha (conferido em 2026-10-16). Proveniência, preparação,
indexação e reconstrução da base estão em
[emails_sinteticos.md](emails_sinteticos.md).
