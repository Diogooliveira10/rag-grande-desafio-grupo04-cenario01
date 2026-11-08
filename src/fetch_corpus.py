r"""Materializa o corpus declarado pelo manifesto.

Uso:
    py -m src.fetch_corpus fetch --manifest corpus_manifest.yaml --official
    py -m src.fetch_corpus update --manifest corpus_manifest.yaml --official
    py -m src.fetch_corpus discover --manifest corpus_manifest.yaml --official --max-pages 2
    py -m src.fetch_corpus inventory --corpus-version v1 --official

As versões são coletadas com checkpoint, staging, inventário e deduplicação
por conteúdo. Coletas experimentais exigem ``--data-root`` explícito.

Organização (2026-11-07): este arquivo é o caso de uso e a CLI (comandos,
descoberta no RJ, relatórios). As peças ficam em ``src/collection/``:
``paths`` (a árvore de dados ``CAMINHOS``, lida como ``coleta.CAMINHOS``),
``manifest``/``manifest_yaml``, ``checkpoint``, ``storage``, ``collect`` (a
coleta em si), ``inventory``, ``rest``, ``selection``, ``sources`` e ``checks``.
Os nomes de antes continuam importáveis daqui, exceto ``CAMINHOS`` e
``PROJECT_ROOT``, que moram só em ``src/collection/paths.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.panel import Panel
from rich.table import Table

try:
    from .providers.base import SourceRef
    from .corpus_validator import validate_inventory_contract
except ImportError:
    from providers.base import SourceRef
    from corpus_validator import validate_inventory_contract

# Para rodar também como script (``py src/fetch_corpus.py``): a raiz do projeto no sys.path.
_RAIZ_DO_PROJETO = Path(__file__).resolve().parents[1]
if str(_RAIZ_DO_PROJETO) not in sys.path:
    sys.path.insert(0, str(_RAIZ_DO_PROJETO))

from src.obs import Stage, new_query_id, registrar
from src.log_context import current_execution
from src.offline_observation import observar_offline

from src.collection import paths as coleta  # noqa: E402  (CAMINHOS e PROJECT_ROOT: coleta.CAMINHOS)
# Peças movidas para src/collection/ (2026-11-07); os nomes continuam importáveis daqui.
from src.collection.paths import (  # noqa: E402,F401  (reexportados)
    CaminhosDaColeta,
    _configure_data_root,
    _available_versions,
    _next_version,
    _latest_version,
    _version_path,
    _inventory_path,
    _relative_path,
    _path_from_record,
    _effective_objects_root,
    _object_path,
)
from src.collection.manifest import (  # noqa: E402,F401  (reexportados)
    load_manifest,
    _manifest_for_scope,
    _manifest_hash,
)
from src.collection.storage import (  # noqa: E402,F401  (reexportados)
    _wait_rate_limit,
    _ensure_object,
    _save_version_bytes,
    _materialize_previous_record,
    _base_record,
    _completed_item,
    _failed_record,
)
from src.collection.checkpoint import (  # noqa: E402,F401  (reexportados)
    _seed_discovered_contract_selection,
    _record_pncp_discovery_page,
    _checkpoint_item_errors,
    _validate_checkpoint,
    _checkpoint_write,
    _load_version_context,
)
from src.collection.collect import (  # noqa: E402,F401  (reexportados)
    _collect_http_item,
    _collect_selected_rest_endpoint,
    _unmodified_page_record,
    _rest_page_failure,
    _collect_rest_endpoint,
    _collect_local_base,
    _collect_pncp_documents,
    _collect_resource_set,
    _attach_discovery_checkpoint,
    _verify_population_units,
    _discover_provider_sources,
    _collect_provider_base,
    _ProviderSourceCollector,
    _collect_base,
)
from src.collection.inventory import (  # noqa: E402,F401  (reexportados)
    _build_inventory,
    _write_inventory,
    _render_corpus_inventory,
    _load_inventory,
    _scan_existing_version,
    _inventory_for_version,
    _diff_inventories,
    _promote_version,
    _find_complete_matching_manifest,
    _find_resumable_version,
    _previous_inventory,
)
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
        self.path = coleta.CAMINHOS.runs_root / f"{self.run_id}.jsonl"
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
        self.path = coleta.CAMINHOS.raw_root / ".staging" / f"v{version}.lock"
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


MANIFEST_DEFAULT = coleta.PROJECT_ROOT / "corpus_manifest.yaml"


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
    checkpoint_path = coleta.CAMINHOS.inventory_root / "checkpoints" / f"v{version}.json"
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
    official_root = (coleta.PROJECT_ROOT / "data").resolve()
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
