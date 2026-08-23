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

# Format chars that legitimately appear *inside* an emoji grapheme. Kept so
# multi-codepoint emoji (ZWJ sequences like the family/profession emoji, and
# VS16-presented glyphs) survive normalisation; every other control/format char —
# newlines, tabs, and the bidi overrides used for display spoofing — is stripped.
_KEEP: Final = ("‍", "️")  # ZERO WIDTH JOINER, VARIATION SELECTOR-16

#: Account display names (``users.display_name``).
MAX_DISPLAY_NAME: Final = 50

#: Room guest labels (``chatroom_guests.display_name``), matching both the column
#: and the ``max_length`` on the enrolment request model.
MAX_GUEST_LABEL: Final = 100


def normalise_label(raw: str | None, *, max_len: int) -> str | None:
    """Trim and strip control/format characters; empty collapses to ``None``.

    Printable Unicode (incl. CJK and emoji) is preserved — this is user content,
    not project UI text. Length is capped defensively even where an API boundary
    also validates it, because not every writer comes through one.
    """
    if raw is None:
        return None
    cleaned = "".join(
        ch for ch in raw if ch in _KEEP or ch == " " or not unicodedata.category(ch).startswith("C")
    )
    cleaned = cleaned.strip()[:max_len].strip()
    return cleaned or None


__all__ = ["MAX_DISPLAY_NAME", "MAX_GUEST_LABEL", "normalise_label"]
