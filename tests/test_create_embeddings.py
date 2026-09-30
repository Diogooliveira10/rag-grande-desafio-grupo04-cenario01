import json
from pathlib import Path

import pytest

from src.create_embeddings import create_embeddings


def _write_chunks(path: Path, texts: list[str]) -> None:
    path.write_text(
        "".join(
            json.dumps(
                {
                    "chunk_id": f"base:chunk-{index}",
                    "document_id": f"base:doc-{index}",
                    "base_id": "exemplos_teste",
                    "natureza": "normativa",
                    "doc_type": "pdf",
                    "origem": "tests/fixtures/pdf",
                    "manifest_hash": "sha256:manifest",
                    "bytes": 123,
                    "data_vigencia": None,
                    "source_file": "tests/fixtures/pdf/example.pdf",
                    "text": text,
                },
                ensure_ascii=False,
            )
            + "\n"
            for index, text in enumerate(texts, start=1)
        ),
        encoding="utf-8",
    )


def test_create_embeddings_batches_and_reuses_content(tmp_path):
    input_path = tmp_path / "documents.jsonl"
    output_path = tmp_path / "embeddings.jsonl"
    _write_chunks(input_path, ["mesmo texto", "outro texto", "mesmo texto"])
    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[float(len(text))] for text in texts]

    summary = create_embeddings(input_path, output_path, batch_size=1, pause_seconds=0, embed_batch=fake_embed)

    assert summary["chunks"] == 3
    assert summary["pendentes"] == 2
    assert summary["gerados"] == 2
    assert len(calls) == 2
    records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 3
    assert records[0]["embedding"] == records[2]["embedding"]
    assert records[0]["base_id"] == "exemplos_teste"
    assert "metadata" not in records[0]
    assert records[0]["natureza"] == "normativa"
    assert records[0]["origem"] == "tests/fixtures/pdf"
    assert records[0]["manifest_hash"] == "sha256:manifest"
    assert records[0]["embedding_cache_schema"] == 4
    assert records[0]["embedding_input_sha256"]
    assert not list(tmp_path.glob("embeddings.part-*.jsonl"))
    report = json.loads((tmp_path / "embedding_report.json").read_text(encoding="utf-8"))
    assert report["gerados"] == 2
    assert report["dimensao"] == 1
    assert "Users" not in report["input"]
    assert not Path(report["input"]).is_absolute()

    reused = create_embeddings(
        input_path,
        output_path,
        pause_seconds=0,
        embed_batch=lambda _: pytest.fail("cache atual não deveria chamar o provider"),
    )
    assert reused["pendentes"] == 0
    assert reused["reutilizados"] == 3


def test_create_embeddings_dry_run_does_not_call_provider(tmp_path):
    input_path = tmp_path / "documents.jsonl"
    output_path = tmp_path / "embeddings.jsonl"
    _write_chunks(input_path, ["texto"])

    summary = create_embeddings(input_path, output_path, dry_run=True, embed_batch=lambda _: pytest.fail("não deveria chamar"))

    assert summary["dry_run"] is True
    assert summary["pendentes"] == 1
    assert not output_path.exists()
    assert (tmp_path / "embedding_report.json").exists()


def test_resume_parts_and_refresh_provenance_without_reembedding(tmp_path):
    input_path, output_path = tmp_path / "documents.jsonl", tmp_path / "embeddings.jsonl"
    _write_chunks(input_path, ["primeiro", "segundo"])
    calls = []
    encoding = {"checkpoint_sha256": "verified", "pooling": "last"}

    def interrupted(texts):
        calls.extend(texts)
        if texts == ["segundo"]:
            raise RuntimeError("interrupted")
        return [[1.0, 0.5]]

    with pytest.raises(RuntimeError, match="interrupted"):
        create_embeddings(input_path, output_path, batch_size=1, pause_seconds=0,
                          encoding_config=encoding, embed_batch=interrupted)
    assert list(tmp_path.glob("embeddings.part-*.jsonl"))
    resumed = []
    create_embeddings(input_path, output_path, batch_size=1, pause_seconds=0,
                      encoding_config=encoding, embed_batch=lambda texts: resumed.extend(texts) or [[1.0, 0.5]])
    assert resumed == ["segundo"]
    chunks = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines()]
    for chunk in chunks:
        chunk.update(manifest_hash="new-manifest", inventory_version="v5")
    input_path.write_text("".join(json.dumps(chunk) + "\n" for chunk in chunks), encoding="utf-8")
    summary = create_embeddings(input_path, output_path, pause_seconds=0, encoding_config=encoding,
                                embed_batch=lambda _: pytest.fail("unchanged encoding must reuse vectors"))
    assert summary["reutilizados"] == 2
    assert all(json.loads(line)["inventory_version"] == "v5" for line in output_path.read_text().splitlines())


def test_create_embeddings_does_not_reuse_cache_without_current_input_fingerprint(tmp_path):
    input_path = tmp_path / "documents.jsonl"
    output_path = tmp_path / "embeddings.jsonl"
    _write_chunks(input_path, ["texto"])
    output_path.write_text(
        json.dumps(
            {
                "content_sha256": "a" * 64,
                "embedding_model": "modelo-antigo",
                "embedding": [0.1],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.2] for _ in texts]

    summary = create_embeddings(input_path, output_path, pause_seconds=0, embed_batch=fake_embed)

    assert summary["pendentes"] == 1
    assert summary["gerados"] == 1
    assert calls == [["texto"]]


def test_create_embeddings_rejects_inconsistent_vector_dimensions(tmp_path):
    input_path = tmp_path / "documents.jsonl"
    output_path = tmp_path / "embeddings.jsonl"
    _write_chunks(input_path, ["primeiro", "segundo"])
    calls = 0

    def fake_embed(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[0.1, 0.2] if calls == 1 else [0.3, 0.4, 0.5] for _ in texts]

    with pytest.raises(RuntimeError, match="dimensões incompatíveis"):
        create_embeddings(
            input_path,
            output_path,
            batch_size=1,
            pause_seconds=0,
            embed_batch=fake_embed,
        )


def test_create_embeddings_rejects_legacy_metadata_contract(tmp_path):
    input_path = tmp_path / "documents.jsonl"
    output_path = tmp_path / "embeddings.jsonl"
    payload = {
        "chunk_id": "base:chunk-1",
        "text": "texto legado",
        "metadata": {"base_id": "exemplos_teste"},
    }
    input_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="registro legado"):
        create_embeddings(
            input_path,
            output_path,
            dry_run=True,
        )
