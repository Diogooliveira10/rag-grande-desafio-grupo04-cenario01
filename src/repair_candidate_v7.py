"""Repara um candidato separado, sem coleta, inferência ou promoção.

Reutiliza metadados, vetores e FAISS existentes. Reconcilia cada posição FAISS,
herda as bases declaradas no manifesto e registra lacunas/proveniência.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from src.fetch_corpus import load_manifest
from src.index import REQUIRED_METADATA_FIELDS, _chunk_metadata, _relative, file_sha256
from src.recover_corpus_v7 import agora, destino_novo, gravar_json
from src.schema import ChunkMetadata


def _linhas(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _metadata(document: dict[str, Any]) -> dict[str, Any]:
    declared = document.get("metadata", document)
    if not declared.get("sensitivity"):
        raise ValueError(f"{document.get('chunk_id')}: sensitivity ausente na origem")
    metadata = _chunk_metadata(document)
    missing = {key for key in REQUIRED_METADATA_FIELDS if not metadata.get(key)}
    if missing:
        raise ValueError(f"{document.get('chunk_id')}: metadados ausentes: {sorted(missing)}")
    ChunkMetadata.model_validate(metadata)
    return metadata


def reparar(
    candidato: Path, destino: Path, *, referencia_documents: Path,
    referencia_index: Path, manifest: Path, modelo_declarado: str | None = None,
    fontes_index: tuple[Path, ...] = (),
) -> dict[str, Any]:
    # Sem FAISS/numpy, falhar antes de criar o destino.
    import faiss  # noqa: F401
    import numpy  # noqa: F401

    candidato, referencia_index = Path(candidato), Path(referencia_index)
    source_index = candidato / "index"
    original = json.loads((source_index / "manifest.json").read_text(encoding="utf-8"))
    declaration = load_manifest(manifest)
    inherited = set(declaration.get("curadoria_v7", {}).get("bases_herdadas_v6", []))
    if not inherited:
        raise ValueError("manifesto sem bases_herdadas_v6 explícitas")
    bases, added = _reunir_documentos(candidato, referencia_documents, inherited)

    destino = destino_novo(Path(destino), [candidato, referencia_documents, referencia_index, *fontes_index])
    report: dict[str, Any] = {
        "gerado_em": agora(), "candidato_origem": _relative(candidato),
        "manifest_sha256": file_sha256(manifest), "herdados_adicionados": dict(added),
        "modelo_declarado_pelo_operador": modelo_declarado,
        "bases": {}, "promocao_autorizada": False, "inferencia_executada": False,
    }
    global_hash = hashlib.sha256()
    with (destino / "documents.jsonl").open("wb") as all_documents:
        for base, documents in sorted(bases.items()):
            print(f"Reparando {base}: {len(documents)} documentos", flush=True)
            target = destino / "index" / base
            target.mkdir(parents=True)
            base_hash = _gravar_documentos_da_base(documents, target, all_documents, global_hash)
            vetores = _VetoresDaBase()
            source = source_index / base
            if (source / "manifest.json").exists():
                vetores.source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            vetores.dimension = vetores.source_manifest.get("dimension")
            with (target / "vectors.jsonl").open("w", encoding="utf-8", newline="\n") as output:
                sources = [(source, "candidato"), (referencia_index / base, "referencia")]
                sources += [(Path(folder) / base, "recuperacao") for folder in fontes_index]
                for folder, label in sources:
                    if not (folder / "vectors.jsonl").exists():
                        continue
                    _copiar_vetores_da_fonte(base, folder, label, documents, vetores, output)
            if vetores.index is not None:
                import faiss

                faiss.write_index(vetores.index, str(target / "vectors.faiss"))
            report["bases"][base] = _manifest_da_base(base, documents, target, base_hash, vetores, modelo_declarado)
            vetores.index = None
    _gravar_manifest_global(destino, original, bases, report, global_hash, modelo_declarado)
    report["documents_sha256"] = global_hash.hexdigest()
    report["cobertura_vetorial_completa"] = all(not b["vetores_ausentes"] for b in report["bases"].values())
    report["proveniencia_encoding_completa"] = False
    report["nota_proveniencia_encoding"] = (
        "Manifests registram declarações/configurações de origem, mas não provam o encoding de cada vetor. "
        "Sem logs de embedding por lote ou re-embedding verificável, homogeneidade não é certificável."
    )
    gravar_json(destino / "reparo.json", report)
    return report


def _reunir_documentos(candidato: Path, referencia_documents: Path,
                       inherited: set[str]) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, int]]:
    """Documentos do candidato mais os das bases herdadas da referência, por base e chunk_id."""
    bases: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for document in _linhas(candidato / "documents.jsonl"):
        base, cid = document["base_id"], document["chunk_id"]
        if cid in bases[base]:
            raise ValueError(f"chunk_id repetido na origem: {cid}")
        _metadata(document)
        bases[base][cid] = document
    added = defaultdict(int)
    for document in _linhas(referencia_documents):
        base, cid = document["base_id"], document["chunk_id"]
        if base not in inherited:
            continue
        if cid in bases[base]:
            if bases[base][cid].get("text") != document.get("text"):
                raise ValueError(f"mesmo chunk_id com textos diferentes: {cid}")
            continue
        _metadata(document)
        bases[base][cid] = document
        added[base] += 1
    missing_bases = inherited - set(bases)
    if missing_bases:
        raise ValueError(f"herança ausente também na referência: {sorted(missing_bases)}")
    return bases, added


def _gravar_documentos_da_base(documents: dict[str, dict[str, Any]], target: Path, all_documents: Any,
                               global_hash: Any) -> Any:
    """documents.jsonl da base (e o global, junto); devolve o hash da base."""
    base_hash = hashlib.sha256()
    with (target / "documents.jsonl").open("wb") as output:
        for document in documents.values():
            raw = (json.dumps(document, ensure_ascii=False) + "\n").encode("utf-8")
            output.write(raw)
            all_documents.write(raw)
            base_hash.update(raw)
            global_hash.update(raw)
    return base_hash


@dataclass
class _VetoresDaBase:
    """Vetores reaproveitados de uma base, acumulados fonte a fonte."""

    source_manifest: dict[str, Any] = field(default_factory=dict)
    dimension: int | None = None
    index: Any = None
    vector_count: int = 0
    recovered: int = 0
    seen: set[str] = field(default_factory=set)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    unmapped: list[dict[str, Any]] = field(default_factory=list)
    origin_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _copiar_vetores_da_fonte(base: str, folder: Path, label: str, documents: dict[str, dict[str, Any]],
                             vetores: _VetoresDaBase, output: Any) -> None:
    """Copia os vetores de uma fonte que pertencem aos documentos, conferindo cada um com a posição no FAISS."""
    import faiss
    import numpy as np

    source_info = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if not vetores.source_manifest:
        vetores.source_manifest = dict(source_info)
    source_faiss = faiss.read_index(str(folder / source_info.get("faiss_index", "vectors.faiss")))
    if vetores.dimension is None:
        vetores.dimension = source_faiss.d
    if vetores.dimension != source_faiss.d:
        raise ValueError(f"dimensão incompatível na herança: {base}")
    if vetores.index is None:
        vetores.index = faiss.IndexFlatIP(vetores.dimension)
    vetores.provenance.append({
        "origem": label, "manifest": _relative(folder / "manifest.json"),
        "manifest_sha256": file_sha256(folder / "manifest.json"),
        "embedding_model_original": source_info.get("embedding_model"),
        "encoding_config": source_info.get("encoding_config"),
    })
    positions = 0
    batch = []
    for record in _linhas(folder / "vectors.jsonl"):
        cid = record["chunk_id"]
        position = positions
        positions += 1
        if label != "candidato" and (cid in vetores.seen or cid not in documents):
            continue
        vector = np.asarray(record.get("embedding"), dtype=np.float32)
        if vector.shape != (vetores.dimension,) or not np.isfinite(vector).all() or not np.any(vector):
            raise ValueError(f"vetor inválido: {cid}")
        normalized = vector / np.linalg.norm(vector)
        if position >= source_faiss.ntotal or not np.allclose(
            normalized, source_faiss.reconstruct(position), rtol=1e-5, atol=1e-6,
        ):
            raise ValueError(f"posição FAISS não corresponde ao payload: {base}:{position}")
        if cid in vetores.seen:
            continue
        if cid not in documents:
            if label == "candidato":
                raise ValueError(f"vetor sem documento: {cid}")
            continue
        document = documents[cid]
        # Vetor da referência só serve para o mesmo input, não só o mesmo ID.
        if label != "candidato":
            expected = _metadata(document).get("chunk_content_sha256")
            actual = record.get("metadata", {}).get("chunk_content_sha256")
            if not expected or expected != actual:
                raise ValueError(f"hash da referência não corresponde ao documento: {cid}")
        metadata = _metadata(document)
        record.update({
            "metadata": metadata, "source_file": metadata["source_file"],
            "base_id": base, "document_id": document.get("document_id"),
            "embedding_source": label,
        })
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
        vetores.seen.add(cid)
        vetores.vector_count += 1
        vetores.origin_counts[label] += 1
        vetores.recovered += int(label == "recuperacao")
        batch.append(normalized)
        if len(batch) == 256:
            vetores.index.add(np.ascontiguousarray(batch, dtype=np.float32))
            batch.clear()
    if batch:
        vetores.index.add(np.ascontiguousarray(batch, dtype=np.float32))
    if positions != source_faiss.ntotal:
        if label != "candidato" or positions > source_faiss.ntotal:
            raise ValueError(f"quantidade FAISS/payload divergente: {base}/{label}")
        vetores.unmapped.append({"origem": _relative(folder), "posicoes_sem_payload": source_faiss.ntotal - positions})


def _manifest_da_base(base: str, documents: dict[str, dict[str, Any]], target: Path, base_hash: Any,
                      vetores: _VetoresDaBase, modelo_declarado: str | None) -> dict[str, Any]:
    """Grava o manifest da base e devolve o resumo dela para o relatório."""
    absent = sorted(set(documents) - vetores.seen)
    declared_models = sorted({
        str(item["embedding_model_original"])
        for item in vetores.provenance if item.get("embedding_model_original")
    })
    manifest_base = {
        **vetores.source_manifest, "base_id": base,
        "embedding_model": declared_models[0] if len(declared_models) == 1 else "mixed_or_unknown",
        "embedding_model_declarado_pelo_operador": modelo_declarado,
        "dimension": vetores.dimension, "faiss_index": "vectors.faiss",
        "total_chunks": len(documents), "total_vectors": vetores.vector_count,
        "source_file": _relative(target / "documents.jsonl"), "source_sha256": base_hash.hexdigest(),
        "proveniencia": vetores.provenance, "vetores_ausentes": absent,
        "origens_vetores": {label: vetores.origin_counts.get(label, 0)
                            for label in ("candidato", "referencia", "recuperacao")},
        "modelos_declarados_origem": declared_models,
        "encoding_config_disponivel": bool(vetores.provenance) and all(
            bool(p["encoding_config"]) for p in vetores.provenance
        ),
        # Config presente em manifest não prova a configuração efetiva
        # usada para produzir os vetores; manter false sem evidência por vetor.
        "encoding_verificado": False,
    }
    gravar_json(target / "manifest.json", manifest_base)
    return {
        "documentos": len(documents), "vetores": vetores.vector_count,
        "faiss_posicoes_verificadas": True, "vetores_ausentes": absent,
        "origens_vetores": manifest_base["origens_vetores"],
        "modelos_declarados_origem": manifest_base["modelos_declarados_origem"],
        "encoding_config_disponivel": manifest_base["encoding_config_disponivel"],
        "encoding_verificado": False,
        "faiss_origem_sem_payload": vetores.unmapped, "vetores_recuperados_de_fontes": vetores.recovered,
        "vectors_sha256": file_sha256(target / "vectors.jsonl"),
        "faiss_sha256": file_sha256(target / "vectors.faiss"),
    }


def _gravar_manifest_global(destino: Path, original: dict[str, Any], bases: dict[str, Any], report: dict[str, Any],
                            global_hash: Any, modelo_declarado: str | None) -> None:
    """Manifest do índice inteiro, com a proveniência dos vetores; o resumo também vai para o relatório."""
    global_manifest = {
        **original, "source_file": _relative(destino / "documents.jsonl"),
        "source_sha256": global_hash.hexdigest(),
        "total_chunks": sum(b["documentos"] for b in report["bases"].values()),
        "total_vectors": sum(b["vetores"] for b in report["bases"].values()),
        "bases": [{"base_id": base} for base in sorted(bases)],
        "snapshot_id": destino.name, "built_at": report["gerado_em"],
        "vetores_embedados": 0,
        "vetores_reaproveitados": sum(b["vetores"] for b in report["bases"].values()),
        "promocao_autorizada": False,
    }
    global_manifest.pop("faiss_index", None)  # Só há índices por base neste layout.
    global_manifest.pop("base_id", None)
    declared_models = sorted({
        model for base_report in report["bases"].values()
        for model in base_report["modelos_declarados_origem"]
    })
    global_manifest["embedding_model"] = declared_models[0] if len(declared_models) == 1 else "mixed_or_unknown"
    if modelo_declarado:
        global_manifest["embedding_model_declarado_pelo_operador"] = modelo_declarado
        global_manifest["declaracao_modelo"] = "declaração do operador; não substitui os modelos declarados nas fontes"
    origins = ("candidato", "referencia", "recuperacao")
    origin_totals = {
        label: sum(base_report["origens_vetores"][label] for base_report in report["bases"].values())
        for label in origins
    }
    provenance_summary = {
        "total_vetores": sum(origin_totals.values()),
        "por_origem": origin_totals,
        "por_base": {base_id: base_report["origens_vetores"]
                     for base_id, base_report in report["bases"].items()},
        "homogeneidade_modelo_comprovada": False,
        "homogeneidade_configuracao_comprovada": False,
    }
    global_manifest["proveniencia_vetores"] = provenance_summary
    global_manifest["proveniencia_encoding_completa"] = False
    global_manifest["nota_proveniencia_encoding"] = (
        "Manifests preservam declarações/configurações de origem, mas não comprovam encoding por vetor."
    )
    gravar_json(destino / "index" / "manifest.json", global_manifest)
    report["proveniencia_vetores"] = provenance_summary


def preparar_sql(candidato: Path, staging_origem: Path) -> dict[str, Any]:
    """Construtor canônico, preservando vínculos e extras do staging em arquivo novo."""
    from contextlib import closing
    import sqlite3
    from src.structured_db import Banco, construir_banco, validar_banco

    candidato, staging_origem = Path(candidato), Path(staging_origem)
    output = candidato / "base_estruturada.sqlite"
    if output.exists():
        raise FileExistsError(f"banco já preparado: {output}")
    banco = Banco(sqlite_path=output)
    summary = construir_banco(candidato / "documents.jsonl", banco)
    with closing(sqlite3.connect(output, uri=True)) as connection, connection:
        connection.execute("ATTACH DATABASE ? AS origem", [staging_origem.resolve().as_uri() + "?mode=ro"])
        missing = connection.execute(
            "SELECT COUNT(*) FROM origem.contratacoes o LEFT JOIN contratacoes n "
            "ON n.numero_controle_pncp=o.numero_controle_pncp WHERE n.numero_controle_pncp IS NULL"
        ).fetchone()[0]
        critical = ("tipo_registro", "numero_controle_compra", "valor_contratado", "valor_estimado", "valor_homologado", "modalidade", "uf")
        differences = connection.execute(
            "SELECT o.numero_controle_pncp FROM origem.contratacoes o JOIN contratacoes n "
            "ON n.numero_controle_pncp=o.numero_controle_pncp WHERE "
            + " OR ".join(f"n.{column} IS NOT o.{column}" for column in critical) + " LIMIT 20"
        ).fetchall()
        if missing or differences:
            raise ValueError(f"reconstrução SQL diverge do staging: ausentes={missing}; amostra={differences}")
        current = {row[1] for row in connection.execute("PRAGMA table_info(contratacoes)")}
        extras = []
        for _, name, sql_type, *_ in connection.execute("PRAGMA origem.table_info(contratacoes)").fetchall():
            if name in current:
                continue
            if not name.replace("_", "").isalnum() or sql_type not in {"TEXT", "INTEGER", "REAL"}:
                raise ValueError(f"coluna de staging não permitida: {name}/{sql_type}")
            connection.execute(f"ALTER TABLE contratacoes ADD COLUMN {name} {sql_type}")
            extras.append(name)
        if extras:
            connection.execute(
                f"UPDATE contratacoes SET ({', '.join(extras)}) = "
                f"(SELECT {', '.join(extras)} FROM origem.contratacoes o "
                "WHERE o.numero_controle_pncp=contratacoes.numero_controle_pncp) "
                "WHERE numero_controle_pncp IN (SELECT numero_controle_pncp FROM origem.contratacoes)"
            )
        has_pending = connection.execute(
            "SELECT name FROM origem.sqlite_master WHERE type='table' AND name='vinculos_pendentes'"
        ).fetchone()
        if has_pending:
            connection.execute("CREATE TABLE vinculos_pendentes AS SELECT * FROM origem.vinculos_pendentes")
        connection.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
            ("corpus_version", "v7-reparo"), ("staging_source_sha256", file_sha256(staging_origem)),
        ])
        summary["colunas_staging_preservadas"] = extras
        summary["tipos_registro"] = dict(connection.execute("SELECT tipo_registro,COUNT(*) FROM contratacoes GROUP BY 1"))
        summary["relacoes_compra_contrato"] = list(connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT child.numero_controle_compra), "
            "COALESCE(SUM(EXISTS (SELECT 1 FROM contratacoes parent "
            "WHERE parent.numero_controle_pncp=child.numero_controle_compra)),0), "
            "COALESCE(SUM(NOT EXISTS (SELECT 1 FROM contratacoes parent "
            "WHERE parent.numero_controle_pncp=child.numero_controle_compra)),0) "
            "FROM contratacoes child WHERE child.numero_controle_compra IS NOT NULL"
        ).fetchone())
        if has_pending:
            summary["vinculos_pendentes"] = connection.execute(
                "SELECT COUNT(*) FROM vinculos_pendentes"
            ).fetchone()[0]
            summary["vinculos_pendentes_diagnostico"] = list(connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT p.compra), COUNT(DISTINCT p.registro), "
                "SUM(EXISTS (SELECT 1 FROM contratacoes c WHERE c.numero_controle_pncp=p.compra)), "
                "SUM(EXISTS (SELECT 1 FROM contratacoes c WHERE c.numero_controle_pncp=p.registro)), "
                "SUM(EXISTS (SELECT 1 FROM contratacoes compra WHERE compra.numero_controle_pncp=p.compra) "
                "AND EXISTS (SELECT 1 FROM contratacoes registro WHERE registro.numero_controle_pncp=p.registro)) "
                "FROM vinculos_pendentes p"
            ).fetchone())
            summary["vinculos_pendentes_escopo"] = (
                "Tabela preservada do staging para rastreabilidade; structured_query não a consulta. "
                "Consultas usam numero_controle_compra nos registros presentes no candidato."
            )
        else:
            summary["vinculos_pendentes"] = 0
    validar_banco(candidato / "documents.jsonl", banco)
    summary["corpus_version"] = "v7-reparo"
    summary["staging_preservado"] = True
    gravar_json(candidato / "sql_reparo.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidato", type=Path, required=True)
    parser.add_argument("--destino", type=Path, required=True)
    parser.add_argument("--referencia-documents", type=Path, required=True)
    parser.add_argument("--referencia-index", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("corpus_manifest.yaml"))
    parser.add_argument("--modelo-declarado")
    parser.add_argument("--fontes-index", type=Path, nargs="*", default=[])
    parser.add_argument("--preparar-sql", action="store_true")
    args = parser.parse_args(argv)
    report = reparar(args.candidato, args.destino, referencia_documents=args.referencia_documents,
                     referencia_index=args.referencia_index, manifest=args.manifest, modelo_declarado=args.modelo_declarado,
                     fontes_index=tuple(args.fontes_index))
    if args.preparar_sql:
        report["sql"] = preparar_sql(args.destino, args.candidato / "base_estruturada.sqlite")
        gravar_json(args.destino / "reparo.json", report)
    print(json.dumps({"cobertura_vetorial_completa": report["cobertura_vetorial_completa"],
                      "bases": {b: {k: v for k, v in r.items() if k != "vetores_ausentes"} for b, r in report["bases"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
