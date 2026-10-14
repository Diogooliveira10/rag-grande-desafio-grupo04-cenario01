'''Confere se um clone novo tem tudo para consultar o corpus v6.

A v6 está versionada no Git (SQLite estruturado, índice FAISS, snapshot de
embeddings, documentos normalizados), então não é preciso recoletar para
consultar. Este script verifica os artefatos, resolve as configurações ativas
(provedor de embeddings, modo do LLM, orçamento de contexto) e falha com uma
mensagem acionável quando algo essencial falta.

Uso: python scripts/bootstrap_check.py
'''

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

BASES = (
    "editais_normativo",
    "contratos_estruturado",
    "atas_conversacional",
    "pca_agregado",
)

problemas: list[str] = []


def conferir(caminho: Path, descricao: str) -> None:
    if caminho.exists():
        print(f"  [ok]      {descricao}")
    else:
        print(f"  [FALTA]   {descricao}  ({caminho.relative_to(RAIZ)})")
        problemas.append(descricao)


print("== Artefatos da v6 ==")
conferir(RAIZ / "data" / "structured" / "base_estruturada.sqlite", "SQLite estruturado")
for base in BASES:
    pasta = RAIZ / "index" / base
    conferir(pasta / "manifest.json", f"manifesto do índice ({base})")
    conferir(pasta / "vectors.faiss", f"vetores FAISS ({base})")
    conferir(pasta / "vectors.jsonl", f"vetores JSONL ({base})")
snapshots = sorted((RAIZ / "data" / "embeddings").glob("2*/"))
if snapshots:
    print(f"  [ok]      snapshot de embeddings ({snapshots[-1].name})")
else:
    print("  [AVISO]   nenhum snapshot em data/embeddings/")
conferir(RAIZ / "eval" / "golden_set.jsonl", "golden set")

print("\n== Configuração resolvida ==")
import config  # noqa: E402  (depois dos caminhos)

endpoint, _, modelo = config.embedding_endpoint()
print(f"  LLM_PROVIDER_MODE      = {config.LLM_PROVIDER_MODE}")
print(f"  GENERATION_MODEL       = {config.GENERATION_MODEL}")
print(f"  orçamento de contexto  = {config.GENERATION_CONTEXT_CHARS} chars")
print(f"  EMBEDDING_PROVIDER     = {config.EMBEDDING_PROVIDER}")
print(f"  endpoint de embedding  = {endpoint} (modelo {modelo})")
print(f"  rerank                 = {config.RERANK_PROVIDER}/{config.RERANK_MODEL}")
if not (RAIZ / ".env").exists():
    print("  [AVISO]   .env ausente; usando os defaults (endpoint da turma).")
    print("            copie .env.example para .env para configurar chaves/modos.")

if problemas:
    print(f"\n{len(problemas)} item(ns) essencial(is) ausente(s).", file=sys.stderr)
    sys.exit(1)
print("\nTudo no lugar para consultar o corpus v6.")
