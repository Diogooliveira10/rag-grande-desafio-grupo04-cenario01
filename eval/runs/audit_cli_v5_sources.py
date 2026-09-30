"""Reúne evidência de fontes e métricas; o julgamento factual é revisão humana."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.sensitivity import mascarar

output = ROOT / 'eval/runs/cli-v5'
responses = [json.loads(line) for line in (output / 'responses.jsonl').open(encoding='utf8')]
assert len(responses) == 22 and len({row['case_id'] for row in responses}) == 22
identifiers = {source['chunk_id'] for row in responses for source in row['response']['sources_used']}
chunks = {}
for line in (ROOT / 'data/normalized/v5/documents.jsonl').open(encoding='utf8'):
    chunk = json.loads(line)
    if chunk['chunk_id'] in identifiers:
        assert hashlib.sha256(chunk['text'].encode('utf8')).hexdigest() == chunk['chunk_content_sha256']
        chunks[chunk['chunk_id']] = chunk
inventory = json.loads((ROOT / 'data/inventory/v5.json').read_text(encoding='utf8'))
hashes = {row['source_file']: row['sha256'] for row in inventory['arquivos'] if row['status'] == 'ok'}
verified = {}
for chunk in chunks.values():
    source = chunk['source_file']
    if source in verified:
        continue
    expected = hashes[source]
    digest = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    assert digest == expected
    verified[source] = {'sha256': digest, 'inventory_matches': True}
cases = []
for row in responses:
    response = row['response']
    events = response['eventos']
    cited = []
    for source in response['sources_used']:
        chunk = chunks.get(source['chunk_id'])
        if chunk is not None:
            text, masking = mascarar(chunk['text'])
            cited.append({**source, 'text_masked': text, 'masking': masking,
                          'inventory_version': chunk['inventory_version'],
                          'chunk_content_sha256': chunk['chunk_content_sha256'],
                          'raw_file_validation': verified[chunk['source_file']]})
        else:
            sql_events = [event for event in events if event['stage'] == 'consulta_sql']
            cited.append({**source, 'derived_sql': sql_events,
                          'exists': (ROOT / source['source_file']).is_file()})
    embedding_events = [event for event in events if event['stage'] == 'retrieval_denso']
    retrieval = [event['extra'] for event in events if event['stage'] == 'fusao_rrf']
    cases.append({'case_id': row['case_id'], 'question': row['pergunta'], 'answer': response['answer'],
                  'route': response['rota_cli'], 'confidence': response['confidence_level'],
                  'refusal': response['is_refusal'], 'refusal_reason': response['refusal_reason'],
                  'latencies_cli_ms': response['latencias_cli_ms'], 'retrieval': retrieval,
                  'tokens': {key: sum(event.get('tokens', {}).get(key, 0) for event in events)
                             for key in ['prompt', 'completion', 'total']},
                  'embedding_calls': sum(event['extra'].get('query_embedding', {}).get('calls', 0)
                                         for event in embedding_events),
                  'embedding_cache_hits': sum(event['extra'].get('query_embedding', {}).get('cache_hits', 0)
                                              for event in embedding_events),
                  'generation_calls': sum(event['stage'] == 'geracao' and bool(event.get('modelo'))
                                          for event in events),
                  'sensitivity_events': [event['extra'] for event in events if event['stage'] == 'geracao'],
                  'cited_evidence': cited})
report = {'scope': 'Fontes verificadas contra inventário e chunks; não prova automática de correção factual.',
          'case_count': len(cases), 'verified_raw_sources': verified, 'cases': cases}
(output / 'source-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
print(json.dumps({'status': 'ok', 'cases': len(cases), 'verified_raw_files': len(verified),
                  'cited_chunks': len(chunks)}, ensure_ascii=False))
