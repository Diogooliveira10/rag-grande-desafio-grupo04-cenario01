"""Reparo offline: herança, metadados, lacunas e alinhamento FAISS."""
import hashlib
import json
from pathlib import Path

import pytest

from src.index import build_index
from src import repair_candidate_v7 as repair


def _document(base, natureza, cid, sensitivity="publico"):
    text = f"Conteúdo de teste {cid}"
    return {
        "chunk_id": cid, "document_id": cid, "base_id": base,
        "natureza": natureza, "doc_type": "json", "source_file": f"{cid}.json",
        "sensitivity": sensitivity, "text": text, "extras": {"orgao": "Órgão"},
        "chunk_content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def _write(path, records):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    records = [_document("contratos_estruturado", "estruturada", "c1"),
               _document("atas_conversacional", "conversacional", "a1")]
    documents = candidate / "documents.jsonl"
    _write(documents, records)
    embed = lambda texts: [[1.0, float(len(t))] for t in texts]
    build_index(documents, candidate / "index", embed, model="source-q4")
    # Simula o payload incompleto do candidato atual.
    for p in (candidate / "index").glob("*/vectors.jsonl"):
        rows = [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines()]
        for r in rows:
            r["metadata"] = {"chunk_id": r["chunk_id"]}
        _write(p, rows)
    records.append(_document("atas_conversacional", "conversacional", "sem-vetor"))
    _write(documents, records)
    reference = tmp_path / "reference.jsonl"
    inherited = [_document("atas_conversacional", "conversacional", "historica"),
                 _document("editais_normativo", "normativa", "lei"),
                 _document("pca_agregado", "agregada", "pca"),
                 _document("emails_sinteticos", "conversacional", "email", "restrito")]
    _write(reference, inherited)
    reference_index = tmp_path / "reference-index"
    build_index(reference, reference_index, embed, model="source-q8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(repair, "load_manifest", lambda _: {
        "curadoria_v7": {"bases_herdadas_v6": list({r["base_id"] for r in inherited})},
    })
    return candidate, dict(referencia_documents=reference, referencia_index=reference_index,
                           manifest=manifest, modelo_declarado="Q8-confirmado"), tmp_path / "repaired"


def test_reparo_preserva_origens_herda_email_e_declara_lacuna(inputs):
    candidate, kwargs, target = inputs
    source = candidate / "index/atas_conversacional/vectors.jsonl"
    before = source.read_bytes()
    report = repair.reparar(candidate, target, **kwargs)
    assert source.read_bytes() == before
    assert report["herdados_adicionados"]["emails_sinteticos"] == 1
    assert report["bases"]["atas_conversacional"]["vetores_ausentes"] == ["sem-vetor"]
    assert not report["cobertura_vetorial_completa"]
    assert not report["proveniencia_encoding_completa"]
    assert report["proveniencia_vetores"]["por_origem"] == {
        "candidato": 2, "referencia": 4, "recuperacao": 0,
    }
    assert not report["proveniencia_vetores"]["homogeneidade_modelo_comprovada"]
    assert not report["promocao_autorizada"]
    assert not report["inferencia_executada"]
    rows = [json.loads(s) for s in (target / "index/emails_sinteticos/vectors.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["metadata"]["sensitivity"] == "restrito"
    assert rows[0]["metadata"]["extras"] == {"orgao": "Órgão"}
    manifest = json.loads((target / "index/contratos_estruturado/manifest.json").read_text(encoding="utf-8"))
    assert manifest["embedding_model"] == "source-q4"
    assert manifest["embedding_model_declarado_pelo_operador"] == "Q8-confirmado"
    assert manifest["proveniencia"][0]["embedding_model_original"] == "source-q4"
    assert manifest["origens_vetores"]["candidato"] == 1
    assert manifest["origens_vetores"]["recuperacao"] == 0
    assert manifest["encoding_verificado"] is False
    email_manifest = json.loads((target / "index/emails_sinteticos/manifest.json").read_text(encoding="utf-8"))
    assert email_manifest["origens_vetores"]["referencia"] == 1
    assert email_manifest["encoding_config_disponivel"] is False
    assert email_manifest["encoding_verificado"] is False
    with pytest.raises(FileExistsError):
        repair.reparar(candidate, target, **kwargs)


def test_recupera_payload_truncado_por_id_e_hash_de_fonte_existente(inputs, tmp_path):
    candidate, kwargs, target = inputs
    source_docs = tmp_path / "source.jsonl"
    records = [json.loads(s) for s in (candidate / "documents.jsonl").read_text(encoding="utf-8").splitlines()]
    _write(source_docs, [r for r in records if r["base_id"] == "contratos_estruturado"])
    source_index = tmp_path / "source-index"
    build_index(source_docs, source_index, lambda texts: [[1.0, float(len(t))] for t in texts], model="old-q4")
    (candidate / "index/contratos_estruturado/vectors.jsonl").write_text("", encoding="utf-8")
    report = repair.reparar(candidate, target, fontes_index=(source_index,), **kwargs)
    contracts = report["bases"]["contratos_estruturado"]
    assert contracts["vetores"] == 1
    assert contracts["vetores_recuperados_de_fontes"] == 1
    assert contracts["faiss_origem_sem_payload"][0]["posicoes_sem_payload"] == 1
    assert contracts["vetores_ausentes"] == []


def test_sql_reusa_construtor_preserva_staging_e_carrega_pca(tmp_path):
    from contextlib import closing
    from src.structured_db import Banco, construir_banco, validar_banco
    from tests.test_structured_db import _contratacao, _plano, escrever_documents

    source_documents = escrever_documents(tmp_path / "source-documents.jsonl",
                                          contratacoes=[_contratacao("111-1-000001/2025")])
    staging = Banco(sqlite_path=tmp_path / "staging.sqlite")
    construir_banco(source_documents, staging)
    with closing(staging.conectar(somente_leitura=False)) as connection, connection:
        connection.execute("ALTER TABLE contratacoes ADD COLUMN instrumento_id INTEGER")
        connection.execute("UPDATE contratacoes SET instrumento_id=1")
        connection.execute("CREATE TABLE vinculos_pendentes (compra TEXT, registro TEXT)")
        connection.execute("INSERT INTO vinculos_pendentes VALUES ('compra','registro')")
        connection.execute("DROP TABLE pca_planos")
        connection.execute("UPDATE meta SET valor='v7-staging-2' WHERE chave='schema_version'")
    before = staging.sqlite_path.read_bytes()
    candidate = tmp_path / "candidate-sql"
    candidate.mkdir()
    escrever_documents(candidate / "documents.jsonl", contratacoes=[_contratacao("111-1-000001/2025")],
                        planos=[_plano("42498600000171-0-000004/2025")])
    summary = repair.preparar_sql(candidate, staging.sqlite_path)
    assert summary["pca_planos"] == 1
    assert summary["vinculos_pendentes"] == 1
    assert summary["relacoes_compra_contrato"] == [0, 0, 0, 0]
    assert summary["vinculos_pendentes_diagnostico"] == [1, 1, 1, 0, 0, 0]
    assert "structured_query não a consulta" in summary["vinculos_pendentes_escopo"]
    banco = Banco(sqlite_path=candidate / "base_estruturada.sqlite")
    assert banco.consultar("SELECT instrumento_id FROM contratacoes") == [{"instrumento_id": 1}]
    assert validar_banco(candidate / "documents.jsonl", banco) is banco
    assert staging.sqlite_path.read_bytes() == before
    with pytest.raises(FileExistsError):
        repair.preparar_sql(candidate, staging.sqlite_path)


def test_reparo_recusa_payload_que_nao_corresponde_a_posicao_faiss(inputs):
    candidate, kwargs, target = inputs
    path = candidate / "index/atas_conversacional/vectors.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["embedding"] = [-1.0, 0.0]
    _write(path, [row])
    with pytest.raises(ValueError, match="posição FAISS"):
        repair.reparar(candidate, target, **kwargs)
    assert not (target / "reparo.json").exists()
