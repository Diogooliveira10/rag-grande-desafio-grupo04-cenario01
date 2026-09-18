from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

# --------------------------------------------------------------------------
# Caminhos padrão (relativos à raiz do repositório)
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH_DEFAULT = ROOT / "corpus_manifest.yaml"
RAW_DIR = ROOT / "data" / "raw"
INVENTORY_DIR = ROOT / "data" / "inventory"
LOGS_DIR = ROOT / "logs"
DONE_MARKER = "_VERSION_COMPLETE.json"
CHECKPOINT_NAME = "_checkpoint.json"

USER_AGENT = "grande-desafio-cenario01-fetch-corpus/1.0 (+residencia-ecoa)"

logger = logging.getLogger("fetch_corpus")


def _setup_logging() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"fetch-corpus-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"

    class JsonlHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "msg": record.getMessage(),
            }
            extra = getattr(record, "extra_fields", None)
            if extra:
                entry.update(extra)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.addHandler(JsonlHandler())
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(stream)
    return log_path


def log_event(level: str, msg: str, **fields: Any) -> None:
    getattr(logger, level)(msg, extra={"extra_fields": fields})


# --------------------------------------------------------------------------
# Manifesto
# --------------------------------------------------------------------------

@dataclass
class BaseConfig:
    base_id: str
    natureza: str
    fonte: str
    acesso: dict
    selecao_deterministica: bool = True
    campo_vigencia: Optional[str] = None
    metadados_extras: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "BaseConfig":
        return cls(
            base_id=d["base_id"],
            natureza=d["natureza"],
            fonte=d.get("fonte", ""),
            acesso=d["acesso"],
            selecao_deterministica=d.get("selecao_deterministica", True),
            campo_vigencia=d.get("campo_vigencia"),
            metadados_extras=d.get("metadados_extras", []),
            raw=d,
        )


def load_manifest(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    bases = [BaseConfig.from_dict(b) for b in data.get("bases", [])]
    if not bases:
        raise ValueError(f"Nenhuma base encontrada em {path}")
    return bases


# --------------------------------------------------------------------------
# Filtro local seguro (sem eval): campo aninhado + operador + valor
# --------------------------------------------------------------------------

def _get_path(record: dict, path: str) -> Any:
    cur: Any = record
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _load_valor_ref(ref: str) -> list:
    p = ROOT / ref
    if not p.exists():
        log_event("warning", "valor_ref não encontrado — filtro retornará lista vazia", path=str(p))
        return []
    with open(p, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("cnpjs", [])
    return data


def apply_filtro_local(record: dict, filtro: Optional[dict]) -> bool:
    """Filtro determinístico e auditável. Nunca usa eval()/exec() sobre dado externo."""
    if not filtro:
        return True
    campo = filtro.get("campo")
    operador = filtro.get("operador", "==")
    if "valor_ref" in filtro:
        valor = _load_valor_ref(filtro["valor_ref"])
        operador = "in"
    else:
        valor = filtro.get("valor")
    atual = _get_path(record, campo)
    if operador == "==":
        return atual == valor
    if operador == "in":
        return atual in (valor or [])
    raise ValueError(f"Operador de filtro não suportado: {operador}")


# --------------------------------------------------------------------------
# Versionamento imutável
# --------------------------------------------------------------------------

def _existing_versions(base_id: str) -> list:
    if not RAW_DIR.exists():
        return []
    versoes = []
    for p in RAW_DIR.glob("v*"):
        if not p.is_dir():
            continue
        m = re.fullmatch(r"v(\d+)", p.name)
        if m and (p / base_id).exists():
            versoes.append(int(m.group(1)))
    return sorted(versoes)


def _is_complete(base_id: str, versao: int) -> bool:
    return (RAW_DIR / f"v{versao}" / base_id / DONE_MARKER).exists()


def resolve_target_version(base_id: str):
    """
    Decide em qual versão gravar:
    - se a última versão existe mas está incompleta, RETOMA nela (resumibilidade);
    - caso contrário, abre uma versão nova (nunca sobrescreve uma versão concluída).
    Retorna (numero_versao, diretorio_da_base, é_retomada).
    """
    versoes = _existing_versions(base_id)
    if versoes:
        ultima = versoes[-1]
        if not _is_complete(base_id, ultima):
            path = RAW_DIR / f"v{ultima}" / base_id
            return ultima, path, True
        nova = ultima + 1
    else:
        nova = 1
    path = RAW_DIR / f"v{nova}" / base_id
    path.mkdir(parents=True, exist_ok=True)
    return nova, path, False


# --------------------------------------------------------------------------
# Checkpoint (resumibilidade)
# --------------------------------------------------------------------------

def _load_checkpoint(base_dir: Path) -> dict:
    cp = base_dir / CHECKPOINT_NAME
    if cp.exists():
        with open(cp, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"pagina_atual": 1, "total_registros": 0, "total_filtrados": 0, "falhas": 0}


def _save_checkpoint(base_dir: Path, state: dict) -> None:
    with open(base_dir / CHECKPOINT_NAME, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def _mark_complete(base_dir: Path, resumo: dict) -> None:
    with open(base_dir / DONE_MARKER, "w", encoding="utf-8") as fh:
        json.dump(
            {"finalizado_em": datetime.now(timezone.utc).isoformat(), **resumo},
            fh, ensure_ascii=False, indent=2,
        )


# --------------------------------------------------------------------------
# Coletores por tipo de acesso
# --------------------------------------------------------------------------

def coletar_rest_api(cfg: BaseConfig, base_dir: Path) -> dict:
    acesso = cfg.acesso
    endpoint = acesso["endpoint"]
    params_base = dict(acesso.get("params", {}))
    paginacao = acesso.get("paginacao", {})
    campo_pagina = paginacao.get("campo_pagina", "pagina")
    campo_tamanho = paginacao.get("campo_tamanho", "tamanhoPagina")
    tamanho_pagina = acesso.get("tamanho_pagina", 50)
    rate_limit = float(acesso.get("rate_limit_seg", 1.0))
    filtro = acesso.get("filtro_local")

    state = _load_checkpoint(base_dir)
    pagina = state.get("pagina_atual", 1)
    total_registros = state.get("total_registros", 0)
    total_filtrados = state.get("total_filtrados", 0)
    falhas = state.get("falhas", 0)
    falhas_seguidas = 0

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    while True:
        params = dict(params_base)
        params[campo_pagina] = pagina
        params[campo_tamanho] = tamanho_pagina

        try:
            resp = session.get(endpoint, params=params, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            falhas += 1
            falhas_seguidas += 1
            log_event(
                "error", "falha ao buscar página — registrando e continuando",
                base_id=cfg.base_id, pagina=pagina, erro=str(exc),
            )
            state.update(pagina_atual=pagina, total_registros=total_registros,
                          total_filtrados=total_filtrados, falhas=falhas)
            _save_checkpoint(base_dir, state)
            if falhas_seguidas >= 5:
                log_event("error", "muitas falhas consecutivas — abortando esta base por ora "
                                     "(resumível: rode fetch de novo mais tarde)", base_id=cfg.base_id)
                break
            time.sleep(rate_limit * 3)
            pagina += 1
            continue

        falhas_seguidas = 0
        registros = payload.get("data", payload if isinstance(payload, list) else [])
        if not registros:
            break

        registros_filtrados = [r for r in registros if apply_filtro_local(r, filtro)]

        page_file = base_dir / f"pagina_{pagina:05d}.json"
        with open(page_file, "w", encoding="utf-8") as fh:
            json.dump(registros_filtrados, fh, ensure_ascii=False, indent=2)

        total_registros += len(registros)
        total_filtrados += len(registros_filtrados)
        log_event("info", "página coletada", base_id=cfg.base_id, pagina=pagina,
                  registros=len(registros), retidos=len(registros_filtrados))

        state.update(pagina_atual=pagina + 1, total_registros=total_registros,
                      total_filtrados=total_filtrados, falhas=falhas)
        _save_checkpoint(base_dir, state)

        total_paginas = payload.get("totalPaginas")
        if total_paginas is not None and pagina >= int(total_paginas):
            break

        pagina += 1
        time.sleep(rate_limit)

    return {
        "total_registros_api": total_registros,
        "total_registros_retidos": total_filtrados,
        "falhas": falhas,
        "ultima_pagina": pagina,
    }


def coletar_http_pdf(cfg: BaseConfig, base_dir: Path) -> dict:
    acesso = cfg.acesso
    urls = acesso.get("urls", [])
    rate_limit = float(acesso.get("rate_limit_seg", 1.0))
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    baixados, falhas = 0, 0
    for i, url in enumerate(urls, start=1):
        sufixo = Path(url).suffix or ".html"
        destino = base_dir / f"doc_{i:04d}{sufixo}"
        if destino.exists():
            continue  # idempotente: já baixado nesta versão
        try:
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            falhas += 1
            log_event("error", "falha ao baixar documento normativo",
                      base_id=cfg.base_id, url=url, erro=str(exc))
            continue
        destino.write_bytes(resp.content)
        with open(base_dir / f"doc_{i:04d}.meta.json", "w", encoding="utf-8") as fh:
            json.dump({"url": url, "baixado_em": datetime.now(timezone.utc).isoformat()},
                      fh, ensure_ascii=False, indent=2)
        baixados += 1
        time.sleep(rate_limit)

    return {"total_documentos": len(urls), "baixados": baixados, "falhas": falhas}


COLETORES = {
    "rest_api": coletar_rest_api,
    "http_pdf": coletar_http_pdf,
}


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------

def cmd_fetch(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    bases = load_manifest(manifest_path)
    if args.base:
        bases = [b for b in bases if b.base_id == args.base]
        if not bases:
            print(f"Base '{args.base}' não encontrada em {manifest_path}", file=sys.stderr)
            sys.exit(1)

    for cfg in bases:
        tipo = cfg.acesso.get("tipo")
        coletor = COLETORES.get(tipo)
        if coletor is None:
            log_event("error", "tipo de acesso não suportado — pulando base",
                      base_id=cfg.base_id, tipo=tipo)
            continue

        versao, base_dir, retomando = resolve_target_version(cfg.base_id)
        log_event("info", f"{'retomando' if retomando else 'iniciando'} coleta",
                  base_id=cfg.base_id, versao=versao, tipo=tipo)

        resumo = coletor(cfg, base_dir)
        _mark_complete(base_dir, resumo)
        log_event("info", "base concluída", base_id=cfg.base_id, versao=versao, **resumo)


def cmd_update(args: argparse.Namespace) -> None:
    # v1: update delega para fetch — sempre grava uma nova versão imutável.
    # Refinamento futuro: usar campo_vigencia / data do último inventário para
    # ajustar dataInicial e coletar só o delta, em vez do conjunto inteiro de novo.
    log_event("info", "update: delegando para fetch (nova versão imutável)")
    cmd_fetch(args)


def cmd_inventory(args: argparse.Namespace) -> None:
    INVENTORY_DIR.mkdir(parents=True, exist_ok=True)
    if not RAW_DIR.exists():
        print("Nenhum dado em data/raw/ ainda — rode 'fetch' primeiro.", file=sys.stderr)
        sys.exit(1)

    versoes_disponiveis = sorted(
        int(m.group(1)) for p in RAW_DIR.glob("v*")
        for m in [re.fullmatch(r"v(\d+)", p.name)] if m
    )
    if not versoes_disponiveis:
        print("Nenhuma versão encontrada em data/raw/.", file=sys.stderr)
        sys.exit(1)
    versao = versoes_disponiveis[-1]
    versao_dir = RAW_DIR / f"v{versao}"

    inventario = {
        "versao": versao,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "bases": {},
    }
    for base_dir in sorted(p for p in versao_dir.iterdir() if p.is_dir()):
        base_id = base_dir.name
        arquivos_pagina = list(base_dir.glob("pagina_*.json"))
        arquivos_doc = list(base_dir.glob("doc_*.*"))
        tamanho_total = sum(f.stat().st_size for f in base_dir.glob("*") if f.is_file())
        completo = (base_dir / DONE_MARKER).exists()
        registros = 0
        for f in arquivos_pagina:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    registros += len(json.load(fh))
            except (json.JSONDecodeError, OSError):
                pass
        inventario["bases"][base_id] = {
            "completo": completo,
            "arquivos": len(arquivos_pagina) + len(arquivos_doc),
            "registros_estimados": registros,
            "tamanho_bytes": tamanho_total,
        }

    inventory_path = INVENTORY_DIR / f"v{versao}.json"
    with open(inventory_path, "w", encoding="utf-8") as fh:
        json.dump(inventario, fh, ensure_ascii=False, indent=2)

    md_lines = [
        "# CORPUS_INVENTORY",
        "",
        f"Versão mais recente: **v{versao}**  ",
        f"Gerado em: {inventario['gerado_em']}",
        "",
        "| Base | Completo | Arquivos | Registros (estimado) | Tamanho |",
        "|---|---|---|---|---|",
    ]
    for base_id, info in inventario["bases"].items():
        tamanho_mb = info["tamanho_bytes"] / (1024 * 1024)
        status = "OK" if info["completo"] else "incompleto"
        md_lines.append(
            f"| {base_id} | {status} | {info['arquivos']} | "
            f"{info['registros_estimados']} | {tamanho_mb:.2f} MB |"
        )
    md_lines += [
        "",
        "> Gerado automaticamente por `python -m src.fetch_corpus inventory`. Não editar manualmente.",
    ]
    (ROOT / "CORPUS_INVENTORY.md").write_text("\n".join(md_lines), encoding="utf-8")

    print(f"Inventário gerado: {inventory_path}")
    print("CORPUS_INVENTORY.md atualizado.")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_corpus",
        description="Coleta, atualiza e inventaria o corpus do Cenário 1 (Contratações Públicas RJ).",
    )
    parser.add_argument("--manifest", default=str(MANIFEST_PATH_DEFAULT),
                         help="Caminho do corpus_manifest.yaml")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_fetch = sub.add_parser("fetch", help="Coleta completa (versão nova ou retomada)")
    p_fetch.add_argument("--base", help="Coletar apenas esta base_id")
    p_fetch.set_defaults(func=cmd_fetch)

    p_update = sub.add_parser("update", help="Atualização (v1: nova versão completa)")
    p_update.add_argument("--base", help="Atualizar apenas esta base_id")
    p_update.set_defaults(func=cmd_update)

    p_inv = sub.add_parser("inventory", help="Gera data/inventory/v{n}.json e CORPUS_INVENTORY.md")
    p_inv.set_defaults(func=cmd_inventory)

    return parser


def main(argv=None) -> None:
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()