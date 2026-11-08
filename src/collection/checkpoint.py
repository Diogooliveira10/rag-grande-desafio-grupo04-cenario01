"""Checkpoint da coleta: gravação atômica, validação, contexto da versão e páginas de descoberta.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from src.providers.base import SourceRef
from src.corpus_validator import ValidationResult
from src.retry import retry_delay
from src.collection import paths as coleta
from src.collection.paths import _path_from_record
from src.collection.selection import _VersionContext, _discovery_checkpoint_matches_manifest, _discovery_errors, _isolated_pca_checkpoint_matches_manifest, _record_discovered_candidates, _record_observed_units, _selection_errors, _selection_from_items, _set_selection
from src.collection.sources import _file_sha256


def _seed_discovered_contract_selection(
    context: _VersionContext,
    manifest: dict[str, Any],
) -> int:
    base_id = "contratos_estruturado"
    base = next(
        (item for item in manifest.get("bases", []) if item.get("base_id") == base_id),
        None,
    )
    if base is None:
        raise RuntimeError(f"manifesto não declara a base {base_id}")
    base_discovery = context.discovery.get(base_id, {})
    unit_state = base_discovery.get("unidades_orgaos_rj", {})
    if not unit_state.get("verificacao_completa"):
        raise RuntimeError("unidades RJ não foram verificadas; não é seguro selecionar contratos")
    discovery_state = base_discovery.get("contrato_assinado_rj_unidades", {})
    eligible_sources = discovery_state.get("eligible_sources") or {}
    if not isinstance(eligible_sources, dict):
        raise RuntimeError("checkpoint não contém referências de contratos elegíveis")

    scopes = discovery_state.get("config", {}).get("verified_unit_scopes") or []
    verified_scopes = {
        (
            str(scope.get("cnpj") or "").strip(),
            str(scope.get("codigoUnidadeAdministrativa") or "").strip(),
        )
        for scope in scopes
        if isinstance(scope, dict)
    }
    if not verified_scopes:
        raise RuntimeError("checkpoint não contém unidades RJ verificadas para o censo de contratos")

    contracts_by_id: dict[str, dict[str, Any]] = {}
    for candidate_id, source_value in eligible_sources.items():
        if not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        if source_ref.provider.casefold() != "pncp" or source_ref.resource_type.casefold() != "contrato_assinado":
            continue
        contract_id = str(source_ref.key.get("numeroControlePNCP") or candidate_id).strip()
        cnpj = str(source_ref.key.get("cnpj") or "").strip()
        unit_code = str(source_ref.key.get("codigoUnidadeAdministrativa") or "").strip()
        if not contract_id or (cnpj, unit_code) not in verified_scopes:
            continue
        contracts_by_id.setdefault(contract_id, source_ref.to_dict())

    selection = base.get("selecao") or {}
    if str(selection.get("modo") or "").casefold() == "censo":
        # Censo: todos os elegíveis da janela, em ordem estável — sem teto.
        target = len(contracts_by_id)
    else:
        target = max(
            int(base.get("volume_alvo") or 1),
            int(selection.get("limite") or selection.get("limit") or 1),
        )
    if len(contracts_by_id) < target:
        raise RuntimeError(
            f"checkpoint tem {len(contracts_by_id)} contratos elegíveis em unidades RJ verificadas; "
            f"são necessários ao menos {target}"
        )

    selected = [contracts_by_id[contract_id] for contract_id in sorted(contracts_by_id)[:target]]
    _set_selection(context, base_id, selected)
    if len(context.selection[base_id]) < target:
        raise RuntimeError("seleção determinística não atingiu o alvo de contratos únicos")
    _checkpoint_write(context)
    return len(context.selection[base_id])


def _record_pncp_discovery_page(
    context: _VersionContext,
    base_id: str,
    resource_type: str,
    report: dict[str, Any],
) -> None:
    populations = context.discovery.setdefault(base_id, {})
    state = populations.setdefault(
        resource_type,
        {
            "completed_pages": [],
            "completed_windows": [],
            "eligible_sources": {},
            "cnpjs_derivados": [],
            "unidades_observadas_por_cnpj": {},
            "publication_sources": {},
            "total_paginas_por_janela": {},
            "total_registros_nacionais_por_janela": {},
        },
    )
    completed_pages = set(state.get("completed_pages") or [])
    completed_pages.add(str(report["page_key"]))
    state["completed_pages"] = sorted(completed_pages)

    total_pages = report.get("total_paginas")
    if total_pages is not None:
        state.setdefault("total_paginas_por_janela", {})[str(report["window"])] = int(total_pages)
    total_records = report.get("total_registros")
    if total_records is not None:
        state.setdefault("total_registros_nacionais_por_janela", {})[str(report["window"])] = int(total_records)

    eligible_sources = state.setdefault("eligible_sources", {})
    cnpjs = set(state.get("cnpjs_derivados") or [])
    observed_units = state.setdefault("unidades_observadas_por_cnpj", {})
    publication_count = int(state.get("total_publicacoes_observadas") or 0) + len(
        report.get("publication_organizations") or []
    )
    _record_observed_units(report.get("publication_organizations") or [], cnpjs, observed_units)
    _record_discovered_candidates(report.get("candidates") or [], state, eligible_sources, cnpjs)
    state["cnpjs_derivados"] = sorted(cnpjs)
    state["total_publicacoes_observadas"] = publication_count

    if report.get("window_done"):
        completed_windows = set(state.get("completed_windows") or [])
        completed_windows.add(str(report["window"]))
        state["completed_windows"] = sorted(completed_windows)
    state["total_contratos_elegiveis"] = len(eligible_sources)
    _checkpoint_write(context)


def _checkpoint_item_errors(
    checkpoint: dict[str, Any],
    items: dict[str, Any],
    manifest_bases: dict[str, dict[str, Any]],
    staging_path: Path,
    errors: list[str],
) -> tuple[dict[str, int], dict[str, set[str]], dict[str, dict[str, str]]]:
    """Confere arquivo, hash e SourceRef de cada item; devolve unidades lógicas, fontes e compras por base."""
    logical_by_base: dict[str, int] = {}
    source_ids_by_base: dict[str, set[str]] = {}
    source_purchase_ids_by_base: dict[str, dict[str, str]] = {}
    for logical_id, record in items.items():
        if not isinstance(record, dict):
            errors.append(f"item {logical_id}: registro inválido")
            continue
        if record.get("logical_id") not in {None, logical_id}:
            errors.append(f"item {logical_id}: logical_id divergente")
        base_id = str(record.get("base_id") or "")
        if manifest_bases and base_id not in manifest_bases:
            errors.append(f"item {logical_id}: base desconhecida {base_id!r}")
        if record.get("status") == "ok":
            path = _path_from_record(record)
            if path is None:
                errors.append(f"item {logical_id}: arquivo ausente")
            elif checkpoint.get("state") != "promoted":
                try:
                    path.resolve().relative_to(staging_path.resolve())
                except ValueError:
                    errors.append(f"item {logical_id}: caminho fora do staging")
            expected_sha = record.get("sha256")
            if not expected_sha:
                errors.append(f"item {logical_id}: SHA-256 ausente")
            elif path is not None and _file_sha256(path) != expected_sha:
                errors.append(f"item {logical_id}: SHA-256 divergente")
            units = int(record.get("unidades_logicas", record.get("registros", 0)) or 0)
            logical_by_base[base_id] = logical_by_base.get(base_id, 0) + units
            source_value = record.get("source_ref")
            if isinstance(source_value, dict):
                try:
                    source_ref = SourceRef.from_dict(source_value)
                    source_id = source_ref.stable_id
                except (TypeError, ValueError, KeyError) as exc:
                    errors.append(f"item {logical_id}: SourceRef inválida: {exc}")
                else:
                    source_ids_by_base.setdefault(base_id, set()).add(source_id)
                    purchase_id = str(source_ref.key.get("numeroControlePNCPCompra") or "")
                    if purchase_id:
                        source_purchase_ids_by_base.setdefault(base_id, {})[source_id] = purchase_id
    return logical_by_base, source_ids_by_base, source_purchase_ids_by_base


def _validate_checkpoint(
    checkpoint: dict[str, Any],
    manifest: dict[str, Any] | None,
    manifest_hash: str,
    version: int,
    staging_path: Path,
    *,
    allow_discovery_manifest_drift: bool = False,
    allow_isolated_pca_manifest_drift: bool = False,
    expected_external_inputs: dict[str, str] | None = None,
) -> ValidationResult:
    errors: list[str] = []
    if checkpoint.get("manifest_hash") != manifest_hash and not (
        (allow_discovery_manifest_drift and _discovery_checkpoint_matches_manifest(checkpoint, manifest))
        or (
            allow_isolated_pca_manifest_drift
            and _isolated_pca_checkpoint_matches_manifest(checkpoint, manifest)
        )
    ):
        errors.append("manifest_hash incompatível")
    if checkpoint.get("version") not in {None, f"v{version}"}:
        errors.append("versão do checkpoint incompatível")
    if checkpoint.get("state") not in {"staging", "incomplete", "promoted"}:
        errors.append("estado do checkpoint inválido")
    if checkpoint.get("external_inputs", {}) != (expected_external_inputs or {}):
        errors.append("entradas externas incompatíveis")

    items = checkpoint.get("items", {})
    if not isinstance(items, dict):
        errors.append("items do checkpoint não é um objeto")
        return ValidationResult(tuple(errors))

    manifest_bases = {
        str(base.get("base_id")): base
        for base in (manifest or {}).get("bases", [])
        if isinstance(base, dict) and base.get("base_id")
    }
    logical_by_base, source_ids_by_base, source_purchase_ids_by_base = _checkpoint_item_errors(
        checkpoint, items, manifest_bases, staging_path, errors
    )

    _selection_errors(checkpoint.get("selection"), manifest_bases, source_ids_by_base, source_purchase_ids_by_base, errors)

    for base_id, observed in logical_by_base.items():
        base = manifest_bases.get(base_id, {})
        target = base.get("volume_alvo")
        if target is not None and base.get("volume_regra") != "minimo" and observed > int(target):
            errors.append(f"{base_id}: unidades lógicas excedem volume_alvo")

    _discovery_errors(checkpoint.get("discovery", {}), errors)
    return ValidationResult(tuple(errors))


def _checkpoint_write(context: _VersionContext, state: str = "staging") -> None:
    context.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    selection = context.selection or _selection_from_items(context.items)
    payload = {
        "scenario_id": context.manifest["scenario_id"],
        "version": f"v{context.version}",
        "manifest_hash": context.manifest_hash,
        "coletado_em": context.collected_at,
        "state": state,
        "selection": selection,
        "discovery": context.discovery,
        "external_inputs": context.external_inputs,
        "items": context.items,
    }
    temporary = context.checkpoint_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(1, 5):
        try:
            temporary.replace(context.checkpoint_path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(retry_delay(attempt, None))


def _load_version_context(
    manifest: dict[str, Any],
    manifest_hash: str,
    version: int,
    *,
    previous_items: dict[str, dict[str, Any]] | None = None,
    previous_version: int | None = None,
    allow_discovery_manifest_drift: bool = False,
    allow_isolated_pca_manifest_drift: bool = False,
    external_inputs: dict[str, str] | None = None,
) -> _VersionContext:
    staging_path = coleta.CAMINHOS.raw_root / ".staging" / f"v{version}"
    checkpoint_path = coleta.CAMINHOS.inventory_root / "checkpoints" / f"v{version}.json"
    items: dict[str, dict[str, Any]] = {}
    if staging_path.exists() and not checkpoint_path.exists():
        raise RuntimeError(f"staging sem checkpoint; recuse sobrescrever: {staging_path}")
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        validation = _validate_checkpoint(
            checkpoint,
            manifest,
            manifest_hash,
            version,
            staging_path,
            allow_discovery_manifest_drift=allow_discovery_manifest_drift,
            allow_isolated_pca_manifest_drift=allow_isolated_pca_manifest_drift,
            expected_external_inputs=external_inputs,
        )
        if not validation.ok:
            details = "; ".join(validation.errors)
            raise RuntimeError(f"checkpoint inválido para resume: {details}")
        items = dict(checkpoint.get("items", {}))
        selection = checkpoint.get("selection") or _selection_from_items(items)
        discovery = dict(checkpoint.get("discovery") or {})
    else:
        selection = {}
        discovery = {}
    staging_path.mkdir(parents=True, exist_ok=True)
    context = _VersionContext(
        manifest=manifest,
        version=version,
        manifest_hash=manifest_hash,
        staging_path=staging_path,
        checkpoint_path=checkpoint_path,
        collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        items=items,
        hash_index={
            str(record["sha256"]): str(record["path"])
            for record in items.values()
            if record.get("status") == "ok" and record.get("sha256") and record.get("path")
        },
        previous_items=previous_items or {},
        previous_version=previous_version,
        selection=selection,
        discovery=discovery,
        external_inputs=external_inputs or {},
        last_request_at={},
    )
    _checkpoint_write(context)
    return context
