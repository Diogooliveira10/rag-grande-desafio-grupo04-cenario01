from __future__ import annotations

import json

from src import pipeline
from src.retrieve import RetrievalResult
from src.schema import RefusalReason


def test_responder_sem_bases_recusa_sem_chamar_retrieve_nem_llm(monkeypatch):
    """Roteador sem bases (ex.: juízo de legalidade) deve recusar sem tocar retrieve/LLM."""
    chamadas = {"retrieve": 0, "llm": 0}

    def retrieve_espiao(*args, **kwargs):
        chamadas["retrieve"] += 1
        return []

    def cliente_espiao(mensagens):
        chamadas["llm"] += 1
        return "{}"

    monkeypatch.setattr(pipeline, "retrieve", retrieve_espiao)

    resposta = pipeline.responder("Isso foi fraude?", cliente=cliente_espiao)

    assert chamadas["retrieve"] == 0
    assert chamadas["llm"] == 0
    assert resposta["is_refusal"] is True
    assert resposta["refusal_reason"] == RefusalReason.JUIZO_LEGALIDADE.value
    assert resposta["bases_consultadas"] == []


def test_responder_converte_retrieval_result_em_chunk_e_gera_resposta(monkeypatch):
    """RetrievalResult (retrieve.py) precisa virar Chunk (schema.py) para generate.py aceitar."""
    resultado = RetrievalResult(
        chunk_id="c1",
        content="texto do chunk",
        metadata={
            "source_file": "arquivo.json",
            "chunk_id": "c1",
            "base_id": "contratos_estruturado",
            "doc_type": "json",
            "natureza": "estruturada",
        },
        score=1.0,
    )

    def retrieve_fake(query, route, **kwargs):
        return [resultado]

    def cliente_fake(mensagens):
        return json.dumps(
            {
                "answer": "resposta",
                "is_refusal": False,
                "confidence_level": "alta",
                "sources_used": ["c1"],
            }
        )

    monkeypatch.setattr(pipeline, "retrieve", retrieve_fake)

    resposta = pipeline.responder(
        "Quais contratos foram assinados acima de R$ 500 mil?", cliente=cliente_fake
    )

    assert resposta["is_refusal"] is False
    assert resposta["confidence_level"] == "alta"
    assert resposta["bases_consultadas"] == ["estruturada"]
    assert resposta["sources_used"] == [
        {"chunk_id": "c1", "base_id": "contratos_estruturado", "source_file": "arquivo.json"}
    ]
