"""Room record, roster and agent list under the room read gate -- AC-1, AC-2, AC-3, AC-8.

Spec: ``docs/tasks/2026-10-05-guest-room-read-and-identity/spec.md`` §7.1, §7.2.

All three routes used to authorize through tenancy roles plus the legacy
``chatroom_guests`` table. An anonymous guest has neither, so it got 403 from all
three; and a project or org member the room's access flags refuse still got 200,
learning the room's name and roster ([R13.32]). The fix routes all three through
``resolve_room_access`` + ``ensure_can_read``, the gate messages, presence and the
socket already use.

The fakes below also stand up the pre-fix collaborators (``_project_id_for_chatroom``,
``get_role_resolver``, ``is_chatroom_guest``), so against the old handlers these tests
fail on the authorization outcome rather than on a missing patch.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import app.api.v1.chatrooms as chatrooms_mod
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.application.room_guests import RoomGuests
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.conversation.domain.models import ChatroomAgentRole
from shared_kernel.auth.permissions import Principal, Role
from tests.unit.chatroom_fakes import chatroom_row

_REFUSED = (HTTPException, ForbiddenInRoom)


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
    guest_sessions: dict[uuid.UUID, str] | None = None,
    registered: dict[uuid.UUID, str | None] | None = None,
    unaffiliated: frozenset[uuid.UUID] = frozenset(),
    senders: set[uuid.UUID] | None = None,
    account_names: dict[uuid.UUID, str] | None = None,
    agent_rows: list[Any] | None = None,
    agent_names: dict[uuid.UUID, str] | None = None,
) -> None:
    room = access.chatroom

    async def _resolve(db: object, *, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
        return access

    async def _pid(db: object, chatroom_id: uuid.UUID) -> uuid.UUID:
        return access.project_id

    class _Resolver:
        async def roles_for(self, principal: Principal, scope: object) -> frozenset[Role]:
            return access.roles

    async def _get_resolver(db: object) -> _Resolver:
        return _Resolver()

    class _Conversation:
        def __init__(self, db: object) -> None:
            pass

        async def is_chatroom_guest(self, *, chatroom_id: uuid.UUID, user_id: uuid.UUID) -> bool:
            return False

        async def list_guests(self, chatroom_id: uuid.UUID) -> list[Any]:
            return []

        async def distinct_user_sender_ids(self, chatroom_id: uuid.UUID) -> set[uuid.UUID]:
            return set(senders or ())

        async def guest_session_labels(self, chatroom_id: uuid.UUID) -> dict[uuid.UUID, str]:
            return dict(guest_sessions or {})

        async def room_guests(self, chatroom_id: uuid.UUID) -> RoomGuests:
            return RoomGuests(
                sessions=dict(guest_sessions or {}),
                registered=dict(registered or {}),
                unaffiliated=unaffiliated,
            )

    class _Identity:
        def __init__(self, db: object) -> None:
            pass

        async def get_display_names(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
            return {i: n for i, n in (account_names or {}).items() if i in ids}

    class _Agents:
        def __init__(self, db: object) -> None:
            pass

        async def agent_names(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
            return {i: n for i, n in (agent_names or {}).items() if i in ids}

    class _Service:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, chatroom_id: uuid.UUID) -> Any:
            return room

        async def rooms_with_observers(self, ids: list[uuid.UUID]) -> set[uuid.UUID]:
            return set()

        async def rooms_with_draft_readers(self, ids: list[uuid.UUID]) -> set[uuid.UUID]:
            return set()

        async def list_agents(self, chatroom_id: uuid.UUID) -> list[Any]:
            return list(agent_rows or [])

    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "_project_id_for_chatroom", _pid)
    monkeypatch.setattr(chatrooms_mod, "get_role_resolver", _get_resolver)
    monkeypatch.setattr(chatrooms_mod, "ConversationFacade", _Conversation)
    monkeypatch.setattr(chatrooms_mod, "IdentityFacade", _Identity)
    monkeypatch.setattr(chatrooms_mod, "AgentsFacade", _Agents)
    monkeypatch.setattr(chatrooms_mod, "ChatroomService", _Service)


def _access(room: Any, *, roles: frozenset[Role] = frozenset(), is_guest: bool = False) -> RoomAccess:
    return RoomAccess(chatroom=room, project_id=uuid.uuid4(), roles=roles, is_guest=is_guest)


def _agent_row(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=uuid.uuid4(),
        role=ChatroomAgentRole.NORMAL,
        may_control_activities=False,
        activity_type_allowlist=(),
        may_read_drafts=False,
        may_read_canvas=False,
        may_write_canvas=False,
        name=name,
    )


_PAGE = SimpleNamespace(offset=0, limit=50)


class TestAnonymousGuest:
    """AC-1, AC-3, AC-8."""

    async def test_reads_its_room_as_a_neutralised_guest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = chatroom_row(created_by=uuid.uuid4())
        _wire(monkeypatch, access=_access(room, is_guest=True))

        out = await chatrooms_mod.read_chatroom(chatroom_id=room.id, principal=_guest(room.id), db=object())

        assert out.viewer_is_guest is True
        assert out.created_by_user_id is None
        assert out.is_moderator is False
        assert out.name == "room"

    async def test_is_refused_once_guest_links_are_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = chatroom_row()
        room.allow_guest_links = False
        _wire(monkeypatch, access=_access(room, is_guest=True))

        with pytest.raises(_REFUSED):
            await chatrooms_mod.read_chatroom(chatroom_id=room.id, principal=_guest(room.id), db=object())
        with pytest.raises(_REFUSED):
            await chatrooms_mod.list_chatroom_members(
                chatroom_id=room.id, principal=_guest(room.id), db=object()
            )

    async def test_roster_includes_guest_sessions_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = chatroom_row()
        alice, bob = uuid.uuid4(), uuid.uuid4()
        _wire(monkeypatch, access=_access(room, is_guest=True), guest_sessions={alice: "Alice", bob: "Bob"})

        roster = await chatrooms_mod.list_chatroom_members(
            chatroom_id=room.id, principal=_guest(room.id), db=object()
        )

        assert {(m.user_id, m.display_name) for m in roster} == {(alice, "Alice"), (bob, "Bob")}
        assert {m.kind for m in roster} == {"guest_session"}


class TestRosterKind:
    """Guest sender marking AC-2, AC-3: the roster says which participants are guests.

    Spec: ``docs/tasks/2026-10-05-guest-sender-marking/spec.md`` §6, Q-3.
    """

    async def test_each_source_carries_its_kind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = chatroom_row()
        session, outsider, enrolled_member, speaker = (uuid.uuid4() for _ in range(4))
        _wire(
            monkeypatch,
            access=_access(room, roles=frozenset({Role.PROJECT_MEMBER})),
            guest_sessions={session: "Sam"},
            # The enrolled member clicked the link with their account before
            # enrolment skipped readers; their row survives but grants no kind.
            registered={outsider: "Olive", enrolled_member: None},
            unaffiliated=frozenset({outsider}),
            senders={speaker, enrolled_member},
            account_names={speaker: "Teacher", enrolled_member: "Mia"},
        )

        roster = await chatrooms_mod.list_chatroom_members(
            chatroom_id=room.id, principal=_member(), db=object()
        )

        assert {(m.user_id, m.display_name, m.kind) for m in roster} == {
            (session, "Sam", "guest_session"),
            (outsider, "Olive", "room_guest"),
            (enrolled_member, "Mia", "member"),
            (speaker, "Teacher", "member"),
        }

    async def test_a_registered_guest_without_a_room_label_is_still_a_guest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        room = chatroom_row()
        outsider = uuid.uuid4()
        _wire(
            monkeypatch,
            access=_access(room, roles=frozenset({Role.PROJECT_MEMBER})),
            registered={outsider: None},
            unaffiliated=frozenset({outsider}),
            account_names={outsider: "Teacher"},
        )

        roster = await chatrooms_mod.list_chatroom_members(
            chatroom_id=room.id, principal=_member(), db=object()
        )

        assert [(m.display_name, m.kind) for m in roster] == [("Teacher", "room_guest")]

    async def test_sees_the_rooms_agents_by_name_without_creator_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        room = chatroom_row(created_by=uuid.uuid4())
        row = _agent_row("Teaching Assistant")
        _wire(
            monkeypatch,
            access=_access(room, is_guest=True),
            agent_rows=[row],
            agent_names={row.agent_id: "Teaching Assistant"},
        )

        agents = await chatrooms_mod.list_chatroom_agents(
            chatroom_id=room.id, pagination=_PAGE, principal=_guest(room.id), db=object()
        )

        assert [(a.agent_id, a.name) for a in agents] == [(row.agent_id, "Teaching Assistant")]
        assert agents[0].role is None
        assert agents[0].may_read_drafts is None


class TestRoomFlagsGovernReads:
    """AC-2: [R13.32] -- a caller the room's flags refuse learns nothing about it."""

    @pytest.mark.parametrize("route", ["room", "members", "agents"])
    async def test_a_project_member_of_an_owners_only_room_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, route: str
    ) -> None:
        room = chatroom_row()
        room.allow_project_owners_only = True
        _wire(
            monkeypatch,
            access=_access(room, roles=frozenset({Role.PROJECT_MEMBER})),
            agent_rows=[_agent_row("A")],
        )
        member = _member()
        calls = {
            "room": lambda: chatrooms_mod.read_chatroom(chatroom_id=room.id, principal=member, db=object()),
            "members": lambda: chatrooms_mod.list_chatroom_members(
                chatroom_id=room.id, principal=member, db=object()
            ),
            "agents": lambda: chatrooms_mod.list_chatroom_agents(
                chatroom_id=room.id, pagination=_PAGE, principal=member, db=object()
            ),
        }

        with pytest.raises(_REFUSED):
            await calls[route]()

    async def test_an_org_member_of_a_project_only_room_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        room = chatroom_row()
        _wire(monkeypatch, access=_access(room, roles=frozenset({Role.ORG_MEMBER})))

        with pytest.raises(_REFUSED):
            await chatrooms_mod.read_chatroom(chatroom_id=room.id, principal=_member(), db=object())

    async def test_a_project_member_of_an_open_room_still_reads_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        room = chatroom_row()
        _wire(monkeypatch, access=_access(room, roles=frozenset({Role.PROJECT_MEMBER})))

        out = await chatrooms_mod.read_chatroom(chatroom_id=room.id, principal=_member(), db=object())

        assert out.viewer_is_guest is False

    async def test_a_platform_admin_reads_an_owners_only_room(self, monkeypatch: pytest.MonkeyPatch) -> None:
        room = chatroom_row()
        room.allow_project_owners_only = True
        _wire(monkeypatch, access=_access(room))
        admin = Principal(user_id=uuid.uuid4(), is_admin=True, email_verified=True)

        out = await chatrooms_mod.read_chatroom(chatroom_id=room.id, principal=admin, db=object())

        assert out.is_moderator is True


class TestGuestOfAnotherRoom:
    """A guest token is bound to one room: the real gate refuses any other room
    before it touches the database, on all three routes."""

    @pytest.mark.parametrize("route", ["room", "members", "agents"])
    async def test_is_refused(self, route: str) -> None:
        other_room = uuid.uuid4()
        guest = _guest(uuid.uuid4())
        calls = {
            "room": lambda: chatrooms_mod.read_chatroom(chatroom_id=other_room, principal=guest, db=object()),
            "members": lambda: chatrooms_mod.list_chatroom_members(
                chatroom_id=other_room, principal=guest, db=object()
            ),
            "agents": lambda: chatrooms_mod.list_chatroom_agents(
                chatroom_id=other_room, pagination=_PAGE, principal=guest, db=object()
            ),
        }

        with pytest.raises(ForbiddenInRoom):
            await calls[route]()
