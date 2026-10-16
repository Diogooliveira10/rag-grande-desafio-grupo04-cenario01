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
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def reestruturar(
    index_dir: Path,
    documents_file: Path,
) -> dict[str, Any]:
    import faiss
    import gc

    # 1. Build chunk_id → base_id map from documents
    chunk_base: dict[str, str] = {}
    por_base: dict[str, list[dict[str, Any]]] = {}
    with documents_file.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            chunk = json.loads(line)
            base_id = chunk.get("base_id", "unknown")
            cid = chunk["chunk_id"]
            chunk_base[cid] = base_id
            por_base.setdefault(base_id, []).append(chunk)

    # 2. Load vectors and group by base_id (streaming to avoid OOM)
    vectors_path = index_dir / "vectors.jsonl"
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

    # 3. Create subdirectories
    stats: dict[str, Any] = {}
    for base_id, chunks in por_base.items():
        base_dir = index_dir / base_id
        base_dir.mkdir(parents=True, exist_ok=True)

        # Documents
        docs_path = base_dir / "documents.jsonl"
        with docs_path.open("w", encoding="utf-8", newline="\n") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")

        # Vectors — copy full record from global vectors, not just embedding
        vecs = por_base_vecs.get(base_id, [])
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
                    "metadata": {
                        "chunk_id": cid,
                        "document_id": doc.get("document_id", ""),
                        "base_id": base_id,
                        "natureza": doc.get("natureza", ""),
                        "escopo": "oficial",
                        "data_vigencia": doc.get("data_vigencia", ""),
                        "status_extracao": doc.get("status_extracao", ""),
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

        # FAISS
        if vecs:
            mat = np.array([emb for _, emb in vecs], dtype=np.float32)
            faiss.normalize_L2(mat)
            idx = faiss.IndexFlatIP(mat.shape[1])
            idx.add(mat)
            faiss.write_index(idx, str(base_dir / "vectors.faiss"))
            del mat, idx
            gc.collect()

        # Manifest
        manifest = {
            "base_id": base_id,
            "embedding_model": "Qwen3-Embedding-0.6B.Q8_0",
            "dimension": 1024,
            "faiss_index": "vectors.faiss",
            "total_chunks": len(chunks),
            "total_vectors": len(vecs),
            "source_file": str(Path(documents_file).resolve().relative_to(PROJECT_ROOT)),
        }
        (base_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        stats[base_id] = {"chunks": len(chunks), "vetores": len(vecs)}
        print(f"  {base_id}: {len(chunks)} chunks, {len(vecs)} vetores", flush=True)

    # 4. Update global manifest
    global_manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    global_manifest["bases"] = [
        {"base_id": bid} for bid in sorted(stats.keys())
    ]
    (index_dir / "manifest.json").write_text(
        json.dumps(global_manifest, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )

    return stats


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