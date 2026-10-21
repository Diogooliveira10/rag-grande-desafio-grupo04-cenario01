"""Fornece observabilidade ao pipeline RAG por meio de logs JSONL estruturados."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, ParamSpec, TypeVar

import config
from src.log_context import current_details, current_execution, current_origin, stage_outcome

_model_call_lock = threading.Lock()
_last_model_call = 0.0


def aguardar_modelo() -> None:
    """Intervalo entre chamadas reais; cache e clientes de teste não esperam."""
    import config
    global _last_model_call
    with _model_call_lock:
        espera = config.MODEL_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_model_call)
        if espera > 0:
            time.sleep(espera)
        _last_model_call = time.monotonic()


class Stage(str, Enum):
    """Nomes permitidos para as etapas observáveis do pipeline."""

    REQUISICAO_INICIO = "requisicao_inicio"
    REQUISICAO_FIM = "requisicao_fim"
    PIPELINE_INICIO = "pipeline_inicio"
    PIPELINE_FIM = "pipeline_fim"
    PERSISTENCIA_HISTORICO = "persistencia_historico"
    # Conversa/cortesia ou consulta ao acervo: decidida antes da query_analysis.
    INTERACAO = "interacao"
    # Pergunta de continuação reescrita com o histórico enviado pelo cliente.
    REFORMULACAO = "reformulacao"
    QUERY_ANALYSIS = "query_analysis"
    ROTEAMENTO = "roteamento"
    RETRIEVAL_DENSO = "retrieval_denso"
    RETRIEVAL_BM25 = "retrieval_bm25"
    FUSAO_RRF = "fusao_rrf"
    CONSULTA_SQL = "consulta_sql"
    RERANK = "rerank"
    GERACAO = "geracao"
    VALIDACAO = "validacao"
    FEEDBACK = "feedback"
    COLETA = "coleta"
    INVENTARIO = "inventario"
    INGESTAO = "ingestao"
    PREPARACAO = "preparacao"
    EMBEDDINGS = "embeddings"
    INDEXACAO = "indexacao"
    EMBEDDING_LOTE = "embedding_lote"
    # Fecha cada pergunta: bases consultadas (SQL e vetorial), evidências e fontes citadas.
    RESUMO_CONSULTA = "resumo_consulta"


@dataclass(frozen=True)
class TokenUsage:
    """Representa a quantidade de tokens consumidos por uma chamada ao LLM."""

    prompt: int = 0
    completion: int = 0
    total: int = 0

    def __post_init__(self) -> None:
        """Valida se todas as contagens de tokens são não negativas."""
        if min(self.prompt, self.completion, self.total) < 0:
            raise ValueError("A contagem de tokens não pode ser negativa.")


def new_run_id() -> str:
    """Cria um identificador de execução com oito caracteres."""
    return uuid.uuid4().hex[:8]


def new_query_id() -> str:
    """Cria um identificador de consulta no formato ``q_<id>``."""
    return f"q_{uuid.uuid4().hex[:8]}"


def new_session_id() -> str:
    """Cria um identificador de sessão (conversa) no formato ``s_<id>``.

    Aleatório e longo (128 bits): quem tem o id vê o trace da conversa
    (``app/api.py``), então ele não pode ser adivinhado.
    """
    return f"s_{secrets.token_urlsafe(16)}"


# Sessão da pergunta em andamento. ``pipeline.responder`` abre a sessão e todo
# registro gravado dentro dela herda o id, sem passar ``session_id`` por cada
# módulo. ContextVar: requisições simultâneas da API não se misturam.
_sessao_atual: ContextVar[str | None] = ContextVar("sessao_atual", default=None)


@contextmanager
def sessao(session_id: str | None) -> Iterator[None]:
    """Marca com ``session_id`` os registros gravados dentro do bloco."""
    token = _sessao_atual.set(session_id)
    try:
        yield
    finally:
        _sessao_atual.reset(token)


def sessao_atual() -> str | None:
    """``session_id`` da pergunta em andamento, ou ``None`` fora de uma sessão."""
    return _sessao_atual.get()


def token_usage_from_response(response: Any, *, extra: dict[str, Any] | None = None) -> TokenUsage:
    """Preserva contadores inteiros; metadado distingue zero de uso desconhecido."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    def informado(key: str) -> bool:
        value = usage.get(key) if isinstance(usage, Mapping) else getattr(usage, key, None)
        return value is not None
    if extra is not None:
        fields = {key: informado(key + "_tokens") for key in ("prompt", "completion", "total")}
        embedding = getattr(response, "data", None) is not None or isinstance(response, Mapping) and "data" in response
        available = usage is not None and any(fields.values())
        complete = fields["prompt"] and fields["total"] and (fields["completion"] or embedding)
        extra["uso_tokens"] = {"status": "reportado" if complete else "parcial" if available else "indisponivel",
                               "fonte": "response.usage", "chamadas_observadas": 1,
                               "campos_reportados": fields}
    if usage is None:
        # Resposta de embedding não tem texto gerado: estimar pelo texto das
        # escolhas contaria tokens que não existem (o log dizia "indisponivel"
        # com total diferente de zero).
        if getattr(response, "data", None) is not None or isinstance(response, Mapping) and "data" in response:
            return TokenUsage()
        # Estimativa quando o provider não reporta usage (ex.: OpenRouter free).
        # ~4 caracteres por token é a aproximação usual para português/inglês.
        estimated = _estimate_tokens_from_response(response)
        if extra is not None and estimated.total:
            extra["uso_tokens"] = {"status": "estimado", "fonte": "estimativa_texto",
                                   "chamadas_observadas": 1,
                                   "campos_reportados": {"prompt": True, "completion": True, "total": True}}
        return estimated

    prompt = _read_usage_value(usage, "prompt_tokens")
    completion = _read_usage_value(usage, "completion_tokens")
    total = _read_usage_value(usage, "total_tokens") or prompt + completion
    return TokenUsage(prompt=prompt, completion=completion, total=total)


def _estimate_tokens_from_response(response: Any) -> TokenUsage:
    """Estima tokens pelo tamanho do texto quando usage não é reportado."""
    prompt_chars = 0
    completion_chars = 0
    # Extrai texto das escolhas (completion) e das mensagens (prompt).
    choices = getattr(response, "choices", None) or (response.get("choices") if isinstance(response, Mapping) else None) or []
    for choice in choices:
        message = getattr(choice, "message", None) or (choice.get("message") if isinstance(choice, Mapping) else None)
        if message:
            content = getattr(message, "content", None) or (message.get("content") if isinstance(message, Mapping) else None) or ""
            completion_chars += len(str(content))
    messages = getattr(response, "messages", None) or (response.get("messages") if isinstance(response, Mapping) else None) or []
    for msg in messages:
        content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, Mapping) else None) or ""
        prompt_chars += len(str(content))
    if not completion_chars and not prompt_chars:
        return TokenUsage()
    prompt = prompt_chars // 4 if prompt_chars else 0
    completion = completion_chars // 4 if completion_chars else 0
    return TokenUsage(prompt=prompt, completion=completion, total=prompt + completion)


def _read_usage_value(usage: Any, field_name: str) -> int:
    """Lê um campo de uso de tokens de um objeto ou dicionário."""
    if isinstance(usage, Mapping):
        value = usage.get(field_name, 0)
    else:
        value = getattr(usage, field_name, 0)
    return int(value or 0)


def _json_default(value: Any) -> Any:
    """Converte tipos conhecidos para valores serializáveis em JSON."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


@dataclass
class LogRecord:
    """Armazena o contrato mínimo de um evento de observabilidade."""

    ts: str
    run_id: str
    query_id: str
    stage: Stage
    status: str
    latencia_ms: int
    input: Any
    output: Any
    tokens: TokenUsage = field(default_factory=TokenUsage)
    modelo: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Liga as perguntas da mesma conversa; vem de ``sessao()`` quando omitido.
    session_id: str | None = field(default_factory=sessao_atual)

    def to_dict(self) -> dict[str, Any]:
        """Converte o registro para um dicionário pronto para serialização.

        Os textos de ``input``/``output``/``extra``, inclusive dentro de dicionários e
        listas, passam pelo mascaramento de ``src/sensitivity.py``: CPF,
        telefone e e-mail pessoal da pergunta não ficam gravados no log.
        """
        data = asdict(self)
        data["stage"] = self.stage.value
        for campo in ("input", "output", "extra"):
            data[campo] = _mascarar_textos(data[campo])
        return data


def _mascarar_textos(valor: Any) -> Any:
    """Mascara cada texto de ``valor``, percorrendo dicionários e listas."""
    from src.sensitivity import mascarar_valores

    return mascarar_valores(valor)


class PipelineLogger:
    """Grava eventos estruturados e correlacionados do pipeline em JSONL."""

    def __init__(self, logs_dir: str | Path = "logs", *, retention_days: int = 0) -> None:
        """Configura o diretório; zero dias preserva todos os arquivos."""
        if retention_days < 0:
            raise ValueError("retention_days deve ser não negativo")
        self.logs_dir = Path(logs_dir)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._last_cleanup: tuple[Path, date] | None = None
        self._writer = None
        self._writer_pid = os.getpid()

    def expired_paths(self, now: datetime | None = None) -> list[Path]:
        """Somente JSONL diários antigos; nunca diretórios, links ou backups."""
        if not self.retention_days:
            return []
        current = now or datetime.now().astimezone()
        cutoff = current.date() - timedelta(days=self.retention_days)
        expired = []
        for path in sorted(self.logs_dir.glob("pipeline-*.jsonl")):
            match = re.fullmatch(r"pipeline-(\d{4}-\d{2}-\d{2})\.jsonl", path.name)
            if not match or not path.is_file() or path.is_symlink():
                continue
            try:
                day = date.fromisoformat(match[1])
            except ValueError:
                continue
            if day < cutoff:
                expired.append(path)
        return expired

    def _purge_expired(self, now: datetime) -> None:
        for path in self.expired_paths(now):
            try:
                path.unlink()
            except OSError:
                # Retention failure must not turn a successful query into HTTP 500.
                logging.getLogger(__name__).warning("Não foi possível expurgar %s", path)

    def log_path(self, now: datetime | None = None) -> Path:
        """Retorna o caminho diário ``pipeline-AAAA-MM-DD.jsonl``."""
        current = now or datetime.now().astimezone()
        return self.logs_dir / f"pipeline-{current.date().isoformat()}.jsonl"

    def write(self, record: LogRecord) -> Path:
        """Acrescenta um registro ao JSONL e retorna seu caminho."""
        path = self.log_path(datetime.fromisoformat(record.ts))
        data = record.to_dict()
        context = current_execution()
        data["extra"] = {**_mascarar_textos(current_details()), **data["extra"],
                         "schema_version": 2, "origem": data["extra"].get("origem", current_origin())}
        data["extra"].update({"outcome": stage_outcome(record.status, data["extra"]),
                              "attempted": record.status != "pulado"})
        llm = data["extra"].get("llm") or {}
        if "llm_called" in llm:
            data["extra"]["llm_called"] = llm["llm_called"]
        if llm.get("uso_tokens"):
            data["extra"]["uso_tokens"] = llm["uso_tokens"]
        if "uso_tokens" not in data["extra"]:
            data["extra"]["uso_tokens"] = {"status": "reportado" if record.tokens.total else
                "indisponivel" if data["extra"].get("llm_called") else "nao_aplicavel"}
        if data["extra"].get("llm_called") or llm.get("llm_called"):
            data["modelo"] = data["modelo"] or data["extra"].get("model") or llm.get("model")
        if context and (record.run_id, record.query_id) == (context.run_id, context.query_id):
            context.sequence += 1
            data["extra"].update({"schema_version": 2, "origem": context.origin,
                                  "sequencia": context.sequence})
            context.events.append({"stage": data["stage"], "status": data["status"],
                                   "extra": data["extra"], "latencia_ms": data["latencia_ms"],
                                   "tokens": data["tokens"]})
        line = json.dumps(data, ensure_ascii=False, default=_json_default)
        try:
            from src.log_store import EventStore, QueuedEventWriter
            if self._writer_pid != os.getpid():
                # A fork must not reuse a queue whose consumer stayed in the parent.
                self._writer, self._lock = None, threading.Lock()
                self._writer_pid = os.getpid()
            with self._lock:
                now = datetime.now().astimezone()
                cleanup_key = (self.logs_dir, now.date())
                if self._last_cleanup != cleanup_key:
                    self._purge_expired(now)
                    self._last_cleanup = cleanup_key
                if self._writer and self._writer.store.directory != Path(self.logs_dir).resolve():
                    self._writer.close()
                    self._writer = None
                if context:
                    if self._writer is None:
                        self._writer = QueuedEventWriter(self.logs_dir)
                    job = self._writer.submit(path, line, lambda error: self._write_error(error, context))
                else:
                    # Isolated module calls promise an immediately readable event.
                    EventStore(self.logs_dir).append_batch([(path, line)])
                    job = None
            if job and record.stage in {Stage.PIPELINE_FIM, Stage.REQUISICAO_FIM}:
                job.done.wait()  # Trace/history can read the complete execution now.
        except Exception as error:
            self._write_error(error, context)
        return path

    @staticmethod
    def _write_error(error: BaseException, context: Any) -> None:
        if context:
            context.logging_errors += 1
        logging.getLogger(__name__).error("Falha ao gravar evento de observabilidade",
            exc_info=(type(error), error, error.__traceback__))

    def flush(self) -> None:
        """Wait for queued events before building an envelope or reading a trace."""
        if self._writer_pid == os.getpid() and self._writer is not None:
            self._writer.flush()

    def close(self) -> None:
        """Flush on orderly shutdown; JSONL and the index stay readable."""
        if self._writer_pid == os.getpid() and self._writer is not None:
            self._writer.close()
            self._writer = None

    def span(
        self,
        *,
        run_id: str,
        query_id: str,
        stage: Stage | str,
        input: Any,
        modelo: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> "LogSpan":
        """Cria um contexto para medir e registrar uma etapa do pipeline."""
        return LogSpan(
            logger=self,
            run_id=run_id,
            query_id=query_id,
            stage=Stage(stage),
            input=input,
            modelo=modelo,
            extra=dict(extra or {}),
        )


class LogSpan:
    """Mede uma etapa e garante registro tanto em sucesso quanto em erro."""

    def __init__(
        self,
        *,
        logger: PipelineLogger,
        run_id: str,
        query_id: str,
        stage: Stage,
        input: Any,
        modelo: str | None,
        extra: dict[str, Any],
    ) -> None:
        """Inicializa os dados de uma medição ainda não iniciada."""
        self.logger = logger
        self.run_id = run_id
        self.query_id = query_id
        self.stage = stage
        self.input = input
        self.modelo = modelo
        self.extra = extra
        self.output: Any = None
        self.status = "ok"
        self.tokens = TokenUsage()
        self._started = 0.0
        self._timestamp = ""

    def __enter__(self) -> "LogSpan":
        """Marca o instante inicial da etapa."""
        self._timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._started = time.perf_counter()
        return self

    def set_result(self, output: Any, response: Any | None = None) -> None:
        """Define a saída observável e captura tokens opcionais."""
        self.output = output
        if response is not None:
            self.tokens = token_usage_from_response(response, extra=self.extra)

    def __exit__(
        self,
        _exc_type: Any,
        exc: BaseException | None,
        _traceback: Any,
    ) -> bool:
        """Persiste o evento e retorna ``False`` para preservar exceções."""
        latency = max(0, round((time.perf_counter() - self._started) * 1000))
        status = "erro" if exc is not None else self.status
        if status == "ok" and (self.extra.get("fallback_used")
                               or (self.extra.get("llm") or {}).get("fallback_used")):
            status = "degradado"
        output = self.output
        extra = dict(self.extra)
        if exc is not None:
            output = (f"{type(exc).__name__}: falha ao salvar histórico"
                      if self.stage == Stage.PERSISTENCIA_HISTORICO else f"{type(exc).__name__}: {exc}")
            extra["error_type"] = type(exc).__name__

        self.logger.write(
            LogRecord(
                ts=self._timestamp,
                run_id=self.run_id,
                query_id=self.query_id,
                stage=self.stage,
                status=status,
                latencia_ms=latency,
                input=self.input,
                output=output,
                tokens=self.tokens,
                modelo=self.modelo,
                extra=extra,
            )
        )
        return False


P = ParamSpec("P")
R = TypeVar("R")


def observe(
    logger: PipelineLogger,
    *,
    stage: Stage | str,
    run_id: str,
    query_id: str,
    modelo: str | None = None,
    input_builder: Callable[..., Any] | None = None,
    output_builder: Callable[[Any], Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decora uma função para registrar latência, resultado e uso de tokens."""

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        """Constrói o wrapper de observabilidade para a função recebida."""

        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            """Executa a função dentro de um span e devolve seu resultado."""
            observed_input = (
                input_builder(*args, **kwargs)
                if input_builder
                else {"args": args, "kwargs": kwargs}
            )
            with logger.span(
                run_id=run_id,
                query_id=query_id,
                stage=stage,
                input=observed_input,
                modelo=modelo,
                extra=extra,
            ) as span:
                result = function(*args, **kwargs)
                observed_output = output_builder(result) if output_builder else result
                span.set_result(observed_output, response=result)
                return result

        return wrapped

    return decorator


def registrar(
    *,
    run_id: str,
    query_id: str,
    stage: Stage | str,
    status: str,
    latencia_ms: float,
    input: Any,
    output: Any,
    tokens: TokenUsage | None = None,
    modelo: str | None = None,
    extra: dict[str, Any] | None = None,
    logger: PipelineLogger | None = None,
    session_id: str | None = None,
) -> Path:
    """Grava um evento com ``status`` livre (ex.: ``bloqueado_guardrail_previo``).

    Use quando o status não é só ok/erro — caso em que ``span`` não serve.
    ``session_id`` omitido vem da sessão aberta com ``sessao()``.
    """
    return (logger or default_logger).write(
        LogRecord(
            ts=datetime.now().astimezone().isoformat(timespec="milliseconds"),
            run_id=run_id,
            query_id=query_id,
            stage=Stage(stage),
            status=status,
            latencia_ms=max(0, round(latencia_ms)),
            input=input,
            output=output,
            tokens=tokens or TokenUsage(),
            modelo=modelo,
            extra=dict(extra or {}),
            session_id=session_id or sessao_atual(),
        )
    )


def bounded_log_value(value: Any, limit: int = 8000) -> Any:
    """Mask before truncating so a cut cannot expose half of a personal identifier."""
    if isinstance(value, str):
        value = _mascarar_textos(value)
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, Mapping):
        return {key: bounded_log_value(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [bounded_log_value(item, limit) for item in value]
    return value


def registrar_pulo(*, run_id: str, query_id: str, stage: Stage, query: str, motivo: str) -> None:
    """Explicit skipped stages only inside a complete pipeline/request context."""
    if current_execution() is not None:
        registrar(run_id=run_id, query_id=query_id, stage=stage, status="pulado",
                  latencia_ms=0, input=query, output=None, extra={"motivo": motivo})


@dataclass
class Lifecycle:
    output: Any = None
    status: str = "ok"
    extra: dict[str, Any] = field(default_factory=dict)


@contextmanager
def lifecycle(*, run_id: str, query_id: str, query: str, start: Stage, end: Stage,
              extra: dict[str, Any] | None = None) -> Iterator[Lifecycle]:
    """Monotonic wall time and a terminal event even when an exception escapes.

    A process kill still leaves a start without a finish: an incomplete trace,
    not a false success. No stack trace/error message is exposed to the client.
    """
    started = time.perf_counter()
    timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    state = Lifecycle()
    from src.log_context import runtime_context
    try:
        snapshot = runtime_context()
    except Exception as error:
        snapshot = {"unavailable": True, "error_type": type(error).__name__}
        logging.getLogger(__name__).warning("Metadados de observabilidade indisponíveis: %s",
                                            type(error).__name__)
    metadata = {"contexto_execucao": snapshot, **(extra or {})}
    default_logger.write(LogRecord(ts=timestamp, run_id=run_id, query_id=query_id, stage=start,
                                   status="iniciado", latencia_ms=0, input=query, output=None,
                                   extra=metadata))
    try:
        yield state
    except BaseException as error:
        state.status = "erro"
        state.extra.update({"error_type": type(error).__name__, "completed": False})
        raise
    finally:
        default_logger.flush()
        context = current_execution()
        state.extra.setdefault("completed", True)
        if context:
            state.extra.update({"etapas": context.stages(), "logging_errors": context.logging_errors,
                                "tokens_reportados": context.reported_tokens(),
                                "cobertura_tokens": context.token_coverage()})
        masked_output = _mascarar_textos(state.output)
        bounded_output = bounded_log_value(masked_output)
        state.extra.update({"log_text_limit": 8000, "output_truncated": bounded_output != masked_output})
        registrar(run_id=run_id, query_id=query_id, stage=end, status=state.status,
                  latencia_ms=(time.perf_counter() - started) * 1000, input=query,
                  output=bounded_output, extra=state.extra)


# Caminho absoluto: o log vai para <repo>/logs independentemente do diretório
# de onde o pipeline é executado.
default_logger = PipelineLogger(config.PIPELINE_LOGS_DIR, retention_days=config.LOG_RETENTION_DAYS)
