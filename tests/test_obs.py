import json
from types import SimpleNamespace

import pytest

from src.obs import PipelineLogger, Stage, TokenUsage, new_query_id, new_run_id, observe


def _read_only_record(tmp_path):
    files = list(tmp_path.glob("pipeline-*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_span_grava_contrato_jsonl_e_tokens(tmp_path):
    logger = PipelineLogger(tmp_path)
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14)
    )

    with logger.span(
        run_id="run12345",
        query_id="q_0042",
        stage=Stage.GERACAO,
        input="Qual é o contrato?",
        modelo="modelo-teste",
        extra={"k": 12},
    ) as span:
        span.set_result("Resposta", response=response)

    record = _read_only_record(tmp_path)
    assert set(record) == {
        "ts", "run_id", "query_id", "stage", "status", "latencia_ms",
        "input", "output", "tokens", "modelo", "extra", "session_id",
        "hash_anterior", "hash",
    }
    assert record["stage"] == "geracao"
    assert record["status"] == "ok"
    assert record["tokens"] == {"prompt": 10, "completion": 4, "total": 14}
    assert record["latencia_ms"] >= 0


def test_span_registra_erro_e_preserva_excecao(tmp_path):
    logger = PipelineLogger(tmp_path)

    with pytest.raises(ValueError, match="falha esperada"):
        with logger.span(
            run_id="run12345",
            query_id="q_0042",
            stage="validacao",
            input="entrada",
        ):
            raise ValueError("falha esperada")

    record = _read_only_record(tmp_path)
    assert record["status"] == "erro"
    assert record["output"] == "ValueError: falha esperada"
    assert record["extra"]["error_type"] == "ValueError"


def test_decorator_captura_usage_da_resposta(tmp_path):
    logger = PipelineLogger(tmp_path)

    @observe(
        logger,
        stage=Stage.ROTEAMENTO,
        run_id="run12345",
        query_id="q_0042",
        input_builder=lambda query: query,
        output_builder=lambda response: "normativa",
    )
    def route(query):
        return SimpleNamespace(
            usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        )

    route("Posso participar do pregão?")
    record = _read_only_record(tmp_path)
    assert record["input"] == "Posso participar do pregão?"
    assert record["output"] == "normativa"
    assert record["tokens"]["total"] == 10


def test_ids_e_validacoes_basicas(tmp_path):
    assert len(new_run_id()) == 8
    assert new_query_id().startswith("q_")
    with pytest.raises(ValueError):
        TokenUsage(prompt=-1)
    with pytest.raises(ValueError):
        PipelineLogger(tmp_path).span(
            run_id="r", query_id="q", stage="stage_inexistente", input="x"
        )
