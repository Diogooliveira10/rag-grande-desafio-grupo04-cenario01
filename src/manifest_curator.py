"""Curadoria explícita de fontes antes da coleta do corpus.

Este módulo descobre fontes candidatas, gera propostas e mostra diffs sem
alterar o manifesto. A alteração só acontece em ``apply`` e exige que o hash
do manifesto ainda seja o mesmo usado na proposta.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from InquirerPy import inquirer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "corpus_manifest.yaml"
PROPOSALS_ROOT = PROJECT_ROOT / "data" / "proposals"
CURATION_COMMANDS = {"discover", "propose", "normative-proposal", "review", "manifest-diff", "apply"}
console = Console()
app = typer.Typer(
    name="source_catalog",
    help="Descobre fontes e prepara alterações revisáveis para o corpus_manifest.yaml.",
    no_args_is_help=True,
)


def manifest_hash(path: Path) -> str:
    """Calcula o hash dos bytes exatos do manifesto versionado."""
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _load_manifest(path: Path) -> dict[str, Any]:
    """Usa o mesmo parser/validador da coleta sem duplicar sua implementação."""
    try:
        from .fetch_corpus import load_manifest
    except ImportError:
        from fetch_corpus import load_manifest

    return load_manifest(path)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _declared_urls(manifest: dict[str, Any]) -> set[str]:
    urls: set[str] = set()

    def visit(value: Any) -> None:
        if _http_url(value):
            urls.add(value)
        elif isinstance(value, dict):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    for base in manifest.get("bases", []):
        visit(base.get("acesso", {}))
    return urls


def _safe_base_id(source_id: str, url: str) -> str:
    candidate = source_id.strip().lower() or "fonte_nova"
    candidate = re.sub(r"[^a-z0-9_]+", "_", candidate).strip("_")
    return f"proposta_{candidate or 'fonte_nova'}"


def _infer_natureza(file_type: str) -> str:
    return "normativa" if file_type in {"pdf", "html", "htm"} else "estruturada"


def _infer_access(file_type: str, url: str) -> dict[str, Any]:
    access_type = "http_pdf" if file_type == "pdf" else "http_misto"
    return {"tipo": access_type, "urls": [url], "rate_limit_seg": 1.0}


def _source_records(checked_at: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for result in results:
        url = result.get("url")
        if not _http_url(url):
            continue
        records.append(
            {
                "source_id": str(result.get("id") or "unknown"),
                "nome": str(result.get("nome") or result.get("id") or "fonte"),
                "url": url,
                "file_type": str(result.get("tipo") or result.get("content_type") or "json").split("/")[-1],
                "status": result.get("status"),
                "http_status": result.get("http_status"),
                "checked_at": checked_at,
            }
        )
    return records


def discover(config: Any) -> dict[str, Any]:
    """Executa a verificação existente e retorna um snapshot curável."""
    try:
        from .source_catalog import run_checks
    except ImportError:
        from source_catalog import run_checks

    checked_at, results = run_checks(config)
    return {
        "discovery_type": "source_catalog",
        "checked_at": checked_at,
        "parameters": {
            "ano": config.ano,
            "uf": config.uf,
            "modalidade": config.modalidade,
            "tamanho_pagina": config.tamanho_pagina,
        },
        "sources": _source_records(checked_at, results),
    }


def _write_json(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _proposal_entry(source: dict[str, Any]) -> dict[str, Any]:
    file_type = str(source.get("file_type", "json")).lower()
    source_ref = source.get("source_ref")
    if source_ref:
        source_ref = dict(source_ref)
        provider = str(source_ref.get("provider") or source.get("provider") or "").strip()
        resource_type = str(source_ref.get("resource_type") or "resource").strip()
        access = {
            "tipo": "provider",
            "provider": provider,
            "resource_type": resource_type,
            "sources": [source_ref],
        }
    else:
        access = _infer_access(file_type, str(source["url"]))
    base_id = _safe_base_id(
        str(source.get("source_id", "fonte_nova")),
        str(source.get("url") or source_ref or "fonte_nova"),
    )
    entry = {
        "base_id": base_id,
        "escopo": "experimental",
        "natureza": _infer_natureza(file_type),
        "fonte": source.get("nome", source.get("url")),
        "acesso": access,
        "selecao_deterministica": True,
        "volume_alvo": 1,
        "formato": file_type,
        "licenca": "A confirmar pela equipe",
        "sensitivity_default": "publico",
    }
    proposal = {
        "proposal_id": str(source.get("source_id") or base_id),
        "action": "add_base",
        "base_id": base_id,
        "reason": "Fonte descoberta pelo source_catalog e ausente do manifesto.",
        "suggested_manifest_entry": entry,
    }
    if source_ref:
        proposal["source_ref"] = source_ref
        proposal["artifacts"] = source.get("documents") or []
    return proposal


def build_proposal(manifest_path: Path, discovery_payload: dict[str, Any]) -> dict[str, Any]:
    """Converte descobertas em propostas sem modificar o manifesto."""
    manifest = _load_manifest(manifest_path)
    declared = _declared_urls(manifest)
    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()
    sources = discovery_payload.get("sources") or discovery_payload.get("results") or []
    for source in sources:
        url = source.get("url")
        source_ref = source.get("source_ref")
        if not _http_url(url) and not source_ref:
            continue
        identity = str(url) if url else json.dumps(source_ref, sort_keys=True, ensure_ascii=False)
        if url in declared or identity in seen:
            continue
        seen.add(identity)
        proposals.append(_proposal_entry(source))
    return {
        "proposal_type": "manifest_curation",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current_manifest_hash": manifest_hash(manifest_path),
        "manifest": str(manifest_path),
        "discovery_checked_at": discovery_payload.get("checked_at"),
        "proposals": proposals,
    }


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, nested in value.items():
            if isinstance(nested, dict) and nested:
                lines.append(f"{prefix}{key}:")
                lines.extend(_yaml_lines(nested, indent + 2))
            elif isinstance(nested, list):
                lines.append(f"{prefix}{key}: {json.dumps(nested, ensure_ascii=False)}")
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(nested)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _proposed_manifest_text(manifest_path: Path, proposal: dict[str, Any]) -> str:
    current = manifest_path.read_text(encoding="utf-8")
    for item in proposal.get("proposals", []):
        if item.get("action") == "update_base":
            current = _patch_manifest_base_text(current, item)
    additions = [
        item["suggested_manifest_entry"]
        for item in proposal.get("proposals", [])
        if item.get("action") == "add_base" and item.get("suggested_manifest_entry")
    ]
    if not additions:
        return current
    text = current.rstrip() + "\n\n  # Bases propostas pela curadoria; revisar antes de coletar.\n"
    for index, base in enumerate(additions):
        lines = _yaml_lines(base, indent=4)
        text += "  - " + lines[0].lstrip() + "\n"
        text += "\n".join(lines[1:]) + "\n"
    return text


def _render_yaml_entry(key: str, value: Any, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        rendered_key = json.dumps(key, ensure_ascii=False) if ":" in key else key
        lines = [f"{prefix}{rendered_key}:"]
        for nested_key, nested_value in value.items():
            lines.extend(_render_yaml_entry(str(nested_key), nested_value, indent + 2))
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}{key}: []"]
        lines = [f"{prefix}{key}:"]
        lines.extend(f"{' ' * (indent + 2)}- {_yaml_scalar(item)}" for item in value)
        return lines
    return [f"{prefix}{key}: {_yaml_scalar(value)}"]


def _replace_yaml_entry(
    lines: list[str],
    start: int,
    end: int,
    *,
    key: str,
    value: Any,
    indent: int,
) -> tuple[list[str], int]:
    prefix = " " * indent
    match = next(
        (index for index in range(start, end) if lines[index].startswith(prefix + key + ":")),
        None,
    )
    if match is None:
        replacement = _render_yaml_entry(key, value, indent)
        return lines[:end] + replacement + lines[end:], len(replacement)
    replace_end = match + 1
    while replace_end < end:
        line = lines[replace_end]
        if line.strip() and not line.lstrip().startswith("#"):
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent <= indent:
                break
        replace_end += 1
    replacement = _render_yaml_entry(key, value, indent)
    updated = lines[:match] + replacement + lines[replace_end:]
    return updated, len(replacement) - (replace_end - match)


def _patch_manifest_base_text(text: str, proposal_item: dict[str, Any]) -> str:
    base_id = str(proposal_item.get("base_id") or "")
    patch = proposal_item.get("patch")
    if not base_id or not isinstance(patch, dict):
        raise ValueError("proposta update_base precisa de base_id e patch")
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line == f"  - base_id: {base_id}"),
        None,
    )
    if start is None:
        raise ValueError(f"base não encontrada no manifesto: {base_id}")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("  - base_id:")),
        len(lines),
    )

    for key in ("fonte", "volume_alvo", "campo_vigencia", "metadados_extras"):
        if key in patch:
            lines, delta = _replace_yaml_entry(
                lines, start, end, key=key, value=patch[key], indent=4
            )
            end += delta

    access_patch = patch.get("acesso") or {}
    if access_patch:
        access_start = next(
            (index for index in range(start, end) if lines[index] == "    acesso:"),
            None,
        )
        if access_start is None:
            raise ValueError(f"base {base_id} não declara acesso")
        access_end = next(
            (index for index in range(access_start + 1, end) if lines[index].startswith("    ") and not lines[index].startswith("      ") and lines[index].strip()),
            end,
        )
        for key, value in access_patch.items():
            lines, delta = _replace_yaml_entry(
                lines, access_start + 1, access_end, key=str(key), value=value, indent=6
            )
            end += delta
            access_end += delta
    return "\n".join(lines) + "\n"


def build_normative_proposal(manifest_path: Path) -> dict[str, Any]:
    """Propose official legal sources and metadata for the existing normative base."""
    try:
        from .source_catalog import NORMATIVE_SOURCES
    except ImportError:
        from source_catalog import NORMATIVE_SOURCES

    manifest = _load_manifest(manifest_path)
    base = next(
        (item for item in manifest.get("bases", []) if item.get("base_id") == "editais_normativo"),
        None,
    )
    if base is None:
        raise ValueError("manifesto não declara a base editais_normativo")
    urls = [source["url"] for source in NORMATIVE_SOURCES]
    metadata = {source["url"]: source["metadata"] for source in NORMATIVE_SOURCES}
    return {
        "proposal_type": "manifest_curation",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current_manifest_hash": manifest_hash(manifest_path),
        "manifest": str(manifest_path),
        "proposals": [
            {
                "proposal_id": "normativa_fontes_oficiais",
                "action": "update_base",
                "base_id": "editais_normativo",
                "reason": "Usa os textos oficiais da Lei 14.133, LC 123 e decretos estaduais do RJ, com metadados temporais.",
                "patch": {
                    "fonte": "Lei 14.133 e LC 123 (Planalto); Decretos estaduais 48.816/2023 e 48.820/2023 (CGE-RJ); editais e termos de referência vinculados no PNCP",
                    "acesso": {
                        "urls": urls,
                        "metadados_por_url": metadata,
                        "linked_source_inventory_base": "contratos_estruturado",
                        "documentos_edital_filtro": {
                            "campos": ["titulo", "tipoDocumentoNome"],
                            "termos": ["edital", "termo de referencia"],
                        },
                        "artefatos_relacionados": {"documentos_edital": "editais_normativo"},
                    },
                    "volume_alvo": len(urls),
                    "campo_vigencia": "data_vigencia_inicio",
                    "metadados_extras": [
                        "numero_norma", "tipo_documento", "data_publicacao", "data_vigencia_inicio",
                        "data_vigencia_fim", "ambito", "fonte_oficial", "id_contratacao",
                        "numero_edital", "orgao", "objeto",
                    ],
                },
            },
            {
                "proposal_id": "normativa_filtro_documentos_pncp",
                "action": "update_base",
                "base_id": "contratos_estruturado",
                "reason": "Encaminha para a base Normativa somente anexos identificados como edital ou termo de referência.",
                "patch": {
                    "acesso": {
                        "documentos_edital_filtro": {
                            "campos": ["titulo", "tipoDocumentoNome"],
                            "termos": ["edital", "termo de referencia"],
                        }
                    }
                },
            },
        ],
    }


def manifest_diff(manifest_path: Path, proposal: dict[str, Any]) -> str:
    current = manifest_path.read_text(encoding="utf-8").splitlines(keepends=True)
    proposed = _proposed_manifest_text(manifest_path, proposal).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            current,
            proposed,
            fromfile=str(manifest_path),
            tofile=f"{manifest_path} (proposto)",
        )
    )


def apply_proposal(manifest_path: Path, proposal: dict[str, Any], *, new_version: str | None = None) -> Path:
    """Aplica propostas de adição ou atualização após validar o hash atual."""
    expected = proposal.get("current_manifest_hash")
    actual = manifest_hash(manifest_path)
    if expected != actual:
        raise ValueError(
            "manifesto mudou desde a proposta: "
            f"esperado {expected or 'ausente'}, encontrado {actual}"
        )
    if any(item.get("action") not in {"add_base", "update_base"} for item in proposal.get("proposals", [])):
        raise ValueError("apply aceita somente action=add_base ou action=update_base")

    text = _proposed_manifest_text(manifest_path, proposal)
    if new_version:
        text = re.sub(r'(?m)^(version:\s*)(["\']?)[^"\'\r\n]+\2\s*$', rf'\g<1>"{new_version}"', text, count=1)

    temporary = manifest_path.with_name(f".{manifest_path.name}.curation.tmp")
    temporary.write_text(text, encoding="utf-8")
    try:
        _load_manifest(temporary)
        temporary.replace(manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest_path


def _make_config(
    *,
    ano: int,
    uf: str,
    modalidade: int,
    tamanho_pagina: int,
    tentativas: int,
    timeout: float,
    classificacao_superior: str,
    data_inicial: Optional[str],
    data_final: Optional[str],
) -> Any:
    try:
        from .source_catalog import CheckConfig
    except ImportError:
        from source_catalog import CheckConfig

    return CheckConfig(
        ano=ano,
        uf=uf.strip().upper(),
        modalidade=modalidade,
        tamanho_pagina=tamanho_pagina,
        timeout=timeout,
        attempts=tentativas,
        classificacao_superior=classificacao_superior,
        data_inicial=data_inicial,
        data_final=data_final,
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON precisa conter um objeto: {path}")
    return payload


def _print_discovery(payload: dict[str, Any]) -> None:
    table = Table(title=f"Fontes descobertas — {payload.get('checked_at', '—')}")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Fonte")
    table.add_column("Tipo", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("URL", overflow="fold")
    for source in payload.get("sources", []):
        table.add_row(
            str(source.get("source_id", "—")),
            str(source.get("nome", "—")),
            str(source.get("file_type", "—")),
            str(source.get("status", "—")),
            str(source.get("url", "—")),
        )
    console.print(table)


def _print_proposal(proposal: dict[str, Any]) -> None:
    proposals = proposal.get("proposals", [])
    console.print(
        Panel(
            f"[bold]Manifesto:[/] {proposal.get('manifest')}\n"
            f"[bold]Hash atual:[/] {proposal.get('current_manifest_hash')}\n"
            f"[bold]Propostas:[/] {len(proposals)}",
            title="Curadoria do manifesto",
            border_style="cyan",
        )
    )
    table = Table(show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("Ação", style="yellow")
    table.add_column("Base sugerida", style="green")
    for item in proposals:
        entry = item.get("suggested_manifest_entry", {})
        table.add_row(
            str(item.get("proposal_id", "—")),
            str(item.get("action", "—")),
            str(entry.get("base_id") or item.get("base_id") or "—"),
        )
    console.print(table)


def _print_lookup(payload: dict[str, Any]) -> None:
    results = payload.get("results", [])
    console.print(
        Panel(
            f"[bold]Fonte:[/] PNCP\n"
            f"[bold]Consulta:[/] {payload.get('query', '') or '(todos os registros)'}\n"
            f"[bold]Páginas:[/] {payload.get('pages_consulted', 0)}\n"
            f"[bold]Resultados:[/] {len(results)}",
            title="Busca remota",
            border_style="cyan",
        )
    )
    table = Table(title="Contratações encontradas")
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Objeto")
    table.add_column("ID externo", style="yellow")
    table.add_column("Docs", justify="right", style="green")
    for index, result in enumerate(results, start=1):
        table.add_row(
            str(index),
            str(result.get("title", "—")),
            str(result.get("external_id") or "—"),
            str(len(result.get("documents", []))),
        )
    console.print(table)
    for index, result in enumerate(results, start=1):
        documents = result.get("documents", [])
        if not documents:
            continue
        console.print(f"[bold cyan][{index}] Documentos[/]")
        for document in documents:
            console.print(
                f"  • {document.get('name', 'documento')} "
                f"({document.get('tipo') or 'arquivo'}) — {document.get('url', '—')}"
            )
    if payload.get("errors"):
        console.print(f"[yellow]Avisos:[/] {len(payload['errors'])} consulta(s) não concluída(s)")


def _review(proposal_path: Path, output_path: Path) -> Path:
    proposal = _read_json(proposal_path)
    items = proposal.get("proposals", [])
    choices = [
        {
            "name": f"{item.get('proposal_id', '—')} — {item.get('suggested_manifest_entry', {}).get('base_id') or item.get('base_id', '—')}",
            "value": index,
        }
        for index, item in enumerate(items)
    ]
    selected = set(
        inquirer.checkbox(
            message="Selecione as propostas para aceitar",
            choices=choices,
            instruction="Use ↑↓, espaço para selecionar e Enter para confirmar",
        ).execute()
    )
    accepted = [item for index, item in enumerate(items) if index in selected]
    rejected = [item for index, item in enumerate(items) if index not in selected]
    reviewed = {
        **proposal,
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "proposals": accepted,
        "rejected": rejected,
        "skipped": [],
    }
    _write_json(reviewed, output_path)
    return output_path


@app.command("discover")
def discover_command(
    output: Optional[Path] = typer.Option(None, "--output"),
    ano: int = typer.Option(2025, "--ano"),
    uf: str = typer.Option("RJ", "--uf"),
    modalidade: int = typer.Option(1, "--modalidade"),
    tamanho_pagina: int = typer.Option(10, "--tamanho-pagina"),
    tentativas: int = typer.Option(3, "--tentativas"),
    timeout: float = typer.Option(30.0, "--timeout"),
    classificacao_superior: str = typer.Option("979", "--classificacao-superior"),
    data_inicial: Optional[str] = typer.Option(None, "--data-inicial"),
    data_final: Optional[str] = typer.Option(None, "--data-final"),
) -> None:
    payload = discover(
        _make_config(
            ano=ano,
            uf=uf,
            modalidade=modalidade,
            tamanho_pagina=tamanho_pagina,
            tentativas=tentativas,
            timeout=timeout,
            classificacao_superior=classificacao_superior,
            data_inicial=data_inicial,
            data_final=data_final,
        )
    )
    output_path = output or PROPOSALS_ROOT / f"discovery-{_timestamp()}.json"
    _write_json(payload, output_path)
    _print_discovery(payload)
    console.print(f"[green]Snapshot salvo:[/] {output_path}")


@app.command("propose")
def propose_command(
    manifest: Path = typer.Option(DEFAULT_MANIFEST, "--manifest"),
    discovery_path: Optional[Path] = typer.Option(None, "--discovery"),
    output: Optional[Path] = typer.Option(None, "--output"),
    ano: int = typer.Option(2025, "--ano"),
    uf: str = typer.Option("RJ", "--uf"),
    modalidade: int = typer.Option(1, "--modalidade"),
    tamanho_pagina: int = typer.Option(10, "--tamanho-pagina"),
    tentativas: int = typer.Option(3, "--tentativas"),
    timeout: float = typer.Option(30.0, "--timeout"),
    classificacao_superior: str = typer.Option("979", "--classificacao-superior"),
    data_inicial: Optional[str] = typer.Option(None, "--data-inicial"),
    data_final: Optional[str] = typer.Option(None, "--data-final"),
) -> None:
    discovery_payload = (
        _read_json(discovery_path)
        if discovery_path
        else discover(
            _make_config(
                ano=ano,
                uf=uf,
                modalidade=modalidade,
                tamanho_pagina=tamanho_pagina,
                tentativas=tentativas,
                timeout=timeout,
                classificacao_superior=classificacao_superior,
                data_inicial=data_inicial,
                data_final=data_final,
            )
        )
    )
    proposal = build_proposal(manifest.resolve(), discovery_payload)
    output_path = output or PROPOSALS_ROOT / f"manifest-proposal-{_timestamp()}.json"
    _write_json(proposal, output_path)
    _print_proposal(proposal)
    console.print(f"[green]Proposta salva:[/] {output_path}")


@app.command("normative-proposal")
def normative_proposal_command(
    manifest: Path = typer.Option(DEFAULT_MANIFEST, "--manifest"),
    output: Optional[Path] = typer.Option(None, "--output"),
) -> None:
    proposal = build_normative_proposal(manifest.resolve())
    output_path = output or PROPOSALS_ROOT / f"normative-proposal-{_timestamp()}.json"
    _write_json(proposal, output_path)
    _print_proposal(proposal)
    console.print(f"[green]Proposta salva:[/] {output_path}")


@app.command("lookup")
def lookup_command(
    provider: str = typer.Argument("pncp", help="fonte remota suportada"),
    query: str = typer.Option("", "--query", help="texto/termos para filtrar o registro localmente"),
    year: int = typer.Option(2025, "--year", "--ano"),
    uf: str = typer.Option("RJ", "--uf"),
    modalidade: Optional[int] = typer.Option(1, "--modalidade"),
    page_size: int = typer.Option(50, "--page-size"),
    max_pages: int = typer.Option(5, "--max-pages"),
    max_results: int = typer.Option(20, "--max-results"),
    data_inicial: Optional[str] = typer.Option(None, "--data-inicial"),
    data_final: Optional[str] = typer.Option(None, "--data-final"),
    tentativas: int = typer.Option(3, "--tentativas"),
    timeout: float = typer.Option(30.0, "--timeout"),
    rate_limit_seg: float = typer.Option(1.0, "--rate-limit-seg"),
    output: Optional[Path] = typer.Option(None, "--output"),
) -> None:
    if provider.casefold() != "pncp":
        raise typer.BadParameter("por enquanto, a única fonte remota suportada é pncp", param_hint="provider")
    try:
        from .source_lookup import lookup_pncp
    except ImportError:
        from source_lookup import lookup_pncp

    payload = lookup_pncp(
        query=query,
        year=year,
        uf=uf,
        modality=modalidade,
        page_size=page_size,
        max_pages=max_pages,
        max_results=max_results,
        data_initial=data_inicial,
        data_final=data_final,
        attempts=tentativas,
        timeout=timeout,
        rate_limit_seconds=rate_limit_seg,
    )
    _print_lookup(payload)
    if output:
        _write_json(payload, output)
        console.print(f"[green]Resultado salvo:[/] {output}")
    if payload.get("errors") and not payload.get("results"):
        raise typer.Exit(code=2)


@app.command("manifest-diff")
def manifest_diff_command(
    proposal: Path = typer.Option(..., "--proposal"),
    manifest: Path = typer.Option(DEFAULT_MANIFEST, "--manifest"),
) -> None:
    proposal_payload = _read_json(proposal)
    console.print(manifest_diff(manifest.resolve(), proposal_payload), markup=False, end="")


@app.command("review")
def review_command(
    proposal: Path = typer.Option(..., "--proposal"),
    output: Optional[Path] = typer.Option(None, "--output"),
) -> None:
    output_path = output or proposal.with_name(f"{proposal.stem}-reviewed.json")
    path = _review(proposal, output_path)
    console.print(f"[green]Revisão salva:[/] {path}")


@app.command("apply")
def apply_command(
    proposal: Path = typer.Option(..., "--proposal"),
    manifest: Path = typer.Option(DEFAULT_MANIFEST, "--manifest"),
    new_version: Optional[str] = typer.Option(None, "--new-version"),
    yes: bool = typer.Option(False, "--yes", help="confirma sem prompt interativo"),
) -> None:
    proposal_payload = _read_json(proposal)
    _print_proposal(proposal_payload)
    confirmed = yes or inquirer.confirm(
        message="Aplicar estas propostas ao manifesto?",
        default=False,
    ).execute()
    if not confirmed:
        console.print("[yellow]Aplicação cancelada.[/]")
        raise typer.Exit(code=1)
    path = apply_proposal(manifest.resolve(), proposal_payload, new_version=new_version)
    console.print(f"[green]Manifesto atualizado:[/] {path}")
    console.print(f"[green]Novo hash:[/] {manifest_hash(path)}")


def main(argv: list[str] | None = None) -> int:
    try:
        result = app(args=argv, prog_name="source_catalog", standalone_mode=False)
        return int(result or 0)
    except typer.Exit as exc:
        return int(exc.exit_code or 0)
    except SystemExit as exc:
        return int(exc.code or 0)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]erro:[/] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
