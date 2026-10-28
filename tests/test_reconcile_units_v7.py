import json
from pathlib import Path
import pytest

from src.reconcile_units_v7 import reconciliar
from src.recover_corpus_v7 import construir_staging
from tests.test_recover_corpus_v7 import CNPJ, chunk, salvar


def fontes(tmp_path, unidades, observadas):
    cadastro = tmp_path / "cadastro"; cadastro.mkdir()
    url = f"https://pncp.gov.br/api/pncp/v1/orgaos/{CNPJ}/unidades"
    (cadastro / f"{CNPJ}.json").write_text(json.dumps({"origem": url, "coletado_em": "2026-10-15T00:00:00", "unidades": unidades}))
    d = {"verificacoes_orgaos": {CNPJ: {"status": "verificado"}}, "verificacao_completa": False, "orgaos_alvo_observados": 2}
    p = cadastro / "cadastro_parcial.json"; p.write_text(json.dumps(d))
    obs = tmp_path / "observadas.json"; obs.write_text(json.dumps({"unidades": observadas}))
    return obs, p


def test_conflito_nao_vira_janela_e_unidade_nova_rj_entra_no_plano(tmp_path):
    unidades = [{"codigoUnidade": "1", "ufSigla": "DF"}, {"codigoUnidade": "2", "ufSigla": "RJ"}]
    obs, cadastro = fontes(tmp_path, unidades, [{"cnpj": CNPJ, "codigoUnidadeAdministrativa": "1", "ufs_observadas": ["RJ"]}])
    r = reconciliar(obs, cadastro, tmp_path / "out")
    assert r["unidades_observadas_por_situacao"] == {"conflito_snapshot_cadastro": 1}
    assert r["unidades_RJ_elegiveis"] == 1
    assert r["unidades_RJ_nao_observadas_no_snapshot"] == 1
    assert r["janelas_contratos"] == {"aquisicao_faltante": 2}
    assert r["promocao_autorizada"] is False


def test_resolve_ata_pendente_pelo_cadastro_sem_mudar_candidato(tmp_path):
    ata = chunk("ignorado", numeroControlePNCPAta="ata-1", numeroControlePNCP=None, anoAta=2025, cnpjOrgao=CNPJ, codigoUnidadeOrgao="1", unidadeOrgao=None, orgaoEntidade=None)
    ata["base_id"] = "atas_conversacional"
    source = salvar(tmp_path / "atas.jsonl", [ata])
    stage = tmp_path / "stage"
    construir_staging([source], stage)
    antes = (stage / "documents.jsonl").read_bytes()
    _, cadastro = fontes(tmp_path, [{"codigoUnidade": "1", "ufSigla": "RJ"}], [])
    r = reconciliar(stage / "unidades_observadas.json", cadastro, tmp_path / "out", catalogo_aquisicao=stage / "catalogo_aquisicao.sqlite")
    assert r["atas_por_situacao"] == {"confirmada_RJ": 1, "RJ_resolvida_sem_UF_no_snapshot": 1}
    assert r["unidades_RJ_nao_observadas_no_snapshot"] == 0
    assert r["unidades_RJ_sem_UF_no_snapshot"] == 1
    assert (stage / "documents.jsonl").read_bytes() == antes


def test_unidade_ausente_nao_prova_fora_rj_e_nao_inferir_nome(tmp_path):
    obs, cadastro = fontes(tmp_path, [], [{"cnpj": CNPJ, "codigoUnidadeAdministrativa": "9", "ufs_observadas": ["RJ"]}])
    r = reconciliar(obs, cadastro, tmp_path / "out")
    assert r["unidades_observadas_por_situacao"] == {"unidade_ausente_no_cadastro_atual": 1}
    assert r["unidades_RJ_elegiveis"] == 0


def test_checkpoint_antigo_somente_prioriza_auditoria_nao_da_baixa(tmp_path):
    obs, cadastro = fontes(tmp_path, [{"codigoUnidade": "1", "ufSigla": "RJ"}], [])
    estado = tmp_path / "estado.json"
    estado.write_text(json.dumps({"janelas": {f"contrato_assinado:{CNPJ}": {"status": "completa"}}}))
    r = reconciliar(obs, cadastro, tmp_path / "out", estado_contratos=estado)
    assert r["janelas_contratos"] == {"auditar_reaproveitamento_snapshot_2025": 1, "aquisicao_faltante": 1}
    plano = json.loads((tmp_path / "out/plano_janelas_contratos.json").read_text())
    assert all(not j["execucao_autorizada_por_este_arquivo"] for j in plano["janelas"])


def test_rejeita_cadastro_de_outro_orgao(tmp_path):
    obs, cadastro = fontes(tmp_path, [{"codigoUnidade": "1", "ufSigla": "RJ", "orgao": {"cnpj": "outro"}}], [])
    with pytest.raises(ValueError, match="outro órgão"):
        reconciliar(obs, cadastro, tmp_path / "out")
