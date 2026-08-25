"""How every agent-facing activity surface names a participant and an outcome.

Two pure rules, in one module, because several surfaces must agree on them
exactly: the ``[Recent room activity]`` context block, the observation aggregates
behind a computed presentation block ([R28.18]), and the repository that builds
the latter's rows. A code in a figure that does not match the code in the agent's
own context is worse than no code at all — it invites the reader to connect two
rows that are about different people — and an outcome called "invalid" in one
place and "failed" in another gives the model two vocabularies for one fact.

In ``domain`` because both application and infrastructure need them, and because
neither rule depends on anything but a value.

Truncation is the privacy property of :func:`subject_code`. The code identifies a
row *within one room's window*; it is not a handle anything can be looked up by,
and no display name or login email is ever resolved on either path.
"""

from __future__ import annotations

import uuid

from contexts.activities.domain.models import ValidationStatus

#: Characters of the UUID kept. Short enough to read down a column, long enough
#: that a room-sized set of participants does not collide in practice.
_CODE_CHARS = 8


def subject_code(subject_user_id: uuid.UUID) -> str:
    return f"u:{str(subject_user_id)[:_CODE_CHARS]}"


def group_subject_code(member_group_id: uuid.UUID) -> str:
    """The code for a Member Group subject ([R30.43]).

    A distinct prefix rather than a longer truncation of the same space: a group
    row is one submission by several people, and a reader that cannot tell it
    from a person's row will count it as a person. The two prefixes also mean the
    code spaces cannot collide even when a group id and a user id share their
    first eight characters.
    """
    return f"g:{str(member_group_id)[:_CODE_CHARS]}"


def outcome_word(status: ValidationStatus, is_valid: bool | None) -> str:
    """One of ``pending`` / ``error`` / ``valid`` / ``invalid``.

    ``PENDING`` and ``ERROR`` are about whether the verdict exists at all, so they
    are answered before ``is_valid``, which is ``None`` in both.
    """
    if status is ValidationStatus.PENDING:
        return "pending"
    if status is ValidationStatus.ERROR:
        return "error"
    return "valid" if is_valid else "invalid"


__all__ = ["group_subject_code", "outcome_word", "subject_code"]
