#!/usr/bin/env bash
# Bootstrap do backend RAG em uma máquina nova (Linux/macOS).
# Cria o .venv, instala o lock de dependências, copia .env.example -> .env
# quando faltar, e confere os artefatos da v6.
# Uso: bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "== Criando .venv =="
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements-lock.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "== .env criado a partir de .env.example (defaults: endpoint da turma) =="
fi

.venv/bin/python scripts/bootstrap_check.py
echo
echo "Pronto. Suba o backend com:"
echo "  .venv/bin/python -m uvicorn app.api:app --host 127.0.0.1 --port 8080"
