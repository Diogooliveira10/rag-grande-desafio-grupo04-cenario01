import hashlib
import json
from pathlib import Path

import pytest

from src.ingest import chunk_text, ingest, sha256_file
from src.prepare_documents import prepare_documents


@pytest.mark.parametrize(
    ("max_chars", "overlap"),
    [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 11)],
)
def test_chunk_text_rejects_invalid_bounds(max_chars: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        list(chunk_text("texto", max_chars=max_chars, overlap=overlap))


def test_chunk_text_is_deterministic_and_respects_maximum() -> None:
    text = "A" * 18 + "\n\n" + "B" * 18 + "\n\n" + "C" * 18

    first = list(chunk_text(text, max_chars=20, overlap=4))
    second = list(chunk_text(text, max_chars=20, overlap=4))

    assert first == second
    assert first
    assert all(0 < len(chunk) <= 20 for chunk in first)


def test_sha256_file_matches_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "documento.txt"
    path.write_bytes("conteúdo com acentuação".encode("utf-8"))

    assert sha256_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_ingest_delegates_to_prepare_documents_and_writes_report(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "documento.txt").write_text(
        "Primeiro parágrafo.\n\nSegundo parágrafo.", encoding="utf-8"
    )

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """scenario_id: teste-ingest
version: "1"
bases:
  - base_id: base_teste
    natureza: normativa
    fonte: fixture local
    acesso:
      tipo: arquivo_local
""",
        encoding="utf-8",
    )
    output = tmp_path / "documents.jsonl"
    report = tmp_path / "extraction_report.json"

    summary = ingest(
        input_dir,
        output,
        max_chars=1200,
        overlap=150,
        manifest_path=manifest,
        report_file=report,
        base_id="base_teste",
        allow_uninventoried=True,
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert summary["documentos"] == 1
    assert summary["chunks"] == 1
    assert summary["falhas"] == 0
    assert report.exists()
    assert records[0]["base_id"] == "base_teste"
    assert records[0]["doc_type"] == "txt"
    assert records[0]["text"].startswith("Primeiro parágrafo")


def test_ingest_requires_inventory_by_default(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "documento.txt").write_text("texto", encoding="utf-8")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """scenario_id: teste-ingest-required
version: "1"
bases:
  - base_id: base_teste
    natureza: normativa
    fonte: fixture local
    acesso:
      tipo: arquivo_local
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exige --inventory"):
        ingest(
            input_dir,
            tmp_path / "documents.jsonl",
            max_chars=1200,
            overlap=150,
            manifest_path=manifest,
            base_id="base_teste",
        )


def test_ingest_processes_only_inventory_files(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    listed = input_dir / "listed.txt"
    listed.write_text("arquivo listado", encoding="utf-8")
    (input_dir / "not-listed.txt").write_text("arquivo fora do inventário", encoding="utf-8")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """scenario_id: teste-ingest-inventory
version: "1"
bases:
  - base_id: base_teste
    escopo: oficial
    natureza: normativa
    fonte: fixture local
    acesso:
      tipo: arquivo_local
""",
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "arquivos": [
                    {
                        "base_id": "base_teste",
                        "status": "ok",
                        "source_file": str(listed),
                        "path": str(listed),
                        "sha256": hashlib.sha256(listed.read_bytes()).hexdigest(),
                        "origem": "fixture://listed.txt",
                        "coletado_em": "2026-09-23T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "documents.jsonl"
    summary = ingest(
        input_dir,
        output,
        max_chars=1200,
        overlap=150,
        manifest_path=manifest,
        inventory_path=inventory,
        base_id="base_teste",
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert summary["arquivos"] == 1
    assert len(records) == 1
    assert records[0]["text"] == "arquivo listado"


def test_prepare_documents_reports_invalid_json_and_ignores_unsupported(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "valido.txt").write_text("texto válido", encoding="utf-8")
    (input_dir / "quebrado.json").write_text("{ inválido", encoding="utf-8")
    (input_dir / "anexo.bin").write_bytes(b"unsupported")

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """scenario_id: teste-ingest
version: "1"
bases:
  - base_id: base_teste
    natureza: normativa
    fonte: fixture local
    acesso:
      tipo: arquivo_local
""",
        encoding="utf-8",
    )
    output = tmp_path / "documents.jsonl"
    report = tmp_path / "extraction_report.json"

    summary = prepare_documents(
        input_dir,
        output,
        report,
        manifest,
        base_id="base_teste",
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert summary["ignorados"] == 1
    assert summary["documentos"] == 2
    assert summary["chunks"] == 1
    assert summary["status_extracao"]["json_invalido"] == 1
    assert any(
        document["status_extracao"] == "json_invalido"
        for document in payload["documentos"]
    )
