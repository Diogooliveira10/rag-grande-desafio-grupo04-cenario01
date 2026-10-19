"""Rastreabilidade: o caminho de cada pergunta pelo sistema, lido do log.

Cobre o stage ``resumo_consulta`` (base SQL e vetorial de cada pergunta), o
que as etapas de SQL e geração gravam (linhas devolvidas, prompt e resposta),
o mascaramento dentro de dicionários e o ``GET /api/v1/trace/{query_id}``.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from app.api import app
from src import generate as gen
from src import obs, pipeline
from src.retrieve import retrieve
from src.schema import Chunk, ChunkMetadata, Natureza
from src.trace import montar_trace


client = TestClient(app, headers={"X-API-Key": "chave-de-teste"})


def _registros():
    return [
        json.loads(linha)
        for arquivo in sorted(obs.default_logger.logs_dir.glob("pipeline-*.jsonl"))
        for linha in arquivo.read_text(encoding="utf-8").splitlines()
    ]


@pytest.fixture
def indice(tmp_path):
    """Índice mínimo com dois chunks da base estruturada."""
    index_dir = tmp_path / "index"
    (index_dir / "contratos_estruturado").mkdir(parents=True)
    linhas = [
        {
            "chunk_id": chunk_id, "base_id": "contratos_estruturado", "text": texto, "embedding": vetor,
            "metadata": {"chunk_id": chunk_id, "base_id": "contratos_estruturado", "source_file": f"{chunk_id}.json",
                         "doc_type": "json", "natureza": "estruturada"},
        }
        for chunk_id, texto, vetor in [
            ("c1", "contrato de ambulancias da secretaria de saude", [1.0, 0.0]),
            ("c2", "contrato de material de escritorio", [0.0, 1.0]),
        ]
    ]
    conteudo = "\n".join(json.dumps(linha, ensure_ascii=False) for linha in linhas)
    (index_dir / "contratos_estruturado" / "vectors.jsonl").write_text(conteudo, encoding="utf-8")
    documents = tmp_path / "documents.jsonl"
    documents.write_text(conteudo, encoding="utf-8")
    return {"index_dir": index_dir, "documents_file": documents, "embed_fn": lambda textos: [[1.0, 0.0]]}


@pytest.fixture
def pipeline_com_indice(monkeypatch, indice):
    monkeypatch.setattr(pipeline, "retrieve", lambda q, rota, **kw: retrieve(q, rota, **indice, **kw))


def _resposta_sdk(answer="Há um contrato de ambulâncias.", sources=("c1",)):
    conteudo = json.dumps({
        "answer": answer, "is_refusal": False, "confidence_level": "alta", "sources_used": list(sources),
    })
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=conteudo))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _chunk_sql():
    return Chunk(
        content="Consulta SQL: 1 contratação\n- Ambulância tipo D | R$ 0,00",
        metadata=ChunkMetadata(source_file="data/structured/base.sqlite", chunk_id="sql:contratacoes:listagem:abc:1",
                               base_id="contratos_estruturado", doc_type="sql", natureza="estruturada"),
    )


# --------------------------------------------------------------------------
# resumo_consulta: SQL e vetorial em cada pergunta
# --------------------------------------------------------------------------


def test_resumo_diz_que_a_pergunta_usou_so_a_base_vetorial(pipeline_com_indice):
    pipeline.responder("Quais contratos de ambulancias foram publicados?", cliente=lambda m: _resposta_sdk())

    resumo = next(r for r in _registros() if r["stage"] == "resumo_consulta")
    assert resumo["stage"] == "resumo_consulta"
    assert resumo["status"] == "ok"
    assert resumo["output"]["caminho"] == "consulta"
    assert resumo["output"]["bases_roteadas"] == ["estruturada"]
    assert resumo["output"]["consultou_vetorial"] is True
    assert resumo["output"]["consultou_sql"] is False
    assert resumo["output"]["evidencias_sql"] == 0
    assert resumo["output"]["evidencias_vetorial"] == {"contratos_estruturado": 2}
    assert resumo["output"]["fontes_citadas"] == ["c1"]


def test_resumo_diz_que_a_pergunta_usou_sql_e_vetorial(pipeline_com_indice, monkeypatch):
    monkeypatch.setattr(pipeline, "consultar_estruturado", lambda *a, **kw: [_chunk_sql()])

    pipeline.responder(
        "Quantos contratos de ambulancias foram publicados?",
        cliente=lambda m: _resposta_sdk(sources=("sql:contratacoes:listagem:abc:1",)),
    )

    resumo = next(r["output"] for r in _registros() if r["stage"] == "resumo_consulta")
    assert resumo["consultou_sql"] is True
    assert resumo["consultou_vetorial"] is True
    assert resumo["evidencias_sql"] == 1
    assert resumo["fontes_citadas"] == ["sql:contratacoes:listagem:abc:1"]


def test_resumo_de_recusa_do_roteador_nao_consulta_nenhuma_base():
    pipeline.responder("Esse contrato de ambulâncias foi fraude?", cliente=lambda m: _resposta_sdk())

    resumo = next(r for r in _registros() if r["stage"] == "resumo_consulta")
    assert resumo["stage"] == "resumo_consulta"
    assert resumo["status"] == "recusa"
    assert resumo["output"]["caminho"] == "recusa_sem_consulta"
    assert resumo["output"]["consultou_sql"] is False
    assert resumo["output"]["consultou_vetorial"] is False
    assert resumo["output"]["refusal_reason"] == "juizo_legalidade_contrato"


# --------------------------------------------------------------------------
# O que cada etapa grava
# --------------------------------------------------------------------------


def test_geracao_grava_prompt_e_resposta_mascarados():
    chunk = Chunk(
        content="contrato de ambulancias",
        metadata=ChunkMetadata(source_file="c1.json", chunk_id="c1", base_id="contratos_estruturado", doc_type="json"),
    )
    gen.gerar_resposta(
        "O contrato do CPF 123.456.789-09 é de ambulâncias?", [chunk], [Natureza.ESTRUTURADA],
        cliente=lambda m: _resposta_sdk(), run_id="run1", query_id="q_1",
    )

    geracao = next(r for r in _registros() if r["stage"] == "geracao")
    prompt = geracao["extra"]["prompt"]
    assert [m["role"] for m in prompt] == ["system", "user"]
    assert "contrato de ambulancias" in prompt[-1]["content"]
    assert "123.456.789-09" not in json.dumps(prompt, ensure_ascii=False)
    assert geracao["extra"]["resposta"] == {
        "answer": "Há um contrato de ambulâncias.", "sources_used": ["c1"], "refusal_reason": None,
    }


def test_recusa_sem_chunks_grava_a_resposta_e_nenhum_prompt():
    gen.gerar_resposta("pergunta", [], [Natureza.ESTRUTURADA], cliente=lambda m: _resposta_sdk())

    [geracao] = _registros()
    assert "prompt" not in geracao["extra"]
    assert geracao["extra"]["resposta"]["refusal_reason"] == "sem_evidencia"
    assert geracao["extra"]["resposta"]["sources_used"] == []


def test_log_mascara_dado_pessoal_dentro_de_dicionarios_e_listas():
    obs.registrar(
        run_id="run1", query_id="q_1", stage=obs.Stage.ROTEAMENTO, status="ok", latencia_ms=0,
        input={"pergunta": "CPF 123.456.789-09"}, output={"subperguntas": ["CPF 123.456.789-09"], "n": 1},
    )

    [registro] = _registros()
    assert registro["input"] == {"pergunta": "CPF [CPF omitido]"}
    assert registro["output"] == {"subperguntas": ["CPF [CPF omitido]"], "n": 1}


# --------------------------------------------------------------------------
# montar_trace e GET /api/v1/trace/{query_id}
# --------------------------------------------------------------------------


def test_trace_monta_o_caminho_da_pergunta_em_ordem(pipeline_com_indice):
    # "Explique": a listagem pura sai do SQL; esta pergunta precisa da busca.
    pergunta = "Quais contratos de ambulancias foram publicados? Explique o objeto."
    pipeline.responder(pergunta, cliente=lambda m: _resposta_sdk(), query_id="q_trace")

    trace = montar_trace("q_trace")

    assert trace["query_id"] == "q_trace"
    assert trace["pergunta"] == pergunta
    assert [e["stage"] for e in trace["etapas"]] == [
        "pipeline_inicio", "reformulacao", "interacao", "query_analysis", "roteamento",
        "retrieval_denso", "retrieval_bm25", "fusao_rrf", "rerank", "consulta_sql",
        "geracao", "validacao", "resumo_consulta", "pipeline_fim",
    ]
    assert [e["ordem"] for e in trace["etapas"]] == list(range(1, 15))
    bases = {e["stage"]: e["base"] for e in trace["etapas"]}
    assert bases["retrieval_denso"] == "vetorial" and bases["fusao_rrf"] == "vetorial"
    assert bases["geracao"] is None
    fusao = next(e for e in trace["etapas"] if e["stage"] == "fusao_rrf")
    assert [ev["chunk_id"] for ev in fusao["detalhes"]["evidencias"]] == ["c1", "c2"]
    assert trace["resumo"]["consultou_vetorial"] is True
    assert trace["resumo"]["consultou_sql"] is False


def test_trace_nao_tem_saida_nula_sem_motivo_nem_repete_as_etapas(pipeline_com_indice):
    pipeline.responder("Quais contratos de ambulancias foram publicados? Explique o objeto.",
                       cliente=lambda m: _resposta_sdk(), query_id="q_saidas")

    trace = montar_trace("q_saidas")
    etapas = {e["stage"]: e for e in trace["etapas"]}

    # Marco de início e etapa pulada não aparecem mais com output null.
    assert etapas["pipeline_inicio"]["output"]["marco"] == "início do pipeline"
    assert etapas["reformulacao"]["output"] == {"pulado": "sem_historico"}
    assert all(e["output"] is not None for e in trace["etapas"] if e["status"] in {"iniciado", "pulado"})
    # O resumo fica só no topo; a lista de etapas não se repete dentro dele.
    assert etapas["resumo_consulta"]["output"] == "ver o campo resumo, no topo do trace"
    assert "etapas" not in trace["resumo"]
    assert sum(trace["resumo"]["situacao_das_etapas"].values()) == len(trace["etapas"])
    assert trace["resumo"]["consultou_vetorial"] is True


def test_trace_de_log_antigo_sem_resumo_deduz_as_bases():
    for stage in (obs.Stage.ROTEAMENTO, obs.Stage.CONSULTA_SQL, obs.Stage.GERACAO):
        obs.registrar(run_id="run1", query_id="q_antigo", stage=stage, status="ok", latencia_ms=5,
                      input="Quantos contratos?", output="ok")

    trace = montar_trace("q_antigo")

    assert trace["resumo"] == {"caminho": None, "consultou_sql": True, "consultou_vetorial": False,
                               "situacao_das_etapas": {"ok": 3}}
    assert trace["latencia_total_ms"] == 15
    assert [e["base"] for e in trace["etapas"]] == [None, "sql", None]


def test_trace_de_log_antigo_tira_evidencias_e_confianca_das_etapas():
    def etapa(stage, output, extra):
        obs.registrar(run_id="run1", query_id="q_velho", stage=stage, status="ok", latencia_ms=1,
                      input="Quantos contratos?", output=output, extra=extra)

    etapa(obs.Stage.FUSAO_RRF, "3 chunks; top base: editais_normativo", {"evidencias": [
        {"chunk_id": "editais_normativo:a", "base_id": "editais_normativo"},
        {"chunk_id": "editais_normativo:b", "base_id": "editais_normativo"},
        {"chunk_id": "pca_agregado:c", "base_id": "pca_agregado"},
    ]})
    etapa(obs.Stage.CONSULTA_SQL, "2 linha(s) em 1 chunk(s)", {"linhas": 2, "chunks": 1})
    etapa(obs.Stage.GERACAO, "resposta media; 2 fontes",
          {"confidence_level": "media", "is_refusal": False, "num_sources": 2})
    etapa(obs.Stage.VALIDACAO, "2 fontes válidas", {"confidence_level": "baixa", "is_refusal": False})

    resumo = montar_trace("q_velho")["resumo"]

    assert resumo["evidencias_sql"] == 1
    assert resumo["evidencias_vetorial"] == {"editais_normativo": 2, "pca_agregado": 1}
    assert resumo["confidence_level"] == "baixa"
    assert resumo["is_refusal"] is False
    assert resumo["num_fontes"] == 2


def test_trace_de_log_antigo_com_latencia_decimal_responde_200():
    linha = {"ts": "2026-09-21T10:00:00+00:00", "run_id": "run1", "query_id": "q_decimal",
             "stage": "geracao", "status": "ok", "latencia_ms": 1234.6, "input": "x", "output": "y"}
    arquivo = obs.default_logger.logs_dir / "pipeline-2026-09-21.jsonl"
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(linha) + "\n", encoding="utf-8")

    resposta = client.get("/api/v1/trace/q_decimal")

    assert resposta.status_code == 200
    assert resposta.json()["etapas"][0]["latencia_ms"] == 1235


def test_trace_de_log_antigo_sem_lista_de_evidencias_usa_o_total_do_output():
    obs.registrar(run_id="run1", query_id="q_velho2", stage=obs.Stage.RERANK, status="ok", latencia_ms=1,
                  input="Quantos contratos?", output="10 chunks; top base: contratos_estruturado",
                  extra={"rerank_k": 10})

    assert montar_trace("q_velho2")["resumo"]["evidencias_vetorial"] == 10


def test_trace_de_query_id_desconhecido_e_none():
    obs.registrar(run_id="run1", query_id="q_outro", stage=obs.Stage.ROTEAMENTO, status="ok",
                  latencia_ms=0, input="x", output="y")

    assert montar_trace("q_inexistente") is None


def test_endpoint_trace_devolve_o_caminho(pipeline_com_indice):
    pipeline.responder("Quais contratos de ambulancias foram publicados?",
                       cliente=lambda m: _resposta_sdk(), query_id="q_api")

    response = client.get("/api/v1/trace/q_api")

    assert response.status_code == 200
    corpo = response.json()
    assert corpo["query_id"] == "q_api"
    assert corpo["resumo"]["consultou_vetorial"] is True
    assert corpo["etapas"][-1]["stage"] == "pipeline_fim"
    assert corpo["completo"] is True


def test_endpoint_trace_de_query_id_desconhecido_e_404():
    response = client.get("/api/v1/trace/q_inexistente")

    assert response.status_code == 404


# --------------------------------------------------------------------------
# session_id: o caminho da conversa inteira
# --------------------------------------------------------------------------


@pytest.fixture
def api_com_pipeline(pipeline_com_indice, monkeypatch, tmp_path):
    """API com o pipeline de verdade (índice mínimo, LLM falso) e histórico temporário."""
    from functools import partial

    from app import api as api_module
    from src.history_store import HistoricoStore

    monkeypatch.setattr(api_module.pipeline, "responder",
                        partial(pipeline.responder, cliente=lambda m: _resposta_sdk()))
    monkeypatch.setattr(api_module, "historico", HistoricoStore(db_path=tmp_path / "historico.sqlite"))


def test_responder_grava_session_id_em_todas_as_etapas(pipeline_com_indice):
    pipeline.responder("Quais contratos de ambulancias foram publicados?",
                       cliente=lambda m: _resposta_sdk(), session_id="s_conversa")

    registros = _registros()
    assert len(registros) == 14
    assert {r["session_id"] for r in registros} == {"s_conversa"}


def test_responder_sem_sessao_grava_session_id_nulo_e_nao_vaza_para_a_proxima(pipeline_com_indice):
    pipeline.responder("Quais contratos de ambulancias foram publicados?",
                       cliente=lambda m: _resposta_sdk(), session_id="s_a")
    pipeline.responder("Quais contratos de ambulancias foram publicados?",
                       cliente=lambda m: _resposta_sdk(), query_id="q_sem_sessao")

    sem_sessao = [r for r in _registros() if r["query_id"] == "q_sem_sessao"]
    assert sem_sessao and {r["session_id"] for r in sem_sessao} == {None}
    assert obs.sessao_atual() is None


def test_trace_da_pergunta_traz_o_session_id(pipeline_com_indice):
    pipeline.responder("Quais contratos de ambulancias foram publicados?",
                       cliente=lambda m: _resposta_sdk(), query_id="q_1", session_id="s_conversa")

    assert montar_trace("q_1")["session_id"] == "s_conversa"


def test_trace_da_sessao_lista_as_perguntas_em_ordem(pipeline_com_indice, monkeypatch):
    from src.trace import montar_trace_sessao

    responder = lambda pergunta, query_id, sessao: pipeline.responder(  # noqa: E731
        pergunta, cliente=lambda m: _resposta_sdk(), query_id=query_id, session_id=sessao)
    responder("Quais contratos de ambulancias foram publicados?", "q_1", "s_conversa")
    responder("Esse contrato de ambulâncias foi fraude?", "q_outra", "s_outra")
    monkeypatch.setattr(pipeline, "consultar_estruturado", lambda *a, **kw: [_chunk_sql()])
    responder("Quantos contratos de ambulancias foram publicados?", "q_2", "s_conversa")

    trace = montar_trace_sessao("s_conversa")

    assert trace["total_perguntas"] == 2
    assert [p["query_id"] for p in trace["perguntas"]] == ["q_1", "q_2"]
    assert [p["ordem"] for p in trace["perguntas"]] == [1, 2]
    assert trace["perguntas"][0]["pergunta"] == "Quais contratos de ambulancias foram publicados?"
    assert trace["perguntas"][0]["etapas"][-1] == "pipeline_fim"
    assert [p["resumo"]["consultou_sql"] for p in trace["perguntas"]] == [False, True]
    assert trace["consultou_sql"] == 1
    assert trace["consultou_vetorial"] == 2
    assert montar_trace_sessao("s_inexistente") is None


def test_api_devolve_session_id_e_reaproveita_o_enviado(api_com_pipeline):
    primeira = client.post("/api/v1/query", json={"query": "Quais contratos de ambulancias foram publicados?"})
    session_id = primeira.json()["session_id"]
    segunda = client.post("/api/v1/query", json={
        "query": "Quantos contratos de ambulancias foram publicados?", "session_id": session_id,
    })

    assert session_id.startswith("s_")
    assert segunda.json()["session_id"] == session_id

    trace = client.get(f"/api/v1/sessions/{session_id}/trace")
    assert trace.status_code == 200
    assert [p["query_id"] for p in trace.json()["perguntas"]] == [
        primeira.json()["query_id"], segunda.json()["query_id"],
    ]
    assert client.get(f"/api/v1/trace/{segunda.json()['query_id']}").json()["session_id"] == session_id

    historico = client.get("/api/v1/history", params={"session_id": session_id}).json()
    assert [item["session_id"] for item in historico] == [session_id, session_id]


def test_feedback_entra_no_log_com_a_sessao_da_pergunta(api_com_pipeline):
    resposta = client.post("/api/v1/query", json={
        "query": "Quais contratos de ambulancias foram publicados?", "session_id": "s_sessao_feedback_01",
    }).json()

    client.post("/api/v1/feedback", json={"query_id": resposta["query_id"], "avaliacao": "positivo"})

    feedback = next(r for r in _registros() if r["stage"] == "feedback")
    assert feedback["session_id"] == "s_sessao_feedback_01"


def test_endpoint_trace_da_sessao_desconhecida_e_404():
    assert client.get("/api/v1/sessions/s_sessao_inexistente/trace").status_code == 404


@pytest.mark.parametrize("session_id", ["com espaco com espaco", "a/b/c/d/e/f/g/h/i", "x" * 65, "s_curto"])
def test_session_id_invalido_e_recusado(session_id):
    response = client.post("/api/v1/query", json={"query": "pergunta", "session_id": session_id})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Dono da conversa: X-Session-Id no lugar da chave
# --------------------------------------------------------------------------


def _conversa_sem_chave():
    """Duas perguntas pela API, como o site público faz: sem X-API-Key."""
    usuario = TestClient(app)
    primeira = usuario.post("/api/v1/query", json={"query": "Quais contratos de ambulancias foram publicados?"}).json()
    return usuario, primeira["session_id"], primeira["query_id"]


def test_session_id_gerado_pela_api_e_longo_e_aleatorio():
    ids = {obs.new_session_id() for _ in range(50)}

    assert len(ids) == 50
    assert all(i.startswith("s_") and len(i) >= 24 for i in ids)


def test_dono_da_conversa_ve_o_proprio_trace_sem_chave(api_com_pipeline):
    usuario, session_id, query_id = _conversa_sem_chave()
    dono = {"X-Session-Id": session_id}

    trace = usuario.get(f"/api/v1/trace/{query_id}", headers=dono)
    sessao = usuario.get(f"/api/v1/sessions/{session_id}/trace", headers=dono)

    assert trace.status_code == 200 and trace.json()["session_id"] == session_id
    assert sessao.status_code == 200 and sessao.json()["total_perguntas"] == 1


def test_dono_ve_o_trace_mesmo_sem_rag_api_key_no_servidor(api_com_pipeline, monkeypatch):
    usuario, session_id, query_id = _conversa_sem_chave()
    monkeypatch.setattr(config, "RAG_API_KEY", "")

    response = usuario.get(f"/api/v1/trace/{query_id}", headers={"X-Session-Id": session_id})

    assert response.status_code == 200


def test_outra_sessao_nao_ve_o_trace_alheio(api_com_pipeline):
    usuario, session_id, query_id = _conversa_sem_chave()
    intruso = {"X-Session-Id": "s_outra_sessao_qualquer"}

    assert usuario.get(f"/api/v1/trace/{query_id}", headers=intruso).status_code == 401
    assert usuario.get(f"/api/v1/sessions/{session_id}/trace", headers=intruso).status_code == 401
    assert usuario.get(f"/api/v1/trace/{query_id}").status_code == 401


def test_sem_credencial_trace_inexistente_e_401_e_nao_404():
    sem_chave = TestClient(app)

    assert sem_chave.get("/api/v1/trace/q_inexistente").status_code == 401


def test_pergunta_sem_sessao_so_aparece_com_a_chave(pipeline_com_indice):
    pipeline.responder("Quais contratos de ambulancias foram publicados?",
                       cliente=lambda m: _resposta_sdk(), query_id="q_sem_dono")

    assert TestClient(app, headers={"X-Session-Id": "s_qualquer_sessao_x"}).get(
        "/api/v1/trace/q_sem_dono").status_code == 401
    assert client.get("/api/v1/trace/q_sem_dono").status_code == 200


# --------------------------------------------------------------------------
# Endereço do banco fora do que chega ao usuário
# --------------------------------------------------------------------------


def test_trace_de_log_antigo_nao_mostra_o_host_do_postgres():
    from src.trace import montar_trace_sessao

    host = "postgres://ep-exemplo-pooler.us-east-2.aws.neon.tech"
    obs.registrar(run_id="run1", query_id="q_sql", stage=obs.Stage.CONSULTA_SQL, status="ok", latencia_ms=5,
                  input="Quantos contratos?", output="1 linha(s)", session_id="s_sessao_banco_antiga",
                  extra={"banco": host, "sql": "SELECT 1", "chunks": [{"source_file": host}]})

    for trace in (montar_trace("q_sql"), montar_trace_sessao("s_sessao_banco_antiga")):
        assert "neon.tech" not in json.dumps(trace)
    etapa = montar_trace("q_sql")["etapas"][0]
    assert etapa["detalhes"]["banco"] == "postgres"
    assert etapa["detalhes"]["sql"] == "SELECT 1"


def test_resposta_da_api_nao_mostra_o_host_em_fonte_sql_antiga(monkeypatch):
    from app import api as api_module

    payload = {"query": "q", "answer": "a", "bases_consultadas": ["estruturada"], "confidence_level": "alta",
               "is_refusal": False, "refusal_reason": None,
               "sources_used": [{"chunk_id": "sql:contratacoes:x:1", "base_id": "contratos_estruturado",
                                 "source_file": "postgres://ep-exemplo.neon.tech"},
                                {"chunk_id": "c1", "base_id": "contratos_estruturado",
                                 "source_file": r"data\raw\v6\c1.json"}]}
    monkeypatch.setattr(api_module.pipeline, "responder", lambda *a, **kw: payload)

    fontes = client.post("/api/v1/query", json={"query": "q"}).json()["sources_used"]

    assert [f["source_file"] for f in fontes] == ["postgres", "c1.json"]
