import src.source_catalog as source_catalog
from src.source_lookup import lookup_pncp


def test_lookup_pncp_filters_records_and_lists_documents(monkeypatch):
    requests = []

    def fake_request(url, kind, *, attempts, timeout, headers=None):
        requests.append(url)
        if url.endswith("/arquivos"):
            return {
                "status": "disponivel",
                "json": [{"nomeArquivo": "edital.pdf", "url": "https://files.test/edital.pdf"}],
            }
        if "pagina=1" in url:
            return {
                "status": "disponivel",
                "json": {
                    "data": [
                        {
                            "objetoCompra": "Contratação de software de gestão",
                            "cnpjOrgao": "12345678000199",
                            "anoCompra": 2025,
                            "sequencialCompra": 12,
                            "numeroControlePNCP": "12345678000199-1-000012/2025",
                        },
                        {"objetoCompra": "Compra de material de escritório"},
                    ]
                },
            }
        return {"status": "disponivel", "json": {"data": []}}

    monkeypatch.setattr(source_catalog, "request_url", fake_request)
    monkeypatch.setattr(source_catalog, "make_consulta_url", lambda path, params: f"{path}?pagina={params['pagina']}")
    monkeypatch.setattr(source_catalog, "make_api_url", lambda path: f"https://pncp.test{path}")

    result = lookup_pncp(
        query="software",
        year=2025,
        uf="RJ",
        page_size=2,
        max_pages=2,
        rate_limit_seconds=0,
    )

    assert len(result["results"]) == 1
    match = result["results"][0]
    assert match["title"] == "Contratação de software de gestão"
    assert match["external_id"] == "12345678000199-1-000012/2025"
    assert match["document_urls"] == ["https://files.test/edital.pdf"]
    assert any(url.endswith("/arquivos") for url in requests)


def test_lookup_pncp_reports_remote_failure(monkeypatch):
    monkeypatch.setattr(
        source_catalog,
        "request_url",
        lambda *args, **kwargs: {"status": "erro_requisicao", "erro": "timeout"},
    )
    monkeypatch.setattr(source_catalog, "make_consulta_url", lambda path, params: path)

    result = lookup_pncp(query="edital", year=2025, uf="RJ", rate_limit_seconds=0)

    assert result["results"] == []
    assert result["errors"][0]["error"] == "timeout"
