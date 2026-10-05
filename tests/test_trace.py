"""Rastreabilidade: o caminho de cada pergunta pelo sistema, lido do log.

Cobre o stage ``resumo_consulta`` (base SQL e vetorial de cada pergunta), o
que as etapas de SQL e geração gravam (linhas devolvidas, prompt e resposta),
o mascaramento dentro de dicionários e o ``GET /api/v1/trace/{query_id}``.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import app
from src import generate as gen
from src import obs, pipeline
from src.retrieve import retrieve
from src.schema import Chunk, ChunkMetadata, Natureza
from src.trace import montar_trace


client = TestClient(app)


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

    resumo = _registros()[-1]
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

    resumo = _registros()[-1]["output"]
    assert resumo["consultou_sql"] is True
    assert resumo["consultou_vetorial"] is True
    assert resumo["evidencias_sql"] == 1
    assert resumo["fontes_citadas"] == ["sql:contratacoes:listagem:abc:1"]


def test_resumo_de_recusa_do_roteador_nao_consulta_nenhuma_base():
    pipeline.responder("Esse contrato de ambulâncias foi fraude?", cliente=lambda m: _resposta_sdk())

    resumo = _registros()[-1]
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
    pipeline.responder("Quais contratos de ambulancias foram publicados?",
                       cliente=lambda m: _resposta_sdk(), query_id="q_trace")

    trace = montar_trace("q_trace")

    assert trace["query_id"] == "q_trace"
    assert trace["pergunta"] == "Quais contratos de ambulancias foram publicados?"
    assert [e["stage"] for e in trace["etapas"]] == [
        "query_analysis", "roteamento", "retrieval_denso", "retrieval_bm25", "fusao_rrf",
        "geracao", "validacao", "resumo_consulta",
    ]
    assert [e["ordem"] for e in trace["etapas"]] == list(range(1, 9))
    bases = {e["stage"]: e["base"] for e in trace["etapas"]}
    assert bases["retrieval_denso"] == "vetorial" and bases["fusao_rrf"] == "vetorial"
    assert bases["geracao"] is None
    fusao = next(e for e in trace["etapas"] if e["stage"] == "fusao_rrf")
    assert [ev["chunk_id"] for ev in fusao["detalhes"]["evidencias"]] == ["c1", "c2"]
    assert trace["resumo"]["consultou_vetorial"] is True
    assert trace["resumo"]["consultou_sql"] is False


def test_trace_de_log_antigo_sem_resumo_deduz_as_bases():
    for stage in (obs.Stage.ROTEAMENTO, obs.Stage.CONSULTA_SQL, obs.Stage.GERACAO):
        obs.registrar(run_id="run1", query_id="q_antigo", stage=stage, status="ok", latencia_ms=5,
                      input="Quantos contratos?", output="ok")

    trace = montar_trace("q_antigo")

    assert trace["resumo"] == {"caminho": None, "consultou_sql": True, "consultou_vetorial": False}
    assert trace["latencia_total_ms"] == 15
    assert [e["base"] for e in trace["etapas"]] == [None, "sql", None]


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
    assert corpo["etapas"][-1]["stage"] == "resumo_consulta"


def test_endpoint_trace_de_query_id_desconhecido_e_404():
    response = client.get("/api/v1/trace/q_inexistente")

    assert response.status_code == 404
