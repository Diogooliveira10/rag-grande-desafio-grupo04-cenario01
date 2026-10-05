"""Leituras públicas e somente leitura dos dados reais do projeto.

Este módulo não participa do pipeline RAG. Ele apenas transforma a base SQL,
o inventário oficial e o manifesto do corpus em contratos HTTP estáveis para
as telas de exploração e transparência do frontend.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

import config
from src.fetch_corpus import load_manifest
from src.structured_db import Banco, banco_padrao


INVENTORY_DIR = config.ROOT_DIR / "data" / "inventory"
MANIFEST_PATH = config.ROOT_DIR / "corpus_manifest.yaml"


class ContratacaoRecord(BaseModel):
    id: str
    numero_contrato: str
    orgao: Optional[str] = None
    orgao_cnpj: Optional[str] = None
    fornecedor: Optional[str] = None
    objeto: Optional[str] = None
    valor_global: Optional[float] = None
    data_homologacao: Optional[str] = None
    modalidade: Optional[str] = None
    fonte_oficial: str = "PNCP"
    status: Optional[str] = None
    esfera: Optional[str] = None
    municipio: Optional[str] = None


class DocumentoRecord(BaseModel):
    id: str
    titulo: str
    tipo: str
    orgao: Optional[str] = None
    ano: Optional[int] = None
    fonte: Optional[str] = None
    descricao: Optional[str] = None
    link: Optional[str] = None
    base_id: str
    natureza: str
    formato: Optional[str] = None


class OrgaoRecord(BaseModel):
    cnpj: Optional[str] = None
    sigla: Optional[str] = None
    nome: str
    esfera: Optional[str] = None
    municipio: Optional[str] = None
    total_contratacoes: int = 0
    valor_total: Optional[float] = None
    principais_categorias: list[str] = Field(default_factory=list)
    principais_fornecedores: list[str] = Field(default_factory=list)


class OfficialSource(BaseModel):
    id: str
    nome: str
    sigla: Optional[str] = None
    instituicao: Optional[str] = None
    tipo_informacao: Optional[str] = None
    url: Optional[str] = None
    natureza: Literal["normativa", "estruturada", "conversacional", "agregada"]
    frequencia_coleta: Optional[str] = None
    descricao: Optional[str] = None
    escopo: str
    formatos: list[str] = Field(default_factory=list)


class ProjectBaseSummary(BaseModel):
    base_id: str
    natureza: str
    escopo: str
    status: Optional[str] = None
    documentos: int = 0
    formatos: dict[str, int] = Field(default_factory=dict)


class ProjectSummary(BaseModel):
    projeto: str
    scenario_id: str
    descricao: Optional[str] = None
    versao_manifesto: Optional[str] = None
    versao_corpus: str
    coletado_em: Optional[str] = None
    inventario_completo: bool
    total_documentos: int
    total_itens: int
    total_falhas: int
    bases: list[ProjectBaseSummary]


def _latest_inventory_path() -> Path:
    candidates: list[tuple[int, Path]] = []
    for path in INVENTORY_DIR.glob("v*.json"):
        match = re.fullmatch(r"v(\d+)\.json", path.name)
        if match:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        raise FileNotFoundError("nenhum inventário oficial foi encontrado")
    return max(candidates, key=lambda item: item[0])[1]


def _inventory() -> dict[str, Any]:
    return json.loads(_latest_inventory_path().read_text(encoding="utf-8"))


def _bounded(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, 200)), max(0, offset)


def listar_contratacoes(
    *, limit: int = 50, offset: int = 0, busca: Optional[str] = None, orgao: Optional[str] = None
) -> list[ContratacaoRecord]:
    limit, offset = _bounded(limit, offset)
    clauses: list[str] = []
    params: list[Any] = []
    if busca:
        pattern = f"%{busca.strip().casefold()}%"
        clauses.append(
            "(LOWER(COALESCE(objeto, '')) LIKE ? OR LOWER(COALESCE(fornecedor, '')) LIKE ? "
            "OR LOWER(COALESCE(numero_controle_pncp, '')) LIKE ? OR LOWER(COALESCE(orgao_nome, '')) LIKE ?)"
        )
        params.extend([pattern] * 4)
    if orgao:
        clauses.append("LOWER(COALESCE(orgao_nome, '')) LIKE ?")
        params.append(f"%{orgao.strip().casefold()}%")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT numero_controle_pncp, orgao_nome, orgao_cnpj, fornecedor, objeto, valor, "
        "data_publicacao, modalidade, situacao, esfera, municipio FROM contratacoes"
        f"{where} ORDER BY COALESCE(data_publicacao, '') DESC, numero_controle_pncp LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])
    rows = banco_padrao().consultar(sql, params)
    return [
        ContratacaoRecord(
            id=row["numero_controle_pncp"],
            numero_contrato=row["numero_controle_pncp"],
            orgao=row.get("orgao_nome"),
            orgao_cnpj=row.get("orgao_cnpj"),
            fornecedor=row.get("fornecedor"),
            objeto=row.get("objeto"),
            valor_global=row.get("valor"),
            # A base não possui uma coluna de homologação confiável. A data
            # pública disponível é exposta sem alegar que seja homologação.
            data_homologacao=None,
            modalidade=row.get("modalidade"),
            status=row.get("situacao"),
            esfera=row.get("esfera"),
            municipio=row.get("municipio"),
        )
        for row in rows
    ]


_DOCUMENT_TYPES = {
    "editais_normativo": "Normativo",
    "contratos_estruturado": "Contrato",
    "atas_conversacional": "Ata de Registro",
    "pca_agregado": "PCA",
}


def _year(record: dict[str, Any]) -> Optional[int]:
    metadata = record.get("document_metadata") or {}
    for key in ("ano", "ano_compra", "anoCompra", "ano_pca", "anoPca"):
        value = metadata.get(key)
        if value is not None and str(value).isdigit():
            return int(value)
    match = re.search(r"/(20\d{2})(?:/|\b)", str(record.get("origem") or ""))
    return int(match.group(1)) if match else None


def listar_documentos(
    *, limit: int = 50, offset: int = 0, busca: Optional[str] = None, tipo: Optional[str] = None
) -> list[DocumentoRecord]:
    limit, offset = _bounded(limit, offset)
    result: list[DocumentoRecord] = []
    needle = (busca or "").strip().casefold()
    requested_type = (tipo or "").strip().casefold()
    for record in _inventory().get("arquivos", []):
        if record.get("status") != "ok":
            continue
        metadata = record.get("document_metadata") or {}
        base_id = str(record.get("base_id") or "")
        document_type = _DOCUMENT_TYPES.get(base_id, str(record.get("resource_kind") or "Documento"))
        title = str(
            metadata.get("titulo")
            or metadata.get("numero_controle_pncp")
            or metadata.get("numero_controle_compra")
            or record.get("artifact_id")
            or record.get("logical_id")
        )
        orgao_name = metadata.get("orgao") or metadata.get("orgao_nome")
        description = str(record.get("resource_kind") or "") or None
        haystack = " ".join(filter(None, (title, str(orgao_name or ""), str(description or "")))).casefold()
        if needle and needle not in haystack:
            continue
        if requested_type and requested_type not in {document_type.casefold(), base_id.casefold()}:
            continue
        result.append(
            DocumentoRecord(
                id=str(record.get("artifact_id") or record.get("logical_id")),
                titulo=title,
                tipo=document_type,
                orgao=orgao_name,
                ano=_year(record),
                fonte=record.get("source_ref", {}).get("provider") or metadata.get("fonte_oficial"),
                descricao=description,
                link=record.get("origem") if str(record.get("origem") or "").startswith("https://") else None,
                base_id=base_id,
                natureza=str(record.get("natureza") or ""),
                formato=record.get("tipo"),
            )
        )
    return result[offset : offset + limit]


def listar_orgaos(
    *, limit: int = 50, offset: int = 0, busca: Optional[str] = None
) -> list[OrgaoRecord]:
    limit, offset = _bounded(limit, offset)
    clauses = ["orgao_nome IS NOT NULL", "TRIM(orgao_nome) <> ''"]
    params: list[Any] = []
    if busca:
        clauses.append("LOWER(orgao_nome) LIKE ?")
        params.append(f"%{busca.strip().casefold()}%")
    params.extend([limit, offset])
    rows = banco_padrao().consultar(
        "SELECT orgao_cnpj, orgao_nome, esfera, municipio, COUNT(*) AS total, SUM(valor) AS valor_total "
        f"FROM contratacoes WHERE {' AND '.join(clauses)} "
        "GROUP BY orgao_cnpj, orgao_nome, esfera, municipio ORDER BY total DESC, orgao_nome LIMIT ? OFFSET ?",
        params,
    )
    suppliers = banco_padrao().consultar(
        "SELECT orgao_cnpj, fornecedor, SUM(valor) AS total FROM contratacoes "
        "WHERE fornecedor IS NOT NULL GROUP BY orgao_cnpj, fornecedor ORDER BY total DESC"
    )
    by_org: dict[str, list[str]] = defaultdict(list)
    for supplier in suppliers:
        key = str(supplier.get("orgao_cnpj") or "")
        name = supplier.get("fornecedor")
        if name and len(by_org[key]) < 3:
            by_org[key].append(str(name))
    return [
        OrgaoRecord(
            cnpj=row.get("orgao_cnpj"),
            # Não existe sigla oficial confiável na base; não inferimos uma.
            sigla=None,
            nome=row["orgao_nome"],
            esfera=row.get("esfera"),
            municipio=row.get("municipio"),
            total_contratacoes=int(row.get("total") or 0),
            valor_total=row.get("valor_total"),
            principais_categorias=[],
            principais_fornecedores=by_org.get(str(row.get("orgao_cnpj") or ""), []),
        )
        for row in rows
    ]


def _source_url(base: dict[str, Any]) -> Optional[str]:
    access = base.get("acesso") or {}
    urls = access.get("urls") or []
    if urls:
        return str(urls[0])
    if access.get("provider") == "pncp":
        return "https://pncp.gov.br"
    return None


def listar_fontes() -> list[OfficialSource]:
    manifest = load_manifest(MANIFEST_PATH)
    return [
        OfficialSource(
            id=str(base["base_id"]),
            nome=str(base.get("fonte") or base["base_id"]),
            sigla="PNCP" if (base.get("acesso") or {}).get("provider") == "pncp" else None,
            instituicao="Portal Nacional de Contratações Públicas"
            if (base.get("acesso") or {}).get("provider") == "pncp"
            else None,
            tipo_informacao=base.get("chunking_sugerido"),
            url=_source_url(base),
            natureza=base["natureza"],
            frequencia_coleta=None,
            descricao=base.get("fonte"),
            escopo=str(base.get("escopo") or ""),
            formatos=[str(value) for value in (base.get("formatos") or [base.get("formato")]) if value],
        )
        for base in manifest.get("bases", [])
        if base.get("escopo") == "oficial"
    ]


def project_summary() -> ProjectSummary:
    inventory = _inventory()
    manifest = load_manifest(MANIFEST_PATH)
    totals = inventory.get("totais") or {}
    return ProjectSummary(
        projeto="NEXO RJ — Adaptive RAG para contratações públicas",
        scenario_id=str(inventory.get("scenario_id") or manifest.get("scenario_id") or ""),
        descricao=manifest.get("description"),
        versao_manifesto=str(manifest.get("version")) if manifest.get("version") is not None else None,
        versao_corpus=str(inventory.get("version") or _latest_inventory_path().stem),
        coletado_em=inventory.get("coletado_em"),
        inventario_completo=bool(inventory.get("completo")),
        total_documentos=int(totals.get("arquivos") or 0),
        total_itens=int(totals.get("itens") or 0),
        total_falhas=int(totals.get("falhas") or totals.get("failure_count") or 0),
        bases=[
            ProjectBaseSummary(
                base_id=str(base.get("base_id") or ""),
                natureza=str(base.get("natureza") or ""),
                escopo=str(base.get("escopo") or ""),
                status=base.get("status"),
                documentos=int(base.get("arquivos") or 0),
                formatos={str(k): int(v) for k, v in (base.get("formatos") or {}).items()},
            )
            for base in inventory.get("por_base", [])
        ],
    )


__all__ = [
    "ContratacaoRecord", "DocumentoRecord", "OrgaoRecord", "OfficialSource",
    "ProjectSummary", "listar_contratacoes", "listar_documentos", "listar_orgaos",
    "listar_fontes", "project_summary",
]
