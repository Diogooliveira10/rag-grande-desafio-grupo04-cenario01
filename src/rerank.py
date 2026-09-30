"""Estágio ``rerank``: um cross-encoder reordena os candidatos da fusão RRF.

A busca densa compara vetores da pergunta e do chunk calculados separadamente,
e a BM25 só conta termos. O cross-encoder lê pergunta e chunk juntos e dá uma
nota de relevância para o par — mais caro, por isso roda só sobre os
``config.RERANK_CANDIDATES`` primeiros do RRF, e o ``retrieve`` corta o
resultado em ``top_k`` depois.

O modelo padrão (``config.RERANK_MODEL``) é um cross-encoder multilíngue
treinado no mMARCO (que inclui português) e roda em CPU. O modelo é carregado
na primeira chamada e fica em cache no processo; o download inicial vai para o
cache do Hugging Face. Quando o modelo de rerank da turma ganhar endpoint,
basta outra classe com a mesma assinatura ``(query, resultados) -> resultados``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from typing import Any, Callable, Optional, Sequence

import config
from src.retrieve import RetrievalResult

ScoreFn = Callable[[list[tuple[str, str]]], Sequence[float]]

_PAR_JSON_RE = re.compile(r'"(\w+)":\s*(?:"((?:[^"\\]|\\.)*)"|(-?\d+(?:\.\d+)?))')
# Campos que dizem do que o registro trata vêm primeiro; o resto segue na ordem original.
_CAMPOS_PRIORITARIOS = (
    "objeto", "titulo", "descricao", "razaosocial", "nomefornecedor", "nomeunidade", "nomerazaosocial",
    "modalidadenome", "valor", "municipio", "ufsigla", "situacao", "tipodocumentonome",
)


def texto_para_rerank(conteudo: str) -> str:
    """Texto que o cross-encoder lê para um chunk.

    Os chunks das bases estruturadas são JSON em ordem alfabética de chave: o
    ``objetoCompra`` de um contrato fica perto do token 480, e o modelo só lê
    ``RERANK_MAX_LENGTH`` tokens (pergunta incluída). Para JSON, os pares
    ``chave: valor`` saem com os campos de conteúdo primeiro, sem nulos, URLs e
    códigos; texto comum passa sem mudança.
    """
    if not conteudo.lstrip().startswith("{"):
        return conteudo
    pares = []
    for chave, texto, numero in _PAR_JSON_RE.findall(conteudo):
        valor = texto if texto else numero
        if not valor or valor.startswith("http"):
            continue
        pares.append((chave, valor.replace("\\n", " ").replace("\\t", " ")))
    if not pares:
        return conteudo

    def prioridade(item: tuple[int, tuple[str, str]]) -> tuple[int, int]:
        posicao, (chave, _valor) = item
        chave_normalizada = chave.lower()
        for indice, prefixo in enumerate(_CAMPOS_PRIORITARIOS):
            if chave_normalizada.startswith(prefixo):
                return (indice, posicao)
        return (len(_CAMPOS_PRIORITARIOS), posicao)

    ordenados = sorted(enumerate(pares), key=prioridade)
    return "; ".join(f"{chave}: {valor}" for _posicao, (chave, valor) in ordenados)


@lru_cache(maxsize=2)
def _carregar_cross_encoder(model_name: str, max_length: int) -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name, max_length=max_length)


class CrossEncoderReranker:
    """Reranker plugável no ``retrieve``: ``reranker(query, resultados)``.

    ``score_fn`` substitui o modelo (testes, ou outro backend); recebe a lista
    de pares ``(pergunta, texto)`` e devolve uma nota por par.
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        max_length: int | None = None,
        batch_size: int = 16,
        score_fn: Optional[ScoreFn] = None,
    ) -> None:
        self.modelo = model_name or config.RERANK_MODEL
        self.max_length = max_length or config.RERANK_MAX_LENGTH
        self.batch_size = batch_size
        self._score_fn = score_fn

    def _scores(self, pares: list[tuple[str, str]]) -> list[float]:
        if self._score_fn is not None:
            return [float(nota) for nota in self._score_fn(pares)]
        modelo = _carregar_cross_encoder(self.modelo, self.max_length)
        notas = modelo.predict(pares, batch_size=self.batch_size, show_progress_bar=False)
        return [float(nota) for nota in notas]

    def __call__(self, query: str, resultados: list[RetrievalResult]) -> list[RetrievalResult]:
        if not resultados:
            return []
        notas = self._scores([(query, texto_para_rerank(item.content)) for item in resultados])
        if len(notas) != len(resultados):
            raise ValueError(f"reranker devolveu {len(notas)} notas para {len(resultados)} chunks")
        # score passa a ser a nota do cross-encoder; rrf_score guarda a da fusão.
        reordenados = [
            replace(item, score=nota, rerank_score=nota) for item, nota in zip(resultados, notas)
        ]
        reordenados.sort(key=lambda item: (-item.score, item.chunk_id))
        return reordenados


def reranker_padrao() -> Optional[CrossEncoderReranker]:
    """Reranker usado pelo pipeline, ou ``None`` com ``RERANK_ENABLED=false``."""
    if not config.RERANK_ENABLED:
        return None
    return CrossEncoderReranker()


__all__ = ["CrossEncoderReranker", "reranker_padrao"]
