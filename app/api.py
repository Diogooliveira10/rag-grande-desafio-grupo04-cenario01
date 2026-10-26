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

Acesso ao historico com dois personas (decisao de 2026-10-14): quem tem a
``RAG_API_KEY`` (operador) ve o historico inteiro e pode filtrar por
``session_id``; o dono da conversa se identifica pelo header ``X-Session-Id``
e ve so as suas consultas. O query param ``session_id`` e filtro do operador,
nao autenticacao. O trace segue a mesma autorizacao. ``HISTORY_PUBLIC=true``
no ``.env`` abre o historico geral sem chave, para uma demo que precise dele;
mostra consultas de todos os usuarios, entao fica desligado por padrao.
``/query`` e ``/feedback``
continuam abertos: sao o uso normal do chat.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

import config
from src import pipeline, rerank, retrieve, snapshot
from app import catalog
from app.rate_limit import LimiteDeRequisicoes, cliente_real
from app.catalog_models import ContratacaoRecord, DocumentoRecord, OfficialSource, OrgaoRecord, ProjectSummary
from src.history_store import ConsultaNaoEncontrada, default_store as historico
from src.obs import (Stage, default_logger, lifecycle, new_query_id, new_run_id,
                     new_session_id, sessao)
from src.log_context import current_execution, execution
from src.obs import registrar as obs_registrar
from src.reformulation import TurnoAnterior
from src.schema import RAGResponse
from src.trace import montar_trace, montar_trace_sessao

logger = logging.getLogger(__name__)

# Ids que chegam do front-end vão para o log e para a URL do trace: só letras,
# dígitos, "_" e "-".
_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
# O session_id da acesso ao trace da conversa: um id curto escolhido pelo
# cliente ("s_1") seria facil de adivinhar, entao exige pelo menos 16 caracteres.
_SESSION_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"


def _chave_valida(x_api_key: Optional[str]) -> bool:
    chave = config.RAG_API_KEY
    return bool(chave and x_api_key) and secrets.compare_digest(x_api_key.encode(), chave.encode())


def _autorizar_trace(
    x_api_key: Optional[str], x_session_id: Optional[str], sessao_do_trace: Optional[str]
) -> None:
    """Trace liberado para a chave de quem opera ou para o dono da conversa.

    Sem nenhuma das duas, responde 401 mesmo quando o id nao existe, para nao
    revelar quais perguntas estao no log.
    """
    if _chave_valida(x_api_key):
        return
    if x_session_id and sessao_do_trace and secrets.compare_digest(
        x_session_id.encode(), sessao_do_trace.encode()
    ):
        return
    raise HTTPException(
        status_code=401,
        detail="Envie X-Session-Id da conversa ou X-API-Key.",
    )


def _sessao_do_historico(
    x_api_key: Optional[str], x_session_id: Optional[str], session_id: Optional[str]
) -> Optional[str]:
    """Sessão a que o histórico fica restrito; ``None`` = histórico geral.

    Dois personas: o operador com chave (ou ``HISTORY_PUBLIC``) vê tudo e
    filtra por ``session_id``; o dono se identifica pelo ``X-Session-Id`` e vê
    só a sua conversa. Para o dono o ``session_id`` de query não autentica: o
    escopo vem do header. Sem nenhuma das duas credenciais, 401 sem revelar o
    que existe no histórico.
    """
    if config.HISTORY_PUBLIC or _chave_valida(x_api_key):
        return session_id
    if x_session_id:
        return x_session_id
    raise HTTPException(
        status_code=401,
        detail="Envie X-Session-Id da conversa ou X-API-Key.",
    )


def _public_source_file(source_file: object) -> str:
    """Nome do arquivo, sem caminho local; fonte SQL antiga sem o host do banco."""
    texto = str(source_file or "")
    if texto.startswith(("postgres://", "postgresql://")):
        return "postgres"
    return PurePosixPath(texto.replace("\\", "/")).name


def _public_rag_payload(payload: dict) -> dict:
    """Remove caminhos locais das fontes antes de persistir ou expor via HTTP."""
    public = dict(payload)
    public["sources_used"] = [
        {**source, "source_file": _public_source_file(source.get("source_file"))}
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
    session_id: Optional[str] = Field(default=None, pattern=_SESSION_PATTERN)
    # Últimas trocas da conversa, do mais antigo ao mais recente. O backend não
    # guarda memória entre perguntas: com isso, "considerando os mesmos
    # contratos…" é reescrita para se sustentar sozinha (src/reformulation.py).
    # O pipeline usa só as 3 mais recentes.
    historico: list[TurnoAnterior] = Field(default_factory=list, max_length=10)

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
    # Pergunta de fato consultada, quando a API reescreveu uma continuação com o
    # historico enviado. ``query`` continua sendo o que o usuario digitou.
    pergunta_reformulada: Optional[str] = None


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
    # Additive fields; old JSONL remains readable and timing is labelled estimated.
    status: Optional[str] = None
    completo: Optional[bool] = None
    latencia_metodo: str = "soma_etapas_legado"
    latencia_pipeline_ms: Optional[int] = None


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
    status: Optional[str] = None
    completo: Optional[bool] = None
    latencia_metodo: str = "soma_etapas_legado"


class SessaoTraceResponse(BaseModel):
    session_id: str
    inicio: str
    fim: str
    total_perguntas: int
    # Quantas perguntas da sessão passaram pela base SQL e pela vetorial.
    consultou_sql: int
    consultou_vetorial: int
    perguntas: list[SessaoPergunta]


@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI):
    # O cross-encoder leva ~15 s para carregar: numa thread, para o servidor
    # já aceitar requisições, e antes da primeira pergunta de verdade.
    if config.RERANK_ENABLED and config.RERANK_PRELOAD:
        threading.Thread(target=rerank.pre_carregar, name="rerank-preload", daemon=True).start()
    if config.INDEX_PRELOAD:
        threading.Thread(target=retrieve.pre_carregar_indices, name="index-preload", daemon=True).start()
        # Estatística das palavras dos objetos, usada para reconhecer fornecedor
        # por um nome curto ("da Medshore"): ~4 s na primeira pergunta sem isso.
        from src import query_analysis
        threading.Thread(target=query_analysis._palavras_dos_objetos, name="fornecedor-preload",
                         daemon=True).start()
    # O catálogo lê e compacta o JSONL ativo uma vez (~7 s na primeira carga).
    # Aquecer em thread evita que a primeira tela de Explorar pague esse custo
    # dentro do timeout de 30 s do navegador. Falha aqui não derruba o servidor.
    threading.Thread(target=catalog.prewarm, name="catalog-preload", daemon=True).start()
    yield


limite_de_consultas = LimiteDeRequisicoes(config.RATE_LIMIT_QUERY_PER_MINUTE)
limite_de_leitura = LimiteDeRequisicoes(config.RATE_LIMIT_READ_PER_MINUTE)
_ROTAS_DE_CONSULTA = {"/api/v1/query", "/api/v1/feedback"}


def _limitar_requisicoes(request: Request) -> None:
    """429 quando o cliente passa do limite; /health e /ready ficam de fora.

    É dependência, e não middleware, para a resposta 429 passar pelo CORS: o
    navegador só deixa o site ler o erro com os cabeçalhos do CORS.
    """
    caminho = request.url.path
    if not caminho.startswith("/api/"):
        return
    limite = limite_de_consultas if caminho in _ROTAS_DE_CONSULTA else limite_de_leitura
    cliente = cliente_real(request.client.host if request.client else None,
                           request.headers.get("x-forwarded-for"))
    espera = limite.consumir(cliente)
    if espera:
        logger.warning("limite de requisições: %s em %s", "consulta" if limite is limite_de_consultas
                       else "leitura", caminho)
        raise HTTPException(
            status_code=429,
            detail=f"Muitas requisições em pouco tempo. Tente de novo em {espera} s.",
            headers={"Retry-After": str(espera)},
        )


app = FastAPI(title="Adaptive RAG API", version="1.0.0", lifespan=_ciclo_de_vida,
              dependencies=[Depends(_limitar_requisicoes)],
              docs_url="/docs" if config.API_DOCS else None,
              redoc_url="/redoc" if config.API_DOCS else None,
              openapi_url="/openapi.json" if config.API_DOCS else None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    # ngrok-skip-browser-warning: o túnel ngrok gratuito põe uma página de aviso
    # na frente do tráfego de navegador; o site envia este header para pulá-la.
    allow_headers=["Content-Type", "X-API-Key", "X-Session-Id", "ngrok-skip-browser-warning"],
)

# Cabeçalhos de segurança em toda resposta. A API só devolve JSON: nada dela
# deve ser renderizado em frame, reinterpretado pelo navegador ou guardado em
# cache (as respostas e os traces trazem as perguntas dos usuários).
_CABECALHOS_DE_SEGURANCA = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@app.middleware("http")
async def _cabecalhos_de_seguranca(request: Request, call_next):
    resposta = await call_next(request)
    for nome, valor in _CABECALHOS_DE_SEGURANCA.items():
        resposta.headers.setdefault(nome, valor)
    return resposta


@app.exception_handler(RequestValidationError)
async def query_validation_error(request: Request, error: RequestValidationError):
    if request.url.path == "/api/v1/query":
        # No unvalidated body, headers or secrets enter observability.
        run_id, query_id = new_run_id(), new_query_id()
        with execution(run_id, query_id, "api"):
            with lifecycle(run_id=run_id, query_id=query_id, query="",
                           start=Stage.REQUISICAO_INICIO, end=Stage.REQUISICAO_FIM,
                           extra={"method": "POST", "path": "/api/v1/query"}) as flow:
                flow.status = "rejeitado"
                flow.extra.update({"http_status": 422, "motivo": "entrada_invalida",
                                   "validation_errors_count": len(error.errors()),
                                   "errors": [{"loc": list(e["loc"]), "type": e["type"]}
                                              for e in error.errors()[:20]]})
    return await request_validation_exception_handler(request, error)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness only; not a promise that analytical snapshots are ready."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, Any]:
    """Read-only bundle readiness, distinct from process liveness."""
    result = snapshot.readiness()
    if not result["ready"]:
        raise HTTPException(status_code=503, detail=result)
    return result


@app.post("/api/v1/query", response_model=QueryResponse)
def query_rag(request: QueryRequest) -> QueryResponse:
    run_id = new_run_id()
    query_id = new_query_id()
    session_id = request.session_id or new_session_id()
    # So repassa o historico quando ha um: quem chama sem ele fica como antes.
    extras = {"historico": request.historico} if request.historico else {}
    with sessao(session_id), execution(run_id, query_id, "api"):
        with lifecycle(run_id=run_id, query_id=query_id, query=request.query,
                       start=Stage.REQUISICAO_INICIO, end=Stage.REQUISICAO_FIM,
                       extra={"method": "POST", "path": "/api/v1/query", "top_k": request.top_k,
                              "historico_turnos": len(request.historico)}) as flow:
            flow.extra["http_status"] = 500
            try:
                result = pipeline.responder(
                    request.query, top_k=request.top_k, run_id=run_id, query_id=query_id,
                    session_id=session_id, **extras,
                )
            except Exception as exc:
                flow.extra["pipeline_error_type"] = type(exc).__name__
                logger.exception("Falha ao executar o pipeline RAG")
                raise HTTPException(
                    status_code=500, detail="Nao foi possivel processar a consulta.",
                ) from exc

            public_result = _public_rag_payload(result)
            resposta = QueryResponse.model_validate(
                {**public_result, "query_id": query_id, "session_id": session_id}
            )
            try:
                with default_logger.span(run_id=run_id, query_id=query_id,
                                         stage=Stage.PERSISTENCIA_HISTORICO,
                                         input={"query_id": query_id}) as span:
                    historico.registrar_consulta(
                        query_id=query_id, run_id=run_id, query=request.query,
                        top_k=request.top_k, resposta=public_result, session_id=session_id,
                    )
                    span.set_result({"saved": True})
                flow.extra["history_saved"] = True
            except Exception:
                flow.extra["history_saved"] = False
                logger.exception("Falha ao salvar historico da consulta %s", query_id)

            flow.extra["http_status"] = 200
            flow.status = ("degradado" if not flow.extra["history_saved"]
                           else "erro" if public_result.get("refusal_reason") == "erro_provedor"
                           else "recusa" if public_result.get("is_refusal")
                           else "degradado" if any(event["outcome"] in {"erro", "degradado"}
                                                    for events in current_execution().stages().values()
                                                    for event in events)
                           else "ok")
            flow.output = resposta.model_dump(mode="json")
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
        extra={"origem": "api"},
    )
    return {"status": "recebido"}


@app.get("/api/v1/history", response_model=list[HistoricoItem])
def obter_historico(
    limit: int = Query(default=20, ge=1, le=100),
    session_id: Optional[str] = Query(default=None, pattern=_ID_PATTERN),
    x_api_key: Optional[str] = Header(default=None),
    x_session_id: Optional[str] = Header(default=None),
) -> list[dict]:
    sessao_permitida = _sessao_do_historico(x_api_key, x_session_id, session_id)
    items = historico.obter_historico(limit=limit, session_id=sessao_permitida)
    for item in items:
        item["resposta"] = _public_rag_payload(item["resposta"])
    return items


@app.get("/api/v1/trace/{query_id}", response_model=TraceResponse)
def obter_trace(
    query_id: str = Path(min_length=1, max_length=64),
    x_api_key: Optional[str] = Header(default=None),
    x_session_id: Optional[str] = Header(default=None),
) -> dict:
    """Caminho da pergunta pelo pipeline, etapa por etapa, lido do log."""
    trace = montar_trace(query_id)
    _autorizar_trace(x_api_key, x_session_id, trace["session_id"] if trace else None)
    if trace is None:
        raise HTTPException(status_code=404, detail="query_id sem registro no log.")
    return trace


@app.get("/api/v1/sessions/{session_id}/trace", response_model=SessaoTraceResponse)
def obter_trace_sessao(
    session_id: str = Path(pattern=_SESSION_PATTERN),
    x_api_key: Optional[str] = Header(default=None),
    x_session_id: Optional[str] = Header(default=None),
) -> dict:
    """Caminho da conversa: as perguntas da sessão em ordem, com o resumo de cada uma."""
    _autorizar_trace(x_api_key, x_session_id, session_id)
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
