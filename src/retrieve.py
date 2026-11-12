"""Busca híbrida: densa + BM25 + RRF, com rerank opcional (fachada de src/retrieval/).

As partes moram em src/retrieval/ e todos os nomes continuam importáveis
daqui (``from src.retrieve import ...``). Aqui ficam a orquestração
(``retrieve`` e suas etapas) e ``pre_carregar_indices``.

Ao trocar um nome num teste, troque-o no módulo onde mora a função que o usa:
``dense_retrieve``/``bm25_retrieve`` aqui (a orquestração os chama daqui);
``_records_for``/``_selecionar`` em ``src.retrieval.selection``;
``QUERY_CACHE_PATH`` em ``src.retrieval.query_embedding``; ``registrar`` do
aviso de vetor descartado em ``src.retrieval.dense``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import config
from src.settings import Configuracao
from src.obs import Stage, TokenUsage, default_logger
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

from src.retrieval import query_embedding, selection as selecao
from src.retrieval.query_embedding import (  # noqa: F401
    EmbedFn,
    QUERY_INSTRUCTION,
    _EMBEDDING_FAILURES,
    _default_embed,
    _embed_fn_com_uso,
    embedding_query_text,
)
from src.retrieval.selection import (  # noqa: F401
    DEFAULT_TOP_K,
    _SELECAO_CACHE,
    _assinatura_da_origem,
    _caminhos_ou_padrao,
    _records_for,
    _selecionar,
)
from src.retrieval.dense import (  # noqa: F401
    _EXEMPLOS_DE_DESCARTE,
    _FAISS_CACHE,
    _avisar_vetores_descartados,
    _base_do,
    _candidatos_com_vetor,
    _cosine,
    _faiss_scores,
    _indices_das_bases,
    _ler_indice_faiss,
    _por_base,
    dense_retrieve,
)
from src.retrieval.sparse import bm25_retrieve  # noqa: F401


DEFAULT_FETCH_K = 50


RerankFn = Callable[[str, list["RetrievalResult"]], Sequence[Any]]


def pre_carregar_indices(
    index_dir: Path | None = None,
    documents_file: Path | None = None,
) -> int:
    """Lê os índices antes da primeira pergunta; devolve quantos arquivos leu.

    Falha só vira aviso: a primeira pergunta lê pelo caminho normal.
    """
    index_dir, documents_file = _caminhos_ou_padrao(index_dir, documents_file)
    lidos = 0
    try:
        # Documentos antes do índice: o índice reaproveita os metadados deles.
        for path in (documents_file, query_embedding.QUERY_CACHE_PATH):
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


def retrieve(
    query: str,
    route: Any = None,
    *,
    index_dir: Path | None = None,
    documents_file: Path | None = None,
    top_k: int = DEFAULT_TOP_K,
    filters: Mapping[str, Any] | None = None,
    embed_fn: EmbedFn | None = None,
    reranker: RerankFn | None = None,
    escopos: Iterable[str] | None = None,
    fetch_k: int | None = None,
    rerank_k: int | None = None,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
    configuracao: Configuracao | None = None,
) -> list[RetrievalResult]:
    """Executa dense + BM25 + RRF e aplica reranker somente se fornecido.

    Com ``reranker``, o RRF entrega ``rerank_k`` candidatos (padrão
    ``max(top_k, rerank_candidates)`` da configuração) e o reranker escolhe os ``top_k``
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

    ``configuracao`` (``src/settings.py``) dá a busca, os embeddings e os
    caminhos; sem ela, vale a configuração atual do ``config``.
    """
    cfg = configuracao or config.atual()
    index_dir = index_dir if index_dir is not None else cfg.caminhos.index_dir
    documents_file = documents_file if documents_file is not None else cfg.caminhos.normalized_data_dir / "documents.jsonl"
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
                     dense_records=dense_records, sparse_records=sparse_records, configuracao=cfg)

    dense, recuperacao_degradada = _busca_densa(r, embed_fn)
    sparse, candidatos_norma = _busca_bm25(r)
    identificadores_exatos = _identificadores_exatos(r)
    if reranker is not None:
        rerank_k = max(top_k, rerank_k if rerank_k is not None else cfg.busca.rerank_candidates)
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
    configuracao: Configuracao

    def span(self, stage: Stage, **mais: Any):
        return default_logger.span(run_id=self.run_id, query_id=self.query_id, stage=stage,
                                   input=self.query, extra={**self.extra, **mais})

    def registros_bm25(self) -> list[dict[str, Any]]:
        """Registros que a BM25 pode ver; sem filtro, todos os das bases escolhidas."""
        if self.sparse_records is not None:
            return self.sparse_records
        return selecao._selecionar(self.index_dir, self.bases, self.escopos, documents_file=self.documents_file)


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
        selecao._selecionar(index_dir, bases, escopos, documents_file=documents_file), route_filters
    )
    # O payload vetorial não repete o texto JSON com o CNPJ do fornecedor.
    # Os IDs dos documentos filtrados delimitam também os candidatos densos.
    dense_filters = {key: value for key, value in route_filters.items() if key != "cnpj"}
    dense_records, ignorados_denso = _filtrar_por_base(
        selecao._selecionar(index_dir, bases, escopos), dense_filters
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
    embeddings = r.configuracao.embeddings
    embed_original = embed_fn or _embed_fn_com_uso(uso_embedding, estatisticas_embedding, observacoes_embedding,
                                                    embeddings)

    def embed_da_consulta(texts):
        if embed_fn is not None:
            estatisticas_embedding["calls"] += 1
        return embed_original(texts)

    recuperacao_degradada = False
    with r.span(Stage.RETRIEVAL_DENSO) as span:
        try:
            if not embeddings.dense_retrieval_enabled:
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
                               "dense_enabled": embeddings.dense_retrieval_enabled})
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
            span.modelo = span.modelo or embeddings.model
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
                selecao._selecionar(r.index_dir, r.bases, r.escopos, documents_file=r.documents_file)
                if not r.route_filters else
                selecao._records_for(r.index_dir, r.bases, r.route_filters, _read_jsonl(r.documents_file), r.escopos))
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
        maximo_por_compra = r.configuracao.busca.max_per_purchase
        fused, rebaixados = _variar_por_compra(r.query, fused, maximo_por_compra)
        fused = fused[:corte]
        span.extra["variedade"] = {"max_por_compra": maximo_por_compra,
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
