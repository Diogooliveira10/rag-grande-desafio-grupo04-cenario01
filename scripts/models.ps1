# Baixa os modelos externos (reranker + GGUF do Qwen3-Embedding) e imprime o
# comando do llama-server para o modo local de embeddings de consulta.
# Uso: powershell -ExecutionPolicy Bypass -File scripts\models.ps1 [--only reranker|gguf] [--dest D:\LLM]
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$py = ".\.venv\Scripts\python"
if (-not (Test-Path $py)) { $py = "python" }
& $py scripts\download_models.py @args
