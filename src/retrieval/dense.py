"""Busca densa: vetor da pergunta contra o índice FAISS de cada base.

Separado de src/retrieve.py em 2026-11-12 sem mudar a lógica; os nomes
continuam importáveis de lá. Quem troca ``registrar`` (aviso de vetor
descartado) num teste troca neste módulo.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.obs import Stage, registrar
from src.retrieval.query_embedding import EmbedFn, _default_embed
from src.retrieval.records import RetrievalResult, _read_jsonl, _record_base_id, _registros_do_indice, _result
from src.retrieval.selection import DEFAULT_TOP_K, _caminhos_ou_padrao, _records_for, _selecionar


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


_FAISS_CACHE: dict[str, tuple[int, int, Any]] = {}


def _ler_indice_faiss(index_path: Path) -> Any:
    """Índice persistido, lido do disco só quando o arquivo muda."""
    import faiss

    stat = index_path.stat()
    chave = str(index_path)
    guardado = _FAISS_CACHE.get(chave)
    if guardado and guardado[0] == stat.st_mtime_ns and guardado[1] == stat.st_size:
        return guardado[2]
    index = faiss.read_index(chave)
    _FAISS_CACHE[chave] = (stat.st_mtime_ns, stat.st_size, index)
    return index


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
        index = _ler_indice_faiss(index_path)
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
    index_dir: Path | None = None,
    documents_file: Path | None = None,
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
    index_dir, documents_file = _caminhos_ou_padrao(index_dir, documents_file)
    candidates = (_selecionar(index_dir, bases, escopos) if not filters and records is None
                  else _records_for(index_dir, bases, filters, records, escopos))
    if not candidates:
        return []

    query_vector = (embed_fn or (lambda texts: [_default_embed(texts[0])]))([query])[0]
    indexed_by_base, index_paths = _indices_das_bases(index_dir, _por_base(candidates), records)
    bases_com_faiss = {base_id for base_id, caminho in index_paths.items() if caminho.exists()}
    normalized_text = {
        str(item.get("chunk_id")): str(item.get("text", ""))
        for item in _read_jsonl(documents_file)
    }
    compatible_candidates_by_base, incompatible_embeddings, missing_embeddings = _candidatos_com_vetor(
        candidates, bases_com_faiss, len(query_vector))
    scored: list[RetrievalResult] = []
    for base_id, group in compatible_candidates_by_base.items():
        faiss_path = index_paths.get(base_id)
        scores = _faiss_scores(
            query_vector,
            group,
            indexed_by_base.get(base_id, group),
            index_path=faiss_path if faiss_path and faiss_path.exists() else None,
            top_k=top_k,
        )
        for record in group:
            chunk_id = str(record.get("chunk_id", ""))
            if chunk_id not in scores:
                continue
            content = str(record.get("text", "")) or normalized_text.get(chunk_id, "")
            scored.append(_result(record, scores[chunk_id], content))
    _avisar_vetores_descartados(query, incompatible_embeddings, missing_embeddings,
                                run_id=run_id, query_id=query_id)
    return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:top_k]


def _base_do(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata")
    return _record_base_id(record, metadata if isinstance(metadata, Mapping) else {})


def _por_base(candidates: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    por_base: dict[str, list[Mapping[str, Any]]] = {}
    for record in candidates:
        por_base.setdefault(_base_do(record), []).append(record)
    return por_base


def _indices_das_bases(index_dir: Path, candidates_by_base: dict[str, list[Mapping[str, Any]]],
                       records: Iterable[Mapping[str, Any]] | None) -> tuple[dict[str, list], dict[str, Path]]:
    """Registros do índice e caminho do FAISS de cada base com candidatos.

    Com ``records`` dados por quem chama, o próprio grupo de candidatos é o índice.
    """
    if records is not None:
        indexed_by_base_recebidos: dict[str, list] = dict(candidates_by_base)
        caminhos: dict[str, Path] = {}
        # Recorte filtrado que veio do índice: os registros foram lidos sem o
        # vetor, que está no FAISS. Sem o FAISS aqui, todos viravam "dimensão 0"
        # e a busca densa ficava vazia em qualquer pergunta com filtro (desde
        # 2026-10-19, quando o índice passou a ser lido sem o vetor).
        for base_id, grupo in candidates_by_base.items():
            faiss_path = index_dir / base_id / "vectors.faiss"
            if faiss_path.exists() and any("embedding" not in registro for registro in grupo):
                indexed_by_base_recebidos[base_id] = _registros_do_indice(index_dir / base_id / "vectors.jsonl")
                caminhos[base_id] = faiss_path
        return indexed_by_base_recebidos, caminhos
    indexed_by_base: dict[str, list[dict[str, Any]]] = {}
    index_paths: dict[str, Path] = {}
    # Só as bases que têm candidatos: com a v7, ler todas custava 3,4 GB
    # de JSON (2,5 GB só dos contratos) para uma pergunta sobre normas.
    for vectors_path in sorted(index_dir.glob("*/vectors.jsonl")):
        base_id = vectors_path.parent.name
        if base_id not in candidates_by_base:
            continue
        indexed_by_base[base_id] = _registros_do_indice(vectors_path)
        index_paths[base_id] = vectors_path.parent / "vectors.faiss"
    return indexed_by_base, index_paths


def _candidatos_com_vetor(candidates: Iterable[Mapping[str, Any]], bases_com_faiss: set[str],
                          dimensao: int) -> tuple[dict[str, list], list[dict[str, Any]], list[dict[str, str]]]:
    """Candidatos que podem ser comparados com o vetor da pergunta, por base.

    Devolve também os descartados: vetor com outra dimensão e vetor ausente.
    """
    compativeis: dict[str, list[Mapping[str, Any]]] = {}
    incompativeis: list[dict[str, Any]] = []
    ausentes: list[dict[str, str]] = []
    for record in candidates:
        embedding = record.get("embedding", [])
        metadata = record.get("metadata")
        chunk_id = str(record.get("chunk_id") or (
            metadata.get("chunk_id", "") if isinstance(metadata, Mapping) else ""
        ))
        base_do_registro = _base_do(record)
        if "embedding" not in record and base_do_registro in bases_com_faiss:
            # Lido sem o vetor (_registros_do_indice): o FAISS tem o vetor e
            # confere a dimensão em _faiss_scores.
            compativeis.setdefault(base_do_registro, []).append(record)
            continue
        if embedding is None:
            ausentes.append({"chunk_id": chunk_id})
            continue
        try:
            embedding_dimension = len(embedding)
        except TypeError:
            embedding_dimension = None
        if embedding_dimension != dimensao:
            incompativeis.append({
                "chunk_id": chunk_id,
                "expected_dimension": dimensao,
                "actual_dimension": embedding_dimension,
            })
            continue
        compativeis.setdefault(base_do_registro, []).append(record)
    return compativeis, incompativeis, ausentes


# Exemplos de vetor descartado que vão para o log; o resto entra só na contagem.
# O aviso listava todos: 106 mil contratos por pergunta, 461 MB do log de 26/10.
_EXEMPLOS_DE_DESCARTE = 20


def _avisar_vetores_descartados(query: str, incompativeis: list[dict[str, Any]], ausentes: list[dict[str, str]],
                                *, run_id: str, query_id: str) -> None:
    """Aviso no log (stage ``retrieval_denso``) para cada tipo de vetor descartado,
    com a contagem e até ``_EXEMPLOS_DE_DESCARTE`` exemplos."""
    if incompativeis:
        registrar(
            run_id=run_id,
            query_id=query_id,
            stage=Stage.RETRIEVAL_DENSO,
            status="aviso",
            latencia_ms=0,
            input=query,
            output=f"{len(incompativeis)} embedding(s) incompatível(is) excluído(s)",
            extra={"embedding_dimension_mismatches": incompativeis[:_EXEMPLOS_DE_DESCARTE],
                   "total_descartados": len(incompativeis)},
        )
    if ausentes:
        registrar(
            run_id=run_id,
            query_id=query_id,
            stage=Stage.RETRIEVAL_DENSO,
            status="aviso",
            latencia_ms=0,
            input=query,
            output=f"{len(ausentes)} embedding(s) ausente(s)/nulo(s) ignorado(s)",
            extra={"missing_embeddings": ausentes[:_EXEMPLOS_DE_DESCARTE],
                   "total_descartados": len(ausentes)},
        )
