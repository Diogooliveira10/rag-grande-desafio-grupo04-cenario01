"""Nota BM25 da busca lexical: varredura trecho a trecho (referência) e índice invertido.

Separado de src/retrieve.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import heapq
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from src.retrieval.records import _metadata_of, _read_jsonl


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    """Tokens em minúsculas e sem acento: "médico" casa com "MEDICO" do catálogo."""
    sem_acento = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    return [token.lower() for token in _TOKEN_RE.findall(sem_acento)]


# Texto do chunk -> (contagem dos termos, número de termos). O BM25 tokenizava
# todos os candidatos a cada pergunta: com os 106.667 contratos da v7, ~2 min.
# A chave é o próprio texto, que vem dos registros já guardados em cache.
_TERMOS_CACHE: dict[str, tuple[Counter[str], int]] = {}


def _termos(text: str) -> tuple[Counter[str], int]:
    guardado = _TERMOS_CACHE.get(text)
    if guardado is None:
        tokens = _tokens(text)
        guardado = (Counter(tokens), len(tokens))
        _TERMOS_CACHE[text] = guardado
    return guardado


_Pontuado = tuple[float, str, Mapping[str, Any], str]


def _chunk_id_do_registro(record: Mapping[str, Any]) -> str:
    return str(record.get("chunk_id") or _metadata_of(record).get("chunk_id", ""))


def _bm25_por_varredura(query_terms: list[str], documents: list[tuple[Mapping[str, Any], str]],
                        top_k: int) -> list[_Pontuado]:
    """BM25 trecho a trecho: a referência do índice e o caminho sem ele."""
    tokenized = [(record, text, *_termos(text)) for record, text in documents]
    average_length = sum(length for _, _, _, length in tokenized) / len(tokenized)
    # Só os termos da pergunta entram na conta: a frequência é a mesma que a
    # contagem sobre todos os termos daria para eles.
    document_frequency = {term: sum(1 for _, _, counts, _ in tokenized if term in counts)
                          for term in set(query_terms)}
    scored: list[_Pontuado] = []
    for record, text, counts, length in tokenized:
        score = 0.0
        for term in query_terms:
            if term not in counts:
                continue
            idf = math.log(1 + (len(tokenized) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = counts[term] + 1.5 * (1 - 0.75 + 0.75 * length / (average_length or 1))
            score += idf * counts[term] * 2.5 / denominator
        if score > 0:
            scored.append((score, _chunk_id_do_registro(record), record, text))
    # Só os top_k viram RetrievalResult: com a v7, quase todos os 106 mil
    # contratos pontuam e montar o resultado de cada um custava ~3 s.
    return heapq.nsmallest(top_k, scored, key=lambda item: (-item[0], item[1]))


def _bm25_pelo_indice(indice: Any, query_terms: list[str], documents: list[tuple[Mapping[str, Any], str]],
                      top_k: int) -> list[_Pontuado] | None:
    """A mesma BM25 pelo índice invertido; ``None`` se algum texto não está nele."""
    import numpy as np

    ids = indice.ids_dos_textos([text for _, text in documents])
    if ids is None:
        return None
    notas = indice.notas(query_terms, ids)
    positivos = np.flatnonzero(notas > 0)
    if len(positivos) > top_k:
        # Só quem pode entrar no top_k vira tupla: os empatados no corte entram
        # todos, e o desempate por chunk_id decide, como na varredura.
        corte = np.partition(notas[positivos], len(positivos) - top_k)[len(positivos) - top_k]
        positivos = positivos[notas[positivos] >= corte]
    escolhidos = [(float(notas[i]), _chunk_id_do_registro(documents[i][0]), documents[i][0], documents[i][1])
                  for i in positivos]
    return heapq.nsmallest(top_k, escolhidos, key=lambda item: (-item[0], item[1]))


# documents.jsonl -> (mtime, tamanho, índice), refeito quando o arquivo muda.
_INDICE_LEXICO_CACHE: dict[str, tuple[int, int, Any]] = {}


def _indice_lexico(documents_file: Path, *, construir: bool) -> Any:
    """Índice invertido dos textos do ``documents.jsonl``; ``None`` sem o arquivo
    ou, com ``construir=False``, enquanto ninguém o montou."""
    try:
        stat = Path(documents_file).stat()
    except OSError:
        return None
    chave = str(documents_file)
    guardado = _INDICE_LEXICO_CACHE.get(chave)
    if guardado and guardado[0] == stat.st_mtime_ns and guardado[1] == stat.st_size:
        return guardado[2]
    if not construir:
        return None
    from src.lexical_index import IndiceLexico

    indice = IndiceLexico((str(record.get("text", "")) for record in _read_jsonl(Path(documents_file))), _tokens)
    _INDICE_LEXICO_CACHE[chave] = (stat.st_mtime_ns, stat.st_size, indice)
    return indice
