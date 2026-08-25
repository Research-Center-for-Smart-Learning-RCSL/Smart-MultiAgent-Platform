"""Shared chatroom row stand-in for unit tests that exercise the DTO seam.

`app.api.v1.chatrooms._to_out` reads a plain attribute bag, so the unit tier
can drive it without a session. Kept in one place so a new `ChatroomOut` field
does not have to be added to every test module's private copy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace


def chatroom_row(*, created_by=None, disclose=True, disclose_drafts=True) -> SimpleNamespace:
    """`disclose` is the observer flag; `disclose_drafts` is §32's ([R32.05]).

    Two parameters rather than one, because they are independent settings a room may
    hold in any combination, and a test that needs one off must be able to say so
    without moving the other.
    """
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="room",
        allow_org_members=False,
        allow_project_members=True,
        allow_project_owners_only=False,
        allow_guest_links=True,
        allow_member_groups=False,
        version=1,
        created_at=now,
        deleted_at=None,
        created_by_user_id=created_by,
        disclose_observers=disclose,
        disclose_drafts=disclose_drafts,
    )
