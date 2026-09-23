"""Recuperação híbrida sobre os índices persistidos por :mod:`src.index`.

O índice atual persiste vetores e metadados, mas não uma implementação BM25.
Por isso, a busca lexical lê o JSONL normalizado e calcula BM25 em memória,
sem reindexar nem gerar embeddings durante a busca.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import config
from src.obs import Stage, default_logger


DEFAULT_RRF_K = 60
DEFAULT_TOP_K = 10
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_NATUREZA_TO_BASE = {
    "normativa": "editais_normativo",
    "estruturada": "contratos_estruturado",
    "conversacional": "atas_conversacional",
    "agregada": "pca_agregado",
}

EmbedFn = Callable[[list[str]], list[list[float]]]
RerankFn = Callable[[str, list["RetrievalResult"]], Sequence[Any]]


@dataclass
class RetrievalResult:
    """Resultado de uma estratégia ou da fusão, com proveniência preservada."""

    chunk_id: str
    content: str
    metadata: dict[str, Any]
    score: float
    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float | None = None
    document_id: str | None = None
    source_path: str | None = None
    _record: dict[str, Any] = field(default_factory=dict, repr=False)

    def __getitem__(self, key: str) -> Any:
        """Permite consumo como mapeamento sem perder o acesso tipado."""
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "metadata": self.metadata,
            "score": self.score,
            "dense_score": self.dense_score,
            "sparse_score": self.sparse_score,
            "rrf_score": self.rrf_score,
            "document_id": self.document_id,
            "source_path": self.source_path,
        }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _selected_base_ids(bases: Iterable[Any] | None) -> set[str] | None:
    if bases is None:
        return None
    if isinstance(bases, str):
        bases = [bases]
    selected: set[str] = set()
    for base in bases:
        value = getattr(base, "value", base)
        value = str(value)
        selected.add(_NATUREZA_TO_BASE.get(value, value))
    return selected


def _metadata_value(metadata: Mapping[str, Any], key: str) -> Any:
    if key in metadata:
        return metadata[key]
    extras = metadata.get("extras")
    if isinstance(extras, Mapping):
        return extras.get(key)
    return None


def _values_equal(left: Any, right: Any) -> bool:
    left_normalized, right_normalized = _comparable_values(left, right)
    return left_normalized == right_normalized


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    for formatter in (date.fromisoformat, lambda item: datetime.strptime(item, "%Y%m%d").date()):
        try:
            return formatter(candidate)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_number(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace("R$", "").replace(" ", "")
    if not candidate:
        return None
    if "," in candidate:
        candidate = candidate.replace(".", "").replace(",", ".")
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def _comparable_values(left: Any, right: Any) -> tuple[Any, Any]:
    left_date, right_date = _parse_date(left), _parse_date(right)
    if left_date is not None and right_date is not None:
        return left_date, right_date
    left_number, right_number = _parse_number(left), _parse_number(right)
    if left_number is not None and right_number is not None:
        return left_number, right_number
    return left, right


def _matches_filters(metadata: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        operator = "=="
        value = expected
        if isinstance(expected, Mapping):
            operator = str(expected.get("operator", expected.get("op", "==")))
            value = expected.get("value")
        elif isinstance(expected, (tuple, list)) and len(expected) == 2:
            operator, value = str(expected[0]), expected[1]
        actual = _metadata_value(metadata, key)
        if actual is None:
            return False
        try:
            if operator in {"==", "="}:
                if not _values_equal(actual, value):
                    return False
            elif operator == "!=":
                if _values_equal(actual, value):
                    return False
            elif operator in {">", ">=", "<", "<="}:
                left, right = _comparable_values(actual, value)
                if type(left) is not type(right):
                    return False
                comparisons = {
                    ">": left > right,
                    ">=": left >= right,
                    "<": left < right,
                    "<=": left <= right,
                }
                if not comparisons[operator]:
                    return False
            elif operator == "in":
                candidates = value if isinstance(value, (list, tuple, set)) else [value]
                if not any(_values_equal(actual, candidate) for candidate in candidates):
                    return False
            elif operator == "contains":
                if isinstance(actual, str):
                    if str(value) not in actual:
                        return False
                elif value not in actual:
                    return False
            else:
                return False
        except (TypeError, ValueError, KeyError):
            return False
    return True


def _records_for(
    index_dir: Path,
    bases: Iterable[Any] | None,
    filters: Mapping[str, Any] | None,
    records: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    selected = _selected_base_ids(bases)
    if records is None:
        paths = sorted(index_dir.glob("*/vectors.jsonl"))
        raw = [record for path in paths for record in _read_jsonl(path)]
    else:
        raw = [dict(record) for record in records]
    filtered: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in raw:
        metadata = record.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        chunk_id = str(record.get("chunk_id") or metadata.get("chunk_id") or "")
        if (
            not chunk_id
            or chunk_id in seen_ids
            or (selected is not None and record.get("base_id") not in selected)
            or not _matches_filters(metadata, filters)
        ):
            continue
        seen_ids.add(chunk_id)
        filtered.append(record)
    return filtered


def _result(record: Mapping[str, Any], score: float, content: str = "") -> RetrievalResult:
    raw_metadata = record.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    return RetrievalResult(
        chunk_id=str(record.get("chunk_id") or metadata.get("chunk_id", "")),
        content=content or str(record.get("text", "")),
        metadata=metadata,
        score=score,
        document_id=record.get("document_id"),
        source_path=record.get("source_path"),
        _record=dict(record),
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def _default_embed(query: str) -> list[float]:
    from src.index import _default_embed_fn

    return _default_embed_fn(config.EMBEDDING_MODEL)([query])[0]


def dense_retrieve(
    query: str,
    *,
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
    top_k: int = DEFAULT_TOP_K,
    bases: Iterable[Any] | None = None,
    filters: Mapping[str, Any] | None = None,
    embed_fn: EmbedFn | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
) -> list[RetrievalResult]:
    """Recupera os vetores mais próximos usando o índice persistido."""
    if top_k <= 0 or not query.strip():
        return []
    candidates = _records_for(index_dir, bases, filters, records)
    if not candidates:
        return []
    query_vector = (embed_fn or (lambda texts: [_default_embed(texts[0])]))([query])[0]
    normalized_text = {
        str(item.get("chunk_id")): str(item.get("text", ""))
        for item in _read_jsonl(documents_file)
    }
    scored = [
        _result(
            record,
            _cosine(query_vector, record.get("embedding", [])),
            str(record.get("text", "")) or normalized_text.get(str(record.get("chunk_id")), ""),
        )
        for record in candidates
    ]
    return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:top_k]


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def bm25_retrieve(
    query: str,
    *,
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
    top_k: int = DEFAULT_TOP_K,
    bases: Iterable[Any] | None = None,
    filters: Mapping[str, Any] | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
) -> list[RetrievalResult]:
    """Recupera chunks lexicalmente com BM25, sem dependências adicionais."""
    if top_k <= 0 or not query.strip():
        return []
    candidates = (
        _records_for(index_dir, bases, filters, records)
        if records is not None
        else _records_for(index_dir, bases, filters, _read_jsonl(documents_file))
    )
    documents = [(record, str(record.get("text", ""))) for record in candidates]
    query_terms = _tokens(query)
    if not query_terms or not documents:
        return []
    tokenized = [(record, text, _tokens(text)) for record, text in documents]
    average_length = sum(len(tokens) for _, _, tokens in tokenized) / len(tokenized)
    document_frequency = Counter(term for _, _, tokens in tokenized for term in set(tokens))
    scored: list[RetrievalResult] = []
    for record, text, tokens in tokenized:
        counts = Counter(tokens)
        score = 0.0
        for term in query_terms:
            if term not in counts:
                continue
            idf = math.log(1 + (len(tokenized) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = counts[term] + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / (average_length or 1))
            score += idf * counts[term] * 2.5 / denominator
        if score > 0:
            scored.append(_result(record, score, text))
    return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:top_k]


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievalResult]],
    *,
    k: int = DEFAULT_RRF_K,
    top_k: int | None = None,
) -> list[RetrievalResult]:
    """Combina rankings por identificador estável usando Reciprocal Rank Fusion."""
    if k < 0:
        raise ValueError("k deve ser >= 0")
    merged: dict[str, RetrievalResult] = {}
    scores: Counter[str] = Counter()
    dense_scores: dict[str, float] = {}
    sparse_scores: dict[str, float] = {}
    for list_number, ranked in enumerate(ranked_lists):
        for rank, item in enumerate(ranked, start=1):
            key = item.chunk_id
            scores[key] += 1 / (k + rank)
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


def retrieve(
    query: str,
    route: Any = None,
    *,
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
    top_k: int = DEFAULT_TOP_K,
    filters: Mapping[str, Any] | None = None,
    embed_fn: EmbedFn | None = None,
    reranker: RerankFn | None = None,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
) -> list[RetrievalResult]:
    """Executa dense + BM25 + RRF e aplica reranker somente se fornecido.

    Cada etapa registra seu stage (``retrieval_denso``, ``retrieval_bm25``,
    ``fusao_rrf``, ``rerank``) em ``logs/pipeline-AAAA-MM-DD.jsonl``.
    """
    bases = None
    route_filters = filters
    if route is not None:
        if isinstance(route, Mapping):
            bases = route.get("bases_selecionadas", route.get("bases"))
            route_filters = route.get("filters", route.get("filtros", route_filters))
        else:
            bases = getattr(route, "bases_selecionadas", getattr(route, "bases", None))
            route_filters = getattr(route, "filters", getattr(route, "filtros", route_filters))
    extra = {
        "bases": sorted(_selected_base_ids(bases) or []),
        "filtros": dict(route_filters or {}),
        "k": top_k,
    }

    def _span(stage: Stage, **mais: Any):
        return default_logger.span(
            run_id=run_id, query_id=query_id, stage=stage, input=query, extra={**extra, **mais}
        )

    with _span(Stage.RETRIEVAL_DENSO) as span:
        dense = dense_retrieve(
            query,
            index_dir=index_dir,
            documents_file=documents_file,
            top_k=top_k,
            bases=bases,
            filters=route_filters,
            embed_fn=embed_fn,
        )
        span.set_result(_resumo(dense))
    with _span(Stage.RETRIEVAL_BM25) as span:
        sparse = bm25_retrieve(query, index_dir=index_dir, documents_file=documents_file, top_k=top_k, bases=bases, filters=route_filters)
        span.set_result(_resumo(sparse))
    with _span(Stage.FUSAO_RRF, rrf_k=DEFAULT_RRF_K) as span:
        fused = reciprocal_rank_fusion([dense, sparse], top_k=top_k)
        span.set_result(_resumo(fused))
    if reranker is not None and fused:
        with _span(Stage.RERANK) as span:
            reranked = list(reranker(query, fused))[:top_k]
            span.set_result(_resumo(reranked))
        return reranked
    return fused


def _resumo(resultados: Sequence[Any]) -> str:
    """Resumo curto para o campo ``output`` do log (ex.: "12 chunks; top base: x")."""
    if not resultados:
        return "0 chunks"
    primeiro = resultados[0]
    metadata = getattr(primeiro, "metadata", None) or {}
    base = metadata.get("base_id") or getattr(primeiro, "_record", {}).get("base_id")
    return f"{len(resultados)} chunks; top base: {base}" if base else f"{len(resultados)} chunks"


__all__ = [
    "RetrievalResult",
    "bm25_retrieve",
    "dense_retrieve",
    "reciprocal_rank_fusion",
    "retrieve",
]
