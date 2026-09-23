import json
from datetime import datetime
import hashlib
import shutil
from pathlib import Path

from src.prepare_documents import prepare_documents
from src.schema import ChunkMetadata


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus_manifest.yaml"
PDF_ROOT = ROOT / "tests" / "fixtures" / "pdf"
CSV_FIXTURE = ROOT / "tests" / "fixtures" / "formats" / "licitacoes_siga_real.csv"


def test_prepare_documents_extracts_pdf_text_and_required_metadata(tmp_path):
    output = tmp_path / "documents.jsonl"
    report = tmp_path / "extraction_report.json"

    summary = prepare_documents(
        PDF_ROOT,
        output,
        report,
        MANIFEST,
        base_id="exemplos_teste",
        chunk_size=800,
        overlap=100,
    )

    assert summary["arquivos"] == 20
    assert summary["falhas"] == 0
    assert summary["chunks"] > 0
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["source_file"].startswith("tests/fixtures/pdf/")
    assert record["base_id"] == "exemplos_teste"
    assert record["natureza"] == "normativa"
    assert record["sensitivity"] == "publico"
    assert record["natureza"] == "normativa"
    assert record["doc_type"] == "pdf"
    assert record["logical_id"].startswith("exemplos_teste:tests/fixtures/pdf/")
    assert record["origem"] == "tests/fixtures/pdf"
    assert len(record["document_content_sha256"]) == 64
    assert len(record["chunk_content_sha256"]) == 64
    assert "content_sha256" not in record
    assert record["manifest_hash"].startswith("sha256:")
    assert datetime.fromisoformat(record["coletado_em"])
    assert "tipo_documento" not in record
    ChunkMetadata.model_validate(record)
    assert record["bytes"] > 0
    assert record["extras"]["pagina"] >= 1
    assert record["manifest_hash"].startswith("sha256:")
    assert "metadata" not in record
    assert report.exists()


def test_prepare_documents_handles_text_and_structured_formats(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    (input_dir / "page.html").write_text("<html><body><h1>Lei</h1><script>ignorar</script></body></html>", encoding="utf-8")
    (input_dir / "records.json").write_text('{"data": [{"id": 1}, {"id": 2}]}', encoding="utf-8")
    (input_dir / "notes.txt").write_text("texto de teste", encoding="utf-8")
    output = tmp_path / "documents.jsonl"
    report = tmp_path / "report.json"

    summary = prepare_documents(
        input_dir,
        output,
        report,
        MANIFEST,
        base_id="exemplos_teste",
        chunk_size=100,
        overlap=10,
    )

    assert summary["documentos"] == 4
    assert summary["chunks"] == 4
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    texts = [record["text"] for record in records]
    assert any("Lei" in text and "ignorar" not in text for text in texts)
    assert all("Users" not in record["source_file"] for record in records)


def test_prepare_documents_extracts_manifest_declared_structured_metadata(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    (input_dir / "records.json").write_text(
        '{"data": [{"orgao": "Secretaria X", "modalidade": "pregao", '
        '"valor": 500000, "municipio": "Rio de Janeiro", "ano": 2025, '
        '"dataVigenciaFim": "2025-12-31", "objeto": "software"}]}',
        encoding="utf-8",
    )
    output = tmp_path / "documents.jsonl"
    report = tmp_path / "report.json"

    prepare_documents(
        input_dir,
        output,
        report,
        MANIFEST,
        base_id="contratos_estruturado",
        chunk_size=200,
        overlap=20,
    )

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["natureza"] == "estruturada"
    assert record["doc_type"] == "json"
    assert record["origem"].startswith("https://pncp.gov.br")
    assert record["data_vigencia"] == "2025-12-31"
    assert record["extras"]["orgao"] == "Secretaria X"
    assert record["extras"]["valor"] == 500000
    assert "metadata" not in record
    assert record["logical_id"].startswith("contratos_estruturado:")


def test_prepare_documents_uses_inventory_provenance_instead_of_file_mtime(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    source = input_dir / "record.json"
    source.write_text('{"data": [{"numeroControlePNCP": "RJ-123"}]}', encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "arquivos": [
                    {
                        "base_id": "contratos_estruturado",
                        "sha256": digest,
                        "source_file": str(source),
                        "path": str(source),
                        "status": "ok",
                        "origem": "https://pncp.gov.br/api/contratos/pagina-1",
                        "coletado_em": "2026-09-20T12:34:56+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "documents.jsonl"
    prepare_documents(
        input_dir,
        output,
        tmp_path / "report.json",
        MANIFEST,
        base_id="contratos_estruturado",
        inventory_path=inventory,
    )

    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert record["origem"] == "https://pncp.gov.br/api/contratos/pagina-1"
    assert record["coletado_em"] == "2026-09-20T12:34:56+00:00"
    assert record["logical_id"] == "contratos_estruturado:numeroControlePNCP:RJ-123"


def test_prepare_documents_processes_only_files_listed_in_inventory(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    listed = input_dir / "listed.json"
    listed.write_text('{"id": "listed"}', encoding="utf-8")
    (input_dir / "not-listed.txt").write_text("não deve entrar", encoding="utf-8")
    digest = hashlib.sha256(listed.read_bytes()).hexdigest()
    manifest_digest = f"sha256:{hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}"
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "manifest_hash": manifest_digest,
                "arquivos": [
                    {
                        "base_id": "contratos_estruturado",
                        "status": "ok",
                        "path": str(listed),
                        "source_file": str(listed),
                        "sha256": digest,
                        "origem": "https://example.test/listed.json",
                        "coletado_em": "2026-09-23T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "documents.jsonl"
    summary = prepare_documents(
        input_dir,
        output,
        tmp_path / "report.json",
        MANIFEST,
        base_id="contratos_estruturado",
        inventory_path=inventory,
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert summary["arquivos"] == 1
    assert len(records) == 1
    assert records[0]["source_file"].endswith("listed.json")


def test_prepare_documents_extracts_real_csv_rows_and_declared_metadata(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    shutil.copy2(CSV_FIXTURE, input_dir / CSV_FIXTURE.name)
    output = tmp_path / "documents.jsonl"

    summary = prepare_documents(
        input_dir,
        output,
        tmp_path / "report.json",
        MANIFEST,
        base_id="licitacoes_siga_csv",
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert summary["documentos"] == 50
    assert len(records) == 50
    assert records[0]["doc_type"] == "csv"
    assert records[0]["natureza"] == "estruturada"
    assert records[0]["chunking_strategy"] == "registro_inteiro"
    assert records[0]["origem"] == "https://www.compras.rj.gov.br/siga/imagens/LICITACOES.CSV"
    assert records[0]["extras"]["ID Licitação"]
    assert records[0]["data_vigencia"] == "2011-06-15"
    assert all(record["text"] for record in records)
    assert all("tipo_documento" not in record for record in records)


def test_structured_records_are_not_split_by_character_limit(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    long_object = "contratação de software " * 100
    (input_dir / "records.json").write_text(
        json.dumps({"data": [{"objeto": long_object, "valor": 500000}]}),
        encoding="utf-8",
    )
    output = tmp_path / "documents.jsonl"

    summary = prepare_documents(
        input_dir,
        output,
        tmp_path / "report.json",
        MANIFEST,
        base_id="contratos_estruturado",
        chunk_size=100,
        overlap=10,
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert summary["chunks"] == 1
    assert records[0]["chunking_strategy"] == "registro_inteiro"
    assert records[0]["parent_chunk_id"] == records[0]["document_id"]


def test_normative_documents_split_at_sections(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    (input_dir / "edital.txt").write_text(
        "PREÂMBULO\n\n"
        "CLÁUSULA PRIMEIRA - OBJETO\n" + "texto do objeto " * 8 + "\n\n"
        "CLÁUSULA SEGUNDA - PRAZO\n" + "texto do prazo " * 8,
        encoding="utf-8",
    )
    output = tmp_path / "documents.jsonl"

    summary = prepare_documents(
        input_dir,
        output,
        tmp_path / "report.json",
        MANIFEST,
        base_id="editais_normativo",
        chunk_size=160,
        overlap=20,
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert summary["chunks"] >= 2
    assert all(record["chunking_strategy"] == "secoes_normativas" for record in records)
    assert all(record["parent_chunk_id"] == records[0]["parent_chunk_id"] for record in records)
    assert any("CLÁUSULA PRIMEIRA" in record["text"] for record in records)
    assert any("CLÁUSULA SEGUNDA" in record["text"] for record in records)
