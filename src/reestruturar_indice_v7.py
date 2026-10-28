"""Reestrutura o índice v7 (FAISS flat) para o layout por base que o retrieve.py espera.

Layout de entrada (candidato_05/index/):
  documents.jsonl, vectors.jsonl, vectors.faiss, manifest.json

Layout de saída:
  candidato_05/index/manifest.json (global)
  candidato_05/index/atas_conversacional/documents.jsonl, vectors.jsonl, vectors.faiss, manifest.json
  candidato_05/index/contratos_estruturado/...
  candidato_05/index/editais_normativo/...
  candidato_05/index/pca_agregado/...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.index import REQUIRED_METADATA_FIELDS, _chunk_metadata, _relative, _write_faiss_index
from src.schema import ChunkMetadata


def reestruturar(
    index_dir: Path,
    documents_file: Path,
) -> dict[str, Any]:
    global_manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    chunk_base, por_base = _agrupar_documentos(documents_file)
    por_base_vecs = _agrupar_vetores(index_dir / "vectors.jsonl", chunk_base)

    # 3. Create subdirectories
    stats: dict[str, Any] = {}
    for base_id, chunks in por_base.items():
        stats[base_id] = _gravar_base(index_dir, base_id, chunks, por_base_vecs.get(base_id, []),
                                      global_manifest, documents_file)

    # 4. Update global manifest
    global_manifest["bases"] = [
        {"base_id": bid} for bid in sorted(stats.keys())
    ]
    (index_dir / "manifest.json").write_text(
        json.dumps(global_manifest, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )

    return stats


def _agrupar_documentos(documents_file: Path) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    """1. chunk_id -> base_id e os chunks de cada base, validando os metadados antes de gravar qualquer base."""
    chunk_base: dict[str, str] = {}
    por_base: dict[str, list[dict[str, Any]]] = {}
    with documents_file.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            chunk = json.loads(line)
            # Não deixar o default legado do adaptador transformar classificação
            # perdida em "publico". Validar antes de sobrescrever qualquer base.
            declared = chunk.get("metadata", chunk)
            if not declared.get("sensitivity"):
                raise ValueError(f"chunk {chunk.get('chunk_id')} sem sensitivity explícita")
            metadata = _chunk_metadata(chunk)
            missing = {key for key in REQUIRED_METADATA_FIELDS if not metadata.get(key)}
            if missing:
                raise ValueError(f"chunk {chunk.get('chunk_id')} sem metadado obrigatório: {sorted(missing)}")
            ChunkMetadata.model_validate(metadata)
            base_id = chunk.get("base_id", "unknown")
            cid = chunk["chunk_id"]
            chunk_base[cid] = base_id
            por_base.setdefault(base_id, []).append(chunk)
    return chunk_base, por_base


def _agrupar_vetores(vectors_path: Path, chunk_base: dict[str, str]) -> dict[str, list[tuple[str, list[float]]]]:
    """2. Vetores do índice plano agrupados por base (lidos em fluxo, sem carregar o arquivo inteiro)."""
    por_base_vecs: dict[str, list[tuple[str, list[float]]]] = {}
    with vectors_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            cid = r.get("chunk_id", "")
            emb = r.get("embedding", [])
            base_id = chunk_base.get(cid)
            if base_id and cid and emb:
                por_base_vecs.setdefault(base_id, []).append((cid, emb))
    return por_base_vecs


def _gravar_base(index_dir: Path, base_id: str, chunks: list[dict[str, Any]], vecs: list[tuple[str, list[float]]],
                 global_manifest: dict[str, Any], documents_file: Path) -> dict[str, int]:
    """Documentos, vetores, FAISS e manifest de uma base no layout por base; devolve as contagens."""
    base_dir = index_dir / base_id
    base_dir.mkdir(parents=True, exist_ok=True)

    # Documents
    docs_path = base_dir / "documents.jsonl"
    with docs_path.open("w", encoding="utf-8", newline="\n") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")

    # Vectors — copy full record from global vectors, not just embedding
    vecs_path = base_dir / "vectors.jsonl"
    # Build lookup: chunk_id from documents → full metadata
    doc_meta: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        doc_meta[chunk["chunk_id"]] = chunk
    with vecs_path.open("w", encoding="utf-8", newline="\n") as f:
        for cid, emb in vecs:
            doc = doc_meta.get(cid, {})
            record = {
                "chunk_id": cid,
                "document_id": doc.get("document_id", ""),
                "base_id": base_id,
                "embedding": emb,
                "source_file": doc.get("source_file", ""),
                "sha256": doc.get("sha256", ""),
                "metadata": _chunk_metadata(doc),
            }
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    # FAISS
    if vecs:
        _write_faiss_index(base_dir / "vectors.faiss", [emb for _, emb in vecs])

    # Manifest
    manifest = {
        "base_id": base_id,
        "embedding_model": global_manifest["embedding_model"],
        "dimension": global_manifest["dimension"],
        "faiss_index": "vectors.faiss",
        "total_chunks": len(chunks),
        "total_vectors": len(vecs),
        "source_file": _relative(documents_file),
    }
    for key in ("encoding_config", "embedding_provider", "embedding_endpoint", "projecao_embedding"):
        if key in global_manifest:
            manifest[key] = global_manifest[key]
    (base_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"  {base_id}: {len(chunks)} chunks, {len(vecs)} vetores", flush=True)
    return {"chunks": len(chunks), "vetores": len(vecs)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    args = parser.parse_args(argv)

    print(f"Reestruturando {args.index_dir}...")
    stats = reestruturar(args.index_dir, args.documents)
    total_chunks = sum(s["chunks"] for s in stats.values())
    total_vetores = sum(s["vetores"] for s in stats.values())
    print(f"\nTotal: {total_chunks} chunks, {total_vetores} vetores em {len(stats)} bases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())