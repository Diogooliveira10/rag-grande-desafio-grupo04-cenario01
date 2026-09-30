"""Avaliação ECOA explicitamente pela CLI existente, com retomada por caso."""
import json,sys,os,re,subprocess
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
from eval.adapters import cli_resposta
from eval.run_eval import load_golden_set,run_eval,save_run
cases=load_golden_set(ROOT/'eval/runs/cases-v5.jsonl')
output=ROOT/'eval/runs/cli-v5';output.mkdir(exist_ok=True)
responses_path=output/'responses.jsonl'
responses={}
if responses_path.exists():
    for line in responses_path.open(encoding='utf8'):
        row=json.loads(line);responses[row['case_id']]=row
sources={}
for line in (ROOT/'data/normalized/v5/documents.jsonl').open(encoding='utf8'):
    row=json.loads(line);sources.setdefault(row['chunk_id'],set()).add(row['source_file'])
for case in cases:
    if case.id in responses:continue
    try:
        response=cli_resposta(case.pergunta)
    except subprocess.CalledProcessError as exc:
        failure={'case_id':case.id,'pergunta':case.pergunta,'command':exc.cmd,'exit_code':exc.returncode,'stdout':exc.stdout,'stderr':exc.stderr}
        (output/'failure.json').write_text(json.dumps(failure,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps({'status':'failed','case_id':case.id,'diagnostic':str(output/'failure.json')}),flush=True)
        raise
    source_check=[]
    for source in response.get('sources_used',[]):
        identifier=source['chunk_id'];path=source['source_file']
        source_check.append({'chunk_id':identifier,'source_file':path,'matches_normalized_source':path in sources.get(identifier,set()),'file_exists':(ROOT/path.split('#')[0]).is_file()})
    row={'case_id':case.id,'pergunta':case.pergunta,'response':response,'source_checks':source_check}
    responses[case.id]=row
    with responses_path.open('a',encoding='utf8') as handle:handle.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps({'case_id':case.id,'bases':response.get('bases_consultadas'),'refusal':response.get('is_refusal'),'confidence':response.get('confidence_level'),'sources':len(source_check),'latencias_cli_ms':response.get('latencias_cli_ms')}),flush=True)
by_question={case.pergunta:responses[case.id]['response'] for case in cases}
report=run_eval(cases,lambda question:by_question[question])
report['scope']='rota/recusa; correção factual exige revisão das respostas e fontes em responses.jsonl'
save_run(report,output,'cli-v5',datetime.now(timezone.utc).isoformat())
print(json.dumps({'status':'ok','cases':report['total'],'route_refusal_accuracy':report['taxa_acerto']}),flush=True)
