"""Testes de contrato do PncpClient, sem rede: urlopen e sleep são substituídos."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from src import pncp_client
from src.pncp_client import PncpClient

URL = "https://pncp.gov.br/api/consulta/v1/contratos"


class _Resposta:
    def __init__(self, body: bytes, *, status: int = 200, headers: dict | None = None, url: str = URL):
        self._body = body
        self.status = status
        self.headers = headers or {}
        self._url = url

    def read(self, limite: int = -1) -> bytes:
        return self._body if limite < 0 else self._body[:limite]

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, headers: dict | None = None, body: bytes = b"") -> HTTPError:
    return HTTPError(URL, code, "motivo", headers or {}, io.BytesIO(body))


@pytest.fixture
def fake_rede(monkeypatch):
    """Devolve, em ordem, as respostas/erros da lista; registra requests e pausas."""
    estado = {"respostas": [], "requests": [], "pausas": []}

    def fake_urlopen(request, timeout, context):
        estado["requests"].append(request)
        item = estado["respostas"].pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(pncp_client, "urlopen", fake_urlopen)
    monkeypatch.setattr(pncp_client.time, "sleep", estado["pausas"].append)
    return estado


def test_urls_separam_consulta_e_api_pncp():
    cliente = PncpClient(host="https://pncp.gov.br/")

    assert cliente.consulta_url("/v1/contratos", {"pagina": 1, "uf": "RJ"}) == (
        "https://pncp.gov.br/api/consulta/v1/contratos?pagina=1&uf=RJ"
    )
    assert cliente.api_url("/v1/orgaos/1/compras") == "https://pncp.gov.br/api/pncp/v1/orgaos/1/compras"


def test_json_valido_vem_decodificado_com_metadados_de_cache(fake_rede):
    corpo = json.dumps({"data": [1, 2]}).encode()
    fake_rede["respostas"] = [
        _Resposta(corpo, headers={"Content-Type": "application/json", "ETag": '"abc"'}, url="final")
    ]

    resultado = PncpClient().request_url(URL, "json")

    assert resultado["status"] == "disponivel"
    assert resultado["json"] == {"data": [1, 2]}
    assert resultado["etag"] == '"abc"'
    assert resultado["url_final"] == "final"
    assert resultado["tentativas"] == 1
    assert fake_rede["requests"][0].get_header("Accept").startswith("application/json")


def test_json_invalido_e_reportado_sem_excecao(fake_rede):
    fake_rede["respostas"] = [_Resposta(b"<html>erro</html>", headers={"Content-Type": "text/html"})]

    resultado = PncpClient().request_url(URL, "json")

    assert resultado["status"] == "resposta_invalida"
    assert "esperado JSON" in resultado["erro"]


def test_resposta_vazia_nao_e_erro(fake_rede):
    fake_rede["respostas"] = [_Resposta(b"", status=204)]

    resultado = PncpClient().request_url(URL, "json")

    assert resultado["status"] == "disponivel"
    assert resultado["observacao"] == "resposta sem conteúdo"


def test_resposta_acima_do_limite_e_truncada(fake_rede):
    fake_rede["respostas"] = [_Resposta(b"x" * 11)]

    resultado = PncpClient(read_limit=10).request_url(URL, "html")

    assert resultado["status"] == "resposta_truncada"
    assert resultado["bytes_lidos"] == 10


def test_304_devolve_nao_modificado(fake_rede):
    fake_rede["respostas"] = [_http_error(304, {"ETag": '"v1"'})]

    resultado = PncpClient().request_url(URL, "json", headers={"If-None-Match": '"v1"'})

    assert resultado["status"] == "nao_modificado"
    assert resultado["etag"] == '"v1"'
    assert fake_rede["requests"][0].get_header("If-none-match") == '"v1"'


def test_erro_transitorio_e_retentado_respeitando_retry_after(fake_rede):
    fake_rede["respostas"] = [
        _http_error(429, {"Retry-After": "3"}),
        _Resposta(b'{"ok": true}', headers={"Content-Type": "application/json"}),
    ]

    resultado = PncpClient().request_url(URL, "json")

    assert resultado["status"] == "disponivel"
    assert resultado["tentativas"] == 2
    assert fake_rede["pausas"] == [3.0]


def test_erro_transitorio_persistente_esgota_tentativas(fake_rede):
    fake_rede["respostas"] = [_http_error(503), _http_error(503)]

    resultado = PncpClient().request_url(URL, "json", attempts=2)

    assert resultado["status"] == "indisponivel_temporariamente"
    assert resultado["http_status"] == 503
    assert resultado["tentativas"] == 2


def test_erro_4xx_nao_e_retentado(fake_rede):
    fake_rede["respostas"] = [_http_error(400, {"Content-Type": "application/json"}, b'{"msg": "falta uf"}')]

    resultado = PncpClient().request_url(URL, "json")

    assert resultado["status"] == "erro_requisicao"
    assert resultado["tentativas"] == 1
    assert "falta uf" in resultado["previa"]
    assert fake_rede["pausas"] == []


def test_falha_de_rede_retenta_e_depois_reporta_indisponivel(fake_rede):
    fake_rede["respostas"] = [URLError("sem rota"), TimeoutError("lento"), URLError("sem rota")]

    resultado = PncpClient().request_url(URL, "json", attempts=3)

    assert resultado["status"] == "indisponivel"
    assert resultado["erro"] == "sem rota"
    assert resultado["tentativas"] == 3
    assert fake_rede["pausas"] == [1.0, 2.0]
