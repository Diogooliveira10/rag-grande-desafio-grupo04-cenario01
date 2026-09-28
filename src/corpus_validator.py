"""Validações puras compartilhadas pelo pipeline e pelo relatório offline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OFFICIAL_BASE_TARGETS = {
    "contratos_estruturado": 1200,
    "editais_normativo": 4,
    "atas_conversacional": 20,
    "pca_agregado": 16,
}


@dataclass(frozen=True)
class ValidationResult:
    """Resultado pequeno e serializável de uma validação de contrato."""

    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _bases_for_scope(manifest: dict[str, Any], *, official: bool) -> list[dict[str, Any]]:
    bases = [base for base in manifest.get("bases", []) if isinstance(base, dict)]
    if official and any(base.get("escopo") == "oficial" for base in bases):
        return [base for base in bases if base.get("escopo") == "oficial"]
    return bases


def validate_manifest_contract(manifest: dict[str, Any], *, official: bool = True) -> ValidationResult:
    """Valida somente regras declarativas do manifesto, sem acessar a rede."""

    errors: list[str] = []
    bases = {
        str(base.get("base_id")): base
        for base in _bases_for_scope(manifest, official=official)
        if base.get("base_id")
    }
    if not bases:
        errors.append("manifesto sem bases declaradas")
        return ValidationResult(tuple(errors))

    if official:
        actual = set(bases)
        expected = set(OFFICIAL_BASE_TARGETS)
        if actual != expected:
            errors.append(
                f"bases oficiais divergentes: esperadas={sorted(expected)} obtidas={sorted(actual)}"
            )
        for base_id, target in OFFICIAL_BASE_TARGETS.items():
            base = bases.get(base_id)
            if base is None:
                continue
            if base.get("volume_alvo") != target:
                errors.append(
                    f"{base_id}: volume_alvo esperado={target} obtido={base.get('volume_alvo')}"
                )

    for base_id, base in bases.items():
        if base.get("selecao_deterministica") is not True:
            errors.append(f"{base_id}: selecao_deterministica não está habilitada")
        if not base.get("natureza"):
            errors.append(f"{base_id}: natureza ausente")
        if base.get("volume_alvo") is not None:
            try:
                if int(base["volume_alvo"]) < 1:
                    errors.append(f"{base_id}: volume_alvo deve ser positivo")
            except (TypeError, ValueError):
                errors.append(f"{base_id}: volume_alvo inválido")
        if base.get("volume_regra", "exato") not in {"exato", "minimo"}:
            errors.append(f"{base_id}: volume_regra deve ser 'exato' ou 'minimo'")

    return ValidationResult(tuple(errors))


def validate_inventory_contract(
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    *,
    official: bool = True,
) -> ValidationResult:
    """Valida se um inventário pode representar uma versão promovível."""

    errors = list(validate_manifest_contract(manifest, official=official).errors)
    if inventory.get("completo") is not True:
        errors.append("inventário marcado como incompleto")
    if inventory.get("falhas"):
        errors.append(f"inventário contém {len(inventory['falhas'])} falha(s)")

    scoped_bases = _bases_for_scope(manifest, official=official)
    expected_ids = {
        str(base.get("base_id"))
        for base in scoped_bases
        if base.get("base_id")
    }
    observed = {
        str(base.get("base_id")): base
        for base in inventory.get("por_base", [])
        if isinstance(base, dict) and base.get("base_id")
    }
    missing = sorted(expected_ids - set(observed))
    if missing:
        errors.append(f"inventário sem base(s): {', '.join(missing)}")

    for base in scoped_bases:
        base_id = str(base.get("base_id"))
        record = observed.get(base_id)
        if record is None:
            continue
        if record.get("status") != "complete":
            errors.append(f"{base_id}: status={record.get('status')!r}")
        target = base.get("volume_alvo")
        obtained = record.get("volume_obtido", record.get("itens"))
        rule = str(base.get("volume_regra") or "exato")
        if target is not None and (
            obtained < target if rule == "minimo" else obtained != target
        ):
            errors.append(f"{base_id}: volume esperado={target} obtido={obtained}")

    return ValidationResult(tuple(errors))
