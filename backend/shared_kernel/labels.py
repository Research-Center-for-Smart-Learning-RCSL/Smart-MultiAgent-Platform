"""Normalisation for the human labels that reach a chat transcript or a prompt.

A display name is user content and is rendered into line-structured text: the
``"Name: message"`` prefix the turn engine puts on every participant turn, and —
since the room-owner note and the activity legend landed — the agent's system
prompt itself. A label carrying a newline therefore does not merely look untidy,
it opens a line of its own inside a structure the reader treats as trustworthy.
Bidi overrides are the same problem spelled visually: they let a name render as
something other than what it is.

This lives in ``shared_kernel`` because two contexts write labels into the same
sink and only one of them used to guard it. Identity normalised account display
names (``AuthService.set_display_name``, ``AdminService``); conversation stored a
room guest's self-chosen label verbatim (``GuestService.enroll``), and the guest
label *wins* the precedence in ``TurnEngine._room_user_labels``. One helper, so
the guarantee cannot hold on one path and not the other.

The render sites still collapse whitespace of their own. That is not redundancy:
rows already written before this guard existed are still in the database, and a
label's safety at the point it is rendered must not depend on every historical
write path having been correct.
"""

from __future__ import annotations

import unicodedata
from typing import Final

import regex

# Unicode extended grapheme clusters (UAX #29); the stdlib `re` has no `\X`.
_GRAPHEME: Final = regex.compile(r"\X")

# Format chars that legitimately appear *inside* an emoji grapheme. Kept so
# multi-codepoint emoji (ZWJ sequences like the family/profession emoji, and
# VS16-presented glyphs) survive normalisation; every other control/format char —
# newlines, tabs, and the bidi overrides used for display spoofing — is stripped.
#
# Written as escapes, not as the codepoints themselves. They are invisible: an
# editor's "strip invisible characters", a paste through a normalising tool or a
# git filter would silently turn this tuple into ``("", "")``, after which every
# ZWJ emoji sequence and every VS16 glyph is quietly mangled — and nothing runs
# differently, so no test, lint or type check would notice.
_KEEP: Final = ("\u200d", "\ufe0f")  # ZERO WIDTH JOINER, VARIATION SELECTOR-16

# Code points that render as nothing by definition (the Hangul fillers among
# them). Outside category C, so the strip above missed them, and a name of only
# these read to everyone else as an author with no name.
_IGNORABLE: Final = regex.compile(r"\p{Default_Ignorable_Code_Point}")

# Draws an empty cell, but is a symbol rather than default-ignorable, so it is
# kept inside a name and only does not count as the visible part of one.
_BLANK_GLYPH: Final = "\u2800"  # BRAILLE PATTERN BLANK

#: Account display names (``users.display_name``).
MAX_DISPLAY_NAME: Final = 50

#: Room guest labels (``chatroom_guests.display_name``), matching both the column
#: and the ``max_length`` on the enrolment request model.
MAX_GUEST_LABEL: Final = 100


def normalise_label(raw: str | None, *, max_len: int) -> str | None:
    """Trim and strip control, format and default-ignorable characters; a name
    with no visible character collapses to ``None``.

    Printable Unicode (incl. CJK and emoji) is preserved — this is user content,
    not project UI text. Length is capped defensively even where an API boundary
    also validates it, because not every writer comes through one.
    """
    if raw is None:
        return None
    cleaned = "".join(
        ch
        for ch in raw
        if ch in _KEEP or ch == " " or not (unicodedata.category(ch).startswith("C") or _IGNORABLE.match(ch))
    )
    cleaned = _truncate_graphemes(cleaned.strip(), max_len).strip()
    return cleaned if has_visible_character(cleaned) else None


def has_visible_character(text: str) -> bool:
    """Whether some character draws on its own: joiners, selectors and combining
    marks only modify a neighbour, and whitespace and the blank cell draw nothing."""
    return any(
        not ch.isspace()
        and ch not in _KEEP
        and ch != _BLANK_GLYPH
        and not unicodedata.category(ch).startswith("M")
        for ch in text
    )


def _truncate_graphemes(text: str, max_len: int) -> str:
    """At most ``max_len`` code points, cut only between grapheme clusters.

    The cap counts code points because the ``VARCHAR(n)`` columns do; slicing at
    that count could leave a dangling ZWJ, half a flag, or a letter without its
    combining accent.

    A first cluster that alone exceeds the cap (a letter stacked with dozens of
    combining marks) keeps its first code point, a cluster of its own, rather than
    collapsing the whole label to nothing.
    """
    if len(text) <= max_len:
        return text
    kept = 0
    for match in _GRAPHEME.finditer(text):
        size = match.end() - match.start()
        if kept + size > max_len:
            break
        kept += size
    return text[: kept or 1]


__all__ = ["MAX_DISPLAY_NAME", "MAX_GUEST_LABEL", "has_visible_character", "normalise_label"]
