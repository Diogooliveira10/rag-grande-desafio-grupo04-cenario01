"""Complemento incremental: cobertura exata, origem imutável e FAISS alinhado."""
import json

import pytest

from src import complete_vectors_v7 as tool
from src.index import build_index, file_sha256
from tests.test_repair_candidate_v7 import _document, _write


@pytest.fixture
def pacote(tmp_path):
    candidato = tmp_path / "origem"
    candidato.mkdir()
    docs = [_document("contratos_estruturado", "estruturada", "c1")]
    path = candidato / "documents.jsonl"
    _write(path, docs)
    build_index(path, candidato / "index", lambda texts: [[1., 0.] for _ in texts], model="source-q4")
    docs.append(_document("contratos_estruturado", "estruturada", "c2"))
    _write(path, docs)
    base = candidato / "index/contratos_estruturado"
    _write(base / "documents.jsonl", docs)
    for mp in (candidato / "index/manifest.json", base / "manifest.json"):
        m = json.loads(mp.read_text(encoding="utf-8"))
        m.update(total_chunks=2, total_vectors=1, source_sha256=file_sha256(path),
                 vetores_ausentes=["c2"], proveniencia_encoding_completa=False)
        mp.write_text(json.dumps(m), encoding="utf-8")
    checkpoint = tmp_path / "modelo-fixture.gguf"
    checkpoint.write_bytes(b"checkpoint sintetico de teste")
    novo = {"chunk_id": "c2", "base_id": "contratos_estruturado", "embedding": [0., 1.],
            "chunk_content_sha256": docs[1]["chunk_content_sha256"],
            "embedding_model": "Qwen3-Embedding-fixture",
            "encoding_config": {"checkpoint": str(checkpoint), "checkpoint_sha256": file_sha256(checkpoint),
                                "pooling": "last", "document_instruction": None}}
    embeddings = tmp_path / "embeddings.jsonl"
    _write(embeddings, [novo])
    return candidato, embeddings, novo, tmp_path / "derivado"


def test_completa_sem_alterar_origem_e_verifica_posicoes(pacote):
    candidato, embeddings, _, destino = pacote
    antes = {p.relative_to(candidato): file_sha256(p) for p in candidato.rglob("*") if p.is_file()}
    report = tool.completar(candidato, destino, embeddings, dimension=2)
    assert report["novos_vetores"] == 1
    assert report["integridade"]["documentos"] == report["integridade"]["vetores"] == 2
    assert report["integridade"]["ausentes"] == 0
    assert report["integridade"]["proveniencia_encoding_completa"] is False
    assert {p.relative_to(candidato): file_sha256(p) for p in candidato.rglob("*") if p.is_file()} == antes
    with pytest.raises(FileExistsError):
        tool.completar(candidato, destino, embeddings, dimension=2)


@pytest.mark.parametrize("mudanca,erro", [
    ({"embedding": [1., 2., 3.]}, "vetor inválido"),
    ({"embedding": [0., 0.]}, "vetor inválido"),
    ({"embedding": [float("nan"), 1.]}, "vetor inválido"),
    ({"chunk_content_sha256": "errado"}, "texto do embedding"),
    ({"base_id": "outra"}, "base divergente"),
    ({"embedding_model": "outro-modelo"}, "modelo não"),
    ({"chunk_id": "extra"}, "extra ou duplicado"),
    ({"encoding_config": {}}, "configuração/checkpoint"),
])
def test_recusa_embedding_invalido_antes_da_copia(pacote, mudanca, erro):
    candidato, embeddings, novo, destino = pacote
    _write(embeddings, [{**novo, **mudanca}])
    with pytest.raises(ValueError, match=erro):
        tool.completar(candidato, destino, embeddings, dimension=2)
    assert not destino.exists()


def test_recusa_lote_incompleto_ou_duplicado(pacote):
    candidato, embeddings, novo, destino = pacote
    for rows, erro in [([], "cobertura incompleta"), ([novo, novo], "extra ou duplicado")]:
        _write(embeddings, rows)
        with pytest.raises(ValueError, match=erro):
            tool.completar(candidato, destino, embeddings, dimension=2)
        assert not destino.exists()


def test_auditoria_detecta_vetor_antigo_fora_de_ordem(pacote):
    candidato, _, _, _ = pacote
    path = candidato / "index/contratos_estruturado/vectors.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["embedding"] = [0., 1.]
    _write(path, [row])
    with pytest.raises(ValueError, match="posição FAISS"):
        tool.verificar(candidato)


def test_prepare_extrai_apenas_lacunas(pacote, tmp_path):
    candidato, _, _, _ = pacote
    trabalho = tmp_path / "lote"
    report = tool.preparar(candidato, trabalho)
    assert report["total"] == 1
    assert json.loads((trabalho / "faltantes.jsonl").read_text(encoding="utf-8"))["chunk_id"] == "c2"
