r"""Materializa o corpus declarado pelo manifesto.

Uso:
    py -m src.fetch_corpus fetch --manifest corpus_manifest.yaml --official
    py -m src.fetch_corpus update --manifest corpus_manifest.yaml --official
    py -m src.fetch_corpus discover --manifest corpus_manifest.yaml --official --max-pages 2
    py -m src.fetch_corpus inventory --corpus-version v1 --official

As versões são coletadas com checkpoint, staging, inventário e deduplicação
por conteúdo. Coletas experimentais exigem ``--data-root`` explícito.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rich.panel import Panel
from rich.table import Table

try:
    from .providers import get_provider
    from .providers.base import SourceRef
    from .corpus_validator import ValidationResult, validate_inventory_contract
    from .retry import retry_delay
except ImportError:
    from providers import get_provider
    from providers.base import SourceRef
    from corpus_validator import ValidationResult, validate_inventory_contract
    from retry import retry_delay


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.obs import Stage, new_query_id, registrar
from src.log_context import current_execution
from src.offline_observation import observar_offline

# Peças movidas para src/collection/ (2026-11-07); os nomes continuam importáveis daqui.
from src.collection.manifest_yaml import (  # noqa: E402,F401  (reexportados)
    _strip_yaml_comment,
    _split_yaml_tokens,
    _split_yaml_mapping,
    _parse_yaml_scalar,
    _parse_manifest_yaml,
)
from src.collection.checks import (  # noqa: E402,F401  (reexportados)
    source_check,
    RICH_CONSOLE,
    FetchCandidate,
    _available,
    _file_urls,
    iter_candidates,
    _unique_samples,
    _TypedChecks,
    _run_checks_for_types,
)
from src.collection.selection import (  # noqa: E402,F401  (reexportados)
    _VersionContext,
    _selection_from_items,
    _set_selection,
    _record_observed_units,
    _record_discovered_candidates,
    _pncp_discovery_config,
    _discovery_checkpoint_matches_manifest,
    _discovery_configs_compatible,
    _isolated_pca_checkpoint_matches_manifest,
    _selection_errors,
    _discovery_errors,
    _validate_census_configuration,
    _validate_pca_attempt,
)
from src.collection.sources import (  # noqa: E402,F401  (reexportados)
    _version_number,
    _file_sha256,
    _materialize_object,
    _previous_record,
    _provider_populations,
    _resumable_sources,
    _population_query,
    _resumed_records,
    _normalized_ascii,
    _DOCUMENT_METADATA_FIELDS,
    _DOCUMENT_METADATA_ALIASES,
    _document_metadata_by_url,
    _base_inventory,
    _CONTRACT_SOURCE_TYPES,
    _linked_contract_sources,
    _pca_source_access,
    _apply_source_inventory,
    _previous_items_and_version,
)
from src.collection.rest import (  # noqa: E402,F401  (reexportados)
    _clean_url,
    _safe_component,
    _estimate_base_requests,
    _conditional_headers,
    _extension_for,
    _build_request_url,
    _payload_records,
    _selection_limit,
    _selection_key,
    _payload_with_records,
    _rest_pagination,
    _request_rest_page,
    _annotate_saved_page,
)


class _RunLogger:
    """Registro JSONL de uma execução da coleta, sem bibliotecas externas."""

    def __init__(self, command: str) -> None:
        timestamp = datetime.now(timezone.utc)
        self.run_id = f"{command}-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{time.time_ns() % 1_000_000:06d}"
        self.path = CAMINHOS.runs_root / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.query_id = new_query_id()
        self._stage_starts: dict[str, float] = {}

    def event(self, stage: str, status: str, **fields: Any) -> None:
        record = {
            "run_id": self.run_id,
            "stage": stage,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        context = current_execution()
        started = self._stage_starts.setdefault(stage, time.perf_counter())
        if status == "started":
            self._stage_starts[stage] = time.perf_counter()
        registrar(run_id=context.run_id if context else self.run_id,
            query_id=context.query_id if context else self.query_id, stage=Stage.COLETA,
            status={"started": "iniciado", "completed": "ok", "passed": "ok", "failed": "erro",
                    "reused": "ok"}.get(status, status),
            latencia_ms=0 if status == "started" else (time.perf_counter() - started) * 1000,
            input={"etapa_coleta": stage}, output=fields,
            extra={"tipo_pipeline": "offline", "origem": "offline", "etapa_coleta": stage,
                   "run_id_legado": self.run_id, "log_legado": str(self.path)})


class _VersionLock:
    """Lock exclusivo para impedir dois writers na mesma versão."""

    def __init__(self, version: int) -> None:
        self.path = CAMINHOS.raw_root / ".staging" / f"v{version}.lock"
        self._owned = False

    @staticmethod
    def _process_is_alive(pid: int) -> bool:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _is_stale(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("hostname") != socket.gethostname():
                return False
            pid = int(payload["pid"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
        return not self._process_is_alive(pid)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for attempt in range(2):
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 0 and self._is_stale():
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        continue
                    continue
                raise RuntimeError(f"versão já está sendo coletada: {self.path}")
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            self._owned = True
            return
        raise RuntimeError(f"não foi possível adquirir lock da versão: {self.path}")

    def release(self) -> None:
        if not self._owned:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self._owned = False


MANIFEST_DEFAULT = PROJECT_ROOT / "corpus_manifest.yaml"
@dataclass(frozen=True)
class CaminhosDaColeta:
    """Onde uma execução da coleta lê e grava: a árvore oficial (``data/``) ou uma isolada.

    Imutável: uma execução troca o objeto inteiro (``_configure_data_root``), em
    vez de reescrever cada caminho como variável global.
    """

    data_root: Path
    raw_root: Path
    objects_root: Path
    inventory_root: Path
    runs_root: Path
    corpus_inventory_path: Path

    @classmethod
    def da_raiz(cls, data_root: Path) -> CaminhosDaColeta:
        """Caminhos derivados de ``data_root``. O ``CORPUS_INVENTORY.md`` da árvore
        oficial fica na raiz do projeto; o de uma árvore isolada, dentro dela."""
        raw_root = data_root / "raw"
        oficial = data_root.resolve() == (PROJECT_ROOT / "data").resolve()
        return cls(
            data_root=data_root,
            raw_root=raw_root,
            objects_root=raw_root / ".objects" / "sha256",
            inventory_root=data_root / "inventory",
            runs_root=data_root / "runs",
            corpus_inventory_path=(PROJECT_ROOT if oficial else data_root) / "CORPUS_INVENTORY.md",
        )


CAMINHOS = CaminhosDaColeta.da_raiz(PROJECT_ROOT / "data")


def _configure_data_root(data_root: Path) -> None:
    """Direciona uma execução para uma árvore de dados isolada."""
    global CAMINHOS
    CAMINHOS = CaminhosDaColeta.da_raiz(data_root)


def load_manifest(path: Path) -> dict[str, Any]:
    """Carrega o formato do manifesto sem adicionar dependências ao projeto."""
    manifest = _parse_manifest_yaml(path.read_text(encoding="utf-8"))
    missing = sorted({"scenario_id", "version", "bases"} - set(manifest))
    if missing:
        raise ValueError(f"manifesto sem campos obrigatórios: {', '.join(missing)}")
    if not isinstance(manifest["bases"], list) or not manifest["bases"]:
        raise ValueError("manifesto precisa declarar pelo menos uma base")
    seen: set[str] = set()
    for base in manifest["bases"]:
        if not isinstance(base, dict):
            raise ValueError("cada base do manifesto precisa ser um mapa")
        for field in ("base_id", "natureza", "acesso"):
            if not base.get(field):
                raise ValueError(f"base sem campo obrigatório: {field}")
        if base["base_id"] in seen:
            raise ValueError(f"base_id duplicado: {base['base_id']}")
        escopo = str(base.get("escopo") or "experimental").lower()
        if escopo not in {"oficial", "experimental"}:
            raise ValueError(f"escopo inválido para a base {base['base_id']}: {escopo}")
        base["escopo"] = escopo
        seen.add(base["base_id"])
    return manifest


def _manifest_for_scope(manifest: dict[str, Any], *, official: bool) -> dict[str, Any]:
    """Retorna o manifesto limitado ao escopo da execução."""
    if not official:
        return manifest
    bases = [base for base in manifest["bases"] if base.get("escopo") == "oficial"]
    if not bases:
        raise ValueError("manifesto não declara bases com escopo oficial")
    return {**manifest, "bases": bases}


def _manifest_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _available_versions() -> list[int]:
    """Versões conhecidas: pastas ``data/raw/vN/`` e inventários ``data/inventory/vN.json``.

    O inventário é versionado no Git e a pasta raw não, então numa máquina que
    só clonou o repositório a versão aparece apenas pelo ``vN.json``.
    """
    values: set[int] = set()
    if CAMINHOS.raw_root.exists():
        values.update(
            number for child in CAMINHOS.raw_root.iterdir() if child.is_dir() and (number := _version_number(child.name)) is not None
        )
    if CAMINHOS.inventory_root.exists():
        values.update(
            number
            for child in CAMINHOS.inventory_root.iterdir()
            if child.is_file() and child.suffix == ".json" and (number := _version_number(child.stem)) is not None
        )
    return sorted(values)


def _next_version() -> int:
    return max(_available_versions(), default=0) + 1


def _latest_version() -> int | None:
    values = _available_versions()
    return max(values) if values else None


def _version_path(version: int) -> Path:
    return CAMINHOS.raw_root / f"v{version}"


def _inventory_path(version: int) -> Path:
    return CAMINHOS.inventory_root / f"v{version}.json"


@observar_offline(Stage.COLETA)
def _dry_run_update(manifest_path: Path, *, official: bool = False) -> int:
    """Mostra o plano do update sem criar logs, checkpoints ou arquivos."""
    manifest = _manifest_for_scope(load_manifest(manifest_path), official=official)
    latest = _latest_version()
    if latest is None:
        raise RuntimeError("update exige uma versão anterior")
    inventory_path = _inventory_path(latest)
    if not inventory_path.exists():
        raise RuntimeError(f"inventário da versão anterior não encontrado: {inventory_path}")
    previous = json.loads(inventory_path.read_text(encoding="utf-8"))
    previous_items = [
        item
        for item in previous.get("arquivos", [])
        if isinstance(item, dict) and item.get("status") == "ok"
    ]
    estimated = sum(_estimate_base_requests(base) for base in manifest["bases"])
    conditional = min(estimated, len(previous_items))
    summary = {
        "dry_run": True,
        "scenario_id": manifest.get("scenario_id"),
        "manifest": str(manifest_path),
        "manifest_hash": _manifest_hash(manifest_path),
        "previous": f"v{latest}",
        "next": f"v{latest + 1}",
        "bases": [
            {
                "base_id": base["base_id"],
                "acesso": (base.get("acesso") or {}).get("tipo"),
                "estimated_requests": _estimate_base_requests(base),
            }
            for base in manifest["bases"]
        ],
        "previous_files": len(previous_items),
        "estimated_requests": estimated,
        "estimated_conditional_requests": conditional,
        "estimated_new_or_changed_requests": max(0, estimated - conditional),
        "writes": False,
    }
    RICH_CONSOLE.print(
        Panel(
            f"[bold]Cenário:[/] {summary['scenario_id']}\n"
            f"[bold]Manifest hash:[/] {summary['manifest_hash']}\n"
            f"[bold]Versão anterior:[/] {summary['previous']}    "
            f"[bold]Próxima:[/] {summary['next']}\n"
            f"[bold]Arquivos anteriores:[/] {summary['previous_files']}    "
            f"[bold]Escrita:[/] não",
            title="Update preview",
            border_style="cyan",
        )
    )
    table = Table(title="Estimativa de requisições")
    table.add_column("Base", style="cyan")
    table.add_column("Acesso", style="magenta")
    table.add_column("Estimadas", justify="right")
    for base in summary["bases"]:
        table.add_row(base["base_id"], str(base["acesso"] or "—"), str(base["estimated_requests"]))
    table.add_row("TOTAL", "", str(summary["estimated_requests"]), style="bold")
    RICH_CONSOLE.print(table)
    RICH_CONSOLE.print(
        f"Condicionais estimadas: [yellow]{summary['estimated_conditional_requests']}[/]  "
        f"Novas/alteradas estimadas: [yellow]{summary['estimated_new_or_changed_requests']}[/]"
    )
    return 0


def _relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _path_from_record(record: dict[str, Any]) -> Path | None:
    raw_path = record.get("path")
    if not raw_path:
        return None
    path = PROJECT_ROOT / str(raw_path)
    return path if path.exists() else None


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
    staging_path = CAMINHOS.raw_root / ".staging" / f"v{version}"
    checkpoint_path = CAMINHOS.inventory_root / "checkpoints" / f"v{version}.json"
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


def _wait_rate_limit(context: _VersionContext, base: dict[str, Any]) -> None:
    try:
        delay = max(0.0, float((base.get("acesso") or {}).get("rate_limit_seg", 0) or 0))
    except (TypeError, ValueError):
        delay = 0.0
    base_id = str(base["base_id"])
    elapsed = time.monotonic() - context.last_request_at.get(base_id, 0.0)
    if delay and elapsed < delay:
        time.sleep(delay - elapsed)
    context.last_request_at[base_id] = time.monotonic()


def _effective_objects_root() -> Path:
    try:
        CAMINHOS.objects_root.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return PROJECT_ROOT / "data" / "raw" / ".objects" / "sha256"
    return CAMINHOS.objects_root


def _object_path(digest: str) -> Path:
    return _effective_objects_root() / digest[:2] / digest


def _ensure_object(body: bytes, digest: str) -> Path:
    object_path = _object_path(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    if object_path.exists():
        if _file_sha256(object_path) != digest:
            raise RuntimeError(f"objeto SHA-256 corrompido: {object_path}")
        return object_path
    temporary = object_path.with_name(f".{object_path.name}.tmp")
    temporary.write_bytes(body)
    try:
        if object_path.exists():
            temporary.unlink(missing_ok=True)
        else:
            temporary.replace(object_path)
    finally:
        temporary.unlink(missing_ok=True)
    return object_path


def _save_version_bytes(context: _VersionContext, body: bytes, base_id: str, filename_prefix: str, extension: str) -> dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    object_path = _ensure_object(body, digest)
    destination_dir = context.staging_path / _safe_component(base_id)
    suffix = extension if extension.startswith(".") else f".{extension}"
    safe_prefix = _safe_component(filename_prefix)
    max_path_length = 259 if os.name == "nt" else 4096
    available_component_length = max_path_length - len(str(destination_dir)) - 1 - 5
    max_component_length = min(120, max(1, available_component_length))
    prefix_limit = max(1, max_component_length - len(digest[:12]) - len(suffix) - 1)
    safe_prefix = safe_prefix[:prefix_limit].rstrip("._") or "item"
    path = destination_dir / f"{safe_prefix}_{digest[:12]}{suffix}"
    storage_mode = _materialize_object(object_path, path, digest)
    relative = _relative_path(path)
    context.hash_index[digest] = relative
    return {
        "status": "ok",
        "bytes": len(body),
        "sha256": digest,
        "path": relative,
        "storage": {
            "mode": storage_mode,
            "object_path": _relative_path(object_path),
            "object_sha256": digest,
        },
    }


def _materialize_previous_record(
    context: _VersionContext,
    base_id: str,
    filename_prefix: str,
    previous: dict[str, Any],
    extension: str,
) -> dict[str, Any] | None:
    digest = str(previous.get("sha256") or "")
    raw_storage = previous.get("storage")
    storage: dict[str, Any] = raw_storage if isinstance(raw_storage, dict) else {}
    object_path = PROJECT_ROOT / str(storage.get("object_path", "")) if storage.get("object_path") else None
    source_path = object_path if object_path and object_path.exists() else _path_from_record(previous)
    if source_path is None or not source_path.exists():
        return None
    if not digest:
        digest = _file_sha256(source_path)
    elif _file_sha256(source_path) != digest:
        raise RuntimeError(f"registro anterior não corresponde ao SHA-256: {source_path}")
    object_path = _ensure_object(source_path.read_bytes(), digest)
    suffix = extension if extension.startswith(".") else f".{extension}"
    destination = context.staging_path / _safe_component(base_id) / f"{_safe_component(filename_prefix)}_{digest[:12]}{suffix}"
    storage_mode = _materialize_object(object_path, destination, digest)
    return {
        "status": "ok",
        "bytes": destination.stat().st_size,
        "sha256": digest,
        "path": _relative_path(destination),
        "storage": {
            "mode": storage_mode,
            "object_path": _relative_path(object_path),
            "object_sha256": digest,
        },
        "reutilizado_de": f"v{context.previous_version}" if context.previous_version else previous.get("path"),
        "reuso_motivo": "resposta HTTP 304" if previous.get("etag") or previous.get("last_modified") else "conteúdo anterior",
    }


def _base_record(context: _VersionContext, base: dict[str, Any], logical_id: str, origin: str, result: dict[str, Any], *, tipo: str, registros: int = 0) -> dict[str, Any]:
    path = result.get("path")
    record = {
        "logical_id": logical_id,
        "base_id": base["base_id"],
        "escopo": base.get("escopo", "experimental"),
        "natureza": base.get("natureza"),
        "tipo": tipo,
        "origem": origin,
        "coletado_em": context.collected_at,
        "manifest_hash": context.manifest_hash,
        "registros": registros,
        "source_file": path,
        "size_bytes": int(result.get("bytes", 0) or 0),
        **result,
    }
    context.items[logical_id] = record
    _checkpoint_write(context)
    return record


def _completed_item(context: _VersionContext, logical_id: str) -> dict[str, Any] | None:
    record = context.items.get(logical_id)
    return record if record and record.get("status") == "ok" and _path_from_record(record) else None


def _failed_record(
    context: _VersionContext,
    base: dict[str, Any],
    logical_id: str,
    origin: str,
    error: str,
    tipo: str = "desconhecido",
    *,
    status: str = "falhou",
) -> dict[str, Any]:
    return _base_record(
        context,
        base,
        logical_id,
        origin,
        {"status": status, "bytes": 0, "sha256": None, "path": None, "erro": error},
        tipo=tipo,
    )


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
            source_path = PROJECT_ROOT / source_path
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
    access = base.get("acesso", {})
    params = dict(access.get("params") or {})
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
            return [
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:{endpoint_name}:selecao",
                    url,
                    str(response.get("erro") or response.get("status") or "resposta JSON ausente"),
                    "json",
                )
            ]
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
                    reason = (
                        response.get("erro")
                        or response.get("observacao")
                        or f"resposta JSON ausente (status={response.get('status')}, HTTP={response.get('http_status')})"
                    )
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
            body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            page_digest = hashlib.sha256(body).hexdigest()
            if page_digest in seen_pages:
                output.append(
                    _failed_record(
                        context,
                        base,
                        f"{base['base_id']}:{endpoint_name}:pagina={page}:duplicada",
                        url,
                        "página repetida durante paginação; enumeração interrompida",
                        "json",
                    )
                )
                break
            seen_pages.add(page_digest)
            saved = _save_version_bytes(context, body, str(base["base_id"]), f"{endpoint_name}_pagina_{page}", ".json")
            _annotate_saved_page(context, saved, previous, response, fallback_used, fallback_trigger)
            records = _payload_records(payload)
            output.append(_base_record(context, base, logical_id, url, saved, tipo="json", registros=len(records)))
            if fallback_used or not page_field or not records or len(records) < page_size:
                break
            page += 1
        except Exception as exc:
            output.append(_failed_record(context, base, logical_id, url, f"{type(exc).__name__}: {exc}", "json"))
            break
    return output


def _collect_local_base(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    access = base.get("acesso", {})
    origin = access.get("origem") or base.get("local")
    if not origin:
        return [_failed_record(context, base, f"{base['base_id']}:local", "local", "origem local não declarada")]
    source = PROJECT_ROOT / str(origin)
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
            pdf_successes = sum(
                1 for record in output if record.get("status") == "ok" and record.get("tipo") == "pdf"
            )
            if pdf_successes >= target:
                return output
        if not new_candidates:
            break
        page += 1

    pdf_successes = sum(1 for record in output if record.get("status") == "ok" and record.get("tipo") == "pdf")
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
    for logical_id, item in list(context.items.items()):
        if item.get("base_id") == base.get("base_id") and item.get("status") != "ok":
            context.items.pop(logical_id, None)
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
    if discovered and not censo_mode and len(sources) < target:
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
    CAMINHOS.inventory_root.mkdir(parents=True, exist_ok=True)
    path = CAMINHOS.inventory_root / f"{inventory['version']}.json"
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
    CAMINHOS.corpus_inventory_path.parent.mkdir(parents=True, exist_ok=True)
    CAMINHOS.corpus_inventory_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    CAMINHOS.inventory_root.mkdir(parents=True, exist_ok=True)
    path = CAMINHOS.inventory_root / f"DIFF_{previous['version']}_{current['version']}.md"
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
    checkpoint_root = CAMINHOS.inventory_root / "checkpoints"
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
        staging_path = CAMINHOS.raw_root / ".staging" / f"v{version}"
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


def _scoped_manifest(
    manifest: dict[str, Any],
    command: str,
    *,
    official: bool,
    base_ids: list[str] | None,
    use_discovered_contracts: bool,
) -> dict[str, Any]:
    """Manifesto da execução: só bases oficiais, só contratos com a descoberta, ou só as bases pedidas."""
    if official and not base_ids:
        manifest = _manifest_for_scope(manifest, official=True)
    if use_discovered_contracts:
        manifest = {
            **manifest,
            "bases": [
                {
                    **base,
                    "acesso": {
                        **(base.get("acesso") or {}),
                        **(
                            {"resource_kinds": ["contrato"]}
                            if base.get("base_id") == "contratos_estruturado"
                            else {}
                        ),
                    },
                }
                for base in manifest.get("bases", [])
            ],
        }
    if use_discovered_contracts and (command != "update" or not official or base_ids):
        raise ValueError("--use-discovered-contracts requer update --official sem --base-id")
    if base_ids:
        if official:
            raise ValueError("--base-id só pode ser usado em uma execução experimental")
        available = {str(base["base_id"]): base for base in manifest["bases"]}
        unknown = sorted(set(base_ids) - set(available))
        if unknown:
            raise ValueError(f"base(s) não declarada(s) no manifesto: {', '.join(unknown)}")
        manifest = {**manifest, "bases": [available[base_id] for base_id in base_ids]}
    return manifest


def _load_adopt_attempt(
    adopt_attempt_inventory_path: Path,
    command: str,
    *,
    official: bool,
    base_ids: list[str] | None,
    use_discovered_contracts: bool,
) -> tuple[dict[str, Any], str, str]:
    """(inventário da tentativa, base adotada, SHA-256 do inventário), conferida a cobertura da tentativa."""
    if command != "update" or not official or base_ids or use_discovered_contracts:
        raise ValueError(
            "--adopt-attempt requer update --official sem --base-id e sem --use-discovered-contracts"
        )
    attempt_bytes = adopt_attempt_inventory_path.read_bytes()
    attempt_payload = json.loads(attempt_bytes.decode("utf-8"))
    if (
        not isinstance(attempt_payload, dict)
        or not isinstance(attempt_payload.get("arquivos"), list)
        or attempt_payload.get("official") is not False
        or attempt_payload.get("completo") is not True
    ):
        raise ValueError("--adopt-attempt requer o inventário de uma tentativa experimental concluída")
    attempt_base_ids = {
        str(record.get("base_id") or "")
        for record in attempt_payload["arquivos"]
        if isinstance(record, dict) and record.get("base_id")
    }
    if len(attempt_base_ids) != 1 or not attempt_base_ids.issubset(
        {"pca_agregado", "atas_conversacional"}
    ):
        raise ValueError(
            "a tentativa precisa conter registros de uma única base: "
            "pca_agregado ou atas_conversacional"
        )
    adopt_attempt_base_id = next(iter(attempt_base_ids))
    attempt_records = [
        record
        for record in attempt_payload["arquivos"]
        if isinstance(record, dict) and record.get("base_id") == adopt_attempt_base_id
    ]
    if not any(record.get("status") == "ok" for record in attempt_records):
        raise ValueError("a tentativa não tem artefatos bem-sucedidos para promover")
    _validate_adopt_attempt_coverage(
        adopt_attempt_inventory_path,
        attempt_payload,
        base_id=adopt_attempt_base_id,
    )
    return attempt_payload, adopt_attempt_base_id, hashlib.sha256(attempt_bytes).hexdigest()


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


@observar_offline(Stage.COLETA)
def _run_manifest_command(
    command: str,
    manifest_path: Path,
    *,
    official: bool,
    base_ids: list[str] | None = None,
    source_inventory_path: Path | None = None,
    adopt_attempt_inventory_path: Path | None = None,
    max_pages: int | None = None,
    use_discovered_contracts: bool = False,
) -> int:
    logger = _RunLogger(command)
    logger.event("manifest_load", "started", manifest=str(manifest_path))
    manifest = load_manifest(manifest_path)
    manifest_hash = _manifest_hash(manifest_path)
    manifest, external_inputs, adopt_attempt_inventory, adopt_attempt_base_id = _prepare_run_inputs(
        manifest, command, official=official, base_ids=base_ids, source_inventory_path=source_inventory_path,
        adopt_attempt_inventory_path=adopt_attempt_inventory_path, use_discovered_contracts=use_discovered_contracts,
    )
    logger.event(
        "manifest_load",
        "completed",
        scenario_id=manifest.get("scenario_id"),
        manifest_hash=manifest_hash,
    )
    if command == "fetch" and not base_ids and _reuse_complete_version(manifest_hash, logger):
        return 0
    allow_discovery_manifest_drift = command == "discover"
    allow_isolated_pca_manifest_drift = (
        command == "fetch"
        and not official
        and base_ids == ["pca_agregado"]
        and source_inventory_path is not None
    )
    resumable_version = _find_resumable_version(
        manifest_hash,
        manifest,
        allow_discovery_manifest_drift=allow_discovery_manifest_drift,
        allow_isolated_pca_manifest_drift=allow_isolated_pca_manifest_drift,
        expected_external_inputs=external_inputs,
    )
    previous = _previous_inventory(command, resumable_version)
    version = resumable_version or _next_version()
    previous_items, previous_version = _previous_items_and_version(previous)
    version_lock = _VersionLock(version)
    version_lock.acquire()
    try:
        context = _load_version_context(
            manifest,
            manifest_hash,
            version,
            previous_items=previous_items,
            previous_version=previous_version,
            allow_discovery_manifest_drift=allow_discovery_manifest_drift,
            allow_isolated_pca_manifest_drift=allow_isolated_pca_manifest_drift,
            external_inputs=external_inputs,
        )
        _seed_carried_selection(
            context, manifest, previous_items, logger,
            use_discovered_contracts=use_discovered_contracts,
            adopt_attempt_inventory=adopt_attempt_inventory,
            adopt_attempt_base_id=adopt_attempt_base_id,
            adopt_attempt_inventory_path=adopt_attempt_inventory_path,
        )
        logger.event(
            "version_resolution",
            "resumed" if resumable_version is not None else "started",
            version=f"v{version}",
        )
        if command == "discover":
            return _run_rj_discovery(context, manifest, version, logger, max_pages=max_pages)
        _collect_manifest_bases(
            context, manifest, logger,
            adopting=adopt_attempt_inventory is not None, use_discovered_contracts=use_discovered_contracts,
        )
        inventory = _run_inventory(manifest, version, manifest_hash, context, official=official)
        validation = validate_inventory_contract(manifest, inventory, official=official)
        if not validation.ok:
            return _report_incomplete_inventory(context, inventory, validation, logger)
        logger.event("validation", "passed", version=inventory["version"])
        _promote_version(context)
    finally:
        version_lock.release()
    logger.event("version_promotion", "completed", version=f"v{version}")
    inventory = _run_inventory(manifest, version, manifest_hash, context, official=official)
    return _report_promoted_inventory(inventory, previous, logger)


def _run_inventory(
    manifest: dict[str, Any], version: int, manifest_hash: str, context: _VersionContext, *, official: bool
) -> dict[str, Any]:
    return _build_inventory(
        manifest,
        version,
        manifest_hash,
        context.collected_at,
        context.items,
        inventory_type="manifest" if official else "experimental",
        official=official,
    )


def _prepare_run_inputs(
    manifest: dict[str, Any],
    command: str,
    *,
    official: bool,
    base_ids: list[str] | None,
    source_inventory_path: Path | None,
    adopt_attempt_inventory_path: Path | None,
    use_discovered_contracts: bool,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any] | None, str | None]:
    """(manifesto da execução, hashes das entradas externas, tentativa a adotar e a base dela)."""
    external_inputs: dict[str, str] = {}
    adopt_attempt_inventory: dict[str, Any] | None = None
    adopt_attempt_base_id: str | None = None
    manifest = _scoped_manifest(
        manifest, command, official=official, base_ids=base_ids, use_discovered_contracts=use_discovered_contracts
    )
    if source_inventory_path is not None:
        manifest, external_inputs = _apply_source_inventory(
            manifest, command, source_inventory_path, official=official, base_ids=base_ids
        )
    if adopt_attempt_inventory_path is not None:
        adopt_attempt_inventory, adopt_attempt_base_id, attempt_sha256 = _load_adopt_attempt(
            adopt_attempt_inventory_path, command, official=official, base_ids=base_ids,
            use_discovered_contracts=use_discovered_contracts,
        )
        external_inputs["adopt_attempt_inventory_sha256"] = attempt_sha256
    return manifest, external_inputs, adopt_attempt_inventory, adopt_attempt_base_id


def _reuse_complete_version(manifest_hash: str, logger: _RunLogger) -> bool:
    """fetch de um manifesto que já tem versão completa: reaproveita a versão em vez de coletar de novo."""
    existing = _find_complete_matching_manifest(manifest_hash)
    if not existing:
        return False
    _render_corpus_inventory(existing)
    logger.event("version_resolution", "reused", version=existing["version"])
    print(
        json.dumps(
            {"version": existing["version"], "reutilizado": True, "run_id": logger.run_id},
            ensure_ascii=False,
        )
    )
    return True


def _report_promoted_inventory(inventory: dict[str, Any], previous: dict[str, Any] | None, logger: _RunLogger) -> int:
    """Inventário e diff da versão promovida; 0 se completa, 2 se parcial."""
    inventory_path = _write_inventory(inventory)
    _render_corpus_inventory(inventory)
    diff_path = _diff_inventories(previous, inventory) if previous is not None else None
    logger.event(
        "inventory",
        "completed",
        version=inventory["version"],
        inventory_path=_relative_path(inventory_path),
        totals=inventory["totais"],
        complete=inventory["completo"],
    )
    summary = {"version": inventory["version"], "inventario": _relative_path(inventory_path), "diff": _relative_path(diff_path) if diff_path else None, "totais": inventory["totais"], "completo": inventory["completo"], "run_id": logger.run_id, "log": _relative_path(logger.path)}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if inventory["completo"] else 2


def _seed_carried_selection(
    context: _VersionContext,
    manifest: dict[str, Any],
    previous_items: dict[str, dict[str, Any]],
    logger: _RunLogger,
    *,
    use_discovered_contracts: bool,
    adopt_attempt_inventory: dict[str, Any] | None,
    adopt_attempt_base_id: str | None,
    adopt_attempt_inventory_path: Path | None,
) -> None:
    """Seleção dos contratos descobertos e/ou adoção da tentativa; as demais bases vêm da versão anterior."""
    if use_discovered_contracts:
        selected_count = _seed_discovered_contract_selection(context, manifest)
        carried_bases = {}
        for base in manifest.get("bases", []):
            base_id = str(base.get("base_id") or "")
            if base_id and base_id != "contratos_estruturado":
                carried_bases[base_id] = _carry_forward_base_records(
                    context,
                    base_id,
                    previous_items,
                )
        _checkpoint_write(context)
    if adopt_attempt_inventory is not None:
        carried_bases = {
            str(base.get("base_id")): _carry_forward_base_records(
                context,
                str(base.get("base_id")),
                previous_items,
            )
            for base in manifest.get("bases", [])
            if base.get("base_id")
        }
        adopted_count = _adopt_attempt_records(
            context,
            adopt_attempt_inventory,
            base_id=adopt_attempt_base_id,
            source_inventory_path=adopt_attempt_inventory_path,
        )
        context.selection = _selection_from_items(context.items)
        _checkpoint_write(context)
        logger.event(
            "attempt_adoption",
            "completed",
            base_id=adopt_attempt_base_id,
            adopted_records=adopted_count,
            carried_forward_bases=carried_bases,
            attempt_inventory=_relative_path(adopt_attempt_inventory_path),
        )
        if use_discovered_contracts:
            logger.event(
                "discovery_selection",
                "completed",
                base_id="contratos_estruturado",
                population="contrato_assinado_rj_unidades",
                selected_contracts=selected_count,
                scan_complete=bool(
                    context.discovery.get("contratos_estruturado", {})
                    .get("contrato_assinado_rj_unidades", {})
                    .get("scan_completo")
                ),
                carried_forward_bases=carried_bases,
            )


def _run_rj_discovery(
    context: _VersionContext,
    manifest: dict[str, Any],
    version: int,
    logger: _RunLogger,
    *,
    max_pages: int | None,
) -> int:
    """discover: publicações do RJ (órgãos e unidades) e, com as unidades verificadas, contratos assinados."""
    contract_base = next(
        (base for base in manifest["bases"] if base.get("base_id") == "contratos_estruturado"),
        None,
    )
    if contract_base is None:
        raise RuntimeError("manifesto não declara a base contratos_estruturado")
    access = contract_base.get("acesso") or {}
    publication_population = next(
        (
            item
            for item in access.get("populations", [])
            if str(item.get("resource_type") or "").casefold() in {"contratacao", "contratação"}
        ),
        None,
    )
    contract_population = next(
        (
            item
            for item in access.get("populations", [])
            if str(item.get("resource_type") or "").casefold() == "contrato_assinado"
        ),
        None,
    )
    if publication_population is None or contract_population is None:
        raise RuntimeError("manifesto precisa declarar contratações publicadas e contratos assinados")

    # A população de publicações encontra órgãos e unidades antes da consulta contratual.
    # O intervalo começa na adoção do PNCP e termina no corte já usado pelo levantamento.
    publication_params = {
        **(access.get("params") or {}),
        **(publication_population.get("params") or {}),
        "dataInicial": "2021-01-01",
        "dataFinal": "2026-09-25",
        "uf": "RJ",
    }
    publication_discovery = {
        **publication_population,
        "params": publication_params,
        "censo": True,
        "rate_limit_seg": max(float(access.get("rate_limit_seg") or 0), 3.0),
        "discovery_checkpoint": True,
        "discovery_key": "contratacao_publicacao_rj",
        "verify_discovered_units": True,
    }
    publication_base = {
        **contract_base,
        "acesso": {**access, "populations": [publication_discovery]},
    }
    started = time.perf_counter()
    _collect_provider_base(
        context,
        publication_base,
        discovery_only=True,
        max_pages=max_pages,
    )
    base_discovery = context.discovery.get("contratos_estruturado", {})
    publication_state = base_discovery.get("contratacao_publicacao_rj", {})
    unit_state = base_discovery.get("unidades_orgaos_rj", {})

    contract_state: dict[str, Any] = {}
    if publication_state.get("scan_completo") and unit_state.get("verificacao_completa"):
        scoped_contract_population = {
            **contract_population,
            "discovery_key": "contrato_assinado_rj_unidades",
        }
        contract_base_for_discovery = {
            **contract_base,
            "acesso": {**access, "populations": [scoped_contract_population]},
        }
        _collect_provider_base(
            context,
            contract_base_for_discovery,
            discovery_only=True,
            max_pages=max_pages,
        )
        contract_state = context.discovery.get("contratos_estruturado", {}).get(
            "contrato_assinado_rj_unidades", {}
        )
    _checkpoint_write(context)
    return _report_rj_discovery(context, version, logger, publication_state, unit_state, contract_state,
                                started=started, max_pages=max_pages)


def _report_rj_discovery(
    context: _VersionContext,
    version: int,
    logger: _RunLogger,
    publication_state: dict[str, Any],
    unit_state: dict[str, Any],
    contract_state: dict[str, Any],
    *,
    started: float,
    max_pages: int | None,
) -> int:
    """Progresso da descoberta no log e na saída: páginas, órgãos, unidades verificadas e contratos elegíveis."""
    logger.event(
        "rj_discovery",
        "completed" if contract_state.get("scan_completo") else "paused",
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        publication_pages_this_run=publication_state.get("pages_this_run", 0),
        publication_completed_pages=len(publication_state.get("completed_pages") or []),
        publication_scan_complete=bool(publication_state.get("scan_completo")),
        organizations=len(publication_state.get("cnpjs_derivados") or []),
        organizations_verified=unit_state.get("organizacoes_verificadas", 0),
        verified_rj_units=len(unit_state.get("verified_unit_scopes") or []),
        contract_pages_this_run=contract_state.get("pages_this_run", 0),
        eligible_contracts=len(contract_state.get("eligible_sources") or {}),
        contract_scan_complete=bool(contract_state.get("scan_completo")),
    )
    summary = {
        "version": f"v{version}",
        "discovery_only": True,
        "publication_period": ["2021-01-01", "2026-09-25"],
        "publication_pages_this_run": publication_state.get("pages_this_run", 0),
        "publication_completed_pages": len(publication_state.get("completed_pages") or []),
        "publication_scan_complete": bool(publication_state.get("scan_completo")),
        "organizations_discovered": len(publication_state.get("cnpjs_derivados") or []),
        "organizations_verified": unit_state.get("organizacoes_verificadas", 0),
        "verified_rj_units": len(unit_state.get("verified_unit_scopes") or []),
        "unit_verification_complete": bool(unit_state.get("verificacao_completa")),
        "contract_pages_this_run": contract_state.get("pages_this_run", 0),
        "eligible_contracts": len(contract_state.get("eligible_sources") or {}),
        "scan_complete": bool(contract_state.get("scan_completo")),
        "pca_registry_complete": False,
        "max_pages": max_pages,
        "promoted": False,
        "run_id": logger.run_id,
        "checkpoint": _relative_path(context.checkpoint_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _collect_manifest_bases(
    context: _VersionContext,
    manifest: dict[str, Any],
    logger: _RunLogger,
    *,
    adopting: bool,
    use_discovered_contracts: bool,
) -> None:
    """Coleta cada base do manifesto; na adoção nenhuma, e com a descoberta só os contratos."""
    for base in manifest["bases"]:
        if adopting:
            continue
        if use_discovered_contracts and base.get("base_id") != "contratos_estruturado":
            continue
        started = time.perf_counter()
        logger.event("base_collection", "started", base_id=base["base_id"])
        try:
            _collect_base(context, base)
        except Exception as exc:
            logger.event(
                "base_collection",
                "failed",
                base_id=base["base_id"],
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        logger.event(
            "base_collection",
            "completed",
            base_id=base["base_id"],
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            items=sum(1 for item in context.items.values() if item.get("base_id") == base["base_id"]),
        )


def _report_incomplete_inventory(
    context: _VersionContext, inventory: dict[str, Any], validation: Any, logger: _RunLogger
) -> int:
    """Inventário que não passou no contrato: fica incompleto, sem promoção, e a execução sai com 2."""
    _checkpoint_write(context, state="incomplete")
    inventory_path = _write_inventory(inventory)
    _render_corpus_inventory(inventory)
    logger.event(
        "validation",
        "failed",
        version=inventory["version"],
        errors=list(validation.errors),
    )
    logger.event(
        "inventory",
        "completed",
        version=inventory["version"],
        inventory_path=_relative_path(inventory_path),
        totals=inventory["totais"],
        complete=False,
    )
    summary = {
        "version": inventory["version"],
        "inventario": _relative_path(inventory_path),
        "diff": None,
        "totais": inventory["totais"],
        "completo": False,
        "validation_errors": list(validation.errors),
        "run_id": logger.run_id,
        "log": _relative_path(logger.path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 2


@observar_offline(Stage.INVENTARIO)
def _discovery_report(
    checkpoint_version: str,
    base_id: str,
    population: str,
    query: str | None,
    limit: int,
    offset: int,
) -> int:
    version = _version_number(checkpoint_version)
    if version is None:
        raise ValueError("--checkpoint-version deve usar o formato vN")
    checkpoint_path = CAMINHOS.inventory_root / "checkpoints" / f"v{version}.json"
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"checkpoint de descoberta não encontrado: {checkpoint_path}")

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    discovery = checkpoint.get("discovery") or {}
    base_discovery = discovery.get(base_id)
    if not isinstance(base_discovery, dict):
        raise ValueError(f"base sem descoberta no checkpoint: {base_id}")
    state = base_discovery.get(population)
    if not isinstance(state, dict):
        raise ValueError(f"população sem descoberta no checkpoint: {base_id}/{population}")

    sources = state.get("eligible_sources") or state.get("publication_sources") or {}
    if isinstance(sources, dict):
        entries = [(str(source_id), source_ref) for source_id, source_ref in sources.items()]
    elif isinstance(sources, list):
        entries = [(str(index), source_ref) for index, source_ref in enumerate(sources)]
    else:
        entries = []

    normalized_query = query.casefold().strip() if query else ""
    matches = []
    for source_id, source_ref in sorted(entries, key=lambda item: item[0]):
        searchable = json.dumps(
            {"id": source_id, "source_ref": source_ref},
            ensure_ascii=False,
            sort_keys=True,
        ).casefold()
        if not normalized_query or normalized_query in searchable:
            matches.append({"id": source_id, "source_ref": source_ref})

    report = {
        "checkpoint_version": f"v{version}",
        "checkpoint": _relative_path(checkpoint_path),
        "base_id": base_id,
        "population": population,
        "scan_completo": bool(state.get("scan_completo")),
        "paginas_concluidas": len(state.get("completed_pages") or []),
        "janelas_concluidas": len(state.get("completed_windows") or []),
        "referencias_registradas": len(entries),
        "contratos_elegiveis": state.get("total_contratos_elegiveis", len(entries)),
        "consulta": query,
        "resultados_encontrados": len(matches),
        "offset": offset,
        "limit": limit,
        "results": matches[offset : offset + limit],
    }


def _carry_forward_base_records(
    context: _VersionContext,
    base_id: str,
    previous_records: dict[str, dict[str, Any]],
) -> int:
    """Copy a complete prior base into staging without recollecting its sources."""
    for logical_id, record in list(context.items.items()):
        if record.get("base_id") == base_id and record.get("status") != "ok":
            context.items.pop(logical_id, None)
    copied = 0
    for logical_id, previous in previous_records.items():
        if previous.get("base_id") != base_id or previous.get("status") != "ok":
            continue
        current = context.items.get(logical_id)
        if current and current.get("status") == "ok" and _path_from_record(current):
            continue
        previous_path = _path_from_record(previous)
        if previous_path is None:
            raise RuntimeError(f"registro anterior sem caminho reutilizável: {logical_id}")
        digest = str(previous.get("sha256") or _file_sha256(previous_path))
        materialized = _materialize_previous_record(
            context,
            base_id,
            f"reuso_{digest[:16]}",
            previous,
            previous_path.suffix or ".bin",
        )
        if materialized is None:
            raise RuntimeError(f"arquivo anterior ausente para reutilização: {logical_id}")
        record = {**previous, **materialized}
        record["logical_id"] = logical_id
        record["base_id"] = base_id
        record["manifest_hash"] = context.manifest_hash
        record["source_file"] = materialized["path"]
        record["size_bytes"] = materialized["bytes"]
        context.items[logical_id] = record
        copied += 1
    return copied


def _adopt_attempt_records(
    context: _VersionContext,
    inventory: dict[str, Any],
    *,
    base_id: str,
    source_inventory_path: Path,
) -> int:
    """Copy a completed isolated base attempt into an official version."""
    adopted = 0
    for candidate in inventory.get("arquivos", []):
        if not isinstance(candidate, dict):
            continue
        logical_id = str(candidate.get("logical_id") or "")
        if not logical_id:
            raise ValueError("registro da tentativa sem logical_id")
        current = context.items.get(logical_id)
        if current and current.get("status") == "ok":
            current_path = _path_from_record(current)
            if current_path is not None and current_path.is_file():
                continue
        record = dict(candidate)
        record["manifest_hash"] = context.manifest_hash
        record["adotado_de_tentativa"] = _relative_path(source_inventory_path)
        if record.get("status") == "ok":
            source_path = _path_from_record(candidate)
            if source_path is None:
                raise RuntimeError(f"registro da tentativa sem arquivo: {logical_id}")
            materialized = _materialize_previous_record(
                context,
                base_id,
                f"adotado_{hashlib.sha256(logical_id.encode('utf-8')).hexdigest()[:16]}",
                candidate,
                source_path.suffix or ".bin",
            )
            if materialized is None:
                raise RuntimeError(f"arquivo da tentativa ausente: {logical_id}")
            record.update(materialized)
            record.pop("reutilizado_de", None)
            record["manifest_hash"] = context.manifest_hash
            record["adotado_de_tentativa"] = _relative_path(source_inventory_path)
        context.items[logical_id] = record
        adopted += 1
    return adopted


def _validate_adopt_attempt_coverage(
    inventory_path: Path,
    inventory: dict[str, Any],
    *,
    base_id: str,
) -> None:
    version = _version_number(str(inventory.get("version") or ""))
    if version is None:
        raise ValueError("inventário da tentativa sem versão vN válida")
    checkpoint_path = inventory_path.parent / "checkpoints" / f"v{version}.json"
    if not checkpoint_path.is_file():
        raise ValueError(f"checkpoint da tentativa não encontrado: {checkpoint_path}")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("state") != "promoted":
        raise ValueError("a tentativa ainda não terminou e não pode ser adotada")

    selected = (checkpoint.get("selection") or {}).get(base_id, [])
    if base_id == "atas_conversacional":
        _validate_atas_attempt(inventory, selected)
        return
    if base_id != "pca_agregado":
        raise ValueError(f"adoção de tentativa não suportada para {base_id}")
    _validate_pca_attempt(inventory, selected)


def _validate_atas_attempt(inventory: dict[str, Any], selected: list[Any]) -> None:
    """Cada compra selecionada com consulta de atas, cada ata com a listagem e todos os PDFs listados baixados."""
    base_id = "atas_conversacional"
    expected_purchases: set[str] = set()
    for entry in selected:
        source_value = entry.get("source_ref") if isinstance(entry, dict) else None
        if not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        control = str(source_ref.key.get("numeroControlePNCPCompra") or "")
        if source_ref.provider.casefold() == "pncp" and control:
            expected_purchases.add(control)
    if not expected_purchases:
        raise ValueError(
            "checkpoint da tentativa não lista compras PNCP selecionadas em atas_conversacional"
        )

    records = [
        record
        for record in inventory.get("arquivos", [])
        if isinstance(record, dict)
        and record.get("base_id") == base_id
        and record.get("status") == "ok"
    ]
    successful_purchase_queries = {
        str(
            (SourceRef.from_dict(record["source_ref"]).key).get(
                "numeroControlePNCPCompra"
            )
            or ""
        )
        for record in records
        if record.get("resource_kind") == "atas_compra"
        and isinstance(record.get("source_ref"), dict)
    }
    missing_purchases = expected_purchases - successful_purchase_queries
    if missing_purchases:
        raise ValueError(
            f"tentativa incompleta: {len(missing_purchases)} de "
            f"{len(expected_purchases)} compras selecionadas sem consulta de atas bem-sucedida"
        )

    ata_sources = {
        SourceRef.from_dict(record["source_ref"]).stable_id
        for record in records
        if record.get("resource_kind") == "ata"
        and isinstance(record.get("source_ref"), dict)
    }
    listed_document_sources = {
        SourceRef.from_dict(record["source_ref"]).stable_id
        for record in records
        if record.get("resource_kind") == "documentos_ata"
        and isinstance(record.get("source_ref"), dict)
    }
    if not ata_sources or ata_sources != listed_document_sources:
        raise ValueError(
            "tentativa incompleta: metadados das atas e listagens de documentos não correspondem"
        )

    listed_documents = 0
    for record in records:
        if record.get("resource_kind") != "documentos_ata":
            continue
        source_path = _path_from_record(record)
        if source_path is None or not source_path.is_file():
            raise ValueError("listagem de documentos da tentativa sem arquivo materializado")
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("listagem de documentos da ata não contém uma lista")
        listed_documents += len(payload)

    downloaded_pdfs = sum(
        1
        for record in records
        if (path := _path_from_record(record)) is not None
        and path.suffix.casefold() == ".pdf"
        and path.is_file()
    )
    if listed_documents != downloaded_pdfs:
        raise ValueError(
            f"tentativa incompleta: {listed_documents} documentos listados e "
            f"{downloaded_pdfs} PDFs baixados"
        )


def _manifest_parser() -> argparse.ArgumentParser:
    """Comandos fetch, update, discover, discovery e inventory, com as opções de cada um."""
    parser = argparse.ArgumentParser(description="Materializa o corpus declarado pelo manifesto.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("fetch", "update"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--manifest", type=Path, default=MANIFEST_DEFAULT)
        command_parser.add_argument("--data-root", type=Path, default=None)
        command_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
        command_parser.add_argument(
            "--base-id",
            action="append",
            dest="base_ids",
            metavar="ID",
            help="limita a execução a uma base declarada; pode ser repetido em modo experimental",
        )
        if command == "fetch":
            command_parser.add_argument(
                "--source-inventory",
                type=Path,
                help="inventário anterior com SourceRef PNCP; usado com --base-id editais_normativo, pca_agregado ou atas_conversacional",
            )
        if command == "update":
            command_parser.add_argument("--dry-run", action="store_true", help="mostra o plano sem modificar arquivos")
            command_parser.add_argument(
                "--adopt-attempt",
                type=Path,
                help="promove uma tentativa experimental isolada de pca_agregado ou atas_conversacional, carregando as demais bases da versão anterior",
            )
            command_parser.add_argument(
                "--use-discovered-contracts",
                action="store_true",
                help="usa até o alvo de contratos elegíveis já registrado no checkpoint v3",
            )
    discovery_parser = subparsers.add_parser(
        "discover",
        help="retoma a descoberta nacional de contratos sem baixar recursos nem promover versão",
        description="Descobre e conta contratos assinados no RJ, registrando o progresso no checkpoint existente sem materializar recursos ou promover uma versão.",
    )
    discovery_parser.add_argument("--manifest", type=Path, default=MANIFEST_DEFAULT)
    discovery_parser.add_argument("--data-root", type=Path, default=None)
    discovery_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
    discovery_parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="limita as páginas consultadas nesta execução; sem limite, conclui toda a descoberta",
    )
    report_parser = subparsers.add_parser(
        "discovery",
        help="consulta referências já registradas no checkpoint, sem acessar a API",
    )
    report_parser.add_argument("--checkpoint-version", default="v3", help="checkpoint a consultar, como v3")
    report_parser.add_argument("--data-root", type=Path, default=None)
    report_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
    report_parser.add_argument("--base-id", default="contratos_estruturado")
    report_parser.add_argument("--population", default="contrato_assinado_rj_unidades")
    report_parser.add_argument("--query", help="busca parcial sem distinção de maiúsculas em IDs e campos da referência")
    report_parser.add_argument("--limit", type=int, default=50, help="máximo de referências retornadas (1–500)")
    report_parser.add_argument("--offset", type=int, default=0, help="número de resultados a pular")
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--corpus-version", required=True, type=str, help="versão do corpus a inspecionar, como v1")
    inventory_parser.add_argument("--data-root", type=Path, default=None)
    inventory_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
    return parser


def _manifest_main(argv: list[str]) -> int:
    parser = _manifest_parser()
    args = parser.parse_args(argv)
    if args.command == "discover" and args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages deve ser maior que zero")
    if args.command == "discovery" and not 1 <= args.limit <= 500:
        parser.error("--limit deve estar entre 1 e 500")
    if args.command == "discovery" and args.offset < 0:
        parser.error("--offset não pode ser negativo")
    official_root = (PROJECT_ROOT / "data").resolve()
    if args.official:
        if args.data_root is not None and args.data_root.resolve() != official_root:
            parser.error("--official não pode ser combinado com outro --data-root")
        data_root = official_root
    else:
        if args.data_root is None:
            parser.error("coletas não oficiais exigem --data-root; use --official para o corpus oficial")
        data_root = args.data_root.resolve()
    _configure_data_root(data_root)
    if args.command == "discovery":
        return _discovery_report(
            args.checkpoint_version,
            args.base_id,
            args.population,
            args.query,
            args.limit,
            args.offset,
        )
    if args.command == "inventory":
        version = _version_number(args.corpus_version)
        if version is None:
            parser.error("--corpus-version deve usar o formato vN")
        inventory = _inventory_for_version(version)
        print(json.dumps({"version": inventory["version"], "totais": inventory["totais"]}, ensure_ascii=False))
        return 0
    if args.command == "update" and args.dry_run:
        if args.base_ids:
            parser.error("--base-id não é compatível com update --dry-run")
        if args.use_discovered_contracts:
            parser.error("--use-discovered-contracts não é compatível com update --dry-run")
        if args.adopt_attempt:
            parser.error("--adopt-attempt não é compatível com update --dry-run")
        return _dry_run_update(args.manifest.resolve(), official=args.official)
    run_kwargs = {"official": args.official}
    if getattr(args, "base_ids", None):
        run_kwargs["base_ids"] = args.base_ids
    if getattr(args, "source_inventory", None):
        run_kwargs["source_inventory_path"] = args.source_inventory.resolve()
    if args.command == "discover":
        run_kwargs["max_pages"] = args.max_pages
    if args.command == "update" and args.use_discovered_contracts:
        run_kwargs["use_discovered_contracts"] = True
    if args.command == "update" and args.adopt_attempt:
        run_kwargs["adopt_attempt_inventory_path"] = args.adopt_attempt.resolve()
    return _run_manifest_command(args.command, args.manifest.resolve(), **run_kwargs)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return _manifest_main(arguments)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"erro: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
