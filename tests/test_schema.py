import pytest
from pydantic import ValidationError

from src.schema import (
    ChunkMetadata,
    ConfidenceLevel,
    Natureza,
    RAGResponse,
    RefusalReason,
    Sensibilidade,
    SourceRef,
)


def test_chunk_metadata_tem_campos_obrigatorios():
    meta = ChunkMetadata(
        source_file="contrato_001.json",
        chunk_id="c001",
        base_id="contratos_estruturado",
        doc_type="json",
    )
    assert meta.sensitivity == Sensibilidade.PUBLICO  # default
    assert meta.doc_type == "json"


def test_chunk_metadata_falha_sem_campo_obrigatorio():
    with pytest.raises(ValidationError):
        ChunkMetadata(chunk_id="c001", base_id="x", doc_type="pdf")  # falta source_file


def test_rag_response_valida_com_fontes():
    resp = RAGResponse(
        query="Qual o valor médio por quilo de arroz em 2025?",
        answer="R$ 4,20 em média, segundo 12 contratos.",
        bases_consultadas=[Natureza.AGREGADA, Natureza.ESTRUTURADA],
        sources_used=[SourceRef(chunk_id="c001", base_id="pca_agregado", source_file="pca_2025.json")],
        confidence_level=ConfidenceLevel.ALTA,
        is_refusal=False,
    )
    assert len(resp.sources_used) == 1


def test_rag_response_recusa_valida_sem_fontes():
    resp = RAGResponse(
        query="Esse contrato foi fraude?",
        answer="Não posso emitir juízo sobre a legalidade de um contrato específico.",
        bases_consultadas=[],
        sources_used=[],
        confidence_level=ConfidenceLevel.RECUSADO,
        is_refusal=True,
        refusal_reason=RefusalReason.JUIZO_LEGALIDADE,
    )
    assert resp.sources_used == []


def test_rag_response_recusa_com_fontes_e_invalida():
    with pytest.raises(ValidationError):
        RAGResponse(
            query="x", answer="y",
            bases_consultadas=[],
            sources_used=[SourceRef(chunk_id="c1", base_id="b1", source_file="f1")],
            confidence_level=ConfidenceLevel.RECUSADO,
            is_refusal=True,
            refusal_reason=RefusalReason.SEM_EVIDENCIA,
        )


def test_rag_response_recusa_sem_motivo_e_invalida():
    with pytest.raises(ValidationError):
        RAGResponse(
            query="x", answer="y",
            bases_consultadas=[], sources_used=[],
            confidence_level=ConfidenceLevel.RECUSADO,
            is_refusal=True,
        )


def test_confidence_recusado_fora_de_recusa_e_invalido():
    with pytest.raises(ValidationError):
        RAGResponse(
            query="x", answer="y",
            bases_consultadas=[Natureza.NORMATIVA], sources_used=[],
            confidence_level=ConfidenceLevel.RECUSADO,
            is_refusal=False,
        )
