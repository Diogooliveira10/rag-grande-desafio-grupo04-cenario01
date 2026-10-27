"""Registros da busca: leitura dos JSONL em cache, metadados compartilhados com o documento e o resultado.

Separado de src/retrieve.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

import config
from src.schema import Natureza


MANIFEST_PATH = config.ROOT_DIR / "corpus_manifest.yaml"


_NATUREZAS = frozenset(item.value for item in Natureza)


class EmbeddingUnavailable(ConnectionError):
    """Endpoint em cooldown ou busca densa desabilitada explicitamente."""


@dataclass
class RetrievalResult:
    """Resultado de uma estratégia ou da fusão, com proveniência preservada."""

    chunk_id: str
    content: str
    metadata: dict[str, Any]
    score: float
    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    document_id: str | None = None
    source_path: str | None = None
    _record: dict[str, Any] = field(default_factory=dict, repr=False)

    def __getitem__(self, key: str) -> Any:
        """Permite consumo como mapeamento sem perder o acesso tipado."""
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "metadata": self.metadata,
            "score": self.score,
            "dense_score": self.dense_score,
            "sparse_score": self.sparse_score,
            "rrf_score": self.rrf_score,
            "rerank_score": self.rerank_score,
            "document_id": self.document_id,
            "source_path": self.source_path,
        }


# Arquivo lido -> (mtime, tamanho, registros). Cada pergunta lia de novo os
# vectors.jsonl e o documents.jsonl (~150 MB de JSON), uns 3 s por consulta;
# a assinatura do arquivo invalida a entrada quando ele é regravado.
_JSONL_CACHE: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}


_RE_EMBEDDING = re.compile(r'"embedding":\s*\[[^\]]*\]\s*,?\s*')


def _read_jsonl(path: Path, *, sem_vetor: bool = False) -> list[dict[str, Any]]:
    """Registros do arquivo, lidos uma vez enquanto ele não muda.

    Os dicts são compartilhados entre as chamadas e ninguém os altera: a busca
    só lê, e o que sai dela (``_result``) leva cópia própria. Copiar os ~144 mil
    registros a cada pergunta custava ~0,75 s por seleção. Quem precisar
    alterar um registro copia antes (``dict(record)``).

    ``sem_vetor`` descarta o campo ``embedding`` antes de decodificar a linha:
    com o FAISS ao lado, o vetor não é usado na consulta. Na v7, os vetores dos
    contratos passam de ~4 GB e 3 min para ~700 MB e 12 s.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    chave = f"{path}|sem_vetor" if sem_vetor else str(path)
    guardado = _JSONL_CACHE.get(chave)
    if guardado and guardado[0] == stat.st_mtime_ns and guardado[1] == stat.st_size:
        return list(guardado[2])
    records: list[dict[str, Any]] = []
    documentos = _documentos_por_id() if sem_vetor else None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = _registro_sem_vetor(line) if sem_vetor else json.loads(line)
                if documentos:
                    _compartilhar_metadado(record, documentos)
                records.append(record)
    _JSONL_CACHE[chave] = (stat.st_mtime_ns, stat.st_size, records)
    return list(records)


class _MetadadoDoDocumento(Mapping):
    """Metadados de um registro do índice, lidos do registro do documents.jsonl.

    Os 18 campos do metadado do índice são idênticos aos do documento; guardar a
    cópia custava ~930 MB nos 144 mil registros (os dicionários pesam mais que o
    JSON). A visão mostra só as chaves do metadado original, na mesma ordem, e só
    lê: quem precisa de um ``dict`` copia (``_result`` já faz ``dict(...)``).
    """

    __slots__ = ("_documento", "_chaves", "_conjunto")

    def __init__(self, documento: Mapping[str, Any], chaves: tuple[str, ...], conjunto: frozenset[str]) -> None:
        self._documento = documento
        self._chaves = chaves
        self._conjunto = conjunto

    def __getitem__(self, chave: str) -> Any:
        if chave in self._conjunto:
            return self._documento[chave]
        raise KeyError(chave)

    # Os filtros perguntam por chaves ausentes milhões de vezes por pergunta:
    # o get/in herdados do Mapping passam por KeyError, bem mais lento.
    def get(self, chave: str, padrao: Any = None) -> Any:
        return self._documento[chave] if chave in self._conjunto else padrao

    def __contains__(self, chave: object) -> bool:
        return chave in self._conjunto

    def __iter__(self):
        return iter(self._chaves)

    def __len__(self) -> int:
        return len(self._chaves)


# Chaves (em ordem e como conjunto) compartilhadas entre registros com o mesmo metadado.
_CHAVES_DE_METADADO: dict[tuple[str, ...], tuple[tuple[str, ...], frozenset[str]]] = {}


# (assinatura do documents.jsonl em cache, chunk_id -> registro).
_DOCUMENTOS_POR_ID: list[Any] = [None, None]


def _documentos_por_id() -> dict[str, Mapping[str, Any]] | None:
    """Registros do documents.jsonl por ``chunk_id``, se ele já está em cache.

    Não lê o arquivo: a pré-carga lê os documentos antes do índice; fora dela,
    sem documentos carregados, o índice fica com os próprios metadados.
    """
    guardado = _JSONL_CACHE.get(str(config.NORMALIZED_DATA_DIR / "documents.jsonl"))
    if not guardado:
        return None
    if _DOCUMENTOS_POR_ID[0] != guardado[:2]:
        _DOCUMENTOS_POR_ID[:] = [guardado[:2], {str(d.get("chunk_id")): d for d in guardado[2]}]
    return _DOCUMENTOS_POR_ID[1]


def _compartilhar_metadado(record: dict[str, Any], documentos: Mapping[str, Mapping[str, Any]]) -> None:
    """Troca o metadado do registro pela visão do documento, só se todos os campos são iguais."""
    metadado = record.get("metadata")
    documento = documentos.get(str(record.get("chunk_id")))
    if not isinstance(metadado, dict) or documento is None:
        return
    if all(chave in documento and documento[chave] == valor for chave, valor in metadado.items()):
        chaves = tuple(metadado)
        record["metadata"] = _MetadadoDoDocumento(
            documento, *_CHAVES_DE_METADADO.setdefault(chaves, (chaves, frozenset(chaves))))


def _registro_sem_vetor(line: str) -> dict[str, Any]:
    """A linha sem o campo ``embedding``, cortado antes de decodificar (o caro é o vetor).

    O corte pressupõe que o campo não é o último da linha (nos índices da v7 é o
    4º de 8); se for, sobra uma vírgula e o JSON fica inválido: aí a linha é lida
    inteira e o campo sai depois.
    """
    try:
        return json.loads(_RE_EMBEDDING.sub("", line, count=1))
    except json.JSONDecodeError:
        registro = json.loads(line)
        registro.pop("embedding", None)
        return registro


def _registros_do_indice(vectors_path: Path) -> list[dict[str, Any]]:
    """Registros de uma base do índice, na ordem do FAISS.

    Com ``vectors.faiss`` ao lado, os vetores ficam no FAISS e o JSON é lido
    sem eles; sem o FAISS, o vetor é necessário para montar o índice em memória.
    """
    return _read_jsonl(vectors_path, sem_vetor=(vectors_path.parent / "vectors.faiss").exists())


@lru_cache(maxsize=4)
def _manifest_bases(manifest_path: Path = MANIFEST_PATH) -> dict[str, dict[str, Any]]:
    """``base_id`` -> declaração da base no manifesto (natureza, escopo...)."""
    if not manifest_path.exists():
        return {}
    from src.fetch_corpus import load_manifest

    return {str(base["base_id"]): base for base in load_manifest(manifest_path)["bases"]}


def _split_selection(bases: Iterable[Any] | None) -> tuple[set[str], set[str]] | None:
    """Separa a seleção do roteador em (naturezas, base_ids explícitos).

    Um item que é nome de natureza (``Natureza`` ou "estruturada") seleciona
    todas as bases daquela natureza; qualquer outro valor é tratado como
    ``base_id``.
    """
    if bases is None:
        return None
    if isinstance(bases, str):
        bases = [bases]
    naturezas: set[str] = set()
    base_ids: set[str] = set()
    for base in bases:
        value = str(getattr(base, "value", base))
        (naturezas if value in _NATUREZAS else base_ids).add(value)
    return naturezas, base_ids


def _record_base_id(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    return str(record.get("base_id") or metadata.get("base_id") or "")


def _record_natureza(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    """Natureza do registro; sem o campo, vem do manifesto pelo ``base_id``."""
    natureza = record.get("natureza") or metadata.get("natureza")
    if natureza:
        return str(getattr(natureza, "value", natureza))
    declarada = _manifest_bases().get(_record_base_id(record, metadata), {}).get("natureza")
    return str(declarada) if declarada else None


def _record_escopo(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    escopo = _manifest_bases().get(_record_base_id(record, metadata), {}).get("escopo")
    return str(escopo) if escopo else None


def _metadata_of(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Metadados do registro: o índice guarda em ``metadata``; o
    ``documents.jsonl`` de ``prepare_documents.py`` guarda no próprio registro."""
    metadata = record.get("metadata")
    return metadata if isinstance(metadata, Mapping) else record


def _result(record: Mapping[str, Any], score: float, content: str = "") -> RetrievalResult:
    metadata = dict(_metadata_of(record))
    metadata.pop("text", None)
    metadata.pop("embedding", None)
    return RetrievalResult(
        chunk_id=str(record.get("chunk_id") or metadata.get("chunk_id", "")),
        content=content or str(record.get("text", "")),
        metadata=metadata,
        score=score,
        document_id=record.get("document_id"),
        source_path=record.get("source_path"),
        _record=dict(record),
    )
