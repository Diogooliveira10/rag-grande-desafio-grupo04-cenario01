"""Lifecycle regression tests: real pipeline/SQLite, isolated data, mocked models."""
import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

import config
from app import api
from src import log_context, obs, pipeline, structured_db, structured_query
from src.history_store import HistoricoStore
from src.retrieve import retrieve
from src.schema import Natureza, QueryAnalysisResult, QueryFilters, QueryIntent
from src.trace import montar_trace, montar_trace_sessao
from tests.test_structured_db import _contratacao, escrever_documents


def records():
    return [json.loads(line) for path in sorted(obs.default_logger.logs_dir.glob('pipeline-*.jsonl'))
            for line in path.read_text(encoding='utf-8').splitlines()]


def event(stage):
    return next(r for r in records() if r['stage'] == stage)


def model_response(messages):
    if "{{sql}}" in messages[0]["content"]:
        return json.dumps({'answer': 'Confira o resultado:\n\n{{sql}}'})
    return json.dumps({'answer': 'Há contratos de ambulâncias.', 'is_refusal': False,
                       'confidence_level': 'alta', 'sources_used': ['E1']})


@pytest.fixture
def local_pipeline(tmp_path, monkeypatch):
    index = tmp_path / 'index'
    base = index / 'contratos_estruturado'
    base.mkdir(parents=True)
    row = {'chunk_id': 'c1', 'base_id': 'contratos_estruturado',
           'text': 'Contrato para ambulância tipo D.', 'embedding': [1.0, 0.0],
           'metadata': {'chunk_id': 'c1', 'base_id': 'contratos_estruturado',
                        'source_file': 'contrato.json', 'natureza': 'estruturada', 'doc_type': 'json'}}
    (base / 'vectors.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
    sparse = tmp_path / 'sparse.jsonl'
    sparse.write_text(json.dumps(row) + '\n', encoding='utf-8')
    manifest = {'embedding_model': 'test-embedding', 'source_sha256': 'test-corpus-hash',
                'built_at': '2026-10-07T00:00:00+00:00', 'bases': [{'base_id': 'contratos_estruturado',
                 'total_chunks': 1, 'dimension': 2, 'inventory_versions': ['fixture']}],
                'encoding_config': {'checkpoint': 'SECRET-LOCAL-PATH'}}
    (index / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.setattr(config, 'INDEX_DIR', index)
    documents = escrever_documents(tmp_path / 'documents.jsonl', contratacoes=[
        _contratacao('M-1', valor_estimado=100.0, objeto='Ambulância tipo D')])
    banco = structured_db.Banco(sqlite_path=tmp_path / 'structured.sqlite')
    structured_db.construir_banco(documents, banco)
    monkeypatch.setattr(structured_query, 'DOCUMENTS_FILE', documents)
    monkeypatch.setattr(structured_db, 'banco_padrao', lambda: banco)
    monkeypatch.setattr(pipeline, 'retrieve', lambda q, route, **kwargs: retrieve(
        q, route, index_dir=index, documents_file=sparse, embed_fn=lambda texts: [[1.0, 0.0]], **kwargs))
    monkeypatch.setattr(api, 'historico', HistoricoStore(tmp_path / 'history.sqlite'))
    monkeypatch.setattr(api.pipeline, 'responder', partial(pipeline.responder, cliente=model_response))
    return TestClient(api.app)


def test_factual_api_trace_follows_evidence_to_prompt_to_final_response(local_pipeline, monkeypatch):
    # Segue a evidência até o prompt da apresentação pelo LLM, desligada por padrão.
    monkeypatch.setattr(config, 'SQL_APRESENTACAO_LLM', True)
    response = local_pipeline.post('/api/v1/query', json={
        'query': 'Qual o valor total de contratos de ambulâncias?', 'top_k': 5})
    assert response.status_code == 200
    body = response.json()
    events = records()
    assert events[0]['stage'] == 'requisicao_inicio'
    assert events[-1]['stage'] == 'requisicao_fim'
    assert {r['query_id'] for r in events} == {body['query_id']}
    assert {r['session_id'] for r in events} == {body['session_id']}
    assert {r['extra']['origem'] for r in events} == {'api'}
    assert [r['extra']['sequencia'] for r in events] == list(range(1, len(events) + 1))
    sql = event('consulta_sql')
    assert sql['status'] == 'ok' and sql['extra']['resultado']
    assert sql['extra']['execucao_sql']['consultas'][-1]['sql']
    generation = event('geracao')
    assert generation['extra']['metodo'] == 'sql_template'
    assert generation['extra']['llm_called'] is True
    assert '{{sql}}' in generation['extra']['prompt'][0]['content']
    assert generation['extra']['chunks_no_prompt'] == 0
    assert '{{sql}}' not in body['answer']
    assert generation['extra']['evidencias_usadas'][0].startswith('sql:')
    assert generation['extra']['resposta']['answer'] == body['answer']
    assert event('persistencia_historico')['output'] == {'saved': True}
    assert events[-1]['output'] == body
    assert events[-1]['extra']['history_saved'] is True
    trace_response = local_pipeline.get('/api/v1/trace/' + body['query_id'],
                                       headers={'X-Session-Id': body['session_id']})
    trace = trace_response.json()
    assert trace_response.status_code == 200
    assert trace['completo'] is True
    assert trace['status'] == 'ok'
    assert trace['resumo']['consultou_sql'] is True
    assert trace['resumo']['consultou_vetorial'] is False
    assert event('retrieval_denso')['extra']['motivo'] == 'calculo_sql_suficiente'
    assert trace['latencia_metodo'] == 'requisicao_monotonica'
    assert trace['latencia_total_ms'] == events[-1]['latencia_ms']
    assert trace['latencia_pipeline_ms'] == event('pipeline_fim')['latencia_ms']
    assert trace['latencia_total_ms'] >= trace['latencia_pipeline_ms']
    assert api.historico.obter_consulta(body['query_id'])['resposta']['answer'] == body['answer']


def test_sql_failure_is_an_attempt_not_no_sql(local_pipeline, monkeypatch):
    def fail(*args, **kwargs):
        raise ConnectionError('SQL unavailable')
    monkeypatch.setattr(structured_query, 'executar_consulta', fail)
    response = local_pipeline.post('/api/v1/query', json={'query': 'Qual o valor total de contratos de ambulâncias?'})
    assert response.status_code == 200
    summary = event('resumo_consulta')['output']
    assert summary['consultou_sql'] is True
    assert summary['sql_retornou_evidencia'] is False
    assert summary['degradado'] is True
    assert summary['etapas']['consulta_sql'][0]['status'] == 'erro'
    assert event('pipeline_fim')['status'] == 'degradado'


def test_history_failure_does_not_break_answer_and_is_logged(local_pipeline, monkeypatch):
    def fail(self, **kwargs):
        raise OSError('cannot save')
    monkeypatch.setattr(HistoricoStore, 'registrar_consulta', fail)
    response = local_pipeline.post('/api/v1/query', json={'query': 'Quais contratos de ambulâncias?'})
    assert response.status_code == 200
    assert event('persistencia_historico')['status'] == 'erro'
    finish = event('requisicao_fim')
    assert finish['status'] == 'degradado'
    assert finish['extra']['history_saved'] is False
    assert finish['extra']['http_status'] == 200
    assert finish['output']['answer'] == response.json()['answer']


def test_unhandled_pipeline_exception_has_both_terminal_events(local_pipeline, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('SECRET_INTERNAL_DETAIL')
    monkeypatch.setattr(pipeline, 'analisar_query', fail)
    response = local_pipeline.post('/api/v1/query', json={'query': 'Quais contratos de ambulâncias?'})
    assert response.status_code == 500
    assert 'SECRET_INTERNAL_DETAIL' not in json.dumps(response.json())
    assert event('pipeline_fim')['status'] == 'erro'
    assert event('pipeline_fim')['extra']['error_type'] == 'RuntimeError'
    finish = event('requisicao_fim')
    assert finish['status'] == 'erro' and finish['extra']['http_status'] == 500
    assert finish['extra']['pipeline_error_type'] == 'RuntimeError'
    assert event('retrieval_denso')['extra']['motivo'] == 'falha_anterior'
    trace = montar_trace(finish['query_id'])
    assert trace['completo'] is True and trace['status'] == 'erro'
    assert not any(r['stage'] == 'persistencia_historico' for r in records())
    assert log_context.current_execution() is None
    assert obs.sessao_atual() is None


def test_validation_rejection_is_logged_without_unvalidated_input(local_pipeline):
    response = local_pipeline.post('/api/v1/query', json={'query': 'SECRET_TEXT' * 401})
    assert response.status_code == 422
    assert [r['stage'] for r in records()] == ['requisicao_inicio', 'requisicao_fim']
    assert event('requisicao_fim')['extra']['http_status'] == 422
    assert event('requisicao_fim')['status'] == 'rejeitado'
    assert 'SECRET_TEXT' not in json.dumps(records())


def test_greeting_records_skips_and_full_answer(local_pipeline):
    response = local_pipeline.post('/api/v1/query', json={'query': 'Olá!'})
    assert response.status_code == 200
    stages = event('pipeline_fim')['extra']['etapas']
    for stage in ['retrieval_denso', 'retrieval_bm25', 'fusao_rrf', 'rerank', 'consulta_sql', 'validacao']:
        assert stages[stage][0]['status'] == 'pulado'
        assert stages[stage][0]['attempted'] is False
        assert stages[stage][0]['motivo']
    assert event('geracao')['extra']['resposta']['answer'] == response.json()['answer']
    summary = event('resumo_consulta')['output']
    assert summary['consultou_sql'] is False and summary['consultou_vetorial'] is False


def test_timing_uses_envelope_not_nested_stage_sum(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(obs.time, 'perf_counter', lambda: clock[0])
    with log_context.execution('run', 'q_time'):
        with obs.lifecycle(run_id='run', query_id='q_time', query='question',
                           start=obs.Stage.PIPELINE_INICIO, end=obs.Stage.PIPELINE_FIM):
            for stage in [obs.Stage.GERACAO, obs.Stage.VALIDACAO]:
                obs.registrar(run_id='run', query_id='q_time', stage=stage, status='ok',
                              latencia_ms=200, input='question', output='ok')
            clock[0] += 0.3
    trace = montar_trace('q_time')
    assert trace['latencia_total_ms'] == 300
    assert trace['latencia_metodo'] == 'pipeline_monotonico'
    assert trace['completo'] is True


def test_start_without_finish_is_incomplete():
    obs.registrar(run_id='run', query_id='q_partial', stage=obs.Stage.PIPELINE_INICIO,
                  status='iniciado', latencia_ms=0, input='question', output=None)
    obs.registrar(run_id='run', query_id='q_partial', stage=obs.Stage.ROTEAMENTO,
                  status='ok', latencia_ms=2, input='question', output='ok')
    trace = montar_trace('q_partial')
    assert trace['completo'] is False
    assert trace['status'] == 'incompleto' and trace['latencia_metodo'] == 'parcial'


def test_pipeline_finish_without_api_finish_does_not_claim_request_success():
    for stage, latency in [(obs.Stage.REQUISICAO_INICIO, 0), (obs.Stage.PIPELINE_INICIO, 0),
                           (obs.Stage.PIPELINE_FIM, 100)]:
        obs.registrar(run_id='run', query_id='q_partial_api', stage=stage,
                      status='ok', latencia_ms=latency, input='question', output=None)
    trace = montar_trace('q_partial_api')
    assert trace['completo'] is False and trace['latencia_metodo'] == 'parcial'
    assert trace['latencia_pipeline_ms'] == 100


def test_feedback_does_not_change_query_latency_or_finish(local_pipeline):
    response = local_pipeline.post('/api/v1/query', json={'query': 'Olá!'}).json()
    before = montar_trace(response['query_id'])
    local_pipeline.post('/api/v1/feedback', json={'query_id': response['query_id'], 'avaliacao': 'positivo'})
    after = montar_trace(response['query_id'])
    assert after['latencia_total_ms'] == before['latencia_total_ms']
    assert after['fim'] == before['fim']
    assert after['status'] == before['status']
    assert after['etapas'][-1]['stage'] == 'feedback'
    assert montar_trace_sessao(response['session_id'])['fim'] == before['fim']


def test_legacy_trace_is_explicitly_estimated_and_unknown_completion():
    obs.registrar(run_id='run', query_id='q_legacy_timing', stage=obs.Stage.GERACAO,
                  status='ok', latencia_ms=20, input='question', output='ok')
    trace = montar_trace('q_legacy_timing')
    assert trace['latencia_total_ms'] == 20
    assert trace['latencia_metodo'] == 'soma_etapas_legado'
    assert trace['completo'] is None and trace['status'] is None


def test_extra_data_is_masked_recursively():
    obs.registrar(run_id='r', query_id='q_mask', stage=obs.Stage.CONSULTA_SQL,
                  status='ok', latencia_ms=0, input='question', output='ok',
                  extra={'parametros': ['CPF 123.456.789-09', 'person@gmail.com'],
                         'nested': {'telefone': '(21) 99999-8888'}})
    serialized = json.dumps(records())
    for secret in ['123.456.789-09', 'person@gmail.com', '99999-8888']:
        assert secret not in serialized
    assert 'omitido' in serialized


def test_runtime_context_has_safe_version_fingerprints(local_pipeline, monkeypatch):
    monkeypatch.setattr(config, 'RAG_API_KEY', 'SECRET-RAG-KEY')
    monkeypatch.setattr(config, 'OLLAMA_API_KEY', 'SECRET-LLM-KEY')
    monkeypatch.setattr(config, 'DATABASE_URL', 'postgres://user:SECRET-PASSWORD@private-host/db')
    context = log_context.runtime_context()
    serialized = json.dumps(context)
    assert 'SECRET' not in serialized and 'private-host' not in serialized
    assert len(context['code_sha256']) == 64
    assert len(context['index']['manifest_sha256']) == 64
    assert context['index']['source_sha256'] == 'test-corpus-hash'
    assert context['config']['analysis_mode'] == 'regras'
    assert context['config']['rerank_enabled'] is False


def test_index_fingerprint_changes_when_manifest_changes(local_pipeline):
    before = log_context.runtime_context()['index']['manifest_sha256']
    path = config.INDEX_DIR / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf8'))
    manifest['new_revision'] = 'updated'
    path.write_text(json.dumps(manifest), encoding='utf8')
    assert log_context.runtime_context()['index']['manifest_sha256'] != before


def test_code_fingerprint_changes_for_uncommitted_code(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT_DIR', tmp_path)
    path = tmp_path / 'config.py'
    path.write_text('old source', encoding='utf8')
    before = log_context.runtime_context()['code_sha256']
    path.write_text('new uncommitted source', encoding='utf8')
    after = log_context.runtime_context()['code_sha256']
    assert before != after


def test_execution_context_is_isolated_between_concurrent_requests():
    barrier = Barrier(2)
    def run(number):
        with log_context.execution(f'r{number}', f'q_{number}', 'diagnostico'), obs.sessao(f's_{number}'):
            with log_context.log_details(intencao=number):
                barrier.wait(timeout=5)
                obs.registrar(run_id=f'r{number}', query_id=f'q_{number}', stage=obs.Stage.ROTEAMENTO,
                              status='ok', latencia_ms=0, input=str(number), output=None)
        assert log_context.current_execution() is None and obs.sessao_atual() is None
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run, [1, 2]))
    for record in records():
        number = int(record['query_id'][-1])
        assert record['run_id'] == f'r{number}' and record['session_id'] == f's_{number}'
        assert record['extra']['intencao'] == number
        assert record['extra']['sequencia'] == 1


def test_nested_unrelated_context_restores_outer_state():
    with log_context.execution('outer', 'q_outer', 'api') as outer:
        with log_context.log_details(intencao=3):
            with log_context.execution('inner', 'q_inner', 'diagnostico'):
                assert log_context.current_details() == {}
            assert log_context.current_execution() is outer
            assert log_context.current_details() == {'intencao': 3}
    assert log_context.current_execution() is None and log_context.current_details() == {}


def test_metadata_failure_does_not_fail_a_valid_query(monkeypatch):
    def fail():
        raise RuntimeError('metadata unavailable')
    monkeypatch.setattr(log_context, 'runtime_context', fail)
    assert pipeline.responder('Olá!')['is_refusal'] is False
    assert event('pipeline_inicio')['extra']['contexto_execucao'] == {
        'unavailable': True, 'error_type': 'RuntimeError'}
    assert event('pipeline_fim')['status'] == 'ok'


def test_invalid_pipeline_response_has_a_terminal_api_error(local_pipeline, monkeypatch):
    monkeypatch.setattr(api.pipeline, 'responder', lambda *a, **kw: {'answer': None})
    client = TestClient(api.app, raise_server_exceptions=False)
    response = client.post('/api/v1/query', json={'query': 'Quais contratos?'})
    assert response.status_code == 500
    assert event('requisicao_fim')['extra']['error_type'] == 'ValidationError'
    assert event('requisicao_fim')['extra']['http_status'] == 500
    assert not any(r['stage'] == 'persistencia_historico' for r in records())


def test_masking_happens_before_truncation_at_a_personal_identifier():
    text = 'x' * 7990 + ' CPF 123.456.789-09'
    assert '123' not in obs.bounded_log_value(text)
    assert 'CPF' not in obs.bounded_log_value(text, limit=7992)


def test_evaluation_origin_is_propagated_into_pipeline_events():
    with log_context.log_origin('avaliacao'):
        pipeline.responder('Olá!')
    assert {r['extra']['origem'] for r in records()} == {'avaliacao'}
    assert log_context.current_origin() == 'direto'


def test_logging_disk_failure_does_not_fail_a_valid_query(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(Path, 'open', fail)
    with log_context.execution('r', 'q_disk') as context:
        response = pipeline.responder('Olá!', run_id='r', query_id='q_disk')
        assert response['is_refusal'] is False
        assert context.logging_errors > 0


def test_sql_observation_keeps_first_and_last_attempts_and_failed_parameters(tmp_path):
    banco = structured_db.Banco(sqlite_path=tmp_path / 'empty.sqlite')
    import sqlite3
    with sqlite3.connect(banco.sqlite_path):
        pass
    with structured_db.observar_consultas() as trace:
        for number in range(25):
            banco.consultar('SELECT ? AS n', [number])
        with pytest.raises(sqlite3.OperationalError):
            banco.consultar('SELECT * FROM missing WHERE name = ?', ['CPF 123.456.789-09'])
    assert trace['total'] == 26 and trace['fora_do_log'] == 6
    assert len(trace['consultas']) == 20
    assert trace['consultas'][0]['parametros'] == [0]
    assert trace['consultas'][0]['linhas_retornadas'] == 1
    assert trace['consultas'][-1]['status'] == 'erro'
    assert trace['consultas'][-1]['parametros'] == ['CPF [CPF omitido]']
    assert structured_db._sql_observation.get() is None


def test_intentions_are_correlated_in_each_retrieval(local_pipeline, monkeypatch):
    intents = [QueryIntent(pergunta=f'Contratos de ambulâncias {number}', bases=[Natureza.ESTRUTURADA])
               for number in range(2)]
    monkeypatch.setattr(pipeline, 'analisar_query', lambda q, **kw: QueryAnalysisResult(
        query_original=q, filtros=QueryFilters(), intencoes=intents, metodo='llm'))
    local_pipeline.post('/api/v1/query', json={'query': 'Quais contratos de ambulâncias?'})
    dense = [r for r in records() if r['stage'] == 'retrieval_denso']
    assert [r['extra']['intencao'] for r in dense] == [0, 1]
    assert all(r['extra']['tentativa_busca'] == 1 for r in dense)
    assert 'intencao' not in event('geracao')['extra']


def test_provider_fallback_is_explicitly_degraded():
    with obs.default_logger.span(run_id='r', query_id='q_fallback', stage=obs.Stage.INTERACAO,
                                 input='question', extra={'fallback_used': True}) as span:
        span.set_result({'tipo_interacao': 'consulta'})
    assert event('interacao')['status'] == 'degradado'


def test_dense_unavailable_is_degraded_and_bm25_still_answers(local_pipeline, monkeypatch):
    from src import retrieve as retrieve_module
    def fail(*args, **kwargs):
        raise ConnectionError('embedding unavailable')
    monkeypatch.setattr(retrieve_module, 'dense_retrieve', fail)
    response = local_pipeline.post('/api/v1/query', json={'query': 'Quais contratos de ambulâncias?'})
    assert response.status_code == 200
    dense = event('retrieval_denso')
    assert dense['status'] == 'degradado' and dense['extra']['fallback'] == 'bm25'
    assert event('retrieval_bm25')['status'] == 'ok'
    assert event('pipeline_fim')['status'] == 'degradado'


def test_rerank_failure_records_fallback_and_retains_rrf_evidence(local_pipeline, monkeypatch):
    def fail(query, items):
        raise RuntimeError('reranker unavailable')
    monkeypatch.setattr(pipeline, 'reranker_padrao', lambda: fail)
    response = local_pipeline.post('/api/v1/query', json={'query': 'Quais contratos de ambulâncias?'})
    assert response.status_code == 200
    rerank = event('rerank')
    assert rerank['status'] == 'erro' and rerank['extra']['fallback_on_error'] == 'fusao_rrf'
    assert 'c1' in event('geracao')['extra']['prompt_chunk_ids']
    assert event('fusao_rrf')['extra']['evidencias'][0]['chunk_id'] == 'c1'
    assert event('pipeline_fim')['status'] == 'degradado'


def test_reported_token_totals_are_not_duplicated_by_envelopes():
    with log_context.execution('run', 'q_tokens'):
        with obs.lifecycle(run_id='run', query_id='q_tokens', query='question',
                           start=obs.Stage.REQUISICAO_INICIO, end=obs.Stage.REQUISICAO_FIM):
            with obs.lifecycle(run_id='run', query_id='q_tokens', query='question',
                               start=obs.Stage.PIPELINE_INICIO, end=obs.Stage.PIPELINE_FIM):
                with obs.default_logger.span(run_id='run', query_id='q_tokens',
                                             stage=obs.Stage.GERACAO, input='question') as span:
                    span.set_result('answer', response={'usage': {
                        'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
    for stage in ['pipeline_fim', 'requisicao_fim']:
        assert event(stage)['extra']['tokens_reportados'] == {'prompt': 10, 'completion': 5, 'total': 15}
    assert montar_trace('q_tokens')['resumo']['tokens_reportados']['total'] == 15


def test_final_output_truncation_is_explicit_and_does_not_change_response():
    answer = 'x' * 9000
    with log_context.execution('run', 'q_long'):
        with obs.lifecycle(run_id='run', query_id='q_long', query='question',
                           start=obs.Stage.PIPELINE_INICIO, end=obs.Stage.PIPELINE_FIM) as flow:
            flow.output = {'answer': answer}
    finish = event('pipeline_fim')
    assert finish['extra']['output_truncated'] is True
    assert finish['extra']['log_text_limit'] == 8000
    assert len(finish['output']['answer']) == 8001
    assert len(flow.output['answer']) == 9000


def test_trace_orders_timestamps_by_instant_not_timezone_strings(tmp_path):
    path = obs.default_logger.logs_dir / 'pipeline-2026-10-07.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [{'ts': '2026-10-07T09:00:00+00:00', 'query_id': 'q_zone', 'run_id': 'run',
             'stage': 'geracao', 'status': 'ok', 'input': 'question', 'latencia_ms': 10},
            {'ts': '2026-10-07T06:00:00-05:00', 'query_id': 'q_zone', 'run_id': 'run',
             'stage': 'validacao', 'status': 'ok', 'input': 'question', 'latencia_ms': 1}]
    path.write_text('\n'.join(json.dumps(r) for r in data) + '\n', encoding='utf8')
    assert [e['stage'] for e in montar_trace('q_zone')['etapas']] == ['geracao', 'validacao']


def test_followup_original_and_rewritten_questions_are_in_final_snapshot(local_pipeline, monkeypatch):
    from src.reformulation import Reformulacao
    monkeypatch.setattr(pipeline, 'reformular_pergunta', lambda *a, **kw: Reformulacao(
        'Quais contratos de ambulâncias?', True, 'teste'))
    result = local_pipeline.post('/api/v1/query', json={'query': 'E os mesmos?',
        'historico': [{'pergunta': 'Quais contratos?', 'resposta': 'Ambulâncias.'}]}).json()
    assert event('pipeline_fim')['input'] == 'E os mesmos?'
    assert event('pipeline_fim')['output']['query'] == 'E os mesmos?'
    assert event('pipeline_fim')['output']['pergunta_reformulada'] == 'Quais contratos de ambulâncias?'
    assert event('requisicao_fim')['output']['pergunta_reformulada'] == result['pergunta_reformulada']
