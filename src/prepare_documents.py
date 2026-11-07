"""Prepara documentos para o estágio de embeddings.

O módulo mantém a coleta separada da ingestão: lê uma base já materializada
ou uma fixture local, extrai texto por formato, preserva a proveniência e
grava chunks JSONL com um contrato canônico de metadados em nível único.

Exemplo rápido com uma fixture local, sem acessar as bases remotas:

    py -m src.prepare_documents `
      --input tests/fixtures/pdf `
      --manifest corpus_manifest.yaml `
      --base-id exemplos_teste `
      --output data/normalized/exemplos_teste.jsonl

Para preparar uma base coletada, use ``--latest --base-id <base-id>``. A CLI
seleciona o inventário completo mais recente e grava em
``data/normalized/vN/<base-id>/``. Para reproduzir uma versão histórica,
informe manualmente os caminhos de entrada, inventário e saída da mesma versão.

Organização (2026-11-07): este arquivo é o caso de uso, a orquestração da
preparação (``prepare_documents``, ``_Preparacao``) e a CLI. As peças ficam no
pacote ``src/preparation/``: ``readers`` (formatos), ``extraction`` (unidades
por formato), ``provenance`` (origem e metadados), ``chunking`` (chunks),
``state`` (cache incremental e escrita) e ``common`` (a unidade e as versões).
Os nomes de antes continuam importáveis daqui.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src import ocr
from src.obs import Stage
from src.offline_observation import observar_offline
from src.fetch_corpus import load_manifest
from src.ingest import sha256_file
from src.preparation.common import (  # noqa: F401  (reexportados)
    PROJECT_ROOT,
    TEXT_SUFFIXES,
    STRUCTURED_SUFFIXES,
    SUPPORTED_SUFFIXES,
    _CANONICAL_UNIT_FIELDS,
    EXTRACTION_SCHEMA_VERSION,
    CHUNKING_SCHEMA_VERSION,
    SourceUnit,
    _relative_path,
)
from src.preparation.readers import (  # noqa: F401  (reexportados)
    _VisibleTextParser,
    _read_text,
    _visible_html,
    _json_records,
    _csv_records,
)
from src.preparation.state import (  # noqa: F401  (reexportados)
    _manifest_hash,
    _fingerprint,
    _extraction_fingerprint,
    _chunking_fingerprint,
    _unit_to_record,
    _unit_from_record,
    _extraction_state_path,
    _load_units_state,
    _load_chunk_state,
    _input_files,
    _input_files_from_inventory,
    _atomic_write_jsonl,
    _atomic_write_json,
)
from src.preparation.provenance import (  # noqa: F401  (reexportados)
    _base_origins,
    _base_origin,
    _declared_record_metadata,
    _normalize_date_value,
    _record_logical_id,
    _base_for,
    _load_inventory_provenance,
    _load_related_context,
    _with_related_context,
    _embedded_origin,
    _file_provenance,
    _unit_common,
    _make_unit,
    _refresh_unit_provenance,
)
from src.preparation.extraction import (  # noqa: F401  (reexportados)
    _texto_legivel_da_pagina,
    _reextrair_pagina_ilegivel,
    _pdf_units,
    _ArquivoDeOrigem,
    _CAMPOS_DECLARADOS_JSONL,
    _file_units,
    _unidades_de_conversas,
    _unidades_jsonl,
    _unidades_de_indicadores_pca,
    _unidades_csv,
    _unidades_json,
    _unidades_de_resultados_homologados,
)
from src.preparation.chunking import (  # noqa: F401  (reexportados)
    _extras_for_unit,
    _join_numeric_page_preamble,
    _section_chunks,
    _chunks_for_unit,
    _chunk_records,
    _refresh_cached_chunks,
)


DEFAULT_INPUT = PROJECT_ROOT / "data" / "raw" / "v1"


DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "normalized" / "documents.jsonl"


DEFAULT_REPORT = PROJECT_ROOT / "data" / "normalized" / "extraction_report.json"


@dataclass
class _Preparacao:
    """Chunks e documentos acumulados na preparação, com os acertos do cache de chunks."""

    chunks_cache: dict[tuple[str, str, str], list[dict[str, Any]]]
    chunk_size: int
    overlap: int
    records: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    chunk_cache_hits: int = 0
    chunk_cache_misses: int = 0

    def adicionar_todas(self, units: Iterable[SourceUnit]) -> int:
        """Acrescenta as unidades em ordem; devolve quantas eram."""
        total = 0
        for unit in units:
            self.adicionar(unit)
            total += 1
        return total

    def adicionar(self, unit: SourceUnit) -> None:
        """Chunks da unidade (do cache quando o texto e a segmentação não mudaram) e o registro do documento."""
        self.status_counts[unit.status] = self.status_counts.get(unit.status, 0) + 1
        document_content_digest = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
        chunking_fingerprint = _chunking_fingerprint(unit, self.chunk_size, self.overlap)
        chunk_cache_key = (unit.document_id, document_content_digest, chunking_fingerprint)
        if chunk_cache_key in self.chunks_cache:
            chunks = _refresh_cached_chunks(self.chunks_cache[chunk_cache_key], unit)
            self.chunk_cache_hits += 1
        else:
            chunks = _chunk_records(unit, self.chunk_size, self.overlap)
            self.chunk_cache_misses += 1
        self.records.extend(chunks)
        self.documents.append(
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


@observar_offline(Stage.PREPARACAO)
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
    inventory_provenance = _load_inventory_provenance(
        inventory_path,
        expected_manifest_hash=summary_manifest_hash,
    )
    related_context = _load_related_context(inventory_path, base)
    fallback_collected_at = datetime.now(timezone.utc).isoformat()
    paths, ignored = (
        _input_files_from_inventory(input_dir, inventory_path, str(base["base_id"]))
        if inventory_path is not None
        else _input_files(input_dir)
    )
    units_state_path = _extraction_state_path(output_path)
    units_cache, units_cache_invalid = _load_units_state(units_state_path)
    chunks_cache, chunks_cache_invalid = _load_chunk_state(output_path)
    preparacao = _Preparacao(chunks_cache, chunk_size, overlap)
    state_units, failures, extraction_cache_hits, extraction_cache_misses = _extrair_arquivos(
        paths, base, units_cache, preparacao, summary_manifest_hash=summary_manifest_hash,
        fallback_collected_at=fallback_collected_at, inventory_provenance=inventory_provenance,
        related_context=related_context,
    )

    derived_result_indicators = 0
    if base.get("base_id") == "pca_agregado" and inventory_path is not None:
        derived_result_indicators = preparacao.adicionar_todas(_unidades_de_resultados_homologados(
            inventory_path, base, summary_manifest_hash, fallback_collected_at
        ))

    records, documents = preparacao.records, preparacao.documents
    summary = {
        "arquivos": len(paths),
        "documentos": len(documents),
        "chunks": len(records),
        "ignorados": ignored,
        "falhas": len(failures),
        "status_extracao": preparacao.status_counts,
        "indicadores_resultados_homologados": derived_result_indicators,
        "base_id": base["base_id"],
        "natureza": base["natureza"],
        "manifest_hash": summary_manifest_hash,
        "corpus_version": inventory_path.stem if inventory_path and re.fullmatch(r"v\d+", inventory_path.stem) else None,
        "inventory": _relative_path(inventory_path) if inventory_path else None,
        "extraction_cache_hits": extraction_cache_hits,
        "extraction_cache_misses": extraction_cache_misses,
        "chunk_cache_hits": preparacao.chunk_cache_hits,
        "chunk_cache_misses": preparacao.chunk_cache_misses,
        "incremental_state": _relative_path(units_state_path),
        "incremental_state_invalid": units_cache_invalid or chunks_cache_invalid,
    }
    if not dry_run:
        _gravar_preparacao(
            output_path, units_state_path, report_path, manifest_path, inventory_path,
            base, summary, preparacao, state_units, failures,
        )
    return summary


def _extrair_arquivos(
    paths: list[Path], base: dict[str, Any], units_cache: dict[str, Any], preparacao: _Preparacao, *,
    summary_manifest_hash: str, fallback_collected_at: str, inventory_provenance: dict[Any, Any],
    related_context: dict[str, Any],
) -> tuple[list[SourceUnit], list[dict[str, str]], int, int]:
    """Extrai cada arquivo (do cache quando possível), liga as relações e entrega as unidades à ``preparacao``.

    Devolve as unidades para o estado incremental, as falhas por arquivo e os
    acertos e erros do cache de extração. Um arquivo que falha não interrompe os demais.
    """
    state_units: list[SourceUnit] = []
    failures: list[dict[str, str]] = []
    extraction_cache_hits = 0
    extraction_cache_misses = 0
    for path in paths:
        digest = sha256_file(path)
        source_file = _relative_path(path)
        extraction_fingerprint = _extraction_fingerprint(base)
        try:
            units, from_cache = _unidades_extraidas(
                path, digest, source_file, base, extraction_fingerprint, units_cache,
                summary_manifest_hash, fallback_collected_at, inventory_provenance,
            )
            if from_cache:
                extraction_cache_hits += 1
            else:
                extraction_cache_misses += 1
            state_units.extend(units)
            units = _com_relacoes(
                units,
                inventory_provenance.get((str(base["base_id"]), digest), {}).get("relacoes") or [],
                related_context.get(digest),
            )
        except Exception as error:
            failures.append({"source_file": source_file, "erro": f"{type(error).__name__}: {error}"})
            continue
        preparacao.adicionar_todas(units)
    return state_units, failures, extraction_cache_hits, extraction_cache_misses


def _com_relacoes(
    units: list[SourceUnit], relations: list[Any], related: Any
) -> list[SourceUnit]:
    """Relações PNCP do inventário e contexto dos arquivos relacionados, fora do cache de extração."""
    if relations:
        units = [
            replace(unit, extras={**unit.extras, "relacoes_pncp": relations})
            for unit in units
        ]
    # Aplicado a cada execução (e fora do cache): depende de outros arquivos.
    return [_with_related_context(unit, related) for unit in units]


def _gravar_preparacao(
    output_path: Path,
    units_state_path: Path,
    report_path: Path,
    manifest_path: Path,
    inventory_path: Path | None,
    base: dict[str, Any],
    summary: dict[str, Any],
    preparacao: _Preparacao,
    state_units: list[SourceUnit],
    failures: list[dict[str, str]],
) -> None:
    """Chunks, estado incremental da extração e relatório, com a versão do inventário nos registros."""
    records, documents = preparacao.records, preparacao.documents
    if inventory_path is not None:
        inventory_payload = json.loads(inventory_path.read_text(encoding="utf-8"))
        provenance = {
            "inventory_version": inventory_payload.get("version"),
            "inventory_hash": sha256_file(inventory_path),
        }
        for record in records + documents:
            record.update(provenance)
    _atomic_write_jsonl(output_path, records)
    _atomic_write_jsonl(units_state_path, [_unit_to_record(unit) for unit in state_units])
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


def _unidades_extraidas(
    path: Path,
    digest: str,
    source_file: str,
    base: dict[str, Any],
    extraction_fingerprint: str,
    units_cache: dict[tuple[str, str, str], list[SourceUnit]],
    summary_manifest_hash: str,
    fallback_collected_at: str,
    inventory_provenance: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[SourceUnit], bool]:
    """(unidades do arquivo, se vieram do cache de extração); extrai de novo só o que mudou."""
    cache_key = (source_file, digest, extraction_fingerprint)
    # Instalar OCR não invalida texto existente. Só páginas antes sem
    # texto precisam de uma nova extração; os demais parâmetros devem coincidir.
    previous_key = (source_file, digest, _extraction_fingerprint(base, ocr_motor="sem-ocr"))
    if cache_key not in units_cache and ocr.disponivel() and previous_key in units_cache:
        refreshed = []
        for cached_unit in units_cache[previous_key]:
            unit = replace(cached_unit, extraction_fingerprint=extraction_fingerprint)
            if unit.formato == "pdf" and unit.status == "sem_texto":
                text = ocr.texto_da_pagina(path, int(unit.extras["pagina"]) - 1).strip()
                if text:
                    unit = replace(unit, text=text, status="ok_ocr",
                                   extras={**unit.extras, "extracao": ocr.motor()})
            refreshed.append(unit)
        units_cache[cache_key] = refreshed
    if cache_key not in units_cache:
        units = [
            replace(unit, extraction_fingerprint=extraction_fingerprint)
            for unit in _file_units(
                path,
                base,
                summary_manifest_hash,
                fallback_collected_at,
                inventory_provenance,
            )
        ]
        return units, False
    units = list(units_cache[cache_key])
    if any(unit.manifest_hash != summary_manifest_hash for unit in units):
        units = [
            _refresh_unit_provenance(
                unit,
                path,
                base,
                summary_manifest_hash,
                fallback_collected_at,
                inventory_provenance,
            )
            for unit in units
        ]
    # Extrações antigas guardaram glifos ("/12 /3 □") como texto.
    units = [_reextrair_pagina_ilegivel(path, unit) for unit in units]
    return units, True


def _latest_corpus_paths(
    data_root: Path,
    base_id: str,
    manifest_path: Path,
) -> tuple[str, Path, Path, Path]:
    inventory_root = data_root / "inventory"
    versioned_inventories = [
        (int(match.group(1)), path)
        for path in inventory_root.glob("v*.json")
        if (match := re.fullmatch(r"v(\d+)\.json", path.name))
    ]
    if not versioned_inventories:
        raise FileNotFoundError(f"nenhum inventário versionado encontrado em {inventory_root}")

    version_number, inventory_path = max(versioned_inventories, key=lambda item: item[0])
    version = f"v{version_number}"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("version") != version:
        raise ValueError(f"versão declarada no inventário não corresponde ao arquivo {inventory_path}")
    if inventory.get("completo") is not True:
        raise ValueError(f"o inventário mais recente ({version}) está incompleto")
    if inventory.get("manifest_hash") != _manifest_hash(manifest_path):
        raise ValueError(
            f"o inventário mais recente ({version}) não corresponde ao manifesto atual; "
            "atualize a coleta antes de preparar os documentos"
        )

    bases = inventory.get("por_base", [])
    base_inventory = next(
        (item for item in bases if isinstance(item, dict) and item.get("base_id") == base_id),
        None,
    )
    if base_inventory is None or base_inventory.get("status") != "complete":
        raise ValueError(f"a base {base_id!r} não está completa no inventário mais recente ({version})")

    input_dir = data_root / "raw" / version
    if not input_dir.is_dir():
        raise FileNotFoundError(f"arquivos brutos do inventário mais recente não encontrados: {input_dir}")
    output_dir = data_root / "normalized" / version / base_id
    return version, input_dir, inventory_path, output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "corpus_manifest.yaml")
    parser.add_argument("--base-id", help="base declarada no manifesto; obrigatório quando houver ambiguidade")
    parser.add_argument(
        "--inventory",
        type=Path,
        help="inventário da coleta; recupera origem e coletado_em específicos de cada arquivo",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="usa o inventário completo mais recente e caminhos versionados de raw/ e normalized/",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=PROJECT_ROOT / "data",
        help="raiz que contém inventory/, raw/ e normalized/ (usada com --latest)",
    )
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=150)
    parser.add_argument("--dry-run", action="store_true", help="prepara e resume sem gravar JSONL")
    args = parser.parse_args(argv)
    try:
        if args.latest:
            if not args.base_id:
                parser.error("--base-id é obrigatório com --latest")
            if args.input or args.output or args.report or args.inventory:
                parser.error("--latest define --input, --output, --report e --inventory automaticamente")
            version, input_dir, inventory_path, output_dir = _latest_corpus_paths(
                args.data_root.resolve(),
                args.base_id,
                args.manifest.resolve(),
            )
            output_path = output_dir / "documents.jsonl"
            report_path = output_dir / "extraction_report.json"
        else:
            version = None
            input_dir = args.input or DEFAULT_INPUT
            output_path = args.output or DEFAULT_OUTPUT
            report_path = args.report or DEFAULT_REPORT
            inventory_path = args.inventory

        summary = prepare_documents(
            input_dir.resolve(),
            output_path.resolve(),
            report_path.resolve(),
            args.manifest.resolve(),
            base_id=args.base_id,
            inventory_path=inventory_path.resolve() if inventory_path else None,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            dry_run=args.dry_run,
        )
        if version:
            summary["corpus_version"] = version
            summary["inventory"] = _relative_path(inventory_path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
