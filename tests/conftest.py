from datetime import datetime
import json
from pathlib import Path
import shutil
import uuid
import warnings

import pytest

import config
from src import obs, structured_query


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Use a unique local temp folder unless --basetemp was explicit."""
    if config.option.basetemp is None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = Path(config.rootpath) / ".tmp" / "tests" / f"{stamp}-{uuid.uuid4().hex[:8]}"
        path.parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(path)
        config._managed_test_temp = path


def pytest_sessionfinish(session, exitstatus):
    """Successful runs leave no fixtures behind; failed runs get a marker."""
    path = getattr(session.config, "_managed_test_temp", None)
    if path is None or not path.exists() or path.is_symlink():
        return
    try:
        if exitstatus == 0:
            shutil.rmtree(path)
        else:
            (path / ".pytest-run.json").write_text(json.dumps({
                "completed": True, "exitstatus": int(exitstatus),
                "finished_at": datetime.now().astimezone().isoformat(),
            }) + "\n", encoding="utf-8")
    except OSError as error:
        warnings.warn(f"Could not finalize test temporary files: {error}", RuntimeWarning)


@pytest.fixture(autouse=True)
def _logs_em_diretorio_temporario(tmp_path, monkeypatch):
    """Nenhum teste escreve no logs/ real do repositório."""
    monkeypatch.setattr(obs.default_logger, "logs_dir", tmp_path / "logs")
    from src import retrieve
    monkeypatch.setattr(retrieve, "QUERY_CACHE_PATH", tmp_path / "query_cache.jsonl")
    retrieve._EMBEDDING_FAILURES.clear()
    from src import query_analysis
    monkeypatch.setattr(query_analysis, "ANALYSIS_MODE", "regras")
    monkeypatch.setattr(query_analysis, "ANALYSIS_CACHE", tmp_path / "analysis-cache.jsonl")
    monkeypatch.setattr(query_analysis.config, "MODEL_MIN_INTERVAL_SECONDS", 0)
    from src import conversation
    monkeypatch.setattr(conversation, "CONVERSATION_MODE", "regras")


@pytest.fixture(autouse=True)
def _historico_da_api_isolado(tmp_path, monkeypatch):
    """All API tests use disposable SQLite, including tests outside test_app_api."""
    from app import api
    from src.history_store import HistoricoStore
    monkeypatch.setattr(api, "historico", HistoricoStore(db_path=tmp_path / "api-history.sqlite"))


@pytest.fixture(autouse=True)
def _sem_limite_de_requisicoes(monkeypatch):
    """Os testes da API fazem dezenas de chamadas seguidas; quem testa o limite liga o seu."""
    from app import api
    from app.rate_limit import LimiteDeRequisicoes
    monkeypatch.setattr(api, "limite_de_consultas", LimiteDeRequisicoes(0))
    monkeypatch.setattr(api, "limite_de_leitura", LimiteDeRequisicoes(0))


@pytest.fixture(autouse=True)
def _cadeia_de_hashes_padrao(monkeypatch):
    """Cadeia ligada e sem a chave do .env, igual em todas as máquinas."""
    monkeypatch.setattr(config, "LOG_CHAIN_ENABLED", True)
    monkeypatch.setattr(config, "LOG_CHAIN_KEY", "")


@pytest.fixture(autouse=True)
def _chave_da_api_de_teste(monkeypatch):
    """Histórico e trace exigem X-API-Key; os testes usam esta chave, nunca a do .env."""
    monkeypatch.setattr(config, "RAG_API_KEY", "chave-de-teste")
    monkeypatch.setattr(config, "HISTORY_PUBLIC", False)


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
