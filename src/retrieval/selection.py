"""Recorte dos registros que uma busca pode ver: bases, escopos e filtros.

Separado de src/retrieve.py em 2026-11-12 sem mudar a lógica; os nomes
continuam importáveis de lá. Quem troca ``_records_for`` num teste troca
neste módulo: é aqui que ``_selecionar``, a busca densa e a BM25 o procuram.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import config
from src.retrieval.filters import _matches_filters, _metadata_value
from src.retrieval.records import (
    _manifest_bases,
    _metadata_of,
    _read_jsonl,
    _record_base_id,
    _record_escopo,
    _record_natureza,
    _registros_do_indice,
    _split_selection,
)


DEFAULT_TOP_K = 10


def _records_for(
    index_dir: Path,
    bases: Iterable[Any] | None,
    filters: Mapping[str, Any] | None,
    records: Iterable[Mapping[str, Any]] | None = None,
    escopos: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    selection = _split_selection(bases)
    allowed_scopes = set(escopos) if escopos is not None else None
    if records is None:
        paths = sorted(index_dir.glob("*/vectors.jsonl"))
        declarations = _manifest_bases()
        paths = [path for path in paths if not declarations.get(path.parent.name) or (
            (selection is None or path.parent.name in selection[1]
             or declarations[path.parent.name].get("natureza") in selection[0])
            and (allowed_scopes is None
                 or declarations[path.parent.name].get("escopo", "oficial") in allowed_scopes)
        )]
        raw = [record for path in paths for record in _registros_do_indice(path)]
    else:
        raw = records
    filtered: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in raw:
        metadata = _metadata_of(record)
        chunk_id = str(record.get("chunk_id") or metadata.get("chunk_id") or "")
        if not chunk_id or chunk_id in seen_ids or not _matches_filters(metadata, filters, content=str(record.get("text", ""))):
            continue
        # Texto ilegível (src/corpus_quality.py) não é evidência: já fica fora
        # do índice denso, e o BM25 lê o documents.jsonl, onde ele continua.
        if _metadata_value(metadata, "status_extracao") == "texto_ilegivel":
            continue
        if selection is not None:
            naturezas, base_ids = selection
            if (
                _record_natureza(record, metadata) not in naturezas
                and _record_base_id(record, metadata) not in base_ids
            ):
                continue
        if allowed_scopes is not None and _record_escopo(record, metadata) not in allowed_scopes:
            continue
        seen_ids.add(chunk_id)
        filtered.append(record)
    return filtered


# Seleção sem filtro (bases e escopos) por versão dos arquivos lidos. Uma pergunta
# percorria os ~144 mil registros 3 ou 4 vezes (busca densa, BM25, identificadores,
# base dos filtros), ~0,2 s cada, para chegar sempre à mesma lista.
_SELECAO_CACHE: dict[tuple[Any, ...], list[dict[str, Any]]] = {}


def _assinatura_da_origem(index_dir: Path, documents_file: Path | None) -> tuple[Any, ...] | None:
    """Muda quando muda o arquivo de onde a seleção sai; ``None`` se ele não existe."""
    try:
        if documents_file is not None:
            stat = Path(documents_file).stat()
            return ("documents", str(documents_file), stat.st_mtime_ns, stat.st_size)
        vetores = sorted(Path(index_dir).glob("*/vectors.jsonl"))
        return ("indice", str(index_dir), tuple(
            (str(v), v.stat().st_mtime_ns, v.stat().st_size, (v.parent / "vectors.faiss").exists()) for v in vetores))
    except OSError:
        return None


def _selecionar(index_dir: Path, bases: Iterable[Any] | None, escopos: Iterable[str] | None,
                *, documents_file: Path | None = None) -> list[dict[str, Any]]:
    """``_records_for`` sem filtros, do índice ou do ``documents.jsonl``, calculado
    uma vez por versão dos arquivos. Devolve cópia da lista; os registros são os
    mesmos dicts compartilhados de sempre (ninguém os altera)."""
    origem = _assinatura_da_origem(index_dir, documents_file)
    fonte = _read_jsonl(documents_file) if documents_file is not None else None
    if origem is None:
        return _records_for(index_dir, bases, None, fonte, escopos)
    selecao = _split_selection(bases)
    chave = (origem,
             None if selecao is None else (tuple(sorted(selecao[0])), tuple(sorted(selecao[1]))),
             None if escopos is None else tuple(sorted(set(escopos))))
    guardado = _SELECAO_CACHE.get(chave)
    if guardado is None:
        # Versão anterior do mesmo arquivo não fica presa na memória.
        for antiga in [c for c in _SELECAO_CACHE if c[0][:2] == origem[:2] and c[0] != origem]:
            del _SELECAO_CACHE[antiga]
        guardado = _records_for(index_dir, bases, None, fonte, escopos)
        _SELECAO_CACHE[chave] = guardado
    return list(guardado)


def _caminhos_ou_padrao(index_dir: Path | None, documents_file: Path | None) -> tuple[Path, Path]:
    """Os caminhos pedidos ou, na falta deles, os da configuração atual (lidos na chamada)."""
    caminhos = config.atual().caminhos
    return (index_dir if index_dir is not None else caminhos.index_dir,
            documents_file if documents_file is not None else caminhos.normalized_data_dir / "documents.jsonl")
