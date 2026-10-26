"""Portas do caso de uso "responder pergunta" (Clean Architecture).

Cada porta é o contrato que ``src/pipeline.py`` espera de uma peça de
infraestrutura: busca híbrida, consulta SQL, LLM, roteamento. O pipeline
depende destes contratos, e não dos módulos que os implementam; as
implementações padrão (``retrieve``, ``consultar_estruturado``,
``gerar_resposta``…) são montadas em ``pipeline.dependencias_padrao``.

Para trocar uma peça (um teste, outra busca, outro LLM), passe uma
``Dependencias`` com a sua implementação a ``pipeline.responder``. Basta ter a
mesma assinatura: ``Protocol`` não exige herança.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

from src.schema import (Chunk, Natureza, QueryAnalysisResult, QueryFilters, QueryIntent, RAGResponse,
                        RefusalReason, RouteDecision)


class Analisador(Protocol):
    """Extrai filtros e intenções da pergunta (``src/query_analysis.py``)."""
    def __call__(self, query: str, *, run_id: str, query_id: str) -> QueryAnalysisResult: ...


class Roteador(Protocol):
    """Escolhe as bases ou decide recusar (``src/router.py``)."""
    def __call__(self, query: str, *, run_id: str, query_id: str,
                 analise: Optional[QueryAnalysisResult]) -> RouteDecision: ...


class Buscador(Protocol):
    """Busca híbrida: densa + BM25 + RRF + rerank (``src/retrieve.py``).

    Devolve objetos com ``content`` e ``metadata`` (``RetrievalResult``).
    """
    def __call__(self, query: str, route: Any, *, reranker: Any, top_k: int,
                 filters: Optional[Mapping[str, Any]], escopos: Optional[Iterable[str]],
                 run_id: str, query_id: str) -> list[Any]: ...


class ConsultaEstruturada(Protocol):
    """Conta exata na base SQL (``src/structured_query.py``)."""
    def __call__(self, query: str, filtros: QueryFilters, bases: Iterable[Natureza], *,
                 run_id: str, query_id: str, intencao: Optional[QueryIntent] = None) -> list[Chunk]: ...


class Gerador(Protocol):
    """Resposta a partir das evidências, com validação (``src/generate.py``)."""
    def __call__(self, query: str, chunks: list[Chunk], bases_consultadas: list[Natureza], *,
                 cliente: Any, run_id: str, query_id: str,
                 refusal_reason_hint: Optional[RefusalReason]) -> RAGResponse: ...


class Conversa(Protocol):
    """Resposta fixa a cumprimento, ajuda ou injeção; ``None`` = é consulta."""
    def __call__(self, query: str, *, run_id: str, query_id: str) -> Optional[RAGResponse]: ...


class Reformulador(Protocol):
    """Reescreve continuações ("E em 2024?") com o histórico (``src/reformulation.py``)."""
    def __call__(self, query: str, historico: Optional[Sequence[Any]], *,
                 run_id: str, query_id: str) -> Any: ...
