"""V-4: `ChatroomOut.is_moderator` — the serialized half of the R13.23 edit /
delete affordance.

The backend already honours project and org owners as moderators
(`messages.py` delete gate, `message_service.edit`), but the DTO carried no
signal, so the frontend could only implement the platform-admin arm. These
tests pin the field's default, its fail-closed behaviour for pure guests, and
the equality of the DTO predicate with the enforcement predicate
(`RoomAccess.is_moderator`).
"""

from __future__ import annotations

import uuid

import pytest

from contexts.conversation.application.access import RoomAccess, is_moderator_roles
from shared_kernel.auth.permissions import Role
from tests.unit.chatroom_fakes import chatroom_row


def test_to_out_reports_is_moderator_only_when_told_to() -> None:
    """T-11: the field defaults to False, so every call site grants explicitly."""
    import app.api.v1.chatrooms as chatrooms_mod

    room = chatroom_row(created_by=uuid.uuid4())
    assert chatrooms_mod._to_out(room).is_moderator is False
    assert chatrooms_mod._to_out(room, is_moderator=True).is_moderator is True


def test_pure_guest_is_never_a_moderator() -> None:
    """T-12: same fail-closed rule as every other observer/authority field —
    a guest link must not become an oracle for who moderates the room."""
    import app.api.v1.chatrooms as chatrooms_mod

    room = chatroom_row(created_by=uuid.uuid4())
    view = chatrooms_mod._to_out(room, is_moderator=True, viewer_is_pure_guest=True)
    assert view.is_moderator is False


_ROLE_TABLE = [
    (frozenset({Role.PROJECT_OWNER}), True),
    (frozenset({Role.ORG_OWNER}), True),
    # R5.03: an org owner needs no `project_members` row to moderate — the case
    # a members-list lookup on the client could never have expressed (Q-2).
    (frozenset({Role.ORG_OWNER, Role.ORG_MEMBER}), True),
    (frozenset({Role.PROJECT_OWNER, Role.PROJECT_MEMBER}), True),
    (frozenset({Role.PROJECT_MEMBER}), False),
    (frozenset({Role.ORG_MEMBER}), False),
    (frozenset(), False),
]


@pytest.mark.parametrize(("roles", "expected"), _ROLE_TABLE)
def test_dto_predicate_matches_enforcement_predicate(
    roles: frozenset[Role],
    expected: bool,
) -> None:
    """T-13: the expression the routes feed into the DTO and the expression the
    delete/edit gates read must be the same function, on every role set."""
    access = RoomAccess(
        chatroom=chatroom_row(created_by=uuid.uuid4()),
        project_id=uuid.uuid4(),
        roles=roles,
        is_guest=False,
    )
    assert is_moderator_roles(roles) is expected
    assert access.is_moderator is expected
