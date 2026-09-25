"""Resolução de referências PNCP sem acoplar a CLI ou o coletor a URLs."""

from __future__ import annotations

import re
import time
from typing import Any

from ..pncp_client import PncpClient
from .base import ResolvedSource, SourceArtifact, SourceCandidate, SourceRef


class PNCPProvider:
    name = "pncp"

    def __init__(self, client: PncpClient | None = None) -> None:
        self.client = client or PncpClient()

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
            for key in ("data", "items", "content", "results", "resultado", "resultados"):
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

    def _search_publication(self, query: dict[str, Any], target: int) -> list[SourceCandidate]:
        params = dict(query.get("params") or {})
        year = int(params.get("ano") or query.get("ano") or 2025)
        page_size = max(10, min(int(params.get("tamanhoPagina") or 50), 100))
        max_pages = max(1, int(params.get("maxPaginas") or ((target + page_size - 1) // page_size + 2)))
        modalities = params.get("modalidades") or params.get("codigoModalidadeContratacao") or [1]
        if isinstance(modalities, (str, int)):
            modalities = [modalities]
        candidates: list[SourceCandidate] = []
        for modality in modalities:
            request_params = {
                "dataInicial": self._api_date(params.get("dataInicial") or f"{year}-01-01"),
                "dataFinal": self._api_date(params.get("dataFinal") or f"{year}-12-31"),
                "codigoModalidadeContratacao": modality,
                "uf": params.get("uf") or "RJ",
                "tamanhoPagina": page_size,
            }
            for page in range(1, max_pages + 1):
                try:
                    records = self._request_records(
                        "/v1/contratacoes/publicacao",
                        {**request_params, "pagina": page},
                    )
                except RuntimeError:
                    break
                if not records:
                    break
                for record in records:
                    source_ref = self.purchase_ref(record)
                    if source_ref is None:
                        continue
                    candidates.append(
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
                if len(records) < page_size:
                    break
        return candidates

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
        cnpjs = params.get("cnpjs") or query.get("cnpjs") or params.get("cnpj") or []
        if isinstance(cnpjs, str):
            cnpjs = [cnpjs]
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
                    continue
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
        if resource_type in {"contratacao", "contratação", "contrato"}:
            base = self.client.consulta_base + self._purchase_path(source_ref)
            artifacts = (
                SourceArtifact(
                    artifact_id=f"{source_ref.stable_id}:metadata",
                    source_ref=source_ref,
                    url=base,
                    artifact_type="json",
                    content_type="application/json",
                    filename="contratacao.json",
                    metadata={"logical_unit": True},
                ),
            )
        elif resource_type in {"ata", "ata_registro_preco"}:
            base = self.client.api_base + self._ata_path(source_ref)
            artifacts = (
                SourceArtifact(
                    artifact_id=f"{source_ref.stable_id}:metadata",
                    source_ref=source_ref,
                    url=base,
                    artifact_type="json",
                    content_type="application/json",
                    filename="ata.json",
                    metadata={"logical_unit": True},
                ),
                SourceArtifact(
                    artifact_id=f"{source_ref.stable_id}:arquivos",
                    source_ref=source_ref,
                    url=base + "/arquivos",
                    artifact_type="json",
                    content_type="application/json",
                    filename="ata_arquivos.json",
                    metadata={"logical_unit": False},
                ),
            )
        elif resource_type in {"pca", "pca_agregado"}:
            cnpj, year, sequence = self._pca_parts(source_ref)
            base = f"{self.client.api_base}/v1/orgaos/{cnpj}/pca/{year}"
            artifacts = (
                SourceArtifact(
                    artifact_id=f"{source_ref.stable_id}:consolidado",
                    source_ref=source_ref,
                    url=f"{base}/{sequence}/consolidado",
                    artifact_type="json",
                    content_type="application/json",
                    filename="pca_consolidado.json",
                    metadata={"logical_unit": True},
                ),
                SourceArtifact(
                    artifact_id=f"{source_ref.stable_id}:csv",
                    source_ref=source_ref,
                    url=f"{base}/csv",
                    artifact_type="csv",
                    content_type="text/csv",
                    filename="pca.csv",
                    metadata={"logical_unit": False},
                ),
            )
        else:
            raise ValueError(f"resource_type PNCP não suportado: {source_ref.resource_type}")

        return ResolvedSource(source_ref=source_ref, artifacts=artifacts)

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

    def search(self, query: dict[str, Any]) -> list[SourceCandidate]:
        """Descobre referências PNCP sem gravar arquivos."""
        resource_type = str(query.get("resource_type") or "").casefold()
        target = max(1, int(query.get("volume_alvo") or query.get("limite") or 1))
        if resource_type in {"contratacao", "contratação", "contrato"}:
            candidates = self._search_publication(query, target)
        elif resource_type in {"ata", "ata_registro_preco"}:
            candidates = self._search_atas(query, target)
        elif resource_type in {"pca", "pca_agregado"}:
            candidates = self._search_pca(query, target)
        else:
            raise ValueError(f"resource_type PNCP não suportado: {resource_type or '<vazio>'}")

        unique = {candidate.candidate_id: candidate for candidate in candidates}
        return [unique[key] for key in sorted(unique)[:target]]
