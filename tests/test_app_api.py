from fastapi.testclient import TestClient

from app.api import app


client = TestClient(app)


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


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_delega_ao_pipeline(monkeypatch) -> None:
    calls = []

    def fake_responder(query: str, *, top_k: int):
        calls.append((query, top_k))
        return _response(query)

    monkeypatch.setattr("app.api.pipeline.responder", fake_responder)

    response = client.post(
        "/api/v1/query", json={"query": "  Qual e a norma?  ", "top_k": 8}
    )

    assert response.status_code == 200
    assert response.json() == _response("Qual e a norma?")
    assert calls == [("Qual e a norma?", 8)]


def test_query_rejeita_texto_vazio() -> None:
    response = client.post("/api/v1/query", json={"query": "   "})

    assert response.status_code == 422


def test_query_trata_erro_sem_expor_detalhes(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr("app.api.pipeline.responder", fail)

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
