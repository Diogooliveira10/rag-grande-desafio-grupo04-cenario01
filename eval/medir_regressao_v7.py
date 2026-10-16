"""Regressão v6→v7 otimizada: pré-carrega vetores uma vez."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from eval.ablacao_rerank import (  # noqa: E402
    CasoRecuperacao,
    carregar_casos,
    e_relevante,
    resumir,
)
from src.retrieve import RetrievalResult  # noqa: E402

EmbedFn = Callable[[list[str]], list[list[float]]]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def _preload_index(index_dir: Path) -> dict[str, dict[str, Any]]:
    """Pré-carrega vetores e FAISS por base."""
    import faiss

    bases: dict[str, dict[str, Any]] = {}
    for base_dir in sorted(p for p in index_dir.iterdir() if p.is_dir()):
        faiss_path = base_dir / "vectors.faiss"
        vecs_path = base_dir / "vectors.jsonl"
        if not faiss_path.exists() or not vecs_path.exists():
            continue

        records = _read_jsonl(vecs_path)
        chunk_ids = [r["chunk_id"] for r in records]
        cid_to_idx = {cid: i for i, cid in enumerate(chunk_ids)}
        idx = faiss.read_index(str(faiss_path))

        bases[base_dir.name] = {
            "records": records,
            "chunk_ids": chunk_ids,
            "cid_to_idx": cid_to_idx,
            "faiss": idx,
        }
    return bases


def _to_retrieval_results(
    query_vector: list[float],
    query: str,
    bases_preloaded: dict[str, dict[str, Any]],
    documents_by_cid: dict[str, dict[str, Any]],
    base_filter: set[str] | None,
    top_k: int = 10,
    hybrid: bool = False,
) -> list[RetrievalResult]:
    """FAISS search (e opcionalmente BM25 + RRF) e converte para RetrievalResult."""
    import faiss

    qvec = np.array([query_vector], dtype=np.float32)
    faiss.normalize_L2(qvec)

    all_results: list[tuple[float, str, dict[str, Any]]] = []

    for base_name, data in bases_preloaded.items():
        if base_filter is not None and base_name not in base_filter:
            continue

        idx = data["faiss"]
        if idx.ntotal == 0:
            continue

        k = min(top_k * 3, idx.ntotal)
        scores, indices = idx.search(qvec, k)

        for score, idx_pos in zip(scores[0], indices[0]):
            if idx_pos < 0 or idx_pos >= len(data["chunk_ids"]):
                continue
            cid = data["chunk_ids"][idx_pos]
            all_results.append((float(score), cid, data["records"][idx_pos]))

    all_results.sort(key=lambda x: x[0], reverse=True)

    dense_results: list[RetrievalResult] = []
    seen: set[str] = set()
    for score, cid, record in all_results:
        if cid in seen:
            continue
        seen.add(cid)
        doc = documents_by_cid.get(cid, {})
        dense_results.append(RetrievalResult(
            chunk_id=cid,
            content=str(doc.get("text", "")),
            metadata=record.get("metadata", {}),
            score=score,
            dense_score=score,
            document_id=record.get("document_id"),
            source_path=record.get("source_file"),
            _record=record,
        ))
        if len(dense_results) >= top_k * 3:
            break

    if not hybrid:
        return dense_results[:top_k]

    # BM25 using the real implementation from retrieve.py
    from src.retrieve import bm25_retrieve
    bm25_records = []
    for base_name, data in bases_preloaded.items():
        if base_filter is not None and base_name not in base_filter:
            continue
        for record in data["records"]:
            doc = documents_by_cid.get(record["chunk_id"], {})
            merged = dict(record)
            merged["text"] = doc.get("text", "")
            merged["base_id"] = base_name
            bm25_records.append(merged)
    bm25_results = bm25_retrieve(
        query, top_k=top_k * 3,
        records=bm25_records, escopos=("oficial",),
    )
    from src.retrieve import reciprocal_rank_fusion
    fused = reciprocal_rank_fusion([dense_results, bm25_results], top_k=top_k)
    return fused


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v6-index", type=Path, required=True)
    parser.add_argument("--v6-documents", type=Path, required=True)
    parser.add_argument("--v7-index", type=Path, required=True)
    parser.add_argument("--v7-documents", type=Path, required=True)
    parser.add_argument("--casos", type=Path, default=PROJECT_ROOT / "eval" / "retrieval_set.jsonl")
    parser.add_argument("--saida", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--hybrid", action="store_true", help="Usar BM25 + RRF ao invés de só dense")
    args = parser.parse_args(argv)

    from src.create_embeddings import _configured_embed_batch

    provider = config.EMBEDDING_PROVIDER
    endpoint, api_key, modelo = config.embedding_endpoint(provider)
    embed_fn = _configured_embed_batch(provider, endpoint, api_key, modelo)

    casos = carregar_casos(args.casos)

    # Pre-load
    print("Pré-carregando v6...", flush=True)
    t0 = time.time()
    v6_bases = _preload_index(args.v6_index)
    v6_docs = {r["chunk_id"]: r for r in _read_jsonl(args.v6_documents)}
    print(f"  v6: {sum(d['faiss'].ntotal for d in v6_bases.values())} vetores, {time.time()-t0:.1f}s", flush=True)

    print("Pré-carregando v7...", flush=True)
    t0 = time.time()
    v7_bases = _preload_index(args.v7_index)
    v7_docs = {r["chunk_id"]: r for r in _read_jsonl(args.v7_documents)}
    print(f"  v7: {sum(d['faiss'].ntotal for d in v7_bases.values())} vetores, {time.time()-t0:.1f}s", flush=True)

    # Map natureza → base_ids
    from src.retrieve import _manifest_bases, _split_selection
    declarations = _manifest_bases()

    def bases_to_base_ids(bases_filter) -> set[str] | None:
        if bases_filter is None:
            return None
        selection = _split_selection(bases_filter)
        naturezas, base_ids = selection
        result = set(base_ids)
        for bid, decl in declarations.items():
            if decl.get("natureza") in naturezas:
                result.add(bid)
        return result

    # Run cases
    print(f"\nRodando {len(casos)} casos...", flush=True)
    pos_v6: list[Optional[int]] = []
    pos_v7: list[Optional[int]] = []
    por_caso: list[dict[str, Any]] = []

    t_total = time.time()
    for i, caso in enumerate(casos):
        t0 = time.time()
        qvec = embed_fn([caso.pergunta])[0]
        base_filter = bases_to_base_ids(caso.bases)

        r6 = _to_retrieval_results(qvec, caso.pergunta, v6_bases, v6_docs, base_filter, args.top_k, hybrid=False)
        p6 = None
        for pos, res in enumerate(r6[:args.top_k], 1):
            if e_relevante(caso, res):
                p6 = pos
                break

        r7 = _to_retrieval_results(qvec, caso.pergunta, v7_bases, v7_docs, base_filter, args.top_k, hybrid=args.hybrid)
        p7 = None
        for pos, res in enumerate(r7[:args.top_k], 1):
            if e_relevante(caso, res):
                p7 = pos
                break

        pos_v6.append(p6)
        pos_v7.append(p7)
        por_caso.append({
            "id": caso.id,
            "pergunta": caso.pergunta[:80],
            "pos_v6": p6,
            "pos_v7": p7,
        })

        status = "✓" if p7 == p6 else ("↓" if p7 is None or (p6 is not None and p7 > p6) else "↑")
        elapsed = time.time() - t0
        if (i + 1) % 5 == 0 or i == len(casos) - 1:
            print(f"  {i+1}/{len(casos)} ({elapsed:.1f}s) {status}", flush=True)

    elapsed_total = time.time() - t_total
    print(f"\nTotal: {elapsed_total:.0f}s ({elapsed_total/len(casos):.1f}s/caso)", flush=True)

    # Summarize
    regressoes = [c for c in por_caso if (c["pos_v7"] is None and c["pos_v6"] is not None)
                  or (c["pos_v6"] is not None and c["pos_v7"] is not None and c["pos_v7"] > c["pos_v6"])]
    melhorias = [c for c in por_caso if (c["pos_v7"] is not None and c["pos_v6"] is None)
                 or (c["pos_v6"] is not None and c["pos_v7"] is not None and c["pos_v7"] < c["pos_v6"])]

    relatorio = {
        "total": len(casos),
        "resumos": {
            "v6": resumir(pos_v6),
            "v7": resumir(pos_v7),
        },
        "regressoes": regressoes,
        "melhorias": melhorias,
        "empates": len(casos) - len(regressoes) - len(melhorias),
        "por_caso": por_caso,
    }

    args.saida.mkdir(parents=True, exist_ok=True)
    (args.saida / "report.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # Markdown report
    chaves = ("hit@1", "hit@3", "hit@5", "hit@10", "mrr@10")
    rotulos = ["v6", "v7"]
    linhas = [
        "# Regressão de recuperação — v6 × v7",
        "",
        f"{len(casos)} casos · {elapsed_total:.0f}s total",
        "",
        "| corpus | " + " | ".join(chaves) + " |",
        "| --- | " + " | ".join("---" for _ in chaves) + " |",
    ]
    for rotulo in rotulos:
        resumo = relatorio["resumos"][rotulo]
        linhas.append(f"| {rotulo} | " + " | ".join(str(resumo[k]) for k in chaves) + " |")
    linhas += [
        "",
        f"Regressões: **{len(regressoes)}** · "
        f"Melhorias: **{len(melhorias)}** · "
        f"Empates: **{relatorio['empates']}**",
    ]
    if regressoes:
        linhas += ["", "## Regressões", ""]
        for r in regressoes:
            linhas.append(f"- `{r['id']}` {r['pergunta']} — v6={r['pos_v6'] or '-'} → v7={r['pos_v7'] or '-'}")
    if melhorias:
        linhas += ["", "## Melhorias", ""]
        for r in melhorias:
            linhas.append(f"- `{r['id']}` {r['pergunta']} — v6={r['pos_v6'] or '-'} → v7={r['pos_v7'] or '-'}")

    (args.saida / "REPORT.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")

    print(json.dumps({"resumos": relatorio["resumos"],
                       "regressoes": len(regressoes),
                       "melhorias": len(melhorias)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())