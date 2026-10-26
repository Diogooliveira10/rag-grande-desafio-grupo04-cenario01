"""Alinhamento ao §2.4: contrato offline, modelo e uso conhecido/desconhecido."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import llm_ecoa
from src import obs
from src.model_observation import observar_chamada, resumir_uso
from src.offline_observation import observar_offline
from src.trace import montar_trace

REQUIRED = {"ts", "run_id", "query_id", "stage", "status", "latencia_ms", "input", "output", "tokens", "modelo", "extra"}


def records():
    obs.default_logger.flush()
    events = [json.loads(line) for p in obs.default_logger.logs_dir.glob("pipeline-*.jsonl")
              for line in p.read_text(encoding="utf-8").splitlines()]
    for event in events:
        assert REQUIRED <= event.keys()
        assert {"prompt", "completion", "total"} == event["tokens"].keys()
        assert event["latencia_ms"] >= 0
        assert "uso_tokens" in event["extra"]
    return events


def sdk_response(text='{"tipo":"conversa","answer":"Olá!"}', usage=True, model="resolved-model"):
    return SimpleNamespace(model=model, choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12) if usage else None)


def test_nested_offline_operations_share_one_execution_without_changing_result():
    @observar_offline(obs.Stage.PREPARACAO)
    def inner(): return {"chunks": 3}
    @observar_offline(obs.Stage.INGESTAO)
    def outer(): return inner()
    assert outer() == {"chunks": 3}
    events = records()
    assert [e["stage"] for e in events] == ["pipeline_inicio", "preparacao", "ingestao", "pipeline_fim"]
    assert len({e["run_id"] for e in events}) == len({e["query_id"] for e in events}) == 1
    assert all(e["session_id"] is None and e["extra"]["origem"] == "offline" for e in events)
    assert [e["extra"]["sequencia"] for e in events] == [1, 2, 3, 4]
    trace = montar_trace(events[0]["query_id"])
    assert trace["completo"] is True and trace["status"] == "ok"
    assert trace["resumo"]["cobertura_tokens"]["eventos_por_status"]["indisponivel"] == 0


def test_offline_error_closes_envelope_and_preserves_exception():
    @observar_offline(obs.Stage.INDEXACAO)
    def broken(): raise ValueError("CPF: 123.456.789-09")
    with pytest.raises(ValueError): broken()
    events = records()
    assert events[-1]["stage"] == "pipeline_fim" and events[-1]["status"] == "erro"
    assert events[-1]["extra"]["completed"] is False
    assert "123.456.789-09" not in json.dumps(events)


def test_offline_nonzero_exit_is_error():
    @observar_offline(obs.Stage.COLETA)
    def command(): return 2
    assert command() == 2
    assert records()[-1]["status"] == "erro"


def test_real_ingest_and_prepare_emit_the_common_contract(tmp_path):
    from src.ingest import ingest
    source = tmp_path / "raw"
    source.mkdir()
    (source / "documento.txt").write_text("Aquisição de papel para o escritório da prefeitura.", encoding="utf-8")
    result = ingest(source, tmp_path / "normalized/documents.jsonl", 300, 20,
                    base_id="exemplos_teste", allow_uninventoried=True)
    assert result
    events = records()
    assert {"ingestao", "preparacao", "pipeline_inicio", "pipeline_fim"} <= {e["stage"] for e in events}
    assert len({e["query_id"] for e in events}) == 1


def input_chunks(tmp_path):
    path = tmp_path / "documents.jsonl"
    rows = [{"chunk_id": f"c{i}", "text": f"Contrato de papel {i}", "base_id": "contratos_estruturado",
             "doc_type": "json", "sensitivity": "publico"} for i in range(2)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


@pytest.mark.parametrize("usage", [True, False])
def test_offline_embedding_records_usage_or_unknown_without_logging_input(tmp_path, monkeypatch, usage):
    from src.create_embeddings import create_embeddings
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        response = sdk_response(usage=usage, model="effective-embedding")
        response.data = [SimpleNamespace(index=i, embedding=[1.0, .5]) for i, _ in enumerate(kwargs["input"])]
        return response
    monkeypatch.setattr(llm_ecoa, "client", SimpleNamespace(embeddings=SimpleNamespace(create=create), max_retries=0))
    path = input_chunks(tmp_path)
    result = create_embeddings(path, tmp_path / "vectors.jsonl", batch_size=1, pause_seconds=0,
                               endpoint=llm_ecoa.OLLAMA_URL)
    assert result["gerados"] == 2 and len(calls) == 2
    events = records()
    batches = [e for e in events if e["stage"] == "embedding_lote"]
    assert len(batches) == 2
    assert all(e["modelo"] == "effective-embedding" for e in batches)
    assert all(e["extra"]["uso_tokens"]["status"] == ("reportado" if usage else "indisponivel") for e in batches)
    assert all(e["tokens"]["total"] == (12 if usage else 0) for e in batches)
    assert "Contrato de papel" not in json.dumps(events)
    end = events[-1]
    assert end["extra"]["tokens_reportados"]["total"] == (24 if usage else 0)
    assert end["extra"]["cobertura_tokens"]["eventos_por_status"]["indisponivel"] == (0 if usage else 2)
    assert create_embeddings(path, tmp_path / "vectors.jsonl", pause_seconds=0,
                             endpoint=llm_ecoa.OLLAMA_URL)["gerados"] == 0
    assert len(calls) == 2  # Cache does not spend/report the old tokens again.


def test_real_index_logs_each_embedding_batch(tmp_path, monkeypatch):
    import openai
    from src.index import _default_embed_fn, build_index
    from tests.test_index import SAMPLE_INPUT
    def create(**kwargs):
        response = sdk_response(model="effective-index-embedding")
        response.data = [SimpleNamespace(embedding=[1.0, .5]) for _ in kwargs["input"]]
        return response
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(
        embeddings=SimpleNamespace(create=create), max_retries=0))
    manifest = build_index(SAMPLE_INPUT, tmp_path / "index", _default_embed_fn("configured"),
                           model="configured", batch_size=2)
    assert manifest["bases"]
    events = records()
    batches = [e for e in events if e["stage"] == "embedding_lote"]
    assert batches and all(e["modelo"] == "effective-index-embedding" for e in batches)
    assert all(e["extra"]["base_id"] and e["tokens"]["total"] == 12 for e in batches)
    assert events[-1]["extra"]["tokens_reportados"]["total"] == 12 * len(batches)


def test_injected_vector_function_has_explicitly_unknown_usage(tmp_path):
    from src.create_embeddings import create_embeddings
    create_embeddings(input_chunks(tmp_path), tmp_path / "vectors.jsonl", pause_seconds=0,
                      embed_batch=lambda texts: [[1.0, .5] for _ in texts])
    batch = next(e for e in records() if e["stage"] == "embedding_lote")
    assert batch["tokens"]["total"] == 0
    assert batch["extra"]["uso_tokens"]["status"] == "indisponivel"
    assert batch["extra"]["model_called"] is None


def test_collection_preserves_legacy_file_and_emits_standard_events(tmp_path, monkeypatch):
    from src import fetch_corpus
    monkeypatch.setattr(fetch_corpus, "RUNS_ROOT", tmp_path / "runs")
    @observar_offline(obs.Stage.COLETA)
    def collect():
        logger = fetch_corpus._RunLogger("fetch")
        logger.event("base_collection", "started", base_id="fixture")
        logger.event("base_collection", "completed", arquivos=2)
        return logger.path
    legacy = collect()
    original = [json.loads(line) for line in legacy.read_text().splitlines()]
    assert original[0]["status"] == "started" and "timestamp" in original[0]
    events = records()
    assert len({e["run_id"] for e in events}) == 1
    mirrored = [e for e in events if e["extra"].get("etapa_coleta")]
    assert len(mirrored) == 2 and mirrored[0]["status"] == "iniciado"
    assert mirrored[-1]["output"]["arquivos"] == 2
    assert mirrored[-1]["extra"]["run_id_legado"] == original[0]["run_id"]


def test_inventory_operation_uses_common_contract(monkeypatch):
    from src import fetch_corpus
    original = {"inventory_type": "manifest", "version": "v1", "totais": {"arquivos": 2}}
    monkeypatch.setattr(fetch_corpus, "_load_inventory", lambda version: original)
    monkeypatch.setattr(fetch_corpus, "_render_corpus_inventory", lambda inventory: None)
    assert fetch_corpus._inventory_for_version(1) is original
    assert next(e for e in records() if e["stage"] == "inventario")["output"]["totais"] == {"arquivos": 2}


# Sem usage, a resposta de conversa tem os tokens estimados pelo texto (PR #67, João).
@pytest.mark.parametrize("response,expected", [(sdk_response(), "reportado"), (sdk_response(usage=False), "estimado"),
    (SimpleNamespace(usage=None, choices=[]), "indisponivel"),
    (SimpleNamespace(usage={"prompt_tokens": 4}), "parcial"),
    (SimpleNamespace(usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}), "reportado")])
def test_known_zero_missing_usage_and_partial_usage_are_distinct(response, expected):
    extra = {}
    tokens = obs.token_usage_from_response(response, extra=extra)
    assert extra["uso_tokens"]["status"] == expected
    assert tokens.total >= 0


def test_model_observation_keeps_failed_and_successful_calls_without_inventing_cost():
    diagnostics = {}
    def broken(): raise TimeoutError("SECRET-NOT-LOGGED")
    with pytest.raises(TimeoutError):
        observar_chamada(broken, provider="ecoa", model="requested", sdk_max_retries=1, diagnosticos=diagnostics)
    response = sdk_response()
    assert observar_chamada(lambda: response, provider="openrouter", model="free", sdk_max_retries=0,
                            diagnosticos=diagnostics) is response
    tokens, coverage = resumir_uso(diagnostics["chamadas_modelo"])
    assert tokens.total == 12 and coverage["status"] == "parcial"
    assert coverage["chamadas_observadas"] == 2 and coverage["chamadas_sem_usage"] == 1
    assert coverage["retries_internos_sdk"] == "nao_observados"
    assert diagnostics["chamadas_modelo"][0]["error_type"] == "TimeoutError"
    assert "SECRET-NOT-LOGGED" not in json.dumps(diagnostics)


def test_chat_fallback_keeps_usage_coverage(monkeypatch):
    from tests.test_llm_ecoa import client_for
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "auto")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "SECRET-KEY")
    def fail(**kwargs): raise TimeoutError("unavailable")
    monkeypatch.setattr(llm_ecoa, "client", client_for(fail))
    monkeypatch.setattr(llm_ecoa, "OpenAI", lambda **kwargs: client_for(lambda **kw: sdk_response()))
    diagnostics = {}
    llm_ecoa.completar_chat([], diagnosticos=diagnostics)
    assert diagnostics["uso_tokens"]["status"] == "parcial"
    assert diagnostics["llm_called"] and len(diagnostics["chamadas_modelo"]) == 2
    assert "SECRET-KEY" not in json.dumps(diagnostics)


def test_conversation_logs_effective_model(monkeypatch):
    from src.conversation import _interpret_interaction
    def complete(*args, diagnosticos, **kwargs):
        diagnosticos.update(model="actual-conversation", llm_called=True)
        return sdk_response(model="actual-conversation")
    monkeypatch.setattr(llm_ecoa, "completar_chat", complete)
    _interpret_interaction("Como usar?", run_id="run-conversation", query_id="q-conversation")
    event = records()[0]
    assert event["modelo"] == "actual-conversation" and event["tokens"]["total"] == 12
    assert event["extra"]["uso_tokens"]["status"] == "reportado"


def test_index_dry_run_emits_contract_without_calling_model():
    from src.index import _dry_run_index
    from tests.test_index import SAMPLE_INPUT
    result = _dry_run_index(SAMPLE_INPUT)
    assert result["chunks_por_base"]
    event = next(e for e in records() if e["stage"] == "indexacao")
    assert event["extra"]["uso_tokens"]["status"] == "nao_aplicavel"


def test_malformed_usage_does_not_break_valid_sdk_response():
    response = sdk_response()
    response.usage = {"prompt_tokens": "not-a-number"}
    diagnostics = {}
    assert observar_chamada(lambda: response, provider="fixture", model="model",
                            diagnosticos=diagnostics) is response
    assert diagnostics["uso_tokens"]["status"] == "indisponivel"


def test_analysis_cache_does_not_replay_old_llm_usage(tmp_path, monkeypatch):
    from src import query_analysis
    from src.schema import QueryAnalysisResult, QueryFilters
    query = "Qual o total dos contratos?"
    cache = tmp_path / "cache.jsonl"
    monkeypatch.setattr(query_analysis, "ANALYSIS_CACHE", cache)
    analysis = QueryAnalysisResult(query_original=query, filtros=QueryFilters())
    cache.write_text(json.dumps({"key": query_analysis._analysis_key(query), "analysis": analysis.model_dump(mode="json"),
        "llm": {"provider": "fixture", "model": "cached-model", "llm_called": True,
                "uso_tokens": {"status": "reportado"}, "chamadas_modelo": [{"tokens": {"total": 100}}]}}) + "\n")
    with obs.default_logger.span(run_id="run-cache", query_id="q-cache", stage=obs.Stage.QUERY_ANALYSIS, input=query) as span:
        result = query_analysis._analisar_llm(query, None, span)
        assert result.query_original == query
    event = records()[0]
    assert event["tokens"]["total"] == 0 and event["extra"]["llm_called"] is False
    assert event["extra"]["uso_tokens"]["status"] == "cache"
    assert event["extra"]["llm"]["chamadas_modelo"] == []


def test_reformulation_troca_de_ano_logs_sem_modelo(monkeypatch):
    """"E em 2024?" vira `troca_de_ano` por regra: modelo ausente e tokens zero.

    O atalho determinístico não pode chamar o modelo só para satisfazer o log.
    """
    from src import reformulation
    monkeypatch.setattr(reformulation, "REFORMULACAO_MODE", "llm")

    def nao_chamar(_):
        raise AssertionError("o atalho troca_de_ano não pode chamar o modelo")

    resultado = reformulation.reformular_pergunta(
        "E em 2024?", [{"pergunta": "Total de contratos de 2025?", "resposta": "R$ 100"}],
        cliente=nao_chamar)
    assert resultado.motivo == "troca_de_ano" and resultado.reescrita
    event = next(e for e in records() if e["stage"] == "reformulacao")
    assert event["modelo"] is None
    assert event["tokens"]["total"] == 0
    assert event["extra"]["llm_called"] is False and event["extra"]["metodo"] == "regras"


def test_reformulation_logs_effective_model(monkeypatch):
    """Continuação que exige modelo registra o modelo efetivo e o uso de tokens."""
    from src import reformulation
    monkeypatch.setattr(reformulation, "REFORMULACAO_MODE", "llm")
    response = sdk_response(text='{"pergunta":"Quem foi o fornecedor dos contratos de 2024?"}',
                            model="actual-reformulation")
    resultado = reformulation.reformular_pergunta(
        "E qual fornecedor aparece nesses contratos?",
        [{"pergunta": "Total de contratos de 2025?", "resposta": "R$ 100"}],
        cliente=lambda messages: response)
    # Continuação real (não é só troca de ano): o modelo foi chamado e reescreveu.
    assert resultado.motivo == "reescrita" and resultado.reescrita
    event = next(e for e in records() if e["stage"] == "reformulacao")
    assert event["modelo"] == "actual-reformulation" and event["tokens"]["total"] == 12
