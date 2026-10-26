"""O caso de uso com peças falsas injetadas pelas portas (src/ports.py), sem monkeypatch.

Análise e roteamento são os de verdade (por regras, sem LLM); busca, SQL e
geração são falsos e registram como foram chamados.
"""

import dataclasses

import pytest

from src import pipeline
from src.schema import Chunk, ChunkMetadata, ConfidenceLevel, Natureza, RAGResponse


class _Registro:
    def __init__(self) -> None:
        self.buscas: list[str] = []
        self.consultas_sql: list[str] = []
        self.geracoes: list[tuple[str, int]] = []
        self.rerankers_carregados = 0


def _chunk_sql() -> Chunk:
    return Chunk(content="Geral: 31 registros.", metadata=ChunkMetadata(
        chunk_id="sql:teste", document_id="sql", base_id="contratos_estruturado", doc_type="resultado_sql",
        natureza=Natureza.ESTRUTURADA, source_file="base.sqlite"))


@pytest.fixture
def registro():
    return _Registro()


@pytest.fixture
def deps(registro):
    def buscar(query, route, **_):
        registro.buscas.append(query)
        return []

    def consultar_sql(query, filtros, bases, **_):
        registro.consultas_sql.append(query)
        return [_chunk_sql()]

    def gerar(query, chunks, bases, *, refusal_reason_hint=None, **_):
        registro.geracoes.append((query, len(chunks)))
        if refusal_reason_hint is not None:
            return RAGResponse(query=query, answer="Recusa.", bases_consultadas=[],
                               confidence_level=ConfidenceLevel.RECUSADO, is_refusal=True,
                               refusal_reason=refusal_reason_hint)
        return RAGResponse(query=query, answer="ok", bases_consultadas=bases,
                           confidence_level=ConfidenceLevel.ALTA)

    def reranker_padrao():
        registro.rerankers_carregados += 1
        return None

    return dataclasses.replace(pipeline.dependencias_padrao(), buscar=buscar, consultar_sql=consultar_sql,
                               gerar=gerar, reranker_padrao=reranker_padrao)


def test_sql_suficiente_nao_busca_nem_carrega_reranker(deps, registro):
    deps = dataclasses.replace(deps, pode_consultar_sql_direto=lambda *a, **k: True,
                               pode_responder_sql=lambda *a: True)
    resposta = pipeline.responder("Quantos contratos de ambulância foram firmados em 2025?", dependencias=deps)
    assert resposta["answer"] == "ok"
    assert registro.consultas_sql and not registro.buscas
    assert registro.rerankers_carregados == 0
    assert registro.geracoes == [("Quantos contratos de ambulância foram firmados em 2025?", 1)]


def test_sql_insuficiente_busca_e_carrega_reranker_uma_vez(deps, registro):
    deps = dataclasses.replace(deps, pode_consultar_sql_direto=lambda *a, **k: True,
                               pode_responder_sql=lambda *a: False)
    pipeline.responder("O que estabelece a Lei 14.133/2021 sobre contratos de ambulância?", dependencias=deps)
    assert registro.buscas
    assert registro.rerankers_carregados == 1


def test_recusa_nao_consulta_nenhuma_base(deps, registro):
    resposta = pipeline.responder("Qual é a conta bancária do fornecedor do maior contrato?", dependencias=deps)
    assert resposta["is_refusal"] is True
    assert not registro.buscas and not registro.consultas_sql
    assert registro.geracoes[0][1] == 0


def test_conversa_nao_chega_a_busca_nem_a_geracao(deps, registro):
    resposta = pipeline.responder("Olá", dependencias=deps)
    assert "Olá!" in resposta["answer"]
    assert not registro.buscas and not registro.consultas_sql and not registro.geracoes


def test_conversa_injetada_substitui_a_padrao(deps, registro):
    deps = dataclasses.replace(deps, conversa=lambda query, **_: RAGResponse(
        query=query, answer="resposta da conversa falsa", bases_consultadas=[],
        confidence_level=ConfidenceLevel.ALTA))
    assert pipeline.responder("qualquer coisa", dependencias=deps)["answer"] == "resposta da conversa falsa"
