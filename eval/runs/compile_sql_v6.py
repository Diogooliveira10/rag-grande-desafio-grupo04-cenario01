"""Offline PostgreSQL compilation for relevant PERSONAS questions; no execution."""
import json,sys
from pathlib import Path
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from src.structured_query import Consulta,compilar_consulta_postgres
common=dict(tabela='contratacoes',tipo='agrupamento',dataset='contratos_estruturado',versao='v6',ano=2025,referencia_temporal='assinatura')
specs={
 'P1.1':dict(metrica='contagem',agrupamentos=('fornecedor',),esfera='estadual',valor_min=500000,valor_min_exclusivo=True,minimo_por_grupo=3,termos_objeto=('tecnolog',)),
 'P4.2':dict(metrica='contagem',agrupamentos=('modalidade',),termos_objeto=('manutenc','computador')),
 'P5.1':dict(metrica='maximo',agrupamentos=('numero_controle_pncp',),limite=10),
 'P5.2':dict(metrica='soma',agrupamentos=('fornecedor',),limite=10),
 'P5.3':dict(metrica='media',agrupamentos=('mes_assinatura',),termos_objeto=('tecnolog',),limite=12,ordem='ASC'),
}
cases=[json.loads(l) for l in (ROOT/'eval/golden_set.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
report=[]
for case in cases:
    ident=case['id'];entry={'id':ident,'question':case['pergunta'],'executed':False}
    if ident in specs:
        consulta=Consulta(**{**common,**specs[ident]});sql,params=compilar_consulta_postgres(consulta)
        entry.update(status='compiled_offline',consulta=asdict(consulta),sql=sql,parameters=params,
                     limitations=['Snapshot intencional com teto de cinco fontes por órgão/base; não representa censo.','Termos literais; sem equivalências ou aliases de itens aprovados.','As tabelas de fatos PostgreSQL não foram criadas nem carregadas.'])
    elif ident in ['P2.2','F1']:
        entry.update(status='unsupported_unit_price_and_technical_equivalence',reason='O compilador atual não representa preço por veículo e equivalência técnica documentada; não substituir por mediana de valores globais.')
    elif ident=='P4.3':entry.update(status='requires_previous_calculation_and_normative_sources',reason='O cálculo da modalidade está em P4.2; a aplicação da regra exige evidência normativa e vínculo com o processo.')
    else:entry['status']='no_arithmetic_compilation_required'
    report.append(entry)
output=ROOT/'eval/runs/sql-v6';output.mkdir(exist_ok=True)
(output/'personas-compilation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
for row in report:
    if row.get('sql'):(output/(row['id']+'.sql')).write_text('-- Compilação offline, sem execução. Parâmetros: '+json.dumps(row['parameters'],ensure_ascii=False)+'\n'+row['sql']+';\n',encoding='utf-8')
print(json.dumps({'compiled':sum(x['status']=='compiled_offline' for x in report),'cases':len(report),'executed':0}))
