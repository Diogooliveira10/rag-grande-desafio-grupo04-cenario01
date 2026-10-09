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
OPENROUTER_MODEL_FALLBACK = "openrouter/free"
OPENROUTER_MAX_TOKENS = int(_env("OPENROUTER_MAX_TOKENS", "4096"))
LLM_PROVIDER_MODE = _env("LLM_PROVIDER_MODE", "auto")
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "xagi/qwen3-embedding:0.6b-q4_k_m")
# Orçamento do contexto da geração (src/generate.py). O Ollama da turma usa
# janela de 2048 tokens e corta o INÍCIO do prompt que passar disso, levando
# junto a instrução de responder em JSON. Os padrões deixam o prompt em torno
# de 1.500 tokens, com folga para a resposta.
GENERATION_CONTEXT_CHARS = int(_env("GENERATION_CONTEXT_CHARS", "3500"))
GENERATION_CHUNK_CHARS = int(_env("GENERATION_CHUNK_CHARS", "900"))
# Tempo máximo (s) de cada chamada de geração e quantas vezes tentar de novo.
# A geração normal leva de 20 a 90 s no servidor da turma.
GENERATION_TIMEOUT = float(_env("GENERATION_TIMEOUT", "180"))
GENERATION_MAX_RETRIES = int(_env("GENERATION_MAX_RETRIES", "1"))
# Rerank (src/rerank.py). "local" (padrão) = cross-encoder local (RERANK_MODEL
# aceita qualquer CrossEncoder do sentence-transformers); "ecoa" = endpoint
# /api/rerank do servidor da turma, com o local de reserva. Na ablação de
# 2026-10-07 o local acertou mais (docs/ABLACAO_RERANK.md).
RERANK_ENABLED = _env("RERANK_ENABLED", "true").lower() in {"1", "true", "sim", "yes"}
RERANK_PROVIDER = _env("RERANK_PROVIDER", "local").lower()
RERANK_URL = _env("RERANK_URL", "") or OLLAMA_URL.rstrip("/").removesuffix("/v1") + "/api/rerank"
RERANK_TIMEOUT = float(_env("RERANK_TIMEOUT", "30"))
RERANK_MODEL = _env("RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
RERANK_CANDIDATES = int(_env("RERANK_CANDIDATES", "10"))
RERANK_MAX_LENGTH = int(_env("RERANK_MAX_LENGTH", "512"))
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
# Dias que o histórico (src/history_store.py) guarda cada consulta; 0 = sem expurgo.
HISTORY_RETENTION_DAYS = int(_env("HISTORY_RETENTION_DAYS", "30"))
# Daily pipeline files follow history retention by default; 0 keeps all files.
LOG_RETENTION_DAYS = int(_env("LOG_RETENTION_DAYS", str(HISTORY_RETENTION_DAYS)))
MAINTENANCE_RETENTION_DAYS = int(_env("MAINTENANCE_RETENTION_DAYS", "14"))
# Apenas o detalhamento das evidências; não altera o orçamento enviado ao LLM.
LOG_EVIDENCE_DETAIL = _env("LOG_EVIDENCE_DETAIL", "resumo").lower()
if LOG_EVIDENCE_DETAIL not in {"resumo", "completo"}:
    raise ValueError("LOG_EVIDENCE_DETAIL deve ser resumo ou completo")
