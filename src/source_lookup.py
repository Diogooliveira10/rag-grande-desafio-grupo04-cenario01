"""Busca remota em fontes já suportadas pelo projeto."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

try:
    from .providers.pncp import PNCPProvider
except ImportError:
    from providers.pncp import PNCPProvider


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _record_text(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, default=str).casefold()


def _matches(record: dict[str, Any], query: str) -> bool:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return True
    text = _record_text(record)
    return all(term in text for term in terms)


def _nested_urls(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in {"url", "uri", "location", "urlarquivo", "urlarquivo"} and isinstance(nested, str):
                if nested.startswith(("http://", "https://")):
                    found.append(nested)
            else:
                found.extend(_nested_urls(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_nested_urls(nested))
    return list(dict.fromkeys(found))


def _document_rows(payload: Any) -> list[dict[str, Any]]:
    try:
        from .source_catalog import records_from_payload
    except ImportError:
        from source_catalog import records_from_payload

    rows: list[dict[str, Any]] = []
    for record in records_from_payload(payload):
        urls = _nested_urls(record)
        if not urls:
            continue
        rows.append(
            {
                "name": _first(
                    record,
                    "nomeArquivo",
                    "nome",
                    "titulo",
                    "descricao",
                    "fileName",
                    "filename",
                )
                or urls[0].rsplit("/", 1)[-1]
                or "documento",
                "url": urls[0],
                "tipo": _first(record, "tipoArquivo", "tipo", "contentType", "mimeType"),
                "metadata": record,
            }
        )
    return rows


def lookup_pncp(
    *,
    query: str,
    year: int,
    uf: str,
    modality: int | None = 1,
    page_size: int = 50,
    max_pages: int = 5,
    max_results: int = 20,
    data_initial: str | None = None,
    data_final: str | None = None,
    attempts: int = 3,
    timeout: float = 30.0,
    rate_limit_seconds: float = 1.0,
) -> dict[str, Any]:
    """Consulta publicações PNCP e filtra os registros localmente.

    A função não grava arquivos, não altera o manifesto e limita paginação para
    evitar que uma busca interativa se transforme em uma coleta completa.
    """
    try:
        from . import source_catalog
    except ImportError:
        import source_catalog

    provider = PNCPProvider()

    page_size = max(1, min(int(page_size), 100))
    max_pages = max(1, int(max_pages))
    max_results = max(1, int(max_results))
    start = data_initial or f"{year}-01-01"
    end = data_final or f"{year}-12-31"
    filters = {
        "dataInicial": start,
        "dataFinal": end,
        "uf": uf.strip().upper(),
        "tamanhoPagina": page_size,
    }
    if modality is not None:
        filters["codigoModalidadeContratacao"] = modality

    matches: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    pages_consulted = 0
    request = _paced_request(source_catalog, attempts=attempts, timeout=timeout, rate_limit_seconds=rate_limit_seconds)

    for page in range(1, max_pages + 1):
        page_params = {**filters, "pagina": page}
        url = source_catalog.make_consulta_url("/v1/contratacoes/publicacao", page_params)
        result = request(url)
        pages_consulted += 1
        if not source_catalog._is_available(result):
            errors.append(
                {
                    "page": page,
                    "url": url,
                    "status": result.get("status"),
                    "error": result.get("erro") or result.get("observacao") or "consulta indisponível",
                }
            )
            break

        records = source_catalog.records_from_payload(result.get("json"))
        if not records:
            break
        for record in records:
            if not _matches(record, query):
                continue
            matches.append(_lookup_match(record, url, request, provider, source_catalog, errors))
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results or len(records) < page_size:
            break

    return {
        "source": "pncp",
        "query": query,
        "filters": filters,
        "pages_consulted": pages_consulted,
        "results": matches,
        "errors": errors,
    }


def _paced_request(
    source_catalog: Any, *, attempts: int, timeout: float, rate_limit_seconds: float
) -> Callable[[str], dict[str, Any]]:
    """Consulta ao PNCP que espera ``rate_limit_seconds`` desde o fim da anterior."""
    last_request_at: float | None = None

    def request(url: str) -> dict[str, Any]:
        nonlocal last_request_at
        if last_request_at is not None and rate_limit_seconds > 0:
            delay = rate_limit_seconds - (time.monotonic() - last_request_at)
            if delay > 0:
                time.sleep(delay)
        result = source_catalog.request_url(url, "json", attempts=attempts, timeout=timeout)
        last_request_at = time.monotonic()
        return result

    return request


def _lookup_match(
    record: dict[str, Any],
    page_url: str,
    request: Callable[[str], dict[str, Any]],
    provider: PNCPProvider,
    source_catalog: Any,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resultado da busca para uma publicação, com os documentos da compra quando ela é identificável."""
    purchase = source_catalog.sample_purchase([record])
    source_ref = provider.purchase_ref(purchase) if purchase else None
    detail_url = None
    documents: list[dict[str, Any]] = []
    if purchase:
        detail_url = source_catalog.make_api_url(
            f"/v1/orgaos/{purchase['cnpj']}/compras/{purchase['ano']}/{purchase['sequencial']}"
        )
        documents_url = detail_url + "/arquivos"
        documents_result = request(documents_url)
        if source_catalog._is_available(documents_result):
            documents = _document_rows(documents_result.get("json"))
        else:
            errors.append(
                {
                    "external_id": _first(record, "numeroControlePNCP", "numeroControlePncp"),
                    "url": documents_url,
                    "status": documents_result.get("status"),
                    "error": documents_result.get("erro") or "documentos indisponíveis",
                }
            )
    return {
        "source_id": "pncp",
        "external_id": _first(
            record,
            "numeroControlePNCP",
            "numeroControlePncp",
            "numeroCompra",
            "sequencialCompra",
        )
        or (f"{purchase['cnpj']}:{purchase['ano']}:{purchase['sequencial']}" if purchase else None),
        "title": _first(
            record,
            "objetoCompra",
            "objeto",
            "descricaoObjeto",
            "nomeCompra",
            "numeroCompra",
        )
        or "Contratação PNCP",
        "url": detail_url or page_url,
        "document_urls": [document["url"] for document in documents],
        "documents": documents,
        "source_ref": source_ref.to_dict() if source_ref else None,
        "provider": "pncp",
        "metadata": record,
    }
