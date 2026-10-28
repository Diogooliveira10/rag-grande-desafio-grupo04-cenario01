'''Mede o efeito da projeção compacta dos embeddings na recuperação.

Condição da §7 do plano v7: **medição obrigatória antes de promover** qualquer
índice com projeção compacta. Dois braços sobre o MESMO corpus e o MESMO
encoder — só muda o texto enviado ao encoder de documento:

- ``completa`` — o texto do chunk inteiro (comportamento da v6);
- ``compacta`` — só os campos de descrição (``src/embedding_projection.py``).

A métrica é a da ablação de rerank (``eval/ablacao_rerank.py``): posição do
primeiro chunk relevante, ``hit@k`` e ``mrr@10`` sobre
``eval/retrieval_set.jsonl``, com **recuperação densa pura** — BM25 e rerank
são idênticos nos dois braços e poluiriam a medição do efeito do embedding.

Uso:

    python -m eval.medir_projecao [--documents ...] [--casos ...] \
        [--provider ollama] [--destino eval/runs/projecao-...]
'''

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from eval.ablacao_rerank import (  # noqa: E402
    carregar_casos,
    posicao_do_primeiro_relevante,
    resumir,
)
from src import index as index_mod  # noqa: E402
from src.embedding_projection import MODO_COMPLETA, MODOS, embed_fn_projetado  # noqa: E402
from src.retrieve import DEFAULT_TOP_K, dense_retrieve  # noqa: E402

EmbedFn = Callable[[list[str]], list[list[float]]]


def medir_projecao(
    casos: list[Any],
    *,
    documents_file: Path,
    embed_fn: EmbedFn,
    modelo: str,
    destino: Path,
    top_k: int = DEFAULT_TOP_K,
    modos: tuple[str, ...] = MODOS,
) -> dict[str, Any]:
    '''Constrói um índice por modo e roda os mesmos casos contra cada um.'''
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    posicoes: dict[str, list[Any]] = {modo: [] for modo in modos}
    por_caso: list[dict[str, Any]] = []

    for modo in modos:
        index_dir = destino / f"index_{modo}"
        inicio = time.perf_counter()
        index_mod.build_index(
            input_file=documents_file,
            output_dir=index_dir,
            embed_fn=embed_fn_projetado(embed_fn, modo),
            model=modelo,
        )
        print(f"[{modo}] índice construído em {time.perf_counter() - inicio:.0f}s",
              file=sys.stderr, flush=True)
        for indice, caso in enumerate(casos, start=1):
            ranking = dense_retrieve(
                caso.pergunta,
                top_k=top_k,
                embed_fn=embed_fn,
                index_dir=index_dir,
                documents_file=documents_file,
                bases=caso.bases,
                escopos=("oficial",),
            )
            posicoes[modo].append(posicao_do_primeiro_relevante(caso, ranking))
            if not por_caso or len(por_caso) < indice:
                por_caso.append({"id": caso.id, "pergunta": caso.pergunta})
            por_caso[indice - 1][f"posicao_{modo}"] = posicoes[modo][-1]
            print(f"[{modo} {indice}/{len(casos)}] {caso.id} "
                  f"pos={posicoes[modo][-1] or '-'}", file=sys.stderr, flush=True)

    resumos = {modo: resumir(posicoes[modo]) for modo in modos}
    base = resumos.get(MODO_COMPLETA) or resumos[modos[0]]
    return {
        "total": len(casos),
        "documents_file": str(documents_file),
        "modelo": modelo,
        "resumos": resumos,
        "deltas_hit@3": {
            modo: round(resumo["hit@3"] - base["hit@3"], 4)
            for modo, resumo in resumos.items() if modo != (MODO_COMPLETA if MODO_COMPLETA in resumos else modos[0])
        },
        "por_caso": por_caso,
    }


def render_markdown(relatorio: dict[str, Any]) -> str:
    linhas = [
        "# Projeção dos embeddings — recuperação densa",
        "",
        f"Corpus: `{relatorio['documents_file']}` · encoder: `{relatorio['modelo']}` · "
        f"{relatorio['total']} casos de `eval/retrieval_set.jsonl`",
        "",
        "| modo | " + " | ".join(k for k in ("hit@1", "hit@3", "hit@5", "hit@10", "mrr@10")) + " |",
        "| --- | " + " | ".join("---" for _ in range(5)) + " |",
    ]
    for modo, resumo in relatorio["resumos"].items():
        linhas.append(f"| {modo} | " + " | ".join(str(resumo[k]) for k in
                      ("hit@1", "hit@3", "hit@5", "hit@10", "mrr@10")) + " |")
    linhas.append("")
    for modo, delta in relatorio["deltas_hit@3"].items():
        linhas.append(f"**delta hit@3 ({modo} − completa): {delta}**")
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path,
                        default=config.NORMALIZED_DATA_DIR / "documents.jsonl")
    parser.add_argument("--casos", type=Path,
                        default=PROJECT_ROOT / "eval" / "retrieval_set.jsonl")
    parser.add_argument("--destino", type=Path, default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args(argv)

    from src.create_embeddings import _configured_embed_batch

    provider = args.provider or config.EMBEDDING_PROVIDER
    endpoint, api_key, modelo = config.embedding_endpoint(provider)
    modelo = args.modelo or modelo
    embed_fn = _configured_embed_batch(provider, endpoint, api_key, modelo)

    destino = args.destino or (
        PROJECT_ROOT / "eval" / "runs" / f"projecao-{time.strftime('%Y-%m-%d_%H-%M-%S')}"
    )
    casos = carregar_casos(args.casos)
    relatorio = medir_projecao(
        casos,
        documents_file=args.documents,
        embed_fn=embed_fn,
        modelo=modelo,
        destino=destino,
        top_k=args.top_k,
    )
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "report.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (destino / "REPORT.md").write_text(render_markdown(relatorio), encoding="utf-8")
    print(json.dumps({"destino": str(destino), "resumos": relatorio["resumos"],
                      "deltas_hit@3": relatorio["deltas_hit@3"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
