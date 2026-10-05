"""Catálogo HTTP somente leitura, derivado do inventário e dos chunks ativos.

Não coleta dados, não chama modelos e não expõe texto bruto, caminhos locais,
credenciais ou registros restritos. O cache invalida quando os arquivos mudam.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import config
from src.fetch_corpus import load_manifest
from src.sensitivity import mascarar

DOCUMENTS_FILE = config.NORMALIZED_DATA_DIR / "documents.jsonl"
INVENTORY_DIR = config.ROOT_DIR / "data/inventory"
MANIFEST_FILE = config.ROOT_DIR / "corpus_manifest.yaml"
DOCUMENT_TYPES = {"editais_normativo": "Normativo", "contratos_estruturado": "Contrato",
                  "atas_conversacional": "Ata de Registro", "pca_agregado": "PCA",
                  "emails_sinteticos": "Conversa sintética"}


def _signature(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("catálogo inválido")
    return payload


@lru_cache(maxsize=2)
def _load_snapshot(signatures: tuple[tuple[str, int, int], ...]) -> tuple[dict, dict, list[dict]]:
    manifest = load_manifest(Path(signatures[0][0]))
    inventories = [_read_json(Path(signature[0])) for signature in signatures[2:]]
    inventory = next((item for item in reversed(inventories)
                      if item.get("official") is True and item.get("completo") is True), None)
    if inventory is None:
        raise ValueError("inventário oficial completo indisponível")
    with Path(signatures[1][0]).open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("documentos ativos inválidos")
    return manifest, inventory, rows


def _snapshot() -> tuple[dict, dict, list[dict]]:
    inventories = sorted((path for path in INVENTORY_DIR.glob("v*.json")
                          if re.fullmatch(r"v\d+", path.stem)), key=lambda path: int(path.stem[1:]))
    return _load_snapshot(tuple(_signature(path) for path in [MANIFEST_FILE, DOCUMENTS_FILE, *inventories]))


def _official_bases(manifest: dict) -> dict[str, dict]:
    return {base["base_id"]: base for base in manifest["bases"] if base.get("escopo") == "oficial"}


def _public(row: dict) -> bool:
    # Falta de classificação não significa publicidade.
    return row.get("sensitivity") == "publico"


def _payload(row: dict) -> dict:
    try:
        value = json.loads(row.get("text", ""))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _safe(value: Any) -> Any:
    if isinstance(value, str):
        return mascarar(value)[0]
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def _fold(value: Any) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(value).casefold())
                   if not unicodedata.combining(c))


def _matches(row: dict, query: str | None, fields: tuple[str, ...]) -> bool:
    searchable = _fold(" ".join(str(row.get(field) or "") for field in fields))
    return all(term in searchable for term in _fold(query or "").split())


def _http_url(value: Any) -> str | None:
    return str(value) if str(value or "").startswith(("https://", "http://")) else None


def _number(value: Any) -> float | None:
    if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _year(*values: Any) -> int | None:
    for value in values:
        match = re.match(r"(\d{4})(?:-|$)", str(value or ""))
        if match:
            return int(match[1])
    return None


def _id(base: str, value: str) -> str:
    return f"{base}:{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def contracts(busca: str | None = None, orgao: str | None = None,
              limit: int = 24, offset: int = 0) -> list[dict]:
    manifest, _, rows = _snapshot()
    if "contratos_estruturado" not in _official_bases(manifest):
        return []
    result: dict[str, dict] = {}
    for row in rows:
        if row.get("base_id") != "contratos_estruturado" or not _public(row):
            continue
        payload, extras = _payload(row), row.get("extras") or {}
        identifier = str(payload.get("numeroControlePNCP") or row.get("logical_id") or row["chunk_id"])
        entity = payload.get("orgaoEntidade") or {}
        unit = payload.get("unidadeOrgao") or {}
        result.setdefault(identifier, _safe({
            "id": identifier, "numero_contrato": str(payload.get("numeroContratoEmpenho") or identifier),
            "orgao": extras.get("orgao") or entity.get("razaoSocial"),
            "orgao_cnpj": extras.get("orgao_cnpj") or entity.get("cnpj"),
            "fornecedor": extras.get("fornecedor") or payload.get("nomeRazaoSocialFornecedor"),
            "objeto": extras.get("objeto") or payload.get("objetoContrato") or payload.get("objetoCompra"),
            "valor_global": _number(payload.get("valorGlobal")),
            "data_homologacao": payload.get("dataHomologacao"),
            "modalidade": extras.get("modalidade") or payload.get("modalidadeNome"),
            "fonte_oficial": "PNCP", "status": payload.get("situacaoCompraNome"),
            "esfera": extras.get("esfera"), "municipio": extras.get("municipio") or unit.get("municipioNome"),
        }))
    filtered = [record for _, record in sorted(result.items())
                if _matches(record, busca, ("id", "numero_contrato", "orgao", "fornecedor", "objeto", "municipio"))
                and _matches(record, orgao, ("orgao", "orgao_cnpj"))]
    return filtered[offset:offset + limit]


def documents(busca: str | None = None, tipo: str | None = None,
              limit: int = 24, offset: int = 0) -> list[dict]:
    manifest, inventory, rows = _snapshot()
    bases = _official_bases(manifest)
    # Uma entrada por arquivo de origem, não por página/chunk.
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if row.get("base_id") in bases:
            grouped.setdefault((row["base_id"], str(row.get("sha256") or row.get("source_file"))), []).append(row)
    inventory_keys: set[tuple[str, str]] = set()
    result: dict[str, dict] = {}

    def add(base_id: str, key: str, record: dict, chunks: list[dict]) -> None:
        if record.get("sensitivity") not in (None, "publico"):
            return
        if chunks and not any(_public(chunk) for chunk in chunks):
            return
        public = next((chunk for chunk in chunks if _public(chunk)), {})
        base = bases[base_id]
        if base.get("dados_sinteticos") and not public:
            return
        extras = {**(record.get("document_metadata") or {}), **(public.get("extras") or {})}
        payload = _payload(public)
        synthetic = base.get("dados_sinteticos") is True
        identifier = _id(base_id, key)
        title = (extras.get("titulo_documento") or extras.get("title") or extras.get("titulo")
                 or payload.get("objetoContrato") or payload.get("objetoCompra") or payload.get("objetoAta")
                 or extras.get("objeto") or record.get("logical_id") or identifier)
        if synthetic:
            title = f"[Sintético] {title}"
        result[identifier] = _safe({
            "id": identifier, "titulo": str(title), "tipo": DOCUMENT_TYPES.get(base_id, base.get("natureza")),
            "orgao": extras.get("orgao"),
            "ano": _year(extras.get("creation_date"), extras.get("data_publicacao"), payload.get("anoContrato"), payload.get("anoPca")),
            "fonte": base.get("fonte"),
            "descricao": "Conversa fictícia para testes; não comprova fatos reais do PNCP." if synthetic else extras.get("objeto"),
            "link": None if synthetic else _http_url(record.get("origem") or public.get("origem")),
            "base_id": base_id, "natureza": base["natureza"],
            "formato": record.get("tipo") or public.get("formato"),
        })

    for record in inventory.get("arquivos", []):
        base_id = record.get("base_id")
        if base_id not in bases or record.get("status") != "ok":
            continue
        key = str(record.get("sha256") or record.get("path"))
        inventory_keys.add((base_id, key))
        add(base_id, key, record, grouped.get((base_id, key), []))
    # Suplementos ativos têm coleta própria, fora do inventário histórico v6.
    for (base_id, key), chunks in grouped.items():
        if (base_id, key) not in inventory_keys and bases[base_id].get("dados_sinteticos"):
            add(base_id, key, {}, chunks)
    filtered = [record for _, record in sorted(result.items())
                if (not tipo or record["tipo"] == tipo)
                and _matches(record, busca, ("titulo", "orgao", "descricao", "base_id"))]
    return filtered[offset:offset + limit]


def organizations(busca: str | None = None, limit: int = 24, offset: int = 0) -> list[dict]:
    grouped: dict[str, dict] = {}
    # A lista de órgãos representa apenas registros reais de contratações.
    for contract in contracts(limit=10**9):
        name, cnpj = contract["orgao"], contract["orgao_cnpj"]
        if not name:
            continue
        key = cnpj or name
        record = grouped.setdefault(key, {"cnpj": cnpj, "sigla": None, "nome": name,
            "esfera": contract["esfera"], "municipio": contract["municipio"],
            "total_contratacoes": 0, "valor_total": None, "principais_categorias": [],
            "principais_fornecedores": [], "_values": [], "_suppliers": Counter()})
        record["total_contratacoes"] += 1
        record["_values"].append(contract["valor_global"])
        if contract["fornecedor"]:
            record["_suppliers"][contract["fornecedor"]] += 1
    result = []
    for _, record in sorted(grouped.items()):
        values = record.pop("_values")
        record["valor_total"] = sum(values) if all(value is not None for value in values) else None
        record["principais_fornecedores"] = [name for name, _ in record.pop("_suppliers").most_common(5)]
        if _matches(record, busca, ("nome", "cnpj", "municipio")):
            result.append(record)
    return result[offset:offset + limit]


def sources() -> list[dict]:
    manifest, _, _ = _snapshot()
    result = []
    for base_id, base in _official_bases(manifest).items():
        access = base.get("acesso") or {}
        url = access.get("url") or access.get("endpoint") or next(iter(access.get("urls") or []), None)
        if not url:
            url = (access.get("endpoints") or {}).get("principal")
        result.append(_safe({"id": base_id, "nome": base.get("fonte") or base_id,
            "sigla": None, "instituicao": None, "tipo_informacao": base.get("natureza"),
            "url": None if base.get("dados_sinteticos") else _http_url(url),
            "natureza": base["natureza"], "frequencia_coleta": None,
            "descricao": "Suplemento sintético: relatos fictícios, não fonte oficial de fatos." if base.get("dados_sinteticos") else base.get("fonte"),
            "escopo": base.get("escopo"), "formatos": base.get("formatos") or ([base["formato"]] if base.get("formato") else [])}))
    return result


def summary() -> dict:
    manifest, inventory, rows = _snapshot()
    bases = _official_bases(manifest)
    counts = {record["base_id"]: record for record in inventory.get("por_base", [])}
    summaries = []
    supplemental_items = 0
    for base_id, base in bases.items():
        observed = counts.get(base_id)
        if observed is not None:
            documents_count = int(observed.get("arquivos", 0))
            formats, status = observed.get("formatos", {}), observed.get("status")
        else:
            physical = {str(row.get("source_file")): row for row in rows if row.get("base_id") == base_id}
            documents_count = len(physical)
            formats = dict(Counter(row.get("formato") or row.get("doc_type") for row in physical.values()))
            status = "complete" if documents_count == base.get("volume_alvo") else "partial"
            supplemental_items += documents_count
        summaries.append({"base_id": base_id, "natureza": base["natureza"], "escopo": base["escopo"],
                          "status": status, "documentos": documents_count, "formatos": formats})
    totals = inventory.get("totais") or {}
    has_supplement = any(base.get("dados_sinteticos") for base in bases.values())
    return {"projeto": "NEXO RJ — Adaptive RAG", "scenario_id": manifest["scenario_id"],
        "descricao": (manifest.get("description") or "") + ("; inclui suplemento de conversas sintéticas, não fatos reais." if has_supplement else ""),
        "versao_manifesto": str(manifest.get("version")) if manifest.get("version") else None,
        "versao_corpus": str(inventory["version"]) + (" + suplemento sintético" if has_supplement else ""),
        "coletado_em": inventory.get("coletado_em"),
        "inventario_completo": inventory.get("completo") is True and all(base["status"] == "complete" for base in summaries),
        "total_documentos": sum(base["documentos"] for base in summaries),
        "total_itens": int(totals.get("itens", 0)) + supplemental_items,
        "total_falhas": int(totals.get("falhas", 0)), "bases": summaries}
