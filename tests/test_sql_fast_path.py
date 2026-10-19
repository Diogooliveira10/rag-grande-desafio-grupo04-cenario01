"""C: sufficient SQL avoids retrieval/embedding/reranker; other routes do not."""
from functools import partial

import pytest

from src import pipeline, structured_db, structured_query
from src.schema import Natureza, QueryAnalysisResult, QueryFilters, QueryIntent
from src.trace import montar_trace
from tests.test_numerical_validation import contract, modelo_template_sql
from tests.test_structured_db import escrever_documents


@pytest.fixture
def local_sql(tmp_path, monkeypatch):
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=[contract(1, 100), contract(2, 200)])
    banco = structured_db.Banco(sqlite_path=tmp_path / "facts.sqlite")
    structured_db.construir_banco(documents, banco)
    monkeypatch.setattr(pipeline, "consultar_estruturado", partial(structured_query.consultar_estruturado,
        banco=banco, documents_file=documents))
    return banco


@pytest.mark.parametrize("query", [
    "Qual o valor total dos contratos assinados em 2025?",
    "Quantos contratos foram assinados em 2025?",
    "Qual a mediana dos contratos de 2025?",
    "Quais os cinco maiores contratos de 2025?",
    "Qual o valor total dos contratos assinados em 2024?",
])
def test_pure_analytics_does_not_load_or_call_retrieval(local_sql, monkeypatch, query):
    from src import generate
    monkeypatch.setattr(generate.config, "SQL_APRESENTACAO_LLM", True)  # conta a chamada de apresentação
    monkeypatch.setattr(pipeline, "retrieve", lambda *a, **k: pytest.fail("retrieval must be skipped"))
    monkeypatch.setattr(pipeline, "reranker_padrao", lambda: pytest.fail("cross-encoder must not be loaded"))
    calls = []
    def model(messages):
        calls.append(messages)
        return modelo_template_sql(messages)
    response = pipeline.responder(query, cliente=model, query_id="q_sql_direct")
    assert len(calls) == 1  # somente apresentação; números são inseridos pelo backend
    assert not response["is_refusal"]
    trace = montar_trace("q_sql_direct")
    assert trace["resumo"]["consultou_sql"]
    assert not trace["resumo"]["consultou_vetorial"]
    skips = [e for e in trace["etapas"] if e["stage"] in {"retrieval_denso", "retrieval_bm25", "fusao_rrf", "rerank"}]
    assert len(skips) == 4
    assert all(e["status"] == "pulado" and e["detalhes"]["motivo"] == "calculo_sql_suficiente" for e in skips)


def test_sql_failure_keeps_existing_retrieval_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "consultar_estruturado", lambda *a, **k: calls.append("sql") or [])
    monkeypatch.setattr(pipeline, "retrieve", lambda *a, **k: calls.append("retrieval") or [])
    pipeline.responder("Qual o valor total dos contratos de 2025?")
    assert calls[0] == "sql" and "retrieval" in calls
    assert calls.count("sql") == 1


def test_typed_multiple_analytics_skip_retrieval_for_each_intent(local_sql, monkeypatch):
    intents = [QueryIntent(pergunta=q, operacao="estatistica", bases=[Natureza.ESTRUTURADA],
                           filtros=QueryFilters(ano=2025, metrica=m)) for q, m in [
        ("Qual o total dos contratos assinados em 2025?", "soma"),
        ("Qual a média dos contratos assinados em 2025?", "media")]]
    monkeypatch.setattr(pipeline, "analisar_query", lambda *a, **k: QueryAnalysisResult(query_original=a[0], filtros=QueryFilters(), intencoes=intents))
    monkeypatch.setattr(pipeline, "retrieve", lambda *a, **k: pytest.fail("both SQL intents are sufficient"))
    result = pipeline.responder("Qual o total e a média dos contratos assinados em 2025?", cliente=modelo_template_sql)
    assert "R$ 300,00" in result["answer"] and "R$ 150,00" in result["answer"]


def test_typed_mixed_query_skips_only_analytic_intent(local_sql, monkeypatch):
    from src.retrieve import RetrievalResult
    from src.schema import ChunkMetadata, RAGResponse
    intents = [
        QueryIntent(pergunta="Qual o total dos contratos de 2025?", operacao="estatistica",
                    bases=[Natureza.ESTRUTURADA], filtros=QueryFilters(ano=2025, metrica="soma")),
        QueryIntent(pergunta="Quais regras da Lei 14.133 se aplicam?", operacao="buscar",
                    bases=[Natureza.NORMATIVA], filtros=QueryFilters()),
    ]
    monkeypatch.setattr(pipeline, "analisar_query", lambda *a, **k: QueryAnalysisResult(
        query_original=a[0], filtros=QueryFilters(), intencoes=intents))
    calls, seen = [], []
    metadata = ChunkMetadata(source_file="lei.txt", chunk_id="norma-1", base_id="editais_normativo",
                             doc_type="txt", natureza=Natureza.NORMATIVA)
    def retrieve(query, route, **kwargs):
        calls.append(route["bases"])
        return [RetrievalResult(chunk_id="norma-1", content="Regra legal", score=1,
                                metadata=metadata.model_dump())]
    def generate(query, chunks, bases, **kwargs):
        seen.extend(c.metadata.chunk_id for c in chunks)
        return RAGResponse(query=query, answer="Resposta mista", bases_consultadas=bases, confidence_level="alta")
    monkeypatch.setattr(pipeline, "retrieve", retrieve)
    monkeypatch.setattr(pipeline, "gerar_resposta", generate)
    pipeline.responder("Total dos contratos e regras aplicáveis")
    assert calls == [[Natureza.NORMATIVA]]
    assert "norma-1" in seen and any(i.startswith("sql:") for i in seen)


@pytest.mark.parametrize("query,bases", [
    ("Qual o total dos contratos e quais regras legais se aplicam?", [Natureza.ESTRUTURADA, Natureza.NORMATIVA]),
    ("Quais contratos de computadores existem e há dados suficientes para concluir algo?", [Natureza.ESTRUTURADA]),
    ("Qual a média de preço unitário de computadores?", [Natureza.ESTRUTURADA, Natureza.AGREGADA]),
])
def test_mixed_listings_and_unsupported_metrics_are_not_fast_routes(query, bases):
    from src.query_analysis import analisar_query
    filtros = analisar_query(query).filtros
    if "unitário" in query:
        filtros = filtros.model_copy(update={"tipo_valor": "unitario"})
    assert not structured_query.pode_consultar_sql_direto(query, filtros, bases)


@pytest.mark.parametrize("query", [
    "Quais contratos de computadores existem?",
    "Quais órgãos contrataram manutenção de computadores em 2025 e quais foram os valores?",
])
def test_listagem_pura_e_rota_rapida(query):
    from src.query_analysis import analisar_query
    assert structured_query.pode_consultar_sql_direto(query, analisar_query(query).filtros, [Natureza.ESTRUTURADA])


@pytest.mark.parametrize("query", [
    "Quais contratos de computadores existem e por que foram feitos?",
    "Quais contratos de computadores existem e quais regras se aplicam a eles?",
])
def test_listagem_com_pedido_alem_da_lista_vai_ao_llm(query):
    from src.query_analysis import analisar_query
    assert not structured_query.pode_consultar_sql_direto(query, analisar_query(query).filtros, [Natureza.ESTRUTURADA])
