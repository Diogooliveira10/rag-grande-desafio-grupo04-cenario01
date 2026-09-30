"""Bounded persona-oriented PNCP discovery using the existing provider/client."""
from __future__ import annotations
import json, sys, time, unicodedata
from pathlib import Path
from datetime import date
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from src.providers.pncp import PNCPProvider
P=PNCPProvider()
OUT=ROOT/'eval/runs/v6-discovery'
OUT.mkdir(parents=True,exist_ok=True)
CALLS=[]
def request(path,params):
    url=P.client.consulta_url(path,params)
    import hashlib
    dest=OUT/(hashlib.sha256(url.encode()).hexdigest()+'.json')
    if dest.exists(): return json.loads(dest.read_text(encoding='utf-8'))['payload']
    time.sleep(12)
    start=time.monotonic()
    for attempt in range(4):
        res=P.client.request_url(url,'json',attempts=1,timeout=40)
        if res.get('status') in ['disponivel','nao_modificado']:break
        error=str(res.get('erro') or '')
        if 'Too Many Requests' not in error and '429' not in error:break
        wait=min(60,15*(2**attempt))
        with (OUT/'retry-events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps({'url':url,'attempt':attempt+1,'wait_seconds':wait,'error':error})+'\n')
        time.sleep(wait)
    event={'url':url,'status':res.get('status'),'seconds':round(time.monotonic()-start,3)}
    CALLS.append(event)
    with (OUT/'requests.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(event)+'\n')
    if res.get('status') not in ['disponivel','nao_modificado']:raise RuntimeError(str(event)+' '+str(res.get('erro')))
    payload=res.get('json') or {}
    dest.write_text(json.dumps({'url':url,'payload':payload},ensure_ascii=False),encoding='utf-8')
    return payload

def normalized(s):return ''.join(c for c in unicodedata.normalize('NFKD',s.casefold()) if not unicodedata.combining(c))
def tags(record):
    s=normalized(str(record.get('objetoContrato') or record.get('objetoCompra') or ''))
    result=[]
    if 'ambul' in s:result.append('ambulancia')
    if any(x in s for x in ['software','licenc','tecnolog','informat','computador']):result.append('tecnologia')
    if any(x in s for x in ['manutenc','suporte']) and any(x in s for x in ['computador','informat','microcomputador']):result.append('manutencao_computadores')
    if any(x in s for x in ['escritorio','papelaria','expediente']):result.append('material_escritorio')
    return result

def run():
    organizations={'42498600000171':{'cnpj':'42498600000171','esferaId':'E','razaoSocial':'ESTADO DO RIO DE JANEIRO'}}
    publications={}
    for month in [1,4,7,10]:
        end={1:'31',4:'30',7:'31',10:'31'}[month]
        for mode in [6,8]:
            for page in [1,2]:
                data=request('/v1/contratacoes/publicacao',{'dataInicial':f'2025{month:02d}01','dataFinal':f'2025{month:02d}{end}','codigoModalidadeContratacao':mode,'uf':'RJ','pagina':page,'tamanhoPagina':50})
                for row in P._records(data):
                    org=row.get('orgaoEntidade') or {};unit=row.get('unidadeOrgao') or {}
                    if org.get('esferaId') in ['M','E'] and unit.get('ufSigla')=='RJ':
                        organizations[str(org['cnpj'])]=org
                        publications[str(row['numeroControlePNCP'])]=row
    (OUT/'publications.json').write_text(json.dumps(list(publications.values()),ensure_ascii=False),encoding='utf-8')
    priority={}
    for row in publications.values():
        c=(row.get('orgaoEntidade') or {}).get('cnpj')
        priority[c]=priority.get(c,0)+len(tags(row))*5
    ordered=sorted(organizations.values(),key=lambda o:(-priority.get(o['cnpj'],0),o['cnpj']))
    selected_orgs=[]
    for sphere,limit in [('E',80),('M',100)]:
        selected_orgs.extend([o for o in ordered if o['esferaId']==sphere][:limit])
    if not any(o['cnpj']=='42498600000171' for o in selected_orgs):selected_orgs.insert(0,organizations['42498600000171'])
    candidates={}
    for index,org in enumerate(selected_orgs):
        for page in range(1,2):
            payload=request('/v1/contratos',{'dataInicial':'20250101','dataFinal':'20251231','cnpjOrgao':org['cnpj'],'pagina':page,'tamanhoPagina':500})
            for row in P._records(payload):
                if P._unit_uf(row)=='RJ' and P._signature_year(row)=='2025' and not P._is_empenho(row) and (row.get('orgaoEntidade') or {}).get('esferaId') in ['M','E']:
                    candidates[row['numeroControlePNCP']]=row
            if page>=int(payload.get('totalPaginas') or 1):break
        summary={'organizations_done':index+1,'organizations_total':len(selected_orgs),'eligible_contracts':len(candidates)}
        (OUT/'progress.json').write_text(json.dumps(summary),encoding='utf-8')
        (OUT/'contracts.json').write_text(json.dumps(list(candidates.values()),ensure_ascii=False),encoding='utf-8')
        print(json.dumps(summary),flush=True)
    from collections import Counter
    report={'organizations':selected_orgs,'candidate_contracts':len(candidates),'spheres':dict(Counter((x.get('orgaoEntidade') or {}).get('esferaId') for x in candidates.values())),'tags':dict(Counter(t for x in candidates.values() for t in tags(x))),'population':'bounded purposive discovery; not a census','max_items_per_base_per_organization':5,'organization_key':'orgaoEntidade.cnpj','request_pause_seconds':12,'signature_year':2025,'uf':'RJ'}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':run()
