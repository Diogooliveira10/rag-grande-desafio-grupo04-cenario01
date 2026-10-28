"""Bundle validation is read-only, path-contained, and distinct from liveness."""
from __future__ import annotations

import hashlib
import json

import faiss
import numpy as np
import pytest

from src import snapshot, structured_db
from tests.test_structured_db import _contratacao, _plano, escrever_documents


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    documents = escrever_documents(root / "documents.jsonl", contratacoes=[_contratacao("A-1")], planos=[_plano("P-1")])
    database = root / "facts.sqlite"
    banco = structured_db.Banco(sqlite_path=database)
    structured_db.construir_banco(documents, banco)

    names = sorted({row["nome"] for table in ("contratacoes", "pca_planos")
                    for column in ("orgao_nome", "unidade_nome")
                    for row in banco.consultar(f"SELECT DISTINCT {column} AS nome FROM {table} WHERE {column} IS NOT NULL")})
    municipalities = sorted({row["v"] for row in banco.consultar(
        "SELECT DISTINCT municipio AS v FROM contratacoes WHERE municipio IS NOT NULL")})
    suppliers = sorted({row["v"] for row in banco.consultar(
        "SELECT DISTINCT fornecedor AS v FROM contratacoes WHERE fornecedor IS NOT NULL")})
    catalog_data = {"nomes": names, "municipios": municipalities, "fornecedores": suppliers}
    catalog_data["fonte_sha256"] = hashlib.sha256(json.dumps(
        {"nomes": names, "municipios": municipalities, "fornecedores": suppliers},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps(catalog_data) + "\n", encoding="utf-8")

    index_root = root / "index"
    index_root.mkdir()
    grouped = {"contratos_estruturado": [], "pca_agregado": []}
    for line in documents.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        grouped[record["base_id"]].append(record)
    index_artifacts = []
    for base_id, records in grouped.items():
        base_dir = index_root / base_id
        base_dir.mkdir()
        base_docs = base_dir / "documents.jsonl"
        base_docs.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        base_vectors = base_dir / "vectors.jsonl"
        base_vectors.write_text("".join(json.dumps({"chunk_id": record["chunk_id"], "embedding": [1.0, 0.0]}) + "\n"
                                               for record in records), encoding="utf-8")
        faiss_index = faiss.IndexFlatIP(2)
        faiss_index.add(np.ones((len(records), 2), dtype="float32"))
        faiss.write_index(faiss_index, str(base_dir / "vectors.faiss"))
        submanifest = {
            "base_id": base_id, "source_sha256": _hash(base_docs),
            "total_chunks": len(records), "total_vectors": len(records),
            "homogeneidade_modelo_comprovada": True,
            "homogeneidade_configuracao_comprovada": True,
            "proveniencia_encoding_completa": True,
        }
        (base_dir / "manifest.json").write_text(json.dumps(submanifest) + "\n", encoding="utf-8")
        for name in ("manifest.json", "documents.jsonl", "vectors.jsonl", "vectors.faiss"):
            artifact = base_dir / name
            index_artifacts.append({"path": artifact.relative_to(root).as_posix(), "sha256": _hash(artifact)})

    index_manifest = index_root / "manifest.json"
    index_manifest.write_text(json.dumps({
        "source_sha256": _hash(documents), "total_chunks": 2, "total_vectors": 2,
        "bases": [{"base_id": base_id} for base_id in grouped],
        "homogeneidade_modelo_comprovada": True,
        "homogeneidade_configuracao_comprovada": True,
        "proveniencia_encoding_completa": True,
    }) + "\n", encoding="utf-8")
    files = {}
    for role, artifact in (("documents", documents), ("database", database),
                           ("catalog", catalog), ("index_manifest", index_manifest)):
        files[role] = {"path": artifact.relative_to(root).as_posix(), "sha256": _hash(artifact)}
    manifest = {
        "format": snapshot.BUNDLE_FORMAT,
        "snapshot_id": "fixture-v1",
        "sql_schema_version": structured_db.SCHEMA_VERSION,
        "documents_sha256": files["documents"]["sha256"],
        "files": files,
        "index_artifacts": index_artifacts,
        "capabilities": ["pca", "contratos"],
    }
    path = root / "bundle.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


def test_validate_bundle_accepts_coherent_immutable_sql_snapshot(tmp_path):
    path, _ = _manifest(tmp_path)

    assert snapshot.validate_bundle(path) == {
        "ready": True,
        "snapshot_id": "fixture-v1",
        "schema_version": structured_db.SCHEMA_VERSION,
        "capabilities": ["contratos", "pca"],
    }


def test_validate_bundle_rejects_file_hash_drift(tmp_path):
    path, _ = _manifest(tmp_path)
    (path.parent / "catalog.json").write_text('{"nomes": ["changed"]}\n', encoding="utf-8")

    with pytest.raises(snapshot.SnapshotValidationError, match="hash_divergente"):
        snapshot.validate_bundle(path)


def test_validate_bundle_rejects_artifact_path_escape(tmp_path):
    path, manifest = _manifest(tmp_path)
    manifest["files"]["catalog"]["path"] = "../outside.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(snapshot.SnapshotValidationError, match="caminho_fora_do_bundle"):
        snapshot.validate_bundle(path)


def test_validate_bundle_rejects_vector_gaps_and_incomplete_encoding(tmp_path):
    path, manifest = _manifest(tmp_path)
    index_dir = path.parent / "index"
    index_path = index_dir / "manifest.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["total_vectors"] = 1
    index_path.write_text(json.dumps(index) + "\n", encoding="utf-8")
    manifest["files"]["index_manifest"]["sha256"] = _hash(index_path)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(snapshot.SnapshotValidationError, match="vetores_ausentes"):
        snapshot.validate_bundle(path)


def test_validate_bundle_rejects_catalog_from_a_different_sql_snapshot(tmp_path):
    path, manifest = _manifest(tmp_path)
    catalog = path.parent / manifest["files"]["catalog"]["path"]
    data = json.loads(catalog.read_text(encoding="utf-8"))
    data["nomes"] = ["another snapshot"]
    catalog.write_text(json.dumps(data) + "\n", encoding="utf-8")
    manifest["files"]["catalog"]["sha256"] = _hash(catalog)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(snapshot.SnapshotValidationError, match="catalogo_divergente"):
        snapshot.validate_bundle(path)


def test_validate_bundle_rejects_wrong_declared_sql_schema(tmp_path):
    path, manifest = _manifest(tmp_path)
    manifest["sql_schema_version"] = "6"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(snapshot.SnapshotValidationError, match="schema_incompativel"):
        snapshot.validate_bundle(path)


def test_readiness_without_manifest_is_not_ready():
    assert snapshot.readiness("") == {"ready": False, "reason": "manifesto_nao_configurado"}


def _accept_historical_provenance(path, manifest):
    model = "Qwen3-Embedding-0.6B.Q8_0"
    flags = {key: False for key in (
        "homogeneidade_modelo_comprovada", "homogeneidade_configuracao_comprovada",
        "proveniencia_encoding_completa",
    )}
    for item in manifest["index_artifacts"]:
        if item["path"].endswith("manifest.json"):
            artifact = path.parent / item["path"]
            data = json.loads(artifact.read_text(encoding="utf-8"))
            data.update(flags, embedding_model=model)
            artifact.write_text(json.dumps(data), encoding="utf-8")
            item["sha256"] = _hash(artifact)
    index_path = path.parent / manifest["files"]["index_manifest"]["path"]
    data = json.loads(index_path.read_text(encoding="utf-8"))
    data.update(flags, snapshot_id=manifest["snapshot_id"], embedding_model=model, promocao_autorizada=True)
    index_path.write_text(json.dumps(data), encoding="utf-8")
    manifest["files"]["index_manifest"]["sha256"] = _hash(index_path)
    manifest["operator_acceptance"] = {
        "scope": "historical_embedding_provenance", "approved": True,
        "approved_by": "test-operator", "approved_at": "2026-10-27T00:00:00Z",
        "snapshot_id": manifest["snapshot_id"], "embedding_model": model,
        "index_manifest_sha256": _hash(index_path),
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_operator_acceptance_allows_exact_release_without_falsifying_proof(tmp_path):
    path, manifest = _manifest(tmp_path)
    _accept_historical_provenance(path, manifest)
    assert snapshot.validate_bundle(path)["ready"] is True
    index = json.loads((path.parent / "index/manifest.json").read_text(encoding="utf-8"))
    assert index["proveniencia_encoding_completa"] is False
    del manifest["operator_acceptance"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(snapshot.SnapshotValidationError, match="proveniencia_incompleta"):
        snapshot.validate_bundle(path)


@pytest.mark.parametrize("field,value", [
    ("approved", False), ("approved_by", ""), ("approved_at", ""),
    ("scope", "all_checks"), ("snapshot_id", "another-release"),
    ("embedding_model", "other-model"), ("index_manifest_sha256", "0" * 64),
])
def test_operator_acceptance_cannot_be_reused_for_another_release(tmp_path, field, value):
    path, manifest = _manifest(tmp_path)
    _accept_historical_provenance(path, manifest)
    manifest["operator_acceptance"][field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(snapshot.SnapshotValidationError, match="aceite_operador_invalido"):
        snapshot.validate_bundle(path)


@pytest.mark.parametrize("corruption", ["hash", "coverage", "model", "faiss"])
def test_operator_acceptance_does_not_bypass_integrity(tmp_path, corruption):
    path, manifest = _manifest(tmp_path)
    _accept_historical_provenance(path, manifest)
    if corruption == "hash":
        (path.parent / "index/contratos_estruturado/vectors.jsonl").write_text("{}\n", encoding="utf-8")
        expected = "hash_divergente"
    else:
        relative = "index/contratos_estruturado/manifest.json"
        artifact = path.parent / relative
        data = json.loads(artifact.read_text(encoding="utf-8"))
        if corruption == "coverage":
            data["total_vectors"] = 0
            expected = "vetores_ausentes"
        elif corruption == "model":
            data["embedding_model"] = "another-model"
            expected = "modelo_indice_divergente"
        else:
            relative = "index/contratos_estruturado/vectors.faiss"
            artifact = path.parent / relative
            faiss.write_index(faiss.IndexFlatIP(2), str(artifact))
            expected = "vetores_ausentes"
        if corruption != "faiss":
            artifact.write_text(json.dumps(data), encoding="utf-8")
        next(item for item in manifest["index_artifacts"] if item["path"] == relative)["sha256"] = _hash(artifact)
        path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(snapshot.SnapshotValidationError, match=expected):
        snapshot.validate_bundle(path)


def _contar_validacoes(monkeypatch):
    chamadas = []
    original = snapshot.validate_bundle
    monkeypatch.setattr(snapshot, "validate_bundle", lambda path: chamadas.append(path) or original(path))
    monkeypatch.setattr(snapshot, "_BUNDLES_VALIDOS", {})
    return chamadas


def test_readiness_valida_o_bundle_uma_vez_enquanto_os_arquivos_nao_mudam(tmp_path, monkeypatch):
    """O /ready relia ~290 mil linhas a cada chamada (2026-10-27): o resultado válido fica guardado."""
    path, _ = _manifest(tmp_path)
    chamadas = _contar_validacoes(monkeypatch)

    primeira = snapshot.readiness(path)
    primeira["capabilities"].append("alterada pelo chamador")
    assert snapshot.readiness(path) == {"ready": True, "snapshot_id": "fixture-v1",
                                        "schema_version": structured_db.SCHEMA_VERSION,
                                        "capabilities": ["contratos", "pca"]}
    assert len(chamadas) == 1


def test_readiness_valida_de_novo_quando_um_arquivo_muda(tmp_path, monkeypatch):
    path, _ = _manifest(tmp_path)
    chamadas = _contar_validacoes(monkeypatch)
    assert snapshot.readiness(path)["ready"] is True

    vetores = tmp_path / "bundle" / "index" / "pca_agregado" / "vectors.jsonl"
    vetores.write_text(vetores.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert snapshot.readiness(path) == {"ready": False, "reason": "hash_divergente"}
    assert snapshot.readiness(path) == {"ready": False, "reason": "hash_divergente"}
    assert len(chamadas) == 3  # falha não fica guardada


def test_readiness_nao_abre_segunda_validacao_simultanea(tmp_path, monkeypatch):
    path, _ = _manifest(tmp_path)
    chamadas = _contar_validacoes(monkeypatch)
    with snapshot._VALIDACAO_EM_ANDAMENTO:
        assert snapshot.readiness(path) == {"ready": False, "reason": "validacao_em_andamento"}
    assert chamadas == []
    assert snapshot.readiness(path)["ready"] is True
