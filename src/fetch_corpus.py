r"""Materializa o corpus declarado pelo manifesto.

Uso:
    py -m src.fetch_corpus fetch --manifest corpus_manifest.yaml --official
    py -m src.fetch_corpus update --manifest corpus_manifest.yaml --official
    py -m src.fetch_corpus discover --manifest corpus_manifest.yaml --official --max-pages 2
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
import unicodedata
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
    from .retry import retry_delay
except ImportError:
    from providers import get_provider
    from providers.base import SourceRef
    from corpus_validator import ValidationResult, validate_inventory_contract
    from retry import retry_delay


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.obs import Stage, new_query_id, registrar
from src.log_context import current_execution
from src.offline_observation import observar_offline

source_check = importlib.import_module("src.source_catalog")  # noqa: E402
RICH_CONSOLE = Console()


class _RunLogger:
    """Registro JSONL de uma execução da coleta, sem bibliotecas externas."""

    def __init__(self, command: str) -> None:
        timestamp = datetime.now(timezone.utc)
        self.run_id = f"{command}-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{time.time_ns() % 1_000_000:06d}"
        self.path = RUNS_ROOT / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.query_id = new_query_id()
        self._stage_starts: dict[str, float] = {}

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
        context = current_execution()
        started = self._stage_starts.setdefault(stage, time.perf_counter())
        if status == "started":
            self._stage_starts[stage] = time.perf_counter()
        registrar(run_id=context.run_id if context else self.run_id,
            query_id=context.query_id if context else self.query_id, stage=Stage.COLETA,
            status={"started": "iniciado", "completed": "ok", "passed": "ok", "failed": "erro",
                    "reused": "ok"}.get(status, status),
            latencia_ms=0 if status == "started" else (time.perf_counter() - started) * 1000,
            input={"etapa_coleta": stage}, output=fields,
            extra={"tipo_pipeline": "offline", "origem": "offline", "etapa_coleta": stage,
                   "run_id_legado": self.run_id, "log_legado": str(self.path)})


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

@dataclass
class _TypedChecks:
    """Consultas das fontes compatíveis com os tipos pedidos, acumuladas em ``results``."""

    config: Any
    selected: set[str]
    before_request: Callable[[], None] | None
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def wants_json(self) -> bool:
        return bool({"json", "jsonl"} & self.selected)

    def request(self, source_id: str, name: str, url: str, kind: str) -> dict[str, Any]:
        if self.before_request is not None:
            self.before_request()
        payload = source_check.request_url(
            url,
            kind,
            attempts=self.config.attempts,
            timeout=self.config.timeout,
        )
        self.results.append(
            {
                "id": source_id,
                "nome": name,
                "url": url,
                **payload,
            }
        )
        return payload

    def web_sources(self) -> None:
        for source_id, name, url, kind in source_check.WEB_SOURCES:
            if kind == "html" and "html" in self.selected:
                self.request(source_id, name, url, kind)
            elif kind == "pdf" and "pdf" in self.selected:
                self.request(source_id, name, url, kind)
            elif kind == "json" and self.wants_json:
                self.request(source_id, name, url, kind)

    def purchases(self) -> None:
        """Publicação, contratos e, para as compras de amostra, detalhe, arquivos e resultados."""
        config = self.config
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
            before_request=self.before_request,
        )
        self.results.append(
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

        if self.wants_json:
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
            contracts = self.request("contratos", "PNCP — contratos", contracts_url, "json")
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
                purchase_samples if "pdf" in self.selected else [purchase_samples[0]]
            )
            for index, purchase_item in enumerate(purchases_for_files, start=1):
                self.purchase_files(index, purchase_item)

    def purchase_files(self, index: int, purchase_item: dict[str, str]) -> None:
        purchase_base = (
            f"/v1/orgaos/{purchase_item['cnpj']}/compras/"
            f"{purchase_item['ano']}/{purchase_item['sequencial']}"
        )
        if index == 1 and self.wants_json:
            self.request(
                "detalhe_contratacao",
                "PNCP — detalhe da contratação encontrada",
                source_check.make_api_url(purchase_base),
                "json",
            )
        self.request(
            f"arquivos_edital_{index:03d}",
            f"PNCP — arquivos/editais da contratação {index:03d}",
            source_check.make_api_url(f"{purchase_base}/arquivos"),
            "json",
        )
        if index == 1 and self.wants_json:
            self.request(
                "resultados_itens",
                "PNCP — resultados do item 1",
                source_check.make_api_url(f"{purchase_base}/itens/1/resultados"),
                "json",
            )

    def atas(self) -> None:
        """Atas e os arquivos das atas de amostra."""
        config = self.config
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
            before_request=self.before_request,
        )
        self.results.append(
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
            atas_for_files = ata_samples if "pdf" in self.selected else [ata_samples[0]]
            for index, ata_item in enumerate(atas_for_files, start=1):
                ata_files_url = source_check.make_api_url(
                    f"/v1/orgaos/{ata_item['cnpj']}/compras/{ata_item['ano']}/"
                    f"{ata_item['sequencial']}/atas/{ata_item['seq_ata']}/arquivos"
                )
                self.request(
                    f"arquivos_atas_{index:03d}",
                    f"PNCP — arquivos das atas {index:03d}",
                    ata_files_url,
                    "json",
                )

    def pca(self) -> None:
        """Consulta de PCA e, com um plano de amostra, o consolidado e os itens."""
        config = self.config
        pca_url = source_check.make_consulta_url(
            "/v1/pca",
            {
                "anoPca": str(config.ano),
                "codigoClassificacaoSuperior": config.classificacao_superior,
                "pagina": str(config.pagina),
                "tamanhoPagina": str(config.tamanho_pagina),
            },
        )
        pca = self.request("pca_consulta", "PNCP — consulta de PCA", pca_url, "json")
        pca_sample = (
            source_check.sample_pca(pca.get("json"))
            if source_check._is_available(pca)
            else None
        )
        if pca_sample:
            pca_base = source_check.make_api_url(
                f"/v1/orgaos/{pca_sample['cnpj']}/pca/{pca_sample['ano']}"
            )
            self.request(
                "pca_consolidado",
                "PNCP — PCA consolidado por órgão",
                f"{pca_base}/consolidado",
                "json",
            )
            self.request(
                "pca_itens",
                "PNCP — itens do PCA",
                f"{pca_base}/{pca_sample['sequencial']}/itens/contratacao",
                "json",
            )


def _run_checks_for_types(
    config: Any,
    tipos: tuple[str, ...],
    *,
    before_request: Callable[[], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Consulta somente as fontes compatíveis com os tipos solicitados."""
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    checks = _TypedChecks(config, set(tipos), before_request)
    checks.web_sources()
    if {"pdf", "json", "jsonl"} & checks.selected:
        checks.purchases()
        checks.atas()
    if checks.wants_json:
        checks.pca()
    return checked_at, checks.results


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
    """Versões conhecidas: pastas ``data/raw/vN/`` e inventários ``data/inventory/vN.json``.

    O inventário é versionado no Git e a pasta raw não, então numa máquina que
    só clonou o repositório a versão aparece apenas pelo ``vN.json``.
    """
    values: set[int] = set()
    if RAW_ROOT.exists():
        values.update(
            number for child in RAW_ROOT.iterdir() if child.is_dir() and (number := _version_number(child.name)) is not None
        )
    if INVENTORY_ROOT.exists():
        values.update(
            number
            for child in INVENTORY_ROOT.iterdir()
            if child.is_file() and child.suffix == ".json" and (number := _version_number(child.stem)) is not None
        )
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


@observar_offline(Stage.COLETA)
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
    discovery: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_inputs: dict[str, str] = field(default_factory=dict)


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


def _seed_discovered_contract_selection(
    context: _VersionContext,
    manifest: dict[str, Any],
) -> int:
    base_id = "contratos_estruturado"
    base = next(
        (item for item in manifest.get("bases", []) if item.get("base_id") == base_id),
        None,
    )
    if base is None:
        raise RuntimeError(f"manifesto não declara a base {base_id}")
    base_discovery = context.discovery.get(base_id, {})
    unit_state = base_discovery.get("unidades_orgaos_rj", {})
    if not unit_state.get("verificacao_completa"):
        raise RuntimeError("unidades RJ não foram verificadas; não é seguro selecionar contratos")
    discovery_state = base_discovery.get("contrato_assinado_rj_unidades", {})
    eligible_sources = discovery_state.get("eligible_sources") or {}
    if not isinstance(eligible_sources, dict):
        raise RuntimeError("checkpoint não contém referências de contratos elegíveis")

    scopes = discovery_state.get("config", {}).get("verified_unit_scopes") or []
    verified_scopes = {
        (
            str(scope.get("cnpj") or "").strip(),
            str(scope.get("codigoUnidadeAdministrativa") or "").strip(),
        )
        for scope in scopes
        if isinstance(scope, dict)
    }
    if not verified_scopes:
        raise RuntimeError("checkpoint não contém unidades RJ verificadas para o censo de contratos")

    contracts_by_id: dict[str, dict[str, Any]] = {}
    for candidate_id, source_value in eligible_sources.items():
        if not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        if source_ref.provider.casefold() != "pncp" or source_ref.resource_type.casefold() != "contrato_assinado":
            continue
        contract_id = str(source_ref.key.get("numeroControlePNCP") or candidate_id).strip()
        cnpj = str(source_ref.key.get("cnpj") or "").strip()
        unit_code = str(source_ref.key.get("codigoUnidadeAdministrativa") or "").strip()
        if not contract_id or (cnpj, unit_code) not in verified_scopes:
            continue
        contracts_by_id.setdefault(contract_id, source_ref.to_dict())

    selection = base.get("selecao") or {}
    if str(selection.get("modo") or "").casefold() == "censo":
        # Censo: todos os elegíveis da janela, em ordem estável — sem teto.
        target = len(contracts_by_id)
    else:
        target = max(
            int(base.get("volume_alvo") or 1),
            int(selection.get("limite") or selection.get("limit") or 1),
        )
    if len(contracts_by_id) < target:
        raise RuntimeError(
            f"checkpoint tem {len(contracts_by_id)} contratos elegíveis em unidades RJ verificadas; "
            f"são necessários ao menos {target}"
        )

    selected = [contracts_by_id[contract_id] for contract_id in sorted(contracts_by_id)[:target]]
    _set_selection(context, base_id, selected)
    if len(context.selection[base_id]) < target:
        raise RuntimeError("seleção determinística não atingiu o alvo de contratos únicos")
    _checkpoint_write(context)
    return len(context.selection[base_id])


def _record_observed_units(
    observations: list[Any], cnpjs: set[str], observed_units: dict[str, dict[str, Any]]
) -> None:
    """CNPJs e unidades vistos nas publicações da página."""
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        cnpj = str(observation.get("cnpj") or "").strip()
        unit = observation.get("unidadeOrgao") or {}
        if not cnpj:
            continue
        cnpjs.add(cnpj)
        if not isinstance(unit, dict):
            continue
        unit_code = str(
            unit.get("codigoUnidadeAdministrativa")
            or unit.get("codigoUnidade")
            or unit.get("codigo")
            or ""
        ).strip()
        if unit_code:
            units = observed_units.setdefault(cnpj, {})
            units.setdefault(
                unit_code,
                {
                    "codigoUnidadeAdministrativa": unit_code,
                    "ufSigla": str(unit.get("ufSigla") or "").upper(),
                    **(
                        {"codigoIbge": str(unit.get("codigoIbge") or unit.get("codigoMunicipioIbge"))}
                        if unit.get("codigoIbge") or unit.get("codigoMunicipioIbge")
                        else {}
                    ),
                },
            )


def _record_discovered_candidates(
    candidates: list[Any], state: dict[str, Any], eligible_sources: dict[str, Any], cnpjs: set[str]
) -> None:
    """Compras publicadas e contratos elegíveis da página; o contrato fica com a compra vinculada de menor controle."""
    for candidate in candidates:
        source_ref = candidate.source_ref
        if source_ref.resource_type.casefold() in {"contratacao", "contratação"}:
            state.setdefault("publication_sources", {})[source_ref.stable_id] = source_ref.to_dict()
            cnpj = str(source_ref.key.get("cnpj") or "").strip()
            if cnpj:
                cnpjs.add(cnpj)
            continue
        contract_id = str(source_ref.key.get("numeroControlePNCP") or "").strip()
        identity = contract_id or source_ref.stable_id
        existing_value = eligible_sources.get(identity)
        if existing_value is None:
            eligible_sources[identity] = source_ref.to_dict()
        else:
            try:
                existing_ref = SourceRef.from_dict(existing_value)
            except (TypeError, ValueError, KeyError):
                eligible_sources[identity] = source_ref.to_dict()
            else:
                existing_link = existing_ref.key.get("numeroControlePNCPCompra")
                current_link = source_ref.key.get("numeroControlePNCPCompra")
                if current_link and (
                    not existing_link or str(current_link) < str(existing_link)
                ):
                    eligible_sources[identity] = source_ref.to_dict()
        cnpj = str(source_ref.key.get("cnpj") or "").strip()
        if cnpj:
            cnpjs.add(cnpj)


def _record_pncp_discovery_page(
    context: _VersionContext,
    base_id: str,
    resource_type: str,
    report: dict[str, Any],
) -> None:
    populations = context.discovery.setdefault(base_id, {})
    state = populations.setdefault(
        resource_type,
        {
            "completed_pages": [],
            "completed_windows": [],
            "eligible_sources": {},
            "cnpjs_derivados": [],
            "unidades_observadas_por_cnpj": {},
            "publication_sources": {},
            "total_paginas_por_janela": {},
            "total_registros_nacionais_por_janela": {},
        },
    )
    completed_pages = set(state.get("completed_pages") or [])
    completed_pages.add(str(report["page_key"]))
    state["completed_pages"] = sorted(completed_pages)

    total_pages = report.get("total_paginas")
    if total_pages is not None:
        state.setdefault("total_paginas_por_janela", {})[str(report["window"])] = int(total_pages)
    total_records = report.get("total_registros")
    if total_records is not None:
        state.setdefault("total_registros_nacionais_por_janela", {})[str(report["window"])] = int(total_records)

    eligible_sources = state.setdefault("eligible_sources", {})
    cnpjs = set(state.get("cnpjs_derivados") or [])
    observed_units = state.setdefault("unidades_observadas_por_cnpj", {})
    publication_count = int(state.get("total_publicacoes_observadas") or 0) + len(
        report.get("publication_organizations") or []
    )
    _record_observed_units(report.get("publication_organizations") or [], cnpjs, observed_units)
    _record_discovered_candidates(report.get("candidates") or [], state, eligible_sources, cnpjs)
    state["cnpjs_derivados"] = sorted(cnpjs)
    state["total_publicacoes_observadas"] = publication_count

    if report.get("window_done"):
        completed_windows = set(state.get("completed_windows") or [])
        completed_windows.add(str(report["window"]))
        state["completed_windows"] = sorted(completed_windows)
    state["total_contratos_elegiveis"] = len(eligible_sources)
    _checkpoint_write(context)


def _pncp_discovery_config(base: dict[str, Any], population: dict[str, Any]) -> dict[str, Any]:
    access = base.get("acesso") or {}
    params = {**(access.get("params") or {}), **(population.get("params") or {})}
    resource_type = str(
        population.get("resource_type") or access.get("resource_type") or ""
    ).casefold()
    if resource_type in {"contrato_assinado", "contrato_pncp"} and population.get("censo_nacional"):
        params["dataInicial"] = min(str(params.get("dataInicial") or "2021-01-01"), "2021-01-01")
    return {
        "provider": str(access.get("provider") or ""),
        "resource_type": resource_type,
        "params": params,
        "dataInicial": str(params.get("dataInicial") or ""),
        "dataFinal": str(params.get("dataFinal") or ""),
        "anoAssinatura": str(params.get("anoAssinatura") or ""),
        "tamanhoPagina": int(params.get("tamanhoPagina") or 500),
        "filtro_uf": "RJ",
        "filtro_tipo": "exclui_empenho",
    }


def _discovery_checkpoint_matches_manifest(
    checkpoint: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> bool:
    if not manifest or checkpoint.get("scenario_id") != manifest.get("scenario_id"):
        return False
    if checkpoint.get("state") not in {"staging", "incomplete"}:
        return False
    if checkpoint.get("items"):
        return False

    contract_base = next(
        (
            base
            for base in manifest.get("bases", [])
            if isinstance(base, dict)
            and base.get("base_id") == "contratos_estruturado"
        ),
        None,
    )
    if contract_base is None:
        return False
    access = contract_base.get("acesso") or {}
    population = next(
        (
            item
            for item in access.get("populations", [])
            if isinstance(item, dict)
            and item.get("resource_type") == "contrato_assinado"
            and item.get("censo_nacional")
        ),
        None,
    )
    if population is None:
        return False

    discovery = checkpoint.get("discovery")
    if not isinstance(discovery, dict):
        return False
    contract_discovery = discovery.get("contratos_estruturado")
    if not isinstance(contract_discovery, dict):
        return False
    population_key = "contrato_assinado_rj_unidades"
    population_state = contract_discovery.get(population_key)
    if not isinstance(population_state, dict):
        population_key = "contrato_assinado"
        population_state = contract_discovery.get(population_key)
    if not isinstance(population_state, dict):
        return False
    saved = population_state.get("config")
    expected = _pncp_discovery_config(contract_base, population)
    if population_key == "contrato_assinado_rj_unidades":
        saved_scopes = saved.get("verified_unit_scopes") if isinstance(saved, dict) else None
        unit_state = contract_discovery.get("unidades_orgaos_rj") or {}
        current_scopes = unit_state.get("verified_unit_scopes")
        if not unit_state.get("verificacao_completa") or saved_scopes != current_scopes:
            return False
        saved = {key: value for key, value in saved.items() if key != "verified_unit_scopes"}
    return _discovery_configs_compatible(saved, expected)


def _discovery_configs_compatible(saved: Any, expected: dict[str, Any]) -> bool:
    if saved == expected:
        return True

    # Compatibilidade para checkpoints criados antes de guardar provider e params completos.
    legacy_expected = {
        key: expected[key]
        for key in (
            "dataInicial",
            "dataFinal",
            "anoAssinatura",
            "tamanhoPagina",
            "filtro_uf",
            "filtro_tipo",
        )
    }
    legacy_params = {"dataInicial", "dataFinal", "anoAssinatura", "tamanhoPagina"}
    return (
        saved == legacy_expected
        and expected["provider"] == "pncp"
        and expected["resource_type"] == "contrato_assinado"
        and set(expected["params"]).issubset(legacy_params)
    )


def _isolated_pca_checkpoint_matches_manifest(
    checkpoint: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> bool:
    bases = (manifest or {}).get("bases", [])
    if len(bases) != 1 or bases[0].get("base_id") != "pca_agregado":
        return False

    access = bases[0].get("acesso") or {}
    if access.get("preserve_explicit_sources") is not True or access.get("censo") is not False:
        return False

    expected_refs = access.get("sources")
    selection = (checkpoint.get("selection") or {}).get("pca_agregado")
    items = checkpoint.get("items")
    if not isinstance(expected_refs, list) or not isinstance(selection, list) or not isinstance(items, dict):
        return False

    try:
        expected_ids = {
            SourceRef.from_dict(source).stable_id
            for source in expected_refs
            if isinstance(source, dict)
        }
        selected_ids = {
            SourceRef.from_dict(entry["source_ref"]).stable_id
            for entry in selection
            if isinstance(entry, dict) and isinstance(entry.get("source_ref"), dict)
        }
    except (TypeError, ValueError, KeyError):
        return False

    if len(expected_ids) != len(expected_refs) or expected_ids != selected_ids:
        return False

    for record in items.values():
        if not isinstance(record, dict) or record.get("base_id") != "pca_agregado":
            return False
        source_value = record.get("source_ref")
        if isinstance(source_value, dict):
            try:
                if SourceRef.from_dict(source_value).stable_id not in expected_ids:
                    return False
            except (TypeError, ValueError, KeyError):
                return False
    return True


def _checkpoint_item_errors(
    checkpoint: dict[str, Any],
    items: dict[str, Any],
    manifest_bases: dict[str, dict[str, Any]],
    staging_path: Path,
    errors: list[str],
) -> tuple[dict[str, int], dict[str, set[str]], dict[str, dict[str, str]]]:
    """Confere arquivo, hash e SourceRef de cada item; devolve unidades lógicas, fontes e compras por base."""
    logical_by_base: dict[str, int] = {}
    source_ids_by_base: dict[str, set[str]] = {}
    source_purchase_ids_by_base: dict[str, dict[str, str]] = {}
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
                    source_ref = SourceRef.from_dict(source_value)
                    source_id = source_ref.stable_id
                except (TypeError, ValueError, KeyError) as exc:
                    errors.append(f"item {logical_id}: SourceRef inválida: {exc}")
                else:
                    source_ids_by_base.setdefault(base_id, set()).add(source_id)
                    purchase_id = str(source_ref.key.get("numeroControlePNCPCompra") or "")
                    if purchase_id:
                        source_purchase_ids_by_base.setdefault(base_id, {})[source_id] = purchase_id
    return logical_by_base, source_ids_by_base, source_purchase_ids_by_base


def _selection_errors(
    selection: Any,
    manifest_bases: dict[str, dict[str, Any]],
    source_ids_by_base: dict[str, set[str]],
    source_purchase_ids_by_base: dict[str, dict[str, str]],
    errors: list[str],
) -> None:
    """A seleção congelada: SourceRefs válidas e únicas, dentro do alvo, cobrindo os itens coletados."""
    if selection is not None:
        if not isinstance(selection, dict):
            errors.append("selection do checkpoint não é um objeto")
        else:
            for base_id, entries in selection.items():
                if not isinstance(entries, list):
                    errors.append(f"selection/{base_id}: não é uma lista")
                    continue
                seen: set[str] = set()
                selected_purchase_ids: set[str] = set()
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("source_ref"), dict):
                        errors.append(f"selection/{base_id}: entrada inválida")
                        continue
                    try:
                        source_ref = SourceRef.from_dict(entry["source_ref"])
                        source_id = source_ref.stable_id
                    except (TypeError, ValueError, KeyError) as exc:
                        errors.append(f"selection/{base_id}: SourceRef inválida: {exc}")
                        continue
                    if source_id in seen:
                        errors.append(f"selection/{base_id}: SourceRef duplicada {source_id}")
                    seen.add(source_id)
                    if entry.get("stable_id") not in {None, source_id}:
                        errors.append(f"selection/{base_id}: stable_id divergente")
                    purchase_id = str(source_ref.key.get("numeroControlePNCPCompra") or "")
                    if purchase_id:
                        selected_purchase_ids.add(purchase_id)
                base = manifest_bases.get(base_id, {})
                target = base.get("volume_alvo")
                if target is not None and base.get("volume_regra") != "minimo" and len(entries) > int(target):
                    errors.append(f"selection/{base_id}: excede volume_alvo")
                missing = {
                    source_id
                    for source_id in source_ids_by_base.get(base_id, set()) - seen
                    if source_purchase_ids_by_base.get(base_id, {}).get(source_id) not in selected_purchase_ids
                }
                if missing:
                    errors.append(f"selection/{base_id}: item fora da seleção congelada")


def _discovery_errors(discovery: Any, errors: list[str]) -> None:
    """Estado da descoberta: páginas, fontes elegíveis e publicadas com identidade coerente, unidades RJ."""
    if not isinstance(discovery, dict):
        errors.append("discovery do checkpoint não é um objeto")
    else:
        for base_id, populations in discovery.items():
            if not isinstance(populations, dict):
                errors.append(f"discovery/{base_id}: não é um objeto")
                continue
            for resource_type, state in populations.items():
                if not isinstance(state, dict):
                    errors.append(f"discovery/{base_id}/{resource_type}: estado inválido")
                    continue
                if not isinstance(state.get("completed_pages", []), list):
                    errors.append(f"discovery/{base_id}/{resource_type}: completed_pages inválido")
                eligible = state.get("eligible_sources", {})
                if not isinstance(eligible, dict):
                    errors.append(f"discovery/{base_id}/{resource_type}: eligible_sources inválido")
                    continue
                for stable_id, source_value in eligible.items():
                    try:
                        source_ref = SourceRef.from_dict(source_value)
                    except (TypeError, ValueError, KeyError) as exc:
                        errors.append(f"discovery/{base_id}/{resource_type}: SourceRef inválida: {exc}")
                        continue
                    identity = (
                        str(source_ref.key.get("numeroControlePNCP") or "")
                        if resource_type.casefold().startswith(("contrato_assinado", "contrato_pncp"))
                        else source_ref.stable_id
                    )
                    if stable_id != identity:
                        errors.append(f"discovery/{base_id}/{resource_type}: identidade divergente")
                publication_sources = state.get("publication_sources", {})
                if not isinstance(publication_sources, dict):
                    errors.append(f"discovery/{base_id}/{resource_type}: publication_sources inválido")
                else:
                    for stable_id, source_value in publication_sources.items():
                        try:
                            source_ref = SourceRef.from_dict(source_value)
                        except (TypeError, ValueError, KeyError) as exc:
                            errors.append(
                                f"discovery/{base_id}/{resource_type}: publication SourceRef inválida: {exc}"
                            )
                            continue
                        if stable_id != source_ref.stable_id:
                            errors.append(
                                f"discovery/{base_id}/{resource_type}: publication stable_id divergente"
                            )
                unit_scopes = state.get("verified_unit_scopes", [])
                if not isinstance(unit_scopes, list) or any(
                    not isinstance(scope, dict)
                    or not scope.get("cnpj")
                    or not scope.get("codigoUnidadeAdministrativa")
                    or str(scope.get("ufSigla") or "").upper() != "RJ"
                    for scope in unit_scopes
                ):
                    errors.append(f"discovery/{base_id}/{resource_type}: verified_unit_scopes inválido")


def _validate_checkpoint(
    checkpoint: dict[str, Any],
    manifest: dict[str, Any] | None,
    manifest_hash: str,
    version: int,
    staging_path: Path,
    *,
    allow_discovery_manifest_drift: bool = False,
    allow_isolated_pca_manifest_drift: bool = False,
    expected_external_inputs: dict[str, str] | None = None,
) -> ValidationResult:
    errors: list[str] = []
    if checkpoint.get("manifest_hash") != manifest_hash and not (
        (allow_discovery_manifest_drift and _discovery_checkpoint_matches_manifest(checkpoint, manifest))
        or (
            allow_isolated_pca_manifest_drift
            and _isolated_pca_checkpoint_matches_manifest(checkpoint, manifest)
        )
    ):
        errors.append("manifest_hash incompatível")
    if checkpoint.get("version") not in {None, f"v{version}"}:
        errors.append("versão do checkpoint incompatível")
    if checkpoint.get("state") not in {"staging", "incomplete", "promoted"}:
        errors.append("estado do checkpoint inválido")
    if checkpoint.get("external_inputs", {}) != (expected_external_inputs or {}):
        errors.append("entradas externas incompatíveis")

    items = checkpoint.get("items", {})
    if not isinstance(items, dict):
        errors.append("items do checkpoint não é um objeto")
        return ValidationResult(tuple(errors))

    manifest_bases = {
        str(base.get("base_id")): base
        for base in (manifest or {}).get("bases", [])
        if isinstance(base, dict) and base.get("base_id")
    }
    logical_by_base, source_ids_by_base, source_purchase_ids_by_base = _checkpoint_item_errors(
        checkpoint, items, manifest_bases, staging_path, errors
    )

    _selection_errors(checkpoint.get("selection"), manifest_bases, source_ids_by_base, source_purchase_ids_by_base, errors)

    for base_id, observed in logical_by_base.items():
        base = manifest_bases.get(base_id, {})
        target = base.get("volume_alvo")
        if target is not None and base.get("volume_regra") != "minimo" and observed > int(target):
            errors.append(f"{base_id}: unidades lógicas excedem volume_alvo")

    _discovery_errors(checkpoint.get("discovery", {}), errors)
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
        "discovery": context.discovery,
        "external_inputs": context.external_inputs,
        "items": context.items,
    }
    temporary = context.checkpoint_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(1, 5):
        try:
            temporary.replace(context.checkpoint_path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(retry_delay(attempt, None))


def _load_version_context(
    manifest: dict[str, Any],
    manifest_hash: str,
    version: int,
    *,
    previous_items: dict[str, dict[str, Any]] | None = None,
    previous_version: int | None = None,
    allow_discovery_manifest_drift: bool = False,
    allow_isolated_pca_manifest_drift: bool = False,
    external_inputs: dict[str, str] | None = None,
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
            allow_discovery_manifest_drift=allow_discovery_manifest_drift,
            allow_isolated_pca_manifest_drift=allow_isolated_pca_manifest_drift,
            expected_external_inputs=external_inputs,
        )
        if not validation.ok:
            details = "; ".join(validation.errors)
            raise RuntimeError(f"checkpoint inválido para resume: {details}")
        items = dict(checkpoint.get("items", {}))
        selection = checkpoint.get("selection") or _selection_from_items(items)
        discovery = dict(checkpoint.get("discovery") or {})
    else:
        selection = {}
        discovery = {}
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
        discovery=discovery,
        external_inputs=external_inputs or {},
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
    safe_prefix = _safe_component(filename_prefix)
    max_path_length = 259 if os.name == "nt" else 4096
    available_component_length = max_path_length - len(str(destination_dir)) - 1 - 5
    max_component_length = min(120, max(1, available_component_length))
    prefix_limit = max(1, max_component_length - len(digest[:12]) - len(suffix) - 1)
    safe_prefix = safe_prefix[:prefix_limit].rstrip("._") or "item"
    path = destination_dir / f"{safe_prefix}_{digest[:12]}{suffix}"
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
        for key in ("data", "items", "results", "resultado", "content"):
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


def _rest_pagination(access: dict[str, Any], params: dict[str, Any]) -> tuple[str, int, str, int]:
    """(campo da página, tamanho da página, campo do tamanho, primeira página) declarados no acesso."""
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
    size_field = str(pagination.get("campo_tamanho") or "tamanhoPagina") if page_field else ""
    page = int(params.get(page_field, 1) or 1) if page_field else 1
    return page_field, page_size, size_field, page


def _request_rest_page(
    url: str,
    previous: dict[str, Any] | None,
    fallback_factory: Callable[[], dict[str, Any]] | None,
    fallback_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool, bool, str]:
    """Resposta da página: PNCP ou, quando ele falha ou o disjuntor está aberto, o Compras.gov.br.

    Devolve (resposta, fallback usado, fallback tentado, o que disparou o fallback).
    Duas falhas de transporte seguidas abrem o disjuntor e as próximas páginas vão direto ao fallback.
    """
    fallback_used = False
    fallback_attempted = False
    fallback_trigger = ""
    circuit_open = bool(fallback_state and fallback_state.get("circuit_open"))
    if circuit_open and fallback_factory:
        fallback_attempted = True
        fallback_trigger = "circuit_breaker_open"
        response = fallback_factory()
        fallback_used = (
            response.get("status") in {"disponivel", "nao_modificado"}
            and response.get("json") is not None
        )
    else:
        response = source_check.request_url(
            url,
            "json",
            attempts=1 if fallback_factory else 3,
            timeout=15.0 if fallback_factory else 30.0,
            headers=(
                {}
                if previous and previous.get("acquisition_backend") == "compras_dados_abertos"
                else _conditional_headers(previous)
            ),
        )
        if fallback_factory and response.get("status") in {
            "indisponivel",
            "indisponivel_temporariamente",
        }:
            fallback_attempted = True
            fallback_trigger = str(response.get("status"))
            if fallback_state is not None:
                failures = int(fallback_state.get("consecutive_transport_failures", 0)) + 1
                fallback_state["consecutive_transport_failures"] = failures
                if failures >= 2:
                    fallback_state["circuit_open"] = True
            primary_error = str(response.get("erro") or response.get("status"))
            alternate = fallback_factory()
            if (
                alternate.get("status") in {"disponivel", "nao_modificado"}
                and alternate.get("json") is not None
            ):
                response = alternate
                fallback_used = True
            else:
                alternate_error = str(alternate.get("erro") or alternate.get("status"))
                response["erro"] = f"PNCP: {primary_error}; fallback Compras.gov.br: {alternate_error}"
        elif fallback_state is not None:
            fallback_state["consecutive_transport_failures"] = 0
    return response, fallback_used, fallback_attempted, fallback_trigger


def _unmodified_page_record(
    context: _VersionContext,
    base: dict[str, Any],
    logical_id: str,
    url: str,
    filename_prefix: str,
    previous: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any] | None:
    """Página que o servidor disse não ter mudado (304): o arquivo anterior vira o desta versão."""
    saved = _materialize_previous_record(context, str(base["base_id"]), filename_prefix, previous, ".json")
    if saved is None:
        return None
    saved["registros"] = int(previous.get("registros", 0) or 0)
    for key in ("etag", "last_modified"):
        if response.get(key):
            saved[key] = response[key]
    return _base_record(context, base, logical_id, url, saved, tipo="json", registros=int(previous.get("registros", 0) or 0))


def _rest_page_failure(
    context: _VersionContext,
    base: dict[str, Any],
    logical_id: str,
    url: str,
    reason: str,
    fallback_attempted: bool,
    fallback_trigger: str,
) -> dict[str, Any]:
    """Falha da página; quando o fallback foi tentado, fica registrada no checkpoint com o motivo."""
    failure = _failed_record(context, base, logical_id, url, reason, "json")
    if fallback_attempted:
        failure["fallback_backend"] = "compras_dados_abertos"
        failure["fallback_trigger"] = fallback_trigger
        context.items[logical_id] = failure
        _checkpoint_write(context)
    return failure


def _annotate_saved_page(
    context: _VersionContext,
    saved: dict[str, Any],
    previous: dict[str, Any] | None,
    response: dict[str, Any],
    fallback_used: bool,
    fallback_trigger: str,
) -> None:
    """Reuso da versão anterior, cabeçalhos de cache e, se veio do fallback, a origem alternativa."""
    if previous and previous.get("sha256") == saved.get("sha256"):
        saved["reutilizado_de"] = f"v{context.previous_version}" if context.previous_version else previous.get("path")
        saved["reuso_motivo"] = "SHA-256 idêntico"
    for key in ("etag", "last_modified", "url_final"):
        if response.get(key) and not (
            fallback_used and key in {"etag", "last_modified"}
        ):
            saved[key] = response[key]
    if fallback_used:
        saved["acquisition_backend"] = "compras_dados_abertos"
        saved["fallback_from"] = "pncp"
        saved["fallback_trigger"] = fallback_trigger


def _collect_rest_endpoint(
    context: _VersionContext,
    base: dict[str, Any],
    endpoint_name: str,
    endpoint: str,
    *,
    fallback_factory: Callable[[], dict[str, Any]] | None = None,
    fallback_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    endpoint = _clean_url(endpoint)
    if "{" in endpoint or "}" in endpoint:
        return [_failed_record(context, base, f"{base['base_id']}:{endpoint_name}:template", endpoint, "endpoint possui placeholders e precisa de adapter de descoberta PNCP", "json")]
    selection_limit = _selection_limit(base)
    if selection_limit is not None:
        return _collect_selected_rest_endpoint(context, base, endpoint_name, endpoint, selection_limit)
    access = base.get("acesso", {})
    params = dict(access.get("params") or {})
    page_field, page_size, size_field, page = _rest_pagination(access, params)
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
        if previous and previous.get("origem") != url:
            previous = None
        _wait_rate_limit(context, base)
        try:
            response, fallback_used, fallback_attempted, fallback_trigger = _request_rest_page(
                url, previous, fallback_factory, fallback_state
            )
            unmodified = (
                _unmodified_page_record(context, base, logical_id, url, f"{endpoint_name}_pagina_{page}", previous, response)
                if response.get("status") == "nao_modificado" and previous
                else None
            )
            if unmodified is not None:
                output.append(unmodified)
                if not page_field or int(previous.get("registros", 0) or 0) < page_size:
                    break
                page += 1
                continue
            payload = response.get("json")
            if payload is None:
                if response.get("status") == "disponivel" and response.get("http_status") == 204:
                    payload = []
                else:
                    reason = (
                        response.get("erro")
                        or response.get("observacao")
                        or f"resposta JSON ausente (status={response.get('status')}, HTTP={response.get('http_status')})"
                    )
                    if (
                        page_field
                        and size_field
                        and page_size > 1
                        and "limite local" in str(reason).casefold()
                    ):
                        page_size = max(1, page_size // 2)
                        params[size_field] = page_size
                        continue
                    output.append(_rest_page_failure(context, base, logical_id, url, str(reason), fallback_attempted, fallback_trigger))
                    break
            body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            page_digest = hashlib.sha256(body).hexdigest()
            if page_digest in seen_pages:
                output.append(
                    _failed_record(
                        context,
                        base,
                        f"{base['base_id']}:{endpoint_name}:pagina={page}:duplicada",
                        url,
                        "página repetida durante paginação; enumeração interrompida",
                        "json",
                    )
                )
                break
            seen_pages.add(page_digest)
            saved = _save_version_bytes(context, body, str(base["base_id"]), f"{endpoint_name}_pagina_{page}", ".json")
            _annotate_saved_page(context, saved, previous, response, fallback_used, fallback_trigger)
            records = _payload_records(payload)
            output.append(_base_record(context, base, logical_id, url, saved, tipo="json", registros=len(records)))
            if fallback_used or not page_field or not records or len(records) < page_size:
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


def _provider_populations(access: dict[str, Any], target: int) -> list[dict[str, Any]]:
    """Populações declaradas no acesso; sem elas, uma só, com o recurso e os parâmetros do próprio acesso."""
    return access.get("populations") or [
        {
            "resource_type": access.get("resource_type"),
            "params": access.get("params") or {},
            "censo": bool(access.get("censo")),
            "exigir_cadastro_completo": bool(access.get("exigir_cadastro_completo")),
            "volume_alvo": target,
        }
    ]


def _resumable_sources(
    context: _VersionContext,
    base: dict[str, Any],
    access: dict[str, Any],
    populations: list[dict[str, Any]],
    target: int,
    *,
    discovery_only: bool,
) -> tuple[list[dict[str, Any]], set[str]]:
    """(fontes, ids das retomadas): explícitas, da seleção congelada ou dos itens já coletados."""
    sources = [] if discovery_only else access.get("sources") or []
    resumed_source_ids: set[str] = set()
    resource_type = str(access.get("resource_type") or "").casefold()
    has_signed_contract_population = any(
        str(population.get("resource_type") or "").casefold() == "contrato_assinado"
        and population.get("censo_nacional")
        for population in populations
    )
    preselected_entries = getattr(context, "selection", {}).get(str(base["base_id"]))
    selected_entries = (
        None
        if discovery_only or (has_signed_contract_population and not preselected_entries)
        else preselected_entries
    )
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
    if not sources and not discovery_only:
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
        if len(existing_sources) >= target and not has_signed_contract_population:
            sources = [existing_sources[key] for key in sorted(existing_sources)[:target]]
            resumed_source_ids = {
                SourceRef.from_dict(source_value).stable_id
                for source_value in sources
            }
    return sources, resumed_source_ids


def _population_query(
    context: _VersionContext,
    base: dict[str, Any],
    access: dict[str, Any],
    population: dict[str, Any],
    target: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(consulta ao provider para a população, unidades RJ verificadas que ela usa)."""
    population_target = int(population.get("volume_alvo") or target)
    query = {
        **access,
        **population,
        "resource_type": population.get("resource_type") or access.get("resource_type"),
        "params": {**(access.get("params") or {}), **(population.get("params") or {})},
        "volume_alvo": population_target,
        "selecao": base.get("selecao") or {},
    }
    resource_name = str(query["resource_type"]).casefold()
    if resource_name in {"contrato_assinado", "contrato_pncp"} and population.get("censo_nacional"):
        query["params"]["dataInicial"] = min(
            str(query["params"].get("dataInicial") or "2021-01-01"),
            "2021-01-01",
        )
    verified_unit_scopes: list[dict[str, Any]] = []
    if resource_name in {"contrato_assinado", "contrato_pncp"}:
        base_discovery = context.discovery.get(str(base["base_id"]), {})
        unit_state = base_discovery.get("unidades_orgaos_rj", {})
        if not unit_state.get("verificacao_completa"):
            raise RuntimeError(
                "unidades dos órgãos ainda não foram verificadas; execute discover e retome até concluir"
            )
        verified_unit_scopes = list(unit_state.get("verified_unit_scopes") or [])
        query["verified_unit_scopes"] = verified_unit_scopes
        query["require_verified_unit_scopes"] = True
    return query, verified_unit_scopes


def _attach_discovery_checkpoint(
    context: _VersionContext,
    base: dict[str, Any],
    population: dict[str, Any],
    query: dict[str, Any],
    verified_unit_scopes: list[dict[str, Any]],
    *,
    discovery_only: bool,
    max_pages: int | None,
) -> dict[str, Any]:
    """Liga a consulta ao estado da descoberta no checkpoint, que registra cada página; devolve o estado."""
    resource_name = str(query["resource_type"]).casefold()
    resource_key = str(
        population.get("discovery_key")
        or (
            "contratacao_publicacao_rj"
            if resource_name in {"contratacao", "contratação"}
            else (
                "contrato_assinado_rj_unidades"
                if resource_name in {"contrato_assinado", "contrato_pncp"}
                else resource_name
            )
        )
    )
    discovery_populations = context.discovery.setdefault(str(base["base_id"]), {})
    discovery_state = discovery_populations.setdefault(resource_key, {})
    discovery_config = _pncp_discovery_config(base, population)
    if verified_unit_scopes:
        discovery_config["verified_unit_scopes"] = verified_unit_scopes
    existing_config = discovery_state.get("config")
    if existing_config is not None and not _discovery_configs_compatible(
        existing_config, discovery_config
    ):
        raise RuntimeError(
            f"discovery {base['base_id']}/{resource_key} incompatível com os parâmetros atuais"
        )
    discovery_state["config"] = discovery_config
    discovery_state["total_contratos_elegiveis"] = len(
        discovery_state.get("eligible_sources") or {}
    )
    _checkpoint_write(context)
    query["discovery_state"] = discovery_state
    if discovery_only:
        query["discovery_only"] = True
        query["max_pages"] = max_pages
    if population.get("verify_discovered_units"):
        query["verify_discovered_units"] = True
    query["on_discovery_page"] = lambda report, base_id=str(base["base_id"]), key=resource_key: _record_pncp_discovery_page(
        context, base_id, key, report
    )
    return discovery_state


def _verify_population_units(
    context: _VersionContext,
    base: dict[str, Any],
    access: dict[str, Any],
    provider: Any,
    discovery_state: dict[str, Any],
) -> None:
    """Confere no cadastro oficial as unidades dos órgãos descobertos, gravando cada órgão no checkpoint."""
    verifier = getattr(provider, "verify_discovered_units", None)
    if not callable(verifier):
        raise RuntimeError("provider não oferece verificação oficial das unidades do órgão")
    unit_state = context.discovery.setdefault(str(base["base_id"]), {}).setdefault(
        "unidades_orgaos_rj", {}
    )
    unit_state["cnpjs_derivados"] = list(discovery_state.get("cnpjs_derivados") or [])
    unit_state["unidades_observadas_por_cnpj"] = dict(
        discovery_state.get("unidades_observadas_por_cnpj") or {}
    )
    unit_state.setdefault("verificacoes_orgaos", {})

    def _save_unit_verification(cnpj: str, result: dict[str, Any]) -> None:
        unit_state.setdefault("verificacoes_orgaos", {})[cnpj] = result
        _checkpoint_write(context)

    verifier(
        unit_state,
        on_organization_verified=_save_unit_verification,
        rate_limit_seg=access.get("rate_limit_seg", 0),
    )
    _checkpoint_write(context)


def _discover_provider_sources(
    context: _VersionContext,
    base: dict[str, Any],
    access: dict[str, Any],
    provider: Any,
    populations: list[dict[str, Any]],
    target: int,
    *,
    discovery_only: bool,
    max_pages: int | None,
) -> list[Any] | None:
    """Candidatos de todas as populações; None quando só a descoberta foi pedida (para após a primeira)."""
    candidates: list[Any] = []
    for population in populations:
        _wait_rate_limit(context, base)
        query, verified_unit_scopes = _population_query(context, base, access, population, target)
        checkpoint_discovery = bool(population.get("censo_nacional")) or bool(
            population.get("discovery_checkpoint")
        )
        if checkpoint_discovery:
            discovery_state = _attach_discovery_checkpoint(
                context, base, population, query, verified_unit_scopes,
                discovery_only=discovery_only, max_pages=max_pages,
            )
        candidates.extend(provider.search(query))
        if checkpoint_discovery:
            if query.get("verify_discovered_units"):
                _verify_population_units(context, base, access, provider, discovery_state)
            discovery_state["total_contratos_elegiveis"] = len(discovery_state.get("eligible_sources") or {})
            _checkpoint_write(context)
        if discovery_only:
            return None
    return candidates


def _validate_census_configuration(provider: Any, access: dict[str, Any], populations: list[dict[str, Any]]) -> None:
    """No censo, o provider recusa antes de coletar uma configuração incompleta (ex.: cadastro de CNPJs)."""
    validate_configuration = getattr(provider, "validate_configuration", None)
    if not callable(validate_configuration):
        return
    for population in populations:
        query = {
            **access,
            **population,
            "resource_type": population.get("resource_type") or access.get("resource_type"),
            "params": {**(access.get("params") or {}), **(population.get("params") or {})},
        }
        validate_configuration(query)


def _resumed_records(
    context: _VersionContext,
    base: dict[str, Any],
    sources: list[dict[str, Any]],
    resumed_source_ids: set[str],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """(registros já coletados, se cobrem todas as fontes retomadas; fontes que ainda faltam coletar)."""
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
        return sorted(cached_records, key=lambda item: str(item.get("logical_id") or "")), sources
    return None, [
        source_value
        for source_value in sources
        if SourceRef.from_dict(source_value).stable_id not in cached_source_ids
    ]


def _collect_provider_base(
    context: _VersionContext,
    base: dict[str, Any],
    *,
    discovery_only: bool = False,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    """Materializa referências explícitas usando o mesmo provider da curadoria."""
    access = base.get("acesso") or {}
    provider_name = str(access.get("provider") or "").strip()
    if not provider_name:
        return [_failed_record(context, base, f"{base['base_id']}:provider", "provider", "provider não declarado")]

    provider = get_provider(provider_name)
    for logical_id, item in list(context.items.items()):
        if item.get("base_id") == base.get("base_id") and item.get("status") != "ok":
            context.items.pop(logical_id, None)
    target = int(base.get("volume_alvo") or 1)
    populations = _provider_populations(access, target)
    censo_mode = any(bool(population.get("censo")) for population in populations)
    if censo_mode:
        _validate_census_configuration(provider, access, populations)
    sources, resumed_source_ids = _resumable_sources(
        context, base, access, populations, target, discovery_only=discovery_only
    )
    candidates: list[Any] = []
    discovered = False
    if not sources:
        found = _discover_provider_sources(
            context, base, access, provider, populations, target,
            discovery_only=discovery_only, max_pages=max_pages,
        )
        if found is None:
            return []
        candidates = found
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

    preserve_explicit_sources = bool(access.get("preserve_explicit_sources"))
    if not censo_mode and not preserve_explicit_sources:
        sources = sources[:target]
    if discovered or str(base["base_id"]) not in getattr(context, "selection", {}):
        _set_selection(context, str(base["base_id"]), sources)
        _checkpoint_write(context)

    if resumed_source_ids:
        cached_records, sources = _resumed_records(context, base, sources, resumed_source_ids)
        if cached_records is not None:
            return cached_records

    collector = _ProviderSourceCollector(context, base, access, provider, provider_name, candidates)
    for index, source_value in enumerate(sources, start=1):
        try:
            collector.collect(index, source_value)
        except Exception as exc:
            collector.output.append(
                _failed_record(
                    context,
                    base,
                    f"{base['base_id']}:provider:{index}",
                    provider_name,
                    f"{type(exc).__name__}: {exc}",
                )
            )
    if discovered and not censo_mode and len(sources) < target:
        collector.output.append(
            _failed_record(
                context,
                base,
                f"{base['base_id']}:provider:alvo",
                provider_name,
                f"alvo de {target} unidades não atingido; somente {len(sources)} encontradas",
            )
        )
    return collector.output


def _normalized_ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


# Metadados do documento anexo lidos da listagem do PNCP e os nomes com que o
# documento os expõe.
_DOCUMENT_METADATA_FIELDS = (
    "sequencialDocumento",
    "titulo",
    "tipoDocumentoNome",
    "dataPublicacaoPncp",
    "dataVigenciaInicio",
    "dataVigenciaFim",
)
_DOCUMENT_METADATA_ALIASES = {
    "titulo_documento": "titulo",
    "tipo_documento": "tipoDocumentoNome",
    "data_publicacao": "dataPublicacaoPncp",
    "data_vigencia_inicio": "dataVigenciaInicio",
    "data_vigencia_fim": "dataVigenciaFim",
}


def _document_metadata_by_url(listing_payload: Any, filter_terms: list[str], filter_fields: list[str]) -> dict[str, dict[str, Any]]:
    """Metadados de cada documento da listagem que passa no filtro de termos, pela URL do arquivo."""
    document_metadata_by_url: dict[str, dict[str, Any]] = {}
    for document in source_check.records_from_payload(listing_payload):
        if not isinstance(document, dict):
            continue
        searchable = " ".join(
            str(document.get(field) or "")
            for field in filter_fields
        ).casefold()
        searchable = _normalized_ascii(searchable)
        if filter_terms and not any(term in searchable for term in filter_terms):
            continue
        document_url = next(
            (
                document.get(key)
                for key in ("url", "uri", "location")
                if isinstance(document.get(key), str)
                and document[key].startswith(("http://", "https://"))
            ),
            None,
        )
        if document_url:
            document_metadata = {
                key: document.get(key)
                for key in _DOCUMENT_METADATA_FIELDS
                if document.get(key) not in (None, "")
            }
            document_metadata.update(
                {
                    alias: document_metadata[source_key]
                    for alias, source_key in _DOCUMENT_METADATA_ALIASES.items()
                    if document_metadata.get(source_key) not in (None, "")
                }
            )
            document_metadata_by_url[str(document_url)] = document_metadata
    return document_metadata_by_url


@dataclass
class _ProviderSourceCollector:
    """Coleta os artefatos de cada fonte de uma base de provider, sem repetir URL nem artefato relacionado."""

    context: _VersionContext
    base: dict[str, Any]
    access: dict[str, Any]
    provider: Any
    provider_name: str
    candidates: list[Any]
    output: list[dict[str, Any]] = field(default_factory=list)
    seen_artifact_urls: set[str] = field(default_factory=set)
    seen_download_urls: set[str] = field(default_factory=set)
    seen_related_artifact_ids: set[str] = field(default_factory=set)
    fallback_state: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.fallback_request = getattr(self.provider, "request_agregada_fallback", None)
        if self.base.get("base_id") == "pca_agregado" and self.provider_name.casefold() == "pncp":
            self.fallback_state = {"consecutive_transport_failures": 0, "circuit_open": False}

    def _keep(self, record: dict[str, Any]) -> None:
        self.context.items[record["logical_id"]] = record
        _checkpoint_write(self.context)
        self.output.append(record)

    def _fallback_options(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Fallback do Compras.gov.br para o artefato, quando a base usa e o artefato declara um."""
        fallback = metadata.get("comprasgov_fallback")
        if self.fallback_state is None or not callable(self.fallback_request) or not isinstance(fallback, dict):
            return {}
        fallback_request = self.fallback_request
        return {
            "fallback_factory": lambda fallback=fallback: fallback_request(fallback),
            "fallback_state": self.fallback_state,
        }

    def _related_base(self, resource_kind: str) -> dict[str, Any] | None:
        """Base declarada em ``artefatos_relacionados`` para o tipo de recurso; None se não houver."""
        related_base_id = str(
            (self.access.get("artefatos_relacionados") or {}).get(resource_kind) or ""
        )
        related_base = next(
            (
                candidate_base
                for candidate_base in self.context.manifest.get("bases", [])
                if str(candidate_base.get("base_id") or "") == related_base_id
            ),
            None,
        ) if related_base_id else None
        if related_base_id and related_base is None:
            raise ValueError(
                f"base relacionada {related_base_id!r} não declarada no manifesto"
            )
        return related_base

    def _endpoint_base(self, artifact_base: dict[str, Any], url: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            **artifact_base,
            # A seleção limita a busca de unidades lógicas; no detalhe,
            # cada artefato já representa uma única unidade resolvida.
            "selecao": {},
            "acesso": {
                **self.access,
                "endpoint": url,
                "params": metadata.get("params") or {},
                "paginacao": metadata.get("paginacao") or {},
            },
        }

    def collect(self, index: int, source_value: dict[str, Any]) -> None:
        source_ref = SourceRef.from_dict(
            {**source_value, "provider": source_value.get("provider", self.provider_name)}
        )
        candidate = next(
            (item for item in self.candidates if item.source_ref.stable_id == source_ref.stable_id),
            None,
        )
        if (
            candidate is not None
            and source_ref.resource_type.casefold() in {"contratacao", "contratação"}
            and candidate.metadata
        ):
            self._publication(source_ref, candidate)
            return
        resolved = self.provider.resolve(source_ref)
        for artifact in self.provider.list_artifacts(resolved):
            if artifact.url in self.seen_artifact_urls:
                continue
            self.seen_artifact_urls.add(artifact.url)
            self._artifact(index, source_ref, artifact)

    def _publication(self, source_ref: SourceRef, candidate: Any) -> None:
        """A compra publicada vem pronta na busca: o próprio registro da publicação vira o artefato."""
        payload = {
            key: value for key, value in candidate.metadata.items() if not str(key).startswith("_")
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        saved = _save_version_bytes(
            self.context,
            body,
            str(self.base["base_id"]),
            f"{_safe_component(source_ref.stable_id)}_publication",
            ".json",
        )
        logical_id = f"{self.base['base_id']}:{source_ref.stable_id}:publication"
        record = _base_record(
            self.context,
            self.base,
            logical_id,
            str(candidate.metadata.get("_source_url") or source_ref.stable_id),
            saved,
            tipo="json",
            registros=1,
        )
        record["source_ref"] = source_ref.to_dict()
        record["artifact_id"] = f"{source_ref.stable_id}:publication"
        record["provider"] = self.provider_name
        record["unidades_logicas"] = 0
        record["resource_kind"] = "contratacao_publicada"
        self._keep(record)

    def _artifact(self, index: int, source_ref: SourceRef, artifact: Any) -> None:
        resource_kind = str(artifact.metadata.get("resource_kind") or "")
        allowed_resource_kinds = {
            str(value) for value in self.access.get("resource_kinds", [])
        }
        if allowed_resource_kinds and resource_kind not in allowed_resource_kinds:
            return
        artifact_base = self._related_base(resource_kind) or self.base
        logical_id = f"{artifact_base['base_id']}:{artifact.artifact_id}"
        if artifact.artifact_type.casefold() == "json":
            records = _collect_rest_endpoint(
                self.context,
                self._endpoint_base(artifact_base, artifact.url, artifact.metadata),
                _safe_component(artifact.artifact_id),
                artifact.url,
                **self._fallback_options(artifact.metadata),
            )
        else:
            records = [
                _collect_http_item(
                    self.context,
                    artifact_base,
                    artifact.url,
                    logical_id,
                    Path(artifact.filename).stem if artifact.filename else f"{self.base['base_id']}_{index:03d}",
                    expected_type=artifact.artifact_type,
                )
            ]
        for record in records:
            record["source_ref"] = source_ref.to_dict()
            record["artifact_id"] = artifact.artifact_id
            record["provider"] = self.provider_name
            resource_kind = artifact.metadata.get("resource_kind") or source_ref.resource_type
            logical_unit = bool(artifact.metadata.get("logical_unit", True))
            if self.base.get("base_id") == "pca_agregado" and resource_kind == "contrato":
                # O contrato é contexto/proveniência para as estatísticas; a
                # unidade lógica da base agregada continua sendo o PCA.
                logical_unit = False
            record["unidades_logicas"] = int(logical_unit)
            record["resource_kind"] = resource_kind
            self._keep(record)
        if resource_kind.startswith("documentos_"):
            self._attachments(index, source_ref, artifact, resource_kind, records)
        related_factory = {
            "itens": getattr(self.provider, "related_result_artifacts", None),
            "atas_compra": getattr(self.provider, "related_ata_artifacts", None),
        }.get(resource_kind)
        if callable(related_factory):
            self._related(source_ref, artifact, artifact_base, related_factory, records)

    def _attachments(
        self, index: int, source_ref: SourceRef, artifact: Any, resource_kind: str, records: list[dict[str, Any]]
    ) -> None:
        """Arquivos anunciados nas listagens de documentos, filtrados pelos termos do edital quando houver."""
        document_base = self._related_base(resource_kind)
        for listing_record in records:
            listing_path = _path_from_record(listing_record)
            if listing_path is None or not listing_path.exists():
                continue
            try:
                listing_payload = json.loads(listing_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            document_filter = (
                self.access.get("documentos_edital_filtro")
                if resource_kind == "documentos_edital"
                else None
            ) or {}
            filter_terms = [
                _normalized_ascii(str(term).casefold())
                for term in document_filter.get("termos", [])
                if str(term).strip()
            ]
            filter_fields = document_filter.get("campos") or [
                "titulo",
                "tipoDocumentoNome",
            ]
            document_metadata_by_url = _document_metadata_by_url(listing_payload, filter_terms, filter_fields)
            for file_index, file_url in _file_urls({"status": "disponivel", "json": listing_payload}):
                if filter_terms and file_url not in document_metadata_by_url:
                    continue
                if file_url in self.seen_download_urls:
                    continue
                self.seen_download_urls.add(file_url)
                self.seen_artifact_urls.add(file_url)
                file_id = f"{artifact.artifact_id}:arquivo:{file_index}"
                destination_base = document_base or self.base
                file_record = _collect_http_item(
                    self.context,
                    destination_base,
                    file_url,
                    f"{destination_base['base_id']}:{_safe_component(file_id)}",
                    f"{_safe_component(resource_kind)}_{index:04d}_{file_index:04d}",
                )
                file_record["source_ref"] = source_ref.to_dict()
                file_record["artifact_id"] = file_id
                file_record["provider"] = self.provider_name
                file_record["resource_kind"] = "documento_anexo"
                file_record["document_metadata"] = document_metadata_by_url.get(file_url, {})
                file_record["unidades_logicas"] = 1 if document_base else 0
                self._keep(file_record)

    def _related(
        self,
        source_ref: SourceRef,
        artifact: Any,
        artifact_base: dict[str, Any],
        related_factory: Callable[..., Any],
        records: list[dict[str, Any]],
    ) -> None:
        """Artefatos derivados do conteúdo (resultados dos itens, atas da compra), uma vez cada."""
        for item_record in records:
            item_path = _path_from_record(item_record)
            if item_path is None or not item_path.exists():
                continue
            try:
                item_payload = json.loads(item_path.read_text(encoding="utf-8"))
                related_artifacts = related_factory(source_ref, item_payload)
            except Exception as exc:
                self.output.append(
                    _failed_record(
                        self.context,
                        self.base,
                        f"{self.base['base_id']}:{artifact.artifact_id}:relacionados",
                        artifact.url,
                        f"{type(exc).__name__}: {exc}",
                        "json",
                    )
                )
                continue
            for related in related_artifacts:
                if related.artifact_id in self.seen_related_artifact_ids:
                    continue
                self.seen_related_artifact_ids.add(related.artifact_id)
                self._related_artifact(source_ref, artifact_base, related)

    def _related_artifact(self, source_ref: SourceRef, artifact_base: dict[str, Any], related: Any) -> None:
        related_base = self._endpoint_base(artifact_base, related.url, related.metadata)
        fallback_options = self._fallback_options(related.metadata)
        if related.artifact_type.casefold() in {"pdf", "html", "csv", "binary"}:
            related_records = [
                _collect_http_item(
                    self.context,
                    related_base,
                    _clean_url(related.url),
                    f"{self.base['base_id']}:{_safe_component(related.artifact_id)}",
                    _safe_component(related.filename or related.artifact_id),
                    expected_type=related.artifact_type,
                )
            ]
        else:
            related_records = _collect_rest_endpoint(
                self.context,
                related_base,
                _safe_component(related.artifact_id),
                related.url,
                **fallback_options,
            )
        for related_record in related_records:
            related_source_ref = related.source_ref or source_ref
            related_record["source_ref"] = related_source_ref.to_dict()
            related_record["artifact_id"] = related.artifact_id
            related_record["provider"] = self.provider_name
            related_record["unidades_logicas"] = int(
                bool(related.metadata.get("logical_unit"))
            )
            related_record["resource_kind"] = related.metadata.get("resource_kind")
            if related.metadata.get("titulo") or related.metadata.get("tipo_documento"):
                related_record["document_metadata"] = {
                    "titulo": related.metadata.get("titulo"),
                    "tipo_documento": related.metadata.get("tipo_documento"),
                }
            if related.metadata.get("acquisition_backend"):
                related_record["acquisition_backend"] = related.metadata[
                    "acquisition_backend"
                ]
            self._keep(related_record)


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
        metadata_by_url = access.get("metadados_por_url") or {}
        records = [
            _collect_http_item(
                context,
                base,
                _clean_url(url),
                f"{base['base_id']}:url:{index}",
                f"{base['base_id']}_{index:03d}",
            )
            for index, url in enumerate(urls, start=1)
        ]
        for url, record in zip(urls, records):
            metadata = metadata_by_url.get(str(url)) or metadata_by_url.get(_clean_url(str(url)))
            if isinstance(metadata, dict) and metadata:
                record["document_metadata"] = metadata
        linked_sources = access.get("linked_sources") or []
        if linked_sources:
            provider_access = {
                **access,
                "tipo": "provider",
                "provider": "pncp",
                "sources": linked_sources,
                "resource_kinds": ["documentos_edital"],
            }
            records.extend(
                _collect_provider_base(
                    context,
                    {
                        **base,
                        "volume_alvo": len(linked_sources),
                        "acesso": provider_access,
                    },
                )
            )
        return records
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


def _base_inventory(base: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumo de uma base no inventário: arquivos, itens, formatos, recursos e status frente ao alvo."""
    base_records = [record for record in records if record.get("base_id") == base["base_id"]]
    successful_base = [record for record in base_records if record.get("status") == "ok" and record.get("path")]
    base_failures = [record for record in base_records if record.get("status") != "ok"]
    observed_records = sum(
        int(record.get("unidades_logicas", record.get("registros", 0)) or 0)
        for record in successful_base
    )
    has_record_counts = any(
        "unidades_logicas" in record or "registros" in record
        for record in successful_base
    )
    observed = observed_records if has_record_counts else len(successful_base)
    formatos: dict[str, int] = {}
    por_recurso: dict[str, dict[str, int]] = {}
    for record in successful_base:
        tipo = str(record.get("tipo") or "bin")
        formatos[tipo] = formatos.get(tipo, 0) + 1
        resource_kind = str(
            record.get("resource_kind")
            or (record.get("source_ref") or {}).get("resource_type")
            or "desconhecido"
        )
        counts = por_recurso.setdefault(
            resource_kind,
            {"artefatos": 0, "registros": 0, "unidades_logicas": 0},
        )
        counts["artefatos"] += 1
        counts["registros"] += int(record.get("registros", 0) or 0)
        counts["unidades_logicas"] += int(record.get("unidades_logicas", 0) or 0)
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
    return {
        "base_id": base["base_id"],
        "escopo": base.get("escopo", "experimental"),
        "natureza": base.get("natureza"),
        "status": base_status,
        "arquivos": len(successful_base),
        "itens": observed,
        "formatos": formatos,
        "por_recurso": por_recurso,
        "bytes": sum(int(record.get("bytes", 0) or 0) for record in successful_base),
        "volume_alvo": target,
        "volume_obtido": observed,
        "volume_observado": observed,
        "divergencia_alvo": observed - target_value if target_value is not None else None,
        "failure_count": len(base_failures),
    }


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
    by_base = [_base_inventory(base, records) for base in manifest.get("bases", [])]
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
        "falhas": [
            {
                "origem": record.get("origem"),
                "erro": record.get("erro", "falha sem detalhe"),
                "logical_id": record.get("logical_id"),
            }
            for record in failures
        ],
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


@observar_offline(Stage.INVENTARIO)
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
    *,
    allow_discovery_manifest_drift: bool = False,
    allow_isolated_pca_manifest_drift: bool = False,
    expected_external_inputs: dict[str, str] | None = None,
) -> int | None:
    checkpoint_root = INVENTORY_ROOT / "checkpoints"
    if not checkpoint_root.exists():
        return None
    checkpoints = sorted(
        checkpoint_root.glob("v*.json"),
        key=lambda path: _version_number(path.stem) or -1,
        reverse=True,
    )
    resumable_candidates: list[tuple[int, int]] = []
    for checkpoint_path in checkpoints:
        version = _version_number(checkpoint_path.stem)
        if version is None:
            continue
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        staging_path = RAW_ROOT / ".staging" / f"v{version}"
        manifest_matches = checkpoint.get("manifest_hash") == manifest_hash
        external_inputs_match = checkpoint.get("external_inputs", {}) == (
            expected_external_inputs or {}
        )
        discovery_matches = (
            allow_discovery_manifest_drift
            and _discovery_checkpoint_matches_manifest(checkpoint, manifest)
        )
        isolated_pca_matches = (
            allow_isolated_pca_manifest_drift
            and _isolated_pca_checkpoint_matches_manifest(checkpoint, manifest)
        )
        if (
            (manifest_matches or discovery_matches or isolated_pca_matches)
            and external_inputs_match
            and checkpoint.get("state") in {"staging", "incomplete"}
            and (staging_path.exists() or discovery_matches)
        ):
            if manifest is None:
                return version
            validation = _validate_checkpoint(
                checkpoint,
                manifest,
                manifest_hash,
                version,
                staging_path,
                allow_discovery_manifest_drift=allow_discovery_manifest_drift,
                allow_isolated_pca_manifest_drift=allow_isolated_pca_manifest_drift,
                expected_external_inputs=expected_external_inputs,
            )
            if validation.ok:
                if allow_isolated_pca_manifest_drift:
                    successful_items = sum(
                        1
                        for item in checkpoint.get("items", {}).values()
                        if isinstance(item, dict) and item.get("status") == "ok"
                    )
                    resumable_candidates.append((successful_items, version))
                else:
                    return version
    return max(resumable_candidates)[1] if resumable_candidates else None


def _scoped_manifest(
    manifest: dict[str, Any],
    command: str,
    *,
    official: bool,
    base_ids: list[str] | None,
    use_discovered_contracts: bool,
) -> dict[str, Any]:
    """Manifesto da execução: só bases oficiais, só contratos com a descoberta, ou só as bases pedidas."""
    if official and not base_ids:
        manifest = _manifest_for_scope(manifest, official=True)
    if use_discovered_contracts:
        manifest = {
            **manifest,
            "bases": [
                {
                    **base,
                    "acesso": {
                        **(base.get("acesso") or {}),
                        **(
                            {"resource_kinds": ["contrato"]}
                            if base.get("base_id") == "contratos_estruturado"
                            else {}
                        ),
                    },
                }
                for base in manifest.get("bases", [])
            ],
        }
    if use_discovered_contracts and (command != "update" or not official or base_ids):
        raise ValueError("--use-discovered-contracts requer update --official sem --base-id")
    if base_ids:
        if official:
            raise ValueError("--base-id só pode ser usado em uma execução experimental")
        available = {str(base["base_id"]): base for base in manifest["bases"]}
        unknown = sorted(set(base_ids) - set(available))
        if unknown:
            raise ValueError(f"base(s) não declarada(s) no manifesto: {', '.join(unknown)}")
        manifest = {**manifest, "bases": [available[base_id] for base_id in base_ids]}
    return manifest


# Tipos de SourceRef que identificam a contratação ou o contrato de origem.
_CONTRACT_SOURCE_TYPES = {"contratacao", "contratação", "contrato_assinado", "contrato_pncp"}


def _linked_contract_sources(
    inventory_payload: dict[str, Any], source_base_id: str, *, pncp_only: bool
) -> dict[str, dict[str, Any]]:
    """SourceRefs de contratações e contratos da base de origem no inventário, por stable_id."""
    linked_sources: dict[str, dict[str, Any]] = {}
    for record in inventory_payload.get("arquivos", []):
        if not isinstance(record, dict) or record.get("base_id") != source_base_id:
            continue
        source_value = record.get("source_ref")
        if not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        if pncp_only and source_ref.provider.casefold() != "pncp":
            continue
        if source_ref.resource_type.casefold() not in _CONTRACT_SOURCE_TYPES:
            continue
        linked_sources[source_ref.stable_id] = source_ref.to_dict()
    return linked_sources


def _pca_source_access(
    inventory_payload: dict[str, Any],
    isolated_base: dict[str, Any],
    isolated_access: dict[str, Any],
    source_inventory_path: Path,
) -> dict[str, Any]:
    """pca_agregado isolado: contratos e PCAs explícitos do inventário, sem censo nem cadastro completo."""
    source_types_by_base = {
        "contratos_estruturado": _CONTRACT_SOURCE_TYPES,
        "pca_agregado": {"pca", "pca_agregado"},
    }
    selected_sources: dict[str, dict[str, Any]] = {}
    sources_by_base = {source_base: set() for source_base in source_types_by_base}
    for record in inventory_payload.get("arquivos", []):
        if not isinstance(record, dict):
            continue
        source_base_id = str(record.get("base_id") or "")
        source_value = record.get("source_ref")
        if source_base_id not in source_types_by_base or not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        if source_ref.resource_type.casefold() not in source_types_by_base[source_base_id]:
            continue
        selected_sources[source_ref.stable_id] = source_ref.to_dict()
        sources_by_base[source_base_id].add(source_ref.stable_id)
    if not sources_by_base["contratos_estruturado"]:
        raise ValueError(
            "inventário não contém SourceRef de contratações/contratos em contratos_estruturado"
        )
    pca_target = int(isolated_base.get("volume_alvo") or 1)
    if len(sources_by_base["pca_agregado"]) < pca_target:
        raise ValueError(
            f"inventário contém {len(sources_by_base['pca_agregado'])} SourceRefs PCA; "
            f"são necessários ao menos {pca_target} para atender o alvo de pca_agregado"
        )
    if not selected_sources:
        raise ValueError(f"inventário não contém SourceRef PNCP elegíveis: {source_inventory_path}")
    return {
        **isolated_access,
        "sources": [selected_sources[key] for key in sorted(selected_sources)],
        "preserve_explicit_sources": True,
        "resource_kinds": [
            "pca_planejado",
            "pca_exportacao",
            "contrato",
            "contratacao_detalhe",
            "contratacao_vinculada",
            "itens",
        ],
        # A execução recebe fontes explícitas; não executa o censo PCA nem
        # exige o cadastro completo de CNPJs configurado para o percurso oficial.
        "censo": False,
        "exigir_cadastro_completo": False,
        "populations": [],
    }


def _apply_source_inventory(
    manifest: dict[str, Any],
    command: str,
    source_inventory_path: Path,
    *,
    official: bool,
    base_ids: list[str] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Base isolada alimentada pelas SourceRefs de um inventário anterior; devolve o manifesto e o hash dele."""
    isolated_source_bases = {"editais_normativo", "pca_agregado", "atas_conversacional"}
    if (
        command != "fetch"
        or official
        or base_ids is None
        or len(base_ids) != 1
        or base_ids[0] not in isolated_source_bases
    ):
        raise ValueError(
            "--source-inventory requer fetch experimental limitado a --base-id editais_normativo, pca_agregado ou atas_conversacional"
        )
    inventory_bytes = source_inventory_path.read_bytes()
    inventory_payload = json.loads(inventory_bytes.decode("utf-8"))
    if not isinstance(inventory_payload, dict) or not isinstance(
        inventory_payload.get("arquivos"), list
    ):
        raise ValueError("--source-inventory precisa apontar para um inventário com lista 'arquivos'")
    external_inputs = {
        "source_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest()
    }
    isolated_base = manifest["bases"][0]
    isolated_access = isolated_base.get("acesso") or {}
    if base_ids[0] == "pca_agregado":
        updated_access = _pca_source_access(inventory_payload, isolated_base, isolated_access, source_inventory_path)
    else:
        source_base_id = str(
            isolated_access.get("linked_source_inventory_base") or "contratos_estruturado"
        )
        is_editais = base_ids[0] == "editais_normativo"
        linked_sources = _linked_contract_sources(inventory_payload, source_base_id, pncp_only=not is_editais)
        if not linked_sources:
            raise ValueError(
                f"inventário não contém SourceRef PNCP elegíveis em {source_base_id}: {source_inventory_path}"
            )
        if is_editais:
            updated_access = {**isolated_access, "linked_sources": list(linked_sources.values())}
        else:
            updated_access = {
                **isolated_access,
                "sources": [linked_sources[key] for key in sorted(linked_sources)],
                "preserve_explicit_sources": True,
                "resource_kinds": [
                    "atas_compra",
                    "ata",
                    "documentos_ata",
                    "contratos_vinculados_ata",
                ],
                # Este percurso segue somente as compras explícitas do inventário,
                # sem iniciar uma busca global de atas nem depender do censo PNCP.
                "censo": False,
                "exigir_cadastro_completo": False,
                "populations": [],
            }
    manifest = {
        **manifest,
        "bases": [{**isolated_base, "acesso": updated_access}],
    }
    return manifest, external_inputs


def _load_adopt_attempt(
    adopt_attempt_inventory_path: Path,
    command: str,
    *,
    official: bool,
    base_ids: list[str] | None,
    use_discovered_contracts: bool,
) -> tuple[dict[str, Any], str, str]:
    """(inventário da tentativa, base adotada, SHA-256 do inventário), conferida a cobertura da tentativa."""
    if command != "update" or not official or base_ids or use_discovered_contracts:
        raise ValueError(
            "--adopt-attempt requer update --official sem --base-id e sem --use-discovered-contracts"
        )
    attempt_bytes = adopt_attempt_inventory_path.read_bytes()
    attempt_payload = json.loads(attempt_bytes.decode("utf-8"))
    if (
        not isinstance(attempt_payload, dict)
        or not isinstance(attempt_payload.get("arquivos"), list)
        or attempt_payload.get("official") is not False
        or attempt_payload.get("completo") is not True
    ):
        raise ValueError("--adopt-attempt requer o inventário de uma tentativa experimental concluída")
    attempt_base_ids = {
        str(record.get("base_id") or "")
        for record in attempt_payload["arquivos"]
        if isinstance(record, dict) and record.get("base_id")
    }
    if len(attempt_base_ids) != 1 or not attempt_base_ids.issubset(
        {"pca_agregado", "atas_conversacional"}
    ):
        raise ValueError(
            "a tentativa precisa conter registros de uma única base: "
            "pca_agregado ou atas_conversacional"
        )
    adopt_attempt_base_id = next(iter(attempt_base_ids))
    attempt_records = [
        record
        for record in attempt_payload["arquivos"]
        if isinstance(record, dict) and record.get("base_id") == adopt_attempt_base_id
    ]
    if not any(record.get("status") == "ok" for record in attempt_records):
        raise ValueError("a tentativa não tem artefatos bem-sucedidos para promover")
    _validate_adopt_attempt_coverage(
        adopt_attempt_inventory_path,
        attempt_payload,
        base_id=adopt_attempt_base_id,
    )
    return attempt_payload, adopt_attempt_base_id, hashlib.sha256(attempt_bytes).hexdigest()


def _previous_items_and_version(previous: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], int | None]:
    """(registros da versão anterior por logical_id, número dela) para reaproveitar o que não mudou."""
    previous_items = {
        str(record["logical_id"]): record
        for record in (previous or {}).get("arquivos", [])
        if isinstance(record, dict) and record.get("logical_id")
    }
    previous_version = _version_number(str(previous["version"])) if previous and previous.get("version") else None
    return previous_items, previous_version


def _previous_inventory(command: str, resumable_version: int | None) -> dict[str, Any] | None:
    """Inventário da versão anterior que o update compara e reaproveita; None fora do update."""
    if command != "update":
        return None
    latest = _latest_version()
    if resumable_version is not None and latest == resumable_version:
        versions = [number for number in _available_versions() if number != resumable_version]
        latest = max(versions) if versions else None
    if latest is None:
        raise RuntimeError("update exige uma versão anterior")
    return _inventory_for_version(latest)


@observar_offline(Stage.COLETA)
def _run_manifest_command(
    command: str,
    manifest_path: Path,
    *,
    official: bool,
    base_ids: list[str] | None = None,
    source_inventory_path: Path | None = None,
    adopt_attempt_inventory_path: Path | None = None,
    max_pages: int | None = None,
    use_discovered_contracts: bool = False,
) -> int:
    logger = _RunLogger(command)
    logger.event("manifest_load", "started", manifest=str(manifest_path))
    manifest = load_manifest(manifest_path)
    manifest_hash = _manifest_hash(manifest_path)
    manifest, external_inputs, adopt_attempt_inventory, adopt_attempt_base_id = _prepare_run_inputs(
        manifest, command, official=official, base_ids=base_ids, source_inventory_path=source_inventory_path,
        adopt_attempt_inventory_path=adopt_attempt_inventory_path, use_discovered_contracts=use_discovered_contracts,
    )
    logger.event(
        "manifest_load",
        "completed",
        scenario_id=manifest.get("scenario_id"),
        manifest_hash=manifest_hash,
    )
    if command == "fetch" and not base_ids and _reuse_complete_version(manifest_hash, logger):
        return 0
    allow_discovery_manifest_drift = command == "discover"
    allow_isolated_pca_manifest_drift = (
        command == "fetch"
        and not official
        and base_ids == ["pca_agregado"]
        and source_inventory_path is not None
    )
    resumable_version = _find_resumable_version(
        manifest_hash,
        manifest,
        allow_discovery_manifest_drift=allow_discovery_manifest_drift,
        allow_isolated_pca_manifest_drift=allow_isolated_pca_manifest_drift,
        expected_external_inputs=external_inputs,
    )
    previous = _previous_inventory(command, resumable_version)
    version = resumable_version or _next_version()
    previous_items, previous_version = _previous_items_and_version(previous)
    version_lock = _VersionLock(version)
    version_lock.acquire()
    try:
        context = _load_version_context(
            manifest,
            manifest_hash,
            version,
            previous_items=previous_items,
            previous_version=previous_version,
            allow_discovery_manifest_drift=allow_discovery_manifest_drift,
            allow_isolated_pca_manifest_drift=allow_isolated_pca_manifest_drift,
            external_inputs=external_inputs,
        )
        _seed_carried_selection(
            context, manifest, previous_items, logger,
            use_discovered_contracts=use_discovered_contracts,
            adopt_attempt_inventory=adopt_attempt_inventory,
            adopt_attempt_base_id=adopt_attempt_base_id,
            adopt_attempt_inventory_path=adopt_attempt_inventory_path,
        )
        logger.event(
            "version_resolution",
            "resumed" if resumable_version is not None else "started",
            version=f"v{version}",
        )
        if command == "discover":
            return _run_rj_discovery(context, manifest, version, logger, max_pages=max_pages)
        _collect_manifest_bases(
            context, manifest, logger,
            adopting=adopt_attempt_inventory is not None, use_discovered_contracts=use_discovered_contracts,
        )
        inventory = _run_inventory(manifest, version, manifest_hash, context, official=official)
        validation = validate_inventory_contract(manifest, inventory, official=official)
        if not validation.ok:
            return _report_incomplete_inventory(context, inventory, validation, logger)
        logger.event("validation", "passed", version=inventory["version"])
        _promote_version(context)
    finally:
        version_lock.release()
    logger.event("version_promotion", "completed", version=f"v{version}")
    inventory = _run_inventory(manifest, version, manifest_hash, context, official=official)
    return _report_promoted_inventory(inventory, previous, logger)


def _run_inventory(
    manifest: dict[str, Any], version: int, manifest_hash: str, context: _VersionContext, *, official: bool
) -> dict[str, Any]:
    return _build_inventory(
        manifest,
        version,
        manifest_hash,
        context.collected_at,
        context.items,
        inventory_type="manifest" if official else "experimental",
        official=official,
    )


def _prepare_run_inputs(
    manifest: dict[str, Any],
    command: str,
    *,
    official: bool,
    base_ids: list[str] | None,
    source_inventory_path: Path | None,
    adopt_attempt_inventory_path: Path | None,
    use_discovered_contracts: bool,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any] | None, str | None]:
    """(manifesto da execução, hashes das entradas externas, tentativa a adotar e a base dela)."""
    external_inputs: dict[str, str] = {}
    adopt_attempt_inventory: dict[str, Any] | None = None
    adopt_attempt_base_id: str | None = None
    manifest = _scoped_manifest(
        manifest, command, official=official, base_ids=base_ids, use_discovered_contracts=use_discovered_contracts
    )
    if source_inventory_path is not None:
        manifest, external_inputs = _apply_source_inventory(
            manifest, command, source_inventory_path, official=official, base_ids=base_ids
        )
    if adopt_attempt_inventory_path is not None:
        adopt_attempt_inventory, adopt_attempt_base_id, attempt_sha256 = _load_adopt_attempt(
            adopt_attempt_inventory_path, command, official=official, base_ids=base_ids,
            use_discovered_contracts=use_discovered_contracts,
        )
        external_inputs["adopt_attempt_inventory_sha256"] = attempt_sha256
    return manifest, external_inputs, adopt_attempt_inventory, adopt_attempt_base_id


def _reuse_complete_version(manifest_hash: str, logger: _RunLogger) -> bool:
    """fetch de um manifesto que já tem versão completa: reaproveita a versão em vez de coletar de novo."""
    existing = _find_complete_matching_manifest(manifest_hash)
    if not existing:
        return False
    _render_corpus_inventory(existing)
    logger.event("version_resolution", "reused", version=existing["version"])
    print(
        json.dumps(
            {"version": existing["version"], "reutilizado": True, "run_id": logger.run_id},
            ensure_ascii=False,
        )
    )
    return True


def _report_promoted_inventory(inventory: dict[str, Any], previous: dict[str, Any] | None, logger: _RunLogger) -> int:
    """Inventário e diff da versão promovida; 0 se completa, 2 se parcial."""
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


def _seed_carried_selection(
    context: _VersionContext,
    manifest: dict[str, Any],
    previous_items: dict[str, dict[str, Any]],
    logger: _RunLogger,
    *,
    use_discovered_contracts: bool,
    adopt_attempt_inventory: dict[str, Any] | None,
    adopt_attempt_base_id: str | None,
    adopt_attempt_inventory_path: Path | None,
) -> None:
    """Seleção dos contratos descobertos e/ou adoção da tentativa; as demais bases vêm da versão anterior."""
    if use_discovered_contracts:
        selected_count = _seed_discovered_contract_selection(context, manifest)
        carried_bases = {}
        for base in manifest.get("bases", []):
            base_id = str(base.get("base_id") or "")
            if base_id and base_id != "contratos_estruturado":
                carried_bases[base_id] = _carry_forward_base_records(
                    context,
                    base_id,
                    previous_items,
                )
        _checkpoint_write(context)
    if adopt_attempt_inventory is not None:
        carried_bases = {
            str(base.get("base_id")): _carry_forward_base_records(
                context,
                str(base.get("base_id")),
                previous_items,
            )
            for base in manifest.get("bases", [])
            if base.get("base_id")
        }
        adopted_count = _adopt_attempt_records(
            context,
            adopt_attempt_inventory,
            base_id=adopt_attempt_base_id,
            source_inventory_path=adopt_attempt_inventory_path,
        )
        context.selection = _selection_from_items(context.items)
        _checkpoint_write(context)
        logger.event(
            "attempt_adoption",
            "completed",
            base_id=adopt_attempt_base_id,
            adopted_records=adopted_count,
            carried_forward_bases=carried_bases,
            attempt_inventory=_relative_path(adopt_attempt_inventory_path),
        )
        if use_discovered_contracts:
            logger.event(
                "discovery_selection",
                "completed",
                base_id="contratos_estruturado",
                population="contrato_assinado_rj_unidades",
                selected_contracts=selected_count,
                scan_complete=bool(
                    context.discovery.get("contratos_estruturado", {})
                    .get("contrato_assinado_rj_unidades", {})
                    .get("scan_completo")
                ),
                carried_forward_bases=carried_bases,
            )


def _run_rj_discovery(
    context: _VersionContext,
    manifest: dict[str, Any],
    version: int,
    logger: _RunLogger,
    *,
    max_pages: int | None,
) -> int:
    """discover: publicações do RJ (órgãos e unidades) e, com as unidades verificadas, contratos assinados."""
    contract_base = next(
        (base for base in manifest["bases"] if base.get("base_id") == "contratos_estruturado"),
        None,
    )
    if contract_base is None:
        raise RuntimeError("manifesto não declara a base contratos_estruturado")
    access = contract_base.get("acesso") or {}
    publication_population = next(
        (
            item
            for item in access.get("populations", [])
            if str(item.get("resource_type") or "").casefold() in {"contratacao", "contratação"}
        ),
        None,
    )
    contract_population = next(
        (
            item
            for item in access.get("populations", [])
            if str(item.get("resource_type") or "").casefold() == "contrato_assinado"
        ),
        None,
    )
    if publication_population is None or contract_population is None:
        raise RuntimeError("manifesto precisa declarar contratações publicadas e contratos assinados")

    # A população de publicações encontra órgãos e unidades antes da consulta contratual.
    # O intervalo começa na adoção do PNCP e termina no corte já usado pelo levantamento.
    publication_params = {
        **(access.get("params") or {}),
        **(publication_population.get("params") or {}),
        "dataInicial": "2021-01-01",
        "dataFinal": "2026-09-25",
        "uf": "RJ",
    }
    publication_discovery = {
        **publication_population,
        "params": publication_params,
        "censo": True,
        "rate_limit_seg": max(float(access.get("rate_limit_seg") or 0), 3.0),
        "discovery_checkpoint": True,
        "discovery_key": "contratacao_publicacao_rj",
        "verify_discovered_units": True,
    }
    publication_base = {
        **contract_base,
        "acesso": {**access, "populations": [publication_discovery]},
    }
    started = time.perf_counter()
    _collect_provider_base(
        context,
        publication_base,
        discovery_only=True,
        max_pages=max_pages,
    )
    base_discovery = context.discovery.get("contratos_estruturado", {})
    publication_state = base_discovery.get("contratacao_publicacao_rj", {})
    unit_state = base_discovery.get("unidades_orgaos_rj", {})

    contract_state: dict[str, Any] = {}
    if publication_state.get("scan_completo") and unit_state.get("verificacao_completa"):
        scoped_contract_population = {
            **contract_population,
            "discovery_key": "contrato_assinado_rj_unidades",
        }
        contract_base_for_discovery = {
            **contract_base,
            "acesso": {**access, "populations": [scoped_contract_population]},
        }
        _collect_provider_base(
            context,
            contract_base_for_discovery,
            discovery_only=True,
            max_pages=max_pages,
        )
        contract_state = context.discovery.get("contratos_estruturado", {}).get(
            "contrato_assinado_rj_unidades", {}
        )
    _checkpoint_write(context)
    return _report_rj_discovery(context, version, logger, publication_state, unit_state, contract_state,
                                started=started, max_pages=max_pages)


def _report_rj_discovery(
    context: _VersionContext,
    version: int,
    logger: _RunLogger,
    publication_state: dict[str, Any],
    unit_state: dict[str, Any],
    contract_state: dict[str, Any],
    *,
    started: float,
    max_pages: int | None,
) -> int:
    """Progresso da descoberta no log e na saída: páginas, órgãos, unidades verificadas e contratos elegíveis."""
    logger.event(
        "rj_discovery",
        "completed" if contract_state.get("scan_completo") else "paused",
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        publication_pages_this_run=publication_state.get("pages_this_run", 0),
        publication_completed_pages=len(publication_state.get("completed_pages") or []),
        publication_scan_complete=bool(publication_state.get("scan_completo")),
        organizations=len(publication_state.get("cnpjs_derivados") or []),
        organizations_verified=unit_state.get("organizacoes_verificadas", 0),
        verified_rj_units=len(unit_state.get("verified_unit_scopes") or []),
        contract_pages_this_run=contract_state.get("pages_this_run", 0),
        eligible_contracts=len(contract_state.get("eligible_sources") or {}),
        contract_scan_complete=bool(contract_state.get("scan_completo")),
    )
    summary = {
        "version": f"v{version}",
        "discovery_only": True,
        "publication_period": ["2021-01-01", "2026-09-25"],
        "publication_pages_this_run": publication_state.get("pages_this_run", 0),
        "publication_completed_pages": len(publication_state.get("completed_pages") or []),
        "publication_scan_complete": bool(publication_state.get("scan_completo")),
        "organizations_discovered": len(publication_state.get("cnpjs_derivados") or []),
        "organizations_verified": unit_state.get("organizacoes_verificadas", 0),
        "verified_rj_units": len(unit_state.get("verified_unit_scopes") or []),
        "unit_verification_complete": bool(unit_state.get("verificacao_completa")),
        "contract_pages_this_run": contract_state.get("pages_this_run", 0),
        "eligible_contracts": len(contract_state.get("eligible_sources") or {}),
        "scan_complete": bool(contract_state.get("scan_completo")),
        "pca_registry_complete": False,
        "max_pages": max_pages,
        "promoted": False,
        "run_id": logger.run_id,
        "checkpoint": _relative_path(context.checkpoint_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _collect_manifest_bases(
    context: _VersionContext,
    manifest: dict[str, Any],
    logger: _RunLogger,
    *,
    adopting: bool,
    use_discovered_contracts: bool,
) -> None:
    """Coleta cada base do manifesto; na adoção nenhuma, e com a descoberta só os contratos."""
    for base in manifest["bases"]:
        if adopting:
            continue
        if use_discovered_contracts and base.get("base_id") != "contratos_estruturado":
            continue
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


def _report_incomplete_inventory(
    context: _VersionContext, inventory: dict[str, Any], validation: Any, logger: _RunLogger
) -> int:
    """Inventário que não passou no contrato: fica incompleto, sem promoção, e a execução sai com 2."""
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


@observar_offline(Stage.INVENTARIO)
def _discovery_report(
    checkpoint_version: str,
    base_id: str,
    population: str,
    query: str | None,
    limit: int,
    offset: int,
) -> int:
    version = _version_number(checkpoint_version)
    if version is None:
        raise ValueError("--checkpoint-version deve usar o formato vN")
    checkpoint_path = INVENTORY_ROOT / "checkpoints" / f"v{version}.json"
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"checkpoint de descoberta não encontrado: {checkpoint_path}")

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    discovery = checkpoint.get("discovery") or {}
    base_discovery = discovery.get(base_id)
    if not isinstance(base_discovery, dict):
        raise ValueError(f"base sem descoberta no checkpoint: {base_id}")
    state = base_discovery.get(population)
    if not isinstance(state, dict):
        raise ValueError(f"população sem descoberta no checkpoint: {base_id}/{population}")

    sources = state.get("eligible_sources") or state.get("publication_sources") or {}
    if isinstance(sources, dict):
        entries = [(str(source_id), source_ref) for source_id, source_ref in sources.items()]
    elif isinstance(sources, list):
        entries = [(str(index), source_ref) for index, source_ref in enumerate(sources)]
    else:
        entries = []

    normalized_query = query.casefold().strip() if query else ""
    matches = []
    for source_id, source_ref in sorted(entries, key=lambda item: item[0]):
        searchable = json.dumps(
            {"id": source_id, "source_ref": source_ref},
            ensure_ascii=False,
            sort_keys=True,
        ).casefold()
        if not normalized_query or normalized_query in searchable:
            matches.append({"id": source_id, "source_ref": source_ref})

    report = {
        "checkpoint_version": f"v{version}",
        "checkpoint": _relative_path(checkpoint_path),
        "base_id": base_id,
        "population": population,
        "scan_completo": bool(state.get("scan_completo")),
        "paginas_concluidas": len(state.get("completed_pages") or []),
        "janelas_concluidas": len(state.get("completed_windows") or []),
        "referencias_registradas": len(entries),
        "contratos_elegiveis": state.get("total_contratos_elegiveis", len(entries)),
        "consulta": query,
        "resultados_encontrados": len(matches),
        "offset": offset,
        "limit": limit,
        "results": matches[offset : offset + limit],
    }


def _carry_forward_base_records(
    context: _VersionContext,
    base_id: str,
    previous_records: dict[str, dict[str, Any]],
) -> int:
    """Copy a complete prior base into staging without recollecting its sources."""
    for logical_id, record in list(context.items.items()):
        if record.get("base_id") == base_id and record.get("status") != "ok":
            context.items.pop(logical_id, None)
    copied = 0
    for logical_id, previous in previous_records.items():
        if previous.get("base_id") != base_id or previous.get("status") != "ok":
            continue
        current = context.items.get(logical_id)
        if current and current.get("status") == "ok" and _path_from_record(current):
            continue
        previous_path = _path_from_record(previous)
        if previous_path is None:
            raise RuntimeError(f"registro anterior sem caminho reutilizável: {logical_id}")
        digest = str(previous.get("sha256") or _file_sha256(previous_path))
        materialized = _materialize_previous_record(
            context,
            base_id,
            f"reuso_{digest[:16]}",
            previous,
            previous_path.suffix or ".bin",
        )
        if materialized is None:
            raise RuntimeError(f"arquivo anterior ausente para reutilização: {logical_id}")
        record = {**previous, **materialized}
        record["logical_id"] = logical_id
        record["base_id"] = base_id
        record["manifest_hash"] = context.manifest_hash
        record["source_file"] = materialized["path"]
        record["size_bytes"] = materialized["bytes"]
        context.items[logical_id] = record
        copied += 1
    return copied


def _adopt_attempt_records(
    context: _VersionContext,
    inventory: dict[str, Any],
    *,
    base_id: str,
    source_inventory_path: Path,
) -> int:
    """Copy a completed isolated base attempt into an official version."""
    adopted = 0
    for candidate in inventory.get("arquivos", []):
        if not isinstance(candidate, dict):
            continue
        logical_id = str(candidate.get("logical_id") or "")
        if not logical_id:
            raise ValueError("registro da tentativa sem logical_id")
        current = context.items.get(logical_id)
        if current and current.get("status") == "ok":
            current_path = _path_from_record(current)
            if current_path is not None and current_path.is_file():
                continue
        record = dict(candidate)
        record["manifest_hash"] = context.manifest_hash
        record["adotado_de_tentativa"] = _relative_path(source_inventory_path)
        if record.get("status") == "ok":
            source_path = _path_from_record(candidate)
            if source_path is None:
                raise RuntimeError(f"registro da tentativa sem arquivo: {logical_id}")
            materialized = _materialize_previous_record(
                context,
                base_id,
                f"adotado_{hashlib.sha256(logical_id.encode('utf-8')).hexdigest()[:16]}",
                candidate,
                source_path.suffix or ".bin",
            )
            if materialized is None:
                raise RuntimeError(f"arquivo da tentativa ausente: {logical_id}")
            record.update(materialized)
            record.pop("reutilizado_de", None)
            record["manifest_hash"] = context.manifest_hash
            record["adotado_de_tentativa"] = _relative_path(source_inventory_path)
        context.items[logical_id] = record
        adopted += 1
    return adopted


def _validate_adopt_attempt_coverage(
    inventory_path: Path,
    inventory: dict[str, Any],
    *,
    base_id: str,
) -> None:
    version = _version_number(str(inventory.get("version") or ""))
    if version is None:
        raise ValueError("inventário da tentativa sem versão vN válida")
    checkpoint_path = inventory_path.parent / "checkpoints" / f"v{version}.json"
    if not checkpoint_path.is_file():
        raise ValueError(f"checkpoint da tentativa não encontrado: {checkpoint_path}")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("state") != "promoted":
        raise ValueError("a tentativa ainda não terminou e não pode ser adotada")

    selected = (checkpoint.get("selection") or {}).get(base_id, [])
    if base_id == "atas_conversacional":
        _validate_atas_attempt(inventory, selected)
        return
    if base_id != "pca_agregado":
        raise ValueError(f"adoção de tentativa não suportada para {base_id}")
    _validate_pca_attempt(inventory, selected)


def _validate_atas_attempt(inventory: dict[str, Any], selected: list[Any]) -> None:
    """Cada compra selecionada com consulta de atas, cada ata com a listagem e todos os PDFs listados baixados."""
    base_id = "atas_conversacional"
    expected_purchases: set[str] = set()
    for entry in selected:
        source_value = entry.get("source_ref") if isinstance(entry, dict) else None
        if not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        control = str(source_ref.key.get("numeroControlePNCPCompra") or "")
        if source_ref.provider.casefold() == "pncp" and control:
            expected_purchases.add(control)
    if not expected_purchases:
        raise ValueError(
            "checkpoint da tentativa não lista compras PNCP selecionadas em atas_conversacional"
        )

    records = [
        record
        for record in inventory.get("arquivos", [])
        if isinstance(record, dict)
        and record.get("base_id") == base_id
        and record.get("status") == "ok"
    ]
    successful_purchase_queries = {
        str(
            (SourceRef.from_dict(record["source_ref"]).key).get(
                "numeroControlePNCPCompra"
            )
            or ""
        )
        for record in records
        if record.get("resource_kind") == "atas_compra"
        and isinstance(record.get("source_ref"), dict)
    }
    missing_purchases = expected_purchases - successful_purchase_queries
    if missing_purchases:
        raise ValueError(
            f"tentativa incompleta: {len(missing_purchases)} de "
            f"{len(expected_purchases)} compras selecionadas sem consulta de atas bem-sucedida"
        )

    ata_sources = {
        SourceRef.from_dict(record["source_ref"]).stable_id
        for record in records
        if record.get("resource_kind") == "ata"
        and isinstance(record.get("source_ref"), dict)
    }
    listed_document_sources = {
        SourceRef.from_dict(record["source_ref"]).stable_id
        for record in records
        if record.get("resource_kind") == "documentos_ata"
        and isinstance(record.get("source_ref"), dict)
    }
    if not ata_sources or ata_sources != listed_document_sources:
        raise ValueError(
            "tentativa incompleta: metadados das atas e listagens de documentos não correspondem"
        )

    listed_documents = 0
    for record in records:
        if record.get("resource_kind") != "documentos_ata":
            continue
        source_path = _path_from_record(record)
        if source_path is None or not source_path.is_file():
            raise ValueError("listagem de documentos da tentativa sem arquivo materializado")
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("listagem de documentos da ata não contém uma lista")
        listed_documents += len(payload)

    downloaded_pdfs = sum(
        1
        for record in records
        if (path := _path_from_record(record)) is not None
        and path.suffix.casefold() == ".pdf"
        and path.is_file()
    )
    if listed_documents != downloaded_pdfs:
        raise ValueError(
            f"tentativa incompleta: {listed_documents} documentos listados e "
            f"{downloaded_pdfs} PDFs baixados"
        )


def _validate_pca_attempt(inventory: dict[str, Any], selected: list[Any]) -> None:
    """Cada contrato selecionado com o recurso contrato, e cada compra vinculada com itens e resultados."""
    expected_contracts: dict[str, str] = {}
    for entry in selected:
        source_value = entry.get("source_ref") if isinstance(entry, dict) else None
        if not isinstance(source_value, dict):
            continue
        source_ref = SourceRef.from_dict(source_value)
        if source_ref.resource_type.casefold() == "contrato_assinado":
            expected_contracts[source_ref.stable_id] = str(
                source_ref.key.get("numeroControlePNCPCompra") or ""
            )
    if not expected_contracts:
        raise ValueError("checkpoint da tentativa não lista contratos selecionados na base pca_agregado")

    expected_purchases = {control for control in expected_contracts.values() if control}
    for resource_kind in ("contrato", "itens", "resultado_item"):
        expected = set(expected_contracts) if resource_kind == "contrato" else expected_purchases
        available: set[str] = set()
        for record in inventory.get("arquivos", []):
            if (
                not isinstance(record, dict)
                or record.get("base_id") != "pca_agregado"
                or record.get("resource_kind") != resource_kind
                or record.get("status") != "ok"
            ):
                continue
            source_value = record.get("source_ref")
            if not isinstance(source_value, dict):
                continue
            source_ref = SourceRef.from_dict(source_value)
            identity = (
                source_ref.stable_id
                if resource_kind == "contrato"
                else str(source_ref.key.get("numeroControlePNCPCompra") or "")
            )
            if identity:
                available.add(identity)

        missing = expected - available
        if missing:
            unit = "contratos" if resource_kind == "contrato" else "compras vinculadas"
            raise ValueError(
                f"tentativa incompleta: {len(missing)} de {len(expected)} {unit} "
                f"não têm recurso {resource_kind} bem-sucedido"
            )


def _manifest_parser() -> argparse.ArgumentParser:
    """Comandos fetch, update, discover, discovery e inventory, com as opções de cada um."""
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
        if command == "fetch":
            command_parser.add_argument(
                "--source-inventory",
                type=Path,
                help="inventário anterior com SourceRef PNCP; usado com --base-id editais_normativo, pca_agregado ou atas_conversacional",
            )
        if command == "update":
            command_parser.add_argument("--dry-run", action="store_true", help="mostra o plano sem modificar arquivos")
            command_parser.add_argument(
                "--adopt-attempt",
                type=Path,
                help="promove uma tentativa experimental isolada de pca_agregado ou atas_conversacional, carregando as demais bases da versão anterior",
            )
            command_parser.add_argument(
                "--use-discovered-contracts",
                action="store_true",
                help="usa até o alvo de contratos elegíveis já registrado no checkpoint v3",
            )
    discovery_parser = subparsers.add_parser(
        "discover",
        help="retoma a descoberta nacional de contratos sem baixar recursos nem promover versão",
        description="Descobre e conta contratos assinados no RJ, registrando o progresso no checkpoint existente sem materializar recursos ou promover uma versão.",
    )
    discovery_parser.add_argument("--manifest", type=Path, default=MANIFEST_DEFAULT)
    discovery_parser.add_argument("--data-root", type=Path, default=None)
    discovery_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
    discovery_parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="limita as páginas consultadas nesta execução; sem limite, conclui toda a descoberta",
    )
    report_parser = subparsers.add_parser(
        "discovery",
        help="consulta referências já registradas no checkpoint, sem acessar a API",
    )
    report_parser.add_argument("--checkpoint-version", default="v3", help="checkpoint a consultar, como v3")
    report_parser.add_argument("--data-root", type=Path, default=None)
    report_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
    report_parser.add_argument("--base-id", default="contratos_estruturado")
    report_parser.add_argument("--population", default="contrato_assinado_rj_unidades")
    report_parser.add_argument("--query", help="busca parcial sem distinção de maiúsculas em IDs e campos da referência")
    report_parser.add_argument("--limit", type=int, default=50, help="máximo de referências retornadas (1–500)")
    report_parser.add_argument("--offset", type=int, default=0, help="número de resultados a pular")
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--corpus-version", required=True, type=str, help="versão do corpus a inspecionar, como v1")
    inventory_parser.add_argument("--data-root", type=Path, default=None)
    inventory_parser.add_argument("--official", action="store_true", help="usa a árvore oficial data/")
    return parser


def _manifest_main(argv: list[str]) -> int:
    parser = _manifest_parser()
    args = parser.parse_args(argv)
    if args.command == "discover" and args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages deve ser maior que zero")
    if args.command == "discovery" and not 1 <= args.limit <= 500:
        parser.error("--limit deve estar entre 1 e 500")
    if args.command == "discovery" and args.offset < 0:
        parser.error("--offset não pode ser negativo")
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
    if args.command == "discovery":
        return _discovery_report(
            args.checkpoint_version,
            args.base_id,
            args.population,
            args.query,
            args.limit,
            args.offset,
        )
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
        if args.use_discovered_contracts:
            parser.error("--use-discovered-contracts não é compatível com update --dry-run")
        if args.adopt_attempt:
            parser.error("--adopt-attempt não é compatível com update --dry-run")
        return _dry_run_update(args.manifest.resolve(), official=args.official)
    run_kwargs = {"official": args.official}
    if getattr(args, "base_ids", None):
        run_kwargs["base_ids"] = args.base_ids
    if getattr(args, "source_inventory", None):
        run_kwargs["source_inventory_path"] = args.source_inventory.resolve()
    if args.command == "discover":
        run_kwargs["max_pages"] = args.max_pages
    if args.command == "update" and args.use_discovered_contracts:
        run_kwargs["use_discovered_contracts"] = True
    if args.command == "update" and args.adopt_attempt:
        run_kwargs["adopt_attempt_inventory_path"] = args.adopt_attempt.resolve()
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
