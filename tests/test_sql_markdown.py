"""Markdown is built from calculated SQL data, logged whole, never by the model."""
import json
from functools import partial

import pytest

from src import generate, pipeline, structured_db, structured_query as sq
from src.schema import Natureza, QueryFilters
from src.sql_markdown import tabela_markdown, texto_markdown
from tests.test_numerical_validation import contract, modelo_template_sql
from tests.test_pipeline_observability import event


@pytest.fixture(autouse=True)
def _apresentacao_pelo_llm_ligada(monkeypatch):
    # Estes testes cobrem a apresentação pelo LLM, desligada por padrão
    # (config.SQL_APRESENTACAO_LLM).
    monkeypatch.setattr(generate.config, "SQL_APRESENTACAO_LLM", True)
from tests.test_structured_db import escrever_documents


@pytest.fixture
def facts(tmp_path, monkeypatch):
    rows = [contract(1, 0), contract(2, 0.01), contract(3, 100), contract(4, 300), contract(5, None)]
    for i, row in enumerate(rows):
        row['orgaoEntidade']['razaoSocial'] = 'ORGÃO A' if i < 3 else 'ORGÃO B'
    documents = escrever_documents(tmp_path / 'documents.jsonl', contratacoes=rows)
    banco = structured_db.Banco(sqlite_path=tmp_path / 'facts.sqlite')
    structured_db.construir_banco(documents, banco)
    monkeypatch.setattr(pipeline, 'consultar_estruturado', partial(sq.consultar_estruturado, banco=banco, documents_file=documents))
    return banco, documents


def chunks_for(facts, consulta):
    banco, _ = facts
    recorte, blocks = {}, []
    sql, header, lines = sq.executar_consulta(consulta, banco, recorte, markdown=blocks)
    return sq._chunks(consulta, sql, header, lines, banco, recorte, markdown='\n\n'.join(blocks))


def test_cells_escape_markdown_html_pipes_and_newlines():
    result = tabela_markdown(['Entidade', 'Valor'], [['A | B\nC [link](javascript:x) <img src=x> *D* `E`', 'R$ 0,00']], numericas=[1])
    assert '| --- | ---: |' in result
    assert 'A \\| B C' in result
    assert r'\[link\]\(javascript:x\)' in result
    assert '<img' not in result and '&lt;img' in result
    assert r'\*D\*' in result and r'\`E\`' in result
    assert len(result.splitlines()) == 3


def test_null_and_empty_tables_are_distinct_from_zero():
    assert texto_markdown(None) == 'não informado'
    assert tabela_markdown(['Valor'], [[0]]) == '| Valor |\n| --- |\n| 0 |'
    assert tabela_markdown(['Valor'], []) == ''
    with pytest.raises(ValueError):
        tabela_markdown(['A', 'B'], [['short']])


@pytest.mark.parametrize('metric,expected', [('contagem', '5 registros'), ('soma', 'R$ 400,01'),
    ('media', 'R$ 200,00'), ('mediana', 'R$ 200,00'), ('minimo', 'R$ 100,00'), ('maximo', 'R$ 300,00')])
def test_statistics_preserve_count_cents_and_informative_value_policy(facts, metric, expected):
    consulta = sq.Consulta(tabela='contratacoes', tipo='estatistica', metrica=metric, so_contratos=True)
    chunks = chunks_for(facts, consulta)
    result = generate.gerar_resposta('Resultado?', chunks, [Natureza.ESTRUTURADA], cliente=modelo_template_sql)
    assert '| Recorte | Registros |' in result.answer
    assert expected in result.answer
    assert '1 registro sem valor informado' in result.answer
    assert 'Fonte: [1]' in result.answer
    assert len(result.sources_used) == 1
    assert '{{sql}}' not in result.answer


@pytest.mark.parametrize('kind,metric', [('ranking', 'maximo'), ('listagem', 'contagem'),
    ('agrupamento', 'soma'), ('agrupamento', 'mediana'), ('evolucao', 'soma')])
def test_each_supported_shape_builds_a_real_table(facts, kind, metric):
    consulta = sq.Consulta(tabela='contratacoes', tipo=kind, metrica=metric, agrupar_por='orgao_nome',
                          so_contratos=True, referencia_temporal='assinatura', ano=2025, listagem_direta=True)
    chunks = chunks_for(facts, consulta)
    markdown = chunks[0].metadata.extras['resultado_sql']['markdown']
    assert '| ---' in markdown
    if kind in ('ranking', 'listagem'):
        assert '| Posição | PNCP | Órgão | Fornecedor | Valor registrado | Assinatura |' in markdown
    if kind == 'agrupamento':
        assert '| Órgão | Registros |' in markdown and 'ORGÃO A' in markdown
    if kind == 'evolucao':
        assert '| Período | Registros |' in markdown and '2025-10' in markdown


def test_no_match_keeps_absence_in_sample_not_a_zero_table(facts):
    consulta = sq.Consulta(tabela='contratacoes', tipo='estatistica', metrica='soma',
                          so_contratos=True, ano=2024, referencia_temporal='assinatura')
    chunks = chunks_for(facts, consulta)
    result = generate.gerar_resposta('Total 2024?', chunks, [Natureza.ESTRUTURADA], cliente=modelo_template_sql)
    assert 'Não foram encontrados registros' in result.answer
    assert '| ---' not in result.answer and 'R$ 0,00' not in result.answer


def test_pipeline_and_logs_contain_same_markdown_not_just_legacy_row_summary(facts, monkeypatch):
    monkeypatch.setattr(pipeline, 'retrieve', lambda *a, **k: pytest.fail('SQL is sufficient'))
    result = pipeline.responder('Qual o valor total dos contratos assinados em 2025?', cliente=modelo_template_sql)
    sql = event('consulta_sql')['extra']['resultado_markdown']
    assert '| Recorte | Registros | Total registrado |' in sql
    assert 'R$ 400,01' in sql
    generation = event('geracao')['extra']
    assert sql in result['answer']
    assert sql in generation['resultado_sql_markdown']
    assert '| ---' in generation['resposta']['answer']
    assert generation['chunks_no_prompt'] == 0
    assert generation['metodo'] == 'sql_template'


def test_rejected_model_template_preserves_markdown_table(facts):
    chunks = chunks_for(facts, sq.Consulta(tabela='contratacoes', tipo='estatistica', metrica='soma', so_contratos=True))
    result = generate.gerar_resposta('Total?', chunks, [Natureza.ESTRUTURADA], cliente=lambda _: '{"answer":"no marker"}')
    assert '| Recorte | Registros | Total registrado |' in result.answer
    assert 'R$ 400,01' in result.answer
    assert event('geracao')['extra']['metodo'] == 'sql_template_fallback'


def test_full_table_logged_beyond_answer_and_row_summary_limits(tmp_path, monkeypatch):
    rows = [contract(i, 100 + i) for i in range(1, 36)]
    for row in rows:
        row['objetoContrato'] = 'Objeto extenso ' * 20
    documents = escrever_documents(tmp_path / 'documents.jsonl', contratacoes=rows)
    banco = structured_db.Banco(sqlite_path=tmp_path / 'facts.sqlite')
    structured_db.construir_banco(documents, banco)
    chunks = sq.consultar_estruturado('Quais os 35 maiores contratos de 2025?', QueryFilters(ano=2025),
        [Natureza.ESTRUTURADA], banco=banco, documents_file=documents)
    sql_markdown = event('consulta_sql')['extra']['resultado_markdown']
    assert len(sql_markdown) > 8000
    assert '| 35 |' in sql_markdown
    result = generate.gerar_resposta('Maiores?', chunks, [Natureza.ESTRUTURADA], cliente=modelo_template_sql)
    assert result.answer.count('| Posição |') == 1
    assert '| 35 |' in result.answer
    assert '| 35 |' in event('geracao')['extra']['resultado_sql_markdown']
    assert event('geracao')['extra']['answer_truncated'] is True
    # Old plain row summaries remain available and explicitly bounded.
    assert len(event('consulta_sql')['extra']['resultado']) == 20


def test_markdown_is_masked_before_response_and_complete_generation_log(facts):
    chunks = chunks_for(facts, sq.Consulta(tabela='contratacoes', tipo='ranking', so_contratos=True))
    chunks[0].metadata.extras['resultado_sql']['markdown'] += '\n\nCPF: 123.456.789-09'
    result = generate.gerar_resposta('Maiores?', chunks, [Natureza.ESTRUTURADA], cliente=modelo_template_sql)
    assert '123.456.789-09' not in result.answer
    assert '123.456.789-09' not in event('geracao')['extra']['resultado_sql_markdown']
