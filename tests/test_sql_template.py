"""SQL stays authoritative: the model only supplies a presentation template."""
import json
from types import SimpleNamespace

import pytest

from src import generate, pipeline
from src.schema import Natureza
from tests.test_numerical_validation import sql_chunk
from tests.test_pipeline_observability import event
from tests.test_sql_fast_path import local_sql  # isolated SQLite fixture


@pytest.fixture(autouse=True)
def _apresentacao_pelo_llm_ligada(monkeypatch):
    # Estes testes cobrem a apresentação pelo LLM, desligada por padrão
    # (config.SQL_APRESENTACAO_LLM).
    monkeypatch.setattr(generate.config, "SQL_APRESENTACAO_LLM", True)


def template_model(answer="Confira o resultado para o recorte solicitado:\n\n{{sql}}"):
    return lambda _: json.dumps({"answer": answer})


def render(chunks=None, cliente=None, query="Qual o total dos contratos?"):
    return generate.gerar_resposta(query, chunks or [sql_chunk(1, ["Total R$ 300,01"])],
                                  [Natureza.ESTRUTURADA], cliente=cliente or template_model())


def test_template_is_rendered_once_without_sending_results_to_model():
    chunks = [sql_chunk(1, ["Total R$ 300,01"]), sql_chunk(2, ["Fornecedor EMPRESA ORIGINAL"])]
    original = generate._resposta_sql_deterministica("Qual o total?", chunks, [Natureza.ESTRUTURADA])
    calls = []
    def model(messages):
        calls.append(messages)
        prompt = json.dumps(messages, ensure_ascii=False)
        assert "{{sql}}" in prompt
        assert "300,01" not in prompt and "EMPRESA ORIGINAL" not in prompt
        return json.dumps({"answer": "Confira o resultado:\n\n{{sql}}"})
    result = render(chunks, model, query="Qual o total?")
    assert len(calls) == 1
    assert result.answer == "Confira o resultado:\n\n" + original.answer
    assert result.sources_used == original.sources_used
    assert result.confidence_level == original.confidence_level
    assert result.avisos == original.avisos
    assert "{{sql}}" not in result.answer
    generation = event("geracao")["extra"]
    assert generation["metodo"] == "sql_template"
    assert generation["llm_called"] is True
    assert generation["chunks_no_prompt"] == 0
    assert generation["evidencias_usadas"] == [s.chunk_id for s in original.sources_used]


@pytest.mark.parametrize("answer", [
    "Esqueci o marcador", "{{sql}}\n{{sql}}", "{{sql}} {{outro}}",
    "O total é R$ 999,00.\n{{sql}}", "Foram 123 contratos.\n{{sql}}",
    "São três contratos.\n{{sql}}", "São cem contratos.\n{{sql}}",
    "O resultado [E1]:\n{{sql}}", "O resultado [1]:\n{{sql}}",
    "```\n{{sql}}\n```", "Veja {{sql}} agora", "{{SQL}}", "{{sql}}" + "a" * 2000,
])
def test_invalid_template_falls_back_without_leaking_markers_or_invented_numbers(answer):
    result = render(cliente=template_model(answer))
    assert result.answer == render(cliente=template_model("{{sql}}")).answer
    assert "999" not in result.answer and "{{" not in result.answer
    generation = event("geracao")
    assert generation["status"] == "degradado"
    assert generation["extra"]["metodo"] == "sql_template_fallback"
    assert generation["extra"]["fallback_motivo"]
    assert generation["extra"]["template_erro"]
    assert any("apresentação" in a for a in result.avisos)


@pytest.mark.parametrize("raw", ["not json", "{}", '{"answer": null}', '{"answer": 123}',
                                  '{"answer": "{{sql}}", "is_refusal": true}'])
def test_invalid_model_json_uses_sql_fallback(raw):
    result = render(cliente=lambda _: raw)
    assert not result.is_refusal and "R$ 300,01" in result.answer
    assert event("geracao")["extra"]["metodo"] == "sql_template_fallback"


def test_provider_failure_does_not_lose_a_valid_sql_result():
    def fail(_):
        raise TimeoutError("provider unavailable")
    result = render(cliente=fail)
    assert not result.is_refusal
    assert "R$ 300,01" in result.answer
    assert result.sources_used
    assert event("geracao")["extra"]["fallback_motivo"] == "erro_provedor"


def test_client_initialization_failure_also_uses_fallback(monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("missing credentials")
    monkeypatch.setattr(generate, "_cliente_padrao", fail)
    result = generate.gerar_resposta("Qual o total?", [sql_chunk(1, ["Total R$ 300,01"])],
                                    [Natureza.ESTRUTURADA])
    assert "R$ 300,01" in result.answer and not result.is_refusal
    assert event("geracao")["extra"]["llm_called"] is False


def test_default_client_schema_avoids_provider_string_bounds_but_keeps_local_validation(monkeypatch):
    schemas = []
    def factory(schema):
        schemas.append(schema)
        return template_model()
    monkeypatch.setattr(generate, "_cliente_padrao", factory)
    result = generate.gerar_resposta("Qual o total?", [sql_chunk(1, ["Total R$ 300,01"])],
                                    [Natureza.ESTRUTURADA])
    assert "R$ 300,01" in result.answer
    assert schemas[0]["required"] == ["answer"]
    assert schemas[0]["additionalProperties"] is False
    # Regressão observada no frontend com MiMo/OpenRouter: os limites remotos
    # removiam {{sql}}. O validador local continua rejeitando templates longos.
    assert schemas[0]["properties"]["answer"] == {"type": "string"}
    with pytest.raises(ValueError, match="template_longo"):
        generate._validar_template_sql(json.dumps({"answer": "a" * 1201 + "\n{{sql}}"}))


def test_tiny_context_does_not_drop_any_sql_part(monkeypatch):
    monkeypatch.setattr(generate.config, "GENERATION_CONTEXT_CHARS", 1)
    chunks = [sql_chunk(n, [f"Valor R$ {n},01"]) for n in range(1, 25)]
    result = render(chunks)
    assert len(result.sources_used) == 24
    assert all(f"R$ {n},01" in result.answer for n in range(1, 25))


def test_insertion_is_literal_and_not_a_second_template_evaluation():
    result = render([sql_chunk(1, ["Objeto literal: {{outro}} e {campo}"])])
    assert "Objeto literal: {{outro}} e {campo}" in result.answer


def test_personal_data_in_question_and_template_is_masked():
    def model(messages):
        assert "123.456.789-09" not in str(messages)
        return json.dumps({"answer": "CPF: 123.456.789-09\n{{sql}}"})
    result = render(cliente=model, query="Qual o total? CPF: 123.456.789-09")
    assert "123.456.789-09" not in result.answer
    assert "123.456.789-09" not in json.dumps(event("geracao"), ensure_ascii=False)


def test_guardrail_prevents_even_template_call():
    result = render(cliente=lambda _: pytest.fail("guardrail must run first"),
                    query="Quais os dados bancários do fornecedor?")
    assert result.is_refusal


def test_usage_from_sdk_is_preserved():
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"{{sql}}"}'))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=8, total_tokens=18))
    render(cliente=lambda _: response)
    record = event("geracao")
    assert record["tokens"] == {"prompt": 10, "completion": 8, "total": 18}
    assert record["extra"]["uso_tokens"]["status"] == "reportado"


def test_pipeline_calls_template_once_without_retrieval(local_sql, monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve", lambda *a, **k: pytest.fail("SQL is sufficient"))
    monkeypatch.setattr(pipeline, "reranker_padrao", lambda: pytest.fail("no cross-encoder"))
    calls = []
    def model(messages):
        calls.append(messages)
        return '{"answer":"Resultado para o recorte solicitado:\\n\\n{{sql}}"}'
    result = pipeline.responder("Qual o valor total dos contratos assinados em 2025?", cliente=model)
    assert len(calls) == 1
    assert "R$ 300,00" in result["answer"]
    assert "{{sql}}" not in result["answer"]
    assert result["sources_used"]


def test_pipeline_fallback_is_observable(local_sql):
    result = pipeline.responder("Quantos contratos foram assinados em 2025?",
                                cliente=template_model("sem marcador"))
    assert not result["is_refusal"] and "2 registros" in result["answer"]
    assert event("geracao")["status"] == "degradado"
    assert event("pipeline_fim")["status"] == "degradado"


def test_complete_audit_distinguishes_inserted_sql_from_prompt(monkeypatch):
    monkeypatch.setattr(generate.config, "LOG_EVIDENCE_DETAIL", "completo")
    render()
    extra = event("geracao")["extra"]
    audit = extra["auditoria_evidencias"]
    assert audit["metodo"] == "sql_template"
    assert "300,01" not in json.dumps(audit["prompt_completo"])
    assert audit["evidencias"][0]["selecionado"] is True
    assert extra["prompt_chunk_ids"] == []


def test_padrao_responde_o_sql_sem_chamar_o_llm(monkeypatch):
    monkeypatch.setattr(generate.config, "SQL_APRESENTACAO_LLM", False)
    chamadas = []

    resposta = generate.gerar_resposta("Qual o valor total dos contratos assinados em 2025?",
                                       [sql_chunk(1, ["Total R$ 300,01"])],
                                       [Natureza.ESTRUTURADA], cliente=lambda m: chamadas.append(m))

    assert chamadas == []
    assert resposta.sources_used and not resposta.is_refusal

