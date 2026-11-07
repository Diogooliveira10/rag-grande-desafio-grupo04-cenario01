"""Base do provedor PNCP: cliente, chaves das referências, caminhos da API e leitura de registros.

As três partes do provedor (descoberta, artefatos e unidades) herdam daqui o que usam em comum."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..pncp_client import PncpClient
from ..collection_policy import uf_unidade
from .base import SourceCandidate, SourceRef


class _PncpBase:
    """Cliente PNCP e os auxiliares comuns às partes do provedor."""

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
                records = _PncpBase._records(payload.get(key))
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
        value = _PncpBase._first(
            record,
            "objetoCompra",
            "objetoContratacao",
            "objeto",
            "descricaoObjeto",
            "nomeUnidade",
            "numeroAtaRegistroPreco",
        )
        return str(value or default)

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
    def _unit_uf(record: dict[str, Any]) -> str:
        return uf_unidade(record)

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
