"""Configurações compartilhadas do projeto: fachada dos nomes de sempre.

Os valores vêm de ``src/settings.py``, que monta uma vez a configuração tipada
(``CONFIGURACAO``) a partir do ambiente. Os nomes de ``_NOMES`` (``config.OLLAMA_URL``,
``config.RAG_API_KEY``...) continuam valendo para o código existente e para os
testes que os trocam. O porquê de cada valor está comentado em ``src/settings.py``.

Código novo recebe a configuração por parâmetro (por exemplo, ``ConfiguracaoBusca``)
e, quando ninguém passa nada, usa ``atual()``: a configuração tipada com os
valores deste módulo naquele momento, inclusive os trocados por um teste.

Os valores podem ser sobrescritos por variáveis no arquivo ``.env``.
Não coloque credenciais reais neste arquivo; use ``.env`` localmente.
"""

import os
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.settings import (ORCAMENTO_CONTEXTO_JANELA_CURTA, ORCAMENTO_CONTEXTO_OPENROUTER,  # noqa: F401
                          OPENROUTER_BASE_URL, Configuracao, Segredo, carregar)

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

CONFIGURACAO = carregar(os.environ, ROOT_DIR)

# Cada nome da fachada e os campos da configuração que ele preenche. O endpoint
# do ECOA serve ao LLM e aos embeddings, por isso aparece nos dois grupos.
_NOMES: dict[str, tuple[tuple[str, str], ...]] = {
    # LLM (geração, reformulação e análise da pergunta).
    "OLLAMA_URL": (("llm", "ollama_url"), ("embeddings", "ecoa_url")),
    "OLLAMA_API_KEY": (("llm", "ollama_api_key"), ("embeddings", "ecoa_api_key")),
    "GENERATION_MODEL": (("llm", "generation_model"),),
    "MODEL_MIN_INTERVAL_SECONDS": (("llm", "model_min_interval_seconds"),),
    "OPENROUTER_API_KEY": (("llm", "openrouter_api_key"),),
    "OPENROUTER_MODEL": (("llm", "openrouter_model"),),
    "OPENROUTER_MODEL_FALLBACK": (("llm", "openrouter_model_fallback"),),
    "OPENROUTER_MAX_TOKENS": (("llm", "openrouter_max_tokens"),),
    "OPENROUTER_TIMEOUT": (("llm", "openrouter_timeout"),),
    "LLM_PROVIDER_MODE": (("llm", "provider_mode"),),
    "GENERATION_CONTEXT_CHARS": (("llm", "generation_context_chars"),),
    "GENERATION_CHUNK_CHARS": (("llm", "generation_chunk_chars"),),
    "GENERATION_TIMEOUT": (("llm", "generation_timeout"),),
    "REFORMULACAO_TIMEOUT": (("llm", "reformulacao_timeout"),),
    "GENERATION_MAX_RETRIES": (("llm", "generation_max_retries"),),
    "LLM_TEMPERATURE": (("llm", "temperature"),),
    "GENERATION_REASONING_EFFORT": (("llm", "reasoning_effort"),),
    "SQL_APRESENTACAO_LLM": (("llm", "sql_apresentacao_llm"),),
    # Embeddings (documentos e consulta).
    "EMBEDDING_MODEL": (("embeddings", "model"),),
    "PAUSA_ENTRE_LOTES_EMBEDDING": (("embeddings", "pausa_entre_lotes"),),
    "OLLAMA_LOCAL_URL": (("embeddings", "ollama_local_url"),),
    "OLLAMA_LOCAL_API_KEY": (("embeddings", "ollama_local_api_key"),),
    "OLLAMA_LOCAL_EMBEDDING_MODEL": (("embeddings", "ollama_local_model"),),
    "OPENAI_API_URL": (("embeddings", "openai_url"),),
    "OPENAI_API_KEY": (("embeddings", "openai_api_key"),),
    "OPENAI_EMBEDDING_MODEL": (("embeddings", "openai_model"),),
    "CUSTOM_API_URL": (("embeddings", "custom_url"),),
    "CUSTOM_API_KEY": (("embeddings", "custom_api_key"),),
    "CUSTOM_EMBEDDING_MODEL": (("embeddings", "custom_model"),),
    "EMBEDDING_PROVIDER": (("embeddings", "provider"),),
    "QUERY_EMBEDDING_TIMEOUT": (("embeddings", "query_timeout"),),
    "QUERY_EMBEDDING_COOLDOWN": (("embeddings", "query_cooldown"),),
    "DENSE_RETRIEVAL_ENABLED": (("embeddings", "dense_retrieval_enabled"),),
    # Busca e rerank.
    "RERANK_ENABLED": (("busca", "rerank_enabled"),),
    "RERANK_PROVIDER": (("busca", "rerank_provider"),),
    "RERANK_URL": (("busca", "rerank_url"),),
    "RERANK_TIMEOUT": (("busca", "rerank_timeout"),),
    "RERANK_MODEL": (("busca", "rerank_model"),),
    "RERANK_CANDIDATES": (("busca", "rerank_candidates"),),
    "RETRIEVAL_MAX_PER_PURCHASE": (("busca", "max_per_purchase"),),
    "RERANK_MAX_LENGTH": (("busca", "rerank_max_length"),),
    "RERANK_PRELOAD": (("busca", "rerank_preload"),),
    "INDEX_PRELOAD": (("busca", "index_preload"),),
    "VALUE_REVIEW_THRESHOLD": (("busca", "value_review_threshold"),),
    # Coleta e ingestão.
    "REQUEST_TIMEOUT": (("coleta", "request_timeout"),),
    "REQUEST_ATTEMPTS": (("coleta", "request_attempts"),),
    "TESSERACT_CMD": (("coleta", "tesseract_cmd"),),
    "OCR_LANG": (("coleta", "ocr_lang"),),
    "OCR_DPI": (("coleta", "ocr_dpi"),),
    # Caminhos e dados.
    "RAW_DATA_DIR": (("caminhos", "raw_data_dir"),),
    "NORMALIZED_DATA_DIR": (("caminhos", "normalized_data_dir"),),
    "INDEX_DIR": (("caminhos", "index_dir"),),
    "SNAPSHOT_MANIFEST": (("caminhos", "snapshot_manifest"),),
    "DATABASE_URL": (("caminhos", "database_url"),),
    "LOGS_DIR": (("caminhos", "logs_dir"),),
    "PIPELINE_LOGS_DIR": (("caminhos", "pipeline_logs_dir"),),
    "MAINTENANCE_LOGS_DIR": (("caminhos", "maintenance_logs_dir"),),
    "BACKUPS_DIR": (("caminhos", "backups_dir"),),
    "TEST_TEMP_DIR": (("caminhos", "test_temp_dir"),),
    # API HTTP.
    "RAG_API_KEY": (("api", "rag_api_key"),),
    "HISTORY_PUBLIC": (("api", "history_public"),),
    "API_DOCS": (("api", "api_docs"),),
    "RATE_LIMIT_QUERY_PER_MINUTE": (("api", "rate_limit_query_per_minute"),),
    "RATE_LIMIT_READ_PER_MINUTE": (("api", "rate_limit_read_per_minute"),),
    # Log, cadeia de hashes e retenção.
    "LOG_CHAIN_ENABLED": (("log", "chain_enabled"),),
    "LOG_CHAIN_KEY": (("log", "chain_key"),),
    "HISTORY_RETENTION_DAYS": (("log", "history_retention_days"),),
    "LOG_RETENTION_DAYS": (("log", "log_retention_days"),),
    "MAINTENANCE_RETENTION_DAYS": (("log", "maintenance_retention_days"),),
    "LOG_EVIDENCE_DETAIL": (("log", "evidence_detail"),),
}


def _publico(valor: Any) -> Any:
    # A fachada entrega chaves como str comum, como antes; o Segredo fica na configuração.
    return str(valor) if isinstance(valor, Segredo) else valor


for _nome, ((_grupo, _campo), *_) in _NOMES.items():
    globals()[_nome] = _publico(getattr(getattr(CONFIGURACAO, _grupo), _campo))


def atual() -> Configuracao:
    """A configuração tipada com os valores deste módulo agora (inclusive os trocados por testes)."""
    modulo = globals()
    trocas: dict[str, dict[str, Any]] = {}
    for nome, destinos in _NOMES.items():
        for grupo, campo in destinos:
            trocas.setdefault(grupo, {})[campo] = modulo[nome]
    grupos = {}
    for grupo, valores in trocas.items():
        original = getattr(CONFIGURACAO, grupo)
        segredos = {f.name for f in fields(original) if isinstance(getattr(original, f.name), Segredo)}
        grupos[grupo] = replace(original, **{campo: Segredo(valor) if campo in segredos else valor
                                             for campo, valor in valores.items()})
    return replace(CONFIGURACAO, **grupos)


def embedding_endpoint(provider: str | None = None) -> tuple[str, str, str]:
    """``(base_url, api_key, model)`` do provedor pedido ou do configurado (regra em ``ConfiguracaoEmbeddings``)."""
    return atual().embeddings.endpoint(provider)
