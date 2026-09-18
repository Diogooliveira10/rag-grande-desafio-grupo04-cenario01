r"""Coleta uma quantidade exata de arquivos novos e únicos para o corpus.

Uso:
    py .\src\fetch_corpus.py --quantidade 2 --tipos html,pdf,json,jsonl
    py .\src\fetch_corpus.py --quantidade 2 --tipos pdf --salvar-json
    py .\src\fetch_corpus.py --quantidade 3 --tipo pdf

O script reutiliza o verificador em ``testes/test-fontes`` como módulo. A
deduplicação é feita pelo downloader compartilhado: somente o status
``baixado`` conta para a quantidade solicitada; ``reutilizado`` não conta.
``--quantidade`` vale para cada tipo selecionado. Cada artefato é baixado e
salvo imediatamente, para preservar o progresso se uma chamada posterior
falhar.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode, urlsplit, urlunsplit


FILE_TYPES = ("html", "pdf", "json", "jsonl")
HTML_SUFFIXES = {".html", ".htm", ".aspx"}


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_FONTES_DIR = PROJECT_ROOT / "testes" / "test-fontes"
if str(TEST_FONTES_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_FONTES_DIR))

source_check = importlib.import_module("testar_fontes_cenario01")  # noqa: E402


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


def _find_result(results: list[dict[str, Any]], result_id: str) -> dict[str, Any] | None:
    return next((result for result in results if result.get("id") == result_id), None)


def _first_file_url(result: dict[str, Any] | None) -> str | None:
    if not result or not _available(result):
        return None
    for record in source_check.records_from_payload(result.get("json")):
        for key in ("url", "uri", "location"):
            value = record.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    return None


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


def _file_type_from_result(result: dict[str, Any], fallback: str) -> str:
    suffix = Path(str(result.get("arquivo") or "")).suffix.lower()
    if suffix in HTML_SUFFIXES:
        return "html"
    if suffix in {".pdf", ".json", ".jsonl"}:
        return suffix[1:]
    return fallback


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
        result = _find_result(results, source_id)
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


def _config_from_args(args: argparse.Namespace) -> Any:
    return source_check.CheckConfig(
        ano=args.ano,
        uf=args.uf.strip().upper(),
        modalidade=args.modalidade,
        tamanho_pagina=args.tamanho_pagina,
        timeout=args.timeout,
        attempts=args.tentativas,
        classificacao_superior=args.classificacao_superior,
        data_inicial=args.data_inicial,
        data_final=args.data_final,
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
                "pagina": "1",
                "tamanhoPagina": str(config.tamanho_pagina),
            },
            source_check.sample_purchase,
            config,
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
                    "pagina": "1",
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
                "pagina": "1",
                "tamanhoPagina": str(config.tamanho_pagina),
            },
            source_check.sample_ata,
            config,
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
                "pagina": "1",
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

def _save_jsonl_candidate(candidate: FetchCandidate) -> dict[str, Any]:
    records = candidate.payload if isinstance(candidate.payload, list) else []
    content = b"".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        for record in records
    )
    result = source_check.ARTIFACT_DOWNLOADER.save_bytes(
        content,
        source_check.RAW_DIR / "arquivos" / candidate.folder,
        candidate.filename_prefix,
        ".jsonl",
    )
    return {
        "http_status": None,
        "tentativas": 0,
        "content_type": "application/x-ndjson",
        "url_final": candidate.url,
        **result,
    }


def fetch_exact(
    results: list[dict[str, Any]],
    quantidade: int,
    config: Any,
    tipos: tuple[str, ...],
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """Baixa e salva imediatamente até atingir a quantidade por tipo."""
    downloads: list[dict[str, Any]] = []
    novos_por_tipo = {file_type: 0 for file_type in tipos}
    candidatos_considerados = 0

    for candidate in iter_candidates(results):
        if all(novos_por_tipo[file_type] >= quantidade for file_type in tipos):
            break
        if candidate.file_type not in novos_por_tipo:
            continue
        if novos_por_tipo[candidate.file_type] >= quantidade:
            continue

        candidatos_considerados += 1
        try:
            if candidate.payload is not None:
                result = _save_jsonl_candidate(candidate)
            else:
                result = source_check.download_url(
                    candidate.url,
                    source_check.RAW_DIR / "arquivos" / candidate.folder,
                    candidate.filename_prefix,
                    attempts=config.attempts,
                    timeout=config.timeout,
                )
        except Exception as exc:
            result = {
                "status": "erro",
                "erro": f"{type(exc).__name__}: {exc}",
                "http_status": None,
                "tentativas": 0,
                "url_final": candidate.url,
            }

        actual_type = _file_type_from_result(result, candidate.file_type)
        record = {
            "id": f"fetch_{candidate.candidate_id}",
            "nome": candidate.nome,
            "url": candidate.url,
            "tipo_solicitado": candidate.file_type,
            "tipo": actual_type,
            "destino": f"data/raw/v1/arquivos/{candidate.folder}",
            **result,
        }
        downloads.append(record)

        if result.get("status") == "baixado" and actual_type in novos_por_tipo:
            novos_por_tipo[actual_type] += 1

    return downloads, novos_por_tipo, candidatos_considerados


def save_report(checked_at: str, payload: dict[str, Any]) -> Path:
    output_dir = source_check.run_output_dir(checked_at)
    output_path = output_dir / f"fetch_corpus_{source_check.run_timestamp(checked_at)}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


MANIFEST_DEFAULT = PROJECT_ROOT / "corpus_manifest.yaml"
RAW_ROOT = PROJECT_ROOT / "data" / "raw"
INVENTORY_ROOT = PROJECT_ROOT / "data" / "inventory"
CORPUS_INVENTORY_PATH = PROJECT_ROOT / "CORPUS_INVENTORY.md"


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
        seen.add(base["base_id"])
    return manifest


def _clean_url(value: str) -> str:
    value = str(value).strip()
    return value[1:-1] if value.startswith("<") and value.endswith(">") else value


def _safe_component(value: str, fallback: str = "item") -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return normalized or fallback


def _manifest_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _relative_path(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


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
    last_request_at: dict[str, float]


def _checkpoint_write(context: _VersionContext, state: str = "staging") -> None:
    context.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scenario_id": context.manifest["scenario_id"],
        "version": f"v{context.version}",
        "manifest_hash": context.manifest_hash,
        "coletado_em": context.collected_at,
        "state": state,
        "items": context.items,
    }
    temporary = context.checkpoint_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(context.checkpoint_path)


def _load_version_context(manifest: dict[str, Any], manifest_hash: str, version: int) -> _VersionContext:
    staging_path = RAW_ROOT / ".staging" / f"v{version}"
    checkpoint_path = INVENTORY_ROOT / "checkpoints" / f"v{version}.json"
    items: dict[str, dict[str, Any]] = {}
    if staging_path.exists() and not checkpoint_path.exists():
        raise RuntimeError(f"staging sem checkpoint; recuse sobrescrever: {staging_path}")
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("manifest_hash") != manifest_hash:
            raise RuntimeError(f"checkpoint de v{version} pertence a outro manifesto")
        items = dict(checkpoint.get("items", {}))
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


def _save_version_bytes(context: _VersionContext, body: bytes, base_id: str, filename_prefix: str, extension: str) -> dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    if digest in context.hash_index:
        return {"status": "ok", "bytes": len(body), "sha256": digest, "path": context.hash_index[digest], "observacao": "conteúdo reutilizado na versão"}
    destination = context.staging_path / _safe_component(base_id)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = extension if extension.startswith(".") else f".{extension}"
    path = destination / f"{_safe_component(filename_prefix)}_{digest[:12]}{suffix}"
    path.write_bytes(body)
    relative = _relative_path(path)
    context.hash_index[digest] = relative
    return {"status": "ok", "bytes": len(body), "sha256": digest, "path": relative}


def _base_record(context: _VersionContext, base: dict[str, Any], logical_id: str, origin: str, result: dict[str, Any], *, tipo: str, registros: int = 0) -> dict[str, Any]:
    record = {"logical_id": logical_id, "base_id": base["base_id"], "tipo": tipo, "origem": origin, "coletado_em": context.collected_at, "registros": registros, **result}
    context.items[logical_id] = record
    _checkpoint_write(context)
    return record


def _completed_item(context: _VersionContext, logical_id: str) -> dict[str, Any] | None:
    record = context.items.get(logical_id)
    return record if record and record.get("status") == "ok" and _path_from_record(record) else None


def _failed_record(context: _VersionContext, base: dict[str, Any], logical_id: str, origin: str, error: str, tipo: str = "desconhecido") -> dict[str, Any]:
    return _base_record(context, base, logical_id, origin, {"status": "falhou", "bytes": 0, "sha256": None, "path": None, "erro": error}, tipo=tipo)


def _extension_for(content_type: str, url: str, default: str = ".bin") -> str:
    content_type = content_type.lower().split(";", 1)[0]
    by_type = {"application/json": ".json", "application/pdf": ".pdf", "text/html": ".html", "application/xhtml+xml": ".html", "application/x-ndjson": ".jsonl", "application/jsonl": ".jsonl"}
    if content_type in by_type:
        return by_type[content_type]
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".json", ".jsonl", ".pdf", ".html", ".htm"} else default


def _collect_http_item(context: _VersionContext, base: dict[str, Any], url: str, logical_id: str, filename_prefix: str) -> dict[str, Any]:
    existing = _completed_item(context, logical_id)
    if existing:
        return existing
    _wait_rate_limit(context, base)
    try:
        result = source_check.download_url(url, context.staging_path / _safe_component(base["base_id"]), filename_prefix, attempts=3, timeout=30.0)
        if result.get("status") not in {"baixado", "reutilizado"}:
            return _failed_record(context, base, logical_id, url, str(result.get("erro") or result.get("observacao") or result.get("status")), _extension_for(str(result.get("content_type", "")), url).lstrip("."))
        source_path = PROJECT_ROOT / str(result.get("arquivo", ""))
        if not source_path.exists():
            return _failed_record(context, base, logical_id, url, "download sem arquivo resultante")
        relative_source = _relative_path(source_path)
        if relative_source.startswith(f"data/raw/.staging/v{context.version}/"):
            saved = {"status": "ok", "bytes": source_path.stat().st_size, "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(), "path": relative_source}
            context.hash_index.setdefault(saved["sha256"], relative_source)
        else:
            saved = _save_version_bytes(context, source_path.read_bytes(), str(base["base_id"]), filename_prefix, source_path.suffix or _extension_for(str(result.get("content_type", "")), url))
        return _base_record(context, base, logical_id, url, saved, tipo=Path(str(saved["path"])).suffix.lstrip(".") or "bin")
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
    query.update({str(key): value for key, value in params.items() if value is not None})
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _payload_records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "content"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _collect_rest_endpoint(context: _VersionContext, base: dict[str, Any], endpoint_name: str, endpoint: str) -> list[dict[str, Any]]:
    endpoint = _clean_url(endpoint)
    if "{" in endpoint or "}" in endpoint:
        return [_failed_record(context, base, f"{base['base_id']}:{endpoint_name}:template", endpoint, "endpoint possui placeholders e precisa de adapter de descoberta PNCP", "json")]
    access = base.get("acesso", {})
    params = dict(access.get("params") or {})
    pagination = access.get("paginacao") or {}
    page_field = str(pagination.get("campo_pagina", "pagina")) if pagination else ""
    size_field = pagination.get("campo_tamanho") if pagination else None
    page_size = int(access.get("tamanho_pagina") or params.get("tamanhoPagina", 50) or 50)
    page = int(params.get(page_field, 1) or 1) if page_field else 1
    output: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    while True:
        request_params = dict(params)
        if page_field:
            request_params[page_field] = page
        if size_field:
            request_params[str(size_field)] = page_size
        url = _build_request_url(endpoint, request_params)
        logical_id = f"{base['base_id']}:{endpoint_name}:pagina={page}"
        existing = _completed_item(context, logical_id)
        if existing:
            output.append(existing)
            if not page_field or int(existing.get("registros", 0) or 0) < page_size:
                break
            page += 1
            continue
        _wait_rate_limit(context, base)
        try:
            response = source_check.request_url(url, "json", attempts=3, timeout=30.0)
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
                extension, registros = path.suffix or ".bin", 0
            saved = _save_version_bytes(context, body, str(base["base_id"]), path.stem, extension)
            output.append(_base_record(context, base, logical_id, _relative_path(path), saved, tipo=extension.lstrip("."), registros=registros))
        except Exception as exc:
            output.append(_failed_record(context, base, logical_id, _relative_path(path), f"{type(exc).__name__}: {exc}"))
    return output


def _collect_pncp_documents(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapter de transição: reaproveita a descoberta PNCP sem usar o fluxo legado de gravação."""
    params = (base.get("acesso") or {}).get("params") or {}
    try:
        year = int(params.get("ano", 2025))
        page_size = int(params.get("tamanhoPagina", 10))
    except (TypeError, ValueError):
        year, page_size = 2025, 10
    config = source_check.CheckConfig(
        ano=year,
        uf=str(params.get("uf", "RJ")).upper(),
        modalidade=params.get("codigoModalidadeContratacao"),
        tamanho_pagina=page_size,
        timeout=30.0,
        attempts=3,
        data_inicial=params.get("dataInicial"),
        data_final=params.get("dataFinal"),
    )
    _, results = _run_checks_for_types(config, ("pdf",))
    candidates = [candidate for candidate in iter_candidates(results) if candidate.folder == "pncp"]
    output = [
        _collect_http_item(
            context,
            base,
            candidate.url,
            f"{base['base_id']}:{candidate.candidate_id}",
            candidate.filename_prefix,
        )
        for candidate in candidates
    ]
    return output or [_failed_record(context, base, f"{base['base_id']}:discovery", "PNCP", "nenhum arquivo PNCP foi descoberto")]


def _collect_base(context: _VersionContext, base: dict[str, Any]) -> list[dict[str, Any]]:
    access = base.get("acesso", {})
    kind = str(access.get("tipo", "")).lower()
    if kind in {"http_pdf", "http_misto"}:
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
    if kind in {"arquivo_local", "processamento_local"}:
        return _collect_local_base(context, base)
    return [_failed_record(context, base, f"{base['base_id']}:adapter", kind, f"tipo de acesso não suportado: {kind}")]


def _build_inventory(manifest: dict[str, Any], version: int, manifest_hash: str, collected_at: str, items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    records = sorted(items.values(), key=lambda record: str(record.get("logical_id", "")))
    successful = [record for record in records if record.get("status") == "ok" and record.get("path")]
    failures = [record for record in records if record.get("status") != "ok"]
    by_base: list[dict[str, Any]] = []
    for base in manifest.get("bases", []):
        base_records = [record for record in records if record.get("base_id") == base["base_id"]]
        observed_records = sum(int(record.get("registros", 0) or 0) for record in base_records)
        observed = observed_records or sum(record.get("status") == "ok" for record in base_records)
        target = base.get("volume_alvo")
        by_base.append({
            "base_id": base["base_id"],
            "natureza": base.get("natureza"),
            "arquivos": sum(record.get("status") == "ok" for record in base_records),
            "formatos": sorted({str(record.get("tipo")) for record in base_records if record.get("tipo")}),
            "bytes": sum(int(record.get("bytes", 0) or 0) for record in base_records if record.get("status") == "ok"),
            "volume_alvo": target,
            "volume_observado": observed,
            "divergencia_alvo": observed - int(target) if target is not None else None,
        })
    return {
        "scenario_id": manifest.get("scenario_id"),
        "version": f"v{version}",
        "coletado_em": collected_at,
        "manifest_hash": manifest_hash,
        "completo": not failures,
        "totais": {"arquivos": len(successful), "bytes": sum(int(record.get("bytes", 0) or 0) for record in successful), "falhas": len(failures)},
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
        "# Inventário do corpus", "", f"- Cenário: `{inventory.get('scenario_id')}`", f"- Versão: `{inventory.get('version')}`",
        f"- Coletado em: `{inventory.get('coletado_em')}`", f"- Arquivos: **{totals.get('arquivos', 0)}**", f"- Bytes: **{totals.get('bytes', 0)}**", f"- Falhas: **{totals.get('falhas', 0)}**", "",
        "## Bases", "", "| Base | Natureza | Arquivos | Formatos | Bytes | Alvo | Divergência |", "|---|---|---:|---|---:|---:|---:|",
    ]
    for base in inventory.get("por_base", []):
        lines.append(f"| `{base['base_id']}` | {base.get('natureza', '')} | {base.get('arquivos', 0)} | {', '.join(base.get('formatos', []))} | {base.get('bytes', 0)} | {base.get('volume_alvo', '')} | {base.get('divergencia_alvo', '')} |")
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
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).as_posix().split("/")
        if not parts:
            continue
        base_id = parts[1] if parts[:1] == ("arquivos",) and len(parts) > 1 else parts[0]
        body = path.read_bytes()
        records.append({"logical_id": f"{base_id}:{_relative_path(path)}", "base_id": base_id, "tipo": path.suffix.lstrip(".") or "bin", "origem": "inventário reconstruído a partir do armazenamento", "coletado_em": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"), "registros": 0, "status": "ok", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "path": _relative_path(path)})
    bases = [{"base_id": base_id, "natureza": "desconhecida"} for base_id in sorted({record["base_id"] for record in records})]
    return _build_inventory({"scenario_id": "desconhecido", "bases": bases}, version, "", datetime.now(timezone.utc).isoformat(timespec="seconds"), {record["logical_id"]: record for record in records})


def _inventory_for_version(version: int) -> dict[str, Any]:
    inventory = _load_inventory(version)
    if inventory is None:
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(context.staging_path), str(destination))
    old_prefix = f"data/raw/.staging/v{context.version}/"
    new_prefix = f"data/raw/v{context.version}/"
    for record in context.items.values():
        if isinstance(record.get("path"), str) and record["path"].startswith(old_prefix):
            record["path"] = new_prefix + record["path"][len(old_prefix):]
    _checkpoint_write(context, state="promoted")


def _find_complete_matching_manifest(manifest_hash: str) -> dict[str, Any] | None:
    for version in reversed(_available_versions()):
        inventory = _load_inventory(version)
        if inventory and inventory.get("manifest_hash") == manifest_hash and inventory.get("completo"):
            return inventory
    return None


def _find_resumable_version(manifest_hash: str) -> int | None:
    checkpoints_dir = INVENTORY_ROOT / "checkpoints"
    if not checkpoints_dir.exists():
        return None
    candidates: list[int] = []
    for child in checkpoints_dir.iterdir():
        version = _version_number(child.name)
        if version is None:
            continue
        try:
            checkpoint = json.loads(child.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if checkpoint.get("manifest_hash") == manifest_hash and checkpoint.get("state") != "promoted":
            candidates.append(version)
    return max(candidates) if candidates else None


def _run_manifest_command(command: str, manifest_path: Path) -> int:
    manifest = load_manifest(manifest_path)
    manifest_hash = _manifest_hash(manifest_path)
    if command == "fetch":
        existing = _find_complete_matching_manifest(manifest_hash)
        if existing:
            _render_corpus_inventory(existing)
            print(json.dumps({"version": existing["version"], "reutilizado": True}, ensure_ascii=False))
            return 0
    previous = None
    if command == "update":
        latest = _latest_version()
        if latest is None:
            raise RuntimeError("update exige uma versão anterior")
        previous = _inventory_for_version(latest)
        version = _next_version()
    else:
        version = _find_resumable_version(manifest_hash) or _next_version()
    context = _load_version_context(manifest, manifest_hash, version)
    for base in manifest["bases"]:
        _collect_base(context, base)
    failures = [item for item in context.items.values() if item.get("status") != "ok"]
    if failures:
        _checkpoint_write(context, state="staging")
    else:
        _promote_version(context)
    inventory = _build_inventory(manifest, version, manifest_hash, context.collected_at, context.items)
    inventory_path = _write_inventory(inventory)
    _render_corpus_inventory(inventory)
    diff_path = _diff_inventories(previous, inventory) if previous is not None else None
    summary = {"version": inventory["version"], "inventario": _relative_path(inventory_path), "diff": _relative_path(diff_path) if diff_path else None, "totais": inventory["totais"], "completo": inventory["completo"]}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if inventory["completo"] else 2


def _manifest_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Materializa o corpus declarado pelo manifesto.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("fetch", "update"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--manifest", type=Path, default=MANIFEST_DEFAULT)
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("--version", required=True, type=str)
    args = parser.parse_args(argv)
    if args.command == "inventory":
        version = _version_number(args.version)
        if version is None:
            parser.error("--version deve usar o formato vN")
        inventory = _inventory_for_version(version)
        print(json.dumps({"version": inventory["version"], "totais": inventory["totais"]}, ensure_ascii=False))
        return 0
    return _run_manifest_command(args.command, args.manifest.resolve())


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"fetch", "update", "inventory"}:
        try:
            return _manifest_main(arguments)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"erro: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    return _legacy_main(arguments)


def _legacy_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quantidade",
        type=int,
        required=True,
        help="quantidade de arquivos novos e únicos por tipo",
    )
    type_group = parser.add_mutually_exclusive_group()
    type_group.add_argument(
        "--tipo",
        choices=FILE_TYPES,
        help="um único tipo de arquivo para coletar",
    )
    type_group.add_argument(
        "--tipos",
        default="html,pdf,json,jsonl",
        help="tipos selecionados, separados por vírgula (html,pdf,json,jsonl)",
    )
    parser.add_argument("--salvar-json", action="store_true", help="salva o relatório em data/runs/")
    parser.add_argument("--dry-run", action="store_true", help="descobre candidatos sem baixar arquivos")
    parser.add_argument("--ano", type=int, default=2025)
    parser.add_argument("--uf", default="RJ")
    parser.add_argument(
        "--modalidade",
        type=int,
        default=None,
        help="filtra uma modalidade; omitido consulta todas",
    )
    parser.add_argument("--tamanho-pagina", type=int, default=10)
    parser.add_argument("--tentativas", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--classificacao-superior", default="979")
    parser.add_argument("--data-inicial", default=None)
    parser.add_argument("--data-final", default=None)
    args = parser.parse_args(argv)

    if args.quantidade < 1:
        parser.error("--quantidade deve ser >= 1")

    if args.tipo:
        tipos = (args.tipo,)
    else:
        tipos = tuple(dict.fromkeys(
            file_type.strip().lower()
            for file_type in args.tipos.split(",")
            if file_type.strip()
        ))
    invalid_types = sorted(set(tipos) - set(FILE_TYPES))
    if not tipos:
        parser.error("--tipos deve conter ao menos um tipo")
    if invalid_types:
        parser.error(f"tipos inválidos: {', '.join(invalid_types)}; use: {', '.join(FILE_TYPES)}")
    if args.tentativas < 1 or args.timeout <= 0:
        parser.error("--tentativas deve ser >= 1 e --timeout deve ser > 0")

    config = _config_from_args(args)
    checked_at, results = _run_checks_for_types(config, tipos)
    candidates = list(iter_candidates(results))
    candidates_by_type = {
        file_type: sum(candidate.file_type == file_type for candidate in candidates)
        for file_type in tipos
    }

    if args.dry_run:
        summary = {
            "candidatos_por_tipo": candidates_by_type,
            "quantidade_por_tipo": {file_type: args.quantidade for file_type in tipos},
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if all(
            count >= args.quantidade for count in candidates_by_type.values()
        ) else 2

    downloads, novos_por_tipo, considerados = fetch_exact(
        results,
        args.quantidade,
        config,
        tipos,
    )
    report: dict[str, Any] = {
        "verificado_em": checked_at,
        "quantidade_por_tipo": {file_type: args.quantidade for file_type in tipos},
        "novos_unicos_por_tipo": novos_por_tipo,
        "candidatos_por_tipo": candidates_by_type,
        "candidatos_considerados": considerados,
        "completo": all(
            novos_por_tipo[file_type] == args.quantidade for file_type in tipos
        ),
        "downloads": downloads,
    }
    print(json.dumps({key: report[key] for key in report if key != "downloads"}, ensure_ascii=False))
    for download in downloads:
        print(f"[{download.get('status')}] {download.get('nome')} — {download.get('arquivo', 'sem arquivo')}")
    if args.salvar_json:
        print(f"relatório={save_report(checked_at, report)}")
    return 0 if report["completo"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
