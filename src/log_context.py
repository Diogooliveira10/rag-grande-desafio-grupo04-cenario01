"""Per-request logging context and small, cached reproducibility fingerprints.

No network calls, credential values or whole-corpus hashing on the query path.
The index source hash is the manifest's declaration, not a freshness check.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import config


@dataclass
class ExecutionContext:
    run_id: str
    query_id: str
    origin: str
    sequence: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    logging_errors: int = 0

    def stages(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for event in self.events:
            result.setdefault(event["stage"], []).append({
                "status": event["status"],
                "attempted": event["extra"]["attempted"],
                "outcome": event["extra"]["outcome"],
                "latencia_ms": event["latencia_ms"],
                **{key: event["extra"][key] for key in
                   ("motivo", "fallback", "fallback_on_error", "error_type", "intencao", "tentativa_busca")
                   if key in event["extra"]},
            })
        return result

    def reported_tokens(self) -> dict[str, int]:
        return {key: sum(event["tokens"].get(key, 0) for event in self.events)
                for key in ("prompt", "completion", "total")}

    def token_coverage(self) -> dict[str, Any]:
        counts = {status: 0 for status in ("reportado", "parcial", "estimado", "indisponivel", "cache", "nao_aplicavel")}
        for event in self.events:
            status = (event["extra"].get("uso_tokens") or {}).get("status", "nao_aplicavel")
            counts[status] = counts.get(status, 0) + 1
        return {"eventos_por_status": counts, "escopo": "uso_disponivel; não é custo total faturado"}


def stage_outcome(status: str, extra: dict[str, Any]) -> str:
    if status in {"erro", "erro_llm", "saida_invalida"}:
        return "erro"
    if status in {"pulado", "degradado", "iniciado", "rejeitado"}:
        return status
    if status == "recusa" or extra.get("is_refusal"):
        return "recusa"
    return "ok"


_current: ContextVar[ExecutionContext | None] = ContextVar("log_execution", default=None)
_details: ContextVar[dict[str, Any]] = ContextVar("log_details", default={})
_origin: ContextVar[str] = ContextVar("log_origin", default="direto")


def current_execution() -> ExecutionContext | None:
    return _current.get()


def current_details() -> dict[str, Any]:
    return _details.get()


def current_origin() -> str:
    return _origin.get()


@contextmanager
def log_origin(origin: str) -> Iterator[None]:
    token = _origin.set(origin)
    try:
        yield
    finally:
        _origin.reset(token)


@contextmanager
def execution(run_id: str, query_id: str, origin: str | None = None) -> Iterator[ExecutionContext]:
    """Reuse the API context in its pipeline; unrelated nested runs stay isolated."""
    previous = _current.get()
    if previous and (previous.run_id, previous.query_id) == (run_id, query_id):
        yield previous
        return
    token = _current.set(ExecutionContext(run_id, query_id, origin or _origin.get()))
    details_token = _details.set({})
    try:
        yield _current.get()
    finally:
        _details.reset(details_token)
        _current.reset(token)


@contextmanager
def log_details(**details: Any) -> Iterator[None]:
    token = _details.set({**_details.get(), **details})
    try:
        yield
    finally:
        _details.reset(token)


def _signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
        return str(path), stat.st_mtime_ns, stat.st_size
    except OSError:
        return str(path), 0, 0


@lru_cache(maxsize=32)
def _small_json(signature: tuple[str, int, int]) -> tuple[str | None, dict[str, Any]]:
    path, _, size = signature
    if not size or size > 1024 * 1024:
        return None, {}
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw)
        return hashlib.sha256(raw).hexdigest(), value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return None, {}


@lru_cache(maxsize=4)
def _code_identity(root: str, signatures: tuple[tuple[str, int, int], ...],
                   git_signatures: tuple[tuple[str, int, int], ...]) -> dict[str, Any]:
    digest = hashlib.sha256()
    try:
        for name, _, _ in signatures:
            path = Path(name)
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
        code_hash = digest.hexdigest()
    except OSError:
        code_hash = None
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
            timeout=2, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = None
    return {"git_revision": revision, "code_sha256": code_hash}


def runtime_context() -> dict[str, Any]:
    """Allowlist only: never .env contents, database URLs, headers or API keys."""
    root = config.ROOT_DIR
    sources = sorted([root / "config.py", *root.glob("src/*.py"), *root.glob("app/*.py")])
    # Git reference signatures invalidate the cache even when source is unchanged.
    git_paths = [root / ".git" / "HEAD", root / ".git" / "packed-refs"]
    git_paths += list((root / ".git" / "refs" / "heads").rglob("*"))
    source_signatures = tuple(_signature(p) for p in sources)
    identity = _code_identity(str(root), source_signatures,
                              tuple(_signature(p) for p in git_paths if not p.is_dir()))
    index_hash, index = _small_json(_signature(config.INDEX_DIR / "manifest.json"))
    inventory_hash, _ = _small_json(_signature(root / "data" / "inventory" / "v6.json"))
    modes = {}
    for module, name, variable, default in (
        ("query_analysis", "analysis_mode", "ANALYSIS_MODE", "regras"),
        ("conversation", "conversation_mode", "CONVERSATION_MODE", "llm"),
        ("reformulacao", "reformulation_mode", "REFORMULACAO_MODE", "llm"),
    ):
        modes[name] = getattr(sys.modules.get(f"src.{module}"), variable,
                              os.getenv("QUERY_ANALYSIS_MODE" if variable == "ANALYSIS_MODE" else variable, default))
    return {
        **identity,
        "index": {
            "manifest_sha256": index_hash, "built_at": index.get("built_at"),
            "source_sha256": index.get("source_sha256"),
            "embedding_model": index.get("embedding_model"),
            "bases": [{key: base.get(key) for key in
                       ("base_id", "total_chunks", "dimension", "inventory_versions", "inventory_hashes")}
                      for base in (index.get("bases") or []) if isinstance(base, dict)],
        },
        "inventory_sha256": inventory_hash,
        "config": {
            **modes, "llm_provider_mode": config.LLM_PROVIDER_MODE,
            "generation_model": config.GENERATION_MODEL,
            "embedding_model": config.EMBEDDING_MODEL,
            "fallback_model": config.OPENROUTER_MODEL_FALLBACK,
            "rerank_enabled": config.RERANK_ENABLED,
            "rerank_provider": config.RERANK_PROVIDER, "rerank_model": config.RERANK_MODEL,
            "rerank_candidates": config.RERANK_CANDIDATES,
            "generation_context_chars": config.GENERATION_CONTEXT_CHARS,
            "generation_chunk_chars": config.GENERATION_CHUNK_CHARS,
            "generation_timeout": config.GENERATION_TIMEOUT,
            "generation_max_retries": config.GENERATION_MAX_RETRIES,
            "model_min_interval_seconds": config.MODEL_MIN_INTERVAL_SECONDS,
        },
    }
