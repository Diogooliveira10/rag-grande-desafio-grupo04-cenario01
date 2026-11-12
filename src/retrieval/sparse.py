"""Busca lexical (BM25) sobre o recorte de registros da pergunta.

Separado de src/retrieve.py em 2026-11-12 sem mudar a lógica; o nome
continua importável de lá. A nota BM25 em si fica em ``lexical.py``; esta
função ficou fora de lá porque precisa do recorte (``selection``), e
``selection`` → ``filters`` → ``lexical`` já importam nesse sentido.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from src.retrieval.lexical import _bm25_pelo_indice, _bm25_por_varredura, _indice_lexico, _tokens
from src.retrieval.records import RetrievalResult, _read_jsonl, _result
from src.retrieval.selection import DEFAULT_TOP_K, _caminhos_ou_padrao, _records_for, _selecionar


def bm25_retrieve(
    query: str,
    *,
    index_dir: Path | None = None,
    documents_file: Path | None = None,
    top_k: int = DEFAULT_TOP_K,
    bases: Iterable[Any] | None = None,
    filters: Mapping[str, Any] | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
    escopos: Iterable[str] | None = None,
) -> list[RetrievalResult]:
    """Recupera chunks lexicalmente com BM25, sem dependências adicionais."""
    if top_k <= 0 or not query.strip():
        return []
    index_dir, documents_file = _caminhos_ou_padrao(index_dir, documents_file)
    candidates = (
        _records_for(index_dir, bases, filters, records, escopos)
        if records is not None
        else _selecionar(index_dir, bases, escopos, documents_file=documents_file) if not filters
        else _records_for(index_dir, bases, filters, _read_jsonl(documents_file), escopos)
    )
    documents = [(record, str(record.get("text", ""))) for record in candidates]
    query_terms = _tokens(query)
    if not query_terms or not documents:
        return []
    # Com registros recebidos (recorte já filtrado), usa o índice se ele já
    # existe; lendo o próprio documents.jsonl, monta-o na primeira vez.
    indice = _indice_lexico(documents_file, construir=records is None)
    melhores = _bm25_pelo_indice(indice, query_terms, documents, top_k) if indice is not None else None
    if melhores is None:
        melhores = _bm25_por_varredura(query_terms, documents, top_k)
    return [_result(record, score, text) for score, _, record, text in melhores]
