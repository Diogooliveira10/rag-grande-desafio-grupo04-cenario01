import pytest
import json
from pathlib import Path

from src import manifest_curator

# Merge da branch joao-reis (2026-09-23): remover os skips quando o código entrar no main.
def _manifest(path: Path) -> None:
    path.write_text(
        """scenario_id: teste\nversion: \"2026-09-18\"\nbases:\n  - base_id: existente\n    natureza: normativa\n    fonte: \"Fonte existente\"\n    acesso:\n      tipo: http_pdf\n      urls: [\"https://example.test/existente.pdf\"]\n    volume_alvo: 1\n    formato: pdf\n""",
        encoding="utf-8",
    )


def test_build_proposal_only_returns_undeclared_http_sources(tmp_path):
    manifest_path = tmp_path / "corpus_manifest.yaml"
    _manifest(manifest_path)
    discovery = {
        "checked_at": "2026-09-18T12:00:00+00:00",
        "sources": [
            {"source_id": "existente", "nome": "Existente", "url": "https://example.test/existente.pdf", "file_type": "pdf"},
            {"source_id": "nova_fonte", "nome": "Nova fonte", "url": "https://example.test/nova.pdf", "file_type": "pdf"},
        ],
    }

    proposal = manifest_curator.build_proposal(manifest_path, discovery)

    assert len(proposal["proposals"]) == 1
    assert proposal["proposals"][0]["base_id"] == "proposta_nova_fonte"
    assert proposal["current_manifest_hash"] == manifest_curator.manifest_hash(manifest_path)


def test_build_proposal_preserves_provider_source_ref(tmp_path):
    manifest_path = tmp_path / "corpus_manifest.yaml"
    _manifest(manifest_path)
    proposal = manifest_curator.build_proposal(
        manifest_path,
        {
            "sources": [
                {
                    "source_id": "pncp:contratacao:one",
                    "nome": "Contratação PNCP",
                    "file_type": "json",
                    "source_ref": {
                        "provider": "pncp",
                        "resource_type": "contratacao",
                        "key": {"cnpj": "42", "ano": 2025, "sequencial": 1},
                    },
                }
            ]
        },
    )

    item = proposal["proposals"][0]
    assert item["source_ref"]["provider"] == "pncp"
    assert item["suggested_manifest_entry"]["acesso"]["tipo"] == "provider"
    assert item["suggested_manifest_entry"]["acesso"]["sources"][0]["key"]["sequencial"] == 1


def test_manifest_diff_is_non_mutating(tmp_path):
    manifest_path = tmp_path / "corpus_manifest.yaml"
    _manifest(manifest_path)
    before = manifest_path.read_bytes()
    proposal = manifest_curator.build_proposal(
        manifest_path,
        {"sources": [{"source_id": "nova", "url": "https://example.test/nova.json", "file_type": "json"}]},
    )

    diff = manifest_curator.manifest_diff(manifest_path, proposal)

    assert '+  - base_id: "proposta_nova"' in diff
    assert manifest_path.read_bytes() == before


def test_apply_requires_current_manifest_hash_and_validates_yaml(tmp_path):
    manifest_path = tmp_path / "corpus_manifest.yaml"
    _manifest(manifest_path)
    proposal = manifest_curator.build_proposal(
        manifest_path,
        {"sources": [{"source_id": "nova", "url": "https://example.test/nova.json", "file_type": "json"}]},
    )

    manifest_curator.apply_proposal(manifest_path, proposal, new_version="2026-09-19")
    updated = manifest_path.read_text(encoding="utf-8")
    assert 'version: "2026-09-19"' in updated
    assert "proposta_nova" in updated

    stale = {**proposal, "current_manifest_hash": "sha256:stale"}
    try:
        manifest_curator.apply_proposal(manifest_path, stale)
    except ValueError as exc:
        assert "manifesto mudou" in str(exc)
    else:
        raise AssertionError("apply aceitou uma proposta com hash obsoleto")


def test_discovery_snapshot_is_serializable(tmp_path, monkeypatch):
    class FakeConfig:
        ano = 2025
        uf = "RJ"
        modalidade = 1
        tamanho_pagina = 10

    monkeypatch.setattr(
        "src.source_catalog.run_checks",
        lambda config: (
            "2026-09-18T12:00:00+00:00",
            [{"id": "nova", "nome": "Nova", "url": "https://example.test/nova.json", "tipo": "json"}],
        ),
    )

    payload = manifest_curator.discover(FakeConfig())
    output = tmp_path / "discovery.json"
    manifest_curator._write_json(payload, output)

    assert json.loads(output.read_text(encoding="utf-8"))["sources"][0]["source_id"] == "nova"
