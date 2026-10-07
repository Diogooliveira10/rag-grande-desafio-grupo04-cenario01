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

Protecao: historico e trace mostram perguntas de todos os usuarios, entao
exigem o header ``X-API-Key`` igual a ``RAG_API_KEY`` do ``.env``
(``exigir_chave``). Sem a variavel configurada, esses endpoints ficam
fechados. ``/query`` e ``/feedback`` continuam abertos: sao o uso normal do chat.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import PurePosixPath
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

import config
from src import pipeline
from app import catalog
from app.catalog_models import ContratacaoRecord, DocumentoRecord, OfficialSource, OrgaoRecord, ProjectSummary
from src.history_store import ConsultaNaoEncontrada, default_store as historico
from src.obs import Stage, new_query_id, new_run_id, new_session_id
from src.obs import registrar as obs_registrar
from src.schema import RAGResponse
from src.trace import montar_trace, montar_trace_sessao

logger = logging.getLogger(__name__)

# Ids que chegam do front-end vão para o log e para a URL do trace: só letras,
# dígitos, "_" e "-".
_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def exigir_chave(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Libera o endpoint só para quem envia ``X-API-Key`` igual a ``RAG_API_KEY``."""
    chave = config.RAG_API_KEY
    if not chave:
        raise HTTPException(
            status_code=403,
            detail="Endpoint desabilitado: defina RAG_API_KEY no .env do servidor.",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), chave.encode()):
        raise HTTPException(status_code=401, detail="X-API-Key ausente ou invalida.")


def _public_rag_payload(payload: dict) -> dict:
    """Remove caminhos locais das fontes antes de persistir ou expor via HTTP."""
    public = dict(payload)
    public["sources_used"] = [
        {**source, "source_file": PurePosixPath(
            str(source.get("source_file") or "").replace("\\", "/")
        ).name}
        for source in payload.get("sources_used", [])
    ]
    return public

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
    # Conversa a que a pergunta pertence. O front-end reenvia o session_id da
    # primeira resposta; sem ele, a API abre uma sessão nova.
    session_id: Optional[str] = Field(default=None, pattern=_ID_PATTERN)

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
    especifica em ``POST /api/v1/feedback``, e ``session_id`` para mandar as
    próximas perguntas na mesma conversa.
    """

    query_id: str
    session_id: str


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
    session_id: Optional[str] = None
    ts: str
    query: str
    resposta: RAGResponse
    feedback: list[FeedbackEntry] = Field(default_factory=list)


class TraceEtapa(BaseModel):
    ordem: int
    stage: str
    # "sql" ou "vetorial" nas etapas que consultam uma base; None nas demais.
    base: Optional[Literal["sql", "vetorial"]] = None
    status: str
    ts: str
    latencia_ms: int
    input: Any = None
    output: Any = None
    tokens: Optional[dict[str, int]] = None
    modelo: Optional[str] = None
    detalhes: dict[str, Any] = Field(default_factory=dict)


class TraceResponse(BaseModel):
    query_id: str
    session_id: Optional[str] = None
    run_ids: list[str]
    pergunta: Optional[str] = None
    inicio: str
    fim: str
    latencia_total_ms: int
    # consultou_sql, consultou_vetorial, evidências de cada base e fontes citadas.
    resumo: dict[str, Any]
    etapas: list[TraceEtapa]


class SessaoPergunta(BaseModel):
    ordem: int
    query_id: str
    pergunta: Optional[str] = None
    inicio: str
    fim: str
    latencia_total_ms: int
    resumo: dict[str, Any]
    # Nomes das etapas, em ordem; o detalhe de cada uma está em /trace/{query_id}.
    etapas: list[str]


class SessaoTraceResponse(BaseModel):
    session_id: str
    inicio: str
    fim: str
    total_perguntas: int
    # Quantas perguntas da sessão passaram pela base SQL e pela vetorial.
    consultou_sql: int
    consultou_vetorial: int
    perguntas: list[SessaoPergunta]


app = FastAPI(title="Adaptive RAG API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/query", response_model=QueryResponse)
def query_rag(request: QueryRequest) -> QueryResponse:
    run_id = new_run_id()
    query_id = new_query_id()
    session_id = request.session_id or new_session_id()
    try:
        result = pipeline.responder(
            request.query, top_k=request.top_k, run_id=run_id, query_id=query_id,
            session_id=session_id,
        )
    except Exception as exc:
        logger.exception("Falha ao executar o pipeline RAG")
        raise HTTPException(
            status_code=500,
            detail="Nao foi possivel processar a consulta.",
        ) from exc

    public_result = _public_rag_payload(result)
    resposta = QueryResponse.model_validate(
        {**public_result, "query_id": query_id, "session_id": session_id}
    )

    try:
        historico.registrar_consulta(
            query_id=query_id,
            run_id=run_id,
            query=request.query,
            top_k=request.top_k,
            resposta=public_result,
            session_id=session_id,
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
        session_id=consulta.get("session_id"),
    )
    return {"status": "recebido"}


@app.get("/api/v1/history", response_model=list[HistoricoItem],
         dependencies=[Depends(exigir_chave)])
def obter_historico(
    limit: int = Query(default=20, ge=1, le=100),
    session_id: Optional[str] = Query(default=None, pattern=_ID_PATTERN),
) -> list[dict]:
    items = historico.obter_historico(limit=limit, session_id=session_id)
    for item in items:
        item["resposta"] = _public_rag_payload(item["resposta"])
    return items


@app.get("/api/v1/trace/{query_id}", response_model=TraceResponse,
         dependencies=[Depends(exigir_chave)])
def obter_trace(query_id: str = Path(min_length=1, max_length=64)) -> dict:
    """Caminho da pergunta pelo pipeline, etapa por etapa, lido do log."""
    trace = montar_trace(query_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="query_id sem registro no log.")
    return trace


@app.get("/api/v1/sessions/{session_id}/trace", response_model=SessaoTraceResponse,
         dependencies=[Depends(exigir_chave)])
def obter_trace_sessao(session_id: str = Path(pattern=_ID_PATTERN)) -> dict:
    """Caminho da conversa: as perguntas da sessão em ordem, com o resumo de cada uma."""
    trace = montar_trace_sessao(session_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="session_id sem registro no log.")
    return trace


def _catalog_result(function, **kwargs):
    try:
        return function(**kwargs)
    except FileNotFoundError as exc:
        logger.exception("Falha ao ler o catálogo local")
        raise HTTPException(status_code=503, detail="O acervo está temporariamente indisponível.") from exc
    except Exception as exc:
        logger.exception("Falha ao ler o catálogo local")
        raise HTTPException(status_code=500, detail="Não foi possível consultar os dados do projeto.") from exc


@app.get("/api/v1/explore/contratacoes", response_model=list[ContratacaoRecord])
def explorar_contratacoes(
    busca: str | None = Query(default=None, max_length=500),
    orgao: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return _catalog_result(catalog.listar_contratacoes, busca=busca, orgao=orgao, limit=limit, offset=offset)


@app.get("/api/v1/explore/documentos", response_model=list[DocumentoRecord])
def explorar_documentos(
    busca: str | None = Query(default=None, max_length=500),
    tipo: Literal["Normativo", "Contrato", "Ata de Registro", "PCA", "Conversa sintética"] | None = None,
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return _catalog_result(catalog.listar_documentos, busca=busca, tipo=tipo, limit=limit, offset=offset)


@app.get("/api/v1/explore/orgaos", response_model=list[OrgaoRecord])
def explorar_orgaos(
    busca: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return _catalog_result(catalog.listar_orgaos, busca=busca, limit=limit, offset=offset)


@app.get("/api/v1/explore/fontes", response_model=list[OfficialSource])
def explorar_fontes():
    return _catalog_result(catalog.listar_fontes)


@app.get("/api/v1/project/summary", response_model=ProjectSummary)
def resumo_projeto():
    return _catalog_result(catalog.project_summary)


__all__ = ["app"]
