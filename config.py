"""Configurações compartilhadas do projeto: fachada dos nomes de sempre.

Os valores vêm de ``src/settings.py``, que monta uma vez a configuração tipada
(``CONFIGURACAO``) a partir do ambiente. Os nomes abaixo (``config.OLLAMA_URL``,
``config.RAG_API_KEY``...) continuam valendo para o código existente e para os
testes que os trocam. Código novo recebe ``CONFIGURACAO`` ou a parte dela de
que precisa. O porquê de cada valor está comentado em ``src/settings.py``.

Os valores podem ser sobrescritos por variáveis no arquivo ``.env``.
Não coloque credenciais reais neste arquivo; use ``.env`` localmente.
"""

import os
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

from src.settings import (ORCAMENTO_CONTEXTO_JANELA_CURTA, ORCAMENTO_CONTEXTO_OPENROUTER,  # noqa: F401
                          OPENROUTER_BASE_URL, carregar)

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

CONFIGURACAO = carregar(os.environ, ROOT_DIR)
_llm, _embeddings, _busca = CONFIGURACAO.llm, CONFIGURACAO.embeddings, CONFIGURACAO.busca
_coleta, _caminhos, _api, _log = CONFIGURACAO.coleta, CONFIGURACAO.caminhos, CONFIGURACAO.api, CONFIGURACAO.log

# LLM (geração, reformulação e análise da pergunta).
OLLAMA_URL = _llm.ollama_url
OLLAMA_API_KEY = str(_llm.ollama_api_key)
GENERATION_MODEL = _llm.generation_model
MODEL_MIN_INTERVAL_SECONDS = _llm.model_min_interval_seconds
OPENROUTER_API_KEY = str(_llm.openrouter_api_key)
OPENROUTER_MODEL = _llm.openrouter_model
OPENROUTER_MODEL_FALLBACK = _llm.openrouter_model_fallback
OPENROUTER_MAX_TOKENS = _llm.openrouter_max_tokens
OPENROUTER_TIMEOUT = _llm.openrouter_timeout
LLM_PROVIDER_MODE = _llm.provider_mode
GENERATION_CONTEXT_CHARS = _llm.generation_context_chars
GENERATION_CHUNK_CHARS = _llm.generation_chunk_chars
GENERATION_TIMEOUT = _llm.generation_timeout
REFORMULACAO_TIMEOUT = _llm.reformulacao_timeout
GENERATION_MAX_RETRIES = _llm.generation_max_retries
LLM_TEMPERATURE = _llm.temperature
GENERATION_REASONING_EFFORT = _llm.reasoning_effort
SQL_APRESENTACAO_LLM = _llm.sql_apresentacao_llm

# Embeddings (documentos e consulta).
EMBEDDING_MODEL = _embeddings.model
PAUSA_ENTRE_LOTES_EMBEDDING = _embeddings.pausa_entre_lotes
OLLAMA_LOCAL_URL = _embeddings.ollama_local_url
OLLAMA_LOCAL_API_KEY = str(_embeddings.ollama_local_api_key)
OLLAMA_LOCAL_EMBEDDING_MODEL = _embeddings.ollama_local_model
OPENAI_API_URL = _embeddings.openai_url
OPENAI_API_KEY = str(_embeddings.openai_api_key)
OPENAI_EMBEDDING_MODEL = _embeddings.openai_model
CUSTOM_API_URL = _embeddings.custom_url
CUSTOM_API_KEY = str(_embeddings.custom_api_key)
CUSTOM_EMBEDDING_MODEL = _embeddings.custom_model
EMBEDDING_PROVIDER = _embeddings.provider
QUERY_EMBEDDING_TIMEOUT = _embeddings.query_timeout
QUERY_EMBEDDING_COOLDOWN = _embeddings.query_cooldown
DENSE_RETRIEVAL_ENABLED = _embeddings.dense_retrieval_enabled

# Busca e rerank.
RERANK_ENABLED = _busca.rerank_enabled
RERANK_PROVIDER = _busca.rerank_provider
RERANK_URL = _busca.rerank_url
RERANK_TIMEOUT = _busca.rerank_timeout
RERANK_MODEL = _busca.rerank_model
RERANK_CANDIDATES = _busca.rerank_candidates
RETRIEVAL_MAX_PER_PURCHASE = _busca.max_per_purchase
RERANK_MAX_LENGTH = _busca.rerank_max_length
RERANK_PRELOAD = _busca.rerank_preload
INDEX_PRELOAD = _busca.index_preload
VALUE_REVIEW_THRESHOLD = _busca.value_review_threshold

# Coleta e ingestão.
REQUEST_TIMEOUT = _coleta.request_timeout
REQUEST_ATTEMPTS = _coleta.request_attempts
TESSERACT_CMD = _coleta.tesseract_cmd
OCR_LANG = _coleta.ocr_lang
OCR_DPI = _coleta.ocr_dpi

# Caminhos e dados.
RAW_DATA_DIR = _caminhos.raw_data_dir
NORMALIZED_DATA_DIR = _caminhos.normalized_data_dir
INDEX_DIR = _caminhos.index_dir
SNAPSHOT_MANIFEST = _caminhos.snapshot_manifest
DATABASE_URL = str(_caminhos.database_url)
LOGS_DIR = _caminhos.logs_dir
PIPELINE_LOGS_DIR = _caminhos.pipeline_logs_dir
MAINTENANCE_LOGS_DIR = _caminhos.maintenance_logs_dir
BACKUPS_DIR = _caminhos.backups_dir
TEST_TEMP_DIR = _caminhos.test_temp_dir

# API HTTP.
RAG_API_KEY = str(_api.rag_api_key)
HISTORY_PUBLIC = _api.history_public
API_DOCS = _api.api_docs
RATE_LIMIT_QUERY_PER_MINUTE = _api.rate_limit_query_per_minute
RATE_LIMIT_READ_PER_MINUTE = _api.rate_limit_read_per_minute

# Log, cadeia de hashes e retenção.
LOG_CHAIN_ENABLED = _log.chain_enabled
LOG_CHAIN_KEY = str(_log.chain_key)
HISTORY_RETENTION_DAYS = _log.history_retention_days
LOG_RETENTION_DAYS = _log.log_retention_days
MAINTENANCE_RETENTION_DAYS = _log.maintenance_retention_days
LOG_EVIDENCE_DETAIL = _log.evidence_detail


def embedding_endpoint(provider: str | None = None) -> tuple[str, str, str]:
    """``(base_url, api_key, model)`` do provedor pedido ou do configurado.

    Lê os nomes deste módulo, e não ``CONFIGURACAO``: os testes trocam
    ``config.OLLAMA_LOCAL_URL`` e esperam ver a troca aqui. A regra dos presets
    é a de ``ConfiguracaoEmbeddings.endpoint``.
    """
    atual = replace(
        _embeddings,
        ecoa_url=OLLAMA_URL, ecoa_api_key=OLLAMA_API_KEY, model=EMBEDDING_MODEL, provider=EMBEDDING_PROVIDER,
        ollama_local_url=OLLAMA_LOCAL_URL, ollama_local_api_key=OLLAMA_LOCAL_API_KEY,
        ollama_local_model=OLLAMA_LOCAL_EMBEDDING_MODEL,
        openai_url=OPENAI_API_URL, openai_api_key=OPENAI_API_KEY, openai_model=OPENAI_EMBEDDING_MODEL,
        custom_url=CUSTOM_API_URL, custom_api_key=CUSTOM_API_KEY, custom_model=CUSTOM_EMBEDDING_MODEL,
    )
    return atual.endpoint(provider)
