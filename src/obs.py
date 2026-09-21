"""Observabilidade do pipeline RAG.

Cada etapa escreve uma linha JSON em ``logs/pipeline-AAAA-MM-DD.jsonl``.
O módulo não depende de rede nem de bibliotecas externas e pode ser usado tanto
como context manager (quando a etapa precisa enriquecer o log) quanto como
decorator (casos simples e chamadas ao LLM).
"""

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


class Stage(str, Enum):
    """Nomes permitidos para as etapas observáveis do pipeline."""

    QUERY_ANALYSIS = "query_analysis"
    ROTEAMENTO = "roteamento"
    RETRIEVAL_DENSO = "retrieval_denso"
    RETRIEVAL_BM25 = "retrieval_bm25"
    FUSAO_RRF = "fusao_rrf"
    RERANK = "rerank"
    GERACAO = "geracao"
    VALIDACAO = "validacao"
    FEEDBACK = "feedback"


@dataclass(frozen=True)
class TokenUsage:
    """Representa a quantidade de tokens consumidos por uma chamada ao LLM."""

    prompt: int = 0
    completion: int = 0
    total: int = 0

    def __post_init__(self) -> None:
        """Valida se todas as contagens são não negativas.

        Complexidade:
            Tempo O(1) e espaço O(1).
        """
        if min(self.prompt, self.completion, self.total) < 0:
            raise ValueError("A contagem de tokens não pode ser negativa.")


def new_run_id() -> str:
    """Cria o identificador compartilhado pelas etapas de uma execução.

    Returns:
        Identificador hexadecimal com oito caracteres.

    Complexidade:
        Tempo O(1) e espaço O(1).
    """
    return uuid.uuid4().hex[:8]


def new_query_id() -> str:
    """Cria um identificador único para uma consulta.

    Returns:
        Identificador no formato ``q_<oito caracteres hexadecimais>``.

    Complexidade:
        Tempo O(1) e espaço O(1).
    """
    return f"q_{uuid.uuid4().hex[:8]}"


def token_usage_from_response(response: Any) -> TokenUsage:
    """Extrai o uso de tokens de respostas OpenAI ou objetos equivalentes.

    Args:
        response: Objeto ou dicionário que pode conter o atributo ``usage``.

    Returns:
        Contagem encontrada ou uma contagem zerada quando ``usage`` não existe.

    Complexidade:
        Tempo O(1) e espaço O(1).
    """
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
    """Lê um campo numérico de um objeto ou dicionário de uso de tokens.

    Complexidade:
        Tempo O(1) e espaço O(1).
    """
    if isinstance(usage, Mapping):
        value = usage.get(field_name, 0)
    else:
        value = getattr(usage, field_name, 0)
    return int(value or 0)


def _json_default(value: Any) -> Any:
    """Converte tipos conhecidos para valores serializáveis em JSON.

    Complexidade:
        Tempo e espaço O(n), em que ``n`` é o tamanho do objeto convertido.
    """
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

        Returns:
            Dicionário contendo todos os campos do contrato de logging.

        Complexidade:
            Tempo e espaço O(n), conforme o tamanho dos dados do registro.
        """
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


class PipelineLogger:
    """Grava eventos estruturados e correlacionados do pipeline em JSONL."""

    def __init__(self, logs_dir: str | Path = "logs") -> None:
        """Configura o diretório de logs e o controle de escrita concorrente.

        Args:
            logs_dir: Diretório no qual os arquivos diários serão armazenados.

        Complexidade:
            Tempo O(1) e espaço O(1).
        """
        self.logs_dir = Path(logs_dir)
        self._lock = threading.Lock()

    def log_path(self, now: datetime | None = None) -> Path:
        """Calcula o caminho do arquivo de log para uma determinada data.

        Args:
            now: Data e hora do evento. Usa o horário local quando omitida.

        Returns:
            Caminho no formato ``pipeline-AAAA-MM-DD.jsonl``.

        Complexidade:
            Tempo O(1) e espaço O(1).
        """
        current = now or datetime.now().astimezone()
        return self.logs_dir / f"pipeline-{current.date().isoformat()}.jsonl"

    def write(self, record: LogRecord) -> Path:
        """Serializa e acrescenta um registro ao arquivo JSONL diário.

        Args:
            record: Evento de observabilidade que será persistido.

        Returns:
            Caminho do arquivo que recebeu o registro.

        Complexidade:
            Tempo e espaço O(n), em que ``n`` é o tamanho do registro.
        """
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
        """Cria um contexto para medir e registrar uma etapa do pipeline.

        Args:
            run_id: Identificador comum a toda a execução.
            query_id: Identificador da pergunta processada.
            stage: Etapa do pipeline, validada pelo enum :class:`Stage`.
            input: Entrada relevante para diagnóstico da etapa.
            modelo: Modelo utilizado, quando houver chamada ao LLM.
            extra: Metadados adicionais específicos da etapa.

        Returns:
            Context manager que registra sucesso ou erro ao finalizar.

        Complexidade:
            Tempo O(m) e espaço O(m), sendo ``m`` o tamanho de ``extra``.
        """
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
        """Inicializa os dados de uma medição ainda não iniciada.

        Complexidade:
            Tempo O(1) e espaço O(1), desconsiderando objetos recebidos.
        """
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
        """Marca o instante inicial da etapa.

        Complexidade:
            Tempo O(1) e espaço O(1).
        """
        self._timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._started = time.perf_counter()
        return self

    def set_result(self, output: Any, response: Any | None = None) -> None:
        """Define a saída observável e captura tokens da resposta opcional.

        Args:
            output: Resumo ou resultado que será registrado.
            response: Resposta de LLM da qual ``usage`` será extraído.

        Complexidade:
            Tempo O(1) e espaço O(1), sem copiar os objetos recebidos.
        """
        self.output = output
        if response is not None:
            self.tokens = token_usage_from_response(response)

    def __exit__(
        self,
        _exc_type: Any,
        exc: BaseException | None,
        _traceback: Any,
    ) -> bool:
        """Finaliza a medição, persiste o evento e preserva exceções.

        Args:
            _exc_type: Tipo da exceção, não utilizado diretamente.
            exc: Exceção capturada pelo contexto, quando existente.
            _traceback: Rastreamento da exceção, não persistido por segurança.

        Returns:
            Sempre ``False`` para nunca suprimir uma exceção da aplicação.

        Complexidade:
            Tempo e espaço O(n), em que ``n`` é o tamanho do evento gravado.
        """
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
    """Decora uma função para capturar latência, resultado e uso de tokens.

    Args:
        logger: Gravador que persistirá o evento.
        stage: Etapa do pipeline executada pela função.
        run_id: Identificador comum às etapas da execução.
        query_id: Identificador da consulta processada.
        modelo: Nome do modelo utilizado, quando aplicável.
        input_builder: Função opcional que resume os argumentos de entrada.
        output_builder: Função opcional que resume o valor retornado.
        extra: Metadados adicionais incluídos no evento.

    Returns:
        Decorator que preserva assinatura lógica, retorno e exceções da função.

    Complexidade:
        Acrescenta O(n) por chamada para serializar um evento de tamanho ``n``.
    """

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


default_logger = PipelineLogger()
