from __future__ import annotations
from datetime import date
import math
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator, model_serializer


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
    # Falha de infraestrutura (provedor do LLM fora do ar, saída que não é JSON
    # válido): não é uma resposta sobre os dados, e o LLM nunca a escolhe.
    ERRO_PROVEDOR = "erro_provedor"


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

    model_config = {"extra": "forbid"}

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
    papel_orgao: Optional[Literal["orgao", "unidade"]] = None
    filtros_analiticos: dict[str, str] = Field(default_factory=dict)
    metrica: Optional[str] = None
    agrupamentos: list[str] = Field(default_factory=list)
    referencia_temporal: Optional[str] = None
    valor_min_exclusivo: bool = False
    valor_max_exclusivo: bool = False
    tipo_valor: Optional[Literal["contratado", "estimado", "homologado", "unitario"]] = None
    tipo_medida: Optional[Literal["global", "unitaria"]] = None
    granularidade: Optional[Literal["contrato", "item", "pca"]] = None
    papel_cnpj: Optional[Literal["orgao", "fornecedor"]] = None
    minimo_por_grupo: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def compatibilidade_valor_unitario(cls, value):
        """Converte o campo legado unitario para o novo contrato de medida."""
        if isinstance(value, dict) and value.get("tipo_valor") == "unitario":
            value = dict(value)
            value.setdefault("tipo_medida", "unitaria")
            value.setdefault("granularidade", "item")
        return value

    def intervalo_de_anos(self) -> tuple[Optional[int], Optional[int]]:
        """(primeiro ano, último ano), inclusivos; None = sem limite daquele lado."""
        if self.ano is not None:
            return self.ano, self.ano_final or self.ano
        return self.ano_desde, self.ano_ate

    def esta_vazio(self) -> bool:
        return not self.filtros_analiticos and all(
            valor is None
            for valor in (
                self.ano, self.ano_final, self.ano_desde, self.ano_ate,
                self.valor_min, self.valor_max, self.orgao, self.papel_orgao, self.papel_cnpj,
                self.tipo_medida, self.granularidade,
            )
        )


class QueryIntent(BaseModel):
    """Necessidade de evidência; não contém SQL nem uma resposta à pergunta."""

    model_config = {"extra": "forbid"}
    pergunta: str = Field(min_length=1)
    bases: list[Natureza] = Field(min_length=1)
    filtros: QueryFilters = Field(default_factory=QueryFilters)
    operacao: Literal["buscar", "ranking", "estatistica", "agrupamento", "evolucao"] = "buscar"
    termos_objeto: list[str] = Field(default_factory=list, max_length=12)
    limite: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validar_plano(self) -> "QueryIntent":
        if self.operacao == "buscar" and self.filtros.metrica is not None:
            raise ValueError("Métrica exige operação analítica")
        if self.operacao in {"estatistica", "agrupamento", "evolucao"} and self.filtros.metrica is None:
            raise ValueError("Operação analítica exige métrica explícita")
        if self.operacao == "agrupamento" and not self.filtros.agrupamentos:
            raise ValueError("Agrupamento exige campos explícitos")
        if self.filtros.metrica not in {None, "contagem", "soma", "media", "media_unitaria", "mediana", "minimo", "maximo"}:
            raise ValueError("Métrica não permitida")
        if self.filtros.papel_cnpj is not None and not self.filtros.filtros_analiticos.get("cnpj"):
            raise ValueError("Papel do CNPJ exige CNPJ explícito")
        if self.filtros.papel_orgao is not None and not self.filtros.orgao:
            raise ValueError("Papel da entidade exige órgão/unidade explícito")
        if self.filtros.tipo_medida == "unitaria" and self.filtros.granularidade != "item":
            raise ValueError("Medida unitária exige fatos no grão de item")
        if self.filtros.granularidade == "item" and self.filtros.tipo_medida != "unitaria":
            raise ValueError("Grão de item exige medida unitária")
        if self.filtros.referencia_temporal not in {None, "assinatura", "publicacao", "resultado", "pca", "vigencia", "propostas"}:
            raise ValueError("Referência temporal não permitida")
        agrupamentos_permitidos = {"esfera", "municipio", "uf", "modalidade", "fornecedor", "orgao_nome", "unidade_nome", "categoria", "classe", "mes_assinatura", "mes_publicacao"}
        filtros_permitidos = agrupamentos_permitidos | {"cnpj"}
        if (set(self.filtros.agrupamentos) - agrupamentos_permitidos
                or set(self.filtros.filtros_analiticos) - filtros_permitidos):
            raise ValueError("Campo analítico não permitido")
        if self.filtros.ano_final is not None and (self.filtros.ano is None or self.filtros.ano_final < self.filtros.ano):
            raise ValueError("Período inválido")
        if any(v is not None and not math.isfinite(v) for v in (self.filtros.valor_min, self.filtros.valor_max)):
            raise ValueError("Valor não finito")
        if any(v is not None and not 1900 <= v <= 2100 for v in (self.filtros.ano, self.filtros.ano_final, self.filtros.ano_desde, self.filtros.ano_ate)):
            raise ValueError("Ano fora do domínio")
        return self


class QueryAnalysisResult(BaseModel):
    """Saída da etapa query_analysis — filtros identificados na pergunta original."""

    query_original: str
    filtros: QueryFilters
    intencoes: list[QueryIntent] = Field(default_factory=list, max_length=4)
    metodo: str = "regras"


class SourceRef(BaseModel):
    chunk_id: str
    base_id: str
    source_file: str
    origem: Optional[str] = None

    @model_serializer(mode="wrap")
    def serializar(self, handler):
        resultado = handler(self)
        if self.origem is None:
            resultado.pop("origem", None)
        return resultado


class CampoCompletude(BaseModel):
    """Quantos registros do recorte têm o campo preenchido.

    Campo ausente não é zero: os ``desconhecidos`` ficam à parte e não entram
    como "não correspondentes".
    """

    model_config = {"extra": "forbid"}
    campo: str = Field(min_length=1)
    conhecidos: int = Field(ge=0)
    desconhecidos: int = Field(ge=0)


class TipoResultado(str, Enum):
    REGISTROS = "registros"
    SEM_CORRESPONDENCIA = "sem_correspondencia"
    SOMA_ZERO = "soma_zero"
    CALCULO_INDISPONIVEL = "calculo_indisponivel"
    # Comparação entre esferas pedida, mas menos de duas têm valor informativo.
    COMPARACAO_INSUFICIENTE = "comparacao_insuficiente"


class RecorteConsulta(BaseModel):
    """Condições da evidência estruturada, lidas do dado e não do texto gerado.

    Separa três dimensões que a resposta não deve misturar:

    - ``execucao``/``tipo_resultado``/``registros_correspondentes`` respondem
      pela **confiabilidade da afirmação no recorte**;
    - ``completude`` diz quais campos têm valor conhecido, com denominador;
    - ``cobertura`` declara que o snapshot é amostra, não censo do Estado.

    Zero correspondências pode ser uma conclusão confiável quando a execução é
    completa; isso é medido aqui, não deduzido do texto da resposta.
    """

    model_config = {"extra": "forbid"}
    tabela: str = Field(min_length=1)
    snapshot_versao: Optional[str] = None
    snapshot_hash: Optional[str] = None
    registros_no_snapshot: int = Field(ge=0)
    registros_correspondentes: int = Field(ge=0)
    execucao: Literal["completa", "parcial"] = "completa"
    filtros_relaxados: list[str] = Field(default_factory=list)
    calculo: str = Field(min_length=1)
    calculo_disponivel: bool = True
    calculo_motivo: Optional[str] = None
    tipo_resultado: TipoResultado = TipoResultado.REGISTROS
    completude: list[CampoCompletude] = Field(default_factory=list)
    cobertura: str = "amostra"

    @model_validator(mode="after")
    def _conferir(self) -> "RecorteConsulta":
        if self.registros_correspondentes > self.registros_no_snapshot:
            raise ValueError("mais correspondências que registros no snapshot")
        if self.tipo_resultado == TipoResultado.SEM_CORRESPONDENCIA and self.registros_correspondentes:
            raise ValueError("sem_correspondencia exige zero correspondências")
        if self.tipo_resultado == TipoResultado.CALCULO_INDISPONIVEL and self.calculo_disponivel:
            raise ValueError("calculo_indisponivel exige calculo_disponivel=False")
        if self.tipo_resultado != TipoResultado.CALCULO_INDISPONIVEL and not self.calculo_disponivel:
            raise ValueError("calculo_disponivel=False só com calculo_indisponivel")
        if self.execucao == "completa" and self.filtros_relaxados:
            raise ValueError("execução completa não pode ter filtros relaxados")
        for campo in self.completude:
            if campo.conhecidos > self.registros_correspondentes:
                raise ValueError(f"completude de {campo.campo} maior que as correspondências")
        return self


class RAGResponse(BaseModel):
    """Resposta final do pipeline."""

    query: str
    answer: str
    bases_consultadas: list[Natureza]
    sources_used: list[SourceRef] = Field(default_factory=list)
    confidence_level: ConfidenceLevel
    is_refusal: bool = False
    refusal_reason: Optional[RefusalReason] = None
    # Notas para o usuário sobre como a resposta foi obtida (ex.: busca
    # ampliada porque um filtro zerou o resultado). Ficam fora de ``answer``.
    avisos: list[str] = Field(default_factory=list)

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
