"""Check live catalog API/organization cards against independent raw records.

Run from the backend root with API, Vite frontend and Python Playwright/Edge available.
"""
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import urllib.request
from playwright.sync_api import sync_playwright

root = Path.cwd()
out = root / 'data/runs/numerical-validation'
rows = [json.loads(line) for line in (root / 'data/normalized/documents.jsonl').open(encoding='utf-8')]
raw = {}
for c in rows:
    if c.get('base_id') != 'contratos_estruturado' or c.get('sensitivity') != 'publico':
        continue
    r = json.loads((root / c['source_file']).read_text(encoding='utf-8'), parse_float=Decimal)
    raw.setdefault(r['numeroControlePNCP'], r)
expected = defaultdict(list)
for r in raw.values():
    expected[r['orgaoEntidade']['cnpj']].append(r)

def fetch(path):
    return json.loads(urllib.request.urlopen('http://localhost:8080' + path, timeout=30).read())

def all_pages(path):
    result = []
    while True:
        page = fetch(path + '?limit=100&offset=' + str(len(result)))
        result.extend(page)
        if len(page) < 100:
            return result

contracts = all_pages('/api/v1/explore/contratacoes')
orgs = all_pages('/api/v1/explore/orgaos')
errors = []
if len(contracts) != len(raw):
    errors.append('Contract catalog count differs')
for c in contracts:
    expected_value = raw[c['id']].get('valorGlobal')
    actual = c['valor_global']
    if expected_value is None:
        if actual is not None:
            errors.append('Missing value replaced: ' + c['id'])
    elif Decimal(str(actual)).quantize(Decimal('.01')) != Decimal(str(expected_value)).quantize(Decimal('.01')):
        errors.append('Wrong catalog value: ' + c['id'])
if len(orgs) != len(expected):
    errors.append('Organization catalog count differs')
for org in orgs:
    rs = expected[org['cnpj']]
    if org['total_contratacoes'] != len(rs):
        errors.append('Wrong organization count: ' + org['cnpj'])
    values = [r.get('valorGlobal') for r in rs]
    total = None if any(v is None for v in values) else sum((Decimal(str(v)) for v in values), Decimal(0))
    actual = org['valor_total']
    if total is None:
        if actual is not None:
            errors.append('Incomplete org total presented as complete: ' + org['cnpj'])
    elif actual is None or Decimal(str(actual)).quantize(Decimal('.01')) != total.quantize(Decimal('.01')):
        errors.append('Wrong organization total: ' + org['cnpj'])
summary = fetch('/api/v1/project/summary')
inventory = json.loads((root / 'data/inventory/v6.json').read_text(encoding='utf-8'))
expected_documents = sum(b['arquivos'] for b in inventory['por_base']) + len({c['source_file'] for c in rows if c.get('base_id') == 'emails_sinteticos'})
if summary['total_documentos'] != expected_documents:
    errors.append('Wrong project document count')
example = next(o for o in orgs if o['cnpj'] == '00038166000105')
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width':1440,'height':1000})
    page.goto('http://localhost:5173/', wait_until='networkidle')
    page.get_by_role('button', name='Órgãos', exact=True).click()
    page.get_by_role('searchbox', name='Buscar órgão contratante').fill('BANCO CENTRAL DO BRASIL')
    page.wait_for_function("document.querySelectorAll('.catalog-card').length === 1")
    page.get_by_role('heading', name='BANCO CENTRAL DO BRASIL', exact=True).wait_for()
    if page.locator('.orgao-metric-box .metric-num').inner_text() != str(example['total_contratacoes']):
        errors.append('Organization count not rendered')
    text = page.locator('body').inner_text()
    currency = 'R$ ' + f"{example['valor_total']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    if currency not in ' '.join(text.split()):
        errors.append('Organization total not rendered')
    page.screenshot(path=str(out/'browser-catalog-org.png'), full_page=True)
    browser.close()
result = {'contracts_checked':len(contracts),'organizations_checked':len(orgs),'project_documents':summary['total_documentos'],
          'browser_example':example,'errors':errors,'pass':not errors,
          'scope':'catalog uses contratos_estruturado only; chat analytics additionally include eligible pca_agregado records'}
(out/'catalog-validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(bool(errors))
