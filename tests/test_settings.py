"""Configuração tipada (src/settings.py) e a fachada config.py (2026-10-30).

``carregar`` é pura: recebe as variáveis num dicionário, sem ler o ``.env`` nem
o ``os.environ``. A fachada ``config.py`` expõe os mesmos nomes de antes.
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import config
from src.settings import Segredo, carregar

RAIZ = Path("/projeto")


def test_sem_variaveis_valem_os_padroes():
    c = carregar({}, RAIZ)

    assert c.llm.generation_model == "qcwind/qwen3-8b-instruct-Q4-K-M"
    assert c.llm.generation_context_chars == 3500
    assert c.llm.reformulacao_timeout == 60.0 and c.llm.temperature == 0.0
    assert c.embeddings.provider == "ecoa"
    assert c.busca.rerank_url == "http://ecoa-llm.duckdns.org:11434/api/rerank"
    assert c.busca.max_per_purchase == 2
    assert c.log.log_retention_days == c.log.history_retention_days == 30
    assert c.caminhos.pipeline_logs_dir == RAIZ / "logs" / "pipeline"
    assert c.api.rag_api_key == "" and c.caminhos.snapshot_manifest == ""


def test_valores_derivados_seguem_as_outras_variaveis():
    c = carregar({"LLM_PROVIDER_MODE": "openrouter", "OLLAMA_URL": "http://servidor:1/v1/",
                  "HISTORY_RETENTION_DAYS": "7", "EMBEDDING_MODEL": "modelo-x"}, RAIZ)

    assert c.llm.generation_context_chars == 12000  # janela grande do OpenRouter
    assert c.busca.rerank_url == "http://servidor:1/api/rerank"
    assert c.log.log_retention_days == 7
    assert c.embeddings.ollama_local_model == c.embeddings.custom_model == "modelo-x"


def test_leitura_dos_tipos_e_dos_limites():
    c = carregar({"SQL_APRESENTACAO_LLM": " SIM ", "RERANK_ENABLED": "nao", "QUERY_EMBEDDING_TIMEOUT": "0",
                  "QUERY_EMBEDDING_COOLDOWN": "-3", "VALUE_REVIEW_THRESHOLD": "0.5", "RERANK_PROVIDER": "ECOA",
                  "LOG_EVIDENCE_DETAIL": "COMPLETO"}, RAIZ)

    assert c.llm.sql_apresentacao_llm is True and c.busca.rerank_enabled is False
    assert c.embeddings.query_timeout == 0.1 and c.embeddings.query_cooldown == 0.0
    assert c.busca.value_review_threshold == 1.0
    assert c.busca.rerank_provider == "ecoa" and c.log.evidence_detail == "completo"


def test_valor_invalido_para_na_carga():
    with pytest.raises(ValueError, match="LOG_EVIDENCE_DETAIL"):
        carregar({"LOG_EVIDENCE_DETAIL": "tudo"}, RAIZ)
    with pytest.raises(ValueError):
        carregar({"OCR_DPI": "alto"}, RAIZ)


def test_chaves_nao_aparecem_quando_a_configuracao_e_impressa():
    c = carregar({"RAG_API_KEY": "chave-secreta", "OPENROUTER_API_KEY": "sk-or-segredo",
                  "LOG_CHAIN_KEY": "hmac-segredo", "DATABASE_URL": "postgres://u:senha@h/d"}, RAIZ)

    texto = repr(c)
    for segredo in ("chave-secreta", "sk-or-segredo", "hmac-segredo", "senha"):
        assert segredo not in texto
    assert isinstance(c.api.rag_api_key, Segredo) and c.api.rag_api_key == "chave-secreta"
    assert repr(Segredo("")) == "Segredo('')"


def test_configuracao_e_imutavel():
    c = carregar({}, RAIZ)
    with pytest.raises(FrozenInstanceError):
        c.llm.generation_timeout = 1  # type: ignore[misc]


def test_endpoint_de_embedding_por_provedor():
    e = carregar({"EMBEDDING_PROVIDER": "OLLAMA", "OLLAMA_LOCAL_URL": "http://local/v1",
                  "OLLAMA_LOCAL_API_KEY": "chave-local"}, RAIZ).embeddings

    assert e.endpoint() == ("http://local/v1", "chave-local", e.ollama_local_model)
    assert e.endpoint("shared") == e.endpoint("ecoa")
    assert type(e.endpoint("ecoa")[1]) is str  # a chave sai como texto comum para o cliente HTTP
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        e.endpoint("inexistente")


def test_fachada_config_expoe_os_mesmos_valores_da_configuracao():
    c = config.CONFIGURACAO
    assert config.GENERATION_TIMEOUT == c.llm.generation_timeout
    assert config.RETRIEVAL_MAX_PER_PURCHASE == c.busca.max_per_purchase
    assert config.PIPELINE_LOGS_DIR == c.caminhos.pipeline_logs_dir
    # A fachada entrega as chaves como str comum, como antes.
    assert type(config.RAG_API_KEY) is str and type(config.LOG_CHAIN_KEY) is str
