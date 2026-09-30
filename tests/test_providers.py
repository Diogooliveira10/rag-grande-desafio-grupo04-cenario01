from types import SimpleNamespace

import pytest

# Merge da branch joao-reis (2026-09-23): o pacote src/providers/ ainda não foi
# publicado. O arquivo é pulado até ele existir e volta a rodar sozinho depois.
pytest.importorskip("src.providers", reason="src/providers/ ainda não publicado")

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
    assert len(purchase.artifacts) == 4
    assert purchase.artifacts[0].url.endswith("/compras/2025/1220")
    assert purchase.artifacts[1].metadata["resource_kind"] == "itens"
    assert purchase.artifacts[2].metadata["resource_kind"] == "documentos_edital"
    assert purchase.artifacts[3].metadata["resource_kind"] == "atas_compra"
    assert purchase.artifacts[3].url.endswith("/compras/2025/1220/atas")

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
    assert ata.artifacts[2].url.endswith("/compras/2024/2138/atas/2/contratos")
    assert ata.artifacts[2].metadata["resource_kind"] == "contratos_vinculados_ata"

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


def test_pncp_provider_filtra_atas_por_cnpj_do_orgao():
    class FakeClient:
        consulta_base = "https://pncp.test/api/consulta"

        def __init__(self):
            self.params = []

        def consulta_url(self, path, params):
            self.params.append(dict(params))
            return f"{self.consulta_base}{path}"

        def request_url(self, url, kind, *, attempts, timeout):
            cnpj = self.params[-1]["cnpj"]
            return {
                "status": "disponivel",
                "json": {"data": [{"numeroControlePNCPAta": f"{cnpj}-1-000001/2025-000001"}]},
            }

    client = FakeClient()
    atas = PNCPProvider(client).search(
        {
            "resource_type": "ata",
            "volume_alvo": 2,
            "params": {"tamanhoPagina": 10, "cnpjs": ["11111111000100", "22222222000100"]},
        }
    )

    assert [params["cnpj"] for params in client.params] == ["11111111000100", "22222222000100"]
    assert [ata.source_ref.key["cnpj"] for ata in atas] == ["11111111000100", "22222222000100"]


_ATA = SourceRef(
    "pncp",
    "ata",
    {"cnpj": "42498600000171", "ano_compra": 2023, "sequencial_compra": 1187, "sequencial_ata": 1},
)


class _ListagemFake:
    api_base = "https://pncp.test/api/pncp"

    def __init__(self, response):
        self.response = response
        self.urls = []

    def request_url(self, url, kind, *, attempts, timeout):
        self.urls.append(url)
        return self.response


def test_pncp_provider_lista_os_pdfs_da_ata():
    client = _ListagemFake(
        {
            "status": "disponivel",
            "json": [
                {"sequencialDocumento": 1, "titulo": "Ata nº 00178", "url": "https://pncp.test/arquivos/1"},
                {"sequencialDocumento": 2, "titulo": "Ata retificada", "url": "https://pncp.test/arquivos/2"},
                {"sequencialDocumento": 3, "titulo": "Sem URL"},
            ],
        }
    )
    provider = PNCPProvider(client)

    artifacts = provider.list_artifacts(provider.resolve(_ATA))

    assert client.urls == [artifacts[1].url]
    assert artifacts[1].artifact_id.endswith(":arquivos")
    pdfs = [artifact for artifact in artifacts if artifact.artifact_type == "pdf"]
    assert [pdf.url for pdf in pdfs] == ["https://pncp.test/arquivos/1", "https://pncp.test/arquivos/2"]
    assert [pdf.artifact_id for pdf in pdfs] == [f"{_ATA.stable_id}:arquivo:1", f"{_ATA.stable_id}:arquivo:2"]
    assert all(pdf.metadata["logical_unit"] is False for pdf in pdfs)


def test_pncp_provider_mantem_a_ata_sem_pdfs_se_a_listagem_falhar():
    provider = PNCPProvider(_ListagemFake({"status": "indisponivel", "erro": "timeout"}))
    resolved = provider.resolve(_ATA)

    assert provider.list_artifacts(resolved) == resolved.artifacts
def test_pncp_provider_enumerates_signed_contracts_and_keeps_all_censo_candidates():
    class FakeClient:
        consulta_base = "https://pncp.test/api/consulta"
        api_base = "https://pncp.test/api/pncp"

        def __init__(self):
            self.calls = []

        def consulta_url(self, path, params):
            self.calls.append(dict(params))
            return f"{self.consulta_base}{path}?pagina={params['pagina']}"

        def request_url(self, url, kind, *, attempts, timeout):
            return {
                "status": "disponivel",
                "json": {
                    "totalPaginas": 1,
                    "data": [
                        {
                            "numeroControlePNCP": "42498600000171-1-000002/2025",
                            "numeroControlePNCPCompra": "42498600000171-1-000002/2025",
                            "dataAssinatura": "2025-06-01",
                            "tipoContrato": {"id": 1, "nome": "Contrato"},
                            "unidadeExecutora": {"localidade": {"uf": "RJ"}},
                        },
                        {
                            "numeroControlePNCP": "42498600000171-1-000001/2025",
                            "numeroControlePncpCompra": "42498600000171-1-000001/2025",
                            "dataAssinatura": "2025-03-01",
                            "tipoContrato": {"id": 2, "nome": "Contrato"},
                            "unidadeOrgao": {"ufSigla": "RJ", "municipioNome": "Niterói"},
                        },
                        {
                            "numeroControlePNCP": "42498600000171-1-000003/2025",
                            "dataAssinatura": "2025-03-01",
                            "tipoContrato": {"id": 3, "nome": "Empenho"},
                            "ufSigla": "RJ",
                        },
                        {
                            "numeroControlePNCP": "42498600000171-1-000004/2025",
                            "dataAssinatura": "2024-12-31",
                            "tipoContrato": {"id": 1, "nome": "Contrato"},
                            "ufSigla": "RJ",
                        },
                        {
                            "numeroControlePNCP": "42498600000171-1-000005/2025",
                            "dataAssinatura": "2025-03-01",
                            "tipoContrato": {"id": 1, "nome": "Contrato"},
                            "ufSigla": "RJ",
                        },
                    ],
                },
            }

    client = FakeClient()
    results = PNCPProvider(client).search(
        {
            "resource_type": "contrato_assinado",
            "volume_alvo": 1,
            "censo": True,
            "params": {
                "cnpjs": ["42498600000171"],
                "anoAssinatura": 2025,
                "dataInicial": "2025-01-01",
                "dataFinal": "2026-09-25",
                "tamanhoPagina": 500,
            },
        }
    )

    assert [candidate.source_ref.key["numeroControlePNCP"] for candidate in results] == [
        "42498600000171-1-000001/2025",
        "42498600000171-1-000002/2025",
    ]
    assert {(call["dataInicial"], call["dataFinal"]) for call in client.calls} == {
        ("20250101", "20251231"),
        ("20260101", "20260925"),
    }
    assert results[0].source_ref.key["numeroControlePNCPCompra"] == "42498600000171-1-000001/2025"


def test_pncp_signed_contract_search_fails_closed_for_incomplete_roster(tmp_path):
    roster = tmp_path / "cnpjs.json"
    roster.write_text('{"completo": false, "cnpjs": ["42498600000171"]}', encoding="utf-8")
    provider = PNCPProvider()

    with pytest.raises(RuntimeError, match="ainda não foi reconciliado como completo"):
        provider.search(
            {
                "resource_type": "contrato_assinado",
                "censo": True,
                "exigir_cadastro_completo": True,
                "params": {"cnpjs_file": str(roster)},
            }
        )


def test_pncp_verified_contract_discovery_filters_rj_and_resumes_completed_pages():
    rj_1 = {
        "numeroControlePNCP": "11111111000100-2-000001/2025",
        "numeroControlePNCPCompra": "11111111000100-1-000001/2025",
        "dataAssinatura": "2025-04-01",
        "tipoContrato": {"id": 1, "nome": "Contrato"},
        "unidadeOrgao": {"ufSigla": "RJ", "codigoUnidade": "1"},
    }
    sp = {
        "numeroControlePNCP": "22222222000100-2-000001/2025",
        "dataAssinatura": "2025-04-01",
        "tipoContrato": {"id": 1, "nome": "Contrato"},
        "unidadeOrgao": {"ufSigla": "SP"},
    }
    rj_2 = {
        "numeroControlePNCP": "11111111000100-2-000002/2025",
        "dataAssinatura": "2025-05-01",
        "tipoContrato": {"id": 1, "nome": "Contrato"},
        "unidadeOrgao": {"ufSigla": "RJ", "codigoUnidade": "1"},
    }
    rj_1_without_purchase_link = {
        "numeroControlePNCP": "11111111000100-2-000001/2025",
        "dataAssinatura": "2025-04-01",
        "tipoContrato": {"id": 1, "nome": "Contrato"},
        "unidadeOrgao": {"ufSigla": "RJ", "codigoUnidade": "1"},
    }

    class FakeClient:
        consulta_base = "https://pncp.test/api/consulta"
        api_base = "https://pncp.test/api/pncp"

        def __init__(self):
            self.calls = []
            self.requested_pages = []

        def consulta_url(self, path, params):
            self.calls.append(dict(params))
            return f"{self.consulta_base}{path}?pagina={params['pagina']}"

        def request_url(self, url, kind, *, attempts, timeout):
            page = int(url.rsplit("=", 1)[1])
            self.requested_pages.append(page)
            rows = {1: [rj_1, sp], 2: [rj_2, rj_1_without_purchase_link]}[page]
            return {
                "status": "disponivel",
                "json": {"totalPaginas": 2, "totalRegistros": 4, "data": rows},
            }

    client = FakeClient()
    state = {
        "completed_pages": [],
        "completed_windows": [],
        "eligible_sources": {},
        "total_paginas_por_janela": {},
    }
    query = {
        "resource_type": "contrato_assinado",
        "volume_alvo": 1200,
        "censo": True,
        "censo_nacional": True,
        "params": {
            "anoAssinatura": 2025,
            "dataInicial": "2025-01-01",
            "dataFinal": "2025-01-02",
            "tamanhoPagina": 500,
        },
        "discovery_state": state,
        "verified_unit_scopes": [
            {"cnpj": "11111111000100", "codigoUnidadeAdministrativa": "1", "ufSigla": "RJ"},
        ],
    }

    def interrupt_after_first_page(report):
        state["completed_pages"].append(report["page_key"])
        state["total_paginas_por_janela"][report["window"]] = report["total_paginas"]
        for candidate in report["candidates"]:
            control = candidate.source_ref.key["numeroControlePNCP"]
            state["eligible_sources"][control] = candidate.source_ref.to_dict()
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        PNCPProvider(client).search({**query, "on_discovery_page": interrupt_after_first_page})

    def record_page(report):
        state["completed_pages"].append(report["page_key"])
        for candidate in report["candidates"]:
            control = candidate.source_ref.key["numeroControlePNCP"]
            existing = state["eligible_sources"].get(control)
            current = candidate.source_ref.to_dict()
            if existing is None or (
                not existing["key"].get("numeroControlePNCPCompra")
                and current["key"].get("numeroControlePNCPCompra")
            ):
                state["eligible_sources"][control] = current

    results = PNCPProvider(client).search({**query, "on_discovery_page": record_page})
    assert client.requested_pages == [1, 2]
    assert all(call["cnpjOrgao"] == "11111111000100" for call in client.calls)
    assert all(call["codigoUnidadeAdministrativa"] == "1" for call in client.calls)
    assert {candidate.source_ref.key["cnpj"] for candidate in results} == {
        "11111111000100",
    }
    assert len(results) == 2
    contract = next(candidate for candidate in results if candidate.candidate_id == rj_1["numeroControlePNCP"])
    assert contract.source_ref.key["numeroControlePNCPCompra"] == "11111111000100-1-000001/2025"


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
