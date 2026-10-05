"""Contrato das telas de exploração; campos não publicados continuam nulos."""

from pydantic import BaseModel


class ContratacaoRecord(BaseModel):
    id: str
    numero_contrato: str
    orgao: str | None
    orgao_cnpj: str | None
    fornecedor: str | None
    objeto: str | None
    valor_global: float | None
    data_homologacao: str | None
    modalidade: str | None
    fonte_oficial: str
    status: str | None
    esfera: str | None
    municipio: str | None


class DocumentoRecord(BaseModel):
    id: str
    titulo: str
    tipo: str
    orgao: str | None
    ano: int | None
    fonte: str | None
    descricao: str | None
    link: str | None
    base_id: str
    natureza: str
    formato: str | None


class OrgaoRecord(BaseModel):
    cnpj: str | None
    sigla: str | None
    nome: str
    esfera: str | None
    municipio: str | None
    total_contratacoes: int
    valor_total: float | None
    principais_categorias: list[str]
    principais_fornecedores: list[str]


class OfficialSource(BaseModel):
    id: str
    nome: str
    sigla: str | None
    instituicao: str | None
    tipo_informacao: str | None
    url: str | None
    natureza: str
    frequencia_coleta: str | None
    descricao: str | None
    escopo: str
    formatos: list[str]


class ProjectBaseSummary(BaseModel):
    base_id: str
    natureza: str
    escopo: str
    status: str | None
    documentos: int
    formatos: dict[str, int]


class ProjectSummary(BaseModel):
    projeto: str
    scenario_id: str
    descricao: str | None
    versao_manifesto: str | None
    versao_corpus: str
    coletado_em: str | None
    inventario_completo: bool
    total_documentos: int
    total_itens: int
    total_falhas: int
    bases: list[ProjectBaseSummary]
