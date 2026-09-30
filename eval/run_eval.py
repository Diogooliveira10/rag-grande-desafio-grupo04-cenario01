r"""Roda o golden set de perguntas de referência contra o pipeline e mede regressão.

Uso:
    py .\eval\run_eval.py check
    py .\eval\run_eval.py run --pipeline eval.adapters:rota_apenas
    py .\eval\run_eval.py run --pipeline src.pipeline:responder
    py .\eval\run_eval.py run --pipeline eval.adapters:pipeline_completo
    py .\eval\run_eval.py run --pipeline eval.adapters:pipeline_completo --ids P1.1 G1

``check`` só valida o golden set (contagem por tipo), sem chamar nenhum
pipeline. ``run`` importa a função apontada por ``--pipeline`` (formato
``modulo.submodulo:funcao``), roda cada pergunta do golden set contra ela e
grava o relatório (JSON + Markdown) em ``eval/runs/<run_id>/``.
``eval.adapters:rota_apenas`` avalia só o roteamento (``src/router.py``) —
rápido e offline. ``src.pipeline:responder`` roda o pipeline completo
(roteamento + recuperação híbrida + geração), chamando o LLM de verdade;
``eval.adapters:pipeline_completo`` faz o mesmo e ainda junta, a partir dos
logs, os tokens e a latência de cada etapa. ``--ids`` roda só alguns casos.

Além de rota e recusa (que definem ``acertou``), o relatório mede citação
(resposta não recusada com ao menos uma fonte; recusa sem fonte), confiança,
latência de ponta a ponta e tokens.

O golden set (``eval/golden_set.jsonl``) é derivado das 18 perguntas de
referência + a pergunta de falha esperada do ``PERSONAS.md``, mais os casos
``G*`` de guardrail do cenário (dados bancários de fornecedor, credenciais em
e-mail, juízo sobre contrato específico). Cada caso
declara a rota esperada (bases) e, quando aplicável, se a resposta correta é
uma recusa — é o gabarito citado em 2.3 do documento do desafio: rodar esse
benchmark antes e depois de atualizar a base mostra se a recuperação piorou.
"""

from __future__ import annotations

import argparse
import importlib
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PipelineFn = Callable[[str], dict[str, Any]]

DEFAULT_GOLDEN_SET = PROJECT_ROOT / "eval" / "golden_set.jsonl"
DEFAULT_RUNS_DIR = PROJECT_ROOT / "eval" / "runs"


@dataclass(frozen=True)
class EvalCase:
    """Um caso do golden set, derivado de uma pergunta de referência do PERSONAS.md."""

    id: str
    persona: str
    titulo: str
    pergunta: str
    tipo: str
    bases_esperadas: list[str]
    is_refusal_esperado: bool
    refusal_reason_esperado: Optional[str]
    e_pergunta_de_falha_esperada: bool
    hipotese_falha: Optional[str] = None
    comportamento_esperado: Optional[str] = None


def load_golden_set(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            cases.append(
                EvalCase(
                    id=record["id"],
                    persona=record["persona"],
                    titulo=record["titulo"],
                    pergunta=record["pergunta"],
                    tipo=record["tipo"],
                    bases_esperadas=list(record.get("bases_esperadas", [])),
                    is_refusal_esperado=bool(record.get("is_refusal_esperado", False)),
                    refusal_reason_esperado=record.get("refusal_reason_esperado"),
                    e_pergunta_de_falha_esperada=bool(record.get("e_pergunta_de_falha_esperada", False)),
                    hipotese_falha=record.get("hipotese_falha"),
                    comportamento_esperado=record.get("comportamento_esperado"),
                )
            )
    ids = [case.id for case in cases]
    duplicated = sorted({item for item in ids if ids.count(item) > 1})
    if duplicated:
        raise ValueError(f"IDs duplicados no golden set: {duplicated}")
    return cases


def evaluate_case(case: EvalCase, response: dict[str, Any]) -> dict[str, Any]:
    """Compara a resposta do pipeline com o gabarito de uma pergunta de referência."""
    bases_obtidas = sorted({str(base).lower() for base in response.get("bases_consultadas", []) or []})
    is_refusal_obtido = bool(response.get("is_refusal", False))
    refusal_reason_obtido = response.get("refusal_reason")

    refusal_ok = is_refusal_obtido == case.is_refusal_esperado
    if case.is_refusal_esperado and case.refusal_reason_esperado is not None:
        refusal_ok = refusal_ok and refusal_reason_obtido == case.refusal_reason_esperado

    # Quando a resposta correta é recusar, não cobramos rota: o sistema não deveria escolher base nenhuma.
    rota_ok = True if case.is_refusal_esperado else bases_obtidas == sorted(case.bases_esperadas)

    # Resposta sem campo sources_used (ex.: rota_apenas) não é avaliada em citação.
    fontes = response.get("sources_used")
    n_fontes = len(fontes) if isinstance(fontes, list) else None
    if n_fontes is None:
        citacao_ok = None
    else:
        citacao_ok = n_fontes == 0 if is_refusal_obtido else n_fontes > 0

    return {
        "id": case.id,
        "tipo": case.tipo,
        "bases_esperadas": sorted(case.bases_esperadas),
        "bases_obtidas": bases_obtidas,
        "is_refusal_esperado": case.is_refusal_esperado,
        "is_refusal_obtido": is_refusal_obtido,
        "refusal_reason_esperado": case.refusal_reason_esperado,
        "refusal_reason_obtido": refusal_reason_obtido,
        "rota_ok": rota_ok,
        "refusal_ok": refusal_ok,
        "acertou": rota_ok and refusal_ok,
        "n_fontes": n_fontes,
        "citacao_ok": citacao_ok,
        "confidence_level": response.get("confidence_level"),
        "answer": response.get("answer"),
        "metricas": response.get("_metricas"),
    }


def _executar_caso(case: EvalCase, pipeline_fn: PipelineFn) -> dict[str, Any]:
    """Roda um caso medindo a latência; exceção vira caso errado, não derruba a rodada."""
    inicio = time.perf_counter()
    try:
        resposta = pipeline_fn(case.pergunta)
        erro = None
    except Exception as exc:  # noqa: BLE001 - o relatório precisa registrar qualquer falha
        resposta = {}
        erro = f"{type(exc).__name__}: {exc}"
    latencia_ms = int((time.perf_counter() - inicio) * 1000)

    resultado = evaluate_case(case, resposta)
    resultado["latencia_ms"] = latencia_ms
    resultado["erro"] = erro
    if erro:
        resultado.update(rota_ok=False, refusal_ok=False, acertou=False, citacao_ok=False)
    return resultado


def _resumo_metricas(resultados: list[dict[str, Any]]) -> dict[str, Any]:
    latencias = [r["latencia_ms"] for r in resultados if r.get("latencia_ms") is not None]
    citacoes = [r["citacao_ok"] for r in resultados if r.get("citacao_ok") is not None]
    confianca: dict[str, int] = defaultdict(int)
    tokens_total = 0
    tokens_por_etapa: dict[str, int] = defaultdict(int)
    for resultado in resultados:
        if resultado.get("confidence_level"):
            confianca[str(resultado["confidence_level"])] += 1
        metricas = resultado.get("metricas") or {}
        tokens_total += int(metricas.get("tokens_total", 0))
        for etapa, dados in (metricas.get("etapas") or {}).items():
            tokens_por_etapa[etapa] += int(dados.get("tokens", 0))
    return {
        "erros": sum(1 for r in resultados if r.get("erro")),
        "citacao_avaliada": len(citacoes),
        "citacao_ok": sum(citacoes),
        "confianca": dict(sorted(confianca.items())),
        "latencia_ms": {
            "media": int(statistics.mean(latencias)) if latencias else 0,
            "mediana": int(statistics.median(latencias)) if latencias else 0,
            "max": max(latencias) if latencias else 0,
            "total": sum(latencias),
        },
        "tokens_total": tokens_total,
        "tokens_por_etapa": dict(sorted(tokens_por_etapa.items())),
    }


def run_eval(cases: list[EvalCase], pipeline_fn: PipelineFn) -> dict[str, Any]:
    """Roda cada caso do golden set contra o pipeline e agrega os resultados."""
    resultados = []
    for indice, case in enumerate(cases, start=1):
        resultado = _executar_caso(case, pipeline_fn)
        resultados.append(resultado)
        status = "ERRO" if resultado["erro"] else ("OK" if resultado["acertou"] else "FALHOU")
        print(
            f"[{indice}/{len(cases)}] {case.id} {status} ({resultado['latencia_ms']} ms)",
            file=sys.stderr,
            flush=True,
        )

    por_tipo: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "acertos": 0})
    for case, resultado in zip(cases, resultados):
        por_tipo[case.tipo]["total"] += 1
        por_tipo[case.tipo]["acertos"] += int(resultado["acertou"])

    total = len(resultados)
    acertos = sum(resultado["acertou"] for resultado in resultados)
    return {
        "total": total,
        "acertos": acertos,
        "taxa_acerto": round(acertos / total, 4) if total else 0.0,
        "por_tipo": dict(sorted(por_tipo.items())),
        "metricas": _resumo_metricas(resultados),
        "resultados": resultados,
    }


def render_report_markdown(report: dict[str, Any], run_id: str, built_at: str) -> str:
    taxa = report["taxa_acerto"]
    lines = [
        "# Relatório de avaliação — golden set",
        "",
        f"- Run: `{run_id}`",
        f"- Gerado em: `{built_at}`",
        f"- Acertos: **{report['acertos']}/{report['total']}** ({taxa:.0%})",
        "",
        "## Por tipo",
        "",
        "| Tipo | Acertos | Total |",
        "|---|---:|---:|",
    ]
    for tipo, stats in report["por_tipo"].items():
        lines.append(f"| {tipo} | {stats['acertos']} | {stats['total']} |")

    metricas = report.get("metricas") or {}
    latencia = metricas.get("latencia_ms") or {}
    if metricas:
        lines.extend(["", "## Métricas", ""])
        if metricas.get("citacao_avaliada"):
            lines.append(f"- Citação correta: **{metricas['citacao_ok']}/{metricas['citacao_avaliada']}**")
        if metricas.get("confianca"):
            confianca = ", ".join(f"{nivel}={qtd}" for nivel, qtd in metricas["confianca"].items())
            lines.append(f"- Confiança: {confianca}")
        lines.append(
            f"- Latência (ms): média {latencia.get('media', 0)}, mediana {latencia.get('mediana', 0)}, "
            f"máx {latencia.get('max', 0)}, total {latencia.get('total', 0)}"
        )
        if metricas.get("tokens_total"):
            lines.append(f"- Tokens: **{metricas['tokens_total']}** no total")
            for etapa, tokens in metricas.get("tokens_por_etapa", {}).items():
                lines.append(f"  - {etapa}: {tokens}")
        lines.append(f"- Erros de execução: {metricas.get('erros', 0)}")

    lines.extend(
        [
            "",
            "## Casos",
            "",
            "| ID | Tipo | Rota esperada | Rota obtida | Recusa esperada | Recusa obtida "
            "| Fontes | Latência (ms) | Resultado |",
            "|---|---|---|---|---|---|---:|---:|---|",
        ]
    )
    for resultado in report["resultados"]:
        if resultado.get("erro"):
            status = "ERRO"
        else:
            status = "OK" if resultado["acertou"] else "FALHOU"
        n_fontes = resultado.get("n_fontes")
        lines.append(
            f"| {resultado['id']} | {resultado['tipo']} "
            f"| {', '.join(resultado['bases_esperadas']) or '—'} "
            f"| {', '.join(resultado['bases_obtidas']) or '—'} "
            f"| {resultado['is_refusal_esperado']} | {resultado['is_refusal_obtido']} "
            f"| {'—' if n_fontes is None else n_fontes} | {resultado.get('latencia_ms', '—')} | {status} |"
        )

    com_texto = [r for r in report["resultados"] if r.get("answer") or r.get("erro")]
    if com_texto:
        lines.extend(["", "## Respostas", ""])
        for resultado in com_texto:
            lines.extend([f"### {resultado['id']}", ""])
            if resultado.get("erro"):
                lines.append(f"Erro: `{resultado['erro']}`")
            else:
                confianca = resultado.get("confidence_level") or "—"
                lines.extend([f"Confiança: {confianca} · fontes: {resultado.get('n_fontes')}", ""])
                texto = " ".join(str(resultado["answer"]).split())
                lines.append(f"> {texto[:1200]}{'…' if len(texto) > 1200 else ''}")
            lines.append("")
    return "\n".join(lines) + "\n"


def save_run(report: dict[str, Any], output_dir: Path, run_id: str, built_at: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    md_path = output_dir / "REPORT.md"
    payload = {"run_id": run_id, "built_at": built_at, **report}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_report_markdown(report, run_id, built_at), encoding="utf-8")
    return json_path, md_path


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _load_pipeline(spec: str) -> PipelineFn:
    module_name, _, func_name = spec.partition(":")
    if not module_name or not func_name:
        raise ValueError("use o formato modulo.submodulo:funcao")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, func_name)
    except AttributeError as exc:
        raise ValueError(f"{module_name} não define '{func_name}'") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="valida o golden set sem chamar o pipeline")
    check_parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)

    run_parser = subparsers.add_parser("run", help="roda o golden set contra o pipeline")
    run_parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    run_parser.add_argument(
        "--pipeline",
        required=True,
        help="função a chamar, formato modulo.submodulo:funcao (ex.: src.pipeline:responder)",
    )
    run_parser.add_argument("--output", type=Path, default=DEFAULT_RUNS_DIR)
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--ids", nargs="+", default=None, help="roda só os casos com esses IDs")

    args = parser.parse_args()

    if not args.golden_set.exists():
        parser.error(f"golden set não encontrado: {args.golden_set}")

    cases = load_golden_set(args.golden_set)

    if args.command == "check":
        por_tipo: dict[str, int] = defaultdict(int)
        for case in cases:
            por_tipo[case.tipo] += 1
        print(json.dumps({"total_casos": len(cases), "por_tipo": dict(sorted(por_tipo.items()))}, ensure_ascii=False))
        return 0

    try:
        pipeline_fn = _load_pipeline(args.pipeline)
    except (ImportError, ValueError) as exc:
        parser.error(
            f"não foi possível carregar --pipeline={args.pipeline}: {exc}. "
            "use eval.adapters:rota_apenas (só roteamento) ou src.pipeline:responder "
            "(pipeline completo)."
        )

    if args.ids:
        desconhecidos = sorted(set(args.ids) - {case.id for case in cases})
        if desconhecidos:
            parser.error(f"IDs fora do golden set: {', '.join(desconhecidos)}")
        cases = [case for case in cases if case.id in set(args.ids)]

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = run_eval(cases, pipeline_fn)
    json_path, md_path = save_run(report, args.output / run_id, run_id, built_at)
    summary = {
        "run_id": run_id,
        "acertos": report["acertos"],
        "total": report["total"],
        "relatorio_json": _relative(json_path),
        "relatorio_md": _relative(md_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["acertos"] == report["total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
