"""Caracterização de src/build_faiss_v7.py: o que a montagem do índice do candidato_05 grava.

O script não tinha teste. Estes fixam o comportamento (ordem dos vetores,
reaproveitamento v7/v6, embedding só dos faltantes, checkpoint, FAISS e
manifest) antes de dividir a ``main`` em funções.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from src import build_faiss_v7 as montar


def _escrever(path, linhas):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(linha, ensure_ascii=False) + "\n" for linha in linhas), encoding="utf-8")


def _vetor(texto):
    return [float(len(texto)), 1.0, 0.5, 2.0]


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    documentos = [
        {"chunk_id": "c1", "base_id": "contratos_estruturado", "text": "contrato um"},
        {"chunk_id": "c2", "base_id": "contratos_estruturado", "text": "contrato dois, sem vetor"},
        {"chunk_id": "n1", "base_id": "editais_normativo", "text": "lei"},
        {"chunk_id": "p1", "base_id": "pca_agregado", "text": "plano anual"},
        {"chunk_id": "n2", "base_id": "editais_normativo", "text": "decreto"},
    ]
    candidato = tmp_path / "candidato_05" / "documents.jsonl"
    _escrever(candidato, documentos)
    v7 = tmp_path / "v7_direto" / "contratos_estruturado" / "vectors.jsonl"
    _escrever(v7, [{"chunk_id": "c1", "embedding": [3.0, 0.0, 0.0, 4.0]}, {"chunk_id": "outro", "embedding": [1.0, 0, 0, 0]}])
    (v7.parent / "manifest.json").write_text(json.dumps({"embedding_model": "modelo-v7", "encoding_config": {"x": 1}}), encoding="utf-8")
    editais = tmp_path / "index" / "editais_normativo" / "vectors.jsonl"
    _escrever(editais, [{"chunk_id": "n1", "embedding": [0.0, 2.0, 0.0, 0.0]}, {"chunk_id": "n2", "embedding": [0.0, 0.0, 5.0, 0.0]}])
    (editais.parent / "manifest.json").write_text(json.dumps({"embedding_model": "modelo-v6"}), encoding="utf-8")
    pca = tmp_path / "index" / "pca_agregado" / "vectors.jsonl"  # sem manifest: o script tolera
    _escrever(pca, [])
    saida = tmp_path / "candidato_05" / "index"

    monkeypatch.setattr(montar, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(montar, "CANDIDATO_05_DOCS", candidato)
    monkeypatch.setattr(montar, "OUTPUT_DIR", saida)
    monkeypatch.setattr(montar, "V7_ORIGINAL_VECTORS", v7)
    monkeypatch.setattr(montar, "V6_VECTORS", {"editais_normativo": editais, "pca_agregado": pca})
    pedidos: list[list[str]] = []

    def embed_falso(textos, url="http://127.0.0.1:18321"):
        pedidos.append(list(textos))
        return [_vetor(t) for t in textos]

    monkeypatch.setattr(montar, "_embed_local", embed_falso)
    return {"saida": saida, "candidato": candidato, "documentos": documentos, "pedidos": pedidos, "tmp": tmp_path}


def test_monta_vetores_reaproveitados_e_embedados_faiss_e_manifest(ambiente):
    assert montar.main([]) == 0
    saida = ambiente["saida"]

    vetores = [json.loads(l) for l in (saida / "vectors.jsonl").read_text(encoding="utf-8").splitlines()]
    # Reaproveitados na ordem dos documentos; os embedados vêm depois.
    assert [v["chunk_id"] for v in vetores] == ["c1", "n1", "n2", "c2", "p1"]
    assert vetores[0]["embedding"] == [3.0, 0.0, 0.0, 4.0]
    assert vetores[3]["embedding"] == _vetor("contrato dois, sem vetor")
    assert ambiente["pedidos"] == [["contrato dois, sem vetor", "plano anual"]]
    assert (saida / "documents.jsonl").read_bytes() == ambiente["candidato"].read_bytes()

    import faiss
    indice = faiss.read_index(str(saida / "vectors.faiss"))
    esperados = np.array([[3.0, 0.0, 0.0, 4.0], _vetor("contrato dois, sem vetor"), [0.0, 2.0, 0.0, 0.0],
                          _vetor("plano anual"), [0.0, 0.0, 5.0, 0.0]], dtype=np.float32)
    faiss.normalize_L2(esperados)  # na ordem do documents.jsonl, normalizados
    assert indice.ntotal == 5
    assert np.allclose(np.vstack([indice.reconstruct(i) for i in range(5)]), esperados)

    manifest = json.loads((saida / "manifest.json").read_text(encoding="utf-8"))
    assert manifest.pop("built_at")
    v6 = manifest["proveniencia_vetores"]["v6"]
    assert v6["bases"]["editais_normativo"].pop("manifest").endswith("manifest.json")
    assert manifest == {
        "base_id": "candidato_05", "embedding_model": "mixed",
        "embedding_model_declarado_para_novos_vetores": "Qwen3-Embedding-0.6B.Q8_0",
        "proveniencia_vetores": {
            "v7_direto": {"vetores": 1, "embedding_model_declarado": "modelo-v7", "encoding_config": {"x": 1},
                          "manifest": str(ambiente["tmp"] / "v7_direto" / "contratos_estruturado" / "manifest.json")},
            "v6": {"vetores": 2, "bases": {
                "editais_normativo": {"vetores": 2, "embedding_model": "modelo-v6"},
                "pca_agregado": {"vetores": 0, "manifest": str(ambiente["tmp"] / "index" / "pca_agregado" / "manifest.json")},
            }},
            "novos_vetores": {"vetores": 2, "embedding_model_declarado": "Qwen3-Embedding-0.6B.Q8_0", "encoding_config": None},
            "homogeneidade_comprovada": False,
        },
        "encoding_verificado": False, "dimension": 1024, "faiss_index": "vectors.faiss",
        "total_chunks": 5, "total_vectors": 5, "source_file": str(Path("candidato_05") / "documents.jsonl"),
        "vetores_reaproveitados": 3, "vetores_embedados": 2, "projecao_embedding": "completa",
    }


def test_checkpoint_evita_embedar_de_novo(ambiente):
    checkpoint = ambiente["saida"] / "embed_checkpoint.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps({"p1": [9.0, 9.0, 9.0, 9.0]}), encoding="utf-8")

    assert montar.main([]) == 0

    assert ambiente["pedidos"] == [["contrato dois, sem vetor"]]
    vetores = {json.loads(l)["chunk_id"]: json.loads(l)["embedding"]
               for l in (ambiente["saida"] / "vectors.jsonl").read_text(encoding="utf-8").splitlines()}
    assert vetores["p1"] == [9.0, 9.0, 9.0, 9.0]
    manifest = json.loads((ambiente["saida"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["vetores_embedados"] == 2 and manifest["vetores_reaproveitados"] == 3


def test_dry_run_nao_embeda_nem_grava(ambiente):
    assert montar.main(["--dry-run"]) == 0
    assert ambiente["pedidos"] == [] and not ambiente["saida"].exists()
