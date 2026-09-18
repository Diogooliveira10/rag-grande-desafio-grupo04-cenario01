"""Configurações compartilhadas do projeto.

Os valores podem ser sobrescritos por variáveis no arquivo ``.env``.
Não coloque credenciais reais neste arquivo; use ``.env`` localmente.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


# Endpoint e modelos compartilhados da turma.
OLLAMA_URL = _env("OLLAMA_URL", "http://ecoa-llm.duckdns.org:11434/v1")
OLLAMA_API_KEY = _env("OLLAMA_API_KEY", "ollama")
GENERATION_MODEL = _env("GENERATION_MODEL", "qcwind/qwen3-8b-instruct-Q4-K-M")
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "xagi/qwen3-embedding:0.6b-q4_k_m")

# Parâmetros de rede e armazenamento usados pela coleta e ingestão.
REQUEST_TIMEOUT = float(_env("REQUEST_TIMEOUT", "30"))
REQUEST_ATTEMPTS = int(_env("REQUEST_ATTEMPTS", "3"))
RAW_DATA_DIR = ROOT_DIR / "data" / "raw" / "v1"
NORMALIZED_DATA_DIR = ROOT_DIR / "data" / "normalized"
LOGS_DIR = ROOT_DIR / "logs"
