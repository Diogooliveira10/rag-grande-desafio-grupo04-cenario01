"""Optional complete evidence audit, independent of the LLM context budget.

Uses existing sensitivity classification/masking; restricted content is never
copied into the detailed audit. The ordinary summary remains unchanged.
"""
from __future__ import annotations

from typing import Any, Sequence

import config
from src.schema import Chunk
from src.sensitivity import mascarar, mascarar_valores


def auditoria_completa() -> bool:
    return config.LOG_EVIDENCE_DETAIL == "completo"


def _conteudo(text: str, sensitivity: Any) -> str:
    if getattr(sensitivity, "value", sensitivity) == "restrito":
        return "[Conteúdo restrito não incluído na auditoria]"
    return mascarar(text)[0]


def _metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    if getattr(metadata.get("sensitivity"), "value", metadata.get("sensitivity")) == "restrito":
        return {key: metadata.get(key) for key in ("chunk_id", "base_id", "sensitivity")}
    return metadata


def auditar_candidatos(resultados: Sequence[Any]) -> dict[str, Any]:
    """Every returned candidate, not every document in the corpus."""
    return {
        "modo": "completo", "total": len(resultados),
        "candidatos": [mascarar_valores({
            "rank": rank, "chunk_id": item.chunk_id,
            "metadata": _metadata(item.metadata),
            "score": item.score, "dense_score": item.dense_score,
            "sparse_score": item.sparse_score, "rrf_score": item.rrf_score,
            "rerank_score": item.rerank_score,
            "conteudo": _conteudo(item.content, item.metadata.get("sensitivity")),
        }) for rank, item in enumerate(resultados, 1)],
    }


def auditar_geracao(
    disponiveis: Sequence[Chunk], selecionados: Sequence[Chunk], *, metodo: str,
    mensagens: list[dict] | None = None,
) -> dict[str, Any]:
    selecionados_por_id = {c.metadata.chunk_id: c for c in selecionados}
    return {
        "modo": "completo", "metodo": metodo,
        "total_disponiveis": len(disponiveis), "total_selecionados": len(selecionados),
        "evidencias": [mascarar_valores({
            "chunk_id": c.metadata.chunk_id,
            "metadata": _metadata(c.metadata.model_dump(mode="json")),
            "conteudo_disponivel": _conteudo(c.content, c.metadata.sensitivity),
            "selecionado": c.metadata.chunk_id in selecionados_por_id,
            "motivo_exclusao": (None if c.metadata.chunk_id in selecionados_por_id else
                                "fora_da_resposta_sql" if metodo == "sql_deterministico" or metodo.startswith("sql_template")
                                else "orcamento_contexto"),
            "conteudo_selecionado": (
                _conteudo(selecionados_por_id[c.metadata.chunk_id].content, c.metadata.sensitivity)
                if c.metadata.chunk_id in selecionados_por_id else None),
        }) for c in disponiveis],
        # Exact masked prompt, without the summary's per-string log limit.
        "prompt_completo": mascarar_valores(mensagens or []),
    }
