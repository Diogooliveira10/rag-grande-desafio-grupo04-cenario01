import json
from pathlib import Path
import pytest

from src.audit_collection_v7 import auditar, planejar
from src.index import file_sha256
from tests.test_recover_corpus_v7 import CNPJ, chunk, salvar

OUTRO = "22222222000100"


def preparar(tmp_path, chunks, janelas):
    for c in chunks:
        c['chunk_content_sha256'] = c['document_content_sha256'] = c['sha256']
        c['chunking_strategy'] = 'registro_inteiro'
        c['origem'] = f'https://pncp.gov.br/api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20251231&cnpjOrgao={CNPJ}&tamanhoPagina=500&pagina=1'
    docs = salvar(tmp_path / 'input.jsonl', chunks)
    estado = tmp_path / 'estado.json'; estado.write_text(json.dumps({'janelas': janelas}))
    base = tmp_path / 'baseline'; base.mkdir()
    (base / 'baseline.json').write_text(json.dumps({'root': str(tmp_path.resolve()), 'adquiridos': [{'path': p.name, 'sha256': file_sha256(p)} for p in [docs, estado]]}))
    return docs, estado, base


def test_audita_hash_e_paginacao_sem_recoletar(tmp_path):
    docs, estado, base = preparar(tmp_path, [chunk(f'{CNPJ}-2-000001/2025')], {f'contrato_assinado:{CNPJ}': {'status': 'completa', 'registros': 1, 'paginas': 1}})
    r = auditar(docs, estado, tmp_path / 'out', baseline=base)
    assert r['janelas_por_status'] == {'reaproveitavel': 1}
    assert r['baseline_conferida']
    assert not r['promocao_autorizada']


def test_dedupe_principal_subrogado_explicita_multiplicidade(tmp_path):
    c = chunk(f'{CNPJ}-2-000001/2025', orgaoSubRogado={'cnpj': OUTRO})
    estados = {f'contrato_assinado:{id_}': {'status': 'completa', 'registros': 1, 'paginas': 1} for id_ in [CNPJ, OUTRO]}
    docs, estado, base = preparar(tmp_path, [c], estados)
    r = auditar(docs, estado, tmp_path / 'out', baseline=base)
    assert r['contratos_empenhos_distintos_retidos'] == 1
    assert r['registros_estado'] == 2
    assert r['janelas_por_status'] == {'reaproveitavel': 1, 'reaproveitavel_com_dedupe_crossrole': 1}


def test_fonte_modificada_recusa_reuso_antes_de_criar_destino(tmp_path):
    docs, estado, base = preparar(tmp_path, [], {f'contrato_assinado:{CNPJ}': {'status': 'completa', 'registros': 0, 'paginas': 1}})
    docs.write_text('\n')
    with pytest.raises(ValueError, match='baseline'):
        auditar(docs, estado, tmp_path / 'out', baseline=base)
    assert not (tmp_path / 'out').exists()


def test_contagem_nao_explicada_precisa_revalidacao(tmp_path):
    docs, estado, base = preparar(tmp_path, [chunk(f'{CNPJ}-2-000001/2025')], {f'contrato_assinado:{CNPJ}': {'status': 'completa', 'registros': 2, 'paginas': 1}})
    r = auditar(docs, estado, tmp_path / 'out', baseline=base)
    assert r['janelas_por_status'] == {'revalidacao_necessaria': 1}


def test_plano_agrupa_cnpj_reusa_so_2025_e_mantem_assinatura2025(tmp_path):
    docs, estado, base = preparar(tmp_path, [], {f'contrato_assinado:{CNPJ}': {'status': 'completa', 'registros': 0, 'paginas': 1}})
    auditar(docs, estado, tmp_path / 'audit', baseline=base)
    cad = tmp_path / 'cad.json'
    cad.write_text(json.dumps({'verificacao_completa': True, 'verified_unit_scopes': [{'cnpj': CNPJ, 'codigoUnidadeAdministrativa': str(i), 'ufSigla': 'RJ'} for i in [1, 2]]}))
    r = planejar(cad, tmp_path / 'audit/auditoria.json', tmp_path / 'plan')
    assert r['unidades_RJ'] == 2
    assert len(r['janelas_reaproveitadas']) == 1
    assert len(r['janelas_a_adquirir']) == 1
    assert r['politica']['ano'] == 2025
    assert r['janelas_a_adquirir'][0]['params']['dataInicial'] == '20260101'
    assert r['janelas_a_adquirir'][0]['params']['dataFinal'] == '20260925'
