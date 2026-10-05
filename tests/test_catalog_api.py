import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import catalog
from app.api import app
from src.router import rotear_pergunta
from src.schema import Natureza

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def _chunk(base, key, text="", extras=None, sensitivity="publico"):
    return {"base_id": base, "sha256": key, "source_file": f"local/{key}.json",
            "chunk_id": f"{base}:{key}", "logical_id": f"{base}:{key}", "text": text,
            "formato": "json", "sensitivity": sensitivity, "extras": extras or {}}


@pytest.fixture(autouse=True)
def local_catalog(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text((ROOT / "corpus_manifest.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    inventory_dir = tmp_path / "inventory"
    inventory_dir.mkdir()
    counts = {"contratos_estruturado": 2, "editais_normativo": 1,
              "atas_conversacional": 1, "pca_agregado": 1}
    files = [{"base_id": base, "sha256": key, "path": f"raw/{key}", "logical_id": key,
              "tipo": "json", "status": "ok", "origem": "https://pncp.gov.br/example"}
             for base, keys in [("contratos_estruturado", ["a", "b"]), ("editais_normativo", ["n"]),
                                 ("atas_conversacional", ["ata"]), ("pca_agregado", ["pca"])] for key in keys]
    inventory = {"official": True, "completo": True, "version": "v6", "coletado_em": "2026-09-30T00:00:00Z",
                 "totais": {"arquivos": 5, "itens": 5, "falhas": 0}, "arquivos": files,
                 "por_base": [{"base_id": base, "arquivos": count, "itens": count,
                               "formatos": {"json": count}, "status": "complete"} for base, count in counts.items()]}
    (inventory_dir / "v6.json").write_text(json.dumps(inventory), encoding="utf-8")
    (inventory_dir / "v7.json").write_text(json.dumps({"official": True, "completo": False}), encoding="utf-8")
    contract = {"numeroControlePNCP": "123-2-000001/2025", "numeroContratoEmpenho": "14",
                "valorGlobal": 100.5, "nomeRazaoSocialFornecedor": "Fornecedor A",
                "objetoContrato": "Licenciamento de software", "dataAssinatura": "2025-01-01"}
    extras = {"orgao": "Câmara de Caxias", "orgao_cnpj": "30381552000158",
              "municipio": "Duque de Caxias", "esfera": "municipal"}
    rows = [_chunk("contratos_estruturado", "a", json.dumps(contract), extras),
            _chunk("contratos_estruturado", "a", json.dumps(contract), extras),
            _chunk("contratos_estruturado", "b", json.dumps({"numeroControlePNCP": "456-1-1/2025",
                "valorTotalEstimado": 999, "objetoCompra": "Ambulâncias"}),
                {"orgao": "Secretaria de Saúde", "orgao_cnpj": "12345678000190"}),
            _chunk("editais_normativo", "n", extras={"titulo_documento": "Lei 14.133/2021"}),
            _chunk("editais_normativo", "n", extras={"titulo_documento": "Lei 14.133/2021"}),
            _chunk("atas_conversacional", "ata", extras={"objeto": "Ata papel A4"}),
            _chunk("pca_agregado", "pca", extras={"objeto": "Planejamento"}),
            _chunk("emails_sinteticos", "mail", extras={"titulo_documento": "Entrega papel A4 CPF 120.456.789-28",
                "orgao": "DPRJ", "creation_date": "2024-08-10", "sintetico": True}),
            _chunk("emails_sinteticos", "secret", "Senha: segredo", {"titulo_documento": "Credencial master"}, "restrito")]
    documents = tmp_path / "documents.jsonl"
    documents.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr(catalog, "DOCUMENTS_FILE", documents)
    monkeypatch.setattr(catalog, "INVENTORY_DIR", inventory_dir)
    monkeypatch.setattr(catalog, "MANIFEST_FILE", manifest)
    catalog._load_snapshot.cache_clear()
    yield documents
    catalog._load_snapshot.cache_clear()


def test_all_frontend_catalog_routes_respond_without_model_calls():
    for endpoint in ["contratacoes", "documentos", "orgaos", "fontes"]:
        response = client.get(f"/api/v1/explore/{endpoint}")
        assert response.status_code == 200
        assert isinstance(response.json(), list) and response.json()
    assert client.get("/api/v1/project/summary").status_code == 200


def test_contract_filters_pagination_and_unknown_values():
    body = client.get("/api/v1/explore/contratacoes", params={"busca": "SOFTWARE", "orgao": "camara"}).json()
    assert len(body) == 1 and body[0]["valor_global"] == 100.5
    assert body[0]["modalidade"] is None and body[0]["status"] is None
    assert body[0]["data_homologacao"] is None
    assert len(client.get("/api/v1/explore/contratacoes").json()) == 2
    page = client.get("/api/v1/explore/contratacoes", params={"limit": 1, "offset": 1}).json()
    assert len(page) == 1 and page[0]["valor_global"] is None  # estimado não é global
    assert client.get("/api/v1/explore/contratacoes", params={"busca": "inexistente"}).json() == []


def test_documents_deduplicate_pages_filter_mask_and_hide_restricted():
    result = client.get("/api/v1/explore/documentos").json()
    assert len(result) == 6  # cinco arquivos públicos reais + uma conversa pública
    assert len([record for record in result if record["tipo"] == "Normativo"]) == 1
    email = client.get("/api/v1/explore/documentos", params={"tipo": "Conversa sintética", "busca": "papel A4"}).json()
    assert len(email) == 1 and email[0]["base_id"] == "emails_sinteticos"
    assert "[Sintético]" in email[0]["titulo"] and "[CPF omitido]" in email[0]["titulo"]
    assert email[0]["ano"] == 2024 and email[0]["link"] is None
    assert "segredo" not in json.dumps(result) and "Credencial master" not in json.dumps(result)


def test_organizations_use_real_contracts_only_and_deduplicate():
    records = client.get("/api/v1/explore/orgaos", params={"busca": "caxias"}).json()
    assert len(records) == 1 and records[0]["total_contratacoes"] == 1
    assert records[0]["valor_total"] == 100.5
    assert records[0]["sigla"] is None and records[0]["principais_categorias"] == []
    assert records[0]["principais_fornecedores"] == ["Fornecedor A"]
    assert not any(record["nome"] == "DPRJ" for record in client.get("/api/v1/explore/orgaos").json())


def test_summary_counts_restricted_metadata_but_preserves_v6_identity():
    body = client.get("/api/v1/project/summary").json()
    assert body["versao_corpus"] == "v6 + suplemento sintético"
    assert body["total_documentos"] == 7 and body["total_itens"] == 7
    assert body["total_falhas"] == 0
    assert len(body["bases"]) == 5
    assert next(base for base in body["bases"] if base["base_id"] == "emails_sinteticos")["documentos"] == 2


def test_sources_distinguish_synthetic_content_without_local_links():
    body = client.get("/api/v1/explore/fontes").json()
    synthetic = next(source for source in body if source["id"] == "emails_sinteticos")
    assert synthetic["url"] is None and "fictícios" in synthetic["descricao"]
    normative = next(source for source in body if source["id"] == "editais_normativo")
    assert normative["formatos"] == ["html", "htm", "pdf"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"tipo": "inventado"}])
def test_catalog_rejects_invalid_pagination_and_types(params):
    assert client.get("/api/v1/explore/documentos", params=params).status_code == 422


def test_missing_acervo_returns_503_without_paths_or_mock_success(local_catalog):
    local_catalog.unlink()
    response = client.get("/api/v1/project/summary")
    assert response.status_code == 503
    assert str(local_catalog) not in response.text


def test_snapshot_cache_invalidates_when_active_documents_change(local_catalog):
    assert len(catalog.documents(tipo="Conversa sintética")) == 1
    with local_catalog.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_chunk("emails_sinteticos", "extra", extras={"title": "Outro e-mail"})) + "\n")
    assert len(catalog.documents(tipo="Conversa sintética")) == 2


@pytest.mark.parametrize("query", [
    'O que diz a conversa sintética "Entrega de resmas" de DPRJ?',
    "O que o email da DPRJ diz sobre papel A4?",
    "O que consta na ARP 14/2024?",
])
def test_synthetic_catalog_queries_route_to_conversational_evidence(query):
    assert Natureza.CONVERSACIONAL in rotear_pergunta(query).bases_selecionadas
