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


def test_respostas_levam_cabecalhos_de_seguranca() -> None:
    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


def test_preflight_do_site_continua_liberado_com_os_cabecalhos() -> None:
    response = TestClient(app).options(
        "/api/v1/query",
        headers={"Origin": "https://etuarda.github.io", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type,ngrok-skip-browser-warning"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://etuarda.github.io"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_readiness_separado_retorna_503_sem_manifesto(monkeypatch) -> None:
    monkeypatch.setattr(api_module.config, "SNAPSHOT_MANIFEST", "")

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": {"ready": False, "reason": "manifesto_nao_configurado"}}


def test_readiness_nao_expoe_excecoes_ou_caminhos(monkeypatch) -> None:
    from src.snapshot import SnapshotValidationError

    monkeypatch.setattr(api_module.snapshot, "readiness",
                        lambda: {"ready": False, "reason": SnapshotValidationError("hash_divergente").code})

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": {"ready": False, "reason": "hash_divergente"}}
    assert "path" not in response.text and "C:/" not in response.text


def test_readiness_ready_devolve_apenas_capacidades_publicas(monkeypatch) -> None:
    monkeypatch.setattr(api_module.snapshot, "readiness", lambda: {
        "ready": True, "snapshot_id": "v7-test", "schema_version": "7", "capabilities": ["pca"],
    })

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["capabilities"] == ["pca"]


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
    assert body.pop("pergunta_reformulada") is None
    assert body.pop("avisos") == []
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
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", "chave-de-teste")
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
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", "chave-de-teste")
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    for i in range(3):
        client.post("/api/v1/query", json={"query": f"Pergunta {i}"})

    response = client.get("/api/v1/history", params={"limit": 1})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_history_sanitiza_registro_legado_com_caminho_interno(monkeypatch, historico) -> None:
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", "chave-de-teste")
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
# Histórico temporariamente aberto; trace continua protegido.
# --------------------------------------------------------------------------

_PROTEGIDOS = ["/api/v1/trace/q_1", "/api/v1/sessions/s_sessao_de_teste_01/trace", "/api/v1/history"]


@pytest.mark.parametrize("url", _PROTEGIDOS)
def test_endpoint_protegido_sem_chave_e_401(url):
    assert TestClient(app).get(url).status_code == 401


@pytest.mark.parametrize("url", _PROTEGIDOS)
def test_endpoint_protegido_com_chave_errada_e_401(url):
    assert TestClient(app, headers={"X-API-Key": "chave-errada"}).get(url).status_code == 401


def _duas_conversas(monkeypatch):
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    minha = TestClient(app).post("/api/v1/query", json={"query": "Minha pergunta"}).json()
    TestClient(app).post("/api/v1/query", json={"query": "Pergunta de outra pessoa"})
    return minha


def test_historico_com_x_session_id_mostra_so_a_propria_conversa(monkeypatch):
    minha = _duas_conversas(monkeypatch)
    dono = TestClient(app, headers={"X-Session-Id": minha["session_id"]})

    response = dono.get("/api/v1/history")

    assert response.status_code == 200
    assert [item["query"] for item in response.json()] == ["Minha pergunta"]


def test_historico_so_com_filtro_de_sessao_e_401(monkeypatch):
    # O parâmetro session_id é filtro, não prova de que a conversa é de quem pede.
    minha = _duas_conversas(monkeypatch)

    response = TestClient(app).get("/api/v1/history", params={"session_id": minha["session_id"]})

    assert response.status_code == 401


def test_historico_geral_com_chave(monkeypatch):
    _duas_conversas(monkeypatch)

    response = client.get("/api/v1/history")

    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "chave-errada"}])
def test_history_public_abre_o_historico_geral_sem_chave(monkeypatch, headers):
    monkeypatch.setattr(api_module.config, "HISTORY_PUBLIC", True)
    _duas_conversas(monkeypatch)

    response = TestClient(app, headers=headers).get("/api/v1/history")

    assert response.status_code == 200
    assert len(response.json()) == 2
@pytest.mark.parametrize("server_key", ["", "chave-de-teste"])
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "chave-errada"}])
def test_historico_sem_credencial_e_401(monkeypatch, server_key, headers):
    '''Dois personas: sem dono (X-Session-Id) e sem chave válida, nada se lista.'''
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", server_key)
    visitante = TestClient(app, headers=headers)
    assert visitante.get("/api/v1/history").status_code == 401


def test_historico_admin_com_chave_ve_todas_as_sessoes(monkeypatch) -> None:
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", "chave-de-teste")
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    dono_a = TestClient(app, headers={"X-Session-Id": "sessao_do_usuario_a_001"})
    dono_b = TestClient(app, headers={"X-Session-Id": "sessao_do_usuario_b_002"})
    dono_a.post("/api/v1/query", json={"query": "Pergunta do A",
                                       "session_id": "sessao_do_usuario_a_001"})
    dono_b.post("/api/v1/query", json={"query": "Pergunta do B",
                                       "session_id": "sessao_do_usuario_b_002"})

    admin = TestClient(app, headers={"X-API-Key": "chave-de-teste"})
    resposta = admin.get("/api/v1/history")

    assert resposta.status_code == 200
    assert {item["query"] for item in resposta.json()} == {"Pergunta do A", "Pergunta do B"}


def test_historico_novo_usuario_ve_so_a_propria_sessao(monkeypatch) -> None:
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", "chave-de-teste")
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    dono_a = TestClient(app, headers={"X-Session-Id": "sessao_do_usuario_a_001"})
    dono_b = TestClient(app, headers={"X-Session-Id": "sessao_do_usuario_b_002"})
    dono_a.post("/api/v1/query", json={"query": "Pergunta do A",
                                       "session_id": "sessao_do_usuario_a_001"})
    dono_b.post("/api/v1/query", json={"query": "Pergunta do B",
                                       "session_id": "sessao_do_usuario_b_002"})

    resposta = dono_a.get("/api/v1/history")

    assert resposta.status_code == 200
    assert [item["query"] for item in resposta.json()] == ["Pergunta do A"]


def test_historico_query_param_nao_autentica_o_novo_usuario(monkeypatch) -> None:
    '''session_id de query é filtro do operador: pedir a sessão de outra pessoa
    não devolve as consultas dela.'''
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", "chave-de-teste")
    monkeypatch.setattr(api_module.pipeline, "responder", _fake_responder)
    dono_a = TestClient(app, headers={"X-Session-Id": "sessao_do_usuario_a_001"})
    dono_b = TestClient(app, headers={"X-Session-Id": "sessao_do_usuario_b_002"})
    dono_a.post("/api/v1/query", json={"query": "Pergunta do A",
                                       "session_id": "sessao_do_usuario_a_001"})
    dono_b.post("/api/v1/query", json={"query": "Pergunta do B",
                                       "session_id": "sessao_do_usuario_b_002"})

    resposta = dono_a.get("/api/v1/history", params={"session_id": "sessao_do_usuario_b_002"})

    assert resposta.status_code == 200
    assert [item["query"] for item in resposta.json()] == ["Pergunta do A"]


def test_trace_continua_protegido_sem_chave_configurada(monkeypatch):
    monkeypatch.setattr(api_module.config, "RAG_API_KEY", "")
    for url in _PROTEGIDOS:
        assert TestClient(app).get(url).status_code == 401


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
        "Access-Control-Request-Headers": "X-API-Key, X-Session-Id",
    })

    assert response.status_code == 200
    liberados = response.headers["access-control-allow-headers"].lower()
    assert "x-api-key" in liberados and "x-session-id" in liberados


def test_cors_libera_o_header_do_tunel_ngrok():
    # O site publicado chama o backend por um túnel ngrok gratuito, que exige
    # este header para não devolver a página de aviso no lugar da resposta.
    response = TestClient(app).options("/api/v1/query", headers={
        "Origin": "https://etuarda.github.io",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type, X-Session-Id, ngrok-skip-browser-warning",
    })

    assert response.status_code == 200
    assert "ngrok-skip-browser-warning" in response.headers["access-control-allow-headers"].lower()


def test_query_repassa_o_historico_ao_pipeline_e_devolve_a_pergunta_reformulada(monkeypatch) -> None:
    chamadas = []

    def fake_responder(query, *, top_k, run_id=None, query_id=None, session_id=None, historico=None):
        chamadas.append((query, [(t.pergunta, t.resposta) for t in historico]))
        return {**_response(query), "pergunta_reformulada": "Quais regras valem para os contratos de TI de 2025?"}

    monkeypatch.setattr(api_module.pipeline, "responder", fake_responder)

    response = client.post("/api/v1/query", json={
        "query": "Considerando os mesmos contratos, quais regras valem?",
        "historico": [{"pergunta": "Contratos de TI em 2025?", "resposta": "Há três."}],
    })

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Considerando os mesmos contratos, quais regras valem?"
    assert body["pergunta_reformulada"] == "Quais regras valem para os contratos de TI de 2025?"
    assert chamadas == [(
        "Considerando os mesmos contratos, quais regras valem?",
        [("Contratos de TI em 2025?", "Há três.")],
    )]


def test_query_sem_historico_nao_envia_o_argumento_ao_pipeline(monkeypatch) -> None:
    recebidos = {}

    def fake_responder(query, **kwargs):
        recebidos.update(kwargs)
        return _response(query)

    monkeypatch.setattr(api_module.pipeline, "responder", fake_responder)

    response = client.post("/api/v1/query", json={"query": "Qual e a norma?", "historico": []})

    assert response.status_code == 200
    assert "historico" not in recebidos
    assert response.json()["pergunta_reformulada"] is None


@pytest.mark.parametrize("historico", [
    [{"pergunta": ""}],
    [{"resposta": "sem pergunta"}],
    [{"pergunta": "x"}] * 11,
    [{"pergunta": "x" * 2001}],
])
def test_query_rejeita_historico_malformado(historico) -> None:
    response = client.post("/api/v1/query", json={"query": "E em 2024?", "historico": historico})

    assert response.status_code == 422


def test_api_pre_carrega_o_rerank_ao_subir(monkeypatch):
    import threading
    chamou = threading.Event()
    monkeypatch.setattr(api_module.config, "RERANK_ENABLED", True)
    monkeypatch.setattr(api_module.config, "RERANK_PRELOAD", True)
    monkeypatch.setattr(api_module.rerank, "pre_carregar", chamou.set)

    with TestClient(app):
        assert chamou.wait(timeout=5)


def test_api_sem_pre_carregamento_nao_carrega_o_rerank(monkeypatch):
    monkeypatch.setattr(api_module.config, "RERANK_PRELOAD", False)
    monkeypatch.setattr(api_module.rerank, "pre_carregar", lambda: pytest.fail("não deveria pré-carregar"))

    with TestClient(app) as cliente:
        assert cliente.get("/health").status_code == 200


def test_api_pre_carrega_os_indices_ao_subir(monkeypatch):
    import threading
    chamou = threading.Event()
    monkeypatch.setattr(api_module.config, "INDEX_PRELOAD", True)
    monkeypatch.setattr(api_module.retrieve, "pre_carregar_indices", chamou.set)

    with TestClient(app):
        assert chamou.wait(timeout=5)


def test_api_sem_pre_carregamento_nao_le_os_indices(monkeypatch):
    chamadas = []
    monkeypatch.setattr(api_module.config, "INDEX_PRELOAD", False)
    monkeypatch.setattr(api_module.retrieve, "pre_carregar_indices", lambda: chamadas.append(1))

    with TestClient(app) as cliente:
        assert cliente.get("/health").status_code == 200
    assert chamadas == []
