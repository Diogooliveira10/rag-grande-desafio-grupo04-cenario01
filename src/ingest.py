r"""Normaliza o corpus bruto para registros prontos para o pipeline de RAG.

Uso:
    py .\src\ingest.py
    py .\src\ingest.py --dry-run

O módulo não coleta dados. Ele lê somente ``data/raw/v1`` e preserva a
proveniência, o formato, o SHA-256 e o estado de extração de cada arquivo.
PDFs ficam explicitamente marcados como pendentes até que um extrator seja
autorizado/configurado.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "raw" / "v1"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "normalized" / "documents.jsonl"
TEXT_EXTENSIONS = {".html", ".htm", ".aspx", ".txt", ".json", ".jsonl"}


@dataclass(frozen=True)
class RawDocument:
    """Documento lógico derivado de um arquivo bruto."""

    document_id: str
    base_id: str
    source_path: str
    sha256: str
    formato: str
    status_extracao: str
    text: str
    metadata: dict[str, Any]


class _VisibleTextParser(HTMLParser):
    """Extrai texto visível de HTML sem depender de biblioteca externa."""

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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def base_id_for(path: Path) -> str:
    """Classifica fontes atuais sem perder o caminho original."""
    name = path.name.lower()
    if "contratacoes" in name or "contratos" in name:
        return "contratos_estruturado"
    if "ata" in name:
        return "atas_conversacional"
    if "pca" in name:
        return "pca_agregado"
    if "edital" in name or "lei_14133" in name or "decretos" in name:
        return "editais_normativo"
    if path.suffix.lower() in {".html", ".htm", ".aspx"}:
        return "fontes_normativas"
    return "fontes_externas"


def _json_records(payload: Any) -> list[tuple[int | None, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return list(enumerate(payload["data"], start=1))
    if isinstance(payload, list):
        return list(enumerate(payload, start=1))
    return [(None, payload)]


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def iter_raw_documents(input_dir: Path) -> Iterator[RawDocument]:
    """Lê arquivos brutos e produz documentos lógicos estáveis."""
    for path in sorted(path for path in input_dir.rglob("*") if path.is_file()):
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        digest = sha256_file(path)
        base_id = base_id_for(path)
        suffix = path.suffix.lower()
        common_metadata = {
            "arquivo_bruto": relative_path,
            "nome_arquivo": path.name,
            "coletado_em": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        }

        if suffix == ".pdf":
            yield RawDocument(
                document_id=f"{base_id}:{digest[:16]}",
                base_id=base_id,
                source_path=relative_path,
                sha256=digest,
                formato="pdf",
                status_extracao="pendente_extracao_pdf",
                text="",
                metadata=common_metadata,
            )
            continue

        if suffix in {".html", ".htm", ".aspx"}:
            parser = _VisibleTextParser()
            parser.feed(_read_text_file(path))
            text = parser.text()
            yield RawDocument(
                document_id=f"{base_id}:{digest[:16]}",
                base_id=base_id,
                source_path=relative_path,
                sha256=digest,
                formato=suffix.removeprefix("."),
                status_extracao="ok" if text else "sem_texto",
                text=text,
                metadata=common_metadata,
            )
            continue

        if suffix == ".jsonl":
            for line_number, line in enumerate(_read_text_file(path).splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
                    status = "ok"
                except json.JSONDecodeError:
                    text = line.strip()
                    status = "json_invalido"
                yield RawDocument(
                    document_id=f"{base_id}:{digest[:16]}:linha-{line_number}",
                    base_id=base_id,
                    source_path=relative_path,
                    sha256=digest,
                    formato="jsonl",
                    status_extracao=status,
                    text=text,
                    metadata={**common_metadata, "linha": line_number},
                )
            continue

        if suffix == ".json":
            try:
                payload = json.loads(_read_text_file(path))
                status = "ok"
            except json.JSONDecodeError:
                payload = _read_text_file(path)
                status = "json_invalido"
            for record_number, record in _json_records(payload):
                text = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
                suffix_id = f":registro-{record_number}" if record_number else ""
                yield RawDocument(
                    document_id=f"{base_id}:{digest[:16]}{suffix_id}",
                    base_id=base_id,
                    source_path=relative_path,
                    sha256=digest,
                    formato="json",
                    status_extracao=status,
                    text=text,
                    metadata={**common_metadata, **({"registro": record_number} if record_number else {})},
                )
            continue

        if suffix in TEXT_EXTENSIONS:
            text = _read_text_file(path)
            yield RawDocument(
                document_id=f"{base_id}:{digest[:16]}",
                base_id=base_id,
                source_path=relative_path,
                sha256=digest,
                formato=suffix.removeprefix("."),
                status_extracao="ok" if text.strip() else "sem_texto",
                text=text,
                metadata=common_metadata,
            )


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 150) -> Iterator[str]:
    """Divide texto em blocos determinísticos, respeitando parágrafos."""
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not normalized:
        return
    paragraphs = [part.strip() for part in normalized.split("\n\n") if part.strip()]
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            yield current
        tail = current[-overlap:] if overlap and current else ""
        current = f"{tail}\n\n{paragraph}".strip()
        while len(current) > max_chars:
            yield current[:max_chars].strip()
            current = current[max_chars - overlap :].strip() if overlap else current[max_chars:].strip()
    if current:
        yield current


def ingest(input_dir: Path, output_file: Path, max_chars: int, overlap: int) -> dict[str, int]:
    """Gera JSONL normalizado; não altera os arquivos em ``data/raw``."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    documents = chunks = pending_pdf = 0
    with output_file.open("w", encoding="utf-8", newline="\n") as handle:
        for document in iter_raw_documents(input_dir):
            documents += 1
            if document.status_extracao == "pendente_extracao_pdf":
                pending_pdf += 1
            for chunk_number, text in enumerate(chunk_text(document.text, max_chars, overlap), start=1):
                record = {
                    "chunk_id": f"{document.document_id}:chunk-{chunk_number}",
                    "document_id": document.document_id,
                    "base_id": document.base_id,
                    "text": text,
                    "source_path": document.source_path,
                    "sha256": document.sha256,
                    "formato": document.formato,
                    "status_extracao": document.status_extracao,
                    "metadata": document.metadata,
                }
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                chunks += 1
    return {"documentos": documents, "chunks": chunks, "pdfs_pendentes": pending_pdf}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="diretório do corpus bruto")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSONL normalizado de saída")
    parser.add_argument("--chunk-size", type=int, default=1200, help="tamanho máximo de cada chunk")
    parser.add_argument("--overlap", type=int, default=150, help="sobreposição entre chunks")
    parser.add_argument("--dry-run", action="store_true", help="conta documentos sem gravar a saída")
    args = parser.parse_args()
    if not args.input.exists():
        parser.error(f"diretório de entrada não encontrado: {args.input}")
    if args.chunk_size < 1 or args.overlap < 0 or args.overlap >= args.chunk_size:
        parser.error("use chunk-size >= 1 e overlap entre 0 e chunk-size-1")

    if args.dry_run:
        stats = {"documentos": 0, "chunks": 0, "pdfs_pendentes": 0}
        for document in iter_raw_documents(args.input):
            stats["documentos"] += 1
            stats["chunks"] += sum(1 for _ in chunk_text(document.text, args.chunk_size, args.overlap))
            stats["pdfs_pendentes"] += document.status_extracao == "pendente_extracao_pdf"
    else:
        stats = ingest(args.input, args.output, args.chunk_size, args.overlap)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
