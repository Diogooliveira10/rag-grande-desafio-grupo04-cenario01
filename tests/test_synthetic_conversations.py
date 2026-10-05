import json
from collections import Counter
from pathlib import Path

import pytest

from src.fetch_corpus import load_manifest
from src.generate import _cabecalho
from src.index import build_index
from src.prepare_documents import prepare_documents
from src.retrieve import bm25_retrieve
from src.schema import Chunk, ChunkMetadata, Natureza
from src.sensitivity import filtrar_chunks_permitidos, mascarar_chunks
from src.synthetic_conversations import conversation_document

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus_manifest.yaml"
SOURCE = ROOT / "data/synthetic/conversacional_sintetica"


def _payload():
    return json.loads((SOURCE / "conv_001_dprj_papel_a4.json").read_text(encoding="utf-8"))


def _prepare(tmp_path, manifest=MANIFEST):
    output = tmp_path / "documents.jsonl"
    summary = prepare_documents(SOURCE, output, tmp_path / "report.json", manifest,
                                base_id="emails_sinteticos")
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    return summary, records, output


def test_dataset_is_unique_and_enabled_for_normal_queries():
    files = sorted(SOURCE.iterdir())
    assert len(files) == 25
    assert all(path.suffix == ".json" for path in files)
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert len({payload["conversation_id"] for payload in payloads}) == 25
    assert sum(len(payload["messages"]) for payload in payloads) == 45
    base = next(base for base in load_manifest(MANIFEST)["bases"] if base["base_id"] == "emails_sinteticos")
    assert base["escopo"] == "oficial"
    assert base["natureza"] == "conversacional"
    assert base["dados_sinteticos"] is True
    assert base["volume_alvo"] == 25


def test_prepare_preserves_threads_provenance_and_sensitivity(tmp_path):
    summary, records, _ = _prepare(tmp_path)
    assert summary["falhas"] == 0
    assert len(records) == 25
    assert Counter(record["sensitivity"] for record in records) == {"publico": 20, "restrito": 5}
    assert len({record["logical_id"] for record in records}) == 25
    for record in records:
        ChunkMetadata.model_validate(record)
        assert record["chunk_count"] == 1
        assert record["chunking_strategy"] == "registro_inteiro"
        assert record["extras"]["sintetico"] is True
        assert record["sha256"] and record["manifest_hash"]
        assert "CONTEÚDO SINTÉTICO" in record["text"]
    first = next(record for record in records if record["extras"]["conversation_id"] == _payload()["conversation_id"])
    assert "josiane.drosdrocky@wrpapeis.com.br" in first["text"]
    assert "Subsolo 1" in first["text"]
    assert first["extras"]["total_mensagens"] == 2


def test_cache_manifest_refresh_does_not_reclassify_restricted_conversations(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    text = MANIFEST.read_text(encoding="utf-8")
    manifest.write_text(text, encoding="utf-8")
    _, original, _ = _prepare(tmp_path, manifest)
    manifest.write_text(text + "\n# Changed provenance, same extraction settings\n", encoding="utf-8")
    summary, refreshed, _ = _prepare(tmp_path, manifest)
    assert summary["extraction_cache_hits"] == 25
    assert [r["sensitivity"] for r in refreshed] == [r["sensitivity"] for r in original]
    assert refreshed[0]["manifest_hash"] != original[0]["manifest_hash"]
    assert Counter(r["sensitivity"] for r in refreshed) == {"publico": 20, "restrito": 5}


@pytest.mark.parametrize("field,value", [("sensitivity_level", None), ("sensitivity_level", "unknown"),
                                        ("conversation_id", ""), ("messages", []),
                                        ("messages", [{"content": ""}])])
def test_invalid_conversations_fail_closed(field, value):
    payload = _payload()
    payload[field] = value
    with pytest.raises(ValueError):
        conversation_document(payload)


@pytest.mark.parametrize("guardrail", ["refuse_bank_info", "refuse_credentials"])
def test_restricted_guardrails_override_public_label(guardrail):
    payload = _payload()
    payload["guardrail_type"] = guardrail
    assert conversation_document(payload)[1] == "restrito"


def test_credentials_override_public_label_without_guardrail():
    payload = _payload()
    payload["messages"][0]["sensitive_data_types"] = ["credentials"]
    assert conversation_document(payload)[1] == "restrito"


def test_masking_and_restricted_filter_apply_to_imported_dataset(tmp_path):
    _, records, _ = _prepare(tmp_path)
    chunks = [Chunk(content=r["text"], metadata=ChunkMetadata.model_validate(r)) for r in records]
    permitted, excluded = filtrar_chunks_permitidos(chunks)
    assert excluded == 5 and len(permitted) == 20
    masked, counts = mascarar_chunks(permitted)
    assert counts["cpf"] > 0 and counts["telefone"] > 0 and counts["email"] > 0
    first = next(c for c in masked if "conv_001_dprj_papel_a4" in c.content)
    assert "120.456.789-28" not in first.content
    assert "99876-5432" not in first.content
    assert "josiane.drosdrocky@wrpapeis.com.br" not in first.content
    assert "logistica@defensoria.rj.def.br" in first.content
    assert "48.975.836/0001-38" in first.content
    assert "sintetico=true" in _cabecalho(first.metadata)


def test_official_conversational_search_finds_synthetic_threads(tmp_path):
    _, _, output = _prepare(tmp_path)
    results = bm25_retrieve("resmas papel A4 Subsolo DPRJ", documents_file=output,
                              bases=[Natureza.CONVERSACIONAL], escopos=("oficial",), top_k=3)
    assert results
    assert results[0].metadata["base_id"] == "emails_sinteticos"
    assert results[0].metadata["extras"]["conversation_id"] == "conv_001_dprj_papel_a4"


def test_index_merge_keeps_existing_bases_and_is_idempotent(tmp_path):
    def embed(texts):
        return [[1.0, 0.5] for _ in texts]

    original = tmp_path / "original.jsonl"
    original.write_text(json.dumps({"chunk_id": "old:1", "document_id": "old:1",
        "base_id": "contratos_estruturado", "text": "Contrato público", "doc_type": "json",
        "source_file": "original.json"}) + "\n", encoding="utf-8")
    index = tmp_path / "index"
    build_index(original, index, embed, model="test")
    original_vectors = (index / "contratos_estruturado/vectors.jsonl").read_bytes()
    _, _, supplemental = _prepare(tmp_path)
    for _ in range(2):
        manifest = build_index(supplemental, index, embed, model="test", merge_existing=True)
        assert {base["base_id"] for base in manifest["bases"]} == {"contratos_estruturado", "emails_sinteticos"}
        assert (index / "contratos_estruturado/vectors.jsonl").read_bytes() == original_vectors
        assert next(base for base in manifest["bases"] if base["base_id"] == "emails_sinteticos")["total_chunks"] == 25
    with pytest.raises(ValueError, match="mesmo modelo"):
        build_index(supplemental, index, embed, model="different", merge_existing=True)
    embed.encoding_config = {"pooling": "different"}
    with pytest.raises(ValueError, match="configuração"):
        build_index(supplemental, index, embed, model="test", merge_existing=True)
