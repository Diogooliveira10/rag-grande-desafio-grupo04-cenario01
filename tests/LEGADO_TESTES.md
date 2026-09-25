# Testes legados arquivados

Este arquivo preserva fontes antigas que dependiam de contratos, caminhos ou módulos removidos. Elas não fazem parte da suíte executável e não devem ser copiadas para os testes ativos sem adaptação ao código atual.

Os documentos JSON antigos de `testes/fixtures/` foram mantidos na pasta `tests/fixtures/`; os 20 arquivos têm o mesmo conteúdo. O manifesto smoke e o log de extração eram artefatos da validação antiga e não são necessários para executar os testes atuais.

## Suíte antiga de coleta do corpus — `testes/test_fetch_corpus.py`

```python
import hashlib
import json
from pathlib import Path

import src.fetch_corpus as fetch_corpus


def _item(logical_id: str, base_id: str, sha256: str, tipo: str = "pdf", **extra) -> dict:
    return {
        "logical_id": logical_id,
        "base_id": base_id,
        "natureza": "normativa",
        "path": f"data/raw/v2/{base_id}/{logical_id.replace(':', '_')}.{tipo}",
        "tipo": tipo,
        "bytes": 10,
        "sha256": sha256,
        "origem": f"https://example.test/{logical_id}",
        "status": "ok",
        **extra,
    }


def _fixture_documents() -> list[dict]:
    fixture_dir = Path(__file__).parent / "fixtures"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(fixture_dir.glob("documento_*.json"))
    ]


def test_manifest_declares_real_bases_and_official_pdf_target():
    manifest = fetch_corpus.load_manifest(Path("corpus_manifest.yaml"))
    bases = {base["base_id"]: base for base in manifest["bases"]}

    assert manifest["scenario_id"] == "contratacoes-rj"
    assert len(bases) >= 4
    assert bases["documentos_pncp"]["volume_alvo"] >= 100
    assert bases["documentos_pncp"]["formato"] == "pdf"
    assert bases["editais_normativo"]["acesso"]["urls"]
    assert all("exemplo.gov.br" not in url for url in bases["editais_normativo"]["acesso"]["urls"])


def test_fixture_contract_has_at_least_twenty_offline_documents():
    fixture_documents = _fixture_documents()
    assert len(fixture_documents) >= 20
    required = {"id", "base_id", "doc_type", "sensitivity", "text"}
    assert all(required <= set(document) for document in fixture_documents)


def test_inventory_aggregates_by_semantic_base_and_format():
    manifest = {
        "scenario_id": "fixture",
        "bases": [
            {"base_id": "documentos_pncp", "natureza": "normativa", "volume_alvo": 2},
            {"base_id": "respostas_api_jsonl", "natureza": "conversacional", "volume_alvo": 1},
        ],
    }
    items = {
        "documentos_pncp:a": _item("documentos_pncp:a", "documentos_pncp", "sha-a"),
        "documentos_pncp:b": _item("documentos_pncp:b", "documentos_pncp", "sha-b"),
        "respostas_api_jsonl:a": _item(
            "respostas_api_jsonl:a", "respostas_api_jsonl", "sha-c", tipo="jsonl", registros=3
        ),
    }

    inventory = fetch_corpus._build_inventory(manifest, 2, "sha256:manifest", "now", items)
    by_base = {base["base_id"]: base for base in inventory["por_base"]}

    assert by_base["documentos_pncp"]["formatos"] == {"pdf": 2}
    assert by_base["documentos_pncp"]["divergencia_alvo"] == 0
    assert by_base["respostas_api_jsonl"]["itens"] == 3
    assert all(record["base_id"] != "pdf" for record in inventory["arquivos"])


def test_diff_uses_logical_id_and_reports_four_categories(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_corpus, "INVENTORY_ROOT", tmp_path)
    previous = {
        "version": "v1",
        "arquivos": [
            _item("base:identico", "base", "same"),
            _item("base:alterado", "base", "old"),
            _item("base:removido", "base", "gone"),
        ],
    }
    current = {
        "version": "v2",
        "arquivos": [
            _item("base:identico", "base", "same"),
            _item("base:alterado", "base", "new"),
            _item("base:adicionado", "base", "new-item"),
        ],
    }

    diff_path = fetch_corpus._diff_inventories(previous, current)
    diff = diff_path.read_text(encoding="utf-8")

    assert "Adicionados: **1**" in diff
    assert "Removidos: **1**" in diff
    assert "Alterados: **1**" in diff
    assert "Idênticos: **1**" in diff


def test_checkpoint_and_hash_deduplication_are_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_corpus, "PROJECT_ROOT", tmp_path)
    completed_path = tmp_path / "data" / "raw" / "v2" / "base" / "item.pdf"
    completed_path.parent.mkdir(parents=True)
    completed_path.write_bytes(b"completed")
    context = fetch_corpus._VersionContext(
        manifest={},
        version=2,
        manifest_hash="sha256:manifest",
        staging_path=tmp_path / "staging" / "v2",
        checkpoint_path=tmp_path / "checkpoint.json",
        collected_at="now",
        items={
            "base:item": {
                "status": "ok",
                "logical_id": "base:item",
                "path": "data/raw/v2/base/item.pdf",
            }
        },
        hash_index={},
        last_request_at={},
    )

    first = fetch_corpus._save_version_bytes(context, b"same", "base", "doc", ".pdf")
    second = fetch_corpus._save_version_bytes(context, b"same", "base", "other", ".pdf")

    completed = fetch_corpus._completed_item(context, "base:item")
    assert completed is not None
    assert completed["status"] == "ok"
    assert fetch_corpus._completed_item(context, "missing") is None
    assert first["path"] != second["path"]
    assert first["sha256"] == second["sha256"]
    assert first["storage"]["object_path"] == second["storage"]["object_path"]
    assert (tmp_path / first["path"]).exists()
    assert (tmp_path / second["path"]).exists()
    assert len(list((tmp_path / "staging" / "v2" / "base").glob("*"))) == 2
    assert len(list((tmp_path / "data" / "raw" / ".objects" / "sha256").rglob("*"))) == 2


def test_update_reuses_previous_record_through_object_store(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_corpus, "PROJECT_ROOT", tmp_path)
    previous_path = tmp_path / "data" / "raw" / "v1" / "base" / "item.pdf"
    previous_path.parent.mkdir(parents=True)
    previous_path.write_bytes(b"previous content")
    digest = hashlib.sha256(b"previous content").hexdigest()
    context = fetch_corpus._VersionContext(
        manifest={},
        version=2,
        manifest_hash="sha256:manifest",
        staging_path=tmp_path / "data" / "raw" / ".staging" / "v2",
        checkpoint_path=tmp_path / "checkpoint.json",
        collected_at="now",
        items={},
        hash_index={},
        previous_items={
            "base:item": {
                "status": "ok",
                "path": "data/raw/v1/base/item.pdf",
                "sha256": digest,
            }
        },
        previous_version=1,
    )

    saved = fetch_corpus._materialize_previous_record(context, "base", "item", context.previous_items["base:item"], ".pdf")

    assert saved is not None
    assert saved["reutilizado_de"] == "v1"
    assert saved["sha256"] == digest
    assert (tmp_path / saved["path"]).read_bytes() == b"previous content"
    assert (tmp_path / saved["storage"]["object_path"]).exists()


def test_fetch_resumes_matching_staging_checkpoint(monkeypatch, tmp_path):
    inventory_root = tmp_path / "inventory"
    raw_root = tmp_path / "raw"
    checkpoint_dir = inventory_root / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    (raw_root / ".staging" / "v9").mkdir(parents=True)
    (checkpoint_dir / "v9.json").write_text(
        json.dumps({"manifest_hash": "sha256:target", "state": "staging"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fetch_corpus, "INVENTORY_ROOT", inventory_root)
    monkeypatch.setattr(fetch_corpus, "RAW_ROOT", raw_root)

    assert fetch_corpus._find_resumable_version("sha256:target") == 9

    (checkpoint_dir / "v9.json").write_text(
        json.dumps({"manifest_hash": "sha256:target", "state": "promoted"}),
        encoding="utf-8",
    )
    assert fetch_corpus._find_resumable_version("sha256:target") is None


def test_data_root_isolates_raw_inventory_logs_and_report(monkeypatch, tmp_path):
    original = {
        name: getattr(fetch_corpus, name)
        for name in ("DATA_ROOT", "RAW_ROOT", "OBJECTS_ROOT", "INVENTORY_ROOT", "RUNS_ROOT", "CORPUS_INVENTORY_PATH")
    }
    isolated = tmp_path / "attempts" / "v-test"
    fetch_corpus._configure_data_root(isolated)

    assert fetch_corpus.DATA_ROOT == isolated
    assert fetch_corpus.RAW_ROOT == isolated / "raw"
    assert fetch_corpus.OBJECTS_ROOT == isolated / "raw" / ".objects" / "sha256"
    assert fetch_corpus.INVENTORY_ROOT == isolated / "inventory"
    assert fetch_corpus.RUNS_ROOT == isolated / "runs"
    assert fetch_corpus.CORPUS_INVENTORY_PATH == isolated / "CORPUS_INVENTORY.md"

    for name, value in original.items():
        monkeypatch.setattr(fetch_corpus, name, value)


def test_cli_requires_explicit_official_or_experimental_root():
    try:
        fetch_corpus._manifest_main(["inventory", "--corpus-version", "v1"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("a CLI deve exigir --official ou --data-root")


def test_cli_rejects_legacy_options_unknown_commands_and_missing_command():
    for arguments in (["--quantidade", "2"], ["fetc"], []):
        try:
            fetch_corpus.main(arguments)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"CLI aceitou argumentos inválidos: {arguments}")


def test_cli_routes_manifest_commands_and_keeps_corpus_version_option(monkeypatch, tmp_path):
    configured_roots = []
    manifest_calls = []
    inventory_calls = []
    monkeypatch.setattr(fetch_corpus, "_configure_data_root", configured_roots.append)
    monkeypatch.setattr(
        fetch_corpus,
        "_run_manifest_command",
        lambda command, manifest_path, *, official: manifest_calls.append((command, manifest_path, official)) or 0,
    )
    monkeypatch.setattr(
        fetch_corpus,
        "_inventory_for_version",
        lambda version: inventory_calls.append(version) or {"version": f"v{version}", "totais": {}},
    )

    assert fetch_corpus._manifest_main(["fetch", "--manifest", "manifest.yaml", "--official"]) == 0
    assert fetch_corpus._manifest_main(["update", "--manifest", "manifest.yaml", "--data-root", str(tmp_path)]) == 0
    assert fetch_corpus._manifest_main(["inventory", "--corpus-version", "v2", "--data-root", str(tmp_path)]) == 0

    assert [call[0] for call in manifest_calls] == ["fetch", "update"]
    assert all(call[2] is False for call in manifest_calls[1:])
    assert inventory_calls == [2]
    assert configured_roots


def test_api_request_normalizes_manifest_iso_dates():
    url = fetch_corpus._build_request_url(
        "https://example.test/api",
        {"dataInicial": "2025-01-01", "dataFinal": "2025-01-31", "pagina": 2},
    )
    assert "dataInicial=20250101" in url
    assert "dataFinal=20250131" in url
    assert "pagina=2" in url


def test_run_logger_writes_run_id_stage_status_and_timestamp(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_corpus, "RUNS_ROOT", tmp_path)
    logger = fetch_corpus._RunLogger("test")
    logger.event("inventory", "completed", files=20)

    record = json.loads(logger.path.read_text(encoding="utf-8").splitlines()[0])
    assert record["run_id"] == logger.run_id
    assert record["stage"] == "inventory"
    assert record["status"] == "completed"
    assert record["files"] == 20
    assert record["timestamp"]
```

## Demonstração antiga de extração — `testes/teste_extração.py`

```python
"""Demonstração offline da extração manifest-driven para apresentação.

Este teste não acessa a internet nem altera ``data/raw``. Ele verifica o contrato
estrutural da extração e salva um log legível em ``testes/output_teste_extracao.log``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fetch_corpus import load_manifest  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "testes" / "output_teste_extracao.log"
_CURRENT_RUN_ID = "offline-not-started"


def _log(lines: list[str], level: str, message: str, **fields: object) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = fields.pop("run_id", _CURRENT_RUN_ID)
    stage = fields.pop("stage", "offline_validation")
    suffix = f" | run_id={run_id} | stage={stage}" + "".join(
        f" | {key}={value}" for key, value in fields.items()
    )
    lines.append(f"{timestamp} [{level}] {message}{suffix}")


def _tracked_raw_files() -> int | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "data/raw"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()])


def run(output_path: Path = DEFAULT_OUTPUT) -> int:
    global _CURRENT_RUN_ID
    _CURRENT_RUN_ID = f"offline-extraction-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    lines: list[str] = []
    _log(lines, "INFO", "Início do teste offline da extração")
    manifest_path = PROJECT_ROOT / "corpus_manifest.yaml"

    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:  # pragma: no cover - mensagem para apresentação
        _log(lines, "ERRO", "Manifesto inválido", erro=f"{type(exc).__name__}: {exc}")
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 1

    _log(
        lines,
        "OK",
        "Manifesto carregado como fonte única de verdade",
        arquivo=manifest_path.relative_to(PROJECT_ROOT),
        scenario_id=manifest["scenario_id"],
        versao=manifest["version"],
    )

    for base in manifest["bases"]:
        acesso = base["acesso"]
        _log(
            lines,
            "INFO",
            "Base declarada no manifesto",
            base_id=base["base_id"],
            natureza=base["natureza"],
            acesso=acesso.get("tipo"),
            volume_alvo=base.get("volume_alvo"),
            rate_limit_seg=acesso.get("rate_limit_seg", 0),
        )
        _log(
            lines,
            "INFO",
            "Parâmetros da base inspecionados",
            base_id=base["base_id"],
            parametros=acesso.get("params", "não declarados"),
            paginacao=acesso.get("paginacao", "não declarada"),
            endpoints=list((acesso.get("endpoints") or {}).keys()) or "não declarados",
            origem_local=acesso.get("origem", "não aplicável"),
        )

    _log(lines, "INFO", "Contrato operacional dos comandos")
    for command, command_line, meaning in (
        ("fetch", "python -m src.fetch_corpus fetch --official", "materializa uma nova versão a partir do manifesto"),
        ("update", "python -m src.fetch_corpus update --official", "materializa a próxima versão e calcula o diff"),
        ("inventory", "python -m src.fetch_corpus inventory --corpus-version v1 --official", "reconstrói ou valida inventário sem baixar dados"),
    ):
        _log(
            lines,
            "INFO",
            "Comando oficial",
            comando=command_line,
            objetivo=meaning,
        )
    _log(
        lines,
        "INFO",
        "Comando experimental",
        comando="python -m src.fetch_corpus fetch --manifest corpus_manifest.yaml --data-root data/attempts/corpus_fetch/tentativa-vN",
        objetivo="testa uma coleta isolada sem alterar a v1 oficial",
    )

    _log(
        lines,
        "INFO",
        "Conceito: idempotência",
        explicacao="reexecutar o mesmo manifesto completo reutiliza a versão existente e não duplica conteúdo",
    )
    _log(
        lines,
        "INFO",
        "Conceito: retomada",
        explicacao="o checkpoint registra cada logical_id concluído para continuar após uma interrupção",
    )
    _log(
        lines,
        "INFO",
        "Conceito: imutabilidade",
        explicacao="a coleta ocorre em data/raw/.staging/vN e só é promovida para data/raw/vN ao finalizar",
    )

    source = PROJECT_ROOT / "src" / "fetch_corpus.py"
    source_text = source.read_text(encoding="utf-8")
    for command in ("fetch", "update", "inventory"):
        found = f'"{command}"' in source_text
        _log(lines, "OK" if found else "ERRO", "Subcomando verificado", comando=command, encontrado=found)

    inventory: dict[str, Any] = {}
    inventory_root = PROJECT_ROOT / "data" / "inventory"
    inventory_paths = sorted(
        inventory_root.glob("v*.json"),
        key=lambda path: int(path.stem[1:]) if path.stem[1:].isdigit() else -1,
    )
    inventory_path = inventory_paths[-1] if inventory_paths else inventory_root / "v1.json"
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        _log(
            lines,
            "OK",
            "Inventário de versão encontrado",
            arquivo=inventory_path.relative_to(PROJECT_ROOT),
            tipo=inventory.get("inventory_type"),
            oficial=inventory.get("official"),
            vinculado_ao_manifesto=inventory.get("manifest_bound"),
            manifest_hash=inventory.get("manifest_hash") or "não aplicável (legado)",
            arquivos=inventory.get("totais", {}).get("arquivos"),
            itens=inventory.get("totais", {}).get("itens"),
            falhas=inventory.get("totais", {}).get("falhas"),
        )
        required_inventory_fields = {
            "scenario_id",
            "version",
            "coletado_em",
            "manifest_hash",
            "inventory_type",
            "official",
            "manifest_bound",
            "totais",
            "por_base",
            "arquivos",
            "falhas",
        }
        missing_inventory_fields = sorted(required_inventory_fields - set(inventory))
        _log(
            lines,
            "OK" if not missing_inventory_fields else "ERRO",
            "Contrato do inventário verificado",
            campos_ausentes=missing_inventory_fields or "nenhum",
            registros=len(inventory.get("arquivos", [])),
            bases=len(inventory.get("por_base", [])),
        )
        for base_summary in inventory.get("por_base", []):
            _log(
                lines,
                "INFO",
                "Resumo da base no inventário",
                base_id=base_summary.get("base_id"),
                arquivos=base_summary.get("arquivos"),
                itens=base_summary.get("itens"),
                formatos=base_summary.get("formatos"),
                bytes=base_summary.get("bytes"),
                volume_observado=base_summary.get("volume_observado"),
                divergencia_alvo=base_summary.get("divergencia_alvo"),
            )
        if not inventory.get("manifest_hash"):
            _log(
                lines,
                "ATENÇÃO",
                "Inventário legado não está vinculado ao manifesto",
                explicacao="as versões oficiais produzidas pelo fetch possuem hash do manifesto",
            )
    else:
        _log(lines, "ATENÇÃO", "Inventário v1 ainda não existe; execute inventory --corpus-version v1 --official")

    diff_files = sorted(path.name for path in inventory_root.glob("DIFF_*.md")) if inventory_root.exists() else []
    checkpoint_files = sorted(path.name for path in (inventory_root / "checkpoints").glob("v*.json")) if (inventory_root / "checkpoints").exists() else []
    _log(lines, "INFO", "Artefatos auxiliares localizados", diffs=diff_files or "nenhum", checkpoints=checkpoint_files or "nenhum")

    raw_path = PROJECT_ROOT / "data" / "raw"
    ignored = "data/raw/" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    tracked = _tracked_raw_files()
    _log(lines, "OK" if ignored else "ERRO", "Regra de armazenamento bruto verificada", gitignore_data_raw=ignored)
    _log(lines, "ATENÇÃO" if tracked else "OK", "Arquivos brutos já rastreados no Git", quantidade=tracked if tracked is not None else "não verificado")
    _log(lines, "OK" if raw_path.exists() else "ATENÇÃO", "Diretório de dados brutos verificado", existe=raw_path.exists())
    raw_files = len([path for path in raw_path.rglob("*") if path.is_file()]) if raw_path.exists() else 0
    _log(lines, "INFO", "Contagem física do armazenamento bruto", arquivos=raw_files, observacao="arquivos existentes não são alterados por este teste")

    _log(lines, "INFO", "Teste concluído sem downloads e sem alteração de data/raw")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"log={output_path.relative_to(PROJECT_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    return run(output_path)


if __name__ == "__main__":
    raise SystemExit(main())
```

## Atalho antigo para verificação de fontes — `testes/test-fontes/testar_fontes.py`

```python
"""Entrada curta para a checagem das fontes do cenário 01."""

try:
    from testar_fontes_cenario01 import main
except ImportError:  # permite import como pacote
    from .testar_fontes_cenario01 import main


if __name__ == "__main__":
    raise SystemExit(main())
```

## Compatibilidade antiga com a verificação de fontes — arquivo local não versionado

Este código estava em um backup local ignorado pelo Git. Foi preservado aqui antes da remoção da pasta antiga.

```python
"""Compatibilidade para os scripts antigos de verificação de fontes.

A implementação de produção agora está em ``src.source_catalog``. Este arquivo
mantém o comando legado funcionando sem duplicar a lógica de coleta.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import source_catalog as _source_catalog  # noqa: E402


for _name in dir(_source_catalog):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_source_catalog, _name)

__all__ = [name for name in dir(_source_catalog) if not name.startswith("__")]


if __name__ == "__main__":
    raise SystemExit(_source_catalog.main())
```
