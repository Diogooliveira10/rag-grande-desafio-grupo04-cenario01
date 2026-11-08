"""Fontes e inventário por base: populações, retomada, metadados dos documentos e acesso às fontes de PCA e contratos."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any


from src.providers.base import SourceRef
from src.collection.checks import source_check
from src.collection.selection import _VersionContext


def _version_number(value: str | Path) -> int | None:
    match = re.fullmatch(r"v(\d+)", str(value))
    return int(match.group(1)) if match else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _materialize_object(object_path: Path, destination: Path, digest: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _file_sha256(destination) != digest:
            raise RuntimeError(f"destino existente não corresponde ao SHA-256: {destination}")
        return "existente"
    try:
        os.link(object_path, destination)
        return "hardlink"
    except FileExistsError:
        if _file_sha256(destination) != digest:
            raise RuntimeError(f"destino criado com conteúdo diferente: {destination}")
        return "existente"
    except OSError:
        temporary = destination.with_name(f".{destination.name}.tmp")
        shutil.copy2(object_path, temporary)
        try:
            if destination.exists():
                if _file_sha256(destination) != digest:
                    raise RuntimeError(f"destino criado com conteúdo diferente: {destination}")
                return "existente"
            temporary.replace(destination)
            return "copy"
        finally:
            temporary.unlink(missing_ok=True)


def _previous_record(context: _VersionContext, logical_id: str) -> dict[str, Any] | None:
    record = context.previous_items.get(logical_id)
    return record if isinstance(record, dict) and record.get("status") == "ok" else None


def _provider_populations(access: dict[str, Any], target: int) -> list[dict[str, Any]]:
    """Populações declaradas no acesso; sem elas, uma só, com o recurso e os parâmetros do próprio acesso."""
    return access.get("populations") or [
        {
            "resource_type": access.get("resource_type"),
            "params": access.get("params") or {},
            "censo": bool(access.get("censo")),
            "exigir_cadastro_completo": bool(access.get("exigir_cadastro_completo")),
            "volume_alvo": target,
        }
    ]


def _resumable_sources(
    context: _VersionContext,
    base: dict[str, Any],
    access: dict[str, Any],
    populations: list[dict[str, Any]],
    target: int,
    *,
    discovery_only: bool,
) -> tuple[list[dict[str, Any]], set[str]]:
    """(fontes, ids das retomadas): explícitas, da seleção congelada ou dos itens já coletados."""
    sources = [] if discovery_only else access.get("sources") or []
    resumed_source_ids: set[str] = set()
    resource_type = str(access.get("resource_type") or "").casefold()
    has_signed_contract_population = any(
        str(population.get("resource_type") or "").casefold() == "contrato_assinado"
        and population.get("censo_nacional")
        for population in populations
    )
    preselected_entries = getattr(context, "selection", {}).get(str(base["base_id"]))
    selected_entries = (
        None
        if discovery_only or (has_signed_contract_population and not preselected_entries)
        else preselected_entries
    )
    if not sources and selected_entries:
        sources = [
            entry["source_ref"]
            for entry in selected_entries
            if isinstance(entry, dict) and isinstance(entry.get("source_ref"), dict)
        ]
        resumed_source_ids = {
            SourceRef.from_dict(source_value).stable_id
            for source_value in sources
        }
    if not sources and not discovery_only:
        existing_sources: dict[str, dict[str, Any]] = {}
        for item in context.items.values():
            if item.get("base_id") != base.get("base_id") or not int(item.get("unidades_logicas", 0) or 0):
                continue
            source_value = item.get("source_ref")
            if not isinstance(source_value, dict):
                continue
            try:
                source_ref = SourceRef.from_dict(source_value)
            except (TypeError, ValueError, KeyError):
                continue
            if source_ref.resource_type.casefold() == resource_type:
                existing_sources[source_ref.stable_id] = source_ref.to_dict()
        if len(existing_sources) >= target and not has_signed_contract_population:
            sources = [existing_sources[key] for key in sorted(existing_sources)[:target]]
            resumed_source_ids = {
                SourceRef.from_dict(source_value).stable_id
                for source_value in sources
            }
    return sources, resumed_source_ids


def _population_query(
    context: _VersionContext,
    base: dict[str, Any],
    access: dict[str, Any],
    population: dict[str, Any],
    target: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(consulta ao provider para a população, unidades RJ verificadas que ela usa)."""
    population_target = int(population.get("volume_alvo") or target)
    query = {
        **access,
        **population,
        "resource_type": population.get("resource_type") or access.get("resource_type"),
        "params": {**(access.get("params") or {}), **(population.get("params") or {})},
        "volume_alvo": population_target,
        "selecao": base.get("selecao") or {},
    }
    resource_name = str(query["resource_type"]).casefold()
    if resource_name in {"contrato_assinado", "contrato_pncp"} and population.get("censo_nacional"):
        query["params"]["dataInicial"] = min(
            str(query["params"].get("dataInicial") or "2021-01-01"),
            "2021-01-01",
        )
    verified_unit_scopes: list[dict[str, Any]] = []
    if resource_name in {"contrato_assinado", "contrato_pncp"}:
        base_discovery = context.discovery.get(str(base["base_id"]), {})
        unit_state = base_discovery.get("unidades_orgaos_rj", {})
        if not unit_state.get("verificacao_completa"):
            raise RuntimeError(
                "unidades dos órgãos ainda não foram verificadas; execute discover e retome até concluir"
            )
        verified_unit_scopes = list(unit_state.get("verified_unit_scopes") or [])
        query["verified_unit_scopes"] = verified_unit_scopes
        query["require_verified_unit_scopes"] = True
    return query, verified_unit_scopes


def _resumed_records(
    context: _VersionContext,
    base: dict[str, Any],
    sources: list[dict[str, Any]],
    resumed_source_ids: set[str],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """(registros já coletados, se cobrem todas as fontes retomadas; fontes que ainda faltam coletar)."""
    cached_records = [
        item
        for item in context.items.values()
        if item.get("base_id") == base.get("base_id")
        and item.get("status") == "ok"
        and isinstance(item.get("source_ref"), dict)
        and SourceRef.from_dict(item["source_ref"]).stable_id in resumed_source_ids
    ]
    cached_source_ids = {
        SourceRef.from_dict(item["source_ref"]).stable_id
        for item in cached_records
    }
    if cached_source_ids == resumed_source_ids:
        return sorted(cached_records, key=lambda item: str(item.get("logical_id") or "")), sources
    return None, [
        source_value
        for source_value in sources
        if SourceRef.from_dict(source_value).stable_id not in cached_source_ids
    ]


def _normalized_ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


# Metadados do documento anexo lidos da listagem do PNCP e os nomes com que o
# documento os expõe.
_DOCUMENT_METADATA_FIELDS = (
    "sequencialDocumento",
    "titulo",
    "tipoDocumentoNome",
    "dataPublicacaoPncp",
    "dataVigenciaInicio",
    "dataVigenciaFim",
)


_DOCUMENT_METADATA_ALIASES = {
    "titulo_documento": "titulo",
    "tipo_documento": "tipoDocumentoNome",
    "data_publicacao": "dataPublicacaoPncp",
    "data_vigencia_inicio": "dataVigenciaInicio",
    "data_vigencia_fim": "dataVigenciaFim",
}


def _document_metadata_by_url(listing_payload: Any, filter_terms: list[str], filter_fields: list[str]) -> dict[str, dict[str, Any]]:
    """Metadados de cada documento da listagem que passa no filtro de termos, pela URL do arquivo."""
    document_metadata_by_url: dict[str, dict[str, Any]] = {}
    for document in source_check.records_from_payload(listing_payload):
        if not isinstance(document, dict):
            continue
        searchable = " ".join(
            str(document.get(field) or "")
            for field in filter_fields
        ).casefold()
        searchable = _normalized_ascii(searchable)
        if filter_terms and not any(term in searchable for term in filter_terms):
            continue
        document_url = next(
            (
                document.get(key)
                for key in ("url", "uri", "location")
                if isinstance(document.get(key), str)
                and document[key].startswith(("http://", "https://"))
            ),
            None,
        )
        if document_url:
            document_metadata = {
                key: document.get(key)
                for key in _DOCUMENT_METADATA_FIELDS
                if document.get(key) not in (None, "")
            }
            document_metadata.update(
                {
                    alias: document_metadata[source_key]
                    for alias, source_key in _DOCUMENT_METADATA_ALIASES.items()
                    if document_metadata.get(source_key) not in (None, "")
                }
            )
            document_metadata_by_url[str(document_url)] = document_metadata
    return document_metadata_by_url


def _base_inventory(base: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumo de uma base no inventário: arquivos, itens, formatos, recursos e status frente ao alvo."""
    base_records = [record for record in records if record.get("base_id") == base["base_id"]]
    successful_base = [record for record in base_records if record.get("status") == "ok" and record.get("path")]
    base_failures = [record for record in base_records if record.get("status") != "ok"]
    observed_records = sum(
        int(record.get("unidades_logicas", record.get("registros", 0)) or 0)
        for record in successful_base
    )
    has_record_counts = any(
        "unidades_logicas" in record or "registros" in record
        for record in successful_base
    )
    observed = observed_records if has_record_counts else len(successful_base)
    formatos: dict[str, int] = {}
    por_recurso: dict[str, dict[str, int]] = {}
    for record in successful_base:
        tipo = str(record.get("tipo") or "bin")
        formatos[tipo] = formatos.get(tipo, 0) + 1
        resource_kind = str(
            record.get("resource_kind")
            or (record.get("source_ref") or {}).get("resource_type")
            or "desconhecido"
        )
        counts = por_recurso.setdefault(
            resource_kind,
            {"artefatos": 0, "registros": 0, "unidades_logicas": 0},
        )
        counts["artefatos"] += 1
        counts["registros"] += int(record.get("registros", 0) or 0)
        counts["unidades_logicas"] += int(record.get("unidades_logicas", 0) or 0)
    target = base.get("volume_alvo")
    target_value = int(target) if target is not None else None
    if any(record.get("status") == "not_configured" for record in base_records):
        base_status = "not_configured"
    elif not base_records:
        base_status = "empty"
    elif not successful_base:
        base_status = "failed"
    elif base_failures or (target_value is not None and observed < target_value):
        base_status = "partial"
    else:
        base_status = "complete"
    return {
        "base_id": base["base_id"],
        "escopo": base.get("escopo", "experimental"),
        "natureza": base.get("natureza"),
        "status": base_status,
        "arquivos": len(successful_base),
        "itens": observed,
        "formatos": formatos,
        "por_recurso": por_recurso,
        "bytes": sum(int(record.get("bytes", 0) or 0) for record in successful_base),
        "volume_alvo": target,
        "volume_obtido": observed,
        "volume_observado": observed,
        "divergencia_alvo": observed - target_value if target_value is not None else None,
        "failure_count": len(base_failures),
    }


# Tipos de SourceRef que identificam a contratação ou o contrato de origem.
_CONTRACT_SOURCE_TYPES = {"contratacao", "contratação", "contrato_assinado", "contrato_pncp"}


def _linked_contract_sources(
    inventory_payload: dict[str, Any], source_base_id: str, *, pncp_only: bool
) -> dict[str, dict[str, Any]]:
    """SourceRefs de contratações e contratos da base de origem no inventário, por stable_id."""
    linked_sources: dict[str, dict[str, Any]] = {}
    for record in inventory_payload.get("arquivos", []):
        if not isinstance(record, dict) or record.get("base_id") != source_base_id:
            continue
        source_value = record.get("source_ref")
        if not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        if pncp_only and source_ref.provider.casefold() != "pncp":
            continue
        if source_ref.resource_type.casefold() not in _CONTRACT_SOURCE_TYPES:
            continue
        linked_sources[source_ref.stable_id] = source_ref.to_dict()
    return linked_sources


def _pca_source_access(
    inventory_payload: dict[str, Any],
    isolated_base: dict[str, Any],
    isolated_access: dict[str, Any],
    source_inventory_path: Path,
) -> dict[str, Any]:
    """pca_agregado isolado: contratos e PCAs explícitos do inventário, sem censo nem cadastro completo."""
    source_types_by_base = {
        "contratos_estruturado": _CONTRACT_SOURCE_TYPES,
        "pca_agregado": {"pca", "pca_agregado"},
    }
    selected_sources: dict[str, dict[str, Any]] = {}
    sources_by_base = {source_base: set() for source_base in source_types_by_base}
    for record in inventory_payload.get("arquivos", []):
        if not isinstance(record, dict):
            continue
        source_base_id = str(record.get("base_id") or "")
        source_value = record.get("source_ref")
        if source_base_id not in source_types_by_base or not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        if source_ref.resource_type.casefold() not in source_types_by_base[source_base_id]:
            continue
        selected_sources[source_ref.stable_id] = source_ref.to_dict()
        sources_by_base[source_base_id].add(source_ref.stable_id)
    if not sources_by_base["contratos_estruturado"]:
        raise ValueError(
            "inventário não contém SourceRef de contratações/contratos em contratos_estruturado"
        )
    pca_target = int(isolated_base.get("volume_alvo") or 1)
    if len(sources_by_base["pca_agregado"]) < pca_target:
        raise ValueError(
            f"inventário contém {len(sources_by_base['pca_agregado'])} SourceRefs PCA; "
            f"são necessários ao menos {pca_target} para atender o alvo de pca_agregado"
        )
    if not selected_sources:
        raise ValueError(f"inventário não contém SourceRef PNCP elegíveis: {source_inventory_path}")
    return {
        **isolated_access,
        "sources": [selected_sources[key] for key in sorted(selected_sources)],
        "preserve_explicit_sources": True,
        "resource_kinds": [
            "pca_planejado",
            "pca_exportacao",
            "contrato",
            "contratacao_detalhe",
            "contratacao_vinculada",
            "itens",
        ],
        # A execução recebe fontes explícitas; não executa o censo PCA nem
        # exige o cadastro completo de CNPJs configurado para o percurso oficial.
        "censo": False,
        "exigir_cadastro_completo": False,
        "populations": [],
    }


def _apply_source_inventory(
    manifest: dict[str, Any],
    command: str,
    source_inventory_path: Path,
    *,
    official: bool,
    base_ids: list[str] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Base isolada alimentada pelas SourceRefs de um inventário anterior; devolve o manifesto e o hash dele."""
    isolated_source_bases = {"editais_normativo", "pca_agregado", "atas_conversacional"}
    if (
        command != "fetch"
        or official
        or base_ids is None
        or len(base_ids) != 1
        or base_ids[0] not in isolated_source_bases
    ):
        raise ValueError(
            "--source-inventory requer fetch experimental limitado a --base-id editais_normativo, pca_agregado ou atas_conversacional"
        )
    inventory_bytes = source_inventory_path.read_bytes()
    inventory_payload = json.loads(inventory_bytes.decode("utf-8"))
    if not isinstance(inventory_payload, dict) or not isinstance(
        inventory_payload.get("arquivos"), list
    ):
        raise ValueError("--source-inventory precisa apontar para um inventário com lista 'arquivos'")
    external_inputs = {
        "source_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest()
    }
    isolated_base = manifest["bases"][0]
    isolated_access = isolated_base.get("acesso") or {}
    if base_ids[0] == "pca_agregado":
        updated_access = _pca_source_access(inventory_payload, isolated_base, isolated_access, source_inventory_path)
    else:
        source_base_id = str(
            isolated_access.get("linked_source_inventory_base") or "contratos_estruturado"
        )
        is_editais = base_ids[0] == "editais_normativo"
        linked_sources = _linked_contract_sources(inventory_payload, source_base_id, pncp_only=not is_editais)
        if not linked_sources:
            raise ValueError(
                f"inventário não contém SourceRef PNCP elegíveis em {source_base_id}: {source_inventory_path}"
            )
        if is_editais:
            updated_access = {**isolated_access, "linked_sources": list(linked_sources.values())}
        else:
            updated_access = {
                **isolated_access,
                "sources": [linked_sources[key] for key in sorted(linked_sources)],
                "preserve_explicit_sources": True,
                "resource_kinds": [
                    "atas_compra",
                    "ata",
                    "documentos_ata",
                    "contratos_vinculados_ata",
                ],
                # Este percurso segue somente as compras explícitas do inventário,
                # sem iniciar uma busca global de atas nem depender do censo PNCP.
                "censo": False,
                "exigir_cadastro_completo": False,
                "populations": [],
            }
    manifest = {
        **manifest,
        "bases": [{**isolated_base, "acesso": updated_access}],
    }
    return manifest, external_inputs


def _previous_items_and_version(previous: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], int | None]:
    """(registros da versão anterior por logical_id, número dela) para reaproveitar o que não mudou."""
    previous_items = {
        str(record["logical_id"]): record
        for record in (previous or {}).get("arquivos", [])
        if isinstance(record, dict) and record.get("logical_id")
    }
    previous_version = _version_number(str(previous["version"])) if previous and previous.get("version") else None
    return previous_items, previous_version
