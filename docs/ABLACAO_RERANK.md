# Ablação do rerank

Registro da medição do rerank (estágio `rerank`, [src/rerank.py](../src/rerank.py)). Os relatórios completos ficam em `eval/runs/ablacao-*/` (fora do Git); para repetir:

```powershell
py .\eval\ablacao_rerank.py --candidatos 10 30
```

## Como foi medido

- **Conjunto:** [eval/retrieval_set.jsonl](../eval/retrieval_set.jsonl), com 47 perguntas "known-item": 34 parafraseadas (14 normativa, 9 agregada, 6 estruturada, 5 conversacional) e 13 de identificador. Cada pergunta foi escrita com outras palavras a partir de um trecho conhecido do corpus. `relevante_contem` diz quais trechos um chunk precisa conter (sem acento e sem caixa) para contar como relevante.
- **Busca:** cada pergunta é buscada só nas bases esperadas (o roteamento não entra na conta), escopo oficial. Denso e BM25 devolvem 50 candidatos cada, como no `retrieve`, e o RRF (k=60) funde as duas listas. "X+rerank@N" reordena os N primeiros da lista X.
- **Métricas:** hit@k é a fração das perguntas com algum chunk relevante entre os k primeiros; MRR@10 é a média de 1/posição do primeiro relevante. **hit@3 é a métrica que mais pesa:** com `GENERATION_CONTEXT_CHARS=3500` e `GENERATION_CHUNK_CHARS=900`, só uns 3–4 chunks cabem no prompt da geração.
- **Reranker:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, multilíngue e treinado no mMARCO (que inclui português), rodando em CPU (notebook sem GPU, 12 núcleos) com `max_length=512`. Desde 07/10 o servidor da turma tem um endpoint de rerank; a comparação está na seção abaixo.
- **Dependência e fallback:** `sentence-transformers` é dependência declarada em `requirements.txt`. Se não estiver instalada ou o modelo não puder ser carregado, `retrieve` registra o estágio `rerank` como erro e devolve a ordem RRF; o relatório de avaliação continua registrando o resultado real, sem tratar o fallback como rerank bem-sucedido. Para executar deliberadamente sem rerank, use `RERANK_ENABLED=false`.

## Rerank do servidor da turma × cross-encoder local (07/10/2026)

O tutor disponibilizou o endpoint `POST /api/rerank` no servidor ECOA (`{"query", "documents", "top_n"}` → `results` com `index` e `relevance_score`). Ele está integrado como `RERANK_PROVIDER=ecoa` (`EcoaReranker` em [src/rerank.py](../src/rerank.py)), mas o padrão continua `local`: nas mesmas 47 perguntas e no mesmo índice, o cross-encoder local acerta bem mais. O endpoint responde com o modelo `xagi/qwen3-embedding:0.6b-q4_k_m`, o mesmo da busca densa, e por isso acrescenta pouco a ela.

Runs `ablacao-20261007T190427Z` (ECOA, `py .\eval\ablacao_rerank.py --modelo ecoa`) e `ablacao-20261007T191422Z` (local). Índice atual: v6 + 25 conversas sintéticas.

| Configuração | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 | Latência do rerank |
|---|---:|---:|---:|---:|---:|---:|
| híbrido RRF (sem rerank) | 0,51 | 0,64 | 0,68 | 0,87 | 0,61 | — |
| RRF + rerank@10 do ECOA | 0,55 | 0,70 | 0,72 | 0,87 | 0,65 | média 2,7 s |
| RRF + rerank@10 local (padrão) | **0,72** | **0,83** | **0,85** | 0,87 | **0,78** | média 1,9 s |
| denso + rerank@10 do ECOA | 0,57 | 0,68 | 0,74 | 0,81 | 0,65 | média 2,9 s |
| denso + rerank@10 local | 0,66 | 0,74 | 0,79 | 0,81 | 0,70 | média 1,7 s |

Os números de denso, BM25 e RRF caíram em relação a 30/09 (denso hit@3 0,88 → 0,72) porque o índice mudou com a entrada das conversas sintéticas. Esta medição compara só os dois rerankers entre si. Com `RERANK_PROVIDER=ecoa`, uma falha do endpoint passa as notas ao cross-encoder local, e o log do estágio `rerank` diz qual dos dois respondeu (`extra.rerank_backend`).

## Resultado no corpus v6 (30/09/2026, run `ablacao-20260930T185340Z`)

Índice v6: 5.621 chunks, embeddings Qwen3-Embedding-0.6B.

| Configuração | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 | Latência do rerank |
|---|---:|---:|---:|---:|---:|---:|
| só denso | 0,74 | **0,88** | 0,94 | 0,97 | **0,82** | — |
| só BM25 | 0,50 | 0,59 | 0,68 | 0,71 | 0,56 | — |
| híbrido RRF (pipeline sem rerank) | 0,62 | 0,71 | 0,71 | 0,88 | 0,69 | — |
| RRF + rerank@10 (padrão atual) | 0,74 | 0,82 | 0,85 | 0,88 | 0,78 | média 2,4 s |
| RRF + rerank@30 | 0,74 | 0,79 | 0,91 | 0,94 | 0,79 | média 6,2 s |
| denso + rerank@10 | **0,76** | **0,88** | 0,94 | 0,97 | **0,82** | média 2,1 s |
| denso + rerank@30 | 0,76 | 0,82 | 0,94 | 0,94 | 0,81 | média 6,1 s |

**Leitura:**

1. **Sobre o RRF, o rerank ajuda muito:** o hit@3 sobe de 0,71 para 0,82 e o MRR de 0,69 para 0,78. Por isso o rerank@10 é o padrão do `retrieve` (`RERANK_CANDIDATES=10`).
2. **O denso sozinho é melhor que o RRF**, e o rerank sobre o denso quase não muda nada (+1 pergunta em hit@1). O ganho do rerank sobre o RRF vem de consertar a ordem que o BM25 bagunça na fusão.
3. **Mais candidatos não compensam:** com 30 candidatos, o hit@10 é maior, mas o hit@3 é menor e o rerank fica quase três vezes mais lento.

Nessas 34 perguntas, o melhor hit@3 vem do denso (com ou sem rerank). Só que todas foram escritas sem repetir as palavras do trecho, o que desfavorece a busca lexical. Por isso a medição seguinte acrescentou perguntas de identificador.

## Perguntas de identificador (30/09/2026, run `ablacao-20260930T192250Z`)

13 perguntas com `"tipo": "identificador"`: número de controle do PNCP, número de contrato, CNPJ de fornecedor, número de pregão e UASG, número de compra, artigo de lei, número de decreto. As 34 perguntas parafraseadas continuam no conjunto, com os mesmos resultados da tabela acima.

| Configuração | identificador (13): hit@3 | MRR@10 | **todas (47): hit@3** | hit@1 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| só denso | 0,31 | 0,28 | 0,72 | 0,60 | 0,67 |
| só BM25 | **0,85** | **0,85** | 0,66 | 0,60 | 0,64 |
| híbrido RRF | 0,46 | 0,43 | 0,64 | 0,53 | 0,62 |
| **RRF + rerank@10 (padrão)** | **0,85** | 0,77 | **0,83** | **0,72** | **0,78** |
| denso + rerank@10 | 0,38 | 0,38 | 0,74 | 0,66 | 0,70 |

**Decisão:** mantém-se a fusão RRF com rerank sobre os 10 primeiros (`RERANK_CANDIDATES=10`), que é a melhor configuração no conjunto completo. O denso não encontra identificadores (acerta 4 de 13); o BM25 acha, mas erra as perguntas parafraseadas. A fusão traz os dois tipos de resultado para os candidatos, e o rerank põe o certo no topo. Sem o rerank, o RRF fica abaixo das duas buscas isoladas em hit@3.

**Formato do CNPJ:** os contratos guardam o CNPJ só com dígitos (`"niFornecedor": "05340639000130"`) e os PDFs das atas, pontuado (`44.292.391/0001-30`). A pergunta com o CNPJ pontuado achou o contrato (I03). Mas a pergunta só com dígitos não achou a ata em nenhuma configuração (I10): o BM25 quebra `44.292.391/0001-30` em cinco tokens, que não casam com `44292391000130`. Uma melhoria possível para a busca é indexar também a forma só com dígitos dos CNPJs pontuados. O art. 75 da Lei 14.133 (I12) também não aparece no top 10 de nenhuma configuração.

## Primeira medição, no corpus v1 (30/09/2026, run `ablacao-20260930T180332Z`)

Índice v1 (3.130 chunks), 32 perguntas com os alvos daquela versão. O corpus mudou na v6, então os números não são comparáveis linha a linha, mas a ordem das configurações se repetiu:

| Configuração | hit@1 | hit@3 | MRR@10 |
|---|---:|---:|---:|
| só denso | 0,78 | 0,84 | 0,83 |
| só BM25 | 0,41 | 0,62 | 0,52 |
| híbrido RRF | 0,59 | 0,69 | 0,66 |
| RRF + rerank@10 | 0,81 | 0,88 | 0,84 |
| RRF + rerank@30 | 0,78 | 0,84 | 0,82 |

## O que a medição mostrou pelo caminho

1. **JSON truncado.** Na primeira rodada (`ablacao-20260930T174826Z`), o rerank derrubava contratos que o RRF acertava. Os chunks estruturados são JSON com as chaves em ordem alfabética; o `objetoCompra` de um contrato fica perto do token 480, e o cross-encoder lê só 512 tokens, contando a pergunta. Ele nunca via o objeto. `texto_para_rerank` passou a mandar os pares `chave: valor` com os campos de conteúdo primeiro (objeto, título, descrição, órgão, fornecedor, modalidade, valor), sem nulos e URLs. Antes da correção, na v1, o rerank@10 tinha hit@3 0,81 e MRR 0,77.
2. **Perguntas que nenhuma configuração acerta** indicam problema de recuperação ou de chunking, e não de ordenação. Na v6: R17 (notebooks homologados, um registro JSON longo sem o termo "notebook" perto do começo).
3. **Rerank piorou um caso na v6:** R15 (valor do PCA da Secretaria de Saúde). O registro do PCA tem só códigos e valores ("SECRET ESTADO SAÚDE", `valorTotal`), e o modelo prefere trechos com mais texto sobre plano de contratações.

## Limites

34 perguntas é pouco: uma pergunta vale ~3 pontos percentuais. Os rótulos por trecho de texto podem aceitar mais de um chunk (ex.: a mesma lei em PDF e HTML). A latência inclui a disputa de CPU da máquina; a primeira chamada de cada processo também carrega o modelo (~470 MB, baixado uma vez para o cache do Hugging Face), cerca de 15 s.
