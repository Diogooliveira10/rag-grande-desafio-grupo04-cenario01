"""Orquestra o pipeline completo: roteamento -> recuperação híbrida -> geração.

Une ``src/router.py``, ``src/retrieve.py`` e ``src/generate.py`` numa única
função compatível com o contrato ``PipelineFn`` de ``eval/run_eval.py``
(``Callable[[str], dict[str, Any]]``), para uso direto via::

    py .\\eval\\run_eval.py run --pipeline src.pipeline:responder
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from src.obs import new_query_id, new_run_id
from src.generate import ClienteLLM, gerar_resposta
from src.query_analysis import analisar_query, filtros_para_retrieve
from src.retrieve import DEFAULT_TOP_K, RerankFn, RetrievalResult, retrieve
from src.router import inferir_motivo_recusa, rotear_pergunta
from src.schema import Chunk, ChunkMetadata

# Só as bases de escopo "oficial" no manifesto respondem perguntas; fixtures de
# teste e amostras experimentais ficam de fora da busca.
ESCOPOS_CONSULTADOS = ("oficial",)


def _chunk_from_retrieval(result: RetrievalResult) -> Chunk:
    """Converte o resultado de retrieve.py no ``Chunk`` que generate.py espera."""
    return Chunk(content=result.content, metadata=ChunkMetadata(**result.metadata))


def responder(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    reranker: Optional[RerankFn] = None,
    cliente: Optional[ClienteLLM] = None,
    run_id: Optional[str] = None,
    escopos: Optional[Sequence[str]] = ESCOPOS_CONSULTADOS,
) -> dict[str, Any]:
    """Roda query_analysis + roteamento + recuperação híbrida + geração para uma pergunta.

    ``escopos=None`` consulta também as bases experimentais (ex.:
    ``exemplos_teste``), útil para desenvolver com um índice parcial.

    Quando o roteador não seleciona nenhuma base (recusa, fora de escopo ou
    "não identificado"), a recuperação é pulada; o motivo da recusa é inferido
    da justificativa do roteador (``router.inferir_motivo_recusa``) e repassado
    para ``gerar_resposta``, que senão cairia sempre em SEM_EVIDENCIA.

    ``query_analysis`` roda antes do roteamento e não influencia a decisão de
    quais bases consultar — só extrai filtros (ano, valor, órgão) que o
    ``retrieve`` aplica sobre os metadados dos chunks candidatos.

    ``run_id`` e ``query_id`` são repassados a todas as etapas, amarrando no
    log (``logs/pipeline-AAAA-MM-DD.jsonl``) os stages de uma mesma pergunta.
    """
    run_id = run_id or new_run_id()
    query_id = new_query_id()

    analise = analisar_query(query, run_id=run_id, query_id=query_id)
    decisao = rotear_pergunta(query, run_id=run_id, query_id=query_id)

    chunks: list[Chunk] = []
    refusal_reason_hint = None
    if decisao.bases_selecionadas:
        resultados = retrieve(
            query,
            decisao,
            top_k=top_k,
            filters=filtros_para_retrieve(analise.filtros),
            reranker=reranker,
            escopos=escopos,
            run_id=run_id,
            query_id=query_id,
        )
        chunks = [_chunk_from_retrieval(item) for item in resultados]
    else:
        refusal_reason_hint = inferir_motivo_recusa(decisao.justificativa)

    resposta = gerar_resposta(
        query,
        chunks,
        decisao.bases_selecionadas,
        cliente=cliente,
        run_id=run_id,
        query_id=query_id,
        refusal_reason_hint=refusal_reason_hint,
    )
    return resposta.model_dump(mode="json")


def responder_incluindo_experimentais(query: str) -> dict[str, Any]:
    """``responder`` sem o recorte de escopo, no contrato ``PipelineFn`` do eval::

        py .\\eval\\run_eval.py run --pipeline src.pipeline:responder_incluindo_experimentais
    """
    return responder(query, escopos=None)


__all__ = ["responder", "responder_incluindo_experimentais"]