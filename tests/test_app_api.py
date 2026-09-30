import pytest
from fastapi.testclient import TestClient

from app import api as api_module
from app.api import app
from src.history_store import HistoricoStore


client = TestClient(app)


@pytest.fixture(autouse=True)
def historico(tmp_path, monkeypatch) -> HistoricoStore:
    """Nenhum teste escreve no historico.sqlite real do repositorio."""
    store = HistoricoStore(db_path=tmp_path / "historico.sqlite")
    monkeypatch.setattr(api_module, "historico", store)
    return store


def _response(query: str) -> dict:
    return {
        "query": query,
        "answer": "Resposta fundamentada.",
        "bases_consultadas": ["normativa"],
        "sources_used": [
            {
                "chunk_id": "chunk-1",
                "base_id": "base-1",
                "source_file": "documento.pdf",
            }
        ],
        "confidence_level": "alta",
        "is_refusal": False,
        "refusal_reason": None,
    }


def _fake_responder(query: str, *, top_k: int, run_id=None, query_id=None):
    return _response(query)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_delega_ao_pipeline(monkeypatch) -> None:
    calls = []

    def fake_responder(query: str, *, top_k: int, run_id=None, query_id=None):
        calls.append((query, top_k))
        return _response(query)

    monkeypatch.setattr(api_module.pipeline, "responder", fake_responder)

    response = client.post(
        "/api/v1/query", json={"query": "  Qual e a norma?  ", "top_k": 8}
    )

    assert response.status_code == 200
    body = response.json()
    query_id = body.pop("query_id")
    assert query_id.startswith("q_")
    assert body == _response("Qual e a norma?")
    assert calls == [("Qual e a norma?", 8)]


def test_query_grava_no_historico(monkeypatch, historico) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)

    response = client.post("/api/v1/query", json={"query": "Qual e a norma?"})

    query_id = response.json()["query_id"]
    consulta = historico.obter_consulta(query_id)
    assert consulta is not None
    assert consulta["query"] == "Qual e a norma?"
    assert consulta["resposta"] == _response("Qual e a norma?")
    assert consulta["feedback"] == []


def test_query_rejeita_texto_vazio() -> None:
    response = client.post("/api/v1/query", json={"query": "   "})

    assert response.status_code == 422


def test_query_trata_erro_sem_expor_detalhes(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(api_module.pipeline, "responder", fail)

    response = client.post("/api/v1/query", json={"query": "Pergunta valida"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Nao foi possivel processar a consulta."}


def test_cors_permite_github_pages() -> None:
    response = client.options(
        "/api/v1/query",
        headers={
            "Origin": "https://etuarda.github.io",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://etuarda.github.io"


def test_feedback_recusa_query_id_desconhecido() -> None:
    response = client.post(
        "/api/v1/feedback", json={"query_id": "q_fantasma", "avaliacao": "positivo"}
    )

    assert response.status_code == 404


def test_feedback_aceita_avaliacao_de_uma_consulta_existente(monkeypatch, historico) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    query_id = client.post("/api/v1/query", json={"query": "Pergunta"}).json()["query_id"]

    response = client.post(
        "/api/v1/feedback",
        json={"query_id": query_id, "avaliacao": "negativo", "comentario": "Faltou fonte."},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "recebido"}
    [feedback] = historico.obter_consulta(query_id)["feedback"]
    assert feedback["avaliacao"] == "negativo"
    assert feedback["comentario"] == "Faltou fonte."


def test_feedback_rejeita_avaliacao_fora_do_vocabulario(monkeypatch) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    query_id = client.post("/api/v1/query", json={"query": "Pergunta"}).json()["query_id"]

    response = client.post(
        "/api/v1/feedback", json={"query_id": query_id, "avaliacao": "mais ou menos"}
    )

    assert response.status_code == 422


def test_history_retorna_consultas_recentes_com_feedback(monkeypatch) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    query_id = client.post("/api/v1/query", json={"query": "Pergunta 1"}).json()["query_id"]
    client.post("/api/v1/query", json={"query": "Pergunta 2"})
    client.post("/api/v1/feedback", json={"query_id": query_id, "avaliacao": "positivo"})

    response = client.get("/api/v1/history")

    assert response.status_code == 200
    itens = response.json()
    assert len(itens) == 2
    assert itens[0]["query"] == "Pergunta 2"  # mais recente primeiro
    mais_antiga = itens[1]
    assert mais_antiga["query_id"] == query_id
    [feedback] = mais_antiga["feedback"]
    assert feedback["avaliacao"] == "positivo"
    assert feedback["comentario"] is None


def test_history_respeita_o_parametro_limit(monkeypatch) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    for i in range(3):
        client.post("/api/v1/query", json={"query": f"Pergunta {i}"})

    response = client.get("/api/v1/history", params={"limit": 1})

    assert response.status_code == 200
    assert len(response.json()) == 1
