"""Demonstração offline da extração manifest-driven para apresentação.

Este teste não acessa a internet nem altera ``data/raw``. Ele verifica o contrato
estrutural da extração e salva um log legível em ``testes/output_teste_extracao.log``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fetch_corpus import load_manifest  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "testes" / "output_teste_extracao.log"
_CURRENT_RUN_ID = "offline-not-started"


def _log(lines: list[str], level: str, message: str, **fields: object) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = fields.pop("run_id", _CURRENT_RUN_ID)
    stage = fields.pop("stage", "offline_validation")
    suffix = f" | run_id={run_id} | stage={stage}" + "".join(
        f" | {key}={value}" for key, value in fields.items()
    )
    lines.append(f"{timestamp} [{level}] {message}{suffix}")


def _tracked_raw_files() -> int | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "data/raw"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()])


def run(output_path: Path = DEFAULT_OUTPUT) -> int:
    global _CURRENT_RUN_ID
    _CURRENT_RUN_ID = f"offline-extraction-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    lines: list[str] = []
    _log(lines, "INFO", "Início do teste offline da extração")
    manifest_path = PROJECT_ROOT / "corpus_manifest.yaml"

    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:  # pragma: no cover - mensagem para apresentação
        _log(lines, "ERRO", "Manifesto inválido", erro=f"{type(exc).__name__}: {exc}")
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 1

    _log(
        lines,
        "OK",
        "Manifesto carregado como fonte única de verdade",
        arquivo=manifest_path.relative_to(PROJECT_ROOT),
        scenario_id=manifest["scenario_id"],
        versao=manifest["version"],
    )

    for base in manifest["bases"]:
        acesso = base["acesso"]
        _log(
            lines,
            "INFO",
            "Base declarada no manifesto",
            base_id=base["base_id"],
            natureza=base["natureza"],
            acesso=acesso.get("tipo"),
            volume_alvo=base.get("volume_alvo"),
            rate_limit_seg=acesso.get("rate_limit_seg", 0),
        )
        _log(
            lines,
            "INFO",
            "Parâmetros da base inspecionados",
            base_id=base["base_id"],
            parametros=acesso.get("params", "não declarados"),
            paginacao=acesso.get("paginacao", "não declarada"),
            endpoints=list((acesso.get("endpoints") or {}).keys()) or "não declarados",
            origem_local=acesso.get("origem", "não aplicável"),
        )

    _log(lines, "INFO", "Contrato operacional dos comandos")
    for command, command_line, meaning in (
        ("fetch", "python -m src.fetch_corpus fetch --official", "materializa uma nova versão a partir do manifesto"),
        ("update", "python -m src.fetch_corpus update --official", "materializa a próxima versão e calcula o diff"),
        ("inventory", "python -m src.fetch_corpus inventory --corpus-version v1 --official", "reconstrói ou valida inventário sem baixar dados"),
    ):
        _log(
            lines,
            "INFO",
            "Comando oficial",
            comando=command_line,
            objetivo=meaning,
        )
    _log(
        lines,
        "INFO",
        "Comando experimental",
        comando="python -m src.fetch_corpus fetch --manifest corpus_manifest.yaml --data-root data/attempts/corpus_fetch/tentativa-vN",
        objetivo="testa uma coleta isolada sem alterar a v1 oficial",
    )

    _log(
        lines,
        "INFO",
        "Conceito: idempotência",
        explicacao="reexecutar o mesmo manifesto completo reutiliza a versão existente e não duplica conteúdo",
    )
    _log(
        lines,
        "INFO",
        "Conceito: retomada",
        explicacao="o checkpoint registra cada logical_id concluído para continuar após uma interrupção",
    )
    _log(
        lines,
        "INFO",
        "Conceito: imutabilidade",
        explicacao="a coleta ocorre em data/raw/.staging/vN e só é promovida para data/raw/vN ao finalizar",
    )

    source = PROJECT_ROOT / "src" / "fetch_corpus.py"
    source_text = source.read_text(encoding="utf-8")
    for command in ("fetch", "update", "inventory"):
        found = f'"{command}"' in source_text
        _log(lines, "OK" if found else "ERRO", "Subcomando verificado", comando=command, encontrado=found)

    inventory: dict[str, Any] = {}
    inventory_root = PROJECT_ROOT / "data" / "inventory"
    inventory_paths = sorted(
        inventory_root.glob("v*.json"),
        key=lambda path: int(path.stem[1:]) if path.stem[1:].isdigit() else -1,
    )
    inventory_path = inventory_paths[-1] if inventory_paths else inventory_root / "v1.json"
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        _log(
            lines,
            "OK",
            "Inventário de versão encontrado",
            arquivo=inventory_path.relative_to(PROJECT_ROOT),
            tipo=inventory.get("inventory_type"),
            oficial=inventory.get("official"),
            vinculado_ao_manifesto=inventory.get("manifest_bound"),
            manifest_hash=inventory.get("manifest_hash") or "não aplicável (legado)",
            arquivos=inventory.get("totais", {}).get("arquivos"),
            itens=inventory.get("totais", {}).get("itens"),
            falhas=inventory.get("totais", {}).get("falhas"),
        )
        required_inventory_fields = {
            "scenario_id",
            "version",
            "coletado_em",
            "manifest_hash",
            "inventory_type",
            "official",
            "manifest_bound",
            "totais",
            "por_base",
            "arquivos",
            "falhas",
        }
        missing_inventory_fields = sorted(required_inventory_fields - set(inventory))
        _log(
            lines,
            "OK" if not missing_inventory_fields else "ERRO",
            "Contrato do inventário verificado",
            campos_ausentes=missing_inventory_fields or "nenhum",
            registros=len(inventory.get("arquivos", [])),
            bases=len(inventory.get("por_base", [])),
        )
        for base_summary in inventory.get("por_base", []):
            _log(
                lines,
                "INFO",
                "Resumo da base no inventário",
                base_id=base_summary.get("base_id"),
                arquivos=base_summary.get("arquivos"),
                itens=base_summary.get("itens"),
                formatos=base_summary.get("formatos"),
                bytes=base_summary.get("bytes"),
                volume_observado=base_summary.get("volume_observado"),
                divergencia_alvo=base_summary.get("divergencia_alvo"),
            )
        if not inventory.get("manifest_hash"):
            _log(
                lines,
                "ATENÇÃO",
                "Inventário legado não está vinculado ao manifesto",
                explicacao="as versões oficiais produzidas pelo fetch possuem hash do manifesto",
            )
    else:
        _log(lines, "ATENÇÃO", "Inventário v1 ainda não existe; execute inventory --corpus-version v1 --official")

    diff_files = sorted(path.name for path in inventory_root.glob("DIFF_*.md")) if inventory_root.exists() else []
    checkpoint_files = sorted(path.name for path in (inventory_root / "checkpoints").glob("v*.json")) if (inventory_root / "checkpoints").exists() else []
    _log(lines, "INFO", "Artefatos auxiliares localizados", diffs=diff_files or "nenhum", checkpoints=checkpoint_files or "nenhum")

    raw_path = PROJECT_ROOT / "data" / "raw"
    ignored = "data/raw/" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    tracked = _tracked_raw_files()
    _log(lines, "OK" if ignored else "ERRO", "Regra de armazenamento bruto verificada", gitignore_data_raw=ignored)
    _log(lines, "ATENÇÃO" if tracked else "OK", "Arquivos brutos já rastreados no Git", quantidade=tracked if tracked is not None else "não verificado")
    _log(lines, "OK" if raw_path.exists() else "ATENÇÃO", "Diretório de dados brutos verificado", existe=raw_path.exists())
    raw_files = len([path for path in raw_path.rglob("*") if path.is_file()]) if raw_path.exists() else 0
    _log(lines, "INFO", "Contagem física do armazenamento bruto", arquivos=raw_files, observacao="arquivos existentes não são alterados por este teste")

    _log(lines, "INFO", "Teste concluído sem downloads e sem alteração de data/raw")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"log={output_path.relative_to(PROJECT_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    return run(output_path)


if __name__ == "__main__":
    raise SystemExit(main())
