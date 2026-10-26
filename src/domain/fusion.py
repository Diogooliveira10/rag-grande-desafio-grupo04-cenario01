"""Reciprocal Rank Fusion: combina rankings da busca densa e da BM25.

Cada item soma ``1 / (k + posição)`` em cada lista em que aparece. Quem está
bem colocado nas duas sobe; o valor absoluto das pontuações de cada busca não
importa, só a posição. A busca (src/retrieve.py) monta o resultado a partir
destas pontuações.
"""

from __future__ import annotations

from collections import Counter
from typing import Hashable, Sequence, TypeVar

Chave = TypeVar("Chave", bound=Hashable)


def pontuacoes_rrf(rankings: Sequence[Sequence[Chave]], k: int = 60) -> dict[Chave, float]:
    """Pontuação RRF de cada chave, na ordem em que apareceu pela primeira vez."""
    if k < 0:
        raise ValueError("k deve ser >= 0")
    pontuacoes: Counter[Chave] = Counter()
    for ranking in rankings:
        for posicao, chave in enumerate(ranking, start=1):
            pontuacoes[chave] += 1 / (k + posicao)
    return dict(pontuacoes)
