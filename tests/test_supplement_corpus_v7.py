import json
import pytest
from src.supplement_corpus_v7 import ConsultasOrcadas, compras_pendentes, unidades_oficiais
from tests.test_recover_corpus_v7 import CNPJ, chunk, salvar
from src.recover_corpus_v7 import construir_staging
from src.providers.pncp import PNCPProvider


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
    def request_url(self, url, kind, **kwargs):
        self.calls.append(url)
        return {"status": "disponivel", "http_status": 200, "json": self.payload}
    def api_url(self, path):
        return "https://pncp.test" + path


def test_orcamento_nao_faz_rede_extra():
    client = FakeClient({})
    rede = ConsultasOrcadas(1, 0, client)
    rede.get("https://teste")
    with pytest.raises(RuntimeError, match="orçamento"):
        rede.get("https://teste")
    assert len(client.calls) == 1


def test_compra_auxiliar_enriquece_sem_aumentar_universo(tmp_path):
    compra = f"{CNPJ}-1-000001/2024"
    fila = tmp_path / "fila.jsonl"
    fila.write_text(json.dumps({"compra": compra}) + "\n")
    rede = ConsultasOrcadas(1, 0, FakeClient({"numeroControlePNCP": compra, "modalidadeId": 6, "modalidadeNome": "Pregão - Eletrônico", "dataPublicacaoPncp": "2024-01-01"}))
    aux = tmp_path / "aux"
    assert compras_pendentes(fila, aux, rede=rede)["compras_resolvidas"] == 1
    source = salvar(tmp_path / "input.jsonl", [chunk(f"{CNPJ}-2-000001/2025", numeroControlePncpCompra=compra)])
    r = construir_staging([source, aux / "documents.jsonl"], tmp_path / "stage")
    assert r["contagens"]["compras_pendentes_unicas"] == 0
    assert r["contagens"]["contrato_assinado"] == 1
    assert r["contagens"].get("publicacao_compra", 0) == 0
    assert r["contagens"]["referencia_auxiliar_nao_contabilizada"] == 1


def test_resposta_de_outra_compra_nao_vira_evidencia(tmp_path):
    fila = tmp_path / "fila.jsonl"
    fila.write_text(json.dumps({"compra": f"{CNPJ}-1-000001/2024"}) + "\n")
    r = compras_pendentes(fila, tmp_path / "aux", rede=ConsultasOrcadas(1, 0, FakeClient({"numeroControlePNCP": "outra", "modalidadeNome": "Dispensa"})))
    assert r["falhas"] == 1
    assert r["compras_resolvidas"] == 0


def test_cadastro_parcial_nao_vira_completo(tmp_path):
    f = tmp_path / "observadas.json"
    f.write_text(json.dumps({"unidades": [{"cnpj": "1"}, {"cnpj": "2"}]}))
    r = unidades_oficiais(f, tmp_path / "unidades", rede=ConsultasOrcadas(1, 0, FakeClient([{"codigoUnidade": "9", "municipio": {"uf": {"siglaUF": "RJ"}}}])))
    assert r["verificacao_completa"] is False
    assert len(r["verified_unit_scopes"]) == 1
    assert r["cobertura_institucional_comprovada"] is False


def test_cache_cadastral_preserva_data_e_continua_apos_orcamento(tmp_path):
    f = tmp_path / "observadas.json"
    f.write_text(json.dumps({"orgaos": ["1", "2"], "unidades": []}))
    cache = tmp_path / "cache"; cache.mkdir()
    url = "https://pncp.gov.br/api/pncp/v1/orgaos/2/unidades"
    (cache / "2.json").write_text(json.dumps({"origem": url, "coletado_em": "2026-10-15T12:00:00", "unidades": []}))
    dest = tmp_path / "unidades"
    r = unidades_oficiais(f, dest, rede=ConsultasOrcadas(1, 0, FakeClient([])), cache=cache)
    assert len(r["verificacoes_orgaos"]) == 2
    assert r["consultas_http"] == 1
    assert r["verificacoes_orgaos"]["2"]["cache"] is True
    assert json.loads((dest / "2.json").read_text())["coletado_em"] == "2026-10-15T12:00:00"
    assert (dest / "cadastro_parcial.json").exists()
    assert not list(dest.glob("*.provisorio"))


def test_roster_vazio_nao_e_certificado(tmp_path):
    f = tmp_path / "vazio.json"; f.write_text(json.dumps({"orgaos": [], "unidades": []}))
    with pytest.raises(ValueError, match="vazio"):
        unidades_oficiais(f, tmp_path / "unidades", rede=ConsultasOrcadas(1, 0, FakeClient([])))


def test_host_cooldown_respeita_retry_after(monkeypatch):
    class Limitado:
        def request_url(self, *args, **kwargs):
            return {"http_status": 429, "retry_after": "120"}
    monkeypatch.setattr("src.supplement_corpus_v7.time.monotonic", lambda: 10.0)
    monkeypatch.setattr("src.supplement_corpus_v7.time.sleep", lambda _: None)
    rede = ConsultasOrcadas(1, 0, Limitado())
    rede.get("https://teste")
    assert rede.proxima == 130.0


def test_provider_nao_restringe_cadastro_a_unidades_ja_observadas():
    client = FakeClient([{"codigoUnidade": "1", "ufSigla": "RJ"}, {"codigoUnidade": "2", "ufSigla": "RJ"}, {"codigoUnidade": "3", "ufSigla": "DF"}])
    state = {"cnpjs_derivados": [CNPJ], "unidades_observadas_por_cnpj": {CNPJ: {"1": {}}}}
    scopes = PNCPProvider(client).verify_discovered_units(state)
    assert [u["codigoUnidadeAdministrativa"] for u in scopes] == ["1", "2"]
    assert state["unidades_observadas_confirmadas"] == 1
