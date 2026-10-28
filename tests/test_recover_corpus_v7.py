import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from src.recover_corpus_v7 import construir_staging, destino_novo, url_compra

CNPJ = "11111111000100"


def chunk(numero, **kwargs):
    raw = {"numeroControlePNCP": numero, "dataAssinatura": "2025-05-01", "dataPublicacaoPncp": "2025-05-02T10:00:00", "dataAtualizacao": "2025-05-02", "unidadeOrgao": {"ufSigla": "RJ", "codigoUnidade": "1"}, "orgaoEntidade": {"cnpj": CNPJ}, "tipoContrato": {"id": 1, "nome": "Contrato (termo inicial)"}, "objetoContrato": "Objeto", "valorGlobal": 100, **kwargs}
    text = json.dumps(raw, sort_keys=True)
    sha = hashlib.sha256(text.encode()).hexdigest()
    return {"chunk_id": "chunk-"+sha, "document_id": numero, "base_id": "contratos_estruturado", "doc_type": "json", "natureza": "estruturada", "text": text, "sha256": sha, "source_file": "https://pncp.test/registro", "origem": "https://pncp.test/registro", "coletado_em": "2026-10-15T00:00:00", "extras": {}}


def salvar(path, chunks):
    path.write_text("".join(json.dumps(c) + "\n" for c in chunks), encoding="utf-8")
    return path


def test_staging_preserva_texto_deriva_so_extras_e_separa_instrumentos(tmp_path):
    compra_id = f"{CNPJ}-1-000001/2024"
    compra = chunk(compra_id, dataPublicacaoPncp="2024-01-01", modalidadeId=6, modalidadeNome="Pregão - Eletrônico")
    contrato = chunk(f"{CNPJ}-2-000001/2025", numeroControlePncpCompra=compra_id, categoriaProcesso={"id": 2, "nome": "Compras"})
    empenho = chunk(f"{CNPJ}-2-000002/2025", tipoContrato={"id": 2, "nome": "Empenho"}, numeroControlePncpCompra=compra_id)
    fora = chunk(f"{CNPJ}-2-000003/2025", unidadeOrgao={"ufSigla": "DF", "codigoUnidade": "2"})
    source = salvar(tmp_path / "input.jsonl", [compra, contrato, empenho, fora])
    antes = source.read_bytes()
    dest = tmp_path / "out"
    r = construir_staging([source], dest)
    assert source.read_bytes() == antes
    assert r["contagens"]["contrato_assinado"] == 1
    assert r["contagens"]["empenho"] == 1
    assert r["contagens"]["unidade_fora_recorte"] == 1
    assert r["promocao_autorizada"] is False
    assert not r["indice_construido"]
    with sqlite3.connect(dest / "base_estruturada.sqlite") as db:
        assert db.execute("SELECT tipo_registro,modalidade_origem,categoria_processo_nome FROM contratacoes ORDER BY numero_controle_pncp").fetchall() == [("contrato_assinado", "compra_vinculada", "Compras"), ("empenho", "compra_vinculada", None)]
    saida = [json.loads(l) for l in (dest / "documents.jsonl").read_text().splitlines()]
    assert saida[0]["text"] == contrato["text"]
    assert saida[0]["chunk_id"] == contrato["chunk_id"]
    assert saida[0]["extras"]["modalidade"] == "Pregão - Eletrônico"
    assert json.loads(saida[0]["text"]).get("modalidadeNome") is None


def test_referencia_pendente_unica_mesmo_com_varios_instrumentos(tmp_path):
    compra = f"{CNPJ}-1-000001/2023"
    source = salvar(tmp_path / "input.jsonl", [chunk(f"{CNPJ}-2-00000{i}/2025", numeroControlePncpCompra=compra) for i in [1, 2]])
    r = construir_staging([source], tmp_path / "out")
    assert r["contagens"]["compras_pendentes_unicas"] == 1
    assert r["contagens"]["registros_com_vinculo_pendente"] == 2


def test_drift_escolhe_revisao_sem_duplicar(tmp_path):
    id_ = f"{CNPJ}-2-000001/2025"
    a = chunk(id_, dataAtualizacao="2025-01-01", valorGlobal=50)
    b = chunk(id_, dataAtualizacao="2025-06-01", valorGlobal=100)
    source = salvar(tmp_path / "input.jsonl", [b, a, b])
    r = construir_staging([source], tmp_path / "out")
    assert r["contagens"]["contrato_assinado"] == 1
    with sqlite3.connect(tmp_path / "out/base_estruturada.sqlite") as db:
        assert db.execute("SELECT valor_contratado FROM contratacoes").fetchone()[0] == 100


def test_destino_existente_e_fontes_protegidos(tmp_path):
    source = salvar(tmp_path / "input.jsonl", [])
    with pytest.raises(ValueError):
        construir_staging([source], tmp_path)
    out = tmp_path / "out"; out.mkdir()
    with pytest.raises(FileExistsError):
        construir_staging([source], out)
    with pytest.raises(ValueError):
        destino_novo(Path("data/v7_direto/sobrepor"))


def test_resolve_ata_por_unidade_sem_inferir_nome(tmp_path):
    original = chunk(f"{CNPJ}-2-000001/2025")
    ata = chunk("ignorado", numeroControlePNCPAta="ata-1", numeroControlePNCP=None, anoAta=2025, cnpjOrgao=CNPJ, codigoUnidadeOrgao="1", unidadeOrgao=None, orgaoEntidade=None)
    ata["base_id"] = "atas_conversacional"
    source = salvar(tmp_path / "input.jsonl", [original, ata])
    assert construir_staging([source], tmp_path / "out")["contagens"]["ata"] == 1


def test_alvos_preservam_orgaos_sem_unidade_no_snapshot(tmp_path):
    from src.recover_corpus_v7 import preparar_alvos
    observadas = tmp_path / "observadas.json"
    observadas.write_text(json.dumps({"orgaos": [CNPJ], "unidades": []}))
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(["22222222000100"]))
    r = preparar_alvos(observadas, [roster], tmp_path / "alvos")
    assert r["orgaos"] == [CNPJ, "22222222000100"]
    assert r["completo"] is False


def test_auxiliar_mesmo_ano_nao_conta_como_publicacao_principal(tmp_path):
    c = chunk(f"{CNPJ}-1-000001/2025", modalidadeNome="Dispensa")
    c["extras"]["papel"] = "referencia_auxiliar"
    source = salvar(tmp_path / "aux.jsonl", [c])
    r = construir_staging([source], tmp_path / "out")
    assert r["contagens"].get("publicacao_compra", 0) == 0
    assert r["contagens"]["referencia_auxiliar_nao_contabilizada"] == 1


def test_carga_sql_respeita_classificacao_explicita_sem_alterar_legado():
    from src.structured_db import _linha_contratacao
    c = chunk(f"{CNPJ}-2-000001/2025", tipoContrato={"nome": "Empenho"})
    payload = json.loads(c["text"])
    assert _linha_contratacao(c, payload)["tipo_registro"] == "contrato_assinado"
    c["extras"]["tipo_instrumento"] = "empenho"
    assert _linha_contratacao(c, payload)["tipo_registro"] == "empenho"


def test_laudo_oficial_resolve_ata_sem_uf_sem_inferir_nome(tmp_path):
    ata=chunk('ignorado',numeroControlePNCPAta='ata-1',numeroControlePNCP=None,anoAta=2025,cnpjOrgao=CNPJ,codigoUnidadeOrgao='1',unidadeOrgao=None,orgaoEntidade=None)
    ata['base_id']='atas_conversacional'
    source=salvar(tmp_path/'input.jsonl',[ata])
    laudo=tmp_path/'laudo.jsonl'
    laudo.write_text(json.dumps({'ata':'ata-1','cnpj':CNPJ,'codigoUnidadeAdministrativa':'1','situacao':'confirmada_RJ','uf_cadastral':'RJ','evidencia_temporal':'cadastro atual'})+'\n')
    r=construir_staging([source],tmp_path/'out',territorio_atas=laudo)
    assert r['contagens']['ata']==1
    c=json.loads((tmp_path/'out/documents.jsonl').read_text())
    assert c['text']==ata['text']
    assert c['extras']['territorio_origem']=='cadastro_oficial_atual'
    assert not r['territorio_atas_validade_historica_comprovada']


def test_laudo_ausente_para_ata_nao_cai_em_fallback_local(tmp_path):
    ata=chunk('ignorado',numeroControlePNCPAta='ata-1',numeroControlePNCP=None,anoAta=2025,cnpjOrgao=CNPJ,codigoUnidadeOrgao='1',unidadeOrgao=None,orgaoEntidade=None)
    ata['base_id']='atas_conversacional'
    source=salvar(tmp_path/'input.jsonl',[chunk(f'{CNPJ}-2-000001/2025'),ata])
    laudo=tmp_path/'laudo.jsonl';laudo.write_text('')
    r=construir_staging([source],tmp_path/'out',territorio_atas=laudo)
    assert r['contagens'].get('ata',0)==0
    assert r['contagens']['territorio_pendente_ou_laudo_divergente']==1


def test_url_compra_valida_e_nao_permite_outro_recurso():
    assert url_compra(f"{CNPJ}-1-000283/2024").endswith(f"/{CNPJ}/compras/2024/283")
    with pytest.raises(ValueError):
        url_compra(f"{CNPJ}-2-000283/2024")
