import hashlib
import json
import sys
from pathlib import Path

import pytest

from src import ingest as ingest_module
from src.ingest import chunk_text, ingest, sha256_file
from src.prepare_documents import prepare_documents
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

    stats = ingest(corpus_dir, output, 200, 20, manifest_path=MANIFEST, base_id="exemplos_teste", allow_uninventoried=True)

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
    ingest(corpus_dir, output, 200, 20, manifest_path=MANIFEST, base_id="exemplos_teste", allow_uninventoried=True)

    for linha in output.read_text(encoding="utf-8").splitlines():
        chunk = json.loads(linha)
        faltando = OBRIGATORIOS - {k for k, v in chunk.items() if v not in (None, "")}
        assert not faltando, f"{chunk.get('chunk_id')} sem: {faltando}"
        ChunkMetadata.model_validate(chunk)


def test_ingest_respeita_report_file_explicito(tmp_path, corpus_dir):
    output = tmp_path / "documents.jsonl"
    report = tmp_path / "relatorios" / "meu_relatorio.json"

    ingest(corpus_dir, output, 200, 20, manifest_path=MANIFEST, report_file=report, base_id="exemplos_teste", allow_uninventoried=True)

    assert report.exists()
    assert not output.with_name("extraction_report.json").exists()


def test_ingest_dry_run_nao_grava_nada(tmp_path, corpus_dir):
    output = tmp_path / "documents.jsonl"

    stats = ingest(corpus_dir, output, 200, 20, manifest_path=MANIFEST, base_id="exemplos_teste", allow_uninventoried=True, dry_run=True)

    assert stats["chunks"] > 0
    assert not output.exists()
    assert not output.with_name("extraction_report.json").exists()


def test_ingest_nao_altera_corpus_bruto(tmp_path, corpus_dir):
    antes = {p.name: sha256_file(p) for p in corpus_dir.iterdir()}

    ingest(corpus_dir, tmp_path / "documents.jsonl", 200, 20, manifest_path=MANIFEST, base_id="exemplos_teste", allow_uninventoried=True)

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
        "--allow-uninventoried",
        "--dry-run",
    )

    assert codigo == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["dry_run"] is True
    assert stats["chunks"] > 0
    assert not output.exists()


# --------------------------------------------------------------------------
# Testes da branch joao-reis (merge de 2026-09-23)
# --------------------------------------------------------------------------

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
