"""Aguarda um lote cadastral e reconcilia offline em destino novo.

Não coleta dados, não gera embeddings e não promove. O arquivo de estado
permite distinguir espera, conclusão parcial, erro e timeout sem supor sucesso.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from src.reconcile_units_v7 import reconciliar
from src.recover_corpus_v7 import agora, gravar_json


def acompanhar(cadastro: Path, observadas: Path, destino: Path, estado: Path, *,
               catalogo_aquisicao: Path | None = None, estado_contratos: Path | None = None,
               timeout: float = 7200, intervalo: float = 10) -> dict:
    if estado.exists() or destino.exists():
        raise FileExistsError("acompanhamento/destino já existe; não duplicar execução")
    if timeout <= 0 or intervalo <= 0:
        raise ValueError("timeout/intervalo inválido")
    inicio = time.monotonic()
    base = {"pid": os.getpid(), "iniciado_em": agora(), "cadastro_aguardado": str(cadastro),
            "destino": str(destino), "promocao_autorizada": False}
    gravar_json(estado, {**base, "status": "aguardando_cadastro"})
    while not cadastro.exists():
        if time.monotonic() - inicio >= timeout:
            result = {**base, "status": "timeout", "atualizado_em": agora()}
            gravar_json(estado, result)
            return result
        time.sleep(intervalo)
    try:
        dados = json.loads(cadastro.read_text(encoding="utf-8"))
        if dados.get("execucao_finalizada") is not True:
            raise ValueError("arquivo não certifica encerramento do lote")
        gravar_json(estado, {**base, "status": "reconciliando_offline", "atualizado_em": agora()})
        report = reconciliar(observadas, cadastro, destino, catalogo_aquisicao=catalogo_aquisicao, estado_contratos=estado_contratos)
        result = {**base, "status": "reconciliado" if report["verificacao_sementes_completa"] else "reconciliado_com_pendencias",
                  "atualizado_em": agora(), "relatorio": str(destino / "relatorio.json"),
                  "orgaos_processados": report["orgaos_processados"], "orgaos_verificados": report["orgaos_verificados"]}
        gravar_json(estado, result)
        return result
    except Exception as exc:
        result = {**base, "status": "erro", "atualizado_em": agora(), "erro": f"{type(exc).__name__}: {exc}"[:500]}
        gravar_json(estado, result)
        raise


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cadastro", type=Path, required=True)
    p.add_argument("--observadas", type=Path, required=True)
    p.add_argument("--destino", type=Path, required=True)
    p.add_argument("--estado", type=Path, required=True)
    p.add_argument("--catalogo-aquisicao", type=Path)
    p.add_argument("--estado-contratos", type=Path)
    p.add_argument("--timeout", type=float, default=7200)
    args = p.parse_args(argv)
    result = acompanhar(args.cadastro, args.observadas, args.destino, args.estado,
                        catalogo_aquisicao=args.catalogo_aquisicao, estado_contratos=args.estado_contratos, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "timeout" else 0


if __name__ == "__main__":
    raise SystemExit(main())
