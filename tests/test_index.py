import json
from pathlib import Path

import pytest

from src.index import REQUIRED_METADATA_FIELDS, build_index, read_normalized_chunks

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_INPUT = FIXTURES_DIR / "documents_sample.jsonl"


def _fake_embed_fn(dimension: int = 4):
    """Embeddings determinísticos e offline, só para testar a montagem do índice."""

    def embed(texts: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7 + 1)] * dimension for text in texts]

    return embed


def test_build_index_agrupa_por_base_e_grava_manifesto(tmp_path):
    output_dir = tmp_path / "index"

    manifest = build_index(SAMPLE_INPUT, output_dir, _fake_embed_fn(), model="fixture-model", batch_size=2)

    base_ids = {base["base_id"] for base in manifest["bases"]}
    assert base_ids == {"contratos_estruturado", "editais_normativo"}

    for base in manifest["bases"]:
        vectors_path = output_dir / base["base_id"] / "vectors.jsonl"
        assert vectors_path.exists()
        records = [json.loads(line) for line in vectors_path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == base["total_chunks"]
        assert all(len(record["embedding"]) == base["dimension"] for record in records)
        assert all(not (REQUIRED_METADATA_FIELDS - set(record["metadata"])) for record in records)

        base_manifest = json.loads((output_dir / base["base_id"] / "manifest.json").read_text(encoding="utf-8"))
        assert base_manifest == base

    assert (output_dir / "manifest.json").exists()


def test_build_index_ignora_chunks_sem_texto(tmp_path):
    output_dir = tmp_path / "index"

    manifest = build_index(SAMPLE_INPUT, output_dir, _fake_embed_fn(), model="fixture-model")

    total_chunks = sum(base["total_chunks"] for base in manifest["bases"])
    assert manifest["chunks_sem_texto_ignorados"] == 1
    assert total_chunks == 4


def test_build_index_e_deterministico_para_a_mesma_entrada(tmp_path):
    output_a = tmp_path / "a"
    output_b = tmp_path / "b"

    manifest_a = build_index(SAMPLE_INPUT, output_a, _fake_embed_fn(), model="fixture-model")
    manifest_b = build_index(SAMPLE_INPUT, output_b, _fake_embed_fn(), model="fixture-model")

    assert manifest_a["source_sha256"] == manifest_b["source_sha256"]
    assert [base["total_chunks"] for base in manifest_a["bases"]] == [
        base["total_chunks"] for base in manifest_b["bases"]
    ]


def test_todo_chunk_tem_metadado_obrigatorio():
    """O teste de ouro do tópico 2.5: todo chunk normalizado segue ChunkMetadata."""
    for chunk in read_normalized_chunks(SAMPLE_INPUT):
        faltando = REQUIRED_METADATA_FIELDS - set(chunk.metadata)
        assert not faltando, f"{chunk.chunk_id} sem: {faltando}"


def test_build_index_falha_rapido_com_metadado_obrigatorio_ausente(tmp_path):
    input_file = tmp_path / "documents_invalidos.jsonl"
    record = {
        "chunk_id": "editais_normativo:xyz:chunk-1",
        "document_id": "editais_normativo:xyz",
        "base_id": "editais_normativo",
        "text": "Edital sem doc_type no metadado.",
        "source_path": "data/raw/v1/edital_xyz.pdf",
        "sha256": "xyz",
        "metadata": {
            "source_file": "edital_xyz.pdf",
            "chunk_id": "editais_normativo:xyz:chunk-1",
            "base_id": "editais_normativo",
            # "doc_type" e "sensitivity" faltando de propósito
        },
    }
    input_file.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="metadado obrigatório ausente"):
        build_index(input_file, tmp_path / "index", _fake_embed_fn(), model="fixture-model")
