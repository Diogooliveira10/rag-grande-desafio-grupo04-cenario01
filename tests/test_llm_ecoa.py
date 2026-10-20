"""Fallback e modo OpenRouter exclusivo, sem chamadas de rede."""
from types import SimpleNamespace

import pytest
from openai import APIStatusError

import config
import llm_ecoa


def client_for(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "auto")
    # Modelos fixos para os testes não dependerem do .env da máquina: sem isso,
    # um OPENROUTER_MODEL local mudaria o contrato observado pelos casos.
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "openrouter/free")
    monkeypatch.setattr(config, "OPENROUTER_MODEL_FALLBACK", "openrouter/free")
    calls = []
    response = SimpleNamespace(model="resolved-model:free", choices=[])
    def factory(**options):
        assert options["base_url"] == "https://openrouter.ai/api/v1"
        assert options["max_retries"] == 0
        def create(**arguments):
            calls.append(arguments)
            return response
        return client_for(create)
    monkeypatch.setattr(llm_ecoa, "OpenAI", factory)
    return calls, response


def test_ecoa_success_does_not_call_fallback(monkeypatch, providers):
    calls, response = providers
    monkeypatch.setattr(llm_ecoa, "client", client_for(lambda **_: response))
    info = {}
    assert llm_ecoa.completar_chat([], diagnosticos=info) is response
    assert not calls and info["provider"] == "ecoa" and not info["fallback_used"]


def test_reasoning_effort_configurado_vai_so_para_o_ecoa(monkeypatch, providers):
    calls, response = providers
    ecoa = []
    def primary(**arguments):
        ecoa.append(arguments)
        raise TimeoutError("offline")
    monkeypatch.setattr(config, "GENERATION_REASONING_EFFORT", "none")
    monkeypatch.setattr(llm_ecoa, "client", client_for(primary))
    info = {}

    llm_ecoa.completar_chat([], diagnosticos=info)

    assert ecoa[0]["extra_body"] == {"reasoning_effort": "none"}
    assert "reasoning_effort" not in calls[0]["extra_body"]
    assert info["reasoning_effort"] == "none"


def test_sem_reasoning_effort_o_ecoa_usa_o_padrao_do_servidor(monkeypatch, providers):
    _, response = providers
    ecoa = []
    monkeypatch.setattr(config, "GENERATION_REASONING_EFFORT", "")
    monkeypatch.setattr(llm_ecoa, "client", client_for(lambda **a: ecoa.append(a) or response))

    llm_ecoa.completar_chat([], diagnosticos={})

    assert "extra_body" not in ecoa[0]


def test_timeout_fallback_keeps_schema_and_free_model(monkeypatch, providers):
    calls, response = providers
    def primary(**_):
        raise TimeoutError("offline")
    monkeypatch.setattr(llm_ecoa, "client", client_for(primary))
    schema = {"type": "json_schema", "json_schema": {"name": "query", "schema": {"type": "object"}}}
    info = {}
    assert llm_ecoa.completar_chat([], response_format=schema, diagnosticos=info) is response
    assert len(calls) == 1 and calls[0]["model"] == "openrouter/free"
    assert calls[0]["response_format"] == schema
    assert calls[0]["extra_body"]["provider"]["require_parameters"] is True
    assert "reasoning" not in calls[0]["extra_body"]
    assert info["fallback_used"] and info["model"] == response.model
    assert "test-key" not in str(info)


@pytest.mark.parametrize("status,available", [(400, False), (401, False), (403, False), (404, True), (408, True), (429, True), (503, True)])
def test_availability_statuses(status, available):
    response = SimpleNamespace(status_code=status, headers={}, request=SimpleNamespace())
    error = APIStatusError("unavailable", response=response, body=None)
    assert llm_ecoa.erro_de_disponibilidade(error) is available


def test_internal_error_does_not_call_fallback(monkeypatch, providers):
    calls, _ = providers
    def primary(**_):
        raise ValueError("invalid input")
    monkeypatch.setattr(llm_ecoa, "client", client_for(primary))
    with pytest.raises(ValueError):
        llm_ecoa.completar_chat([])
    assert not calls


def test_missing_key_preserves_primary_error(monkeypatch, providers):
    calls, _ = providers
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    def primary(**_):
        raise ConnectionError("offline")
    monkeypatch.setattr(llm_ecoa, "client", client_for(primary))
    with pytest.raises(ConnectionError):
        llm_ecoa.completar_chat([])
    assert not calls


def test_openrouter_only_never_calls_ecoa(monkeypatch, providers):
    calls, response = providers
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "openrouter")
    def primary(**_):
        pytest.fail("ECOA must not be called")
    monkeypatch.setattr(llm_ecoa, "client", client_for(primary))
    info = {}
    assert llm_ecoa.completar_chat([], diagnosticos=info) is response
    assert len(calls) == 1 and info["provider"] == "openrouter" and not info["fallback_used"]


def test_openrouter_only_missing_key_never_calls_ecoa(monkeypatch, providers):
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm_ecoa, "client", client_for(lambda **_: pytest.fail("ECOA must not be called")))
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        llm_ecoa.completar_chat([])


@pytest.mark.parametrize("arguments", [
    ["--openrouter-only", "perguntar", "teste"],
    ["perguntar", "--openrouter-only", "teste"],
    ["rota", "teste", "--openrouter-only"],
])
def test_cli_flag_sets_mode_only_during_command(monkeypatch, providers, arguments):
    from app import cli
    observed = []
    def command(*args, **kwargs):
        observed.append(config.LLM_PROVIDER_MODE)
        return 0
    monkeypatch.setattr(cli, "cmd_perguntar", command)
    monkeypatch.setattr(cli, "cmd_rota", command)
    assert cli.main(arguments) == 0
    assert observed == ["openrouter"] and config.LLM_PROVIDER_MODE == "auto"


def _factory_for(calls, response, create=None):
    def factory(**options):
        assert options["base_url"] == "https://openrouter.ai/api/v1"
        assert options["max_retries"] == 0

        def default_create(**arguments):
            calls.append(arguments)
            return response

        return client_for(create or default_create)
    return factory


def test_openrouter_prefers_configured_model(monkeypatch, providers):
    calls, response = providers
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "xiaomi/mimo-v2.6-flash")
    monkeypatch.setattr(config, "OPENROUTER_MODEL_FALLBACK", "openrouter/free")
    monkeypatch.setattr(llm_ecoa, "client", client_for(lambda **_: pytest.fail("ECOA must not be called")))
    info = {}
    assert llm_ecoa.completar_chat([], diagnosticos=info) is response
    assert len(calls) == 1 and calls[0]["model"] == "xiaomi/mimo-v2.6-flash"
    assert not info["fallback_used"]


def test_openrouter_reserva_usada_somente_em_indisponibilidade(monkeypatch, providers):
    calls, response = providers
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "xiaomi/mimo-v2.6-flash")
    monkeypatch.setattr(config, "OPENROUTER_MODEL_FALLBACK", "openrouter/free")

    def create(**arguments):
        calls.append(arguments)
        if arguments["model"] == "xiaomi/mimo-v2.6-flash":
            raise TimeoutError("indisponivel")
        return response

    monkeypatch.setattr(llm_ecoa, "OpenAI", _factory_for(calls, response, create))
    monkeypatch.setattr(llm_ecoa, "client", client_for(lambda **_: pytest.fail("ECOA must not be called")))
    info = {}
    assert llm_ecoa.completar_chat([], diagnosticos=info) is response
    assert [item["model"] for item in calls] == ["xiaomi/mimo-v2.6-flash", "openrouter/free"]
    assert info["fallback_used"] and info["fallback_error_type"] == "TimeoutError"


def test_openrouter_erro_de_entrada_nao_troca_de_modelo(monkeypatch, providers):
    calls, response = providers
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "xiaomi/mimo-v2.6-flash")
    monkeypatch.setattr(config, "OPENROUTER_MODEL_FALLBACK", "openrouter/free")

    def create(**arguments):
        calls.append(arguments)
        raise ValueError("entrada invalida")

    monkeypatch.setattr(llm_ecoa, "OpenAI", _factory_for(calls, response, create))
    monkeypatch.setattr(llm_ecoa, "client", client_for(lambda **_: pytest.fail("ECOA must not be called")))
    with pytest.raises(ValueError):
        llm_ecoa.completar_chat([])
    assert [item["model"] for item in calls] == ["xiaomi/mimo-v2.6-flash"]


def test_openrouter_model_igual_a_reserva_faz_uma_unica_chamada(monkeypatch, providers):
    calls, response = providers
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "openrouter/free")
    monkeypatch.setattr(config, "OPENROUTER_MODEL_FALLBACK", "openrouter/free")
    monkeypatch.setattr(llm_ecoa, "client", client_for(lambda **_: pytest.fail("ECOA must not be called")))
    info = {}
    assert llm_ecoa.completar_chat([], diagnosticos=info) is response
    assert len(calls) == 1 and calls[0]["model"] == "openrouter/free"
    assert not info["fallback_used"]


def test_openrouter_registra_proveniencia_dos_modelos_tentados(monkeypatch, providers):
    calls, response = providers
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "xiaomi/mimo-v2.6-flash")
    monkeypatch.setattr(config, "OPENROUTER_MODEL_FALLBACK", "openrouter/free")

    def create(**arguments):
        calls.append(arguments)
        if arguments["model"] == "xiaomi/mimo-v2.6-flash":
            raise ConnectionError("offline")
        return response

    monkeypatch.setattr(llm_ecoa, "OpenAI", _factory_for(calls, response, create))
    monkeypatch.setattr(llm_ecoa, "client", client_for(lambda **_: pytest.fail("ECOA must not be called")))
    info = {}
    llm_ecoa.completar_chat([], diagnosticos=info)
    assert [item["model_requested"] for item in info["chamadas_modelo"]] == [
        "xiaomi/mimo-v2.6-flash", "openrouter/free",
    ]
    assert [item["status"] for item in info["chamadas_modelo"]] == ["erro", "ok"]
    assert "test-key" not in str(info)


def test_openrouter_lento_e_cortado_pelo_prazo_total(monkeypatch, providers):
    import threading
    import time

    calls, response = providers
    liberar = threading.Event()

    def travado(**_):
        liberar.wait(5)  # o OpenRouter "processando" sem nunca responder
        return response

    def primary(**_):
        raise TimeoutError("ecoa offline")

    monkeypatch.setattr(llm_ecoa, "OpenAI", lambda **_: client_for(travado))
    monkeypatch.setattr(llm_ecoa, "client", client_for(primary))
    monkeypatch.setattr(config, "OPENROUTER_TIMEOUT", 0.2)
    monkeypatch.setattr(config, "MODEL_MIN_INTERVAL_SECONDS", 0)
    inicio = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="OpenRouter sem resposta"):
            llm_ecoa.completar_chat([], diagnosticos={})
    finally:
        liberar.set()
    assert time.monotonic() - inicio < 2


def test_com_prazo_devolve_o_resultado_quando_chega_a_tempo():
    assert llm_ecoa._com_prazo(lambda: 42, 1) == 42


def test_resposta_vazia_com_erro_do_provedor_passa_para_a_reserva(monkeypatch, providers):
    """HTTP 200 com finish_reason "error" e sem texto (nemotron, 2026-10-21):
    antes, virava "falha no serviço de geração" sem tentar o modelo reserva."""
    vazia = SimpleNamespace(model="m1", choices=[SimpleNamespace(
        finish_reason="error", message=SimpleNamespace(content=""))])
    boa = SimpleNamespace(model="m2", choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content='{"answer": "ok"}'))])
    chamadas = []

    def create(**arguments):
        chamadas.append(arguments["model"])
        return vazia if arguments["model"] == "modelo-principal" else boa

    def primary(**_):
        raise TimeoutError("ecoa offline")

    monkeypatch.setattr(config, "OPENROUTER_MODEL", "modelo-principal")
    monkeypatch.setattr(config, "OPENROUTER_MODEL_FALLBACK", "modelo-reserva")
    monkeypatch.setattr(config, "MODEL_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(llm_ecoa, "OpenAI", lambda **_: client_for(create))
    monkeypatch.setattr(llm_ecoa, "client", client_for(primary))
    info = {}

    assert llm_ecoa.completar_chat([], diagnosticos=info) is boa
    assert chamadas == ["modelo-principal", "modelo-reserva"]
    assert info["fallback_error_type"] == "resposta_vazia_do_provedor"
