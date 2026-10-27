"""Adaptadores que plugam módulos já implementados do pipeline no harness de eval/run_eval.py.

``rota_apenas`` avalia só o estágio ``roteamento`` (``src/router.py``): é
determinístico, offline e não chama o LLM — o suficiente para medir se o
roteador escolhe as bases certas e recusa nos casos de recusa/fora de escopo
do golden set, sem o custo (tempo, tokens) de rodar a geração.

O pipeline completo (roteamento + recuperação híbrida + geração) roda direto
via ``src/pipeline.py:responder``, já compatível com o contrato de
``eval/run_eval.py``. ``pipeline_completo`` faz a mesma chamada com um
``run_id`` próprio e depois lê o log do pipeline para anexar à resposta
(chave ``_metricas``) os tokens e a latência de cada etapa:

    py .\\eval\\run_eval.py run --pipeline eval.adapters:pipeline_completo

``cli_resposta`` roda a pergunta pelos comandos ``rota`` e ``perguntar`` da
CLI (``app/cli.py``), com PostgreSQL desligado, e junta rota, latência e os
eventos do log de cada chamada.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from src.obs import default_logger, new_run_id
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


def metricas_do_log(run_id: str, log_paths: Iterable[Path]) -> dict[str, Any]:
    """Soma tokens e latência por etapa dos registros do log com esse ``run_id``."""
    etapas: dict[str, dict[str, int]] = defaultdict(lambda: {"tokens": 0, "latencia_ms": 0})
    for path in log_paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("run_id") != run_id:
                    continue
                etapa = etapas[str(record.get("stage"))]
                etapa["tokens"] += int((record.get("tokens") or {}).get("total", 0))
                etapa["latencia_ms"] += int(record.get("latencia_ms") or 0)
    return {
        "run_id": run_id,
        "tokens_total": sum(etapa["tokens"] for etapa in etapas.values()),
        "etapas": dict(etapas),
    }


def pipeline_completo(pergunta: str, historico: Optional[list[dict[str, str]]] = None) -> dict[str, Any]:
    """Roda ``src.pipeline.responder`` e anexa ``_metricas`` lidas do log do pipeline.

    ``historico`` vem do ``depende_de`` do caso: as perguntas anteriores da
    conversa, para a pergunta de continuação ser reescrita como no frontend.
    """
    from src.pipeline import responder

    run_id = new_run_id()
    inicio = datetime.now().astimezone()
    resposta = responder(pergunta, run_id=run_id, historico=historico)
    # Uma pergunta que atravessa a meia-noite grava em dois arquivos diários.
    paths = {default_logger.log_path(inicio), default_logger.log_path()}
    resposta["_metricas"] = metricas_do_log(run_id, sorted(paths))
    return resposta


def _preparar_como_a_api() -> None:
    """A pré-carga que a API faz ao subir (``app/api.py``), antes da primeira pergunta.

    Sem ela, a primeira pergunta do golden pagava o carregamento do cross-encoder
    (16 a 36 s nos logs de 26/10) e a pergunta que lê o documents.jsonl pagava a
    montagem do índice da BM25 (~30 s), o que não acontece com o backend no ar.
    """
    import config
    from src import rerank, retrieve

    if config.RERANK_ENABLED and config.RERANK_PRELOAD:
        rerank.pre_carregar()
    if config.INDEX_PRELOAD:
        retrieve.pre_carregar_indices()


# eval/run_eval.py chama ``preparar`` uma vez, fora da medição de cada pergunta.
pipeline_completo.preparar = _preparar_como_a_api


def cli_resposta(pergunta: str, *, openrouter_only: bool = False) -> dict[str, Any]:
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
            [sys.executable, str(root / "app" / "cli.py"), command,
             *(["--openrouter-only"] if openrouter_only else []), pergunta],
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
    run_ids = {event['run_id'] for event in events
               if event.get('input') == pergunta and event['run_id'] != 'sem-run-id'
               and event['stage'] in {'query_analysis', 'roteamento', 'geracao'}}
    response["eventos"] = [event for event in events if event['run_id'] in run_ids]
    return response


__all__ = ["rota_apenas", "pipeline_completo", "metricas_do_log", "cli_resposta"]
