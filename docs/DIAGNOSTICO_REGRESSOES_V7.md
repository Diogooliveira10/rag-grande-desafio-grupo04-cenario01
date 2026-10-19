# Diagnóstico das seis regressões do candidato_07

**Estado atual:** ver a seção final “Restrições em produção”. As seções anteriores preservam os experimentos intermediários; não representam todas a configuração atual.

Base: `eval/runs/regressao-v7-candidato-07-dense-2026-10-18/report.json`.
Avaliação original: 47 perguntas, recuperação **densa pura**, sem BM25, lookup exato ou rerank; consultas Qwen3 Q8 e índice v7 de proveniência mista.

O diagnóstico consultou o FAISS persistido, com os embeddings Q8 de consulta já armazenados no cache, e verificou a presença dos trechos rotulados em `documents.jsonl` e sua posição entre todos os vetores da base selecionada. Nenhum embedding foi regenerado, nenhum índice foi modificado e o gabarito permanece intacto.

| Caso | Evidências rotuladas no corpus/vetores | Primeira posição densa | Diagnóstico |
| --- | ---: | ---: | --- |
| R24 | 1 / 1 | 9 | O topo recupera exames de tomografia, ultrassonografia e seringas. O contrato de aquisição de tomógrafo com 16 canais está presente, mas o ranking não distingue suficientemente aparelho de exame/insumo. |
| R29 | 9 / 9 | 45 | O topo recupera transporte genérico/locação de veículos. O fornecimento de recarga de cartões de vale-transporte ao CREAS está presente, mas fora do top 10. |
| R32 | 9 / 9 | 8 | O top 1 é uma ata para aquisição de equipamentos odontológicos especializados (Duque de Caxias). Responde à pergunta, mas o gabarito exige a frase de outra ata (Silva Jardim). Não é evidência de falha semântica por si só. |
| R33 | 18 / 18 | 24 | O top 1 é uma ata para materiais de consumo da seção de cirurgia cardíaca do HFAG. Responde à pergunta, mas o gabarito exige “insumos para cirurgia cardíaca”, presente em outro órgão. Não é evidência de falha semântica por si só. |
| I03 | 27 / 27 | 3 | Os dois primeiros contratos têm outros CNPJs de fornecedor. Busca densa pura não garante identidade exata; o CNPJ solicitado consta em 27 registros. |
| I04 | 1 / 1 | 29 | O topo contém outros seguros/contratos. O contrato 75/2025 da Gente Seguradora existe, mas a busca densa não garante correspondência exata entre número e fornecedor. |

## Exemplos verificáveis

- **R24, alvo:** `10476365000140-2-000002/2025`, contrato 37, objeto “Aquisição de Tomógrafo com 16 canais […]”. Top 1: compra `04543783000100-1-000023/2025`, objeto “AQUISIÇÃO DE 05 (CINCO) TOMOGRAFIAS COM SEDAÇÃO […]”.
- **R29, primeiro alvo:** compra `14787731000133-1-000007/2025`, “FORNECIMENTO DE RECARGA DE CARTÕES DE VALE TRANSPORTE PARA O CREAS […]”. Top 1: compra `13499878000165-1-000006/2025`, “Contratação de serviço de transporte de passageiros”.
- **R32, top 1:** ata `29138328000150-1-000075/2025-000007`, “REGISTRO DE PREÇOS PARA AQUISIÇÃO DE EQUIPAMENTOS ODONTOLOGICOS ESPECIALIZADOS […]”.
- **R33, top 1:** ata `00394429000100-1-000784/2025-000008`, “Aquisição de materiais de consumo para seção de cirurgia cardíaca do HFAG”.
- **I03:** o primeiro alvo é o contrato `00394452000103-2-004769/2025`, fornecedor `05340639000130`; os dois primeiros resultados têm fornecedores `54284583000159` e `30092431000196`.
- **I04, alvo:** `27792373000107-2-000060/2025`, `numeroContratoEmpenho=75/2025`, fornecedor `GENTE SEGURADORA S.A`.

## Limites e encaminhamento

- A preservação da evidência foi confirmada para estes seis casos; isso não resolve as demais lacunas vetoriais do candidato.
- R32/R33 requerem auditoria humana de relevância antes de interpretar queda de posição do documento antigo como piora de resposta. Não alterar o gabarito silenciosamente para melhorar a métrica.
- Regras de identidade exata, BM25 e rerank pertencem à recuperação híbrida. Acrescentá-los e continuar chamando a avaliação de “densa pura” tornaria a comparação enganosa.
- Não priorizar documentos da v6 nem inserir palavras das perguntas no ranking para forçar os resultados esperados.
- Uma correção da recuperação híbrida deve ser comparada nos dois snapshots com as mesmas opções e acompanhada pela ablação densa original, sem substituir seu histórico.
- Uma correção restrita à busca densa pode exigir revisão da representação e homogeneização dos embeddings; re-embedding e mudanças de snapshot dependem de autorização.

Rastreamento detalhado da investigação local: `.tmp/six-regressions-diagnosis.json` (artefato temporário, não necessário à execução do sistema).

## Correções implementadas

- `src/retrieve.py`: lookup em campos de número/ano de contrato, sem confundir referências a compras, datas ou números citados no objeto; combinação com CNPJ quando ambos são informados.
- CNPJ explicitamente solicitado como fornecedor é conferido em `niFornecedor` quando disponível, evitando promover o CNPJ do órgão como se fosse do fornecedor.
- A promoção de matches exatos preserva a relevância da fusão/reranker entre eles; não reordena por hash de chunk.
- O pool de 50 foi avaliado como **experimental** via `RERANK_CANDIDATES=50`. O padrão de 10 foi mantido: o perfil maior melhora recall agregado, mas introduz regressões em outros casos e custa mais inferência. `top_k` final continua 10.
- `src/rerank.py`: JSON PNCP completo recebe rótulos de órgão, objeto, unidade, fornecedor e número de contrato. A parte inicial do objeto é limitada a 600 caracteres antes dos demais campos; o restante é preservado no complemento, não removido da evidência original. JSON incompleto/texto comum mantêm o caminho anterior.
- O lookup de número/ano também usa os termos de fornecedor explicitamente citados na consulta para desambiguar contratos de mesmo número **antes** do corte do reranker; empates preservam a relevância. A seleção não usa IDs do benchmark.
- `tests/test_retrieval_regressions_v7.py`: fixtures sintéticas, sem copiar perguntas ou IDs do golden. **124 testes passaram em 5,39 s**, incluindo recuperação, rerank e avaliadores; não foi rodada a suíte completa.

## Experimento intermediário: pool de 50 (não ativado por padrão)

Relatório: `eval/runs/regressao-v7-candidato-07-hybrid-fix-2026-10-18/REPORT.md`.
Ambos os snapshots usam híbrido, `fetch_k=50`, pool de rerank 50 e saída top 10. Cross-encoder local `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` em CPU; consultas Q8 reutilizadas do cache produzido na GPU. **Zero falhas de reranker** em 94 chamadas. Nenhum re-embedding, alteração do golden, promoção ou mudança dos snapshots.

| Métrica | v6 | candidato_07 |
| --- | ---: | ---: |
| hit@1 | 0,8085 | 0,7872 |
| hit@3 | 0,8511 | 0,8298 |
| hit@5 | 0,9362 | 0,9362 |
| hit@10 | 0,9574 | 0,9362 |
| MRR@10 | 0,8467 | 0,8248 |

| Caso | Densa original v7 | Híbrido corrigido v7 | Estado |
| --- | ---: | ---: | --- |
| R24 | 9 | 4 | Melhorou a posição da evidência rotulada, mas o top 1 ainda é filme para tomografia, não o aparelho. **Não resolvido.** |
| R29 | fora do top 10 | 3 | Evidência correta recuperada, mas o top 1 ainda é vale-transporte de servidores de outro órgão/beneficiário. **Não resolvido.** |
| R32 | 8 | 1 | Evidência rotulada no top 1; o antigo top 1 também era semanticamente válido. |
| R33 | fora do top 10 | fora do top 10 | Top 1 responde à pergunta (material de cirurgia cardíaca do HFAG), mas o golden só aceita a frase de outro órgão. **Requer auditoria de relevância, não maquiagem do ranking.** |
| I03 | 3 | 1 | Identidade exata de fornecedor no topo. |
| I04 | fora do top 10 | 1 | Número/ano do contrato encontrado por lookup e evidência correta no topo. |

Nesse experimento persistiram **3 regressões estritas v6 × v7: R24, R29 e R33**. No mesmo candidato, o replay do híbrido anterior (pool 10 e funções pré-correção capturadas durante a inspeção) tinha hit@10 de 0,8085, contra 0,9362 com pool 50. Houve 10 melhorias de posição e 4 pioras: R03 (2→4), R09 (3→5), R12 (1→3) e R15 (9→fora do top 10). Por isso o perfil de 50 **não foi tornado padrão**. Os detalhes estão em `before-after.json`; scripts do experimento e do replay foram arquivados em `execution_sources/` ao lado dos relatórios.

A comparação acima entre densa e híbrida não isola o efeito das alterações de código: são estratégias diferentes. A ablação densa original continua preservada. O índice v7 também continua com proveniência mista; nenhuma métrica aprova sua promoção.

### Reprodução do experimento pelo avaliador comum

Com o mesmo endpoint/modelo Q8 (ou seu cache correspondente) e `RERANK_CANDIDATES=50`:

```powershell
py -m eval.medir_regressao_v7 `
  --v6-index eval/runs/projecao-v6-3modos-2026-10-14/index_completa `
  --v6-documents data/normalized/documents.jsonl `
  --v7-index data/v7_recuperacao/2026-10-15/candidato_07_reparo/index `
  --v7-documents data/v7_recuperacao/2026-10-15/candidato_07_reparo/documents.jsonl `
  --hybrid `
  --saida eval/runs/regressao-v7-candidato-07-hybrid-reproducao
```

A execução registrada usou cache local de leituras, FAISS e saídas densas/BM25 para evitar reprocessar JSONLs grandes entre experimentos, mantendo os vetores reais em float32 e sem alterar os scores. `report.json` registra hashes de fontes/snapshots; `rankings.json` e `candidates.json` preservam resultados finais e pool pontuado. A execução sem esses caches pelo CLI comum é mais lenta. Foram aproximadamente **917 s** na rodada completa registrada.

## Etapa intermediária: padrão conservador de 10 candidatos, sem análise de filtros

Após o experimento, foi corrigida também a desambiguação por fornecedor antes do corte: o número/ano isolado pode ter muitos matches, e não pode deixar o contrato da empresa solicitada fora do pool por ordem de hash.

Relatório final: `eval/runs/regressao-v7-candidato-07-hybrid-final-2026-10-18/REPORT.md`.
Comparação antes/depois no **mesmo candidato**, com pool 10, consultas e golden congelados:

| Métrica do candidato_07 | Híbrido anterior (replay) | Híbrido final |
| --- | ---: | ---: |
| hit@1 | 0,6596 (31/47) | 0,7234 (34/47) |
| hit@10 | 0,8085 (38/47) | 0,8298 (39/47) |
| MRR@10 | 0,7070 | 0,7546 |

Melhoraram R27 (2→1), R28 (3→1), R29 (4→3) e I04 (fora→1). R15 mudou de 9→10, sem perda de hit@10; **não se afirma ausência total de pioras de posição**. Os outros 42 casos empataram. Houve 94 chamadas de rerank e zero falhas; execução final com caches: aproximadamente 133 s. O replay anterior é identificado como reconstrução das funções pré-correção no respectivo `report.json`, não como um novo snapshot de dados.

| Dos seis casos originais | Posição final no candidato | Estado |
| --- | ---: | --- |
| I03 | 1 | Identidade correta; já era top 1 no híbrido anterior, embora estivesse em 3 na ablação densa. |
| I04 | 1 | Lookup e desambiguação corrigidos, mesmo com pool 10. |
| R24 | fora do top 10 | **Ainda não resolvido.** O perfil de 50 chegou à posição 4, mas o reranker ainda confunde aparelho e insumo. |
| R29 | 3 | Melhorou, mas **ainda não resolvido no top 1**: há confusão de órgão/beneficiário. |
| R32/R33 | trechos literais fora do top 10 | Topos semanticamente válidos; precisam de auditoria de relevância do golden, que permaneceu intacto. |

Com a configuração final, a comparação v6 × v7 tem hit@10 de 0,8936 × 0,8298 e quatro regressões estritas (R24/R29/R32/R33). Isso não representa solução completa das seis nem autorização de promoção. Para reproduzir o padrão final, usar o mesmo comando acima com `RERANK_CANDIDATES=10` e um novo diretório de saída.

### Decisão tomada

O usuário escolheu **manter o modelo e trabalhar nas restrições**, sem baixar outro reranker. O pool padrão permaneceu 10.

## Restrições em produção — estado atual

Correções adicionais:

- `orgao contains` exige todos os termos informativos no **campo do órgão**, ignorando conectores e aceitando qualificadores intermediários. “Fundo de Assistência Social” passa a casar com “Fundo Municipal de Assistência Social”; “Secretaria Municipal” não casa com “Secretaria Estadual”. O objeto/fornecedor não satisfaz esse filtro.
- `src/retrieval_constraints.py` reconhece pedidos explícitos de aparelho/máquina completos e rejeita núcleos de objeto claramente auxiliares (peças, consumíveis ou serviços). “Equipamento com peças incluídas” não é descartado por mencionar peças depois do bem principal. Pedidos de manutenção/locação/peças não são convertidos em compra do aparelho inteiro.
- Quando a pergunta explicita um tema imediatamente após “aparelho/máquina de/para”, o filtro usa radicais lexicais simples **só no objeto**, sem criar sinônimos técnicos ou usar o nome do fornecedor como evidência do produto. Siglas e equivalências técnicas não reconhecidas continuam fora do escopo dessa regra.
- Esses critérios são aplicados antes da busca densa, BM25, lookup e rerank. Não são relaxados para preencher uma resposta com insumos incompatíveis. Normas/PCA não recebem a restrição de tipo de objeto contratual.
- Corrigido um `AttributeError` no modo LLM de `query_analysis`: `_analisar()` retorna `QueryFilters`, não um resultado com `.filtros`. A validação do CNPJ fazia respostas LLM válidas caírem em fallback; os testes existentes passaram a exercitar o caminho correto.
- O avaliador comum ganhou `--query-filters`, exigindo `--hybrid`: extrai os filtros por regras uma vez por pergunta e aplica o mesmo recorte nos dois snapshots. A ablação densa permanece inalterada.

### Resultado dos 47 casos

Relatório atual: `eval/runs/regressao-v7-candidato-07-hybrid-constraints-2026-10-18/REPORT.md`.

Configuração: híbrido, query_analysis por regras, pool 10, top_k 10; mesmo MiniLM local em CPU e consultas Q8 do cache gerado na GPU. **564 testes direcionados passaram em 18,50 s**; zero falhas de reranker em 92 chamadas (duas consultas ficaram sem candidatos e não chamaram o modelo). A avaliação completa com caches levou aproximadamente 305 s.

| Métrica | v6 | candidato_07 |
| --- | ---: | ---: |
| hit@1 | 0,7660 | 0,7447 |
| hit@3 | 0,8298 | 0,7872 |
| hit@10 | 0,8511 | 0,8085 |
| MRR@10 | 0,7986 | 0,7667 |

**R24, R29, I03 e I04 estão em primeiro lugar.**

- R24 recupera o contrato `10476365000140-2-000002/2025`, de aquisição de tomógrafo com 16 canais, e não filme/seringa nem equipamento de outra tecnologia.
- R29 recupera o fornecimento de recarga de cartões de vale-transporte do Fundo Municipal de Assistência Social, não benefícios de servidores de outro órgão.
- R32/R33 continuam como as **duas regressões estritas restantes**, porque o golden exige frases de documentos específicos. O top 1 contém atas semanticamente compatíveis. A [auditoria de relevância](AUDITORIA_RELEVANCIA_V7.md) documenta os trechos; o golden não foi alterado.

### Limites revelados pelo recorte

Não comparar diretamente esta rodada com a anterior sem análise de filtros como se fosse uma comparação isolada de código. Ao ativar o recorte automático, **R15 e R19 ficaram sem o trecho rotulado**, inclusive na referência: R15 recebe `orgao` e `data_vigencia` apesar de perguntar pelo plano de contratações; R19 recebe órgão “MUNICIPIO DE RIO DE JANEIRO” quando a pergunta fala de localização. São pendências do analisador/semântica de campos expostas pela avaliação, não resolvidas nesta correção dos seis casos. Não há afirmação de ausência de problemas ponta a ponta, nem autorização de promoção.

O hash do golden continua `252376a483970733be398aa5ddcdddcc19c2988dc3f2b56aa935995e29d9e703`. Documentos e índices não foram re-embedados, promovidos ou substituídos.

### Reprodução atual

Usar o comando da seção de reprodução com `RERANK_CANDIDATES=10`, acrescentando `--query-filters` junto de `--hybrid`, em um novo diretório de saída. O modo de análise e os filtros por caso ficam registrados no JSON; os critérios derivados de objeto são registrados pelo executor de recuperação no log de filtros.

A avaliação continua usando **rotas do golden**: inclui análise de filtros e recuperação, mas não mede decisão do roteador, suficiência SQL ou geração de resposta.
