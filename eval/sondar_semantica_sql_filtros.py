"""Sondas locais do contrato semântico; registra comportamento, não o corrige."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import query_analysis as qa, structured_db as db, structured_query as sq
from src.obs import default_logger
from src.schema import QueryFilters, QueryIntent, Natureza


def main():
    folder = ROOT/'data/runs/auditoria-sql-pipeline-2026-10-18/semantic_probes'
    if folder.exists():
        raise SystemExit('Destino já existe')
    folder.mkdir(parents=True)
    default_logger.logs_dir = folder/'isolated_logs'
    output = {'field_parity': [], 'synthetic_llm_plans': [], 'cnpj_role': {}, 'temporal_parity': {}}
    fields = ['esfera','municipio','uf','modalidade','fornecedor','orgao_nome','unidade_nome',
              'categoria','classe','mes_assinatura','mes_publicacao','cnpj']
    values = {'esfera':'estadual','uf':'RJ','modalidade':'pregao','cnpj':'11222333000181'}
    for field in fields:
        f = QueryFilters(metrica='contagem',filtros_analiticos={field:values.get(field,'EXEMPLO')})
        intent = QueryIntent(pergunta='Quantos registros?',bases=[Natureza.ESTRUTURADA],operacao='estatistica',filtros=f)
        plan = sq.consulta_da_intencao(intent)
        output['field_parity'].append({'field':field,'accepted_by_schema':True,
            'retrieval_filter_keys':list(qa.filtros_para_retrieve(f)),
            'runtime_sql_plan_available':plan is not None})
    examples = [
        ('pca_missing_reference','Qual o total planejado no PCA de 2025?',
         {'ano':2025,'metrica':'soma'},['agregada']),
        ('unit_price_missing_kind','Qual a mediana do preço unitário dos contratos de engenharia?',
         {'metrica':'mediana'},['estruturada']),
        ('invented_agency','Qual o total dos contratos de 2025?',
         {'ano':2025,'metrica':'soma','orgao':'SECRETARIA INVENTADA PARA SONDA'},['estruturada']),
    ]
    for label, query, filters, bases in examples:
        fake = {'intencoes':[{'pergunta':query,'bases':bases,'operacao':'estatistica','filtros':filters}]}
        analysis = qa.analisar_query(query,modo='llm',cliente=lambda _,fake=fake:json.dumps(fake))
        plan = sq.consulta_da_intencao(analysis.intencoes[0]) if analysis.intencoes else None
        output['synthetic_llm_plans'].append({'case':label,'query':query,'model_is_fake':True,
            'analysis_method':analysis.metodo,'analysis':analysis.model_dump(mode='json'),
            'sql_plan':asdict(plan) if plan else None})
    query='Quais contratos de manutenção foram assinados em 2025?'
    explicit=qa._analisar(query)
    ambiguous=qa._analisar('Quais contratos de manutenção de 2025?')
    for label, f in [('explicit_signature',explicit),('unspecified_year_role',ambiguous)]:
        plan=sq.detectar_consulta(query if label=='explicit_signature' else 'Quais contratos de manutenção de 2025?',f,[Natureza.ESTRUTURADA])
        output['temporal_parity'][label]={'filters':f.model_dump(mode='json'),
            'retrieval':qa.filtros_para_retrieve(f),'sql_plan':asdict(plan) if plan else None,
            'where':sq._where(plan,plan.termos_objeto) if plan else None}
    # A deliberately synthetic role-collision fixture, never the active database.
    target='11222333000181'
    def record(id,org,supplier):
        return {'chunk_id':id,'base_id':'contratos_estruturado','source_file':id+'.json',
                'text':json.dumps({'numeroControlePNCP':org+'-2-'+('000001' if id=='supplier' else '000002')+'/2025',
                    'dataAssinatura':'2025-01-10','dataPublicacaoPncp':'2025-01-12',
                    'valorGlobal':100.0,'objetoContrato':'Manutenção de equipamentos',
                    'niFornecedor':supplier,'nomeRazaoSocialFornecedor':'FORNECEDOR SINTETICO',
                    'orgaoEntidade':{'cnpj':org,'razaoSocial':'ORGAO SINTETICO','esferaId':'M'},
                    'unidadeOrgao':{'ufSigla':'RJ','municipioNome':'Cidade Azul','nomeUnidade':'U'}})}
    rows=[record('supplier','99888777000166',target),record('agency',target,'44555666000177')]
    docs=folder/'role_documents.jsonl'
    docs.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf8')
    bank=db.Banco(sqlite_path=folder/'roles.sqlite')
    db.construir_banco(docs,bank)
    query='Quais contratos do fornecedor CNPJ '+target+'?'
    f=qa._analisar(query)
    plan=sq.detectar_consulta(query,f,[Natureza.ESTRUTURADA])
    resolved=sq._resolver_valores(plan,bank)
    where,params=sq._where(resolved,resolved.termos_objeto)
    matches=bank.consultar('SELECT chunk_id,orgao_cnpj,fornecedor_cnpj FROM contratacoes'+where,params)
    output['cnpj_role']={'synthetic':True,'query':query,'where':where,'params':params,
        'matched_chunk_ids':[m['chunk_id'] for m in matches],'expected_role_specific':['supplier']}
    (folder/'semantic_probes.json').write_text(json.dumps(output,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf8')
    print('FIELD PARITY',output['field_parity'])
    print('LLM PROBES',[(r['case'],r['analysis_method'],r['sql_plan']['tabela'] if r['sql_plan'] else None,
                         r['analysis']['filtros'].get('tipo_valor')) for r in output['synthetic_llm_plans']])
    print('CNPJ ROLE',output['cnpj_role'])
    print('TEMPORAL',output['temporal_parity'])


if __name__=='__main__':
    main()
