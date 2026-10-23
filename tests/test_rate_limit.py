"""Limite de requisições por cliente (app/rate_limit.py) e a resposta 429 da API."""

import pytest
from fastapi.testclient import TestClient

from app import api as api_module
from app.api import app
from app.rate_limit import LimiteDeRequisicoes, cliente_real


class _Relogio:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora


def test_balde_deixa_passar_ate_o_limite_e_depois_pede_espera():
    relogio = _Relogio()
    limite = LimiteDeRequisicoes(10, relogio=relogio)
    assert [limite.consumir("a") for _ in range(10)] == [0] * 10
    # 10 por minuto: uma ficha volta a cada 6 s.
    assert limite.consumir("a") == 6


def test_balde_recarrega_com_o_tempo():
    relogio = _Relogio()
    limite = LimiteDeRequisicoes(10, relogio=relogio)
    for _ in range(10):
        limite.consumir("a")
    relogio.agora += 6
    assert limite.consumir("a") == 0
    assert limite.consumir("a") > 0
    relogio.agora += 600
    assert [limite.consumir("a") for _ in range(10)] == [0] * 10


def test_cada_cliente_tem_o_seu_balde():
    limite = LimiteDeRequisicoes(1, relogio=_Relogio())
    assert limite.consumir("a") == 0
    assert limite.consumir("a") > 0
    assert limite.consumir("b") == 0


def test_limite_zero_desliga():
    limite = LimiteDeRequisicoes(0)
    assert not limite.ligado
    assert all(limite.consumir("a") == 0 for _ in range(1000))


@pytest.mark.parametrize("host, encaminhado, esperado", [
    # Pelo túnel: a conexão vem de 127.0.0.1 e o IP real está no cabeçalho.
    ("127.0.0.1", "203.0.113.7", "203.0.113.7"),
    # O cliente pôs um IP falso; o túnel acrescentou o real no fim.
    ("127.0.0.1", "1.2.3.4, 203.0.113.7", "203.0.113.7"),
    ("::1", "203.0.113.7", "203.0.113.7"),
    # Sem proxy confiável, o cabeçalho é ignorado.
    ("198.51.100.9", "203.0.113.7", "198.51.100.9"),
    ("127.0.0.1", None, "127.0.0.1"),
    ("127.0.0.1", "  ", "127.0.0.1"),
    (None, None, "desconhecido"),
])
def test_cliente_real(host, encaminhado, esperado):
    assert cliente_real(host, encaminhado) == esperado


@pytest.fixture
def limite_de_tres(monkeypatch):
    monkeypatch.setattr(api_module, "limite_de_consultas", LimiteDeRequisicoes(3))
    monkeypatch.setattr(api_module, "limite_de_leitura", LimiteDeRequisicoes(3))
    monkeypatch.setattr(api_module.pipeline, "responder", lambda query, **_: {
        "query": query, "answer": "ok", "bases_consultadas": [], "sources_used": [],
        "confidence_level": "alta", "is_refusal": False, "refusal_reason": None})


def test_api_responde_429_com_retry_after_e_cors(limite_de_tres):
    client = TestClient(app)
    origem = {"Origin": "https://etuarda.github.io"}
    for _ in range(3):
        assert client.post("/api/v1/query", json={"query": "Teste"}, headers=origem).status_code == 200
    resposta = client.post("/api/v1/query", json={"query": "Teste"}, headers=origem)
    assert resposta.status_code == 429
    assert int(resposta.headers["Retry-After"]) >= 1
    assert "Tente de novo" in resposta.json()["detail"]
    # Sem o CORS, o site não consegue ler o 429 e mostra só "falha de rede".
    assert resposta.headers["access-control-allow-origin"] == "https://etuarda.github.io"
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"


def test_api_separa_clientes_atras_do_tunel(limite_de_tres, monkeypatch):
    # O TestClient conecta como "testclient"; aqui ele faz o papel do túnel.
    monkeypatch.setattr("app.rate_limit.PROXIES_CONFIAVEIS", frozenset({"testclient"}))
    client = TestClient(app)
    for _ in range(3):
        client.post("/api/v1/query", json={"query": "Teste"}, headers={"X-Forwarded-For": "203.0.113.7"})
    assert client.post("/api/v1/query", json={"query": "Teste"},
                       headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 429
    assert client.post("/api/v1/query", json={"query": "Teste"},
                       headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 200


def test_health_e_preflight_ficam_fora_do_limite(limite_de_tres):
    client = TestClient(app)
    assert all(client.get("/health").status_code == 200 for _ in range(10))
    preflight = {"Origin": "https://etuarda.github.io", "Access-Control-Request-Method": "POST"}
    assert all(client.options("/api/v1/query", headers=preflight).status_code == 200 for _ in range(10))


def test_leitura_tem_balde_proprio(limite_de_tres):
    client = TestClient(app)
    for _ in range(3):
        client.post("/api/v1/query", json={"query": "Teste"})
    assert client.post("/api/v1/query", json={"query": "Teste"}).status_code == 429
    assert client.get("/api/v1/explore/fontes").status_code != 429
