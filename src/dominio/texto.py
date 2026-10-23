"""Normalização de texto usada nas comparações por regra (sem acento, minúsculas)."""

from __future__ import annotations

import unicodedata


def sem_acento(texto: str) -> str:
    """casefold e sem acentos, preservando os espaços."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto or "").casefold())
                   if not unicodedata.combining(c))


def normalizar(texto: str) -> str:
    """casefold + sem acentos + espaços únicos (mesma regra do router e do query_analysis)."""
    return " ".join(sem_acento(texto).split())
