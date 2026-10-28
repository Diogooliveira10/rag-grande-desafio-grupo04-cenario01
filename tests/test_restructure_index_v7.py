"""Contrato do reestruturador V7 com cinco chunks e vetores locais de dimensão 2."""
import json

import pytest

from src.restructure_index_v7 import reestruturar


@pytest.fixture
def candidato(tmp_path):
    bases = [
        ("contratos_estruturado", "estruturada"),
        ("editais_normativo", "normativa"),
        ("atas_conversacional", "conversacional"),
        ("pca_agregado", "agregada"),
        ("emails_sinteticos", "conversacional"),
    ]
    documents = [
        {
            "chunk_id": f"{base_id}:1", "document_id": f"doc-{numero}",
            "base_id": base_id, "natureza": natureza,
            "text": f"Evidência de teste {numero}", "doc_type": "json",
            "source_file": f"fixture/{base_id}.json",
            "sensitivity": "restrito" if base_id == "emails_sinteticos" else "publico",
            "origem": "https://example.com/fonte", "data_vigencia": "2025-12-31",
            "sha256": f"origem-{numero}", "chunk_content_sha256": f"conteudo-{numero}",
            "inventory_version": "v7-fixture", "manifest_hash": "manifest-fixture",
            "status_extracao": "ok", "extras": {"orgao": "Órgão de teste", "valor": 100},
        }
        for numero, (base_id, natureza) in enumerate(bases, 1)
    ]
    documents_file = tmp_path / "documents.jsonl"
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    vectors = [
        {"chunk_id": document["chunk_id"], "embedding": [1.0, float(numero)]}
        for numero, document in enumerate(documents, 1)
    ]
    (index_dir / "vectors.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in reversed(vectors)), encoding="utf-8",
    )
    manifest = {
        "embedding_model": "fixture-model-not-q8", "dimension": 2,
        "encoding_config": {"projecao": "completa"}, "projecao_embedding": "completa",
    }
    (index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return documents_file, index_dir, documents, manifest


@pytest.mark.parametrize("aninhado", [False, True])
def test_preserva_contrato_metadados_modelo_e_vetores(candidato, aninhado):
    import faiss
    import numpy as np

    from src.index import _chunk_metadata
    from src.pipeline import _chunk_from_retrieval
    from src.retrieve import _result

    documents_file, index_dir, documents, manifest = candidato
    if aninhado:
        documents = [
            {**document, "metadata": _chunk_metadata(document)} for document in documents
        ]
    documents_file.write_text(
        "".join(json.dumps(record) + "\n" for record in documents), encoding="utf-8",
    )
    original_vectors = (index_dir / "vectors.jsonl").read_bytes()

    stats = reestruturar(index_dir, documents_file)

    assert len(stats) == 5
    assert (index_dir / "vectors.jsonl").read_bytes() == original_vectors
    for numero, document in enumerate(documents, 1):
        base_dir = index_dir / document["base_id"]
        record = json.loads((base_dir / "vectors.jsonl").read_text(encoding="utf-8"))
        assert record["metadata"] == _chunk_metadata(document)
        assert record["embedding"] == [1.0, float(numero)]
        chunk = _chunk_from_retrieval(_result(record, 1.0, document["text"]))
        assert chunk.metadata.extras == document["extras"]
        assert chunk.metadata.sensitivity.value == document["sensitivity"]
        assert chunk.metadata.source_file == document["source_file"]
        assert chunk.metadata.origem == document["origem"]
        assert chunk.metadata.inventory_version == "v7-fixture"
        assert chunk.metadata.data_vigencia.isoformat() == "2025-12-31"
        base_manifest = json.loads((base_dir / "manifest.json").read_text(encoding="utf-8"))
        for key, value in manifest.items():
            assert base_manifest[key] == value
        index = faiss.read_index(str(base_dir / "vectors.faiss"))
        assert index.ntotal == 1 and index.d == 2
        vector = np.array([1.0, float(numero)], dtype=np.float32)
        np.testing.assert_allclose(index.reconstruct(0), vector / np.linalg.norm(vector))


@pytest.mark.parametrize("campo", ["sensitivity", "source_file", "doc_type"])
def test_metadado_ausente_nao_sobrescreve_base_existente(candidato, campo):
    documents_file, index_dir, documents, _ = candidato
    documents[-1].pop(campo)
    documents_file.write_text(
        "".join(json.dumps(record) + "\n" for record in documents), encoding="utf-8",
    )
    base_dir = index_dir / documents[0]["base_id"]
    base_dir.mkdir()
    previous = base_dir / "vectors.jsonl"
    previous.write_bytes(b"artefato anterior\n")
    manifest_before = (index_dir / "manifest.json").read_bytes()

    with pytest.raises(ValueError, match=campo):
        reestruturar(index_dir, documents_file)

    assert previous.read_bytes() == b"artefato anterior\n"
    assert (index_dir / "manifest.json").read_bytes() == manifest_before
    assert not (index_dir / "emails_sinteticos").exists()
