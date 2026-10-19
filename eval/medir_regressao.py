'''Benchmark de regressão de recuperação: corpus novo × corpus de referência.

Regra do desafio: "roda o benchmark antes e depois de atualizar a base e mostra
que a recuperação não piorou nas perguntas que já funcionavam". Mede os mesmos
casos rotulados (`eval/retrieval_set.jsonl`) contra DOIS alvos — mesma métrica
da ablação (`eval/ablacao_rerank.py`), recuperação densa pura — e classifica
cada caso: regrediu, melhorou ou empatou.

Para isolar o efeito do CORPUS, os dois alvos devem usar o mesmo encoder e a
mesma projeção de embedding; a comparação v6 × v7 do registro usou os índices
do `eval/runs/projecao-v6-3modos-*/index_completa` (v6, encoder local) contra
`data/v7_direto/index` (v7, encoder local, projeção completa). Se o v7 tiver
sido embutido pelo ECOA (mesmo modelo base, quantização diferente), a
diferença mede corpus+encoder e deve ser lida assim no relatório.

Uso:

    python -m eval.medir_regressao \
        --antigo-documents data/normalized/documents.jsonl \
        --antigo-index eval/runs/projecao-v6-3modos-2026-10-14/index_completa \
        --novo-documents data/v7_direto/documents.jsonl \
        --novo-index data/v7_direto/index
'''

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from eval.ablacao_rerank import (  # noqa: E402
    carregar_casos,
    posicao_do_primeiro_relevante,
    resumir,
)
from src.retrieve import DEFAULT_TOP_K, dense_retrieve, retrieve  # noqa: E402

EmbedFn = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class Alvo:
    rotulo: str
    documents_file: Path
    index_dir: Path


def medir_regressao(
    casos: list[Any],
    *,
    referencia: Alvo,
    novo: Alvo,
    embed_fn: EmbedFn | None = None,
    top_k: int = DEFAULT_TOP_K,
    strategy: str = "dense",
    reranker: Any = None,
    fetch_k: int | None = None,
    query_filters: bool = False,
) -> dict[str, Any]:
    '''Mesmos casos/configuração nos dois alvos, usando o núcleo de produção.'''
    if strategy not in {"dense", "hybrid"}:
        raise ValueError("strategy deve ser dense ou hybrid")
    if query_filters and strategy != "hybrid":
        raise ValueError("query_filters exige strategy=hybrid; a ablação densa permanece pura")
    filtros_extraidos: dict[str, Any] = {}
    if query_filters:
        from src.query_analysis import analisar_query, filtros_para_retrieve
        filtros_extraidos = {
            caso.id: filtros_para_retrieve(analisar_query(caso.pergunta, modo="regras").filtros)
            for caso in casos
        }
    posicoes: dict[str, list[Any]] = {referencia.rotulo: [], novo.rotulo: []}
    por_caso: list[dict[str, Any]] = []
    regressoes: list[dict[str, Any]] = []
    melhorias: list[dict[str, Any]] = []

    for alvo in (referencia, novo):
        for caso in casos:
            comum = dict(top_k=top_k, embed_fn=embed_fn, index_dir=alvo.index_dir,
                         documents_file=alvo.documents_file, escopos=("oficial",))
            if query_filters:
                comum["filters"] = filtros_extraidos[caso.id]
            if strategy == "hybrid":
                ranking = retrieve(caso.pergunta, {"bases": caso.bases}, reranker=reranker,
                                   fetch_k=fetch_k, **comum)
            else:
                ranking = dense_retrieve(caso.pergunta, bases=caso.bases, **comum)
            posicoes[alvo.rotulo].append(posicao_do_primeiro_relevante(caso, ranking))

    for indice, caso in enumerate(casos):
        pos_ref = posicoes[referencia.rotulo][indice]
        pos_novo = posicoes[novo.rotulo][indice]
        linha = {
            "id": caso.id,
            "pergunta": caso.pergunta,
            f"pos_{referencia.rotulo}": pos_ref,
            f"pos_{novo.rotulo}": pos_novo,
        }
        por_caso.append(linha)
        piorou = (pos_novo is None and pos_ref is not None) or (
            pos_ref is not None and pos_novo is not None and pos_novo > pos_ref
        )
        melhorou = (pos_novo is not None and pos_ref is None) or (
            pos_ref is not None and pos_novo is not None and pos_novo < pos_ref
        )
        if piorou:
            regressoes.append(linha)
        elif melhorou:
            melhorias.append(linha)

    return {
        "total": len(casos),
        "configuracao": {"strategy": strategy, "top_k": top_k, "fetch_k": fetch_k,
                         "rerank": reranker is not None,
                         "escopos": ["oficial"], "rotas": "gabarito de recuperação",
                         "encoding": "produção (embedding_query_text)" if embed_fn is None else "embed_fn injetada",
                         "query_analysis": "regras" if query_filters else "não executada",
                         "filtros_por_caso": json.loads(json.dumps(filtros_extraidos, default=str))},
        "resumos": {
            referencia.rotulo: resumir(posicoes[referencia.rotulo]),
            novo.rotulo: resumir(posicoes[novo.rotulo]),
        },
        "regressoes": regressoes,
        "melhorias": melhorias,
        "empates": len(casos) - len(regressoes) - len(melhorias),
        "por_caso": por_caso,
    }


def render_markdown(relatorio: dict[str, Any]) -> str:
    chaves = ("hit@1", "hit@3", "hit@5", "hit@10", "mrr@10")
    rotulos = [r for r in relatorio["resumos"]]
    linhas = [
        "# Regressão de recuperação — corpus de referência × novo",
        "",
        f"{relatorio['total']} casos; configuração idêntica nos dois snapshots: "
        f"`{relatorio.get('configuracao', {}).get('strategy', 'dense')}`; "
        f"rerank={relatorio.get('configuracao', {}).get('rerank', False)}.",
        "",
        "| corpus | " + " | ".join(chaves) + " |",
        "| --- | " + " | ".join("---" for _ in chaves) + " |",
    ]
    for rotulo in rotulos:
        resumo = relatorio["resumos"][rotulo]
        linhas.append(f"| {rotulo} | " + " | ".join(str(resumo[k]) for k in chaves) + " |")
    linhas += [
        "",
        f"Regressões: **{len(relatorio['regressoes'])}** · "
        f"Melhorias: **{len(relatorio['melhorias'])}** · "
        f"Empates: **{relatorio['empates']}**",
    ]
    if relatorio["regressoes"]:
        linhas += ["", "## Regressões (funcionavam e pioraram)", ""]
        linhas += [
            f"- `{r['id']}` {r['pergunta'][:80]} — "
            f"ref {r[f'pos_{rotulos[0]}'] or '-'} → novo {r[f'pos_{rotulos[1]}'] or '-'}"
            for r in relatorio["regressoes"]
        ]
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--antigo-documents", type=Path, required=True)
    parser.add_argument("--antigo-index", type=Path, required=True)
    parser.add_argument("--novo-documents", type=Path, required=True)
    parser.add_argument("--novo-index", type=Path, required=True)
    parser.add_argument("--casos", type=Path,
                        default=PROJECT_ROOT / "eval" / "retrieval_set.jsonl")
    parser.add_argument("--saida", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args(argv)

    # O encoder/cache padrão aplica a mesma instrução de consulta da produção.
    relatorio = medir_regressao(
        carregar_casos(args.casos),
        referencia=Alvo("v6", args.antigo_documents, args.antigo_index),
        novo=Alvo("v7", args.novo_documents, args.novo_index),
        top_k=args.top_k,
    )
    args.saida.mkdir(parents=True, exist_ok=True)
    (args.saida / "report.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (args.saida / "REPORT.md").write_text(render_markdown(relatorio), encoding="utf-8")
    print(json.dumps({"resumos": relatorio["resumos"],
                      "regressoes": len(relatorio["regressoes"]),
                      "melhorias": len(relatorio["melhorias"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
