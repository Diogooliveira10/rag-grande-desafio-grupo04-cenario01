# Auditoria de relevância — alternativas R32/R33

**Estado:** inspeção documental; proposta de ampliar os julgamentos de relevância, **não aplicada ao golden**.

O benchmark `eval/retrieval_set.jsonl` foi preservado. As métricas estritas continuam contando R32/R33 como regressões, pois o trecho rotulado não está no top 10. Esta auditoria não altera scores nem torna uma resposta gerada automaticamente correta.

Fonte dos resultados inspecionados:
`eval/runs/regressao-v7-candidato-07-hybrid-constraints-2026-10-18/rankings.json`.

## R32 — equipamentos para dentistas

Pergunta: “Alguma ata registra preço de equipamentos para dentistas?”

O golden exige a frase `eventual aquisicao de equipamentos odontologicos`, de uma ata de Silva Jardim.

Top 1 no candidato:

- Chunk: `atas_conversacional:36c3e33e8b34227a:chunk-1-36c3e33e8b34`.
- Controle: `29138328000150-1-000075/2025-000002`.
- Órgão: Município de Duque de Caxias.
- `objetoContratacao`: “REGISTRO DE PREÇOS PARA AQUISIÇÃO DE EQUIPAMENTOS ODONTOLOGICOS ESPECIALIZADOS EM ATENDIMENTO ÀS NECESSIDADES DA SECRETARIA MUNICIPAL DE SAÚDE DE DUQUE DE CAXIAS, conforme condições e exigências estabelecidas neste instrumento.”

A pergunta não restringe o município, o número da ata nem a expressão “eventual aquisição”. A alternativa oferece evidência para a existência de uma ata com o objeto pedido. Não prova aquisição efetiva nem entrega dos equipamentos; não é isso que a pergunta solicita.

## R33 — materiais de cirurgia cardíaca

Pergunta: “Há ata para comprar material de cirurgia do coração?”

O golden exige a frase `insumos para cirurgia cardiaca`, de outro órgão.

Top 1 no candidato:

- Chunk: `atas_conversacional:0108828011d69f8e:chunk-1-0108828011d6`.
- Controle: `00394429000100-1-000784/2025-000001`.
- Órgão: Comando da Aeronáutica.
- `objetoContratacao`: “Aquisição de materiais de consumo para seção de cirurgia cardíaca do HFAG”.

A pergunta não restringe o órgão nem exige a palavra “insumos”. A alternativa atende ao pedido de evidência de uma ata para esse material. Isso não é um juízo de legalidade ou de execução do fornecimento.

## Encaminhamento

- Não priorizar documentos antigos da v6 só para reproduzir a frase rotulada.
- Não reescrever o golden durante a correção do ranking.
- Se houver aprovação posterior, acrescentar julgamentos explícitos para documentos alternativos e versionar o conjunto avaliado, preservando o resultado com o golden original.
- Até essa aprovação, reportar **duas regressões estritas remanescentes**, junto desta análise qualitativa. Não declarar “zero regressões” no benchmark original.
