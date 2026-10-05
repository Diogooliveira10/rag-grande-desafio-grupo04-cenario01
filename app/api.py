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
from pathlib import PurePosixPath
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from src import pipeline
from src.history_store import ConsultaNaoEncontrada, default_store as historico
from src.obs import Stage, new_query_id, new_run_id
from src.obs import registrar as obs_registrar
from src.schema import RAGResponse
from app import catalog

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


class HealthResponse(BaseModel):
    status: Literal["ok"]


class FeedbackResponse(BaseModel):
    status: Literal["recebido"]


def _public_rag_payload(payload: dict) -> dict:
    """Remove diretórios internos das referências antes da fronteira HTTP."""
    public = dict(payload)
    public["sources_used"] = [
        {
            **source,
            "source_file": PurePosixPath(
                str(source.get("source_file") or "").replace("\\", "/")
            ).name,
        }
        for source in payload.get("sources_used", [])
    ]
    return public


app = FastAPI(title="Adaptive RAG API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


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

    public_result = _public_rag_payload(result)
    resposta = QueryResponse.model_validate({**public_result, "query_id": query_id})

    try:
        historico.registrar_consulta(
            query_id=query_id,
            run_id=run_id,
            query=request.query,
            top_k=request.top_k,
            resposta=public_result,
        )
    except Exception:
        # Historico e' um extra sobre a resposta ja calculada: uma falha ao
        # salvar nao pode derrubar uma consulta que ja deu certo.
        logger.exception("Falha ao salvar historico da consulta %s", query_id)

    return resposta


@app.post("/api/v1/feedback", response_model=FeedbackResponse)
def enviar_feedback(request: FeedbackRequest) -> FeedbackResponse:
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
    return FeedbackResponse(status="recebido")


@app.get("/api/v1/history", response_model=list[HistoricoItem])
def obter_historico(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    items = historico.obter_historico(limit=limit)
    for item in items:
        item["resposta"] = _public_rag_payload(item["resposta"])
    return items


def _catalog_error(exc: Exception) -> HTTPException:
    logger.exception("Falha ao ler dados reais para a API de exploração")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=503, detail="A base de dados solicitada não está disponível.")
    return HTTPException(status_code=500, detail="Não foi possível consultar os dados do projeto.")


@app.get("/api/v1/explore/contratacoes", response_model=list[catalog.ContratacaoRecord])
def explorar_contratacoes(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    busca: Optional[str] = Query(default=None, max_length=200),
    orgao: Optional[str] = Query(default=None, max_length=200),
) -> list[catalog.ContratacaoRecord]:
    try:
        return catalog.listar_contratacoes(limit=limit, offset=offset, busca=busca, orgao=orgao)
    except Exception as exc:
        raise _catalog_error(exc) from exc


@app.get("/api/v1/explore/documentos", response_model=list[catalog.DocumentoRecord])
def explorar_documentos(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    busca: Optional[str] = Query(default=None, max_length=200),
    tipo: Optional[str] = Query(default=None, max_length=80),
) -> list[catalog.DocumentoRecord]:
    try:
        return catalog.listar_documentos(limit=limit, offset=offset, busca=busca, tipo=tipo)
    except Exception as exc:
        raise _catalog_error(exc) from exc


@app.get("/api/v1/explore/orgaos", response_model=list[catalog.OrgaoRecord])
def explorar_orgaos(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    busca: Optional[str] = Query(default=None, max_length=200),
) -> list[catalog.OrgaoRecord]:
    try:
        return catalog.listar_orgaos(limit=limit, offset=offset, busca=busca)
    except Exception as exc:
        raise _catalog_error(exc) from exc


@app.get("/api/v1/explore/fontes", response_model=list[catalog.OfficialSource])
def explorar_fontes() -> list[catalog.OfficialSource]:
    try:
        return catalog.listar_fontes()
    except Exception as exc:
        raise _catalog_error(exc) from exc


@app.get("/api/v1/project/summary", response_model=catalog.ProjectSummary)
def resumo_projeto() -> catalog.ProjectSummary:
    try:
        return catalog.project_summary()
    except Exception as exc:
        raise _catalog_error(exc) from exc


__all__ = ["app"]
