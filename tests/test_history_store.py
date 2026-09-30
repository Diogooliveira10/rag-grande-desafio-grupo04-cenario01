"""Histórico de consultas e feedback do usuário (src/history_store.py)."""

import pytest

from src.history_store import ConsultaNaoEncontrada, HistoricoStore


def _resposta(**overrides):
    base = {
        "query": "Quais contratos de ambulancias foram publicados?",
        "answer": "Resposta fundamentada.",
        "bases_consultadas": ["estruturada"],
        "sources_used": [{"chunk_id": "c1", "base_id": "contratos_estruturado", "source_file": "a.json"}],
        "confidence_level": "alta",
        "is_refusal": False,
        "refusal_reason": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def store(tmp_path) -> HistoricoStore:
    return HistoricoStore(db_path=tmp_path / "historico.sqlite")


def test_registrar_e_obter_consulta_faz_ida_e_volta(store):
    store.registrar_consulta(
        query_id="q_1", run_id="run_1", query="Qual e a norma?", top_k=5, resposta=_resposta()
    )

    consulta = store.obter_consulta("q_1")

    assert consulta["query_id"] == "q_1"
    assert consulta["run_id"] == "run_1"
    assert consulta["query"] == "Qual e a norma?"
    assert consulta["resposta"] == _resposta()
    assert consulta["feedback"] == []


def test_obter_consulta_desconhecida_devolve_none(store):
    assert store.obter_consulta("q_inexistente") is None


def test_registrar_feedback_para_consulta_desconhecida_levanta_erro(store):
    with pytest.raises(ConsultaNaoEncontrada):
        store.registrar_feedback(query_id="q_fantasma", avaliacao="positivo")


def test_feedback_fica_associado_a_consulta_e_aparece_no_historico(store):
    store.registrar_consulta(query_id="q_1", run_id="run_1", query="Pergunta", top_k=5, resposta=_resposta())

    store.registrar_feedback(query_id="q_1", avaliacao="negativo", comentario="Resposta incompleta.")

    consulta = store.obter_consulta("q_1")
    [feedback] = consulta["feedback"]
    assert feedback["avaliacao"] == "negativo"
    assert feedback["comentario"] == "Resposta incompleta."

    [historico] = store.obter_historico()
    assert historico["query_id"] == "q_1"
    assert historico["feedback"][0]["avaliacao"] == "negativo"


def test_uma_consulta_pode_receber_mais_de_um_feedback(store):
    store.registrar_consulta(query_id="q_1", run_id="run_1", query="Pergunta", top_k=5, resposta=_resposta())

    store.registrar_feedback(query_id="q_1", avaliacao="positivo")
    store.registrar_feedback(query_id="q_1", avaliacao="negativo", comentario="Mudei de ideia.")

    assert len(store.obter_consulta("q_1")["feedback"]) == 2


def test_obter_historico_respeita_o_limite_e_ordena_do_mais_recente(store):
    for i in range(5):
        store.registrar_consulta(
            query_id=f"q_{i}", run_id="run_1", query=f"Pergunta {i}", top_k=5, resposta=_resposta()
        )

    historico = store.obter_historico(limit=2)

    assert [item["query_id"] for item in historico] == ["q_4", "q_3"]


def test_registrar_consulta_e_idempotente_para_o_mesmo_query_id(store):
    store.registrar_consulta(query_id="q_1", run_id="run_1", query="Primeira", top_k=5, resposta=_resposta())
    store.registrar_consulta(
        query_id="q_1", run_id="run_1", query="Primeira", top_k=5, resposta=_resposta(answer="Atualizada")
    )

    assert store.obter_historico() == [store.obter_consulta("q_1")]
    assert store.obter_consulta("q_1")["resposta"]["answer"] == "Atualizada"
