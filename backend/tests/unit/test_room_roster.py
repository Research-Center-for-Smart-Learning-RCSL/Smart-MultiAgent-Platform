"""The room roster names everyone present, and a rename reaches open rooms -- AC-1.

Spec: ``docs/tasks/2026-10-07-room-roster-completeness/spec.md`` 禮7, 禮8.

The roster's id set was message authors plus guest records, so a member who opened
a room and read without posting was listed, typed and left out of the mention list
as a truncated id. A profile rename published nothing, so open rooms kept the old
name. The fix adds the room's present users to the roster and has the profile route
emit ``chatroom.members_changed`` to every room the renamed user is present in.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.v1.auth as auth_mod
import app.api.v1.chatrooms as chatrooms_mod
import contexts.conversation.infrastructure.channels as channels_mod
import contexts.conversation.interfaces.facade as facade_mod
import contexts.identity.application.auth_service as auth_service_mod
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.application.room_guests import RoomGuests
from contexts.conversation.interfaces import room_channel
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.identity.application.auth_service import AuthService
from contexts.identity.application.email_domain_policy_reader import EmailDomainPolicyReader
from contexts.identity.domain.models import UserStatus
from contexts.identity.interfaces.facade import UserProfile
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.permissions import Principal, Role
from tests.unit.chatroom_fakes import chatroom_row

_EVENT = "chatroom.members_changed"


def _member() -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)


def _wire_roster(
    monkeypatch: pytest.MonkeyPatch,
    *,
    present: list[uuid.UUID],
    guest_sessions: dict[uuid.UUID, str] | None = None,
    registered: dict[uuid.UUID, str | None] | None = None,
    unaffiliated: frozenset[uuid.UUID] = frozenset(),
    account_names: dict[uuid.UUID, str | None] | None = None,
) -> None:
    room = chatroom_row()
    access = RoomAccess(
        chatroom=room, project_id=uuid.uuid4(), roles=frozenset({Role.PROJECT_MEMBER}), is_guest=False
    )

    async def _resolve(db: object, *, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
        return access

    class _Conversation:
        def __init__(self, db: object) -> None:
            pass

        async def distinct_user_sender_ids(self, chatroom_id: uuid.UUID) -> set[uuid.UUID]:
            return set()

        async def present_user_ids(self, chatroom_id: uuid.UUID) -> list[uuid.UUID]:
            return list(present)

        async def room_guests(
            self, chatroom_id: uuid.UUID, *, project_id: uuid.UUID | None = None
        ) -> RoomGuests:
            return RoomGuests(
                sessions=dict(guest_sessions or {}),
                registered=dict(registered or {}),
                unaffiliated=unaffiliated,
            )

    class _Identity:
        def __init__(self, db: object) -> None:
            pass

        async def get_display_names(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
            return {i: n for i, n in (account_names or {}).items() if i in ids}

    monkeypatch.setattr(chatrooms_mod, "resolve_room_access", _resolve)
    monkeypatch.setattr(chatrooms_mod, "ConversationFacade", _Conversation)
    monkeypatch.setattr(chatrooms_mod, "IdentityFacade", _Identity)


class TestRosterIncludesPresentUsers:
    async def test_a_present_member_who_never_posted_is_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reader = uuid.uuid4()
        _wire_roster(monkeypatch, present=[reader], account_names={reader: "Alice"})

        roster = await chatrooms_mod.list_chatroom_members(
            chatroom_id=uuid.uuid4(), principal=_member(), db=object()
        )

        assert [(m.user_id, m.display_name, m.kind) for m in roster] == [(reader, "Alice", "member")]

    async def test_a_present_account_without_a_name_is_listed_unnamed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reader = uuid.uuid4()
        _wire_roster(monkeypatch, present=[reader], account_names={reader: None})

        roster = await chatrooms_mod.list_chatroom_members(
            chatroom_id=uuid.uuid4(), principal=_member(), db=object()
        )

        assert [(m.user_id, m.display_name, m.kind) for m in roster] == [(reader, None, "member")]

    async def test_a_present_id_that_is_no_account_is_not_listed_as_a_member(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/code-review: a guest session past `list_labels`' cap, or one whose row is gone
        while its socket is open, is not in `guests.sessions`; it must not become a member."""
        _wire_roster(monkeypatch, present=[uuid.uuid4()])

        roster = await chatrooms_mod.list_chatroom_members(
            chatroom_id=uuid.uuid4(), principal=_member(), db=object()
        )

        assert roster == []

    async def test_a_present_guest_session_is_listed_once_as_a_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = uuid.uuid4()
        _wire_roster(monkeypatch, present=[session], guest_sessions={session: "Sam"})

        roster = await chatrooms_mod.list_chatroom_members(
            chatroom_id=uuid.uuid4(), principal=_member(), db=object()
        )

        assert [(m.user_id, m.display_name, m.kind) for m in roster] == [(session, "Sam", "guest_session")]

    async def test_a_present_unaffiliated_registered_guest_keeps_the_guest_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outsider = uuid.uuid4()
        _wire_roster(
            monkeypatch,
            present=[outsider],
            registered={outsider: None},
            unaffiliated=frozenset({outsider}),
            account_names={outsider: "Teacher"},
        )

        roster = await chatrooms_mod.list_chatroom_members(
            chatroom_id=uuid.uuid4(), principal=_member(), db=object()
        )

        assert [(m.user_id, m.display_name, m.kind) for m in roster] == [(outsider, "Teacher", "room_guest")]


def _profile(user_id: uuid.UUID, name: str | None) -> UserProfile:
    return UserProfile(
        id=user_id,
        email="a@example.com",
        status=UserStatus.ACTIVE,
        email_verified=True,
        is_admin=False,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_login_at=None,
        display_name=name,
    )


def _wire_rename(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_id: uuid.UUID,
    before: str | None,
    after: str | None,
    rooms: list[uuid.UUID],
    events: list[tuple[str, ...]],
    emit_error: Exception | None = None,
) -> MagicMock:
    stored = {"name": before}

    class _Identity:
        def __init__(self, db: object) -> None:
            pass

        async def get_profile(self, uid: uuid.UUID) -> UserProfile:
            return _profile(user_id, stored["name"])

    async def _update_display_name(**kwargs: object) -> bool:
        changed = stored["name"] != after
        stored["name"] = after
        return changed

    class _Presence:
        async def list_user_rooms(self, uid: uuid.UUID) -> list[uuid.UUID]:
            return list(rooms) if uid == user_id else []

    def _publisher(channel: str) -> SimpleNamespace:
        async def _emit(event: str, payload: dict[str, str]) -> None:
            if emit_error is not None:
                raise emit_error
            events.append(("emit", channel, event, payload["chatroom_id"]))

        return SimpleNamespace(emit=_emit)

    async def _commit() -> None:
        events.append(("commit",))

    service = SimpleNamespace(update_display_name=_update_display_name)
    monkeypatch.setattr(auth_mod, "_service", lambda db: service)
    monkeypatch.setattr(auth_mod, "IdentityFacade", _Identity)
    monkeypatch.setattr(facade_mod, "PresenceTracker", _Presence)
    monkeypatch.setattr(channels_mod, "Publisher", _publisher)
    return MagicMock(commit=AsyncMock(side_effect=_commit))


class TestProfileRenameReachesOpenRooms:
    async def test_a_rename_emits_to_every_room_the_user_is_present_in_after_commit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user, room_a, room_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        events: list[tuple[str, ...]] = []
        db = _wire_rename(
            monkeypatch, user_id=user, before="Alice", after="Alicia", rooms=[room_a, room_b], events=events
        )

        out = await auth_mod.update_me(
            body=auth_mod.UpdateProfileIn(display_name="Alicia"),
            ctx=RequestContext(),
            principal=Principal(user_id=user, is_admin=False, email_verified=True),
            db=db,
        )

        assert out.display_name == "Alicia"
        assert events == [
            ("commit",),
            *(("emit", room_channel(r), _EVENT, str(r)) for r in (room_a, room_b)),
        ]

    @pytest.mark.parametrize("sent", [True, False])
    async def test_no_change_emits_nothing(self, monkeypatch: pytest.MonkeyPatch, sent: bool) -> None:
        user = uuid.uuid4()
        events: list[tuple[str, ...]] = []
        db = _wire_rename(
            monkeypatch, user_id=user, before="Alice", after="Alice", rooms=[uuid.uuid4()], events=events
        )
        body = auth_mod.UpdateProfileIn(display_name="Alice") if sent else auth_mod.UpdateProfileIn()

        await auth_mod.update_me(
            body=body,
            ctx=RequestContext(),
            principal=Principal(user_id=user, is_admin=False, email_verified=True),
            db=db,
        )

        assert [e for e in events if e[0] == "emit"] == []

    async def test_a_failed_emit_does_not_fail_the_rename(self, monkeypatch: pytest.MonkeyPatch) -> None:
        user = uuid.uuid4()
        events: list[tuple[str, ...]] = []
        db = _wire_rename(
            monkeypatch,
            user_id=user,
            before="Alice",
            after="Alicia",
            rooms=[uuid.uuid4()],
            events=events,
            emit_error=RuntimeError("redis down"),
        )

        out = await auth_mod.update_me(
            body=auth_mod.UpdateProfileIn(display_name="Alicia"),
            ctx=RequestContext(),
            principal=Principal(user_id=user, is_admin=False, email_verified=True),
            db=db,
        )

        assert out.display_name == "Alicia"


class _BrokenPresence:
    async def list_room(self, room_id: uuid.UUID) -> list[uuid.UUID]:
        raise ConnectionError("redis down")

    async def list_user_rooms(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        raise ConnectionError("redis down")


class TestPresenceOutage:
    """/code-review: presence only adds names, so a Redis outage must not fail the
    Postgres-backed roster or a rename that has already been committed."""

    async def test_the_roster_reads_no_present_users(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(facade_mod, "PresenceTracker", _BrokenPresence)

        assert await ConversationFacade(MagicMock()).present_user_ids(uuid.uuid4()) == []

    async def test_the_rename_announcement_gives_up_quietly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(facade_mod, "PresenceTracker", _BrokenPresence)

        await ConversationFacade(MagicMock()).announce_participant_renamed(uuid.uuid4())


class TestDisplayNameChange:
    """The service reports whether the stored name changed; only a change is announced."""

    @pytest.mark.parametrize(
        ("stored", "sent", "changed"),
        [("Alice", "Alicia", True), ("Alice", "  Alice ", False), (None, None, False), ("Alice", None, True)],
    )
    async def test_reports_a_change_against_the_stored_name(
        self, monkeypatch: pytest.MonkeyPatch, stored: str | None, sent: str | None, changed: bool
    ) -> None:
        svc = AuthService(
            db=AsyncMock(),
            hasher=MagicMock(),
            email_sender=AsyncMock(),
            public_origin="https://smap.test",
            email_domain_policy=EmailDomainPolicyReader(
                repository=AsyncMock(), mirror=AsyncMock(), legacy=AsyncMock()
            ),
        )
        users = AsyncMock()
        users.get_by_id.return_value = SimpleNamespace(display_name=stored)
        svc._users = users
        monkeypatch.setattr(auth_service_mod.audit, "emit", AsyncMock())

        result = await svc.update_display_name(
            user_id=uuid.uuid4(), display_name=sent, remote_ip=None, request_id=None
        )

        assert result is changed
