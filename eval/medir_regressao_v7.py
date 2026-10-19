"""CLI compatível v6/v7 delegando à recuperação e às métricas existentes.

Sem --hybrid: ablação densa nos DOIS snapshots. Com --hybrid: retrieve da
produção (BM25, RRF, lookup e rerank configurado) nos DOIS snapshots.
Não executa geração; rotas são as do gabarito de recuperação.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from eval.ablacao_rerank import carregar_casos
from eval.medir_regressao import Alvo, medir_regressao, render_markdown
from src.index import file_sha256
from src.rerank import reranker_padrao
from src.retrieve import QUERY_INSTRUCTION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v6-index", type=Path, required=True)
    parser.add_argument("--v6-documents", type=Path, required=True)
    parser.add_argument("--v7-index", type=Path, required=True)
    parser.add_argument("--v7-documents", type=Path, required=True)
    parser.add_argument("--casos", type=Path, default=PROJECT_ROOT / "eval/retrieval_set.jsonl")
    parser.add_argument("--saida", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--fetch-k", type=int)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--query-filters", action="store_true",
                        help="query_analysis por regras nos dois snapshots; exige --hybrid")
    parser.add_argument("--sem-rerank", action="store_true", help="ablação explícita, igual nos dois lados")
    args = parser.parse_args(argv)
    if args.query_filters and not args.hybrid:
        parser.error("--query-filters exige --hybrid (a ablação densa permanece pura)")
    reference = Alvo("v6", args.v6_documents, args.v6_index)
    candidate = Alvo("v7", args.v7_documents, args.v7_index)
    reranker = reranker_padrao() if args.hybrid and not args.sem_rerank else None
    report = medir_regressao(
        carregar_casos(args.casos), referencia=reference, novo=candidate,
        top_k=args.top_k, strategy="hybrid" if args.hybrid else "dense",
        reranker=reranker, fetch_k=args.fetch_k, query_filters=args.query_filters,
    )
    _, _, query_embedding_model = config.embedding_endpoint()
    report["configuracao"].update({
        "query_instruction": QUERY_INSTRUCTION,
        "query_embedding_provider": config.EMBEDDING_PROVIDER,
        "query_embedding_model": query_embedding_model,
        "casos_sha256": file_sha256(args.casos),
        "snapshots": {
            alvo.rotulo: {
                "documents_sha256": file_sha256(alvo.documents_file),
                "index_manifest_sha256": file_sha256(alvo.index_dir / "manifest.json"),
                "manifest": json.loads((alvo.index_dir / "manifest.json").read_text(encoding="utf-8")),
            }
            for alvo in (reference, candidate)
        },
        "reranker": getattr(reranker, "modelo", None),
        "limite": "recuperação com rotas do gabarito; não mede roteamento nem geração",
    })
    args.saida.mkdir(parents=True, exist_ok=True)
    (args.saida / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.saida / "REPORT.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"resumos": report["resumos"], "regressoes": len(report["regressoes"]),
                      "melhorias": len(report["melhorias"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
