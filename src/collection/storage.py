"""Armazenamento por conteúdo: objetos por SHA-256, bytes de uma versão e os registros de cada item.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any


from src.collection import paths as coleta
from src.collection.checkpoint import _checkpoint_write
from src.collection.paths import _object_path, _path_from_record, _relative_path
from src.collection.rest import _safe_component
from src.collection.selection import _VersionContext
from src.collection.sources import _file_sha256, _materialize_object


def _wait_rate_limit(context: _VersionContext, base: dict[str, Any]) -> None:
    try:
        delay = max(0.0, float((base.get("acesso") or {}).get("rate_limit_seg", 0) or 0))
    except (TypeError, ValueError):
        delay = 0.0
    base_id = str(base["base_id"])
    elapsed = time.monotonic() - context.last_request_at.get(base_id, 0.0)
    if delay and elapsed < delay:
        time.sleep(delay - elapsed)
    context.last_request_at[base_id] = time.monotonic()


def _ensure_object(body: bytes, digest: str) -> Path:
    object_path = _object_path(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    if object_path.exists():
        if _file_sha256(object_path) != digest:
            raise RuntimeError(f"objeto SHA-256 corrompido: {object_path}")
        return object_path
    temporary = object_path.with_name(f".{object_path.name}.tmp")
    temporary.write_bytes(body)
    try:
        if object_path.exists():
            temporary.unlink(missing_ok=True)
        else:
            temporary.replace(object_path)
    finally:
        temporary.unlink(missing_ok=True)
    return object_path


def _save_version_bytes(context: _VersionContext, body: bytes, base_id: str, filename_prefix: str, extension: str) -> dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    object_path = _ensure_object(body, digest)
    destination_dir = context.staging_path / _safe_component(base_id)
    suffix = extension if extension.startswith(".") else f".{extension}"
    safe_prefix = _safe_component(filename_prefix)
    max_path_length = 259 if os.name == "nt" else 4096
    available_component_length = max_path_length - len(str(destination_dir)) - 1 - 5
    max_component_length = min(120, max(1, available_component_length))
    prefix_limit = max(1, max_component_length - len(digest[:12]) - len(suffix) - 1)
    safe_prefix = safe_prefix[:prefix_limit].rstrip("._") or "item"
    path = destination_dir / f"{safe_prefix}_{digest[:12]}{suffix}"
    storage_mode = _materialize_object(object_path, path, digest)
    relative = _relative_path(path)
    context.hash_index[digest] = relative
    return {
        "status": "ok",
        "bytes": len(body),
        "sha256": digest,
        "path": relative,
        "storage": {
            "mode": storage_mode,
            "object_path": _relative_path(object_path),
            "object_sha256": digest,
        },
    }


def _materialize_previous_record(
    context: _VersionContext,
    base_id: str,
    filename_prefix: str,
    previous: dict[str, Any],
    extension: str,
) -> dict[str, Any] | None:
    digest = str(previous.get("sha256") or "")
    raw_storage = previous.get("storage")
    storage: dict[str, Any] = raw_storage if isinstance(raw_storage, dict) else {}
    object_path = coleta.PROJECT_ROOT / str(storage.get("object_path", "")) if storage.get("object_path") else None
    source_path = object_path if object_path and object_path.exists() else _path_from_record(previous)
    if source_path is None or not source_path.exists():
        return None
    if not digest:
        digest = _file_sha256(source_path)
    elif _file_sha256(source_path) != digest:
        raise RuntimeError(f"registro anterior não corresponde ao SHA-256: {source_path}")
    object_path = _ensure_object(source_path.read_bytes(), digest)
    suffix = extension if extension.startswith(".") else f".{extension}"
    destination = context.staging_path / _safe_component(base_id) / f"{_safe_component(filename_prefix)}_{digest[:12]}{suffix}"
    storage_mode = _materialize_object(object_path, destination, digest)
    return {
        "status": "ok",
        "bytes": destination.stat().st_size,
        "sha256": digest,
        "path": _relative_path(destination),
        "storage": {
            "mode": storage_mode,
            "object_path": _relative_path(object_path),
            "object_sha256": digest,
        },
        "reutilizado_de": f"v{context.previous_version}" if context.previous_version else previous.get("path"),
        "reuso_motivo": "resposta HTTP 304" if previous.get("etag") or previous.get("last_modified") else "conteúdo anterior",
    }


def _base_record(context: _VersionContext, base: dict[str, Any], logical_id: str, origin: str, result: dict[str, Any], *, tipo: str, registros: int = 0) -> dict[str, Any]:
    path = result.get("path")
    record = {
        "logical_id": logical_id,
        "base_id": base["base_id"],
        "escopo": base.get("escopo", "experimental"),
        "natureza": base.get("natureza"),
        "tipo": tipo,
        "origem": origin,
        "coletado_em": context.collected_at,
        "manifest_hash": context.manifest_hash,
        "registros": registros,
        "source_file": path,
        "size_bytes": int(result.get("bytes", 0) or 0),
        **result,
    }
    context.items[logical_id] = record
    _checkpoint_write(context)
    return record


def _completed_item(context: _VersionContext, logical_id: str) -> dict[str, Any] | None:
    record = context.items.get(logical_id)
    return record if record and record.get("status") == "ok" and _path_from_record(record) else None


def _failed_record(
    context: _VersionContext,
    base: dict[str, Any],
    logical_id: str,
    origin: str,
    error: str,
    tipo: str = "desconhecido",
    *,
    status: str = "falhou",
) -> dict[str, Any]:
    return _base_record(
        context,
        base,
        logical_id,
        origin,
        {"status": status, "bytes": 0, "sha256": None, "path": None, "erro": error},
        tipo=tipo,
    )
