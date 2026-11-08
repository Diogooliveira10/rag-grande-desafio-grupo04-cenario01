"""Leitura do ``corpus_manifest.yaml`` com um subconjunto de YAML, sem dependência externa."""

from __future__ import annotations

import ast
from typing import Any




def _strip_yaml_comment(value: str) -> str:
    quoted = False
    quote = ""
    depth = 0
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quoted and char == quote:
                quoted = False
            elif not quoted:
                quoted = True
                quote = char
        elif not quoted and char in "[{":
            depth += 1
        elif not quoted and char in "]}":
            depth = max(0, depth - 1)
        elif char == "#" and not quoted and depth == 0 and (
            index == 0 or value[index - 1].isspace()
        ):
            return value[:index].rstrip()
    return value.rstrip()


def _split_yaml_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    start = 0
    quoted = False
    quote = ""
    depth = 0
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quoted and char == quote:
                quoted = False
            elif not quoted:
                quoted = True
                quote = char
        elif not quoted and char in "[{":
            depth += 1
        elif not quoted and char in "]}":
            depth -= 1
        elif char == "," and not quoted and depth == 0:
            tokens.append(value[start:index].strip())
            start = index + 1
    tokens.append(value[start:].strip())
    return [token for token in tokens if token]


def _split_yaml_mapping(value: str) -> tuple[str, str] | None:
    quoted = False
    quote = ""
    depth = 0
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quoted and char == quote:
                quoted = False
            elif not quoted:
                quoted = True
                quote = char
        elif not quoted and char in "[{":
            depth += 1
        elif not quoted and char in "]}":
            depth -= 1
        elif char == ":" and not quoted and depth == 0:
            if index + 1 == len(value) or value[index + 1].isspace():
                return value[:index].strip(), value[index + 1 :].strip()
    return None


def _parse_yaml_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        return [_parse_yaml_scalar(token) for token in _split_yaml_tokens(value[1:-1])]
    if value.startswith("{") and value.endswith("}"):
        result: dict[str, Any] = {}
        for token in _split_yaml_tokens(value[1:-1]):
            mapping = _split_yaml_mapping(token)
            if mapping is None:
                raise ValueError(f"mapa YAML inline inválido: {token}")
            key, raw = mapping
            result[str(_parse_yaml_scalar(key))] = _parse_yaml_scalar(raw)
        return result
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _parse_manifest_yaml(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indentation = len(raw_line) - len(raw_line.lstrip(" "))
        content = _strip_yaml_comment(raw_line[indentation:])
        if content:
            lines.append((indentation, content))

    def parse_block(position: int, indentation: int) -> tuple[Any, int]:
        if position >= len(lines) or lines[position][0] != indentation:
            return {}, position
        is_list = lines[position][1].startswith("-")
        result: Any = [] if is_list else {}
        while position < len(lines):
            current_indent, content = lines[position]
            if current_indent < indentation:
                break
            if current_indent != indentation:
                raise ValueError(f"indentação YAML inesperada na linha {position + 1}")
            if is_list:
                if not content.startswith("-"):
                    break
                item = content[1:].strip()
                position += 1
                if not item:
                    value, position = parse_block(position, lines[position][0]) if position < len(lines) and lines[position][0] > indentation else (None, position)
                    result.append(value)
                    continue
                mapping = _split_yaml_mapping(item)
                if mapping is None:
                    result.append(_parse_yaml_scalar(item))
                    continue
                key, raw_value = mapping
                item_mapping: dict[str, Any] = {}
                if raw_value:
                    item_mapping[key] = _parse_yaml_scalar(raw_value)
                elif position < len(lines) and lines[position][0] > indentation:
                    item_mapping[key], position = parse_block(position, lines[position][0])
                else:
                    item_mapping[key] = None
                if position < len(lines) and lines[position][0] > indentation:
                    extra, position = parse_block(position, lines[position][0])
                    if not isinstance(extra, dict):
                        raise ValueError(f"item YAML inválido na linha {position + 1}")
                    item_mapping.update(extra)
                result.append(item_mapping)
                continue
            if content.startswith("-"):
                break
            mapping = _split_yaml_mapping(content)
            if mapping is None:
                raise ValueError(f"linha YAML inválida: {content}")
            key, raw_value = mapping
            position += 1
            if raw_value:
                result[key] = _parse_yaml_scalar(raw_value)
            elif position < len(lines) and lines[position][0] > indentation:
                result[key], position = parse_block(position, lines[position][0])
            else:
                result[key] = None
        return result, position

    parsed, position = parse_block(0, lines[0][0] if lines else 0)
    if position != len(lines) or not isinstance(parsed, dict):
        raise ValueError("manifesto YAML inválido")
    return parsed
