"""Seleção determinística e checkpoints de descoberta: o que entra na coleta e se uma retomada é compatível."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from src.providers.base import SourceRef


@dataclass
class _VersionContext:
    manifest: dict[str, Any]
    version: int
    manifest_hash: str
    staging_path: Path
    checkpoint_path: Path
    collected_at: str
    items: dict[str, dict[str, Any]]
    hash_index: dict[str, str]
    last_request_at: dict[str, float] = field(default_factory=dict)
    previous_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    previous_version: int | None = None
    selection: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    discovery: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_inputs: dict[str, str] = field(default_factory=dict)


def _selection_from_items(items: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    selection: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[str]] = {}
    for logical_id, record in sorted(items.items()):
        source_value = record.get("source_ref")
        if not isinstance(source_value, dict):
            continue
        try:
            source_ref = SourceRef.from_dict(source_value)
        except (TypeError, ValueError, KeyError):
            continue
        base_id = str(record.get("base_id") or "")
        stable_id = source_ref.stable_id
        if not base_id or stable_id in seen.setdefault(base_id, set()):
            continue
        seen[base_id].add(stable_id)
        selection.setdefault(base_id, []).append(
            {
                "logical_id": logical_id,
                "stable_id": stable_id,
                "source_ref": source_ref.to_dict(),
            }
        )
    return selection


def _set_selection(
    context: _VersionContext,
    base_id: str,
    source_values: list[dict[str, Any]],
) -> None:
    if not hasattr(context, "selection"):
        context.selection = {}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_value in source_values:
        source_ref = SourceRef.from_dict(source_value)
        if source_ref.stable_id in seen:
            continue
        seen.add(source_ref.stable_id)
        selected.append(
            {
                "stable_id": source_ref.stable_id,
                "source_ref": source_ref.to_dict(),
            }
        )
    context.selection[base_id] = selected


def _record_observed_units(
    observations: list[Any], cnpjs: set[str], observed_units: dict[str, dict[str, Any]]
) -> None:
    """CNPJs e unidades vistos nas publicações da página."""
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        cnpj = str(observation.get("cnpj") or "").strip()
        unit = observation.get("unidadeOrgao") or {}
        if not cnpj:
            continue
        cnpjs.add(cnpj)
        if not isinstance(unit, dict):
            continue
        unit_code = str(
            unit.get("codigoUnidadeAdministrativa")
            or unit.get("codigoUnidade")
            or unit.get("codigo")
            or ""
        ).strip()
        if unit_code:
            units = observed_units.setdefault(cnpj, {})
            units.setdefault(
                unit_code,
                {
                    "codigoUnidadeAdministrativa": unit_code,
                    "ufSigla": str(unit.get("ufSigla") or "").upper(),
                    **(
                        {"codigoIbge": str(unit.get("codigoIbge") or unit.get("codigoMunicipioIbge"))}
                        if unit.get("codigoIbge") or unit.get("codigoMunicipioIbge")
                        else {}
                    ),
                },
            )


def _record_discovered_candidates(
    candidates: list[Any], state: dict[str, Any], eligible_sources: dict[str, Any], cnpjs: set[str]
) -> None:
    """Compras publicadas e contratos elegíveis da página; o contrato fica com a compra vinculada de menor controle."""
    for candidate in candidates:
        source_ref = candidate.source_ref
        if source_ref.resource_type.casefold() in {"contratacao", "contratação"}:
            state.setdefault("publication_sources", {})[source_ref.stable_id] = source_ref.to_dict()
            cnpj = str(source_ref.key.get("cnpj") or "").strip()
            if cnpj:
                cnpjs.add(cnpj)
            continue
        contract_id = str(source_ref.key.get("numeroControlePNCP") or "").strip()
        identity = contract_id or source_ref.stable_id
        existing_value = eligible_sources.get(identity)
        if existing_value is None:
            eligible_sources[identity] = source_ref.to_dict()
        else:
            try:
                existing_ref = SourceRef.from_dict(existing_value)
            except (TypeError, ValueError, KeyError):
                eligible_sources[identity] = source_ref.to_dict()
            else:
                existing_link = existing_ref.key.get("numeroControlePNCPCompra")
                current_link = source_ref.key.get("numeroControlePNCPCompra")
                if current_link and (
                    not existing_link or str(current_link) < str(existing_link)
                ):
                    eligible_sources[identity] = source_ref.to_dict()
        cnpj = str(source_ref.key.get("cnpj") or "").strip()
        if cnpj:
            cnpjs.add(cnpj)


def _pncp_discovery_config(base: dict[str, Any], population: dict[str, Any]) -> dict[str, Any]:
    access = base.get("acesso") or {}
    params = {**(access.get("params") or {}), **(population.get("params") or {})}
    resource_type = str(
        population.get("resource_type") or access.get("resource_type") or ""
    ).casefold()
    if resource_type in {"contrato_assinado", "contrato_pncp"} and population.get("censo_nacional"):
        params["dataInicial"] = min(str(params.get("dataInicial") or "2021-01-01"), "2021-01-01")
    return {
        "provider": str(access.get("provider") or ""),
        "resource_type": resource_type,
        "params": params,
        "dataInicial": str(params.get("dataInicial") or ""),
        "dataFinal": str(params.get("dataFinal") or ""),
        "anoAssinatura": str(params.get("anoAssinatura") or ""),
        "tamanhoPagina": int(params.get("tamanhoPagina") or 500),
        "filtro_uf": "RJ",
        "filtro_tipo": "exclui_empenho",
    }


def _discovery_checkpoint_matches_manifest(
    checkpoint: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> bool:
    if not manifest or checkpoint.get("scenario_id") != manifest.get("scenario_id"):
        return False
    if checkpoint.get("state") not in {"staging", "incomplete"}:
        return False
    if checkpoint.get("items"):
        return False

    contract_base = next(
        (
            base
            for base in manifest.get("bases", [])
            if isinstance(base, dict)
            and base.get("base_id") == "contratos_estruturado"
        ),
        None,
    )
    if contract_base is None:
        return False
    access = contract_base.get("acesso") or {}
    population = next(
        (
            item
            for item in access.get("populations", [])
            if isinstance(item, dict)
            and item.get("resource_type") == "contrato_assinado"
            and item.get("censo_nacional")
        ),
        None,
    )
    if population is None:
        return False

    discovery = checkpoint.get("discovery")
    if not isinstance(discovery, dict):
        return False
    contract_discovery = discovery.get("contratos_estruturado")
    if not isinstance(contract_discovery, dict):
        return False
    population_key = "contrato_assinado_rj_unidades"
    population_state = contract_discovery.get(population_key)
    if not isinstance(population_state, dict):
        population_key = "contrato_assinado"
        population_state = contract_discovery.get(population_key)
    if not isinstance(population_state, dict):
        return False
    saved = population_state.get("config")
    expected = _pncp_discovery_config(contract_base, population)
    if population_key == "contrato_assinado_rj_unidades":
        saved_scopes = saved.get("verified_unit_scopes") if isinstance(saved, dict) else None
        unit_state = contract_discovery.get("unidades_orgaos_rj") or {}
        current_scopes = unit_state.get("verified_unit_scopes")
        if not unit_state.get("verificacao_completa") or saved_scopes != current_scopes:
            return False
        saved = {key: value for key, value in saved.items() if key != "verified_unit_scopes"}
    return _discovery_configs_compatible(saved, expected)


def _discovery_configs_compatible(saved: Any, expected: dict[str, Any]) -> bool:
    if saved == expected:
        return True

    # Compatibilidade para checkpoints criados antes de guardar provider e params completos.
    legacy_expected = {
        key: expected[key]
        for key in (
            "dataInicial",
            "dataFinal",
            "anoAssinatura",
            "tamanhoPagina",
            "filtro_uf",
            "filtro_tipo",
        )
    }
    legacy_params = {"dataInicial", "dataFinal", "anoAssinatura", "tamanhoPagina"}
    return (
        saved == legacy_expected
        and expected["provider"] == "pncp"
        and expected["resource_type"] == "contrato_assinado"
        and set(expected["params"]).issubset(legacy_params)
    )


def _isolated_pca_checkpoint_matches_manifest(
    checkpoint: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> bool:
    bases = (manifest or {}).get("bases", [])
    if len(bases) != 1 or bases[0].get("base_id") != "pca_agregado":
        return False

    access = bases[0].get("acesso") or {}
    if access.get("preserve_explicit_sources") is not True or access.get("censo") is not False:
        return False

    expected_refs = access.get("sources")
    selection = (checkpoint.get("selection") or {}).get("pca_agregado")
    items = checkpoint.get("items")
    if not isinstance(expected_refs, list) or not isinstance(selection, list) or not isinstance(items, dict):
        return False

    try:
        expected_ids = {
            SourceRef.from_dict(source).stable_id
            for source in expected_refs
            if isinstance(source, dict)
        }
        selected_ids = {
            SourceRef.from_dict(entry["source_ref"]).stable_id
            for entry in selection
            if isinstance(entry, dict) and isinstance(entry.get("source_ref"), dict)
        }
    except (TypeError, ValueError, KeyError):
        return False

    if len(expected_ids) != len(expected_refs) or expected_ids != selected_ids:
        return False

    for record in items.values():
        if not isinstance(record, dict) or record.get("base_id") != "pca_agregado":
            return False
        source_value = record.get("source_ref")
        if isinstance(source_value, dict):
            try:
                if SourceRef.from_dict(source_value).stable_id not in expected_ids:
                    return False
            except (TypeError, ValueError, KeyError):
                return False
    return True


def _selection_errors(
    selection: Any,
    manifest_bases: dict[str, dict[str, Any]],
    source_ids_by_base: dict[str, set[str]],
    source_purchase_ids_by_base: dict[str, dict[str, str]],
    errors: list[str],
) -> None:
    """A seleção congelada: SourceRefs válidas e únicas, dentro do alvo, cobrindo os itens coletados."""
    if selection is not None:
        if not isinstance(selection, dict):
            errors.append("selection do checkpoint não é um objeto")
        else:
            for base_id, entries in selection.items():
                if not isinstance(entries, list):
                    errors.append(f"selection/{base_id}: não é uma lista")
                    continue
                seen: set[str] = set()
                selected_purchase_ids: set[str] = set()
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("source_ref"), dict):
                        errors.append(f"selection/{base_id}: entrada inválida")
                        continue
                    try:
                        source_ref = SourceRef.from_dict(entry["source_ref"])
                        source_id = source_ref.stable_id
                    except (TypeError, ValueError, KeyError) as exc:
                        errors.append(f"selection/{base_id}: SourceRef inválida: {exc}")
                        continue
                    if source_id in seen:
                        errors.append(f"selection/{base_id}: SourceRef duplicada {source_id}")
                    seen.add(source_id)
                    if entry.get("stable_id") not in {None, source_id}:
                        errors.append(f"selection/{base_id}: stable_id divergente")
                    purchase_id = str(source_ref.key.get("numeroControlePNCPCompra") or "")
                    if purchase_id:
                        selected_purchase_ids.add(purchase_id)
                base = manifest_bases.get(base_id, {})
                target = base.get("volume_alvo")
                if target is not None and base.get("volume_regra") != "minimo" and len(entries) > int(target):
                    errors.append(f"selection/{base_id}: excede volume_alvo")
                missing = {
                    source_id
                    for source_id in source_ids_by_base.get(base_id, set()) - seen
                    if source_purchase_ids_by_base.get(base_id, {}).get(source_id) not in selected_purchase_ids
                }
                if missing:
                    errors.append(f"selection/{base_id}: item fora da seleção congelada")


def _discovery_errors(discovery: Any, errors: list[str]) -> None:
    """Estado da descoberta: páginas, fontes elegíveis e publicadas com identidade coerente, unidades RJ."""
    if not isinstance(discovery, dict):
        errors.append("discovery do checkpoint não é um objeto")
    else:
        for base_id, populations in discovery.items():
            if not isinstance(populations, dict):
                errors.append(f"discovery/{base_id}: não é um objeto")
                continue
            for resource_type, state in populations.items():
                if not isinstance(state, dict):
                    errors.append(f"discovery/{base_id}/{resource_type}: estado inválido")
                    continue
                if not isinstance(state.get("completed_pages", []), list):
                    errors.append(f"discovery/{base_id}/{resource_type}: completed_pages inválido")
                eligible = state.get("eligible_sources", {})
                if not isinstance(eligible, dict):
                    errors.append(f"discovery/{base_id}/{resource_type}: eligible_sources inválido")
                    continue
                for stable_id, source_value in eligible.items():
                    try:
                        source_ref = SourceRef.from_dict(source_value)
                    except (TypeError, ValueError, KeyError) as exc:
                        errors.append(f"discovery/{base_id}/{resource_type}: SourceRef inválida: {exc}")
                        continue
                    identity = (
                        str(source_ref.key.get("numeroControlePNCP") or "")
                        if resource_type.casefold().startswith(("contrato_assinado", "contrato_pncp"))
                        else source_ref.stable_id
                    )
                    if stable_id != identity:
                        errors.append(f"discovery/{base_id}/{resource_type}: identidade divergente")
                publication_sources = state.get("publication_sources", {})
                if not isinstance(publication_sources, dict):
                    errors.append(f"discovery/{base_id}/{resource_type}: publication_sources inválido")
                else:
                    for stable_id, source_value in publication_sources.items():
                        try:
                            source_ref = SourceRef.from_dict(source_value)
                        except (TypeError, ValueError, KeyError) as exc:
                            errors.append(
                                f"discovery/{base_id}/{resource_type}: publication SourceRef inválida: {exc}"
                            )
                            continue
                        if stable_id != source_ref.stable_id:
                            errors.append(
                                f"discovery/{base_id}/{resource_type}: publication stable_id divergente"
                            )
                unit_scopes = state.get("verified_unit_scopes", [])
                if not isinstance(unit_scopes, list) or any(
                    not isinstance(scope, dict)
                    or not scope.get("cnpj")
                    or not scope.get("codigoUnidadeAdministrativa")
                    or str(scope.get("ufSigla") or "").upper() != "RJ"
                    for scope in unit_scopes
                ):
                    errors.append(f"discovery/{base_id}/{resource_type}: verified_unit_scopes inválido")


def _validate_census_configuration(provider: Any, access: dict[str, Any], populations: list[dict[str, Any]]) -> None:
    """No censo, o provider recusa antes de coletar uma configuração incompleta (ex.: cadastro de CNPJs)."""
    validate_configuration = getattr(provider, "validate_configuration", None)
    if not callable(validate_configuration):
        return
    for population in populations:
        query = {
            **access,
            **population,
            "resource_type": population.get("resource_type") or access.get("resource_type"),
            "params": {**(access.get("params") or {}), **(population.get("params") or {})},
        }
        validate_configuration(query)


def _validate_pca_attempt(inventory: dict[str, Any], selected: list[Any]) -> None:
    """Cada contrato selecionado com o recurso contrato, e cada compra vinculada com itens e resultados."""
    expected_contracts: dict[str, str] = {}
    for entry in selected:
        source_value = entry.get("source_ref") if isinstance(entry, dict) else None
        if not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        if source_ref.resource_type.casefold() == "contrato_assinado":
            expected_contracts[source_ref.stable_id] = str(
                source_ref.key.get("numeroControlePNCPCompra") or ""
            )
    if not expected_contracts:
        raise ValueError("checkpoint da tentativa não lista contratos selecionados na base pca_agregado")

    expected_purchases = {control for control in expected_contracts.values() if control}
    for resource_kind in ("contrato", "itens", "resultado_item"):
        expected = set(expected_contracts) if resource_kind == "contrato" else expected_purchases
        available: set[str] = set()
        for record in inventory.get("arquivos", []):
            if (
                not isinstance(record, dict)
                or record.get("base_id") != "pca_agregado"
                or record.get("resource_kind") != resource_kind
                or record.get("status") != "ok"
            ):
                continue
            source_value = record.get("source_ref")
            if not isinstance(source_value, dict):
                continue
            source_ref = SourceRef.from_dict(source_value)
            identity = (
                source_ref.stable_id
                if resource_kind == "contrato"
                else str(source_ref.key.get("numeroControlePNCPCompra") or "")
            )
            if identity:
                available.add(identity)

        missing = expected - available
        if missing:
            unit = "contratos" if resource_kind == "contrato" else "compras vinculadas"
            raise ValueError(
                f"tentativa incompleta: {len(missing)} de {len(expected)} {unit} "
                f"não têm recurso {resource_kind} bem-sucedido"
            )
