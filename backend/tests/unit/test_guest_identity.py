"""Who is a guest identity, and who enrolment leaves as themselves -- AC-2, AC-3.

Spec: ``docs/tasks/2026-10-05-guest-sender-marking/spec.md`` §6, Q-3, D-3.

A registered guest is marked when they hold no role in the room's project and are
not a platform admin. A signed-in user who can already read the room is not
enrolled at all when they open the guest link with their account; one the room's
flags exclude still needs the row to get in, and is enrolled.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import contexts.conversation.application.room_guests as room_guests_mod
from contexts.conversation.application.access import RoomAccess, reads_without_guest_row
from contexts.conversation.application.guest_service import GuestService
from shared_kernel.auth.permissions import Principal, Role
from tests.unit.chatroom_fakes import chatroom_row

_TOKEN = "t" * 16


def _access(room: Any, *, roles: frozenset[Role] = frozenset(), is_guest: bool = False) -> RoomAccess:
    return RoomAccess(chatroom=room, project_id=uuid.uuid4(), roles=roles, is_guest=is_guest)


class TestReadsWithoutGuestRow:
    def test_a_project_member_of_a_member_room_already_reads_it(self) -> None:
        access = _access(chatroom_row(), roles=frozenset({Role.PROJECT_MEMBER}))

        assert reads_without_guest_row(access, is_admin=False) is True

    def test_an_existing_guest_row_does_not_count(self) -> None:
        # The member enrolled before the skip existed: the row must not make them
        # read "without" it, or the question answers itself.
        room = chatroom_row()
        room.allow_project_members = False
        access = _access(room, roles=frozenset({Role.PROJECT_MEMBER}), is_guest=True)

        assert reads_without_guest_row(access, is_admin=False) is False

    def test_an_org_member_of_an_org_room_already_reads_it(self) -> None:
        room = chatroom_row()
        room.allow_org_members = True
        access = _access(room, roles=frozenset({Role.ORG_MEMBER}))

        assert reads_without_guest_row(access, is_admin=False) is True

    def test_an_outsider_needs_the_row(self) -> None:
        assert reads_without_guest_row(_access(chatroom_row()), is_admin=False) is False

    def test_an_admin_reads_everything(self) -> None:
        assert reads_without_guest_row(_access(chatroom_row()), is_admin=True) is True


class TestEnrolment:
    async def _enroll(self, *, reads: bool, is_admin: bool = False) -> AsyncMock:
        room = SimpleNamespace(guest_token=_TOKEN, allow_guest_links=True)
        service = GuestService.__new__(GuestService)
        service._db = SimpleNamespace()
        service._rooms = SimpleNamespace(get=AsyncMock(return_value=room))
        service._guests = SimpleNamespace(add=AsyncMock())
        principal = Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True)
        access = _access(chatroom_row(), roles=frozenset({Role.PROJECT_MEMBER}) if reads else frozenset())

        with (
            patch(
                "contexts.conversation.application.guest_service.resolve_room_access",
                AsyncMock(return_value=access),
            ),
            patch("contexts.conversation.application.guest_service.audit.emit", AsyncMock()),
        ):
            out = await service.enroll(
                chatroom_id=uuid.uuid4(),
                token=_TOKEN,
                principal=principal,
                display_name="Teacher",
                actor_ip=None,
                request_id=None,
            )

        assert out is room
        return service._guests.add

    async def test_a_member_who_already_reads_the_room_writes_no_row(self) -> None:
        add = await self._enroll(reads=True)

        add.assert_not_awaited()

    async def test_an_admin_writes_no_row(self) -> None:
        add = await self._enroll(reads=False, is_admin=True)

        add.assert_not_awaited()

    async def test_an_outsider_is_enrolled_with_their_label(self) -> None:
        add = await self._enroll(reads=False)

        add.assert_awaited_once()
        assert add.await_args.kwargs["display_name"] == "Teacher"


class TestRoomGuests:
    async def _load(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        sessions: dict[uuid.UUID, str],
        registered: dict[uuid.UUID, str | None],
        holders: set[uuid.UUID],
        admins: set[uuid.UUID],
        project_id: uuid.UUID | None,
    ) -> room_guests_mod.RoomGuests:
        asked: list[list[uuid.UUID]] = []

        class _Sessions:
            def __init__(self, db: object) -> None:
                pass

            async def list_labels(self, chatroom_id: uuid.UUID) -> dict[uuid.UUID, str]:
                return sessions

        class _Guests:
            def __init__(self, db: object) -> None:
                pass

            async def list(self, chatroom_id: uuid.UUID) -> list[Any]:
                return [SimpleNamespace(user_id=u, display_name=n) for u, n in registered.items()]

        class _Resolver:
            def __init__(self, db: object) -> None:
                pass

            async def project_role_holders(
                self, user_ids: list[uuid.UUID], *, project_id: uuid.UUID
            ) -> set[uuid.UUID]:
                asked.append(user_ids)
                return holders & set(user_ids)

        class _Identity:
            def __init__(self, db: object) -> None:
                pass

            async def admin_ids(self) -> set[uuid.UUID]:
                return admins

        async def _pid(db: object, chatroom_id: uuid.UUID) -> uuid.UUID | None:
            return project_id

        monkeypatch.setattr(room_guests_mod, "GuestSessionRepository", _Sessions)
        monkeypatch.setattr(room_guests_mod, "ChatroomGuestRepository", _Guests)
        monkeypatch.setattr(room_guests_mod, "TenancyRoleResolver", _Resolver)
        monkeypatch.setattr(room_guests_mod, "IdentityFacade", _Identity)
        monkeypatch.setattr(room_guests_mod, "project_id_for_room", _pid)
        out = await room_guests_mod.load_room_guests(object(), uuid.uuid4())  # type: ignore[arg-type]
        if not registered:
            assert asked == []
        return out

    async def test_members_and_admins_are_not_guest_identities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session, outsider, member, admin = (uuid.uuid4() for _ in range(4))

        out = await self._load(
            monkeypatch,
            sessions={session: "Sam"},
            registered={outsider: "Teacher", member: "Mia", admin: None},
            holders={member},
            admins={admin},
            project_id=uuid.uuid4(),
        )

        assert out.unaffiliated == frozenset({outsider})
        assert out.identity_ids == frozenset({session, outsider})
        # Labels keep every row: an enrolled member's room label still names them.
        assert out.labels == {session: "Sam", outsider: "Teacher", member: "Mia", admin: None}

    async def test_a_broken_project_chain_marks_every_registered_guest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outsider = uuid.uuid4()

        out = await self._load(
            monkeypatch,
            sessions={},
            registered={outsider: None},
            holders={outsider},
            admins=set(),
            project_id=None,
        )

        assert out.unaffiliated == frozenset({outsider})

    async def test_no_registered_guests_asks_tenancy_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = uuid.uuid4()

        out = await self._load(
            monkeypatch,
            sessions={session: "Sam"},
            registered={},
            holders=set(),
            admins=set(),
            project_id=None,
        )

        assert out.identity_ids == frozenset({session})
