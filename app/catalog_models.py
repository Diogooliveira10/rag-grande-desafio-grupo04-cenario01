"""Contrato das telas de exploração; campos não publicados continuam nulos."""

from pydantic import BaseModel


class ContratacaoRecord(BaseModel):
    id: str
    numero_contrato: str
    orgao: str | None = None
    orgao_cnpj: str | None = None
    fornecedor: str | None = None
    objeto: str | None = None
    valor_global: float | None = None
    data_homologacao: str | None = None
    modalidade: str | None = None
    fonte_oficial: str
    status: str | None = None
    esfera: str | None = None
    municipio: str | None = None


class DocumentoRecord(BaseModel):
    id: str
    titulo: str
    tipo: str
    orgao: str | None = None
    ano: int | None = None
    fonte: str | None = None
    descricao: str | None = None
    link: str | None = None
    base_id: str
    natureza: str
    formato: str | None = None


class OrgaoRecord(BaseModel):
    cnpj: str | None = None
    sigla: str | None = None
    nome: str
    esfera: str | None = None
    municipio: str | None = None
    total_contratacoes: int = 0
    valor_total: float | None = None
    principais_categorias: list[str] = []
    principais_fornecedores: list[str] = []


class OfficialSource(BaseModel):
    id: str
    nome: str
    sigla: str | None = None
    instituicao: str | None = None
    tipo_informacao: str | None = None
    url: str | None = None
    natureza: str
    frequencia_coleta: str | None = None
    descricao: str | None = None
    escopo: str
    formatos: list[str]


class ProjectBaseSummary(BaseModel):
    base_id: str
    natureza: str
    escopo: str
    status: str | None = None
    documentos: int = 0
    formatos: dict[str, int] = {}


class ProjectSummary(BaseModel):
    projeto: str
    scenario_id: str
    descricao: str | None = None
    versao_manifesto: str | None = None
    versao_corpus: str
    coletado_em: str | None = None
    inventario_completo: bool
    total_documentos: int
    total_itens: int
    total_falhas: int
    bases: list[ProjectBaseSummary]
