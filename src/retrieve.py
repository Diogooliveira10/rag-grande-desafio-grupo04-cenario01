from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import config
from src.obs import Stage, TokenUsage, default_logger, registrar, token_usage_from_response
from src.schema import Natureza

# As partes da busca moram em src/retrieval/ desde 2026-10-27; todos os nomes
# continuam importáveis daqui (from src.retrieve import ..., retrieve_module.X).
from src.retrieval.records import (  # noqa: F401
    EmbeddingUnavailable,
    MANIFEST_PATH,
    RetrievalResult,
    _CHAVES_DE_METADADO,
    _DOCUMENTOS_POR_ID,
    _JSONL_CACHE,
    _MetadadoDoDocumento,
    _NATUREZAS,
    _RE_EMBEDDING,
    _compartilhar_metadado,
    _documentos_por_id,
    _manifest_bases,
    _metadata_of,
    _read_jsonl,
    _record_base_id,
    _record_escopo,
    _record_natureza,
    _registro_sem_vetor,
    _registros_do_indice,
    _result,
    _split_selection,
)
from src.retrieval.lexical import (  # noqa: F401
    _INDICE_LEXICO_CACHE,
    _Pontuado,
    _TERMOS_CACHE,
    _TOKEN_RE,
    _bm25_pelo_indice,
    _bm25_por_varredura,
    _chunk_id_do_registro,
    _indice_lexico,
    _termos,
    _tokens,
)
from src.retrieval.identifiers import (  # noqa: F401
    _CNPJ_RE,
    _CONTRACT_NUMBER_RE,
    _PNCP_CONTROL_RE,
    _contrato_corresponde,
    _diagnostico_identificadores,
    _identificador_em_resultado,
    _identificadores_pncp,
    _promover_identificadores_exatos,
    _referencias_contrato,
    _resultados_identificador_exato,
    _variantes,
)
from src.retrieval.filters import (  # noqa: F401
    _COMPARACOES,
    _CONECTORES_DE_NOME,
    _comparable_values,
    _condicao_satisfeita,
    _condicoes,
    _filtrar_por_base,
    _filtro_aplicavel,
    _filtro_opcional,
    _matches_filters,
    _metadata_value,
    _parse_date,
    _parse_number,
    _sem_caixa_e_acento,
    _sem_valor_comparavel,
    _tokens_de_nome,
    _values_equal,
    filtro_opcional,
)
from src.retrieval.norms import (  # noqa: F401
    _ARTIGO_INICIAL,
    _NORMA_RE,
    _VISAO_GERAL_NORMA,
    _consulta_topica_norma,
    _norma_do_documento,
    _normas_mencionadas,
    _ordenar_candidatos_norma,
    _priorizar_norma_solicitada,
)
from src.retrieval.evidence import (  # noqa: F401
    _evidencias,
    _registrar_evidencias,
    _resumo,
)
from src.retrieval.ranking import (  # noqa: F401
    DEFAULT_RRF_K,
    _BASES_COM_VARIEDADE,
    _COMPRA_RE,
    _chave_da_compra,
    _variar_por_compra,
    reciprocal_rank_fusion,
)


DEFAULT_TOP_K = 10


DEFAULT_FETCH_K = 50


EmbedFn = Callable[[list[str]], list[list[float]]]


RerankFn = Callable[[str, list["RetrievalResult"]], Sequence[Any]]


QUERY_INSTRUCTION = "Given a public procurement question, retrieve relevant passages that answer the query"


QUERY_CACHE_PATH = config.ROOT_DIR / "data" / "embeddings" / "query_cache.jsonl"


_EMBEDDING_FAILURES: dict[tuple[str, str], float] = {}


def embedding_query_text(query: str) -> str:
    """Instrução Qwen exclusiva da busca densa; BM25/roteamento recebem o original."""
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery:{query}"


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
        raw = [record for path in paths for record in _registros_do_indice(path)]
    else:
        raw = records
    filtered: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in raw:
        metadata = _metadata_of(record)
        chunk_id = str(record.get("chunk_id") or metadata.get("chunk_id") or "")
        if not chunk_id or chunk_id in seen_ids or not _matches_filters(metadata, filters, content=str(record.get("text", ""))):
            continue
        # Texto ilegível (src/corpus_quality.py) não é evidência: já fica fora
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


# Seleção sem filtro (bases e escopos) por versão dos arquivos lidos. Uma pergunta
# percorria os ~144 mil registros 3 ou 4 vezes (busca densa, BM25, identificadores,
# base dos filtros), ~0,2 s cada, para chegar sempre à mesma lista.
_SELECAO_CACHE: dict[tuple[Any, ...], list[dict[str, Any]]] = {}


def _assinatura_da_origem(index_dir: Path, documents_file: Path | None) -> tuple[Any, ...] | None:
    """Muda quando muda o arquivo de onde a seleção sai; ``None`` se ele não existe."""
    try:
        if documents_file is not None:
            stat = Path(documents_file).stat()
            return ("documents", str(documents_file), stat.st_mtime_ns, stat.st_size)
        vetores = sorted(Path(index_dir).glob("*/vectors.jsonl"))
        return ("indice", str(index_dir), tuple(
            (str(v), v.stat().st_mtime_ns, v.stat().st_size, (v.parent / "vectors.faiss").exists()) for v in vetores))
    except OSError:
        return None


def _selecionar(index_dir: Path, bases: Iterable[Any] | None, escopos: Iterable[str] | None,
                *, documents_file: Path | None = None) -> list[dict[str, Any]]:
    """``_records_for`` sem filtros, do índice ou do ``documents.jsonl``, calculado
    uma vez por versão dos arquivos. Devolve cópia da lista; os registros são os
    mesmos dicts compartilhados de sempre (ninguém os altera)."""
    origem = _assinatura_da_origem(index_dir, documents_file)
    fonte = _read_jsonl(documents_file) if documents_file is not None else None
    if origem is None:
        return _records_for(index_dir, bases, None, fonte, escopos)
    selecao = _split_selection(bases)
    chave = (origem,
             None if selecao is None else (tuple(sorted(selecao[0])), tuple(sorted(selecao[1]))),
             None if escopos is None else tuple(sorted(set(escopos))))
    guardado = _SELECAO_CACHE.get(chave)
    if guardado is None:
        # Versão anterior do mesmo arquivo não fica presa na memória.
        for antiga in [c for c in _SELECAO_CACHE if c[0][:2] == origem[:2] and c[0] != origem]:
            del _SELECAO_CACHE[antiga]
        guardado = _records_for(index_dir, bases, None, fonte, escopos)
        _SELECAO_CACHE[chave] = guardado
    return list(guardado)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def _default_embed(query: str) -> list[float]:
    return _embed_fn_com_uso([])([query])[0]


def _embed_fn_com_uso(uso: list[TokenUsage], estatisticas: dict[str, int] | None = None,
                       observacoes: list[dict] | None = None) -> EmbedFn:
    """Embedding padrão (servidor da turma) que anota o ``usage`` de cada chamada.

    Usado por ``retrieve`` para preencher ``tokens`` do stage
    ``retrieval_denso``; quem passa ``embed_fn`` próprio fica sem contagem.
    """
    def embed(texts: list[str]) -> list[list[float]]:
        encoded = [embedding_query_text(text) for text in texts]
        endpoint, chave_api, modelo = config.embedding_endpoint()
        chave = {"endpoint": endpoint, "model": modelo, "texts": encoded}
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
        # O cache continua disponível durante a indisponibilidade. Nunca usar
        # outro encoder automaticamente: mesma dimensão não garante compatibilidade.
        endpoint_key = (endpoint, modelo)
        if time.monotonic() < _EMBEDDING_FAILURES.get(endpoint_key, 0):
            raise EmbeddingUnavailable("Embedding em cooldown após falha de disponibilidade")
        # Cliente criado só na chamada: sem candidatos, dense_retrieve nem embeda.
        from openai import OpenAI
        import httpx
        from src.obs import aguardar_modelo

        aguardar_modelo()
        client = OpenAI(base_url=endpoint, api_key=chave_api,
                        timeout=httpx.Timeout(config.QUERY_EMBEDDING_TIMEOUT,
                                              connect=min(3.0, config.QUERY_EMBEDDING_TIMEOUT)),
                        max_retries=0)
        from src.model_observation import observar_chamada
        diagnostics: dict = {}
        if estatisticas is not None:
            estatisticas["calls"] += 1
        try:
            response = observar_chamada(lambda: client.embeddings.create(model=modelo, input=encoded),
                provider=config.EMBEDDING_PROVIDER, model=modelo, sdk_max_retries=0, diagnosticos=diagnostics)
        except Exception as error:
            from llm_ecoa import erro_de_disponibilidade
            if erro_de_disponibilidade(error):
                _EMBEDDING_FAILURES[endpoint_key] = time.monotonic() + config.QUERY_EMBEDDING_COOLDOWN
            raise
        else:
            _EMBEDDING_FAILURES.pop(endpoint_key, None)
        finally:
            if observacoes is not None:
                observacoes.extend(diagnostics.get("chamadas_modelo", []))
        uso.append(token_usage_from_response(response))
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: getattr(item, "index", 0))]
        if len(vectors) != len(texts) or not all(
            vector and all(math.isfinite(v) for v in vector) and any(vector) for vector in vectors
        ):
            raise ValueError(f"{config.EMBEDDING_PROVIDER} retornou embeddings de query inválidos")
        QUERY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with QUERY_CACHE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": chave, "vectors": vectors}, ensure_ascii=False) + "\n")
        return vectors

    return embed


def pre_carregar_indices(
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
) -> int:
    """Lê os índices antes da primeira pergunta; devolve quantos arquivos leu.

    Falha só vira aviso: a primeira pergunta lê pelo caminho normal.
    """
    lidos = 0
    try:
        # Documentos antes do índice: o índice reaproveita os metadados deles.
        for path in (documents_file, QUERY_CACHE_PATH):
            if path.exists():
                _read_jsonl(path)
                lidos += 1
        for vectors_path in sorted(index_dir.glob("*/vectors.jsonl")):
            _registros_do_indice(vectors_path)
            lidos += 1
            faiss_path = vectors_path.parent / "vectors.faiss"
            if faiss_path.exists():
                _ler_indice_faiss(faiss_path)
                lidos += 1
        # Monta o índice da BM25 agora, e não na primeira pergunta.
        _indice_lexico(documents_file, construir=True)
    except Exception as erro:
        import logging

        logging.getLogger(__name__).warning(
            "Pré-carregamento dos índices falhou (%s); a primeira pergunta lê do disco", type(erro).__name__)
    return lidos


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
        else _selecionar(index_dir, bases, escopos, documents_file=documents_file) if not filters
        else _records_for(index_dir, bases, filters, _read_jsonl(documents_file), escopos)
    )
    documents = [(record, str(record.get("text", ""))) for record in candidates]
    query_terms = _tokens(query)
    if not query_terms or not documents:
        return []
    # Com registros recebidos (recorte já filtrado), usa o índice se ele já
    # existe; lendo o próprio documents.jsonl, monta-o na primeira vez.
    indice = _indice_lexico(documents_file, construir=records is None)
    melhores = _bm25_pelo_indice(indice, query_terms, documents, top_k) if indice is not None else None
    if melhores is None:
        melhores = _bm25_por_varredura(query_terms, documents, top_k)
    return [_result(record, score, text) for score, _, record, text in melhores]


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

    Os filtros são aplicados base a base (``_filtrar_por_base``). Somente os
    explicitamente opcionais podem ser ignorados quando não há campo ou
    correspondência; os obrigatórios permanecem. O log registra relaxamentos em
    ``extra.filtros_ignorados``. ``dense_retrieve`` e ``bm25_retrieve``, chamados
    diretamente, continuam aplicando os filtros de forma estrita.

    Cada etapa registra seu stage (``retrieval_denso``, ``retrieval_bm25``,
    ``fusao_rrf``, ``rerank``) em ``logs/pipeline/pipeline-AAAA-MM-DD.jsonl``.
    """
    bases, route_filters = _bases_e_filtros_da_rota(query, route, filters)
    fetch_k = fetch_k if fetch_k is not None else max(5 * top_k, DEFAULT_FETCH_K)
    dense_records, sparse_records, filtros_ignorados = _registros_elegiveis(
        index_dir, documents_file, bases, escopos, route_filters)
    extra = {
        "bases": sorted(str(getattr(b, "value", b)) for b in ([bases] if isinstance(bases, str) else bases or [])),
        "escopos": sorted(escopos) if escopos is not None else None,
        "filtros": dict(route_filters or {}),
        "k": top_k,
        "fetch_k": fetch_k,
    }
    if filtros_ignorados:
        extra["filtros_ignorados"] = filtros_ignorados
    r = _Recuperacao(query=query, index_dir=index_dir, documents_file=documents_file, bases=bases,
                     escopos=escopos, route_filters=route_filters, top_k=top_k, fetch_k=fetch_k,
                     run_id=run_id, query_id=query_id, extra=extra,
                     dense_records=dense_records, sparse_records=sparse_records)

    dense, recuperacao_degradada = _busca_densa(r, embed_fn)
    sparse, candidatos_norma = _busca_bm25(r)
    identificadores_exatos = _identificadores_exatos(r)
    if reranker is not None:
        rerank_k = max(top_k, rerank_k if rerank_k is not None else config.RERANK_CANDIDATES)
    corte = rerank_k if reranker is not None else top_k
    fused = _fundir(r, dense, sparse, candidatos_norma, identificadores_exatos, corte,
                    recuperacao_degradada=recuperacao_degradada, filtros_ignorados=filtros_ignorados)
    if reranker is None or not fused:
        from src.obs import registrar_pulo
        registrar_pulo(run_id=run_id, query_id=query_id, stage=Stage.RERANK, query=query,
                       motivo="desabilitado" if reranker is None else "sem_candidatos")
        return fused
    return _reordenar(r, reranker, fused, candidatos_norma, identificadores_exatos, rerank_k)


@dataclass
class _Recuperacao:
    """O que as etapas de uma busca compartilham: a pergunta, o recorte e os ids do log."""
    query: str
    index_dir: Path
    documents_file: Path
    bases: Any
    escopos: Iterable[str] | None
    route_filters: Mapping[str, Any] | None
    top_k: int
    fetch_k: int
    run_id: str
    query_id: str
    extra: dict[str, Any]
    dense_records: list[dict[str, Any]] | None
    sparse_records: list[dict[str, Any]] | None

    def span(self, stage: Stage, **mais: Any):
        return default_logger.span(run_id=self.run_id, query_id=self.query_id, stage=stage,
                                   input=self.query, extra={**self.extra, **mais})

    def registros_bm25(self) -> list[dict[str, Any]]:
        """Registros que a BM25 pode ver; sem filtro, todos os das bases escolhidas."""
        if self.sparse_records is not None:
            return self.sparse_records
        return _selecionar(self.index_dir, self.bases, self.escopos, documents_file=self.documents_file)


def _bases_e_filtros_da_rota(query: str, route: Any, filters: Mapping[str, Any] | None) -> tuple[Any, Any]:
    """Bases e filtros da rota (dict ou objeto), mais os critérios de "bem completo"."""
    bases = None
    route_filters = filters
    if route is not None:
        if isinstance(route, Mapping):
            bases = route.get("bases_selecionadas", route.get("bases"))
            route_filters = route.get("filters", route.get("filtros", route_filters))
        else:
            bases = getattr(route, "bases_selecionadas", getattr(route, "bases", None))
            route_filters = getattr(route, "filters", getattr(route, "filtros", route_filters))
    from src.retrieval_constraints import solicita_bem_completo, tema_bem_completo
    if solicita_bem_completo(query):
        # Critério de admissibilidade antes de dense/BM25, lookup e rerank.
        # Não pode ser relaxado para devolver peças/serviços como aparelho.
        route_filters = {**(route_filters or {}), "objeto_auxiliar": {
            "operator": "==", "value": False, "optional": False,
        }}
        tema = tema_bem_completo(query)
        if tema:
            route_filters["objeto_termo"] = {"operator": "contains", "value": tema, "optional": False}
    return bases, route_filters


def _registros_elegiveis(index_dir: Path, documents_file: Path, bases: Any, escopos: Iterable[str] | None,
                         route_filters: Mapping[str, Any] | None) -> tuple[Any, Any, dict[str, dict[str, list[str]]]]:
    """Registros que passam nos filtros, para a busca densa e para a BM25.

    Sem filtros, ``None``: cada busca lê as bases por conta própria.
    """
    if not route_filters:
        return None, None, {}
    # Seleção de bases e escopos antes, para decidir o filtro só entre as
    # bases que o roteador escolheu.
    sparse_records, ignorados_bm25 = _filtrar_por_base(
        _selecionar(index_dir, bases, escopos, documents_file=documents_file), route_filters
    )
    # O payload vetorial não repete o texto JSON com o CNPJ do fornecedor.
    # Os IDs dos documentos filtrados delimitam também os candidatos densos.
    dense_filters = {key: value for key, value in route_filters.items() if key != "cnpj"}
    dense_records, ignorados_denso = _filtrar_por_base(
        _selecionar(index_dir, bases, escopos), dense_filters
    )
    if "cnpj" in route_filters:
        eligible = {str(record.get("chunk_id")) for record in sparse_records}
        dense_records = [record for record in dense_records if str(record.get("chunk_id")) in eligible]
    return dense_records, sparse_records, {**ignorados_bm25, **ignorados_denso}


def _busca_densa(r: _Recuperacao, embed_fn: EmbedFn | None) -> tuple[list[RetrievalResult], bool]:
    """Stage ``retrieval_denso``; sem embedding disponível, vazia e degradada (segue a BM25)."""
    uso_embedding: list[TokenUsage] = []
    estatisticas_embedding = {"calls": 0, "cache_hits": 0}
    observacoes_embedding: list[dict] = []
    embed_original = embed_fn or _embed_fn_com_uso(uso_embedding, estatisticas_embedding, observacoes_embedding)

    def embed_da_consulta(texts):
        if embed_fn is not None:
            estatisticas_embedding["calls"] += 1
        return embed_original(texts)

    recuperacao_degradada = False
    with r.span(Stage.RETRIEVAL_DENSO) as span:
        try:
            if not config.DENSE_RETRIEVAL_ENABLED:
                raise EmbeddingUnavailable("Busca densa desabilitada por configuração; usando BM25")
            dense = dense_retrieve(
                r.query,
                index_dir=r.index_dir,
                documents_file=r.documents_file,
                top_k=r.fetch_k,
                bases=r.bases,
                filters=None if r.dense_records is not None else r.route_filters,
                embed_fn=embed_da_consulta,
                records=r.dense_records,
                escopos=r.escopos,
                run_id=r.run_id,
                query_id=r.query_id,
            )
        except Exception as error:
            from llm_ecoa import erro_de_disponibilidade
            if not erro_de_disponibilidade(error):
                raise
            dense = []
            recuperacao_degradada = True
            span.status = "degradado"
            span.extra.update({"degraded": True, "fallback": "bm25", "error_type": type(error).__name__,
                               "status_code": getattr(error, "status_code", None),
                               "embedding_cooldown": isinstance(error, EmbeddingUnavailable),
                               "dense_enabled": config.DENSE_RETRIEVAL_ENABLED})
        span.set_result(_resumo(dense))
        _registrar_evidencias(span, dense)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(r.query, dense)
        span.extra.update({"query_embedding": estatisticas_embedding})
        if observacoes_embedding:
            from src.model_observation import resumir_uso
            span.extra["chamadas_modelo"] = observacoes_embedding
            span.extra["uso_tokens"] = resumir_uso(observacoes_embedding)[1]
            span.modelo = observacoes_embedding[-1]["model"]
        elif estatisticas_embedding["cache_hits"]:
            span.extra["uso_tokens"] = {"status": "cache"}
        elif embed_fn is not None and estatisticas_embedding["calls"]:
            span.extra["uso_tokens"] = {"status": "indisponivel", "fonte": "funcao_injetada_sem_usage"}
        if uso_embedding:
            span.modelo = span.modelo or config.EMBEDDING_MODEL
            span.tokens = TokenUsage(
                prompt=sum(u.prompt for u in uso_embedding),
                completion=sum(u.completion for u in uso_embedding),
                total=sum(u.total for u in uso_embedding),
            )
    return dense, recuperacao_degradada


def _busca_bm25(r: _Recuperacao) -> tuple[list[RetrievalResult], list[RetrievalResult]]:
    """Stage ``retrieval_bm25``, com a norma citada na pergunta priorizada.

    Devolve o ranking e os trechos da norma pedida, que a fusão e o rerank
    também priorizam.
    """
    with r.span(Stage.RETRIEVAL_BM25) as span:
        sparse = bm25_retrieve(
            r.query,
            index_dir=r.index_dir,
            documents_file=r.documents_file,
            top_k=r.fetch_k,
            bases=r.bases,
            filters=None if r.sparse_records is not None else r.route_filters,
            records=r.sparse_records,
            escopos=r.escopos,
        )
        candidatos_norma: list[RetrievalResult] = []
        pedidas = _normas_mencionadas(r.query)
        if pedidas:
            elegiveis = r.sparse_records if r.sparse_records is not None else (
                _selecionar(r.index_dir, r.bases, r.escopos, documents_file=r.documents_file)
                if not r.route_filters else
                _records_for(r.index_dir, r.bases, r.route_filters, _read_jsonl(r.documents_file), r.escopos))
            primarios = [registro for registro in elegiveis
                         if _record_natureza(registro, _metadata_of(registro)) == Natureza.NORMATIVA.value
                         and _norma_do_documento(_metadata_of(registro)) & pedidas]
            if primarios:
                candidatos_norma = _trechos_da_norma_pedida(r, primarios)
                sparse = _priorizar_norma_solicitada(r.query, sparse, candidatos_norma, r.fetch_k)
                span.extra["normas_primarias_priorizadas"] = [c.chunk_id for c in candidatos_norma[:r.top_k]]
        span.set_result(_resumo(sparse))
        _registrar_evidencias(span, sparse)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(r.query, sparse)
    return sparse, candidatos_norma


def _trechos_da_norma_pedida(r: _Recuperacao, primarios: list[dict[str, Any]]) -> list[RetrievalResult]:
    """Trechos da norma citada, conteúdo articulado antes de capas, sem texto repetido."""
    candidatos = bm25_retrieve(
        _consulta_topica_norma(r.query), records=primarios, top_k=r.fetch_k,
        index_dir=r.index_dir, documents_file=r.documents_file,
    )
    # Se há conteúdo articulado, capas/cabeçalhos ficam depois.
    candidatos = _ordenar_candidatos_norma(r.query, candidatos)
    vistos_textos: set[str] = set()
    distintos = []
    for item in candidatos:
        assinatura = " ".join(item.content.split()).casefold()
        if assinatura not in vistos_textos:
            distintos.append(item)
            vistos_textos.add(assinatura)
    return distintos


def _identificadores_exatos(r: _Recuperacao) -> list[RetrievalResult]:
    """Registros com o número do PNCP ou do contrato citado na pergunta."""
    if not (_identificadores_pncp(r.query) or _referencias_contrato(r.query)):
        return []
    return _resultados_identificador_exato(r.query, r.registros_bm25())


def _fundir(r: _Recuperacao, dense: list[RetrievalResult], sparse: list[RetrievalResult],
            candidatos_norma: list[RetrievalResult], identificadores_exatos: list[RetrievalResult],
            corte: int, *, recuperacao_degradada: bool,
            filtros_ignorados: dict[str, dict[str, list[str]]]) -> list[RetrievalResult]:
    """Stage ``fusao_rrf``: RRF, variedade por compra, norma pedida e identificadores exatos à frente."""
    with r.span(Stage.FUSAO_RRF, rrf_k=DEFAULT_RRF_K) as span:
        fused = reciprocal_rank_fusion([dense, sparse])
        fused, rebaixados = _variar_por_compra(r.query, fused)
        fused = fused[:corte]
        span.extra["variedade"] = {"max_por_compra": config.RETRIEVAL_MAX_PER_PURCHASE,
                                   "rebaixados": rebaixados}
        fused = _priorizar_norma_solicitada(r.query, fused, candidatos_norma, corte)
        fused = _promover_identificadores_exatos(fused, identificadores_exatos, corte)
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
        _registrar_evidencias(span, fused)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(r.query, fused)
        span.extra["identificadores_exactos_adicionados"] = [
            item.chunk_id for item in identificadores_exatos
        ]
    return fused


def _reordenar(r: _Recuperacao, reranker: RerankFn, fused: list[RetrievalResult],
               candidatos_norma: list[RetrievalResult], identificadores_exatos: list[RetrievalResult],
               rerank_k: int) -> list[RetrievalResult]:
    """Stage ``rerank``; se o reranker falhar, o span registra o erro e valem os do RRF."""
    try:
        with r.span(Stage.RERANK, rerank_k=rerank_k) as span:
            span.modelo = getattr(reranker, "modelo", None)
            span.extra["candidatos"] = len(fused)
            candidates = {item.chunk_id: item for item in fused}
            span.extra["fallback_on_error"] = "fusao_rrf"
            ranked = list(reranker(r.query, fused))
            if getattr(reranker, "backend_usado", None):
                span.extra["rerank_backend"] = reranker.backend_usado
                if "ecoa falhou" in reranker.backend_usado:
                    span.status = "degradado"
                    span.extra["fallback"] = "reranker_local"
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
            antes_do_top_k = reranked
            reranked = _priorizar_norma_solicitada(r.query, reranked, candidatos_norma, r.top_k)
            reranked = _promover_identificadores_exatos(reranked, identificadores_exatos, r.top_k)
            span.set_result(_resumo(reranked))
            _registrar_evidencias(span, reranked, antes_do_top_k=antes_do_top_k)
            span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(r.query, reranked)
        return reranked
    except Exception:  # o span registra o erro; segue com os candidatos originais
        return fused[:r.top_k]


__all__ = [
    "RetrievalResult",
    "bm25_retrieve",
    "dense_retrieve",
    "reciprocal_rank_fusion",
    "retrieve",
]
