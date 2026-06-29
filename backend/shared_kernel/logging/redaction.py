"""Redaction filter for log records and arbitrary JSON-ish payloads.

Implements R17.03 / `docs/operations.md` §1.5 (O1.05):

  1. Any JSON key *containing* a sensitive term (authorization, api[_-]?key,
     secret, password, token, bearer, private[_-]?key, cookie, session,
     credential — case-insensitive, substring so ``access_token`` / ``x-api-key``
     / ``proxy-authorization`` match) has its value replaced with ``"***"``.
  2. Known **secret shapes** found anywhere in string values are replaced:
       - Anthropic ``sk-ant-…``, OpenAI-ish ``sk-…``, Google ``AIza…``,
         Voyage ``pa-…``, Vault ``hvs.…`` / ``s.…`` tokens
       - PEM blocks: ``-----BEGIN [A-Z ]+ PRIVATE KEY-----``
  3. Recursion depth and key-count caps prevent pathological structures from
     blowing CPU during logging.

Redaction is *in-place safe* — we never mutate input dicts; we return a copy.
"""

from __future__ import annotations

import re
from typing import Any, Final

# Substring (not anchored) so compound names — access_token, refresh_token,
# client_secret, x-api-key, proxy-authorization, private-key — are caught too.
# Errs toward over-redaction in logs (e.g. token_count) rather than leaking a
# secret-bearing field.
_REDACT_KEY_RE: Final = re.compile(
    r"(authorization|api[_-]?key|secret|password|token|bearer|private[_-]?key|cookie|session|credential)",
    re.IGNORECASE,
)

# Order matters — match most specific first. Covers every provider SMAP accepts
# plus Vault tokens, so a key leaking into a free-text log value (exception
# string, URL) is masked regardless of the surrounding key name.
_SECRET_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),  # Anthropic
    re.compile(r"sk-[A-Za-z0-9_\-]{40,}"),  # OpenAI-ish
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google / Gemini
    re.compile(r"pa-[A-Za-z0-9_\-]{20,}"),  # Voyage
    re.compile(r"hvs\.[A-Za-z0-9._\-]{20,}"),  # Vault service token (1.10+)
    re.compile(r"\bs\.[A-Za-z0-9]{24,}"),  # Vault legacy token
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----"),
)

_MASK: Final = "***"
_MAX_DEPTH: Final = 8
_MAX_ITEMS: Final = 2048


def mask_str(s: str) -> str:
    """Mask every known secret shape in a free-text string (public helper)."""
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
            if isinstance(k, str) and _REDACT_KEY_RE.search(k):
                out[k] = _MASK
            else:
                out[k] = redact(v, _depth=_depth + 1, _counter=_counter)
        return out

    if isinstance(value, list):
        return [redact(v, _depth=_depth + 1, _counter=_counter) for v in value]

    if isinstance(value, tuple):
        return tuple(redact(v, _depth=_depth + 1, _counter=_counter) for v in value)

    if isinstance(value, str):
        return mask_str(value)

    return value
