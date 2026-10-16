import hashlib
import json
from pathlib import Path

import pytest

from src.index import REQUIRED_METADATA_FIELDS, build_index, read_normalized_chunks, stored_embed_fn

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_INPUT = FIXTURES_DIR / "documents_sample.jsonl"


def test_stored_embed_fn_aceita_hash_atual(tmp_path):
    path = tmp_path / "embeddings.jsonl"
    path.write_text(json.dumps({
        "chunk_content_sha256": hashlib.sha256(b"texto").hexdigest(),
        "embedding": [1.0, 2.0], "embedding_model": "local-q8",
    }) + "\n", encoding="utf-8")
    embed, model = stored_embed_fn(path)
    assert model == "local-q8"
    assert embed(["texto"]) == [[1.0, 2.0]]


def test_index_preserva_proveniencia_e_relacoes(tmp_path):
    record = {
        "chunk_id": "c1", "document_id": "d1", "base_id": "atas_conversacional",
        "text": "ata", "source_file": "ata.pdf", "doc_type": "pdf",
        "origem": "pncp", "inventory_version": "v5", "manifest_hash": "manifest",
        "parent_chunk_id": "pai", "relations": {"anexos": ["anexo.pdf"]},
        "chunk_content_sha256": hashlib.sha256(b"ata").hexdigest(),
    }
    path = tmp_path / "documents.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    chunk = next(read_normalized_chunks(path))
    for key in ("origem", "inventory_version", "manifest_hash", "parent_chunk_id", "relations", "chunk_content_sha256"):
        assert chunk.metadata[key] == record[key]


def _fake_embed_fn(dimension: int = 4):
    """Embeddings determinísticos e offline, só para testar a montagem do índice."""

    def embed(texts: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7 + 1)] * dimension for text in texts]

    return embed


def test_build_index_agrupa_por_base_e_grava_manifesto(tmp_path):
    output_dir = tmp_path / "index"

    manifest = build_index(SAMPLE_INPUT, output_dir, _fake_embed_fn(), model="fixture-model", batch_size=2)

    base_ids = {base["base_id"] for base in manifest["bases"]}
    assert base_ids == {"contratos_estruturado", "editais_normativo"}

    for base in manifest["bases"]:
        vectors_path = output_dir / base["base_id"] / "vectors.jsonl"
        faiss_path = output_dir / base["base_id"] / "vectors.faiss"
        assert vectors_path.exists()
        assert faiss_path.exists()
        records = [json.loads(line) for line in vectors_path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == base["total_chunks"]
        assert all(len(record["embedding"]) == base["dimension"] for record in records)
        assert all(not (REQUIRED_METADATA_FIELDS - set(record["metadata"])) for record in records)

        base_manifest = json.loads((output_dir / base["base_id"] / "manifest.json").read_text(encoding="utf-8"))
        assert base_manifest == base
        assert base_manifest["faiss_index"] == "vectors.faiss"

    assert (output_dir / "manifest.json").exists()


def test_retrieve_usa_indice_faiss_persistido(tmp_path):
    import faiss

    from src.retrieve import dense_retrieve

    input_file = tmp_path / "documents.jsonl"
    records = [
        {
            "chunk_id": "base:hospital",
            "document_id": "base:hospital",
            "base_id": "base",
            "text": "compra de equipamento hospitalar",
            "source_file": "hospital.json",
            "sha256": "a",
            "metadata": {
                "source_file": "hospital.json",
                "chunk_id": "base:hospital",
                "base_id": "base",
                "doc_type": "json",
                "sensitivity": "publico",
            },
        },
        {
            "chunk_id": "base:software",
            "document_id": "base:software",
            "base_id": "base",
            "text": "licença de software",
            "source_file": "software.json",
            "sha256": "b",
            "metadata": {
                "source_file": "software.json",
                "chunk_id": "base:software",
                "base_id": "base",
                "doc_type": "json",
                "sensitivity": "publico",
            },
        },
    ]
    input_file.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )

    def embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "hospital" in text else [0.0, 1.0] for text in texts]

    output_dir = tmp_path / "index"
    build_index(input_file, output_dir, embed, model="fixture-model")
    persisted = faiss.read_index(str(output_dir / "base" / "vectors.faiss"))
    assert persisted.ntotal == 2

    results = dense_retrieve(
        "hospital",
        index_dir=output_dir,
        documents_file=input_file,
        embed_fn=embed,
        top_k=1,
    )

    assert [result.chunk_id for result in results] == ["base:hospital"]
    assert results[0].score == pytest.approx(1.0)


def test_build_index_ignora_chunks_sem_texto(tmp_path):
    output_dir = tmp_path / "index"

    manifest = build_index(SAMPLE_INPUT, output_dir, _fake_embed_fn(), model="fixture-model")

    total_chunks = sum(base["total_chunks"] for base in manifest["bases"])
    assert manifest["chunks_sem_texto_ignorados"] == 1
    assert total_chunks == 4


def test_build_index_e_deterministico_para_a_mesma_entrada(tmp_path):
    output_a = tmp_path / "a"
    output_b = tmp_path / "b"

    manifest_a = build_index(SAMPLE_INPUT, output_a, _fake_embed_fn(), model="fixture-model")
    manifest_b = build_index(SAMPLE_INPUT, output_b, _fake_embed_fn(), model="fixture-model")

    assert manifest_a["source_sha256"] == manifest_b["source_sha256"]
    assert [base["total_chunks"] for base in manifest_a["bases"]] == [
        base["total_chunks"] for base in manifest_b["bases"]
    ]


def test_todo_chunk_tem_metadado_obrigatorio():
    """O teste de ouro do tópico 2.5: todo chunk normalizado segue ChunkMetadata."""
    for chunk in read_normalized_chunks(SAMPLE_INPUT):
        faltando = REQUIRED_METADATA_FIELDS - set(chunk.metadata)
        assert not faltando, f"{chunk.chunk_id} sem: {faltando}"


def test_build_index_falha_rapido_com_metadado_obrigatorio_ausente(tmp_path):
    input_file = tmp_path / "documents_invalidos.jsonl"
    record = {
        "chunk_id": "editais_normativo:xyz:chunk-1",
        "document_id": "editais_normativo:xyz",
        "base_id": "editais_normativo",
        "text": "Edital sem doc_type no metadado.",
        "source_path": "data/raw/v1/edital_xyz.pdf",
        "sha256": "xyz",
        "metadata": {
            "source_file": "edital_xyz.pdf",
            "chunk_id": "editais_normativo:xyz:chunk-1",
            "base_id": "editais_normativo",
            # "doc_type" e "sensitivity" faltando de propósito
        },
    }
    input_file.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="metadado obrigatório ausente"):
        build_index(input_file, tmp_path / "index", _fake_embed_fn(), model="fixture-model")


def _stored_embeddings(path: Path, texts: list[str], model: str = "fixture-model") -> None:
    lines = [
        json.dumps(
            {
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "embedding": [float(index), 1.0],
                "embedding_model": model,
            }
        )
        for index, text in enumerate(texts)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_stored_embed_fn_reaproveita_vetores_pelo_hash_do_texto(tmp_path):
    texts = [chunk.text for chunk in read_normalized_chunks(SAMPLE_INPUT) if chunk.text.strip()]
    embeddings = tmp_path / "embeddings.jsonl"
    _stored_embeddings(embeddings, texts)

    embed_fn, model = stored_embed_fn(embeddings)
    manifest = build_index(SAMPLE_INPUT, tmp_path / "index", embed_fn, model=model)

    assert model == "fixture-model"
    assert {base["dimension"] for base in manifest["bases"]} == {2}
    assert embed_fn([texts[1]]) == [[1.0, 1.0]]


def test_stored_embed_fn_recusa_texto_sem_vetor(tmp_path):
    embeddings = tmp_path / "embeddings.jsonl"
    _stored_embeddings(embeddings, ["texto original"])

    embed_fn, _ = stored_embed_fn(embeddings)

    with pytest.raises(KeyError, match="sem vetor"):
        embed_fn(["texto alterado"])



def test_build_index_reaproveita_vetores_de_execucao_anterior(tmp_path):
    '''Retomada: chunks já embedados não voltam ao encoder.'''
    output_dir = tmp_path / "index"
    build_index(SAMPLE_INPUT, output_dir, _fake_embed_fn(), model="fixture-model", batch_size=2)

    recebidos = []

    def embed(texts):
        recebidos.extend(texts)
        return _fake_embed_fn()(texts)

    manifest = build_index(SAMPLE_INPUT, output_dir, embed, model="fixture-model", batch_size=2)

    assert recebidos == []
    assert all(base["vetores_reaproveitados"] == base["total_chunks"] for base in manifest["bases"])


def test_build_index_reembece_linha_truncada_na_retomada(tmp_path):
    '''Queda no meio da escrita: só o chunk da linha truncada é embedado de novo.'''
    output_dir = tmp_path / "index"
    build_index(SAMPLE_INPUT, output_dir, _fake_embed_fn(), model="fixture-model", batch_size=2)
    vectors_path = output_dir / "contratos_estruturado" / "vectors.jsonl"
    linhas = vectors_path.read_text(encoding="utf-8").splitlines()
    vectors_path.write_text("\n".join(linhas[:-1] + [linhas[-1][:40]]) + "\n", encoding="utf-8")

    recebidos = []

    def embed(texts):
        recebidos.extend(texts)
        return _fake_embed_fn()(texts)

    manifest = build_index(SAMPLE_INPUT, output_dir, embed, model="fixture-model", batch_size=2)

    assert len(recebidos) == 1
    base_contratos = next(b for b in manifest["bases"] if b["base_id"] == "contratos_estruturado")
    assert base_contratos["vetores_reaproveitados"] == base_contratos["total_chunks"] - 1


def test_falha_no_faiss_nao_trunca_checkpoint_existente(tmp_path, monkeypatch):
    out = tmp_path / "index"
    build_index(SAMPLE_INPUT, out, _fake_embed_fn(), model="fixture-model")
    p = out / "contratos_estruturado/vectors.jsonl"
    antes = p.read_bytes()
    def falhar(*args):
        raise MemoryError("simulado")
    monkeypatch.setattr("src.index._write_faiss_index", falhar)
    with pytest.raises(MemoryError):
        build_index(SAMPLE_INPUT, out, _fake_embed_fn(), model="fixture-model")
    assert p.read_bytes() == antes


def test_retomada_nao_relabela_modelo(tmp_path):
    out = tmp_path / "index"
    build_index(SAMPLE_INPUT, out, _fake_embed_fn(), model="fixture-model")
    with pytest.raises(ValueError, match="retomada"):
        build_index(SAMPLE_INPUT, out, _fake_embed_fn(), model="outro-modelo")
