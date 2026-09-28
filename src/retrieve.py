from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import config
from src.obs import Stage, TokenUsage, default_logger, registrar, token_usage_from_response
from src.schema import Natureza


DEFAULT_RRF_K = 60
DEFAULT_TOP_K = 10
DEFAULT_FETCH_K = 50
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
MANIFEST_PATH = config.ROOT_DIR / "corpus_manifest.yaml"
_NATUREZAS = frozenset(item.value for item in Natureza)

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


@lru_cache(maxsize=4)
def _manifest_bases(manifest_path: Path = MANIFEST_PATH) -> dict[str, dict[str, Any]]:
    """``base_id`` -> declaração da base no manifesto (natureza, escopo...)."""
    if not manifest_path.exists():
        return {}
    from src.fetch_corpus import load_manifest

    return {str(base["base_id"]): base for base in load_manifest(manifest_path)["bases"]}


def _split_selection(bases: Iterable[Any] | None) -> tuple[set[str], set[str]] | None:
    """Separa a seleção do roteador em (naturezas, base_ids explícitos).

    Um item que é nome de natureza (``Natureza`` ou "estruturada") seleciona
    todas as bases daquela natureza; qualquer outro valor é tratado como
    ``base_id``.
    """
    if bases is None:
        return None
    if isinstance(bases, str):
        bases = [bases]
    naturezas: set[str] = set()
    base_ids: set[str] = set()
    for base in bases:
        value = str(getattr(base, "value", base))
        (naturezas if value in _NATUREZAS else base_ids).add(value)
    return naturezas, base_ids


def _record_base_id(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    return str(record.get("base_id") or metadata.get("base_id") or "")


def _record_natureza(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    """Natureza do registro; sem o campo, vem do manifesto pelo ``base_id``."""
    natureza = record.get("natureza") or metadata.get("natureza")
    if natureza:
        return str(getattr(natureza, "value", natureza))
    declarada = _manifest_bases().get(_record_base_id(record, metadata), {}).get("natureza")
    return str(declarada) if declarada else None


def _record_escopo(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    escopo = _manifest_bases().get(_record_base_id(record, metadata), {}).get("escopo")
    return str(escopo) if escopo else None


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
        if candidate.count(",") != 1:
            return None
        integer, fraction = candidate.split(",")
        if "." in integer:
            if not re.fullmatch(r"[+-]?[1-9]\d{0,2}(?:\.\d{3})+", integer):
                return None
            integer = integer.replace(".", "")
        candidate = f"{integer}.{fraction}"
    elif candidate.count(".") > 1:
        if not re.fullmatch(r"[+-]?[1-9]\d{0,2}(?:\.\d{3})+", candidate):
            return None
        candidate = candidate.replace(".", "")
    elif candidate.count(".") == 1:
        integer, fraction = candidate.split(".")
        if len(fraction) == 3 and re.fullmatch(r"[+-]?[1-9]\d{0,2}", integer):
            candidate = f"{integer}{fraction}"
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


def _condicoes(expected: Any) -> list[tuple[str, Any]]:
    """Normaliza o valor de um filtro numa lista de condições (operator, value).

    Mantém os formatos já suportados (valor direto = igualdade; dict com
    "operator"/"op" e "value"; tupla/lista de 2 elementos (operator, value))
    e acrescenta um formato novo: lista de dicts, para expressar várias
    condições sobre a MESMA chave com semântica E — ex. uma faixa de valor
    (>= mínimo e <= máximo) ou um intervalo de datas (>= início do ano e <
    início do ano seguinte), usado por ``src/query_analysis.py``.
    """
    if isinstance(expected, Mapping):
        operator = str(expected.get("operator", expected.get("op", "==")))
        return [(operator, expected.get("value"))]
    if isinstance(expected, (tuple, list)):
        if expected and all(isinstance(item, Mapping) for item in expected):
            return [
                (str(item.get("operator", item.get("op", "=="))), item.get("value"))
                for item in expected
            ]
        if len(expected) == 2:
            return [(str(expected[0]), expected[1])]
    return [("==", expected)]


def _condicao_satisfeita(actual: Any, operator: str, value: Any) -> bool:
    try:
        if operator in {"==", "="}:
            return _values_equal(actual, value)
        if operator == "!=":
            return not _values_equal(actual, value)
        if operator in {">", ">=", "<", "<="}:
            left_date, right_date = _parse_date(actual), _parse_date(value)
            if left_date is not None or right_date is not None:
                if left_date is None or right_date is None:
                    return False
                left, right = left_date, right_date
            else:
                left_number, right_number = _parse_number(actual), _parse_number(value)
                if left_number is not None or right_number is not None:
                    if left_number is None or right_number is None:
                        return False
                    left, right = left_number, right_number
                else:
                    left, right = actual, value
            if type(left) is not type(right):
                return False
            comparisons = {
                ">": left > right,
                ">=": left >= right,
                "<": left < right,
                "<=": left <= right,
            }
            return comparisons[operator]
        if operator == "in":
            candidates = value if isinstance(value, (list, tuple, set)) else [value]
            return any(_values_equal(actual, candidate) for candidate in candidates)
        if operator == "contains":
            if isinstance(actual, str):
                return str(value) in actual
            return value in actual
        return False
    except (TypeError, ValueError, KeyError):
        return False


def _filtro_opcional(expected: Any) -> bool:
    """Filtro com ``"optional": True`` não descarta chunk que não tem o campo."""
    if isinstance(expected, Mapping):
        return bool(expected.get("optional"))
    if isinstance(expected, (tuple, list)) and expected:
        return all(isinstance(item, Mapping) and item.get("optional") for item in expected)
    return False


def _matches_filters(metadata: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = _metadata_value(metadata, key)
        if actual is None:
            if _filtro_opcional(expected):
                continue
            return False
        for operator, value in _condicoes(expected):
            if not _condicao_satisfeita(actual, operator, value):
                return False
    return True


def _records_for(
    index_dir: Path,
    bases: Iterable[Any] | None,
    filters: Mapping[str, Any] | None,
    records: Iterable[Mapping[str, Any]] | None = None,
    escopos: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    selection = _split_selection(bases)
    allowed_scopes = set(escopos) if escopos is not None else None
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
        if not chunk_id or chunk_id in seen_ids or not _matches_filters(metadata, filters):
            continue
        if selection is not None:
            naturezas, base_ids = selection
            if (
                _record_natureza(record, metadata) not in naturezas
                and _record_base_id(record, metadata) not in base_ids
            ):
                continue
        if allowed_scopes is not None and _record_escopo(record, metadata) not in allowed_scopes:
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


def _embed_fn_com_uso(uso: list[TokenUsage]) -> EmbedFn:
    """Embedding padrão (servidor da turma) que anota o ``usage`` de cada chamada.

    Usado por ``retrieve`` para preencher ``tokens`` do stage
    ``retrieval_denso``; quem passa ``embed_fn`` próprio fica sem contagem.
    """
    def embed(texts: list[str]) -> list[list[float]]:
        # Cliente criado só na chamada: sem candidatos, dense_retrieve nem embeda.
        from openai import OpenAI

        client = OpenAI(base_url=config.OLLAMA_URL, api_key=config.OLLAMA_API_KEY)
        response = client.embeddings.create(model=config.EMBEDDING_MODEL, input=texts)
        uso.append(token_usage_from_response(response))
        return [item.embedding for item in response.data]

    return embed


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
    escopos: Iterable[str] | None = None,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
) -> list[RetrievalResult]:
    """Recupera os vetores mais próximos usando o índice persistido."""
    if top_k <= 0 or not query.strip():
        return []
    candidates = _records_for(index_dir, bases, filters, records, escopos)
    if not candidates:
        return []
    query_vector = (embed_fn or (lambda texts: [_default_embed(texts[0])]))([query])[0]
    normalized_text = {
        str(item.get("chunk_id")): str(item.get("text", ""))
        for item in _read_jsonl(documents_file)
    }
    scored: list[RetrievalResult] = []
    incompatible_embeddings: list[dict[str, Any]] = []
    missing_embeddings: list[dict[str, str]] = []
    for record in candidates:
        embedding = record.get("embedding", [])
        metadata = record.get("metadata")
        chunk_id = str(record.get("chunk_id") or (
            metadata.get("chunk_id", "") if isinstance(metadata, Mapping) else ""
        ))
        if embedding is None:
            missing_embeddings.append({"chunk_id": chunk_id})
            continue
        if len(embedding) != len(query_vector):
            incompatible_embeddings.append(
                {
                    "chunk_id": chunk_id,
                    "expected_dimension": len(query_vector),
                    "actual_dimension": len(embedding),
                }
            )
            continue
        scored.append(
            _result(
                record,
                _cosine(query_vector, embedding),
                str(record.get("text", "")) or normalized_text.get(str(record.get("chunk_id")), ""),
            )
        )
    if incompatible_embeddings:
        registrar(
            run_id=run_id,
            query_id=query_id,
            stage=Stage.RETRIEVAL_DENSO,
            status="aviso",
            latencia_ms=0,
            input=query,
            output=f"{len(incompatible_embeddings)} embedding(s) incompatível(is) excluído(s)",
            extra={"embedding_dimension_mismatches": incompatible_embeddings},
        )
    if missing_embeddings:
        registrar(
            run_id=run_id,
            query_id=query_id,
            stage=Stage.RETRIEVAL_DENSO,
            status="aviso",
            latencia_ms=0,
            input=query,
            output=f"{len(missing_embeddings)} embedding(s) ausente(s)/nulo(s) ignorado(s)",
            extra={"missing_embeddings": missing_embeddings},
        )
    return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:top_k]


def _tokens(text: str) -> list[str]:
    """Tokens em minúsculas e sem acento: "médico" casa com "MEDICO" do catálogo."""
    sem_acento = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    return [token.lower() for token in _TOKEN_RE.findall(sem_acento)]


def bm25_retrieve(
    query: str,
    *,
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
    top_k: int = DEFAULT_TOP_K,
    bases: Iterable[Any] | None = None,
    filters: Mapping[str, Any] | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
    escopos: Iterable[str] | None = None,
) -> list[RetrievalResult]:
    """Recupera chunks lexicalmente com BM25, sem dependências adicionais."""
    if top_k <= 0 or not query.strip():
        return []
    candidates = (
        _records_for(index_dir, bases, filters, records, escopos)
        if records is not None
        else _records_for(index_dir, bases, filters, _read_jsonl(documents_file), escopos)
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
    escopos: Iterable[str] | None = None,
    fetch_k: int | None = None,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
) -> list[RetrievalResult]:
    """Executa dense + BM25 + RRF e aplica reranker somente se fornecido.

    ``escopos`` restringe a busca às bases com esse ``escopo`` no manifesto
    (ex.: ``("oficial",)``); ``None`` não filtra por escopo.

    ``fetch_k`` é quantos candidatos a busca densa e a BM25 devolvem, cada uma,
    antes da fusão; o RRF escolhe os ``top_k`` finais entre eles. Com
    ``fetch_k == top_k``, um chunk que as duas buscas põem logo abaixo do corte
    nunca chega à fusão. Padrão: ``max(5 * top_k, DEFAULT_FETCH_K)``.

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
    fetch_k = fetch_k if fetch_k is not None else max(5 * top_k, DEFAULT_FETCH_K)
    extra = {
        "bases": sorted(str(getattr(b, "value", b)) for b in ([bases] if isinstance(bases, str) else bases or [])),
        "escopos": sorted(escopos) if escopos is not None else None,
        "filtros": dict(route_filters or {}),
        "k": top_k,
        "fetch_k": fetch_k,
    }

    def _span(stage: Stage, **mais: Any):
        return default_logger.span(
            run_id=run_id, query_id=query_id, stage=stage, input=query, extra={**extra, **mais}
        )

    uso_embedding: list[TokenUsage] = []
    embed_da_consulta = embed_fn or _embed_fn_com_uso(uso_embedding)
    with _span(Stage.RETRIEVAL_DENSO) as span:
        dense = dense_retrieve(
            query,
            index_dir=index_dir,
            documents_file=documents_file,
            top_k=fetch_k,
            bases=bases,
            filters=route_filters,
            embed_fn=embed_da_consulta,
            escopos=escopos,
            run_id=run_id,
            query_id=query_id,
        )
        span.set_result(_resumo(dense))
        if uso_embedding:
            span.modelo = config.EMBEDDING_MODEL
            span.tokens = TokenUsage(
                prompt=sum(u.prompt for u in uso_embedding),
                completion=sum(u.completion for u in uso_embedding),
                total=sum(u.total for u in uso_embedding),
            )
    with _span(Stage.RETRIEVAL_BM25) as span:
        sparse = bm25_retrieve(
            query,
            index_dir=index_dir,
            documents_file=documents_file,
            top_k=fetch_k,
            bases=bases,
            filters=route_filters,
            escopos=escopos,
        )
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