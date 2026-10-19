"""Monta o caminho de uma pergunta pelo sistema a partir do log estruturado.

Cada etapa do pipeline grava uma linha em ``logs/pipeline/pipeline-AAAA-MM-DD.jsonl``
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
import logging
import re
import sqlite3
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

import config
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
    """Indexed lookup; only changed/new JSONL bytes require import."""
    from src.log_store import EventStore
    if logs_dir.resolve() == Path(default_logger.logs_dir).resolve():
        default_logger.flush()
    legacy = [config.LOGS_DIR] if logs_dir == config.PIPELINE_LOGS_DIR else []
    try:
        registros = EventStore(logs_dir).records(campo, valor, legacy)
        return sorted(registros, key=_timestamp_key)
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).warning("Índice de trace indisponível; leitura JSONL de compatibilidade")
        return _registros_jsonl(campo, valor, logs_dir)


def _registros_jsonl(campo: str, valor: str, logs_dir: Path) -> list[dict[str, Any]]:
    """Fallback/legacy reader when the derived index cannot be used."""
    registros: list[dict[str, Any]] = []
    # Transitional reader: only the actual configured directory may also read
    # legacy root files. Isolated test/custom directories never read real logs.
    diretorios = [logs_dir]
    if logs_dir == config.PIPELINE_LOGS_DIR:
        diretorios.append(config.LOGS_DIR)
    vistos: set[str] = set()
    for diretorio in diretorios:
        for arquivo in sorted(diretorio.glob("pipeline-*.jsonl")):
            if not arquivo.is_file() or arquivo.is_symlink():
                continue
            with arquivo.open(encoding="utf-8") as linhas:
                for linha in linhas:
                    if valor not in linha:
                        continue
                    try:
                        registro = json.loads(linha)
                    except json.JSONDecodeError:
                        continue
                    if registro.get(campo) == valor:
                        chave = json.dumps(registro, sort_keys=True, ensure_ascii=False)
                        if chave not in vistos:
                            vistos.add(chave)
                            registros.append(registro)
    # Ordem estável por horário; empates mantêm a ordem de gravação.
    return sorted(registros, key=_timestamp_key)


def _timestamp_key(registro: dict[str, Any]) -> float:
    try:
        timestamp = datetime.fromisoformat(str(registro.get("ts", "")))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.timestamp()
    except ValueError:
        return 0.0


_CHUNKS_NO_OUTPUT = re.compile(r"^(\d+) chunks?\b")


def _evidencias_vetoriais(etapas: list[dict[str, Any]]) -> dict[str, int] | int | None:
    """Evidências da busca vetorial que chegaram à geração, num log antigo.

    Usa a última etapa vetorial: a lista ``evidencias`` do ``extra``, contada
    por base como no ``resumo_consulta``; sem ela, o total de "N chunks" do
    output, que não diz a base de cada trecho.
    """
    for etapa in reversed(etapas):
        if etapa["base"] != "vetorial" or etapa["status"] == "pulado":
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
    stages = {etapa["stage"] for etapa in etapas if etapa["status"] != "pulado"}
    resumo: dict[str, Any] = {
        "caminho": None,
        "consultou_sql": Stage.CONSULTA_SQL.value in stages,
        "consultou_vetorial": bool(stages & _ETAPAS_VETORIAIS),
    }
    chunks_sql = [etapa["detalhes"].get("chunks") for etapa in etapas
                  if etapa["stage"] == Stage.CONSULTA_SQL.value and etapa["status"] != "pulado"]
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


_MARCOS = {
    Stage.REQUISICAO_INICIO.value: "início da requisição",
    Stage.PIPELINE_INICIO.value: "início do pipeline",
}


def _saida_da_etapa(registro: dict[str, Any]) -> Any:
    """``output`` da etapa no trace, sem ``null`` onde o log tem o que mostrar.

    Marco de início não produz saída, e etapa pulada guarda o motivo nos
    detalhes; no trace os dois apareciam com ``output: null``. O resumo da
    consulta já vai inteiro no ``resumo`` do topo: aqui fica só a referência.
    """
    saida = registro.get("output")
    extra = registro.get("extra") or {}
    stage = registro.get("stage")
    if stage == Stage.RESUMO_CONSULTA.value and isinstance(saida, dict):
        return "ver o campo resumo, no topo do trace"
    if saida is not None:
        return saida
    if stage in _MARCOS:
        contexto = extra.get("contexto_execucao") or {}
        marco: dict[str, Any] = {"marco": _MARCOS[stage]}
        if contexto.get("git_revision"):
            marco["versao_codigo"] = str(contexto["git_revision"])[:7]
        parametros = {chave: extra[chave] for chave in ("top_k", "escopos") if chave in extra}
        if parametros:
            marco["parametros"] = parametros
        return marco
    if registro.get("status") == "pulado" or extra.get("outcome") == "pulado":
        return {"pulado": extra.get("motivo") or "sem motivo registrado"}
    return None


def _trace_dos_registros(query_id: str, registros: list[dict[str, Any]]) -> dict[str, Any]:

    etapas = [
        {
            "ordem": ordem,
            "stage": registro.get("stage"),
            "base": _BASE_DA_ETAPA.get(registro.get("stage")),
            "status": registro.get("status"),
            "ts": registro.get("ts"),
            # Logs de antes de 25/09 gravavam a latência com casas decimais.
            "latencia_ms": round(registro.get("latencia_ms") or 0),
            "input": registro.get("input"),
            "output": _saida_da_etapa(registro),
            "tokens": registro.get("tokens"),
            "modelo": registro.get("modelo"),
            "detalhes": registro.get("extra") or {},
        }
        for ordem, registro in enumerate(registros, start=1)
    ]
    resumo = dict(next(
        (registro["output"] for registro in reversed(registros)
         if registro.get("stage") == Stage.RESUMO_CONSULTA.value and isinstance(registro.get("output"), dict)),
        None,
    ) or _resumo_derivado(etapas))
    # Feedback can arrive days later; it is not part of query latency/completion.
    core = [r for r in registros if r.get("stage") != Stage.FEEDBACK.value] or registros
    starts = [r for r in core if r.get("stage") == Stage.REQUISICAO_INICIO.value]
    pipeline_starts = [r for r in core if r.get("stage") == Stage.PIPELINE_INICIO.value]
    request_end = next((r for r in reversed(core) if r.get("stage") == Stage.REQUISICAO_FIM.value), None)
    pipeline_end = next((r for r in reversed(core) if r.get("stage") == Stage.PIPELINE_FIM.value), None)
    terminal = request_end if (starts or request_end) else pipeline_end
    started = bool(starts or pipeline_starts)
    if terminal:
        latency = int(terminal.get("latencia_ms") or 0)
        method = "requisicao_monotonica" if request_end else "pipeline_monotonico"
        status, complete = terminal.get("status"), True
    elif started:
        latency = max(0, round((_timestamp_key(core[-1]) - _timestamp_key(core[0])) * 1000))
        method, status, complete = "parcial", "incompleto", False
    else:
        latency = sum(int(r.get("latencia_ms") or 0) for r in core)
        method, status, complete = "soma_etapas_legado", None, None
    if terminal:
        details = terminal.get("extra") or {}
        for key in ("history_saved", "http_status", "logging_errors", "tokens_reportados", "cobertura_tokens"):
            if key in details:
                resumo[key] = details[key]
    # A lista ``etapas`` já traz cada etapa; o resumo repetia todas de novo
    # (``etapas`` do resumo_consulta e do fim da requisição). Fica a contagem.
    resumo = {chave: valor for chave, valor in resumo.items() if chave != "etapas"}
    resumo["situacao_das_etapas"] = dict(Counter(str(etapa["status"]) for etapa in etapas))
    pergunta = next((r["input"] for r in core if isinstance(r.get("input"), str)), None)
    return {
        "query_id": query_id,
        "session_id": next((r["session_id"] for r in registros if r.get("session_id")), None),
        "run_ids": list(dict.fromkeys(str(registro.get("run_id")) for registro in registros)),
        "pergunta": pergunta,
        "inicio": core[0]["ts"],
        "fim": (terminal or core[-1])["ts"],
        "latencia_total_ms": latency,
        "latencia_metodo": method,
        "latencia_pipeline_ms": int(pipeline_end.get("latencia_ms") or 0) if pipeline_end else None,
        "status": status,
        "completo": complete,
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
               ("query_id", "pergunta", "inicio", "fim", "latencia_total_ms", "latencia_metodo",
                "status", "completo", "resumo")},
            "etapas": [etapa["stage"] for etapa in trace["etapas"]],
        })
    return _sem_endereco_de_banco({
        "session_id": session_id,
        "inicio": perguntas[0]["inicio"],
        "fim": max(perguntas, key=lambda p: _timestamp_key({"ts": p["fim"]}))["fim"],
        "total_perguntas": len(perguntas),
        "consultou_sql": sum(1 for p in perguntas if p["resumo"].get("consultou_sql")),
        "consultou_vetorial": sum(1 for p in perguntas if p["resumo"].get("consultou_vetorial")),
        "perguntas": perguntas,
    })


__all__ = ["montar_trace", "montar_trace_sessao"]
