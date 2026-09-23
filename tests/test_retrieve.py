import json
from pathlib import Path

import pytest

from src.retrieve import (
    RetrievalResult,
    bm25_retrieve,
    dense_retrieve,
    reciprocal_rank_fusion,
    retrieve,
)
from src.schema import Natureza


def _record(
    chunk_id: str,
    *,
    text: str,
    base_id: str = "contratos_estruturado",
    municipio: str = "rio",
    embedding: list[float] | None = None,
    metadata_extra: dict | None = None,
):
    metadata = {
        "source_file": f"{chunk_id}.json",
        "chunk_id": chunk_id,
        "base_id": base_id,
        "doc_type": "json",
        "sensitivity": "publico",
        "municipio": municipio,
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    record = {
        "chunk_id": chunk_id,
        "document_id": f"doc-{chunk_id}",
        "base_id": base_id,
        "text": text,
        "source_path": f"/tmp/{chunk_id}.json",
        "metadata": metadata,
    }
    if embedding is not None:
        record["embedding"] = embedding
    return record


def test_empty_query_short_circuits_without_embedding():
    called = {"texts": []}

    def embed_fn(texts):
        called["texts"].extend(texts)
        return [[1.0, 0.0] for _ in texts]

    records = [_record("c1", text="contrato de ambulancia")]

    assert dense_retrieve("", records=records, embed_fn=embed_fn) == []
    assert dense_retrieve("   ", records=records, embed_fn=embed_fn) == []
    assert bm25_retrieve("", records=records) == []
    assert bm25_retrieve("   ", records=records) == []
    assert called["texts"] == []


def test_dense_retrieve_ranks_by_cosine_and_preserves_metadata():
    records = [
        _record("c1", text="contrato de ambulancia", embedding=[1.0, 0.0], municipio="rio"),
        _record("c2", text="contrato de software", embedding=[0.0, 1.0], municipio="niteroi"),
    ]

    def embed_fn(texts):
        return [[1.0, 0.0] for _ in texts]

    results = dense_retrieve("ambulancia", records=records, embed_fn=embed_fn, top_k=2)

    assert [item.chunk_id for item in results] == ["c1", "c2"]
    assert results[0].metadata["municipio"] == "rio"
    assert results[0].content == "contrato de ambulancia"
    assert results[0].score == pytest.approx(1.0)
    assert results[1].score == pytest.approx(0.0)


def test_dense_retrieve_returns_empty_for_empty_index_without_embedding():
    called = {"count": 0}

    def embed_fn(texts):
        called["count"] += 1
        return [[1.0, 0.0] for _ in texts]

    assert dense_retrieve("ambulancia", records=[], embed_fn=embed_fn) == []
    assert called["count"] == 0


def test_dense_retrieve_treats_incompatible_embedding_dimension_as_zero_score():
    records = [_record("c1", text="ambulancia", embedding=[1.0, 0.0, 0.0])]

    results = dense_retrieve(
        "ambulancia",
        records=records,
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
    )

    assert len(results) == 1
    assert results[0].score == pytest.approx(0.0)


def test_bm25_retrieve_ranks_relevant_terms_and_deduplicates_chunk_ids():
    records = [
        _record("c1", text="ambulancia ambulancia ambulancia emergencia", municipio="rio"),
        _record("c2", text="ambulancia para atendimento", municipio="rio"),
        _record("c3", text="contrato de software e licenciamento", municipio="rio"),
        _record("c1", text="duplicado sem relevancia", municipio="niteroi"),
    ]

    results = bm25_retrieve("ambulancia", records=records, top_k=5, filters={"municipio": "rio"})

    assert [item.chunk_id for item in results] == ["c1", "c2"]
    assert all(item.metadata["municipio"] == "rio" for item in results)
    assert results[0].score > results[1].score
    assert results[0].content.startswith("ambulancia")


def test_bm25_retrieve_is_independent_of_vector_index_and_missing_documents_file(tmp_path):
    records = [_record("c1", text="ambulancia pública")]

    results = bm25_retrieve(
        "ambulancia",
        index_dir=tmp_path / "index-inexistente",
        documents_file=tmp_path / "documents-inexistente.jsonl",
        records=records,
    )

    assert [item.chunk_id for item in results] == ["c1"]


def test_bm25_retrieve_ignores_empty_documents_and_non_matching_terms():
    records = [
        _record("empty", text=""),
        _record("other", text="contrato de software"),
    ]

    assert bm25_retrieve("ambulancia", records=records) == []


def test_filters_support_base_id_and_combined_metadata_conditions():
    records = [
        _record(
            "c1",
            text="ambulancia rio",
            base_id="contratos_estruturado",
            metadata_extra={"status": "ativo"},
        ),
        _record(
            "c2",
            text="ambulancia rio",
            base_id="editais_normativo",
            metadata_extra={"status": "ativo"},
        ),
        _record(
            "c3",
            text="ambulancia rio",
            base_id="contratos_estruturado",
            metadata_extra={"status": "encerrado"},
        ),
    ]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        bases=[Natureza.ESTRUTURADA],
        filters={"municipio": "rio", "status": "ativo"},
    )

    assert [item.chunk_id for item in results] == ["c1"]


@pytest.mark.parametrize(
    ("expected", "matching_ids"),
    [
        ({"municipio": {"operator": "==", "value": "rio"}}, {"c1"}),
        ({"municipio": ("!=", "rio")}, {"c2"}),
        ({"municipio": {"op": "in", "value": ["rio", "niteroi"]}}, {"c1", "c2"}),
        ({"municipio": ("contains", "i")}, {"c1", "c2"}),
    ],
)
def test_filters_support_equality_inequality_membership_and_contains(expected, matching_ids):
    records = [
        _record("c1", text="ambulancia", municipio="rio"),
        _record("c2", text="ambulancia", municipio="niteroi"),
    ]

    results = bm25_retrieve("ambulancia", records=records, filters=expected)

    assert {item.chunk_id for item in results} == matching_ids


@pytest.mark.parametrize(
    ("operator", "expected_ids"),
    [
        (">", {"c2", "c3"}),
        (">=", {"c1", "c2", "c3"}),
        ("<", set()),
        ("<=", {"c1"}),
    ],
)
def test_numeric_filters_compare_brazilian_and_plain_monetary_values(operator, expected_ids):
    records = [
        _record("c1", text="ambulancia", metadata_extra={"valor": "R$ 500.000,00"}),
        _record("c2", text="ambulancia", metadata_extra={"valor": "500000.01"}),
        _record("c3", text="ambulancia", metadata_extra={"valor": "not-a-number"}),
    ]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={"valor": (operator, "500000.00")},
    )

    assert {item.chunk_id for item in results} == expected_ids


@pytest.mark.parametrize(
    "filter_value",
    [
        "2025-03-31",
        "2025-03-31T10:30:00",
        "20250331",
    ],
)
def test_date_filters_normalize_supported_date_formats(filter_value):
    records = [
        _record("before", text="ambulancia", metadata_extra={"data": "2025-03-30"}),
        _record("same", text="ambulancia", metadata_extra={"data": "2025-03-31"}),
        _record("after", text="ambulancia", metadata_extra={"data": "2025-04-01"}),
    ]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={"data": (">=", filter_value)},
    )

    assert {item.chunk_id for item in results} == {"same", "after"}


def test_filters_read_values_from_extras_and_reject_invalid_operator():
    records = [
        _record(
            "c1",
            text="ambulancia",
            metadata_extra={"extras": {"modalidade": "pregao"}},
        ),
        _record(
            "c2",
            text="ambulancia",
            metadata_extra={"extras": {"modalidade": "concorrencia"}},
        ),
    ]

    assert [
        item.chunk_id
        for item in bm25_retrieve(
            "ambulancia",
            records=records,
            filters={"modalidade": ("==", "pregao")},
        )
    ] == ["c1"]
    assert bm25_retrieve(
        "ambulancia",
        records=records,
        filters={"modalidade": ("between", "pregao")},
    ) == []


def test_filters_exclude_missing_empty_and_unexpected_metadata_values():
    records = [
        {"chunk_id": "missing", "text": "ambulancia", "metadata": {}},
        {"chunk_id": "empty", "text": "ambulancia", "metadata": None},
        _record("wrong-type", text="ambulancia", metadata_extra={"valor": {"amount": 1}}),
    ]

    assert bm25_retrieve(
        "ambulancia",
        records=records,
        filters={"valor": (">", "0")},
    ) == []


def test_retrieve_uses_route_bases_and_filters_then_rrf_scores_the_fusion(tmp_path):
    base_dir = tmp_path / "contratos_estruturado"
    base_dir.mkdir()

    vectors = [
        {
            "chunk_id": "c1",
            "document_id": "doc-c1",
            "base_id": "contratos_estruturado",
            "embedding": [1.0, 0.0],
            "metadata": {
                "source_file": "c1.json",
                "chunk_id": "c1",
                "base_id": "contratos_estruturado",
                "doc_type": "json",
                "sensitivity": "publico",
                "municipio": "rio",
            },
        },
        {
            "chunk_id": "c2",
            "document_id": "doc-c2",
            "base_id": "contratos_estruturado",
            "embedding": [0.0, 1.0],
            "metadata": {
                "source_file": "c2.json",
                "chunk_id": "c2",
                "base_id": "contratos_estruturado",
                "doc_type": "json",
                "sensitivity": "publico",
                "municipio": "niteroi",
            },
        },
        {
            "chunk_id": "c3",
            "document_id": "doc-c3",
            "base_id": "contratos_estruturado",
            "embedding": [0.9, 0.1],
            "metadata": {
                "source_file": "c3.json",
                "chunk_id": "c3",
                "base_id": "contratos_estruturado",
                "doc_type": "json",
                "sensitivity": "publico",
                "municipio": "rio",
            },
        },
    ]
    (base_dir / "vectors.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in vectors) + "\n",
        encoding="utf-8",
    )

    documents_file = tmp_path / "documents.jsonl"
    documents_file.write_text(
        "\n".join(
            json.dumps(
                {
                    "chunk_id": record["chunk_id"],
                    "document_id": record["document_id"],
                    "base_id": record["base_id"],
                    "text": text,
                    "metadata": record["metadata"],
                },
                ensure_ascii=False,
            )
            for record, text in [
                (vectors[0], "ambulancia para atendimento de urgencia"),
                (vectors[1], "contrato de licenciamento de software"),
                (vectors[2], "ambulancia emergencial para o hospital"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    results = retrieve(
        "ambulancia",
        {"bases_selecionadas": [Natureza.ESTRUTURADA], "filters": {"municipio": "rio"}},
        index_dir=tmp_path,
        documents_file=documents_file,
        top_k=2,
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
    )

    assert [item.chunk_id for item in results] == ["c1", "c3"]
    assert all(item.metadata["municipio"] == "rio" for item in results)
    assert all(item.rrf_score is not None for item in results)
    assert all(item.dense_score is not None for item in results)
    assert all(item.sparse_score is not None for item in results)


def test_reciprocal_rank_fusion_keeps_dense_and_sparse_scores():
    dense = [
        RetrievalResult(
            chunk_id="d1",
            content="dense 1",
            metadata={"base_id": "contratos_estruturado"},
            score=0.9,
        ),
        RetrievalResult(
            chunk_id="d2",
            content="dense 2",
            metadata={"base_id": "contratos_estruturado"},
            score=0.3,
        ),
    ]
    sparse = [
        RetrievalResult(
            chunk_id="d2",
            content="sparse 2",
            metadata={"base_id": "contratos_estruturado"},
            score=0.8,
        ),
        RetrievalResult(
            chunk_id="d1",
            content="sparse 1",
            metadata={"base_id": "contratos_estruturado"},
            score=0.5,
        ),
    ]

    fused = reciprocal_rank_fusion([dense, sparse], k=60, top_k=2)

    assert [item.chunk_id for item in fused] == ["d1", "d2"]
    assert fused[0].rrf_score == pytest.approx(fused[1].rrf_score)
    assert fused[0].dense_score == pytest.approx(0.9)
    assert fused[0].sparse_score == pytest.approx(0.5)


def test_reciprocal_rank_fusion_combines_shared_and_exclusive_documents():
    dense = [
        RetrievalResult(chunk_id="shared", content="s", metadata={}, score=0.9),
        RetrievalResult(chunk_id="dense-only", content="d", metadata={}, score=0.8),
    ]
    sparse = [
        RetrievalResult(chunk_id="shared", content="s", metadata={}, score=0.7),
        RetrievalResult(chunk_id="sparse-only", content="p", metadata={}, score=0.6),
    ]

    fused = reciprocal_rank_fusion([dense, sparse], k=0)

    assert [item.chunk_id for item in fused] == ["shared", "dense-only", "sparse-only"]
    assert fused[0].score == pytest.approx(1 + 1)
    assert fused[1].score == pytest.approx(0.5)
    assert fused[2].score == pytest.approx(0.5)
    assert fused[1].dense_score == pytest.approx(0.8)
    assert fused[1].sparse_score is None
    assert fused[2].dense_score is None
    assert fused[2].sparse_score == pytest.approx(0.6)


def test_reciprocal_rank_fusion_rejects_negative_k():
    with pytest.raises(ValueError, match="k deve ser >= 0"):
        reciprocal_rank_fusion([], k=-1)


def test_retrieve_applies_optional_reranker_and_respects_top_k(tmp_path):
    records = [
        _record("c1", text="ambulancia", embedding=[1.0, 0.0]),
        _record("c2", text="ambulancia", embedding=[0.9, 0.1]),
    ]
    base_dir = tmp_path / "contratos_estruturado"
    base_dir.mkdir()
    (base_dir / "vectors.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    documents_file = tmp_path / "documents.jsonl"
    documents_file.write_text(
        "\n".join(
            json.dumps(
                {
                    "chunk_id": record["chunk_id"],
                    "document_id": record["document_id"],
                    "base_id": record["base_id"],
                    "text": record["text"],
                    "metadata": record["metadata"],
                },
                ensure_ascii=False,
            )
            for record in records
        )
        + "\n",
        encoding="utf-8",
    )
    reranker_calls = []

    def reranker(query, results):
        reranker_calls.append((query, results))
        return list(reversed(results))

    results = retrieve(
        "ambulancia",
        index_dir=tmp_path,
        documents_file=documents_file,
        top_k=2,
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        reranker=reranker,
    )

    assert len(results) == 2
    assert [item.chunk_id for item in results] == ["c2", "c1"]
    assert reranker_calls[0][0] == "ambulancia"


# --------------------------------------------------------------------------
# Seleção por natureza e escopo (manifesto)
# --------------------------------------------------------------------------


def test_natureza_seleciona_todas_as_bases_daquela_natureza():
    """Antes, cada natureza apontava para uma única base fixa."""
    records = [
        _record("c1", text="ambulancia", base_id="contratos_estruturado"),
        _record("c2", text="ambulancia", base_id="licitacoes_siga_csv"),
        _record("c3", text="ambulancia", base_id="editais_normativo"),
    ]

    results = bm25_retrieve("ambulancia", records=records, bases=[Natureza.ESTRUTURADA])

    assert {item.chunk_id for item in results} == {"c1", "c2"}


def test_natureza_do_proprio_registro_tem_prioridade_sobre_o_manifesto():
    records = [
        _record("c1", text="ata registro", base_id="base_nova", metadata_extra={"natureza": "conversacional"}),
        _record("c2", text="ata registro", base_id="base_nova", metadata_extra={"natureza": "normativa"}),
    ]

    results = bm25_retrieve("ata", records=records, bases=["conversacional"])

    assert [item.chunk_id for item in results] == ["c1"]


def test_base_id_explicito_continua_aceito_na_selecao():
    records = [
        _record("c1", text="ambulancia", base_id="contratos_estruturado"),
        _record("c2", text="ambulancia", base_id="licitacoes_siga_csv"),
    ]

    results = bm25_retrieve("ambulancia", records=records, bases=["licitacoes_siga_csv"])

    assert [item.chunk_id for item in results] == ["c2"]


def test_escopos_restringe_as_bases_oficiais_do_manifesto():
    records = [
        _record("c1", text="edital pregao", base_id="editais_normativo"),
        _record("c2", text="edital pregao", base_id="exemplos_teste"),
        _record("c3", text="edital pregao", base_id="documentos_pncp"),
        _record("c4", text="edital pregao", base_id="base_fora_do_manifesto"),
    ]

    oficiais = bm25_retrieve("edital", records=records, escopos=("oficial",))
    todas = bm25_retrieve("edital", records=records)

    assert [item.chunk_id for item in oficiais] == ["c1"]
    assert {item.chunk_id for item in todas} == {"c1", "c2", "c3", "c4"}


def test_manifesto_declara_uma_base_oficial_por_natureza():
    from src.retrieve import _manifest_bases

    oficiais = {
        base["natureza"]
        for base in _manifest_bases().values()
        if base.get("escopo") == "oficial"
    }

    assert oficiais == {item.value for item in Natureza}
