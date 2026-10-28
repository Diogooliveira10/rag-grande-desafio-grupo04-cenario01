import json
from urllib.parse import parse_qs, urlparse
import pytest

from src.supplement_contracts_v7 import executar, _selecionar
from src.supplement_corpus_v7 import ConsultasOrcadas
from src.collection_policy import PoliticaColeta
from src.direct_collection import ENDPOINT_CONTRATOS
from tests.test_recover_corpus_v7 import CNPJ


def registro(n, uf='RJ', ano=2025):
    return {'numeroControlePNCP': f'{CNPJ}-2-{n:06d}/2026', 'dataAssinatura': f'{ano}-12-30', 'dataPublicacaoPncp': '2026-01-05T12:00:00', 'orgaoEntidade': {'cnpj': CNPJ}, 'unidadeOrgao': {'ufSigla': uf}, 'tipoContrato': {'nome': 'Contrato (termo inicial)'}, 'objetoContrato': 'Objeto de teste'}


def plano(tmp_path, quantidade=1):
    js = [{'id': f'teste-{i}', 'endpoint': ENDPOINT_CONTRATOS, 'params': {'dataInicial': '20260101' if i==0 else '20260201', 'dataFinal': '20260131' if i==0 else '20260228', 'cnpjOrgao': CNPJ, 'tamanhoPagina': 500}} for i in range(quantidade)]
    p = tmp_path / 'plano.json'; p.write_text(json.dumps({'politica': {'ano': 2025, 'uf': 'RJ', 'publicacao_inicio': '2025-01-01', 'publicacao_fim': '2026-09-25', 'versao': 1}, 'janelas_a_adquirir': js}))
    return p


class Paginas:
    def __init__(self): self.calls = []
    def request_url(self, url, *args, **kwargs):
        self.calls.append(url)
        page = int(parse_qs(urlparse(url).query)['pagina'][0])
        rs = [registro(1), registro(2, ano=2026)] if page==1 else [registro(3, uf='SP'), registro(4)]
        return {'status': 'disponivel', 'http_status': 200, 'json': {'data': rs, 'totalRegistros': 4, 'totalPaginas': 2}}


def test_retomada_por_pagina_nao_refaz_http_e_filtra_ano_uf(tmp_path):
    p = plano(tmp_path); client = Paginas(); out=tmp_path/'out'
    a = executar(p, out, rede=ConsultasOrcadas(1, 0, client))
    assert a['janelas_por_status'] == {'parcial': 1}
    assert a['ocorrencias_exportadas_janelas_completas'] == 0
    assert a['registros_elegiveis_em_cache'] == 1
    assert (out/'documents.jsonl').read_bytes() == b''
    b = executar(p, out, rede=ConsultasOrcadas(1, 0, client), retomar=True)
    assert [int(parse_qs(urlparse(u).query)['pagina'][0]) for u in client.calls] == [1,2]
    assert b['aquisicao_plano_completa']
    assert b['ocorrencias_exportadas_janelas_completas'] == 2
    rs = [json.loads(json.loads(l)['text']) for l in (out/'documents.jsonl').read_text().splitlines()]
    assert all(r['dataAssinatura'].startswith('2025') and r['unidadeOrgao']['ufSigla']=='RJ' for r in rs)
    assert all(r['dataPublicacaoPncp'].startswith('2026') for r in rs)
    assert not b['promocao_autorizada']
    assert not (out/'.coleta.lock').exists()


def test_cache_tamper_preserva_exportacao_anterior(tmp_path):
    p=plano(tmp_path);out=tmp_path/'out'
    executar(p,out,rede=ConsultasOrcadas(2,0,Paginas()))
    antes=(out/'documents.jsonl').read_bytes()
    pagina=next((out/'paginas').glob('*/000001.json'))
    d=json.loads(pagina.read_text());d['payload']['data'][0]['objetoContrato']='alterado';pagina.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='cache'):
        executar(p,out,rede=ConsultasOrcadas(1,0,Paginas()),retomar=True)
    assert (out/'documents.jsonl').read_bytes()==antes
    assert (out/'falha_execucao.json').exists()
    assert not (out/'.coleta.lock').exists()


def test_plano_2026_nao_pode_virar_recorte_principal(tmp_path):
    p=plano(tmp_path);d=json.loads(p.read_text());d['politica']['ano']=2026;p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='2025'):
        executar(p,tmp_path/'out',rede=ConsultasOrcadas(1,0,Paginas()))
    assert not (tmp_path/'out').exists()


def test_orgao_subrogado_e_aceito_sem_ignorar_publicacao_da_janela(tmp_path):
    p=plano(tmp_path);j=json.loads(p.read_text())['janelas_a_adquirir'][0]
    r=registro(1);r['orgaoEntidade']['cnpj']='22222222000100';r['orgaoSubRogado']={'cnpj': CNPJ}
    assert _selecionar(r,j,PoliticaColeta())[0]
    r['dataPublicacaoPncp']='2025-01-01'
    assert _selecionar(r,j,PoliticaColeta())==(False,'publicacao_fora_da_janela_consultada')


def test_erro_http_registrado_sem_abortar_outros(tmp_path):
    class Cliente:
        def request_url(self,url,*args,**kwargs):
            if parse_qs(urlparse(url).query)['dataInicial']==['20260101']:
                return {'status':'erro_requisicao','http_status':404,'erro':'Not Found'}
            return {'http_status':204,'observacao':'resposta sem conteúdo'}
    r=executar(plano(tmp_path,2),tmp_path/'out',rede=ConsultasOrcadas(2,0,Cliente()))
    assert r['janelas_por_status']=={'falhou':1,'completa':1}
    assert not r['aquisicao_plano_completa']


def test_deadline_nao_espera_cooldown_fora_do_lote(tmp_path):
    import time
    client=Paginas();rede=ConsultasOrcadas(1,0,client);rede.proxima=time.monotonic()+60
    r=executar(plano(tmp_path),tmp_path/'out',rede=rede,max_segundos=1)
    assert r['consultas_http_esta_execucao']==0
    assert r['janelas_nao_iniciadas']==1
    assert client.calls==[]


def test_falha_antiga_nao_impede_processar_nova_janela(tmp_path):
    class Cliente:
        def __init__(self):self.calls=[]
        def request_url(self,url,*args,**kwargs):
            self.calls.append(url)
            if parse_qs(urlparse(url).query)['dataInicial']==['20260101']:
                return {'status':'erro_requisicao','http_status':404,'erro':'Not Found'}
            return {'http_status':204,'observacao':'resposta sem conteúdo'}
    p=plano(tmp_path,2);client=Cliente();out=tmp_path/'out'
    executar(p,out,rede=ConsultasOrcadas(1,0,client))
    r=executar(p,out,rede=ConsultasOrcadas(1,0,client),retomar=True)
    assert len(client.calls)==2
    assert parse_qs(urlparse(client.calls[1]).query)['dataInicial']==['20260201']
    assert r['janelas_por_status']=={'falhou':1,'completa':1}


def test_tempo_invalido_nao_cria_destino(tmp_path):
    with pytest.raises(ValueError,match='tempo'):
        executar(plano(tmp_path),tmp_path/'out',rede=ConsultasOrcadas(1,0,Paginas()),max_segundos=-1)
    assert not (tmp_path/'out').exists()


def test_congelar_export_e_independente_da_retomada(tmp_path):
    from src.supplement_contracts_v7 import congelar_exportacao
    p=plano(tmp_path);out=tmp_path/'out'
    executar(p,out,rede=ConsultasOrcadas(2,0,Paginas()))
    snapshot=tmp_path/'snapshot';r=congelar_exportacao(out,snapshot)
    assert r['ocorrencias_exportadas']==2
    antes=(snapshot/'documents.jsonl').read_bytes()
    (out/'documents.jsonl').write_bytes(b'alteracao posterior')
    assert (snapshot/'documents.jsonl').read_bytes()==antes
    (out/'.coleta.lock').write_text('trava')
    with pytest.raises(ValueError,match='trava'):
        congelar_exportacao(out,tmp_path/'outro')


def test_estado_de_outro_plano_nao_e_aceito(tmp_path):
    p=plano(tmp_path);out=tmp_path/'out'
    executar(p,out,rede=ConsultasOrcadas(1,0,Paginas()))
    estado=out/'estado.json';d=json.loads(estado.read_text());d['fingerprint']='outro';estado.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='estado incompatível'):
        executar(p,out,rede=ConsultasOrcadas(1,0,Paginas()),retomar=True)
    assert not (out/'.coleta.lock').exists()
