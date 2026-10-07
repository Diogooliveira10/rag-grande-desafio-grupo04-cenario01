import pytest
from fastapi.testclient import TestClient

from app import api as api_module
from app.api import app
from src.history_store import HistoricoStore


client = TestClient(app, headers={"X-API-Key": "chave-de-teste"})


@pytest.fixture(autouse=True)
def historico(tmp_path, monkeypatch) -> HistoricoStore:
    """Nenhum teste escreve no historico.sqlite real do repositorio."""
    store = HistoricoStore(db_path=tmp_path / "historico.sqlite")
    monkeypatch.setattr(api_module, "historico", store)
    return store


def _response(query: str) -> dict:
    return {
        "query": query,
        "answer": "Resposta fundamentada.",
        "bases_consultadas": ["normativa"],
        "sources_used": [
            {
                "chunk_id": "chunk-1",
                "base_id": "base-1",
                "source_file": "documento.pdf",
            }
        ],
        "confidence_level": "alta",
        "is_refusal": False,
        "refusal_reason": None,
    }


def _fake_responder(query: str, *, top_k: int, run_id=None, query_id=None, session_id=None):
    return _response(query)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_delega_ao_pipeline(monkeypatch) -> None:
    calls = []

    def fake_responder(query: str, *, top_k: int, run_id=None, query_id=None, session_id=None):
        calls.append((query, top_k, session_id))
        return _response(query)

    monkeypatch.setattr(api_module.pipeline, "responder", fake_responder)

    response = client.post(
        "/api/v1/query", json={"query": "  Qual e a norma?  ", "top_k": 8}
    )

    assert response.status_code == 200
    body = response.json()
    query_id = body.pop("query_id")
    session_id = body.pop("session_id")
    assert query_id.startswith("q_")
    assert session_id.startswith("s_")
    assert body == _response("Qual e a norma?")
    assert calls == [("Qual e a norma?", 8, session_id)]


def test_query_grava_no_historico(monkeypatch, historico) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)

    response = client.post("/api/v1/query", json={"query": "Qual e a norma?"})

    query_id = response.json()["query_id"]
    consulta = historico.obter_consulta(query_id)
    assert consulta is not None
    assert consulta["query"] == "Qual e a norma?"
    assert consulta["resposta"] == _response("Qual e a norma?")
    assert consulta["feedback"] == []


def test_query_nao_expoe_caminho_interno_da_fonte(monkeypatch, historico) -> None:
    result = _response("Pergunta")
    result["sources_used"][0]["source_file"] = "data/raw/v6/segredo/documento.pdf"
    monkeypatch.setattr(api_module.pipeline, "responder", lambda *args, **kwargs: result)

    response = client.post("/api/v1/query", json={"query": "Pergunta"})

    assert response.status_code == 200
    assert response.json()["sources_used"][0]["source_file"] == "documento.pdf"
    query_id = response.json()["query_id"]
    assert historico.obter_consulta(query_id)["resposta"]["sources_used"][0]["source_file"] == "documento.pdf"


def test_query_rejeita_texto_vazio() -> None:
    response = client.post("/api/v1/query", json={"query": "   "})

    assert response.status_code == 422


def test_query_trata_erro_sem_expor_detalhes(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(api_module.pipeline, "responder", fail)

    response = client.post("/api/v1/query", json={"query": "Pergunta valida"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Nao foi possivel processar a consulta."}


def test_cors_permite_github_pages() -> None:
    response = client.options(
        "/api/v1/query",
        headers={
            "Origin": "https://etuarda.github.io",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://etuarda.github.io"


def test_feedback_recusa_query_id_desconhecido() -> None:
    response = client.post(
        "/api/v1/feedback", json={"query_id": "q_fantasma", "avaliacao": "positivo"}
    )

    assert response.status_code == 404


def test_feedback_aceita_avaliacao_de_uma_consulta_existente(monkeypatch, historico) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    query_id = client.post("/api/v1/query", json={"query": "Pergunta"}).json()["query_id"]

    response = client.post(
        "/api/v1/feedback",
        json={"query_id": query_id, "avaliacao": "negativo", "comentario": "Faltou fonte."},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "recebido"}
    [feedback] = historico.obter_consulta(query_id)["feedback"]
    assert feedback["avaliacao"] == "negativo"
    assert feedback["comentario"] == "Faltou fonte."


def test_feedback_rejeita_avaliacao_fora_do_vocabulario(monkeypatch) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    query_id = client.post("/api/v1/query", json={"query": "Pergunta"}).json()["query_id"]

    response = client.post(
        "/api/v1/feedback", json={"query_id": query_id, "avaliacao": "mais ou menos"}
    )

    assert response.status_code == 422


def test_history_retorna_consultas_recentes_com_feedback(monkeypatch) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    query_id = client.post("/api/v1/query", json={"query": "Pergunta 1"}).json()["query_id"]
    client.post("/api/v1/query", json={"query": "Pergunta 2"})
    client.post("/api/v1/feedback", json={"query_id": query_id, "avaliacao": "positivo"})

    response = client.get("/api/v1/history")

    assert response.status_code == 200
    itens = response.json()
    assert len(itens) == 2
    assert itens[0]["query"] == "Pergunta 2"  # mais recente primeiro
    mais_antiga = itens[1]
    assert mais_antiga["query_id"] == query_id
    [feedback] = mais_antiga["feedback"]
    assert feedback["avaliacao"] == "positivo"
    assert feedback["comentario"] is None


def test_history_respeita_o_parametro_limit(monkeypatch) -> None:
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    for i in range(3):
        client.post("/api/v1/query", json={"query": f"Pergunta {i}"})

    response = client.get("/api/v1/history", params={"limit": 1})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_history_sanitiza_registro_legado_com_caminho_interno(monkeypatch, historico) -> None:
    result = _response("Pergunta antiga")
    result["sources_used"][0]["source_file"] = "C:\\dados\\privados\\documento.pdf"
    historico.registrar_consulta(
        query_id="q_legado", run_id="run1", query="Pergunta antiga", top_k=5, resposta=result
    )

    response = client.get("/api/v1/history")

    assert response.status_code == 200
    assert response.json()[0]["resposta"]["sources_used"][0]["source_file"] == "documento.pdf"


def test_explore_contratacoes_retorna_dados_reais_tipados(monkeypatch) -> None:
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return [{
            "id": "123-2-000001/2025",
            "numero_contrato": "123-2-000001/2025",
            "orgao": "Órgão público",
            "valor_global": 1250.5,
            "fonte_oficial": "PNCP",
        }]

    monkeypatch.setattr(api_module.catalog, "listar_contratacoes", fake)
    response = client.get(
        "/api/v1/explore/contratacoes",
        params={"limit": 10, "offset": 2, "busca": "software", "orgao": "órgão"},
    )

    assert response.status_code == 200
    assert response.json()[0]["numero_contrato"] == "123-2-000001/2025"
    assert response.json()[0]["data_homologacao"] is None
    assert calls == [{"limit": 10, "offset": 2, "busca": "software", "orgao": "órgão"}]


def test_explore_documentos_retorna_inventario_sem_caminho_interno(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module.catalog,
        "listar_documentos",
        lambda **kwargs: [{
            "id": "pncp:documento:1",
            "titulo": "Lei nº 14.133/2021",
            "tipo": "Normativo",
            "fonte": "Planalto",
            "link": "https://www.planalto.gov.br/",
            "base_id": "editais_normativo",
            "natureza": "normativa",
        }],
    )

    response = client.get("/api/v1/explore/documentos")

    assert response.status_code == 200
    body = response.json()[0]
    assert body["base_id"] == "editais_normativo"
    assert "source_file" not in body
    assert "path" not in body


def test_explore_orgaos_nao_inventa_sigla_ou_categoria(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module.catalog,
        "listar_orgaos",
        lambda **kwargs: [{
            "cnpj": "12345678000100",
            "sigla": None,
            "nome": "Município de Exemplo",
            "esfera": "municipal",
            "total_contratacoes": 3,
            "valor_total": 9000,
            "principais_categorias": [],
            "principais_fornecedores": ["Fornecedor real"],
        }],
    )

    response = client.get("/api/v1/explore/orgaos")

    assert response.status_code == 200
    assert response.json()[0]["sigla"] is None
    assert response.json()[0]["principais_categorias"] == []


def test_explore_fontes_retorna_manifesto(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module.catalog,
        "listar_fontes",
        lambda: [{
            "id": "contratos_estruturado",
            "nome": "PNCP - contratos",
            "sigla": "PNCP",
            "instituicao": "Portal Nacional de Contratações Públicas",
            "url": "https://pncp.gov.br",
            "natureza": "estruturada",
            "escopo": "oficial",
            "formatos": ["json"],
        }],
    )

    response = client.get("/api/v1/explore/fontes")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "contratos_estruturado"


def test_project_summary_retorna_metricas_do_inventario(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module.catalog,
        "project_summary",
        lambda: {
            "projeto": "NEXO RJ",
            "scenario_id": "contratacoes-rj",
            "versao_corpus": "v6",
            "inventario_completo": True,
            "total_documentos": 1400,
            "total_itens": 1400,
            "total_falhas": 0,
            "bases": [{
                "base_id": "contratos_estruturado",
                "natureza": "estruturada",
                "escopo": "oficial",
                "status": "complete",
                "documentos": 625,
                "formatos": {"json": 625},
            }],
        },
    )

    response = client.get("/api/v1/project/summary")

    assert response.status_code == 200
    assert response.json()["versao_corpus"] == "v6"
    assert response.json()["total_documentos"] == 1400


def test_explore_valida_paginacao() -> None:
    response = client.get("/api/v1/explore/contratacoes", params={"limit": 0})

    assert response.status_code == 422


def test_explore_nao_expoe_erro_interno(monkeypatch) -> None:
    monkeypatch.setattr(
        api_module.catalog, "listar_documentos", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("C:/segredo"))
    )

    response = client.get("/api/v1/explore/documentos")

    assert response.status_code == 500
    assert response.json() == {"detail": "Não foi possível consultar os dados do projeto."}


# --------------------------------------------------------------------------
# Proteção: histórico e trace só com X-API-Key
# --------------------------------------------------------------------------

_PROTEGIDOS = ["/api/v1/history", "/api/v1/trace/q_1", "/api/v1/sessions/s_1/trace"]


@pytest.mark.parametrize("url", _PROTEGIDOS)
def test_endpoint_protegido_sem_chave_e_401(url):
    assert TestClient(app).get(url).status_code == 401


@pytest.mark.parametrize("url", _PROTEGIDOS)
def test_endpoint_protegido_com_chave_errada_e_401(url):
    assert TestClient(app, headers={"X-API-Key": "chave-errada"}).get(url).status_code == 401


@pytest.mark.parametrize("url", _PROTEGIDOS)
def test_endpoint_protegido_fica_fechado_sem_rag_api_key_no_servidor(url, monkeypatch):
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", "")

    response = client.get(url)

    assert response.status_code == 403
    assert "RAG_API_KEY" in response.json()["detail"]


def test_query_e_feedback_continuam_abertos_sem_chave(monkeypatch):
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    sem_chave = TestClient(app)

    query_id = sem_chave.post("/api/v1/query", json={"query": "Qual e a norma?"}).json()["query_id"]
    feedback = sem_chave.post("/api/v1/feedback", json={"query_id": query_id, "avaliacao": "positivo"})

    assert feedback.status_code == 200


def test_cors_libera_o_header_da_chave():
    response = TestClient(app).options("/api/v1/history", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "X-API-Key",
    })

    assert response.status_code == 200
    assert "x-api-key" in response.headers["access-control-allow-headers"].lower()
