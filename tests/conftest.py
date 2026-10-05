import pytest

import config
from src import obs, structured_query


@pytest.fixture(autouse=True)
def _logs_em_diretorio_temporario(tmp_path, monkeypatch):
    """Nenhum teste escreve no logs/ real do repositório."""
    monkeypatch.setattr(obs.default_logger, "logs_dir", tmp_path / "logs")
    from src import retrieve
    monkeypatch.setattr(retrieve, "QUERY_CACHE_PATH", tmp_path / "query_cache.jsonl")
    from src import query_analysis
    monkeypatch.setattr(query_analysis, "ANALYSIS_MODE", "regras")
    monkeypatch.setattr(query_analysis, "ANALYSIS_CACHE", tmp_path / "analysis-cache.jsonl")
    monkeypatch.setattr(query_analysis.config, "MODEL_MIN_INTERVAL_SECONDS", 0)
    from src import conversation
    monkeypatch.setattr(conversation, "CONVERSATION_MODE", "regras")


@pytest.fixture(autouse=True)
def _sem_modelo_de_rerank(monkeypatch):
    """Nenhum teste baixa ou carrega o cross-encoder; quem testa rerank passa o seu."""
    monkeypatch.setattr(config, "RERANK_ENABLED", False)


@pytest.fixture(autouse=True)
def _base_estruturada_isolada(tmp_path, monkeypatch):
    """Nenhum teste lê o documents.jsonl real nem conecta ao Postgres do .env.

    Sem documents.jsonl, consultar_estruturado não roda; os testes da base
    estruturada passam banco e documents_file explicitamente.
    """
    monkeypatch.setattr(structured_query, "DOCUMENTS_FILE", tmp_path / "sem-documents.jsonl")
    monkeypatch.setattr(structured_query.structured_db.config, "DATABASE_URL", "")
