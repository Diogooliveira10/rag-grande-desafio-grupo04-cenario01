r"""Roda o golden set de perguntas de referência contra o pipeline e mede regressão.

Uso:
    py .\eval\run_eval.py check
    py .\eval\run_eval.py run --pipeline eval.adapters:rota_apenas
    py .\eval\run_eval.py run --pipeline src.pipeline:responder

``check`` só valida o golden set (contagem por tipo), sem chamar nenhum
pipeline. ``run`` importa a função apontada por ``--pipeline`` (formato
``modulo.submodulo:funcao``), roda cada pergunta do golden set contra ela e
grava o relatório (JSON + Markdown) em ``eval/runs/<run_id>/``.
``eval.adapters:rota_apenas`` já funciona hoje e avalia só o roteamento
(``src/router.py``); um pipeline completo (roteamento + recuperação +
geração) só é possível quando ``retrieve.py`` existir.

O golden set (``eval/golden_set.jsonl``) é derivado das 18 perguntas de
referência + a pergunta de falha esperada do ``PERSONAS.md``. Cada caso
declara a rota esperada (bases) e, quando aplicável, se a resposta correta é
uma recusa — é o gabarito citado em 2.3 do documento do desafio: rodar esse
benchmark antes e depois de atualizar a base mostra se a recuperação piorou.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
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
    }


def run_eval(cases: list[EvalCase], pipeline_fn: PipelineFn) -> dict[str, Any]:
    """Roda cada caso do golden set contra o pipeline e agrega os resultados."""
    resultados = [evaluate_case(case, pipeline_fn(case.pergunta)) for case in cases]

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

    lines.extend(
        [
            "",
            "## Casos",
            "",
            "| ID | Tipo | Rota esperada | Rota obtida | Recusa esperada | Recusa obtida | Resultado |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for resultado in report["resultados"]:
        status = "OK" if resultado["acertou"] else "FALHOU"
        lines.append(
            f"| {resultado['id']} | {resultado['tipo']} "
            f"| {', '.join(resultado['bases_esperadas']) or '—'} "
            f"| {', '.join(resultado['bases_obtidas']) or '—'} "
            f"| {resultado['is_refusal_esperado']} | {resultado['is_refusal_obtido']} | {status} |"
        )
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
            "router.py/retrieve.py/generate.py ainda podem não existir neste repositório; "
            "aponte para uma função compatível quando o pipeline estiver pronto."
        )

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
