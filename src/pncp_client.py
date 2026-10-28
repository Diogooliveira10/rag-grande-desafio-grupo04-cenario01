"""Cliente pequeno e reutilizável para as duas APIs do PNCP.

Mantém a separação entre descoberta (`/api/consulta`) e recursos identificados
(`/api/pncp`) sem impor dependências externas ao verificador.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .retry import retry_delay
    from .ssl_context import criar_contexto_ssl
except ImportError:  # permite executar o módulo diretamente
    from retry import retry_delay
    from ssl_context import criar_contexto_ssl


def _preview(body: bytes, content_type: str, limit: int = 240) -> str:
    if "json" in content_type.lower():
        try:
            return json.dumps(json.loads(body), ensure_ascii=False)[:limit]
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return body.decode("utf-8", errors="replace").replace("\x00", " ").strip()[:limit]


@dataclass
class PncpClient:
    """Transporte PNCP com URLs e política de retry centralizadas."""

    host: str = "https://pncp.gov.br"
    user_agent: str = "rag-grande-desafio/1.0"
    read_limit: int = 2 * 1024 * 1024
    retryable_http: frozenset[int] = field(
        default_factory=lambda: frozenset({408, 425, 429, 500, 502, 503, 504})
    )

    @property
    def consulta_base(self) -> str:
        return self.host.rstrip("/") + "/api/consulta"

    @property
    def api_base(self) -> str:
        return self.host.rstrip("/") + "/api/pncp"

    def consulta_url(self, path: str, params: dict[str, Any]) -> str:
        return self.consulta_base + path + "?" + urlencode(params)

    def api_url(self, path: str) -> str:
        return self.api_base + path

    def request_url(
        self,
        url: str,
        kind: str,
        *,
        attempts: int = 3,
        timeout: float = 20.0,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Consulta um endpoint, retentando apenas falhas transitórias."""
        attempts = max(1, attempts)
        accept = (
            "application/json, */*;q=0.1"
            if kind == "json"
            else "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8"
        )
        last_network_error: BaseException | None = None

        for attempt in range(1, attempts + 1):
            request_headers = {
                "Accept": accept,
                "User-Agent": self.user_agent,
                "Cache-Control": "no-cache",
            }
            request_headers.update(headers or {})
            request = Request(
                url,
                headers=request_headers,
            )
            try:
                with urlopen(request, timeout=timeout, context=criar_contexto_ssl()) as response:
                    return self._response_result(response, kind, attempt)

            except HTTPError as error:
                result, retry_after = self._http_error_result(error, url, attempt, attempts)
                if result is None:
                    time.sleep(retry_delay(attempt, retry_after))
                    continue
                return result

            except (TimeoutError, URLError, OSError) as error:
                last_network_error = error
                if attempt < attempts:
                    time.sleep(retry_delay(attempt, None))
                    continue

        return {
            "status": "indisponivel",
            "http_status": None,
            "content_type": "",
            "bytes_lidos": 0,
            "url_final": url,
            "previa": "",
            "erro": str(getattr(last_network_error, "reason", last_network_error)),
            "tentativas": attempts,
        }

    def _response_result(self, response: Any, kind: str, attempt: int) -> dict[str, Any]:
        """Resposta recebida: disponível, truncada, vazia ou JSON inválido."""
        raw = response.read(self.read_limit + 1)
        content_type = response.headers.get("Content-Type", "")
        body = raw[: self.read_limit]
        result: dict[str, Any] = {
            "status": "disponivel",
            "http_status": response.status,
            "content_type": content_type,
            "bytes_lidos": len(body),
            "url_final": response.geturl(),
            "previa": _preview(body, content_type),
            "tentativas": attempt,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
        }

        if len(raw) > self.read_limit:
            result["status"] = "resposta_truncada"
            result["observacao"] = (
                f"resposta excedeu o limite local de {self.read_limit} bytes"
            )
            return result

        if response.status == 204 or not body:
            result["observacao"] = "resposta sem conteúdo"
            return result

        if kind == "json":
            try:
                result["json"] = json.loads(body)
            except json.JSONDecodeError as error:
                result["status"] = "resposta_invalida"
                result["erro"] = f"esperado JSON: {error.msg}"
        return result

    def _http_error_result(
        self, error: HTTPError, url: str, attempt: int, attempts: int
    ) -> tuple[dict[str, Any] | None, str | None]:
        """(resultado, Retry-After) do erro HTTP; resultado None quando vale tentar de novo."""
        response_headers = error.headers
        if error.code == 304:
            return {
                "status": "nao_modificado",
                "http_status": 304,
                "content_type": response_headers.get("Content-Type", "") if response_headers else "",
                "bytes_lidos": 0,
                "url_final": url,
                "etag": response_headers.get("ETag") if response_headers else None,
                "last_modified": response_headers.get("Last-Modified") if response_headers else None,
                "tentativas": attempt,
            }, None
        body = error.read(4096)
        content_type = response_headers.get("Content-Type", "") if response_headers else ""
        retry_after = response_headers.get("Retry-After") if response_headers else None
        if error.code in self.retryable_http and attempt < attempts:
            return None, retry_after
        if error.code in self.retryable_http:
            status = "indisponivel_temporariamente"
        elif 400 <= error.code < 500:
            status = "erro_requisicao"
        else:
            status = "erro_http"
        return {
            "status": status,
            "http_status": error.code,
            "retry_after": retry_after,
            "content_type": content_type,
            "bytes_lidos": len(body),
            "url_final": url,
            "previa": _preview(body, content_type),
            "erro": str(error.reason),
            "tentativas": attempt,
            "etag": response_headers.get("ETag") if response_headers else None,
            "last_modified": response_headers.get("Last-Modified") if response_headers else None,
        }, retry_after
