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
from src.rerank import reranker_padrao
from src.retrieve import DEFAULT_TOP_K, RerankFn, RetrievalResult, retrieve
from src.router import inferir_motivo_recusa, rotear_pergunta
from src.schema import Chunk, ChunkMetadata
from src.structured_query import consultar_estruturado

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
    query_id: Optional[str] = None,
    escopos: Optional[Sequence[str]] = ESCOPOS_CONSULTADOS,
) -> dict[str, Any]:
    """Roda query_analysis + roteamento + recuperação híbrida + geração para uma pergunta.

    ``escopos=None`` consulta também as bases experimentais (ex.:
    ``exemplos_teste``), útil para desenvolver com um índice parcial.

    Quando o roteador não seleciona nenhuma base (recusa, fora de escopo ou
    "não identificado"), a recuperação é pulada; o motivo da recusa é inferido
    da justificativa do roteador (``router.inferir_motivo_recusa``) e repassado
    para ``gerar_resposta``, que senão cairia sempre em SEM_EVIDENCIA.

    A recuperação funde busca densa e BM25 (RRF) e reordena os primeiros
    candidatos com o cross-encoder de ``src/rerank.py``, quando ligado.

    ``query_analysis`` roda antes do roteamento e não influencia a decisão de
    quais bases consultar — só extrai filtros (ano, valor, órgão) que o
    ``retrieve`` aplica sobre os metadados dos chunks candidatos. Chunk sem o
    campo não é descartado, e se os filtros zerarem o resultado a busca é
    refeita sem eles.

    Perguntas de conta (ranking, estatística, comparação, evolução,
    agrupamento) sobre as bases estruturada/agregada também passam pela etapa
    ``consulta_sql`` (``src/structured_query.py``), cujo resultado entra como
    evidência antes dos chunks da busca.

    ``run_id`` e ``query_id`` são repassados a todas as etapas, amarrando no
    log (``logs/pipeline-AAAA-MM-DD.jsonl``) os stages de uma mesma pergunta.
    Passe ``query_id`` quando o chamador (ex.: ``app/api.py``, para o
    histórico em ``src/history_store.py``) precisa saber o id *antes* da
    resposta pronta; por padrão um novo id é gerado aqui.
    """
    run_id = run_id or new_run_id()
    query_id = query_id or new_query_id()

    analise = analisar_query(query, run_id=run_id, query_id=query_id)
    decisao = (rotear_pergunta(query, run_id=run_id, query_id=query_id, analise=analise)
               if analise.intencoes else rotear_pergunta(query, run_id=run_id, query_id=query_id))

    chunks: list[Chunk] = []
    filtros_relaxados: dict[str, dict[str, list[str]]] = {}
    refusal_reason_hint = None
    if decisao.bases_selecionadas and analise.intencoes:
        grupos: list[list[Chunk]] = []
        for numero, intent in enumerate(analise.intencoes):
            route = {"bases": [b for b in intent.bases if b in decisao.bases_selecionadas]}
            resultados = retrieve(intent.pergunta, route, top_k=top_k,
                filters=filtros_para_retrieve(intent.filtros), reranker=reranker,
                escopos=escopos, run_id=run_id, query_id=query_id)
            grupo = consultar_estruturado(intent.pergunta, intent.filtros, route["bases"],
                intencao=intent, run_id=run_id, query_id=query_id)
            calculo_disponivel = bool(grupo)
            grupo += [_chunk_from_retrieval(item) for item in resultados]
            for chunk in grupo:
                chunk.metadata.extras = {**chunk.metadata.extras, "intencao": numero,
                    "subpergunta": intent.pergunta, "operacao": intent.operacao,
                    "tipo_valor_solicitado": intent.filtros.tipo_valor,
                    "calculo_disponivel": calculo_disponivel if intent.operacao != "buscar" else None}
                relaxed = chunk.metadata.extras.get("filtros_relaxados")
                if relaxed:
                    filtros_relaxados[chunk.metadata.base_id] = relaxed
            grupos.append(grupo)
        # Intercala intenções e bases: nenhum pacote SQL elimina a outra intenção.
        for grupo in grupos:
            grupo.sort(key=lambda c: not c.metadata.chunk_id.startswith("sql:"))
        vistos = set()
        while any(grupos):
            for grupo in grupos:
                if not grupo:
                    continue
                chunk = grupo.pop(0)
                key = chunk.metadata.chunk_content_sha256 or chunk.metadata.chunk_id
                if key not in vistos:
                    chunks.append(chunk)
                    vistos.add(key)
    elif decisao.bases_selecionadas:
        filtros = filtros_para_retrieve(analise.filtros)
        # Sem reranker explícito, usa o cross-encoder padrão (src/rerank.py;
        # desligável com RERANK_ENABLED=false). Ablação em docs/ABLACAO_RERANK.md.
        reranker = reranker if reranker is not None else reranker_padrao()
        resultados = retrieve(
            query,
            decisao,
            top_k=top_k,
            filters=filtros,
            reranker=reranker,
            escopos=escopos,
            run_id=run_id,
            query_id=query_id,
        )
        if filtros and not resultados:
            # Os filtros são heurísticos; se zeraram tudo, a busca sem eles
            # ainda dá evidência ao generate (que decide se basta).
            resultados = retrieve(
                query,
                decisao,
                top_k=top_k,
                filters=None,
                reranker=reranker,
                escopos=escopos,
                run_id=run_id,
                query_id=query_id,
            )
        chunks = [_chunk_from_retrieval(item) for item in resultados]
        filtros_relaxados = {
            item.metadata["base_id"]: item.metadata.get("extras", {}).get("filtros_relaxados", {})
            for item in resultados if item.metadata.get("extras", {}).get("filtros_relaxados")
        }
        # Ranking/estatística/comparação: o resultado SQL vai à frente, para
        # caber no orçamento do prompt antes dos trechos da busca.
        sql_chunks = consultar_estruturado(
            query, analise.filtros, decisao.bases_selecionadas, run_id=run_id, query_id=query_id
        )
        from src.structured_query import requer_calculo
        for chunk in sql_chunks + chunks:
            chunk.metadata.extras = {**chunk.metadata.extras,
                "calculo_disponivel": bool(sql_chunks) if requer_calculo(query) else None,
                "tipo_valor_solicitado": analise.filtros.tipo_valor}
        chunks = sql_chunks + chunks
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
    if filtros_relaxados:
        labels = {"data_vigencia": "ano/período de vigência", "orgao": "órgão", "valor": "valor", "esfera": "esfera",
                  "data_assinatura": "data de assinatura", "data_resultado": "data do resultado",
                  "data_publicacao": "data de publicação", "ano_pca": "ano do PCA"}
        bases = {"contratos_estruturado": "contratos", "atas_conversacional": "atas",
                 "editais_normativo": "normas", "pca_agregado": "indicadores"}
        notes = []
        for base, reasons in sorted(filtros_relaxados.items()):
            for reason, keys in sorted(reasons.items()):
                fields = ", ".join(labels.get(key, key) for key in keys)
                why = "metadados ausentes" if reason == "sem_campo" else "nenhum resultado com o recorte solicitado"
                notes.append(f"{bases.get(base, base)}: {fields} ({why})")
        resposta.answer += "\n\nBusca ampliada; filtros relaxados em " + "; ".join(notes) + "."
    return resposta.model_dump(mode="json")


def responder_incluindo_experimentais(query: str) -> dict[str, Any]:
    """``responder`` sem o recorte de escopo, no contrato ``PipelineFn`` do eval::

        py .\\eval\\run_eval.py run --pipeline src.pipeline:responder_incluindo_experimentais
    """
    return responder(query, escopos=None)


__all__ = ["responder", "responder_incluindo_experimentais"]
