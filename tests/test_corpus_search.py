from src.corpus_search import search_inventory


def test_search_inventory_matches_metadata_and_filters_base():
    inventory = {
        "version": "v3",
        "arquivos": [
            {
                "status": "ok",
                "logical_id": "documentos_pncp:edital:90012",
                "base_id": "documentos_pncp",
                "tipo": "pdf",
                "path": "data/raw/v3/documentos_pncp/edital_90012.pdf",
                "origem": "https://pncp.test/90012",
                "sha256": "abc",
            },
            {
                "status": "ok",
                "logical_id": "fontes_normativas:lei",
                "base_id": "fontes_normativas",
                "tipo": "pdf",
                "path": "data/raw/v3/fontes_normativas/lei.pdf",
                "origem": "https://planalto.test/lei",
                "sha256": "def",
            },
        ],
    }

    result = search_inventory(
        inventory=inventory,
        query="90012",
        base_id="documentos_pncp",
    )

    assert result["version"] == "v3"
    assert result["total_matches"] == 1
    assert result["matches"][0]["logical_id"] == "documentos_pncp:edital:90012"
    assert "logical_id" in result["matches"][0]["match_fields"]


def test_search_inventory_can_list_all_items_without_query():
    inventory = {"version": "v1", "arquivos": [{"status": "ok", "logical_id": "a"}, {"status": "falhou", "logical_id": "b"}]}

    result = search_inventory(inventory=inventory, query="", limit=10)

    assert result["total_matches"] == 1
    assert result["matches"][0]["logical_id"] == "a"
