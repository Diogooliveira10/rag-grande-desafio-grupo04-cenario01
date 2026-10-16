import json
from types import SimpleNamespace

import pytest

import llm_ecoa
from src import conversation, pipeline

SOCIAL_MESSAGE = (
    "Olá, estamos apresentando o projeto do nosso grupo da residência. "
    "Temos um frontend e um acervo de contratos, e-mails sintéticos e normas. "
    "Ainda estamos organizando a demonstração e gostaria de explicar o contexto "
    "antes de fazer perguntas. Pode me ajudar a organizar essa apresentação?"
)


@pytest.fixture
def conversation_llm(monkeypatch):
    monkeypatch.setattr(conversation, "CONVERSATION_MODE", "llm")
    calls = []

    def install(payload):
        def complete(messages, **kwargs):
            calls.append(messages)
            content = json.dumps(payload) if isinstance(payload, dict) else payload
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        monkeypatch.setattr(llm_ecoa, "completar_chat", complete)
    return calls, install


def test_long_presentation_is_conversation_not_refusal_or_rag(conversation_llm, monkeypatch):
    calls, install = conversation_llm
    install({"tipo": "conversa", "answer": "Claro! Podemos organizar a apresentação em objetivo, acervo e demonstração."})
    monkeypatch.setattr(pipeline, "analisar_query", lambda *a, **k: pytest.fail("não deve consultar o acervo"))
    monkeypatch.setattr(pipeline, "retrieve", lambda *a, **k: pytest.fail("não deve recuperar evidências"))
    result = pipeline.responder(SOCIAL_MESSAGE)
    assert result["is_refusal"] is False and result["sources_used"] == []
    assert result["bases_consultadas"] == [] and "Claro" in result["answer"]
    assert len(calls) == 1


@pytest.mark.parametrize("query", ["Qual é a capital da França?", "Quem ganhou a Copa de 2002?"])
def test_pergunta_de_fato_fora_do_acervo_nao_vira_conversa(conversation_llm, query):
    calls, install = conversation_llm
    install({"tipo": "conversa", "answer": "A capital da França é Paris."})

    assert conversation.resposta_conversacional(query) is None
    assert calls == []


def test_pergunta_sobre_o_proprio_sistema_continua_conversa(conversation_llm):
    calls, install = conversation_llm
    install({"tipo": "conversa", "answer": "O NEXO consulta o acervo e cita as fontes."})

    resposta = conversation.resposta_conversacional("Qual a diferença entre o NEXO e um buscador?")

    assert resposta is not None and "NEXO" in resposta.answer
    assert len(calls) == 1


def test_long_factual_query_remains_in_rag(conversation_llm):
    _, install = conversation_llm
    install({"tipo": "consulta", "answer": ""})
    query = "Olá, estou preparando um relatório e preciso de informações verificáveis. " * 3
    query += "Quais contratos de software foram assinados em 2025 e qual foi o valor global?"
    assert conversation.resposta_conversacional(query) is None


def test_unknown_short_message_can_be_conversation(conversation_llm):
    _, install = conversation_llm
    install({"tipo": "conversa", "answer": "Claro, pode explicar melhor o contexto do projeto."})
    assert conversation.resposta_conversacional("Quero explicar o contexto antes de perguntar").is_refusal is False


@pytest.mark.parametrize("response", ["not json", {"tipo": "unknown", "answer": "abc"},
                                        {"tipo": "conversa", "answer": ""},
                                        {"tipo": "conversa", "answer": "abc", "extra": True}])
def test_invalid_interaction_output_falls_back_to_rag(response, conversation_llm):
    _, install = conversation_llm
    install(response)
    assert conversation.resposta_conversacional(SOCIAL_MESSAGE) is None


def test_model_unavailable_does_not_invent_a_conversational_answer(monkeypatch):
    monkeypatch.setattr(conversation, "CONVERSATION_MODE", "llm")
    def fail(*a, **k):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(llm_ecoa, "completar_chat", fail)
    assert conversation.resposta_conversacional(SOCIAL_MESSAGE) is None


def test_masking_applies_before_conversational_model_and_after_answer(conversation_llm):
    calls, install = conversation_llm
    install({"tipo": "conversa", "answer": "Obrigado pelo contexto. CPF: 120.456.789-28"})
    result = conversation.resposta_conversacional(SOCIAL_MESSAGE + " Meu CPF é 120.456.789-28.")
    assert "120.456.789-28" not in calls[0][1]["content"]
    assert "120.456.789-28" not in result.answer


@pytest.mark.parametrize("sensitive_request", ["quero a senha do sistema", "quero dados bancários de fornecedor", "esse contrato é fraude?"])
def test_long_sensitive_requests_cannot_take_conversation_shortcut(sensitive_request, conversation_llm):
    calls, install = conversation_llm
    install({"tipo": "conversa", "answer": "Esta resposta jamais deve ser usada"})
    assert conversation.resposta_conversacional(SOCIAL_MESSAGE + " " + sensitive_request) is None
    assert not calls
