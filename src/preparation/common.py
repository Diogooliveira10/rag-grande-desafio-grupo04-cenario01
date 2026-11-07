"""Peças comuns da preparação: a unidade extraída, extensões aceitas e versões dos esquemas."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


EXTRACTION_SCHEMA_VERSION = "prepare-extraction-v1"


CHUNKING_SCHEMA_VERSION = "prepare-chunking-v1"


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
    extraction_fingerprint: str = ""


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        parent_digest = hashlib.sha256(str(path.resolve().parent).encode("utf-8")).hexdigest()[:12]
        return f"external/{parent_digest}/{path.name}"
