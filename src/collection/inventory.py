"""Inventário de versões: montagem, gravação, relatório, diff, promoção e retomada.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from src.obs import Stage
from src.offline_observation import observar_offline
from src.collection import paths as coleta
from src.collection.checkpoint import _checkpoint_write, _validate_checkpoint
from src.collection.paths import _available_versions, _inventory_path, _latest_version, _path_from_record, _relative_path, _version_path
from src.collection.selection import _VersionContext, _discovery_checkpoint_matches_manifest, _isolated_pca_checkpoint_matches_manifest
from src.collection.sources import _base_inventory, _file_sha256, _version_number


def _build_inventory(
    manifest: dict[str, Any],
    version: int,
    manifest_hash: str,
    collected_at: str,
    items: dict[str, dict[str, Any]],
    *,
    inventory_type: str = "manifest",
    manifest_bound: bool = True,
    official: bool = True,
) -> dict[str, Any]:
    records = sorted(items.values(), key=lambda record: str(record.get("logical_id", "")))
    successful = [record for record in records if record.get("status") == "ok" and record.get("path")]
    failures = [record for record in records if record.get("status") != "ok"]
    by_base = [_base_inventory(base, records) for base in manifest.get("bases", [])]
    total_items = sum(
        int(record.get("unidades_logicas", record.get("registros", 0)) or 0)
        for record in successful
    )
    return {
        "inventory_type": inventory_type,
        "official": official,
        "manifest_bound": manifest_bound,
        "scenario_id": manifest.get("scenario_id") if manifest_bound else None,
        "version": f"v{version}",
        "coletado_em": collected_at,
        "manifest_hash": manifest_hash if manifest_bound else None,
        "completo": not failures and all(base["status"] == "complete" for base in by_base),
        "totais": {
            "arquivos": len(successful),
            "itens": total_items,
            "bytes": sum(int(record.get("bytes", 0) or 0) for record in successful),
            "falhas": len(failures),
            "failure_count": len(failures),
        },
        "por_base": by_base,
        "arquivos": records,
        "falhas": [
            {
                "origem": record.get("origem"),
                "erro": record.get("erro", "falha sem detalhe"),
                "logical_id": record.get("logical_id"),
            }
            for record in failures
        ],
    }


def _write_inventory(inventory: dict[str, Any]) -> Path:
    coleta.CAMINHOS.inventory_root.mkdir(parents=True, exist_ok=True)
    path = coleta.CAMINHOS.inventory_root / f"{inventory['version']}.json"
    path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _render_corpus_inventory(inventory: dict[str, Any]) -> None:
    totals = inventory["totais"]
    lines = [
        "# Inventário do corpus", "", f"- Tipo: `{inventory.get('inventory_type', 'manifest')}`", f"- Oficial: `{inventory.get('official', False)}`", f"- Vinculado ao manifesto: `{inventory.get('manifest_bound', False)}`", f"- Cenário: `{inventory.get('scenario_id') or '—'}`", f"- Versão: `{inventory.get('version')}`",
        f"- Coletado em: `{inventory.get('coletado_em')}`", f"- Completo: **{inventory.get('completo', False)}**", f"- Arquivos: **{totals.get('arquivos', 0)}**", f"- Itens/registros: **{totals.get('itens', 0)}**", f"- Bytes: **{totals.get('bytes', 0)}**", f"- Falhas: **{totals.get('falhas', 0)}**", "",
        "## Bases", "", "| Base | Escopo | Natureza | Status | Arquivos | Itens | Formatos | Bytes | Alvo | Divergência |", "|---|---|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for base in inventory.get("por_base", []):
        formatos = base.get("formatos") or {}
        formatos_text = ", ".join(f"{tipo}: {quantidade}" for tipo, quantidade in formatos.items()) if isinstance(formatos, dict) else ", ".join(formatos)
        alvo = base.get("volume_alvo") if base.get("volume_alvo") is not None else "—"
        divergencia = base.get("divergencia_alvo") if base.get("divergencia_alvo") is not None else "—"
        lines.append(f"| `{base['base_id']}` | {base.get('escopo') or '—'} | {base.get('natureza') or '—'} | {base.get('status') or '—'} | {base.get('arquivos', 0)} | {base.get('itens', 0)} | {formatos_text or '—'} | {base.get('bytes', 0)} | {alvo} | {divergencia} |")
    lines.extend(["", "## Falhas", ""])
    failures = inventory.get("falhas", [])
    lines.extend((f"- `{failure.get('origem')}`: {failure.get('erro')}" for failure in failures) if failures else ["Nenhuma falha registrada."])
    coleta.CAMINHOS.corpus_inventory_path.parent.mkdir(parents=True, exist_ok=True)
    coleta.CAMINHOS.corpus_inventory_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_inventory(version: int) -> dict[str, Any] | None:
    path = _inventory_path(version)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _scan_existing_version(version: int) -> dict[str, Any]:
    root = _version_path(version)
    if not root.exists():
        raise FileNotFoundError(f"versão não encontrada: v{version}")
    records: list[dict[str, Any]] = []
    legacy_base_id = "legacy_physical"
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).as_posix().split("/")
        if not parts:
            continue
        physical_group = parts[1] if parts[:1] == ["arquivos"] and len(parts) > 1 else parts[0]
        base_id = legacy_base_id
        body = path.read_bytes()
        records.append({"logical_id": f"{base_id}:{_relative_path(path)}", "base_id": base_id, "natureza": "não classificada", "physical_group": physical_group, "tipo": path.suffix.lstrip(".") or "bin", "origem": "inventário reconstruído a partir do armazenamento", "coletado_em": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"), "registros": 0, "status": "ok", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "path": _relative_path(path)})
    bases = [{"base_id": legacy_base_id, "natureza": "não classificada"}]
    return _build_inventory({"scenario_id": None, "bases": bases}, version, "", datetime.now(timezone.utc).isoformat(timespec="seconds"), {record["logical_id"]: record for record in records}, inventory_type="legacy_physical", manifest_bound=False, official=True)


@observar_offline(Stage.INVENTARIO)
def _inventory_for_version(version: int) -> dict[str, Any]:
    inventory = _load_inventory(version)
    legacy_needs_rebuild = bool(
        inventory
        and inventory.get("inventory_type") == "legacy_physical"
        and (
            "official" not in inventory
            or any(record.get("base_id") != "legacy_physical" for record in inventory.get("arquivos", []))
        )
    )
    if inventory is None or inventory.get("inventory_type") not in {"manifest", "legacy_physical"} or legacy_needs_rebuild:
        inventory = _scan_existing_version(version)
        _write_inventory(inventory)
    _render_corpus_inventory(inventory)
    return inventory


def _diff_inventories(previous: dict[str, Any], current: dict[str, Any]) -> Path:
    def keyed(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(record.get("logical_id") or f"{record.get('base_id')}:{record.get('path')}"): record for record in inventory.get("arquivos", [])}
    old, new = keyed(previous), keyed(current)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(key for key in set(old) & set(new) if old[key].get("sha256") != new[key].get("sha256"))
    identical = sorted(key for key in set(old) & set(new) if old[key].get("sha256") == new[key].get("sha256"))
    coleta.CAMINHOS.inventory_root.mkdir(parents=True, exist_ok=True)
    path = coleta.CAMINHOS.inventory_root / f"DIFF_{previous['version']}_{current['version']}.md"
    lines = [f"# Diff {previous['version']} → {current['version']}", "", f"- Adicionados: **{len(added)}**", f"- Removidos: **{len(removed)}**", f"- Alterados: **{len(changed)}**", f"- Idênticos: **{len(identical)}**"]
    for title, values in (("Adicionados", added), ("Removidos", removed), ("Alterados", changed), ("Idênticos", identical)):
        lines.extend(["", f"## {title}", ""])
        lines.extend((f"- `{value}`" for value in values) if values else ["Nenhum."])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _promote_version(context: _VersionContext) -> None:
    destination = _version_path(context.version)
    if destination.exists():
        raise FileExistsError(f"versão imutável já existe: {destination}")
    for logical_id, record in context.items.items():
        if record.get("status") != "ok":
            continue
        path = _path_from_record(record)
        if path is None or not path.is_file():
            raise RuntimeError(f"arquivo coletado ausente antes da promoção: {logical_id}")
        if not record.get("sha256"):
            raise RuntimeError(f"arquivo coletado sem SHA-256 antes da promoção: {logical_id}")
        if _file_sha256(path) != record["sha256"]:
            raise RuntimeError(f"SHA-256 divergente antes da promoção: {logical_id}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(context.staging_path), str(destination))
    old_prefix = _relative_path(context.staging_path).rstrip("/") + "/"
    new_prefix = _relative_path(destination).rstrip("/") + "/"
    for record in context.items.values():
        if isinstance(record.get("path"), str) and record["path"].startswith(old_prefix):
            record["path"] = new_prefix + record["path"][len(old_prefix):]
            record["source_file"] = record["path"]
    _checkpoint_write(context, state="promoted")


def _find_complete_matching_manifest(manifest_hash: str) -> dict[str, Any] | None:
    for version in reversed(_available_versions()):
        inventory = _load_inventory(version)
        if inventory and inventory.get("manifest_hash") == manifest_hash and inventory.get("completo"):
            return inventory
    return None


def _find_resumable_version(
    manifest_hash: str,
    manifest: dict[str, Any] | None = None,
    *,
    allow_discovery_manifest_drift: bool = False,
    allow_isolated_pca_manifest_drift: bool = False,
    expected_external_inputs: dict[str, str] | None = None,
) -> int | None:
    checkpoint_root = coleta.CAMINHOS.inventory_root / "checkpoints"
    if not checkpoint_root.exists():
        return None
    checkpoints = sorted(
        checkpoint_root.glob("v*.json"),
        key=lambda path: _version_number(path.stem) or -1,
        reverse=True,
    )
    resumable_candidates: list[tuple[int, int]] = []
    for checkpoint_path in checkpoints:
        version = _version_number(checkpoint_path.stem)
        if version is None:
            continue
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        staging_path = coleta.CAMINHOS.raw_root / ".staging" / f"v{version}"
        manifest_matches = checkpoint.get("manifest_hash") == manifest_hash
        external_inputs_match = checkpoint.get("external_inputs", {}) == (
            expected_external_inputs or {}
        )
        discovery_matches = (
            allow_discovery_manifest_drift
            and _discovery_checkpoint_matches_manifest(checkpoint, manifest)
        )
        isolated_pca_matches = (
            allow_isolated_pca_manifest_drift
            and _isolated_pca_checkpoint_matches_manifest(checkpoint, manifest)
        )
        if (
            (manifest_matches or discovery_matches or isolated_pca_matches)
            and external_inputs_match
            and checkpoint.get("state") in {"staging", "incomplete"}
            and (staging_path.exists() or discovery_matches)
        ):
            if manifest is None:
                return version
            validation = _validate_checkpoint(
                checkpoint,
                manifest,
                manifest_hash,
                version,
                staging_path,
                allow_discovery_manifest_drift=allow_discovery_manifest_drift,
                allow_isolated_pca_manifest_drift=allow_isolated_pca_manifest_drift,
                expected_external_inputs=expected_external_inputs,
            )
            if validation.ok:
                if allow_isolated_pca_manifest_drift:
                    successful_items = sum(
                        1
                        for item in checkpoint.get("items", {}).values()
                        if isinstance(item, dict) and item.get("status") == "ok"
                    )
                    resumable_candidates.append((successful_items, version))
                else:
                    return version
    return max(resumable_candidates)[1] if resumable_candidates else None


def _previous_inventory(command: str, resumable_version: int | None) -> dict[str, Any] | None:
    """Inventário da versão anterior que o update compara e reaproveita; None fora do update."""
    if command != "update":
        return None
    latest = _latest_version()
    if resumable_version is not None and latest == resumable_version:
        versions = [number for number in _available_versions() if number != resumable_version]
        latest = max(versions) if versions else None
    if latest is None:
        raise RuntimeError("update exige uma versão anterior")
    return _inventory_for_version(latest)
