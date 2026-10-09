"""A decisão "conversa ou consulta" tem etapa própria no log (``interacao``).

Antes ela era gravada como ``query_analysis``, e uma mensagem de cortesia
aparecia no log como se fosse análise de consulta.
"""

import json
from types import SimpleNamespace

import llm_ecoa
from src import conversation, obs, pipeline

MENSAGEM_DE_CONVERSA = (
    "Olá, estamos apresentando o projeto do nosso grupo da residência. "
    "Temos um frontend e um acervo de contratos, e-mails sintéticos e normas. "
    "Pode me ajudar a organizar a apresentação antes de começarmos as perguntas?"
)
MENSAGEM_DE_CONSULTA = (
    "Olá, estou preparando um relatório para a minha equipe e preciso de informações verificáveis. "
    "Quais contratos de software foram assinados em 2025 e qual foi o valor global de cada um?"
)


def _registros():
    arquivos = sorted(obs.default_logger.logs_dir.glob("pipeline-*.jsonl"))
    return [json.loads(linha) for arquivo in arquivos
            for linha in arquivo.read_text(encoding="utf-8").splitlines() if linha.strip()]


def _modelo_classifica(monkeypatch, decisao):
    monkeypatch.setattr(conversation, "CONVERSATION_MODE", "llm")

    def completar(mensagens, **kwargs):
        conteudo = json.dumps(decisao)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=conteudo))])

    monkeypatch.setattr(llm_ecoa, "completar_chat", completar)


def test_mensagem_de_conversa_e_registrada_como_interacao(monkeypatch):
    _modelo_classifica(monkeypatch, {"tipo": "conversa", "answer": "Claro, vamos organizar a apresentação."})

    pipeline.responder(MENSAGEM_DE_CONVERSA)

    todos = _registros()
    assert todos[0]["stage"] == "pipeline_inicio"
    assert todos[-1]["stage"] == "pipeline_fim"
    registros = [r for r in todos if r["status"] != "pulado"
                 and r["stage"] not in {"pipeline_inicio", "pipeline_fim"}]
    assert [r["stage"] for r in registros] == ["interacao", "geracao", "resumo_consulta"]
    assert registros[0]["output"] == {"tipo_interacao": "conversa"}
    assert registros[1]["status"] == "conversa"
    assert registros[2]["output"]["caminho"] == "conversa"
    assert registros[2]["output"]["consultou_sql"] is False
    assert registros[2]["output"]["consultou_vetorial"] is False
    assert len({r["run_id"] for r in registros}) == 1


def test_consulta_passa_pela_interacao_e_depois_pela_query_analysis(monkeypatch):
    _modelo_classifica(monkeypatch, {"tipo": "consulta", "answer": ""})
    monkeypatch.setattr(pipeline, "retrieve", lambda *a, **k: [])

    pipeline.responder(MENSAGEM_DE_CONSULTA)

    registros = [r for r in _registros() if r["status"] != "pulado"
                 and r["stage"] not in {"pipeline_inicio", "pipeline_fim"}]
    assert [r["stage"] for r in registros][:2] == ["interacao", "query_analysis"]
    assert registros[0]["output"] == {"tipo_interacao": "consulta"}
    assert len({r["run_id"] for r in registros}) == 1
