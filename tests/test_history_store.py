"""Histórico de consultas e feedback do usuário (src/history_store.py)."""

from contextlib import closing

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


def test_historico_filtra_por_sessao(store):
    for query_id, sessao in [("q_1", "s_a"), ("q_2", "s_b"), ("q_3", "s_a")]:
        store.registrar_consulta(query_id=query_id, run_id="r", query="p", top_k=5,
                                 resposta=_resposta(), session_id=sessao)

    assert [c["query_id"] for c in store.obter_historico(session_id="s_a")] == ["q_3", "q_1"]
    assert store.obter_consulta("q_2")["session_id"] == "s_b"
    assert len(store.obter_historico()) == 3


def test_historico_antigo_sem_coluna_session_id_e_migrado(tmp_path):
    import json
    import sqlite3

    db = tmp_path / "historico.sqlite"
    with sqlite3.connect(db) as conexao:
        conexao.execute(
            "CREATE TABLE consultas (query_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ts TEXT NOT NULL, "
            "query TEXT NOT NULL, top_k INTEGER NOT NULL, resposta_json TEXT NOT NULL, "
            "confidence_level TEXT, is_refusal INTEGER NOT NULL)"
        )
        conexao.execute("INSERT INTO consultas VALUES ('q_velha', 'r', '2026-10-01T00:00:00+00:00', 'p', 5, ?, "
                        "'alta', 0)", (json.dumps(_resposta()),))
    conexao.close()
    store = HistoricoStore(db_path=db, retencao_dias=0)

    store.registrar_consulta(query_id="q_nova", run_id="r", query="p", top_k=5,
                             resposta=_resposta(), session_id="s_a")

    assert store.obter_consulta("q_velha")["session_id"] is None
    assert store.obter_consulta("q_nova")["session_id"] == "s_a"


def test_historico_grava_pergunta_resposta_e_comentario_mascarados(store):
    store.registrar_consulta(
        query_id="q_1", run_id="r", query="Meu CPF é 123.456.789-09, tel (21) 99999-8888", top_k=5,
        resposta=_resposta(query="Meu CPF é 123.456.789-09", answer="Contato: joao@gmail.com"),
    )
    store.registrar_feedback(query_id="q_1", avaliacao="negativo", comentario="CPF 123.456.789-09")

    consulta = store.obter_consulta("q_1")

    assert consulta["query"] == "Meu CPF é [CPF omitido], tel [telefone omitido]"
    assert consulta["resposta"]["query"] == "Meu CPF é [CPF omitido]"
    assert consulta["resposta"]["answer"] == "Contato: [e-mail omitido]"
    assert consulta["resposta"]["sources_used"] == _resposta()["sources_used"]
    assert consulta["feedback"][0]["comentario"] == "CPF [CPF omitido]"


def _consulta_com_data(db, query_id, ts):
    import json
    import sqlite3

    with sqlite3.connect(db) as conexao:
        conexao.execute("INSERT INTO consultas (query_id, run_id, ts, query, top_k, resposta_json, is_refusal) "
                        "VALUES (?, 'r', ?, 'p', 5, ?, 0)", (query_id, ts, json.dumps(_resposta())))
        conexao.execute("INSERT INTO feedback (query_id, ts, avaliacao) VALUES (?, ?, 'positivo')", (query_id, ts))
    conexao.close()


def test_nova_consulta_expurga_as_que_passaram_da_retencao(tmp_path):
    from datetime import datetime, timedelta, timezone

    store = HistoricoStore(db_path=tmp_path / "historico.sqlite", retencao_dias=30)
    store.obter_historico()  # cria o schema
    agora = datetime.now(timezone.utc)
    _consulta_com_data(store.db_path, "q_velha", (agora - timedelta(days=31)).isoformat(timespec="seconds"))
    _consulta_com_data(store.db_path, "q_recente", (agora - timedelta(days=29)).isoformat(timespec="seconds"))

    store.registrar_consulta(query_id="q_nova", run_id="r", query="p", top_k=5, resposta=_resposta())

    assert {c["query_id"] for c in store.obter_historico()} == {"q_recente", "q_nova"}
    import sqlite3
    with sqlite3.connect(store.db_path) as conexao:
        assert conexao.execute("SELECT COUNT(*) FROM feedback WHERE query_id = 'q_velha'").fetchone()[0] == 0
    conexao.close()


def test_retencao_zero_nao_expurga(tmp_path):
    store = HistoricoStore(db_path=tmp_path / "historico.sqlite", retencao_dias=0)
    store.obter_historico()
    _consulta_com_data(store.db_path, "q_antiga", "2020-01-01T00:00:00+00:00")

    store.registrar_consulta(query_id="q_nova", run_id="r", query="p", top_k=5, resposta=_resposta())

    assert {c["query_id"] for c in store.obter_historico()} == {"q_antiga", "q_nova"}


def test_resposta_e_gravada_comprimida_e_lida_de_volta(tmp_path):
    import sqlite3
    import zlib

    db = tmp_path / "historico.sqlite"
    store = HistoricoStore(db_path=db)
    resposta = _resposta(answer="Contratos de ambulância em 2025. " * 40)
    store.registrar_consulta(query_id="q_1", run_id="r", query="p", top_k=5, resposta=resposta)

    with closing(sqlite3.connect(db)) as conexao:
        gravado = conexao.execute("SELECT resposta_json FROM consultas").fetchone()[0]

    assert isinstance(gravado, bytes)
    assert len(gravado) < len(zlib.decompress(gravado))
    assert store.obter_consulta("q_1")["resposta"] == resposta


def test_resposta_curta_fica_em_texto():
    from src.history_store import _comprimir_json, _ler_json

    assert _comprimir_json({"a": 1}) == '{"a": 1}'
    assert _ler_json(_comprimir_json({"a": 1})) == {"a": 1}


def test_resposta_antiga_em_texto_continua_legivel(tmp_path):
    import json

    db = tmp_path / "historico.sqlite"
    HistoricoStore(db_path=db).obter_historico()  # cria o schema
    _consulta_com_data(db, "q_texto", "2999-01-01T00:00:00+00:00")

    assert HistoricoStore(db_path=db).obter_consulta("q_texto")["resposta"] == json.loads(json.dumps(_resposta()))
