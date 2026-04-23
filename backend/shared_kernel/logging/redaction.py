"""Redaction filter for log records and arbitrary JSON-ish payloads.

Implements R17.03 / `docs/operations.md` §1.5 (O1.05):

  1. Any JSON key matching ``^(authorization|api[_-]?key|secret|password|token
     |bearer|private[_-]?key|cookie|session)$`` (case-insensitive) has its
     value replaced with ``"***"``.
  2. Known **secret shapes** found anywhere in string values are replaced:
       - Anthropic: ``sk-ant-…`` up to the next whitespace or quote
       - OpenAI-ish: ``sk-…`` of length >= 40
       - PEM blocks: ``-----BEGIN [A-Z ]+ PRIVATE KEY-----``
  3. Recursion depth and key-count caps prevent pathological structures from
     blowing CPU during logging.

Redaction is *in-place safe* — we never mutate input dicts; we return a copy.
"""

from __future__ import annotations

import re
from typing import Any, Final

_REDACT_KEY_RE: Final = re.compile(
    r"^(authorization|api[_-]?key|secret|password|token|bearer|private[_-]?key|cookie|session)$",
    re.IGNORECASE,
)

# Order matters — match most specific first.
_SECRET_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{40,}"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----"),
)

_MASK: Final = "***"
_MAX_DEPTH: Final = 8
_MAX_ITEMS: Final = 2048


def _mask_str(s: str) -> str:
    out = s
    for pat in _SECRET_VALUE_PATTERNS:
        out = pat.sub(_MASK, out)
    return out


def redact(value: Any, *, _depth: int = 0, _counter: list[int] | None = None) -> Any:
    """Return a defensively-copied, redacted version of ``value``."""
    if _counter is None:
        _counter = [0]
    if _depth > _MAX_DEPTH or _counter[0] > _MAX_ITEMS:
        return _MASK

    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            _counter[0] += 1
            if isinstance(k, str) and _REDACT_KEY_RE.match(k):
                out[k] = _MASK
            else:
                out[k] = redact(v, _depth=_depth + 1, _counter=_counter)
        return out

    if isinstance(value, list):
        return [redact(v, _depth=_depth + 1, _counter=_counter) for v in value]

    if isinstance(value, tuple):
        return tuple(redact(v, _depth=_depth + 1, _counter=_counter) for v in value)

    if isinstance(value, str):
        return _mask_str(value)

    return value
