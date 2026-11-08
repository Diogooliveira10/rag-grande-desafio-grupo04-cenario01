"""Requisições REST das fontes: URL, cabeçalhos condicionais, paginação, registros da página e estimativa de requisições."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit


from src.collection.checks import source_check
from src.collection.selection import _VersionContext


def _clean_url(value: str) -> str:
    value = str(value).strip()
    return value[1:-1] if value.startswith("<") and value.endswith(">") else value


def _safe_component(value: str, fallback: str = "item") -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return normalized or fallback


def _estimate_base_requests(base: dict[str, Any]) -> int:
    access = base.get("acesso") or {}
    kind = str(access.get("tipo", "")).lower()
    if kind in {"arquivo_local", "processamento_local"}:
        return 0
    if kind in {"http_pdf", "http_misto", "http_csv"}:
        urls = access.get("urls") or ([access.get("url")] if access.get("url") else [])
        return len([url for url in urls if url])
    if kind in {"resource_set", "pncp_resource_set"}:
        resources = access.get("recursos") or access.get("resources") or []
        return len(resources)
    if kind == "provider":
        return max(1, len(access.get("sources") or []))
    endpoints = access.get("endpoints") or {"principal": access.get("endpoint")}
    endpoint_count = len([endpoint for endpoint in endpoints.values() if endpoint])
    if kind == "rest_api_mais_arquivo" and any("{" in str(endpoint) for endpoint in endpoints.values() if endpoint):
        params = access.get("params") or {}
        try:
            pages = int(params.get("maxPaginas", 1))
        except (TypeError, ValueError):
            pages = 1
        try:
            target = int(base.get("volume_alvo", 1))
        except (TypeError, ValueError):
            target = 1
        return max(1, pages) + max(0, target)
    return max(1, endpoint_count)


def _conditional_headers(record: dict[str, Any] | None) -> dict[str, str]:
    if not record:
        return {}
    headers: dict[str, str] = {}
    if record.get("etag"):
        headers["If-None-Match"] = str(record["etag"])
    if record.get("last_modified"):
        headers["If-Modified-Since"] = str(record["last_modified"])
    return headers


def _extension_for(content_type: str, url: str, default: str = ".bin") -> str:
    content_type = content_type.lower().split(";", 1)[0]
    by_type = {"application/json": ".json", "application/pdf": ".pdf", "text/html": ".html", "application/xhtml+xml": ".html", "application/x-ndjson": ".jsonl", "application/jsonl": ".jsonl"}
    if content_type in by_type:
        return by_type[content_type]
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".json", ".jsonl", ".pdf", ".html", ".htm"} else default


def _build_request_url(endpoint: str, params: dict[str, Any]) -> str:
    endpoint = _clean_url(endpoint)
    split = urlsplit(endpoint)
    query: dict[str, Any] = {}
    if split.query:
        for part in split.query.split("&"):
            key, separator, value = part.partition("=")
            query[key] = value if separator else ""
    date_fields = {"dataInicial", "dataFinal", "dataInicio", "dataFim"}
    query.update(
        {
            str(key): (
                value.replace("-", "")
                if str(key) in date_fields
                and isinstance(value, str)
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
                else value
            )
            for key, value in params.items()
            if value is not None
        }
    )
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _payload_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "resultado", "content"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _selection_limit(base: dict[str, Any]) -> int | None:
    selection = base.get("selecao") or {}
    raw_limit = selection.get("limite") or selection.get("limit")
    if raw_limit in (None, ""):
        return None
    return max(1, int(raw_limit))


def _selection_key(record: Any, base: dict[str, Any]) -> str:
    selection = base.get("selecao") or {}
    field = str(selection.get("chave") or selection.get("key") or "")
    if isinstance(record, dict) and field and record.get(field) not in (None, ""):
        return str(record[field])
    return json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)


def _payload_with_records(payload: Any, records: list[Any]) -> Any:
    if isinstance(payload, list):
        return records
    if isinstance(payload, dict):
        result = dict(payload)
        for key in ("data", "items", "results", "content"):
            if isinstance(payload.get(key), list):
                result[key] = records
                return result
    return {"data": records}


def _rest_pagination(access: dict[str, Any], params: dict[str, Any]) -> tuple[str, int, str, int]:
    """(campo da página, tamanho da página, campo do tamanho, primeira página) declarados no acesso."""
    pagination = access.get("paginacao") or (
        {"campo": "pagina", "tamanho": access.get("params", {}).get("tamanhoPagina", 50)}
        if "pagina" in params
        else {}
    )
    page_field = str(
        pagination.get("campo") or pagination.get("campo_pagina") or "pagina"
    ) if pagination else ""
    page_size = int(
        pagination.get("tamanho")
        or pagination.get("campo_tamanho")
        or params.get("tamanhoPagina")
        or access.get("tamanho_pagina")
        or 50
    )
    size_field = str(pagination.get("campo_tamanho") or "tamanhoPagina") if page_field else ""
    page = int(params.get(page_field, 1) or 1) if page_field else 1
    return page_field, page_size, size_field, page


def _request_rest_page(
    url: str,
    previous: dict[str, Any] | None,
    fallback_factory: Callable[[], dict[str, Any]] | None,
    fallback_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool, bool, str]:
    """Resposta da página: PNCP ou, quando ele falha ou o disjuntor está aberto, o Compras.gov.br.

    Devolve (resposta, fallback usado, fallback tentado, o que disparou o fallback).
    Duas falhas de transporte seguidas abrem o disjuntor e as próximas páginas vão direto ao fallback.
    """
    fallback_used = False
    fallback_attempted = False
    fallback_trigger = ""
    circuit_open = bool(fallback_state and fallback_state.get("circuit_open"))
    if circuit_open and fallback_factory:
        fallback_attempted = True
        fallback_trigger = "circuit_breaker_open"
        response = fallback_factory()
        fallback_used = (
            response.get("status") in {"disponivel", "nao_modificado"}
            and response.get("json") is not None
        )
    else:
        response = source_check.request_url(
            url,
            "json",
            attempts=1 if fallback_factory else 3,
            timeout=15.0 if fallback_factory else 30.0,
            headers=(
                {}
                if previous and previous.get("acquisition_backend") == "compras_dados_abertos"
                else _conditional_headers(previous)
            ),
        )
        if fallback_factory and response.get("status") in {
            "indisponivel",
            "indisponivel_temporariamente",
        }:
            fallback_attempted = True
            fallback_trigger = str(response.get("status"))
            if fallback_state is not None:
                failures = int(fallback_state.get("consecutive_transport_failures", 0)) + 1
                fallback_state["consecutive_transport_failures"] = failures
                if failures >= 2:
                    fallback_state["circuit_open"] = True
            primary_error = str(response.get("erro") or response.get("status"))
            alternate = fallback_factory()
            if (
                alternate.get("status") in {"disponivel", "nao_modificado"}
                and alternate.get("json") is not None
            ):
                response = alternate
                fallback_used = True
            else:
                alternate_error = str(alternate.get("erro") or alternate.get("status"))
                response["erro"] = f"PNCP: {primary_error}; fallback Compras.gov.br: {alternate_error}"
        elif fallback_state is not None:
            fallback_state["consecutive_transport_failures"] = 0
    return response, fallback_used, fallback_attempted, fallback_trigger


def _annotate_saved_page(
    context: _VersionContext,
    saved: dict[str, Any],
    previous: dict[str, Any] | None,
    response: dict[str, Any],
    fallback_used: bool,
    fallback_trigger: str,
) -> None:
    """Reuso da versão anterior, cabeçalhos de cache e, se veio do fallback, a origem alternativa."""
    if previous and previous.get("sha256") == saved.get("sha256"):
        saved["reutilizado_de"] = f"v{context.previous_version}" if context.previous_version else previous.get("path")
        saved["reuso_motivo"] = "SHA-256 idêntico"
    for key in ("etag", "last_modified", "url_final"):
        if response.get(key) and not (
            fallback_used and key in {"etag", "last_modified"}
        ):
            saved[key] = response[key]
    if fallback_used:
        saved["acquisition_backend"] = "compras_dados_abertos"
        saved["fallback_from"] = "pncp"
        saved["fallback_trigger"] = fallback_trigger
