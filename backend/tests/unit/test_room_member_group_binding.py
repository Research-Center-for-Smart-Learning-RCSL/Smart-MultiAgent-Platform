"""The two refusals that make the group tier mean something (§13.2a).

R13.04's mutual exclusion, and the cross-project binding check. Both are refusals
rather than corrections: a room that silently ends up readable by the whole
project, or by another project's members, is the failure this feature exists to
prevent, so neither is something to quietly fix up on the caller's behalf.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1 import chatrooms as chatrooms_mod
from contexts.conversation.application.chatroom_service import (
    ChatroomFlagsPatch,
    ChatroomService,
)
from contexts.conversation.domain.errors import RoomAccessFlagsConflict
from contexts.conversation.domain.models import Chatroom
from contexts.tenancy.domain.models import MemberGroup

_ROOM_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
_PROJECT_ID = uuid.UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
_OTHER_PROJECT_ID = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
_USER_ID = uuid.uuid4()

_AUDIT = patch("contexts.conversation.application.chatroom_service.audit.emit", new_callable=AsyncMock)


def _room(*, allow_project_members: bool, allow_member_groups: bool) -> Chatroom:
    return Chatroom(
        id=_ROOM_ID,
        workspace_id=uuid.uuid4(),
        name="room",
        allow_org_members=False,
        allow_project_members=allow_project_members,
        allow_project_owners_only=False,
        allow_guest_links=False,
        allow_member_groups=allow_member_groups,
        guest_token="t",
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _service(*, current: Chatroom | None = None) -> ChatroomService:
    svc = ChatroomService(AsyncMock())
    rooms = AsyncMock()
    if current is not None:
        rooms.get.return_value = current
    rooms.update.return_value = current
    svc._rooms = rooms
    svc._member_groups = AsyncMock()
    return svc


class TestFlagExclusivity:
    """R13.04 — the group tier and the whole-project tier cannot both be on."""

    @pytest.mark.asyncio
    async def test_create_refuses_both_flags(self) -> None:
        svc = _service()
        with _AUDIT, pytest.raises(RoomAccessFlagsConflict):
            await svc.create(
                workspace_id=uuid.uuid4(),
                name="r",
                allow_project_members=True,
                allow_member_groups=True,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )
        svc._rooms.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_allows_the_group_tier_alone(self) -> None:
        svc = _service()
        svc._rooms.create.return_value = _room(allow_project_members=False, allow_member_groups=True)
        with _AUDIT:
            await svc.create(
                workspace_id=uuid.uuid4(),
                name="r",
                allow_project_members=False,
                allow_member_groups=True,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )
        svc._rooms.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_patch_naming_only_the_group_flag_is_still_refused(self) -> None:
        """The two-step widening. The request names one flag; the room already has
        the other, and the merged state is what R13.04 forbids."""
        svc = _service(current=_room(allow_project_members=True, allow_member_groups=False))

        with _AUDIT, pytest.raises(RoomAccessFlagsConflict):
            await svc.patch(
                chatroom_id=_ROOM_ID,
                expected_version=1,
                patch=ChatroomFlagsPatch(allow_member_groups=True),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )
        svc._rooms.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_patch_naming_only_the_project_flag_is_still_refused(self) -> None:
        """The same widening from the other direction: re-opening a group-scoped
        room to the whole project."""
        svc = _service(current=_room(allow_project_members=False, allow_member_groups=True))

        with _AUDIT, pytest.raises(RoomAccessFlagsConflict):
            await svc.patch(
                chatroom_id=_ROOM_ID,
                expected_version=1,
                patch=ChatroomFlagsPatch(allow_project_members=True),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )
        svc._rooms.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_swapping_both_flags_in_one_patch_is_allowed(self) -> None:
        """The normal transition: narrow a project room down to named groups."""
        current = _room(allow_project_members=True, allow_member_groups=False)
        svc = _service(current=current)

        with _AUDIT:
            await svc.patch(
                chatroom_id=_ROOM_ID,
                expected_version=1,
                patch=ChatroomFlagsPatch(allow_project_members=False, allow_member_groups=True),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )
        svc._rooms.update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_unrelated_patch_on_a_group_room_is_untouched(self) -> None:
        current = _room(allow_project_members=False, allow_member_groups=True)
        svc = _service(current=current)

        with _AUDIT:
            await svc.patch(
                chatroom_id=_ROOM_ID,
                expected_version=1,
                patch=ChatroomFlagsPatch(name="renamed"),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )
        svc._rooms.update.assert_awaited_once()


def _member_group(project_id: uuid.UUID) -> MemberGroup:
    return MemberGroup(
        id=uuid.uuid4(),
        project_id=project_id,
        name="g",
        created_by_user_id=_USER_ID,
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


class TestCrossProjectBinding:
    """SEC: a binding may only name groups of the room's own project.

    Without the check, an owner of project A binds a group from project B to a
    room in A and hands B's members a room they have no standing in — a
    cross-project grant assembled entirely from ids the caller may legitimately
    know.
    """

    @staticmethod
    async def _call(groups: list[MemberGroup]) -> object:
        service = AsyncMock()
        service.set_bound_groups = AsyncMock(return_value={g.id for g in groups})
        tenancy = AsyncMock()
        # The facade answers with the subset that is live *and* in this project;
        # a foreign or deleted id is simply absent from it.
        tenancy.live_member_group_ids = AsyncMock(
            return_value={g.id for g in groups if g.project_id == _PROJECT_ID}
        )

        with (
            patch.object(
                chatrooms_mod,
                "_project_id_for_chatroom",
                AsyncMock(return_value=_PROJECT_ID),
            ),
            patch.object(chatrooms_mod, "_require_project_cap", AsyncMock()),
            patch.object(chatrooms_mod, "TenancyFacade", return_value=tenancy),
            patch.object(chatrooms_mod, "ChatroomService", return_value=service),
        ):
            return await chatrooms_mod.set_chatroom_member_groups(
                body=chatrooms_mod.ChatroomMemberGroupsIn(member_group_ids=[g.id for g in groups]),
                chatroom_id=_ROOM_ID,
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=SimpleNamespace(user_id=_USER_ID, is_admin=False, email_verified=True),
                db=AsyncMock(),
            )

    @pytest.mark.asyncio
    async def test_a_group_from_another_project_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await self._call([_member_group(_OTHER_PROJECT_ID)])
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_one_foreign_group_refuses_the_whole_request(self) -> None:
        """No partial write: the caller's intent was one set, and applying half of
        it would leave a binding neither they nor the check chose."""
        with pytest.raises(HTTPException) as exc:
            await self._call([_member_group(_PROJECT_ID), _member_group(_OTHER_PROJECT_ID)])
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_same_project_groups_are_written(self) -> None:
        groups = [_member_group(_PROJECT_ID), _member_group(_PROJECT_ID)]
        out = await self._call(groups)
        assert sorted(out.member_group_ids) == sorted(g.id for g in groups)

    @pytest.mark.asyncio
    async def test_an_unknown_group_id_is_refused_rather_than_written(self) -> None:
        """The facade answers None for a missing or soft-deleted group; binding it
        would write a row that grants nothing and reads as if it did."""
        service = AsyncMock()
        tenancy = AsyncMock()
        tenancy.live_member_group_ids = AsyncMock(return_value=set())

        with (
            patch.object(chatrooms_mod, "_project_id_for_chatroom", AsyncMock(return_value=_PROJECT_ID)),
            patch.object(chatrooms_mod, "_require_project_cap", AsyncMock()),
            patch.object(chatrooms_mod, "TenancyFacade", return_value=tenancy),
            patch.object(chatrooms_mod, "ChatroomService", return_value=service),
            pytest.raises(HTTPException) as exc,
        ):
            await chatrooms_mod.set_chatroom_member_groups(
                body=chatrooms_mod.ChatroomMemberGroupsIn(member_group_ids=[uuid.uuid4()]),
                chatroom_id=_ROOM_ID,
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=SimpleNamespace(user_id=_USER_ID, is_admin=False, email_verified=True),
                db=AsyncMock(),
            )
        assert exc.value.status_code == 422
        service.set_bound_groups.assert_not_called()


class TestReadingBindingsAgreesWithWriting:
    """The read and the write must answer the same question about a binding.

    They used not to: the GET returned raw rows (live and stale alike) while the
    PUT refused a deleted id. The settings UI sends the GET's list straight back
    on the next edit, so deleting a bound group left every later toggle failing
    with a 422 and no way to clear the stale binding from the UI.
    """

    @staticmethod
    async def _get(*, bound: set[uuid.UUID], live: set[uuid.UUID]) -> object:
        service = AsyncMock()
        service.bound_group_ids = AsyncMock(return_value=bound)
        tenancy = AsyncMock()
        tenancy.live_member_group_ids = AsyncMock(return_value=live)

        with (
            patch.object(chatrooms_mod, "_project_id_for_chatroom", AsyncMock(return_value=_PROJECT_ID)),
            patch.object(chatrooms_mod, "_require_project_cap", AsyncMock()),
            patch.object(chatrooms_mod, "TenancyFacade", return_value=tenancy),
            patch.object(chatrooms_mod, "ChatroomService", return_value=service),
        ):
            return await chatrooms_mod.list_chatroom_member_groups(
                chatroom_id=_ROOM_ID,
                principal=SimpleNamespace(user_id=_USER_ID, is_admin=False, email_verified=True),
                db=AsyncMock(),
            )

    @pytest.mark.asyncio
    async def test_a_binding_to_a_deleted_group_is_not_reported(self) -> None:
        alive, dead = uuid.uuid4(), uuid.uuid4()
        out = await self._get(bound={alive, dead}, live={alive})
        assert out.member_group_ids == [alive]

    @pytest.mark.asyncio
    async def test_live_bindings_are_reported_unchanged(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        out = await self._get(bound={a, b}, live={a, b})
        assert sorted(out.member_group_ids) == sorted([a, b])

    @pytest.mark.asyncio
    async def test_the_read_back_list_is_accepted_by_the_write(self) -> None:
        """The regression, end to end at the route layer: whatever the GET hands
        the UI must survive being sent straight back."""
        alive, dead = uuid.uuid4(), uuid.uuid4()
        got = await self._get(bound={alive, dead}, live={alive})

        service = AsyncMock()
        service.set_bound_groups = AsyncMock(return_value=set(got.member_group_ids))
        tenancy = AsyncMock()
        tenancy.live_member_group_ids = AsyncMock(return_value={alive})

        with (
            patch.object(chatrooms_mod, "_project_id_for_chatroom", AsyncMock(return_value=_PROJECT_ID)),
            patch.object(chatrooms_mod, "_require_project_cap", AsyncMock()),
            patch.object(chatrooms_mod, "TenancyFacade", return_value=tenancy),
            patch.object(chatrooms_mod, "ChatroomService", return_value=service),
        ):
            out = await chatrooms_mod.set_chatroom_member_groups(
                body=chatrooms_mod.ChatroomMemberGroupsIn(member_group_ids=got.member_group_ids),
                chatroom_id=_ROOM_ID,
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=SimpleNamespace(user_id=_USER_ID, is_admin=False, email_verified=True),
                db=AsyncMock(),
            )
        assert out.member_group_ids == [alive]
