"""Mede o efeito da variedade por compra (RETRIEVAL_MAX_PER_PURCHASE) nas atas.

Para cada pergunta do gabarito que usa a base de atas, roda a mesma busca de
produção com a regra desligada (0) e ligada (N) e compara, nos 10 primeiros:
quantas compras distintas aparecem, quantos trechos foram rebaixados e a
posição do primeiro trecho relevante. Não executa geração.

    python -u eval/medir_variedade.py --index <indice> --documents <documents.jsonl>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import src.retrieve as retrieve_module  # noqa: E402
from eval.ablacao_rerank import carregar_casos, posicao_do_primeiro_relevante  # noqa: E402
from src.rerank import reranker_padrao  # noqa: E402


def _rodar(caso, args, reranker, maximo):
    config.RETRIEVAL_MAX_PER_PURCHASE = maximo
    ranking = retrieve_module.retrieve(
        caso.pergunta, {"bases": caso.bases}, reranker=reranker, top_k=args.top_k,
        index_dir=args.index, documents_file=args.documents, escopos=("oficial",))
    chaves = [retrieve_module._chave_da_compra(item) for item in ranking]
    atas = [c for c in chaves if c is not None]
    return {"ranking": ranking, "atas": len(atas), "compras": len(set(atas)),
            "pos": posicao_do_primeiro_relevante(caso, ranking)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--casos", type=Path, default=PROJECT_ROOT / "eval/retrieval_set.jsonl")
    parser.add_argument("--maximo", type=int, default=2)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--sem-rerank", action="store_true")
    parser.add_argument("--base", default="conversacional", help="base do gabarito a medir")
    args = parser.parse_args(argv)
    casos = [c for c in carregar_casos(args.casos) if args.base in c.bases]
    reranker = None if args.sem_rerank else reranker_padrao()
    soma = {"sem": 0, "com": 0}
    mudou = 0
    print(f"{'caso':6} {'atas':>9} {'compras':>9} {'1o relevante':>14}   (sem regra -> com regra)")
    for caso in casos:
        sem = _rodar(caso, args, reranker, 0)
        com = _rodar(caso, args, reranker, args.maximo)
        soma["sem"] += sem["compras"]
        soma["com"] += com["compras"]
        mudou += [i.chunk_id for i in sem["ranking"]] != [i.chunk_id for i in com["ranking"]]
        print(f"{caso.id:6} {sem['atas']:>3}->{com['atas']:<4} {sem['compras']:>3}->{com['compras']:<4} "
              f"{str(sem['pos']):>6}->{str(com['pos']):<6}")
    n = len(casos) or 1
    print(f"\n{len(casos)} perguntas de ata; ranking mudou em {mudou}; "
          f"compras distintas no top-{args.top_k} (média): {soma['sem'] / n:.2f} -> {soma['com'] / n:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
