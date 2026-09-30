"""Download the official rule texts through the existing corpus downloader."""
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import curate_v6 as C

def main():
    output=C.OUT/'official-laws.json'
    if output.exists():return
    items=[]
    md={'orgao_identificador':'publicador:www.cge.rj.gov.br','orgao':'Controladoria Geral do Estado do Rio de Janeiro','esfera':'estadual','uf':'RJ','tipo_fonte':'texto_normativo','vigencia_historica_verificada':False}
    for name,url in [('Decreto estadual 48.816/2023','https://www.cge.rj.gov.br/wp-content/uploads/2024/01/Decreto-no-48.816.pdf'),('Decreto estadual 48.760/2023','https://www.cge.rj.gov.br/wp-content/uploads/2024/01/decreto-48.760.pdf')]:
        item=C.pdf_item(url,md,'editais_normativo',None,name)
        if item is None:raise RuntimeError('Official decree did not return PDF: '+url)
        item['kind']='texto_normativo';items.append(item)
    url='https://legis.senado.gov.br/norma/572878/publicacao/34621013'
    result=C.F.source_check.download_url(url,C.OUT/'files','lc123_compilacao',attempts=3,timeout=40,deduplicate=False)
    if result.get('status') not in ('baixado','reutilizado'):raise RuntimeError('LC 123 download failed: '+str(result.get('erro')))
    path=Path(result['arquivo']);path=path if path.is_absolute() else ROOT/path
    text=path.read_text(encoding='utf-8',errors='replace')
    if '123' not in text or '2006' not in text or len(text)<10000:raise RuntimeError('LC 123 HTML not verified')
    old={'path':str(path.relative_to(ROOT)).replace('\\','/'),'origem':url,'sha256':C.hashlib.sha256(path.read_bytes()).hexdigest(),'tipo':'html','bytes':path.stat().st_size}
    items.append({'id':url,'base':'editais_normativo','old':old,'new_download':True,'digest':old['sha256'],'metadata':{'orgao_identificador':'publicador:legis.senado.gov.br','orgao':'Senado Federal','esfera':'federal','tipo_fonte':'texto_normativo','titulo_documento':'Lei Complementar 123/2006 - compilação consultada em 2026-09-30','vigencia_historica_verificada':False},'kind':'texto_normativo'})
    C.dump(output,items);print(json.dumps({'official_rules':len(items)}))

if __name__=='__main__':main()
