'''DIFF entre dois corpus normalizados (v6 → v7), por registro.

Mesma semântica do ``DIFF_v1_v2.md`` do ``src/fetch_corpus.py`` (adicionados,
removidos, alterados, idênticos), mas sobre os ``documents.jsonl``: a chave é o
``logical_id`` (ex. ``contratos_estruturado:numeroControlePNCP:...``) e a
comparação de conteúdo é o ``document_content_sha256`` — **não** o ``sha256``
bruto, que na v6 é o hash do arquivo/página e no modo direto é o hash derivado
do registro (comparações entre gerações precisam do hash do conteúdo).

O diff é o insumo do teste de regressão (regra do desafio): "roda o benchmark
antes e depois de atualizar a base e mostra que a recuperação não piorou".

Uso:

    python -m src.diff_corpus --antigo data/normalized/documents.jsonl \
        --novo data/v7_direto/documents.jsonl --base-id contratos_estruturado \
        --saida data/v7_direto
'''

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _registros_por_logical_id(caminho: Path, base_id: str | None) -> dict[str, dict[str, Any]]:
    registros: dict[str, dict[str, Any]] = {}
    with caminho.open("r", encoding="utf-8") as handle:
        for linha in handle:
            if not linha.strip():
                continue
            registro = json.loads(linha)
            if base_id and registro.get("base_id") != base_id:
                continue
            chave = str(registro.get("logical_id") or registro.get("chunk_id"))
            if chave in registros:
                continue  # primeiro chunk do documento vence (registro_inteiro: 1)
            registros[chave] = registro
    return registros


def diff_corpus(
    antigo: Path,
    novo: Path,
    *,
    base_id: str | None = None,
) -> dict[str, Any]:
    '''Classifica cada registro: entrou, saiu, mudou ou ficou idêntico.'''
    velhos = _registros_por_logical_id(Path(antigo), base_id)
    novos = _registros_por_logical_id(Path(novo), base_id)

    entraram = sorted(set(novos) - set(velhos))
    sairam = sorted(set(velhos) - set(novos))
    comuns = set(velhos) & set(novos)
    alterados = sorted(
        chave for chave in comuns
        if velhos[chave].get("document_content_sha256") != novos[chave].get("document_content_sha256")
    )
    identicos = sorted(chave for chave in comuns if chave not in set(alterados))
    return {
        "base_id": base_id,
        "antigo": str(antigo),
        "novo": str(novo),
        "contagens": {
            "entraram": len(entraram),
            "sairam": len(sairam),
            "alterados": len(alterados),
            "identicos": len(identicos),
            "total_antigo": len(velhos),
            "total_novo": len(novos),
        },
        "entraram": entraram,
        "sairam": sairam,
        "alterados": alterados,
        "identicos": identicos,
    }


def render_markdown(diff: dict[str, Any], *, amostra: int = 40) -> str:
    c = diff["contagens"]
    linhas = [
        f"# Diff de corpus — base `{diff['base_id'] or 'todas'}`",
        "",
        f"- Corpus antigo: `{diff['antigo']}` ({c['total_antigo']} registros)",
        f"- Corpus novo: `{diff['novo']}` ({c['total_novo']} registros)",
        "",
        f"- Entraram: **{c['entraram']}**",
        f"- Saíram: **{c['sairam']}**",
        f"- Alterados (mesmo `logical_id`, conteúdo diferente): **{c['alterados']}**",
        f"- Idênticos: **{c['identicos']}**",
    ]
    for titulo, chave in (("Entraram", "entraram"), ("Saíram", "sairam"), ("Alterados", "alterados")):
        valores = diff[chave]
        linhas.extend(["", f"## {titulo}", ""])
        if not valores:
            linhas.append("Nenhum.")
        else:
            linhas.extend(f"- `{v}`" for v in valores[:amostra])
            if len(valores) > amostra:
                linhas.append(f"- … e mais {len(valores) - amostra} (lista completa no JSON).")
    return "\n".join(linhas) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--antigo", type=Path, required=True)
    parser.add_argument("--novo", type=Path, required=True)
    parser.add_argument("--base-id", default="contratos_estruturado")
    parser.add_argument("--saida", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    diff = diff_corpus(args.antigo, args.novo, base_id=args.base_id)
    args.saida.mkdir(parents=True, exist_ok=True)
    destino_json = args.saida / "DIFF_v6_v7.json"
    destino_md = args.saida / "DIFF_v6_v7.md"
    destino_json.write_text(json.dumps(diff, ensure_ascii=False, indent=1), encoding="utf-8")
    destino_md.write_text(render_markdown(diff), encoding="utf-8")
    print(json.dumps({"contagens": diff["contagens"],
                      "markdown": str(destino_md), "json": str(destino_json)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
