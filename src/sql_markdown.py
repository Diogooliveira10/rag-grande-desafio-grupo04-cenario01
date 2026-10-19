"""Deterministic GFM tables. Source values are literal text, never Markdown/HTML."""
from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Any


def texto_markdown(value: Any) -> str:
    text = "não informado" if value is None else str(value)
    text = html.escape(" ".join(text.split()), quote=False)
    return re.sub(r"([\\`*_\[\]()|~])", r"\\\1", text)


def tabela_markdown(headers: Sequence[str], rows: Sequence[Sequence[Any]], *, numericas: Sequence[int] = ()) -> str:
    """Keep whole rows; GFM alignment is presentation, not a numeric calculation."""
    if not headers or not rows:
        return ""
    if any(len(row) != len(headers) for row in rows):
        raise ValueError("Tabela Markdown exige uma célula por coluna")
    def line(cells: Sequence[Any]) -> str:
        return "| " + " | ".join(texto_markdown(cell) for cell in cells) + " |"
    separator = "| " + " | ".join("---:" if i in numericas else "---" for i in range(len(headers))) + " |"
    return "\n".join([line(headers), separator, *(line(row) for row in rows)])
