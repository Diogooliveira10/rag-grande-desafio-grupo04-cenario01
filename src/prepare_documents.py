"""Prepara documentos para o estágio de embeddings.

O módulo mantém a coleta separada da ingestão: lê uma base já materializada
ou uma fixture local, extrai texto por formato, preserva a proveniência e
grava chunks JSONL com um contrato canônico de metadados em nível único.

Exemplo rápido, sem acessar as bases remotas:

    py -m src.prepare_documents `
      --input tests/fixtures/pdf `
      --manifest corpus_manifest.yaml `
      --base-id exemplos_teste `
      --output data/attempts/corpus_fetch/demo/normalized/examples.jsonl

Para dados extraídos de uma coleta já inventariada, acrescente
``--inventory data/inventory/v1.json`` para recuperar a origem e o horário
real de coleta de cada arquivo pelo SHA-256.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator

from src.fetch_corpus import load_manifest
from src.ingest import chunk_text, sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "raw" / "v1"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "normalized" / "documents.jsonl"
DEFAULT_REPORT = PROJECT_ROOT / "data" / "normalized" / "extraction_report.json"
TEXT_SUFFIXES = {".html", ".htm", ".aspx", ".txt", ".md", ".csv"}
STRUCTURED_SUFFIXES = {".json", ".jsonl"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | STRUCTURED_SUFFIXES | {".pdf"}
_CANONICAL_UNIT_FIELDS = {
    "source_file",
    "arquivo_bruto",
    "nome_arquivo",
    "bytes",
    "coletado_em",
    "manifest_fonte",
    "manifest_hash",
    "origem",
    "acesso_tipo",
    "sha256",
    "logical_id",
    "data_vigencia",
}


class _VisibleTextParser(HTMLParser):
    """Extrai texto visível de HTML sem introduzir outra dependência."""

    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned:
                self.parts.append(cleaned)

    def text(self) -> str:
        return "\n".join(self.parts)


@dataclass(frozen=True)
class SourceUnit:
    """Unidade de texto antes do chunking: arquivo, página ou registro."""

    source_file: str
    logical_id: str
    document_id: str
    base_id: str
    natureza: str
    doc_type: str
    sensitivity: str
    origem: str
    manifest_hash: str
    bytes: int
    data_vigencia: str | None
    chunking_sugerido: str
    formato: str
    sha256: str
    text: str
    status: str
    extras: dict[str, Any]


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        parent_digest = hashlib.sha256(str(path.resolve().parent).encode("utf-8")).hexdigest()[:12]
        return f"external/{parent_digest}/{path.name}"


def _manifest_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _visible_html(text: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(text)
    return parser.text()


def _json_records(payload: Any) -> list[tuple[int | None, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return list(enumerate(payload["data"], start=1))
    if isinstance(payload, list):
        return list(enumerate(payload, start=1))
    return [(None, payload)]


def _csv_records(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    for record_number, record in enumerate(reader, start=1):
        if any(value not in (None, "") for value in record.values()):
            yield record_number, {str(key): str(value or "") for key, value in record.items() if key}


def _base_origins(base: dict[str, Any]) -> list[str]:
    access = base.get("acesso") or {}
    origins: list[str] = []
    for key in ("origem", "url", "endpoint"):
        value = access.get(key)
        if value:
            origins.append(str(value))
    urls = access.get("urls")
    if isinstance(urls, list):
        origins.extend(str(url) for url in urls if url)
    endpoints = access.get("endpoints")
    if isinstance(endpoints, dict):
        origins.extend(str(url) for url in endpoints.values() if url)
    if not origins and base.get("fonte"):
        origins.append(str(base["fonte"]))
    return list(dict.fromkeys(origins))


def _base_origin(base: dict[str, Any]) -> str:
    origins = _base_origins(base)
    return origins[0] if len(origins) == 1 else ""


def _declared_record_metadata(record: Any, base: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    declared = base.get("metadados_extras") or []
    fields = [str(field) for field in declared]
    validity_field = base.get("campo_vigencia")
    if validity_field:
        fields.append(str(validity_field))
    extracted = {
        field: record[field]
        for field in dict.fromkeys(fields)
        if field in record and record[field] not in (None, "")
    }
    if validity_field and str(validity_field) in extracted:
        extracted["data_vigencia"] = _normalize_date_value(extracted[str(validity_field)])
    return extracted


def _normalize_date_value(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return text


def _record_logical_id(
    base_id: str,
    source_file: str,
    record: Any,
    fallback: str,
) -> str:
    if isinstance(record, dict):
        for field in (
            "id",
            "idCompra",
            "idContratacao",
            "external_id",
            "source_id",
            "numeroControlePNCP",
            "numeroControlePncpCompra",
            "numeroControlePncpAta",
            "sequencialContrato",
            "numeroContratoEmpenho",
            "ID Licitação",
            "ID",
            "idLicitacao",
            "numeroProcesso",
            "numero",
            "sequencial",
        ):
            value = record.get(field)
            if value not in (None, ""):
                return f"{base_id}:{field}:{value}"
    return f"{base_id}:{source_file}:{fallback}"


def _extras_for_unit(unit: SourceUnit) -> dict[str, Any]:
    return {
        key: value
        for key, value in unit.extras.items()
        if key not in _CANONICAL_UNIT_FIELDS
    }


def _base_for(manifest: dict[str, Any], input_dir: Path, base_id: str | None) -> dict[str, Any]:
    bases = manifest.get("bases", [])
    by_id = {str(base.get("base_id")): base for base in bases}
    if base_id:
        if base_id not in by_id:
            raise ValueError(f"base_id não declarado no manifesto: {base_id}")
        return by_id[base_id]

    resolved_input = input_dir.resolve()
    candidates: list[dict[str, Any]] = []
    for base in bases:
        access = base.get("acesso") or {}
        origin = access.get("origem") or base.get("local")
        if origin and (PROJECT_ROOT / str(origin)).resolve() == resolved_input:
            candidates.append(base)
        elif input_dir.name == str(base.get("base_id")):
            candidates.append(base)
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("informe --base-id quando a pasta não identifica uma única base")


def _load_inventory_provenance(path: Path | None) -> dict[tuple[str, str], dict[str, str]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("arquivos", [])
    if not isinstance(records, list):
        raise ValueError("inventário precisa conter uma lista em 'arquivos'")

    provenance: dict[tuple[str, str], dict[str, str]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        base_id = str(record.get("base_id") or "")
        digest = str(record.get("sha256") or "")
        if not base_id or not digest:
            continue
        entry = {
            "origem": str(record.get("origem") or ""),
            "coletado_em": str(record.get("coletado_em") or ""),
        }
        key = (base_id, digest)
        previous = provenance.get(key)
        if previous is None:
            provenance[key] = entry
            continue
        if previous["origem"] != entry["origem"]:
            previous["origem"] = ""
        if previous["coletado_em"] != entry["coletado_em"]:
            previous["coletado_em"] = ""
    return provenance


def _embedded_origin(path: Path) -> str:
    if path.suffix.lower() not in {".html", ".htm", ".aspx"}:
        return ""
    text = _read_text(path)
    patterns = (
        r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)',
        r'<link\b[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']',
        r'<meta\b[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)',
        r'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:url["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _file_provenance(
    base: dict[str, Any],
    path: Path,
    digest: str,
    inventory_provenance: dict[tuple[str, str], dict[str, str]],
    fallback_collected_at: str,
) -> tuple[str, str]:
    entry = inventory_provenance.get((str(base["base_id"]), digest), {})
    origin = entry.get("origem") or _embedded_origin(path) or _base_origin(base)
    collected_at = entry.get("coletado_em") or fallback_collected_at
    return origin, collected_at


def _unit_common(
    path: Path,
    base: dict[str, Any],
    digest: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
) -> dict[str, Any]:
    source_file = _relative_path(path)
    return {
        "source_file": source_file,
        "arquivo_bruto": source_file,
        "nome_arquivo": path.name,
        "bytes": path.stat().st_size,
        "coletado_em": collected_at,
        "manifest_fonte": base.get("fonte"),
        "manifest_hash": manifest_hash,
        "origem": origin,
        "acesso_tipo": (base.get("acesso") or {}).get("tipo"),
        "sha256": digest,
    }


def _make_unit(
    path: Path,
    base: dict[str, Any],
    digest: str,
    text: str,
    document_id: str,
    formato: str,
    status: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
    extras: dict[str, Any] | None = None,
) -> SourceUnit:
    common = _unit_common(path, base, digest, manifest_hash, collected_at, origin)
    if extras:
        common.update(extras)
    natureza = str(base["natureza"])
    logical_id = str(common.get("logical_id") or f"{base['base_id']}:{common['source_file']}")
    data_vigencia = common.get("data_vigencia")
    return SourceUnit(
        source_file=common["source_file"],
        logical_id=logical_id,
        document_id=document_id,
        base_id=str(base["base_id"]),
        natureza=natureza,
        doc_type=formato,
        sensitivity=str(base.get("sensitivity_default") or "publico"),
        origem=str(common.get("origem") or ""),
        manifest_hash=manifest_hash,
        bytes=int(common["bytes"]),
        data_vigencia=str(data_vigencia) if data_vigencia not in (None, "") else None,
        chunking_sugerido=str(base.get("chunking_sugerido") or "chunking_generico"),
        formato=formato,
        sha256=digest,
        text=text,
        status=status,
        extras=common,
    )


def _pdf_units(
    path: Path,
    base: dict[str, Any],
    digest: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
) -> Iterator[SourceUnit]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("extração de PDF requer pypdf; execute pip install -r requirements.txt") from error

    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    base_document_id = f"{base['base_id']}:{digest[:16]}"
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        yield _make_unit(
            path,
            base,
            digest,
            text,
            f"{base_document_id}:pagina-{page_number}",
            "pdf",
            "ok" if text else "sem_texto",
            manifest_hash,
            collected_at,
            origin,
            {"pagina": page_number, "total_paginas": total_pages},
        )


def _file_units(
    path: Path,
    base: dict[str, Any],
    manifest_hash: str,
    fallback_collected_at: str,
    inventory_provenance: dict[tuple[str, str], dict[str, str]],
) -> Iterator[SourceUnit]:
    digest = sha256_file(path)
    origin, collected_at = _file_provenance(
        base, path, digest, inventory_provenance, fallback_collected_at
    )
    suffix = path.suffix.lower()
    base_document_id = f"{base['base_id']}:{digest[:16]}"

    if suffix == ".pdf":
        yield from _pdf_units(path, base, digest, manifest_hash, collected_at, origin)
        return

    if suffix == ".jsonl":
        for line_number, line in enumerate(_read_text(path).splitlines(), start=1):
            if not line.strip():
                continue
            payload: Any = None
            try:
                payload = json.loads(line)
                text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
                status = "ok"
            except json.JSONDecodeError:
                text = line.strip()
                status = "json_invalido"
            source_file = _relative_path(path)
            yield _make_unit(
                path,
                base,
                digest,
                text,
                f"{base_document_id}:linha-{line_number}",
                "jsonl",
                status,
                manifest_hash,
                collected_at,
                origin,
                {
                    "linha": line_number,
                    "logical_id": _record_logical_id(
                        str(base["base_id"]),
                        source_file,
                        payload,
                        f"linha-{line_number}",
                    ),
                    **_declared_record_metadata(payload, base),
                },
            )
        return

    if suffix == ".csv":
        for record_number, record in _csv_records(path):
            text = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
            source_file = _relative_path(path)
            yield _make_unit(
                path,
                base,
                digest,
                text,
                f"{base_document_id}:registro-{record_number}",
                "csv",
                "ok",
                manifest_hash,
                collected_at,
                origin,
                {
                    "registro": record_number,
                    "logical_id": _record_logical_id(
                        str(base["base_id"]),
                        source_file,
                        record,
                        f"registro-{record_number}",
                    ),
                    **_declared_record_metadata(record, base),
                },
            )
        return

    if suffix == ".json":
        raw = _read_text(path)
        try:
            payload = json.loads(raw)
            status = "ok"
        except json.JSONDecodeError:
            payload = raw
            status = "json_invalido"
        for record_number, record in _json_records(payload):
            text = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
            suffix_id = f":registro-{record_number}" if record_number else ""
            source_file = _relative_path(path)
            yield _make_unit(
                path,
                base,
                digest,
                text,
                f"{base_document_id}{suffix_id}",
                "json",
                status,
                manifest_hash,
                collected_at,
                origin,
                {
                    **({"registro": record_number} if record_number else {}),
                    "logical_id": _record_logical_id(
                        str(base["base_id"]),
                        source_file,
                        record,
                        f"registro-{record_number or 1}",
                    ),
                    **_declared_record_metadata(record, base),
                },
            )
        return

    text = _read_text(path)
    if suffix in {".html", ".htm", ".aspx"}:
        text = _visible_html(text)
    yield _make_unit(
        path,
        base,
        digest,
        text,
        base_document_id,
        suffix.removeprefix(".") or "bin",
        "ok" if text.strip() else "sem_texto",
        manifest_hash,
        collected_at,
        origin,
    )


def _section_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Divide normas por cabeçalhos e só usa overlap dentro de seções longas."""
    sections = [
        section.strip()
        for section in re.split(
            r"(?im)(?=^\s*(?:CL[ÁA]USULA|ART(?:IGO)?\.?|CAP[ÍI]TULO)\b)",
            text,
        )
        if section.strip()
    ]
    if len(sections) <= 1:
        return list(chunk_text(text, chunk_size, overlap))

    chunks: list[str] = []
    for section in sections:
        if len(section) <= chunk_size:
            chunks.append(section)
        else:
            chunks.extend(chunk_text(section, chunk_size, overlap))
    return chunks


def _chunks_for_unit(unit: SourceUnit, chunk_size: int, overlap: int) -> tuple[list[str], str]:
    hint = unit.chunking_sugerido.casefold()
    if (
        "1 contrato" in hint
        or "1 registro" in hint
        or "1 licitação" in hint
        or "1 licitacao" in hint
        or (unit.formato in {"json", "jsonl"} and unit.natureza in {"estruturada", "agregada", "conversacional"})
    ):
        return [unit.text.strip()], "registro_inteiro"
    if "cláusula" in hint or "clausula" in hint or "artigo" in hint:
        return _section_chunks(unit.text, chunk_size, overlap), "secoes_normativas"
    return list(chunk_text(unit.text, chunk_size, overlap)), "generico"


def _chunk_records(unit: SourceUnit, chunk_size: int, overlap: int) -> list[dict[str, Any]]:
    if unit.status != "ok" or not unit.text.strip():
        return []
    chunks, chunking_strategy = _chunks_for_unit(unit, chunk_size, overlap)
    records: list[dict[str, Any]] = []
    unit_extras = _extras_for_unit(unit)
    for index, text in enumerate(chunks, start=1):
        content_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunk_id = f"{unit.document_id}:chunk-{index}-{content_digest[:12]}"
        records.append(
            {
                "chunk_id": chunk_id,
                "document_id": unit.document_id,
                "logical_id": unit.logical_id,
                "base_id": unit.base_id,
                "natureza": unit.natureza,
                "doc_type": unit.doc_type,
                "sensitivity": unit.sensitivity,
                "parent_chunk_id": unit.document_id,
                "chunk_index": index,
                "chunk_count": len(chunks),
                "chunking_strategy": chunking_strategy,
                "source_file": unit.source_file,
                "origem": unit.origem,
                "manifest_fonte": unit.extras.get("manifest_fonte"),
                "manifest_hash": unit.manifest_hash,
                "bytes": unit.bytes,
                "coletado_em": unit.extras.get("coletado_em"),
                "data_vigencia": unit.data_vigencia,
                "formato": unit.formato,
                "sha256": unit.sha256,
                "content_sha256": content_digest,
                "status_extracao": unit.status,
                "nome_arquivo": unit.extras.get("nome_arquivo"),
                "acesso_tipo": unit.extras.get("acesso_tipo"),
                "text": text,
                "extras": {
                    key: value
                    for key, value in unit_extras.items()
                    if key not in {"manifest_fonte", "coletado_em", "nome_arquivo", "acesso_tipo"}
                },
            }
        )
    return records


def _input_files(input_dir: Path) -> tuple[list[Path], int]:
    paths = sorted(path for path in input_dir.rglob("*") if path.is_file())
    supported = [path for path in paths if path.suffix.lower() in SUPPORTED_SUFFIXES]
    return supported, len(paths) - len(supported)


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


def prepare_documents(
    input_dir: Path,
    output_path: Path,
    report_path: Path,
    manifest_path: Path,
    *,
    base_id: str | None = None,
    inventory_path: Path | None = None,
    chunk_size: int = 1200,
    overlap: int = 150,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not input_dir.exists():
        raise FileNotFoundError(f"diretório de entrada não encontrado: {input_dir}")
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("use chunk_size >= 1 e overlap entre 0 e chunk_size-1")

    manifest = load_manifest(manifest_path)
    summary_manifest_hash = _manifest_hash(manifest_path)
    base = _base_for(manifest, input_dir, base_id)
    inventory_provenance = _load_inventory_provenance(inventory_path)
    fallback_collected_at = datetime.now(timezone.utc).isoformat()
    paths, ignored = _input_files(input_dir)
    records: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    status_counts: dict[str, int] = {}

    for path in paths:
        try:
            units = list(
                _file_units(
                    path,
                    base,
                    summary_manifest_hash,
                    fallback_collected_at,
                    inventory_provenance,
                )
            )
        except Exception as error:
            failures.append({"source_file": _relative_path(path), "erro": f"{type(error).__name__}: {error}"})
            continue
        for unit in units:
            status_counts[unit.status] = status_counts.get(unit.status, 0) + 1
            chunks = _chunk_records(unit, chunk_size, overlap)
            records.extend(chunks)
            documents.append(
                {
                    "document_id": unit.document_id,
                    "logical_id": unit.logical_id,
                    "source_file": unit.source_file,
                    "base_id": unit.base_id,
                    "natureza": unit.natureza,
                    "doc_type": unit.doc_type,
                    "sensitivity": unit.sensitivity,
                    "origem": unit.origem,
                    "manifest_fonte": unit.extras.get("manifest_fonte"),
                    "manifest_hash": unit.manifest_hash,
                    "bytes": unit.bytes,
                    "coletado_em": unit.extras.get("coletado_em"),
                    "data_vigencia": unit.data_vigencia,
                    "formato": unit.formato,
                    "sha256": unit.sha256,
                    "status_extracao": unit.status,
                    "chunks": len(chunks),
                    "extras": _extras_for_unit(unit),
                }
            )

    summary = {
        "arquivos": len(paths),
        "documentos": len(documents),
        "chunks": len(records),
        "ignorados": ignored,
        "falhas": len(failures),
        "status_extracao": status_counts,
        "base_id": base["base_id"],
        "natureza": base["natureza"],
        "manifest_hash": summary_manifest_hash,
    }
    if not dry_run:
        _atomic_write_jsonl(output_path, records)
        _atomic_write_json(
            report_path,
            {
                "gerado_em": datetime.now(timezone.utc).isoformat(),
                "manifest": _relative_path(manifest_path),
                "manifest_hash": summary["manifest_hash"],
                "base_id": base["base_id"],
                "resumo": summary,
                "documentos": documents,
                "falhas": failures,
            },
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "corpus_manifest.yaml")
    parser.add_argument("--base-id", help="base declarada no manifesto; obrigatório quando houver ambiguidade")
    parser.add_argument(
        "--inventory",
        type=Path,
        help="inventário da coleta; recupera origem e coletado_em específicos de cada arquivo",
    )
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=150)
    parser.add_argument("--dry-run", action="store_true", help="prepara e resume sem gravar JSONL")
    args = parser.parse_args(argv)
    try:
        summary = prepare_documents(
            args.input.resolve(),
            args.output.resolve(),
            args.report.resolve(),
            args.manifest.resolve(),
            base_id=args.base_id,
            inventory_path=args.inventory.resolve() if args.inventory else None,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            dry_run=args.dry_run,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
