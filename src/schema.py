from __future__ import annotations
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class Natureza(str, Enum):
    NORMATIVA = "normativa"
    ESTRUTURADA = "estruturada"
    CONVERSACIONAL = "conversacional"
    AGREGADA = "agregada"


class Sensibilidade(str, Enum):
    PUBLICO = "publico"
    INTERNO = "interno"
    RESTRITO = "restrito"


class ConfidenceLevel(str, Enum):
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"
    RECUSADO = "recusado"


class RefusalReason(str, Enum):
    DADOS_BANCARIOS = "dados_bancarios_fornecedor"
    CREDENCIAIS_VAZADAS = "credenciais_vazadas"
    JUIZO_LEGALIDADE = "juizo_legalidade_contrato"
    FORA_DE_ESCOPO = "fora_de_escopo"
    SEM_EVIDENCIA = "sem_evidencia"


class ChunkMetadata(BaseModel):
    """Metadado obrigatório de todo chunk indexado."""

    source_file: str
    chunk_id: str
    base_id: str
    doc_type: str
    document_id: Optional[str] = None
    parent_chunk_id: Optional[str] = None
    natureza: Optional[Natureza] = None
    sensitivity: Sensibilidade = Sensibilidade.PUBLICO
    data_vigencia: Optional[date] = None
    origem: Optional[str] = None
    coletado_em: Optional[str] = None
    manifest_hash: Optional[str] = None
    sha256: Optional[str] = None
    document_content_sha256: Optional[str] = None
    chunk_content_sha256: Optional[str] = None
    chunking_strategy: Optional[str] = None
    inventory_version: Optional[str] = None
    inventory_hash: Optional[str] = None
    extras: dict = Field(default_factory=dict)  # orgao, modalidade, valor, municipio...


class Chunk(BaseModel):
    content: str
    metadata: ChunkMetadata


class RouteDecision(BaseModel):
    """Saída do router.py — quais bases consultar e por quê."""

    bases_selecionadas: list[Natureza]
    justificativa: str
    confidence: float = Field(ge=0.0, le=1.0)


class QueryFilters(BaseModel):
    """Filtros estruturados extraídos da pergunta pela etapa query_analysis."""

    # Ano da pergunta ("em 2025") ou primeiro ano de um período fechado.
    ano: Optional[int] = None
    # Último ano de um período fechado ("entre 2024 e 2025"); None = só ``ano``.
    ano_final: Optional[int] = None
    # Períodos abertos, usados só quando ``ano`` é None: "a partir de 2024" /
    # "depois de 2023" -> ano_desde; "até 2025" / "antes de 2026" -> ano_ate.
    ano_desde: Optional[int] = None
    ano_ate: Optional[int] = None
    valor_min: Optional[float] = None
    valor_max: Optional[float] = None
    orgao: Optional[str] = None
    filtros_analiticos: dict[str, str] = Field(default_factory=dict)
    metrica: Optional[str] = None
    agrupamentos: list[str] = Field(default_factory=list)
    referencia_temporal: Optional[str] = None

    def intervalo_de_anos(self) -> tuple[Optional[int], Optional[int]]:
        """(primeiro ano, último ano), inclusivos; None = sem limite daquele lado."""
        if self.ano is not None:
            return self.ano, self.ano_final or self.ano
        return self.ano_desde, self.ano_ate

    def esta_vazio(self) -> bool:
        return all(
            valor is None
            for valor in (
                self.ano, self.ano_final, self.ano_desde, self.ano_ate,
                self.valor_min, self.valor_max, self.orgao,
            )
        )


class QueryAnalysisResult(BaseModel):
    """Saída da etapa query_analysis — filtros identificados na pergunta original."""

    query_original: str
    filtros: QueryFilters


class SourceRef(BaseModel):
    chunk_id: str
    base_id: str
    source_file: str


class RAGResponse(BaseModel):
    """Resposta final do pipeline."""

    query: str
    answer: str
    bases_consultadas: list[Natureza]
    sources_used: list[SourceRef] = Field(default_factory=list)
    confidence_level: ConfidenceLevel
    is_refusal: bool = False
    refusal_reason: Optional[RefusalReason] = None

    @model_validator(mode="after")
    def validar_consistencia_recusa(self) -> "RAGResponse":
        if self.is_refusal:
            if self.sources_used:
                raise ValueError("Recusa não pode citar fontes (sources_used deve estar vazio).")
            if self.confidence_level != ConfidenceLevel.RECUSADO:
                raise ValueError("Recusa exige confidence_level='recusado'.")
            if self.refusal_reason is None:
                raise ValueError("Recusa exige refusal_reason preenchido.")
        elif self.confidence_level == ConfidenceLevel.RECUSADO:
            raise ValueError("confidence_level='recusado' só é válido quando is_refusal=True.")
        return self
