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
import importlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


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


def main() -> int:
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
    args = parser.parse_args()

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
