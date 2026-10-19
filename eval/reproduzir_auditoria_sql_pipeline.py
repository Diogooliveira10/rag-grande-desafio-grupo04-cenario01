"""Reprodução isolada de análise/SQL/pipeline, sem provedores externos.

Bancos existentes são abertos por Banco somente leitura. Uma reconstrução de
controle opcional é criada SOMENTE no diretório novo de saída; não troca o
banco ativo nem os índices. Recuperação e LLM são bloqueados no teste do
pipeline: uma tentativa vira um resultado de auditoria, não tráfego de rede.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config
from src import structured_db as db, structured_query as sql, query_analysis as qa, pipeline
from src.generate import pode_responder_sql
from src.obs import default_logger
from src.router import _rotear
from src.schema import Natureza, QueryFilters, QueryIntent
from src.sensitivity import mascarar


class RetrievalBlockedForAudit(RuntimeError):
    pass


def unavailable(*args, **kwargs):
    raise RetrievalBlockedForAudit('Audit blocks retrieval and model calls')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saida', type=Path, required=True)
    args = parser.parse_args()
    if args.saida.exists():
        raise SystemExit('Destino já existe: ' + str(args.saida))
    args.saida.mkdir(parents=True)
    default_logger.logs_dir = args.saida / 'isolated_logs'
    questions = {
        'R15': 'Quanto a Secretaria de Estado de Saúde do RJ planejou gastar no plano de contratações de 2025?',
        'R19': 'Qual a mediana do preço unitário homologado de serviço de engenharia no município do Rio?',
        'R15_pca_alias': 'Quanto a Secretaria de Estado de Saúde planejou gastar no PCA de 2025?',
        'R19_location_full': 'Qual a mediana do preço unitário homologado de serviço de engenharia no município do Rio de Janeiro?',
        'R19_prefeitura': 'Qual a mediana do preço unitário homologado de serviço de engenharia contratado pela Prefeitura do Rio de Janeiro?',
        'R19_explicit_unit': 'Qual a mediana do preço homologado por unidade de serviço de engenharia no Rio?',
        'simple_total': 'Qual o valor total dos contratos assinados em 2025 no corpus?',
    }
    snapshots = {
        'active_existing': (ROOT/'data/structured/base_estruturada.sqlite', ROOT/'data/normalized/documents.jsonl'),
        'candidate_07': (ROOT/'data/v7_recuperacao/2026-10-15/candidato_07_reparo/base_estruturada.sqlite',
                         ROOT/'data/v7_recuperacao/2026-10-15/candidato_07_reparo/documents.jsonl'),
    }
    # Control artifact: the supported administrative builder, in a new path only.
    control = args.saida/'v6_schema7_control.sqlite'
    build = db.construir_banco(snapshots['active_existing'][1], db.Banco(sqlite_path=control))
    snapshots['v6_schema7_control'] = (control, snapshots['active_existing'][1])
    report = {'runtime': {'analysis_mode': qa.ANALYSIS_MODE, 'sql_dialect': db.banco_padrao().dialeto,
                         'expected_schema': db.SCHEMA_VERSION, 'default_index': str(config.INDEX_DIR),
                         'default_documents': str(db.DOCUMENTS_FILE)}, 'control_build': build,
              'validation': {}, 'cases': {}, 'pipeline_probes': [], 'typed_plan_probes': []}
    for name, (path, docs) in snapshots.items():
        bank = db.Banco(sqlite_path=path)
        try:
            db.validar_banco(docs, bank)
            report['validation'][name] = {'ok': True}
        except Exception as exc:
            report['validation'][name] = {'ok': False, 'error_type': type(exc).__name__, 'error': str(exc)}
    for label, query in questions.items():
        filters = qa._analisar(query)
        route = _rotear(query)
        planned = sql.detectar_consulta(query, filters, route.bases_selecionadas)
        row = {'query': query, 'filters': filters.model_dump(mode='json'),
               'retrieval_filters': qa.filtros_para_retrieve(filters),
               'bases': [b.value for b in route.bases_selecionadas],
               'sql_plan': asdict(planned) if planned else None,
               'sql_fast_path': sql.pode_consultar_sql_direto(query, filters, route.bases_selecionadas),
               'executions': {}}
        for name, (path, docs) in snapshots.items():
            chunks = sql.consultar_estruturado(query, filters, route.bases_selecionadas,
                       banco=db.Banco(sqlite_path=path), documents_file=docs,
                       run_id='audit-sql-'+name, query_id='audit-'+label+'-'+name)
            row['executions'][name] = {
                'chunks': len(chunks), 'deterministic_sufficient': pode_responder_sql(chunks, route.bases_selecionadas),
                'contents': [c.content for c in chunks],
                'recortes': [c.metadata.extras.get('resultado_sql',{}).get('recorte') for c in chunks],
            }
        report['cases'][label] = row
    # Typed mode has its own dispatch behavior; unit prices / result dates reject SQL.
    for label, filters in [('unitario', QueryFilters(metrica='mediana', tipo_valor='unitario', referencia_temporal='resultado')),
                           ('pca', QueryFilters(ano=2025, referencia_temporal='pca', metrica='soma', orgao='Secretaria de Estado de Saúde'))]:
        intent = QueryIntent(pergunta='Mediana ou soma solicitada', bases=[Natureza.AGREGADA],
                             operacao='estatistica', filtros=filters)
        consulta = sql.consulta_da_intencao(intent)
        report['typed_plan_probes'].append({'kind': label, 'consulta': asdict(consulta) if consulta else None})
    # Run actual orchestration and actual SQL, while trapping any fallback before network.
    original_analysis = qa.analisar_query
    original_sql = sql.consultar_estruturado
    for name, (path, docs) in snapshots.items():
        for label in ('R15', 'R19', 'simple_total'):
            def local_sql(query, filters, bases, **kw):
                return original_sql(query, filters, bases, banco=db.Banco(sqlite_path=path), documents_file=docs, **kw)
            def rules(query, **kw):
                return original_analysis(query, modo='regras', **kw)
            item = {'snapshot': name, 'case': label}
            try:
                with patch.object(pipeline, 'consultar_estruturado', local_sql), \
                     patch.object(pipeline, 'analisar_query', rules), \
                     patch.object(pipeline, 'retrieve', unavailable):
                    answer = pipeline.responder(questions[label], cliente=unavailable, reranker=unavailable,
                               run_id='audit-pipeline-'+name, query_id='audit-pipeline-'+label+'-'+name)
                item.update({'outcome': 'completed_without_models', 'response': answer})
            except RetrievalBlockedForAudit:
                item.update({'outcome': 'would_fallback_to_retrieval', 'response': None})
            except Exception as exc:
                item.update({'outcome': 'unexpected_error', 'error_type': type(exc).__name__, 'error': str(exc)})
            report['pipeline_probes'].append(item)
    text, _ = mascarar(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    (args.saida/'reproduction.json').write_text(text+'\n', encoding='utf8')
    print('VALIDATION', report['validation'])
    for label in ('R15', 'R19', 'R15_pca_alias', 'R19_explicit_unit'):
        row=report['cases'][label]
        print(label, 'filter=', row['filters'], 'plan=', row['sql_plan'],
              'chunks=', {k:v['chunks'] for k,v in row['executions'].items()})
    print('PIPELINE', [{k:v for k,v in p.items() if k!='response'} for p in report['pipeline_probes']])


if __name__ == '__main__':
    main()
