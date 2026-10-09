"""Contrato JSONL comum para coleta, preparação, embeddings e indexação.

Não modifica os artefatos ou retornos existentes. Execuções offline têm IDs de
correlação próprios; query_id é sintético, não uma pergunta de usuário.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import inspect
from pathlib import Path
from typing import Any, Callable, Iterator

from src.log_context import current_execution, execution, log_details
from src.obs import Stage, default_logger, lifecycle, new_query_id, new_run_id
from src.model_observation import coletar_chamadas, resumir_uso

_SAFE_ARGUMENTS = {
    "input_dir", "input_file", "input_path", "output_dir", "output_file", "output_path",
    "manifest_path", "inventory_path", "report_path", "report_file", "base_id", "base_ids",
    "command", "version", "batch_size", "chunk_size", "max_chars", "overlap", "dry_run",
    "provider", "model", "merge_existing", "pause_seconds", "official", "allow_uninventoried",
}


def _summary(value: Any) -> dict:
    if isinstance(value, int):
        return {"exit_code": value}
    if not isinstance(value, dict):
        return {"tipo_resultado": type(value).__name__}
    result = {}
    for key, item in value.items():
        if key in {"endpoint", "api_key", "url"}:
            continue
        if item is None or isinstance(item, (bool, int, float, str, Path)):
            result[key] = item
        elif isinstance(item, dict) and all(v is None or isinstance(v, (bool, int, float)) for v in item.values()):
            result[key] = item
        elif isinstance(item, (list, dict)):
            result[key + "_quantidade"] = len(item)
    return result


def observar_offline(stage: Stage) -> Callable:
    def decorate(function: Callable) -> Callable:
        signature = inspect.signature(function)
        module_name = getattr(function.__globals__.get("__spec__"), "name", None) or function.__module__
        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            inputs = {k: v for k, v in bound.arguments.items() if k in _SAFE_ARGUMENTS}
            label = f"Operação offline: {module_name}.{function.__name__}"
            parent = current_execution()
            with execution(parent.run_id if parent else new_run_id(), parent.query_id if parent else new_query_id(),
                           origin=parent.origin if parent else "offline"), log_details(tipo_pipeline="offline", modulo=module_name):
                context = current_execution()
                def invoke():
                    with default_logger.span(run_id=context.run_id, query_id=context.query_id,
                        stage=stage, input=inputs, extra={"funcao": function.__name__}) as span:
                        result = function(*args, **kwargs)
                        span.set_result(_summary(result))
                        if isinstance(result, int) and result != 0:
                            span.status = "erro"
                        return result
                if parent:
                    return invoke()
                with lifecycle(run_id=context.run_id, query_id=context.query_id, query=label,
                    start=Stage.PIPELINE_INICIO, end=Stage.PIPELINE_FIM,
                    extra={"tipo_pipeline": "offline", "modulo": module_name}) as flow:
                    result = invoke()
                    flow.output = _summary(result)
                    if isinstance(result, int) and result != 0:
                        flow.status = "erro"
                    elif any(e["extra"]["outcome"] in {"erro", "degradado"} for e in context.events):
                        flow.status = "degradado"
                    return result
        return wrapped
    return decorate


@contextmanager
def observar_lote_embedding(*, model: str, count: int, cache: bool = False,
                            base_id: str | None = None) -> Iterator[None]:
    context = current_execution()
    if context is None:
        raise RuntimeError("Lote de embedding deve pertencer a uma execução observada")
    with default_logger.span(run_id=context.run_id, query_id=context.query_id, stage=Stage.EMBEDDING_LOTE,
        input={"textos": count}, modelo=None if cache else model,
        extra={"cache_hit": cache, "modelo_configurado": model, "base_id": base_id}) as span:
        with coletar_chamadas() as calls:
            try:
                yield
            finally:
                if calls:
                    span.tokens, span.extra["uso_tokens"] = resumir_uso(calls)
                    span.extra["chamadas_modelo"] = calls
                    span.extra["model_called"] = True
                    span.modelo = calls[-1]["model"]
                else:
                    span.extra.update(model_called=False if cache else None,
                        uso_tokens={"status": "cache" if cache else "indisponivel",
                                    "fonte": "vetores_persistidos" if cache else "funcao_sem_usage"})
                span.set_result({"textos_solicitados": count})
