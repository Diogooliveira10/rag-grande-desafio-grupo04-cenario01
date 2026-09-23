import hashlib
import json
import sys
from pathlib import Path

import pytest

from src import ingest as ingest_module
from src.ingest import chunk_text, ingest, sha256_file
from src.schema import ChunkMetadata


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus_manifest.yaml"
OBRIGATORIOS = {"source_file", "doc_type", "chunk_id", "sensitivity", "base_id"}


@pytest.fixture
def corpus_dir(tmp_path):
    input_dir = tmp_path / "source"
    input_dir.mkdir()
    (input_dir / "page.html").write_text(
        "<html><body><h1>Lei 14.133</h1><p>Tratamento diferenciado a ME e EPP.</p></body></html>",
        encoding="utf-8",
    )
    (input_dir / "records.json").write_text('{"data": [{"id": 1}, {"id": 2}]}', encoding="utf-8")
    (input_dir / "notes.txt").write_text("texto de teste sobre pregão eletrônico", encoding="utf-8")
    return input_dir


# --------------------------------------------------------------------------
# Unitário: chunk_text
# --------------------------------------------------------------------------


def test_chunk_text_vazio_nao_gera_chunks():
    assert list(chunk_text("")) == []
    assert list(chunk_text("   \n\n\n  ")) == []


def test_chunk_text_curto_gera_um_chunk():
    assert list(chunk_text("um parágrafo curto")) == ["um parágrafo curto"]


def test_chunk_text_agrupa_paragrafos_ate_o_limite():
    texto = "aaa\n\nbbb\n\nccc"
    assert list(chunk_text(texto, max_chars=100, overlap=0)) == ["aaa\n\nbbb\n\nccc"]


def test_chunk_text_colapsa_linhas_em_branco_excedentes():
    assert list(chunk_text("aaa\n\n\n\n\nbbb", max_chars=100, overlap=0)) == ["aaa\n\nbbb"]


def test_chunk_text_respeita_max_chars():
    texto = "\n\n".join(f"paragrafo {i} " + "x" * 80 for i in range(20))
    chunks = list(chunk_text(texto, max_chars=200, overlap=30))
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_chunk_text_quebra_paragrafo_maior_que_o_limite():
    chunks = list(chunk_text("y" * 1000, max_chars=300, overlap=0))
    assert all(len(c) <= 300 for c in chunks)
    assert "".join(chunks) == "y" * 1000


def test_chunk_text_aplica_sobreposicao_entre_chunks():
    primeiro = "a" * 90
    segundo = "b" * 80  # 10 de sobreposição + "\n\n" + 80 cabe em max_chars=100
    chunks = list(chunk_text(f"{primeiro}\n\n{segundo}", max_chars=100, overlap=10))
    assert len(chunks) == 2
    assert chunks[1].startswith("a" * 10)
    assert chunks[1].endswith(segundo)


def test_chunk_text_e_deterministico():
    texto = "\n\n".join("parágrafo " + str(i) * 50 for i in range(30))
    assert list(chunk_text(texto, 250, 40)) == list(chunk_text(texto, 250, 40))


# --------------------------------------------------------------------------
# Unitário: sha256_file
# --------------------------------------------------------------------------


def test_sha256_file_confere_com_hashlib(tmp_path):
    arquivo = tmp_path / "doc.bin"
    conteudo = b"contrato 001" * 200_000  # > 1 MiB, exercita a leitura em blocos
    arquivo.write_bytes(conteudo)
    assert sha256_file(arquivo) == hashlib.sha256(conteudo).hexdigest()


# --------------------------------------------------------------------------
# Contrato: fachada ingest -> prepare_documents
# --------------------------------------------------------------------------


def test_ingest_gera_jsonl_e_relatorio(tmp_path, corpus_dir):
    output = tmp_path / "out" / "documents.jsonl"

    stats = ingest(corpus_dir, output, 200, 20, manifest_path=MANIFEST, base_id="exemplos_teste")

    assert stats["arquivos"] == 3
    assert stats["falhas"] == 0
    assert stats["chunks"] > 0
    assert stats["base_id"] == "exemplos_teste"
    assert output.exists()
    assert output.with_name("extraction_report.json").exists()
    linhas = output.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == stats["chunks"]


def test_todo_chunk_tem_metadado_obrigatorio(tmp_path, corpus_dir):
    output = tmp_path / "documents.jsonl"
    ingest(corpus_dir, output, 200, 20, manifest_path=MANIFEST, base_id="exemplos_teste")

    for linha in output.read_text(encoding="utf-8").splitlines():
        chunk = json.loads(linha)
        faltando = OBRIGATORIOS - {k for k, v in chunk.items() if v not in (None, "")}
        assert not faltando, f"{chunk.get('chunk_id')} sem: {faltando}"
        ChunkMetadata.model_validate(chunk)


def test_ingest_respeita_report_file_explicito(tmp_path, corpus_dir):
    output = tmp_path / "documents.jsonl"
    report = tmp_path / "relatorios" / "meu_relatorio.json"

    ingest(corpus_dir, output, 200, 20, manifest_path=MANIFEST, report_file=report, base_id="exemplos_teste")

    assert report.exists()
    assert not output.with_name("extraction_report.json").exists()


def test_ingest_dry_run_nao_grava_nada(tmp_path, corpus_dir):
    output = tmp_path / "documents.jsonl"

    stats = ingest(corpus_dir, output, 200, 20, manifest_path=MANIFEST, base_id="exemplos_teste", dry_run=True)

    assert stats["chunks"] > 0
    assert not output.exists()
    assert not output.with_name("extraction_report.json").exists()


def test_ingest_nao_altera_corpus_bruto(tmp_path, corpus_dir):
    antes = {p.name: sha256_file(p) for p in corpus_dir.iterdir()}

    ingest(corpus_dir, tmp_path / "documents.jsonl", 200, 20, manifest_path=MANIFEST, base_id="exemplos_teste")

    assert {p.name: sha256_file(p) for p in corpus_dir.iterdir()} == antes


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _rodar_main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["ingest.py", *args])
    return ingest_module.main()


def test_main_rejeita_diretorio_inexistente(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _rodar_main(monkeypatch, "--input", str(tmp_path / "nao_existe"))
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "chunk_size, overlap",
    [("0", "0"), ("100", "-1"), ("100", "100"), ("100", "150")],
)
def test_main_rejeita_parametros_de_chunk_invalidos(monkeypatch, corpus_dir, chunk_size, overlap):
    with pytest.raises(SystemExit) as exc:
        _rodar_main(monkeypatch, "--input", str(corpus_dir), "--chunk-size", chunk_size, "--overlap", overlap)
    assert exc.value.code == 2


def test_main_dry_run_imprime_estatisticas(monkeypatch, capsys, tmp_path, corpus_dir):
    output = tmp_path / "documents.jsonl"

    codigo = _rodar_main(
        monkeypatch,
        "--input", str(corpus_dir),
        "--output", str(output),
        "--manifest", str(MANIFEST),
        "--base-id", "exemplos_teste",
        "--dry-run",
    )

    assert codigo == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["dry_run"] is True
    assert stats["chunks"] > 0
    assert not output.exists()
