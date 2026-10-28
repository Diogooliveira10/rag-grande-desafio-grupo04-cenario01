"""Resolução de referências PNCP sem acoplar a CLI ou o coletor a URLs."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode

from ..pncp_client import PncpClient
from ..collection_policy import ano_assinatura, nome_instrumento, uf_unidade
from .base import ResolvedSource, SourceArtifact, SourceCandidate, SourceRef


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


class PNCPProvider:
    name = "pncp"

    def __init__(self, client: PncpClient | None = None) -> None:
        self.client = client or PncpClient()
        self._comprasgov_item_ids: dict[str, dict[str, str]] = {}

    @staticmethod
    def _key(source_ref: SourceRef, *names: str) -> tuple[str, ...]:
        values = tuple(str(source_ref.key.get(name, "")).strip() for name in names)
        if any(not value for value in values):
            joined = ", ".join(names)
            raise ValueError(f"SourceRef PNCP exige: {joined}")
        return values

    def _purchase_parts(self, source_ref: SourceRef) -> tuple[str, str, str]:
        cnpj, year, sequence = self._key(source_ref, "cnpj", "ano", "sequencial")
        return cnpj, year, sequence

    def _purchase_control_number(self, source_ref: SourceRef) -> str:
        cnpj, year, sequence = self._purchase_parts(source_ref)
        return f"{cnpj}-1-{sequence}/{year}"

    @staticmethod
    def _comprasgov_url(resource: str, purchase_control: str, item_id: str | None = None) -> str:
        endpoint = {
            "contratacao": "1.1_consultarContratacoes_PNCP_14133_Id",
            "itens": "2.1_consultarItensContratacoes_PNCP_14133_Id",
            "resultado_item": "3.1_consultarResultadoItensContratacoes_PNCP_14133_Id",
        }[resource]
        params = {"tipo": "numeroControlePNCPCompra", "codigo": purchase_control}
        if item_id:
            params["idCompraItem"] = item_id
        return (
            "https://dadosabertos.compras.gov.br/modulo-contratacoes/"
            f"{endpoint}?{urlencode(params)}"
        )

    def request_agregada_fallback(self, fallback: dict[str, Any]) -> dict[str, Any]:
        """Consulta Dados Abertos por ID após falha de transporte do PNCP."""
        resource = str(fallback.get("resource") or "")
        purchase_control = str(fallback.get("numeroControlePNCPCompra") or "").strip()
        if not purchase_control or resource not in {"contratacao", "itens", "resultado_item"}:
            return {"status": "fallback_invalido", "erro": "referência de fallback incompleta"}

        if resource == "resultado_item":
            item_number = str(fallback.get("numeroItem") or "").strip()
            item_id = str(fallback.get("idCompraItem") or "").strip()
            cached = self._comprasgov_item_ids.get(purchase_control, {})
            item_id = item_id or cached.get(item_number, "")
            if not item_id:
                items_url = self._comprasgov_url("itens", purchase_control)
                items_response = self.client.request_url(
                    items_url, "json", attempts=2, timeout=20.0
                )
                if items_response.get("status") not in {"disponivel", "nao_modificado"}:
                    items_response["fallback_lookup_url"] = items_url
                    return items_response
                self._remember_comprasgov_items(purchase_control, items_response.get("json"))
                item_id = self._comprasgov_item_ids.get(purchase_control, {}).get(item_number, "")
            if not item_id:
                return {
                    "status": "referencia_nao_encontrada",
                    "url_final": self._comprasgov_url("itens", purchase_control),
                    "erro": f"item {item_number!r} sem idCompraItem na API alternativa",
                }
            url = self._comprasgov_url("resultado_item", purchase_control, item_id)
        else:
            url = self._comprasgov_url(resource, purchase_control)

        response = self.client.request_url(url, "json", attempts=2, timeout=20.0)
        response["url_final"] = response.get("url_final") or url
        if resource == "itens" and response.get("status") in {"disponivel", "nao_modificado"}:
            self._remember_comprasgov_items(purchase_control, response.get("json"))
        response["acquisition_backend"] = "compras_dados_abertos"
        response["fallback_from"] = "pncp"
        return response

    def _remember_comprasgov_items(self, purchase_control: str, payload: Any) -> None:
        item_ids: dict[str, str] = {}
        for item in self._records(payload):
            number = self._first(item, "numeroItemPncp", "numeroItem", "numeroItemCompra")
            item_id = item.get("idCompraItem")
            if number not in (None, "") and item_id not in (None, ""):
                item_ids[str(number)] = str(item_id)
        self._comprasgov_item_ids[purchase_control] = item_ids

    def _purchase_path(self, source_ref: SourceRef) -> str:
        cnpj, year, sequence = self._purchase_parts(source_ref)
        return f"/v1/orgaos/{cnpj}/compras/{year}/{sequence}"

    def _ata_path(self, source_ref: SourceRef) -> str:
        cnpj, year, sequence = self._key(
            source_ref,
            "cnpj",
            "ano_compra",
            "sequencial_compra",
        )
        ata = self._key(source_ref, "sequencial_ata")[0]
        return f"/v1/orgaos/{cnpj}/compras/{year}/{sequence}/atas/{ata}"

    def _pca_parts(self, source_ref: SourceRef) -> tuple[str, str, str]:
        cnpj, year, sequence = self._key(source_ref, "cnpj", "ano", "sequencial")
        return cnpj, year, sequence

    @staticmethod
    def purchase_ref(purchase: dict[str, Any]) -> SourceRef | None:
        """Converte os campos de uma resposta de contratação em identidade estável."""
        values = {
            "cnpj": purchase.get("cnpj") or purchase.get("cnpjOrgao"),
            "ano": purchase.get("ano") or purchase.get("anoCompra"),
            "sequencial": purchase.get("sequencial") or purchase.get("sequencialCompra"),
        }
        control_number = (
            purchase.get("numeroControlePNCP")
            or purchase.get("numeroControlePncp")
            or purchase.get("numeroControlePNCPCompra")
            or purchase.get("numeroControlePncpCompra")
        )
        match = re.match(r"^(\d{14})-\d+-(\d+)/(\d{4})$", str(control_number or ""))
        if match:
            values["cnpj"] = values["cnpj"] or match.group(1)
            values["sequencial"] = values["sequencial"] or match.group(2)
            values["ano"] = values["ano"] or match.group(3)
        if any(value in (None, "") for value in values.values()):
            return None
        return SourceRef(provider="pncp", resource_type="contratacao", key=values)

    @staticmethod
    def _records(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in (
                "data",
                "items",
                "itens",
                "unidades",
                "listaUnidades",
                "content",
                "results",
                "resultado",
                "resultados",
                "listaResultados",
            ):
                records = PNCPProvider._records(payload.get(key))
                if records:
                    return records
        return []

    @staticmethod
    def _first(record: dict[str, Any], *names: str) -> Any:
        for name in names:
            value = record.get(name)
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _candidate_title(record: dict[str, Any], default: str) -> str:
        value = PNCPProvider._first(
            record,
            "objetoCompra",
            "objetoContratacao",
            "objeto",
            "descricaoObjeto",
            "nomeUnidade",
            "numeroAtaRegistroPreco",
        )
        return str(value or default)

    @staticmethod
    def _api_date(value: Any) -> str:
        return str(value or "").replace("-", "")

    def _request_records(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = self.client.request_url(
            self.client.consulta_url(path, params),
            "json",
            attempts=int(params.pop("_attempts", 3)),
            timeout=float(params.pop("_timeout", 30.0)),
        )
        if response.get("status") not in {"disponivel", "nao_modificado"}:
            raise RuntimeError(
                f"PNCP indisponível em {path}: "
                f"{response.get('erro') or response.get('status') or 'resposta sem status'}"
            )
        return self._records(response.get("json"))

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

    def _ata_candidate(self, record: dict[str, Any]) -> SourceCandidate | None:
        control_number = self._first(
            record,
            "numeroControlePNCPAta",
            "numeroControlePncpAta",
            "numeroControlePNCP",
            "numeroControlePncp",
        )
        match = re.match(
            r"^(\d{14})-\d+-(\d+)/(\d{4})-(\d+)$",
            str(control_number or ""),
        )
        cnpj = self._first(record, "cnpj", "cnpjOrgao")
        purchase_year = self._first(record, "anoCompra", "ano")
        purchase_sequence = self._first(record, "sequencialCompra", "sequencial")
        ata_sequence = self._first(record, "sequencialAta", "seqAta")
        if match:
            cnpj = cnpj or match.group(1)
            purchase_sequence = purchase_sequence or match.group(2)
            purchase_year = purchase_year or match.group(3)
            ata_sequence = ata_sequence or match.group(4)
        if not all(value not in (None, "") for value in (cnpj, purchase_year, purchase_sequence, ata_sequence)):
            return None
        source_ref = SourceRef(
            provider=self.name,
            resource_type="ata",
            key={
                "cnpj": str(cnpj),
                "ano_compra": str(purchase_year),
                "sequencial_compra": str(purchase_sequence),
                "sequencial_ata": str(ata_sequence),
            },
        )
        return SourceCandidate(
            candidate_id=source_ref.stable_id,
            source_ref=source_ref,
            title=self._candidate_title(record, "Ata PNCP"),
            metadata=record,
        )
    @staticmethod
    def _contract_parts(record: dict[str, Any]) -> tuple[str, str, str, str] | None:
        control = PNCPProvider._first(record, "numeroControlePNCP", "numeroControlePncp")
        if not control:
            return None
        control = str(control)
        pieces = control.rsplit("-", 2)
        suffix = pieces[-1] if pieces else ""
        if len(pieces) == 3 and "/" in suffix:
            sequence, year = suffix.split("/", 1)
            cnpj = str(
                PNCPProvider._first(record, "cnpjOrgao", "cnpj") or pieces[0]
            )
        else:
            cnpj = str(PNCPProvider._first(record, "cnpjOrgao", "cnpj") or "")
            year = str(PNCPProvider._first(record, "anoContrato", "anoContratoEmpenho") or "")
            sequence = str(
                PNCPProvider._first(record, "sequencialContrato", "sequencialContratoEmpenho", "sequencial") or ""
            )
        year = str(PNCPProvider._first(record, "anoContrato", "anoContratoEmpenho") or year)
        sequence = str(
            PNCPProvider._first(record, "sequencialContrato", "sequencialContratoEmpenho", "sequencial") or sequence
        )
        if not all((cnpj, year, sequence)):
            return None
        return control, cnpj, year, sequence

    @staticmethod
    def _is_empenho(record: dict[str, Any]) -> bool:
        return "empenho" in nome_instrumento(record).casefold()

    @staticmethod
    def _unit_uf(record: dict[str, Any]) -> str:
        return uf_unidade(record)

    @staticmethod
    def _signature_year(record: dict[str, Any]) -> str:
        return ano_assinatura(record)

    def _load_contract_cnpjs(self, query: dict[str, Any], params: dict[str, Any]) -> list[str]:
        if query.get("censo_nacional"):
            return []
        cnpjs: Any = (
            params.get("cnpjs")
            or query.get("cnpjs")
            or params.get("cnpj")
            or query.get("cnpj")
            or []
        )
        if isinstance(cnpjs, str):
            cnpjs = [cnpjs]
        roster_file = params.get("cnpjs_file") or query.get("cnpjs_file")
        if not cnpjs and roster_file:
            path = Path(str(roster_file))
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[2] / path
            if not path.exists():
                raise RuntimeError(f"cadastro de CNPJs não encontrado: {roster_file}")
            roster = json.loads(path.read_text(encoding="utf-8"))
            if query.get("exigir_cadastro_completo") and roster.get("completo") is not True:
                raise RuntimeError("cadastro de CNPJs do RJ ainda não foi reconciliado como completo")
            cnpjs = roster.get("cnpjs") or []
        normalized = sorted({str(value).strip() for value in cnpjs if str(value).strip()})
        if not normalized:
            raise RuntimeError("nenhum CNPJ configurado para a consulta PNCP")
        return normalized

    def verify_discovered_units(
        self,
        discovery_state: dict[str, Any],
        on_organization_verified: Any = None,
        rate_limit_seg: float = 0,
    ) -> list[dict[str, str]]:
        """Resolve the official unit list for each discovered organization and keep RJ units only."""
        organizations = sorted(
            {
                str(value).strip()
                for value in discovery_state.get("cnpjs_derivados", [])
                if str(value).strip()
            }
        )
        verifications = discovery_state.setdefault("verificacoes_orgaos", {})
        observed_by_cnpj = discovery_state.get("unidades_observadas_por_cnpj") or {}
        scopes: dict[tuple[str, str], dict[str, str]] = {}
        rate_limit = max(0.0, float(rate_limit_seg or 0))
        first_request = True
        for cnpj in organizations:
            saved = verifications.get(cnpj)
            if isinstance(saved, dict) and saved.get("status") == "verificado":
                units = saved.get("unidades_rj") or []
            else:
                if rate_limit and not first_request:
                    time.sleep(rate_limit)
                first_request = False
                units = self._request_rj_units(cnpj, verifications, on_organization_verified)
                if units is None:
                    continue
            verification = verifications[cnpj]
            observed_codes = {
                str(code).strip()
                for code in (observed_by_cnpj.get(cnpj) or {})
                if str(code).strip()
            }
            verified_codes = {
                str(unit.get("codigoUnidadeAdministrativa") or "").strip()
                for unit in units
                if str(unit.get("codigoUnidadeAdministrativa") or "").strip()
            }
            verification["unidades_publicadas_observadas"] = len(observed_codes)
            verification["unidades_observadas_nao_listadas_pelo_orgao"] = sorted(
                observed_codes - verified_codes
            )
            if callable(on_organization_verified):
                on_organization_verified(cnpj, verification)
            for unit in units:
                code = str(unit.get("codigoUnidadeAdministrativa") or "").strip()
                # Uma unidade RJ cadastrada não deixa de ser elegível só por
                # não ter aparecido nas publicações já observadas.
                if code:
                    scopes[(cnpj, code)] = {
                        "cnpj": cnpj,
                        "codigoUnidadeAdministrativa": code,
                        "ufSigla": "RJ",
                        **(
                            {"codigoIbge": str(unit["codigoIbge"])}
                            if unit.get("codigoIbge") not in (None, "")
                            else {}
                        ),
                    }
        result = [scopes[key] for key in sorted(scopes)]
        self._summarize_unit_verification(discovery_state, organizations, verifications, observed_by_cnpj, result)
        return result

    def _request_rj_units(
        self, cnpj: str, verifications: dict[str, Any], on_organization_verified: Any
    ) -> list[dict[str, str]] | None:
        """Unidades RJ do cadastro oficial do órgão, registradas em ``verifications``; None se a consulta falhou."""
        response = self.client.request_url(
            self.client.api_url(f"/v1/orgaos/{cnpj}/unidades"),
            "json",
            attempts=5,
            timeout=45.0,
        )
        if response.get("status") not in {"disponivel", "nao_modificado"}:
            verification = {
                "status": "falhou",
                "erro": response.get("erro") or response.get("status") or "resposta sem status",
            }
            verifications[cnpj] = verification
            if callable(on_organization_verified):
                on_organization_verified(cnpj, verification)
            return None
        units = []
        for unit in self._records(response.get("json")):
            code = self._first(
                unit,
                "codigoUnidadeAdministrativa",
                "codigoUnidade",
                "codigo",
            )
            if not code or self._unit_uf({"unidadeOrgao": unit}) != "RJ":
                continue
            municipality_code = self._first(
                unit,
                "codigoIbge",
                "codigoMunicipioIbge",
                "municipioCodigoIbge",
            )
            units.append(
                {
                    "codigoUnidadeAdministrativa": str(code),
                    "ufSigla": "RJ",
                    **(
                        {"codigoIbge": str(municipality_code)}
                        if municipality_code not in (None, "")
                        else {}
                    ),
                }
            )
        verifications[cnpj] = {"status": "verificado", "unidades_rj": units}
        return units

    @staticmethod
    def _summarize_unit_verification(
        discovery_state: dict[str, Any],
        organizations: list[str],
        verifications: dict[str, Any],
        observed_by_cnpj: dict[str, Any],
        result: list[dict[str, str]],
    ) -> None:
        discovery_state["verified_unit_scopes"] = result
        discovery_state["unidades_rj_catalogadas"] = sum(
            len((verification.get("unidades_rj") or []))
            for verification in verifications.values()
            if isinstance(verification, dict) and verification.get("status") == "verificado"
        )
        discovery_state["unidades_observadas_confirmadas"] = sum(
            scope["codigoUnidadeAdministrativa"] in (observed_by_cnpj.get(scope["cnpj"]) or {})
            for scope in result
        )
        discovery_state["organizacoes_verificadas"] = sum(
            1
            for cnpj in organizations
            if isinstance(verifications.get(cnpj), dict)
            and verifications[cnpj].get("status") == "verificado"
        )
        discovery_state["unidades_rj_verificadas"] = discovery_state[
            "unidades_rj_catalogadas"
        ]
        discovery_state["verificacao_completa"] = discovery_state["organizacoes_verificadas"] == len(
            organizations
        )

    def validate_configuration(self, query: dict[str, Any]) -> None:
        """Fail closed on an incomplete RJ CNPJ roster before resuming or collecting a census."""
        resource_type = str(query.get("resource_type") or "").casefold()
        params = dict(query.get("params") or {})
        if resource_type in {"contrato_assinado", "contrato_pncp"}:
            if not query.get("censo_nacional"):
                self._load_contract_cnpjs(query, params)
        elif resource_type in {"pca", "pca_agregado"} and query.get("exigir_cadastro_completo"):
            self._load_contract_cnpjs(query, params)

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

    def resolve(self, source_ref: SourceRef) -> ResolvedSource:
        if source_ref.provider.casefold() != self.name:
            raise ValueError(f"SourceRef pertence a {source_ref.provider!r}, não a pncp")

        resource_type = source_ref.resource_type.casefold()
        if resource_type in {"contrato_assinado", "contrato_pncp"}:
            artifacts = self._contract_artifacts(source_ref)
        elif resource_type in {"contratacao", "contratação", "contrato"}:
            artifacts = self._purchase_artifacts(source_ref)
        elif resource_type in {"ata", "ata_registro_preco"}:
            artifacts = self._ata_artifacts(source_ref)
        elif resource_type in {"pca", "pca_agregado"}:
            artifacts = self._pca_artifacts(source_ref)
        else:
            raise ValueError(f"resource_type PNCP não suportado: {source_ref.resource_type}")

        return ResolvedSource(source_ref=source_ref, artifacts=artifacts)

    @staticmethod
    def _artifact(
        source_ref: SourceRef,
        suffix: str,
        url: str,
        filename: str,
        metadata: dict[str, Any],
        *,
        artifact_type: str = "json",
        content_type: str = "application/json",
    ) -> SourceArtifact:
        return SourceArtifact(
            artifact_id=f"{source_ref.stable_id}:{suffix}",
            source_ref=source_ref,
            url=url,
            artifact_type=artifact_type,
            content_type=content_type,
            filename=filename,
            metadata=metadata,
        )

    @staticmethod
    def _paginated_items(purchase_control: str) -> dict[str, Any]:
        """Metadados dos itens da compra: paginados de 100 em 100, com o Compras.gov.br como alternativa."""
        return {
            "logical_unit": False,
            "resource_kind": "itens",
            "params": {"pagina": 1, "tamanhoPagina": 100},
            "paginacao": {"campo": "pagina", "tamanho": 100},
            "comprasgov_fallback": {
                "resource": "itens",
                "numeroControlePNCPCompra": purchase_control,
            },
        }

    def _contract_artifacts(self, source_ref: SourceRef) -> tuple[SourceArtifact, ...]:
        """Detalhe e arquivos do contrato; com a compra vinculada, também ela, as atas e os itens."""
        cnpj, year, sequence = self._key(source_ref, "cnpj", "ano", "sequencial")
        base = f"{self.client.api_base}/v1/orgaos/{cnpj}/contratos/{year}/{sequence}"
        artifacts = (
            self._artifact(source_ref, "detalhe", base, "contrato_detalhe.json",
                           {"logical_unit": True, "resource_kind": "contrato"}),
            self._artifact(source_ref, "arquivos", base + "/arquivos", "contrato_arquivos.json",
                           {"logical_unit": False, "resource_kind": "documentos_contrato"}),
        )
        linked_purchase = source_ref.key.get("numeroControlePNCPCompra")
        if not linked_purchase:
            return artifacts
        purchase = self.purchase_ref({"numeroControlePNCP": linked_purchase})
        if purchase is None:
            return artifacts
        purchase_base = self.client.api_base + self._purchase_path(purchase)
        return artifacts + (
            self._artifact(
                source_ref,
                "contratacao_vinculada",
                self.client.consulta_base + self._purchase_path(purchase),
                "contratacao_vinculada.json",
                {
                    "logical_unit": False,
                    "resource_kind": "contratacao_vinculada",
                    "comprasgov_fallback": {
                        "resource": "contratacao",
                        "numeroControlePNCPCompra": linked_purchase,
                    },
                },
            ),
            self._artifact(source_ref, "atas_compra", purchase_base + "/atas", "atas_compra.json",
                           {"logical_unit": False, "resource_kind": "atas_compra"}),
            self._artifact(source_ref, "itens_vinculados", purchase_base + "/itens", "itens_vinculados.json",
                           self._paginated_items(linked_purchase)),
        )

    def _purchase_artifacts(self, source_ref: SourceRef) -> tuple[SourceArtifact, ...]:
        """Contratação, itens, arquivos do edital e atas da compra."""
        api_base = self.client.api_base + self._purchase_path(source_ref)
        return (
            self._artifact(source_ref, "metadata", self.client.consulta_base + self._purchase_path(source_ref),
                           "contratacao.json", {"logical_unit": False, "resource_kind": "contratacao_detalhe"}),
            self._artifact(source_ref, "itens", api_base + "/itens", "itens.json",
                           self._paginated_items(self._purchase_control_number(source_ref))),
            self._artifact(source_ref, "arquivos_edital", api_base + "/arquivos", "edital_arquivos.json",
                           {"logical_unit": False, "resource_kind": "documentos_edital"}),
            self._artifact(source_ref, "atas_compra", api_base + "/atas", "atas_compra.json",
                           {"logical_unit": False, "resource_kind": "atas_compra"}),
        )

    def _ata_artifacts(self, source_ref: SourceRef) -> tuple[SourceArtifact, ...]:
        """Ata, arquivos e contratos vinculados a ela."""
        base = self.client.api_base + self._ata_path(source_ref)
        return (
            self._artifact(source_ref, "metadata", base, "ata.json", {"logical_unit": True, "resource_kind": "ata"}),
            self._artifact(source_ref, "arquivos", base + "/arquivos", "ata_arquivos.json",
                           {"logical_unit": False, "resource_kind": "documentos_ata"}),
            self._artifact(
                source_ref,
                "contratos_vinculados",
                base + "/contratos",
                "ata_contratos_vinculados.json",
                {
                    "logical_unit": False,
                    "resource_kind": "contratos_vinculados_ata",
                    "params": {"pagina": 1, "tamanhoPagina": 100},
                    "paginacao": {"campo": "pagina", "tamanho": 100},
                },
            ),
        )

    def _pca_artifacts(self, source_ref: SourceRef) -> tuple[SourceArtifact, ...]:
        """PCA consolidado do órgão e a exportação em CSV."""
        cnpj, year, sequence = self._pca_parts(source_ref)
        base = f"{self.client.api_base}/v1/orgaos/{cnpj}/pca/{year}"
        return (
            self._artifact(source_ref, "consolidado", f"{base}/{sequence}/consolidado", "pca_consolidado.json",
                           {"logical_unit": True, "resource_kind": "pca_planejado"}),
            self._artifact(source_ref, "csv", f"{base}/csv", "pca.csv",
                           {"logical_unit": False, "resource_kind": "pca_exportacao"},
                           artifact_type="csv", content_type="text/csv"),
        )

    def list_artifacts(self, source: ResolvedSource) -> tuple[SourceArtifact, ...]:
        """Para atas, acrescenta os PDFs anunciados na listagem ``/arquivos``.

        A listagem é consultada aqui só para descobrir as URLs; ela continua
        sendo coletada como artefato JSON próprio. Se a consulta falhar, a ata
        segue só com os artefatos JSON e a falha aparece na coleta da listagem.
        """
        if source.source_ref.resource_type.casefold() not in {"ata", "ata_registro_preco"}:
            return source.artifacts
        listing = next(
            (artifact for artifact in source.artifacts if artifact.artifact_id.endswith(":arquivos")),
            None,
        )
        if listing is None:
            return source.artifacts
        response = self.client.request_url(listing.url, "json", attempts=3, timeout=30.0)
        if response.get("status") not in {"disponivel", "nao_modificado"}:
            return source.artifacts
        documents: list[SourceArtifact] = []
        for document in self._records(response.get("json")):
            sequence = self._first(document, "sequencialDocumento")
            url = document.get("url")
            if sequence in (None, "") or not url:
                continue
            documents.append(
                SourceArtifact(
                    artifact_id=f"{source.source_ref.stable_id}:arquivo:{sequence}",
                    source_ref=source.source_ref,
                    url=str(url),
                    artifact_type="pdf",
                    content_type="application/pdf",
                    filename=f"ata_arquivo_{sequence}.pdf",
                    metadata={
                        "logical_unit": False,
                        "titulo": document.get("titulo"),
                        "tipo_documento": document.get("tipoDocumentoNome"),
                    },
                )
            )
        return source.artifacts + tuple(documents)

    def related_result_artifacts(
        self,
        source_ref: SourceRef,
        payload: Any,
    ) -> tuple[SourceArtifact, ...]:
        """Return one official bulk result endpoint for the linked purchase."""
        if source_ref.resource_type.casefold() == "contrato_assinado":
            control = source_ref.key.get("numeroControlePNCPCompra")
            purchase = self.purchase_ref({"numeroControlePNCP": control}) if control else None
        elif source_ref.resource_type.casefold() in {"contratacao", "contratação"}:
            purchase = source_ref
        else:
            purchase = None
        if purchase is None or not self._records(payload):
            return ()
        purchase_control = (
            source_ref.key.get("numeroControlePNCPCompra")
            or self._purchase_control_number(purchase)
        )
        return (
            SourceArtifact(
                artifact_id=f"{source_ref.stable_id}:resultados_compra",
                source_ref=source_ref,
                url=self._comprasgov_url("resultado_item", str(purchase_control)),
                artifact_type="json",
                content_type="application/json",
                filename="resultados_compra.json",
                metadata={
                    "logical_unit": False,
                    "resource_kind": "resultado_item",
                    "acquisition_backend": "compras_dados_abertos",
                    "consulta_em_lote": True,
                },
            ),
        )

    def related_ata_artifacts(
        self,
        source_ref: SourceRef,
        payload: Any,
    ) -> tuple[SourceArtifact, ...]:
        """Resolve ARPs listed by a purchase and include their official documents."""
        if source_ref.resource_type.casefold() == "contrato_assinado":
            control = source_ref.key.get("numeroControlePNCPCompra")
            purchase = self.purchase_ref({"numeroControlePNCP": control}) if control else None
        elif source_ref.resource_type.casefold() in {"contratacao", "contratação"}:
            purchase = source_ref
        else:
            purchase = None
        if purchase is None:
            return ()

        fallback = {
            "cnpj": purchase.key.get("cnpj"),
            "anoCompra": purchase.key.get("ano"),
            "sequencialCompra": purchase.key.get("sequencial"),
        }
        purchase_control = self._purchase_control_number(purchase)
        artifacts: dict[str, SourceArtifact] = {}
        for record in self._records(payload):
            candidate = self._ata_candidate({**fallback, **record})
            if candidate is None:
                continue
            ata_ref = SourceRef(
                provider=self.name,
                resource_type="ata",
                key={
                    **candidate.source_ref.key,
                    "numeroControlePNCPCompra": purchase_control,
                },
            )
            resolved = self.resolve(ata_ref)
            for artifact in self.list_artifacts(resolved):
                artifacts.setdefault(artifact.artifact_id, artifact)
        return tuple(artifacts[key] for key in sorted(artifacts))

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
