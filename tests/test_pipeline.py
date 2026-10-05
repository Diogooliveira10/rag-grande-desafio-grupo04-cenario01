from __future__ import annotations

import json

from src import pipeline
from src.retrieve import RetrievalResult
from src.schema import Chunk, ChunkMetadata, Natureza, RefusalReason


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


def test_responder_preserva_resultado_sql_e_normativo_em_pergunta_multi_base(monkeypatch):
    pergunta = (
        "Quais regras se aplicam à participação de microempresas e quais contratações "
        "de material de escritório do corpus usam modalidades compatíveis com essas regras?"
    )
    normativa = RetrievalResult(
        chunk_id="norma:artigo-1",
        content="A regra exige tratamento favorecido para microempresas.",
        metadata={
            "source_file": "lei.html",
            "chunk_id": "norma:artigo-1",
            "base_id": "editais_normativo",
            "doc_type": "html",
            "natureza": "normativa",
        },
        score=1.0,
    )
    estruturada = Chunk(
        content="Pregão eletrônico | Material de escritório | órgão A",
        metadata=ChunkMetadata(
            source_file="sqlite",
            chunk_id="sql:contratacoes:listagem:abc:1",
            base_id="contratos_estruturado",
            doc_type="sql",
            natureza=Natureza.ESTRUTURADA,
        ),
    )
    seen = {}

    def retrieve_fake(query, route, **kwargs):
        seen["route"] = route.bases_selecionadas
        return [normativa]

    def structured_fake(query, filters, bases, **kwargs):
        seen["structured_bases"] = bases
        return [estruturada]

    def cliente_fake(mensagens):
        seen["prompt"] = mensagens[1]["content"]
        return json.dumps({
            "answer": "Resposta com evidências normativa e estruturada.",
            "is_refusal": False,
            "confidence_level": "alta",
            "sources_used": ["E1", "E2"],
        })

    monkeypatch.setattr(pipeline, "retrieve", retrieve_fake)
    monkeypatch.setattr(pipeline, "consultar_estruturado", structured_fake)

    resposta = pipeline.responder(pergunta, cliente=cliente_fake)

    assert set(seen["route"]) == {Natureza.NORMATIVA, Natureza.ESTRUTURADA}
    assert set(seen["structured_bases"]) == {Natureza.NORMATIVA, Natureza.ESTRUTURADA}
    assert "[evidence_id=E1 | base=contratos_estruturado" in seen["prompt"]
    assert "[evidence_id=E2 | base=editais_normativo" in seen["prompt"]
    assert {source["base_id"] for source in resposta["sources_used"]} == {
        "contratos_estruturado", "editais_normativo",
    }


def test_responder_nao_inventa_evidencia_de_base_sem_resultados(monkeypatch):
    pergunta = (
        "Quais regras se aplicam à participação de microempresas e quais contratações "
        "de material de escritório do corpus usam modalidades compatíveis com essas regras?"
    )
    normativa = RetrievalResult(
        chunk_id="norma:artigo-1",
        content="A regra exige tratamento favorecido para microempresas.",
        metadata={
            "source_file": "lei.html",
            "chunk_id": "norma:artigo-1",
            "base_id": "editais_normativo",
            "doc_type": "html",
            "natureza": "normativa",
        },
        score=1.0,
    )
    seen = {}

    monkeypatch.setattr(pipeline, "retrieve", lambda *args, **kwargs: [normativa])
    monkeypatch.setattr(pipeline, "consultar_estruturado", lambda *args, **kwargs: [])

    def cliente_fake(mensagens):
        seen["prompt"] = mensagens[1]["content"]
        return json.dumps({
            "answer": "A evidência normativa está disponível.",
            "is_refusal": False,
            "confidence_level": "baixa",
            "sources_used": ["E1"],
        })

    resposta = pipeline.responder(pergunta, cliente=cliente_fake)

    assert "[evidence_id=E1 | base=editais_normativo" in seen["prompt"]
    assert "base=contratos_estruturado" not in seen["prompt"]
    assert [source["base_id"] for source in resposta["sources_used"]] == ["editais_normativo"]
