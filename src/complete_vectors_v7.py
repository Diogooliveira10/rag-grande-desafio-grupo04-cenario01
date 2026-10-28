"""Completa lacunas vetoriais em destino novo, sem promoção nem re-embedding.

prepare extrai apenas os textos sem vetor. Gere-os com run_local_embeddings;
complete exige cobertura exata, hashes, modelo/configuração e 1.024 dimensões.
verify confere IDs, conteúdo, posição FAISS, SQL e catálogo, sem inferência.
A proveniência histórica incompleta não é convertida em atestação de encoding.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any

from src.create_embeddings import _content_sha256
from src.index import file_sha256, _relative
from src.recover_corpus_v7 import agora, destino_novo, gravar_json
from src.repair_candidate_v7 import _linhas, _metadata


def lacunas(candidato: Path) -> dict[str, dict[str, Any]]:
    faltantes = {}
    for folder in sorted((candidato / "index").iterdir()):
        if not folder.is_dir():
            continue
        ids = set()
        for row in _linhas(folder / "vectors.jsonl"):
            cid = row["chunk_id"]
            if cid in ids:
                raise ValueError(f"vetor duplicado: {cid}")
            ids.add(cid)
        docs = {}
        for doc in _linhas(folder / "documents.jsonl"):
            if doc["chunk_id"] in docs:
                raise ValueError(f"documento duplicado: {doc['chunk_id']}")
            docs[doc["chunk_id"]] = doc
        if ids - docs.keys():
            raise ValueError(f"vetor sem documento: {folder.name}")
        for cid in docs.keys() - ids:
            if cid in faltantes:
                raise ValueError(f"chunk_id entre bases: {cid}")
            faltantes[cid] = docs[cid]
    return faltantes


def preparar(candidato: Path, trabalho: Path) -> dict[str, Any]:
    faltantes = lacunas(candidato)
    trabalho = destino_novo(trabalho, [candidato])
    with (trabalho / "faltantes.jsonl").open("w", encoding="utf-8", newline="\n") as output:
        for cid, doc in sorted(faltantes.items()):
            output.write(json.dumps(doc, ensure_ascii=False) + "\n")
    por_base: dict[str, int] = {}
    for doc in faltantes.values():
        base = doc["base_id"]
        por_base[base] = por_base.get(base, 0) + 1
    report = {"origem": _relative(candidato), "total": len(faltantes), "por_base": por_base,
              "source_sha256": file_sha256(candidato / "documents.jsonl"),
              "index_manifest_sha256": file_sha256(candidato / "index/manifest.json")}
    gravar_json(trabalho / "lacunas.json", report)
    return report


def _vetor(embedding: Any, dimension: int):
    import numpy as np

    vetor = np.asarray(embedding, dtype=np.float32)
    if vetor.shape != (dimension,) or not np.isfinite(vetor).all() or not np.any(vetor):
        raise ValueError("vetor inválido: dimensão, finitude ou norma")
    norma = np.linalg.norm(vetor)
    if not np.isfinite(norma) or norma <= 0:
        raise ValueError("vetor inválido: norma não finita ou nula")
    return vetor / norma


def completar(candidato: Path, destino: Path, embeddings: Path, *, dimension: int = 1024,
              catalogo: Path | None = None) -> dict[str, Any]:
    import faiss
    import numpy as np

    faltantes = lacunas(candidato)
    novos = {}
    checkpoints: dict[Path, str] = {}
    for row in _linhas(embeddings):
        cid = row["chunk_id"]
        if cid in novos or cid not in faltantes:
            raise ValueError(f"embedding extra ou duplicado: {cid}")
        doc = faltantes[cid]
        if row.get("chunk_content_sha256") != _content_sha256(doc["text"]):
            raise ValueError(f"texto do embedding divergente: {cid}")
        if row.get("base_id") != doc["base_id"]:
            raise ValueError(f"base divergente: {cid}")
        _vetor(row.get("embedding"), dimension)
        if "qwen3-embedding" not in str(row.get("embedding_model", "")).lower():
            raise ValueError("modelo não é Qwen3-Embedding")
        encoding = row.get("encoding_config") or {}
        checkpoint = Path(encoding.get("checkpoint", ""))
        if checkpoint.is_file() and checkpoint not in checkpoints:
            checkpoints[checkpoint] = file_sha256(checkpoint)
        if (encoding.get("pooling") != "last" or encoding.get("document_instruction") is not None
                or not checkpoint.is_file() or encoding.get("checkpoint_sha256") != checkpoints.get(checkpoint)):
            raise ValueError("configuração/checkpoint do embedding não verificável")
        novos[cid] = row
    if novos.keys() != faltantes.keys():
        raise ValueError("cobertura incompleta dos embeddings faltantes")
    # Valida a origem inteira antes de copiar ou modificar qualquer índice.
    verificar(candidato)
    destino = destino_novo(destino, [candidato, embeddings])
    shutil.copytree(candidato, destino, dirs_exist_ok=True)
    if catalogo is not None:
        shutil.copy2(catalogo, destino / "catalogo.json")
    report: dict[str, Any] = {"gerado_em": agora(), "origem": _relative(candidato),
        "embeddings_sha256": file_sha256(embeddings), "novos_vetores": len(novos),
        "promocao_autorizada": False, "bases": {}}
    for folder in sorted((destino / "index").iterdir()):
        if not folder.is_dir():
            continue
        batch = [row for cid, row in sorted(novos.items()) if faltantes[cid]["base_id"] == folder.name]
        if not batch:
            continue
        manifest_path = folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        index = faiss.read_index(str(folder / "vectors.faiss"))
        if index.d != dimension:
            raise ValueError("dimensão FAISS incompatível")
        with (folder / "vectors.jsonl").open("a", encoding="utf-8", newline="\n") as output:
            for row in batch:
                metadata = _metadata(faltantes[row["chunk_id"]])
                record = {**row, "metadata": metadata, "source_file": metadata["source_file"],
                          "embedding_source": "complemento_q8_local"}
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
        index.add(np.ascontiguousarray([_vetor(row["embedding"], dimension) for row in batch], dtype=np.float32))
        faiss.write_index(index, str(folder / "vectors.faiss"))
        manifest.update(total_vectors=int(index.ntotal), vetores_ausentes=[],
                        source_file=_relative(folder / "documents.jsonl"))
        manifest["origens_vetores"] = {**manifest.get("origens_vetores", {}), "complemento_q8_local": len(batch)}
        manifest["complemento_vetores"] = {
            "quantidade": len(batch), "modelo": batch[0]["embedding_model"],
            "encoding_config": batch[0]["encoding_config"], "embeddings_sha256": report["embeddings_sha256"]}
        # As declarações históricas continuam válidas só para os vetores antigos.
        gravar_json(manifest_path, manifest)
        report["bases"][folder.name] = {"adicionados": len(batch), "total_vectors": int(index.ntotal)}
    path = destino / "index/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(total_vectors=manifest["total_vectors"] + len(novos),
                    snapshot_id=destino.name, built_at=report["gerado_em"],
                    source_file=_relative(destino / "documents.jsonl"))
    manifest["complemento_vetores"] = dict(report)
    manifest["vetores_embedados"] = len(novos)
    manifest["vetores_reaproveitados"] = manifest["total_vectors"] - len(novos)
    manifest["proveniencia_anterior"] = manifest.get("proveniencia_vetores", {})
    origens: dict[str, int] = {}
    por_base = {}
    for entry in manifest["bases"]:
        base = entry["base_id"] if isinstance(entry, dict) else entry
        sub = json.loads((destino / "index" / base / "manifest.json").read_text(encoding="utf-8"))
        por_base[base] = sub.get("origens_vetores", {})
        for origem, count in por_base[base].items():
            origens[origem] = origens.get(origem, 0) + count
    manifest["proveniencia_vetores"] = {
        "total_vetores": manifest["total_vectors"], "por_base": por_base, "por_origem": origens,
        "homogeneidade_modelo_comprovada": False, "homogeneidade_configuracao_comprovada": False,
    }
    gravar_json(path, manifest)
    report["integridade"] = verificar(destino)
    gravar_json(destino / "complemento_vetores.json", report)
    return report


def verificar(candidato: Path) -> dict[str, Any]:
    """Confere o payload contra cada posição FAISS (não só as contagens)."""
    import faiss
    import numpy as np

    global_manifest = json.loads((candidato / "index/manifest.json").read_text(encoding="utf-8"))
    if file_sha256(candidato / "documents.jsonl") != global_manifest["source_sha256"]:
        raise ValueError("hash global de documentos divergente")
    # Só guarde a assinatura global: manter também os 629 MB de documentos
    # decodificados duplicaria a memória ao conferir cada base.
    def assinatura(doc: dict[str, Any]) -> str:
        return _content_sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True))

    globais = {}
    for row in _linhas(candidato / "documents.jsonl"):
        cid = row["chunk_id"]
        if cid in globais:
            raise ValueError(f"documento global duplicado: {cid}")
        globais[cid] = assinatura(row)
    report: dict[str, Any] = {"documentos": 0, "vetores": 0, "bases": {}}
    seen = set()
    for entry in global_manifest["bases"]:
        base = entry["base_id"] if isinstance(entry, dict) else entry
        folder = candidato / "index" / base
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if file_sha256(folder / "documents.jsonl") != manifest["source_sha256"]:
            raise ValueError(f"hash de documentos divergente: {base}")
        docs = {}
        for doc in _linhas(folder / "documents.jsonl"):
            cid = doc["chunk_id"]
            if cid in docs or cid in seen or assinatura(doc) != globais.get(cid) or doc["base_id"] != base:
                raise ValueError(f"documentos incoerentes: {cid}")
            if _metadata(doc)["chunk_content_sha256"] != _content_sha256(doc["text"]):
                raise ValueError(f"hash do texto divergente: {cid}")
            docs[cid] = doc
            seen.add(cid)
        index = faiss.read_index(str(folder / "vectors.faiss"))
        if index.d != manifest["dimension"]:
            raise ValueError(f"dimensão divergente: {base}")
        ids = set()
        for pos, row in enumerate(_linhas(folder / "vectors.jsonl")):
            cid = row["chunk_id"]
            if cid in ids or cid not in docs:
                raise ValueError(f"vetor extra/duplicado: {cid}")
            expected = _metadata(docs[cid])["chunk_content_sha256"]
            if row.get("metadata", {}).get("chunk_content_sha256") != expected:
                raise ValueError(f"hash de conteúdo divergente: {cid}")
            vetor = _vetor(row["embedding"], index.d)
            if pos >= index.ntotal or not np.allclose(vetor, index.reconstruct(pos), rtol=1e-5, atol=1e-6):
                raise ValueError(f"posição FAISS divergente: {base}:{pos}")
            ids.add(cid)
        ausentes = sorted(docs.keys() - ids)
        if (len(docs) != manifest["total_chunks"] or len(ids) != manifest["total_vectors"]
                or len(ids) != index.ntotal or ausentes != sorted(manifest.get("vetores_ausentes", []))):
            raise ValueError(f"contagens/lacunas divergentes: {base}")
        report["bases"][base] = {"documentos": len(docs), "vetores": len(ids),
            "ausentes": len(ausentes), "vectors_sha256": file_sha256(folder / "vectors.jsonl"),
            "faiss_sha256": file_sha256(folder / "vectors.faiss"), "posicoes_faiss_verificadas": True}
        report["documentos"] += len(docs)
        report["vetores"] += len(ids)
    if seen != globais.keys() or any(report[key] != global_manifest[field] for key, field in (
            ("documentos", "total_chunks"), ("vetores", "total_vectors"))):
        raise ValueError("contagens globais divergentes")
    report["ausentes"] = report["documentos"] - report["vetores"]
    report["proveniencia_encoding_completa"] = global_manifest.get("proveniencia_encoding_completa", False)
    database = candidato / "base_estruturada.sqlite"
    if database.exists():
        from src.structured_db import Banco, validar_banco
        validar_banco(candidato / "documents.jsonl", Banco(sqlite_path=database))
        report["sql_validado"] = True
        if (candidato / "catalogo.json").exists():
            from src.snapshot import _validar_catalogo
            _validar_catalogo(candidato / "catalogo.json", database)
            report["catalogo_validado"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("acao", choices=("prepare", "complete", "verify"))
    parser.add_argument("--candidato", type=Path, required=True)
    parser.add_argument("--destino", type=Path)
    parser.add_argument("--embeddings", type=Path)
    parser.add_argument("--catalogo", type=Path)
    args = parser.parse_args(argv)
    if args.acao in {"prepare", "complete"} and args.destino is None:
        parser.error("--destino é obrigatório")
    if args.acao == "complete" and args.embeddings is None:
        parser.error("--embeddings é obrigatório")
    report = (preparar(args.candidato, args.destino) if args.acao == "prepare" else
              completar(args.candidato, args.destino, args.embeddings, catalogo=args.catalogo) if args.acao == "complete" else
              verificar(args.candidato))
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
