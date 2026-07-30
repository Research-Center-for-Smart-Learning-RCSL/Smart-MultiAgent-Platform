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
from shared_kernel.auth.permissions import _MATRIX, Capability, Outcome, Role
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


def test_create_chatroom_serializes_the_creator_as_a_moderator() -> None:
    """`create_chatroom` was the one `_to_out` call site never given the flag, so
    its 201 body claimed the creator was not a moderator while a GET one request
    later said they were.

    Asserted against the route's real source rather than a live request: the fix
    is only sound because the capability gate above it already proves the roles,
    and that is what this pins.
    """
    import inspect

    import app.api.v1.chatrooms as chatrooms_mod

    source = inspect.getsource(chatrooms_mod.create_chatroom)
    assert "is_moderator=True" in source, (
        "create_chatroom must serialize the creator as a moderator; _to_out defaults "
        "the field to False and every other call site in the module passes it"
    )


def test_chat_create_grants_exactly_the_moderator_roles() -> None:
    """What makes `create_chatroom`'s hardcoded ``is_moderator=True`` sound.

    Getting past ``_require_project_cap(..., CHAT_CREATE)`` proves the caller holds
    a role the moderator predicate also accepts, so the route needs no second role
    lookup. Widen CHAT_CREATE to a non-owner role and that stops being true — this
    fails, rather than the 201 body quietly starting to lie.
    """
    granted = {
        role for role, outcome in _MATRIX[Capability.CHAT_CREATE].items() if outcome is not Outcome.DENY
    }

    assert granted, "CHAT_CREATE grants nothing — the invariant would be vacuous"
    for role in granted:
        assert is_moderator_roles(frozenset({role})), (
            f"CHAT_CREATE allows {role}, which is not a moderator role. "
            "create_chatroom's is_moderator=True is now wrong."
        )
