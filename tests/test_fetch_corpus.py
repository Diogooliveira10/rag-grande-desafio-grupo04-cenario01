from __future__ import annotations

import pytest
import hashlib
import json
from pathlib import Path

import pytest

import src.fetch_corpus as fetch_corpus
from src import fetch_corpus as fc

# Merge da branch joao-reis (2026-09-23): remover os skips quando o código entrar no main.
AGUARDA_FETCH_CORPUS_NOVO = (
    "depende da versão do fetch_corpus/manifesto da branch joao-reis (escopo oficial/experimental, status por base, promoção do staging), ainda não publicada em src/"
)


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


@pytest.mark.skip(reason=AGUARDA_FETCH_CORPUS_NOVO)
def test_manifest_declares_real_bases_and_official_pdf_target():
    manifest = fetch_corpus.load_manifest(Path("corpus_manifest.yaml"))
    bases = {base["base_id"]: base for base in manifest["bases"]}

    assert manifest["scenario_id"] == "contratacoes-rj"
    assert len(bases) >= 4
    assert bases["documentos_pncp"]["volume_alvo"] >= 100
    assert bases["documentos_pncp"]["formato"] == "pdf"
    assert bases["contratos_estruturado"]["acesso"]["tipo"] == "provider"
    assert bases["contratos_estruturado"]["acesso"]["resource_type"] == "contratacao"
    assert bases["contratos_estruturado"]["volume_alvo"] == 160
    assert bases["contratos_estruturado"]["selecao"]["limite"] == 160
    assert bases["pca_agregado"]["acesso"]["tipo"] == "provider"
    assert bases["pca_agregado"]["acesso"]["provider"] == "pncp"
    assert bases["pca_agregado"]["volume_alvo"] == 16
    assert bases["pca_agregado"]["acesso"]["params"]["anos"] == [2024, 2025]
    assert bases["atas_conversacional"]["acesso"]["tipo"] == "provider"
    assert bases["atas_conversacional"]["volume_alvo"] == 20
    assert bases["editais_normativo"]["acesso"]["urls"]
    assert all("exemplo.gov.br" not in url for url in bases["editais_normativo"]["acesso"]["urls"])


def test_manifest_covers_four_official_base_types():
    manifest = fetch_corpus.load_manifest(Path("corpus_manifest.yaml"))
    bases = {
        base["base_id"]: base
        for base in manifest["bases"]
        if base.get("escopo") == "oficial"
    }

    assert set(bases) == {
        "contratos_estruturado",
        "editais_normativo",
        "atas_conversacional",
        "pca_agregado",
    }
    assert bases["contratos_estruturado"]["acesso"]["tipo"] == "provider"
    assert bases["editais_normativo"]["acesso"]["tipo"] == "http_misto"
    assert bases["atas_conversacional"]["acesso"]["tipo"] == "provider"
    assert bases["pca_agregado"]["acesso"]["tipo"] == "provider"


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


@pytest.mark.skip(reason=AGUARDA_FETCH_CORPUS_NOVO)
def test_resource_set_collects_declared_json_and_binary_resources(monkeypatch):
    calls = []

    def fake_json(context, base, endpoint_name, endpoint):
        calls.append(("json", endpoint_name, endpoint))
        return [{"base_id": base["base_id"], "status": "ok", "path": endpoint}]

    def fake_binary(context, base, url, logical_id, filename_prefix, **kwargs):
        calls.append(("binary", logical_id, url, filename_prefix))
        return {"base_id": base["base_id"], "status": "ok", "path": url}

    monkeypatch.setattr(fetch_corpus, "_collect_rest_endpoint", fake_json)
    monkeypatch.setattr(fetch_corpus, "_collect_http_item", fake_binary)
    base = {
        "base_id": "ata_exemplo",
        "natureza": "conversacional",
        "acesso": {
            "tipo": "resource_set",
            "recursos": [
                {"id": "metadata", "tipo": "json", "url": "https://example.test/ata.json"},
                {"id": "documento", "tipo": "pdf", "url": "https://example.test/ata.pdf"},
            ],
        },
    }

    records = fetch_corpus._collect_base(object(), base)

    assert [call[0] for call in calls] == ["json", "binary"]
    assert len(records) == 2
    assert fetch_corpus._estimate_base_requests(base) == 2


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


def test_version_lock_allows_only_one_writer(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_corpus, "RAW_ROOT", tmp_path / "raw")

    first = fetch_corpus._VersionLock(4)
    second = fetch_corpus._VersionLock(4)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="já está sendo coletada"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_checkpoint_persists_and_validates_frozen_selection(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    staging = project_root / "data" / "raw" / ".staging" / "v1"
    source = staging / "base" / "item.json"
    source.parent.mkdir(parents=True)
    body = b'{"id": 1}'
    source.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    source_ref = {
        "provider": "pncp",
        "resource_type": "contratacao",
        "key": {"id": "1"},
    }
    manifest = {
        "scenario_id": "fixture",
        "bases": [{"base_id": "base", "volume_alvo": 1, "natureza": "estruturada"}],
    }
    context = fetch_corpus._VersionContext(
        manifest=manifest,
        version=1,
        manifest_hash="sha256:manifest",
        staging_path=staging,
        checkpoint_path=project_root / "data" / "inventory" / "checkpoints" / "v1.json",
        collected_at="now",
        items={
            "base:item": {
                "logical_id": "base:item",
                "base_id": "base",
                "status": "ok",
                "path": "data/raw/.staging/v1/base/item.json",
                "sha256": digest,
                "unidades_logicas": 1,
                "source_ref": source_ref,
            }
        },
        hash_index={},
        last_request_at={},
    )
    monkeypatch.setattr(fetch_corpus, "PROJECT_ROOT", project_root)

    fetch_corpus._checkpoint_write(context)
    payload = json.loads(context.checkpoint_path.read_text(encoding="utf-8"))

    assert payload["selection"]["base"][0]["stable_id"] == "pncp:contratacao:id=1"
    assert fetch_corpus._validate_checkpoint(
        payload, manifest, "sha256:manifest", 1, staging
    ).ok

    payload["selection"]["base"].append(payload["selection"]["base"][0])
    validation = fetch_corpus._validate_checkpoint(
        payload, manifest, "sha256:manifest", 1, staging
    )
    assert not validation.ok
    assert any("duplicada" in error for error in validation.errors)


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


def test_failed_http_item_is_recorded_without_raising(monkeypatch, tmp_path):
    context = fetch_corpus._VersionContext(
        manifest={"scenario_id": "teste"},
        version=1,
        manifest_hash="sha256:manifest",
        staging_path=tmp_path / "raw" / ".staging" / "v1",
        checkpoint_path=tmp_path / "inventory" / "checkpoints" / "v1.json",
        collected_at="now",
        items={},
        hash_index={},
        last_request_at={},
    )
    base = {
        "base_id": "base_teste",
        "natureza": "normativa",
        "acesso": {"rate_limit_seg": 0},
    }

    def failed_download(*args, **kwargs):
        return {"status": "falhou", "erro": "fonte indisponível"}

    monkeypatch.setattr(fetch_corpus.source_check, "download_url", failed_download)

    record = fetch_corpus._collect_http_item(
        context,
        base,
        "https://example.test/documento.pdf",
        "base_teste:url:1",
        "base_teste_001",
    )

    assert record["status"] == "falhou"
    assert record["erro"] == "fonte indisponível"
    assert context.items["base_teste:url:1"] == record
    checkpoint = json.loads(context.checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["items"]["base_teste:url:1"]["status"] == "falhou"


@pytest.mark.skip(reason=AGUARDA_FETCH_CORPUS_NOVO)
def test_manifest_separates_official_bases_from_experimental_ones():
    manifest = fetch_corpus.load_manifest(Path("corpus_manifest.yaml"))
    bases = {base["base_id"]: base for base in manifest["bases"]}

    assert {
        base_id
        for base_id, base in bases.items()
        if base["escopo"] == "oficial"
    } == {
        "contratos_estruturado",
        "editais_normativo",
        "atas_conversacional",
        "pca_agregado",
    }
    assert all(
        bases[base_id]["escopo"] == "experimental"
        for base_id in {"exemplos_teste", "respostas_api_jsonl", "licitacoes_siga_csv"}
    )


@pytest.mark.skip(reason=AGUARDA_FETCH_CORPUS_NOVO)
def test_inventory_marks_not_configured_and_partial_bases_as_incomplete():
    manifest = {
        "scenario_id": "fixture",
        "bases": [
            {
                "base_id": "atas_conversacional",
                "escopo": "oficial",
                "natureza": "conversacional",
                "volume_alvo": 1,
            },
            {
                "base_id": "contratos_estruturado",
                "escopo": "oficial",
                "natureza": "estruturada",
                "volume_alvo": 2,
            },
        ],
    }
    items = {
        "atas_conversacional:not-configured": {
            "logical_id": "atas_conversacional:not-configured",
            "base_id": "atas_conversacional",
            "status": "not_configured",
            "path": None,
            "bytes": 0,
            "sha256": None,
            "origem": "not_configured",
        },
        "contratos_estruturado:pagina-1": _item(
            "contratos_estruturado:pagina-1",
            "contratos_estruturado",
            "sha-a",
            registros=1,
        ),
    }

    inventory = fetch_corpus._build_inventory(
        manifest,
        3,
        "sha256:manifest",
        "now",
        items,
    )
    by_base = {base["base_id"]: base for base in inventory["por_base"]}

    assert by_base["atas_conversacional"]["status"] == "not_configured"
    assert by_base["contratos_estruturado"]["status"] == "partial"
    assert inventory["completo"] is False


@pytest.mark.skip(reason=AGUARDA_FETCH_CORPUS_NOVO)
def test_promotion_moves_staging_paths_to_immutable_version(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    staging = project_root / "data" / "raw" / ".staging" / "v7"
    source = staging / "contratos_estruturado" / "contrato.json"
    source.parent.mkdir(parents=True)
    body = b'{"id": 7}'
    source.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    checkpoint = project_root / "data" / "inventory" / "checkpoints" / "v7.json"
    context = fetch_corpus._VersionContext(
        manifest={"scenario_id": "fixture"},
        version=7,
        manifest_hash="sha256:manifest",
        staging_path=staging,
        checkpoint_path=checkpoint,
        collected_at="now",
        items={
            "contratos_estruturado:contrato": {
                "logical_id": "contratos_estruturado:contrato",
                "base_id": "contratos_estruturado",
                "status": "ok",
                "path": "data/raw/.staging/v7/contratos_estruturado/contrato.json",
                "source_file": "data/raw/.staging/v7/contratos_estruturado/contrato.json",
                "bytes": len(body),
                "sha256": digest,
            }
        },
        hash_index={digest: "data/raw/.staging/v7/contratos_estruturado/contrato.json"},
    )
    monkeypatch.setattr(fetch_corpus, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(fetch_corpus, "RAW_ROOT", project_root / "data" / "raw")

    fetch_corpus._promote_version(context)

    promoted = project_root / "data" / "raw" / "v7" / "contratos_estruturado" / "contrato.json"
    assert promoted.exists()
    assert not staging.exists()
    assert context.items["contratos_estruturado:contrato"]["path"] == (
        "data/raw/v7/contratos_estruturado/contrato.json"
    )
    assert context.items["contratos_estruturado:contrato"]["source_file"] == (
        "data/raw/v7/contratos_estruturado/contrato.json"
    )
    checkpoint_payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert checkpoint_payload["state"] == "promoted"


@pytest.mark.skip(reason=AGUARDA_FETCH_CORPUS_NOVO)
def test_official_run_excludes_experimental_bases(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "scenario_id: fixture\n"
        "version: \"1\"\n"
        "bases:\n"
        "  - base_id: oficial\n"
        "    escopo: oficial\n"
        "    natureza: normativa\n"
        "    acesso:\n"
        "      tipo: not_configured\n"
        "  - base_id: experimental\n"
        "    escopo: experimental\n"
        "    natureza: estruturada\n"
        "    acesso:\n"
        "      tipo: unsupported_fixture\n",
        encoding="utf-8",
    )
    data_root = tmp_path / "data"
    monkeypatch.setattr(fetch_corpus, "DATA_ROOT", data_root)
    monkeypatch.setattr(fetch_corpus, "RAW_ROOT", data_root / "raw")
    monkeypatch.setattr(fetch_corpus, "OBJECTS_ROOT", data_root / "raw" / ".objects" / "sha256")
    monkeypatch.setattr(fetch_corpus, "INVENTORY_ROOT", data_root / "inventory")
    monkeypatch.setattr(fetch_corpus, "RUNS_ROOT", data_root / "runs")
    monkeypatch.setattr(fetch_corpus, "CORPUS_INVENTORY_PATH", data_root / "CORPUS_INVENTORY.md")

    assert fetch_corpus._run_manifest_command("fetch", manifest, official=True) == 2

    inventory = json.loads(
        (data_root / "inventory" / "v1.json").read_text(encoding="utf-8")
    )
    assert [base["base_id"] for base in inventory["por_base"]] == ["oficial"]
    assert inventory["por_base"][0]["status"] == "not_configured"


# ==========================================================================
# Testes da branch marco/test-fetch-corpus (merge de 2026-09-23):
# armazenamento por conteúdo, versionamento imutável e checkpoint
# ==========================================================================

# --------------------------------------------------------------------------
# Fixture: isola cada teste numa árvore de dados própria em tmp_path
# --------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    """Redireciona PROJECT_ROOT/DATA_ROOT/etc. para uma árvore descartável.

    _relative_path() exige que tudo fique sob PROJECT_ROOT, então também
    trocamos PROJECT_ROOT (não só DATA_ROOT) para o mesmo tmp_path.
    """
    monkeypatch.setattr(fc, "PROJECT_ROOT", tmp_path)
    # _configure_data_root altera globais do módulo; registrá-las no monkeypatch
    # garante que voltem ao valor original no fim do teste e não vazem para os
    # testes seguintes.
    for nome in ("DATA_ROOT", "RAW_ROOT", "OBJECTS_ROOT", "INVENTORY_ROOT", "RUNS_ROOT", "CORPUS_INVENTORY_PATH"):
        monkeypatch.setattr(fc, nome, getattr(fc, nome))
    fc._configure_data_root(tmp_path / "data")
    return tmp_path


def _write_manifest(root, text: str):
    path = root / "corpus_manifest.yaml"
    path.write_text(text, encoding="utf-8")
    return path


MANIFEST_MINIMO = """\
scenario_id: cenario01
version: 1
bases:
  - base_id: base_a
    natureza: estruturada
    acesso:
      tipo: rest_api
      endpoint: "https://fake/v1/contratos"
      params: {}
      rate_limit_seg: 0
"""


# --------------------------------------------------------------------------
# Parsing / validação do manifesto (YAML próprio, sem pyyaml)
# --------------------------------------------------------------------------

def test_parse_manifest_yaml_le_mapa_lista_e_lista_inline():
    texto = """\
scenario_id: cenario01
version: 1
bases:
  - base_id: base_a
    natureza: estruturada
    metadados_extras: [a, b, c]
    acesso:
      tipo: rest_api
      endpoint: "https://fake"
"""
    manifest = fc._parse_manifest_yaml(texto)
    assert manifest["scenario_id"] == "cenario01"
    assert manifest["bases"][0]["base_id"] == "base_a"
    assert manifest["bases"][0]["metadados_extras"] == ["a", "b", "c"]
    assert manifest["bases"][0]["acesso"]["tipo"] == "rest_api"


def test_load_manifest_ok(project):
    path = _write_manifest(project, MANIFEST_MINIMO)
    manifest = fc.load_manifest(path)
    assert manifest["scenario_id"] == "cenario01"
    assert len(manifest["bases"]) == 1


def test_load_manifest_falha_sem_campo_obrigatorio_do_topo(project):
    texto = "version: 1\nbases:\n  - base_id: x\n    natureza: y\n    acesso: {tipo: rest_api}\n"
    path = _write_manifest(project, texto)
    with pytest.raises(ValueError, match="scenario_id"):
        fc.load_manifest(path)


def test_load_manifest_falha_com_base_id_duplicado(project):
    texto = """\
scenario_id: c
version: 1
bases:
  - base_id: dup
    natureza: estruturada
    acesso: {tipo: rest_api, endpoint: "https://fake/1"}
  - base_id: dup
    natureza: normativa
    acesso: {tipo: http_pdf, urls: ["https://fake/2"]}
"""
    path = _write_manifest(project, texto)
    with pytest.raises(ValueError, match="duplicado"):
        fc.load_manifest(path)


def test_load_manifest_falha_sem_campo_obrigatorio_da_base(project):
    texto = "scenario_id: c\nversion: 1\nbases:\n  - base_id: x\n    acesso: {tipo: rest_api}\n"
    path = _write_manifest(project, texto)
    with pytest.raises(ValueError, match="natureza"):
        fc.load_manifest(path)


# --------------------------------------------------------------------------
# Armazenamento por conteúdo (dedup por SHA-256)
# --------------------------------------------------------------------------

def _novo_contexto(project, manifest=None):
    manifest = manifest or {"scenario_id": "c", "version": 1, "bases": []}
    return fc._load_version_context(manifest, "sha256:fake", 1)


def test_save_version_bytes_deduplica_conteudo_identico(project):
    context = _novo_contexto(project)
    corpo = b'{"a": 1}'
    primeiro = fc._save_version_bytes(context, corpo, "base_a", "arq1", ".json")
    segundo = fc._save_version_bytes(context, corpo, "base_a", "arq2", ".json")

    assert primeiro["sha256"] == segundo["sha256"]
    # só um objeto físico no armazenamento de conteúdo, mesmo com dois nomes de arquivo
    objetos = list((project / "data" / "raw" / ".objects" / "sha256").rglob("*"))
    objetos_arquivo = [o for o in objetos if o.is_file() and not o.name.startswith(".")]
    assert len(objetos_arquivo) == 1


def test_save_version_bytes_conteudos_diferentes_geram_objetos_diferentes(project):
    context = _novo_contexto(project)
    a = fc._save_version_bytes(context, b"conteudo A", "base_a", "arq1", ".json")
    b = fc._save_version_bytes(context, b"conteudo B", "base_a", "arq2", ".json")
    assert a["sha256"] != b["sha256"]


# --------------------------------------------------------------------------
# Versionamento imutável
# --------------------------------------------------------------------------

def test_next_version_comeca_em_1(project):
    assert fc._next_version() == 1


def test_promote_version_nunca_sobrescreve_versao_existente(project):
    context = _novo_contexto(project)
    fc._save_version_bytes(context, b"dado", "base_a", "arq", ".json")
    fc._promote_version(context)
    assert fc._version_path(1).exists()

    # uma segunda tentativa de promover a MESMA versão precisa falhar, nunca
    # sobrescrever silenciosamente uma versão já imutável.
    outro_contexto = fc._VersionContext(
        manifest=context.manifest,
        version=1,
        manifest_hash="sha256:fake",
        staging_path=fc.RAW_ROOT / ".staging" / "v1_outro",
        checkpoint_path=fc.INVENTORY_ROOT / "checkpoints" / "v1.json",
        collected_at="2026-01-01T00:00:00+00:00",
        items={},
        hash_index={},
    )
    outro_contexto.staging_path.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        fc._promote_version(outro_contexto)


def test_promote_version_marca_checkpoint_como_promovido(project):
    context = _novo_contexto(project)
    fc._save_version_bytes(context, b"dado", "base_a", "arq", ".json")
    fc._promote_version(context)
    checkpoint = json.loads(context.checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["state"] == "promoted"


# --------------------------------------------------------------------------
# Checkpoint / resumibilidade / isolamento por manifest_hash
# --------------------------------------------------------------------------

def test_load_version_context_resume_com_mesmo_manifest_hash(project):
    manifest = {"scenario_id": "c", "version": 1, "bases": []}
    context1 = fc._load_version_context(manifest, "sha256:abc", 1)
    saved = fc._save_version_bytes(context1, b"dado", "base_a", "arq", ".json")
    fc._base_record(context1, {"base_id": "base_a"}, "logical_1", "origin", saved, tipo="json")

    context2 = fc._load_version_context(manifest, "sha256:abc", 1)
    assert context2.items  # itens do checkpoint anterior foram carregados
    assert "logical_1" in context2.items
    assert context2.staging_path == context1.staging_path


def test_load_version_context_rejeita_manifest_diferente_no_mesmo_checkpoint(project):
    manifest = {"scenario_id": "c", "version": 1, "bases": []}
    fc._load_version_context(manifest, "sha256:abc", 1)

    with pytest.raises(RuntimeError, match="manifest_hash incompatível"):
        fc._load_version_context(manifest, "sha256:DIFERENTE", 1)


def test_load_version_context_recusa_staging_sem_checkpoint(project):
    staging = fc.RAW_ROOT / ".staging" / "v1"
    staging.mkdir(parents=True)
    (staging / "arquivo_orfao.json").write_text("{}", encoding="utf-8")

    manifest = {"scenario_id": "c", "version": 1, "bases": []}
    with pytest.raises(RuntimeError, match="recuse sobrescrever"):
        fc._load_version_context(manifest, "sha256:abc", 1)


# --------------------------------------------------------------------------
# Coleta local (arquivo_local) — sem rede
# --------------------------------------------------------------------------

def test_collect_base_arquivo_local_e_idempotente(project):
    origem = project / "fonte_local.json"
    origem.write_text('{"x": 1}', encoding="utf-8")
    base = {
        "base_id": "base_local",
        "natureza": "estruturada",
        "acesso": {"tipo": "arquivo_local", "origem": "fonte_local.json"},
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    primeira = fc._collect_base(context, base)
    assert primeira[0]["status"] == "ok"

    # segunda chamada: item já completo, não deve reprocessar (mesmo objeto de registro)
    segunda = fc._collect_base(context, base)
    assert segunda[0] is primeira[0]


def test_collect_base_arquivo_local_ausente_gera_falha_sem_lancar(project):
    base = {
        "base_id": "base_local",
        "natureza": "estruturada",
        "acesso": {"tipo": "arquivo_local", "origem": "nao_existe.json"},
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})
    registros = fc._collect_base(context, base)
    assert registros[0]["status"] == "falhou"


# --------------------------------------------------------------------------
# Coleta HTTP (http_pdf) — source_catalog.download_url mockado
# --------------------------------------------------------------------------

def test_collect_http_item_sucesso_e_idempotente(project, monkeypatch):
    arquivo_baixado = project / "download_tmp.pdf"
    arquivo_baixado.write_bytes(b"%PDF-conteudo")

    chamadas = {"n": 0}

    def fake_download_url(url, destination_dir, filename_prefix, **kwargs):
        chamadas["n"] += 1
        return {"status": "baixado", "arquivo": str(arquivo_baixado), "content_type": "application/pdf"}

    monkeypatch.setattr(fc.source_check, "download_url", fake_download_url)

    base = {"base_id": "base_pdf", "natureza": "normativa", "acesso": {"tipo": "http_pdf", "urls": ["https://fake/lei.pdf"]}}
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_base(context, base)
    assert registros[0]["status"] == "ok"
    assert chamadas["n"] == 1

    # roda de novo: item já concluído, não deve baixar outra vez
    fc._collect_base(context, base)
    assert chamadas["n"] == 1


def test_collect_http_item_falha_de_download_nao_lanca_excecao(project, monkeypatch):
    def fake_download_url(url, destination_dir, filename_prefix, **kwargs):
        return {"status": "indisponivel", "erro": "timeout simulado"}

    monkeypatch.setattr(fc.source_check, "download_url", fake_download_url)

    base = {"base_id": "base_pdf", "natureza": "normativa", "acesso": {"tipo": "http_pdf", "urls": ["https://fake/lei.pdf"]}}
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_base(context, base)
    assert registros[0]["status"] == "falhou"


# --------------------------------------------------------------------------
# Coleta REST paginada — source_catalog.request_url mockado
# --------------------------------------------------------------------------

def test_collect_rest_endpoint_pagina_ate_pagina_incompleta(project, monkeypatch):
    paginas = {
        1: {"json": {"data": [{"id": 1}, {"id": 2}], "totalPaginas": None}},
        2: {"json": {"data": [{"id": 3}]}},  # menor que o tamanho de página -> última
    }
    chamadas = {"pagina_vista": []}

    def fake_request_url(url, kind, **kwargs):
        pagina = int(url.rsplit("pagina=", 1)[1].split("&")[0])
        chamadas["pagina_vista"].append(pagina)
        return paginas[pagina]

    monkeypatch.setattr(fc.source_check, "request_url", fake_request_url)

    base = {
        "base_id": "base_rest",
        "natureza": "estruturada",
        "acesso": {
            "tipo": "rest_api",
            "endpoint": "https://fake/v1/contratos",
            # "pagina" precisa estar em params para o fetch_corpus.py detectar
            # paginação automaticamente (ver _collect_rest_endpoint).
            "params": {"tamanhoPagina": 2, "pagina": 1},
            "rate_limit_seg": 0,
        },
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_rest_endpoint(context, base, "principal", base["acesso"]["endpoint"])

    assert chamadas["pagina_vista"] == [1, 2]
    assert [r["registros"] for r in registros] == [2, 1]
    assert all(r["status"] == "ok" for r in registros)


def test_collect_rest_endpoint_erro_de_rede_gera_falha_sem_lancar(project, monkeypatch):
    def fake_request_url(url, kind, **kwargs):
        raise ConnectionError("PNCP fora do ar")

    monkeypatch.setattr(fc.source_check, "request_url", fake_request_url)

    base = {
        "base_id": "base_rest",
        "natureza": "estruturada",
        "acesso": {"tipo": "rest_api", "endpoint": "https://fake/v1/contratos", "params": {}, "rate_limit_seg": 0},
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_rest_endpoint(context, base, "principal", base["acesso"]["endpoint"])
    assert registros[0]["status"] == "falhou"
    assert "ConnectionError" in registros[0]["erro"]


def test_collect_rest_endpoint_com_placeholder_falha_sem_chamar_rede(project, monkeypatch):
    chamou = {"n": 0}

    def fake_request_url(url, kind, **kwargs):
        chamou["n"] += 1
        return {"json": {"data": []}}

    monkeypatch.setattr(fc.source_check, "request_url", fake_request_url)

    base = {
        "base_id": "base_rest",
        "natureza": "estruturada",
        "acesso": {"tipo": "rest_api", "endpoint": "https://fake/v1/orgaos/{cnpj}/pca", "params": {}},
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_rest_endpoint(context, base, "principal", base["acesso"]["endpoint"])
    assert registros[0]["status"] == "falhou"
    assert "placeholders" in registros[0]["erro"]
    assert chamou["n"] == 0