"""API HTTP do Adaptive RAG.

Esta camada limita-se a validar o contrato HTTP e delegar a consulta ao
pipeline. As regras de roteamento, recuperacao e guardrails permanecem em
``src.pipeline``.

Feedback e historico (item 8 das pendencias do time, exigido pela
especificacao): toda consulta em ``POST /api/v1/query`` ganha um ``query_id``
e e' salva em ``src/history_store.py``; ``POST /api/v1/feedback`` associa uma
avaliacao a essa consulta e ``GET /api/v1/history`` devolve o historico
salvo. O estagio ``feedback`` ja existia reservado em ``src/obs.py``
(Stage.FEEDBACK) mas nao estava integrado a nenhum fluxo ate agora.
"""

from __future__ import annotations

import logging
import os
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from src import pipeline
from src.history_store import ConsultaNaoEncontrada, default_store as historico
from src.obs import Stage, new_query_id, new_run_id
from src.obs import registrar as obs_registrar
from src.schema import RAGResponse

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = (
    "https://etuarda.github.io",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ALLOW_ORIGINS", "")
    extras = [origin.strip().rstrip("/") for origin in configured.split(",")]
    return list(dict.fromkeys((*DEFAULT_CORS_ORIGINS, *(o for o in extras if o))))


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def query_nao_vazia(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query nao pode conter apenas espacos")
        return normalized


class QueryResponse(RAGResponse):
    """``RAGResponse`` + o id que identifica esta consulta no historico.

    O front usa ``query_id`` para depois mandar o feedback dessa resposta
    especifica em ``POST /api/v1/feedback``.
    """

    query_id: str


class FeedbackRequest(BaseModel):
    query_id: str = Field(min_length=1)
    avaliacao: Literal["positivo", "negativo"]
    comentario: Optional[str] = Field(default=None, max_length=2000)


class FeedbackEntry(BaseModel):
    avaliacao: str
    comentario: Optional[str] = None
    ts: str


class HistoricoItem(BaseModel):
    query_id: str
    run_id: str
    ts: str
    query: str
    resposta: RAGResponse
    feedback: list[FeedbackEntry] = Field(default_factory=list)


app = FastAPI(title="Adaptive RAG API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/query", response_model=QueryResponse)
def query_rag(request: QueryRequest) -> QueryResponse:
    run_id = new_run_id()
    query_id = new_query_id()
    try:
        result = pipeline.responder(
            request.query, top_k=request.top_k, run_id=run_id, query_id=query_id
        )
    except Exception as exc:
        logger.exception("Falha ao executar o pipeline RAG")
        raise HTTPException(
            status_code=500,
            detail="Nao foi possivel processar a consulta.",
        ) from exc

    resposta = QueryResponse.model_validate({**result, "query_id": query_id})

    try:
        historico.registrar_consulta(
            query_id=query_id,
            run_id=run_id,
            query=request.query,
            top_k=request.top_k,
            resposta=result,
        )
    except Exception:
        # Historico e' um extra sobre a resposta ja calculada: uma falha ao
        # salvar nao pode derrubar uma consulta que ja deu certo.
        logger.exception("Falha ao salvar historico da consulta %s", query_id)

    return resposta


@app.post("/api/v1/feedback")
def enviar_feedback(request: FeedbackRequest) -> dict[str, str]:
    consulta = historico.obter_consulta(request.query_id)
    if consulta is None:
        raise HTTPException(status_code=404, detail="query_id desconhecido.")

    try:
        historico.registrar_feedback(
            query_id=request.query_id,
            avaliacao=request.avaliacao,
            comentario=request.comentario,
        )
    except ConsultaNaoEncontrada as exc:
        raise HTTPException(status_code=404, detail="query_id desconhecido.") from exc

    # Amarra o feedback ao mesmo run_id da consulta original, para aparecer
    # junto dos outros stages dela no log JSONL.
    obs_registrar(
        run_id=consulta["run_id"],
        query_id=request.query_id,
        stage=Stage.FEEDBACK,
        status=request.avaliacao,
        latencia_ms=0,
        input={"comentario": request.comentario},
        output=request.avaliacao,
    )
    return {"status": "recebido"}


@app.get("/api/v1/history", response_model=list[HistoricoItem])
def obter_historico(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    return historico.obter_historico(limit=limit)


__all__ = ["app"]
