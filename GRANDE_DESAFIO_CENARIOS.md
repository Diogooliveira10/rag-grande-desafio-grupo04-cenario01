# Grande Desafio · IA Generativa & RAG

**Residência TIC em Trilhas · PUC-Rio · Instituto ECOA**

| | |
| --- | --- |
| **Período** | 14/09/2026 a 23/10/2026 |
| **Formato** | Equipe |
| **Encontros** | Segunda, quarta e sexta · 4 horas |
| **Apresentações** | Toda sexta-feira · Demo Day em 23/10 com banca externa |
| **Entrega** | Repositório GitHub próprio da equipe |

---

## Como usar este documento

Este é o documento de abertura. Ele contém tudo o que sua equipe precisa para começar hoje: os três cenários disponíveis, os padrões de engenharia que valem para todos eles, e o que deve estar no repositório até as 18:00.

Leia a **Parte I** para escolher o cenário. Leia a **Parte II** antes de escrever qualquer linha de código - ela define contratos que, se você ignorar hoje, vão custar reescrita na terceira semana.

> **A escolha do cenário é definitiva.** A partir do fim do Sprint 1 o corpus está congelado e o gabarito de avaliação é específico do cenário. Não há troca no meio do caminho.

---

## O que muda em relação ao Mini Desafio

No Mini Desafio você construiu um RAG sobre **uma** base de documentos heterogêneos, em dupla, com o corpus entregue pronto.

Aqui:

| Mini Desafio | Grande Desafio |
| --- | --- |
| Corpus entregue pronto | **Você coleta**, versiona e documenta o corpus |
| Uma base | **Quatro bases** de naturezas diferentes |
| Busca direta | **Adaptive RAG** - o sistema decide onde buscar antes de buscar |
| Dense + BM25 + RRF | **+ rerank por cross-encoder**, com ablação medida |
| Resposta estruturada | **+ feedback do usuário** e histórico persistido |
| Pipeline que roda | **Pipeline observável** - logs, testes e custo de tokens por etapa |
| Dupla | Equipe de 4 a 6, com divisão de responsabilidade explícita |

---

# PARTE I - Os três cenários

## O que todos têm em comum

Cada cenário tem **quatro bases de naturezas diferentes**, e o sistema precisa **decidir para onde ir** antes de buscar. Essa decisão é o coração do **Adaptive RAG**, e ela só é interessante porque as quatro bases pedem tratamentos incompatíveis entre si:

| Natureza | O que é | Por que o tratamento muda |
| --- | --- | --- |
| **Normativa** | Leis, protocolos, editais, resoluções | Texto denso e hierárquico, com **data de vigência**. A resposta certa de 2021 pode ser a resposta errada de 2026. |
| **Estruturada** | Registros, cadastros, metadados, tabelas | Um registro é uma unidade semântica inteira. Filtro por metadado resolve o que a busca semântica não resolve. |
| **Conversacional** | Atendimentos, e-mails, pareceres, ouvidoria | Texto ruidoso, com opinião, contradição e **dado pessoal**. É onde moram os guardrails. |
| **Agregada** | Indicadores, séries, totais, estatísticas | Números sem contexto. Citar o número sem citar o recorte é errar com aparência de acerto. |

**A pergunta que o roteador precisa responder não é "sobre o que o usuário falou", e sim "que tipo de evidência responde isso".** São coisas diferentes, e confundi-las é o erro número um do Adaptive RAG mal construído.

Exemplo dessa diferença, no cenário de saúde:

- *"Qual a dose de metformina no protocolo?"* → fala de medicamento, mas a evidência está na **base normativa**.
- *"Quantas unidades de metformina foram dispensadas em 2025?"* → mesmo medicamento, evidência na **base agregada**.
- *"Pacientes reclamam da falta de metformina?"* → mesmo medicamento, evidência na **base conversacional**.

Três perguntas quase idênticas em vocabulário, três rotas distintas. Um roteador que decide por palavra-chave erra as três.

---

## Cenário 1 · Contratações Públicas do Estado do Rio de Janeiro

### O problema real

Todo órgão público brasileiro é obrigado a publicar suas contratações no **PNCP (Portal Nacional de Contratações Públicas)**, por força da Lei 14.133/2021. O resultado é uma base gigantesca, pública, gratuita - e praticamente inutilizável por quem não programa.

O portal permite consultar contrato por contrato. Não permite perguntar *"o Estado do RJ está pagando mais caro por ambulância do que os municípios?"*. Essa pergunta exige cruzar um edital em PDF, uma tabela de contratos, uma ata de registro de preços e uma série de valores homologados - quatro documentos de quatro naturezas.

Hoje isso é feito por jornalistas de dados e consultorias, manualmente, ao custo de dias de trabalho por pergunta.

### As quatro bases e suas fontes-semente

| Base | Conteúdo | Fonte | Acesso |
| --- | --- | --- | --- |
| **Normativa** | Lei 14.133/2021, decretos estaduais, editais de licitação | Planalto + arquivos de contratação do PNCP | PDF por URL |
| **Estruturada** | Contratos e contratações, UF=RJ, período fechado | PNCP, API de consulta v3 | REST, paginado |
| **Conversacional** | Atas de registro de preço + trilha de e-mails de compras | PNCP + base sintética fornecida | Texto |
| **Agregada** | Plano de Contratações Anual, valores homologados por item | PNCP | REST |

Ponto de partida da API: `https://pncp.gov.br/api/consulta/v3/api-docs` - a especificação OpenAPI está em `/api/consulta/v3/api-docs` e não exige cadastro nem login.

> **Atenção técnica:** a API do PNCP **não tem busca textual**. Filtro por objeto é feito localmente, e as consultas por publicação ou atualização exigem `modalidadeId`. Você baixa uma fatia por período e modalidade e indexa. Todo o trabalho de recuperação é seu - que é exatamente o ponto.

### Personas

**Marina · Analista de licitações de uma prefeitura do interior fluminense.** Precisa montar o edital de merenda escolar e quer saber por quanto outros municípios compraram o mesmo item, para não ser questionada pelo Tribunal de Contas por preço fora do mercado.

> *"Qual foi o valor médio por quilo de arroz contratado por municípios do RJ em 2025, e em quais modalidades?"*
>
> **Rota esperada:** agregada + estruturada.
> **Rota errada comum:** ir para a normativa, porque a palavra "modalidade" aparece na Lei 14.133. O sistema devolve a *definição* de modalidade em vez do *valor praticado*. Resposta fluente, inútil.

**Rodrigo · Repórter de dados de um veículo local.** Investiga se uma mesma empresa vem vencendo contratos de tecnologia em vários órgãos estaduais. Não programa, mas sabe exatamente o que quer perguntar.

> *"Quais contratos de licenciamento de software foram assinados por órgãos estaduais do RJ acima de R$ 500 mil em 2025, e quem foram os fornecedores?"*
>
> **O que testa:** filtragem por metadado (`uf`, `valor`, `ano`) combinada com busca semântica sobre o objeto do contrato. O Query Analyzer precisa extrair um filtro **numérico** - coisa que a busca vetorial sozinha nunca faz bem.

**Cláudia · Sócia de uma pequena empresa que quer vender para o governo.** Nunca participou de licitação, não tem vocabulário técnico.

> *"Sou MEI, posso participar de pregão eletrônico? Tem algum aberto pra material de escritório?"*
>
> **O que testa:** uma pergunta, **duas rotas**. A primeira metade é normativa (tratamento diferenciado a ME/EPP na lei); a segunda é estruturada (contratações com propostas em aberto). Um roteador que escolhe uma rota só responde metade e parece ter respondido tudo - a falha mais perigosa do RAG.

### Sensibilidade

- **Responder:** valores de contrato, nome de empresa fornecedora, CNPJ de pessoa jurídica, objeto contratado. Público por lei.
- **Mascarar:** CPF de pessoa física contratada, telefone e e-mail pessoal de servidor que aparecem em atas.
- **Recusar:** dados bancários de fornecedor, credenciais vazadas em e-mail, juízo sobre a legalidade de um contrato específico (*"esse contrato foi fraude?"* - o sistema apresenta dados, não acusa).

### Por que escolher

O mais **verificável** dos três: qualquer pessoa confere no portal se a resposta está certa. E o de melhor contraste entre busca esparsa e densa - número de contrato e CNPJ só aparecem com BM25; "compra de equipamento hospitalar" só aparece com embeddings.

---

## Cenário 2 · Saúde Pública e Protocolos Clínicos

### O problema real

O Ministério da Saúde publica os **PCDT (Protocolos Clínicos e Diretrizes Terapêuticas)** - documentos oficiais que definem, doença por doença, como fazer o diagnóstico, quais os critérios de inclusão e exclusão para o tratamento, quais medicamentos usar, em que dose, e como acompanhar o paciente.

São dezenas de documentos, de dezenas de páginas cada, e - este é o ponto - **eles mudam**. A norma exige atualização a cada dois anos, ou sempre que uma tecnologia é incorporada, alterada ou excluída do SUS.

O resultado prático: circulam simultaneamente versões antigas e vigentes do mesmo protocolo, em PDFs idênticos na aparência. Um profissional que consulta a versão errada não recebe uma resposta "mais ou menos certa" - recebe uma conduta que foi **oficialmente revogada**.

### As quatro bases e suas fontes-semente

| Base | Conteúdo | Fonte | Acesso |
| --- | --- | --- | --- |
| **Normativa** | 15 a 20 PCDT, **incluindo pares versão antiga + vigente** | Conitec / gov.br | PDF por URL |
| **Estruturada** | RENAME e bulário - medicamentos, apresentações, indicações | Ministério da Saúde / ANVISA | Tabular + PDF |
| **Conversacional** | Registros de atendimento e ouvidoria | Base sintética fornecida | Texto |
| **Agregada** | Indicadores: dispensação, cobertura, internações | DATASUS / dados abertos | CSV |

> **A armadilha central deste cenário está montada de propósito.** Se você não colocar a data de vigência no **metadado** do chunk, seu sistema vai misturar as duas versões do mesmo protocolo e responder com a média das duas - com alta confiança, porque os dois trechos são semanticamente quase idênticos. O benchmark tem perguntas para pegar exatamente isso.

### Personas

**Dra. Helena · Médica de uma unidade básica de saúde.** Atende 30 pacientes por turno. Lembra que o protocolo de diabetes tipo 2 mudou, mas não lembra o quê, e não tem 20 minutos para reler o PDF entre consultas.

> *"O que mudou no critério de início de insulinoterapia no protocolo mais recente?"*
>
> **O que testa:** a pergunta pede explicitamente uma **comparação entre versões**. O sistema precisa recuperar dois chunks da mesma seção em documentos de vigências diferentes e contrastá-los, citando cada um com sua data. É a pergunta mais difícil do cenário e a que mais separa uma boa solução de uma medíocre.

**Paulo · Gestor de assistência farmacêutica municipal.** Precisa justificar ao conselho por que determinado medicamento não está sendo dispensado.

> *"Este medicamento está na RENAME? O protocolo vigente o recomenda como primeira linha?"*
>
> **Rota esperada:** estruturada + normativa, em sequência.
> **Rota errada comum:** responder só pela RENAME e afirmar que "não está previsto" - quando o protocolo prevê e a lista ainda não incorporou. São informações diferentes, e a resposta honesta é dizer que divergem.

**Sandra · Cidadã buscando entender um tratamento.** Recebeu um diagnóstico na família e quer entender o que o SUS oferece. Não é profissional de saúde.

> *"Meu pai tem 68 anos e pressão alta. Que remédio ele deve tomar?"*
>
> **Este é um caso de recusa, e é o mais importante do cenário.** O sistema **não prescreve**. A resposta correta explica o que o protocolo estabelece em termos gerais, deixa claro que a conduta depende de avaliação clínica individual, e orienta a procurar um profissional. Um sistema que responde "ele deve tomar X" falhou - mesmo que X esteja tecnicamente no protocolo.
>
> Note a diferença: *"o que o PCDT de hipertensão estabelece como primeira linha?"* é legítima e deve ser respondida. A recusa é da **prescrição individualizada**, não do conteúdo do protocolo.

### Sensibilidade

- **Responder:** conteúdo dos protocolos, medicamentos, doses previstas, indicadores agregados.
- **Mascarar:** dados de contato em registros de ouvidoria.
- **Recusar:** prescrição ou conduta para um indivíduo; dado clínico identificável de paciente; interpretação de exame.

### Por que escolher

O de **maior impacto demonstrável** e guardrails mais nítidos - a diferença entre responder e recusar é qualitativa, não sutil, o que rende uma demo forte na banca.

**A dificuldade específica:** não há download em lote. Os PDFs são coletados um a um a partir de uma lista fixa de URLs, e vários são escaneados ou mal estruturados. Espere gastar boa parte do Sprint 1 só em extração.

---

## Cenário 3 · Inteligência sobre Pesquisa Acadêmica

### O problema real

Quem coordena um programa de pós-graduação, gerencia uma agência de fomento ou orienta alunos vive uma frustração específica: **a informação existe, aberta, em quatro lugares que não conversam**.

O edital de fomento está num PDF no site da agência. Quem foi financiado está num campo de metadado do OpenAlex. O que foi produzido está no abstract do artigo. E o parecer que aprovou ou rejeitou o projeto está em linguagem completamente diferente das outras três.

A pergunta *"o dinheiro deste edital virou produção científica?"* é óbvia de fazer e cara de responder.

### As quatro bases e suas fontes-semente

| Base | Conteúdo | Fonte | Acesso |
| --- | --- | --- | --- |
| **Normativa** | Editais de fomento, normas de ética, regulamentos de programa | FAPERJ / CNPq / CONEP | PDF por URL |
| **Estruturada** | Metadados: autores, instituições, financiamento, citações | OpenAlex | REST |
| **Conversacional** | Pareceres de revisão por pares e correspondência editorial | Base sintética fornecida | Texto |
| **Agregada** | Indicadores bibliométricos, abstracts e texto completo | OpenAlex / arXiv / SciELO | REST + texto |

### Personas

**Profa. Ana · Coordenadora de um programa de pós-graduação.** Monta o relatório quadrienal e precisa mapear a produção do programa contra as linhas de pesquisa declaradas - trabalho que hoje consome semanas de secretaria.

> *"Quais trabalhos publicados pelo nosso programa nos últimos três anos se enquadram na linha de 'saúde digital', e quais tiveram financiamento externo?"*
>
> **O que testa:** cross-source puro. O enquadramento temático vem do abstract (semântico), o vínculo institucional e o financiamento vêm do metadado (filtro estruturado). Nenhuma das duas bases responde sozinha.

**Lucas · Mestrando no primeiro semestre.** Precisa montar a revisão de literatura e saber se seu projeto exige aprovação em comitê de ética.

> *"Quais os trabalhos mais citados sobre detecção de fraude em séries temporais?"* e, logo depois, *"meu estudo usa dados públicos anonimizados, preciso submeter ao CEP?"*
>
> **O que testa:** duas perguntas seguidas, rotas opostas - a primeira agregada/bibliométrica, a segunda normativa. Caso ideal para demonstrar o roteador ao vivo na banca: mesma conversa, duas decisões visíveis.

**Beatriz · Analista de uma agência estadual de fomento.** Avalia se uma chamada pública cumpriu seus objetivos declarados e precisa de evidência, não de impressão.

> *"Os projetos aprovados na chamada de 2022 produziram publicações alinhadas ao objetivo do edital?"*
>
> **O que testa:** a pergunta mais composta do cenário. Objetivo do edital (normativa) → lista de aprovados (estruturada) → produção associada (agregada) → alinhamento temático (semântico). Se seu sistema responder isso bem, ele está pronto.

### Sensibilidade

- **Responder:** publicações, citações, afiliações, valores de edital - tudo público.
- **Mascarar:** e-mail de contato de autor correspondente.
- **Recusar:** identidade de revisor em parecer de revisão cega - quebrar o anonimato do *peer review* é falha ética grave, e o parecer sintético terá pistas plantadas de propósito para testar isso; também não se emite juízo sobre a qualidade individual de um pesquisador.

### Por que escolher

O mais **cross-source** dos três: quase nenhuma pergunta interessante se resolve numa base só, o que torna a fusão de evidências o desafio central em vez de um detalhe.

**A dificuldade específica:** as bases são tematicamente próximas - tudo é "pesquisa". O roteador não pode se apoiar em vocabulário distinto para decidir, e precisa raciocinar sobre **tipo de evidência**. É o cenário mais difícil de rotear e o mais fácil de coletar.

---

## Comparativo rápido

| | Contratações RJ | Saúde Pública | Pesquisa Acadêmica |
| --- | --- | --- | --- |
| Coleta dos dados | Média (API sem busca textual) | **Difícil** (PDF a PDF, extração ruim) | Fácil (APIs generosas) |
| Roteamento entre bases | Médio | Médio | **Difícil** (bases próximas) |
| Guardrails | Médios | **Fortes e nítidos** | Sutis (anonimato) |
| Problema de vigência | Presente | **Central** | Baixo |
| Verificabilidade da resposta | **Alta** | Média | Alta |
| Apelo na banca | Alto | **Muito alto** | Médio |

Não existe cenário mais fácil. Existem cenários que concentram a dificuldade em lugares diferentes.

---

# PARTE II - Padrões de engenharia

Esta parte vale para os três cenários. São contratos a serem seguidos: o que está aqui é verificado na correção de cada etapa.

## 2.1 Infraestrutura de modelos

A residência disponibiliza um endpoint compatível com a API da OpenAI:

```python
from openai import OpenAI

OLLAMA_URL       = "http://ecoa-llm.duckdns.org:11434/v1"
GENERATION_MODEL = "qcwind/qwen3-8b-instruct-Q4-K-M"
EMBEDDING_MODEL  = "xagi/qwen3-embedding:0.6b-q4_k_m"

client = OpenAI(base_url=OLLAMA_URL, api_key="ollama")
```

O endpoint é **compartilhado por toda a turma**. Três consequências práticas, que não são negociáveis:

1. **Cache de embeddings em disco, desde o primeiro dia.** Reembeddar o corpus inteiro a cada execução é o caminho mais rápido para derrubar o servidor de todo mundo.
2. **Índices persistidos.** `save_local` e `load_local` - recarregar, nunca reindexar.
3. **Lotes pequenos e com pausa.** Estamos compartilhando a infra com toda a turma (recomendação de lote tamanho 50 e pausa de 10 segundos).

O modelo de rerank será anunciado no Sprint 2, em endpoint próprio (ainda não está liberado!).

## 2.2 O manifesto do corpus

Sua equipe **não baixa arquivos na mão**. O corpus é descrito por um arquivo declarativo, `corpus_manifest.yaml`, versionado no repositório. Ele é a fonte única de verdade sobre o que entra na base. Dica: crie um modulo para download e versionamento do corpus (ver fetch_corpus abaixo)

Por que isso importa: sem manifesto, ninguém consegue reproduzir seu corpus, nem eu na correção, nem você mesmo daqui a três semanas quando o índice quebrar e você não lembrar quais arquivos entraram.

**Estrutura obrigatória:**

```yaml
scenario_id: contratacoes-rj
version: "2026-09-14"              # data do congelamento do corpus
description: "Contratações públicas do Estado do RJ, ano-base 2025"

bases:
  - base_id: contratos_estruturado
    natureza: estruturada            # normativa | estruturada | conversacional | agregada
    fonte: "PNCP - API de consulta v3"
    acesso:
      tipo: rest_api                 # rest_api | http_pdf | arquivo_local | sintetico
      endpoint: "https://pncp.gov.br/api/consulta/v1/contratos"
      params:
        dataInicial: "2025-01-01"
        dataFinal: "2025-12-31"
        uf: "RJ"
      paginacao: { campo: "pagina", tamanho: 50 }
      rate_limit_seg: 1.0
    selecao_deterministica: true     # mesmo manifesto ⇒ mesmo corpus
    volume_alvo: 1200                # registros esperados; divergência grande = investigar
    formato: json
    licenca: "Dados abertos gov.br - atribuição"
    sensitivity_default: publico     # publico | interno | restrito
    campo_vigencia: "dataVigenciaFim"
    chunking_sugerido: "1 contrato = 1 chunk, serializado em linguagem natural"
    metadados_extras: [orgao, modalidade, valor, municipio, ano]

  - base_id: editais_normativo
    natureza: normativa
    fonte: "Editais e legislação"
    acesso:
      tipo: http_pdf
      urls:
        - "https://exemplo.gov.br/edital-001-2025.pdf"
        - "https://exemplo.gov.br/edital-014-2025.pdf"
    selecao_deterministica: true
    volume_alvo: 20
    formato: pdf
    licenca: "Domínio público"
    sensitivity_default: publico
    campo_vigencia: "data_publicacao"
    chunking_sugerido: "Split por cláusula, com overlap"
    metadados_extras: [numero_edital, orgao, data_publicacao, objeto]
```

Os campos que carregam mais peso:

- **`selecao_deterministica`** - é o que garante que duas equipes do mesmo cenário cheguem ao mesmo corpus. Se a seleção depende de "os 100 mais recentes no momento da execução", ela **não** é determinística: fixe período, IDs ou lista de URLs e hash dos arquivos.
- **`campo_vigencia`** - é o que sustenta as perguntas de contradição entre versões. Sem ele, o cenário de saúde é impossível e os outros dois ficam frágeis.
- **`sensitivity_default`** - classificação na **ingestão**, nunca na hora de responder. Filtrar por metadado é barato e determinístico; pedir ao LLM que decida depois é caro e falível.

## 2.3 O script de extração - `src/fetch_corpus.py`

Um único script, com três subcomandos. Ele lê o manifesto e é o **único** caminho pelo qual dado entra no repositório. Dica: Salve os dados de cada etapa em arquivos

```bash
python -m src.fetch_corpus fetch      --manifest corpus_manifest.yaml
python -m src.fetch_corpus update     --manifest corpus_manifest.yaml
python -m src.fetch_corpus inventory  --version v2
```

### `fetch` - coleta inicial

Baixa tudo o que o manifesto declara e grava em uma **versão nova e imutável**:

```
data/
├── raw/
│   ├── v1/                          ← nunca editado depois de criado
│   │   ├── contratos_estruturado/
│   │   ├── editais_normativo/
│   │   ├── atas_conversacional/
│   │   └── indicadores_agregado/
│   └── v2/
├── inventory/
│   ├── v1.json
│   ├── v2.json
│   └── DIFF_v1_v2.md
└── CORPUS_INVENTORY.md              ← legível por humano, sempre da versão corrente
```

Requisitos do `fetch`:

- **Idempotente.** Rodar duas vezes com o mesmo manifesto não duplica arquivo nem reinicia download concluído.
- **Retomável.** Queda de rede no arquivo 340 de 500 não obriga a recomeçar do zero.
- **Respeita `rate_limit_seg`.** Portal público derrubado por script é problema de todos (acontece com frequência 😄🤣).
- **Registra falhas sem abortar.** Uma URL morta vira linha no inventário com status `falhou`, não uma exceção que mata a coleta inteira.

### `update` - nova versão

Reexecuta a coleta comparando com a versão anterior e cria `v(n+1)`. **Nunca sobrescreve `v(n)`.** Gera `DIFF_v1_v2.md` respondendo: quais arquivos entraram, quais saíram, quais mudaram de conteúdo (hash diferente), e quais permaneceram idênticos.

Esse diff é o insumo do **teste de regressão** de uma próxima sprint, você roda o benchmark antes e depois de atualizar a base e mostra que a recuperação não piorou nas perguntas que já funcionavam. Sem versionamento, esse teste é impossível.

### `inventory` - o inventário

Gera dois artefatos por versão. O JSON, para consumo por outros módulos:

```json
{
  "scenario_id": "contratacoes-rj",
  "version": "v1",
  "coletado_em": "2026-09-14T16:42:11-03:00",
  "manifest_hash": "sha256:9f2c...",
  "totais": {
    "arquivos": 1247,
    "bytes": 184320512,
    "falhas": 3
  },
  "por_base": [
    {
      "base_id": "contratos_estruturado",
      "natureza": "estruturada",
      "arquivos": 1200,
      "formatos": { "json": 1200 },
      "bytes": 41230000,
      "volume_alvo": 1200,
      "divergencia_alvo": 0
    }
  ],
  "arquivos": [
    {
      "path": "raw/v1/editais_normativo/edital-001-2025.pdf",
      "base_id": "editais_normativo",
      "tipo": "pdf",
      "bytes": 842103,
      "sha256": "4a7e...",
      "origem": "https://exemplo.gov.br/edital-001-2025.pdf",
      "coletado_em": "2026-09-14T16:39:02-03:00",
      "status": "ok"
    }
  ],
  "falhas": [
    { "origem": "https://exemplo.gov.br/edital-099.pdf", "erro": "HTTP 404" }
  ]
}
```

E o `CORPUS_INVENTORY.md`, legível, com: total de arquivos, tabela por base (natureza, contagem, formatos, tamanho, divergência em relação ao `volume_alvo`), lista de falhas e data da coleta.

**O `CORPUS_INVENTORY.md` é entregável de correção da Etapa 1.** Um repositório sem ele não tem a etapa corrigida.

> **Regra de ouro:** `data/raw/` **não vai para o Git**. Vão o manifesto, o inventário e o diff. Qualquer pessoa reconstrói o corpus rodando `fetch`. Coloque `data/raw/` no `.gitignore` hoje, antes do primeiro commit - arquivo grande commitado por engano é difícil de remover depois.

## 2.4 Logging - todo módulo registra por onde passou

O Grande Desafio exige um pipeline **observável**. Quando uma resposta sai errada, você precisa saber em qual etapa ela começou a dar errado, sem adivinhação e sem `print`.

Todo módulo do pipeline emite log estruturado em JSONL, em `logs/pipeline-AAAA-MM-DD.jsonl`, com no mínimo:

```json
{
  "ts": "2026-09-14T17:02:44-03:00",
  "run_id": "f3a91c2e",
  "query_id": "q_0042",
  "stage": "retrieval_hibrido",
  "status": "ok",
  "latencia_ms": 412,
  "input": "contratos software RJ acima de 500 mil",
  "output": "12 chunks; top base: contratos_estruturado",
  "tokens": { "prompt": 0, "completion": 0, "total": 0 },
  "modelo": null,
  "extra": { "filtros": {"uf": "RJ"}, "k": 12, "fetch_k": 500 }
}
```

- **`run_id`** amarra todas as etapas de uma mesma pergunta. Sem ele, os logs são ruído.
- **`stage`** usa nomes fixos, definidos pela equipe no README: `query_analysis`, `roteamento`, `retrieval_denso`, `retrieval_bm25`, `fusao_rrf`, `rerank`, `geracao`, `validacao`, `feedback`.
- **`tokens`** é preenchido em toda etapa que chama o LLM. O endpoint é compatível com a OpenAI, então `response.usage` já traz `prompt_tokens` e `completion_tokens` - basta um decorator que capture e registre. **Implemente isso hoje** : instrumentar depois significa reabrir todo o pipeline.

## 2.5 Testabilidade - todo módulo tem teste

Cada módulo entregue vem com testes em `tests/`, executáveis por `pytest`. Não é cobertura total; é **teste de contrato**: o módulo faz o que promete fazer?

Três camadas:

| Camada | O que testa | Precisa do LLM? |
| --- | --- | --- |
| **Unitário** | Serialização de registro, split por cabeçalho, normalização de filtro, cálculo do RRF | Não |
| **Contrato** | Todo chunk tem `doc_type` e `sensitivity`; todo `RAGResponse` é válido; `is_refusal=True` ⇒ `sources_used` vazia | Não (use fixtures) |
| **Integração** | Pipeline ponta a ponta com 3 perguntas conhecidas | Sim, marcado `@pytest.mark.llm` |

Regra prática: **teste que depende de rede não roda por padrão.** Os testes das duas primeiras camadas rodam offline, em segundos, sobre fixtures - 20 documentos pequenos versionados em `tests/fixtures/`. Só assim você vai realmente rodá-los.

Um teste que vale ouro e custa dez linhas:

```python
def test_todo_chunk_tem_metadado_obrigatorio(chunks_fixture):
    obrigatorios = {"source_file", "doc_type", "chunk_id", "sensitivity", "base_id"}
    for c in chunks_fixture:
        faltando = obrigatorios - set(c.metadata)
        assert not faltando, f"{c.metadata.get('chunk_id')} sem: {faltando}"
```

Esse teste pega, no Sprint 1, o erro que só apareceria como "o filtro não funciona" no Sprint 3.

## 2.6 Estrutura do repositório

```
├── acompanhamento/
│   ├──ACOMPANHAMENTO_ALUNO_NOME_01.md            ← atualizado a cada encontro
│   ├── ACOMPANHAMENTO_ALUNO_NOME_02.md            ← atualizado a cada encontro
├── README.md                    ← como rodar, arquitetura, política de sensibilidade
├── ARQUITETURA.md               ← plano de arquitetura (entregável do Sprint 1)
├── PERSONAS.md                  ← personas e perguntas de referência
├── corpus_manifest.yaml         ← o que entra na base
├── CORPUS_INVENTORY.md          ← o que entrou de fato
├── .env.example                 ← variáveis necessárias, SEM valores reais
├── .gitignore                   ← .env, data/raw/, index/, logs/, __pycache__/
├── requirements.txt
├── config.py                    ← credenciais e parâmetros centralizados
├── src/
│   ├── fetch_corpus.py          ← coleta, versionamento e inventário
│   ├── ingest.py                ← chunking e metadados
│   ├── index.py                 ← embeddings e índices por base
│   ├── router.py                ← Adaptive RAG
│   ├── retrieve.py              ← híbrida, filtros, RRF, rerank
│   ├── generate.py              ← síntese e guardrails
│   ├── schema.py                ← modelos Pydantic
│   └── obs.py                   ← logging estruturado e contagem de tokens
├── tests/
│   ├── fixtures/
│   └── test_*.py
├── eval/
├── app/
├── data/                        ← raw/ ignorado; inventory/ versionado
└── logs/                        ← ignorado pelo Git
```

---

# PARTE III - O que entregar hoje

## Entregáveis do Encontro 1

**1. Repositório criado e configurado**
- Um único repositório por equipe, privado, com **todos os integrantes** e o mentor (`@feliperafael`) como colaboradores.
- Nome: `rag-grande-desafio-<grupo>-<cenario>`
- Cada integrante configura `git config user.name` e `user.email` **antes do primeiro commit** - é por eles que a contribuição individual é auditada.

**2. `ARQUITETURA.md` - versão inicial**
- Cenário escolhido, com duas frases de justificativa.
- Diagrama do pipeline planejado, com os estágios nomeados (os mesmos nomes que vão para o campo `stage` do log).
- Divisão de responsabilidades: quem cuida de qual módulo. A divisão deve ser feita por módulo, e não em "todo mundo faz tudo (temos muito trabalho!!!)". Defina o coordenador da equipe, que será o responsável por cobrar e organizar as tarefas, mas também vai colocar a mão na massa! (pode ser trocado ao longo das sprints).

**3. `PERSONAS.md`**
- **Três personas próprias**, além das deste documento: quem usa, em que situação, com que urgência. Persona genérica ("um usuário que quer informação") não conta.
- **Nove perguntas de referência**, três por persona, cada uma classificada por **rota esperada** (qual base responde) e **tipo** (factual, filtro, cross-source, vigência, recusa, fora de escopo).
- **Uma pergunta que você acredita que seu sistema vai errar**, com a hipótese de por quê.

O último item não é retórica. Ele é reaberto nas proximas Sprints, quando você vai medir se errou mesmo, e por quê. Equipes que escrevem uma "pergunta fácil" aqui perdem a chance de mostrar diagnóstico no relatório final.

**4. `corpus_manifest.yaml` - primeira versão**
- As quatro bases declaradas, com natureza, fonte e tipo de acesso.
- Não precisa estar completo em endpoints e parâmetros hoje. Precisa existir, com as quatro bases nomeadas e a seleção pensada.

**5. `├──ACOMPANHAMENTO_ALUNO_NOME.md` com o bloco do dia**
- Relato individual, em primeira pessoa, **escrito por cada integrante**.
- Resumo do dia em conjunto: o que a equipe entregou, o que travou, qual o próximo passo.

## Horários - sem exceção

| Horário | O que acontece |
| --- | --- |
| 17:30 – 17:40 | Parar de programar. Escrever o bloco do dia no `ACOMPANHAMENTO.md`. |
| 17:40 – 18:00 | `git add` + `commit` + `push`. Conferir no GitHub que apareceu. |
| 18:00 | **Prazo final.** Vale o horário do *push*, não o do commit local. |

Registrar um dia improdutivo com honestidade **não gera desconto**. Não registrar, gera.

## Uso de assistentes de IA

Permitido e esperado. Duas condições, as mesmas do Mini Desafio:

1. O relato individual indica **onde** a IA foi usada e **o que você mudou no resultado**.
2. Na defesa técnica, **qualquer linha do repositório pode ser questionada**. Código que o integrante não consegue explicar conta como não entregue por ele.

## Checklist de saída

- [ ] Cenário escolhido e registrado
- [ ] Repositório criado, integrantes e mentor com acesso, `user.email` configurado por todos
- [ ] `.gitignore` com `.env`, `data/raw/`, `index/`, `logs/`
- [ ] `ARQUITETURA.md` com pipeline, estágios nomeados e divisão de responsabilidades
- [ ] `PERSONAS.md` com 3 personas, 9 perguntas classificadas e a pergunta que você acha que vai errar
- [ ] `corpus_manifest.yaml` com as 4 bases declaradas
- [ ] `ACOMPANHAMENTO_ALUNO_NOME.md` com relato individual de **cada** integrante
- [ ] Push feito até as 18:00

---

*TIC em Trilhas · PUC-Rio · Instituto ECOA · MCTI Futuro · Softex*
*Dúvidas e justificativas: felipe.souza@ecoa.puc-rio.br*
