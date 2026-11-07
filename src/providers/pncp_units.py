"""Verificação no PNCP: unidades descobertas do RJ e a configuração dos CNPJs de contratos."""

from __future__ import annotations

import time
from typing import Any

from .pncp_common import _PncpBase


class _PncpUnits(_PncpBase):
    """``verify_discovered_units`` e ``validate_configuration``."""

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
