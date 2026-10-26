"""Reciprocal Rank Fusion: combina rankings da busca densa e da BM25.

Cada item soma ``1 / (k + posição)`` em cada lista em que aparece. Quem está
bem colocado nas duas sobe; o valor absoluto das pontuações de cada busca não
importa, só a posição. A busca (src/retrieve.py) monta o resultado a partir
destas pontuações.

``limitar_repeticoes`` dá variedade ao resultado: quando vários trechos vêm da
mesma compra, só os primeiros ficam à frente e os demais passam para o fim.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable, Hashable, Optional, Sequence, TypeVar

Chave = TypeVar("Chave", bound=Hashable)
Item = TypeVar("Item")


def pontuacoes_rrf(rankings: Sequence[Sequence[Chave]], k: int = 60) -> dict[Chave, float]:
    """Pontuação RRF de cada chave, na ordem em que apareceu pela primeira vez."""
    if k < 0:
        raise ValueError("k deve ser >= 0")
    pontuacoes: Counter[Chave] = Counter()
    for ranking in rankings:
        for posicao, chave in enumerate(ranking, start=1):
            pontuacoes[chave] += 1 / (k + posicao)
    return dict(pontuacoes)


def limitar_repeticoes(
    itens: Sequence[Item],
    chave: Callable[[Item], Optional[Hashable]],
    maximo: int,
) -> tuple[list[Item], int]:
    """Mantém a ordem, mas o que passa de ``maximo`` itens da mesma chave vai para o fim.

    Ninguém é descartado: os excedentes continuam na lista, na ordem original,
    depois de todos os que ficaram. Assim uma compra com muitos trechos não
    ocupa sozinha as primeiras posições, e o que faltar para completar o corte
    ainda é preenchido por ela. ``chave`` devolvendo ``None`` deixa o item sem
    limite; ``maximo <= 0`` desliga a regra. Devolve a lista e quantos itens
    foram rebaixados.
    """
    if maximo <= 0:
        return list(itens), 0
    contagem: Counter[Hashable] = Counter()
    na_frente: list[Item] = []
    excedentes: list[Item] = []
    for item in itens:
        grupo = chave(item)
        if grupo is None:
            na_frente.append(item)
            continue
        contagem[grupo] += 1
        (na_frente if contagem[grupo] <= maximo else excedentes).append(item)
    return na_frente + excedentes, len(excedentes)
