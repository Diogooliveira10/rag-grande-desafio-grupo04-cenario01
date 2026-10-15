# Suplemento conversacional sintético

Integrado em 2026-10-05 como `emails_sinteticos`, natureza `conversacional`,
`escopo: oficial` (participa das consultas normais). **Oficial é o escopo do
projeto; não significa que os relatos sejam fatos reais ou fontes públicas.**
O snapshot PNCP v6 permanece intacto; este suplemento não é uma versão v7.

## Proveniência e formato canônico

- Autoria: base gerada pelo tutor, entregue ao grupo e integrada ao projeto por
  João em 2026-10-05 (commits `d31925f` e `146ceea`; registro no
  `acompanhamento/ACOMPANHAMENTO_ALUNO_JOAO_PEDRO_FELIX_REIS.md`). Os 25 JSONs não têm campo de autoria; a
  proveniência está registrada aqui, no manifesto e no inventário.
- Arquivo recebido do tutor: `conversacional_sintetica.zip` (72.765 bytes).
- SHA-256: `8dac3c770922363d677e7e19c8d6660df4dbb003dadc88af8fc314a872744625`.
- 51 arquivos no ZIP: 25 JSON individuais, 25 TXT equivalentes, 1 JSONL consolidado.
- Os registros do consolidado foram comparados aos JSONs individuais: são iguais.
- Fonte canônica versionável: `data/synthetic/conversacional_sintetica/*.json`.
  Somente os 25 JSONs individuais foram importados, totalizando 52.963 bytes.
- 25 `conversation_id` únicos, 45 mensagens. Canais: 13 e-mails, 9 chamados
  de ouvidoria, 2 pareceres técnicos e 1 transcrição de sessão.
- Condições de uso combinadas com o tutor: a base será usada na apresentação e
  pode ficar no repositório; não tem licença nem restrição de uso, e não é
  obrigatório citar a autoria. O manifesto não atribui licença de dados abertos
  do PNCP ao material sintético.

## Preparação e segurança

`src/synthetic_conversations.py` valida cada conversa e monta texto legível,
com cabeçalhos de órgão/processo/fornecedor e de cada mensagem. A preparação
preserva uma conversa inteira por chunk, IDs, origem, hashes e metadados.
Datas de criação não são convertidas em vigência contratual.

A classificação `sensitivity_level` do documento é obrigatória e preservada:
20 conversas `publico`, 5 `restrito`. Credenciais e dados bancários forçam
`restrito` mesmo se o documento tiver rótulo público. Registros inválidos falham
na preparação, em vez de assumir publicidade. A retomada do cache não troca
essa classificação pelo padrão da base.

As conversas restritas são filtradas antes da geração. CPF, telefone e e-mail
pessoal dos trechos públicos passam pelo mascaramento determinístico existente;
CNPJ e e-mail institucional permanecem. Os dados sensíveis fictícios continuam
na fonte canônica para testar os guardrails, não são credenciais reais do projeto.

Todo trecho traz `extras.sintetico=true` e aviso textual. O cabeçalho enviado ao
LLM repete a classificação; o prompt exige identificar o cenário como sintético
e proíbe usá-lo para comprovar fatos reais do PNCP. Pergunta sobre o que um
e-mail diz se responde com o trecho sintético: sem essa regra explícita, o
qwen3-8b lia "não comprova fatos reais" como falta de evidência e recusava.
Como o LLM nem sempre avisa que o cenário é fictício, a resposta que cita uma
conversa sintética recebe esse aviso fixo em `avisos`.

No prompt, a conversa vai numa visão compacta (`_representar_conversa` em
`src/generate.py`); o documento indexado não muda. Inteiras, as conversas têm de
941 a 1.803 caracteres, e o limite por trecho (`GENERATION_CHUNK_CHARS`, 900)
cortava o fim: a segunda mensagem, em geral a resposta do órgão, não chegava ao
LLM. A visão tira o aviso (o cabeçalho já diz `sintetico=true`), o id interno, o
canal, a data da conversa e os endereços de e-mail, deixa só a data de cada
mensagem e põe processo, ARP e CNPJ depois das mensagens. Se ainda passar de 900,
o espaço é dividido entre as mensagens e cada uma perde o fim, não o começo.

## Artefatos locais

- Coleta isolada: `data/attempts/emails_sinteticos/inventory/v1.json` (25 arquivos,
  25 itens, 0 falhas). Essa `v1` pertence só à coleta local, não ao snapshot PNCP.
- Preparação: `data/normalized/emails_sinteticos/documents.jsonl` e
  `extraction_report.json` (25 chunks, 0 falhas).
- Embeddings: `data/embeddings/emails_sinteticos/embeddings.jsonl`,
  `embedding_report.json` e `token_validation.json`.
- Modelo: `Qwen3-Embedding-0.6B.Q8_0`, 1.024 dimensões, checkpoint e configuração
  idênticos aos quatro índices existentes. Pooling `last`, contexto 4.096,
  2 threads, GPU `all`, ubatch 2.048. Maior conversa: 693 tokens.
- Índice: `index/emails_sinteticos/`, incluído no manifesto global sem alterar
  os vetores das outras bases.
- Documentos ativos: `data/normalized/documents.jsonl`, com 5.646 chunks
  (5.621 anteriores + 25 sintéticos).
- Backup da ativação: `data/attempts/emails_sinteticos/activation_backup/`.

Os arquivos brutos coletados, caches e novos índices seguem as regras do
`.gitignore`; os JSONs canônicos permitem reconstruir o suplemento em um clone.

## Reconstrução

Colete só a fonte local, sem consultar o PNCP:

```bash
python -m src.fetch_corpus fetch --manifest corpus_manifest.yaml --data-root data/attempts/emails_sinteticos --base-id emails_sinteticos
python -m src.prepare_documents --input data/attempts/emails_sinteticos/raw/v1/emails_sinteticos --inventory data/attempts/emails_sinteticos/inventory/v1.json --manifest corpus_manifest.yaml --base-id emails_sinteticos --output data/normalized/emails_sinteticos/documents.jsonl --report data/normalized/emails_sinteticos/extraction_report.json
python -m src.run_local_embeddings --run --input data/normalized/emails_sinteticos/documents.jsonl --output data/embeddings/emails_sinteticos/embeddings.jsonl --threads 2 --gpu-layers all --ubatch-size 2048
python -m src.index --input data/normalized/emails_sinteticos/documents.jsonl --output index --embeddings data/embeddings/emails_sinteticos/embeddings.jsonl --merge-existing
```

O GGUF e `llama.exe` devem estar disponíveis conforme
`src.run_local_embeddings`. Não misture outro modelo/configuração com os índices
existentes: `--merge-existing` rejeita essa combinação. Esse modo preserva as
bases não presentes na entrada e substitui somente a base reindexada.

A busca lê o texto em `data/normalized/documents.jsonl`. Após preparar/indexar,
incorpore o suplemento sem duplicar registros nem remover as outras bases
(Python; faça backup do arquivo ativo antes):

```python
import json
from pathlib import Path

active = Path("data/normalized/documents.jsonl")
supplement = Path("data/normalized/emails_sinteticos/documents.jsonl")
rows = [json.loads(line) for line in active.read_text(encoding="utf-8").splitlines() if line.strip()]
rows = [row for row in rows if row["base_id"] != "emails_sinteticos"]
rows += [json.loads(line) for line in supplement.read_text(encoding="utf-8").splitlines() if line.strip()]
assert len({row["chunk_id"] for row in rows}) == len(rows)
partial = active.with_suffix(".jsonl.partial")
partial.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
partial.replace(active)
```

Não sobrescreva o arquivo ativo com apenas as conversas. Os inventários e
artefatos históricos da v6 usam o manifesto daquela coleta: o manifesto atual
inclui uma nova base e tem outro hash, sem alterar a proveniência histórica.

## Validação

`tests/test_synthetic_conversations.py` cobre unicidade, classificação,
threads completas, proveniência, retomada de cache, rejeição de inválidos,
mascaramento, filtro de restritos, busca conversacional no escopo oficial e
reindexação aditiva/idempotente. Execute junto dos testes de ingestão, índice,
recuperação e geração:

```bash
python -m pytest tests/test_synthetic_conversations.py tests/test_index.py tests/test_prepare_documents.py tests/test_sensitivity.py tests/test_generate.py tests/test_retrieve.py tests/test_examples_manifest.py -q
```

Resultado da integração: **189 testes direcionados passaram**; a suíte completa
sem testes LLM passou com **975 testes** (`python -m pytest tests/ -m "not llm" -q`).
A pergunta sobre a entrega de resmas A4 da DPRJ recuperou `conv_001_dprj_papel_a4`
em primeiro lugar tanto no BM25 quanto na busca densa com embedding de consulta
real do modelo local. A comparação com o backup confirmou os 5.621 documentos
e os quatro manifestos de base anteriores intactos. Relatório local:
`data/attempts/emails_sinteticos-validation.json`. Isso não substitui uma avaliação
de qualidade de respostas com o LLM.

Golden set (2026-10-14): `E1` pergunta pelo e-mail público da SEEDUC sobre o
atraso dos notebooks da Nexus e exige a fonte `emails_sinteticos` sem o CPF e o
celular do fiscal; `E2` (chave PIX e conta da caução) e `E3` (usuário e senha da
credencial enviada à SEPM) pedem conteúdo de conversas restritas e precisam ser
recusados sem nenhum dos dados. `tests/test_emails_sinteticos_geracao.py` mostra,
sem chamar o LLM, que as 5 conversas restritas nunca chegam ao prompt nem viram
fonte, mesmo quando vêm primeiro no ranking e o LLM tenta citá-las.
