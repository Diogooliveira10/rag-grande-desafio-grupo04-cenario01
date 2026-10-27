"""Combinação dos rankings (RRF) e variedade por compra nas atas.

Separado de src/retrieve.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import re
from typing import Sequence

import config
from src.domain import fusion
from src.retrieval.identifiers import _identificadores_pncp, _referencias_contrato
from src.retrieval.records import RetrievalResult


DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievalResult]],
    *,
    k: int = DEFAULT_RRF_K,
    top_k: int | None = None,
) -> list[RetrievalResult]:
    """Combina rankings por identificador estável usando Reciprocal Rank Fusion.

    A conta é do domínio (src/domain/fusion.py); aqui só se monta o resultado.
    """
    scores = fusion.pontuacoes_rrf([[item.chunk_id for item in ranked] for ranked in ranked_lists], k=k)
    merged: dict[str, RetrievalResult] = {}
    dense_scores: dict[str, float] = {}
    sparse_scores: dict[str, float] = {}
    for list_number, ranked in enumerate(ranked_lists):
        for item in ranked:
            key = item.chunk_id
            if list_number == 0:
                dense_scores[key] = item.score
            else:
                sparse_scores[key] = item.score
            if key not in merged:
                merged[key] = item
    result: list[RetrievalResult] = []
    for key, item in merged.items():
        value = scores[key]
        result.append(
            RetrievalResult(
                **{field: getattr(item, field) for field in (
                    "chunk_id", "content", "metadata",
                    "document_id", "source_path", "_record"
                )},
                score=value,
                rrf_score=value,
                dense_score=dense_scores.get(key),
                sparse_score=sparse_scores.get(key),
            )
        )
    result.sort(key=lambda item: (-item.score, item.chunk_id))
    return result[:top_k] if top_k is not None else result


# Só as atas repetem muitos trechos da mesma compra (as páginas do PDF); nas
# demais bases cada chunk já é um registro próprio e não há o que variar.
_BASES_COM_VARIEDADE = frozenset({"atas_conversacional"})


_COMPRA_RE = re.compile(r"\d{14}-\d-\d{6}/\d{4}")


def _chave_da_compra(item: RetrievalResult) -> str | None:
    """Compra a que o trecho pertence; ``None`` se a base não entra na regra.

    Usa o número de controle da compra gravado nos extras; se faltar, o do PNCP
    da ata (``cnpj-d-seq/ano-seqAta``) sem o sufixo da ata; por último, o
    arquivo de origem, que numa ata é o próprio PDF.
    """
    if item.metadata.get("base_id") not in _BASES_COM_VARIEDADE:
        return None
    extras = item.metadata.get("extras") or {}
    for campo in ("numero_controle_compra", "numero_controle_pncp"):
        achado = _COMPRA_RE.search(str(extras.get(campo) or ""))
        if achado:
            return achado.group(0)
    origem = item.metadata.get("source_file") or item.source_path
    return f"arquivo:{origem}" if origem else None


def _variar_por_compra(query: str, resultados: list[RetrievalResult]) -> tuple[list[RetrievalResult], int]:
    """No máximo ``RETRIEVAL_MAX_PER_PURCHASE`` trechos por compra à frente do corte.

    Não vale quando a pergunta cita um número do PNCP ou de contrato: aí o
    usuário quer justamente os trechos daquele registro.
    """
    maximo = config.RETRIEVAL_MAX_PER_PURCHASE
    if maximo <= 0 or _identificadores_pncp(query) or _referencias_contrato(query):
        return resultados, 0
    return fusion.limitar_repeticoes(resultados, _chave_da_compra, maximo)
