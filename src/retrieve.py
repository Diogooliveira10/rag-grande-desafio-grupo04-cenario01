from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
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
_PNCP_CONTROL_RE = re.compile(r"(?<!\d)\d{14}-\d-\d{6}/\d{4}(?:-\d{6})?(?!\d)")
MANIFEST_PATH = config.ROOT_DIR / "corpus_manifest.yaml"
_NATUREZAS = frozenset(item.value for item in Natureza)

EmbedFn = Callable[[list[str]], list[list[float]]]
RerankFn = Callable[[str, list["RetrievalResult"]], Sequence[Any]]
QUERY_INSTRUCTION = "Given a public procurement question, retrieve relevant passages that answer the query"
QUERY_CACHE_PATH = config.ROOT_DIR / "data" / "embeddings" / "query_cache.jsonl"


def embedding_query_text(query: str) -> str:
    """Instrução Qwen exclusiva da busca densa; BM25/roteamento recebem o original."""
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery:{query}"


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
    rerank_score: float | None = None
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
            "rerank_score": self.rerank_score,
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


def _sem_caixa_e_acento(texto: str) -> str:
    sem_acento = "".join(
        char for char in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(char)
    )
    return " ".join(sem_acento.split())


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
                # Sem caixa nem acento: "Secretaria de Saúde" (pergunta) casa
                # com "SECRETARIA DE ESTADO DE SAUDE" (razaoSocial do PNCP).
                return _sem_caixa_e_acento(str(value)) in _sem_caixa_e_acento(actual)
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


_COMPARACOES = frozenset({">", ">=", "<", "<="})


def _sem_valor_comparavel(actual: Any, expected: Any) -> bool:
    """Campo presente, mas vazio ou ilegível para uma comparação (``""``,
    ``"Sigiloso"``, ``{}``): num filtro opcional, conta como campo ausente."""
    if actual in ("", [], {}) or (isinstance(actual, str) and not actual.strip()):
        return True
    operadores = {operator for operator, _ in _condicoes(expected)}
    if operadores and operadores <= _COMPARACOES:
        return _parse_date(actual) is None and _parse_number(actual) is None
    return False


def _matches_filters(metadata: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = _metadata_value(metadata, key)
        if actual is None or (_filtro_opcional(expected) and _sem_valor_comparavel(actual, expected)):
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
        declarations = _manifest_bases()
        paths = [path for path in paths if not declarations.get(path.parent.name) or (
            (selection is None or path.parent.name in selection[1]
             or declarations[path.parent.name].get("natureza") in selection[0])
            and (allowed_scopes is None
                 or declarations[path.parent.name].get("escopo", "oficial") in allowed_scopes)
        )]
        raw = [record for path in paths for record in _read_jsonl(path)]
    else:
        raw = [dict(record) for record in records]
    filtered: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in raw:
        metadata = _metadata_of(record)
        chunk_id = str(record.get("chunk_id") or metadata.get("chunk_id") or "")
        if not chunk_id or chunk_id in seen_ids or not _matches_filters(metadata, filters):
            continue
        # Texto ilegível (src/qualidade_corpus.py) não é evidência: já fica fora
        # do índice denso, e o BM25 lê o documents.jsonl, onde ele continua.
        if _metadata_value(metadata, "status_extracao") == "texto_ilegivel":
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


def _metadata_of(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Metadados do registro: o índice guarda em ``metadata``; o
    ``documents.jsonl`` de ``prepare_documents.py`` guarda no próprio registro."""
    metadata = record.get("metadata")
    return metadata if isinstance(metadata, Mapping) else record


def _filtrar_por_base(
    records: Iterable[Mapping[str, Any]],
    filters: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, list[str]]]]:
    """Aplica os filtros base a base, sem deixar um filtro zerar a busca.

    Numa base, um filtro só vale se ao menos um chunk dela tem o campo (ex.:
    ``valor`` não existe na base normativa) e se ao menos um desses chunks
    passa nele: um filtro que reprova todos os chunks que têm o campo está
    lendo errado aquela base (ex.: órgão "Secretaria de Saúde" contra os
    indicadores do PCA, cujo órgão é "Estado do Rio de Janeiro"), e deixaria
    só os chunks sem o campo. Se os filtros que sobram, juntos, excluem todos
    os chunks da base, ela entra sem filtro. Devolve os registros e, por
    ``base_id``, as chaves ignoradas (``sem_campo`` / ``nenhum_chunk_passou``
    / ``zerou_resultados``).
    """
    por_base: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        base_id = _record_base_id(record, _metadata_of(record))
        por_base.setdefault(base_id, []).append(dict(record))
    selecionados: list[dict[str, Any]] = []
    ignorados: dict[str, dict[str, list[str]]] = {}
    for base_id, registros in por_base.items():
        metadados = [_metadata_of(record) for record in registros]
        motivo: dict[str, list[str]] = {}
        aplicaveis: dict[str, Any] = {}
        for key, value in filters.items():
            com_campo = [m for m in metadados if _metadata_value(m, key) is not None]
            if not com_campo:
                motivo.setdefault("sem_campo", []).append(key)
            elif not any(_matches_filters(m, {key: value}) for m in com_campo):
                motivo.setdefault("nenhum_chunk_passou", []).append(key)
            else:
                aplicaveis[key] = value
        for chave in motivo:
            motivo[chave].sort()
        passam = [
            record
            for record, metadata in zip(registros, metadados)
            if _matches_filters(metadata, aplicaveis)
        ]
        if aplicaveis and not passam:
            passam = registros
            motivo["zerou_resultados"] = sorted(aplicaveis)
        if motivo:
            ignorados[base_id] = motivo
        selecionados.extend(passam)
    return selecionados, ignorados


def _result(record: Mapping[str, Any], score: float, content: str = "") -> RetrievalResult:
    metadata = dict(_metadata_of(record))
    metadata.pop("text", None)
    metadata.pop("embedding", None)
    return RetrievalResult(
        chunk_id=str(record.get("chunk_id") or metadata.get("chunk_id", "")),
        content=content or str(record.get("text", "")),
        metadata=metadata,
        score=score,
        document_id=record.get("document_id"),
        source_path=record.get("source_path"),
        _record=dict(record),
    )


def _identificadores_pncp(query: str) -> list[str]:
    return list(dict.fromkeys(_PNCP_CONTROL_RE.findall(query)))


def _identificador_em_resultado(identificador: str, item: RetrievalResult) -> bool:
    metadata = item.metadata
    extras = metadata.get("extras")
    return identificador in item.content or (
        isinstance(extras, Mapping)
        and identificador in json.dumps(extras, ensure_ascii=False, default=str)
    )


def _diagnostico_identificadores(
    query: str, resultados: Sequence[RetrievalResult]
) -> list[dict[str, Any]]:
    diagnosticos = []
    for identificador in _identificadores_pncp(query):
        matches = [
            {
                "rank": rank,
                "chunk_id": item.chunk_id,
                "score": item.score,
                "dense_score": item.dense_score,
                "sparse_score": item.sparse_score,
                "rrf_score": item.rrf_score,
                "exact_lookup": bool((item.metadata.get("extras") or {}).get("identificador_pncp_exato")),
            }
            for rank, item in enumerate(resultados, start=1)
            if _identificador_em_resultado(identificador, item)
        ]
        diagnosticos.append({"identificador": identificador, "matches": matches})
    return diagnosticos


def _resultados_identificador_exato(
    query: str,
    records: Iterable[Mapping[str, Any]],
) -> list[RetrievalResult]:
    identificadores = _identificadores_pncp(query)
    if not identificadores:
        return []
    resultados: list[RetrievalResult] = []
    vistos: set[str] = set()
    for record in records:
        metadata = _metadata_of(record)
        extras = metadata.get("extras")
        texto_busca = str(record.get("text", ""))
        if isinstance(extras, Mapping):
            texto_busca += json.dumps(extras, ensure_ascii=False, default=str)
        if not any(identificador in texto_busca for identificador in identificadores):
            continue
        item = _result(record, 0.0, str(record.get("text", "")))
        if item.chunk_id in vistos:
            continue
        vistos.add(item.chunk_id)
        item.metadata = {
            **item.metadata,
            "extras": {
                **(item.metadata.get("extras") or {}),
                "identificador_pncp_exato": True,
            },
        }
        resultados.append(item)
    return sorted(resultados, key=lambda item: item.chunk_id)


def _promover_identificadores_exatos(
    resultados: Sequence[RetrievalResult],
    identificadores_exatos: Sequence[RetrievalResult],
    limite: int,
) -> list[RetrievalResult]:
    por_id = {item.chunk_id: item for item in resultados}
    exatos = []
    for item in identificadores_exatos:
        existente = por_id.get(item.chunk_id)
        if existente is not None:
            existente.metadata = item.metadata
            exatos.append(existente)
        else:
            exatos.append(item)
    ids_exatos = {item.chunk_id for item in exatos}
    ordenados = sorted(exatos, key=lambda item: item.chunk_id) + [
        item for item in resultados if item.chunk_id not in ids_exatos
    ]
    return ordenados[:limite]


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
            "identificador_pncp_exato": bool(
                (item.metadata.get("extras") or {}).get("identificador_pncp_exato")
            ),
        }
        for rank, item in enumerate(resultados[:limite], start=1)
    ]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def _default_embed(query: str) -> list[float]:
    return _embed_fn_com_uso([])([query])[0]


def _embed_fn_com_uso(uso: list[TokenUsage], estatisticas: dict[str, int] | None = None) -> EmbedFn:
    """Embedding padrão (servidor da turma) que anota o ``usage`` de cada chamada.

    Usado por ``retrieve`` para preencher ``tokens`` do stage
    ``retrieval_denso``; quem passa ``embed_fn`` próprio fica sem contagem.
    """
    def embed(texts: list[str]) -> list[list[float]]:
        encoded = [embedding_query_text(text) for text in texts]
        chave = {"endpoint": config.OLLAMA_URL, "model": config.EMBEDDING_MODEL, "texts": encoded}
        for record in _read_jsonl(QUERY_CACHE_PATH):
            if record.get("key") == chave:
                vectors = record["vectors"]
                if len(vectors) == len(texts) and all(
                    vector and all(math.isfinite(v) for v in vector) and any(vector)
                    for vector in vectors
                ):
                    uso.append(TokenUsage())
                    if estatisticas is not None:
                        estatisticas["cache_hits"] += 1
                    return vectors
        # Cliente criado só na chamada: sem candidatos, dense_retrieve nem embeda.
        from openai import OpenAI
        from src.obs import aguardar_modelo

        aguardar_modelo()
        client = OpenAI(base_url=config.OLLAMA_URL, api_key=config.OLLAMA_API_KEY, timeout=60.0, max_retries=0)
        response = client.embeddings.create(model=config.EMBEDDING_MODEL, input=encoded)
        uso.append(token_usage_from_response(response))
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: getattr(item, "index", 0))]
        if len(vectors) != len(texts) or not all(
            vector and all(math.isfinite(v) for v in vector) and any(vector) for vector in vectors
        ):
            raise ValueError("ECOA retornou embeddings de query inválidos")
        QUERY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with QUERY_CACHE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": chave, "vectors": vectors}, ensure_ascii=False) + "\n")
        if estatisticas is not None:
            estatisticas["calls"] += 1
        return vectors

    return embed


def _faiss_scores(
    query_vector: Sequence[float],
    candidates: list[dict[str, Any]],
    indexed_records: list[dict[str, Any]],
    *,
    index_path: Path | None,
    top_k: int,
) -> dict[str, float]:
    """Busca candidatos no índice persistido ou monta um índice FAISS em memória."""
    import faiss
    import numpy as np

    dimension = len(query_vector)
    if not dimension:
        return {str(record.get("chunk_id", "")): 0.0 for record in candidates}

    candidate_ids = {str(record.get("chunk_id", "")) for record in candidates}
    if index_path is not None and index_path.exists():
        index = faiss.read_index(str(index_path))
        if index.ntotal != len(indexed_records):
            raise RuntimeError(
                f"índice FAISS desatualizado em {index_path}; regenere com src.index"
            )
        if index.d != dimension:
            return {chunk_id: 0.0 for chunk_id in candidate_ids}
    else:
        vectors = np.zeros((len(indexed_records), dimension), dtype=np.float32)
        for row, record in enumerate(indexed_records):
            embedding = record.get("embedding", [])
            if isinstance(embedding, list) and len(embedding) == dimension:
                vectors[row] = embedding
        vectors = np.ascontiguousarray(vectors)
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(dimension)
        index.add(vectors)

    query = np.ascontiguousarray([query_vector], dtype=np.float32)
    faiss.normalize_L2(query)
    target = min(top_k, len(candidate_ids))
    search_k = min(index.ntotal, max(1, target))
    scores_by_id: dict[str, float] = {}
    while index.ntotal:
        scores, positions = index.search(query, search_k)
        scores_by_id = {}
        for score, position in zip(scores[0], positions[0]):
            if position < 0:
                continue
            chunk_id = str(indexed_records[int(position)].get("chunk_id", ""))
            if chunk_id in candidate_ids:
                scores_by_id[chunk_id] = float(score)
        if len(scores_by_id) >= target or search_k >= index.ntotal:
            break
        search_k = min(index.ntotal, max(search_k + 1, search_k * 2))
    return scores_by_id


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
    """Recupera chunks próximos pelo FAISS e aplica filtros de metadados."""
    if top_k <= 0 or not query.strip():
        return []
    candidates = _records_for(index_dir, bases, filters, records, escopos)
    if not candidates:
        return []

    query_vector = (embed_fn or (lambda texts: [_default_embed(texts[0])]))([query])[0]
    candidates_by_base: dict[str, list[dict[str, Any]]] = {}
    for record in candidates:
        metadata = record.get("metadata")
        base_id = _record_base_id(
            record, metadata if isinstance(metadata, Mapping) else {}
        )
        candidates_by_base.setdefault(base_id, []).append(record)

    indexed_by_base: dict[str, list[dict[str, Any]]] = {}
    index_paths: dict[str, Path] = {}
    if records is None:
        for vectors_path in sorted(index_dir.glob("*/vectors.jsonl")):
            base_id = vectors_path.parent.name
            indexed_by_base[base_id] = _read_jsonl(vectors_path)
            index_paths[base_id] = vectors_path.parent / "vectors.faiss"
    else:
        indexed_by_base = {base_id: group for base_id, group in candidates_by_base.items()}

    normalized_text = {
        str(item.get("chunk_id")): str(item.get("text", ""))
        for item in _read_jsonl(documents_file)
    }
    scored: list[RetrievalResult] = []
    incompatible_embeddings: list[dict[str, Any]] = []
    missing_embeddings: list[dict[str, str]] = []
    compatible_candidates_by_base: dict[str, list[dict[str, Any]]] = {}
    for record in candidates:
        embedding = record.get("embedding", [])
        metadata = record.get("metadata")
        chunk_id = str(record.get("chunk_id") or (
            metadata.get("chunk_id", "") if isinstance(metadata, Mapping) else ""
        ))
        if embedding is None:
            missing_embeddings.append({"chunk_id": chunk_id})
            continue
        try:
            embedding_dimension = len(embedding)
        except TypeError:
            embedding_dimension = None
        if embedding_dimension != len(query_vector):
            incompatible_embeddings.append(
                {
                    "chunk_id": chunk_id,
                    "expected_dimension": len(query_vector),
                    "actual_dimension": embedding_dimension,
                }
            )
            continue
        compatible_candidates_by_base.setdefault(
            _record_base_id(record, metadata if isinstance(metadata, Mapping) else {}), []
        ).append(record)
    for base_id, group in compatible_candidates_by_base.items():
        indexed_records = indexed_by_base.get(base_id, group)
        faiss_path = index_paths.get(base_id)
        scores = _faiss_scores(
            query_vector,
            group,
            indexed_records,
            index_path=faiss_path if faiss_path and faiss_path.exists() else None,
            top_k=top_k,
        )
        for record in group:
            chunk_id = str(record.get("chunk_id", ""))
            if chunk_id not in scores:
                continue
            content = str(record.get("text", "")) or normalized_text.get(chunk_id, "")
            scored.append(_result(record, scores[chunk_id], content))
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
    rerank_k: int | None = None,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
) -> list[RetrievalResult]:
    """Executa dense + BM25 + RRF e aplica reranker somente se fornecido.

    Com ``reranker``, o RRF entrega ``rerank_k`` candidatos (padrão
    ``max(top_k, config.RERANK_CANDIDATES)``) e o reranker escolhe os ``top_k``
    finais entre eles — assim ele pode trazer para cima um chunk que o RRF
    deixou abaixo do corte. Se o reranker falhar (ex.: modelo indisponível), o
    stage ``rerank`` é registrado com ``status: "erro"`` e o resultado volta a
    ser o do RRF.

    ``escopos`` restringe a busca às bases com esse ``escopo`` no manifesto
    (ex.: ``("oficial",)``); ``None`` não filtra por escopo.

    ``fetch_k`` é quantos candidatos a busca densa e a BM25 devolvem, cada uma,
    antes da fusão; o RRF escolhe os ``top_k`` finais entre eles. Com
    ``fetch_k == top_k``, um chunk que as duas buscas põem logo abaixo do corte
    nunca chega à fusão. Padrão: ``max(5 * top_k, DEFAULT_FETCH_K)``.

    Os filtros são aplicados base a base (``_filtrar_por_base``): numa base sem
    o campo filtrado, ou em que o filtro excluiria todos os chunks, ele é
    ignorado, e o log registra quais chaves foram ignoradas em
    ``extra.filtros_ignorados``. ``dense_retrieve`` e ``bm25_retrieve``, chamados
    diretamente, continuam aplicando os filtros de forma estrita.

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
    dense_records: list[dict[str, Any]] | None = None
    sparse_records: list[dict[str, Any]] | None = None
    filtros_ignorados: dict[str, dict[str, list[str]]] = {}
    if route_filters:
        # Seleção de bases e escopos antes, para decidir o filtro só entre as
        # bases que o roteador escolheu.
        dense_records, ignorados_denso = _filtrar_por_base(
            _records_for(index_dir, bases, None, None, escopos), route_filters
        )
        sparse_records, ignorados_bm25 = _filtrar_por_base(
            _records_for(index_dir, bases, None, _read_jsonl(documents_file), escopos), route_filters
        )
        filtros_ignorados = {**ignorados_bm25, **ignorados_denso}
    extra = {
        "bases": sorted(str(getattr(b, "value", b)) for b in ([bases] if isinstance(bases, str) else bases or [])),
        "escopos": sorted(escopos) if escopos is not None else None,
        "filtros": dict(route_filters or {}),
        "k": top_k,
        "fetch_k": fetch_k,
    }
    if filtros_ignorados:
        extra["filtros_ignorados"] = filtros_ignorados

    def _span(stage: Stage, **mais: Any):
        return default_logger.span(
            run_id=run_id, query_id=query_id, stage=stage, input=query, extra={**extra, **mais}
        )

    uso_embedding: list[TokenUsage] = []
    estatisticas_embedding = {"calls": 0, "cache_hits": 0}
    embed_da_consulta = embed_fn or _embed_fn_com_uso(uso_embedding, estatisticas_embedding)
    recuperacao_degradada = False
    with _span(Stage.RETRIEVAL_DENSO) as span:
        try:
            dense = dense_retrieve(
                query,
                index_dir=index_dir,
                documents_file=documents_file,
                top_k=fetch_k,
                bases=bases,
                filters=None if dense_records is not None else route_filters,
                embed_fn=embed_da_consulta,
                records=dense_records,
                escopos=escopos,
                run_id=run_id,
                query_id=query_id,
            )
        except Exception as error:
            from llm_ecoa import erro_de_disponibilidade
            if not erro_de_disponibilidade(error):
                raise
            dense = []
            recuperacao_degradada = True
            span.extra.update({"degraded": True, "fallback": "bm25", "error_type": type(error).__name__,
                               "status_code": getattr(error, "status_code", None)})
        span.set_result(_resumo(dense))
        span.extra["evidencias"] = _evidencias(dense)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(query, dense)
        span.extra.update({"query_embedding": estatisticas_embedding})
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
            filters=None if sparse_records is not None else route_filters,
            records=sparse_records,
            escopos=escopos,
        )
        span.set_result(_resumo(sparse))
        span.extra["evidencias"] = _evidencias(sparse)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(query, sparse)

    identificadores_exatos: list[RetrievalResult] = []
    if _identificadores_pncp(query):
        records_exatos = sparse_records
        if records_exatos is None:
            records_exatos = _records_for(
                index_dir,
                bases,
                None,
                _read_jsonl(documents_file),
                escopos,
            )
        identificadores_exatos = _resultados_identificador_exato(query, records_exatos)

    if reranker is not None:
        rerank_k = max(top_k, rerank_k if rerank_k is not None else config.RERANK_CANDIDATES)
    with _span(Stage.FUSAO_RRF, rrf_k=DEFAULT_RRF_K) as span:
        fused = reciprocal_rank_fusion([dense, sparse], top_k=rerank_k if reranker is not None else top_k)
        fused = _promover_identificadores_exatos(
            fused,
            identificadores_exatos,
            rerank_k if reranker is not None else top_k,
        )
        for item in fused:
            if recuperacao_degradada:
                item.metadata = {**item.metadata, "extras": {
                    **item.metadata.get("extras", {}), "recuperacao_degradada": "bm25_sem_embedding",
                }}
            ignored = filtros_ignorados.get(str(item.metadata.get("base_id")), {})
            if ignored:
                item.metadata = {**item.metadata, "extras": {
                    **item.metadata.get("extras", {}), "filtros_relaxados": ignored,
                }}
        span.set_result(_resumo(fused))
        span.extra["evidencias"] = _evidencias(fused)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(query, fused)
        span.extra["identificadores_exactos_adicionados"] = [
            item.chunk_id for item in identificadores_exatos
        ]
    if reranker is not None and fused:
        try:
            with _span(Stage.RERANK, rerank_k=rerank_k) as span:
                span.modelo = getattr(reranker, "modelo", None)
                span.extra["candidatos"] = len(fused)
                candidates = {item.chunk_id: item for item in fused}
                ranked = list(reranker(query, fused))
                if getattr(reranker, "backend_usado", None):
                    span.extra["rerank_backend"] = reranker.backend_usado
                if {item.chunk_id for item in ranked} - candidates.keys():
                    raise ValueError("Reranker devolveu evidência fora dos candidatos")
                reranked = []
                seen = set()
                for item in ranked:
                    if item.chunk_id not in seen:
                        original = candidates[item.chunk_id]
                        reranked.append(replace(original,
                            score=getattr(item, "score", original.score),
                            rerank_score=getattr(item, "rerank_score", original.rerank_score)))
                        seen.add(item.chunk_id)
                if not reranked:
                    raise ValueError("Reranker vazio")
                reranked = _promover_identificadores_exatos(reranked, identificadores_exatos, top_k)
                span.set_result(_resumo(reranked))
                span.extra["evidencias"] = _evidencias(reranked)
                span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(
                    query, reranked
                )
            return reranked
        except Exception:  # o span registra o erro; segue com os candidatos originais
            return fused[:top_k]
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
