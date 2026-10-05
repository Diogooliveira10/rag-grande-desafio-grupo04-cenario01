import json
from decimal import Decimal
from pathlib import Path

import pytest

import src.retrieve as retrieve_module
from src.pipeline import _chunk_from_retrieval
from src.retrieve import (
    RetrievalResult,
    _parse_number,
    _result,
    bm25_retrieve,
    dense_retrieve,
    reciprocal_rank_fusion,
    retrieve,
)
from src.obs import default_logger
from src.schema import Natureza


def test_bm25_normalized_flat_metadata_survives_pipeline_and_filters(tmp_path):
    record = {"chunk_id": "ata:1", "base_id": "atas_conversacional", "doc_type": "pdf",
              "source_file": "ata.pdf", "natureza": "conversacional", "text": "atraso de entrega",
              "inventory_version": "v5", "extras": {"ano_pca": 2025}}
    results = bm25_retrieve("atraso de entrega", records=[record],
                            bases=[Natureza.CONVERSACIONAL], filters={"ano_pca": 2025})
    assert len(results) == 1
    chunk = _chunk_from_retrieval(results[0])
    assert chunk.metadata.source_file == "ata.pdf"
    assert chunk.metadata.inventory_version == "v5"
    assert chunk.metadata.extras["ano_pca"] == 2025
    assert "text" not in results[0].metadata
    assert bm25_retrieve("atraso", records=[record], filters={"ano_pca": 2024}) == []


def test_optional_missing_fields_cannot_hide_empty_scoped_result():
    records = [{"chunk_id": "contract", "base_id": "contratos_estruturado", "extras": {"valor": 10}},
               {"chunk_id": "attachment", "base_id": "contratos_estruturado"}]
    selected, ignored = retrieve_module._filtrar_por_base(
        records, {"valor": {"operator": ">=", "value": 500000, "optional": True}})
    assert len(selected) == 2
    # O filtro reprova o único chunk que tem o campo: é ignorado na base, e o
    # anexo sem valor não fica sozinho escondendo o resultado.
    assert ignored == {"contratos_estruturado": {"nenhum_chunk_passou": ["valor"]}}


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


def test_result_preserves_nested_metadata():
    metadata = {
        "source_file": "arquivo.json",
        "chunk_id": "chunk-001",
        "base_id": "estruturada",
        "doc_type": "contrato",
    }

    result = _result(
        {"chunk_id": "chunk-001", "metadata": metadata},
        score=0.75,
        content="conteúdo do contrato",
    )

    assert result.metadata == metadata
    assert result.chunk_id == "chunk-001"
    assert result.content == "conteúdo do contrato"
    assert result.score == pytest.approx(0.75)


def test_result_reads_metadata_from_top_level_fields():
    metadata = {
        "source_file": "arquivo.json",
        "chunk_id": "chunk-001",
        "base_id": "estruturada",
        "doc_type": "contrato",
    }

    result = _result(
        {"chunk_id": "chunk-001", **metadata},
        score=0.75,
        content="conteúdo do contrato",
    )

    assert result.metadata == metadata
    assert result.chunk_id == "chunk-001"
    assert result.content == "conteúdo do contrato"
    assert result.score == pytest.approx(0.75)


def test_dense_retrieve_returns_empty_for_empty_index_without_embedding():
    called = {"count": 0}

    def embed_fn(texts):
        called["count"] += 1
        return [[1.0, 0.0] for _ in texts]

    assert dense_retrieve("ambulancia", records=[], embed_fn=embed_fn) == []
    assert called["count"] == 0


def test_retrieve_excludes_and_logs_incompatible_embedding_dimensions(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    records = [
        _record("invalid", text="software", embedding=[1.0, 0.0, 0.0]),
        _record("valid", text="ambulancia", embedding=[1.0, 0.0]),
    ]
    monkeypatch.setattr(retrieve_module, "_records_for", lambda *args, **kwargs: records)

    results = retrieve(
        "ambulancia",
        index_dir=tmp_path,
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        run_id="run-real",
        query_id="query-real",
    )

    assert [item.chunk_id for item in results] == ["valid"]
    assert results[0].dense_score == pytest.approx(1.0)
    logs = [
        json.loads(line)
        for line in default_logger.log_path().read_text(encoding="utf-8").splitlines()
    ]
    warning = next(log for log in logs if log["status"] == "aviso")
    assert warning["run_id"] == "run-real"
    assert warning["query_id"] == "query-real"
    assert warning["extra"]["embedding_dimension_mismatches"] == [
        {"chunk_id": "invalid", "expected_dimension": 2, "actual_dimension": 3}
    ]


def test_dense_retrieve_ignores_and_logs_null_embedding(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    missing = _record("missing", text="ambulancia")
    missing["embedding"] = None
    records = [
        missing,
        _record("valid", text="ambulancia", embedding=[1.0, 0.0]),
    ]

    results = dense_retrieve(
        "ambulancia",
        records=records,
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        run_id="run-null",
        query_id="query-null",
    )

    assert [item.chunk_id for item in results] == ["valid"]
    assert results[0].score == pytest.approx(1.0)
    log = json.loads(default_logger.log_path().read_text(encoding="utf-8").splitlines()[0])
    assert log["status"] == "aviso"
    assert log["run_id"] == "run-null"
    assert log["query_id"] == "query-null"
    assert "ausente(s)/nulo(s)" in log["output"]
    assert log["extra"]["missing_embeddings"] == [{"chunk_id": "missing"}]


def test_dense_retrieve_does_not_rank_unreturned_faiss_candidates_as_zero():
    records = [
        _record("c1", text="resultado menos próximo", embedding=[-1.0, 0.0]),
        _record("c2", text="resultado mais próximo", embedding=[-0.2, 0.9797959]),
    ]

    results = dense_retrieve(
        "consulta",
        records=records,
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        top_k=1,
    )

    assert [item.chunk_id for item in results] == ["c2"]
    assert results[0].score == pytest.approx(-0.2, abs=1e-6)


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


def test_bm25_retrieve_preserves_flat_metadata_through_rrf():
    records = [
        {
            "chunk_id": "chunk-001",
            "text": "contrato de tecnologia",
            "source_file": "arquivo.json",
            "base_id": "estruturada",
            "doc_type": "contrato",
        }
    ]

    results = bm25_retrieve("tecnologia", records=records)
    fused = reciprocal_rank_fusion([[], results])

    assert len(results) == 1
    assert results[0].metadata["source_file"] == "arquivo.json"
    assert results[0].metadata["chunk_id"] == "chunk-001"
    assert results[0].metadata["base_id"] == "estruturada"
    assert results[0].metadata["doc_type"] == "contrato"
    assert results[0].content == "contrato de tecnologia"
    assert len(fused) == 1
    assert fused[0].metadata == results[0].metadata


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


@pytest.mark.parametrize("valor_ilegivel", ["", "Sigiloso", "   ", {}])
def test_filtro_opcional_trata_valor_ilegivel_como_campo_ausente(valor_ilegivel):
    filtro = {"valor": {"operator": ">=", "value": 500_000, "optional": True}}
    assert retrieve_module._matches_filters({"extras": {"valor": valor_ilegivel}}, filtro)
    # Sem "optional", o comportamento estrito continua: valor ilegível não passa.
    assert not retrieve_module._matches_filters({"extras": {"valor": "Sigiloso"}}, {"valor": (">=", 500_000)})


def test_contains_ignora_caixa_e_acento():
    records = [
        _record("estado", text="ambulancia", metadata_extra={"orgao": "SECRETARIA DE ESTADO DE SAUDE"}),
        _record("outro", text="ambulancia", metadata_extra={"orgao": "SECRETARIA DE EDUCACAO"}),
    ]

    results = bm25_retrieve(
        "ambulancia", records=records, filters={"orgao": {"operator": "contains", "value": "Estado de Saúde"}}
    )

    assert [item.chunk_id for item in results] == ["estado"]


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
        (">", {"c2"}),
        (">=", {"c1", "c2"}),
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
    ("record_value", "filter_value"),
    [
        ("R$ 1.500", "1500"),
        ("1.234.567", "1234567"),
        ("500000", "500000"),
        ("R$ 1.500,50", "1500.50"),
        ("1.234.567,89", "1234567.89"),
        ("1500,50", "1500.50"),
        ("1500.50", "1500.50"),
    ],
)
def test_numeric_filters_parse_brazilian_thousands_and_decimal_separators(record_value, filter_value):
    records = [
        _record("match", text="ambulancia", metadata_extra={"valor": record_value}),
        _record("other", text="ambulancia", metadata_extra={"valor": "0"}),
    ]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={"valor": ("==", filter_value)},
    )

    assert [item.chunk_id for item in results] == ["match"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.125", "0.125"),
        ("0.500", "0.500"),
        ("1.500", "1500.0"),
        ("1.234.567", "1234567.0"),
        ("1500.50", "1500.50"),
        ("1500,50", "1500.50"),
    ],
)
def test_parse_number_distinguishes_thousands_and_decimal_separators(value, expected):
    assert _parse_number(value) == Decimal(expected)


@pytest.mark.parametrize(
    ("record_value", "filter_value"),
    [
        ("R$ 1.500", "100"),
        ("1.234.567", "1.000.000"),
    ],
)
def test_numeric_filters_include_values_above_threshold(record_value, filter_value):
    records = [_record("match", text="ambulancia", metadata_extra={"valor": record_value})]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={"valor": (">", filter_value)},
    )

    assert [item.chunk_id for item in results] == ["match"]


def test_numeric_filters_do_not_compare_invalid_values_lexically():
    records = [
        _record("invalid", text="ambulancia", metadata_extra={"valor": "not-a-number"}),
        _record("valid", text="ambulancia", metadata_extra={"valor": "2000"}),
    ]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={"valor": (">", "1000")},
    )

    assert [item.chunk_id for item in results] == ["valid"]


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


@pytest.mark.parametrize(
    "identificador",
    [
        "28741080000155-1-000001/2023-000001",
        "00394429000100-2-000183/2025",
    ],
)
def test_retrieve_registra_posicao_dense_bm25_rrf_e_promove_match_exato(
    identificador, tmp_path, monkeypatch
):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    distractors = [
        RetrievalResult(
            chunk_id=f"d{i:02d}",
            content=f"ata de registro de preços documento {i}",
            metadata={"base_id": "atas_conversacional", "source_file": f"d{i}.json"},
            score=1.0 - i / 100,
        )
        for i in range(8)
    ]
    alvo_id = "alvo-exato"
    alvo = RetrievalResult(
        chunk_id=alvo_id,
        content=f"registro PNCP {identificador}",
        metadata={
            "base_id": "atas_conversacional",
            "source_file": "ata.json",
            "extras": {"numero_controle_pncp": identificador},
        },
        score=0.01,
    )
    record = {
        "chunk_id": alvo_id,
        "base_id": "atas_conversacional",
        "text": alvo.content,
        "metadata": alvo.metadata,
    }
    monkeypatch.setattr(retrieve_module, "_records_for", lambda *args, **kwargs: [record])
    monkeypatch.setattr(retrieve_module, "dense_retrieve", lambda *args, **kwargs: [*distractors, alvo])
    monkeypatch.setattr(retrieve_module, "bm25_retrieve", lambda *args, **kwargs: [*distractors, alvo])

    resultados = retrieve(
        f"Qual ata PNCP {identificador}?",
        None,
        top_k=10,
        documents_file=tmp_path / "documents.jsonl",
        index_dir=tmp_path / "index",
        embed_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        run_id="identifier-run",
        query_id="identifier-query",
    )

    assert resultados[0].chunk_id == alvo_id
    assert resultados[0].metadata["extras"]["identificador_pncp_exato"] is True
    assert resultados[0].metadata["source_file"] == "ata.json"
    logs = [
        json.loads(line)
        for line in default_logger.log_path().read_text(encoding="utf-8").splitlines()
    ]
    for stage in ("retrieval_denso", "retrieval_bm25"):
        diagnostico = next(
            registro["extra"]["diagnostico_identificadores"]
            for registro in logs
            if registro["stage"] == stage and registro["query_id"] == "identifier-query"
        )
        assert diagnostico[0]["matches"][0]["rank"] == 9
    fusao = next(
        registro
        for registro in logs
        if registro["stage"] == "fusao_rrf" and registro["query_id"] == "identifier-query"
    )
    assert fusao["extra"]["diagnostico_identificadores"][0]["matches"][0]["rank"] == 1
    assert fusao["extra"]["diagnostico_identificadores"][0]["matches"][0]["exact_lookup"] is True


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


def test_optional_filters_keep_chunks_without_the_field():
    records = [
        {"chunk_id": "sem-campo", "text": "ambulancia", "metadata": {}},
        _record("dentro", text="ambulancia", metadata_extra={"valor": "150000"}),
        _record("fora", text="ambulancia", metadata_extra={"valor": "50000"}),
    ]

    resultados = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={"valor": {"operator": ">=", "value": 100_000, "optional": True}},
    )

    assert {item.chunk_id for item in resultados} == {"sem-campo", "dentro"}


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


def test_bm25_ignora_acentos_e_caixa():
    records = [
        _record("c1", text="SUPRIMENTOS PARA USO MEDICO E CIRURGICO"),
        _record("c2", text="material de escritorio"),
    ]

    results = bm25_retrieve("uso médico e cirúrgico", records=records)

    assert [item.chunk_id for item in results] == ["c1"]


def test_fetch_k_deixa_chegar_a_fusao_o_chunk_que_as_duas_buscas_poem_abaixo_do_corte(tmp_path):
    """Caso real da base agregada: "valor total" ficou em 4º na densa e na BM25."""
    base_dir = tmp_path / "pca_agregado"
    base_dir.mkdir()
    # densa: d1, d2, d3 à frente de "alvo"; BM25: b1, b2, b3 à frente de "alvo".
    especificacao = [
        ("d1", [1.0, 0.00], "x"), ("d2", [0.99, 0.01], "x"), ("d3", [0.98, 0.02], "x"),
        ("alvo", [0.97, 0.03], "total total"),
        # Na densa, os campeões da BM25 ficam bem abaixo (como nos dados reais).
        *[(f"f{i}", [0.5, 0.5], "x") for i in range(6)],
        ("b1", [0.0, 1.0], "total total total total"), ("b2", [0.0, 1.0], "total total total"),
        ("b3", [0.0, 1.0], "total total total"),
    ]
    registros = [
        {**_record(cid, text=texto, base_id="pca_agregado", embedding=vetor), "natureza": "agregada"}
        for cid, vetor, texto in especificacao
    ]
    (base_dir / "vectors.jsonl").write_text("\n".join(json.dumps(r) for r in registros), encoding="utf-8")
    documentos = tmp_path / "documents.jsonl"
    documentos.write_text("\n".join(json.dumps(r) for r in registros), encoding="utf-8")
    comum = dict(index_dir=tmp_path, documents_file=documentos, top_k=3, embed_fn=lambda t: [[1.0, 0.0]])

    sem_folga = retrieve("total", None, fetch_k=3, **comum)
    com_folga = retrieve("total", None, **comum)

    assert "alvo" not in [r.chunk_id for r in sem_folga]
    assert com_folga[0].chunk_id == "alvo"


# --------------------------------------------------------------------------
# Filtros de múltiplas condições sobre a mesma chave (faixas), usados por
# src/query_analysis.py — ex.: "entre X e Y", ou um ano virando um intervalo
# [1º de janeiro, 1º de janeiro do ano seguinte) em data_vigencia.
# --------------------------------------------------------------------------


def test_filtro_de_faixa_de_valor_com_lista_de_condicoes():
    records = [
        _record("baixo", text="ambulancia", metadata_extra={"valor": "50.000,00"}),
        _record("dentro", text="ambulancia", metadata_extra={"valor": "150.000,00"}),
        _record("alto", text="ambulancia", metadata_extra={"valor": "500.000,00"}),
    ]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={
            "valor": [
                {"operator": ">=", "value": "100.000,00"},
                {"operator": "<=", "value": "200.000,00"},
            ]
        },
    )

    assert [item.chunk_id for item in results] == ["dentro"]


def test_filtro_de_faixa_de_data_simula_filtro_de_ano():
    """Um "ano" vira [1º de jan do ano, 1º de jan do ano seguinte) em data_vigencia."""
    records = [
        _record("antes", text="edital", metadata_extra={"data_vigencia": "2022-12-31"}),
        _record("dentro_inicio", text="edital", metadata_extra={"data_vigencia": "2023-01-01"}),
        _record("dentro_fim", text="edital", metadata_extra={"data_vigencia": "2023-12-31"}),
        _record("depois", text="edital", metadata_extra={"data_vigencia": "2024-01-01"}),
    ]

    results = bm25_retrieve(
        "edital",
        records=records,
        filters={
            "data_vigencia": [
                {"operator": ">=", "value": "2023-01-01"},
                {"operator": "<", "value": "2024-01-01"},
            ]
        },
    )

    assert {item.chunk_id for item in results} == {"dentro_inicio", "dentro_fim"}


def test_filtro_de_faixa_combinado_com_condicao_de_igualdade_em_outra_chave():
    records = [
        _record(
            "fora_do_orgao",
            text="ambulancia contrato",
            metadata_extra={"valor": "150.000,00", "extras": {"orgao": "Secretaria de Educacao"}},
        ),
        _record(
            "dentro_da_faixa_e_orgao",
            text="ambulancia contrato",
            metadata_extra={"valor": "150.000,00", "extras": {"orgao": "Secretaria de Saude"}},
        ),
        _record(
            "fora_da_faixa",
            text="ambulancia contrato",
            metadata_extra={"valor": "999.000,00", "extras": {"orgao": "Secretaria de Saude"}},
        ),
    ]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={
            "valor": [
                {"operator": ">=", "value": "100.000,00"},
                {"operator": "<=", "value": "200.000,00"},
            ],
            "orgao": {"operator": "contains", "value": "Saude"},
        },
    )

    assert [item.chunk_id for item in results] == ["dentro_da_faixa_e_orgao"]


# --------------------------------------------------------------------------
# retrieve() aplica os filtros base a base: um filtro que a base não tem, ou
# que zeraria a base, é ignorado — caso real do índice sem valor/data_vigencia.
# --------------------------------------------------------------------------


def _grava_indice(tmp_path, registros, *, documentos_planos=False):
    for base_id in {r["base_id"] for r in registros}:
        base_dir = tmp_path / base_id
        base_dir.mkdir(exist_ok=True)
        (base_dir / "vectors.jsonl").write_text(
            "\n".join(json.dumps(r) for r in registros if r["base_id"] == base_id), encoding="utf-8"
        )
    documentos = tmp_path / "documents.jsonl"
    if documentos_planos:
        # Formato real de prepare_documents.py: metadados no próprio registro.
        linhas = [{**r["metadata"], "text": r["text"]} for r in registros]
    else:
        linhas = registros
    documentos.write_text("\n".join(json.dumps(r) for r in linhas), encoding="utf-8")
    return dict(index_dir=tmp_path, documents_file=documentos, embed_fn=lambda t: [[1.0, 0.0]])


def _logs_de_retrieval():
    linhas = default_logger.log_path().read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in linhas if json.loads(l)["stage"] == "retrieval_denso"]


def test_retrieve_ignora_filtro_de_campo_que_a_base_nao_tem(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    registros = [
        _record("c1", text="software", embedding=[1.0, 0.0]),
        _record("c2", text="software", embedding=[0.9, 0.1]),
    ]
    comum = _grava_indice(tmp_path, registros)

    results = retrieve("software", None, filters={"valor": {"operator": ">=", "value": 500000}}, **comum)

    assert {item.chunk_id for item in results} == {"c1", "c2"}
    [log] = _logs_de_retrieval()
    assert log["extra"]["filtros_ignorados"] == {"contratos_estruturado": {"sem_campo": ["valor"]}}


def test_retrieve_ignora_filtro_que_zeraria_a_base(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    registros = [
        _record("c1", text="software", embedding=[1.0, 0.0], metadata_extra={"valor": "100"}),
        _record("c2", text="software", embedding=[0.9, 0.1], metadata_extra={"valor": "200"}),
    ]
    comum = _grava_indice(tmp_path, registros)

    results = retrieve("software", None, filters={"valor": {"operator": ">=", "value": 500000}}, **comum)

    assert {item.chunk_id for item in results} == {"c1", "c2"}
    [log] = _logs_de_retrieval()
    assert log["extra"]["filtros_ignorados"] == {"contratos_estruturado": {"nenhum_chunk_passou": ["valor"]}}


def test_retrieve_ignora_filtro_que_reprova_todos_os_chunks_com_o_campo(tmp_path, monkeypatch):
    """Caso real do PCA: indicadores têm órgão "Estado do Rio de Janeiro", os
    planos JSON não têm órgão; o filtro "Secretaria de Saúde" não pode deixar
    só os JSON."""
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    registros = [
        _record("indicador", text="pca classe", base_id="pca_agregado", embedding=[1.0, 0.0],
                metadata_extra={"extras": {"orgao": "Estado do Rio de Janeiro"}}),
        _record("plano", text="pca classe", base_id="pca_agregado", embedding=[0.9, 0.1]),
    ]
    comum = _grava_indice(tmp_path, registros)
    filtros = {"orgao": {"operator": "contains", "value": "Secretaria de Saúde", "optional": True}}

    results = retrieve("pca classe", None, filters=filtros, **comum)

    assert {item.chunk_id for item in results} == {"indicador", "plano"}
    [log] = _logs_de_retrieval()
    assert log["extra"]["filtros_ignorados"] == {"pca_agregado": {"nenhum_chunk_passou": ["orgao"]}}


def test_retrieve_filtro_opcional_continua_valendo_quando_algum_chunk_com_o_campo_passa(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    registros = [
        _record("pca2025", text="pca total", base_id="pca_agregado", embedding=[1.0, 0.0],
                metadata_extra={"data_vigencia": "2025-12-31"}),
        _record("pca2024", text="pca total", base_id="pca_agregado", embedding=[0.9, 0.1],
                metadata_extra={"data_vigencia": "2024-12-31"}),
        _record("indicador", text="pca total", base_id="pca_agregado", embedding=[0.8, 0.2]),
    ]
    comum = _grava_indice(tmp_path, registros)
    filtros = {"data_vigencia": [
        {"operator": ">=", "value": "2025-01-01", "optional": True},
        {"operator": "<", "value": "2026-01-01", "optional": True},
    ]}

    results = retrieve("pca total", None, filters=filtros, **comum)

    assert {item.chunk_id for item in results} == {"pca2025", "indicador"}


def test_retrieve_mantem_filtro_onde_ele_existe_e_ignora_nas_outras_bases(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    registros = [
        _record("barato", text="software", embedding=[1.0, 0.0], metadata_extra={"valor": "100"}),
        _record("caro", text="software", embedding=[0.9, 0.1], metadata_extra={"valor": "900000"}),
        _record("lei", text="software", base_id="editais_normativo", embedding=[0.8, 0.2]),
    ]
    comum = _grava_indice(tmp_path, registros, documentos_planos=True)

    results = retrieve("software", None, filters={"valor": {"operator": ">=", "value": 500000}}, **comum)

    assert {item.chunk_id for item in results} == {"caro", "lei"}
    assert all(item.sparse_score is not None for item in results)
    [log] = _logs_de_retrieval()
    assert log["extra"]["filtros_ignorados"] == {"editais_normativo": {"sem_campo": ["valor"]}}


def test_retrieve_sem_filtro_nao_registra_filtros_ignorados(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    comum = _grava_indice(tmp_path, [_record("c1", text="software", embedding=[1.0, 0.0])])

    retrieve("software", None, **comum)

    [log] = _logs_de_retrieval()
    assert "filtros_ignorados" not in log["extra"]


def test_lista_de_duas_condicoes_nao_e_confundida_com_tupla_operador_valor():
    """(">=", 10) é 1 condição; [{"operator": ">=", ...}, {...}] são 2 — mesmo com len == 2."""
    records = [
        _record("dentro", text="ambulancia", metadata_extra={"valor": "150"}),
        _record("fora", text="ambulancia", metadata_extra={"valor": "5"}),
    ]

    results = bm25_retrieve(
        "ambulancia",
        records=records,
        filters={
            "valor": [
                {"operator": ">=", "value": "100"},
                {"operator": "<=", "value": "300"},
            ]
        },
    )

    assert [item.chunk_id for item in results] == ["dentro"]


def test_reranker_recebe_candidato_abaixo_do_corte_e_falha_volta_rrf(tmp_path):
    records = [_record(f'c{i}', text='ambulancia', embedding=[1.0, 0.1 * i]) for i in range(3)]
    base_dir = tmp_path / 'contratos_estruturado'
    base_dir.mkdir()
    (base_dir / 'vectors.jsonl').write_text('\n'.join(json.dumps(r) for r in records) + '\n', encoding='utf8')
    documents = tmp_path / 'documents.jsonl'
    documents.write_text('', encoding='utf8')
    chamados = []
    def reranker(query, candidatos):
        chamados.append(len(candidatos))
        return [next(c for c in candidatos if c.chunk_id == 'c2')]
    resultado = retrieve('ambulancia', index_dir=tmp_path, documents_file=documents, top_k=1, embed_fn=lambda _: [[1.0, 0.0]], reranker=reranker)
    assert chamados == [3] and resultado[0].chunk_id == 'c2'
    def quebrado(query, candidatos):
        raise RuntimeError('endpoint indisponível')
    fallback = retrieve('ambulancia', index_dir=tmp_path, documents_file=documents, top_k=1, embed_fn=lambda _: [[1.0, 0.0]], reranker=quebrado)
    assert len(fallback) == 1 and fallback[0].chunk_id == 'c0'
