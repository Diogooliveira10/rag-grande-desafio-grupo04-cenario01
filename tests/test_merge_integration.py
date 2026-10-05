"""Regressões da combinação dos planos locais com as correções de main."""
from types import SimpleNamespace

import pytest

import config
import llm_ecoa
from src import query_analysis as qa, structured_db, structured_query as sq
from src.schema import Natureza, QueryFilters, QueryIntent
from tests.test_structured_db import _contratacao, escrever_documents


@pytest.mark.parametrize("question,lower,upper", [
    ("contratos acima de R$ 100 mil", True, False),
    ("contratos com valor igual ou superior a R$ 100 mil", False, False),
    ("contratos abaixo de R$ 500 mil", False, True),
    ("contratos com valor inferior ou igual a R$ 500 mil", False, False),
    ("contratos entre R$ 100 mil e R$ 500 mil", False, False),
    ("depois de 2023, contratos com valor até R$ 500 mil", False, False),
])
def test_exclusividade_depende_do_gatilho_monetario(question, lower, upper):
    filtros = qa.analisar_query(question, modo="regras").filtros
    assert filtros.valor_min_exclusivo is lower
    assert filtros.valor_max_exclusivo is upper


def test_plano_tipado_preserva_periodo_aberto_e_modalidade_normalizada(tmp_path):
    docs = escrever_documents(tmp_path / "documents.jsonl", contratacoes=[
        _contratacao("A-1", data="2024-03-01", valor_estimado=100),
        _contratacao("A-2", data="2025-03-01", valor_estimado=200),
        _contratacao("A-3", data="2025-03-01", valor_estimado=300, modalidade="Dispensa"),
    ])
    banco = structured_db.Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(docs, banco)
    intent = QueryIntent(pergunta="Quantos pregões eletrônicos desde 2025?",
        bases=[Natureza.ESTRUTURADA], operacao="estatistica",
        filtros=QueryFilters(ano_desde=2025, referencia_temporal="publicacao",
                            metrica="contagem", filtros_analiticos={"modalidade": "pregao eletronico"}))
    consulta = sq.consulta_da_intencao(intent)
    _, header, rows = sq.executar_consulta(consulta, banco)
    assert "ano>=2025" in header
    assert "modalidade em Pregão - Eletrônico" in header
    assert "1 registros" in rows[0]


def test_mediana_agrupada_exclui_simbolicos_sem_perder_contagem(tmp_path):
    docs = escrever_documents(tmp_path / "documents.jsonl", contratacoes=[
        _contratacao("A-1", valor_estimado=0),
        _contratacao("A-2", valor_estimado=0.01),
        _contratacao("A-3", valor_estimado=100),
        _contratacao("A-4", valor_estimado=300),
    ])
    banco = structured_db.Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(docs, banco)
    consulta = sq.Consulta(tabela="contratacoes", tipo="agrupamento", agrupar_por="esfera",
                          metrica="mediana", minimo_por_grupo=4)
    _, _, rows = sq.executar_consulta(consulta, banco)
    assert "mediana R$ 200,00" in rows[0]
    _, _, rows = sq.executar_consulta(sq.Consulta(tabela="contratacoes", tipo="estatistica",
                                               metrica="contagem", plano_tipado=True), banco)
    assert "4 registros" in rows[0]


def test_cliente_compartilhado_respeita_timeout_e_retries_de_main(monkeypatch):
    calls = []
    response = SimpleNamespace(model="test", choices=[])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: response)))
    def with_options(**options):
        calls.append(options)
        return client
    client.with_options = with_options
    monkeypatch.setattr(llm_ecoa, "client", client)
    monkeypatch.setattr(config, "LLM_PROVIDER_MODE", "auto")
    monkeypatch.setattr(config, "GENERATION_TIMEOUT", 17)
    monkeypatch.setattr(config, "GENERATION_MAX_RETRIES", 2)
    assert llm_ecoa.completar_chat([]) is response
    assert calls == [{"timeout": 17, "max_retries": 2}]
