"""Reverte a troca V7 -> V6 restaurando o backup mais recente.

Uso:
    python scripts/rollback_v6.py
"""
from pathlib import Path
import shutil
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def encontrar_backup() -> Path:
    backups = sorted(DATA.glob("backup_v6_*"), reverse=True)
    if not backups:
        raise FileNotFoundError("Nenhum backup V6 encontrado em data/backup_v6_*")
    return backups[0]


def reverter(backup: Path) -> None:
    cur_docs = DATA / "normalized" / "documents.jsonl"
    cur_sql = DATA / "structured" / "base_estruturada.sqlite"
    cur_idx = ROOT / "index"

    print(f"Revertendo de: {backup}")

    if (backup / "documents.jsonl").exists():
        shutil.copy2(backup / "documents.jsonl", cur_docs)
        print(f"  documents.jsonl restaurado")
    if (backup / "base_estruturada.sqlite").exists():
        shutil.copy2(backup / "base_estruturada.sqlite", cur_sql)
        print(f"  base_estruturada.sqlite restaurado")
    if (backup / "index").exists():
        if cur_idx.exists():
            shutil.rmtree(cur_idx)
        shutil.copytree(backup / "index", cur_idx)
        print(f"  index/ restaurado")

    print("\nRollback concluído. Sistema voltou ao V6.")


if __name__ == "__main__":
    backup = encontrar_backup()
    reverter(backup)