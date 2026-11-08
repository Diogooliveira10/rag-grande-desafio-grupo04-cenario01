"""A coleta em si: itens HTTP, endpoints REST paginados, bases locais, documentos do PNCP,
conjuntos de recursos e fontes de provedores.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


from src.providers import get_provider
from src.providers.base import SourceRef
from src.collection import paths as coleta
from src.collection.checkpoint import _checkpoint_write, _record_pncp_discovery_page
from src.collection.checks import _file_urls, _run_checks_for_types, iter_candidates, source_check
from src.collection.paths import _path_from_record, _relative_path
from src.collection.rest import _annotate_saved_page, _build_request_url, _clean_url, _conditional_headers, _extension_for, _payload_records, _payload_with_records, _request_rest_page, _rest_pagination, _safe_component, _selection_key, _selection_limit
from src.collection.selection import _VersionContext, _discovery_configs_compatible, _pncp_discovery_config, _set_selection, _validate_census_configuration
from src.collection.sources import _document_metadata_by_url, _normalized_ascii, _population_query, _previous_record, _provider_populations, _resumable_sources, _resumed_records
from src.collection.storage import _base_record, _completed_item, _failed_record, _materialize_previous_record, _save_version_bytes, _wait_rate_limit


def _collect_http_item(
    context: _VersionContext,
    base: dict[str, Any],
    url: str,
    logical_id: str,
    filename_prefix: str,
    *,
    expected_type: str | None = None,
) -> dict[str, Any]:
    existing = _completed_item(context, logical_id)
    if existing:
        existing["unidades_logicas"] = 1
        return existing
    previous = _previous_record(context, logical_id)
    conditional_headers = _conditional_headers(previous)
    _wait_rate_limit(context, base)
    download_dir = context.staging_path / ".downloads" / _safe_component(base["base_id"])
    try:
        result = source_check.download_url(
            url,
            download_dir,
            filename_prefix,
            attempts=3,
            timeout=30.0,
            headers=conditional_headers,
            deduplicate=False,
        )
        if result.get("status") == "nao_modificado" and previous:
            previous_path = _path_from_record(previous)
            extension = previous_path.suffix if previous_path is not None else _extension_for("", url)
            saved = _materialize_previous_record(context, str(base["base_id"]), filename_prefix, previous, extension)
            if saved is not None:
                for key in ("etag", "last_modified"):
                    if result.get(key):
                        saved[key] = result[key]
                record = _base_record(context, base, logical_id, url, saved, tipo=Path(str(saved["path"])).suffix.lstrip(".") or "bin", registros=int(previous.get("registros", 0) or 0))
                record["unidades_logicas"] = 1
                return record
        if result.get("status") not in {"baixado", "reutilizado"}:
            return _failed_record(context, base, logical_id, url, str(result.get("erro") or result.get("observacao") or result.get("status")), _extension_for(str(result.get("content_type", "")), url).lstrip("."))
        source_path = Path(str(result.get("arquivo", "")))
        if not source_path.is_absolute():
            source_path = coleta.PROJECT_ROOT / source_path
        if not source_path.exists():
            return _failed_record(context, base, logical_id, url, "download sem arquivo resultante")
        extension = source_path.suffix or _extension_for(str(result.get("content_type", "")), url)
        if expected_type and extension in {"", ".bin"}:
            extension = f".{expected_type.lstrip('.') }"
        saved = _save_version_bytes(context, source_path.read_bytes(), str(base["base_id"]), filename_prefix, extension)
        if previous and previous.get("sha256") == saved.get("sha256"):
            saved["reutilizado_de"] = f"v{context.previous_version}" if context.previous_version else previous.get("path")
            saved["reuso_motivo"] = "SHA-256 idêntico"
        for key in ("etag", "last_modified", "url_final"):
            if result.get(key):
                saved[key] = result[key]
        source_path.unlink(missing_ok=True)
        record = _base_record(context, base, logical_id, url, saved, tipo=Path(str(saved["path"])).suffix.lstrip(".") or "bin")
        record["unidades_logicas"] = 1
        return record
    except Exception as exc:
        return _failed_record(context, base, logical_id, url, f"{type(exc).__name__}: {exc}")


def _collect_selected_rest_endpoint(
    context: _VersionContext,
    base: dict[str, Any],
    endpoint_name: str,
    endpoint: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Coleta páginas completas e persiste somente a seleção canônica."""
    endpoint = _clean_url(endpoint)
    all_records, last_payload, falha = _ler_paginas_da_selecao(context, base, endpoint_name, endpoint)
    if falha is not None:
        return [falha]
    return _gravar_selecao(context, base, endpoint_name, endpoint, limit, all_records, last_payload)


def _paginacao_da_selecao(access: dict[str, Any], params: dict[str, Any]) -> tuple[str, int]:
    """Campo e tamanho da página declarados no acesso (padrão: ``pagina``, 50)."""
    pagination = access.get("paginacao") or {
        "campo": "pagina",
        "tamanho": params.get("tamanhoPagina", 50),
    }
    page_field = str(pagination.get("campo") or "pagina")
    page_size = max(
        1,
        int(
            pagination.get("tamanho")
            or params.get("tamanhoPagina")
            or access.get("tamanho_pagina")
            or 50
        ),
    )
    return page_field, page_size


def _ler_paginas_da_selecao(
    context: _VersionContext, base: dict[str, Any], endpoint_name: str, endpoint: str,
) -> tuple[list[Any], Any, dict[str, Any] | None]:
    """Lê as páginas até a última incompleta, repetida ou ``max_paginas``.

    Devolve os registros lidos, o último payload e, se uma página veio sem
    JSON, o registro da falha (aí a seleção não é gravada).
    """
    access = base.get("acesso", {})
    params = dict(access.get("params") or {})
    page_field, page_size = _paginacao_da_selecao(access, params)
    max_pages = max(1, int((base.get("selecao") or {}).get("max_paginas") or 100))
    all_records: list[Any] = []
    seen_pages: set[str] = set()
    last_payload: Any = {"data": []}
    for page in range(1, max_pages + 1):
        request_params = dict(params)
        request_params[page_field] = page
        url = _build_request_url(endpoint, request_params)
        response = source_check.request_url(url, "json", attempts=3, timeout=30.0)
        payload = response.get("json")
        if payload is None:
            return all_records, last_payload, _failed_record(
                context,
                base,
                f"{base['base_id']}:{endpoint_name}:selecao",
                url,
                str(response.get("erro") or response.get("status") or "resposta JSON ausente"),
                "json",
            )
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest in seen_pages:
            break
        seen_pages.add(digest)
        last_payload = payload
        records = _payload_records(payload)
        all_records.extend(records)
        if not records or len(records) < page_size:
            break
    return all_records, last_payload, None


def _gravar_selecao(
    context: _VersionContext, base: dict[str, Any], endpoint_name: str, endpoint: str, limit: int,
    all_records: list[Any], last_payload: Any,
) -> list[dict[str, Any]]:
    """Grava os ``limit`` primeiros registros pela chave canônica; avisa se o alvo não foi atingido."""
    unique = {_selection_key(record, base): record for record in all_records}
    selected = [unique[key] for key in sorted(unique)[:limit]]
    selected_payload = _payload_with_records(last_payload, selected)
    saved = _save_version_bytes(
        context,
        json.dumps(selected_payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        str(base["base_id"]),
        f"{endpoint_name}_selecionado",
        ".json",
    )
    logical_id = f"{base['base_id']}:{endpoint_name}:selecionado"
    record = _base_record(
        context,
        base,
        logical_id,
        endpoint,
        saved,
        tipo="json",
        registros=len(selected),
    )
    record["selecao"] = {
        "limite": limit,
        "observados": len(all_records),
        "deduplicados": len(unique),
    }
    _checkpoint_write(context)
    output = [record]
    if len(selected) < limit:
        output.append(
            _failed_record(
                context,
                base,
                f"{base['base_id']}:{endpoint_name}:alvo",
                endpoint,
                f"alvo de {limit} registros não atingido; somente {len(selected)} selecionados",
                "json",
            )
        )
    return output


def _unmodified_page_record(
    context: _VersionContext,
    base: dict[str, Any],
    logical_id: str,
    url: str,
    filename_prefix: str,
    previous: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any] | None:
    """Página que o servidor disse não ter mudado (304): o arquivo anterior vira o desta versão."""
    saved = _materialize_previous_record(context, str(base["base_id"]), filename_prefix, previous, ".json")
    if saved is None:
        return None
    saved["registros"] = int(previous.get("registros", 0) or 0)
    for key in ("etag", "last_modified"):
        if response.get(key):
            saved[key] = response[key]
    return _base_record(context, base, logical_id, url, saved, tipo="json", registros=int(previous.get("registros", 0) or 0))


def _rest_page_failure(
    context: _VersionContext,
    base: dict[str, Any],
    logical_id: str,
    url: str,
    reason: str,
    fallback_attempted: bool,
    fallback_trigger: str,
) -> dict[str, Any]:
    """Falha da página; quando o fallback foi tentado, fica registrada no checkpoint com o motivo."""
    failure = _failed_record(context, base, logical_id, url, reason, "json")
    if fallback_attempted:
        failure["fallback_backend"] = "compras_dados_abertos"
        failure["fallback_trigger"] = fallback_trigger
        context.items[logical_id] = failure
        _checkpoint_write(context)
    return failure


def _collect_rest_endpoint(
    context: _VersionContext,
    base: dict[str, Any],
    endpoint_name: str,
    endpoint: str,
    *,
    fallback_factory: Callable[[], dict[str, Any]] | None = None,
    fallback_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    endpoint = _clean_url(endpoint)
    if "{" in endpoint or "}" in endpoint:
        return [_failed_record(context, base, f"{base['base_id']}:{endpoint_name}:template", endpoint, "endpoint possui placeholders e precisa de adapter de descoberta PNCP", "json")]
    selection_limit = _selection_limit(base)
    if selection_limit is not None:
        return _collect_selected_rest_endpoint(context, base, endpoint_name, endpoint, selection_limit)
    access = base.get("acesso", {})
    params = dict(access.get("params") or {})
    page_field, page_size, size_field, page = _rest_pagination(access, params)
    output: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    while True:
        request_params = dict(params)
        if page_field:
            request_params[page_field] = page
        url = _build_request_url(endpoint, request_params)
        logical_id = f"{base['base_id']}:{endpoint_name}:pagina={page}"
        existing = _completed_item(context, logical_id)
        if existing:
            output.append(existing)
            if not page_field or int(existing.get("registros", 0) or 0) < page_size:
                break
            page += 1
            continue
        previous = _previous_record(context, logical_id)
        if previous and previous.get("origem") != url:
            previous = None
        _wait_rate_limit(context, base)
        try:
            response, fallback_used, fallback_attempted, fallback_trigger = _request_rest_page(
                url, previous, fallback_factory, fallback_state
            )
            unmodified = (
                _unmodified_page_record(context, base, logical_id, url, f"{endpoint_name}_pagina_{page}", previous, response)
                if response.get("status") == "nao_modificado" and previous
                else None
            )
            if unmodified is not None:
                output.append(unmodified)
                if not page_field or int(previous.get("registros", 0) or 0) < page_size:
                    break
                page += 1
                continue
            payload = response.get("json")
            if payload is None:
                if response.get("status") == "disponivel" and response.get("http_status") == 204:
                    payload = []
                else:
                    reason = _motivo_sem_json(response)
                    if (
                        page_field
                        and size_field
                        and page_size > 1
                        and "limite local" in str(reason).casefold()
                    ):
                        page_size = max(1, page_size // 2)
                        params[size_field] = page_size
                        continue
                    output.append(_rest_page_failure(context, base, logical_id, url, str(reason), fallback_attempted, fallback_trigger))
                    break
            record, records = _salvar_pagina(
                context, base, endpoint_name, page, logical_id, url, payload, seen_pages,
                previous, response, fallback_used, fallback_trigger,
            )
            output.append(record)
            if records is None:
                break  # página repetida: a enumeração para aqui
            if fallback_used or not page_field or not records or len(records) < page_size:
                break
            page += 1
        except Exception as exc:
            output.append(_failed_record(context, base, logical_id, url, f"{type(exc).__name__}: {exc}", "json"))
            break
    return output


def _motivo_sem_json(response: dict[str, Any]) -> str:
    """Por que a página não trouxe JSON: o erro, a observação ou o status e o HTTP."""
    return (
        response.get("erro")
        or response.get("observacao")
        or f"resposta JSON ausente (status={response.get('status')}, HTTP={response.get('http_status')})"
    )


def _salvar_pagina(
    context: _VersionContext, base: dict[str, Any], endpoint_name: str, page: int, logical_id: str, url: str,
    payload: Any, seen_pages: set[str], previous: dict[str, Any] | None, response: dict[str, Any],
    fallback_used: bool, fallback_trigger: Any,
) -> tuple[dict[str, Any], list[Any] | None]:
    """Grava a página e devolve o registro e os registros lidos dela.

    Página idêntica a uma já vista (mesmo SHA-256) não é gravada: volta o
    registro de falha e ``None``, e quem pagina para a enumeração.
    """
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    page_digest = hashlib.sha256(body).hexdigest()
    if page_digest in seen_pages:
        return _failed_record(
            context,
            base,
            f"{base['base_id']}:{endpoint_name}:pagina={page}:duplicada",
            url,
            "página repetida durante paginação; enumeração interrompida",
            "json",
        ), None
    seen_pages.add(page_digest)
    saved = _save_version_bytes(context, body, str(base["base_id"]), f"{endpoint_name}_pagina_{page}", ".json")
    _annotate_saved_page(context, saved, previous, response, fallback_used, fallback_trigger)
    records = _payload_records(payload)
    return _base_record(context, base, logical_id, url, saved, tipo="json", registros=len(records)), records


def _collect_local_base(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    access = base.get("acesso", {})
    origin = access.get("origem") or base.get("local")
    if not origin:
        return [_failed_record(context, base, f"{base['base_id']}:local", "local", "origem local não declarada")]
    source = coleta.PROJECT_ROOT / str(origin)
    paths = sorted(source.rglob("*") if source.is_dir() else [source])
    paths = [path for path in paths if path.is_file()]
    if not paths:
        return [_failed_record(context, base, f"{base['base_id']}:local", str(source), "nenhum arquivo local encontrado")]
    output: list[dict[str, Any]] = []
    for path in paths:
        logical_id = f"{base['base_id']}:{_relative_path(path)}"
        existing = _completed_item(context, logical_id)
        if existing:
            output.append(existing)
            continue
        try:
            body = path.read_bytes()
            formato = str(base.get("formato") or path.suffix.lstrip(".")).lower()
            if formato == "jsonl" and path.suffix.lower() == ".json":
                payload = json.loads(body.decode("utf-8"))
                records = payload if isinstance(payload, list) else payload.get("data", [payload]) if isinstance(payload, dict) else [payload]
                body = b"".join(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n" for record in records)
                extension, registros = ".jsonl", len(records)
            else:
                extension, registros = path.suffix or ".bin", 1
            saved = _save_version_bytes(context, body, str(base["base_id"]), path.stem, extension)
            output.append(_base_record(context, base, logical_id, _relative_path(path), saved, tipo=extension.lstrip("."), registros=registros))
        except Exception as exc:
            output.append(_failed_record(context, base, logical_id, _relative_path(path), f"{type(exc).__name__}: {exc}"))
    return output


def _collect_pncp_documents(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    """Descobre e baixa documentos PNCP até cumprir o alvo declarado.

    A descoberta usa páginas sucessivas da API. O limite é controlado pelo
    manifesto para evitar que uma fonte indisponível provoque uma coleta
    infinita. Cada URL é baixada no mesmo contexto de checkpoint da versão.
    """
    params = (base.get("acesso") or {}).get("params") or {}
    year, page_size, target, max_pages = _limites_da_descoberta_pncp(params, base)

    output: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    page = 1
    while page <= max_pages:
        _wait_rate_limit(context, base)
        config = source_check.CheckConfig(
            ano=year,
            uf=str(params.get("uf", "RJ")).upper(),
            modalidade=params.get("codigoModalidadeContratacao"),
            tamanho_pagina=page_size,
            pagina=page,
            timeout=30.0,
            attempts=3,
            data_inicial=params.get("dataInicial"),
            data_final=params.get("dataFinal"),
        )
        _, results = _run_checks_for_types(
            config,
            ("pdf",),
            before_request=lambda: _wait_rate_limit(context, base),
        )
        candidates = [candidate for candidate in iter_candidates(results) if candidate.folder == "pncp"]
        new_candidates = [candidate for candidate in candidates if candidate.url not in seen_urls]
        for candidate in new_candidates:
            seen_urls.add(candidate.url)
            output.append(
                _collect_http_item(
                    context,
                    base,
                    candidate.url,
                    f"{base['base_id']}:{candidate.candidate_id}",
                    candidate.filename_prefix,
                )
            )
            if _pdfs_baixados(output) >= target:
                return output
        if not new_candidates:
            break
        page += 1

    pdf_successes = _pdfs_baixados(output)
    if not output:
        return [_failed_record(context, base, f"{base['base_id']}:discovery", "PNCP", "nenhum arquivo PNCP foi descoberto")]
    if pdf_successes < target:
        output.append(
            _failed_record(
                context,
                base,
                f"{base['base_id']}:target",
                "PNCP",
                f"alvo de {target} PDFs não atingido; somente {pdf_successes} foram coletados",
                "pdf",
            )
        )
    return output


def _limites_da_descoberta_pncp(params: dict[str, Any], base: dict[str, Any]) -> tuple[int, int, int, int]:
    """Ano, tamanho da página, alvo de PDFs e máximo de páginas; valor inválido no manifesto volta ao padrão."""
    try:
        year = int(params.get("ano", 2025))
        page_size = max(1, int(params.get("tamanhoPagina", 10)))
        target = max(1, int(base.get("volume_alvo", 1)))
    except (TypeError, ValueError):
        year, page_size, target = 2025, 10, 1

    try:
        max_pages = max(
            1,
            int(params.get("maxPaginas", (target + page_size - 1) // page_size + 2)),
        )
    except (TypeError, ValueError):
        max_pages = (target + page_size - 1) // page_size + 2
    return year, page_size, target, max_pages


def _pdfs_baixados(output: list[dict[str, Any]]) -> int:
    return sum(1 for record in output if record.get("status") == "ok" and record.get("tipo") == "pdf")


def _collect_resource_set(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    """Coleta um conjunto pequeno e determinístico de recursos declarados."""
    access = base.get("acesso") or {}
    resources = access.get("recursos") or access.get("resources") or []
    output: list[dict[str, Any]] = []
    for index, resource in enumerate(resources, start=1):
        if isinstance(resource, str):
            resource = {"url": resource}
        if not isinstance(resource, dict) or not resource.get("url"):
            output.append(
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:resource:{index}",
                    "resource_set",
                    "recurso sem URL declarada",
                )
            )
            continue

        url = _clean_url(str(resource["url"]))
        resource_id = _safe_component(str(resource.get("id") or f"{index:03d}"))
        kind = str(resource.get("tipo") or _extension_for("", url)).lower().lstrip(".")
        logical_id = f"{base['base_id']}:resource:{resource_id}"
        if kind == "json":
            resource_access = {
                **access,
                "endpoint": url,
                "params": resource.get("params") or {},
                "paginacao": resource.get("paginacao") or {},
            }
            resource_base = {**base, "acesso": resource_access}
            output.extend(
                _collect_rest_endpoint(
                    context,
                    resource_base,
                    resource_id,
                    url,
                )
            )
        else:
            output.append(
                _collect_http_item(
                    context,
                    base,
                    url,
                    logical_id,
                    f"{base['base_id']}_{resource_id}",
                    expected_type=kind,
                )
            )
    return output or [
        _failed_record(
            context,
            base,
            f"{base['base_id']}:resource-set",
            "resource_set",
            "nenhum recurso declarado",
        )
    ]


def _attach_discovery_checkpoint(
    context: _VersionContext,
    base: dict[str, Any],
    population: dict[str, Any],
    query: dict[str, Any],
    verified_unit_scopes: list[dict[str, Any]],
    *,
    discovery_only: bool,
    max_pages: int | None,
) -> dict[str, Any]:
    """Liga a consulta ao estado da descoberta no checkpoint, que registra cada página; devolve o estado."""
    resource_name = str(query["resource_type"]).casefold()
    resource_key = str(
        population.get("discovery_key")
        or (
            "contratacao_publicacao_rj"
            if resource_name in {"contratacao", "contratação"}
            else (
                "contrato_assinado_rj_unidades"
                if resource_name in {"contrato_assinado", "contrato_pncp"}
                else resource_name
            )
        )
    )
    discovery_populations = context.discovery.setdefault(str(base["base_id"]), {})
    discovery_state = discovery_populations.setdefault(resource_key, {})
    discovery_config = _pncp_discovery_config(base, population)
    if verified_unit_scopes:
        discovery_config["verified_unit_scopes"] = verified_unit_scopes
    existing_config = discovery_state.get("config")
    if existing_config is not None and not _discovery_configs_compatible(
        existing_config, discovery_config
    ):
        raise RuntimeError(
            f"discovery {base['base_id']}/{resource_key} incompatível com os parâmetros atuais"
        )
    discovery_state["config"] = discovery_config
    discovery_state["total_contratos_elegiveis"] = len(
        discovery_state.get("eligible_sources") or {}
    )
    _checkpoint_write(context)
    query["discovery_state"] = discovery_state
    if discovery_only:
        query["discovery_only"] = True
        query["max_pages"] = max_pages
    if population.get("verify_discovered_units"):
        query["verify_discovered_units"] = True
    query["on_discovery_page"] = lambda report, base_id=str(base["base_id"]), key=resource_key: _record_pncp_discovery_page(
        context, base_id, key, report
    )
    return discovery_state


def _verify_population_units(
    context: _VersionContext,
    base: dict[str, Any],
    access: dict[str, Any],
    provider: Any,
    discovery_state: dict[str, Any],
) -> None:
    """Confere no cadastro oficial as unidades dos órgãos descobertos, gravando cada órgão no checkpoint."""
    verifier = getattr(provider, "verify_discovered_units", None)
    if not callable(verifier):
        raise RuntimeError("provider não oferece verificação oficial das unidades do órgão")
    unit_state = context.discovery.setdefault(str(base["base_id"]), {}).setdefault(
        "unidades_orgaos_rj", {}
    )
    unit_state["cnpjs_derivados"] = list(discovery_state.get("cnpjs_derivados") or [])
    unit_state["unidades_observadas_por_cnpj"] = dict(
        discovery_state.get("unidades_observadas_por_cnpj") or {}
    )
    unit_state.setdefault("verificacoes_orgaos", {})

    def _save_unit_verification(cnpj: str, result: dict[str, Any]) -> None:
        unit_state.setdefault("verificacoes_orgaos", {})[cnpj] = result
        _checkpoint_write(context)

    verifier(
        unit_state,
        on_organization_verified=_save_unit_verification,
        rate_limit_seg=access.get("rate_limit_seg", 0),
    )
    _checkpoint_write(context)


def _discover_provider_sources(
    context: _VersionContext,
    base: dict[str, Any],
    access: dict[str, Any],
    provider: Any,
    populations: list[dict[str, Any]],
    target: int,
    *,
    discovery_only: bool,
    max_pages: int | None,
) -> list[Any] | None:
    """Candidatos de todas as populações; None quando só a descoberta foi pedida (para após a primeira)."""
    candidates: list[Any] = []
    for population in populations:
        _wait_rate_limit(context, base)
        query, verified_unit_scopes = _population_query(context, base, access, population, target)
        checkpoint_discovery = bool(population.get("censo_nacional")) or bool(
            population.get("discovery_checkpoint")
        )
        if checkpoint_discovery:
            discovery_state = _attach_discovery_checkpoint(
                context, base, population, query, verified_unit_scopes,
                discovery_only=discovery_only, max_pages=max_pages,
            )
        candidates.extend(provider.search(query))
        if checkpoint_discovery:
            if query.get("verify_discovered_units"):
                _verify_population_units(context, base, access, provider, discovery_state)
            discovery_state["total_contratos_elegiveis"] = len(discovery_state.get("eligible_sources") or {})
            _checkpoint_write(context)
        if discovery_only:
            return None
    return candidates


def _collect_provider_base(
    context: _VersionContext,
    base: dict[str, Any],
    *,
    discovery_only: bool = False,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Materializa referências explícitas usando o mesmo provider da curadoria."""
    access = base.get("acesso") or {}
    provider_name = str(access.get("provider") or "").strip()
    if not provider_name:
        return [_failed_record(context, base, f"{base['base_id']}:provider", "provider", "provider não declarado")]

    provider = get_provider(provider_name)
    _descartar_itens_com_falha(context, base)
    target = int(base.get("volume_alvo") or 1)
    populations = _provider_populations(access, target)
    censo_mode = any(bool(population.get("censo")) for population in populations)
    if censo_mode:
        _validate_census_configuration(provider, access, populations)
    sources, resumed_source_ids = _resumable_sources(
        context, base, access, populations, target, discovery_only=discovery_only
    )
    candidates: list[Any] = []
    discovered = False
    if not sources:
        found = _discover_provider_sources(
            context, base, access, provider, populations, target,
            discovery_only=discovery_only, max_pages=max_pages,
        )
        if found is None:
            return []
        candidates = found
        sources = [candidate.source_ref.to_dict() for candidate in candidates]
        discovered = True
        if not sources:
            return [
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:provider:search",
                    provider_name,
                    "nenhuma SourceRef encontrada",
                )
            ]

    preserve_explicit_sources = bool(access.get("preserve_explicit_sources"))
    if not censo_mode and not preserve_explicit_sources:
        sources = sources[:target]
    if discovered or str(base["base_id"]) not in getattr(context, "selection", {}):
        _set_selection(context, str(base["base_id"]), sources)
        _checkpoint_write(context)

    if resumed_source_ids:
        cached_records, sources = _resumed_records(context, base, sources, resumed_source_ids)
        if cached_records is not None:
            return cached_records

    collector = _ProviderSourceCollector(context, base, access, provider, provider_name, candidates)
    return _coletar_fontes(context, base, collector, sources, provider_name,
                           acusar_alvo=discovered and not censo_mode, target=target)


def _descartar_itens_com_falha(context: _VersionContext, base: dict[str, Any]) -> None:
    """Itens desta base que não terminaram ``ok`` saem do contexto, para serem tentados de novo."""
    for logical_id, item in list(context.items.items()):
        if item.get("base_id") == base.get("base_id") and item.get("status") != "ok":
            context.items.pop(logical_id, None)


def _coletar_fontes(context: _VersionContext, base: dict[str, Any], collector: Any, sources: list[Any],
                    provider_name: str, *, acusar_alvo: bool, target: int) -> list[dict[str, Any]]:
    """Coleta cada fonte; a falha de uma vira registro e não interrompe as demais.

    Com ``acusar_alvo``, registra também quando as fontes não chegam ao alvo.
    """
    for index, source_value in enumerate(sources, start=1):
        try:
            collector.collect(index, source_value)
        except Exception as exc:
            collector.output.append(
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:provider:{index}",
                    provider_name,
                    f"{type(exc).__name__}: {exc}",
                )
            )
    if acusar_alvo and len(sources) < target:
        collector.output.append(
            _failed_record(
                context,
                base,
                f"{base['base_id']}:provider:alvo",
                provider_name,
                f"alvo de {target} unidades não atingido; somente {len(sources)} encontradas",
            )
        )
    return collector.output


@dataclass
class _ProviderSourceCollector:
    """Coleta os artefatos de cada fonte de uma base de provider, sem repetir URL nem artefato relacionado."""

    context: _VersionContext
    base: dict[str, Any]
    access: dict[str, Any]
    provider: Any
    provider_name: str
    candidates: list[Any]
    output: list[dict[str, Any]] = field(default_factory=list)
    seen_artifact_urls: set[str] = field(default_factory=set)
    seen_download_urls: set[str] = field(default_factory=set)
    seen_related_artifact_ids: set[str] = field(default_factory=set)
    fallback_state: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.fallback_request = getattr(self.provider, "request_agregada_fallback", None)
        if self.base.get("base_id") == "pca_agregado" and self.provider_name.casefold() == "pncp":
            self.fallback_state = {"consecutive_transport_failures": 0, "circuit_open": False}

    def _keep(self, record: dict[str, Any]) -> None:
        self.context.items[record["logical_id"]] = record
        _checkpoint_write(self.context)
        self.output.append(record)

    def _fallback_options(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Fallback do Compras.gov.br para o artefato, quando a base usa e o artefato declara um."""
        fallback = metadata.get("comprasgov_fallback")
        if self.fallback_state is None or not callable(self.fallback_request) or not isinstance(fallback, dict):
            return {}
        fallback_request = self.fallback_request
        return {
            "fallback_factory": lambda fallback=fallback: fallback_request(fallback),
            "fallback_state": self.fallback_state,
        }

    def _related_base(self, resource_kind: str) -> dict[str, Any] | None:
        """Base declarada em ``artefatos_relacionados`` para o tipo de recurso; None se não houver."""
        related_base_id = str(
            (self.access.get("artefatos_relacionados") or {}).get(resource_kind) or ""
        )
        related_base = next(
            (
                candidate_base
                for candidate_base in self.context.manifest.get("bases", [])
                if str(candidate_base.get("base_id") or "") == related_base_id
            ),
            None,
        ) if related_base_id else None
        if related_base_id and related_base is None:
            raise ValueError(
                f"base relacionada {related_base_id!r} não declarada no manifesto"
            )
        return related_base

    def _endpoint_base(self, artifact_base: dict[str, Any], url: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            **artifact_base,
            # A seleção limita a busca de unidades lógicas; no detalhe,
            # cada artefato já representa uma única unidade resolvida.
            "selecao": {},
            "acesso": {
                **self.access,
                "endpoint": url,
                "params": metadata.get("params") or {},
                "paginacao": metadata.get("paginacao") or {},
            },
        }

    def collect(self, index: int, source_value: dict[str, Any]) -> None:
        source_ref = SourceRef.from_dict(
            {**source_value, "provider": source_value.get("provider", self.provider_name)}
        )
        candidate = next(
            (item for item in self.candidates if item.source_ref.stable_id == source_ref.stable_id),
            None,
        )
        if (
            candidate is not None
            and source_ref.resource_type.casefold() in {"contratacao", "contratação"}
            and candidate.metadata
        ):
            self._publication(source_ref, candidate)
            return
        resolved = self.provider.resolve(source_ref)
        for artifact in self.provider.list_artifacts(resolved):
            if artifact.url in self.seen_artifact_urls:
                continue
            self.seen_artifact_urls.add(artifact.url)
            self._artifact(index, source_ref, artifact)

    def _publication(self, source_ref: SourceRef, candidate: Any) -> None:
        """A compra publicada vem pronta na busca: o próprio registro da publicação vira o artefato."""
        payload = {
            key: value for key, value in candidate.metadata.items() if not str(key).startswith("_")
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        saved = _save_version_bytes(
            self.context,
            body,
            str(self.base["base_id"]),
            f"{_safe_component(source_ref.stable_id)}_publication",
            ".json",
        )
        logical_id = f"{self.base['base_id']}:{source_ref.stable_id}:publication"
        record = _base_record(
            self.context,
            self.base,
            logical_id,
            str(candidate.metadata.get("_source_url") or source_ref.stable_id),
            saved,
            tipo="json",
            registros=1,
        )
        record["source_ref"] = source_ref.to_dict()
        record["artifact_id"] = f"{source_ref.stable_id}:publication"
        record["provider"] = self.provider_name
        record["unidades_logicas"] = 0
        record["resource_kind"] = "contratacao_publicada"
        self._keep(record)

    def _artifact(self, index: int, source_ref: SourceRef, artifact: Any) -> None:
        resource_kind = str(artifact.metadata.get("resource_kind") or "")
        allowed_resource_kinds = {
            str(value) for value in self.access.get("resource_kinds", [])
        }
        if allowed_resource_kinds and resource_kind not in allowed_resource_kinds:
            return
        artifact_base = self._related_base(resource_kind) or self.base
        logical_id = f"{artifact_base['base_id']}:{artifact.artifact_id}"
        if artifact.artifact_type.casefold() == "json":
            records = _collect_rest_endpoint(
                self.context,
                self._endpoint_base(artifact_base, artifact.url, artifact.metadata),
                _safe_component(artifact.artifact_id),
                artifact.url,
                **self._fallback_options(artifact.metadata),
            )
        else:
            records = [
                _collect_http_item(
                    self.context,
                    artifact_base,
                    artifact.url,
                    logical_id,
                    Path(artifact.filename).stem if artifact.filename else f"{self.base['base_id']}_{index:03d}",
                    expected_type=artifact.artifact_type,
                )
            ]
        for record in records:
            record["source_ref"] = source_ref.to_dict()
            record["artifact_id"] = artifact.artifact_id
            record["provider"] = self.provider_name
            resource_kind = artifact.metadata.get("resource_kind") or source_ref.resource_type
            logical_unit = bool(artifact.metadata.get("logical_unit", True))
            if self.base.get("base_id") == "pca_agregado" and resource_kind == "contrato":
                # O contrato é contexto/proveniência para as estatísticas; a
                # unidade lógica da base agregada continua sendo o PCA.
                logical_unit = False
            record["unidades_logicas"] = int(logical_unit)
            record["resource_kind"] = resource_kind
            self._keep(record)
        if resource_kind.startswith("documentos_"):
            self._attachments(index, source_ref, artifact, resource_kind, records)
        related_factory = {
            "itens": getattr(self.provider, "related_result_artifacts", None),
            "atas_compra": getattr(self.provider, "related_ata_artifacts", None),
        }.get(resource_kind)
        if callable(related_factory):
            self._related(source_ref, artifact, artifact_base, related_factory, records)

    def _attachments(
        self, index: int, source_ref: SourceRef, artifact: Any, resource_kind: str, records: list[dict[str, Any]]
    ) -> None:
        """Arquivos anunciados nas listagens de documentos, filtrados pelos termos do edital quando houver."""
        document_base = self._related_base(resource_kind)
        for listing_record in records:
            listing_path = _path_from_record(listing_record)
            if listing_path is None or not listing_path.exists():
                continue
            try:
                listing_payload = json.loads(listing_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            document_filter = (
                self.access.get("documentos_edital_filtro")
                if resource_kind == "documentos_edital"
                else None
            ) or {}
            filter_terms = [
                _normalized_ascii(str(term).casefold())
                for term in document_filter.get("termos", [])
                if str(term).strip()
            ]
            filter_fields = document_filter.get("campos") or [
                "titulo",
                "tipoDocumentoNome",
            ]
            document_metadata_by_url = _document_metadata_by_url(listing_payload, filter_terms, filter_fields)
            for file_index, file_url in _file_urls({"status": "disponivel", "json": listing_payload}):
                if filter_terms and file_url not in document_metadata_by_url:
                    continue
                if file_url in self.seen_download_urls:
                    continue
                self.seen_download_urls.add(file_url)
                self.seen_artifact_urls.add(file_url)
                file_id = f"{artifact.artifact_id}:arquivo:{file_index}"
                destination_base = document_base or self.base
                file_record = _collect_http_item(
                    self.context,
                    destination_base,
                    file_url,
                    f"{destination_base['base_id']}:{_safe_component(file_id)}",
                    f"{_safe_component(resource_kind)}_{index:04d}_{file_index:04d}",
                )
                file_record["source_ref"] = source_ref.to_dict()
                file_record["artifact_id"] = file_id
                file_record["provider"] = self.provider_name
                file_record["resource_kind"] = "documento_anexo"
                file_record["document_metadata"] = document_metadata_by_url.get(file_url, {})
                file_record["unidades_logicas"] = 1 if document_base else 0
                self._keep(file_record)

    def _related(
        self,
        source_ref: SourceRef,
        artifact: Any,
        artifact_base: dict[str, Any],
        related_factory: Callable[..., Any],
        records: list[dict[str, Any]],
    ) -> None:
        """Artefatos derivados do conteúdo (resultados dos itens, atas da compra), uma vez cada."""
        for item_record in records:
            item_path = _path_from_record(item_record)
            if item_path is None or not item_path.exists():
                continue
            try:
                item_payload = json.loads(item_path.read_text(encoding="utf-8"))
                related_artifacts = related_factory(source_ref, item_payload)
            except Exception as exc:
                self.output.append(
                    _failed_record(
                        self.context,
                        self.base,
                        f"{self.base['base_id']}:{artifact.artifact_id}:relacionados",
                        artifact.url,
                        f"{type(exc).__name__}: {exc}",
                        "json",
                    )
                )
                continue
            for related in related_artifacts:
                if related.artifact_id in self.seen_related_artifact_ids:
                    continue
                self.seen_related_artifact_ids.add(related.artifact_id)
                self._related_artifact(source_ref, artifact_base, related)

    def _related_artifact(self, source_ref: SourceRef, artifact_base: dict[str, Any], related: Any) -> None:
        related_base = self._endpoint_base(artifact_base, related.url, related.metadata)
        fallback_options = self._fallback_options(related.metadata)
        if related.artifact_type.casefold() in {"pdf", "html", "csv", "binary"}:
            related_records = [
                _collect_http_item(
                    self.context,
                    related_base,
                    _clean_url(related.url),
                    f"{self.base['base_id']}:{_safe_component(related.artifact_id)}",
                    _safe_component(related.filename or related.artifact_id),
                    expected_type=related.artifact_type,
                )
            ]
        else:
            related_records = _collect_rest_endpoint(
                self.context,
                related_base,
                _safe_component(related.artifact_id),
                related.url,
                **fallback_options,
            )
        for related_record in related_records:
            related_source_ref = related.source_ref or source_ref
            related_record["source_ref"] = related_source_ref.to_dict()
            related_record["artifact_id"] = related.artifact_id
            related_record["provider"] = self.provider_name
            related_record["unidades_logicas"] = int(
                bool(related.metadata.get("logical_unit"))
            )
            related_record["resource_kind"] = related.metadata.get("resource_kind")
            if related.metadata.get("titulo") or related.metadata.get("tipo_documento"):
                related_record["document_metadata"] = {
                    "titulo": related.metadata.get("titulo"),
                    "tipo_documento": related.metadata.get("tipo_documento"),
                }
            if related.metadata.get("acquisition_backend"):
                related_record["acquisition_backend"] = related.metadata[
                    "acquisition_backend"
                ]
            self._keep(related_record)


def _collect_base(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    access = base.get("acesso", {})
    kind = str(access.get("tipo", "")).lower()
    if kind == "not_configured":
        return [
            _failed_record(
                context,
                base,
                f"{base['base_id']}:not-configured",
                "not_configured",
                "coletor oficial ainda não configurado",
                "not_configured",
                status="not_configured",
            )
        ]
    if kind in {"http_pdf", "http_misto", "http_csv"}:
        urls = access.get("urls") or ([access["url"]] if access.get("url") else [])
        metadata_by_url = access.get("metadados_por_url") or {}
        records = [
            _collect_http_item(
                context,
                base,
                _clean_url(url),
                f"{base['base_id']}:url:{index}",
                f"{base['base_id']}_{index:03d}",
            )
            for index, url in enumerate(urls, start=1)
        ]
        for url, record in zip(urls, records):
            metadata = metadata_by_url.get(str(url)) or metadata_by_url.get(_clean_url(str(url)))
            if isinstance(metadata, dict) and metadata:
                record["document_metadata"] = metadata
        linked_sources = access.get("linked_sources") or []
        if linked_sources:
            provider_access = {
                **access,
                "tipo": "provider",
                "provider": "pncp",
                "sources": linked_sources,
                "resource_kinds": ["documentos_edital"],
            }
            records.extend(
                _collect_provider_base(
                    context,
                    {
                        **base,
                        "volume_alvo": len(linked_sources),
                        "acesso": provider_access,
                    },
                )
            )
        return records
    if kind in {"rest_api", "rest_api_mais_arquivo"}:
        endpoints = access.get("endpoints") or {"principal": access.get("endpoint")}
        if kind == "rest_api_mais_arquivo" and any("{" in str(endpoint) for endpoint in endpoints.values() if endpoint):
            return _collect_pncp_documents(context, base)
        output: list[dict[str, Any]] = []
        for endpoint_name, endpoint in endpoints.items():
            if endpoint:
                output.extend(_collect_rest_endpoint(context, base, str(endpoint_name), str(endpoint)))
        return output or [_failed_record(context, base, f"{base['base_id']}:rest", "rest_api", "endpoint não declarado")]
    if kind in {"resource_set", "pncp_resource_set"}:
        return _collect_resource_set(context, base)
    if kind == "provider":
        return _collect_provider_base(context, base)
    if kind in {"arquivo_local", "processamento_local"}:
        return _collect_local_base(context, base)
    return [_failed_record(context, base, f"{base['base_id']}:adapter", kind, f"tipo de acesso não suportado: {kind}")]
