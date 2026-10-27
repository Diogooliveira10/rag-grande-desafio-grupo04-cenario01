"""Evidências da busca no log: resumo e lista dos trechos de cada etapa.

Separado de src/retrieve.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.retrieval.records import RetrievalResult


def _registrar_evidencias(
    span: Any, resultados: Sequence[RetrievalResult], *,
    antes_do_top_k: Sequence[RetrievalResult] | None = None,
) -> None:
    from src.log_evidence import auditoria_completa, auditar_candidatos
    span.extra["evidencias"] = _evidencias(resultados)
    span.extra["evidencias_fora_do_log"] = max(0, len(resultados) - 10)
    if auditoria_completa():
        audit = auditar_candidatos(antes_do_top_k if antes_do_top_k is not None else resultados)
        if antes_do_top_k is not None:
            selecionados = {item.chunk_id for item in resultados}
            for item in audit["candidatos"]:
                item["selecionado_apos_top_k"] = item["chunk_id"] in selecionados
        span.extra["auditoria_evidencias"] = audit


def _evidencias(resultados: Sequence[RetrievalResult], limite: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "chunk_id": item.chunk_id,
            "base_id": item.metadata.get("base_id"),
            "source_file": item.metadata.get("source_file"),
            "score": item.score,
            "dense_score": item.dense_score,
            "sparse_score": item.sparse_score,
            "rrf_score": item.rrf_score,
            "rerank_score": item.rerank_score,
            "identificador_contrato_exato": bool(
                (item.metadata.get("extras") or {}).get("identificador_contrato_exato")
            ),
            "identificador_pncp_exato": bool(
                (item.metadata.get("extras") or {}).get("identificador_pncp_exato")
            ),
        }
        for rank, item in enumerate(resultados[:limite], start=1)
    ]


def _resumo(resultados: Sequence[Any]) -> str:
    """Resumo curto para o campo ``output`` do log (ex.: "12 chunks; top base: x")."""
    if not resultados:
        return "0 chunks"
    primeiro = resultados[0]
    metadata = getattr(primeiro, "metadata", None) or {}
    base = metadata.get("base_id") or getattr(primeiro, "_record", {}).get("base_id")
    return f"{len(resultados)} chunks; top base: {base}" if base else f"{len(resultados)} chunks"
