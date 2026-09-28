r"""Materializa o corpus declarado pelo manifesto.

Uso:
    py -m src.fetch_corpus fetch --manifest corpus_manifest.yaml --official
    py -m src.fetch_corpus update --manifest corpus_manifest.yaml --official
    py -m src.fetch_corpus inventory --corpus-version v1 --official

As versões são coletadas com checkpoint, staging, inventário e deduplicação
por conteúdo. Coletas experimentais exigem ``--data-root`` explícito.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import re
import socket
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlencode, urlsplit, urlunsplit

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

try:
    from .providers import get_provider
    from .providers.base import SourceRef
    from .corpus_validator import ValidationResult, validate_inventory_contract
except ImportError:
    from providers import get_provider
    from providers.base import SourceRef
    from corpus_validator import ValidationResult, validate_inventory_contract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

source_check = importlib.import_module("src.source_catalog")  # noqa: E402
RICH_CONSOLE = Console()


class _RunLogger:
    """Registro JSONL de uma execução da coleta, sem bibliotecas externas."""

    def __init__(self, command: str) -> None:
        timestamp = datetime.now(timezone.utc)
        self.run_id = f"{command}-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{time.time_ns() % 1_000_000:06d}"
        self.path = RUNS_ROOT / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, stage: str, status: str, **fields: Any) -> None:
        record = {
            "run_id": self.run_id,
            "stage": stage,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class _VersionLock:
    """Lock exclusivo para impedir dois writers na mesma versão."""

    def __init__(self, version: int) -> None:
        self.path = RAW_ROOT / ".staging" / f"v{version}.lock"
        self._owned = False

    @staticmethod
    def _process_is_alive(pid: int) -> bool:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _is_stale(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("hostname") != socket.gethostname():
                return False
            pid = int(payload["pid"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
        return not self._process_is_alive(pid)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for attempt in range(2):
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 0 and self._is_stale():
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        continue
                    continue
                raise RuntimeError(f"versão já está sendo coletada: {self.path}")
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            self._owned = True
            return
        raise RuntimeError(f"não foi possível adquirir lock da versão: {self.path}")

    def release(self) -> None:
        if not self._owned:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self._owned = False


@dataclass(frozen=True)
class FetchCandidate:
    candidate_id: str
    nome: str
    url: str
    kind: str
    folder: str
    filename_prefix: str
    file_type: str
    payload: Any | None = None


def _available(result: dict[str, Any] | None) -> bool:
    return bool(result and source_check._is_available(result))


def _file_urls(result: dict[str, Any] | None) -> Iterator[tuple[int, str]]:
    if not result or not _available(result):
        return
    seen: set[str] = set()
    for index, record in enumerate(source_check.records_from_payload(result.get("json")), start=1):
        for key in ("url", "uri", "location"):
            value = record.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                if value not in seen:
                    seen.add(value)
                    yield index, value
                break


def iter_candidates(results: list[dict[str, Any]]) -> Iterator[FetchCandidate]:
    """Gera candidatos estáveis, incluindo todos os arquivos PNCP descobertos."""
    seen_urls: set[str] = set()
    web_ids = {source_id for source_id, _, _, _ in source_check.WEB_SOURCES}

    for result in results:
        result_id = str(result.get("id") or "")
        url = result.get("url")
        if result_id in web_ids or not _available(result) or not isinstance(url, str):
            continue
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        seen_urls.add(url)
        yield FetchCandidate(
            candidate_id=f"api_{result_id}",
            nome=f"API — {result.get('nome', result_id)}",
            url=url,
            kind="json",
            folder="json",
            filename_prefix=f"api_{result_id}",
            file_type="json",
        )

        records = source_check.records_from_payload(result.get("json"))
        if records:
            yield FetchCandidate(
                candidate_id=f"api_{result_id}_jsonl",
                nome=f"JSONL — registros de {result.get('nome', result_id)}",
                url=url,
                kind="jsonl",
                folder="jsonl",
                filename_prefix=f"records_{result_id}",
                file_type="jsonl",
                payload=records,
            )

    for source_id, name, url, kind in source_check.WEB_SOURCES:
        result = source_check.find_result(results, source_id)
        if not _available(result) or url in seen_urls:
            continue
        seen_urls.add(url)
        file_type = "html" if kind in {"html", "htm", "aspx"} else kind
        yield FetchCandidate(
            candidate_id=source_id,
            nome=name,
            url=url,
            kind=kind,
            folder=file_type,
            filename_prefix=source_id,
            file_type=file_type,
        )

    for result in results:
        result_id = str(result.get("id") or "")
        if not (
            result_id.startswith("arquivos_edital")
            or result_id.startswith("arquivos_atas")
        ):
            continue

        base_name = "edital" if result_id.startswith("arquivos_edital") else "ata"
        for index, url in _file_urls(result):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            yield FetchCandidate(
                candidate_id=f"pncp_{result_id}_{index:03d}",
                nome=f"PNCP — {base_name} {index:03d}",
                url=url,
                kind="pncp",
                folder="pncp",
                filename_prefix=f"{base_name}_{result_id}_{index:03d}",
                file_type="pdf",
            )


def _unique_samples(payload: Any, extractor: Any) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for record in source_check.records_from_payload(payload):
        sample = extractor([record])
        if not sample:
            continue
        key = tuple(sorted(sample.items()))
        if key not in seen:
            seen.add(key)
            samples.append(sample)
    return samples

def _run_checks_for_types(
    config: Any,
    tipos: tuple[str, ...],
    *,
    before_request: Callable[[], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Consulta somente as fontes compatíveis com os tipos solicitados."""
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    selected = set(tipos)
    results: list[dict[str, Any]] = []

    def append_request(
        source_id: str,
        name: str,
        url: str,
        kind: str,
    ) -> dict[str, Any]:
        if before_request is not None:
            before_request()
        payload = source_check.request_url(
            url,
            kind,
            attempts=config.attempts,
            timeout=config.timeout,
        )
        results.append(
            {
                "id": source_id,
                "nome": name,
                "url": url,
                **payload,
            }
        )
        return payload

    for source_id, name, url, kind in source_check.WEB_SOURCES:
        if kind == "html" and "html" in selected:
            append_request(source_id, name, url, kind)
        elif kind == "pdf" and "pdf" in selected:
            append_request(source_id, name, url, kind)
        elif kind == "json" and {"json", "jsonl"} & selected:
            append_request(source_id, name, url, kind)

    needs_pncp = bool({"pdf", "json", "jsonl"} & selected)
    purchase: dict[str, str] | None = None
    purchase_samples: list[dict[str, str]] = []
    publication: dict[str, Any] = source_check.empty_result(
        "sem_amostra",
        "consulta de contratação não selecionada",
    )

    if needs_pncp:
        publication_url, publication, purchase = source_check.query_for_sample(
            "/v1/contratacoes/publicacao",
            lambda start, end: {
                "dataInicial": start,
                "dataFinal": end,
                **(
                    {"codigoModalidadeContratacao": str(config.modalidade)}
                    if config.modalidade is not None
                    else {}
                ),
                "uf": config.uf,
                "pagina": str(config.pagina),
                "tamanhoPagina": str(config.tamanho_pagina),
            },
            source_check.sample_purchase,
            config,
            before_request=before_request,
        )
        results.append(
            {
                "id": "contratacoes_publicacao",
                "nome": "PNCP — contratações/publicação",
                "url": publication_url,
                **publication,
            }
        )
        purchase_samples = _unique_samples(
            publication.get("json"),
            source_check.sample_purchase,
        )
        if purchase is None and purchase_samples:
            purchase = purchase_samples[0]

        if {"json", "jsonl"} & selected:
            first_start, first_end = source_check.sample_windows(config)[0]
            contracts_url = source_check.make_consulta_url(
                "/v1/contratos",
                {
                    "dataInicial": first_start,
                    "dataFinal": first_end,
                    "pagina": str(config.pagina),
                    "tamanhoPagina": str(config.tamanho_pagina),
                },
            )
            contracts = append_request(
                "contratos",
                "PNCP — contratos",
                contracts_url,
                "json",
            )
            if source_check._is_available(contracts):
                contract_samples = _unique_samples(
                    contracts.get("json"),
                    source_check.sample_purchase,
                )
                if purchase is None and contract_samples:
                    purchase_samples = contract_samples
                    purchase = contract_samples[0]

        if purchase:
            if not purchase_samples:
                purchase_samples = [purchase]
            purchases_for_files = (
                purchase_samples if "pdf" in selected else [purchase_samples[0]]
            )
            for index, purchase_item in enumerate(purchases_for_files, start=1):
                purchase_base = (
                    f"/v1/orgaos/{purchase_item['cnpj']}/compras/"
                    f"{purchase_item['ano']}/{purchase_item['sequencial']}"
                )
                if index == 1 and {"json", "jsonl"} & selected:
                    purchase_url = source_check.make_api_url(purchase_base)
                    append_request(
                        "detalhe_contratacao",
                        "PNCP — detalhe da contratação encontrada",
                        purchase_url,
                        "json",
                    )

                files_url = source_check.make_api_url(f"{purchase_base}/arquivos")
                append_request(
                    f"arquivos_edital_{index:03d}",
                    f"PNCP — arquivos/editais da contratação {index:03d}",
                    files_url,
                    "json",
                )

                if index == 1 and {"json", "jsonl"} & selected:
                    results_url = source_check.make_api_url(
                        f"{purchase_base}/itens/1/resultados"
                    )
                    append_request(
                        "resultados_itens",
                        "PNCP — resultados do item 1",
                        results_url,
                        "json",
                    )

        ata_url, atas, ata = source_check.query_for_sample(
            "/v1/atas",
            lambda start, end: {
                "dataInicial": start,
                "dataFinal": end,
                "pagina": str(config.pagina),
                "tamanhoPagina": str(config.tamanho_pagina),
            },
            source_check.sample_ata,
            config,
            before_request=before_request,
        )
        results.append(
            {
                "id": "atas",
                "nome": "PNCP — atas",
                "url": ata_url,
                **atas,
            }
        )
        ata_samples = _unique_samples(atas.get("json"), source_check.sample_ata)
        if ata is None and ata_samples:
            ata = ata_samples[0]
        if ata:
            if not ata_samples:
                ata_samples = [ata]
            atas_for_files = ata_samples if "pdf" in selected else [ata_samples[0]]
            for index, ata_item in enumerate(atas_for_files, start=1):
                ata_files_url = source_check.make_api_url(
                    f"/v1/orgaos/{ata_item['cnpj']}/compras/{ata_item['ano']}/"
                    f"{ata_item['sequencial']}/atas/{ata_item['seq_ata']}/arquivos"
                )
                append_request(
                    f"arquivos_atas_{index:03d}",
                    f"PNCP — arquivos das atas {index:03d}",
                    ata_files_url,
                    "json",
                )

    if {"json", "jsonl"} & selected:
        pca_url = source_check.make_consulta_url(
            "/v1/pca",
            {
                "anoPca": str(config.ano),
                "codigoClassificacaoSuperior": config.classificacao_superior,
                "pagina": str(config.pagina),
                "tamanhoPagina": str(config.tamanho_pagina),
            },
        )
        pca = append_request(
            "pca_consulta",
            "PNCP — consulta de PCA",
            pca_url,
            "json",
        )
        pca_sample = (
            source_check.sample_pca(pca.get("json"))
            if source_check._is_available(pca)
            else None
        )
        if pca_sample:
            pca_base = source_check.make_api_url(
                f"/v1/orgaos/{pca_sample['cnpj']}/pca/{pca_sample['ano']}"
            )
            append_request(
                "pca_consolidado",
                "PNCP — PCA consolidado por órgão",
                f"{pca_base}/consolidado",
                "json",
            )
            append_request(
                "pca_itens",
                "PNCP — itens do PCA",
                f"{pca_base}/{pca_sample['sequencial']}/itens/contratacao",
                "json",
            )

    return checked_at, results

MANIFEST_DEFAULT = PROJECT_ROOT / "corpus_manifest.yaml"
DATA_ROOT = PROJECT_ROOT / "data"
RAW_ROOT = DATA_ROOT / "raw"
OBJECTS_ROOT = RAW_ROOT / ".objects" / "sha256"
INVENTORY_ROOT = DATA_ROOT / "inventory"
RUNS_ROOT = DATA_ROOT / "runs"
CORPUS_INVENTORY_PATH = PROJECT_ROOT / "CORPUS_INVENTORY.md"


def _configure_data_root(data_root: Path) -> None:
    """Direciona uma execução para uma árvore de dados isolada."""
    global DATA_ROOT, RAW_ROOT, OBJECTS_ROOT, INVENTORY_ROOT, RUNS_ROOT, CORPUS_INVENTORY_PATH
    DATA_ROOT = data_root
    RAW_ROOT = DATA_ROOT / "raw"
    OBJECTS_ROOT = RAW_ROOT / ".objects" / "sha256"
    INVENTORY_ROOT = DATA_ROOT / "inventory"
    RUNS_ROOT = DATA_ROOT / "runs"
    official_data_root = (PROJECT_ROOT / "data").resolve()
    CORPUS_INVENTORY_PATH = (
        PROJECT_ROOT / "CORPUS_INVENTORY.md"
        if DATA_ROOT.resolve() == official_data_root
        else DATA_ROOT / "CORPUS_INVENTORY.md"
    )


def _strip_yaml_comment(value: str) -> str:
    quoted = False
    quote = ""
    depth = 0
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quoted and char == quote:
                quoted = False
            elif not quoted:
                quoted = True
                quote = char
        elif not quoted and char in "[{":
            depth += 1
        elif not quoted and char in "]}":
            depth = max(0, depth - 1)
        elif char == "#" and not quoted and depth == 0 and (
            index == 0 or value[index - 1].isspace()
        ):
            return value[:index].rstrip()
    return value.rstrip()


def _split_yaml_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    start = 0
    quoted = False
    quote = ""
    depth = 0
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quoted and char == quote:
                quoted = False
            elif not quoted:
                quoted = True
                quote = char
        elif not quoted and char in "[{":
            depth += 1
        elif not quoted and char in "]}":
            depth -= 1
        elif char == "," and not quoted and depth == 0:
            tokens.append(value[start:index].strip())
            start = index + 1
    tokens.append(value[start:].strip())
    return [token for token in tokens if token]


def _split_yaml_mapping(value: str) -> tuple[str, str] | None:
    quoted = False
    quote = ""
    depth = 0
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quoted and char == quote:
                quoted = False
            elif not quoted:
                quoted = True
                quote = char
        elif not quoted and char in "[{":
            depth += 1
        elif not quoted and char in "]}":
            depth -= 1
        elif char == ":" and not quoted and depth == 0:
            if index + 1 == len(value) or value[index + 1].isspace():
                return value[:index].strip(), value[index + 1 :].strip()
    return None


def _parse_yaml_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        return [_parse_yaml_scalar(token) for token in _split_yaml_tokens(value[1:-1])]
    if value.startswith("{") and value.endswith("}"):
        result: dict[str, Any] = {}
        for token in _split_yaml_tokens(value[1:-1]):
            mapping = _split_yaml_mapping(token)
            if mapping is None:
                raise ValueError(f"mapa YAML inline inválido: {token}")
            key, raw = mapping
            result[str(_parse_yaml_scalar(key))] = _parse_yaml_scalar(raw)
        return result
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _parse_manifest_yaml(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indentation = len(raw_line) - len(raw_line.lstrip(" "))
        content = _strip_yaml_comment(raw_line[indentation:])
        if content:
            lines.append((indentation, content))

    def parse_block(position: int, indentation: int) -> tuple[Any, int]:
        if position >= len(lines) or lines[position][0] != indentation:
            return {}, position
        is_list = lines[position][1].startswith("-")
        result: Any = [] if is_list else {}
        while position < len(lines):
            current_indent, content = lines[position]
            if current_indent < indentation:
                break
            if current_indent != indentation:
                raise ValueError(f"indentação YAML inesperada na linha {position + 1}")
            if is_list:
                if not content.startswith("-"):
                    break
                item = content[1:].strip()
                position += 1
                if not item:
                    value, position = parse_block(position, lines[position][0]) if position < len(lines) and lines[position][0] > indentation else (None, position)
                    result.append(value)
                    continue
                mapping = _split_yaml_mapping(item)
                if mapping is None:
                    result.append(_parse_yaml_scalar(item))
                    continue
                key, raw_value = mapping
                item_mapping: dict[str, Any] = {}
                if raw_value:
                    item_mapping[key] = _parse_yaml_scalar(raw_value)
                elif position < len(lines) and lines[position][0] > indentation:
                    item_mapping[key], position = parse_block(position, lines[position][0])
                else:
                    item_mapping[key] = None
                if position < len(lines) and lines[position][0] > indentation:
                    extra, position = parse_block(position, lines[position][0])
                    if not isinstance(extra, dict):
                        raise ValueError(f"item YAML inválido na linha {position + 1}")
                    item_mapping.update(extra)
                result.append(item_mapping)
                continue
            if content.startswith("-"):
                break
            mapping = _split_yaml_mapping(content)
            if mapping is None:
                raise ValueError(f"linha YAML inválida: {content}")
            key, raw_value = mapping
            position += 1
            if raw_value:
                result[key] = _parse_yaml_scalar(raw_value)
            elif position < len(lines) and lines[position][0] > indentation:
                result[key], position = parse_block(position, lines[position][0])
            else:
                result[key] = None
        return result, position

    parsed, position = parse_block(0, lines[0][0] if lines else 0)
    if position != len(lines) or not isinstance(parsed, dict):
        raise ValueError("manifesto YAML inválido")
    return parsed


def load_manifest(path: Path) -> dict[str, Any]:
    """Carrega o formato do manifesto sem adicionar dependências ao projeto."""
    manifest = _parse_manifest_yaml(path.read_text(encoding="utf-8"))
    missing = sorted({"scenario_id", "version", "bases"} - set(manifest))
    if missing:
        raise ValueError(f"manifesto sem campos obrigatórios: {', '.join(missing)}")
    if not isinstance(manifest["bases"], list) or not manifest["bases"]:
        raise ValueError("manifesto precisa declarar pelo menos uma base")
    seen: set[str] = set()
    for base in manifest["bases"]:
        if not isinstance(base, dict):
            raise ValueError("cada base do manifesto precisa ser um mapa")
        for field in ("base_id", "natureza", "acesso"):
            if not base.get(field):
                raise ValueError(f"base sem campo obrigatório: {field}")
        if base["base_id"] in seen:
            raise ValueError(f"base_id duplicado: {base['base_id']}")
        escopo = str(base.get("escopo") or "experimental").lower()
        if escopo not in {"oficial", "experimental"}:
            raise ValueError(f"escopo inválido para a base {base['base_id']}: {escopo}")
        base["escopo"] = escopo
        seen.add(base["base_id"])
    return manifest


def _manifest_for_scope(manifest: dict[str, Any], *, official: bool) -> dict[str, Any]:
    """Retorna o manifesto limitado ao escopo da execução."""
    if not official:
        return manifest
    bases = [base for base in manifest["bases"] if base.get("escopo") == "oficial"]
    if not bases:
        raise ValueError("manifesto não declara bases com escopo oficial")
    return {**manifest, "bases": bases}


def _clean_url(value: str) -> str:
    value = str(value).strip()
    return value[1:-1] if value.startswith("<") and value.endswith(">") else value


def _safe_component(value: str, fallback: str = "item") -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return normalized or fallback


def _manifest_hash(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _version_number(value: str | Path) -> int | None:
    match = re.fullmatch(r"v(\d+)", str(value))
    return int(match.group(1)) if match else None


def _available_versions() -> list[int]:
    values: set[int] = set()
    for root in (RAW_ROOT, INVENTORY_ROOT):
        if root.exists():
            values.update(number for child in root.iterdir() if (number := _version_number(child.name)) is not None)
    return sorted(values)


def _next_version() -> int:
    return max(_available_versions(), default=0) + 1


def _latest_version() -> int | None:
    values = _available_versions()
    return max(values) if values else None


def _version_path(version: int) -> Path:
    return RAW_ROOT / f"v{version}"


def _inventory_path(version: int) -> Path:
    return INVENTORY_ROOT / f"v{version}.json"


def _estimate_base_requests(base: dict[str, Any]) -> int:
    access = base.get("acesso") or {}
    kind = str(access.get("tipo", "")).lower()
    if kind in {"arquivo_local", "processamento_local"}:
        return 0
    if kind in {"http_pdf", "http_misto", "http_csv"}:
        urls = access.get("urls") or ([access.get("url")] if access.get("url") else [])
        return len([url for url in urls if url])
    if kind in {"resource_set", "pncp_resource_set"}:
        resources = access.get("recursos") or access.get("resources") or []
        return len(resources)
    if kind == "provider":
        return max(1, len(access.get("sources") or []))
    endpoints = access.get("endpoints") or {"principal": access.get("endpoint")}
    endpoint_count = len([endpoint for endpoint in endpoints.values() if endpoint])
    if kind == "rest_api_mais_arquivo" and any("{" in str(endpoint) for endpoint in endpoints.values() if endpoint):
        params = access.get("params") or {}
        try:
            pages = int(params.get("maxPaginas", 1))
        except (TypeError, ValueError):
            pages = 1
        try:
            target = int(base.get("volume_alvo", 1))
        except (TypeError, ValueError):
            target = 1
        return max(1, pages) + max(0, target)
    return max(1, endpoint_count)


def _dry_run_update(manifest_path: Path, *, official: bool = False) -> int:
    """Mostra o plano do update sem criar logs, checkpoints ou arquivos."""
    manifest = _manifest_for_scope(load_manifest(manifest_path), official=official)
    latest = _latest_version()
    if latest is None:
        raise RuntimeError("update exige uma versão anterior")
    inventory_path = _inventory_path(latest)
    if not inventory_path.exists():
        raise RuntimeError(f"inventário da versão anterior não encontrado: {inventory_path}")
    previous = json.loads(inventory_path.read_text(encoding="utf-8"))
    previous_items = [
        item
        for item in previous.get("arquivos", [])
        if isinstance(item, dict) and item.get("status") == "ok"
    ]
    estimated = sum(_estimate_base_requests(base) for base in manifest["bases"])
    conditional = min(estimated, len(previous_items))
    summary = {
        "dry_run": True,
        "scenario_id": manifest.get("scenario_id"),
        "manifest": str(manifest_path),
        "manifest_hash": _manifest_hash(manifest_path),
        "previous": f"v{latest}",
        "next": f"v{latest + 1}",
        "bases": [
            {
                "base_id": base["base_id"],
                "acesso": (base.get("acesso") or {}).get("tipo"),
                "estimated_requests": _estimate_base_requests(base),
            }
            for base in manifest["bases"]
        ],
        "previous_files": len(previous_items),
        "estimated_requests": estimated,
        "estimated_conditional_requests": conditional,
        "estimated_new_or_changed_requests": max(0, estimated - conditional),
        "writes": False,
    }
    RICH_CONSOLE.print(
        Panel(
            f"[bold]Cenário:[/] {summary['scenario_id']}\n"
            f"[bold]Manifest hash:[/] {summary['manifest_hash']}\n"
            f"[bold]Versão anterior:[/] {summary['previous']}    "
            f"[bold]Próxima:[/] {summary['next']}\n"
            f"[bold]Arquivos anteriores:[/] {summary['previous_files']}    "
            f"[bold]Escrita:[/] não",
            title="Update preview",
            border_style="cyan",
        )
    )
    table = Table(title="Estimativa de requisições")
    table.add_column("Base", style="cyan")
    table.add_column("Acesso", style="magenta")
    table.add_column("Estimadas", justify="right")
    for base in summary["bases"]:
        table.add_row(base["base_id"], str(base["acesso"] or "—"), str(base["estimated_requests"]))
    table.add_row("TOTAL", "", str(summary["estimated_requests"]), style="bold")
    RICH_CONSOLE.print(table)
    RICH_CONSOLE.print(
        f"Condicionais estimadas: [yellow]{summary['estimated_conditional_requests']}[/]  "
        f"Novas/alteradas estimadas: [yellow]{summary['estimated_new_or_changed_requests']}[/]"
    )
    return 0


def _relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _path_from_record(record: dict[str, Any]) -> Path | None:
    raw_path = record.get("path")
    if not raw_path:
        return None
    path = PROJECT_ROOT / str(raw_path)
    return path if path.exists() else None


@dataclass
class _VersionContext:
    manifest: dict[str, Any]
    version: int
    manifest_hash: str
    staging_path: Path
    checkpoint_path: Path
    collected_at: str
    items: dict[str, dict[str, Any]]
    hash_index: dict[str, str]
    last_request_at: dict[str, float] = field(default_factory=dict)
    previous_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    previous_version: int | None = None
    selection: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def _selection_from_items(items: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    selection: dict[str, list[dict[str, Any]]] = {}
    seen: dict[str, set[str]] = {}
    for logical_id, record in sorted(items.items()):
        source_value = record.get("source_ref")
        if not isinstance(source_value, dict):
            continue
        try:
            source_ref = SourceRef.from_dict(source_value)
        except (TypeError, ValueError, KeyError):
            continue
        base_id = str(record.get("base_id") or "")
        stable_id = source_ref.stable_id
        if not base_id or stable_id in seen.setdefault(base_id, set()):
            continue
        seen[base_id].add(stable_id)
        selection.setdefault(base_id, []).append(
            {
                "logical_id": logical_id,
                "stable_id": stable_id,
                "source_ref": source_ref.to_dict(),
            }
        )
    return selection


def _set_selection(
    context: _VersionContext,
    base_id: str,
    source_values: list[dict[str, Any]],
) -> None:
    if not hasattr(context, "selection"):
        context.selection = {}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_value in source_values:
        source_ref = SourceRef.from_dict(source_value)
        if source_ref.stable_id in seen:
            continue
        seen.add(source_ref.stable_id)
        selected.append(
            {
                "stable_id": source_ref.stable_id,
                "source_ref": source_ref.to_dict(),
            }
        )
    context.selection[base_id] = selected


def _validate_checkpoint(
    checkpoint: dict[str, Any],
    manifest: dict[str, Any] | None,
    manifest_hash: str,
    version: int,
    staging_path: Path,
) -> ValidationResult:
    errors: list[str] = []
    if checkpoint.get("manifest_hash") != manifest_hash:
        errors.append("manifest_hash incompatível")
    if checkpoint.get("version") not in {None, f"v{version}"}:
        errors.append("versão do checkpoint incompatível")
    if checkpoint.get("state") not in {"staging", "incomplete", "promoted"}:
        errors.append("estado do checkpoint inválido")

    items = checkpoint.get("items", {})
    if not isinstance(items, dict):
        errors.append("items do checkpoint não é um objeto")
        return ValidationResult(tuple(errors))

    manifest_bases = {
        str(base.get("base_id")): base
        for base in (manifest or {}).get("bases", [])
        if isinstance(base, dict) and base.get("base_id")
    }
    logical_by_base: dict[str, int] = {}
    source_ids_by_base: dict[str, set[str]] = {}
    for logical_id, record in items.items():
        if not isinstance(record, dict):
            errors.append(f"item {logical_id}: registro inválido")
            continue
        if record.get("logical_id") not in {None, logical_id}:
            errors.append(f"item {logical_id}: logical_id divergente")
        base_id = str(record.get("base_id") or "")
        if manifest_bases and base_id not in manifest_bases:
            errors.append(f"item {logical_id}: base desconhecida {base_id!r}")
        if record.get("status") == "ok":
            path = _path_from_record(record)
            if path is None:
                errors.append(f"item {logical_id}: arquivo ausente")
            elif checkpoint.get("state") != "promoted":
                try:
                    path.resolve().relative_to(staging_path.resolve())
                except ValueError:
                    errors.append(f"item {logical_id}: caminho fora do staging")
            expected_sha = record.get("sha256")
            if not expected_sha:
                errors.append(f"item {logical_id}: SHA-256 ausente")
            elif path is not None and _file_sha256(path) != expected_sha:
                errors.append(f"item {logical_id}: SHA-256 divergente")
            units = int(record.get("unidades_logicas", record.get("registros", 0)) or 0)
            logical_by_base[base_id] = logical_by_base.get(base_id, 0) + units
            source_value = record.get("source_ref")
            if isinstance(source_value, dict):
                try:
                    source_id = SourceRef.from_dict(source_value).stable_id
                except (TypeError, ValueError, KeyError) as exc:
                    errors.append(f"item {logical_id}: SourceRef inválida: {exc}")
                else:
                    source_ids_by_base.setdefault(base_id, set()).add(source_id)

    selection = checkpoint.get("selection")
    if selection is not None:
        if not isinstance(selection, dict):
            errors.append("selection do checkpoint não é um objeto")
        else:
            for base_id, entries in selection.items():
                if not isinstance(entries, list):
                    errors.append(f"selection/{base_id}: não é uma lista")
                    continue
                seen: set[str] = set()
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("source_ref"), dict):
                        errors.append(f"selection/{base_id}: entrada inválida")
                        continue
                    try:
                        source_id = SourceRef.from_dict(entry["source_ref"]).stable_id
                    except (TypeError, ValueError, KeyError) as exc:
                        errors.append(f"selection/{base_id}: SourceRef inválida: {exc}")
                        continue
                    if source_id in seen:
                        errors.append(f"selection/{base_id}: SourceRef duplicada {source_id}")
                    seen.add(source_id)
                    if entry.get("stable_id") not in {None, source_id}:
                        errors.append(f"selection/{base_id}: stable_id divergente")
                target = manifest_bases.get(base_id, {}).get("volume_alvo")
                if target is not None and len(entries) > int(target):
                    errors.append(f"selection/{base_id}: excede volume_alvo")
                missing = source_ids_by_base.get(base_id, set()) - seen
                if missing:
                    errors.append(f"selection/{base_id}: item fora da seleção congelada")

    for base_id, observed in logical_by_base.items():
        target = manifest_bases.get(base_id, {}).get("volume_alvo")
        if target is not None and observed > int(target):
            errors.append(f"{base_id}: unidades lógicas excedem volume_alvo")
    return ValidationResult(tuple(errors))


def _checkpoint_write(context: _VersionContext, state: str = "staging") -> None:
    context.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    selection = context.selection or _selection_from_items(context.items)
    payload = {
        "scenario_id": context.manifest["scenario_id"],
        "version": f"v{context.version}",
        "manifest_hash": context.manifest_hash,
        "coletado_em": context.collected_at,
        "state": state,
        "selection": selection,
        "items": context.items,
    }
    temporary = context.checkpoint_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(context.checkpoint_path)


def _load_version_context(
    manifest: dict[str, Any],
    manifest_hash: str,
    version: int,
    *,
    previous_items: dict[str, dict[str, Any]] | None = None,
    previous_version: int | None = None,
) -> _VersionContext:
    staging_path = RAW_ROOT / ".staging" / f"v{version}"
    checkpoint_path = INVENTORY_ROOT / "checkpoints" / f"v{version}.json"
    items: dict[str, dict[str, Any]] = {}
    if staging_path.exists() and not checkpoint_path.exists():
        raise RuntimeError(f"staging sem checkpoint; recuse sobrescrever: {staging_path}")
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        validation = _validate_checkpoint(
            checkpoint,
            manifest,
            manifest_hash,
            version,
            staging_path,
        )
        if not validation.ok:
            details = "; ".join(validation.errors)
            raise RuntimeError(f"checkpoint inválido para resume: {details}")
        items = dict(checkpoint.get("items", {}))
        selection = checkpoint.get("selection") or _selection_from_items(items)
    else:
        selection = {}
    staging_path.mkdir(parents=True, exist_ok=True)
    context = _VersionContext(
        manifest=manifest,
        version=version,
        manifest_hash=manifest_hash,
        staging_path=staging_path,
        checkpoint_path=checkpoint_path,
        collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        items=items,
        hash_index={
            str(record["sha256"]): str(record["path"])
            for record in items.values()
            if record.get("status") == "ok" and record.get("sha256") and record.get("path")
        },
        previous_items=previous_items or {},
        previous_version=previous_version,
        selection=selection,
        last_request_at={},
    )
    _checkpoint_write(context)
    return context


def _wait_rate_limit(context: _VersionContext, base: dict[str, Any]) -> None:
    try:
        delay = max(0.0, float((base.get("acesso") or {}).get("rate_limit_seg", 0) or 0))
    except (TypeError, ValueError):
        delay = 0.0
    base_id = str(base["base_id"])
    elapsed = time.monotonic() - context.last_request_at.get(base_id, 0.0)
    if delay and elapsed < delay:
        time.sleep(delay - elapsed)
    context.last_request_at[base_id] = time.monotonic()


def _effective_objects_root() -> Path:
    try:
        OBJECTS_ROOT.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return PROJECT_ROOT / "data" / "raw" / ".objects" / "sha256"
    return OBJECTS_ROOT


def _object_path(digest: str) -> Path:
    return _effective_objects_root() / digest[:2] / digest


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_object(body: bytes, digest: str) -> Path:
    object_path = _object_path(digest)
    object_path.parent.mkdir(parents=True, exist_ok=True)
    if object_path.exists():
        if _file_sha256(object_path) != digest:
            raise RuntimeError(f"objeto SHA-256 corrompido: {object_path}")
        return object_path
    temporary = object_path.with_name(f".{object_path.name}.tmp")
    temporary.write_bytes(body)
    try:
        if object_path.exists():
            temporary.unlink(missing_ok=True)
        else:
            temporary.replace(object_path)
    finally:
        temporary.unlink(missing_ok=True)
    return object_path


def _materialize_object(object_path: Path, destination: Path, digest: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _file_sha256(destination) != digest:
            raise RuntimeError(f"destino existente não corresponde ao SHA-256: {destination}")
        return "existente"
    try:
        os.link(object_path, destination)
        return "hardlink"
    except FileExistsError:
        if _file_sha256(destination) != digest:
            raise RuntimeError(f"destino criado com conteúdo diferente: {destination}")
        return "existente"
    except OSError:
        temporary = destination.with_name(f".{destination.name}.tmp")
        shutil.copy2(object_path, temporary)
        try:
            if destination.exists():
                if _file_sha256(destination) != digest:
                    raise RuntimeError(f"destino criado com conteúdo diferente: {destination}")
                return "existente"
            temporary.replace(destination)
            return "copy"
        finally:
            temporary.unlink(missing_ok=True)


def _save_version_bytes(context: _VersionContext, body: bytes, base_id: str, filename_prefix: str, extension: str) -> dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    object_path = _ensure_object(body, digest)
    destination_dir = context.staging_path / _safe_component(base_id)
    suffix = extension if extension.startswith(".") else f".{extension}"
    path = destination_dir / f"{_safe_component(filename_prefix)}_{digest[:12]}{suffix}"
    storage_mode = _materialize_object(object_path, path, digest)
    relative = _relative_path(path)
    context.hash_index[digest] = relative
    return {
        "status": "ok",
        "bytes": len(body),
        "sha256": digest,
        "path": relative,
        "storage": {
            "mode": storage_mode,
            "object_path": _relative_path(object_path),
            "object_sha256": digest,
        },
    }


def _previous_record(context: _VersionContext, logical_id: str) -> dict[str, Any] | None:
    record = context.previous_items.get(logical_id)
    return record if isinstance(record, dict) and record.get("status") == "ok" else None


def _conditional_headers(record: dict[str, Any] | None) -> dict[str, str]:
    if not record:
        return {}
    headers: dict[str, str] = {}
    if record.get("etag"):
        headers["If-None-Match"] = str(record["etag"])
    if record.get("last_modified"):
        headers["If-Modified-Since"] = str(record["last_modified"])
    return headers


def _materialize_previous_record(
    context: _VersionContext,
    base_id: str,
    filename_prefix: str,
    previous: dict[str, Any],
    extension: str,
) -> dict[str, Any] | None:
    digest = str(previous.get("sha256") or "")
    raw_storage = previous.get("storage")
    storage: dict[str, Any] = raw_storage if isinstance(raw_storage, dict) else {}
    object_path = PROJECT_ROOT / str(storage.get("object_path", "")) if storage.get("object_path") else None
    source_path = object_path if object_path and object_path.exists() else _path_from_record(previous)
    if source_path is None or not source_path.exists():
        return None
    if not digest:
        digest = _file_sha256(source_path)
    elif _file_sha256(source_path) != digest:
        raise RuntimeError(f"registro anterior não corresponde ao SHA-256: {source_path}")
    object_path = _ensure_object(source_path.read_bytes(), digest)
    suffix = extension if extension.startswith(".") else f".{extension}"
    destination = context.staging_path / _safe_component(base_id) / f"{_safe_component(filename_prefix)}_{digest[:12]}{suffix}"
    storage_mode = _materialize_object(object_path, destination, digest)
    return {
        "status": "ok",
        "bytes": destination.stat().st_size,
        "sha256": digest,
        "path": _relative_path(destination),
        "storage": {
            "mode": storage_mode,
            "object_path": _relative_path(object_path),
            "object_sha256": digest,
        },
        "reutilizado_de": f"v{context.previous_version}" if context.previous_version else previous.get("path"),
        "reuso_motivo": "resposta HTTP 304" if previous.get("etag") or previous.get("last_modified") else "conteúdo anterior",
    }


def _base_record(context: _VersionContext, base: dict[str, Any], logical_id: str, origin: str, result: dict[str, Any], *, tipo: str, registros: int = 0) -> dict[str, Any]:
    path = result.get("path")
    record = {
        "logical_id": logical_id,
        "base_id": base["base_id"],
        "escopo": base.get("escopo", "experimental"),
        "natureza": base.get("natureza"),
        "tipo": tipo,
        "origem": origin,
        "coletado_em": context.collected_at,
        "manifest_hash": context.manifest_hash,
        "registros": registros,
        "source_file": path,
        "size_bytes": int(result.get("bytes", 0) or 0),
        **result,
    }
    context.items[logical_id] = record
    _checkpoint_write(context)
    return record


def _completed_item(context: _VersionContext, logical_id: str) -> dict[str, Any] | None:
    record = context.items.get(logical_id)
    return record if record and record.get("status") == "ok" and _path_from_record(record) else None


def _failed_record(
    context: _VersionContext,
    base: dict[str, Any],
    logical_id: str,
    origin: str,
    error: str,
    tipo: str = "desconhecido",
    *,
    status: str = "falhou",
) -> dict[str, Any]:
    return _base_record(
        context,
        base,
        logical_id,
        origin,
        {"status": status, "bytes": 0, "sha256": None, "path": None, "erro": error},
        tipo=tipo,
    )


def _extension_for(content_type: str, url: str, default: str = ".bin") -> str:
    content_type = content_type.lower().split(";", 1)[0]
    by_type = {"application/json": ".json", "application/pdf": ".pdf", "text/html": ".html", "application/xhtml+xml": ".html", "application/x-ndjson": ".jsonl", "application/jsonl": ".jsonl"}
    if content_type in by_type:
        return by_type[content_type]
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".json", ".jsonl", ".pdf", ".html", ".htm"} else default


def _collect_http_item(
    context: _VersionContext,
    base: dict[str, Any],
    url: str,
    logical_id: str,
    filename_prefix: str,
    *,
    expected_type: str | None = None,
) -> dict[str, Any]:
    existing = _completed_item(context, logical_id)
    if existing:
        existing["unidades_logicas"] = 1
        return existing
    previous = _previous_record(context, logical_id)
    conditional_headers = _conditional_headers(previous)
    _wait_rate_limit(context, base)
    download_dir = context.staging_path / ".downloads" / _safe_component(base["base_id"])
    try:
        result = source_check.download_url(
            url,
            download_dir,
            filename_prefix,
            attempts=3,
            timeout=30.0,
            headers=conditional_headers,
            deduplicate=False,
        )
        if result.get("status") == "nao_modificado" and previous:
            previous_path = _path_from_record(previous)
            extension = previous_path.suffix if previous_path is not None else _extension_for("", url)
            saved = _materialize_previous_record(context, str(base["base_id"]), filename_prefix, previous, extension)
            if saved is not None:
                for key in ("etag", "last_modified"):
                    if result.get(key):
                        saved[key] = result[key]
                record = _base_record(context, base, logical_id, url, saved, tipo=Path(str(saved["path"])).suffix.lstrip(".") or "bin", registros=int(previous.get("registros", 0) or 0))
                record["unidades_logicas"] = 1
                return record
        if result.get("status") not in {"baixado", "reutilizado"}:
            return _failed_record(context, base, logical_id, url, str(result.get("erro") or result.get("observacao") or result.get("status")), _extension_for(str(result.get("content_type", "")), url).lstrip("."))
        source_path = Path(str(result.get("arquivo", "")))
        if not source_path.is_absolute():
            source_path = PROJECT_ROOT / source_path
        if not source_path.exists():
            return _failed_record(context, base, logical_id, url, "download sem arquivo resultante")
        extension = source_path.suffix or _extension_for(str(result.get("content_type", "")), url)
        if expected_type and extension in {"", ".bin"}:
            extension = f".{expected_type.lstrip('.') }"
        saved = _save_version_bytes(context, source_path.read_bytes(), str(base["base_id"]), filename_prefix, extension)
        if previous and previous.get("sha256") == saved.get("sha256"):
            saved["reutilizado_de"] = f"v{context.previous_version}" if context.previous_version else previous.get("path")
            saved["reuso_motivo"] = "SHA-256 idêntico"
        for key in ("etag", "last_modified", "url_final"):
            if result.get(key):
                saved[key] = result[key]
        source_path.unlink(missing_ok=True)
        record = _base_record(context, base, logical_id, url, saved, tipo=Path(str(saved["path"])).suffix.lstrip(".") or "bin")
        record["unidades_logicas"] = 1
        return record
    except Exception as exc:
        return _failed_record(context, base, logical_id, url, f"{type(exc).__name__}: {exc}")


def _build_request_url(endpoint: str, params: dict[str, Any]) -> str:
    endpoint = _clean_url(endpoint)
    split = urlsplit(endpoint)
    query: dict[str, Any] = {}
    if split.query:
        for part in split.query.split("&"):
            key, separator, value = part.partition("=")
            query[key] = value if separator else ""
    date_fields = {"dataInicial", "dataFinal", "dataInicio", "dataFim"}
    query.update(
        {
            str(key): (
                value.replace("-", "")
                if str(key) in date_fields
                and isinstance(value, str)
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
                else value
            )
            for key, value in params.items()
            if value is not None
        }
    )
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _payload_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "content"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _selection_limit(base: dict[str, Any]) -> int | None:
    selection = base.get("selecao") or {}
    raw_limit = selection.get("limite") or selection.get("limit")
    if raw_limit in (None, ""):
        return None
    return max(1, int(raw_limit))


def _selection_key(record: Any, base: dict[str, Any]) -> str:
    selection = base.get("selecao") or {}
    field = str(selection.get("chave") or selection.get("key") or "")
    if isinstance(record, dict) and field and record.get(field) not in (None, ""):
        return str(record[field])
    return json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)


def _payload_with_records(payload: Any, records: list[Any]) -> Any:
    if isinstance(payload, list):
        return records
    if isinstance(payload, dict):
        result = dict(payload)
        for key in ("data", "items", "results", "content"):
            if isinstance(payload.get(key), list):
                result[key] = records
                return result
    return {"data": records}


def _collect_selected_rest_endpoint(
    context: _VersionContext,
    base: dict[str, Any],
    endpoint_name: str,
    endpoint: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Coleta páginas completas e persiste somente a seleção canônica."""
    endpoint = _clean_url(endpoint)
    access = base.get("acesso", {})
    params = dict(access.get("params") or {})
    pagination = access.get("paginacao") or {
        "campo": "pagina",
        "tamanho": params.get("tamanhoPagina", 50),
    }
    page_field = str(pagination.get("campo") or "pagina")
    page_size = max(
        1,
        int(
            pagination.get("tamanho")
            or params.get("tamanhoPagina")
            or access.get("tamanho_pagina")
            or 50
        ),
    )
    max_pages = max(1, int((base.get("selecao") or {}).get("max_paginas") or 100))
    all_records: list[Any] = []
    seen_pages: set[str] = set()
    last_payload: Any = {"data": []}
    for page in range(1, max_pages + 1):
        request_params = dict(params)
        request_params[page_field] = page
        url = _build_request_url(endpoint, request_params)
        response = source_check.request_url(url, "json", attempts=3, timeout=30.0)
        payload = response.get("json")
        if payload is None:
            return [
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:{endpoint_name}:selecao",
                    url,
                    str(response.get("erro") or response.get("status") or "resposta JSON ausente"),
                    "json",
                )
            ]
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest in seen_pages:
            break
        seen_pages.add(digest)
        last_payload = payload
        records = _payload_records(payload)
        all_records.extend(records)
        if not records or len(records) < page_size:
            break

    unique = {_selection_key(record, base): record for record in all_records}
    selected = [unique[key] for key in sorted(unique)[:limit]]
    selected_payload = _payload_with_records(last_payload, selected)
    saved = _save_version_bytes(
        context,
        json.dumps(selected_payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        str(base["base_id"]),
        f"{endpoint_name}_selecionado",
        ".json",
    )
    logical_id = f"{base['base_id']}:{endpoint_name}:selecionado"
    record = _base_record(
        context,
        base,
        logical_id,
        endpoint,
        saved,
        tipo="json",
        registros=len(selected),
    )
    record["selecao"] = {
        "limite": limit,
        "observados": len(all_records),
        "deduplicados": len(unique),
    }
    _checkpoint_write(context)
    output = [record]
    if len(selected) < limit:
        output.append(
            _failed_record(
                context,
                base,
                f"{base['base_id']}:{endpoint_name}:alvo",
                endpoint,
                f"alvo de {limit} registros não atingido; somente {len(selected)} selecionados",
                "json",
            )
        )
    return output


def _collect_rest_endpoint(context: _VersionContext, base: dict[str, Any], endpoint_name: str, endpoint: str) -> list[dict[str, Any]]:
    endpoint = _clean_url(endpoint)
    if "{" in endpoint or "}" in endpoint:
        return [_failed_record(context, base, f"{base['base_id']}:{endpoint_name}:template", endpoint, "endpoint possui placeholders e precisa de adapter de descoberta PNCP", "json")]
    selection_limit = _selection_limit(base)
    if selection_limit is not None:
        return _collect_selected_rest_endpoint(context, base, endpoint_name, endpoint, selection_limit)
    access = base.get("acesso", {})
    params = dict(access.get("params") or {})
    pagination = access.get("paginacao") or (
        {"campo": "pagina", "tamanho": access.get("params", {}).get("tamanhoPagina", 50)}
        if "pagina" in params
        else {}
    )
    page_field = str(
        pagination.get("campo") or pagination.get("campo_pagina") or "pagina"
    ) if pagination else ""
    page_size = int(
        pagination.get("tamanho")
        or pagination.get("campo_tamanho")
        or params.get("tamanhoPagina")
        or access.get("tamanho_pagina")
        or 50
    )
    page = int(params.get(page_field, 1) or 1) if page_field else 1
    output: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    while True:
        request_params = dict(params)
        if page_field:
            request_params[page_field] = page
        url = _build_request_url(endpoint, request_params)
        logical_id = f"{base['base_id']}:{endpoint_name}:pagina={page}"
        existing = _completed_item(context, logical_id)
        if existing:
            output.append(existing)
            if not page_field or int(existing.get("registros", 0) or 0) < page_size:
                break
            page += 1
            continue
        previous = _previous_record(context, logical_id)
        _wait_rate_limit(context, base)
        try:
            response = source_check.request_url(
                url,
                "json",
                attempts=3,
                timeout=30.0,
                headers=_conditional_headers(previous),
            )
            if response.get("status") == "nao_modificado" and previous:
                saved = _materialize_previous_record(context, str(base["base_id"]), f"{endpoint_name}_pagina_{page}", previous, ".json")
                if saved is not None:
                    saved["registros"] = int(previous.get("registros", 0) or 0)
                    for key in ("etag", "last_modified"):
                        if response.get(key):
                            saved[key] = response[key]
                    output.append(_base_record(context, base, logical_id, url, saved, tipo="json", registros=int(previous.get("registros", 0) or 0)))
                    break
            payload = response.get("json")
            if payload is None:
                output.append(_failed_record(context, base, logical_id, url, str(response.get("erro") or response.get("status") or "resposta JSON ausente"), "json"))
                break
            body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            page_digest = hashlib.sha256(body).hexdigest()
            if page_digest in seen_pages:
                break
            seen_pages.add(page_digest)
            saved = _save_version_bytes(context, body, str(base["base_id"]), f"{endpoint_name}_pagina_{page}", ".json")
            if previous and previous.get("sha256") == saved.get("sha256"):
                saved["reutilizado_de"] = f"v{context.previous_version}" if context.previous_version else previous.get("path")
                saved["reuso_motivo"] = "SHA-256 idêntico"
            for key in ("etag", "last_modified", "url_final"):
                if response.get(key):
                    saved[key] = response[key]
            records = _payload_records(payload)
            output.append(_base_record(context, base, logical_id, url, saved, tipo="json", registros=len(records)))
            if not page_field or not records or len(records) < page_size:
                break
            page += 1
        except Exception as exc:
            output.append(_failed_record(context, base, logical_id, url, f"{type(exc).__name__}: {exc}", "json"))
            break
    return output


def _collect_local_base(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    access = base.get("acesso", {})
    origin = access.get("origem") or base.get("local")
    if not origin:
        return [_failed_record(context, base, f"{base['base_id']}:local", "local", "origem local não declarada")]
    source = PROJECT_ROOT / str(origin)
    paths = sorted(source.rglob("*") if source.is_dir() else [source])
    paths = [path for path in paths if path.is_file()]
    if not paths:
        return [_failed_record(context, base, f"{base['base_id']}:local", str(source), "nenhum arquivo local encontrado")]
    output: list[dict[str, Any]] = []
    for path in paths:
        logical_id = f"{base['base_id']}:{_relative_path(path)}"
        existing = _completed_item(context, logical_id)
        if existing:
            output.append(existing)
            continue
        try:
            body = path.read_bytes()
            formato = str(base.get("formato") or path.suffix.lstrip(".")).lower()
            if formato == "jsonl" and path.suffix.lower() == ".json":
                payload = json.loads(body.decode("utf-8"))
                records = payload if isinstance(payload, list) else payload.get("data", [payload]) if isinstance(payload, dict) else [payload]
                body = b"".join(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n" for record in records)
                extension, registros = ".jsonl", len(records)
            else:
                extension, registros = path.suffix or ".bin", 1
            saved = _save_version_bytes(context, body, str(base["base_id"]), path.stem, extension)
            output.append(_base_record(context, base, logical_id, _relative_path(path), saved, tipo=extension.lstrip("."), registros=registros))
        except Exception as exc:
            output.append(_failed_record(context, base, logical_id, _relative_path(path), f"{type(exc).__name__}: {exc}"))
    return output


def _collect_pncp_documents(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    """Descobre e baixa documentos PNCP até cumprir o alvo declarado.

    A descoberta usa páginas sucessivas da API. O limite é controlado pelo
    manifesto para evitar que uma fonte indisponível provoque uma coleta
    infinita. Cada URL é baixada no mesmo contexto de checkpoint da versão.
    """
    params = (base.get("acesso") or {}).get("params") or {}
    try:
        year = int(params.get("ano", 2025))
        page_size = max(1, int(params.get("tamanhoPagina", 10)))
        target = max(1, int(base.get("volume_alvo", 1)))
    except (TypeError, ValueError):
        year, page_size, target = 2025, 10, 1

    try:
        max_pages = max(
            1,
            int(params.get("maxPaginas", (target + page_size - 1) // page_size + 2)),
        )
    except (TypeError, ValueError):
        max_pages = (target + page_size - 1) // page_size + 2

    output: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    page = 1
    while page <= max_pages:
        _wait_rate_limit(context, base)
        config = source_check.CheckConfig(
            ano=year,
            uf=str(params.get("uf", "RJ")).upper(),
            modalidade=params.get("codigoModalidadeContratacao"),
            tamanho_pagina=page_size,
            pagina=page,
            timeout=30.0,
            attempts=3,
            data_inicial=params.get("dataInicial"),
            data_final=params.get("dataFinal"),
        )
        _, results = _run_checks_for_types(
            config,
            ("pdf",),
            before_request=lambda: _wait_rate_limit(context, base),
        )
        candidates = [candidate for candidate in iter_candidates(results) if candidate.folder == "pncp"]
        new_candidates = [candidate for candidate in candidates if candidate.url not in seen_urls]
        for candidate in new_candidates:
            seen_urls.add(candidate.url)
            output.append(
                _collect_http_item(
                    context,
                    base,
                    candidate.url,
                    f"{base['base_id']}:{candidate.candidate_id}",
                    candidate.filename_prefix,
                )
            )
            pdf_successes = sum(
                1 for record in output if record.get("status") == "ok" and record.get("tipo") == "pdf"
            )
            if pdf_successes >= target:
                return output
        if not new_candidates:
            break
        page += 1

    pdf_successes = sum(1 for record in output if record.get("status") == "ok" and record.get("tipo") == "pdf")
    if not output:
        return [_failed_record(context, base, f"{base['base_id']}:discovery", "PNCP", "nenhum arquivo PNCP foi descoberto")]
    if pdf_successes < target:
        output.append(
            _failed_record(
                context,
                base,
                f"{base['base_id']}:target",
                "PNCP",
                f"alvo de {target} PDFs não atingido; somente {pdf_successes} foram coletados",
                "pdf",
            )
        )
    return output


def _collect_resource_set(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    """Coleta um conjunto pequeno e determinístico de recursos declarados."""
    access = base.get("acesso") or {}
    resources = access.get("recursos") or access.get("resources") or []
    output: list[dict[str, Any]] = []
    for index, resource in enumerate(resources, start=1):
        if isinstance(resource, str):
            resource = {"url": resource}
        if not isinstance(resource, dict) or not resource.get("url"):
            output.append(
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:resource:{index}",
                    "resource_set",
                    "recurso sem URL declarada",
                )
            )
            continue

        url = _clean_url(str(resource["url"]))
        resource_id = _safe_component(str(resource.get("id") or f"{index:03d}"))
        kind = str(resource.get("tipo") or _extension_for("", url)).lower().lstrip(".")
        logical_id = f"{base['base_id']}:resource:{resource_id}"
        if kind == "json":
            resource_access = {
                **access,
                "endpoint": url,
                "params": resource.get("params") or {},
                "paginacao": resource.get("paginacao") or {},
            }
            resource_base = {**base, "acesso": resource_access}
            output.extend(
                _collect_rest_endpoint(
                    context,
                    resource_base,
                    resource_id,
                    url,
                )
            )
        else:
            output.append(
                _collect_http_item(
                    context,
                    base,
                    url,
                    logical_id,
                    f"{base['base_id']}_{resource_id}",
                    expected_type=kind,
                )
            )
    return output or [
        _failed_record(
            context,
            base,
            f"{base['base_id']}:resource-set",
            "resource_set",
            "nenhum recurso declarado",
        )
    ]


def _collect_provider_base(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    """Materializa referências explícitas usando o mesmo provider da curadoria."""
    access = base.get("acesso") or {}
    provider_name = str(access.get("provider") or "").strip()
    if not provider_name:
        return [_failed_record(context, base, f"{base['base_id']}:provider", "provider", "provider não declarado")]

    provider = get_provider(provider_name)
    for logical_id, item in list(context.items.items()):
        if item.get("base_id") == base.get("base_id") and item.get("status") != "ok":
            context.items.pop(logical_id, None)
    sources = access.get("sources") or []
    candidates = []
    discovered = False
    target = int(base.get("volume_alvo") or 1)
    resource_type = str(access.get("resource_type") or "").casefold()
    resumed_source_ids: set[str] = set()
    selected_entries = getattr(context, "selection", {}).get(str(base["base_id"]))
    if not sources and selected_entries:
        sources = [
            entry["source_ref"]
            for entry in selected_entries
            if isinstance(entry, dict) and isinstance(entry.get("source_ref"), dict)
        ]
        resumed_source_ids = {
            SourceRef.from_dict(source_value).stable_id
            for source_value in sources
        }
    if not sources:
        existing_sources: dict[str, dict[str, Any]] = {}
        for item in context.items.values():
            if item.get("base_id") != base.get("base_id") or not int(item.get("unidades_logicas", 0) or 0):
                continue
            source_value = item.get("source_ref")
            if not isinstance(source_value, dict):
                continue
            try:
                source_ref = SourceRef.from_dict(source_value)
            except (TypeError, ValueError, KeyError):
                continue
            if source_ref.resource_type.casefold() == resource_type:
                existing_sources[source_ref.stable_id] = source_ref.to_dict()
        if len(existing_sources) >= target:
            sources = [existing_sources[key] for key in sorted(existing_sources)[:target]]
            resumed_source_ids = {
                SourceRef.from_dict(source_value).stable_id
                for source_value in sources
            }
    if not sources:
        _wait_rate_limit(context, base)
        candidates = provider.search(
            {
                **access,
                "resource_type": access.get("resource_type"),
                "volume_alvo": target,
                "selecao": base.get("selecao") or {},
            }
        )
        sources = [candidate.source_ref.to_dict() for candidate in candidates]
        discovered = True
        if not sources:
            return [
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:provider:search",
                    provider_name,
                    "nenhuma SourceRef encontrada",
                )
                ]

    sources = sources[:target]
    if discovered or str(base["base_id"]) not in getattr(context, "selection", {}):
        _set_selection(context, str(base["base_id"]), sources)
        _checkpoint_write(context)

    if resumed_source_ids:
        cached_records = [
            item
            for item in context.items.values()
            if item.get("base_id") == base.get("base_id")
            and item.get("status") == "ok"
            and isinstance(item.get("source_ref"), dict)
            and SourceRef.from_dict(item["source_ref"]).stable_id in resumed_source_ids
        ]
        cached_source_ids = {
            SourceRef.from_dict(item["source_ref"]).stable_id
            for item in cached_records
        }
        if cached_source_ids == resumed_source_ids:
            return sorted(cached_records, key=lambda item: str(item.get("logical_id") or ""))
        sources = [
            source_value
            for source_value in sources
            if SourceRef.from_dict(source_value).stable_id not in cached_source_ids
        ]

    output: list[dict[str, Any]] = []
    seen_artifact_urls: set[str] = set()
    for index, source_value in enumerate(sources, start=1):
        try:
            source_ref = SourceRef.from_dict(
                {**source_value, "provider": source_value.get("provider", provider_name)}
            )
            candidate = next(
                (item for item in candidates if item.source_ref.stable_id == source_ref.stable_id),
                None,
            )
            if (
                candidate is not None
                and resource_type in {"contratacao", "contratação", "contrato"}
                and candidate.metadata
            ):
                payload = {
                    key: value for key, value in candidate.metadata.items() if not str(key).startswith("_")
                }
                body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                saved = _save_version_bytes(
                    context,
                    body,
                    str(base["base_id"]),
                    f"{_safe_component(source_ref.stable_id)}_publication",
                    ".json",
                )
                logical_id = f"{base['base_id']}:{source_ref.stable_id}:publication"
                record = _base_record(
                    context,
                    base,
                    logical_id,
                    str(candidate.metadata.get("_source_url") or source_ref.stable_id),
                    saved,
                    tipo="json",
                    registros=1,
                )
                record["source_ref"] = source_ref.to_dict()
                record["artifact_id"] = f"{source_ref.stable_id}:publication"
                record["provider"] = provider_name
                record["unidades_logicas"] = 1
                context.items[logical_id] = record
                _checkpoint_write(context)
                output.append(record)
                continue
            resolved = provider.resolve(source_ref)
            for artifact in provider.list_artifacts(resolved):
                if artifact.url in seen_artifact_urls:
                    continue
                seen_artifact_urls.add(artifact.url)
                logical_id = f"{base['base_id']}:{artifact.artifact_id}"
                if artifact.artifact_type.casefold() == "json":
                    resource_base = {
                        **base,
                        # A seleção limita a busca de unidades lógicas; no detalhe,
                        # cada artefato já representa uma única unidade resolvida.
                        "selecao": {},
                        "acesso": {
                            **access,
                            "endpoint": artifact.url,
                            "params": {},
                            "paginacao": {},
                        },
                    }
                    records = _collect_rest_endpoint(
                        context,
                        resource_base,
                        _safe_component(artifact.artifact_id),
                        artifact.url,
                    )
                else:
                    records = [
                        _collect_http_item(
                            context,
                            base,
                            artifact.url,
                            logical_id,
                            Path(artifact.filename).stem if artifact.filename else f"{base['base_id']}_{index:03d}",
                            expected_type=artifact.artifact_type,
                        )
                    ]
                for record in records:
                    record["source_ref"] = source_ref.to_dict()
                    record["artifact_id"] = artifact.artifact_id
                    record["provider"] = provider_name
                    record["unidades_logicas"] = int(artifact.metadata.get("logical_unit", True))
                    context.items[record["logical_id"]] = record
                    _checkpoint_write(context)
                    output.append(record)
        except Exception as exc:
            output.append(
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:provider:{index}",
                    provider_name,
                    f"{type(exc).__name__}: {exc}",
                )
            )
    if discovered and len(sources) < target:
        output.append(
            _failed_record(
                context,
                base,
                f"{base['base_id']}:provider:alvo",
                provider_name,
                f"alvo de {target} unidades não atingido; somente {len(sources)} encontradas",
            )
        )
    return output

def _collect_base(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    access = base.get("acesso", {})
    kind = str(access.get("tipo", "")).lower()
    if kind == "not_configured":
        return [
            _failed_record(
                context,
                base,
                f"{base['base_id']}:not-configured",
                "not_configured",
                "coletor oficial ainda não configurado",
                "not_configured",
                status="not_configured",
            )
        ]
    if kind in {"http_pdf", "http_misto", "http_csv"}:
        urls = access.get("urls") or ([access["url"]] if access.get("url") else [])
        return [_collect_http_item(context, base, _clean_url(url), f"{base['base_id']}:url:{index}", f"{base['base_id']}_{index:03d}") for index, url in enumerate(urls, start=1)]
    if kind in {"rest_api", "rest_api_mais_arquivo"}:
        endpoints = access.get("endpoints") or {"principal": access.get("endpoint")}
        if kind == "rest_api_mais_arquivo" and any("{" in str(endpoint) for endpoint in endpoints.values() if endpoint):
            return _collect_pncp_documents(context, base)
        output: list[dict[str, Any]] = []
        for endpoint_name, endpoint in endpoints.items():
            if endpoint:
                output.extend(_collect_rest_endpoint(context, base, str(endpoint_name), str(endpoint)))
        return output or [_failed_record(context, base, f"{base['base_id']}:rest", "rest_api", "endpoint não declarado")]
    if kind in {"resource_set", "pncp_resource_set"}:
        return _collect_resource_set(context, base)
    if kind == "provider":
        return _collect_provider_base(context, base)
    if kind in {"arquivo_local", "processamento_local"}:
        return _collect_local_base(context, base)
    return [_failed_record(context, base, f"{base['base_id']}:adapter", kind, f"tipo de acesso não suportado: {kind}")]


def _build_inventory(
    manifest: dict[str, Any],
    version: int,
    manifest_hash: str,
    collected_at: str,
    items: dict[str, dict[str, Any]],
    *,
    inventory_type: str = "manifest",
    manifest_bound: bool = True,
    official: bool = True,
) -> dict[str, Any]:
    records = sorted(items.values(), key=lambda record: str(record.get("logical_id", "")))
    successful = [record for record in records if record.get("status") == "ok" and record.get("path")]
    failures = [record for record in records if record.get("status") != "ok"]
    by_base: list[dict[str, Any]] = []
    for base in manifest.get("bases", []):
        base_records = [record for record in records if record.get("base_id") == base["base_id"]]
        successful_base = [record for record in base_records if record.get("status") == "ok" and record.get("path")]
        base_failures = [record for record in base_records if record.get("status") != "ok"]
        observed_records = sum(
            int(
                record.get(
                    "unidades_logicas",
                    record.get("registros", 0),
                )
                or 0
            )
            for record in successful_base
        )
        has_record_counts = any(
            "unidades_logicas" in record or "registros" in record
            for record in successful_base
        )
        observed = observed_records if has_record_counts else len(successful_base)
        formatos: dict[str, int] = {}
        for record in successful_base:
            tipo = str(record.get("tipo") or "bin")
            formatos[tipo] = formatos.get(tipo, 0) + 1
        target = base.get("volume_alvo")
        target_value = int(target) if target is not None else None
        if any(record.get("status") == "not_configured" for record in base_records):
            base_status = "not_configured"
        elif not base_records:
            base_status = "empty"
        elif not successful_base:
            base_status = "failed"
        elif base_failures or (target_value is not None and observed < target_value):
            base_status = "partial"
        else:
            base_status = "complete"
        by_base.append({
            "base_id": base["base_id"],
            "escopo": base.get("escopo", "experimental"),
            "natureza": base.get("natureza"),
            "status": base_status,
            "arquivos": len(successful_base),
            "itens": observed,
            "formatos": formatos,
            "bytes": sum(int(record.get("bytes", 0) or 0) for record in successful_base),
            "volume_alvo": target,
            "volume_obtido": observed,
            "volume_observado": observed,
            "divergencia_alvo": observed - target_value if target_value is not None else None,
            "failure_count": len(base_failures),
        })
    total_items = sum(
        int(record.get("unidades_logicas", record.get("registros", 0)) or 0)
        for record in successful
    )
    return {
        "inventory_type": inventory_type,
        "official": official,
        "manifest_bound": manifest_bound,
        "scenario_id": manifest.get("scenario_id") if manifest_bound else None,
        "version": f"v{version}",
        "coletado_em": collected_at,
        "manifest_hash": manifest_hash if manifest_bound else None,
        "completo": not failures and all(base["status"] == "complete" for base in by_base),
        "totais": {
            "arquivos": len(successful),
            "itens": total_items,
            "bytes": sum(int(record.get("bytes", 0) or 0) for record in successful),
            "falhas": len(failures),
            "failure_count": len(failures),
        },
        "por_base": by_base,
        "arquivos": records,
        "falhas": [{"origem": record.get("origem"), "erro": record.get("erro", "falha sem detalhe"), "logical_id": record.get("logical_id")} for record in failures],
    }


def _write_inventory(inventory: dict[str, Any]) -> Path:
    INVENTORY_ROOT.mkdir(parents=True, exist_ok=True)
    path = INVENTORY_ROOT / f"{inventory['version']}.json"
    path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _render_corpus_inventory(inventory: dict[str, Any]) -> None:
    totals = inventory["totais"]
    lines = [
        "# Inventário do corpus", "", f"- Tipo: `{inventory.get('inventory_type', 'manifest')}`", f"- Oficial: `{inventory.get('official', False)}`", f"- Vinculado ao manifesto: `{inventory.get('manifest_bound', False)}`", f"- Cenário: `{inventory.get('scenario_id') or '—'}`", f"- Versão: `{inventory.get('version')}`",
        f"- Coletado em: `{inventory.get('coletado_em')}`", f"- Completo: **{inventory.get('completo', False)}**", f"- Arquivos: **{totals.get('arquivos', 0)}**", f"- Itens/registros: **{totals.get('itens', 0)}**", f"- Bytes: **{totals.get('bytes', 0)}**", f"- Falhas: **{totals.get('falhas', 0)}**", "",
        "## Bases", "", "| Base | Escopo | Natureza | Status | Arquivos | Itens | Formatos | Bytes | Alvo | Divergência |", "|---|---|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for base in inventory.get("por_base", []):
        formatos = base.get("formatos") or {}
        formatos_text = ", ".join(f"{tipo}: {quantidade}" for tipo, quantidade in formatos.items()) if isinstance(formatos, dict) else ", ".join(formatos)
        alvo = base.get("volume_alvo") if base.get("volume_alvo") is not None else "—"
        divergencia = base.get("divergencia_alvo") if base.get("divergencia_alvo") is not None else "—"
        lines.append(f"| `{base['base_id']}` | {base.get('escopo') or '—'} | {base.get('natureza') or '—'} | {base.get('status') or '—'} | {base.get('arquivos', 0)} | {base.get('itens', 0)} | {formatos_text or '—'} | {base.get('bytes', 0)} | {alvo} | {divergencia} |")
    lines.extend(["", "## Falhas", ""])
    failures = inventory.get("falhas", [])
    lines.extend((f"- `{failure.get('origem')}`: {failure.get('erro')}" for failure in failures) if failures else ["Nenhuma falha registrada."])
    CORPUS_INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_INVENTORY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_inventory(version: int) -> dict[str, Any] | None:
    path = _inventory_path(version)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _scan_existing_version(version: int) -> dict[str, Any]:
    root = _version_path(version)
    if not root.exists():
        raise FileNotFoundError(f"versão não encontrada: v{version}")
    records: list[dict[str, Any]] = []
    legacy_base_id = "legacy_physical"
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).as_posix().split("/")
        if not parts:
            continue
        physical_group = parts[1] if parts[:1] == ["arquivos"] and len(parts) > 1 else parts[0]
        base_id = legacy_base_id
        body = path.read_bytes()
        records.append({"logical_id": f"{base_id}:{_relative_path(path)}", "base_id": base_id, "natureza": "não classificada", "physical_group": physical_group, "tipo": path.suffix.lstrip(".") or "bin", "origem": "inventário reconstruído a partir do armazenamento", "coletado_em": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"), "registros": 0, "status": "ok", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "path": _relative_path(path)})
    bases = [{"base_id": legacy_base_id, "natureza": "não classificada"}]
    return _build_inventory({"scenario_id": None, "bases": bases}, version, "", datetime.now(timezone.utc).isoformat(timespec="seconds"), {record["logical_id"]: record for record in records}, inventory_type="legacy_physical", manifest_bound=False, official=True)


def _inventory_for_version(version: int) -> dict[str, Any]:
    inventory = _load_inventory(version)
    legacy_needs_rebuild = bool(
        inventory
        and inventory.get("inventory_type") == "legacy_physical"
        and (
            "official" not in inventory
            or any(record.get("base_id") != "legacy_physical" for record in inventory.get("arquivos", []))
        )
    )
    if inventory is None or inventory.get("inventory_type") not in {"manifest", "legacy_physical"} or legacy_needs_rebuild:
        inventory = _scan_existing_version(version)
        _write_inventory(inventory)
    _render_corpus_inventory(inventory)
    return inventory


def _diff_inventories(previous: dict[str, Any], current: dict[str, Any]) -> Path:
    def keyed(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(record.get("logical_id") or f"{record.get('base_id')}:{record.get('path')}"): record for record in inventory.get("arquivos", [])}
    old, new = keyed(previous), keyed(current)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(key for key in set(old) & set(new) if old[key].get("sha256") != new[key].get("sha256"))
    identical = sorted(key for key in set(old) & set(new) if old[key].get("sha256") == new[key].get("sha256"))
    INVENTORY_ROOT.mkdir(parents=True, exist_ok=True)
    path = INVENTORY_ROOT / f"DIFF_{previous['version']}_{current['version']}.md"
    lines = [f"# Diff {previous['version']} → {current['version']}", "", f"- Adicionados: **{len(added)}**", f"- Removidos: **{len(removed)}**", f"- Alterados: **{len(changed)}**", f"- Idênticos: **{len(identical)}**"]
    for title, values in (("Adicionados", added), ("Removidos", removed), ("Alterados", changed), ("Idênticos", identical)):
        lines.extend(["", f"## {title}", ""])
        lines.extend((f"- `{value}`" for value in values) if values else ["Nenhum."])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _promote_version(context: _VersionContext) -> None:
    destination = _version_path(context.version)
    if destination.exists():
        raise FileExistsError(f"versão imutável já existe: {destination}")
    for logical_id, record in context.items.items():
        if record.get("status") != "ok":
            continue
        path = _path_from_record(record)
        if path is None or not path.is_file():
            raise RuntimeError(f"arquivo coletado ausente antes da promoção: {logical_id}")
        if not record.get("sha256"):
            raise RuntimeError(f"arquivo coletado sem SHA-256 antes da promoção: {logical_id}")
        if _file_sha256(path) != record["sha256"]:
            raise RuntimeError(f"SHA-256 divergente antes da promoção: {logical_id}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(context.staging_path), str(destination))
    old_prefix = _relative_path(context.staging_path).rstrip("/") + "/"
    new_prefix = _relative_path(destination).rstrip("/") + "/"
    for record in context.items.values():
        if isinstance(record.get("path"), str) and record["path"].startswith(old_prefix):
            record["path"] = new_prefix + record["path"][len(old_prefix):]
            record["source_file"] = record["path"]
    _checkpoint_write(context, state="promoted")


def _find_complete_matching_manifest(manifest_hash: str) -> dict[str, Any] | None:
    for version in reversed(_available_versions()):
        inventory = _load_inventory(version)
        if inventory and inventory.get("manifest_hash") == manifest_hash and inventory.get("completo"):
            return inventory
    return None


def _find_resumable_version(
    manifest_hash: str,
    manifest: dict[str, Any] | None = None,
) -> int | None:
    checkpoint_root = INVENTORY_ROOT / "checkpoints"
    if not checkpoint_root.exists():
        return None
    checkpoints = sorted(
        checkpoint_root.glob("v*.json"),
        key=lambda path: _version_number(path.stem) or -1,
        reverse=True,
    )
    for checkpoint_path in checkpoints:
        version = _version_number(checkpoint_path.stem)
        if version is None:
            continue
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        staging_path = RAW_ROOT / ".staging" / f"v{version}"
        if (
            checkpoint.get("manifest_hash") == manifest_hash
            and checkpoint.get("state") in {"staging", "incomplete"}
            and staging_path.exists()
        ):
            if manifest is None:
                return version
            validation = _validate_checkpoint(
                checkpoint,
                manifest,
                manifest_hash,
                version,
                staging_path,
            )
            if validation.ok:
                return version
    return None


def _run_manifest_command(
    command: str,
    manifest_path: Path,
    *,
    official: bool,
    base_ids: list[str] | None = None,
) -> int:
    logger = _RunLogger(command)
    logger.event("manifest_load", "started", manifest=str(manifest_path))
    manifest = load_manifest(manifest_path)
    manifest_hash = _manifest_hash(manifest_path)
    if official and not base_ids:
        manifest = _manifest_for_scope(manifest, official=True)
    if base_ids:
        if official:
            raise ValueError("--base-id só pode ser usado em uma execução experimental")
        available = {str(base["base_id"]): base for base in manifest["bases"]}
        unknown = sorted(set(base_ids) - set(available))
        if unknown:
            raise ValueError(f"base(s) não declarada(s) no manifesto: {', '.join(unknown)}")
        manifest = {**manifest, "bases": [available[base_id] for base_id in base_ids]}
    logger.event(
        "manifest_load",
        "completed",
        scenario_id=manifest.get("scenario_id"),
        manifest_hash=manifest_hash,
    )
    if command == "fetch":
        existing = None if base_ids else _find_complete_matching_manifest(manifest_hash)
        if existing:
            _render_corpus_inventory(existing)
            logger.event("version_resolution", "reused", version=existing["version"])
            print(
                json.dumps(
                    {"version": existing["version"], "reutilizado": True, "run_id": logger.run_id},
                    ensure_ascii=False,
                )
            )
            return 0
    resumable_version = _find_resumable_version(manifest_hash, manifest)
    previous = None
    if command == "update":
        latest = _latest_version()
        if resumable_version is not None and latest == resumable_version:
            versions = [number for number in _available_versions() if number != resumable_version]
            latest = max(versions) if versions else None
        if latest is None:
            raise RuntimeError("update exige uma versão anterior")
        previous = _inventory_for_version(latest)
    version = resumable_version or _next_version()
    previous_items = {
        str(record["logical_id"]): record
        for record in (previous or {}).get("arquivos", [])
        if isinstance(record, dict) and record.get("logical_id")
    }
    previous_version = _version_number(str(previous["version"])) if previous and previous.get("version") else None
    version_lock = _VersionLock(version)
    version_lock.acquire()
    try:
        context = _load_version_context(
            manifest,
            manifest_hash,
            version,
            previous_items=previous_items,
            previous_version=previous_version,
        )
        logger.event(
            "version_resolution",
            "resumed" if resumable_version is not None else "started",
            version=f"v{version}",
        )
        for base in manifest["bases"]:
            started = time.perf_counter()
            logger.event("base_collection", "started", base_id=base["base_id"])
            try:
                _collect_base(context, base)
            except Exception as exc:
                logger.event(
                    "base_collection",
                    "failed",
                    base_id=base["base_id"],
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            logger.event(
                "base_collection",
                "completed",
                base_id=base["base_id"],
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                items=sum(1 for item in context.items.values() if item.get("base_id") == base["base_id"]),
            )

        inventory = _build_inventory(
            manifest,
            version,
            manifest_hash,
            context.collected_at,
            context.items,
            inventory_type="manifest" if official else "experimental",
            official=official,
        )
        validation = validate_inventory_contract(manifest, inventory, official=official)
        if not validation.ok:
            _checkpoint_write(context, state="incomplete")
            inventory_path = _write_inventory(inventory)
            _render_corpus_inventory(inventory)
            logger.event(
                "validation",
                "failed",
                version=inventory["version"],
                errors=list(validation.errors),
            )
            logger.event(
                "inventory",
                "completed",
                version=inventory["version"],
                inventory_path=_relative_path(inventory_path),
                totals=inventory["totais"],
                complete=False,
            )
            summary = {
                "version": inventory["version"],
                "inventario": _relative_path(inventory_path),
                "diff": None,
                "totais": inventory["totais"],
                "completo": False,
                "validation_errors": list(validation.errors),
                "run_id": logger.run_id,
                "log": _relative_path(logger.path),
            }
            print(json.dumps(summary, ensure_ascii=False))
            return 2
        logger.event("validation", "passed", version=inventory["version"])
        _promote_version(context)
    finally:
        version_lock.release()
    logger.event("version_promotion", "completed", version=f"v{version}")
    inventory = _build_inventory(
        manifest,
        version,
        manifest_hash,
        context.collected_at,
        context.items,
        inventory_type="manifest" if official else "experimental",
        official=official,
    )
    inventory_path = _write_inventory(inventory)
    _render_corpus_inventory(inventory)
    diff_path = _diff_inventories(previous, inventory) if previous is not None else None
    logger.event(
        "inventory",
        "completed",
        version=inventory["version"],
        inventory_path=_relative_path(inventory_path),
        totals=inventory["totais"],
        complete=inventory["completo"],
    )
    summary = {"version": inventory["version"], "inventario": _relative_path(inventory_path), "diff": _relative_path(diff_path) if diff_path else None, "totais": inventory["totais"], "completo": inventory["completo"], "run_id": logger.run_id, "log": _relative_path(logger.path)}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if inventory["completo"] else 2


def _manifest_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Materializa o corpus declarado pelo manifesto.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("fetch", "update"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--manifest", type=Path, default=MANIFEST_DEFAULT)
        command_parser.add_argument("--data-root", type=Path, default=None)
        command_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
        command_parser.add_argument(
            "--base-id",
            action="append",
            dest="base_ids",
            metavar="ID",
            help="limita a execução a uma base declarada; pode ser repetido em modo experimental",
        )
        if command == "update":
            command_parser.add_argument("--dry-run", action="store_true", help="mostra o plano sem modificar arquivos")
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--corpus-version", required=True, type=str, help="versão do corpus a inspecionar, como v1")
    inventory_parser.add_argument("--data-root", type=Path, default=None)
    inventory_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
    args = parser.parse_args(argv)
    official_root = (PROJECT_ROOT / "data").resolve()
    if args.official:
        if args.data_root is not None and args.data_root.resolve() != official_root:
            parser.error("--official não pode ser combinado com outro --data-root")
        data_root = official_root
    else:
        if args.data_root is None:
            parser.error("coletas não oficiais exigem --data-root; use --official para o corpus oficial")
        data_root = args.data_root.resolve()
    _configure_data_root(data_root)
    if args.command == "inventory":
        version = _version_number(args.corpus_version)
        if version is None:
            parser.error("--corpus-version deve usar o formato vN")
        inventory = _inventory_for_version(version)
        print(json.dumps({"version": inventory["version"], "totais": inventory["totais"]}, ensure_ascii=False))
        return 0
    if args.command == "update" and args.dry_run:
        if args.base_ids:
            parser.error("--base-id não é compatível com update --dry-run")
        return _dry_run_update(args.manifest.resolve(), official=args.official)
    run_kwargs = {"official": args.official}
    if args.base_ids:
        run_kwargs["base_ids"] = args.base_ids
    return _run_manifest_command(args.command, args.manifest.resolve(), **run_kwargs)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return _manifest_main(arguments)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"erro: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
