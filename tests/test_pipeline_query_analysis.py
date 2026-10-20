"""Testes de fiação: query_analysis -> retrieve dentro de src.pipeline.responder.

Não testam retrieve.py nem generate.py de verdade (isso é responsabilidade dos
testes deles) — só garantem que responder() chama analisar_query() e repassa
os filtros extraídos pro retrieve(), e que uma recusa do roteador continua
pulando a recuperação (comportamento existente, não deve quebrar).
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from src import pipeline
from src.schema import (
    Chunk,
    ChunkMetadata,
    ConfidenceLevel,
    Natureza,
    QueryAnalysisResult,
    QueryFilters,
    QueryIntent,
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

    def fake_rotear(query, *, run_id, query_id, analise=None):
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
    assert filtros["orgao"] == {"operator": "contains", "value": "Secretaria de Saude", "optional": False}
    assert filtros["valor"] == {"operator": ">", "value": 100_000.0, "optional": False}
    assert "data_assinatura" in filtros
    assert filtros["data_assinatura"][0]["value"].isoformat() == "2023-01-01"
    assert chamadas_retrieve["num_chunks"] == 1
    assert resultado["answer"] == "ok"


def test_responder_sem_filtro_nenhum_passa_dict_vazio(monkeypatch):
    chamadas_retrieve = {}

    def fake_rotear(query, *, run_id, query_id, analise=None):
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


@pytest.mark.parametrize("com_intencoes", [False, True], ids=["regras", "intencoes"])
@pytest.mark.parametrize("modo_rerank", ["padrao", "explicito", "desligado"])
def test_responder_resolve_reranker_antes_de_recuperar(monkeypatch, com_intencoes, modo_rerank):
    query = "Quais editais e atas estão disponíveis?"
    intencoes = [
        QueryIntent(pergunta="Quais editais estão disponíveis?", bases=[Natureza.NORMATIVA]),
        QueryIntent(pergunta="Quais atas estão disponíveis?", bases=[Natureza.CONVERSACIONAL]),
    ] if com_intencoes else []
    analise = QueryAnalysisResult(
        query_original=query, filtros=QueryFilters(), intencoes=intencoes,
        metodo="llm" if com_intencoes else "regras",
    )
    decisao = RouteDecision(
        bases_selecionadas=[Natureza.NORMATIVA, Natureza.CONVERSACIONAL],
        justificativa="teste", confidence=0.9,
    )
    padrao = Mock(return_value=[])
    explicito = Mock(return_value=[])
    resolver = Mock(return_value=None if modo_rerank == "desligado" else padrao)
    recuperar = Mock(return_value=[])
    monkeypatch.setattr(pipeline, "resposta_conversacional", Mock(return_value=None))
    monkeypatch.setattr(pipeline, "analisar_query", Mock(return_value=analise))
    monkeypatch.setattr(pipeline, "rotear_pergunta", Mock(return_value=decisao))
    monkeypatch.setattr(pipeline, "reranker_padrao", resolver)
    monkeypatch.setattr(pipeline, "retrieve", recuperar)
    monkeypatch.setattr(pipeline, "consultar_estruturado", Mock(return_value=[]))
    monkeypatch.setattr(pipeline, "gerar_resposta", Mock(return_value=_resposta_dummy(decisao.bases_selecionadas)))

    pipeline.responder(query, reranker=explicito if modo_rerank == "explicito" else None)

    esperado = {"padrao": padrao, "explicito": explicito, "desligado": None}[modo_rerank]
    assert recuperar.call_count == (2 if com_intencoes else 1)
    for chamada in recuperar.call_args_list:
        assert chamada.kwargs["reranker"] is esperado
    if modo_rerank == "explicito":
        resolver.assert_not_called()
    else:
        resolver.assert_called_once_with()


def test_responder_recusa_do_roteador_nao_chama_retrieve(monkeypatch):
    chamado = {"retrieve": False}
    resolver = Mock()
    monkeypatch.setattr(pipeline, "reranker_padrao", resolver)

    def fake_rotear(query, *, run_id, query_id, analise=None):
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
    resolver.assert_not_called()
