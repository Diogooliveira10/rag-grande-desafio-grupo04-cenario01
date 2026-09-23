"""Orquestra o pipeline completo: roteamento -> recuperação híbrida -> geração.

Une ``src/router.py``, ``src/retrieve.py`` e ``src/generate.py`` numa única
função compatível com o contrato ``PipelineFn`` de ``eval/run_eval.py``
(``Callable[[str], dict[str, Any]]``), para uso direto via::

    py .\\eval\\run_eval.py run --pipeline src.pipeline:responder
"""

from __future__ import annotations

from typing import Any, Optional

from src.obs import new_query_id, new_run_id
from src.generate import ClienteLLM, gerar_resposta
from src.retrieve import DEFAULT_TOP_K, RerankFn, RetrievalResult, retrieve
from src.router import inferir_motivo_recusa, rotear_pergunta
from src.schema import Chunk, ChunkMetadata


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
) -> dict[str, Any]:
    """Roda roteamento + recuperação híbrida + geração para uma pergunta.

    Quando o roteador não seleciona nenhuma base (recusa, fora de escopo ou
    "não identificado"), a recuperação é pulada; o motivo da recusa é inferido
    da justificativa do roteador (``router.inferir_motivo_recusa``) e repassado
    para ``gerar_resposta``, que senão cairia sempre em SEM_EVIDENCIA.

    ``run_id`` e ``query_id`` são repassados a todas as etapas, amarrando no
    log (``logs/pipeline-AAAA-MM-DD.jsonl``) os stages de uma mesma pergunta.
    """
    run_id = run_id or new_run_id()
    query_id = new_query_id()
    decisao = rotear_pergunta(query, run_id=run_id, query_id=query_id)

    chunks: list[Chunk] = []
    refusal_reason_hint = None
    if decisao.bases_selecionadas:
        resultados = retrieve(
            query, decisao, top_k=top_k, reranker=reranker, run_id=run_id, query_id=query_id
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


__all__ = ["responder"]
