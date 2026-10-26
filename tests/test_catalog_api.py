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
    monkeypatch.setattr(catalog, "STRUCTURED_FILE", tmp_path / "structured.sqlite")
    monkeypatch.setattr(catalog, "INDEX_MANIFEST", tmp_path / "index-manifest.json")
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


def test_compact_snapshot_keeps_every_contract_in_a_batch_file(local_catalog):
    # Uma página da coleta PNCP pode conter muitos contratos distintos.
    with local_catalog.open("a", encoding="utf-8") as handle:
        for number in range(50):
            row = _chunk("contratos_estruturado", f"batch-{number}", json.dumps({
                "numeroControlePNCP": f"batch-{number:03d}", "valorGlobal": number,
                "objetoContrato": f"Objeto {number}",
            }), {"orgao": "Órgão do lote", "orgao_cnpj": "11222333000181"})
            row["source_file"] = "raw/mesma-pagina-pncp.json"
            handle.write(json.dumps(row) + "\n")
    records = catalog.contracts(limit=100)
    assert len(records) == 52
    batch = [record for record in records if record["id"].startswith("batch-")]
    assert len(batch) == 50 and sum(record["valor_global"] for record in batch) == 1225
    assert catalog.contracts(busca="Objeto 49")[0]["id"] == "batch-049"
    assert len(catalog.contracts(limit=24, offset=24)) == 24
    org = catalog.organizations(busca="Órgão do lote")[0]
    assert org["total_contratacoes"] == 50 and org["valor_total"] == 1225
    _, _, rows = catalog._snapshot()
    assert all("text" not in row for row in rows)


def test_contracts_mask_only_the_requested_page(local_catalog, monkeypatch):
    with local_catalog.open("a", encoding="utf-8") as handle:
        for number in range(60):
            handle.write(json.dumps(_chunk("contratos_estruturado", f"c{number}", json.dumps({
                "numeroControlePNCP": f"c{number:03d}", "objetoContrato": "CPF 120.456.789-28",
            }))) + "\n")
    masked_ids = []
    original = catalog._safe

    def spy(value):
        if isinstance(value, dict) and "id" in value:
            masked_ids.append(value["id"])
        return original(value)

    monkeypatch.setattr(catalog, "_safe", spy)
    result = catalog.contracts(limit=3, offset=2)
    assert len(result) == len(masked_ids) == 3
    assert all("120.456.789-28" not in json.dumps(row) for row in result)
    assert all("[CPF omitido]" in row["objeto"] for row in result)


def test_concurrent_catalog_requests_load_documents_only_once(local_catalog, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(6)
    reads = []
    original = Path.open

    def spy(path, *args, **kwargs):
        if path == local_catalog:
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)

    def request(_):
        barrier.wait(timeout=10)
        return catalog.contracts()

    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = list(pool.map(request, range(6)))
    assert all(len(page) == 2 for page in pages)
    assert len(reads) == 1


def test_sources_need_no_documents_and_inventory_reads_stop_at_latest_complete(local_catalog, monkeypatch):
    catalog._load_metadata.cache_clear()
    reads = []
    original = catalog._read_json

    def spy(path):
        reads.append(path.name)
        return original(path)

    monkeypatch.setattr(catalog, "_read_json", spy)
    # Um inventário antigo inválido não deve ser lido se v6 já é completo.
    (catalog.INVENTORY_DIR / "v5.json").write_text("invalid", encoding="utf-8")
    local_catalog.unlink()
    assert len(catalog.sources()) == 5
    assert reads == ["v7.json", "v6.json"]


def test_compact_snapshot_prefers_public_chunk_but_never_exposes_restricted(local_catalog):
    with local_catalog.open("a", encoding="utf-8") as handle:
        for sensitivity, title in [("restrito", "Segredo"), ("publico", "Visível"),
                                   ("publico", "Página duplicada"), ("restrito", "Outro segredo")]:
            handle.write(json.dumps(_chunk("emails_sinteticos", "mixed", extras={"title": title},
                                           sensitivity=sensitivity)) + "\n")
    records = catalog.documents(tipo="Conversa sintética", busca="Visível")
    assert len(records) == 1 and records[0]["titulo"] == "[Sintético] Visível"
    assert not catalog.documents(busca="Segredo")
    assert not catalog.documents(busca="Página duplicada")


def _active_metadata_fixture(local_catalog, *, matching=True, nested_provenance=False):
    import hashlib
    import sqlite3

    source_hash = hashlib.sha256(local_catalog.read_bytes()).hexdigest() if matching else "divergente"
    with sqlite3.connect(catalog.STRUCTURED_FILE) as conn:
        conn.execute("CREATE TABLE meta (chave TEXT, valor TEXT)")
        conn.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("corpus_version", "v7-reparo"), ("source_sha256", source_hash),
            ("built_at", "2026-10-18T21:14:05+00:00"), ("schema_version", "7"),
        ])
    index = {
        "source_sha256": source_hash, "snapshot_id": "candidato_teste", "total_chunks": 9,
        "total_vectors": 8, "por_base": {"contratos_estruturado": {"candidato": 3, "recuperacao": 2}},
        "homogeneidade_modelo_comprovada": False,
        "proveniencia_encoding_completa": False, "promocao_autorizada": False,
    }
    if nested_provenance:
        index.update({
            "built_at": "2026-10-26T17:45:00+00:00",
            "snapshot_id": "candidato_08_vetores_completos",
            "total_vectors": 9,
            "por_base": None,
            "proveniencia_vetores": {"por_base": {
                "contratos_estruturado": {"candidato": 3, "recuperacao": 2},
                "atas_conversacional": {"complemento_q8_local": 4},
            }},
        })
    catalog.INDEX_MANIFEST.write_text(json.dumps(index), encoding="utf-8")


def test_summary_separates_verified_active_v7_from_historical_v6(local_catalog):
    _active_metadata_fixture(local_catalog)
    body = client.get("/api/v1/project/summary").json()
    assert sum(base["vetores_ativos"] or 0 for base in body["bases"]) == 5
    assert body["versao_corpus"] == "v7-reparo"
    assert body["versao_inventario"] == "v6 + suplemento sintético"
    active = body["corpus_ativo"]
    assert active["versao"] == "v7-reparo" and active["snapshot_id"] == "candidato_teste"
    assert active["total_chunks"] == 9 and active["total_documentos"] == 7
    assert active["total_vetores"] == 8 and active["chunks_sem_vetor"] == 1
    assert active["schema_sql"] == "7" and active["bytes_documents"] == local_catalog.stat().st_size
    assert active["vetores_por_base"] == {"contratos_estruturado": 5}
    assert body["snapshot_anterior"]["versao"] == "v6 + suplemento sintético"
    assert body["snapshot_anterior"]["arquivos"] == 5
    assert any("mista" in warning for warning in active["avisos"])
    assert any("parcial" in warning for warning in active["avisos"])
    assert any("produção" in warning for warning in active["avisos"])
    assert body["total_documentos"] == 7  # contrato antigo preservado
    assert sum(base["chunks_ativos"] for base in body["bases"]) == 9
    assert sum(base["documentos_ativos"] for base in body["bases"]) == 7
    assert str(catalog.STRUCTURED_FILE) not in json.dumps(body)


def test_summary_reads_nested_candidate_vector_counts_without_dropping_readiness_warnings(local_catalog):
    _active_metadata_fixture(local_catalog, nested_provenance=True)
    body = client.get("/api/v1/project/summary").json()
    active = body["corpus_ativo"]
    assert active["snapshot_id"] == "candidato_08_vetores_completos"
    assert active["construido_em"] == "2026-10-26T17:45:00+00:00"
    assert active["total_chunks"] == active["total_vetores"] == 9
    assert active["chunks_sem_vetor"] == 0
    assert active["vetores_por_base"] == {"contratos_estruturado": 5, "atas_conversacional": 4}
    assert sum(base["vetores_ativos"] or 0 for base in body["bases"]) == 9
    assert not any("cobertura da busca vetorial é parcial" in warning for warning in active["avisos"])
    assert any("mista" in warning for warning in active["avisos"])
    assert any("proveniência de encoding" in warning for warning in active["avisos"])
    assert any("produção" in warning for warning in active["avisos"])


def test_summary_does_not_claim_v7_when_source_hashes_differ(local_catalog):
    _active_metadata_fixture(local_catalog, matching=False)
    active = catalog.summary()["corpus_ativo"]
    assert active["versao"] is None and active["snapshot_id"] is None
    assert active["total_vetores"] is None and active["chunks_sem_vetor"] is None
    assert sum("não corresponde" in warning for warning in active["avisos"]) == 2


def test_summary_cache_invalidates_when_active_index_metadata_changes(local_catalog):
    _active_metadata_fixture(local_catalog)
    assert catalog.summary()["corpus_ativo"]["total_vetores"] == 8
    value = json.loads(catalog.INDEX_MANIFEST.read_text(encoding="utf-8"))
    value["total_vectors"] = 7
    catalog.INDEX_MANIFEST.write_text(json.dumps(value), encoding="utf-8")
    assert catalog.summary()["corpus_ativo"]["total_vetores"] == 7


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
