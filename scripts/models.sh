#!/usr/bin/env bash
# Baixa os modelos externos (reranker + GGUF do Qwen3-Embedding) e imprime o
# comando do llama-server para o modo local de embeddings de consulta.
# Uso: bash scripts/models.sh [--only reranker|gguf] [--dest DIR]
set -euo pipefail
cd "$(dirname "$0")/.."

py=".venv/bin/python"
[ -x "$py" ] || py="python3"
exec "$py" scripts/download_models.py "$@"
