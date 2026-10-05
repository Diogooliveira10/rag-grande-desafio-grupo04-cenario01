from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.obs import new_query_id, new_run_id
from src.pipeline import ESCOPOS_CONSULTADOS, responder
from src.router import rotear_pergunta
from src.query_analysis import analisar_query


def _imprimir_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _parsear_perguntas(perguntas_raw: list[str]) -> list[str]:
    perguntas: list[str] = []
    for item in perguntas_raw:
        texto = item.strip()
        if texto:
            perguntas.append(texto)
    return perguntas


def cmd_rota(perguntas: list[str]) -> int:
    for pergunta in _parsear_perguntas(perguntas):
        run_id, query_id = new_run_id(), new_query_id()
        analise = analisar_query(pergunta, run_id=run_id, query_id=query_id)
        decisao = rotear_pergunta(pergunta, run_id=run_id, query_id=query_id, analise=analise)
        print(f"Pergunta: {pergunta}")
        _imprimir_json(decisao.model_dump(mode="json"))
        print()
    return 0


def cmd_perguntar(perguntas: list[str], *, incluir_experimentais: bool = False) -> int:
    escopos = None if incluir_experimentais else ESCOPOS_CONSULTADOS
    for pergunta in _parsear_perguntas(perguntas):
        resposta = responder(pergunta, escopos=escopos)
        print(f"Pergunta: {pergunta}")
        _imprimir_json(resposta)
        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI simples para roteamento e resposta do projeto RAG.",
        epilog=(
            "Exemplos:\n"
            "  python app/cli.py rota\n"
            "  python app/cli.py perguntar\n"
            "  python app/cli.py rota \"Pergunta 1?\" \"Pergunta 2?\"\n"
            "  python app/cli.py perguntar \"Pergunta 1?\" \"Pergunta 2?\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--openrouter-only", action="store_true",
                        help="Usa somente OpenRouter Free para LLM; embeddings continuam no ECOA.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rota_parser = subparsers.add_parser("rota", help="Mostra em quais bases cada pergunta deve buscar.")
    rota_parser.add_argument("--openrouter-only", action="store_true", default=argparse.SUPPRESS,
                            help="Usa somente OpenRouter Free para LLM.")
    rota_parser.add_argument("perguntas", nargs="*", help="Perguntas em linguagem natural. Se vazio, pede interação no terminal.")
    rota_parser.set_defaults(func=lambda args: cmd_rota(args.perguntas if args.perguntas else [input("Pergunta: ")]))

    perguntar_parser = subparsers.add_parser("perguntar", help="Executa a resposta completa do pipeline para uma ou mais perguntas.")
    perguntar_parser.add_argument("--openrouter-only", action="store_true", default=argparse.SUPPRESS,
                                 help="Usa somente OpenRouter Free para LLM.")
    perguntar_parser.add_argument("perguntas", nargs="*", help="Perguntas em linguagem natural. Se vazio, pede interação no terminal.")
    perguntar_parser.add_argument(
        "--incluir-experimentais",
        action="store_true",
        help="Busca também nas bases experimentais (ex.: exemplos_teste), não só nas oficiais.",
    )
    perguntar_parser.set_defaults(
        func=lambda args: cmd_perguntar(
            args.perguntas if args.perguntas else [input("Pergunta: ")],
            incluir_experimentais=args.incluir_experimentais,
        )
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    previous_mode = config.LLM_PROVIDER_MODE
    if args.openrouter_only:
        if not config.OPENROUTER_API_KEY:
            parser.error("--openrouter-only exige OPENROUTER_API_KEY")
        config.LLM_PROVIDER_MODE = "openrouter"
    try:
        return args.func(args)
    except Exception as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    finally:
        config.LLM_PROVIDER_MODE = previous_mode


if __name__ == "__main__":
    raise SystemExit(main())
