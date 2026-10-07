"""`AuditEvent.actor_guest_room_id` -- the one owner of the guest audit shape (F-14).

Spec: ``docs/tasks/2026-10-05-guest-session-backend-hardening/spec.md`` §7.1, Q-2.
The end-to-end property (every guest-caused row carries the shape) is the db-tier
``TestGuestAuditTrail``; this pins the merge itself.
"""

from __future__ import annotations

import uuid
from typing import Any

from shared_kernel import audit


class _Session:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {}
        self.metadata: dict[str, Any] | None = None

    async def execute(self, stmt: Any) -> None:
        self.metadata = stmt.compile().params["metadata"]


async def test_a_guest_actor_gets_the_guest_tag_and_its_room() -> None:
    room = uuid.uuid4()
    session = _Session()
    event = audit.AuditEvent(action="message.sent", metadata={"len": 3}, actor_guest_room_id=room)

    await audit.emit(session, event)  # type: ignore[arg-type]

    assert session.metadata == {"len": 3, "guest": True, "chatroom_id": str(room)}


async def test_the_tag_wins_over_a_conflicting_caller_key() -> None:
    room = uuid.uuid4()
    session = _Session()
    event = audit.AuditEvent(
        action="message.sent", metadata={"guest": False, "chatroom_id": "other"}, actor_guest_room_id=room
    )

    await audit.emit(session, event)  # type: ignore[arg-type]

    assert session.metadata == {"guest": True, "chatroom_id": str(room)}


async def test_any_other_actor_is_left_untagged() -> None:
    session = _Session()
    original = {"chatroom_id": "c"}

    await audit.emit(session, audit.AuditEvent(action="message.deleted", metadata=original))  # type: ignore[arg-type]

    assert session.metadata == {"chatroom_id": "c"}
    assert original == {"chatroom_id": "c"}
