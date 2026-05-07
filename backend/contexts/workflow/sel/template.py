"""SEL v1 template interpolation (§3.4).

Simple {{ var.path }} interpolation with the same var_ref grammar.
No control flow in templates. Unresolved vars render as the literal
{{ var.path }}.
"""

from __future__ import annotations

import re
from typing import Any

_VAR_RE = re.compile(
    r"\{\{\s*"
    r"([a-zA-Z_][a-zA-Z0-9_]*"
    r"(?:\.[a-zA-Z_][a-zA-Z0-9_]*|\[\d+\]|\[\"[^\"]*\"\]|\['[^']*'\])*)"
    r"\s*\}\}",
)


def _parse_path(path_str: str) -> list[str | int]:
    """Parse a dotted/bracket path string into segments."""
    segments: list[str | int] = []
    i = 0
    n = len(path_str)
    while i < n:
        if path_str[i] == "[":
            end = path_str.index("]", i)
            inner = path_str[i + 1 : end]
            if inner.isdigit():
                segments.append(int(inner))
            else:
                segments.append(inner.strip("'\""))
            i = end + 1
            if i < n and path_str[i] == ".":
                i += 1
        elif path_str[i] == ".":
            i += 1
        else:
            dot = path_str.find(".", i)
            bracket = path_str.find("[", i)
            if dot == -1 and bracket == -1:
                end_idx = n
            elif dot == -1:
                end_idx = bracket
            elif bracket == -1:
                end_idx = dot
            else:
                end_idx = min(dot, bracket)
            segments.append(path_str[i:end_idx])
            i = end_idx
    return segments


def _resolve(segments: list[str | int], variables: dict[str, Any]) -> Any:
    """Resolve path segments against variable scopes. Returns _UNRESOLVED sentinel on miss."""
    if not segments:
        return _UNRESOLVED

    first = segments[0]
    if first == "trigger":
        obj: Any = variables.get("__trigger__", {})
        rest = segments[1:]
    elif first == "ctx":
        obj = variables.get("__ctx__", {})
        rest = segments[1:]
    else:
        obj = variables
        rest = segments

    for seg in rest:
        if obj is None:
            return _UNRESOLVED
        if isinstance(seg, int):
            if isinstance(obj, list | tuple) and 0 <= seg < len(obj):
                obj = obj[seg]
            else:
                return _UNRESOLVED
        elif isinstance(obj, dict):
            if seg not in obj:
                return _UNRESOLVED
            obj = obj[seg]
        else:
            return _UNRESOLVED
    return obj


class _Unresolved:
    """Sentinel for unresolved template variables."""


_UNRESOLVED = _Unresolved()


def _format_value(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def interpolate(template: str, variables: dict[str, Any] | None = None) -> str:
    """Replace {{ var.path }} placeholders in a template string.

    Unresolved vars render as the literal {{ var.path }} (§3.4).
    """
    vars_ = variables or {}

    def _replace(match: re.Match[str]) -> str:
        path_str = match.group(1)
        segments = _parse_path(path_str)
        val = _resolve(segments, vars_)
        if isinstance(val, _Unresolved):
            return match.group(0)
        return _format_value(val)

    return _VAR_RE.sub(_replace, template)
