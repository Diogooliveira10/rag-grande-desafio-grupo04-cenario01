"""Pesquisa local nos metadados de uma versão já materializada do corpus."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


PROJECT_ROOT = Path(__file__).resolve().parents[1]
app = typer.Typer(
    name="corpus_search",
    help="Pesquisa metadados do corpus sem baixar ou modificar arquivos.",
    no_args_is_help=True,
)
search_app = typer.Typer(help="Tipos de busca local disponíveis.", no_args_is_help=True)
app.add_typer(search_app, name="search")
console = Console()


def _version_number(value: str) -> int:
    match = re.fullmatch(r"v(\d+)", value.strip())
    if not match:
        raise ValueError("a versão deve usar o formato vN, por exemplo v1")
    return int(match.group(1))


def load_inventory(data_root: Path, corpus_version: str) -> dict[str, Any]:
    version = _version_number(corpus_version)
    path = data_root / "inventory" / f"v{version}.json"
    if not path.exists():
        raise FileNotFoundError(f"inventário não encontrado: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"inventário inválido: {path}")
    return payload


def _match_fields(record: dict[str, Any], query: str) -> list[str]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return []
    fields = {
        key: value
        for key, value in record.items()
        if isinstance(value, (str, int, float, bool))
    }
    return [
        key
        for key, value in fields.items()
        if all(term in str(value).casefold() for term in terms)
    ]


def search_inventory(
    *,
    inventory: dict[str, Any],
    query: str,
    base_id: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Busca somente nos metadados dos registros do inventário."""
    terms = [term.casefold() for term in query.split() if term.strip()]
    limit = max(1, int(limit))
    matches: list[dict[str, Any]] = []
    for record in inventory.get("arquivos", []):
        if not isinstance(record, dict) or record.get("status") not in {None, "ok"}:
            continue
        if base_id and record.get("base_id") != base_id:
            continue
        searchable = json.dumps(record, ensure_ascii=False, default=str).casefold()
        if terms and not all(term in searchable for term in terms):
            continue
        matches.append(
            {
                "logical_id": record.get("logical_id"),
                "base_id": record.get("base_id"),
                "tipo": record.get("tipo"),
                "path": record.get("path"),
                "origem": record.get("origem"),
                "sha256": record.get("sha256"),
                "match_fields": _match_fields(record, query),
            }
        )
        if len(matches) >= limit:
            break
    return {
        "search_type": "local_inventory_metadata",
        "version": inventory.get("version"),
        "query": query,
        "base_id": base_id,
        "total_matches": len(matches),
        "matches": matches,
    }


def _render(payload: dict[str, Any]) -> None:
    console.print(
        Panel(
            f"[bold]Versão:[/] {payload.get('version', '—')}\n"
            f"[bold]Consulta:[/] {payload.get('query') or '(todos os itens)'}\n"
            f"[bold]Resultados:[/] {payload.get('total_matches', 0)}",
            title="Busca local no inventário",
            border_style="cyan",
        )
    )
    table = Table(title="Itens encontrados")
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Base", style="green")
    table.add_column("Tipo", style="magenta")
    table.add_column("Logical ID")
    table.add_column("Path", overflow="fold")
    for index, match in enumerate(payload.get("matches", []), start=1):
        table.add_row(
            str(index),
            str(match.get("base_id", "—")),
            str(match.get("tipo", "—")),
            str(match.get("logical_id", "—")),
            str(match.get("path", "—")),
        )
    console.print(table)


@search_app.command("local")
def local_command(
    query: str = typer.Option("", "--query", help="termos para pesquisar nos metadados"),
    corpus_version: str = typer.Option(..., "--corpus-version"),
    base_id: Optional[str] = typer.Option(None, "--base-id"),
    limit: int = typer.Option(20, "--limit"),
    data_root: Path = typer.Option(PROJECT_ROOT / "data", "--data-root"),
    output: Optional[Path] = typer.Option(None, "--output"),
) -> None:
    try:
        inventory = load_inventory(data_root.resolve(), corpus_version)
        payload = search_inventory(
            inventory=inventory,
            query=query,
            base_id=base_id,
            limit=limit,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]erro:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=2)
    _render(payload)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        console.print(f"[green]Resultado salvo:[/] {output}")


def main(argv: list[str] | None = None) -> int:
    try:
        result = app(args=argv, prog_name="corpus_search", standalone_mode=False)
        return int(result or 0)
    except typer.Exit as exc:
        return int(exc.exit_code or 0)
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
