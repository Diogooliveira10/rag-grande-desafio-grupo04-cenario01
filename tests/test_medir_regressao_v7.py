"""Paridade do benchmark usando o núcleo existente, sem modelos reais."""
from pathlib import Path

from eval.ablacao_rerank import CasoRecuperacao
from eval import medir_regressao as regression
from src.retrieve import RetrievalResult


def test_hibrido_usa_mesmo_executor_e_configuracao_nos_dois_snapshots(monkeypatch):
    calls = []
    reranker = object()
    def retrieve(query, route, **kwargs):
        calls.append((query, route, kwargs))
        return [RetrievalResult(chunk_id="c1", content="software", metadata={}, score=1)]
    monkeypatch.setattr(regression, "retrieve", retrieve)
    monkeypatch.setattr(regression, "dense_retrieve", lambda *a, **k: (_ for _ in ()).throw(AssertionError("caminho paralelo")))
    case = CasoRecuperacao(id="1", pergunta="software", bases=["estruturada"], relevante_contem=["software"])
    report = regression.medir_regressao([case], referencia=regression.Alvo("v6", Path("v6.jsonl"), Path("v6")),
        novo=regression.Alvo("v7", Path("v7.jsonl"), Path("v7")), strategy="hybrid", reranker=reranker, fetch_k=50)
    assert len(calls) == 2
    for query, route, kwargs in calls:
        assert query == "software" and route == {"bases": ["estruturada"]}
        assert kwargs["reranker"] is reranker and kwargs["fetch_k"] == 50
        assert kwargs["embed_fn"] is None  # encoder/cache/instrução da produção
    assert report["empates"] == 1
    assert report["configuracao"]["strategy"] == "hybrid"
