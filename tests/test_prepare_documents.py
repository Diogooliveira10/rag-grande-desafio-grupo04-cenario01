import pytest
import json
from datetime import datetime
import hashlib
import shutil
from pathlib import Path

from src.prepare_documents import prepare_documents
from src.schema import ChunkMetadata

# Merge da branch joao-reis (2026-09-23): remover os skips quando o código entrar no main.
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
    assert record["origem"] == "PNCP - API de consulta de contratações/publicação"
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


def test_prepare_documents_reuses_incremental_extraction_and_invalidates_chunks(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    source = input_dir / "notes.txt"
    source.write_text("texto de teste " * 120, encoding="utf-8")
    output = tmp_path / "documents.jsonl"
    report = tmp_path / "extraction_report.json"

    first = prepare_documents(
        input_dir,
        output,
        report,
        MANIFEST,
        base_id="exemplos_teste",
        chunk_size=100,
        overlap=10,
    )
    first_output = output.read_bytes()

    second = prepare_documents(
        input_dir,
        output,
        report,
        MANIFEST,
        base_id="exemplos_teste",
        chunk_size=100,
        overlap=10,
    )
    assert first["extraction_cache_misses"] == 1
    assert second["extraction_cache_hits"] == 1
    assert second["extraction_cache_misses"] == 0
    assert second["chunk_cache_hits"] == 1
    assert second["chunk_cache_misses"] == 0
    assert output.read_bytes() == first_output
    assert (tmp_path / "documents.units.jsonl").exists()

    changed_chunking = prepare_documents(
        input_dir,
        output,
        report,
        MANIFEST,
        base_id="exemplos_teste",
        chunk_size=60,
        overlap=5,
    )
    assert changed_chunking["extraction_cache_hits"] == 1
    assert changed_chunking["extraction_cache_misses"] == 0
    assert changed_chunking["chunk_cache_hits"] == 0
    assert changed_chunking["chunk_cache_misses"] == 1


def _pdf_em_branco(path: Path) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.mark.parametrize("com_ocr", [True, False])
def test_pagina_escaneada_usa_ocr_quando_disponivel(tmp_path, monkeypatch, com_ocr):
    from src import prepare_documents as prepare

    input_dir = tmp_path / "source"
    input_dir.mkdir()
    _pdf_em_branco(input_dir / "escaneado.pdf")
    monkeypatch.setattr(prepare.ocr, "disponivel", lambda: com_ocr)
    monkeypatch.setattr(prepare.ocr, "motor", lambda: "ocr-fake" if com_ocr else "sem-ocr")
    monkeypatch.setattr(prepare.ocr, "texto_da_pagina", lambda path, index: "ATA DE REGISTRO DE PREÇOS Nº 79/2023")

    output = tmp_path / "documents.jsonl"
    summary = prepare_documents(input_dir, output, tmp_path / "report.json", MANIFEST, base_id="exemplos_teste")

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    if com_ocr:
        assert summary["status_extracao"] == {"ok_ocr": 1}
        assert records[0]["text"] == "ATA DE REGISTRO DE PREÇOS Nº 79/2023"
        assert records[0]["extras"]["extracao"] == "ocr-fake"
    else:
        assert summary["status_extracao"] == {"sem_texto": 1}
        assert records == []


def test_pdf_da_ata_herda_vigencia_e_titulo_dos_artefatos_da_mesma_ata(tmp_path):
    input_dir = tmp_path / "atas_conversacional"
    input_dir.mkdir()
    pdf = input_dir / "ata_arquivo_1.pdf"
    shutil.copy(next(PDF_ROOT.glob("*.pdf")), pdf)
    metadata = input_dir / "ata_metadata.json"
    metadata.write_text(json.dumps({"dataVigenciaFim": "2026-10-22", "objetoCompra": "combustível"}), encoding="utf-8")
    listing = input_dir / "ata_arquivos.json"
    listing.write_text(
        json.dumps(
            [
                {
                    "url": "https://pncp.test/atas/1/arquivos/1",
                    "titulo": "Ata de Registro de Preços nº 00178",
                    "tipoDocumentoNome": "Ata de Registro de Preços",
                }
            ]
        ),
        encoding="utf-8",
    )
    source_ref = {"provider": "pncp", "resource_type": "ata", "key": {"sequencial_ata": "1"}}
    registros = [
        (metadata, "json", "pncp:ata:1:metadata", "https://pncp.test/atas/1"),
        (listing, "json", "pncp:ata:1:arquivos", "https://pncp.test/atas/1/arquivos"),
        (pdf, "pdf", "pncp:ata:1:arquivo:1", "https://pncp.test/atas/1/arquivos/1"),
    ]
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "manifest_hash": f"sha256:{hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}",
                "arquivos": [
                    {
                        "base_id": "atas_conversacional",
                        "status": "ok",
                        "path": str(path),
                        "source_file": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "tipo": tipo,
                        "artifact_id": artifact_id,
                        "source_ref": source_ref,
                        "origem": origem,
                        "coletado_em": "2026-09-25T00:00:00+00:00",
                    }
                    for path, tipo, artifact_id, origem in registros
                ],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "documents.jsonl"
    prepare_documents(
        input_dir, output, tmp_path / "report.json", MANIFEST,
        base_id="atas_conversacional", inventory_path=inventory,
    )

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    pdf_chunks = [record for record in records if record["doc_type"] == "pdf"]
    assert pdf_chunks
    assert {record["data_vigencia"] for record in pdf_chunks} == {"2026-10-22"}
    assert {record["extras"]["titulo_documento"] for record in pdf_chunks} == {"Ata de Registro de Preços nº 00178"}
