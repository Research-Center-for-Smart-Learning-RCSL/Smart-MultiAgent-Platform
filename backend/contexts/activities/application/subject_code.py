"""The truncated participant code every agent-facing activity surface uses.

One function, in its own module, because two surfaces must agree on it exactly:
the ``[Recent room activity]`` context block and the observation aggregates behind
a computed presentation block ([R28.18]). A code in a figure that does not match
the code in the agent's own context is worse than no code at all — it invites the
reader to connect two rows that are about different people.

Truncation is the privacy property. The code identifies a row *within one room's
window*; it is not a handle anything can be looked up by, and no display name or
login email is ever resolved on either path.
"""

from __future__ import annotations

import uuid

#: Characters of the UUID kept. Short enough to read down a column, long enough
#: that a room-sized set of participants does not collide in practice.
_CODE_CHARS = 8


def subject_code(subject_user_id: uuid.UUID) -> str:
    return f"u:{str(subject_user_id)[:_CODE_CHARS]}"


__all__ = ["subject_code"]
