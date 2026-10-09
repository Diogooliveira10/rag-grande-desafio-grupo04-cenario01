"""Observações de chamadas SDK: uso disponível, erros e retries não observáveis."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import time
from typing import Any, Callable, Iterator

from src.obs import TokenUsage, token_usage_from_response

_calls: ContextVar[list[dict] | None] = ContextVar("model_calls", default=None)


@contextmanager
def coletar_chamadas() -> Iterator[list[dict]]:
    calls: list[dict] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


def resumir_uso(calls: list[dict]) -> tuple[TokenUsage, dict]:
    known = [c for c in calls if c["uso_tokens"]["status"] == "reportado"]
    partial = [c for c in calls if c["uso_tokens"]["status"] == "parcial"]
    status = ("reportado" if calls and len(known) == len(calls) else
              "parcial" if known or partial else "indisponivel")
    totals = {key: sum(c["tokens"].get(key, 0) for c in calls) for key in ("prompt", "completion", "total")}
    return TokenUsage(**totals), {
        "status": status, "fonte": "respostas_sdk_disponiveis",
        "chamadas_observadas": len(calls), "chamadas_sem_usage": len(calls) - len(known) - len(partial),
        "chamadas_usage_parcial": len(partial),
        "retries_internos_sdk": "nao_observados" if any(c["sdk_max_retries"] != 0 for c in calls) else "desativados",
        "nivel_observacao": "chamada_sdk",  # Never pretend these are individual HTTP attempts.
    }


def observar_chamada(
    call: Callable[[], Any], *, provider: str, model: str,
    sdk_max_retries: int | None = None, diagnosticos: dict | None = None,
) -> Any:
    calls = diagnosticos.setdefault("chamadas_modelo", []) if diagnosticos is not None else []
    event = {"provider": provider, "model_requested": model, "model": model,
             "sdk_max_retries": sdk_max_retries, "status": "iniciado",
             "tokens": asdict(TokenUsage()),
             "uso_tokens": {"status": "indisponivel", "fonte": "response.usage"}}
    calls.append(event)
    collector = _calls.get()
    if collector is not None and collector is not calls:
        collector.append(event)
    if diagnosticos is not None:
        diagnosticos["llm_called"] = True
    started = time.perf_counter()
    try:
        response = call()
        event["model"] = getattr(response, "model", None) or model
        extra: dict = {}
        try:
            event["tokens"] = asdict(token_usage_from_response(response, extra=extra))
            event["uso_tokens"] = extra["uso_tokens"]
        except (TypeError, ValueError, AttributeError) as error:
            # Observação de usage malformado não invalida uma resposta/vetor válido.
            event["uso_tokens"] = {"status": "indisponivel", "fonte": "response.usage",
                                   "error_type": type(error).__name__}
        event["status"] = "ok"
        return response
    except BaseException as error:
        event.update(status="erro", error_type=type(error).__name__)
        status_code = getattr(error, "status_code", None)
        if status_code is not None:
            event["status_code"] = status_code
        raise  # Do not change retry/fallback behavior, exception or response.
    finally:
        event["latencia_ms"] = max(0, round((time.perf_counter() - started) * 1000))
        if diagnosticos is not None:
            diagnosticos["uso_tokens"] = resumir_uso(calls)[1]
