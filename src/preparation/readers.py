"""Leitura dos formatos de arquivo: texto, HTML visível, JSON e CSV."""

from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator


class _VisibleTextParser(HTMLParser):
    """Extrai texto visível de HTML sem introduzir outra dependência."""

    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned:
                self.parts.append(cleaned)

    def text(self) -> str:
        return "\n".join(self.parts)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _visible_html(text: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(text)
    return parser.text()


def _json_records(payload: Any, *, unwrap_resultado: bool = False) -> list[tuple[int | None, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return list(enumerate(payload["data"], start=1))
    if unwrap_resultado and isinstance(payload, dict) and isinstance(payload.get("resultado"), list):
        return list(enumerate(payload["resultado"], start=1))
    if isinstance(payload, list):
        return list(enumerate(payload, start=1))
    return [(None, payload)]


def _csv_records(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    for record_number, record in enumerate(reader, start=1):
        if any(value not in (None, "") for value in record.values()):
            yield record_number, {str(key): str(value or "") for key, value in record.items() if key}
