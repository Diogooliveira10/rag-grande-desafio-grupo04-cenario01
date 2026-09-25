from types import SimpleNamespace

from src import fetch_corpus
from src.providers.base import ResolvedSource, SourceArtifact, SourceRef
from src.providers.pncp import PNCPProvider


def test_pncp_provider_resolves_stable_resource_references():
    provider = PNCPProvider()

    purchase = provider.resolve(
        SourceRef(
            "pncp",
            "contratacao",
            {"cnpj": "00394544000185", "ano": 2025, "sequencial": 1220},
        )
    )
    assert purchase.artifacts[0].url.endswith(
        "/api/consulta/v1/orgaos/00394544000185/compras/2025/1220"
    )
    assert len(purchase.artifacts) == 1
    assert purchase.artifacts[0].url.endswith("/compras/2025/1220")

    ata = provider.resolve(
        SourceRef(
            "pncp",
            "ata",
            {
                "cnpj": "42498733000148",
                "ano_compra": 2024,
                "sequencial_compra": 2138,
                "sequencial_ata": 2,
            },
        )
    )
    assert "/api/pncp/v1/orgaos/42498733000148/compras/2024/2138/atas/2" in ata.artifacts[0].url

    pca = provider.resolve(
        SourceRef(
            "pncp",
            "pca",
            {"cnpj": "42498600000171", "ano": 2025, "sequencial": 41},
        )
    )
    assert pca.artifacts[0].url.endswith("/pca/2025/41/consolidado")
    assert pca.artifacts[1].url.endswith("/pca/2025/csv")


def test_source_ref_is_deterministic_and_serializable():
    source = SourceRef("pncp", "pca", {"sequencial": 41, "ano": 2025, "cnpj": "42"})
    restored = SourceRef.from_dict(source.to_dict())

    assert restored == source
    assert restored.stable_id == "pncp:pca:ano=2025:cnpj=42:sequencial=41"


def test_pncp_provider_searches_and_sorts_contracts_and_atas():
    class FakeClient:
        consulta_base = "https://pncp.test/api/consulta"
        api_base = "https://pncp.test/api/pncp"

        def consulta_url(self, path, params):
            return f"{self.consulta_base}{path}"

        def api_url(self, path):
            return f"{self.api_base}{path}"

        def request_url(self, url, kind, *, attempts, timeout):
            if "/atas" in url:
                return {
                    "status": "disponivel",
                    "json": {
                        "data": [
                            {
                                "numeroControlePNCPAta": "11111111000100-1-000002/2025-000001",
                                "objetoContratacao": "B",
                            },
                            {
                                "numeroControlePNCPAta": "11111111000100-1-000001/2025-000001",
                                "objetoContratacao": "A",
                            },
                        ]
                    },
                }
            return {
                "status": "disponivel",
                "json": {
                    "data": [
                        {"numeroControlePNCP": "11111111000100-1-000002/2025"},
                        {"numeroControlePNCP": "11111111000100-1-000001/2025"},
                    ]
                },
            }

    provider = PNCPProvider(FakeClient())
    contracts = provider.search(
        {"resource_type": "contratacao", "volume_alvo": 1, "params": {"tamanhoPagina": 10}}
    )
    atas = provider.search(
        {"resource_type": "ata", "volume_alvo": 1, "params": {"tamanhoPagina": 10}}
    )

    assert contracts[0].source_ref.key["sequencial"] == "000001"
    assert atas[0].source_ref.key["sequencial_compra"] == "000001"


def test_pncp_provider_searches_pca_units():
    class FakeClient:
        api_base = "https://pncp.test/api/pncp"

        def api_url(self, path):
            return f"{self.api_base}{path}"

        def request_url(self, url, kind, *, attempts, timeout):
            return {
                "status": "disponivel",
                "json": [
                    {
                        "sequencialPca": 2,
                        "numeroControlePNCP": "11111111000100-0-000002/2025",
                    },
                    {
                        "sequencialPca": 1,
                        "numeroControlePNCP": "11111111000100-0-000001/2025",
                    },
                ],
            }

    candidates = PNCPProvider(FakeClient()).search(
        {
            "resource_type": "pca",
            "volume_alvo": 1,
            "params": {"cnpjs": ["11111111000100"], "anos": [2025]},
        }
    )

    assert len(candidates) == 1
    assert candidates[0].source_ref.key == {
        "cnpj": "11111111000100",
        "ano": "2025",
        "sequencial": "1",
    }


def test_fetch_provider_access_uses_provider_artifacts(monkeypatch):
    source = SourceRef("fake", "resource", {"id": "one"})
    artifacts = (
        SourceArtifact("fake:json", source, "https://example.test/one.json", "json"),
        SourceArtifact("fake:csv", source, "https://example.test/one.csv", "csv", filename="one.csv"),
    )

    class FakeProvider:
        def resolve(self, source_ref):
            assert source_ref == source
            return ResolvedSource(source_ref, artifacts)

        def list_artifacts(self, resolved):
            return resolved.artifacts

    monkeypatch.setattr(fetch_corpus, "get_provider", lambda name: FakeProvider())
    monkeypatch.setattr(fetch_corpus, "_checkpoint_write", lambda context: None)
    calls = []

    def fake_json(context, base, endpoint_name, endpoint):
        calls.append(("json", endpoint))
        return [{"logical_id": "json", "status": "ok", "path": endpoint}]

    def fake_binary(context, base, url, logical_id, filename_prefix, **kwargs):
        calls.append(("binary", url, kwargs["expected_type"]))
        return {"logical_id": logical_id, "status": "ok", "path": url}

    monkeypatch.setattr(fetch_corpus, "_collect_rest_endpoint", fake_json)
    monkeypatch.setattr(fetch_corpus, "_collect_http_item", fake_binary)
    context = SimpleNamespace(items={})
    base = {
        "base_id": "base_provider",
        "natureza": "estruturada",
        "acesso": {"tipo": "provider", "provider": "fake", "sources": [source.to_dict()]},
    }

    records = fetch_corpus._collect_base(context, base)

    assert [call[0] for call in calls] == ["json", "binary"]
    assert records[0]["source_ref"] == source.to_dict()
    assert records[1]["artifact_id"] == "fake:csv"
    assert records[1]["provider"] == "fake"
