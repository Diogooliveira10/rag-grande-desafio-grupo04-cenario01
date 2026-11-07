"""Resolução no PNCP: de uma referência aos artefatos (PDFs, itens, resultados e atas relacionadas)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .base import ResolvedSource, SourceArtifact, SourceRef
from .pncp_common import _PncpBase


class _PncpArtifacts(_PncpBase):
    """``resolve`` e ``list_artifacts``: o que baixar de cada referência."""

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
