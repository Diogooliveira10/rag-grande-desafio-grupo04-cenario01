"""Monta o índice FAISS do candidato_05 reutilizando vetores existentes.

Reaproveita vetores de contratos do índice v7 original e normativas/PCA da v6.
O modelo declarado nas fontes é misto (manifest v7 original e manifests v6); os
chunks faltantes são embedados no servidor local esperado Q8. O manifesto de saída
registra contagens e configurações de cada origem, sem declarar o índice inteiro Q8.

Saída: data/v7_recuperacao/2026-10-15/candidato_05/index/
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Fontes de vetores reaproveitáveis
V7_ORIGINAL_VECTORS = PROJECT_ROOT / "data" / "v7_direto" / "index" / "contratos_estruturado" / "vectors.jsonl"
V6_VECTORS = {
    "editais_normativo": PROJECT_ROOT / "index" / "editais_normativo" / "vectors.jsonl",
    "pca_agregado": PROJECT_ROOT / "index" / "pca_agregado" / "vectors.jsonl",
}
CANDIDATO_05_DOCS = PROJECT_ROOT / "data" / "v7_recuperacao" / "2026-10-15" / "candidato_05" / "documents.jsonl"
OUTPUT_DIR = PROJECT_ROOT / "data" / "v7_recuperacao" / "2026-10-15" / "candidato_05" / "index"


def _load_vector_map(path: Path) -> dict[str, list[float]]:
    """Carrega mapa chunk_id → embedding de um vectors.jsonl."""
    mapa: dict[str, list[float]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            chunk_id = record.get("chunk_id", "")
            embedding = record.get("embedding", [])
            if chunk_id and embedding:
                mapa[chunk_id] = embedding
    return mapa


def _load_documents_by_base(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Agrupa chunks por base_id."""
    por_base: dict[str, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            chunk = json.loads(line)
            base_id = chunk.get("base_id", "unknown")
            por_base.setdefault(base_id, []).append(chunk)
    return por_base


def _embed_local(texts: list[str], url: str = "http://127.0.0.1:18321") -> list[list[float]]:
    """Embeda textos usando o servidor local (llama-server)."""
    import httpx

    response = httpx.post(
        f"{url}/v1/embeddings",
        json={"input": texts},
        timeout=600,
    )
    response.raise_for_status()
    data = response.json()
    return [item["embedding"] for item in data["data"]]


def _embed_concurrent(
    all_chunks: list[dict[str, Any]],
    batch_size: int = 64,
    workers: int = 3,
    url: str = "http://127.0.0.1:18321",
    checkpoint_path: Path | None = None,
    already_done: dict[str, list[float]] | None = None,
) -> dict[str, list[float]]:
    """Embeda chunks em paralelo usando ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result: dict[str, list[float]] = dict(already_done or {})
    todo_chunks = [c for c in all_chunks if c["chunk_id"] not in result]
    if not todo_chunks:
        print(f"  Todos já embedados ({len(result)})")
        return result

    batches = [
        todo_chunks[i:i + batch_size]
        for i in range(0, len(todo_chunks), batch_size)
    ]
    total = len(todo_chunks)
    done = 0
    errors = 0
    t0 = time.time()

    def process_batch(batch: list[dict[str, Any]]) -> dict[str, list[float]]:
        texts = [c.get("text", "") for c in batch]
        chunk_ids = [c["chunk_id"] for c in batch]
        embeddings = _embed_local(texts, url)
        return dict(zip(chunk_ids, embeddings))

    def save_checkpoint() -> None:
        if checkpoint_path:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(
                json.dumps(result, ensure_ascii=False),
                encoding="utf-8",
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_batch, b): b for b in batches}
        for future in as_completed(futures):
            try:
                batch_result = future.result()
                result.update(batch_result)
                done += len(batch_result)
            except Exception as e:
                errors += 1
                batch = futures[future]
                print(f"  ERRO lote ({len(batch)} chunks): {e}")
            # Report + checkpoint every 128 texts
            if done % 128 < batch_size or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(f"  {done}/{total} ({rate:.1f}/s, ETA {eta/60:.0f}min, erros={errors})")
                save_checkpoint()

    save_checkpoint()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    print("=== Montagem FAISS v7 (candidato_05) ===\n")

    # 1. Carregar documentos do candidato_05
    print("Carregando documentos do candidato_05...")
    docs_por_base = _load_documents_by_base(CANDIDATO_05_DOCS)
    for base_id, chunks in docs_por_base.items():
        print(f"  {base_id}: {len(chunks)} chunks")
    total_chunks = sum(len(chunks) for chunks in docs_por_base.values())
    print(f"  Total: {total_chunks} chunks\n")

    fontes = _carregar_vetores_reaproveitaveis()
    reaproveitados, faltantes_por_base, reused_v7, reused_v6 = _classificar_chunks(
        docs_por_base, fontes["v7_map"], fontes["v6_map"])
    if args.dry_run:
        print("DRY RUN — nada será feito.")
        return 0

    embedados = _embedar_faltantes(faltantes_por_base, reaproveitados)
    written = _gravar_vetores_e_documentos(reaproveitados)
    index = _construir_faiss(reaproveitados)
    _gravar_manifest(fontes, reused_v7=reused_v7, reused_v6=reused_v6, embedados=embedados,
                     reaproveitados=reaproveitados, written=written, total_vectors=index.ntotal)

    print("\n=== Concluído ===")
    return 0


def _carregar_vetores_reaproveitaveis() -> dict[str, Any]:
    """2. Vetores da v7 original (contratos) e da v6 (normativas e PCA), com os manifests de origem."""
    print("Carregando vetores reaproveitáveis...")

    # v7 original: contratos
    v7_map = _load_vector_map(V7_ORIGINAL_VECTORS)
    v7_manifest_path = V7_ORIGINAL_VECTORS.parent / "manifest.json"
    v7_manifest = json.loads(v7_manifest_path.read_text(encoding="utf-8")) if v7_manifest_path.exists() else {}
    print(f"  v7 original (contratos): {len(v7_map)} vetores")

    # v6: normativas e PCA
    v6_map: dict[str, list[float]] = {}
    v6_sources: dict[str, dict[str, Any]] = {}
    for base_id, path in V6_VECTORS.items():
        if path.exists():
            m = _load_vector_map(path)
            v6_map.update(m)
            source_manifest_path = path.parent / "manifest.json"
            source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8")) if source_manifest_path.exists() else {}
            v6_sources[base_id] = {"manifest": str(source_manifest_path), **source_manifest}
            print(f"  v6 ({base_id}): {len(m)} vetores")
    return {"v7_map": v7_map, "v7_manifest": v7_manifest, "v7_manifest_path": v7_manifest_path,
            "v6_map": v6_map, "v6_sources": v6_sources}


def _classificar_chunks(docs_por_base: dict[str, list[dict[str, Any]]], v7_map: dict[str, list[float]],
                        v6_map: dict[str, list[float]]) -> tuple[dict[str, list[float]], dict[str, list[dict[str, Any]]], int, dict[str, int]]:
    """3. (reaproveitados, faltantes por base, reaproveitados da v7, reaproveitados da v6 por base)."""
    reaproveitados: dict[str, list[float]] = {}  # chunk_id → embedding
    faltantes_por_base: dict[str, list[dict[str, Any]]] = {}
    reused_v7 = 0
    reused_v6: dict[str, int] = {base_id: 0 for base_id in V6_VECTORS}

    for base_id, chunks in docs_por_base.items():
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            if chunk_id in v7_map:
                reaproveitados[chunk_id] = v7_map[chunk_id]
                reused_v7 += 1
            elif chunk_id in v6_map:
                reaproveitados[chunk_id] = v6_map[chunk_id]
                reused_v6[base_id] = reused_v6.get(base_id, 0) + 1
            else:
                faltantes_por_base.setdefault(base_id, []).append(chunk)

    print(f"\n  Reaproveitados: {len(reaproveitados)}")
    for base_id, chunks in faltantes_por_base.items():
        print(f"  Faltantes ({base_id}): {len(chunks)}")
    total_faltantes = sum(len(c) for c in faltantes_por_base.values())
    print(f"  Total faltantes: {total_faltantes}\n")
    return reaproveitados, faltantes_por_base, reused_v7, reused_v6


def _embedar_faltantes(faltantes_por_base: dict[str, list[dict[str, Any]]],
                       reaproveitados: dict[str, list[float]]) -> dict[str, list[float]]:
    """4. Embeda os faltantes no servidor local, retomando do checkpoint; os novos entram em ``reaproveitados``."""
    print("Embedando faltantes localmente...")
    todos_faltantes = []
    for chunks in faltantes_por_base.values():
        todos_faltantes.extend(chunks)

    checkpoint_path = OUTPUT_DIR / "embed_checkpoint.json"
    embedados: dict[str, list[float]] = {}
    if checkpoint_path.exists():
        try:
            ckpt = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            for cid, emb in ckpt.items():
                embedados[cid] = emb
                reaproveitados[cid] = emb
            print(f"  Checkpoint: {len(embedados)} vetores recuperados")
        except Exception:
            pass

    restantes = [c for c in todos_faltantes if c["chunk_id"] not in embedados]
    print(f"  Restantes: {len(restantes)} (já embedados: {len(embedados)})")
    t0 = time.time()
    novos = _embed_concurrent(
        restantes, batch_size=64, workers=2,
        checkpoint_path=checkpoint_path, already_done=embedados,
    )
    embedados.update(novos)
    reaproveitados.update(novos)
    elapsed = time.time() - t0
    print(f"\n  Embedados: {len(novos)} em {elapsed/60:.1f}min ({len(novos)/elapsed:.1f}/s)")
    return embedados


def _gravar_vetores_e_documentos(reaproveitados: dict[str, list[float]]) -> int:
    """5. vectors.jsonl unificado e cópia do documents.jsonl; devolve quantos vetores foram gravados."""
    print("\nMontando vectors.jsonl unificado...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    vectors_path = OUTPUT_DIR / "vectors.jsonl"
    documents_path = OUTPUT_DIR / "documents.jsonl"

    written = 0
    with vectors_path.open("w", encoding="utf-8", newline="\n") as vf:
        for chunk_id, embedding in reaproveitados.items():
            record = {
                "chunk_id": chunk_id,
                "embedding": embedding,
            }
            vf.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            written += 1

    # Copiar documents.jsonl
    import shutil
    shutil.copy2(CANDIDATO_05_DOCS, documents_path)

    print(f"  vectors.jsonl: {written} vetores")
    print(f"  documents.jsonl: copiado de candidato_05")
    return written


def _construir_faiss(reaproveitados: dict[str, list[float]]) -> Any:
    """6. Índice FAISS (cosseno) com os vetores na ordem do documents.jsonl."""
    print("\nConstruindo FAISS...")
    import numpy as np
    import faiss

    # Carregar todos os vetores na ordem do documents.jsonl
    vetores_np = []
    with CANDIDATO_05_DOCS.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            chunk = json.loads(line)
            chunk_id = chunk["chunk_id"]
            emb = reaproveitados.get(chunk_id)
            if emb is None:
                print(f"  AVISO: {chunk_id} sem vetor!")
                continue
            vetores_np.append(emb)

    vetores_np = np.array(vetores_np, dtype=np.float32)
    print(f"  Matriz: {vetores_np.shape}")

    # Normalizar para cosine similarity (Inner Product)
    faiss.normalize_L2(vetores_np)

    # IndexFlatIP para busca por similaridade cosseno
    index = faiss.IndexFlatIP(vetores_np.shape[1])
    index.add(vetores_np)

    faiss_path = OUTPUT_DIR / "vectors.faiss"
    faiss.write_index(index, str(faiss_path))
    print(f"  FAISS: {index.ntotal} vetores, {faiss_path.stat().st_size // 1024 // 1024}MB")
    return index


def _gravar_manifest(fontes: dict[str, Any], *, reused_v7: int, reused_v6: dict[str, int],
                     embedados: dict[str, list[float]], reaproveitados: dict[str, list[float]],
                     written: int, total_vectors: int) -> None:
    # 7. Manifest: declarar mistura de fontes, não só o modelo do encoder local.
    v7_manifest, v7_manifest_path, v6_sources = fontes["v7_manifest"], fontes["v7_manifest_path"], fontes["v6_sources"]
    manifest = {
        "base_id": "candidato_05",
        "embedding_model": "mixed",
        "embedding_model_declarado_para_novos_vetores": "Qwen3-Embedding-0.6B.Q8_0",
        "proveniencia_vetores": {
            "v7_direto": {
                "vetores": reused_v7,
                "embedding_model_declarado": v7_manifest.get("embedding_model"),
                "encoding_config": v7_manifest.get("encoding_config"),
                "manifest": str(v7_manifest_path),
            },
            "v6": {
                "vetores": sum(reused_v6.values()),
                "bases": {base_id: {"vetores": count, **v6_sources.get(base_id, {})}
                          for base_id, count in reused_v6.items()},
            },
            "novos_vetores": {
                "vetores": len(embedados),
                "embedding_model_declarado": "Qwen3-Embedding-0.6B.Q8_0",
                "encoding_config": None,
            },
            "homogeneidade_comprovada": False,
        },
        "encoding_verificado": False,
        "dimension": 1024,
        "faiss_index": "vectors.faiss",
        "total_chunks": written,
        "total_vectors": total_vectors,
        "source_file": str(CANDIDATO_05_DOCS.relative_to(PROJECT_ROOT)),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "vetores_reaproveitados": len(reaproveitados) - len(embedados),
        "vetores_embedados": len(embedados),
        "projecao_embedding": "completa",
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"\nManifest: {manifest_path}")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())