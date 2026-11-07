"""Descoberta no PNCP: busca de publicações, contratos, atas e PCA, com varredura paginada retomável."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterator

from ..collection_policy import ano_assinatura, nome_instrumento
from .base import SourceCandidate, SourceRef
from .pncp_common import _PncpBase


@dataclass
class _DiscoveryScan:
    """Varredura paginada do PNCP, retomável pelo checkpoint de descoberta.

    Guarda o que as buscas de publicação e de contratos têm em comum: páginas e
    janelas já concluídas, total de páginas por janela, orçamento de páginas da
    execução e o intervalo entre requisições.
    """

    candidates: dict[str, SourceCandidate]
    completed_pages: set[str]
    completed_windows: set[str]
    pages_by_window: dict[str, Any]
    page_size: int
    rate_limit: float
    page_budget: int
    pages_requested: int = 0
    budget_hit: bool = False
    first_request: bool = True
    expected_windows: set[str] = field(default_factory=set)

    @classmethod
    def from_state(
        cls,
        discovery_state: Any,
        candidates: dict[str, SourceCandidate],
        *,
        page_size: int,
        rate_limit: float,
        page_budget: int,
    ) -> "_DiscoveryScan":
        resumed = isinstance(discovery_state, dict)
        return cls(
            candidates=candidates,
            completed_pages=set(discovery_state.get("completed_pages") or []) if resumed else set(),
            completed_windows=set(discovery_state.get("completed_windows") or []) if resumed else set(),
            pages_by_window=dict(discovery_state.get("total_paginas_por_janela") or {}) if resumed else {},
            page_size=page_size,
            rate_limit=rate_limit,
            page_budget=page_budget,
        )

    def starts_window(self, window_key: str) -> bool:
        """Registra a janela como esperada; False quando o checkpoint já a concluiu."""
        self.expected_windows.add(window_key)
        if window_key in self.completed_windows:
            return False
        int(self.pages_by_window.get(window_key) or 0)  # total do checkpoint ilegível falha antes de pedir a janela
        return True

    def pages(self, window_key: str, max_pages: int) -> Iterator[tuple[int, str]]:
        """Páginas a pedir na janela: pula as concluídas e para no orçamento da execução."""
        for page in range(1, max_pages + 1):
            if self.page_budget and self.pages_requested >= self.page_budget:
                self.budget_hit = True
                return
            page_key = f"{window_key}:pagina={page}"
            if page_key in self.completed_pages:
                expected_total_pages = int(self.pages_by_window.get(window_key) or 0)
                if expected_total_pages and page >= expected_total_pages:
                    return
                continue
            if self.rate_limit and not self.first_request:
                time.sleep(self.rate_limit)
            self.first_request = False
            yield page, page_key

    def record_page(self, window_key: str, page: int, payload: Any, records: list[dict[str, Any]]) -> bool:
        """Guarda o total de páginas informado e diz se a janela terminou nesta página."""
        total_pages = payload.get("totalPaginas") if isinstance(payload, dict) else None
        if total_pages is not None:
            self.pages_by_window[window_key] = int(total_pages)
        return (
            not records
            or (total_pages is not None and page >= int(total_pages))
            or (total_pages is None and len(records) < self.page_size)
        )

    def notify(
        self,
        query: dict[str, Any],
        window_key: str,
        page: int,
        page_key: str,
        payload: Any,
        window_done: bool,
        candidates: list[SourceCandidate],
        **extra: Any,
    ) -> None:
        on_page = query.get("on_discovery_page")
        if callable(on_page):
            on_page(
                {
                    "window": window_key,
                    "page": page,
                    "page_key": page_key,
                    "total_paginas": int(self.pages_by_window.get(window_key) or 0) or None,
                    "total_registros": payload.get("totalRegistros") if isinstance(payload, dict) else None,
                    "window_done": window_done,
                    "candidates": candidates,
                    **extra,
                }
            )

    def finish(self, discovery_state: Any) -> None:
        if isinstance(discovery_state, dict):
            discovery_state["pages_this_run"] = self.pages_requested
            discovery_state["max_pages_this_run"] = self.page_budget or None
            discovery_state["limite_execucao_atingido"] = self.budget_hit
            discovery_state["scan_completo"] = self.expected_windows.issubset(
                set(discovery_state.get("completed_windows") or [])
            )


class _PncpDiscovery(_PncpBase):
    """``search``: candidatos de publicações, contratos assinados, atas e PCA."""

    @staticmethod
    def _api_date(value: Any) -> str:
        return str(value or "").replace("-", "")

    def _date_windows(self, start: date, end: date) -> list[tuple[str, str]]:
        """Janelas de até 365 dias cobrindo o período, com as datas no formato da API."""
        if end < start:
            raise ValueError("dataFinal deve ser igual ou posterior a dataInicial")
        windows: list[tuple[str, str]] = []
        window_start = start
        while window_start <= end:
            window_end = min(end, window_start + timedelta(days=364))
            windows.append((self._api_date(window_start.isoformat()), self._api_date(window_end.isoformat())))
            window_start = window_end + timedelta(days=1)
        return windows

    @staticmethod
    def _restored_candidates(discovery_state: Any, key: str, title: str, by_contract: bool = False) -> dict[str, SourceCandidate]:
        """Candidatos já descobertos que o checkpoint guardou, sem os metadados da página."""
        candidates: dict[str, SourceCandidate] = {}
        if not isinstance(discovery_state, dict):
            return candidates
        for stable_id, source_value in (discovery_state.get(key) or {}).items():
            try:
                source_ref = SourceRef.from_dict(source_value)
            except (TypeError, ValueError, KeyError):
                continue
            candidate_id = str(source_ref.key.get("numeroControlePNCP") or stable_id) if by_contract else stable_id
            candidates[candidate_id] = SourceCandidate(
                candidate_id=candidate_id,
                source_ref=source_ref,
                title=title,
                metadata={},
            )
        return candidates

    def _search_publication(self, query: dict[str, Any], target: int) -> list[SourceCandidate]:
        params = dict(query.get("params") or {})
        year = int(params.get("ano") or query.get("ano") or 2025)
        publication_windows = self._date_windows(
            date.fromisoformat(str(params.get("dataInicial") or f"{year}-01-01")),
            date.fromisoformat(str(params.get("dataFinal") or f"{year}-12-31")),
        )
        page_size = max(10, min(int(params.get("tamanhoPagina") or 50), 50))
        max_pages = max(
            1,
            int(
                params.get("maxPaginas")
                or (10_000 if query.get("censo") else ((target + page_size - 1) // page_size + 2))
            ),
        )
        modalities = params.get("modalidades") or params.get("codigoModalidadeContratacao") or [1]
        if isinstance(modalities, (str, int)):
            modalities = [modalities]
        discovery_state = query.get("discovery_state")
        scan = _DiscoveryScan.from_state(
            discovery_state,
            self._restored_candidates(discovery_state, "publication_sources", "Contratação PNCP"),
            page_size=page_size,
            rate_limit=max(0.0, float(params.get("rate_limit_seg") or query.get("rate_limit_seg") or 0)),
            page_budget=max(0, int(query.get("max_pages") or 0)) if query.get("discovery_only") else 0,
        )
        uf = params.get("uf") or "RJ"
        for modality in modalities:
            for range_start, range_end in publication_windows:
                window_key = f"{range_start}:{range_end}:modalidade={modality}:uf={uf}"
                if not scan.starts_window(window_key):
                    continue
                request_params = {
                    "dataInicial": range_start,
                    "dataFinal": range_end,
                    "codigoModalidadeContratacao": modality,
                    "uf": uf,
                    "tamanhoPagina": page_size,
                }
                self._scan_publication_window(scan, query, window_key, request_params, max_pages)
                if scan.budget_hit:
                    break
            if scan.budget_hit:
                break
        scan.finish(discovery_state)
        ordered = [scan.candidates[key] for key in sorted(scan.candidates)]
        return [] if query.get("discovery_only") else ordered if query.get("censo") else ordered[:target]

    def _scan_publication_window(
        self,
        scan: _DiscoveryScan,
        query: dict[str, Any],
        window_key: str,
        request_params: dict[str, Any],
        max_pages: int,
    ) -> None:
        for page, page_key in scan.pages(window_key, max_pages):
            try:
                response = self.client.request_url(
                    self.client.consulta_url(
                        "/v1/contratacoes/publicacao", {**request_params, "pagina": page}
                    ),
                    "json",
                    attempts=5,
                    timeout=45.0,
                )
            except RuntimeError:
                if query.get("censo"):
                    raise
                break
            if response.get("status") not in {"disponivel", "nao_modificado"}:
                if query.get("censo"):
                    raise RuntimeError(
                        f"PNCP indisponível em /v1/contratacoes/publicacao para UF {request_params['uf']}, "
                        f"modalidade {request_params['codigoModalidadeContratacao']}, "
                        f"janela {request_params['dataInicial']}–{request_params['dataFinal']}, página {page}: "
                        f"{response.get('erro') or response.get('status') or 'resposta sem status'}"
                    )
                break
            payload = response.get("json") or {}
            records = self._records(payload)
            scan.pages_requested += 1
            page_candidates, page_organizations = self._publication_page(records)
            for candidate in page_candidates:
                scan.candidates[candidate.candidate_id] = candidate
            window_done = scan.record_page(window_key, page, payload, records)
            scan.notify(query, window_key, page, page_key, payload, window_done, page_candidates,
                        publication_organizations=page_organizations)
            if window_done:
                break

    def _publication_page(self, records: list[dict[str, Any]]) -> tuple[list[SourceCandidate], list[dict[str, Any]]]:
        """(compras da página, órgãos que publicaram nela)."""
        page_candidates: list[SourceCandidate] = []
        page_organizations: list[dict[str, Any]] = []
        for record in records:
            organization = record.get("orgaoEntidade") or {}
            unit = record.get("unidadeOrgao") or {}
            organization_cnpj = self._first(
                organization,
                "cnpj",
                "cnpjOrgao",
            ) if isinstance(organization, dict) else None
            if organization_cnpj:
                page_organizations.append(
                    {
                        "cnpj": str(organization_cnpj),
                        "unidadeOrgao": unit if isinstance(unit, dict) else {},
                    }
                )
            source_ref = self.purchase_ref(record)
            if source_ref is None:
                continue
            page_candidates.append(
                SourceCandidate(
                    candidate_id=source_ref.stable_id,
                    source_ref=source_ref,
                    title=self._candidate_title(record, "Contratação PNCP"),
                    metadata={
                        **record,
                        "_source_url": self.client.consulta_base + self._purchase_path(source_ref),
                    },
                )
            )
        return page_candidates, page_organizations
    @staticmethod
    def _contract_parts(record: dict[str, Any]) -> tuple[str, str, str, str] | None:
        control = _PncpBase._first(record, "numeroControlePNCP", "numeroControlePncp")
        if not control:
            return None
        control = str(control)
        pieces = control.rsplit("-", 2)
        suffix = pieces[-1] if pieces else ""
        if len(pieces) == 3 and "/" in suffix:
            sequence, year = suffix.split("/", 1)
            cnpj = str(
                _PncpBase._first(record, "cnpjOrgao", "cnpj") or pieces[0]
            )
        else:
            cnpj = str(_PncpBase._first(record, "cnpjOrgao", "cnpj") or "")
            year = str(_PncpBase._first(record, "anoContrato", "anoContratoEmpenho") or "")
            sequence = str(
                _PncpBase._first(record, "sequencialContrato", "sequencialContratoEmpenho", "sequencial") or ""
            )
        year = str(_PncpBase._first(record, "anoContrato", "anoContratoEmpenho") or year)
        sequence = str(
            _PncpBase._first(record, "sequencialContrato", "sequencialContratoEmpenho", "sequencial") or sequence
        )
        if not all((cnpj, year, sequence)):
            return None
        return control, cnpj, year, sequence

    @staticmethod
    def _is_empenho(record: dict[str, Any]) -> bool:
        return "empenho" in nome_instrumento(record).casefold()

    @staticmethod
    def _signature_year(record: dict[str, Any]) -> str:
        return ano_assinatura(record)

    def _search_signed_contracts(self, query: dict[str, Any], target: int) -> list[SourceCandidate]:
        params = dict(query.get("params") or {})
        cnpj_scopes = self._contract_scopes(query, params)
        signature_year = str(params.get("anoAssinatura") or query.get("anoAssinatura") or "2025")
        publication_windows = self._date_windows(
            date.fromisoformat(str(params.get("dataInicial") or "2025-01-01")),
            date.fromisoformat(str(params.get("dataFinal") or "2026-09-25")),
        )
        page_size = max(1, min(int(params.get("tamanhoPagina") or 500), 500))
        rate_limit = max(0.0, float(params.get("rate_limit_seg") or query.get("rate_limit_seg") or 0))
        discovery_state = query.get("discovery_state")
        scan = _DiscoveryScan.from_state(
            discovery_state,
            self._restored_candidates(discovery_state, "eligible_sources", "Contrato PNCP", by_contract=True),
            page_size=page_size,
            rate_limit=rate_limit,
            page_budget=max(0, int(query.get("max_pages") or 0)) if query.get("discovery_only") else 0,
        )
        for cnpj, unit_code in cnpj_scopes:
            for range_start, range_end in publication_windows:
                scope_key = f"{cnpj or 'nacional'}:{unit_code or 'todas'}"
                window_key = f"{range_start}:{range_end}:{scope_key}"
                if not scan.starts_window(window_key):
                    continue
                first_params = {
                    "dataInicial": range_start,
                    "dataFinal": range_end,
                    "pagina": 1,
                    "tamanhoPagina": page_size,
                }
                if cnpj:
                    first_params["cnpjOrgao"] = cnpj
                if unit_code:
                    first_params["codigoUnidadeAdministrativa"] = unit_code
                self._scan_contract_window(scan, query, window_key, first_params, cnpj, unit_code, signature_year)
                if scan.budget_hit:
                    break
            if scan.budget_hit:
                break
        scan.finish(discovery_state)
        ordered = [scan.candidates[key] for key in sorted(scan.candidates)]
        return ordered if query.get("censo") else ordered[:target]

    def _contract_scopes(self, query: dict[str, Any], params: dict[str, Any]) -> list[tuple[str | None, str | None]]:
        """(CNPJ, unidade) a consultar: unidades RJ verificadas ou, sem elas, os CNPJs configurados."""
        unit_scopes = query.get("verified_unit_scopes") or params.get("verified_unit_scopes") or []
        if (query.get("require_verified_unit_scopes") or query.get("censo_nacional")) and not unit_scopes:
            raise RuntimeError("nenhuma unidade RJ verificada disponível para consultar contratos")
        if not unit_scopes:
            return [(cnpj, None) for cnpj in self._load_contract_cnpjs(query, params)] or [(None, None)]
        normalized_scopes = sorted(
            {
                (
                    str(scope.get("cnpj") or "").strip(),
                    str(scope.get("codigoUnidadeAdministrativa") or "").strip(),
                )
                for scope in unit_scopes
                if isinstance(scope, dict)
                and str(scope.get("cnpj") or "").strip()
                and str(scope.get("codigoUnidadeAdministrativa") or "").strip()
                and str(scope.get("ufSigla") or "").upper() == "RJ"
            }
        )
        if (query.get("require_verified_unit_scopes") or query.get("censo_nacional")) and len(normalized_scopes) != len(unit_scopes):
            raise RuntimeError("escopos de unidade inválidos ou fora do RJ no checkpoint")
        return list(normalized_scopes)

    def _scan_contract_window(
        self,
        scan: _DiscoveryScan,
        query: dict[str, Any],
        window_key: str,
        first_params: dict[str, Any],
        cnpj: str | None,
        unit_code: str | None,
        signature_year: str,
    ) -> None:
        for page, page_key in scan.pages(window_key, 100_000):
            request_params = {**first_params, "pagina": page}
            response = self.client.request_url(
                self.client.consulta_url("/v1/contratos", request_params),
                "json",
                attempts=5,
                timeout=45.0,
            )
            scan.pages_requested += 1
            if response.get("status") not in {"disponivel", "nao_modificado"}:
                raise RuntimeError(
                    f"PNCP indisponível em /v1/contratos para CNPJ {cnpj or 'consulta nacional'}, "
                    f"janela {first_params['dataInicial']}–{first_params['dataFinal']}, página {page}: "
                    f"{response.get('erro') or response.get('status') or 'resposta sem status'}"
                )
            payload = response.get("json") or {}
            records = self._records(payload)
            page_candidates: list[SourceCandidate] = []
            for record in records:
                candidate = self._contract_candidate(record, request_params, signature_year, unit_code)
                if candidate is None:
                    continue
                control = candidate.candidate_id
                previous_candidate = scan.candidates.get(control)
                previous_purchase = (
                    previous_candidate.source_ref.key.get("numeroControlePNCPCompra")
                    if previous_candidate
                    else None
                )
                current_purchase = candidate.source_ref.key.get("numeroControlePNCPCompra")
                # A mesma compra vinculada vence de forma estável: a de menor controle.
                if previous_candidate is None or (
                    current_purchase
                    and (not previous_purchase or str(current_purchase) < str(previous_purchase))
                ):
                    scan.candidates[control] = candidate
                page_candidates.append(scan.candidates[control])
            window_done = scan.record_page(window_key, page, payload, records)
            scan.notify(query, window_key, page, page_key, payload, window_done, page_candidates)
            if window_done:
                break

    def _contract_candidate(
        self, record: dict[str, Any], request_params: dict[str, Any], signature_year: str, unit_code: str | None
    ) -> SourceCandidate | None:
        """Contrato assinado no ano, numa unidade do RJ (e na unidade pedida); empenhos ficam de fora."""
        parts = self._contract_parts(record)
        if parts is None or self._is_empenho(record):
            return None
        unit_record = self._first(record, "unidadeOrgao", "unidadeAdministrativa") or {}
        record_unit = self._first(
            record,
            "codigoUnidadeAdministrativa",
            "codigoUnidade",
        ) or self._first(
            unit_record,
            "codigoUnidadeAdministrativa",
            "codigoUnidade",
            "codigo",
        )
        if (
            self._signature_year(record) != signature_year
            or self._unit_uf(record) != "RJ"
            or (unit_code and str(record_unit or "") != unit_code)
        ):
            return None
        control, contract_cnpj, year, sequence = parts
        purchase_control = self._first(
            record,
            "numeroControlePNCPCompra",
            "numeroControlePncpCompra",
        )
        source_ref = SourceRef(
            provider=self.name,
            resource_type="contrato_assinado",
            key={
                "numeroControlePNCP": control,
                "cnpj": contract_cnpj,
                "ano": year,
                "sequencial": sequence,
                **(
                    {"codigoUnidadeAdministrativa": str(record_unit)}
                    if record_unit not in (None, "")
                    else {}
                ),
                **({"numeroControlePNCPCompra": str(purchase_control)} if purchase_control else {}),
            },
        )
        return SourceCandidate(
            candidate_id=control,
            source_ref=source_ref,
            title=self._candidate_title(record, "Contrato PNCP"),
            metadata={
                **record,
                "_source_url": self.client.consulta_url("/v1/contratos", request_params),
                "_resource_kind": "contrato",
            },
        )

    def _search_atas(self, query: dict[str, Any], target: int) -> list[SourceCandidate]:
        params = dict(query.get("params") or {})
        year = int(params.get("ano") or query.get("ano") or 2025)
        page_size = max(10, min(int(params.get("tamanhoPagina") or 50), 100))
        max_pages = max(1, int(params.get("maxPaginas") or ((target + page_size - 1) // page_size + 2)))
        request_params = {
            "dataInicial": self._api_date(params.get("dataInicial") or f"{year}-01-01"),
            "dataFinal": self._api_date(params.get("dataFinal") or f"{year}-12-31"),
            "tamanhoPagina": page_size,
        }
        # /v1/atas ignora ``uf``: o recorte geográfico só é possível pelo CNPJ
        # do órgão. Sem ``cnpjs``, a busca cobre atas de todo o país.
        cnpjs = params.get("cnpjs") or params.get("cnpj") or [None]
        if isinstance(cnpjs, (str, int)):
            cnpjs = [cnpjs]
        candidates: list[SourceCandidate] = []
        for cnpj in cnpjs:
            cnpj_params = {**request_params, "cnpj": str(cnpj)} if cnpj else request_params
            for page in range(1, max_pages + 1):
                records = self._request_records("/v1/atas", {**cnpj_params, "pagina": page})
                if not records:
                    break
                candidates.extend(
                    candidate for record in records if (candidate := self._ata_candidate(record)) is not None
                )
                if len(candidates) >= target:
                    return candidates
                if len(records) < page_size:
                    break
        return candidates

    def _search_pca(self, query: dict[str, Any], target: int) -> list[SourceCandidate]:
        params = dict(query.get("params") or {})
        try:
            rate_limit = max(0.0, float(query.get("rate_limit_seg") or params.get("rate_limit_seg") or 0))
        except (TypeError, ValueError):
            rate_limit = 0.0
        cnpjs = self._load_contract_cnpjs(query, params)
        years = params.get("anos") or query.get("anos") or params.get("ano") or [2025]
        if isinstance(years, (str, int)):
            years = [years]
        candidates: list[SourceCandidate] = []
        for cnpj in cnpjs:
            for year in years:
                if rate_limit:
                    time.sleep(rate_limit)
                path = f"/v1/orgaos/{cnpj}/pca/{year}/consolidado/unidades"
                response = self.client.request_url(self.client.api_url(path), "json", attempts=3, timeout=30.0)
                if response.get("status") not in {"disponivel", "nao_modificado"}:
                    raise RuntimeError(
                        f"PNCP indisponível em {path} para CNPJ {cnpj}, ano {year}: "
                        f"{response.get('erro') or response.get('status') or 'resposta sem status'}"
                    )
                for record in self._records(response.get("json")):
                    sequence = self._first(record, "sequencialPca", "sequencial")
                    if sequence in (None, ""):
                        continue
                    source_ref = SourceRef(
                        provider=self.name,
                        resource_type="pca",
                        key={"cnpj": str(cnpj), "ano": str(year), "sequencial": str(sequence)},
                    )
                    candidates.append(
                        SourceCandidate(
                            candidate_id=source_ref.stable_id,
                            source_ref=source_ref,
                            title=self._candidate_title(record, "PCA PNCP"),
                            metadata=record,
                        )
                    )
        return candidates

    def search(self, query: dict[str, Any]) -> list[SourceCandidate]:
        """Descobre referências PNCP sem gravar arquivos."""
        resource_type = str(query.get("resource_type") or "").casefold()
        target = max(1, int(query.get("volume_alvo") or query.get("limite") or 1))
        if resource_type in {"contrato_assinado", "contrato_pncp"}:
            candidates = self._search_signed_contracts(query, target)
        elif resource_type in {"contratacao", "contratação", "contrato"}:
            candidates = self._search_publication(query, target)
        elif resource_type in {"ata", "ata_registro_preco"}:
            candidates = self._search_atas(query, target)
        elif resource_type in {"pca", "pca_agregado"}:
            candidates = self._search_pca(query, target)
        else:
            raise ValueError(f"resource_type PNCP não suportado: {resource_type or '<vazio>'}")

        unique = {candidate.candidate_id: candidate for candidate in candidates}
        ordered = [unique[key] for key in sorted(unique)]
        return ordered if query.get("censo") else ordered[:target]
