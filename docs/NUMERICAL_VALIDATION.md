# Validação numérica — CLI e frontend, corpus v6

> **Relatório histórico:** a execução abaixo antecede o template `{{sql}}`.
> Atualmente, SQL suficiente usa `metodo=sql_template`: a LLM escreve somente
> a apresentação, e o backend insere os números depois. Este relatório não
> comprova latência ou aderência do modelo real ao novo template; os testes
> novos são offline, com clientes simulados. Veja
> [QUALIDADE_RESPOSTAS_SEM_REINDEXACAO.md](QUALIDADE_RESPOSTAS_SEM_REINDEXACAO.md).

Execução local: 09/10/2026. Escopo: números presentes no corpus, **não um censo de contratos do RJ**, nem uma verificação externa dos fatos publicados no PNCP.

## Método

`eval/validate_numerical_corpus.py oracle` lê os JSONs **brutos** referenciados pelos documentos ativos das bases estruturada e agregada. Deduplica por `numeroControlePNCP`, distingue contratos (`-2-`) de compras (`-1-`) e usa `Decimal` para calcular totais. Não usa o parser de produção nem SQL para obter os valores esperados. Guarda o hash SHA-256 do normalizado e os caminhos das fontes dos cinco maiores contratos.

Cada uma das dez perguntas foi executada pela CLI real (`python -m app.cli ... perguntar`) e pelo frontend Vite, via Playwright/Edge headless, sem mock de API ou modelo. O navegador preencheu o formulário, enviou a pergunta, verificou a resposta renderizada e salvou screenshot, payload e trace. Cada caso começa uma conversa nova, para não confundir o teste numérico com reformulação de perguntas.

Ambiente: API em `localhost:8080`, frontend em `localhost:5173`, `LLM_PROVIDER_MODE=openrouter`, `RERANK_ENABLED=false` (sentence-transformers não instalado). Embeddings seguem o caminho real do pipeline. A geração numérica corrigida não chama LLM; a execução inicial chamou OpenRouter real e registrou uma alucinação numérica.

## Resultados após as correções

**20/20 verificações passaram**: dez perguntas em cada interface. Todas as respostas do frontend tiveram HTTP 200, texto renderizado, fontes SQL válidas e trace completo com `metodo=sql_deterministico`, `llm_called=false`. Nenhum erro de JavaScript foi observado. Tempo por pergunta no navegador, incluindo a espera de renderização: 1,66–2,59 segundos nesta execução, não um benchmark de produção.

| Pergunta/medida | Referência independente | CLI | Frontend |
|---|---|---|---|
| Valor total dos contratos de 2025, pela assinatura | **R$ 2.546.786.958,21** | OK | OK |
| Quantidade de contratos assinados em 2025 | **455** | OK | OK |
| Média dos valores informativos | **R$ 5.950.436,82**, sobre 428 valores >= R$ 1,00 | OK | OK |
| Mediana dos valores informativos | **R$ 68.950,00**, sobre os mesmos 428 valores | OK | OK |
| Total dos contratos publicados em 2025 | **R$ 2.544.866.970,21**, 454 contratos | OK | OK |
| Total municipal, assinatura em 2025 | **R$ 534.883.349,11**, 389 contratos | OK | OK |
| Cinco maiores contratos, assinatura em 2025 | Valores e ordem da tabela abaixo | OK | OK |
| Contratos acima de R$ 1 milhão, assinatura em 2025 | **74**; limite estritamente exclusivo | OK | OK |
| Total por mês de assinatura em 2025 | Todos os 12 meses conferidos contra o bruto | OK | OK |
| Contratos assinados em 2024 | **0 registros**; valor desconhecido, não total financeiro zero | OK | OK |

Nos 455 contratos de 2025, todos têm `valorGlobal` numérico; 27 registram valores abaixo de R$ 1,00. Esses valores **entram no total literal**, inclusive os R$ 0,06 acumulados em registros de centavos. Média, mediana e extremos informativos continuam excluindo valores abaixo de R$ 1,00 e deixam esse denominador explícito. O total não é pagamento realizado.

### Contadores e valores das telas de catálogo

Também foram conferidos no bruto os **625 registros** do catálogo de contratações,
os contadores/totais de **164 órgãos** e o resumo de **1.425 documentos**. Nenhuma
divergência foi encontrada. A tela de Órgãos foi exercitada com o BANCO CENTRAL
DO BRASIL: **5 contratações**, **R$ 676.534.010,04**, com contador e valor renderizados.
A evidência fica em `catalog-validation.json` e `browser-catalog-org.png`; o teste
reproduzível é `eval/validate_catalog_corpus.py`.

Os escopos são diferentes por contrato: o catálogo usa a base
`contratos_estruturado`, incluindo compras publicadas, e deixa o total do órgão
nulo quando algum registro não informa `valorGlobal`. As contas de contratos
assinados também usam os contratos elegíveis da base agregada. Assim, 625
registros de catálogo **não significam 625 contratos assinados**, e o número
455 no chat não é uma inconsistência entre telas.

### Totais por esfera — assinatura em 2025

| Esfera | Contratos | Total literal |
|---|---:|---:|
| Estadual | 29 | R$ 117.534.010,90 |
| Federal | 37 | R$ 1.894.369.598,20 |
| Municipal | 389 | R$ 534.883.349,11 |
| **Total** | **455** | **R$ 2.546.786.958,21** |

### Cinco maiores valores registrados

| Ordem | Órgão, conforme o bruto | `numeroControlePNCP` | Valor |
|---|---|---|---:|
| 1 | COMANDO DA AERONAUTICA | `00394429000100-2-000183/2025` | R$ 1.196.949.440,06 |
| 2 | BANCO CENTRAL DO BRASIL | `00038166000105-2-000105/2025` | R$ 672.969.074,40 |
| 3 | MUNICIPIO DE RIO DE JANEIRO | `42498733000148-2-000009/2025` | R$ 61.552.617,60 |
| 4 | MUNICIPIO DE MAGE | `29138351000145-2-000001/2025` | R$ 54.451.933,00 |
| 5 | MUNICIPIO DE SAO PEDRO DA ALDEIA | `28909604000174-2-000001/2025` | R$ 54.169.397,10 |

## Falhas encontradas e corrigidas

1. **LLM alterava números prontos.** A primeira pergunta na CLI produziu R$ 2.979.983.621,07, embora seu contexto SQL trouxesse R$ 2.544.866.970,15. A resposta ainda declarava confiança alta. Operações analíticas suportadas, sem intenção normativa/conversacional/sem cálculo, agora são renderizadas diretamente da evidência SQL, com todas as partes e citações, fora do orçamento de contexto do LLM. Listagens e perguntas mistas permanecem no caminho de geração normal.
2. **Contrato descartado pelo ano errado na carga.** `00394429000100-2-001108/2024`, no bruto `data/raw/v6/pca_agregado/reuso_e2bb268d03b67485_e2bb268d03b6.json`, informa assinatura em **2025-10-24**, publicação em **2024-12-10**, valor **R$ 1.919.988,00**. A carga da base agregada exigia publicação em 2025, excluindo-o até das perguntas sobre assinatura. Agora contratos entram também pelo ano de assinatura do recorte. Compras continuam pelo período de publicação. O schema derivado passou de 5 para 6 para invalidar o cache automaticamente.
3. **Centavos registrados desapareciam da soma.** A política que excluía valores simbólicos de média/mediana também excluía esses valores de totais. Totais gerais, agrupados e mensais agora incluem todo valor registrado; valores ausentes permanecem distintos de zero.

**Anomalia no corpus:** o contrato citado no item 2 tem publicação anterior à assinatura. Os campos originais foram preservados, não corrigidos por suposição. Por isso os dois recortes de ano geram totais diferentes. Este relatório verifica fidelidade ao corpus, não resolve essa inconsistência na fonte.

## Testes automatizados

- Backend: **1.310 testes passaram** (`python -m pytest -q`).
- Frontend: **36 testes passaram**; `npm run build` concluiu.
- Regressões em `tests/test_numerical_validation.py`: assinatura/publicação em anos diferentes, soma literal com centavos/zeros/ausentes, preservação de todas as partes SQL com orçamento curto, não desviar perguntas mistas ou intenções incompletas para a resposta determinística.
- O trace registra o método determinístico, fontes usadas, zero prompt de geração e nenhuma chamada ao LLM, em vez de fingir que um prompt foi enviado.

## Reproduzir

Da raiz do backend:

```bash
python eval/validate_numerical_corpus.py oracle
RERANK_ENABLED=false LLM_PROVIDER_MODE=openrouter python -m uvicorn app.api:app --host 127.0.0.1 --port 8080
```

Em outro terminal, na pasta `../frontendRAG`:

```bash
VITE_API_BASE_URL=http://localhost:8080 npm run dev -- --host 127.0.0.1 --port 5173
```

Com Playwright Python instalado (`python -m pip install playwright`) e Edge disponível:

```bash
python eval/validate_numerical_corpus.py cli
python eval/validate_numerical_corpus.py browser
python eval/validate_numerical_corpus.py check
python eval/validate_catalog_corpus.py
```

Os comandos acima usam sintaxe Bash; em PowerShell, defina as variáveis com `$env:NOME = "valor"` antes do comando. `cli` usa a configuração OpenRouter local, sem modificar `.env`.

Evidências locais, não versionadas: `data/runs/numerical-validation/` (`oracle.json`, `cases.json`, `cli-results.json`, `browser-results.json`, `validation.json`, `trace-*.json`, screenshots `browser-*.png`). A pasta `baseline/` preserva as respostas anteriores às correções. As consultas pelo frontend geram entradas normais no histórico da aplicação. Os dados brutos e o normalizado não foram modificados; somente o SQLite derivado foi reconstruído.
