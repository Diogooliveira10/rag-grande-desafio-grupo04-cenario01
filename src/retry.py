"""Política compartilhada de atraso entre tentativas HTTP."""

from __future__ import annotations


def retry_delay(attempt: int, retry_after: str | None) -> float:
    """Calcula o atraso respeitando Retry-After e backoff exponencial."""
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 15.0)
        except ValueError:
            pass
    return min(float(2 ** (attempt - 1)), 8.0)
