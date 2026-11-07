"""Resolução de referências PNCP sem acoplar a CLI ou o coletor a URLs.

Fachada: ``PNCPProvider`` junta as três partes do provedor, cada uma com uma
responsabilidade e um módulo próprio (2026-11-07):

- ``pncp_discovery``: ``search`` (publicações, contratos, atas e PCA);
- ``pncp_artifacts``: ``resolve``, ``list_artifacts`` e artefatos relacionados;
- ``pncp_units``: verificação das unidades descobertas e da configuração.

O que as três usam em comum (cliente, chaves, caminhos, leitura de registros)
fica em ``pncp_common``.
"""

from __future__ import annotations

from .pncp_artifacts import _PncpArtifacts
from .pncp_discovery import _PncpDiscovery
from .pncp_units import _PncpUnits


class PNCPProvider(_PncpDiscovery, _PncpArtifacts, _PncpUnits):
    name = "pncp"
