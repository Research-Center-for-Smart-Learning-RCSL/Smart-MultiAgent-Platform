"""Coarse token estimation shared across contexts.

A deliberately cheap heuristic (no tokenizer dependency): CJK characters count
as one token each, everything else as ``len // 4``. Used by the agents-runtime
context manager to size a turn's payload and by the knowledge providers to trim
a retrieved block to a token budget — both must agree on the estimate, so the
single definition lives here in the shared kernel. Any budget derived from it
carries a safety margin because the heuristic under- and over-counts.
"""

from __future__ import annotations

__all__ = ["estimate_tokens"]


def estimate_tokens(text: str) -> int:
    """Coarse token estimate; CJK characters count as 1 token each, Latin as
    ``len // 4`` (M10). Messages carry no token column."""
    cjk = 0
    latin = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0x3040 <= cp <= 0x30FF
            or 0xAC00 <= cp <= 0xD7AF
            or 0xFF00 <= cp <= 0xFFEF
        ):
            cjk += 1
        else:
            latin += 1
    return max(1, cjk + latin // 4)
