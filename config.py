"""Configurações compartilhadas do projeto.

Os valores podem ser sobrescritos por variáveis no arquivo ``.env``.
Não coloque credenciais reais neste arquivo; use ``.env`` localmente.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


# Endpoint e modelos compartilhados da turma.
OLLAMA_URL = _env("OLLAMA_URL", "http://ecoa-llm.duckdns.org:11434/v1")
OLLAMA_API_KEY = _env("OLLAMA_API_KEY", "ollama")
GENERATION_MODEL = _env("GENERATION_MODEL", "qcwind/qwen3-8b-instruct-Q4-K-M")
MODEL_MIN_INTERVAL_SECONDS = float(_env("MODEL_MIN_INTERVAL_SECONDS", "10"))
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Modelo preferido das chamadas OpenRouter (geração/reformulação). O padrão
# preserva o comportamento anterior: sem configurar, só o "openrouter/free" é
# usado. A demo define explicitamente um modelo nomeado (ex. MiMo-V2.6-Flash) e
# mantém o free como reserva quando ele estiver indisponível.
OPENROUTER_MODEL = _env("OPENROUTER_MODEL", "openrouter/free")
# Reserva usada somente quando o modelo preferido está indisponível (rede,
# timeout, 404/408/429/5xx). Erros de entrada não trocam de modelo.
OPENROUTER_MODEL_FALLBACK = _env("OPENROUTER_MODEL_FALLBACK", "openrouter/free")
OPENROUTER_MAX_TOKENS = int(_env("OPENROUTER_MAX_TOKENS", "4096"))
# Prazo total de cada chamada ao OpenRouter, em segundos. O timeout do SDK é
# entre pacotes, e o OpenRouter manda sinais de "processando" enquanto gera:
# no golden de 20/10 uma chamada levou 277 s com timeout de 60 s.
OPENROUTER_TIMEOUT = float(_env("OPENROUTER_TIMEOUT", "60"))
LLM_PROVIDER_MODE = _env("LLM_PROVIDER_MODE", "auto")
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "xagi/qwen3-embedding:0.6b-q4_k_m")
# Respiro entre lotes de embedding quando o encoder é o endpoint compartilhado
# (ECOA). O modo direto usa isto antes de cair no encoder local.
PAUSA_ENTRE_LOTES_EMBEDDING = float(_env("PAUSA_ENTRE_LOTES_EMBEDDING", "0.5"))
# Orçamento do contexto da geração (src/generate.py). O Ollama da turma usa
# janela de 2048 tokens e corta o INÍCIO do prompt que passar disso, levando
# junto a instrução de responder em JSON; para essa janela curta, 3500 chars
# deixam o prompt em torno de 1.500 tokens, com folga para a resposta. O
# OpenRouter tem janela grande e o orçamento precisa comportar o top-k inteiro:
# 3500 chars descartavam 7 dos 10 chunks recuperados e o modelo declarava
# "ausente" o que estava no topo do ranking (defeito achado na P2.2).
ORCAMENTO_CONTEXTO_JANELA_CURTA = 3500
ORCAMENTO_CONTEXTO_OPENROUTER = 12000
GENERATION_CONTEXT_CHARS = int(_env(
    "GENERATION_CONTEXT_CHARS",
    str(ORCAMENTO_CONTEXTO_OPENROUTER if LLM_PROVIDER_MODE == "openrouter"
        else ORCAMENTO_CONTEXTO_JANELA_CURTA)))
GENERATION_CHUNK_CHARS = int(_env("GENERATION_CHUNK_CHARS", "900"))
# Tempo máximo (s) de cada chamada de geração e quantas vezes tentar de novo.
# A geração normal leva de 20 a 90 s no servidor da turma.
GENERATION_TIMEOUT = float(_env("GENERATION_TIMEOUT", "180"))
# Prazo próprio de cada reformulação: a etapa reescreve consultas curtas e não
# deve competir com a geração pelo mesmo timeout global. Nos logs de outubro,
# 91% das reescritas do ECOA terminam em até 60 s (com 15 s, só 43%: o resto
# caía no fallback e a P4.3 falhava); o prazo corta a cauda, que chegou a 149 s.
REFORMULACAO_TIMEOUT = float(_env("REFORMULACAO_TIMEOUT", "60"))
GENERATION_MAX_RETRIES = int(_env("GENERATION_MAX_RETRIES", "1"))
# Temperatura das chamadas com resposta em JSON (geração e análise da pergunta).
# 0 = a mesma pergunta com as mesmas evidências tende ao mesmo texto; com 0,2 a
# redação variava entre execuções e mudava o resultado do golden set.
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0"))
# ``reasoning_effort`` mandado ao LLM do ECOA. Vazio = padrão do servidor (o
# Qwen3 pensa antes de responder). ``none`` desliga o raciocínio: medido em
# 2026-10-16, a mesma pergunta caiu de 15 s para 2 s. Só o ECOA recebe; o
# OpenRouter usa o padrão de cada modelo.
GENERATION_REASONING_EFFORT = _env("GENERATION_REASONING_EFFORT", "").strip()
# Resposta que sai do SQL redigida pelo LLM (só a apresentação; números e
# fontes continuam do SQL). Desligado por padrão: medido em 2026-10-19, as
# consultas ao vivo iam de ~2,5 s para 7-25 s e passavam a depender do ECOA.
SQL_APRESENTACAO_LLM = _env("SQL_APRESENTACAO_LLM", "false").lower() in {"1", "true", "sim", "yes"}
# Rerank (src/rerank.py). "local" (padrão) = cross-encoder local (RERANK_MODEL
# aceita qualquer CrossEncoder do sentence-transformers); "ecoa" = endpoint
# /api/rerank do servidor da turma, com o local de reserva. Na ablação de
# 2026-10-07 o local acertou mais (docs/ABLACAO_RERANK.md).
RERANK_ENABLED = _env("RERANK_ENABLED", "true").lower() in {"1", "true", "sim", "yes"}
RERANK_PROVIDER = _env("RERANK_PROVIDER", "local").lower()
RERANK_URL = _env("RERANK_URL", "") or OLLAMA_URL.rstrip("/").removesuffix("/v1") + "/api/rerank"
RERANK_TIMEOUT = float(_env("RERANK_TIMEOUT", "30"))
RERANK_MODEL = _env("RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
# O perfil experimental de 50 candidatos aumenta recall, mas ainda tem
# regressões de ranking; ativar explicitamente, não como mudança global.
RERANK_CANDIDATES = int(_env("RERANK_CANDIDATES", "10"))
# Variedade na busca: no máximo N trechos da mesma compra (ata) à frente dos
# candidatos do rerank; o excedente vai para o fim, não é descartado. 0 desliga.
RETRIEVAL_MAX_PER_PURCHASE = int(_env("RETRIEVAL_MAX_PER_PURCHASE", "2"))
RERANK_MAX_LENGTH = int(_env("RERANK_MAX_LENGTH", "512"))
# Carregar o cross-encoder quando a API sobe, e não na primeira pergunta:
# o carregamento leva ~15 s (69 s se ainda precisar baixar o modelo) e o
# rerank em si menos de 1 s (medido em 2026-10-14).
RERANK_PRELOAD = _env("RERANK_PRELOAD", "true").lower() in {"1", "true", "sim", "yes"}
# Ler os índices (vectors.jsonl, FAISS e documents.jsonl, ~150 MB) quando a API
# sobe: a busca guarda o que leu, e a primeira pergunta deixa de pagar ~3 s.
INDEX_PRELOAD = _env("INDEX_PRELOAD", "true").lower() in {"1", "true", "sim", "yes"}
OLLAMA_LOCAL_URL = _env("OLLAMA_LOCAL_URL", "http://localhost:11434/v1")
OLLAMA_LOCAL_API_KEY = _env("OLLAMA_LOCAL_API_KEY", "ollama")
OLLAMA_LOCAL_EMBEDDING_MODEL = _env("OLLAMA_LOCAL_EMBEDDING_MODEL", EMBEDDING_MODEL)

# Presets comuns OpenAI-compatible. Todos usam o mesmo contrato
# ``POST /v1/embeddings``; o endpoint e o modelo podem ser sobrescritos.
OPENAI_API_URL = _env("OPENAI_API_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = _env("OPENAI_API_KEY", "")
OPENAI_EMBEDDING_MODEL = _env("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
CUSTOM_API_URL = _env("CUSTOM_API_URL", "")
CUSTOM_API_KEY = _env("CUSTOM_API_KEY", "")
CUSTOM_EMBEDDING_MODEL = _env("CUSTOM_EMBEDDING_MODEL", EMBEDDING_MODEL)

# Provedor dos embeddings de CONSULTA (src/retrieve.py). Vale os mesmos nomes
# de src.create_embeddings._provider_defaults: ecoa (padrão, endpoint
# compartilhado da turma), ollama (llama.cpp/Ollama local), openai e custom.
# Só muda quem embeda a PERGUNTA; os vetores dos documentos são os do índice e
# não são reembedados por causa disso. A chave do cache de query inclui endpoint
# e modelo, então vetores de um provedor não valem para o outro.
EMBEDDING_PROVIDER = _env("EMBEDDING_PROVIDER", "ecoa").lower()
# Consulta somente: não muda o encoder nem regrava vetores do índice.
QUERY_EMBEDDING_TIMEOUT = max(0.1, float(_env("QUERY_EMBEDDING_TIMEOUT", "8")))
QUERY_EMBEDDING_COOLDOWN = max(0.0, float(_env("QUERY_EMBEDDING_COOLDOWN", "30")))
DENSE_RETRIEVAL_ENABLED = _env("DENSE_RETRIEVAL_ENABLED", "true").lower() in {"1", "true", "sim", "yes"}
# Aviso de conferência na origem; nunca remove/corrige valores do snapshot.
VALUE_REVIEW_THRESHOLD = max(1.0, float(_env("VALUE_REVIEW_THRESHOLD", "1000000000")))


def embedding_endpoint(provider: str | None = None) -> tuple[str, str, str]:
    """``(base_url, api_key, model)`` do provedor pedido ou do configurado."""
    nome = (provider or EMBEDDING_PROVIDER).lower()
    if nome == "shared":
        nome = "ecoa"
    presets = {
        "ecoa": (OLLAMA_URL, OLLAMA_API_KEY, EMBEDDING_MODEL),
        "ollama": (OLLAMA_LOCAL_URL, OLLAMA_LOCAL_API_KEY, OLLAMA_LOCAL_EMBEDDING_MODEL),
        "openai": (OPENAI_API_URL, OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL),
        "custom": (CUSTOM_API_URL, CUSTOM_API_KEY, CUSTOM_EMBEDDING_MODEL),
    }
    if nome not in presets:
        raise ValueError("EMBEDDING_PROVIDER deve ser ecoa, ollama, openai ou custom")
    return presets[nome]

# Parâmetros de rede e armazenamento usados pela coleta e ingestão.
REQUEST_TIMEOUT = float(_env("REQUEST_TIMEOUT", "30"))
REQUEST_ATTEMPTS = int(_env("REQUEST_ATTEMPTS", "3"))

# OCR de páginas de PDF sem texto (src/ocr.py). TESSERACT_CMD vazio procura o
# executável no PATH e no local padrão do instalador do Windows.
TESSERACT_CMD = _env("TESSERACT_CMD", "")
OCR_LANG = _env("OCR_LANG", "por")
OCR_DPI = int(_env("OCR_DPI", "300"))
RAW_DATA_DIR = ROOT_DIR / "data" / "raw" / "v1"
NORMALIZED_DATA_DIR = ROOT_DIR / "data" / "normalized"
INDEX_DIR = ROOT_DIR / "index"
# Manifesto de um bundle imutável de runtime. Vazio mantém o comportamento
# existente e deixa readiness analítico explicitamente não pronto.
SNAPSHOT_MANIFEST = _env("SNAPSHOT_MANIFEST", "")
# Base estruturada em SQL (src/structured_db.py). Vazio = SQLite local em
# data/structured/; preenchido = Postgres (ex.: Neon). Nunca versionar o valor.
DATABASE_URL = _env("DATABASE_URL", "")
LOGS_DIR = ROOT_DIR / "logs"
PIPELINE_LOGS_DIR = LOGS_DIR / "pipeline"
MAINTENANCE_LOGS_DIR = LOGS_DIR / "maintenance"
BACKUPS_DIR = ROOT_DIR / "data" / "backups"
TEST_TEMP_DIR = ROOT_DIR / ".tmp" / "tests"
# API HTTP (app/api.py): chave administrativa para consultar histórico e traces
# de outras sessões. Sem ela, os dois exigem X-Session-Id do dono. Nunca
# versionar o valor da chave.
RAG_API_KEY = _env("RAG_API_KEY", "")
# true = histórico geral (perguntas de todos) aberto sem chave, para uma demo
# que precise dele. Padrão fechado.
HISTORY_PUBLIC = _env("HISTORY_PUBLIC", "false").lower() in {"1", "true", "sim", "yes"}
# false = sem /docs, /redoc e /openapi.json: com o backend exposto pelo túnel,
# o esquema da API deixa de ficar público. Padrão ligado, para uso local.
API_DOCS = _env("API_DOCS", "true").lower() in {"1", "true", "sim", "yes"}
# Limite por cliente (app/rate_limit.py), em requisições por minuto; 0 desliga.
# Perguntas e feedback: cada pergunta pelo LLM ocupa o ECOA por 20 a 100 s.
# Leitura (histórico, trace, explorar): o site faz várias chamadas por tela.
RATE_LIMIT_QUERY_PER_MINUTE = int(_env("RATE_LIMIT_QUERY_PER_MINUTE", "10"))
RATE_LIMIT_READ_PER_MINUTE = int(_env("RATE_LIMIT_READ_PER_MINUTE", "120"))
# Cadeia de hashes no log JSONL (src/log_chain.py): cada linha guarda o hash da
# anterior. Com a chave, HMAC-SHA256; sem ela, SHA-256. Nunca versionar a chave.
LOG_CHAIN_ENABLED = _env("LOG_CHAIN_ENABLED", "true").lower() in {"1", "true", "sim", "yes"}
LOG_CHAIN_KEY = _env("LOG_CHAIN_KEY", "")
# Dias que o histórico (src/history_store.py) guarda cada consulta; 0 = sem expurgo.
HISTORY_RETENTION_DAYS = int(_env("HISTORY_RETENTION_DAYS", "30"))
# Daily pipeline files follow history retention by default; 0 keeps all files.
LOG_RETENTION_DAYS = int(_env("LOG_RETENTION_DAYS", str(HISTORY_RETENTION_DAYS)))
MAINTENANCE_RETENTION_DAYS = int(_env("MAINTENANCE_RETENTION_DAYS", "14"))
# Apenas o detalhamento das evidências; não altera o orçamento enviado ao LLM.
LOG_EVIDENCE_DETAIL = _env("LOG_EVIDENCE_DETAIL", "resumo").lower()
if LOG_EVIDENCE_DETAIL not in {"resumo", "completo"}:
    raise ValueError("LOG_EVIDENCE_DETAIL deve ser resumo ou completo")
