# Ablação do rerank

Registro da medição que definiu o rerank do pipeline (estágio `rerank`, [src/rerank.py](../src/rerank.py)). Os relatórios completos ficam em `eval/runs/ablacao-*/` (fora do Git); para repetir:

```powershell
py .\eval\ablacao_rerank.py --candidatos 10 30
```

## Como foi medido

- **Conjunto:** [eval/retrieval_set.jsonl](../eval/retrieval_set.jsonl), com 32 perguntas "known-item" (14 normativa, 9 agregada, 6 estruturada, 3 conversacional). Cada pergunta foi escrita com outras palavras a partir de um trecho conhecido do corpus. `relevante_contem` diz quais trechos um chunk precisa conter para contar como relevante; por isso o rótulo continua valendo depois de uma reindexação.
- **Busca:** cada pergunta é buscada só nas bases esperadas (o roteamento não entra na conta), escopo oficial, índice local de 3.130 chunks (v1). Denso e BM25 devolvem 50 candidatos cada, como no `retrieve`, e o RRF (k=60) funde as duas listas. As linhas "rerank@N" reordenam os N primeiros da fusão.
- **Métricas:** hit@k é a fração das perguntas com algum chunk relevante entre os k primeiros; MRR@10 é a média de 1/posição do primeiro relevante. **hit@3 é a métrica que mais pesa:** com `GENERATION_CONTEXT_CHARS=3500` e `GENERATION_CHUNK_CHARS=900`, só uns 3–4 chunks cabem no prompt da geração.
- **Reranker:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, multilíngue e treinado no mMARCO (que inclui português), rodando em CPU (notebook sem GPU, 12 núcleos) com `max_length=512`. O servidor da turma ainda não tem modelo de rerank.

## Resultado (30/09/2026, run `ablacao-20260930T180332Z`)

| Configuração | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 | Latência do rerank |
|---|---:|---:|---:|---:|---:|---:|
| só denso | 0,78 | 0,84 | 0,91 | 0,94 | 0,83 | — |
| só BM25 | 0,41 | 0,62 | 0,66 | 0,69 | 0,52 | — |
| híbrido RRF (antes do rerank) | 0,59 | 0,69 | 0,69 | 0,88 | 0,66 | — |
| **RRF + rerank@10 (padrão)** | **0,81** | **0,88** | 0,88 | 0,88 | **0,84** | média 1,9 s |
| RRF + rerank@30 | 0,78 | 0,84 | 0,94 | 0,94 | 0,82 | média 4,4 s |

**Decisão:** rerank ligado, sobre os 10 primeiros do RRF (`RERANK_CANDIDATES=10`). Em relação ao pipeline anterior (só RRF), o hit@3 sobe de 0,69 para 0,88 e o MRR de 0,66 para 0,84. Com 30 candidatos, o hit@10 é maior, mas o hit@3 é menor e a latência passa do dobro.

## O que a medição mostrou pelo caminho

1. **JSON truncado.** Na primeira rodada (`ablacao-20260930T174826Z`), o rerank derrubava contratos que o RRF acertava (R24 e R25 saíam do top 10). Os chunks estruturados são JSON com as chaves em ordem alfabética; o `objetoCompra` de um contrato fica perto do token 480, e o cross-encoder lê só 512 tokens, contando a pergunta. Ele nunca via o objeto. `texto_para_rerank` passou a mandar os pares `chave: valor` com os campos de conteúdo primeiro (objeto, título, descrição, órgão, modalidade, valor), sem nulos e URLs. Antes da correção, os números do rerank eram: @10 hit@3 0,81 e MRR 0,77; @30 hit@3 0,75 e MRR 0,74.
2. **O BM25 puxa a fusão para baixo neste conjunto.** Sozinho, o denso ganha do RRF (hit@3 0,84 contra 0,69). Há um viés conhecido: as perguntas foram escritas justamente sem repetir as palavras do trecho, o que desfavorece a busca lexical. Mesmo assim, vale revisar o peso do BM25 na fusão.
3. **Duas perguntas falham em todas as configurações** (R19, unidade com maior plano; R21, divisão material × serviço). O chunk certo não aparece no top 10 de nenhuma busca, então é um problema de recuperação ou de chunking, e não de ordenação.

## Limites

32 perguntas é pouco: uma pergunta vale ~3 pontos percentuais. O índice é o v1 (atas antigas, fora do RJ). A medição precisa ser repetida depois da reindexação v3. A latência inclui a disputa de CPU da máquina; a primeira chamada também carrega o modelo (~470 MB, baixado uma vez para o cache do Hugging Face).
