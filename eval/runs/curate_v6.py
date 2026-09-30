"""Curate bounded v6 sources with existing PNCP and corpus storage helpers."""
from __future__ import annotations
import json,sys,hashlib,time,collections,urllib.parse,re,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from src import fetch_corpus as F
from src.providers.pncp import PNCPProvider
from src.providers.base import SourceRef
from discover_v6 import tags
P=PNCPProvider();OUT=ROOT/'eval/runs/v6-curation';OUT.mkdir(parents=True,exist_ok=True)
BASES=['contratos_estruturado','editais_normativo','atas_conversacional','pca_agregado']
CAP=5;TARGET=1400
SPHERES={'M':'municipal','E':'estadual','F':'federal'}
def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def req(url):
    cache=OUT/('api-'+hashlib.sha256(url.encode()).hexdigest()+'.json')
    if cache.exists():return json.loads(cache.read_text(encoding='utf-8'))['payload']
    skipped=OUT/('skipped-'+hashlib.sha256(url.encode()).hexdigest()+'.json')
    if skipped.exists():return []
    for attempt in range(4):
        time.sleep(20 if attempt==0 else min(60,20*2**attempt))
        result=P.client.request_url(url,'json',attempts=1,timeout=60)
        status=result.get('status');err=str(result.get('erro') or '')
        with (OUT/'requests.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'url':url,'status':status,'error':err,'attempt':attempt+1})+'\n')
        if status in ['disponivel','nao_modificado']:
            payload=result.get('json') or [];dump(cache,{'url':url,'payload':payload});return payload
        transient=('Too Many Requests','429','10060','timed out','Timeout','502','503','504','Remote end closed','Connection reset','10054')
        if not any(marker in err for marker in transient):break
    issue={'url':url,'issue':'unavailable_after_bounded_attempts','error':err,'attempts':attempt+1}
    dump(skipped,issue)
    with (OUT/'coverage-warnings.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(issue)+'\n')
    print(json.dumps({'stage':'source_skipped',**issue}),flush=True)
    return []
def info(row):
    o=row.get('orgaoEntidade') or {};u=row.get('unidadeOrgao') or {}
    return {'orgao_cnpj':o.get('cnpj'),'orgao':o.get('razaoSocial'),'esfera':SPHERES.get(o.get('esferaId')),
            'municipio':u.get('municipioNome'),'uf':u.get('ufSigla'),'codigo_unidade':u.get('codigoUnidade'),
            'modalidade':row.get('modalidadeNome'),'numero_controle_pncp':row.get('numeroControlePNCP')}
def purchase(row):
    c=str(row.get('numeroControlePncpCompra') or row.get('numeroControlePNCPCompra') or row.get('numeroControlePNCP') or '')
    m=re.match(r'(\d+)-1-(\d+)/(\d{4})',c)
    return f'{m[1]}-1-{int(m[2]):06d}/{m[3]}' if m else None
def source_purchase(ref):
    k=(ref or {}).get('key') or {};c=k.get('numeroControlePNCPCompra')
    if c:return purchase({'numeroControlePNCP':c})
    resource=str((ref or {}).get('resource_type') or '').casefold()
    if resource in ('pca','pca_agregado','contrato_assinado'):return None
    y=k.get('ano_compra') or k.get('ano');s=k.get('sequencial_compra') or k.get('sequencial')
    return f"{k['cnpj']}-1-{int(s):06d}/{y}" if k.get('cnpj') and y and s and str(s).isdigit() else None
def score(x):return 20*len(tags(x.get('payload') or {}))+ (120 if x.get('kind')=='pca_planejado' else 100 if x.get('aggregate_bundle') else 10 if x.get('kind')=='contrato' else 0)
def balanced(candidates,limit):
    groups=collections.defaultdict(list)
    for x in candidates:
        md=x['metadata'];key=md.get('orgao_cnpj') or md.get('orgao_identificador')
        if not key:continue
        groups[(x['base'],key)].append(x)
    selected=[];seen=set();group_counts=collections.Counter();sphere_counts=collections.Counter()
    queues={key:sorted(rows,key=lambda x:(-score(x),x['id'])) for key,rows in groups.items()}
    while len(selected)<limit:
        available=[key for key,q in queues.items() if q and group_counts[key]<CAP]
        if not available:break
        key=min(available,key=lambda k:(sphere_counts[queues[k][0]['metadata'].get('esfera','norma')],group_counts[k],-score(queues[k][0]),k))
        item=queues[key].pop(0);identity=(item['base'],item.get('digest') or (item.get('kind'),item['id']))
        if identity in seen:continue
        seen.add(identity);selected.append(item);group_counts[key]+=1;sphere_counts[item['metadata'].get('esfera','norma')]+=1
    return selected

def catalog():
    inventory=json.loads((ROOT/'data/inventory/v5.json').read_text(encoding='utf-8'))
    rows=[];metadata_by_purchase={};metadata_by_org={}
    for entry in inventory['arquivos']:
        if entry.get('status')!='ok' or entry['tipo']!='json':continue
        body=json.loads((ROOT/entry['path']).read_text(encoding='utf-8-sig'))
        if isinstance(body,dict) and body.get('orgaoEntidade') and body.get('unidadeOrgao'):
            md=info(body)
            if md['uf']!='RJ':continue
            if md.get('orgao_cnpj'):metadata_by_org[md['orgao_cnpj']]=md
            buy=purchase(body)
            if buy:md['numero_controle_compra']=buy
            if buy:metadata_by_purchase[buy]=md
            if entry['base_id']=='contratos_estruturado':rows.append({'id':body['numeroControlePNCP'],'base':entry['base_id'],'old':entry,'metadata':md,'payload':body,'kind':'contrato'})
    for cache in (ROOT/'eval/runs/v6-discovery').glob('*.json'):
        d=json.loads(cache.read_text(encoding='utf-8'))
        if 'payload' not in d:continue
        is_contract='/v1/contratos?' in d['url']
        for body in P._records(d['payload']):
            md=info(body)
            if md['uf']!='RJ' or md['esfera'] not in SPHERES.values():continue
            if is_contract and (P._signature_year(body)!='2025' or P._is_empenho(body)):continue
            if not body.get('numeroControlePNCP'):continue
            metadata_by_org[md['orgao_cnpj']]=md
            buy=purchase(body)
            if buy:md['numero_controle_compra']=buy
            if buy:metadata_by_purchase[buy]=md
            item={'id':body['numeroControlePNCP'],'base':'contratos_estruturado','payload':body,'url':d['url'],'metadata':md,'kind':'contrato' if is_contract else 'contratacao_publicacao'}
            if is_contract:
                parts=P._contract_parts(body)
                if parts:
                    control,cnpj,year,sequence=parts
                    item['source_ref']=SourceRef('pncp','contrato_assinado',{'numeroControlePNCP':control,'cnpj':cnpj,'ano':year,'sequencial':sequence,**({'numeroControlePNCPCompra':buy} if buy else {})}).to_dict()
            else:
                ref=P.purchase_ref(body)
                if ref:item['source_ref']=ref.to_dict()
            rows.append(item)
            if not is_contract:rows.append({**item,'base':'pca_agregado','kind':'contratacao_publicacao'})
    for entry in inventory['arquivos']:
        if entry['base_id'] not in ['atas_conversacional','editais_normativo','pca_agregado'] or entry.get('status')!='ok':continue
        if entry['base_id']=='editais_normativo':
            host=urllib.parse.urlparse(entry['origem']).netloc
            md={'orgao_identificador':'publicador:'+host,'orgao':host,'esfera':'federal' if 'senado' in host or 'mppi' in host else 'estadual','uf':'RJ','ambito':'norma de aplicação geral; não é contrato de órgão RJ'}
        else:
            buy=source_purchase(entry.get('source_ref'));md=metadata_by_purchase.get(buy)
            if not md:continue
        rows.append({'id':entry['logical_id'],'base':entry['base_id'],'old':entry,'digest':entry['sha256'],'metadata':dict(md),'kind':entry.get('resource_kind') or ('ata_documento' if entry['base_id']=='atas_conversacional' else 'contratacao_detalhe')})
    selected=[]
    for base,limit in [('contratos_estruturado',650),('pca_agregado',450),('atas_conversacional',200),('editais_normativo',50)]:
        selected.extend(balanced([x for x in rows if x['base']==base],limit))
    return selected,metadata_by_org,metadata_by_purchase

def cached_aggregate(selected,metadata_by_purchase):
    inventory=json.loads((ROOT/'data/inventory/v5.json').read_text(encoding='utf-8'))
    groups=collections.defaultdict(list)
    for entry in inventory['arquivos']:
        if entry.get('status')!='ok' or entry.get('base_id')!='pca_agregado':continue
        if entry.get('resource_kind') not in ('contratacao_vinculada','itens','resultado_item'):continue
        buy=source_purchase(entry.get('source_ref'))
        if buy in metadata_by_purchase:groups[buy].append(entry)
    by_org=collections.defaultdict(list)
    for buy,entries in groups.items():
        kinds={e['resource_kind'] for e in entries}
        if not {'contratacao_vinculada','itens','resultado_item'}<=kinds:continue
        item=next(e for e in entries if e['resource_kind']=='itens')
        payload=json.loads((ROOT/item['path']).read_text(encoding='utf-8-sig'))
        descriptions=' '.join(str(r.get('descricao') or r.get('descricaoItem') or '') for r in P._records(payload))
        priority=len(tags({'objetoCompra':descriptions}))
        md=metadata_by_purchase[buy]
        by_org[md['orgao_cnpj']].append((priority,buy,entries,md))
    bundles=[]
    for org,options in sorted(by_org.items()):
        _,buy,entries,md=max(options,key=lambda x:(x[0],x[1]))
        details=[e for e in entries if e['resource_kind']=='contratacao_vinculada'][:1]
        items=[e for e in entries if e['resource_kind']=='itens'][:1]
        results=sorted([e for e in entries if e['resource_kind']=='resultado_item'],key=lambda e:e['logical_id'])[:2]
        for entry in details+items+results:
            bundles.append({'id':entry['logical_id'],'base':'pca_agregado','old':entry,'digest':entry['sha256'],'metadata':dict(md),'kind':entry['resource_kind'],'aggregate_bundle':True})
    selected.extend(bundles)
    dump(OUT/'cached-aggregate.json',{'files':len(bundles),'organizations':len(by_org),'network_calls':0})
    return balanced(selected,len(selected))

def supplementary_plans(selected):
    state=OUT/'pca-plans.json'
    if state.exists():return balanced(selected+json.loads(state.read_text(encoding='utf-8')),len(selected)+3)
    plans=[]
    for sphere in ('estadual','municipal','federal'):
        candidates=[x for x in selected if x['base']=='pca_agregado' and x['metadata'].get('esfera')==sphere and x.get('payload')]
        if not candidates:continue
        candidate=max(candidates,key=score);md=candidate['metadata'];cnpj=md['orgao_cnpj']
        url=P.client.api_url(f'/v1/orgaos/{cnpj}/pca/2025/consolidado/unidades')
        units=P._records(req(url))
        for unit in units:
            sequence=P._first(unit,'sequencialPca','sequencial')
            code=P._first(unit,'codigoUnidade','codigoUnidadeAdministrativa','codigoUnidadeOrgao')
            if sequence is None:continue
            if sphere=='federal' and str(code)!=str(md.get('codigo_unidade')):continue
            ref=SourceRef('pncp','pca',{'cnpj':cnpj,'ano':'2025','sequencial':str(sequence)})
            artifact=next(a for a in P.resolve(ref).artifacts if a.metadata.get('resource_kind')=='pca_planejado')
            payload=req(artifact.url)
            if not payload:continue
            metadata={k:v for k,v in md.items() if k not in ('modalidade','numero_controle_pncp','numero_controle_compra','codigo_unidade')}
            metadata.update({'ano_pca':2025,'titulo_documento':'PCA 2025 consolidado','tipo_fonte':'pca_planejado'})
            plans.append({'id':ref.stable_id,'base':'pca_agregado','payload':payload,'url':artifact.url,'metadata':metadata,'kind':'pca_planejado','source_ref':ref.to_dict()})
            break
    dump(state,plans)
    print(json.dumps({'stage':'pca_plans','files':len(plans)}),flush=True)
    return balanced(selected+plans,len(selected)+len(plans))

def pdf_item(url,md,base,reference,title):
    key=hashlib.sha256(url.encode()).hexdigest()
    sidecar=OUT/('pdf-'+key+'.json')
    if sidecar.exists():return json.loads(sidecar.read_text(encoding='utf-8'))
    directory=OUT/'files';directory.mkdir(exist_ok=True)
    time.sleep(12)
    result=F.source_check.download_url(url,directory,'document_'+key[:16],attempts=3,timeout=40,deduplicate=False)
    if result.get('status') not in ['baixado','reutilizado']:
        issue={'url':url,'issue':'document_download_unavailable','error':str(result.get('erro') or result.get('status'))}
        with (OUT/'coverage-warnings.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(issue)+'\n')
        print(json.dumps({'stage':'source_skipped',**issue}),flush=True)
        return None
    path=Path(result['arquivo'])
    if not path.is_absolute():path=ROOT/path
    body=path.read_bytes()
    if not body.startswith(b'%PDF-'):
        with (OUT/'coverage-warnings.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'url':url,'issue':'not_a_pdf'})+'\n')
        return None
    raw={'path':str(path.relative_to(ROOT)).replace('\\','/'),'origem':url,'sha256':hashlib.sha256(body).hexdigest(),'tipo':'pdf','bytes':len(body)}
    item={'id':url,'base':base,'old':raw,'new_download':True,'digest':raw['sha256'],'metadata':{**md,'titulo_documento':title},'kind':'documentos_edital' if base=='editais_normativo' else 'ata_documento','source_ref':reference}
    dump(sidecar,item);return item

def supplementary_documents(selected):
    state=OUT/'normative-supplement.json'
    saved=json.loads(state.read_text(encoding='utf-8')) if state.exists() else []
    selected.extend(saved)
    marker=OUT/'normative-publications-complete.json'
    if marker.exists():return selected
    already={x['metadata'].get('orgao_cnpj') for x in saved}
    candidates=[x for x in selected if x['base']=='pca_agregado' and x.get('payload') and purchase(x['payload'])]
    counts=collections.Counter()
    for item in sorted(candidates,key=lambda x:(-score(x),x['id'])):
        md=item['metadata'];cnpj=md['orgao_cnpj'];sphere=md['esfera']
        if cnpj in already or counts[sphere]>=4:continue
        already.add(cnpj);counts[sphere]+=1
        buy=purchase(item['payload']);ref=P.purchase_ref({'numeroControlePNCP':buy})
        if ref is None:continue
        listing=next(a for a in P.resolve(ref).artifacts if a.metadata.get('resource_kind')=='documentos_edital')
        data=req(listing.url)
        for document in P._records(data):
            title=str(document.get('titulo') or document.get('tipoDocumentoNome') or '')
            document_type=str(document.get('tipoDocumentoNome') or '')
            if not any(t in (title+' '+document_type).casefold() for t in ['edital','aviso de contrata','termo de refer','termo_de_refer','anexo','especific']):continue
            content_url=document.get('url') or document.get('uri')
            if not content_url:continue
            new=pdf_item(content_url,md,'editais_normativo',ref.to_dict(),title)
            if new:
                selected.append(new);saved.append(new);break
        dump(state,saved)
        print(json.dumps({'stage':'normative_publication','sphere':sphere,'org':cnpj,'documents':len(saved)}),flush=True)
    dump(marker,{'documents':len(saved),'organizations_probed':len(already)})
    return selected

def supplementary(selected,organizations):
    counts=collections.Counter((x['base'],x['metadata'].get('orgao_cnpj') or x['metadata'].get('orgao_identificador')) for x in selected)
    done=OUT/'supplementary.json';saved=json.loads(done.read_text(encoding='utf-8')) if done.exists() else []
    saved=list({(x['base'],x['id']):x for x in saved}.values())
    dump(done,saved)
    selected.extend(saved)
    known={(x['base'],x['id']) for x in selected}
    for x in saved:counts[x['base'],x['metadata'].get('orgao_cnpj') or x['metadata'].get('orgao_identificador')]+=1
    md_list=list(organizations.values());md_list.sort(key=lambda m:(collections.Counter(x['metadata'].get('esfera') for x in selected)[m['esfera']],m['orgao_cnpj']))
    pdf_count=sum(x.get('kind')=='ata_documento' and x.get('new_download',False) for x in saved)
    for index,md in enumerate(md_list):
        cnpj=md['orgao_cnpj'];base='atas_conversacional'
        if counts[base,cnpj]>=CAP or len(selected)>=TARGET:continue
        params={'dataInicial':'20250101','dataFinal':'20251231','cnpj':cnpj,'pagina':1,'tamanhoPagina':10}
        if md.get('codigo_unidade'):params['codigoUnidadeAdministrativa']=md['codigo_unidade']
        url=P.client.consulta_url('/v1/atas',params);payload=req(url)
        records=P._records(payload)
        if md.get('codigo_unidade'):
            records=[r for r in records if not r.get('codigoUnidadeOrgao') or str(r['codigoUnidadeOrgao'])==str(md['codigo_unidade'])]
        if records and pdf_count<24 and counts[base,cnpj]<CAP:
            candidate=P._ata_candidate(records[0])
            if candidate:
                listing=next(a for a in P.resolve(candidate.source_ref).artifacts if a.artifact_id.endswith(':arquivos'))
                data=req(listing.url)
                for document in P._records(data):
                    if not document.get('url'):continue
                    new=pdf_item(document['url'],md,base,candidate.source_ref.to_dict(),str(document.get('titulo') or 'Ata de registro de preços'))
                    if new and (new['base'],new['id']) not in known:
                        selected.append(new);saved.append(new);counts[base,cnpj]+=1;pdf_count+=1;break
        for row in sorted(records,key=lambda x:(-len(tags(x)),str(x.get('numeroControlePNCP') or x.get('numeroAta') or ''))):
            if counts[base,cnpj]>=CAP or len(selected)>=TARGET:break
            if P._unit_uf(row) and P._unit_uf(row)!='RJ':continue
            candidate=P._ata_candidate(row)
            if candidate is None:continue
            if (base,candidate.candidate_id) in known:continue
            item={'id':candidate.candidate_id,'base':base,'payload':row,'url':url,'metadata':dict(md),'kind':'ata','source_ref':candidate.source_ref.to_dict()}
            selected.append(item);saved.append(item);counts[base,cnpj]+=1;known.add((base,candidate.candidate_id))
        dump(done,saved);dump(OUT/'progress.json',{'organizations_done':index+1,'organizations_total':len(md_list),'files_selected':len(selected),'cap':CAP})
        print(json.dumps({'stage':'ata_discovery','org':index+1,'total':len(md_list),'selected':len(selected)}),flush=True)
    return selected

def finalize(selected):
    for item in selected:
        metadata=item['metadata']
        if metadata.get('orgao_identificador','').startswith('publicador:www.'):
            metadata['orgao_identificador']=metadata['orgao_identificador'].replace('publicador:www.','publicador:',1)
        if item['base']=='atas_conversacional':metadata.pop('modalidade',None)
        metadata.pop('numero_controle_pncp',None)
        metadata.pop('numero_controle_compra',None)
        body=item.get('payload') or {}
        if item['kind']=='pca_planejado' and isinstance(body,dict):
            for key,raw in [('orgao_cnpj','cnpj'),('orgao','razaoSocial'),('municipio','municipio'),('uf','uf'),('codigo_unidade','codigoUnidade')]:
                if body.get(raw) not in (None,''):metadata[key]=body[raw]
        control=body.get('numeroControlePNCP') or body.get('numeroControlePNCPAta')
        if control:metadata['numero_controle_pncp']=control
        buy=purchase(body) or source_purchase(item.get('source_ref') or item.get('old',{}).get('source_ref'))
        if buy and buy!=control:metadata['numero_controle_compra']=buy
    manifest_path=ROOT/'corpus_manifest.yaml';original=manifest_path.read_text(encoding='utf-8')
    backup=OUT/'corpus_manifest.before-v6.yaml'
    if not backup.exists():backup.write_text(original,encoding='utf-8')
    selected=balanced(selected,TARGET)
    sizes=collections.Counter(x['base'] for x in selected)
    text=backup.read_text(encoding='utf-8')
    for base in BASES:
        pattern=r'(^  - base_id: '+re.escape(base)+r'\n)(.*?)(?=^  - base_id: |\Z)'
        def change(m):return m[1]+re.sub(r'(?m)^(    volume_alvo: )\d+',lambda v:v[1]+str(sizes[base]),m[2])
        text,count=re.subn(pattern,change,text,flags=re.S|re.M)
        if count!=1:raise RuntimeError('Manifest section not found: '+base)
    if 'curadoria_v6:' not in text:text+='\ncuradoria_v6:\n  limite_por_orgao_por_base: 5\n  identificador_orgao: cnpj\n  alvo_arquivos_total: 1400\n  selecao: amostra_intencional_personas_nao_censo\n'
    manifest_path.write_text(text,encoding='utf-8')
    manifest=F._manifest_for_scope(F.load_manifest(manifest_path),official=True)
    digest=F._manifest_hash(manifest_path)
    context=F._load_version_context(manifest,digest,6,previous_version=5)
    by_base={b['base_id']:b for b in manifest['bases']}
    for item in selected:
        base=by_base[item['base']];logical_id=item['base']+':curado:'+hashlib.sha256(item['id'].encode()).hexdigest()[:24]
        if logical_id in context.items:continue
        if 'old' in item:
            old=item['old'];saved=F._materialize_previous_record(context,item['base'],'reuso_'+old['sha256'][:16],old,old['tipo']);origin=old['origem']
        else:
            body=(json.dumps(item['payload'],ensure_ascii=False,indent=2)+'\n').encode()
            saved=F._save_version_bytes(context,body,item['base'],'registro_'+hashlib.sha256(item['id'].encode()).hexdigest()[:16],'.json');origin=item['url']
        if item.get('new_download') and saved:
            saved.pop('reutilizado_de',None);saved.pop('reuso_motivo',None)
        if not saved:raise RuntimeError('Cannot preserve source: '+logical_id)
        record=F._base_record(context,base,logical_id,origin,saved,tipo=Path(saved['path']).suffix.lstrip('.'),registros=1)
        record.update({'unidades_logicas':1,'resource_kind':item['kind'],'document_metadata':item['metadata'],'curadoria':{'limite_por_orgao_por_base':CAP,'criterio':'personas_2025_rj'}})
        if item.get('source_ref'):record['source_ref']=item['source_ref']
        elif item.get('old',{}).get('source_ref'):record['source_ref']=item['old']['source_ref']
        if 'old' in item:
            for key in ['artifact_id']:
                if key in item['old']:record[key]=item['old'][key]
    audit=collections.Counter((x['base_id'],x['document_metadata'].get('orgao_cnpj') or x['document_metadata'].get('orgao_identificador')) for x in context.items.values())
    assert max(audit.values())<=CAP
    assert all(urllib.parse.urlparse(x['origem']).scheme in ['http','https'] for x in context.items.values())
    for x in context.items.values():assert not Path(x['source_file']).is_absolute()
    F._checkpoint_write(context)
    before=F._build_inventory(manifest,6,digest,context.collected_at,context.items)
    if not before['completo']:raise RuntimeError('Selected inventory does not satisfy its manifest: '+str(before['por_base']))
    F._promote_version(context)
    inventory=F._build_inventory(manifest,6,digest,context.collected_at,context.items)
    F._write_inventory(inventory)
    F._render_corpus_inventory(inventory)
    report={'version':'v6','target':TARGET,'files':len(context.items),'by_base':dict(sizes),'sphere_files':dict(collections.Counter(x['document_metadata'].get('esfera') for x in context.items.values())),'max_per_org_per_base':max(audit.values()),'organizations':len(set(key[1] for key in audit)),'reused_files':sum('reutilizado_de' in x for x in context.items.values()),'all_source_urls_public_http':True,'absolute_machine_paths_in_metadata':False,'sampling':'purposive; partial coverage; not a census','target_gap':TARGET-len(context.items)}
    dump(OUT/'report.json',report);print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':
    if not (ROOT/'eval/runs/v6-discovery/report.json').exists():raise RuntimeError('Finish discovery before curating the immutable version')
    selected,orgs,buy=catalog();dump(OUT/'initial-selection.json',selected)
    selected=cached_aggregate(selected,buy)
    selected=supplementary_plans(selected)
    selected=supplementary_documents(selected)
    laws=OUT/'official-laws.json'
    if laws.exists():selected.extend(json.loads(laws.read_text(encoding='utf-8')))
    selected=supplementary(selected,orgs);finalize(selected)
