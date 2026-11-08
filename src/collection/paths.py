"""Onde a coleta lê e grava: a árvore de dados (``CAMINHOS``), a raiz do projeto e as versões.

``CAMINHOS`` e ``PROJECT_ROOT`` moram só aqui: quem precisa importa este módulo como ``coleta`` e lê
``coleta.CAMINHOS`` na hora, e uma
execução troca a árvore com ``_configure_data_root``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


from src.collection.sources import _version_number


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _effective_objects_root() -> Path:
    try:
        CAMINHOS.objects_root.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return PROJECT_ROOT / "data" / "raw" / ".objects" / "sha256"
    return CAMINHOS.objects_root


def _object_path(digest: str) -> Path:
    return _effective_objects_root() / digest[:2] / digest
