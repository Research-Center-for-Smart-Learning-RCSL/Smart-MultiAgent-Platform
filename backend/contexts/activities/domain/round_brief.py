"""The round brief of an activation ([R30.21]).

Class-visible plain text that a delegated agent or the room creator sets when a
round starts, such as the three clue characters of a word puzzle. It is shown on
every participant's activity panel and quoted in agent context, so it is bounded
here once for both paths that start a round.
"""

from __future__ import annotations

import unicodedata
from typing import Final

from contexts.activities.domain.errors import RoundBriefInvalid

MAX_BRIEF_CHARS: Final = 200
MAX_BRIEF_LINES: Final = 3


def normalize_round_brief(raw: str | None) -> str | None:
    """The stored form of ``raw``: trimmed, ``\\n`` line breaks, or ``None`` when blank.

    Refuses rather than strips a control or format character: a brief is short
    enough that silently changing it would show participants text nobody wrote.
    """
    if raw is None:
        return None
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return None
    if len(lines) > MAX_BRIEF_LINES:
        raise RoundBriefInvalid(f"a round brief has at most {MAX_BRIEF_LINES} lines")
    brief = "\n".join(lines)
    if len(brief) > MAX_BRIEF_CHARS:
        raise RoundBriefInvalid(f"a round brief has at most {MAX_BRIEF_CHARS} characters")
    for ch in brief:
        if ch != "\n" and unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"}:
            raise RoundBriefInvalid("a round brief cannot contain control characters")
    return brief


__all__ = ["MAX_BRIEF_CHARS", "MAX_BRIEF_LINES", "normalize_round_brief"]
