"""Prepara documentos para o estágio de embeddings.

O módulo mantém a coleta separada da ingestão: lê uma base já materializada
ou uma fixture local, extrai texto por formato, preserva a proveniência e
grava chunks JSONL com um contrato canônico de metadados em nível único.

Exemplo rápido com uma fixture local, sem acessar as bases remotas:

    py -m src.prepare_documents `
      --input tests/fixtures/pdf `
      --manifest corpus_manifest.yaml `
      --base-id exemplos_teste `
      --output data/normalized/exemplos_teste.jsonl

Para preparar uma base coletada, use ``--latest --base-id <base-id>``. A CLI
seleciona o inventário completo mais recente e grava em
``data/normalized/vN/<base-id>/``. Para reproduzir uma versão histórica,
informe manualmente os caminhos de entrada, inventário e saída da mesma versão.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator

from src import ocr
from src.obs import Stage
from src.offline_observation import observar_offline
from src.fetch_corpus import load_manifest
from src.ingest import chunk_text, sha256_file
from src.pca_indicators import eh_csv_pca, indicadores_pca
from src.corpus_quality import decodificar_codigos_unicode, motivo_de_texto_ilegivel
from src.pncp_result_indicators import indicadores_resultados_homologados


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "raw" / "v1"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "normalized" / "documents.jsonl"
DEFAULT_REPORT = PROJECT_ROOT / "data" / "normalized" / "extraction_report.json"
TEXT_SUFFIXES = {".html", ".htm", ".aspx", ".txt", ".md", ".csv"}
STRUCTURED_SUFFIXES = {".json", ".jsonl"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | STRUCTURED_SUFFIXES | {".pdf"}
_CANONICAL_UNIT_FIELDS = {
    "source_file",
    "arquivo_bruto",
    "nome_arquivo",
    "bytes",
    "coletado_em",
    "manifest_fonte",
    "manifest_hash",
    "origem",
    "acesso_tipo",
    "sha256",
    "logical_id",
    "data_vigencia",
}
EXTRACTION_SCHEMA_VERSION = "prepare-extraction-v1"
CHUNKING_SCHEMA_VERSION = "prepare-chunking-v1"


class _VisibleTextParser(HTMLParser):
    """Extrai texto visível de HTML sem introduzir outra dependência."""

    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned:
                self.parts.append(cleaned)

    def text(self) -> str:
        return "\n".join(self.parts)


@dataclass(frozen=True)
class SourceUnit:
    """Unidade de texto antes do chunking: arquivo, página ou registro."""

    source_file: str
    logical_id: str
    document_id: str
    base_id: str
    natureza: str
    doc_type: str
    sensitivity: str
    origem: str
    manifest_hash: str
    bytes: int
    data_vigencia: str | None
    chunking_sugerido: str
    formato: str
    sha256: str
    text: str
    status: str
    extras: dict[str, Any]
    extraction_fingerprint: str = ""


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        parent_digest = hashlib.sha256(str(path.resolve().parent).encode("utf-8")).hexdigest()[:12]
        return f"external/{parent_digest}/{path.name}"


def _manifest_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _extraction_fingerprint(base: dict[str, Any], *, ocr_motor: str | None = None) -> str:
    return _fingerprint(
        {
            "schema": EXTRACTION_SCHEMA_VERSION,
            "base_id": base.get("base_id"),
            "natureza": base.get("natureza"),
            "sensitivity_default": base.get("sensitivity_default"),
            "campo_vigencia": base.get("campo_vigencia"),
            "metadados_extras": base.get("metadados_extras"),
            **({"synthetic_conversation_schema": 1} if base.get("dados_sinteticos") else {}),
            "chunking_sugerido": base.get("chunking_sugerido"),
            **({"json_resultado_records": True} if base.get("natureza") == "agregada" else {}),
            # Instalar ou trocar o OCR muda o texto extraído das páginas escaneadas.
            "ocr": ocr.motor() if ocr_motor is None else ocr_motor,
        }
    )


def _chunking_fingerprint(unit: SourceUnit, chunk_size: int, overlap: int) -> str:
    return _fingerprint(
        {
            "schema": CHUNKING_SCHEMA_VERSION,
            "document_id": unit.document_id,
            "document_content_sha256": hashlib.sha256(unit.text.encode("utf-8")).hexdigest(),
            "chunk_size": chunk_size,
            "overlap": overlap,
            "chunking_sugerido": unit.chunking_sugerido,
            "natureza": unit.natureza,
            "formato": unit.formato,
            # Invalidate only the section caches affected by the page-number fix.
            **({"numeric_page_preamble": 1} if re.match(
                r"^\s*\d+\s*\n", unit.text,
            ) else {}),
        }
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _visible_html(text: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(text)
    return parser.text()


def _json_records(payload: Any, *, unwrap_resultado: bool = False) -> list[tuple[int | None, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return list(enumerate(payload["data"], start=1))
    if unwrap_resultado and isinstance(payload, dict) and isinstance(payload.get("resultado"), list):
        return list(enumerate(payload["resultado"], start=1))
    if isinstance(payload, list):
        return list(enumerate(payload, start=1))
    return [(None, payload)]


def _csv_records(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    for record_number, record in enumerate(reader, start=1):
        if any(value not in (None, "") for value in record.values()):
            yield record_number, {str(key): str(value or "") for key, value in record.items() if key}


def _base_origins(base: dict[str, Any]) -> list[str]:
    access = base.get("acesso") or {}
    origins: list[str] = []
    for key in ("origem", "url", "endpoint"):
        value = access.get(key)
        if value:
            origins.append(str(value))
    urls = access.get("urls")
    if isinstance(urls, list):
        origins.extend(str(url) for url in urls if url)
    endpoints = access.get("endpoints")
    if isinstance(endpoints, dict):
        origins.extend(str(url) for url in endpoints.values() if url)
    if not origins and base.get("fonte"):
        origins.append(str(base["fonte"]))
    return list(dict.fromkeys(origins))


def _base_origin(base: dict[str, Any]) -> str:
    origins = _base_origins(base)
    return origins[0] if len(origins) == 1 else ""


def _declared_record_metadata(record: Any, base: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    declared = base.get("metadados_extras") or []
    fields = [str(field) for field in declared]
    validity_field = base.get("campo_vigencia")
    if validity_field:
        fields.append(str(validity_field))
    extracted = {
        field: record[field]
        for field in dict.fromkeys(fields)
        if field in record and record[field] not in (None, "")
    }
    if validity_field and str(validity_field) in extracted:
        extracted["data_vigencia"] = _normalize_date_value(extracted[str(validity_field)])
    return extracted


def _normalize_date_value(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value).strip()
    # Só o ano (ex.: anoPca do PCA): o plano vale até o fim do ano.
    if re.fullmatch(r"\d{4}", text):
        return f"{text}-12-31"
    for pattern in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return text


def _record_logical_id(
    base_id: str,
    source_file: str,
    record: Any,
    fallback: str,
) -> str:
    if isinstance(record, dict):
        for field in (
            "id",
            "idCompra",
            "idContratacao",
            "external_id",
            "source_id",
            "numeroControlePNCP",
            "numeroControlePncpCompra",
            "numeroControlePncpAta",
            "sequencialContrato",
            "numeroContratoEmpenho",
            "ID Licitação",
            "ID",
            "idLicitacao",
            "numeroProcesso",
            "numero",
            "sequencial",
        ):
            value = record.get(field)
            if value not in (None, ""):
                return f"{base_id}:{field}:{value}"
    return f"{base_id}:{source_file}:{fallback}"


def _extras_for_unit(unit: SourceUnit) -> dict[str, Any]:
    extras = {
        key: value
        for key, value in unit.extras.items()
        if key not in _CANONICAL_UNIT_FIELDS and key != "arquivo_bruto"
    }
    # ``origem`` e ``source_file`` já são campos canônicos do chunk.
    relations = []
    for relation in extras.get("relacoes_pncp", []):
        relation = dict(relation)
        document_metadata = relation.get("document_metadata") or {}
        for key in ("orgao_cnpj", "orgao", "esfera", "municipio", "uf", "modalidade",
                    "numero_controle_pncp", "numero_controle_compra", "titulo_documento"):
            if document_metadata.get(key) not in (None, ""):
                extras.setdefault(key, document_metadata[key])
        remaining = {key: value for key, value in document_metadata.items()
                     if key not in extras or extras[key] != value}
        if remaining:
            relation["document_metadata"] = remaining
        else:
            relation.pop("document_metadata", None)
        relations.append(relation)
    if "relacoes_pncp" in extras:
        extras["relacoes_pncp"] = relations
    # Unidades já extraídas também recebem os campos analíticos na retomada.
    # O texto e os valores/unidades originais permanecem intactos.
    if unit.formato == "json":
        try:
            record = json.loads(unit.text)
        except (TypeError, ValueError):
            record = None
        if isinstance(record, dict):
            orgao = record.get("orgaoEntidade") or {}
            unidade = record.get("unidadeOrgao") or {}
            orgao = orgao if isinstance(orgao, dict) else {}
            unidade = unidade if isinstance(unidade, dict) else {}
            fields = {
                "orgao_cnpj": orgao.get("cnpj") or record.get("cnpjOrgao") or record.get("cnpj"),
                "orgao": orgao.get("razaoSocial") or record.get("nomeOrgao") or record.get("razaoSocial"),
                "unidade_nome": unidade.get("nomeUnidade") or record.get("nomeUnidade"),
                "municipio": unidade.get("municipioNome") or record.get("municipio"),
                "uf": unidade.get("ufSigla") or record.get("uf"),
                "esfera": {"E": "estadual", "M": "municipal", "F": "federal", "D": "distrital"}.get(orgao.get("esferaId") or record.get("esfera")),
                "modalidade": record.get("modalidadeNome"),
                "fornecedor": record.get("nomeRazaoSocialFornecedor"),
                "fornecedor_cnpj": (re.sub(r"\D", "", str(record.get("niFornecedor")))
                                    if record.get("niFornecedor") else None),
                "objeto": record.get("objetoContrato") or record.get("objetoCompra") or record.get("objetoContratacao") or record.get("objeto"),
                "numero_controle_pncp": record.get("numeroControlePNCP") or record.get("numeroControlePNCPAta"),
                "ano_pca": record.get("anoPca"),
                "data_assinatura": record.get("dataAssinatura"),
                "data_resultado": record.get("dataResultado"),
                "data_publicacao": record.get("dataPublicacaoPncp"),
            }
            for source in ("valorGlobal", "valorTotal", "valorTotalEstimado"):
                if record.get(source) is not None:
                    fields["valor"] = record[source]
                    extras.setdefault("campo_valor_original", source)
                    break
            for key, value in fields.items():
                if value not in (None, ""):
                    extras[key] = value
    # Campos lidos do corpo JSON também podem ter aparecido nas relações.
    for relation in extras.get("relacoes_pncp", []):
        remaining = {key: value for key, value in (relation.get("document_metadata") or {}).items()
                     if key not in extras or extras[key] != value}
        if remaining:
            relation["document_metadata"] = remaining
        else:
            relation.pop("document_metadata", None)
    return extras


def _base_for(manifest: dict[str, Any], input_dir: Path, base_id: str | None) -> dict[str, Any]:
    bases = manifest.get("bases", [])
    by_id = {str(base.get("base_id")): base for base in bases}
    if base_id:
        if base_id not in by_id:
            raise ValueError(f"base_id não declarado no manifesto: {base_id}")
        return by_id[base_id]

    resolved_input = input_dir.resolve()
    candidates: list[dict[str, Any]] = []
    for base in bases:
        access = base.get("acesso") or {}
        origin = access.get("origem") or base.get("local")
        if origin and (PROJECT_ROOT / str(origin)).resolve() == resolved_input:
            candidates.append(base)
        elif input_dir.name == str(base.get("base_id")):
            candidates.append(base)
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("informe --base-id quando a pasta não identifica uma única base")


def _load_inventory_provenance(
    path: Path | None,
    *,
    expected_manifest_hash: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    inventory_manifest_hash = payload.get("manifest_hash")
    if (
        expected_manifest_hash
        and inventory_manifest_hash
        and inventory_manifest_hash != expected_manifest_hash
    ):
        raise ValueError(
            "manifest_hash do inventário não corresponde ao manifesto utilizado"
        )
    records = payload.get("arquivos", [])
    if not isinstance(records, list):
        raise ValueError("inventário precisa conter uma lista em 'arquivos'")

    provenance: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        base_id = str(record.get("base_id") or "")
        digest = str(record.get("sha256") or "")
        if not base_id or not digest:
            continue
        relation = {
            key: record[key]
            for key in ("source_ref", "artifact_id", "resource_kind", "document_metadata")
            if record.get(key) not in (None, "")
        }
        entry = {
            "origem": str(record.get("origem") or ""),
            "coletado_em": str(record.get("coletado_em") or ""),
            "relacoes": [relation] if relation else [],
        }
        key = (base_id, digest)
        previous = provenance.get(key)
        if previous is None:
            provenance[key] = entry
            continue
        for field in ("origem", "coletado_em"):
            if previous[field] != entry[field]:
                previous[field] = ""
        known_relations = {
            json.dumps(value, ensure_ascii=False, sort_keys=True)
            for value in previous["relacoes"]
        }
        for value in entry["relacoes"]:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if encoded not in known_relations:
                previous["relacoes"].append(value)
                known_relations.add(encoded)
    return provenance


def _load_related_context(path: Path | None, base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Metadados que um arquivo herda de outros artefatos da mesma fonte.

    Um PDF de ata não traz vigência nem título próprios: eles estão no JSON de
    metadados (``...:metadata``) e na listagem ``...:arquivos`` da mesma
    ``source_ref`` no inventário. Devolve, por SHA-256 do arquivo, os campos
    declarados no manifesto (incluindo ``data_vigencia``) e o título do
    documento anunciado na listagem.
    """
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in payload.get("arquivos", []):
        if (
            isinstance(record, dict)
            and record.get("base_id") == base.get("base_id")
            and record.get("status") == "ok"
            and isinstance(record.get("source_ref"), dict)
        ):
            key = json.dumps(record["source_ref"], sort_keys=True)
            groups.setdefault(key, []).append(record)

    def _json_file(record: dict[str, Any] | None) -> Any:
        file_path = PROJECT_ROOT / str((record or {}).get("path") or "")
        if record is None or not file_path.is_file():
            return None
        try:
            return json.loads(_read_text(file_path))
        except json.JSONDecodeError:
            return None

    context: dict[str, dict[str, Any]] = {}
    for records in groups.values():
        by_suffix = {str(record.get("artifact_id", "")).rsplit(":", 1)[-1]: record for record in records}
        inherited = _declared_record_metadata(_json_file(by_suffix.get("metadata")), base)
        listing = _json_file(by_suffix.get("arquivos"))
        linked_contracts_payload = _json_file(by_suffix.get("contratos_vinculados"))
        linked_contracts = []
        for _, contract in _json_records(linked_contracts_payload):
            if not isinstance(contract, dict):
                continue
            identifiers = {
                key: contract[key]
                for key in (
                    "numeroControlePNCP",
                    "numeroControlePNCPContrato",
                    "numeroContratoEmpenho",
                    "anoContrato",
                    "sequencialContrato",
                )
                if contract.get(key) not in (None, "")
            }
            if identifiers:
                linked_contracts.append(identifiers)
        if linked_contracts:
            inherited["contratos_vinculados"] = linked_contracts
        documents = {
            str(document.get("url")): document
            for document in (listing if isinstance(listing, list) else [])
            if isinstance(document, dict) and document.get("url")
        }
        for record in records:
            if record.get("tipo") != "pdf" or not record.get("sha256"):
                continue
            entry = dict(inherited)
            document = documents.get(str(record.get("origem")))
            if document:
                entry["titulo_documento"] = document.get("titulo")
                entry["tipo_documento"] = document.get("tipoDocumentoNome")
            if entry:
                context[str(record["sha256"])] = entry
    return context


def _with_related_context(unit: SourceUnit, related: dict[str, Any] | None) -> SourceUnit:
    if not related:
        return unit
    extras = {**related, **unit.extras}
    vigencia = unit.data_vigencia or related.get("data_vigencia")
    return replace(
        unit,
        extras=extras,
        data_vigencia=str(vigencia) if vigencia not in (None, "") else None,
    )


def _embedded_origin(path: Path) -> str:
    if path.suffix.lower() not in {".html", ".htm", ".aspx"}:
        return ""
    text = _read_text(path)
    patterns = (
        r'<link\b[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']+)',
        r'<link\b[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']',
        r'<meta\b[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']+)',
        r'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:url["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _file_provenance(
    base: dict[str, Any],
    path: Path,
    digest: str,
    inventory_provenance: dict[tuple[str, str], dict[str, str]],
    fallback_collected_at: str,
) -> tuple[str, str]:
    entry = inventory_provenance.get((str(base["base_id"]), digest), {})
    origin = entry.get("origem") or _embedded_origin(path) or _base_origin(base)
    collected_at = entry.get("coletado_em") or fallback_collected_at
    return origin, collected_at


def _unit_common(
    path: Path,
    base: dict[str, Any],
    digest: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
) -> dict[str, Any]:
    source_file = _relative_path(path)
    return {
        "source_file": source_file,
        "arquivo_bruto": source_file,
        "nome_arquivo": path.name,
        "bytes": path.stat().st_size,
        "coletado_em": collected_at,
        "manifest_fonte": base.get("fonte"),
        "manifest_hash": manifest_hash,
        "origem": origin,
        "acesso_tipo": (base.get("acesso") or {}).get("tipo"),
        "sha256": digest,
    }


def _make_unit(
    path: Path,
    base: dict[str, Any],
    digest: str,
    text: str,
    document_id: str,
    formato: str,
    status: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
    extras: dict[str, Any] | None = None,
) -> SourceUnit:
    common = _unit_common(path, base, digest, manifest_hash, collected_at, origin)
    if extras:
        common.update(extras)
    natureza = str(base["natureza"])
    logical_id = str(common.get("logical_id") or f"{base['base_id']}:{common['source_file']}")
    data_vigencia = common.get("data_vigencia")
    return SourceUnit(
        source_file=common["source_file"],
        logical_id=logical_id,
        document_id=document_id,
        base_id=str(base["base_id"]),
        natureza=natureza,
        doc_type=formato,
        sensitivity=str(base.get("sensitivity_default") or "publico"),
        origem=str(common.get("origem") or ""),
        manifest_hash=manifest_hash,
        bytes=int(common["bytes"]),
        data_vigencia=str(data_vigencia) if data_vigencia not in (None, "") else None,
        chunking_sugerido=str(base.get("chunking_sugerido") or "chunking_generico"),
        formato=formato,
        sha256=digest,
        text=text,
        status=status,
        extras=common,
    )


def _unit_to_record(unit: SourceUnit) -> dict[str, Any]:
    return {
        "source_file": unit.source_file,
        "logical_id": unit.logical_id,
        "document_id": unit.document_id,
        "base_id": unit.base_id,
        "natureza": unit.natureza,
        "doc_type": unit.doc_type,
        "sensitivity": unit.sensitivity,
        "origem": unit.origem,
        "manifest_hash": unit.manifest_hash,
        "bytes": unit.bytes,
        "data_vigencia": unit.data_vigencia,
        "chunking_sugerido": unit.chunking_sugerido,
        "formato": unit.formato,
        "sha256": unit.sha256,
        "text": unit.text,
        "status": unit.status,
        "extras": unit.extras,
        "extraction_fingerprint": unit.extraction_fingerprint,
    }


def _unit_from_record(record: dict[str, Any]) -> SourceUnit:
    return SourceUnit(
        source_file=str(record["source_file"]),
        logical_id=str(record["logical_id"]),
        document_id=str(record["document_id"]),
        base_id=str(record["base_id"]),
        natureza=str(record["natureza"]),
        doc_type=str(record["doc_type"]),
        sensitivity=str(record["sensitivity"]),
        origem=str(record.get("origem") or ""),
        manifest_hash=str(record.get("manifest_hash") or ""),
        bytes=int(record.get("bytes", 0) or 0),
        data_vigencia=(
            str(record["data_vigencia"])
            if record.get("data_vigencia") not in (None, "")
            else None
        ),
        chunking_sugerido=str(record.get("chunking_sugerido") or "chunking_generico"),
        formato=str(record["formato"]),
        sha256=str(record["sha256"]),
        text=str(record.get("text") or ""),
        status=str(record.get("status") or "ok"),
        extras=dict(record.get("extras") or {}),
        extraction_fingerprint=str(record.get("extraction_fingerprint") or ""),
    )


def _extraction_state_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.units{output_path.suffix}")


def _load_units_state(path: Path) -> tuple[dict[tuple[str, str, str], list[SourceUnit]], bool]:
    if not path.exists():
        return {}, False
    index: dict[tuple[str, str, str], list[SourceUnit]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            unit = _unit_from_record(json.loads(line))
            key = (unit.source_file, unit.sha256, unit.extraction_fingerprint)
            index.setdefault(key, []).append(unit)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return {}, True
    return index, False


def _load_chunk_state(
    path: Path,
) -> tuple[dict[tuple[str, str, str], list[dict[str, Any]]], bool]:
    if not path.exists():
        return {}, False
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if not {
                "document_id",
                "document_content_sha256",
                "chunking_fingerprint",
            }.issubset(record):
                continue
            key = (
                str(record["document_id"]),
                str(record["document_content_sha256"]),
                str(record["chunking_fingerprint"]),
            )
            index.setdefault(key, []).append(record)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return {}, True
    for records in index.values():
        records.sort(key=lambda record: int(record.get("chunk_index", 0) or 0))
    return index, False


def _refresh_unit_provenance(
    unit: SourceUnit,
    path: Path,
    base: dict[str, Any],
    manifest_hash: str,
    fallback_collected_at: str,
    inventory_provenance: dict[tuple[str, str], dict[str, str]],
) -> SourceUnit:
    origin, collected_at = _file_provenance(
        base, path, unit.sha256, inventory_provenance, fallback_collected_at
    )
    common = _unit_common(path, base, unit.sha256, manifest_hash, collected_at, origin)
    extras = dict(unit.extras)
    extras.update(common)
    return replace(
        unit,
        origem=origin,
        manifest_hash=manifest_hash,
        bytes=path.stat().st_size,
        sensitivity=(unit.sensitivity if base.get("dados_sinteticos")
                     else str(base.get("sensitivity_default") or unit.sensitivity)),
        chunking_sugerido=str(base.get("chunking_sugerido") or unit.chunking_sugerido),
        extras=extras,
    )


def _texto_legivel_da_pagina(path: Path, page_index: int, text: str) -> tuple[str, str, str | None]:
    """(texto, status, extração) da página de PDF; extração ``None`` = mantém o texto.

    Página escaneada (sem camada de texto) ou com texto ilegível vai para o
    OCR (src/ocr.py). "Ilegível" é o critério de src/corpus_quality.py:
    glifos sem mapa de caracteres ("/12 /3 □") ou códigos no lugar das letras
    ("/U0044"), que são só decodificados, sem OCR.
    """
    # A mostly readable page can still have an encoded header. Decode before
    # the whole-page quality threshold, otherwise its first chunk stays bad.
    decodificado = decodificar_codigos_unicode(text)
    if decodificado != text and motivo_de_texto_ilegivel(decodificado) is None:
        return decodificado, "ok", "codigos_unicode_decodificados"
    motivo = motivo_de_texto_ilegivel(text) if text else None
    if text and motivo is None:
        return text, "ok", None
    if ocr.disponivel():
        lido = ocr.texto_da_pagina(path, page_index).strip()
        # Página vazia aceita o que o OCR ler; texto ilegível só troca por legível.
        if lido and (not text or motivo_de_texto_ilegivel(lido) is None):
            return lido, "ok_ocr", ocr.motor()
    return text, "ok" if text else "sem_texto", None


def _reextrair_pagina_ilegivel(path: Path, unit: SourceUnit) -> SourceUnit:
    """Página de PDF guardada no cache com texto ilegível: tenta de novo."""
    if unit.formato != "pdf" or unit.status != "ok":
        return unit
    text, status, extracao = _texto_legivel_da_pagina(path, int(unit.extras["pagina"]) - 1, unit.text)
    if not extracao:
        return unit
    return replace(unit, text=text, status=status, extras={**unit.extras, "extracao": extracao})


def _pdf_units(
    path: Path,
    base: dict[str, Any],
    digest: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
) -> Iterator[SourceUnit]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("extração de PDF requer pypdf; execute pip install -r requirements.txt") from error

    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    base_document_id = f"{base['base_id']}:{digest[:16]}"
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        status = "ok" if text else "sem_texto"
        extras: dict[str, Any] = {"pagina": page_number, "total_paginas": total_pages}
        legivel, status_legivel, extracao = _texto_legivel_da_pagina(path, page_number - 1, text)
        if extracao:
            text, status = legivel, status_legivel
            extras["extracao"] = extracao
        yield _make_unit(
            path,
            base,
            digest,
            text,
            f"{base_document_id}:pagina-{page_number}",
            "pdf",
            status,
            manifest_hash,
            collected_at,
            origin,
            extras,
        )


@dataclass(frozen=True)
class _ArquivoDeOrigem:
    """O arquivo bruto e a proveniência que cada unidade extraída dele carrega."""

    path: Path
    base: dict[str, Any]
    digest: str
    manifest_hash: str
    collected_at: str
    origin: str

    @property
    def document_id(self) -> str:
        return f"{self.base['base_id']}:{self.digest[:16]}"

    def unidade(
        self, text: str, document_id: str, formato: str, status: str, extras: dict[str, Any] | None = None
    ) -> SourceUnit:
        return _make_unit(
            self.path, self.base, self.digest, text, document_id, formato, status,
            self.manifest_hash, self.collected_at, self.origin, extras,
        )


# Campos do registro JSONL que viram metadados da unidade quando presentes.
_CAMPOS_DECLARADOS_JSONL = (
    "dataset",
    "tipo_valor",
    "indicador",
    "dimensao",
    "recorte",
    "metadados",
    "source_ref",
    "artifact_id",
    "source_sha256",
    "source_url",
)


def _file_units(
    path: Path,
    base: dict[str, Any],
    manifest_hash: str,
    fallback_collected_at: str,
    inventory_provenance: dict[tuple[str, str], dict[str, str]],
) -> Iterator[SourceUnit]:
    digest = sha256_file(path)
    origin, collected_at = _file_provenance(
        base, path, digest, inventory_provenance, fallback_collected_at
    )
    suffix = path.suffix.lower()
    arquivo = _ArquivoDeOrigem(path, base, digest, manifest_hash, collected_at, origin)

    if base.get("dados_sinteticos"):
        yield from _unidades_de_conversas(arquivo, suffix)
    elif suffix == ".pdf":
        yield from _pdf_units(path, base, digest, manifest_hash, collected_at, origin)
    elif suffix == ".jsonl":
        yield from _unidades_jsonl(arquivo)
    elif suffix == ".csv":
        records = [record for _, record in _csv_records(path)] if str(base.get("natureza")) == "agregada" else None
        if records and eh_csv_pca(records[0]):
            yield from _unidades_de_indicadores_pca(arquivo, records)
        else:
            yield from _unidades_csv(arquivo)
    elif suffix == ".json":
        yield from _unidades_json(arquivo)
    else:
        text = _read_text(path)
        if suffix in {".html", ".htm", ".aspx"}:
            text = _visible_html(text)
        yield arquivo.unidade(
            text,
            arquivo.document_id,
            suffix.removeprefix(".") or "bin",
            "ok" if text.strip() else "sem_texto",
        )


def _unidades_de_conversas(arquivo: _ArquivoDeOrigem, suffix: str) -> Iterator[SourceUnit]:
    """Uma unidade por conversa sintética, com a sensibilidade declarada nela."""
    from src.synthetic_conversations import conversation_document

    if suffix == ".json":
        payloads = [json.loads(_read_text(arquivo.path))]
    elif suffix == ".jsonl":
        payloads = [json.loads(line) for line in _read_text(arquivo.path).splitlines() if line.strip()]
    else:
        raise ValueError("conversas sintéticas exigem JSON/JSONL canônico, sem cópias TXT")
    seen_ids: set[str] = set()
    for payload in payloads:
        text, sensitivity, extras = conversation_document(payload)
        conversation_id = str(payload["conversation_id"])
        if conversation_id in seen_ids:
            raise ValueError(f"conversation_id duplicado: {conversation_id}")
        seen_ids.add(conversation_id)
        logical_id = f"{arquivo.base['base_id']}:{conversation_id}"
        unit = arquivo.unidade(
            text, f"{arquivo.document_id}:{conversation_id}", suffix.lstrip("."), "ok",
            {**extras, "logical_id": logical_id},
        )
        yield replace(unit, sensitivity=sensitivity)


def _unidades_jsonl(arquivo: _ArquivoDeOrigem) -> Iterator[SourceUnit]:
    """Uma unidade por linha; linha que não é JSON vira unidade com status json_invalido."""
    for line_number, line in enumerate(_read_text(arquivo.path).splitlines(), start=1):
        if not line.strip():
            continue
        payload: Any = None
        try:
            payload = json.loads(line)
            text = (
                str(payload.get("texto"))
                if isinstance(payload, dict) and payload.get("texto")
                else json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
            )
            status = "ok"
        except json.JSONDecodeError:
            text = line.strip()
            status = "json_invalido"
        source_file = _relative_path(arquivo.path)
        extras = _declared_record_metadata(payload, arquivo.base)
        if isinstance(payload, dict):
            for key in _CAMPOS_DECLARADOS_JSONL:
                if key in payload:
                    extras[key] = payload[key]
        yield arquivo.unidade(
            text,
            f"{arquivo.document_id}:linha-{line_number}",
            "jsonl",
            status,
            {
                "linha": line_number,
                "logical_id": (
                    str(payload.get("logical_id"))
                    if isinstance(payload, dict) and payload.get("logical_id")
                    else _record_logical_id(
                        str(arquivo.base["base_id"]),
                        source_file,
                        payload,
                        f"linha-{line_number}",
                    )
                ),
                **extras,
            },
        )


def _unidades_de_indicadores_pca(arquivo: _ArquivoDeOrigem, records: list[dict[str, Any]]) -> Iterator[SourceUnit]:
    """Base agregada: um chunk por indicador calculado, não por linha do CSV
    (ver src/pca_indicators.py)."""
    base = arquivo.base
    source_file = _relative_path(arquivo.path)
    for indicador in indicadores_pca(
        records,
        orgao=str(base.get("orgao") or base.get("fonte") or "o órgão"),
        ano=base.get("ano"),
    ):
        yield arquivo.unidade(
            indicador.texto,
            f"{arquivo.document_id}:indicador-{indicador.id}",
            "csv",
            "ok",
            {
                "indicador": indicador.id,
                "dimensao": indicador.dimensao,
                "logical_id": f"{base['base_id']}:{source_file}:indicador-{indicador.id}",
                **indicador.metadados,
            },
        )


def _unidades_csv(arquivo: _ArquivoDeOrigem) -> Iterator[SourceUnit]:
    """Uma unidade por registro do CSV."""
    for record_number, record in _csv_records(arquivo.path):
        text = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
        source_file = _relative_path(arquivo.path)
        yield arquivo.unidade(
            text,
            f"{arquivo.document_id}:registro-{record_number}",
            "csv",
            "ok",
            {
                "registro": record_number,
                "logical_id": _record_logical_id(
                    str(arquivo.base["base_id"]),
                    source_file,
                    record,
                    f"registro-{record_number}",
                ),
                **_declared_record_metadata(record, arquivo.base),
            },
        )


def _unidades_json(arquivo: _ArquivoDeOrigem) -> Iterator[SourceUnit]:
    """Uma unidade por registro do JSON; JSON ilegível vira unidade com status json_invalido."""
    raw = _read_text(arquivo.path)
    try:
        payload = json.loads(raw)
        status = "ok"
    except json.JSONDecodeError:
        payload = raw
        status = "json_invalido"
    for record_number, record in _json_records(payload, unwrap_resultado=arquivo.base.get("natureza") == "agregada"):
        text = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
        suffix_id = f":registro-{record_number}" if record_number else ""
        source_file = _relative_path(arquivo.path)
        yield arquivo.unidade(
            text,
            f"{arquivo.document_id}{suffix_id}",
            "json",
            status,
            {
                **({"registro": record_number} if record_number else {}),
                "logical_id": _record_logical_id(
                    str(arquivo.base["base_id"]),
                    source_file,
                    record,
                    f"registro-{record_number or 1}",
                ),
                **_declared_record_metadata(record, arquivo.base),
            },
        )


def _join_numeric_page_preamble(text: str) -> str:
    """Keep an OCR page number attached to the following paragraph."""
    return re.sub(r"^\s*(\d+)[ \t]*\n\s*\n", r"\1\n", text, count=1)


def _section_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Divide normas por cabeçalhos e só usa overlap dentro de seções longas."""
    text = _join_numeric_page_preamble(text)
    sections = [
        section.strip()
        for section in re.split(
            r"(?im)(?=^\s*(?:CL[ÁA]USULA|ART(?:IGO)?\.?|CAP[ÍI]TULO)\b)",
            text,
        )
        if section.strip()
    ]
    # OCR sometimes leaves only a page number before the first heading.
    # Keep it with the section, not as a separate, context-free numeric chunk.
    if len(sections) > 1 and sections[0].isdigit():
        sections[1] = sections[0] + "\n" + sections[1]
        sections.pop(0)
    if len(sections) <= 1:
        return list(chunk_text("\n".join(sections), chunk_size, overlap))

    chunks: list[str] = []
    for section in sections:
        if len(section) <= chunk_size:
            chunks.append(section)
        else:
            chunks.extend(chunk_text(section, chunk_size, overlap))
    return chunks


def _chunks_for_unit(unit: SourceUnit, chunk_size: int, overlap: int) -> tuple[list[str], str]:
    hint = unit.chunking_sugerido.casefold()
    if (
        "1 contrato" in hint
        or "1 registro" in hint
        or "1 licitação" in hint
        or "1 licitacao" in hint
        or "1 indicador" in hint
        or (unit.formato in {"json", "jsonl"} and unit.natureza in {"estruturada", "agregada", "conversacional"})
    ):
        return [unit.text.strip()], "registro_inteiro"
    if "cláusula" in hint or "clausula" in hint or "artigo" in hint:
        return _section_chunks(unit.text, chunk_size, overlap), "secoes_normativas"
    return list(chunk_text(_join_numeric_page_preamble(unit.text), chunk_size, overlap)), "generico"


def _chunk_records(unit: SourceUnit, chunk_size: int, overlap: int) -> list[dict[str, Any]]:
    if unit.status not in {"ok", "ok_ocr"} or not unit.text.strip():
        return []
    chunks, chunking_strategy = _chunks_for_unit(unit, chunk_size, overlap)
    records: list[dict[str, Any]] = []
    unit_extras = _extras_for_unit(unit)
    document_content_digest = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
    chunking_fingerprint = _chunking_fingerprint(unit, chunk_size, overlap)
    for index, text in enumerate(chunks, start=1):
        chunk_content_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunk_id = f"{unit.document_id}:chunk-{index}-{chunk_content_digest[:12]}"
        records.append(
            {
                "chunk_id": chunk_id,
                "document_id": unit.document_id,
                "logical_id": unit.logical_id,
                "base_id": unit.base_id,
                "natureza": unit.natureza,
                "doc_type": unit.doc_type,
                "sensitivity": unit.sensitivity,
                "parent_chunk_id": unit.document_id,
                "chunk_index": index,
                "chunk_count": len(chunks),
                "chunking_strategy": chunking_strategy,
                "chunking_fingerprint": chunking_fingerprint,
                "source_file": unit.source_file,
                "origem": unit.origem,
                "manifest_fonte": unit.extras.get("manifest_fonte"),
                "manifest_hash": unit.manifest_hash,
                "bytes": unit.bytes,
                "coletado_em": unit.extras.get("coletado_em"),
                "data_vigencia": unit.data_vigencia,
                "formato": unit.formato,
                "sha256": unit.sha256,
                "document_content_sha256": document_content_digest,
                "chunk_content_sha256": chunk_content_digest,
                "status_extracao": unit.status,
                "nome_arquivo": unit.extras.get("nome_arquivo"),
                "acesso_tipo": unit.extras.get("acesso_tipo"),
                "text": text,
                "extras": {
                    key: value
                    for key, value in unit_extras.items()
                    if key not in {"manifest_fonte", "coletado_em", "nome_arquivo", "acesso_tipo"}
                },
            }
        )
    return records


def _refresh_cached_chunks(
    cached: list[dict[str, Any]],
    unit: SourceUnit,
) -> list[dict[str, Any]]:
    """Atualiza proveniência sem recalcular a segmentação nem o texto."""

    unit_extras = _extras_for_unit(unit)
    document_content_digest = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
    refreshed: list[dict[str, Any]] = []
    for cached_record in cached:
        record = dict(cached_record)
        record.update(
            {
                "document_id": unit.document_id,
                "logical_id": unit.logical_id,
                "base_id": unit.base_id,
                "natureza": unit.natureza,
                "doc_type": unit.doc_type,
                "sensitivity": unit.sensitivity,
                "parent_chunk_id": unit.document_id,
                "source_file": unit.source_file,
                "origem": unit.origem,
                "manifest_fonte": unit.extras.get("manifest_fonte"),
                "manifest_hash": unit.manifest_hash,
                "bytes": unit.bytes,
                "coletado_em": unit.extras.get("coletado_em"),
                "data_vigencia": unit.data_vigencia,
                "formato": unit.formato,
                "sha256": unit.sha256,
                "document_content_sha256": document_content_digest,
                "status_extracao": unit.status,
                "nome_arquivo": unit.extras.get("nome_arquivo"),
                "acesso_tipo": unit.extras.get("acesso_tipo"),
                "extras": {
                    key: value
                    for key, value in unit_extras.items()
                    if key not in {"manifest_fonte", "coletado_em", "nome_arquivo", "acesso_tipo"}
                },
            }
        )
        refreshed.append(record)
    return refreshed


def _input_files(input_dir: Path) -> tuple[list[Path], int]:
    paths = sorted(path for path in input_dir.rglob("*") if path.is_file())
    supported = [path for path in paths if path.suffix.lower() in SUPPORTED_SUFFIXES]
    return supported, len(paths) - len(supported)


def _input_files_from_inventory(
    input_dir: Path,
    inventory_path: Path,
    base_id: str,
) -> tuple[list[Path], int]:
    """Resolve somente arquivos promovidos e listados no inventário."""
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    records = payload.get("arquivos", [])
    if not isinstance(records, list):
        raise ValueError("inventário precisa conter uma lista em 'arquivos'")

    selected: list[Path] = []
    ignored = 0
    seen: set[Path] = set()
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "ok":
            continue
        if str(record.get("base_id") or "") != base_id:
            continue
        raw_path = record.get("source_file") or record.get("path")
        if not raw_path:
            raise ValueError(f"registro do inventário sem source_file: {record.get('logical_id')}")
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path = path.resolve()
        try:
            path.relative_to(input_dir.resolve())
        except ValueError as error:
            raise ValueError(
                f"arquivo do inventário fora do diretório de entrada: {path}"
            ) from error
        if not path.is_file():
            raise FileNotFoundError(f"arquivo do inventário não encontrado: {path}")
        if path in seen:
            continue
        seen.add(path)
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            selected.append(path)
        else:
            ignored += 1
    return sorted(selected), ignored


def _atomic_write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    partial.replace(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


@dataclass
class _Preparacao:
    """Chunks e documentos acumulados na preparação, com os acertos do cache de chunks."""

    chunks_cache: dict[tuple[str, str, str], list[dict[str, Any]]]
    chunk_size: int
    overlap: int
    records: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    chunk_cache_hits: int = 0
    chunk_cache_misses: int = 0

    def adicionar_todas(self, units: Iterable[SourceUnit]) -> int:
        """Acrescenta as unidades em ordem; devolve quantas eram."""
        total = 0
        for unit in units:
            self.adicionar(unit)
            total += 1
        return total

    def adicionar(self, unit: SourceUnit) -> None:
        """Chunks da unidade (do cache quando o texto e a segmentação não mudaram) e o registro do documento."""
        self.status_counts[unit.status] = self.status_counts.get(unit.status, 0) + 1
        document_content_digest = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
        chunking_fingerprint = _chunking_fingerprint(unit, self.chunk_size, self.overlap)
        chunk_cache_key = (unit.document_id, document_content_digest, chunking_fingerprint)
        if chunk_cache_key in self.chunks_cache:
            chunks = _refresh_cached_chunks(self.chunks_cache[chunk_cache_key], unit)
            self.chunk_cache_hits += 1
        else:
            chunks = _chunk_records(unit, self.chunk_size, self.overlap)
            self.chunk_cache_misses += 1
        self.records.extend(chunks)
        self.documents.append(
            {
                "document_id": unit.document_id,
                "logical_id": unit.logical_id,
                "source_file": unit.source_file,
                "base_id": unit.base_id,
                "natureza": unit.natureza,
                "doc_type": unit.doc_type,
                "sensitivity": unit.sensitivity,
                "origem": unit.origem,
                "manifest_fonte": unit.extras.get("manifest_fonte"),
                "manifest_hash": unit.manifest_hash,
                "bytes": unit.bytes,
                "coletado_em": unit.extras.get("coletado_em"),
                "data_vigencia": unit.data_vigencia,
                "formato": unit.formato,
                "sha256": unit.sha256,
                "status_extracao": unit.status,
                "chunks": len(chunks),
                "extras": _extras_for_unit(unit),
            }
        )


@observar_offline(Stage.PREPARACAO)
def prepare_documents(
    input_dir: Path,
    output_path: Path,
    report_path: Path,
    manifest_path: Path,
    *,
    base_id: str | None = None,
    inventory_path: Path | None = None,
    chunk_size: int = 1200,
    overlap: int = 150,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not input_dir.exists():
        raise FileNotFoundError(f"diretório de entrada não encontrado: {input_dir}")
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("use chunk_size >= 1 e overlap entre 0 e chunk_size-1")

    manifest = load_manifest(manifest_path)
    summary_manifest_hash = _manifest_hash(manifest_path)
    base = _base_for(manifest, input_dir, base_id)
    inventory_provenance = _load_inventory_provenance(
        inventory_path,
        expected_manifest_hash=summary_manifest_hash,
    )
    related_context = _load_related_context(inventory_path, base)
    fallback_collected_at = datetime.now(timezone.utc).isoformat()
    paths, ignored = (
        _input_files_from_inventory(input_dir, inventory_path, str(base["base_id"]))
        if inventory_path is not None
        else _input_files(input_dir)
    )
    units_state_path = _extraction_state_path(output_path)
    units_cache, units_cache_invalid = _load_units_state(units_state_path)
    chunks_cache, chunks_cache_invalid = _load_chunk_state(output_path)
    preparacao = _Preparacao(chunks_cache, chunk_size, overlap)
    state_units: list[SourceUnit] = []
    failures: list[dict[str, str]] = []
    extraction_cache_hits = 0
    extraction_cache_misses = 0

    for path in paths:
        digest = sha256_file(path)
        source_file = _relative_path(path)
        extraction_fingerprint = _extraction_fingerprint(base)
        try:
            units, from_cache = _unidades_extraidas(
                path, digest, source_file, base, extraction_fingerprint, units_cache,
                summary_manifest_hash, fallback_collected_at, inventory_provenance,
            )
            if from_cache:
                extraction_cache_hits += 1
            else:
                extraction_cache_misses += 1
            state_units.extend(units)
            units = _com_relacoes(
                units,
                inventory_provenance.get((str(base["base_id"]), digest), {}).get("relacoes") or [],
                related_context.get(digest),
            )
        except Exception as error:
            failures.append({"source_file": source_file, "erro": f"{type(error).__name__}: {error}"})
            continue
        preparacao.adicionar_todas(units)

    derived_result_indicators = 0
    if base.get("base_id") == "pca_agregado" and inventory_path is not None:
        derived_result_indicators = preparacao.adicionar_todas(_unidades_de_resultados_homologados(
            inventory_path, base, summary_manifest_hash, fallback_collected_at
        ))

    records, documents = preparacao.records, preparacao.documents
    summary = {
        "arquivos": len(paths),
        "documentos": len(documents),
        "chunks": len(records),
        "ignorados": ignored,
        "falhas": len(failures),
        "status_extracao": preparacao.status_counts,
        "indicadores_resultados_homologados": derived_result_indicators,
        "base_id": base["base_id"],
        "natureza": base["natureza"],
        "manifest_hash": summary_manifest_hash,
        "corpus_version": inventory_path.stem if inventory_path and re.fullmatch(r"v\d+", inventory_path.stem) else None,
        "inventory": _relative_path(inventory_path) if inventory_path else None,
        "extraction_cache_hits": extraction_cache_hits,
        "extraction_cache_misses": extraction_cache_misses,
        "chunk_cache_hits": preparacao.chunk_cache_hits,
        "chunk_cache_misses": preparacao.chunk_cache_misses,
        "incremental_state": _relative_path(units_state_path),
        "incremental_state_invalid": units_cache_invalid or chunks_cache_invalid,
    }
    if not dry_run:
        _gravar_preparacao(
            output_path, units_state_path, report_path, manifest_path, inventory_path,
            base, summary, preparacao, state_units, failures,
        )
    return summary


def _com_relacoes(
    units: list[SourceUnit], relations: list[Any], related: Any
) -> list[SourceUnit]:
    """Relações PNCP do inventário e contexto dos arquivos relacionados, fora do cache de extração."""
    if relations:
        units = [
            replace(unit, extras={**unit.extras, "relacoes_pncp": relations})
            for unit in units
        ]
    # Aplicado a cada execução (e fora do cache): depende de outros arquivos.
    return [_with_related_context(unit, related) for unit in units]


def _gravar_preparacao(
    output_path: Path,
    units_state_path: Path,
    report_path: Path,
    manifest_path: Path,
    inventory_path: Path | None,
    base: dict[str, Any],
    summary: dict[str, Any],
    preparacao: _Preparacao,
    state_units: list[SourceUnit],
    failures: list[dict[str, str]],
) -> None:
    """Chunks, estado incremental da extração e relatório, com a versão do inventário nos registros."""
    records, documents = preparacao.records, preparacao.documents
    if inventory_path is not None:
        inventory_payload = json.loads(inventory_path.read_text(encoding="utf-8"))
        provenance = {
            "inventory_version": inventory_payload.get("version"),
            "inventory_hash": sha256_file(inventory_path),
        }
        for record in records + documents:
            record.update(provenance)
    _atomic_write_jsonl(output_path, records)
    _atomic_write_jsonl(units_state_path, [_unit_to_record(unit) for unit in state_units])
    _atomic_write_json(
        report_path,
        {
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "manifest": _relative_path(manifest_path),
            "manifest_hash": summary["manifest_hash"],
            "base_id": base["base_id"],
            "resumo": summary,
            "documentos": documents,
            "falhas": failures,
        },
    )


def _unidades_extraidas(
    path: Path,
    digest: str,
    source_file: str,
    base: dict[str, Any],
    extraction_fingerprint: str,
    units_cache: dict[tuple[str, str, str], list[SourceUnit]],
    summary_manifest_hash: str,
    fallback_collected_at: str,
    inventory_provenance: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[SourceUnit], bool]:
    """(unidades do arquivo, se vieram do cache de extração); extrai de novo só o que mudou."""
    cache_key = (source_file, digest, extraction_fingerprint)
    # Instalar OCR não invalida texto existente. Só páginas antes sem
    # texto precisam de uma nova extração; os demais parâmetros devem coincidir.
    previous_key = (source_file, digest, _extraction_fingerprint(base, ocr_motor="sem-ocr"))
    if cache_key not in units_cache and ocr.disponivel() and previous_key in units_cache:
        refreshed = []
        for cached_unit in units_cache[previous_key]:
            unit = replace(cached_unit, extraction_fingerprint=extraction_fingerprint)
            if unit.formato == "pdf" and unit.status == "sem_texto":
                text = ocr.texto_da_pagina(path, int(unit.extras["pagina"]) - 1).strip()
                if text:
                    unit = replace(unit, text=text, status="ok_ocr",
                                   extras={**unit.extras, "extracao": ocr.motor()})
            refreshed.append(unit)
        units_cache[cache_key] = refreshed
    if cache_key not in units_cache:
        units = [
            replace(unit, extraction_fingerprint=extraction_fingerprint)
            for unit in _file_units(
                path,
                base,
                summary_manifest_hash,
                fallback_collected_at,
                inventory_provenance,
            )
        ]
        return units, False
    units = list(units_cache[cache_key])
    if any(unit.manifest_hash != summary_manifest_hash for unit in units):
        units = [
            _refresh_unit_provenance(
                unit,
                path,
                base,
                summary_manifest_hash,
                fallback_collected_at,
                inventory_provenance,
            )
            for unit in units
        ]
    # Extrações antigas guardaram glifos ("/12 /3 □") como texto.
    units = [_reextrair_pagina_ilegivel(path, unit) for unit in units]
    return units, True


def _unidades_de_resultados_homologados(
    inventory_path: Path,
    base: dict[str, Any],
    summary_manifest_hash: str,
    fallback_collected_at: str,
) -> Iterator[SourceUnit]:
    """Um documento por indicador de resultados homologados calculado sobre o inventário."""
    inventory_payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    for indicador in indicadores_resultados_homologados(inventory_payload, PROJECT_ROOT):
        text = str(indicador.get("texto") or "").strip()
        if not text:
            continue
        indicator_id = str(indicador["indicador"])
        document_id = f"pca_agregado:pncp:{indicator_id}"
        logical_id = document_id
        source_file = f"{_relative_path(inventory_path)}#derivado:{indicator_id}"
        content_digest = hashlib.sha256(
            json.dumps(indicador, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        extras = {
            key: value
            for key, value in indicador.items()
            if key != "texto"
        }
        # O indicador é calculado localmente; a origem aponta para uma
        # fonte pública do cálculo, nunca para o caminho do inventário.
        sources = indicador.get("observacoes") or inventory_payload.get("arquivos") or []
        indicator_origin = next(
            (str(source["origem"]) for source in sources
             if isinstance(source, dict) and
             str(source.get("origem") or "").startswith(("https://", "http://"))),
            "PNCP: resultados de itens homologados",
        )
        extras["tipo_origem"] = "calculo_local_sobre_fontes_publicas"
        yield SourceUnit(
            source_file=source_file,
            logical_id=logical_id,
            document_id=document_id,
            base_id="pca_agregado",
            natureza=str(base["natureza"]),
            doc_type="indicador_pncp",
            sensitivity=str(base.get("sensitivity_default") or "publico"),
            origem=indicator_origin,
            manifest_hash=summary_manifest_hash,
            bytes=len(text.encode("utf-8")),
            data_vigencia=None,
            chunking_sugerido=str(base.get("chunking_sugerido") or "chunking_generico"),
            formato="json",
            sha256=content_digest,
            text=text,
            status="ok",
            extras={
                "source_file": source_file,
                "arquivo_bruto": source_file,
                "nome_arquivo": indicator_id,
                "manifest_fonte": base.get("fonte"),
                "manifest_hash": summary_manifest_hash,
                "origem": indicator_origin,
                "acesso_tipo": (base.get("acesso") or {}).get("tipo"),
                "logical_id": logical_id,
                "coletado_em": fallback_collected_at,
                **extras,
            },
        )


def _latest_corpus_paths(
    data_root: Path,
    base_id: str,
    manifest_path: Path,
) -> tuple[str, Path, Path, Path]:
    inventory_root = data_root / "inventory"
    versioned_inventories = [
        (int(match.group(1)), path)
        for path in inventory_root.glob("v*.json")
        if (match := re.fullmatch(r"v(\d+)\.json", path.name))
    ]
    if not versioned_inventories:
        raise FileNotFoundError(f"nenhum inventário versionado encontrado em {inventory_root}")

    version_number, inventory_path = max(versioned_inventories, key=lambda item: item[0])
    version = f"v{version_number}"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("version") != version:
        raise ValueError(f"versão declarada no inventário não corresponde ao arquivo {inventory_path}")
    if inventory.get("completo") is not True:
        raise ValueError(f"o inventário mais recente ({version}) está incompleto")
    if inventory.get("manifest_hash") != _manifest_hash(manifest_path):
        raise ValueError(
            f"o inventário mais recente ({version}) não corresponde ao manifesto atual; "
            "atualize a coleta antes de preparar os documentos"
        )

    bases = inventory.get("por_base", [])
    base_inventory = next(
        (item for item in bases if isinstance(item, dict) and item.get("base_id") == base_id),
        None,
    )
    if base_inventory is None or base_inventory.get("status") != "complete":
        raise ValueError(f"a base {base_id!r} não está completa no inventário mais recente ({version})")

    input_dir = data_root / "raw" / version
    if not input_dir.is_dir():
        raise FileNotFoundError(f"arquivos brutos do inventário mais recente não encontrados: {input_dir}")
    output_dir = data_root / "normalized" / version / base_id
    return version, input_dir, inventory_path, output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "corpus_manifest.yaml")
    parser.add_argument("--base-id", help="base declarada no manifesto; obrigatório quando houver ambiguidade")
    parser.add_argument(
        "--inventory",
        type=Path,
        help="inventário da coleta; recupera origem e coletado_em específicos de cada arquivo",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="usa o inventário completo mais recente e caminhos versionados de raw/ e normalized/",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=PROJECT_ROOT / "data",
        help="raiz que contém inventory/, raw/ e normalized/ (usada com --latest)",
    )
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=150)
    parser.add_argument("--dry-run", action="store_true", help="prepara e resume sem gravar JSONL")
    args = parser.parse_args(argv)
    try:
        if args.latest:
            if not args.base_id:
                parser.error("--base-id é obrigatório com --latest")
            if args.input or args.output or args.report or args.inventory:
                parser.error("--latest define --input, --output, --report e --inventory automaticamente")
            version, input_dir, inventory_path, output_dir = _latest_corpus_paths(
                args.data_root.resolve(),
                args.base_id,
                args.manifest.resolve(),
            )
            output_path = output_dir / "documents.jsonl"
            report_path = output_dir / "extraction_report.json"
        else:
            version = None
            input_dir = args.input or DEFAULT_INPUT
            output_path = args.output or DEFAULT_OUTPUT
            report_path = args.report or DEFAULT_REPORT
            inventory_path = args.inventory

        summary = prepare_documents(
            input_dir.resolve(),
            output_path.resolve(),
            report_path.resolve(),
            args.manifest.resolve(),
            base_id=args.base_id,
            inventory_path=inventory_path.resolve() if inventory_path else None,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            dry_run=args.dry_run,
        )
        if version:
            summary["corpus_version"] = version
            summary["inventory"] = _relative_path(inventory_path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
