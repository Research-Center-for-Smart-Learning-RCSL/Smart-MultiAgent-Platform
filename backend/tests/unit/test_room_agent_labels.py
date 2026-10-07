"""Agent names for a room's history, for every reader of it -- AC-1, AC-2.

Spec: ``docs/tasks/2026-10-07-name-fallback-surfaces/spec.md`` §7.1, §8.

The room view named agents from the room's current non-observer bindings, plus the
project's live agents for members only. A guest therefore saw an id on the messages
of an agent since unbound and in the header of a released observation, and every
viewer saw an id once a deleted agent's binding was removed. The fix is a
room-gated read naming the agents the room's history already shows: message
authors, and the observer named in a released observation's disclosed metadata.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import app.api.v1.chatrooms as chatrooms_mod
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.conversation.domain.models import ChatroomAgentRole
from shared_kernel.auth.permissions import Principal, Role
from tests.unit.chatroom_fakes import chatroom_row


def _guest(room_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), is_admin=False, email_verified=False, is_guest=True, chatroom_id=room_id
    )


def _member() -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    access: RoomAccess,
    label_ids: set[uuid.UUID],
    agent_names: dict[uuid.UUID, str],
    bindings: list[Any] | None = None,
) -> None:
    async def _resolve(db: object, *, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
        return access

    class _Conversation:
        def __init__(self, db: object) -> None:
            pass

        async def agent_label_ids(self, chatroom_id: uuid.UUID) -> set[uuid.UUID]:
            return set(label_ids)

    class _Agents:
        def __init__(self, db: object) -> None:
            pass

        async def agent_names(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
            return {i: n for i, n in agent_names.items() if i in ids}

    class _Service:
        def __init__(self, db: object) -> None:
            pass

        async def list_agents(self, chatroom_id: uuid.UUID) -> list[Any]:
            return list(bindings or [])

    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ConversationFacade", _Conversation)
    monkeypatch.setattr(chatrooms_mod, "AgentsFacade", _Agents)
    monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)


def _access(room: Any, *, roles: frozenset[Role] = frozenset(), is_guest: bool = False) -> RoomAccess:
    return RoomAccess(chatroom=room, project_id=uuid.uuid4(), roles=roles, is_guest=is_guest)


class TestAgentLabels:
    async def test_a_guest_reads_the_names_of_unbound_deleted_and_disclosed_agents(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        room = chatroom_row(created_by=uuid.uuid4())
        unbound, deleted, observer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        # `agent_names` includes soft-deleted agents by contract; the facade fake
        # stands in for that, so the deleted author resolves like any other.
        _wire(
            monkeypatch,
            access=_access(room, is_guest=True),
            label_ids={unbound, deleted, observer},
            agent_names={unbound: "Tutor", deleted: "Retired", observer: "Analyst"},
        )

        labels = await chatrooms_mod.list_chatroom_agent_labels(
            chatroom_id=room.id, principal=_guest(room.id), db=object()
        )

        assert {(a.agent_id, a.name) for a in labels} == {
            (unbound, "Tutor"),
            (deleted, "Retired"),
            (observer, "Analyst"),
        }

    async def test_an_id_with_no_name_is_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = chatroom_row()
        gone = uuid.uuid4()
        _wire(
            monkeypatch,
            access=_access(room, roles=frozenset({Role.PROJECT_MEMBER})),
            label_ids={gone},
            agent_names={},
        )

        labels = await chatrooms_mod.list_chatroom_agent_labels(
            chatroom_id=room.id, principal=_member(), db=object()
        )

        assert labels == []

    async def test_an_undisclosed_bound_observer_is_absent_for_a_non_creator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # [R28.10]: the read names only ids the history shows, never the bindings.
        room = chatroom_row(created_by=uuid.uuid4())
        author, observer = uuid.uuid4(), uuid.uuid4()
        _wire(
            monkeypatch,
            access=_access(room, roles=frozenset({Role.PROJECT_MEMBER})),
            label_ids={author},
            agent_names={author: "Tutor", observer: "Analyst"},
            bindings=[SimpleNamespace(agent_id=observer, role=ChatroomAgentRole.OBSERVER)],
        )

        labels = await chatrooms_mod.list_chatroom_agent_labels(
            chatroom_id=room.id, principal=_member(), db=object()
        )

        assert [a.agent_id for a in labels] == [author]

    async def test_a_member_the_room_flags_refuse_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = chatroom_row()
        room.allow_project_owners_only = True
        author = uuid.uuid4()
        _wire(
            monkeypatch,
            access=_access(room, roles=frozenset({Role.PROJECT_MEMBER})),
            label_ids={author},
            agent_names={author: "Tutor"},
        )

        with pytest.raises(ForbiddenInRoom):
            await chatrooms_mod.list_chatroom_agent_labels(
                chatroom_id=room.id, principal=_member(), db=object()
            )

    async def test_a_guest_of_another_room_is_refused_by_the_real_gate(self) -> None:
        guest = _guest(uuid.uuid4())

        with pytest.raises(ForbiddenInRoom):
            await chatrooms_mod.list_chatroom_agent_labels(
                chatroom_id=uuid.uuid4(), principal=guest, db=object()
            )
