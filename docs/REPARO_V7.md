# Reparo incremental V7 — resultado local

**Branch:** `joao-reis` · **Data:** 18/10/2026 · **Sem promoção da base ativa.**

## Resultado dos seis itens

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
