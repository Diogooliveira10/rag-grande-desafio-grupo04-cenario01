"""Carrega fatos e agregados JSONL da Agregada em PostgreSQL/Neon via psql.

Não instala nem importa driver Python. Os JSONL versionados continuam sendo o
snapshot reproduzível; PostgreSQL recebe uma cópia consultável por versão.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, TextIO


DATASETS = (
    ("facts/pca_items.jsonl", "pca_item_facts", "fact_id"),
    ("facts/homologated_items.jsonl", "homologated_item_facts", "fact_id"),
    ("aggregates/pca_planned.jsonl", "pca_planned_aggregates", "logical_id"),
    (
        "aggregates/homologated_item_prices.jsonl",
        "homologated_item_aggregates",
        "logical_id",
    ),
)

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS pca_agregado;

CREATE TABLE IF NOT EXISTS pca_agregado.pca_item_facts (
    corpus_version text NOT NULL,
    record_id text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (corpus_version, record_id)
);
CREATE TABLE IF NOT EXISTS pca_agregado.homologated_item_facts (
    corpus_version text NOT NULL,
    record_id text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (corpus_version, record_id)
);
CREATE TABLE IF NOT EXISTS pca_agregado.pca_planned_aggregates (
    corpus_version text NOT NULL,
    record_id text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (corpus_version, record_id)
);
CREATE TABLE IF NOT EXISTS pca_agregado.homologated_item_aggregates (
    corpus_version text NOT NULL,
    record_id text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (corpus_version, record_id)
);

CREATE OR REPLACE VIEW pca_agregado.pca_items AS
SELECT
    corpus_version,
    record_id AS fact_id,
    payload->>'cnpj_orgao' AS cnpj_orgao,
    NULLIF(payload->>'ano_pca', '')::integer AS ano_pca,
    payload->>'categoria' AS categoria,
    payload->>'descricao' AS descricao,
    payload->>'unidade_fornecimento' AS unidade_fornecimento,
    payload->>'quantidade_estimada' AS quantidade_estimada_texto,
    payload->>'valor_unitario_planejado' AS valor_unitario_planejado_texto,
    payload->>'valor_total_planejado' AS valor_total_planejado_texto,
    payload->'source_ref' AS source_ref,
    payload AS evidencia
FROM pca_agregado.pca_item_facts;

CREATE OR REPLACE VIEW pca_agregado.homologated_items AS
SELECT
    corpus_version,
    record_id AS fact_id,
    payload->>'compra' AS compra,
    NULLIF(payload->>'numeroItem', '')::integer AS numero_item,
    payload->>'descricao' AS descricao,
    payload->>'unidadeMedida' AS unidade_medida,
    NULLIF(payload->>'quantidadeHomologada', '')::numeric AS quantidade_homologada,
    NULLIF(payload->>'valorUnitarioHomologado', '')::numeric AS valor_unitario_homologado,
    payload->>'valorTotalHomologado' AS valor_total_homologado_informado,
    payload->>'fornecedor' AS fornecedor,
    payload->>'niFornecedor' AS identificador_fornecedor,
    payload->>'municipio' AS municipio,
    payload->>'esfera' AS esfera,
    payload->>'modalidade' AS modalidade,
    NULLIF(payload->>'anoAssinatura', '')::integer AS ano_assinatura,
    payload->>'mesResultado' AS mes_resultado,
    payload->>'situacaoCompraItemResultadoId' AS situacao_resultado,
    payload AS evidencia
FROM pca_agregado.homologated_item_facts;

CREATE OR REPLACE VIEW pca_agregado.planned_indicators AS
SELECT
    corpus_version,
    record_id AS indicator_id,
    payload->>'tipo_valor' AS tipo_valor,
    payload->>'indicador' AS indicador,
    payload->>'dimensao' AS dimensao,
    payload->>'texto' AS texto,
    payload->'recorte' AS recorte,
    payload AS evidencia
FROM pca_agregado.pca_planned_aggregates;

CREATE OR REPLACE VIEW pca_agregado.homologated_price_indicators AS
SELECT
    corpus_version,
    record_id AS indicator_id,
    payload->>'tipo_valor' AS tipo_valor,
    payload->>'indicador' AS indicador,
    payload->>'texto' AS texto,
    payload AS evidencia
FROM pca_agregado.homologated_item_aggregates;
"""


def _read_dataset(
    path: Path, *, version: str, id_field: str
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: JSONL inválido: {error}") from error
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: cada linha precisa ser um objeto JSON")
            record_id = payload.get(id_field)
            if not record_id:
                raise ValueError(f"{path}:{line_number}: campo {id_field!r} ausente")
            payload_version = str(payload.get("corpus_version") or version)
            if payload_version != version:
                raise ValueError(
                    f"{path}:{line_number}: corpus_version {payload_version!r} difere de {version!r}"
                )
            rows.append((version, str(record_id), json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
    return rows


def _write_copy(stream: TextIO, table_name: str, rows: list[tuple[str, str, str]]) -> None:
    stream.write(
        "\\copy _staging (corpus_version, record_id, payload) "
        "FROM STDIN WITH (FORMAT csv)\n"
    )
    stream.flush()


def _safe_error(message: str, database_url: str) -> str:
    return message.replace(database_url, "<DATABASE_URL redacted>")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    stream.write("\\.\n")
    stream.flush()
    stream.write(
        f"INSERT INTO pca_agregado.{table_name} (corpus_version, record_id, payload) "
        "SELECT corpus_version, record_id, payload::jsonb FROM _staging "
        "ON CONFLICT (corpus_version, record_id) DO NOTHING;\n"
        "TRUNCATE _staging;\n"
    )
    stream.flush()


def load(input_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    report_path = input_dir / "materialization_report.json"
    if not report_path.is_file():
        raise ValueError(f"relatório de materialização ausente: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    version = str(report.get("corpus_version") or "")
    if not version:
        raise ValueError("o relatório não declara corpus_version")

    datasets: list[dict[str, Any]] = []
    for relative_path, table_name, id_field in DATASETS:
        path = input_dir / relative_path
        if not path.is_file():
            raise ValueError(f"arquivo materializado ausente: {path}")
        rows = _read_dataset(path, version=version, id_field=id_field)
        datasets.append({"table": table_name, "path": path, "rows": rows})

    summary = {
        "corpus_version": version,
        "database": "PostgreSQL/Neon",
        "datasets": {dataset["table"]: len(dataset["rows"]) for dataset in datasets},
        "dry_run": dry_run,
    }
    if dry_run:
        return summary

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("defina DATABASE_URL no ambiente; a credencial não deve ser passada como argumento")
    psql = shutil.which("psql")
    if not psql:
        raise ValueError("psql não encontrado; instale/use o cliente PostgreSQL já disponível no ambiente")

    child_env = os.environ.copy()
    child_env["PGDATABASE"] = database_url
    process = subprocess.Popen(
        [psql, "--no-psqlrc", "--quiet", "--set", "ON_ERROR_STOP=1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=child_env,
    )
    assert process.stdin is not None
    try:
        process.stdin.write("BEGIN;\n" + SCHEMA_SQL)
        process.stdin.write(
            "CREATE TEMP TABLE _staging (corpus_version text, record_id text, payload text) "
            "ON COMMIT DROP;\n"
        )
        process.stdin.flush()
        for dataset in datasets:
            _write_copy(process.stdin, dataset["table"], dataset["rows"])
        process.stdin.write("COMMIT;\n")
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate()
    except (BrokenPipeError, OSError):
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate()
        if process.returncode:
            raise RuntimeError(
                f"psql falhou ({process.returncode}): "
                f"{_safe_error(stderr.strip() or stdout.strip(), database_url)}"
            )
        raise
    if process.returncode:
        raise RuntimeError(
            f"psql falhou ({process.returncode}): "
            f"{_safe_error(stderr.strip() or stdout.strip(), database_url)}"
        )
    summary["loaded"] = True
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="diretório da materialização versionada")
    parser.add_argument("--dry-run", action="store_true", help="valida JSONL e mostra contagens sem conectar ao banco")
    args = parser.parse_args(argv)
    try:
        summary = load(args.input.resolve(), dry_run=args.dry_run)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
