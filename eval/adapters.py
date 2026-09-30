"""Adaptadores que plugam módulos já implementados do pipeline no harness de eval/run_eval.py.

``rota_apenas`` avalia só o estágio ``roteamento`` (``src/router.py``): é
determinístico, offline e não chama o LLM — o suficiente para medir se o
roteador escolhe as bases certas e recusa nos casos de recusa/fora de escopo
do golden set, sem o custo (tempo, tokens) de rodar a geração.

Agora que ``src/retrieve.py`` existe, o pipeline completo (roteamento +
recuperação híbrida + geração) roda direto via ``src/pipeline.py:responder``
— já compatível com o contrato de ``eval/run_eval.py`` sem precisar de
adaptador:

    py .\\eval\\run_eval.py run --pipeline src.pipeline:responder
"""

from __future__ import annotations

from typing import Any
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from src.router import inferir_motivo_recusa, rotear_pergunta


def rota_apenas(pergunta: str) -> dict[str, Any]:
    """Roda só ``rotear_pergunta`` e devolve o formato que ``evaluate_case`` espera."""
    decisao = rotear_pergunta(pergunta)
    bases = [base.value for base in decisao.bases_selecionadas]
    is_refusal = not bases
    motivo = inferir_motivo_recusa(decisao.justificativa) if is_refusal else None
    return {
        "bases_consultadas": bases,
        "is_refusal": is_refusal,
        "refusal_reason": motivo.value if motivo else None,
    }


def cli_resposta(pergunta: str) -> dict[str, Any]:
    """Integração explícita ECOA via os comandos existentes, sem mudar a CLI.

    Mantém PostgreSQL offline: a análise numérica legada usa somente SQLite.
    A resposta inclui rota, latência e logs para revisão contra as fontes.
    """
    root = Path(__file__).resolve().parents[1]
    logs = root / "logs"
    offsets = {path: path.stat().st_size for path in logs.glob("pipeline-*.jsonl")}
    env = {**os.environ, "DATABASE_URL": "", "PYTHONIOENCODING": "utf-8"}
    payloads, latencies = {}, {}
    for command in ("rota", "perguntar"):
        started = time.perf_counter()
        result = subprocess.run(
            [sys.executable, str(root / "app" / "cli.py"), command, pergunta],
            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", check=True,
        )
        latencies[command] = (time.perf_counter() - started) * 1000
        payloads[command] = json.loads(result.stdout[result.stdout.index("\n{") + 1:].strip())
    events = []
    for path in logs.glob("pipeline-*.jsonl"):
        with path.open("rb") as handle:
            handle.seek(offsets.get(path, 0))
            events.extend(json.loads(line) for line in handle.read().decode("utf-8").splitlines() if line.strip())
    response = payloads["perguntar"]
    response["rota_cli"] = payloads["rota"]
    response["latencias_cli_ms"] = latencies
    response["eventos"] = events
    return response


__all__ = ["rota_apenas", "cli_resposta"]
