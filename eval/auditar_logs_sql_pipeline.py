"""Auditoria somente leitura de TODOS os JSONLs canônicos do pipeline.

Não usa o índice derivado de traces, não apaga logs, não chama modelos e não
confunde eventos sem IDs únicos (benchmarks) com execuções ponta a ponta.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.sensitivity import mascarar


def norm(text):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', str(text).casefold())
                            if not unicodedata.combining(c)).split())


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * p) - 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logs', type=Path, default=ROOT / 'logs/pipeline')
    parser.add_argument('--saida', type=Path, required=True)
    args = parser.parse_args()
    if args.saida.exists():
        raise SystemExit('Destino já existe: ' + str(args.saida))
    args.saida.mkdir(parents=True)
    files = []
    total = Counter()
    statuses = Counter()
    origins = Counter()
    outcomes = Counter()
    errors = Counter()
    fallbacks = Counter()
    skip_reasons = Counter()
    stage_stats = defaultdict(Counter)
    durations = defaultdict(list)
    groups = {}
    focal_counts = Counter()
    focal_stage = defaultdict(Counter)
    focal_queries = {'R15': 'quanto a secretaria de estado de saude do rj planejou gastar no plano de contratacoes de 2025',
                     'R19': 'qual a mediana do preco unitario homologado de servico de engenharia no municipio do rio'}
    seen_pairs = Counter()
    with (args.saida / 'focal_events.jsonl').open('w', encoding='utf8') as focused:
        for path in sorted(args.logs.glob('*.jsonl')):
            counts = Counter()
            digest = hashlib.sha256()
            first = last = None
            with path.open('rb') as handle:
                for line_number, raw in enumerate(handle, 1):
                    digest.update(raw)
                    counts['lines'] += 1
                    if not raw.strip():
                        counts['blank'] += 1
                        continue
                    try:
                        event = json.loads(raw)
                        if not isinstance(event, dict):
                            raise ValueError('non-object')
                    except (ValueError, UnicodeDecodeError):
                        counts['invalid_json'] += 1
                        continue
                    counts['events'] += 1
                    stage = str(event.get('stage', 'unknown'))
                    status = str(event.get('status', 'unknown'))
                    extra = event.get('extra') if isinstance(event.get('extra'), dict) else {}
                    origin = str(extra.get('origem', 'legacy_unspecified'))
                    outcome = str(extra.get('outcome', status))
                    timestamp = event.get('ts')
                    first = first or timestamp
                    last = timestamp or last
                    statuses[(stage, status)] += 1
                    origins[origin] += 1
                    outcomes[outcome] += 1
                    stage_stats[stage]['events'] += 1
                    if status == 'pulado' or extra.get('attempted') is False:
                        stage_stats[stage]['skipped'] += 1
                    elif isinstance(event.get('latencia_ms'), (float, int)):
                        durations[stage].append(float(event['latencia_ms']))
                    if outcome in {'erro', 'degradado', 'erro_llm'}:
                        stage_stats[stage][outcome] += 1
                    if extra.get('auditoria_evidencias'):
                        stage_stats[stage]['full_evidence_events'] += 1
                    for field in ('prompt_truncated', 'answer_truncated', 'output_truncated'):
                        if extra.get(field):
                            stage_stats[stage][field] += 1
                    if extra.get('evidencias_fora_do_log'):
                        stage_stats[stage]['evidence_omitted_events'] += 1
                    if extra.get('error_type'):
                        errors[(stage, str(extra['error_type']))] += 1
                    if extra.get('fallback'):
                        fallbacks[(stage, str(extra['fallback'])[:180])] += 1
                    if extra.get('motivo'):
                        skip_reasons[(stage, str(extra['motivo']))] += 1
                    sql_trace = extra.get('execucao_sql')
                    if isinstance(sql_trace, dict):
                        stage_stats[stage]['sql_statements'] += sql_trace.get('total', 0)
                        stage_stats[stage]['sql_statements_omitted'] += sql_trace.get('fora_do_log', 0)
                    run = event.get('run_id')
                    query = event.get('query_id')
                    usable_ids = bool(run and query and run != 'sem-run-id' and query != 'sem-query-id')
                    if not usable_ids:
                        counts['uncorrelated_events'] += 1
                    else:
                        key = (str(run), str(query))
                        seen_pairs[key] += 1
                        group = groups.setdefault(key, {'stages': Counter(), 'origins': Counter(),
                            'first': timestamp, 'last': timestamp, 'start': 0, 'end': 0,
                            'sql_attempted': False, 'dense_attempted': False,
                            'terminal_status': None, 'injected': None, 'kind': None, 'terminal_latency_ms': None})
                        group['stages'][stage] += 1
                        group['origins'][origin] += 1
                        group['last'] = timestamp
                        if stage == 'pipeline_inicio':
                            group['start'] += 1
                            group['injected'] = extra.get('cliente_injetado')
                            group['kind'] = extra.get('tipo_pipeline', 'query_or_unspecified')
                        elif stage == 'pipeline_fim':
                            group['end'] += 1
                            group['terminal_status'] = status
                            group['terminal_latency_ms'] = event.get('latencia_ms')
                        if stage == 'consulta_sql' and extra.get('attempted') is not False and status != 'pulado':
                            group['sql_attempted'] = True
                        if stage == 'retrieval_denso' and extra.get('attempted') is not False and status != 'pulado':
                            group['dense_attempted'] = True
                    input_value = event.get('input', '')
                    normalized = norm(input_value) if isinstance(input_value, str) else ''
                    for label, needle in focal_queries.items():
                        if needle not in normalized:
                            continue
                        focal_counts[label] += 1
                        focal_stage[label][stage] += 1
                        selected = {'case': label, 'log_file': str(path.relative_to(ROOT)),
                                    'line': line_number, 'correlatable_ids': usable_ids, 'event': event}
                        masked, _ = mascarar(json.dumps(selected, ensure_ascii=False))
                        focused.write(masked + '\n')
            total.update(counts)
            files.append({'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
                          'sha256': digest.hexdigest(), **counts, 'first_ts': first, 'last_ts': last})
    group_summary = Counter()
    serialized_groups = []
    for (run, query), group in groups.items():
        if group['start'] and group['end']:
            kind = 'closed_envelope'
        elif group['start']:
            kind = 'start_without_end'
        elif group['end']:
            kind = 'end_without_start'
        else:
            kind = 'module_events_only'
        group_summary[kind] += 1
        if group['start'] > 1 or group['end'] > 1:
            group_summary['reused_envelope_ids'] += 1
        serialized_groups.append({'run_id': run, 'query_id': query, 'correlation': kind,
                                  **group, 'stages': dict(group['stages']), 'origins': dict(group['origins'])})
    def pairs(counter):
        return [{'stage': k[0], 'value': k[1], 'count': v} for k, v in counter.most_common()]
    report = {'scope': 'all canonical logs/pipeline/*.jsonl, every nonblank line; no source mutation',
        'files': files, 'totals': dict(total), 'declared_origins': dict(origins), 'outcomes': dict(outcomes),
        'stages': {key: {**stats, 'latency_samples': len(durations[key]),
            'p50_ms': percentile(durations[key], .5), 'p95_ms': percentile(durations[key], .95),
            'max_ms': max(durations[key]) if durations[key] else None} for key, stats in stage_stats.items()},
        'statuses': pairs(statuses), 'error_types': pairs(errors), 'fallbacks': pairs(fallbacks),
        'reasons': pairs(skip_reasons), 'correlation': dict(group_summary),
        'focal_counts': dict(focal_counts), 'focal_stages': {k: dict(v) for k, v in focal_stage.items()},
        'limitations': ['Historical versions and injected clients are mixed; counts are not current-production error rates.',
                       'Default IDs cannot correlate independent benchmark calls into a pipeline.',
                       'Fields truncated/omitted by the original logger cannot be recovered by reading full files.',
                       'Declared origin is not proof of an organic user request.']}
    (args.saida / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    (args.saida / 'correlated_groups.json').write_text(json.dumps(serialized_groups, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'files': len(files), 'MB': round(sum(f['bytes'] for f in files) / 1024**2, 2),
        'events': total['events'], 'invalid_json': total['invalid_json'], 'uncorrelated': total['uncorrelated_events'],
        'groups': dict(group_summary), 'origins': dict(origins), 'focal_events': dict(focal_counts)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
