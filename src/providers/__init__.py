"""Providers compartilhados pela curadoria e pela coleta do corpus."""

from .base import ResolvedSource, SourceArtifact, SourceCandidate, SourceProvider, SourceRef
from .pncp import PNCPProvider


_PROVIDERS: dict[str, SourceProvider] = {
    "pncp": PNCPProvider(),
}


def get_provider(name: str) -> SourceProvider:
    """Retorna um provider registrado sem acoplar a CLI aos endpoints."""
    key = name.strip().casefold()
    try:
        return _PROVIDERS[key]
    except KeyError as exc:
        supported = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"provider não suportado: {name!r}; disponíveis: {supported}") from exc


__all__ = [
    "PNCPProvider",
    "ResolvedSource",
    "SourceArtifact",
    "SourceCandidate",
    "SourceProvider",
    "SourceRef",
    "get_provider",
]
