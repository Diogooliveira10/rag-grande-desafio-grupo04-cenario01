from pathlib import Path

from src import fetch_corpus


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus_manifest.yaml"
PDF_ROOT = ROOT / "tests" / "fixtures" / "pdf"


def test_examples_manifest_declares_local_deterministic_pdf_base():
    manifest = fetch_corpus.load_manifest(MANIFEST)
    base = next(base for base in manifest["bases"] if base["base_id"] == "exemplos_teste")

    assert base["base_id"] == "exemplos_teste"
    assert base["acesso"]["tipo"] == "arquivo_local"
    assert base["acesso"]["origem"] == "tests/fixtures/pdf"
    assert base["selecao_deterministica"] is True
    assert base["volume_alvo"] == 20


def test_examples_fixture_contains_at_least_twenty_valid_pdfs():
    pdfs = sorted(PDF_ROOT.glob("*.pdf"))

    assert len(pdfs) >= 20
    assert all(pdf.read_bytes().startswith(b"%PDF") for pdf in pdfs)


def test_manifest_declares_real_siga_csv_base():
    manifest = fetch_corpus.load_manifest(MANIFEST)
    base = next(base for base in manifest["bases"] if base["base_id"] == "licitacoes_siga_csv")

    assert base["natureza"] == "estruturada"
    assert base["acesso"]["tipo"] == "http_csv"
    assert base["acesso"]["url"].endswith("/LICITACOES.CSV")
    assert base["formato"] == "csv"
    assert base["selecao_deterministica"] is True
