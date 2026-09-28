"""Executa a validação do corpus e grava um relatório Markdown timestamped.

Uso:
    py tests/run_corpus_validation.py

O runner não coleta dados externos. Ele valida a suíte offline, o manifesto
oficial e a composição atual dos módulos, deixando a coleta real para um comando
explícito do pipeline.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "tests" / "reports"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(label: str, command: Sequence[str]) -> tuple[int, str]:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode, f"### {label}\n\n```text\n{output}\n```"


def _manifest_checks() -> tuple[bool, str]:
    from src.corpus_validator import validate_manifest_contract
    from src.fetch_corpus import load_manifest

    path = ROOT / "corpus_manifest.yaml"
    manifest = load_manifest(path)
    bases = {
        str(base["base_id"]): base
        for base in manifest["bases"]
        if base.get("escopo") == "oficial"
    }
    validation = validate_manifest_contract(manifest, official=True)
    actual = set(bases)
    access_types = {base_id: (base.get("acesso") or {}).get("tipo") for base_id, base in bases.items()}
    ok = validation.ok
    details = (
        f"- arquivo: `{path.relative_to(ROOT)}`\n"
        f"- bases: `{', '.join(sorted(actual))}`\n"
        f"- tipos de acesso: `{access_types}`\n"
        f"- erros: `{list(validation.errors)}`"
    )
    return ok, details


def _architecture_checks() -> tuple[bool, list[str]]:
    source_catalog = (ROOT / "src" / "source_catalog.py").read_text(encoding="utf-8")
    source_lookup = (ROOT / "src" / "source_lookup.py").read_text(encoding="utf-8")
    provider = (ROOT / "src" / "providers" / "pncp.py").read_text(encoding="utf-8")
    fetch = (ROOT / "src" / "fetch_corpus.py").read_text(encoding="utf-8")
    prepare = (ROOT / "src" / "prepare_documents.py").read_text(encoding="utf-8")

    checks = {
        "CLI antiga removida do entrypoint": not any(
            flag in source_catalog
            for flag in ("--atualizar-md", "--salvar-json", "--baixar-arquivos")
        ),
        "source_lookup usa SourceRef/provider": "PNCPProvider" in source_lookup and "source_ref" in source_lookup,
        "PNCPProvider.search implementado": "NotImplementedError" not in provider,
        "fetch possui caminho genérico REST": "def _collect_rest_endpoint" in fetch,
        "fetch possui caminho provider": "def _collect_provider_base" in fetch,
        "prepare ainda importa compatibilidade ingest": "from src.ingest import" in prepare,
    }
    lines = [f"- {name}: `{value}`" for name, value in checks.items()]
    return all(checks.values()), lines


def main() -> int:
    started = datetime.now(timezone.utc)
    timestamp = started.astimezone().strftime("%Y%m%d-%H%M%S%z")
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / f"corpus-validation-{timestamp}.md"

    commands = [
        ("Suíte pytest", [sys.executable, "-m", "pytest", "-q", "tests"]),
        ("Compilação Python", [sys.executable, "-m", "compileall", "-q", "src", "tests"]),
        ("CLI source_catalog", [sys.executable, "-m", "src.source_catalog", "--help"]),
        ("CLI fetch_corpus", [sys.executable, "-m", "src.fetch_corpus", "--help"]),
    ]
    command_results: list[str] = []
    command_ok = True
    for label, command in commands:
        returncode, output = _run(label, command)
        command_ok = command_ok and returncode == 0
        command_results.append(f"- exit code: `{returncode}`\n\n{output}")

    manifest_ok, manifest_details = _manifest_checks()
    architecture_ok, architecture_details = _architecture_checks()
    finished = datetime.now(timezone.utc)
    status = "PASS" if command_ok and manifest_ok else "FAIL"

    report = [
        "# Validação do corpus",
        "",
        f"- status: **{status}**",
        f"- iniciado em (UTC): `{started.isoformat(timespec='seconds')}`",
        f"- finalizado em (UTC): `{finished.isoformat(timespec='seconds')}`",
        f"- duração: `{(finished - started).total_seconds():.2f}s`",
        "",
        "## Manifesto oficial v2",
        "",
        f"- resultado: **{'PASS' if manifest_ok else 'FAIL'}**",
        manifest_details,
        "",
        "## Comandos executados",
        "",
        *command_results,
        "",
        "## Evidências arquiteturais",
        "",
        *architecture_details,
        "",
        "## Interpretação",
        "",
        "A validação é offline e não baixa o corpus real. Ela confirma a suíte, o "
        "contrato do manifesto oficial v2 e os caminhos de execução presentes no código. "
        "A coleta real é executada separadamente pelo pipeline.",
        "",
    ]
    report_path.write_text("\n".join(report), encoding="utf-8")
    print(report_path.relative_to(ROOT))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
