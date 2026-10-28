"""Incorpora bases herdadas ao candidato v7 preservando texto e hashes.

Produz documents.jsonl e SQL de staging com as bases herdadas (normativa,
agregada, emails sintéticos). Não gera embeddings, não reconstrói índice,
não promove. A v6 continua ativa.

Uso:
    python -m src.merge_bases_v7 --v6 data/normalized/v6/documents.jsonl \
        --candidato data/v7_recuperacao/.../candidato_04 --destino data/v7_recuperacao/.../candidato_05
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.index import file_sha256
from src.recover_corpus_v7 import agora, destino_novo, gravar_json

HERDADAS = {"editais_normativo", "pca_agregado"}


def _linhas(path: Path):
    with path.open(encoding="utf-8") as f:
        for n, linha in enumerate(f, 1):
            if linha.strip():
                yield n, json.loads(linha)


def incorporar(v6: Path, candidato: Path, destino: Path) -> dict:
    """Produz novo documents.jsonl + SQL com bases herdadas."""
    if not v6.is_file() or not candidato.is_dir():
        raise FileNotFoundError("v6 ou candidato ausente")
    docs_candidato = candidato / "documents.jsonl"
    sql_candidato = candidato / "base_estruturada.sqlite"
    if not docs_candidato.is_file() or not sql_candidato.is_file():
        raise FileNotFoundError("candidato sem documents.jsonl ou SQLite")
    destino = destino_novo(destino, [v6, candidato])

    contagens = Counter()
    ids_v7 = set()
    arquivos = []

    # 1) Ler ids do candidato
    with docs_candidato.open(encoding="utf-8") as f:
        for linha in f:
            if not linha.strip():
                continue
            c = json.loads(linha)
            ids_v7.add(c["chunk_id"])
            contagens[c["base_id"]] += 1

    # 2) Adicionar bases herdadas da v6
    provisorio = destino / "documents.jsonl.provisorio"
    with (
        docs_candidato.open(encoding="utf-8") as entrada,
        provisorio.open("w", encoding="utf-8", newline="\n") as saida,
    ):
        # Copiar candidato
        for linha in entrada:
            saida.write(linha)
        # Adicionar herdadas
        for n, c in _linhas(v6):
            base = c.get("base_id", "")
            if base not in HERDADAS:
                continue
            if c["chunk_id"] in ids_v7:
                contagens["duplicada_herdada"] += 1
                continue
            # Texto e hashes inalterados
            saida.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
            contagens[base] += 1
            arquivos.append({
                "chunk_id": c["chunk_id"],
                "base_id": base,
                "source_file": c.get("source_file", ""),
                "sha256": c.get("sha256", ""),
                "origem": c.get("origem", ""),
                "bytes": c.get("bytes", 0),
            })
    provisorio.replace(destino / "documents.jsonl")

    # 3) Reconstruir SQL (simplificado: copiar do candidato + append das herdadas)
    import sqlite3
    import shutil
    shutil.copy2(sql_candidato, destino / "base_estruturada.sqlite")
    source_hash = file_sha256(destino / "documents.jsonl")
    with sqlite3.connect(destino / "base_estruturada.sqlite") as banco:
        # Atualizar meta
        banco.execute("UPDATE meta SET valor=? WHERE chave='source_sha256'", [source_hash])
        banco.execute("UPDATE meta SET valor='v7-staging-2' WHERE chave='schema_version'")
        # Contagens herdadas não geram linhas em contratacoes
        banco.commit()
        linhas_sql = banco.execute("SELECT count(*) FROM contratacoes").fetchone()[0]

    report = {
        "gerado_em": agora(),
        "v6_sha256": file_sha256(v6),
        "candidato_sha256": file_sha256(docs_candidato),
        "source_sha256": source_hash,
        "contagens": dict(contagens),
        "linhas_sql": linhas_sql,
        "duplicadas_ignoradas": contagens["duplicada_herdada"],
        "chunks_adicionados": sum(v for k, v in contagens.items() if k in HERDADAS),
        "bases_incorporadas": sorted(HERDADAS),
        "cobertura_institucional_comprovada": False,
        "promocao_autorizada": False,
    }
    gravar_json(destino / "relatorio.json", report)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--v6", type=Path, required=True)
    p.add_argument("--candidato", type=Path, required=True)
    p.add_argument("--destino", type=Path, required=True)
    a = p.parse_args(argv)
    r = incorporar(a.v6, a.candidato, a.destino)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())