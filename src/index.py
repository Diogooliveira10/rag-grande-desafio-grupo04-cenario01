r"""Gera embeddings e constrói o índice vetorial por base.

Uso:
    py .\src\index.py
    py .\src\index.py --input data/normalized/documents.jsonl --output index
    py .\src\index.py --dry-run

Lê o JSONL normalizado (saída de ``ingest.py``), agrupa os chunks por
``base_id`` e grava, para cada base, um índice FAISS
(``index/<base_id>/vectors.faiss``), os vetores e metadados em JSONL
(``index/<base_id>/vectors.jsonl``) e um manifesto
(``index/<base_id>/manifest.json``) com o modelo usado, a dimensão dos
vetores e a proveniência da fonte. Chunks sem texto (ex.: PDFs ainda
pendentes de extração) e chunks marcados como ``texto_ilegivel`` por
``src/corpus_quality.py`` são pulados e contados à parte.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from src.obs import Stage
from src.offline_observation import observar_offline, observar_lote_embedding

EmbedFn = Callable[[list[str]], list[list[float]]]

DEFAULT_INPUT = config.NORMALIZED_DATA_DIR / "documents.jsonl"
DEFAULT_OUTPUT_DIR = config.INDEX_DIR
DEFAULT_BATCH_SIZE = 64

# Campos obrigatórios de ChunkMetadata (src/schema.py); "extras" e
# "data_vigencia" são opcionais e não entram nessa checagem.
REQUIRED_METADATA_FIELDS = {"source_file", "chunk_id", "base_id", "doc_type", "sensitivity"}


def _write_faiss_index(path: Path, embeddings: list[list[float]]) -> None:
    """Persiste busca por produto interno após normalizar os vetores para cosseno."""
    import faiss
    import numpy as np

    # Evitar uma segunda matriz contígua do corpus inteiro em RAM.
    index = faiss.IndexFlatIP(len(embeddings[0]))
    for inicio in range(0, len(embeddings), 1024):
        vectors = np.ascontiguousarray(embeddings[inicio:inicio + 1024], dtype=np.float32)
        faiss.normalize_L2(vectors)
        index.add(vectors)
    faiss.write_index(index, str(path))


@dataclass(frozen=True)
class NormalizedChunk:
    """Um registro do JSONL normalizado, pronto para embedding."""

    chunk_id: str
    document_id: str
    base_id: str
    text: str
    source_file: str
    sha256: str
    metadata: dict[str, Any]


def _chunk_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Monta o metadado no formato de ChunkMetadata (src/schema.py).

    src/prepare_documents.py grava um contrato plano (sem ``"metadata"``
    aninhado): ``doc_type`` é o formato do arquivo (json/pdf/html) e
    ``natureza`` é a classificação Adaptive RAG — ambos viram campos de
    ChunkMetadata diretamente.
    """
    if "metadata" in record:  # compat: formato antigo, já aninhado
        return record["metadata"]
    metadata = {
        "source_file": record.get("source_file", ""),
        "chunk_id": record["chunk_id"],
        "base_id": record["base_id"],
        "doc_type": record.get("doc_type", record.get("formato", "")),
        "natureza": record.get("natureza"),
        "sensitivity": record.get("sensitivity", "publico"),
        "data_vigencia": record.get("data_vigencia"),
        "extras": record.get("extras", {}),
    }
    for key in (
        "document_id", "parent_chunk_id", "origem", "coletado_em", "manifest_hash",
        "sha256", "document_content_sha256", "chunk_content_sha256", "chunking_strategy",
        "inventory_version", "inventory_hash", "version", "relations", "status_extracao",
    ):
        if key in record:
            metadata[key] = record[key]
    return metadata


def read_normalized_chunks(input_file: Path) -> Iterator[NormalizedChunk]:
    with input_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            yield NormalizedChunk(
                chunk_id=record["chunk_id"],
                document_id=record["document_id"],
                base_id=record["base_id"],
                text=record.get("text", ""),
                source_file=record.get("source_file", record.get("source_path", "")),
                sha256=record.get("sha256", ""),
                metadata=_chunk_metadata(record),
            )


def _texto_ilegivel(chunk: NormalizedChunk) -> bool:
    """Glifos sem mapa de caracteres, sem palavras (``src/corpus_quality.py``)."""
    return chunk.metadata.get("status_extracao") == "texto_ilegivel"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def batched(items: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _default_embed_fn(model: str) -> EmbedFn:
    """Cliente OpenAI-compatível apontando para o servidor Ollama da turma."""
    from openai import OpenAI

    client = OpenAI(base_url=config.OLLAMA_URL, api_key=config.OLLAMA_API_KEY)

    def embed(texts: list[str]) -> list[list[float]]:
        from src.model_observation import observar_chamada
        response = observar_chamada(lambda: client.embeddings.create(model=model, input=texts),
            provider="ecoa", model=model, sdk_max_retries=getattr(client, "max_retries", None))
        return [item.embedding for item in response.data]

    return embed


def stored_embed_fn(embeddings_file: Path) -> tuple[EmbedFn, str]:
    """Reaproveita vetores já publicados (ex.: ``data/embeddings/embeddings.jsonl``).

    Cada texto é localizado pelo SHA-256 do conteúdo (``content_sha256``), então
    só é aceito um chunk cujo texto seja idêntico ao que gerou o vetor. Devolve
    a função e o modelo registrado nos vetores.
    """
    vectors: dict[str, list[float]] = {}
    models: set[str] = set()
    configurations: set[str] = set()
    with embeddings_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            digest = record.get("chunk_content_sha256") or record.get("content_sha256")
            if not digest:
                raise ValueError("embedding sem hash de conteúdo")
            vectors[digest] = record["embedding"]
            models.add(str(record.get("embedding_model", "")))
            configurations.add(json.dumps(record.get("encoding_config"), sort_keys=True))
    if len(models) != 1:
        raise ValueError(f"{embeddings_file} mistura modelos de embedding: {sorted(models)}")
    if len(configurations) != 1:
        raise ValueError(f"{embeddings_file} mistura configurações de codificação")

    def embed(texts: list[str]) -> list[list[float]]:
        digests = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        missing = [digest for digest in digests if digest not in vectors]
        if missing:
            raise KeyError(f"{len(missing)} chunk(s) sem vetor em {embeddings_file}: {missing[:3]}")
        return [vectors[digest] for digest in digests]

    embed.observation_source = "cache"
    embed.encoding_config = json.loads(configurations.pop())
    return embed, models.pop()


def _vetores_existentes(vectors_path: Path) -> dict[str, list[float]]:
    '''Vetores já gravados por execução anterior, para retomar sem reembedar.

    Linhas truncadas (queda no meio da escrita) são ignoradas: o chunk
    correspondente é embedado de novo.
    '''
    existentes: dict[str, list[float]] = {}
    if not vectors_path.exists():
        return existentes
    with vectors_path.open(encoding="utf-8") as handle:
        for linha in handle:
            try:
                registro = json.loads(linha)
            except ValueError:
                continue
            if registro.get("chunk_id") and registro.get("embedding") is not None:
                existentes[registro["chunk_id"]] = registro["embedding"]
    return existentes


@observar_offline(Stage.INDEXACAO)
def build_index(
    input_file: Path,
    output_dir: Path,
    embed_fn: EmbedFn,
    *,
    model: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    merge_existing: bool = False,
) -> dict[str, Any]:
    """Agrupa os chunks por base; merge_existing preserva as demais bases."""
    existing_bases = _existing_bases(output_dir, embed_fn, model) if merge_existing else {}
    by_base, skipped_sem_texto, skipped_ilegiveis = _chunks_by_base(input_file)

    source_hash = file_sha256(input_file)
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    output_dir.mkdir(parents=True, exist_ok=True)

    bases_summary: list[dict[str, Any]] = []
    for base_id in sorted(by_base):
        chunks = by_base[base_id]
        base_dir = output_dir / base_id
        base_dir.mkdir(parents=True, exist_ok=True)
        dimension, reaproveitados = _write_base_vectors(
            base_dir, base_id, chunks, embed_fn,
            model=model, batch_size=batch_size, source_hash=source_hash, existing_bases=existing_bases,
        )
        manifest = {
            "base_id": base_id,
            "embedding_model": model,
            "encoding_config": getattr(embed_fn, "encoding_config", None),
            "inventory_versions": sorted({c.metadata["inventory_version"] for c in chunks if c.metadata.get("inventory_version")}),
            "inventory_hashes": sorted({c.metadata["inventory_hash"] for c in chunks if c.metadata.get("inventory_hash")}),
            "dimension": dimension or 0,
            "faiss_index": "vectors.faiss",
            "total_chunks": len(chunks),
            "vetores_reaproveitados": reaproveitados,
            "chunks_ilegiveis_ignorados": skipped_ilegiveis.get(base_id, 0),
            "source_file": _relative(input_file),
            "source_sha256": source_hash,
            "built_at": built_at,
        }
        (base_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        bases_summary.append(manifest)

    if merge_existing:
        existing_bases.update({base["base_id"]: base for base in bases_summary})
        bases_summary = [existing_bases[key] for key in sorted(existing_bases)]

    global_manifest = {
        "embedding_model": model,
        "source_file": _relative(input_file),
        "source_sha256": source_hash,
        "built_at": built_at,
        "chunks_sem_texto_ignorados": skipped_sem_texto,
        "chunks_ilegiveis_ignorados": sum(base.get("chunks_ilegiveis_ignorados", 0) for base in bases_summary),
        "bases": bases_summary,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(global_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return global_manifest


def _existing_bases(output_dir: Path, embed_fn: EmbedFn, model: str) -> dict[str, dict[str, Any]]:
    """Manifests das bases já indexadas, que o suplemento preserva; exigem o mesmo modelo e configuração."""
    existing_bases: dict[str, dict[str, Any]] = {}
    for path in sorted(output_dir.glob("*/manifest.json")):
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("embedding_model") != model or existing.get("encoding_config") != getattr(embed_fn, "encoding_config", None):
            raise ValueError("suplemento exige o mesmo modelo e configuração dos índices existentes")
        existing_bases[str(existing["base_id"])] = existing
    return existing_bases


def _chunks_by_base(input_file: Path) -> tuple[dict[str, list[NormalizedChunk]], int, dict[str, int]]:
    """(chunks por base, chunks sem texto, ilegíveis por base); recusa chunk sem metadado obrigatório."""
    by_base: dict[str, list[NormalizedChunk]] = defaultdict(list)
    skipped_sem_texto = 0
    skipped_ilegiveis: dict[str, int] = defaultdict(int)
    for chunk in read_normalized_chunks(input_file):
        if not chunk.text.strip():
            skipped_sem_texto += 1
            continue
        if _texto_ilegivel(chunk):
            skipped_ilegiveis[chunk.base_id] += 1
            continue
        by_base[chunk.base_id].append(chunk)

    missing_by_chunk = {
        chunk.chunk_id: sorted(REQUIRED_METADATA_FIELDS - set(chunk.metadata))
        for chunks in by_base.values()
        for chunk in chunks
        if REQUIRED_METADATA_FIELDS - set(chunk.metadata)
    }
    if missing_by_chunk:
        raise ValueError(f"chunks com metadado obrigatório ausente: {missing_by_chunk}")
    return by_base, skipped_sem_texto, skipped_ilegiveis


def _write_base_vectors(
    base_dir: Path,
    base_id: str,
    chunks: list[NormalizedChunk],
    embed_fn: EmbedFn,
    *,
    model: str,
    batch_size: int,
    source_hash: str,
    existing_bases: dict[str, dict[str, Any]],
) -> tuple[int | None, int]:
    """vectors.jsonl e vectors.faiss de uma base, retomando vetores já gravados; devolve (dimensão, reaproveitados)."""
    vectors_path = base_dir / "vectors.jsonl"
    base_manifest = base_dir / "manifest.json"
    if vectors_path.exists() and base_manifest.exists():
        anterior = json.loads(base_manifest.read_text(encoding="utf-8"))
        if anterior.get("embedding_model") != model or anterior.get("encoding_config") != getattr(embed_fn, "encoding_config", None):
            raise ValueError("retomada exige o mesmo modelo e configuração; não relabelar vetores")
    vectors_temporario = base_dir / "vectors.jsonl.provisorio"
    dimension: int | None = None
    embedding_rows: list[list[float]] = []
    existentes = _vetores_existentes(vectors_path)
    retomada_path = base_dir / "retomada.json"
    configuracao_retomada = {"model": model, "encoding_config": getattr(embed_fn, "encoding_config", None), "source_sha256": source_hash}
    if vectors_temporario.exists():
        if not retomada_path.exists() or json.loads(retomada_path.read_text(encoding="utf-8")) != configuracao_retomada:
            raise ValueError("checkpoint provisório incompatível; preserve-o e use outro staging")
        existentes.update(_vetores_existentes(vectors_temporario))
    retomada_path.write_text(json.dumps(configuracao_retomada, sort_keys=True) + "\n", encoding="utf-8")
    reaproveitados = 0
    # Nunca truncar o único checkpoint ao retomar. Em erro, o antigo fica.
    with vectors_temporario.open("w", encoding="utf-8", newline="\n") as handle:
        for batch in batched(chunks, batch_size):
            novos = [chunk for chunk in batch if chunk.chunk_id not in existentes]
            if novos:
                with observar_lote_embedding(model=model, count=len(novos),
                    cache=getattr(embed_fn, "observation_source", None) == "cache", base_id=base_id):
                    embeddings = embed_fn([chunk.text for chunk in novos])
                if len(embeddings) != len(novos):
                    raise RuntimeError(
                        f"embed_fn retornou {len(embeddings)} vetores para {len(novos)} textos"
                    )
                for chunk, vetor in zip(novos, embeddings):
                    existentes[chunk.chunk_id] = vetor
            reaproveitados += len(batch) - len(novos)
            for chunk in batch:
                embedding = existentes[chunk.chunk_id]
                if dimension is None:
                    dimension = len(embedding)
                    if any(base.get("dimension") != dimension for base in existing_bases.values()):
                        raise ValueError("dimensão do suplemento difere dos índices existentes")
                elif len(embedding) != dimension:
                    raise RuntimeError(
                        f"dimensão inconsistente no chunk {chunk.chunk_id}: "
                        f"esperado {dimension}, recebido {len(embedding)}"
                    )
                record = {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "base_id": chunk.base_id,
                    "embedding": embedding,
                    "source_file": chunk.source_file,
                    "sha256": chunk.sha256,
                    "metadata": chunk.metadata,
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                embedding_rows.append(embedding)

    faiss_temporario = base_dir / "vectors.faiss.provisorio"
    _write_faiss_index(faiss_temporario, embedding_rows)
    vectors_temporario.replace(vectors_path)
    faiss_temporario.replace(base_dir / "vectors.faiss")
    return dimension, reaproveitados


@observar_offline(Stage.INDEXACAO)
def _dry_run_index(input_file: Path) -> dict[str, Any]:
    by_base: dict[str, int] = defaultdict(int)
    skipped = 0
    ilegiveis = 0
    for chunk in read_normalized_chunks(input_file):
        if not chunk.text.strip():
            skipped += 1
            continue
        if _texto_ilegivel(chunk):
            ilegiveis += 1
            continue
        by_base[chunk.base_id] += 1
    return {"chunks_por_base": dict(sorted(by_base.items())),
            "chunks_sem_texto_ignorados": skipped, "chunks_ilegiveis_ignorados": ilegiveis}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="JSONL normalizado de entrada")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="diretório de saída do índice")
    parser.add_argument("--model", default=config.EMBEDDING_MODEL, help="modelo de embeddings")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="chunks por chamada de embeddings")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="conta chunks por base sem chamar o modelo de embeddings",
    )
    parser.add_argument(
        "--embeddings",
        type=Path,
        help="reaproveita os vetores deste JSONL (ex.: data/embeddings/embeddings.jsonl) em vez de chamar o modelo",
    )
    parser.add_argument(
        "--merge-existing", action="store_true",
        help="adiciona/atualiza as bases de entrada sem remover as demais do manifesto do índice",
    )
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"arquivo normalizado não encontrado: {args.input} (rode src/ingest.py primeiro)")
    if args.batch_size < 1:
        parser.error("--batch-size deve ser >= 1")

    if args.dry_run:
        summary = _dry_run_index(args.input)
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    if args.embeddings:
        if not args.embeddings.exists():
            parser.error(f"arquivo de embeddings não encontrado: {args.embeddings}")
        embed_fn, model = stored_embed_fn(args.embeddings)
    else:
        embed_fn, model = _default_embed_fn(args.model), args.model
    manifest = build_index(args.input, args.output, embed_fn, model=model, batch_size=args.batch_size,
                           merge_existing=args.merge_existing)
    summary = {
        "bases": [
            {"base_id": base["base_id"], "total_chunks": base["total_chunks"], "dimension": base["dimension"]}
            for base in manifest["bases"]
        ],
        "chunks_sem_texto_ignorados": manifest["chunks_sem_texto_ignorados"],
        "chunks_ilegiveis_ignorados": manifest["chunks_ilegiveis_ignorados"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
