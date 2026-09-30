# Ablação do rerank

Registro da medição do rerank (estágio `rerank`, [src/rerank.py](../src/rerank.py)). Os relatórios completos ficam em `eval/runs/ablacao-*/` (fora do Git); para repetir:

```powershell
py .\eval\ablacao_rerank.py --candidatos 10 30
```

## Como foi medido

- **Conjunto:** [eval/retrieval_set.jsonl](../eval/retrieval_set.jsonl), com 34 perguntas "known-item" (14 normativa, 9 agregada, 6 estruturada, 5 conversacional). Cada pergunta foi escrita com outras palavras a partir de um trecho conhecido do corpus. `relevante_contem` diz quais trechos um chunk precisa conter (sem acento e sem caixa) para contar como relevante.
- **Busca:** cada pergunta é buscada só nas bases esperadas (o roteamento não entra na conta), escopo oficial. Denso e BM25 devolvem 50 candidatos cada, como no `retrieve`, e o RRF (k=60) funde as duas listas. "X+rerank@N" reordena os N primeiros da lista X.
- **Métricas:** hit@k é a fração das perguntas com algum chunk relevante entre os k primeiros; MRR@10 é a média de 1/posição do primeiro relevante. **hit@3 é a métrica que mais pesa:** com `GENERATION_CONTEXT_CHARS=3500` e `GENERATION_CHUNK_CHARS=900`, só uns 3–4 chunks cabem no prompt da geração.
- **Reranker:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, multilíngue e treinado no mMARCO (que inclui português), rodando em CPU (notebook sem GPU, 12 núcleos) com `max_length=512`. O servidor da turma ainda não tem modelo de rerank.

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

**Decisão em aberto para a equipe:** hoje o melhor hit@3 medido vem do denso (com ou sem rerank), e não do RRF + rerank. Antes de tirar o BM25 da fusão, falta medir o que ele faz de melhor e este conjunto não cobre: busca por identificador exato (CNPJ, número de controle do PNCP, número do pregão). As perguntas daqui foram escritas justamente sem repetir as palavras do trecho, o que desfavorece a busca lexical. O próximo passo é incluir perguntas de identificador e comparar RRF + rerank com denso + rerank.

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
