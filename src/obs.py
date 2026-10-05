"""Fornece observabilidade ao pipeline RAG por meio de logs JSONL estruturados."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping, ParamSpec, TypeVar

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

    # Conversa/cortesia ou consulta ao acervo: decidida antes da query_analysis.
    INTERACAO = "interacao"
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


def token_usage_from_response(response: Any) -> TokenUsage:
    """Extrai tokens de ``response.usage`` ou retorna uma contagem zerada."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return TokenUsage()

    prompt = _read_usage_value(usage, "prompt_tokens")
    completion = _read_usage_value(usage, "completion_tokens")
    total = _read_usage_value(usage, "total_tokens") or prompt + completion
    return TokenUsage(prompt=prompt, completion=completion, total=total)


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

    def to_dict(self) -> dict[str, Any]:
        """Converte o registro para um dicionário pronto para serialização.

        Os textos de ``input``/``output``, inclusive dentro de dicionários e
        listas, passam pelo mascaramento de ``src/sensitivity.py``: CPF,
        telefone e e-mail pessoal da pergunta não ficam gravados no log.
        """
        data = asdict(self)
        data["stage"] = self.stage.value
        for campo in ("input", "output"):
            data[campo] = _mascarar_textos(data[campo])
        return data


def _mascarar_textos(valor: Any) -> Any:
    """Mascara cada texto de ``valor``, percorrendo dicionários e listas."""
    from src.sensitivity import mascarar

    if isinstance(valor, str):
        return mascarar(valor)[0]
    if isinstance(valor, Mapping):
        return {chave: _mascarar_textos(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_mascarar_textos(item) for item in valor]
    return valor


class PipelineLogger:
    """Grava eventos estruturados e correlacionados do pipeline em JSONL."""

    def __init__(self, logs_dir: str | Path = "logs") -> None:
        """Configura o diretório e o controle de escrita concorrente."""
        self.logs_dir = Path(logs_dir)
        self._lock = threading.Lock()

    def log_path(self, now: datetime | None = None) -> Path:
        """Retorna o caminho diário ``pipeline-AAAA-MM-DD.jsonl``."""
        current = now or datetime.now().astimezone()
        return self.logs_dir / f"pipeline-{current.date().isoformat()}.jsonl"

    def write(self, record: LogRecord) -> Path:
        """Acrescenta um registro ao JSONL e retorna seu caminho."""
        path = self.log_path(datetime.fromisoformat(record.ts))
        line = json.dumps(record.to_dict(), ensure_ascii=False, default=_json_default)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        return path

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
            self.tokens = token_usage_from_response(response)

    def __exit__(
        self,
        _exc_type: Any,
        exc: BaseException | None,
        _traceback: Any,
    ) -> bool:
        """Persiste o evento e retorna ``False`` para preservar exceções."""
        latency = max(0, round((time.perf_counter() - self._started) * 1000))
        status = "erro" if exc is not None else "ok"
        output = self.output
        extra = dict(self.extra)
        if exc is not None:
            output = f"{type(exc).__name__}: {exc}"
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
) -> Path:
    """Grava um evento com ``status`` livre (ex.: ``bloqueado_guardrail_previo``).

    Use quando o status não é só ok/erro — caso em que ``span`` não serve.
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
        )
    )


# Caminho absoluto: o log vai para <repo>/logs independentemente do diretório
# de onde o pipeline é executado.
default_logger = PipelineLogger(Path(__file__).resolve().parents[1] / "logs")
