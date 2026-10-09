r"""Compatibilidade para normalizar o corpus bruto com o manifesto.

Uso:
    py .\src\ingest.py
    py .\src\ingest.py --dry-run

O fluxo oficial está em ``prepare_documents.py``. Este módulo preserva
``chunk_text`` e ``sha256_file`` para uso compartilhado e mantém a função
``ingest`` como uma fachada compatível, agora delegando a extração de PDFs,
metadados e validação do manifesto ao preparador oficial.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.obs import Stage
from src.offline_observation import observar_offline

DEFAULT_INPUT = PROJECT_ROOT / "data" / "raw" / "v1"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "normalized" / "documents.jsonl"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 150) -> Iterator[str]:
    """Divide texto em blocos determinísticos, respeitando parágrafos."""
    if max_chars < 1:
        raise ValueError("max_chars deve ser >= 1")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap deve estar entre 0 e max_chars-1")

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


@observar_offline(Stage.INGESTAO)
def ingest(
    input_dir: Path,
    output_file: Path,
    max_chars: int,
    overlap: int,
    *,
    manifest_path: Path = PROJECT_ROOT / "corpus_manifest.yaml",
    report_file: Path | None = None,
    base_id: str | None = None,
    inventory_path: Path | None = None,
    allow_uninventoried: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Gera JSONL oficial sem alterar os arquivos em ``data/raw``."""
    from src.prepare_documents import prepare_documents

    if inventory_path is None and not allow_uninventoried:
        raise ValueError(
            "ingest exige --inventory; use allow_uninventoried=True apenas para diagnóstico explícito"
        )

    report_path = report_file or output_file.with_name("extraction_report.json")
    return prepare_documents(
        input_dir,
        output_file,
        report_path,
        manifest_path,
        base_id=base_id,
        inventory_path=inventory_path,
        chunk_size=max_chars,
        overlap=overlap,
        dry_run=dry_run,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="diretório do corpus bruto")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSONL normalizado de saída")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "corpus_manifest.yaml")
    parser.add_argument("--report", type=Path, help="relatório JSON da preparação")
    parser.add_argument("--base-id", help="base declarada no manifesto")
    parser.add_argument("--inventory", type=Path, help="inventário oficial da versão promovida")
    parser.add_argument(
        "--allow-uninventoried",
        action="store_true",
        help="permite o modo legado sem inventário; não usar no corpus oficial",
    )
    parser.add_argument("--chunk-size", type=int, default=1200, help="tamanho máximo de cada chunk")
    parser.add_argument("--overlap", type=int, default=150, help="sobreposição entre chunks")
    parser.add_argument("--dry-run", action="store_true", help="conta documentos sem gravar a saída")
    args = parser.parse_args()
    if not args.input.exists():
        parser.error(f"diretório de entrada não encontrado: {args.input}")
    if args.chunk_size < 1 or args.overlap < 0 or args.overlap >= args.chunk_size:
        parser.error("use chunk-size >= 1 e overlap entre 0 e chunk-size-1")

    stats = ingest(
        args.input,
        args.output,
        args.chunk_size,
        args.overlap,
        manifest_path=args.manifest.resolve(),
        report_file=args.report.resolve() if args.report else None,
        base_id=args.base_id,
        inventory_path=args.inventory.resolve() if args.inventory else None,
        allow_uninventoried=args.allow_uninventoried,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        stats["dry_run"] = True
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
