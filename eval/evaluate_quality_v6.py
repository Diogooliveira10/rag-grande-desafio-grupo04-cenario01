"""Avaliação explícita da qualidade pela CLI, com retomada e chamadas sequenciais."""
import os,sys,json,time,contextlib,hashlib,argparse
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT);sys.path.insert(0,str(ROOT))
os.environ.setdefault("QUERY_ANALYSIS_MODE", "regras")
os.environ["LLM_PROVIDER_MODE"] = "openrouter"
from eval.adapters import cli_resposta
from eval.run_eval import load_golden_set,run_eval,save_run
import config
parser = argparse.ArgumentParser(description='Avaliação CLI OpenRouter Free, com retomada e corpus congelado.')
parser.add_argument('--case', action='append', dest='case_ids', help='Reavaliar somente este ID; repetível.')
parser.add_argument('--output', default='quality-v6', help='Nome do diretório em eval/runs.')
args = parser.parse_args()
if Path(args.output).name != args.output or args.output in {'.', '..'}:
 parser.error('--output deve ser um nome de diretório simples')
OUT=ROOT/'eval/runs'/args.output;OUT.mkdir(exist_ok=True)
cases=load_golden_set(ROOT/'eval/runs/cases-v5.jsonl')
if args.case_ids:
 unknown = set(args.case_ids) - {case.id for case in cases}
 if unknown:parser.error('IDs desconhecidos: ' + ', '.join(sorted(unknown)))
 cases = [case for case in cases if case.id in args.case_ids]
priority=['P2.1','P3.2','P4.3','P5.3','P5.2','cenario1-claudia','P3.1','P6.2']
cases.sort(key=lambda c:priority.index(c.id) if c.id in priority else len(priority))
path=OUT/'responses.jsonl'
runtime = {'generation_model': config.GENERATION_MODEL, 'max_output_tokens': config.OPENROUTER_MAX_TOKENS,
           'analysis_mode': os.environ['QUERY_ANALYSIS_MODE'],
           'embedding_model': config.EMBEDDING_MODEL, 'llm_provider_mode': 'openrouter'}
fingerprint = hashlib.sha256(b''.join((ROOT / name).read_bytes() for name in
    ['src/query_analysis.py', 'src/schema.py', 'src/router.py', 'src/pipeline.py',
     'src/retrieve.py', 'src/structured_query.py', 'src/generate.py', 'llm_ecoa.py', 'config.py'])
    + json.dumps(runtime, sort_keys=True).encode()).hexdigest()
manifest_path = OUT / 'execution-manifest.json'
if path.exists() and (not manifest_path.exists() or
        json.loads(manifest_path.read_text(encoding='utf8')).get('code_sha256') != fingerprint):
 raise RuntimeError('Código mudou ou manifesto ausente. Preserve as respostas anteriores em outro nome antes de iniciar uma avaliação nova.')
manifest_path.write_text(json.dumps({'code_sha256': fingerprint, 'analysis_mode': os.environ['QUERY_ANALYSIS_MODE'],
    'llm_provider_mode': 'openrouter', 'cases': len(cases),
    'runtime': runtime,
    'scope': 'Integração OpenRouter Free explícita; embeddings ECOA/cache; corpus v6 congelado'}, indent=2), encoding='utf8')
rows={r['case_id']:r for r in [json.loads(l) for l in path.read_text(encoding='utf8').splitlines()]} if path.exists() else {}
for case in cases:
 if case.id in rows:continue
 started=time.monotonic()
 response=cli_resposta(case.pergunta, openrouter_only=True)
 row={'case_id':case.id,'pergunta':case.pergunta,'response':response}
 with path.open('a',encoding='utf8') as handle:handle.write(json.dumps(row,ensure_ascii=False)+'\n')
 rows[case.id]=row
 status={'case_id':case.id,'completed':len(rows),'total':len(cases),'answer':response.get('answer'),'refusal':response.get('is_refusal'),'bases':response.get('bases_consultadas'),'elapsed_seconds':round(time.monotonic()-started,2)}
 (OUT/'progress.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(status,ensure_ascii=False),flush=True)
 time.sleep(10)
by_question={r['pergunta']:r['response'] for r in rows.values()}
report=run_eval(cases,lambda question:by_question[question]);report['scope']='Rota/recusa do gabarito original; avaliação factual e de cobertura separada.'
save_run(report,OUT,args.output,datetime.now(timezone.utc).isoformat())
original=ROOT/'eval/runs/audit_cli_v5_sources.py'
script=original.read_text(encoding='utf8').replace('eval/runs/cli-v5', OUT.relative_to(ROOT).as_posix()).replace('/v5/','/v6/').replace('/v5.json','/v6.json')
script=script.replace("assert len(responses) == 22 and len({row['case_id'] for row in responses}) == 22",
    f"assert len(responses) == {len(cases)} and len({{row['case_id'] for row in responses}}) == {len(cases)}")
script=script.replace('expected = hashes[source]',"expected = hashes.get(source)\n    if '#derivado:' in source:\n        source_path = source.split('#', 1)[0]\n        digest = hashlib.sha256((ROOT / source_path).read_bytes()).hexdigest()\n        assert digest == chunk['inventory_hash']\n        verified[source] = {'sha256': digest, 'inventory_matches': True, 'derived_indicator': True}\n        continue")
exec(compile(script,str(original),'exec'),{'__file__':str(original),'__name__':'__main__'})
print(json.dumps({'completed':report['total'],'joint_accuracy':report['taxa_acerto']}),flush=True)
