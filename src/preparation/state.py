"""Estado incremental da preparação: fingerprints, cache de unidades e chunks, arquivos de entrada e escrita atômica."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from src import ocr
from src.preparation.common import CHUNKING_SCHEMA_VERSION, EXTRACTION_SCHEMA_VERSION, PROJECT_ROOT, SUPPORTED_SUFFIXES, SourceUnit


def _manifest_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _extraction_fingerprint(base: dict[str, Any], *, ocr_motor: str | None = None) -> str:
    return _fingerprint(
        {
            "schema": EXTRACTION_SCHEMA_VERSION,
            "base_id": base.get("base_id"),
            "natureza": base.get("natureza"),
            "sensitivity_default": base.get("sensitivity_default"),
            "campo_vigencia": base.get("campo_vigencia"),
            "metadados_extras": base.get("metadados_extras"),
            **({"synthetic_conversation_schema": 1} if base.get("dados_sinteticos") else {}),
            "chunking_sugerido": base.get("chunking_sugerido"),
            **({"json_resultado_records": True} if base.get("natureza") == "agregada" else {}),
            # Instalar ou trocar o OCR muda o texto extraído das páginas escaneadas.
            "ocr": ocr.motor() if ocr_motor is None else ocr_motor,
        }
    )


def _chunking_fingerprint(unit: SourceUnit, chunk_size: int, overlap: int) -> str:
    return _fingerprint(
        {
            "schema": CHUNKING_SCHEMA_VERSION,
            "document_id": unit.document_id,
            "document_content_sha256": hashlib.sha256(unit.text.encode("utf-8")).hexdigest(),
            "chunk_size": chunk_size,
            "overlap": overlap,
            "chunking_sugerido": unit.chunking_sugerido,
            "natureza": unit.natureza,
            "formato": unit.formato,
            # Invalidate only the section caches affected by the page-number fix.
            **({"numeric_page_preamble": 1} if re.match(
                r"^\s*\d+\s*\n", unit.text,
            ) else {}),
        }
    )


def _unit_to_record(unit: SourceUnit) -> dict[str, Any]:
    return {
        "source_file": unit.source_file,
        "logical_id": unit.logical_id,
        "document_id": unit.document_id,
        "base_id": unit.base_id,
        "natureza": unit.natureza,
        "doc_type": unit.doc_type,
        "sensitivity": unit.sensitivity,
        "origem": unit.origem,
        "manifest_hash": unit.manifest_hash,
        "bytes": unit.bytes,
        "data_vigencia": unit.data_vigencia,
        "chunking_sugerido": unit.chunking_sugerido,
        "formato": unit.formato,
        "sha256": unit.sha256,
        "text": unit.text,
        "status": unit.status,
        "extras": unit.extras,
        "extraction_fingerprint": unit.extraction_fingerprint,
    }


def _unit_from_record(record: dict[str, Any]) -> SourceUnit:
    return SourceUnit(
        source_file=str(record["source_file"]),
        logical_id=str(record["logical_id"]),
        document_id=str(record["document_id"]),
        base_id=str(record["base_id"]),
        natureza=str(record["natureza"]),
        doc_type=str(record["doc_type"]),
        sensitivity=str(record["sensitivity"]),
        origem=str(record.get("origem") or ""),
        manifest_hash=str(record.get("manifest_hash") or ""),
        bytes=int(record.get("bytes", 0) or 0),
        data_vigencia=(
            str(record["data_vigencia"])
            if record.get("data_vigencia") not in (None, "")
            else None
        ),
        chunking_sugerido=str(record.get("chunking_sugerido") or "chunking_generico"),
        formato=str(record["formato"]),
        sha256=str(record["sha256"]),
        text=str(record.get("text") or ""),
        status=str(record.get("status") or "ok"),
        extras=dict(record.get("extras") or {}),
        extraction_fingerprint=str(record.get("extraction_fingerprint") or ""),
    )


def _extraction_state_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.units{output_path.suffix}")


def _load_units_state(path: Path) -> tuple[dict[tuple[str, str, str], list[SourceUnit]], bool]:
    if not path.exists():
        return {}, False
    index: dict[tuple[str, str, str], list[SourceUnit]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            unit = _unit_from_record(json.loads(line))
            key = (unit.source_file, unit.sha256, unit.extraction_fingerprint)
            index.setdefault(key, []).append(unit)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return {}, True
    return index, False


def _load_chunk_state(
    path: Path,
) -> tuple[dict[tuple[str, str, str], list[dict[str, Any]]], bool]:
    if not path.exists():
        return {}, False
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if not {
                "document_id",
                "document_content_sha256",
                "chunking_fingerprint",
            }.issubset(record):
                continue
            key = (
                str(record["document_id"]),
                str(record["document_content_sha256"]),
                str(record["chunking_fingerprint"]),
            )
            index.setdefault(key, []).append(record)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return {}, True
    for records in index.values():
        records.sort(key=lambda record: int(record.get("chunk_index", 0) or 0))
    return index, False


def _input_files(input_dir: Path) -> tuple[list[Path], int]:
    paths = sorted(path for path in input_dir.rglob("*") if path.is_file())
    supported = [path for path in paths if path.suffix.lower() in SUPPORTED_SUFFIXES]
    return supported, len(paths) - len(supported)


def _input_files_from_inventory(
    input_dir: Path,
    inventory_path: Path,
    base_id: str,
) -> tuple[list[Path], int]:
    """Resolve somente arquivos promovidos e listados no inventário."""
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    records = payload.get("arquivos", [])
    if not isinstance(records, list):
        raise ValueError("inventário precisa conter uma lista em 'arquivos'")

    selected: list[Path] = []
    ignored = 0
    seen: set[Path] = set()
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "ok":
            continue
        if str(record.get("base_id") or "") != base_id:
            continue
        raw_path = record.get("source_file") or record.get("path")
        if not raw_path:
            raise ValueError(f"registro do inventário sem source_file: {record.get('logical_id')}")
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path = path.resolve()
        try:
            path.relative_to(input_dir.resolve())
        except ValueError as error:
            raise ValueError(
                f"arquivo do inventário fora do diretório de entrada: {path}"
            ) from error
        if not path.is_file():
            raise FileNotFoundError(f"arquivo do inventário não encontrado: {path}")
        if path in seen:
            continue
        seen.add(path)
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            selected.append(path)
        else:
            ignored += 1
    return sorted(selected), ignored


def _atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    partial.replace(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)
