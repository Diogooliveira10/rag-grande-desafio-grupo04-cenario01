"""Modelos estáveis para descoberta, curadoria e coleta de fontes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SourceRef:
    """Identidade lógica de um recurso; URL é apenas uma resolução posterior."""

    provider: str
    resource_type: str
    key: dict[str, Any]

    @property
    def stable_id(self) -> str:
        key = ":".join(f"{name}={self.key[name]}" for name in sorted(self.key))
        return f"{self.provider}:{self.resource_type}:{key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "resource_type": self.resource_type,
            "key": dict(self.key),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SourceRef":
        required = ("provider", "resource_type", "key")
        missing = [name for name in required if not value.get(name)]
        if missing:
            raise ValueError(f"SourceRef incompleto; campos ausentes: {', '.join(missing)}")
        if not isinstance(value["key"], dict):
            raise ValueError("SourceRef.key deve ser um objeto")
        return cls(
            provider=str(value["provider"]),
            resource_type=str(value["resource_type"]),
            key=dict(value["key"]),
        )


@dataclass(frozen=True)
class SourceArtifact:
    """Arquivo ou endpoint materializável pertencente a uma referência."""

    artifact_id: str
    source_ref: SourceRef
    url: str
    artifact_type: str
    content_type: str | None = None
    filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "source": self.source_ref.to_dict(),
            "url": self.url,
            "artifact_type": self.artifact_type,
            "content_type": self.content_type,
            "filename": self.filename,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ResolvedSource:
    source_ref: SourceRef
    artifacts: tuple[SourceArtifact, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceCandidate:
    candidate_id: str
    source_ref: SourceRef
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[SourceArtifact, ...] = ()


class SourceProvider(Protocol):
    name: str

    def search(self, query: dict[str, Any]) -> list[SourceCandidate]:
        """Descobre candidatos sem gravar dados no corpus."""
        ...

    def resolve(self, source_ref: SourceRef) -> ResolvedSource:
        """Resolve uma referência canônica em artefatos coletáveis."""
        ...

    def list_artifacts(self, source: ResolvedSource) -> tuple[SourceArtifact, ...]:
        """Permite expandir relações sem alterar a identidade da fonte."""
        ...
