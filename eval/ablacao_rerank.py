r"""Ablação da recuperação: denso, BM25, híbrido (RRF) e híbrido + rerank.

Uso:
    py .\eval\ablacao_rerank.py
    py .\eval\ablacao_rerank.py --modelo BAAI/bge-reranker-v2-m3 --candidatos 10 30 50
    py .\eval\ablacao_rerank.py --modelo ecoa

O golden set (``eval/golden_set.jsonl``) diz qual base deve responder, mas não
qual chunk; para medir recuperação usamos ``eval/retrieval_set.jsonl``: cada
pergunta foi escrita com outras palavras a partir de um trecho conhecido do
corpus ("known-item"), e ``relevante_contem`` lista os trechos que um chunk
precisa conter (sem acento/caixa) para contar como relevante — assim o rótulo
sobrevive a uma reindexação que mude os ``chunk_id``. ``relevante_documento``,
quando presente, restringe ao arquivo de origem. ``tipo`` separa as perguntas
parafraseadas (padrão) das de ``identificador`` (CNPJ, número de controle,
número de pregão), onde a busca lexical deveria ajudar; o relatório também
resume cada tipo em separado.

Cada pergunta é buscada só nas bases de ``bases`` (o roteamento não entra na
conta) e só no escopo oficial, como no pipeline. Denso e BM25 devolvem
``DEFAULT_FETCH_K`` candidatos cada, como no ``retrieve``; o RRF funde e cada
configuração ``rrf+rerank@N`` reordena os N primeiros da fusão, e
``denso+rerank@N`` os N primeiros só da busca densa. Métricas: hit@k
(algum relevante entre os k primeiros) e MRR@10. hit@3 é a que mais pesa: com
``GENERATION_CONTEXT_CHARS``/``GENERATION_CHUNK_CHARS`` padrão só ~3–4 chunks
cabem no prompt da geração.

O relatório vai para ``eval/runs/ablacao-<timestamp>/`` (JSON + Markdown).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from src.rerank import CrossEncoderReranker, EcoaReranker  # noqa: E402
from src.retrieve import (  # noqa: E402
    DEFAULT_FETCH_K,
    RetrievalResult,
    bm25_retrieve,
    dense_retrieve,
    reciprocal_rank_fusion,
)

DEFAULT_SET = PROJECT_ROOT / "eval" / "retrieval_set.jsonl"
DEFAULT_RUNS_DIR = PROJECT_ROOT / "eval" / "runs"
ESCOPOS = ("oficial",)
TOP_K = 10
HITS_EM = (1, 3, 5, 10)


@dataclass(frozen=True)
class CasoRecuperacao:
    id: str
    pergunta: str
    bases: list[str]
    relevante_contem: list[str]
    relevante_documento: Optional[str] = None
    tipo: str = "parafrase"


def _normalizar(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c)
    )
    return " ".join(sem_acento.split())


def carregar_casos(path: Path) -> list[CasoRecuperacao]:
    casos = []
    with path.open("r", encoding="utf-8") as handle:
        for linha in handle:
            if linha.strip():
                r = json.loads(linha)
                casos.append(
                    CasoRecuperacao(
                        id=r["id"],
                        pergunta=r["pergunta"],
                        bases=list(r["bases"]),
                        relevante_contem=list(r["relevante_contem"]),
                        relevante_documento=r.get("relevante_documento"),
                        tipo=r.get("tipo", "parafrase"),
                    )
                )
    return casos


def e_relevante(caso: CasoRecuperacao, resultado: RetrievalResult) -> bool:
    texto = _normalizar(resultado.content)
    if not all(_normalizar(trecho) in texto for trecho in caso.relevante_contem):
        return False
    if caso.relevante_documento:
        origem = " ".join(
            str(valor)
            for valor in (
                resultado.source_path,
                resultado.metadata.get("source_file"),
                resultado.metadata.get("nome_arquivo"),
                resultado._record.get("nome_arquivo"),
            )
            if valor
        )
        return caso.relevante_documento in origem
    return True


def posicao_do_primeiro_relevante(caso: CasoRecuperacao, ranking: list[RetrievalResult]) -> Optional[int]:
    for posicao, resultado in enumerate(ranking[:TOP_K], start=1):
        if e_relevante(caso, resultado):
            return posicao
    return None


def resumir(posicoes: list[Optional[int]]) -> dict[str, float]:
    total = len(posicoes) or 1
    resumo = {f"hit@{k}": round(sum(1 for p in posicoes if p and p <= k) / total, 4) for k in HITS_EM}
    resumo["mrr@10"] = round(sum(1 / p for p in posicoes if p) / total, 4)
    return resumo


def rodar_ablacao(
    casos: list[CasoRecuperacao],
    *,
    reranker: Callable[[str, list[RetrievalResult]], list[RetrievalResult]],
    candidatos: list[int],
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
) -> dict[str, Any]:
    com_rerank = [f"{origem}+rerank@{n}" for origem in ("rrf", "denso") for n in candidatos]
    configuracoes = ["denso", "bm25", "hibrido_rrf"] + com_rerank
    posicoes: dict[str, list[Optional[int]]] = {nome: [] for nome in configuracoes}
    latencias_rerank: dict[str, list[int]] = {nome: [] for nome in com_rerank}
    por_caso = []
    for indice, caso in enumerate(casos, start=1):
        comum = dict(index_dir=index_dir, documents_file=documents_file, bases=caso.bases, escopos=ESCOPOS)
        denso = dense_retrieve(caso.pergunta, top_k=DEFAULT_FETCH_K, embed_fn=embed_fn, **comum)
        bm25 = bm25_retrieve(caso.pergunta, top_k=DEFAULT_FETCH_K, **comum)
        fusao = reciprocal_rank_fusion([denso, bm25])
        rankings = {"denso": denso, "bm25": bm25, "hibrido_rrf": fusao}
        for origem, lista in (("rrf", fusao), ("denso", denso)):
            for n in candidatos:
                nome = f"{origem}+rerank@{n}"
                inicio = time.perf_counter()
                rankings[nome] = reranker(caso.pergunta, lista[:n])
                latencias_rerank[nome].append(int((time.perf_counter() - inicio) * 1000))
        linha = {"id": caso.id, "tipo": caso.tipo, "pergunta": caso.pergunta}
        for nome in configuracoes:
            posicao = posicao_do_primeiro_relevante(caso, rankings[nome])
            posicoes[nome].append(posicao)
            linha[nome] = posicao
        por_caso.append(linha)
        print(f"[{indice}/{len(casos)}] {caso.id} " + " ".join(
            f"{nome}={linha[nome] or '-'}" for nome in configuracoes
        ), file=sys.stderr, flush=True)
    return {
        "total": len(casos),
        "configuracoes": {nome: resumir(posicoes[nome]) for nome in configuracoes},
        "por_tipo": {
            tipo: {
                "total": sum(1 for caso in casos if caso.tipo == tipo),
                "configuracoes": {
                    nome: resumir([p for caso, p in zip(casos, posicoes[nome]) if caso.tipo == tipo])
                    for nome in configuracoes
                },
            }
            for tipo in sorted({caso.tipo for caso in casos})
        },
        "latencia_rerank_ms": {
            nome: {"media": int(statistics.mean(v)), "max": max(v)} for nome, v in latencias_rerank.items() if v
        },
        "por_caso": por_caso,
    }


def render_markdown(relatorio: dict[str, Any]) -> str:
    colunas = [f"hit@{k}" for k in HITS_EM] + ["mrr@10"]
    linhas = [
        "# Ablação da recuperação — denso, BM25, RRF e rerank",
        "",
        f"- Run: `{relatorio['run_id']}` · gerado em `{relatorio['built_at']}`",
        f"- Conjunto: `{relatorio['conjunto']}` ({relatorio['total']} perguntas known-item)",
        f"- Reranker: `{relatorio['modelo_rerank']}` (CPU)",
        "",
        "| Configuração | " + " | ".join(colunas) + " |",
        "|---|" + "---:|" * len(colunas),
    ]
    for nome, metricas in relatorio["configuracoes"].items():
        linhas.append(f"| {nome} | " + " | ".join(f"{metricas[c]:.2f}" for c in colunas) + " |")
    if len(relatorio.get("por_tipo", {})) > 1:
        for tipo, grupo in relatorio["por_tipo"].items():
            linhas.extend([
                "",
                f"### Só perguntas do tipo `{tipo}` ({grupo['total']})",
                "",
                "| Configuração | " + " | ".join(colunas) + " |",
                "|---|" + "---:|" * len(colunas),
            ])
            for nome, metricas in grupo["configuracoes"].items():
                linhas.append(f"| {nome} | " + " | ".join(f"{metricas[c]:.2f}" for c in colunas) + " |")
    if relatorio.get("latencia_rerank_ms"):
        linhas.extend(["", "Latência do rerank por pergunta (ms):", ""])
        for nome, lat in relatorio["latencia_rerank_ms"].items():
            linhas.append(f"- {nome}: média {lat['media']}, máx {lat['max']}")
    nomes = list(relatorio["configuracoes"])
    linhas.extend([
        "",
        "## Posição do primeiro chunk relevante (— = fora do top 10)",
        "",
        "| ID | " + " | ".join(nomes) + " |",
        "|---|" + "---:|" * len(nomes),
    ])
    for linha in relatorio["por_caso"]:
        linhas.append(f"| {linha['id']} | " + " | ".join(str(linha[n] or "—") for n in nomes) + " |")
    return "\n".join(linhas) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--conjunto", type=Path, default=DEFAULT_SET)
    parser.add_argument("--modelo", default=None,
                        help="cross-encoder (padrão: config.RERANK_MODEL), ou 'ecoa' para o /api/rerank da turma")
    parser.add_argument("--candidatos", type=int, nargs="+", default=[TOP_K, config.RERANK_CANDIDATES])
    parser.add_argument("--output", type=Path, default=DEFAULT_RUNS_DIR)
    args = parser.parse_args()

    casos = carregar_casos(args.conjunto)
    # "ecoa" sem fallback: uma falha do endpoint aparece no relatório em vez de
    # medir o cross-encoder local sem avisar.
    reranker = EcoaReranker() if args.modelo == "ecoa" else CrossEncoderReranker(args.modelo)
    run_id = "ablacao-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    relatorio = rodar_ablacao(casos, reranker=reranker, candidatos=sorted(set(args.candidatos)))
    relatorio = {
        "run_id": run_id,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conjunto": args.conjunto.relative_to(PROJECT_ROOT).as_posix()
        if args.conjunto.is_relative_to(PROJECT_ROOT) else str(args.conjunto),
        "modelo_rerank": reranker.modelo,
        **relatorio,
    }
    destino = args.output / run_id
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "report.json").write_text(json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destino / "REPORT.md").write_text(render_markdown(relatorio), encoding="utf-8")
    print(json.dumps({"run_id": run_id, "configuracoes": relatorio["configuracoes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
