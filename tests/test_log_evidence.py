"""D: optional full audit preserves candidates and excluded evidence separately."""
import json

import config
from src import generate, obs
from src.log_evidence import auditar_candidatos
from src.retrieve import RetrievalResult, retrieve
from src.schema import Chunk, ChunkMetadata, Natureza


def records():
    return [json.loads(line) for path in obs.default_logger.logs_dir.glob("pipeline-*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]


def candidates(n):
    return [RetrievalResult(chunk_id=f"c{i}", content="Aquisição de computadores", score=1.0/(i+1),
        metadata={"chunk_id": f"c{i}", "base_id": "contratos_estruturado", "source_file": "contract.json",
                  "natureza": "estruturada", "sensitivity": "publico"}) for i in range(n)]


def test_retrieval_audit_keeps_all_returned_candidates_and_summary(monkeypatch):
    import src.retrieve as module
    monkeypatch.setattr(config, "LOG_EVIDENCE_DETAIL", "completo")
    monkeypatch.setattr(module, "dense_retrieve", lambda *a, **k: candidates(50))
    monkeypatch.setattr(module, "bm25_retrieve", lambda *a, **k: candidates(50))
    retrieve("computadores", {"bases": ["estruturada"]}, embed_fn=lambda t: [[1.0]], top_k=5)
    for e in records():
        if e["stage"] in {"retrieval_denso", "retrieval_bm25"}:
            assert len(e["extra"]["evidencias"]) == 10
            assert e["extra"]["evidencias_fora_do_log"] == 40
            audit = e["extra"]["auditoria_evidencias"]
            assert audit["total"] == len(audit["candidatos"]) == 50
            assert audit["candidatos"][-1]["chunk_id"] == "c49"


def test_rerank_audit_keeps_scores_of_candidates_excluded_by_top_k(monkeypatch):
    import src.retrieve as module
    monkeypatch.setattr(config, "LOG_EVIDENCE_DETAIL", "completo")
    monkeypatch.setattr(module, "dense_retrieve", lambda *a, **k: candidates(25))
    monkeypatch.setattr(module, "bm25_retrieve", lambda *a, **k: candidates(25))
    def rank(query, items):
        from dataclasses import replace
        return [replace(item, score=25-i, rerank_score=25-i) for i, item in enumerate(items)]
    result = retrieve("computadores", {"bases": ["estruturada"]}, embed_fn=lambda t: [[1.0]],
                      reranker=rank, rerank_k=25, top_k=5)
    assert len(result) == 5
    event = next(e for e in records() if e["stage"] == "rerank")
    audit = event["extra"]["auditoria_evidencias"]
    assert audit["total"] == 25 and len(event["extra"]["evidencias"]) == 5
    assert sum(item["selecionado_apos_top_k"] for item in audit["candidatos"]) == 5
    assert audit["candidatos"][-1]["rerank_score"] == 1


def test_default_summary_does_not_add_complete_content(monkeypatch):
    import src.retrieve as module
    monkeypatch.setattr(config, "LOG_EVIDENCE_DETAIL", "resumo")
    monkeypatch.setattr(module, "dense_retrieve", lambda *a, **k: candidates(20))
    monkeypatch.setattr(module, "bm25_retrieve", lambda *a, **k: candidates(20))
    retrieve("computadores", {"bases": ["estruturada"]}, embed_fn=lambda t: [[1.0]])
    assert all("auditoria_evidencias" not in e["extra"] for e in records())


def test_restricted_content_and_metadata_are_not_copied_to_audit():
    items = candidates(2)
    items[0].content = "CPF: 123.456.789-09; e-mail: pessoa@example.com"
    items[1].content = "senha: SECRET-DO-NOT-COPY"
    items[1].metadata.update(sensitivity="restrito", extras={"senha": "SECRET-DO-NOT-COPY"})
    data = json.dumps(auditar_candidatos(items), ensure_ascii=False)
    assert "123.456.789-09" not in data and "pessoa@example.com" not in data
    assert "SECRET-DO-NOT-COPY" not in data
    assert "Conteúdo restrito" in data


def test_generation_audit_keeps_long_original_and_excluded_chunk_without_changing_prompt(monkeypatch):
    monkeypatch.setattr(config, "LOG_EVIDENCE_DETAIL", "completo")
    monkeypatch.setattr(config, "GENERATION_CONTEXT_CHARS", 200)
    monkeypatch.setattr(config, "GENERATION_CHUNK_CHARS", 70)
    monkeypatch.setattr(generate, "_SYSTEM_PROMPT", "s" * 9000)
    chunks = [Chunk(content=("x" * 10000 + " ORIGINAL-END CPF: 123.456.789-09"), metadata=ChunkMetadata(
        source_file="contract.json", chunk_id=f"c{i}", base_id="contratos_estruturado", doc_type="json", natureza=Natureza.ESTRUTURADA))
        for i in range(2)]
    seen = []
    def model(messages):
        seen.extend(messages)
        return json.dumps({"answer": "Resposta parcial", "is_refusal": False, "confidence_level": "baixa", "sources_used": ["E1"]})
    generate.gerar_resposta("Qual o objeto?", chunks, [Natureza.ESTRUTURADA], cliente=model)
    event = next(e for e in records() if e["stage"] == "geracao")
    audit = event["extra"]["auditoria_evidencias"]
    assert audit["total_disponiveis"] == 2 and audit["total_selecionados"] == 1
    assert len(audit["evidencias"][0]["conteudo_disponivel"]) > 8000
    assert "ORIGINAL-END" in audit["evidencias"][1]["conteudo_disponivel"]
    assert audit["evidencias"][1]["motivo_exclusao"] == "orcamento_contexto"
    assert "ORIGINAL-END" not in seen[1]["content"]
    assert "123.456.789-09" not in json.dumps(event, ensure_ascii=False)
    assert len(event["extra"]["prompt"][0]["content"]) < 9000
    assert audit["prompt_completo"] == seen


def test_complete_sql_audit_keeps_lines_beyond_summary(tmp_path, monkeypatch):
    from src import structured_db, structured_query
    from src.schema import QueryFilters
    monkeypatch.setattr(config, "LOG_EVIDENCE_DETAIL", "completo")
    documents = tmp_path / "documents.jsonl"
    documents.write_text("", encoding="utf-8")
    banco = structured_db.Banco(sqlite_path=tmp_path / "facts.sqlite")
    structured_db.construir_banco(documents, banco)
    lines = [f"Órgão {i}: total R$ 100,00" for i in range(50)]
    monkeypatch.setattr(structured_query, "executar_consulta", lambda *a, **k: ("SELECT test", "Amostra", lines))
    chunks = structured_query.consultar_estruturado("Qual o valor total dos contratos por órgão?",
        QueryFilters(), [Natureza.ESTRUTURADA], banco=banco, documents_file=documents)
    assert chunks
    event = next(e for e in records() if e["stage"] == "consulta_sql")
    assert event["extra"]["resultado"] == lines[:20]
    assert event["extra"]["linhas_fora_do_log"] == 30
    assert event["extra"]["auditoria_evidencias"]["resultado_completo"] == lines
