"""Materializa fatos e indicadores da base lógica ``pca_agregado`` em JSONL.

O script lê somente arquivos listados no inventário informado. Não coleta da
rede, não cria outra base e não altera o inventário oficial da versão.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any, Iterator

from src.pca_indicators import (
    COL_CATEGORIA,
    COL_CLASSE,
    COL_CODIGO_CLASSE,
    COL_DATA,
    COL_DESCRICAO,
    COL_UNIDADE,
    COL_VALOR_TOTAL,
    eh_csv_pca,
    indicadores_pca,
)
from src.pncp_result_indicators import indicadores_resultados_homologados


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "pca-agregado-jsonl-v1"


def _csv_rows(path: Path) -> Iterator[dict[str, str]]:
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
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    for row in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        if any(value not in (None, "") for value in row.values()):
            yield {str(key): str(value or "") for key, value in row.items() if key}


def _read_inventory_path(record: dict[str, Any], project_root: Path) -> Path | None:
    raw_path = record.get("path") or record.get("source_file")
    if not raw_path:
        return None
    path = Path(str(raw_path))
    return path if path.is_absolute() else project_root / path


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
            stream.write("\n")
    temporary.replace(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pca_facts(
    inventory: dict[str, Any], project_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    records = [
        record
        for record in inventory.get("arquivos", [])
        if isinstance(record, dict)
        and record.get("base_id") == "pca_agregado"
        and record.get("status") == "ok"
        and isinstance(record.get("source_ref"), dict)
        and record["source_ref"].get("resource_type") == "pca"
    ]

    for record in sorted(records, key=lambda item: str(item.get("artifact_id") or item.get("logical_id") or "")):
        path = _read_inventory_path(record, project_root)
        if path is None or path.suffix.lower() != ".csv" or not path.is_file():
            continue
        rows = list(_csv_rows(path))
        if not rows or not eh_csv_pca(rows[0]):
            continue
        source_ref = record["source_ref"]
        source_key = source_ref.get("key") if isinstance(source_ref.get("key"), dict) else {}
        source_artifact = str(record.get("artifact_id") or record.get("logical_id") or path.name)
        digest = str(record.get("sha256") or _sha256(path))
        pca_context = {
            "cnpj_orgao": source_key.get("cnpj"),
            "ano_pca": source_key.get("ano"),
            "sequencial_pca": source_key.get("sequencial"),
        }
        for row_number, row in enumerate(rows, start=1):
            fact_id = hashlib.sha256(f"{source_artifact}:{row_number}".encode("utf-8")).hexdigest()
            facts.append(
                {
                    "base_id": "pca_agregado",
                    "dataset": "pca_items",
                    "fact_id": f"pca-item:{fact_id}",
                    "corpus_version": inventory.get("version"),
                    "manifest_hash": inventory.get("manifest_hash"),
                    **pca_context,
                    "unidade_responsavel": row.get(COL_UNIDADE),
                    "uasg": row.get("UASG"),
                    "numero_item_pca": row.get("Id do item no PCA"),
                    "categoria": row.get(COL_CATEGORIA),
                    "identificador_futura_contratacao": row.get("Identificador da Futura Contratação"),
                    "nome_futura_contratacao": row.get("Nome da Futura Contratação"),
                    "catalogo_utilizado": row.get("Catálogo Utilizado"),
                    "codigo_classe": row.get(COL_CODIGO_CLASSE),
                    "classe": row.get(COL_CLASSE),
                    "codigo_pdm": row.get("Código do PDM do Item"),
                    "nome_pdm": row.get("Nome do PDM do Item"),
                    "codigo_item": row.get("Código do Item"),
                    "descricao": row.get(COL_DESCRICAO),
                    "unidade_fornecimento": row.get("Unidade de Fornecimento"),
                    "quantidade_estimada": row.get("Quantidade Estimada"),
                    "valor_unitario_planejado": row.get("Valor Unitário Estimado (R$)"),
                    "valor_total_planejado": row.get(COL_VALOR_TOTAL),
                    "orcamento_exercicio_planejado": row.get("Valor orçamentário estimado para o exercício (R$)"),
                    "data_desejada": row.get(COL_DATA),
                    "source_ref": source_ref,
                    "artifact_id": source_artifact,
                    "source_file": _relative_path(path, project_root),
                    "source_sha256": digest,
                    "source_url": record.get("url_final") or record.get("origem"),
                    "source_row": row_number,
                }
            )

        orgao = str(
            record.get("orgao")
            or record.get("fonte")
            or f"órgão CNPJ {pca_context.get('cnpj_orgao') or 'não informado'}"
        )
        for indicator in indicadores_pca(rows, orgao=orgao, ano=pca_context.get("ano_pca")):
            aggregates.append(
                {
                    "base_id": "pca_agregado",
                    "dataset": "pca_planned_aggregates",
                    "logical_id": f"pca_agregado:aggregate:pca:{source_artifact}:{indicator.id}",
                    "corpus_version": inventory.get("version"),
                    "manifest_hash": inventory.get("manifest_hash"),
                    "tipo_valor": "valor_planejado_estimado",
                    "indicador": indicator.id,
                    "dimensao": indicator.dimensao,
                    "recorte": pca_context,
                    "metadados": indicator.metadados,
                    "texto": indicator.texto,
                    "source_ref": source_ref,
                    "artifact_id": source_artifact,
                    "source_sha256": digest,
                    "source_url": record.get("url_final") or record.get("origem"),
                }
            )
    return facts, aggregates


def materialize(
    inventory_path: Path,
    output_dir: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise ValueError("o inventário precisa ser um objeto JSON")
    if not isinstance(inventory.get("arquivos"), list):
        raise ValueError("o inventário não contém a lista 'arquivos'")
    inventory_digest = _sha256(inventory_path)
    prior_report = output_dir / "materialization_report.json"
    if prior_report.is_file():
        prior = json.loads(prior_report.read_text(encoding="utf-8"))
        if (
            prior.get("schema_version") != SCHEMA_VERSION
            or prior.get("source_inventory_sha256") != inventory_digest
        ):
            raise ValueError(
                "o diretório já pertence a outra entrada/versão; use um novo caminho de saída"
            )

    pca_facts, pca_aggregates = _pca_facts(inventory, project_root)
    result_facts_path = output_dir / "facts" / "homologated_items.jsonl"
    result_aggregates = indicadores_resultados_homologados(
        inventory,
        project_root,
        fatos_saida=result_facts_path,
    )
    result_aggregate_rows = [
        {
            "base_id": "pca_agregado",
            "dataset": "homologated_item_aggregates",
            "logical_id": f"pca_agregado:aggregate:homologated:{indicator.get('indicador')}",
            "corpus_version": inventory.get("version"),
            "manifest_hash": inventory.get("manifest_hash"),
            **indicator,
        }
        for indicator in result_aggregates
    ]

    pca_facts_path = output_dir / "facts" / "pca_items.jsonl"
    pca_aggregates_path = output_dir / "aggregates" / "pca_planned.jsonl"
    result_aggregates_path = output_dir / "aggregates" / "homologated_item_prices.jsonl"
    _write_jsonl(pca_facts_path, pca_facts)
    _write_jsonl(pca_aggregates_path, pca_aggregates)
    _write_jsonl(result_aggregates_path, result_aggregate_rows)

    files = [pca_facts_path, result_facts_path, pca_aggregates_path, result_aggregates_path]
    with result_facts_path.open(encoding="utf-8") as stream:
        result_fact_count = sum(1 for _ in stream)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "base_id": "pca_agregado",
        "natureza": "agregada",
        "source_inventory": _relative_path(inventory_path, project_root),
        "source_inventory_sha256": inventory_digest,
        "corpus_version": inventory.get("version"),
        "manifest_hash": inventory.get("manifest_hash"),
        "counts": {
            "pca_item_facts": len(pca_facts),
            "homologated_item_facts": result_fact_count,
            "pca_aggregate_rows": len(pca_aggregates),
            "homologated_aggregate_rows": len(result_aggregate_rows),
        },
        "files": {
            str(path.relative_to(output_dir)): {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in files
        },
    }
    _write_json(output_dir / "materialization_report.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path, help="inventário da versão já coletada")
    parser.add_argument("--output", required=True, type=Path, help="diretório versionado para facts/ e aggregates/")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="calcula contagens sem deixar artefatos no caminho de saída")
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            with tempfile.TemporaryDirectory(prefix="pca-agregado-") as temporary_dir:
                summary = materialize(
                    args.inventory.resolve(),
                    Path(temporary_dir) / "pca_agregado",
                    project_root=args.project_root.resolve(),
                )
            summary["dry_run"] = True
        else:
            summary = materialize(
                args.inventory.resolve(),
                args.output.resolve(),
                project_root=args.project_root.resolve(),
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
