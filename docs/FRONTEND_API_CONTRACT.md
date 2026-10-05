# Contrato oficial da API do frontend NEXO

Este documento é a fonte de verdade da integração entre o frontend React NEXO
e o backend FastAPI do Grande Desafio. O frontend não deve importar arquivos
do backend nem conhecer FAISS, BM25, RRF ou rerank.

## 1. URL base local

```text
http://localhost:8000
```

Inicie o backend na raiz do repositório:

```bash
uvicorn app.api:app --reload --host 127.0.0.1 --port 8000
```

No frontend Vite:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_USE_MOCKS=false
```

## 2. Endpoints

| Método | Endpoint | Fonte real |
| --- | --- | --- |
| `GET` | `/health` | processo FastAPI |
| `POST` | `/api/v1/query` | pipeline RAG |
| `GET` | `/api/v1/history` | SQLite de histórico |
| `POST` | `/api/v1/feedback` | SQLite de histórico/feedback |
| `GET` | `/api/v1/explore/contratacoes` | SQLite/PostgreSQL estruturado |
| `GET` | `/api/v1/explore/documentos` | inventário oficial mais recente |
| `GET` | `/api/v1/explore/orgaos` | agregação da base estruturada |
| `GET` | `/api/v1/explore/fontes` | `corpus_manifest.yaml` |
| `GET` | `/api/v1/project/summary` | manifesto e inventário oficial |

Todos os corpos são JSON UTF-8. Não há fallback para mock. Valores ausentes na
fonte são `null`; coleções ausentes são `[]`.

## 3. Saúde

### `GET /health`

Sem parâmetros ou body.

```json
{"status":"ok"}
```

Resposta: `200`. Uma falha de rede deve ser tratada como backend offline.

## 4. Consulta RAG

### `POST /api/v1/query`

Body:

| Campo | Tipo | Obrigatório | Regra |
| --- | --- | --- | --- |
| `query` | `string` | sim | 1 a 4.000 caracteres após trim |
| `top_k` | `number` | não | inteiro de 1 a 50; padrão 5 |

Campos adicionais enviados por versões antigas do frontend não alteram o
pipeline. O contrato oficial contém somente os campos acima.

Request:

```json
{
  "query": "Quais contratos de software foram assinados em 2025?",
  "top_k": 5
}
```

Response `200`:

```json
{
  "query": "Quais contratos de software foram assinados em 2025?",
  "answer": "Resposta fundamentada nas evidências recuperadas.",
  "bases_consultadas": ["estruturada"],
  "sources_used": [
    {
      "chunk_id": "contratos_estruturado:714dea5f02114ee9:chunk-1-1a24f399cecc",
      "base_id": "contratos_estruturado",
      "source_file": "registro.json"
    }
  ],
  "confidence_level": "alta",
  "is_refusal": false,
  "refusal_reason": null,
  "query_id": "q_a1b2c3d4"
}
```

`bases_consultadas`: `normativa`, `estruturada`, `conversacional` ou
`agregada`. `confidence_level`: `alta`, `media`, `baixa` ou `recusado`.
Quando `is_refusal=true`, `sources_used=[]`, a confiança é `recusado` e
`refusal_reason` é obrigatório.

Erros: `422` para body inválido; `500` quando o pipeline não consegue concluir.
A mensagem interna e credenciais nunca são retornadas.

## 5. `query_id`

Cada consulta aceita recebe um identificador opaco `q_<8 caracteres>`. O
frontend deve:

1. guardá-lo junto da resposta;
2. usá-lo sem alteração no feedback;
3. não extrair significado nem gerar identificadores substitutos.

O histórico é gravado pelo backend após o pipeline. Uma falha isolada nessa
gravação não invalida uma resposta RAG já calculada.

## 6. Histórico persistido

### `GET /api/v1/history`

Query params:

| Campo | Tipo | Padrão | Regra |
| --- | --- | --- | --- |
| `limit` | `number` | 20 | inteiro de 1 a 100 |

Response `200`:

```json
[
  {
    "query_id": "q_a1b2c3d4",
    "run_id": "7d19ab20",
    "ts": "2026-10-05T18:30:00+00:00",
    "query": "Quais contratos de software foram assinados em 2025?",
    "resposta": {
      "query": "Quais contratos de software foram assinados em 2025?",
      "answer": "Resposta fundamentada nas evidências recuperadas.",
      "bases_consultadas": ["estruturada"],
      "sources_used": [],
      "confidence_level": "alta",
      "is_refusal": false,
      "refusal_reason": null
    },
    "feedback": []
  }
]
```

A ordem é da consulta mais recente para a mais antiga. Vazio: `200` com `[]`.
O frontend não deve usar `sessionStorage`, `localStorage` ou dados simulados
como fonte paralela do histórico.

## 7. Feedback

### `POST /api/v1/feedback`

Body:

| Campo | Tipo | Obrigatório | Regra |
| --- | --- | --- | --- |
| `query_id` | `string` | sim | ID devolvido por `/query` |
| `avaliacao` | `string` | sim | `positivo` ou `negativo` |
| `comentario` | `string \| null` | não | máximo 2.000 caracteres |

```json
{
  "query_id": "q_a1b2c3d4",
  "avaliacao": "positivo",
  "comentario": "A fonte respondeu à pergunta."
}
```

Response `200`:

```json
{"status":"recebido"}
```

Erros: `404` para `query_id` desconhecido; `422` para avaliação/corpo inválido.
O frontend deve mostrar falha real; não deve simular sucesso.

## 8. Explorar contratações

### `GET /api/v1/explore/contratacoes`

Query params: `limit` (1–200, padrão 50), `offset` (>=0, padrão 0), `busca`
(objeto, fornecedor, contrato ou órgão) e `orgao` (trecho do nome).

```json
[
  {
    "id": "30381552000158-2-000016/2025",
    "numero_contrato": "30381552000158-2-000016/2025",
    "orgao": "DUQUE DE CAXIAS CAMARA MUNICIPAL",
    "orgao_cnpj": "30381552000158",
    "fornecedor": "MODERNIZACAO PUBLICA E INFORMATICA LTDA",
    "objeto": "Prestação de serviços especializados de licenciamento de programas de informática.",
    "valor_global": 38551.16,
    "data_homologacao": null,
    "modalidade": null,
    "fonte_oficial": "PNCP",
    "status": null,
    "esfera": "municipal",
    "municipio": "Duque de Caxias"
  }
]
```

`data_homologacao` permanece `null` porque essa coluna não existe de forma
confiável na base atual. Nenhum status é inferido. Vazio: `[]`.

## 9. Explorar documentos

### `GET /api/v1/explore/documentos`

Query params: `limit` (1–200), `offset` (>=0), `busca` e `tipo`. `tipo` pode
usar o rótulo (`Normativo`, `Contrato`, `Ata de Registro`, `PCA`) ou o
`base_id` correspondente.

```json
[
  {
    "id": "pncp:ata:ano_compra=2025:cnpj=28741080000155",
    "titulo": "28741080000155-1-000001/2023-000001",
    "tipo": "Ata de Registro",
    "orgao": "MUNICIPIO DE ITABORAI",
    "ano": 2023,
    "fonte": "pncp",
    "descricao": "ata",
    "link": "https://pncp.gov.br/api/consulta/v1/atas?...",
    "base_id": "atas_conversacional",
    "natureza": "conversacional",
    "formato": "json"
  }
]
```

O endpoint nunca devolve `source_file`, caminhos locais, hash de armazenamento
ou credenciais. Vazio: `[]`.

## 10. Explorar órgãos

### `GET /api/v1/explore/orgaos`

Query params: `limit` (1–200), `offset` (>=0) e `busca` (nome do órgão).

```json
[
  {
    "cnpj": "30381552000158",
    "sigla": null,
    "nome": "DUQUE DE CAXIAS CAMARA MUNICIPAL",
    "esfera": "municipal",
    "municipio": "Duque de Caxias",
    "total_contratacoes": 5,
    "valor_total": 192755.8,
    "principais_categorias": [],
    "principais_fornecedores": ["MODERNIZACAO PUBLICA E INFORMATICA LTDA"]
  }
]
```

Contagem, soma e fornecedores vêm do SQL real. A base não possui sigla nem
categoria oficial confiável; por isso esses campos são `null`/`[]`.

## 11. Explorar fontes

### `GET /api/v1/explore/fontes`

Sem parâmetros.

```json
[
  {
    "id": "contratos_estruturado",
    "nome": "PNCP - contratos assinados e contratações publicadas",
    "sigla": "PNCP",
    "instituicao": "Portal Nacional de Contratações Públicas",
    "tipo_informacao": "1 contrato = 1 chunk, serializado em linguagem natural",
    "url": "https://pncp.gov.br",
    "natureza": "estruturada",
    "frequencia_coleta": null,
    "descricao": "PNCP - contratos assinados e contratações publicadas",
    "escopo": "oficial",
    "formatos": ["json"]
  }
]
```

Os registros são derivados das bases oficiais do manifesto, sem catálogos
hardcoded. Vazio: `[]` se o manifesto não declarar bases oficiais.

## 12. Resumo do projeto

### `GET /api/v1/project/summary`

Sem parâmetros.

```json
{
  "projeto": "NEXO RJ — Adaptive RAG para contratações públicas",
  "scenario_id": "contratacoes-rj",
  "descricao": "Contratações públicas do Estado do RJ, ano-base 2025",
  "versao_manifesto": "2026-09-25",
  "versao_corpus": "v6",
  "coletado_em": "2026-09-30T17:08:01+00:00",
  "inventario_completo": true,
  "total_documentos": 1400,
  "total_itens": 1400,
  "total_falhas": 0,
  "bases": [
    {
      "base_id": "contratos_estruturado",
      "natureza": "estruturada",
      "escopo": "oficial",
      "status": "complete",
      "documentos": 625,
      "formatos": {"json": 625}
    }
  ]
}
```

As quatro bases da v6 são `contratos_estruturado` (625),
`editais_normativo` (13), `atas_conversacional` (376) e `pca_agregado` (386).

## 13. Códigos HTTP e estados de UI

| Código | Significado | Comportamento do frontend |
| --- | --- | --- |
| `200` | sucesso, inclusive lista vazia | renderizar dados ou estado vazio |
| `404` | `query_id` inexistente | informar que a consulta não foi encontrada |
| `422` | parâmetro/body inválido | mostrar mensagem de validação |
| `500` | falha interna/pipeline | mostrar erro e permitir tentar novamente |
| `503` | inventário ou banco indisponível | mostrar acervo temporariamente indisponível |

Em toda chamada, o frontend deve ter estados distintos de `loading`, `empty` e
`error`. Não substituir erro por mock, catálogo local ou sucesso falso.

## 14. Integração React → FastAPI

As origens `http://localhost:5173` e `http://127.0.0.1:5173` são permitidas.
Use um cliente HTTP central, preserve o status HTTP e configure timeout visual
compatível com consultas RAG, que podem demorar mais que consultas de catálogo.

Fluxo recomendado:

```text
React localhost:5173
        ↓ JSON/HTTP
FastAPI localhost:8000
        ↓
dados reais + pipeline RAG + histórico + feedback
```

## 15. Tipos TypeScript

```ts
export type EvidenceNature =
  | 'normativa'
  | 'estruturada'
  | 'conversacional'
  | 'agregada';

export type ConfidenceLevel = 'alta' | 'media' | 'baixa' | 'recusado';

export interface SourceRef {
  chunk_id: string;
  base_id: string;
  source_file: string;
}

export interface RagQueryRequest {
  query: string;
  top_k?: number;
}

export interface RagResponse {
  query_id: string;
  query: string;
  answer: string;
  bases_consultadas: EvidenceNature[];
  sources_used: SourceRef[];
  confidence_level: ConfidenceLevel;
  is_refusal: boolean;
  refusal_reason: string | null;
}

export interface FeedbackRequest {
  query_id: string;
  avaliacao: 'positivo' | 'negativo';
  comentario?: string | null;
}

export interface FeedbackEntry {
  avaliacao: 'positivo' | 'negativo';
  comentario: string | null;
  ts: string;
}

export interface HistoryItem {
  query_id: string;
  run_id: string;
  ts: string;
  query: string;
  resposta: Omit<RagResponse, 'query_id'>;
  feedback: FeedbackEntry[];
}

export interface ContratacaoRecord {
  id: string;
  numero_contrato: string;
  orgao: string | null;
  orgao_cnpj: string | null;
  fornecedor: string | null;
  objeto: string | null;
  valor_global: number | null;
  data_homologacao: string | null;
  modalidade: string | null;
  fonte_oficial: string;
  status: string | null;
  esfera: string | null;
  municipio: string | null;
}

export interface DocumentoRecord {
  id: string;
  titulo: string;
  tipo: string;
  orgao: string | null;
  ano: number | null;
  fonte: string | null;
  descricao: string | null;
  link: string | null;
  base_id: string;
  natureza: string;
  formato: string | null;
}

export interface OrgaoRecord {
  cnpj: string | null;
  sigla: string | null;
  nome: string;
  esfera: string | null;
  municipio: string | null;
  total_contratacoes: number;
  valor_total: number | null;
  principais_categorias: string[];
  principais_fornecedores: string[];
}

export interface OfficialSource {
  id: string;
  nome: string;
  sigla: string | null;
  instituicao: string | null;
  tipo_informacao: string | null;
  url: string | null;
  natureza: EvidenceNature;
  frequencia_coleta: string | null;
  descricao: string | null;
  escopo: string;
  formatos: string[];
}

export interface ProjectBaseSummary {
  base_id: string;
  natureza: string;
  escopo: string;
  status: string | null;
  documentos: number;
  formatos: Record<string, number>;
}

export interface ProjectSummary {
  projeto: string;
  scenario_id: string;
  descricao: string | null;
  versao_manifesto: string | null;
  versao_corpus: string;
  coletado_em: string | null;
  inventario_completo: boolean;
  total_documentos: number;
  total_itens: number;
  total_falhas: number;
  bases: ProjectBaseSummary[];
}
```
