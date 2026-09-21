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
