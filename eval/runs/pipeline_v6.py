"""Run the existing preparation, local embeddings, persisted index and CLI evaluation."""
from __future__ import annotations
import os,sys,json,time,subprocess,shutil,hashlib,math
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
from src.index import build_index,stored_embed_fn,file_sha256
BASES=['contratos_estruturado','editais_normativo','atas_conversacional','pca_agregado']
RUN=ROOT/'eval/runs/v6-pipeline';RUN.mkdir(exist_ok=True)
STATE=RUN/'state.json'
def state(stage,**extra):STATE.write_text(json.dumps({'stage':stage,**extra},ensure_ascii=False,indent=2),encoding='utf-8')
def command(args,name):
    with (RUN/(name+'.log')).open('w',encoding='utf-8') as output:
        result=subprocess.run([sys.executable,*args],cwd=ROOT,stdout=output,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError(name+' failed; read '+str((RUN/(name+'.log')).relative_to(ROOT)))
def main():
    while not (ROOT/'eval/runs/v6-curation/report.json').exists():time.sleep(15)
    compatibility=json.loads((ROOT/'eval/runs/embedding-compatibility-v6.json').read_text(encoding='utf-8'))
    if not compatibility.get('compatible'):raise RuntimeError('Local/ECOA compatibility check must pass first')
    run_path=RUN/'embedding-run.json'
    if run_path.exists():run=json.loads(run_path.read_text(encoding='utf-8'))
    else:
        run={'corpus_version':'v6','folder':'data/embeddings/'+datetime.now().strftime('%Y-%m-%d_%H-%M-%S'),'threads':2,'batch_size':16,'gpu_layers':'all','context_size':4096,'ubatch_size':2048,'parallel':1,'pause_seconds':0}
        run_path.write_text(json.dumps(run,indent=2),encoding='utf-8')
    folder=ROOT/run['folder']
    for base in BASES:
        state('prepare',base=base)
        command(['-m','src.prepare_documents','--latest','--base-id',base],'prepare-'+base)
        report=json.loads((ROOT/'data/normalized/v6'/base/'extraction_report.json').read_text(encoding='utf-8'))
        if report['resumo'].get('falhas'):raise RuntimeError('Extraction errors: '+base)
        source=ROOT/'data/normalized/v6'/base/'documents.jsonl'
        destination=folder/base/'embeddings.jsonl';destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.exists():
            needed=set()
            with source.open(encoding='utf-8') as f:
                for line in f:
                    row=json.loads(line);needed.add(row['chunk_content_sha256'])
            old=ROOT/'data/embeddings/2026-09-30_03-08-01'/base/'embeddings.jsonl';count=0
            if old.exists():
                with old.open(encoding='utf-8') as prior,destination.open('w',encoding='utf-8') as output:
                    for line in prior:
                        row=json.loads(line)
                        if row.get('chunk_content_sha256') in needed:
                            output.write(line);count+=1
            state('cache_seeded',base=base,cached_records=count)
        state('local_embeddings',base=base,folder=run['folder'])
        command(['-m','src.run_local_embeddings','--run','--input',str(source),'--output',str(destination),
                 '--threads','2','--gpu-layers','all','--parallel','1','--ubatch-size','2048','--context-size','4096',
                 '--batch-size','16','--pause-seconds','0'],'embeddings-'+base)
    combined=ROOT/'data/normalized/v6/documents.jsonl'
    with combined.open('wb') as output:
        for base in BASES:
            with (ROOT/'data/normalized/v6'/base/'documents.jsonl').open('rb') as source:shutil.copyfileobj(source,output)
    state('build_stored_vector_indices')
    stage=RUN/'index-staging';stage.mkdir(exist_ok=True)
    manifests=[]
    for base in BASES:
        stored,model=stored_embed_fn(folder/base/'embeddings.jsonl')
        manifest=build_index(ROOT/'data/normalized/v6'/base/'documents.jsonl',stage,stored,model=model,batch_size=256)
        manifests.extend(manifest['bases']);del stored
    global_manifest={'embedding_model':model,'source_file':str(combined.relative_to(ROOT)).replace('\\','/'),'source_sha256':file_sha256(combined),'bases':manifests}
    (stage/'manifest.json').write_text(json.dumps(global_manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    # Resolve and verify both paths before moving the existing persistent index.
    active=ROOT/'index';backup=RUN/('index-before-v6-'+datetime.now().strftime('%Y-%m-%d_%H-%M-%S'))
    assert active.resolve().is_relative_to(ROOT.resolve()) and backup.resolve().is_relative_to(ROOT.resolve())
    if backup.exists():raise RuntimeError('Refuse to overwrite the old index backup')
    if active.exists():os.rename(active,backup)
    try:
        os.rename(stage,active)
    except OSError:
        if backup.exists() and not active.exists():os.rename(backup,active)
        raise
    normalized_active=ROOT/'data/normalized/documents.jsonl'
    if normalized_active.exists():
        old=normalized_active.with_name('documents.before-v6-'+datetime.now().strftime('%Y-%m-%d_%H-%M-%S')+'.jsonl')
        if old.exists():raise RuntimeError('Refuse to overwrite normalized backup')
        os.rename(normalized_active,old)
    shutil.copy2(combined,normalized_active)
    state('offline_audit',embedding_folder=run['folder'])
    command(['eval/runs/audit_v6.py'],'offline-audit')
    state('ecoa_cli_evaluation',embedding_folder=run['folder'],preserved_index=str(backup.relative_to(ROOT)))
    original=ROOT/'eval/runs/evaluate_cli_v5.py'
    script=original.read_text(encoding='utf-8').replace("output=ROOT/'eval/runs/cli-v5'","output=ROOT/'eval/runs/cli-v6'")
    script=script.replace("ROOT/'data/normalized/v5/documents.jsonl'","ROOT/'data/normalized/v6/documents.jsonl'")
    script=script.replace("save_run(report,output,'cli-v5'","save_run(report,output,'cli-v6'")
    script=script.replace('import json,sys,os,re,subprocess','import json,sys,os,re,subprocess,time')
    script=script.replace('response=cli_resposta(case.pergunta)','time.sleep(10)\n        response=cli_resposta(case.pergunta)')
    with (RUN/'ecoa-cli.log').open('w',encoding='utf-8') as output:
        import contextlib
        with contextlib.redirect_stdout(output):exec(compile(script,str(original),'exec'),{'__file__':str(original),'__name__':'__main__'})
    original=ROOT/'eval/runs/audit_cli_v5_sources.py'
    script=original.read_text(encoding='utf-8').replace('/cli-v5','/cli-v6').replace('/v5/','/v6/').replace('/v5.json','/v6.json')
    script=script.replace("expected = hashes[source]", "expected = hashes.get(source)\n    if '#derivado:' in source:\n        source_path = source.split('#', 1)[0]\n        digest = hashlib.sha256((ROOT / source_path).read_bytes()).hexdigest()\n        assert digest == chunk['inventory_hash']\n        verified[source] = {'sha256': digest, 'inventory_matches': True, 'derived_indicator': True}\n        continue")
    with (RUN/'cli-source-audit.log').open('w',encoding='utf-8') as output:
        with contextlib.redirect_stdout(output):exec(compile(script,str(original),'exec'),{'__file__':str(original),'__name__':'__main__'})
    state('evaluated',embedding_folder=run['folder'],preserved_index=str(backup.relative_to(ROOT)))
if __name__=='__main__':
    try:main()
    except Exception as error:
        state('failed',error=str(error));raise
