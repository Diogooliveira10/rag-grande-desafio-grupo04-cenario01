'''Modo separado "direto para artefatos finais" (decisão §7.6 do plano v7).

Recebe registros do provider em memória e grava os artefatos finais sem
materializar os intermediários do caminho padrão — nada de ``data/raw/``,
``data/backups/``, checkpoints nem snapshot de ``data/embeddings/``:

- ``documents.jsonl`` — hub compacto: corpus do BM25 e proveniência
  (``source_sha256``). Não é andaime: a busca esparsa o lê em consulta
  (``src/retrieve.py``);
- ``index/<base_id>/`` — vetores FAISS + payloads + manifest (o embedding é
  feito direto, via ``embed_fn``);
- ``base_estruturada.sqlite`` — consulta estruturada;
- ``inventario_direto.json`` — inventário final compacto, por origem, com
  hashes (§8 do plano: proveniência como na v6).

A semântica dos chunks é a do caminho padrão: as mesmas funções de
``src/prepare_documents.py`` (``SourceUnit``, ``_chunk_records``,
``_declared_record_metadata``, ``_record_logical_id``) constroem cada registro,
então ``chunk_id``, ``logical_id``, extras analíticos e ``data_vigencia`` saem
idênticos. O comportamento padrão do pipeline não muda; este modo é opt-in.

Dois pontos de entrada:

- ``construir_direto`` — lotes em memória, uma passada só (testes e lotes
  pequenos);
- ``chunk_de_registro`` + ``finalizar_direto`` — o caminho de streaming do
  coletor (``src/direct_collection.py``), que acumula ``documents.jsonl`` por
  janelas retomáveis e finaliza no fim.

Uso (CLI):

    python -m src.direct_build --registros registros.jsonl \
        --manifest corpus_manifest.yaml --base-id contratos_estruturado \
        --destino data/v7_direto

Cada linha de ``registros.jsonl`` é ``{"payload": {...}, "origem": URL,
"coletado_em": ISO, "sha256": ..., "bytes": int}`` — o ``sha256``/``bytes`` são
os dos bytes brutos do registro na origem (o coletor os passa; sem eles, o
hash é derivado do JSON canônico e marcado como derivado no inventário).
'''

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from src import index as index_mod
from src.prepare_documents import (
    SourceUnit,
    _chunk_records,
    _declared_record_metadata,
    _record_logical_id,
)
from src.embedding_projection import (
    MODO_COMPLETA,
    MODOS,
    embed_fn_com_fallback,
    embed_fn_com_pausa,
    embed_fn_concorrente,
    embed_fn_projetado,
)
from src.structured_db import Banco, construir_banco

EmbedFn = Callable[[list[str]], list[list[float]]]

CHUNK_SIZE_PADRAO = 1200
OVERLAP_PADRAO = 150
# Slots do servidor local de embeddings (llama-server --parallel).
CONCORRENCIA_PADRAO = 4


@dataclass
class RegistroDireto:
    '''Um registro do provider, antes de qualquer escrita em disco.'''

    payload: dict[str, Any]
    origem: str
    sha256: str = ""
    bytes: int = 0
    coletado_em: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> "RegistroDireto":
        return cls(
            payload=dados["payload"],
            origem=str(dados.get("origem") or ""),
            sha256=str(dados.get("sha256") or ""),
            bytes=int(dados.get("bytes") or 0),
            coletado_em=str(dados.get("coletado_em") or ""),
            extras=dict(dados.get("extras") or {}),
        )


def _serializar(payload: dict[str, Any]) -> str:
    '''Mesma serialização de registro do caminho padrão (paridade de hash).'''
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def chunk_de_registro(
    registro: RegistroDireto | dict[str, Any],
    base: dict[str, Any],
    *,
    manifest_hash: str = "",
    chunk_size: int = CHUNK_SIZE_PADRAO,
    overlap: int = OVERLAP_PADRAO,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    '''Converte um registro nos chunks do caminho padrão + entrada de inventário.

    Sem ``sha256`` dos bytes na origem, o hash é derivado do JSON canônico e o
    flag ``hash_derivado`` acompanha o chunk até o inventário — nunca
    silencioso.
    '''
    reg = registro if isinstance(registro, RegistroDireto) else RegistroDireto.de_dict(registro)
    text = _serializar(reg.payload)
    digest = reg.sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()
    derivado = not reg.sha256
    bytes_ = reg.bytes or len(text.encode("utf-8"))
    coletado_em = reg.coletado_em or _agora()
    declared = _declared_record_metadata(reg.payload, base)
    data_vigencia = declared.pop("data_vigencia", None)

    extras = {
        "source_file": reg.origem,
        "nome_arquivo": f"registro_{digest[:16]}.json",
        "bytes": bytes_,
        "coletado_em": coletado_em,
        "manifest_fonte": base.get("fonte"),
        "manifest_hash": manifest_hash,
        "origem": reg.origem,
        "acesso_tipo": (base.get("acesso") or {}).get("tipo"),
        "sha256": digest,
        "hash_derivado": derivado,
        **declared,
        **reg.extras,
    }
    if data_vigencia not in (None, ""):
        extras["data_vigencia"] = data_vigencia
    unit = SourceUnit(
        source_file=reg.origem,
        logical_id=_record_logical_id(str(base["base_id"]), reg.origem, reg.payload, digest[:16]),
        document_id=f"{base['base_id']}:{digest[:16]}",
        base_id=str(base["base_id"]),
        natureza=str(base["natureza"]),
        doc_type="json",
        sensitivity=str(base.get("sensitivity_default") or "publico"),
        origem=reg.origem,
        manifest_hash=manifest_hash,
        bytes=bytes_,
        data_vigencia=str(data_vigencia) if data_vigencia not in (None, "") else None,
        chunking_sugerido=str(base.get("chunking_sugerido") or "chunking_generico"),
        formato="json",
        sha256=digest,
        text=text,
        status="ok",
        extras=extras,
    )
    chunks = _chunk_records(unit, chunk_size, overlap)
    entrada = {
        "base_id": str(base["base_id"]),
        "origem": reg.origem,
        "sha256": digest,
        "hash_derivado": derivado,
        "bytes": bytes_,
        "coletado_em": coletado_em,
        "status": "ok",
    }
    return chunks, entrada


def finalizar_direto(
    destino: Path,
    base: dict[str, Any],
    *,
    embed_fn: EmbedFn | None = None,
    modelo: str | None = None,
    manifest_hash: str = "",
    projecao: str = MODO_COMPLETA,
) -> dict[str, Any]:
    '''Finaliza um ``documents.jsonl`` acumulado: deduplica, indexa e grava o
    SQLite + inventário. Idempotente: rodar de novo não duplica nada.'''

    import config

    destino = Path(destino)
    estatisticas_embed: dict[str, int] = {}
    eventos_fallback: list[dict[str, Any]] = []
    if embed_fn is None:
        embed_fn, modelo_ecoa = _embed_padrao_do_modo_direto(estatisticas_embed, eventos_fallback)
        modelo = modelo or modelo_ecoa
    modelo = modelo or config.EMBEDDING_MODEL
    documents = destino / "documents.jsonl"
    arquivos, chunks_unicos = _deduplicar_documentos(documents)
    source_sha256 = index_mod.file_sha256(documents)

    relatorio_indice = index_mod.build_index(
        input_file=documents,
        output_dir=destino / "index",
        embed_fn=embed_fn_concorrente(
            embed_fn_projetado(embed_fn, projecao), CONCORRENCIA_PADRAO
        ),
        model=modelo,
    )
    modelo_efetivo = _modelo_efetivo(modelo, estatisticas_embed)
    _registrar_projecao_nos_manifests(destino / "index", projecao, modelo_efetivo)
    relatorio_banco = construir_banco(
        documents_file=documents,
        banco=Banco(sqlite_path=destino / "base_estruturada.sqlite"),
    )
    inventario_path = _gravar_inventario(
        destino, base, arquivos, manifest_hash=manifest_hash, source_sha256=source_sha256, projecao=projecao,
        modelo_efetivo=modelo_efetivo, estatisticas_embed=estatisticas_embed, eventos_fallback=eventos_fallback,
    )
    return {
        "destino": str(destino),
        "documents": str(documents),
        "source_sha256": source_sha256,
        "registros": len(arquivos),
        "chunks": chunks_unicos,
        "index": relatorio_indice,
        "banco": relatorio_banco,
        "inventario": str(inventario_path),
    }


def _embed_padrao_do_modo_direto(estatisticas_embed: dict[str, int],
                                 eventos_fallback: list[dict[str, Any]]) -> tuple[EmbedFn, str]:
    """Embedding padrão do modo direto e o nome do modelo do ECOA."""
    import config

    # Padrão do modo direto: ECOA primeiro (endpoint compartilhado, com
    # pausa entre lotes) e encoder local como reserva. Os dois servem o
    # modelo base Qwen3-Embedding-0.6B, mas quantizações distintas não
    # provam identidade: compatibilidade/proveniência são gates de promoção.
    # A contagem por origem desta execução fica registrada no inventário.
    from src.create_embeddings import _configured_embed_batch

    endpoint, api_key, modelo_ecoa = config.embedding_endpoint("ecoa")
    primario = embed_fn_com_pausa(
        _configured_embed_batch("ecoa", endpoint, api_key, modelo_ecoa),
        config.PAUSA_ENTRE_LOTES_EMBEDDING,
    )
    local_url, local_key, modelo_local = config.embedding_endpoint("ollama")
    reserva = embed_fn_concorrente(
        _configured_embed_batch("ollama", local_url, local_key, modelo_local),
        CONCORRENCIA_PADRAO,
    )

    def _registrar_fallback(erro: Exception, tamanho: int) -> None:
        if len(eventos_fallback) < 20:
            eventos_fallback.append({
                "erro": f"{type(erro).__name__}: {erro}"[:200],
                "lote": tamanho,
            })

    embed_fn = embed_fn_com_fallback(
        primario, reserva,
        estatisticas=estatisticas_embed, ao_fallback=_registrar_fallback,
    )
    return embed_fn, modelo_ecoa


def _deduplicar_documentos(documents: Path) -> tuple[list[dict[str, Any]], int]:
    """Tira chunks repetidos do ``documents.jsonl``; devolve os arquivos (um por documento) e os chunks únicos."""
    # Deduplicação em streaming com troca atômica: só os ids ficam na memória e
    # o arquivo original só é substituído depois do temporário completo.
    # (Uma reescrita em memória já derrubou o processo com MemoryError depois
    # de truncar o arquivo, custando uma coleta inteira.)
    provisorio = documents.with_name(documents.name + ".provisorio")
    vistas: set[str] = set()
    vistos_documentos: set[str] = set()
    arquivos: list[dict[str, Any]] = []
    total_linhas = 0
    chunks_unicos = 0
    try:
        with (
            documents.open(encoding="utf-8") as entrada,
            provisorio.open("w", encoding="utf-8", newline="\n") as saida,
        ):
            for linha in entrada:
                if not linha.strip():
                    continue
                total_linhas += 1
                chunk = json.loads(linha)
                if chunk["chunk_id"] in vistas:
                    continue
                vistas.add(chunk["chunk_id"])
                chunks_unicos += 1
                saida.write(linha if linha.endswith("\n") else linha + "\n")
                if chunk["document_id"] in vistos_documentos:
                    continue
                vistos_documentos.add(chunk["document_id"])
                extras = chunk.get("extras") or {}
                arquivos.append({
                    "base_id": chunk["base_id"],
                    "origem": chunk.get("origem") or "",
                    "sha256": chunk.get("sha256") or "",
                    "hash_derivado": bool(extras.get("hash_derivado")),
                    "bytes": chunk.get("bytes") or 0,
                    "coletado_em": chunk.get("coletado_em") or "",
                    "status": "ok",
                })
    except BaseException:
        provisorio.unlink(missing_ok=True)
        raise
    if chunks_unicos != total_linhas:
        provisorio.replace(documents)
    else:
        provisorio.unlink(missing_ok=True)
    return arquivos, chunks_unicos


def _modelo_efetivo(modelo: str, estatisticas_embed: dict[str, int]) -> str:
    """Encoder que de fato gerou os vetores, pelas contagens primário/reserva."""
    import config

    # Corrigir modelo no manifest: o nome inicial é do ECOA (primário),
    # mas se todos os vetores vieram do fallback local, o manifest deve
    # refletir o encoder que realmente gerou os vetores.
    modelo_efetivo = modelo
    if estatisticas_embed:
        primario = config.embedding_endpoint("ecoa")[2] if "primario" in estatisticas_embed else None
        reserva = config.embedding_endpoint("ollama")[2] if "reserva" in estatisticas_embed else None
        if estatisticas_embed.get("reserva", 0) > 0 and estatisticas_embed.get("primario", 0) == 0:
            modelo_efetivo = reserva or modelo
        elif estatisticas_embed.get("primario", 0) > 0 and estatisticas_embed.get("reserva", 0) > 0:
            modelo_efetivo = f"{primario or modelo}+{reserva or modelo}"
    return modelo_efetivo


def _registrar_projecao_nos_manifests(index_dir: Path, projecao: str, modelo_efetivo: str) -> None:
    # A projeção é registrada no manifest de cada base e no inventário: um
    # índice nunca é misturado com projeção diferente em silêncio.
    for manifest_path in sorted(index_dir.glob("*/manifest.json")):
        manifest_indice = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_indice["projecao_embedding"] = projecao
        manifest_indice["embedding_model"] = modelo_efetivo
        manifest_path.write_text(
            json.dumps(manifest_indice, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )


def _gravar_inventario(destino: Path, base: dict[str, Any], arquivos: list[dict[str, Any]], *, manifest_hash: str,
                       source_sha256: str, projecao: str, modelo_efetivo: str, estatisticas_embed: dict[str, int],
                       eventos_fallback: list[dict[str, Any]]) -> Path:
    inventario = {
        "completo": True,  # artefatos finalizados, não prova de censo estadual
        "cobertura_institucional_comprovada": False,
        "falhas": [],
        "modo": "direto",
        "gerado_em": _agora(),
        "meta": {
            "manifest_hash": manifest_hash,
            "source_sha256": source_sha256,
            "projecao_embedding": projecao,
            "embedding_model": modelo_efetivo,
            "embedding_por_origem": estatisticas_embed,
            "embedding_fallbacks": eventos_fallback,
        },
        "por_base": [{
            "base_id": str(base["base_id"]),
            "status": "complete",
            "volume_obtido": len(arquivos),
        }],
        "arquivos": arquivos,
    }
    inventario_path = destino / "inventario_direto.json"
    inventario_path.write_text(
        json.dumps(inventario, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return inventario_path


def construir_direto(
    registros: Iterable[RegistroDireto | dict[str, Any]],
    base: dict[str, Any],
    destino: Path,
    *,
    embed_fn: EmbedFn | None = None,
    modelo: str | None = None,
    manifest_hash: str = "",
    chunk_size: int = CHUNK_SIZE_PADRAO,
    overlap: int = OVERLAP_PADRAO,
    projecao: str = MODO_COMPLETA,
) -> dict[str, Any]:
    '''Grava os artefatos finais da base em ``destino`` e devolve o relatório.

    ``embed_fn`` é injetável (os testes usam um determinístico); sem ele, usa
    o cliente padrão do índice. Nada fora de ``destino`` é criado ou alterado.
    '''
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    documents = destino / "documents.jsonl"
    # Escrita em streaming com troca atômica: nenhum lote inteiro na memória e
    # o arquivo anterior só é substituído depois do temporário completo.
    provisorio = documents.with_name(documents.name + ".provisorio")
    try:
        with provisorio.open("w", encoding="utf-8", newline="\n") as saida:
            for registro in registros:
                chunks, _entrada = chunk_de_registro(
                    registro, base, manifest_hash=manifest_hash,
                    chunk_size=chunk_size, overlap=overlap,
                )
                for chunk in chunks:
                    saida.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")
    except BaseException:
        provisorio.unlink(missing_ok=True)
        raise
    provisorio.replace(documents)
    return finalizar_direto(
        destino, base, embed_fn=embed_fn, modelo=modelo, manifest_hash=manifest_hash,
        projecao=projecao,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registros", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--base-id", required=True)
    parser.add_argument("--destino", type=Path, required=True)
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE_PADRAO)
    parser.add_argument("--overlap", type=int, default=OVERLAP_PADRAO)
    parser.add_argument("--projecao", choices=MODOS, default=MODO_COMPLETA)
    args = parser.parse_args(argv)

    from src.fetch_corpus import load_manifest

    manifest = load_manifest(args.manifest)
    manifest_hash = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    base = next(
        (b for b in manifest.get("bases", []) if b.get("base_id") == args.base_id), None
    )
    if base is None:
        parser.error(f"base {args.base_id!r} não está no manifesto")

    registros = (
        RegistroDireto.de_dict(json.loads(linha))
        for linha in args.registros.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    )
    relatorio = construir_direto(
        registros,
        base,
        args.destino,
        modelo=args.modelo,
        manifest_hash=f"sha256:{manifest_hash}",
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        projecao=args.projecao,
    )
    print(json.dumps(relatorio, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
