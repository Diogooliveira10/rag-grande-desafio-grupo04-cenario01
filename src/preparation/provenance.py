"""Proveniência das unidades: origem declarada, metadados dos registros, inventário e contexto relacionado."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.preparation.common import PROJECT_ROOT, SourceUnit, _relative_path
from src.preparation.readers import _json_records, _read_text


def _base_origins(base: dict[str, Any]) -> list[str]:
    access = base.get("acesso") or {}
    origins: list[str] = []
    for key in ("origem", "url", "endpoint"):
        value = access.get(key)
        if value:
            origins.append(str(value))
    urls = access.get("urls")
    if isinstance(urls, list):
        origins.extend(str(url) for url in urls if url)
    endpoints = access.get("endpoints")
    if isinstance(endpoints, dict):
        origins.extend(str(url) for url in endpoints.values() if url)
    if not origins and base.get("fonte"):
        origins.append(str(base["fonte"]))
    return list(dict.fromkeys(origins))


def _base_origin(base: dict[str, Any]) -> str:
    origins = _base_origins(base)
    return origins[0] if len(origins) == 1 else ""


def _declared_record_metadata(record: Any, base: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    declared = base.get("metadados_extras") or []
    fields = [str(field) for field in declared]
    validity_field = base.get("campo_vigencia")
    if validity_field:
        fields.append(str(validity_field))
    extracted = {
        field: record[field]
        for field in dict.fromkeys(fields)
        if field in record and record[field] not in (None, "")
    }
    if validity_field and str(validity_field) in extracted:
        extracted["data_vigencia"] = _normalize_date_value(extracted[str(validity_field)])
    return extracted


def _normalize_date_value(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value).strip()
    # Só o ano (ex.: anoPca do PCA): o plano vale até o fim do ano.
    if re.fullmatch(r"\d{4}", text):
        return f"{text}-12-31"
    for pattern in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return text


def _record_logical_id(
    base_id: str,
    source_file: str,
    record: Any,
    fallback: str,
) -> str:
    if isinstance(record, dict):
        for field in (
            "id",
            "idCompra",
            "idContratacao",
            "external_id",
            "source_id",
            "numeroControlePNCP",
            "numeroControlePncpCompra",
            "numeroControlePncpAta",
            "sequencialContrato",
            "numeroContratoEmpenho",
            "ID Licitação",
            "ID",
            "idLicitacao",
            "numeroProcesso",
            "numero",
            "sequencial",
        ):
            value = record.get(field)
            if value not in (None, ""):
                return f"{base_id}:{field}:{value}"
    return f"{base_id}:{source_file}:{fallback}"


def _base_for(manifest: dict[str, Any], input_dir: Path, base_id: str | None) -> dict[str, Any]:
    bases = manifest.get("bases", [])
    by_id = {str(base.get("base_id")): base for base in bases}
    if base_id:
        if base_id not in by_id:
            raise ValueError(f"base_id não declarado no manifesto: {base_id}")
        return by_id[base_id]

    resolved_input = input_dir.resolve()
    candidates: list[dict[str, Any]] = []
    for base in bases:
        access = base.get("acesso") or {}
        origin = access.get("origem") or base.get("local")
        if origin and (PROJECT_ROOT / str(origin)).resolve() == resolved_input:
            candidates.append(base)
        elif input_dir.name == str(base.get("base_id")):
            candidates.append(base)
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("informe --base-id quando a pasta não identifica uma única base")


def _load_inventory_provenance(
    path: Path | None,
    *,
    expected_manifest_hash: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    inventory_manifest_hash = payload.get("manifest_hash")
    if (
        expected_manifest_hash
        and inventory_manifest_hash
        and inventory_manifest_hash != expected_manifest_hash
    ):
        raise ValueError(
            "manifest_hash do inventário não corresponde ao manifesto utilizado"
        )
    records = payload.get("arquivos", [])
    if not isinstance(records, list):
        raise ValueError("inventário precisa conter uma lista em 'arquivos'")

    provenance: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        base_id = str(record.get("base_id") or "")
        digest = str(record.get("sha256") or "")
        if not base_id or not digest:
            continue
        relation = {
            key: record[key]
            for key in ("source_ref", "artifact_id", "resource_kind", "document_metadata")
            if record.get(key) not in (None, "")
        }
        entry = {
            "origem": str(record.get("origem") or ""),
            "coletado_em": str(record.get("coletado_em") or ""),
            "relacoes": [relation] if relation else [],
        }
        key = (base_id, digest)
        previous = provenance.get(key)
        if previous is None:
            provenance[key] = entry
            continue
        for field in ("origem", "coletado_em"):
            if previous[field] != entry[field]:
                previous[field] = ""
        known_relations = {
            json.dumps(value, ensure_ascii=False, sort_keys=True)
            for value in previous["relacoes"]
        }
        for value in entry["relacoes"]:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if encoded not in known_relations:
                previous["relacoes"].append(value)
                known_relations.add(encoded)
    return provenance


def _load_related_context(path: Path | None, base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Metadados que um arquivo herda de outros artefatos da mesma fonte.

    Um PDF de ata não traz vigência nem título próprios: eles estão no JSON de
    metadados (``...:metadata``) e na listagem ``...:arquivos`` da mesma
    ``source_ref`` no inventário. Devolve, por SHA-256 do arquivo, os campos
    declarados no manifesto (incluindo ``data_vigencia``) e o título do
    documento anunciado na listagem.
    """
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in payload.get("arquivos", []):
        if (
            isinstance(record, dict)
            and record.get("base_id") == base.get("base_id")
            and record.get("status") == "ok"
            and isinstance(record.get("source_ref"), dict)
        ):
            key = json.dumps(record["source_ref"], sort_keys=True)
            groups.setdefault(key, []).append(record)

    def _json_file(record: dict[str, Any] | None) -> Any:
        file_path = PROJECT_ROOT / str((record or {}).get("path") or "")
        if record is None or not file_path.is_file():
            return None
        try:
            return json.loads(_read_text(file_path))
        except json.JSONDecodeError:
            return None

    context: dict[str, dict[str, Any]] = {}
    for records in groups.values():
        by_suffix = {str(record.get("artifact_id", "")).rsplit(":", 1)[-1]: record for record in records}
        inherited = _declared_record_metadata(_json_file(by_suffix.get("metadata")), base)
        listing = _json_file(by_suffix.get("arquivos"))
        linked_contracts_payload = _json_file(by_suffix.get("contratos_vinculados"))
        linked_contracts = []
        for _, contract in _json_records(linked_contracts_payload):
            if not isinstance(contract, dict):
                continue
            identifiers = {
                key: contract[key]
                for key in (
                    "numeroControlePNCP",
                    "numeroControlePNCPContrato",
                    "numeroContratoEmpenho",
                    "anoContrato",
                    "sequencialContrato",
                )
                if contract.get(key) not in (None, "")
            }
            if identifiers:
                linked_contracts.append(identifiers)
        if linked_contracts:
            inherited["contratos_vinculados"] = linked_contracts
        documents = {
            str(document.get("url")): document
            for document in (listing if isinstance(listing, list) else [])
            if isinstance(document, dict) and document.get("url")
        }
        for record in records:
            if record.get("tipo") != "pdf" or not record.get("sha256"):
                continue
            entry = dict(inherited)
            document = documents.get(str(record.get("origem")))
            if document:
                entry["titulo_documento"] = document.get("titulo")
                entry["tipo_documento"] = document.get("tipoDocumentoNome")
            if entry:
                context[str(record["sha256"])] = entry
    return context


def _with_related_context(unit: SourceUnit, related: dict[str, Any] | None) -> SourceUnit:
    if not related:
        return unit
    extras = {**related, **unit.extras}
    vigencia = unit.data_vigencia or related.get("data_vigencia")
    return replace(
        unit,
        extras=extras,
        data_vigencia=str(vigencia) if vigencia not in (None, "") else None,
    )


def _embedded_origin(path: Path) -> str:
    if path.suffix.lower() not in {".html", ".htm", ".aspx"}:
        return ""
    text = _read_text(path)
    patterns = (
        r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)',
        r'<link\b[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']',
        r'<meta\b[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)',
        r'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:url["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _file_provenance(
    base: dict[str, Any],
    path: Path,
    digest: str,
    inventory_provenance: dict[tuple[str, str], dict[str, str]],
    fallback_collected_at: str,
) -> tuple[str, str]:
    entry = inventory_provenance.get((str(base["base_id"]), digest), {})
    origin = entry.get("origem") or _embedded_origin(path) or _base_origin(base)
    collected_at = entry.get("coletado_em") or fallback_collected_at
    return origin, collected_at


def _unit_common(
    path: Path,
    base: dict[str, Any],
    digest: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
) -> dict[str, Any]:
    source_file = _relative_path(path)
    return {
        "source_file": source_file,
        "arquivo_bruto": source_file,
        "nome_arquivo": path.name,
        "bytes": path.stat().st_size,
        "coletado_em": collected_at,
        "manifest_fonte": base.get("fonte"),
        "manifest_hash": manifest_hash,
        "origem": origin,
        "acesso_tipo": (base.get("acesso") or {}).get("tipo"),
        "sha256": digest,
    }


def _make_unit(
    path: Path,
    base: dict[str, Any],
    digest: str,
    text: str,
    document_id: str,
    formato: str,
    status: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
    extras: dict[str, Any] | None = None,
) -> SourceUnit:
    common = _unit_common(path, base, digest, manifest_hash, collected_at, origin)
    if extras:
        common.update(extras)
    natureza = str(base["natureza"])
    logical_id = str(common.get("logical_id") or f"{base['base_id']}:{common['source_file']}")
    data_vigencia = common.get("data_vigencia")
    return SourceUnit(
        source_file=common["source_file"],
        logical_id=logical_id,
        document_id=document_id,
        base_id=str(base["base_id"]),
        natureza=natureza,
        doc_type=formato,
        sensitivity=str(base.get("sensitivity_default") or "publico"),
        origem=str(common.get("origem") or ""),
        manifest_hash=manifest_hash,
        bytes=int(common["bytes"]),
        data_vigencia=str(data_vigencia) if data_vigencia not in (None, "") else None,
        chunking_sugerido=str(base.get("chunking_sugerido") or "chunking_generico"),
        formato=formato,
        sha256=digest,
        text=text,
        status=status,
        extras=common,
    )


def _refresh_unit_provenance(
    unit: SourceUnit,
    path: Path,
    base: dict[str, Any],
    manifest_hash: str,
    fallback_collected_at: str,
    inventory_provenance: dict[tuple[str, str], dict[str, str]],
) -> SourceUnit:
    origin, collected_at = _file_provenance(
        base, path, unit.sha256, inventory_provenance, fallback_collected_at
    )
    common = _unit_common(path, base, unit.sha256, manifest_hash, collected_at, origin)
    extras = dict(unit.extras)
    extras.update(common)
    return replace(
        unit,
        origem=origin,
        manifest_hash=manifest_hash,
        bytes=path.stat().st_size,
        sensitivity=(unit.sensitivity if base.get("dados_sinteticos")
                     else str(base.get("sensitivity_default") or unit.sensitivity)),
        chunking_sugerido=str(base.get("chunking_sugerido") or unit.chunking_sugerido),
        extras=extras,
    )
