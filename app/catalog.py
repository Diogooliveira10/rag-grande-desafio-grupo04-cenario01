"""Catálogo HTTP somente leitura, derivado do inventário e dos chunks ativos.

Não coleta dados, não chama modelos e não expõe texto bruto, caminhos locais,
credenciais ou registros restritos. O cache invalida quando os arquivos mudam.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

import config
from src.fetch_corpus import load_manifest
from src.sensitivity import mascarar

DOCUMENTS_FILE = config.NORMALIZED_DATA_DIR / "documents.jsonl"
INVENTORY_DIR = config.ROOT_DIR / "data/inventory"
MANIFEST_FILE = config.ROOT_DIR / "corpus_manifest.yaml"
STRUCTURED_FILE = config.ROOT_DIR / "data/structured/base_estruturada.sqlite"
INDEX_MANIFEST = config.INDEX_DIR / "manifest.json"
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


# O catálogo guarda só metadados por arquivo e por contrato (um arquivo de
# origem pode conter vários contratos). Nunca retém textos/vetores.
# O lock evita leituras simultâneas do JSONL no primeiro acesso (lru_cache sozinho
# pode executar a mesma carga mais de uma vez em threads concorrentes).
_SNAPSHOT_LOCK = RLock()
_PAYLOAD_FIELDS = ("numeroControlePNCP", "numeroContratoEmpenho", "orgaoEntidade",
                   "unidadeOrgao", "nomeRazaoSocialFornecedor", "objetoContrato",
                   "objetoCompra", "objetoAta", "valorGlobal", "dataHomologacao",
                   "modalidadeNome", "situacaoCompraNome", "anoContrato", "anoPca")
_EXTRA_FIELDS = ("orgao", "orgao_cnpj", "fornecedor", "objeto", "modalidade", "esfera",
                 "municipio", "titulo_documento", "title", "titulo", "creation_date",
                 "data_publicacao")


@lru_cache(maxsize=1)
def _load_metadata(signatures: tuple[tuple[str, int, int], ...]) -> tuple[dict, dict]:
    manifest = load_manifest(Path(signatures[0][0]))
    # Não carregar todos os inventários históricos (dezenas de MB) para usar só um.
    for signature in reversed(signatures[1:]):
        inventory = _read_json(Path(signature[0]))
        if inventory.get("official") is True and inventory.get("completo") is True:
            return manifest, inventory
    raise ValueError("inventário oficial completo indisponível")


def _metadata_signatures() -> tuple[tuple[str, int, int], ...]:
    inventories = sorted((path for path in INVENTORY_DIR.glob("v*.json")
                          if re.fullmatch(r"v\d+", path.stem)), key=lambda path: int(path.stem[1:]))
    return tuple(_signature(path) for path in [MANIFEST_FILE, *inventories])


def _active_metadata(source_hash: str) -> dict:
    """Identidade só é confiável quando vinculada ao hash do JSONL ativo.

    Consulta apenas meta, em mode=ro: nunca valida/reconstrói o banco ou índice.
    Contagens de chunks/arquivos são observadas no JSONL, não inferidas da v6.
    """
    active: dict[str, Any] = {"versao": None, "snapshot_id": None, "construido_em": None,
                              "total_vetores": None, "chunks_sem_vetor": None,
                              "bytes_documents": None, "schema_sql": None,
                              "vetores_por_base": {}, "avisos": []}
    if STRUCTURED_FILE.is_file():
        try:
            with closing(sqlite3.connect(STRUCTURED_FILE.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                meta = dict(conn.execute("SELECT chave, valor FROM meta"))
            if meta.get("source_sha256") == source_hash:
                active["versao"] = meta.get("corpus_version")
                active["construido_em"] = meta.get("built_at")
                active["schema_sql"] = meta.get("schema_version")
            else:
                active["avisos"].append("A proveniência do banco estruturado não corresponde ao JSONL ativo.")
        except (OSError, sqlite3.Error):
            active["avisos"].append("Não foi possível verificar a versão do banco estruturado.")
    if INDEX_MANIFEST.is_file():
        try:
            index = _read_json(INDEX_MANIFEST)
            if index.get("source_sha256") == source_hash:
                active["snapshot_id"] = index.get("snapshot_id")
                if isinstance(index.get("built_at"), str):
                    active["construido_em"] = index["built_at"]
                chunks, vectors = index.get("total_chunks"), index.get("total_vectors")
                if type(vectors) is int and vectors >= 0:
                    active["total_vetores"] = vectors
                if type(chunks) is int and type(vectors) is int and 0 <= vectors <= chunks:
                    active["chunks_sem_vetor"] = chunks - vectors
                por_base = index.get("por_base")
                if not isinstance(por_base, dict):
                    provenance = index.get("proveniencia_vetores")
                    por_base = provenance.get("por_base") if isinstance(provenance, dict) else {}
                if isinstance(por_base, dict):
                    active["vetores_por_base"] = {
                        base: sum(value for value in counts.values() if type(value) is int)
                        for base, counts in por_base.items() if isinstance(counts, dict)
                    }
                if index.get("homogeneidade_modelo_comprovada") is False:
                    active["avisos"].append("O índice possui linhagem de modelos de embedding mista; homogeneidade não comprovada.")
                if index.get("proveniencia_encoding_completa") is False:
                    active["avisos"].append("A proveniência de encoding dos vetores não está completamente comprovada.")
                if index.get("promocao_autorizada") is False:
                    active["avisos"].append("O manifesto do índice não declara autorização de promoção para produção.")
            else:
                active["avisos"].append("A proveniência do índice não corresponde ao JSONL ativo.")
        except (OSError, ValueError):
            active["avisos"].append("Não foi possível verificar os metadados do índice ativo.")
    if not active["versao"]:
        active["avisos"].append("A versão do corpus ativo não pôde ser verificada; o inventário de coleta é informado separadamente.")
    try:
        active["bytes_documents"] = DOCUMENTS_FILE.stat().st_size
    except OSError:
        pass
    return active


@lru_cache(maxsize=1)
def _load_snapshot(signatures: tuple[tuple[str, int, int], ...],
                   runtime_signatures: tuple[tuple[str, int, int], ...] = ()) -> tuple[dict, dict, list[dict]]:
    manifest, inventory = _load_metadata((signatures[0], *signatures[2:]))
    bases = _official_bases(manifest)
    compact: dict[tuple[str, str, str], dict] = {}
    chunks_by_base: Counter = Counter()
    source_files: set[tuple[str, str]] = set()
    source_hash = hashlib.sha256()
    with Path(signatures[1][0]).open("rb") as handle:
        for line in handle:
            source_hash.update(line)
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("documentos ativos inválidos")
            base_id = row.get("base_id")
            if base_id not in bases:
                continue
            chunks_by_base[base_id] += 1
            source_files.add((base_id, str(row.get("source_file"))))
            payload = _payload(row) if base_id == "contratos_estruturado" and _public(row) else {}
            identifier = str(payload.get("numeroControlePNCP") or row.get("logical_id") or row.get("chunk_id"))
            key = (base_id, str(row.get("source_file")),
                   identifier if base_id == "contratos_estruturado" and _public(row) else "")
            previous = compact.get(key)
            # Preserva o primeiro chunk público por documento/contrato, sem
            # confundir arquivo de coleta (pode ser um lote) com um contrato.
            # Restritos contam no inventário, não na UI.
            if previous is not None and (_public(previous) or not _public(row)):
                continue
            if not payload and _public(row):
                payload = _payload(row)
            extras = row.get("extras") or {}
            compact[key] = {
                **{field: row.get(field) for field in (
                    "base_id", "source_file", "sha256", "chunk_id", "logical_id",
                    "sensitivity", "origem", "formato", "doc_type")},
                "extras": {field: extras[field] for field in _EXTRA_FIELDS if field in extras},
                "_catalog_payload": {field: payload[field] for field in _PAYLOAD_FIELDS if field in payload},
            }
    active = _active_metadata(source_hash.hexdigest())
    active.update(total_chunks=sum(chunks_by_base.values()), total_documentos=len(source_files))
    if active["total_vetores"] is not None and active["total_vetores"] <= active["total_chunks"]:
        active["chunks_sem_vetor"] = active["total_chunks"] - active["total_vetores"]
    if active["chunks_sem_vetor"]:
        active["avisos"].append("Há trechos sem vetor; a cobertura da busca vetorial é parcial.")
    return manifest, {**inventory, "_corpus_ativo": active,
                      "_chunks_por_base": dict(chunks_by_base),
                      "_arquivos_por_base": dict(Counter(base for base, _ in source_files))}, list(compact.values())


def _snapshot() -> tuple[dict, dict, list[dict]]:
    metadata = _metadata_signatures()
    signatures = (metadata[0], _signature(DOCUMENTS_FILE), *metadata[1:])
    runtime = tuple(_signature(path) for path in (STRUCTURED_FILE, INDEX_MANIFEST) if path.is_file())
    with _SNAPSHOT_LOCK:
        return _load_snapshot(signatures, runtime)


def prewarm() -> None:
    """Pré-carrega o snapshot no boot; erro deixa para a primeira requisição reportar."""
    try:
        _snapshot()
    except Exception:  # noqa: BLE001 - aquecimento nunca derruba o servidor
        _load_snapshot.cache_clear()
        _load_metadata.cache_clear()


def _official_bases(manifest: dict) -> dict[str, dict]:
    return {base["base_id"]: base for base in manifest["bases"] if base.get("escopo") == "oficial"}


def _public(row: dict) -> bool:
    # Falta de classificação não significa publicidade.
    return row.get("sensitivity") == "publico"


def _payload(row: dict) -> dict:
    if "_catalog_payload" in row:
        return row["_catalog_payload"]
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
    if not query or not query.strip():
        return True
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


def _contracts() -> dict[str, dict]:
    manifest, _, rows = _snapshot()
    if "contratos_estruturado" not in _official_bases(manifest):
        return {}
    result: dict[str, dict] = {}
    for row in rows:
        if row.get("base_id") != "contratos_estruturado" or not _public(row):
            continue
        payload, extras = _payload(row), row.get("extras") or {}
        identifier = str(payload.get("numeroControlePNCP") or row.get("logical_id") or row["chunk_id"])
        if identifier in result:
            continue
        entity = payload.get("orgaoEntidade") or {}
        unit = payload.get("unidadeOrgao") or {}
        result[identifier] = {
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
        }
    return result


def contracts(busca: str | None = None, orgao: str | None = None,
              limit: int = 24, offset: int = 0) -> list[dict]:
    filtered = [record for _, record in sorted(_contracts().items())
                if _matches(record, busca, ("id", "numero_contrato", "orgao", "fornecedor", "objeto", "municipio"))
                and _matches(record, orgao, ("orgao", "orgao_cnpj"))]
    # Mascarar apenas a página publicada, não milhares de registros descartados.
    return _safe(filtered[offset:offset + limit])


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
        result[identifier] = {
            "id": identifier, "titulo": str(title), "tipo": DOCUMENT_TYPES.get(base_id, base.get("natureza")),
            "orgao": extras.get("orgao"),
            "ano": _year(extras.get("creation_date"), extras.get("data_publicacao"), payload.get("anoContrato"), payload.get("anoPca")),
            "fonte": base.get("fonte"),
            "descricao": "Conversa fictícia para testes; não comprova fatos reais do PNCP." if synthetic else extras.get("objeto"),
            "link": None if synthetic else _http_url(record.get("origem") or public.get("origem")),
            "base_id": base_id, "natureza": base["natureza"],
            "formato": record.get("tipo") or public.get("formato"),
        }

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
    return _safe(filtered[offset:offset + limit])


def organizations(busca: str | None = None, limit: int = 24, offset: int = 0) -> list[dict]:
    grouped: dict[str, dict] = {}
    # A lista de órgãos representa apenas registros reais de contratações.
    for contract in _contracts().values():
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
    return _safe(result[offset:offset + limit])


def sources() -> list[dict]:
    # Fontes dependem só do manifesto/inventário, não dos 629 MB de documentos.
    with _SNAPSHOT_LOCK:
        manifest, _ = _load_metadata(_metadata_signatures())
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
                          "status": status, "documentos": documents_count, "formatos": formats,
                          "documentos_ativos": inventory["_arquivos_por_base"].get(base_id, 0),
                          "chunks_ativos": inventory["_chunks_por_base"].get(base_id, 0)})
    totals = inventory.get("totais") or {}
    has_supplement = any(base.get("dados_sinteticos") for base in bases.values())
    inventory_version = str(inventory["version"]) + (" + suplemento sintético" if has_supplement else "")
    active = inventory["_corpus_ativo"]
    for item in summaries:
        item["vetores_ativos"] = active["vetores_por_base"].get(item["base_id"])
    backups = sorted(path.name for path in (config.ROOT_DIR / "data").glob("backup_v*") if path.is_dir())
    snapshot_anterior = {"versao": inventory_version, "coletado_em": inventory.get("coletado_em"),
                         "arquivos": int(totals.get("arquivos", 0)),
                         "backup": f"data/{backups[-1]}" if backups else None}
    return {"projeto": "NEXO RJ — Adaptive RAG", "scenario_id": manifest["scenario_id"],
        "descricao": (manifest.get("description") or "") + ("; inclui suplemento de conversas sintéticas, não fatos reais." if has_supplement else ""),
        "versao_manifesto": str(manifest.get("version")) if manifest.get("version") else None,
        "versao_corpus": active["versao"] or inventory_version,
        "versao_inventario": inventory_version, "corpus_ativo": _safe(active),
        "snapshot_anterior": _safe(snapshot_anterior),
        "coletado_em": inventory.get("coletado_em"),
        "inventario_completo": inventory.get("completo") is True and all(base["status"] == "complete" for base in summaries),
        "total_documentos": sum(base["documentos"] for base in summaries),
        "total_itens": int(totals.get("itens", 0)) + supplemental_items,
        "total_falhas": int(totals.get("falhas", 0)), "bases": summaries}


# Nomes compatíveis com a interface da API introduzida na main.
listar_contratacoes = contracts
listar_documentos = documents
listar_orgaos = organizations
listar_fontes = sources
project_summary = summary
