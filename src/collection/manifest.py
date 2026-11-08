"""O manifesto do corpus: leitura, recorte pelo escopo (oficial ou experimental) e hash.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


from src.collection.manifest_yaml import _parse_manifest_yaml


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
