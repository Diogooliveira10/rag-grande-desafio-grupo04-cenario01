"""Checagens das fontes declaradas no manifesto (``check``): candidatos, amostras e verificações por tipo."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from rich.console import Console



source_check = importlib.import_module("src.source_catalog")  # noqa: E402


RICH_CONSOLE = Console()


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
    yield from _candidatos_da_api(results, seen_urls)
    yield from _candidatos_web(results, seen_urls)
    yield from _candidatos_de_arquivos_pncp(results, seen_urls)


def _candidatos_da_api(results: list[dict[str, Any]], seen_urls: set[str]) -> Iterator[FetchCandidate]:
    """O JSON de cada consulta disponível da API e, quando há registros, o JSONL deles."""
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


def _candidatos_web(results: list[dict[str, Any]], seen_urls: set[str]) -> Iterator[FetchCandidate]:
    """As fontes web fixas cuja checagem deu disponível."""
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


def _candidatos_de_arquivos_pncp(results: list[dict[str, Any]], seen_urls: set[str]) -> Iterator[FetchCandidate]:
    """Cada arquivo de edital ou de ata descoberto no PNCP."""
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
