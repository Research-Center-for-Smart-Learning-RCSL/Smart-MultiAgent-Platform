"""Room ACL gate tests (access.py §21.1 + R13.04).

SEC regression: read confidentiality must mirror the send matrix — holding any
org/project role is not sufficient to read a room whose flags do not admit the
caller's tier.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from contexts.conversation.application.access import (
    RoomAccess,
    ensure_can_read,
    ensure_can_send,
)
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.conversation.domain.models import Chatroom
from shared_kernel.auth.permissions import Role


def _room(
    *,
    allow_org_members: bool = False,
    allow_project_members: bool = True,
    allow_project_owners_only: bool = False,
    allow_guest_links: bool = False,
) -> Chatroom:
    return Chatroom(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="general",
        allow_org_members=allow_org_members,
        allow_project_members=allow_project_members,
        allow_project_owners_only=allow_project_owners_only,
        allow_guest_links=allow_guest_links,
        guest_token="t",
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _access(room: Chatroom, *, roles: set[Role] | None = None, is_guest: bool = False) -> RoomAccess:
    return RoomAccess(
        chatroom=room,
        project_id=uuid.uuid4(),
        roles=frozenset(roles or set()),
        is_guest=is_guest,
    )


def test_org_member_cannot_read_project_members_only_room() -> None:
    # Default room: project members only. An org member of the parent org gets
    # ORG_MEMBER but is not a project member -> must be denied read (was the bug).
    access = _access(_room(), roles={Role.ORG_MEMBER})
    with pytest.raises(ForbiddenInRoom):
        ensure_can_read(access, is_admin=False)


def test_project_member_can_read_default_room() -> None:
    access = _access(_room(), roles={Role.PROJECT_MEMBER})
    ensure_can_read(access, is_admin=False)  # no raise


def test_owners_only_room_denies_project_member_read() -> None:
    room = _room(allow_project_members=False, allow_project_owners_only=True)
    access = _access(room, roles={Role.PROJECT_MEMBER})
    with pytest.raises(ForbiddenInRoom):
        ensure_can_read(access, is_admin=False)


def test_owners_only_room_allows_moderator_read() -> None:
    room = _room(allow_project_members=False, allow_project_owners_only=True)
    access = _access(room, roles={Role.PROJECT_OWNER})
    ensure_can_read(access, is_admin=False)


def test_owners_only_is_exclusive_even_with_other_flags() -> None:
    # owners_only set together with project_members must still exclude members.
    room = _room(allow_project_members=True, allow_project_owners_only=True)
    access = _access(room, roles={Role.PROJECT_MEMBER})
    with pytest.raises(ForbiddenInRoom):
        ensure_can_read(access, is_admin=False)


def test_revoked_guest_link_revokes_read() -> None:
    # Guest still on chatroom_guests, but allow_guest_links was turned off.
    room = _room(allow_project_members=False, allow_guest_links=False)
    access = _access(room, is_guest=True)
    with pytest.raises(ForbiddenInRoom):
        ensure_can_read(access, is_admin=False)


def test_guest_can_read_when_links_enabled() -> None:
    room = _room(allow_project_members=False, allow_guest_links=True)
    access = _access(room, is_guest=True)
    ensure_can_read(access, is_admin=False)


def test_org_member_can_read_when_org_flag_enabled() -> None:
    room = _room(allow_project_members=False, allow_org_members=True)
    access = _access(room, roles={Role.ORG_MEMBER})
    ensure_can_read(access, is_admin=False)


def test_admin_bypasses_read_gate() -> None:
    room = _room(allow_project_members=False, allow_project_owners_only=True)
    access = _access(room, roles=set())
    ensure_can_read(access, is_admin=True)


def test_read_and_send_share_one_matrix() -> None:
    # An org member denied read on a default room is likewise denied send.
    access = _access(_room(), roles={Role.ORG_MEMBER})
    with pytest.raises(ForbiddenInRoom):
        ensure_can_send(access, is_admin=False)
    with pytest.raises(ForbiddenInRoom):
        ensure_can_read(access, is_admin=False)
