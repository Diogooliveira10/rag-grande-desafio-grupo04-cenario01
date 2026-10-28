"""Cria embeddings a partir do JSONL preparado, com cache por conteúdo.

O módulo não conhece a coleta nem extrai PDF. Ele consome a saída de
``prepare_documents.py`` e delega as chamadas ao cliente compartilhado em
``llm_ecoa.py``. O cache preserva ``chunk_id``, proveniência e metadados para
que o índice vetorial possa ser construído depois sem repetir requisições.
Os vetores também são gravados em partes atômicas e acompanhados de um
relatório, permitindo retomar a execução e inspecionar o processamento.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.obs import Stage
from src.offline_observation import observar_offline, observar_lote_embedding

import config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "normalized" / "documents.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "embeddings" / "embeddings.jsonl"
DEFAULT_MAX_PART_BYTES = 45 * 1024 * 1024
EMBEDDING_CACHE_SCHEMA = 4
EmbeddingBatch = Callable[[list[str]], list[list[float]]]


def _content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        parent_digest = hashlib.sha256(str(path.resolve().parent).encode("utf-8")).hexdigest()[:12]
        return f"external/{parent_digest}/{path.name}"


def _embedding_input_sha256(chunk: dict[str, Any]) -> str:
    """Identifica texto e proveniência que justificam um novo embedding."""
    payload = {
        "text": str(chunk.get("text") or ""),
        "base_id": chunk.get("base_id"),
        "natureza": chunk.get("natureza"),
        "doc_type": chunk.get("doc_type"),
        "chunking_strategy": chunk.get("chunking_strategy"),
        "origem": chunk.get("origem"),
        "manifest_hash": chunk.get("manifest_hash"),
        "data_vigencia": chunk.get("data_vigencia"),
        "sensitivity": chunk.get("sensitivity"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _content_sha256(encoded)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"JSONL não encontrado: {path}")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict) or not str(payload.get("text") or "").strip():
            raise ValueError(f"registro sem texto válido na linha {line_number}: {path}")
        if not payload.get("chunk_id"):
            raise ValueError(f"registro sem chunk_id na linha {line_number}: {path}")
        if "metadata" in payload or "source_path" in payload:
            raise ValueError(
                f"registro legado na linha {line_number}: remova metadata/source_path "
                "e gere novamente com src.prepare_documents"
            )
        records.append(payload)
    return records


def _read_cache(path: Path) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    cache_paths = [path] if path.exists() else []
    cache_paths.extend(sorted(path.parent.glob(f"{path.stem}.part-*{path.suffix}")))
    for cache_path in cache_paths:
        for line_number, line in enumerate(
            cache_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(
                    f"registro inválido no cache, linha {line_number}: {cache_path}"
                )
            digest = str(
                record.get("chunk_content_sha256")
                or record.get("content_sha256")
                or ""
            )
            vector = record.get("embedding")
            if digest and isinstance(vector, list) and vector:
                cache[digest] = record
    return cache


def _write_cache(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    partial.replace(path)


def _next_part_number(path: Path) -> int:
    numbers = []
    for candidate in path.parent.glob(f"{path.stem}.part-*{path.suffix}"):
        marker = candidate.name.removeprefix(f"{path.stem}.part-").removesuffix(path.suffix)
        if marker.isdigit():
            numbers.append(int(marker))
    return max(numbers, default=0) + 1


def _remove_part_files(path: Path) -> None:
    for candidate in path.parent.glob(f"{path.stem}.part-*{path.suffix}"):
        candidate.unlink()


def _write_part_records(
    output_path: Path,
    records: list[dict[str, Any]],
    *,
    first_part_number: int,
    max_part_bytes: int,
) -> list[Path]:
    """Persiste um lote em partes atômicas para facilitar retomada e inspeção."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    current: list[str] = []
    current_size = 0
    part_number = first_part_number

    def flush() -> None:
        nonlocal current, current_size, part_number
        if not current:
            return
        part_path = output_path.with_name(
            f"{output_path.stem}.part-{part_number:04d}{output_path.suffix}"
        )
        partial = part_path.with_suffix(part_path.suffix + ".partial")
        partial.write_text("".join(current), encoding="utf-8", newline="\n")
        partial.replace(part_path)
        written.append(part_path)
        part_number += 1
        current = []
        current_size = 0

    for record in records:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        line_size = len(line.encode("utf-8"))
        if current and current_size + line_size > max_part_bytes:
            flush()
        current.append(line)
        current_size += line_size
    flush()
    return written


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    partial.replace(path)


def _remote_embed_batch(texts: list[str]) -> list[list[float]]:
    from llm_ecoa import gerar_embeddings

    return gerar_embeddings(texts)


def _provider_defaults(provider: str) -> tuple[str, str, str]:
    """Fonte única dos presets de provedor: ``config.embedding_endpoint``.

    Aqui e em ``src/retrieve.py`` (embeddings de consulta) precisam resolver
    endpoint/chave/modelo do mesmo jeito, então a tabela vive em ``config``.
    """
    return config.embedding_endpoint(provider)


def _normalize_endpoint(endpoint: str) -> str:
    return endpoint.rstrip("/")


def _configured_embed_batch(
    provider: str,
    endpoint: str,
    api_key: str,
    model: str,
) -> EmbeddingBatch:
    def embed(texts: list[str]) -> list[list[float]]:
        from llm_ecoa import gerar_embeddings

        return gerar_embeddings(
            texts,
            base_url=endpoint,
            api_key=api_key,
            model=model,
        )

    return embed


def _materialize_record(
    chunk: dict[str, Any],
    cached: dict[str, Any],
    digest: str,
) -> dict[str, Any]:
    """Combina o vetor reutilizado com toda a proveniência do chunk atual."""
    record = dict(cached)
    record.update({key: value for key, value in chunk.items() if key != "text"})
    record.pop("content_sha256", None)
    record["chunk_content_sha256"] = digest
    record["embedding_input_sha256"] = _embedding_input_sha256(chunk)
    record["embedding_cache_schema"] = EMBEDDING_CACHE_SCHEMA
    return record


@observar_offline(Stage.EMBEDDINGS)
def create_embeddings(
    input_path: Path,
    output_path: Path,
    *,
    batch_size: int = 64,
    pause_seconds: float = 10.0,
    max_part_bytes: int = DEFAULT_MAX_PART_BYTES,
    report_path: Path | None = None,
    provider: str = "ecoa",
    endpoint: str | None = None,
    model: str | None = None,
    embed_batch: EmbeddingBatch | None = None,
    dry_run: bool = False,
    encoding_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size deve ser positivo")
    if pause_seconds < 0:
        raise ValueError("pause_seconds não pode ser negativo")
    if max_part_bytes < 1:
        raise ValueError("max_part_bytes deve ser positivo")
    if provider == "shared":
        provider = "ecoa"
    default_endpoint, api_key, default_model = _provider_defaults(provider)
    endpoint = _normalize_endpoint(endpoint or default_endpoint)
    model = model or default_model
    if not endpoint:
        raise ValueError("configure o endpoint no .env ou informe --endpoint")

    chunks = _read_jsonl(input_path)
    cache = _read_cache(output_path)
    by_digest: dict[str, dict[str, Any]] = dict(cache)
    pending, reutilizados, embedding_dimension = _reusable_or_pending(
        chunks, by_digest, provider=provider, endpoint=endpoint, model=model, encoding_config=encoding_config
    )

    summary: dict[str, int | str | bool] = {
        "chunks": len(chunks),
        "reutilizados": reutilizados,
        "pendentes": len(pending),
        "lotes": 0,
        "gerados": 0,
        "partes_geradas": 0,
        "dimensao": 0,
        "modelo": model,
        "provedor": provider,
        "endpoint": endpoint,
        "dry_run": dry_run,
        "encoding_config": encoding_config,
    }
    summary["dimensao"] = embedding_dimension or 0
    report_path = report_path or output_path.with_name("embedding_report.json")
    run = {
        "input": _display_path(input_path),
        "output": _display_path(output_path),
        "model": model,
        "provider": provider,
        "endpoint": endpoint,
        "batch_size": batch_size,
        "pause_seconds": pause_seconds,
        "max_part_bytes": max_part_bytes,
    }
    if not dry_run and not pending:
        _materialize_cache(output_path, chunks, by_digest)
    if dry_run or not pending:
        _write_run_report(report_path, output_path, run, "dry_run" if dry_run else "complete", summary)
        return summary

    embedder = embed_batch or _configured_embed_batch(provider, endpoint, api_key, model)
    _embed_pending(
        pending, by_digest, summary, embedder, output_path,
        embedding_dimension=embedding_dimension, batch_size=batch_size, pause_seconds=pause_seconds,
        max_part_bytes=max_part_bytes, provider=provider, endpoint=endpoint, model=model,
        encoding_config=encoding_config,
    )

    # Cada lote já está persistido atomicamente em partes para retomada.
    # Materializar o corpus completo uma vez evita reescrever gigabytes a cada texto.
    _materialize_cache(output_path, chunks, by_digest)
    _write_run_report(report_path, output_path, run, "complete", summary)
    return summary


def _reusable_or_pending(
    chunks: list[dict[str, Any]],
    by_digest: dict[str, dict[str, Any]],
    *,
    provider: str,
    endpoint: str,
    model: str,
    encoding_config: dict[str, Any] | None,
) -> tuple[dict[str, tuple[str, str]], int, int | None]:
    """(pendentes por conteúdo, quantos o cache reaproveita, dimensão do cache).

    Só reaproveita o vetor gerado com o mesmo esquema, entrada, encoding,
    provedor, endpoint e modelo; vetor inválido no cache interrompe.
    """
    pending: dict[str, tuple[str, str]] = {}
    reutilizados = 0
    embedding_dimension: int | None = None
    for chunk in chunks:
        digest = _content_sha256(str(chunk["text"]))
        input_digest = _embedding_input_sha256(chunk)
        cached = by_digest.get(digest)
        if (
            cached
            and cached.get("embedding_cache_schema") == EMBEDDING_CACHE_SCHEMA
            and (cached.get("embedding_input_sha256") == input_digest or (
                encoding_config is not None and cached.get("encoding_config") == encoding_config
            ))
            and cached.get("encoding_config") == encoding_config
            and cached.get("embedding_provider") == provider
            and cached.get("embedding_endpoint") == endpoint
            and cached.get("embedding_model") == model
        ):
            cached_vector = cached.get("embedding")
            if not isinstance(cached_vector, list) or not cached_vector:
                pending.setdefault(digest, (str(chunk["text"]), input_digest))
                continue
            cached_dimension = len(cached_vector)
            if not all(math.isfinite(value) for value in cached_vector) or not any(cached_vector):
                raise RuntimeError("cache contém vetor não finito ou norma nula")
            if embedding_dimension is None:
                embedding_dimension = cached_dimension
            elif cached_dimension != embedding_dimension:
                raise RuntimeError("cache contém embeddings com dimensões incompatíveis")
            reutilizados += 1
        else:
            pending.setdefault(digest, (str(chunk["text"]), input_digest))
    return pending, reutilizados, embedding_dimension


def _embed_pending(
    pending: dict[str, tuple[str, str]],
    by_digest: dict[str, dict[str, Any]],
    summary: dict[str, int | str | bool],
    embedder: EmbeddingBatch,
    output_path: Path,
    *,
    embedding_dimension: int | None,
    batch_size: int,
    pause_seconds: float,
    max_part_bytes: int,
    provider: str,
    endpoint: str,
    model: str,
    encoding_config: dict[str, Any] | None,
) -> None:
    """Embeda os pendentes em lotes, gravando cada lote em partes para retomada e atualizando o resumo."""
    pending_items = list(pending.items())
    next_part_number = _next_part_number(output_path)
    generated_parts: list[Path] = []
    for start in range(0, len(pending_items), batch_size):
        batch = pending_items[start : start + batch_size]
        with observar_lote_embedding(model=model, count=len(batch)):
            vectors = embedder([text for _, (text, _) in batch])
        if len(vectors) != len(batch):
            raise RuntimeError("o endpoint retornou quantidade incorreta de embeddings")
        for (digest, (_, input_digest)), vector in zip(batch, vectors):
            if not isinstance(vector, list) or not vector:
                raise RuntimeError(f"embedding vazio para o conteúdo {digest[:12]}")
            if not all(math.isfinite(value) for value in vector) or not any(vector):
                raise RuntimeError(f"embedding não finito ou norma nula para {digest[:12]}")
            by_digest[digest] = {
                "chunk_content_sha256": digest,
                "embedding_input_sha256": input_digest,
                "embedding_cache_schema": EMBEDDING_CACHE_SCHEMA,
                "embedding_provider": provider,
                "embedding_endpoint": endpoint,
                "embedding_model": model,
                "encoding_config": encoding_config,
                "embedding": vector,
            }
        for vector in vectors:
            vector_dimension = len(vector)
            if embedding_dimension is None:
                embedding_dimension = vector_dimension
            elif vector_dimension != embedding_dimension:
                raise RuntimeError(
                    "o endpoint retornou embeddings com dimensões incompatíveis"
                )
        summary["dimensao"] = embedding_dimension or 0
        summary["lotes"] = int(summary["lotes"]) + 1
        summary["gerados"] = int(summary["gerados"]) + len(batch)
        generated_records = [by_digest[digest] for digest, _ in batch]
        parts = _write_part_records(
            output_path,
            generated_records,
            first_part_number=next_part_number,
            max_part_bytes=max_part_bytes,
        )
        generated_parts.extend(parts)
        next_part_number += len(parts)
        summary["partes_geradas"] = len(generated_parts)
        if start + batch_size < len(pending_items) and pause_seconds:
            time.sleep(pause_seconds)


def _materialize_cache(output_path: Path, chunks: list[dict[str, Any]], by_digest: dict[str, dict[str, Any]]) -> None:
    """Cache completo na ordem dos chunks, no lugar das partes da retomada."""
    ordered = [
        _materialize_record(
            chunk,
            by_digest[_content_sha256(str(chunk["text"]))],
            _content_sha256(str(chunk["text"])),
        )
        for chunk in chunks
    ]
    _write_cache(output_path, ordered)
    _remove_part_files(output_path)


def _write_run_report(
    report_path: Path, output_path: Path, run: dict[str, Any], status: str, summary: dict[str, int | str | bool]
) -> None:
    _write_report(
        report_path,
        {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **run,
            "status": status,
            **summary,
            "cache_parts": [
                _display_path(path)
                for path in sorted(
                    output_path.parent.glob(
                        f"{output_path.stem}.part-*{output_path.suffix}"
                    )
                )
            ],
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--pause-seg", type=float, default=10.0)
    parser.add_argument("--max-part-mb", type=float, default=45.0)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--provider",
        choices=("ecoa", "ollama", "openai", "custom"),
        default="ecoa",
        help="preset de API OpenAI-compatible para embeddings",
    )
    parser.add_argument("--endpoint", help="sobrescreve o endpoint do provedor")
    parser.add_argument("--model", help="sobrescreve o modelo de embeddings")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--encoding-config", help="JSON da configuração de codificação e checkpoint")
    args = parser.parse_args(argv)
    try:
        summary = create_embeddings(
            args.input.resolve(),
            args.output.resolve(),
            batch_size=args.batch_size,
            pause_seconds=args.pause_seg,
            max_part_bytes=int(args.max_part_mb * 1024 * 1024),
            report_path=args.report.resolve() if args.report else None,
            provider=args.provider,
            endpoint=args.endpoint,
            model=args.model,
            dry_run=args.dry_run,
            encoding_config=json.loads(args.encoding_config) if args.encoding_config else None,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
