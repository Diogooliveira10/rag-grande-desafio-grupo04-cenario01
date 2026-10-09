"""Monta o caminho de uma pergunta pelo sistema a partir do log estruturado.

Cada etapa do pipeline grava uma linha em ``logs/pipeline-AAAA-MM-DD.jsonl``
(``src/obs.py``), todas com o mesmo ``query_id``. ``montar_trace`` junta essas
linhas em ordem e diz, em cada etapa, se ela usou a base SQL ou a base
vetorial, com input, output e os detalhes que a etapa registrou (trechos com
score, SQL executado e linhas devolvidas, prompt e resposta do LLM).

Os registros também levam o ``session_id`` da conversa: ``montar_trace_sessao``
lista, em ordem, as perguntas de uma sessão com o resumo de cada uma.

Serve o ``GET /api/v1/trace/{query_id}`` e o
``GET /api/v1/sessions/{session_id}/trace`` de ``app/api.py``.
"""

from __future__ import annotations

import json
import re
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

# O trace vai para o usuário do site. Logs gravados antes de
# ``Banco.rotulo_publico`` trazem o endereço do servidor Postgres; ele sai daqui.
_ENDERECO_POSTGRES = re.compile(r"postgres(?:ql)?://[^\s\"']+")


def _sem_endereco_de_banco(valor: Any) -> Any:
    if isinstance(valor, str):
        return _ENDERECO_POSTGRES.sub("postgres", valor)
    if isinstance(valor, dict):
        return {chave: _sem_endereco_de_banco(item) for chave, item in valor.items()}
    if isinstance(valor, list):
        return [_sem_endereco_de_banco(item) for item in valor]
    return valor


def _registros(campo: str, valor: str, logs_dir: Path) -> list[dict[str, Any]]:
    """Linhas do log com ``registro[campo] == valor``, em ordem de horário."""
    registros: list[dict[str, Any]] = []
    for arquivo in sorted(logs_dir.glob("pipeline-*.jsonl")):
        with arquivo.open(encoding="utf-8") as linhas:
            for linha in linhas:
                # Filtro barato antes do json.loads: o log cresce a cada pergunta.
                if valor not in linha:
                    continue
                try:
                    registro = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if registro.get(campo) == valor:
                    registros.append(registro)
    # Ordem estável por horário; empates mantêm a ordem de gravação.
    return sorted(registros, key=lambda registro: str(registro.get("ts", "")))


_CHUNKS_NO_OUTPUT = re.compile(r"^(\d+) chunks?\b")


def _evidencias_vetoriais(etapas: list[dict[str, Any]]) -> dict[str, int] | int | None:
    """Evidências da busca vetorial que chegaram à geração, num log antigo.

    Usa a última etapa vetorial: a lista ``evidencias`` do ``extra``, contada
    por base como no ``resumo_consulta``; sem ela, o total de "N chunks" do
    output, que não diz a base de cada trecho.
    """
    for etapa in reversed(etapas):
        if etapa["base"] != "vetorial":
            continue
        evidencias = etapa["detalhes"].get("evidencias")
        if isinstance(evidencias, list):
            por_base: dict[str, int] = {}
            for evidencia in evidencias:
                if not isinstance(evidencia, dict) or str(evidencia.get("chunk_id", "")).startswith("sql:"):
                    continue
                base_id = str(evidencia.get("base_id"))
                por_base[base_id] = por_base.get(base_id, 0) + 1
            return por_base
        total = _CHUNKS_NO_OUTPUT.match(str(etapa["output"] or ""))
        if total:
            return int(total.group(1))
    return None


def _resumo_derivado(etapas: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumo para perguntas registradas antes do stage ``resumo_consulta``.

    Tira do que as etapas antigas já gravavam o que der: evidências SQL e
    vetoriais, confiança e recusa. As fontes citadas não estão nesses logs
    (só a contagem, em ``num_fontes``).
    """
    stages = {etapa["stage"] for etapa in etapas}
    resumo: dict[str, Any] = {
        "caminho": None,
        "consultou_sql": Stage.CONSULTA_SQL.value in stages,
        "consultou_vetorial": bool(stages & _ETAPAS_VETORIAIS),
    }
    chunks_sql = [etapa["detalhes"].get("chunks") for etapa in etapas
                  if etapa["stage"] == Stage.CONSULTA_SQL.value]
    if any(isinstance(chunks, int) for chunks in chunks_sql):
        resumo["evidencias_sql"] = sum(chunks for chunks in chunks_sql if isinstance(chunks, int))
    vetorial = _evidencias_vetoriais(etapas)
    if vetorial is not None:
        resumo["evidencias_vetorial"] = vetorial
    # A validação revê o que a geração decidiu; vale a última que gravou.
    for etapa in reversed(etapas):
        if etapa["stage"] not in (Stage.VALIDACAO.value, Stage.GERACAO.value):
            continue
        detalhes = etapa["detalhes"]
        for campo in ("is_refusal", "confidence_level"):
            if campo in detalhes:
                resumo.setdefault(campo, detalhes[campo])
        if isinstance(detalhes.get("num_sources"), int):
            resumo.setdefault("num_fontes", detalhes["num_sources"])
    return resumo


def montar_trace(query_id: str, logs_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Caminho da pergunta ``query_id``, ou ``None`` se ela não está no log."""
    registros = _registros("query_id", query_id, Path(logs_dir or default_logger.logs_dir))
    if not registros:
        return None
    return _sem_endereco_de_banco(_trace_dos_registros(query_id, registros))


def _trace_dos_registros(query_id: str, registros: list[dict[str, Any]]) -> dict[str, Any]:

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
        "session_id": next((r["session_id"] for r in registros if r.get("session_id")), None),
        "run_ids": list(dict.fromkeys(str(registro.get("run_id")) for registro in registros)),
        "pergunta": pergunta,
        "inicio": etapas[0]["ts"],
        "fim": etapas[-1]["ts"],
        "latencia_total_ms": sum(int(etapa["latencia_ms"] or 0) for etapa in etapas),
        "resumo": resumo,
        "etapas": etapas,
    }


def montar_trace_sessao(session_id: str, logs_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Perguntas da sessão em ordem, com o resumo de cada uma, ou ``None``.

    Os detalhes de cada etapa ficam no trace da pergunta (``montar_trace``);
    aqui vai só o caminho da conversa: o que foi perguntado, em que ordem, e
    se cada pergunta consultou a base SQL e a vetorial. O feedback dado
    depois da resposta (outro ``run``) fica junto da pergunta a que se refere.
    """
    registros = _registros("session_id", session_id, Path(logs_dir or default_logger.logs_dir))
    if not registros:
        return None
    por_pergunta: dict[str, list[dict[str, Any]]] = {}
    for registro in registros:
        por_pergunta.setdefault(str(registro.get("query_id")), []).append(registro)
    perguntas = []
    for query_id, da_pergunta in por_pergunta.items():
        trace = _trace_dos_registros(query_id, da_pergunta)
        perguntas.append({
            "ordem": len(perguntas) + 1,
            **{chave: trace[chave] for chave in
               ("query_id", "pergunta", "inicio", "fim", "latencia_total_ms", "resumo")},
            "etapas": [etapa["stage"] for etapa in trace["etapas"]],
        })
    return _sem_endereco_de_banco({
        "session_id": session_id,
        "inicio": perguntas[0]["inicio"],
        "fim": perguntas[-1]["fim"],
        "total_perguntas": len(perguntas),
        "consultou_sql": sum(1 for p in perguntas if p["resumo"].get("consultou_sql")),
        "consultou_vetorial": sum(1 for p in perguntas if p["resumo"].get("consultou_vetorial")),
        "perguntas": perguntas,
    })


__all__ = ["montar_trace", "montar_trace_sessao"]
