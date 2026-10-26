"""Índice invertido da BM25 de ``src/retrieve.py``: a mesma nota, sem percorrer todos os trechos.

A BM25 percorria cada trecho candidato e, nele, cada termo da pergunta: numa
pergunta de contrato, 106 mil trechos e ~1,7 s por chamada. Aqui cada termo
aponta para os textos que o contêm, com a contagem, e a nota é calculada só
sobre eles, em numpy.

A nota sai igual à da varredura, até o último dígito:

* a frequência de documento e o tamanho médio são os dos candidatos da pergunta
  (com os filtros já aplicados), como antes; um texto repetido em dois
  registros conta duas vezes;
* as operações de ponto flutuante são as mesmas, na mesma ordem, termo a termo
  da pergunta (termo repetido na pergunta soma duas vezes);
* o desempate (nota, depois ``chunk_id``) continua com quem chama.

Um candidato cujo texto não está no índice faz ``ids_dos_textos`` devolver
``None``: quem chama volta para a varredura.
"""

from __future__ import annotations

import math
from array import array
from collections import Counter
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

Tokenizador = Callable[[str], list[str]]


class IndiceLexico:
    """Termo -> (textos que o contêm, quantas vezes), em arrays contíguos (CSR).

    Na v7 são ~17 milhões de pares (termo, texto): ~140 MB, menos que um
    ``Counter`` por trecho, que é o que a varredura guardava.
    """

    def __init__(self, textos: Iterable[str], tokenizar: Tokenizador) -> None:
        posicao: dict[str, int] = {}
        vocabulario: dict[str, int] = {}
        termos, textos_dos_pares, contagens = array("i"), array("i"), array("i")
        comprimentos = array("q")
        for texto in textos:
            if texto in posicao:
                continue
            indice = len(posicao)
            posicao[texto] = indice
            tokens = tokenizar(texto)
            comprimentos.append(len(tokens))
            for termo, contagem in Counter(tokens).items():
                termos.append(vocabulario.setdefault(termo, len(vocabulario)))
                textos_dos_pares.append(indice)
                contagens.append(contagem)
        termos_np = np.frombuffer(termos, dtype=np.int32) if termos else np.zeros(0, dtype=np.int32)
        # Estável: dentro de cada termo, os textos ficam na ordem em que entraram.
        ordem = np.argsort(termos_np, kind="stable")
        self._textos = (np.frombuffer(textos_dos_pares, dtype=np.int32) if termos
                        else np.zeros(0, dtype=np.int32))[ordem]
        self._contagens = (np.frombuffer(contagens, dtype=np.int32) if termos
                           else np.zeros(0, dtype=np.int32))[ordem]
        self._inicio = np.concatenate(
            ([0], np.cumsum(np.bincount(termos_np, minlength=len(vocabulario))))).astype(np.int64)
        self._comprimentos = np.array(comprimentos, dtype=np.int64)
        self._posicao = posicao
        self._vocabulario = vocabulario

    def __len__(self) -> int:
        return len(self._posicao)

    def ids_dos_textos(self, textos: Sequence[str]) -> Optional[np.ndarray]:
        """Posição de cada texto no índice, na ordem dada; ``None`` se algum falta."""
        posicao = self._posicao
        ids = np.fromiter((posicao.get(texto, -1) for texto in textos), dtype=np.int64, count=len(textos))
        return None if len(ids) and int(ids.min()) < 0 else ids

    def notas(self, termos_da_pergunta: Sequence[str], ids: np.ndarray) -> np.ndarray:
        """Nota BM25 (k1 = 1,5; b = 0,75) de cada candidato, na ordem de ``ids``."""
        n = len(ids)
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        # int / int, como a soma dos tamanhos dividida pela quantidade na varredura.
        media = int(self._comprimentos[ids].sum()) / n
        divisor = media or 1
        multiplicidade = np.bincount(ids, minlength=len(self._posicao))
        notas_por_texto = np.zeros(len(self._posicao), dtype=np.float64)
        frequencias: dict[str, int] = {}
        for termo in termos_da_pergunta:
            id_termo = self._vocabulario.get(termo)
            if id_termo is None:
                continue
            inicio, fim = self._inicio[id_termo], self._inicio[id_termo + 1]
            textos = self._textos[inicio:fim]
            if termo not in frequencias:
                frequencias[termo] = int(multiplicidade[textos].sum())
            frequencia = frequencias[termo]
            if frequencia == 0:
                continue
            idf = math.log(1 + (n - frequencia + 0.5) / (frequencia + 0.5))
            contagem = self._contagens[inicio:fim].astype(np.float64)
            # Mesma expressão da varredura: tf + 1,5 * (1 - 0,75 + 0,75 * tamanho / média).
            denominador = contagem + 1.5 * ((1 - 0.75) + 0.75 * self._comprimentos[textos] / divisor)
            notas_por_texto[textos] += idf * contagem * 2.5 / denominador
        return notas_por_texto[ids]


__all__ = ["IndiceLexico"]
