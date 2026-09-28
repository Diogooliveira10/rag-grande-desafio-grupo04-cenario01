"""Testes de fiação: query_analysis -> retrieve dentro de src.pipeline.responder.

Não testam retrieve.py nem generate.py de verdade (isso é responsabilidade dos
testes deles) — só garantem que responder() chama analisar_query() e repassa
os filtros extraídos pro retrieve(), e que uma recusa do roteador continua
pulando a recuperação (comportamento existente, não deve quebrar).
"""
from __future__ import annotations

from src import pipeline
from src.schema import (
    Chunk,
    ChunkMetadata,
    ConfidenceLevel,
    Natureza,
    RAGResponse,
    RouteDecision,
)


def _resposta_dummy(bases_consultadas):
    return RAGResponse(
        query="q",
        answer="ok",
        bases_consultadas=bases_consultadas,
        confidence_level=ConfidenceLevel.ALTA,
    )


def test_responder_passa_filtros_extraidos_para_retrieve(monkeypatch):
    chamadas_retrieve = {}

    def fake_rotear(query, *, run_id, query_id):
        return RouteDecision(
            bases_selecionadas=[Natureza.ESTRUTURADA],
            justificativa="teste",
            confidence=0.9,
        )

    def fake_retrieve(query, route, *, top_k, filters, reranker, escopos, run_id, query_id):
        chamadas_retrieve["filters"] = filters
        metadata = ChunkMetadata(
            source_file="f.json", chunk_id="c1", base_id="contratos_estruturado", doc_type="estruturada"
        )
        return [type("R", (), {"content": "texto", "metadata": metadata.model_dump()})()]

    def fake_gerar_resposta(query, chunks, bases_consultadas, *, cliente, run_id, query_id, refusal_reason_hint):
        chamadas_retrieve["refusal_reason_hint"] = refusal_reason_hint
        chamadas_retrieve["num_chunks"] = len(chunks)
        return _resposta_dummy(bases_consultadas)

    monkeypatch.setattr(pipeline, "rotear_pergunta", fake_rotear)
    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "gerar_resposta", fake_gerar_resposta)

    resultado = pipeline.responder("contratos da Secretaria de Saude acima de 100 mil em 2023")

    filtros = chamadas_retrieve["filters"]
    assert filtros["orgao"] == {"operator": "contains", "value": "Secretaria de Saude", "optional": True}
    assert filtros["valor"] == {"operator": ">=", "value": 100_000.0, "optional": True}
    assert "data_vigencia" in filtros
    assert chamadas_retrieve["num_chunks"] == 1
    assert resultado["answer"] == "ok"


def test_responder_sem_filtro_nenhum_passa_dict_vazio(monkeypatch):
    chamadas_retrieve = {}

    def fake_rotear(query, *, run_id, query_id):
        return RouteDecision(
            bases_selecionadas=[Natureza.ESTRUTURADA], justificativa="teste", confidence=0.9
        )

    def fake_retrieve(query, route, *, top_k, filters, reranker, escopos, run_id, query_id):
        chamadas_retrieve["filters"] = filters
        return []

    def fake_gerar_resposta(query, chunks, bases_consultadas, *, cliente, run_id, query_id, refusal_reason_hint):
        return _resposta_dummy(bases_consultadas)

    monkeypatch.setattr(pipeline, "rotear_pergunta", fake_rotear)
    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "gerar_resposta", fake_gerar_resposta)

    pipeline.responder("como funciona o pregao eletronico?")

    assert chamadas_retrieve["filters"] == {}


def test_responder_recusa_do_roteador_nao_chama_retrieve(monkeypatch):
    chamado = {"retrieve": False}

    def fake_rotear(query, *, run_id, query_id):
        return RouteDecision(bases_selecionadas=[], justificativa="dados bancarios", confidence=0.98)

    def fake_retrieve(*args, **kwargs):
        chamado["retrieve"] = True
        return []

    def fake_gerar_resposta(query, chunks, bases_consultadas, *, cliente, run_id, query_id, refusal_reason_hint):
        chamado["refusal_reason_hint"] = refusal_reason_hint
        return _resposta_dummy(bases_consultadas)

    monkeypatch.setattr(pipeline, "rotear_pergunta", fake_rotear)
    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "gerar_resposta", fake_gerar_resposta)

    pipeline.responder("quais dados bancarios do fornecedor X?")

    assert chamado["retrieve"] is False