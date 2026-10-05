"""Monta o caminho de uma pergunta pelo sistema a partir do log estruturado.

Cada etapa do pipeline grava uma linha em ``logs/pipeline-AAAA-MM-DD.jsonl``
(``src/obs.py``), todas com o mesmo ``query_id``. ``montar_trace`` junta essas
linhas em ordem e diz, em cada etapa, se ela usou a base SQL ou a base
vetorial, com input, output e os detalhes que a etapa registrou (trechos com
score, SQL executado e linhas devolvidas, prompt e resposta do LLM).

Serve o ``GET /api/v1/trace/{query_id}`` de ``app/api.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.obs import Stage, default_logger

# Qual base cada etapa consulta. BM25 é busca por palavras sobre os mesmos
# trechos indexados da base vetorial; as demais etapas não consultam base.
_BASE_DA_ETAPA = {
    Stage.RETRIEVAL_DENSO.value: "vetorial",
    Stage.RETRIEVAL_BM25.value: "vetorial",
    Stage.FUSAO_RRF.value: "vetorial",
    Stage.RERANK.value: "vetorial",
    Stage.CONSULTA_SQL.value: "sql",
}

_ETAPAS_VETORIAIS = {stage for stage, base in _BASE_DA_ETAPA.items() if base == "vetorial"}


def _registros_da_consulta(query_id: str, logs_dir: Path) -> list[dict[str, Any]]:
    registros: list[dict[str, Any]] = []
    for arquivo in sorted(logs_dir.glob("pipeline-*.jsonl")):
        with arquivo.open(encoding="utf-8") as linhas:
            for linha in linhas:
                # Filtro barato antes do json.loads: o log cresce a cada pergunta.
                if query_id not in linha:
                    continue
                try:
                    registro = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if registro.get("query_id") == query_id:
                    registros.append(registro)
    # Ordem estável por horário; empates mantêm a ordem de gravação.
    return sorted(registros, key=lambda registro: str(registro.get("ts", "")))


def _resumo_derivado(etapas: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumo para perguntas registradas antes do stage ``resumo_consulta``."""
    stages = {etapa["stage"] for etapa in etapas}
    return {
        "caminho": None,
        "consultou_sql": Stage.CONSULTA_SQL.value in stages,
        "consultou_vetorial": bool(stages & _ETAPAS_VETORIAIS),
    }


def montar_trace(query_id: str, logs_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Caminho da pergunta ``query_id``, ou ``None`` se ela não está no log."""
    registros = _registros_da_consulta(query_id, Path(logs_dir or default_logger.logs_dir))
    if not registros:
        return None

    etapas = [
        {
            "ordem": ordem,
            "stage": registro.get("stage"),
            "base": _BASE_DA_ETAPA.get(registro.get("stage")),
            "status": registro.get("status"),
            "ts": registro.get("ts"),
            "latencia_ms": registro.get("latencia_ms", 0),
            "input": registro.get("input"),
            "output": registro.get("output"),
            "tokens": registro.get("tokens"),
            "modelo": registro.get("modelo"),
            "detalhes": registro.get("extra") or {},
        }
        for ordem, registro in enumerate(registros, start=1)
    ]
    resumo = next(
        (etapa["output"] for etapa in reversed(etapas)
         if etapa["stage"] == Stage.RESUMO_CONSULTA.value and isinstance(etapa["output"], dict)),
        None,
    ) or _resumo_derivado(etapas)
    pergunta = next((etapa["input"] for etapa in etapas if isinstance(etapa["input"], str)), None)
    return {
        "query_id": query_id,
        "run_ids": list(dict.fromkeys(str(registro.get("run_id")) for registro in registros)),
        "pergunta": pergunta,
        "inicio": etapas[0]["ts"],
        "fim": etapas[-1]["ts"],
        "latencia_total_ms": sum(int(etapa["latencia_ms"] or 0) for etapa in etapas),
        "resumo": resumo,
        "etapas": etapas,
    }


__all__ = ["montar_trace"]
