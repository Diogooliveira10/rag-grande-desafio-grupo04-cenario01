# Projeção dos embeddings — recuperação densa

Corpus: `C:\Users\REVY\Desktop\PUC_Rio\rag-grande-desafio-grupo04-cenario01\data\normalized\documents.jsonl` · encoder: `Qwen3-Embedding-0.6B.Q8_0` · 47 casos de `eval/retrieval_set.jsonl`

| modo | hit@1 | hit@3 | hit@5 | hit@10 | mrr@10 |
| --- | --- | --- | --- | --- | --- |
| completa | 0.5532 | 0.7021 | 0.766 | 0.7872 | 0.6401 |
| compacta | 0.5106 | 0.6383 | 0.6809 | 0.6809 | 0.5734 |
| enriquecida | 0.5106 | 0.617 | 0.6809 | 0.7021 | 0.574 |

**delta hit@3 (compacta − completa): -0.0638**
**delta hit@3 (enriquecida − completa): -0.0851**
