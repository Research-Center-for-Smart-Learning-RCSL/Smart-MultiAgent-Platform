"""A guest's orchestration reads for another room -- AC-5 (F-17), [R15.24].

Spec: ``docs/tasks/2026-10-05-guest-session-backend-hardening/spec.md`` §7.4, Q-7.

The guest branch of ``resolve_room_access`` raises ``ForbiddenInRoom`` for any
room but the one its token names, and ``_room_readable`` caught only the
not-found errors, so a single read answered 403 instead of the not-found 404 and
a listing failed whole instead of omitting the row.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from contexts.conversation.application.access import (
    can_read_orchestration_record,
    filter_readable_by_room,
)
from shared_kernel.auth.permissions import Principal


def _guest_of(chatroom_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), is_admin=False, email_verified=False, is_guest=True, chatroom_id=chatroom_id
    )


async def test_a_record_of_another_room_is_unreadable_not_forbidden() -> None:
    readable = await can_read_orchestration_record(
        MagicMock(),
        principal=_guest_of(uuid.uuid4()),
        chatroom_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        resolver=AsyncMock(),
    )
    assert readable is False


async def test_a_listing_omits_rows_of_another_room() -> None:
    other = SimpleNamespace(chatroom_id=uuid.uuid4())
    rows = await filter_readable_by_room(
        MagicMock(),
        principal=_guest_of(uuid.uuid4()),
        rows=[other],
        chatroom_id_of=lambda r: r.chatroom_id,
        project_id=uuid.uuid4(),
        resolver=AsyncMock(),
    )
    assert rows == []
