import json
from decimal import Decimal
from pathlib import Path

import pytest

import config
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


def test_temporal_fields_are_scoped_to_their_domain():
    from src.retrieve import _matches_filters

    rule = {"optional": False, "operator": ">=", "value": "2025-01-01"}
    assert _matches_filters({"natureza": "estruturada", "data_assinatura": "2025-01-02"},
                            {"data_assinatura": rule})
    assert _matches_filters({"natureza": "normativa", "data_vigencia": "2025-01-02"},
                            {"data_assinatura": rule})
    assert not _matches_filters({"natureza": "estruturada", "data_assinatura": "2024-12-31"},
                                {"data_assinatura": rule})


def test_explicit_supplier_cnpj_does_not_match_only_organization_cnpj():
    from src.retrieve import _matches_filters

    record = {"natureza": "estruturada", "orgao_cnpj": "30381552000158",
              "fornecedor_cnpj": "15064270000133"}
    condition = {"optional": False, "operator": "contains", "value": "30381552000158"}

    assert not _matches_filters(record, {"fornecedor_cnpj": condition})
    assert _matches_filters(record, {"orgao_cnpj": condition})


def test_pca_entity_role_filters_only_the_named_unit_or_organization():
    from src.query_analysis import analisar_query, filtros_para_retrieve
    from src.retrieve import _matches_filters

    unit_query = "Quanto a Secretaria de Estado de Saúde planejou no plano de contratações de 2025?"
    org_query = "Qual o total planejado pelo órgão Secretaria de Estado de Saúde no PCA de 2025?"
    unit_filter = filtros_para_retrieve(analisar_query(unit_query).filtros)
    org_filter = filtros_para_retrieve(analisar_query(org_query).filtros)
    unit_record = {"base_id": "pca_agregado", "natureza": "agregada", "ano_pca": 2025,
                   "unidade_nome": "SECRET ESTADO SAUDE", "orgao": "ESTADO DO RIO DE JANEIRO"}
    org_record = {"base_id": "pca_agregado", "natureza": "agregada", "ano_pca": 2025,
                  "unidade_nome": "UNIDADE ADMINISTRATIVA", "orgao": "SECRETARIA DE ESTADO DE SAUDE"}

    assert _matches_filters(unit_record, unit_filter)
    assert not _matches_filters(org_record, unit_filter)
    assert not _matches_filters(unit_record, org_filter)
    assert _matches_filters(org_record, org_filter)


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
# Só filtros explicitamente opcionais podem ser ampliados base a base.
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

    results = retrieve("software", None, filters={"valor": {"operator": ">=", "value": 500000, "optional": True}}, **comum)

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

    results = retrieve("software", None, filters={"valor": {"operator": ">=", "value": 500000, "optional": True}}, **comum)

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

    results = retrieve("software", None, filters={"valor": {"operator": ">=", "value": 500000, "optional": True}}, **comum)

    assert {item.chunk_id for item in results} == {"caro", "lei"}
    assert all(item.sparse_score is not None for item in results)
    [log] = _logs_de_retrieval()
    assert log["extra"]["filtros_ignorados"] == {"editais_normativo": {"sem_campo": ["valor"]}}


@pytest.mark.parametrize("extras", [{}, {"valor": 100}])
def test_filtro_obrigatorio_nao_e_removido_quando_nao_ha_resultado(tmp_path, extras):
    comum = _grava_indice(tmp_path, [_record("c1", text="software", embedding=[1.0, 0.0], metadata_extra=extras)])
    assert retrieve("software", None, filters={"valor": {"operator": ">=", "value": 500000}}, **comum) == []


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


@pytest.mark.parametrize("pergunta", [
    "Qual ata tem o fornecedor CNPJ 44292391000130?",
    "Qual ata tem o fornecedor CNPJ 44.292.391/0001-30?",
])
def test_cnpj_da_pergunta_acha_o_trecho_nas_duas_formas(pergunta):
    ata = {"chunk_id": "ata", "base_id": "atas_conversacional",
           "text": "Fornecedor: EMPRESA X LTDA, CNPJ 44.292.391/0001-30",
           "metadata": {"base_id": "atas_conversacional", "source_file": "ata.pdf"}}
    contrato = {"chunk_id": "contrato", "base_id": "contratos_estruturado",
                "text": '{"niFornecedor": "44292391000130"}',
                "metadata": {"base_id": "contratos_estruturado", "source_file": "c.json"}}
    outro = {"chunk_id": "outro", "base_id": "atas_conversacional", "text": "CNPJ 11.111.111/0001-11",
             "metadata": {"base_id": "atas_conversacional", "source_file": "o.pdf"}}

    achados = retrieve_module._resultados_identificador_exato(pergunta, [ata, contrato, outro])

    assert [item.chunk_id for item in achados] == ["ata", "contrato"]


def test_cnpj_no_comeco_do_numero_de_controle_nao_vira_identificador_proprio():
    assert retrieve_module._identificadores_pncp("Contrato 00394429000100-2-001108/2024") == [
        "00394429000100-2-001108/2024"
    ]


def test_filtros_de_publicacao_e_orgao_aplicam_se_a_normas():
    from src.retrieve import _matches_filters

    assert not _matches_filters(
        {"base_id": "editais_normativo", "data_publicacao": "2020-01-01"},
        {"data_publicacao": {"operator": ">=", "value": "2025-01-01", "optional": False}},
    )
    assert not _matches_filters(
        {"base_id": "editais_normativo", "extras": {"orgao": "Outro órgão"}},
        {"orgao": {"operator": "contains", "value": "Secretaria X", "optional": False}},
    )


def test_filtros_de_valor_nao_se_aplicam_a_normas():
    from src.retrieve import _matches_filters

    assert _matches_filters(
        {"base_id": "editais_normativo"},
        {"valor": {"operator": ">=", "value": 100000, "optional": False}},
    )
# --------------------------------------------------------------------------
# Embeddings de consulta por provedor (config.embedding_endpoint)
# --------------------------------------------------------------------------


def test_embedding_endpoint_resolve_cada_provedor(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_LOCAL_URL", "http://127.0.0.1:18321/v1")
    monkeypatch.setattr(config, "OLLAMA_LOCAL_EMBEDDING_MODEL", "Qwen3-Embedding-0.6B.Q8_0")
    assert config.embedding_endpoint("ollama")[0] == "http://127.0.0.1:18321/v1"
    assert config.embedding_endpoint("ollama")[2] == "Qwen3-Embedding-0.6B.Q8_0"
    # "shared" é apelido do ECOA, como já era em create_embeddings.
    assert config.embedding_endpoint("shared") == config.embedding_endpoint("ecoa")


def test_embedding_endpoint_padrao_continua_ecoa(monkeypatch):
    """Sem configurar, nada muda: quem embeda a pergunta segue no ECOA."""
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "ecoa")
    endpoint, _chave, modelo = config.embedding_endpoint()
    assert endpoint == config.OLLAMA_URL and modelo == config.EMBEDDING_MODEL


def test_embedding_endpoint_rejeita_provedor_desconhecido():
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        config.embedding_endpoint("inexistente")


def _resposta_de_embedding(vetores):
    from types import SimpleNamespace
    return SimpleNamespace(
        model="fake", choices=[],
        data=[SimpleNamespace(embedding=v, index=i) for i, v in enumerate(vetores)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1),
    )


def test_embed_de_consulta_usa_o_provedor_configurado(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src import retrieve

    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OLLAMA_LOCAL_URL", "http://127.0.0.1:18321/v1")
    monkeypatch.setattr(config, "OLLAMA_LOCAL_EMBEDDING_MODEL", "Qwen3-Embedding-0.6B.Q8_0")
    monkeypatch.setattr(retrieve, "QUERY_CACHE_PATH", tmp_path / "query_cache.jsonl")

    visto = {}

    def fake_openai(**opcoes):
        visto["base_url"] = opcoes["base_url"]

        def create(**argumentos):
            visto["model"] = argumentos["model"]
            visto["input"] = argumentos["input"]
            return _resposta_de_embedding([[1.0, 0.0]])

        return SimpleNamespace(embeddings=SimpleNamespace(create=create))

    import openai
    monkeypatch.setattr(openai, "OpenAI", fake_openai)

    vetores = retrieve._embed_fn_com_uso([])(["contratos de ambulância"])
    assert vetores == [[1.0, 0.0]]
    assert visto["base_url"] == "http://127.0.0.1:18321/v1"
    assert visto["model"] == "Qwen3-Embedding-0.6B.Q8_0"
    # A instrução Qwen é exclusiva da busca densa e vai junto do texto.
    assert visto["input"] == [retrieve.embedding_query_text("contratos de ambulância")]


def test_cache_de_query_separa_por_provedor(tmp_path, monkeypatch):
    """Vetor do ECOA não pode valer para a pergunta quando se pede o local."""
    from types import SimpleNamespace
    from src import retrieve

    monkeypatch.setattr(retrieve, "QUERY_CACHE_PATH", tmp_path / "query_cache.jsonl")
    chamadas = []

    def fake_openai(**opcoes):
        def create(**argumentos):
            chamadas.append((opcoes["base_url"], argumentos["model"]))
            return _resposta_de_embedding([[1.0, 0.0]])
        return SimpleNamespace(embeddings=SimpleNamespace(create=create))

    import openai
    monkeypatch.setattr(openai, "OpenAI", fake_openai)

    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "ecoa")
    retrieve._embed_fn_com_uso([])(["uma pergunta"])
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr(config, "OLLAMA_LOCAL_URL", "http://127.0.0.1:18321/v1")
    retrieve._embed_fn_com_uso([])(["uma pergunta"])

    # Duas chamadas: o mesmo texto, num provedor diferente, não vem do cache.
    assert len(chamadas) == 2
    assert chamadas[0][0] != chamadas[1][0]

    # E o mesmo provedor, mesma pergunta, aí sim vem do cache (uma chamada só).
    chamadas.clear()
    retrieve._embed_fn_com_uso([])(["uma pergunta"])
    assert chamadas == []


def test_read_jsonl_le_uma_vez_e_rele_quando_o_arquivo_muda(tmp_path, monkeypatch):
    arquivo = tmp_path / "vectors.jsonl"
    arquivo.write_text('{"chunk_id": "a"}\n', encoding="utf-8")
    assert [r["chunk_id"] for r in retrieve_module._read_jsonl(arquivo)] == ["a"]

    leituras = []
    original = json.loads
    monkeypatch.setattr(retrieve_module.json, "loads", lambda *a, **k: leituras.append(1) or original(*a, **k))
    primeira = retrieve_module._read_jsonl(arquivo)
    primeira.append({"chunk_id": "intruso"})
    assert [r["chunk_id"] for r in retrieve_module._read_jsonl(arquivo)] == ["a"]
    assert leituras == []

    arquivo.write_text('{"chunk_id": "a"}\n{"chunk_id": "b"}\n', encoding="utf-8")
    assert [r["chunk_id"] for r in retrieve_module._read_jsonl(arquivo)] == ["a", "b"]
    assert leituras == [1, 1]


def test_read_jsonl_de_arquivo_ausente_devolve_lista_vazia(tmp_path):
    assert retrieve_module._read_jsonl(tmp_path / "nao-existe.jsonl") == []


def test_pre_carregar_indices_deixa_os_arquivos_no_cache(tmp_path, monkeypatch):
    base = tmp_path / "index" / "contratos_estruturado"
    base.mkdir(parents=True)
    (base / "vectors.jsonl").write_text('{"chunk_id": "a"}\n', encoding="utf-8")
    documents = tmp_path / "documents.jsonl"
    documents.write_text('{"chunk_id": "a", "text": "x"}\n', encoding="utf-8")
    monkeypatch.setattr(retrieve_module, "QUERY_CACHE_PATH", tmp_path / "sem-cache.jsonl")

    assert retrieve_module.pre_carregar_indices(tmp_path / "index", documents) == 2
    assert str(base / "vectors.jsonl") in retrieve_module._JSONL_CACHE
    assert str(documents) in retrieve_module._JSONL_CACHE


def test_indice_com_faiss_e_lido_sem_o_vetor(tmp_path):
    base = tmp_path / "contratos_estruturado"
    base.mkdir()
    (base / "vectors.jsonl").write_text(
        '{"chunk_id": "a", "embedding": [0.1, 0.2], "metadata": {"base_id": "contratos_estruturado"}}\n',
        encoding="utf-8")

    assert "embedding" in retrieve_module._registros_do_indice(base / "vectors.jsonl")[0]

    (base / "vectors.faiss").write_bytes(b"")
    registro = retrieve_module._registros_do_indice(base / "vectors.jsonl")[0]

    assert "embedding" not in registro
    assert registro["chunk_id"] == "a" and registro["metadata"]["base_id"] == "contratos_estruturado"


def _bm25_antigo(query, textos):
    """Fórmula de antes do cache de termos, para comparar o ranking."""
    import math
    from collections import Counter
    termos = retrieve_module._tokens(query)
    tokenizados = [retrieve_module._tokens(t) for t in textos]
    media = sum(len(t) for t in tokenizados) / len(tokenizados)
    df = Counter(term for tokens in tokenizados for term in set(tokens))
    notas = []
    for tokens in tokenizados:
        contagem, nota = Counter(tokens), 0.0
        for term in termos:
            if term in contagem:
                idf = math.log(1 + (len(tokenizados) - df[term] + 0.5) / (df[term] + 0.5))
                nota += idf * contagem[term] * 2.5 / (contagem[term] + 1.5 * (0.25 + 0.75 * len(tokens) / media))
        notas.append(nota)
    return notas


def test_bm25_com_cache_de_termos_da_o_mesmo_ranking():
    textos = ["Locação de veículos para a secretaria", "Aquisição de ambulância tipo D",
              "Locação de ambulância e veículos de apoio", "Serviço de limpeza predial"]
    registros = [{"chunk_id": f"c{i}", "text": t, "metadata": {"base_id": "b"}} for i, t in enumerate(textos)]
    notas = _bm25_antigo("locação de veículos e ambulância", textos)

    resultado = retrieve_module.bm25_retrieve("locação de veículos e ambulância", records=registros, top_k=2)

    esperado = sorted((i for i, n in enumerate(notas) if n > 0), key=lambda i: (-notas[i], f"c{i}"))[:2]
    assert [r.chunk_id for r in resultado] == [f"c{i}" for i in esperado]
    assert [r.score for r in resultado] == pytest.approx([notas[i] for i in esperado])


def _ata(chunk_id, compra=None, **extras_extra):
    extras = {"numero_controle_compra": compra, **extras_extra} if compra else dict(extras_extra)
    return RetrievalResult(chunk_id=chunk_id, content="ata", score=1.0,
                           metadata={"base_id": "atas_conversacional", "source_file": f"{chunk_id}.pdf",
                                     "extras": extras})


def test_chave_da_compra_usa_extras_e_so_vale_para_atas():
    compra = "12345678000190-1-000007/2025"
    assert retrieve_module._chave_da_compra(_ata("a", compra)) == compra
    # número da ata do PNCP: vale a parte que identifica a compra
    por_ata = _ata("b", numero_controle_pncp=compra + "-000003")
    assert retrieve_module._chave_da_compra(por_ata) == compra
    # sem número nenhum, o PDF de origem é a compra
    assert retrieve_module._chave_da_compra(_ata("c")) == "arquivo:c.pdf"
    contrato = RetrievalResult(chunk_id="k", content="x", score=1.0,
                               metadata={"base_id": "contratos_estruturado",
                                         "extras": {"numero_controle_compra": compra}})
    assert retrieve_module._chave_da_compra(contrato) is None


def test_variar_por_compra_limita_atas_e_respeita_a_configuracao(monkeypatch):
    compra = "12345678000190-1-000007/2025"
    itens = [_ata(f"a{i}", compra) for i in range(4)] + [_ata("b0", "12345678000190-1-000008/2025")]
    monkeypatch.setattr(config, "RETRIEVAL_MAX_PER_PURCHASE", 2)
    lista, rebaixados = retrieve_module._variar_por_compra("atraso de entrega", itens)
    assert [i.chunk_id for i in lista] == ["a0", "a1", "b0", "a2", "a3"] and rebaixados == 2
    monkeypatch.setattr(config, "RETRIEVAL_MAX_PER_PURCHASE", 0)
    assert retrieve_module._variar_por_compra("atraso de entrega", itens) == (itens, 0)


@pytest.mark.parametrize("pergunta", [
    "O que diz a ata da compra 12345678000190-1-000007/2025?",
    "O que diz o contrato 15/2024 sobre multa?",
])
def test_variar_por_compra_nao_atua_quando_a_pergunta_cita_o_registro(monkeypatch, pergunta):
    monkeypatch.setattr(config, "RETRIEVAL_MAX_PER_PURCHASE", 2)
    itens = [_ata(f"a{i}", "12345678000190-1-000007/2025") for i in range(4)]
    assert retrieve_module._variar_por_compra(pergunta, itens) == (itens, 0)


def test_retrieve_traz_compras_diferentes_no_topo_sem_perder_recall(tmp_path, monkeypatch):
    def ata(chunk_id, compra, i):
        return _record(chunk_id, text="ambulancia", base_id="atas_conversacional",
                       embedding=[1.0, 0.1 * i], metadata_extra={"extras": {"numero_controle_compra": compra}})
    a, b, c = (f"12345678000190-1-00000{n}/2025" for n in (1, 2, 3))
    registros = [ata("a0", a, 0), ata("a1", a, 1), ata("a2", a, 2), ata("a3", a, 3),
                 ata("b0", b, 4), ata("c0", c, 5)]
    indice = _grava_indice(tmp_path, registros)

    monkeypatch.setattr(config, "RETRIEVAL_MAX_PER_PURCHASE", 0)
    sem_regra = retrieve("ambulancia", top_k=4, **indice)
    assert [r.chunk_id for r in sem_regra] == ["a0", "a1", "a2", "a3"]

    monkeypatch.setattr(config, "RETRIEVAL_MAX_PER_PURCHASE", 2)
    com_regra = retrieve("ambulancia", top_k=4, **indice)
    assert [r.chunk_id for r in com_regra] == ["a0", "a1", "b0", "c0"]
    # com mais espaço no corte o excedente volta: nada é descartado
    ampla = retrieve("ambulancia", top_k=6, **indice)
    assert [r.chunk_id for r in ampla] == ["a0", "a1", "b0", "c0", "a2", "a3"]


def test_busca_nao_altera_os_registros_compartilhados_do_cache(tmp_path):
    """A seleção de candidatos deixou de copiar os ~144 mil registros a cada
    pergunta; isso só é seguro enquanto nada na busca os altera."""
    import copy

    documentos = tmp_path / "documents.jsonl"
    registros = [
        _record("c1", text='{"objetoContrato": "Aquisição de tomógrafo"}',
                metadata_extra={"orgao": "Fundo Municipal de Saúde de Niterói"}),
        _record("c2", text='{"objetoContrato": "Peças para tomógrafo"}',
                metadata_extra={"orgao": "Secretaria de Estado de Saúde"}),
        _record("c3", text="manutenção de ambulância", base_id="atas_conversacional"),
    ]
    documentos.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in registros) + "\n", encoding="utf-8")
    em_cache = retrieve_module._read_jsonl(documentos)
    antes = copy.deepcopy(em_cache)
    filtros = {
        "orgao": {"operator": "contains", "value": "Fundo Municipal de Saúde", "optional": True},
        "objeto_auxiliar": {"operator": "==", "value": False, "optional": False},
        "objeto_termo": {"operator": "contains", "value": "tomograf", "optional": False},
    }

    resultados = bm25_retrieve("tomógrafo", documents_file=documentos, filters=filtros)
    retrieve_module._filtrar_por_base(em_cache, filtros)
    resultados[0]._record["text"] = "alterado fora da busca"

    assert [item.chunk_id for item in resultados] == ["c1"]
    assert retrieve_module._read_jsonl(documentos) == antes


def test_selecao_sem_filtro_e_calculada_uma_vez_por_versao_do_arquivo(tmp_path, monkeypatch):
    """A mesma pergunta percorria os ~144 mil registros 3 ou 4 vezes para chegar
    à mesma lista; agora a seleção por base e escopo fica guardada até o arquivo mudar."""
    documentos = tmp_path / "documents.jsonl"
    registros = [_record("c1", text="ambulancia"), _record("a1", text="ata", base_id="atas_conversacional")]
    documentos.write_text("".join(json.dumps(r) + "\n" for r in registros), encoding="utf-8")
    chamadas = []
    original = retrieve_module._records_for
    monkeypatch.setattr(retrieve_module, "_records_for", lambda *a, **k: chamadas.append(1) or original(*a, **k))

    primeira = retrieve_module._selecionar(tmp_path, ["contratos_estruturado"], None, documents_file=documentos)
    segunda = retrieve_module._selecionar(tmp_path, ["contratos_estruturado"], None, documents_file=documentos)
    assert [r["chunk_id"] for r in primeira] == [r["chunk_id"] for r in segunda] == ["c1"]
    assert len(chamadas) == 1

    primeira.clear()  # quem recebe a lista pode mexer nela sem estragar a guardada
    assert [r["chunk_id"] for r in retrieve_module._selecionar(
        tmp_path, ["contratos_estruturado"], None, documents_file=documentos)] == ["c1"]

    registros.append(_record("c2", text="tomografo"))
    documentos.write_text("".join(json.dumps(r) + "\n" for r in registros), encoding="utf-8")
    assert [r["chunk_id"] for r in retrieve_module._selecionar(
        tmp_path, ["contratos_estruturado"], None, documents_file=documentos)] == ["c1", "c2"]
    assert len(chamadas) == 2


def _indice_com_faiss(tmp_path, vetores):
    import faiss
    import numpy as np

    base = tmp_path / "contratos_estruturado"
    base.mkdir()
    registros = [_record(chunk_id, text=chunk_id, embedding=vetor) for chunk_id, vetor in vetores]
    (base / "vectors.jsonl").write_text("".join(json.dumps(r) + "\n" for r in registros), encoding="utf-8")
    matriz = np.ascontiguousarray([v for _, v in vetores], dtype=np.float32)
    faiss.normalize_L2(matriz)
    indice = faiss.IndexFlatIP(matriz.shape[1])
    indice.add(matriz)
    faiss.write_index(indice, str(base / "vectors.faiss"))
    return base


def test_busca_densa_com_filtro_usa_o_faiss_dos_registros_lidos_sem_vetor(tmp_path, monkeypatch):
    """Com filtro, os candidatos vêm do índice já sem o vetor (ele fica no FAISS).
    Desde 2026-10-19 o FAISS não era usado nesse caminho: todos viravam "dimensão 0"
    e a busca densa ficava vazia em qualquer pergunta com filtro."""
    base = _indice_com_faiss(tmp_path, [("longe", [-1.0, 0.0]), ("perto", [0.9, 0.1]), ("meio", [0.0, 1.0])])
    avisos = []
    monkeypatch.setattr(retrieve_module, "registrar", lambda **k: avisos.append(k))
    sem_vetor = retrieve_module._registros_do_indice(base / "vectors.jsonl")
    assert all("embedding" not in r for r in sem_vetor)

    filtrados = [r for r in sem_vetor if r["chunk_id"] != "meio"]  # o recorte de um filtro
    resultados = dense_retrieve("consulta", records=filtrados, index_dir=tmp_path,
                                documents_file=tmp_path / "sem-documents.jsonl",
                                embed_fn=lambda textos: [[1.0, 0.0] for _ in textos], top_k=2)

    assert [r.chunk_id for r in resultados] == ["perto", "longe"]
    assert avisos == []


def test_aviso_de_vetor_descartado_grava_contagem_e_poucos_exemplos(monkeypatch):
    """O aviso listava os 106 mil contratos a cada pergunta: 461 MB do log de 26/10."""
    avisos = []
    monkeypatch.setattr(retrieve_module, "registrar", lambda **k: avisos.append(k))
    incompativeis = [{"chunk_id": f"c{i}", "expected_dimension": 1024, "actual_dimension": 0} for i in range(5000)]

    retrieve_module._avisar_vetores_descartados("q", incompativeis, [], run_id="r", query_id="q")

    (aviso,) = avisos
    assert aviso["output"].startswith("5000 embedding(s) incompatível(is)")
    assert len(aviso["extra"]["embedding_dimension_mismatches"]) == 20
    assert aviso["extra"]["total_descartados"] == 5000


def test_metadado_do_indice_reaproveita_o_do_documento_so_quando_e_igual():
    """Os 18 campos do metadado do índice repetem os do documents.jsonl; a visão
    evita a segunda cópia (~930 MB) e se comporta como o dict original."""
    documento = {"chunk_id": "c1", "base_id": "contratos_estruturado", "natureza": "estruturada",
                 "extras": {"orgao": "X"}, "text": "texto que não é metadado"}
    original = {"chunk_id": "c1", "base_id": "contratos_estruturado", "extras": {"orgao": "X"}}
    registro = {"chunk_id": "c1", "metadata": dict(original)}

    retrieve_module._compartilhar_metadado(registro, {"c1": documento})

    visao = registro["metadata"]
    assert isinstance(visao, retrieve_module._MetadadoDoDocumento)
    assert dict(visao) == original and list(visao) == list(original) and len(visao) == 3
    assert "text" not in visao and visao.get("text") is None and visao.get("natureza", "-") == "-"
    assert retrieve_module._metadata_value(visao, "orgao") == "X"
    with pytest.raises(KeyError):
        visao["natureza"]

    diferente = {"chunk_id": "c1", "metadata": {"chunk_id": "c1", "base_id": "outra_base"}}
    retrieve_module._compartilhar_metadado(diferente, {"c1": documento})
    assert type(diferente["metadata"]) is dict


def test_pre_carga_le_os_documentos_antes_e_o_indice_usa_os_metadados_deles(tmp_path, monkeypatch):
    normalizados = tmp_path / "normalized"
    normalizados.mkdir()
    monkeypatch.setattr(retrieve_module.config, "NORMALIZED_DATA_DIR", normalizados)
    documentos = normalizados / "documents.jsonl"
    metadado = {"chunk_id": "a", "base_id": "contratos_estruturado", "source_file": "a.json"}
    documentos.write_text(json.dumps({**metadado, "text": "ambulancia"}) + "\n", encoding="utf-8")
    indice = tmp_path / "index"
    (indice / "contratos_estruturado").mkdir(parents=True)
    (indice / "contratos_estruturado" / "vectors.jsonl").write_text(
        json.dumps({"chunk_id": "a", "metadata": metadado, "embedding": [1.0, 0.0], "base_id": "contratos_estruturado"})
        + "\n", encoding="utf-8")
    (indice / "contratos_estruturado" / "vectors.faiss").write_bytes(b"")  # só para ler sem o vetor

    monkeypatch.setattr(retrieve_module, "_ler_indice_faiss", lambda caminho: None)
    monkeypatch.setattr(retrieve_module, "_indice_lexico", lambda *a, **k: None)
    retrieve_module.pre_carregar_indices(indice, documentos)

    (registro,) = retrieve_module._registros_do_indice(indice / "contratos_estruturado" / "vectors.jsonl")
    assert isinstance(registro["metadata"], retrieve_module._MetadadoDoDocumento)
    assert dict(registro["metadata"]) == metadado and "embedding" not in registro
