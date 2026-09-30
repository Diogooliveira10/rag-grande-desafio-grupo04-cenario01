"""Export the existing PDF extraction as one Markdown per inventoried ata PDF."""
import collections, hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'eval/runs/atas-v6-md'

def main():
    inventory=json.loads((ROOT/'data/inventory/v6.json').read_text(encoding='utf-8'))
    pdfs=[x for x in inventory['arquivos'] if x['status']=='ok' and x['base_id']=='atas_conversacional' and x['tipo']=='pdf']
    source_zip=json.loads((ROOT/'eval/runs/zip-atas-v6.json').read_text(encoding='utf-8-sig'))
    assert len(pdfs)==source_zip['pdf_entries']
    units=collections.defaultdict(list)
    with (ROOT/'data/normalized/v6/atas_conversacional/documents.units.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line)
            if row['formato']=='pdf':units[row['source_file']].append(row)
    records=[]
    for source in pdfs:
        pages=sorted(units[source['source_file']],key=lambda row:int(row.get('extras',{}).get('pagina') or 0))
        assert pages, ('PDF has no extracted units',source['source_file'])
        raw_path=(ROOT/source['source_file']).resolve()
        assert raw_path.is_relative_to((ROOT/'data/raw/v6').resolve())
        assert hashlib.sha256(raw_path.read_bytes()).hexdigest()==source['sha256']
        relative=raw_path.relative_to(ROOT/'data/raw/v6').with_suffix('.md')
        destination=OUT/relative
        if destination.exists():raise RuntimeError('Refuse to overwrite Markdown export: '+str(relative))
        destination.parent.mkdir(parents=True,exist_ok=True)
        title=(source.get('document_metadata') or {}).get('titulo_documento') or raw_path.stem
        body=[f'# {title}', '', f'Fonte: [PDF original]({source["origem"]})', '', f'SHA-256 do PDF: `{source["sha256"]}`', '', 'Texto extraído automaticamente, organizado por página.', '']
        empty=0
        for page in pages:
            number=page.get('extras',{}).get('pagina') or 1
            text=str(page.get('text') or '').strip()
            body.extend([f'## Página {number}', '', text or '[Página sem texto extraível na preparação.]', ''])
            empty+=not bool(text)
        destination.write_text('\n'.join(body),encoding='utf-8')
        records.append({'source_file':str(destination.relative_to(ROOT)).replace('\\','/'),'entry_name':relative.as_posix(),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'pdf_sha256':source['sha256'],'origem':source['origem'],'pages':len(pages),'empty_pages':empty})
    report={'version':'v6','pdf_sources':len(pdfs),'markdown_files':len(records),'uses_existing_extraction':True,'only_public_source_links':True,'records':records}
    (ROOT/'eval/runs/atas-v6-md-export.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='records'}))

if __name__=='__main__':main()
