"""Divisão das unidades em chunks com o contrato canônico de metadados."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from src.ingest import chunk_text
from src.preparation.common import SourceUnit, _CANONICAL_UNIT_FIELDS
from src.preparation.state import _chunking_fingerprint


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
