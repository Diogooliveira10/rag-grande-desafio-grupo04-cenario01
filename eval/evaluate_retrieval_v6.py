"""Ablação offline com cache ECOA e âncoras documentais julgadas na v6.

As âncoras são parciais: não representam todas as respostas relevantes do corpus.
Nenhuma chamada de modelo é permitida por este script.
"""
import importlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANCHORS = {
    "P2.1": ["contratos_estruturado:c95c495bfcb719ef:chunk-1-23cce8371195"],
    "P3.1": ["editais_normativo:1f7354fb65590678:chunk-69-71d4bb691eac"],
}


def main():
    import sys
    sys.path.insert(0, str(ROOT))
    retrieval = importlib.import_module("src.retrieve")
    cache = {}
    for line in (ROOT / "data/embeddings/query_cache.jsonl").read_text(encoding="utf8").splitlines():
        row = json.loads(line)
        cache[row["key"]["texts"][0]] = row["vectors"][0]
    cases = [json.loads(line) for line in (ROOT / "eval/runs/cli-v6/responses.jsonl").read_text(encoding="utf8").splitlines()]
    results = []
    for case in cases:
        relevant = set(ANCHORS.get(case["case_id"], []))
        if not relevant:
            continue
        query = case["pergunta"]
        vector = cache[retrieval.embedding_query_text(query)]
        bases = case["response"]["bases_consultadas"]
        dense = retrieval.dense_retrieve(query, bases=bases, top_k=50, embed_fn=lambda _: [vector])
        lexical = retrieval.bm25_retrieve(query, bases=bases, top_k=50)
        variants = {"dense": dense[:10], "hybrid_rrf": retrieval.reciprocal_rank_fusion([dense, lexical], top_k=10)}
        for name, ranking in variants.items():
            ids = [item.chunk_id for item in ranking]
            hits = [i for i, chunk_id in enumerate(ids) if chunk_id in relevant]
            dcg = sum(1 / math.log2(i + 2) for i in hits)
            ideal = sum(1 / math.log2(i + 2) for i in range(min(10, len(relevant))))
            results.append({"case_id": case["case_id"], "variant": name, "recall_at_10": len(hits) / len(relevant), "ndcg_at_10": dcg / ideal, "ranking": ids})
    report = {"scope": "Duas perguntas; âncoras parciais julgadas no texto real, sem avaliar vigência histórica. Sem filtros, mesmas bases da avaliação anterior.", "anchors": ANCHORS, "results": results, "cross_encoder": "Pendente: endpoint oficial não fornecido; teste do hook não é ablação real."}
    out = ROOT / "eval/runs/quality-v6/retrieval-ablation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps([{k: v for k, v in row.items() if k != "ranking"} for row in results], ensure_ascii=False))


if __name__ == "__main__":
    main()
